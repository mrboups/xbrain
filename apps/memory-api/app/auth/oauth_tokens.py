"""Pure helpers for the OAuth 2.1 Authorization Server — no DB, no I/O.

Mirrors the xbt_ pattern in ``app/deps.py``: tokens are minted as opaque
url-safe strings and only their SHA-256 hash is ever persisted. The raw token
is returned ONCE to the caller (the /oauth/token response) and never logged.

Token prefixes:
  - ``oat_``  OAuth access token (Custom-Connector bearer presented to mcp-brain)
  - ``ort_``  OAuth refresh token (rotated on each refresh)

``normalize_resource`` is the single source of truth for audience comparison:
the resource/audience value is ``rstrip("/")``-normalized on EVERY store and
EVERY lookup so a pasted ``.../mcp/`` never silently mismatches a stored
``.../mcp`` (RESEARCH.md Gotcha: trailing-slash → silent 401).
"""
from __future__ import annotations

import hashlib
import secrets

ACCESS_TOKEN_PREFIX = "oat_"
REFRESH_TOKEN_PREFIX = "ort_"


def hash_token(raw: str) -> str:
    """Return the lowercase hex SHA-256 of a raw token (matches deps.py xbt_)."""
    return hashlib.sha256(raw.encode()).hexdigest()


def mint_access_token() -> str:
    """Return a fresh raw access token: ``oat_`` + 32 url-safe bytes."""
    return ACCESS_TOKEN_PREFIX + secrets.token_urlsafe(32)


def mint_refresh_token() -> str:
    """Return a fresh raw refresh token: ``ort_`` + 32 url-safe bytes."""
    return REFRESH_TOKEN_PREFIX + secrets.token_urlsafe(32)


def normalize_resource(url: str) -> str:
    """Normalize an audience/resource URL for storage + comparison.

    Strips a single trailing slash so ``https://mcp.example/mcp/`` and
    ``https://mcp.example/mcp`` compare equal. Empty / None-ish inputs return
    an empty string (the caller validates presence separately).
    """
    if not url:
        return ""
    return url.rstrip("/")
