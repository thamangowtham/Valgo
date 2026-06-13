"""SuperTrend + PSAR Confluence — tick-driven, 251-row DataFrame (250 closed + 1 live).

On startup:
    preload_history() fetches the last 250 closed 5-min candles from Kite REST API
    so the strategy is ready to trade from the first live tick.

On every tick:
    1. Update the live (forming) candle OHLC
    2. Recompute all indicators on a 251-row DataFrame:
           rows 0-249  → last 250 closed bars (from state.bars)
           row  250    → live bar (state.live)  ← always the last row
    3. Run entry/exit decision against fresh indicator values

Entry (side = None)
───────────────────
BUY if all four hold + RSI > 50:
    (1) ltp >= st + atr * 0.2          above supertrend with 0.2-ATR clearance
    (2) ltp -  st < atr                not overextended above supertrend
    (3) ltp >  psar                    above Parabolic SAR
    (4) ltp >  sgl  OR  sgl - ltp > 5 * atr

SELL if all four hold + RSI < 50 (exact mirror):
    (1) ltp <= st - atr * 0.2
    (2) st  -  ltp < atr
    (3) ltp <  psar
    (4) ltp <  sgl  OR  ltp - sgl > 5 * atr

Exit (side = "B") — any one triggers:
    ltp < st - atr
    ltp < st  AND  prev_mid < st
    ltp < psar
    ltp < sgl  OR  sgl - ltp > 5 * atr

Exit (side = "S") — any one triggers (mirror):
    ltp > st + atr
    ltp > st  AND  prev_mid > st
    ltp > psar
    ltp > sgl  OR  ltp - sgl > 5 * atr
"""
from __future__ import annotations

import math
from collections import deque
from datetime import datetime, timedelta
from typing import Any

import httpx
import pandas as pd
import pytz

from valgo_common.config import settings
from valgo_common.logging import get_logger
from valgo_common.models import OrderSide, Tick
from valgo_common.notifier import telegram_send

from .. import dataframe_indicators as di
from ..option_selector import OptionSelector
from .base import StrategyBase

log = get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")

BARS        = 250          # closed bars kept in rolling buffer
ST_PERIOD   = 10
ST_MULT     = 3.0
SGL_PERIOD  = 21
PSAR_AF0    = 0.02
PSAR_MAX_AF = 0.2
ATR_PERIOD  = 14
RSI_PERIOD  = 14
RSI_BUY     = 50
RSI_SELL    = 50
MIN_BARS    = 50           # minimum rows (closed + live) before emitting signals

# Kite instrument tokens for NSE indices — never change
_KITE_TOKEN: dict[str, int] = {
    "NIFTY":      256265,
    "BANKNIFTY":  260105,
    "FINNIFTY":   257801,
    "MIDCPNIFTY": 288009,
    "SENSEX":     265,
}


def _bar_start(ts: datetime, minutes: int = 5) -> datetime:
    if ts.tzinfo is not None:
        ts = ts.astimezone(IST).replace(tzinfo=None)
    return ts.replace(minute=(ts.minute // minutes) * minutes, second=0, microsecond=0)


# ── Pure condition functions (module-level so tests can import them directly) ──

def check_buy_entry(ltp: float, st: float, sgl: float, psar: float, atr: float) -> bool:
    c1 = ltp >= st + atr * 0.2
    c2 = (ltp - st) < atr
    c3 = ltp > psar
    c4 = ltp > sgl or (sgl - ltp) > 5 * atr
    return c1 and c2 and c3 and c4


def check_sell_entry(ltp: float, st: float, sgl: float, psar: float, atr: float) -> bool:
    c1 = ltp <= st - atr * 0.2
    c2 = (st - ltp) < atr
    c3 = ltp < psar
    c4 = ltp < sgl or (ltp - sgl) > 5 * atr
    return c1 and c2 and c3 and c4


def check_buy_exit(ltp: float, st: float, sgl: float, psar: float, atr: float, prev_mid: float) -> bool:
    e1 = ltp < st - atr
    e2 = ltp < st and prev_mid < st
    e3 = ltp < psar
    e4 = ltp < sgl or (sgl - ltp) > 5 * atr
    return e1 or e2 or e3 or e4


def check_sell_exit(ltp: float, st: float, sgl: float, psar: float, atr: float, prev_mid: float) -> bool:
    e1 = ltp > st + atr
    e2 = ltp > st and prev_mid > st
    e3 = ltp > psar
    e4 = ltp > sgl or (ltp - sgl) > 5 * atr
    return e1 or e2 or e3 or e4


# ── Per-symbol rolling state ──────────────────────────────────────────────────

class _SymbolState:
    def __init__(self, tradingsymbol: str) -> None:
        self.tradingsymbol = tradingsymbol

        # Closed bars (up to BARS + 50 kept; we slice to last BARS on each recompute)
        self.bars: deque[dict[str, Any]] = deque(maxlen=BARS + 50)

        # Currently forming candle
        self.live_start: datetime | None = None
        self.live: dict[str, Any] | None = None

        # Cached indicator values — refreshed on every tick
        self.st:       float = float("nan")
        self.sgl:      float = float("nan")
        self.psar:     float = float("nan")
        self.atr:      float = float("nan")
        self.rsi:      float = float("nan")
        self.prev_mid: float = float("nan")

        # Trade direction: None | "B" | "S"
        self.side: str | None = None


# ── Strategy ──────────────────────────────────────────────────────────────────

class STPSARConfluenceStrategy(StrategyBase):
    """SuperTrend + PSAR Confluence.

    DataFrame always has 251 rows: 250 closed bars + 1 live bar.
    Indicators recomputed on every tick using the live bar's current close.
    Historical bars pre-loaded from Kite REST API on startup.
    """

    def __init__(self, config) -> None:
        super().__init__(config)
        self._states: dict[str, _SymbolState] = {
            sym: _SymbolState(sym) for sym in config.instruments
        }
        self._option_selector = OptionSelector()

    @property
    def required_instruments(self) -> list[str]:
        return list(self._states.keys())

    # ── Historical preload ────────────────────────────────────────────────────

    async def preload_history(self) -> None:
        """Fetch last 250 closed 5-min candles from Kite API for every symbol."""
        if not settings.kite_api_key or not settings.kite_access_token:
            log.warning("st_psar.preload_skipped", reason="no Kite credentials")
            return

        now_ist = datetime.now(IST)
        # Fetch 7 calendar days to cover ~4 trading days (250 bars × 5 min = ~20.8 hrs)
        from_dt = (now_ist - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        to_dt   = now_ist.strftime("%Y-%m-%d %H:%M:%S")
        headers = {
            "Authorization": f"token {settings.kite_api_key}:{settings.kite_access_token}",
            "X-Kite-Version": "3",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            for sym, state in self._states.items():
                token = _KITE_TOKEN.get(sym)
                if token is None:
                    log.warning("st_psar.preload_no_token", symbol=sym)
                    continue
                url = f"https://api.kite.trade/instruments/historical/{token}/5minute"
                try:
                    resp = await client.get(url, headers=headers,
                                            params={"from": from_dt, "to": to_dt,
                                                    "continuous": 0, "oi": 0})
                    resp.raise_for_status()
                    candles = resp.json()["data"]["candles"]
                    # Drop the last candle — it may be the currently forming bar
                    if candles:
                        candles = candles[:-1]
                    # Keep only last BARS closed candles
                    for c in candles[-BARS:]:
                        # Kite format: [timestamp, open, high, low, close, volume, oi]
                        ts = datetime.fromisoformat(c[0]) if isinstance(c[0], str) else c[0]
                        state.bars.append({
                            "timestamp": ts,
                            "open":  float(c[1]),
                            "high":  float(c[2]),
                            "low":   float(c[3]),
                            "close": float(c[4]),
                        })
                    log.info("st_psar.preloaded", symbol=sym, bars=len(state.bars))
                except Exception as e:
                    log.error("st_psar.preload_failed", symbol=sym, error=str(e))

    # ── Hot path — called on every WebSocket tick ─────────────────────────────

    async def on_tick(self, tick: Tick) -> None:
        state = self._states.get(tick.tradingsymbol)
        if state is None:
            return

        ltp = float(tick.last_price)

        # 1. Update live (forming) candle with this tick
        self._update_bar(state, tick.timestamp, ltp)

        # 2. Recompute indicators on 250 closed + 1 live = 251 rows
        self._recompute(state)

        # 3. Check entry/exit conditions
        action = self._decide(state, ltp)
        if action:
            await self._emit(state, action, ltp)

    # ── Bar aggregation ───────────────────────────────────────────────────────

    def _update_bar(self, state: _SymbolState, ts: datetime, price: float) -> None:
        """Update the live forming candle; push it to state.bars on bar close."""
        new_start = _bar_start(ts)

        if state.live_start is None:
            # Very first tick — open the first live bar
            state.live_start = new_start
            state.live = {"timestamp": new_start,
                          "open": price, "high": price, "low": price, "close": price}
            return

        if new_start > state.live_start:
            # Bar boundary crossed — seal the closed bar and open a new live bar
            state.bars.append(state.live)
            state.live_start = new_start
            state.live = {"timestamp": new_start,
                          "open": price, "high": price, "low": price, "close": price}
            return

        # Same bar — update OHLC
        bar = state.live
        bar["high"]  = max(bar["high"],  price)
        bar["low"]   = min(bar["low"],   price)
        bar["close"] = price

    # ── Indicator recompute on every tick ─────────────────────────────────────

    def _recompute(self, state: _SymbolState) -> None:
        """Build 251-row DataFrame (250 closed + 1 live) and refresh all indicators."""
        if state.live is None:
            return

        # 250 closed bars (trim to BARS) + live bar as row 251
        closed = list(state.bars)[-BARS:]
        rows = closed + [state.live]

        if len(rows) < MIN_BARS:
            return

        df = pd.DataFrame(rows)

        atr = di.calculate_atr(df, period=ATR_PERIOD).iloc[-1]
        sgl = di.calculate_ema(df, period=SGL_PERIOD).iloc[-1]
        rsi = di.calculate_rsi(df, period=RSI_PERIOD).iloc[-1]

        psar_df   = di.calculate_psar(df, af0=PSAR_AF0, max_af=PSAR_MAX_AF)
        long_col  = next(c for c in psar_df.columns if c.startswith("PSARl"))
        short_col = next(c for c in psar_df.columns if c.startswith("PSARs"))
        last_psar = psar_df.iloc[-1]
        psar = (last_psar[long_col]
                if not math.isnan(last_psar[long_col])
                else last_psar[short_col])

        st_df  = di.calculate_supertrend(df, period=ST_PERIOD, multiplier=ST_MULT)
        st_col = next(
            c for c in st_df.columns
            if c.startswith("SUPERT_") and not c.startswith(("SUPERTd", "SUPERTl", "SUPERTs"))
        )
        st = st_df[st_col].iloc[-1]

        # prev_mid = midpoint of the last closed bar (row before live)
        prev_mid = float("nan")
        if len(closed) >= 1:
            prev = closed[-1]
            prev_mid = (prev["high"] + prev["low"]) / 2.0

        state.atr      = float(atr)      if not pd.isna(atr)  else float("nan")
        state.sgl      = float(sgl)      if not pd.isna(sgl)  else float("nan")
        state.psar     = float(psar)     if not pd.isna(psar) else float("nan")
        state.st       = float(st)       if not pd.isna(st)   else float("nan")
        state.rsi      = float(rsi)      if not pd.isna(rsi)  else float("nan")
        state.prev_mid = float(prev_mid) if not math.isnan(prev_mid) else float("nan")

    # ── Decision logic ────────────────────────────────────────────────────────

    def _decide(self, state: _SymbolState, ltp: float) -> str | None:
        if any(math.isnan(v) for v in (state.st, state.sgl, state.psar, state.atr, state.rsi)):
            return None

        st, sgl, psar, atr, rsi = state.st, state.sgl, state.psar, state.atr, state.rsi
        prev_mid = state.prev_mid

        if state.side is None:
            if check_buy_entry(ltp, st, sgl, psar, atr) and rsi > RSI_BUY:
                return "BUY"
            if check_sell_entry(ltp, st, sgl, psar, atr) and rsi < RSI_SELL:
                return "SELL"

        elif state.side == "B":
            pm = prev_mid if not math.isnan(prev_mid) else st - 1
            if check_buy_exit(ltp, st, sgl, psar, atr, pm):
                return "BUY_EXIT"

        elif state.side == "S":
            pm = prev_mid if not math.isnan(prev_mid) else st + 1
            if check_sell_exit(ltp, st, sgl, psar, atr, pm):
                return "SEL_EXIT"

        return None

    # ── Order emission ────────────────────────────────────────────────────────

    async def _emit(self, state: _SymbolState, action: str, ltp: float) -> None:
        # ── Update trade side state ───────────────────────────────────────────
        if state.side is None:
            if action == "BUY":
                state.side = "B"
            elif action == "SELL":
                state.side = "S"
            else:
                return
        elif state.side == "B" and action == "BUY_EXIT":
            state.side = None
        elif state.side == "S" and action == "SEL_EXIT":
            state.side = None
        else:
            return

        order_side = OrderSide.BUY if action in ("BUY", "SEL_EXIT") else OrderSide.SELL

        # ── Entry: find best option strike ────────────────────────────────────
        # For exits we trade the same option that was entered — tracked separately.
        # For entries (BUY / SELL) we run the option selector.
        if action in ("BUY", "SELL"):
            option = await self._option_selector.find_best_strike(
                symbol=state.tradingsymbol,
                action=action,
                spot_ltp=ltp,
            )
            if option is None:
                log.warning(
                    "st_psar.no_qualifying_option",
                    symbol=state.tradingsymbol, action=action, spot_ltp=ltp,
                )
                # Revert side — we did not actually enter a trade
                state.side = None
                return

            trade_symbol = option.tradingsymbol
            trade_price  = option.ltp

            log.info(
                "st_psar.option_selected",
                spot=state.tradingsymbol, action=action,
                option=option.tradingsymbol, strike=option.strike,
                option_ltp=option.ltp, vol_oi=round(option.volume_oi_ratio, 3),
            )
            telegram_send(
                f"<b>STPSARConfluence {state.tradingsymbol}</b> | {action} @ spot {ltp:.2f}\n"
                f"Option: <b>{option.tradingsymbol}</b> LTP={option.ltp:.2f}  "
                f"V/OI={option.volume_oi_ratio:.1%}\n"
                f"st={state.st:.2f}  atr={state.atr:.2f}  "
                f"psar={state.psar:.2f}  rsi={state.rsi:.1f}"
            )
        else:
            # EXIT order — trade spot (option exit handled by execution layer)
            trade_symbol = state.tradingsymbol
            trade_price  = ltp
            telegram_send(
                f"<b>STPSARConfluence {state.tradingsymbol}</b> | {action} @ {ltp:.2f}\n"
                f"st={state.st:.2f}  atr={state.atr:.2f}  "
                f"psar={state.psar:.2f}  rsi={state.rsi:.1f}"
            )

        await self.emit_order(trade_symbol, order_side, self.config.quantity, price=trade_price)

        log.info(
            "st_psar.action",
            symbol=state.tradingsymbol, action=action,
            trade_symbol=trade_symbol, side=order_side.value,
            spot_ltp=ltp, trade_price=trade_price,
            st=state.st, atr=state.atr, psar=state.psar, sgl=state.sgl, rsi=state.rsi,
        )
