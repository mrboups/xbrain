"""Tests for /v1/me/external-sessions — Phase 9 session-bridge registry.

Coverage:
- GET returns empty list / own sessions / cross-user isolation / 401 unauth
- POST upserts as user principal, as bridge JWT with acting_user_sub
- POST as bridge JWT without acting_user_sub → 403
- POST twice updates the same row (last_seen_at advances, metadata replaced)
- DELETE returns 204 on success, 404 on absent, cannot cross users
"""

import os
import time

import pytest
from authlib.jose import jwt

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# ── Helpers ─────────────────────────────────────────────────────────────────


def _pipeline_jwt_for(sub: str, email: str, ttl: int = 300) -> str:
    """Bridge JWT with iss=openwebui-pipeline + acting_user_*.

    `get_current_principal` resolves this to kind="user" (pipeline impersonation
    pattern from Phase 7), creating/returning the users row keyed by `sub`.
    """
    secret = os.environ["BRIDGE_SHARED_SECRET"]
    now = int(time.time())
    payload = {
        "iss": "openwebui-pipeline",
        "sub": "pipeline-service",
        "scope": "bridge",
        "iat": now,
        "exp": now + ttl,
        "acting_user_sub": sub,
        "acting_user_email": email,
    }
    return jwt.encode({"alg": "HS256"}, payload, secret).decode("ascii")


def _pure_bridge_jwt(sub: str = "session-bridge", *, acting_user_sub: str | None = None,
                     ttl: int = 300) -> str:
    """Bridge JWT that stays kind="bridge" (no iss=openwebui-pipeline).

    Used by apps/session-bridge when it calls POST /v1/me/external-sessions
    after the extension WebSocket register-handshake. The acting_user_sub
    claim names the target user.
    """
    secret = os.environ["BRIDGE_SHARED_SECRET"]
    now = int(time.time())
    payload = {
        "iss": "session-bridge",
        "sub": sub,
        "scope": "bridge",
        "iat": now,
        "exp": now + ttl,
    }
    if acting_user_sub:
        payload["acting_user_sub"] = acting_user_sub
    return jwt.encode({"alg": "HS256"}, payload, secret).decode("ascii")


async def _seed_user(session, sub: str, email: str):
    """Create a users row directly so a later pure-bridge JWT can resolve it."""
    from app.repos.users import get_or_create_user
    u = await get_or_create_user(session, source_user_id=sub, email=email, display_name=None)
    await session.commit()
    return u


# ── Tests ───────────────────────────────────────────────────────────────────


async def test_list_unauthenticated(client):
    r = await client.get("/v1/me/external-sessions")
    # Missing Authorization header → FastAPI Header(...) yields 422 (not 401)
    # since the dep declares it required without default. Acceptable.
    assert r.status_code in (401, 422), r.text


async def test_list_invalid_token(client):
    r = await client.get(
        "/v1/me/external-sessions",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert r.status_code == 401


async def test_list_empty(client):
    token = _pipeline_jwt_for("user-empty-sub", "empty@test.local")
    r = await client.get(
        "/v1/me/external-sessions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == []


async def test_post_upsert_creates_as_user(client):
    token = _pipeline_jwt_for("user-create-sub", "create@test.local")
    r = await client.post(
        "/v1/me/external-sessions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "provider": "claude",
            "extension_id": "chrome-ext-abc123",
            "metadata": {"email_logged": "user@claude.ai", "org_id": "org-xyz"},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "claude"
    assert body["extension_id"] == "chrome-ext-abc123"
    assert body["metadata"] == {"email_logged": "user@claude.ai", "org_id": "org-xyz"}

    # Confirm GET sees it
    r2 = await client.get(
        "/v1/me/external-sessions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 200
    rows = r2.json()
    assert len(rows) == 1
    assert rows[0]["provider"] == "claude"


async def test_post_upsert_updates_existing(client):
    token = _pipeline_jwt_for("user-update-sub", "update@test.local")
    # First insert
    r1 = await client.post(
        "/v1/me/external-sessions",
        headers={"Authorization": f"Bearer {token}"},
        json={"provider": "claude", "extension_id": "ext-1", "metadata": {"v": 1}},
    )
    assert r1.status_code == 200
    first = r1.json()

    # Second upsert — different metadata + extension_id
    r2 = await client.post(
        "/v1/me/external-sessions",
        headers={"Authorization": f"Bearer {token}"},
        json={"provider": "claude", "extension_id": "ext-2", "metadata": {"v": 2}},
    )
    assert r2.status_code == 200
    second = r2.json()

    # Same row id, updated payload
    assert second["id"] == first["id"]
    assert second["extension_id"] == "ext-2"
    assert second["metadata"] == {"v": 2}
    # last_seen_at advanced (or equal — both are valid since now() is monotonic)
    assert second["last_seen_at"] >= first["last_seen_at"]

    # Only one row exists
    r3 = await client.get(
        "/v1/me/external-sessions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert len(r3.json()) == 1


async def test_post_pure_bridge_requires_acting_user_sub(client):
    """Bridge JWT without acting_user_sub claim → 403."""
    token = _pure_bridge_jwt(acting_user_sub=None)
    r = await client.post(
        "/v1/me/external-sessions",
        headers={"Authorization": f"Bearer {token}"},
        json={"provider": "claude", "extension_id": "ext-x"},
    )
    assert r.status_code == 403
    assert "acting_user_sub" in r.text


async def test_post_pure_bridge_with_acting_user_sub_upserts(client, session):
    """The session-bridge → memory-api POST upsert contract (the actual cross-plan
    contract with 09-01)."""
    # Bridge cannot create users; it can only upsert for an existing user.
    # Seed the user first (simulates the user having an xbt_ token already issued).
    await _seed_user(session, sub="bridge-acting-sub", email="acting@test.local")

    bridge_token = _pure_bridge_jwt(acting_user_sub="bridge-acting-sub")
    r = await client.post(
        "/v1/me/external-sessions",
        headers={"Authorization": f"Bearer {bridge_token}"},
        json={
            "provider": "claude",
            "extension_id": "ext-via-bridge",
            "metadata": {"email_logged": "actor@claude.ai", "org_id": "org-abc"},
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["extension_id"] == "ext-via-bridge"

    # The acting user can now see their own row via a user-principal GET
    user_token = _pipeline_jwt_for("bridge-acting-sub", "acting@test.local")
    r2 = await client.get(
        "/v1/me/external-sessions",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert r2.status_code == 200
    rows = r2.json()
    assert len(rows) == 1
    assert rows[0]["metadata"]["email_logged"] == "actor@claude.ai"


async def test_list_rejects_pure_bridge(client):
    """GET requires a user principal — pure bridge JWT must be 403."""
    token = _pure_bridge_jwt(acting_user_sub="anyone")
    r = await client.get(
        "/v1/me/external-sessions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403


async def test_cross_user_isolation(client):
    """User A's sessions are not visible to user B (T-09-04-02)."""
    token_a = _pipeline_jwt_for("user-iso-a", "alice-iso@test.local")
    token_b = _pipeline_jwt_for("user-iso-b", "bob-iso@test.local")

    # Alice creates a row
    r = await client.post(
        "/v1/me/external-sessions",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"provider": "claude", "metadata": {"who": "alice"}},
    )
    assert r.status_code == 200

    # Bob sees zero rows
    rb = await client.get(
        "/v1/me/external-sessions",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert rb.status_code == 200
    assert rb.json() == []


async def test_delete_existing_returns_204(client):
    token = _pipeline_jwt_for("user-del-sub", "del@test.local")
    await client.post(
        "/v1/me/external-sessions",
        headers={"Authorization": f"Bearer {token}"},
        json={"provider": "claude", "metadata": {}},
    )
    r = await client.delete(
        "/v1/me/external-sessions/claude",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 204
    # And the row is gone
    rg = await client.get(
        "/v1/me/external-sessions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert rg.json() == []


async def test_delete_absent_returns_404(client):
    token = _pipeline_jwt_for("user-nodel-sub", "nodel@test.local")
    r = await client.delete(
        "/v1/me/external-sessions/claude",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404


async def test_delete_cross_user_returns_404(client):
    """User A trying to DELETE provider that only exists for user B → 404 (not 200)."""
    token_a = _pipeline_jwt_for("user-cd-a", "cda@test.local")
    token_b = _pipeline_jwt_for("user-cd-b", "cdb@test.local")

    # Bob has a claude session
    await client.post(
        "/v1/me/external-sessions",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"provider": "claude", "metadata": {}},
    )
    # Alice's DELETE on "claude" matches no row of HERS
    r = await client.delete(
        "/v1/me/external-sessions/claude",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r.status_code == 404

    # Bob's row still exists
    rb = await client.get(
        "/v1/me/external-sessions",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert len(rb.json()) == 1
