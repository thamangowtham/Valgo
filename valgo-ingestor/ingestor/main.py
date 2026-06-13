"""Tick ingestor entrypoint — Broker WebSocket (or REST) → Redis.

Boot sequence:
    1. Select broker via BROKER env var (kite or shoonya)
    2. Resolve effective subscription from DynamoDB strategies
    3. Resolve symbol names → instrument tokens (Kite)
    4. Start TickSource
    5. Every tick → publish to Redis tick:{tradingsymbol}

Kite auth modes (auto-selected):
    enctoken set   → KiteRestTickSource  (REST poll every 1 s, no subscription)
    access_token   → KiteTickSource      (WebSocket, requires Kite Connect)

Run:  python -m ingestor.main
"""
from __future__ import annotations

import asyncio
import os
import signal

from valgo_common.config import settings
from valgo_common.dynamodb import get_config
from valgo_common.logging import get_logger, setup_logging
from valgo_common.models import Tick
from valgo_common.redis_client import close_redis, publish_tick

log = get_logger(__name__)
BROKER = os.getenv("BROKER", "kite").lower()

# Static NSE index tokens (never change)
_NSE_STATIC: dict[str, int] = {
    "NIFTY":      256265,
    "BANKNIFTY":  260105,
    "FINNIFTY":   257801,
    "MIDCPNIFTY": 288009,
    "SENSEX":     265,
}

# Zerodha instrument strings for static indices (used by /quote API)
_NSE_STATIC_INST: dict[int, str] = {
    256265: "NSE:NIFTY 50",
    260105: "NSE:NIFTY BANK",
    257801: "NSE:NIFTY FIN SERVICE",
    288009: "NSE:NIFTY MID SELECT",
    265:    "BSE:SENSEX",
}


async def on_tick(tick: Tick) -> None:
    await publish_tick(tick)


async def resolve_effective_subscription() -> list[str]:
    try:
        cfg = await get_config("strategies") or {}
        strategies = cfg.get("strategies", [])
        symbols: set[str] = set()
        for s in strategies:
            if not s.get("active", True):
                continue
            insts = s.get("instruments", [])
            if isinstance(insts, list):
                symbols.update(i for i in insts if i)
            elif isinstance(insts, str):
                symbols.update(i.strip() for i in insts.split(",") if i.strip())
        if symbols:
            return sorted(symbols)
    except Exception as e:
        log.warning("ingestor.subscription_resolve_failed", error=str(e))
    return ["NIFTY", "BANKNIFTY"]


def resolve_kite_tokens(
    symbols: list[str],
) -> tuple[list[int], dict[int, str], dict[int, str]]:
    """Resolve symbol names to Kite instrument tokens.

    Returns:
        tokens           — list of integer tokens
        token_to_symbol  — {token: strategy-facing symbol name}
        token_to_inst    — {token: "EXCHANGE:TRADINGSYMBOL"} for /quote API
    """
    tokens: list[int] = []
    token_map: dict[int, str] = {}
    inst_map: dict[int, str] = {}

    unresolved = []
    for sym in symbols:
        if sym in _NSE_STATIC:
            t = _NSE_STATIC[sym]
            tokens.append(t)
            token_map[t] = sym
            if t in _NSE_STATIC_INST:
                inst_map[t] = _NSE_STATIC_INST[t]
        else:
            unresolved.append(sym)

    if unresolved:
        try:
            from kiteconnect import KiteConnect
            kite = KiteConnect(api_key=settings.kite_api_key or "kitefront")
            if settings.kite_enctoken:
                kite.set_access_token(settings.kite_enctoken)
                kite.reqsession.headers["Authorization"] = f"enctoken {settings.kite_enctoken}"
            else:
                kite.set_access_token(settings.kite_access_token)

            for exchange in ("MCX", "NSE", "NFO"):
                if not unresolved:
                    break
                instruments = kite.instruments(exchange)
                for inst in instruments:
                    name  = inst.get("name", "") or inst.get("tradingsymbol", "")
                    tsym  = inst.get("tradingsymbol", "")
                    tok   = inst.get("instrument_token")
                    itype = inst.get("instrument_type", "")
                    for sym in list(unresolved):
                        if (name == sym or tsym.startswith(sym)) and itype == "FUT":
                            tokens.append(tok)
                            token_map[tok] = sym
                            inst_map[tok] = f"{exchange}:{tsym}"
                            unresolved.remove(sym)
                            log.info("ingestor.token_resolved",
                                     symbol=sym, token=tok, tradingsymbol=tsym)
                            break
        except Exception as e:
            log.error("ingestor.token_lookup_failed", error=str(e))

    if unresolved:
        log.warning("ingestor.unresolved_symbols", symbols=unresolved)

    return tokens, token_map, inst_map


async def main() -> None:
    setup_logging()
    log.info("ingestor.starting", env=settings.env, broker=BROKER)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    symbols = await resolve_effective_subscription()
    log.info("ingestor.subscription_resolved", count=len(symbols), symbols=symbols)

    if BROKER == "shoonya":
        from .shoonya_source import ShoonyaTickSource
        if not settings.shoonya_user_id:
            log.error("ingestor.missing_shoonya_credentials")
            return
        source = ShoonyaTickSource(
            on_tick=on_tick,
            on_status_change=lambda s: log.info("ingestor.status", status=s),
        )
        try:
            await loop.run_in_executor(None, source.login)
        except Exception as e:
            log.error("ingestor.login_failed", error=str(e))
            return
        await source.start(symbols)

    else:  # kite
        if not settings.kite_enctoken and not settings.kite_access_token:
            log.error("ingestor.missing_kite_credentials",
                      hint="Set KITE_ENCTOKEN or KITE_ACCESS_TOKEN")
            return

        tokens, token_map, inst_map = resolve_kite_tokens(symbols)
        if not tokens:
            log.error("ingestor.no_tokens_resolved", symbols=symbols)
            return

        log.info("ingestor.tokens_resolved", count=len(tokens), map=token_map)

        if settings.kite_enctoken:
            # No Kite Connect subscription — poll REST every second
            from .kite_rest_source import KiteRestTickSource
            log.info("ingestor.kite_mode", mode="REST poll (enctoken)")
            source = KiteRestTickSource(
                enctoken=settings.kite_enctoken,
                on_tick=on_tick,
                token_to_symbol=token_map,
                on_status_change=lambda s: log.info("ingestor.status", status=s),
                poll_interval=1.0,
            )
        else:
            # Kite Connect subscription with WebSocket
            from .kite_source import KiteTickSource
            log.info("ingestor.kite_mode", mode="WebSocket (Kite Connect)")
            source = KiteTickSource(
                api_key=settings.kite_api_key,
                access_token=settings.kite_access_token,
                on_tick=on_tick,
                token_to_symbol=token_map,
                on_status_change=lambda s: log.info("ingestor.status", status=s),
            )

        await source.start(tokens)

    log.info("ingestor.ready")
    await stop_event.wait()

    log.info("ingestor.shutting_down")
    await source.stop()
    await close_redis()


if __name__ == "__main__":
    asyncio.run(main())
