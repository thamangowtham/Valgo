"""CLI runner for the MA / SuperTrend optimizer.

Fetches historical 5-min data from Kite, runs the optimizer, and prints
the top-ranked parameters.

Usage
─────
  # From the valgo-decision directory:
  python scripts/run_optimizer.py --symbol NIFTY
  python scripts/run_optimizer.py --symbol BANKNIFTY --method all --top 5
  python scripts/run_optimizer.py --symbol NIFTY --days 30 --interval 5minute

Environment variables required (or in .env):
  KITE_API_KEY, KITE_ACCESS_TOKEN
"""
from __future__ import annotations

import argparse
import sys
import os
from datetime import datetime, timedelta

# Allow running from the repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
import pandas as pd

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    pass

from decision.ma_optimizer import find_best_ma, find_best_supertrend

# Static NSE token map
_TOKEN = {
    "NIFTY":      256265,
    "BANKNIFTY":  260105,
    "FINNIFTY":   257801,
    "MIDCPNIFTY": 288009,
    "SENSEX":     265,
}


def fetch_kite_ohlc(
    symbol: str,
    interval: str,
    days: int,
    api_key: str,
    access_token: str,
) -> pd.DataFrame:
    token = _TOKEN.get(symbol.upper())
    if token is None:
        raise ValueError(f"Unknown symbol '{symbol}'. Add its Kite token to _TOKEN.")

    now     = datetime.now()
    from_dt = (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    to_dt   = now.strftime("%Y-%m-%d %H:%M:%S")

    url = f"https://api.kite.trade/instruments/historical/{token}/{interval}"
    resp = requests.get(
        url,
        headers={
            "Authorization": f"token {api_key}:{access_token}",
            "X-Kite-Version": "3",
        },
        params={"from": from_dt, "to": to_dt, "continuous": 0, "oi": 0},
        timeout=30,
    )
    resp.raise_for_status()
    candles = resp.json()["data"]["candles"]
    # Drop the last (possibly forming) candle
    if candles:
        candles = candles[:-1]

    cols = ["timestamp", "open", "high", "low", "close", "volume", "oi"]
    df = pd.DataFrame(candles, columns=cols[:len(candles[0])] if candles else cols)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    for col in ("open", "high", "low", "close"):
        df[col] = df[col].astype(float)
    print(f"Fetched {len(df)} candles for {symbol} ({interval})")
    return df


def _fmt(df: pd.DataFrame) -> str:
    return df.to_string(index=True, float_format=lambda x: f"{x:.4f}")


def main() -> None:
    p = argparse.ArgumentParser(description="MA / SuperTrend parameter optimizer")
    p.add_argument("--symbol",   default="NIFTY",    help="NSE symbol (NIFTY, BANKNIFTY, ...)")
    p.add_argument("--interval", default="5minute",  help="Kite interval (5minute, 15minute, ...)")
    p.add_argument("--days",     type=int, default=90, help="Calendar days of history to fetch")
    p.add_argument("--method",   default="avg",      choices=["avg", "all"],
                   help="Bounce method: avg=trending, all=ranging")
    p.add_argument("--top",      type=int, default=10, help="Top N results to show")
    p.add_argument("--conf",     type=int, default=3,  help="Confirmation candles for bounce")
    p.add_argument("--tol",      type=float, default=0.10,
                   help="Tolerance band as ATR multiplier (default 0.10)")
    args = p.parse_args()

    api_key      = os.getenv("KITE_API_KEY", "")
    access_token = os.getenv("KITE_ACCESS_TOKEN", "")
    if not api_key or not access_token:
        print("ERROR: set KITE_API_KEY and KITE_ACCESS_TOKEN in environment or .env")
        sys.exit(1)

    df = fetch_kite_ohlc(args.symbol, args.interval, args.days, api_key, access_token)

    print(f"\n{'='*60}")
    print(f"  TOP {args.top} MOVING AVERAGES  —  {args.symbol} {args.interval}")
    print(f"  method={args.method}  conf={args.conf}  tol_atr={args.tol}")
    print(f"{'='*60}")
    ma_results = find_best_ma(
        df, conf=args.conf, method=args.method,
        tol_mult=args.tol, top_n=args.top,
    )
    print(_fmt(ma_results[["period", "ma_type", "score",
                            "bounce_rate", "touch_freq", "clean_cross",
                            "touches", "bounces"]]))

    print(f"\n{'='*60}")
    print(f"  TOP {args.top} SUPERTREND COMBOS  —  {args.symbol} {args.interval}")
    print(f"  method={args.method}  conf={args.conf}  tol_atr={args.tol}")
    print(f"{'='*60}")
    st_results = find_best_supertrend(
        df, conf=args.conf, method=args.method,
        tol_mult=args.tol, top_n=args.top,
    )
    print(_fmt(st_results[["period", "multiplier", "score",
                            "bounce_rate", "touch_freq", "clean_cross",
                            "touches", "bounces"]]))

    # Recommendation
    print(f"\n{'='*60}")
    if not ma_results.empty:
        best_ma = ma_results.iloc[0]
        print(f"  BEST MA  -> {best_ma['ma_type']}({int(best_ma['period'])})  score={best_ma['score']:.1f}")
    if not st_results.empty:
        best_st = st_results.iloc[0]
        print(f"  BEST ST  -> ST({int(best_st['period'])}, {best_st['multiplier']})  score={best_st['score']:.1f}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
