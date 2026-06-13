"""Kite Connect WebSocket source.

Implements the TickSource protocol — connects to wss://ws.kite.trade,
subscribes in FULL mode (LTP + OHLC + 5-level depth), normalizes ticks
to the common Tick model, and pushes them to Redis.

Reconnection is delegated to the kiteconnect SDK with our backoff config.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable

from kiteconnect import KiteTicker

from valgo_common.config import settings
from valgo_common.logging import get_logger
from valgo_common.models import DepthLevel, Tick, TickMode

log = get_logger(__name__)


class KiteTickSource:
    """Async wrapper around KiteTicker. Pushes normalized Ticks to a callback.

    Auth modes (mutually exclusive — pass one):
      enctoken     — browser enctoken from kite.zerodha.com cookies (no API key needed)
      api_key + access_token — standard Kite Connect OAuth token
    """

    name = "Zerodha Kite"
    provider_id = "kite"

    def __init__(
        self,
        on_tick: Callable[[Tick], Any],
        enctoken: str = "",
        api_key: str = "",
        access_token: str = "",
        on_status_change: Callable[[str], Any] | None = None,
        max_reconnect_attempts: int = 5,
        reconnect_max_delay: int = 32,
        token_to_symbol: dict[int, str] | None = None,
    ) -> None:
        if enctoken:
            self._api_key = "kitefront"   # Zerodha browser app key — accepted by WS with enctoken
            self._access_token = enctoken
            self._enctoken = enctoken
        else:
            self._api_key = api_key
            self._access_token = access_token
            self._enctoken = ""
        self._on_tick = on_tick
        self._on_status_change = on_status_change or (lambda s: None)
        self._max_reconnect = max_reconnect_attempts
        self._reconnect_max_delay = reconnect_max_delay
        self._token_to_symbol: dict[int, str] = token_to_symbol or {}

        self._ticker: KiteTicker | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tokens: list[int] = []
        self._connected = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self, instrument_tokens: list[int]) -> None:
        """Connect and subscribe. Returns once initial connection succeeds."""
        self._loop = asyncio.get_running_loop()
        self._tokens = list(instrument_tokens)

        self._ticker = KiteTicker(self._api_key, self._access_token)
        self._ticker.on_ticks = self._on_ticks_handler
        self._ticker.on_connect = self._on_connect_handler
        self._ticker.on_close = self._on_close_handler
        self._ticker.on_error = self._on_error_handler
        self._ticker.on_reconnect = self._on_reconnect_handler
        self._ticker.on_noreconnect = self._on_noreconnect_handler

        # KiteTicker.connect() runs the WebSocket loop in its own thread when
        # threaded=True. We're async — let it run threaded so it doesn't block.
        # Reconnection options moved to instance attributes in kiteconnect 5+.
        self._ticker.reconnect_max_tries = self._max_reconnect
        self._ticker.reconnect_max_delay = self._reconnect_max_delay
        self._ticker.connect(threaded=True, disable_ssl_verification=False)

        # Wait for the initial connection — caller probably wants to know we're up
        for _ in range(50):  # ~5s
            if self._connected:
                return
            await asyncio.sleep(0.1)
        raise TimeoutError("Kite WebSocket failed to connect within 5s")

    async def stop(self) -> None:
        if self._ticker:
            self._ticker.close(code=1000, reason="orderly shutdown")
            self._ticker = None
        self._connected = False

    async def update_subscription(self, instrument_tokens: list[int]) -> None:
        """Replace the active subscription set."""
        if not self._ticker or not self._connected:
            log.warning("kite.update_subscription_skipped_not_connected")
            return

        old = set(self._tokens)
        new = set(instrument_tokens)
        to_remove = list(old - new)
        to_add = list(new - old)

        if to_remove:
            self._ticker.unsubscribe(to_remove)
        if to_add:
            self._ticker.subscribe(to_add)
            self._ticker.set_mode(self._ticker.MODE_FULL, to_add)

        self._tokens = list(new)
        log.info("kite.subscription_updated", added=len(to_add), removed=len(to_remove), total=len(new))

    # ------------------------------------------------------------------
    # KiteTicker callbacks (run on KiteTicker's thread)
    # ------------------------------------------------------------------
    def _on_connect_handler(self, ws: Any, response: Any) -> None:
        log.info("kite.connected", instrument_count=len(self._tokens))
        self._connected = True
        if self._tokens:
            ws.subscribe(self._tokens)
            ws.set_mode(ws.MODE_FULL, self._tokens)
        self._dispatch_status("connected")

    def _on_ticks_handler(self, ws: Any, kite_ticks: list[dict]) -> None:
        for kt in kite_ticks:
            try:
                tick = self._normalize(kt)
            except Exception as e:
                log.error("kite.normalize_failed", error=str(e), raw=kt)
                continue
            # Dispatch tick callback on the asyncio loop
            if self._loop and not self._loop.is_closed():
                asyncio.run_coroutine_threadsafe(
                    _maybe_async(self._on_tick, tick), self._loop
                )

    def _on_close_handler(self, ws: Any, code: int, reason: str) -> None:
        log.warning("kite.disconnected", code=code, reason=reason)
        self._connected = False
        self._dispatch_status("disconnected")

    def _on_error_handler(self, ws: Any, code: int, reason: str) -> None:
        log.error("kite.error", code=code, reason=reason)

    def _on_reconnect_handler(self, ws: Any, attempts_count: int) -> None:
        log.info("kite.reconnecting", attempt=attempts_count)
        self._dispatch_status("reconnecting")

    def _on_noreconnect_handler(self, ws: Any) -> None:
        log.error("kite.gave_up_reconnecting", max_attempts=self._max_reconnect)
        self._connected = False
        self._dispatch_status("failed")

    # ------------------------------------------------------------------
    # Normalization: Kite tick dict → common Tick model
    # ------------------------------------------------------------------
    def _normalize(self, kt: dict) -> Tick:
        depth = kt.get("depth", {})
        tok = int(kt["instrument_token"])
        tradingsymbol = self._token_to_symbol.get(tok) or str(kt.get("tradingsymbol", tok))
        return Tick(
            instrument_token=tok,
            tradingsymbol=tradingsymbol,
            last_price=Decimal(str(kt.get("last_price", 0))),
            last_traded_quantity=int(kt.get("last_traded_quantity") or 0),
            timestamp=kt.get("timestamp") or datetime.utcnow(),
            mode=TickMode.FULL,
            ohlc_open=_dec(kt.get("ohlc", {}).get("open")),
            ohlc_high=_dec(kt.get("ohlc", {}).get("high")),
            ohlc_low=_dec(kt.get("ohlc", {}).get("low")),
            ohlc_close=_dec(kt.get("ohlc", {}).get("close")),
            volume=kt.get("volume_traded"),
            oi=kt.get("oi"),
            average_price=_dec(kt.get("average_traded_price")),
            depth_buy=[_depth_level(d) for d in depth.get("buy", [])],
            depth_sell=[_depth_level(d) for d in depth.get("sell", [])],
            source=self.provider_id,
        )

    def _dispatch_status(self, status: str) -> None:
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                _maybe_async(self._on_status_change, status), self._loop
            )


def _dec(v: Any) -> Decimal | None:
    return Decimal(str(v)) if v is not None else None


def _depth_level(d: dict) -> DepthLevel:
    return DepthLevel(
        price=Decimal(str(d.get("price", 0))),
        quantity=int(d.get("quantity") or 0),
        orders=int(d.get("orders") or 0),
    )


async def _maybe_async(fn: Callable, *args, **kwargs):
    """Call fn — await it if it's a coroutine."""
    result = fn(*args, **kwargs)
    if asyncio.iscoroutine(result):
        return await result
    return result
