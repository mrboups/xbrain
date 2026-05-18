"""Tests for /v1/admin/wipe-team and /v1/admin/wipe-database (superadmin wipe).

Marked @pytest.mark.integration because they need a real PG (alembic schema).
External subsystems (Qdrant, Neo4j, Mongo, MinIO) are not running in test —
the route is intentionally tolerant of "subsystem unreachable" responses, so
tests verify the PG side strictly and accept "error"/"skipped" status
fields on the external side.
"""
from __future__ import annotations

import os
import time

import pytest
import sqlalchemy as sa
from authlib.jose import jwt

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# --- Helpers --------------------------------------------------------------


def _admin_bridge_jwt(secret: str | None = None) -> str:
    """Bridge JWT — treated as admin by _is_admin() (kind=bridge)."""
    secret = secret or os.environ["BRIDGE_SHARED_SECRET"]
    now = int(time.time())
    payload = {
        "iss": "test-bridge",
        "sub": "test-admin",
        "team_scope": "default",
        "scope": "bridge",
        "iat": now,
        "exp": now + 300,
    }
    return jwt.encode({"alg": "HS256"}, payload, secret).decode("ascii")


def _non_admin_bridge_jwt(secret: str | None = None) -> str:
    """A non-bridge user JWT is needed to test 403 — bridge is always admin.
    We craft an OIDC-like sub that is NOT in ADMIN_USER_SUBS. To do this, we
    must hit the endpoint with a sub that's not admin. The easiest path is
    monkey-patching ADMIN_USER_SUBS to empty so the bridge override is the
    only way in — but tests below take a different approach: they create a
    user via the user_api_tokens path with a non-admin sub. Falling back to
    'wrong bearer' which returns 401 not 403."""
    raise NotImplementedError  # not used — see tests below


async def _ensure_team(session, slug: str, display: str = "Test team") -> str:
    """Create a team row and return its slug. Idempotent."""
    await session.execute(
        sa.text(
            """
            INSERT INTO teams (id, slug, display_name)
            VALUES (gen_random_uuid(), :slug, :display)
            ON CONFLICT (slug) DO NOTHING
            """
        ),
        {"slug": slug, "display": display},
    )
    await session.commit()
    return slug


async def _ensure_user(session, sub: str, email: str) -> str:
    """Create a user row and return its id (uuid str)."""
    row = (
        await session.execute(
            sa.text(
                """
                INSERT INTO users (id, source_user_id, email)
                VALUES (gen_random_uuid(), :sub, :email)
                ON CONFLICT (source_user_id) DO UPDATE SET email = EXCLUDED.email
                RETURNING id
                """
            ),
            {"sub": sub, "email": email},
        )
    ).fetchone()
    await session.commit()
    return str(row.id)


async def _seed_team_data(session, team_slug: str) -> None:
    """Insert a memory_item + a tag-like row scoped to the team."""
    await session.execute(
        sa.text(
            """
            INSERT INTO memory_items
                (team_scope, content, source, truth_level, validation_status,
                 visibility, confidence, metadata)
            VALUES
                (:slug, 'fact-for-test', 'test', 'WORKING', 'pending',
                 'team', 0.9, '{}'::jsonb)
            """
        ),
        {"slug": team_slug},
    )
    await session.commit()


# --- /v1/admin/wipe-team tests --------------------------------------------


async def test_wipe_team_requires_auth(client):
    """No Authorization header → 401."""
    r = await client.post(
        "/v1/admin/wipe-team/some-team",
        json={"confirm": "some-team"},
    )
    assert r.status_code == 401


async def test_wipe_team_requires_superadmin(client, bridge_jwt, session):
    """A real-user token with sub NOT in ADMIN_USER_SUBS → 403.

    We forge a Bridge-shaped JWT but with iss != 'test-bridge' so the
    deps code routes it through the bridge path; bridge is admin. So
    instead we test by creating an xbt_ API token for a non-admin user.
    """
    await _ensure_team(session, "team-403-test")
    user_id = await _ensure_user(session, "non-admin-sub", "noadmin@test.local")
    # Create an xbt_ API token directly in DB (mimics /v1/me/api-token flow).
    import hashlib

    raw_token = "xbt_test_non_admin_403"
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    await session.execute(
        sa.text(
            """
            INSERT INTO user_api_tokens (id, user_id, token_hash, name, team_scope)
            VALUES (gen_random_uuid(), :uid, :hash, 'test', 'team-403-test')
            """
        ),
        {"uid": user_id, "hash": token_hash},
    )
    await session.commit()

    r = await client.post(
        "/v1/admin/wipe-team/team-403-test",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={"confirm": "team-403-test"},
    )
    # Non-admin user → _is_admin() returns False → 403
    assert r.status_code == 403, r.text


async def test_wipe_team_requires_confirm_match(client, session):
    """Bridge JWT is admin, but confirm body doesn't match team_slug → 400."""
    await _ensure_team(session, "team-confirm-test")
    token = _admin_bridge_jwt()
    r = await client.post(
        "/v1/admin/wipe-team/team-confirm-test",
        headers={"Authorization": f"Bearer {token}"},
        json={"confirm": "wrong-slug"},
    )
    assert r.status_code == 400, r.text
    assert "confirm" in r.text.lower()


async def test_wipe_team_404_when_missing(client):
    token = _admin_bridge_jwt()
    r = await client.post(
        "/v1/admin/wipe-team/does-not-exist",
        headers={"Authorization": f"Bearer {token}"},
        json={"confirm": "does-not-exist"},
    )
    assert r.status_code == 404


async def test_wipe_team_actually_deletes(client, session):
    """Create team + memory_item → wipe → team row gone, memory_item gone."""
    slug = "team-wipe-actually"
    await _ensure_team(session, slug)
    await _seed_team_data(session, slug)

    # Confirm seed visible.
    pre = (await session.execute(
        sa.text("SELECT count(*) FROM memory_items WHERE team_scope = :s"),
        {"s": slug},
    )).scalar_one()
    assert pre == 1

    token = _admin_bridge_jwt()
    r = await client.post(
        f"/v1/admin/wipe-team/{slug}",
        headers={"Authorization": f"Bearer {token}"},
        json={"confirm": slug},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["team_slug"] == slug
    assert body["deleted"]["memory_items"] == 1
    assert body["deleted"]["teams"] == 1

    # Direct DB check.
    post_team = (await session.execute(
        sa.text("SELECT count(*) FROM teams WHERE slug = :s"), {"s": slug}
    )).scalar_one()
    assert post_team == 0
    post_items = (await session.execute(
        sa.text("SELECT count(*) FROM memory_items WHERE team_scope = :s"),
        {"s": slug},
    )).scalar_one()
    assert post_items == 0


async def test_wipe_team_preserves_other_teams(client, session):
    """Two teams A + B with data → wipe A → B intact."""
    await _ensure_team(session, "team-keep-a")
    await _ensure_team(session, "team-keep-b")
    await _seed_team_data(session, "team-keep-a")
    await _seed_team_data(session, "team-keep-b")

    token = _admin_bridge_jwt()
    r = await client.post(
        "/v1/admin/wipe-team/team-keep-a",
        headers={"Authorization": f"Bearer {token}"},
        json={"confirm": "team-keep-a"},
    )
    assert r.status_code == 200, r.text

    a_count = (await session.execute(
        sa.text("SELECT count(*) FROM teams WHERE slug = 'team-keep-a'")
    )).scalar_one()
    b_count = (await session.execute(
        sa.text("SELECT count(*) FROM teams WHERE slug = 'team-keep-b'")
    )).scalar_one()
    b_items = (await session.execute(
        sa.text("SELECT count(*) FROM memory_items WHERE team_scope = 'team-keep-b'")
    )).scalar_one()
    assert a_count == 0
    assert b_count == 1
    assert b_items == 1


# --- /v1/admin/wipe-database tests ----------------------------------------


async def test_wipe_database_requires_auth(client):
    r = await client.post("/v1/admin/wipe-database", json={"confirm": "WIPE EVERYTHING"})
    assert r.status_code == 401


async def test_wipe_database_requires_superadmin(client, session):
    """Non-admin xbt_ token → 403."""
    import hashlib

    await _ensure_team(session, "team-403-wipe-db")
    user_id = await _ensure_user(session, "non-admin-sub-2", "no2@test.local")
    raw_token = "xbt_test_non_admin_wipedb_403"
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    await session.execute(
        sa.text(
            """
            INSERT INTO user_api_tokens (id, user_id, token_hash, name, team_scope)
            VALUES (gen_random_uuid(), :uid, :hash, 'test', 'team-403-wipe-db')
            """
        ),
        {"uid": user_id, "hash": token_hash},
    )
    await session.commit()

    r = await client.post(
        "/v1/admin/wipe-database",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={"confirm": "WIPE EVERYTHING"},
    )
    assert r.status_code == 403, r.text


async def test_wipe_database_requires_exact_confirm_string(client):
    token = _admin_bridge_jwt()
    for bad in ["wipe everything", "WIPE", "WIPE EVERYTHING ", "yes", "WIPE everything"]:
        r = await client.post(
            "/v1/admin/wipe-database",
            headers={"Authorization": f"Bearer {token}"},
            json={"confirm": bad},
        )
        assert r.status_code == 400, f"expected 400 for confirm={bad!r}, got {r.status_code}"


async def test_wipe_database_truncates_and_reseeds_agents(client, session):
    """Seed users/teams/items → wipe → all gone, agent_definitions re-seeded."""
    await _ensure_team(session, "team-fullwipe-1")
    await _ensure_team(session, "team-fullwipe-2")
    await _ensure_user(session, "user-fullwipe", "fw@test.local")
    await _seed_team_data(session, "team-fullwipe-1")

    token = _admin_bridge_jwt()
    r = await client.post(
        "/v1/admin/wipe-database",
        headers={"Authorization": f"Bearer {token}"},
        json={"confirm": "WIPE EVERYTHING"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body["truncated_tables"], list)
    assert "teams" in body["truncated_tables"]
    assert "users" in body["truncated_tables"]
    assert "memory_items" in body["truncated_tables"]
    assert body["agent_definitions"]["status"] in ("ok", "skipped")

    # Direct DB checks.
    teams_n = (await session.execute(sa.text("SELECT count(*) FROM teams"))).scalar_one()
    users_n = (await session.execute(sa.text("SELECT count(*) FROM users"))).scalar_one()
    items_n = (await session.execute(sa.text("SELECT count(*) FROM memory_items"))).scalar_one()
    assert teams_n == 0
    assert users_n == 0
    assert items_n == 0

    # meeting-recap agent re-seeded.
    agent_n = (await session.execute(
        sa.text("SELECT count(*) FROM agent_definitions WHERE name = 'meeting-recap'")
    )).scalar_one()
    assert agent_n == 1


async def test_wipe_database_audit_log_first_row_is_wipe(client, session):
    """After a full wipe, audit_log holds exactly the wipe action."""
    await _ensure_team(session, "team-audit-test")
    await _seed_team_data(session, "team-audit-test")

    # Pollute audit_log so we can prove the wipe really truncated it.
    await session.execute(
        sa.text(
            "INSERT INTO audit_log (action, payload) VALUES ('seed-noise', '{}'::jsonb)"
        )
    )
    await session.commit()
    pre = (await session.execute(sa.text("SELECT count(*) FROM audit_log"))).scalar_one()
    assert pre >= 1

    token = _admin_bridge_jwt()
    r = await client.post(
        "/v1/admin/wipe-database",
        headers={"Authorization": f"Bearer {token}"},
        json={"confirm": "WIPE EVERYTHING"},
    )
    assert r.status_code == 200, r.text

    rows = (await session.execute(
        sa.text("SELECT action FROM audit_log ORDER BY ts ASC")
    )).fetchall()
    assert len(rows) == 1
    assert rows[0].action == "superadmin_wipe_database"
