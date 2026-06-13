"""Module 1 — get_greeks(instrument)

Enhanced Greeks engine using mibian (Black-Scholes) + Newton-Raphson IV solver.
Accepts an instrument token or tradingsymbol, fetches live market data from Kite,
and returns a structured dict of all option Greeks.

Usage:
    from decision.greeks_engine import get_greeks, GreeksAnalyzer

    # Single instrument
    result = await get_greeks("NIFTY26JUN23600CE")
    print(result["delta"], result["theta"], result["iv"])

    # Multi-instrument scanner (Module 2)
    analyzer = GreeksAnalyzer()
    watchlist = await analyzer.scan(
        underlyings=["NIFTY", "BANKNIFTY"],
        filters={"gamma": (">", 0.003), "iv": (">", 15)}
    )
"""
from __future__ import annotations

import asyncio
import io
import math
import re
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from typing import Any, Callable

import httpx
import mibian
import numpy as np
import pandas as pd
import pytz

from valgo_common.config import settings
from valgo_common.logging import get_logger

log = get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")

# ── Constants ─────────────────────────────────────────────────────────────────

RISK_FREE_RATE  = 6.5          # % — RBI repo rate
DIVIDEND_YIELD  = 0.0          # Index options: no dividend
IV_LOWER_BOUND  = 0.5          # % minimum IV
IV_UPPER_BOUND  = 500.0        # % maximum IV
IV_TOLERANCE    = 0.01         # Rupees — Newton-Raphson convergence

_KNOWN_UNDERLYINGS = sorted(
    ["BANKNIFTY","FINNIFTY","MIDCPNIFTY","NIFTY","SENSEX"],
    key=len, reverse=True
)
_MONTH_MAP = {m: i for i, m in enumerate(
    ["JAN","FEB","MAR","APR","MAY","JUN",
     "JUL","AUG","SEP","OCT","NOV","DEC"], start=1
)}
_KITE_UNDERLYING: dict[str, str] = {
    "NIFTY":      "NSE:NIFTY 50",
    "BANKNIFTY":  "NSE:NIFTY BANK",
    "FINNIFTY":   "NSE:NIFTY FIN SERVICE",
    "MIDCPNIFTY": "NSE:NIFTY MID SELECT",
}
_STRIKE_INTERVAL: dict[str, int] = {
    "NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50, "MIDCPNIFTY": 25
}


# ── Output payload ────────────────────────────────────────────────────────────

@dataclass
class GreeksPayload:
    """Structured output for get_greeks()."""
    # Identification
    instrument:       str
    underlying:       str
    strike:           float
    expiry:           str
    option_type:      str         # CE | PE
    moneyness:        str         # ITM | ATM | OTM

    # Market data
    ltp:              float
    underlying_price: float
    days_to_expiry:   int
    time_to_expiry:   float       # years

    # Volatility
    iv:               float       # Implied Volatility %

    # Greeks (Black-Scholes via mibian)
    delta:            float       # Δ  -1 to +1
    gamma:            float       # Γ  always positive
    theta:            float       # Θ  per calendar day (negative)
    vega:             float       # ν  per 1% IV change
    rho:              float       # ρ  per 1% rate change

    # Value breakdown
    intrinsic_value:  float
    time_value:       float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def __str__(self) -> str:
        return (
            f"{self.instrument:<28} LTP={self.ltp:>8.2f} "
            f"IV={self.iv:>6.2f}% "
            f"Δ={self.delta:>+7.4f} "
            f"Γ={self.gamma:>8.6f} "
            f"Θ={self.theta:>8.2f} "
            f"ν={self.vega:>7.2f} "
            f"ρ={self.rho:>7.2f} "
            f"[{self.moneyness}]"
        )


# ── Symbol parser ─────────────────────────────────────────────────────────────

def parse_symbol(symbol: str) -> tuple[str, date, float, str]:
    """Parse Kite NFO tradingsymbol → (underlying, expiry, strike, option_type)."""
    s = symbol.upper().strip()
    otype = "CE" if s.endswith("CE") else ("PE" if s.endswith("PE") else None)
    if not otype:
        raise ValueError(f"Symbol must end CE or PE: {symbol}")
    s = s[:-2]

    underlying = next((u for u in _KNOWN_UNDERLYINGS if s.startswith(u)), None)
    if not underlying:
        raise ValueError(f"Unknown underlying in {symbol}")
    s = s[len(underlying):]

    # Monthly: DDMMM + STRIKE
    m = re.match(r'^(\d{2})([A-Z]{3})(\d+)$', s)
    if m:
        day_s, mon_s, strike_s = m.groups()
        if mon_s not in _MONTH_MAP:
            raise ValueError(f"Unknown month {mon_s}")
        today = datetime.now(IST).date()
        year  = today.year
        expiry = date(year, _MONTH_MAP[mon_s], int(day_s))
        if expiry < today:
            expiry = date(year + 1, _MONTH_MAP[mon_s], int(day_s))
        return underlying, expiry, float(strike_s), otype

    # Monthly with year: DDMMMYY + STRIKE
    m = re.match(r'^(\d{2})([A-Z]{3})(\d{2})(\d+)$', s)
    if m:
        day_s, mon_s, yr_s, strike_s = m.groups()
        return underlying, date(2000+int(yr_s), _MONTH_MAP[mon_s], int(day_s)), float(strike_s), otype

    # Weekly numeric: YY + M_digit + DD + STRIKE
    m = re.match(r'^(\d{2})(\d{1})(\d{2})(\d+)$', s)
    if m:
        yr_s, mon_s, day_s, strike_s = m.groups()
        return underlying, date(2000+int(yr_s), int(mon_s), int(day_s)), float(strike_s), otype

    raise ValueError(f"Cannot parse expiry/strike from '{s}' in '{symbol}'")


# ── Kite REST helpers ─────────────────────────────────────────────────────────

def _kite_headers() -> dict[str, str]:
    return {
        "Authorization": f"token {settings.kite_api_key}:{settings.kite_access_token}",
        "X-Kite-Version": "3",
    }


async def _kite_quote(client: httpx.AsyncClient,
                      symbols: list[str]) -> dict[str, dict]:
    resp = await client.get(
        "https://api.kite.trade/quote",
        headers=_kite_headers(),
        params={"i": symbols},
        timeout=15.0,
    )
    resp.raise_for_status()
    return resp.json().get("data", {})


async def _kite_underlying_price(client: httpx.AsyncClient,
                                  underlying: str) -> float | None:
    sym = _KITE_UNDERLYING.get(underlying)
    if not sym:
        return None
    q = await _kite_quote(client, [sym])
    ltp = q.get(sym, {}).get("last_price")
    return float(ltp) if ltp else None


# ── IV solver (Newton-Raphson + mibian) ───────────────────────────────────────

def _solve_iv_mibian(market_price: float, S: float, K: float,
                     T_days: int, r: float, otype: str) -> float:
    """Solve IV using mibian's Black-Scholes with Newton-Raphson iteration."""
    if market_price <= 0 or T_days <= 0 or S <= 0 or K <= 0:
        return math.nan

    intrinsic = max(0.0, S - K) if otype == "CE" else max(0.0, K - S)
    if market_price < intrinsic * 0.99:
        return math.nan

    sigma = 30.0   # start at 30% IV
    for _ in range(200):
        try:
            bs = mibian.BS([S, K, r, T_days], volatility=sigma)
            theo = bs.callPrice if otype == "CE" else bs.putPrice
            vega = bs.vega  # per 1% IV

            diff = theo - market_price
            if abs(diff) < IV_TOLERANCE:
                return round(sigma, 4)

            if abs(vega) < 1e-8:
                break

            sigma -= diff / vega
            sigma = max(IV_LOWER_BOUND, min(sigma, IV_UPPER_BOUND))
        except Exception:
            break

    return round(sigma, 4) if IV_LOWER_BOUND < sigma < IV_UPPER_BOUND else math.nan


# ── Core Greeks calculator ────────────────────────────────────────────────────

def _compute_greeks_mibian(S: float, K: float, T_days: int,
                            r: float, iv: float,
                            otype: str) -> dict[str, float]:
    """Compute all Greeks via mibian Black-Scholes."""
    try:
        bs = mibian.BS([S, K, r, T_days], volatility=iv)
        if otype == "CE":
            return {
                "delta": round(bs.callDelta,  4),
                "gamma": round(bs.gamma,       6),
                "theta": round(bs.callTheta / 365.0, 4),  # per day
                "vega":  round(bs.vega / 100.0, 4),        # per 1% IV
                "rho":   round(bs.callRho / 100.0, 4),     # per 1% rate
            }
        else:
            return {
                "delta": round(bs.putDelta,  4),
                "gamma": round(bs.gamma,      6),
                "theta": round(bs.putTheta / 365.0, 4),
                "vega":  round(bs.vega / 100.0, 4),
                "rho":   round(bs.putRho / 100.0, 4),
            }
    except Exception as e:
        log.warning("greeks_engine.mibian_failed", error=str(e))
        return {k: math.nan for k in ("delta","gamma","theta","vega","rho")}


def _moneyness(S: float, K: float, otype: str) -> str:
    pct = abs(S - K) / S * 100
    if pct < 0.3:
        return "ATM"
    if otype == "CE":
        return "ITM" if S > K else "OTM"
    return "ITM" if S < K else "OTM"


# ── Module 1: get_greeks() ────────────────────────────────────────────────────

async def get_greeks(
    instrument: str,
    risk_free_rate: float = RISK_FREE_RATE,
    client: httpx.AsyncClient | None = None,
) -> GreeksPayload | None:
    """
    Core function — Module 1.

    Accepts a Kite NFO tradingsymbol (e.g. "NIFTY26JUN23600CE").
    Fetches live LTP + underlying spot from Kite.
    Solves IV via Newton-Raphson using mibian Black-Scholes.
    Returns a GreeksPayload with Δ, Γ, Θ, ν, ρ.

    Args:
        instrument:     Kite NFO tradingsymbol
        risk_free_rate: Annual rate % (default 6.5)
        client:         Optional shared httpx client for batch calls
    Returns:
        GreeksPayload or None if data unavailable
    """
    should_close = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=20.0)

    try:
        # Parse symbol
        underlying, expiry, strike, otype = parse_symbol(instrument)

        today    = datetime.now(IST).date()
        T_days   = max((expiry - today).days, 1)
        T_years  = T_days / 365.0

        # Fetch option LTP + underlying spot in parallel
        opt_sym  = f"NFO:{instrument}"
        spot_sym = _KITE_UNDERLYING.get(underlying, "")

        symbols_to_fetch = [sym for sym in [opt_sym, spot_sym] if sym]
        quotes = await _kite_quote(client, symbols_to_fetch)

        ltp = float(quotes.get(opt_sym,  {}).get("last_price", 0) or 0)
        S   = float(quotes.get(spot_sym, {}).get("last_price", 0) or 0)

        if ltp <= 0 or S <= 0:
            log.warning("greeks_engine.zero_price",
                        instrument=instrument, ltp=ltp, spot=S)
            return None

        # Solve IV
        iv = _solve_iv_mibian(ltp, S, strike, T_days, risk_free_rate, otype)
        if math.isnan(iv):
            log.warning("greeks_engine.iv_failed", instrument=instrument)
            iv = 15.0  # fallback

        # Greeks via mibian
        g = _compute_greeks_mibian(S, strike, T_days, risk_free_rate, iv, otype)

        # Intrinsic / time value
        intrinsic = max(0.0, S - strike) if otype == "CE" else max(0.0, strike - S)
        time_val  = max(0.0, ltp - intrinsic)

        payload = GreeksPayload(
            instrument=instrument,
            underlying=underlying,
            strike=strike,
            expiry=expiry.isoformat(),
            option_type=otype,
            moneyness=_moneyness(S, strike, otype),
            ltp=round(ltp, 2),
            underlying_price=round(S, 2),
            days_to_expiry=T_days,
            time_to_expiry=round(T_years, 6),
            iv=round(iv, 2),
            delta=g["delta"],
            gamma=g["gamma"],
            theta=g["theta"],
            vega=g["vega"],
            rho=g["rho"],
            intrinsic_value=round(intrinsic, 2),
            time_value=round(time_val, 2),
        )

        log.info("greeks_engine.computed", instrument=instrument,
                 ltp=ltp, iv=iv, delta=g["delta"], theta=g["theta"])
        return payload

    except Exception as e:
        log.error("greeks_engine.failed", instrument=instrument, error=str(e))
        return None
    finally:
        if should_close:
            await client.aclose()


# ── Module 2: GreeksAnalyzer (multi-instrument scanner) ──────────────────────

@dataclass
class FilterRule:
    """A single filter condition: field OP value (e.g. gamma > 0.003)."""
    field:    str
    operator: str   # ">" | "<" | ">=" | "<=" | "==" | "between"
    value:    float | tuple[float, float]

    def matches(self, payload: GreeksPayload) -> bool:
        v = getattr(payload, self.field, None)
        if v is None or math.isnan(v):
            return False
        if self.operator == ">":    return v > self.value
        if self.operator == "<":    return v < self.value
        if self.operator == ">=":   return v >= self.value
        if self.operator == "<=":   return v <= self.value
        if self.operator == "==":   return abs(v - self.value) < 0.001
        if self.operator == "between":
            lo, hi = self.value
            return lo <= v <= hi
        return False


class GreeksAnalyzer:
    """
    Module 2 — Multi-Instrument Greeks Analyzer.

    Scans a basket of underlying symbols, loops their F&O chains,
    computes Greeks for every active contract, then applies user-defined
    filters to return a live actionable watchlist.

    Usage:
        analyzer = GreeksAnalyzer()
        watchlist = await analyzer.scan(
            underlyings=["NIFTY", "BANKNIFTY"],
            filters=[
                FilterRule("gamma", ">",  0.003),
                FilterRule("iv",    ">",  15.0),
                FilterRule("delta", "between", (0.3, 0.7)),
            ],
            option_types=["CE", "PE"],
            max_strikes_per_side=5,
        )
        for item in watchlist:
            print(item)
    """

    def __init__(self) -> None:
        self._nfo_cache: pd.DataFrame | None = None
        self._nfo_date:  date | None = None

    async def scan(
        self,
        underlyings:        list[str],
        filters:            list[FilterRule] | None = None,
        option_types:       list[str] | None = None,
        max_strikes_per_side: int = 10,
        risk_free_rate:     float = RISK_FREE_RATE,
    ) -> list[GreeksPayload]:
        """
        Scan all underlyings, compute Greeks, apply filters.

        Args:
            underlyings:          List of underlying symbols (NIFTY, BANKNIFTY, ...)
            filters:              FilterRule conditions; all must pass
            option_types:         ["CE"], ["PE"], or ["CE","PE"]
            max_strikes_per_side: ATM ± N strikes to scan
            risk_free_rate:       Annual rate %

        Returns:
            Sorted list of GreeksPayload matching all filters
        """
        if option_types is None:
            option_types = ["CE", "PE"]
        filters = filters or []

        async with httpx.AsyncClient(timeout=20.0) as client:
            nfo_df = await self._load_nfo(client)
            if nfo_df.empty:
                return []

            tasks = []
            for underlying in underlyings:
                instruments = self._resolve_chain(
                    nfo_df, underlying, option_types,
                    max_strikes_per_side, client
                )
                tasks.append(instruments)

            instrument_lists = await asyncio.gather(*tasks)
            all_instruments  = [i for lst in instrument_lists for i in lst]

        log.info("greeks_analyzer.scanning",
                 count=len(all_instruments), underlyings=underlyings)

        # Compute Greeks in parallel batches of 10
        results: list[GreeksPayload] = []
        async with httpx.AsyncClient(timeout=20.0) as client:
            batch_size = 10
            for i in range(0, len(all_instruments), batch_size):
                batch   = all_instruments[i: i + batch_size]
                payloads = await asyncio.gather(
                    *[get_greeks(sym, risk_free_rate, client) for sym in batch],
                    return_exceptions=True,
                )
                for p in payloads:
                    if isinstance(p, GreeksPayload):
                        if all(f.matches(p) for f in filters):
                            results.append(p)
                await asyncio.sleep(0.1)   # rate-limit guard

        # Sort by IV descending (most active first)
        results.sort(key=lambda x: x.iv, reverse=True)

        log.info("greeks_analyzer.done",
                 scanned=len(all_instruments), matched=len(results))
        return results

    async def _load_nfo(self, client: httpx.AsyncClient) -> pd.DataFrame:
        today = datetime.now(IST).date()
        if self._nfo_cache is not None and self._nfo_date == today:
            return self._nfo_cache
        try:
            resp = await client.get(
                "https://api.kite.trade/instruments/NFO",
                headers=_kite_headers(),
            )
            resp.raise_for_status()
            df = pd.read_csv(io.StringIO(resp.text))
            df["expiry"] = pd.to_datetime(df["expiry"], errors="coerce").dt.date
            df["strike"] = pd.to_numeric(df["strike"], errors="coerce")
            self._nfo_cache = df.dropna(subset=["expiry","strike"])
            self._nfo_date  = today
            return self._nfo_cache
        except Exception as e:
            log.error("greeks_analyzer.nfo_load_failed", error=str(e))
            return pd.DataFrame()

    async def _resolve_chain(
        self,
        nfo_df:   pd.DataFrame,
        underlying: str,
        option_types: list[str],
        n_strikes:  int,
        client:     httpx.AsyncClient,
    ) -> list[str]:
        """Return tradingsymbols for ATM±N strikes of the nearest expiry."""
        spot = await _kite_underlying_price(client, underlying)
        if not spot:
            return []

        interval = _STRIKE_INTERVAL.get(underlying, 50)
        atm      = round(spot / interval) * interval
        today    = datetime.now(IST).date()

        instruments: list[str] = []
        for otype in option_types:
            expiry_df = nfo_df[
                (nfo_df["name"]            == underlying) &
                (nfo_df["instrument_type"] == otype) &
                (nfo_df["expiry"]          >= today)
            ]
            if expiry_df.empty:
                continue
            nearest_expiry = expiry_df["expiry"].min()
            chain = expiry_df[expiry_df["expiry"] == nearest_expiry]

            strikes = sorted(chain["strike"].unique())
            atm_idx = min(range(len(strikes)),
                          key=lambda i: abs(strikes[i] - atm))
            lo = max(0,            atm_idx - n_strikes)
            hi = min(len(strikes), atm_idx + n_strikes + 1)

            for strike in strikes[lo:hi]:
                rows = chain[chain["strike"] == strike]
                if not rows.empty:
                    instruments.append(str(rows.iloc[0]["tradingsymbol"]))

        return instruments

    def print_watchlist(self, watchlist: list[GreeksPayload]) -> None:
        """Pretty-print the filtered watchlist."""
        if not watchlist:
            print("  No contracts matched the filter criteria.")
            return
        print(f"\n{'Instrument':<28} {'LTP':>7} {'IV%':>6} "
              f"{'Delta':>7} {'Gamma':>9} {'Theta':>8} "
              f"{'Vega':>7} {'Moneyness':>9}")
        print("  " + "-" * 90)
        for p in watchlist:
            print(f"  {p.instrument:<28} {p.ltp:>7.2f} {p.iv:>6.2f}% "
                  f"{p.delta:>+7.4f} {p.gamma:>9.6f} {p.theta:>8.2f} "
                  f"{p.vega:>7.2f} {p.moneyness:>9}")
