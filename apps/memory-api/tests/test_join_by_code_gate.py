"""Phase 25 — THE definitive Join-by-Code gate (JOINCODE-01, D-25-01..05).

A join code is a BEARER SECRET to the team-scoped brain (the product's sensitive
core), so "it works" is not enough — every SECURITY guard is PROVEN here against a
REAL Postgres testcontainer, NON-mocked on the security-bearing paths. Nothing about
the mint / redeem / revoke / migration logic is stubbed: the real
`app.repos.team_invite_codes` + `app.routes.teams` run end-to-end against the live
container, and the assertions read the actual rows back. A mocked-DB test, or one
that asserted the plaintext sits in the row, would pass even with the security
broken; THIS gate cannot.

Three integration groups, all `@pytest.mark.integration` (Docker-gated):

  1. `test_join_by_code_gate` — the real HTTP gate: mint (plaintext once +
     sha256-at-rest), non-admin 403 on mint/list/revoke, list carries no hash, a
     valid join adds the caller to the code's team ONLY + increments uses, an
     already-member join is a 200 no-op with uses UNCHANGED, and garbage / revoked /
     expired / max-uses-reached codes ALL return the identical generic 404 (no oracle).
  2. `test_double_spend_race_cannot_exceed_max_uses` — two concurrent sessions race
     to redeem a max_uses=1 code via the real `redeem_atomic`; EXACTLY ONE wins,
     `uses` ends at 1, and EXACTLY ONE membership is created (the atomic-increment
     claim proven under true concurrency, not merely asserted).
  3. `test_migration_0027_team_invite_codes_forward_only` — `alembic upgrade head`
     under EDITION=oss AND saas on fresh containers creates the `team_invite_codes`
     table with a NOT-NULL `code_hash` and the UNIQUE `ix_team_invite_codes_code_hash`
     index identically under both editions (no schema fork). No reverse migration.

CRITICAL — cross-connection visibility (mirrors test_catch_me_up_gate.py): the join
endpoint and the direct DB assertions open their OWN pooled connections, so every
fixture row is seeded + COMMITTED against the real container via a dedicated
`async_session_factory()` (torn down in `finally`). The `client` fixture's default
`get_session` is re-overridden with a per-request COMMITTING session so each request
writes straight through and releases its locks — no cross-connection blind spot.

SKIP=FAIL discipline: the `integration` marker lets CI's skip-grep capture this file.
A clean SKIP is legitimate ONLY when Docker is genuinely absent; under Docker this
file MUST run green — a wrong status or side-effect FAILS, it never skips. Git Bash
docker invocations need MSYS_NO_PATHCONV=1. English-only.
"""
from __future__ import annotations

import hashlib
import types
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# ── Principal override helpers (mirror test_catch_me_up_gate.py) ───────────────


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


# ── Group 1: the real HTTP mint -> join -> revoke gate ─────────────────────────


async def test_join_by_code_gate(client):
    """Real Postgres, non-mocked on every security path: mint reveals the plaintext
    ONCE and stores only its sha256 (hash-at-rest); a non-admin is 403 on
    mint/list/revoke; the list never carries a hash; a valid join adds the caller to
    the code's team ONLY and increments uses; an already-member join is a 200 no-op
    with uses UNCHANGED; and garbage / revoked / expired / max-uses-reached codes ALL
    return the SAME generic 404 (no oracle).

    Session model: the join route + the direct DB reads open their OWN pooled
    connections and can only see COMMITTED data, so every fixture row is committed to
    the real container and torn down in `finally`. The `client` fixture's default
    rollback session is re-overridden with a per-request COMMITTING session so the
    real routes run end-to-end against the real DB and release their locks on close."""
    import sqlalchemy as sa

    import app.db.session as db_session
    from app.deps import get_session
    from app.main import app
    from app.repos import teams as teams_repo
    from app.repos import users as users_repo
    from app.services import rate_limit

    # Real routes, per-request COMMITTING session: each request writes straight
    # through to the container and releases its locks on close, so a subsequent
    # request's own connection sees the same committed state.
    async def _committing_get_session():
        async with db_session.async_session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = _committing_get_session

    # Clean per-caller rate-limit buckets so an earlier test can't 429 us (the
    # join-by-code endpoint is rate-limited, D-25-04).
    rate_limit._storage.reset()

    # ── COMMITTED seed (distinct slugs/subs — never collide with other fixtures) ─
    async with db_session.async_session_factory() as seed:
        admin_a = await users_repo.get_or_create_user(
            seed, source_user_id="jbc-admin-a-sub", email="jbc-admin-a@test.local",
            display_name="JBC Admin A",
        )
        admin_b = await users_repo.get_or_create_user(
            seed, source_user_id="jbc-admin-b-sub", email="jbc-admin-b@test.local",
            display_name="JBC Admin B",
        )
        joiner = await users_repo.get_or_create_user(
            seed, source_user_id="jbc-joiner-sub", email="jbc-joiner@test.local",
            display_name="JBC Joiner",
        )
        joiner2 = await users_repo.get_or_create_user(
            seed, source_user_id="jbc-joiner2-sub", email="jbc-joiner2@test.local",
            display_name="JBC Joiner 2",
        )
        outsider = await users_repo.get_or_create_user(
            seed, source_user_id="jbc-outsider-sub", email="jbc-outsider@test.local",
            display_name="JBC Outsider",
        )
        team_a = await teams_repo.create_team(
            seed, slug="jbc-team-a", display_name="JBC Team A",
            creator_user_id=admin_a.id,   # admin_a -> admin+member of team-a
        )
        team_b = await teams_repo.create_team(
            seed, slug="jbc-team-b", display_name="JBC Team B",
            creator_user_id=admin_b.id,   # the DECOY team (isolation proof)
        )
        await seed.commit()

        admin_a_id, admin_a_sub = admin_a.id, admin_a.source_user_id
        joiner_id, joiner_sub = joiner.id, joiner.source_user_id
        joiner2_id, joiner2_sub = joiner2.id, joiner2.source_user_id
        outsider_id, outsider_sub = outsider.id, outsider.source_user_id
        admin_b_id = admin_b.id
        team_a_id, team_a_slug = team_a.id, team_a.slug
        team_b_id, team_b_slug = team_b.id, team_b.slug

    admin_a_p = types.SimpleNamespace(
        id=admin_a_id, source_user_id=admin_a_sub, email="jbc-admin-a@test.local",
        display_name="JBC Admin A", github_username=None, github_id=None,
    )
    joiner_p = types.SimpleNamespace(
        id=joiner_id, source_user_id=joiner_sub, email="jbc-joiner@test.local",
        display_name="JBC Joiner", github_username=None, github_id=None,
    )
    joiner2_p = types.SimpleNamespace(
        id=joiner2_id, source_user_id=joiner2_sub, email="jbc-joiner2@test.local",
        display_name="JBC Joiner 2", github_username=None, github_id=None,
    )
    outsider_p = types.SimpleNamespace(
        id=outsider_id, source_user_id=outsider_sub, email="jbc-outsider@test.local",
        display_name="JBC Outsider", github_username=None, github_id=None,
    )

    async def _as(user_p, method: str, path: str, **kw):
        _install_principal(user_p)
        try:
            if method == "POST":
                return await client.post(path, **kw)
            if method == "GET":
                return await client.get(path, **kw)
            if method == "DELETE":
                return await client.delete(path, **kw)
            raise AssertionError(f"unsupported method {method}")
        finally:
            _clear_principal()

    async def _read_code(code_id: str):
        """Read an invite-code row DIRECTLY (own connection) — the security assertion
        must see what the DB actually stored, not what the API chose to echo back."""
        async with db_session.async_session_factory() as c:
            row = (
                await c.execute(
                    sa.text(
                        "SELECT code_hash, code_prefix, uses, revoked_at, "
                        "expires_at, max_uses, team_id "
                        "FROM team_invite_codes WHERE id = CAST(:id AS uuid)"
                    ),
                    {"id": code_id},
                )
            ).mappings().first()
        return row

    async def _membership(user_id, team_slug: str):
        async with db_session.async_session_factory() as c:
            return await teams_repo.get_membership(
                c, user_id=user_id, team_slug=team_slug
            )

    try:
        # ── A. MINT (SC#1) — plaintext once + sha256-at-rest ──────────────────
        r = await _as(
            admin_a_p, "POST", f"/v1/teams/{team_a_id}/invite-codes",
            json={"role": "member", "max_uses": 5},
        )
        assert r.status_code == 201, r.text
        mint = r.json()
        plaintext = mint["code"]
        assert plaintext.startswith("xbi_"), mint
        assert mint["code_prefix"], mint
        assert mint["code_prefix"] != plaintext, "prefix must not be the full code"
        assert "code_hash" not in mint, "mint response must NEVER carry the hash"
        code_id = mint["id"]

        # Read the row the DB actually persisted: only the sha256 hash is stored.
        row = await _read_code(code_id)
        assert row is not None, "minted code row must exist"
        expected_hash = hashlib.sha256(plaintext.encode()).hexdigest()
        assert row["code_hash"] == expected_hash, (
            "DB must store sha256(plaintext), not the plaintext (D-25-01)"
        )
        assert row["code_hash"] != plaintext, "the plaintext must NOT be the stored value"
        # The plaintext must be absent from every stored text column.
        for col in ("code_hash", "code_prefix"):
            assert plaintext not in (row[col] or ""), (
                f"the plaintext leaked into stored column {col!r}"
            )
        assert row["uses"] == 0, "a freshly minted code has 0 uses"

        # ── B. AUTHZ (SC#1, D-25-03) — non-admin 403 on mint/list/revoke ──────
        r = await _as(
            joiner_p, "POST", f"/v1/teams/{team_a_id}/invite-codes",
            json={"role": "member"},
        )
        assert r.status_code == 403, r.text
        r = await _as(joiner_p, "GET", f"/v1/teams/{team_a_id}/invite-codes")
        assert r.status_code == 403, r.text
        r = await _as(
            joiner_p, "DELETE", f"/v1/teams/{team_a_id}/invite-codes/{code_id}"
        )
        assert r.status_code == 403, r.text

        # ── C. LIST NO-HASH (D-25-03) — admin sees metadata, never the secret ─
        r = await _as(admin_a_p, "GET", f"/v1/teams/{team_a_id}/invite-codes")
        assert r.status_code == 200, r.text
        items = r.json()
        assert len(items) >= 1, "the minted code must appear in the list"
        for item in items:
            assert "code_hash" not in item, "list item must NEVER carry the hash"
            assert "code" not in item, "list item must NEVER carry the plaintext"
            assert "code_prefix" in item and "uses" in item and "role" in item, item

        # ── D. JOIN valid (SC#2 + SC#3) — member added to team_a ONLY, uses++ ─
        r = await _as(
            joiner_p, "POST", "/v1/teams/join-by-code", json={"code": plaintext}
        )
        assert r.status_code == 200, r.text
        jr = r.json()
        assert jr["already_member"] is False, jr
        assert jr["team_id"] == str(team_a_id), jr
        # Added to team_a...
        assert await _membership(joiner_id, team_a_slug) is not None, (
            "a valid join must add a team_members row in the code's team"
        )
        # ...and NEVER to team_b (team_scope integrity — a code carries no cross-team reach).
        assert await _membership(joiner_id, team_b_slug) is None, (
            "a team-A code must never add the caller to team-B (decoy)"
        )
        assert (await _read_code(code_id))["uses"] == 1, "a valid join increments uses"

        # ── E. IDEMPOTENT (SC#2, D-25-04) — already-member is a no-op, uses UNCHANGED ─
        r = await _as(
            joiner_p, "POST", "/v1/teams/join-by-code", json={"code": plaintext}
        )
        assert r.status_code == 200, r.text
        assert r.json()["already_member"] is True, r.text
        assert (await _read_code(code_id))["uses"] == 1, (
            "an already-member no-op must NOT increment uses (D-25-04)"
        )

        # ── F. GARBAGE (SC#2) — a wrong code is a generic 404, no side-effect ─
        r = await _as(
            outsider_p, "POST", "/v1/teams/join-by-code",
            json={"code": "xbi_not-a-real-code"},
        )
        assert r.status_code == 404, r.text
        generic_msg = r.json()["detail"]  # the uniform no-oracle message
        assert await _membership(outsider_id, team_a_slug) is None
        assert await _membership(outsider_id, team_b_slug) is None

        # ── G. REVOKED (SC#3) — a revoked code rejects with the SAME 404 ──────
        r = await _as(
            admin_a_p, "POST", f"/v1/teams/{team_a_id}/invite-codes",
            json={"role": "member"},
        )
        assert r.status_code == 201, r.text
        revoked = r.json()
        r = await _as(
            admin_a_p, "DELETE",
            f"/v1/teams/{team_a_id}/invite-codes/{revoked['id']}",
        )
        assert r.status_code == 204, r.text
        r = await _as(
            joiner2_p, "POST", "/v1/teams/join-by-code",
            json={"code": revoked["code"]},
        )
        assert r.status_code == 404, r.text
        assert r.json()["detail"] == generic_msg, "revoked must not leak WHICH check failed"
        assert await _membership(joiner2_id, team_a_slug) is None

        # ── H. EXPIRED (SC#3) — an expired code rejects with the SAME 404 ─────
        r = await _as(
            admin_a_p, "POST", f"/v1/teams/{team_a_id}/invite-codes",
            json={"role": "member"},
        )
        assert r.status_code == 201, r.text
        expired = r.json()
        async with db_session.async_session_factory() as c:
            await c.execute(
                sa.text(
                    "UPDATE team_invite_codes SET expires_at = :past "
                    "WHERE id = CAST(:id AS uuid)"
                ),
                {"past": datetime.now(timezone.utc) - timedelta(days=1),
                 "id": expired["id"]},
            )
            await c.commit()
        r = await _as(
            joiner2_p, "POST", "/v1/teams/join-by-code",
            json={"code": expired["code"]},
        )
        assert r.status_code == 404, r.text
        assert r.json()["detail"] == generic_msg, "expired must reuse the generic message"
        assert await _membership(joiner2_id, team_a_slug) is None

        # ── I. MAX-USES-REACHED (SC#3) — the sequential ceiling holds ─────────
        r = await _as(
            admin_a_p, "POST", f"/v1/teams/{team_a_id}/invite-codes",
            json={"role": "member", "max_uses": 1},
        )
        assert r.status_code == 201, r.text
        capped = r.json()
        # First redemption consumes the single use.
        r = await _as(
            joiner2_p, "POST", "/v1/teams/join-by-code",
            json={"code": capped["code"]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["already_member"] is False, r.text
        assert await _membership(joiner2_id, team_a_slug) is not None
        # A second, DIFFERENT caller hits the ceiling -> the SAME generic 404.
        r = await _as(
            outsider_p, "POST", "/v1/teams/join-by-code",
            json={"code": capped["code"]},
        )
        assert r.status_code == 404, r.text
        assert r.json()["detail"] == generic_msg, "exhausted must reuse the generic message"
        assert await _membership(outsider_id, team_a_slug) is None
    finally:
        # Resource hygiene: deleting the teams cascades to team_members +
        # team_invite_codes (FK ondelete=CASCADE). The mint/join/revoke endpoints
        # wrote audit_log rows whose actor_user_id FK RESTRICTs the user delete, so
        # clear those first; users are deleted last.
        user_ids = [
            str(admin_a_id), str(admin_b_id), str(joiner_id),
            str(joiner2_id), str(outsider_id),
        ]
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
