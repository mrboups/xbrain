"""Migration 0030 — the profile columns apply, and they come back off cleanly.

Two layers, because they fail differently.

**Without Docker** (always runs): the migration module is imported with
`alembic.op.execute` replaced by a recorder, so `upgrade()` and `downgrade()` can
be compared statement for statement. That catches the mistake this kind of
additive migration actually makes — a column added and then forgotten in
`downgrade()`, which leaves a rollback silently half-applied. It also pins the
revision chain: a second head is a deploy that stops with an error nobody can
read at 2am.

**With Docker** (skipped otherwise): the real thing, against a real Postgres, on
a database of its own. `upgrade → downgrade → upgrade` must land the schema back
where it started. It runs on a SEPARATE database created for the test rather than
the session-wide one every other integration test shares, because a downgrade on
the shared schema would drop columns out from under whatever runs next.
"""
from __future__ import annotations

import os

import pytest
import sqlalchemy as sa

MIGRATION_REVISION = "0030_user_profile"
PARENT_REVISION = "0029_push_subscriptions"

# The four columns this migration owns. Anything added to `upgrade()` and not to
# this list fails the symmetry test below.
PROFILE_COLUMNS = [
    "preferred_name",
    "bio",
    "avatar_media_id",
    "avatar_media_team_scope",
]


def _migration_module():
    """Import alembic/versions/0030_user_profile.py by path (it is not a package)."""
    import importlib.util

    path = os.path.join(
        os.path.dirname(__file__), "..", "alembic", "versions", "0030_user_profile.py"
    )
    spec = importlib.util.spec_from_file_location("_mig_0030", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _record(monkeypatch, fn) -> list[str]:
    """Run an upgrade()/downgrade() with op.execute captured instead of executed."""
    statements: list[str] = []
    monkeypatch.setattr("alembic.op.execute", lambda sql: statements.append(str(sql)))
    fn()
    return statements


# ── Container-free: shape, symmetry, chain ──────────────────────────────────


def test_upgrade_adds_exactly_the_four_profile_columns(monkeypatch):
    statements = _record(monkeypatch, _migration_module().upgrade)
    assert len(statements) == len(PROFILE_COLUMNS)
    for column in PROFILE_COLUMNS:
        assert any(
            "add column" in s.lower() and column in s for s in statements
        ), f"upgrade() never adds users.{column}"


def test_downgrade_drops_everything_upgrade_added_and_nothing_else(monkeypatch):
    """The failure this catches: a fifth column added to upgrade() alone.

    A rollback that leaves one column behind is worse than one that fails —
    the next `upgrade` hits an already-existing column and the operator has to
    reconstruct by hand which half applied.
    """
    module = _migration_module()
    added = _record(monkeypatch, module.upgrade)
    dropped = _record(monkeypatch, module.downgrade)

    def _columns(statements, verb):
        return {c for c in PROFILE_COLUMNS if any(verb in s.lower() and c in s for s in statements)}

    assert _columns(added, "add column") == _columns(dropped, "drop column")
    assert len(dropped) == len(added), (
        f"upgrade() runs {len(added)} statements and downgrade() {len(dropped)} — "
        f"a rollback must undo exactly what was applied"
    )


def test_both_directions_are_idempotent_on_a_partially_applied_database(monkeypatch):
    """IF [NOT] EXISTS on every statement.

    An environment where the migration was interrupted, or where a column was
    added by hand during an incident, must still converge instead of erroring.
    """
    module = _migration_module()
    for statement in _record(monkeypatch, module.upgrade):
        assert "IF NOT EXISTS" in statement.upper(), statement
    for statement in _record(monkeypatch, module.downgrade):
        assert "IF EXISTS" in statement.upper(), statement


def test_the_migration_only_touches_the_users_table(monkeypatch):
    module = _migration_module()
    for statement in _record(monkeypatch, module.upgrade) + _record(
        monkeypatch, module.downgrade
    ):
        assert "ALTER TABLE users" in statement, (
            f"0030 is the user-profile migration; it must not reach another "
            f"table: {statement!r}"
        )


def test_the_provider_display_name_column_is_never_touched(monkeypatch):
    """`display_name` is the identity provider's value. The migration is additive.

    Rewriting or dropping it is what would make a rename irreversible — the whole
    reason `preferred_name` is a separate column.
    """
    module = _migration_module()
    for statement in _record(monkeypatch, module.upgrade) + _record(
        monkeypatch, module.downgrade
    ):
        assert "display_name" not in statement, statement


def test_the_revision_chain_has_one_head_and_0030_is_it():
    """A branched migration tree is a deploy that stops with an unreadable error."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "alembic"))
    script = ScriptDirectory.from_config(cfg)

    heads = list(script.get_heads())
    assert heads == [MIGRATION_REVISION], f"expected a single head at 0030, got {heads}"
    assert script.get_revision(MIGRATION_REVISION).down_revision == PARENT_REVISION


def test_the_orm_model_and_the_migration_agree_on_the_columns():
    """A column in the model but not the migration is a 500 on a fresh install."""
    from app.models.user import User

    for column in PROFILE_COLUMNS:
        assert column in User.__table__.c, f"users.{column} missing from the ORM model"
        assert User.__table__.c[column].nullable, (
            f"users.{column} must stay nullable — every existing row is a valid "
            f"profile the moment this migration runs, with no backfill"
        )


# ── Docker-gated: the real upgrade and the real downgrade ───────────────────

pytestmark_integration = pytest.mark.integration


@pytest.mark.integration
@pytest.mark.asyncio
async def test_0030_applies_and_downgrades_cleanly_on_a_real_database(pg_url):
    """upgrade → downgrade → upgrade, on a database created for this test alone.

    Not the session-wide one: a downgrade there would drop `preferred_name` out
    from under every test that runs afterwards.
    """
    import asyncio

    from alembic import command
    from alembic.config import Config
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.config as app_config

    db_name = "mig_0030_roundtrip"
    admin = create_async_engine(pg_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{db_name}"'))
            await conn.execute(sa.text(f'CREATE DATABASE "{db_name}"'))
    finally:
        await admin.dispose()

    target_url = pg_url.rsplit("/", 1)[0] + "/" + db_name
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "alembic"))
    cfg.set_main_option("sqlalchemy.url", target_url)

    # alembic/env.py reads settings.DATABASE_URL and overwrites the config URL, so
    # the singleton is what actually decides which database is migrated.
    original_url = app_config.settings.DATABASE_URL
    app_config.settings.DATABASE_URL = target_url

    engine = create_async_engine(target_url)

    async def columns_present() -> set[str]:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    sa.text(
                        """
                        SELECT column_name FROM information_schema.columns
                        WHERE table_schema = 'public' AND table_name = 'users'
                          AND column_name = ANY(:cols)
                        """
                    ),
                    {"cols": PROFILE_COLUMNS},
                )
            ).fetchall()
        return {r.column_name for r in rows}

    try:
        # 1. Up to head: all four columns exist, all nullable.
        await asyncio.to_thread(command.upgrade, cfg, "head")
        assert await columns_present() == set(PROFILE_COLUMNS)

        async with engine.connect() as conn:
            head = (
                await conn.execute(sa.text("SELECT version_num FROM alembic_version"))
            ).scalar_one()
            assert head == MIGRATION_REVISION
            # An existing row is a valid profile with no backfill: insert one with
            # none of the new columns and read the NULLs back.
            await conn.execute(
                sa.text(
                    "INSERT INTO users (id, source_user_id, email) "
                    "VALUES (gen_random_uuid(), :sid, :email)"
                ),
                {"sid": "test:0030:smoke", "email": "smoke-0030@x.io"},
            )
            await conn.commit()
            row = (
                await conn.execute(
                    sa.text(
                        "SELECT preferred_name, bio, avatar_media_id, "
                        "avatar_media_team_scope FROM users WHERE source_user_id = :sid"
                    ),
                    {"sid": "test:0030:smoke"},
                )
            ).first()
            assert row is not None and all(v is None for v in row)

        # 2. Down one: every column this migration owns is gone, and the table
        #    (with its pre-0030 rows) survives.
        await asyncio.to_thread(command.downgrade, cfg, PARENT_REVISION)
        assert await columns_present() == set()

        async with engine.connect() as conn:
            head = (
                await conn.execute(sa.text("SELECT version_num FROM alembic_version"))
            ).scalar_one()
            assert head == PARENT_REVISION
            survivors = (
                await conn.execute(
                    sa.text("SELECT count(*) FROM users WHERE source_user_id = :sid"),
                    {"sid": "test:0030:smoke"},
                )
            ).scalar_one()
            assert survivors == 1, (
                "the downgrade must drop the profile columns, not the accounts"
            )

        # 3. Back up: re-applying after a rollback is not a special case.
        await asyncio.to_thread(command.upgrade, cfg, "head")
        assert await columns_present() == set(PROFILE_COLUMNS)
    finally:
        app_config.settings.DATABASE_URL = original_url
        await engine.dispose()
        admin = create_async_engine(pg_url, isolation_level="AUTOCOMMIT")
        try:
            async with admin.connect() as conn:
                await conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        finally:
            await admin.dispose()
