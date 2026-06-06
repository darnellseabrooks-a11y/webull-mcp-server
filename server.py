import os
import hmac
import hashlib
import base64
import uuid
import json
import httpx
from datetime import datetime, timezone
from mcp.server.fastmcp import FastMCP

# ── Config from environment variables ──────────────────────
APP_KEY    = os.environ.get("WEBULL_APP_KEY", "")
APP_SECRET = os.environ.get("WEBULL_APP_SECRET", "")
TOKEN      = os.environ.get("WEBULL_TOKEN", "")
ACCOUNT_ID = os.environ.get("WEBULL_ACCOUNT_ID", "")
BASE_URL   = os.environ.get("WEBULL_BASE_URL", "https://us-openapi-alb.uat.webullbroker.com")

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
    path = f"/openapi/account/v2/account/list"
    headers = sign("GET", path)
    r = httpx.get(BASE_URL + path, headers=headers, timeout=10)
    return r.text

@mcp.tool()
def get_positions() -> str:
    """Get current stock and options positions."""
    path = f"/openapi/account/v2/{ACCOUNT_ID}/positions"
    headers = sign("GET", path)
    r = httpx.get(BASE_URL + path, headers=headers, timeout=10)
    return r.text

@mcp.tool()
def get_quote(symbol: str) -> str:
    """Get real-time quote for a stock symbol. Example: AAPL, TSLA, SPY"""
    path = f"/openapi/quote/v1/ticker/snapshot?symbols={symbol}"
    headers = sign("GET", path)
    r = httpx.get(BASE_URL + path, headers=headers, timeout=10)
    return r.text

@mcp.tool()
def get_orders() -> str:
    """Get list of open and recent orders."""
    path = f"/openapi/trade/v2/{ACCOUNT_ID}/orders?status=Working"
    headers = sign("GET", path)
    r = httpx.get(BASE_URL + path, headers=headers, timeout=10)
    return r.text

@mcp.tool()
def place_order(symbol: str, action: str, quantity: int, order_type: str = "MKT", limit_price: float = 0.0) -> str:
    """
    Place a stock order on Webull.
    - symbol: stock ticker e.g. AAPL
    - action: BUY or SELL
    - quantity: number of shares
    - order_type: MKT (market) or LMT (limit)
    - limit_price: required if order_type is LMT
    """
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
    headers  = sign("POST", path, body_str)
    r = httpx.post(BASE_URL + path, headers=headers, content=body_str.encode(), timeout=10)
    return r.text

@mcp.tool()
def cancel_order(order_id: str) -> str:
    """Cancel an open order by order ID."""
    path = f"/openapi/trade/v2/{ACCOUNT_ID}/orders/{order_id}/cancel"
    headers = sign("POST", path)
    r = httpx.post(BASE_URL + path, headers=headers, timeout=10)
    return r.text

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(mcp.get_asgi_app(), host="0.0.0.0", port=port)
