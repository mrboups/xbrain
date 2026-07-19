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
# the alias list changed). Used when `detect` is called WITHOUT an explicit
# alias list (unchanged callers).
_MENTION_RE = _build_mention_regex(settings.AGENT_MENTION_ALIASES)

# "claude" is RESERVED — it is never a summon trigger, regardless of what a team
# stored or an env var declares (Phase 21 / D-21-01). Compared case-insensitively.
_RESERVED = frozenset({"claude"})


def effective_aliases(custom_csv: str | None) -> list[str]:
    """Resolve a team's effective mention-alias list — the single server-side source.

    Union of the env/`AGENT_MENTION_ALIASES` defaults and the team's CUSTOM aliases
    (`custom_csv`, may be None), with these invariants (Phase 21 / D-21-01):
      * `"agent"` is ALWAYS present (universal default that never breaks).
      * `"claude"` is NEVER present (reserved; filtered case-insensitively — defense
        in depth on top of the 21-02 edge validation).
      * deduped case-insensitively, first occurrence kept with its original casing.
      * order: `agent` first, then env defaults, then custom.

    Returned tokens carry NO leading '@' and feed straight into `_build_mention_regex`
    (which `re.escape`s each), so a malicious stored alias cannot inject regex.
    """
    ordered = (
        ["agent"]
        + [a.strip() for a in settings.AGENT_MENTION_ALIASES.split(",") if a.strip()]
        + [a.strip() for a in (custom_csv or "").split(",") if a.strip()]
    )
    result: list[str] = []
    seen: set[str] = set()
    for tok in ordered:
        low = tok.lower()
        if low in _RESERVED or low in seen:
            continue
        seen.add(low)
        result.append(tok)
    return result


# Per-alias-set compiled-regex cache, keyed by the NORMALIZED (sorted + lowercased)
# alias csv so `["agent","wizard"]`, `["wizard","agent"]` and `["Agent","WIZARD"]`
# all reuse one compiled Pattern instead of recompiling on every message.
_regex_cache: dict[str, re.Pattern[str]] = {}


def _cache_key(aliases: list[str]) -> str:
    return ",".join(sorted(a.lower() for a in aliases))


# CR-02: bound the cache. `PATCH agent-aliases` is self-service (any team admin),
# so an attacker could otherwise grow this process-global dict without limit by
# setting many distinct alias sets — a memory-exhaustion DoS. In real use the number
# of distinct sets is tiny (≈ one per team's config), so the cap is never hit; on
# overflow we drop the whole cache and let it rebuild lazily (crude but keeps it O(cap)).
_REGEX_CACHE_MAX = 512


def _regex_for(aliases: list[str]) -> re.Pattern[str]:
    """Return (building + caching on miss) the compiled regex for an alias set."""
    key = _cache_key(aliases)
    pattern = _regex_cache.get(key)
    if pattern is None:
        if len(_regex_cache) >= _REGEX_CACHE_MAX:
            _regex_cache.clear()  # bounded eviction — rebuilds lazily on next miss
        # Reuse the server's escape + longest-first sort. Order in the csv does not
        # matter (the key is normalized and _build_mention_regex re-sorts by length).
        pattern = _build_mention_regex(",".join(aliases))
        _regex_cache[key] = pattern
    return pattern


def starts_with_mention(content: str, aliases: list[str] | None = None) -> bool:
    """True if `content` BEGINS with an agent mention (word-boundary aware).

    Used by brain_ingest to skip agent-COMMAND messages (queries, not facts). Uses the
    same word-boundary regex as detect(), so `@a` matches `@a hi` but NOT `@austin`
    (fixing the naive `str.startswith("@a")` over-match). `aliases` scopes it to a
    team's effective list (env defaults ∪ custom); None → the env defaults with @agent
    guaranteed and @claude filtered.
    """
    if not content:
        return False
    rx = _regex_for(aliases) if aliases is not None else _regex_for(effective_aliases(None))
    return rx.match(content) is not None


class Mention(TypedDict):
    agent_name: str  # canonical: claude-sonnet-4-6
    trigger: str  # the literal alias that matched


def detect(content: str, aliases: list[str] | None = None) -> Mention | None:
    """Return the FIRST mention found in `content`, or None.

    Team-aware (Phase 21 / D-21-04): when `aliases` is a non-empty list, detection
    uses that team's per-alias-set CACHED regex; when omitted/empty, it falls back
    to the module-level `_MENTION_RE` so existing callers are unchanged.

    We only act on the first mention per message — multiple mentions in one
    message still fire ONE agent task (not N).
    """
    if not content:
        return None
    regex = _regex_for(aliases) if aliases else _MENTION_RE
    m = regex.search(content)
    if not m:
        return None
    return {
        "agent_name": "claude-sonnet-4-6",
        "trigger": m.group(1).lower(),
    }
