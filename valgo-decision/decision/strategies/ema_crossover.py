"""Example strategy: EMA(9) / EMA(21) crossover on the underlying.

This is illustrative — not investment advice. Logic:
    - Maintain a rolling window of the underlying's last_price values
    - Compute EMA(9) and EMA(21) via TA-Lib (C-backed, ~100x faster than pure Python)
    - On golden cross (EMA9 crosses above EMA21): BUY ATM CE
    - On death cross: SELL the open CE position

Why TA-Lib: when you scale to dozens of strategies each computing 5+ indicators
on every tick across multiple instruments, the C-level speed is the difference
between staying inside the latency budget and missing it.
"""
from __future__ import annotations

from collections import deque
from decimal import Decimal

import numpy as np
import talib

from valgo_common.logging import get_logger
from valgo_common.models import OrderSide, Tick

from .base import StrategyBase

log = get_logger(__name__)


class EmaCrossoverStrategy(StrategyBase):
    """EMA(9)/EMA(21) golden-cross entry, death-cross exit, computed via TA-Lib."""

    UNDERLYING = "NIFTY 50"
    FAST = 9
    SLOW = 21
    WINDOW = 200  # rolling buffer; large enough that EMA has stabilized past warmup

    def __init__(self, config) -> None:
        super().__init__(config)
        self._prices: deque[float] = deque(maxlen=self.WINDOW)
        self._prev_diff: float | None = None
        self._in_position: str | None = None  # tradingsymbol of open position, if any

    async def on_tick(self, tick: Tick) -> None:
        if tick.tradingsymbol != self.UNDERLYING:
            return

        self._prices.append(float(tick.last_price))

        # Need at least SLOW samples before EMA(SLOW) is meaningful
        if len(self._prices) < self.SLOW:
            return

        # TA-Lib operates on numpy arrays of float64
        arr = np.fromiter(self._prices, dtype=np.float64)
        ema_fast = talib.EMA(arr, timeperiod=self.FAST)[-1]
        ema_slow = talib.EMA(arr, timeperiod=self.SLOW)[-1]

        # Either EMA may be NaN during warmup
        if np.isnan(ema_fast) or np.isnan(ema_slow):
            return

        diff = ema_fast - ema_slow
        prev = self._prev_diff
        self._prev_diff = diff

        if prev is None:
            return  # need a previous diff to detect a cross

        crossed_up = prev <= 0 < diff
        crossed_down = prev >= 0 > diff

        if crossed_up and not self._in_position:
            symbol = self._pick_atm_ce(tick.last_price)
            await self.emit_order(symbol, OrderSide.BUY, self.config.quantity)
            self._in_position = symbol
            log.info("strategy.golden_cross", strategy=self.id, symbol=symbol,
                     ema_fast=round(ema_fast, 2), ema_slow=round(ema_slow, 2))
        elif crossed_down and self._in_position:
            await self.emit_order(self._in_position, OrderSide.SELL, self.config.quantity)
            log.info("strategy.death_cross", strategy=self.id, exited=self._in_position,
                     ema_fast=round(ema_fast, 2), ema_slow=round(ema_slow, 2))
            self._in_position = None

    def _pick_atm_ce(self, spot_price: Decimal) -> str:
        """Round to nearest 50, return e.g. 'NIFTY26500CE' for the current weekly expiry."""
        atm = int(round(float(spot_price) / 50)) * 50
        # Real implementation: look up the actual current-week expiry symbol
        # via the Kite instrument dump (cached in DDB at /config/instruments).
        return f"NIFTY{atm}CE"
