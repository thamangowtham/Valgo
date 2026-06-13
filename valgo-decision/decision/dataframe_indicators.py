"""DataFrame-style indicators (TA-Lib + small inline SuperTrend).

Companion to ``decision/indicators.py``. The TA-Lib helpers in that module
are tuned for streaming use ("compute the last value from a rolling deque").
The helpers here operate on the *full* OHLC DataFrame at once, which is what
bar-based strategies and backtests want.

History note: an earlier version of this module wrapped pandas-ta. That
package's PyPI release line currently requires Python 3.12+, and its source
repository (twopirllc/pandas-ta) was deleted, so we now wrap TA-Lib for
everything except SuperTrend (which TA-Lib does not include — implemented
inline at the bottom of this file).

When to reach for which:
    - on_tick path, latency-sensitive  -> indicators.py (TA-Lib, last-value)
    - bar close, full-frame indicators -> dataframe_indicators.py (this file)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import talib


# ── Volatility ────────────────────────────────────────────────────────────────

def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    out = talib.ATR(df["high"].values, df["low"].values, df["close"].values, timeperiod=period)
    return pd.Series(out, index=df.index)


# ── Trend / Moving Averages ───────────────────────────────────────────────────

def calculate_ema(df: pd.DataFrame, period: int = 20) -> pd.Series:
    return pd.Series(talib.EMA(df["close"].values, timeperiod=period), index=df.index)


def calculate_sma(df: pd.DataFrame, period: int = 20) -> pd.Series:
    return pd.Series(talib.SMA(df["close"].values, timeperiod=period), index=df.index)


def calculate_psar(
    df: pd.DataFrame,
    af0: float = 0.02,
    af: float = 0.02,    # kept for API compat with the old pandas-ta wrapper
    max_af: float = 0.2,
) -> pd.DataFrame:
    """Parabolic SAR.

    TA-Lib returns a single SAR series. Callers expect the pandas-ta-style
    DataFrame layout with separate long/short columns and a reversal flag,
    so we split TA-Lib's output by direction and synthesise the reversal
    column.

    Returns columns:
        PSARl_{af0}_{max_af}  — SAR value when in uptrend (NaN otherwise)
        PSARs_{af0}_{max_af}  — SAR value when in downtrend (NaN otherwise)
        PSARr_{af0}_{max_af}  — 1 on the bar where the trend just reversed
    """
    high = df["high"].values
    low = df["low"].values
    close = df["close"].values

    sar = talib.SAR(high, low, acceleration=af0, maximum=max_af)
    in_long = close > sar    # uptrend mask

    long_col  = f"PSARl_{af0}_{max_af}"
    short_col = f"PSARs_{af0}_{max_af}"
    rev_col   = f"PSARr_{af0}_{max_af}"

    long_vals  = np.where(in_long,  sar, np.nan)
    short_vals = np.where(~in_long, sar, np.nan)

    # Reversal flag: 1 on the bar where long/short flips
    rev = np.zeros(len(close), dtype=np.int64)
    rev[1:] = (in_long[1:] != in_long[:-1]).astype(np.int64)

    out = pd.DataFrame(
        {long_col: long_vals, short_col: short_vals, rev_col: rev},
        index=df.index,
    )
    return out


def calculate_supertrend(
    df: pd.DataFrame,
    period: int = 7,
    multiplier: float = 3.0,
) -> pd.DataFrame:
    """SuperTrend (not in TA-Lib — implemented here).

    Algorithm: classic Olivier Seban / pandas-ta variant.
        ATR        = TA-Lib ATR(period)
        upper_band = (high+low)/2 + multiplier * ATR
        lower_band = (high+low)/2 - multiplier * ATR
        upper_band trails non-increasingly while close stays below it
        lower_band trails non-decreasingly while close stays above it
        direction flips when close crosses the active band
        SuperTrend line = lower_band when in uptrend, upper_band in downtrend

    Returns columns matching the old pandas-ta layout:
        SUPERT_{period}_{multiplier}    — SuperTrend line value
        SUPERTd_{period}_{multiplier}   — Direction: 1=up, -1=down
        SUPERTl_{period}_{multiplier}   — Long support line (NaN in downtrend)
        SUPERTs_{period}_{multiplier}   — Short resistance line (NaN in uptrend)
    """
    high = df["high"].values.astype(np.float64)
    low = df["low"].values.astype(np.float64)
    close = df["close"].values.astype(np.float64)
    n = len(close)

    atr = talib.ATR(high, low, close, timeperiod=period)
    hl2 = (high + low) / 2.0
    upper = hl2 + multiplier * atr
    lower = hl2 - multiplier * atr

    st = np.full(n, np.nan)
    direction = np.zeros(n, dtype=np.int64)

    # First valid bar: where ATR has stabilised.
    start = period
    while start < n and (np.isnan(atr[start]) or np.isnan(upper[start])):
        start += 1
    if start >= n:
        return _empty_supertrend(df, period, multiplier)

    direction[start] = 1     # arbitrary seed — first iteration overrides
    st[start] = lower[start]

    for i in range(start + 1, n):
        # Trailing bands
        if upper[i] > upper[i - 1] and close[i - 1] <= upper[i - 1]:
            upper[i] = upper[i - 1]
        if lower[i] < lower[i - 1] and close[i - 1] >= lower[i - 1]:
            lower[i] = lower[i - 1]

        # Direction flip on close crossing the active band
        if direction[i - 1] == 1:
            direction[i] = -1 if close[i] < lower[i - 1] else 1
        else:
            direction[i] = 1 if close[i] > upper[i - 1] else -1

        st[i] = lower[i] if direction[i] == 1 else upper[i]

    sufx = f"_{period}_{multiplier}"
    return pd.DataFrame(
        {
            f"SUPERT{sufx}":  st,
            f"SUPERTd{sufx}": direction,
            f"SUPERTl{sufx}": np.where(direction == 1,  st, np.nan),
            f"SUPERTs{sufx}": np.where(direction == -1, st, np.nan),
        },
        index=df.index,
    )


def _empty_supertrend(df: pd.DataFrame, period: int, multiplier: float) -> pd.DataFrame:
    sufx = f"_{period}_{multiplier}"
    nan_col = np.full(len(df), np.nan)
    return pd.DataFrame(
        {
            f"SUPERT{sufx}":  nan_col,
            f"SUPERTd{sufx}": np.zeros(len(df), dtype=np.int64),
            f"SUPERTl{sufx}": nan_col,
            f"SUPERTs{sufx}": nan_col,
        },
        index=df.index,
    )


# ── Momentum ──────────────────────────────────────────────────────────────────

def calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return pd.Series(talib.RSI(df["close"].values, timeperiod=period), index=df.index)


def calculate_macd(
    df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9,
) -> pd.DataFrame:
    macd, sig, hist = talib.MACD(
        df["close"].values, fastperiod=fast, slowperiod=slow, signalperiod=signal,
    )
    sufx = f"_{fast}_{slow}_{signal}"
    return pd.DataFrame(
        {f"MACD{sufx}": macd, f"MACDh{sufx}": hist, f"MACDs{sufx}": sig},
        index=df.index,
    )


def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    return pd.DataFrame(
        {
            f"ADX_{period}": talib.ADX(h, l, c, timeperiod=period),
            f"DMP_{period}": talib.PLUS_DI(h, l, c, timeperiod=period),
            f"DMN_{period}": talib.MINUS_DI(h, l, c, timeperiod=period),
        },
        index=df.index,
    )


# ── Bands ─────────────────────────────────────────────────────────────────────

def calculate_bbands(df: pd.DataFrame, period: int = 20, std: float = 2.0) -> pd.DataFrame:
    upper, mid, lower = talib.BBANDS(
        df["close"].values, timeperiod=period, nbdevup=std, nbdevdn=std,
    )
    sufx = f"_{period}_{std}"
    return pd.DataFrame(
        {f"BBL{sufx}": lower, f"BBM{sufx}": mid, f"BBU{sufx}": upper},
        index=df.index,
    )


# ── Volume ────────────────────────────────────────────────────────────────────

def calculate_vwap(df: pd.DataFrame) -> pd.Series:
    """Session-anchored VWAP. Resets at the day boundary, computed inline
    because TA-Lib doesn't ship VWAP."""
    if "timestamp" in df.columns:
        day = pd.to_datetime(df["timestamp"]).dt.date.values
    else:
        day = pd.to_datetime(df.index).date

    tp = (df["high"].values + df["low"].values + df["close"].values) / 3.0
    vol = df["volume"].values.astype(np.float64)

    cum_pv = np.zeros(len(tp))
    cum_v = np.zeros(len(tp))
    last_day = None
    pv = 0.0
    v = 0.0
    for i, d in enumerate(day):
        if d != last_day:
            pv = 0.0
            v = 0.0
            last_day = d
        pv += tp[i] * vol[i]
        v += vol[i]
        cum_pv[i] = pv
        cum_v[i] = v
    vwap = np.where(cum_v > 0, cum_pv / cum_v, np.nan)
    return pd.Series(vwap, index=df.index)


# ── OHLC resample (for non-standard timeframes like 75m / 90m) ────────────────

def resample_ohlc(df: pd.DataFrame, factor: int) -> pd.DataFrame:
    """Group every `factor` candles per day into a single candle.

    Used for timeframes Kite doesn't natively expose (e.g. 75m = 5x of 15m).
    Resampling honours day boundaries — we don't merge candles across sessions.
    """
    df = df.copy()
    df["_date"] = df["timestamp"].dt.date
    out = []
    for _, day_df in df.groupby("_date"):
        for i in range(0, len(day_df), factor):
            chunk = day_df.iloc[i: i + factor]
            out.append({
                "timestamp": chunk.iloc[0]["timestamp"],
                "open":      chunk.iloc[0]["open"],
                "high":      chunk["high"].max(),
                "low":       chunk["low"].min(),
                "close":     chunk.iloc[-1]["close"],
                "volume":    chunk["volume"].sum() if "volume" in chunk else 0,
            })
    df.drop(columns=["_date"], inplace=True)
    return pd.DataFrame(out).reset_index(drop=True)
