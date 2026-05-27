"""Tests for message_enricher.enrich_turn (Phase 13 CHAT-07 / D5).

TDD RED phase — all tests must fail before implementation.

Tests:
  1. Happy path: facts returned → system message inserted, returns True
  2. No facts → no insertion, returns False
  3. Idempotency: already injected → early return False (before memory-api call)
  4. memory-api failure swallowed → returns False, logs warning
  5. db.insert_one failure swallowed → returns False, logs warning
  6. min_level=VALIDATED + top_k=5 passed to get_system_prompt
  7. Query truncated to 500 chars
  8. Missing conversationId → returns False without calling mem
  9. Missing msg _id → returns False without calling mem
  10. mongo_watcher integration: enrich_turn scheduled for user messages, not assistant
"""

from __future__ import annotations

import asyncio
import os

import httpx
import pytest
from bson import ObjectId
from unittest.mock import AsyncMock, MagicMock, patch, call

# Ensure env defaults
os.environ.setdefault("LIBRECHAT_MONGO_URI", "mongodb://localhost:27017/test")
os.environ.setdefault("BRIDGE_SHARED_SECRET", "test-bridge-secret-do-not-use-in-prod")
os.environ.setdefault("MEMORY_API_URL", "http://memory-api.test")
os.environ.setdefault("BRAIN_INGEST_ENABLED", "true")

pytestmark = pytest.mark.asyncio


# ---- helpers ----


def make_msg_doc(
    conv_id: str = "conv-1",
    text: str = "What is our deploy window?",
    model: str = "claude-haiku-4-5",
    msg_id: ObjectId | None = None,
) -> dict:
    """Build a synthetic Mongo messages fullDocument."""
    if msg_id is None:
        msg_id = ObjectId()
    return {
        "_id": msg_id,
        "conversationId": conv_id,
        "text": text,
        "model": model,
        "isCreatedByUser": True,
    }


def make_db_mock(find_one_return=None):
    """Return a dict-of-AsyncMocks mimicking motor db['messages']."""
    messages_coll = MagicMock()
    messages_coll.find_one = AsyncMock(return_value=find_one_return)
    messages_coll.insert_one = AsyncMock(return_value=MagicMock())

    db = MagicMock()
    db.__getitem__ = MagicMock(return_value=messages_coll)
    return db, messages_coll


def make_mem_mock(addendum: str = "## Team facts\n- fact one", fact_count: int = 1):
    """Return an AsyncMock for MemoryApiClient.get_system_prompt."""
    mem = MagicMock()
    mem.get_system_prompt = AsyncMock(
        return_value={"system_addendum": addendum, "fact_count": fact_count}
    )
    mem.post_message = AsyncMock(return_value={"id": "msg-1"})
    mem.brain_ingest = AsyncMock(return_value={"status": "accepted"})
    return mem


# ---- Test 1: happy path — facts returned ----


async def test_enrich_turn_happy_path_inserts_system_message():
    """Happy path: memory-api returns facts → system message inserted, returns True."""
    from app.message_enricher import enrich_turn

    msg_id = ObjectId()
    msg_doc = make_msg_doc(conv_id="conv-1", msg_id=msg_id)
    db, messages_coll = make_db_mock(find_one_return=None)  # not yet injected
    mem = make_mem_mock(addendum="## Team facts\n- fact one", fact_count=3)

    result = await enrich_turn(msg_doc, db, mem, sub="u@e.com", team_scope="t1")

    assert result is True
    messages_coll.insert_one.assert_called_once()
    inserted = messages_coll.insert_one.call_args[0][0]
    assert inserted["messageId"] == f"xbrain-turn-conv-1-{msg_id}"
    assert inserted["conversationId"] == "conv-1"
    assert inserted["user"] == "system"
    assert inserted["isCreatedByUser"] is False
    assert inserted["metadata"]["xbrain_turn_enrichment"] is True
    assert inserted["metadata"]["fact_count"] == 3
    assert inserted["metadata"]["trigger_msg_id"] == str(msg_id)
    assert inserted["metadata"]["source"] == "memory-api:rag-validated"
    assert inserted["metadata"]["xbrain_injected"] is True


# ---- Test 2: no facts → no injection ----


async def test_enrich_turn_empty_addendum_does_not_insert():
    """Empty system_addendum → no system message inserted, returns False."""
    from app.message_enricher import enrich_turn

    msg_doc = make_msg_doc()
    db, messages_coll = make_db_mock(find_one_return=None)
    mem = make_mem_mock(addendum="", fact_count=0)

    result = await enrich_turn(msg_doc, db, mem, sub="u@e.com", team_scope="t1")

    assert result is False
    messages_coll.insert_one.assert_not_called()


# ---- Test 3: idempotency — already injected ----


async def test_enrich_turn_idempotency_skips_if_already_injected():
    """If find_one returns an existing doc, skip everything (even mem call), return False."""
    from app.message_enricher import enrich_turn

    msg_id = ObjectId()
    msg_doc = make_msg_doc(conv_id="conv-1", msg_id=msg_id)
    existing_doc = {"messageId": f"xbrain-turn-conv-1-{msg_id}"}
    db, messages_coll = make_db_mock(find_one_return=existing_doc)
    mem = make_mem_mock()

    result = await enrich_turn(msg_doc, db, mem, sub="u@e.com", team_scope="t1")

    assert result is False
    # CRITICAL: mem.get_system_prompt must NOT be called (early return before API call)
    mem.get_system_prompt.assert_not_called()
    messages_coll.insert_one.assert_not_called()


# ---- Test 4: memory-api failure swallowed ----


async def test_enrich_turn_memapi_failure_swallowed():
    """HTTPError from get_system_prompt must not propagate; returns False and logs warning."""
    import structlog.testing

    from app.message_enricher import enrich_turn

    msg_doc = make_msg_doc()
    db, messages_coll = make_db_mock(find_one_return=None)
    mem = MagicMock()
    mem.get_system_prompt = AsyncMock(side_effect=httpx.HTTPError("boom"))

    with structlog.testing.capture_logs() as captured:
        result = await enrich_turn(msg_doc, db, mem, sub="u@e.com", team_scope="t1")

    assert result is False
    messages_coll.insert_one.assert_not_called()
    # Should log a warning
    warning_events = [e for e in captured if e.get("log_level") == "warning"]
    assert any("message_enricher.memapi_failed" in str(e) for e in warning_events), (
        f"Expected 'message_enricher.memapi_failed' warning, got: {captured}"
    )


# ---- Test 5: db.insert_one failure swallowed ----


async def test_enrich_turn_db_insert_failure_swallowed():
    """RuntimeError from db.insert_one must not propagate; returns False."""
    from app.message_enricher import enrich_turn

    msg_doc = make_msg_doc()
    db, messages_coll = make_db_mock(find_one_return=None)
    mem = make_mem_mock(addendum="## facts", fact_count=1)
    messages_coll.insert_one = AsyncMock(side_effect=RuntimeError("DB write failed"))

    result = await enrich_turn(msg_doc, db, mem, sub="u@e.com", team_scope="t1")

    assert result is False


# ---- Test 6: min_level=VALIDATED and top_k=5 passed ----


async def test_enrich_turn_passes_validated_min_level_and_top_k():
    """enrich_turn must pass min_level=settings.CHAT07_TRUTH_FILTER_MIN_LEVEL and top_k=settings.CHAT07_TOP_K."""
    from app.config import settings
    from app.message_enricher import enrich_turn

    msg_doc = make_msg_doc()
    db, _ = make_db_mock(find_one_return=None)
    mem = make_mem_mock()

    await enrich_turn(msg_doc, db, mem, sub="u@e.com", team_scope="t1")

    mem.get_system_prompt.assert_called_once()
    kwargs = mem.get_system_prompt.call_args.kwargs
    assert kwargs.get("min_level") == settings.CHAT07_TRUTH_FILTER_MIN_LEVEL
    assert kwargs.get("top_k") == settings.CHAT07_TOP_K


# ---- Test 7: query truncated to 500 chars ----


async def test_enrich_turn_query_truncated_to_500_chars():
    """Message text longer than 500 chars must be truncated to 500 before API call."""
    from app.message_enricher import enrich_turn

    long_text = "a" * 1000
    msg_doc = make_msg_doc(text=long_text)
    db, _ = make_db_mock(find_one_return=None)
    mem = make_mem_mock()

    await enrich_turn(msg_doc, db, mem, sub="u@e.com", team_scope="t1")

    kwargs = mem.get_system_prompt.call_args.kwargs
    assert len(kwargs["query"]) <= 500


# ---- Test 8: missing conversationId → skip ----


async def test_enrich_turn_missing_conv_id_returns_false():
    """msg_doc without conversationId must return False without calling mem."""
    from app.message_enricher import enrich_turn

    msg_doc = {"_id": ObjectId(), "text": "something"}  # no conversationId
    db, messages_coll = make_db_mock(find_one_return=None)
    mem = make_mem_mock()

    result = await enrich_turn(msg_doc, db, mem, sub="u@e.com", team_scope="t1")

    assert result is False
    mem.get_system_prompt.assert_not_called()


# ---- Test 9: missing _id → skip ----


async def test_enrich_turn_missing_msg_id_returns_false():
    """msg_doc without _id must return False without calling mem."""
    from app.message_enricher import enrich_turn

    msg_doc = {"conversationId": "conv-1", "text": "something"}  # no _id
    db, messages_coll = make_db_mock(find_one_return=None)
    mem = make_mem_mock()

    result = await enrich_turn(msg_doc, db, mem, sub="u@e.com", team_scope="t1")

    assert result is False
    mem.get_system_prompt.assert_not_called()


# ---- Test 10: mongo_watcher integration ----


async def test_messages_watch_loop_schedules_enrich_turn_for_user_messages_only():
    """User-message events schedule enrich_turn; assistant messages do NOT."""
    from mongomock_motor import AsyncMongoMockClient

    # Build minimal Mongo with user + conversation
    client = AsyncMongoMockClient()
    db = client["LibreChat"]
    user_id = ObjectId()
    await db["users"].insert_one({"_id": user_id, "googleId": "user@example.com"})
    await db["conversations"].insert_one({
        "conversationId": "conv-test",
        "user": str(user_id),
        "model": "claude-haiku-4-5",
    })

    msg_id = ObjectId()
    user_change_event = {
        "_id": "resume-token-enrich",
        "operationType": "insert",
        "ns": {"coll": "messages"},
        "fullDocument": {
            "_id": msg_id,
            "conversationId": "conv-test",
            "user": str(user_id),
            "isCreatedByUser": True,
            "text": "When is the next deploy window?",
            "model": "claude-haiku-4-5",
        },
    }

    mem = MagicMock()
    mem.post_message = AsyncMock(return_value={"id": "msg-1"})
    mem.brain_ingest = AsyncMock(return_value={"status": "accepted"})

    async def mock_aiter(self):
        yield user_change_event

    mock_stream = MagicMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=False)
    mock_stream.__aiter__ = mock_aiter

    from app.mongo_watcher import messages_watch_loop

    # Track coroutine names passed to create_task (check scheduling, not execution)
    scheduled_coro_names = []
    enrich_turn_calls = []

    async def fake_enrich_turn(msg_doc, db_arg, mem_arg, *, sub, team_scope):
        enrich_turn_calls.append({"sub": sub, "team_scope": team_scope})
        return True

    def capture_create_task(coro):
        scheduled_coro_names.append(getattr(coro, "__qualname__", repr(coro)))
        # Run synchronously to capture call args
        return asyncio.ensure_future(coro)

    with patch.object(db, "watch", return_value=mock_stream):
        with patch("app.mongo_watcher.save_resume_token"):
            with patch("app.mongo_watcher.asyncio.create_task", side_effect=capture_create_task):
                with patch("app.mongo_watcher.detect_task_intent", new=AsyncMock(return_value=None)):
                    with patch("app.mongo_watcher.extract_contacts_from_message", new=AsyncMock()):
                        with patch("app.mongo_watcher.enrich_turn", fake_enrich_turn):
                            await messages_watch_loop(db, mem)
                            # Give scheduled tasks a chance to run
                            await asyncio.sleep(0)

    # enrich_turn coroutine must have been scheduled for the user message
    assert any("enrich_turn" in name or "fake_enrich_turn" in name for name in scheduled_coro_names), (
        f"Expected create_task to schedule enrich_turn, got: {scheduled_coro_names}"
    )


async def test_messages_watch_loop_does_not_enrich_assistant_messages():
    """Assistant-message events (isCreatedByUser=False) must NOT schedule enrich_turn."""
    from mongomock_motor import AsyncMongoMockClient

    client = AsyncMongoMockClient()
    db = client["LibreChat"]
    user_id = ObjectId()
    await db["users"].insert_one({"_id": user_id, "googleId": "user@example.com"})
    await db["conversations"].insert_one({
        "conversationId": "conv-asst",
        "user": str(user_id),
        "model": "claude-haiku-4-5",
    })

    msg_id = ObjectId()
    assistant_change_event = {
        "_id": "resume-token-asst",
        "operationType": "insert",
        "ns": {"coll": "messages"},
        "fullDocument": {
            "_id": msg_id,
            "conversationId": "conv-asst",
            "user": str(user_id),
            "isCreatedByUser": False,  # assistant message
            "text": "Here is the deploy schedule...",
            "model": "claude-haiku-4-5",
        },
    }

    mem = MagicMock()
    mem.post_message = AsyncMock(return_value={"id": "msg-1"})
    mem.brain_ingest = AsyncMock(return_value={"status": "accepted"})

    async def mock_aiter(self):
        yield assistant_change_event

    mock_stream = MagicMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=False)
    mock_stream.__aiter__ = mock_aiter

    from app.mongo_watcher import messages_watch_loop

    # Track which coroutines get scheduled
    scheduled_coro_names = []

    async def fake_enrich_turn(msg_doc, db_arg, mem_arg, *, sub, team_scope):
        return False

    def capture_create_task(coro):
        scheduled_coro_names.append(getattr(coro, "__qualname__", repr(coro)))
        return asyncio.ensure_future(coro)

    with patch.object(db, "watch", return_value=mock_stream):
        with patch("app.mongo_watcher.save_resume_token"):
            with patch("app.mongo_watcher.asyncio.create_task", side_effect=capture_create_task):
                with patch("app.mongo_watcher.detect_task_intent", new=AsyncMock(return_value=None)):
                    with patch("app.mongo_watcher.extract_contacts_from_message", new=AsyncMock()):
                        with patch("app.mongo_watcher.enrich_turn", fake_enrich_turn):
                            await messages_watch_loop(db, mem)
                            await asyncio.sleep(0)

    # enrich_turn must NOT be scheduled for assistant messages
    enrich_scheduled = any(
        "enrich_turn" in name or "fake_enrich_turn" in name for name in scheduled_coro_names
    )
    assert not enrich_scheduled, (
        f"enrich_turn must NOT be scheduled for assistant messages, scheduled: {scheduled_coro_names}"
    )
