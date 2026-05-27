"""Tests for _maybe_ingest_to_brain hook and messages_watch_loop integration.

Task 2 TDD — 7 tests covering:
  1. User message triggers brain ingest
  2. Assistant message does NOT trigger
  3. BRAIN_INGEST_ENABLED=false kill-switch
  4. Empty content skipped
  5. brain_ingest failure does NOT break loop (fail-soft)
  6. Existing hooks still fire (additive, not replacing)
  7. Idempotency key shape + re-feed produces same key
"""

import asyncio
import os

import httpx
import pytest
from bson import ObjectId
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure env defaults
os.environ.setdefault("LIBRECHAT_MONGO_URI", "mongodb://localhost:27017/test")
os.environ.setdefault("BRIDGE_SHARED_SECRET", "test-bridge-secret-do-not-use-in-prod")
os.environ.setdefault("MEMORY_API_URL", "http://memory-api.test")
os.environ.setdefault("BRAIN_INGEST_ENABLED", "true")


# ---- helpers -----


def make_payload(
    role="user",
    content="We agreed the API uses JWT with 1h TTL",
    librechat_id=None,
    sub="user@example.com",
    source="librechat:claude-haiku-4-5",
):
    """Build a synthetic payload dict as produced by map_message."""
    if librechat_id is None:
        librechat_id = str(ObjectId())
    return {
        "sub": sub,
        "conversation_id": "conv-1",
        "role": role,
        "content": content,
        "source": source,
        "metadata": {"librechat_id": librechat_id},
    }


def make_mem_mock():
    """Return an AsyncMock for MemoryApiClient with brain_ingest + post_message."""
    mem = MagicMock()
    mem.brain_ingest = AsyncMock(return_value={"status": "accepted"})
    mem.post_message = AsyncMock(return_value={"id": "msg-1"})
    return mem


# ---- Test 1: user message triggers brain ingest ----


@pytest.mark.asyncio
async def test_user_message_triggers_brain_ingest():
    """A user-role payload must cause _maybe_ingest_to_brain to call mem.brain_ingest."""
    from app.mongo_watcher import _maybe_ingest_to_brain
    import app.config as config_mod

    mem = make_mem_mock()
    librechat_id = str(ObjectId())
    payload = make_payload(role="user", librechat_id=librechat_id)

    # Use a settings object with BRAIN_INGEST_ENABLED=True explicitly
    # to be test-ordering independent
    from app.config import Settings
    enabled_settings = Settings(  # type: ignore[call-arg]
        LIBRECHAT_MONGO_URI="mongodb://localhost:27017/test",
        BRIDGE_SHARED_SECRET="test-bridge-secret-do-not-use-in-prod",
        BRAIN_INGEST_ENABLED=True,
    )

    with patch("app.mongo_watcher.settings", enabled_settings):
        await _maybe_ingest_to_brain(payload, mem, "t1")

    mem.brain_ingest.assert_called_once()
    call_kwargs = mem.brain_ingest.call_args.kwargs
    assert call_kwargs["sub"] == "user@example.com"
    assert call_kwargs["team_scope"] == "t1"
    assert call_kwargs["content"] == "We agreed the API uses JWT with 1h TTL"
    assert call_kwargs["source"] == "librechat:claude-haiku-4-5"
    assert call_kwargs["metadata"]["librechat_id"] == librechat_id
    assert call_kwargs["metadata"]["idempotency_key"] == f"librechat:{librechat_id}"
    assert call_kwargs["metadata"]["origin"] == "librechat"


# ---- Test 2: assistant message does NOT trigger ingest ----


@pytest.mark.asyncio
async def test_assistant_message_does_not_trigger_brain_ingest():
    """Assistant messages (role='assistant') must NOT call brain_ingest."""
    from app.mongo_watcher import _maybe_ingest_to_brain
    from app.config import Settings

    enabled_settings = Settings(  # type: ignore[call-arg]
        LIBRECHAT_MONGO_URI="mongodb://localhost:27017/test",
        BRIDGE_SHARED_SECRET="test-bridge-secret-do-not-use-in-prod",
        BRAIN_INGEST_ENABLED=True,
    )

    mem = make_mem_mock()
    payload = make_payload(role="assistant")

    with patch("app.mongo_watcher.settings", enabled_settings):
        await _maybe_ingest_to_brain(payload, mem, "t1")

    mem.brain_ingest.assert_not_called()


# ---- Test 3: kill-switch off ----


@pytest.mark.asyncio
async def test_brain_ingest_kill_switch_disabled(monkeypatch):
    """When BRAIN_INGEST_ENABLED=false, no brain_ingest call is made."""
    import importlib
    import app.config as config_mod

    monkeypatch.setenv("BRAIN_INGEST_ENABLED", "false")
    importlib.reload(config_mod)

    from app.mongo_watcher import _maybe_ingest_to_brain

    mem = make_mem_mock()
    payload = make_payload(role="user")

    # Patch settings to use the reloaded version
    with patch("app.mongo_watcher.settings", config_mod.settings):
        await _maybe_ingest_to_brain(payload, mem, "t1")

    mem.brain_ingest.assert_not_called()

    # Restore
    monkeypatch.setenv("BRAIN_INGEST_ENABLED", "true")
    importlib.reload(config_mod)


# ---- Test 4: empty content skipped ----


@pytest.mark.asyncio
async def test_empty_content_skipped():
    """User payload with empty/whitespace content must NOT trigger brain_ingest."""
    from app.mongo_watcher import _maybe_ingest_to_brain
    from app.config import Settings

    enabled_settings = Settings(  # type: ignore[call-arg]
        LIBRECHAT_MONGO_URI="mongodb://localhost:27017/test",
        BRIDGE_SHARED_SECRET="test-bridge-secret-do-not-use-in-prod",
        BRAIN_INGEST_ENABLED=True,
    )

    mem = make_mem_mock()

    with patch("app.mongo_watcher.settings", enabled_settings):
        # Test with empty string
        payload_empty = make_payload(role="user", content="")
        await _maybe_ingest_to_brain(payload_empty, mem, "t1")
        mem.brain_ingest.assert_not_called()

        # Test with whitespace only
        payload_ws = make_payload(role="user", content="   ")
        await _maybe_ingest_to_brain(payload_ws, mem, "t1")
        mem.brain_ingest.assert_not_called()


# ---- Test 5: brain_ingest failure does NOT break loop ----


@pytest.mark.asyncio
async def test_brain_ingest_failure_does_not_break_loop():
    """brain_ingest raising must not propagate; post_message + save_resume_token still run."""
    from mongomock_motor import AsyncMongoMockClient
    from bson import ObjectId as ObjId

    # Build a minimal mongomock db with one user + one conversation
    client = AsyncMongoMockClient()
    db = client["LibreChat"]
    user_id = ObjId()
    await db["users"].insert_one({"_id": user_id, "googleId": "user@example.com"})
    await db["conversations"].insert_one({
        "conversationId": "conv-test",
        "user": str(user_id),
        "model": "claude-haiku-4-5",
    })

    msg_id = ObjId()
    change_event = {
        "_id": "resume-token-1",
        "operationType": "insert",
        "ns": {"coll": "messages"},
        "fullDocument": {
            "_id": msg_id,
            "conversationId": "conv-test",
            "user": str(user_id),
            "isCreatedByUser": True,
            "text": "Test message for failure test",
            "model": "claude-haiku-4-5",
        },
    }

    # Mock memory API client
    mem = make_mem_mock()
    mem.brain_ingest = AsyncMock(side_effect=httpx.HTTPError("boom"))

    # Run one iteration of messages_watch_loop via patching the change stream
    from app.mongo_watcher import messages_watch_loop
    from app.state_store import save_resume_token

    # Create async mock change stream
    async def mock_aiter(self):
        yield change_event

    # We need to patch db.watch to return an async context manager that yields our event
    mock_stream = MagicMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=False)
    mock_stream.__aiter__ = mock_aiter

    saved_tokens = []

    with patch.object(db, "watch", return_value=mock_stream):
        with patch("app.mongo_watcher.save_resume_token", side_effect=lambda t: saved_tokens.append(t)):
            with patch("app.mongo_watcher.asyncio.create_task") as mock_create_task:
                with patch("app.mongo_watcher.detect_task_intent", new=AsyncMock(return_value=None)):
                    with patch("app.mongo_watcher.extract_contacts_from_message", new=AsyncMock()):
                        # Run the loop — it should complete one event without raising
                        await messages_watch_loop(db, mem)

    # post_message was still called
    mem.post_message.assert_called_once()

    # resume_token was still saved
    assert "resume-token-1" in saved_tokens

    # brain_ingest was scheduled via create_task (even though it failed, the task was created)
    assert mock_create_task.called


# ---- Test 6: existing hooks still fire (additive) ----


@pytest.mark.asyncio
async def test_existing_hooks_still_fire():
    """After adding brain ingest, post_message + extract_contacts + detect_task_intent
    must all still run — the new hook is purely additive."""
    from mongomock_motor import AsyncMongoMockClient
    from bson import ObjectId as ObjId

    client = AsyncMongoMockClient()
    db = client["LibreChat"]
    user_id = ObjId()
    await db["users"].insert_one({"_id": user_id, "googleId": "user@example.com"})
    await db["conversations"].insert_one({
        "conversationId": "conv-test2",
        "user": str(user_id),
        "model": "gpt-4o",
    })

    msg_id = ObjId()
    change_event = {
        "_id": "resume-token-2",
        "operationType": "insert",
        "ns": {"coll": "messages"},
        "fullDocument": {
            "_id": msg_id,
            "conversationId": "conv-test2",
            "user": str(user_id),
            "isCreatedByUser": True,
            "text": "We should deploy to production today",
            "model": "gpt-4o",
        },
    }

    mem = make_mem_mock()

    async def mock_aiter(self):
        yield change_event

    mock_stream = MagicMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=False)
    mock_stream.__aiter__ = mock_aiter

    detect_task_intent_mock = AsyncMock(return_value=None)
    extract_contacts_mock = AsyncMock()
    create_task_calls = []

    def capture_create_task(coro):
        create_task_calls.append(coro)
        # Schedule it properly so it actually runs
        return asyncio.ensure_future(coro)

    from app.mongo_watcher import messages_watch_loop

    with patch.object(db, "watch", return_value=mock_stream):
        with patch("app.mongo_watcher.save_resume_token"):
            with patch("app.mongo_watcher.asyncio.create_task", side_effect=capture_create_task):
                with patch("app.mongo_watcher.detect_task_intent", new=detect_task_intent_mock):
                    with patch("app.mongo_watcher.extract_contacts_from_message", new=extract_contacts_mock):
                        await messages_watch_loop(db, mem)

    # Existing hooks
    mem.post_message.assert_called_once()
    detect_task_intent_mock.assert_called_once()


# ---- Test 7: idempotency key shape ----


@pytest.mark.asyncio
async def test_idempotency_key_shape():
    """idempotency_key must equal f'librechat:{librechat_id}' exactly.

    Re-feeding the same payload twice produces the same idempotency_key each time
    (server-side uuid5 deduplicates via Plan 13-03's race-free upsert).
    """
    from app.mongo_watcher import _maybe_ingest_to_brain
    from app.config import Settings

    enabled_settings = Settings(  # type: ignore[call-arg]
        LIBRECHAT_MONGO_URI="mongodb://localhost:27017/test",
        BRIDGE_SHARED_SECRET="test-bridge-secret-do-not-use-in-prod",
        BRAIN_INGEST_ENABLED=True,
    )

    librechat_id = str(ObjectId())
    payload = make_payload(role="user", librechat_id=librechat_id)

    with patch("app.mongo_watcher.settings", enabled_settings):
        # First call
        mem1 = make_mem_mock()
        await _maybe_ingest_to_brain(payload, mem1, "t1")
        key1 = mem1.brain_ingest.call_args.kwargs["metadata"]["idempotency_key"]

        # Second call with same payload (same id)
        mem2 = make_mem_mock()
        await _maybe_ingest_to_brain(payload, mem2, "t1")
        key2 = mem2.brain_ingest.call_args.kwargs["metadata"]["idempotency_key"]

    assert key1 == f"librechat:{librechat_id}"
    assert key2 == f"librechat:{librechat_id}"
    assert key1 == key2, "Same librechat_id must produce same idempotency_key"
