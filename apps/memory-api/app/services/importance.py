"""The two upper truth levels, and who is allowed to set each — 2026-08-05.

Four levels the team can explain, of which this module writes two:

    not interesting     nothing stored          the relevance classifier
    normal working      WORKING                 the ingest default
    important / final   VALIDATED               THE AI, and it is REVERSIBLE
    starred             CANONICAL               A PERSON, and only a person

WHY THE ONE-WAY RULE NOW HAS AN EXCEPTION, AND EXACTLY ONE.

"Promotion is one-way and requires a human" existed so that a high level meant
something. That rule still holds — for **starred**. It does NOT hold for
important/final, and the difference is the whole point: *important* is a MODEL
OPINION, so the model that formed it must be able to withdraw it. A flag the AI
can raise but never lower is a ratchet, and a ratchet on an opinion ends with
every item flagged and the flag meaning nothing.

*Starred* is the level a person sets, one-way in the sense that no automation
can reach it — the AI cannot star and cannot un-star. A person may un-star their
own judgement, because that is still a person judging. Nothing else may.

HOW THAT IS ENFORCED, RATHER THAN DOCUMENTED.

Both statements below name the level they are allowed to move FROM, in the
WHERE clause:

    flag   ... WHERE truth_level = 'WORKING'    -> VALIDATED
    unflag ... WHERE truth_level = 'VALIDATED'  -> WORKING

So an item a person has starred (CANONICAL) matches neither. The AI cannot
promote into it and cannot demote out of it, and that is a property of the SQL
rather than of a caller remembering to check. EPHEMERAL and PUBLIC are equally
out of reach here: PUBLIC is reserved for the sharing flow (see BACKLOG.md,
"Sharing beyond the team"), and EPHEMERAL no longer has a producer.

SUPERSESSION IS NOT IMPLEMENTED, DELIBERATELY.

The owner's design says an item is de-flagged "when another takes over". Knowing
WHAT supersedes what is a graph question — recency is not enough, and Neo4j /
Graphiti are already deployed for exactly this kind of question. That is its own
feature. What ships here is the reversible flag and the path that clears it;
`set_ai_importance(..., important=False)` is what a supersession trigger will
call once one exists. Nothing calls it automatically today.

WHY POSTGRES ALONE IS ENOUGH.

`NativeProvider.search` filters Qdrant on team_scope and the soft-delete stamp
only, then hydrates every hit from `memory_items` and applies `truth_level_min`
to the HYDRATED row. So a level written here is authoritative for retrieval the
moment it commits; the vector payload's copy is not read for this decision.
"""
from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import write_audit

log = structlog.get_logger(__name__)

#: The AI's own two actions. Named constants because the action string is what
#: gets grepped six months later; the payload is for detail.
AUDIT_ACTION_FLAG = "memory_item.flag_important"
AUDIT_ACTION_UNFLAG = "memory_item.unflag_important"

#: What goes in `payload.actor_kind` when no human is behind the change.
#:
#: `audit_log.actor_user_id` is nullable, so an AI-set level writes NULL there —
#: and NULL is ambiguous between "the agent did it", "a system job did it" and
#: "nobody recorded who did it". The row must SAY which, or the trail answers
#: "who made this important?" with a shrug. Rows written by a person carry
#: `actor_kind: "user"` (see routes/team_chat.py) for the same reason.
ACTOR_KIND_AI = "ai"

#: Which piece of automation. There is one today; there will be more.
ACTOR_AI_RELEVANCE = "relevance_filter"

#: Item metadata keys. `ai_important` is what makes the flag legible on the item
#: itself rather than only in the audit trail — and it is what tells an un-star
#: whether the AI had judged the item final BEFORE a person starred it, so
#: removing a star restores that judgement instead of silently discarding it.
META_AI_IMPORTANT = "ai_important"
META_AI_MODEL = "ai_important_model"
META_AI_SCORE = "ai_important_score"

_LEVEL_WORKING = "WORKING"
_LEVEL_VALIDATED = "VALIDATED"


async def set_ai_importance(
    session: AsyncSession,
    *,
    team_scope: str,
    item_ids: list[str],
    important: bool,
    model: str | None = None,
    score: float | None = None,
    message_id: Any = None,
    source: str | None = None,
) -> list[str]:
    """Raise or clear the AI's important/final flag. Returns what actually changed.

    Moves WORKING -> VALIDATED (`important=True`) or VALIDATED -> WORKING
    (`important=False`), never anything else. An item at CANONICAL — starred by a
    person — matches neither WHERE clause and is returned as unchanged, so this
    function is incapable of overruling a human judgement in either direction.

    One audit row per item that genuinely moved. An item already at the target
    level changes nothing and writes nothing: a trail that records no-ops as
    events is a trail nobody can read.

    Caller commits — so the level change and its audit row land in ONE
    transaction. A promotion that succeeded while its audit row was lost is
    precisely the case the trail exists for.
    """
    if not item_ids:
        return []

    if important:
        from_level, to_level = _LEVEL_WORKING, _LEVEL_VALIDATED
        validation_status = "validated"
        action = AUDIT_ACTION_FLAG
    else:
        from_level, to_level = _LEVEL_VALIDATED, _LEVEL_WORKING
        validation_status = "pending"
        action = AUDIT_ACTION_UNFLAG

    meta_patch: dict[str, Any] = {META_AI_IMPORTANT: important}
    if important:
        meta_patch[META_AI_MODEL] = model
        meta_patch[META_AI_SCORE] = score

    rows = (
        await session.execute(
            sa.text(
                """
                UPDATE memory_items
                SET truth_level = :to_level,
                    validation_status = :validation_status,
                    metadata = COALESCE(metadata, '{}'::jsonb) || CAST(:patch AS jsonb),
                    updated_at = NOW()
                WHERE team_scope = :ts
                  AND deleted_at IS NULL
                  AND truth_level = :from_level
                  AND id = ANY(CAST(:ids AS uuid[]))
                RETURNING id
                """
            ),
            {
                "to_level": to_level,
                "from_level": from_level,
                "validation_status": validation_status,
                "patch": json.dumps(meta_patch),
                "ts": team_scope,
                "ids": list(item_ids),
            },
        )
    ).fetchall()
    changed = [str(r[0]) for r in rows]

    for item_id in changed:
        await write_audit(
            session,
            # NULL by design — no person did this. The payload names the actor
            # so the NULL is never read as "unknown".
            actor_user_id=None,
            team_scope=team_scope,
            action=action,
            target_id=item_id,
            payload={
                "actor_kind": ACTOR_KIND_AI,
                "actor": ACTOR_AI_RELEVANCE,
                "model": model,
                "score": score,
                "from": from_level,
                "to": to_level,
                "reversible": True,
                # The join back to the chat trail (`team_message.*` actions) when
                # the item came from a message. Absent for LibreChat / Open WebUI
                # ingest, which never had one.
                "message_id": str(message_id) if message_id is not None else None,
                "source": source,
            },
        )

    return changed


async def flag_ingested_item(
    *,
    team_scope: str,
    item_id: str,
    model: str | None,
    score: float | None,
    message_id: Any = None,
    source: str | None = None,
) -> bool:
    """Flag ONE freshly-ingested item, on this coroutine's own session.

    The ingest paths run detached from any request (`asyncio.create_task`), so
    there is no request session to borrow — this opens one, commits the level
    change together with its audit row, and closes it.

    Never raises. A failure here leaves the item exactly where the upsert put it,
    at WORKING, which is the same outcome as the classifier declining to flag it:
    knowledge kept, judgement absent. Losing the flag is a small loss; failing
    the ingest that carries the knowledge is not.
    """
    from app.db.session import async_session_factory  # noqa: PLC0415 — import cycle

    try:
        async with async_session_factory() as session:
            changed = await set_ai_importance(
                session,
                team_scope=team_scope,
                item_ids=[item_id],
                important=True,
                model=model,
                score=score,
                message_id=message_id,
                source=source,
            )
            await session.commit()
        log.info(
            "importance.flagged",
            team_scope=team_scope,
            item_id=item_id,
            changed=bool(changed),
        )
        return bool(changed)
    except Exception as exc:  # noqa: BLE001 — the item stays at WORKING
        log.warning(
            "importance.flag_failed",
            team_scope=team_scope,
            item_id=item_id,
            error=str(exc),
        )
        return False


async def item_ai_important(session: AsyncSession, item_id: str) -> bool:
    """Did the AI judge this item final? Read from the item, not the audit trail.

    Used when a star is removed: the item goes back to what the AI thought of it,
    not blindly to WORKING. Reading the flag off `metadata` rather than replaying
    `audit_log` keeps the answer O(1) and keeps the audit table what it is — a
    record of what happened, not the state of the world.
    """
    row = (
        await session.execute(
            sa.text(
                "SELECT metadata->>:key FROM memory_items WHERE id = CAST(:id AS uuid)"
            ),
            {"key": META_AI_IMPORTANT, "id": str(item_id)},
        )
    ).fetchone()
    return bool(row and row[0] == "true")


__all__ = [
    "ACTOR_AI_RELEVANCE",
    "ACTOR_KIND_AI",
    "AUDIT_ACTION_FLAG",
    "AUDIT_ACTION_UNFLAG",
    "META_AI_IMPORTANT",
    "META_AI_MODEL",
    "META_AI_SCORE",
    "flag_ingested_item",
    "item_ai_important",
    "set_ai_importance",
]
