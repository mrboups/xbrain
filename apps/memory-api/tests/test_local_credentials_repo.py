"""Integration tests — local_credentials repo (Phase 18 LAUTH-01, D-18-03).

Real Postgres via testcontainers (tests/conftest.py `session` + `seeded_two_teams`
fixtures). A SKIPPED run here (Docker absent) counts as a FAILURE per the
governing lesson from Phases 14-15: it proves nothing about whether the real
migration 0024 chain actually applies or the real table behaves as expected.
"""

from datetime import datetime, timezone

import pytest
import sqlalchemy as sa

from app.config import settings
from app.repos import local_credentials as creds_repo
from app.repos import users as users_repo

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_create_returns_true_and_row_is_readable(session, seeded_two_teams):
    alice = seeded_two_teams["alice"]
    created = await creds_repo.create(
        session, user_id=alice.id, password_hash="argon2id$fakehash"
    )
    assert created is True

    row = await creds_repo.get_by_user_id(session, alice.id)
    assert row is not None
    assert row["user_id"] == alice.id
    assert row["algo"] == "argon2id"
    assert row["failed_attempts"] == 0
    assert row["locked_until"] is None
    assert row["password_hash"] == "argon2id$fakehash"


async def test_create_conflict_returns_false_without_raising(session, seeded_two_teams):
    bob = seeded_two_teams["bob"]
    first = await creds_repo.create(session, user_id=bob.id, password_hash="hash-1")
    assert first is True
    second = await creds_repo.create(session, user_id=bob.id, password_hash="hash-2")
    assert second is False
    # Row untouched by the failed second insert (ON CONFLICT DO NOTHING).
    row = await creds_repo.get_by_user_id(session, bob.id)
    assert row["password_hash"] == "hash-1"


async def test_get_by_email_case_insensitive(session):
    user = await users_repo.get_or_create_user(
        session, source_user_id="email:carol@test.local", email="Carol@Test.LOCAL"
    )
    await creds_repo.create(session, user_id=user.id, password_hash="hash")

    found = await creds_repo.get_by_email(session, "carol@test.local")
    assert found is not None
    assert found["user_id"] == user.id

    found_upper = await creds_repo.get_by_email(session, "CAROL@TEST.LOCAL")
    assert found_upper is not None
    assert found_upper["user_id"] == user.id


async def test_get_by_email_no_match_returns_none(session):
    missing = await creds_repo.get_by_email(session, "nobody@nowhere.example")
    assert missing is None


async def test_record_failure_locks_after_threshold(session, seeded_two_teams):
    alice = seeded_two_teams["alice"]
    await creds_repo.create(session, user_id=alice.id, password_hash="hash")

    row = None
    for _ in range(settings.LOCAL_AUTH_MAX_FAILED_ATTEMPTS):
        row = await creds_repo.record_failure(session, alice.id)

    assert row is not None
    assert row["failed_attempts"] == settings.LOCAL_AUTH_MAX_FAILED_ATTEMPTS
    assert row["locked_until"] is not None
    assert row["locked_until"] > datetime.now(timezone.utc)


async def test_record_failure_below_threshold_does_not_lock(session, seeded_two_teams):
    bob = seeded_two_teams["bob"]
    await creds_repo.create(session, user_id=bob.id, password_hash="hash")

    row = await creds_repo.record_failure(session, bob.id)
    assert row["failed_attempts"] == 1
    assert row["locked_until"] is None


async def test_reset_failures_clears_lockout(session, seeded_two_teams):
    alice = seeded_two_teams["alice"]
    await creds_repo.create(session, user_id=alice.id, password_hash="hash")
    for _ in range(settings.LOCAL_AUTH_MAX_FAILED_ATTEMPTS):
        await creds_repo.record_failure(session, alice.id)

    await creds_repo.reset_failures(session, alice.id)

    row = await creds_repo.get_by_user_id(session, alice.id)
    assert row["failed_attempts"] == 0
    assert row["locked_until"] is None


async def test_update_hash_replaces_password(session, seeded_two_teams):
    bob = seeded_two_teams["bob"]
    await creds_repo.create(session, user_id=bob.id, password_hash="old-hash")
    await creds_repo.update_hash(session, bob.id, "new-hash")

    row = await creds_repo.get_by_user_id(session, bob.id)
    assert row["password_hash"] == "new-hash"


async def test_upsert_inserts_then_updates(session, seeded_two_teams):
    alice = seeded_two_teams["alice"]
    await creds_repo.upsert(session, user_id=alice.id, password_hash="first")
    row = await creds_repo.get_by_user_id(session, alice.id)
    assert row["password_hash"] == "first"

    await creds_repo.upsert(session, user_id=alice.id, password_hash="second")
    row = await creds_repo.get_by_user_id(session, alice.id)
    assert row["password_hash"] == "second"


async def test_cascade_delete_removes_credential_row(session):
    dave = await users_repo.get_or_create_user(
        session, source_user_id="email:dave@test.local", email="dave@test.local"
    )
    await creds_repo.create(session, user_id=dave.id, password_hash="hash")
    assert await creds_repo.get_by_user_id(session, dave.id) is not None

    await session.execute(sa.text("DELETE FROM users WHERE id = :id"), {"id": dave.id})
    await session.flush()

    assert await creds_repo.get_by_user_id(session, dave.id) is None
