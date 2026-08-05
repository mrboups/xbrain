"""Which memory items belong to one team-chat message — and how to remove them.

The chat's "remove this message from the brain too" option is only honest if it
removes EVERYTHING the message put in the brain. A message can seed more than one
`memory_items` row:

  1. **The text itself.** `brain_ingest.ingest_team_message` upserts a WORKING
     item whose metadata carries `origin='team-chat'` and, since this module
     shipped, `message_id` — the precise back-link.
  2. **An attachment's card item.** `POST /v1/media/upload` mints one item per
     upload and the chat message references it as `metadata.media.item_id`.
  3. **That attachment's CHILDREN.** A document's body is chunked into one item
     per chunk (`doc_body_ingest`) and an image gets a vision description item
     (`image_describe`). Both carry `metadata.parent_item_id` pointing at (2).

Children are the whole reason this module exists. Deleting only the parent leaves
a PDF's body chunks and an image's description sitting in the vector store, fully
recallable — so the agent can still quote a document that the person watched
disappear from the chat. "It is gone" would be false, and the person would have no
way to find out. So the parent is expanded through `parent_item_id` before
anything is written.

WHY RAW SQL AND NOT THE PROVIDER. `MemoryProvider` has no "find the children of"
query and no bulk soft delete; it addresses items one id at a time through its own
asyncpg pool. Going through the request's `AsyncSession` instead means the message
row and every memory item it owns are flipped in ONE transaction — the audit row
included. Qdrant is reconciled after the commit, best-effort, exactly as
`routes/brain.py` does it: Postgres is the source of truth and the janitor
re-syncs the vector payloads nightly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)

# What `brain_ingest.ingest_team_message` stamps on a chat-derived memory item.
ORIGIN_TEAM_CHAT = "team-chat"

# How deep the parent -> child expansion is allowed to walk. Today the tree is
# exactly two levels (upload -> chunks/description) and a third pass returns
# nothing. The cap is here so that a future grandchild is covered without this
# becoming a query that can run forever on a cycle in the metadata.
MAX_CHILD_DEPTH = 3

# Hard ceiling on how many items one message may take with it. A 500-page PDF
# chunks into hundreds of rows, which is legitimate; an unbounded `IN (...)` built
# from user-influenced metadata is not. Hitting the cap is reported, never silent.
MAX_LINKED_ITEMS = 2000


@dataclass
class LinkedItems:
    """The memory items one message owns, split by how they were found."""

    #: Items seeded DIRECTLY by the message — its indexed text and its upload.
    roots: list[str] = field(default_factory=list)
    #: Items reachable from a root through `metadata.parent_item_id`.
    children: list[str] = field(default_factory=list)
    #: True when the expansion stopped at MAX_LINKED_ITEMS instead of finishing.
    truncated: bool = False
    #: True when the text item was matched on content rather than on `message_id`
    #: (a message ingested before the back-link existed).
    matched_legacy_text: bool = False

    @property
    def all_ids(self) -> list[str]:
        """Every id, roots first, de-duplicated, in a stable order."""
        seen: set[str] = set()
        out: list[str] = []
        for item_id in [*self.roots, *self.children]:
            if item_id in seen:
                continue
            seen.add(item_id)
            out.append(item_id)
        return out


def media_item_id(message_metadata: dict[str, Any] | None) -> str | None:
    """The uploaded item a chat message points at, or None.

    Defensive about the shape because it comes from a JSONB column that older
    rows wrote before the media block existed.
    """
    if not isinstance(message_metadata, dict):
        return None
    media = message_metadata.get("media")
    if not isinstance(media, dict):
        return None
    item_id = media.get("item_id")
    if not item_id:
        return None
    return str(item_id)


async def _live_ids(
    session: AsyncSession, *, sql: str, params: dict[str, Any]
) -> list[str]:
    rows = (await session.execute(sa.text(sql), params)).fetchall()
    return [str(r[0]) for r in rows]


async def collect_linked_items(
    session: AsyncSession,
    *,
    team_scope: str,
    message_id: UUID,
    content: str,
    author_sub: str | None,
    message_metadata: dict[str, Any] | None,
) -> LinkedItems:
    """Every live memory item that this message put in the brain.

    Every query is filtered on `team_scope` AND `deleted_at IS NULL`. The scope
    predicate is the cross-team boundary — the ids and the content come from a
    row the caller was already authorised for, but an item is only ever touched
    inside the team that owns it. The deleted filter keeps an already-removed item
    from being counted twice in the audit payload.
    """
    linked = LinkedItems()

    # ── 1. The text item, by its explicit back-link ──────────────────────────
    text_ids = await _live_ids(
        session,
        sql="""
            SELECT id FROM memory_items
            WHERE team_scope = :ts
              AND deleted_at IS NULL
              AND metadata->>'message_id' = :mid
        """,
        params={"ts": team_scope, "mid": str(message_id)},
    )

    # ── 2. Same thing for a message ingested BEFORE the back-link existed ────
    #
    # Without this, "remove it from the brain" would silently do nothing for
    # every message already in the deployment — the worst possible outcome, since
    # the person is told it worked. The fallback is deliberately narrow: same
    # team, chat origin, same author, and the exact stored text (brain_ingest
    # strips before it writes, so the comparison strips too). A person who said
    # the identical sentence twice loses both copies, which is the intent — the
    # content is what they asked to have removed.
    if not text_ids and content.strip():
        text_ids = await _live_ids(
            session,
            sql="""
                SELECT id FROM memory_items
                WHERE team_scope = :ts
                  AND deleted_at IS NULL
                  AND metadata->>'origin' = :origin
                  AND metadata->>'author_sub' = :sub
                  AND content = :content
            """,
            params={
                "ts": team_scope,
                "origin": ORIGIN_TEAM_CHAT,
                "sub": author_sub or "",
                "content": content.strip(),
            },
        )
        if text_ids:
            linked.matched_legacy_text = True

    linked.roots.extend(text_ids)

    # ── 3. The attachment's card item ────────────────────────────────────────
    upload_id = media_item_id(message_metadata)
    if upload_id:
        # Re-read it through the same scope filter rather than trusting the id in
        # the message metadata: that value is JSONB and a row rewritten by hand
        # must not be able to name an item in another team.
        found = await _live_ids(
            session,
            sql="""
                SELECT id FROM memory_items
                WHERE team_scope = :ts AND deleted_at IS NULL AND id = :id
            """,
            params={"ts": team_scope, "id": upload_id},
        )
        linked.roots.extend(found)

    # ── 4. Expand children — the half that makes the promise true ────────────
    frontier = list(linked.roots)
    known = set(linked.roots)
    for _ in range(MAX_CHILD_DEPTH):
        if not frontier:
            break
        if len(known) >= MAX_LINKED_ITEMS:
            linked.truncated = True
            break
        kids = await _live_ids(
            session,
            sql="""
                SELECT id FROM memory_items
                WHERE team_scope = :ts
                  AND deleted_at IS NULL
                  AND metadata->>'parent_item_id' = ANY(:parents)
                LIMIT :lim
            """,
            params={
                "ts": team_scope,
                "parents": list(frontier),
                "lim": MAX_LINKED_ITEMS,
            },
        )
        frontier = [k for k in kids if k not in known]
        for k in frontier:
            known.add(k)
            linked.children.append(k)

    if len(known) >= MAX_LINKED_ITEMS:
        linked.truncated = True

    return linked


async def soft_delete_items(
    session: AsyncSession,
    *,
    team_scope: str,
    item_ids: list[str],
    deleted_by: UUID | None,
    deleted_at: datetime,
) -> list[str]:
    """Flip `deleted_at` + `deleted_by` on every named item. Returns what changed.

    Scoped to `team_scope` a second time (the collector already filtered on it)
    because this is the statement that writes: a defence that only exists in the
    read half is one refactor away from not existing at all.

    `deleted_at IS NULL` in the WHERE clause makes a repeat call a no-op and keeps
    the returned list honest — it names the rows THIS call removed, which is what
    the audit payload reports.

    The timestamp is passed in, not taken here, so the message and its memory
    items carry the same soft-delete moment and the Qdrant payloads written after
    the commit cannot drift from Postgres.

    Caller commits.
    """
    if not item_ids:
        return []
    rows = (
        await session.execute(
            sa.text(
                """
                UPDATE memory_items
                SET deleted_at = :now, deleted_by = :uid
                WHERE team_scope = :ts
                  AND deleted_at IS NULL
                  AND id = ANY(:ids)
                RETURNING id
                """
            ),
            {
                "now": deleted_at,
                "uid": str(deleted_by) if deleted_by else None,
                "ts": team_scope,
                "ids": list(item_ids),
            },
        )
    ).fetchall()
    return [str(r[0]) for r in rows]


async def mark_deleted_in_qdrant(provider, item_ids: list[str], deleted_at: datetime) -> int:
    """Best-effort: hide every deleted item from vector search. Returns the count.

    Called AFTER the Postgres commit and never allowed to raise. Postgres is the
    source of truth and the nightly janitor re-flips any point this missed; a
    Qdrant outage must not turn a completed deletion into a 500 for the person who
    asked for it — they would press it again and see the same error over a message
    that is already gone.
    """
    done = 0
    for item_id in item_ids:
        try:
            await provider.mark_deleted(item_id, deleted_at)
            done += 1
        except Exception as exc:  # noqa: BLE001 — best-effort, janitor reconciles
            log.warning(
                "message_brain_links.qdrant_mark_deleted_failed",
                item_id=item_id,
                error=str(exc),
            )
    return done
