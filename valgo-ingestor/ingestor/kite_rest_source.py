"""Kite REST polling tick source — no WebSocket / no Kite Connect subscription.

Polls kite.zerodha.com/oms/instruments/historical/{token}/minute every
poll_interval seconds using enctoken.  Takes the latest completed candle's
close as last_price and emits a Tick to the same callback used by
KiteTickSource, so all strategies work unchanged.

Price resolution: 1-minute candle close (updates once per minute when market
is open).  Sub-minute last_price is not available without a Kite Connect
WebSocket subscription.

Switch back to KiteTickSource when a Kite Connect subscription is available.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable

import requests

from valgo_common.logging import get_logger
from valgo_common.models import Tick, TickMode

log = get_logger(__name__)

OMS_BASE = "https://kite.zerodha.com/oms"


class KiteRestTickSource:
    """Polls Zerodha OMS historical API, emits Ticks via callback.

    Drop-in replacement for KiteTickSource when only an enctoken is available.
    Does not require a Kite Connect WebSocket subscription.
    """

    name = "Zerodha Kite (REST)"
    provider_id = "kite"

    def __init__(
        self,
        enctoken: str,
        on_tick: Callable[[Tick], Any],
        token_to_symbol: dict[int, str],
        on_status_change: Callable[[str], Any] | None = None,
        poll_interval: float = 1.0,
    ) -> None:
        self._enctoken = enctoken
        self._on_tick = on_tick
        self._token_to_symbol = token_to_symbol
        self._on_status_change = on_status_change or (lambda s: None)
        self._poll_interval = poll_interval
        self._running = False
        self._task: asyncio.Task | None = None
        self._tokens: list[int] = []
        # Cache last known tick per token so we keep publishing when market is closed
        self._last_tick: dict[int, Tick] = {}

        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"enctoken {enctoken}",
            "X-Kite-Version": "3",
        })

    async def start(self, instrument_tokens: list[int]) -> None:
        self._tokens = list(instrument_tokens)
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        self._on_status_change("connected")
        log.info("kite_rest.started", tokens=len(instrument_tokens),
                 interval=self._poll_interval)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
        self._on_status_change("disconnected")

    async def update_subscription(self, instrument_tokens: list[int]) -> None:
        self._tokens = list(instrument_tokens)
        log.info("kite_rest.subscription_updated", tokens=len(instrument_tokens))

    # ------------------------------------------------------------------
    # Poll loop
    # ------------------------------------------------------------------
    async def _poll_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while self._running:
            try:
                # Run blocking HTTP calls in thread pool to stay non-blocking
                await asyncio.gather(*(
                    loop.run_in_executor(None, self._fetch_and_emit, t)
                    for t in self._tokens
                ))
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.warning("kite_rest.poll_exception", error=str(e))

            await asyncio.sleep(self._poll_interval)

    def _fetch_and_emit(self, token: int) -> None:
        """Fetch latest 1-min candle for one token (runs in thread pool)."""
        ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        to_dt = ist.strftime("%Y-%m-%d %H:%M:%S")
        fr_dt = (ist - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")

        try:
            r = self._session.get(
                f"{OMS_BASE}/instruments/historical/{token}/minute",
                params={"from": fr_dt, "to": to_dt, "continuous": 0, "oi": 0},
                timeout=5,
            )
        except Exception as e:
            log.warning("kite_rest.request_failed", token=token, error=str(e))
            return

        if r.status_code == 403:
            log.error("kite_rest.enctoken_expired",
                      hint="Update KITE_ENCTOKEN in dev.env and restart")
            self._running = False
            self._on_status_change("failed")
            return

        if r.status_code != 200:
            log.warning("kite_rest.http_error", token=token, status=r.status_code)
            return

        candles = r.json().get("data", {}).get("candles", [])
        if not candles:
            # Market closed — re-emit last known tick if we have one
            if token in self._last_tick:
                tick = self._last_tick[token]
                asyncio.run_coroutine_threadsafe(
                    _maybe_async(self._on_tick, tick),
                    asyncio.get_event_loop(),
                )
            return

        last = candles[-1]  # [timestamp, open, high, low, close, volume]
        symbol = self._token_to_symbol.get(token, str(token))
        tick = Tick(
            instrument_token=token,
            tradingsymbol=symbol,
            last_price=Decimal(str(last[4])),   # close
            last_traded_quantity=0,
            timestamp=datetime.utcnow(),
            mode=TickMode.FULL,
            ohlc_open=Decimal(str(last[1])),
            ohlc_high=Decimal(str(last[2])),
            ohlc_low=Decimal(str(last[3])),
            ohlc_close=Decimal(str(last[4])),
            volume=int(last[5]) if len(last) > 5 else None,
            source=self.provider_id,
        )
        self._last_tick[token] = tick
        asyncio.run_coroutine_threadsafe(
            _maybe_async(self._on_tick, tick),
            asyncio.get_event_loop(),
        )


async def _maybe_async(fn: Callable, *args, **kwargs):
    result = fn(*args, **kwargs)
    if asyncio.iscoroutine(result):
        return await result
    return result
