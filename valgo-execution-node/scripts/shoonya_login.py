"""Shoonya login — get access token and save to shoonya_token.txt.

Usage:
  Step 1 — Open browser and login:
    python scripts/shoonya_login.py --open

  Step 2 — Exchange code for token:
    python scripts/shoonya_login.py --code YOUR_CODE_FROM_REDIRECT_URL

  Step 3 — Test connection with saved token:
    python scripts/shoonya_login.py --test
"""
from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from execution_node.shoonya_adapter import (
    create_access_token,
    get_auth_url,
    load_token,
    TOKEN_FILE,
    _ShoonyaApi,
    USER_ID,
)


def main():
    p = argparse.ArgumentParser(description="Shoonya login helper")
    p.add_argument("--open",  action="store_true", help="Open OAuth browser URL")
    p.add_argument("--code",  default="",          help="Auth code from redirect URL")
    p.add_argument("--test",  action="store_true", help="Test saved token")
    args = p.parse_args()

    # Step 1 — open browser
    if args.open:
        url = get_auth_url()
        print(f"\n{'='*60}")
        print("  STEP 1: Open Shoonya Login")
        print(f"{'='*60}")
        print(f"\n  URL: {url}")
        print()
        print("  After login, copy the 'code' from the redirect URL:")
        print("    https://...?code=COPY-THIS-VALUE")
        print()
        print("  Then run:")
        print("    python scripts/shoonya_login.py --code YOUR_CODE")
        webbrowser.open(url)
        return

    # Step 2 — exchange code for token
    if args.code:
        print(f"\n{'='*60}")
        print("  STEP 2: Getting Access Token")
        print(f"{'='*60}")
        try:
            token = create_access_token(args.code)
            print(f"\n  SUCCESS!")
            print(f"  Token   : {token[:30]}...")
            print(f"  Saved to: {TOKEN_FILE}")
            print()
            print("  Now test connection:")
            print("    python scripts/shoonya_login.py --test")
        except Exception as e:
            print(f"\n  FAILED: {e}")
            sys.exit(1)
        return

    # Step 3 — test saved token
    if args.test:
        print(f"\n{'='*60}")
        print("  STEP 3: Testing Saved Token")
        print(f"{'='*60}")

        token = load_token()
        if not token:
            print(f"\n  ERROR: No token found at {TOKEN_FILE}")
            print("  Run --open first, then --code YOUR_CODE")
            sys.exit(1)

        print(f"\n  Token loaded: {token[:30]}...")

        api = _ShoonyaApi()
        api.injectOAuthHeader(token, USER_ID, USER_ID)

        # Test: get limits
        print("\n  Testing Limits API...")
        ret = api.get_limits()
        if ret:
            print(f"  Cash     : {ret.get('cash', 'n/a')}")
            print(f"  Net      : {ret.get('net', 'n/a')}")
            print(f"  Margin   : {ret.get('marginused', 'n/a')}")
        else:
            print("  No response from Limits API")

        # Test: get quote for NIFTY
        print("\n  Testing GetQuotes (NIFTY, token=26000)...")
        quote = api.get_quotes(exchange="NSE", token="26000")
        if quote:
            print(f"  LTP  : {quote.get('lp', 'n/a')}")
            print(f"  High : {quote.get('h',  'n/a')}")
            print(f"  Low  : {quote.get('l',  'n/a')}")
        else:
            print("  No response from GetQuotes API")

        print(f"\n{'='*60}")
        print("  Connection test complete.")
        print(f"{'='*60}")
        return

    # No args — show help
    p.print_help()


if __name__ == "__main__":
    main()
