"""validate_xbt_token: 60s TTL cache + kind filtering."""
from __future__ import annotations

import httpx
import pytest
import respx

from app import auth as auth_mod
from app.config import settings


@pytest.mark.asyncio
async def test_cache_ttl_one_http_call_within_window(monkeypatch):
    """Two validate_xbt_token calls within TTL should hit memory-api exactly once."""
    monkeypatch.setattr(auth_mod, "_TTL", 60.0)

    with respx.mock(assert_all_called=False) as m:
        route = m.get(f"{settings.MEMORY_API_URL}/v1/me").mock(
            return_value=httpx.Response(
                200, json={"sub": "user-1", "kind": "user_api_token", "email": "a@b"}
            )
        )

        me1 = await auth_mod.validate_xbt_token("xbt_test")
        me2 = await auth_mod.validate_xbt_token("xbt_test")

        assert me1 == me2
        assert me1["sub"] == "user-1"
        assert route.call_count == 1


@pytest.mark.asyncio
async def test_cache_miss_after_ttl_expires(monkeypatch):
    """When clock advances past TTL, the next call must hit memory-api again."""
    fake_time = [1000.0]

    def fake_now() -> float:
        return fake_time[0]

    monkeypatch.setattr(auth_mod, "_now", fake_now)
    monkeypatch.setattr(auth_mod, "_TTL", 60.0)

    with respx.mock(assert_all_called=False) as m:
        route = m.get(f"{settings.MEMORY_API_URL}/v1/me").mock(
            return_value=httpx.Response(
                200, json={"sub": "user-1", "kind": "user_api_token"}
            )
        )

        await auth_mod.validate_xbt_token("xbt_test")
        assert route.call_count == 1

        # Advance past TTL
        fake_time[0] += 61.0

        await auth_mod.validate_xbt_token("xbt_test")
        assert route.call_count == 2


@pytest.mark.asyncio
async def test_rejects_non_user_api_token_kind():
    """memory-api returns 200 but kind != user_api_token → PermissionError."""
    with respx.mock(assert_all_called=False) as m:
        m.get(f"{settings.MEMORY_API_URL}/v1/me").mock(
            return_value=httpx.Response(
                200, json={"sub": "user-1", "kind": "oauth_session"}
            )
        )
        with pytest.raises(PermissionError, match="only xbt_"):
            await auth_mod.validate_xbt_token("not-an-xbt-token")


@pytest.mark.asyncio
async def test_rejects_non_200_response():
    """memory-api returns 401 → PermissionError with the status in the message."""
    with respx.mock(assert_all_called=False) as m:
        m.get(f"{settings.MEMORY_API_URL}/v1/me").mock(
            return_value=httpx.Response(401, json={"detail": "invalid"})
        )
        with pytest.raises(PermissionError, match="401"):
            await auth_mod.validate_xbt_token("xbt_bad")
