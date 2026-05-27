"""Watch LibreChat MongoDB change streams and forward messages to memory-api.

Two parallel watchers:
  - messages watcher: forwards each new chat message to memory-api as an EPHEMERAL fact
  - conversations watcher: on new conv, calls memory-api /v1/system-prompt and injects
    the team's CANONICAL facts as a system message (RAG enrichment, idempotent)
"""

import asyncio
import time

import structlog
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from app.config import settings
from app.conv_enricher import enrich_new_conversation
from app.memory_api_client import MemoryApiClient
from app.state_store import load_resume_token, save_resume_token
from app.task_intent_detector import detect_task_intent
from app.contact_extractor import extract_contacts_from_message

log = structlog.get_logger()


async def resolve_team_scope(client: MemoryApiClient, user_sub: str) -> str:
    """Phase 1 simplification: every user maps to BRIDGE_DEFAULT_TEAM_SCOPE.

    Phase 2 will resolve via memory-api /v1/me with a cache.
    """
    return settings.BRIDGE_DEFAULT_TEAM_SCOPE


async def map_message(mongo_doc: dict, mongo_db) -> dict | None:
    """Translate a LibreChat `messages` Mongo doc → payload for memory-api.

    Returns None when we can't extract enough info (skip silently).
    """
    conversation_id_lc = mongo_doc.get("conversationId") or mongo_doc.get("parentMessageId")
    if not conversation_id_lc:
        log.info("skip_message_no_convid", _id=str(mongo_doc.get("_id")))
        return None

    convs = mongo_db["conversations"]
    conv = await convs.find_one({"conversationId": conversation_id_lc})

    user_id = mongo_doc.get("user") or (conv.get("user") if conv else None)
    if not user_id:
        log.info("skip_message_no_user", _id=str(mongo_doc.get("_id")))
        return None

    users = mongo_db["users"]
    user = None
    try:
        user = await users.find_one({"_id": ObjectId(user_id)}) if isinstance(user_id, str) else None
    except Exception:
        user = await users.find_one({"_id": user_id})
    sub = (user or {}).get("googleId") or (user or {}).get("email") or str(user_id)

    model = mongo_doc.get("model") or (conv.get("model") if conv else "unknown")
    source = f"librechat:{model}"
    role = "user" if mongo_doc.get("isCreatedByUser") else "assistant"
    content = mongo_doc.get("text") or ""

    return {
        "sub": sub,
        "conversation_id": conversation_id_lc,
        "role": role,
        "content": content,
        "source": source,
        "metadata": {"librechat_id": str(mongo_doc.get("_id"))},
    }


async def _maybe_ingest_to_brain(
    payload: dict,
    mem: MemoryApiClient,
    team_scope: str,
) -> None:
    """Fire-and-forget: upsert a LibreChat user message into memory_items + Qdrant.

    User messages only (assistant messages are LLM output, not new team facts).
    Gated by BRAIN_INGEST_ENABLED (default True). Idempotency: deterministic
    idempotency_key=f"librechat:{librechat_id}" -- safe under Mongo change-stream
    resume-token re-delivery (Phase 13 13-RESEARCH Pitfall 1 + Plan 13-03 race fix).

    Never raises -- failure is logged and swallowed; the change-stream loop continues.
    """
    if not settings.BRAIN_INGEST_ENABLED:
        return
    if payload.get("role") != "user":
        return
    content = payload.get("content") or ""
    if not content.strip():
        return
    librechat_id = (payload.get("metadata") or {}).get("librechat_id", "")
    if not librechat_id:
        return
    try:
        await mem.brain_ingest(
            sub=payload["sub"],
            team_scope=team_scope,
            content=content,
            source=payload.get("source") or "librechat:unknown",
            metadata={
                "origin": "librechat",
                "librechat_id": librechat_id,
                "idempotency_key": f"librechat:{librechat_id}",
            },
        )
    except Exception as exc:  # noqa: BLE001 -- fire-and-forget must not propagate
        log.warning(
            "librechat_brain_ingest.failed",
            err=str(exc),
            sub=payload.get("sub"),
            team_scope=team_scope,
            librechat_id=librechat_id,
        )


async def _resolve_sub_for_conv(conv_doc: dict, mongo_db) -> str | None:
    """Map a conversations doc to a sub the bridge can sign as. Returns None if unresolved."""
    user_id = conv_doc.get("user")
    if not user_id:
        return None
    try:
        user = (
            await mongo_db["users"].find_one({"_id": ObjectId(user_id)})
            if isinstance(user_id, str)
            else await mongo_db["users"].find_one({"_id": user_id})
        )
    except Exception:
        user = None
    return (user or {}).get("googleId") or (user or {}).get("email") or str(user_id)


async def messages_watch_loop(db, mem: MemoryApiClient) -> None:
    """Forward every new `messages` insert to memory-api."""
    resume_token = load_resume_token()
    pipeline = [{"$match": {"operationType": "insert", "ns.coll": "messages"}}]
    log.info("starting_messages_change_stream", resume=bool(resume_token))

    async with db.watch(pipeline=pipeline, resume_after=resume_token) as stream:
        async for change in stream:
            try:
                doc = change["fullDocument"]
                payload = await map_message(doc, db)
                if payload:
                    # Phase 7 plan 07-09 — Task intent detection (D5 trigger 3)
                    # Only consider user-authored messages (assistant outputs go through
                    # agent path / 07-06 trigger 2). Fail-soft: None return = no-op.
                    if payload.get("role") == "user" and payload.get("content"):
                        intent = await detect_task_intent(payload["content"])
                        if intent and intent.get("has_task"):
                            # Flag the memory_item so memory-api's
                            # _maybe_create_task_from_action (07-06) creates the task.
                            # We don't call /v1/tasks directly — bridge JWT is rejected
                            # there (07-03 requires user identity, not bridge principal).
                            payload.setdefault("metadata", {})
                            payload["metadata"]["contains_action"] = True
                            if intent.get("title"):
                                payload["metadata"]["task_intent_title"] = intent["title"]
                            if intent.get("assignee_email"):
                                payload["metadata"]["task_intent_assignee_email"] = intent[
                                    "assignee_email"
                                ]
                            log.info(
                                "task_intent.detected",
                                sub=payload.get("sub"),
                                conv=payload.get("conversation_id"),
                                title=intent.get("title"),
                            )
                    team_scope = await resolve_team_scope(mem, payload["sub"])
                    await mem.post_message(team_scope=team_scope, **payload)
                    log.info(
                        "forwarded_message",
                        lc_id=payload["metadata"]["librechat_id"],
                        team=team_scope,
                        source=payload["source"],
                    )
                    # Phase 13 plan 13-04 -- fire-and-forget brain ingest (MEM-04 / CHAT-03).
                    # User messages only; idempotent via librechat_id-derived key.
                    # Independent of post_message above -- runs in parallel via create_task.
                    asyncio.create_task(
                        _maybe_ingest_to_brain(payload, mem, team_scope)
                    )
                    # Phase 8 plan 08-06 — fire-and-forget contact extraction (D3 RESEARCH.md)
                    # Process BOTH user AND assistant messages (Open Question 2 resolution).
                    if payload.get("content"):
                        asyncio.create_task(
                            extract_contacts_from_message(
                                content=payload["content"],
                                sub=payload["sub"],
                                team_scope=team_scope,
                                source=payload.get("source") or "librechat",
                                source_ref=payload["metadata"].get("librechat_id"),
                            )
                        )
                save_resume_token(change.get("_id"))
                _heartbeat()
            except Exception as e:  # noqa: BLE001
                log.error(
                    "messages_watch_event_failed",
                    err=str(e),
                    err_type=type(e).__name__,
                )
                continue


async def conversations_watch_loop(db, mem: MemoryApiClient) -> None:
    """RAG enrichment: on new/updated conv, inject CANONICAL facts as system message."""
    # Watch both insert (new conv) and update (title set later by LibreChat)
    pipeline = [
        {
            "$match": {
                "operationType": {"$in": ["insert", "update"]},
                "ns.coll": "conversations",
            }
        }
    ]
    log.info("starting_conversations_change_stream")

    async with db.watch(pipeline=pipeline, full_document="updateLookup") as stream:
        async for change in stream:
            try:
                conv_doc = change.get("fullDocument") or {}
                if not conv_doc:
                    continue
                sub = await _resolve_sub_for_conv(conv_doc, db)
                if not sub:
                    log.debug("conv_skipped_no_sub", conv=conv_doc.get("conversationId"))
                    continue
                team_scope = await resolve_team_scope(mem, sub)
                await enrich_new_conversation(
                    conv_doc, db, mem, sub=sub, team_scope=team_scope
                )
                _heartbeat()
            except Exception as e:  # noqa: BLE001
                log.error(
                    "conversations_watch_event_failed",
                    err=str(e),
                    err_type=type(e).__name__,
                )
                continue


async def watch_loop() -> None:
    """Run both change-stream watchers in parallel — restart if either dies."""
    mongo = AsyncIOMotorClient(settings.LIBRECHAT_MONGO_URI)
    db = mongo.get_default_database()
    mem = MemoryApiClient()
    try:
        await asyncio.gather(
            messages_watch_loop(db, mem),
            conversations_watch_loop(db, mem),
        )
    finally:
        await mem.aclose()
        mongo.close()


def _heartbeat() -> None:
    try:
        with open(settings.BRIDGE_HEARTBEAT_PATH, "w") as f:
            f.write(str(int(time.time())))
    except Exception:
        pass


async def heartbeat_loop() -> None:
    """Even if Mongo is silent, keep the docker healthcheck file fresh."""
    while True:
        _heartbeat()
        await asyncio.sleep(20)
