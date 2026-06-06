import os
import uuid
import hmac
import hashlib
import base64
import json
import httpx
from datetime import datetime, timezone
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, HTMLResponse
from starlette.routing import Route

APP_KEY    = os.environ.get("WEBULL_APP_KEY", "")
APP_SECRET = os.environ.get("WEBULL_APP_SECRET", "")
TOKEN      = os.environ.get("WEBULL_TOKEN", "")
ACCOUNT_ID = os.environ.get("WEBULL_ACCOUNT_ID", "")
BASE_URL   = os.environ.get("WEBULL_BASE_URL", "https://us-openapi-alb.uat.webullbroker.com")
SERVER_URL = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "localhost:8000")

mcp = FastMCP("Webull Trading Assistant")

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

async def oauth_metadata(request: Request):
    base = f"https://{SERVER_URL}"
    return JSONResponse({
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
    })

async def oauth_authorize(request: Request):
    redirect_uri = request.query_params.get("redirect_uri", "")
    state = request.query_params.get("state", "")
    code = "webull-auth-code-" + str(uuid.uuid4())
    return RedirectResponse(url=f"{redirect_uri}?code={code}&state={state}")

async def oauth_token(request: Request):
    return JSONResponse({
        "access_token":  "webull-static-token-" + str(uuid.uuid4()),
        "token_type":    "bearer",
        "expires_in":    86400,
        "scope":         "read write",
        "refresh_token": "webull-refresh-" + str(uuid.uuid4()),
    })

async def homepage(request: Request):
    return HTMLResponse("<h2>Webull MCP Server is running.</h2>")

oauth_routes = [
    Route("/", homepage),
    Route("/.well-known/oauth-authorization-server", oauth_metadata),
    Route("/oauth/authorize", oauth_authorize),
    Route("/oauth/token", oauth_token, methods=["POST", "GET"]),
]

oauth_app = Starlette(routes=oauth_routes)
mcp_app   = mcp.streamable_http_app()

async def combined_app(scope, receive, send):
    path = scope.get("path", "")
    if path.startswith("/mcp"):
        await mcp_app(scope, receive, send)
    else:
        await oauth_app(scope, receive, send)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(combined_app, host="0.0.0.0", port=port)
