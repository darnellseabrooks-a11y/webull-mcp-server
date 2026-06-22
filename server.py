import os
import uuid
import json
import httpx
import hmac
import hashlib
import base64
from datetime import datetime, timezone
from mcp.server.fastmcp import FastMCP
from mcp.server import Server
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse
import uvicorn

APP_KEY    = os.environ.get("WEBULL_APP_KEY", "")
APP_SECRET = os.environ.get("WEBULL_APP_SECRET", "")
TOKEN      = os.environ.get("WEBULL_TOKEN", "")
ACCOUNT_ID = os.environ.get("WEBULL_ACCOUNT_ID", "")
BASE_URL   = os.environ.get("WEBULL_BASE_URL", "https://prod-openapi-alb.webullbroker.com")
SERVER_URL = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "localhost:8000")

mcp = FastMCP("Webull Trading Assistant", stateless_http=True)

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

# Build MCP app
mcp_app = mcp.streamable_http_app()

# FastAPI app
app = FastAPI(
    lifespan=mcp_app.router.lifespan_context,
    redirect_slashes=False
)

@app.get("/")
async def homepage():
    return HTMLResponse("<h2>Webull MCP Server is running.</h2>")

@app.get("/.well-known/oauth-protected-resource")
@app.get("/.well-known/oauth-protected-resource/mcp")
async def oauth_protected_resource():
    base = f"https://{SERVER_URL}"
    return JSONResponse({
        "resource": f"{base}/mcp",
        "authorization_servers": [base],
    })

@app.get("/.well-known/oauth-authorization-server")
async def oauth_metadata():
    base = f"https://{SERVER_URL}"
    return JSONResponse({
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
    })

@app.post("/oauth/register")
async def oauth_register(request: Request):
    body = await request.json()
    client_id = "webull-" + str(uuid.uuid4())[:8]
    return JSONResponse({
        "client_id": client_id,
        "client_secret": "webull-secret",
        "redirect_uris": body.get("redirect_uris", []),
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
    })

@app.get("/oauth/authorize")
async def oauth_authorize(request: Request):
    redirect_uri = request.query_params.get("redirect_uri", "")
    state = request.query_params.get("state", "")
    code = "webull-code-" + str(uuid.uuid4())
    return RedirectResponse(url=f"{redirect_uri}?code={code}&state={state}")

@app.api_route("/oauth/token", methods=["GET", "POST"])
async def oauth_token():
    return JSONResponse({
        "access_token":  "webull-token-" + str(uuid.uuid4()),
        "token_type":    "bearer",
        "expires_in":    86400,
        "scope":         "read write",
        "refresh_token": "webull-refresh-" + str(uuid.uuid4()),
    })

# Pass MCP requests through with corrected path
@app.api_route("/mcp", methods=["GET", "POST", "DELETE", "PUT"])
@app.api_route("/mcp/{path:path}", methods=["GET", "POST", "DELETE", "PUT"])
async def mcp_handler(request: Request, path: str = ""):
    scope = dict(request.scope)
    scope["path"] = "/" if not path else f"/{path}"
    scope["raw_path"] = scope["path"].encode()
    scope["root_path"] = ""
    await mcp_app(scope, request._receive, request._send)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
