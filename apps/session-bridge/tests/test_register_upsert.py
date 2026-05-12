"""memory_api_client.upsert_external_session: bridge JWT shape + fire-and-forget semantics."""
from __future__ import annotations

import httpx
import pytest
import respx
from authlib.jose import jwt as jose_jwt

from app import memory_api_client
from app.config import settings


@pytest.mark.asyncio
async def test_upsert_signs_jwt_with_acting_user_sub():
    """The Authorization header must carry a JWT whose acting_user_sub == user_sub arg."""
    captured: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = httpx.Request("POST", request.url, content=request.content).content
        return httpx.Response(200, json={"id": "row-1", "provider": "claude"})

    with respx.mock(assert_all_called=False) as m:
        m.post(f"{settings.MEMORY_API_URL}/v1/me/external-sessions").mock(side_effect=_handler)

        ok = await memory_api_client.upsert_external_session(
            user_sub="user-abc",
            provider="claude",
            extension_id="ext-123",
            metadata={"email_logged": "a@b.com", "org_id": "org-1"},
        )

    assert ok is True
    auth_header = captured["auth"]
    assert auth_header.startswith("Bearer ")
    bridge_jwt = auth_header.removeprefix("Bearer ")

    claims = jose_jwt.decode(bridge_jwt, settings.BRIDGE_SHARED_SECRET)
    assert claims["sub"] == "session-bridge"
    assert claims["iss"] == "session-bridge"
    assert claims["scope"] == "bridge"
    assert claims["acting_user_sub"] == "user-abc"

    body_raw = captured["body"]
    import json as _json

    payload = _json.loads(body_raw)
    assert payload["provider"] == "claude"
    assert payload["extension_id"] == "ext-123"
    assert payload["metadata"]["email_logged"] == "a@b.com"
    assert payload["metadata"]["org_id"] == "org-1"


@pytest.mark.asyncio
async def test_upsert_returns_false_on_5xx():
    """Fire-and-forget: 500 must NOT raise; returns False."""
    with respx.mock(assert_all_called=False) as m:
        m.post(f"{settings.MEMORY_API_URL}/v1/me/external-sessions").mock(
            return_value=httpx.Response(500, json={"detail": "boom"})
        )
        ok = await memory_api_client.upsert_external_session(
            user_sub="user-1", provider="claude", extension_id=None, metadata=None
        )
    assert ok is False


@pytest.mark.asyncio
async def test_upsert_returns_false_on_network_error():
    """ConnectError must NOT bubble — returns False."""
    with respx.mock(assert_all_called=False) as m:
        m.post(f"{settings.MEMORY_API_URL}/v1/me/external-sessions").mock(
            side_effect=httpx.ConnectError("memory-api unreachable")
        )
        ok = await memory_api_client.upsert_external_session(
            user_sub="user-1", provider="claude", extension_id=None, metadata=None
        )
    assert ok is False


@pytest.mark.asyncio
async def test_upsert_returns_false_without_secret(monkeypatch):
    """If BRIDGE_SHARED_SECRET is empty, no HTTP call must happen and returns False."""
    monkeypatch.setattr(settings, "BRIDGE_SHARED_SECRET", "")

    with respx.mock(assert_all_called=False) as m:
        route = m.post(f"{settings.MEMORY_API_URL}/v1/me/external-sessions")

        ok = await memory_api_client.upsert_external_session(
            user_sub="user-1", provider="claude", extension_id=None, metadata=None
        )

        assert ok is False
        assert route.call_count == 0
