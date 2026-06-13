from fastapi import APIRouter, HTTPException
from valgo_common.dynamodb import get_config, put_config

router = APIRouter()
KEY = "nodes"


@router.get("")
async def list_nodes() -> dict:
    return await get_config(KEY) or {"nodes": []}


@router.put("")
async def upsert_nodes(payload: dict) -> dict:
    if "nodes" not in payload:
        raise HTTPException(status_code=400, detail="missing 'nodes' field")
    await put_config(KEY, payload)
    return {"saved": len(payload["nodes"])}
