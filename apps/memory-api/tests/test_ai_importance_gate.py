"""THE gate for the AI's important/final flag — real Postgres, real audit rows.

The flag is the first truth level in this project's history that a MODEL sets,
and the first that anything is allowed to LOWER. Both of those are only safe if
two properties hold, so both are asserted here against a real database rather
than a mock that cannot disagree:

  1. THE AI CANNOT REACH STARRED. `CANONICAL` is the level a person sets. The
     enforcement is not a caller remembering to check — it is the `truth_level`
     named in each statement's WHERE clause, so an item a person starred matches
     neither the flag nor the unflag. Cases 3 and 4 vary only that one column.
  2. EVERY CHANGE NAMES ITS ACTOR, AND A NON-CHANGE WRITES NOTHING.
     `actor_user_id` is NULL for an AI-set level, which is ambiguous between "the
     agent", "the system" and "nobody recorded it" — so the payload must say
     which. And a call that moves no row must leave no trace, or the trail fills
     with events that never happened.

SKIP=FAIL discipline: the `integration` marker lets CI's skip-grep capture this
file. A clean SKIP is legitimate ONLY when Docker is genuinely absent (conftest
gate); under Docker this file MUST run green.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
import sqlalchemy as sa

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _insert_item(
    session,
    *,
    team_scope: str,
    truth_level: str,
    content: str = "Q3 budget signed off by finance: 240k",
    metadata: dict[str, Any] | None = None,
    validation_status: str = "pending",
) -> str:
    item_id = str(uuid.uuid4())
    await session.execute(
        sa.text(
            """
            INSERT INTO memory_items
                (id, team_scope, content, metadata, visibility, truth_level,
                 validation_status, confidence, source)
            VALUES
                (:id, :ts, :content, CAST(:md AS jsonb), 'team', :level,
                 :vs, 0.7, 'team-chat:alice-sub')
            """
        ),
        {
            "id": item_id,
            "ts": team_scope,
            "content": content,
            "md": json.dumps(metadata or {}),
            "level": truth_level,
            "vs": validation_status,
        },
    )
    return item_id


async def _read_item(session, item_id: str) -> dict[str, Any]:
    row = (
        await session.execute(
            sa.text(
                "SELECT truth_level, validation_status, metadata "
                "FROM memory_items WHERE id = CAST(:id AS uuid)"
            ),
            {"id": item_id},
        )
    ).mappings().one()
    return dict(row)


async def _audit_rows(session, target_id: str) -> list[Any]:
    return (
        await session.execute(
            sa.text(
                "SELECT action, actor_user_id, team_scope, payload "
                "FROM audit_log WHERE target_id = :t ORDER BY id"
            ),
            {"t": str(target_id)},
        )
    ).fetchall()


async def test_ai_importance_gate(session, seeded_two_teams):
    from app.services import importance

    team_a = seeded_two_teams["team_a"]
    team_b = seeded_two_teams["team_b"]

    # ── 1. SET: WORKING → VALIDATED, named actor, one audit row ──────────
    item = await _insert_item(session, team_scope=team_a.slug, truth_level="WORKING")
    await session.commit()

    changed = await importance.set_ai_importance(
        session,
        team_scope=team_a.slug,
        item_ids=[item],
        important=True,
        model="claude-haiku-4-5-20251001",
        score=0.94,
        message_id="7d8e5f10-0000-4000-8000-000000000001",
        source="team-chat:alice-sub",
    )
    await session.commit()

    assert changed == [item]
    after = await _read_item(session, item)
    assert after["truth_level"] == "VALIDATED"
    assert after["validation_status"] == "validated"
    assert after["metadata"][importance.META_AI_IMPORTANT] is True, (
        "the flag must be legible on the item itself, not only in the trail"
    )
    assert after["metadata"][importance.META_AI_MODEL] == "claude-haiku-4-5-20251001"

    rows = await _audit_rows(session, item)
    assert len(rows) == 1, f"exactly one audit row per change, got {rows}"
    action, actor_user_id, audited_scope, payload = rows[0]
    assert action == "memory_item.flag_important"
    assert actor_user_id is None, "no person did this"
    assert payload["actor_kind"] == "ai", (
        "NULL actor_user_id is ambiguous between the agent, the system and an "
        "unrecorded actor — the row must SAY which"
    )
    assert payload["actor"] == "relevance_filter"
    assert payload["model"] == "claude-haiku-4-5-20251001"
    assert payload["score"] == 0.94
    assert payload["from"] == "WORKING"
    assert payload["to"] == "VALIDATED"
    assert payload["message_id"] == "7d8e5f10-0000-4000-8000-000000000001", (
        "the back-link is what joins this row to the team_message.* trail"
    )
    assert audited_scope == team_a.slug

    # ── 2. CLEAR: VALIDATED → WORKING. The flag is REVERSIBLE — that is the
    #        point, and it is what separates it from a star. ───────────────
    changed = await importance.set_ai_importance(
        session,
        team_scope=team_a.slug,
        item_ids=[item],
        important=False,
        model="claude-haiku-4-5-20251001",
    )
    await session.commit()

    assert changed == [item]
    after = await _read_item(session, item)
    assert after["truth_level"] == "WORKING"
    assert after["validation_status"] == "pending"
    assert after["metadata"][importance.META_AI_IMPORTANT] is False

    rows = await _audit_rows(session, item)
    assert len(rows) == 2, "the clear is its own event, not an edit of the first"
    assert rows[1][0] == "memory_item.unflag_important", (
        "set and clear must be DIFFERENT actions — the question later is which "
        "one happened, and the action string is what gets grepped"
    )
    assert rows[1][1] is None
    assert rows[1][3]["actor_kind"] == "ai"
    assert rows[1][3]["from"] == "VALIDATED"
    assert rows[1][3]["to"] == "WORKING"

    # ── 3. THE AI CANNOT REACH STARRED ───────────────────────────────────
    starred = await _insert_item(
        session,
        team_scope=team_a.slug,
        truth_level="CANONICAL",
        content="A person starred this one",
    )
    await session.commit()

    changed = await importance.set_ai_importance(
        session, team_scope=team_a.slug, item_ids=[starred], important=True,
    )
    await session.commit()
    assert changed == [], "the AI must not be able to promote INTO starred"
    assert (await _read_item(session, starred))["truth_level"] == "CANONICAL"

    # ── 4. …AND CANNOT DEMOTE OUT OF IT EITHER ───────────────────────────
    changed = await importance.set_ai_importance(
        session, team_scope=team_a.slug, item_ids=[starred], important=False,
    )
    await session.commit()
    assert changed == [], "nor demote a human judgement it never made"
    assert (await _read_item(session, starred))["truth_level"] == "CANONICAL"

    # A refused change writes NOTHING. A trail that records attempts as if they
    # were actions is worse than no trail.
    assert await _audit_rows(session, starred) == []

    # ── 5. A NO-OP IS NOT AN EVENT ───────────────────────────────────────
    already = await _insert_item(
        session, team_scope=team_a.slug, truth_level="VALIDATED",
        content="Already flagged", validation_status="validated",
    )
    await session.commit()

    changed = await importance.set_ai_importance(
        session, team_scope=team_a.slug, item_ids=[already], important=True,
    )
    await session.commit()
    assert changed == []
    assert await _audit_rows(session, already) == []

    # ── 6. CROSS-TEAM — a scope is a boundary, not a label ───────────────
    other = await _insert_item(session, team_scope=team_b.slug, truth_level="WORKING")
    await session.commit()

    changed = await importance.set_ai_importance(
        session, team_scope=team_a.slug, item_ids=[other], important=True,
    )
    await session.commit()
    assert changed == [], "team A's classifier must not reach team B's item"
    assert (await _read_item(session, other))["truth_level"] == "WORKING"
    assert await _audit_rows(session, other) == []

    # ── 7. A SOFT-DELETED ITEM IS NOT FLAGGABLE ──────────────────────────
    gone = await _insert_item(session, team_scope=team_a.slug, truth_level="WORKING")
    await session.execute(
        sa.text(
            "UPDATE memory_items SET deleted_at = NOW() WHERE id = CAST(:id AS uuid)"
        ),
        {"id": gone},
    )
    await session.commit()

    changed = await importance.set_ai_importance(
        session, team_scope=team_a.slug, item_ids=[gone], important=True,
    )
    await session.commit()
    assert changed == [], "an item somebody removed must not come back flagged"
    assert await _audit_rows(session, gone) == []


async def test_item_ai_important_reads_the_item_not_the_trail(session, seeded_two_teams):
    """`item_ai_important` answers from `metadata`, so un-starring can restore it.

    Kept separate because it is the read half the star path depends on: removing
    a star must put the item back to what the AI thought of it, not blindly to
    WORKING.
    """
    from app.services import importance

    team_a = seeded_two_teams["team_a"]
    plain = await _insert_item(session, team_scope=team_a.slug, truth_level="WORKING")
    flagged = await _insert_item(
        session, team_scope=team_a.slug, truth_level="WORKING", content="Final figure"
    )
    await session.commit()

    await importance.set_ai_importance(
        session, team_scope=team_a.slug, item_ids=[flagged], important=True,
    )
    await session.commit()

    assert await importance.item_ai_important(session, flagged) is True
    assert await importance.item_ai_important(session, plain) is False
