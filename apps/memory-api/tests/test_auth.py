"""Auth tests — bridge JWT (HS256, no DB needed) and Google JWKs flow (mocked)."""

import time

import pytest
from authlib.jose import JoseError, jwt

from app.auth import verify_bridge_jwt
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
