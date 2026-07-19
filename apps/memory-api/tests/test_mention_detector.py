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

from app.config import settings
from app.services.mention_detector import (
    _build_mention_regex,
    _regex_cache,
    _regex_for,
    detect,
    effective_aliases,
)


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


# === Phase 21 (21-01) — effective_aliases() resolver ===
#     defaults (settings.AGENT_MENTION_ALIASES) union custom, "@agent" always,
#     "@claude" never, deduped case-insensitively (defaults precede custom).


def test_effective_aliases_agent_always_present():
    """@agent is a universal default — present on any effective list."""
    result = effective_aliases(None)
    lowers = [a.lower() for a in result]
    assert "agent" in lowers
    # every default token from settings is carried through
    for tok in [t.strip().lower() for t in settings.AGENT_MENTION_ALIASES.split(",") if t.strip()]:
        assert tok in lowers


def test_effective_aliases_custom_added_after_defaults():
    result = effective_aliases("wizard")
    lowers = [a.lower() for a in result]
    assert "agent" in lowers
    assert "wizard" in lowers
    # defaults precede custom
    assert lowers.index("agent") < lowers.index("wizard")


def test_effective_aliases_dedup_case_insensitive():
    result = effective_aliases("Agent, wizard, WIZARD")
    lowers = [a.lower() for a in result]
    assert lowers.count("agent") == 1
    assert lowers.count("wizard") == 1


def test_effective_aliases_default_set_monkeypatched(monkeypatch):
    """With the expanded default (D-21-01), agent/chad/a all resolve."""
    monkeypatch.setattr(
        "app.services.mention_detector.settings.AGENT_MENTION_ALIASES", "agent,chad,a"
    )
    lowers = [a.lower() for a in effective_aliases(None)]
    assert "agent" in lowers
    assert "chad" in lowers
    assert "a" in lowers


def test_effective_aliases_claude_never_present():
    """@claude is reserved — filtered out even if a bad string persisted it."""
    lowers = [a.lower() for a in effective_aliases("claude, wizard")]
    assert "claude" not in lowers
    assert "wizard" in lowers


# === Phase 21 (21-01) — team-aware detect(content, aliases) ===


@pytest.mark.parametrize(
    "text,aliases,expected",
    [
        ("@wizard hi", ["agent", "wizard"], "wizard"),  # custom alias fires
        ("@agent hi", ["agent", "wizard"], "agent"),    # universal default fires
        ("@a hi", ["agent", "a"], "a"),                 # short alias, boundary-correct
    ],
)
def test_detect_team_aware_positive(text: str, aliases: list[str], expected: str):
    result = detect(text, aliases)
    assert result is not None, f"expected match in {text!r} with {aliases}"
    assert result["trigger"] == expected


@pytest.mark.parametrize(
    "text,aliases",
    [
        ("@wizard hi", ["agent"]),                       # team did NOT set this alias
        ("@claude hi", ["agent", "wizard", "chad", "a"]),  # @claude never triggers
        ("@apple hi", ["agent", "a"]),                   # short alias must not truncate
    ],
)
def test_detect_team_aware_negative(text: str, aliases: list[str]):
    assert detect(text, aliases) is None, f"should NOT match {text!r} with {aliases}"


def test_detect_backward_compat_no_aliases():
    """Unchanged callers (no aliases arg) keep using the module-level default regex."""
    result = detect("@agent hi")
    assert result is not None
    assert result["trigger"] == "agent"


def test_detect_regex_cache_reused():
    """A repeated alias-set does not recompile — no new cache key on the 2nd call."""
    _regex_cache.clear()
    aliases = ["agent", "wizard"]
    detect("@wizard one", aliases)
    assert len(_regex_cache) == 1
    detect("@wizard two", aliases)
    assert len(_regex_cache) == 1  # identical set → no new key
    # key is normalized (sorted + lowercased): a reordered/recased list hits the SAME entry
    detect("@wizard three", ["WIZARD", "Agent"])
    assert len(_regex_cache) == 1


def test_regex_for_returns_same_pattern_object():
    """_regex_for keys on the normalized alias tuple → same compiled Pattern reused."""
    _regex_cache.clear()
    p1 = _regex_for(["agent", "wizard"])
    p2 = _regex_for(["wizard", "agent"])
    assert p1 is p2


def test_detect_malicious_alias_is_escaped_literal():
    """A metacharacter alias like ".*" is re.escape()d — literal, never 'any char'."""
    aliases = ["agent", ".*"]
    # the literal token "@.*" fires (escaped alias matches itself)
    assert detect("@.* hi", aliases) is not None
    # if ".*" leaked unescaped, "@x" would match; escaped, it must NOT
    assert detect("@x hi", aliases) is None
