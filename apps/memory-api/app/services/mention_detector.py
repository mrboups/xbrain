"""Mention detector — pure regex matching for @groove triggers.

`@groove`, `@gr`, and `@g` (case-insensitive, word-boundary anchored) all
trigger the Groove team agent (Claude Sonnet 4.6 under the hood).

Returns a normalized {"agent_name": str, "trigger": str} dict or None.

Word-boundary intentional: matches `hey @g what's up` but NOT
`alice@groove.com` or `@google`/`@github`. The trailing `(?=\\s|$|[^\\w])`
makes sure `@gr` matches `@gr ` or end-of-text but rejects `@group`.
"""
from __future__ import annotations

import re
from typing import TypedDict

# Word-boundary start + exact alias + (whitespace | EOS | punctuation) after.
# IMPORTANT: order matters — `groove` must come before `gr`/`g` so the regex
# captures the longest alias for proper rendering in logs.
_MENTION_RE = re.compile(
    r"(?:^|(?<=[^\w@]))@(groove|gr|g)(?=$|[\s.,!?;:()\[\]{}'\"])",
    re.IGNORECASE,
)


class Mention(TypedDict):
    agent_name: str  # canonical: claude-sonnet-4-6
    trigger: str  # the literal alias that matched (groove / gr / g)


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
