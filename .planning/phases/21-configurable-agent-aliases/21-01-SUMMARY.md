---
phase: 21-configurable-agent-aliases
plan: 01
subsystem: api
tags: [alembic, sqlalchemy, regex, mention-detection, agent-aliases, fastapi, pydantic-settings]

# Dependency graph
requires:
  - phase: 14-neutral-mention-alias
    provides: "_build_mention_regex (re.escape + longest-first sort + empty fallback) and config-driven AGENT_MENTION_ALIASES"
  - phase: 18-local-auth
    provides: "alembic head 0024_local_credentials (down_revision base for 0025)"
provides:
  - "Nullable teams.agent_aliases TEXT column (migration 0025, forward-only, edition-agnostic)"
  - "Team.agent_aliases ORM attribute (Mapped[str | None])"
  - "effective_aliases(custom_csv) — single server-side resolver: defaults union custom, @agent always, @claude never, deduped"
  - "detect(content, aliases=None) — team-aware with per-alias-set compiled-regex cache; backward-compatible for existing callers"
  - "Config default AGENT_MENTION_ALIASES expanded to agent,chad,a (D-21-01)"
affects: [21-02-api-wiring, 21-03-summon-gate, team_chat, chrome-extension-mention-regex]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Per-alias-set compiled-regex cache keyed on normalized (sorted+lowercased) csv — avoids recompiling every message"
    - "Optional-arg overload (detect(content, aliases=None)) preserves existing single-arg callers while adding team-awareness"
    - "Reserved-token filter (frozenset) applied in the resolver as defense-in-depth, independent of edge validation"

key-files:
  created:
    - apps/memory-api/alembic/versions/0025_team_agent_aliases.py
    - .planning/phases/21-configurable-agent-aliases/21-01-SUMMARY.md
  modified:
    - apps/memory-api/app/models/team.py
    - apps/memory-api/app/config.py
    - apps/memory-api/app/services/mention_detector.py
    - apps/memory-api/tests/test_mention_detector.py

key-decisions:
  - "effective_aliases() is the SOLE server-side source of truth for a team's alias list; downstream (21-02 GET/PATCH, 21-03 gate) consume it rather than re-deriving"
  - "@claude filtered case-insensitively inside the resolver (defense in depth) even though 21-02 also rejects it at the edge"
  - "detect() keys its regex cache on the normalized alias tuple so reordered/recased lists reuse one compiled Pattern"
  - "Migration 0025 is additive/nullable/no-backfill and never branches on the install-edition flag (forward-only, Phase-17 pattern)"

patterns-established:
  - "Alias resolution: [agent] + env defaults + custom, first-occurrence-wins dedup, claude removed"
  - "Compiled-regex memoization by normalized key for per-team dynamic pattern sets"

requirements-completed: [ALIAS-01]

# Metrics
duration: 6min
completed: 2026-07-19
---

# Phase 21 Plan 01: Persistence + Team-Aware Detector Core Summary

**Nullable teams.agent_aliases column (migration 0025) plus effective_aliases() resolver and a per-alias-set cached, team-aware detect() overload — @agent always fires, @claude never does, custom aliases fire only for the team that set them.**

## Performance

- **Duration:** 6 min
- **Started:** 2026-07-19T01:02:18Z
- **Completed:** 2026-07-19T01:08:27Z
- **Tasks:** 2 (Task 2 was TDD: RED → GREEN)
- **Files modified:** 4 modified + 1 created (migration)

## Accomplishments
- Migration `0025_team_agent_aliases` chained to `0024_local_credentials`: additive nullable `teams.agent_aliases TEXT`, forward-only, no edition branch.
- `Team.agent_aliases` ORM column (`Mapped[str | None]`, comma-separated custom aliases, no leading `@`).
- `effective_aliases(custom_csv)`: the single server-side resolver — defaults (`settings.AGENT_MENTION_ALIASES`) ∪ custom, `@agent` guaranteed present, `@claude` filtered (case-insensitive), deduped with defaults preceding custom.
- `detect(content, aliases=None)`: team-aware via a per-alias-set compiled-regex cache (`_regex_cache` / `_regex_for`), keyed on the normalized `sorted+lowercased` csv; falls back to the module-level `_MENTION_RE` for unchanged callers.
- Config default `AGENT_MENTION_ALIASES` expanded from `agent` to `agent,chad,a` (D-21-01).
- Unit tests extended by 15 cases; full file passes 36/36.

## Task Commits

Each task was committed atomically:

1. **Task 1: Migration 0025 + Team.agent_aliases column + config default** - `7810adb` (feat)
2. **Task 2 (TDD RED): failing tests for effective_aliases + team-aware detect** - `9e26c5f` (test)
3. **Task 2 (TDD GREEN): effective_aliases() + cached team-aware detect()** - `06badea` (feat)

_TDD REFACTOR gate: not needed — GREEN implementation was already clean._

## Files Created/Modified
- `apps/memory-api/alembic/versions/0025_team_agent_aliases.py` - Adds nullable `teams.agent_aliases TEXT` (IF NOT EXISTS), down_revision `0024_local_credentials`.
- `apps/memory-api/app/models/team.py` - `Team.agent_aliases: Mapped[str | None]` (Text, nullable).
- `apps/memory-api/app/config.py` - Default `AGENT_MENTION_ALIASES = "agent,chad,a"` + updated comment.
- `apps/memory-api/app/services/mention_detector.py` - `effective_aliases()`, `_regex_cache`, `_cache_key`, `_regex_for`, and the `detect(content, aliases=None)` overload; `_RESERVED = {"claude"}`.
- `apps/memory-api/tests/test_mention_detector.py` - 15 new tests (resolver invariants, per-team fire/non-fire, @claude-gone, short-alias boundary, cache identity, malicious-alias escape).

## Verification (real output)

- `python -m pytest tests/test_mention_detector.py -q` → **36 passed, 1 warning** (15 new Phase-21 cases + 21 existing).
- `test_migration_editions.py::test_no_migration_branches_on_edition` → **1 passed** (migration 0025 contains no `EDITION` token).
- `ast.parse` clean on `0025_team_agent_aliases.py`, `team.py`, `mention_detector.py`.
- Acceptance assertions (real values):
  - `effective_aliases(None)` = `['agent']` (test env default)
  - `effective_aliases("wizard")` = `['agent', 'wizard']`
  - `effective_aliases("claude, wizard")` = `['agent', 'wizard']` (claude filtered)
  - `detect("@wizard x", ["agent","wizard"])["trigger"]` = `"wizard"`; `detect("@wizard x", ["agent"])` = `None`
  - `detect("@claude x", ["agent","wizard","chad","a"])` = `None`; `detect("@agent x", ["agent","wizard"])["trigger"]` = `"agent"`
  - cache keys after 2 identical `detect` calls = `['agent,wizard']` (one entry — no recompile)

## Decisions Made
- Kept `effective_aliases()` as the sole resolver so downstream plans import one contract instead of re-deriving alias merging. Filter `@claude` here (defense in depth) even though 21-02 will also reject it at the PATCH edge.
- Regex cache key normalizes order + case so semantically identical alias sets share one compiled `Pattern`; `_build_mention_regex` still re-sorts longest-first internally, so csv order into `_regex_for` is irrelevant.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- A direct `python -c` smoke run initially failed because `Settings()` requires env vars (`DATABASE_URL`, `OAUTH_*`, etc.) that `conftest.py` normally seeds. Re-ran with those env defaults exported — not a code issue; the pytest suite (which loads conftest) passed throughout.

## Known Stubs
None - no placeholder data or unwired sources introduced. `detect()`'s team-aware branch is functional; wiring `team_chat.py` to resolve and pass a team's aliases is intentionally deferred to plan 21-02 (route/endpoint wave), per this plan's objective ("No route wiring or endpoints yet").

## User Setup Required
None for fresh OSS installs — the code default already delivers `agent,chad,a`.

**Ops note (next deploy):** the xbrain VM `.env` currently sets `AGENT_MENTION_ALIASES=agent,chad`. Update it to `AGENT_MENTION_ALIASES=agent,chad,a` on the next deploy so the live default matches the code default (adds the short `@a`). This is a `.env` change only — no migration or restart-order dependency.

## Next Phase Readiness
- Contracts ready for 21-02: `Team.agent_aliases` (persistence), `effective_aliases(custom_csv)` (effective-list resolver for the GET endpoint + client sync), and `detect(content, aliases)` (team-aware summon at `team_chat.py:243`).
- Migration 0025 applies forward-only; the real-Postgres per-edition upgrade proof runs in CI (`test_migration_editions.py`) and the end-to-end summon gate against real Postgres lands in 21-03.
- No blockers.

## Self-Check: PASSED

All created/modified files present on disk; all task commits (`7810adb`, `9e26c5f`, `06badea`) exist in git history.

---
*Phase: 21-configurable-agent-aliases*
*Completed: 2026-07-19*
