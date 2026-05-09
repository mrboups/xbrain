"""User repo — UPSERT by source_user_id (Google OIDC sub)."""

from uuid import UUID

from sqlalchemy import select
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
    """UPSERT by source_user_id. Idempotent — safe to call on every authenticated request."""
    result = await session.execute(select(User).where(User.source_user_id == source_user_id))
    user = result.scalar_one_or_none()
    if user is not None:
        if email and user.email != email:
            user.email = email
        if display_name and user.display_name != display_name:
            user.display_name = display_name
        if github_id and user.github_id is None:
            user.github_id = github_id
        return user
    user = User(source_user_id=source_user_id, email=email, display_name=display_name, github_id=github_id)
    session.add(user)
    await session.flush()
    return user


async def get_user_by_id(session: AsyncSession, user_id: UUID) -> User | None:
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()
