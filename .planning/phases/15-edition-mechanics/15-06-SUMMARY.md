---
phase: 15-edition-mechanics
plan: 06
subsystem: api
tags: [fastapi, dependency-injection, authz, team-scope, pytest, testcontainers]

# Dependency graph
requires:
  - phase: 15-edition-mechanics
    provides: "15-02's out-of-scope finding — require_paid_tier live-blocking every /v1/crm/* and /v1/tasks/* call on every starter-plan (i.e. every self-hosted) team"
provides:
  - "app.deps no longer defines require_paid_tier — the cancelled paid-tier gate is fully removed from the product"
  - "all 10 /v1/crm/* and /v1/tasks/* endpoints depend directly on get_team_scope — same value, same source, minus the plan lookup"
  - "tests/test_no_paywall.py — paired starter-team-served / non-member-still-blocked contract, integration-tested against real Postgres"
affects: []

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Injected-regression proof for authz-widening changes: temporarily restore the removed gate on one call site, confirm the new test fails for the expected reason, then revert and confirm a byte-for-byte diff back to the prior commit before trusting the test."

key-files:
  created:
    - apps/memory-api/tests/test_no_paywall.py
  modified:
    - apps/memory-api/app/deps.py
    - apps/memory-api/app/routes/crm.py
    - apps/memory-api/app/routes/tasks.py

key-decisions:
  - "Substituted Depends(require_paid_tier) -> Depends(get_team_scope) directly at all 10 call sites rather than leaving require_paid_tier as a stub or wrapper — the plan explicitly rejected a no-op stub as 'an invitation for someone to re-wire it later'."
  - "Did not touch migration 0008_team_plan.py, the plan column, or the teams_plan_check constraint — confirmed via git diff --name-only after both commits. A migration is an immutable record; the hosted control plane may bill on that column later."
  - "Read team_a's plan column via raw SQL (SELECT plan FROM teams WHERE slug = :slug) in the test rather than the ORM Team model — the Team ORM class (app/models/team.py) never mapped the plan column in the first place; require_paid_tier itself always went through raw SQL for the same reason."

requirements-completed: [EDIT-02]

# Metrics
duration: ~32min
completed: 2026-07-13
---

# Phase 15 Plan 06: Remove the CRM/Tasks Paywall Summary

**Deleted `require_paid_tier` (the cancelled paid-tier gate that 403'd every `/v1/crm/*` and `/v1/tasks/*` call for every schema-default `starter`-plan team, i.e. every self-hosted install) from all 10 call sites, replacing it with the `get_team_scope` dependency it already wrapped — proven safe with a paired starter-team-served / non-member-still-blocked test and a verified injected-regression failure.**

## Performance

- **Duration:** ~32 min
- **Started:** 2026-07-13T22:40:00+02:00
- **Completed:** 2026-07-13T23:12:00+02:00
- **Tasks:** 2 (both completed)
- **Files modified:** 4 (1 created, 3 modified)

## Accomplishments

- `require_paid_tier` deleted entirely from `app/deps.py` — function body, docstring, and its stale "Used for /v1/crm/* and /v1/tasks/* (D2)" pointer to the cancelled paid-tier design.
- All 10 call sites (5 in `crm.py`, 5 in `tasks.py`) switched from `Depends(require_paid_tier)` to `Depends(get_team_scope)` — the exact same return value from the exact same underlying dependency, minus the plan lookup. `git diff` on both route files shows only the dependency name changing on each line; zero parameters added or removed.
- `tests/test_no_paywall.py` added: `test_starter_team_can_use_crm_and_tasks` reads the team's `plan` column directly (proves the schema DEFAULT, doesn't set it) and asserts non-403/200 from both routers; `test_non_member_still_blocked_on_crm_and_tasks` proves team-scope isolation is untouched — a non-member of the target team still gets 403 from both routers.
- Injected-regression proof executed and recorded verbatim (see below): temporarily restoring `Depends(require_paid_tier)` on `list_contacts` made `test_starter_team_can_use_crm_and_tasks` fail with the exact original 403 payload, confirming the test actually exercises the gate. Reverted cleanly — `git diff` against the Task 1 commit was empty after the revert.
- Full memory-api suite run before and after the change: **57 failed / 359 passed** (baseline) → **57 failed / 361 passed** (after). The sorted `FAILED` line lists are byte-for-byte identical (`diff` exit code 0) between the two runs; the +2 passed are exactly the two new tests. Zero skips in either run.

## Task Commits

Each task was committed atomically:

1. **Task 1: remove the plan gate from the 10 endpoints, keeping the membership check intact** — `252d866` (fix)
2. **Task 2: prove a starter team is served, and prove another team still is not** — `3b31a0b` (test)

_No TDD gate applies — plan type is `execute`, not `tdd`._

## Injected-regression proof (verbatim)

Temporarily restored `require_paid_tier` in `app/deps.py` and re-wired only `list_contacts` in `crm.py` to `Depends(require_paid_tier)`, then re-ran the new test in isolation:

```
tests/test_no_paywall.py::test_starter_team_can_use_crm_and_tasks FAILED

AssertionError: starter team got 403 from /v1/crm/contacts — paywall gate
still active: {"detail":"CRM and task tracking require a Team or
Enterprise plan"}
assert 403 != 403
 +  where 403 = <Response [403 Forbidden]>.status_code
```

Reverted both files immediately after. `git diff` against the Task 1 commit (`252d866`) was empty — the revert was exact, not approximate. Re-ran `tests/test_no_paywall.py` afterward: both tests passed again (29.95s, 0 skipped).

## Full-suite delta (required by the plan's success criteria)

| | Failed | Passed | Skipped |
|---|---|---|---|
| Baseline (before this plan's edits) | 57 | 359 | 0 |
| After this plan's edits | 57 | 361 | 0 |

`diff` on the sorted `FAILED ...` line lists between the two runs: **exit code 0 (identical)**. The 57 pre-existing failures span `test_admin_brain.py`, `test_admin_wipe.py`, `test_brain_events_list.py`, `test_external_sessions.py`, `test_github_sync.py`, `test_media.py`, `test_migration_0019.py`, `test_phase10_auth.py`, `test_phase10_repos.py`, `test_phase12_auto_grant_regression.py`, `test_phase12_org_membership.py`, `test_phase12_refresh_token.py`, `test_phase12_webhook.py`, `test_soft_delete_regression.py`, `test_team_context_cache.py` — none of these files were touched by this plan, matching the environment heads-up (phases 7–12 code, ~56 across ~14 files; this repo currently has 57 across 15). This plan added **zero** new failures.

## Files Created/Modified

- `apps/memory-api/app/deps.py` — deleted `require_paid_tier` (function + docstring), 21 lines removed
- `apps/memory-api/app/routes/crm.py` — import swap + 5 `Depends(require_paid_tier)` → `Depends(get_team_scope)`
- `apps/memory-api/app/routes/tasks.py` — import swap + 5 `Depends(require_paid_tier)` → `Depends(get_team_scope)`
- `apps/memory-api/tests/test_no_paywall.py` — new, 2 integration tests (starter-team-served, non-member-still-blocked)

## Decisions Made

- See `key-decisions` in frontmatter.

## Deviations from Plan

None — plan executed exactly as written. Pre-change usage counts (5+5) matched the plan's stated expectation exactly, so no adjustment was needed there either.

## Issues Encountered

None. The Team ORM model not mapping the `plan` column (confirmed by reading `app/models/team.py`) was anticipated by using raw SQL to read it in the test, matching `require_paid_tier`'s own original implementation — not a blocker, just a design constraint carried over correctly.

## Known Stubs

None — no UI/data stubs introduced by this plan.

## Threat Flags

None. This plan removes an authorization check (the widening the plan frontmatter explicitly calls out) but does not introduce any new network endpoint, auth path, or schema change. The removed check's counterpart (`get_team_scope` — authentication + team membership) was verified intact by both the diff inspection and `test_non_member_still_blocked_on_crm_and_tasks`.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- Resolves the "out-of-scope finding" flagged in `15-02-SUMMARY.md`: `require_paid_tier` no longer exists anywhere in `apps/memory-api/app/`.
- No other Phase 15 plan (15-03 docker-compose/.env.example, 15-05 memory-api `main.py`/`neo4j_client.py`) touches `deps.py`, `crm.py`, or `tasks.py` — confirmed no file overlap by staying strictly inside this plan's declared `files_modified`.
- The 57 pre-existing, unrelated test failures documented above (phases 7–12 code) remain untriaged — same recommendation as 15-02's summary: a dedicated audit/cleanup pass, disproportionate to any single Phase 15 plan's scope.

---
*Phase: 15-edition-mechanics*
*Completed: 2026-07-13*

## Self-Check: PASSED

All 4 created/modified source files, `tests/test_no_paywall.py`, and this SUMMARY.md confirmed present
on disk. Both task commit hashes (`252d866`, `3b31a0b`) confirmed present in `git log --oneline --all`.
