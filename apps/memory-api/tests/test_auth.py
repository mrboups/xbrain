"""Auth tests — bridge JWT (HS256, no DB needed) and Google JWKs flow (mocked)."""

import time
from unittest.mock import AsyncMock, patch

import pytest
from authlib.jose import JoseError, jwt

from app.auth import (
    _reset_google_userinfo_cache_for_tests,
    verify_bridge_jwt,
    verify_google_access_token,
)
from app.config import settings

# --- Bridge JWT unit tests (no DB) ---


def test_valid_bridge_jwt_verified():
    payload = {
        "iss": "test-bridge",
        "sub": "alice-sub",
        "team_scope": "team-a",
        "scope": "bridge",
        "iat": int(time.time()),
        "exp": int(time.time()) + 60,
    }
    token = jwt.encode({"alg": "HS256"}, payload, settings.BRIDGE_SHARED_SECRET).decode("ascii")
    claims = verify_bridge_jwt(token, settings.BRIDGE_SHARED_SECRET)
    assert claims["sub"] == "alice-sub"
    assert claims["team_scope"] == "team-a"
    assert claims["scope"] == "bridge"


def test_expired_bridge_jwt_rejected():
    payload = {
        "iss": "test-bridge",
        "sub": "alice-sub",
        "team_scope": "team-a",
        "scope": "bridge",
        "iat": int(time.time()) - 600,
        "exp": int(time.time()) - 60,
    }
    token = jwt.encode({"alg": "HS256"}, payload, settings.BRIDGE_SHARED_SECRET).decode("ascii")
    with pytest.raises(JoseError):
        verify_bridge_jwt(token, settings.BRIDGE_SHARED_SECRET)


def test_bridge_jwt_with_wrong_secret_rejected():
    payload = {
        "iss": "test-bridge",
        "sub": "alice-sub",
        "team_scope": "team-a",
        "scope": "bridge",
        "iat": int(time.time()),
        "exp": int(time.time()) + 60,
    }
    token = jwt.encode({"alg": "HS256"}, payload, "wrong-secret").decode("ascii")
    with pytest.raises((JoseError, ValueError)):
        verify_bridge_jwt(token, settings.BRIDGE_SHARED_SECRET)


def test_bridge_jwt_with_non_bridge_scope_rejected():
    payload = {
        "iss": "test-bridge",
        "sub": "alice-sub",
        "team_scope": "team-a",
        "scope": "user",  # not "bridge"
        "iat": int(time.time()),
        "exp": int(time.time()) + 60,
    }
    token = jwt.encode({"alg": "HS256"}, payload, settings.BRIDGE_SHARED_SECRET).decode("ascii")
    with pytest.raises(ValueError):
        verify_bridge_jwt(token, settings.BRIDGE_SHARED_SECRET)


# --- Google OAuth access-token verification (quick task 260512-zca) ---


class _FakeResp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _clear_google_userinfo_cache():
    _reset_google_userinfo_cache_for_tests()
    yield
    _reset_google_userinfo_cache_for_tests()


@pytest.mark.asyncio
async def test_access_token_userinfo_happy_path():
    """A valid access token resolves to the userinfo claims dict."""
    payload = {
        "sub": "115306826285374220115",
        "email": "alice@example.com",
        "email_verified": True,
        "name": "Alice Example",
        "given_name": "Alice",
        "picture": "https://example.com/a.png",
    }
    with patch("app.auth.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get = AsyncMock(return_value=_FakeResp(200, payload))
        mock_client_cls.return_value = mock_client
        claims = await verify_google_access_token("ya29.opaque_access_token_xxx")
    assert claims["sub"] == "115306826285374220115"
    assert claims["email"] == "alice@example.com"
    assert claims["email_verified"] is True
    assert claims["name"] == "Alice Example"
    assert claims["iss"] == "https://accounts.google.com"


@pytest.mark.asyncio
async def test_access_token_401_rejected():
    """Google userinfo 401 → ValueError, never returns claims."""
    with patch("app.auth.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get = AsyncMock(return_value=_FakeResp(401, {"error": "invalid_token"}))
        mock_client_cls.return_value = mock_client
        with pytest.raises(ValueError, match="google userinfo failed: 401"):
            await verify_google_access_token("ya29.revoked")


@pytest.mark.asyncio
async def test_access_token_missing_sub_rejected():
    """Userinfo response without `sub` → ValueError."""
    with patch("app.auth.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get = AsyncMock(
            return_value=_FakeResp(200, {"email": "x@y.com", "email_verified": True})
        )
        mock_client_cls.return_value = mock_client
        with pytest.raises(ValueError, match="missing sub"):
            await verify_google_access_token("ya29.weird")


@pytest.mark.asyncio
async def test_access_token_unverified_email_rejected():
    """email_verified=false → ValueError (defensive against spoofable emails)."""
    with patch("app.auth.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get = AsyncMock(
            return_value=_FakeResp(
                200,
                {
                    "sub": "999",
                    "email": "unverified@example.com",
                    "email_verified": False,
                },
            )
        )
        mock_client_cls.return_value = mock_client
        with pytest.raises(ValueError, match="email is not verified"):
            await verify_google_access_token("ya29.unverified")


@pytest.mark.asyncio
async def test_access_token_empty_rejected():
    """Empty token short-circuits before any network call."""
    with pytest.raises(ValueError, match="empty access token"):
        await verify_google_access_token("")


@pytest.mark.asyncio
async def test_access_token_cached_on_second_call():
    """Second call within the TTL window returns the cached result (no HTTP)."""
    payload = {
        "sub": "555",
        "email": "bob@example.com",
        "email_verified": True,
        "name": "Bob",
    }
    call_count = 0

    async def fake_get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return _FakeResp(200, payload)

    with patch("app.auth.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get = AsyncMock(side_effect=fake_get)
        mock_client_cls.return_value = mock_client
        a = await verify_google_access_token("ya29.cached")
        b = await verify_google_access_token("ya29.cached")
    assert a["sub"] == "555"
    assert b["sub"] == "555"
    assert call_count == 1  # cache hit on the 2nd call


# --- HTTP-level (integration) ---


@pytest.mark.integration
@pytest.mark.asyncio
async def test_missing_bearer_returns_401(client, seeded_two_teams):
    r = await client.get("/v1/me")
    assert r.status_code in (401, 422)  # FastAPI may 422 if Header(...) is missing


@pytest.mark.integration
@pytest.mark.asyncio
async def test_invalid_token_returns_401(client, seeded_two_teams):
    r = await client.get(
        "/v1/me", headers={"Authorization": "Bearer this-is-not-a-jwt"}
    )
    assert r.status_code == 401


@pytest.mark.integration
@pytest.mark.asyncio
async def test_valid_bridge_jwt_works_on_me(client, seeded_two_teams, bridge_jwt):
    token = bridge_jwt(sub="alice-sub", team_scope="team-a")
    r = await client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "bridge"
    assert body["sub"] == "alice-sub"
