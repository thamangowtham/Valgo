"""Risk limits + kill switch routes."""
from fastapi import APIRouter

from valgo_common.dynamodb import get_config, put_config
from valgo_common.models import RiskLimits
from valgo_common.redis_client import get_redis

router = APIRouter()
KEY = "risk_limits"


@router.get("")
async def get_risk() -> dict:
    cfg = await get_config(KEY)
    return cfg or RiskLimits().model_dump(mode="json")


@router.put("")
async def update_risk(limits: RiskLimits) -> dict:
    await put_config(KEY, limits.model_dump(mode="json"))
    # Also push to Redis so the execution router picks it up immediately
    r = get_redis()
    await r.set("risk:limits", limits.model_dump_json(), ex=10)
    if limits.kill_switch:
        await r.set("risk:kill_switch", "1")
    else:
        await r.delete("risk:kill_switch")
    return {"saved": True}
