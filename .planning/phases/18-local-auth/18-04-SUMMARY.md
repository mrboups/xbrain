---
phase: 18-local-auth
plan: 04
subsystem: auth
tags: [local-auth, argon2id, convergence, set-password, xbt, fastapi, postgres, testcontainers]

# Dependency graph
requires:
  - phase: 18-local-auth (plan 03)
    provides: "auth_local.py register/login routes, local_credentials repo (get_by_user_id/upsert/update_hash), password_hash service, mint_xbt_for_user"
provides:
  - "POST /v1/auth/local/set-password — authenticated attach-or-change, the ONLY safe convergence path (D-18-05)"
  - "_require_user_any() accepting kind in {user, user_api_token} without reusing me.py::_require_user"
  - "docs/local-auth-recovery.md — operator-level DB recovery runbook, explicitly no SMTP (SC#5)"
affects: [18-local-auth-ui, mcp-connector-local-auth, edition-gating]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Convergence via authenticated proof-of-ownership only: identity from the resolved principal, never the request body"
    - "Attach-vs-change branch keyed on whether a local_credentials row already exists (first-attach skips old-password; change requires it)"

key-files:
  created:
    - apps/memory-api/tests/test_local_auth_set_password.py
    - docs/local-auth-recovery.md
  modified:
    - apps/memory-api/app/routes/auth_local.py

key-decisions:
  - "Identity for set-password is taken exclusively from get_current_principal — SetPasswordBody has no user_id/email field, so horizontal-priv-escalation is structurally impossible, not just checked"
  - "Change (existing credential) requires the correct current password; a live session alone must never silently overwrite an existing password"
  - "_require_user_any accepts both kind=user AND kind=user_api_token (xbt_), deliberately NOT reusing me.py::_require_user which 403s xbt_ principals"
  - "Recovery is operator DB action (clear lockout / delete row to re-attach), never email/SMTP — this doc IS the SC#5 recovery story"

patterns-established:
  - "Attach-or-change single endpoint branching on row existence (D-18-05 / research OQ3)"
  - "Real-Postgres convergence proof: seed GitHub-style user + team + real xbt_, authenticate as kind=user_api_token, set-password, then local login succeeds"

requirements-completed: [LAUTH-01]

# Metrics
duration: ~55min
completed: 2026-07-14
---

# Phase 18 Plan 04: Authenticated set-password (safe convergence) + operator recovery docs

**POST /v1/auth/local/set-password — an authenticated attach-or-change endpoint that converges a GitHub/Google account to a working local login through proof-of-ownership (never cold register), plus an operator DB recovery runbook that replaces SMTP-based reset.**

## Performance

- **Duration:** ~55 min (across a mid-plan pause/resume)
- **Started:** 2026-07-13 (initial cut)
- **Completed:** 2026-07-14
- **Tasks:** 2
- **Files modified:** 3 (1 modified, 2 created)

## Accomplishments
- `POST /v1/auth/local/set-password` added to `auth_local.py`: authenticated (`Depends(get_current_principal)`), writes ONLY the caller's own `user_id`, branches first-attach (no old password) vs change (verify current password), single commit, returns `{"status": "ok"}`.
- `_require_user_any()` helper that accepts both `kind="user"` and `kind="user_api_token"` — the local-auth `xbt_` principal is a first-class caller, unlike under `me.py::_require_user`.
- 6 real-Postgres integration tests (testcontainers, no SKIPs) proving convergence, horizontal-priv-escalation blocked, change-requires-old-password, argon2id-not-plaintext, and unauthenticated rejection.
- `docs/local-auth-recovery.md`: operator recovery via psql (`UPDATE`/`DELETE` on `local_credentials`), explicit no-SMTP statement, and the D-18-07 known limitations (GitHub-only MCP connector + `kind=="user"` route gates).
- `deps.py` and `auth_github.py` confirmed untouched (SC#4).

## Task Commits

Each task was committed atomically (the mid-plan `wip(18-04)` placeholder was replaced via `git reset --soft` before these real commits):

1. **Task 1a: set-password route** - `2fb48dc` (feat)
2. **Task 1b: real-Postgres convergence tests** - `2e63052` (test)
3. **Task 2: operator recovery runbook** - `d1f9012` (docs)

_Note: this SUMMARY commit follows as the plan-metadata commit._

## Files Created/Modified
- `apps/memory-api/app/routes/auth_local.py` - Added `SetPasswordBody`, `_require_user_any()`, and the `set_password` route; extended the module docstring for the convergence contract.
- `apps/memory-api/tests/test_local_auth_set_password.py` - 6 integration tests against real Postgres.
- `docs/local-auth-recovery.md` - Operator recovery runbook (no SMTP), D-18-07 limitations.

## Decisions Made
- None beyond the plan — followed the `<interfaces>` shape exactly (attach-or-change, generic 400 for wrong/missing old password, single commit).
- Emphasis: the anti-escalation property is structural — `SetPasswordBody` carries no target identity at all, so there is no code path to address another user's row.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- The plan run was paused mid-execution before the first commit; the coordinator preserved the work-in-progress as `891f24a` ("wip(18-04)"). On resume it was un-committed via `git reset --soft 891f24a~1` and replaced with the three proper atomic commits above. No work was lost and no `wip(...)` commit remains in the final history.
- Git emitted the usual LF→CRLF warnings on Windows for the two new files; cosmetic only, tests unaffected.

## Verification Evidence
- `pytest tests/test_local_auth_set_password.py -x` → 6 passed, 0 skipped.
- `pytest tests/test_local_auth.py tests/test_local_auth_set_password.py` → 17 passed, 0 skipped (Plan 03's register/login suite still green — no regression).
- Security must-haves, each a passing real-Postgres test:
  - (a) horizontal-priv-escalation blocked — `test_cannot_set_another_users_password`;
  - (b) change requires correct current password — `test_change_password_wrong_old_password_rejected` + `test_change_password_missing_old_password_rejected` (hash byte-for-byte unchanged);
  - (c) convergence proven — `test_convergence_github_style_user_attaches_password` (authenticates as `kind=user_api_token`, ends with a successful local login);
  - (d) stored hash is `$argon2id$`, never plaintext — asserted in the convergence test.
- `git diff --name-only 7cc778f HEAD` → only `auth_local.py`, the new test, and the recovery doc; `deps.py`/`auth_github.py` absent.
- `docs/local-auth-recovery.md` present with `UPDATE local_credentials`/`DELETE FROM local_credentials` examples, explicit no-SMTP statement, and D-18-07 limitations.

## Known Stubs
None.

## User Setup Required
None - no external service configuration required. (Operator recovery is a documented DB action, not a build-time setup step.)

## Next Phase Readiness
- The convergence surface (D-18-05) is complete: register (409 on existing email), login, and now authenticated set-password. A GitHub/Google account can gain a local password through proof-of-ownership.
- Remaining Phase 18 work (per CONTEXT): the minimal auth UI surface. D-18-07 limitations (MCP connector local-auth branch, `kind=="user"` route gates) are documented and deliberately out of this plan's scope.

## Self-Check: PASSED

- Files: `apps/memory-api/tests/test_local_auth_set_password.py`, `docs/local-auth-recovery.md`, `.planning/phases/18-local-auth/18-04-SUMMARY.md` all present.
- Commits `2fb48dc`, `2e63052`, `d1f9012` all exist in git history.

---
*Phase: 18-local-auth*
*Completed: 2026-07-14*
