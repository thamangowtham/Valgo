"""Strategy CRUD routes."""
from fastapi import APIRouter, HTTPException

from valgo_common.dynamodb import get_config, put_config

router = APIRouter()
KEY = "strategies"


@router.get("")
async def list_strategies() -> dict:
    cfg = await get_config(KEY) or {"strategies": []}
    return cfg


@router.put("")
async def upsert_strategies(payload: dict) -> dict:
    """Replace the entire strategies list. Simpler than per-item PUT."""
    if "strategies" not in payload:
        raise HTTPException(status_code=400, detail="missing 'strategies' field")
    await put_config(KEY, payload)
    return {"saved": len(payload["strategies"])}
