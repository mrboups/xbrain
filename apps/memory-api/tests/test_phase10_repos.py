"""Unit tests for Phase 10 repo helpers + atomic merge.

Uses the same testcontainers-Postgres + session fixture as test_team_isolation.py.
Tests are marked @pytest.mark.integration — skipped automatically if Docker absent.
"""

import hashlib
from datetime import datetime
from uuid import uuid4

import pytest
import sqlalchemy as sa

from app.repos import teams as teams_repo
from app.repos import users as users_repo
from app.repos.merge import merge_user_rows

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_block_unblock_roundtrip(session, seeded_two_teams):
    """block_member sets blocked_at to a real datetime; unblock_member clears it.

    Phase 10 M-1: the returned blocked_at MUST be a `datetime` instance (not a
    SQL expression object) so callers can call `.isoformat()` on it.
    """
    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]
    bob = seeded_two_teams["bob"]
    # Bob joins team A first.
    await teams_repo.add_member(session, team_id=team_a.id, user_id=bob.id, role="member")
    await session.flush()

    blocked = await teams_repo.block_member(
        session, team_id=team_a.id, user_id=bob.id, blocked_by=alice.id
    )
    assert blocked is not None
    assert blocked.blocked_at is not None
    # M-1 guard: must be a real datetime; isoformat() must succeed.
    assert isinstance(blocked.blocked_at, datetime), (
        f"blocked_at is {type(blocked.blocked_at)} — expected datetime "
        "(see Phase 10 M-1: do NOT use sa.func.now())"
    )
    iso = blocked.blocked_at.isoformat()
    assert "T" in iso
    assert blocked.blocked_by == alice.id

    unblocked = await teams_repo.unblock_member(
        session, team_id=team_a.id, user_id=bob.id
    )
    assert unblocked is not None
    assert unblocked.blocked_at is None
    assert unblocked.blocked_by is None


async def test_block_member_nonexistent_returns_none(session, seeded_two_teams):
    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]
    fake_user = uuid4()
    result = await teams_repo.block_member(
        session, team_id=team_a.id, user_id=fake_user, blocked_by=alice.id
    )
    assert result is None


async def test_org_block_roundtrip(session, seeded_two_teams):
    team_a = seeded_two_teams["team_a"]
    alice = seeded_two_teams["alice"]
    assert await teams_repo.is_org_blocked(
        session, team_id=team_a.id, github_login="evilbot"
    ) is False
    await teams_repo.add_org_block(
        session, team_id=team_a.id, github_login="evilbot", blocked_by=alice.id
    )
    assert await teams_repo.is_org_blocked(
        session, team_id=team_a.id, github_login="evilbot"
    ) is True
    # Idempotency: second add is no-op.
    await teams_repo.add_org_block(
        session, team_id=team_a.id, github_login="evilbot", blocked_by=alice.id
    )
    blocks = await teams_repo.list_org_blocks(session, team_id=team_a.id)
    assert len(blocks) == 1
    # Removal.
    await teams_repo.remove_org_block(
        session, team_id=team_a.id, github_login="evilbot"
    )
    assert await teams_repo.is_org_blocked(
        session, team_id=team_a.id, github_login="evilbot"
    ) is False


async def test_find_user_by_github_id_excludes_merged(session):
    """A merged orphan must not be returned — survivor lookup is via the
    survivor's own row (after follow_merge_pointer)."""
    orphan = await users_repo.get_or_create_user(
        session, source_user_id="github:ghonly", email="g@noreply", github_id=42
    )
    found = await users_repo.find_user_by_github_id(session, 42)
    assert found is not None
    assert found.id == orphan.id

    # Make survivor + simulate merge.
    survivor = await users_repo.get_or_create_user(
        session, source_user_id="google:abc", email="real@user.com"
    )
    # CRITICAL ordering (mirrors the 10-02 B-2 fix): clear orphan.github_id FIRST,
    # flush, then assign on survivor. Otherwise the unique index on github_id
    # fires UniqueViolationError at flush time.
    orphan.github_id = None
    await session.flush()
    survivor.github_id = 42
    orphan.merged_into_user_id = survivor.id
    await session.flush()

    found = await users_repo.find_user_by_github_id(session, 42)
    assert found is not None
    assert found.id == survivor.id


async def test_follow_merge_pointer_idempotent(session, seeded_two_teams):
    alice = seeded_two_teams["alice"]
    survivor = await users_repo.follow_merge_pointer(session, alice)
    assert survivor.id == alice.id


async def test_merge_user_rows_migrates_team_memberships(session, seeded_two_teams):
    """Orphan member in team_a → merge → survivor inherits membership."""
    team_a = seeded_two_teams["team_a"]
    orphan = await users_repo.get_or_create_user(
        session, source_user_id="github:dup", email="dup@noreply"
    )
    survivor = await users_repo.get_or_create_user(
        session, source_user_id="google:dup", email="dup@real.com"
    )
    await teams_repo.add_member(session, team_id=team_a.id, user_id=orphan.id, role="member")
    await session.flush()

    await merge_user_rows(session, orphan_id=orphan.id, survivor_id=survivor.id)
    await session.flush()

    # Orphan no longer has team_members row.
    orphan_membership = await teams_repo.get_membership(
        session, user_id=orphan.id, team_slug=team_a.slug
    )
    assert orphan_membership is None
    # Survivor has the membership.
    survivor_membership = await teams_repo.get_membership(
        session, user_id=survivor.id, team_slug=team_a.slug
    )
    assert survivor_membership is not None
    assert survivor_membership.role == "member"
    # Orphan is soft-deleted.
    refreshed = await users_repo.get_user_by_id(session, orphan.id)
    assert refreshed is not None
    assert refreshed.merged_into_user_id == survivor.id


async def test_merge_admin_role_wins_on_conflict(session, seeded_two_teams):
    """Orphan admin + survivor member in same team → merged role is admin."""
    team_a = seeded_two_teams["team_a"]
    orphan = await users_repo.get_or_create_user(
        session, source_user_id="github:adm", email="adm@noreply"
    )
    survivor = await users_repo.get_or_create_user(
        session, source_user_id="google:adm", email="adm@real.com"
    )
    await teams_repo.add_member(session, team_id=team_a.id, user_id=orphan.id, role="admin")
    await teams_repo.add_member(session, team_id=team_a.id, user_id=survivor.id, role="member")
    await session.flush()

    await merge_user_rows(session, orphan_id=orphan.id, survivor_id=survivor.id)
    await session.flush()

    survivor_membership = await teams_repo.get_membership(
        session, user_id=survivor.id, team_slug=team_a.slug
    )
    assert survivor_membership is not None
    assert survivor_membership.role == "admin"


async def test_merge_is_idempotent(session, seeded_two_teams):
    """Calling merge twice on the same pair is safe."""
    orphan = await users_repo.get_or_create_user(
        session, source_user_id="github:idem", email="i@noreply"
    )
    survivor = await users_repo.get_or_create_user(
        session, source_user_id="google:idem", email="i@real.com"
    )
    await session.flush()
    await merge_user_rows(session, orphan_id=orphan.id, survivor_id=survivor.id)
    await session.flush()
    # Second call: orphan.merged_into_user_id is already set → early return.
    await merge_user_rows(session, orphan_id=orphan.id, survivor_id=survivor.id)
    refreshed = await users_repo.get_user_by_id(session, orphan.id)
    assert refreshed is not None
    assert refreshed.merged_into_user_id == survivor.id


async def test_merge_migrates_api_tokens(session, seeded_two_teams):
    """Phase 10 M-3 — xbt_ token rows minted for the orphan MUST be re-pointed
    to the survivor during merge. Otherwise the orphan's pre-merge token
    continues to resolve to a soft-deleted row, breaking auth post-merge.

    See test_orphan_token_lands_on_survivor in plan 10-06 for the end-to-end
    HTTP-layer assertion. This test is the SQL-layer guard.

    Note: user_api_tokens.team_scope is NOT NULL today (per migration 0013),
    so the row is inserted with the seeded team_a.slug as a placeholder. The
    point of the test is to assert user_id re-parenting; the team_scope value
    is irrelevant to that assertion.
    """
    team_a = seeded_two_teams["team_a"]
    orphan = await users_repo.get_or_create_user(
        session, source_user_id="github:tokorph", email="tokorph@noreply"
    )
    survivor = await users_repo.get_or_create_user(
        session, source_user_id="google:tokorph", email="tokorph@real.com"
    )
    await session.flush()

    # Mint a fake xbt_ token row for the orphan (mirrors _mint_xbt_for_user
    # from 10-02 — token_hash is a sha256 of arbitrary bytes; the value is
    # irrelevant to this test, only the user_id mapping matters).
    fake_hash = hashlib.sha256(b"orphan_test_token").hexdigest()
    token_row = (await session.execute(sa.text("""
        INSERT INTO user_api_tokens (id, user_id, token_hash, team_scope, name, created_at)
        VALUES (gen_random_uuid(), :user_id, :hash, :team_scope, 'merge-test', now())
        RETURNING id
    """), {
        "user_id": orphan.id,
        "hash": fake_hash,
        "team_scope": team_a.slug,
    })).scalar()
    assert token_row is not None
    await session.flush()

    # Sanity — token currently points at orphan.
    pre_user_id = (await session.execute(sa.text(
        "SELECT user_id FROM user_api_tokens WHERE id = :id"
    ), {"id": token_row})).scalar()
    assert pre_user_id == orphan.id

    await merge_user_rows(session, orphan_id=orphan.id, survivor_id=survivor.id)
    await session.flush()

    # Post-merge: same token row now points at the survivor.
    post_user_id = (await session.execute(sa.text(
        "SELECT user_id FROM user_api_tokens WHERE id = :id"
    ), {"id": token_row})).scalar()
    assert post_user_id == survivor.id, (
        f"user_api_tokens.user_id was not re-parented: {post_user_id} != {survivor.id}"
    )
