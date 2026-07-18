"""Behavioral coverage of the zero-key connector sign-in leg (Phase 16-01,
PKG-01 / D-16-02).

When no GitHub App is configured (`GITHUB_APP_CLIENT_ID` empty — the zero-
external-key OSS-light install), `GET /oauth/authorize` must NOT 302 into a
`client_id=`-empty GitHub URL (which GitHub rejects). Instead it renders a
local email/password login form, and `POST /oauth/authorize/local` proves the
credential against the SAME Phase-18 argon2id store `auth_local.login` uses,
then converges into the SAME `_finalize_consent` the GitHub leg already uses —
single-team users get a minted PKCE-bound code, multi-team users reach the
reused consent page (stage=post_github, NO code minted yet).

Everything runs against a REAL Postgres (testcontainers, the `client`/`session`
fixtures in conftest) and the REAL FastAPI app — the `client` fixture only
overrides `get_session`, never `get_current_principal` / the oauth store — so
these tests exercise the real credential-proof + team-membership path, not a
mock. That is the whole point (the "gate lesson"): a mocked store cannot fail a
"no code minted" assertion even when the convergence is broken.
"""

from __future__ import annotations

import base64
import hashlib
import re

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.integration

_REDIRECT = "https://claude.ai/api/mcp/auth_callback"
_RESOURCE = "https://mcp.test.example/mcp"
_VERIFIER = "verifier_with_plenty_of_entropy_aaaaaaaaaaaaaa"
_PASSWORD = "correcthorse9"


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """`app.services.rate_limit` uses a process-wide MemoryStorage (by design)
    so every test in this process shares ONE budget. POST /oauth/authorize/local
    is rate-limited (LOCAL_AUTH_RATE_LIMIT) on the same in-process limiter, so
    reset the bucket before each test — otherwise a 429 from cross-test
    cross-talk would masquerade as the scenario under test."""
    from app.services import rate_limit as rate_limit_module

    rate_limit_module._storage.reset()
    yield


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


_CHALLENGE = _s256(_VERIFIER)


def _scrape_hidden_state(html: str) -> str | None:
    m = re.search(r'name="state"\s+value="([^"]+)"', html)
    return m.group(1) if m else None


async def _seed_client(session, redirect_uri: str = _REDIRECT) -> str:
    """Register a public OAuth client with a known registered redirect_uri and
    commit so the route (sharing this session via the override) sees it."""
    from app.auth import oauth_store

    reg = await oauth_store.register_client(
        session, client_name="Test Connector", redirect_uris=[redirect_uri]
    )
    await session.commit()
    return reg["client_id"]


async def _register_local_user(client, email: str, password: str = _PASSWORD) -> dict:
    r = await client.post(
        "/v1/auth/local/register",
        json={"email": email, "password": password},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _authorize_params(client_id: str, redirect_uri: str = _REDIRECT) -> dict:
    return {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": _CHALLENGE,
        "code_challenge_method": "S256",
        "state": "claude-xyz",
        "resource": _RESOURCE,
        "scope": "brain:read brain:write",
    }


async def _get_pre_github_state(client, client_id: str, redirect_uri: str = _REDIRECT) -> str:
    """GET /oauth/authorize with GitHub unconfigured → local form; scrape the
    hidden signed pre_github state token the form carries."""
    r = await client.get(
        "/oauth/authorize",
        params=_authorize_params(client_id, redirect_uri),
        follow_redirects=False,
    )
    assert r.status_code == 200, r.text
    state = _scrape_hidden_state(r.text)
    assert state, "no hidden state field in the local login form"
    return state


# ── Task 1: GET /oauth/authorize branches on GitHub configuration ──────────


async def test_zero_key_get_authorize_renders_local_form(client, session):
    """GITHUB_APP_CLIENT_ID empty → GET /oauth/authorize returns 200 HTML with a
    form action="/oauth/authorize/local" carrying the signed pre_github state —
    NOT a 302 to github.com with an empty client_id."""
    from app.config import settings

    # Default test env leaves GITHUB_APP_CLIENT_ID = "" — assert the zero-key path.
    assert settings.GITHUB_APP_CLIENT_ID == ""
    client_id = await _seed_client(session)

    r = await client.get(
        "/oauth/authorize",
        params=_authorize_params(client_id),
        follow_redirects=False,
    )
    assert r.status_code == 200, r.text
    assert 'action="/oauth/authorize/local"' in r.text
    assert _scrape_hidden_state(r.text), "hidden state field missing"
    assert "github.com/login/oauth/authorize" not in r.text


async def test_malicious_client_name_is_escaped_on_login_form(client, session):
    """CR-01 regression: client_name is attacker-controlled (POST /oauth/register
    is public + unauthenticated per RFC 7591 DCR). This is the CREDENTIAL-ENTRY
    page, so an unescaped interpolation is stored-XSS -> password theft. The
    payload MUST come back HTML-escaped, never as live markup."""
    from app.auth import oauth_store

    payload = '<script>alert("xss")</script>'
    reg = await oauth_store.register_client(
        session, client_name=payload, redirect_uris=[_REDIRECT]
    )
    await session.commit()

    r = await client.get(
        "/oauth/authorize",
        params=_authorize_params(reg["client_id"]),
        follow_redirects=False,
    )
    assert r.status_code == 200, r.text
    # The raw script tag must NOT survive into the response...
    assert "<script>alert(" not in r.text
    # ...and the payload must be present in escaped form (proving it was rendered,
    # not merely dropped — a silently-empty page would also pass the check above).
    assert "&lt;script&gt;" in r.text


async def test_github_configured_get_authorize_still_redirects_to_github(
    client, session, monkeypatch
):
    """Retained-behavior guard: when a GitHub App IS configured the GET path is
    byte-unchanged — 302 into GitHub via memory-api's OWN callback, never the
    Claude.ai redirect_uri (additive branch, not a replacement)."""
    from app.config import settings

    monkeypatch.setattr(settings, "GITHUB_APP_CLIENT_ID", "Iv23test")
    client_id = await _seed_client(session)

    r = await client.get(
        "/oauth/authorize",
        params=_authorize_params(client_id),
        follow_redirects=False,
    )
    assert r.status_code == 302, r.text
    loc = r.headers["location"]
    assert loc.startswith("https://github.com/login/oauth/authorize")
    assert "oauth%2Fgithub-callback" in loc or "/oauth/github-callback" in loc
    assert "claude.ai%2Fapi%2Fmcp" not in loc


# ── Task 2: POST /oauth/authorize/local — credential proof + convergence ────


async def test_valid_single_team_mints_code(client, session):
    """Valid creds for a SINGLE-team local user + a valid pre_github state ->
    302 to the registered Claude.ai redirect_uri with ?code=<minted>, and the
    auth code row is bound to that user_id + that team_scope (converged into
    _finalize_consent, code minted via oauth_store.create_auth_code)."""
    reg = await _register_local_user(client, "single@x.io")
    user_id = reg["user"]["id"]
    team_scope = reg["team_scope"]
    client_id = await _seed_client(session)
    state = await _get_pre_github_state(client, client_id)

    r = await client.post(
        "/oauth/authorize/local",
        data={"email": "single@x.io", "password": _PASSWORD, "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 302, r.text
    loc = r.headers["location"]
    assert loc.startswith(_REDIRECT)
    assert "code=" in loc

    row = (
        await session.execute(
            sa.text(
                "SELECT user_id, team_scope FROM oauth_authorization_codes"
            )
        )
    ).mappings().first()
    assert row is not None, "no authorization code was minted"
    assert str(row["user_id"]) == user_id
    assert row["team_scope"] == team_scope


async def test_valid_multi_team_reaches_consent_page(client, session):
    """Valid creds for a MULTI-team local user (2+ teams) + valid pre_github
    state -> HTTP 200 that IS the reused oauth_consent.html team-selection page,
    carrying a re-signed state with stage=post_github, and NO code minted yet
    (the existing POST /oauth/authorize consent submit finishes it). This is the
    multi-team fork now reachable from the new entry point."""
    from uuid import UUID

    from app.repos import teams as teams_repo
    from app.routes.oauth_authorize import _verify_state

    reg = await _register_local_user(client, "multi@x.io")
    user_id = reg["user"]["id"]
    # Give the user a SECOND team so list_teams_for_user returns 2+.
    await teams_repo.create_team(
        session,
        slug=f"extra-{UUID(user_id).hex[:12]}",
        display_name="Extra Team",
        creator_user_id=UUID(user_id),
    )
    await session.commit()

    client_id = await _seed_client(session)
    state = await _get_pre_github_state(client, client_id)

    r = await client.post(
        "/oauth/authorize/local",
        data={"email": "multi@x.io", "password": _PASSWORD, "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 200, r.text
    # It IS the reused consent page (unique marker + the team-selection form).
    assert "Authorize for the selected team" in r.text
    assert 'name="team_scope"' in r.text
    assert 'action="/oauth/authorize"' in r.text

    # The carried state is re-signed to stage=post_github (ready for consent submit).
    post_state = _scrape_hidden_state(r.text)
    assert post_state, "no hidden state in the consent page"
    claims = _verify_state(post_state)
    assert claims is not None
    assert claims["stage"] == "post_github"
    assert claims["user_id"] == user_id

    # NO code minted yet — the multi-team fork defers issuance to the consent submit.
    count = (
        await session.execute(
            sa.text("SELECT count(*) FROM oauth_authorization_codes")
        )
    ).scalar_one()
    assert count == 0


async def test_wrong_password_401_and_records_failure(client, session):
    """Wrong password -> HTTP 401 with the generic message AND record_failure
    called (failed_attempts incremented) — the same durable per-account lockout
    counter login uses (T-16-01-06)."""
    reg = await _register_local_user(client, "wrongpw@x.io")
    user_id = reg["user"]["id"]
    client_id = await _seed_client(session)
    state = await _get_pre_github_state(client, client_id)

    r = await client.post(
        "/oauth/authorize/local",
        data={"email": "wrongpw@x.io", "password": "totallywrong1", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 401, r.text
    assert "Invalid email or password." in r.text

    failed = (
        await session.execute(
            sa.text(
                "SELECT failed_attempts FROM local_credentials WHERE user_id = :uid"
            ),
            {"uid": user_id},
        )
    ).scalar_one()
    assert failed == 1


async def test_absent_email_401_identical_to_wrong_password(client, session):
    """Absent email and wrong password must be byte-identical (same status, same
    body) so there is no user-enumeration oracle (T-16-01-02). Both re-render the
    SAME login form (SAME state, SAME generic error, email never echoed back)."""
    await _register_local_user(client, "enumtarget@x.io")
    client_id = await _seed_client(session)
    state = await _get_pre_github_state(client, client_id)

    resp_absent = await client.post(
        "/oauth/authorize/local",
        data={"email": "nobody-absent@x.io", "password": "whatever123", "state": state},
        follow_redirects=False,
    )
    resp_wrong = await client.post(
        "/oauth/authorize/local",
        data={"email": "enumtarget@x.io", "password": "totallywrong1", "state": state},
        follow_redirects=False,
    )
    assert resp_absent.status_code == 401
    assert resp_wrong.status_code == 401
    assert resp_absent.status_code == resp_wrong.status_code
    assert resp_absent.text == resp_wrong.text
    assert "Invalid email or password." in resp_absent.text


async def test_locked_account_401_identical(client, session):
    """A locked account (locked_until in the future) returns the SAME generic
    401 as an absent email — no distinct 'locked' status/message (that would be
    a second enumeration oracle). No code is minted."""
    reg = await _register_local_user(client, "locked@x.io")
    user_id = reg["user"]["id"]
    # Force the account into a live lockout window.
    await session.execute(
        sa.text(
            "UPDATE local_credentials SET locked_until = now() + interval '15 minutes' "
            "WHERE user_id = :uid"
        ),
        {"uid": user_id},
    )
    await session.commit()

    client_id = await _seed_client(session)
    state = await _get_pre_github_state(client, client_id)

    # Correct password, but locked → generic 401 (no 'locked' oracle).
    resp_locked = await client.post(
        "/oauth/authorize/local",
        data={"email": "locked@x.io", "password": _PASSWORD, "state": state},
        follow_redirects=False,
    )
    resp_absent = await client.post(
        "/oauth/authorize/local",
        data={"email": "nobody-absent@x.io", "password": "whatever123", "state": state},
        follow_redirects=False,
    )
    assert resp_locked.status_code == 401, resp_locked.text
    assert resp_locked.text == resp_absent.text  # indistinguishable from absent
    assert "Invalid email or password." in resp_locked.text

    count = (
        await session.execute(
            sa.text("SELECT count(*) FROM oauth_authorization_codes")
        )
    ).scalar_one()
    assert count == 0


async def test_bad_state_no_code_minted(client, session):
    """Missing/expired/foreign state (bad signature or stage != pre_github) ->
    an error page, NO code minted. redirect_uri/identity are only ever taken
    from a VALID signed state (T-16-01-03/04)."""
    from app.routes.oauth_authorize import _sign_state

    await _register_local_user(client, "badstate@x.io")

    # (a) Garbage / unsigned state -> rejected.
    r_garbage = await client.post(
        "/oauth/authorize/local",
        data={"email": "badstate@x.io", "password": _PASSWORD, "state": "not-a-jwt"},
        follow_redirects=False,
    )
    assert r_garbage.status_code in (400, 401), r_garbage.text
    assert "location" not in {k.lower() for k in r_garbage.headers}

    # (b) A validly-SIGNED token but with the WRONG stage (post_github) -> rejected;
    #     only a pre_github token from GET /oauth/authorize is accepted here.
    foreign = _sign_state(
        {
            "client_id": "oac_whatever",
            "redirect_uri": _REDIRECT,
            "code_challenge": _CHALLENGE,
            "resource": _RESOURCE,
            "scope": "brain:read",
            "claude_state": "",
            "user_id": "00000000-0000-0000-0000-000000000001",
            "stage": "post_github",
        }
    )
    r_foreign = await client.post(
        "/oauth/authorize/local",
        data={"email": "badstate@x.io", "password": _PASSWORD, "state": foreign},
        follow_redirects=False,
    )
    assert r_foreign.status_code in (400, 401), r_foreign.text
    assert "location" not in {k.lower() for k in r_foreign.headers}

    count = (
        await session.execute(
            sa.text("SELECT count(*) FROM oauth_authorization_codes")
        )
    ).scalar_one()
    assert count == 0
