"""Pre-trade risk checks.

Every order from the decision engine flows through these gates before being
dispatched to an execution node. Any gate can deny.

Order of checks matters: cheapest first (kill switch is a single Redis GET).
"""
from __future__ import annotations

from decimal import Decimal

from valgo_common.logging import get_logger
from valgo_common.models import OrderRequest, RiskCheckResult, RiskLimits
from valgo_common.redis_client import get_redis

log = get_logger(__name__)


# Redis keys
def _kill_switch_key() -> str:
    return "risk:kill_switch"


def _daily_pnl_key(account_id: str) -> str:
    return f"risk:daily_pnl:{account_id}"


def _open_positions_key(account_id: str) -> str:
    return f"risk:open_positions:{account_id}"


async def check(order: OrderRequest, limits: RiskLimits) -> RiskCheckResult:
    """Run all gates. Returns first denial or 'allowed' if all pass."""
    r = get_redis()

    # 1. Kill switch — global master off
    kill = await r.get(_kill_switch_key())
    if kill == "1" or limits.kill_switch:
        return RiskCheckResult(allowed=False, reason="kill_switch_engaged")

    # 2. Daily loss
    daily_pnl_raw = await r.get(_daily_pnl_key(order.account_id))
    if daily_pnl_raw is not None:
        daily_pnl = Decimal(daily_pnl_raw)
        if daily_pnl <= -limits.max_daily_loss:
            log.warning("risk.daily_loss_breached", account=order.account_id, pnl=str(daily_pnl))
            return RiskCheckResult(
                allowed=False,
                reason=f"daily_loss_breached:{daily_pnl}>={-limits.max_daily_loss}",
            )

    # 3. Open positions count
    open_count = await r.scard(_open_positions_key(order.account_id))
    # Adding to an existing position doesn't increase count, but be conservative
    if open_count >= limits.max_open_positions:
        is_member = await r.sismember(_open_positions_key(order.account_id), order.tradingsymbol)
        if not is_member:
            return RiskCheckResult(
                allowed=False,
                reason=f"max_open_positions:{open_count}>={limits.max_open_positions}",
            )

    # 4. Notional value (qty × price)
    if order.price is not None:
        notional = order.price * order.quantity
        if notional > limits.max_position_value:
            return RiskCheckResult(
                allowed=False,
                reason=f"position_notional_too_large:{notional}>{limits.max_position_value}",
            )

    return RiskCheckResult(allowed=True)


async def engage_kill_switch() -> None:
    r = get_redis()
    await r.set(_kill_switch_key(), "1")
    log.warning("risk.kill_switch_engaged_globally")


async def release_kill_switch() -> None:
    r = get_redis()
    await r.delete(_kill_switch_key())
    log.info("risk.kill_switch_released")


async def update_daily_pnl(account_id: str, delta: Decimal) -> Decimal:
    r = get_redis()
    new_value = await r.incrbyfloat(_daily_pnl_key(account_id), float(delta))
    # Keep until end of day — set TTL to next midnight (simplified: 24h)
    await r.expire(_daily_pnl_key(account_id), 24 * 3600, nx=True)
    return Decimal(str(new_value))
