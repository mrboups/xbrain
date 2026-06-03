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

    Gathers EVERY candidate row the sub could denote — the exact
    ``source_user_id`` match AND the email-based match — then returns the first
    candidate that actually HAS a team. This guards against phantom
    ``*@bridged.local`` rows the bridge ingest path mints with a bare-email
    ``source_user_id`` and NO team: such a row matches ``sub`` exactly and, with
    a naive "first match wins" lookup, would shadow the real team-bearing row
    (keyed by the real email) and resolve to null. (2026-06-03 regression fix —
    see project-xbrain-librechat-team-scope-fix.)

    Candidates considered:
      1. Exact match on users.source_user_id == sub
      2. Email match — sub with the ``email:`` prefix stripped, or a bare ``@`` email
    Each candidate follows its merge pointer; the first one with a team wins.
    If none has a team, the first resolved user is returned (so callers still
    get a user_id), team_scope=null.

    Auth: any authenticated principal (including kind='bridge').
    Never returns 500 for unknown users — nulls are returned instead.
    """
    try:
        candidates: list[User] = []

        exact = (
            await session.execute(select(User).where(User.source_user_id == sub))
        ).scalar_one_or_none()
        if exact is not None:
            candidates.append(exact)

        # Derive an email to ALSO look up by. A phantom row may match `sub`
        # exactly but carry no team; the real team-bearing row is keyed by the
        # real email (e.g. sub="nicoboups@gmail.com" → phantom no-team row, while
        # the real row has source_user_id="email:nicoboups@gmail.com").
        email: str | None = None
        if sub.startswith("email:"):
            email = sub[len("email:"):]
        elif "@" in sub:
            email = sub
        if email:
            by_email = await get_user_by_email(session, email)
            if by_email is not None:
                candidates.append(by_email)

        # Resolve merge pointers and dedup by id, preserving discovery order.
        seen_ids: set[Any] = set()
        resolved: list[User] = []
        for cand in candidates:
            rc = await follow_merge_pointer(session, cand)
            if rc.id not in seen_ids:
                seen_ids.add(rc.id)
                resolved.append(rc)

        # Prefer the first candidate that actually has a team.
        chosen: User | None = None
        chosen_team = None
        for rc in resolved:
            team = await get_first_team_for_user(session, user_id=rc.id)
            if team is not None:
                chosen, chosen_team = rc, team
                break
        if chosen is None and resolved:
            chosen = resolved[0]

        result = {
            "team_scope": chosen_team.slug if chosen_team is not None else None,
            "user_id": str(chosen.id) if chosen is not None else None,
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
