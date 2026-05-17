"""Tests for app.services.github_installation — cache, hybrid lookup, refresh.

Phase 12 plan 12-03 task 2.

Coverage:
  - Cache hit returns cached token (no HTTP call).
  - Cache miss + table miss → fallback /orgs/{org}/installation → UPSERTs row.
  - force_refresh=True bypasses cache, mints new token.
  - Concurrent calls for same installation_id share the per-id asyncio.Lock
    (token minted EXACTLY ONCE — regression guard for RESEARCH §Pitfall 6).
  - 401 on the membership-endpoint retry path triggers force_refresh
    (REVISION 2 M-4 fix — exercised via the 401-on-mint internal retry inside
    get_installation_token_for_org).
  - 404 from /orgs/{org}/installation → find_installation_for_org returns None.

Mocking strategy:
  - GitHub HTTP calls are intercepted with respx.MockRouter (mirrors the
    test_phase10_auth.py pattern). respx is wired against httpx so any
    unmocked call raises immediately — failures are not silent.
  - mint_app_jwt() is exercised for real but against a synthetic RSA key
    generated per-test via cryptography (mirrors test_phase12_jwt.py — the
    real prod key MUST NOT be loaded in unit tests).

Marker note:
  - Tests using the `session` fixture require the testcontainers-Postgres
    integration tier (see tests/conftest.py). The module-level pytestmark
    therefore applies `integration` to ALL tests in the file; the pure-cache
    cases (which never touch `session`) still pass under that tier since
    testcontainers spin-up is shared per session.
"""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
import sqlalchemy as sa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.models.installation import Installation
from app.services import github_app_jwt, github_installation
from app.services.github_installation import (
    _reset_caches_for_tests,
    find_installation_for_org,
    get_installation_token,
    get_installation_token_for_org,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# === Fixtures ================================================================


@pytest.fixture
def app_pem_b64() -> str:
    """Generate a fresh RSA key per test, return base64-encoded PEM.

    Mirrors test_phase12_jwt.py — the real prod key is never loaded from env
    in unit tests (success-criteria invariant from plan 12-02).
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return base64.b64encode(pem).decode()


@pytest.fixture(autouse=True)
def _configure_app_jwt(monkeypatch, app_pem_b64):
    """Configure GITHUB_APP_* settings + reset module caches each test.

    The github_installation module loads the App JWT via mint_app_jwt() which
    reads settings.GITHUB_APP_PRIVATE_KEY_B64 and settings.GITHUB_APP_CLIENT_ID.
    Without these, mint_app_jwt() raises GitHubAppNotConfigured before any
    respx-mocked HTTP call would fire.

    Also clears the in-process installation token cache + per-id locks between
    tests so cache-state from one test does not leak into the next.
    """
    monkeypatch.setattr(github_app_jwt.settings, "GITHUB_APP_CLIENT_ID", "Iv23li_test")
    monkeypatch.setattr(github_app_jwt.settings, "GITHUB_APP_PRIVATE_KEY_B64", app_pem_b64)
    github_app_jwt._reset_private_key_cache_for_tests()
    _reset_caches_for_tests()
    yield
    _reset_caches_for_tests()
    github_app_jwt._reset_private_key_cache_for_tests()


# === Helpers =================================================================


def _mock_mint_token(
    mock: respx.MockRouter,
    installation_id: int,
    *,
    token: str = "ghs_test_token",
    ttl_hours: int = 1,
) -> respx.Route:
    """Register a respx route mocking POST /app/installations/{id}/access_tokens.

    Returns the route object so tests can assert call_count.
    """
    expires_at = (
        datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    return mock.post(
        f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    ).mock(
        return_value=httpx.Response(
            201, json={"token": token, "expires_at": expires_at}
        )
    )


def _mint_response(token: str, *, ttl_hours: int = 1) -> httpx.Response:
    """Build a fresh mint-endpoint 201 response (used for side_effect lists)."""
    expires_at = (
        datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    return httpx.Response(201, json={"token": token, "expires_at": expires_at})


# === get_installation_token: cache behaviour =================================


async def test_cache_hit_returns_cached_token_no_http_call():
    """Two sequential calls for same installation → exactly 1 HTTP mint call."""
    with respx.mock(assert_all_called=False) as mock:
        route = _mock_mint_token(mock, 12345)
        t1 = await get_installation_token(12345)
        t2 = await get_installation_token(12345)
    assert t1 == "ghs_test_token"
    assert t2 == t1, "second call must return the same cached token"
    assert route.call_count == 1, (
        f"cache hit should NOT call GitHub (got {route.call_count} calls)"
    )


async def test_force_refresh_bypasses_cache():
    """force_refresh=True ignores the cache and mints a new token."""
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(
            "https://api.github.com/app/installations/12345/access_tokens"
        ).mock(
            side_effect=[
                _mint_response("ghs_first"),
                _mint_response("ghs_second"),
            ]
        )
        t1 = await get_installation_token(12345)
        t2 = await get_installation_token(12345, force_refresh=True)
    assert t1 == "ghs_first"
    assert t2 == "ghs_second", "force_refresh must mint a NEW token"
    assert route.call_count == 2


async def test_different_installations_have_independent_caches():
    """installation_id 1 and installation_id 2 do not share cache entries."""
    with respx.mock(assert_all_called=False) as mock:
        r1 = _mock_mint_token(mock, 1, token="ghs_one")
        r2 = _mock_mint_token(mock, 2, token="ghs_two")
        t1 = await get_installation_token(1)
        t2 = await get_installation_token(2)
    assert t1 == "ghs_one"
    assert t2 == "ghs_two"
    assert r1.call_count == 1
    assert r2.call_count == 1


# === get_installation_token: concurrent mint race regression =================


async def test_concurrent_calls_for_same_installation_share_lock():
    """Regression guard for RESEARCH §Pitfall 6 — cache stampede.

    Without the per-installation_id asyncio.Lock + double-checked locking
    inside the lock, three concurrent cold-cache calls would each fire a
    mint POST, and call_count would be 3. With the lock, exactly ONE mint
    call fires; the other two coroutines wait and consume the cached result.
    """
    with respx.mock(assert_all_called=False) as mock:
        route = _mock_mint_token(mock, 99)
        results = await asyncio.gather(
            get_installation_token(99),
            get_installation_token(99),
            get_installation_token(99),
        )
    assert all(r == "ghs_test_token" for r in results)
    assert route.call_count == 1, (
        f"per-installation lock failed: expected exactly 1 mint call, "
        f"got {route.call_count} — cache stampede regressed"
    )


# === find_installation_for_org: hybrid lookup ================================


async def test_find_installation_db_hit_no_http_call(session):
    """When installations row exists, returns the id without hitting GitHub."""
    session.add(
        Installation(
            installation_id=4242,
            github_org_login="dejavudev",
            github_account_type="Organization",
            permissions={},
            raw_payload={},
        )
    )
    await session.commit()
    with respx.mock(assert_all_called=False):
        # No mock routes registered — any HTTP call would raise.
        out = await find_installation_for_org(session, "dejavudev")
    assert out == 4242


async def test_find_installation_db_miss_then_github_hit_backfills(session):
    """DB miss + GitHub 200 → INSERT installations row + return id.

    This is RESEARCH §Pitfall 2 reconciliation — the webhook was missed but
    the App is actually installed, so we backfill the row on-demand.
    """
    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://api.github.com/orgs/dejavudev/installation").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 7777,
                    "account": {
                        "login": "dejavudev",
                        "type": "Organization",
                        "id": 100,
                    },
                    "permissions": {"members": "read"},
                },
            )
        )
        out = await find_installation_for_org(session, "dejavudev")
    assert out == 7777
    # Verify backfill row inserted with correct shape.
    row = (
        await session.execute(
            sa.select(Installation).where(Installation.installation_id == 7777)
        )
    ).scalar_one()
    assert row.github_org_login == "dejavudev"
    assert row.github_account_type == "Organization"
    assert row.permissions == {"members": "read"}
    assert row.revoked_at is None


async def test_find_installation_not_installed_returns_none(session):
    """GitHub 404 → app is not installed on this org → returns None."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://api.github.com/orgs/randomorg/installation").mock(
            return_value=httpx.Response(404, json={"message": "Not Found"})
        )
        out = await find_installation_for_org(session, "randomorg")
    assert out is None


# === get_installation_token_for_org: combined paths ==========================


async def test_get_token_for_org_combines_lookup_and_mint(session):
    """DB lookup returns id, then mint produces token."""
    session.add(
        Installation(
            installation_id=5555,
            github_org_login="dejavudev",
            github_account_type="Organization",
            permissions={},
            raw_payload={},
        )
    )
    await session.commit()
    with respx.mock(assert_all_called=False) as mock:
        _mock_mint_token(mock, 5555, token="ghs_dejavu")
        out = await get_installation_token_for_org(session, "dejavudev")
    assert out == "ghs_dejavu"


async def test_get_token_for_org_returns_none_when_app_not_installed(session):
    """Lookup returns None → no mint attempt, returns None."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://api.github.com/orgs/nope/installation").mock(
            return_value=httpx.Response(404)
        )
        out = await get_installation_token_for_org(session, "nope")
    assert out is None


async def test_get_token_for_org_retries_once_on_401_from_mint(session):
    """401 on mint endpoint → internal force_refresh retry returns fresh token.

    This is the in-helper retry path: get_installation_token raises
    HTTPStatusError(401) on the first call, get_installation_token_for_org
    catches it and retries once with force_refresh=True. The retry succeeds
    because the cache was cleared and the second mint call returns 201.
    """
    session.add(
        Installation(
            installation_id=8888,
            github_org_login="rotated",
            github_account_type="Organization",
            permissions={},
            raw_payload={},
        )
    )
    await session.commit()
    with respx.mock(assert_all_called=False) as mock:
        # side_effect list — first call 401, second call 201.
        mock.post(
            "https://api.github.com/app/installations/8888/access_tokens"
        ).mock(
            side_effect=[
                httpx.Response(401, json={"message": "Bad credentials"}),
                _mint_response("ghs_after_refresh"),
            ]
        )
        out = await get_installation_token_for_org(session, "rotated")
    assert out == "ghs_after_refresh"


async def test_get_token_for_org_force_refresh_kwarg_bypasses_cache(session):
    """REVISION 2 (M-4 fix) — caller-provided force_refresh proxies through.

    This is the path used by app/auth.py:check_github_org_membership (Plan
    12-04 Task 1) when /orgs/{org}/members/{username} returns 401. The
    caller passes force_refresh=True on the retry so the cache is bypassed
    and a fresh installation token is minted.
    """
    session.add(
        Installation(
            installation_id=9999,
            github_org_login="forcetest",
            github_account_type="Organization",
            permissions={},
            raw_payload={},
        )
    )
    await session.commit()
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(
            "https://api.github.com/app/installations/9999/access_tokens"
        ).mock(
            side_effect=[
                _mint_response("ghs_first"),
                _mint_response("ghs_second"),
            ]
        )
        t1 = await get_installation_token_for_org(session, "forcetest")
        # Cached call — must not hit GitHub.
        t2 = await get_installation_token_for_org(session, "forcetest")
        # force_refresh=True bypasses cache → mints again.
        t3 = await get_installation_token_for_org(
            session, "forcetest", force_refresh=True
        )
    assert t1 == "ghs_first"
    assert t2 == "ghs_first", "second call should serve from cache"
    assert t3 == "ghs_second", "force_refresh must mint a new token"
    assert route.call_count == 2, (
        f"expected 2 mint calls (initial + force_refresh), got {route.call_count}"
    )
