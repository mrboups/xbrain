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
        if not is_brain_relevant(content):
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
