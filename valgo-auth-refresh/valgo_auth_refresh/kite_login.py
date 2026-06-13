"""Kite-specific login helper. Used by the Lambda handler.

Kept separate so it can also be invoked manually during local dev for
producing an access_token to paste into .env.

Run locally:
    python -m valgo_auth_refresh.kite_login
"""
import os

from kiteconnect import KiteConnect


def manual_login() -> str:
    """Interactive login for local dev. Prints the access_token."""
    api_key = os.environ["KITE_API_KEY"]
    api_secret = os.environ["KITE_API_SECRET"]

    kite = KiteConnect(api_key=api_key)
    print("Open this URL, log in, and paste the request_token from the redirect:")
    print(kite.login_url())
    request_token = input("request_token: ").strip()

    data = kite.generate_session(request_token, api_secret=api_secret)
    print(f"\naccess_token: {data['access_token']}")
    print("Add to .env as KITE_ACCESS_TOKEN=...")
    return data["access_token"]


if __name__ == "__main__":
    manual_login()
