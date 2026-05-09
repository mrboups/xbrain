"""/v1/messages — POST enforces 7-field contract (422 on missing), GET filters by team_scope."""

import asyncio
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import write_audit
from app.deps import get_current_principal, get_session, get_team_scope
from app.models.tagging import TaggingContract
from app.repos import conversations as conv_repo
from app.repos import messages as messages_repo

router = APIRouter()


class MessageCreateBody(BaseModel):
    """Pydantic v2 + extra='forbid' makes any missing of the 7 tagging fields → HTTP 422.

    This is the contract enforcement gate (success criterion 2).
    """

    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID
    role: str = Field(..., pattern=r"^(user|assistant|system|tool)$")
    content: str = Field(..., min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    tagging: TaggingContract


class MessageOut(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: str
    tagging: TaggingContract
    created_at: str


def _row_to_out(m) -> MessageOut:
    return MessageOut(
        id=str(m.id),
        conversation_id=str(m.conversation_id),
        role=m.role,
        content=m.content,
        tagging=TaggingContract(
            team_scope=m.team_scope,
            project_scope=m.project_scope,
            visibility=m.visibility,
            confidence=m.confidence,
            truth_level=m.truth_level,
            source=m.source,
            validation_status=m.validation_status,
        ),
        created_at=m.created_at.isoformat(),
    )


@router.post("/messages", response_model=MessageOut, status_code=201)
async def create_message(
    body: MessageCreateBody,
    session: AsyncSession = Depends(get_session),
    principal: dict[str, Any] = Depends(get_current_principal),
    team_scope: str = Depends(get_team_scope),
):
    if body.tagging.team_scope != team_scope:
        raise HTTPException(400, "tagging.team_scope must match X-Team-Scope header")
    conv = await conv_repo.get_conversation(session, conversation_id=body.conversation_id, team_scope=team_scope)
    if conv is None:
        # Upsert silencieux (Décision 4-D-02) — pipeline OWUI ne crée jamais la conversation au préalable.
        # ON CONFLICT (id) DO NOTHING garantit l'idempotence sur UUID v5 déterministe.
        # Le bridge LibreChat crée explicitement la conversation avant d'envoyer des messages
        # — pour lui cet upsert est un no-op (la row existe déjà).
        if principal["kind"] == "user":
            owner_id = principal["user"].id
        else:
            from app.repos.users import get_or_create_user
            _user = await get_or_create_user(
                session,
                source_user_id=principal["sub"],
                email=f"{principal['sub']}@bridged.local",
                display_name=None,
            )
            owner_id = _user.id
        await conv_repo.upsert_conversation_silent(
            session,
            conversation_id=body.conversation_id,
            team_scope=team_scope,
            owner_user_id=owner_id,
            source=body.tagging.source,
            project_scope=body.tagging.project_scope,
        )
    msg = await messages_repo.create_message(
        session,
        conversation_id=body.conversation_id,
        tagging=body.tagging,
        role=body.role,
        content=body.content,
        metadata=body.metadata,
    )
    actor_id = principal["user"].id if principal["kind"] == "user" else None
    await write_audit(
        session,
        actor_user_id=actor_id,
        team_scope=team_scope,
        action="messages.create",
        target_id=str(msg.id),
        payload={"role": body.role, "source": body.tagging.source},
    )
    await session.commit()

    if body.content and body.role in ("user", "assistant"):
        from app.routes.memory import _enrich_with_graphiti  # noqa: PLC0415
        asyncio.create_task(_enrich_with_graphiti(body.content, team_scope))

    return _row_to_out(msg)


@router.get("/messages", response_model=list[MessageOut])
async def list_messages(
    session: AsyncSession = Depends(get_session),
    team_scope: str = Depends(get_team_scope),
    conversation_id: UUID | None = Query(default=None),
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, le=200),
):
    rows = await messages_repo.list_messages(
        session, team_scope=team_scope, conversation_id=conversation_id, q=q, limit=limit
    )
    return [_row_to_out(m) for m in rows]
