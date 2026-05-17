"""Migration 0019 schema lock — Phase 12 GHAPP-03 + GHAPP-05.

Locks the contract of the `installations` table and the 4 new `users` token
columns introduced by `0019_github_app_install`. Without these tests:

  - a downstream migration could silently drop the partial unique index on
    (github_org_login WHERE revoked_at IS NULL), allowing two concurrent
    active installs on the same org and a hard-to-debug race in webhook
    de-dup logic.
  - a CHECK constraint regression on github_account_type could let a typo
    like 'organisation' (British spelling) creep through and bypass the
    intended 'Organization' | 'User' shortlist.
  - a NOT NULL added to the new users token columns later would break the
    "user has not yet linked GitHub" sign-in flow.

Locked invariants (5 assertions matching Plan 12-01 Section 3 Task 4):

  1. `alembic_version.version_num == '0019_github_app_install'` after
     `alembic upgrade head`.
  2. `information_schema.columns` contains the 4 new `users` columns AND
     all 10 `installations` columns. Plan 12-06 will extend users to 5
     new cols — update the assertion list at that point.
  3. INSERT into `installations` with `github_account_type='InvalidType'`
     raises IntegrityError (the CHECK constraint fires).
  4. The partial unique index allows two rows for the same `github_org_login`
     when one has `revoked_at IS NOT NULL` (re-install audit trail).
  5. INSERT into `users` without providing any of the 4 new token columns
     succeeds — they are nullable, as the "user hasn't connected GitHub"
     baseline state.

Skipped automatically when Docker is unavailable — same gate as the other
migration smoke tests (see conftest.py::pg_url).
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# Columns this migration adds to the `users` table. Plan 12-06 will extend
# with `github_access_token_hash` (M-5 fix). When that lands, append it to
# this list AND bump the count assertion in test_0019_users_token_columns_present.
EXPECTED_NEW_USER_COLUMNS: list[str] = [
    "github_access_token_enc",
    "github_refresh_token_enc",
    "github_token_expires_at",
    "github_refresh_expires_at",
]

# All 10 columns of the new `installations` table.
EXPECTED_INSTALLATIONS_COLUMNS: list[str] = [
    "installation_id",
    "github_org_login",
    "github_account_type",
    "installed_at",
    "installed_by_github_id",
    "permissions",
    "suspended_at",
    "revoked_at",
    "raw_payload",
    "updated_at",
]


async def test_0019_alembic_head_is_current(session) -> None:
    """After conftest's `alembic upgrade head`, the head MUST be 0019_github_app_install."""
    head = (
        await session.execute(sa.text("SELECT version_num FROM alembic_version"))
    ).scalar_one()
    assert head == "0019_github_app_install", (
        f"alembic head should be 0019_github_app_install after upgrade, got {head!r}. "
        "Did a later migration land without updating this test?"
    )


async def test_0019_users_token_columns_present(session) -> None:
    """4 new nullable token columns MUST exist on users after the migration."""
    rows = (
        await session.execute(
            sa.text(
                """
                SELECT column_name, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'users'
                  AND column_name = ANY(:cols)
                """
            ),
            {"cols": EXPECTED_NEW_USER_COLUMNS},
        )
    ).fetchall()
    found = {r.column_name for r in rows}
    missing = set(EXPECTED_NEW_USER_COLUMNS) - found
    assert not missing, (
        f"Migration 0019 left {len(missing)} user token columns unapplied: "
        f"{sorted(missing)}. Did a downstream migration drop them?"
    )
    assert len(found) == 4, (
        f"Expected exactly 4 new user token cols; got {len(found)}. "
        "If Plan 12-06 (M-5 fix) has landed, update EXPECTED_NEW_USER_COLUMNS."
    )
    # All four MUST be nullable — sign-in flow inserts a user row long before
    # they ever link GitHub. A NOT NULL would break first-login.
    for r in rows:
        assert r.is_nullable == "YES", (
            f"users.{r.column_name} must be nullable for the unlinked-user "
            f"baseline; got is_nullable={r.is_nullable!r}"
        )


async def test_0019_installations_columns_present(session) -> None:
    """All 10 installations columns MUST exist after the migration."""
    rows = (
        await session.execute(
            sa.text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'installations'
                """
            )
        )
    ).fetchall()
    found = {r.column_name for r in rows}
    missing = set(EXPECTED_INSTALLATIONS_COLUMNS) - found
    assert not missing, (
        f"installations table is missing {sorted(missing)} after 0019. "
        "Migration regression or table creation skipped?"
    )


async def test_0019_account_type_check_constraint_rejects_bogus(session) -> None:
    """INSERT installations with bogus account_type → IntegrityError on the CHECK.

    The constraint is `installations_account_type_check`: account_type IN
    ('Organization', 'User'). British spelling 'Organisation' MUST fail —
    GitHub's webhook payload uses American spelling and we want to catch
    typos at the DB layer rather than silently inserting unreachable rows.
    """
    with pytest.raises(IntegrityError) as excinfo:
        await session.execute(
            sa.text(
                """
                INSERT INTO installations
                  (installation_id, github_org_login, github_account_type)
                VALUES (:iid, :org, 'InvalidType')
                """
            ),
            {"iid": 9001, "org": "test-bogus-type"},
        )
    msg = str(excinfo.value).lower()
    assert "installations_account_type_check" in msg, (
        f"Expected CHECK constraint 'installations_account_type_check' to "
        f"fire on bogus account_type; got: {msg!r}"
    )


async def test_0019_partial_unique_index_allows_revoked_dup(session) -> None:
    """Partial unique index permits a fresh install after the prior one is revoked.

    Spec (Plan 12-01):
      - INSERT installation_id=1, org='testorg', revoked_at=NULL → OK
      - INSERT installation_id=2, org='testorg', revoked_at=NULL → MUST fail
      - UPDATE row 1 SET revoked_at=now()
      - INSERT installation_id=2, org='testorg', revoked_at=NULL → OK now

    This is the audit-preserving re-install pattern: GitHub deletes-and-creates
    on re-install with a new installation_id, and the old row must remain as
    audit. The partial WHERE clause is what makes the two rows compatible.
    """
    await session.execute(
        sa.text(
            "INSERT INTO installations (installation_id, github_org_login) "
            "VALUES (:iid, :org)"
        ),
        {"iid": 8001, "org": "testorg-0019"},
    )
    # Same org_login, no revoked_at on either → unique violation.
    with pytest.raises(IntegrityError) as excinfo:
        await session.execute(
            sa.text(
                "INSERT INTO installations (installation_id, github_org_login) "
                "VALUES (:iid, :org)"
            ),
            {"iid": 8002, "org": "testorg-0019"},
        )
    msg = str(excinfo.value).lower()
    assert "idx_installations_org_login_active" in msg or "unique" in msg, (
        f"Expected partial unique violation; got: {msg!r}"
    )
    # Recover the session from the rolled-back IntegrityError state.
    await session.rollback()

    # Now soft-delete the first row by setting revoked_at, then the same org
    # MUST accept a fresh installation_id with revoked_at IS NULL.
    await session.execute(
        sa.text(
            "UPDATE installations SET revoked_at = now() WHERE installation_id = :iid"
        ),
        {"iid": 8001},
    )
    await session.execute(
        sa.text(
            "INSERT INTO installations (installation_id, github_org_login) "
            "VALUES (:iid, :org)"
        ),
        {"iid": 8002, "org": "testorg-0019"},
    )
    # If we got here the partial WHERE clause is correct.
    count = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM installations WHERE github_org_login = :org"
            ),
            {"org": "testorg-0019"},
        )
    ).scalar_one()
    assert count == 2, (
        f"After revoke+reinstall, expected 2 rows for the same org_login (one "
        f"revoked, one active); got {count}"
    )


async def test_0019_users_token_columns_default_null(session) -> None:
    """INSERT a user without the 4 new token columns → all NULL by default.

    Pin the "user has not yet linked GitHub via the App" baseline. Per
    MINOR-1 (REVISION 2), use the exact row shape that matches users-table
    NOT NULL constraints: source_user_id + email are required, display_name
    is nullable so omitted.
    """
    await session.execute(
        sa.text(
            """
            INSERT INTO users (id, source_user_id, email)
            VALUES (gen_random_uuid(), :sid, :email)
            """
        ),
        {"sid": "test:0019:smoke", "email": "smoke-0019@x.io"},
    )
    row = (
        await session.execute(
            sa.text(
                """
                SELECT github_access_token_enc,
                       github_refresh_token_enc,
                       github_token_expires_at,
                       github_refresh_expires_at
                FROM users
                WHERE source_user_id = :sid
                """
            ),
            {"sid": "test:0019:smoke"},
        )
    ).first()
    assert row is not None, "smoke user row not found — INSERT silently dropped?"
    assert row[0] is None, "github_access_token_enc default must be NULL"
    assert row[1] is None, "github_refresh_token_enc default must be NULL"
    assert row[2] is None, "github_token_expires_at default must be NULL"
    assert row[3] is None, "github_refresh_expires_at default must be NULL"
