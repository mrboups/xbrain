"""Unit tests for the Claude.ai Custom-Connector (oat_) path in mcp-brain.

Covers (all run as REAL assertions — httpx mocked so they never silently skip):
  (a) truth_level clamp helper (VALIDATED/CANONICAL/PUBLIC -> WORKING;
      EPHEMERAL/WORKING pass through)
  (b) oauth_verify audience-mismatch ValueError
  (c) an "oat_"-prefixed token routes to introspect (httpx mocked) and yields
      is_connector=True
  (d) an "xbt_"-prefixed Bearer does NOT call introspect() and stays on the
      existing path with is_connector=False
  (e) the internal email path bypasses the oat_ branch (no introspect call) and
      returns is_connector=False
  (f) _resolve returns a 3-tuple and the connector path sets is_connector=True

settings are patched on the module; memory_client + oauth_verify are mocked.
"""
from __future__ import annotations

import os

# Required env before any app imports.
os.environ.setdefault("BRIDGE_SHARED_SECRET", "test-secret-do-not-use")
os.environ.setdefault("MEMORY_API_URL", "http://memory-api.test")

import pytest
import pytest_asyncio  # noqa: F401 — needed for async fixture support
from unittest.mock import AsyncMock, patch

# Async tests are marked individually below; two pure-helper tests are sync, so
# we do NOT apply a module-level asyncio mark (it would warn on the sync ones).
_aio = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fake Context helpers (mirror tests/test_resolve.py).
# ---------------------------------------------------------------------------

class _FakeHeaders:
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
    def __init__(self, headers: dict[str, str]) -> None:
        self.request_context = _FakeRequestContext(headers)


_SECRET = "test-secret-do-not-use"
_TEAM = "team-a"
_OAT = "oat_faketoken123"
_XBT = "xbt_faketoken123"
_EMAIL = "alice@test.local"


def _ctx(**headers: str) -> FakeContext:
    out: dict[str, str] = {}
    if headers.get("authorization"):
        out["authorization"] = headers["authorization"]
    if headers.get("email"):
        out["x-librechat-user-email"] = headers["email"]
    if headers.get("internal_secret"):
        out["x-internal-secret"] = headers["internal_secret"]
    return FakeContext(out)


# ---------------------------------------------------------------------------
# (a) truth_level clamp helper.
# ---------------------------------------------------------------------------

def test_clamp_truth_level_caps_at_working():
    from app.memory_client import clamp_truth_level

    assert clamp_truth_level("EPHEMERAL") == "EPHEMERAL"
    assert clamp_truth_level("WORKING") == "WORKING"
    assert clamp_truth_level("VALIDATED") == "WORKING"
    assert clamp_truth_level("CANONICAL") == "WORKING"
    assert clamp_truth_level("PUBLIC") == "WORKING"
    # Unknown values are also downgraded (fail-safe).
    assert clamp_truth_level("WHATEVER") == "WORKING"


# ---------------------------------------------------------------------------
# (b) oauth_verify audience mismatch.
# ---------------------------------------------------------------------------

@_aio
async def test_oauth_verify_rejects_audience_mismatch():
    from app import oauth_verify

    class _Resp:
        status_code = 200

        def json(self):
            # active token, but aud points at a DIFFERENT resource.
            return {
                "active": True,
                "sub": "github:alice",
                "team_scope": _TEAM,
                "aud": "https://evil.example/mcp",
                "source": "claude.ai-connector",
            }

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.post = AsyncMock(return_value=_Resp())

    with (
        patch("app.oauth_verify.httpx.AsyncClient", return_value=mock_client),
        patch("app.oauth_verify.settings.OAUTH_RESOURCE_URL", "https://mcp.example.com/mcp"),
        patch("app.oauth_verify.settings.BRIDGE_SHARED_SECRET", _SECRET),
    ):
        with pytest.raises(ValueError, match="audience"):
            await oauth_verify.introspect(_OAT)


@_aio
async def test_oauth_verify_accepts_matching_audience():
    from app import oauth_verify

    class _Resp:
        status_code = 200

        def json(self):
            return {
                "active": True,
                "sub": "github:alice",
                "team_scope": _TEAM,
                "aud": "https://mcp.example.com/mcp/",  # trailing slash normalized
                "source": "claude.ai-connector",
            }

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.post = AsyncMock(return_value=_Resp())

    with (
        patch("app.oauth_verify.httpx.AsyncClient", return_value=mock_client),
        patch("app.oauth_verify.settings.OAUTH_RESOURCE_URL", "https://mcp.example.com/mcp"),
        patch("app.oauth_verify.settings.BRIDGE_SHARED_SECRET", _SECRET),
    ):
        info = await oauth_verify.introspect(_OAT)
    assert info["team_scope"] == _TEAM
    assert info["source"] == "claude.ai-connector"


@_aio
async def test_oauth_verify_inactive_raises():
    from app import oauth_verify

    class _Resp:
        status_code = 200

        def json(self):
            return {"active": False}

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.post = AsyncMock(return_value=_Resp())

    with (
        patch("app.oauth_verify.httpx.AsyncClient", return_value=mock_client),
        patch("app.oauth_verify.settings.BRIDGE_SHARED_SECRET", _SECRET),
    ):
        with pytest.raises(ValueError, match="inactive"):
            await oauth_verify.introspect(_OAT)


# ---------------------------------------------------------------------------
# (c) oat_ routes through introspect -> is_connector=True (3-tuple).
# ---------------------------------------------------------------------------

@_aio
async def test_oat_routes_to_introspect_and_sets_is_connector():
    ctx = _ctx(authorization=f"Bearer {_OAT}")

    mock_introspect = AsyncMock(return_value={
        "sub": "github:alice", "team_scope": _TEAM,
        "source": "claude.ai-connector", "resource": "https://mcp.example.com/mcp",
    })

    with (
        patch("app.main.oauth_verify.introspect", mock_introspect),
        patch("app.main.settings.BRIDGE_SHARED_SECRET", _SECRET),
    ):
        from app.main import _resolve

        result = await _resolve(ctx)

    # 3-tuple contract.
    assert len(result) == 3
    token, team_scope, is_connector = result
    assert team_scope == _TEAM
    assert is_connector is True
    # Connector path mints a bridge JWT (3 dot-separated segments).
    assert token.count(".") == 2
    mock_introspect.assert_awaited_once_with(_OAT)


# ---------------------------------------------------------------------------
# (d) xbt_ NEVER calls introspect; is_connector=False.
# ---------------------------------------------------------------------------

@_aio
async def test_xbt_does_not_call_introspect():
    ctx = _ctx(authorization=f"Bearer {_XBT}")

    mock_introspect = AsyncMock()
    mock_get_me = AsyncMock(return_value={"api_token_team_scope": _TEAM})

    with (
        patch("app.main.oauth_verify.introspect", mock_introspect),
        patch("app.main.memory_client.get_me", mock_get_me),
        patch("app.main.settings.BRIDGE_SHARED_SECRET", _SECRET),
    ):
        from app.main import _resolve

        token, team_scope, is_connector = await _resolve(ctx)

    assert token == _XBT
    assert team_scope == _TEAM
    assert is_connector is False
    mock_get_me.assert_awaited_once_with(_XBT)
    mock_introspect.assert_not_awaited()  # xbt_ MUST NOT introspect


# ---------------------------------------------------------------------------
# (e) email path bypasses oat_ branch; is_connector=False.
# ---------------------------------------------------------------------------

@_aio
async def test_email_path_bypasses_oat_branch():
    ctx = _ctx(email=_EMAIL, internal_secret=_SECRET)

    mock_introspect = AsyncMock()
    mock_resolve = AsyncMock(return_value=_TEAM)

    with (
        patch("app.main.oauth_verify.introspect", mock_introspect),
        patch("app.main.memory_client.resolve_team_scope_internal", mock_resolve),
        patch("app.main.settings.BRIDGE_SHARED_SECRET", _SECRET),
        patch("app.main.settings.INTERNAL_EMAIL_PATH_ENABLED", True),
    ):
        from app.main import _resolve

        token, team_scope, is_connector = await _resolve(ctx)

    assert team_scope == _TEAM
    assert is_connector is False
    assert token.count(".") == 2  # bridge JWT
    mock_introspect.assert_not_awaited()  # email path never introspects


# ---------------------------------------------------------------------------
# (f) explicit 3-tuple shape on the connector path.
# ---------------------------------------------------------------------------

@_aio
async def test_resolve_returns_three_tuple_for_connector():
    ctx = _ctx(authorization=f"Bearer {_OAT}")

    mock_introspect = AsyncMock(return_value={
        "sub": "github:bob", "team_scope": _TEAM,
        "source": "claude.ai-connector", "resource": "https://mcp.example.com/mcp",
    })

    with (
        patch("app.main.oauth_verify.introspect", mock_introspect),
        patch("app.main.settings.BRIDGE_SHARED_SECRET", _SECRET),
    ):
        from app.main import _resolve

        out = await _resolve(ctx)

    assert isinstance(out, tuple) and len(out) == 3
    assert out[2] is True


# ---------------------------------------------------------------------------
# Protected-resource metadata + WWW-Authenticate URL shape.
# ---------------------------------------------------------------------------

def test_protected_resource_metadata_shape():
    from app import main as m

    meta = m._protected_resource_metadata()
    # resource has NO trailing slash.
    assert meta["resource"] == m.settings.OAUTH_RESOURCE_URL.rstrip("/")
    assert meta["authorization_servers"] == [m.settings.OAUTH_ISSUER_URL.rstrip("/")]
    assert meta["scopes_supported"] == ["brain:read", "brain:write"]
    assert meta["bearer_methods_supported"] == ["header"]
    # WWW-Authenticate resource_metadata URL points at the resource host ROOT.
    assert m._PROTECTED_RESOURCE_METADATA_URL.endswith(
        "/.well-known/oauth-protected-resource"
    )
    assert "/mcp/.well-known" not in m._PROTECTED_RESOURCE_METADATA_URL


# ---------------------------------------------------------------------------
# memory_add connector guardrails: source forced + truth clamped.
# ---------------------------------------------------------------------------

@_aio
async def test_memory_add_connector_forces_source_and_clamps_truth():
    from app import memory_client

    captured = {}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

    async def _fake_post(url, json=None, headers=None):
        captured["item"] = json["item"]
        return _Resp()

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.post = _fake_post

    with patch("app.memory_client.httpx.AsyncClient", return_value=mock_client):
        await memory_client.memory_add(
            "bridge.jwt.token", _TEAM, "a fact", None,
            truth_level="CANONICAL", is_connector=True,
        )

    assert captured["item"]["source"] == "claude.ai-connector"
    assert captured["item"]["truth_level"] == "WORKING"  # clamped from CANONICAL


@_aio
async def test_memory_add_non_connector_unchanged():
    from app import memory_client

    captured = {}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

    async def _fake_post(url, json=None, headers=None):
        captured["item"] = json["item"]
        return _Resp()

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.post = _fake_post

    with patch("app.memory_client.httpx.AsyncClient", return_value=mock_client):
        await memory_client.memory_add(
            "xbt_token", _TEAM, "a fact", None, truth_level="VALIDATED",
        )

    # Default (non-connector) behavior unchanged: source mcp-brain, truth as-is.
    assert captured["item"]["source"] == "mcp-brain"
    assert captured["item"]["truth_level"] == "VALIDATED"
