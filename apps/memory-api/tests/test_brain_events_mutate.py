"""Integration tests for PATCH/DELETE/POST .../restore on /v1/brain/events.

Locks Phase 11 BMO-04 / BMO-05 / BMO-06 contracts:

  1. Author PATCHes their own task → 200, DB shows new truth_level,
     audit_log has a 'brain.patch_truth_level' row.
  2. Non-author non-admin PATCHes → 403 with the exact phrasing locked
     by `assert_can_edit_brain_event` ("You can only edit items you
     created. Contact a team admin to modify items created by others.").
  3. Per-team admin PATCHes another user's task → 200.
  4. Bridge JWT PATCHes any → 200 (real bridge JWT, not an override —
     exercises the get_current_principal / get_team_scope pair).
  5. PATCH with truth_level='BOGUS' → 422 (Pydantic regex rejection at
     the API boundary, ahead of the DB CHECK constraint).
  6. Author DELETEs own task → 204; default `GET /v1/brain/events`
     excludes the row; `?include_deleted=true` includes it; audit_log
     has a 'brain.soft_delete' row.
  7. POST .../restore within the retention window → 200, `deleted_at`
     IS NULL on the underlying row; audit_log has 'brain.restore'.
  8. POST .../restore after manually setting `deleted_at = now() -
     INTERVAL '31 days'` → 410 (retention window expired).
  9. End-to-end round-trip: insert → soft-delete → assert hidden →
     restore → assert visible → soft-delete → fast-forward 31d via
     direct UPDATE → restore returns 410.
 10. DELETE on a memory_item invokes the MemoryProvider.mark_deleted
     fan-out exactly once with the same datetime that landed on PG.

Why not freezegun for case 9: `restore_entity` enforces the window with
`NOW() - INTERVAL '30 days'` server-side. Freezing the Python clock
would not affect Postgres's clock, but rewriting the `deleted_at`
column to `now() - INTERVAL '31 days'` achieves the same outcome
without an extra dependency. Plan 11-05 acceptance §5 explicitly
allows this equivalence ("freezegun OR equivalent time-travel helper").
"""
from __future__ import annotations

import time
import types

import pytest
import sqlalchemy as sa
from authlib.jose import jwt

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# ── Principal-shape fixtures (mirrors test_brain_events_list.py) ─────


def _get_app_and_dep():
    """Lazy import so test collection stays light when integration deps
    (Docker, qdrant_client) aren't present."""
    from app.deps import get_current_principal
    from app.main import app

    return app, get_current_principal


def _install_principal_override(
    user,
    *,
    kind: str = "user",
    api_token_team_scope: str | None = None,
    sub_override: str | None = None,
) -> None:
    """Install a get_current_principal override on the FastAPI app.

    Duplicates the helper from test_brain_events_list.py rather than
    importing it (cross-file fixture imports are pytest-fragile). The
    two helpers must stay in sync — both populate the same principal
    shape consumed by `assert_can_edit_brain_event`.
    """
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
            "api_token_team_scope": api_token_team_scope,
            "github_is_org_member": None,
        }

    app.dependency_overrides[get_current_principal] = _override


def _clear_principal_override() -> None:
    from app.deps import get_current_principal
    from app.main import app

    app.dependency_overrides.pop(get_current_principal, None)


# ── MemoryProvider override (for the Qdrant fan-out test) ────────────


class _RecordingMemoryProvider:
    """In-memory stand-in for MemoryProvider.mark_deleted / mark_restored.

    Records every call so the test can assert the exact arguments the
    route handler passed in — the timestamp must match what `soft_delete_entity`
    wrote to Postgres (RESEARCH §Q3 — no PG/Qdrant clock drift).
    """

    def __init__(self) -> None:
        self.deleted: list[tuple[str, object]] = []
        self.restored: list[str] = []

    async def mark_deleted(self, item_id, deleted_at):
        self.deleted.append((str(item_id), deleted_at))

    async def mark_restored(self, item_id):
        self.restored.append(str(item_id))


def _install_memory_provider_override(provider) -> _RecordingMemoryProvider:
    from app.deps import get_memory_provider
    from app.main import app

    app.dependency_overrides[get_memory_provider] = lambda: provider
    return provider


def _clear_memory_provider_override() -> None:
    from app.deps import get_memory_provider
    from app.main import app

    app.dependency_overrides.pop(get_memory_provider, None)


# ── Seeders ─────────────────────────────────────────────────────────


async def _seed_task(session, *, team_slug: str, owner_id, title: str = "seed-task"):
    """Insert one task and return its id. `created_by` is set so the
    author-edit branch in `assert_can_edit_brain_event` can fire."""
    row = (await session.execute(
        sa.text(
            """
            INSERT INTO tasks (team_scope, title, source, created_by)
            VALUES (:ts, :title, 'manual', :uid)
            RETURNING id, truth_level
            """
        ),
        {"ts": team_slug, "title": title, "uid": str(owner_id)},
    )).fetchone()
    await session.commit()
    return row.id, row.truth_level


async def _seed_memory_item(session, *, team_slug: str, content: str = "seed-memory"):
    row = (await session.execute(
        sa.text(
            """
            INSERT INTO memory_items (team_scope, content, source)
            VALUES (:ts, :c, 'chat')
            RETURNING id
            """
        ),
        {"ts": team_slug, "c": content},
    )).fetchone()
    await session.commit()
    return row.id


async def _make_admin(session, *, team_id, user_id) -> None:
    """Promote a user to admin in a team (mirrors the conftest's seed
    pattern — the creator is admin by default; bob is NOT admin of team-a)."""
    await session.execute(
        sa.text(
            """
            INSERT INTO team_members (team_id, user_id, role)
            VALUES (:tid, :uid, 'admin')
            ON CONFLICT (team_id, user_id) DO UPDATE SET role = 'admin'
            """
        ),
        {"tid": str(team_id), "uid": str(user_id)},
    )
    await session.commit()


async def _make_member(session, *, team_id, user_id) -> None:
    """Add a user as a plain member (role='member')."""
    await session.execute(
        sa.text(
            """
            INSERT INTO team_members (team_id, user_id, role)
            VALUES (:tid, :uid, 'member')
            ON CONFLICT (team_id, user_id) DO UPDATE SET role = 'member'
            """
        ),
        {"tid": str(team_id), "uid": str(user_id)},
    )
    await session.commit()


# ── 1. Author PATCHes own task → 200 + audit row ─────────────────────


async def test_patch_author_succeeds(client, session, seeded_two_teams):
    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]
    task_id, _ = await _seed_task(
        session, team_slug=team_a.slug, owner_id=alice.id
    )

    _install_principal_override(alice, kind="user")
    r = await client.patch(
        f"/v1/brain/events/task/{task_id}",
        headers={"X-Team-Scope": team_a.slug},
        json={"truth_level": "VALIDATED"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["truth_level"] == "VALIDATED"

    new_level = (await session.execute(
        sa.text("SELECT truth_level FROM tasks WHERE id = :id"),
        {"id": str(task_id)},
    )).scalar()
    assert new_level == "VALIDATED"

    audit_rows = (await session.execute(
        sa.text(
            "SELECT action, payload FROM audit_log "
            "WHERE target_id = :tid AND action = 'brain.patch_truth_level'"
        ),
        {"tid": str(task_id)},
    )).mappings().all()
    assert len(audit_rows) == 1, f"expected exactly one audit row, got {audit_rows}"
    assert audit_rows[0]["payload"]["entity_type"] == "task"
    assert audit_rows[0]["payload"]["new_truth_level"] == "VALIDATED"

    _clear_principal_override()


# ── 2. Non-author non-admin → 403 with the locked phrasing ───────────


async def test_patch_non_author_non_admin_forbidden(
    client, session, seeded_two_teams
):
    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]
    bob = seeded_two_teams["bob"]
    # Bob is admin of team-b, NOT a member of team-a. To exercise the
    # "non-author non-admin" branch in team-a, add him as a plain member.
    await _make_member(session, team_id=team_a.id, user_id=bob.id)
    task_id, _ = await _seed_task(
        session, team_slug=team_a.slug, owner_id=alice.id
    )

    _install_principal_override(bob, kind="user")
    r = await client.patch(
        f"/v1/brain/events/task/{task_id}",
        headers={"X-Team-Scope": team_a.slug},
        json={"truth_level": "VALIDATED"},
    )
    assert r.status_code == 403, r.text
    assert "You can only edit items you created" in r.json()["detail"], (
        f"403 detail phrasing changed — locked by deps.py: {r.json()}"
    )

    _clear_principal_override()


# ── 3. Per-team admin PATCHes another user's task → 200 ──────────────


async def test_patch_team_admin_succeeds_on_other_users_task(
    client, session, seeded_two_teams
):
    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]
    bob = seeded_two_teams["bob"]
    await _make_admin(session, team_id=team_a.id, user_id=bob.id)
    task_id, _ = await _seed_task(
        session, team_slug=team_a.slug, owner_id=alice.id, title="alice-task"
    )

    _install_principal_override(bob, kind="user")
    r = await client.patch(
        f"/v1/brain/events/task/{task_id}",
        headers={"X-Team-Scope": team_a.slug},
        json={"truth_level": "CANONICAL"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["truth_level"] == "CANONICAL"

    _clear_principal_override()


# ── 4. Bridge JWT PATCHes any → 200 ──────────────────────────────────


async def test_patch_bridge_jwt_succeeds(client, session, seeded_two_teams):
    """Bridge JWTs bypass `assert_can_edit_brain_event` (kind='bridge'
    short-circuit). Uses a real bridge JWT — not a principal override —
    so the test exercises get_current_principal end-to-end."""
    import os

    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]
    task_id, _ = await _seed_task(
        session, team_slug=team_a.slug, owner_id=alice.id
    )

    # Make sure no principal override interferes.
    _clear_principal_override()
    _clear_memory_provider_override()

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

    r = await client.patch(
        f"/v1/brain/events/task/{task_id}",
        headers={
            "X-Team-Scope": team_a.slug,
            "Authorization": f"Bearer {token}",
        },
        json={"truth_level": "PUBLIC"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["truth_level"] == "PUBLIC"


# ── 5. PATCH with truth_level='BOGUS' → 422 ──────────────────────────


async def test_patch_invalid_truth_level_rejected_at_boundary(
    client, session, seeded_two_teams
):
    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]
    task_id, original = await _seed_task(
        session, team_slug=team_a.slug, owner_id=alice.id
    )

    _install_principal_override(alice, kind="user")
    r = await client.patch(
        f"/v1/brain/events/task/{task_id}",
        headers={"X-Team-Scope": team_a.slug},
        json={"truth_level": "BOGUS"},
    )
    assert r.status_code == 422, r.text

    # Row was not modified (defence in depth — the Pydantic regex caught
    # it ahead of the DB CHECK; this asserts the boundary actually
    # rejected the request rather than failing silently after the write).
    new_level = (await session.execute(
        sa.text("SELECT truth_level FROM tasks WHERE id = :id"),
        {"id": str(task_id)},
    )).scalar()
    assert new_level == original

    _clear_principal_override()


# ── 6. DELETE → 204 + hidden from default list + audit row ───────────


async def test_delete_soft_deletes_and_default_list_hides(
    client, session, seeded_two_teams
):
    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]
    task_id, _ = await _seed_task(
        session, team_slug=team_a.slug, owner_id=alice.id
    )

    _install_principal_override(alice, kind="user")
    r = await client.delete(
        f"/v1/brain/events/task/{task_id}",
        headers={"X-Team-Scope": team_a.slug},
    )
    assert r.status_code == 204, r.text

    deleted_at = (await session.execute(
        sa.text("SELECT deleted_at FROM tasks WHERE id = :id"),
        {"id": str(task_id)},
    )).scalar()
    assert deleted_at is not None, "soft-delete did not set deleted_at"

    audit_rows = (await session.execute(
        sa.text(
            "SELECT action FROM audit_log "
            "WHERE target_id = :tid AND action = 'brain.soft_delete'"
        ),
        {"tid": str(task_id)},
    )).mappings().all()
    assert len(audit_rows) == 1

    # Default list excludes; include_deleted=true exposes.
    r_default = await client.get(
        "/v1/brain/events?entity_type=task&limit=50",
        headers={"X-Team-Scope": team_a.slug},
    )
    assert str(task_id) not in {it["entity_id"] for it in r_default.json()["items"]}

    r_with = await client.get(
        "/v1/brain/events?entity_type=task&limit=50&include_deleted=true",
        headers={"X-Team-Scope": team_a.slug},
    )
    assert str(task_id) in {it["entity_id"] for it in r_with.json()["items"]}

    _clear_principal_override()


# ── 7. Restore within window → 200 + audit ──────────────────────────


async def test_restore_within_window_succeeds(client, session, seeded_two_teams):
    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]
    task_id, _ = await _seed_task(
        session, team_slug=team_a.slug, owner_id=alice.id
    )

    _install_principal_override(alice, kind="user")
    # Delete first.
    r = await client.delete(
        f"/v1/brain/events/task/{task_id}",
        headers={"X-Team-Scope": team_a.slug},
    )
    assert r.status_code == 204

    # Restore.
    r = await client.post(
        f"/v1/brain/events/task/{task_id}/restore",
        headers={"X-Team-Scope": team_a.slug},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted_at"] is None

    db_state = (await session.execute(
        sa.text(
            "SELECT deleted_at, deleted_by FROM tasks WHERE id = :id"
        ),
        {"id": str(task_id)},
    )).fetchone()
    assert db_state.deleted_at is None
    assert db_state.deleted_by is None

    audit_rows = (await session.execute(
        sa.text(
            "SELECT action FROM audit_log "
            "WHERE target_id = :tid AND action = 'brain.restore'"
        ),
        {"tid": str(task_id)},
    )).mappings().all()
    assert len(audit_rows) == 1

    _clear_principal_override()


# ── 8. Restore after manual fast-forward 31d → 410 ───────────────────


async def test_restore_outside_window_returns_410(
    client, session, seeded_two_teams
):
    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]
    task_id, _ = await _seed_task(
        session, team_slug=team_a.slug, owner_id=alice.id
    )

    # Soft-delete directly + rewind deleted_at to 31d ago. This is the
    # "equivalent time-travel helper" alternative to freezegun called out
    # in plan 11-05 §Task 3 acceptance.
    await session.execute(
        sa.text(
            "UPDATE tasks SET deleted_at = NOW() - INTERVAL '31 days', "
            "deleted_by = :uid WHERE id = :id"
        ),
        {"id": str(task_id), "uid": str(alice.id)},
    )
    await session.commit()

    _install_principal_override(alice, kind="user")
    r = await client.post(
        f"/v1/brain/events/task/{task_id}/restore",
        headers={"X-Team-Scope": team_a.slug},
    )
    assert r.status_code == 410, r.text
    assert "retention window expired" in r.json()["detail"]

    # Row remains soft-deleted (restore was rejected — must not be a no-op
    # that silently revives anyway).
    deleted_at = (await session.execute(
        sa.text("SELECT deleted_at FROM tasks WHERE id = :id"),
        {"id": str(task_id)},
    )).scalar()
    assert deleted_at is not None

    _clear_principal_override()


# ── 9. End-to-end round-trip ─────────────────────────────────────────


async def test_round_trip_delete_restore_then_fast_forward(
    client, session, seeded_two_teams
):
    """One task: insert → soft-delete → assert hidden → restore →
    assert visible → soft-delete → fast-forward 31d → restore → 410.

    This is the regression net for the full retention-window machinery
    end-to-end through the HTTP layer."""
    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]
    task_id, _ = await _seed_task(
        session, team_slug=team_a.slug, owner_id=alice.id
    )

    _install_principal_override(alice, kind="user")

    # delete #1
    r = await client.delete(
        f"/v1/brain/events/task/{task_id}",
        headers={"X-Team-Scope": team_a.slug},
    )
    assert r.status_code == 204

    # hidden in default list
    listed = await client.get(
        "/v1/brain/events?entity_type=task&limit=50",
        headers={"X-Team-Scope": team_a.slug},
    )
    assert str(task_id) not in {it["entity_id"] for it in listed.json()["items"]}

    # restore #1
    r = await client.post(
        f"/v1/brain/events/task/{task_id}/restore",
        headers={"X-Team-Scope": team_a.slug},
    )
    assert r.status_code == 200

    # visible again
    listed = await client.get(
        "/v1/brain/events?entity_type=task&limit=50",
        headers={"X-Team-Scope": team_a.slug},
    )
    assert str(task_id) in {it["entity_id"] for it in listed.json()["items"]}

    # delete #2
    r = await client.delete(
        f"/v1/brain/events/task/{task_id}",
        headers={"X-Team-Scope": team_a.slug},
    )
    assert r.status_code == 204

    # fast-forward
    await session.execute(
        sa.text(
            "UPDATE tasks SET deleted_at = NOW() - INTERVAL '31 days' WHERE id = :id"
        ),
        {"id": str(task_id)},
    )
    await session.commit()

    # restore now 410
    r = await client.post(
        f"/v1/brain/events/task/{task_id}/restore",
        headers={"X-Team-Scope": team_a.slug},
    )
    assert r.status_code == 410

    _clear_principal_override()


# ── 10. memory_item DELETE → MemoryProvider.mark_deleted called once ─


async def test_memory_item_delete_invokes_qdrant_mark_deleted(
    client, session, seeded_two_teams
):
    """The route handler must fan out to MemoryProvider.mark_deleted for
    memory_item exactly once, with the same datetime that landed on PG."""
    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]
    mi_id = await _seed_memory_item(session, team_slug=team_a.slug)

    recorder = _install_memory_provider_override(_RecordingMemoryProvider())
    _install_principal_override(alice, kind="user")

    r = await client.delete(
        f"/v1/brain/events/memory_item/{mi_id}",
        headers={"X-Team-Scope": team_a.slug},
    )
    assert r.status_code == 204, r.text

    assert len(recorder.deleted) == 1, (
        f"expected one mark_deleted call, got {recorder.deleted}"
    )
    called_id, called_at = recorder.deleted[0]
    assert called_id == str(mi_id)

    # The datetime passed to Qdrant must match what PG stored, to within
    # the asyncpg→Python tz-aware round-trip (microseconds preserved).
    pg_deleted_at = (await session.execute(
        sa.text("SELECT deleted_at FROM memory_items WHERE id = :id"),
        {"id": str(mi_id)},
    )).scalar()
    assert pg_deleted_at == called_at, (
        f"PG/Qdrant clock drift on soft-delete: PG={pg_deleted_at} "
        f"Qdrant={called_at} — RESEARCH §Q3 regression"
    )

    _clear_principal_override()
    _clear_memory_provider_override()


# ── 11. memory_item restore → MemoryProvider.mark_restored called once
#       (paired test, locks the symmetric path) ────────────────────────


async def test_memory_item_restore_invokes_qdrant_mark_restored(
    client, session, seeded_two_teams
):
    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]
    mi_id = await _seed_memory_item(session, team_slug=team_a.slug)

    recorder = _install_memory_provider_override(_RecordingMemoryProvider())
    _install_principal_override(alice, kind="user")

    # Delete first.
    r = await client.delete(
        f"/v1/brain/events/memory_item/{mi_id}",
        headers={"X-Team-Scope": team_a.slug},
    )
    assert r.status_code == 204

    # Restore.
    r = await client.post(
        f"/v1/brain/events/memory_item/{mi_id}/restore",
        headers={"X-Team-Scope": team_a.slug},
    )
    assert r.status_code == 200, r.text

    assert len(recorder.restored) == 1, (
        f"expected one mark_restored call, got {recorder.restored}"
    )
    assert recorder.restored[0] == str(mi_id)

    _clear_principal_override()
    _clear_memory_provider_override()
