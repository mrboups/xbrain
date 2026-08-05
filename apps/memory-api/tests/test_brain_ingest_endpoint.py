"""Tests for Phase 13 brain ingest endpoint + ingest_external_message helper.

Tests 1-4: POST /v1/brain/ingest endpoint behaviour.
           These tests use @pytest.mark.integration because they import app.main,
           which transitively imports qdrant_client (not available in the unit-test
           environment, only inside Docker). They run the same way as
           test_brain_events_list.py: skipped when Docker is unavailable.

Tests 5-8: ingest_external_message unit tests — no DB, no Docker, no app.main.
           These run as plain unit tests via mock.patch.
"""
from __future__ import annotations

import os
import uuid

# Ensure env defaults before any app import
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("BRIDGE_SHARED_SECRET", "test-bridge-secret-do-not-use-in-prod")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")

import pytest
import pytest_asyncio
import httpx
from unittest.mock import AsyncMock, MagicMock, patch


def _verdict(relevant: bool, *, important: bool = False, **kw):
    """The classifier's answer. The seam is `classify_detailed`, not `classify`.

    Since 2026-08-05 one call answers both AI-owned levels, so the ingest path
    reads a ClassifyResult rather than a bool — patching the bool wrapper would
    leave the real classifier running and these tests asserting nothing.
    """
    from app.services.relevance_filter import ClassifyResult

    return ClassifyResult(relevant=relevant, important=important, **kw)


# ─── Integration-test helpers (require app.main → qdrant_client) ─────


def _get_app_and_dep():
    """Lazy import so unit tests don't fail on missing qdrant_client."""
    from app.deps import get_current_principal, get_team_scope
    from app.main import app

    return app, get_current_principal, get_team_scope


def _make_bridge_principal(team_scope: str = "dejavudev", sub: str = "bridge-service"):
    return {"kind": "bridge", "sub": sub, "team_scope": team_scope}


@pytest_asyncio.fixture
async def app_client_bridge(pg_url):
    """httpx.AsyncClient with bridge principal override + team_scope=dejavudev.
    Requires the pg_url integration fixture (Docker).
    """
    app, get_current_principal, get_team_scope = _get_app_and_dep()
    from app.deps import get_session

    team_scope = "dejavudev"
    principal = _make_bridge_principal(team_scope=team_scope)

    app.dependency_overrides[get_current_principal] = lambda: principal
    app.dependency_overrides[get_team_scope] = lambda: team_scope

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


# ─── Tests 1-4: Endpoint behaviour (integration) ─────────────────────

pytestmark_integration = pytest.mark.integration


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ingest_endpoint_accepts_bridge_jwt(app_client_bridge):
    """Endpoint returns 202 with {status: accepted} for a valid bridge JWT."""
    resp = await app_client_bridge.post(
        "/v1/brain/ingest",
        json={
            "content": "We agreed the API uses JWT with 1h TTL",
            "source": "librechat:claude-haiku-4-5",
            "metadata": {"origin": "librechat", "librechat_id": "test-1"},
        },
    )
    assert resp.status_code == 202
    assert resp.json() == {"status": "accepted"}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ingest_endpoint_rejects_unauthenticated(pg_url):
    """Endpoint returns 422 when no Authorization header is provided (missing required header)."""
    app, _, _ = _get_app_and_dep()
    from app.deps import get_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/brain/ingest",
            headers={"X-Team-Scope": "dejavudev"},
            json={
                "content": "We agreed the API uses JWT with 1h TTL",
                "source": "librechat:x",
            },
        )
    # FastAPI returns 422 for missing required Header field
    assert resp.status_code == 422


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ingest_endpoint_validates_schema(app_client_bridge):
    """Endpoint returns 422 for empty content."""
    resp = await app_client_bridge.post(
        "/v1/brain/ingest",
        json={"content": "", "source": "x"},
    )
    assert resp.status_code == 422


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ingest_endpoint_fire_and_forget(app_client_bridge):
    """Endpoint returns 202 even if the background upsert would raise RuntimeError.

    The task runs as asyncio.create_task so errors in upsert never reach
    the HTTP response path.
    """
    with patch(
        "app.services.brain_ingest.get_memory_provider",
        return_value=MagicMock(upsert=AsyncMock(side_effect=RuntimeError("db exploded"))),
    ):
        with patch(
            "app.services.relevance_filter.classify_detailed",
            new=AsyncMock(return_value=_verdict(True)),
        ):
            resp = await app_client_bridge.post(
                "/v1/brain/ingest",
                json={
                    "content": "We agreed the API uses JWT with 1h TTL",
                    "source": "librechat:x",
                },
            )
    # 202 is returned immediately — the background task error is swallowed
    assert resp.status_code == 202


# ─── Tests 5-8: ingest_external_message unit tests (no DB, no Docker) ─


@pytest.mark.asyncio
async def test_ingest_external_message_builds_memory_item():
    """ingest_external_message builds MemoryItem with correct tagging contract fields."""
    from app.services import brain_ingest

    captured_items = []

    mock_provider = MagicMock()
    mock_provider.upsert = AsyncMock(side_effect=lambda item: captured_items.append(item))

    with patch("app.services.brain_ingest.get_memory_provider", return_value=mock_provider):
        with patch(
            "app.services.relevance_filter.classify_detailed",
            new=AsyncMock(return_value=_verdict(True)),
        ):
            await brain_ingest.ingest_external_message(
                team_scope="t1",
                content="long enough substantive content here for ingest",
                source="librechat:claude-haiku-4-5",
                author_sub="user@example.com",
                metadata={"origin": "librechat"},
            )

    assert len(captured_items) == 1
    item = captured_items[0]
    from xbrain_memory.types import TruthLevel, ValidationStatus, Visibility

    assert item.team_scope == "t1"
    assert item.truth_level == TruthLevel.WORKING
    assert item.visibility == Visibility.TEAM
    assert item.validation_status == ValidationStatus.PENDING
    assert item.source == "librechat:claude-haiku-4-5"
    assert item.metadata["origin"] == "librechat"
    assert item.metadata["author_sub"] == "user@example.com"
    assert item.confidence == 0.7


@pytest.mark.asyncio
async def test_ingest_external_message_skips_when_classify_false():
    """ingest_external_message skips upsert when classify returns False."""
    from app.services import brain_ingest

    mock_provider = MagicMock()
    mock_provider.upsert = AsyncMock()

    with patch("app.services.brain_ingest.get_memory_provider", return_value=mock_provider):
        with patch(
            "app.services.relevance_filter.classify_detailed",
            new=AsyncMock(return_value=_verdict(False)),
        ):
            await brain_ingest.ingest_external_message(
                team_scope="t1",
                content="long enough substantive content here for ingest",
                source="librechat:x",
                author_sub="user@example.com",
            )

    mock_provider.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_ingest_external_message_deterministic_id():
    """ingest_external_message uses deterministic UUID5 when idempotency_key present.

    Two calls with the same key produce the same MemoryItem.id.
    """
    from app.services import brain_ingest
    from app.services.brain_ingest import BRAIN_INGEST_NS

    captured_items = []
    mock_provider = MagicMock()
    mock_provider.upsert = AsyncMock(side_effect=lambda item: captured_items.append(item))

    idem_key = "librechat:abc-123"
    expected_id = str(uuid.uuid5(BRAIN_INGEST_NS, idem_key))

    with patch("app.services.brain_ingest.get_memory_provider", return_value=mock_provider):
        with patch(
            "app.services.relevance_filter.classify_detailed",
            new=AsyncMock(return_value=_verdict(True)),
        ):
            # Call twice with the same idempotency_key
            for _ in range(2):
                await brain_ingest.ingest_external_message(
                    team_scope="t1",
                    content="long enough substantive content here for ingest",
                    source="librechat:x",
                    author_sub="u1",
                    metadata={"idempotency_key": idem_key},
                )

    assert len(captured_items) == 2
    assert captured_items[0].id == expected_id
    assert captured_items[1].id == expected_id


@pytest.mark.asyncio
async def test_ingest_external_message_fail_soft():
    """ingest_external_message swallows exceptions — never raises to caller."""
    from app.services import brain_ingest

    with patch("app.services.brain_ingest.get_memory_provider") as mock_get_provider:
        mock_get_provider.return_value = MagicMock(
            upsert=AsyncMock(side_effect=RuntimeError("simulated upsert failure"))
        )
        with patch(
            "app.services.relevance_filter.classify_detailed",
            new=AsyncMock(return_value=_verdict(True)),
        ):
            # Must not raise
            result = await brain_ingest.ingest_external_message(
                team_scope="t1",
                content="long enough substantive content here for ingest",
                source="librechat:x",
                author_sub="u1",
            )
    # Returns None, never raises
    assert result is None
