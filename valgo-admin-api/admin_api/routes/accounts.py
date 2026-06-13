from fastapi import APIRouter, HTTPException
from valgo_common.dynamodb import get_config, put_config

router = APIRouter()
KEY = "accounts"


@router.get("")
async def list_accounts() -> dict:
    return await get_config(KEY) or {"accounts": []}


@router.put("")
async def upsert_accounts(payload: dict) -> dict:
    if "accounts" not in payload:
        raise HTTPException(status_code=400, detail="missing 'accounts' field")
    await put_config(KEY, payload)
    return {"saved": len(payload["accounts"])}
