"""Tests for app.services.mention_detector — config-driven alias regex.

Phase 14 (14-01) — the mention trigger is no longer a hardcoded brand token
(`grooveos|groove|gr|g`); it is built from `settings.AGENT_MENTION_ALIASES`
(comma-separated, no leading '@'). These tests drive both the module-level
default (neutral "agent" alias, set via conftest.py) and custom alias lists
built directly through the private `_build_mention_regex` helper — no brand
name is hardcoded into the assertions themselves.
"""
from __future__ import annotations

import pytest

from app.services.mention_detector import _build_mention_regex, detect


# === Default (module-level) behavior — driven by conftest's
#     AGENT_MENTION_ALIASES=agent default. ===


@pytest.mark.parametrize(
    "text,expected_trigger",
    [
        ("@agent what is up", "agent"),
        ("hey @agent please summarize", "agent"),
        ("Question for @AGENT about churn", "agent"),  # case-insensitive
        ("(@agent!)", "agent"),  # punctuation boundary
        ("multi line\n@agent hello\nbye", "agent"),
        ("@agent", "agent"),  # alone, end-of-string
    ],
)
def test_detect_default_alias_positive(text: str, expected_trigger: str):
    result = detect(text)
    assert result is not None, f"expected match in: {text!r}"
    assert result["agent_name"]  # canonical model id, unchanged by this refactor
    assert result["trigger"] == expected_trigger


@pytest.mark.parametrize(
    "text",
    [
        "",  # empty
        "no mention here",
        "user@agent.com sent the email",  # email — @ has word-char before ([^\\w@] boundary holds)
        "@nobody said yes",  # not a configured alias
        "@agents are nice",  # trailing word char
        "Look at@agent",  # @ has word-char before — looks like email
    ],
)
def test_detect_default_alias_negative(text: str):
    assert detect(text) is None, f"should NOT match: {text!r}"


def test_first_mention_wins():
    """Multiple mentions in one message → only the first fires (no spam)."""
    text = "@nobody then @agent and also @agent"
    result = detect(text)
    assert result is not None
    assert result["trigger"] == "agent"


# === Custom alias lists (backwards-compat / prod override) built directly
#     via the private regex builder — no module reload required. ===


def test_longest_alias_wins_not_truncated():
    """Longest-first ordering: a longer alias must not be truncated by a shorter prefix alias."""
    regex = _build_mention_regex("agent,grooveos,groove,gr,g")
    m = regex.search("@grooveos hi")
    assert m is not None
    assert m.group(1).lower() == "grooveos"


@pytest.mark.parametrize(
    "text,expected_trigger",
    [
        ("@groove hi", "groove"),
        ("@grooveos hi", "grooveos"),
        ("@agent hi", "agent"),
        ("@gr hi", "gr"),
        ("@g hi", "g"),
    ],
)
def test_backwards_compat_alias_list(text: str, expected_trigger: str):
    """Prod .env override (AGENT_MENTION_ALIASES=agent,grooveos,groove,gr,g)
    must preserve every legacy trigger AND the new neutral 'agent' alias."""
    regex = _build_mention_regex("agent,grooveos,groove,gr,g")
    m = regex.search(text)
    assert m is not None, f"expected match in: {text!r}"
    assert m.group(1).lower() == expected_trigger


def test_case_insensitive_flag_preserved():
    import re

    regex = _build_mention_regex("agent")
    assert regex.flags & re.IGNORECASE


def test_non_alias_mention_does_not_match():
    regex = _build_mention_regex("agent,grooveos,groove,gr,g")
    assert regex.search("@nobody") is None
