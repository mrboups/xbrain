"""Fan a parsed transcript into the team brain, exactly once.

This module owns the two things the parsers deliberately do not: the dedupe
ledger, and the tagging contract on every turn that reaches ``memory_items``.

Dedupe is two layers, and both are needed:

1. **Conversation level** (``transcript_imports``, UNIQUE on
   ``(team_scope, dedupe_key)``). ``INSERT ... ON CONFLICT DO NOTHING
   RETURNING id`` — "no row came back" is the duplicate verdict, decided by
   Postgres rather than by a read-then-write that two shortcuts fired in the
   same second would both win.
2. **Turn level** (``metadata.idempotency_key``). Every turn gets a
   deterministic key, which ``brain_ingest.ingest_external_message`` turns into
   a uuid5 ``MemoryItem.id``. A forced re-import therefore UPSERTS the same
   rows in place instead of creating second copies — which is what makes the
   ``force`` escape hatch safe to offer at all (an import interrupted halfway
   leaves a ledger row, and without ``force`` the missing half could never be
   completed).

   The key includes ``team_scope``. Without it, importing the same conversation
   into team B would compute the same item ids as team A's copy and CLOBBER
   them — a cross-team write through a supposedly isolated boundary.

Truth level is not set here: ``ingest_external_message`` writes ``WORKING`` for
every external ingest, which is exactly right for an imported transcript. See
``TRUTH_LEVEL_RATIONALE`` below.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session_factory
from app.services import brain_ingest
from app.services.transcript_import import SOURCE_TAGS
from app.services.transcript_import.types import ParsedConversation

log = structlog.get_logger(__name__)

# Why WORKING and not VALIDATED, in one line: the tagging contract reserves
# VALIDATED for content a person has reviewed, and pressing "import" reviews
# nothing — while EPHEMERAL would exclude the transcript from every retrieval
# path (team_context_cache and the agent bundle both filter to WORKING and
# above), making the import a no-op with extra steps.
TRUTH_LEVEL_RATIONALE = (
    "WORKING — durable but unreviewed. Importing is not reviewing, so VALIDATED "
    "would be a lie; EPHEMERAL would hide the transcript from every retrieval "
    "path and make the import pointless."
)

ORIGIN = "transcript-import"


@dataclass
class ConversationOutcome:
    """What happened to one conversation in one import request."""

    dedupe_key: str
    status: str  # "imported" | "duplicate" | "over_limit"
    turns: int
    queued: int = 0
    import_id: str | None = None
    source_conversation_id: str | None = None
    title: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "dedupe_key": self.dedupe_key,
            "status": self.status,
            "turns": self.turns,
            "queued": self.queued,
            "import_id": self.import_id,
            "source_conversation_id": self.source_conversation_id,
            "title": self.title,
        }


@dataclass
class ImportPlan:
    """The synchronous half of an import: what was new, what was already here."""

    source_format: str
    outcomes: list[ConversationOutcome] = field(default_factory=list)
    # (import_id, conversation) pairs whose turns still have to be fanned out.
    pending: list[tuple[UUID, ParsedConversation]] = field(default_factory=list)

    @property
    def totals(self) -> dict[str, int]:
        return {
            "conversations": len(self.outcomes),
            "imported": sum(1 for o in self.outcomes if o.status == "imported"),
            "duplicates": sum(1 for o in self.outcomes if o.status == "duplicate"),
            "over_limit": sum(1 for o in self.outcomes if o.status == "over_limit"),
            "turns": sum(o.turns for o in self.outcomes),
            "queued": sum(o.queued for o in self.outcomes),
        }


def turn_idempotency_key(team_scope: str, dedupe_key: str, index: int) -> str:
    """Deterministic per-turn identity — team-scoped, so two teams never collide."""
    return f"import:{team_scope}:{dedupe_key}#{index}"


async def plan_import(
    session: AsyncSession,
    *,
    team_scope: str,
    source_format: str,
    conversations: list[ParsedConversation],
    user_id: UUID | None,
    max_turns: int,
    force: bool = False,
) -> ImportPlan:
    """Claim each conversation in the ledger; return what is new and what is not.

    Runs synchronously inside the request so the caller can be TOLD what was
    skipped — a re-import that silently returns 202 and does nothing is
    indistinguishable from one that worked.

    ``force=True`` re-runs an already-claimed conversation. Safe because the
    turn-level idempotency keys make the second pass an upsert of the same rows.
    """
    plan = ImportPlan(source_format=source_format)
    budget = max_turns

    for conv in conversations:
        key = conv.dedupe_key(source_format)

        if budget <= 0:
            plan.outcomes.append(
                ConversationOutcome(
                    dedupe_key=key,
                    status="over_limit",
                    turns=conv.turn_count,
                    source_conversation_id=conv.source_id,
                    title=conv.title,
                )
            )
            continue

        row = (await session.execute(sa.text("""
            INSERT INTO transcript_imports
                (team_scope, dedupe_key, source_format, source_conversation_id,
                 title, imported_by, turn_count, queued_count)
            VALUES (:ts, :key, :fmt, :conv_id, :title, :uid, :turns, 0)
            ON CONFLICT (team_scope, dedupe_key) DO NOTHING
            RETURNING id
        """), {
            "ts": team_scope,
            "key": key,
            "fmt": source_format,
            "conv_id": conv.source_id,
            "title": conv.title,
            "uid": str(user_id) if user_id else None,
            "turns": conv.turn_count,
        })).mappings().fetchone()

        if row is None:
            # Already imported into THIS team. The no-op case.
            if not force:
                plan.outcomes.append(
                    ConversationOutcome(
                        dedupe_key=key,
                        status="duplicate",
                        turns=conv.turn_count,
                        source_conversation_id=conv.source_id,
                        title=conv.title,
                    )
                )
                continue
            existing = (await session.execute(sa.text("""
                SELECT id FROM transcript_imports
                WHERE team_scope = :ts AND dedupe_key = :key
            """), {"ts": team_scope, "key": key})).mappings().fetchone()
            if existing is None:  # pragma: no cover — the row was deleted mid-request
                continue
            import_id = existing["id"]
        else:
            import_id = row["id"]

        queued = min(conv.turn_count, budget)
        budget -= queued
        plan.outcomes.append(
            ConversationOutcome(
                dedupe_key=key,
                status="imported",
                turns=conv.turn_count,
                queued=queued,
                import_id=str(import_id),
                source_conversation_id=conv.source_id,
                title=conv.title,
            )
        )
        if queued < conv.turn_count:
            conv = ParsedConversation(
                turns=conv.turns[:queued],
                source_id=conv.source_id,
                title=conv.title,
                started_at=conv.started_at,
            )
        plan.pending.append((import_id, conv))

    return plan


async def fan_out(
    *,
    team_scope: str,
    source_format: str,
    pending: list[tuple[UUID, ParsedConversation]],
    author_sub: str | None,
    project_scope: str | None,
    concurrency: int,
) -> None:
    """Push every claimed turn through the existing brain-ingest path.

    Fire-and-forget from the route's perspective, and never raises: an import
    that fails halfway must not take the API down with it. Bounded concurrency
    because each turn can call the Haiku relevance classifier, and a 500-turn
    export fired all at once would spike both the event loop and that budget.
    """
    semaphore = asyncio.Semaphore(max(1, concurrency))
    source = SOURCE_TAGS.get(source_format, f"import:{source_format}")[:128]

    async def _one(import_id: UUID, conv: ParsedConversation, index: int, turn) -> None:
        async with semaphore:
            await brain_ingest.ingest_external_message(
                team_scope=team_scope,
                content=turn.content,
                source=source,
                author_sub=author_sub,
                project_scope=project_scope,
                metadata={
                    "origin": ORIGIN,
                    "import_id": str(import_id),
                    "source_format": source_format,
                    "source_conversation_id": conv.source_id or "",
                    "conversation_title": conv.title or "",
                    "turn_index": index,
                    "turn_role": turn.role,
                    "turn_timestamp": turn.timestamp.isoformat() if turn.timestamp else "",
                    "idempotency_key": turn_idempotency_key(
                        team_scope, conv.dedupe_key(source_format), index
                    ),
                },
            )

    try:
        for import_id, conv in pending:
            await asyncio.gather(
                *(
                    _one(import_id, conv, index, turn)
                    for index, turn in enumerate(conv.turns)
                )
            )
            await _record_queued(import_id, conv.turn_count)
        log.info(
            "transcript_import.fan_out.done",
            team_scope=team_scope,
            source_format=source_format,
            conversations=len(pending),
        )
    except Exception as exc:  # noqa: BLE001 — background task, never propagate
        log.warning(
            "transcript_import.fan_out.failed",
            error=str(exc),
            team_scope=team_scope,
            source_format=source_format,
        )


async def _record_queued(import_id: UUID, queued: int) -> None:
    """Best-effort progress marker on the ledger row. Never raises."""
    try:
        async with async_session_factory() as s:
            await s.execute(
                sa.text(
                    "UPDATE transcript_imports SET queued_count = :n WHERE id = :id"
                ),
                {"n": queued, "id": str(import_id)},
            )
            await s.commit()
    except Exception:  # noqa: BLE001
        pass
