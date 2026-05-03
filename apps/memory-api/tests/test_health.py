"""Healthz / readyz tests."""

import pytest


@pytest.mark.asyncio
async def test_healthz_returns_200_without_db():
    """`/healthz` is the liveness probe — must return 200 even without DB connectivity."""
    import httpx

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/v1/healthz")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_readyz_returns_200_when_deps_ok(client, pg_url):
    """`/readyz` should return 200 when Postgres is up. Qdrant may or may not be — if down, 503."""
    r = await client.get("/v1/readyz")
    assert r.status_code in (200, 503)
    body = r.json()
    assert "deps" in body
    assert "postgres" in body["deps"]
    # Postgres should always be ok in this test (testcontainers spins it up)
    assert body["deps"]["postgres"] == "ok"
