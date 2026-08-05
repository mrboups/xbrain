"""Migration 0033 — teams.agent_provider.

Two things are load-bearing and both are cheap to get wrong.

The DEFAULT. Every team that exists when this migration runs must keep exactly
today's behaviour, and today's behaviour is Anthropic. A column that defaulted to
NULL — or to anything else — would silently re-route the fallback spend of every
existing team on deploy.

The CHECK. The value is read on the path that decides which vendor to bill, so
the accepted set is enforced by the database and not only by the route that
happens to write it today. The set in the constraint and the set the application
will select from are pinned equal here: a value the code can choose but the
constraint rejects fails at write time, in an admin's face, as a database error.
"""
from __future__ import annotations

import os

import pytest
import sqlalchemy as sa

MIGRATION_REVISION = "0033_team_agent_provider"
PARENT_REVISION = "0032_transcript_imports"


def _migration_module():
    import importlib.util

    path = os.path.join(
        os.path.dirname(__file__), "..", "alembic", "versions",
        "0033_team_agent_provider.py",
    )
    spec = importlib.util.spec_from_file_location("_mig_0033", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _record(monkeypatch, fn) -> list[str]:
    statements: list[str] = []
    monkeypatch.setattr("alembic.op.execute", lambda sql: statements.append(str(sql)))
    fn()
    return statements


def test_the_column_defaults_to_anthropic(monkeypatch):
    statements = _record(monkeypatch, _migration_module().upgrade)
    add = [s for s in statements if "ADD COLUMN" in s.upper()]
    assert len(add) == 1
    assert "agent_provider" in add[0]
    assert "DEFAULT 'anthropic'" in add[0], (
        "without this default every existing team is re-routed on deploy"
    )
    assert "NOT NULL" in add[0].upper()


def test_the_accepted_set_is_enforced_by_the_database(monkeypatch):
    statements = _record(monkeypatch, _migration_module().upgrade)
    check = [s for s in statements if "CHECK" in s.upper()]
    assert len(check) == 1
    for provider in ("anthropic", "openai", "xai"):
        assert f"'{provider}'" in check[0]


def test_the_constraint_and_the_code_accept_the_same_providers():
    from app.services.team_keys import SUPPORTED_PROVIDERS

    assert set(_migration_module()._ACCEPTED) == set(SUPPORTED_PROVIDERS), (
        "a provider the code can select but the constraint rejects is a database "
        "error thrown in an admin's face"
    )


def test_every_supported_provider_has_a_name_a_person_can_read():
    from app.services.team_keys import PROVIDER_LABELS, SUPPORTED_PROVIDERS

    assert set(PROVIDER_LABELS) == set(SUPPORTED_PROVIDERS)
    for label in PROVIDER_LABELS.values():
        assert label and label == label.strip()


def test_normalize_forgives_typing_and_refuses_everything_else():
    from app.services.team_keys import normalize_provider

    assert normalize_provider("  OpenAI ") == "openai"
    assert normalize_provider("XAI") == "xai"
    for junk in [None, "", "   ", "gemini", "anthropic; drop table teams", "openai "*4]:
        assert normalize_provider(junk) is None


def test_downgrade_undoes_exactly_what_upgrade_applied(monkeypatch):
    module = _migration_module()
    added = _record(monkeypatch, module.upgrade)
    dropped = _record(monkeypatch, module.downgrade)
    assert len(added) == len(dropped)
    assert any("DROP COLUMN IF EXISTS agent_provider" in s for s in dropped)
    assert any("DROP CONSTRAINT IF EXISTS" in s for s in dropped)


def test_both_directions_are_idempotent(monkeypatch):
    module = _migration_module()
    for statement in _record(monkeypatch, module.upgrade):
        upper = statement.upper()
        # ADD CONSTRAINT has no IF NOT EXISTS in Postgres; the catalogue guard is
        # the same property expressed the only way the grammar allows.
        assert "IF NOT EXISTS" in upper, statement
    for statement in _record(monkeypatch, module.downgrade):
        assert "IF EXISTS" in statement.upper(), statement


def test_the_chain_has_one_head_and_0033_is_on_it():
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
async def test_a_team_created_without_choosing_gets_anthropic(session):
    row = (await session.execute(sa.text(
        "INSERT INTO teams (id, slug, display_name, visibility) "
        "VALUES (gen_random_uuid(), :slug, 'Default Provider', 'closed') "
        "RETURNING agent_provider"
    ), {"slug": "mig0033-default"})).scalar_one()
    assert row == "anthropic", (
        "a team that never chose must keep today's behaviour exactly"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgres_refuses_a_provider_nobody_implemented(session):
    with pytest.raises(Exception) as caught:
        await session.execute(sa.text(
            "INSERT INTO teams (id, slug, display_name, visibility, agent_provider) "
            "VALUES (gen_random_uuid(), :slug, 'Bad Provider', 'closed', 'gemini')"
        ), {"slug": "mig0033-bad"})
    assert "teams_agent_provider_check" in str(caught.value)
