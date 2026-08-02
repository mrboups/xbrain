"""Migration 0032 — the transcript_imports ledger.

The whole feature rests on ONE database object: the UNIQUE index on
``(team_scope, dedupe_key)``. It is what makes "importing the same
conversation twice must not double the brain" a guarantee rather than a
best-effort read-then-write that two shortcuts fired from a phone in the same
second would both win. So it gets a real-Postgres test that inserts the
conflict and reads the verdict, not just a statement-shape check.

Equally load-bearing and equally cheap to get wrong: the index is on
(team, key), NOT on key alone. The same conversation belongs in two teams as
two separately-tagged pieces of knowledge; a global unique index would let the
first team to import it lock every other team out.
"""
from __future__ import annotations

import os

import pytest
import sqlalchemy as sa

MIGRATION_REVISION = "0032_transcript_imports"
PARENT_REVISION = "0031_api_token_capability"


def _migration_module():
    import importlib.util

    path = os.path.join(
        os.path.dirname(__file__), "..", "alembic", "versions",
        "0032_transcript_imports.py",
    )
    spec = importlib.util.spec_from_file_location("_mig_0032", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _record(monkeypatch, fn) -> list[str]:
    statements: list[str] = []
    monkeypatch.setattr("alembic.op.execute", lambda sql: statements.append(str(sql)))
    fn()
    return statements


def test_upgrade_creates_the_table_and_the_unique_dedupe_index(monkeypatch):
    statements = _record(monkeypatch, _migration_module().upgrade)
    assert any("create table" in s.lower() and "transcript_imports" in s for s in statements)
    unique = [s for s in statements if "create unique index" in s.lower()]
    assert len(unique) == 1
    assert "team_scope, dedupe_key" in unique[0]


def test_downgrade_undoes_exactly_what_upgrade_applied(monkeypatch):
    module = _migration_module()
    added = _record(monkeypatch, module.upgrade)
    dropped = _record(monkeypatch, module.downgrade)
    assert len(added) == len(dropped)
    assert any("drop table" in s.lower() and "transcript_imports" in s for s in dropped)


def test_both_directions_are_idempotent(monkeypatch):
    module = _migration_module()
    for statement in _record(monkeypatch, module.upgrade):
        assert "IF NOT EXISTS" in statement.upper(), statement
    for statement in _record(monkeypatch, module.downgrade):
        assert "IF EXISTS" in statement.upper(), statement


def test_the_chain_has_one_head_and_0032_is_on_it():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "alembic"))
    script = ScriptDirectory.from_config(cfg)

    heads = list(script.get_heads())
    assert len(heads) == 1, f"expected a single migration head, got {heads}"
    assert script.get_revision(MIGRATION_REVISION).down_revision == PARENT_REVISION


@pytest.mark.integration
@pytest.mark.asyncio
async def test_the_same_key_cannot_be_claimed_twice_in_one_team(session):
    claim = sa.text("""
        INSERT INTO transcript_imports (team_scope, dedupe_key, source_format)
        VALUES (:ts, :key, 'chatgpt')
        ON CONFLICT (team_scope, dedupe_key) DO NOTHING
        RETURNING id
    """)
    first = (await session.execute(
        claim, {"ts": "team-a", "key": "chatgpt:conv-x"}
    )).fetchone()
    second = (await session.execute(
        claim, {"ts": "team-a", "key": "chatgpt:conv-x"}
    )).fetchone()
    assert first is not None
    # "No row came back" IS the duplicate verdict — decided by Postgres, not
    # by a read-then-write two concurrent requests would both win.
    assert second is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_two_teams_can_each_hold_the_same_conversation(session):
    claim = sa.text("""
        INSERT INTO transcript_imports (team_scope, dedupe_key, source_format)
        VALUES (:ts, :key, 'chatgpt')
        ON CONFLICT (team_scope, dedupe_key) DO NOTHING
        RETURNING id
    """)
    a = (await session.execute(claim, {"ts": "team-a", "key": "chatgpt:shared"})).fetchone()
    b = (await session.execute(claim, {"ts": "team-b", "key": "chatgpt:shared"})).fetchone()
    assert a is not None and b is not None
    assert a[0] != b[0]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_deleting_the_importer_keeps_the_ledger_row(session):
    """ON DELETE SET NULL, not CASCADE: removing an account must not silently
    reopen re-importing everything that account ever brought in."""
    user_id = (await session.execute(sa.text(
        "INSERT INTO users (id, source_user_id, email) "
        "VALUES (gen_random_uuid(), :sid, :email) RETURNING id"
    ), {"sid": "test:0032:importer", "email": "importer-0032@x.io"})).scalar_one()

    await session.execute(sa.text("""
        INSERT INTO transcript_imports (team_scope, dedupe_key, source_format, imported_by)
        VALUES ('team-a', 'chatgpt:orphan', 'chatgpt', :uid)
    """), {"uid": str(user_id)})
    await session.execute(
        sa.text("DELETE FROM users WHERE id = :uid"), {"uid": str(user_id)}
    )

    row = (await session.execute(sa.text(
        "SELECT imported_by FROM transcript_imports WHERE dedupe_key = 'chatgpt:orphan'"
    ))).mappings().fetchone()
    assert row is not None, "the ledger row was cascaded away with the account"
    assert row["imported_by"] is None
