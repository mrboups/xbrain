"""Unit tests for the dual-path _resolve function in mcp-brain main.py.

Covers:
  1. Token path: xbt_ token -> get_me called, (token, team_scope) returned.
  2. Email path (allow): correct X-Internal-Secret + email -> team resolved, bridge JWT returned.
  3. Email path (refuse): wrong/missing X-Internal-Secret -> ValueError raised.
  4. Email path (no team): resolver returns None for both sub forms -> ValueError raised.

All async tests use asyncio.run() via pytest-asyncio (pytestmark = pytest.mark.asyncio).
memory_client functions are patched with AsyncMock; settings are patched on the module.
"""
from __future__ import annotations

import os

# Set required env vars before any app imports.
os.environ.setdefault("BRIDGE_SHARED_SECRET", "test-secret-do-not-use")
os.environ.setdefault("MEMORY_API_URL", "http://memory-api.test")

import pytest
import pytest_asyncio  # noqa: F401 — needed for async fixture support
from unittest.mock import AsyncMock, patch

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Fake Context helpers
# ---------------------------------------------------------------------------

class _FakeHeaders:
    """Case-insensitive header dict (minimal subset used by _resolve)."""

    def __init__(self, data: dict[str, str]) -> None:
        self._data = {k.lower(): v for k, v in data.items()}

    def get(self, key: str, default: str | None = None) -> str | None:
        return self._data.get(key.lower(), default)


class _FakeRequest:
    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = _FakeHeaders(headers)


class _FakeRequestContext:
    def __init__(self, headers: dict[str, str]) -> None:
        self.request = _FakeRequest(headers)


class FakeContext:
    """Minimal FastMCP Context substitute."""

    def __init__(self, headers: dict[str, str]) -> None:
        self.request_context = _FakeRequestContext(headers)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SECRET = "test-secret-do-not-use"
_WRONG_SECRET = "wrong-secret"
_EMAIL = "alice@test.local"
_TEAM = "aibrussels"
_XBT_TOKEN = "xbt_faketoken123"


def _make_ctx(
    *,
    authorization: str = "",
    email: str = "",
    internal_secret: str = "",
) -> FakeContext:
    headers: dict[str, str] = {}
    if authorization:
        headers["authorization"] = authorization
    if email:
        headers["x-librechat-user-email"] = email
    if internal_secret:
        headers["x-internal-secret"] = internal_secret
    return FakeContext(headers)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_token_path_returns_token_and_team_scope():
    """xbt_ token -> get_me called once -> (token, team_scope) returned."""
    ctx = _make_ctx(authorization=f"Bearer {_XBT_TOKEN}")

    mock_get_me = AsyncMock(return_value={"api_token_team_scope": _TEAM})

    with (
        patch("app.main.memory_client.get_me", mock_get_me),
        patch("app.main.settings.BRIDGE_SHARED_SECRET", _SECRET),
        patch("app.main.settings.INTERNAL_EMAIL_PATH_ENABLED", True),
    ):
        from app.main import _resolve

        token, team_scope = await _resolve(ctx)

    assert token == _XBT_TOKEN
    assert team_scope == _TEAM
    mock_get_me.assert_awaited_once_with(_XBT_TOKEN)


async def test_token_path_uses_team_scope_fallback_key():
    """Falls back to me['team_scope'] when api_token_team_scope is absent."""
    ctx = _make_ctx(authorization=f"Bearer {_XBT_TOKEN}")

    mock_get_me = AsyncMock(return_value={"team_scope": _TEAM})

    with (
        patch("app.main.memory_client.get_me", mock_get_me),
        patch("app.main.settings.BRIDGE_SHARED_SECRET", _SECRET),
    ):
        from app.main import _resolve

        token, team_scope = await _resolve(ctx)

    assert token == _XBT_TOKEN
    assert team_scope == _TEAM


async def test_token_path_raises_when_no_team():
    """xbt_ token with no team -> ValueError referencing /v1/me/api-token."""
    ctx = _make_ctx(authorization=f"Bearer {_XBT_TOKEN}")

    mock_get_me = AsyncMock(return_value={})

    with (
        patch("app.main.memory_client.get_me", mock_get_me),
        patch("app.main.settings.BRIDGE_SHARED_SECRET", _SECRET),
    ):
        from app.main import _resolve

        with pytest.raises(ValueError, match="api-token"):
            await _resolve(ctx)


async def test_email_path_allow_returns_bridge_jwt_and_team():
    """Correct secret + email -> resolve_team_scope_internal called -> bridge JWT returned."""
    ctx = _make_ctx(email=_EMAIL, internal_secret=_SECRET)

    mock_resolve = AsyncMock(return_value=_TEAM)

    with (
        patch("app.main.memory_client.resolve_team_scope_internal", mock_resolve),
        patch("app.main.settings.BRIDGE_SHARED_SECRET", _SECRET),
        patch("app.main.settings.INTERNAL_EMAIL_PATH_ENABLED", True),
    ):
        from app.main import _resolve

        result_jwt, team_scope = await _resolve(ctx)

    # The returned "token" must be a bridge JWT (3 dot-separated segments).
    assert team_scope == _TEAM
    parts = result_jwt.split(".")
    assert len(parts) == 3, f"Expected JWT with 3 segments, got: {result_jwt!r}"
    # Resolver must have been called at least once.
    assert mock_resolve.await_count >= 1


async def test_email_path_refuse_wrong_secret():
    """Wrong X-Internal-Secret -> ValueError; email path is unreachable."""
    ctx = _make_ctx(email=_EMAIL, internal_secret=_WRONG_SECRET)

    mock_resolve = AsyncMock(return_value=_TEAM)

    with (
        patch("app.main.memory_client.resolve_team_scope_internal", mock_resolve),
        patch("app.main.settings.BRIDGE_SHARED_SECRET", _SECRET),
        patch("app.main.settings.INTERNAL_EMAIL_PATH_ENABLED", True),
    ):
        from app.main import _resolve

        with pytest.raises(ValueError, match="Authentication required"):
            await _resolve(ctx)

    mock_resolve.assert_not_awaited()


async def test_email_path_refuse_missing_secret():
    """No X-Internal-Secret header -> ValueError regardless of email."""
    ctx = _make_ctx(email=_EMAIL)  # no internal_secret

    mock_resolve = AsyncMock(return_value=_TEAM)

    with (
        patch("app.main.memory_client.resolve_team_scope_internal", mock_resolve),
        patch("app.main.settings.BRIDGE_SHARED_SECRET", _SECRET),
        patch("app.main.settings.INTERNAL_EMAIL_PATH_ENABLED", True),
    ):
        from app.main import _resolve

        with pytest.raises(ValueError, match="Authentication required"):
            await _resolve(ctx)

    mock_resolve.assert_not_awaited()


async def test_email_path_refuse_when_killswitch_off():
    """INTERNAL_EMAIL_PATH_ENABLED=False -> email path blocked even with correct secret."""
    ctx = _make_ctx(email=_EMAIL, internal_secret=_SECRET)

    mock_resolve = AsyncMock(return_value=_TEAM)

    with (
        patch("app.main.memory_client.resolve_team_scope_internal", mock_resolve),
        patch("app.main.settings.BRIDGE_SHARED_SECRET", _SECRET),
        patch("app.main.settings.INTERNAL_EMAIL_PATH_ENABLED", False),
    ):
        from app.main import _resolve

        with pytest.raises(ValueError, match="Authentication required"):
            await _resolve(ctx)

    mock_resolve.assert_not_awaited()


async def test_email_path_refuse_empty_server_secret():
    """Empty BRIDGE_SHARED_SECRET -> email path blocked (fail-closed when unconfigured)."""
    ctx = _make_ctx(email=_EMAIL, internal_secret=_SECRET)

    mock_resolve = AsyncMock(return_value=_TEAM)

    with (
        patch("app.main.memory_client.resolve_team_scope_internal", mock_resolve),
        patch("app.main.settings.BRIDGE_SHARED_SECRET", ""),
        patch("app.main.settings.INTERNAL_EMAIL_PATH_ENABLED", True),
    ):
        from app.main import _resolve

        with pytest.raises(ValueError, match="Authentication required"):
            await _resolve(ctx)

    mock_resolve.assert_not_awaited()


async def test_email_path_no_team_raises():
    """resolver returns None for both sub forms -> ValueError 'No team'."""
    ctx = _make_ctx(email=_EMAIL, internal_secret=_SECRET)

    # Both resolve attempts return None.
    mock_resolve = AsyncMock(return_value=None)

    with (
        patch("app.main.memory_client.resolve_team_scope_internal", mock_resolve),
        patch("app.main.settings.BRIDGE_SHARED_SECRET", _SECRET),
        patch("app.main.settings.INTERNAL_EMAIL_PATH_ENABLED", True),
    ):
        from app.main import _resolve

        with pytest.raises(ValueError, match="No team"):
            await _resolve(ctx)

    # Must have been called twice: once with "email:<addr>", once with bare email.
    assert mock_resolve.await_count == 2


async def test_email_path_retry_with_bare_email_succeeds():
    """First resolve call (email: prefix) returns None; retry with bare email succeeds."""
    ctx = _make_ctx(email=_EMAIL, internal_secret=_SECRET)

    # First call (sub="email:alice@test.local") returns None; second (sub=bare email) returns team.
    call_count = 0

    async def _side_effect(jwt_token: str, sub: str) -> str | None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return None  # email: prefix not found
        return _TEAM  # bare email found

    with (
        patch("app.main.memory_client.resolve_team_scope_internal", side_effect=_side_effect),
        patch("app.main.settings.BRIDGE_SHARED_SECRET", _SECRET),
        patch("app.main.settings.INTERNAL_EMAIL_PATH_ENABLED", True),
    ):
        from app.main import _resolve

        result_jwt, team_scope = await _resolve(ctx)

    assert team_scope == _TEAM
    assert call_count == 2
