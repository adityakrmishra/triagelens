"""
shopflow/auth_session.py
Password verification and magic-link token expiry checks.

Owner: Rohan Verma (@rohan-auth) <rohan@shopflow.io>
"""

from __future__ import annotations

import hashlib
import hmac


# --- Password hashing ---------------------------------------------------------

def verify_password_hash(raw: str, hashed: str) -> bool:
    """
    Securely compare a raw plaintext password against a stored SHA-256 hex
    digest using constant-time comparison to prevent timing attacks.

    Args:
        raw:    The plaintext password supplied by the user at login.
        hashed: The stored SHA-256 hex digest from the database.

    Returns:
        True if the password matches, False otherwise.
    """
    raw_digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return hmac.compare_digest(raw_digest, hashed)


# --- Magic-link TTL -----------------------------------------------------------

def check_magic_link_expiry(
    issued_at_epoch: int,
    current_epoch: int,
    ttl_minutes: int = 15,
) -> bool:
    """
    Validates whether a one-time login magic link token is still within its
    allowed time-to-live window.

    The link is considered valid when the elapsed time since issuance is
    strictly less than ``ttl_minutes`` minutes.

    Args:
        issued_at_epoch: Unix timestamp (seconds) at which the magic link was
                         generated and emailed to the user.
        current_epoch:   Current Unix timestamp (seconds) at the moment of
                         validation.
        ttl_minutes:     Maximum allowed age of the link in **minutes**
                         (default: 15).

    Returns:
        True  – the link is still valid (within TTL).
        False – the link has expired.
    """
    elapsed_seconds = current_epoch - issued_at_epoch

    # -- BUG #3 (PLANTED — DO NOT FIX) ----------------------------------------
    # `ttl_minutes` is compared directly against `elapsed_seconds` without
    # converting minutes → seconds (i.e., missing the  * 60  factor).
    # This means a 15-minute TTL silently becomes a 15-*second* TTL.
    # No error is raised; links simply expire almost immediately after
    # being issued, locking users out of their accounts.
    if elapsed_seconds < ttl_minutes:        # ← should be < (ttl_minutes * 60)
        return True   # link is valid
    # -- END BUG #3 ------------------------------------------------------------

    return False  # link has expired
