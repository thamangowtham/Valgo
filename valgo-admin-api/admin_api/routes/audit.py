"""Audit log read endpoints."""
from fastapi import APIRouter, Query

from valgo_common.dynamodb import get_table

router = APIRouter()


@router.get("")
async def list_audit(limit: int = Query(100, le=500), status: str | None = None) -> dict:
    """Recent audit events. Filter by event_type if provided."""
    async with get_table("audit") as t:
        # Naive scan — fine for personal-scale volumes. Replace with GSI queries
        # if your daily volume crosses ~10k events.
        kwargs = {"Limit": limit}
        if status:
            kwargs["FilterExpression"] = "event_type = :et"
            kwargs["ExpressionAttributeValues"] = {":et": status}
        resp = await t.scan(**kwargs)
    return {"events": resp.get("Items", []), "count": resp.get("Count", 0)}
