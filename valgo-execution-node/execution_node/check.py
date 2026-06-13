"""check.py — Test Shoonya connection using saved access token.

This uses the OAuth flow (same as shoonya_adapter.py), NOT direct login.
Direct login (api.login) does NOT work for OAuth accounts.

Steps:
  1. Get auth code:  python scripts/shoonya_login.py --open
  2. Get token:      python scripts/shoonya_login.py --code YOUR_CODE
  3. Run this file:  python execution_node/check.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from execution_node.shoonya_adapter import (
    _ShoonyaApi, load_token, get_auth_url,
    USER_ID, TOKEN_FILE,
)

print("=" * 50)
print("  Shoonya Connection Check")
print("=" * 50)

# 1. Load saved token
token = load_token()
if not token:
    print(f"\nNo token found at: {TOKEN_FILE}")
    print(f"\nGet one:")
    print(f"  1. Open: {get_auth_url()}")
    print(f"  2. Login → copy code from redirect URL")
    print(f"  3. python scripts/shoonya_login.py --code YOUR_CODE")
    sys.exit(1)

print(f"\nToken loaded : {token[:30]}...")

# 2. Inject token into API
api = _ShoonyaApi()
api.injectOAuthHeader(token, USER_ID, USER_ID)
print("Session      : ready\n")

# 3. Test: account limits
print("Testing get_limits()...")
limits = api.get_limits()
if limits and limits.get("stat") == "Ok":
    print(f"  Cash        : {limits.get('cash', 'n/a')}")
    print(f"  Net         : {limits.get('net', 'n/a')}")
    print(f"  Margin used : {limits.get('marginused', 'n/a')}")
else:
    print(f"  Response: {limits}")

# 4. Test: live quote (NIFTY)
print("\nTesting get_quotes(NSE, 26000) — NIFTY...")
quote = api.get_quotes("NSE", "26000")
if quote and quote.get("stat") == "Ok":
    print(f"  LTP  : {quote.get('lp', 'n/a')}")
    print(f"  High : {quote.get('h',  'n/a')}")
    print(f"  Low  : {quote.get('l',  'n/a')}")
else:
    print(f"  Response: {quote}")

print("\n" + "=" * 50)
print("  Done.")
print("=" * 50)
