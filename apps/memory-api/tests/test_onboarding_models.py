"""Unit tests for Team model new fields + TeamApiKey + TeamJoinRequest."""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("BRIDGE_SHARED_SECRET", "test")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")


def test_team_visibility_defaults_to_closed():
    from app.models.team import Team
    t = Team(slug="a", display_name="A")
    assert t.visibility == "closed"


def test_team_visibility_can_be_open():
    from app.models.team import Team
    t = Team(slug="b", display_name="B", visibility="open")
    assert t.visibility == "open"


def test_team_nullable_fields_default_to_none():
    from app.models.team import Team
    t = Team(slug="c", display_name="C")
    assert t.description is None
    assert t.github_org is None


def test_team_api_key_fields():
    from app.models.team import TeamApiKey
    k = TeamApiKey(provider="anthropic", key_enc="enc123")
    assert k.provider == "anthropic"
    assert k.key_enc == "enc123"


def test_team_join_request_status_defaults_to_pending():
    from app.models.team import TeamJoinRequest
    r = TeamJoinRequest()
    assert r.status == "pending"


def test_team_join_request_status_can_be_set():
    from app.models.team import TeamJoinRequest
    r = TeamJoinRequest(status="approved")
    assert r.status == "approved"
