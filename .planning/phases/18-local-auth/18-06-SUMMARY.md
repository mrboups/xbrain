---
phase: 18-local-auth
plan: 06
subsystem: testing
tags: [pytest, junit-xml, docker, testcontainers, postgres, respx, acceptance-gate, bash]

# Dependency graph
requires:
  - phase: 18-local-auth (plans 01-05)
    provides: local_credentials table + repo, argon2id hashing, rate limiter, register/login/set-password routes (all CORE-mounted), the auth UI screens
provides:
  - "infrastructure/scripts/verify-phase18.sh — the Phase 18 acceptance gate: real-Postgres security suite (SKIP-as-FAIL), SC#4 regression (router classification + tolerant-of-known-pre-existing-breakage phase10 rerun + a gate-owned live GitHub-signin proof + a historical byte-diff), a live zero-OAuth boot harness (no image built), and a self-excluding repo-wide negative-provenance grep"
  - "make verify-phase18 target"
  - "app-site/docs/auth.html — Local (email + password) section documenting the native auth path, the three endpoints, convergence, and the no-SMTP recovery story"
  - "two newly-discovered, documented (not fixed) pre-existing bugs in tests/test_phase10_auth.py, logged to deferred-items.md"
affects: []

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "JUnit-XML-based pytest result parsing (python ElementTree) instead of text-grepping pytest output — real structured data, not a brittle string match"
    - "Tolerant regression check: compare the SET of failing test names against a documented pre-existing-broken set, so a gate can fail on genuinely NEW regressions without being permanently red over bugs it must not fix (Scope Boundary rule)"
    - "Gate-owned, ephemeral pytest file (written by the shell script into tests/, deleted by its EXIT trap) used to independently re-prove a behavior a stale, unrelated test file can no longer prove"

key-files:
  created:
    - infrastructure/scripts/verify-phase18.sh
  modified:
    - Makefile
    - app-site/docs/auth.html
    - .planning/phases/18-local-auth/deferred-items.md

key-decisions:
  - "docs/auth.html (the plan's stated path) does not exist; the actual existing auth doc the plan describes (\"the Phase 10/12 GitHub/Google auth doc\") lives at app-site/docs/auth.html. Extended that file instead (Rule 3 — missing referenced file), documented as a deviation."
  - "tests/test_phase10_auth.py is provably broken by THREE independent, pre-existing, Phase-18-unrelated bugs (not one, as 18-03 had logged) — confirmed by running it in isolation against the pre-Phase-18 base commit. Per the Scope Boundary rule this file (Phase 10 vintage) is not edited. Instead the gate's check (b2) is tolerant of exactly this documented failure set (any OTHER failing test name fails the gate), and check (b3) adds a gate-owned, correctly-shaped respx-mocked live exercise of the real /v1/auth/github/signin route as the genuine, independent SC#4 proof the stale file can no longer provide on its own."
  - "check (b4) diffs app/deps.py + app/routes/auth_github.py against the pre-Phase-18 base commit (100e6d9, the commit immediately before Plan 18-01's first commit) rather than merely 'no uncommitted changes' — a real structural proof spanning the whole phase, with a non-fatal fallback if that commit ever becomes unreachable."
  - "check (c)'s live-boot harness explicitly sets GOOGLE_CLIENT_ID/GITHUB_CLIENT_ID/GITHUB_CLIENT_SECRET/GITHUB_APP_CLIENT_ID/GITHUB_APP_CLIENT_SECRET to EMPTY STRINGS (not merely omitted) — a stronger, more explicit statement of SC#1's zero-OAuth claim."

requirements-completed: [LAUTH-01, LAUTH-02]

# Metrics
duration: ~55min
completed: 2026-07-14
---

# Phase 18 Plan 06: Acceptance Gate + Auth Docs Summary

**verify-phase18.sh — a 13-assertion acceptance gate proving SC#1..SC#6/LAUTH-01/02 against a REAL Postgres and a REAL live-booted memory-api (register→login→authorized GET /v1/tasks 200, zero social OAuth configured), plus the Local (email + password) section in app-site/docs/auth.html.**

## Performance

- **Duration:** ~55 min
- **Started:** 2026-07-14 (worktree base commit 4b15ded, 18-05 completion)
- **Completed:** 2026-07-14
- **Tasks:** 2/2 completed
- **Files modified:** 4 (1 created, 3 modified)

## Accomplishments

- `infrastructure/scripts/verify-phase18.sh`: a 4-check, 13-assertion gate (mirroring `verify-phase15.sh`'s PASS/FAIL/SKIP shape) that traverses the REAL deployment path throughout:
  - **(a) Security suite** — `test_local_auth.py` + `test_local_auth_set_password.py` + `test_local_credentials_repo.py` (27 tests) against real testcontainers Postgres, parsed via JUnit XML (not text-grepping pytest output). Docker-absent or ANY skip is scored a FAILURE, never a pass.
  - **(b) SC#4 regression** — `test_edition_gating.py` hard-gated green (router classification, no Docker needed); `test_phase10_auth.py` re-run and found to be provably broken by **three independent, pre-existing, Phase-18-unrelated bugs** (only one of which, `memory_promotions`, had been logged before — see Deviations); a **gate-owned, correctly-shaped respx-mocked live exercise of the real `POST /v1/auth/github/signin`** route (new here) proves SC#4 against current behavior where the stale fixture no longer can; `app/deps.py`/`app/routes/auth_github.py` diffed byte-for-byte against the pre-Phase-18 base commit (`100e6d9`).
  - **(c) Live zero-OAuth boot** — a real memory-api (no image built — `python:3.12-slim` + bind mount, same no-build harness `verify-phase15.sh` established) against a real `postgres:17` container, with `GOOGLE_CLIENT_ID`/`GITHUB_APP_CLIENT_ID`/`GITHUB_APP_CLIENT_SECRET`/`GITHUB_CLIENT_ID`/`GITHUB_CLIENT_SECRET` all **explicitly emptied**. Proves `register` → `login` → authorized `GET /v1/tasks` all return 200, live.
  - **(d) Negative provenance** — `deps.py`/`auth_github.py` contain zero `local_credentials`/`auth_local` marker strings; a **self-excluding repo-wide grep** proves the `/auth/local/*` endpoint surface appears only in its 7 known owner files (route, tests, account UI pages, docs) — never leaked into e.g. `oauth_authorize.py`.
  - Verified live end to end: **13/13 PASS, 0 SKIP, exit 0** on this worktree.
- `Makefile`: `make verify-phase18` target added, mirroring the `verify-phase15` pattern.
- `app-site/docs/auth.html`: new "Local (email + password)" section — zero-OAuth registration, the three endpoints, that local login mints the identical `xbt_` token GitHub/Google sign-in does, the authenticated-convergence story (D-18-05), and the no-SMTP operator recovery runbook (`docs/local-auth-recovery.md`, linked via GitHub). Existing GitHub/Google content preserved and re-flowed around the new section.
- `deferred-items.md`: logged two NEWLY discovered pre-existing bugs in `tests/test_phase10_auth.py` (stale `GITHUB_CLIENT_ID`/`SECRET` env-var names post-Phase-12 rename; a stale mocked token-exchange response missing `refresh_token`) — found while building the gate, confirmed pre-existing against the pre-Phase-18 base commit, not fixed (Scope Boundary rule).

## Task Commits

Each task was committed atomically:

1. **Task 1: verify-phase18.sh acceptance gate + make target** - `d7db374` (feat)
2. **Task 2: docs/auth.html — document the native email/password path** - `b5e1f30` (docs)

## Files Created/Modified

- `infrastructure/scripts/verify-phase18.sh` - The Phase 18 acceptance gate (checks a-d, 622 lines)
- `Makefile` - `verify-phase18` target
- `app-site/docs/auth.html` - New "Local (email + password)" section (129 insertions)
- `.planning/phases/18-local-auth/deferred-items.md` - Logged 2 newly-found pre-existing `test_phase10_auth.py` bugs

## Decisions Made

- **Path correction (Rule 3):** the plan's frontmatter/task named `docs/auth.html`, which does not exist anywhere in the repo. The file the plan actually describes ("the existing auth documentation to extend — the Phase 10/12 GitHub/Google auth doc") is `app-site/docs/auth.html` — confirmed by content (its meta description already says "How signing in with GitHub works on GrooveOS"). Extended that file; documented here rather than silently substituting.
- **`test_phase10_auth.py` is not forced green.** Running it in isolation (Docker present, zero Phase 18 files in the working tree, diffed against the pre-Phase-18 base commit `100e6d9`) reproduces **7/7 failing**, for THREE separate pre-existing causes: (1) `app/repos/merge.py` references a nonexistent `memory_promotions` table (actual name `promotions`) — already logged by 18-03; (2) the test's own `_github_oauth_env` fixture sets `GITHUB_CLIENT_ID`/`GITHUB_CLIENT_SECRET`, but `auth_github.py`'s actual gate is `GITHUB_APP_CLIENT_ID`/`GITHUB_APP_CLIENT_SECRET` (renamed by the Phase 12 GitHub App migration — the two var pairs now identify *different* OAuth Apps, per `config.py`'s own comment); (3) that same fixture's mocked token-exchange response is missing the `refresh_token` field `_exchange_code_for_token()` now requires. None of these are in files Phase 18 touches or should touch. Rather than edit a Phase 10 test file (out of scope), the gate's check (b2) is tolerant ONLY of this exact documented failure set — any OTHER failing test name fails the gate — and check (b3) adds an independent, gate-owned, correctly-shaped live proof of the same underlying claim (GitHub signin still works).
- **Historical git-diff over "no uncommitted changes."** `test_deps_and_auth_github_untouched` (18-03) only proves the working tree has no uncommitted edits to `deps.py`/`auth_github.py` — trivially true once everything is committed. Check (b4) instead diffs those two files against the actual pre-Phase-18 base commit, a real structural proof spanning all 6 plans.
- **Explicit-empty over omitted** for the live-boot harness's social-OAuth env vars, matching the plan's acceptance-criteria wording ("the live-boot env sets it... to empty") and making SC#1's claim unambiguous in the gate's own env file, not just implicit via pydantic-settings defaults.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Missing referenced file] `docs/auth.html` does not exist; extended `app-site/docs/auth.html` instead**
- **Found during:** Task 2 (read_first)
- **Issue:** The plan's frontmatter and Task 2 both name `docs/auth.html`. No such file exists anywhere in the repo (`docs/` only contains `.md` files). The file matching the plan's own description ("the existing auth documentation to extend — the Phase 10/12 GitHub/Google auth doc") is `app-site/docs/auth.html`.
- **Fix:** Extended `app-site/docs/auth.html` with the new section; left `docs/` untouched (no stray duplicate file created).
- **Files modified:** `app-site/docs/auth.html`
- **Committed in:** `b5e1f30` (Task 2 commit)

**2. [Rule 1 - Bug, discovered not fixed] Two more pre-existing bugs found in `tests/test_phase10_auth.py` while building check (b)**
- **Found during:** Task 1, building check (b2)'s tolerant regression logic
- **Issue:** Beyond the already-logged `memory_promotions` bug, 6 of the file's 7 tests fail with `503 GitHub App OAuth not configured` (stale `GITHUB_CLIENT_ID`/`SECRET` env-var names in the test fixture, post-Phase-12 rename) and, once worked around, a `400 GitHub token exchange missing required fields` (the mocked token-exchange response predates the Phase 12 requirement for a `refresh_token` field).
- **Scope boundary respected:** `tests/test_phase10_auth.py` is a Phase 10 file; NOT edited. Confirmed pre-existing by reproducing identically against the pre-Phase-18 base commit `100e6d9`.
- **Fix:** Logged to `deferred-items.md` (not fixed). Gate's check (b2) made tolerant of exactly this documented set; check (b3) adds an independent, gate-owned, correctly-shaped live proof of the same underlying claim.
- **Files modified:** `.planning/phases/18-local-auth/deferred-items.md` (docs only — no test/production code touched)
- **Committed in:** `d7db374` (Task 1 commit)

---

**Total deviations:** 2 (1 Rule 3 path correction, 1 Rule 1 discovery-and-log — no production or test code outside this plan's own scope was modified). **Impact on plan:** Both deviations were necessary to build a gate/doc that reflects real, current behavior rather than a plan-authoring typo or a false-green over pre-existing, out-of-scope bugs.

## Issues Encountered

None beyond the two deviations above — the gate script was iterated against real Docker/Postgres until it ran clean (13/13 PASS, 0 SKIP, exit 0) before being committed.

## Verification Evidence

- `bash infrastructure/scripts/verify-phase18.sh` — full live run: **PASS: 13 / 13 (SKIP: 0)**, exit 0.
- `bash -n infrastructure/scripts/verify-phase18.sh` — syntax OK.
- `grep -c 'PASS:' infrastructure/scripts/verify-phase18.sh` = 2, `grep -c 'exclude'` = 5, `grep -c 'docker build'` = 0, `grep -c 'GOOGLE_CLIENT_ID'` = 3, `grep -c 'verify-phase18' Makefile` = 2 — all plan acceptance criteria satisfied.
- Post-run: no leftover `xbrain-p18-*` containers, no stray `tests/test_zzverify_phase18_*` files — the gate's cleanup trap runs to completion on both success and failure paths (tested both during iteration).
- `app-site/docs/auth.html`: `grep -qi 'auth/local'` and `grep -qi 'github'` both match; `grep -ci 'email'` = 24, `grep -ci 'recovery\|reset'` = 5; HTML tag balance (h1/h2/h3/p/ul/ol/table/div) verified via a Python check — all matched.

## Known Stubs

None.

## User Setup Required

None — no external service configuration required. The gate is self-contained (spins its own throwaway Postgres + memory-api containers, uniquely `xbrain-p18-*` prefixed, never touching a real deployment's containers).

## Next Phase Readiness

- Phase 18 (Local Auth) is now fully shipped: data layer (18-01), crypto primitives (18-02), register/login (18-03), authenticated set-password + recovery docs (18-04), UI screens (18-05, browser verification deferred to Phase 16 per that plan's own summary), and this acceptance gate + operator-facing docs (18-06).
- `make verify-phase18` / `bash infrastructure/scripts/verify-phase18.sh` is ready for CI or pre-deploy use.
- Two newly-documented, NOT-fixed pre-existing bugs remain in `tests/test_phase10_auth.py` (env-var name drift + stale mock shape) — flagged in `deferred-items.md` for a dedicated fix outside Phase 18, alongside the already-known `memory_promotions` table-name bug in `app/repos/merge.py`.
- D-18-07's known limitations (MCP Custom-Connector is GitHub-only; some routes still gate strictly on `kind=="user"`) remain flagged, not fixed, exactly as scoped.

---
*Phase: 18-local-auth*
*Completed: 2026-07-14*

## Self-Check: PASSED

- FOUND: `apps/memory-api` verification — `infrastructure/scripts/verify-phase18.sh` exists and is executable via bash.
- FOUND: `Makefile` contains `verify-phase18` target.
- FOUND: `app-site/docs/auth.html` contains the new Local (email + password) section.
- FOUND: `.planning/phases/18-local-auth/deferred-items.md` updated with the 18-06 entry.
- FOUND commit `d7db374` (Task 1) in `git log --oneline --all`.
- FOUND commit `b5e1f30` (Task 2) in `git log --oneline --all`.
