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
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.integration
# NOTE: pytest-asyncio is configured `asyncio_mode = "auto"` (pyproject.toml) —
# async test functions below run as asyncio tests without an explicit
# @pytest.mark.asyncio; that marker is intentionally omitted from this
# module-level `pytestmark` because the file also has a plain sync test
# (test_deps_and_auth_github_untouched) that would otherwise trip pytest-
# asyncio's "marked with asyncio but not an async function" warning.


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """`app.services.rate_limit` uses a module-level, process-wide
    MemoryStorage (by design — see its docstring) so every test in this
    process shares ONE budget. Several tests here (lockout, e2e loop) issue
    many register/login calls in a row and would otherwise trip
    LOCAL_AUTH_RATE_LIMIT purely from test-suite cross-talk, not from the
    scenario under test. Reset the bucket before each test so rate-limit
    exhaustion is never an artifact of test ordering."""
    from app.services import rate_limit as rate_limit_module

    rate_limit_module._storage.reset()
    yield


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


# ── 2. login ───────────────────────────────────────────────────────────

async def test_login_success(client):
    reg = await client.post(
        "/v1/auth/local/register",
        json={"email": "logintest@x.io", "password": "correcthorse9"},
    )
    assert reg.status_code == 200, reg.text

    r = await client.post(
        "/v1/auth/local/login",
        json={"email": "logintest@x.io", "password": "correcthorse9"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["xbt_token"].startswith("xbt_")


async def test_no_enumeration_oracle(client):
    """login(absent email) and login(wrong password) must be byte-identical
    (T-18-03-02) — a locked account must ALSO return this same generic 401,
    checked separately in test_lockout_then_recovery."""
    await client.post(
        "/v1/auth/local/register",
        json={"email": "enumtarget@x.io", "password": "correcthorse9"},
    )

    resp_absent = await client.post(
        "/v1/auth/local/login",
        json={"email": "absent-nobody@x.io", "password": "whatever123"},
    )
    resp_wrongpw = await client.post(
        "/v1/auth/local/login",
        json={"email": "enumtarget@x.io", "password": "wrongpassword1"},
    )

    assert resp_absent.status_code == 401
    assert resp_wrongpw.status_code == 401
    assert resp_absent.status_code == resp_wrongpw.status_code
    assert resp_absent.json() == resp_wrongpw.json()


async def test_lockout_then_recovery(client, session):
    """SC#6 — N consecutive failures lock the account; the correct password
    is refused (SAME generic 401, not a distinct 'locked' response) while
    locked; after locked_until passes, the correct password works again and
    failed_attempts resets to 0."""
    from app.config import settings

    reg = await client.post(
        "/v1/auth/local/register",
        json={"email": "lockout@x.io", "password": "correcthorse9"},
    )
    assert reg.status_code == 200, reg.text
    user_id = reg.json()["user"]["id"]

    last_resp = None
    for _ in range(settings.LOCAL_AUTH_MAX_FAILED_ATTEMPTS):
        last_resp = await client.post(
            "/v1/auth/local/login",
            json={"email": "lockout@x.io", "password": "wrongpassword1"},
        )
        assert last_resp.status_code == 401, last_resp.text

    row = (
        await session.execute(
            sa.text(
                "SELECT locked_until, failed_attempts FROM local_credentials "
                "WHERE user_id = :uid"
            ),
            {"uid": user_id},
        )
    ).mappings().first()
    assert row["locked_until"] is not None
    assert row["locked_until"] > datetime.now(tz=timezone.utc)

    # Correct password still refused while locked — SAME generic 401 (never a
    # distinct "locked" status/message — that would be a second oracle).
    still_locked = await client.post(
        "/v1/auth/local/login",
        json={"email": "lockout@x.io", "password": "correcthorse9"},
    )
    assert still_locked.status_code == 401
    assert still_locked.json() == last_resp.json()

    # Force the lock window into the past.
    await session.execute(
        sa.text(
            "UPDATE local_credentials SET locked_until = :past WHERE user_id = :uid"
        ),
        {"past": datetime.now(tz=timezone.utc) - timedelta(minutes=1), "uid": user_id},
    )
    await session.commit()

    recovered = await client.post(
        "/v1/auth/local/login",
        json={"email": "lockout@x.io", "password": "correcthorse9"},
    )
    assert recovered.status_code == 200, recovered.text

    row_after = (
        await session.execute(
            sa.text("SELECT failed_attempts FROM local_credentials WHERE user_id = :uid"),
            {"uid": user_id},
        )
    ).mappings().first()
    assert row_after["failed_attempts"] == 0


# ── 3. SC#1 end-to-end (zero social OAuth) ──────────────────────────────

async def test_clean_boot_no_oauth_e2e(client, monkeypatch):
    """The whole point of the phase: register -> login -> authorized request
    succeeds with GOOGLE_CLIENT_ID / GITHUB_APP_CLIENT_ID / GITHUB_CLIENT_ID
    all blanked (no social OAuth configured). OAUTH_ISSUER_URL/
    OAUTH_RESOURCE_URL are the unrelated MCP-connector AS vars and are left
    alone — SC#1 is about social login, not the connector."""
    from app.config import settings

    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "")
    monkeypatch.setattr(settings, "GITHUB_APP_CLIENT_ID", "")
    monkeypatch.setattr(settings, "GITHUB_CLIENT_ID", "")

    reg = await client.post(
        "/v1/auth/local/register",
        json={"email": "cleanboot@x.io", "password": "correcthorse9"},
    )
    assert reg.status_code == 200, reg.text

    login_r = await client.post(
        "/v1/auth/local/login",
        json={"email": "cleanboot@x.io", "password": "correcthorse9"},
    )
    assert login_r.status_code == 200, login_r.text
    body = login_r.json()

    tasks_r = await client.get(
        "/v1/tasks",
        headers={
            "Authorization": f"Bearer {body['xbt_token']}",
            "X-Team-Scope": reg.json()["team_scope"],
        },
    )
    assert tasks_r.status_code == 200, tasks_r.text


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
