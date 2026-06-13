"""MCX multi-commodity strategy — tick-driven, hot-path-optimized.

Same signal logic as ``mcx_multi.py`` (the bar-polling reference port), but
designed to run inside the engine's <1s data-to-decision latency budget:

    Hot path (every tick):
        - Update the rolling 1-min OHLC builder
        - Compare ltp against cached SuperTrend / ATR thresholds
        - Emit BUY_EXIT / SEL_EXIT immediately if breached

    Bar close (every 5 min, per symbol):
        - Aggregate 1-min bars into a closed 5-min bar
        - Recompute EMA / SMA / ATR / PSAR / SuperTrend on the bar series
        - Refresh the 30-min HTF 'sgl' value
        - Evaluate BUY / SELL conditions; emit if the state machine allows

The engine still calls ``on_tick`` for every published tick. Bar-close work
runs synchronously inside the same call when a 5-min boundary is crossed —
that's the only slow path, and it runs at most once every 300s per symbol.

Warm-up uses ingestor.historical to backfill ~250 closed 5-min bars and
~60 closed 30-min bars, so indicators are usable from the first tick.
"""
from __future__ import annotations

import math
from collections import deque
from datetime import datetime
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
import pytz
from kiteconnect import KiteConnect

from valgo_common.config import settings
from valgo_common.logging import get_logger
from valgo_common.models import OrderSide, Tick
from valgo_common.notifier import telegram_send

from .. import dataframe_indicators as di
from ..strategies.base import StrategyBase

log = get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


# ── Strategy config (matches the reference) ──────────────────────────────────
SYMBOLS = ["CRUDEOIL", "SILVER", "GOLD"]
WARMUP_BARS = 250
HTF_WARMUP_BARS = 60
INTERVAL = "5minute"
HTF_INTERVAL = "30minute"

ST_PERIOD = 10
ST_MULTIPLIER = 2.0
PSAR_AF0 = 0.02
PSAR_MAX_AF = 0.2


def _bar_start(ts: datetime, minutes: int) -> datetime:
    """Round a timestamp down to the start of its N-minute bucket (tz-naive IST)."""
    if ts.tzinfo is not None:
        ts = ts.astimezone(IST).replace(tzinfo=None)
    return ts.replace(minute=(ts.minute // minutes) * minutes, second=0, microsecond=0)


class _SymbolState:
    """Rolling per-symbol state — the only thing on the hot path."""

    def __init__(self, tradingsymbol: str, instrument_token: int) -> None:
        self.tradingsymbol = tradingsymbol
        self.instrument_token = instrument_token

        # Closed 5-min bars (timestamp, open, high, low, close)
        self.bars_5m: deque[dict] = deque(maxlen=WARMUP_BARS + 50)
        # Closed 30-min bars — used only to compute the HTF 'sgl' moving average
        self.bars_30m: deque[dict] = deque(maxlen=HTF_WARMUP_BARS + 10)

        # In-progress 5-min bar: aggregated from incoming ticks
        self.live_5m_start: datetime | None = None
        self.live_5m: dict | None = None
        # In-progress 30-min bar
        self.live_30m_start: datetime | None = None
        self.live_30m: dict | None = None

        # Cached indicator values from the last 5-min bar close.
        # Hot-path code reads these; full recompute populates them.
        self.st: float = float("nan")
        self.atr: float = float("nan")
        self.psar: float = float("nan")
        self.ema_8: float = float("nan")
        self.sma_21: float = float("nan")
        self.sgl: float = float("nan")

        # Position state machine: None | "bought" | "sold"
        self.position: str | None = None

        # Pending BUY/SELL for the current bar — set on bar close, cleared on
        # the next on_tick once the order is emitted.
        self.pending_action: str | None = None


class MCXMultiCommodityTickStrategy(StrategyBase):
    """Tick-driven MCX strategy. Inherits the StrategyBase HTTP order helper."""

    def __init__(self, config, kite: KiteConnect | None = None) -> None:
        super().__init__(config)
        # Lazy import — same reasoning as in mcx_multi.py
        from ingestor import historical, instruments  # type: ignore

        self._historical = historical
        self._instruments = instruments
        self._kite = kite or self._build_kite()

        self._states: dict[int, _SymbolState] = {}     # token -> state
        self._symbols_by_name: dict[str, _SymbolState] = {}
        self._discover_and_warmup()

    @property
    def required_instruments(self) -> list[str]:
        """Resolved MCX FUT tradingsymbols — drives the engine's Redis subscriptions."""
        return [s.tradingsymbol for s in self._states.values()]

    # ──────────────────────────────────────────────────────────────────────
    # Hot path
    # ──────────────────────────────────────────────────────────────────────
    async def on_tick(self, tick: Tick) -> None:
        state = self._states.get(tick.instrument_token)
        if state is None:
            return

        ltp = float(tick.last_price)
        ts = tick.timestamp

        # ── Aggregate into the in-progress 5-min and 30-min bars ──
        bar_closed_5m = self._update_live_bar(state, "5m", ts, ltp)
        self._update_live_bar(state, "30m", ts, ltp)

        # ── Hot-path exit checks: O(1), runs every tick ──
        action = self._check_threshold_exit(state, ltp)
        if action is None and bar_closed_5m:
            # Full indicator recompute happens only on bar close.
            self._recompute_indicators(state)
            action = self._evaluate_entry_exit(state, ltp)

        if action is not None:
            await self._emit_action(state, action, ltp)

    def _check_threshold_exit(self, state: _SymbolState, ltp: float) -> str | None:
        """Cheap exit check using cached SuperTrend and ATR values."""
        if math.isnan(state.st) or math.isnan(state.atr):
            return None
        if state.position == "bought" and ltp < (state.st - state.atr):
            return "BUY_EXIT"
        if state.position == "sold" and ltp > (state.st + state.atr):
            return "SEL_EXIT"
        return None

    def _evaluate_entry_exit(self, state: _SymbolState, ltp: float) -> str | None:
        """Full BUY/SELL eval — same logic as the reference, runs on 5-min close."""
        if any(math.isnan(v) for v in (state.st, state.atr, state.psar, state.ema_8, state.sma_21)):
            return None

        atr, st, psar, sgl = state.atr, state.st, state.psar, state.sgl
        ok = not math.isnan(sgl)

        c1 = ltp > st;   c2 = (ltp - st) < atr
        c3 = (ok and ltp > sgl) or (ok and (sgl - ltp) > atr * 5)
        c4 = ltp > psar
        s1 = ltp < st;   s2 = (st - ltp) < atr
        s3 = (ok and ltp < sgl) or (ok and (ltp - sgl) > atr * 5)
        s4 = ltp < psar

        if state.position is None:
            if c1 and c2 and c3 and c4:
                return "BUY"
            if s1 and s2 and s3 and s4:
                return "SELL"
        elif state.position == "bought" and ltp < (st - atr):
            return "BUY_EXIT"
        elif state.position == "sold" and ltp > (st + atr):
            return "SEL_EXIT"
        return None

    # ──────────────────────────────────────────────────────────────────────
    # Bar aggregation
    # ──────────────────────────────────────────────────────────────────────
    def _update_live_bar(
        self, state: _SymbolState, scope: str, ts: datetime, price: float,
    ) -> bool:
        """Update the in-progress bar; return True if scope's bar just closed."""
        minutes = 5 if scope == "5m" else 30
        live_attr   = f"live_{scope}"
        start_attr  = f"live_{scope}_start"
        closed_deck = state.bars_5m if scope == "5m" else state.bars_30m

        new_start = _bar_start(ts, minutes)
        live_start: datetime | None = getattr(state, start_attr)
        live: dict | None = getattr(state, live_attr)

        bar_closed = False
        if live_start is None:
            setattr(state, start_attr, new_start)
            setattr(state, live_attr, {
                "timestamp": new_start,
                "open": price, "high": price, "low": price, "close": price,
            })
            return False

        if new_start > live_start:
            # Previous bar just closed — append it and start a new one.
            closed_deck.append(live)
            bar_closed = True
            setattr(state, start_attr, new_start)
            setattr(state, live_attr, {
                "timestamp": new_start,
                "open": price, "high": price, "low": price, "close": price,
            })
        else:
            # Same bar — extend it.
            live["high"]  = max(live["high"], price)
            live["low"]   = min(live["low"],  price)
            live["close"] = price

        return bar_closed

    # ──────────────────────────────────────────────────────────────────────
    # Indicators (only on bar close)
    # ──────────────────────────────────────────────────────────────────────
    def _recompute_indicators(self, state: _SymbolState) -> None:
        """Recompute all indicators from the closed-bar deque. Caches into state."""
        bars = list(state.bars_5m)
        if len(bars) < 21:    # need enough for the slowest non-200 indicator
            return

        df = pd.DataFrame(bars)

        ema_8  = di.calculate_ema(df, period=8).iloc[-1]
        sma_21 = di.calculate_sma(df, period=21).iloc[-1]
        atr    = di.calculate_atr(df, period=14).iloc[-1]

        psar_df = di.calculate_psar(df, af0=PSAR_AF0, max_af=PSAR_MAX_AF)
        long_col  = next(c for c in psar_df.columns if c.startswith("PSARl"))
        short_col = next(c for c in psar_df.columns if c.startswith("PSARs"))
        last_psar = psar_df.iloc[-1]
        psar = last_psar[long_col] if not math.isnan(last_psar[long_col]) else last_psar[short_col]

        st_df = di.calculate_supertrend(df, period=ST_PERIOD, multiplier=ST_MULTIPLIER)
        st_col = next(
            c for c in st_df.columns
            if c.startswith("SUPERT_") and not c.startswith(("SUPERTd", "SUPERTl", "SUPERTs"))
        )
        st = st_df[st_col].iloc[-1]

        # HTF — sma(21) on 30-min closed bars
        sgl = float("nan")
        if len(state.bars_30m) >= 21:
            htf_df = pd.DataFrame(list(state.bars_30m))
            sgl = di.calculate_sma(htf_df, period=21).iloc[-1]

        state.ema_8 = float(ema_8)  if not pd.isna(ema_8)  else float("nan")
        state.sma_21 = float(sma_21) if not pd.isna(sma_21) else float("nan")
        state.atr   = float(atr)    if not pd.isna(atr)    else float("nan")
        state.psar  = float(psar)   if not pd.isna(psar)   else float("nan")
        state.st    = float(st)     if not pd.isna(st)     else float("nan")
        state.sgl   = float(sgl)    if not pd.isna(sgl)    else float("nan")

    # ──────────────────────────────────────────────────────────────────────
    # Discovery + warm-up (one-shot at construction)
    # ──────────────────────────────────────────────────────────────────────
    def _discover_and_warmup(self) -> None:
        all_inst = self._instruments.download()
        for name in SYMBOLS:
            futures = self._instruments.find_mcx_futures(all_inst, name)
            if not futures:
                log.warning("mcx_tick.no_contract", symbol=name)
                continue
            contract = futures[0]
            token = int(contract["instrument_token"])
            state = _SymbolState(
                tradingsymbol=str(contract["tradingsymbol"]),
                instrument_token=token,
            )
            self._warmup_bars(state)
            self._recompute_indicators(state)
            self._states[token] = state
            self._symbols_by_name[name] = state
            log.info(
                "mcx_tick.ready",
                symbol=name,
                tradingsymbol=state.tradingsymbol,
                token=token,
                bars_5m=len(state.bars_5m),
                bars_30m=len(state.bars_30m),
                st=state.st, atr=state.atr,
            )

    def _warmup_bars(self, state: _SymbolState) -> None:
        df_5m = self._historical.fetch_latest(
            self._kite, state.instrument_token, INTERVAL, WARMUP_BARS,
        )
        for _, row in df_5m.iterrows():
            state.bars_5m.append({
                "timestamp": row["timestamp"], "open": float(row["open"]),
                "high": float(row["high"]), "low": float(row["low"]),
                "close": float(row["close"]),
            })

        df_30m = self._historical.fetch_latest(
            self._kite, state.instrument_token, HTF_INTERVAL, HTF_WARMUP_BARS,
        )
        for _, row in df_30m.iterrows():
            state.bars_30m.append({
                "timestamp": row["timestamp"], "open": float(row["open"]),
                "high": float(row["high"]), "low": float(row["low"]),
                "close": float(row["close"]),
            })

    # ──────────────────────────────────────────────────────────────────────
    # Order emission
    # ──────────────────────────────────────────────────────────────────────
    async def _emit_action(self, state: _SymbolState, action: str, ltp: float) -> None:
        # Apply state machine
        if state.position is None:
            if action == "BUY":
                state.position = "bought"
            elif action == "SELL":
                state.position = "sold"
            else:
                return
        elif state.position == "bought" and action == "BUY_EXIT":
            state.position = None
        elif state.position == "sold" and action == "SEL_EXIT":
            state.position = None
        else:
            return  # invalid transition; ignore

        side = OrderSide.BUY if action in ("BUY", "SEL_EXIT") else OrderSide.SELL
        await self.emit_order(
            state.tradingsymbol, side, self.config.quantity, price=ltp,
        )
        log.info(
            "mcx_tick.action",
            symbol=state.tradingsymbol, action=action, side=side.value, ltp=ltp,
        )
        telegram_send(
            f"<b>MCX {state.tradingsymbol}</b> | {action} @ {ltp}\n"
            f"st={state.st:.2f} atr={state.atr:.2f} psar={state.psar:.2f}"
        )

    # ──────────────────────────────────────────────────────────────────────
    def _build_kite(self) -> KiteConnect:
        kite = KiteConnect(api_key=settings.kite_api_key)
        # In production the access_token is loaded from Secrets Manager by
        # the runtime; locally it lives in env. The strategy itself doesn't
        # need to refresh — just use whatever the running process was given.
        import os
        token = os.getenv("KITE_ACCESS_TOKEN", "")
        if token:
            kite.set_access_token(token)
        return kite
