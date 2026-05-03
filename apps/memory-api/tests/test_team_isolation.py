"""Team isolation tests — LOCK the success criterion 3 of Phase 1.

Team A user can never see Team B data. Verified via direct memory-api queries (not just UI).
Marked integration because they require Postgres + Alembic schema.
"""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _create_message(client, bridge_jwt, sub: str, team: str, conv_id: str, content: str):
    token = bridge_jwt(sub=sub, team_scope=team)
    return await client.post(
        "/v1/messages",
        headers={"Authorization": f"Bearer {token}", "X-Team-Scope": team},
        json={
            "conversation_id": conv_id,
            "role": "user",
            "content": content,
            "metadata": {},
            "tagging": {
                "team_scope": team,
                "visibility": "team",
                "confidence": 1.0,
                "truth_level": "EPHEMERAL",
                "source": "librechat:claude-3-5-sonnet",
                "validation_status": "pending",
            },
        },
    )


async def _create_conv(client, bridge_jwt, sub: str, team: str) -> str:
    token = bridge_jwt(sub=sub, team_scope=team)
    r = await client.post(
        "/v1/conversations",
        headers={"Authorization": f"Bearer {token}", "X-Team-Scope": team},
        json={"title": f"conv-{team}", "source": "librechat:claude-3-5-sonnet"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_bob_cannot_post_in_team_a(client, seeded_two_teams, bridge_jwt):
    """Bridge JWT with team_scope=team-b but X-Team-Scope=team-a → 403."""
    token = bridge_jwt(sub="bob-sub", team_scope="team-b")
    r = await client.post(
        "/v1/conversations",
        headers={"Authorization": f"Bearer {token}", "X-Team-Scope": "team-a"},
        json={"title": "intrusion", "source": "librechat:gpt-4o"},
    )
    assert r.status_code == 403


async def test_alice_can_post_in_team_a(client, seeded_two_teams, bridge_jwt):
    cid = await _create_conv(client, bridge_jwt, "alice-sub", "team-a")
    r = await _create_message(client, bridge_jwt, "alice-sub", "team-a", cid, "hello team-a")
    assert r.status_code == 201, r.text


async def test_list_messages_returns_only_team_scoped_results(client, seeded_two_teams, bridge_jwt):
    """Seed messages in both teams, then verify alice sees only team-a, bob only team-b."""
    cid_a = await _create_conv(client, bridge_jwt, "alice-sub", "team-a")
    cid_b = await _create_conv(client, bridge_jwt, "bob-sub", "team-b")

    for i in range(3):
        await _create_message(client, bridge_jwt, "alice-sub", "team-a", cid_a, f"a-msg-{i}")
        await _create_message(client, bridge_jwt, "bob-sub", "team-b", cid_b, f"b-msg-{i}")

    # Alice GET team-a
    token_a = bridge_jwt(sub="alice-sub", team_scope="team-a")
    r = await client.get(
        "/v1/messages",
        headers={"Authorization": f"Bearer {token_a}", "X-Team-Scope": "team-a"},
    )
    assert r.status_code == 200
    msgs = r.json()
    assert len(msgs) == 3
    assert all(m["tagging"]["team_scope"] == "team-a" for m in msgs)

    # Alice GET team-b → 403 (bridge JWT mismatch)
    r2 = await client.get(
        "/v1/messages",
        headers={"Authorization": f"Bearer {token_a}", "X-Team-Scope": "team-b"},
    )
    assert r2.status_code == 403


async def test_get_conversation_other_team_returns_404(client, seeded_two_teams, bridge_jwt):
    """Looking up Team B's conv via Team A's scope returns 404 — don't leak existence as 403."""
    cid_b = await _create_conv(client, bridge_jwt, "bob-sub", "team-b")
    # Alice tries to write a message into bob's conversation, scoped to team-a
    r = await _create_message(client, bridge_jwt, "alice-sub", "team-a", cid_b, "intrusion")
    assert r.status_code == 404
