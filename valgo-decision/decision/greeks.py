"""Option Greeks Calculator.

Pipeline per instrument:
  1. Parse Kite NFO tradingsymbol → underlying, expiry, strike, type
  2. Fetch real-time LTP from Kite /quote
  3. Fetch underlying spot price from Kite /quote (NIFTY / BANKNIFTY index)
  4. Fetch IV from NSE option chain (15-min delay) — fallback: solve from LTP
  5. Calculate Delta, Gamma, Theta, Vega, Rho via Black-Scholes
  6. Return GreekResult per instrument

Usage:
    from decision.greeks import get_greeks

    results = await get_greeks([
        "NIFTY26JUN23600CE",
        "NIFTY26JUN23600PE",
        "BANKNIFTY26JUN51000CE",
    ])
    for sym, g in results.items():
        print(f"{sym}: delta={g.delta:.3f}  gamma={g.gamma:.4f}  "
              f"theta={g.theta:.2f}  vega={g.vega:.2f}  iv={g.iv:.1f}%")
"""
from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from typing import Any

import httpx
import pytz

from valgo_common.config import settings
from valgo_common.logging import get_logger

log = get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")

# ── Constants ─────────────────────────────────────────────────────────────────

RISK_FREE_RATE = 0.065          # RBI repo rate ~6.5%
DIVIDEND_YIELD = 0.0            # Index options: no dividend adjustment

# Kite underlying quote symbols
_UNDERLYING_KITE_SYMBOL: dict[str, str] = {
    "NIFTY":      "NSE:NIFTY 50",
    "BANKNIFTY":  "NSE:NIFTY BANK",
    "FINNIFTY":   "NSE:NIFTY FIN SERVICE",
    "MIDCPNIFTY": "NSE:NIFTY MID SELECT",
    "SENSEX":     "BSE:SENSEX",
}

# NSE option chain symbols
_UNDERLYING_NSE_SYMBOL: dict[str, str] = {
    "NIFTY":      "NIFTY",
    "BANKNIFTY":  "BANKNIFTY",
    "FINNIFTY":   "FINNIFTY",
    "MIDCPNIFTY": "MIDCPNIFTY",
}

_KNOWN_UNDERLYINGS = sorted(_UNDERLYING_KITE_SYMBOL.keys(), key=len, reverse=True)

# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class GreekResult:
    symbol:           str
    underlying:       str
    strike:           float
    expiry:           str          # YYYY-MM-DD
    option_type:      str          # CE | PE
    ltp:              float        # current option premium
    underlying_price: float        # spot price of index
    time_to_expiry:   float        # years (e.g. 0.077 for 28 days)
    iv:               float        # implied volatility %
    delta:            float        # -1 to +1
    gamma:            float        # always positive
    theta:            float        # per day (always negative)
    vega:             float        # per 1% IV change
    rho:              float        # per 1% interest rate change
    intrinsic_value:  float        # max(0, S-K) for CE, max(0, K-S) for PE
    time_value:       float        # ltp - intrinsic_value
    moneyness:        str          # ITM | ATM | OTM

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Symbol parser ─────────────────────────────────────────────────────────────

_MONTH_MAP = {m: i for i, m in enumerate(
    ["JAN","FEB","MAR","APR","MAY","JUN",
     "JUL","AUG","SEP","OCT","NOV","DEC"], start=1)}


def parse_symbol(symbol: str) -> tuple[str, date, float, str]:
    """Parse a Kite NFO tradingsymbol into (underlying, expiry, strike, option_type).

    Handles formats:
      NIFTY26JUN23600CE   → monthly  (UNDERLYING + DD + MMM + STRIKE + TYPE)
      NIFTY2562926600CE   → weekly   (UNDERLYING + YY + M_digit + DD + STRIKE + TYPE)
    """
    s = symbol.upper().strip()

    # Extract option type
    if s.endswith("CE"):
        otype = "CE"
    elif s.endswith("PE"):
        otype = "PE"
    else:
        raise ValueError(f"Symbol must end with CE or PE: {symbol}")
    s = s[:-2]

    # Extract underlying (longest match first)
    underlying = None
    for u in _KNOWN_UNDERLYINGS:
        if s.startswith(u):
            underlying = u
            s = s[len(u):]
            break
    if not underlying:
        raise ValueError(f"Unknown underlying in symbol: {symbol}")

    # Try monthly format: DDMMM + STRIKE
    m = re.match(r'^(\d{2})([A-Z]{3})(\d+)$', s)
    if m:
        day_s, mon_s, strike_s = m.groups()
        if mon_s not in _MONTH_MAP:
            raise ValueError(f"Unknown month '{mon_s}' in {symbol}")
        mon = _MONTH_MAP[mon_s]
        day = int(day_s)
        today = datetime.now(IST).date()
        year = today.year
        try:
            expiry = date(year, mon, day)
        except ValueError:
            raise ValueError(f"Invalid date {day}/{mon}/{year} from {symbol}")
        if expiry < today:
            expiry = date(year + 1, mon, day)
        return underlying, expiry, float(strike_s), otype

    # Try monthly with explicit year: DDMMMYY + STRIKE
    m = re.match(r'^(\d{2})([A-Z]{3})(\d{2})(\d+)$', s)
    if m:
        day_s, mon_s, yr_s, strike_s = m.groups()
        if mon_s not in _MONTH_MAP:
            raise ValueError(f"Unknown month '{mon_s}' in {symbol}")
        expiry = date(2000 + int(yr_s), _MONTH_MAP[mon_s], int(day_s))
        return underlying, expiry, float(strike_s), otype

    # Try weekly NSE format: YY + M(single digit) + DD + STRIKE
    m = re.match(r'^(\d{2})(\d{1})(\d{2})(\d+)$', s)
    if m:
        yr_s, mon_s, day_s, strike_s = m.groups()
        expiry = date(2000 + int(yr_s), int(mon_s), int(day_s))
        return underlying, expiry, float(strike_s), otype

    raise ValueError(f"Cannot parse expiry+strike from '{s}' in '{symbol}'")


def _moneyness(S: float, K: float, otype: str) -> str:
    diff = abs(S - K)
    pct  = diff / S * 100
    if pct < 0.3:
        return "ATM"
    if otype == "CE":
        return "ITM" if S > K else "OTM"
    else:
        return "ITM" if S < K else "OTM"


# ── Black-Scholes engine ──────────────────────────────────────────────────────

def _norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution (no scipy needed)."""
    return 0.5 * math.erfc(-x / math.sqrt(2))

def _norm_pdf(x: float) -> float:
    """Standard normal probability density."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1_d2(S: float, K: float, T: float, r: float, sigma: float) -> tuple[float, float]:
    if T <= 1e-9 or sigma <= 1e-9 or S <= 0 or K <= 0:
        return 0.0, 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return d1, d2


def bs_price(S: float, K: float, T: float, r: float,
             sigma: float, otype: str) -> float:
    """Black-Scholes option price."""
    d1, d2 = _d1_d2(S, K, T, r, sigma)
    disc = math.exp(-r * T)
    if otype == "CE":
        return S * _norm_cdf(d1) - K * disc * _norm_cdf(d2)
    else:
        return K * disc * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def bs_delta(S: float, K: float, T: float, r: float,
             sigma: float, otype: str) -> float:
    d1, _ = _d1_d2(S, K, T, r, sigma)
    return _norm_cdf(d1) if otype == "CE" else _norm_cdf(d1) - 1.0


def bs_gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 1e-9 or sigma <= 1e-9 or S <= 0:
        return 0.0
    d1, _ = _d1_d2(S, K, T, r, sigma)
    return _norm_pdf(d1) / (S * sigma * math.sqrt(T))


def bs_theta(S: float, K: float, T: float, r: float,
             sigma: float, otype: str) -> float:
    """Theta per calendar day."""
    if T <= 1e-9 or sigma <= 1e-9:
        return 0.0
    d1, d2 = _d1_d2(S, K, T, r, sigma)
    disc = math.exp(-r * T)
    decay = -(S * _norm_pdf(d1) * sigma) / (2.0 * math.sqrt(T))
    if otype == "CE":
        theta_yr = decay - r * K * disc * _norm_cdf(d2)
    else:
        theta_yr = decay + r * K * disc * _norm_cdf(-d2)
    return theta_yr / 365.0


def bs_vega(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Vega per 1% change in IV."""
    if T <= 1e-9 or sigma <= 1e-9:
        return 0.0
    d1, _ = _d1_d2(S, K, T, r, sigma)
    return S * _norm_pdf(d1) * math.sqrt(T) / 100.0


def bs_rho(S: float, K: float, T: float, r: float,
           sigma: float, otype: str) -> float:
    """Rho per 1% change in risk-free rate."""
    if T <= 1e-9:
        return 0.0
    d1, d2 = _d1_d2(S, K, T, r, sigma)
    disc = math.exp(-r * T)
    if otype == "CE":
        return K * T * disc * _norm_cdf(d2) / 100.0
    else:
        return -K * T * disc * _norm_cdf(-d2) / 100.0


def solve_iv(market_price: float, S: float, K: float, T: float,
             r: float, otype: str,
             tol: float = 0.01, max_iter: int = 200) -> float:
    """Newton-Raphson implied volatility solver.

    Returns IV as a percentage (e.g. 15.3 for 15.3%).
    Returns nan if solution cannot be found.
    """
    if market_price <= 0 or T <= 1e-9 or S <= 0 or K <= 0:
        return math.nan

    # Intrinsic check — option price below intrinsic → IV = 0
    intrinsic = max(0.0, S - K) if otype == "CE" else max(0.0, K - S)
    if market_price < intrinsic * 0.99:
        return math.nan

    sigma = 0.30  # 30% initial guess
    for _ in range(max_iter):
        price = bs_price(S, K, T, r, sigma, otype)
        v = bs_vega(S, K, T, r, sigma) * 100.0  # vega in raw (not /100)
        if abs(v) < 1e-10:
            break
        diff = price - market_price
        if abs(diff) < tol:
            return round(sigma * 100.0, 4)
        sigma -= diff / v
        if sigma <= 1e-4:
            sigma = 1e-4
        if sigma > 10.0:   # > 1000% → no solution
            return math.nan

    # Final check
    if abs(bs_price(S, K, T, r, sigma, otype) - market_price) < tol * 10:
        return round(sigma * 100.0, 4)
    return math.nan


# ── NSE India client (option chain with IV) ───────────────────────────────────

class NSEClient:
    """Fetches option chain from NSE India with proper session/cookie handling.

    NSE blocks bare requests — must first hit the homepage to get cookies,
    then call the API. Session is reused for 25 minutes before refresh.
    """

    _BASE = "https://www.nseindia.com"
    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept":          "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer":         "https://www.nseindia.com/option-chain",
        "Connection":      "keep-alive",
    }

    def __init__(self) -> None:
        self._client:      httpx.AsyncClient | None = None
        self._valid_until: datetime | None = None
        self._chain_cache: dict[str, tuple[datetime, dict]] = {}

    async def _ensure_session(self) -> None:
        now = datetime.now()
        if (self._client is not None
                and self._valid_until
                and now < self._valid_until):
            return
        if self._client:
            try:
                await self._client.aclose()
            except Exception:
                pass
        self._client = httpx.AsyncClient(
            headers=self._HEADERS,
            follow_redirects=True,
            timeout=30.0,
        )
        # NSE requires hitting the homepage first to get session cookies
        try:
            await self._client.get(self._BASE + "/")
            await asyncio.sleep(1.0)
            await self._client.get(self._BASE + "/option-chain")
            await asyncio.sleep(0.5)
        except Exception as e:
            log.warning("nse.session_warmup_failed", error=str(e))
        self._valid_until = now + timedelta(minutes=25)

    async def fetch_option_chain(self, symbol: str) -> dict:
        """Fetch full option chain for a given underlying. Results cached 60s."""
        sym = symbol.upper()
        now = datetime.now()
        if sym in self._chain_cache:
            cached_at, data = self._chain_cache[sym]
            if (now - cached_at).seconds < 60:
                return data

        await self._ensure_session()
        url = f"{self._BASE}/api/option-chain-indices"
        try:
            resp = await self._client.get(url, params={"symbol": sym})
            resp.raise_for_status()
            data = resp.json()
            self._chain_cache[sym] = (now, data)
            log.info("nse.option_chain_fetched", symbol=sym,
                     records=len(data.get("records", {}).get("data", [])))
            return data
        except Exception as e:
            log.error("nse.option_chain_failed", symbol=sym, error=str(e))
            return {}

    def extract_iv(self, chain: dict, strike: float,
                   otype: str, expiry: date) -> float | None:
        """Extract IV for a specific strike/expiry from a chain response."""
        records = chain.get("records", {}).get("data", [])
        expiry_str = expiry.strftime("%-d-%b-%Y").upper()   # e.g. "26-JUN-2026"
        for rec in records:
            if abs(rec.get("strikePrice", -1) - strike) > 0.5:
                continue
            opt = rec.get(otype, {})
            rec_expiry = str(opt.get("expiryDate", "")).upper()
            if rec_expiry != expiry_str:
                # Try alternate format match
                try:
                    re_date = datetime.strptime(rec_expiry, "%d-%b-%Y").date()
                    if re_date != expiry:
                        continue
                except ValueError:
                    continue
            iv = opt.get("impliedVolatility")
            if iv and float(iv) > 0:
                return float(iv)
        return None

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


# ── Kite quote client ─────────────────────────────────────────────────────────

class KiteQuoteClient:
    """Fetches real-time LTP and underlying price from Kite REST API."""

    _BASE = "https://api.kite.trade"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"token {settings.kite_api_key}:{settings.kite_access_token}",
            "X-Kite-Version": "3",
        }

    async def fetch_quotes(self, symbols: list[str]) -> dict[str, dict]:
        """Fetch quotes for a list of exchange:tradingsymbol strings.

        Returns dict of symbol → {last_price, volume, oi, ...}
        """
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{self._BASE}/quote",
                headers=self._headers(),
                params={"i": symbols},
            )
            resp.raise_for_status()
            return resp.json().get("data", {})

    async def fetch_underlying_price(self, underlying: str) -> float | None:
        """Fetch spot price for NIFTY / BANKNIFTY / etc."""
        kite_sym = _UNDERLYING_KITE_SYMBOL.get(underlying)
        if not kite_sym:
            log.warning("greeks.unknown_underlying", underlying=underlying)
            return None
        quotes = await self.fetch_quotes([kite_sym])
        q = quotes.get(kite_sym, {})
        ltp = q.get("last_price")
        return float(ltp) if ltp else None


# ── Greeks engine (ties everything together) ──────────────────────────────────

_nse_client  = NSEClient()
_kite_client = KiteQuoteClient()


async def get_greeks(
    instruments: list[str],
    risk_free_rate: float = RISK_FREE_RATE,
) -> dict[str, GreekResult]:
    """Calculate all option Greeks for a list of Kite NFO tradingsymbols.

    Args:
        instruments: e.g. ["NIFTY26JUN23600CE", "BANKNIFTY26JUN51000PE"]
        risk_free_rate: annual risk-free rate as decimal (default 0.065 = 6.5%)

    Returns:
        dict[tradingsymbol → GreekResult]
    """
    if not instruments:
        return {}

    # ── Step 1: Parse all symbols ─────────────────────────────────────────────
    parsed: dict[str, tuple] = {}   # symbol → (underlying, expiry, strike, otype)
    for sym in instruments:
        try:
            parsed[sym] = parse_symbol(sym)
        except Exception as e:
            log.error("greeks.parse_failed", symbol=sym, error=str(e))

    if not parsed:
        return {}

    # ── Step 2: Fetch option LTPs from Kite ───────────────────────────────────
    kite_symbols = [f"NFO:{sym}" for sym in parsed]
    try:
        option_quotes = await _kite_client.fetch_quotes(kite_symbols)
    except Exception as e:
        log.error("greeks.kite_quote_failed", error=str(e))
        option_quotes = {}

    # ── Step 3: Fetch underlying spot prices (one per unique underlying) ───────
    underlyings = set(v[0] for v in parsed.values())
    spot_prices: dict[str, float] = {}
    spot_tasks = {u: _kite_client.fetch_underlying_price(u) for u in underlyings}
    for u, task in spot_tasks.items():
        try:
            price = await task
            if price:
                spot_prices[u] = price
        except Exception as e:
            log.error("greeks.spot_fetch_failed", underlying=u, error=str(e))

    # ── Step 4: Fetch NSE option chains for IV (one chain per underlying) ──────
    nse_chains: dict[str, dict] = {}
    for u in underlyings:
        nse_sym = _UNDERLYING_NSE_SYMBOL.get(u)
        if nse_sym:
            try:
                nse_chains[u] = await _nse_client.fetch_option_chain(nse_sym)
            except Exception as e:
                log.warning("greeks.nse_chain_failed", underlying=u, error=str(e))
                nse_chains[u] = {}

    # ── Step 5: Calculate Greeks per instrument ───────────────────────────────
    today    = datetime.now(IST).date()
    results: dict[str, GreekResult] = {}

    for sym, (underlying, expiry, strike, otype) in parsed.items():
        # LTP
        q_key = f"NFO:{sym}"
        ltp   = float(option_quotes.get(q_key, {}).get("last_price", 0) or 0)

        # Spot price
        S = spot_prices.get(underlying)
        if S is None:
            log.warning("greeks.no_spot_price", symbol=sym, underlying=underlying)
            continue
        if ltp <= 0:
            log.warning("greeks.zero_ltp", symbol=sym)
            continue

        # Time to expiry
        days_left = (expiry - today).days
        T = max(days_left / 365.0, 1.0 / 365.0)   # minimum 1 day

        # IV — try NSE first, fallback to Newton-Raphson from LTP
        iv_pct: float = math.nan
        chain = nse_chains.get(underlying, {})
        if chain:
            nse_iv = _nse_client.extract_iv(chain, strike, otype, expiry)
            if nse_iv:
                iv_pct = nse_iv
                log.info("greeks.iv_from_nse", symbol=sym, iv=iv_pct)

        if math.isnan(iv_pct):
            iv_pct = solve_iv(ltp, S, strike, T, risk_free_rate, otype)
            if not math.isnan(iv_pct):
                log.info("greeks.iv_from_bs", symbol=sym, iv=iv_pct)
            else:
                log.warning("greeks.iv_failed", symbol=sym, ltp=ltp, S=S, K=strike, T=T)
                iv_pct = 15.0  # fallback to 15% to still return a result

        sigma = iv_pct / 100.0

        # Compute all Greeks
        delta = bs_delta(S, strike, T, risk_free_rate, sigma, otype)
        gamma = bs_gamma(S, strike, T, risk_free_rate, sigma)
        theta = bs_theta(S, strike, T, risk_free_rate, sigma, otype)
        vega  = bs_vega(S, strike, T, risk_free_rate, sigma)
        rho   = bs_rho(S, strike, T, risk_free_rate, sigma, otype)

        # Intrinsic and time value
        intrinsic = max(0.0, S - strike) if otype == "CE" else max(0.0, strike - S)
        time_val  = max(0.0, ltp - intrinsic)

        results[sym] = GreekResult(
            symbol=sym,
            underlying=underlying,
            strike=strike,
            expiry=expiry.isoformat(),
            option_type=otype,
            ltp=round(ltp, 2),
            underlying_price=round(S, 2),
            time_to_expiry=round(T, 6),
            iv=round(iv_pct, 2),
            delta=round(delta, 4),
            gamma=round(gamma, 6),
            theta=round(theta, 4),
            vega=round(vega, 4),
            rho=round(rho, 4),
            intrinsic_value=round(intrinsic, 2),
            time_value=round(time_val, 2),
            moneyness=_moneyness(S, strike, otype),
        )

        log.info(
            "greeks.computed",
            symbol=sym, ltp=ltp, S=S, K=strike,
            T_days=days_left, iv=iv_pct,
            delta=round(delta, 4), gamma=round(gamma, 6),
            theta=round(theta, 4), vega=round(vega, 4),
        )

    return results


async def close() -> None:
    """Close NSE session. Call on shutdown."""
    await _nse_client.close()
