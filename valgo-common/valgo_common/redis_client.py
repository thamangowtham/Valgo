"""Async Redis client wrapper. Centralizes key schemas across services.

Key schema:
    tick:ltp:{symbol}              → JSON Tick (LTP only)
    tick:full:{symbol}             → JSON Tick (FULL with depth)
    tick:channel:{symbol}          → pub/sub channel for tick updates
    rate:orders:{account_id}       → token bucket counter (1s TTL)
    risk:daily_pnl:{account_id}    → running daily P&L
    risk:open_positions:{account_id} → set of open position symbols
"""
from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis
from redis.asyncio.client import Redis

from .config import settings
from .models import Tick


_pool: Redis | None = None


def _build_pool() -> Redis:
    return aioredis.from_url(
        f"redis://{settings.redis_host}:{settings.redis_port}",
        password=settings.redis_password or None,
        db=settings.redis_db,
        decode_responses=True,
        max_connections=50,
    )


def get_redis() -> Redis:
    """Singleton Redis connection pool."""
    global _pool
    if _pool is None:
        _pool = _build_pool()
    return _pool


# ============================================================================
# Tick storage / pub-sub
# ============================================================================
def tick_key(symbol: str, mode: str = "full") -> str:
    return f"tick:{mode.lower()}:{symbol}"


def tick_channel(symbol: str) -> str:
    return f"tick:channel:{symbol}"


async def publish_tick(tick: Tick) -> None:
    """Write tick to its key + publish on channel for live subscribers."""
    r = get_redis()
    payload = tick.model_dump_json()
    key = tick_key(tick.tradingsymbol, tick.mode.value)

    pipe = r.pipeline()
    pipe.set(key, payload, ex=300)             # 5min TTL — tick freshness window
    pipe.publish(tick_channel(tick.tradingsymbol), payload)
    await pipe.execute()


async def get_latest_tick(symbol: str, mode: str = "full") -> Tick | None:
    r = get_redis()
    raw = await r.get(tick_key(symbol, mode))
    if not raw:
        return None
    return Tick.model_validate_json(raw)


# ============================================================================
# Generic helpers
# ============================================================================
async def get_json(key: str) -> Any | None:
    r = get_redis()
    raw = await r.get(key)
    return json.loads(raw) if raw else None


async def set_json(key: str, value: Any, ttl: int | None = None) -> None:
    r = get_redis()
    await r.set(key, json.dumps(value, default=str), ex=ttl)


async def close_redis() -> None:
    """Cleanup at process shutdown."""
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
