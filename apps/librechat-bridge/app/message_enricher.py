"""Per-turn RAG enrichment for LibreChat (Phase 13 CHAT-07 / D5).

Fired by mongo_watcher.messages_watch_loop on every user-message INSERT.
Replaces the conv-boot-only enrichment with per-turn fact injection.

Idempotency:
  - Distinct messageId from conv-boot (xbrain-turn-{conv_id}-{msg_id} vs xbrain-system-{conv_id})
  - Per-turn pre-insert find_one guard against Mongo change-stream resume-token re-delivery

Fail-soft: every error path returns False and is logged; the change-stream loop is never broken.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

from app.config import settings
from app.memory_api_client import MemoryApiClient

log = structlog.get_logger(__name__)


async def enrich_turn(
    msg_doc: dict[str, Any],
    db,
    mem: MemoryApiClient,
    *,
    sub: str,
    team_scope: str,
) -> bool:
    """Inject VALIDATED+CANONICAL facts as a system message before this user turn.

    Returns True iff a system message was inserted; False on skip / error.
    Never raises.

    Args:
        msg_doc: Raw Mongo fullDocument from the messages change stream.
        db: Motor AsyncIOMotorDatabase instance.
        mem: MemoryApiClient instance.
        sub: User subject identifier (for bridge JWT).
        team_scope: Team scope for memory-api retrieval scoping.
    """
    conv_id = msg_doc.get("conversationId") or msg_doc.get("parentMessageId")
    msg_id = msg_doc.get("_id")
    if not conv_id or msg_id is None:
        return False

    target_id = f"xbrain-turn-{conv_id}-{msg_id}"

    # Idempotency guard — cheap find_one BEFORE any memory-api call.
    # Prevents double injection under Mongo change-stream resume-token re-delivery.
    try:
        existing = await db["messages"].find_one({"messageId": target_id})
    except Exception as exc:  # noqa: BLE001
        log.warning("message_enricher.find_one_failed", err=str(exc), conv=conv_id)
        return False
    if existing:
        log.debug("message_enricher.already_injected", conv=conv_id, msg=str(msg_id))
        return False

    query_text = (msg_doc.get("text") or "")[:500]
    if not query_text.strip():
        return False

    try:
        sys = await mem.get_system_prompt(
            sub=sub,
            team_scope=team_scope,
            query=query_text,
            top_k=settings.CHAT07_TOP_K,
            min_level=settings.CHAT07_TRUTH_FILTER_MIN_LEVEL,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "message_enricher.memapi_failed",
            err=str(exc),
            err_type=type(exc).__name__,
            conv=conv_id,
            msg=str(msg_id),
        )
        return False

    addendum = sys.get("system_addendum", "")
    fact_count = sys.get("fact_count", 0)

    if not addendum:
        log.info("message_enricher.no_facts", conv=conv_id, team=team_scope)
        return False

    sys_msg = {
        "conversationId": conv_id,
        "messageId": target_id,
        "user": "system",
        "isCreatedByUser": False,
        "text": addendum,
        "model": msg_doc.get("model"),
        "metadata": {
            "xbrain_injected": True,
            "xbrain_turn_enrichment": True,
            "source": "memory-api:rag-validated",
            "fact_count": fact_count,
            "trigger_msg_id": str(msg_id),
        },
        "createdAt": datetime.now(timezone.utc),
    }

    try:
        await db["messages"].insert_one(sys_msg)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "message_enricher.insert_failed",
            err=str(exc),
            conv=conv_id,
            msg=str(msg_id),
        )
        return False

    log.info(
        "message_enricher.turn_enriched",
        conv=conv_id,
        msg=str(msg_id),
        team=team_scope,
        facts=fact_count,
    )
    return True
