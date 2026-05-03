"""Shared fixtures for agent-runtime tests.

Tests in this package require a real Postgres (testcontainers) because
LangGraph's AsyncPostgresSaver does not have an in-memory equivalent that
exercises the same code paths. Tests are skipped if Docker is unavailable.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from typing import Any

import httpx
import pytest
import pytest_asyncio

# --- Env defaults so app.config import doesn't blow up in unit tests ---
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("BRIDGE_SHARED_SECRET", "test-bridge-secret-do-not-use-in-prod")
os.environ.setdefault("MEMORY_API_URL", "http://memory-api.test")
os.environ.setdefault("GOOGLE_CLIENT_ID", "")


def _docker_available() -> bool:
    try:
        import docker  # noqa: F401

        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


@pytest_asyncio.fixture(scope="session")
async def pg_url() -> AsyncGenerator[str, None]:
    if not _docker_available():
        pytest.skip("Docker not available — skipping integration fixture")
    from testcontainers.postgres import PostgresContainer

    pg = PostgresContainer("postgres:17", username="test", password="test", dbname="test")
    pg.start()
    raw = pg.get_connection_url()
    psycopg_url = raw.replace("postgresql+psycopg2://", "postgresql://")
    os.environ["DATABASE_URL"] = psycopg_url
    yield psycopg_url
    pg.stop()


@pytest_asyncio.fixture
async def client(pg_url: str) -> AsyncGenerator[httpx.AsyncClient, None]:
    """httpx.AsyncClient bound to the FastAPI app — fresh checkpointer per session."""
    # Reset module-level singleton so each test session uses the testcontainer pool
    from app import checkpointer as cp_mod
    cp_mod._pool = None
    cp_mod._saver = None

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            yield c

    await cp_mod.close_checkpointer()


def _make_bridge_jwt(sub: str, team_scope: str) -> str:
    """Build a service JWT the agent-runtime will accept (HS256 with shared secret)."""
    import time

    from authlib.jose import jwt

    now = int(time.time())
    payload = {
        "iss": "test",
        "sub": sub,
        "team_scope": team_scope,
        "scope": "bridge",
        "iat": now,
        "exp": now + 600,
    }
    return jwt.encode({"alg": "HS256"}, payload, os.environ["BRIDGE_SHARED_SECRET"]).decode("ascii")


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Default headers for an agent-runtime call: bridge JWT + team_scope=team-a."""
    token = _make_bridge_jwt(sub="test-agent", team_scope="team-a")
    return {"Authorization": f"Bearer {token}", "X-Team-Scope": "team-a"}


@pytest.fixture
def make_auth_headers():
    """Factory for producing auth headers with a custom team_scope."""

    def _f(team_scope: str = "team-a", sub: str = "test-agent") -> dict[str, str]:
        token = _make_bridge_jwt(sub=sub, team_scope=team_scope)
        return {"Authorization": f"Bearer {token}", "X-Team-Scope": team_scope}

    return _f
