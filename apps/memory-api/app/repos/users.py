"""User repo — UPSERT by source_user_id (Google OIDC sub)."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


async def get_or_create_user(
    session: AsyncSession,
    *,
    source_user_id: str,
    email: str,
    display_name: str | None = None,
    github_id: int | None = None,
) -> User:
    """UPSERT by source_user_id. Idempotent + race-safe.

    Two concurrent requests for the same `source_user_id` (e.g. two LibreChat
    messages from an anonymous session) used to both SELECT no-row, then both
    INSERT, and one would hit `UniqueViolationError`. We now issue an
    `INSERT ... ON CONFLICT (source_user_id) DO NOTHING` first, then SELECT
    the live row — guaranteed to return exactly one row either way.
    """
    stmt = (
        pg_insert(User)
        .values(
            source_user_id=source_user_id,
            email=email,
            display_name=display_name,
            github_id=github_id,
        )
        .on_conflict_do_nothing(index_elements=["source_user_id"])
    )
    await session.execute(stmt)
    await session.flush()

    result = await session.execute(
        select(User).where(User.source_user_id == source_user_id)
    )
    user = result.scalar_one()

    if email and user.email != email:
        user.email = email
    if display_name and user.display_name != display_name:
        user.display_name = display_name
    if github_id and user.github_id is None:
        user.github_id = github_id
    return user


async def get_user_by_id(session: AsyncSession, user_id: UUID) -> User | None:
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    """Case-insensitive email lookup. Returns the first match or None.

    Used by the invite-by-email flow (quick task 260513-tmu) — admins type
    an email, we look up the user record. If the invitee hasn't signed
    in yet (no row), the caller surfaces a "user not registered" error.
    """
    if not email:
        return None
    from sqlalchemy import func
    result = await session.execute(
        select(User).where(func.lower(User.email) == email.strip().lower())
    )
    return result.scalar_one_or_none()


async def find_user_by_github_id(
    session: AsyncSession, github_id: int
) -> User | None:
    """Return the active (non-merged) user row with this github_id, or None.

    Excludes rows where merged_into_user_id IS NOT NULL — those are soft-deleted
    orphans whose successor is referenced via merged_into_user_id.

    Use follow_merge_pointer() after this lookup if you need to handle the case
    where a previously-active row was just merged.
    """
    result = await session.execute(
        select(User).where(
            (User.github_id == github_id) & (User.merged_into_user_id.is_(None))
        )
    )
    return result.scalar_one_or_none()


async def follow_merge_pointer(session: AsyncSession, user: User) -> User:
    """If user.merged_into_user_id is set, return the survivor; else return user.

    Idempotent and safe to call on any User instance. Raises ValueError if the
    merge pointer references a deleted row (data-integrity error).
    """
    if user.merged_into_user_id is None:
        return user
    survivor = await get_user_by_id(session, user.merged_into_user_id)
    if survivor is None:
        raise ValueError(
            f"merge_pointer dangling: user {user.id} -> {user.merged_into_user_id}"
        )
    return survivor
