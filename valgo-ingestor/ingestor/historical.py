"""Historical candle fetcher — Zerodha Kite REST.

Pull-based companion to the websocket tick stream. Used by:
    - decision strategies for warm-up / backfill
    - admin tooling for backtests and analytics
    - reconciliation jobs

Supported intervals and max lookback per single Kite call:
    minute    ->  60 days       15minute -> 200 days
    3minute   -> 100 days       30minute -> 200 days
    5minute   -> 100 days       60minute -> 400 days
    10minute  -> 100 days       day      -> 2000 days

Zerodha caps each response at ~2000 candles, so longer date ranges
are auto-chunked.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

from kiteconnect import KiteConnect

INTERVALS = ["minute", "3minute", "5minute", "10minute",
             "15minute", "30minute", "60minute", "day"]

MAX_DAYS: dict[str, int] = {
    "minute":   60,
    "3minute":  100,
    "5minute":  100,
    "10minute": 100,
    "15minute": 200,
    "30minute": 200,
    "60minute": 400,
    "day":      2000,
}

REQUEST_DELAY = 0.4   # ~3 req/sec — Kite's documented limit


def fetch(
    kite: KiteConnect,
    instrument_token: int,
    interval: str,
    from_date: str,
    to_date: str,
    oi: bool = False,
    continuous: bool = False,
) -> list[dict]:
    """Fetch candles between two dates, auto-chunking when range > per-interval limit."""
    if interval not in INTERVALS:
        raise ValueError(f"Invalid interval '{interval}'. Choose from: {INTERVALS}")

    max_days = MAX_DAYS[interval]
    start    = datetime.strptime(from_date, "%Y-%m-%d").date()
    end      = datetime.strptime(to_date,   "%Y-%m-%d").date()
    out: list[dict] = []
    chunk_start = start

    while chunk_start <= end:
        chunk_end = min(chunk_start + timedelta(days=max_days - 1), end)
        try:
            candles = kite.historical_data(
                instrument_token=instrument_token,
                from_date=chunk_start,
                to_date=chunk_end,
                interval=interval,
                continuous=continuous,
                oi=oi,
            )
            out.extend(candles)
        except Exception as e:
            raise RuntimeError(f"historical.fetch failed {chunk_start}->{chunk_end}: {e}") from e

        chunk_start = chunk_end + timedelta(days=1)
        time.sleep(REQUEST_DELAY)

    return out


def fetch_latest(
    kite: KiteConnect,
    instrument_token: int,
    interval: str,
    n_candles: int = 200,
    oi: bool = False,
) -> Any:
    """Last N candles — single API call. Returns a pandas DataFrame.

    Pandas import is local so callers that don't need DataFrames pay no cost.
    Timestamps are tz-converted to Asia/Kolkata and stripped to tz-naive,
    matching the sharemarket reference behaviour.
    """
    import pandas as pd

    if interval not in INTERVALS:
        raise ValueError(f"Invalid interval '{interval}'. Choose from: {INTERVALS}")

    days     = MAX_DAYS[interval]
    from_dt  = datetime.now() - timedelta(days=days)
    to_dt    = datetime.now()

    candles = kite.historical_data(
        instrument_token=instrument_token,
        from_date=from_dt,
        to_date=to_dt,
        interval=interval,
        oi=oi,
    )
    if not candles:
        return pd.DataFrame()

    df = pd.DataFrame(candles[-n_candles:])
    df.rename(columns={"date": "timestamp"}, inplace=True)
    df["timestamp"] = (
        pd.to_datetime(df["timestamp"], utc=True)
        .dt.tz_convert("Asia/Kolkata")
        .dt.tz_localize(None)
    )
    df.reset_index(drop=True, inplace=True)
    return df
