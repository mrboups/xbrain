"""/v1/me — current authenticated principal + per-user Granola API key management (D1 Phase 8)."""

import hashlib
import secrets
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import write_audit
from app.deps import get_current_principal, get_session
from app.routes.granola_integration import _require_granola_fernet

log = structlog.get_logger(__name__)
router = APIRouter()


# ── /v1/me — existing endpoint (extended for API token kind) ────────────────


@router.get("/me")
async def me(principal: dict[str, Any] = Depends(get_current_principal)) -> dict[str, Any]:
    if principal.get("kind") == "user_api_token":
        u = principal["user"]
        return {
            "kind": "user_api_token",
            "id": str(u.id),
            "source_user_id": u.source_user_id,
            "email": u.email,
            "display_name": u.display_name,
            # Phase 1b — extension uses these to decide whether to surface the
            # "Link GitHub" button (null → not linked yet).
            "github_username": getattr(u, "github_username", None),
            "github_id": getattr(u, "github_id", None),
            "api_token_team_scope": principal.get("api_token_team_scope"),
        }
    if principal["kind"] == "user":
        u = principal["user"]
        return {
            "kind": "user",
            "id": str(u.id),
            "source_user_id": u.source_user_id,
            "email": u.email,
            "display_name": u.display_name,
            "github_username": getattr(u, "github_username", None),
            "github_id": getattr(u, "github_id", None),
        }
    return {
        "kind": "bridge",
        "sub": principal.get("sub"),
        "team_scope": principal.get("team_scope"),
        "iss": principal["claims"].get("iss"),
    }


# ── /v1/me/granola-key — D1 Phase 8 ────────────────────────────────────────


class GranolaKeyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: str = Field(..., min_length=8, max_length=512)
    team_scope: str = Field(..., min_length=1, max_length=64)


class GranolaKeyStatus(BaseModel):
    connected: bool
    team_scope: str | None
    last_polled_at: datetime | None


def _require_user(principal: dict[str, Any]) -> Any:
    """Reject bridge JWTs — Granola key belongs to a real user."""
    if principal.get("kind") != "user":
        raise HTTPException(403, "User authentication required")
    user = principal.get("user")
    if user is None:
        raise HTTPException(403, "User authentication required")
    return user


@router.post("/me/granola-key", response_model=GranolaKeyStatus, status_code=201)
async def set_my_granola_key(
    body: GranolaKeyBody,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Store/replace the user's personal Granola API key (Fernet-encrypted at rest).

    UPSERT on user_id — one key per user. If the user re-submits, the previous
    encrypted ciphertext is replaced atomically. Re-enables the connection if it
    was previously soft-disabled via DELETE.

    Auth: user JWT only — bridge service principals are rejected.
    """
    user = _require_user(principal)
    fernet = _require_granola_fernet()
    encrypted = fernet.encrypt(body.api_key.encode()).decode()

    row = (await session.execute(sa.text("""
        INSERT INTO granola_user_connections (user_id, team_scope, api_key_enc, enabled)
        VALUES (:uid, :ts, :key, true)
        ON CONFLICT (user_id) DO UPDATE
        SET api_key_enc = EXCLUDED.api_key_enc,
            team_scope = EXCLUDED.team_scope,
            enabled = true,
            updated_at = now()
        RETURNING team_scope, last_polled_at
    """), {
        "uid": str(user.id),
        "ts": body.team_scope,
        "key": encrypted,
    })).mappings().fetchone()

    await write_audit(
        session,
        actor_user_id=user.id,
        team_scope=body.team_scope,
        action="me.granola_key.upserted",
        target_id=str(user.id),
        payload={"team_scope": body.team_scope},
    )
    await session.commit()

    log.info("me.granola_key.upserted", user_id=str(user.id), team_scope=body.team_scope)
    return GranolaKeyStatus(
        connected=True,
        team_scope=row["team_scope"],
        last_polled_at=row["last_polled_at"],
    )


@router.get("/me/granola-key", response_model=GranolaKeyStatus)
async def get_my_granola_key_status(
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Return the user's Granola connection status. Never returns the plaintext key.

    Auth: user JWT only.
    """
    user = _require_user(principal)
    row = (await session.execute(sa.text("""
        SELECT team_scope, last_polled_at, enabled
        FROM granola_user_connections
        WHERE user_id = :uid
        LIMIT 1
    """), {"uid": str(user.id)})).mappings().fetchone()

    if row is None or not row["enabled"]:
        return GranolaKeyStatus(connected=False, team_scope=None, last_polled_at=None)
    return GranolaKeyStatus(
        connected=True,
        team_scope=row["team_scope"],
        last_polled_at=row["last_polled_at"],
    )


@router.delete("/me/granola-key", status_code=204)
async def delete_my_granola_key(
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Soft-delete the user's Granola connection (enabled=false).

    The encrypted key remains stored but the poller skips it (filter enabled=true
    in 08-04). A subsequent POST re-enables the connection with a new key.

    Auth: user JWT only.
    """
    user = _require_user(principal)
    row = (await session.execute(sa.text("""
        UPDATE granola_user_connections
        SET enabled = false, updated_at = now()
        WHERE user_id = :uid AND enabled = true
        RETURNING team_scope
    """), {"uid": str(user.id)})).fetchone()

    if row is None:
        # Nothing to delete — return 204 idempotently
        return None

    await write_audit(
        session,
        actor_user_id=user.id,
        team_scope=row.team_scope,
        action="me.granola_key.disabled",
        target_id=str(user.id),
        payload={},
    )
    await session.commit()
    log.info("me.granola_key.disabled", user_id=str(user.id))
    return None


# ── /v1/me/api-token — personal API token CRUD ──────────────────────────────


class ApiTokenCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Empty string is the MULTI-TEAM sentinel, and it is the default here on purpose.
    #
    # `deps.get_team_scope` reads it as "this token is not pinned to one team, so fall
    # through to the team_members membership check" — the same sentinel
    # `services/api_tokens.mint_xbt_for_user` writes for every GitHub and local sign-in.
    #
    # It used to be REQUIRED with min_length=1, which made the multi-team sentinel
    # unreachable through this route. Every web and extension sign-in therefore minted a
    # token pinned to the literal string "default", which matches no real team slug — so
    # the first feature to send a genuine slug in X-Team-Scope (media upload) got a 403
    # on both surfaces. A token that cannot name any team it can act in is not a scope,
    # it is a dead end.
    #
    # Pinning is still available: pass a real slug to get a single-team token.
    team_scope: str = Field(default="", max_length=64)
    name: str = Field(default="default", min_length=1, max_length=128)


class ApiTokenCreated(BaseModel):
    id: str
    token: str  # plaintext — returned ONCE only
    team_scope: str
    name: str
    created_at: datetime


class ApiTokenInfo(BaseModel):
    id: str
    team_scope: str
    name: str
    created_at: datetime
    last_used_at: datetime | None


@router.post("/me/api-token", response_model=ApiTokenCreated, status_code=201)
async def create_api_token(
    body: ApiTokenCreateBody,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Generate a personal API token (xbt_ prefix) for the authenticated user.

    The plaintext token is returned ONCE. Store it immediately — it cannot be retrieved again.
    Auth: user JWT only (Google OIDC or GitHub OAuth).
    """
    user = _require_user(principal)
    raw_token = "xbt_" + secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    row = (await session.execute(sa.text("""
        INSERT INTO user_api_tokens (user_id, token_hash, team_scope, name)
        VALUES (:uid, :hash, :ts, :name)
        RETURNING id, team_scope, name, created_at
    """), {
        "uid": str(user.id),
        "hash": token_hash,
        "ts": body.team_scope,
        "name": body.name,
    })).mappings().fetchone()

    await session.commit()
    log.info("me.api_token.created", user_id=str(user.id), team_scope=body.team_scope, name=body.name)

    return ApiTokenCreated(
        id=str(row["id"]),
        token=raw_token,
        team_scope=row["team_scope"],
        name=row["name"],
        created_at=row["created_at"],
    )


@router.get("/me/api-token", response_model=list[ApiTokenInfo])
async def list_api_tokens(
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """List non-revoked personal API tokens for the authenticated user.

    Never returns plaintext tokens.
    Auth: user JWT only.
    """
    user = _require_user(principal)
    rows = (await session.execute(sa.text("""
        SELECT id, team_scope, name, created_at, last_used_at
        FROM user_api_tokens
        WHERE user_id = :uid AND revoked_at IS NULL
        ORDER BY created_at DESC
    """), {"uid": str(user.id)})).mappings().all()

    return [
        ApiTokenInfo(
            id=str(r["id"]),
            team_scope=r["team_scope"],
            name=r["name"],
            created_at=r["created_at"],
            last_used_at=r["last_used_at"],
        )
        for r in rows
    ]


@router.delete("/me/api-token/{token_id}", status_code=204)
async def revoke_api_token(
    token_id: str,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Soft-revoke a personal API token (sets revoked_at=now()).

    The token immediately becomes invalid for authentication.
    Auth: user JWT only.
    """
    user = _require_user(principal)
    result = await session.execute(sa.text("""
        UPDATE user_api_tokens
        SET revoked_at = now()
        WHERE id = :tid AND user_id = :uid AND revoked_at IS NULL
    """), {"tid": token_id, "uid": str(user.id)})

    await session.commit()
    if result.rowcount == 0:
        raise HTTPException(404, "Token not found or already revoked")

    log.info("me.api_token.revoked", user_id=str(user.id), token_id=token_id)
    return None
