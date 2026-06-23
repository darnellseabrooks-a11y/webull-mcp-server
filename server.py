import os
import uuid
import json
import httpx
import hmac
import hashlib
import base64
from datetime import datetime, timezone
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, HTMLResponse
from starlette.routing import Route
import uvicorn

APP_KEY    = os.environ.get("WEBULL_APP_KEY", "")
APP_SECRET = os.environ.get("WEBULL_APP_SECRET", "")
TOKEN      = os.environ.get("WEBULL_TOKEN", "")
ACCOUNT_ID = os.environ.get("WEBULL_ACCOUNT_ID", "")
BASE_URL   = os.environ.get("WEBULL_BASE_URL", "https://prod-openapi-alb.webullbroker.com")
SERVER_URL = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "localhost:8000")

# ── FastMCP mounted at "/" so Claude.ai's POST / hits it directly ──────────
mcp = FastMCP(
    "Webull Trading Assistant",
    stateless_http=True,
)

def sign(method, path, body_str=""):
    ts    = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    nonce = str(uuid.uuid4()).replace("-", "")
    src   = "\n".join([method, path, ts, nonce, body_str])
    sig   = base64.b64encode(
        hmac.new(APP_SECRET.encode(), src.encode(), hashlib.sha1).digest()
    ).decode()
    return {
        "Content-Type":          "application/json",
        "x-app-key":             APP_KEY,
        "x-auth-token":          TOKEN,
        "x-signature":           sig,
        "x-signature-algorithm": "HmacSHA1",
        "x-signature-version":   "1",
        "x-signature-nonce":     nonce,
        "x-timestamp":           ts,
    }

# ── MCP Tools ─────────────────────────────────────────────────────────────
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
    body = {"symbol": symbol, "action": action, "orderType": order_type, "quantity": quantity}
    if order_type == "LMT":
        body["limitPrice"] = limit_price
    body_str = json.dumps(body)
    r = httpx.post(BASE_URL + path, headers=sign("POST", path, body_str), content=body_str.encode(), timeout=10)
    return r.text

@mcp.tool()
def cancel_order(order_id: str) -> str:
    """Cancel an open order by order ID."""
    path = f"/openapi/trade/v2/{ACCOUNT_ID}/orders/{order_id}/cancel"
    r = httpx.post(BASE_URL + path, headers=sign("POST", path), timeout=10)
    return r.text

# ── Build MCP ASGI app ─────────────────────────────────────────────────────
# streamable_http_path="/" means FastMCP listens on POST /
# OAuth routes (/.well-known/*, /oauth/*, /) are shorter and matched FIRST
# by Starlette before falling through to FastMCP's catch-all.
mcp_asgi = mcp.streamable_http_app()

# ── OAuth / discovery route handlers ──────────────────────────────────────
async def homepage(request: Request):
    return HTMLResponse("<h2>Webull MCP Server — running ✅</h2>")

async def oauth_protected_resource(request: Request):
    # resource_server_url = root (no /mcp suffix).
    # Claude.ai will POST to this URL directly after OAuth.
    base = f"https://{SERVER_URL}"
    return JSONResponse({
        "resource":               base,          # ← root, NOT /mcp
        "authorization_servers":  [base],
        "scopes_supported":       ["read", "write"],
        "bearer_methods_supported": ["header"],
    })

async def oauth_metadata(request: Request):
    base = f"https://{SERVER_URL}"
    return JSONResponse({
        "issuer":                              base,
        "authorization_endpoint":             f"{base}/oauth/authorize",
        "token_endpoint":                     f"{base}/oauth/token",
        "registration_endpoint":              f"{base}/oauth/register",
        "response_types_supported":           ["code"],
        "grant_types_supported":              ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported":   ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
    })

async def oauth_register(request: Request):
    body = await request.json()
    client_id = "webull-" + str(uuid.uuid4())[:8]
    return JSONResponse({
        "client_id":    client_id,
        # public client — no client_secret
        "redirect_uris":  body.get("redirect_uris", []),
        "grant_types":    ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    })

async def oauth_authorize(request: Request):
    redirect_uri = request.query_params.get("redirect_uri", "")
    state        = request.query_params.get("state", "")
    code         = "webull-code-" + str(uuid.uuid4())
    return RedirectResponse(url=f"{redirect_uri}?code={code}&state={state}")

async def oauth_token(request: Request):
    return JSONResponse({
        "access_token":  "webull-token-" + str(uuid.uuid4()),
        "token_type":    "bearer",
        "expires_in":    86400,
        "scope":         "read write",
        "refresh_token": "webull-refresh-" + str(uuid.uuid4()),
    })

# ── Top-level ASGI router ─────────────────────────────────────────────────
# Priority: OAuth / discovery routes → FastMCP catch-all (POST / GET / DELETE /)
_oauth_routes = Starlette(routes=[
    Route("/",                                        homepage),
    Route("/.well-known/oauth-protected-resource",    oauth_protected_resource),
    # Claude also fetches the sub-path variant
    Route("/.well-known/oauth-protected-resource/{path:path}", oauth_protected_resource),
    Route("/.well-known/oauth-authorization-server",  oauth_metadata),
    Route("/oauth/register",  oauth_register,  methods=["POST"]),
    Route("/oauth/authorize", oauth_authorize, methods=["GET"]),
    Route("/oauth/token",     oauth_token,     methods=["GET", "POST"]),
])

# Paths handled by OAuth/Starlette — everything else goes to FastMCP
_OAUTH_PREFIXES = (
    "/.well-known/",
    "/oauth/",
)
_OAUTH_EXACT = {"/"}

async def app(scope, receive, send):
    # Lifespan events must go to FastMCP (it owns the lifespan)
    if scope["type"] == "lifespan":
        await mcp_asgi(scope, receive, send)
        return

    path = scope.get("path", "/")

    # Route OAuth/discovery requests to Starlette
    if path in _OAUTH_EXACT or any(path.startswith(p) for p in _OAUTH_PREFIXES):
        await _oauth_routes(scope, receive, send)
        return

    # Everything else (POST /, GET /, DELETE /) → FastMCP
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
