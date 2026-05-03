"""Shared fixtures for bridge tests."""

import os

# --- Defaults so app.config import doesn't blow up in unit tests ---
os.environ.setdefault("LIBRECHAT_MONGO_URI", "mongodb://localhost:27017/test")
os.environ.setdefault("BRIDGE_SHARED_SECRET", "test-bridge-secret-do-not-use-in-prod")
os.environ.setdefault("MEMORY_API_URL", "http://memory-api.test")
os.environ.setdefault("BRIDGE_DEFAULT_TEAM_SCOPE", "default")

import pytest
import pytest_asyncio
from bson import ObjectId


@pytest_asyncio.fixture
async def mongomock_db():
    """In-memory Mongo via mongomock-motor with seeded collections."""
    from mongomock_motor import AsyncMongoMockClient

    client = AsyncMongoMockClient()
    db = client["LibreChat"]

    # Seed users
    alice_id = ObjectId()
    bob_id = ObjectId()
    await db["users"].insert_many(
        [
            {"_id": alice_id, "email": "alice@test.local", "googleId": "alice-google-sub"},
            {"_id": bob_id, "email": "bob@test.local"},  # no googleId, falls back to email
        ]
    )
    # Seed conversations
    await db["conversations"].insert_many(
        [
            {"conversationId": "conv-1", "user": str(alice_id), "model": "claude-3-5-sonnet"},
            {"conversationId": "conv-2", "user": str(bob_id), "model": "gpt-4o"},
        ]
    )

    yield db, alice_id, bob_id
