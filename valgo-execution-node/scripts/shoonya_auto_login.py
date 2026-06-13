"""Shoonya auto-login — fully automated, no browser interaction needed.

Uses Playwright to:
  1. Open OAuth login page (headless browser)
  2. Fill User ID + Password automatically
  3. Generate TOTP from seed (pyotp)
  4. Submit form
  5. Capture auth code from redirect URL
  6. Call getAccessToken() → save to shoonya_token.txt

Usage:
  python scripts/shoonya_auto_login.py

For cloud / scheduled task (headless):
  python scripts/shoonya_auto_login.py --headless

Schedule (Windows Task Scheduler):
  Run daily at 9:00 AM before market open.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pyotp
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

sys.path.insert(0, str(Path(__file__).parents[1]))

from execution_node.shoonya_adapter import create_access_token, TOKEN_FILE

# ── Credentials ───────────────────────────────────────────────────────────────
USER_ID   = os.getenv("SHOONYA_USER_ID",    "FN213657")
PASSWORD  = os.getenv("SHOONYA_PASSWORD",   "Valgo@1350")
TOTP_SEED = os.getenv("SHOONYA_TOTP_SEED",  "X33RC4H723FG773JO7752Y746VQVH3HF")
CLIENT_ID = os.getenv("SHOONYA_VENDOR_CODE","FN213657_U")

LOGIN_URL = (
    f"https://trade.shoonya.com/OAuthlogin/investor-entry-level/login"
    f"?api_key={CLIENT_ID}&route_to={USER_ID}"
)


def get_totp() -> str:
    return pyotp.TOTP(TOTP_SEED).now()


def extract_code(url: str) -> str | None:
    """Extract 'code' parameter from redirect URL."""
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    code = params.get("code", [None])[0]
    if not code:
        # Also check fragment
        params2 = parse_qs(parsed.fragment)
        code = params2.get("code", [None])[0]
    return code


def auto_login(headless: bool = True) -> str:
    """
    Automate Shoonya OAuth login.
    Returns auth code from redirect URL.
    """
    print(f"[auto_login] Starting browser (headless={headless})...")
    print(f"[auto_login] URL: {LOGIN_URL}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page    = context.new_page()

        # Track redirect URL
        redirect_url: list[str] = []

        def on_request(request):
            url = request.url
            if "code=" in url and "OAuthlogin" not in url:
                redirect_url.append(url)

        page.on("request", on_request)

        # Navigate to login page
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
        print(f"[auto_login] Page loaded: {page.title()}")
        time.sleep(2)

        # Fill User ID
        try:
            uid_field = page.locator(
                "input[placeholder*='User'], input[name*='uid'], "
                "input[id*='uid'], input[type='text']"
            ).first
            uid_field.fill(USER_ID)
            print(f"[auto_login] Filled User ID: {USER_ID}")
        except Exception as e:
            print(f"[auto_login] WARNING: Could not find UID field: {e}")

        # Fill Password
        try:
            pwd_field = page.locator("input[type='password']").first
            pwd_field.fill(PASSWORD)
            print("[auto_login] Filled Password")
        except Exception as e:
            print(f"[auto_login] WARNING: Could not find password field: {e}")

        # Click Continue / Next (if two-step form)
        try:
            btn = page.locator(
                "button:has-text('Continue'), button:has-text('Next'), "
                "button:has-text('Login'), button[type='submit']"
            ).first
            btn.click()
            print("[auto_login] Clicked submit/continue")
            time.sleep(2)
        except Exception as e:
            print(f"[auto_login] WARNING: Could not click button: {e}")

        # Fill TOTP
        try:
            otp = get_totp()
            print(f"[auto_login] Generated TOTP: {otp}")
            otp_field = page.locator(
                "input[placeholder*='OTP'], input[placeholder*='TOTP'], "
                "input[name*='otp'], input[name*='totp'], "
                "input[id*='otp'], input[maxlength='6']"
            ).first
            otp_field.fill(otp)
            print("[auto_login] Filled TOTP")
        except Exception as e:
            print(f"[auto_login] WARNING: Could not find OTP field: {e}")

        # Submit
        try:
            btn2 = page.locator(
                "button:has-text('Login'), button:has-text('Submit'), "
                "button:has-text('Verify'), button[type='submit']"
            ).first
            btn2.click()
            print("[auto_login] Submitted login form")
        except Exception as e:
            print(f"[auto_login] WARNING: Could not submit: {e}")

        # Wait for redirect with code
        print("[auto_login] Waiting for redirect with auth code...")
        for _ in range(20):
            time.sleep(1)
            current_url = page.url
            if "code=" in current_url:
                redirect_url.append(current_url)
                break

        browser.close()

        # Extract code
        for url in redirect_url:
            code = extract_code(url)
            if code:
                print(f"[auto_login] Auth code found: {code[:20]}...")
                return code

        # If redirect not captured, check final page URL
        raise RuntimeError(
            "Could not capture auth code from redirect.\n"
            "Check if login page structure has changed.\n"
            f"Last URL seen in redirect_url list: {redirect_url}"
        )


def main():
    p = argparse.ArgumentParser(description="Shoonya auto-login")
    p.add_argument("--headless", action="store_true", default=False,
                   help="Run browser in headless mode (for cloud/server)")
    p.add_argument("--show",    action="store_true",
                   help="Show browser window (for debugging)")
    args = p.parse_args()

    headless = args.headless and not args.show

    print(f"\n{'='*60}")
    print("  Shoonya Auto Login")
    print(f"{'='*60}")
    print(f"  User ID  : {USER_ID}")
    print(f"  Headless : {headless}")
    print()

    try:
        # Step 1: Auto-login → get auth code
        code = auto_login(headless=headless)

        # Step 2: Exchange code for access token
        print("\n[token] Exchanging code for access token...")
        token = create_access_token(code)

        print(f"\n{'='*60}")
        print("  SUCCESS")
        print(f"{'='*60}")
        print(f"  Token   : {token[:30]}...")
        print(f"  Saved to: {TOKEN_FILE}")
        print()
        print("  Shoonya is ready for trading.")

    except Exception as e:
        print(f"\nFAILED: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
