"""Mention detector — pure regex matching for @agent triggers.

The set of aliases that trigger the team agent (Claude Sonnet 4.6 under the
hood) is CONFIG-DRIVEN via `settings.AGENT_MENTION_ALIASES` (comma-separated,
no leading '@'). Neutral default is `agent` (`@agent`) so a fresh OSS install
works out of the box without any brand token baked into the regex; operators
override the list in `.env` to add their own aliases.

Returns a normalized {"agent_name": str, "trigger": str} dict or None.

Word-boundary intentional: matches `hey @g what's up` but NOT
`alice@groove.com` or `@google`/`@github`. The trailing `(?=\\s|$|[^\\w])`
makes sure `@gr` matches `@gr ` or end-of-text but rejects `@group`.
"""
from __future__ import annotations

import re
from typing import TypedDict

from app.config import settings


def _build_mention_regex(aliases_csv: str) -> re.Pattern[str]:
    """Build the mention regex from a comma-separated alias list.

    Aliases are sorted LONGEST-FIRST before joining with `|` — regex
    alternation is first-match-wins, so `g|groove` would match `@g` inside
    `@groove` and truncate. Longest-first preserves correct behavior
    regardless of alias ordering in the env var.
    """
    aliases = [a.strip() for a in aliases_csv.split(",") if a.strip()]
    if not aliases:
        # A blank/comma-only value (AGENT_MENTION_ALIASES="" or ",,") would otherwise
        # produce the alternation "" and compile to `@()`, which matches a BARE "@" —
        # every "@" in every message would summon the agent. Fall back to the same
        # default the Settings field declares.
        aliases = ["agent"]
    aliases.sort(key=len, reverse=True)
    escaped = "|".join(re.escape(a) for a in aliases)
    return re.compile(
        rf"(?:^|(?<=[^\w@]))@({escaped})(?=$|[\s.,!?;:()\[\]{{}}'\"])",
        re.IGNORECASE,
    )


# Word-boundary start + exact alias + (whitespace | EOS | punctuation) after.
# Compiled once at module level from config (as before — only the source of
# the alias list changed).
_MENTION_RE = _build_mention_regex(settings.AGENT_MENTION_ALIASES)


class Mention(TypedDict):
    agent_name: str  # canonical: claude-sonnet-4-6
    trigger: str  # the literal alias that matched


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
