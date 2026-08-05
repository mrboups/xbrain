"""Two production defects, from one transcript.

    23:15:19  user   https://pitch.example/
    23:18:53  user   @agent When is it and where is it?
    23:19:00  agent  "I was able to fetch the URL from your message. ... I don't
                      have the fetched content available in my context right now."

DEFECT ONE — the pre-fetch read the summoning message and nothing else, so the
link three minutes earlier was invisible. Zero `web_prefetch` log lines in three
hours confirmed it. Pasting a link and THEN asking about it is the normal way
people use a chat; nobody composes "@agent what is https://… about" in one
breath.

DEFECT TWO — "I was able to fetch the URL" is false, and it is stated before the
model contradicts itself. Someone reading only the first line believes the page
was read. A prompt that silently omits the section invites the model to imagine
one, so the absence is now stated rather than left out.

And a third, from the same thread's neighbours: two agent rows persisted as the
literal string `(empty response)`. Both were `user_promax`, both reached
`team_chat_agent.done`, and one of two people in the SAME team was getting real
answers at the time — the socket was alive and claude.ai gave that browser
nothing usable. A parenthetical apology written into the thread as the agent's
own words is indistinguishable from the agent choosing to say nothing.
"""
from __future__ import annotations

import inspect
import json
import types
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
import respx

from app.services import team_chat_agent
from app.services.team_chat_agent import (
    AGENT_FAILURE_EMPTY_PROVIDER,
    AGENT_FAILURE_EMPTY_SUBSCRIPTION,
    FAILURE_CODE_EMPTY_ANSWER,
    NO_WEB_CONTENT_MARKER,
    EmptyAnswer,
    classify_stream_failure,
)

NOW = datetime(2026, 8, 5, 23, 18, 53, tzinfo=UTC)


def _message(
    *,
    content: str,
    kind: str = "user",
    minutes_ago: float = 0.0,
    message_id=None,
):
    """A stand-in with exactly the attributes the URL window reads."""
    return types.SimpleNamespace(
        id=message_id or uuid4(),
        kind=kind,
        content=content,
        created_at=NOW - timedelta(minutes=minutes_ago),
    )


# ── 1. The link in a PRECEDING message is the one being asked about ──────────


class TestTheWindowIsTheConversationNotOneMessage:
    def test_a_link_pasted_before_the_mention_is_found(self):
        """The exact transcript that produced nothing."""
        mention = _message(content="@agent When is it and where is it?")
        link = _message(content="https://pitch.example/", minutes_ago=3.6)
        urls = team_chat_agent._recent_urls(
            triggering_message=mention, recent=[link, mention]
        )
        assert urls == ["https://pitch.example/"], (
            "the link three minutes earlier is what the question is about"
        )

    def test_the_summoning_message_still_wins_when_it_has_one(self):
        mention = _message(content="@agent read https://newest.example/ please")
        older = _message(content="https://older.example/", minutes_ago=5)
        urls = team_chat_agent._recent_urls(
            triggering_message=mention, recent=[older, mention]
        )
        assert urls[0] == "https://newest.example/", "newest first"

    def test_no_links_anywhere_is_an_empty_list_not_a_guess(self):
        mention = _message(content="@agent what did we decide yesterday?")
        chat = [_message(content="nothing here", minutes_ago=n) for n in (1, 2, 3)]
        assert team_chat_agent._recent_urls(
            triggering_message=mention, recent=[*chat, mention]
        ) == []


class TestTheWidenedWindowIsStillBounded:
    def test_the_cap_of_three_holds_across_messages(self):
        """Widening the source must not turn one mention into a dozen fetches."""
        mention = _message(content="@agent summarise these")
        links = [
            _message(content=f"https://example.com/{n}", minutes_ago=n)
            for n in range(1, 6)
        ]
        urls = team_chat_agent._recent_urls(
            triggering_message=mention, recent=[*reversed(links), mention]
        )
        assert len(urls) == 3

    def test_the_newest_three_are_the_ones_kept(self):
        """When a thread carries more links than the cap, the newest are the ones
        being asked about."""
        mention = _message(content="@agent and this?")
        links = [
            _message(content=f"https://example.com/{n}", minutes_ago=n)
            for n in range(1, 6)
        ]
        urls = team_chat_agent._recent_urls(
            triggering_message=mention, recent=[*reversed(links), mention]
        )
        assert urls == [
            "https://example.com/1", "https://example.com/2", "https://example.com/3",
        ]

    def test_the_same_link_repeated_is_fetched_once(self):
        mention = _message(content="@agent thoughts on https://example.com/a ?")
        echoes = [
            _message(content="https://example.com/a", minutes_ago=n) for n in (1, 2, 3)
        ]
        urls = team_chat_agent._recent_urls(
            triggering_message=mention, recent=[*echoes, mention]
        )
        assert urls == ["https://example.com/a"]

    def test_a_link_from_last_week_is_not_refetched(self):
        """Still being in the history buffer is not the same as being asked about."""
        mention = _message(content="@agent what is our headcount?")
        stale = _message(content="https://stale.example/", minutes_ago=60 * 24 * 7)
        assert team_chat_agent._recent_urls(
            triggering_message=mention, recent=[stale, mention]
        ) == []

    def test_the_message_count_bound_holds_independently_of_time(self):
        mention = _message(content="@agent ?")
        chatter = [_message(content="ok", minutes_ago=0.1 * n) for n in range(1, 20)]
        buried = _message(content="https://buried.example/", minutes_ago=0.05 * 40)
        urls = team_chat_agent._recent_urls(
            triggering_message=mention, recent=[buried, *reversed(chatter), mention]
        )
        assert urls == [], "a link 20 messages back is not what the mention is about"

    def test_the_agents_own_links_are_never_fetched(self):
        """One hallucinated URL would otherwise become a fetch, then context,
        then the next answer."""
        mention = _message(content="@agent is that right?")
        agent_row = _message(
            content="See https://invented.example/", kind="agent", minutes_ago=1
        )
        assert team_chat_agent._recent_urls(
            triggering_message=mention, recent=[agent_row, mention]
        ) == []

    def test_a_missing_timestamp_does_not_drop_the_link(self):
        """Failing in the direction of not answering is the wrong direction."""
        mention = _message(content="@agent ?")
        mention.created_at = None
        link = _message(content="https://example.com/x", minutes_ago=2)
        link.created_at = None
        assert team_chat_agent._recent_urls(
            triggering_message=mention, recent=[link, mention]
        ) == ["https://example.com/x"]


# ── 2. The prompt cannot produce a claim of having fetched ──────────────────


class TestTheAbsenceIsStatedNotOmitted:
    @pytest.mark.asyncio
    async def test_a_block_is_present_even_with_nothing_to_fetch(self):
        block = await team_chat_agent._build_fetched_web_block([], "team-a")
        assert "## Fetched web content" in block
        assert NO_WEB_CONTENT_MARKER in block
        assert "NOT" in NO_WEB_CONTENT_MARKER, (
            "a marker the model can read past is the same as no marker"
        )

    @pytest.mark.asyncio
    async def test_a_failed_fetch_is_marked_and_does_not_stop_the_turn(
        self, monkeypatch
    ):
        async def _fetch_fails(url, team_scope):
            return None

        monkeypatch.setattr(team_chat_agent, "_fetch_url_via_scraper", _fetch_fails)
        block = await team_chat_agent._build_fetched_web_block(
            ["https://example.com/a"], "team-a"
        )
        assert "could not fetch this URL" in block
        assert "https://example.com/a" in block

    @pytest.mark.asyncio
    async def test_fetched_text_reaches_the_block_under_its_own_url(
        self, monkeypatch
    ):
        async def _fetch(url, team_scope):
            return f"CONTENT OF {url}"

        monkeypatch.setattr(team_chat_agent, "_fetch_url_via_scraper", _fetch)
        block = await team_chat_agent._build_fetched_web_block(
            ["https://a.example/", "https://b.example/"], "team-a"
        )
        assert "CONTENT OF https://a.example/" in block
        assert "CONTENT OF https://b.example/" in block

    def test_the_system_prompt_forbids_claiming_a_fetch(self):
        prompt = team_chat_agent._build_system_prompt(team_slug="team-a").lower()
        assert "cannot browse" in prompt
        assert "fetched web content" in prompt, "name the section it must look at"
        for verb in ["fetched", "opened", "visited", "read a link"]:
            assert verb in prompt, (
                f"the ban must name {verb!r} — the reply that prompted this said "
                "'I was able to fetch the URL from your message'"
            )
        assert "not even as a preamble" in prompt, (
            "the false claim was the FIRST line of a reply that then admitted it "
            "had nothing; banning the conclusion is not enough"
        )

    def test_the_turn_always_carries_the_section(self):
        """Appended unconditionally — an omitted section is what invited the
        model to imagine one."""
        source = inspect.getsource(team_chat_agent._do_handle)
        assert "chat_history_block + await _build_fetched_web_block(" in source
        assert "if web_block:" not in source

    def test_the_preamble_is_stable_across_turns(self):
        """It sits in front of the cache_control'd KB block, and Anthropic matches
        cached PREFIXES — a per-turn preamble would invalidate the KB cache on
        every message. The per-turn fact lives in the uncached user turn."""
        first = team_chat_agent._build_system_prompt(team_slug="team-a")
        second = team_chat_agent._build_system_prompt(team_slug="team-a")
        assert first == second
        assert "fetched web content" not in first.split("ONLY web page content")[0].lower()

    def test_the_knowledge_base_does_not_teach_a_stale_control(self):
        kb = team_chat_agent._PRODUCT_KB
        assert kb, "the KB failed to load; this test would pass vacuously"
        assert "📎" not in kb, "the composer's attach control is `+`"
        assert "`+` button" in kb
        # Extension-only advice must be labelled, or it gets given to a phone.
        assert kb.count("Extension only") >= 2


# ── 3. An empty stream is a failure, not a message ──────────────────────────


class TestAnEmptyStreamIsNotAnAnswer:
    def test_the_placeholder_is_gone_from_the_persist_path(self):
        source = inspect.getsource(team_chat_agent._do_handle)
        assert "(empty response)" not in source, (
            "a parenthetical apology written into the thread as the agent's own "
            "words, indistinguishable from the agent choosing to say nothing"
        )
        assert "raise EmptyAnswer(via_subscription=has_promax)" in source

    def test_it_has_its_own_code_and_is_retryable(self):
        failure = classify_stream_failure(EmptyAnswer(via_subscription=True))
        assert failure["code"] == FAILURE_CODE_EMPTY_ANSWER
        assert failure["retryable"] is True
        assert set(failure) == {"code", "message", "retryable"}

    def test_it_is_a_failure_and_not_an_unavailability(self):
        """The request went out and the transport reported success. Something WAS
        attempted, so this is not the 'nothing to try' family."""
        assert FAILURE_CODE_EMPTY_ANSWER not in team_chat_agent.UNAVAILABILITY_CODES

    def test_the_subscription_sentence_names_a_check_a_person_can_do(self):
        """Observed per-USER, not per-surface: two people in one team, both
        user_promax, and the FRESHER bridge was the failing one. The socket was
        alive; claude.ai gave that browser nothing usable."""
        message = classify_stream_failure(
            EmptyAnswer(via_subscription=True)
        )["message"]
        assert message == AGENT_FAILURE_EMPTY_SUBSCRIPTION
        lowered = message.lower()
        assert "claude.ai" in lowered and "signed in" in lowered, (
            "the person can fix this in under a minute; say where to look"
        )
        # It must not assert a cause the server never verified.
        for guess in ["because", "expired", "logged out", "your session has"]:
            assert guess not in lowered, (
                f"{guess!r} claims something the server only inferred from an "
                "empty body"
            )

    def test_the_direct_provider_sentence_does_not_send_anyone_to_a_browser(self):
        message = classify_stream_failure(
            EmptyAnswer(via_subscription=False)
        )["message"]
        assert message == AGENT_FAILURE_EMPTY_PROVIDER
        assert "claude.ai" not in message.lower(), (
            "a direct API call returning nothing has nothing to do with a browser "
            "session"
        )

    def test_an_empty_subscription_stream_does_not_fall_through_to_a_key(self):
        """Deliberate, and the reasoning is written down beside it: a stale
        claude.ai session is free to fix, so billing an API key for it spends
        money on a login problem."""
        source = inspect.getsource(team_chat_agent._do_handle)
        # The EmptyAnswer is raised, not caught-and-retried: exactly one provider
        # call remains in the routing block.
        assert source.count("_stream_via_fallback_provider") == 1
        after_raise = source.split("raise EmptyAnswer(")[1]
        assert "_stream_via_fallback_provider" not in after_raise
        assert "resolve_fallback_key" not in after_raise

    def test_neither_sentence_leaks_or_invents(self):
        for via in (True, False):
            message = classify_stream_failure(EmptyAnswer(via_subscription=via))[
                "message"
            ]
            assert message[0].isupper() and message.endswith(".")
            for fragment in ["{", "}", "401", "403", "200", "traceback", "sse"]:
                assert fragment not in message.lower()

    def test_the_exception_carries_no_renderable_text(self):
        assert str(EmptyAnswer(via_subscription=True)) == ""

    def test_the_summary_path_makes_the_same_call(self):
        source = inspect.getsource(team_chat_agent.catch_me_up)
        assert "raise EmptyAnswer(via_subscription=has_promax)" in source, (
            "an empty summary otherwise resolves to a blank panel and the person "
            "is left guessing whether there was nothing to say"
        )


class TestTheBridgeStreamIsObservable:
    """"The socket is alive" and "the socket returned an answer" are different
    claims, and only the first was ever checked."""

    def test_an_empty_bridge_stream_is_logged_with_what_arrived(self):
        source = inspect.getsource(team_chat_agent._stream_via_promax)
        assert "bridge_stream_empty" in source
        for counter in ["lines=", "data_lines=", "parsed="]:
            assert counter in source, (
                "the CRLF-delimiter failure shows up as lines high and parsed "
                "zero — that is a diagnosis rather than a mystery"
            )

    @pytest.mark.asyncio
    async def test_a_200_that_carries_no_content_yields_nothing(self):
        """The exact shape session-bridge relayed: routed, 200 OK, no blocks."""
        body = (
            b'data: {"choices":[{"index":0,"delta":{"role":"assistant"}}]}\n\n'
            b"data: [DONE]\n\n"
        )
        with respx.mock(assert_all_called=True) as mock:
            mock.post("http://session-bridge:8105/v1/chat/completions").mock(
                return_value=httpx.Response(
                    200, headers={"content-type": "text/event-stream"}, content=body
                )
            )
            produced = [
                text
                async for text, _usage in team_chat_agent._stream_via_promax(
                    triggering_user_sub="someone",
                    system_prompt="sys",
                    cached_memory_block="mem",
                    chat_history_block="hi",
                )
                if text
            ]
        assert produced == [], "this is the state that used to become a message"

    @pytest.mark.asyncio
    async def test_a_normal_bridge_stream_still_produces_text(self):
        body = (
            b'data: {"choices":[{"index":0,"delta":{"content":"Hello"}}]}\n\n'
            b'data: {"choices":[{"index":0,"delta":{"content":" there"}}]}\n\n'
            b"data: [DONE]\n\n"
        )
        with respx.mock(assert_all_called=True) as mock:
            mock.post("http://session-bridge:8105/v1/chat/completions").mock(
                return_value=httpx.Response(
                    200, headers={"content-type": "text/event-stream"}, content=body
                )
            )
            produced = [
                text
                async for text, _usage in team_chat_agent._stream_via_promax(
                    triggering_user_sub="someone",
                    system_prompt="sys",
                    cached_memory_block="mem",
                    chat_history_block="hi",
                )
                if text
            ]
        assert "".join(produced) == "Hello there"


# ── 4. End to end: the row and the frame agree, against a real database ─────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_an_empty_subscription_turn_persists_as_a_failure_not_a_message(
    pg_url, monkeypatch
):
    """The whole defect, end to end, with only the TRANSPORT mocked.

    session-bridge answers 200 with an SSE stream carrying no content — exactly
    what production logged — and the assertions are that the live frame and the
    persisted row say the same thing, and that neither of them is a message the
    agent appears to have written.

    Seeded through COMMITTED rows: `handle_claude_mention` opens its own pooled
    connection, which cannot see a rollback fixture's savepoints.
    """
    import sqlalchemy as sa

    import app.db.session as db_session
    from app.services import centrifugo_client

    team_id = uuid4()
    user_id = uuid4()
    message_id = uuid4()
    sub = "empty-stream-user"

    published: list[dict] = []

    async def _recorder(channel, data):
        published.append(data)
        return True

    monkeypatch.setattr(centrifugo_client, "publish", _recorder)
    # PLUMBING, not a logic stub (mirrors test_catch_me_up_gate.py). The module
    # bound `async_session_factory` by value at import, before the testcontainer
    # fixture swapped it, so the persist step would otherwise open a connection to
    # the conftest default host and be refused.
    monkeypatch.setattr(
        "app.services.team_chat_agent.async_session_factory",
        db_session.async_session_factory,
    )

    async with db_session.async_session_factory() as seed:
        await seed.execute(sa.text(
            "INSERT INTO teams (id, slug, display_name, visibility) "
            "VALUES (:id, :slug, 'Empty Stream', 'closed')"
        ), {"id": str(team_id), "slug": f"empty-{team_id.hex[:8]}"})
        await seed.execute(sa.text(
            "INSERT INTO users (id, source_user_id, email) "
            "VALUES (:id, :sub, :email)"
        ), {"id": str(user_id), "sub": sub, "email": f"{team_id.hex[:8]}@test.local"})
        await seed.execute(sa.text(
            "INSERT INTO team_messages (id, team_id, author_user_id, kind, content) "
            "VALUES (:id, :team, :user, 'user', '@agent When is it and where is it?')"
        ), {"id": str(message_id), "team": str(team_id), "user": str(user_id)})
        await seed.commit()

    try:
        empty_stream = (
            b'data: {"choices":[{"index":0,"delta":{"role":"assistant"}}]}\n\n'
            b"data: [DONE]\n\n"
        )
        with respx.mock(assert_all_called=False) as mock:
            mock.get(url__regex=r"http://session-bridge:8105/v1/internal/bridge-status/.*").mock(
                return_value=httpx.Response(200, json={"live": True})
            )
            mock.post("http://session-bridge:8105/v1/chat/completions").mock(
                return_value=httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content=empty_stream,
                )
            )
            # _do_handle rather than handle_claude_mention: the public wrapper
            # swallows every exception into a log line, so a test that went
            # through it would pass silently if the turn blew up on the way.
            async with db_session.async_session_factory() as run:
                await team_chat_agent._do_handle(
                    session=run,
                    team_id=team_id,
                    triggering_message_id=message_id,
                    triggering_user_sub=sub,
                )

        # ── the LIVE view ────────────────────────────────────────────────────
        errors = [f for f in published if f.get("type") == "agent_stream_error"]
        assert len(errors) == 1, published
        assert errors[0]["code"] == FAILURE_CODE_EMPTY_ANSWER
        assert errors[0]["error"] == AGENT_FAILURE_EMPTY_SUBSCRIPTION
        assert errors[0]["retryable"] is True

        # ── the RELOADED view ────────────────────────────────────────────────
        async with db_session.async_session_factory() as check:
            row = (await check.execute(sa.text(
                "SELECT content, agent_name, routed_via, metadata "
                "FROM team_messages WHERE team_id = :t AND kind = 'agent'"
            ), {"t": str(team_id)})).mappings().fetchone()

        assert row is not None, "the turn persisted nothing at all"
        assert row["content"] != "(empty response)", (
            "the placeholder that started all of this"
        )
        assert row["content"] == AGENT_FAILURE_EMPTY_SUBSCRIPTION, (
            "a reload must not show as an ordinary answer what the live view "
            "showed as a failure"
        )
        metadata = row["metadata"]
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        assert metadata["agent_failure"]["code"] == FAILURE_CODE_EMPTY_ANSWER
        assert metadata["agent_failure"]["partial"] is False
        # Which path answered stays legible on the row itself.
        assert row["routed_via"] == "user_promax"
        assert row["agent_name"] == team_chat_agent.MODEL_SONNET
    finally:
        async with db_session.async_session_factory() as cleanup:
            await cleanup.execute(
                sa.text("DELETE FROM team_messages WHERE team_id = :t"),
                {"t": str(team_id)},
            )
            await cleanup.execute(
                sa.text("DELETE FROM teams WHERE id = :t"), {"t": str(team_id)}
            )
            await cleanup.execute(
                sa.text("DELETE FROM users WHERE id = :u"), {"u": str(user_id)}
            )
            await cleanup.commit()
