"""Base class for all decision strategies.

A strategy:
    - Declares which instruments it needs ticks for
    - Receives ticks via on_tick (called from the decision engine's tick consumer)
    - Emits OrderRequests via the provided execution_router client when entry/exit fires
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import uuid4

import httpx

from valgo_common.config import settings
from valgo_common.logging import get_logger
from valgo_common.models import OrderRequest, OrderSide, Strategy, Tick

log = get_logger(__name__)


class StrategyBase(ABC):
    """Each strategy subclass implements on_tick(). Most also override on_signal() for webhook signals."""

    def __init__(self, config: Strategy) -> None:
        self.config = config
        self._http = httpx.AsyncClient(timeout=2.0)

    @property
    def id(self) -> str:
        return self.config.id

    @property
    def required_instruments(self) -> list[str]:
        return self.config.instruments

    @abstractmethod
    async def on_tick(self, tick: Tick) -> None:
        """Called for each tick of any subscribed instrument. Implement entry/exit logic here."""

    async def on_signal(self, signal: dict) -> None:
        """Optional: handle external webhook signals (e.g., TradingView). Default is no-op."""

    # ----------------------------------------------------------------------
    # Helper for subclasses: emit an order
    # ----------------------------------------------------------------------
    async def emit_order(
        self,
        tradingsymbol: str,
        side: OrderSide,
        quantity: int,
        price: float | None = None,
    ) -> str:
        """Send OrderRequest to execution router. Returns server-assigned order_id."""
        req = OrderRequest(
            strategy_id=self.id,
            account_id=self.config.account_id,
            tradingsymbol=tradingsymbol,
            side=side,
            quantity=quantity,
            price=price,
            idempotency_key=str(uuid4()),
            tag=f"strat:{self.id}",
        )
        log.info("strategy.emitting_order", strategy=self.id, symbol=tradingsymbol, side=side.value)
        resp = await self._http.post(
            f"{settings.execution_router_url}/orders",
            json=req.model_dump(mode="json"),
        )
        resp.raise_for_status()
        return resp.json()["order_id"]

    async def close(self) -> None:
        await self._http.aclose()
