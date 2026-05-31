"""Internal service endpoints — not exposed to end users.

Currently exposes:
  GET /internal/resolve-team-scope?sub=<str>
    Resolves a LibreChat user sub (source_user_id, email:-prefixed, or bare email)
    to their primary team slug. Used by the librechat-bridge to replace its Phase-1
    hardcoded BRIDGE_DEFAULT_TEAM_SCOPE stub with the real per-user team.
"""

from typing import Any

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_current_principal, get_session
from app.models.user import User
from app.repos.teams import get_first_team_for_user
from app.repos.users import follow_merge_pointer, get_user_by_email

log = structlog.get_logger(__name__)
router = APIRouter()


@router.get("/internal/resolve-team-scope")
async def resolve_team_scope(
    sub: str,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str | None]:
    """Resolve a user sub to their primary team slug.

    Resolution order:
      1. Exact match on users.source_user_id == sub
      2. If sub starts with 'email:', strip prefix and look up by email
      3. If sub contains '@', look up by email
      4. Follow merge pointer if user was soft-merged into another row
      5. Return the user's first team slug (or null if unknown/no team)

    Auth: any authenticated principal (including kind='bridge').
    Never returns 500 for unknown users — nulls are returned instead.
    """
    try:
        user = (
            await session.execute(select(User).where(User.source_user_id == sub))
        ).scalar_one_or_none()

        if user is None and sub.startswith("email:"):
            user = await get_user_by_email(session, sub[len("email:"):])

        if user is None and "@" in sub:
            user = await get_user_by_email(session, sub)

        if user is not None:
            user = await follow_merge_pointer(session, user)

        team = (
            await get_first_team_for_user(session, user_id=user.id)
            if user is not None
            else None
        )

        result = {
            "team_scope": team.slug if team is not None else None,
            "user_id": str(user.id) if user is not None else None,
        }
        log.debug(
            "internal.resolve_team_scope",
            sub=sub,
            team_scope=result["team_scope"],
            user_id=result["user_id"],
        )
        return result

    except Exception as exc:
        # Never 500 on resolution failure — return nulls and log the error.
        log.warning("internal.resolve_team_scope.error", sub=sub, err=str(exc))
        return {"team_scope": None, "user_id": None}
