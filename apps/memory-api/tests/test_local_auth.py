"""Integration tests for POST /v1/auth/local/{register,login} (Phase 18-03,
LAUTH-01/02).

Everything here runs against a REAL Postgres (testcontainers, via the
session-scoped `pg_url` fixture) and the REAL FastAPI app (`client` fixture
only overrides `get_session` — never `get_current_principal` /
`get_team_scope`). A test that mocked the principal would prove nothing
about SC#3 ("indistinguishable principal"); the whole point of this file is
that it doesn't.

Task 1 (this file, initial cut): register — new email, argon2id
persistence, xbt_ authorizes a real team-scoped route, existing-email 409,
concurrent-duplicate-credential 409. Task 2 extends this file with login.
"""

from __future__ import annotations

import subprocess

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.integration
# NOTE: pytest-asyncio is configured `asyncio_mode = "auto"` (pyproject.toml) —
# async test functions below run as asyncio tests without an explicit
# @pytest.mark.asyncio; that marker is intentionally omitted from this
# module-level `pytestmark` because the file also has a plain sync test
# (test_deps_and_auth_github_untouched) that would otherwise trip pytest-
# asyncio's "marked with asyncio but not an async function" warning.


# ── 1. register ───────────────────────────────────────────────────────

async def test_register_new_email(client):
    r = await client.post(
        "/v1/auth/local/register",
        json={"email": "new@x.io", "password": "correcthorse9"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["xbt_token"].startswith("xbt_")
    assert body["user"]["email"] == "new@x.io"
    assert body["team_scope"].startswith("solo-")


async def test_password_hash_is_argon2id(client, session):
    r = await client.post(
        "/v1/auth/local/register",
        json={"email": "hashcheck@x.io", "password": "correcthorse9"},
    )
    assert r.status_code == 200, r.text
    user_id = r.json()["user"]["id"]

    row = (
        await session.execute(
            sa.text("SELECT * FROM local_credentials WHERE user_id = :uid"),
            {"uid": user_id},
        )
    ).mappings().first()
    assert row is not None
    assert row["password_hash"].startswith("$argon2id$")
    for value in row.values():
        assert "correcthorse9" not in str(value)


async def test_local_xbt_authorizes_team_route(client):
    """SC#3/LAUTH-02 — the register-minted xbt_ authorizes a REAL
    team-scoped route via the real get_current_principal -> get_team_scope
    -> team_members path. No dependency_overrides on either dependency."""
    r = await client.post(
        "/v1/auth/local/register",
        json={"email": "authorizes@x.io", "password": "correcthorse9"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    xbt = body["xbt_token"]
    team_scope = body["team_scope"]

    tasks_r = await client.get(
        "/v1/tasks",
        headers={"Authorization": f"Bearer {xbt}", "X-Team-Scope": team_scope},
    )
    assert tasks_r.status_code == 200, tasks_r.text
    assert tasks_r.json() == []


async def test_register_collision_409(client, session):
    from app.repos import users as users_repo

    dup_user = await users_repo.get_or_create_user(
        session, source_user_id="github:someone", email="dup@x.io"
    )
    await session.commit()

    r = await client.post(
        "/v1/auth/local/register",
        json={"email": "dup@x.io", "password": "correcthorse9"},
    )
    assert r.status_code == 409, r.text
    assert "xbt_token" not in r.json()

    count = (
        await session.execute(
            sa.text("SELECT count(*) FROM local_credentials WHERE user_id = :uid"),
            {"uid": dup_user.id},
        )
    ).scalar_one()
    assert count == 0


async def test_register_duplicate_credential_409(client, pg_url):
    """A user + credential already exist under source_user_id="email:<addr>"
    but users.email does NOT match, so the register email-precheck misses.
    The credential INSERT then collides on the user_id PK -> 409, not 500.

    Seeded via an INDEPENDENT engine/connection with a REAL commit — not the
    `session` fixture. `session` is bound to one ambient connection-level
    transaction for the whole test (the fixture wraps it in `conn.begin()` /
    `trans.rollback()` with no savepoint), so a `session.commit()` there is
    only durable relative to that SAME connection. Since this test exercises
    the route's `session.rollback()` path (the concurrent-duplicate branch),
    a same-session seed would be wiped by that very rollback — this is a
    fixture-mechanics artifact, not a claim about production behavior. A
    genuinely independent, already-committed row is required to prove the
    route's rollback does NOT touch data outside its own transaction.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.repos import local_credentials as local_credentials_repo
    from app.repos import users as users_repo
    from app.services.password_hash import hash_password

    seed_engine = create_async_engine(pg_url)
    seed_session_factory = async_sessionmaker(
        seed_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with seed_session_factory() as seed_session:
        racer = await users_repo.get_or_create_user(
            seed_session, source_user_id="email:race@x.io", email="other@x.io"
        )
        racer_id = racer.id
        created = await local_credentials_repo.create(
            seed_session, user_id=racer.id, password_hash=hash_password("existingpw1")
        )
        assert created is True
        await seed_session.commit()
    await seed_engine.dispose()

    r = await client.post(
        "/v1/auth/local/register",
        json={"email": "race@x.io", "password": "correcthorse9"},
    )
    assert r.status_code == 409, r.text
    assert r.status_code != 500

    verify_engine = create_async_engine(pg_url)
    async with verify_engine.connect() as vconn:
        count = (
            await vconn.execute(
                sa.text("SELECT count(*) FROM local_credentials WHERE user_id = :uid"),
                {"uid": racer_id},
            )
        ).scalar_one()
    await verify_engine.dispose()
    assert count == 1


async def test_register_short_password_422(client, session):
    r = await client.post(
        "/v1/auth/local/register",
        json={"email": "shortpw@x.io", "password": "short1"},
    )
    assert r.status_code == 422, r.text

    row = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM users WHERE source_user_id = 'email:shortpw@x.io'"
            )
        )
    ).scalar_one()
    assert row == 0


# ── 4. SC#4 safety — untouched files ────────────────────────────────────

def test_deps_and_auth_github_untouched():
    """This plan must not add a sixth branch to deps.py or edit
    auth_github.py (LAUTH-02 'no sixth branch'; SC#4 safety)."""
    import os

    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )
    changed = set(result.stdout.splitlines())
    assert not any(f.endswith("app/deps.py") for f in changed), changed
    assert not any(f.endswith("app/routes/auth_github.py") for f in changed), changed
