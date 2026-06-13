"""
Zerodha REST tick source checker — polls historical/minute endpoint.

Usage:
    cd valgo-ingestor
    python -m ingestor.ws_check

Prints the latest 1-min candle for NIFTY 50 and ITC every second for 10 polls.
This is the same source used by KiteRestTickSource in the ingestor.
"""
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

# ── paste fresh enctoken here ──────────────────────────────────────
ENCTOKEN = "PASTE_FRESH_ENCTOKEN_HERE"  # kite.zerodha.com -> F12 -> Application -> Cookies -> enctoken
# ──────────────────────────────────────────────────────────────────

OMS_BASE = "https://kite.zerodha.com/oms"
HEADERS = {"Authorization": f"enctoken {ENCTOKEN}", "X-Kite-Version": "3"}

TOKENS = {256265: "NIFTY 50", 424961: "ITC"}
POLLS = 10

print("=" * 55)
print("  Zerodha REST tick source check")
print("=" * 55)

session = requests.Session()
session.headers.update(HEADERS)

ok = 0
for i in range(1, POLLS + 1):
    ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    to_ = ist.strftime("%Y-%m-%d %H:%M:%S")
    fr_ = (ist - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")

    print(f"\n  Poll #{i}  ({ist.strftime('%H:%M:%S IST')})")
    for token, name in TOKENS.items():
        r = session.get(
            f"{OMS_BASE}/instruments/historical/{token}/minute",
            params={"from": fr_, "to": to_, "continuous": 0, "oi": 0},
            timeout=5,
        )
        if r.status_code == 403:
            print("  [403] enctoken expired.")
            print("  Get a fresh one: kite.zerodha.com -> F12 -> Application -> Cookies -> enctoken")
            sys.exit(1)
        if r.status_code != 200:
            print(f"    {name}: HTTP {r.status_code}")
            continue
        candles = r.json().get("data", {}).get("candles", [])
        if not candles:
            print(f"    {name:12s}: no candles (market closed)")
            continue
        c = candles[-1]
        print(f"    {name:12s}: close={c[4]}  O={c[1]}  H={c[2]}  L={c[3]}  vol={c[5]}  [{c[0][:19]}]")
        ok += 1

    time.sleep(1)

print()
print(f"  Done. {ok} successful price reads across {POLLS} polls.")
if ok == 0:
    print("  0 reads — market is closed or enctoken expired.")
print("=" * 55)
