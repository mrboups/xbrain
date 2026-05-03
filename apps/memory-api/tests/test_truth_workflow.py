"""Truth-level promotion workflow tests — state machine + 4-eyes invariants.

Uses NativeStubProvider in-process for the memory layer; Promotion rows go through
the real SQLAlchemy session (testcontainers Postgres). This isolates the workflow
state machine + DB row mutations from any specific provider impl.
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

def _make_item(team_scope: str, level: TruthLevel = TruthLevel.EPHEMERAL) -> MemoryItem:
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


# === Tests ===


async def test_propose_ephemeral_to_working_auto_approves(session):
    """WORKING requires 0 approvers → auto-applied on propose."""
    seed = await _seed_team_with_two_admins(session)
    provider = NativeStubProvider()
    item = _make_item("team-a", TruthLevel.EPHEMERAL)
    item_id = await provider.upsert(item)

    promo = await truth_workflow.propose_promotion(
        session,
        provider=provider,
        item_id=item_id,
        team_scope="team-a",
        proposer_id=seed["alice"].id,
        target_level=TruthLevel.WORKING,
        rationale="ready for team review",
    )
    await session.flush()

    assert promo.status == "auto"
    assert promo.resolved_at is not None
    # Item was actually promoted in the provider
    refreshed = await provider.get(item_id, team_scope="team-a")
    assert refreshed.truth_level == TruthLevel.WORKING


async def test_propose_working_to_validated_stays_pending(session):
    """VALIDATED requires 1 admin approver → propose creates pending row, no mutation."""
    seed = await _seed_team_with_two_admins(session)
    provider = NativeStubProvider()
    item = _make_item("team-a", TruthLevel.WORKING)
    item_id = await provider.upsert(item)

    promo = await truth_workflow.propose_promotion(
        session,
        provider=provider,
        item_id=item_id,
        team_scope="team-a",
        proposer_id=seed["alice"].id,
        target_level=TruthLevel.VALIDATED,
    )
    await session.flush()

    assert promo.status == "pending"
    assert promo.resolved_at is None
    # truth_level NOT mutated yet
    refreshed = await provider.get(item_id, team_scope="team-a")
    assert refreshed.truth_level == TruthLevel.WORKING


async def test_validated_promoted_after_one_admin_approval(session):
    seed = await _seed_team_with_two_admins(session)
    provider = NativeStubProvider()
    item_id = await provider.upsert(_make_item("team-a", TruthLevel.WORKING))

    promo = await truth_workflow.propose_promotion(
        session,
        provider=provider,
        item_id=item_id,
        team_scope="team-a",
        proposer_id=seed["alice"].id,
        target_level=TruthLevel.VALIDATED,
    )
    await session.flush()

    # Bob (admin, distinct from proposer alice) approves
    promo = await truth_workflow.approve_promotion(
        session,
        provider=provider,
        promotion_id=promo.id,
        approver_id=seed["bob"].id,
        team_scope="team-a",
    )
    await session.flush()

    assert promo.status == "approved"
    refreshed = await provider.get(item_id, team_scope="team-a")
    assert refreshed.truth_level == TruthLevel.VALIDATED
    assert refreshed.validation_status == ValidationStatus.VALIDATED


async def test_canonical_requires_two_distinct_admins(session):
    """4-eyes principle for CANONICAL: 1 approval → still pending; 2 approvals → applied."""
    seed = await _seed_team_with_two_admins(session)
    # We need a 3rd admin (dave) so 2 approvers can both be ≠ proposer
    dave = await users_repo.get_or_create_user(
        session, source_user_id="dave-sub", email="dave@t.local", display_name="Dave"
    )
    await teams_repo.add_member(
        session, team_id=seed["team"].id, user_id=dave.id, role="admin"
    )
    await session.commit()

    provider = NativeStubProvider()
    item_id = await provider.upsert(_make_item("team-a", TruthLevel.VALIDATED))

    promo = await truth_workflow.propose_promotion(
        session,
        provider=provider,
        item_id=item_id,
        team_scope="team-a",
        proposer_id=seed["alice"].id,
        target_level=TruthLevel.CANONICAL,
    )
    await session.flush()

    # First admin approval — still pending
    promo = await truth_workflow.approve_promotion(
        session, provider=provider, promotion_id=promo.id,
        approver_id=seed["bob"].id, team_scope="team-a",
    )
    await session.flush()
    assert promo.status == "pending"
    assert promo.approved_by_1 == seed["bob"].id
    refreshed = await provider.get(item_id, team_scope="team-a")
    assert refreshed.truth_level == TruthLevel.VALIDATED  # not yet promoted

    # Second distinct admin approval — applies
    promo = await truth_workflow.approve_promotion(
        session, provider=provider, promotion_id=promo.id,
        approver_id=dave.id, team_scope="team-a",
    )
    await session.flush()
    assert promo.status == "approved"
    assert promo.approved_by_2 == dave.id
    refreshed = await provider.get(item_id, team_scope="team-a")
    assert refreshed.truth_level == TruthLevel.CANONICAL


async def test_proposer_cannot_approve_own_promotion(session):
    seed = await _seed_team_with_two_admins(session)
    provider = NativeStubProvider()
    item_id = await provider.upsert(_make_item("team-a", TruthLevel.WORKING))

    promo = await truth_workflow.propose_promotion(
        session, provider=provider, item_id=item_id, team_scope="team-a",
        proposer_id=seed["alice"].id, target_level=TruthLevel.VALIDATED,
    )
    await session.flush()

    with pytest.raises(truth_workflow.WorkflowError) as exc:
        await truth_workflow.approve_promotion(
            session, provider=provider, promotion_id=promo.id,
            approver_id=seed["alice"].id, team_scope="team-a",
        )
    assert exc.value.status_code == 403
    assert "proposer" in exc.value.detail.lower()


async def test_member_cannot_approve_validated(session):
    seed = await _seed_team_with_two_admins(session)
    provider = NativeStubProvider()
    item_id = await provider.upsert(_make_item("team-a", TruthLevel.WORKING))

    promo = await truth_workflow.propose_promotion(
        session, provider=provider, item_id=item_id, team_scope="team-a",
        proposer_id=seed["alice"].id, target_level=TruthLevel.VALIDATED,
    )
    await session.flush()

    with pytest.raises(truth_workflow.WorkflowError) as exc:
        await truth_workflow.approve_promotion(
            session, provider=provider, promotion_id=promo.id,
            approver_id=seed["carol"].id, team_scope="team-a",
        )
    assert exc.value.status_code == 403
    assert "admin" in exc.value.detail.lower()


async def test_illegal_transition_skip_returns_422(session):
    """EPHEMERAL → CANONICAL is a skip and must be rejected."""
    seed = await _seed_team_with_two_admins(session)
    provider = NativeStubProvider()
    item_id = await provider.upsert(_make_item("team-a", TruthLevel.EPHEMERAL))

    with pytest.raises(truth_workflow.WorkflowError) as exc:
        await truth_workflow.propose_promotion(
            session, provider=provider, item_id=item_id, team_scope="team-a",
            proposer_id=seed["alice"].id, target_level=TruthLevel.CANONICAL,
        )
    assert exc.value.status_code == 422


async def test_demotion_returns_422(session):
    """VALIDATED → WORKING (going backwards) is forbidden."""
    seed = await _seed_team_with_two_admins(session)
    provider = NativeStubProvider()
    item_id = await provider.upsert(_make_item("team-a", TruthLevel.VALIDATED))

    with pytest.raises(truth_workflow.WorkflowError) as exc:
        await truth_workflow.propose_promotion(
            session, provider=provider, item_id=item_id, team_scope="team-a",
            proposer_id=seed["alice"].id, target_level=TruthLevel.WORKING,
        )
    assert exc.value.status_code == 422


async def test_reject_keeps_truth_level_and_records_reason(session):
    seed = await _seed_team_with_two_admins(session)
    provider = NativeStubProvider()
    item_id = await provider.upsert(_make_item("team-a", TruthLevel.WORKING))

    promo = await truth_workflow.propose_promotion(
        session, provider=provider, item_id=item_id, team_scope="team-a",
        proposer_id=seed["alice"].id, target_level=TruthLevel.VALIDATED,
    )
    await session.flush()

    promo = await truth_workflow.reject_promotion(
        session, promotion_id=promo.id, rejector_id=seed["bob"].id,
        team_scope="team-a", reason="Not enough evidence",
    )
    await session.flush()

    assert promo.status == "rejected"
    assert promo.rejection_reason == "Not enough evidence"
    assert promo.resolved_at is not None
    refreshed = await provider.get(item_id, team_scope="team-a")
    assert refreshed.truth_level == TruthLevel.WORKING


async def test_resolved_promotion_cannot_be_re_approved(session):
    seed = await _seed_team_with_two_admins(session)
    provider = NativeStubProvider()
    item_id = await provider.upsert(_make_item("team-a", TruthLevel.WORKING))

    promo = await truth_workflow.propose_promotion(
        session, provider=provider, item_id=item_id, team_scope="team-a",
        proposer_id=seed["alice"].id, target_level=TruthLevel.VALIDATED,
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
    # Add team-b with bob as admin
    eve = await users_repo.get_or_create_user(
        session, source_user_id="eve-sub", email="eve@t.local", display_name="Eve"
    )
    team_b = await teams_repo.create_team(
        session, slug="team-b", display_name="Team B", creator_user_id=eve.id
    )
    await session.commit()

    provider = NativeStubProvider()
    item_id = await provider.upsert(_make_item("team-a", TruthLevel.WORKING))
    promo = await truth_workflow.propose_promotion(
        session, provider=provider, item_id=item_id, team_scope="team-a",
        proposer_id=seed["alice"].id, target_level=TruthLevel.VALIDATED,
    )
    await session.flush()

    # eve from team-b cannot see/approve this promotion
    with pytest.raises(truth_workflow.WorkflowError) as exc:
        await truth_workflow.approve_promotion(
            session, provider=provider, promotion_id=promo.id,
            approver_id=eve.id, team_scope="team-b",
        )
    assert exc.value.status_code == 404


async def test_pending_listing_filters_by_team(session):
    seed = await _seed_team_with_two_admins(session)
    provider = NativeStubProvider()
    # 2 pending in team-a
    for _ in range(2):
        item_id = await provider.upsert(_make_item("team-a", TruthLevel.WORKING))
        await truth_workflow.propose_promotion(
            session, provider=provider, item_id=item_id, team_scope="team-a",
            proposer_id=seed["alice"].id, target_level=TruthLevel.VALIDATED,
        )
    await session.flush()

    pending = await truth_workflow.list_pending(session, team_scope="team-a")
    assert len(pending) == 2
    pending_other = await truth_workflow.list_pending(session, team_scope="team-b")
    assert len(pending_other) == 0
