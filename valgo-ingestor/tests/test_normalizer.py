"""Smoke test: a Kite tick dict normalizes to a valid common.Tick."""
from datetime import datetime, timezone

from ingestor.kite_source import KiteTickSource
from valgo_common.models import TickMode


def test_kite_normalize_quote_mode():
    """A QUOTE-mode Kite tick produces a Tick with ohlc and volume."""
    src = KiteTickSource(api_key="x", access_token="x", on_tick=lambda _: None)
    raw = {
        "instrument_token": 256265,
        "tradingsymbol": "NIFTY 50",
        "last_price": 26512.45,
        "last_traded_quantity": 25,
        "timestamp": datetime.now(timezone.utc),
        "ohlc": {"open": 26498.0, "high": 26545.5, "low": 26492.1, "close": 26505.0},
        "volume_traded": 12345678,
        "average_traded_price": 26508.3,
        "depth": {"buy": [], "sell": []},
    }
    tick = src._normalize(raw)
    assert tick.tradingsymbol == "NIFTY 50"
    assert tick.mode == TickMode.FULL
    assert tick.volume == 12345678
