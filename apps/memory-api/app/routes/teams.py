"""/v1/teams — admin-managed team CRUD + membership."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import write_audit
from app.auth import is_admin
from app.deps import get_current_principal, get_session
from app.repos import teams as teams_repo
from app.repos import users as users_repo

router = APIRouter()


def _require_user(principal: dict[str, Any]):
    if principal["kind"] != "user":
        raise HTTPException(403, "user-only endpoint")
    return principal["user"]


def _require_admin_user(principal: dict[str, Any]):
    user = _require_user(principal)
    if not is_admin(user.source_user_id):
        raise HTTPException(403, "admin-only endpoint")
    return user


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


class MemberOut(BaseModel):
    user_id: str
    role: str


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


@router.post("/teams/{team_id}/members", response_model=MemberOut, status_code=201)
async def invite_member(
    team_id: UUID,
    body: MemberInviteBody,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    actor = _require_admin_user(principal)
    team = await teams_repo.get_team_by_id(session, team_id)
    if team is None:
        raise HTTPException(404, "team not found")
    invitee = await users_repo.get_or_create_user(
        session,
        source_user_id=body.source_user_id,
        email=body.email,
        display_name=body.display_name,
    )
    # Idempotent: if already member, return current row
    existing = await teams_repo.get_membership(session, user_id=invitee.id, team_slug=team.slug)
    if existing is not None:
        return MemberOut(user_id=str(invitee.id), role=existing.role)
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
    return MemberOut(user_id=str(member.user_id), role=member.role)


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
    members = await teams_repo.list_members(session, team_id=team_id)
    return [MemberOut(user_id=str(m.user_id), role=m.role) for m in members]


@router.delete("/teams/{team_id}/members/{user_id}", status_code=204)
async def remove_member(
    team_id: UUID,
    user_id: UUID,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    actor = _require_admin_user(principal)
    team = await teams_repo.get_team_by_id(session, team_id)
    if team is None:
        raise HTTPException(404, "team not found")
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
