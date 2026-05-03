"""PKCE (RFC 7636) helpers for OAuth flows.

Used by oauth_remote and any future provider OAuth implementation.
"""

from __future__ import annotations

import base64
import hashlib
import secrets


def generate_pkce_pair() -> tuple[str, str]:
    """Return (verifier, challenge) where challenge = base64url(sha256(verifier)).

    Verifier is 43+ chars of url-safe characters per RFC 7636.
    """
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge
