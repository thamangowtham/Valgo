"""Daily instrument dump — Zerodha publishes this CSV every morning ~08:30 IST.

Endpoint is public (no auth). We cache the file locally for the day to avoid
hammering Kite's CDN — production callers should also cache the parsed result
in DynamoDB so the auth-refresh Lambda's morning warm-up populates it once.

Helpers cover the lookups that strategies and the execution router actually
need: futures discovery, options by strike + CE/PE, nearest expiry, and ATM
strike rounding.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime
from pathlib import Path

import requests

from valgo_common.config import settings
from valgo_common.logging import get_logger

log = get_logger(__name__)

INSTRUMENTS_URL = "https://api.kite.trade/instruments"
DEFAULT_CACHE = Path("/tmp/valgo_instruments_cache.csv") if not settings.is_local \
    else Path.cwd() / "instruments_cache.csv"


def download(force: bool = False, cache_path: Path | None = None) -> list[dict]:
    """Download today's instruments dump (cached on disk)."""
    path = cache_path or DEFAULT_CACHE

    if not force and path.exists():
        mtime = datetime.fromtimestamp(path.stat().st_mtime).date()
        if mtime == date.today():
            log.info("instruments.cache_hit", path=str(path))
            return _parse(path.read_text())

    log.info("instruments.downloading", url=INSTRUMENTS_URL)
    resp = requests.get(INSTRUMENTS_URL, timeout=30)
    resp.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(resp.text)
    log.info("instruments.cached", bytes=len(resp.text), path=str(path))
    return _parse(resp.text)


def _parse(csv_text: str) -> list[dict]:
    rows: list[dict] = []
    reader = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        for field in ("instrument_token", "exchange_token", "strike", "lot_size", "tick_size"):
            try:
                row[field] = float(row[field]) if "." in str(row[field]) else int(row[field])
            except (ValueError, KeyError):
                pass
        rows.append(row)
    log.info("instruments.parsed", count=len(rows))
    return rows


def find_futures(instruments: list[dict], name: str, expiry: str | None = None) -> list[dict]:
    """NFO futures by underlying name. Returns sorted by expiry ascending."""
    results = [
        r for r in instruments
        if r.get("name", "").upper() == name.upper()
        and r.get("instrument_type") == "FUT"
        and r.get("exchange") == "NFO"
    ]
    results.sort(key=lambda r: r.get("expiry", ""))
    if expiry:
        results = [r for r in results if r.get("expiry") == expiry]
    return results


def find_options(
    instruments: list[dict],
    name: str,
    strike: float,
    option_type: str,    # 'CE' or 'PE'
    expiry: str | None = None,
) -> list[dict]:
    """NFO options by name + strike + type. Sorted by expiry ascending."""
    results = [
        r for r in instruments
        if r.get("name", "").upper() == name.upper()
        and r.get("instrument_type") == option_type.upper()
        and r.get("exchange") == "NFO"
        and r.get("strike") == float(strike)
    ]
    results.sort(key=lambda r: r.get("expiry", ""))
    if expiry:
        results = [r for r in results if r.get("expiry") == expiry]
    return results


def find_mcx_futures(instruments: list[dict], name: str) -> list[dict]:
    """MCX futures (commodities — CRUDEOIL, GOLD, SILVER, etc) by underlying name."""
    today = date.today().isoformat()
    results = [
        r for r in instruments
        if r.get("name", "").upper() == name.upper()
        and r.get("exchange") == "MCX"
        and r.get("instrument_type") == "FUT"
        and r.get("expiry", "") >= today
    ]
    results.sort(key=lambda r: r.get("expiry", ""))
    return results


def nearest_expiry(instruments: list[dict], name: str) -> str:
    futs = find_futures(instruments, name)
    if not futs:
        raise ValueError(f"No futures found for '{name}'")
    return futs[0]["expiry"]


def atm_strike(spot_price: float, strike_interval: int) -> float:
    """Round spot to nearest strike_interval (e.g. 50 for NIFTY, 100 for BANKNIFTY)."""
    return round(round(spot_price / strike_interval) * strike_interval, 2)
