"""Unit tests for the Phase 23 "Catch me up" read-cursor foundation (Plan 23-01).

These are pure unit tests — no Docker, no Postgres. They assert the CONTRACTS the
downstream endpoints (23-02) and the real-Postgres gate (23-03) consume:

  * migration 0026 chains off 0025, is additive/idempotent, and carries no EDITION
    branch;
  * `TeamMember.last_read_at` is a nullable timezone-aware timestamp column;
  * `repos.teams.set_last_read`, `repos.team_messages.list_messages(after_created_at=)`
    and `repos.team_messages.count_unread_since` exist with the exact signatures the
    endpoints call, and the count query filters to "other members' non-deleted user
    messages" (excludes the caller + agent frames + soft-deleted rows).

Behavioral proof against a real Postgres (cursor advance, since-window rows, the
count excluding the caller) is deliberately deferred to the gate in Plan 23-03.
"""
from __future__ import annotations

import importlib.util
import inspect
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import DateTime

from app.models.team import TeamMember
from app.repos import team_messages as tm_repo
from app.repos import teams as teams_repo

_HERE = Path(__file__).resolve().parent
_MIGRATION_0026 = (
    _HERE / ".." / "alembic" / "versions" / "0026_team_member_last_read.py"
).resolve()


def _load_migration_0026():
    """Import the 0026 module by path (its name starts with a digit → not importable
    with a plain `import`). Executes `from alembic import op` — a real import, so a
    broken migration module would raise here."""
    spec = importlib.util.spec_from_file_location("migration_0026", _MIGRATION_0026)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


# === Migration 0026 ===

def test_migration_0026_chains_off_0025():
    mod = _load_migration_0026()
    assert mod.revision == "0026_team_member_last_read"
    assert mod.down_revision == "0025_team_agent_aliases"


def test_migration_0026_is_additive_idempotent():
    src = _MIGRATION_0026.read_text(encoding="utf-8")
    up = inspect.getsource(_load_migration_0026().upgrade)
    assert "ALTER TABLE team_members ADD COLUMN IF NOT EXISTS last_read_at TIMESTAMPTZ" in up
    # Additive-only: the upgrade must not DROP or backfill.
    assert "DROP" not in up
    assert "UPDATE" not in up
    # Symmetric downgrade present for form only.
    down = inspect.getsource(_load_migration_0026().downgrade)
    assert "DROP COLUMN IF EXISTS last_read_at" in down
    # Edition-agnostic: the test_migration_editions gate greps for the EDITION token
    # (case-sensitive) across code lines — assert it is absent here too.
    for line in src.splitlines():
        if line.lstrip().startswith("#"):
            continue
        assert "EDITION" not in line, f"migration 0026 must not branch on EDITION: {line!r}"


# === ORM column ===

def test_team_member_last_read_at_column_is_nullable_timestamptz():
    col = TeamMember.__table__.columns["last_read_at"]
    assert col.nullable is True, "last_read_at must be nullable (NULL = never read)"
    assert isinstance(col.type, DateTime)
    assert col.type.timezone is True, "last_read_at must be timezone-aware (TIMESTAMPTZ)"
    # Forward-only column: no server_default (the cursor is set via a Python datetime
    # in set_last_read, never auto-filled on INSERT).
    assert col.server_default is None


# === Repo signatures ===

def test_set_last_read_signature():
    sig = inspect.signature(teams_repo.set_last_read)
    params = sig.parameters
    assert "team_id" in params and "user_id" in params
    # Both are keyword-only (mirrors the other membership mutators in this repo).
    assert params["team_id"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["user_id"].kind is inspect.Parameter.KEYWORD_ONLY
    # The repo flushes; the route commits — it must NOT call session.commit().
    body = inspect.getsource(teams_repo.set_last_read)
    assert "session.flush()" in body
    assert ".commit()" not in body
    assert "last_read_at" in body


def test_list_messages_has_after_created_at_window():
    sig = inspect.signature(tm_repo.list_messages)
    params = sig.parameters
    assert "after_created_at" in params, "list_messages must accept after_created_at"
    assert params["after_created_at"].default is None
    # before_created_at must still be there (existing behavior unchanged).
    assert "before_created_at" in params
    body = inspect.getsource(tm_repo.list_messages)
    assert "TeamMessage.created_at > after_created_at" in body
    # ordering unchanged — still newest-first.
    assert "desc(TeamMessage.created_at)" in body


def test_count_unread_since_signature_and_predicates():
    sig = inspect.signature(tm_repo.count_unread_since)
    params = sig.parameters
    assert set(("team_id", "after_created_at", "exclude_user_id")).issubset(params)
    assert params["after_created_at"].default is inspect.Parameter.empty or (
        params["after_created_at"].annotation is not inspect.Parameter.empty
    )
    body = inspect.getsource(tm_repo.count_unread_since)
    # Counts only OTHER members' non-deleted user messages.
    assert 'TeamMessage.kind == "user"' in body
    assert "TeamMessage.author_user_id != exclude_user_id" in body
    assert "TeamMessage.deleted_at.is_(None)" in body
    # NULL cursor ⇒ count everything (no lower bound applied).
    assert "if after_created_at is not None" in body
    assert body.rstrip().endswith("return int(result.scalar_one() or 0)")


def test_now_is_timezone_aware_helper_used():
    """set_last_read must write a tz-aware datetime (mirrors block_member) so the
    returned row stays `.isoformat()`-safe. Guards the D-23-01 note."""
    body = inspect.getsource(teams_repo.set_last_read)
    assert "datetime.now(tz=timezone.utc)" in body
    # sanity: the helper this mirrors is importable and tz-aware.
    assert datetime.now(tz=timezone.utc).tzinfo is timezone.utc
