"""Operator notifications — Telegram bot.

Strategies and the execution router send human-readable status / alert
messages here; this module forwards them to the configured Telegram chat.

Configuration is via environment variables (see Settings):
    TELEGRAM_BOT_TOKEN  — bot API token from @BotFather
    TELEGRAM_CHAT_ID    — chat or channel ID

Both must be set. If either is missing, telegram_send() is a no-op so
strategies don't have to guard the call site.
"""
from __future__ import annotations

import os
from typing import Any

import requests

from .logging import get_logger

log = get_logger(__name__)

_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


def telegram_send(text: str, parse_mode: str = "HTML") -> bool:
    """Send `text` to the configured Telegram chat. Returns True on success."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False

    try:
        resp = requests.post(
            _API_URL.format(token=token),
            json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
            timeout=10,
        )
        if resp.status_code == 200:
            return True
        log.warning("notifier.telegram_failed", status=resp.status_code, body=resp.text[:200])
        return False
    except Exception as e:
        log.warning("notifier.telegram_error", error=str(e))
        return False


async def telegram_send_async(text: str, parse_mode: str = "HTML") -> bool:
    """Async variant — runs the blocking POST in a thread."""
    import asyncio
    return await asyncio.to_thread(telegram_send, text, parse_mode)
