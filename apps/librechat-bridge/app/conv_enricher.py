"""Inject xbrain CANONICAL facts as a system message at the start of new LibreChat conversations.

Triggered by a `conversations` insert in the Mongo change stream (parallel to messages watcher).
Idempotent: marks `metadata.xbrain_enriched=true` after processing — even when no facts surface,
to avoid re-querying memory-api on every conversation update.

Skipped cases (return False, no message inserted):
- conv_doc has no `conversationId`            → can't address the conv
- already enriched                            → idempotency
- title is empty/missing                      → no useful query for RAG yet (will retry on title set)
- memory-api returns no addendum              → still mark enriched=true (avoid retry storm)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

from app.config import settings
from app.memory_api_client import MemoryApiClient

log = structlog.get_logger()


async def enrich_new_conversation(
    conv_doc: dict[str, Any],
    db,
    mem: MemoryApiClient,
    *,
    sub: str,
    team_scope: str,
) -> bool:
    """Process a Mongo `conversations` insert/update event.

    Returns True if a system message was actually inserted, False otherwise.
    All "skip" paths still update `metadata.xbrain_enriched=true` so we don't
    re-process the conversation on subsequent change-stream events.
    """
    conv_id = conv_doc.get("conversationId")
    if not conv_id:
        return False

    # Idempotency check — never re-enrich a conv we've already touched
    md = conv_doc.get("metadata") or {}
    if md.get("xbrain_enriched"):
        log.debug("conv_already_enriched", conv=conv_id)
        return False

    title = (conv_doc.get("title") or "").strip()
    if not title:
        # Conv was created before user typed anything — wait for title to be set.
        # We do NOT mark enriched=true here so the next event (when title arrives) re-runs.
        log.debug("conv_skipped_empty_title", conv=conv_id)
        return False

    try:
        sys = await mem.get_system_prompt(
            sub=sub,
            team_scope=team_scope,
            query=title,
            top_k=settings.CHAT07_TOP_K,
            min_level=settings.CHAT07_TRUTH_FILTER_MIN_LEVEL,
        )
    except Exception as e:  # noqa: BLE001
        log.warning(
            "conv_enrich_memapi_failed",
            conv=conv_id,
            err=str(e),
            err_type=type(e).__name__,
        )
        # Don't mark enriched — let next event retry
        return False

    addendum = sys.get("system_addendum", "")
    fact_count = sys.get("fact_count", 0)

    if not addendum:
        log.info("no_canonical_facts_for_team", conv=conv_id, team=team_scope)
        await db["conversations"].update_one(
            {"conversationId": conv_id},
            {"$set": {"metadata.xbrain_enriched": True, "metadata.xbrain_facts_injected": 0}},
        )
        return False

    sys_msg = {
        "conversationId": conv_id,
        "messageId": f"xbrain-system-{conv_id}",
        "user": "system",
        "isCreatedByUser": False,
        "text": addendum,
        "model": conv_doc.get("model"),
        "metadata": {
            "xbrain_injected": True,
            "source": "memory-api:rag-canonical",
            "fact_count": fact_count,
        },
        "createdAt": conv_doc.get("createdAt") or datetime.now(timezone.utc),
    }
    await db["messages"].insert_one(sys_msg)
    await db["conversations"].update_one(
        {"conversationId": conv_id},
        {
            "$set": {
                "metadata.xbrain_enriched": True,
                "metadata.xbrain_facts_injected": fact_count,
            }
        },
    )
    log.info("conv_enriched", conv=conv_id, team=team_scope, facts=fact_count)
    return True
