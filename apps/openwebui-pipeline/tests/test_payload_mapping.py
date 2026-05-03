"""Test that log_exchange constructs the right tagging payload."""

from unittest.mock import AsyncMock

import pytest

from app.pipelines.xbrain_logger import log_exchange


@pytest.mark.asyncio
async def test_log_exchange_sends_user_then_assistant():
    mem = AsyncMock()
    mem.post_message = AsyncMock(return_value={})
    await log_exchange(
        mem=mem,
        sub="alice-sub",
        team_scope="team-a",
        conversation_id="conv-1",
        model="claude-3-5-sonnet",
        user_content="hi",
        assistant_content="hello",
    )
    assert mem.post_message.call_count == 2
    roles = sorted(call.kwargs["role"] for call in mem.post_message.call_args_list)
    assert roles == ["assistant", "user"]


@pytest.mark.asyncio
async def test_log_exchange_uses_openwebui_source_prefix():
    mem = AsyncMock()
    mem.post_message = AsyncMock(return_value={})
    await log_exchange(
        mem=mem,
        sub="alice-sub",
        team_scope="team-a",
        conversation_id="conv-1",
        model="gpt-4o",
        user_content="hi",
        assistant_content="hello",
    )
    sources = {call.kwargs["source"] for call in mem.post_message.call_args_list}
    assert sources == {"openwebui:gpt-4o"}


@pytest.mark.asyncio
async def test_log_exchange_skips_empty_content():
    mem = AsyncMock()
    mem.post_message = AsyncMock(return_value={})
    await log_exchange(
        mem=mem,
        sub="alice-sub",
        team_scope="team-a",
        conversation_id="conv-1",
        model="gpt-4o",
        user_content="",  # empty
        assistant_content="hello",
    )
    # Only 1 POST: just the assistant message
    assert mem.post_message.call_count == 1


@pytest.mark.asyncio
async def test_log_exchange_swallows_exceptions():
    """Best-effort guarantee: if mem.post_message raises, log_exchange does NOT propagate."""
    mem = AsyncMock()
    mem.post_message = AsyncMock(side_effect=RuntimeError("memory-api is down"))
    # Should NOT raise
    await log_exchange(
        mem=mem,
        sub="alice-sub",
        team_scope="team-a",
        conversation_id="conv-1",
        model="gpt-4o",
        user_content="hi",
        assistant_content="hello",
    )
