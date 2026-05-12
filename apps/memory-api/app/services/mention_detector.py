"""Mention detector — pure regex matching for @claude triggers.

Quick task 260512-tcr decision #2: `@claude`, `@c`, and `@cl` (case-
insensitive, word-boundary anchored) all map to Claude Sonnet 4.6.

Returns a normalized {"agent_name": str, "trigger": str} dict or None.

Word-boundary intentional: matches `hey @c what's up` but NOT
`alice@claude.com` or `@cloud`/`@claire`. The trailing `(?=\\s|$|[^\\w])`
makes sure `@cl` matches `@cl ` or end-of-text but rejects `@cloud`.
"""
from __future__ import annotations

import re
from typing import TypedDict

# Word-boundary start + exact alias + (whitespace | EOS | punctuation) after.
# IMPORTANT: order matters — `claude` must come before `cl` so the regex
# captures the longest alias for proper rendering in logs.
_MENTION_RE = re.compile(
    r"(?:^|(?<=[^\w@]))@(claude|cl|c)(?=$|[\s.,!?;:()\[\]{}'\"])",
    re.IGNORECASE,
)


class Mention(TypedDict):
    agent_name: str  # canonical: claude-sonnet-4-6
    trigger: str  # the literal alias that matched (claude / cl / c)


def detect(content: str) -> Mention | None:
    """Return the FIRST mention found in `content`, or None.

    We only act on the first mention per message — multiple mentions in one
    message still fire ONE agent task (not N).
    """
    if not content:
        return None
    m = _MENTION_RE.search(content)
    if not m:
        return None
    return {
        "agent_name": "claude-sonnet-4-6",
        "trigger": m.group(1).lower(),
    }
