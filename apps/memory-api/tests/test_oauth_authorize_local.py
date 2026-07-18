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
