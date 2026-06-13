"""TA-Lib wrappers tuned for streaming tick data.

TA-Lib operates on numpy arrays — it expects the full price series each call
and returns the indicator series. For streaming use we maintain a rolling
deque per instrument and call TA-Lib on the deque snapshot.

These helpers are thin — they exist so strategy code reads as:
    ema = ema_last(prices, 21)
instead of:
    arr = np.fromiter(prices, dtype=np.float64)
    ema = talib.EMA(arr, timeperiod=21)[-1]

Used by strategies in services/decision/strategies/.
"""
from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import talib


def _arr(prices: Iterable[float]) -> np.ndarray:
    """Convert any iterable (list, deque, generator) to numpy float64."""
    return np.fromiter(prices, dtype=np.float64)


# ============================================================================
# "Last value" wrappers — return the most recent indicator value as a float,
# or NaN if the series is too short for the indicator's lookback.
# ============================================================================
def ema_last(prices: Iterable[float], period: int) -> float:
    return float(talib.EMA(_arr(prices), timeperiod=period)[-1])


def sma_last(prices: Iterable[float], period: int) -> float:
    return float(talib.SMA(_arr(prices), timeperiod=period)[-1])


def rsi_last(prices: Iterable[float], period: int = 14) -> float:
    return float(talib.RSI(_arr(prices), timeperiod=period)[-1])


def atr_last(high: Iterable[float], low: Iterable[float], close: Iterable[float], period: int = 14) -> float:
    return float(talib.ATR(_arr(high), _arr(low), _arr(close), timeperiod=period)[-1])


def adx_last(high: Iterable[float], low: Iterable[float], close: Iterable[float], period: int = 14) -> float:
    return float(talib.ADX(_arr(high), _arr(low), _arr(close), timeperiod=period)[-1])


def macd_last(prices: Iterable[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[float, float, float]:
    """Returns (macd, signal, histogram) — last values of each."""
    macd, sig, hist = talib.MACD(_arr(prices), fastperiod=fast, slowperiod=slow, signalperiod=signal)
    return float(macd[-1]), float(sig[-1]), float(hist[-1])


def bollinger_last(prices: Iterable[float], period: int = 20, std: float = 2.0) -> tuple[float, float, float]:
    """Returns (upper, middle, lower) — last values."""
    upper, middle, lower = talib.BBANDS(_arr(prices), timeperiod=period, nbdevup=std, nbdevdn=std)
    return float(upper[-1]), float(middle[-1]), float(lower[-1])


# ============================================================================
# Cross detection — utility used by many strategies
# ============================================================================
def crossed_up(prev_diff: float | None, curr_diff: float) -> bool:
    """Return True if signal just crossed from non-positive to positive."""
    if prev_diff is None:
        return False
    return prev_diff <= 0 < curr_diff


def crossed_down(prev_diff: float | None, curr_diff: float) -> bool:
    """Return True if signal just crossed from non-negative to negative."""
    if prev_diff is None:
        return False
    return prev_diff >= 0 > curr_diff
