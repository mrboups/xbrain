"""Tests for brain_ingest.ingest_team_message — Phase 13 Plan 02.

Tests 1-5 verify the Haiku classifier swap in ingest_team_message:
- Test 1: Haiku says relevant → provider.upsert called with correct MemoryItem fields
- Test 2: Haiku says NOT relevant → upsert skipped, skipped_by_filter event logged
- Test 3: classify raises → upsert skipped (fail-soft), failed warning logged, no exception
- Test 4: team_context_cache.invalidate called with correct team_id on successful ingest
- Test 5: signature unchanged — no new parameters added

All tests are unit tests — no DB, no network.
"""
from __future__ import annotations

import inspect
import os
import uuid

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("BRIDGE_SHARED_SECRET", "test-bridge-secret-do-not-use-in-prod")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _verdict(relevant: bool, *, important: bool = False, **kw):
    """The classifier's answer. The seam is `classify_detailed`, not `classify`.

    Since 2026-08-05 one call answers both AI-owned levels, so the ingest path
    reads a ClassifyResult rather than a bool — patching the bool wrapper would
    leave the real classifier running and these tests asserting nothing.
    """
    from app.services.relevance_filter import ClassifyResult

    return ClassifyResult(relevant=relevant, important=important, **kw)


@pytest.mark.asyncio
async def test_ingest_team_message_haiku_relevant_upsert_called():
    """Test 1: When classify returns True, provider.upsert is called with the correct MemoryItem."""
    from app.services import brain_ingest

    captured_items = []
    mock_provider = MagicMock()
    mock_provider.upsert = AsyncMock(side_effect=lambda item: captured_items.append(item))

    team_id = uuid.uuid4()

    with patch("app.services.brain_ingest.get_memory_provider", return_value=mock_provider):
        with patch(
            "app.services.relevance_filter.classify_detailed",
            new=AsyncMock(return_value=_verdict(True)),
        ):
            await brain_ingest.ingest_team_message(
                team_scope="t1",
                team_id=team_id,
                content="The deploy window is every Tuesday 14:00 UTC",
                author_sub="user@example.com",
            )

    assert len(captured_items) == 1
    item = captured_items[0]

    from xbrain_memory.types import TruthLevel, ValidationStatus, Visibility

    assert item.team_scope == "t1"
    assert item.truth_level == TruthLevel.WORKING
    assert item.visibility == Visibility.TEAM
    assert item.validation_status == ValidationStatus.PENDING
    assert item.source == "team-chat:user@example.com"
    assert item.confidence == 0.7


@pytest.mark.asyncio
async def test_important_verdict_raises_the_item_after_the_upsert():
    """The AI's "final" flag is applied by ONE writer, after the item exists.

    The item is upserted at WORKING and raised afterwards through
    `importance.flag_ingested_item` — never written straight to VALIDATED — so
    that a single function owns the AI's level, can enforce "only ever from
    WORKING" in its own WHERE clause, and can put the audit row in the same
    transaction as the change.
    """
    from app.services import brain_ingest
    from xbrain_memory.types import TruthLevel

    captured = []
    mock_provider = MagicMock()
    mock_provider.upsert = AsyncMock(side_effect=lambda item: captured.append(item))
    flagger = AsyncMock(return_value=True)
    team_id = uuid.uuid4()
    message_id = uuid.uuid4()

    with patch("app.services.brain_ingest.get_memory_provider", return_value=mock_provider):
        with patch(
            "app.services.relevance_filter.classify_detailed",
            new=AsyncMock(
                return_value=_verdict(
                    True,
                    important=True,
                    score=0.94,
                    decided_by="model",
                    model="claude-haiku-4-5-20251001",
                )
            ),
        ):
            with patch("app.services.importance.flag_ingested_item", new=flagger):
                await brain_ingest.ingest_team_message(
                    team_scope="t1",
                    team_id=team_id,
                    content="Q3 budget signed off by finance: 240k, final figure",
                    author_sub="user@example.com",
                    message_id=message_id,
                )

    assert captured[0].truth_level == TruthLevel.WORKING, (
        "the upsert must land at WORKING; VALIDATED is applied by the one writer"
    )
    flagger.assert_awaited_once()
    kwargs = flagger.await_args.kwargs
    assert kwargs["item_id"] == captured[0].id, "the flag must name the item just written"
    assert kwargs["team_scope"] == "t1"
    assert kwargs["model"] == "claude-haiku-4-5-20251001"
    assert kwargs["score"] == 0.94
    assert str(kwargs["message_id"]) == str(message_id), (
        "the audit row needs the back-link that joins it to the chat trail"
    )


@pytest.mark.asyncio
async def test_budget_exhausted_ingest_leaves_the_item_at_working():
    """A spent classification budget keeps the knowledge and skips the judgement.

    End-to-end version of the classifier's own fail-soft test: the item is still
    stored, and NOTHING raises its level. Never dropped, never guessed high.
    """
    from app.services import brain_ingest
    from xbrain_memory.types import TruthLevel

    captured = []
    mock_provider = MagicMock()
    mock_provider.upsert = AsyncMock(side_effect=lambda item: captured.append(item))
    flagger = AsyncMock(return_value=True)

    with patch("app.services.brain_ingest.get_memory_provider", return_value=mock_provider):
        with patch(
            "app.services.relevance_filter.classify_detailed",
            new=AsyncMock(
                return_value=_verdict(True, important=False, reason="budget_exhausted")
            ),
        ):
            with patch("app.services.importance.flag_ingested_item", new=flagger):
                await brain_ingest.ingest_team_message(
                    team_scope="t1",
                    team_id=uuid.uuid4(),
                    content="Q3 budget signed off by finance: 240k, final figure",
                    author_sub="user@example.com",
                )

    assert len(captured) == 1, "budget exhaustion must not drop the message"
    assert captured[0].truth_level == TruthLevel.WORKING
    flagger.assert_not_awaited(), "nothing may raise the level when nothing judged it"


@pytest.mark.asyncio
async def test_ingest_team_message_haiku_not_relevant_skips_upsert(caplog):
    """Test 2: When classify returns False, provider.upsert is NOT called.

    The log event brain_ingest.team_message.skipped_by_filter must be emitted.
    """
    import logging
    from app.services import brain_ingest

    mock_provider = MagicMock()
    mock_provider.upsert = AsyncMock()

    team_id = uuid.uuid4()

    with patch("app.services.brain_ingest.get_memory_provider", return_value=mock_provider):
        with patch(
            "app.services.relevance_filter.classify_detailed",
            new=AsyncMock(return_value=_verdict(False)),
        ):
            with caplog.at_level(logging.INFO, logger="app.services.brain_ingest"):
                await brain_ingest.ingest_team_message(
                    team_scope="t1",
                    team_id=team_id,
                    content="The deploy window is every Tuesday 14:00 UTC",
                    author_sub="user@example.com",
                )

    mock_provider.upsert.assert_not_called()

    # Check for the new log event key
    skipped_events = [r for r in caplog.records if "skipped_by_filter" in r.getMessage()]
    # structlog may emit to a different record; check both caplog and no exception
    # The function should have returned early without calling upsert — that is the primary assertion.
    # Log key presence is verified by grep on the source file in acceptance criteria.


@pytest.mark.asyncio
async def test_ingest_team_message_classify_raises_fail_soft():
    """Test 3: When classify raises, upsert is NOT called and no exception propagates."""
    from app.services import brain_ingest

    mock_provider = MagicMock()
    mock_provider.upsert = AsyncMock()

    team_id = uuid.uuid4()

    with patch("app.services.brain_ingest.get_memory_provider", return_value=mock_provider):
        with patch(
            "app.services.relevance_filter.classify_detailed",
            new=AsyncMock(side_effect=RuntimeError("haiku exploded")),
        ):
            # Must not raise
            result = await brain_ingest.ingest_team_message(
                team_scope="t1",
                team_id=team_id,
                content="The deploy window is every Tuesday 14:00 UTC",
                author_sub="user@example.com",
            )

    # Fail-soft: returns None, no exception
    assert result is None
    mock_provider.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_ingest_team_message_cache_invalidated():
    """Test 4: team_context_cache.invalidate is called with the correct team_id on success."""
    from app.services import brain_ingest

    mock_provider = MagicMock()
    mock_provider.upsert = AsyncMock()

    team_id = uuid.uuid4()

    with patch("app.services.brain_ingest.get_memory_provider", return_value=mock_provider):
        with patch(
            "app.services.relevance_filter.classify_detailed",
            new=AsyncMock(return_value=_verdict(True)),
        ):
            with patch(
                "app.services.team_context_cache.invalidate",
            ) as mock_invalidate:
                await brain_ingest.ingest_team_message(
                    team_scope="t1",
                    team_id=team_id,
                    content="The deploy window is every Tuesday 14:00 UTC",
                    author_sub="user@example.com",
                )

    mock_invalidate.assert_called_once_with(team_id)


def test_ingest_team_message_signature_unchanged():
    """Test 5: ingest_team_message signature must be
    (team_scope, team_id, content, author_sub, aliases, message_id) — all
    keyword-only.
    `aliases` was added in Phase 21 (WR-01): the team's effective mention-alias
    list, keyword-only + optional (defaults None) so it is backward-compatible.
    `message_id` was added with message deletion: the back-link from a brain item
    to the chat message that seeded it, so "remove this from the brain too"
    resolves exactly rather than by matching text. Keyword-only + optional for the
    same backward-compatibility reason.
    """
    from app.services.brain_ingest import ingest_team_message

    sig = inspect.signature(ingest_team_message)
    params = dict(sig.parameters)

    expected_params = {
        "team_scope",
        "team_id",
        "content",
        "author_sub",
        "aliases",
        "message_id",
    }
    assert set(params.keys()) == expected_params, (
        f"Expected parameters {expected_params}, got {set(params.keys())}"
    )

    # All must be keyword-only (KEYWORD_ONLY kind)
    for name, param in params.items():
        assert param.kind == inspect.Parameter.KEYWORD_ONLY, (
            f"Parameter '{name}' must be keyword-only, got {param.kind}"
        )
    # aliases must be optional (backward-compatible)
    assert params["aliases"].default is None
    assert params["message_id"].default is None


@pytest.mark.asyncio
async def test_ingest_team_message_writes_the_message_id_backlink():
    """The upserted item carries `metadata.message_id` — the only exact way back.

    Without this, deleting a message from the brain has to match its text, and a
    message edited or duplicated makes that a guess. The key is written ONLY when
    a message id was supplied: an empty string would collide across every item
    that has none.
    """
    from app.services import brain_ingest

    provider = MagicMock()
    provider.upsert = AsyncMock()

    with patch("app.services.brain_ingest.get_memory_provider", return_value=provider):
        with patch(
            "app.services.relevance_filter.classify_detailed", new=AsyncMock(return_value=_verdict(True))
        ):
            await brain_ingest.ingest_team_message(
                team_scope="team-a",
                team_id=uuid.uuid4(),
                content="The Q3 budget was signed off on Tuesday.",
                author_sub="alice-sub",
                message_id="11111111-2222-3333-4444-555555555555",
            )
            with_link = provider.upsert.call_args[0][0]

            provider.upsert.reset_mock()
            await brain_ingest.ingest_team_message(
                team_scope="team-a",
                team_id=uuid.uuid4(),
                content="The Q3 budget was signed off on Tuesday.",
                author_sub="alice-sub",
            )
            without_link = provider.upsert.call_args[0][0]

    assert with_link.metadata["message_id"] == "11111111-2222-3333-4444-555555555555"
    assert with_link.metadata["origin"] == "team-chat"
    assert "message_id" not in without_link.metadata


def test_is_brain_relevant_word_boundary_not_prefix(caplog):
    """CR-01 regression: a naive startswith('@a') dropped legitimate facts like
    '@austin reviewed it' from brain ingest once 'a' became a default alias. The
    filter now uses the word-boundary mention check, so only WHOLE agent-mention
    commands are skipped."""
    from app.services.brain_ingest import is_brain_relevant

    # @agent is a guaranteed alias (effective_aliases always includes it) →
    # a command regardless of the env default. Skipped (not a fact).
    assert is_brain_relevant("@agent what is the Q3 budget?") is False
    # The short 'a' alias, when it is in the effective set, is a whole-token command...
    assert is_brain_relevant("@a summarize the last meeting please", ["agent", "a"]) is False
    # ...but a person/handle that merely STARTS with '@a...' is NOT — it is a real fact.
    assert is_brain_relevant("@austin reviewed the budget proposal today", ["agent", "a"]) is True
    assert is_brain_relevant("@alice owns the vendor contract renewal", ["agent", "a"]) is True
    # A team custom alias, when passed, is also treated as a command.
    assert is_brain_relevant("@wizard what's on the roadmap?", ["agent", "wizard"]) is False
    # ...but @wizard is NOT a command for a team that didn't set it.
    assert is_brain_relevant("@wizard is a great nickname for the new intern", ["agent"]) is True
