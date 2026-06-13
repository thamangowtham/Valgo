"""Shoonya (Finvasia) broker adapter — using NorenRestApiPy SDK.

Authentication flow:
  Step 1: Open browser:
          https://trade.shoonya.com/OAuthlogin/investor-entry-level/login
          ?api_key=FN213657_U&route_to=FN213657
  Step 2: Login → copy CODE from redirect URL
  Step 3: Run: python scripts/shoonya_login.py --code YOUR_CODE
  Step 4: access_token saved to shoonya_token.txt automatically
  Step 5: All API calls use the saved token (valid for the trading day)

Exchange codes: NSE / BSE / NFO / MCX / CDS
Product codes:  C=Delivery  I=Intraday  M=Margin  H=CoverOrder  B=BracketOrder
Order types:    LMT / MKT / SL-LMT / SL-MKT
Trans types:    B=Buy  S=Sell
Retention:      DAY / IOC / EOS
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from NorenRestApiPy.NorenApi import NorenApi

from valgo_common.logging import get_logger

log = get_logger(__name__)

# ── API constants (correct endpoints from working code) ───────────────────────
HOST_URL  = "https://api.shoonya.com/NorenWClientAPI/"
WS_URL    = "wss://api.shoonya.com/NorenWS/"
AUTH_URL  = (
    "https://trade.shoonya.com/OAuthlogin/investor-entry-level/login"
    "?api_key={client_id}&route_to={user_id}"
)

# Token file — stored next to this file
TOKEN_FILE = Path(__file__).parent / "shoonya_token.txt"

# Credentials
USER_ID    = os.getenv("SHOONYA_USER_ID",    "FN213657")
CLIENT_ID  = os.getenv("SHOONYA_VENDOR_CODE","FN213657_U")
SECRET_CODE= os.getenv("SHOONYA_SECRET_CODE",
    "oepmiLp1HNkkEyDQIpd0YaEKJkycfNxm4QJfJCDNpq21aJp1Q3Ed2zJmeydLzyoo")

# Product / order type mappings
_PRODUCT_MAP: dict[str, str] = {
    "MIS": "I", "NRML": "M", "CNC": "C", "CO": "H", "BO": "B",
}
_ORDER_TYPE_MAP: dict[str, str] = {
    "MARKET": "MKT", "LIMIT": "LMT", "SL": "SL-LMT", "SL-M": "SL-MKT",
}


# ── SDK wrapper ───────────────────────────────────────────────────────────────

class _ShoonyaApi(NorenApi):
    def __init__(self):
        super().__init__(host=HOST_URL, websocket=WS_URL)

    def injectOAuthHeader(self, access_token: str, user_id: str, account_id: str) -> None:
        """Set a saved OAuth access token so API calls work without re-logging in.
        NorenRestApiPy stores the session token as self.susertoken (used as jKey
        in every request payload). uid / actid are needed by order/position calls.
        """
        self.susertoken = access_token
        self.uid        = user_id
        self.actid      = account_id or user_id


# ── Token helpers ─────────────────────────────────────────────────────────────

def load_token() -> str | None:
    """Load saved access token from file."""
    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text().strip()
        return token if token else None
    return None


def save_token(token: str) -> None:
    """Save access token to file."""
    TOKEN_FILE.write_text(token)
    log.info("shoonya.token_saved", path=str(TOKEN_FILE))


def get_auth_url() -> str:
    return AUTH_URL.format(client_id=CLIENT_ID, user_id=USER_ID)


def create_access_token(auth_code: str) -> str:
    """
    Exchange OAuth auth_code for access_token using SDK.
    Saves token to shoonya_token.txt.
    Returns the access_token string.
    """
    api = _ShoonyaApi()
    result = api.getAccessToken(
        authcode=auth_code,
        Secret_Code=SECRET_CODE,
        client_id=CLIENT_ID,
        UID=USER_ID,
    )
    if result is None:
        raise RuntimeError(
            "Failed to get access token.\n"
            "  - Check IP is whitelisted at https://trade.shoonya.com/\n"
            "  - Auth code may have expired — get a fresh one from the URL above."
        )
    acc_tok, usrid, _, actid = result
    save_token(acc_tok)
    log.info("shoonya.token_created", user=usrid, account=actid,
             token_preview=acc_tok[:20] + "...")
    return acc_tok


# ── Adapter ───────────────────────────────────────────────────────────────────

class ShoonyaBrokerAdapter:
    """
    Async broker adapter using NorenRestApiPy SDK.

    Token is loaded from shoonya_token.txt on startup.
    Run scripts/shoonya_login.py to generate a fresh token.
    """

    def __init__(self) -> None:
        self._api  = _ShoonyaApi()
        token = load_token()
        if not token:
            log.error("shoonya_adapter.no_token",
                      hint="Run: python scripts/shoonya_login.py --code YOUR_CODE")
            raise RuntimeError(
                "No Shoonya access token found.\n"
                f"  1. Open: {get_auth_url()}\n"
                "  2. Login and copy the code from redirect URL\n"
                "  3. Run: python scripts/shoonya_login.py --code YOUR_CODE"
            )
        self._api.injectOAuthHeader(token, USER_ID, USER_ID)
        log.info("shoonya_adapter.ready", token_preview=token[:20] + "...")

    # ── Orders ────────────────────────────────────────────────────────────────

    async def place_order(self, req: dict) -> str:
        """Place order. Returns Shoonya norenordno (order ID)."""
        side       = req.get("side", "BUY")
        order_type = req.get("order_type", "MARKET")
        product    = req.get("product", "MIS")
        price      = float(req.get("price") or 0)
        trig_price = req.get("trigger_price")
        exchange   = req.get("exchange", "NFO")

        log.info("shoonya_adapter.placing_order",
                 symbol=req["tradingsymbol"], side=side,
                 qty=req["quantity"], order_type=order_type)

        ret = await asyncio.to_thread(
            self._api.place_order,
            buy_or_sell   = "B" if side == "BUY" else "S",
            product_type  = _PRODUCT_MAP.get(product, "I"),
            exchange      = exchange,
            tradingsymbol = str(req["tradingsymbol"]),
            quantity      = int(req["quantity"]),
            discloseqty   = 0,
            price_type    = _ORDER_TYPE_MAP.get(order_type, "MKT"),
            price         = price,
            trigger_price = float(trig_price) if trig_price else None,
            retention     = "DAY",
            remarks       = str(req.get("tag", "valgo"))[:30],
        )

        if not ret or ret.get("stat") != "Ok":
            raise RuntimeError(f"Shoonya rejected order: {ret}")

        order_id = ret["norenordno"]
        log.info("shoonya_adapter.order_placed",
                 norenordno=order_id, symbol=req["tradingsymbol"])
        return order_id

    async def cancel_order(self, broker_order_id: str) -> bool:
        ret = await asyncio.to_thread(self._api.cancel_order, broker_order_id)
        ok = bool(ret and ret.get("stat") == "Ok")
        log.info("shoonya_adapter.cancel_order", orderno=broker_order_id, ok=ok)
        return ok

    async def get_order_status(self, broker_order_id: str) -> dict:
        ret = await asyncio.to_thread(
            self._api.single_order_history, broker_order_id
        )
        if isinstance(ret, list) and ret:
            return ret[-1]
        return {}

    async def get_order_book(self) -> list[dict]:
        ret = await asyncio.to_thread(self._api.get_order_book)
        return ret if isinstance(ret, list) else []

    async def get_limits(self) -> dict:
        ret = await asyncio.to_thread(self._api.get_limits)
        return ret if isinstance(ret, dict) else {}

    async def get_quote(self, exchange: str, token: str) -> dict:
        ret = await asyncio.to_thread(self._api.get_quotes, exchange, token)
        return ret if isinstance(ret, dict) else {}
