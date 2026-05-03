"""/v1/conversations — create + list (team-scoped)."""

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import write_audit
from app.deps import get_current_principal, get_session, get_team_scope
from app.repos import conversations as conv_repo

router = APIRouter()


class ConversationCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=512)
    project_scope: str | None = Field(default=None, max_length=64)
    source: str = Field(..., min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_-]*:[a-z0-9._-]+$")


class ConversationOut(BaseModel):
    id: str
    team_scope: str
    project_scope: str | None
    owner_user_id: str
    title: str | None
    source: str
    created_at: str


@router.post("/conversations", response_model=ConversationOut, status_code=201)
async def create_conversation(
    body: ConversationCreateBody,
    session: AsyncSession = Depends(get_session),
    principal: dict[str, Any] = Depends(get_current_principal),
    team_scope: str = Depends(get_team_scope),
):
    # Owner = either the user or, for bridge calls, the user identified by claims.sub (which we
    # resolve against memory-api's User table via get_or_create_user inside the bridge handshake).
    if principal["kind"] == "user":
        owner_id = principal["user"].id
    else:
        # Bridge calls: we look up the user by source_user_id (the sub in the bridge JWT)
        from app.repos.users import get_or_create_user

        user = await get_or_create_user(
            session,
            source_user_id=principal["sub"],
            email=f"{principal['sub']}@bridged.local",
            display_name=None,
        )
        owner_id = user.id

    conv = await conv_repo.create_conversation(
        session,
        team_scope=team_scope,
        project_scope=body.project_scope,
        owner_user_id=owner_id,
        title=body.title,
        source=body.source,
    )
    await write_audit(
        session,
        actor_user_id=owner_id,
        team_scope=team_scope,
        action="conversations.create",
        target_id=str(conv.id),
        payload={"source": body.source, "project_scope": body.project_scope},
    )
    await session.commit()
    return ConversationOut(
        id=str(conv.id),
        team_scope=conv.team_scope,
        project_scope=conv.project_scope,
        owner_user_id=str(conv.owner_user_id),
        title=conv.title,
        source=conv.source,
        created_at=conv.created_at.isoformat(),
    )


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    session: AsyncSession = Depends(get_session),
    principal: dict[str, Any] = Depends(get_current_principal),
    team_scope: str = Depends(get_team_scope),
    limit: int = Query(default=50, le=200),
):
    # Users see only their own conversations within the team. Bridges see all in the team.
    owner_filter = principal["user"].id if principal["kind"] == "user" else None
    convs = await conv_repo.list_conversations(
        session, team_scope=team_scope, owner_user_id=owner_filter, limit=limit
    )
    return [
        ConversationOut(
            id=str(c.id),
            team_scope=c.team_scope,
            project_scope=c.project_scope,
            owner_user_id=str(c.owner_user_id),
            title=c.title,
            source=c.source,
            created_at=c.created_at.isoformat(),
        )
        for c in convs
    ]
