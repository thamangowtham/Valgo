"""Data sources (tick feed providers) CRUD routes."""
from fastapi import APIRouter, HTTPException

from valgo_common.dynamodb import get_config, put_config

router = APIRouter()
KEY = "data_sources"


@router.get("")
async def list_sources() -> dict:
    return await get_config(KEY) or {"sources": []}


@router.put("")
async def upsert_sources(payload: dict) -> dict:
    if "sources" not in payload:
        raise HTTPException(status_code=400, detail="missing 'sources' field")
    await put_config(KEY, payload)
    return {"saved": len(payload["sources"])}
