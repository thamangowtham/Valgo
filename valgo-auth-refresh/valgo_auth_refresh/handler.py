"""Daily Kite authentication refresh — AWS Lambda.

Triggered by EventBridge at 08:45 IST every weekday. Performs the Kite
login flow (which under SEBI 2026 requires daily 2FA), generates the
TOTP, exchanges request_token for access_token, and writes the fresh
token to Secrets Manager.

Execution nodes pull the token from Secrets Manager at startup and
re-fetch on 401.

Why a Lambda? This isn't on the hot path. Cold start is fine.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import boto3
import pyotp
import requests
from kiteconnect import KiteConnect

# AWS clients (init at module load — Lambda re-uses across invocations)
_secrets = boto3.client("secretsmanager", region_name=os.getenv("AWS_REGION", "ap-south-1"))
_sns = boto3.client("sns", region_name=os.getenv("AWS_REGION", "ap-south-1"))


def handler(event: dict, context: Any) -> dict:
    """Lambda entrypoint. Event ignored — runs the full refresh flow."""
    print("auth_refresh.starting")
    try:
        access_token = _refresh_kite_token()
        _write_token_to_secrets(access_token)
        _notify(success=True)
        return {"status": "ok", "token_length": len(access_token)}
    except Exception as e:
        print(f"auth_refresh.failed: {e}")
        _notify(success=False, error=str(e))
        raise


# ============================================================================
def _refresh_kite_token() -> str:
    """Run Kite's full login flow with TOTP. Returns fresh access_token.

    Steps:
        1. POST username/password → get request_id
        2. POST request_id + TOTP → get sess_id (sets cookies)
        3. Hit Kite Connect login endpoint → 302 with request_token
        4. POST request_token + api_secret → access_token
    """
    api_key = _get_secret("valgo/kite/api_key")
    api_secret = _get_secret("valgo/kite/api_secret")
    user_id = _get_secret("valgo/kite/user_id")
    password = _get_secret("valgo/kite/password")
    totp_seed = _get_secret("valgo/kite/totp_seed")

    session = requests.Session()

    # Step 1: username/password
    r1 = session.post(
        "https://kite.zerodha.com/api/login",
        data={"user_id": user_id, "password": password},
        timeout=10,
    )
    r1.raise_for_status()
    request_id = r1.json()["data"]["request_id"]

    # Step 2: TOTP
    totp = pyotp.TOTP(totp_seed).now()
    r2 = session.post(
        "https://kite.zerodha.com/api/twofa",
        data={
            "user_id": user_id,
            "request_id": request_id,
            "twofa_value": totp,
            "twofa_type": "totp",
        },
        timeout=10,
    )
    r2.raise_for_status()

    # Step 3: hit Kite Connect login URL — captures request_token in the redirect
    kite = KiteConnect(api_key=api_key)
    login_url = kite.login_url()
    r3 = session.get(login_url, allow_redirects=False, timeout=10)
    # The redirect URL contains ?request_token=XXX
    location = r3.headers.get("Location", "")
    if "request_token=" not in location:
        raise RuntimeError(f"no request_token in redirect: {location}")
    request_token = location.split("request_token=")[1].split("&")[0]

    # Step 4: exchange request_token for access_token
    data = kite.generate_session(request_token, api_secret=api_secret)
    return data["access_token"]


def _get_secret(name: str) -> str:
    resp = _secrets.get_secret_value(SecretId=name)
    return resp["SecretString"]


def _write_token_to_secrets(access_token: str) -> None:
    secret_id = "valgo/kite/access_token"
    try:
        _secrets.update_secret(SecretId=secret_id, SecretString=access_token)
    except _secrets.exceptions.ResourceNotFoundException:
        _secrets.create_secret(Name=secret_id, SecretString=access_token)
    print(f"auth_refresh.token_written secret_id={secret_id}")


def _notify(success: bool, error: str | None = None) -> None:
    topic_arn = os.getenv("ALERT_SNS_TOPIC")
    if not topic_arn:
        return
    subject = "Valgo: daily auth refresh " + ("OK" if success else "FAILED")
    message = json.dumps({"success": success, "error": error, "ts": time.time()})
    try:
        _sns.publish(TopicArn=topic_arn, Subject=subject, Message=message)
    except Exception as e:
        print(f"auth_refresh.notify_failed: {e}")
