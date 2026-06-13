"""Smoke tests for the canonical models. More importantly, examples for consumers."""
from datetime import datetime, timezone
from decimal import Decimal

from valgo_common.models import (
    DepthLevel, OrderRequest, OrderSide, OrderType, RiskLimits, Tick, TickMode,
)


def test_tick_full_round_trip():
    """A FULL tick from Kite normalizes to a Tick with depth and survives JSON."""
    tick = Tick(
        instrument_token=256265,
        tradingsymbol="NIFTY 50",
        last_price=Decimal("26512.45"),
        timestamp=datetime.now(timezone.utc),
        mode=TickMode.FULL,
        ohlc_open=Decimal("26498.0"),
        depth_buy=[DepthLevel(price=Decimal("26512.40"), quantity=150, orders=2)],
        source="kite",
    )
    payload = tick.model_dump_json()
    revived = Tick.model_validate_json(payload)
    assert revived.tradingsymbol == "NIFTY 50"
    assert revived.depth_buy[0].quantity == 150


def test_order_request_requires_idempotency_key():
    """Idempotency key isn't optional — the router uses it for dedup."""
    req = OrderRequest(
        strategy_id="s1",
        account_id="a1",
        tradingsymbol="NIFTY26500CE",
        side=OrderSide.BUY,
        quantity=50,
        order_type=OrderType.MARKET,
        idempotency_key="strat:s1:tick:1714521825000",
    )
    assert req.product == "MIS"  # default
    assert req.exchange == "NFO"


def test_risk_limits_defaults_match_sebi():
    limits = RiskLimits()
    assert limits.max_orders_per_sec == 10  # SEBI cap
    assert not limits.kill_switch
