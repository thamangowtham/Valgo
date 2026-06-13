"""Shoonya (Finvasia) WebSocket tick source.

Mirrors the KiteTickSource interface — connects to Shoonya's NorenWS,
subscribes to instruments in FULL mode, normalises ticks to the common
Tick model, and calls the on_tick callback so the rest of the ingestor
pipeline is broker-agnostic.

Instrument resolution:
  - NSE indices (NIFTY, BANKNIFTY, FINNIFTY …) use well-known static tokens.
  - MCX futures (CRUDEOIL, GOLD, SILVER …) are resolved to the nearest
    front-month contract by downloading Shoonya's daily MCX instrument file.
  - Unknown symbols are tried via the Shoonya searchscrip API.

The `tradingsymbol` field on the published Tick is set to the strategy-facing
name (e.g. "BANKNIFTY", "CRUDEOIL") so it matches `config.instruments` and
the Redis channel subscriptions in the decision engine.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import zipfile
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable

import pyotp
import requests

from valgo_common.config import settings
from valgo_common.logging import get_logger
from valgo_common.models import DepthLevel, Tick, TickMode

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Shoonya SDK bootstrap
# ---------------------------------------------------------------------------
try:
    from NorenRestApiPy.NorenApi import NorenApi as _NorenBase

    class _ShoonyaSession(_NorenBase):
        def __init__(self) -> None:
            _NorenBase.__init__(
                self,
                host="https://api.shoonya.com/NorenWClientTP/",
                websocket="wss://api.shoonya.com/NorenWSTP/",
            )

except ImportError:
    log.warning("shoonya.sdk_not_installed — pip install NorenRestApiPy")

    class _ShoonyaSession:  # type: ignore[no-redef]
        def login(self, **kw): return {"stat": "Not_Ok", "emsg": "SDK not installed"}
        def start_websocket(self, **kw): pass
        def subscribe(self, scrips): pass
        def close_websocket(self): pass
        def searchscrip(self, **kw): return {}

# ---------------------------------------------------------------------------
# Known static Shoonya tokens for NSE indices (never change)
# ---------------------------------------------------------------------------
_STATIC: dict[str, tuple[str, str]] = {
    "NIFTY":        ("NSE", "26000"),
    "BANKNIFTY":    ("NSE", "26009"),
    "FINNIFTY":     ("NSE", "257801"),
    "MIDCPNIFTY":   ("NSE", "288009"),
    "SENSEX":       ("BSE", "1"),
    "BANKEX":       ("BSE", "12"),
}

# MCX commodity names as they appear in Shoonya's Symbol column
_MCX_NAMES: dict[str, str] = {
    "CRUDEOIL":  "CRUDEOIL",
    "CRUDEOILM": "CRUDEOILM",
    "GOLD":      "GOLD",
    "GOLDM":     "GOLDM",
    "GOLDPETAL": "GOLDPETAL",
    "SILVER":    "SILVER",
    "SILVERM":   "SILVERM",
    "SILVERMIC": "SILVERMIC",
    "COPPER":    "COPPER",
    "ZINC":      "ZINC",
    "LEAD":      "LEAD",
    "NICKEL":    "NICKEL",
    "ALUMINIUM": "ALUMINIUM",
    "NATURALGAS":"NATURALGAS",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _totp(seed: str) -> str:
    return pyotp.TOTP(seed).now()


def _dec(v: Any) -> Decimal | None:
    try:
        return Decimal(str(v)) if v is not None and str(v) != "" else None
    except Exception:
        return None


async def _maybe_async(fn: Callable, *args: Any, **kwargs: Any) -> Any:
    result = fn(*args, **kwargs)
    if asyncio.iscoroutine(result):
        return await result
    return result


# ---------------------------------------------------------------------------
# Instrument file fetch + resolution
# ---------------------------------------------------------------------------
def _fetch_shoonya_instruments(exchange: str) -> list[dict]:
    """Download and parse Shoonya's daily instrument file for an exchange."""
    url = f"https://api.shoonya.com/{exchange}_symbols.txt.zip"
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        text = zf.read(zf.namelist()[0]).decode("utf-8")
        lines = text.strip().splitlines()
        if not lines:
            return []
        headers = [h.strip() for h in lines[0].split(",")]
        rows: list[dict] = []
        for line in lines[1:]:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= len(headers):
                rows.append(dict(zip(headers, parts)))
        log.info("shoonya.instruments_loaded", exchange=exchange, count=len(rows))
        return rows
    except Exception as e:
        log.error("shoonya.instruments_fetch_failed", exchange=exchange, error=str(e))
        return []


def _build_front_month_map(rows: list[dict]) -> dict[str, tuple[str, str]]:
    """Return {Symbol_name: (token, tradingsymbol)} for nearest-expiry FUT."""
    today = date.today().isoformat()  # YYYY-MM-DD

    # Shoonya expiry can be "26-May-2026" — normalise to YYYY-MM-DD for sort
    def _normalise_expiry(raw: str) -> str:
        raw = raw.strip()
        if not raw:
            return "9999-12-31"
        for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y"):
            try:
                return datetime.strptime(raw, fmt).date().isoformat()
            except ValueError:
                pass
        return raw

    by_name: dict[str, list[tuple[str, dict]]] = {}
    for r in rows:
        inst = r.get("Instrument", r.get("instrument", "")).strip()
        if inst not in ("FUTCOM", "FUTSTK", "FUTIDX", "FUT"):
            continue
        name = r.get("Symbol", r.get("symbol", "")).strip()
        if not name:
            continue
        exp = _normalise_expiry(r.get("Expiry", r.get("expiry", "")))
        if exp < today:          # skip already-expired contracts
            continue
        by_name.setdefault(name, []).append((exp, r))

    result: dict[str, tuple[str, str]] = {}
    for name, items in by_name.items():
        items.sort(key=lambda x: x[0])     # nearest expiry first
        _, best = items[0]
        tok = best.get("Token", best.get("token", "")).strip()
        ts  = best.get("TradingSymbol", best.get("tradingsymbol", name)).strip()
        if tok:
            result[name] = (tok, ts)
    return result


# ---------------------------------------------------------------------------
# Main source class
# ---------------------------------------------------------------------------
class ShoonyaTickSource:
    """Shoonya WebSocket market-data source. Drop-in replacement for KiteTickSource."""

    name        = "Shoonya"
    provider_id = "shoonya"

    def __init__(
        self,
        on_tick: Callable[[Tick], Any],
        on_status_change: Callable[[str], Any] | None = None,
    ) -> None:
        self._on_tick          = on_tick
        self._on_status_change = on_status_change or (lambda s: None)
        self._api              = _ShoonyaSession()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._token_to_symbol: dict[str, str] = {}   # "26000" → "NIFTY"
        self._subscribed: list[str] = []
        self._connected = False

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def login(self) -> None:
        """Login to Shoonya — SDK handles SHA256 hashing internally."""
        twofa = _totp(settings.shoonya_totp_seed) if settings.shoonya_totp_seed else ""
        ret   = self._api.login(
            userid      = settings.shoonya_user_id,
            password    = settings.shoonya_password,
            twoFA       = twofa,
            vendor_code = settings.shoonya_vendor_code,
            api_secret  = settings.shoonya_api_secret,
            imei        = settings.shoonya_imei,
        )
        if not ret or ret.get("stat") != "Ok":
            raise RuntimeError(f"Shoonya login failed: {ret}")
        log.info("shoonya.logged_in", user=settings.shoonya_user_id)

    async def start(self, symbols: list[str]) -> None:
        """Resolve symbols → tokens, open WebSocket, subscribe."""
        self._loop = asyncio.get_running_loop()

        scrips, token_map = await self._loop.run_in_executor(
            None, self._resolve_symbols, symbols
        )
        self._token_to_symbol = token_map

        self._api.start_websocket(
            subscribe_callback      = self._on_ticks_handler,
            order_update_callback   = lambda msg: None,
            socket_open_callback    = self._on_open_handler,
            socket_error_callback   = self._on_error_handler,
            socket_close_callback   = self._on_close_handler,
        )

        # Wait up to 10 s for the connection to open
        for _ in range(100):
            if self._connected:
                break
            await asyncio.sleep(0.1)
        else:
            raise TimeoutError("Shoonya WebSocket did not connect within 10 s")

        self._subscribed = scrips
        self._api.subscribe(scrips)
        log.info("shoonya.subscribed", count=len(scrips), scrips=scrips)

    async def stop(self) -> None:
        self._api.close_websocket()
        self._connected = False

    async def update_subscription(self, symbols: list[str]) -> None:
        scrips, token_map = await asyncio.get_running_loop().run_in_executor(
            None, self._resolve_symbols, symbols
        )
        self._token_to_symbol.update(token_map)
        self._api.subscribe(scrips)
        self._subscribed = scrips
        log.info("shoonya.subscription_updated", count=len(scrips))

    # ── WebSocket callbacks (run on Shoonya's internal thread) ───────────────

    def _on_open_handler(self) -> None:
        log.info("shoonya.connected")
        self._connected = True
        self._dispatch_status("connected")

    def _on_close_handler(self) -> None:
        log.warning("shoonya.disconnected")
        self._connected = False
        self._dispatch_status("disconnected")

    def _on_error_handler(self, msg: Any) -> None:
        log.error("shoonya.ws_error", msg=str(msg))
        self._dispatch_status("error")

    def _on_ticks_handler(self, msg: dict) -> None:
        # Shoonya sends various message types; only process quote feed messages
        if not msg or msg.get("t") not in ("tk", "tf", "dk", "df"):
            return
        try:
            tick = self._normalize(msg)
        except Exception as e:
            log.error("shoonya.normalize_failed", error=str(e), raw=msg)
            return
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                _maybe_async(self._on_tick, tick), self._loop
            )

    # ── Tick normalisation ───────────────────────────────────────────────────

    def _normalize(self, msg: dict) -> Tick:
        token  = msg.get("tk", "")
        symbol = self._token_to_symbol.get(token, token)

        # Shoonya sends epoch seconds in "ft" (feed time) or "ts" (string timestamp)
        ts_raw = msg.get("ft")
        if ts_raw:
            try:
                ts = datetime.fromtimestamp(int(ts_raw))
            except Exception:
                ts = datetime.utcnow()
        else:
            ts = datetime.utcnow()

        # Depth (bp1/sp1 … bp5/sp5 with bq1/sq1 …)
        depth_buy  = [
            DepthLevel(price=Decimal(str(msg[f"bp{i}"])), quantity=int(msg.get(f"bq{i}", 0) or 0))
            for i in range(1, 6) if msg.get(f"bp{i}")
        ]
        depth_sell = [
            DepthLevel(price=Decimal(str(msg[f"sp{i}"])), quantity=int(msg.get(f"sq{i}", 0) or 0))
            for i in range(1, 6) if msg.get(f"sp{i}")
        ]

        return Tick(
            instrument_token    = int(token) if token.isdigit() else 0,
            tradingsymbol       = symbol,
            last_price          = Decimal(str(msg.get("lp") or 0)),
            last_traded_quantity= int(msg.get("ltq") or 0),
            timestamp           = ts,
            mode                = TickMode.FULL,
            ohlc_open           = _dec(msg.get("o")),
            ohlc_high           = _dec(msg.get("h")),
            ohlc_low            = _dec(msg.get("l")),
            ohlc_close          = _dec(msg.get("c")),
            volume              = int(msg.get("v") or 0) or None,
            oi                  = int(msg.get("oi") or 0) or None,
            average_price       = _dec(msg.get("ap")),
            depth_buy           = depth_buy,
            depth_sell          = depth_sell,
            source              = self.provider_id,
        )

    # ── Instrument resolution ────────────────────────────────────────────────

    def _resolve_symbols(self, symbols: list[str]) -> tuple[list[str], dict[str, str]]:
        """Map strategy symbol names → ["NSE|26000", …] and build reverse map."""
        scrips:    list[str]       = []
        token_map: dict[str, str]  = {}   # token_str → strategy symbol name

        mcx_needed = [s for s in symbols if s.upper() in _MCX_NAMES]
        mcx_front  = {}
        if mcx_needed:
            mcx_rows  = _fetch_shoonya_instruments("MCX")
            mcx_front = _build_front_month_map(mcx_rows)

        for sym in symbols:
            up = sym.upper()
            if up in _STATIC:
                exch, tok = _STATIC[up]
                scrips.append(f"{exch}|{tok}")
                token_map[tok] = sym
                log.info("shoonya.resolved_static", symbol=sym, scrip=f"{exch}|{tok}")

            elif up in _MCX_NAMES:
                entry = mcx_front.get(_MCX_NAMES[up])
                if entry:
                    tok, ts = entry
                    scrips.append(f"MCX|{tok}")
                    token_map[tok] = sym
                    log.info("shoonya.resolved_mcx", symbol=sym, tradingsymbol=ts, token=tok)
                else:
                    log.warning("shoonya.mcx_not_found", symbol=sym)

            else:
                # Generic fallback — search NSE then BSE
                for exch in ("NSE", "BSE", "NFO"):
                    ret = self._api.searchscrip(exchange=exch, searchtext=sym)
                    if ret and ret.get("stat") == "Ok" and ret.get("values"):
                        first = ret["values"][0]
                        tok = first.get("token", "")
                        scrips.append(f"{exch}|{tok}")
                        token_map[tok] = sym
                        log.info("shoonya.resolved_search", symbol=sym, exchange=exch, token=tok)
                        break
                else:
                    log.warning("shoonya.symbol_not_resolved", symbol=sym)

        return scrips, token_map

    def _dispatch_status(self, status: str) -> None:
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                _maybe_async(self._on_status_change, status), self._loop
            )
