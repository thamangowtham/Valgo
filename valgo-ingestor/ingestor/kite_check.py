"""
Zerodha Kite — Live & Historical Data Checker
==============================================
Auth:    enctoken from browser (F12 → Application → Cookies → enctoken)
Works:   profile, near-real-time price, historical candles
Skipped: WebSocket ticks (needs Kite Connect OAuth access_token, not browser session)

Install: pip install requests
"""

import sys
from datetime import datetime, timedelta, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import requests

# ── Paste fresh values here each day (from F12 → Application → Cookies) ──
ENCTOKEN = "enKqtIMWvS6la/1SE0rHYqMnUBFoAjlPpdmWv4CZtwOHv/+REub570UKTKmxDwIY6B9FMgyXtsiiM3SmhJ6yYL6vl6i725pO2QvhI4ItVfiTNDRQVlbSOA=="
# ──────────────────────────────────────────────────────────────────────────

OMS_BASE = "https://kite.zerodha.com/oms"   # fixed: was zeraodha
OMS_HEADERS = {
    "Authorization": f"enctoken {ENCTOKEN}",
    "X-Kite-Version": "3",
}

INSTRUMENTS = [
    {"symbol": "ITC",      "exchange": "NSE", "token": 424961,  "yahoo": "ITC.NS"},
    {"symbol": "NIFTY 50", "exchange": "NSE", "token": 256265,  "yahoo": "^NSEI"},
]


# ── helpers ─────────────────────────────────────────────────────
def line(title=""):
    print("\n" + "=" * 60)
    if title:
        print(f"  {title}")
        print("=" * 60)

def ist_now():
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)

def market_open():
    now = ist_now()
    hr  = now.hour + now.minute / 60
    return now.weekday() < 5 and 9.25 <= hr <= 15.5


# ── 1. Profile / auth check ──────────────────────────────────────
def check_profile():
    line("1. AUTH CHECK")
    r = requests.get(f"{OMS_BASE}/user/profile", headers=OMS_HEADERS, timeout=10)
    print(f"  HTTP {r.status_code}", end="  ")
    if r.status_code == 200:
        d = r.json().get("data", {})
        print(f"OK  {d.get('user_name')}  ({d.get('email')})")
        return True
    print(f"FAIL  {r.json().get('message', r.text[:80])}")
    print("\n  Token may have expired. Grab a fresh enctoken from:")
    print("  kite.zerodha.com  -->  F12  -->  Application  -->  Cookies  -->  enctoken")
    return False


# ── 2. Near-real-time price (two sources) ───────────────────────
def check_live_price():
    line("2. CURRENT PRICE  (Zerodha 1-min candle + Yahoo Finance)")

    ist = ist_now()
    from_dt = (ist - timedelta(days=2)).strftime("%Y-%m-%d 09:00:00")
    to_dt   = ist.strftime("%Y-%m-%d %H:%M:%S")

    print(f"  As of: {ist.strftime('%Y-%m-%d %H:%M:%S IST')}  |  "
          f"Market: {'OPEN' if market_open() else 'CLOSED'}\n")

    for inst in INSTRUMENTS:
        print(f"  --- {inst['exchange']}:{inst['symbol']} ---")

        # 5-min candle
        url5 = f"{OMS_BASE}/instruments/historical/{inst['token']}/5minute"
        r5 = requests.get(url5, headers=OMS_HEADERS,
                          params={"from": from_dt, "to": to_dt, "continuous": 0, "oi": 0},
                          timeout=15)
        if r5.status_code == 200:
            candles5 = r5.json().get("data", {}).get("candles", [])
            if candles5:
                last5 = candles5[-1]
                ts5, o5, h5, lo5, c5, v5 = last5[0], last5[1], last5[2], last5[3], last5[4], last5[5]
                ts5_str = ts5 if isinstance(ts5, str) else str(ts5)
                print(f"  [Zerodha 5-min] Last candle @ {ts5_str[:19]}")
                print(f"    LTP (close) : Rs {c5:,.2f}")
                print(f"    O: {o5}  H: {h5}  L: {lo5}  C: {c5}  Vol: {v5:,}")
            else:
                print("  [Zerodha 5-min] No candle data (market closed / holiday?)")
        else:
            print(f"  [Zerodha 5-min] HTTP {r5.status_code} — {r5.json().get('message', '')}")

        # 1-min candle
        url1 = f"{OMS_BASE}/instruments/historical/{inst['token']}/minute"
        r1 = requests.get(url1, headers=OMS_HEADERS,
                          params={"from": from_dt, "to": to_dt, "continuous": 0, "oi": 0},
                          timeout=15)
        if r1.status_code == 200:
            candles1 = r1.json().get("data", {}).get("candles", [])
            if candles1:
                last1 = candles1[-1]
                ts1, c1, v1 = last1[0], last1[4], last1[5]
                ts1_str = ts1 if isinstance(ts1, str) else str(ts1)
                print(f"  [Zerodha 1-min] Last candle @ {ts1_str[:19]}  close: Rs {c1:,.2f}  vol: {v1:,}")
        else:
            print(f"  [Zerodha 1-min] HTTP {r1.status_code}")

        # Yahoo Finance cross-check
        if inst.get("yahoo"):
            try:
                yf = requests.get(
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{inst['yahoo']}",
                    params={"interval": "1m", "range": "1d"},
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=10
                )
                if yf.status_code == 200:
                    meta   = yf.json()["chart"]["result"][0]["meta"]
                    ltp    = meta.get("regularMarketPrice")
                    prev   = meta.get("chartPreviousClose")
                    change = ((ltp - prev) / prev * 100) if ltp and prev else None
                    chg_str = f"  ({change:+.2f}%)" if change is not None else ""
                    print(f"  [Yahoo  ~15min] LTP: Rs {ltp}{chg_str}  prev close: Rs {prev}")
                else:
                    print(f"  [Yahoo ] HTTP {yf.status_code}")
            except Exception as e:
                print(f"  [Yahoo ] Error: {e}")

        print()


# ── 3. Historical candles ────────────────────────────────────────
def check_historical(interval="5minute", days_back=3):
    line(f"3. HISTORICAL DATA  [{interval}, last {days_back} trading days]")
    ist     = ist_now()
    to_dt   = ist.strftime("%Y-%m-%d %H:%M:%S")
    from_dt = (ist - timedelta(days=days_back)).strftime("%Y-%m-%d %H:%M:%S")
    print(f"  Range: {from_dt}  to  {to_dt}\n")

    for inst in INSTRUMENTS:
        url = f"{OMS_BASE}/instruments/historical/{inst['token']}/{interval}"
        r   = requests.get(url, headers=OMS_HEADERS,
                           params={"from": from_dt, "to": to_dt, "continuous": 0, "oi": 0},
                           timeout=15)
        print(f"  [{inst['symbol']:12s}] HTTP {r.status_code}", end="  ")
        if r.status_code == 200:
            candles = r.json().get("data", {}).get("candles", [])
            if candles:
                f, l = candles[0], candles[-1]
                print(f"OK  {len(candles)} candles")
                print(f"     First: {f[0]}  O:{f[1]}  H:{f[2]}  L:{f[3]}  C:{f[4]}  V:{f[5]}")
                print(f"     Last : {l[0]}  O:{l[1]}  H:{l[2]}  L:{l[3]}  C:{l[4]}  V:{l[5]}")
            else:
                print("OK  0 candles returned (holiday / weekend range?)")
        else:
            print(f"FAIL  {r.json().get('message', r.text[:80])}")


# ── 4. WebSocket note ────────────────────────────────────────────
def websocket_note():
    line("4. LIVE TICKS (WebSocket)")
    print("  WebSocket at wss://ws.kite.trade requires a Kite Connect")
    print("  OAuth access_token obtained via api.kite.trade developer flow.")
    print("  Browser session cookies (enctoken) are NOT accepted by the WS server.")
    print()
    print("  To get live tick data, subscribe to Kite Connect:")
    print("  https://developers.kite.trade/  (from Rs 2000/month)")
    print()
    if market_open():
        print("  Alternative: the 1-min candle in section 2 updates every minute.")
    else:
        print("  Market is closed. Historical data in section 3 is available.")


# ── main ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    line("ZERODHA KITE — DATA CHECKER")
    print(f"  Time (IST): {ist_now().strftime('%Y-%m-%d %H:%M:%S')}  |  "
          f"Market: {'OPEN' if market_open() else 'CLOSED'}")

    if not check_profile():
        sys.exit(1)

    check_live_price()
    check_historical(interval="5minute", days_back=3)
    websocket_note()

    line("DONE")
    print("  All checks complete.\n")
