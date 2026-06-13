"""Test and verify Greeks against NSE option chain values.

Usage:
  python scripts/test_greeks.py
  python scripts/test_greeks.py --symbol NIFTY --strike 23600
  python scripts/test_greeks.py --instruments NIFTY26JUN23600CE NIFTY26JUN23600PE
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    pass

from decision.greeks import get_greeks, GreekResult, _UNDERLYING_KITE_SYMBOL
from valgo_common.config import settings


def print_greeks(results: dict[str, GreekResult]) -> None:
    if not results:
        print("  No results returned.")
        return

    print()
    print(f"  {'Symbol':<28} {'LTP':>7} {'Spot':>8} {'Expiry':<12} {'T(days)':>7} "
          f"{'IV%':>6} {'Money':>5}")
    print("  " + "-" * 82)
    for sym, g in results.items():
        t_days = round(g.time_to_expiry * 365)
        print(f"  {sym:<28} {g.ltp:>7.2f} {g.underlying_price:>8.2f} "
              f"{g.expiry:<12} {t_days:>7} {g.iv:>6.2f}% {g.moneyness:>5}")

    print()
    print(f"  {'Symbol':<28} {'Delta':>7} {'Gamma':>8} {'Theta':>8} {'Vega':>7} {'Rho':>7}")
    print("  " + "-" * 70)
    for sym, g in results.items():
        print(f"  {sym:<28} {g.delta:>7.4f} {g.gamma:>8.6f} {g.theta:>8.4f} "
              f"{g.vega:>7.4f} {g.rho:>7.4f}")

    print()
    print(f"  {'Symbol':<28} {'Intrinsic':>10} {'Time Val':>9} {'LTP':>7}")
    print("  " + "-" * 57)
    for sym, g in results.items():
        print(f"  {sym:<28} {g.intrinsic_value:>10.2f} {g.time_value:>9.2f} {g.ltp:>7.2f}")


def build_test_instruments(symbol: str, strike: int, expiry_code: str) -> list[str]:
    """Build CE + PE symbol pair for a given strike."""
    return [
        f"{symbol}{expiry_code}{strike}CE",
        f"{symbol}{expiry_code}{strike}PE",
    ]


async def main():
    p = argparse.ArgumentParser(description="Greeks calculator test")
    p.add_argument("--instruments", nargs="+",
                   help="Full instrument names e.g. NIFTY26JUN23600CE")
    p.add_argument("--symbol",  default="NIFTY",
                   help="Underlying (NIFTY, BANKNIFTY)")
    p.add_argument("--strike",  type=int, default=0,
                   help="Strike price (0 = auto detect ATM)")
    p.add_argument("--expiry",  default="",
                   help="Expiry code e.g. 26JUN (default: nearest monthly)")
    args = p.parse_args()

    if not settings.kite_api_key or not settings.kite_access_token:
        print("ERROR: KITE_API_KEY and KITE_ACCESS_TOKEN must be set in environment.")
        sys.exit(1)

    # ── Determine instruments to test ─────────────────────────────────────────
    if args.instruments:
        instruments = args.instruments
    else:
        from decision.greeks import _kite_client
        import httpx

        # Auto-detect ATM strike from live spot price
        underlying = args.symbol.upper()
        spot = await _kite_client.fetch_underlying_price(underlying)
        if spot is None:
            print(f"ERROR: Could not fetch spot price for {underlying}.")
            sys.exit(1)

        interval = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50}.get(underlying, 50)
        atm = round(spot / interval) * interval

        if args.strike:
            strike = args.strike
        else:
            strike = atm

        # Use nearest expiry if not specified
        expiry = args.expiry or "26JUN"  # fallback — user should specify

        instruments = [
            f"{underlying}{expiry}{strike}CE",
            f"{underlying}{expiry}{strike}PE",
            f"{underlying}{expiry}{strike + interval}CE",    # OTM1 CE
            f"{underlying}{expiry}{strike - interval}PE",    # OTM1 PE
        ]
        print(f"  Spot: {spot:.2f}   ATM: {atm}   Testing strikes: {strike}, {strike+interval}")

    print(f"\nFetching Greeks for {len(instruments)} instruments ...")
    print(f"  {instruments}")

    results = await get_greeks(instruments)

    print(f"\n{'='*85}")
    print(f"  GREEKS REPORT")
    print(f"{'='*85}")
    print_greeks(results)

    # ── Verification hints ────────────────────────────────────────────────────
    print()
    print("=" * 85)
    print("  VERIFICATION — check these values on NSE option chain:")
    print("  https://www.nseindia.com/option-chain")
    print()
    print("  Expected ranges for ATM options:")
    print("    Delta  CE: 0.45-0.55     PE: -0.45 to -0.55")
    print("    Gamma:     0.002-0.010   (same for CE and PE)")
    print("    Theta:    -10 to -30     (per day, always negative)")
    print("    Vega:      5 to 20       (per 1% IV change)")
    print("    IV:        12% to 25%    (typical for NIFTY)")
    print()

    # ── Put-Call Parity Check ─────────────────────────────────────────────────
    ce_list = {sym: g for sym, g in results.items() if g.option_type == "CE"}
    pe_list = {sym: g for sym, g in results.items() if g.option_type == "PE"}

    print("  PUT-CALL PARITY CHECK (Delta CE + Delta PE should be close to 0):")
    for ce_sym, ce in ce_list.items():
        # Find matching PE
        for pe_sym, pe in pe_list.items():
            if pe.strike == ce.strike:
                parity = ce.delta + pe.delta   # should be ~0 (slight offset due to rates)
                ok = "OK" if abs(parity) < 0.05 else "MISMATCH"
                print(f"    {ce_sym} delta={ce.delta:+.4f}  "
                      f"{pe_sym} delta={pe.delta:+.4f}  "
                      f"sum={parity:+.4f}  {ok}")
                break

    print()


if __name__ == "__main__":
    asyncio.run(main())
