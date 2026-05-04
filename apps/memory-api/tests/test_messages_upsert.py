"""Tests for the silent upsert behaviour of POST /v1/messages (Décision 4-D-02).

When POST /v1/messages is called with a conversation_id that does not exist yet,
the endpoint must create the conversation row silently (ON CONFLICT DO NOTHING) and
return 201 — not 404. This fixes the Phase 2 gap where the Open WebUI pipeline sent
messages without pre-creating conversations.

All tests are integration-level (require Postgres via testcontainers).
"""

import uuid

import pytest
from sqlalchemy import select, text

from app.models.conversation import Conversation

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FULL_TAGGING = {
    "team_scope": "team-a",
    "visibility": "team",
    "confidence": 0.9,
    "truth_level": "EPHEMERAL",
    "source": "openwebui:gpt-4o",
    "validation_status": "pending",
}


async def _post_message(client, token: str, conversation_id: str, team: str = "team-a") -> dict:
    """POST /v1/messages and return the httpx response."""
    tagging = dict(_FULL_TAGGING)
    tagging["team_scope"] = team
    return await client.post(
        "/v1/messages",
        headers={"Authorization": f"Bearer {token}", "X-Team-Scope": team},
        json={
            "conversation_id": conversation_id,
            "role": "user",
            "content": "test message from pipeline",
            "metadata": {},
            "tagging": tagging,
        },
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_post_message_unknown_conv_returns_201(client, seeded_two_teams, bridge_jwt):
    """POST /v1/messages with an unknown conversation_id must return 201 (not 404)."""
    token = bridge_jwt(sub="alice-sub", team_scope="team-a")
    unknown_id = str(uuid.uuid4())

    r = await _post_message(client, token, unknown_id)

    assert r.status_code == 201, r.text
    data = r.json()
    assert data["conversation_id"] == unknown_id
    assert data["role"] == "user"
    assert data["tagging"]["team_scope"] == "team-a"


async def test_post_message_unknown_conv_creates_conversation_row(client, session, seeded_two_teams, bridge_jwt):
    """After the silent upsert, a conversations row must exist with title=NULL."""
    token = bridge_jwt(sub="alice-sub", team_scope="team-a")
    unknown_id = str(uuid.uuid4())

    r = await _post_message(client, token, unknown_id)
    assert r.status_code == 201, r.text

    # Check that the conversation row was created
    result = await session.execute(
        select(Conversation).where(Conversation.id == uuid.UUID(unknown_id))
    )
    conv = result.scalar_one_or_none()

    assert conv is not None, "Conversation row should have been created by silent upsert"
    assert conv.team_scope == "team-a"
    assert conv.title is None, "title must be NULL (pipeline does not know it)"
    assert conv.source == "openwebui:gpt-4o"


async def test_post_message_upsert_is_idempotent(client, seeded_two_teams, bridge_jwt):
    """Calling POST /v1/messages twice with the same unknown conversation_id must return 201 both times."""
    token = bridge_jwt(sub="alice-sub", team_scope="team-a")
    unknown_id = str(uuid.uuid4())

    r1 = await _post_message(client, token, unknown_id)
    assert r1.status_code == 201, r1.text

    r2 = await _post_message(client, token, unknown_id)
    assert r2.status_code == 201, r2.text


async def test_post_message_idempotent_creates_only_one_conversation_row(client, session, seeded_two_teams, bridge_jwt):
    """Two concurrent upserts with the same UUID must result in exactly one conversation row."""
    token = bridge_jwt(sub="alice-sub", team_scope="team-a")
    unknown_id = str(uuid.uuid4())

    await _post_message(client, token, unknown_id)
    await _post_message(client, token, unknown_id)

    result = await session.execute(
        text("SELECT count(*) FROM conversations WHERE id = :id"),
        {"id": unknown_id},
    )
    count = result.scalar()
    assert count == 1, f"Expected exactly 1 conversation row, got {count}"


async def test_post_message_existing_conv_still_works(client, seeded_two_teams, bridge_jwt):
    """POST /v1/messages for a conversation that already exists must still return 201 (no duplicate)."""
    token = bridge_jwt(sub="alice-sub", team_scope="team-a")

    # First, explicitly create the conversation (LibreChat bridge pattern)
    r_conv = await client.post(
        "/v1/conversations",
        headers={"Authorization": f"Bearer {token}", "X-Team-Scope": "team-a"},
        json={"title": "pre-existing conv", "source": "librechat:claude-3-5-sonnet"},
    )
    assert r_conv.status_code == 201, r_conv.text
    conv_id = r_conv.json()["id"]

    # Now POST a message to that existing conversation
    r_msg = await _post_message(client, token, conv_id)
    assert r_msg.status_code == 201, r_msg.text
    assert r_msg.json()["conversation_id"] == conv_id


async def test_post_message_existing_conv_no_duplicate_row(client, session, seeded_two_teams, bridge_jwt):
    """Upsert must be no-op when conversation already exists (ON CONFLICT DO NOTHING)."""
    token = bridge_jwt(sub="alice-sub", team_scope="team-a")

    # Create conversation explicitly
    r_conv = await client.post(
        "/v1/conversations",
        headers={"Authorization": f"Bearer {token}", "X-Team-Scope": "team-a"},
        json={"title": "pre-existing for no-dup test", "source": "librechat:claude-3-5-sonnet"},
    )
    assert r_conv.status_code == 201, r_conv.text
    conv_id = r_conv.json()["id"]

    # Send a message
    await _post_message(client, token, conv_id)

    # Must still be exactly one row
    result = await session.execute(
        text("SELECT count(*) FROM conversations WHERE id = :id"),
        {"id": conv_id},
    )
    count = result.scalar()
    assert count == 1, f"Expected exactly 1 conversation row, got {count}"
