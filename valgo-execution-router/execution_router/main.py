"""Execution router — internal-only FastAPI service.

Sits behind an internal NLB. Decision engine and webhook handler POST OrderRequest
here. Pipeline:
    1. Idempotency check (DDB conditional write on idempotency_key)
    2. Risk gate (kill switch, daily loss, position count, notional)
    3. Rate limit (10/sec per account_id, SEBI cap)
    4. Persist Order to DDB with status=PENDING
    5. Dispatch to least-busy execution node
    6. Return 202 Accepted with order_id

Failures here MUST NOT leak duplicate orders. Idempotency is enforced
via DDB's ConditionExpression.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI, HTTPException

from valgo_common.config import settings
from valgo_common.dynamodb import put_order
from valgo_common.logging import get_logger, setup_logging
from valgo_common.models import (
    Order, OrderRequest, OrderStatus, RiskLimits,
)
from valgo_common.redis_client import close_redis, get_redis

from . import risk
from .dispatcher import dispatch_to_node
from .rate_limiter import RateLimitExceeded, acquire as rate_acquire

log = get_logger(__name__)


# ============================================================================
# Lifespan / app
# ============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    log.info("router.starting", env=settings.env)
    yield
    log.info("router.shutting_down")
    await close_redis()


app = FastAPI(title="Valgo Execution Router", lifespan=lifespan)


# ============================================================================
# Endpoints
# ============================================================================
@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/orders", status_code=202)
async def submit_order(req: OrderRequest) -> dict:
    """Accept order from decision engine. Risk-check, rate-limit, dispatch."""
    log.info("router.order_received", strategy=req.strategy_id, symbol=req.tradingsymbol)

    # 1. Idempotency — short-circuit if we've seen this key before
    if await _is_duplicate(req.idempotency_key):
        log.info("router.duplicate_idempotency_key", key=req.idempotency_key)
        raise HTTPException(status_code=409, detail="duplicate idempotency_key")

    # 2. Risk gates
    limits = await _load_risk_limits()
    result = await risk.check(req, limits)
    if not result.allowed:
        log.warning("router.risk_denied", reason=result.reason, symbol=req.tradingsymbol)
        await _record_rejection(req, result.reason or "risk_denied")
        raise HTTPException(status_code=403, detail={"reason": result.reason})

    # 3. Rate limit
    try:
        await rate_acquire(req.account_id, max_per_sec=limits.max_orders_per_sec)
    except RateLimitExceeded as e:
        log.warning("router.rate_limited", account=req.account_id)
        await _record_rejection(req, str(e))
        raise HTTPException(status_code=429, detail=str(e)) from e

    # 4. Persist + 5. Dispatch
    order_id = str(uuid4())
    now = datetime.now(timezone.utc)
    order = Order(
        order_id=order_id,
        strategy_id=req.strategy_id,
        account_id=req.account_id,
        tradingsymbol=req.tradingsymbol,
        side=req.side,
        quantity=req.quantity,
        order_type=req.order_type,
        price=req.price,
        status=OrderStatus.PENDING,
        placed_at=now,
        updated_at=now,
        idempotency_key=req.idempotency_key,
    )
    await put_order(order.model_dump(mode="json"))
    await _mark_idempotency_seen(req.idempotency_key)

    # Fire-and-forget dispatch (the node returns the broker_order_id async)
    await dispatch_to_node(order, req)

    return {"order_id": order_id, "status": "submitted"}


# ============================================================================
# Helpers
# ============================================================================
async def _is_duplicate(idempotency_key: str) -> bool:
    r = get_redis()
    seen = await r.get(f"idem:{idempotency_key}")
    return seen is not None


async def _mark_idempotency_seen(idempotency_key: str) -> None:
    r = get_redis()
    # 24-hour window — same key seen again within a day is a dupe
    await r.set(f"idem:{idempotency_key}", "1", ex=24 * 3600)


async def _load_risk_limits() -> RiskLimits:
    """Load current risk config. Cached briefly in Redis to avoid DDB round-trip on every order."""
    r = get_redis()
    cached = await r.get("risk:limits")
    if cached:
        return RiskLimits.model_validate_json(cached)

    # Fall back to defaults; admin API writes the actual limits to this key
    limits = RiskLimits()
    await r.set("risk:limits", limits.model_dump_json(), ex=10)
    return limits


async def _record_rejection(req: OrderRequest, reason: str) -> None:
    """Record rejected orders in audit log even though they never hit the broker."""
    from valgo_common.dynamodb import append_audit_event
    await append_audit_event({
        "event_id": str(uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": "order_rejected_pretrade",
        "actor": req.strategy_id,
        "payload": {**req.model_dump(mode="json"), "reason": reason},
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("execution_router.main:app", host="0.0.0.0", port=8090)
