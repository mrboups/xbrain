---
phase: 21-configurable-agent-aliases
reviewed: 2026-07-19T00:00:00Z
depth: deep
files_reviewed: 12
files_reviewed_list:
  - apps/memory-api/app/services/mention_detector.py
  - apps/memory-api/app/routes/team_chat.py
  - apps/memory-api/app/routes/teams.py
  - apps/memory-api/app/repos/teams.py
  - apps/memory-api/app/models/team.py
  - apps/memory-api/app/config.py
  - apps/memory-api/app/services/brain_ingest.py
  - apps/memory-api/alembic/versions/0025_team_agent_aliases.py
  - apps/memory-api/tests/test_agent_aliases_api.py
  - apps/memory-api/tests/test_agent_aliases_gate.py
  - apps/memory-api/tests/test_mention_detector.py
  - chrome-extension/chat_stream.js
  - chrome-extension/popup.js
  - chrome-extension/options.js
findings:
  critical: 2
  warning: 2
  info: 2
  total: 6
status: issues_found
---

# Phase 21: Code Review Report

**Reviewed:** 2026-07-19
**Depth:** deep (cross-file, call-chain traced from `team_chat.py` → `mention_detector.py` → `brain_ingest.py`, and client `popup.js` → `chat_stream.js`)
**Files Reviewed:** 14
**Status:** issues_found

## Summary

The core Phase 21 feature — per-team, configurable, escaped, cached mention-alias
detection with `@agent` guaranteed and `@claude` reserved — is implemented
correctly and is well covered by real-Postgres gate tests
(`test_agent_aliases_gate.py::test_summon_per_team_gate` genuinely proves
cross-team isolation, the `@claude` block, and `@agent` universality end to end).
AuthZ on `PATCH /v1/teams/{id}/agent-aliases` is correctly team-admin-gated
(with the pre-existing global-admin backdoor, consistent with the rest of the
file), `GET` is correctly member-gated, and both server and client escape
user-controlled alias tokens before compiling them into a regex — no ReDoS or
regex-injection path was found.

Two real defects were found outside the "summon" happy path the plan's gate
lesson targeted:

1. A silent-data-loss regression in `brain_ingest.py`'s command-prefix filter,
   directly caused by adding the single-character `"a"` default alias without
   preserving the previous code's boundary-safe short-prefix handling.
2. An unbounded, never-evicted process-wide regex cache in `mention_detector.py`
   that any authenticated (non-privileged) user can grow indefinitely via
   self-service team creation + repeated alias PATCHes — a resource-exhaustion
   DoS vector introduced by this phase.

Both are addressable with small, targeted fixes (see below). Two further
issues are quality/robustness WARNINGs, and two are INFO-level nits.

## Critical Issues

### CR-01: `_AGENT_COMMAND_PREFIXES` naive prefix match causes silent data loss for any message starting with "@a" (BLOCKER)

**File:** `apps/memory-api/app/services/brain_ingest.py:41-48,63-65`

**Issue:** Phase 21 changed the env default `AGENT_MENTION_ALIASES` to
`"agent,chad,a"` (`app/config.py:245`) and re-derived the brain-ingest
skip-filter from it:

```python
_AGENT_COMMAND_PREFIXES = tuple(f"@{alias}" for alias in effective_aliases(None))
# -> ("@agent", "@chad", "@a")
...
if low.startswith(_AGENT_COMMAND_PREFIXES):
    return False
```

`str.startswith()` has no word-boundary awareness — unlike the real mention
detector (`mention_detector._build_mention_regex`), which requires the alias
to be followed by whitespace/punctuation/end-of-string. With `"@a"` in the
prefix tuple, **any** message that merely *begins* with `"@a"` is treated as
an agent command and silently dropped from brain ingest, regardless of what
follows: `"@austin can you review the PR by EOD"`, `"@alice mentioned this in
standup"`, `"@amanda's design doc is ready"`, `"@api rate limit changed to
40k/min"` — all of these are ≥15 chars, clearly substantive by the product's
own relevance-classifier examples (`relevance_filter.py` explicitly lists
"Personnel assignments" as a RELEVANT category), and none of them are agent
commands. `is_brain_relevant()` is called as a **hard, unconditional gate** in
`relevance_filter.classify()` (`if not is_brain_relevant(content): return
False`) — Haiku is never consulted, so there is no semantic-classifier safety
net for this case. The memory item is silently never created; nothing logs an
error, nothing surfaces to the user. This directly contradicts the project's
core value proposition (`CLAUDE.md`: "Toute donnée produite... atterrit dans
une mémoire commune... Si tout le reste plante, ce contrat doit tenir").

The pre-Phase-21 code avoided exactly this class of bug for its short aliases
by requiring an explicit trailing boundary: `("@claude", "@c ", "@c\n", "@cl
", "@cl\n")`. That care was dropped when the prefix list was made
alias-driven. There is no test coverage for `is_brain_relevant` in this diff
(`test_brain_ingest.py` has zero references to it), so this regression is not
caught anywhere in CI.

**Fix:** Reuse the mention detector's own boundary-aware regex instead of a
bare `str.startswith()`:

```python
from app.services.mention_detector import _build_mention_regex, effective_aliases

_AGENT_COMMAND_RE = _build_mention_regex(",".join(effective_aliases(None)))

def is_brain_relevant(content: str) -> bool:
    c = (content or "").strip()
    if len(c) < _MIN_CHARS:
        return False
    if _AGENT_COMMAND_RE.match(c):
        return False
    return True
```

`_build_mention_regex` already anchors on `^` as one of its alternatives, so
`.match()` correctly requires the mention to be at the very start of the
message and to be followed by whitespace/punctuation/EOS — eliminating the
false-positive class entirely, and keeping the skip-list in the same single
source of truth the summon path uses.

### CR-02: `mention_detector._regex_cache` grows without bound — process-wide memory-exhaustion DoS (BLOCKER)

**File:** `apps/memory-api/app/services/mention_detector.py:90,97-106`

**Issue:**

```python
_regex_cache: dict[str, re.Pattern[str]] = {}

def _regex_for(aliases: list[str]) -> re.Pattern[str]:
    key = _cache_key(aliases)
    pattern = _regex_cache.get(key)
    if pattern is None:
        pattern = _build_mention_regex(",".join(aliases))
        _regex_cache[key] = pattern
    return pattern
```

This module-level dict has no size cap, no TTL, and no eviction — every
distinct normalized alias set ever seen by `detect()` stays cached for the
life of the process. `detect()` is invoked from `team_chat.py:247` on **every**
`POST /v1/teams/{id}/messages`, keyed on `mention_detector.effective_aliases(team.agent_aliases)`.

Team creation is self-service (`POST /v1/teams/self` and `/v1/teams/self-solo`
— any authenticated user becomes admin of their own new team, no approval
step, `apps/memory-api/app/routes/teams.py:498-592`), and
`PATCH /v1/teams/{id}/agent-aliases` is rate-limit-free. A single authenticated
(non-privileged) attacker can therefore:

1. Create N teams via `/v1/teams/self` (they're auto-admin of each).
2. For each team, `PATCH agent-aliases` with a fresh, never-before-seen custom
   alias (charset `[A-Za-z0-9_-]`, ≤32 chars, ≤8 items — ample entropy).
3. POST one chat message to that team (`detect()` runs, misses the cache,
   compiles + permanently caches a new `re.Pattern`).
4. Repeat indefinitely.

Every iteration adds one permanent, never-reclaimed entry to a process-global
dict, with no cap. This is a straightforward, cheap, unauthenticated-to-the-feature
(only requires an ordinary account) resource-exhaustion vector against the
`memory-api` process — a new DoS surface introduced by this phase, since no
such shared cache existed before.

Note also that legitimate churn compounds this: every time a team admin
changes their alias set, the *previous* combination's cache entry becomes
orphaned and is never removed — the cache never shrinks even for well-behaved
usage.

**Fix:** Bound the cache. Simplest: an LRU with a fixed capacity via
`functools.lru_cache` on a helper keyed by the normalized alias tuple, or a
manual cap-and-evict:

```python
from collections import OrderedDict

_MAX_CACHE_ENTRIES = 512
_regex_cache: "OrderedDict[str, re.Pattern[str]]" = OrderedDict()

def _regex_for(aliases: list[str]) -> re.Pattern[str]:
    key = _cache_key(aliases)
    pattern = _regex_cache.get(key)
    if pattern is not None:
        _regex_cache.move_to_end(key)
        return pattern
    pattern = _build_mention_regex(",".join(aliases))
    _regex_cache[key] = pattern
    if len(_regex_cache) > _MAX_CACHE_ENTRIES:
        _regex_cache.popitem(last=False)  # evict least-recently-used
    return pattern
```

(Concurrency note: the current get-then-set is safe under the single-threaded
asyncio event loop model since `_build_mention_regex` contains no `await` —
this is not itself a race condition. The bug is purely unbounded growth.)

## Warnings

### WR-01: Custom per-team aliases are not excluded from brain ingest — command text gets stored as a "fact"

**File:** `apps/memory-api/app/services/brain_ingest.py:41-48`

**Issue:** `_AGENT_COMMAND_PREFIXES` is derived once from `effective_aliases(None)`
— env defaults only, deliberately excluding any team's custom `agent_aliases`
(per the inline comment: "Per-team resolution is unnecessary for this cheap
... filter"). But the summon path (`team_chat.py:246-247`) *does* resolve
per-team, so a team that sets a custom alias (e.g. `"wizard"`) will summon the
agent on `"@wizard summarize the meeting"` — and that same message, being a
query rather than a fact, still passes `is_brain_relevant()` (it doesn't start
with `@agent`/`@chad`/`@a`) and gets persisted into the team's brain as a
`WORKING` memory item with `confidence=0.7`. This directly contradicts the
function's own docstring intent ("skip... agent-mention commands... those are
queries, not facts") for exactly the teams that used the feature this phase
shipped. It's a data-quality/pollution issue, not data loss, and the tradeoff
is at least documented — hence WARNING rather than BLOCKER.

**Fix:** Once CR-01's fix lands (regex-based, reusing `_build_mention_regex`),
extending it to accept an optional per-team alias list costs little:

```python
def is_brain_relevant(content: str, aliases: list[str] | None = None) -> bool:
    ...
    regex = _build_mention_regex(",".join(aliases)) if aliases else _AGENT_COMMAND_RE
    if regex.match(c):
        return False
    return True
```
and thread the team's `effective_aliases(team.agent_aliases)` through from
`team_chat.py` into `brain_ingest.ingest_team_message`, which already receives
`team_id`/`team_scope`.

### WR-02: `refreshAgentAliases()` race condition can leave the composer hint reflecting the wrong team

**File:** `chrome-extension/popup.js:413-430` (called from `switchTeam`, line 404)

**Issue:**

```js
async function refreshAgentAliases() {
  if (!state.activeTeamId) return;
  try {
    ...
    const data = await fetchJson(
      `${MEMORY_API_BASE}/v1/teams/${state.activeTeamId}/agent-aliases`,
      xbt_token,
    );
    const aliases = Array.isArray(data && data.aliases) ? data.aliases : null;
    if (aliases && aliases.length) {
      state.agentAliases = aliases;
      state.mentionRe = buildMentionRegex(aliases);
    }
  } catch (e) { ... }
}
```

`switchTeam(teamId)` is invoked un-awaited from the team-selector's `change`
handler (`wireHeader()`, line ~133-138). If the user switches teams twice in
quick succession (Team B, then Team C, before the first fetch resolves), two
concurrent `refreshAgentAliases()` calls are in flight. The function reads
`state.activeTeamId` only to build the request URL, and never re-checks it
when the response arrives — so if Team B's fetch resolves *after* Team C's
(a plausible ordering under real network conditions), `state.mentionRe` ends
up holding Team B's alias regex while the user is actually chatting in Team C.
The composer's "Will summon @X" hint (`updateMentionHint`) would then be
wrong for the active team. This is UI-only (the server independently resolves
`team.agent_aliases` from the URL `team_id` at POST time, so the actual summon
decision is never affected), but it's a real, user-visible correctness bug
introduced by this phase's new function.

**Fix:** Guard against stale responses by capturing and re-checking the team id:

```js
async function refreshAgentAliases() {
  const teamId = state.activeTeamId;
  if (!teamId) return;
  try {
    const { xbt_token } = await chrome.storage.local.get(["xbt_token"]);
    if (!xbt_token) return;
    const data = await fetchJson(
      `${MEMORY_API_BASE}/v1/teams/${teamId}/agent-aliases`,
      xbt_token,
    );
    if (state.activeTeamId !== teamId) return; // stale response — a newer switch won the race
    const aliases = Array.isArray(data && data.aliases) ? data.aliases : null;
    if (aliases && aliases.length) {
      state.agentAliases = aliases;
      state.mentionRe = buildMentionRegex(aliases);
    }
  } catch (e) {
    console.warn("[xbrain] agent-aliases refresh failed:", e);
  }
}
```

## Info

### IN-01: Dead code — `len(cleaned) > 8` check in `_validate_aliases` is unreachable

**File:** `apps/memory-api/app/routes/teams.py:126-127`

**Issue:** `_validate_aliases(raw)` is called exactly once
(`routes/teams.py:649`), always with `body.aliases`, which pydantic already
bounds to `Field(..., max_length=8)` (line 92) before the route handler runs.
Since the dedup loop only ever *removes* items (`cleaned` is built by
appending a subset of `raw`, `len(cleaned) <= len(raw) <= 8`), the guard

```python
if len(cleaned) > 8:
    raise HTTPException(422, "at most 8 custom aliases allowed")
```

can never trigger given the only call site. The docstring's claim that the
count is "re-checked post-dedup" is misleading — dedup can only shrink the
count, never grow it, so nothing is actually re-checked. Harmless today, but
worth removing (or converting to an `assert`) so a future caller of
`_validate_aliases` that bypasses the pydantic `Field` bound doesn't rely on a
check that reads as load-bearing but currently is not exercised by any test.

**Fix:** Either delete the dead branch, or change the comment to make clear
it is defensive/aspirational, not currently reachable via the API.

### IN-02: Docstring overstates PATCH validation ordering

**File:** `apps/memory-api/app/routes/teams.py:635-637`

**Issue:** The docstring says "The admin check runs BEFORE validation so a
non-admin never learns whether their input was well-formed." This is true for
the per-item checks inside `_validate_aliases` (charset, `claude`-reserved,
length), but FastAPI runs pydantic validation of the request body
(`AgentAliasesBody`, including the `Field(..., max_length=8)` list-length
bound) as part of dependency resolution **before** the route function body
executes at all — i.e. before `_require_team_admin` runs. A non-admin (or
even a non-member) submitting >8 items gets a 422 from pydantic, not the 403
from `_require_team_admin`. This leaks nothing team-specific (the same 422
fires for any team_id, valid or not), so it is not a security issue — just an
inaccurate comment that could mislead a future maintainer reasoning about
ordering guarantees.

**Fix:** Tighten the docstring: "the team-admin check runs before the
per-alias content validation in `_validate_aliases` (charset, length, `claude`
reserved-word). Pydantic's list-length bound on the request body is still
enforced by FastAPI before the handler runs, independent of this check."

---

## What was verified clean (no issues found)

- **Regex escaping / injection:** every user-controlled alias reaches
  `re.escape()` server-side (`_build_mention_regex`) and the mirrored
  `escapeAlias()` client-side before being placed in a regex alternation.
  Verified against the explicit `.* ` hostile-alias tests on both sides
  (`test_detect_malicious_alias_is_escaped_literal`, `buildMentionRegex: a
  hostile '.*' alias matches only the literal @.*`).
- **`@claude` invariant:** filtered in `effective_aliases()` (defense in
  depth against a badly-persisted DB row), rejected at input in
  `_validate_aliases()` (422), and filtered client-side in
  `buildMentionRegex()`. All three layers agree, and the real-Postgres gate
  test (`test_summon_per_team_gate`, Case D) proves `@claude` summons no team
  end to end.
- **Cross-team isolation:** `effective_aliases()` is a pure function of
  `(settings.AGENT_MENTION_ALIASES, team.agent_aliases)` with no shared
  mutable state; `test_cross_team_isolation` and `test_summon_per_team_gate`
  both prove team A's custom alias never fires for team B.
  `_regex_for`/`_regex_cache` is keyed purely on the normalized alias set, not
  on team identity, which is correct — two teams with the same effective
  alias set legitimately sharing one compiled `Pattern` is not a leak.
- **AuthZ on PATCH:** confirmed both by reading and by
  `test_non_admin_patch_forbidden` — a plain member of team A (403) and an
  admin of a *different* team B (403) are both rejected on team A's PATCH; the
  membership lookup is scoped by `team.slug` resolved from the path's
  `team_id`, so there is no cross-team admin bypass.
- **Migration 0025:** `down_revision = "0024_local_credentials"` is the sole
  reference to that revision in the versions directory (no branch), the
  column add is `IF NOT EXISTS` / idempotent, nullable, no backfill, and
  `downgrade()` exists for symmetry only — matches the stated Phase-17
  forward-only pattern. `test_migration_0025_agent_aliases_forward_only`
  exercises both `oss` and `saas` editions against a real container.
- **Alias input validation:** charset `[A-Za-z0-9_-]{1,32}` via `fullmatch`
  rejects all regex metacharacters and non-ASCII homoglyphs; count is capped
  at 8 both at the pydantic layer and (redundantly, see IN-01) in
  `_validate_aliases`.

---

_Reviewed: 2026-07-19_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
