---
phase: 23-catch-me-up
plan: 01
subsystem: database
tags: [alembic, sqlalchemy, postgres, team-chat, read-cursor, migration]

# Dependency graph
requires:
  - phase: 21-agent-aliases
    provides: "0025_team_agent_aliases — the forward-only, edition-agnostic migration head + additive ADD COLUMN pattern this migration copies"
  - phase: 10-github-auth
    provides: "TeamMember + block_member's Python-datetime mutator pattern (set the cursor with datetime.now(tz=utc), not a DDL server_default)"
provides:
  - "team_members.last_read_at nullable TIMESTAMPTZ column (migration 0026)"
  - "TeamMember.last_read_at ORM mapping (nullable, no server_default)"
  - "repos.teams.set_last_read(team_id, user_id) — advances one member's read cursor to now(); caller commits"
  - "repos.team_messages.list_messages(after_created_at=) — since-window symmetric to before_created_at"
  - "repos.team_messages.count_unread_since(team_id, after_created_at, exclude_user_id) — other members' non-deleted user messages after the cursor"
affects: [23-02-endpoints, 23-03-real-postgres-gate, catch-me-up, mark-read, unread-summary]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Read cursor as a nullable timestamptz set via Python datetime (mirrors block_member); NULL = never read"
    - "Since-window query symmetric to the existing before-window on list_messages; ordering unchanged (newest-first, reverse client-side)"
    - "'Unread that matters' count excludes the caller's own messages AND all agent frames (kind='user' AND author_user_id != caller)"

key-files:
  created:
    - apps/memory-api/alembic/versions/0026_team_member_last_read.py
    - apps/memory-api/tests/test_catchup_read_cursor_unit.py
  modified:
    - apps/memory-api/app/models/team.py
    - apps/memory-api/app/repos/teams.py
    - apps/memory-api/app/repos/team_messages.py

key-decisions:
  - "0026 copies the 0025 shape exactly: additive ADD COLUMN IF NOT EXISTS, no EDITION branch, no backfill, symmetric downgrade for form only (forward-only)"
  - "last_read_at has no server_default — the cursor is only ever set via a Python datetime in set_last_read, so the returned row stays .isoformat()-safe and INSERT never auto-fills it"
  - "count_unread_since excludes the caller's own rows and agent frames (kind='user' AND author_user_id != exclude_user_id); NULL cursor counts all other members' user messages"
  - "Repo functions flush; routes commit — no commit() added in either repo function (T-23-03 boundary honored)"

patterns-established:
  - "Pattern 1: per-member private cursor column (not a social read-receipt) — set via repo mutator, no cross-member exposure"
  - "Pattern 2: bidirectional time-window pagination on list_messages (before_ + after_ can be combined)"

requirements-completed: [CATCHUP-01]

# Metrics
duration: 7min
completed: 2026-07-19
---

# Phase 23 Plan 01: Catch Me Up Read-Cursor Foundation Summary

**Per-member `team_members.last_read_at` cursor (migration 0026 + ORM) plus the repo primitives Catch me up consumes: `set_last_read`, an `after_created_at` since-window on `list_messages`, and a `count_unread_since` that excludes the caller's own messages and agent frames.**

## Performance

- **Duration:** ~7 min
- **Started:** 2026-07-19T08:14:31+02:00 (base commit)
- **Completed:** 2026-07-19T06:21:04Z
- **Tasks:** 2
- **Files modified:** 5 (2 created, 3 modified)

## Accomplishments
- Migration 0026 adds a nullable `team_members.last_read_at` TIMESTAMPTZ, chained off 0025, additive/idempotent (`ADD COLUMN IF NOT EXISTS`), edition-agnostic (no EDITION token), single Alembic head confirmed = `0026_team_member_last_read`.
- `TeamMember.last_read_at` mapped nullable with no server_default (cursor is Python-datetime-driven).
- `teams.set_last_read` advances one member's cursor to `now()` (mirrors `block_member`; caller commits).
- `team_messages.list_messages` gains a keyword-only `after_created_at` since-window (`created_at > after`), symmetric to the untouched `before_created_at`; ordering unchanged.
- `team_messages.count_unread_since` counts only OTHER members' non-deleted user messages after the cursor (excludes the caller + agent frames); NULL cursor counts all.
- 7 unit tests (no Docker) assert the migration chain/additivity, ORM column nullability + tz-awareness, and repo signatures + query predicates — all PASS.

## Task Commits

Each task was committed atomically:

1. **Task 1: Migration 0026 + TeamMember.last_read_at ORM** - `b1ad1f5` (feat)
2. **Task 2: Repo primitives — set_last_read, after_created_at window, count_unread_since** - `0a3ef45` (feat)

_Task 2's commit also carries the unit test file (`test_catchup_read_cursor_unit.py`) that exercises both tasks' contracts._

## Files Created/Modified
- `apps/memory-api/alembic/versions/0026_team_member_last_read.py` (created) - Migration 0026: nullable `team_members.last_read_at`, forward-only, edition-agnostic.
- `apps/memory-api/app/models/team.py` (modified) - Added `TeamMember.last_read_at` nullable, after `joined_at`.
- `apps/memory-api/app/repos/teams.py` (modified) - Added `set_last_read` cursor mutator (Phase 23 section).
- `apps/memory-api/app/repos/team_messages.py` (modified) - Added `after_created_at` param to `list_messages`; added `count_unread_since`; imported `func`.
- `apps/memory-api/tests/test_catchup_read_cursor_unit.py` (created) - 7 pure unit tests for the migration, ORM column, and repo primitives.

## Test Output (real)

```
tests/test_catchup_read_cursor_unit.py::test_migration_0026_chains_off_0025 PASSED
tests/test_catchup_read_cursor_unit.py::test_migration_0026_is_additive_idempotent PASSED
tests/test_catchup_read_cursor_unit.py::test_team_member_last_read_at_column_is_nullable_timestamptz PASSED
tests/test_catchup_read_cursor_unit.py::test_set_last_read_signature PASSED
tests/test_catchup_read_cursor_unit.py::test_list_messages_has_after_created_at_window PASSED
tests/test_catchup_read_cursor_unit.py::test_count_unread_since_signature_and_predicates PASSED
tests/test_catchup_read_cursor_unit.py::test_now_is_timezone_aware_helper_used PASSED
======================== 7 passed, 1 warning in 1.48s =========================
```

Existing guard still green: `test_migration_editions.py::test_no_migration_branches_on_edition` PASSED (0026 introduces no EDITION branch). Alembic single head confirmed: `['0026_team_member_last_read']`.

## Decisions Made
None beyond the plan — followed the locked D-23-01 / D-23-02 decisions and the 0025 pattern exactly. See frontmatter `key-decisions` for the load-bearing rationale (no server_default; count excludes caller + agent frames; repos flush, routes commit).

## Deviations from Plan
None - plan executed exactly as written.

## Issues Encountered
- The agent runs in a git worktree at a path distinct from the shared checkout; the initial file write targeted the shared path and was redirected to the worktree copy. Resolved by writing/editing under the worktree path. No impact on output.
- Git reports `LF will be replaced by CRLF` warnings on the new files (Windows autocrlf). Cosmetic; files committed correctly.

## User Setup Required
None - no external service configuration required. Migration 0026 applies automatically on the next `alembic upgrade head` (proven under oss AND saas by the real-Postgres gate in Plan 23-03).

## Next Phase Readiness
- The contracts the endpoints (23-02) call — `set_last_read`, `list_messages(after_created_at=)`, `count_unread_since` — exist with the exact signatures documented here.
- The real-Postgres behavioral + forward-only-under-oss-AND-saas gate is Plan 23-03's job (not mocked here by design).
- No blockers.

## Self-Check: PASSED

All created/modified files present on disk; both task commits (`b1ad1f5`, `0a3ef45`) exist in git history.

---
*Phase: 23-catch-me-up*
*Completed: 2026-07-19*
