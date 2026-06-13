"""TOTP code generation for daily 2FA.

Wrapped here so we can swap the seed source (Secrets Manager / env) without
touching the main handler.
"""
import pyotp


def generate(seed: str) -> str:
    """Return the current 6-digit TOTP code for the given base32 seed."""
    return pyotp.TOTP(seed).now()
