"""/v1/me/external-sessions — Phase 9 session-bridge external session registry.

Endpoints:
  - GET    /v1/me/external-sessions             — list caller's connected sessions (user principal only)
  - POST   /v1/me/external-sessions             — upsert (user OR bridge JWT carrying acting_user_sub)
  - DELETE /v1/me/external-sessions/{provider}  — revoke a session (user principal)

Notes:
  - User principal (kind in {"user", "user_api_token"}): uses principal["user"].id directly.
  - Bridge principal (kind == "bridge"): requires claims.acting_user_sub; resolves via
    users.source_user_id. This is the path used by apps/session-bridge when it
    POSTs an upsert on register-handshake (the contract is signed by
    BRIDGE_SHARED_SECRET, matching the Phase 7 pipeline pattern).
  - Cross-user isolation is enforced by always filtering `user_id = me.user_id`
    (T-09-04-02).
"""

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_current_principal, get_session

router = APIRouter()


# ── Pydantic models ─────────────────────────────────────────────────────────


class ExternalSession(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: UUID
    provider: str
    extension_id: str | None = None
    last_seen_at: datetime
    metadata: dict[str, Any] | None = None


class ExternalSessionUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(..., min_length=1, max_length=32)
    extension_id: str | None = Field(None, max_length=64)
    metadata: dict[str, Any] | None = None


# ── Principal → user_id resolver ────────────────────────────────────────────


async def _resolve_user_id(
    principal: dict[str, Any],
    db: AsyncSession,
) -> UUID:
    """Return the users.id this principal acts on behalf of, or raise 403."""
    kind = principal.get("kind")
    if kind in ("user", "user_api_token"):
        return principal["user"].id
    if kind == "bridge":
        claims = principal.get("claims") or {}
        acting_sub = claims.get("acting_user_sub")
        if not acting_sub:
            raise HTTPException(403, "bridge JWT missing acting_user_sub claim")
        row = (
            await db.execute(
                text("SELECT id FROM users WHERE source_user_id = :sub"),
                {"sub": acting_sub},
            )
        ).fetchone()
        if row is None:
            raise HTTPException(404, "acting_user_sub not found")
        return row.id
    raise HTTPException(403, f"unsupported principal kind: {kind}")


# ── Routes ──────────────────────────────────────────────────────────────────


@router.get("/me/external-sessions", response_model=list[ExternalSession])
async def list_external_sessions(
    principal: dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_session),
) -> list[ExternalSession]:
    # GET requires a user-bearing principal (popup UX). Bridge JWTs are rejected.
    if principal.get("kind") == "bridge":
        raise HTTPException(403, "GET requires a user principal")
    user_id = await _resolve_user_id(principal, db)
    rows = (
        await db.execute(
            text("""
                SELECT id, provider, extension_id, last_seen_at, metadata
                FROM user_external_sessions
                WHERE user_id = :uid
                ORDER BY last_seen_at DESC
            """),
            {"uid": user_id},
        )
    ).mappings().all()
    return [ExternalSession(**dict(r)) for r in rows]


@router.post("/me/external-sessions", response_model=ExternalSession)
async def upsert_external_session(
    body: ExternalSessionUpsert,
    principal: dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_session),
) -> ExternalSession:
    """Upsert on UNIQUE(user_id, provider) — refreshes last_seen_at + metadata + extension_id."""
    user_id = await _resolve_user_id(principal, db)
    meta_json = None if body.metadata is None else json.dumps(body.metadata)
    row = (
        await db.execute(
            text("""
                INSERT INTO user_external_sessions (user_id, provider, extension_id, metadata)
                VALUES (:uid, :provider, :ext_id, CAST(:meta AS JSONB))
                ON CONFLICT ON CONSTRAINT uq_external_sessions_user_provider DO UPDATE
                  SET extension_id = EXCLUDED.extension_id,
                      metadata     = EXCLUDED.metadata,
                      last_seen_at = now()
                RETURNING id, provider, extension_id, last_seen_at, metadata
            """),
            {
                "uid": user_id,
                "provider": body.provider,
                "ext_id": body.extension_id,
                "meta": meta_json,
            },
        )
    ).mappings().fetchone()
    await db.commit()
    return ExternalSession(**dict(row))


@router.delete(
    "/me/external-sessions/{provider}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_external_session(
    provider: str,
    principal: dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_session),
) -> None:
    user_id = await _resolve_user_id(principal, db)
    result = await db.execute(
        text(
            "DELETE FROM user_external_sessions "
            "WHERE user_id = :uid AND provider = :prov"
        ),
        {"uid": user_id, "prov": provider},
    )
    if result.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="session_not_found")
    await db.commit()
