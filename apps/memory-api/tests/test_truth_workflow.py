"""The approval workflow, narrowed to the ONE transition it still carries.

Since 2026-08-05 the four-level design assigns the levels this table used to
cover: VALIDATED is the assistant's reversible important/final flag, CANONICAL is
one person's star. Both write `audit_log`. `promotions` is REPURPOSED — reserved
for the sharing-approval flow (CANONICAL → PUBLIC), where a member requests and
an admin approves or refuses with a reason.

The first four tests are the decision itself: this workflow can no longer record
a level somebody else owns. That is what stops "who made this important?" having
a second possible answer, and it is enforced in `ALLOWED_TRANSITIONS` rather than
asserted in a comment.

The rest are the approval machinery that survives, re-pointed at CANONICAL →
PUBLIC: distinct approver, admin-only, resolved-once, team isolation, listing.

Uses NativeStubProvider in-process for the memory layer; Promotion rows go through
the real SQLAlchemy session (testcontainers Postgres).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from xbrain_memory import MemoryItem, TruthLevel, ValidationStatus, Visibility
from xbrain_memory.providers.native_stub import NativeStubProvider

from app.models.promotion import Promotion
from app.repos import teams as teams_repo
from app.repos import users as users_repo
from app.services import truth_workflow

pytestmark = pytest.mark.asyncio


# === Helpers ===

def _make_item(team_scope: str, level: TruthLevel = TruthLevel.CANONICAL) -> MemoryItem:
    now = datetime.now(timezone.utc)
    return MemoryItem(
        id=str(uuid.uuid4()),
        team_scope=team_scope,
        project_scope=None,
        content="The build broke at commit abc123",
        metadata={},
        embedding=None,
        visibility=Visibility.TEAM,
        truth_level=level,
        confidence=0.9,
        source="test",
        validation_status=ValidationStatus.PENDING,
        created_at=now,
        updated_at=now,
    )


async def _seed_team_with_two_admins(session) -> dict:
    """Team-a with alice (admin), bob (admin), carol (member)."""
    alice = await users_repo.get_or_create_user(
        session, source_user_id="alice-sub", email="alice@t.local", display_name="Alice"
    )
    bob = await users_repo.get_or_create_user(
        session, source_user_id="bob-sub", email="bob@t.local", display_name="Bob"
    )
    carol = await users_repo.get_or_create_user(
        session, source_user_id="carol-sub", email="carol@t.local", display_name="Carol"
    )
    team = await teams_repo.create_team(
        session, slug="team-a", display_name="Team A", creator_user_id=alice.id
    )
    await teams_repo.add_member(session, team_id=team.id, user_id=bob.id, role="admin")
    await teams_repo.add_member(session, team_id=team.id, user_id=carol.id, role="member")
    await session.commit()
    return {"alice": alice, "bob": bob, "carol": carol, "team": team}


# === The decision: levels this workflow no longer sets ===


@pytest.mark.parametrize(
    ("start", "target", "owner_phrase"),
    [
        (TruthLevel.WORKING, TruthLevel.VALIDATED, "important/final"),
        (TruthLevel.VALIDATED, TruthLevel.CANONICAL, "star"),
        (TruthLevel.EPHEMERAL, TruthLevel.WORKING, "ingest default"),
    ],
)
async def test_levels_owned_elsewhere_are_refused(session, start, target, owner_phrase):
    """Two half-used trails is how "who approved this?" gets the wrong answer.

    Each of these levels now has exactly one writer, none of them this table. The
    refusal NAMES the owner rather than saying only "no" — a caller who reaches
    for the old endpoint needs to learn where the level actually lives.
    """
    seed = await _seed_team_with_two_admins(session)
    provider = NativeStubProvider()
    item_id = await provider.upsert(_make_item("team-a", start))

    with pytest.raises(truth_workflow.WorkflowError) as exc:
        await truth_workflow.propose_promotion(
            session, provider=provider, item_id=item_id, team_scope="team-a",
            proposer_id=seed["alice"].id, target_level=target,
        )
    assert exc.value.status_code == 422
    assert owner_phrase in exc.value.detail, (
        f"the refusal must name who owns {target.value}, got: {exc.value.detail!r}"
    )

    # Nothing moved, and no row was written to speak for a decision nobody made.
    refreshed = await provider.get(item_id, team_scope="team-a")
    assert refreshed.truth_level == start
    rows = (
        await session.execute(select(Promotion).where(Promotion.team_scope == "team-a"))
    ).scalars().all()
    assert rows == []


async def test_a_legacy_pending_row_cannot_be_approved(session):
    """A row proposed before the narrowing must not be honoured afterwards.

    Its target level has a different owner now, so applying the old approval
    would write exactly the second answer this narrowing exists to prevent. It is
    refused, not KeyError'd, and refused rather than silently applied.
    """
    seed = await _seed_team_with_two_admins(session)
    provider = NativeStubProvider()
    item_id = await provider.upsert(_make_item("team-a", TruthLevel.WORKING))

    # Written directly: the propose path refuses to create one of these today,
    # which is the point — this is the shape already sitting in a live database.
    legacy = Promotion(
        memory_item_id=uuid.UUID(item_id),
        team_scope="team-a",
        from_level="WORKING",
        to_level="VALIDATED",
        proposed_by=seed["alice"].id,
        status="pending",
    )
    session.add(legacy)
    await session.flush()

    with pytest.raises(truth_workflow.WorkflowError) as exc:
        await truth_workflow.approve_promotion(
            session, provider=provider, promotion_id=legacy.id,
            approver_id=seed["bob"].id, team_scope="team-a",
        )
    assert exc.value.status_code == 409
    assert "no longer set through the approval workflow" in exc.value.detail
    refreshed = await provider.get(item_id, team_scope="team-a")
    assert refreshed.truth_level == TruthLevel.WORKING


async def test_only_canonical_to_public_survives():
    """The lattice itself, stated once so a widening is a visible diff."""
    assert truth_workflow.ALLOWED_TRANSITIONS == {
        TruthLevel.EPHEMERAL: set(),
        TruthLevel.WORKING: set(),
        TruthLevel.VALIDATED: set(),
        TruthLevel.CANONICAL: {TruthLevel.PUBLIC},
        TruthLevel.PUBLIC: set(),
    }
    assert truth_workflow.APPROVAL_REQUIREMENTS == {TruthLevel.PUBLIC: 1}


# === The approval machinery that survives, on CANONICAL -> PUBLIC ===


async def test_propose_public_stays_pending(session):
    """Nothing auto-approves: sharing beyond the team needs somebody to decide."""
    seed = await _seed_team_with_two_admins(session)
    provider = NativeStubProvider()
    item_id = await provider.upsert(_make_item("team-a", TruthLevel.CANONICAL))

    promo = await truth_workflow.propose_promotion(
        session,
        provider=provider,
        item_id=item_id,
        team_scope="team-a",
        proposer_id=seed["carol"].id,   # a plain member may REQUEST
        target_level=TruthLevel.PUBLIC,
        rationale="the customer asked for this publicly",
    )
    await session.flush()

    assert promo.status == "pending"
    assert promo.resolved_at is None
    assert promo.rationale == "the customer asked for this publicly"
    refreshed = await provider.get(item_id, team_scope="team-a")
    assert refreshed.truth_level == TruthLevel.CANONICAL, (
        "a request is not a grant"
    )


async def test_public_applied_after_one_admin_approval(session):
    seed = await _seed_team_with_two_admins(session)
    provider = NativeStubProvider()
    item_id = await provider.upsert(_make_item("team-a", TruthLevel.CANONICAL))

    promo = await truth_workflow.propose_promotion(
        session, provider=provider, item_id=item_id, team_scope="team-a",
        proposer_id=seed["carol"].id, target_level=TruthLevel.PUBLIC,
    )
    await session.flush()

    promo = await truth_workflow.approve_promotion(
        session, provider=provider, promotion_id=promo.id,
        approver_id=seed["bob"].id, team_scope="team-a",
    )
    await session.flush()

    assert promo.status == "approved"
    assert promo.approved_by_1 == seed["bob"].id
    assert promo.resolved_at is not None
    refreshed = await provider.get(item_id, team_scope="team-a")
    assert refreshed.truth_level == TruthLevel.PUBLIC
    assert refreshed.validation_status == ValidationStatus.VALIDATED


async def test_requester_cannot_approve_their_own_request(session):
    seed = await _seed_team_with_two_admins(session)
    provider = NativeStubProvider()
    item_id = await provider.upsert(_make_item("team-a", TruthLevel.CANONICAL))

    promo = await truth_workflow.propose_promotion(
        session, provider=provider, item_id=item_id, team_scope="team-a",
        proposer_id=seed["alice"].id, target_level=TruthLevel.PUBLIC,
    )
    await session.flush()

    with pytest.raises(truth_workflow.WorkflowError) as exc:
        await truth_workflow.approve_promotion(
            session, provider=provider, promotion_id=promo.id,
            approver_id=seed["alice"].id, team_scope="team-a",
        )
    assert exc.value.status_code == 403
    assert "proposer" in exc.value.detail.lower()
    refreshed = await provider.get(item_id, team_scope="team-a")
    assert refreshed.truth_level == TruthLevel.CANONICAL


async def test_member_cannot_approve_public(session):
    """Only an admin may grant it — the sharing design's central rule."""
    seed = await _seed_team_with_two_admins(session)
    provider = NativeStubProvider()
    item_id = await provider.upsert(_make_item("team-a", TruthLevel.CANONICAL))

    promo = await truth_workflow.propose_promotion(
        session, provider=provider, item_id=item_id, team_scope="team-a",
        proposer_id=seed["alice"].id, target_level=TruthLevel.PUBLIC,
    )
    await session.flush()

    with pytest.raises(truth_workflow.WorkflowError) as exc:
        await truth_workflow.approve_promotion(
            session, provider=provider, promotion_id=promo.id,
            approver_id=seed["carol"].id, team_scope="team-a",
        )
    assert exc.value.status_code == 403
    assert "admin" in exc.value.detail.lower()
    refreshed = await provider.get(item_id, team_scope="team-a")
    assert refreshed.truth_level == TruthLevel.CANONICAL


async def test_demotion_returns_422(session):
    """PUBLIC → CANONICAL is not un-sharing, and must not be spelled this way.

    Revoking stops future access; it cannot recall what somebody already read.
    That is its own operation with its own record — not a backwards edge here.
    """
    seed = await _seed_team_with_two_admins(session)
    provider = NativeStubProvider()
    item_id = await provider.upsert(_make_item("team-a", TruthLevel.PUBLIC))

    with pytest.raises(truth_workflow.WorkflowError) as exc:
        await truth_workflow.propose_promotion(
            session, provider=provider, item_id=item_id, team_scope="team-a",
            proposer_id=seed["alice"].id, target_level=TruthLevel.CANONICAL,
        )
    assert exc.value.status_code == 422


async def test_reject_keeps_truth_level_and_records_reason(session):
    """A refusal is a record: who asked, what for, and why it was refused."""
    seed = await _seed_team_with_two_admins(session)
    provider = NativeStubProvider()
    item_id = await provider.upsert(_make_item("team-a", TruthLevel.CANONICAL))

    promo = await truth_workflow.propose_promotion(
        session, provider=provider, item_id=item_id, team_scope="team-a",
        proposer_id=seed["carol"].id, target_level=TruthLevel.PUBLIC,
    )
    await session.flush()

    promo = await truth_workflow.reject_promotion(
        session, promotion_id=promo.id, rejector_id=seed["bob"].id,
        team_scope="team-a", reason="contains a customer name",
    )
    await session.flush()

    assert promo.status == "rejected"
    assert promo.rejection_reason == "contains a customer name"
    assert promo.proposed_by == seed["carol"].id
    assert promo.resolved_at is not None
    refreshed = await provider.get(item_id, team_scope="team-a")
    assert refreshed.truth_level == TruthLevel.CANONICAL


async def test_resolved_promotion_cannot_be_re_approved(session):
    seed = await _seed_team_with_two_admins(session)
    provider = NativeStubProvider()
    item_id = await provider.upsert(_make_item("team-a", TruthLevel.CANONICAL))

    promo = await truth_workflow.propose_promotion(
        session, provider=provider, item_id=item_id, team_scope="team-a",
        proposer_id=seed["carol"].id, target_level=TruthLevel.PUBLIC,
    )
    await session.flush()
    promo = await truth_workflow.approve_promotion(
        session, provider=provider, promotion_id=promo.id,
        approver_id=seed["bob"].id, team_scope="team-a",
    )
    await session.flush()
    assert promo.status == "approved"

    with pytest.raises(truth_workflow.WorkflowError) as exc:
        await truth_workflow.approve_promotion(
            session, provider=provider, promotion_id=promo.id,
            approver_id=seed["bob"].id, team_scope="team-a",
        )
    assert exc.value.status_code == 409


async def test_team_isolation_promotion_lookup_in_other_team_404(session):
    """A promotion created in team-a is not visible to team-b queries."""
    seed = await _seed_team_with_two_admins(session)
    eve = await users_repo.get_or_create_user(
        session, source_user_id="eve-sub", email="eve@t.local", display_name="Eve"
    )
    await teams_repo.create_team(
        session, slug="team-b", display_name="Team B", creator_user_id=eve.id
    )
    await session.commit()

    provider = NativeStubProvider()
    item_id = await provider.upsert(_make_item("team-a", TruthLevel.CANONICAL))
    promo = await truth_workflow.propose_promotion(
        session, provider=provider, item_id=item_id, team_scope="team-a",
        proposer_id=seed["alice"].id, target_level=TruthLevel.PUBLIC,
    )
    await session.flush()

    with pytest.raises(truth_workflow.WorkflowError) as exc:
        await truth_workflow.approve_promotion(
            session, provider=provider, promotion_id=promo.id,
            approver_id=eve.id, team_scope="team-b",
        )
    assert exc.value.status_code == 404


async def test_pending_listing_filters_by_team(session):
    seed = await _seed_team_with_two_admins(session)
    provider = NativeStubProvider()
    for _ in range(2):
        item_id = await provider.upsert(_make_item("team-a", TruthLevel.CANONICAL))
        await truth_workflow.propose_promotion(
            session, provider=provider, item_id=item_id, team_scope="team-a",
            proposer_id=seed["alice"].id, target_level=TruthLevel.PUBLIC,
        )
    await session.flush()

    pending = await truth_workflow.list_pending(session, team_scope="team-a")
    assert len(pending) == 2
    pending_other = await truth_workflow.list_pending(session, team_scope="team-b")
    assert len(pending_other) == 0
