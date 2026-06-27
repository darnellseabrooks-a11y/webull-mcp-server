import os
import uuid
import json
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, HTMLResponse
from starlette.routing import Route
from webull.core.client import ApiClient
from webull.trade.trade_client import TradeClient
from webull.quotes.quotes_client import QuotesClient
import uvicorn

# ── Env vars ───────────────────────────────────────────────────────────────
APP_KEY    = os.environ.get("WEBULL_APP_KEY", "")
APP_SECRET = os.environ.get("WEBULL_APP_SECRET", "")
ACCOUNT_ID = os.environ.get("WEBULL_ACCOUNT_ID", "")
SERVER_URL = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "localhost:8000")
MCP_SECRET = os.environ.get("MCP_SECRET", "")

# ── Webull SDK clients ─────────────────────────────────────────────────────
def get_trade_client() -> TradeClient:
    client = ApiClient(APP_KEY, APP_SECRET, "us")
    return TradeClient(client)

def get_quotes_client() -> QuotesClient:
    client = ApiClient(APP_KEY, APP_SECRET, "us")
    return QuotesClient(client)

# ── FastMCP — DNS rebinding protection disabled for Railway proxy ──────────
mcp = FastMCP(
    "Webull Trading Assistant",
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)

@mcp.tool()
def get_account_info() -> str:
    """Get Webull account balance and buying power."""
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
        res = tc.account_v2.get_positions(account_id=ACCOUNT_ID)
        return json.dumps(res.json(), indent=2)
    except Exception as e:
        return f"Error: {e}"

@mcp.tool()
def get_quote(symbol: str) -> str:
    """Get real-time quote for a stock symbol e.g. AAPL, TSLA, SPY."""
    try:
        qc = get_quotes_client()
        res = qc.market_data.get_snapshot(symbols=symbol, category="US_STOCK")
        return json.dumps(res.json(), indent=2)
    except Exception as e:
        return f"Error: {e}"

@mcp.tool()
def get_options_chain(symbol: str, expiration: str = "") -> str:
    """Get options chain for a symbol. expiration format: YYYY-MM-DD"""
    try:
        qc = get_quotes_client()
        params = {"symbol": symbol, "category": "US_OPTION"}
        if expiration:
            params["expireDate"] = expiration
        res = qc.market_data.get_options_chain(**params)
        return json.dumps(res.json(), indent=2)
    except Exception as e:
        return f"Error: {e}"

@mcp.tool()
def get_orders() -> str:
    """Get list of open and recent orders."""
    try:
        tc = get_trade_client()
        res = tc.order_v2.get_orders(account_id=ACCOUNT_ID, status="Working")
        return json.dumps(res.json(), indent=2)
    except Exception as e:
        return f"Error: {e}"

@mcp.tool()
def place_order(symbol: str, action: str, quantity: int, order_type: str = "MKT", limit_price: float = 0.0) -> str:
    """Place a stock order. action=BUY or SELL, order_type=MKT or LMT."""
    try:
        tc = get_trade_client()
        order = {
            "symbol":      symbol,
            "action":      action,
            "order_type":  order_type,
            "quantity":    quantity,
            "time_in_force": "DAY",
        }
        if order_type == "LMT":
            order["limit_price"] = str(limit_price)
        res = tc.order_v2.place_order(account_id=ACCOUNT_ID, **order)
        return json.dumps(res.json(), indent=2)
    except Exception as e:
        return f"Error: {e}"

@mcp.tool()
def cancel_order(order_id: str) -> str:
    """Cancel an open order by order ID."""
    try:
        tc = get_trade_client()
        res = tc.order_v2.cancel_order(account_id=ACCOUNT_ID, client_order_id=order_id)
        return json.dumps(res.json(), indent=2)
    except Exception as e:
        return f"Error: {e}"

# FastMCP mounts internally at /mcp
mcp_asgi = mcp.streamable_http_app()

# ── Discovery / auth route handlers ───────────────────────────────────────
async def homepage(request: Request):
    return HTMLResponse("<h2>Webull MCP Server — running ✅</h2>")

async def oauth_protected_resource(request: Request):
    base = f"https://{SERVER_URL}"
    return JSONResponse({
        "resource":                 base,
        "authorization_servers":    [base],
        "bearer_methods_supported": ["header"],
    })

async def oauth_metadata(request: Request):
    base = f"https://{SERVER_URL}"
    return JSONResponse({
        "issuer":                                base,
        "token_endpoint":                        f"{base}/token",
        "registration_endpoint":                 f"{base}/register",
        "response_types_supported":              ["token"],
        "grant_types_supported":                 ["client_credentials"],
        "token_endpoint_auth_methods_supported": ["none"],
    })

async def openid_config(request: Request):
    base = f"https://{SERVER_URL}"
    return JSONResponse({
        "issuer":                                base,
        "token_endpoint":                        f"{base}/token",
        "registration_endpoint":                 f"{base}/register",
        "grant_types_supported":                 ["client_credentials"],
        "token_endpoint_auth_methods_supported": ["none"],
    })

async def register(request: Request):
    return JSONResponse({
        "client_id":                  "webull-mcp-client",
        "grant_types":                ["client_credentials"],
        "token_endpoint_auth_method": "none",
    })

async def token(request: Request):
    return JSONResponse({
        "access_token": MCP_SECRET,
        "token_type":   "bearer",
        "expires_in":   315360000,
    })

# ── Starlette for non-MCP routes ───────────────────────────────────────────
_meta_app = Starlette(routes=[
    Route("/",                                       homepage,             methods=["GET"]),
    Route("/.well-known/oauth-protected-resource",   oauth_protected_resource),
    Route("/.well-known/oauth-protected-resource/{path:path}", oauth_protected_resource),
    Route("/.well-known/oauth-authorization-server", oauth_metadata),
    Route("/.well-known/openid-configuration",       openid_config),
    Route("/register",                               register,             methods=["POST"]),
    Route("/token",                                  token,                methods=["POST"]),
])

_META_PREFIXES = ("/.well-known/", "/register", "/token")

# ── Top-level ASGI router ──────────────────────────────────────────────────
async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        await mcp_asgi(scope, receive, send)
        return

    path   = scope.get("path", "/")
    method = scope.get("method", "") if scope["type"] == "http" else ""

    if path == "/" and method == "GET":
        await _meta_app(scope, receive, send)
        return

    if any(path.startswith(p) for p in _META_PREFIXES):
        await _meta_app(scope, receive, send)
        return

    # Rewrite to /mcp where FastMCP listens
    scope = dict(scope)
    scope["path"]     = "/mcp"
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
