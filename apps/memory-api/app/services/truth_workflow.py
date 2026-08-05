"""The approval workflow, now reserved for ONE transition: CANONICAL -> PUBLIC.

WHY THIS SHRANK — 2026-08-05.

`promotions` models a human two-approver workflow (`proposed_by`,
`approved_by_1`, `approved_by_2`, `status`, `rationale`, `rejection_reason`).
The four-level design does not use it for the levels it once covered:

    important / final (VALIDATED)  is set by the AI, alone, and reversibly
                                   -> services/importance.py
    starred (CANONICAL)            is set by one person, alone
                                   -> routes/team_chat.py, PUT .../star

Both write `audit_log`, which is where every other mutation in this codebase
already goes. Leaving this table able to record those same two levels would give
"who made this important?" and "who starred this?" a SECOND possible answer, from
a table nobody writes — and the wrong one is the one somebody eventually reads.
Production has never held a single row at VALIDATED, CANONICAL or PUBLIC, so
there is no history to preserve either way.

RETIRED, REPURPOSED, OR LEFT? **Repurposed**, and the narrowing below is what
makes that a decision rather than a note. What survives is exactly the shape the
sharing flow needs (BACKLOG.md, "Sharing beyond the team"): a member REQUESTS
that an item go public, an admin APPROVES or REFUSES with a reason, and the row
answers "who approved this?" six months later — the question that actually gets
asked about anything public. `proposed_by` is the requester, `approved_by_1` the
admin, `rejection_reason` the refusal. Dropping the table would mean rebuilding
that within the month.

`approved_by_2` and the 4-eyes machinery stay in the schema and go unused: the
sharing design calls for one admin, and CANONICAL — the level that once required
two — is now one person's star. Keeping the column costs nothing and removing it
is a migration for no gain.

Invariants still enforced here:

    ALLOWED_TRANSITIONS — directed edges in the truth-level lattice
    APPROVAL_REQUIREMENTS — count of distinct admin approvers required per target level
    proposer != approver_1 != approver_2 — distinct natural persons

PATCH /v1/memory/{id} on `truth_level` is rejected upstream (HTTP 405).
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

# The ONE transition this workflow still carries. Everything else is empty on
# purpose, so a caller aiming at a level this design assigns elsewhere gets a 422
# naming the owner rather than a second trail nobody reads.
#
# Forward-only still holds: there is no demotion here, and un-sharing will be its
# own operation with its own audit action — the sharing design is explicit that
# revoking is not un-sharing and must not be dressed up as one.
ALLOWED_TRANSITIONS: dict[TruthLevel, set[TruthLevel]] = {
    TruthLevel.EPHEMERAL: set(),   # no producer since 2026-08-05
    TruthLevel.WORKING: set(),     # -> VALIDATED belongs to the AI
    TruthLevel.VALIDATED: set(),   # -> CANONICAL belongs to a person's star
    TruthLevel.CANONICAL: {TruthLevel.PUBLIC},
    TruthLevel.PUBLIC: set(),
}

# Approver count required per *target* truth level.
# 1 = single admin. The sharing design grants public to an ADMIN only, on a
# member's request — so one approver, distinct from the requester.
APPROVAL_REQUIREMENTS: dict[TruthLevel, int] = {
    TruthLevel.PUBLIC: 1,
}

#: Who owns a level this workflow no longer sets, quoted back in the 422 so the
#: caller learns where to go instead of learning only that they were refused.
_LEVEL_OWNERS: dict[TruthLevel, str] = {
    TruthLevel.WORKING: "the ingest default — every stored item starts there",
    TruthLevel.VALIDATED: (
        "the assistant's important/final flag, set and cleared automatically "
        "(services/importance.py)"
    ),
    TruthLevel.CANONICAL: (
        "a person's star, set by any team member "
        "(PUT /v1/teams/{team_id}/messages/{message_id}/star)"
    ),
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
    if to_level in ALLOWED_TRANSITIONS.get(from_level, set()):
        return
    owner = _LEVEL_OWNERS.get(to_level)
    if owner is not None:
        raise WorkflowError(
            422,
            f"{to_level.value} is not set through the approval workflow. "
            f"It belongs to {owner}.",
        )
    raise WorkflowError(
        422,
        f"Illegal transition {from_level.value} → {to_level.value}. "
        f"Allowed from {from_level.value}: "
        f"{sorted(t.value for t in ALLOWED_TRANSITIONS.get(from_level, set()))}",
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
    """Open a request. Persists a pending row for an admin to approve or refuse.

    Nothing auto-approves any more: the only surviving target is PUBLIC, and
    sharing beyond the team is precisely the decision that must not happen
    without somebody deciding it.
    """
    item = await provider.get(item_id, team_scope=team_scope)
    if item is None:
        raise WorkflowError(404, "memory item not found in this team")

    current = item.truth_level
    _validate_transition(current, target_level)

    promotion = Promotion(
        memory_item_id=UUID(item_id),
        team_scope=team_scope,
        from_level=current.value,
        to_level=target_level.value,
        proposed_by=proposer_id,
        rationale=rationale,
        status="pending",
    )

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
    required = APPROVAL_REQUIREMENTS.get(target)
    if required is None:
        # A row proposed before the workflow narrowed to CANONICAL → PUBLIC.
        # Refuse rather than KeyError, and refuse rather than apply: the level it
        # targets now has a different owner, and honouring the old approval would
        # write the very second answer this narrowing exists to prevent.
        raise WorkflowError(
            409,
            f"This promotion targets {target.value}, which is no longer set "
            "through the approval workflow. It cannot be approved.",
        )

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
