"""/v1/teams — admin-managed team CRUD + membership."""

from typing import Any
from uuid import UUID

import httpx
from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import write_audit
from app.auth import is_admin
from app.config import settings
from app.deps import get_current_principal, get_session
from app.models.team import Team  # forward-typing for _require_team_admin
from app.repos import teams as teams_repo
from app.repos import users as users_repo

router = APIRouter()


def _require_user(principal: dict[str, Any]):
    # Accept both kind=user (Google ID token / GitHub OAuth) AND
    # kind=user_api_token (xbt_). Both map to the same User row (the SimpleNamespace
    # built in deps.py for user_api_token exposes the same fields: id,
    # source_user_id, email, display_name, github_username, github_id).
    # Without this, the Chrome extension (which holds xbt_, not the Google ID
    # token) gets 403 on /v1/teams/my-teams, /v1/teams/self-solo, etc.
    kind = principal.get("kind")
    if kind not in ("user", "user_api_token"):
        raise HTTPException(403, "user-only endpoint")
    return principal["user"]


def _require_admin_user(principal: dict[str, Any]):
    user = _require_user(principal)
    if not is_admin(user.source_user_id):
        raise HTTPException(403, "admin-only endpoint")
    return user


async def _require_team_admin(
    principal: dict[str, Any],
    team: Team,
    session: AsyncSession,
):
    """Per-team admin check — caller must have role='admin' in this team's
    team_members (NOT the global env admin list).

    Used by invite/remove/manage member endpoints so a regular team admin
    can actually administer their team without being in ADMIN_USER_SUBS.
    Global env admins (is_admin) are also accepted as a backdoor for ops.
    """
    user = _require_user(principal)
    # Backdoor for ops — global env admins can manage any team.
    if is_admin(user.source_user_id):
        return user
    membership = await teams_repo.get_membership(
        session, user_id=user.id, team_slug=team.slug
    )
    if membership is None or membership.role != "admin":
        raise HTTPException(403, "team admin required")
    return user


def _get_fernet() -> Fernet:
    key = settings.FERNET_KEY or settings.OAUTH_CREDENTIALS_ENCRYPTION_KEY
    if not key:
        raise HTTPException(500, "FERNET_KEY not configured")
    return Fernet(key.encode())


class TeamCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")
    display_name: str = Field(..., min_length=1, max_length=256)


class TeamOut(BaseModel):
    id: str
    slug: str
    display_name: str


class MemberInviteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_user_id: str = Field(..., min_length=1)
    email: str = Field(..., min_length=1)
    display_name: str | None = None
    role: str = Field(default="member", pattern=r"^(admin|member)$")


class MemberInviteByEmailBody(BaseModel):
    """Invite-by-email body — used by the extension Settings + app-site UI
    where the admin only knows the invitee's email, not their OIDC sub.
    """
    model_config = ConfigDict(extra="forbid")
    email: str = Field(..., min_length=1, max_length=256)
    role: str = Field(default="member", pattern=r"^(admin|member)$")


class MemberOut(BaseModel):
    user_id: str
    role: str
    # Enriched fields (quick task 260513-tmu) — populated by list_members /
    # invite endpoints. Older clients ignore the extra keys.
    email: str | None = None
    display_name: str | None = None
    source_user_id: str | None = None
    github_username: str | None = None


class TeamSelfCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")
    display_name: str = Field(..., min_length=1, max_length=256)
    description: str | None = None
    visibility: str = Field(default="closed", pattern=r"^(open|closed)$")
    github_org: str | None = None


class TeamSearchOut(BaseModel):
    id: str
    slug: str
    display_name: str
    visibility: str
    github_org: str | None = None


class ApiKeyIn(BaseModel):
    provider: str = Field(..., min_length=1, max_length=64)
    api_key: str = Field(..., min_length=1)


class ApiKeysBulkBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keys: list[ApiKeyIn]


class ApiKeyOut(BaseModel):
    provider: str


class JoinRequestOut(BaseModel):
    status: str
    team_id: str


class GithubOrgOut(BaseModel):
    login: str
    description: str | None = None


@router.post("/teams", response_model=TeamOut, status_code=201)
async def create_team(
    body: TeamCreateBody,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    user = _require_admin_user(principal)
    if await teams_repo.get_team_by_slug(session, body.slug) is not None:
        raise HTTPException(409, f"team slug '{body.slug}' already exists")
    team = await teams_repo.create_team(
        session, slug=body.slug, display_name=body.display_name, creator_user_id=user.id
    )
    await write_audit(
        session,
        actor_user_id=user.id,
        team_scope=team.slug,
        action="teams.create",
        target_id=str(team.id),
        payload={"slug": team.slug, "display_name": team.display_name},
    )
    await session.commit()
    return TeamOut(id=str(team.id), slug=team.slug, display_name=team.display_name)


# ── Static-path routes MUST come before /{team_id} routes ──────────────────────


@router.get("/teams/my-teams", response_model=list[TeamSearchOut])
async def get_my_teams(
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Return all teams the caller belongs to.

    Two sources are merged:
      1. Explicit team_members rows (user was invited/joined directly).
      2. GitHub-org-derived teams — if the user has `github_username` set
         (linked via Phase 1b /v1/me/link-github), include teams whose
         `github_org` matches an org the user belongs to. Phase 1b
         (quick task 260512-glk) — gives Google-linked-to-GitHub users
         transparent access to their org teams without explicit invitation.

    Used by the Chrome extension to populate the team dropdown and the
    "CortX OS" right-click context submenu.
    """
    user = _require_user(principal)
    teams = await teams_repo.get_all_teams_for_user(session, user_id=user.id)
    teams_by_id = {t.id: t for t in teams}

    # Merge in GitHub-org-derived teams when linked. Soft-fail on any GitHub
    # API error — we still return the user's explicit teams.
    if settings.GITHUB_API_PAT and getattr(user, "github_username", None):
        try:
            all_org_teams = await teams_repo.get_teams_with_github_org(session)
            org_teams_to_check = [
                t for t in all_org_teams if t.id not in teams_by_id
            ]
            if org_teams_to_check:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    for team in org_teams_to_check:
                        url = (
                            f"https://api.github.com/orgs/{team.github_org}"
                            f"/members/{user.github_username}"
                        )
                        r = await client.get(
                            url,
                            headers={
                                "Authorization": f"Bearer {settings.GITHUB_API_PAT}",
                                **_GH_HEADERS,
                            },
                        )
                        if r.status_code == 204:
                            teams_by_id[team.id] = team
        except Exception:  # noqa: BLE001 — never fail my-teams on GitHub issues
            pass

    return [
        TeamSearchOut(
            id=str(t.id),
            slug=t.slug,
            display_name=t.display_name,
            visibility=t.visibility,
            github_org=t.github_org,
        )
        for t in teams_by_id.values()
    ]


@router.get("/teams/my-team")
async def get_my_team(
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Return the caller's first team, or 204 if they belong to none."""
    from fastapi.responses import Response
    user = _require_user(principal)
    team = await teams_repo.get_first_team_for_user(session, user_id=user.id)
    if team is None:
        return Response(status_code=204)
    return TeamSearchOut(
        id=str(team.id),
        slug=team.slug,
        display_name=team.display_name,
        visibility=team.visibility,
        github_org=team.github_org,
    )


@router.get("/teams/search", response_model=list[TeamSearchOut])
async def search_teams(
    name: str,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    _require_user(principal)
    if not name or len(name) < 2:
        raise HTTPException(400, "name query must be at least 2 characters")
    teams = await teams_repo.search_teams(session, query=name)
    return [
        TeamSearchOut(
            id=str(t.id),
            slug=t.slug,
            display_name=t.display_name,
            visibility=t.visibility,
            github_org=t.github_org,
        )
        for t in teams
    ]


_GH_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


async def _resolve_github_username(user, session: AsyncSession, pat: str) -> str | None:
    """Return GitHub login, resolving from github_id via API if username not cached yet."""
    if user.github_username:
        return user.github_username
    if not user.github_id:
        return None
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(
            f"https://api.github.com/user/{user.github_id}",
            headers={"Authorization": f"Bearer {pat}", **_GH_HEADERS},
        )
        if r.status_code != 200:
            return None
        login = r.json().get("login")
        if login:
            user.github_username = login
            await session.flush()
            await session.commit()
        return login


@router.get("/teams/github-matches", response_model=list[TeamSearchOut])
async def github_matches(
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Return xbrain teams whose github_org matches any org the user is a member of."""
    user = _require_user(principal)
    if not settings.GITHUB_API_PAT:
        return []

    username = await _resolve_github_username(user, session, settings.GITHUB_API_PAT)
    if not username:
        return []

    all_teams = await teams_repo.get_teams_with_github_org(session)
    if not all_teams:
        return []

    matches = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        for team in all_teams:
            url = f"https://api.github.com/orgs/{team.github_org}/members/{username}"
            r = await client.get(
                url,
                headers={"Authorization": f"Bearer {settings.GITHUB_API_PAT}", **_GH_HEADERS},
            )
            if r.status_code == 204:
                matches.append(
                    TeamSearchOut(
                        id=str(team.id),
                        slug=team.slug,
                        display_name=team.display_name,
                        visibility=team.visibility,
                        github_org=team.github_org,
                    )
                )
    return matches


@router.get("/teams/my-github-orgs", response_model=list[GithubOrgOut])
async def my_github_orgs(
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Return the caller's GitHub organizations."""
    user = _require_user(principal)
    if not settings.GITHUB_API_PAT:
        return []

    username = await _resolve_github_username(user, session, settings.GITHUB_API_PAT)
    if not username:
        return []

    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(
            f"https://api.github.com/users/{username}/orgs",
            headers={"Authorization": f"Bearer {settings.GITHUB_API_PAT}", **_GH_HEADERS},
        )
        if r.status_code != 200:
            return []
        return [
            GithubOrgOut(login=o["login"], description=o.get("description"))
            for o in r.json()
        ]


@router.post("/teams/self-solo", response_model=TeamSearchOut, status_code=200)
async def self_create_solo_team(
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Idempotent: create or return the caller's solo workspace team (no github_org).

    Called automatically at first login for Google-only users. Returns the existing
    team if the user already belongs to one (solo or otherwise).
    Slug pattern: solo-<first 16 hex chars of user UUID>.
    """
    user = _require_user(principal)
    existing = await teams_repo.get_first_team_for_user(session, user_id=user.id)
    if existing is not None:
        return TeamSearchOut(
            id=str(existing.id),
            slug=existing.slug,
            display_name=existing.display_name,
            visibility=existing.visibility,
            github_org=existing.github_org,
        )
    slug = f"solo-{user.id.hex[:16]}"
    if await teams_repo.get_team_by_slug(session, slug) is not None:
        slug = f"solo-{user.id.hex}"
    team = await teams_repo.create_team(
        session,
        slug=slug,
        display_name="My Workspace",
        creator_user_id=user.id,
        visibility="closed",
        github_org=None,
    )
    await write_audit(
        session,
        actor_user_id=user.id,
        team_scope=team.slug,
        action="teams.solo_create",
        target_id=str(team.id),
        payload={"slug": team.slug},
    )
    await session.commit()
    return TeamSearchOut(
        id=str(team.id),
        slug=team.slug,
        display_name=team.display_name,
        visibility=team.visibility,
        github_org=team.github_org,
    )


@router.post("/teams/self", response_model=TeamSearchOut, status_code=201)
async def self_create_team(
    body: TeamSelfCreateBody,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Any authenticated user can create a team and become its admin (founder)."""
    user = _require_user(principal)
    if await teams_repo.get_team_by_slug(session, body.slug) is not None:
        raise HTTPException(409, f"team slug '{body.slug}' already exists")
    team = await teams_repo.create_team(
        session,
        slug=body.slug,
        display_name=body.display_name,
        creator_user_id=user.id,
        description=body.description,
        visibility=body.visibility,
        github_org=body.github_org,
    )
    await write_audit(
        session,
        actor_user_id=user.id,
        team_scope=team.slug,
        action="teams.self_create",
        target_id=str(team.id),
        payload={"slug": team.slug, "visibility": team.visibility},
    )
    await session.commit()
    return TeamSearchOut(
        id=str(team.id),
        slug=team.slug,
        display_name=team.display_name,
        visibility=team.visibility,
        github_org=team.github_org,
    )


# ── Parameterized routes /{team_id}/... ────────────────────────────────────────


@router.post("/teams/{team_id}/members", response_model=MemberOut, status_code=201)
async def invite_member(
    team_id: UUID,
    body: MemberInviteBody,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Legacy invite endpoint — requires source_user_id (OIDC sub). Kept for
    backward compatibility with the LibreChat onboarding modal. New UIs
    should use /v1/teams/{id}/invite (invite-by-email, below)."""
    team = await teams_repo.get_team_by_id(session, team_id)
    if team is None:
        raise HTTPException(404, "team not found")
    actor = await _require_team_admin(principal, team, session)
    invitee = await users_repo.get_or_create_user(
        session,
        source_user_id=body.source_user_id,
        email=body.email,
        display_name=body.display_name,
    )
    existing = await teams_repo.get_membership(session, user_id=invitee.id, team_slug=team.slug)
    if existing is not None:
        return MemberOut(
            user_id=str(invitee.id),
            role=existing.role,
            email=invitee.email,
            display_name=invitee.display_name,
            source_user_id=invitee.source_user_id,
            github_username=getattr(invitee, "github_username", None),
        )
    member = await teams_repo.add_member(
        session, team_id=team.id, user_id=invitee.id, role=body.role
    )
    await write_audit(
        session,
        actor_user_id=actor.id,
        team_scope=team.slug,
        action="members.invite",
        target_id=str(invitee.id),
        payload={"role": body.role, "email": body.email},
    )
    await session.commit()
    return MemberOut(
        user_id=str(member.user_id),
        role=member.role,
        email=invitee.email,
        display_name=invitee.display_name,
        source_user_id=invitee.source_user_id,
        github_username=getattr(invitee, "github_username", None),
    )


@router.post("/teams/{team_id}/invite", response_model=MemberOut, status_code=201)
async def invite_by_email(
    team_id: UUID,
    body: MemberInviteByEmailBody,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Invite an existing user (by email) to this team.

    Quick task 260513-tmu — used by the extension Settings + app-site Teams
    page. The invitee MUST already have a row in `users` (i.e. they've
    signed into xbrain at least once). If not, returns 404 with a clear
    message so the UI can surface "ask them to sign in first".

    Auth: team admin only (role='admin' in this team OR global env admin).
    """
    team = await teams_repo.get_team_by_id(session, team_id)
    if team is None:
        raise HTTPException(404, "team not found")
    actor = await _require_team_admin(principal, team, session)

    invitee = await users_repo.get_user_by_email(session, body.email)
    if invitee is None:
        raise HTTPException(
            404,
            f"No xbrain user with email {body.email}. Ask them to sign in once first.",
        )

    existing = await teams_repo.get_membership(session, user_id=invitee.id, team_slug=team.slug)
    if existing is not None:
        return MemberOut(
            user_id=str(invitee.id),
            role=existing.role,
            email=invitee.email,
            display_name=invitee.display_name,
            source_user_id=invitee.source_user_id,
            github_username=getattr(invitee, "github_username", None),
        )

    member = await teams_repo.add_member(
        session, team_id=team.id, user_id=invitee.id, role=body.role
    )
    await write_audit(
        session,
        actor_user_id=actor.id,
        team_scope=team.slug,
        action="members.invite_by_email",
        target_id=str(invitee.id),
        payload={"role": body.role, "email": body.email},
    )
    await session.commit()
    return MemberOut(
        user_id=str(member.user_id),
        role=member.role,
        email=invitee.email,
        display_name=invitee.display_name,
        source_user_id=invitee.source_user_id,
        github_username=getattr(invitee, "github_username", None),
    )


@router.get("/teams/{team_id}/members", response_model=list[MemberOut])
async def list_members(
    team_id: UUID,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    user = _require_user(principal)
    team = await teams_repo.get_team_by_id(session, team_id)
    if team is None:
        raise HTTPException(404, "team not found")
    # caller must be a member of the team to see the list
    membership = await teams_repo.get_membership(session, user_id=user.id, team_slug=team.slug)
    if membership is None:
        raise HTTPException(403, "not a member")
    # JOIN with users so the UI gets email + display_name in one round-trip.
    rows = await teams_repo.list_members_with_user_info(session, team_id=team_id)
    return [
        MemberOut(
            user_id=str(m.user_id),
            role=m.role,
            email=u.email,
            display_name=u.display_name,
            source_user_id=u.source_user_id,
            github_username=getattr(u, "github_username", None),
        )
        for (m, u) in rows
    ]


@router.delete("/teams/{team_id}/members/me", status_code=204)
async def leave_team(
    team_id: UUID,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Leave a team — caller removes their own membership.

    Quick task 260513-tmu. Refuses to remove the last admin so a team
    can't be orphaned (the UI surfaces this as "Promote another member
    to admin before leaving").
    """
    user = _require_user(principal)
    team = await teams_repo.get_team_by_id(session, team_id)
    if team is None:
        raise HTTPException(404, "team not found")
    membership = await teams_repo.get_membership(
        session, user_id=user.id, team_slug=team.slug
    )
    if membership is None:
        raise HTTPException(404, "not a member")

    if membership.role == "admin":
        admin_count = await teams_repo.count_admins(session, team_id=team_id)
        if admin_count <= 1:
            raise HTTPException(
                409,
                "you are the only admin — promote another member to admin before leaving",
            )

    await teams_repo.remove_member(session, team_id=team_id, user_id=user.id)
    await write_audit(
        session,
        actor_user_id=user.id,
        team_scope=team.slug,
        action="members.leave",
        target_id=str(user.id),
        payload={"role": membership.role},
    )
    await session.commit()


@router.delete("/teams/{team_id}/members/{user_id}", status_code=204)
async def remove_member(
    team_id: UUID,
    user_id: UUID,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    team = await teams_repo.get_team_by_id(session, team_id)
    if team is None:
        raise HTTPException(404, "team not found")
    actor = await _require_team_admin(principal, team, session)

    # Block removing the last admin (same guardrail as leave_team).
    target = await teams_repo.get_membership(
        session, user_id=user_id, team_slug=team.slug
    )
    if target is None:
        raise HTTPException(404, "not a member of this team")
    if target.role == "admin":
        admin_count = await teams_repo.count_admins(session, team_id=team_id)
        if admin_count <= 1:
            raise HTTPException(
                409,
                "cannot remove the last admin — promote another member first",
            )

    await teams_repo.remove_member(session, team_id=team_id, user_id=user_id)
    await write_audit(
        session,
        actor_user_id=actor.id,
        team_scope=team.slug,
        action="members.remove",
        target_id=str(user_id),
        payload={},
    )
    await session.commit()


@router.post("/teams/{team_id}/join", status_code=204)
async def join_team(
    team_id: UUID,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Join an open team directly. Returns 403 for closed teams."""
    from fastapi.responses import Response
    user = _require_user(principal)
    team = await teams_repo.get_team_by_id(session, team_id)
    if team is None:
        raise HTTPException(404, "team not found")
    if team.visibility != "open":
        raise HTTPException(403, "team is closed — use join-request instead")
    existing = await teams_repo.get_membership(session, user_id=user.id, team_slug=team.slug)
    if existing is not None:
        return Response(status_code=204)
    await teams_repo.add_member(session, team_id=team.id, user_id=user.id, role="member")
    await write_audit(
        session,
        actor_user_id=user.id,
        team_scope=team.slug,
        action="teams.join",
        target_id=str(team.id),
        payload={},
    )
    await session.commit()
    return Response(status_code=204)


@router.post("/teams/{team_id}/join-request", response_model=JoinRequestOut, status_code=201)
async def request_join_team(
    team_id: UUID,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Submit a join request for a closed team. Idempotent."""
    user = _require_user(principal)
    team = await teams_repo.get_team_by_id(session, team_id)
    if team is None:
        raise HTTPException(404, "team not found")
    req = await teams_repo.create_join_request(session, team_id=team.id, user_id=user.id)
    await session.commit()
    return JoinRequestOut(status=req.status, team_id=str(req.team_id))


@router.get("/teams/{team_id}/api-keys", response_model=list[ApiKeyOut])
async def list_api_keys(
    team_id: UUID,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """List provider names for which the team has an API key. Never returns plaintext keys."""
    user = _require_user(principal)
    team = await teams_repo.get_team_by_id(session, team_id)
    if team is None:
        raise HTTPException(404, "team not found")
    membership = await teams_repo.get_membership(session, user_id=user.id, team_slug=team.slug)
    if membership is None:
        raise HTTPException(403, "not a member")
    keys = await teams_repo.list_team_api_keys(session, team_id=team_id)
    return [ApiKeyOut(provider=k.provider) for k in keys]


@router.put("/teams/{team_id}/api-keys", status_code=204)
async def upsert_api_keys(
    team_id: UUID,
    body: ApiKeysBulkBody,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Upsert team API keys. Caller must be a team admin. Keys Fernet-encrypted at rest."""
    from fastapi.responses import Response
    user = _require_user(principal)
    team = await teams_repo.get_team_by_id(session, team_id)
    if team is None:
        raise HTTPException(404, "team not found")
    membership = await teams_repo.get_membership(session, user_id=user.id, team_slug=team.slug)
    if membership is None or membership.role != "admin":
        raise HTTPException(403, "team admin required")
    fernet = _get_fernet()
    for item in body.keys:
        encrypted = fernet.encrypt(item.api_key.encode()).decode()
        await teams_repo.upsert_team_api_key(
            session, team_id=team_id, provider=item.provider, key_enc=encrypted
        )
    await session.commit()
    return Response(status_code=204)
