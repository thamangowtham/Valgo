"""Failover orchestrator.

Holds N TickSource instances ordered by priority. Tracks the active source.
Promotes next-priority backup when active source reports failed/disconnected
beyond the configured threshold.

Strategies / decision engine consume from Redis — they don't see this layer.
"""
from __future__ import annotations

import asyncio
import time
from typing import Protocol

from valgo_common.logging import get_logger
from valgo_common.models import Tick

log = get_logger(__name__)


class TickSource(Protocol):
    name: str
    provider_id: str

    async def start(self, instrument_tokens: list[int]) -> None: ...
    async def stop(self) -> None: ...
    async def update_subscription(self, instrument_tokens: list[int]) -> None: ...


class FailoverManager:
    """Manages a chain of TickSource instances ordered by priority.

    Args:
        sources: Ordered list (primary, backup-1, backup-2, ...).
        on_tick: Callback invoked with every tick from the *active* source.
        threshold_ms: Switch to next source if active reports failed and
                      doesn't recover within this window.
    """

    def __init__(
        self,
        sources: list[TickSource],
        on_tick,
        threshold_ms: int = 10_000,
    ) -> None:
        if not sources:
            raise ValueError("FailoverManager needs at least one source")
        self._sources = sources
        self._on_tick = on_tick
        self._threshold_ms = threshold_ms

        self._active_index = 0
        self._instrument_tokens: list[int] = []
        self._failed_at: float | None = None  # monotonic timestamp
        self._failover_task: asyncio.Task | None = None

    @property
    def active(self) -> TickSource:
        return self._sources[self._active_index]

    async def start(self, instrument_tokens: list[int]) -> None:
        self._instrument_tokens = list(instrument_tokens)
        await self._activate(0)

    async def stop(self) -> None:
        if self._failover_task:
            self._failover_task.cancel()
        for s in self._sources:
            try:
                await s.stop()
            except Exception as e:
                log.warning("failover.stop_error", source=s.name, error=str(e))

    async def update_subscription(self, instrument_tokens: list[int]) -> None:
        self._instrument_tokens = list(instrument_tokens)
        await self.active.update_subscription(instrument_tokens)

    # ------------------------------------------------------------------
    # Internal: the active source's tick callback hooks here
    # ------------------------------------------------------------------
    async def _on_tick_internal(self, tick: Tick) -> None:
        # Tag with the active provider id (already done by source, defensive)
        await self._dispatch(tick)

    async def _on_status_change(self, status: str) -> None:
        log.info("failover.status_change", source=self.active.name, status=status)
        if status in ("connected", "reconnecting"):
            self._failed_at = None
            return

        if status in ("disconnected", "failed"):
            if self._failed_at is None:
                self._failed_at = time.monotonic()

            # If we've already given up, promote immediately
            if status == "failed":
                await self._maybe_promote(force=True)
            else:
                # Schedule a check after threshold_ms
                if self._failover_task is None or self._failover_task.done():
                    self._failover_task = asyncio.create_task(self._check_threshold())

    async def _check_threshold(self) -> None:
        await asyncio.sleep(self._threshold_ms / 1000)
        await self._maybe_promote()

    async def _maybe_promote(self, force: bool = False) -> None:
        if self._failed_at is None and not force:
            return
        if self._active_index + 1 >= len(self._sources):
            log.error("failover.no_more_backups", active=self.active.name)
            return

        old = self.active.name
        new_index = self._active_index + 1
        log.warning("failover.promoting", from_=old, to=self._sources[new_index].name)

        try:
            await self.active.stop()
        except Exception as e:
            log.warning("failover.old_source_stop_error", error=str(e))

        await self._activate(new_index)

    async def _activate(self, index: int) -> None:
        self._active_index = index
        source = self._sources[index]
        # Wire callbacks — assumes source instances accept these via their constructor;
        # here we just call start. In real wiring, the source is constructed with
        # on_tick and on_status_change pointing at our internal handlers.
        log.info("failover.activating", source=source.name, instruments=len(self._instrument_tokens))
        await source.start(self._instrument_tokens)
        self._failed_at = None

    async def _dispatch(self, tick: Tick) -> None:
        result = self._on_tick(tick)
        if asyncio.iscoroutine(result):
            await result
