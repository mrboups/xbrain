"""Phase 21 — THE definitive per-team summon gate (ALIAS-01, D-21-03).

This is the "gate lesson" made executable: a per-team alias that *should* summon
the agent proves nothing until a real message with that alias actually traverses
the REAL `team_chat` POST path against a REAL Postgres and reaches the agent
handler. A mocked detector or an in-memory assertion is worthless here.

Two groups, both `@pytest.mark.integration` (Docker-gated):

  1. `test_summon_*` — a real POST to `/v1/teams/{id}/messages` runs the REAL
     `mention_detector.effective_aliases` + `detect` resolution (team_chat.py:246-247)
     against a real testcontainer Postgres. The ONLY things stubbed are the three
     downstream fire-and-forget network callers:
       * `team_chat_agent.handle_claude_mention`  → replaced by a RECORDER (records
         WHICH team was summoned; never runs the real handler / never calls Anthropic).
       * `centrifugo_client.publish`              → inert async no-op.
       * `brain_ingest.ingest_team_message`       → inert async no-op.
     The mention DECISION (effective_aliases + detect) is NEVER mocked — that is
     precisely what this gate exists to prove (T-21-03-01).

  2. `test_migration_*` — migration 0025 applies forward-only under EDITION=oss AND
     EDITION=saas, leaving a NULLABLE `teams.agent_aliases` column under both. Reuses
     the fresh-container-per-edition + config-singleton-patch pattern from
     test_migration_editions.py (os.environ alone is INERT: `settings` is frozen at
     import and alembic/env.py reads that singleton). No `downgrade()` is ever called.

SKIP=FAIL discipline (T-21-03-03): the `integration` marker lets CI's skip-grep capture
this file. A clean SKIP is legitimate ONLY when Docker is genuinely absent; when Docker
is present (CI) a SKIP is a FAILURE signal, not a pass — this file MUST run green.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# ── Principal override helpers (mirror test_admin_brain.py / test_agent_aliases_api.py) ──


def _install_principal(user, *, kind: str = "user") -> None:
    import types

    from app.deps import get_current_principal
    from app.main import app

    fake_user = types.SimpleNamespace(
        id=user.id,
        source_user_id=getattr(user, "source_user_id", None),
        email=getattr(user, "email", None),
        display_name=getattr(user, "display_name", None),
        github_username=getattr(user, "github_username", None),
        github_id=getattr(user, "github_id", None),
    )

    async def _override():
        return {
            "kind": kind,
            "user": fake_user,
            "sub": fake_user.source_user_id,
            "github_is_org_member": None,
        }

    app.dependency_overrides[get_current_principal] = _override


def _clear_principal() -> None:
    from app.deps import get_current_principal
    from app.main import app

    app.dependency_overrides.pop(get_current_principal, None)


# ── Group 1: the real POST → detect → enqueue summon gate ─────────────────────


async def test_summon_per_team_gate(client, seeded_two_teams, session, monkeypatch):
    """Real Postgres, real detection: custom alias summons ITS team only; @agent
    summons every team; @claude summons no team.

    Deterministic signal: whether `team_chat_agent.handle_claude_mention` is
    scheduled (recorded). The three fire-and-forget network callers are stubbed;
    the mention DECISION is executed for real against the DB-resolved alias list.
    """
    from app.repos import teams as teams_repo

    alice = seeded_two_teams["alice"]  # admin+member of team-a
    bob = seeded_two_teams["bob"]      # admin+member of team-b
    team_a = seeded_two_teams["team_a"]
    team_b = seeded_two_teams["team_b"]

    # RECORDER replaces the real agent handler — records the summoned team_id and
    # returns immediately (no Anthropic call, no network). team_chat.py invokes it
    # via the module attribute, so patching the attribute intercepts the call.
    summoned: list[Any] = []

    async def _recorder(**kwargs):
        summoned.append(kwargs["team_id"])

    async def _noop_ingest(**kwargs):
        return None

    async def _noop_publish(**kwargs):
        return None

    monkeypatch.setattr("app.services.team_chat_agent.handle_claude_mention", _recorder)
    monkeypatch.setattr("app.services.brain_ingest.ingest_team_message", _noop_ingest)
    monkeypatch.setattr("app.services.centrifugo_client.publish", _noop_publish)

    # team-a sets a CUSTOM alias "wizard"; team-b sets NOTHING (defaults only).
    await teams_repo.set_agent_aliases(session, team_id=team_a.id, aliases_csv="wizard")
    await session.commit()

    async def _post_as(user, team_id, content) -> Any:
        """POST as `user`, then flush the loop so the fire-and-forget create_task
        (the recorder) runs before we assert. Returns the httpx response."""
        summoned.clear()
        _install_principal(user)
        try:
            r = await client.post(
                f"/v1/teams/{team_id}/messages", json={"content": content}
            )
        finally:
            _clear_principal()
        # The handler schedules the recorder via asyncio.create_task and returns
        # without awaiting it; yielding to the loop lets that task run one tick.
        for _ in range(5):
            await asyncio.sleep(0)
        return r

    # Case A (SC#1) — alice: "@wizard" in team-a → summons team-a (custom alias, own team).
    r = await _post_as(alice, team_a.id, "@wizard summarize")
    assert r.status_code == 201, r.text
    assert summoned == [team_a.id], (
        f"team-a's custom alias @wizard must summon team-a, got {summoned!r}"
    )

    # Case B (SC#1) — bob: "@wizard" in team-b → does NOT summon (team-b never set it).
    r = await _post_as(bob, team_b.id, "@wizard summarize")
    assert r.status_code == 201, r.text
    assert summoned == [], (
        f"@wizard must NOT summon team-b (it never set that alias), got {summoned!r}"
    )

    # Case C (SC#2) — "@agent" is the universal default → summons BOTH teams,
    # including team-b which has NO custom alias configured.
    r = await _post_as(alice, team_a.id, "@agent hi")
    assert r.status_code == 201, r.text
    assert summoned == [team_a.id], f"@agent must summon team-a, got {summoned!r}"

    r = await _post_as(bob, team_b.id, "@agent hi")
    assert r.status_code == 201, r.text
    assert summoned == [team_b.id], (
        f"@agent must summon team-b (no custom alias), got {summoned!r}"
    )

    # Case D (SC#2) — "@claude" is reserved and summons NO team, ever.
    r = await _post_as(alice, team_a.id, "@claude hi")
    assert r.status_code == 201, r.text
    assert summoned == [], f"@claude must NOT summon team-a, got {summoned!r}"

    r = await _post_as(bob, team_b.id, "@claude hi")
    assert r.status_code == 201, r.text
    assert summoned == [], f"@claude must NOT summon team-b, got {summoned!r}"


# ── Group 2: migration 0025 forward-only + edition-agnostic column proof ──────
#
# Self-contained mirror of test_migration_editions.py's PATTERN: a FRESH Postgres
# container per edition, the config singleton patched DIRECTLY (os.environ is inert
# because `settings` is frozen at import and alembic/env.py reads that singleton),
# `command.upgrade(cfg, "head")` in a worker thread (env.py calls asyncio.run
# internally). No downgrade() anywhere — this is forward-only.

_HERE = Path(__file__).resolve().parent
_ALEMBIC_INI = _HERE / ".." / "alembic.ini"
_SCRIPT_LOCATION = _HERE / ".." / "alembic"

# D-17-05 / D-21-03: the release matrix is exactly the two editions config.py
# permits — there is NO `pro` tier. Do NOT add it here.
_EDITIONS = ["oss", "saas"]


def _docker_available() -> bool:
    """Mirror conftest.py::_docker_available — skip ONLY when Docker is truly absent."""
    try:
        import docker  # noqa: F401

        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


def _alembic_config(sqlalchemy_url: str):
    """Alembic Config with dirname-relative paths. env.py overwrites sqlalchemy.url
    from the patched settings.DATABASE_URL, so this URL is only a fallback."""
    from alembic.config import Config

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", sqlalchemy_url)
    cfg.set_main_option("script_location", str(_SCRIPT_LOCATION))
    return cfg


async def _upgrade_and_probe_agent_aliases(edition: str) -> tuple[str, str]:
    """Spin fresh PG, patch the config singleton to `edition`, upgrade head, then
    read teams.agent_aliases from information_schema. Returns (data_type, is_nullable).

    Restores the singleton + os.environ in `finally` so no other session test is
    polluted, and stops the container even on assert/raise (T-21-03-02).
    """
    from testcontainers.postgres import PostgresContainer

    import app.config as app_config

    prev_edition = app_config.settings.EDITION
    prev_db_url = app_config.settings.DATABASE_URL
    prev_env_edition = os.environ.get("EDITION")
    prev_env_db = os.environ.get("DATABASE_URL")

    # Migrations call gen_random_uuid() → pgcrypto must be preloaded (mirrors conftest).
    pg = PostgresContainer(
        "postgres:17", username="test", password="test", dbname="test"
    ).with_command("postgres -c shared_preload_libraries=pgcrypto")
    pg.start()
    try:
        raw = pg.get_connection_url()  # postgresql+psycopg2://test:test@host:port/test
        asyncpg_url = raw.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        plain_dsn = raw.replace("postgresql+psycopg2://", "postgresql://")

        # THE LOAD-BEARING PATCH: switch the singleton DIRECTLY, not just os.environ.
        os.environ["EDITION"] = edition
        os.environ["DATABASE_URL"] = asyncpg_url
        app_config.settings.EDITION = edition
        app_config.settings.DATABASE_URL = asyncpg_url

        cfg = _alembic_config(asyncpg_url)

        # env.py's run_migrations_online() calls asyncio.run() internally; a worker
        # thread gives it a clean, loop-free thread under pytest-asyncio's loop.
        from alembic import command

        await asyncio.to_thread(command.upgrade, cfg, "head")

        import asyncpg

        conn = await asyncpg.connect(dsn=plain_dsn)
        try:
            rows = await conn.fetch(
                """
                SELECT data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = 'teams' AND column_name = 'agent_aliases'
                """
            )
        finally:
            await conn.close()

        assert len(rows) == 1, (
            f"[{edition}] expected exactly ONE teams.agent_aliases column after "
            f"upgrade head, got {len(rows)} — migration 0025 did not create the "
            f"column under this edition (or the schema forked on EDITION)."
        )
        return rows[0]["data_type"], rows[0]["is_nullable"]
    finally:
        # Resource hygiene: never leak a container; never pollute other session tests.
        pg.stop()
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


@pytest.mark.parametrize("edition", _EDITIONS)
async def test_migration_0025_agent_aliases_forward_only(edition: str) -> None:
    """Forward-only, edition-agnostic: `alembic upgrade head` under `edition` leaves
    a NULLABLE teams.agent_aliases column (additive, no backfill). No downgrade()."""
    if not _docker_available():
        pytest.skip("Docker not available — skipping real-Postgres migration test")

    data_type, is_nullable = await _upgrade_and_probe_agent_aliases(edition)

    assert is_nullable == "YES", (
        f"[{edition}] teams.agent_aliases must be NULLABLE (additive column, D-21-03) "
        f"but is_nullable={is_nullable!r}."
    )
    # Belt-and-braces: it is the TEXT column 0025 declares.
    assert data_type in ("text", "character varying"), (
        f"[{edition}] unexpected data_type for teams.agent_aliases: {data_type!r} "
        f"(expected a text column)."
    )
