"""Strategy backtester — bar-by-bar replay with full indicator capture.

Fetches historical 5-min OHLC from Kite, replays bar-by-bar through the
exact same STPSARConfluence conditions, and writes two CSV files:

  nifty_bars_YYYY-MM-DD.csv   — every bar: OHLC + all indicator values + signal
  nifty_trades_YYYY-MM-DD.csv — only trade events: entry, exit, P&L

Usage
─────
  # Yesterday (default):
  python scripts/backtest.py

  # Specific date:
  python scripts/backtest.py --date 2026-05-29

  # Different symbol:
  python scripts/backtest.py --symbol BANKNIFTY --date 2026-05-29

  # More warmup bars (default 30 calendar days = ~450 5-min bars):
  python scripts/backtest.py --warmup-days 60

Environment variables required:
  KITE_API_KEY, KITE_ACCESS_TOKEN
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import sys
from datetime import date, datetime, timedelta

import pytz
import requests
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    pass

import talib  # noqa: E402 — needs PATH to TA-Lib .so

IST = pytz.timezone("Asia/Kolkata")

# ── Strategy parameters (must match st_psar_confluence.py) ────────────────────
ST_PERIOD   = 10
ST_MULT     = 3.0
SGL_PERIOD  = 21      # EMA period (signal line)
PSAR_AF0    = 0.02
PSAR_MAX_AF = 0.2
ATR_PERIOD  = 14
RSI_PERIOD  = 14
RSI_BUY     = 50
RSI_SELL    = 50
BARS        = 250     # rolling window size
MIN_BARS    = 50      # minimum bars before first signal

# ── Static token map ──────────────────────────────────────────────────────────
_TOKEN = {
    "NIFTY":      256265,
    "BANKNIFTY":  260105,
    "FINNIFTY":   257801,
    "MIDCPNIFTY": 288009,
    "SENSEX":     265,
}


# ── Kite data fetch ───────────────────────────────────────────────────────────

def fetch_ohlc(symbol: str, from_dt: str, to_dt: str,
               api_key: str, access_token: str) -> pd.DataFrame:
    token = _TOKEN[symbol.upper()]
    resp = requests.get(
        f"https://api.kite.trade/instruments/historical/{token}/5minute",
        headers={
            "Authorization": f"token {api_key}:{access_token}",
            "X-Kite-Version": "3",
        },
        params={"from": from_dt, "to": to_dt, "continuous": 0, "oi": 0},
        timeout=30,
    )
    resp.raise_for_status()
    candles = resp.json()["data"]["candles"]

    cols = ["timestamp", "open", "high", "low", "close", "volume"]
    df = pd.DataFrame(candles, columns=cols[:len(candles[0])])
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    for col in ("open", "high", "low", "close"):
        df[col] = df[col].astype(float)
    return df.reset_index(drop=True)


# ── Indicator computation (TA-Lib, same as dataframe_indicators.py) ───────────

def compute_supertrend(high, low, close, period, mult):
    n = len(close)
    atr = talib.ATR(high, low, close, timeperiod=period)
    hl2 = (high + low) / 2.0
    upper = hl2 + mult * atr
    lower = hl2 - mult * atr

    st = np.full(n, np.nan)
    direction = np.zeros(n, dtype=np.int8)

    start = period
    while start < n and np.isnan(atr[start]):
        start += 1
    if start >= n:
        return st, direction

    direction[start] = 1
    st[start] = lower[start]

    for i in range(start + 1, n):
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


# ── Entry / exit conditions (identical to st_psar_confluence.py) ─────────────

def check_buy_entry(ltp, st, sgl, psar, atr, rsi):
    c1 = ltp >= st + atr * 0.2
    c2 = (ltp - st) < atr
    c3 = ltp > psar
    c4 = ltp > sgl or (sgl - ltp) > 5 * atr
    c5 = rsi > RSI_BUY
    return c1 and c2 and c3 and c4 and c5, (c1, c2, c3, c4, c5)

def check_sell_entry(ltp, st, sgl, psar, atr, rsi):
    c1 = ltp <= st - atr * 0.2
    c2 = (st - ltp) < atr
    c3 = ltp < psar
    c4 = ltp < sgl or (ltp - sgl) > 5 * atr
    c5 = rsi < RSI_SELL
    return c1 and c2 and c3 and c4 and c5, (c1, c2, c3, c4, c5)

def check_buy_exit(ltp, st, sgl, psar, atr, prev_mid):
    e1 = ltp < st - atr
    e2 = ltp < st and prev_mid < st
    e3 = ltp < psar
    e4 = ltp < sgl or (sgl - ltp) > 5 * atr
    triggered = [n for n, v in [("e1_below_st_atr", e1), ("e2_two_bar", e2),
                                  ("e3_psar", e3), ("e4_ema", e4)] if v]
    return (e1 or e2 or e3 or e4), triggered

def check_sell_exit(ltp, st, sgl, psar, atr, prev_mid):
    e1 = ltp > st + atr
    e2 = ltp > st and prev_mid > st
    e3 = ltp > psar
    e4 = ltp > sgl or (ltp - sgl) > 5 * atr
    triggered = [n for n, v in [("e1_above_st_atr", e1), ("e2_two_bar", e2),
                                  ("e3_psar", e3), ("e4_ema", e4)] if v]
    return (e1 or e2 or e3 or e4), triggered


# ── Bar-by-bar backtest ───────────────────────────────────────────────────────

def run_backtest(df: pd.DataFrame, target_date: date) -> tuple[list[dict], list[dict]]:
    """Replay all bars. Return (bar_records, trade_records).

    Window rule (identical to live strategy):
      At bar i, the indicator window = the previous 250 closed bars + bar i as the live bar.
      Window size = min(i, BARS) + 1, capped at BARS + 1 = 251 rows.
      Signals only produced for bars on target_date.
    """
    n = len(df)
    high  = df["high"].values.astype(np.float64)
    low   = df["low"].values.astype(np.float64)
    close = df["close"].values.astype(np.float64)
    open_ = df["open"].values.astype(np.float64)

    bar_records   = []
    trade_records = []

    side        = None   # None | "B" | "S"
    entry_price = None
    entry_bar   = None

    # Find index of first bar on target_date
    target_str = target_date.isoformat()
    first_target_idx = next(
        (i for i, ts in enumerate(df["timestamp"])
         if str(ts).startswith(target_str)),
        n
    )

    for i in range(1, n):
        ts = df["timestamp"].iloc[i]
        on_target = str(ts).startswith(target_str)

        # Window: last BARS closed bars (i-BARS .. i-1) + bar i as live bar = 251 rows
        # "Previous 250 candles" at bar i means bars [i-250 .. i-1] closed, bar i is live
        closed_start = max(0, i - BARS)
        w_high  = high[closed_start: i + 1]
        w_low   = low[closed_start:  i + 1]
        w_close = close[closed_start: i + 1]
        w_open  = open_[closed_start: i + 1]

        num_closed = i - closed_start   # closed bars in window (excludes bar i)

        if num_closed < MIN_BARS:
            if on_target:
                bar_records.append(_make_bar_row(ts, df, i, {}, "WARMUP", side))
            continue

        # ── Indicators ────────────────────────────────────────────────────────
        atr_arr = talib.ATR(w_high, w_low, w_close, timeperiod=ATR_PERIOD)
        sgl_arr = talib.EMA(w_close, timeperiod=SGL_PERIOD)
        rsi_arr = talib.RSI(w_close, timeperiod=RSI_PERIOD)

        sar_arr = talib.SAR(w_high, w_low, acceleration=PSAR_AF0, maximum=PSAR_MAX_AF)
        in_long = w_close > sar_arr
        psar    = float(sar_arr[-1]) if not np.isnan(sar_arr[-1]) else math.nan

        st_arr, dir_arr = compute_supertrend(w_high, w_low, w_close, ST_PERIOD, ST_MULT)

        atr = float(atr_arr[-1])
        sgl = float(sgl_arr[-1])
        rsi = float(rsi_arr[-1])
        st  = float(st_arr[-1])
        st_dir = int(dir_arr[-1])   # 1=UP -1=DOWN

        prev_mid = (float(w_high[-2]) + float(w_low[-2])) / 2.0 if len(w_high) >= 2 else math.nan
        ltp = float(w_close[-1])

        if any(math.isnan(v) for v in (st, sgl, psar, atr, rsi)):
            bar_records.append(_make_bar_row(ts, df, i, {}, "NAN", side))
            continue

        inds = dict(st=st, st_dir=st_dir, sgl=sgl, psar=psar, atr=atr, rsi=rsi)

        # ── Decision ──────────────────────────────────────────────────────────
        signal     = "-"
        conditions = {}
        exit_reason = ""

        if side is None:
            ok, conds = check_buy_entry(ltp, st, sgl, psar, atr, rsi)
            conditions = dict(zip(["c1_above_st","c2_not_over","c3_psar","c4_ema","c5_rsi"], conds))
            if ok:
                signal      = "BUY"
                side        = "B"
                entry_price = ltp
                entry_bar   = ts
            else:
                ok, conds = check_sell_entry(ltp, st, sgl, psar, atr, rsi)
                conditions = dict(zip(["c1_below_st","c2_not_over","c3_psar","c4_ema","c5_rsi"], conds))
                if ok:
                    signal      = "SELL"
                    side        = "S"
                    entry_price = ltp
                    entry_bar   = ts

        elif side == "B":
            ok, reasons = check_buy_exit(ltp, st, sgl, psar, atr, prev_mid)
            if ok:
                signal      = "BUY_EXIT"
                exit_reason = "|".join(reasons)
                pnl_pct     = (ltp - entry_price) / entry_price * 100
                trade_records.append({
                    "side":        "LONG",
                    "entry_time":  entry_bar,
                    "entry_price": round(entry_price, 2),
                    "exit_time":   ts,
                    "exit_price":  round(ltp, 2),
                    "pnl_pts":     round(ltp - entry_price, 2),
                    "pnl_pct":     round(pnl_pct, 3),
                    "exit_reason": exit_reason,
                    "result":      "PROFIT" if pnl_pct > 0 else "LOSS",
                })
                side = None; entry_price = None; entry_bar = None

        elif side == "S":
            ok, reasons = check_sell_exit(ltp, st, sgl, psar, atr, prev_mid)
            if ok:
                signal      = "SELL_EXIT"
                exit_reason = "|".join(reasons)
                pnl_pct     = (entry_price - ltp) / entry_price * 100
                trade_records.append({
                    "side":        "SHORT",
                    "entry_time":  entry_bar,
                    "entry_price": round(entry_price, 2),
                    "exit_time":   ts,
                    "exit_price":  round(ltp, 2),
                    "pnl_pts":     round(entry_price - ltp, 2),
                    "pnl_pct":     round(pnl_pct, 3),
                    "exit_reason": exit_reason,
                    "result":      "PROFIT" if pnl_pct > 0 else "LOSS",
                })
                side = None; entry_price = None; entry_bar = None

        if on_target:
            bar_records.append(_make_bar_row(ts, df, i, inds, signal, side, conditions, prev_mid))

    return bar_records, trade_records


def _make_bar_row(ts, df, i, inds, signal, side, conditions=None, prev_mid=None):
    row = {
        "timestamp":  ts,
        "open":       round(float(df["open"].iloc[i]),  2),
        "high":       round(float(df["high"].iloc[i]),  2),
        "low":        round(float(df["low"].iloc[i]),   2),
        "close":      round(float(df["close"].iloc[i]), 2),
        "st":         round(inds.get("st",  math.nan), 2),
        "st_dir":     "UP" if inds.get("st_dir", 0) == 1 else "DOWN",
        "ema21":      round(inds.get("sgl", math.nan), 2),
        "psar":       round(inds.get("psar",math.nan), 2),
        "atr":        round(inds.get("atr", math.nan), 2),
        "rsi":        round(inds.get("rsi", math.nan), 2),
        "prev_mid":   round(prev_mid, 2) if prev_mid and not math.isnan(prev_mid) else "",
        "signal":     signal,
        "position":   {"B": "LONG", "S": "SHORT", None: "FLAT"}.get(side, "FLAT"),
        **(conditions or {}),
    }
    return row


# ── CSV writer ────────────────────────────────────────────────────────────────

def write_csv(records: list[dict], path: str) -> None:
    if not records:
        print(f"  (no records — skipping {path})")
        return
    keys = list(records[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(records)
    print(f"  saved: {path}  ({len(records)} rows)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="Strategy backtester")
    p.add_argument("--symbol",      default="NIFTY",  help="Symbol (NIFTY, BANKNIFTY, ...)")
    p.add_argument("--date",        default="",       help="Target date YYYY-MM-DD (default: yesterday)")
    p.add_argument("--warmup-days", type=int, default=30,
                   help="Calendar days of history before target date for indicator warmup (default 30)")
    p.add_argument("--out-dir",     default=".",      help="Output directory for CSV files")
    args = p.parse_args()

    api_key      = os.getenv("KITE_API_KEY", "")
    access_token = os.getenv("KITE_ACCESS_TOKEN", "")
    if not api_key or not access_token:
        print("ERROR: set KITE_API_KEY and KITE_ACCESS_TOKEN in environment or .env")
        sys.exit(1)

    # Resolve target date
    today_ist = datetime.now(IST).date()
    if args.date:
        target_date = date.fromisoformat(args.date)
    else:
        target_date = today_ist - timedelta(days=1)
        # Skip weekends
        while target_date.weekday() >= 5:
            target_date -= timedelta(days=1)

    warmup_start = target_date - timedelta(days=args.warmup_days)
    # Fetch one extra day into the target date so we have its candles
    fetch_end    = target_date + timedelta(days=1)

    from_dt = warmup_start.strftime("%Y-%m-%d") + " 09:00:00"
    to_dt   = fetch_end.strftime("%Y-%m-%d")    + " 15:35:00"

    symbol = args.symbol.upper()
    print(f"\nBacktest: {symbol}  date={target_date}  warmup={args.warmup_days} days")
    print(f"Fetching {from_dt} to {to_dt} ...")

    df = fetch_ohlc(symbol, from_dt, to_dt, api_key, access_token)
    print(f"Fetched {len(df)} candles total")

    # Filter to market hours only (9:15–15:30 IST)
    df["hour_min"] = df["timestamp"].dt.hour * 60 + df["timestamp"].dt.minute
    df = df[(df["hour_min"] >= 555) & (df["hour_min"] <= 930)].reset_index(drop=True)
    print(f"After market-hours filter: {len(df)} candles")

    print("Running bar-by-bar simulation ...")
    bar_records, trade_records = run_backtest(df, target_date)

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\n=== {target_date} {symbol} Backtest Summary ===")
    print(f"  Bars on target date : {len(bar_records)}")

    signals = [r for r in bar_records if r["signal"] not in ("-", "WARMUP", "NAN")]
    print(f"  Signal bars         : {len(signals)}")
    print(f"  Trades completed    : {len(trade_records)}")

    if trade_records:
        profits = [t for t in trade_records if t["result"] == "PROFIT"]
        losses  = [t for t in trade_records if t["result"] == "LOSS"]
        total_pnl = sum(t["pnl_pts"] for t in trade_records)
        print(f"  Wins / Losses       : {len(profits)} / {len(losses)}")
        print(f"  Total P&L (pts)     : {total_pnl:+.2f}")
        print()
        print(f"  {'Side':<6} {'Entry Time':<20} {'Entry':>8} {'Exit Time':<20} {'Exit':>8} {'P&L pts':>8} {'P&L %':>7} {'Result':<8} {'Exit Reason'}")
        print(f"  {'-'*6} {'-'*20} {'-'*8} {'-'*20} {'-'*8} {'-'*8} {'-'*7} {'-'*8} {'-'*30}")
        for t in trade_records:
            ok = "WIN" if t["result"] == "PROFIT" else "LOSS"
            print(f"  {t['side']:<6} {str(t['entry_time']):<20} {t['entry_price']:>8.2f} "
                  f"{str(t['exit_time']):<20} {t['exit_price']:>8.2f} "
                  f"{t['pnl_pts']:>+8.2f} {t['pnl_pct']:>+7.3f}% "
                  f"{ok:<8} {t['exit_reason']}")

    # ── Write CSVs ────────────────────────────────────────────────────────────
    os.makedirs(args.out_dir, exist_ok=True)
    date_str = target_date.isoformat()
    sym_lo   = symbol.lower()

    print(f"\nWriting CSV files to {args.out_dir}/")
    write_csv(bar_records,   os.path.join(args.out_dir, f"{sym_lo}_bars_{date_str}.csv"))
    write_csv(trade_records, os.path.join(args.out_dir, f"{sym_lo}_trades_{date_str}.csv"))
    print("Done.\n")


if __name__ == "__main__":
    main()
