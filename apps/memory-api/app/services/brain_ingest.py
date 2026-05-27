"""Auto-ingest relevant team-chat messages into the searchable brain.

Closes the MEM-04 / CHAT-03 gap (the xbrain core differentiator): team-chat
messages were stored in ``team_messages`` but never indexed into
``memory_items`` + Qdrant, so the brain stayed empty and no frontend could
retrieve chat knowledge.

This hook upserts each substantive human message as a ``WORKING`` memory item.
Both retrieval paths already read from there with zero extra wiring:
  - ``team_context_cache.get_team_memory_bundle`` (the @claude system block)
    selects ``memory_items WHERE truth_level IN (WORKING, VALIDATED, CANONICAL)``
  - the MCP ``memory_search`` tool queries the Qdrant vectors that
    ``provider.upsert`` writes.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog

from app.deps import get_memory_provider
from app.services import team_context_cache
from xbrain_memory.types import (
    MemoryItem,
    TruthLevel,
    ValidationStatus,
    Visibility,
)

# Fixed Phase 13 UUID5 namespace — never change (used for deterministic item IDs).
# Changing this would invalidate all idempotency_key-derived IDs in production.
BRAIN_INGEST_NS = uuid.UUID("8e7c2b00-1aae-5a40-9c4f-13b7c0d72f10")

log = structlog.get_logger(__name__)

_MIN_CHARS = 15


def is_brain_relevant(content: str) -> bool:
    """v1 relevance heuristic: ingest substantive human messages, skip trivia
    and ``@claude`` commands (those are queries, not facts).

    "En fonction de ce qui peut être pertinent" — a Haiku classifier can
    replace this later for semantic relevance scoring; the heuristic keeps the
    hot path zero-latency and zero-cost for v1.
    """
    c = (content or "").strip()
    if len(c) < _MIN_CHARS:
        return False
    low = c.lower()
    if low.startswith(("@claude", "@c ", "@c\n", "@cl ", "@cl\n")):
        return False
    return True


async def ingest_team_message(
    *,
    team_scope: str,
    team_id: Any,
    content: str,
    author_sub: str | None,
) -> None:
    """Fire-and-forget: upsert a relevant team-chat message into the brain.

    Never raises — ingestion must never break the chat-send response path.
    """
    try:
        # Phase 13 D1/D4 — Haiku 4.5 classifier replaces standalone heuristic.
        # classify() runs is_brain_relevant() first as a fast-path filter,
        # then Haiku for semantic relevance, with heuristic fallback on error.
        from app.services.relevance_filter import classify  # local import — avoids cycle at module load
        if not await classify(content, team_scope=team_scope):
            log.info("brain_ingest.team_message.skipped_by_filter", team_scope=team_scope)
            return
        provider = get_memory_provider()
        now = datetime.now(timezone.utc)
        item = MemoryItem(
            id=str(uuid.uuid4()),
            team_scope=team_scope,
            content=content.strip(),
            metadata={"origin": "team-chat", "author_sub": author_sub or ""},
            visibility=Visibility.TEAM,
            truth_level=TruthLevel.WORKING,
            confidence=0.7,
            source=(f"team-chat:{author_sub or 'unknown'}")[:128],
            validation_status=ValidationStatus.PENDING,
            created_at=now,
            updated_at=now,
        )
        await provider.upsert(item)
        # Bundle has a 5-min TTL; drop it so @claude sees the new fact now.
        try:
            team_context_cache.invalidate(team_id)
        except Exception:  # noqa: BLE001
            pass
        log.info(
            "brain_ingest.team_message.ok",
            team_scope=team_scope,
            chars=len(content.strip()),
        )
    except Exception as exc:  # noqa: BLE001 — never break chat send
        log.warning("brain_ingest.team_message.failed", error=str(exc))


async def ingest_external_message(
    *,
    team_scope: str,
    content: str,
    source: str,
    author_sub: str | None,
    metadata: dict | None = None,
    project_scope: str | None = None,
) -> None:
    """Fire-and-forget brain ingest from LibreChat, Open WebUI, or future frontends.

    Gates on the Haiku relevance classifier (relevance_filter.classify) — falls back
    to the heuristic on any classifier error per Phase 13 D1.

    Idempotency: when metadata['idempotency_key'] is present, the MemoryItem.id is
    derived deterministically via uuid5(BRAIN_INGEST_NS, idempotency_key). This makes
    the upsert safe under MongoDB change-stream resume-token re-delivery.

    Never raises — failure must not propagate to the caller (chat-send path).
    """
    try:
        # Local import to avoid circular dependency:
        # relevance_filter imports is_brain_relevant from brain_ingest;
        # brain_ingest imports classify from relevance_filter lazily here.
        from app.services.relevance_filter import classify  # noqa: PLC0415

        if not await classify(content, team_scope=team_scope):
            log.info(
                "brain_ingest.external.skipped_by_filter",
                team_scope=team_scope,
                source=source,
            )
            return

        provider = get_memory_provider()
        now = datetime.now(timezone.utc)
        md = dict(metadata or {})
        md["author_sub"] = author_sub or ""

        idem_key = md.get("idempotency_key")
        if idem_key:
            item_id = str(uuid.uuid5(BRAIN_INGEST_NS, idem_key))
        else:
            item_id = str(uuid.uuid4())

        item = MemoryItem(
            id=item_id,
            team_scope=team_scope,
            project_scope=project_scope,
            content=content.strip(),
            metadata=md,
            visibility=Visibility.TEAM,
            truth_level=TruthLevel.WORKING,
            confidence=0.7,
            source=source[:128],
            validation_status=ValidationStatus.PENDING,
            created_at=now,
            updated_at=now,
        )
        await provider.upsert(item)
        log.info(
            "brain_ingest.external.ok",
            team_scope=team_scope,
            source=source,
            chars=len(content.strip()),
            idem_key=idem_key,
        )
    except Exception as exc:  # noqa: BLE001 — fail-soft, never break caller
        log.warning(
            "brain_ingest.external.failed",
            error=str(exc),
            source=source,
            team_scope=team_scope,
        )
