import os
import uuid
import json
import httpx
import hmac
import hashlib
import base64
import logging
from datetime import datetime, timezone
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, HTMLResponse
from starlette.routing import Route
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Env vars ───────────────────────────────────────────────────────────────
APP_KEY    = os.environ.get("WEBULL_APP_KEY", "")
APP_SECRET = os.environ.get("WEBULL_APP_SECRET", "")
ACCOUNT_ID = os.environ.get("WEBULL_ACCOUNT_ID", "")
SERVER_URL = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "localhost:8000")
MCP_SECRET = os.environ.get("MCP_SECRET", "")
BASE_URL   = "https://api.webull.com"

# ── Signing helper ─────────────────────────────────────────────────────────
# Webull signs only the path portion (no query string)
def sign(method: str, path: str, body_str: str = "") -> dict:
    # Strip query string for signing
    sign_path = path.split("?")[0]
    ts    = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    nonce = str(uuid.uuid4()).replace("-", "")
    src   = "\n".join([method, sign_path, ts, nonce, body_str])
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

def call(method: str, path: str, body_str: str = "") -> str:
    url = BASE_URL + path
    headers = sign(method, path, body_str)
    logger.info(f"→ {method} {url}")
    if method == "GET":
        r = httpx.get(url, headers=headers, timeout=10)
    else:
        r = httpx.post(url, headers=headers, content=body_str.encode(), timeout=10)
    logger.info(f"← {r.status_code} {r.text[:200]}")
    return r.text

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
    """Get Webull account list and info."""
    return call("GET", "/openapi/account/list")

@mcp.tool()
def get_positions() -> str:
    """Get current stock and options positions."""
    return call("GET", f"/openapi/assets/positions?account_id={ACCOUNT_ID}")

@mcp.tool()
def get_quote(symbol: str) -> str:
    """Get real-time quote for a stock symbol e.g. AAPL, TSLA, SPY."""
    return call("GET", f"/openapi/market-data/stock/snapshot?symbols={symbol}&category=US_STOCK")

@mcp.tool()
def get_options_chain(symbol: str, expiration: str = "") -> str:
    """Get options chain for a symbol. expiration format: YYYY-MM-DD"""
    path = f"/openapi/market-data/option/chain?symbol={symbol}"
    if expiration:
        path += f"&expireDate={expiration}"
    return call("GET", path)

@mcp.tool()
def get_orders() -> str:
    """Get list of open and recent orders."""
    return call("GET", f"/openapi/trade/order/list?account_id={ACCOUNT_ID}&status=Working")

@mcp.tool()
def place_order(symbol: str, action: str, quantity: int, order_type: str = "MKT", limit_price: float = 0.0) -> str:
    """Place a stock order. action=BUY or SELL, order_type=MKT or LMT."""
    body = {
        "account_id":      ACCOUNT_ID,
        "symbol":          symbol,
        "side":            action,
        "order_type":      order_type,
        "qty":             str(quantity),
        "time_in_force":   "DAY",
        "client_order_id": uuid.uuid4().hex,
    }
    if order_type == "LMT":
        body["limit_price"] = str(limit_price)
    return call("POST", "/openapi/trade/order/place", json.dumps(body))

@mcp.tool()
def cancel_order(order_id: str) -> str:
    """Cancel an open order by client order ID."""
    body = {"account_id": ACCOUNT_ID, "client_order_id": order_id}
    return call("POST", "/openapi/trade/order/cancel", json.dumps(body))

mcp_asgi = mcp.streamable_http_app()

# ── Diagnostic endpoint ────────────────────────────────────────────────────
async def diagnostic(request: Request):
    """Quick test — hit /test to verify Webull API connectivity."""
    results = {}
    try:
        r = httpx.get(BASE_URL + "/openapi/account/list",
                      headers=sign("GET", "/openapi/account/list"), timeout=10)
        results["account"] = {"status": r.status_code, "body": r.text[:300]}
    except Exception as e:
        results["account"] = {"error": str(e)}
    return JSONResponse(results)

# ── Discovery / auth handlers ──────────────────────────────────────────────
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

_meta_app = Starlette(routes=[
    Route("/",                                       homepage,             methods=["GET"]),
    Route("/test",                                   diagnostic,           methods=["GET"]),
    Route("/.well-known/oauth-protected-resource",   oauth_protected_resource),
    Route("/.well-known/oauth-protected-resource/{path:path}", oauth_protected_resource),
    Route("/.well-known/oauth-authorization-server", oauth_metadata),
    Route("/.well-known/openid-configuration",       openid_config),
    Route("/register",                               register,             methods=["POST"]),
    Route("/token",                                  token,                methods=["POST"]),
])

_META_PREFIXES = ("/.well-known/", "/register", "/token", "/test")

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
