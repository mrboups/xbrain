"""Integration tests for /v1/admin/brain/* superadmin endpoints.

Phase 11 plan 11-10. Locks the 10-case contract for the cross-team
superadmin surface:

  1. 403 for non-superadmin on every endpoint (5 endpoints).
  2. 200 for superadmin on overview + nested shape correct.
  3. Storage shape — pg_rows dict + qdrant_points present + minio_bytes
     is None when MinIO client is None.
  4. Activity zero-fill — daily is exactly 30 entries regardless of seed.
  5. Sources shape — pivoted dict per team.
  6. Drill-down events writes one audit_log row with correct payload keys.
  7. Drill-down missing team_slug returns 422 (FastAPI required-Query).
  8. Drill-down audit-failure path returns 500 and writes NO data
     (defensive — proves the no-data-without-audit invariant).
  9. Bridge JWT passes + payload.actor_sub captures bridge identity
     when actor_user_id IS NULL (MAJOR-1 invariant).
 10. Lockdown — ADMIN_USER_SUBS empty -> every endpoint 403 even for a
     previously-superadmin user; bridge JWTs still pass.

The fixtures bypass get_current_principal with app.dependency_overrides
(same pattern as test_brain_events_list.py / test_brain_events_mutate.py)
to avoid signing real JWTs in the test loop. Test 4 and 9 use a real
bridge JWT to exercise the full get_current_principal path for the
service-trust branch.
"""
from __future__ import annotations

import os
import time
import types

import pytest
import sqlalchemy as sa
from authlib.jose import jwt

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# ── Principal-shape fixtures ───────────────────────────────────────────


def _get_app_and_dep():
    from app.deps import get_current_principal
    from app.main import app

    return app, get_current_principal


def _install_principal_override(
    user,
    *,
    kind: str = "user",
    sub_override: str | None = None,
) -> None:
    """Install a get_current_principal override on the FastAPI app."""
    app, get_current_principal = _get_app_and_dep()

    sub = sub_override or getattr(user, "source_user_id", None)
    fake_user = types.SimpleNamespace(
        id=user.id,
        source_user_id=getattr(user, "source_user_id", sub),
        email=getattr(user, "email", None),
        display_name=getattr(user, "display_name", None),
        github_username=getattr(user, "github_username", None),
        github_id=getattr(user, "github_id", None),
    )

    async def _override():
        return {
            "kind": kind,
            "user": fake_user,
            "sub": sub,
            "github_is_org_member": None,
        }

    app.dependency_overrides[get_current_principal] = _override


def _clear_principal_override() -> None:
    from app.deps import get_current_principal
    from app.main import app

    app.dependency_overrides.pop(get_current_principal, None)


# ── Seeders ────────────────────────────────────────────────────────────


async def _seed_memory_item(session, *, team_slug: str, source: str = "chat", content: str = "seed"):
    row = (await session.execute(
        sa.text(
            "INSERT INTO memory_items (team_scope, content, source) "
            "VALUES (:ts, :c, :src) RETURNING id"
        ),
        {"ts": team_slug, "c": content, "src": source},
    )).fetchone()
    await session.commit()
    return row.id


async def _seed_task(session, *, team_slug: str, owner_id, title: str = "seed-task"):
    row = (await session.execute(
        sa.text(
            "INSERT INTO tasks (team_scope, title, source, created_by) "
            "VALUES (:ts, :t, 'manual', :uid) RETURNING id"
        ),
        {"ts": team_slug, "t": title, "uid": str(owner_id)},
    )).fetchone()
    await session.commit()
    return row.id


async def _seed_conversation(session, *, team_slug: str, owner_id, source: str = "librechat"):
    row = (await session.execute(
        sa.text(
            "INSERT INTO conversations (team_scope, owner_user_id, source, title) "
            "VALUES (:ts, :uid, :src, 'seed-conv') RETURNING id"
        ),
        {"ts": team_slug, "uid": str(owner_id), "src": source},
    )).fetchone()
    await session.commit()
    return row.id


# ── 1. 403 for non-superadmin on every endpoint ──────────────────────


async def test_non_superadmin_403_all_endpoints(client, seeded_two_teams):
    """A kind='user' principal whose sub is NOT in ADMIN_USER_SUBS gets
    403 on every /v1/admin/brain/* endpoint."""
    alice = seeded_two_teams["alice"]
    # alice-sub is NOT in ADMIN_USER_SUBS (conftest seeds it as
    # 'admin-sub-1,admin-sub-2'), so the override below puts alice in the
    # non-admin branch.
    _install_principal_override(alice, kind="user")
    try:
        for path in (
            "/v1/admin/brain/overview",
            "/v1/admin/brain/storage",
            "/v1/admin/brain/activity",
            "/v1/admin/brain/sources",
            "/v1/admin/brain/events?team_slug=team-a",
        ):
            r = await client.get(path)
            assert r.status_code == 403, f"{path} -> {r.status_code}: {r.text}"
    finally:
        _clear_principal_override()


# ── 2. 200 for superadmin on overview + nested shape ─────────────────


async def test_overview_superadmin_returns_nested_counts(
    client, session, seeded_two_teams
):
    """Seed multiple entities across truth_levels in team-a and team-b.
    Overview returns >=2 entries with the expected nested shape."""
    alice = seeded_two_teams["alice"]
    bob = seeded_two_teams["bob"]
    team_a = seeded_two_teams["team_a"]
    team_b = seeded_two_teams["team_b"]

    # Seed in team-a: 2 memory_items, 1 task
    await _seed_memory_item(session, team_slug=team_a.slug, content="m1")
    await _seed_memory_item(session, team_slug=team_a.slug, content="m2")
    await _seed_task(session, team_slug=team_a.slug, owner_id=alice.id)
    # Promote 1 memory_item to VALIDATED to exercise multiple truth_levels
    await session.execute(
        sa.text(
            "UPDATE memory_items SET truth_level = 'VALIDATED' "
            "WHERE team_scope = :ts AND content = 'm2'"
        ),
        {"ts": team_a.slug},
    )
    # Seed in team-b: 1 conversation
    await _seed_conversation(session, team_slug=team_b.slug, owner_id=bob.id)
    await session.commit()

    _install_principal_override(alice, kind="user", sub_override="admin-sub-1")
    try:
        r = await client.get("/v1/admin/brain/overview")
        assert r.status_code == 200, r.text
        body = r.json()
        assert isinstance(body, list)
        slugs = {b["team_slug"] for b in body}
        assert team_a.slug in slugs
        assert team_b.slug in slugs

        # team-a entry must have memory_item bucket with at least one
        # truth_level key.
        ta = next(b for b in body if b["team_slug"] == team_a.slug)
        assert "counts" in ta
        # memory_items default truth_level is EPHEMERAL (per 0002 migration);
        # we promoted m2 to VALIDATED so the bucket has 2 entries.
        mi = ta["counts"].get("memory_item", {})
        assert sum(mi.values()) >= 2
        # And the task bucket exists (truth_level default WORKING per 0017).
        assert "task" in ta["counts"]
    finally:
        _clear_principal_override()


# ── 3. Storage shape ────────────────────────────────────────────────


async def test_storage_pg_rows_present_minio_none_when_unconfigured(
    client, session, seeded_two_teams, monkeypatch
):
    """With MINIO_URL unset (default in tests), get_minio_client returns
    None and minio_bytes is None in the response. PG rows dict has all 6
    expected table keys."""
    alice = seeded_two_teams["alice"]
    team_a = seeded_two_teams["team_a"]
    # Seed one row so pg_rows.memory_items > 0 for team-a.
    await _seed_memory_item(session, team_slug=team_a.slug, content="storage-seed")

    # Belt-and-suspenders: force the MinIO client cache to be empty + the env
    # var to be empty so get_minio_client returns None on first call.
    from app.db import minio as minio_mod
    minio_mod.get_minio_client.cache_clear()
    monkeypatch.setattr(
        "app.config.settings.MINIO_URL", "", raising=False
    )

    _install_principal_override(alice, kind="user", sub_override="admin-sub-1")
    try:
        r = await client.get("/v1/admin/brain/storage")
        assert r.status_code == 200, r.text
        body = r.json()
        assert isinstance(body, list)
        ta = next(b for b in body if b["team_slug"] == team_a.slug)
        # 6 brain tables expected in pg_rows
        for tbl in (
            "memory_items",
            "conversations",
            "messages",
            "team_messages",
            "tasks",
            "contacts",
        ):
            assert tbl in ta["pg_rows"], f"missing pg_rows key {tbl} in {ta!r}"
        assert ta["pg_rows"]["memory_items"] >= 1
        # Without MinIO config, minio_bytes must be None.
        assert ta["minio_bytes"] is None
        # qdrant_points may be int or None — both are valid responses; we
        # only assert the key exists.
        assert "qdrant_points" in ta
    finally:
        _clear_principal_override()
        minio_mod.get_minio_client.cache_clear()


# ── 4. Activity zero-fill (exactly N entries regardless of seed) ─────


async def test_activity_zero_fill_30_days(client, session, seeded_two_teams):
    """daily list is always exactly N entries (default 30) per team."""
    alice = seeded_two_teams["alice"]
    team_a = seeded_two_teams["team_a"]
    # Seed one task today so we exercise the non-zero path on the most
    # recent bucket.
    await _seed_task(session, team_slug=team_a.slug, owner_id=alice.id, title="today")

    _install_principal_override(alice, kind="user", sub_override="admin-sub-1")
    try:
        r = await client.get("/v1/admin/brain/activity?days=30")
        assert r.status_code == 200, r.text
        body = r.json()
        ta = next(b for b in body if b["team_slug"] == team_a.slug)
        assert len(ta["daily"]) == 30, f"expected 30 entries, got {len(ta['daily'])}"
        # The most recent bucket should reflect today's seed (>= 1 event).
        last = ta["daily"][-1]
        assert last["events"] >= 1
        # Older buckets are zero-filled (the conftest does not seed older
        # rows, so every entry except possibly the last is zero).
        zeros = [d["events"] for d in ta["daily"][:-1]]
        assert all(z == 0 for z in zeros)
    finally:
        _clear_principal_override()


# ── 5. Sources shape ─────────────────────────────────────────────────


async def test_sources_pivoted_per_team(client, session, seeded_two_teams):
    """Multiple memory_items with different source labels surface as a
    pivoted sources dict per team."""
    alice = seeded_two_teams["alice"]
    team_a = seeded_two_teams["team_a"]
    # 3 from librechat
    for i in range(3):
        await _seed_memory_item(
            session, team_slug=team_a.slug, source="librechat", content=f"lc-{i}"
        )
    # 2 from granola
    for i in range(2):
        await _seed_memory_item(
            session, team_slug=team_a.slug, source="granola", content=f"g-{i}"
        )

    _install_principal_override(alice, kind="user", sub_override="admin-sub-1")
    try:
        r = await client.get("/v1/admin/brain/sources?days=30")
        assert r.status_code == 200, r.text
        body = r.json()
        ta = next(b for b in body if b["team_slug"] == team_a.slug)
        # granola_note is the entity_type alias the v_brain_events view emits
        # when memory_items.source = 'granola', but the source LABEL itself
        # remains 'granola' in the sources column — so the dict has both.
        # Look for librechat (3) and granola (2).
        assert ta["sources"].get("librechat") == 3, ta["sources"]
        assert ta["sources"].get("granola") == 2, ta["sources"]
    finally:
        _clear_principal_override()


# ── 6. Drill-down writes audit_log row with expected payload keys ────


async def test_drilldown_writes_audit_row(client, session, seeded_two_teams):
    """GET /v1/admin/brain/events?team_slug=X must write exactly one
    audit_log row with action='superadmin_brain_access' and the expected
    payload keys (actor_sub, actor_kind, endpoint, query_params,
    target_team_slug)."""
    alice = seeded_two_teams["alice"]
    team_a = seeded_two_teams["team_a"]
    await _seed_task(session, team_slug=team_a.slug, owner_id=alice.id, title="drill")

    # Count audit rows BEFORE the call.
    pre = (await session.execute(
        sa.text(
            "SELECT COUNT(*) FROM audit_log "
            "WHERE action = 'superadmin_brain_access' AND team_scope = :ts"
        ),
        {"ts": team_a.slug},
    )).scalar()

    _install_principal_override(alice, kind="user", sub_override="admin-sub-1")
    try:
        r = await client.get(f"/v1/admin/brain/events?team_slug={team_a.slug}")
        assert r.status_code == 200, r.text
    finally:
        _clear_principal_override()

    post = (await session.execute(
        sa.text(
            "SELECT COUNT(*) FROM audit_log "
            "WHERE action = 'superadmin_brain_access' AND team_scope = :ts"
        ),
        {"ts": team_a.slug},
    )).scalar()
    assert post == pre + 1, f"expected +1 audit row, got {post} from {pre}"

    # Verify payload shape.
    payload = (await session.execute(
        sa.text(
            "SELECT payload FROM audit_log "
            "WHERE action = 'superadmin_brain_access' AND team_scope = :ts "
            "ORDER BY id DESC LIMIT 1"
        ),
        {"ts": team_a.slug},
    )).scalar()
    assert payload is not None
    # actor_sub is the MAJOR-1 invariant key — must be non-NULL.
    assert payload.get("actor_sub") == "admin-sub-1", payload
    assert payload.get("actor_kind") == "user", payload
    assert payload.get("endpoint") == "/v1/admin/brain/events", payload
    assert payload.get("target_team_slug") == team_a.slug, payload
    assert isinstance(payload.get("query_params"), dict)
    assert payload["query_params"].get("team_slug") == team_a.slug


# ── 7. Missing team_slug returns 422 ─────────────────────────────────


async def test_drilldown_missing_team_slug_422(client, seeded_two_teams):
    """FastAPI's required-Query validation rejects the call when
    team_slug is absent."""
    alice = seeded_two_teams["alice"]
    _install_principal_override(alice, kind="user", sub_override="admin-sub-1")
    try:
        r = await client.get("/v1/admin/brain/events")
        assert r.status_code == 422, r.text
    finally:
        _clear_principal_override()


# ── 8. Audit-failure path returns 500 + no data + no audit row ───────


async def test_drilldown_audit_failure_returns_500_no_data(
    client, session, seeded_two_teams, monkeypatch
):
    """If write_audit raises, the route must return 500 AND not write a
    partial audit row AND not serve any data."""
    alice = seeded_two_teams["alice"]
    team_a = seeded_two_teams["team_a"]
    await _seed_task(session, team_slug=team_a.slug, owner_id=alice.id)

    # Monkey-patch the write_audit imported by the admin_brain route to raise.
    async def _explode(*args, **kwargs):
        raise RuntimeError("forced audit failure for test")

    from app.routes import admin_brain as adm
    monkeypatch.setattr(adm, "write_audit", _explode)

    pre = (await session.execute(
        sa.text(
            "SELECT COUNT(*) FROM audit_log "
            "WHERE action = 'superadmin_brain_access' AND team_scope = :ts"
        ),
        {"ts": team_a.slug},
    )).scalar()

    _install_principal_override(alice, kind="user", sub_override="admin-sub-1")
    try:
        r = await client.get(f"/v1/admin/brain/events?team_slug={team_a.slug}")
        assert r.status_code == 500, r.text
    finally:
        _clear_principal_override()

    post = (await session.execute(
        sa.text(
            "SELECT COUNT(*) FROM audit_log "
            "WHERE action = 'superadmin_brain_access' AND team_scope = :ts"
        ),
        {"ts": team_a.slug},
    )).scalar()
    assert post == pre, f"audit row count must not change on failure: {pre} -> {post}"


# ── 9. Bridge JWT passes + audit captures bridge identity ────────────


async def test_drilldown_bridge_jwt_audit_captures_actor_sub(
    client, session, seeded_two_teams
):
    """Bridge JWT (kind='bridge') reaches the route AND the audit_log row
    has actor_user_id IS NULL AND payload.actor_sub == bridge sub AND
    payload.actor_kind == 'bridge'. This is the MAJOR-1 invariant."""
    alice = seeded_two_teams["alice"]
    team_a = seeded_two_teams["team_a"]
    await _seed_task(session, team_slug=team_a.slug, owner_id=alice.id)

    # Clear any user override; use a real bridge JWT so the route runs the
    # full get_current_principal pathway for the bridge branch.
    _clear_principal_override()

    secret = os.environ["BRIDGE_SHARED_SECRET"]
    now = int(time.time())
    payload = {
        "iss": "test-bridge",
        "sub": "granola-sync",
        # team_scope on the JWT is the BRIDGE's own scope — irrelevant for
        # the admin endpoint (which bypasses X-Team-Scope), but the
        # get_current_principal path still expects the field.
        "team_scope": team_a.slug,
        "scope": "bridge",
        "iat": now,
        "exp": now + 300,
    }
    token = jwt.encode({"alg": "HS256"}, payload, secret).decode("ascii")

    r = await client.get(
        f"/v1/admin/brain/events?team_slug={team_a.slug}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text

    row = (await session.execute(
        sa.text(
            "SELECT actor_user_id, payload FROM audit_log "
            "WHERE action = 'superadmin_brain_access' AND team_scope = :ts "
            "ORDER BY id DESC LIMIT 1"
        ),
        {"ts": team_a.slug},
    )).fetchone()
    assert row is not None
    assert row.actor_user_id is None, "bridge JWT must have NULL actor_user_id"
    pl = row.payload
    assert pl.get("actor_sub") == "granola-sync", pl
    assert pl.get("actor_kind") == "bridge", pl


# ── 10. Lockdown — ADMIN_USER_SUBS empty -> 403 for users; bridge OK ──


async def test_lockdown_admin_user_subs_empty(
    client, seeded_two_teams, monkeypatch
):
    """With ADMIN_USER_SUBS='' (lockdown default), real-user principals
    cannot reach any admin endpoint even if their sub used to be in the
    list. Bridge JWTs still pass — service trust is documented."""
    alice = seeded_two_teams["alice"]
    team_a = seeded_two_teams["team_a"]

    monkeypatch.setattr("app.config.settings.ADMIN_USER_SUBS", "")

    # User who was previously a superadmin (admin-sub-1) is now locked out.
    _install_principal_override(alice, kind="user", sub_override="admin-sub-1")
    try:
        for path in (
            "/v1/admin/brain/overview",
            "/v1/admin/brain/storage",
            "/v1/admin/brain/activity",
            "/v1/admin/brain/sources",
            f"/v1/admin/brain/events?team_slug={team_a.slug}",
        ):
            r = await client.get(path)
            assert r.status_code == 403, f"{path} -> {r.status_code}: {r.text}"
    finally:
        _clear_principal_override()

    # Bridge JWT still passes (service trust unchanged by lockdown).
    secret = os.environ["BRIDGE_SHARED_SECRET"]
    now = int(time.time())
    payload = {
        "iss": "test-bridge",
        "sub": "bridge-sub-1",
        "team_scope": team_a.slug,
        "scope": "bridge",
        "iat": now,
        "exp": now + 300,
    }
    token = jwt.encode({"alg": "HS256"}, payload, secret).decode("ascii")
    r = await client.get(
        "/v1/admin/brain/overview",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
