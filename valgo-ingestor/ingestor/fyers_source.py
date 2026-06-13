"""Fyers WebSocket source — backup feed.

Skeleton only. Implement when:
    1. You have a Fyers account and credentials in .env
    2. The Kite primary has been stable enough that you trust failover testing

The interface mirrors KiteTickSource — same on_tick callback signature,
same start/stop/update_subscription methods. The FailoverManager doesn't
care which provider is active.

API docs: https://myapi.fyers.in/docsv3
"""
from __future__ import annotations

from valgo_common.logging import get_logger
from valgo_common.models import Tick

log = get_logger(__name__)


class FyersTickSource:
    name = "Fyers"
    provider_id = "fyers"

    def __init__(self, app_id: str, access_token: str, on_tick, on_status_change=None) -> None:
        raise NotImplementedError("Fyers source pending — see docstring")
