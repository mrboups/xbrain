"""Watch LibreChat MongoDB change streams and forward messages to memory-api."""

import asyncio
import time

import structlog
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from app.config import settings
from app.memory_api_client import MemoryApiClient
from app.state_store import load_resume_token, save_resume_token

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


async def watch_loop() -> None:
    """Main loop: open change stream, dispatch each insert."""
    mongo = AsyncIOMotorClient(settings.LIBRECHAT_MONGO_URI)
    db = mongo.get_default_database()
    mem = MemoryApiClient()
    resume_token = load_resume_token()

    pipeline = [{"$match": {"operationType": "insert", "ns.coll": "messages"}}]
    log.info("starting_change_stream", resume=bool(resume_token))

    try:
        async with db.watch(pipeline=pipeline, resume_after=resume_token) as stream:
            async for change in stream:
                try:
                    doc = change["fullDocument"]
                    payload = await map_message(doc, db)
                    if payload:
                        team_scope = await resolve_team_scope(mem, payload["sub"])
                        await mem.post_message(team_scope=team_scope, **payload)
                        log.info(
                            "forwarded_message",
                            lc_id=payload["metadata"]["librechat_id"],
                            team=team_scope,
                            source=payload["source"],
                        )
                    save_resume_token(change.get("_id"))
                    _heartbeat()
                except Exception as e:  # noqa: BLE001
                    log.error("watch_event_failed", err=str(e), err_type=type(e).__name__)
                    continue
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
