---
phase: 25-team-join-by-code
plan: 03
subsystem: testing
tags: [pytest, testcontainers, postgres, asyncpg, alembic, security-gate, bearer-secret, invite-code, concurrency]

# Dependency graph
requires:
  - phase: 25-01
    provides: team_invite_codes model + migration 0027 + repo (generate_code/mint_code/get_by_hash/redeem_atomic/revoke_code/list_codes)
  - phase: 25-02
    provides: the 4 HTTP endpoints (mint/list/revoke invite-codes + POST /teams/join-by-code)
provides:
  - "tests/test_join_by_code_gate.py — the phase's binding acceptance proof: real-Postgres, non-mocked, mint->join->revoke + every guard + double-spend race + migration-both-editions"
  - "Executable proof of hash-at-rest, authZ, idempotency, no-oracle generic-404, team-isolation, revoked/expired/max-uses rejection, and the atomic-increment ceiling under true concurrency"
affects: [25-04, team-join-by-code, invite-code, join-code, bearer-secret, security-gate]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Real-PG route+repo security gate: committing-session override + committed seed via async_session_factory (mirror test_catch_me_up_gate.py) so the endpoint's own pooled connection sees the seeded rows"
    - "Concurrent double-spend proof: two independent async_session_factory sessions driving redeem_atomic via asyncio.gather — the row-lock serialization is proven, not asserted"
    - "Migration-both-editions probe: fresh PostgresContainer per edition + settings.EDITION patched directly + information_schema/pg_indexes assertions"

key-files:
  created:
    - apps/memory-api/tests/test_join_by_code_gate.py
  modified: []

key-decisions:
  - "Teardown clears audit_log rows by actor_user_id before deleting users (the mint/join/revoke endpoints write audit rows whose actor FK RESTRICTs the user delete) — Rule 3 blocking fix"
  - "Sequential max-uses ceiling proven in the HTTP gate (Task 1); the concurrent racing proof of the same ceiling is a dedicated repo-level test (Task 2), matching the plan's split"
  - "code_hash data_type asserted as text-or-varchar (migration declares TEXT) and the index asserted UNIQUE via pg_indexes.indexdef"

patterns-established:
  - "SKIP=FAIL discipline: integration-marked, Docker-gated — a wrong status/side-effect FAILS, only genuinely-absent Docker skips"
  - "No mock of the repo/DB on any security-bearing path — the gate would be meaningless otherwise"

requirements-completed: [JOINCODE-01]

# Metrics
duration: 22min
completed: 2026-07-19
---

# Phase 25 Plan 03: Join-by-Code Security Gate Summary

**Real-Postgres, non-mocked acceptance gate proving mint (sha256-at-rest + plaintext-once) -> join (member added to the code's team only + uses++) -> revoke, plus authZ 403s, idempotent no-op, uniform generic-404 no-oracle, team-A/team-B isolation, revoked/expired/max-uses rejection, the concurrent double-spend ceiling (uses stays 1), and migration 0027 clean under EDITION=oss AND saas.**

## Gate Run Outcome (SKIP=FAIL)

**Ran GREEN under Docker on this host (local dev, Docker Desktop up).** The gate actually executed against real Postgres 17 testcontainers — it did NOT environment-skip.

```
$ cd apps/memory-api && MSYS_NO_PATHCONV=1 python -m pytest tests/test_join_by_code_gate.py -q
....                                                                     [100%]
4 passed, 4 warnings in 34.17s
```

Real pytest passed-count: **4 passed / 0 failed / 0 skipped**. The four tests:

1. `test_join_by_code_gate` — the HTTP mint->join->revoke gate + every guard.
2. `test_double_spend_race_cannot_exceed_max_uses` — the concurrent atomic-increment ceiling.
3. `test_migration_0027_team_invite_codes_forward_only[oss]` — migration under EDITION=oss.
4. `test_migration_0027_team_invite_codes_forward_only[saas]` — migration under EDITION=saas.

Two spin-up cycles of fresh PostgresContainers (one per migration edition) plus the session-scoped `pg_url` container back the run — the ~34s wall-time is real container work, not a mock.

## Performance

- **Duration:** ~22 min
- **Started:** 2026-07-19T16:33:00Z (approx)
- **Completed:** 2026-07-19T16:55:31Z
- **Tasks:** 2
- **Files modified:** 1 (created)

## Accomplishments
- Proved **hash-at-rest**: mint returns an `xbi_` plaintext once; a DIRECT DB read confirms the stored `code_hash == sha256(plaintext)` and the plaintext is absent from every stored column.
- Proved **authZ**: a non-admin non-member gets 403 on mint, list, and revoke.
- Proved **list-no-hash**: list items carry `code_prefix`/`uses`/`role` but never `code_hash`/`code`.
- Proved **valid join + team-isolation**: the caller is added to the code's team only (decoy team-B never gains the member) and `uses` increments to 1.
- Proved **idempotency**: an already-member join is a 200 no-op with `uses` UNCHANGED.
- Proved **no-oracle**: garbage / revoked / expired / max-uses-reached codes ALL return the identical generic 404 (`"invalid or expired invite code"`), and no membership side-effect occurs.
- Proved the **concurrent double-spend ceiling**: two racing sessions redeem a `max_uses=1` code; EXACTLY ONE wins, `uses` ends at 1 (not 2), EXACTLY ONE membership is created.
- Proved **migration 0027 is edition-agnostic**: `team_invite_codes.code_hash` is NOT NULL (text) and `ix_team_invite_codes_code_hash` is UNIQUE, identically under oss AND saas — no schema fork; forward-only.

## Task Commits

Each task was committed atomically:

1. **Task 1: The HTTP gate (mint sha256-at-rest + authZ + list-no-hash + join + idempotency + no-oracle + team-isolation + revoked/expired/max-uses)** — `95a09c9` (test)
2. **Task 2: The double-spend race + migration 0027 under EDITION=oss AND saas** — `46a51b2` (test)

**Plan metadata:** committed separately with this SUMMARY.

## Files Created/Modified
- `apps/memory-api/tests/test_join_by_code_gate.py` (created, 679 lines) — three integration-marked groups: the real HTTP gate, the concurrent race, and the two parametrized migration probes.

## Decisions Made
- **audit_log teardown ordering:** the mint/join/revoke endpoints call `write_audit(actor_user_id=...)`, so `audit_log.actor_user_id` RESTRICTs deleting the seeded users. The `finally` block deletes teams (CASCADE removes members + codes), then audit_log rows by actor, then users — otherwise cleanup raised a ForeignKeyViolationError (this is teardown hygiene, not a product bug).
- **Sequential vs concurrent max-uses:** per the plan split, the sequential ceiling is proven inside the HTTP gate (Task 1, block I) and the concurrent racing ceiling is a dedicated repo-level test (Task 2) driving `redeem_atomic` directly through two independent sessions.
- **Column-type assertion:** `code_hash` data_type accepted as `text` or `character varying` (0027 declares TEXT) with `is_nullable == 'NO'`; the unique index is asserted via `pg_indexes.indexdef` containing `UNIQUE`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] audit_log FK blocked user teardown**
- **Found during:** Task 1 (the HTTP gate) — the gate assertions all passed, but the `finally` teardown's `DELETE FROM users` raised `ForeignKeyViolationError` on `audit_log_actor_user_id_fkey`.
- **Issue:** The real mint/join/revoke endpoints write `audit_log` rows referencing the seeded users; deleting teams does not cascade to `audit_log` (its `team_scope` is a string, and `actor_user_id` is a RESTRICT FK to `users`).
- **Fix:** Added `DELETE FROM audit_log WHERE actor_user_id = ANY(...)` between the team-delete and the user-delete in the teardown.
- **Files modified:** apps/memory-api/tests/test_join_by_code_gate.py
- **Verification:** Re-ran the gate — `1 passed`; then the full file — `4 passed`.
- **Committed in:** `95a09c9` (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking).
**Impact on plan:** Test-harness hygiene only — no product-code change, no scope creep. The security assertions were already green before the fix; the fix only lets the committed seed clean up.

## Issues Encountered
None beyond the teardown FK above. The concurrent race resolved deterministically to a single winner on every run (the `UPDATE ... WHERE uses < max_uses` row lock serializes the two redemptions, as designed in 25-01's `redeem_atomic`).

## Known Stubs
None — the gate is non-mocked on every security-bearing path (no `MagicMock`, no `unittest.mock`, no `monkeypatch.setattr` on the invite-code repo/DB). The real routes + repos run end-to-end against real Postgres.

## User Setup Required
None - no external service configuration required. (Docker must be running for the integration gate to execute; it was, and the suite ran green.)

## Next Phase Readiness
- JOINCODE-01 is now proven against real Postgres — the API + repo from 25-01/25-02 are gate-verified.
- Plan 25-04 (extension UI) runs in parallel on disjoint files (`chrome-extension/*`); this plan touched only the test file, so there is no merge overlap.
- STATE.md / ROADMAP.md intentionally NOT updated by this executor (parallel-wave constraint) — the orchestrator advances them.

## Self-Check: PASSED
- `apps/memory-api/tests/test_join_by_code_gate.py` — FOUND
- `.planning/phases/25-team-join-by-code/25-03-SUMMARY.md` — FOUND
- Commit `95a09c9` (Task 1) — FOUND
- Commit `46a51b2` (Task 2) — FOUND

---
*Phase: 25-team-join-by-code*
*Completed: 2026-07-19*
