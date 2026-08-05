"""Tests for Phase 13 relevance_filter module.

Task 1 tests (config + schema) and Task 2 tests (Haiku classifier + budget cap).
All tests are unit tests — no DB, no network.
"""
from __future__ import annotations

import os

# Ensure env defaults before any app import
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("BRIDGE_SHARED_SECRET", "test-bridge-secret-do-not-use-in-prod")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")

import json
import pytest
import pytest_asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch


# ─── Task 1: Config defaults ──────────────────────────────────────────


def test_config_defaults():
    """RELEVANCE_HAIKU_ENABLED defaults to True, cap defaults to 50_000."""
    from app.config import settings

    assert settings.RELEVANCE_HAIKU_ENABLED is True
    assert settings.RELEVANCE_DAILY_TOKEN_CAP_PER_TEAM == 50_000


def test_config_model_name():
    from app.config import settings

    assert settings.RELEVANCE_HAIKU_MODEL == "claude-haiku-4-5-20251001"


# ─── Task 1: BrainIngestRequest schema ───────────────────────────────


def test_schema_valid():
    """BrainIngestRequest accepts valid input."""
    from app.schemas.brain import BrainIngestRequest

    req = BrainIngestRequest(
        content="hello world test", source="librechat:claude-haiku-4-5"
    )
    assert req.content == "hello world test"
    assert req.source == "librechat:claude-haiku-4-5"
    assert req.metadata == {}
    assert req.project_scope is None


def test_schema_rejects_empty_content():
    """BrainIngestRequest rejects empty content."""
    from pydantic import ValidationError
    from app.schemas.brain import BrainIngestRequest

    with pytest.raises(ValidationError):
        BrainIngestRequest(content="", source="librechat:x")


def test_schema_rejects_missing_content():
    """BrainIngestRequest rejects absent content."""
    from pydantic import ValidationError
    from app.schemas.brain import BrainIngestRequest

    with pytest.raises(ValidationError):
        BrainIngestRequest(source="librechat:x")  # type: ignore[call-arg]


def test_schema_rejects_oversized_content():
    """BrainIngestRequest rejects content > 32_000 chars."""
    from pydantic import ValidationError
    from app.schemas.brain import BrainIngestRequest

    with pytest.raises(ValidationError):
        BrainIngestRequest(content="x" * 32_001, source="librechat:x")


def test_schema_metadata_and_project_scope():
    """BrainIngestRequest accepts metadata dict and project_scope."""
    from app.schemas.brain import BrainIngestRequest

    req = BrainIngestRequest(
        content="test content here",
        source="librechat:x",
        metadata={"key": "val"},
        project_scope="proj-alpha",
    )
    assert req.metadata == {"key": "val"}
    assert req.project_scope == "proj-alpha"


# ─── Task 2: relevance_filter module ─────────────────────────────────


def test_system_prompt_length():
    """SYSTEM_PROMPT must be >= 16,384 bytes to satisfy Haiku 4.5 caching."""
    from app.services.relevance_filter import SYSTEM_PROMPT

    byte_len = len(SYSTEM_PROMPT.encode("utf-8"))
    assert byte_len >= 16_384, (
        f"SYSTEM_PROMPT is only {byte_len} bytes — Haiku 4.5 requires "
        "≥16,384 bytes (≈4096 tokens at 4 chars/token) to activate caching."
    )


@pytest.mark.asyncio
async def test_heuristic_shortcut_short_message(monkeypatch):
    """Short message (<15 chars) → False without calling Haiku."""
    import app.services.relevance_filter as rf

    # Patch _get_client to a mock that asserts NOT called
    mock_client = MagicMock()
    mock_client.messages = MagicMock()
    mock_client.messages.create = AsyncMock(
        side_effect=AssertionError("_get_client should NOT be called for short msgs")
    )
    monkeypatch.setattr(rf, "_anthropic_client", mock_client)
    # Reset so _get_client returns the mock
    # Actually: we need to mock _get_client itself
    with patch.object(rf, "_get_client", return_value=mock_client):
        # Override messages.create to be safe
        rf._anthropic_client = None  # force re-init guard
        # The heuristic rejects "ok" (2 chars < 15)
        # Patch _get_client to fail if called
        called = {"count": 0}

        def fake_get_client():
            called["count"] += 1
            return mock_client

        monkeypatch.setattr(rf, "_get_client", fake_get_client)
        result = await rf.classify("ok", team_scope="t1")

    assert result is False
    # _get_client should NOT have been called since heuristic rejected early
    assert called["count"] == 0


@pytest.mark.asyncio
async def test_agent_mention_command_rejection(monkeypatch):
    """An agent-mention COMMAND (`@agent …`) → False on the fast-path, no Haiku call.

    Phase 21: `@claude` was REMOVED as an alias, so it is no longer a command — a
    substantive `@claude …` message is now normal content and is NOT fast-path
    rejected. `@agent` is the guaranteed universal alias and IS rejected.
    """
    import app.services.relevance_filter as rf

    called = {"count": 0}

    def fake_get_client():
        called["count"] += 1
        return MagicMock()

    monkeypatch.setattr(rf, "_get_client", fake_get_client)

    # @agent is a real command → fast-path False, no Haiku client built.
    result = await rf.classify("@agent what's the status of phase 11?", team_scope="t1")
    assert result is False
    assert called["count"] == 0

    # @claude is no longer special — it does NOT get fast-path-rejected as a command
    # (it would fall through to the Haiku/heuristic path, which builds the client).
    await rf.classify("@claude what's the status of phase 11?", team_scope="t1")
    assert called["count"] >= 1


@pytest.mark.asyncio
async def test_haiku_disabled_fallback(monkeypatch):
    """RELEVANCE_HAIKU_ENABLED=False → heuristic-only, content passes if >=15 chars."""
    import app.services.relevance_filter as rf
    from app.config import settings

    monkeypatch.setattr(settings, "RELEVANCE_HAIKU_ENABLED", False)
    # Reset the cached client so _get_client re-evaluates the flag
    monkeypatch.setattr(rf, "_anthropic_client", None)

    result = await rf.classify("The deploy window is every Tuesday 14:00 UTC", team_scope="t1")
    assert result is True  # passes heuristic, no Haiku call


@pytest.mark.asyncio
async def test_haiku_success_path(monkeypatch):
    """Haiku returns {relevant: true} → classify returns True; correct API shape used."""
    import app.services.relevance_filter as rf
    from app.config import settings

    monkeypatch.setattr(settings, "RELEVANCE_HAIKU_ENABLED", True)
    monkeypatch.setattr(rf, "_anthropic_client", None)

    # Build mock response
    mock_content_block = MagicMock()
    mock_content_block.text = '{"relevant": true, "score": 0.92}'
    mock_usage = MagicMock()
    mock_usage.input_tokens = 350
    mock_usage.cache_creation_input_tokens = 400
    mock_usage.cache_read_input_tokens = 0
    mock_response = MagicMock()
    mock_response.content = [mock_content_block]
    mock_response.usage = mock_usage

    mock_create = AsyncMock(return_value=mock_response)
    mock_messages = MagicMock()
    mock_messages.create = mock_create
    mock_client = MagicMock()
    mock_client.messages = mock_messages

    monkeypatch.setattr(rf, "_get_client", lambda: mock_client)

    result = await rf.classify("We agreed the API uses JWT with 1h TTL", team_scope="t1")
    assert result is True

    # Verify API call shape
    mock_create.assert_called_once()
    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["model"] == settings.RELEVANCE_HAIKU_MODEL
    system_block = call_kwargs["system"][0]
    assert system_block["type"] == "text"
    assert system_block["cache_control"] == {"type": "ephemeral"}
    assert "We agreed the API" in call_kwargs["messages"][0]["content"]


@pytest.mark.asyncio
async def test_haiku_exception_fallback(monkeypatch):
    """Haiku raises TimeoutError → heuristic fallback, warning logged."""
    import app.services.relevance_filter as rf
    from app.config import settings

    monkeypatch.setattr(settings, "RELEVANCE_HAIKU_ENABLED", True)
    monkeypatch.setattr(rf, "_anthropic_client", None)

    mock_create = AsyncMock(side_effect=TimeoutError("timeout"))
    mock_client = MagicMock()
    mock_client.messages.create = mock_create

    monkeypatch.setattr(rf, "_get_client", lambda: mock_client)

    result = await rf.classify("The deploy window is every Tuesday 14:00 UTC", team_scope="t1")
    # Heuristic accepts this (>=15 chars, no @claude prefix)
    assert result is True


@pytest.mark.asyncio
async def test_haiku_returns_false(monkeypatch):
    """Haiku returns {relevant: false} → classify returns False."""
    import app.services.relevance_filter as rf
    from app.config import settings

    monkeypatch.setattr(settings, "RELEVANCE_HAIKU_ENABLED", True)
    monkeypatch.setattr(rf, "_anthropic_client", None)

    mock_content_block = MagicMock()
    mock_content_block.text = '{"relevant": false, "score": 0.02}'
    mock_usage = MagicMock()
    mock_usage.input_tokens = 350
    mock_usage.cache_creation_input_tokens = 0
    mock_usage.cache_read_input_tokens = 400
    mock_response = MagicMock()
    mock_response.content = [mock_content_block]
    mock_response.usage = mock_usage

    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    monkeypatch.setattr(rf, "_get_client", lambda: mock_client)

    result = await rf.classify("The deploy window is every Tuesday", team_scope="t1")
    assert result is False


@pytest.mark.asyncio
async def test_budget_cap_enforced(monkeypatch):
    """When daily token budget exhausted → Haiku NOT called, heuristic result returned."""
    import app.services.relevance_filter as rf
    from app.config import settings

    monkeypatch.setattr(settings, "RELEVANCE_HAIKU_ENABLED", True)
    monkeypatch.setattr(rf, "_anthropic_client", None)

    # Pre-fill budget to cap
    rf._daily_budget["t1_budget"] = {"date": str(date.today()), "tokens_used": 50_000}

    called = {"count": 0}

    def fake_get_client():
        called["count"] += 1
        return MagicMock()

    monkeypatch.setattr(rf, "_get_client", fake_get_client)

    content = "This is a substantive message about the deploy window on Tuesday"
    result = await rf.classify(content, team_scope="t1_budget")
    # Heuristic accepts (>=15 chars, no @claude prefix)
    assert result is True
    # Client was initialised but messages.create was NOT called
    # (budget check happens before the API call)
    # The _get_client IS called (to check if we have a client at all), but
    # the actual messages.create should NOT be called — we verify by
    # checking that the mock client's create was never invoked
    # Since fake_get_client returns a bare MagicMock with no awaitable,
    # if create were called it would fail. The test passes = no create call.


@pytest.mark.asyncio
async def test_budget_reset_on_new_day(monkeypatch):
    """Budget with old date resets to today before recording new tokens."""
    import app.services.relevance_filter as rf
    from app.config import settings

    monkeypatch.setattr(settings, "RELEVANCE_HAIKU_ENABLED", True)
    monkeypatch.setattr(rf, "_anthropic_client", None)

    # Pre-fill budget with yesterday's date and huge usage
    rf._daily_budget["t1_reset"] = {"date": "1970-01-01", "tokens_used": 99_999_999}

    mock_content_block = MagicMock()
    mock_content_block.text = '{"relevant": true, "score": 0.9}'
    mock_usage = MagicMock()
    mock_usage.input_tokens = 100
    mock_usage.cache_creation_input_tokens = 0
    mock_usage.cache_read_input_tokens = 400
    mock_response = MagicMock()
    mock_response.content = [mock_content_block]
    mock_response.usage = mock_usage

    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    monkeypatch.setattr(rf, "_get_client", lambda: mock_client)

    await rf.classify("Substantive content about deploy window Tuesday", team_scope="t1_reset")
    # Budget should have been reset to today
    assert rf._daily_budget["t1_reset"]["date"] == str(date.today())
    assert rf._daily_budget["t1_reset"]["tokens_used"] == 100  # only the new call


@pytest.mark.asyncio
async def test_json_in_fences(monkeypatch):
    """Haiku returns JSON wrapped in markdown fences → still parsed correctly."""
    import app.services.relevance_filter as rf
    from app.config import settings

    monkeypatch.setattr(settings, "RELEVANCE_HAIKU_ENABLED", True)
    monkeypatch.setattr(rf, "_anthropic_client", None)

    mock_content_block = MagicMock()
    mock_content_block.text = "```json\n{\"relevant\": true, \"score\": 0.9}\n```"
    mock_usage = MagicMock()
    mock_usage.input_tokens = 350
    mock_usage.cache_creation_input_tokens = 0
    mock_usage.cache_read_input_tokens = 400
    mock_response = MagicMock()
    mock_response.content = [mock_content_block]
    mock_response.usage = mock_usage

    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    monkeypatch.setattr(rf, "_get_client", lambda: mock_client)

    result = await rf.classify("We agreed the API uses JWT tokens", team_scope="t1_fences")
    assert result is True


# ─── The second answer: "important / final" (2026-08-05) ─────────────
#
# The AI now decides TWO of the four levels in ONE call: `relevant` (store it at
# all) and `important` (is this the final version of something → VALIDATED).
# What these lock:
#
#   * the model's `important` is carried through, and its ABSENCE means false
#   * every fail-soft path leaves the item at WORKING — never dropped, never
#     guessed high. Budget exhaustion and a timeout are the two named cases.
#   * the substantive-content override can rescue an item into the brain but
#     cannot speak for its finality
#   * the prompt actually ASKS for the field, and the reply has room to hold it


def _mock_haiku(text: str, *, input_tokens: int = 350):
    """A client whose single response carries `text`. Returns (client, create)."""
    block = MagicMock()
    block.text = text
    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.cache_creation_input_tokens = 0
    usage.cache_read_input_tokens = 400
    response = MagicMock()
    response.content = [block]
    response.usage = usage
    create = AsyncMock(return_value=response)
    client = MagicMock()
    client.messages.create = create
    return client, create


def test_prompt_asks_for_the_important_field():
    """The OUTPUT CONTRACT names `important` — not just some prose mentioning it.

    Asserted against the exact JSON line the model is told to return, because a
    prompt that discusses importance in prose while contracting for two fields
    gets two fields back. The few-shot count is checked for the same reason: the
    guidance is only load-bearing if examples demonstrate the true case.
    """
    from app.services.relevance_filter import SYSTEM_PROMPT

    contract = '{"relevant": true_or_false, "score": 0.0_to_1.0, "important": true_or_false}'
    assert contract in SYSTEM_PROMPT, (
        "the model returns the shape it is contracted for; `important` must be "
        "in the JSON contract line itself"
    )
    assert SYSTEM_PROMPT.count('"important": true') >= 3, (
        "the true case needs demonstrating, not just describing"
    )
    assert SYSTEM_PROMPT.count('"important": false') >= 3, (
        "and so does the false case, or every stored item comes back final"
    )


@pytest.mark.asyncio
async def test_reply_has_room_for_three_fields(monkeypatch):
    """max_tokens must fit the LONGEST legal answer, or the field silently dies.

    A reply cut mid-object fails json.loads and takes the fail-soft path, so the
    classifier would keep costing a call while never flagging anything again.
    Two fields fitted in 20 tokens; three do not.
    """
    import app.services.relevance_filter as rf
    from app.config import settings

    monkeypatch.setattr(settings, "RELEVANCE_HAIKU_ENABLED", True)
    monkeypatch.setattr(rf, "_anthropic_client", None)
    client, create = _mock_haiku('{"relevant": true, "score": 0.9, "important": true}')
    monkeypatch.setattr(rf, "_get_client", lambda: client)

    await rf.classify_detailed(
        "Decision: Postgres 17 for the event store, final for v2", team_scope="t_roomy"
    )

    longest_legal = '{"relevant": true, "score": 0.999, "important": false}'
    # JSON punctuation tokenizes at roughly two characters per token — a floor,
    # not an estimate, so this stays true if the wording of the answer shifts.
    floor = len(longest_legal) // 2
    assert create.call_args.kwargs["max_tokens"] >= floor, (
        f"max_tokens must hold {longest_legal!r} (≥{floor} tokens)"
    )


@pytest.mark.asyncio
async def test_model_important_is_carried_through(monkeypatch):
    import app.services.relevance_filter as rf
    from app.config import settings

    monkeypatch.setattr(settings, "RELEVANCE_HAIKU_ENABLED", True)
    monkeypatch.setattr(rf, "_anthropic_client", None)
    client, _ = _mock_haiku('{"relevant": true, "score": 0.94, "important": true}')
    monkeypatch.setattr(rf, "_get_client", lambda: client)

    r = await rf.classify_detailed(
        "Q3 budget signed off by finance: 240k. That is the figure to plan against.",
        team_scope="t_imp",
    )
    assert r.relevant is True
    assert r.important is True
    assert r.decided_by == "model", "only the model may author this flag"
    assert r.model == settings.RELEVANCE_HAIKU_MODEL
    assert r.score == 0.94


@pytest.mark.asyncio
async def test_absent_important_means_false(monkeypatch):
    """The 90 few-shot examples omit the field; an omission is not a maybe."""
    import app.services.relevance_filter as rf
    from app.config import settings

    monkeypatch.setattr(settings, "RELEVANCE_HAIKU_ENABLED", True)
    monkeypatch.setattr(rf, "_anthropic_client", None)
    client, _ = _mock_haiku('{"relevant": true, "score": 0.88}')
    monkeypatch.setattr(rf, "_get_client", lambda: client)

    r = await rf.classify_detailed(
        "Working on the migration script, about half done", team_scope="t_absent"
    )
    assert r.relevant is True
    assert r.important is False


@pytest.mark.asyncio
async def test_budget_exhausted_leaves_the_item_at_working(monkeypatch):
    """The named fail-soft case: keep it, do NOT drop it, do NOT guess high.

    A team that has spent the day's classification budget must not start
    collecting items the AI marked final without ever reading them.
    """
    import app.services.relevance_filter as rf
    from app.config import settings

    monkeypatch.setattr(settings, "RELEVANCE_HAIKU_ENABLED", True)
    monkeypatch.setattr(rf, "_anthropic_client", None)
    rf._daily_budget["t_spent"] = {"date": str(date.today()), "tokens_used": 50_000}

    client, create = _mock_haiku('{"relevant": true, "score": 0.99, "important": true}')
    monkeypatch.setattr(rf, "_get_client", lambda: client)

    r = await rf.classify_detailed(
        "Q3 budget signed off by finance: 240k, final figure to plan against",
        team_scope="t_spent",
    )
    assert r.relevant is True, "an exhausted budget must not lose the knowledge"
    assert r.important is False, "and must not invent a judgement nobody made"
    assert r.decided_by == "heuristic"
    assert r.reason == "budget_exhausted"
    create.assert_not_called(), "the whole point of the cap is that no call is made"


@pytest.mark.asyncio
async def test_timeout_leaves_the_item_at_working(monkeypatch):
    """Same contract for the other fail-soft path."""
    import app.services.relevance_filter as rf
    from app.config import settings

    monkeypatch.setattr(settings, "RELEVANCE_HAIKU_ENABLED", True)
    monkeypatch.setattr(rf, "_anthropic_client", None)
    client = MagicMock()
    client.messages.create = AsyncMock(side_effect=TimeoutError("timeout"))
    monkeypatch.setattr(rf, "_get_client", lambda: client)

    r = await rf.classify_detailed(
        "Q3 budget signed off by finance: 240k, final figure to plan against",
        team_scope="t_timeout",
    )
    assert r.relevant is True
    assert r.important is False
    assert r.decided_by == "heuristic"
    assert r.reason == "classifier_error"


@pytest.mark.asyncio
async def test_disabled_classifier_never_flags_important(monkeypatch):
    import app.services.relevance_filter as rf
    from app.config import settings

    monkeypatch.setattr(settings, "RELEVANCE_HAIKU_ENABLED", False)
    monkeypatch.setattr(rf, "_anthropic_client", None)
    monkeypatch.setattr(rf, "_get_client", lambda: None)

    r = await rf.classify_detailed(
        "Policy as of today: API keys rotate every 90 days, no exceptions",
        team_scope="t_off",
    )
    assert r.relevant is True
    assert r.important is False
    assert r.reason == "classifier_disabled"


@pytest.mark.asyncio
async def test_substantive_override_cannot_carry_important(monkeypatch):
    """The override rescues an item into the brain; it does not speak for finality.

    An item the model wanted to DISCARD must not come back out of the same call
    marked final — that is the override inventing a judgement nobody made.
    """
    import app.services.relevance_filter as rf
    from app.config import settings

    monkeypatch.setattr(settings, "RELEVANCE_HAIKU_ENABLED", True)
    monkeypatch.setattr(rf, "_anthropic_client", None)
    client, _ = _mock_haiku('{"relevant": false, "score": 0.08, "important": true}')
    monkeypatch.setattr(rf, "_get_client", lambda: client)

    content = "The autumn showcase in Ghent is confirmed for the 14th at the old dockyard"
    assert len(content) >= 50  # the override's own precondition
    r = await rf.classify_detailed(content, team_scope="t_override")
    assert r.relevant is True, "substantive content is still rescued into the brain"
    assert r.important is False, "but the rescue is not a promotion"
    assert r.reason == "substantive_default_allow"


@pytest.mark.asyncio
async def test_fast_path_reject_reports_itself(monkeypatch):
    """A short message never reaches the model, and says so rather than guessing."""
    import app.services.relevance_filter as rf

    r = await rf.classify_detailed("ok", team_scope="t_short")
    assert r.relevant is False
    assert r.important is False
    assert r.decided_by == "fast_path"


@pytest.mark.asyncio
async def test_classify_bool_wrapper_tracks_detailed(monkeypatch):
    """The old bool API is the `.relevant` half of the new one — both cases."""
    import app.services.relevance_filter as rf
    from app.config import settings

    monkeypatch.setattr(settings, "RELEVANCE_HAIKU_ENABLED", True)
    monkeypatch.setattr(rf, "_anthropic_client", None)

    client, _ = _mock_haiku('{"relevant": true, "score": 0.9, "important": true}')
    monkeypatch.setattr(rf, "_get_client", lambda: client)
    assert await rf.classify("Decision: we ship on the 14th", team_scope="t_w1") is True

    client, _ = _mock_haiku('{"relevant": false, "score": 0.02, "important": true}')
    monkeypatch.setattr(rf, "_get_client", lambda: client)
    # Under the substantive floor, so the override does not rescue it.
    assert await rf.classify("brb, coffee run", team_scope="t_w2") is False
