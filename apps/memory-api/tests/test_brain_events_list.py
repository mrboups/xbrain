"""Integration tests for GET /v1/brain/events (Phase 11 BMO-02 / BMO-03).

Locks the contract of the universal brain-event read surface:

1. Seeding one row per entity_type returns all 3 expected rows.
2. entity_type filter narrows the set.
3. Cursor pagination round-trips correctly (no overlap, no gap).
4. Soft-delete is hidden by default; include_deleted=true exposes it.
5. Non-member of the target team gets 403.
6. Bridge service JWT with matching team_scope gets 200.
7. Cursor tie-break: 3 rows with identical created_at, limit=2 → union
   of two pages equals the seeded set without overlap (locks the tuple-
   comparison clause).
8. Author authenticated via the Google OIDC ("user") branch sees their
   own row.
9. Author authenticated via the GitHub gho_ branch (same user.id after
   Phase 10 auth-merge) also sees their own row — locks the B-2
   regression that the principal-shape change in Phase 10 was supposed
   to be invariant under for this helper.
10. Author authenticated via the xbt_ (user_api_token) branch sees their
    own row.

Cases 5 and 6 also implicitly assert that ``get_team_scope`` is wired
into the new router — without that dependency, a non-member would not
get a 403 at all.

The fixtures bypass ``get_current_principal`` with
``app.dependency_overrides`` (same pattern as
``tests/test_phase10_block.py``). That avoids signing real JWTs and
hitting the GitHub / Google verification paths in the test loop.
"""
from __future__ import annotations

import time
import types
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from authlib.jose import jwt

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# ── Principal-shape fixtures (mirrors deps.py get_current_principal) ─


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
    """Install a principal override on the FastAPI app.

    Args:
        user: any object with ``.id``, ``.source_user_id``, ``.email``,
            ``.display_name``. The two real shapes — ORM User and
            ``types.SimpleNamespace`` (the xbt_ branch) — are both
            duck-compatible.
        kind: ``user`` for Google/GitHub/bridge-acting-user paths,
            ``user_api_token`` for xbt_ tokens.
        api_token_team_scope: ``None`` for multi-team xbt_ (falls
            through to membership check) or a slug for single-team
            scoped tokens.
        sub_override: Optional override for the ``sub`` claim. Used by
            the GitHub-flavoured tests to seat a ``github:<login>``
            shape — invisible to ``assert_can_edit_brain_event`` (which
            uses ``user.id``) but proves the helper is truly
            sub-agnostic.
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
    """Pop just the get_current_principal override so the get_session
    override installed by the conftest `client` fixture survives between
    overrides set inside a single test."""
    from app.deps import get_current_principal
    from app.main import app

    app.dependency_overrides.pop(get_current_principal, None)


# ── Seeders ─────────────────────────────────────────────────────────


async def _seed_three_distinct(session, *, team_slug: str, owner_id) -> dict[str, str]:
    """Insert one memory_item + one conversation + one task in `team_slug`.

    Returns a dict of {entity_type: entity_id} so tests can pinpoint
    specific rows. Picking one row per representative entity type keeps
    the assertions readable while still exercising both arms of the
    UNION that have / lack a `created_by` column.
    """
    seeded: dict[str, str] = {}

    mi_id = (
        await session.execute(
            sa.text(
                """
                INSERT INTO memory_items (team_scope, content, source)
                VALUES (:ts, 'seed-memory', 'chat')
                RETURNING id
                """
            ),
            {"ts": team_slug},
        )
    ).scalar_one()
    seeded["memory_item"] = str(mi_id)

    conv_id = (
        await session.execute(
            sa.text(
                """
                INSERT INTO conversations
                  (team_scope, owner_user_id, source, title)
                VALUES (:ts, :uid, 'librechat', 'seed-conversation')
                RETURNING id
                """
            ),
            {"ts": team_slug, "uid": str(owner_id)},
        )
    ).scalar_one()
    seeded["conversation"] = str(conv_id)

    tk_id = (
        await session.execute(
            sa.text(
                """
                INSERT INTO tasks (team_scope, title, source, created_by)
                VALUES (:ts, 'seed-task', 'manual', :uid)
                RETURNING id
                """
            ),
            {"ts": team_slug, "uid": str(owner_id)},
        )
    ).scalar_one()
    seeded["task"] = str(tk_id)

    await session.commit()
    return seeded


async def _seed_identical_timestamp_tasks(
    session, *, team_slug: str, owner_id, n: int = 3
) -> list[str]:
    """Insert `n` tasks all sharing the exact same `created_at`.

    Exercises the cursor tie-break path. Using SQL `:ts_param` for
    created_at directly (overriding the DEFAULT now()) guarantees all
    inserted rows compare equal on the primary cursor column.
    """
    fixed_ts = datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)
    ids: list[str] = []
    for i in range(n):
        row = (
            await session.execute(
                sa.text(
                    """
                    INSERT INTO tasks (team_scope, title, source, created_at, created_by)
                    VALUES (:ts, :title, 'manual', :created_at, :uid)
                    RETURNING id
                    """
                ),
                {
                    "ts": team_slug,
                    "title": f"tied-task-{i}",
                    "created_at": fixed_ts,
                    "uid": str(owner_id),
                },
            )
        ).fetchone()
        ids.append(str(row.id))
    await session.commit()
    return ids


# ── 1. Seed three rows, listing returns three ───────────────────────


async def test_list_returns_seeded_rows(client, session, seeded_two_teams):
    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]

    seeded = await _seed_three_distinct(
        session, team_slug=team_a.slug, owner_id=alice.id
    )

    _install_principal_override(alice, kind="user")
    r = await client.get(
        "/v1/brain/events?limit=50",
        headers={"X-Team-Scope": team_a.slug},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    returned_ids = {item["entity_id"] for item in body["items"]}
    for et, eid in seeded.items():
        assert eid in returned_ids, f"seeded {et}={eid} missing from response"


# ── 2. entity_type filter narrows results ───────────────────────────


async def test_entity_type_filter(client, session, seeded_two_teams):
    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]
    await _seed_three_distinct(
        session, team_slug=team_a.slug, owner_id=alice.id
    )

    _install_principal_override(alice, kind="user")
    r = await client.get(
        "/v1/brain/events?entity_type=task&limit=50",
        headers={"X-Team-Scope": team_a.slug},
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert items, "task filter returned nothing despite seeded row"
    assert all(it["entity_type"] == "task" for it in items)


# ── 3. Cursor pagination round-trip ──────────────────────────────────


async def test_cursor_pagination_no_overlap(client, session, seeded_two_teams):
    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]
    await _seed_three_distinct(
        session, team_slug=team_a.slug, owner_id=alice.id
    )

    _install_principal_override(alice, kind="user")
    r1 = await client.get(
        "/v1/brain/events?limit=1",
        headers={"X-Team-Scope": team_a.slug},
    )
    assert r1.status_code == 200
    page1 = r1.json()
    assert len(page1["items"]) == 1
    cursor = page1["next_cursor"]
    assert cursor, "expected a next_cursor when more rows are available"

    r2 = await client.get(
        f"/v1/brain/events?limit=10&cursor={cursor}",
        headers={"X-Team-Scope": team_a.slug},
    )
    assert r2.status_code == 200
    page2 = r2.json()

    ids1 = {it["entity_id"] for it in page1["items"]}
    ids2 = {it["entity_id"] for it in page2["items"]}
    assert not (ids1 & ids2), (
        f"pages overlap: page1={ids1} page2={ids2} — cursor walked backwards "
        f"or duplicated a row"
    )
    # The 3 seeded rows + (possibly) other rows from the conftest. We
    # don't assert exact count here, only that the union grows.
    assert len(page2["items"]) >= 2, (
        f"expected at least the 2 remaining seeded rows on page 2, got "
        f"{len(page2['items'])}"
    )


# ── 4. Soft-delete: hidden by default, exposed with include_deleted ──


async def test_soft_delete_visibility(client, session, seeded_two_teams):
    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]
    seeded = await _seed_three_distinct(
        session, team_slug=team_a.slug, owner_id=alice.id
    )
    # Soft-delete the task directly in DB — bypass any endpoint logic
    # so the test isolates the view filter, not the endpoint.
    await session.execute(
        sa.text(
            "UPDATE tasks SET deleted_at = now() WHERE id = :id"
        ),
        {"id": seeded["task"]},
    )
    await session.commit()

    _install_principal_override(alice, kind="user")
    r1 = await client.get(
        "/v1/brain/events?limit=50",
        headers={"X-Team-Scope": team_a.slug},
    )
    assert r1.status_code == 200
    ids_default = {it["entity_id"] for it in r1.json()["items"]}
    assert seeded["task"] not in ids_default, (
        "soft-deleted task surfaced in default list — deleted_at IS NULL "
        "filter is missing"
    )

    r2 = await client.get(
        "/v1/brain/events?include_deleted=true&limit=50",
        headers={"X-Team-Scope": team_a.slug},
    )
    assert r2.status_code == 200
    ids_with_deleted = {it["entity_id"] for it in r2.json()["items"]}
    assert seeded["task"] in ids_with_deleted, (
        "include_deleted=true should surface the soft-deleted task"
    )


# ── 5. Non-member → 403 ──────────────────────────────────────────────


async def test_non_member_gets_403(client, session, seeded_two_teams):
    team_a = seeded_two_teams["team_a"]
    bob = seeded_two_teams["bob"]  # bob is admin of team-b, NOT a member of team-a

    _install_principal_override(bob, kind="user")
    r = await client.get(
        "/v1/brain/events?limit=50",
        headers={"X-Team-Scope": team_a.slug},
    )
    assert r.status_code == 403, r.text


# ── 6. Bridge JWT (kind=bridge) matching team_scope → 200 ────────────


async def test_bridge_jwt_with_matching_scope(client, seeded_two_teams):
    """Bridge JWTs are accepted by get_team_scope when the JWT's
    team_scope claim matches the X-Team-Scope header.

    Uses a real bridge JWT (not an override) so the test exercises the
    full get_current_principal → get_team_scope path for bridge —
    matching the production trust boundary for service callers.
    """
    import os

    app, get_current_principal = _get_app_and_dep()
    # No principal override here — we want the real verify_bridge_jwt
    # path. Just make sure the conftest get_session override is still
    # the one in place (the `client` fixture installs it).
    app.dependency_overrides.pop(get_current_principal, None)

    team_a = seeded_two_teams["team_a"]
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
        "/v1/brain/events?limit=50",
        headers={
            "X-Team-Scope": team_a.slug,
            "Authorization": f"Bearer {token}",
        },
    )
    assert r.status_code == 200, r.text


# ── 7. Cursor tie-break on identical created_at ──────────────────────


async def test_cursor_tie_break_identical_timestamps(client, session, seeded_two_teams):
    """Three tasks with the exact same created_at, paged with limit=2.

    Page 1 returns 2 rows + cursor; page 2 returns the remaining 1
    row. Union = 3, no overlap. This exercises the
    `(created_at, entity_type, entity_id) < (:c_ts, :c_et, :c_id)`
    tuple comparison — splitting it into ANDed scalar comparisons would
    skip or duplicate rows when the timestamp ties.
    """
    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]
    ids = await _seed_identical_timestamp_tasks(
        session, team_slug=team_a.slug, owner_id=alice.id, n=3
    )
    assert len(ids) == 3

    _install_principal_override(alice, kind="user")
    r1 = await client.get(
        "/v1/brain/events?entity_type=task&limit=2",
        headers={"X-Team-Scope": team_a.slug},
    )
    assert r1.status_code == 200, r1.text
    page1 = r1.json()
    assert len(page1["items"]) == 2, (
        f"expected exactly 2 tasks on page 1, got {len(page1['items'])}"
    )
    cursor = page1["next_cursor"]
    assert cursor, "expected a next_cursor when more tied rows remain"

    r2 = await client.get(
        f"/v1/brain/events?entity_type=task&limit=2&cursor={cursor}",
        headers={"X-Team-Scope": team_a.slug},
    )
    assert r2.status_code == 200, r2.text
    page2 = r2.json()

    ids1 = {it["entity_id"] for it in page1["items"]}
    ids2 = {it["entity_id"] for it in page2["items"]}
    assert not (ids1 & ids2), (
        f"pages overlap on identical timestamps: page1={ids1} page2={ids2} — "
        f"tuple cursor comparison broken"
    )
    # The seeded rows must all appear across the two pages.
    seeded_set = set(ids)
    assert seeded_set <= (ids1 | ids2), (
        f"seeded ids {seeded_set} not fully covered by union of pages "
        f"{ids1 | ids2} — cursor skipped a row"
    )
    # Specifically the 3rd row lands on page 2 (or earlier if page 1
    # happened to start with it, which is allowed by the secondary sort
    # on entity_id) — assert at least one seeded id is on page 2.
    assert seeded_set & ids2, (
        f"none of the seeded ids landed on page 2 — pagination is "
        f"silently truncating instead of advancing"
    )


# ── 8. Google OIDC ("user") author sees their own row ────────────────


async def test_author_via_google_user_principal(client, session, seeded_two_teams):
    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]
    seeded = await _seed_three_distinct(
        session, team_slug=team_a.slug, owner_id=alice.id
    )

    # alice is the team-a admin (per conftest seed) AND the task author.
    _install_principal_override(alice, kind="user")
    r = await client.get(
        "/v1/brain/events?entity_type=task&limit=50",
        headers={"X-Team-Scope": team_a.slug},
    )
    assert r.status_code == 200, r.text
    ids = {it["entity_id"] for it in r.json()["items"]}
    assert seeded["task"] in ids


# ── 9. GitHub gho_ (same user.id) sees the row ───────────────────────


async def test_author_via_github_gho_principal(client, session, seeded_two_teams):
    """Phase 10 unified Google and GitHub identities under the same user
    row. Surfacing the principal as GitHub ('sub'='github:alice') must
    still resolve to alice's user.id, so the list endpoint and the
    helper see her as the author of her seeded task.
    """
    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]
    seeded = await _seed_three_distinct(
        session, team_slug=team_a.slug, owner_id=alice.id
    )

    # Override the sub claim to a GitHub-flavoured value while keeping
    # the same user.id — that's exactly the Phase-10 auth-merge invariant.
    _install_principal_override(
        alice, kind="user", sub_override="github:alice-login"
    )
    r = await client.get(
        "/v1/brain/events?entity_type=task&limit=50",
        headers={"X-Team-Scope": team_a.slug},
    )
    assert r.status_code == 200, r.text
    ids = {it["entity_id"] for it in r.json()["items"]}
    assert seeded["task"] in ids, (
        "GitHub-principal author should see her own task — Phase 10 "
        "auth-merge regression?"
    )


# ── 10. xbt_ token (user_api_token kind) sees the row ────────────────


async def test_author_via_xbt_user_api_token_principal(
    client, session, seeded_two_teams
):
    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]
    seeded = await _seed_three_distinct(
        session, team_slug=team_a.slug, owner_id=alice.id
    )

    # Scoped single-team xbt_ — early-return path in get_team_scope after
    # the scope match (this exercises the user_api_token kind specifically).
    _install_principal_override(
        alice,
        kind="user_api_token",
        api_token_team_scope=team_a.slug,
    )
    r = await client.get(
        "/v1/brain/events?entity_type=task&limit=50",
        headers={"X-Team-Scope": team_a.slug},
    )
    assert r.status_code == 200, r.text
    ids = {it["entity_id"] for it in r.json()["items"]}
    assert seeded["task"] in ids
