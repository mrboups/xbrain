"""Tests for app.services.github_user_token.refresh_user_token_if_needed.

Plan 12-06 Task 5 — covers the refresh state machine (fresh / expiring /
expired / no-refresh-token / refresh-token-expired) PLUS the per-user lock
contract (RESEARCH §Pitfall 6 — single-use-refresh-token race) AND the
M-5 invariant that token_hash is rotated atomically with the ciphertext.

Mocking strategy mirrors tests/test_phase12_installation_token.py — respx
intercepts the GitHub token endpoint; the Fernet key is generated per-test
so the real prod key is never loaded.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
from cryptography.fernet import Fernet

from app.services import github_user_token, token_crypto
from app.services.github_user_token import (
    GitHubReauthRequired,
    _reset_locks_for_tests,
    refresh_user_token_if_needed,
)
from app.services.token_crypto import (
    decrypt_token,
    encrypt_token,
    token_lookup_hash,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def _configure(monkeypatch):
    """Per-test Fernet key + GitHub App OAuth client credentials.

    The two ``_reset_*_for_tests`` calls clear module-level caches: the Fernet
    instance is lru_cached on ``token_crypto._fernet``, and the per-user lock
    dict carries over between tests if not flushed.
    """
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(token_crypto.settings, "FERNET_KEY", key)
    monkeypatch.setattr(
        github_user_token.settings, "GITHUB_APP_CLIENT_ID", "Iv23li_test"
    )
    monkeypatch.setattr(
        github_user_token.settings, "GITHUB_APP_CLIENT_SECRET", "test_secret"
    )
    token_crypto._reset_for_tests()
    _reset_locks_for_tests()
    yield
    _reset_locks_for_tests()
    token_crypto._reset_for_tests()


async def _make_user(
    session,
    *,
    expires_at,
    refresh_token: str = "ghr_test_refresh",
    access: str = "ghu_old_access",
):
    """Insert a User row pre-populated with encrypted tokens + hash + expiry."""
    from app.models.user import User as UserModel

    u = UserModel(
        source_user_id="github:tester",
        email="t@example.com",
        display_name="Tester",
        github_username="tester",
        github_id=999,
        github_access_token_enc=encrypt_token(access),
        github_access_token_hash=token_lookup_hash(access),
        github_refresh_token_enc=encrypt_token(refresh_token),
        github_token_expires_at=expires_at,
        github_refresh_expires_at=(
            datetime.now(timezone.utc) + timedelta(days=180)
        ),
    )
    session.add(u)
    await session.commit()
    await session.refresh(u)
    return u


def _ok_refresh_response(
    *, access: str = "ghu_new_access", refresh: str = "ghr_new_refresh"
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "access_token": access,
            "refresh_token": refresh,
            "expires_in": 28800,
            "refresh_token_expires_in": 15897600,
            "token_type": "bearer",
            "scope": "",
        },
    )


async def test_returns_current_when_fresh(session):
    """When expires_at is > now+5min, returns current token without HTTP call."""
    u = await _make_user(
        session, expires_at=datetime.now(timezone.utc) + timedelta(hours=2)
    )
    with respx.mock(assert_all_called=False) as mock:
        # No route registered — any HTTP call would 404 the assertion.
        _ = mock
        out = await refresh_user_token_if_needed(session, u)
    assert out == "ghu_old_access"


@respx.mock
async def test_refreshes_when_expiring_and_updates_hash(session):
    """REVISION 2 (M-5) — token rotation updates the indexed hash column."""
    u = await _make_user(
        session, expires_at=datetime.now(timezone.utc) + timedelta(minutes=1)
    )
    old_hash = u.github_access_token_hash
    respx.post("https://github.com/login/oauth/access_token").mock(
        return_value=_ok_refresh_response()
    )
    out = await refresh_user_token_if_needed(session, u)
    assert out == "ghu_new_access"
    await session.refresh(u)
    assert decrypt_token(u.github_access_token_enc) == "ghu_new_access"
    assert decrypt_token(u.github_refresh_token_enc) == "ghr_new_refresh"
    # Hash MUST be updated to match the new plaintext.
    assert u.github_access_token_hash == token_lookup_hash("ghu_new_access")
    assert u.github_access_token_hash != old_hash


@respx.mock
async def test_concurrent_refresh_calls_github_once(session):
    """RESEARCH §Pitfall 6 — three concurrent refresh attempts share one mint.

    Without the per-user lock, each coroutine would call GitHub with the same
    refresh_token; the first wins, the next two get invalid_grant 401.
    The lock + double-checked locking pattern serialises them so GitHub
    sees exactly one call.
    """
    u = await _make_user(
        session, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
    )
    route = respx.post("https://github.com/login/oauth/access_token").mock(
        return_value=_ok_refresh_response()
    )
    results = await asyncio.gather(
        refresh_user_token_if_needed(session, u),
        refresh_user_token_if_needed(session, u),
        refresh_user_token_if_needed(session, u),
    )
    assert all(r == "ghu_new_access" for r in results)
    assert route.call_count == 1, (
        f"expected 1 refresh call, got {route.call_count} — "
        "per-user lock did not serialise correctly (Pitfall 6 regression)"
    )


@respx.mock
async def test_refresh_logical_error_clears_tokens_and_hash_and_raises(
    session,
):
    """REVISION 2 (M-5) — hash is cleared on logical refresh failure too.

    GitHub returns HTTP 200 with body['error'] on refresh-token-invalid
    (the most common logical failure). The handler MUST clear the stored
    ciphertext AND the lookup hash so deps.py O(log n) index cannot return
    a dead row for this user.
    """
    u = await _make_user(
        session, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
    )
    respx.post("https://github.com/login/oauth/access_token").mock(
        return_value=httpx.Response(
            200,
            json={
                "error": "bad_refresh_token",
                "error_description": "Token expired",
            },
        )
    )
    with pytest.raises(GitHubReauthRequired, match="bad_refresh_token"):
        await refresh_user_token_if_needed(session, u)
    await session.refresh(u)
    assert u.github_access_token_enc is None
    assert u.github_refresh_token_enc is None
    assert u.github_access_token_hash is None  # REVISION 2 (M-5)
    assert u.github_token_expires_at is None


@respx.mock
async def test_refresh_http_error_raises(session):
    u = await _make_user(
        session, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
    )
    respx.post("https://github.com/login/oauth/access_token").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    with pytest.raises(GitHubReauthRequired, match="HTTP 500"):
        await refresh_user_token_if_needed(session, u)


async def test_no_refresh_token_raises(session):
    """User with no stored refresh_token → 401 immediately, no HTTP call."""
    from app.models.user import User as UserModel

    u = UserModel(
        source_user_id="github:noref",
        email="n@example.com",
        github_username="noref",
        github_id=1,
        github_access_token_enc=encrypt_token("ghu_x"),
        github_access_token_hash=token_lookup_hash("ghu_x"),
        github_refresh_token_enc=None,
        github_token_expires_at=(
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ),
    )
    session.add(u)
    await session.commit()
    with pytest.raises(GitHubReauthRequired, match="No refresh token"):
        await refresh_user_token_if_needed(session, u)


async def test_refresh_token_expired_raises(session):
    """If refresh_token's own 6-month TTL elapsed, raise immediately."""
    u = await _make_user(
        session, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
    )
    u.github_refresh_expires_at = (
        datetime.now(timezone.utc) - timedelta(days=1)
    )
    await session.commit()
    with pytest.raises(GitHubReauthRequired, match="Refresh token expired"):
        await refresh_user_token_if_needed(session, u)
