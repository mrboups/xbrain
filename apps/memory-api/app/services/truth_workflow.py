"""Truth-level promotion state machine — 4-eyes for CANONICAL.

Invariants enforced here (single source of truth for all promotion logic):

    ALLOWED_TRANSITIONS — directed edges in the truth-level lattice
    APPROVAL_REQUIREMENTS — count of distinct admin approvers required per target level
    proposer != approver_1 != approver_2 — distinct natural persons

PATCH /v1/memory/{id} on `truth_level` is rejected upstream (HTTP 405).
The only path to mutate truth_level is via this module.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from xbrain_memory import MemoryProvider, TruthLevel

from app.models.promotion import Promotion
from app.models.team import Team, TeamMember

# === State machine ===

# Allowed promotions: only forward in the truth-level lattice.
# Demotions and skips are explicitly forbidden — they require a separate "revoke" op
# (out of scope for Phase 2; will land in Phase 4 governance).
ALLOWED_TRANSITIONS: dict[TruthLevel, set[TruthLevel]] = {
    TruthLevel.EPHEMERAL: {TruthLevel.WORKING},
    TruthLevel.WORKING: {TruthLevel.VALIDATED},
    TruthLevel.VALIDATED: {TruthLevel.CANONICAL},
    TruthLevel.CANONICAL: {TruthLevel.PUBLIC},
    TruthLevel.PUBLIC: set(),
}

# Approver count required per *target* truth level (4-eyes for CANONICAL).
# 0 = auto-approved on propose; 1 = single admin; 2 = two distinct admins.
APPROVAL_REQUIREMENTS: dict[TruthLevel, int] = {
    TruthLevel.WORKING: 0,       # any teammate can promote EPHEMERAL → WORKING
    TruthLevel.VALIDATED: 1,     # 1 admin
    TruthLevel.CANONICAL: 2,     # 2 distinct admins (4-eyes)
    TruthLevel.PUBLIC: 1,        # 1 admin (after CANONICAL — already vetted)
}


class WorkflowError(HTTPException):
    """Domain error → HTTP 4xx surfaced by routes."""


# === Helpers ===

async def _is_team_admin(
    session: AsyncSession, *, user_id: UUID, team_scope: str
) -> bool:
    stmt = (
        select(TeamMember.role)
        .join(Team, Team.id == TeamMember.team_id)
        .where(TeamMember.user_id == user_id, Team.slug == team_scope)
    )
    role = (await session.execute(stmt)).scalar_one_or_none()
    return role == "admin"


def _validate_transition(from_level: TruthLevel, to_level: TruthLevel) -> None:
    if to_level not in ALLOWED_TRANSITIONS.get(from_level, set()):
        raise WorkflowError(
            422,
            f"Illegal transition {from_level.value} → {to_level.value}. "
            f"Allowed from {from_level.value}: "
            f"{sorted(t.value for t in ALLOWED_TRANSITIONS[from_level])}",
        )


# === Public API ===

async def propose_promotion(
    session: AsyncSession,
    *,
    provider: MemoryProvider,
    item_id: str,
    team_scope: str,
    proposer_id: UUID,
    target_level: TruthLevel,
    rationale: str | None = None,
) -> Promotion:
    """Open a promotion. Auto-approves WORKING. Persists pending row otherwise."""
    item = await provider.get(item_id, team_scope=team_scope)
    if item is None:
        raise WorkflowError(404, "memory item not found in this team")

    current = item.truth_level
    _validate_transition(current, target_level)

    required = APPROVAL_REQUIREMENTS[target_level]

    promotion = Promotion(
        memory_item_id=UUID(item_id),
        team_scope=team_scope,
        from_level=current.value,
        to_level=target_level.value,
        proposed_by=proposer_id,
        rationale=rationale,
        status="pending",
    )

    if required == 0:
        # Auto-promote (e.g. EPHEMERAL → WORKING) — no approval gate
        await _apply_promotion(provider, item_id, team_scope, target_level)
        promotion.status = "auto"
        promotion.resolved_at = datetime.now(timezone.utc)

    session.add(promotion)
    await session.flush()
    return promotion


async def approve_promotion(
    session: AsyncSession,
    *,
    provider: MemoryProvider,
    promotion_id: UUID,
    approver_id: UUID,
    team_scope: str,
) -> Promotion:
    """Add an approval. Promotes the item once approver count == required."""
    promotion = await session.get(Promotion, promotion_id)
    if promotion is None or promotion.team_scope != team_scope:
        raise WorkflowError(404, "promotion not found in this team")

    if promotion.status != "pending":
        raise WorkflowError(409, f"promotion already {promotion.status}")

    if approver_id == promotion.proposed_by:
        raise WorkflowError(403, "proposer cannot approve their own promotion")

    if approver_id in (promotion.approved_by_1, promotion.approved_by_2):
        raise WorkflowError(409, "this user already approved this promotion")

    if not await _is_team_admin(session, user_id=approver_id, team_scope=team_scope):
        raise WorkflowError(
            403, "only team admins can approve VALIDATED+ promotions"
        )

    target = TruthLevel(promotion.to_level)
    required = APPROVAL_REQUIREMENTS[target]

    # Record approval in next free slot
    if promotion.approved_by_1 is None:
        promotion.approved_by_1 = approver_id
    else:
        promotion.approved_by_2 = approver_id

    approvals_so_far = sum(
        1 for slot in (promotion.approved_by_1, promotion.approved_by_2) if slot
    )

    if approvals_so_far >= required:
        await _apply_promotion(
            provider,
            str(promotion.memory_item_id),
            team_scope,
            target,
        )
        promotion.status = "approved"
        promotion.resolved_at = datetime.now(timezone.utc)

    await session.flush()
    return promotion


async def reject_promotion(
    session: AsyncSession,
    *,
    promotion_id: UUID,
    rejector_id: UUID,
    team_scope: str,
    reason: str,
) -> Promotion:
    """Mark promotion rejected — item stays at current truth_level."""
    promotion = await session.get(Promotion, promotion_id)
    if promotion is None or promotion.team_scope != team_scope:
        raise WorkflowError(404, "promotion not found in this team")
    if promotion.status != "pending":
        raise WorkflowError(409, f"promotion already {promotion.status}")
    if not await _is_team_admin(session, user_id=rejector_id, team_scope=team_scope):
        raise WorkflowError(403, "only team admins can reject promotions")

    promotion.status = "rejected"
    promotion.rejection_reason = reason
    promotion.resolved_at = datetime.now(timezone.utc)
    await session.flush()
    return promotion


async def list_pending(
    session: AsyncSession, *, team_scope: str, limit: int = 50
) -> list[Promotion]:
    stmt = (
        select(Promotion)
        .where(Promotion.team_scope == team_scope, Promotion.status == "pending")
        .order_by(Promotion.created_at.asc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


# === Internal: apply the truth-level mutation via provider ===

async def _apply_promotion(
    provider: MemoryProvider,
    item_id: str,
    team_scope: str,
    new_level: TruthLevel,
) -> None:
    """The ONLY callsite in the codebase that mutates truth_level on a memory item."""
    patch: dict[str, Any] = {"truth_level": new_level.value}
    # validation_status flips to 'validated' the moment we cross VALIDATED
    if new_level >= TruthLevel.VALIDATED:
        patch["validation_status"] = "validated"
    await provider.update(item_id, team_scope=team_scope, patch=patch)
