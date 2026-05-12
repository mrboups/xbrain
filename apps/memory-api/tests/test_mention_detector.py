"""Tests for app.services.mention_detector — pure regex, no fixtures needed.

Quick task 260512-tcr Wave 2.6.
"""
from __future__ import annotations

import pytest

from app.services.mention_detector import detect


# Positive cases — should match.

@pytest.mark.parametrize(
    "text,expected_trigger",
    [
        ("@claude what is up", "claude"),
        ("hey @claude please summarize", "claude"),
        ("@cl quick question", "cl"),
        ("@c what time is it", "c"),
        ("Question for @CLAUDE about churn", "claude"),  # case-insensitive
        ("Please ping @Cl with the update", "cl"),
        ("(@claude!)", "claude"),  # punctuation boundary
        ("ok @c.", "c"),
        ("multi line\n@claude hello\nbye", "claude"),
        ("@claude", "claude"),  # alone, end-of-string
    ],
)
def test_detect_positive_cases(text: str, expected_trigger: str):
    result = detect(text)
    assert result is not None, f"expected match in: {text!r}"
    assert result["agent_name"] == "claude-sonnet-4-6"
    assert result["trigger"] == expected_trigger


# Negative cases — must NOT match (false-positive guards).

@pytest.mark.parametrize(
    "text",
    [
        "",  # empty
        "no mention here",
        "alice@claude.com sent the email",  # email — @ has word-char before
        "see @cloud function",  # @cloud (longer word)
        "@claire said yes",  # @claire (longer word)
        "@claudes are nice",  # @claudes (trailing word char)
        "@coding",  # @coding
        "ping @clive",  # @clive
        "ping @charles",
        "x@cl-banana",  # surrounded by other tokens
        "ABC@CLD",  # not a real word boundary
        "Look at@claude",  # @ has word-char before — looks like email
    ],
)
def test_detect_negative_cases(text: str):
    assert detect(text) is None, f"should NOT match: {text!r}"


def test_first_mention_wins():
    """Multiple mentions in one message → only the first fires (no spam)."""
    text = "@c then @claude and also @cl"
    result = detect(text)
    assert result is not None
    assert result["trigger"] == "c"
