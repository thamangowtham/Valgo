"""Execution node — Shoonya-facing service.

This is the ONLY service that calls the Shoonya broker API.
The execution-router POSTs an order to /place; the node:
    1. Calls ShoonyaBrokerAdapter.place_order()
    2. Updates the Order record in DynamoDB with norenordno + SUBMITTED status
    3. Returns {broker_order_id, status} to the router

Run:  python -m execution_node.main
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException

from valgo_common.config import settings
from valgo_common.dynamodb import update_order_status
from valgo_common.logging import get_logger, setup_logging

from .shoonya_adapter import ShoonyaBrokerAdapter

log = get_logger(__name__)
broker: ShoonyaBrokerAdapter | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global broker
    setup_logging()
    log.info("node.starting", instance_id=os.getenv("INSTANCE_ID", "local"), broker="shoonya")

    if not settings.shoonya_user_id:
        log.error("node.missing_shoonya_credentials — set SHOONYA_USER_ID in env")
        yield
        return

    try:
        broker = ShoonyaBrokerAdapter()
        log.info("node.broker_ready", broker="shoonya", user=settings.shoonya_user_id)
    except Exception as e:
        log.error("node.broker_init_failed", error=str(e),
                  hint="Run: python scripts/shoonya_login.py --open, then --code CODE")

    yield
    log.info("node.shutting_down")


app = FastAPI(title="Valgo Execution Node — Shoonya", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "broker": "shoonya", "ready": broker is not None}


@app.post("/place")
async def place(payload: dict) -> dict:
    if broker is None:
        raise HTTPException(status_code=503, detail="broker not initialised")

    order_id = payload["order_id"]
    req      = payload["request"]

    try:
        broker_order_id = await broker.place_order(req)
    except Exception as e:
        log.error("node.broker_rejected", order=order_id, error=str(e))
        await update_order_status(
            order_id, "REJECTED",
            updated_at=datetime.now(timezone.utc).isoformat(),
            rejection_reason=str(e),
        )
        raise HTTPException(status_code=502, detail=str(e)) from e

    await update_order_status(
        order_id, "SUBMITTED",
        updated_at=datetime.now(timezone.utc).isoformat(),
        broker_order_id=broker_order_id,
    )
    return {"broker_order_id": broker_order_id, "status": "submitted"}


@app.delete("/orders/{broker_order_id}")
async def cancel(broker_order_id: str) -> dict:
    if broker is None:
        raise HTTPException(status_code=503, detail="broker not initialised")
    ok = await broker.cancel_order(broker_order_id)
    return {"cancelled": ok}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("execution_node.main:app", host="0.0.0.0", port=8095)
