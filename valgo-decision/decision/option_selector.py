"""Option Strike Selector — called after a spot entry signal.

Two-filter pipeline
───────────────────
Filter 1  Liquidity  : Volume / OI > 15%  (fetched live from Kite quote API)
Filter 2  Confluence : same ST + PSAR + EMA + RSI entry conditions applied to
                       the option's own 5-min OHLC (not spot price)

Ranking (among survivors)
─────────────────────────
  Primary  : Volume/OI descending  (most active wins)
  Tiebreak : proximity to ATM      (ITM1 > ATM > OTM1 > OTM2 > OTM3)

If zero strikes survive both filters → returns None (trade is skipped).
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal

import httpx
import pandas as pd
import pytz

from valgo_common.config import settings
from valgo_common.logging import get_logger

from . import dataframe_indicators as di

log = get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")

# ── Constants ─────────────────────────────────────────────────────────────────

LIQUIDITY_THRESHOLD = 0.15   # Volume / OI must exceed this
MIN_OPTION_BARS     = 30     # minimum 5-min bars needed for reliable indicators

# ATM strike rounding interval per underlying
_STRIKE_INTERVAL: dict[str, int] = {
    "NIFTY":      50,
    "BANKNIFTY":  100,
    "FINNIFTY":   50,
    "MIDCPNIFTY": 25,
    "SENSEX":     100,
}

# Indicator parameters — must match st_psar_confluence.py
_ST_PERIOD   = 10
_ST_MULT     = 3.0
_SGL_PERIOD  = 21
_PSAR_AF0    = 0.02
_PSAR_MAX_AF = 0.2
_ATR_PERIOD  = 14
_RSI_PERIOD  = 14
_RSI_THRESHOLD = 50


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class OptionResult:
    tradingsymbol:    str
    instrument_token: int
    strike:           float
    option_type:      str    # "CE" or "PE"
    ltp:              float
    volume:           int
    oi:               int
    volume_oi_ratio:  float
    proximity:        int    # 0=ITM1, 1=ATM, 2=OTM1, 3=OTM2, 4=OTM3


# ── Selector ──────────────────────────────────────────────────────────────────

class OptionSelector:
    """Selects the best option strike after a spot BUY/SELL signal."""

    def __init__(self) -> None:
        self._nfo_instruments: pd.DataFrame | None = None
        self._nfo_loaded_on:   date | None = None

    # ── Public API ────────────────────────────────────────────────────────────

    async def find_best_strike(
        self,
        symbol:    str,
        action:    Literal["BUY", "SELL"],
        spot_ltp:  float,
    ) -> OptionResult | None:
        """Run the full two-filter pipeline and return the best strike, or None."""
        option_type = "CE" if action == "BUY" else "PE"

        async with httpx.AsyncClient(timeout=30.0) as client:
            # ── Step 1: resolve instruments ───────────────────────────────────
            try:
                instruments = await self._load_nfo_instruments(client)
            except Exception as e:
                log.error("option_selector.instruments_failed", error=str(e))
                return None

            # ── Step 2: nearest expiry for this underlying ────────────────────
            try:
                expiry = self._nearest_expiry(instruments, symbol, option_type)
            except Exception as e:
                log.error("option_selector.expiry_failed", symbol=symbol, error=str(e))
                return None

            # ── Step 3: build 5 candidate strikes and resolve tokens ──────────
            candidates = self._resolve_candidates(
                instruments, symbol, option_type, expiry, spot_ltp
            )
            if not candidates:
                log.warning("option_selector.no_candidates",
                            symbol=symbol, expiry=str(expiry))
                return None

            log.info("option_selector.candidates",
                     symbol=symbol, action=action, expiry=str(expiry),
                     count=len(candidates),
                     strikes=[c["strike"] for c in candidates])

            # ── Step 4: fetch live quotes → Volume / OI filter ────────────────
            liquid = await self._liquidity_filter(client, candidates, option_type)
            if not liquid:
                log.warning("option_selector.all_failed_liquidity",
                            symbol=symbol, threshold=LIQUIDITY_THRESHOLD)
                return None

            # ── Step 5: fetch option OHLC + strategy confirmation ─────────────
            confirmed = await self._strategy_filter(client, liquid, action)
            if not confirmed:
                log.warning("option_selector.all_failed_strategy", symbol=symbol)
                return None

            # ── Step 6: rank and return best ──────────────────────────────────
            confirmed.sort(key=lambda x: (-x["volume_oi_ratio"], x["proximity"]))
            best = confirmed[0]

            log.info("option_selector.selected",
                     symbol=symbol, action=action,
                     tradingsymbol=best["tradingsymbol"],
                     strike=best["strike"],
                     vol_oi=round(best["volume_oi_ratio"], 3))

            return OptionResult(
                tradingsymbol=best["tradingsymbol"],
                instrument_token=best["instrument_token"],
                strike=best["strike"],
                option_type=option_type,
                ltp=best["ltp"],
                volume=best["volume"],
                oi=best["oi"],
                volume_oi_ratio=best["volume_oi_ratio"],
                proximity=best["proximity"],
            )

    # ── Instruments cache ─────────────────────────────────────────────────────

    async def _load_nfo_instruments(self, client: httpx.AsyncClient) -> pd.DataFrame:
        today = datetime.now(IST).date()
        if self._nfo_instruments is not None and self._nfo_loaded_on == today:
            return self._nfo_instruments

        resp = await client.get(
            "https://api.kite.trade/instruments/NFO",
            headers=self._headers(),
        )
        resp.raise_for_status()

        df = pd.read_csv(io.StringIO(resp.text))
        df["expiry"] = pd.to_datetime(df["expiry"], errors="coerce").dt.date
        df["strike"] = pd.to_numeric(df["strike"], errors="coerce")
        df = df.dropna(subset=["expiry", "strike"])

        self._nfo_instruments = df
        self._nfo_loaded_on   = today
        log.info("option_selector.instruments_loaded", rows=len(df))
        return df

    # ── Expiry resolution ─────────────────────────────────────────────────────

    def _nearest_expiry(
        self,
        instruments: pd.DataFrame,
        symbol:      str,
        option_type: str,
    ) -> date:
        today = datetime.now(IST).date()
        subset = instruments[
            (instruments["name"] == symbol) &
            (instruments["instrument_type"] == option_type) &
            (instruments["expiry"] >= today)
        ]
        if subset.empty:
            raise ValueError(f"No {option_type} options found for {symbol}")
        return subset["expiry"].min()

    # ── Strike builder ────────────────────────────────────────────────────────

    def _resolve_candidates(
        self,
        instruments: pd.DataFrame,
        symbol:      str,
        option_type: str,
        expiry:      date,
        spot_ltp:    float,
    ) -> list[dict[str, Any]]:
        interval = _STRIKE_INTERVAL.get(symbol, 50)
        atm = round(spot_ltp / interval) * interval

        if option_type == "CE":
            # BUY signal: ITM1 below ATM, OTMs above
            strikes_with_proximity = [
                (atm - interval,     0),   # ITM1
                (atm,                1),   # ATM
                (atm + interval,     2),   # OTM1
                (atm + 2 * interval, 3),   # OTM2
                (atm + 3 * interval, 4),   # OTM3
            ]
        else:
            # SELL signal: ITM1 above ATM, OTMs below
            strikes_with_proximity = [
                (atm + interval,     0),   # ITM1
                (atm,                1),   # ATM
                (atm - interval,     2),   # OTM1
                (atm - 2 * interval, 3),   # OTM2
                (atm - 3 * interval, 4),   # OTM3
            ]

        expiry_df = instruments[
            (instruments["name"] == symbol) &
            (instruments["instrument_type"] == option_type) &
            (instruments["expiry"] == expiry)
        ]

        candidates = []
        for strike_price, proximity in strikes_with_proximity:
            row = expiry_df[expiry_df["strike"] == float(strike_price)]
            if row.empty:
                continue
            r = row.iloc[0]
            candidates.append({
                "tradingsymbol":    r["tradingsymbol"],
                "instrument_token": int(r["instrument_token"]),
                "strike":           float(strike_price),
                "proximity":        proximity,
            })
        return candidates

    # ── Filter 1: Liquidity ───────────────────────────────────────────────────

    async def _liquidity_filter(
        self,
        client:      httpx.AsyncClient,
        candidates:  list[dict],
        option_type: str,
    ) -> list[dict]:
        tokens = [f"NFO:{c['tradingsymbol']}" for c in candidates]
        try:
            resp = await client.get(
                "https://api.kite.trade/quote",
                headers=self._headers(),
                params={"i": tokens},
                timeout=15.0,
            )
            resp.raise_for_status()
            quotes = resp.json().get("data", {})
        except Exception as e:
            log.error("option_selector.quote_failed", error=str(e))
            return []

        liquid = []
        for c in candidates:
            key = f"NFO:{c['tradingsymbol']}"
            q      = quotes.get(key, {})
            volume = int(q.get("volume", 0) or 0)
            oi     = int(q.get("oi",     0) or 0)
            ltp    = float(q.get("last_price", 0) or 0)
            ratio  = volume / oi if oi > 0 else 0.0

            log.info("option_selector.liquidity_check",
                     strike=c["strike"], type=option_type,
                     volume=volume, oi=oi,
                     ratio=round(ratio, 3),
                     pass_=ratio > LIQUIDITY_THRESHOLD)

            if ratio > LIQUIDITY_THRESHOLD:
                liquid.append({
                    **c,
                    "volume":           volume,
                    "oi":               oi,
                    "ltp":              ltp,
                    "volume_oi_ratio":  ratio,
                })
        return liquid

    # ── Filter 2: Strategy confirmation on option OHLC ────────────────────────

    async def _strategy_filter(
        self,
        client:  httpx.AsyncClient,
        liquid:  list[dict],
        action:  str,
    ) -> list[dict]:
        now_ist = datetime.now(IST)
        # Fetch up to 5 calendar days back to cover at least one full trading week
        from_dt = (now_ist - timedelta(days=5)).replace(
            hour=9, minute=0, second=0, microsecond=0
        ).strftime("%Y-%m-%d %H:%M:%S")
        to_dt = now_ist.strftime("%Y-%m-%d %H:%M:%S")

        confirmed = []
        for c in liquid:
            try:
                resp = await client.get(
                    f"https://api.kite.trade/instruments/historical"
                    f"/{c['instrument_token']}/5minute",
                    headers=self._headers(),
                    params={"from": from_dt, "to": to_dt, "continuous": 0, "oi": 1},
                    timeout=15.0,
                )
                resp.raise_for_status()
                candles = resp.json()["data"]["candles"]

                # Drop the current (possibly incomplete) forming candle
                if candles:
                    candles = candles[:-1]

                if len(candles) < MIN_OPTION_BARS:
                    log.warning("option_selector.insufficient_bars",
                                strike=c["strike"], bars=len(candles),
                                required=MIN_OPTION_BARS)
                    continue

                cols = ["timestamp", "open", "high", "low", "close", "volume", "oi"]
                df = pd.DataFrame(candles, columns=cols[:len(candles[0])])
                for col in ("open", "high", "low", "close"):
                    df[col] = df[col].astype(float)

                passes, indicators = self._check_strategy(df, c["ltp"], action)

                log.info("option_selector.strategy_check",
                         strike=c["strike"],
                         bars=len(candles),
                         passes=passes,
                         **{k: round(v, 2) for k, v in indicators.items()})

                if passes:
                    confirmed.append(c)

            except Exception as e:
                log.error("option_selector.ohlc_failed",
                          strike=c["strike"], error=str(e))

        return confirmed

    def _check_strategy(
        self,
        df:     pd.DataFrame,
        ltp:    float,
        action: str,
    ) -> tuple[bool, dict]:
        """Run ST+PSAR+EMA+RSI on option OHLC and apply entry conditions."""
        atr = di.calculate_atr(df, period=_ATR_PERIOD).iloc[-1]
        sgl = di.calculate_ema(df, period=_SGL_PERIOD).iloc[-1]
        rsi = di.calculate_rsi(df, period=_RSI_PERIOD).iloc[-1]

        psar_df   = di.calculate_psar(df, af0=_PSAR_AF0, max_af=_PSAR_MAX_AF)
        long_col  = next(c for c in psar_df.columns if c.startswith("PSARl"))
        short_col = next(c for c in psar_df.columns if c.startswith("PSARs"))
        last_psar = psar_df.iloc[-1]
        psar = (float(last_psar[long_col])
                if not math.isnan(float(last_psar[long_col]))
                else float(last_psar[short_col]))

        st_df  = di.calculate_supertrend(df, period=_ST_PERIOD, multiplier=_ST_MULT)
        st_col = next(
            c for c in st_df.columns
            if c.startswith("SUPERT_") and
            not c.startswith(("SUPERTd", "SUPERTl", "SUPERTs"))
        )
        st  = float(st_df[st_col].iloc[-1])
        atr = float(atr)
        sgl = float(sgl)
        rsi = float(rsi)

        indicators = {"st": st, "psar": psar, "sgl": sgl, "atr": atr, "rsi": rsi}

        if any(math.isnan(v) for v in indicators.values()):
            return False, indicators

        if action == "BUY":
            c1 = ltp >= st + atr * 0.2
            c2 = (ltp - st) < atr
            c3 = ltp > psar
            c4 = ltp > sgl or (sgl - ltp) > 5 * atr
            passes = c1 and c2 and c3 and c4 and rsi > _RSI_THRESHOLD
        else:
            c1 = ltp <= st - atr * 0.2
            c2 = (st - ltp) < atr
            c3 = ltp < psar
            c4 = ltp < sgl or (ltp - sgl) > 5 * atr
            passes = c1 and c2 and c3 and c4 and rsi < _RSI_THRESHOLD

        return passes, indicators

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"token {settings.kite_api_key}:{settings.kite_access_token}",
            "X-Kite-Version": "3",
        }
