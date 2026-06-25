import os
import uuid
import json
import httpx
import hmac
import hashlib
import base64
from datetime import datetime, timezone
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
BASE_URL   = os.environ.get("WEBULL_BASE_URL", "https://prod-openapi-alb.webullbroker.com")
SERVER_URL = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "localhost:8000")
MCP_SECRET = os.environ.get("MCP_SECRET", "")

# ── Signing helper ─────────────────────────────────────────────────────────
def sign(method: str, path: str, body_str: str = "") -> dict:
    ts    = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    nonce = str(uuid.uuid4()).replace("-", "")
    src   = "\n".join([method, path, ts, nonce, body_str])
    sig   = base64.b64encode(
        hmac.new(APP_SECRET.encode(), src.encode(), hashlib.sha1).digest()
    ).decode()
    return {
        "Content-Type":          "application/json",
        "x-app-key":             APP_KEY,
        "x-signature":           sig,
        "x-signature-algorithm": "HmacSHA1",
        "x-signature-version":   "1",
        "x-signature-nonce":     nonce,
        "x-timestamp":           ts,
    }

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
    path = "/openapi/account/v2/account/list"
    r = httpx.get(BASE_URL + path, headers=sign("GET", path), timeout=10)
    return r.text

@mcp.tool()
def get_positions() -> str:
    """Get current stock and options positions."""
    path = f"/openapi/account/v2/{ACCOUNT_ID}/positions"
    r = httpx.get(BASE_URL + path, headers=sign("GET", path), timeout=10)
    return r.text

@mcp.tool()
def get_quote(symbol: str) -> str:
    """Get real-time quote for a stock symbol e.g. AAPL, TSLA, SPY."""
    path = f"/openapi/quote/v1/ticker/snapshot?symbols={symbol}"
    r = httpx.get(BASE_URL + path, headers=sign("GET", path), timeout=10)
    return r.text

@mcp.tool()
def get_options_chain(symbol: str, expiration: str = "") -> str:
    """Get options chain for a symbol. expiration format: YYYY-MM-DD"""
    path = f"/openapi/quote/v1/option/chain?symbol={symbol}"
    if expiration:
        path += f"&expireDate={expiration}"
    r = httpx.get(BASE_URL + path, headers=sign("GET", path), timeout=10)
    return r.text

@mcp.tool()
def get_orders() -> str:
    """Get list of open and recent orders."""
    path = f"/openapi/trade/v2/{ACCOUNT_ID}/orders?status=Working"
    r = httpx.get(BASE_URL + path, headers=sign("GET", path), timeout=10)
    return r.text

@mcp.tool()
def place_order(symbol: str, action: str, quantity: int, order_type: str = "MKT", limit_price: float = 0.0) -> str:
    """Place a stock order. action=BUY or SELL, order_type=MKT or LMT."""
    path = f"/openapi/trade/v2/{ACCOUNT_ID}/orders"
    body = {
        "symbol":    symbol,
        "action":    action,
        "orderType": order_type,
        "quantity":  quantity,
    }
    if order_type == "LMT":
        body["limitPrice"] = limit_price
    body_str = json.dumps(body)
    r = httpx.post(
        BASE_URL + path,
        headers=sign("POST", path, body_str),
        content=body_str.encode(),
        timeout=10,
    )
    return r.text

@mcp.tool()
def cancel_order(order_id: str) -> str:
    """Cancel an open order by order ID."""
    path = f"/openapi/trade/v2/{ACCOUNT_ID}/orders/{order_id}/cancel"
    r = httpx.post(BASE_URL + path, headers=sign("POST", path), timeout=10)
    return r.text

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

    # GET / → homepage
    if path == "/" and method == "GET":
        await _meta_app(scope, receive, send)
        return

    # Discovery / token routes → Starlette
    if any(path.startswith(p) for p in _META_PREFIXES):
        await _meta_app(scope, receive, send)
        return

    # All MCP traffic → rewrite to /mcp where FastMCP listens
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
