"""Migration 0031 — user_api_tokens.capability applies and comes back off cleanly.

Same two layers as test_migration_0030.py: a container-free shape/symmetry pass,
and a Docker-gated round trip on a database of its own.

The property worth pinning here beyond the usual: the column must be NULLABLE
with no default. Every token minted before this migration — and every token the
unchanged POST /v1/me/api-token still mints — reads back capability=NULL, which
``token_capabilities.is_path_allowed`` treats as unrestricted. A NOT NULL column
with a default would have silently narrowed the whole deployed fleet.
"""
from __future__ import annotations

import os

import pytest
import sqlalchemy as sa

MIGRATION_REVISION = "0031_api_token_capability"
PARENT_REVISION = "0030_user_profile"


def _migration_module():
    import importlib.util

    path = os.path.join(
        os.path.dirname(__file__), "..", "alembic", "versions",
        "0031_api_token_capability.py",
    )
    spec = importlib.util.spec_from_file_location("_mig_0031", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _record(monkeypatch, fn) -> list[str]:
    statements: list[str] = []
    monkeypatch.setattr("alembic.op.execute", lambda sql: statements.append(str(sql)))
    fn()
    return statements


def test_upgrade_adds_the_column_and_its_index(monkeypatch):
    statements = _record(monkeypatch, _migration_module().upgrade)
    assert any("add column" in s.lower() and "capability" in s for s in statements)
    assert any("create index" in s.lower() and "capability" in s for s in statements)


def test_downgrade_undoes_exactly_what_upgrade_applied(monkeypatch):
    module = _migration_module()
    added = _record(monkeypatch, module.upgrade)
    dropped = _record(monkeypatch, module.downgrade)
    assert len(added) == len(dropped)
    assert any("drop column" in s.lower() and "capability" in s for s in dropped)
    assert any("drop index" in s.lower() and "capability" in s for s in dropped)


def test_both_directions_are_idempotent(monkeypatch):
    module = _migration_module()
    for statement in _record(monkeypatch, module.upgrade):
        assert "IF NOT EXISTS" in statement.upper(), statement
    for statement in _record(monkeypatch, module.downgrade):
        assert "IF EXISTS" in statement.upper(), statement


def test_the_migration_only_touches_user_api_tokens(monkeypatch):
    module = _migration_module()
    for statement in _record(monkeypatch, module.upgrade) + _record(
        monkeypatch, module.downgrade
    ):
        # `DROP INDEX` names only the index, which is itself namespaced to the
        # table by its own name — that is the one statement without the table.
        assert "user_api_tokens" in statement or "idx_api_tokens_capability" in statement, statement


def test_the_chain_has_one_head_and_0031_is_on_it():
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
async def test_0031_leaves_existing_tokens_unrestricted(session):
    """A token minted before the migration must read back capability=NULL.

    That NULL is what keeps the whole deployed fleet full-access. Asserting it
    against a real Postgres is the only way to catch a DEFAULT sneaking in.
    """
    user_id = (await session.execute(sa.text(
        "INSERT INTO users (id, source_user_id, email) "
        "VALUES (gen_random_uuid(), :sid, :email) RETURNING id"
    ), {"sid": "test:0031:legacy", "email": "legacy-0031@x.io"})).scalar_one()
    # Exactly the INSERT the pre-0031 code path runs — no capability column.
    row = (await session.execute(sa.text(
        "INSERT INTO user_api_tokens (user_id, token_hash, team_scope, name) "
        "VALUES (:uid, :hash, :ts, :name) RETURNING capability"
    ), {
        "uid": str(user_id),
        "hash": "0031-legacy-hash",
        "ts": "team-a",
        "name": "legacy",
    })).mappings().fetchone()
    assert row["capability"] is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_the_column_is_nullable_with_no_default(session):
    row = (await session.execute(sa.text(
        "SELECT is_nullable, column_default FROM information_schema.columns "
        "WHERE table_name = 'user_api_tokens' AND column_name = 'capability'"
    ))).mappings().fetchone()
    assert row is not None, "0031 did not apply"
    assert row["is_nullable"] == "YES"
    assert row["column_default"] is None
