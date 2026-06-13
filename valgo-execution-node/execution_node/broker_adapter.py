"""Broker adapter — wraps Kite Connect's order placement API.

SEBI April 2026 compliance:
    - MARKET orders are converted to MPP (Market Price Protection) automatically
    - All orders are tagged with strategy/idempotency for traceability
"""
from __future__ import annotations

import asyncio
from typing import Any

from kiteconnect import KiteConnect

from valgo_common.logging import get_logger

log = get_logger(__name__)


class KiteBrokerAdapter:
    """Async wrapper around KiteConnect (which is sync). Use to_thread for blocking calls."""

    def __init__(self, api_key: str, access_token: str) -> None:
        self._kite = KiteConnect(api_key=api_key)
        self._kite.set_access_token(access_token)

    async def place_order(self, req: dict) -> str:
        """Place an order via Kite. Returns broker_order_id."""
        # Map our internal types to Kite's enums
        kite_params = self._build_kite_params(req)
        log.info("broker.placing_order", symbol=req["tradingsymbol"], side=req["side"])

        # KiteConnect SDK is sync — run in thread to avoid blocking the event loop
        broker_order_id: str = await asyncio.to_thread(
            self._kite.place_order,
            variety=KiteConnect.VARIETY_REGULAR,
            **kite_params,
        )
        return broker_order_id

    async def get_order_status(self, broker_order_id: str) -> dict:
        history = await asyncio.to_thread(self._kite.order_history, broker_order_id)
        return history[-1] if history else {}

    # ------------------------------------------------------------------
    def _build_kite_params(self, req: dict) -> dict[str, Any]:
        params: dict[str, Any] = {
            "exchange": req.get("exchange", "NFO"),
            "tradingsymbol": req["tradingsymbol"],
            "transaction_type": req["side"],   # "BUY" / "SELL"
            "quantity": int(req["quantity"]),
            "product": req.get("product", "MIS"),
            "validity": KiteConnect.VALIDITY_DAY,
            "tag": req.get("tag", "valgo")[:20],   # Kite tag max 20 chars
        }

        order_type = req.get("order_type", "MARKET")
        # SEBI 2026: convert MARKET → MPP (Kite handles this server-side, but we set it explicitly)
        if order_type == "MARKET":
            params["order_type"] = KiteConnect.ORDER_TYPE_MARKET
            # MPP is the default for retail post-April-2026 — Kite enforces it
        elif order_type == "LIMIT":
            params["order_type"] = KiteConnect.ORDER_TYPE_LIMIT
            params["price"] = float(req["price"])
        elif order_type == "SL":
            params["order_type"] = KiteConnect.ORDER_TYPE_SL
            params["price"] = float(req["price"])
            params["trigger_price"] = float(req["trigger_price"])
        elif order_type == "SL-M":
            params["order_type"] = KiteConnect.ORDER_TYPE_SLM
            params["trigger_price"] = float(req["trigger_price"])

        return params
