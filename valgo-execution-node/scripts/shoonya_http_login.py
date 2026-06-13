"""Shoonya no-browser auto-login — pure HTTP requests, works on any cloud.

Reverse-engineered from the Shoonya OAuth login page JS bundle.
No Playwright, no Chrome, no display required.

How it works:
  1. POST /NorenWClientAPI/QuickAuth  (credentials + TOTP)
  2. POST /NorenWClientAPI/GetAuthCode (app_key + session token)
  3. Follow redirect → capture code from URL
  4. POST /NorenWClientAPI/GenAcsTok  (exchange code for access token)
  5. Save access token to shoonya_token.txt

Usage:
  python scripts/shoonya_http_login.py
  python scripts/shoonya_http_login.py --test     # also test the token
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pyotp
import requests

sys.path.insert(0, str(Path(__file__).parents[1]))
from execution_node.shoonya_adapter import create_access_token, TOKEN_FILE

# ── Constants ─────────────────────────────────────────────────────────────────
USER_ID    = os.getenv("SHOONYA_USER_ID",    "FN213657")
PASSWORD   = os.getenv("SHOONYA_PASSWORD",   "Valgo@1350")
TOTP_SEED  = os.getenv("SHOONYA_TOTP_SEED",  "X33RC4H723FG773JO7752Y746VQVH3HF")
CLIENT_ID  = os.getenv("SHOONYA_VENDOR_CODE","FN213657_U")

BASE_URL   = "https://trade.shoonya.com/NorenWClientAPI"
APK_VER    = "W2_20250926"
SOURCE     = "API"
VC         = "NOREN_API"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# K array reverse-engineered from Login-56398151.js
# Xa = new Uint8Array([83,50,97,114,110,46,27,93])
_K = [83, 50, 97, 114, 110, 46, 27, 93]


# ── Crypto helpers ────────────────────────────────────────────────────────────

def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def compute_appkey(uid: str) -> str:
    """Replicates: d = uid+'|'; for p,k in enumerate(K): d += chr(k+p); sha256(d)"""
    d = uid + "|"
    for p, k in enumerate(_K):
        d += chr(k + p)
    return sha256(d)


def get_totp() -> str:
    return pyotp.TOTP(TOTP_SEED).now()


def extract_code(url: str) -> str | None:
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    return params.get("code", [None])[0]


# ── API calls ─────────────────────────────────────────────────────────────────

def quick_auth(session: requests.Session, totp: str) -> str:
    """
    POST /NorenWClientAPI/QuickAuth
    Returns susertoken (session token).
    """
    payload = {
        "uid":       USER_ID,
        "pwd":       sha256(PASSWORD),
        "factor2":   totp,
        "appkey":    compute_appkey(USER_ID),
        "imei":      str(uuid.uuid4()),
        "source":    SOURCE,
        "vc":        VC,
        "apkversion": APK_VER,
        "addldivinf": USER_AGENT,
        "app_key":   CLIENT_ID,
    }
    body = "jData=" + json.dumps(payload, separators=(",", ":"))
    resp = session.post(
        f"{BASE_URL}/QuickAuth",
        data=body,
        headers={"Content-Type": "text/plain"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("stat") != "Ok":
        raise ValueError(f"QuickAuth failed: {data.get('emsg', data)}")
    return data["susertoken"]


def get_auth_code(session: requests.Session, susertoken: str) -> str:
    """
    POST /NorenWClientAPI/GetAuthCode
    Returns OAuth auth code from redirect URL.
    """
    payload = {"app_key": CLIENT_ID}
    # Form body (enctype=text/plain): jData={"app_key":"..."}
    body = f"jData={json.dumps(payload, separators=(',', ':'))}&jKey={susertoken}"
    resp = session.post(
        f"{BASE_URL}/GetAuthCode",
        data=body,
        headers={"Content-Type": "text/plain"},
        allow_redirects=False,
        timeout=15,
    )

    # 302 redirect → Location header has the code
    if resp.status_code in (301, 302, 303, 307, 308):
        location = resp.headers.get("Location", "")
        code = extract_code(location)
        if code:
            return code
        # If no code in Location, check body for redirect URL
        raise RuntimeError(f"GetAuthCode redirected but no code in Location: {location}")

    # 200 with JSON response
    if resp.status_code == 200:
        try:
            data = resp.json()
            if data.get("code"):
                return data["code"]
            if data.get("stat") != "Ok":
                raise RuntimeError(f"GetAuthCode error: {data.get('emsg', data)}")
        except ValueError:
            pass

        # Parse from body text
        code = extract_code(resp.url)
        if code:
            return code

    raise RuntimeError(
        f"GetAuthCode unexpected response {resp.status_code}: {resp.text[:500]}"
    )


# ── Main flow ─────────────────────────────────────────────────────────────────

def http_login() -> str:
    """Full login flow. Returns auth code."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Origin":     "https://trade.shoonya.com",
        "Referer":    "https://trade.shoonya.com/OAuthlogin/investor-entry-level/login",
    })

    # Try pyotp first; if OTP fails, fall back to manual entry
    import time as _time
    totp_candidates = [
        ("auto-current", get_totp()),
        ("auto-previous", pyotp.TOTP(TOTP_SEED).at(_time.time() - 30)),
        ("auto-next",    pyotp.TOTP(TOTP_SEED).at(_time.time() + 30)),
    ]

    susertoken = None
    for label, totp in totp_candidates:
        print(f"[1/2] QuickAuth  uid={USER_ID} totp={totp} ({label}) ...")
        try:
            susertoken = quick_auth(session, totp)
            print(f"      susertoken: {susertoken[:20]}...")
            break
        except ValueError as e:
            msg = str(e)
            if "OTP" in msg or "otp" in msg.lower():
                print(f"      OTP rejected — trying next window...")
            else:
                raise  # other errors (wrong VC, blocked, etc.)

    if susertoken is None:
        # pyotp failed — ask for manual TOTP
        if sys.stdin.isatty():
            print()
            print("  Auto-TOTP failed. Open your authenticator app and enter the code.")
            print(f"  (Shoonya 2FA for {USER_ID})")
            manual_totp = input("  Enter TOTP: ").strip()
            if not manual_totp:
                raise RuntimeError("No TOTP entered.")
            print(f"[1/2] QuickAuth  uid={USER_ID} totp={manual_totp} (manual) ...")
            susertoken = quick_auth(session, manual_totp)
            print(f"      susertoken: {susertoken[:20]}...")
            print()
            print(f"  TIP: Set SHOONYA_TOTP_SEED env var with your actual authenticator seed")
            print(f"       to enable fully automatic login next time.")
        else:
            raise RuntimeError(
                "TOTP auto-generation failed and no terminal available for manual entry.\n"
                "Set SHOONYA_TOTP_SEED env var to your correct authenticator seed."
            )

    print(f"[2/2] GetAuthCode  app_key={CLIENT_ID} ...")
    code = get_auth_code(session, susertoken)
    print(f"      auth_code : {code[:20]}...")
    return code


def main():
    p = argparse.ArgumentParser(description="Shoonya no-browser HTTP login")
    p.add_argument("--test", action="store_true", help="Also test the saved token")
    args = p.parse_args()

    print(f"\n{'='*60}")
    print("  Shoonya HTTP Auto-Login (no browser)")
    print(f"{'='*60}")
    print(f"  User    : {USER_ID}")
    print(f"  Base URL: {BASE_URL}")
    print()

    try:
        code  = http_login()
        print()
        print("Exchanging code for access token...")
        token = create_access_token(code)

        print(f"\n{'='*60}")
        print("  SUCCESS")
        print(f"{'='*60}")
        print(f"  Token   : {token[:30]}...")
        print(f"  Saved to: {TOKEN_FILE}")

        if args.test:
            print()
            print("Testing token...")
            from execution_node.shoonya_adapter import _ShoonyaApi, USER_ID as UID
            api = _ShoonyaApi()
            api.injectOAuthHeader(token, UID, UID)
            ret = api.get_limits()
            if ret:
                print(f"  Cash  : {ret.get('cash', 'n/a')}")
                print(f"  Net   : {ret.get('net', 'n/a')}")
            else:
                print("  get_limits() returned no data")
            q = api.get_quotes("NSE", "26000")
            if q:
                print(f"  NIFTY : {q.get('lp', 'n/a')}")
            print("  Token is working.")

    except Exception as e:
        print(f"\nFAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
