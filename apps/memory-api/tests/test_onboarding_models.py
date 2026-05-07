"""Unit tests for Team model new fields + TeamApiKey + TeamJoinRequest."""

import os

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("BRIDGE_SHARED_SECRET", "test")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")


def test_team_model_has_visibility():
    from app.models.team import Team
    t = Team(slug="a", display_name="A", visibility="open")
    assert t.visibility == "open"


def test_team_api_key_model_exists():
    from app.models.team import TeamApiKey
    k = TeamApiKey(provider="anthropic", key_enc="enc")
    assert k.provider == "anthropic"


def test_team_join_request_model_exists():
    from app.models.team import TeamJoinRequest
    r = TeamJoinRequest(status="pending")
    assert r.status == "pending"
