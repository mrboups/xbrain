"""Async DB layer for the OAuth 2.1 Authorization Server.

All functions take an ``AsyncSession`` and use ``sa.text(...)`` parameterized
SQL, consistent with ``app/deps.py`` and ``app/routes/auth_github.py``. Raw
tokens are NEVER logged or stored — only their SHA-256 hashes (see
``oauth_tokens.hash_token``). Resource/audience values are normalized via
``normalize_resource`` on every store and every lookup.

Backs three tables created in migration ``0022_oauth_as_tables``:
``oauth_clients``, ``oauth_authorization_codes``, ``oauth_access_tokens``.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.oauth_tokens import (
    hash_token,
    mint_access_token,
    mint_refresh_token,
    normalize_resource,
)

# Claude.ai's fixed connector callback. Used only when a registration is run in
# strict mode; default registration accepts any redirect_uris (public client).
CLAUDE_CALLBACK = "https://claude.ai/api/mcp/auth_callback"

AUTH_CODE_TTL = timedelta(minutes=5)
ACCESS_TOKEN_TTL = timedelta(hours=1)
REFRESH_TOKEN_TTL = timedelta(days=30)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


# === Client registration (RFC 7591) ===

async def register_client(
    session: AsyncSession,
    *,
    client_name: str | None,
    redirect_uris: list[str],
    grant_types: list[str] | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Persist a new public client and return its registration dict.

    Public client (no client_secret). ``strict=True`` rejects a registration
    whose redirect_uris does not include Claude.ai's fixed callback; the default
    (``strict=False``) accepts any non-empty redirect_uris list.
    """
    if not redirect_uris:
        raise ValueError("redirect_uris must not be empty")
    if strict and CLAUDE_CALLBACK not in redirect_uris:
        raise ValueError("redirect_uris must include the Claude.ai callback")

    client_id = "oac_" + secrets.token_urlsafe(24)
    grants = grant_types or ["authorization_code", "refresh_token"]
    await session.execute(
        sa.text("""
            INSERT INTO oauth_clients (client_id, client_name, redirect_uris, grant_types)
            VALUES (:cid, :name, :uris, :grants)
        """),
        {"cid": client_id, "name": client_name, "uris": redirect_uris, "grants": grants},
    )
    return {
        "client_id": client_id,
        "client_name": client_name,
        "redirect_uris": redirect_uris,
        "grant_types": grants,
        "token_endpoint_auth_method": "none",
    }


async def get_client(session: AsyncSession, client_id: str) -> dict[str, Any] | None:
    """Return {client_id, client_name, redirect_uris, grant_types} or None."""
    row = (await session.execute(
        sa.text("""
            SELECT client_id, client_name, redirect_uris, grant_types
            FROM oauth_clients WHERE client_id = :cid
        """),
        {"cid": client_id},
    )).mappings().fetchone()
    if row is None:
        return None
    return dict(row)


async def is_redirect_uri_registered(
    session: AsyncSession, *, client_id: str, redirect_uri: str
) -> bool:
    """True iff the client exists AND redirect_uri is one of its registered URIs."""
    client = await get_client(session, client_id)
    if client is None:
        return False
    return redirect_uri in (client.get("redirect_uris") or [])


# === Authorization codes (one-time, PKCE-bound) ===

async def create_auth_code(
    session: AsyncSession,
    *,
    client_id: str,
    user_id: UUID,
    team_scope: str,
    resource: str,
    code_challenge: str,
    redirect_uri: str,
    scope: str,
) -> str:
    """Insert a fresh one-time authorization code; return the raw code string."""
    code = "oac_code_" + secrets.token_urlsafe(32)
    expires_at = _now() + AUTH_CODE_TTL
    await session.execute(
        sa.text("""
            INSERT INTO oauth_authorization_codes
                (code, client_id, user_id, team_scope, resource,
                 code_challenge, redirect_uri, scope, expires_at)
            VALUES
                (:code, :cid, :uid, :team, :resource,
                 :challenge, :redirect, :scope, :expires)
        """),
        {
            "code": code,
            "cid": client_id,
            "uid": str(user_id),
            "team": team_scope,
            "resource": normalize_resource(resource),
            "challenge": code_challenge,
            "redirect": redirect_uri,
            "scope": scope,
            "expires": expires_at,
        },
    )
    return code


async def consume_auth_code(session: AsyncSession, code: str) -> dict[str, Any] | None:
    """Atomically mark a live code used and return its row; None if unusable.

    One-time use: the UPDATE sets ``used_at`` only ``WHERE used_at IS NULL AND
    expires_at > now()`` and RETURNs the row. A second exchange of the same code
    matches no row (used_at already set) and returns None — defeating replay.
    """
    row = (await session.execute(
        sa.text("""
            UPDATE oauth_authorization_codes
            SET used_at = now()
            WHERE code = :code
              AND used_at IS NULL
              AND expires_at > now()
            RETURNING code, client_id, user_id, team_scope, resource,
                      code_challenge, redirect_uri, scope, expires_at
        """),
        {"code": code},
    )).mappings().fetchone()
    if row is None:
        return None
    return dict(row)


# === Access + refresh tokens ===

async def mint_and_store_access_token(
    session: AsyncSession,
    *,
    client_id: str,
    user_id: UUID,
    team_scope: str,
    resource: str,
    scope: str,
    source: str = "claude.ai-connector",
) -> dict[str, str]:
    """Mint an oat_ access token + ort_ refresh token; store hashes; return raw.

    The raw tokens are returned ONCE (for the /oauth/token response) and never
    persisted or logged. ``resource`` is normalized before storage so the stored
    audience matches what introspection compares against.
    """
    raw_access = mint_access_token()
    raw_refresh = mint_refresh_token()
    now = _now()
    await session.execute(
        sa.text("""
            INSERT INTO oauth_access_tokens
                (token_hash, client_id, user_id, team_scope, resource, scope,
                 source, created_at, expires_at,
                 refresh_token_hash, refresh_token_expires_at)
            VALUES
                (:thash, :cid, :uid, :team, :resource, :scope,
                 :source, :created, :expires,
                 :rhash, :rexpires)
        """),
        {
            "thash": hash_token(raw_access),
            "cid": client_id,
            "uid": str(user_id),
            "team": team_scope,
            "resource": normalize_resource(resource),
            "scope": scope,
            "source": source,
            "created": now,
            "expires": now + ACCESS_TOKEN_TTL,
            "rhash": hash_token(raw_refresh),
            "rexpires": now + REFRESH_TOKEN_TTL,
        },
    )
    return {
        "access_token": raw_access,
        "refresh_token": raw_refresh,
        "expires_in": int(ACCESS_TOKEN_TTL.total_seconds()),
        "scope": scope,
    }


async def rotate_refresh_token(
    session: AsyncSession, *, raw_refresh: str
) -> dict[str, str] | None:
    """Validate + rotate a refresh token. Returns new raw token bundle or None.

    Looks up the live (non-revoked, non-expired) row by refresh_token_hash,
    revokes it, and mints a fresh access+refresh pair bound to the same
    user/team/resource/scope/source. Returns None when the refresh token is
    unknown, revoked, or expired.
    """
    row = (await session.execute(
        sa.text("""
            SELECT client_id, user_id, team_scope, resource, scope, source
            FROM oauth_access_tokens
            WHERE refresh_token_hash = :rhash
              AND revoked_at IS NULL
              AND (refresh_token_expires_at IS NULL OR refresh_token_expires_at > now())
        """),
        {"rhash": hash_token(raw_refresh)},
    )).mappings().fetchone()
    if row is None:
        return None
    # Revoke the old row (invalidate the rotated-out refresh + its access token).
    await session.execute(
        sa.text("""
            UPDATE oauth_access_tokens SET revoked_at = now()
            WHERE refresh_token_hash = :rhash AND revoked_at IS NULL
        """),
        {"rhash": hash_token(raw_refresh)},
    )
    return await mint_and_store_access_token(
        session,
        client_id=row["client_id"],
        user_id=row["user_id"],
        team_scope=row["team_scope"],
        resource=row["resource"],
        scope=row["scope"],
        source=row["source"],
    )


async def introspect_token(session: AsyncSession, raw: str) -> dict[str, Any]:
    """RFC 7662 introspection for an oat_ access token. Never raises for a bad token.

    Returns ``{active: True, sub, team_scope, aud, scope, source}`` for a live,
    non-revoked, non-expired token; ``{active: False}`` for anything else
    (unknown / revoked / expired). The single indexed lookup is on token_hash.
    ``sub`` is the user's ``source_user_id`` (the canonical principal key used
    by the bridge JWT path downstream).
    """
    if not raw or not raw.startswith("oat_"):
        return {"active": False}
    try:
        row = (await session.execute(
            sa.text("""
                SELECT t.team_scope, t.resource, t.scope, t.source,
                       u.source_user_id
                FROM oauth_access_tokens t
                JOIN users u ON u.id = t.user_id
                WHERE t.token_hash = :thash
                  AND t.revoked_at IS NULL
                  AND (t.expires_at IS NULL OR t.expires_at > now())
            """),
            {"thash": hash_token(raw)},
        )).mappings().fetchone()
    except Exception:
        # Never reveal failure modes / never raise to the caller.
        return {"active": False}
    if row is None:
        return {"active": False}
    return {
        "active": True,
        "sub": row["source_user_id"],
        "team_scope": row["team_scope"],
        "aud": row["resource"],
        "scope": row["scope"],
        "source": row["source"],
    }
