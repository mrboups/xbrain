"""Team repo — create team, manage memberships."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.team import Team, TeamMember


async def create_team(
    session: AsyncSession,
    *,
    slug: str,
    display_name: str,
    creator_user_id: UUID,
) -> Team:
    """Create a team and add the creator as admin (atomic)."""
    team = Team(slug=slug, display_name=display_name)
    session.add(team)
    await session.flush()
    membership = TeamMember(team_id=team.id, user_id=creator_user_id, role="admin")
    session.add(membership)
    return team


async def get_team_by_slug(session: AsyncSession, slug: str) -> Team | None:
    result = await session.execute(select(Team).where(Team.slug == slug))
    return result.scalar_one_or_none()


async def get_team_by_id(session: AsyncSession, team_id: UUID) -> Team | None:
    result = await session.execute(select(Team).where(Team.id == team_id))
    return result.scalar_one_or_none()


async def add_member(
    session: AsyncSession,
    *,
    team_id: UUID,
    user_id: UUID,
    role: str,
) -> TeamMember:
    if role not in ("admin", "member"):
        raise ValueError(f"invalid role: {role}")
    member = TeamMember(team_id=team_id, user_id=user_id, role=role)
    session.add(member)
    await session.flush()
    return member


async def remove_member(session: AsyncSession, *, team_id: UUID, user_id: UUID) -> None:
    result = await session.execute(
        select(TeamMember).where(
            (TeamMember.team_id == team_id) & (TeamMember.user_id == user_id)
        )
    )
    member = result.scalar_one_or_none()
    if member is not None:
        await session.delete(member)


async def list_members(session: AsyncSession, *, team_id: UUID) -> list[TeamMember]:
    result = await session.execute(select(TeamMember).where(TeamMember.team_id == team_id))
    return list(result.scalars().all())


async def get_membership(
    session: AsyncSession,
    *,
    user_id: UUID,
    team_slug: str,
) -> TeamMember | None:
    """Return membership for (user, team) or None. Used by deps.get_team_scope."""
    result = await session.execute(
        select(TeamMember)
        .join(Team, Team.id == TeamMember.team_id)
        .where((TeamMember.user_id == user_id) & (Team.slug == team_slug))
    )
    return result.scalar_one_or_none()


async def list_teams_for_user(session: AsyncSession, *, user_id: UUID) -> list[Team]:
    result = await session.execute(
        select(Team).join(TeamMember, TeamMember.team_id == Team.id).where(TeamMember.user_id == user_id)
    )
    return list(result.scalars().all())
