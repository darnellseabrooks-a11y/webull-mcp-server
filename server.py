# v6 - added get_options_chain (Massive/Polygon) + get_webull_option_quotes (Webull DataClient)
import os
import uuid
import json
from datetime import date, timedelta

import requests
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

# Massive/Polygon (options chain discovery). MASSIVE_API_KEY preferred;
# POLYGON_API_KEY kept as a fallback since Massive's own libs support both names.
MASSIVE_API_KEY = os.environ.get("MASSIVE_API_KEY") or os.environ.get("POLYGON_API_KEY", "")
MASSIVE_BASE_URL = "https://api.massive.com"

# ── Webull SDK clients (lazy init) ─────────────────────────────────────────
_trade_client = None
_data_client = None

def get_trade_client():
    global _trade_client
    if _trade_client is None:
        from webull.core.client import ApiClient
        from webull.trade.trade_client import TradeClient
        api_client = ApiClient(APP_KEY, APP_SECRET, "us")
        _trade_client = TradeClient(api_client)
    return _trade_client

def get_data_client():
    global _data_client
    if _data_client is None:
        from webull.core.client import ApiClient
        from webull.data.data_client import DataClient
        api_client = ApiClient(APP_KEY, APP_SECRET, "us")
        _data_client = DataClient(api_client)
    return _data_client

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

def build_option_order(symbol, side, quantity, limit_price, expiry, strike, option_type="CALL"):
    """Build a single-leg option order matching Webull's confirmed schema.
    Field names/values verified live via preview_option on 2026-07-03:
    - option_type must be 'CALL' / 'PUT' (NOT 'CALL_OPTION'/'PUT_OPTION')
    - expiry field is 'option_expire_date' (NOT 'expire_date')
    - order-level dict needs its own instrument_type/symbol IN ADDITION to the legs array
    - quantity/limit_price/strike_price are sent as strings
    """
    return {
        "client_order_id":  uuid.uuid4().hex,
        "combo_type":       "NORMAL",
        "order_type":       "LIMIT",
        "limit_price":      str(limit_price),
        "quantity":         str(quantity),
        "option_strategy":  "SINGLE",
        "side":             side,
        "time_in_force":    "DAY",
        "entrust_type":     "QTY",
        "instrument_type":  "OPTION",
        "market":           "US",
        "symbol":           symbol,
        "legs": [
            {
                "side":               side,
                "quantity":           str(quantity),
                "symbol":             symbol,
                "strike_price":       f"{strike:.2f}",
                "option_expire_date": expiry,
                "instrument_type":    "OPTION",
                "option_type":        option_type,
                "market":             "US",
            }
        ],
    }

def build_option_spread_order(symbol, long_strike, short_strike, quantity, limit_price, expiry, option_type="CALL", side="BUY"):
    """Build a two-leg vertical spread order (e.g. bull call debit spread, bull put credit spread).
    Confirmed against Webull's documented VERTICAL example on 2026-07-04.

    side: order-level direction — 'BUY' for a net debit spread (e.g. bull call: buy lower
          strike call, sell higher strike call), 'SELL' for a net credit spread.
    long_strike: strike price of the leg you're buying (long)
    short_strike: strike price of the leg you're selling (short)
    Both legs share the same underlying symbol, expiry, and option_type.
    """
    return {
        "client_order_id":  uuid.uuid4().hex,
        "combo_type":       "NORMAL",
        "option_strategy":  "VERTICAL",
        "order_type":       "LIMIT",
        "limit_price":      str(limit_price),
        "quantity":         str(quantity),
        "side":             side,
        "time_in_force":    "DAY",
        "entrust_type":     "QTY",
        "instrument_type":  "OPTION",
        "market":           "US",
        "symbol":           symbol,
        "legs": [
            {
                "side":               "BUY",
                "quantity":           str(quantity),
                "symbol":             symbol,
                "strike_price":       f"{long_strike:.2f}",
                "option_expire_date": expiry,
                "instrument_type":    "OPTION",
                "option_type":        option_type,
                "market":             "US",
            },
            {
                "side":               "SELL",
                "quantity":           str(quantity),
                "symbol":             symbol,
                "strike_price":       f"{short_strike:.2f}",
                "option_expire_date": expiry,
                "instrument_type":    "OPTION",
                "option_type":        option_type,
                "market":             "US",
            },
        ],
    }

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
def preview_option_order(
    symbol: str,
    side: str,
    quantity: int,
    limit_price: float,
    expiry: str,
    strike: float,
    option_type: str = "CALL"
) -> str:
    """Preview a single-leg option order (no execution) — returns estimated cost/fees.
    symbol: underlying e.g. SPY
    side: BUY or SELL
    option_type: CALL or PUT
    expiry: YYYY-MM-DD
    strike: strike price
    limit_price: limit price for the option contract
    """
    try:
        tc = get_trade_client()
        order = build_option_order(symbol, side, quantity, limit_price, expiry, strike, option_type)
        res = tc.order_v2.preview_option(ACCOUNT_ID, [order])
        return json.dumps(res.json(), indent=2)
    except Exception as e:
        return f"Error: {getattr(e, 'error_msg', str(e))}"

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
        order = build_option_order(symbol, side, quantity, limit_price, expiry, strike, option_type)
        res = tc.order_v2.place_option(ACCOUNT_ID, [order])
        return json.dumps(res.json(), indent=2)
    except Exception as e:
        return f"Error: {getattr(e, 'error_msg', str(e))}"

@mcp.tool()
def preview_option_spread(
    symbol: str,
    long_strike: float,
    short_strike: float,
    quantity: int,
    limit_price: float,
    expiry: str,
    option_type: str = "CALL",
    side: str = "BUY",
) -> str:
    """Preview a two-leg vertical spread (e.g. bull call debit spread) — no execution, returns estimated cost/fees.
    symbol: underlying e.g. NVDA
    long_strike: strike of the leg you're buying
    short_strike: strike of the leg you're selling
    limit_price: net debit (side=BUY) or net credit (side=SELL) for the spread
    expiry: YYYY-MM-DD (same for both legs)
    option_type: CALL or PUT (same for both legs)
    side: BUY for a net debit spread (e.g. bull call), SELL for a net credit spread (e.g. bull put)
    """
    try:
        tc = get_trade_client()
        order = build_option_spread_order(symbol, long_strike, short_strike, quantity, limit_price, expiry, option_type, side)
        res = tc.order_v2.preview_option(ACCOUNT_ID, [order])
        return json.dumps(res.json(), indent=2)
    except Exception as e:
        return f"Error: {getattr(e, 'error_msg', str(e))}"

@mcp.tool()
def place_option_spread(
    symbol: str,
    long_strike: float,
    short_strike: float,
    quantity: int,
    limit_price: float,
    expiry: str,
    option_type: str = "CALL",
    side: str = "BUY",
) -> str:
    """Place a two-leg vertical spread (e.g. bull call debit spread).
    symbol: underlying e.g. NVDA
    long_strike: strike of the leg you're buying
    short_strike: strike of the leg you're selling
    limit_price: net debit (side=BUY) or net credit (side=SELL) for the spread
    expiry: YYYY-MM-DD (same for both legs)
    option_type: CALL or PUT (same for both legs)
    side: BUY for a net debit spread (e.g. bull call), SELL for a net credit spread (e.g. bull put)
    """
    try:
        tc = get_trade_client()
        order = build_option_spread_order(symbol, long_strike, short_strike, quantity, limit_price, expiry, option_type, side)
        res = tc.order_v2.place_option(ACCOUNT_ID, [order])
        return json.dumps(res.json(), indent=2)
    except Exception as e:
        return f"Error: {getattr(e, 'error_msg', str(e))}"

@mcp.tool()
def get_options_chain(
    symbol: str,
    min_dte: int = 0,
    max_dte: int = 45,
    strike_range_pct: float = 0.15,
    option_type: str = "",
    limit: int = 100,
) -> str:
    """Discover an options chain for an underlying via Massive/Polygon.
    Returns strikes/expiries/OI/greeks/IV for contracts near the money.
    Use get_webull_option_quotes afterward for live Webull bid/ask on specific contracts.

    symbol: underlying ticker e.g. SPY, QQQ
    min_dte/max_dte: days-to-expiration window (default 0-45)
    strike_range_pct: only return strikes within +/- this fraction of reference price (default 0.15 = 15%)
    option_type: 'call', 'put', or '' for both
    limit: max contracts to return (max 250 per Massive's API)
    """
    try:
        if not MASSIVE_API_KEY:
            return "Error: MASSIVE_API_KEY (or POLYGON_API_KEY) not set in environment"

        headers = {"Authorization": f"Bearer {MASSIVE_API_KEY}"}

        # Reference price from previous close (used only to bound the strike window;
        # use get_webull_option_quotes / place/preview flows for live execution pricing).
        prev_resp = requests.get(
            f"{MASSIVE_BASE_URL}/v2/aggs/ticker/{symbol}/prev",
            headers=headers,
            timeout=10,
        )
        prev_resp.raise_for_status()
        prev_data = prev_resp.json()
        results = prev_data.get("results", [])
        if not results:
            return f"Error: no reference price found for {symbol}"
        ref_price = results[0].get("c")
        if not ref_price:
            return f"Error: could not parse reference price for {symbol}"

        strike_lo = round(ref_price * (1 - strike_range_pct), 2)
        strike_hi = round(ref_price * (1 + strike_range_pct), 2)

        today = date.today()
        exp_lo = (today + timedelta(days=min_dte)).isoformat()
        exp_hi = (today + timedelta(days=max_dte)).isoformat()

        params = {
            "expiration_date.gte": exp_lo,
            "expiration_date.lte": exp_hi,
            "strike_price.gte": strike_lo,
            "strike_price.lte": strike_hi,
            "limit": min(limit, 250),
            "sort": "strike_price",
        }
        if option_type:
            params["contract_type"] = option_type.lower()

        chain_resp = requests.get(
            f"{MASSIVE_BASE_URL}/v3/snapshot/options/{symbol}",
            headers=headers,
            params=params,
            timeout=15,
        )
        chain_resp.raise_for_status()
        chain_data = chain_resp.json()

        contracts = []
        for r in chain_data.get("results", []):
            details = r.get("details", {}) or {}
            day = r.get("day", {}) or {}
            greeks = r.get("greeks", {}) or {}
            contracts.append({
                "ticker": details.get("ticker"),
                "strike": details.get("strike_price"),
                "expiry": details.get("expiration_date"),
                "type": details.get("contract_type"),
                "open_interest": r.get("open_interest"),
                "implied_volatility": r.get("implied_volatility"),
                "delta": greeks.get("delta"),
                "gamma": greeks.get("gamma"),
                "theta": greeks.get("theta"),
                "vega": greeks.get("vega"),
                "day_close": day.get("close"),
                "day_volume": day.get("volume"),
            })

        return json.dumps({
            "underlying": symbol,
            "reference_price": ref_price,
            "strike_range": [strike_lo, strike_hi],
            "expiry_range": [exp_lo, exp_hi],
            "contract_count": len(contracts),
            "contracts": contracts,
        }, indent=2)
    except requests.exceptions.RequestException as e:
        return f"Error calling Massive API: {e}"
    except Exception as e:
        return f"Error: {e}"

@mcp.tool()
def get_webull_option_quotes(option_codes: str) -> str:
    """Get live Webull bid/ask/greeks for specific option contracts (real execution pricing).
    Feed this the 'ticker' values from get_options_chain results (strip the leading 'O:' prefix,
    e.g. 'O:SPY260706C00500000' -> 'SPY260706C00500000').

    option_codes: comma-separated Webull option codes, up to 20 per call
                  e.g. 'SPY260706C00500000,SPY260706C00505000'
    """
    try:
        dc = get_data_client()
        codes = [c.strip().lstrip("O:") for c in option_codes.split(",") if c.strip()]
        if len(codes) > 20:
            return "Error: max 20 option codes per call"
        res = dc.option_market_data.get_option_snapshot(codes, "US_OPTION")
        return json.dumps(res.json(), indent=2)
    except Exception as e:
        return f"Error: {getattr(e, 'error_msg', str(e))}"

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
