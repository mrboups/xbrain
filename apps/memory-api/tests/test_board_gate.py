"""Phase 26a — THE non-mocked collaborative-board gate (BOARD-01, D-26-02..04).

A board token is a BEARER SECRET to a team-scoped live document, and the Y.Doc bytes
are the team's actual work — so "it works" is not enough. Every security-bearing path
is PROVEN here against a REAL Postgres testcontainer, NON-mocked: the real
``app.routes.boards`` router, the real ``app.repos.boards`` repo, the real
``mint_board_token`` minter and the real migration ``0028_boards`` all run end-to-end
against the live container, and the assertions read the actual rows back. A mocked-DB
test, or one that trusted the API's own echo instead of the stored ``bytea``, would pass
even with persistence silently corrupting the document; THIS gate cannot.

Two integration groups, all ``@pytest.mark.integration`` (Docker-gated):

  1. ``test_board_http_gate`` — the real HTTP gate: create/list are membership-gated
     (team B and an outsider get 403 on team A's paths); mint re-checks membership and
     answers the SAME generic 404 for "not your board" AND "no such board" (no oracle);
     the minted token's claim set matches the frozen cross-language contract the Node
     verifier reads; the internal doc endpoints are bridge-only and move the Y.Doc
     BYTE-IDENTICALLY (asserted both over HTTP and directly in the ``bytea`` column); an
     oversize write is refused 413 WITHOUT clobbering the stored document; and an unknown
     board is a 404 that never conjures a row.
  2. ``test_migration_0028_boards_forward_only`` — ``alembic upgrade head`` under
     EDITION=oss AND saas on FRESH containers creates ``boards`` (with the soft-delete
     column) and ``board_docs.state bytea NOT NULL`` plus the PARTIAL unique index
     ``ux_boards_team_default`` (UNIQUE ... WHERE) IDENTICALLY under both editions — no
     schema fork (D-26-02). No reverse migration is invoked.

CROSS-CONNECTION VISIBILITY (mirrors test_join_by_code_gate.py): the board router, the
bridge doc endpoints and the direct DB assertions each open their OWN pooled connection,
so they see only COMMITTED rows. Every fixture row is seeded + COMMITTED against the real
container via a dedicated ``async_session_factory()`` (torn down in ``finally``); the
``client`` fixture's default rollback session is re-overridden with a per-request
COMMITTING session so each request writes straight through and releases its locks.

SKIP=FAIL discipline: the ``integration`` marker lets CI's skip-grep capture this file. A
clean SKIP is legitimate ONLY when Docker is genuinely absent; under Docker this file MUST
run green — a wrong status or a corrupted byte FAILS, it never skips. Git Bash docker
invocations need MSYS_NO_PATHCONV=1. English-only.
"""
from __future__ import annotations

import asyncio
import os
import types
import uuid
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# ── Principal override helpers (mirror test_join_by_code_gate.py) ──────────────


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


# ── Group 1: the real HTTP create -> list -> mint -> bridge-doc gate ───────────


async def test_board_http_gate(client):
    """Real Postgres, non-mocked on every security path.

    Session model: the board router + the bridge doc endpoints + the direct DB reads
    open their OWN pooled connections and can only see COMMITTED data, so every fixture
    row is committed to the real container and torn down in ``finally``. The ``client``
    fixture's default rollback session is re-overridden with a per-request COMMITTING
    session so the real routes run end-to-end and release their locks on close.
    """
    import sqlalchemy as sa

    import app.db.session as db_session
    from app.config import settings
    from app.deps import get_session
    from app.main import app
    from app.repos import teams as teams_repo
    from app.repos import users as users_repo
    from app.services import rate_limit
    from tests.conftest import make_bridge_jwt

    # Real routes, per-request COMMITTING session: each request writes straight through
    # to the container and releases its locks on close, so a subsequent request's own
    # connection (and the bridge endpoints' and the direct SQL reads') sees the same
    # committed state — no cross-connection blind spot.
    async def _committing_get_session():
        async with db_session.async_session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = _committing_get_session

    # Clean any per-caller rate-limit buckets so an earlier test can't 429 us.
    rate_limit._storage.reset()

    # ── COMMITTED seed (distinct slugs/subs — never collide with other fixtures) ──
    async with db_session.async_session_factory() as seed:
        member_a = await users_repo.get_or_create_user(
            seed, source_user_id="brd-a-sub", email="brd-a@test.local",
            display_name="Board Member A",
        )
        member_b = await users_repo.get_or_create_user(
            seed, source_user_id="brd-b-sub", email="brd-b@test.local",
            display_name="Board Member B",
        )
        outsider = await users_repo.get_or_create_user(
            seed, source_user_id="brd-out-sub", email="brd-out@test.local",
            display_name="Board Outsider",
        )
        team_a = await teams_repo.create_team(
            seed, slug="brd-team-a", display_name="Board Team A",
            creator_user_id=member_a.id,   # member_a -> admin+member of team-a
        )
        team_b = await teams_repo.create_team(
            seed, slug="brd-team-b", display_name="Board Team B",
            creator_user_id=member_b.id,   # the DECOY team (isolation proof)
        )
        await seed.commit()

        member_a_id, member_a_sub = member_a.id, member_a.source_user_id
        member_b_id, member_b_sub = member_b.id, member_b.source_user_id
        outsider_id, outsider_sub = outsider.id, outsider.source_user_id
        team_a_id, team_a_slug = team_a.id, team_a.slug
        team_b_id, team_b_slug = team_b.id, team_b.slug

    member_a_p = types.SimpleNamespace(
        id=member_a_id, source_user_id=member_a_sub, email="brd-a@test.local",
        display_name="Board Member A", github_username=None, github_id=None,
    )
    member_b_p = types.SimpleNamespace(
        id=member_b_id, source_user_id=member_b_sub, email="brd-b@test.local",
        display_name="Board Member B", github_username=None, github_id=None,
    )
    outsider_p = types.SimpleNamespace(
        id=outsider_id, source_user_id=outsider_sub, email="brd-out@test.local",
        display_name="Board Outsider", github_username=None, github_id=None,
    )

    async def _as(user_p, method: str, path: str, **kw):
        _install_principal(user_p)
        try:
            if method == "POST":
                return await client.post(path, **kw)
            if method == "GET":
                return await client.get(path, **kw)
            if method == "PUT":
                return await client.put(path, **kw)
            raise AssertionError(f"unsupported method {method}")
        finally:
            _clear_principal()

    # A REAL bridge principal is resolved by the ACTUAL get_current_principal (NOT the
    # user-principal override): the token has dots and is not xbt_/ghu_/gho_, so the deps
    # chain falls through to the bridge-JWT branch. It is signed with the SAME
    # BRIDGE_SHARED_SECRET the endpoints trust, so kind == "bridge".
    bridge = make_bridge_jwt("brd-bridge-sub", team_a_slug)
    bridge_hdr = {"Authorization": f"Bearer {bridge}"}

    async def _read_doc_row(board_id: str):
        """Read the board_docs row DIRECTLY (own connection) — the byte-exactness
        assertion must see what the DB actually stored, not what the API echoed back."""
        async with db_session.async_session_factory() as c:
            row = (
                await c.execute(
                    sa.text(
                        "SELECT state, size_bytes FROM board_docs "
                        "WHERE board_id = CAST(:id AS uuid)"
                    ),
                    {"id": board_id},
                )
            ).mappings().first()
        return row

    async def _count_boards() -> int:
        async with db_session.async_session_factory() as c:
            return (
                await c.execute(sa.text("SELECT count(*) FROM boards"))
            ).scalar_one()

    try:
        # ── A. CREATE (SC#1) — token rides in the FRAGMENT, never the query string ──
        r = await _as(
            member_a_p, "POST", f"/v1/teams/{team_a_id}/boards",
            json={"title": "Team board"},
        )
        assert r.status_code == 201, r.text
        created = r.json()
        board_a = created["id"]
        assert uuid.UUID(board_a), "board id must be a valid UUID"
        assert created["open_url"], created
        assert created["expires_at"], created
        # The token is delivered in the URL fragment (#t=), which never reaches a server
        # log — it must NOT appear as a query parameter.
        assert "#t=" in created["open_url"], created["open_url"]
        assert "?t=" not in created["open_url"], created["open_url"]
        assert "&token=" not in created["open_url"], created["open_url"]

        # ── B. IDEMPOTENT — one default board per team; a second POST returns the same id ─
        r = await _as(
            member_a_p, "POST", f"/v1/teams/{team_a_id}/boards", json={},
        )
        assert r.status_code == 201, r.text
        assert r.json()["id"] == board_a, "create must be idempotent (same default board)"

        # ── C. LIST — the board appears; NO item carries doc bytes or a token ──────
        r = await _as(member_a_p, "GET", f"/v1/teams/{team_a_id}/boards")
        assert r.status_code == 200, r.text
        boards = r.json()["boards"]
        assert any(b["id"] == board_a for b in boards), "the board must be listed"
        for b in boards:
            assert "state" not in b, "list item must NEVER carry the Y.Doc bytes"
            assert "token" not in b, "list item must NEVER carry a token"
            assert "open_url" not in b, "list item must NEVER carry the tokenized open URL"

        # ── D. TEAM ISOLATION on create/list (SC#2) — team B + outsider get 403 ───
        for stranger in (member_b_p, outsider_p):
            r = await _as(
                stranger, "POST", f"/v1/teams/{team_a_id}/boards", json={},
            )
            assert r.status_code == 403, (stranger.source_user_id, r.text)
            r = await _as(stranger, "GET", f"/v1/teams/{team_a_id}/boards")
            assert r.status_code == 403, (stranger.source_user_id, r.text)

        # ── E. MINT + NO ORACLE (SC#2) — one 404 for "not yours" AND "no such board" ─
        r = await _as(member_a_p, "POST", f"/v1/boards/{board_a}/token")
        assert r.status_code == 200, r.text
        mint = r.json()
        token = mint["token"]
        assert token, mint
        assert mint["board_id"] == board_a, mint
        assert mint["ws_url"], mint

        # member_b is a member of team B, NOT team A: mint on team A's board -> 404.
        r_not_yours = await _as(member_b_p, "POST", f"/v1/boards/{board_a}/token")
        assert r_not_yours.status_code == 404, r_not_yours.text
        # A board id that does not exist at all -> the SAME 404.
        random_board = str(uuid.uuid4())
        r_no_such = await _as(member_a_p, "POST", f"/v1/boards/{random_board}/token")
        assert r_no_such.status_code == 404, r_no_such.text
        # No oracle: the two answers must be BYTE-EQUAL. A distinct message would tell an
        # outsider that a given board id exists and belongs to someone else.
        assert r_not_yours.json() == r_no_such.json(), (
            "the 'not your board' and 'no such board' responses must be identical"
        )
        assert r_not_yours.content == r_no_such.content, (
            "the two 404 bodies must be byte-equal (no existence oracle)"
        )

        # ── F. TOKEN CLAIMS — the frozen cross-language contract the Node verifier reads ─
        from authlib.jose import jwt as authlib_jwt

        claims = authlib_jwt.decode(token, settings.BRIDGE_SHARED_SECRET)
        claims.validate()
        assert claims["scope"] == "board", claims
        assert claims["board_id"] == board_a, claims
        assert claims["team_scope"] == team_a_slug, claims
        assert claims["team_id"] == str(team_a_id), claims
        assert claims["sub"] == member_a_sub, claims
        assert int(claims["exp"]) > int(claims["iat"]), claims

        # ── G. INTERNAL DOC — bridge-only + BYTE-EXACT (SC#3) ─────────────────────
        # A member's USER token — even a legitimate one — is 403 on the internal path.
        r = await _as(member_a_p, "GET", f"/v1/internal/boards/{board_a}/doc")
        assert r.status_code == 403, r.text
        r = await _as(
            member_a_p, "PUT", f"/v1/internal/boards/{board_a}/doc",
            content=b"\x00\x01", headers={"Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 403, r.text

        # With a bridge JWT: no doc stored yet -> 404 (the extension treats it as a new,
        # empty board).
        r = await client.get(f"/v1/internal/boards/{board_a}/doc", headers=bridge_hdr)
        assert r.status_code == 404, r.text

        # Build a Y.Doc-shaped update. pycrdt is NOT installed in this environment, so per
        # the plan we use a fixed byte string that INCLUDES NUL and high bytes — if any
        # accidental text/base64/UTF-8 round-trip occurred on the wire or in the bytea
        # column it would corrupt these bytes VISIBLY (0xFF/0xFE are not valid UTF-8, and a
        # NUL truncates a C string). USED: fixed high-byte pattern (pycrdt unavailable).
        doc_v1 = bytes([0, 1, 2, 255, 254, 0, 0, 127]) * 64  # 512 bytes, non-text
        r = await client.put(
            f"/v1/internal/boards/{board_a}/doc",
            content=doc_v1,
            headers={**bridge_hdr, "Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 204, r.text

        r = await client.get(f"/v1/internal/boards/{board_a}/doc", headers=bridge_hdr)
        assert r.status_code == 200, r.text
        assert r.content == doc_v1, "GET must return byte-identical to what was PUT"
        assert r.headers["content-type"] == "application/octet-stream", r.headers

        # And the raw bytea column holds EXACTLY those bytes (not what the API chose to echo).
        row = await _read_doc_row(board_a)
        assert row is not None, "board_docs row must exist after a store"
        assert bytes(row["state"]) == doc_v1, "the stored bytea must be byte-identical"
        assert row["size_bytes"] == len(doc_v1), "size_bytes must equal the byte length"

        # Overwrite (compaction strategy: one row, no append log) with DIFFERENT bytes.
        doc_v2 = bytes([255, 0, 128, 64, 1, 2, 3, 253]) * 96  # 768 bytes, different
        assert doc_v2 != doc_v1
        r = await client.put(
            f"/v1/internal/boards/{board_a}/doc",
            content=doc_v2,
            headers={**bridge_hdr, "Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 204, r.text
        r = await client.get(f"/v1/internal/boards/{board_a}/doc", headers=bridge_hdr)
        assert r.status_code == 200 and r.content == doc_v2, (
            "the overwrite must replace the stored bytes (no append log)"
        )
        current_bytes = doc_v2

        # ── H. SIZE CAP (SC#3) — an oversize write is 413 and MUST NOT clobber ────
        oversize = b"\x00" * (settings.BOARD_MAX_DOC_BYTES + 1)
        r = await client.put(
            f"/v1/internal/boards/{board_a}/doc",
            content=oversize,
            headers={**bridge_hdr, "Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 413, r.text
        # A rejected oversize write must not partially clobber a good document.
        r = await client.get(f"/v1/internal/boards/{board_a}/doc", headers=bridge_hdr)
        assert r.status_code == 200 and r.content == current_bytes, (
            "the 413 must leave the previously stored bytes intact"
        )
        row = await _read_doc_row(board_a)
        assert bytes(row["state"]) == current_bytes, "the bytea must be unchanged after 413"

        # ── I. UNKNOWN BOARD — PUT to a random board id is 404, never an implicit insert ─
        n_before = await _count_boards()
        unknown_board = str(uuid.uuid4())
        r = await client.put(
            f"/v1/internal/boards/{unknown_board}/doc",
            content=doc_v1,
            headers={**bridge_hdr, "Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 404, r.text
        n_after = await _count_boards()
        assert n_after == n_before, "the socket side must never conjure a boards row"
        # And no board_docs row was created for the unknown id either.
        assert await _read_doc_row(unknown_board) is None
    finally:
        # Deleting the teams cascades boards -> board_docs and team_members (FK CASCADE).
        # The board flow writes no audit_log rows, but delete any defensively before users.
        user_ids = [str(member_a_id), str(member_b_id), str(outsider_id)]
        async with db_session.async_session_factory() as cleaner:
            await cleaner.execute(
                sa.text("DELETE FROM teams WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": [str(team_a_id), str(team_b_id)]},
            )
            await cleaner.execute(
                sa.text(
                    "DELETE FROM audit_log WHERE actor_user_id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": user_ids},
            )
            await cleaner.execute(
                sa.text("DELETE FROM users WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": user_ids},
            )
            await cleaner.commit()


@pytest.mark.integration
async def test_blocked_member_cannot_board(client):
    """CR BLOCKER regression — an admin-BLOCKED member (team_members.blocked_at set) must
    lose board access, matching the canonical deps.get_team_scope gate (Phase-10 GHA-03).

    Before the fix, `_resolve_team_and_check_membership` checked only that a membership
    ROW existed, so a blocked member could still create/list boards and mint fresh board
    tokens — a standing back door past an admin's block. Real Postgres, real HTTP, real
    block written by the real repo; nothing mocked.
    """
    import app.db.session as db_session
    from app.deps import get_session
    from app.main import app
    from app.repos import teams as teams_repo
    from app.repos import users as users_repo
    from app.services import rate_limit

    async def _committing_get_session():
        async with db_session.async_session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = _committing_get_session
    rate_limit._storage.reset()

    async with db_session.async_session_factory() as seed:
        admin = await users_repo.get_or_create_user(
            seed, source_user_id="brd-blk-admin-sub", email="brd-blk-admin@test.local",
            display_name="Blk Admin",
        )
        victim = await users_repo.get_or_create_user(
            seed, source_user_id="brd-blk-victim-sub", email="brd-blk-victim@test.local",
            display_name="Blk Victim",
        )
        team = await teams_repo.create_team(
            seed, slug="brd-blk-team", display_name="Blk Team", creator_user_id=admin.id,
        )
        # victim is a real member, THEN blocked by the admin.
        await teams_repo.add_member(seed, team_id=team.id, user_id=victim.id, role="member")
        await seed.commit()
        blocked = await teams_repo.block_member(
            seed, team_id=team.id, user_id=victim.id, blocked_by=admin.id
        )
        assert blocked is not None and blocked.blocked_at is not None
        await seed.commit()
        team_id, victim_id, victim_sub = team.id, victim.id, victim.source_user_id
        admin_id = admin.id

    victim_p = types.SimpleNamespace(
        id=victim_id, source_user_id=victim_sub, email="brd-blk-victim@test.local",
        display_name="Blk Victim", github_username=None, github_id=None,
    )

    async def _as(user_p, method, path, **kw):
        _install_principal(user_p)
        try:
            if method == "POST":
                return await client.post(path, **kw)
            return await client.get(path, **kw)
        finally:
            _clear_principal()

    try:
        # A blocked member must NOT be able to create a board...
        r_create = await _as(victim_p, "POST", f"/v1/teams/{team_id}/boards", json={})
        assert r_create.status_code == 403, (
            f"blocked member created a board: {r_create.status_code} {r_create.text[:200]}"
        )
        # ...nor list boards...
        r_list = await _as(victim_p, "GET", f"/v1/teams/{team_id}/boards")
        assert r_list.status_code == 403, f"blocked member listed boards: {r_list.status_code}"
        # (mint-token also runs the same gate; create already proved the shared helper.)
    finally:
        import sqlalchemy as sa
        async with db_session.async_session_factory() as cleaner:
            await cleaner.execute(
                sa.text("DELETE FROM boards WHERE team_id = :tid"), {"tid": team_id}
            )
            await cleaner.execute(
                sa.text("DELETE FROM team_members WHERE team_id = :tid"), {"tid": team_id}
            )
            await cleaner.execute(sa.text("DELETE FROM teams WHERE id = :tid"), {"tid": team_id})
            await cleaner.execute(
                sa.text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": [admin_id, victim_id]},
            )
            await cleaner.commit()
        app.dependency_overrides.pop(get_session, None)


# ── Group 2: migration 0028 forward-only + edition-agnostic ────────────────────
#
# Self-contained mirror of test_join_by_code_gate.py's Group 3: a FRESH Postgres
# container per edition, the config singleton patched DIRECTLY (os.environ alone is
# inert — `settings` is frozen at import and alembic/env.py reads that singleton),
# `command.upgrade(cfg, "head")` in a worker thread (env.py calls asyncio.run
# internally). No reverse migration is ever invoked — this is upgrade-only.

_HERE = Path(__file__).resolve().parent
_ALEMBIC_INI = _HERE / ".." / "alembic.ini"
_SCRIPT_LOCATION = _HERE / ".." / "alembic"

# The release matrix is exactly the two editions config.py permits (D-26-02).
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
    """Alembic Config with dirname-relative paths. env.py overwrites sqlalchemy.url from
    the patched settings.DATABASE_URL, so this URL is only a fallback."""
    from alembic.config import Config

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", sqlalchemy_url)
    cfg.set_main_option("script_location", str(_SCRIPT_LOCATION))
    return cfg


async def _upgrade_and_probe_boards(edition: str) -> dict:
    """Spin fresh PG, patch the config singleton to `edition`, upgrade head, then probe
    information_schema + pg_indexes for boards + board_docs. Returns a dict of probes.

    Restores the singleton + os.environ in `finally` so no other session test is
    polluted, and stops the container even on assert/raise.
    """
    from testcontainers.postgres import PostgresContainer

    import app.config as app_config

    prev_edition = app_config.settings.EDITION
    prev_db_url = app_config.settings.DATABASE_URL
    prev_env_edition = os.environ.get("EDITION")
    prev_env_db = os.environ.get("DATABASE_URL")

    # Migrations call gen_random_uuid() -> pgcrypto must be preloaded (mirrors conftest).
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

        # env.py's run_migrations_online() calls asyncio.run() internally; a worker thread
        # gives it a clean, loop-free thread under pytest-asyncio's loop.
        from alembic import command

        await asyncio.to_thread(command.upgrade, cfg, "head")

        import asyncpg

        conn = await asyncpg.connect(dsn=plain_dsn)
        try:
            boards_cols = await conn.fetch(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'boards'
                """
            )
            state_col = await conn.fetch(
                """
                SELECT data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = 'board_docs' AND column_name = 'state'
                """
            )
            idx_rows = await conn.fetch(
                """
                SELECT indexdef
                FROM pg_indexes
                WHERE tablename = 'boards'
                  AND indexname = 'ux_boards_team_default'
                """
            )
        finally:
            await conn.close()

        assert len(state_col) == 1, (
            f"[{edition}] expected exactly ONE board_docs.state column after upgrade "
            f"head, got {len(state_col)} — migration 0028 did not create the table "
            f"under this edition (or the schema forked on EDITION)."
        )
        assert len(idx_rows) == 1, (
            f"[{edition}] expected exactly ONE ux_boards_team_default index after "
            f"upgrade head, got {len(idx_rows)} — the partial unique default-board "
            f"index is missing under this edition (or the schema forked on EDITION)."
        )
        return {
            "boards_columns": sorted(r["column_name"] for r in boards_cols),
            "state_data_type": state_col[0]["data_type"],
            "state_is_nullable": state_col[0]["is_nullable"],
            "default_indexdef": idx_rows[0]["indexdef"],
        }
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
async def test_migration_0028_boards_forward_only(edition: str) -> None:
    """Forward-only, edition-agnostic (D-26-02): `alembic upgrade head` under `edition`
    creates `boards` (with the soft-delete column) and `board_docs.state` as a NOT-NULL
    `bytea`, plus the PARTIAL unique index `ux_boards_team_default` (UNIQUE ... WHERE) —
    IDENTICALLY under oss AND saas (a fork would fail one edition). No reverse migration
    is invoked."""
    if not _docker_available():
        pytest.skip("Docker not available — skipping real-Postgres migration test")

    probe = await _upgrade_and_probe_boards(edition)

    # (a) boards has the expected shape, including the Phase-11 soft-delete column.
    for col in ("id", "team_id", "title", "is_default", "created_at", "deleted_at"):
        assert col in probe["boards_columns"], (
            f"[{edition}] boards.{col} missing; columns={probe['boards_columns']!r}"
        )

    # (b) board_docs.state is a NOT-NULL bytea (raw Y.Doc bytes, never nullable).
    assert probe["state_data_type"] == "bytea", (
        f"[{edition}] board_docs.state must be bytea (the Y.Doc update is opaque binary) "
        f"but data_type={probe['state_data_type']!r}."
    )
    assert probe["state_is_nullable"] == "NO", (
        f"[{edition}] board_docs.state must be NOT NULL but "
        f"is_nullable={probe['state_is_nullable']!r}."
    )

    # (c) the default-board index is a PARTIAL UNIQUE index (many boards per team, at most
    #     one live default) — its indexdef must carry BOTH UNIQUE and WHERE.
    indexdef = probe["default_indexdef"]
    assert "UNIQUE" in indexdef.upper(), (
        f"[{edition}] ux_boards_team_default must be UNIQUE but indexdef={indexdef!r}."
    )
    assert "WHERE" in indexdef.upper(), (
        f"[{edition}] ux_boards_team_default must be PARTIAL (WHERE is_default ...) but "
        f"indexdef={indexdef!r}."
    )
