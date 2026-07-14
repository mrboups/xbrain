"""Integration tests for POST /v1/auth/local/set-password (Phase 18-04,
LAUTH-01, D-18-05).

Everything here runs against a REAL Postgres (testcontainers, via the
session-scoped `pg_url` fixture) and the REAL FastAPI app (`client` fixture
only overrides `get_session` — never `get_current_principal` /
`get_team_scope`). set-password is THE convergence mechanism (D-18-05): an
already-authenticated caller (proven by their own session, never by data in
the request body) attaches or changes a password on their OWN row. These
tests prove:

  1. change (existing local_credentials row) requires the correct
     old_password, and the new password logs in while the old one 401s;
  2. a wrong/missing old_password is rejected (400) and the stored hash is
     byte-for-byte unchanged;
  3. convergence — a GitHub-style user (no local_credentials row yet),
     authenticated as kind="user_api_token" via a real xbt_ token, attaches
     a password and can then log in locally with it;
  4. horizontal-privilege-escalation is blocked — principal A's own session
     can only ever touch principal A's row, even if the request body tries
     to smuggle a different target;
  5. an unauthenticated call is rejected (401).
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Same cross-talk guard as test_local_auth.py — several tests here issue
    multiple register/login calls in a row and would otherwise trip
    LOCAL_AUTH_RATE_LIMIT purely from test-suite ordering."""
    from app.services import rate_limit as rate_limit_module

    rate_limit_module._storage.reset()
    yield


# ── 1. change — correct old_password ────────────────────────────────────

async def test_change_password_with_correct_old_password(client):
    reg = await client.post(
        "/v1/auth/local/register",
        json={"email": "changer@x.io", "password": "correcthorse9"},
    )
    assert reg.status_code == 200, reg.text
    xbt = reg.json()["xbt_token"]

    r = await client.post(
        "/v1/auth/local/set-password",
        json={"old_password": "correcthorse9", "new_password": "newhorse99"},
        headers={"Authorization": f"Bearer {xbt}"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "ok"}

    new_login = await client.post(
        "/v1/auth/local/login",
        json={"email": "changer@x.io", "password": "newhorse99"},
    )
    assert new_login.status_code == 200, new_login.text
    assert new_login.json()["xbt_token"].startswith("xbt_")

    old_login = await client.post(
        "/v1/auth/local/login",
        json={"email": "changer@x.io", "password": "correcthorse9"},
    )
    assert old_login.status_code == 401, old_login.text


# ── 2. change — wrong/missing old_password rejected, hash unchanged ──────

async def test_change_password_wrong_old_password_rejected(client, session):
    reg = await client.post(
        "/v1/auth/local/register",
        json={"email": "wrongold@x.io", "password": "correcthorse9"},
    )
    assert reg.status_code == 200, reg.text
    xbt = reg.json()["xbt_token"]
    user_id = reg.json()["user"]["id"]

    hash_before = (
        await session.execute(
            sa.text("SELECT password_hash FROM local_credentials WHERE user_id = :uid"),
            {"uid": user_id},
        )
    ).scalar_one()

    r = await client.post(
        "/v1/auth/local/set-password",
        json={"old_password": "totallywrongpw", "new_password": "newhorse99"},
        headers={"Authorization": f"Bearer {xbt}"},
    )
    assert r.status_code == 400, r.text

    hash_after = (
        await session.execute(
            sa.text("SELECT password_hash FROM local_credentials WHERE user_id = :uid"),
            {"uid": user_id},
        )
    ).scalar_one()
    assert hash_after == hash_before


async def test_change_password_missing_old_password_rejected(client, session):
    reg = await client.post(
        "/v1/auth/local/register",
        json={"email": "missingold@x.io", "password": "correcthorse9"},
    )
    assert reg.status_code == 200, reg.text
    xbt = reg.json()["xbt_token"]
    user_id = reg.json()["user"]["id"]

    hash_before = (
        await session.execute(
            sa.text("SELECT password_hash FROM local_credentials WHERE user_id = :uid"),
            {"uid": user_id},
        )
    ).scalar_one()

    # old_password omitted entirely — must be rejected exactly like a wrong one.
    r = await client.post(
        "/v1/auth/local/set-password",
        json={"new_password": "newhorse99"},
        headers={"Authorization": f"Bearer {xbt}"},
    )
    assert r.status_code == 400, r.text

    hash_after = (
        await session.execute(
            sa.text("SELECT password_hash FROM local_credentials WHERE user_id = :uid"),
            {"uid": user_id},
        )
    ).scalar_one()
    assert hash_after == hash_before

    # Original password must still work — nothing was silently changed.
    still_works = await client.post(
        "/v1/auth/local/login",
        json={"email": "missingold@x.io", "password": "correcthorse9"},
    )
    assert still_works.status_code == 200, still_works.text


# ── 3. convergence — GitHub-style user attaches a password ───────────────

async def test_convergence_github_style_user_attaches_password(client, session):
    """The single most important test in this file (D-18-05). A user who
    signed in via GitHub (no local_credentials row, kind="user_api_token"
    resolved from a real xbt_ — the same principal shape a GitHub sign-in
    would mint) attaches a password with NO old_password, then proves a
    subsequent POST /v1/auth/local/login with that email+password succeeds:
    one converged account, reached through proof-of-ownership, never
    through cold register."""
    from app.repos import teams as teams_repo
    from app.repos import users as users_repo
    from app.services.api_tokens import mint_xbt_for_user

    gh_user = await users_repo.get_or_create_user(
        session,
        source_user_id="github:conv",
        email="conv@x.io",
        display_name="Convergence User",
    )
    await teams_repo.create_team(
        session,
        slug="conv-team",
        display_name="Convergence Team",
        creator_user_id=gh_user.id,
    )
    xbt = await mint_xbt_for_user(session, gh_user.id, name="github-signin-sim")
    await session.commit()

    # Confirm this authenticates as kind="user_api_token" via the REAL
    # get_current_principal xbt_ branch (deps.py:225-272) — no override.
    me = await client.get("/v1/me", headers={"Authorization": f"Bearer {xbt}"})
    assert me.status_code == 200, me.text
    assert me.json()["kind"] == "user_api_token"

    # No existing local_credentials row for this user -> first-attach, no
    # old_password required.
    row_before = await session.execute(
        sa.text("SELECT count(*) FROM local_credentials WHERE user_id = :uid"),
        {"uid": gh_user.id},
    )
    assert row_before.scalar_one() == 0

    r = await client.post(
        "/v1/auth/local/set-password",
        json={"new_password": "convergedpw1"},
        headers={"Authorization": f"Bearer {xbt}"},
    )
    assert r.status_code == 200, r.text

    row = (
        await session.execute(
            sa.text("SELECT password_hash FROM local_credentials WHERE user_id = :uid"),
            {"uid": gh_user.id},
        )
    ).mappings().first()
    assert row is not None
    assert row["password_hash"].startswith("$argon2id$")

    login_r = await client.post(
        "/v1/auth/local/login",
        json={"email": "conv@x.io", "password": "convergedpw1"},
    )
    assert login_r.status_code == 200, login_r.text
    assert login_r.json()["xbt_token"].startswith("xbt_")
    assert login_r.json()["user"]["id"] == str(gh_user.id)


# ── 4. horizontal-privilege-escalation blocked ────────────────────────────

async def test_cannot_set_another_users_password(client, session):
    """Principal A's authenticated session must only ever be able to touch
    A's own local_credentials row — never B's, even if the request body
    tries to smuggle B's identity. Identity comes from the resolved
    principal (deps.py), not from anything in SetPasswordBody."""
    reg_a = await client.post(
        "/v1/auth/local/register",
        json={"email": "usera@x.io", "password": "correcthorse9"},
    )
    assert reg_a.status_code == 200, reg_a.text
    xbt_a = reg_a.json()["xbt_token"]

    reg_b = await client.post(
        "/v1/auth/local/register",
        json={"email": "userb@x.io", "password": "correcthorse9"},
    )
    assert reg_b.status_code == 200, reg_b.text
    user_b_id = reg_b.json()["user"]["id"]

    hash_b_before = (
        await session.execute(
            sa.text("SELECT password_hash FROM local_credentials WHERE user_id = :uid"),
            {"uid": user_b_id},
        )
    ).scalar_one()

    # A's authenticated call, with B's id/email smuggled into the body.
    # SetPasswordBody has no user_id/email field at all, so this can only
    # ever act on A's own row (the extra keys are simply ignored).
    r = await client.post(
        "/v1/auth/local/set-password",
        json={
            "old_password": "correcthorse9",
            "new_password": "atakeoverpw1",
            "user_id": user_b_id,
            "email": "userb@x.io",
        },
        headers={"Authorization": f"Bearer {xbt_a}"},
    )
    assert r.status_code == 200, r.text

    # B's row is untouched — B still logs in with B's ORIGINAL password.
    hash_b_after = (
        await session.execute(
            sa.text("SELECT password_hash FROM local_credentials WHERE user_id = :uid"),
            {"uid": user_b_id},
        )
    ).scalar_one()
    assert hash_b_after == hash_b_before

    b_login = await client.post(
        "/v1/auth/local/login",
        json={"email": "userb@x.io", "password": "correcthorse9"},
    )
    assert b_login.status_code == 200, b_login.text

    # A's own password DID change to the new value (proving the call went
    # somewhere — just never to B).
    a_login_new = await client.post(
        "/v1/auth/local/login",
        json={"email": "usera@x.io", "password": "atakeoverpw1"},
    )
    assert a_login_new.status_code == 200, a_login_new.text


# ── 5. unauthenticated call rejected ──────────────────────────────────────

async def test_set_password_requires_authentication(client):
    r = await client.post(
        "/v1/auth/local/set-password",
        json={"new_password": "somepassword1"},
    )
    assert r.status_code in (401, 422)
    # FastAPI's Header(...) with no default raises 422 when the header is
    # entirely absent (not a 401) — either way, no set-password succeeded
    # unauthenticated. Assert explicitly it did NOT succeed.
    assert r.status_code != 200
