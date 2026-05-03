"""Test that map_message correctly translates Mongo docs to memory-api payloads."""

import pytest

from app.mongo_watcher import map_message


@pytest.mark.asyncio
async def test_map_user_message_returns_role_user(mongomock_db):
    db, alice_id, _ = mongomock_db
    doc = {
        "_id": "abc123",
        "conversationId": "conv-1",
        "user": str(alice_id),
        "isCreatedByUser": True,
        "text": "hello",
    }
    payload = await map_message(doc, db)
    assert payload is not None
    assert payload["role"] == "user"
    assert payload["content"] == "hello"


@pytest.mark.asyncio
async def test_map_assistant_message_returns_role_assistant(mongomock_db):
    db, alice_id, _ = mongomock_db
    doc = {
        "_id": "abc456",
        "conversationId": "conv-1",
        "user": str(alice_id),
        "isCreatedByUser": False,
        "text": "Hi! How can I help?",
        "model": "claude-3-5-sonnet",
    }
    payload = await map_message(doc, db)
    assert payload["role"] == "assistant"


@pytest.mark.asyncio
async def test_map_source_format_is_librechat_colon_model(mongomock_db):
    """The source field MUST follow `librechat:{model}` convention."""
    db, alice_id, _ = mongomock_db
    doc = {
        "_id": "abc",
        "conversationId": "conv-1",
        "user": str(alice_id),
        "isCreatedByUser": True,
        "text": "x",
    }
    payload = await map_message(doc, db)
    assert payload["source"] == "librechat:claude-3-5-sonnet"
    assert payload["source"].startswith("librechat:")


@pytest.mark.asyncio
async def test_map_extracts_model_from_message_when_present(mongomock_db):
    """If model is in the message doc itself, prefer it over the conversation's."""
    db, alice_id, _ = mongomock_db
    doc = {
        "_id": "abc",
        "conversationId": "conv-1",
        "user": str(alice_id),
        "isCreatedByUser": False,
        "text": "x",
        "model": "claude-3-opus",  # overrides conv-1's claude-3-5-sonnet
    }
    payload = await map_message(doc, db)
    assert payload["source"] == "librechat:claude-3-opus"


@pytest.mark.asyncio
async def test_map_skips_message_without_conversation_id(mongomock_db):
    db, alice_id, _ = mongomock_db
    doc = {"_id": "x", "user": str(alice_id), "isCreatedByUser": True, "text": "y"}
    payload = await map_message(doc, db)
    assert payload is None


@pytest.mark.asyncio
async def test_map_uses_googleId_as_sub_when_present(mongomock_db):
    db, alice_id, _ = mongomock_db
    doc = {
        "_id": "abc",
        "conversationId": "conv-1",
        "user": str(alice_id),
        "isCreatedByUser": True,
        "text": "x",
    }
    payload = await map_message(doc, db)
    assert payload["sub"] == "alice-google-sub"


@pytest.mark.asyncio
async def test_map_falls_back_to_email_when_no_googleId(mongomock_db):
    db, _, bob_id = mongomock_db
    doc = {
        "_id": "abc",
        "conversationId": "conv-2",
        "user": str(bob_id),
        "isCreatedByUser": True,
        "text": "x",
    }
    payload = await map_message(doc, db)
    assert payload["sub"] == "bob@test.local"


@pytest.mark.asyncio
async def test_map_includes_librechat_id_in_metadata(mongomock_db):
    """Metadata should carry the original Mongo _id for future deduplication."""
    db, alice_id, _ = mongomock_db
    doc = {
        "_id": "unique-id-42",
        "conversationId": "conv-1",
        "user": str(alice_id),
        "isCreatedByUser": True,
        "text": "x",
    }
    payload = await map_message(doc, db)
    assert payload["metadata"]["librechat_id"] == "unique-id-42"
