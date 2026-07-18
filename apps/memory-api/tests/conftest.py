"""Shared pytest fixtures.

Two-tier strategy:
- Unit tests (no DB) just import from `app.*` modules with config monkey-patched.
- Integration tests (`@pytest.mark.integration`) use testcontainers-Postgres + a real engine.

If Docker is not available, integration tests are skipped automatically.
"""

import asyncio
import os
import time
from collections.abc import AsyncGenerator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from authlib.jose import jwt

# --- Env defaults so `from app.config import settings` works in unit tests too ---
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("BRIDGE_SHARED_SECRET", "test-bridge-secret-do-not-use-in-prod")
os.environ.setdefault("GOOGLE_CLIENT_ID", "")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")
os.environ.setdefault("ADMIN_USER_SUBS", "admin-sub-1,admin-sub-2")
os.environ.setdefault("OAUTH_ISSUER_URL", "https://api.test.example")
os.environ.setdefault("OAUTH_RESOURCE_URL", "https://mcp.test.example/mcp")
os.environ.setdefault("AGENT_MENTION_ALIASES", "agent")


# === Helpers ===

def make_bridge_jwt(sub: str, team_scope: str, ttl: int = 300, secret: str | None = None) -> str:
    secret = secret or os.environ["BRIDGE_SHARED_SECRET"]
    now = int(time.time())
    payload = {
        "iss": "test-bridge",
        "sub": sub,
        "team_scope": team_scope,
        "scope": "bridge",
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode({"alg": "HS256"}, payload, secret).decode("ascii")


@pytest.fixture
def bridge_jwt():
    """Factory fixture: bridge_jwt(sub, team_scope) → JWT string."""
    return make_bridge_jwt


# === Integration fixtures (Postgres via testcontainers) ===

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
    """Spin a Postgres container, run Alembic upgrade head, yield the asyncpg URL."""
    if not _docker_available():
        pytest.skip("Docker not available — skipping integration fixture")
    from testcontainers.postgres import PostgresContainer

    pg = PostgresContainer("postgres:17", username="test", password="test", dbname="test").with_command(
        "postgres -c shared_preload_libraries=pgcrypto"
    )
    pg.start()
    raw = pg.get_connection_url()  # postgresql+psycopg2://...
    asyncpg_url = raw.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
    os.environ["DATABASE_URL"] = asyncpg_url

    # `app.config.settings` and `app.db.session.engine` are both module-level singletons
    # evaluated ONCE at first import, reading DATABASE_URL from the environment at that
    # instant. Setting os.environ above is not enough: if ANY test module was already
    # imported before this fixture ran this session (collection-time imports, or an
    # earlier-collected test file that imports app.config/app.main/app.db.session — even
    # lazily, inside a test body that executes before this fixture's first use), those
    # singletons are already frozen on the OLD (non-testcontainers) DATABASE_URL, and
    # env.py's own `settings.DATABASE_URL` read (below) would silently overwrite the
    # correct URL we're about to pass to Alembic with the stale one — every DB-touching
    # test for the rest of the session then fails to connect. Patch the singletons
    # directly so this fixture is correct regardless of what already ran or imported
    # things earlier in the session.
    import app.config as app_config

    app_config.settings.DATABASE_URL = asyncpg_url
    import app.db.session as db_session
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    await db_session.engine.dispose()
    db_session.engine = create_async_engine(
        asyncpg_url, pool_size=10, max_overflow=5, pool_pre_ping=True
    )
    db_session.async_session_factory = async_sessionmaker(
        db_session.engine, class_=AsyncSession, expire_on_commit=False
    )

    # Run alembic upgrade head against the test DB.
    # alembic/env.py's run_migrations_online() calls asyncio.run(...) internally. Calling
    # command.upgrade() directly here would nest that asyncio.run() inside the event loop
    # pytest-asyncio is already running this fixture under ("asyncio.run() cannot be called
    # from a running event loop"). Running it in a worker thread gives it a clean thread with
    # no running loop, which is exactly what asyncio.run() requires.
    from alembic import command
    from alembic.config import Config

    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", asyncpg_url)
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    await asyncio.to_thread(command.upgrade, cfg, "head")

    yield asyncpg_url
    pg.stop()


@pytest_asyncio.fixture(scope="session")
async def qdrant_url() -> AsyncGenerator[str, None]:
    """Spin a real Qdrant container, yield its REST URL, point settings.QDRANT_URL at it.

    Mirrors `pg_url` above: session-scoped, Docker-gated (skips cleanly when Docker is
    unavailable so restricted CI does not error rather than run). Phase 19 (EMBED-01)
    uses this to prove the keyless local-embed -> Qdrant -> semantic-search path against
    a REAL Qdrant (the "gate lesson": a mocked vector store cannot fail a ranking
    assertion even when the integration is broken).
    """
    if not _docker_available():
        pytest.skip("Docker not available — skipping integration fixture")
    from testcontainers.qdrant import QdrantContainer

    # Pin the image to the stack version (matches infrastructure/docker-compose.yml),
    # not testcontainers' older built-in default.
    q = QdrantContainer("qdrant/qdrant:v1.17.1")
    q.start()
    # REST port is 6333; get_exposed_port maps it to the host-published port.
    url = f"http://{q.get_container_host_ip()}:{q.get_exposed_port(6333)}"
    os.environ["QDRANT_URL"] = url

    # `app.config.settings` is a module-level singleton frozen at first import (the same
    # reasoning the pg_url fixture documents for DATABASE_URL). Setting os.environ alone
    # is not enough once app.config was imported earlier this session — patch the
    # singleton directly so `ensure_collections()` and NativeProvider both target THIS
    # container rather than the frozen localhost default.
    import app.config as app_config

    app_config.settings.QDRANT_URL = url

    yield url
    q.stop()


@pytest_asyncio.fixture
async def session(pg_url: str):
    """Per-test AsyncSession wrapped in a transaction that rolls back at teardown."""
    from app.db.session import async_session_factory, engine

    async with engine.connect() as conn:
        trans = await conn.begin()
        async with async_session_factory(bind=conn) as s:
            yield s
        await trans.rollback()
    # `engine` is a module-level singleton (app/db/session.py) shared across the whole
    # pytest session, but pytest-asyncio gives each test FUNCTION its own event loop by
    # default. A pooled asyncpg connection checked back in here stays bound to THIS
    # test's (about-to-close) loop; the next test's fresh loop then trips
    # "RuntimeError: Event loop is closed" trying to reuse or terminate it. Disposing the
    # pool after every test forces the next test to open connections fresh on its own
    # loop instead of inheriting a stale one.
    await engine.dispose()


@pytest_asyncio.fixture
async def client(pg_url: str, session) -> AsyncGenerator[httpx.AsyncClient, None]:
    """httpx.AsyncClient bound to the FastAPI app, with get_session override → test session."""
    from app.deps import get_session
    from app.main import app

    async def override_session():
        yield session

    app.dependency_overrides[get_session] = override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def seeded_two_teams(session) -> dict[str, Any]:
    """Seed: Team A (alice admin), Team B (bob admin). Returns ids/slugs/users."""
    from app.repos import teams as teams_repo
    from app.repos import users as users_repo

    alice = await users_repo.get_or_create_user(
        session, source_user_id="alice-sub", email="alice@test.local", display_name="Alice"
    )
    bob = await users_repo.get_or_create_user(
        session, source_user_id="bob-sub", email="bob@test.local", display_name="Bob"
    )
    team_a = await teams_repo.create_team(
        session, slug="team-a", display_name="Team A", creator_user_id=alice.id
    )
    team_b = await teams_repo.create_team(
        session, slug="team-b", display_name="Team B", creator_user_id=bob.id
    )
    await session.commit()
    return {"alice": alice, "bob": bob, "team_a": team_a, "team_b": team_b}
