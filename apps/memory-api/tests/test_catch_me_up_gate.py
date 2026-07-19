"""Phase 23 — THE definitive Catch-me-up gate (CATCHUP-01, D-23-01..05).

The "gate lesson" made executable: a "catch-me-up works" claim proves nothing
until a real POST traverses the REAL `/mark-read` -> `/unread-summary` ->
`/catch-me-up` path against a REAL Postgres testcontainer. Here, the ONLY things
stubbed are the terminal network callers — the agent's token stream
(`_stream_via_anthropic_api`, replaced by a RECORDER that captures the gathered
since-window), the bridge probe (`_user_has_live_bridge` -> False, forcing the
anthropic path), and the fire-and-forget `centrifugo_client.publish` (a recorder).
The read cursor, the `after_created_at` gather, the caller's-own filter, the
unread count, the membership gate and team_scope isolation ALL run for real — that
is precisely what this gate exists to prove.

CRITICAL — cross-connection visibility (the prior attempt's finding):
`team_chat_agent.catch_me_up` opens its OWN `async_session_factory()` session (a
separate pooled connection). Under the rollback-transaction `session` fixture, data
"committed" by the request session is only a SAVEPOINT release inside the still-open
outer transaction — INVISIBLE to any other connection. So the team, members and
messages the background task must gather are seeded + COMMITTED against the real
container via a dedicated `async_session_factory()` (host-clock `created_at` values,
not container `now()`), and cleaned up in `finally`. The request path (via the
`client` fixture's rollback session) still sees those rows through READ COMMITTED.
Only the terminal streaming/publish is stubbed; the gather is never mocked.

Two groups, both `@pytest.mark.integration` (Docker-gated):

  1. `test_catch_me_up_gate` — the real cursor / since-window / count / 403 /
     team_scope-isolation / ephemerality gate against a real Postgres.
  2. `test_migration_0026_last_read_forward_only` — migration 0026 applies
     forward-only under EDITION=oss AND EDITION=saas, leaving a NULLABLE
     `team_members.last_read_at` TIMESTAMPTZ under both. Fresh container per edition,
     config singleton patched directly (os.environ alone is inert). No reverse
     migration is ever invoked.

SKIP=FAIL discipline: the `integration` marker lets CI's skip-grep capture this
file. A clean SKIP is legitimate ONLY when Docker is genuinely absent; under Docker
this file MUST run green.
"""
from __future__ import annotations

import asyncio
import os
import time
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# ── Principal override helpers (copied from test_nudge_open_gate.py) ───────────


def _install_principal(user, *, kind: str = "user") -> None:
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


# ── Group 1: the real cursor / since-window / isolation / 403 gate ────────────


async def test_catch_me_up_gate(client, monkeypatch):
    """Real Postgres, real gather, stubbed stream: mark-read moves the caller's
    cursor; the unread count excludes own + agent; a non-member is 403; catch-me-up
    gathers EXACTLY the since-window and no other team's messages; and NO
    team_messages row is persisted (streamed only to the caller's own channel).

    Session model: `catch_me_up` opens its OWN pooled connection and can only see
    COMMITTED data, so every fixture row here is committed to the real container and
    torn down in `finally`. The `client` fixture's default `get_session` override is
    a rollback transaction whose commits are savepoint-only (invisible cross-
    connection) AND which would hold a row-lock on the cursor row that blocks
    cleanup — so we re-override `get_session` with a per-request COMMITTING session:
    the real routes still run end-to-end against the real DB, each request commits
    and releases immediately, and there is no cross-connection blind spot."""
    from sqlalchemy import func, select, text

    import app.db.session as db_session
    from app.deps import get_session
    from app.main import app
    from app.models.team_message import TeamMessage
    from app.repos import team_messages as tm_repo
    from app.repos import teams as teams_repo
    from app.repos import users as users_repo
    from app.services import rate_limit

    # Real routes, but a per-request COMMITTING session (see docstring): each request
    # writes straight through to the container and releases its locks on close, so the
    # background task's own session sees the same committed state the request wrote.
    async def _committing_get_session():
        async with db_session.async_session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = _committing_get_session

    # Clean per-caller rate-limit buckets so an earlier test can't 429 us.
    rate_limit._storage.reset()

    anchor = datetime.now(timezone.utc)
    phase_a_ts = anchor - timedelta(hours=1)

    # ── COMMITTED seed (the background task's own session must see this) ───────
    # Distinct slugs / subs so we never collide with other fixtures' team-a/team-b.
    async with db_session.async_session_factory() as seed:
        alice = await users_repo.get_or_create_user(
            seed, source_user_id="cmu-alice-sub", email="cmu-alice@test.local",
            display_name="Alice",
        )
        bob = await users_repo.get_or_create_user(
            seed, source_user_id="cmu-bob-sub", email="cmu-bob@test.local",
            display_name="Bob",
        )
        carol = await users_repo.get_or_create_user(
            seed, source_user_id="cmu-carol-sub", email="cmu-carol@test.local",
            display_name="Carol",
        )
        team_a = await teams_repo.create_team(
            seed, slug="cmu-team-a", display_name="CMU Team A",
            creator_user_id=alice.id,   # alice -> admin+member of team-a
        )
        team_b = await teams_repo.create_team(
            seed, slug="cmu-team-b", display_name="CMU Team B",
            creator_user_id=bob.id,     # bob -> admin+member of team-b ONLY
        )
        await teams_repo.add_member(
            seed, team_id=team_a.id, user_id=carol.id, role="member",
        )
        # Phase A (BEFORE the cursor): carol's user messages in team-a.
        a1 = await tm_repo.insert_user_message(
            seed, team_id=team_a.id, author_user_id=carol.id, content="PHASE_A_1 old",
        )
        a2 = await tm_repo.insert_user_message(
            seed, team_id=team_a.id, author_user_id=carol.id, content="PHASE_A_2 old",
        )
        # Team-b isolation decoys (must NEVER reach team-a's since-window).
        d1 = await tm_repo.insert_user_message(
            seed, team_id=team_b.id, author_user_id=bob.id, content="TEAMB_1 secret",
        )
        d2 = await tm_repo.insert_user_message(
            seed, team_id=team_b.id, author_user_id=bob.id, content="TEAMB_2 secret",
        )
        # Pin Phase-A + decoys to a fixed PAST host-clock instant (override the DDL
        # server_default=now(), which is the CONTAINER clock — keep every timestamp
        # on the host clock so it is directly comparable to the Python-set cursor).
        for m in (a1, a2, d1, d2):
            m.created_at = phase_a_ts
        await seed.commit()

        alice_id, alice_sub = alice.id, alice.source_user_id
        bob_id, bob_sub = bob.id, bob.source_user_id
        carol_id = carol.id
        team_a_id, team_a_slug = team_a.id, team_a.slug
        team_b_id = team_b.id

    alice_p = types.SimpleNamespace(
        id=alice_id, source_user_id=alice_sub, email="cmu-alice@test.local",
        display_name="Alice", github_username=None, github_id=None,
    )
    bob_p = types.SimpleNamespace(
        id=bob_id, source_user_id=bob_sub, email="cmu-bob@test.local",
        display_name="Bob", github_username=None, github_id=None,
    )

    try:
        # ── RECORDERS — the ONLY stubs. Gather/cursor/count run for real. ─────
        captured: dict[str, object] = {}
        stream_calls: list[str] = []

        async def _fake_stream(*, system_prompt, cached_memory_block, chat_history_block):
            # Capture the gathered since-window (chat_history_block) so the test can
            # assert EXACTLY which messages the real gather handed the model.
            stream_calls.append(chat_history_block)
            captured["history"] = chat_history_block
            captured["system"] = system_prompt
            yield ("catch-up briefing text", {})

        async def _no_bridge(user_sub):  # force the anthropic (stubbed) path
            return False

        published: list[tuple[str, dict]] = []

        async def _pub_recorder(channel, data):
            published.append((channel, data))
            return True

        monkeypatch.setattr(
            "app.services.team_chat_agent._stream_via_anthropic_api", _fake_stream,
        )
        monkeypatch.setattr(
            "app.services.team_chat_agent._user_has_live_bridge", _no_bridge,
        )
        monkeypatch.setattr("app.services.centrifugo_client.publish", _pub_recorder)
        # Plumbing (NOT a logic stub): ensure the background task's own
        # async_session_factory() hits the SAME testcontainer the fixtures configured
        # (the module bound the name at import; repoint it to the live factory).
        monkeypatch.setattr(
            "app.services.team_chat_agent.async_session_factory",
            db_session.async_session_factory,
        )

        async def _as(user_p, method: str, path: str):
            _install_principal(user_p)
            try:
                if method == "POST":
                    return await client.post(path)
                return await client.get(path)
            finally:
                _clear_principal()

        async def _wait_until(pred, timeout: float = 8.0) -> bool:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if pred():
                    return True
                await asyncio.sleep(0.02)
            return False

        async def _count_team_a_rows() -> int:
            async with db_session.async_session_factory() as c:
                n = (
                    await c.execute(
                        select(func.count())
                        .select_from(TeamMessage)
                        .where(TeamMessage.team_id == team_a_id)
                    )
                ).scalar_one()
            return int(n)

        async def _read_cursor():
            async with db_session.async_session_factory() as c:
                m = await teams_repo.get_membership(
                    c, user_id=alice_id, team_slug=team_a_slug,
                )
                return m.last_read_at if m else None

        # ── mark-read #1: the real endpoint sets the caller's cursor to now() ─
        r = await _as(alice_p, "POST", f"/v1/teams/{team_a_id}/mark-read")
        assert r.status_code == 200, r.text
        assert r.json() == {"status": "ok"}

        # A small margin so Phase B strictly follows the cursor on the host clock.
        await asyncio.sleep(0.05)

        # Read the committed cursor back through the REAL membership row.
        cursor1 = await _read_cursor()

        # ── Phase B (AFTER the cursor): host-clock created_at, committed. ────
        # 2 from carol, 1 from ALICE herself (exclude-own), 1 agent frame (exclude-agent).
        async with db_session.async_session_factory() as seed2:
            pb1 = await tm_repo.insert_user_message(
                seed2, team_id=team_a_id, author_user_id=carol_id,
                content="PHASE_B_1 decision",
            )
            pb2 = await tm_repo.insert_user_message(
                seed2, team_id=team_a_id, author_user_id=carol_id,
                content="PHASE_B_2 question for you",
            )
            pba = await tm_repo.insert_user_message(
                seed2, team_id=team_a_id, author_user_id=alice_id,
                content="PHASE_B_ALICE_OWN mine",
            )
            pag = await tm_repo.insert_agent_message(
                seed2, team_id=team_a_id, agent_name="claude-sonnet-4-6",
                content="PHASE_B_AGENT frame", routed_via="team_api",
            )
            phase_b_ts = datetime.now(timezone.utc)
            for m in (pb1, pb2, pba, pag):
                m.created_at = phase_b_ts
            await seed2.commit()

        # ── Assertion 1 (SC#1): mark-read moved the cursor, strictly between the
        #    Phase-A and Phase-B timestamps. ───────────────────────────────────
        assert cursor1 is not None, "mark-read must set last_read_at"
        assert phase_a_ts < cursor1 < phase_b_ts, (
            f"cursor {cursor1} must sit between Phase-A {phase_a_ts} and "
            f"Phase-B {phase_b_ts}"
        )

        # ── Assertion 2 (SC#1): unread count = only carol's 2 post-cursor USER
        #    messages (NOT alice's own, NOT the agent frame). ──────────────────
        r = await _as(alice_p, "GET", f"/v1/teams/{team_a_id}/unread-summary")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["count"] == 2, f"unread must exclude own + agent, got {body!r}"
        assert body["since"] == cursor1.isoformat(), body
        assert body["threshold"] == 10, body  # settings.CATCHUP_UNREAD_THRESHOLD

        # ── Assertion 3 (SC#2, T-23-01): a non-member (bob) is 403 on ALL three
        #    endpoints, and starts NO summary. ──────────────────────────────────
        r = await _as(bob_p, "POST", f"/v1/teams/{team_a_id}/mark-read")
        assert r.status_code == 403, r.text
        r = await _as(bob_p, "GET", f"/v1/teams/{team_a_id}/unread-summary")
        assert r.status_code == 403, r.text
        r = await _as(bob_p, "POST", f"/v1/teams/{team_a_id}/catch-me-up")
        assert r.status_code == 403, r.text
        # Give any (illegitimate) background task a chance to run — there must be none.
        for _ in range(5):
            await asyncio.sleep(0)
        assert captured.get("history") is None, "a non-member must start no summary"
        assert published == [], "a 403 catch-me-up must publish nothing"

        # ── Assertion 4 (SC#2, T-23-02): catch-me-up gathers EXACTLY the
        #    since-window + team_scope isolation. ──────────────────────────────
        rows_before = await _count_team_a_rows()

        r = await _as(alice_p, "POST", f"/v1/teams/{team_a_id}/catch-me-up")
        assert r.status_code == 202, r.text
        assert r.json() == {"status": "accepted"}

        # Wait for the fire-and-forget background task (real DB gather) to finish —
        # its terminal publish is a `catchup_stream_end` frame.
        done = await _wait_until(
            lambda: any(d.get("type") == "catchup_stream_end" for _, d in published)
        )
        assert done, "catch_me_up background task did not complete in time"

        assert len(stream_calls) == 1, (
            f"the agent stream must be invoked exactly once, got {len(stream_calls)}"
        )
        history = captured.get("history")
        assert isinstance(history, str)
        # Gathered the post-cursor team-a window...
        assert "PHASE_B_1" in history and "PHASE_B_2" in history, history
        # ...and NONE of the other team's messages (team_scope isolation)...
        assert "TEAMB_1" not in history and "TEAMB_2" not in history, history
        # ...and NONE of the pre-cursor messages (strictly after the cursor).
        assert "PHASE_A_1" not in history and "PHASE_A_2" not in history, history
        # The caller's OWN message is filtered out of the gathered window (D-23-04).
        assert "PHASE_B_ALICE_OWN" not in history, history

        # ── Assertion 5 (SC#3, T-23-06): EPHEMERAL — no persisted row, and every
        #    publish went to the caller's PRIVATE channel, never the team channel. ─
        rows_after = await _count_team_a_rows()
        assert rows_after == rows_before, (
            f"catch-me-up must persist NO team_messages row "
            f"(before={rows_before}, after={rows_after})"
        )
        assert published, "the summary must have streamed at least one frame"
        assert all(ch == f"user:{alice_sub}" for ch, _ in published), (
            f"catch-me-up must stream ONLY to user:{alice_sub}, got "
            f"{[ch for ch, _ in published]}"
        )
        assert all(ch != f"team:{team_a_id}" for ch, _ in published), (
            "catch-me-up must NEVER publish to the shared team channel"
        )

        # ── Assertion 6 (T-23-03): empty-window no-op — mark read AGAIN (cursor
        #    advances past every message), then catch-me-up short-circuits with NO
        #    LLM call. ──────────────────────────────────────────────────────────
        await asyncio.sleep(0.05)
        r = await _as(alice_p, "POST", f"/v1/teams/{team_a_id}/mark-read")
        assert r.status_code == 200, r.text

        r = await _as(alice_p, "POST", f"/v1/teams/{team_a_id}/catch-me-up")
        assert r.status_code == 200, r.text
        assert r.json() == {"status": "nothing_to_summarize"}
        for _ in range(5):
            await asyncio.sleep(0)
        assert len(stream_calls) == 1, (
            "a 0-unread catch-me-up must make NO new LLM call "
            f"(stream invocations={len(stream_calls)})"
        )
    finally:
        # Resource hygiene: remove the COMMITTED rows so the session-scoped DB is
        # not polluted for other tests. Deleting the teams cascades to their
        # team_members + team_messages (FK ondelete=CASCADE); users are deleted last.
        async with db_session.async_session_factory() as cleaner:
            await cleaner.execute(
                text("DELETE FROM teams WHERE id = ANY(:ids)"),
                {"ids": [team_a_id, team_b_id]},
            )
            await cleaner.execute(
                text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": [alice_id, bob_id, carol_id]},
            )
            await cleaner.commit()


# ── Group 2: migration 0026 forward-only + edition-agnostic column proof ──────
#
# Self-contained mirror of test_agent_aliases_gate.py's Group 2 / test_migration_
# editions.py's PATTERN: a FRESH Postgres container per edition, the config
# singleton patched DIRECTLY (os.environ alone is inert — `settings` is frozen at
# import and alembic/env.py reads that singleton), `command.upgrade(cfg, "head")` in
# a worker thread (env.py calls asyncio.run internally). No reverse migration is ever
# invoked — this is upgrade-only.

_HERE = Path(__file__).resolve().parent
_ALEMBIC_INI = _HERE / ".." / "alembic.ini"
_SCRIPT_LOCATION = _HERE / ".." / "alembic"

# The release matrix is exactly the two editions config.py permits (D-17-05).
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


async def _upgrade_and_probe_last_read(edition: str) -> tuple[str, str]:
    """Spin fresh PG, patch the config singleton to `edition`, upgrade head, then
    read team_members.last_read_at from information_schema. Returns
    (data_type, is_nullable).

    Restores the singleton + os.environ in `finally` so no other session test is
    polluted, and stops the container even on assert/raise.
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
                WHERE table_name = 'team_members' AND column_name = 'last_read_at'
                """
            )
        finally:
            await conn.close()

        assert len(rows) == 1, (
            f"[{edition}] expected exactly ONE team_members.last_read_at column after "
            f"upgrade head, got {len(rows)} — migration 0026 did not create the column "
            f"under this edition (or the schema forked on EDITION)."
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
async def test_migration_0026_last_read_forward_only(edition: str) -> None:
    """Forward-only, edition-agnostic: `alembic upgrade head` under `edition` leaves
    a NULLABLE team_members.last_read_at TIMESTAMPTZ (additive, no backfill). No
    reverse migration is invoked."""
    if not _docker_available():
        pytest.skip("Docker not available — skipping real-Postgres migration test")

    data_type, is_nullable = await _upgrade_and_probe_last_read(edition)

    assert is_nullable == "YES", (
        f"[{edition}] team_members.last_read_at must be NULLABLE (additive column, "
        f"D-23-01) but is_nullable={is_nullable!r}."
    )
    assert data_type == "timestamp with time zone", (
        f"[{edition}] unexpected data_type for team_members.last_read_at: "
        f"{data_type!r} (0026 declares TIMESTAMPTZ)."
    )
