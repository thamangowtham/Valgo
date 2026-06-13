from fastapi import APIRouter, HTTPException
from valgo_common.dynamodb import get_config, put_config

router = APIRouter()
KEY = "signals"


@router.get("")
async def list_signals() -> dict:
    return await get_config(KEY) or {"signals": []}


@router.put("")
async def upsert_signals(payload: dict) -> dict:
    if "signals" not in payload:
        raise HTTPException(status_code=400, detail="missing 'signals' field")
    await put_config(KEY, payload)
    return {"saved": len(payload["signals"])}
