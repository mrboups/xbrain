"""Phase 10 GHA-02 — auto-grant team membership on GitHub sign-in.

For every team where `teams.github_org` matches one of the user's GitHub orgs,
add the user as `member` unless:
  - The user already has a row in team_members for that team (idempotent).
  - The (team_id, user.github_username) is in team_org_blocks (pre-block, GHA-04).
  - The user's existing team_members row is blocked_at IS NOT NULL.

Returns the list of newly-joined Team rows so the caller can emit notifications.
"""

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.team import Team
from app.models.user import User
from app.repos import teams as teams_repo
from app.services.notifications import send_member_autojoined_email

log = structlog.get_logger(__name__)


async def auto_grant_via_org_match(
    session: AsyncSession,
    *,
    user: User,
    github_login: str,
    github_org_logins: list[str],
) -> list[Team]:
    """Insert team_members rows for org-matched teams (idempotent).

    Notification emails are fired in a background task (does NOT block return).
    Returns the list of teams the user newly joined.
    """
    if not github_org_logins:
        return []

    # Find all teams with github_org IN user's orgs.
    result = await session.execute(
        sa.select(Team).where(Team.github_org.in_(github_org_logins))
    )
    candidate_teams = list(result.scalars().all())

    newly_joined: list[Team] = []
    for team in candidate_teams:
        # 1. Pre-block check (GHA-04).
        if await teams_repo.is_org_blocked(
            session, team_id=team.id, github_login=github_login
        ):
            log.info(
                "autograant.skip_org_blocked",
                team_slug=team.slug,
                github_login=github_login,
            )
            continue
        # 2. Already a member? (idempotent)
        existing = await teams_repo.get_membership(
            session, user_id=user.id, team_slug=team.slug
        )
        if existing is not None:
            # 3. Re-grant guard — previously blocked.
            if existing.blocked_at is not None:
                log.info(
                    "autograant.skip_existing_blocked",
                    team_slug=team.slug,
                    user_id=str(user.id),
                )
            continue
        # 4. INSERT membership.
        await teams_repo.add_member(
            session, team_id=team.id, user_id=user.id, role="member"
        )
        newly_joined.append(team)
        log.info(
            "autograant.granted",
            team_slug=team.slug,
            user_id=str(user.id),
            github_login=github_login,
        )

    return newly_joined


async def emit_autogrant_notifications(
    *,
    newly_joined: list[Team],
    new_member_login: str,
    new_member_display: str,
    session_factory,
) -> None:
    """Background fan-out — for each newly-joined team, query admins + email.

    Uses its OWN session (the sign-in request's session is already closed by
    the time this task runs).
    """
    for team in newly_joined:
        try:
            async with session_factory() as bg_session:
                admin_emails = await teams_repo.get_team_admins_emails(
                    bg_session, team_id=team.id
                )
            await send_member_autojoined_email(
                admin_emails=admin_emails,
                team_name=team.display_name,
                team_slug=team.slug,
                new_member_login=new_member_login,
                new_member_display=new_member_display,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "autograant.notify_failed",
                team_slug=team.slug,
                error=str(exc),
            )
