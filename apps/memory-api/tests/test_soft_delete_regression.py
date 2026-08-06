"""Regression tests — soft-deleted rows must NOT appear on any default
read path patched by plan 11-06 (commits c0d4210 + 1435f2a).

Every legacy read endpoint must filter `deleted_at IS NULL`. The Brain
Monitor (`GET /v1/brain/events?include_deleted=true`) is the ONLY surface
that opts into soft-deleted rows. Every other list/get endpoint should hide
them.

Pattern per test: insert N rows → soft-delete one via direct DB UPDATE →
hit the public endpoint (or the patched helper) → assert N-1 rows returned
AND the deleted id is absent.

Coverage map (rows in this file ↔ files patched by 11-06):

| Test                                         | File patched                                          |
| -------------------------------------------- | ----------------------------------------------------- |
| test_tasks_list_excludes_soft_deleted        | app/routes/tasks.py — list_tasks                      |
| test_tasks_get_returns_404_for_soft_deleted  | app/routes/tasks.py — get_task                        |
| test_tasks_validate_assignee_rejects_deleted | app/routes/tasks.py — _validate_assignee              |
| test_crm_contacts_list_excludes_soft_deleted | app/routes/crm.py — list_contacts                     |
| test_crm_contacts_get_returns_404_for_soft   | app/routes/crm.py — get_contact                       |
| test_conversations_list_excludes_soft_del    | app/repos/conversations.py — list_conversations       |
| test_conversations_get_returns_none_for_soft | app/repos/conversations.py — get_conversation         |
| test_messages_list_excludes_soft_deleted     | app/repos/messages.py — list_messages (B-3 closure)   |
| test_messages_get_returns_none_for_soft_del  | app/repos/messages.py — get_message                   |
| test_team_messages_list_excludes_soft_del    | app/repos/team_messages.py — list_messages            |
| test_team_context_cache_excludes_soft_del    | app/services/team_context_cache.py — bundle SQL       |
| test_admin_projects_list_excludes_soft_del   | app/routes/admin_projects.py — list_projects          |
| test_native_provider_search_excludes_soft_del| packages/memory-models native_provider.search() PG    |
| test_native_provider_get_returns_none_for_soft| packages/memory-models native_provider.get()         |
| test_brain_events_include_deleted_remains    | Negative — locks the opt-in path stays opt-in         |

Skips: tests that exercise the public HTTP API rely on the integration
`client` + `session` fixtures, which spin a Postgres container via
testcontainers. If Docker is unavailable locally, the `pg_url` fixture in
conftest.py skips and these tests are SKIPPED — same pattern as
`test_brain_events_mutate.py`. CI provides Docker.

The Qdrant-backed `NativeProvider` cases are kept lightweight by stubbing
the async Qdrant client — we only need to assert that the PG hydration
clause (`AND deleted_at IS NULL`) filters out tombstoned rows even when
the vector-side filter happens to let them through. That single Qdrant
seam is what plan 11-06 Task 2 explicitly calls "belt and suspenders".
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
import sqlalchemy as sa

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# ── Helpers ──────────────────────────────────────────────────────────────


def _get_app_and_dep():
    """Lazy import — keeps unit-test collection cheap when integration
    deps (Docker, qdrant_client) are not installed."""
    from app.deps import get_current_principal
    from app.main import app

    return app, get_current_principal


def _install_principal_override(user, *, kind: str = "user") -> None:
    """Mirror of the helper in test_brain_events_mutate.py. We duplicate
    rather than import — cross-file fixture imports are pytest-fragile and
    the two helpers must stay in sync (they both populate the same
    principal shape consumed by `assert_can_edit_brain_event`)."""
    import types

    app, get_current_principal = _get_app_and_dep()

    sub = getattr(user, "source_user_id", None)
    fake_user = types.SimpleNamespace(
        id=user.id,
        source_user_id=sub,
        email=getattr(user, "email", None),
        display_name=getattr(user, "display_name", None),
        github_username=None,
        github_id=None,
    )

    async def _override():
        return {
            "kind": kind,
            "user": fake_user,
            "sub": sub,
            "api_token_team_scope": None,
            "github_is_org_member": None,
        }

    app.dependency_overrides[get_current_principal] = _override


def _clear_principal_override() -> None:
    from app.deps import get_current_principal
    from app.main import app

    app.dependency_overrides.pop(get_current_principal, None)


async def _upgrade_team_to_paid(session, *, team_slug: str) -> None:
    """`require_paid_tier` returns 403 on default `plan='starter'` teams.
    Tests that hit `/v1/tasks` or `/v1/crm/contacts` must bump the team
    to a paid plan first — alembic migration 0008 introduced this gate."""
    await session.execute(
        sa.text("UPDATE teams SET plan = 'team' WHERE slug = :slug"),
        {"slug": team_slug},
    )
    await session.commit()


async def _seed_task(session, *, team_slug: str, owner_id, title: str = "seed-task") -> str:
    row = (await session.execute(
        sa.text(
            """
            INSERT INTO tasks (team_scope, title, source, created_by)
            VALUES (:ts, :title, 'manual', :uid)
            RETURNING id
            """
        ),
        {"ts": team_slug, "title": title, "uid": str(owner_id)},
    )).fetchone()
    await session.commit()
    return str(row.id)


async def _seed_contact(
    session, *, team_slug: str, email: str | None, full_name: str = "Seed Person"
) -> str:
    """Insert one contact. `email` may be NULL — the table has a partial
    UNIQUE index (team_scope, email) WHERE email IS NOT NULL so distinct
    emails (or NULLs) are fine."""
    row = (await session.execute(
        sa.text(
            """
            INSERT INTO contacts
              (team_scope, contact_type, full_name, email, source,
               truth_level, confidence, opt_in_status, interaction_count)
            VALUES (:ts, 'direct', :fn, :em, 'test',
                    'WORKING', 1.0, 'unknown', 0)
            RETURNING id
            """
        ),
        {"ts": team_slug, "fn": full_name, "em": email},
    )).fetchone()
    await session.commit()
    return str(row.id)


async def _seed_conversation(
    session, *, team_slug: str, owner_id, title: str = "seed-conv"
) -> str:
    row = (await session.execute(
        sa.text(
            """
            INSERT INTO conversations (team_scope, owner_user_id, source, title)
            VALUES (:ts, :uid, 'librechat', :title)
            RETURNING id
            """
        ),
        {"ts": team_slug, "uid": str(owner_id), "title": title},
    )).fetchone()
    await session.commit()
    return str(row.id)


async def _seed_message(
    session, *, team_slug: str, conversation_id: str, content: str
) -> str:
    row = (await session.execute(
        sa.text(
            """
            INSERT INTO messages (
                conversation_id, team_scope, role, content,
                visibility, confidence, truth_level, source, validation_status,
                msg_metadata
            )
            VALUES (
                :cid, :ts, 'user', :c,
                'team', 1.0, 'WORKING', 'test', 'unverified',
                '{}'::jsonb
            )
            RETURNING id
            """
        ),
        {"cid": conversation_id, "ts": team_slug, "c": content},
    )).fetchone()
    await session.commit()
    return str(row.id)


async def _seed_memory_item(
    session,
    *,
    team_slug: str,
    content: str,
    source: str = "chat",
    truth_level: str = "WORKING",
) -> str:
    row = (await session.execute(
        sa.text(
            """
            INSERT INTO memory_items
              (team_scope, content, source, truth_level, validation_status, visibility)
            VALUES (:ts, :c, :src, :tl, 'unverified', 'team')
            RETURNING id
            """
        ),
        {"ts": team_slug, "c": content, "src": source, "tl": truth_level},
    )).fetchone()
    await session.commit()
    return str(row.id)


async def _seed_team_message(
    session, *, team_id, author_id, content: str
) -> str:
    row = (await session.execute(
        sa.text(
            """
            INSERT INTO team_messages (team_id, author_user_id, kind, content)
            VALUES (:tid, :uid, 'user', :c)
            RETURNING id
            """
        ),
        {"tid": str(team_id), "uid": str(author_id), "c": content},
    )).fetchone()
    await session.commit()
    return str(row.id)


async def _soft_delete(session, *, table: str, row_id: str) -> None:
    """Generic soft-delete: set deleted_at = now() on the given table row.
    Avoids dragging in the brain repo so the test exercises pure SQL state."""
    # Table names come from a closed allow-list in the test code itself —
    # never user input. Safe to f-string the table name.
    assert table in {
        "tasks", "contacts", "conversations", "messages",
        "memory_items", "team_messages",
    }, f"unexpected table: {table}"
    await session.execute(
        sa.text(f"UPDATE {table} SET deleted_at = now() WHERE id = :id"),
        {"id": row_id},
    )
    await session.commit()


# ── 1. tasks list — soft-deleted hidden ──────────────────────────────────


async def test_tasks_list_excludes_soft_deleted(client, session, seeded_two_teams):
    """c0d4210 — routes/tasks.py list_tasks adds `AND deleted_at IS NULL`."""
    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]
    await _upgrade_team_to_paid(session, team_slug=team_a.slug)

    keep_id = await _seed_task(session, team_slug=team_a.slug, owner_id=alice.id, title="keep")
    drop_id = await _seed_task(session, team_slug=team_a.slug, owner_id=alice.id, title="drop")
    await _soft_delete(session, table="tasks", row_id=drop_id)

    _install_principal_override(alice)
    try:
        r = await client.get("/v1/tasks", headers={"X-Team-Scope": team_a.slug})
        assert r.status_code == 200, r.text
        ids = {item["id"] for item in r.json()}
        assert keep_id in ids
        assert drop_id not in ids, (
            "soft-deleted task leaked into /v1/tasks — c0d4210 regression"
        )
    finally:
        _clear_principal_override()


# ── 2. tasks get — soft-deleted 404s ─────────────────────────────────────


async def test_tasks_get_returns_404_for_soft_deleted(
    client, session, seeded_two_teams
):
    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]
    await _upgrade_team_to_paid(session, team_slug=team_a.slug)

    task_id = await _seed_task(session, team_slug=team_a.slug, owner_id=alice.id)
    await _soft_delete(session, table="tasks", row_id=task_id)

    _install_principal_override(alice)
    try:
        r = await client.get(
            f"/v1/tasks/{task_id}", headers={"X-Team-Scope": team_a.slug}
        )
        assert r.status_code == 404, (
            f"soft-deleted task surfaced via /v1/tasks/{{id}} — c0d4210 regression: {r.text}"
        )
    finally:
        _clear_principal_override()


# ── 3. tasks _validate_assignee — soft-deleted contact rejected ──────────


async def test_tasks_validate_assignee_rejects_soft_deleted_contact(
    client, session, seeded_two_teams
):
    """c0d4210 Rule-2 extension — a soft-deleted contact must not be
    assignable to a fresh task. The route returns 422 (same shape as a
    missing contact) before the INSERT INTO tasks runs."""
    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]
    await _upgrade_team_to_paid(session, team_slug=team_a.slug)

    contact_id = await _seed_contact(
        session, team_slug=team_a.slug, email="tombstone@test.local"
    )
    await _soft_delete(session, table="contacts", row_id=contact_id)

    _install_principal_override(alice)
    try:
        r = await client.post(
            "/v1/tasks",
            headers={"X-Team-Scope": team_a.slug},
            json={
                "title": "task with deleted assignee",
                "source": "manual",
                "assigned_to": contact_id,
            },
        )
        assert r.status_code == 422, (
            f"create_task accepted a soft-deleted contact as assignee — "
            f"c0d4210 Rule-2 regression: {r.text}"
        )
        assert "not found" in r.json()["detail"].lower()
    finally:
        _clear_principal_override()


# ── 4. crm contacts list — soft-deleted hidden ───────────────────────────


async def test_crm_contacts_list_excludes_soft_deleted(
    client, session, seeded_two_teams
):
    """c0d4210 — routes/crm.py list_contacts adds `AND deleted_at IS NULL`."""
    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]
    await _upgrade_team_to_paid(session, team_slug=team_a.slug)

    keep_id = await _seed_contact(
        session, team_slug=team_a.slug, email="keep@test.local", full_name="Keep"
    )
    drop_id = await _seed_contact(
        session, team_slug=team_a.slug, email="drop@test.local", full_name="Drop"
    )
    await _soft_delete(session, table="contacts", row_id=drop_id)

    _install_principal_override(alice)
    try:
        r = await client.get(
            "/v1/crm/contacts", headers={"X-Team-Scope": team_a.slug}
        )
        assert r.status_code == 200, r.text
        ids = {item["id"] for item in r.json()}
        assert keep_id in ids
        assert drop_id not in ids, (
            "soft-deleted contact leaked into /v1/crm/contacts — c0d4210 regression"
        )
    finally:
        _clear_principal_override()


# ── 5. crm contacts get — soft-deleted 404s ──────────────────────────────


async def test_crm_contacts_get_returns_404_for_soft_deleted(
    client, session, seeded_two_teams
):
    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]
    await _upgrade_team_to_paid(session, team_slug=team_a.slug)

    contact_id = await _seed_contact(
        session, team_slug=team_a.slug, email="tombstone-get@test.local"
    )
    await _soft_delete(session, table="contacts", row_id=contact_id)

    _install_principal_override(alice)
    try:
        r = await client.get(
            f"/v1/crm/contacts/{contact_id}",
            headers={"X-Team-Scope": team_a.slug},
        )
        assert r.status_code == 404, (
            f"soft-deleted contact surfaced via /v1/crm/contacts/{{id}} — "
            f"c0d4210 regression: {r.text}"
        )
    finally:
        _clear_principal_override()


# ── 6. conversations list — soft-deleted hidden ──────────────────────────


async def test_conversations_list_excludes_soft_deleted(
    session, seeded_two_teams
):
    """c0d4210 — repos/conversations.py list_conversations filters
    Conversation.deleted_at.is_(None). Exercise the repo directly because
    the route layer is thin and the SQL gate lives in the repo."""
    from app.repos import conversations as conv_repo

    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]

    keep_id = await _seed_conversation(
        session, team_slug=team_a.slug, owner_id=alice.id, title="keep-conv"
    )
    drop_id = await _seed_conversation(
        session, team_slug=team_a.slug, owner_id=alice.id, title="drop-conv"
    )
    await _soft_delete(session, table="conversations", row_id=drop_id)

    rows = await conv_repo.list_conversations(
        session, team_scope=team_a.slug, owner_user_id=alice.id, limit=50
    )
    ids = {str(r.id) for r in rows}
    assert keep_id in ids
    assert drop_id not in ids, (
        "soft-deleted conversation surfaced via list_conversations — "
        "c0d4210 regression"
    )


# ── 7. conversations get — soft-deleted returns None ─────────────────────


async def test_conversations_get_returns_none_for_soft_deleted(
    session, seeded_two_teams
):
    from uuid import UUID

    from app.repos import conversations as conv_repo

    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]

    conv_id = await _seed_conversation(
        session, team_slug=team_a.slug, owner_id=alice.id
    )
    await _soft_delete(session, table="conversations", row_id=conv_id)

    got = await conv_repo.get_conversation(
        session, conversation_id=UUID(conv_id), team_scope=team_a.slug
    )
    assert got is None, (
        "get_conversation returned a soft-deleted row — c0d4210 regression"
    )


# ── 8. messages list — soft-deleted hidden (B-3 closure) ─────────────────


async def test_messages_list_excludes_soft_deleted(session, seeded_two_teams):
    """c0d4210 — repos/messages.py list_messages filters
    Message.deleted_at.is_(None). Closes plan-check Iter 1 blocker B-3
    (11-RESEARCH §Q8 flagged routes/messages.py as MEDIUM-risk gap)."""
    from uuid import UUID

    from app.repos import messages as msg_repo

    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]

    conv_id = await _seed_conversation(
        session, team_slug=team_a.slug, owner_id=alice.id, title="msg-host"
    )
    keep_id = await _seed_message(
        session, team_slug=team_a.slug, conversation_id=conv_id, content="keep msg"
    )
    drop_id = await _seed_message(
        session, team_slug=team_a.slug, conversation_id=conv_id, content="drop msg"
    )
    await _soft_delete(session, table="messages", row_id=drop_id)

    rows = await msg_repo.list_messages(
        session,
        team_scope=team_a.slug,
        conversation_id=UUID(conv_id),
        limit=50,
    )
    ids = {str(r.id) for r in rows}
    assert keep_id in ids
    assert drop_id not in ids, (
        "soft-deleted message surfaced via list_messages — c0d4210 "
        "regression (B-3)"
    )


# ── 9. messages get — soft-deleted returns None ──────────────────────────


async def test_messages_get_returns_none_for_soft_deleted(
    session, seeded_two_teams
):
    from uuid import UUID

    from app.repos import messages as msg_repo

    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]

    conv_id = await _seed_conversation(
        session, team_slug=team_a.slug, owner_id=alice.id
    )
    msg_id = await _seed_message(
        session, team_slug=team_a.slug, conversation_id=conv_id, content="m"
    )
    await _soft_delete(session, table="messages", row_id=msg_id)

    got = await msg_repo.get_message(
        session, message_id=UUID(msg_id), team_scope=team_a.slug
    )
    assert got is None, (
        "get_message returned a soft-deleted row — c0d4210 regression (B-3)"
    )


# ── 10. team_messages list — already filtered, lock the behaviour ────────


async def test_team_messages_list_excludes_soft_deleted(
    session, seeded_two_teams
):
    """team_messages was pre-filtered before 11-06 (RESEARCH §Q8). Test
    locks the contract so a future refactor cannot regress it silently."""
    from app.repos import team_messages as tm_repo

    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]

    keep_id = await _seed_team_message(
        session, team_id=team_a.id, author_id=alice.id, content="keep tm"
    )
    drop_id = await _seed_team_message(
        session, team_id=team_a.id, author_id=alice.id, content="drop tm"
    )
    await _soft_delete(session, table="team_messages", row_id=drop_id)

    rows = await tm_repo.list_messages(
        session, team_id=team_a.id, viewer_user_id=None, limit=50
    )
    ids = {str(r.id) for r in rows}
    assert keep_id in ids
    assert drop_id not in ids, (
        "soft-deleted team_message surfaced via list_messages — pre-existing "
        "filter regressed"
    )


# ── 11. team_context_cache bundle — soft-deleted memory_item hidden ──────


async def test_team_context_cache_excludes_soft_deleted_memory_items(
    session, seeded_two_teams
):
    """c0d4210 Rule-2 extension — services/team_context_cache.py adds the
    `AND deleted_at IS NULL` filter so a soft-deleted memory_item cannot
    leak into the LLM context bundle for up to 30 days while the janitor
    waits to purge it."""
    from app.services import team_context_cache as tcc

    tcc._reset_for_tests()
    team_a = seeded_two_teams["team_a"]

    keep_id = await _seed_memory_item(
        session, team_slug=team_a.slug, content="keep memory bundle",
        truth_level="VALIDATED",
    )
    drop_id = await _seed_memory_item(
        session, team_slug=team_a.slug, content="drop memory bundle",
        truth_level="VALIDATED",
    )
    await _soft_delete(session, table="memory_items", row_id=drop_id)

    result = await tcc.get_team_memory_bundle(
        session, team_scope=team_a.slug, team_id=team_a.id
    )
    assert "keep memory bundle" in result["bundle"]
    assert "drop memory bundle" not in result["bundle"], (
        "soft-deleted memory_item leaked into LLM context bundle — c0d4210 "
        "Rule-2 regression"
    )
    # Sanity: only the non-deleted row was counted.
    assert result["item_count"] == 1, (
        f"expected 1 item after soft-deleting one of two seeds, got "
        f"{result['item_count']}"
    )
    # IDs of the actual rows for forensic clarity in CI logs.
    assert keep_id != drop_id


# ── 12. admin_projects list — soft-deleted hidden ────────────────────────


async def test_admin_projects_list_excludes_soft_deleted(
    client, session, seeded_two_teams
):
    """c0d4210 Rule-2 extension — routes/admin_projects.py list_projects
    adds `AND deleted_at IS NULL`. An admin removing a project via the
    brain monitor must see it disappear from /v1/admin/projects right
    away rather than 30 days later when the janitor purges it."""
    import json

    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]

    # Seed two admin:project memory_items directly (mimics what
    # POST /v1/admin/projects writes — saves an admin-auth round trip).
    meta_keep = json.dumps({
        "url": "https://keep.example.com",
        "deploy_target": "firebase",
        "type": "static",
        "name": "Keep Project",
        "slug": "keep-project",
    })
    meta_drop = json.dumps({
        "url": "https://drop.example.com",
        "deploy_target": "firebase",
        "type": "static",
        "name": "Drop Project",
        "slug": "drop-project",
    })

    async def _seed_admin_project(content_key: str, meta: str) -> str:
        row = (await session.execute(
            sa.text(
                """
                INSERT INTO memory_items
                  (team_scope, content, metadata, visibility,
                   truth_level, validation_status, confidence, source)
                VALUES
                  (:ts, :c, :m::jsonb, 'team',
                   'CANONICAL', 'validated', 1.0, 'admin:project')
                RETURNING id
                """
            ),
            {"ts": team_a.slug, "c": content_key, "m": meta},
        )).fetchone()
        await session.commit()
        return str(row.id)

    await _seed_admin_project(f"project:{team_a.slug}:keep-project", meta_keep)
    drop_id = await _seed_admin_project(
        f"project:{team_a.slug}:drop-project", meta_drop
    )
    await _soft_delete(session, table="memory_items", row_id=drop_id)

    _install_principal_override(alice)
    try:
        r = await client.get(
            f"/v1/admin/projects?team_scope={team_a.slug}",
            headers={"X-Team-Scope": team_a.slug},
        )
        assert r.status_code == 200, r.text
        slugs = {p["slug"] for p in r.json()}
        assert "keep-project" in slugs
        assert "drop-project" not in slugs, (
            "soft-deleted admin:project leaked into /v1/admin/projects "
            "— c0d4210 Rule-2 regression"
        )
    finally:
        _clear_principal_override()


# ── 13. NativeProvider.search() — PG hydration filter ────────────────────


async def test_native_provider_search_pg_hydration_excludes_soft_deleted(
    pg_url, session, seeded_two_teams
):
    """1435f2a — packages/memory-models native_provider.search() adds
    `AND deleted_at IS NULL` to the PG hydration step. The Qdrant `Range`
    filter handles most cases; this is the belt-and-suspenders PG layer
    that catches a row flipped to deleted in PG between the Qdrant write
    and the search call (RESEARCH §Q3 PITFALL).

    We stub the Qdrant client to return BOTH ids — simulating exactly the
    drift scenario the PG filter exists to defend against. The provider
    must still drop the deleted row in the post-Qdrant PG hydration.
    """
    from xbrain_memory.providers.native_provider import NativeProvider

    team_a = seeded_two_teams["team_a"]

    keep_id = await _seed_memory_item(
        session, team_slug=team_a.slug, content="keep-search-hit"
    )
    drop_id = await _seed_memory_item(
        session, team_slug=team_a.slug, content="drop-search-hit"
    )
    await _soft_delete(session, table="memory_items", row_id=drop_id)

    # asyncpg DSN format (NativeProvider expects no SQLAlchemy driver prefix).
    pg_dsn = pg_url.replace("postgresql+asyncpg://", "postgresql://")

    async def _embedder(_text: str) -> list[float]:
        return [0.0] * 1536  # OpenAI text-embedding-3-small dim

    provider = NativeProvider(
        pg_dsn=pg_dsn,
        qdrant_url="http://localhost:6333",  # never reached — search is mocked
        embedder=_embedder,
    )

    class _StubHit:
        def __init__(self, hit_id: str, score: float = 0.9):
            self.id = hit_id
            self.score = score

    provider._qdrant = AsyncMock()
    # Simulate the drift: Qdrant returns BOTH points. The PG filter must
    # eliminate the deleted one when we hydrate (this is what 1435f2a
    # guarantees).
    provider._qdrant.search = AsyncMock(
        return_value=[_StubHit(keep_id), _StubHit(drop_id)]
    )

    hits = await provider.search(query="anything", team_scope=team_a.slug, limit=10)

    ids = {h.item.id for h in hits}
    assert keep_id in ids
    assert drop_id not in ids, (
        "NativeProvider.search() hydrated a soft-deleted memory_item — "
        "1435f2a regression (RESEARCH §Q3 PITFALL)"
    )


# ── 14. NativeProvider.get() — soft-deleted returns None ─────────────────


async def test_native_provider_get_returns_none_for_soft_deleted(
    pg_url, session, seeded_two_teams
):
    """1435f2a — native_provider.get() guards the single-row fetch with
    `AND deleted_at IS NULL`. Hides tombstones from `GET /v1/memory/{id}`
    and from internal `update()` / `history()` paths that delegate here.
    """
    from xbrain_memory.providers.native_provider import NativeProvider

    team_a = seeded_two_teams["team_a"]

    mi_id = await _seed_memory_item(
        session, team_slug=team_a.slug, content="single-row-get"
    )
    await _soft_delete(session, table="memory_items", row_id=mi_id)

    pg_dsn = pg_url.replace("postgresql+asyncpg://", "postgresql://")

    async def _embedder(_text: str) -> list[float]:
        return [0.0] * 1536

    provider = NativeProvider(
        pg_dsn=pg_dsn,
        qdrant_url="http://localhost:6333",
        embedder=_embedder,
    )
    provider._qdrant = AsyncMock()  # never reached

    got = await provider.get(mi_id, team_scope=team_a.slug)
    assert got is None, (
        "NativeProvider.get() returned a soft-deleted memory_item — "
        "1435f2a regression"
    )


# ── 15. /v1/brain/events?include_deleted=true still works (negative) ─────


async def test_brain_events_include_deleted_remains_opt_in(
    client, session, seeded_two_teams
):
    """The only contract `deleted_at IS NULL` filters MUST NOT break: the
    Brain Monitor opt-in. Soft-delete one task, two list calls — default
    hides, `?include_deleted=true` surfaces. This negative test locks the
    single surface that's allowed to see tombstoned rows."""
    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]

    keep_id = await _seed_task(session, team_slug=team_a.slug, owner_id=alice.id, title="keep-bm")
    drop_id = await _seed_task(session, team_slug=team_a.slug, owner_id=alice.id, title="drop-bm")
    await _soft_delete(session, table="tasks", row_id=drop_id)

    _install_principal_override(alice)
    try:
        # Default — drop hidden.
        r_default = await client.get(
            "/v1/brain/events?entity_type=task&limit=50",
            headers={"X-Team-Scope": team_a.slug},
        )
        assert r_default.status_code == 200, r_default.text
        default_ids = {it["entity_id"] for it in r_default.json()["items"]}
        assert keep_id in default_ids
        assert drop_id not in default_ids

        # Opt-in — drop visible.
        r_with = await client.get(
            "/v1/brain/events?entity_type=task&limit=50&include_deleted=true",
            headers={"X-Team-Scope": team_a.slug},
        )
        assert r_with.status_code == 200, r_with.text
        with_ids = {it["entity_id"] for it in r_with.json()["items"]}
        assert keep_id in with_ids
        assert drop_id in with_ids, (
            "brain monitor failed to surface soft-deleted row with "
            "include_deleted=true — 11-04 GET regression"
        )
    finally:
        _clear_principal_override()
