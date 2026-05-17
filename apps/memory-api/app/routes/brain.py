"""`/v1/brain/*` — universal brain monitor (Phase 11).

This module ships the read endpoint `GET /v1/brain/events` (BMO-02 +
BMO-03). It reads from the `v_brain_events` SQL view introduced in
migration 0018 — a UNION ALL over the 7 entity types tracked by xbrain
(memory_item, granola_note, conversation, message, team_message, task,
contact).

Mutation endpoints (PATCH truth_level / DELETE soft / restore — BMO-04,
BMO-05, BMO-06) ship in plan 11-05 and live in this same file so a
single router declaration is registered in `main.py`.

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
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_session, get_team_scope
from app.schemas.brain import BrainEventListOut, BrainEventOut

router = APIRouter()


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
    # Build the SQL incrementally so each filter is opt-in. The text query
    # uses named bind params throughout — no string interpolation of user
    # input — which keeps the ILIKE pattern safe from injection (the
    # `q_pattern` value is fully parameterised; only the surrounding `%`
    # wildcards are concatenated in Python).
    sql_parts: list[str] = ["SELECT * FROM v_brain_events WHERE team_scope = :ts"]
    params: dict[str, Any] = {"ts": team_scope}

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

    return BrainEventListOut(
        items=[BrainEventOut(**dict(r)) for r in page],
        next_cursor=next_cursor,
    )
