"""`/v1/brain/*` — universal brain monitor (Phase 11).

This module ships the read endpoint `GET /v1/brain/events` (BMO-02 +
BMO-03) and the three mutation endpoints
`PATCH/DELETE/POST .../restore /v1/brain/events/{entity_type}/{entity_id}`
(BMO-04 / BMO-05 / BMO-06).

The read path streams from the `v_brain_events` SQL view introduced in
migration 0018 — a UNION ALL over the 7 entity types tracked by xbrain
(memory_item, granola_note, conversation, message, team_message, task,
contact). The mutation path dispatches through `app.repos.brain` to
land the change on the correct base table while preserving the view's
team-scope guarantee (the dispatch resolver is a closed allow-list —
never user-controlled — so the f-string SQL inside the repo cannot be
tampered with from the URL path).

Authorisation for the mutation endpoints lives in
`app.deps.assert_can_edit_brain_event`: bridge service JWTs and global
admins are allowed unconditionally; per-team admins are allowed within
their team; non-admin members are allowed when they are the row's
`created_by`. Rows whose `created_by` is NULL (memory_items, messages,
contacts) are admin-only — locking the Phase 11 "Permissions model —
option A" decision from `11-CONTEXT.md`.

Every mutation writes one row to `audit_log` via the project's
`app.audit.write_audit` helper (target_id carries the entity uuid;
payload carries entity_type + the per-action diff). For memory_items
(including granola_note) the soft-delete / restore path ALSO flips the
Qdrant payload via `MemoryProvider.mark_deleted / mark_restored`. That
side-effect is best-effort: a Qdrant failure logs a warning and the
Postgres write is the source of truth — the daily janitor (plan 11-07)
reconciles divergence.

Pagination is cursor-based on the tuple `(created_at, entity_type,
entity_id)` ordered DESC / ASC / ASC. The composite secondaries are
required because timestamps collide in practice (synthetic seed data,
concurrent inserts, batched fixture loaders). The cursor token is the
base64-encoded JSON of those three fields — opaque to clients but
trivially decodable for debugging.

Filter semantics:

- `entity_type[]`, `truth_level[]`, `source[]` — repeated query params,
  matched with `ANY(:array)`.
- `created_by` — single UUID, exact match (NULL entity types are
  excluded by this filter, which is desirable: filtering by author only
  makes sense for entity types that have an author).
- `q` — ILIKE on the `preview` column (the view already truncates
  `content`/`title` to 200 chars with `LEFT(..., 200)`, so the
  full-text scan is bounded).
- `include_deleted` — default false, which adds `deleted_at IS NULL`
  to the query. Passing `true` returns every row including soft-
  deleted ones (used by the Brain Monitor "show deleted" toggle and
  the janitor's pre-purge sanity sweep).
- `since` — `created_at >= :since` filter for the polling path
  (Brain Monitor UI fetches new rows since the latest-seen timestamp).
"""

import base64
import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from xbrain_memory import MemoryProvider

from app.audit import write_audit
from app.deps import (
    _user_id_from_principal,
    assert_can_edit_brain_event,
    get_current_principal,
    get_memory_provider,
    get_session,
    get_team_scope,
)
from app.repos.brain import (
    ALLOWED_ENTITY_TYPES,
    fetch_event_row,
    restore_entity,
    soft_delete_entity,
    update_truth_level,
)
from app.routes.media_helpers import mint_media_token
from app.schemas.brain import BrainEventListOut, BrainEventOut, BrainIngestRequest, TruthLevelPatchBody
from app.services import background
from app.services import brain_ingest as brain_ingest_svc

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Entity types whose underlying rows also live in Qdrant ────────────
#
# Only memory_items (and its `granola_note` view-side alias) mirror into
# the vector store. Soft-delete + restore on those entity types fan out
# to Qdrant via `MemoryProvider.mark_deleted / mark_restored`. Every
# other entity type stays Postgres-only and skips the Qdrant fan-out.
_QDRANT_BACKED_ENTITY_TYPES = frozenset({"memory_item", "granola_note"})


# ── Cursor helpers ────────────────────────────────────────────────────
#
# Cursor format: base64-url-encoded JSON `{"ts": ISO8601, "et": str,
# "id": UUID-str}`. Encoding choices:
#
# - URL-safe base64 (no `+` / `/` characters) so the cursor can ride in a
#   query string without %-escaping.
# - No padding stripping — keep the raw `=`s; Pydantic / FastAPI handle
#   them fine, and removing them complicates the decode round-trip.
# - JSON wrapper (rather than a delimited string) so the field order
#   never depends on a parsing convention. Adding a future cursor field
#   only requires teaching `_decode_cursor` to read it.


def _encode_cursor(ts: datetime, entity_type: str, entity_id: UUID) -> str:
    payload = {
        "ts": ts.isoformat(),
        "et": entity_type,
        "id": str(entity_id),
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_cursor(token: str) -> tuple[datetime, str, UUID]:
    """Decode an opaque cursor token. Raises HTTPException(400) on malformed input."""
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
        payload = json.loads(raw)
        return (
            datetime.fromisoformat(payload["ts"]),
            str(payload["et"]),
            UUID(payload["id"]),
        )
    except Exception as e:
        # Don't 500 on a malformed cursor — a paginated client iterating
        # against an evolving cursor format would otherwise see scary
        # server errors. 400 + clear detail is the right contract.
        raise HTTPException(
            400, f"Malformed cursor token (expected base64-json triplet): {e}"
        ) from e


# ── GET /v1/brain/events ─────────────────────────────────────────────

# BL-003 slice 2 — strip the raw MinIO key from media before serialisation.
# The raw ``key`` (e.g. ``media/default/abc.jpg``) is an internal detail;
# callers only receive the safe subset + a tokenized URL that the serve
# endpoint validates.  The URL is relative (``/v1/…``) so the frontend
# prepends MEMORY_API_BASE.


def _enrich_event(ev: BrainEventOut) -> BrainEventOut:
    """Replace raw media dict with a safe subset + signed token URL.

    If ``ev.media`` is not set (non-memory_item rows or items without a
    media blob) the event is returned unchanged.
    """
    if ev.media and isinstance(ev.media, dict):
        m = ev.media
        ev.media = {
            "mime": m.get("mime"),
            "size": m.get("size"),
            "filename": m.get("filename"),
            "url": (
                f"/v1/media/{ev.entity_id}/img"
                f"?t={mint_media_token(str(ev.entity_id), ev.team_scope)}"
            ),
        }
    return ev


@router.get("/brain/events", response_model=BrainEventListOut)
async def list_brain_events(
    session: AsyncSession = Depends(get_session),
    team_scope: str = Depends(get_team_scope),
    entity_type: list[str] | None = Query(
        None,
        description="Repeatable. Filter to one or more of: memory_item, "
        "granola_note, conversation, message, team_message, task, contact.",
    ),
    truth_level: list[str] | None = Query(
        None,
        description="Repeatable. Filter to one or more of: EPHEMERAL, "
        "WORKING, VALIDATED, CANONICAL, PUBLIC.",
    ),
    source: list[str] | None = Query(
        None,
        description="Repeatable. Filter by source label (librechat, owui, "
        "granola, agent, manual, etc).",
    ),
    created_by: UUID | None = Query(
        None,
        description="Filter to rows authored by this user id. Excludes rows "
        "whose entity type carries no author column (memory_item, message, "
        "contact).",
    ),
    q: str | None = Query(
        None,
        min_length=1,
        max_length=200,
        description="Case-insensitive substring search on the preview column.",
    ),
    include_deleted: bool = Query(
        False,
        description="If false (default), soft-deleted rows are hidden. If "
        "true, every row is returned regardless of deleted_at.",
    ),
    since: datetime | None = Query(
        None,
        description="ISO 8601 — only rows with created_at >= since. Used by "
        "the Brain Monitor polling path.",
    ),
    cursor: str | None = Query(
        None,
        description="Opaque cursor token returned in the previous response's "
        "next_cursor. Pass to fetch the next page.",
    ),
    limit: int = Query(50, ge=1, le=200),
) -> BrainEventListOut:
    """Return a page of brain events, newest first, scoped to the caller's team.

    The endpoint is intentionally read-only — every mutation lives under
    the same prefix in plan 11-05 (PATCH/DELETE/restore). Authorisation
    for those mutations goes through ``assert_can_edit_brain_event``
    (deps.py); this list endpoint just enforces the team-scope
    membership check via the existing ``get_team_scope`` dependency.
    """
    items, next_cursor = await _build_list_query(
        session,
        team_scope=team_scope,
        entity_type=entity_type,
        truth_level=truth_level,
        source=source,
        created_by=created_by,
        q=q,
        include_deleted=include_deleted,
        since=since,
        cursor=cursor,
        limit=limit,
    )
    return BrainEventListOut(
        items=[_enrich_event(BrainEventOut(**dict(r))) for r in items],
        next_cursor=next_cursor,
    )


# ── Shared filter+page query builder (Phase 11 plan 11-10 reuses this) ─
#
# Extracted from list_brain_events so the /v1/admin/brain/events drill-down
# endpoint (plan 11-10, routes/admin_brain.py) can call the EXACT SAME query
# logic with team_scope=<arbitrary slug> after superadmin authorization
# bypasses the X-Team-Scope guard. The two callers MUST share an
# implementation so filter semantics never drift between team-scoped and
# cross-team reads.


async def _build_list_query(
    session: AsyncSession,
    *,
    team_scope: str,
    entity_type: list[str] | None = None,
    truth_level: list[str] | None = None,
    source: list[str] | None = None,
    created_by: UUID | None = None,
    q: str | None = None,
    include_deleted: bool = False,
    since: datetime | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], str | None]:
    """Build + execute the v_brain_events filter query.

    Returns ``(page_rows, next_cursor)`` where ``page_rows`` is a list of
    mappings (one per matched row, oldest-first within a single page) and
    ``next_cursor`` is the opaque base64 token to pass back for the next
    page, or None when the caller has reached the end of the feed.

    The text query uses named bind params throughout — no string
    interpolation of user input — which keeps the ILIKE pattern safe from
    injection (the ``q_pattern`` value is fully parameterised; only the
    surrounding ``%`` wildcards are concatenated in Python).
    """
    sql_parts: list[str] = ["SELECT * FROM v_brain_events WHERE team_scope = :ts"]
    params: dict[str, Any] = {"ts": team_scope}

    # NOTE on the brain tag (migration 0034): this feed deliberately does NOT
    # filter brain-tag rows. The tag keeps a message out of the CHAT; the note
    # itself is in the team's brain and every member can find it — that is the
    # owner's decision of 2026-08-05, and the UI copy tells the author so in as
    # many words. A filter here briefly existed and was removed on 2026-08-14:
    # it hid the `team_message` arm while the `memory_item` twin carrying the
    # same text stayed searchable one entity_type over, so it bought no privacy
    # and only made the product disagree with itself.

    if not include_deleted:
        sql_parts.append("AND deleted_at IS NULL")

    if entity_type:
        sql_parts.append("AND entity_type = ANY(:et_arr)")
        params["et_arr"] = entity_type

    if truth_level:
        sql_parts.append("AND truth_level = ANY(:tl_arr)")
        params["tl_arr"] = truth_level

    if source:
        sql_parts.append("AND source = ANY(:src_arr)")
        params["src_arr"] = source

    if created_by is not None:
        sql_parts.append("AND created_by = :cb")
        params["cb"] = str(created_by)

    if q:
        sql_parts.append("AND preview ILIKE :q_pattern")
        params["q_pattern"] = f"%{q}%"

    if since is not None:
        sql_parts.append("AND created_at >= :since")
        params["since"] = since

    if cursor:
        c_ts, c_et, c_id = _decode_cursor(cursor)
        # Tuple comparison gives us a single, lexicographic ordering that
        # exactly mirrors the ORDER BY below. Splitting it into three
        # ANDed scalar comparisons would either skip rows (too strict on
        # equality) or duplicate them (too loose). Postgres has supported
        # row-value comparisons since 7.x — no extension needed.
        sql_parts.append(
            "AND (created_at, entity_type, entity_id) < (:c_ts, :c_et, :c_id)"
        )
        params["c_ts"] = c_ts
        params["c_et"] = c_et
        params["c_id"] = str(c_id)

    sql_parts.append(
        "ORDER BY created_at DESC, entity_type ASC, entity_id ASC LIMIT :lim"
    )
    # Fetch one extra row to know whether a `next_cursor` should be emitted
    # — cheaper than COUNT(*) and avoids the dreaded "always emit a
    # cursor, last page is empty" bug.
    params["lim"] = limit + 1

    sql = " ".join(sql_parts)
    rows = (await session.execute(sa.text(sql), params)).mappings().all()

    has_more = len(rows) > limit
    page = rows[:limit]

    next_cursor: str | None = None
    if has_more and page:
        tail = page[-1]
        next_cursor = _encode_cursor(
            tail["created_at"], tail["entity_type"], tail["entity_id"]
        )

    return list(page), next_cursor


# ── Internal helpers shared by the three mutation endpoints ──────────


async def _qdrant_mark_deleted_safe(
    provider: MemoryProvider,
    entity_id: UUID,
    deleted_at: datetime,
) -> None:
    """Best-effort `mark_deleted` against Qdrant.

    Postgres is the source of truth. A Qdrant outage must NOT roll back
    the soft-delete on the PG side (otherwise the user clicks Delete
    and nothing happens). The janitor (plan 11-07) re-scans PG nightly
    and force-flips every Qdrant point whose PG twin is soft-deleted —
    that's the reconciliation pathway.
    """
    try:
        await provider.mark_deleted(str(entity_id), deleted_at)
    except Exception as exc:  # pragma: no cover — best-effort
        logger.warning(
            "qdrant mark_deleted failed for %s: %s — janitor will reconcile",
            entity_id,
            exc,
        )


async def _qdrant_mark_restored_safe(
    provider: MemoryProvider, entity_id: UUID
) -> None:
    """Best-effort `mark_restored` against Qdrant. Same rationale as
    `_qdrant_mark_deleted_safe`."""
    try:
        await provider.mark_restored(str(entity_id))
    except Exception as exc:  # pragma: no cover — best-effort
        logger.warning(
            "qdrant mark_restored failed for %s: %s — janitor will reconcile",
            entity_id,
            exc,
        )


def _reject_unknown_entity_type(entity_type: str) -> None:
    """Pre-route guard so a bad entity_type returns 400 immediately, before
    a DB round-trip. The repo also re-checks via `_resolve_table` — defence
    in depth, no behaviour change."""
    if entity_type not in ALLOWED_ENTITY_TYPES:
        raise HTTPException(400, f"unknown entity_type: {entity_type}")


# ── PATCH /v1/brain/events/{entity_type}/{entity_id} ─────────────────
#
# Sets the row's `truth_level`. Author or per-team admin only (see
# `assert_can_edit_brain_event` in deps.py for the full matrix). Soft-
# deleted rows cannot be patched — the route returns 404 the same way
# DELETE does, so the UI can treat both as "row is gone" without a
# special case.


@router.patch("/brain/events/{entity_type}/{entity_id}", response_model=BrainEventOut)
async def patch_truth_level(
    entity_type: str,
    entity_id: UUID,
    body: TruthLevelPatchBody,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
    team_scope: str = Depends(get_team_scope),
) -> BrainEventOut:
    _reject_unknown_entity_type(entity_type)

    row = await fetch_event_row(
        session,
        entity_type,
        entity_id,
        team_scope,
    )
    if row is None:
        raise HTTPException(404, "brain event not found in this team")
    if row["deleted_at"] is not None:
        # PATCH on a soft-deleted row would silently revive the row's
        # truth_level without un-deleting it — never what the caller wants.
        # The Brain Monitor UI explicitly uses POST .../restore for that.
        raise HTTPException(404, "brain event is soft-deleted; restore first")

    await assert_can_edit_brain_event(
        principal,
        created_by=row["created_by"],
        team_slug=team_scope,
        session=session,
    )

    updated = await update_truth_level(
        session, entity_type, entity_id, body.truth_level
    )
    if not updated:
        # Vanishing between fetch and update is a rare race (concurrent
        # hard-purge from the janitor) — surface as 404 so the client can
        # retry cleanly.
        raise HTTPException(404, "brain event vanished mid-update")

    await write_audit(
        session,
        actor_user_id=_user_id_from_principal(principal),
        team_scope=team_scope,
        action="brain.patch_truth_level",
        target_id=str(entity_id),
        payload={
            "entity_type": entity_type,
            "old_truth_level": row["truth_level"],
            "new_truth_level": body.truth_level,
        },
    )
    await session.commit()

    new_row = await fetch_event_row(
        session,
        entity_type,
        entity_id,
        team_scope,
    )
    if new_row is None:
        # Should not happen — same vanishing race as above; covered defensively.
        raise HTTPException(404, "brain event vanished after update")
    return BrainEventOut(**dict(new_row))


# ── DELETE /v1/brain/events/{entity_type}/{entity_id} ────────────────
#
# Soft-deletes the row. Sets `deleted_at = now()` + `deleted_by =
# principal.user.id` on the underlying table and, for memory_items /
# granola_note, flips the Qdrant payload `deleted_at_ts` so vector
# search excludes the point (BMO-05). The Qdrant call is fire-and-forget
# — see `_qdrant_mark_deleted_safe`.


@router.delete("/brain/events/{entity_type}/{entity_id}", status_code=204)
async def soft_delete_event(
    entity_type: str,
    entity_id: UUID,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
    team_scope: str = Depends(get_team_scope),
    provider: MemoryProvider = Depends(get_memory_provider),
) -> None:
    _reject_unknown_entity_type(entity_type)

    row = await fetch_event_row(
        session,
        entity_type,
        entity_id,
        team_scope,
    )
    if row is None:
        raise HTTPException(404, "brain event not found in this team")
    if row["deleted_at"] is not None:
        # Already soft-deleted — treat as idempotent success would be
        # nicer for clients, but it would also paper over double-DELETE
        # bugs. Surface as 404 (matches the repo's behaviour on the
        # `deleted_at IS NULL` WHERE filter).
        raise HTTPException(404, "brain event already soft-deleted")

    await assert_can_edit_brain_event(
        principal,
        created_by=row["created_by"],
        team_slug=team_scope,
        session=session,
    )

    actor_id = _user_id_from_principal(principal)
    deleted_at = await soft_delete_entity(session, entity_type, entity_id, actor_id)

    await write_audit(
        session,
        actor_user_id=actor_id,
        team_scope=team_scope,
        action="brain.soft_delete",
        target_id=str(entity_id),
        payload={
            "entity_type": entity_type,
            "deleted_at": deleted_at.isoformat(),
        },
    )
    await session.commit()

    # Qdrant fan-out AFTER PG commit — if Qdrant is the only thing that
    # fails, PG already holds the source of truth and the janitor will
    # reconcile. If we did it before commit, a PG rollback + successful
    # Qdrant write would leave Qdrant ahead of PG (the bad direction).
    if entity_type in _QDRANT_BACKED_ENTITY_TYPES:
        await _qdrant_mark_deleted_safe(provider, entity_id, deleted_at)
    # FastAPI returns 204 on a None return when status_code=204 is set.


# ── POST /v1/brain/events/{entity_type}/{entity_id}/restore ──────────
#
# Clears `deleted_at` + `deleted_by` IFF the soft-delete happened within
# the last 30 days (the retention window before the janitor hard-purges).
# Outside the window the repo raises 410 Gone, propagated verbatim.


@router.post(
    "/brain/events/{entity_type}/{entity_id}/restore",
    response_model=BrainEventOut,
)
async def restore_event(
    entity_type: str,
    entity_id: UUID,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
    team_scope: str = Depends(get_team_scope),
    provider: MemoryProvider = Depends(get_memory_provider),
) -> BrainEventOut:
    _reject_unknown_entity_type(entity_type)

    # The view shows soft-deleted rows — restore needs to see them to do
    # the team-scope + author check before resurrecting the row.
    row = await fetch_event_row(
        session,
        entity_type,
        entity_id,
        team_scope,
    )
    if row is None:
        raise HTTPException(404, "brain event not found in this team")
    if row["deleted_at"] is None:
        # Nothing to restore — restoring an already-live row is a no-op
        # but caller probably has a stale view. 404 keeps the contract
        # symmetric with DELETE's already-deleted case.
        raise HTTPException(404, "brain event is not soft-deleted")

    await assert_can_edit_brain_event(
        principal,
        created_by=row["created_by"],
        team_slug=team_scope,
        session=session,
    )

    # `restore_entity` raises HTTPException(410) when the row is outside
    # the 30-day window. Propagate verbatim — the route layer needs no
    # extra logic.
    await restore_entity(session, entity_type, entity_id)

    await write_audit(
        session,
        actor_user_id=_user_id_from_principal(principal),
        team_scope=team_scope,
        action="brain.restore",
        target_id=str(entity_id),
        payload={
            "entity_type": entity_type,
            "previous_deleted_at": row["deleted_at"].isoformat(),
        },
    )
    await session.commit()

    # Qdrant fan-out AFTER PG commit (same reasoning as soft-delete).
    if entity_type in _QDRANT_BACKED_ENTITY_TYPES:
        await _qdrant_mark_restored_safe(provider, entity_id)

    new_row = await fetch_event_row(
        session,
        entity_type,
        entity_id,
        team_scope,
    )
    if new_row is None:
        raise HTTPException(404, "brain event vanished after restore")
    return BrainEventOut(**dict(new_row))


# ── POST /v1/brain/ingest (Phase 13 plan 13-01) ──────────────────────
#
# Called by librechat-bridge and openwebui-pipeline to push user messages
# into the team brain without a full user identity. Bridge JWT auth is
# accepted via `get_current_principal` (kind=bridge). Returns 202
# immediately; the actual upsert runs as a background.spawn task (fire-and-
# forget). Never blocks the caller's critical path.


@router.post("/brain/ingest", status_code=202)
async def ingest_message(
    body: BrainIngestRequest,
    principal: dict[str, Any] = Depends(get_current_principal),
    team_scope: str = Depends(get_team_scope),
) -> dict[str, str]:
    """Accept a chat message for brain ingest. Returns 202 immediately.

    The upsert into memory_items + Qdrant runs as a background.spawn task —
    the caller never blocks on the classification or storage operation.

    Auth: bridge JWT (kind=bridge) OR user JWT (kind=user).
    team_scope: enforced by X-Team-Scope header.
    author_sub: extracted from principal for user kind; from body.metadata
                or principal.sub for bridge kind.
    """
    if principal["kind"] == "user":
        author_sub = principal["user"].source_user_id
    else:
        # Bridge JWT or user-acting bridge — no user object attached
        author_sub = body.metadata.get("author_sub") or principal.get("sub")

    background.spawn(
        brain_ingest_svc.ingest_external_message(
            team_scope=team_scope,
            content=body.content,
            source=body.source,
            author_sub=author_sub,
            metadata=body.metadata,
            project_scope=body.project_scope,
        ),
        name="brain_ingest.external_message",
    )
    return {"status": "accepted"}
