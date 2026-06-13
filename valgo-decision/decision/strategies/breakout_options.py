"""Breakout / Momentum Options Strategy — Kite-driven, 5-min bar execution.

Architecture
────────────
Phase 1  Base Instrument Trend Filter
         Stream 5-min candles for NIFTY / BANKNIFTY.
         Compute SuperTrend(10,3) to derive structural bias: BUY or SELL.

Phase 2  Dynamic Option Chain — 5-Contract Bucket  (1 ITM · 1 ATM · 3 OTM)
         On trend signal, resolve nearest weekly expiry and build bucket:
           CE bucket on BUY  →  ITM1 · ATM · OTM1 · OTM2 · OTM3
           PE bucket on SELL →  ITM1 · ATM · OTM1 · OTM2 · OTM3

Phase 3  Live Option Validation — Entry Triggers (all must hold)
         Condition A: Intraday Vol / OI > 15%          (liquidity gate)
         Condition B: LTP > session VWAP               (price above average)
         Condition C: Vol / OI threshold (same as A, explicit guard)
         Condition D: LTP - prev_close > 3 × ATR(14)  (momentum expansion)

Phase 4  Position Management — Exit Triggers (any one fires)
         Exit 1: Base instrument trend flips direction
         Exit 2: Option LTP > entry_price + 5 × ATR   (volatility profit-take)
         Exit 3: Two consecutive 5-min closes BELOW SuperTrend line
                 AND volume on both bars is above average

Run via decision engine:
    class_name: "breakout_options"
    instruments: ["NIFTY", "BANKNIFTY"]   # base instruments
    quantity: 1
"""
from __future__ import annotations

import asyncio
import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Any

import httpx
import numpy as np
import pandas as pd
import pytz
import talib

from valgo_common.config import settings
from valgo_common.logging import get_logger
from valgo_common.models import OrderSide, Tick
from valgo_common.notifier import telegram_send

from .. import dataframe_indicators as di
from .base import StrategyBase

log = get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")

# ── Strategy parameters ───────────────────────────────────────────────────────

BASE_ST_PERIOD   = 10
BASE_ST_MULT     = 3.0
BASE_ATR_PERIOD  = 14
BASE_RSI_PERIOD  = 14
BASE_BARS        = 250       # rolling bar buffer for base instrument

OPT_ST_PERIOD    = 10
OPT_ST_MULT      = 3.0
OPT_ATR_PERIOD   = 14
OPT_RSI_PERIOD   = 14
OPT_MIN_BARS     = 15        # minimum option bars before indicators stabilise

VOL_OI_THRESHOLD = 0.15      # 15% — Condition A
ATR_MULT_EXIT    = 5.0       # 5×ATR profit/stop
ATR_MULT_ENTRY   = 3.0       # 3×ATR momentum filter (Condition D)
ST_EXIT_BARS     = 2         # consecutive bars below ST triggers exit
MIN_VOLUME       = 1000      # Condition B: minimum volume per 5-min bar

STRIKE_INTERVALS: dict[str, int] = {
    "NIFTY":      50,
    "BANKNIFTY":  100,
    "FINNIFTY":   50,
    "MIDCPNIFTY": 25,
}

# Kite underlying quote keys
_KITE_UNDERLYING: dict[str, str] = {
    "NIFTY":      "NSE:NIFTY 50",
    "BANKNIFTY":  "NSE:NIFTY BANK",
    "FINNIFTY":   "NSE:NIFTY FIN SERVICE",
    "MIDCPNIFTY": "NSE:NIFTY MID SELECT",
}


# ── Bar aggregation helper ────────────────────────────────────────────────────

def _bar_start(ts: datetime, minutes: int = 5) -> datetime:
    if ts.tzinfo is not None:
        ts = ts.astimezone(IST).replace(tzinfo=None)
    return ts.replace(minute=(ts.minute // minutes) * minutes,
                      second=0, microsecond=0)


# ── State containers ──────────────────────────────────────────────────────────

@dataclass
class _BarBuffer:
    """Rolling OHLCV bar buffer + live forming bar."""
    maxlen: int = BASE_BARS + 50
    bars: deque = field(default_factory=lambda: deque())
    live_start: datetime | None = None
    live: dict | None = None

    def __post_init__(self):
        self.bars = deque(maxlen=self.maxlen)

    def update(self, ts: datetime, price: float, volume: int = 0) -> bool:
        """Return True when a bar closes."""
        new_start = _bar_start(ts)
        if self.live_start is None:
            self.live_start = new_start
            self.live = self._new_bar(new_start, price, volume)
            return False
        if new_start > self.live_start:
            self.bars.append(self.live)
            self.live_start = new_start
            self.live = self._new_bar(new_start, price, volume)
            return True
        bar = self.live
        bar["high"]   = max(bar["high"], price)
        bar["low"]    = min(bar["low"],  price)
        bar["close"]  = price
        bar["volume"] = bar["volume"] + volume
        return False

    @staticmethod
    def _new_bar(ts, price, volume):
        return {"timestamp": ts, "open": price, "high": price,
                "low": price, "close": price, "volume": volume}

    def to_df(self, include_live: bool = True) -> pd.DataFrame:
        rows = list(self.bars)
        if include_live and self.live:
            rows = rows + [self.live]
        return pd.DataFrame(rows) if rows else pd.DataFrame()


@dataclass
class _BaseState:
    """Indicators + side for the base instrument (NIFTY / BANKNIFTY)."""
    symbol: str
    buf: _BarBuffer = field(default_factory=_BarBuffer)
    st:    float = math.nan
    st_dir: int  = 0        # 1 = uptrend, -1 = downtrend
    atr:   float = math.nan
    rsi:   float = math.nan
    trend: str | None = None  # "BUY" | "SELL" | None


@dataclass
class _VwapAccum:
    """Session-anchored VWAP accumulator. Resets at 09:15 each day."""
    day:    date | None = None
    cum_pv: float = 0.0
    cum_v:  float = 0.0

    def update(self, price: float, volume: int, ts: datetime) -> float:
        today = ts.date() if hasattr(ts, "date") else date.today()
        if today != self.day:
            self.day   = today
            self.cum_pv = 0.0
            self.cum_v  = 0.0
        self.cum_pv += price * volume
        self.cum_v  += volume
        return self.cum_pv / self.cum_v if self.cum_v > 0 else price


@dataclass
class _OptionState:
    """Per-option state: bars, indicators, live VWAP, OI."""
    symbol:     str
    strike:     float
    option_type: str        # CE | PE
    proximity:  int         # 0=ITM1, 1=ATM, 2=OTM1, 3=OTM2, 4=OTM3
    instrument_token: int | None = None
    buf:        _BarBuffer  = field(default_factory=lambda: _BarBuffer(maxlen=150))
    vwap_acc:   _VwapAccum  = field(default_factory=_VwapAccum)
    vwap:       float       = math.nan
    ltp:        float       = math.nan
    prev_close: float       = math.nan
    volume_day: int         = 0         # cumulative intraday volume
    oi:         int         = 0
    atr:        float       = math.nan
    st:         float       = math.nan
    st_dir:     int         = 0
    below_st_count: int     = 0         # consecutive closes below ST


@dataclass
class _Position:
    """Tracks a single open option position."""
    symbol:       str
    option_type:  str
    entry_price:  float
    entry_time:   datetime
    quantity:     int
    atr_at_entry: float
    direction:    str    # "BUY" | "SELL" (of base instrument)


# ── Main Strategy ─────────────────────────────────────────────────────────────

class BreakoutOptionsStrategy(StrategyBase):
    """Breakout / Momentum Options Strategy.

    Monitors base instruments for directional trend, then dynamically builds
    a 5-contract option bucket (1 ITM + 1 ATM + 3 OTM) and enters on
    confluence of Vol/OI, VWAP, and ATR momentum conditions.
    """

    def __init__(self, config) -> None:
        super().__init__(config)

        # One _BaseState per base instrument (NIFTY, BANKNIFTY …)
        self._base: dict[str, _BaseState] = {
            sym: _BaseState(sym) for sym in config.instruments
        }

        # Active option bucket: symbol → _OptionState (up to 5 per base)
        self._bucket: dict[str, _OptionState] = {}
        self._bucket_direction: str | None = None
        self._bucket_base: str | None = None

        # Current open position (only one at a time)
        self._position: _Position | None = None

        # Lock — guards bucket and position mutations
        self._lock = asyncio.Lock()

        # Kite access for quote + option chain lookups
        self._http = httpx.AsyncClient(timeout=15.0)

        log.info("breakout_options.init",
                 instruments=config.instruments,
                 quantity=config.quantity)

    # ── Required instruments ──────────────────────────────────────────────────

    @property
    def required_instruments(self) -> list[str]:
        """Base instruments. Option instruments subscribed dynamically."""
        return list(self._base.keys())

    # ── Hot path ──────────────────────────────────────────────────────────────

    async def on_tick(self, tick: Tick) -> None:
        sym = tick.tradingsymbol
        ltp = float(tick.last_price)
        vol = int(tick.volume or 0)
        oi  = int(tick.oi   or 0)
        ts  = tick.timestamp

        async with self._lock:
            if sym in self._base:
                await self._on_base_tick(sym, ltp, vol, ts)
            elif sym in self._bucket:
                await self._on_option_tick(sym, ltp, vol, oi, ts)

    # ── Base instrument tick ──────────────────────────────────────────────────

    async def _on_base_tick(self, sym: str, ltp: float,
                            vol: int, ts: datetime) -> None:
        state = self._base[sym]
        bar_closed = state.buf.update(ts, ltp, vol)

        if bar_closed and len(state.buf.bars) >= OPT_MIN_BARS:
            self._recompute_base(state)

        # Check if position needs exit due to trend flip
        if self._position and self._position.direction:
            await self._check_trend_flip_exit(state)

        # Check if we should build a new option bucket
        await self._check_bucket_signal(state, ltp)

    def _recompute_base(self, state: _BaseState) -> None:
        """Recompute ST, ATR, RSI on the base instrument's 5-min bars."""
        df = state.buf.to_df(include_live=True)
        if len(df) < OPT_MIN_BARS:
            return

        atr_arr = di.calculate_atr(df, period=BASE_ATR_PERIOD)
        rsi_arr = di.calculate_rsi(df, period=BASE_RSI_PERIOD)
        st_df   = di.calculate_supertrend(df, period=BASE_ST_PERIOD,
                                           multiplier=BASE_ST_MULT)
        st_col  = next(
            c for c in st_df.columns
            if c.startswith("SUPERT_") and
            not c.startswith(("SUPERTd", "SUPERTl", "SUPERTs"))
        )
        st_dir_col = f"SUPERTd_{BASE_ST_PERIOD}_{float(BASE_ST_MULT)}"

        state.atr    = float(atr_arr.iloc[-1]) if not pd.isna(atr_arr.iloc[-1]) else math.nan
        state.rsi    = float(rsi_arr.iloc[-1]) if not pd.isna(rsi_arr.iloc[-1]) else math.nan
        state.st     = float(st_df[st_col].iloc[-1])
        state.st_dir = int(st_df[st_dir_col].iloc[-1]) if st_dir_col in st_df.columns else 0

        # Derive trend
        ltp = float(df["close"].iloc[-1])
        if state.st_dir == 1 and ltp > state.st:
            state.trend = "BUY"
        elif state.st_dir == -1 and ltp < state.st:
            state.trend = "SELL"
        else:
            state.trend = None

        log.debug("breakout.base_recompute",
                  symbol=state.symbol, trend=state.trend,
                  st=round(state.st, 2), rsi=round(state.rsi, 2),
                  atr=round(state.atr, 2))

    # ── Option tick ───────────────────────────────────────────────────────────

    async def _on_option_tick(self, sym: str, ltp: float,
                              vol: int, oi: int, ts: datetime) -> None:
        opt = self._bucket.get(sym)
        if not opt:
            return

        opt.ltp       = ltp
        opt.volume_day += vol
        if oi > 0:
            opt.oi = oi

        # VWAP — update on every tick
        opt.vwap = opt.vwap_acc.update(ltp, max(vol, 1), ts)

        # Bar update
        bar_closed = opt.buf.update(ts, ltp, vol)
        if bar_closed and len(opt.buf.bars) >= OPT_MIN_BARS:
            self._recompute_option(opt)

        # Check entry if no position open
        if self._position is None:
            await self._check_entry(opt)

        # Check exit conditions on option
        if self._position and self._position.symbol == sym:
            await self._check_option_exit(opt)

    def _recompute_option(self, opt: _OptionState) -> None:
        """Recompute ATR and SuperTrend on the option's own 5-min bars."""
        df = opt.buf.to_df(include_live=True)
        if len(df) < OPT_MIN_BARS:
            return

        atr_arr = di.calculate_atr(df, period=OPT_ATR_PERIOD)
        st_df   = di.calculate_supertrend(df, period=OPT_ST_PERIOD,
                                           multiplier=OPT_ST_MULT)
        st_col     = next(
            c for c in st_df.columns
            if c.startswith("SUPERT_") and
            not c.startswith(("SUPERTd", "SUPERTl", "SUPERTs"))
        )
        st_dir_col = f"SUPERTd_{OPT_ST_PERIOD}_{float(OPT_ST_MULT)}"

        opt.atr    = float(atr_arr.iloc[-1]) if not pd.isna(atr_arr.iloc[-1]) else math.nan
        opt.st     = float(st_df[st_col].iloc[-1])
        opt.st_dir = int(st_df[st_dir_col].iloc[-1]) if st_dir_col in st_df.columns else 0

        # Track consecutive closes below ST for exit logic
        close = float(df["close"].iloc[-1])
        if close < opt.st:
            opt.below_st_count += 1
        else:
            opt.below_st_count = 0

        # Save prev_close (second-to-last closed bar)
        closed = list(opt.buf.bars)
        if len(closed) >= 1:
            opt.prev_close = float(closed[-1]["close"])

    # ── Entry logic ───────────────────────────────────────────────────────────

    async def _check_entry(self, opt: _OptionState) -> None:
        """Check all entry conditions for one option contract."""
        if self._position is not None:
            return
        if math.isnan(opt.ltp) or opt.ltp <= 0:
            return
        if opt.oi <= 0:
            return

        # Condition A: Vol / OI > 15%
        vol_oi = opt.volume_day / opt.oi
        cond_a = vol_oi > VOL_OI_THRESHOLD

        # Condition B: minimum absolute volume
        recent_vol = (opt.buf.live or {}).get("volume", 0)
        cond_b = recent_vol >= MIN_VOLUME

        # Condition C: LTP > VWAP
        cond_c = (not math.isnan(opt.vwap)) and (opt.ltp > opt.vwap)

        # Condition D (optional): LTP - prev_close > 3 × ATR
        cond_d = True
        if not math.isnan(opt.atr) and not math.isnan(opt.prev_close) and opt.atr > 0:
            cond_d = (opt.ltp - opt.prev_close) > ATR_MULT_ENTRY * opt.atr

        log.debug("breakout.entry_check",
                  symbol=opt.symbol, ltp=opt.ltp, vwap=round(opt.vwap, 2),
                  vol_oi=round(vol_oi, 3),
                  A=cond_a, B=cond_b, C=cond_c, D=cond_d)

        if cond_a and cond_b and cond_c and cond_d:
            await self._enter_position(opt)

    async def _enter_position(self, opt: _OptionState) -> None:
        """Place market order and record position."""
        qty = self.config.quantity
        base_sym = self._bucket_base or "NIFTY"
        base_state = self._base.get(base_sym)
        atr_at_entry = base_state.atr if base_state else math.nan

        try:
            await self.emit_order(opt.symbol, OrderSide.BUY, qty, price=opt.ltp)
        except Exception as e:
            log.error("breakout.entry_order_failed", symbol=opt.symbol, error=str(e))
            return

        self._position = _Position(
            symbol=opt.symbol,
            option_type=opt.option_type,
            entry_price=opt.ltp,
            entry_time=datetime.now(IST),
            quantity=qty,
            atr_at_entry=atr_at_entry,
            direction=self._bucket_direction or "BUY",
        )

        log.info("breakout.entered",
                 symbol=opt.symbol, ltp=opt.ltp,
                 vwap=round(opt.vwap, 2), vol_oi=round(opt.volume_day / opt.oi, 3))
        telegram_send(
            f"<b>BreakoutOptions ENTRY</b> | {opt.symbol}\n"
            f"LTP={opt.ltp:.2f}  VWAP={opt.vwap:.2f}  "
            f"Vol/OI={opt.volume_day/opt.oi:.1%}\n"
            f"Proximity: {['ITM1','ATM','OTM1','OTM2','OTM3'][opt.proximity]}"
        )

    # ── Exit logic ────────────────────────────────────────────────────────────

    async def _check_trend_flip_exit(self, base_state: _BaseState) -> None:
        """Exit 1: Base instrument trend flipped direction."""
        if not self._position:
            return
        pos_dir = self._position.direction
        cur_trend = base_state.trend
        if cur_trend and cur_trend != pos_dir:
            log.info("breakout.exit_trend_flip",
                     position=pos_dir, new_trend=cur_trend)
            await self._exit_position("TREND_FLIP")

    async def _check_option_exit(self, opt: _OptionState) -> None:
        """Exit 2 (5×ATR) and Exit 3 (2 bars below ST + volume)."""
        if not self._position:
            return

        # Exit 2: Volatility stop — option moved > 5×ATR from entry
        if not math.isnan(opt.atr) and opt.atr > 0:
            entry = self._position.entry_price
            if abs(opt.ltp - entry) > ATR_MULT_EXIT * opt.atr:
                log.info("breakout.exit_atr_extension",
                         ltp=opt.ltp, entry=entry, atr=opt.atr)
                await self._exit_position("ATR_EXTENSION")
                return

        # Exit 3: Two consecutive closes below Supertrend + volume confirmation
        if opt.below_st_count >= ST_EXIT_BARS:
            recent_vol = (opt.buf.live or {}).get("volume", 0)
            avg_vol = self._avg_volume(opt)
            if recent_vol >= avg_vol * 0.8:   # volume at least 80% of average
                log.info("breakout.exit_supertrend",
                         below_count=opt.below_st_count, ltp=opt.ltp, st=opt.st)
                await self._exit_position("SUPERTREND_BREAK")

    async def _exit_position(self, reason: str) -> None:
        if not self._position:
            return
        pos = self._position
        self._position = None

        opt = self._bucket.get(pos.symbol)
        ltp = opt.ltp if opt else 0.0

        try:
            await self.emit_order(pos.symbol, OrderSide.SELL,
                                  pos.quantity, price=ltp)
        except Exception as e:
            log.error("breakout.exit_order_failed", symbol=pos.symbol, error=str(e))

        pnl_pts = ltp - pos.entry_price
        pnl_rs  = pnl_pts * pos.quantity * 75   # approximate — actual lot size varies

        log.info("breakout.exited",
                 symbol=pos.symbol, reason=reason,
                 entry=pos.entry_price, exit=ltp,
                 pnl_pts=round(pnl_pts, 2))
        telegram_send(
            f"<b>BreakoutOptions EXIT</b> | {pos.symbol}\n"
            f"Reason: {reason}\n"
            f"Entry={pos.entry_price:.2f}  Exit={ltp:.2f}  "
            f"P&L={pnl_pts:+.2f} pts"
        )

        # Clear bucket after exit
        self._bucket.clear()
        self._bucket_direction = None
        self._bucket_base = None

    # ── Bucket management ─────────────────────────────────────────────────────

    async def _check_bucket_signal(self, base_state: _BaseState,
                                   spot: float) -> None:
        """Build a new option bucket when trend signal fires and no position open."""
        if self._position:
            return
        if not base_state.trend:
            return
        if self._bucket and self._bucket_direction == base_state.trend:
            return   # bucket already built for this direction

        log.info("breakout.trend_signal",
                 base=base_state.symbol, direction=base_state.trend, spot=spot)
        await self._build_bucket(base_state.symbol, base_state.trend, spot)

    async def _build_bucket(self, base_sym: str,
                            direction: str, spot: float) -> None:
        """
        Build 5-contract bucket: 1 ITM + 1 ATM + 3 OTM.
        Fetch NFO instruments from Kite, resolve tokens, subscribe.
        """
        interval = STRIKE_INTERVALS.get(base_sym, 50)
        atm = round(spot / interval) * interval
        otype = "CE" if direction == "BUY" else "PE"

        if otype == "CE":
            strikes_prox = [
                (atm - interval, 0),        # ITM1
                (atm,            1),        # ATM
                (atm + interval, 2),        # OTM1
                (atm + 2*interval, 3),      # OTM2
                (atm + 3*interval, 4),      # OTM3
            ]
        else:
            strikes_prox = [
                (atm + interval, 0),        # ITM1
                (atm,            1),        # ATM
                (atm - interval, 2),        # OTM1
                (atm - 2*interval, 3),      # OTM2
                (atm - 3*interval, 4),      # OTM3
            ]

        # Resolve instruments from Kite NFO list
        instruments = await self._resolve_instruments(base_sym, otype, strikes_prox)

        self._bucket.clear()
        for opt_state in instruments:
            self._bucket[opt_state.symbol] = opt_state

        self._bucket_direction = direction
        self._bucket_base      = base_sym

        log.info("breakout.bucket_built",
                 base=base_sym, direction=direction, atm=atm,
                 symbols=list(self._bucket.keys()))
        telegram_send(
            f"<b>BreakoutOptions BUCKET</b> | {base_sym} {direction}\n"
            f"Spot={spot:.0f}  ATM={atm}\n"
            f"Contracts: {', '.join(self._bucket.keys())}"
        )

    async def _resolve_instruments(
        self,
        underlying: str,
        otype: str,
        strikes_prox: list[tuple[float, int]],
    ) -> list[_OptionState]:
        """Resolve Kite NFO instrument tokens for each strike."""
        try:
            import io
            headers = {
                "Authorization": (
                    f"token {settings.kite_api_key}:{settings.kite_access_token}"
                ),
                "X-Kite-Version": "3",
            }
            resp = await self._http.get(
                "https://api.kite.trade/instruments/NFO",
                headers=headers,
            )
            resp.raise_for_status()
            df = pd.read_csv(io.StringIO(resp.text))
            df["expiry"] = pd.to_datetime(df["expiry"], errors="coerce").dt.date
            df["strike"] = pd.to_numeric(df["strike"], errors="coerce")

            today  = datetime.now(IST).date()
            expiry = df[
                (df["name"] == underlying) &
                (df["instrument_type"] == otype) &
                (df["expiry"] >= today)
            ]["expiry"].min()

            results: list[_OptionState] = []
            expiry_df = df[
                (df["name"] == underlying) &
                (df["instrument_type"] == otype) &
                (df["expiry"] == expiry)
            ]

            for strike, prox in strikes_prox:
                row = expiry_df[expiry_df["strike"] == float(strike)]
                if row.empty:
                    log.warning("breakout.strike_not_found",
                                underlying=underlying, strike=strike)
                    continue
                r = row.iloc[0]
                results.append(_OptionState(
                    symbol=str(r["tradingsymbol"]),
                    strike=float(strike),
                    option_type=otype,
                    proximity=prox,
                    instrument_token=int(r["instrument_token"]),
                ))

            return results

        except Exception as e:
            log.error("breakout.resolve_instruments_failed", error=str(e))
            return []

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _avg_volume(opt: _OptionState) -> float:
        """Average volume over closed bars."""
        bars = list(opt.buf.bars)
        if not bars:
            return MIN_VOLUME
        vols = [b.get("volume", 0) for b in bars[-10:]]
        return sum(vols) / len(vols) if vols else MIN_VOLUME

    async def close(self) -> None:
        await self._http.aclose()
        await super().close()
