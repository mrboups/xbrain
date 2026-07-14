---
phase: 18-local-auth
plan: 03
subsystem: auth
tags: [fastapi, sqlalchemy, argon2, postgres, testcontainers, rate-limiting, lockout]

# Dependency graph
requires:
  - phase: 18-local-auth (plan 01)
    provides: local_credentials repo (create/get_by_email/record_failure/reset_failures/update_hash), migration 0024_local_credentials
  - phase: 18-local-auth (plan 02)
    provides: argon2id password_hash service (hash_password/verify_password/verify_decoy/needs_rehash), in-process rate_limit service, shared mint_xbt_for_user helper
provides:
  - "POST /v1/auth/local/register — atomic single-commit account creation (user + credential + solo team + xbt_), 409 on existing-email or concurrent-duplicate-credential collision"
  - "POST /v1/auth/local/login — decoy-timed, byte-identical generic 401 for absent/wrong/locked, DB-backed lockout with post-expiry recovery, rehash-on-success"
  - "auth_local.router mounted CORE in app/main.py (always-on, both editions)"
  - "tests/test_local_auth.py — 11 real-Postgres integration tests proving SC#1/2/3/6, D-18-05, and the concurrent-duplicate 409"
affects: [18-04, 18-05, 18-06]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Single-commit multi-step register mirroring auth_github.py's signin structure, with teams_repo.create_team called DIRECT (never the self-committing HTTP route) to preserve the transaction boundary"
    - "Decoy-timed generic-401 login shape: absent/locked/wrong-password all call verify_decoy() then raise ONE shared literal message, closing both a status-code oracle and a timing oracle"
    - "Autouse pytest fixture resetting a process-wide in-memory rate-limiter between tests, to keep cross-test rate-limit budget sharing from becoming test-order-dependent flakiness"

key-files:
  created:
    - apps/memory-api/app/routes/auth_local.py
    - apps/memory-api/tests/test_local_auth.py
  modified:
    - apps/memory-api/app/main.py
    - .planning/phases/18-local-auth/deferred-items.md

key-decisions:
  - "register() calls teams_repo.create_team directly (never POST /v1/teams/self-solo) to keep the whole account-creation flow inside one transaction with one commit"
  - "local_credentials_repo.create() returning False (ON CONFLICT DO NOTHING) is treated identically to a caught IntegrityError — both map to 409, never a 500, for the concurrent-duplicate-registration race"
  - "login's absent-account and locked-account branches both call verify_decoy() and raise the exact same HTTPException(401, 'Invalid email or password.') instance text as the wrong-password branch — one shared string constant, no second code path that could drift"
  - "Rate limiter storage (module-level, process-wide by design per 18-02) is reset via an autouse pytest fixture in test_local_auth.py so the lockout/e2e tests' multi-request sequences never trip a shared budget exhausted by earlier tests in the same pytest process"

requirements-completed: [LAUTH-01, LAUTH-02]

# Metrics
duration: 30min
completed: 2026-07-14
---

# Phase 18 Plan 03: Register + Login Routes Summary

**POST /v1/auth/local/{register,login} — atomic single-commit account creation and decoy-timed lockout-enforced sign-in, both minting the same xbt_ token every other login path mints, proven against real Postgres with zero mocked principal.**

## Performance

- **Duration:** ~30 min
- **Started:** 2026-07-14T03:35:00Z (worktree base)
- **Completed:** 2026-07-14T04:05:17Z
- **Tasks:** 2/2 completed
- **Files modified:** 4 (2 created, 2 modified)

## Accomplishments
- `POST /v1/auth/local/register`: brand-new email -> `xbt_` token + solo team_scope in ONE transaction/ONE commit; existing email -> 409 with no token and no row; a concurrent-duplicate credential PK collision -> 409, never a 500.
- `POST /v1/auth/local/login`: correct password -> `xbt_`; absent email, wrong password, and a locked account all return a **byte-identical** generic 401, timing-equalized via `verify_decoy()`; N consecutive failures lock the account (DB-backed, durable), and the correct password recovers automatically once `locked_until` passes, resetting `failed_attempts` to 0.
- The register-minted `xbt_` authorizes a real team-scoped route (`GET /v1/tasks`) through the unmodified `get_current_principal` -> `get_team_scope` -> `team_members` path — proving SC#3/LAUTH-02 without a single `dependency_overrides` mock.
- Full zero-social-OAuth loop (register -> login -> authorized `GET /v1/tasks`) passes with `GOOGLE_CLIENT_ID` / `GITHUB_APP_CLIENT_ID` / `GITHUB_CLIENT_ID` all blanked (SC#1).
- `auth_local.router` classified CORE in `app/main.py` — ships in every edition, never SaaS-gated.
- `app/deps.py` and `app/routes/auth_github.py` are byte-for-byte untouched (verified by `git diff --name-only` at every commit and by a dedicated regression test).

## Task Commits

Each task was committed atomically:

1. **Task 1: register route (single-commit, 409 on collision incl. concurrent dup, xbt_ mint) + wire router CORE** - `de12465` (feat)
2. **Task 2: login route (decoy-timed generic 401, DB lockout, rehash-on-success) + enumeration + lockout + clean-boot tests** - `bc2a6d7` (feat)

**Plan metadata:** (this commit, made after this summary)

## Files Created/Modified
- `apps/memory-api/app/routes/auth_local.py` - `POST /auth/local/register` + `POST /auth/local/login`; single-commit register, decoy-timed lockout-enforced login
- `apps/memory-api/app/main.py` - `auth_local` imported and added to `CORE_ROUTERS`
- `apps/memory-api/tests/test_local_auth.py` - 11 real-Postgres integration tests (register, login, lockout, no-enumeration-oracle, SC#1 e2e, SC#4 safety)
- `.planning/phases/18-local-auth/deferred-items.md` - logged a pre-existing, unrelated `memory_promotions` table-name bug found while running the plan's own regression check

## Decisions Made
- `teams_repo.create_team` is called DIRECT inside `register()`'s transaction, never via `POST /v1/teams/self-solo` (that route commits internally per `teams.py:485` and would break the single-commit boundary required by D-18-05/T-18-03-05).
- `local_credentials_repo.create()`'s boolean return (rather than letting the DB raise) is the primary concurrent-duplicate signal; the `try/except IntegrityError` around the whole mutation block is belt-and-suspenders for any other residual race (e.g. on `source_user_id`).
- `login()` uses exactly one shared message constant (`_GENERIC_LOGIN_ERROR`) across the absent/locked/wrong-password branches — enforced structurally (one string, three call sites) rather than by convention, so a future edit can't accidentally introduce a second, differently-worded 401.
- Test-only: seeded rows that the register route's own `session.rollback()` must NOT disturb are seeded via a genuinely independent `create_async_engine(pg_url)` connection with a real commit, not the shared `session` fixture — see the docstring on `test_register_duplicate_credential_409` for why (the `session` fixture's `conn.begin()`/`trans.rollback()` wrapper has no savepoint, so a same-session `session.commit()` is not durable against a later `session.rollback()` on the same connection within the same test).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `grep -c '423'` acceptance check failed on a doc comment, not code**
- **Found during:** Task 2 verification
- **Issue:** The login route's inline comment said "never a 423 or a distinct message", which itself contains the literal substring `423` that the plan's acceptance grep (`grep -c '423' app/routes/auth_local.py` must return 0) matches against.
- **Fix:** Reworded the comment to convey the same intent ("never a distinct 'locked' status code or message") without the literal digits.
- **Files modified:** `apps/memory-api/app/routes/auth_local.py`
- **Committed in:** `bc2a6d7` (Task 2 commit)

**2. [Rule 1 - Bug] `grep -c 'self.solo'` acceptance check failed on a doc comment, not code**
- **Found during:** Task 1 verification
- **Issue:** The module docstring referenced "POST /v1/teams/self-solo route"; the plan's acceptance grep pattern `self.solo` (where `.` is a regex any-char) matches the literal substring "self-solo" too, so the check returned 1 instead of the required 0.
- **Fix:** Reworded to "the solo-team-bootstrap HTTP route" — same meaning, no matching substring.
- **Files modified:** `apps/memory-api/app/routes/auth_local.py`
- **Committed in:** `de12465` (Task 1 commit)

**3. [Rule 3 - Blocking] Process-wide rate limiter exhausted by earlier tests in the same pytest session**
- **Found during:** Task 2, `test_clean_boot_no_oauth_e2e`
- **Issue:** `app/services/rate_limit.py`'s `MemoryStorage`/limiter are module-level singletons shared by every test in the process (by design, documented in that module). By the time the e2e test ran, prior tests (especially the 8-request lockout cycle) had already consumed the `LOCAL_AUTH_RATE_LIMIT` (10/minute) budget for the shared `(bucket, client_ip)` key that `httpx.ASGITransport` gives every in-process test call, producing a spurious `429` unrelated to the scenario under test.
- **Fix:** Added an `autouse` pytest fixture (`_reset_rate_limiter`) that calls `rate_limit_module._storage.reset()` before every test in `test_local_auth.py`, isolating each test's rate-limit budget.
- **Files modified:** `apps/memory-api/tests/test_local_auth.py`
- **Committed in:** `bc2a6d7` (Task 2 commit)

**4. [Rule 1 - Bug] Test used an ORM object attribute after the route's own `session.rollback()` expired it**
- **Found during:** Task 1, `test_register_duplicate_credential_409` (first draft)
- **Issue:** The test read `racer.id` (an ORM attribute) AFTER calling the register route, but the shared `session`/connection between the test and the route means the route's `session.rollback()` (fired on the concurrent-duplicate 409 path) expired the identity-mapped `racer` object, producing `sqlalchemy.exc.MissingGreenlet` on the next lazy-load of `.id`.
- **Fix:** Captured `racer_id = racer.id` before issuing the request. This surfaced the deeper fixture-mechanics issue documented in deviation 5 below.
- **Files modified:** `apps/memory-api/tests/test_local_auth.py`
- **Committed in:** `de12465` (Task 1 commit)

**5. [Rule 1 - Bug] Same-session `session.commit()` seed data was wiped by the route's later `session.rollback()`**
- **Found during:** Task 1, `test_register_duplicate_credential_409`
- **Issue:** `tests/conftest.py`'s `session` fixture wraps the whole test in one connection-level transaction (`conn.begin()` / `trans.rollback()` at teardown) with **no savepoint**. Verified empirically (a throwaway debug test, discarded) that a mid-test `await session.commit()` is not durable to an independent connection — it is invisible to a totally separate `create_async_engine(pg_url)` connection — and a LATER `session.rollback()` on the SAME session (which the register route calls on the concurrent-duplicate-credential 409 path) discards that "committed" data too. Seeding the racer user + pre-existing credential row via the shared `session` fixture therefore could not survive to the post-request assertion.
- **Fix:** Seeded the racer + credential row via a genuinely independent `create_async_engine(pg_url)` session with a real commit and disposed engine, decoupled from the test's wrapped `session`/`client`. Verified the row via another independent connection after the request. This is a test-fixture-mechanics fix only — no production code changed, and the underlying route behavior (409, not 500, no second row) is exactly what the plan specifies.
- **Files modified:** `apps/memory-api/tests/test_local_auth.py`
- **Committed in:** `de12465` (Task 1 commit)

---

**Total deviations:** 5 auto-fixed (2 Rule 1 doc/grep wording, 1 Rule 3 test-isolation blocker, 2 Rule 1 test-mechanics bugs). **Zero deviations in production code behavior** — `app/routes/auth_local.py` implements exactly the single-commit register and decoy-timed lockout login shapes specified in the plan's `<interfaces>` block.
**Impact on plan:** All fixes are test-file or comment-wording adjustments needed to satisfy the plan's own acceptance criteria; no scope creep, no architectural change.

## Issues Encountered

**Pre-existing, out-of-scope test failure (logged, not fixed):** `pytest tests/test_phase10_auth.py` fails 7/7 with `sqlalchemy.exc.ProgrammingError: UndefinedTableError: relation "memory_promotions" does not exist`, raised from `app/repos/merge.py`'s `UPDATE memory_promotions ...` (the actual table, created by migration `0002_memory_promotions.py`, is named `promotions`, not `memory_promotions` — a naming-drift bug in `merge.py` unrelated to any Phase 18 file). Confirmed pre-existing by running `test_phase10_auth.py` alone, with none of this plan's files present in the session — it fails identically. `app/repos/merge.py` is not touched by this plan. Logged to `.planning/phases/18-local-auth/deferred-items.md` per the Scope Boundary rule; NOT fixed here. `tests/test_edition_gating.py` (the other half of the plan's regression check) passes cleanly, 13/13.

## User Setup Required

None - no external service configuration required. Local-auth register/login work out of the box on any Postgres-backed install, with zero OAuth configuration (SC#1).

## Next Phase Readiness

- `POST /v1/auth/local/{register,login}` are live and CORE-mounted; plan 18-04 (set-password / authenticated convergence) and 18-05/18-06 (UI surface) can build directly on the `xbt_` session this plan mints.
- `local_credentials.password_hash` upgrade path (`needs_rehash` + `update_hash`) is already wired into `login()`, so a future argon2 parameter bump auto-migrates on next successful sign-in with no batch job needed.
- Known limitation carried forward per D-18-07 (not this plan's job): the in-process rate limiter is per-worker-process, not durable across `uvicorn --workers 2` — documented in `app/services/rate_limit.py`, unchanged here.
- Blocker for a FUTURE, unrelated fix: `app/repos/merge.py`'s `memory_promotions` table-name bug (see Issues Encountered) breaks the GitHub-merge test suite; flagged in `deferred-items.md`, does not block this plan or Phase 18.

---
*Phase: 18-local-auth*
*Completed: 2026-07-14*

## Self-Check: PASSED

- FOUND: `apps/memory-api/app/routes/auth_local.py`
- FOUND: `apps/memory-api/tests/test_local_auth.py`
- FOUND: `.planning/phases/18-local-auth/deferred-items.md`
- FOUND commit `de12465` (Task 1)
- FOUND commit `bc2a6d7` (Task 2)
- Verified: `auth_local` present in `apps/memory-api/app/main.py` CORE_ROUTERS
- Verified: `pytest tests/test_local_auth.py -x` — 11 passed, 0 skipped
- Verified: `git diff --name-only` (both task commits) excludes `app/deps.py` and `app/routes/auth_github.py`
