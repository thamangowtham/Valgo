"""Dispatcher — selects an execution node and forwards the order.

Strategy: simplest possible. Pick a healthy node bound to the order's account_id,
use round-robin, fall back to least-recently-used. Production deployments could
swap this for least-loaded based on in-flight order counts.

In dev / single-node setups, this just calls localhost:8095.
"""
from __future__ import annotations

import asyncio
from itertools import cycle
from typing import Iterator

import httpx

from valgo_common.config import settings
from valgo_common.logging import get_logger
from valgo_common.models import Order, OrderRequest

log = get_logger(__name__)


# Static list for now. Production: pull from DDB nodes table, filter by status=running
# and accountId match, refresh on a timer.
_NODE_URLS = ["http://localhost:8095"]
_node_iter: Iterator[str] = cycle(_NODE_URLS)


async def dispatch_to_node(order: Order, request: OrderRequest) -> None:
    """POST the order to the chosen node. Fire-and-forget (node updates DDB on fill)."""
    url = next(_node_iter)
    payload = {
        "order_id": order.order_id,
        "request": request.model_dump(mode="json"),
    }

    async def _send():
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.post(f"{url}/place", json=payload)
                resp.raise_for_status()
        except Exception as e:
            log.error("dispatch.failed", node=url, order=order.order_id, error=str(e))
            # TODO: mark order as failed_to_dispatch and retry on another node

    # Don't await — return to the router so we can release the request quickly.
    # The execution node updates the order record in DDB independently.
    asyncio.create_task(_send())
