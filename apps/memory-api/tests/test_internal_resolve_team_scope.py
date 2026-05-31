"""Tests for GET /v1/internal/resolve-team-scope.

Three cases:
  1. Known user (email-style sub, team member) -> returns team slug + user_id, status 200.
  2. Unknown sub -> returns {team_scope: null, user_id: null}, status 200.
  3. Bridge JWT accepted (kind=='bridge') -> same resolution logic, status 200.

Uses app.dependency_overrides to bypass get_current_principal (same pattern as
test_admin_brain.py / test_brain_ingest_endpoint.py — no real JWT signing needed
in unit/integration tests; the route itself does not branch on principal kind).

Integration mark: requires Docker + testcontainers-Postgres (pg_url fixture from
conftest.py). Will be skipped automatically when Docker is not available.
"""
from __future__ import annotations

import os

import httpx
import pytest

# Env defaults so app.config parses without real values
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("BRIDGE_SHARED_SECRET", "test-bridge-secret-do-not-use-in-prod")
os.environ.setdefault("GOOGLE_CLIENT_ID", "")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# ── Helpers ──────────────────────────────────────────────────────────────


def _get_app_and_principal_dep():
    from app.deps import get_current_principal
    from app.main import app

    return app, get_current_principal


def _install_bridge_principal_override(team_scope: str = "test-team", sub: str = "bridge-svc") -> None:
    app, get_current_principal = _get_app_and_principal_dep()

    async def _override():
        return {"kind": "bridge", "sub": sub, "team_scope": team_scope}

    app.dependency_overrides[get_current_principal] = _override


def _clear_overrides() -> None:
    from app.main import app

    app.dependency_overrides.clear()


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clear_app_overrides():
    """Ensure dependency overrides are cleared after every test."""
    yield
    _clear_overrides()


# ── Tests ─────────────────────────────────────────────────────────────────


async def test_resolve_known_email_sub_returns_team_slug(pg_url, session, client):
    """An email-style sub that maps to a team member must return the team's slug."""
    from app.repos import teams as teams_repo
    from app.repos import users as users_repo

    # Seed: create user + team
    user = await users_repo.get_or_create_user(
        session,
        source_user_id="email:alice@internal.test",
        email="alice@internal.test",
        display_name="Alice Internal",
    )
    team = await teams_repo.create_team(
        session,
        slug="aibrussels",
        display_name="AI Brussels",
        creator_user_id=user.id,
    )
    await session.commit()

    # Install a bridge principal override so the endpoint accepts the request
    _install_bridge_principal_override(team_scope="aibrussels")

    resp = await client.get(
        "/v1/internal/resolve-team-scope",
        params={"sub": "email:alice@internal.test"},
        headers={"Authorization": "Bearer ignored-overridden"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["team_scope"] == "aibrussels"
    assert body["user_id"] == str(user.id)


async def test_resolve_unknown_sub_returns_nulls(client):
    """An unknown sub must return {team_scope: null, user_id: null} with status 200."""
    _install_bridge_principal_override()

    resp = await client.get(
        "/v1/internal/resolve-team-scope",
        params={"sub": "email:nobody@example.com"},
        headers={"Authorization": "Bearer ignored-overridden"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["team_scope"] is None
    assert body["user_id"] is None


async def test_resolve_accepts_bridge_jwt_kind(pg_url, session, client):
    """Endpoint must accept a bridge-kind principal (kind=='bridge') without 401/403."""
    from app.repos import teams as teams_repo
    from app.repos import users as users_repo

    user = await users_repo.get_or_create_user(
        session,
        source_user_id="bridge-user-sub",
        email="bridge-user@internal.test",
        display_name="Bridge User",
    )
    team = await teams_repo.create_team(
        session,
        slug="bridge-team",
        display_name="Bridge Team",
        creator_user_id=user.id,
    )
    await session.commit()

    # Explicitly verify the principal that gets passed is of kind='bridge'
    app, get_current_principal = _get_app_and_principal_dep()

    bridge_principal = {"kind": "bridge", "sub": "librechat-bridge-svc", "team_scope": "bridge-team"}

    async def _bridge_override():
        return bridge_principal

    app.dependency_overrides[get_current_principal] = _bridge_override

    resp = await client.get(
        "/v1/internal/resolve-team-scope",
        params={"sub": "bridge-user-sub"},
        headers={"Authorization": "Bearer ignored-overridden"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["team_scope"] == "bridge-team"
    assert body["user_id"] == str(user.id)
