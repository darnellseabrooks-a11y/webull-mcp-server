# v4 - correct order structure confirmed via console testing
import os
import uuid
import json

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, HTMLResponse
from starlette.routing import Route
import uvicorn

# ── Env vars ───────────────────────────────────────────────────────────────
APP_KEY    = os.environ.get("WEBULL_APP_KEY", "")
APP_SECRET = os.environ.get("WEBULL_APP_SECRET", "")
ACCOUNT_ID = os.environ.get("WEBULL_ACCOUNT_ID", "")
SERVER_URL = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "localhost:8000")
MCP_SECRET = os.environ.get("MCP_SECRET", "")

# ── Webull SDK client (lazy init) ──────────────────────────────────────────
_trade_client = None

def get_trade_client():
    global _trade_client
    if _trade_client is None:
        from webull.core.client import ApiClient
        from webull.trade.trade_client import TradeClient
        api_client = ApiClient(APP_KEY, APP_SECRET, "us")
        _trade_client = TradeClient(api_client)
    return _trade_client

def build_stock_order(symbol, side, quantity, order_type="LIMIT", limit_price=0.0, time_in_force="DAY"):
    order = {
        "client_order_id":        uuid.uuid4().hex,
        "combo_type":             "NORMAL",
        "symbol":                 symbol,
        "side":                   side,
        "order_type":             order_type,
        "entrust_type":           "QTY",
        "quantity":               quantity,
        "time_in_force":          time_in_force,
        "market":                 "US",
        "support_trading_session": "CORE",
    }
    if order_type == "LIMIT" and limit_price > 0:
        order["limit_price"] = limit_price
    return order

# ── FastMCP ────────────────────────────────────────────────────────────────
mcp = FastMCP(
    "Webull Trading Assistant",
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)

@mcp.tool()
def get_account_info() -> str:
    """Get Webull account list."""
    try:
        tc = get_trade_client()
        res = tc.account_v2.get_account_list()
        return json.dumps(res.json(), indent=2)
    except Exception as e:
        return f"Error: {e}"

@mcp.tool()
def get_positions() -> str:
    """Get current stock and options positions."""
    try:
        tc = get_trade_client()
        res = tc.account_v2.get_account_position(ACCOUNT_ID)
        return json.dumps(res.json(), indent=2)
    except Exception as e:
        return f"Error: {e}"

@mcp.tool()
def get_account_balance() -> str:
    """Get account balance and buying power details."""
    try:
        tc = get_trade_client()
        res = tc.account_v2.get_account_balance(ACCOUNT_ID)
        return json.dumps(res.json(), indent=2)
    except Exception as e:
        return f"Error: {e}"

@mcp.tool()
def get_orders() -> str:
    """Get list of open orders."""
    try:
        tc = get_trade_client()
        res = tc.order_v2.get_order_open(ACCOUNT_ID)
        return json.dumps(res.json(), indent=2)
    except Exception as e:
        return f"Error: {e}"

@mcp.tool()
def get_order_history(page_size: int = 20, start_date: str = "", end_date: str = "") -> str:
    """Get order history. Dates format: YYYY-MM-DD"""
    try:
        tc = get_trade_client()
        res = tc.order_v2.get_order_history(
            ACCOUNT_ID,
            page_size=page_size,
            start_date=start_date or None,
            end_date=end_date or None,
        )
        return json.dumps(res.json(), indent=2)
    except Exception as e:
        return f"Error: {e}"

@mcp.tool()
def preview_stock_order(
    symbol: str,
    side: str,
    quantity: int,
    order_type: str = "LIMIT",
    limit_price: float = 0.0,
    time_in_force: str = "DAY"
) -> str:
    """Preview a stock order to see estimated cost before placing.
    side: BUY or SELL
    order_type: MARKET or LIMIT
    time_in_force: DAY or GTC
    """
    try:
        tc = get_trade_client()
        order = build_stock_order(symbol, side, quantity, order_type, limit_price, time_in_force)
        res = tc.order_v2.preview_order(ACCOUNT_ID, [order])
        return json.dumps(res.json(), indent=2)
    except Exception as e:
        return f"Error: {e}"

@mcp.tool()
def place_stock_order(
    symbol: str,
    side: str,
    quantity: int,
    order_type: str = "LIMIT",
    limit_price: float = 0.0,
    time_in_force: str = "DAY"
) -> str:
    """Place a stock or ETF order.
    side: BUY or SELL
    order_type: MARKET or LIMIT
    time_in_force: DAY or GTC
    """
    try:
        tc = get_trade_client()
        order = build_stock_order(symbol, side, quantity, order_type, limit_price, time_in_force)
        res = tc.order_v2.place_order(ACCOUNT_ID, [order])
        return json.dumps(res.json(), indent=2)
    except Exception as e:
        return f"Error: {e}"

@mcp.tool()
def place_option_order(
    symbol: str,
    side: str,
    quantity: int,
    limit_price: float,
    expiry: str,
    strike: float,
    option_type: str = "CALL"
) -> str:
    """Place a single-leg option order.
    symbol: underlying e.g. SPY
    side: BUY or SELL
    option_type: CALL or PUT
    expiry: YYYY-MM-DD
    strike: strike price
    limit_price: limit price for the option contract
    """
    try:
        tc = get_trade_client()
        order = {
            "client_order_id":        uuid.uuid4().hex,
            "combo_type":             "NORMAL",
            "symbol":                 symbol,
            "side":                   side,
            "order_type":             "LIMIT",
            "entrust_type":           "QTY",
            "quantity":               quantity,
            "time_in_force":          "DAY",
            "limit_price":            limit_price,
            "market":                 "US",
            "support_trading_session": "CORE",
            "option_type":            option_type,
            "expire_date":            expiry,
            "strike_price":           strike,
        }
        res = tc.order_v2.place_option(ACCOUNT_ID, [order])
        return json.dumps(res.json(), indent=2)
    except Exception as e:
        return f"Error: {e}"

@mcp.tool()
def cancel_order(order_id: str) -> str:
    """Cancel an open order by client order ID."""
    try:
        tc = get_trade_client()
        res = tc.order_v2.cancel_order(ACCOUNT_ID, client_order_id=order_id)
        return json.dumps(res.json(), indent=2)
    except Exception as e:
        return f"Error: {e}"

mcp_asgi = mcp.streamable_http_app()

# ── Discovery / auth handlers ──────────────────────────────────────────────
async def homepage(request: Request):
    return HTMLResponse("<h2>Webull MCP Server — running ✅</h2>")

async def oauth_protected_resource(request: Request):
    base = f"https://{SERVER_URL}"
    return JSONResponse({
        "resource": base,
        "authorization_servers": [base],
        "bearer_methods_supported": ["header"],
    })

async def oauth_metadata(request: Request):
    base = f"https://{SERVER_URL}"
    return JSONResponse({
        "issuer": base,
        "token_endpoint": f"{base}/token",
        "registration_endpoint": f"{base}/register",
        "response_types_supported": ["token"],
        "grant_types_supported": ["client_credentials"],
        "token_endpoint_auth_methods_supported": ["none"],
    })

async def openid_config(request: Request):
    base = f"https://{SERVER_URL}"
    return JSONResponse({
        "issuer": base,
        "token_endpoint": f"{base}/token",
        "registration_endpoint": f"{base}/register",
        "grant_types_supported": ["client_credentials"],
        "token_endpoint_auth_methods_supported": ["none"],
    })

async def register(request: Request):
    return JSONResponse({
        "client_id": "webull-mcp-client",
        "grant_types": ["client_credentials"],
        "token_endpoint_auth_method": "none",
    })

async def token(request: Request):
    return JSONResponse({
        "access_token": MCP_SECRET,
        "token_type": "bearer",
        "expires_in": 315360000,
    })

_meta_app = Starlette(routes=[
    Route("/", homepage, methods=["GET"]),
    Route("/.well-known/oauth-protected-resource", oauth_protected_resource),
    Route("/.well-known/oauth-protected-resource/{path:path}", oauth_protected_resource),
    Route("/.well-known/oauth-authorization-server", oauth_metadata),
    Route("/.well-known/openid-configuration", openid_config),
    Route("/register", register, methods=["POST"]),
    Route("/token", token, methods=["POST"]),
])

_META_PREFIXES = ("/.well-known/", "/register", "/token")

async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        await mcp_asgi(scope, receive, send)
        return

    path = scope.get("path", "/")
    method = scope.get("method", "") if scope["type"] == "http" else ""

    if path == "/" and method == "GET":
        await _meta_app(scope, receive, send)
        return

    if any(path.startswith(p) for p in _META_PREFIXES):
        await _meta_app(scope, receive, send)
        return

    scope = dict(scope)
    scope["path"] = "/mcp"
    scope["raw_path"] = b"/mcp"
    await mcp_asgi(scope, receive, send)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=port,
        forwarded_allow_ips="*",
        proxy_headers=True,
    )
