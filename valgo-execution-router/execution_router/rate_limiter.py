"""Redis-backed token bucket rate limiter.

Enforces SEBI's 10 orders/sec cap per API key (account). Atomic INCR on a
1-second TTL key — simple, fast, correct under concurrent execution router
pods.

Why not a leaky bucket / sliding window?
    The SEBI rule is "max 10 orders per second", which is exactly a fixed-window
    counter. Token bucket would allow burst > 10 in a sub-second window if
    tokens accumulated; that's not what the rule says.
"""
from __future__ import annotations

from valgo_common.logging import get_logger
from valgo_common.redis_client import get_redis

log = get_logger(__name__)


class RateLimitExceeded(Exception):
    """Raised when an order would exceed the per-second cap."""


def _bucket_key(account_id: str) -> str:
    return f"rate:orders:{account_id}"


async def acquire(account_id: str, max_per_sec: int = 10) -> None:
    """Reserve one slot. Raises RateLimitExceeded if cap reached.

    Implementation: INCR a key with 1s TTL. The key is the bucket for the
    current second. If INCR returns > cap, deny.

    Note: this is fixed-window — a burst right at second boundary could
    technically allow 2*cap in 1 wall-clock second across two windows.
    For our use case (10/s cap) this is acceptable and matches how brokers
    enforce it server-side.
    """
    r = get_redis()
    key = _bucket_key(account_id)

    # Pipeline: INCR + EXPIRE. EXPIRE only takes effect if key was just created
    # (otherwise we'd reset TTL on every call and it'd never expire).
    pipe = r.pipeline()
    pipe.incr(key)
    pipe.expire(key, 1, nx=True)  # Redis 7+: NX = only if no existing TTL
    count, _ = await pipe.execute()

    if count > max_per_sec:
        log.warning("rate_limit.exceeded", account=account_id, count=count, cap=max_per_sec)
        raise RateLimitExceeded(
            f"Account {account_id}: {count} orders this second (cap {max_per_sec})"
        )


async def current_usage(account_id: str) -> int:
    """Read the current second's count without incrementing. For monitoring."""
    r = get_redis()
    val = await r.get(_bucket_key(account_id))
    return int(val) if val else 0
