"""/v1/promotions/* — truth-level promotion workflow.

Only path that mutates `truth_level` on a memory item.
PATCH /v1/memory/{id} on truth_level returns 405 (see routes/memory.py).
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from xbrain_memory import MemoryProvider, TruthLevel

from app.audit import write_audit
from app.deps import (
    get_current_principal,
    get_memory_provider,
    get_session,
    get_team_scope,
)
from app.services import truth_workflow

router = APIRouter()


class ProposeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str
    target_level: TruthLevel
    rationale: str | None = Field(default=None, max_length=2000)


class RejectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(..., min_length=1, max_length=2000)


class PromotionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")
    id: UUID
    memory_item_id: UUID
    team_scope: str
    from_level: str
    to_level: str
    proposed_by: UUID
    approved_by_1: UUID | None
    approved_by_2: UUID | None
    status: str
    rationale: str | None
    rejection_reason: str | None
    created_at: datetime
    resolved_at: datetime | None


def _principal_user_id(principal: dict[str, Any]) -> UUID:
    """Bridge tokens cannot propose/approve — must be a real user."""
    if principal["kind"] != "user":
        from fastapi import HTTPException
        raise HTTPException(403, "promotions require a real user principal (not bridge)")
    return principal["user"].id


@router.post("/promotions", status_code=201, response_model=PromotionOut)
async def propose(
    body: ProposeBody,
    session: AsyncSession = Depends(get_session),
    principal: dict[str, Any] = Depends(get_current_principal),
    team_scope: str = Depends(get_team_scope),
    provider: MemoryProvider = Depends(get_memory_provider),
):
    proposer_id = _principal_user_id(principal)
    promotion = await truth_workflow.propose_promotion(
        session,
        provider=provider,
        item_id=body.item_id,
        team_scope=team_scope,
        proposer_id=proposer_id,
        target_level=body.target_level,
        rationale=body.rationale,
    )
    await write_audit(
        session,
        actor_user_id=proposer_id,
        team_scope=team_scope,
        action="promotion.propose",
        target_id=body.item_id,
        payload={
            "promotion_id": str(promotion.id),
            "from": promotion.from_level,
            "to": promotion.to_level,
            "auto": promotion.status == "auto",
        },
    )
    await session.commit()
    await session.refresh(promotion)
    return promotion


@router.get("/promotions/pending", response_model=list[PromotionOut])
async def list_pending(
    limit: int = Query(default=50, ge=1, le=200),
    team_scope: str = Depends(get_team_scope),
    session: AsyncSession = Depends(get_session),
    _principal: dict[str, Any] = Depends(get_current_principal),
):
    return await truth_workflow.list_pending(session, team_scope=team_scope, limit=limit)


@router.post("/promotions/{promotion_id}/approve", response_model=PromotionOut)
async def approve(
    promotion_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: dict[str, Any] = Depends(get_current_principal),
    team_scope: str = Depends(get_team_scope),
    provider: MemoryProvider = Depends(get_memory_provider),
):
    approver_id = _principal_user_id(principal)
    promotion = await truth_workflow.approve_promotion(
        session,
        provider=provider,
        promotion_id=promotion_id,
        approver_id=approver_id,
        team_scope=team_scope,
    )
    await write_audit(
        session,
        actor_user_id=approver_id,
        team_scope=team_scope,
        action="promotion.approve",
        target_id=str(promotion.memory_item_id),
        payload={
            "promotion_id": str(promotion.id),
            "to": promotion.to_level,
            "status_after": promotion.status,
        },
    )
    await session.commit()
    await session.refresh(promotion)
    return promotion


@router.post("/promotions/{promotion_id}/reject", response_model=PromotionOut)
async def reject(
    promotion_id: UUID,
    body: RejectBody,
    session: AsyncSession = Depends(get_session),
    principal: dict[str, Any] = Depends(get_current_principal),
    team_scope: str = Depends(get_team_scope),
):
    rejector_id = _principal_user_id(principal)
    promotion = await truth_workflow.reject_promotion(
        session,
        promotion_id=promotion_id,
        rejector_id=rejector_id,
        team_scope=team_scope,
        reason=body.reason,
    )
    await write_audit(
        session,
        actor_user_id=rejector_id,
        team_scope=team_scope,
        action="promotion.reject",
        target_id=str(promotion.memory_item_id),
        payload={
            "promotion_id": str(promotion.id),
            "reason": body.reason,
        },
    )
    await session.commit()
    await session.refresh(promotion)
    return promotion
