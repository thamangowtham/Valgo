"""MCX multi-commodity strategy — CRUDEOIL, SILVER, GOLD on Zerodha MCX FUT.

Ported from the sharemarket reference. Bar-based, not tick-driven: the engine
runs a periodic polling loop that pulls fresh 1-min and 5-min candles via
Kite's REST historical_data API, builds an in-progress 5-min candle from
buffered 1-min bars, and evaluates the signal on every 1-min boundary.

Timeframes:
    5-min  — EMA8, SMA21, SMA200, PSAR, SuperTrend  (entry signals)
    30-min — SMA21 ('sgl')                          (higher-TF trend filter)

Signals:
    BUY      — c1..c4 all true (c5 disabled in reference)
    SELL     — s1..s4 all true (s5 disabled in reference)
    BUY_EXIT — ltp < st - atr   (only when position == bought)
    SEL_EXIT — ltp > st + atr   (only when position == sold)

Position state machine:
    FLAT   + BUY      -> bought
    FLAT   + SELL     -> sold
    bought + BUY_EXIT -> FLAT (exit signal emits SELL order to close)
    sold   + SEL_EXIT -> FLAT (exit signal emits BUY order to close)
"""
from __future__ import annotations

import asyncio
import math
from datetime import datetime
from typing import Any

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


# ── Strategy config (ported from reference) ──────────────────────────────────
SYMBOLS = ["CRUDEOIL", "SILVER", "GOLD"]
CANDLES_NEEDED = 250          # SMA200 needs ~200 + warmup
INTERVAL = "5minute"
HTF_INTERVAL = "30minute"
HTF_CANDLES = 60

ST_PERIOD = 10
ST_MULTIPLIER = 2.0
PSAR_AF0 = 0.02
PSAR_MAX_AF = 0.2


def _nan(v: Any) -> bool:
    return isinstance(v, float) and math.isnan(v)


def _candle_start_time() -> datetime:
    """Start of the current 5-min period in IST, tz-naive."""
    now = datetime.now(IST)
    return now.replace(minute=(now.minute // 5) * 5, second=0, microsecond=0, tzinfo=None)


def _generate_signal(row: pd.Series) -> str:
    ltp, st, atr = row["close"], row["st"], row["atr"]
    psar, e8, e21, sgl = row["psar"], row["ema_8"], row["sma_21"], row["sgl"]

    if any(_nan(v) for v in [st, atr, psar, e8, e21]):
        return "HOLD"

    ok = not _nan(sgl)

    c1 = ltp > st;   c2 = (ltp - st) < atr
    c3 = (ok and ltp > sgl) or (ok and (sgl - ltp) > atr * 5)
    c4 = ltp > psar
    s1 = ltp < st;   s2 = (st - ltp) < atr
    s3 = (ok and ltp < sgl) or (ok and (ltp - sgl) > atr * 5)
    s4 = ltp < psar

    if c1 and c2 and c3 and c4:
        return "BUY"
    if s1 and s2 and s3 and s4:
        return "SELL"
    if ltp < (st - atr):
        return "BUY_EXIT"
    if ltp > (st + atr):
        return "SEL_EXIT"
    return "HOLD"


def _add_indicators(df: pd.DataFrame, htf_df: pd.DataFrame) -> pd.DataFrame:
    """Compute all indicators on the 5-min DataFrame and merge HTF (30-min) sgl."""
    df["ema_8"]   = di.calculate_ema(df, period=8).values
    df["sma_21"]  = di.calculate_sma(df, period=21).values
    df["sma_200"] = di.calculate_sma(df, period=200).values

    htf_df = htf_df.copy()
    htf_df["sgl"] = di.calculate_sma(htf_df, period=21).values
    htf_df = htf_df[["timestamp", "sgl"]].dropna()
    df = pd.merge_asof(
        df.sort_values("timestamp"),
        htf_df.sort_values("timestamp"),
        on="timestamp", direction="backward",
    ).reset_index(drop=True)

    psar_df = di.calculate_psar(df, af0=PSAR_AF0, max_af=PSAR_MAX_AF)
    long_col  = next(c for c in psar_df.columns if c.startswith("PSARl"))
    short_col = next(c for c in psar_df.columns if c.startswith("PSARs"))
    df["psar"] = psar_df.apply(
        lambda r: r[long_col] if not math.isnan(r[long_col]) else r[short_col], axis=1
    ).values

    st_df = di.calculate_supertrend(df, period=ST_PERIOD, multiplier=ST_MULTIPLIER)
    val_col = next(
        c for c in st_df.columns
        if c.startswith("SUPERT_") and not c.startswith(("SUPERTd", "SUPERTl", "SUPERTs"))
    )
    df["st"]  = st_df[val_col].values
    df["atr"] = di.calculate_atr(df, period=14).values
    df["signal"] = df.apply(_generate_signal, axis=1)
    return df


class MCXMultiCommodityStrategy(StrategyBase):
    """MCX multi-commodity (CRUDEOIL/SILVER/GOLD) bar strategy.

    Diverges from tick-driven strategies: uses periodic REST polling rather
    than the Redis tick stream, because MCX FUT tick coverage is patchier
    than NFO and the reference implementation was bar-based by design.
    """

    POLL_SECONDS = 60

    def __init__(self, config, kite: KiteConnect | None = None) -> None:
        super().__init__(config)
        # Lazy import: ingestor.historical lives in the sibling repo so this
        # works only when run from the platform docker-compose where both
        # services share the install.
        from ingestor import historical, instruments  # type: ignore

        self._historical = historical
        self._instruments = instruments
        self._kite = kite or self._build_kite()

        # Per-symbol state: token, base_df, buf (1-min candles in current 5m), position
        self._states: dict[str, dict] = {}
        self._stop_event = asyncio.Event()

    # ──────────────────────────────────────────────────────────────────────
    # Lifecycle hooks called by the decision engine
    # ──────────────────────────────────────────────────────────────────────
    async def on_tick(self, tick: Tick) -> None:
        """No-op: this strategy is bar-driven, not tick-driven."""
        return

    async def run(self) -> None:
        """Discover contracts, warm up, then poll every minute until stopped."""
        await self._discover_contracts()
        if not self._states:
            log.warning("mcx_multi.no_symbols_loaded")
            return

        await self._warmup_base_candles()
        await self._evaluate_all()

        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._wait_seconds())
            except asyncio.TimeoutError:
                pass
            if self._stop_event.is_set():
                break
            try:
                await self._evaluate_all()
            except Exception as e:
                log.error("mcx_multi.eval_failed", error=str(e))

    async def stop(self) -> None:
        self._stop_event.set()

    # ──────────────────────────────────────────────────────────────────────
    # Setup
    # ──────────────────────────────────────────────────────────────────────
    def _build_kite(self) -> KiteConnect:
        kite = KiteConnect(api_key=settings.kite_api_key)
        token = settings.kite_api_secret  # Placeholder — production loads from Secrets Manager
        if token:
            kite.set_access_token(token)
        return kite

    async def _discover_contracts(self) -> None:
        all_inst = await asyncio.to_thread(self._instruments.download)
        for sym in SYMBOLS:
            futures = self._instruments.find_mcx_futures(all_inst, sym)
            if not futures:
                log.warning("mcx_multi.no_contract", symbol=sym)
                continue
            contract = futures[0]   # nearest active
            self._states[sym] = {
                "token":         int(contract["instrument_token"]),
                "tradingsymbol": contract["tradingsymbol"],
                "expiry":        contract["expiry"],
                "base_df":       None,
                "buf":           [],
                "position":      None,
            }
            log.info(
                "mcx_multi.contract_loaded",
                symbol=sym,
                tradingsymbol=contract["tradingsymbol"],
                expiry=contract["expiry"],
            )

    async def _warmup_base_candles(self) -> None:
        for sym, state in self._states.items():
            df = await asyncio.to_thread(
                self._historical.fetch_latest,
                self._kite, state["token"], INTERVAL, CANDLES_NEEDED,
            )
            state["base_df"] = df
            log.info("mcx_multi.warmup", symbol=sym, candles=len(df))

    # ──────────────────────────────────────────────────────────────────────
    # Polling cycle
    # ──────────────────────────────────────────────────────────────────────
    def _wait_seconds(self) -> int:
        now = datetime.now(IST)
        return (60 - now.second) or 60

    def _is_5min_boundary(self) -> bool:
        now = datetime.now(IST)
        return now.minute % 5 == 0 and now.second < 10

    async def _evaluate_all(self) -> None:
        is_5min = self._is_5min_boundary()
        for sym, state in self._states.items():
            try:
                if is_5min:
                    state["base_df"] = await asyncio.to_thread(
                        self._historical.fetch_latest,
                        self._kite, state["token"], INTERVAL, CANDLES_NEEDED,
                    )

                ltp = await self._refresh_buf(sym)
                if ltp is None:
                    continue

                live_df = await asyncio.to_thread(self._build_live_df, sym)
                if live_df.empty:
                    continue

                last = live_df.iloc[-1]
                signal = last["signal"]
                action = self._apply_position(sym, signal)

                if action:
                    await self._emit_action(sym, action, float(last["close"]))
                    telegram_send(self._format_alert(sym, action, last))
            except Exception as e:
                log.error("mcx_multi.symbol_eval_failed", symbol=sym, error=str(e))

    async def _refresh_buf(self, sym: str) -> float | None:
        state = self._states[sym]
        period_start = _candle_start_time()
        df1 = await asyncio.to_thread(
            self._historical.fetch_latest, self._kite, state["token"], "minute", 10,
        )
        if df1.empty:
            return None
        in_period = df1[df1["timestamp"] >= period_start]
        state["buf"] = in_period.to_dict("records")
        return float(state["buf"][-1]["close"]) if state["buf"] else None

    def _live_candle(self, sym: str) -> dict | None:
        buf = self._states[sym]["buf"]
        if not buf:
            return None
        return {
            "timestamp": _candle_start_time(),
            "open":   buf[0]["open"],
            "high":   max(c["high"]  for c in buf),
            "low":    min(c["low"]   for c in buf),
            "close":  buf[-1]["close"],
            "volume": sum(c.get("volume", 0) for c in buf),
        }

    def _build_live_df(self, sym: str) -> pd.DataFrame:
        state = self._states[sym]
        lc = self._live_candle(sym)
        if lc is None:
            return pd.DataFrame()

        base = state["base_df"].copy()
        if not base.empty and base.iloc[-1]["timestamp"] == lc["timestamp"]:
            base = base.iloc[:-1]

        combined = pd.concat([base, pd.DataFrame([lc])], ignore_index=True)
        htf = self._historical.fetch_latest(
            self._kite, state["token"], HTF_INTERVAL, HTF_CANDLES,
        )
        return _add_indicators(combined, htf)

    # ──────────────────────────────────────────────────────────────────────
    # Position state machine
    # ──────────────────────────────────────────────────────────────────────
    def _apply_position(self, sym: str, raw_signal: str) -> str | None:
        state = self._states[sym]
        pos = state["position"]
        if pos is None:
            if raw_signal == "BUY":
                state["position"] = "bought"; return "BUY"
            if raw_signal == "SELL":
                state["position"] = "sold";   return "SELL"
        elif pos == "bought" and raw_signal == "BUY_EXIT":
            state["position"] = None; return "BUY_EXIT"
        elif pos == "sold" and raw_signal == "SEL_EXIT":
            state["position"] = None; return "SEL_EXIT"
        return None

    # ──────────────────────────────────────────────────────────────────────
    # Order emission
    # ──────────────────────────────────────────────────────────────────────
    async def _emit_action(self, sym: str, action: str, ltp: float) -> None:
        state = self._states[sym]
        symbol = state["tradingsymbol"]
        side = OrderSide.BUY if action in ("BUY", "SEL_EXIT") else OrderSide.SELL
        await self.emit_order(symbol, side, self.config.quantity, price=ltp)

    def _format_alert(self, sym: str, action: str, last: pd.Series) -> str:
        ts = self._states[sym]["tradingsymbol"]
        when = datetime.now(IST).strftime("%d-%b-%Y %H:%M:%S")
        return (
            f"<b>MCX {sym} ({ts}) | {when}</b>\n"
            f"<b>Action: {action}</b>  ltp={last['close']}\n"
            f"st={last['st']:.2f}  atr={last['atr']:.2f}  psar={last['psar']:.2f}"
        )
