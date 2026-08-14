"""Phase 11 plan 11-10 — Superadmin cross-team brain endpoints (BMO-10 + BMO-11).

Five endpoints gated by ``assert_is_superadmin`` (deps.py — wraps the
existing ``_is_admin`` predicate against ``ADMIN_USER_SUBS``):

  - GET /v1/admin/brain/overview — counts x entity_type x truth_level per team
  - GET /v1/admin/brain/storage — PG rows + Qdrant points + MinIO bytes per team
  - GET /v1/admin/brain/activity?days=N — events/day per team (zero-filled)
  - GET /v1/admin/brain/sources?days=N — source -> count per team
  - GET /v1/admin/brain/events?team_slug=X&... — drill-down with audit

Audit semantics (drill-down endpoint only):
  1. team_slug is REQUIRED — Query(..., min_length=1) returns 422 if absent.
  2. The audit row is written + session.flush()ed BEFORE the data query.
  3. If audit write fails, raise 500 and rollback; NO data is served.
  4. payload.actor_sub is the FIRST key — captures bridge service identity
     when actor_user_id IS NULL (MAJOR-1 invariant: bridge JWTs auditable).

Aggregate endpoints do NOT write audit per call (cross-team aggregates with
no per-team content payload — see CONTEXT.md).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import write_audit
from app.db.minio import get_minio_client
from app.db.qdrant import get_qdrant_client
from app.deps import (
    _user_id_from_principal,
    assert_is_superadmin,
    get_current_principal,
    get_session,
)
from app.repos.brain_metrics import (
    get_activity_per_team,
    get_overview_per_team,
    get_sources_per_team,
    get_storage_per_team,
)
from app.routes.brain import _build_list_query
from app.schemas.admin_brain import (
    TeamActivityOut,
    TeamOverviewOut,
    TeamSourcesOut,
    TeamStorageOut,
)
from app.schemas.brain import BrainEventListOut, BrainEventOut

router = APIRouter(prefix="/admin/brain", tags=["admin-brain"])


@router.get("/overview", response_model=list[TeamOverviewOut])
async def overview(
    session: AsyncSession = Depends(get_session),
    _: None = Depends(assert_is_superadmin),
) -> list[dict[str, Any]]:
    """Cross-team counts x entity_type x truth_level matrix."""
    return await get_overview_per_team(session)


@router.get("/storage", response_model=list[TeamStorageOut])
async def storage(
    session: AsyncSession = Depends(get_session),
    _: None = Depends(assert_is_superadmin),
) -> list[dict[str, Any]]:
    """Per-team Postgres rows + Qdrant points + MinIO bytes."""
    qdrant = get_qdrant_client()
    minio = get_minio_client()
    return await get_storage_per_team(session, qdrant, minio)


@router.get("/activity", response_model=list[TeamActivityOut])
async def activity(
    days: int = Query(30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
    _: None = Depends(assert_is_superadmin),
) -> list[dict[str, Any]]:
    """Per-team events/day for the last days days, zero-filled."""
    return await get_activity_per_team(session, days=days)


@router.get("/sources", response_model=list[TeamSourcesOut])
async def sources(
    days: int = Query(30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
    _: None = Depends(assert_is_superadmin),
) -> list[dict[str, Any]]:
    """Per-team source-label -> count for the last days days."""
    return await get_sources_per_team(session, days=days)


@router.get("/events", response_model=BrainEventListOut)
async def events_drilldown(
    request: Request,
    team_slug: str = Query(..., min_length=1, max_length=64),
    session: AsyncSession = Depends(get_session),
    principal: dict[str, Any] = Depends(get_current_principal),
    _: None = Depends(assert_is_superadmin),
    entity_type: list[str] | None = Query(None),
    truth_level: list[str] | None = Query(None),
    source: list[str] | None = Query(None),
    created_by: UUID | None = Query(None),
    q: str | None = Query(None, min_length=1, max_length=200),
    include_deleted: bool = Query(False),
    since: str | None = Query(None),
    cursor: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
) -> BrainEventListOut:
    """Drill-down into any team's brain events. Bypasses X-Team-Scope guard.

    SYNCHRONOUS audit write BEFORE any data read:
      - audit_log row written + session.flush() called.
      - If the audit write or flush fails, raise 500 and DO NOT serve data.

    This guarantees no superadmin content access is served without an audit trail.
    """
    # 1) Synchronous audit write FIRST — guarantees no read without an audit row.
    actor_user_id = _user_id_from_principal(principal)
    try:
        await write_audit(
            session,
            actor_user_id=actor_user_id,
            # team_scope on audit_log = the TARGET team, not the actor's
            team_scope=team_slug,
            action="superadmin_brain_access",
            target_id=None,
            payload={
                # actor_sub FIRST — captures bridge identity when actor_user_id IS NULL
                # (MAJOR-1 invariant: bridge JWTs must remain auditable).
                "actor_sub": principal.get("sub"),
                "actor_kind": principal.get("kind"),
                "endpoint": "/v1/admin/brain/events",
                "query_params": dict(request.query_params),
                "target_team_slug": team_slug,
            },
        )
        # Surface DB errors NOW — before the data read.
        await session.flush()
    except Exception as exc:
        # Do not leak data without an audit row.
        await session.rollback()
        raise HTTPException(
            status_code=500,
            detail="audit log write failed; access denied",
        ) from exc

    # 2) Parse since at the boundary so we can return 422 with a clear message
    # rather than the generic FastAPI one.
    since_dt: datetime | None = None
    if since is not None:
        try:
            since_dt = datetime.fromisoformat(since)
        except ValueError as exc:
            raise HTTPException(
                422, f"invalid since ISO-8601 timestamp: {since!r}"
            ) from exc

    # 3) Reuse the shared filter+page query builder from routes/brain.py with
    # team_slug as the effective team_scope. Same filter semantics as the
    # team-scoped GET /v1/brain/events.
    items, next_cursor = await _build_list_query(
        session,
        team_scope=team_slug,
        entity_type=entity_type,
        truth_level=truth_level,
        source=source,
        created_by=created_by,
        q=q,
        include_deleted=include_deleted,
        since=since_dt,
        cursor=cursor,
        limit=limit,
    )

    # 4) Commit audit row + return data.
    await session.commit()
    return BrainEventListOut(
        items=[BrainEventOut(**dict(r)) for r in items],
        next_cursor=next_cursor,
    )
