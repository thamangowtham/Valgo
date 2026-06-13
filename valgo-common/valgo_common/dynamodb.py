"""Async DynamoDB wrapper using aioboto3.

Tables (logical names; actual names are prefixed via settings.table_name):
    orders          PK: order_id
    positions       PK: account_id, SK: tradingsymbol
    audit           PK: event_id, SK: timestamp
    config          PK: config_key  (singleton config rows: strategies, accounts, etc)
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import aioboto3

from .config import settings


_session = aioboto3.Session()


@asynccontextmanager
async def get_dynamodb():
    """Async context manager yielding a DynamoDB resource."""
    kwargs: dict[str, Any] = {"region_name": settings.aws_region}
    if settings.dynamodb_endpoint:
        kwargs["endpoint_url"] = settings.dynamodb_endpoint
        kwargs["aws_access_key_id"] = "local"
        kwargs["aws_secret_access_key"] = "local"

    async with _session.resource("dynamodb", **kwargs) as ddb:
        yield ddb


@asynccontextmanager
async def get_table(name: str):
    """Yield a DynamoDB Table resource for a logical table name."""
    async with get_dynamodb() as ddb:
        table = await ddb.Table(settings.table_name(name))
        yield table


# ============================================================================
# Convenience accessors
# ============================================================================
async def put_order(order: dict) -> None:
    async with get_table("orders") as t:
        await t.put_item(Item=order)


async def get_order(order_id: str) -> dict | None:
    async with get_table("orders") as t:
        resp = await t.get_item(Key={"order_id": order_id})
        return resp.get("Item")


async def update_order_status(order_id: str, status: str, **fields) -> None:
    update_expr = "SET #s = :s, updated_at = :ts"
    expr_values = {":s": status, ":ts": fields.pop("updated_at", "")}
    expr_names = {"#s": "status"}
    for k, v in fields.items():
        update_expr += f", {k} = :{k}"
        expr_values[f":{k}"] = v
    async with get_table("orders") as t:
        await t.update_item(
            Key={"order_id": order_id},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=expr_values,
            ExpressionAttributeNames=expr_names,
        )


async def append_audit_event(event: dict) -> None:
    async with get_table("audit") as t:
        await t.put_item(Item=event)


async def get_config(key: str) -> dict | None:
    async with get_table("config") as t:
        resp = await t.get_item(Key={"config_key": key})
        return resp.get("Item")


async def put_config(key: str, payload: dict) -> None:
    async with get_table("config") as t:
        await t.put_item(Item={"config_key": key, **payload})
