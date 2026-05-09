"""Team repo — create team, manage memberships."""

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.team import Team, TeamMember


async def create_team(
    session: AsyncSession,
    *,
    slug: str,
    display_name: str,
    creator_user_id: UUID,
    description: str | None = None,
    visibility: str = "closed",
    github_org: str | None = None,
) -> Team:
    """Create a team and add the creator as admin (atomic)."""
    team = Team(
        slug=slug,
        display_name=display_name,
        description=description,
        visibility=visibility,
        github_org=github_org,
    )
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


async def search_teams(session: AsyncSession, *, query: str, limit: int = 10) -> list[Team]:
    """Case-insensitive search on slug and display_name. Returns up to `limit` results."""
    pattern = f"%{query.lower()}%"
    result = await session.execute(
        select(Team)
        .where(
            sa.or_(
                sa.func.lower(Team.slug).like(pattern),
                sa.func.lower(Team.display_name).like(pattern),
            )
        )
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_first_team_for_user(session: AsyncSession, *, user_id: UUID) -> Team | None:
    """Return the first team the user belongs to, or None."""
    result = await session.execute(
        select(Team)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(TeamMember.user_id == user_id)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_all_teams_for_user(session: AsyncSession, *, user_id: UUID) -> list[Team]:
    """Return all teams the user belongs to, ordered by slug."""
    result = await session.execute(
        select(Team)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(TeamMember.user_id == user_id)
        .order_by(Team.slug)
    )
    return list(result.scalars().all())


async def get_teams_with_github_org(session: AsyncSession) -> list[Team]:
    """Return all teams that have a github_org set."""
    result = await session.execute(
        select(Team).where(Team.github_org.isnot(None))
    )
    return list(result.scalars().all())


async def create_join_request(
    session: AsyncSession,
    *,
    team_id: UUID,
    user_id: UUID,
) -> "TeamJoinRequest":
    """Create a join request, or return existing one (idempotent)."""
    from app.models.team import TeamJoinRequest

    result = await session.execute(
        select(TeamJoinRequest).where(
            (TeamJoinRequest.team_id == team_id) & (TeamJoinRequest.user_id == user_id)
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing
    req = TeamJoinRequest(team_id=team_id, user_id=user_id, status="pending")
    session.add(req)
    await session.flush()
    return req


async def upsert_team_api_key(
    session: AsyncSession,
    *,
    team_id: UUID,
    provider: str,
    key_enc: str,
) -> "TeamApiKey":
    """Insert or replace an encrypted API key for (team_id, provider)."""
    from app.models.team import TeamApiKey

    result = await session.execute(
        select(TeamApiKey).where(
            (TeamApiKey.team_id == team_id) & (TeamApiKey.provider == provider)
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        existing.key_enc = key_enc
        await session.flush()
        return existing
    key = TeamApiKey(team_id=team_id, provider=provider, key_enc=key_enc)
    session.add(key)
    await session.flush()
    return key


async def list_team_api_keys(session: AsyncSession, *, team_id: UUID) -> list["TeamApiKey"]:
    from app.models.team import TeamApiKey

    result = await session.execute(
        select(TeamApiKey).where(TeamApiKey.team_id == team_id)
    )
    return list(result.scalars().all())
