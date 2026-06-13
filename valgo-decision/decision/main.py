"""Decision engine entrypoint.

For each active strategy:
    - Instantiate it
    - Subscribe to its required instruments via Redis pub/sub
    - Route incoming ticks to the strategy's on_tick

Webhook signals (TradingView etc) come from the webhook handler via a separate
Redis channel and are dispatched to the strategy's on_signal.

Run:  python -m services.decision.main
"""
from __future__ import annotations

import asyncio
import importlib
import signal
from typing import Any

from valgo_common.config import settings
from valgo_common.dynamodb import get_config
from valgo_common.logging import get_logger, setup_logging
from valgo_common.models import Strategy, Tick
from valgo_common.redis_client import close_redis, get_redis, tick_channel

from .strategies.base import StrategyBase

log = get_logger(__name__)


# Map of strategy_class_name → fully-qualified import path
STRATEGY_REGISTRY: dict[str, str] = {
    "ema_crossover":      "decision.strategies.ema_crossover.EmaCrossoverStrategy",
    # Reference port — bar polling. NOT for production (>1s decision latency).
    "mcx_multi":          "decision.strategies.mcx_multi.MCXMultiCommodityStrategy",
    # Tick-driven, hot-path-optimized. Use this in production.
    "mcx_multi_tick":     "decision.strategies.mcx_multi_tick.MCXMultiCommodityTickStrategy",
    # SuperTrend + PSAR Confluence — 5-min bar-driven, two-sided.
    "st_psar_confluence": "decision.strategies.st_psar_confluence.STPSARConfluenceStrategy",
    "breakout_options":   "decision.strategies.breakout_options.BreakoutOptionsStrategy",
}


async def load_strategies() -> list[StrategyBase]:
    """Load active strategies from DynamoDB config and instantiate them."""
    config = await get_config("strategies") or {}
    raw_list = config.get("strategies", [])

    strategies: list[StrategyBase] = []
    for raw in raw_list:
        if not raw.get("active", True):
            continue
        try:
            cfg = Strategy.model_validate(raw)
            cls_name = raw.get("class_name", "ema_crossover")
            cls = _import_class(STRATEGY_REGISTRY[cls_name])
            strategies.append(cls(cfg))
        except Exception as e:
            log.error("decision.strategy_load_failed", strategy=raw.get("id"), error=str(e))
    return strategies


def _import_class(path: str) -> Any:
    module_path, _, class_name = path.rpartition(".")
    return getattr(importlib.import_module(module_path), class_name)


async def run_strategy_loop(strategy: StrategyBase) -> None:
    """Subscribe to the strategy's instruments and route ticks."""
    r = get_redis()
    pubsub = r.pubsub()
    channels = [tick_channel(sym) for sym in strategy.required_instruments]
    if not channels:
        log.warning("decision.strategy_no_instruments", strategy=strategy.id)
        return
    await pubsub.subscribe(*channels)
    log.info("decision.strategy_started", strategy=strategy.id, channels=len(channels))

    try:
        async for msg in pubsub.listen():
            if msg["type"] != "message":
                continue
            try:
                tick = Tick.model_validate_json(msg["data"])
                await strategy.on_tick(tick)
            except Exception as e:
                log.error("decision.tick_handling_error", strategy=strategy.id, error=str(e))
    finally:
        await pubsub.unsubscribe()
        await pubsub.close()
        await strategy.close()


async def main() -> None:
    setup_logging()
    log.info("decision.starting", env=settings.env)

    strategies = await load_strategies()
    log.info("decision.strategies_loaded", count=len(strategies))
    if not strategies:
        log.warning("decision.no_active_strategies — nothing to do")
        return

    # Pre-load historical candles so indicators are ready from tick 1
    for s in strategies:
        if hasattr(s, "preload_history"):
            try:
                await s.preload_history()
            except Exception as e:
                log.warning("decision.preload_failed", strategy=s.id, error=str(e))

    tasks = [asyncio.create_task(run_strategy_loop(s)) for s in strategies]

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)
    await stop_event.wait()

    log.info("decision.shutting_down")
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await close_redis()


if __name__ == "__main__":
    asyncio.run(main())
