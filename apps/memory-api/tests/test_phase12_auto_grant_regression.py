"""REVISION 2 (B-3 fix) — Phase 12 MUST NOT regress Phase 10's
team_org_blocks + auto-grant semantics under the new install_required branch.

ROADMAP SC-5 (verbatim):
  "team_org_blocks + auto-grant team membership semantics unchanged from
  Phase 10 (regression tests pass): blocked GitHub login still cannot join
  even if org is installed; auto-grant still triggers on first sign-in for
  installed-org members."

The signin route now has a THREE-way conditional fan-out (auto_grant ->
install_check -> install_required_flag). Any reordering would silently break
either:
  - Blocked users gaining unexpected team membership (security regression), OR
  - Unblocked users losing auto-grant (UX regression).

This file pins both directions with respx-mocked GitHub flow and a real DB
session (testcontainers Postgres — auto-skips on Docker-less hosts via the
session fixture chain in conftest.py).

Mocking strategy mirrors test_phase12_org_membership.py:
  - respx intercepts every GitHub HTTPS call (token exchange, /user, /emails,
    /orgs, /app/installations/.../access_tokens, /orgs/{org}/members/{login}).
  - cryptography generates a fresh RSA private key per test for App JWT
    minting — the prod key MUST NEVER load in unit tests.

Pre-conditions established in each test:
  - One Installation row for org='dejavudev' (Phase 12 installations table).
    Pre-inserted so check_github_org_membership() returns MEMBER (not
    INSTALL_REQUIRED), proving the install-is-present branch ran.
  - One Team row with github_org='dejavudev' (matches the user's GitHub orgs
    so auto_grant_via_org_match() finds the team as a candidate).
  - For the blocked test only: one team_org_blocks row keyed on
    (team_id, github_login).
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.models.installation import Installation
from app.models.team import Team
from app.repos import teams as teams_repo
from app.services import (
    github_app_jwt,
    github_installation,
    github_user_token,
    token_crypto,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# === Fixtures ================================================================


@pytest.fixture
def app_pem_b64() -> str:
    """Fresh RSA key per test, returned base64-encoded PEM.

    Mirrors test_phase12_jwt.py / test_phase12_org_membership.py — the real
    prod GitHub App private key is never loaded from env in unit tests.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return base64.b64encode(pem).decode()


@pytest.fixture(autouse=True)
def _configure(monkeypatch, app_pem_b64):
    """Configure GITHUB_APP_* + FERNET_KEY + GITHUB_ORG + reset module caches.

    The signin route reads settings.GITHUB_APP_CLIENT_ID,
    GITHUB_APP_CLIENT_SECRET, GITHUB_APP_SLUG, GITHUB_ORG, FERNET_KEY at
    request time — patch the canonical Settings instance via monkeypatch.setattr
    so every importer sees the same values.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "GITHUB_APP_CLIENT_ID", "Iv23li_test")
    monkeypatch.setattr(settings, "GITHUB_APP_CLIENT_SECRET", "test_secret")
    monkeypatch.setattr(settings, "GITHUB_APP_PRIVATE_KEY_B64", app_pem_b64)
    monkeypatch.setattr(settings, "GITHUB_APP_SLUG", "xbrain")
    monkeypatch.setattr(settings, "GITHUB_ORG", "dejavudev")
    monkeypatch.setattr(settings, "FERNET_KEY", Fernet.generate_key().decode())

    # github_app_jwt._reset_private_key_cache_for_tests caches the parsed PEM
    # — must clear between tests because each test generates a NEW key.
    github_app_jwt._reset_private_key_cache_for_tests()
    # github_installation._reset_caches_for_tests clears the installation_id
    # token cache AND per-installation locks (Pitfall 6 fix).
    github_installation._reset_caches_for_tests()
    # token_crypto caches the Fernet instance (lru_cache) — must clear because
    # each test generates a fresh FERNET_KEY.
    token_crypto._reset_for_tests()
    # github_user_token caches per-user asyncio.Lock — clear so the dict
    # doesn't accumulate uuid keys across tests.
    github_user_token._reset_locks_for_tests()
    # app.auth caches /orgs/{org}/members/{user} results 5min keyed by
    # (token[:16], org). Reset so cached MEMBER doesn't leak into the next
    # test with a different mocked response.
    from app import auth as auth_module
    auth_module._github_membership_cache.clear()

    yield

    github_installation._reset_caches_for_tests()
    github_app_jwt._reset_private_key_cache_for_tests()
    github_user_token._reset_locks_for_tests()
    token_crypto._reset_for_tests()
    auth_module._github_membership_cache.clear()


# === Helpers =================================================================


def _expires_at_iso(hours: int = 1) -> str:
    """ISO-8601 UTC timestamp `hours` from now — for ghs_ installation token."""
    return (datetime.now(UTC) + timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ",
    )


def _mock_signin_full_flow_with_install(
    mock: respx.MockRouter,
    *,
    login: str,
    gh_id: int,
    org: str,
    installation_id: int,
    member: bool,
) -> None:
    """Stub the complete GitHub-side flow against an INSTALLED org.

    Step-by-step contract matches routes/auth_github.signin_github body:
      1. POST /login/oauth/access_token              — code -> ghu_/ghr_ bundle.
      2. GET  /user                                  — login, github_id, name.
      3. GET  /user/emails                           — primary verified email.
      4. GET  /user/orgs?per_page=100&page=1         — user's org memberships.
      5. POST /app/installations/{id}/access_tokens  — mint ghs_ for membership check.
      6. GET  /user                                  — installation-check helper
           also re-fetches /user (auth.py:215). Same mock satisfies both.
      7. GET  /orgs/{org}/members/{login}            — 204=member, 404=not.

    The Installation row MUST exist in the DB before the request so
    find_installation_for_org() returns it without an on-demand fallback to
    GET /orgs/{org}/installation (which would also need mocking).
    """
    # Step 1 — token exchange returns the Phase 12 bundle shape.
    mock.post("https://github.com/login/oauth/access_token").mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "ghu_test_access",
                "refresh_token": "ghr_test_refresh",
                "expires_in": 28800,
                "refresh_token_expires_in": 15897600,
                "token_type": "bearer",
                "scope": "",
            },
        ),
    )
    # Steps 2 + 6 — /user is fetched twice (profile-fetch + install-check
    # helper both call it). A single respx mock answers both calls.
    mock.get("https://api.github.com/user").mock(
        return_value=httpx.Response(
            200,
            json={"id": gh_id, "login": login, "name": login, "email": None},
        ),
    )
    # Step 3 — primary verified email.
    mock.get("https://api.github.com/user/emails").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "email": f"{login}@real.com",
                    "primary": True,
                    "verified": True,
                    "visibility": "private",
                },
            ],
        ),
    )
    # Step 4 — user's org memberships (single page).
    mock.get(url__regex=r"https://api\.github\.com/user/orgs.*").mock(
        return_value=httpx.Response(
            200, json=[{"login": org, "id": 1}],
        ),
    )
    # Step 5 — install-check mints an installation token for the App on this org.
    mock.post(
        f"https://api.github.com/app/installations/{installation_id}/access_tokens",
    ).mock(
        return_value=httpx.Response(
            201,
            json={"token": "ghs_inst_test", "expires_at": _expires_at_iso()},
        ),
    )
    # Step 7 — membership check. 204 = MEMBER, 404 = NOT_MEMBER.
    mock.get(f"https://api.github.com/orgs/{org}/members/{login}").mock(
        return_value=httpx.Response(204 if member else 404),
    )


# === SC-5.1 — Blocked login on installed org NEVER auto-grants ===============


@respx.mock
async def test_auto_grant_blocked_login_returns_xbt_but_zero_teams_joined(
    client, session,
):
    """SC-5.1 regression — team_org_blocks honored even when install_required=False.

    Precondition:
      - Installation row exists for org=dejavudev (App is INSTALLED).
      - Team row with github_org=dejavudev exists.
      - team_org_blocks row pre-blocks github_login='bandit_dev' on this team.

    User signs in:
      - User is a MEMBER of the org per /orgs/{org}/members/{login} (204).
      - install_required MUST be False in the response (org IS installed).
      - teams_joined MUST be empty: the pre-block on github_login takes
        precedence over the org-match auto-grant (Phase 10 GHA-04 semantic).
      - xbt_token MUST still be issued (user sign-in not denied; the block
        only suppresses the auto-grant).
    """
    # ---- Setup: Installation row (App installed on dejavudev) ----
    session.add(
        Installation(
            installation_id=555,
            github_org_login="dejavudev",
            github_account_type="Organization",
            permissions={"members": "read"},
            raw_payload={},
        ),
    )
    # ---- Setup: Team with github_org match ----
    team = Team(
        slug="dejavu-blocked-test",
        display_name="Dejavu Team (blocked)",
        github_org="dejavudev",
        visibility="closed",
    )
    session.add(team)
    await session.flush()
    # ---- Setup: pre-block 'bandit_dev' on this team (Phase 10 team_org_blocks) ----
    await teams_repo.add_org_block(
        session,
        team_id=team.id,
        github_login="bandit_dev",
        blocked_by=None,  # No admin context in the test — repo accepts NULL.
    )
    await session.commit()

    # ---- Setup: GitHub HTTP mocks (user IS a member of the installed org) ----
    _mock_signin_full_flow_with_install(
        respx,
        login="bandit_dev",
        gh_id=99701,
        org="dejavudev",
        installation_id=555,
        member=True,
    )

    # ---- Action: POST /v1/auth/github/signin ----
    r = await client.post(
        "/v1/auth/github/signin",
        json={
            "code": "test_code",
            "redirect_uri": "https://example.com/account/teams/",
            "state": "csrf_test_state",
        },
    )

    # ---- Assertion: response shape locks SC-5.1 invariant ----
    assert r.status_code == 200, (
        f"SC-5.1 regression: blocked user signin returned {r.status_code} "
        f"— expected 200 (block suppresses auto-grant, NOT signin itself). "
        f"Body: {r.text}"
    )
    body = r.json()
    assert body["user"]["teams_joined"] == [], (
        f"SC-5.1 regression: blocked github_login 'bandit_dev' joined "
        f"{body['user']['teams_joined']} — must be empty. team_org_blocks "
        f"was not honored under Phase 12 signin flow."
    )
    # install_required MUST be False because installation_id=555 exists in DB
    # AND the membership check returned 204 (MEMBER, not INSTALL_REQUIRED).
    # If this assertion fails, either the Installation row was not picked up,
    # or the install-status check ran against the wrong org.
    assert body["install_required"] is False, (
        f"SC-5.1 regression: expected install_required=False (App IS "
        f"installed on dejavudev with installation_id=555); got True. "
        f"Likely cause: Installation row not visible to the request session, "
        f"or check_github_org_membership cache leak from a prior test."
    )
    assert body["install_url"] is None
    # xbt_token MUST be issued — block suppresses auto-grant only, not signin.
    assert "xbt_token" in body
    assert body["xbt_token"].startswith("xbt_")


# === SC-5.2 — Unblocked login on installed org DOES auto-grant ===============


@respx.mock
async def test_auto_grant_unblocked_login_joins_org_matched_team(
    client, session,
):
    """SC-5.2 regression — auto-grant still fires for installed-org members.

    Precondition:
      - Installation row exists for org=dejavudev (App is INSTALLED).
      - Team row with github_org=dejavudev exists.
      - NO team_org_blocks entry for github_login='happy_dev'.

    User signs in:
      - User is a MEMBER of the org per /orgs/{org}/members/{login} (204).
      - teams_joined MUST contain the team's slug (auto-grant via org-match
        fired AND was not blocked).
      - install_required MUST be False (org IS installed).
      - xbt_token MUST be issued.
    """
    # ---- Setup: Installation row (App installed on dejavudev) ----
    session.add(
        Installation(
            installation_id=556,
            github_org_login="dejavudev",
            github_account_type="Organization",
            permissions={"members": "read"},
            raw_payload={},
        ),
    )
    # ---- Setup: Team with github_org match — slug isolated from sibling test ----
    team = Team(
        slug="dejavu-allowed-test",
        display_name="Dejavu Team (allowed)",
        github_org="dejavudev",
        visibility="closed",
    )
    session.add(team)
    await session.commit()

    # ---- Setup: GitHub HTTP mocks (user IS a member, no block exists) ----
    _mock_signin_full_flow_with_install(
        respx,
        login="happy_dev",
        gh_id=99801,
        org="dejavudev",
        installation_id=556,
        member=True,
    )

    # ---- Action: POST /v1/auth/github/signin ----
    r = await client.post(
        "/v1/auth/github/signin",
        json={
            "code": "test_code",
            "redirect_uri": "https://example.com/account/teams/",
            "state": "csrf_test_state",
        },
    )

    # ---- Assertion: response shape locks SC-5.2 invariant ----
    assert r.status_code == 200, (
        f"SC-5.2 regression: unblocked user signin returned {r.status_code} "
        f"— expected 200. Body: {r.text}"
    )
    body = r.json()
    assert "dejavu-allowed-test" in body["user"]["teams_joined"], (
        f"SC-5.2 regression: unblocked github_login 'happy_dev' did NOT "
        f"auto-join dejavu-allowed-test; teams_joined="
        f"{body['user']['teams_joined']}. Either auto_grant_via_org_match "
        f"did not run, or the Team row's github_org match failed under "
        f"Phase 12 signin rewrite."
    )
    assert body["install_required"] is False
    assert body["install_url"] is None
    assert "xbt_token" in body
    assert body["xbt_token"].startswith("xbt_")
