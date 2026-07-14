---
phase: 18-local-auth
plan: 01
subsystem: auth
tags: [postgres, alembic, sqlalchemy, argon2id, lockout, asyncpg]

# Dependency graph
requires:
  - phase: 12-github-app-migration
    provides: users table (no unique constraint on email), repos/users.py get_or_create_user + get_user_by_email patterns to mirror
provides:
  - "local_credentials table (migration 0024) — user_id PK/FK -> users.id ON DELETE CASCADE, password_hash, algo, failed_attempts, locked_until, timestamps"
  - "app/repos/local_credentials.py — create (conflict-safe bool)/get_by_user_id/get_by_email (case-insensitive)/record_failure/reset_failures/update_hash/upsert, all flush()-only"
  - "LOCAL_AUTH_MAX_FAILED_ATTEMPTS / LOCAL_AUTH_LOCKOUT_MINUTES / LOCAL_AUTH_RATE_LIMIT / LOCAL_AUTH_MIN_PASSWORD_LENGTH config knobs with safe defaults, no fail-fast validator"
affects: [18-02-password-hashing-rate-limit, 18-03-register-login-routes, 18-04-set-password, 18-06-recovery-docs]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Raw sa.text() SQL for tables without a full ORM model (mirrors app/repos/merge.py) — used throughout local_credentials.py instead of adding a new app/models/*.py file, since the plan's files_modified list did not include one"
    - "Repo helpers flush() only, never commit() — caller owns the transaction (mirrors teams_repo.create_team)"
    - "Postgres UPDATE...SET with a CASE referencing the pre-update column value (failed_attempts + 1 >= :max_attempts) to compute the new lockout state in a single round trip"

key-files:
  created:
    - apps/memory-api/alembic/versions/0024_local_credentials.py
    - apps/memory-api/app/repos/local_credentials.py
    - apps/memory-api/tests/test_local_credentials_repo.py
  modified:
    - apps/memory-api/app/config.py

key-decisions:
  - "down_revision is the full stem \"0023_tasks_source_connector\" (verified against the live file, not just grepped) — alembic heads resolves to 0024_local_credentials (head) against the real chain"
  - "No app/models/local_credentials.py ORM model added — the plan's files_modified list only names alembic/repo/config/test files, so the repo uses raw sa.text() SQL (the same style already established by app/repos/merge.py for tables that don't need ORM mapping)"
  - "No uniqueness guard added on users.email (research Pitfall 4) — D-18-05 collision correctness stays app-layer only"
  - "record_failure uses Postgres make_interval(mins => :lockout_minutes) rather than string-concatenated interval literals, avoiding bind-parameter/interval-cast ambiguity"

patterns-established:
  - "Lockout state transition computed server-side in one UPDATE (no read-then-write race window between checking failed_attempts and setting locked_until)"

requirements-completed: [LAUTH-01]

# Metrics
duration: 15min
completed: 2026-07-14
---

# Phase 18 Plan 01: Local Credentials Data Layer Summary

**New `local_credentials` Postgres table (migration 0024) plus a flush()-only repo (create/get_by_email/get_by_user_id/record_failure/reset_failures/update_hash/upsert) and four `LOCAL_AUTH_*` config knobs, all proven against a real testcontainers Postgres — the data-layer foundation the rest of Phase 18's email/password auth builds on.**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-07-14T01:17:56Z (worktree base commit)
- **Completed:** 2026-07-14T01:30:27Z
- **Tasks:** 2 completed
- **Files modified:** 4 (3 created, 1 modified)

## Accomplishments
- Migration `0024_local_credentials` creates the dedicated credentials table per D-18-03 (no new columns on the already GitHub-column-heavy `users` table), with the full-stem `down_revision = "0023_tasks_source_connector"` verified to resolve via `alembic heads` against the real chain — not just grepped.
- `app/repos/local_credentials.py` provides all seven helpers the plan requires, tested against real Postgres: conflict-safe `create` (bool return, no raised IntegrityError on a duplicate `user_id`), case-insensitive `get_by_email` joining the canonical `users.email` column, DB-backed lockout counters (`record_failure`/`reset_failures`), `update_hash`, and `upsert`.
- `Settings` gained `LOCAL_AUTH_MAX_FAILED_ATTEMPTS` (5), `LOCAL_AUTH_LOCKOUT_MINUTES` (15), `LOCAL_AUTH_RATE_LIMIT` ("10/minute"), `LOCAL_AUTH_MIN_PASSWORD_LENGTH` (10) — all with safe defaults, no `field_validator`, confirmed a clean boot still works with no Google/GitHub/OAuth-social env vars set.
- 10/10 integration tests pass against real testcontainers Postgres (not skipped) — proves the migration chain applies via the `pg_url` fixture's `alembic upgrade head`, the conflict-safe create, case-insensitive lookup, lockout threshold + reset, `update_hash`, `upsert`, and FK cascade on `DELETE FROM users`.

## Task Commits

Each task was committed atomically (Task 2 followed the RED/GREEN TDD cycle per its `tdd="true"` flag):

1. **Task 1: Migration 0024 (local_credentials) + LOCAL_AUTH_* config knobs** - `043ccdf` (feat)
2. **Task 2 (RED): failing tests for local_credentials repo** - `61bd936` (test)
3. **Task 2 (GREEN): local_credentials repo implementation** - `afa6167` (feat)

## Files Created/Modified
- `apps/memory-api/alembic/versions/0024_local_credentials.py` - New `local_credentials` table, full-stem revision/down_revision, no email uniqueness guard
- `apps/memory-api/app/repos/local_credentials.py` - create/get_by_user_id/get_by_email/record_failure/reset_failures/update_hash/upsert, raw `sa.text()` SQL, flush()-only
- `apps/memory-api/tests/test_local_credentials_repo.py` - 10 real-Postgres integration tests covering every behavior in the plan
- `apps/memory-api/app/config.py` - Added `LOCAL_AUTH_MAX_FAILED_ATTEMPTS`/`LOCAL_AUTH_LOCKOUT_MINUTES`/`LOCAL_AUTH_RATE_LIMIT`/`LOCAL_AUTH_MIN_PASSWORD_LENGTH`

## Decisions Made
- **No new ORM model file.** The plan's `files_modified` list names only the migration, config, repo, and test files — no `app/models/local_credentials.py`. Rather than add an out-of-scope file, the repo uses raw parameterized `sa.text()` SQL, the same approach `app/repos/merge.py` already uses in this codebase for tables that don't need a mapped ORM class. All acceptance criteria (case-insensitive lookup via `lower(...)`, zero `commit()` calls) are satisfied by this approach.
- **Docstring wording adjusted to avoid a false-positive on the "no email unique index" acceptance-criteria grep.** The first draft's explanatory comment ("No UNIQUE index is added on users.email...") itself matched the `unique.*index.*email` pattern the grep checks for. Reworded to describe the same reasoning without using the words "unique", "index", and "email" in that order — the actual SQL never had an index, only the comment's phrasing needed to change.
- **Lockout interval computed via `make_interval(mins => :lockout_minutes)`** rather than a concatenated interval-literal string, avoiding any ambiguity in how asyncpg/SQLAlchemy would bind an integer parameter into an interval expression.

## Deviations from Plan

None — plan executed exactly as written. The docstring wording adjustment above was a same-task correction to satisfy the plan's own acceptance criterion, not a deviation from the plan's intent.

## Issues Encountered

**Grep false positive on the email-uniqueness acceptance check.** The migration's explanatory comment about *not* adding a unique index on `users.email` itself matched the acceptance-criteria grep pattern (`unique.*index.*email`) because the comment contained those three words in that order. Caught by re-running the exact acceptance-criteria command before committing; fixed by rewording the comment (see Decisions Made). No functional/SQL change was needed — the migration never had an index to remove.

## User Setup Required

None - no external service configuration required. This plan is data-layer only; no routes are exposed yet (Plan 03 wires `register`/`login`).

## Next Phase Readiness

- The `local_credentials` table, repo, and config knobs are ready for Plan 02 (password hashing service + in-process rate limiter) and Plan 03 (register/login routes) to build on.
- Plan 02 owns `apps/memory-api/pyproject.toml` (adds `argon2-cffi`) and `app/services/*` — untouched by this plan, confirmed via `git diff --stat` against files this plan is scoped to avoid.
- `alembic heads` resolves to `0024_local_credentials (head)`; any parallel plan touching migrations after this one must chain from that revision id.

---
*Phase: 18-local-auth*
*Completed: 2026-07-14*

## Self-Check: PASSED

All created files verified present on disk (0024_local_credentials.py, local_credentials.py repo, test_local_credentials_repo.py, config.py, this SUMMARY). All 3 task commit hashes (043ccdf, 61bd936, afa6167) verified present in git log.
