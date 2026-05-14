"""Integration tests for POST /v1/auth/github/signin (Phase 10 GHA-01).

Each test builds a respx MockRouter that intercepts the GitHub API calls
(POST /login/oauth/access_token, GET /user, /user/emails, /user/orgs) so the
endpoint runs end-to-end against the FastAPI app + testcontainers Postgres
without hitting GitHub for real. The respx routes are wired with
assert_all_called=False because not every test triggers /user/orgs paging
or the email fetch.
"""

import hashlib
import os

import httpx
import pytest
import respx
import sqlalchemy as sa
from sqlalchemy import select, update

from app.models.team import Team
from app.models.user import User

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _configure_gh_router(
    mock: respx.MockRouter,
    *,
    login: str,
    github_id: int,
    email: str | None,
    orgs: list[str],
) -> None:
    """Register the GitHub API routes on an existing MockRouter."""
    mock.post("https://github.com/login/oauth/access_token").mock(
        return_value=httpx.Response(200, json={"access_token": "gho_test_abc"})
    )
    mock.get("https://api.github.com/user").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": github_id,
                "login": login,
                "name": f"Display {login}",
                "email": None,
            },
        )
    )
    emails_body = []
    if email:
        emails_body = [
            {
                "email": email,
                "primary": True,
                "verified": True,
                "visibility": "private",
            }
        ]
    mock.get("https://api.github.com/user/emails").mock(
        return_value=httpx.Response(200, json=emails_body)
    )
    mock.get(url__regex=r"https://api\.github\.com/user/orgs.*").mock(
        return_value=httpx.Response(
            200, json=[{"login": o, "id": i} for i, o in enumerate(orgs)]
        )
    )


@pytest.fixture(autouse=True)
def _github_oauth_env(monkeypatch):
    """Ensure GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET look configured so the
    503 short-circuit in _exchange_code_for_token does not fire."""
    monkeypatch.setenv("GITHUB_CLIENT_ID", "test_client_id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "test_client_secret")
    from app.config import settings

    monkeypatch.setattr(settings, "GITHUB_CLIENT_ID", "test_client_id")
    monkeypatch.setattr(settings, "GITHUB_CLIENT_SECRET", "test_client_secret")
    yield


async def test_signin_brand_new_user(client, session, seeded_two_teams, monkeypatch):
    """First-time GitHub sign-in with email + matching org → user row created,
    auto-grant fires, xbt_ returned."""
    team_a = seeded_two_teams["team_a"]
    await session.execute(
        update(Team).where(Team.id == team_a.id).values(github_org="acme")
    )
    await session.commit()

    # Disable SMTP so the background notification path is the fail-soft branch.
    from app.config import settings

    monkeypatch.setattr(settings, "SMTP_HOST", "")

    with respx.mock(assert_all_called=False) as mock:
        _configure_gh_router(
            mock, login="newdev", github_id=99001, email="newdev@real.com", orgs=["acme"]
        )
        r = await client.post(
            "/v1/auth/github/signin",
            json={
                "code": "test_code",
                "redirect_uri": "https://test/cb",
                "state": "csrf123",
            },
        )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["xbt_token"].startswith("xbt_")
    assert payload["user"]["github_username"] == "newdev"
    assert payload["user"]["email"] == "newdev@real.com"
    assert "team-a" in payload["user"]["teams_joined"]

    # Token row exists with empty-string team_scope sentinel.
    raw_token = payload["xbt_token"]
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    scope = (await session.execute(sa.text(
        "SELECT team_scope FROM user_api_tokens WHERE token_hash = :h"
    ), {"h": token_hash})).scalar()
    assert scope == ""


async def test_signin_existing_google_user_attaches_github(
    client, session, seeded_two_teams
):
    """Google-linked user with same email → github_id attached, no new row."""
    alice = seeded_two_teams["alice"]
    alice_id_before = alice.id

    with respx.mock(assert_all_called=False) as mock:
        _configure_gh_router(
            mock,
            login="aliceonline",
            github_id=99002,
            email=alice.email,
            orgs=[],
        )
        r = await client.post(
            "/v1/auth/github/signin",
            json={
                "code": "test_code",
                "redirect_uri": "https://test/cb",
                "state": "x",
            },
        )
    assert r.status_code == 200, r.text
    assert r.json()["user"]["id"] == str(alice_id_before)
    # github_id now set on Alice's row.
    refreshed = (
        await session.execute(select(User).where(User.id == alice_id_before))
    ).scalar_one()
    assert refreshed.github_id == 99002
    assert refreshed.github_username == "aliceonline"


async def test_signin_merges_orphan_row(client, session, seeded_two_teams):
    """Orphan github-only row + Google row with same email → merged."""
    from app.repos import users as users_repo

    # Create Google row.
    google_user = await users_repo.get_or_create_user(
        session,
        source_user_id="google:zoe",
        email="zoe@real.com",
        display_name="Zoe",
    )
    # Create orphan github-only row (carries github_id 99003 — important for
    # the B-2 regression test below: this is the unique value that MUST be
    # cleared on the orphan before assignment on the survivor).
    orphan = User(
        source_user_id="github:zoeonline",
        email="zoeonline@users.noreply.github.com",
        display_name="zoeonline",
        github_id=99003,
        github_username="zoeonline",
    )
    session.add(orphan)
    await session.commit()

    with respx.mock(assert_all_called=False) as mock:
        _configure_gh_router(
            mock,
            login="zoeonline",
            github_id=99003,
            email="zoe@real.com",
            orgs=[],
        )
        r = await client.post(
            "/v1/auth/github/signin",
            json={"code": "code", "redirect_uri": "https://test/cb", "state": "x"},
        )
    assert r.status_code == 200, r.text
    # Survivor is the Google row.
    assert r.json()["user"]["id"] == str(google_user.id)
    # Orphan is soft-deleted.
    refreshed_orphan = (
        await session.execute(select(User).where(User.id == orphan.id))
    ).scalar_one()
    assert refreshed_orphan.merged_into_user_id == google_user.id


async def test_merge_does_not_violate_github_id_unique(
    client, session, seeded_two_teams
):
    """REVISION 1 B-2 regression test.

    When the merge path triggers (orphan has github_id X, survivor has no
    github_id, both share verified email), the implementation MUST clear
    orphan.github_id BEFORE assigning github_id on survivor — otherwise the
    UNIQUE constraint on users.github_id raises IntegrityError at flush time
    and the entire transaction rolls back (no merge, no xbt_ minted, 500
    returned to client).

    This test sets up the exact precondition (orphan carries github_id) and
    asserts the endpoint returns 200 + survivor row owns github_id post-merge.
    """
    from app.repos import users as users_repo

    google_user = await users_repo.get_or_create_user(
        session,
        source_user_id="google:bea",
        email="bea@real.com",
        display_name="Bea",
    )
    # Orphan with pre-existing github_id (created via prior GitHub-only flow).
    orphan = User(
        source_user_id="github:beaonline",
        email="beaonline@users.noreply.github.com",
        display_name="beaonline",
        github_id=99099,
        github_username="beaonline",
    )
    session.add(orphan)
    await session.commit()

    with respx.mock(assert_all_called=False) as mock:
        _configure_gh_router(
            mock,
            login="beaonline",
            github_id=99099,
            email="bea@real.com",
            orgs=[],
        )
        r = await client.post(
            "/v1/auth/github/signin",
            json={"code": "c", "redirect_uri": "https://test/cb", "state": "x"},
        )
    # The bug surfaces as 500 (IntegrityError); the fix makes this 200.
    assert r.status_code == 200, (
        f"B-2 regression: signin returned {r.status_code} — likely "
        f"UniqueViolationError on users.github_id. Body: {r.text}"
    )
    assert r.json()["user"]["id"] == str(google_user.id)

    # Verify post-state: survivor owns github_id, orphan does not.
    survivor_refreshed = (
        await session.execute(select(User).where(User.id == google_user.id))
    ).scalar_one()
    orphan_refreshed = (
        await session.execute(select(User).where(User.id == orphan.id))
    ).scalar_one()
    assert survivor_refreshed.github_id == 99099
    assert orphan_refreshed.github_id is None
    assert orphan_refreshed.merged_into_user_id == google_user.id


async def test_signin_respects_org_block(client, session, seeded_two_teams):
    """If team_org_blocks contains (team, login) → auto-grant skips that team."""
    from app.repos import teams as teams_repo

    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]
    await session.execute(
        update(Team).where(Team.id == team_a.id).values(github_org="acme")
    )
    await teams_repo.add_org_block(
        session, team_id=team_a.id, github_login="bandit", blocked_by=alice.id
    )
    await session.commit()

    with respx.mock(assert_all_called=False) as mock:
        _configure_gh_router(
            mock,
            login="bandit",
            github_id=99004,
            email="bandit@real.com",
            orgs=["acme"],
        )
        r = await client.post(
            "/v1/auth/github/signin",
            json={"code": "c", "redirect_uri": "https://test/cb", "state": "x"},
        )
    assert r.status_code == 200, r.text
    assert "team-a" not in r.json()["user"]["teams_joined"]


async def test_signin_existing_blocked_membership_no_regrant(
    client, session, seeded_two_teams
):
    """User with blocked_at set on an existing membership → auto-grant skips."""
    from app.repos import teams as teams_repo
    from app.repos import users as users_repo

    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]
    await session.execute(
        update(Team).where(Team.id == team_a.id).values(github_org="acme")
    )
    target = await users_repo.get_or_create_user(
        session, source_user_id="github:dejavu", email="dejavu@real.com"
    )
    await teams_repo.add_member(
        session, team_id=team_a.id, user_id=target.id, role="member"
    )
    await teams_repo.block_member(
        session, team_id=team_a.id, user_id=target.id, blocked_by=alice.id
    )
    await session.commit()

    with respx.mock(assert_all_called=False) as mock:
        _configure_gh_router(
            mock,
            login="dejavu",
            github_id=99005,
            email="dejavu@real.com",
            orgs=["acme"],
        )
        r = await client.post(
            "/v1/auth/github/signin",
            json={"code": "c", "redirect_uri": "https://test/cb", "state": "x"},
        )
    assert r.status_code == 200, r.text
    # Not in newly_joined teams.
    assert "team-a" not in r.json()["user"]["teams_joined"]


async def test_orphan_token_lands_on_survivor(client, session, seeded_two_teams):
    """REVISION 1 M-3 — SC-7(h) end-to-end follow-pointer test at the HTTP layer.

    Scenario:
      1. Create an orphan user row (source_user_id="github:johndoe", own
         email + display_name, no Google linkage yet).
      2. Mint an xbt_ token directly for the orphan (mirrors what the legacy
         "GitHub-only" sign-in path would have produced before Phase 10's
         auto-merge was in place).
      3. Create a survivor user row (Google identity, real verified email).
      4. Trigger merge via merge_user_rows(orphan, survivor) — this MUST
         re-point user_api_tokens.user_id to the survivor (Plan 10-01
         M-3 fix) AND deps.py xbt_ branch MUST follow merge_into_user_id
         when resolving the principal (Plan 10-02 Task 5).
      5. Use the ORIGINAL orphan xbt_ token to call GET /v1/me.
      6. Assert: response id == survivor.id (NOT orphan.id) and
         response email == survivor.email — proves the auth path resolved
         to the survivor identity end-to-end.

    If this test fails with the orphan's identity in the response, the
    follow-merge-pointer logic in deps.py is missing or the
    user_api_tokens FK re-parent in merge_user_rows did not run.
    """
    import hashlib
    import secrets

    from app.repos import users as users_repo
    from app.repos.merge import merge_user_rows

    # 1. Create the orphan with GitHub identity.
    orphan = await users_repo.get_or_create_user(
        session,
        source_user_id="github:johndoe",
        email="johndoe@users.noreply.github.com",
        display_name="John Doe (orphan)",
    )
    # Add github metadata to the orphan to mirror a real pre-merge state.
    orphan.github_username = "johndoe"
    orphan.github_id = 88001
    await session.flush()

    # 2. Mint xbt_ token for the orphan (same shape as 10-02 _mint_xbt_for_user).
    raw_token = "xbt_" + secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    await session.execute(sa.text("""
        INSERT INTO user_api_tokens (id, user_id, token_hash, team_scope, name, created_at)
        VALUES (gen_random_uuid(), :user_id, :hash, '', 'pre-merge', now())
    """), {"user_id": orphan.id, "hash": token_hash})

    # 3. Create the survivor row (Google identity, will adopt github after merge).
    survivor = await users_repo.get_or_create_user(
        session,
        source_user_id="google:johndoe-google",
        email="john.doe@real.com",
        display_name="John Doe",
    )

    # Clear orphan github fields BEFORE merge so the survivor can take ownership
    # without violating the github_id unique constraint (mirrors the B-2 fix
    # in _resolve_or_merge_user — see test_merge_does_not_violate_github_id_unique).
    orphan.github_id = None
    orphan.github_username = None
    await session.flush()
    survivor.github_id = 88001
    survivor.github_username = "johndoe"
    await session.flush()

    # 4. Trigger merge. merge_user_rows MUST re-point user_api_tokens.user_id
    # to the survivor (the M-3 partner test in test_phase10_repos.py asserts
    # this at SQL level; here we rely on it).
    await merge_user_rows(session, orphan_id=orphan.id, survivor_id=survivor.id)
    await session.commit()

    # 5. Call GET /v1/me with the ORIGINAL orphan token. deps.py xbt_ branch
    # MUST follow merge_into_user_id and resolve to the survivor identity.
    r = await client.get(
        "/v1/me",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert r.status_code == 200, (
        f"M-3 regression: orphan token returned {r.status_code} after merge. "
        f"Either user_api_tokens.user_id was not re-parented (10-01 M-3 fix) "
        f"or deps.py xbt_ branch is not following merge_into_user_id "
        f"(10-02 Task 5). Body: {r.text}"
    )

    # 6. Assert survivor identity is returned, NOT orphan identity.
    body = r.json()
    survivor_id_str = str(survivor.id)
    orphan_id_str = str(orphan.id)
    returned_id = body.get("id") or body.get("user_id")
    returned_email = body.get("email")
    assert returned_id == survivor_id_str, (
        f"M-3 regression: orphan xbt_ resolved to {returned_id} "
        f"(expected survivor {survivor_id_str}, orphan was {orphan_id_str})."
    )
    assert returned_email == survivor.email, (
        f"M-3 regression: orphan xbt_ resolved to email {returned_email} "
        f"(expected survivor {survivor.email})."
    )
