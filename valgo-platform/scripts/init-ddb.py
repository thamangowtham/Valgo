#!/usr/bin/env python3
"""One-shot DynamoDB bootstrap for the local dev stack.

Creates the four tables that valgo-common expects (orders, positions, audit,
config) in the local dynamodb-local container, then seeds a minimal config:

    config[subscriptions]  -> empty (ingestor will fall back to defaults)
    config[strategies]     -> one placeholder ema_crossover strategy so the
                              decision engine stays up instead of exiting
                              on "no active strategies"

Idempotent: existing tables are skipped, existing config rows are overwritten.

Run from the host:
    python3 valgo-platform/scripts/init-ddb.py

Picks up DYNAMODB_ENDPOINT and DYNAMODB_TABLE_PREFIX from the environment;
defaults match the dev compose (`http://localhost:8003`, `valgo_local_`).
"""
from __future__ import annotations

import os
import sys

import boto3
from botocore.exceptions import ClientError

ENDPOINT = os.getenv("DYNAMODB_ENDPOINT", "http://localhost:8003")
PREFIX = os.getenv("DYNAMODB_TABLE_PREFIX", "valgo_local_")
REGION = os.getenv("AWS_REGION", "ap-south-1")


TABLES = [
    {
        "TableName": f"{PREFIX}orders",
        "KeySchema":            [{"AttributeName": "order_id", "KeyType": "HASH"}],
        "AttributeDefinitions": [{"AttributeName": "order_id", "AttributeType": "S"}],
    },
    {
        "TableName": f"{PREFIX}positions",
        "KeySchema": [
            {"AttributeName": "account_id",    "KeyType": "HASH"},
            {"AttributeName": "tradingsymbol", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "account_id",    "AttributeType": "S"},
            {"AttributeName": "tradingsymbol", "AttributeType": "S"},
        ],
    },
    {
        "TableName": f"{PREFIX}audit",
        "KeySchema": [
            {"AttributeName": "event_id",  "KeyType": "HASH"},
            {"AttributeName": "timestamp", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "event_id",  "AttributeType": "S"},
            {"AttributeName": "timestamp", "AttributeType": "S"},
        ],
    },
    {
        "TableName": f"{PREFIX}config",
        "KeySchema":            [{"AttributeName": "config_key", "KeyType": "HASH"}],
        "AttributeDefinitions": [{"AttributeName": "config_key", "AttributeType": "S"}],
    },
]


SEED = {
    "subscriptions": {"instrument_tokens": []},
    "strategies": {
        "strategies": [
            {
                "id": "stpsar-1",
                "name": "SuperTrend+PSAR Confluence",
                "instrument": "NIFTY",
                "type": "CE",
                "strike_logic": "ATM",
                "entry_condition": "st_psar_confluence",
                "target_pct": "1.0",
                "stop_loss_pct": "0.5",
                "quantity": 1,
                "account_id": "default",
                "instruments": ["NIFTY", "BANKNIFTY"],
                "class_name": "st_psar_confluence",
                "active": True,
            }
        ]
    },
}


def make_client():
    kwargs = {"region_name": REGION}
    if ENDPOINT:
        kwargs["endpoint_url"] = ENDPOINT
        kwargs["aws_access_key_id"] = "local"
        kwargs["aws_secret_access_key"] = "local"
    return boto3.client("dynamodb", **kwargs), boto3.resource("dynamodb", **kwargs)


def create_tables(ddb_client) -> None:
    existing = set(ddb_client.list_tables()["TableNames"])
    for spec in TABLES:
        name = spec["TableName"]
        if name in existing:
            print(f"  skip   {name} (exists)")
            continue
        ddb_client.create_table(BillingMode="PAY_PER_REQUEST", **spec)
        print(f"  create {name}")
    # Wait for all to be active
    for spec in TABLES:
        ddb_client.get_waiter("table_exists").wait(TableName=spec["TableName"])


def seed_config(ddb_resource) -> None:
    table = ddb_resource.Table(f"{PREFIX}config")
    for key, payload in SEED.items():
        table.put_item(Item={"config_key": key, **payload})
        print(f"  seed   config[{key}]")


def main() -> int:
    print(f"DynamoDB endpoint: {ENDPOINT}")
    print(f"Table prefix:      {PREFIX}")
    print()
    try:
        ddb_client, ddb_resource = make_client()
        print("Tables:")
        create_tables(ddb_client)
        print()
        print("Seed:")
        seed_config(ddb_resource)
        print()
        print("Done.")
        return 0
    except ClientError as e:
        print(f"AWS error: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
