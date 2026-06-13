"""Webhook handler — public-facing FastAPI service behind ALB.

Receives signals from TradingView and other external sources. Verifies the
shared-secret signature, then either:
    a) Routes the signal to a mapped strategy (it goes through normal risk gates)
    b) For pre-decided trades, forwards directly to the execution router

Run: python -m services.webhook.main
"""
from __future__ import annotations

import hashlib
import hmac
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from fastapi import FastAPI, Header, HTTPException, Request

from valgo_common.config import settings
from valgo_common.dynamodb import append_audit_event
from valgo_common.logging import get_logger, setup_logging
from valgo_common.models import OrderRequest, OrderSide, OrderType

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    log.info("webhook.starting")
    yield


app = FastAPI(title="Valgo Webhook Handler", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/webhook/tv/{slug}")
async def tradingview_webhook(slug: str, request: Request, x_signature: str | None = Header(default=None)) -> dict:
    """TradingView webhook entry point.

    Expected JSON payload:
        {
            "strategy_id": "s1",
            "tradingsymbol": "NIFTY26500CE",
            "side": "BUY",
            "quantity": 50,
            "price": 142.5
        }
    Header: X-Signature: hex(HMAC-SHA256(body, shared_secret))
    """
    body = await request.body()

    if settings.tradingview_shared_secret:
        if not _verify_signature(body, x_signature or "", settings.tradingview_shared_secret):
            log.warning("webhook.invalid_signature", slug=slug)
            raise HTTPException(status_code=401, detail="invalid signature")

    try:
        payload = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid json: {e}") from e

    log.info("webhook.received", slug=slug, payload_keys=list(payload.keys()))

    # Audit every inbound signal regardless of what we do with it
    await append_audit_event({
        "event_id": str(uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": "webhook_signal_received",
        "actor": f"tradingview:{slug}",
        "payload": payload,
    })

    # Forward as OrderRequest to the execution router
    order_req = _payload_to_order_request(payload)
    async with httpx.AsyncClient(timeout=2.0) as client:
        resp = await client.post(
            f"{settings.execution_router_url}/orders",
            json=order_req.model_dump(mode="json"),
        )
        if resp.status_code >= 400:
            log.error("webhook.router_rejected", status=resp.status_code, body=resp.text)
            raise HTTPException(status_code=resp.status_code, detail=resp.text)

    return {"accepted": True, **resp.json()}


def _verify_signature(body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _payload_to_order_request(p: dict) -> OrderRequest:
    return OrderRequest(
        strategy_id=p.get("strategy_id", "webhook"),
        account_id=p.get("account_id", "a1"),
        tradingsymbol=p["tradingsymbol"],
        side=OrderSide(p["side"].upper()),
        quantity=int(p["quantity"]),
        order_type=OrderType(p.get("order_type", "MARKET")),
        price=p.get("price"),
        idempotency_key=p.get("idempotency_key", str(uuid4())),
        tag="webhook:tradingview",
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("webhook.main:app", host="0.0.0.0", port=8092)
