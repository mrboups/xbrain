"""REL-03 / SC4 + SC5 — forward-only, edition-agnostic migration proof.

This is the single most important release gate in Phase 17: a real-Postgres test
that runs `alembic upgrade head` under EDITION=oss AND EDITION=saas — the two
editions `app.config.Settings._validate_edition` permits (D-17-05 / EDIT-03; there
is NO `pro` tier, config.py raises ValueError for anything but {"oss", "saas"}) —
and asserts every edition reaches the SAME Alembic head with no error. That proves
two invariants at once:

  * forward-only  — `alembic upgrade head` reaches the declared script head; no
    `downgrade()` is ever invoked in release validation.
  * edition-agnostic — both editions produce the IDENTICAL applied head, so the
    schema does not fork on the EDITION flag.

Why this file exists instead of extending conftest.py::pg_url
-------------------------------------------------------------
conftest's `pg_url` is session-scoped and shared by 20+ files; it upgrades ONE
container ONCE. This test needs a FRESH, function-scoped container PER edition so
the edition can be switched *before* each upgrade — hence a self-contained helper.

The load-bearing detail (the exact bug this test is designed around)
-------------------------------------------------------------------
`app.config` builds `settings = Settings()` ONCE at import (config.py ~line 265),
and `alembic/env.py` both `from app.config import settings` AND re-reads
`settings.DATABASE_URL` at env-load time (env.py ~line 24), OVERRIDING any
`sqlalchemy.url` set on the Alembic `Config`. Therefore setting
`os.environ["EDITION"]` / `os.environ["DATABASE_URL"]` alone is INERT: the
singleton is already frozen, every "edition" would silently stay on the default
`oss` and migrate the wrong DB. The helper below patches the singleton DIRECTLY
(`app_config.settings.EDITION = edition`, `app_config.settings.DATABASE_URL = ...`),
mirroring the pattern conftest.py documents at lines ~82-96 for DATABASE_URL. A
future migration that branched on `settings.EDITION` would read that same patched
singleton, so this is a genuine per-edition run, not a mock or a config parse.

Docker gate
-----------
Skipped automatically when Docker is genuinely absent (same gate as conftest's
`pg_url`). NOTE: in CI Docker IS present, so a SKIP there is a real failure signal
(SKIP != PASS), not a green — Plan 17-03 wires this file as the `test-migrations`
job and treats a skip as a job failure.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# D-17-05 scopes the release matrix to the two editions config.py permits. There is
# NO `pro`: `Settings._validate_edition` allows only {"oss", "saas"} and raises
# ValueError otherwise (EDIT-03 dropped the pro tier on 2026-07-11). ROADMAP SC4's
# "or pro" wording is stale — do not encode it as an executable expectation.
EDITIONS = ["oss", "saas"]

_HERE = Path(__file__).resolve().parent
_ALEMBIC_INI = _HERE / ".." / "alembic.ini"
_SCRIPT_LOCATION = _HERE / ".." / "alembic"
_VERSIONS_DIR = _SCRIPT_LOCATION / "versions"


def _docker_available() -> bool:
    """Mirror conftest.py::_docker_available — skip only when Docker is truly absent."""
    try:
        import docker  # noqa: F401

        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


def _alembic_config(sqlalchemy_url: str) -> "object":
    """Build an Alembic Config with dirname-relative paths (never bare relatives).

    `env.py` overwrites `sqlalchemy.url` from the patched `settings.DATABASE_URL`,
    so the URL passed here is only a fallback — the singleton patch is what routes
    the upgrade at the DB the container exposes.
    """
    from alembic.config import Config

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", sqlalchemy_url)
    cfg.set_main_option("script_location", str(_SCRIPT_LOCATION))
    return cfg


def _script_head() -> str:
    """The Alembic chain's DECLARED head — read dynamically, never hardcoded.

    Reading it via ScriptDirectory means this test does not go brittle when a
    later plan appends `0025_*` (today the head is `0024_local_credentials`).
    """
    from alembic.script import ScriptDirectory

    cfg = _alembic_config("postgresql+asyncpg://unused:unused@localhost/unused")
    return ScriptDirectory.from_config(cfg).get_current_head()


async def _run_upgrade_head(edition: str) -> str:
    """Spin a fresh Postgres, patch the config singleton to `edition`, upgrade head.

    Returns the head recorded in `alembic_version` after the upgrade — the REAL
    applied head, read straight from the DB the migrations wrote to.
    """
    from testcontainers.postgres import PostgresContainer

    # Migrations call gen_random_uuid() → pgcrypto must be preloaded (mirrors conftest).
    pg = PostgresContainer(
        "postgres:17", username="test", password="test", dbname="test"
    ).with_command("postgres -c shared_preload_libraries=pgcrypto")

    # Save the singleton state so this test never pollutes the EDITION/DATABASE_URL
    # that other session tests read off the shared config module.
    import app.config as app_config

    prev_edition = app_config.settings.EDITION
    prev_db_url = app_config.settings.DATABASE_URL
    prev_env_edition = os.environ.get("EDITION")
    prev_env_db = os.environ.get("DATABASE_URL")

    pg.start()
    try:
        raw = pg.get_connection_url()  # postgresql+psycopg2://test:test@host:port/test
        asyncpg_url = raw.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        plain_dsn = raw.replace("postgresql+psycopg2://", "postgresql://")

        # --- THE LOAD-BEARING PATCH (mirror conftest.py ~82-96) ---
        # Patch the module singleton DIRECTLY. os.environ is set too for completeness,
        # but the singleton assignment is the one that actually switches edition + DB,
        # because `settings = Settings()` was already built once at import and env.py
        # reads THAT object, not os.environ.
        os.environ["EDITION"] = edition
        os.environ["DATABASE_URL"] = asyncpg_url
        app_config.settings.EDITION = edition
        app_config.settings.DATABASE_URL = asyncpg_url

        cfg = _alembic_config(asyncpg_url)

        # env.py's run_migrations_online() calls asyncio.run() internally; calling
        # command.upgrade inline under pytest-asyncio's running loop would raise
        # "asyncio.run() cannot be called from a running event loop". A worker thread
        # gives it a clean, loop-free thread — exactly what conftest.py does.
        from alembic import command

        await asyncio.to_thread(command.upgrade, cfg, "head")

        # Read the head the migrations actually stamped, from the DB itself.
        import asyncpg

        conn = await asyncpg.connect(dsn=plain_dsn)
        try:
            applied_head = await conn.fetchval("SELECT version_num FROM alembic_version")
        finally:
            await conn.close()

        assert applied_head, (
            f"[{edition}] alembic_version is empty after upgrade head — no migration "
            f"stamped a version; the upgrade did not run against this container."
        )
        return applied_head
    finally:
        # Resource hygiene (T-17-01-01): never leak a container, even on assert/raise.
        pg.stop()
        # Restore the singleton so downstream session tests see the original edition/DB.
        app_config.settings.EDITION = prev_edition
        app_config.settings.DATABASE_URL = prev_db_url
        if prev_env_edition is None:
            os.environ.pop("EDITION", None)
        else:
            os.environ["EDITION"] = prev_env_edition
        if prev_env_db is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prev_env_db


@pytest.mark.parametrize("edition", EDITIONS)
async def test_alembic_upgrade_head_per_edition(edition: str) -> None:
    """Forward-only: `alembic upgrade head` under `edition` reaches the DECLARED head.

    Runs the real Alembic chain against a real Postgres 17 container with the config
    singleton switched to `edition`, then asserts the applied DB head equals the
    ScriptDirectory head. No `downgrade()` is ever called — this is upgrade-only.
    """
    if not _docker_available():
        pytest.skip("Docker not available — skipping real-Postgres migration test")

    applied_head = await _run_upgrade_head(edition)
    declared_head = _script_head()
    assert applied_head == declared_head, (
        f"[{edition}] upgrade head reached {applied_head!r}, but the Alembic chain's "
        f"declared head is {declared_head!r}. The upgrade did not reach head — the "
        f"migration path is broken for this edition."
    )


async def test_all_editions_reach_same_head() -> None:
    """Edition-agnostic: oss and saas upgrade to the IDENTICAL head (schema does not fork)."""
    if not _docker_available():
        pytest.skip("Docker not available — skipping real-Postgres migration test")

    heads = {edition: await _run_upgrade_head(edition) for edition in EDITIONS}
    distinct = set(heads.values())
    assert len(distinct) == 1, (
        f"Editions diverged on migration head: {heads}. A released migration must "
        f"reach the same head for every edition (edition-agnostic schema). One edition "
        f"applied a different final revision than another."
    )
    # And that single shared head must be the declared chain head — belt and braces.
    declared_head = _script_head()
    assert distinct == {declared_head}, (
        f"Editions agreed on {distinct} but the declared script head is {declared_head!r}."
    )


def test_no_migration_branches_on_edition() -> None:
    """Static forward-only guard: NO migration references the EDITION flag.

    Cheap (no container). Today `grep -rl EDITION alembic/versions` returns nothing —
    edition-agnosticism is true by omission. This turns "true by omission" into
    "asserted", catching a FUTURE migration that silently branches its schema on
    `settings.EDITION`. If this fails, make the offending migration edition-agnostic
    (create every column/table/index unconditionally, regardless of edition).
    """
    offenders: list[str] = []
    for path in sorted(_VERSIONS_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        source = path.read_text(encoding="utf-8")
        if "EDITION" in source:
            offenders.append(path.name)
    assert not offenders, (
        "These migrations reference the EDITION flag and could fork the schema per "
        f"edition: {offenders}. Migrations MUST be edition-agnostic — a released "
        "schema is identical under oss and saas. Remove the edition branch."
    )
