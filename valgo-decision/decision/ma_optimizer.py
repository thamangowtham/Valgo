"""MA and SuperTrend parameter optimizer.

Scores every MA period (3–250) and every SuperTrend (period, multiplier)
combination using four structural metrics and returns a ranked table.

METRIC DEFINITIONS
──────────────────
Touch   — price enters the tolerance band around the line WITHOUT crossing to
          the other side.  Three conditions (any one qualifies):
              A: wick enters the band   (low ≤ upper_band OR high ≥ lower_band)
              B: close is inside band
              C: MA/ST line is inside the candle body
          Crosses are explicitly excluded — if the previous definitive side
          and the exit side are opposites, it is a cross, not a touch.

Bounce  — after a touch, the next CONF candles confirm the price moved away:
              method="avg"  → mean(future_closes) > touch_close  (support)
                              mean(future_closes) < touch_close  (resistance)
              method="all"  → ALL future_closes satisfy the condition
          Cooldown: once a bounce is counted, skip CONF candles so the same
          event is not double-counted.

Touch frequency (bell-curve) — raw_freq = touches / total_candles.
          Bell-curve peaks at TARGET_FREQ (~8%) and penalizes both extremes,
          making the score fair across all periods (avoids short-period bias).

Clean-cross ratio — a CROSS is a definitive side change.  A WHIPSAW is a
          cross followed by another cross within WHIPSAW_WINDOW candles.
          Whipsaws are chained: A→B→A→B = 2 whipsaws, not 4 crosses.
          clean_cross_ratio = (total_crosses − whipsaws) / total_crosses.

FINAL SCORE
───────────
  score = bounce_rate × 0.50
        + touch_freq  × 0.30
        + clean_cross × 0.20
  Multiplied by 100 → range 0–100.

Usage
─────
  from decision.ma_optimizer import find_best_ma, find_best_supertrend

  # df must have columns: open, high, low, close
  top_ma = find_best_ma(df, top_n=5)
  top_st = find_best_supertrend(df, top_n=5)
  print(top_ma.to_string())
  print(top_st.to_string())
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Literal

import numpy as np
import pandas as pd
import talib

# ── Configuration ─────────────────────────────────────────────────────────────

CONF            = 3       # confirmation candles for bounce
TOL_ATR_MULT    = 0.10    # tolerance band = line ± (TOL_ATR_MULT × ATR)
WHIPSAW_WINDOW  = 5       # max candles between two crosses to count as whipsaw
TARGET_FREQ     = 0.08    # bell-curve peak: 8% touch frequency is ideal
FREQ_SIGMA      = 0.06    # bell-curve width

# Score weights
W_BOUNCE = 0.50
W_FREQ   = 0.30
W_CROSS  = 0.20


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class MAScore:
    period:        int
    ma_type:       str        # "EMA" | "SMA"
    bounce_rate:   float      # bounces / touches
    touch_freq:    float      # bell-curve normalised frequency (0–1)
    clean_cross:   float      # clean crosses / total crosses (0–1)
    score:         float      # 0–100 final score
    touches:       int
    bounces:       int
    total_crosses: int
    clean_crosses: int


@dataclass
class STScore:
    period:        int
    multiplier:    float
    bounce_rate:   float
    touch_freq:    float
    clean_cross:   float
    score:         float
    touches:       int
    bounces:       int
    total_flips:   int        # total direction changes
    clean_flips:   int        # direction changes that were not whipsaws


# ── Internal helpers ──────────────────────────────────────────────────────────

def _sticky_side(close: np.ndarray,
                 upper: np.ndarray,
                 lower: np.ndarray) -> np.ndarray:
    """Return the last 'definitive' side for each bar.

    +1 = last clear close was above the upper band
    -1 = last clear close was below the lower band
     0 = no definitive side yet (startup)
    """
    n = len(close)
    sides = np.zeros(n, dtype=np.int8)
    current = 0
    for i in range(n):
        if close[i] > upper[i]:
            current = 1
        elif close[i] < lower[i]:
            current = -1
        # inside band → keep current
        sides[i] = current
    return sides


def _freq_bell(freq: float) -> float:
    """Bell-curve score: 1.0 at TARGET_FREQ, decays toward 0 at extremes."""
    return math.exp(-((freq - TARGET_FREQ) ** 2) / (2 * FREQ_SIGMA ** 2))


def _candle_touches_band(high_i: float, low_i: float,
                         open_i: float, close_i: float,
                         line: float,
                         upper: float, lower: float,
                         from_side: int) -> bool:
    """True if this candle constitutes a touch from `from_side`.

    from_side = +1 → price approached from above (support touch)
    from_side = -1 → price approached from below (resistance touch)

    Any of three sub-conditions qualifies (A / B / C).
    """
    if from_side == 1:
        # Case A: low wick enters the upper band
        caseA = low_i <= upper
        # Case B: close is inside the band
        caseB = lower <= close_i <= upper
        # Case C: line is inside the candle body
        body_lo = min(open_i, close_i)
        body_hi = max(open_i, close_i)
        caseC = body_lo <= line <= body_hi
        return caseA or caseB or caseC
    else:  # from_side == -1
        # Case A: high wick enters the lower band
        caseA = high_i >= lower
        # Case B: close is inside the band
        caseB = lower <= close_i <= upper
        # Case C: line is inside the candle body
        body_lo = min(open_i, close_i)
        body_hi = max(open_i, close_i)
        caseC = body_lo <= line <= body_hi
        return caseA or caseB or caseC


# ── Core metric engines ───────────────────────────────────────────────────────

def _touches_and_bounces(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    line: np.ndarray,
    upper: np.ndarray,
    lower: np.ndarray,
    conf: int,
    method: Literal["avg", "all"],
) -> tuple[int, int]:
    """Return (touches, bounces) for a given indicator line."""
    n = len(close)
    sides = _sticky_side(close, upper, lower)

    touches = 0
    bounces = 0
    skip_until = 0

    for i in range(1, n - conf):
        if i < skip_until:
            continue
        if np.isnan(line[i]) or np.isnan(upper[i]):
            continue

        prev_side = sides[i - 1]
        curr_side = sides[i]

        # Skip bars where we have no prior context
        if prev_side == 0:
            continue

        # EXCLUDE crosses: price went from one definitive side to the other
        # (prev_side and curr_side both non-zero and opposite)
        if curr_side != 0 and curr_side != prev_side:
            continue  # this is a cross, not a touch

        # Check if this candle touches the band from prev_side
        if not _candle_touches_band(
            high[i], low[i], open_[i], close[i],
            line[i], upper[i], lower[i],
            from_side=prev_side,
        ):
            continue

        touches += 1

        # Bounce check over the next CONF candles
        future = close[i + 1: i + 1 + conf]
        if len(future) < conf:
            continue

        if method == "all":
            bounced = (
                all(c > close[i] for c in future) if prev_side == 1
                else all(c < close[i] for c in future)
            )
        else:  # "avg"
            bounced = (
                float(np.mean(future)) > close[i] if prev_side == 1
                else float(np.mean(future)) < close[i]
            )

        if bounced:
            bounces += 1
            skip_until = i + conf  # cooldown guard — no double-counting

    return touches, bounces


def _crosses_and_whipsaws(
    sides: np.ndarray,
    whipsaw_window: int,
) -> tuple[int, int]:
    """Return (total_crosses, whipsaws) using chained skip_until logic."""
    # Find all cross indices
    cross_idx: list[int] = []
    for i in range(1, len(sides)):
        if sides[i] != 0 and sides[i - 1] != 0 and sides[i] != sides[i - 1]:
            cross_idx.append(i)

    if not cross_idx:
        return 0, 0

    total = len(cross_idx)
    whipsaws = 0
    skip_until = 0

    for k, ci in enumerate(cross_idx):
        if ci < skip_until:
            continue
        # Is there a follow-up cross within whipsaw_window?
        if k + 1 < len(cross_idx) and cross_idx[k + 1] - ci <= whipsaw_window:
            whipsaws += 1
            # Chain: skip the paired cross so it is not counted again
            skip_until = cross_idx[k + 1] + 1

    return total, whipsaws


# ── MA scorer ─────────────────────────────────────────────────────────────────

def score_ma(
    df: pd.DataFrame,
    period: int,
    ma_type: Literal["EMA", "SMA"] = "EMA",
    conf: int = CONF,
    method: Literal["avg", "all"] = "avg",
    tol_mult: float = TOL_ATR_MULT,
    whipsaw_window: int = WHIPSAW_WINDOW,
) -> MAScore:
    close = df["close"].values.astype(np.float64)
    high  = df["high"].values.astype(np.float64)
    low   = df["low"].values.astype(np.float64)
    open_ = df["open"].values.astype(np.float64)

    ma  = talib.EMA(close, timeperiod=period) if ma_type == "EMA" else talib.SMA(close, timeperiod=period)
    atr = talib.ATR(high, low, close, timeperiod=14)

    upper = ma + tol_mult * atr
    lower = ma - tol_mult * atr

    n = len(close)
    touches, bounces = _touches_and_bounces(
        open_, high, low, close, ma, upper, lower, conf, method
    )

    sides = _sticky_side(close, upper, lower)
    total_crosses, whipsaws = _crosses_and_whipsaws(sides, whipsaw_window)
    clean_crosses = max(0, total_crosses - whipsaws)

    bounce_rate  = bounces / touches                          if touches > 0 else 0.0
    raw_freq     = touches / n                                if n > 0       else 0.0
    freq_score   = _freq_bell(raw_freq)
    cross_score  = clean_crosses / total_crosses              if total_crosses > 0 else 0.0

    final = (bounce_rate * W_BOUNCE + freq_score * W_FREQ + cross_score * W_CROSS) * 100

    return MAScore(
        period=period, ma_type=ma_type,
        bounce_rate=round(bounce_rate, 4),
        touch_freq=round(freq_score, 4),
        clean_cross=round(cross_score, 4),
        score=round(final, 2),
        touches=touches, bounces=bounces,
        total_crosses=total_crosses, clean_crosses=clean_crosses,
    )


# ── SuperTrend scorer ─────────────────────────────────────────────────────────

def _compute_supertrend(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    period: int,
    multiplier: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (st_line, direction) arrays.  direction: +1 = uptrend, -1 = downtrend."""
    n = len(close)
    atr   = talib.ATR(high, low, close, timeperiod=period)
    hl2   = (high + low) / 2.0
    upper = hl2 + multiplier * atr
    lower = hl2 - multiplier * atr

    st        = np.full(n, np.nan)
    direction = np.zeros(n, dtype=np.int8)

    start = period
    while start < n and np.isnan(atr[start]):
        start += 1
    if start >= n:
        return st, direction

    direction[start] = 1
    st[start] = lower[start]

    for i in range(start + 1, n):
        # Trail bands
        if upper[i] > upper[i - 1] and close[i - 1] <= upper[i - 1]:
            upper[i] = upper[i - 1]
        if lower[i] < lower[i - 1] and close[i - 1] >= lower[i - 1]:
            lower[i] = lower[i - 1]

        if direction[i - 1] == 1:
            direction[i] = -1 if close[i] < lower[i - 1] else 1
        else:
            direction[i] = 1 if close[i] > upper[i - 1] else -1

        st[i] = lower[i] if direction[i] == 1 else upper[i]

    return st, direction


def score_supertrend(
    df: pd.DataFrame,
    period: int,
    multiplier: float,
    conf: int = CONF,
    method: Literal["avg", "all"] = "avg",
    tol_mult: float = TOL_ATR_MULT,
    whipsaw_window: int = WHIPSAW_WINDOW,
) -> STScore:
    close = df["close"].values.astype(np.float64)
    high  = df["high"].values.astype(np.float64)
    low   = df["low"].values.astype(np.float64)
    open_ = df["open"].values.astype(np.float64)

    atr_base = talib.ATR(high, low, close, timeperiod=14)
    st_line, direction = _compute_supertrend(high, low, close, period, multiplier)

    upper = st_line + tol_mult * atr_base
    lower = st_line - tol_mult * atr_base

    n = len(close)
    touches, bounces = _touches_and_bounces(
        open_, high, low, close, st_line, upper, lower, conf, method
    )

    # For SuperTrend, "crosses" = direction flips
    total_flips = int(np.sum(np.abs(np.diff(direction[direction != 0])) > 0))
    # Whipsaw on direction array
    dir_nonzero = direction[direction != 0]
    flip_indices = [i for i in range(1, len(dir_nonzero)) if dir_nonzero[i] != dir_nonzero[i - 1]]

    # Map flip indices back to actual candle indices for whipsaw distance check
    nonzero_idx = np.where(direction != 0)[0]
    real_flip_idx = [nonzero_idx[k] for k in flip_indices]

    whipsaws = 0
    skip_until = 0
    for k, ci in enumerate(real_flip_idx):
        if ci < skip_until:
            continue
        if k + 1 < len(real_flip_idx) and real_flip_idx[k + 1] - ci <= whipsaw_window:
            whipsaws += 1
            skip_until = real_flip_idx[k + 1] + 1

    clean_flips = max(0, total_flips - whipsaws)

    bounce_rate = bounces / touches                       if touches > 0     else 0.0
    raw_freq    = touches / n                             if n > 0           else 0.0
    freq_score  = _freq_bell(raw_freq)
    cross_score = clean_flips / total_flips               if total_flips > 0 else 0.0

    final = (bounce_rate * W_BOUNCE + freq_score * W_FREQ + cross_score * W_CROSS) * 100

    return STScore(
        period=period, multiplier=multiplier,
        bounce_rate=round(bounce_rate, 4),
        touch_freq=round(freq_score, 4),
        clean_cross=round(cross_score, 4),
        score=round(final, 2),
        touches=touches, bounces=bounces,
        total_flips=total_flips, clean_flips=clean_flips,
    )


# ── Public API ────────────────────────────────────────────────────────────────

def find_best_ma(
    df: pd.DataFrame,
    periods: range | list[int] | None = None,
    ma_types: tuple[str, ...] = ("EMA", "SMA"),
    conf: int = CONF,
    method: Literal["avg", "all"] = "avg",
    tol_mult: float = TOL_ATR_MULT,
    whipsaw_window: int = WHIPSAW_WINDOW,
    top_n: int = 10,
) -> pd.DataFrame:
    """Score all MA (period, type) combinations and return top_n sorted by score.

    Parameters
    ----------
    df          : OHLC DataFrame with columns open, high, low, close
    periods     : iterable of MA periods to test; default = range(3, 251)
    ma_types    : which MA types to test ("EMA", "SMA", or both)
    conf        : confirmation candles for bounce detection
    method      : "avg" (trending) or "all" (ranging) bounce method
    tol_mult    : band width as multiple of ATR(14)
    top_n       : how many results to return

    Returns
    -------
    DataFrame with columns: period, ma_type, score, bounce_rate, touch_freq,
    clean_cross, touches, bounces, total_crosses, clean_crosses
    """
    if periods is None:
        periods = range(3, 251)

    rows = []
    for ma_type in ma_types:
        for period in periods:
            try:
                s = score_ma(df, period, ma_type, conf, method, tol_mult, whipsaw_window)
                rows.append(asdict(s))
            except Exception:
                pass

    if not rows:
        return pd.DataFrame()

    result = (
        pd.DataFrame(rows)
        .sort_values("score", ascending=False)
        .head(top_n)
        .reset_index(drop=True)
    )
    return result


def find_best_supertrend(
    df: pd.DataFrame,
    periods: range | list[int] | None = None,
    multipliers: list[float] | None = None,
    conf: int = CONF,
    method: Literal["avg", "all"] = "avg",
    tol_mult: float = TOL_ATR_MULT,
    whipsaw_window: int = WHIPSAW_WINDOW,
    top_n: int = 10,
) -> pd.DataFrame:
    """Score all SuperTrend (period, multiplier) combinations and return top_n.

    Parameters
    ----------
    df          : OHLC DataFrame with columns open, high, low, close
    periods     : ST periods to test; default = range(5, 31)
    multipliers : ST multipliers to test; default = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0]
    conf        : confirmation candles for bounce
    method      : "avg" or "all"
    tol_mult    : band around ST line (ATR multiples)
    top_n       : results to return

    Returns
    -------
    DataFrame with columns: period, multiplier, score, bounce_rate, touch_freq,
    clean_cross, touches, bounces, total_flips, clean_flips
    """
    if periods is None:
        periods = range(5, 31)
    if multipliers is None:
        multipliers = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0]

    rows = []
    for period in periods:
        for mult in multipliers:
            try:
                s = score_supertrend(df, period, mult, conf, method, tol_mult, whipsaw_window)
                rows.append(asdict(s))
            except Exception:
                pass

    if not rows:
        return pd.DataFrame()

    result = (
        pd.DataFrame(rows)
        .sort_values("score", ascending=False)
        .head(top_n)
        .reset_index(drop=True)
    )
    return result
