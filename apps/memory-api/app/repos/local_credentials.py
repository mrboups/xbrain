"""local_credentials repo — email/password data layer (Phase 18 LAUTH-01, D-18-03).

Targets the dedicated `local_credentials` table (migration 0024) rather than
new columns on `users`. All helpers flush() only; transaction ownership stays
with the caller, mirroring teams_repo.create_team (app/repos/teams.py).

Raw parameterized SQL via sa.text() is used throughout (no ORM model needed
for this table) — the same style already established by app/repos/merge.py
for tables that don't need full ORM mapping.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

_COLUMNS = (
    "user_id, password_hash, algo, failed_attempts, locked_until, "
    "created_at, updated_at"
)


async def create(
    session: AsyncSession,
    *,
    user_id: UUID,
    password_hash: str,
    algo: str = "argon2id",
) -> bool:
    """INSERT ... ON CONFLICT (user_id) DO NOTHING. Returns True iff inserted.

    A False return lets the register route (Plan 03) turn a concurrent
    duplicate into a clean 409 instead of a 500 IntegrityError.
    """
    result = await session.execute(
        sa.text(
            """
            INSERT INTO local_credentials (user_id, password_hash, algo)
            VALUES (:user_id, :password_hash, :algo)
            ON CONFLICT (user_id) DO NOTHING
            RETURNING user_id
            """
        ),
        {"user_id": user_id, "password_hash": password_hash, "algo": algo},
    )
    inserted = result.first() is not None
    await session.flush()
    return inserted


async def get_by_user_id(session: AsyncSession, user_id: UUID) -> dict[str, Any] | None:
    result = await session.execute(
        sa.text(f"SELECT {_COLUMNS} FROM local_credentials WHERE user_id = :user_id"),
        {"user_id": user_id},
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None


async def get_by_email(session: AsyncSession, email: str) -> dict[str, Any] | None:
    """Case-insensitive lookup against the canonical users.email column.

    Never a constructed source_user_id (research Pitfalls 1+2) — this JOINs
    the real users.email, the only correct collision/lookup key.
    """
    result = await session.execute(
        sa.text(
            """
            SELECT lc.user_id, lc.password_hash, lc.algo, lc.failed_attempts,
                   lc.locked_until, lc.created_at, lc.updated_at
            FROM local_credentials lc
            JOIN users u ON u.id = lc.user_id
            WHERE lower(u.email) = lower(:email)
            """
        ),
        {"email": email.strip()},
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None


async def record_failure(session: AsyncSession, user_id: UUID) -> dict[str, Any] | None:
    """Increment failed_attempts; lock when the threshold is reached.

    The CASE branch reads `failed_attempts` (the pre-update value, since a
    Postgres UPDATE's SET expressions all see the OLD row), so
    `failed_attempts + 1 >= :max_attempts` compares against the same count
    that becomes the new failed_attempts. Returns the updated row.
    """
    result = await session.execute(
        sa.text(
            f"""
            UPDATE local_credentials
            SET failed_attempts = failed_attempts + 1,
                locked_until = CASE
                    WHEN failed_attempts + 1 >= :max_attempts
                    THEN now() + make_interval(mins => :lockout_minutes)
                    ELSE locked_until
                END,
                updated_at = now()
            WHERE user_id = :user_id
            RETURNING {_COLUMNS}
            """
        ),
        {
            "user_id": user_id,
            "max_attempts": settings.LOCAL_AUTH_MAX_FAILED_ATTEMPTS,
            "lockout_minutes": settings.LOCAL_AUTH_LOCKOUT_MINUTES,
        },
    )
    row = result.mappings().first()
    await session.flush()
    return dict(row) if row is not None else None


async def reset_failures(session: AsyncSession, user_id: UUID) -> None:
    await session.execute(
        sa.text(
            """
            UPDATE local_credentials
            SET failed_attempts = 0, locked_until = NULL, updated_at = now()
            WHERE user_id = :user_id
            """
        ),
        {"user_id": user_id},
    )
    await session.flush()


async def update_hash(session: AsyncSession, user_id: UUID, password_hash: str) -> None:
    """Replace password_hash and bump updated_at (rehash-on-login, change-password)."""
    await session.execute(
        sa.text(
            """
            UPDATE local_credentials
            SET password_hash = :password_hash, updated_at = now()
            WHERE user_id = :user_id
            """
        ),
        {"user_id": user_id, "password_hash": password_hash},
    )
    await session.flush()


async def upsert(
    session: AsyncSession,
    *,
    user_id: UUID,
    password_hash: str,
    algo: str = "argon2id",
) -> None:
    """INSERT ... ON CONFLICT (user_id) DO UPDATE — set-password first-attach."""
    await session.execute(
        sa.text(
            """
            INSERT INTO local_credentials (user_id, password_hash, algo)
            VALUES (:user_id, :password_hash, :algo)
            ON CONFLICT (user_id) DO UPDATE
            SET password_hash = EXCLUDED.password_hash, updated_at = now()
            """
        ),
        {"user_id": user_id, "password_hash": password_hash, "algo": algo},
    )
    await session.flush()
