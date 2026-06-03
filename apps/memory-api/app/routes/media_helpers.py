"""Pure helpers for the media upload/serve endpoints (BL-003 slice 1 + 2).

Isolated here so unit tests can import them without triggering FastAPI route
registration (which requires python-multipart and other runtime deps that are
not installed in the local dev environment outside Docker).

BL-003 slice 2 adds short-lived HS256 signed tokens so bare ``<img src>``
elements can retrieve media objects without a Bearer header.  Token shape::

    {"scope": "media", "item_id": "<uuid>", "team_scope": "<slug>",
     "iat": <unix>, "exp": <unix+3600>}

Secret: settings.BRIDGE_SHARED_SECRET (already used by mcp-aggregate for
bridge JWTs — reusing it avoids adding a new secret to the .env).
"""
from __future__ import annotations

import mimetypes
import os
import time

_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB


def derive_key_and_mime(
    filename: str | None,
    content_type: str | None,
    team_scope: str,
    item_id: str,
) -> tuple[str, str, str]:
    """Return (object_key, mime, ext) for a given upload.

    All three values are derived deterministically from the inputs so
    callers can unit-test this without touching MinIO.
    """
    mime: str = (
        content_type
        or (mimetypes.guess_type(filename or "")[0])
        or "application/octet-stream"
    )
    raw_ext = os.path.splitext(filename or "")[1]
    if not raw_ext:
        raw_ext = mimetypes.guess_extension(mime) or ""
    # Normalise: keep only the first extension component (mimetypes can return
    # e.g. ".jpe" for image/jpeg — that's fine; we just strip any spurious chars).
    ext = raw_ext if raw_ext.startswith(".") else ("." + raw_ext if raw_ext else "")
    key = f"media/{team_scope}/{item_id}{ext}"
    return key, mime, ext


# ---------------------------------------------------------------------------
# Signed media token (BL-003 slice 2)
# ---------------------------------------------------------------------------


def mint_media_token(item_id: str, team_scope: str, ttl: int = 3600) -> str:
    """Mint a short-lived HS256 JWT that authorises ``GET /v1/media/{item_id}/img``.

    The token carries ``item_id`` and ``team_scope`` as claims so the serve
    endpoint can validate both without a database lookup before retrieving
    the object.

    Args:
        item_id:    UUID string of the memory_item.
        team_scope: Team slug embedded in the token.
        ttl:        Token lifetime in seconds (default 1 hour).

    Returns:
        URL-safe ASCII JWT string.
    """
    from authlib.jose import jwt as authlib_jwt  # lazy — not installed outside Docker

    from app.config import settings  # local import avoids circular at module level

    now = int(time.time())
    payload = {
        "scope": "media",
        "item_id": item_id,
        "team_scope": team_scope,
        "iat": now,
        "exp": now + ttl,
    }
    token = authlib_jwt.encode({"alg": "HS256"}, payload, settings.BRIDGE_SHARED_SECRET)
    return token.decode("ascii") if isinstance(token, bytes) else token


def verify_media_token(token: str, item_id: str) -> str:
    """Decode and validate a media token; return the ``team_scope`` claim.

    Raises:
        fastapi.HTTPException(403): if the token is missing, expired,
            malformed, has the wrong scope, or ``item_id`` claim mismatch.

    Returns:
        The ``team_scope`` claim from the validated token.
    """
    from fastapi import HTTPException  # local import — fastapi always present
    from authlib.jose import jwt as authlib_jwt  # lazy

    from app.config import settings

    try:
        claims = authlib_jwt.decode(token, settings.BRIDGE_SHARED_SECRET)
        claims.validate()  # validates exp, iat, nbf
    except Exception as exc:
        raise HTTPException(403, f"invalid or expired media token: {exc}") from exc

    if claims.get("scope") != "media":
        raise HTTPException(403, "media token has wrong scope")
    if claims.get("item_id") != item_id:
        raise HTTPException(403, "media token item_id mismatch")

    team_scope = claims.get("team_scope")
    if not team_scope:
        raise HTTPException(403, "media token missing team_scope")
    return str(team_scope)
