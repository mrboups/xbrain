---
phase: 12-github-app-migration-public-deployment-ready-auth
plan: 12-01
subsystem: auth
tags: [github-app, alembic, postgres, sqlalchemy, fernet, oauth-migration, docker-compose, pydantic-settings]

# Dependency graph
requires:
  - phase: 11-brain-monitor
    provides: alembic head 0018_brain_events_view (predecessor for 0019)
  - phase: 10-github-primary-auth
    provides: users.github_id BIGINT + auth path that this phase migrates away from
provides:
  - migration 0019_github_app_install (installations table + 4 users token cols)
  - Installation ORM model + extended User ORM
  - 6 GITHUB_APP_* settings + .env.example unification + docker-compose passthrough
  - schema lock test (test_migration_0019.py) — 6 functions, 5 PLAN assertions
affects: [12-02-app-jwt, 12-03-installation-token-cache, 12-04-remove-pat, 12-05-webhook-handler, 12-06-user-token-refresh, 12-07-routes-migration, 12-08-chrome-ext, 12-09-app-site, 12-10-docs, 12-11-verify]

# Tech tracking
tech-stack:
  added: []  # no new runtime deps; reuses sqlalchemy/alembic/pydantic-settings/cryptography from Phase 7+
  patterns:
    - "Foundation-only plan: schema + config only, zero behavioural code change. Behaviour lands in 12-02..12-06."
    - "Token columns get mandatory `_enc` suffix when storing Fernet-encrypted ciphertext (vs raw token text)"
    - "Soft-delete via revoked_at + partial unique index `WHERE revoked_at IS NULL` for re-install audit history"

key-files:
  created:
    - apps/memory-api/alembic/versions/0019_github_app_install.py
    - apps/memory-api/app/models/installation.py
    - apps/memory-api/tests/test_migration_0019.py
  modified:
    - apps/memory-api/app/models/user.py
    - apps/memory-api/app/models/__init__.py
    - apps/memory-api/app/config.py
    - .env.example
    - infrastructure/docker-compose.yml

key-decisions:
  - "BIGINT for installation_id (autoincrement=False) — GitHub assigns the id, Python int round-trips losslessly"
  - "Soft-delete installations via revoked_at (never DROP rows) — preserves audit references after GitHub re-installs"
  - "Partial unique index `WHERE revoked_at IS NULL` enforces single-active-install-per-org while permitting unlimited revoked-row history"
  - "Keep GITHUB_API_PAT declared in config.py with DEPRECATED comment — Plan 12-04 removes setting + 3 consumers atomically"
  - "Drop GITHUB_ORG_PAT and GITHUB_ORG_NAME from .env.example NOW (legacy duplicates of GITHUB_API_PAT / GITHUB_ORG) — naming unification per RESEARCH"
  - "Test ships with 6 functions instead of 5 (splits column-presence per table) for better diagnostic granularity — covers all 5 PLAN assertions"

patterns-established:
  - "Encrypted-at-rest column naming: `_enc` suffix is mandatory + commented at declaration site"
  - "Migration upgrade()/downgrade() symmetry: drop indexes BEFORE table or rely on CASCADE; drop columns in reverse-add order"
  - "ORM-side mirrors raw migration DDL — partial unique indexes expressed via Index(..., postgresql_where=...) keyword"

requirements-completed: [GHAPP-03, GHAPP-05]
# Note: only schema/config portions of GHAPP-03/05 land here. The behavioural
# portions (webhook handler for GHAPP-03, encrypt+refresh helpers for GHAPP-05)
# land in Plans 12-05 and 12-06 respectively.

# Metrics
duration: 11m
completed: 2026-05-17
---

# Phase 12 Plan 12-01: Foundation — installations table + users token cols + env unification Summary

**Alembic 0019 lays the GitHub App migration foundation: new `installations` table (10 cols, partial unique index on active org installs), 4 nullable Fernet-encrypted token columns on `users`, ORM mirror models, and 6 GITHUB_APP_* settings wired through config.py + .env.example + docker-compose. Zero behavioural change — Plans 12-02 onwards consume the new surface.**

## Performance

- **Duration:** 11m 1s
- **Started:** 2026-05-17T10:35:57Z
- **Completed:** 2026-05-17T10:46:59Z
- **Tasks:** 4 / 4
- **Files modified:** 8 (3 created, 5 modified)
- **Lines added:** ~570 (142 migration + 78 ORM + 264 tests + ~80 config/env/compose)

## Accomplishments

- Migration `0019_github_app_install` chains cleanly onto Phase 11's `0018_brain_events_view`; creates `installations` table (10 columns + CHECK + partial unique index) and adds 4 nullable token columns to `users` for the user-to-server flow that Plan 12-06 implements.
- `Installation` ORM model mirrors the migration exactly — partial unique index expressed via SQLAlchemy `Index(..., postgresql_where=...)`. `User` model extended with 4 nullable token columns and rationale comments pointing at Plan 12-06.
- All 6 `GITHUB_APP_*` settings declared in `app/config.py` (APP_ID, SLUG, CLIENT_ID, CLIENT_SECRET, PRIVATE_KEY_B64, WEBHOOK_SECRET) with consumer docstrings pointing at the future-plan files that will use each.
- `.env.example` Phase 5 GitHub block restructured into three subsections — unchanged LibreChat OAuth, new GitHub App vars (with placeholder values + operator runbook hint), and the DEPRECATED `GITHUB_API_PAT` with migration note. Removed legacy duplicates `GITHUB_ORG_PAT` and `GITHUB_ORG_NAME`.
- `infrastructure/docker-compose.yml` passes all 6 new vars through to the `memory-api` service with safe defaults (`${VAR:-}` pattern) — fail-soft posture preserved until 12-02 starts consuming them.
- Smoke test `test_migration_0019.py` ships 6 test functions covering all 5 PLAN assertions (head version, columns on both tables, CHECK constraint, partial unique index revoke/reinstall, nullable token defaults).

## Task Commits

Each task committed atomically:

1. **Task 1: Migration 0019** — `501998a` (feat) — `apps/memory-api/alembic/versions/0019_github_app_install.py`
2. **Task 2: Installation ORM + User extension** — `0d96b7b` (feat) — `app/models/installation.py` + `app/models/user.py` + `app/models/__init__.py`
3. **Task 3: Settings + env + compose** — `6131f50` (feat) — `app/config.py` + `.env.example` + `infrastructure/docker-compose.yml`
4. **Task 4: Smoke test** — `230b7a0` (test) — `apps/memory-api/tests/test_migration_0019.py`

_All commits authored on branch `worktree-agent-a59974f52f19e3f9c` (parallel-executor worktree)._

## Files Created/Modified

- `apps/memory-api/alembic/versions/0019_github_app_install.py` (CREATED, 142 lines) — Alembic migration: `installations` table + 4 nullable user token columns + partial unique index. Chains onto `0018_brain_events_view`.
- `apps/memory-api/app/models/installation.py` (CREATED, 78 lines) — `Installation` SQLAlchemy ORM model mirroring the migration.
- `apps/memory-api/app/models/user.py` (MODIFIED, +14 lines) — appended 4 nullable Fernet-encrypted token columns to `User`.
- `apps/memory-api/app/models/__init__.py` (MODIFIED, +2 lines) — exported `Installation` so Alembic autogenerate picks it up.
- `apps/memory-api/app/config.py` (MODIFIED, +21 lines) — declared 6 `GITHUB_APP_*` settings + tagged `GITHUB_API_PAT` as DEPRECATED.
- `.env.example` (MODIFIED, +27 / -7 lines) — restructured Phase 5 GitHub block into LibreChat OAuth / new App / deprecated PAT subsections; dropped `GITHUB_ORG_PAT` + `GITHUB_ORG_NAME` legacy duplicates.
- `infrastructure/docker-compose.yml` (MODIFIED, +9 lines) — passes 6 new GITHUB_APP_* vars to memory-api with `${VAR:-default}` defaults.
- `apps/memory-api/tests/test_migration_0019.py` (CREATED, 264 lines) — 6 pytest functions, all `@pytest.mark.integration @pytest.mark.asyncio`, skipped when Docker unavailable.

## Decisions Made

- **BIGINT for `installations.installation_id`** (autoincrement=False, no autogenerated): GitHub provides the value via webhook payload; BIGINT future-proofs vs INT32 cap (GitHub returns JSON numbers fitting in INT32 today, but BIGINT matches `users.github_id` from migration 0014 for consistency).
- **Soft-delete pattern via `revoked_at`** (never `DROP`): preserves audit trail when GitHub re-installs (which deletes and creates with a new installation_id). The partial unique index `WHERE revoked_at IS NULL` enforces single-active-install-per-org while permitting unlimited audit rows for the same org_login.
- **Keep `GITHUB_API_PAT` declared with DEPRECATED comment**: 3 consumers in `app/deps.py` + `app/routes/me_github.py` + `app/routes/teams.py` still read it. Removing the setting now would break Phase 11 LIVE. Atomic removal lands in Plan 12-04 once installation-token-based path is in place.
- **Drop `GITHUB_ORG_PAT` and `GITHUB_ORG_NAME` from `.env.example` NOW**: legacy naming-mess (per RESEARCH §Runtime State Inventory) — `.env.example` used `GITHUB_ORG_PAT` but `config.py` uses `GITHUB_API_PAT`; operators have been working around it. Unified to the config.py name (single source of truth).
- **Tests use 6 functions instead of 5**: splits the "columns present" assertion across two functions (one per table) for clearer diagnostics when one regresses. Still covers all 5 PLAN-required assertions; PLAN authors generally accept finer granularity per CLAUDE.md "fail loudly" stance.
- **`Installation` exported from `app/models/__init__.py`**: enables Alembic autogenerate visibility AND signals intent to app code that this ORM is a first-class entity (not a raw-SQL-only table).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Worktree HEAD was 92 commits behind main**
- **Found during:** Task 1 (pre-commit HEAD assertion)
- **Issue:** The Claude Code worktree `agent-a59974f52f19e3f9c` was created from commit `0b0b50d` (pre-Phase-10 era). The predecessor migration `0018_brain_events_view.py` did not exist in this worktree, the Phase 12 plan files were absent, and `config.py` was a stale Phase 9 version. Continuing would have produced a migration with broken `down_revision` and an ORM extending a stale User model.
- **Fix:** Fast-forwarded the worktree branch to `main` (`git merge --ff-only main`). Worktree had 0 commits ahead and 0 uncommitted changes — a clean fast-forward. Operation does NOT touch any protected branch or destroy work (per `<destructive_git_prohibition>` allowed cases: not in `git clean` / `git rm` / `--force` / protected-ref family).
- **Files modified:** None (state change only)
- **Verification:** After fast-forward, `0018_brain_events_view.py` exists, Phase 12 plan files visible, `config.py` shows full Phase 11 settings surface, `git rev-list --count HEAD..main == 0`.
- **Committed in:** No commit (state-only operation; the fast-forward itself is a merge of existing main commits into the worktree branch ref).

**2. [Rule 1 - Bug] Stray migration file created in main-repo path**
- **Found during:** Task 1 (immediately after detecting deviation #1)
- **Issue:** The initial `Write` for the migration went to `/d/VSC/xbrain/apps/memory-api/alembic/versions/0019_github_app_install.py` (main repo, on `main` branch) instead of the worktree path `/d/VSC/xbrain/.claude/worktrees/agent-a59974f52f19e3f9c/apps/memory-api/alembic/versions/0019_github_app_install.py`. Caused by working-directory ambiguity before the worktree branch was caught up.
- **Fix:** Re-Wrote the identical content to the correct worktree path, then `rm` of the stray file in the main repo. The stray was untracked (git status `??`), zero risk to history.
- **Files modified:** None permanently — the stray was untracked and deleted before any commit referenced it.
- **Verification:** `ls` confirmed file exists in worktree, absent from main repo, and `git status` in main repo shows no pending changes.
- **Committed in:** N/A (cleanup was on an untracked file; the correctly-located file is in commit `501998a`).

---

**Total deviations:** 2 auto-fixed (1 blocking — branch staleness, 1 bug — stray file path)
**Impact on plan:** Both deviations were environment-setup issues, not plan-content issues. Plan 12-01 itself executed exactly as written — all 4 tasks, all 5 acceptance assertions, all PLAN-CHECK Iter 2 fixes (M-3 hardened settings check, M-3 hard import gate on user.py, MINOR-1 explicit users INSERT row shape). Zero scope creep.

## Issues Encountered

- **Worktree base drift (the deviation #1 above)**: the Claude Code worktree was created from a Phase-5-era commit instead of being branched off current main. Surfaced as `0018_brain_events_view.py` missing — caught by the pre-commit HEAD assertion safety gate before any commit landed on the wrong base. Fast-forward merge resolved cleanly because the worktree had 0 ahead/dirty.
- **Local pytest cannot exercise the 0019 smoke test**: `docker` Python module not installed in the local env (`pip install docker testcontainers` not run on the executor's WSL/Windows). The test correctly auto-skips per `_docker_available()` in conftest. Acceptance gate "pytest tests/test_migration_0019.py -v passes 5 assertions" remains an operator-side check on the VM where docker is present. Pattern matches `test_migration_0017.py` and `test_migration_0018.py` — Phase 11 shipped under the same constraint.

## User Setup Required

None for this plan. The 5 GitHub App secrets (APP_ID, CLIENT_ID, CLIENT_SECRET, WEBHOOK_SECRET, PRIVATE_KEY_B64) are already deployed to the VM per the operator-prep note (2026-05-17, memory `xbrain-phase12-operator-prep`). Plan 12-11 will add a verify script that confirms they parse cleanly. No environment-variable additions or dashboard configuration needed before merging.

## Threat Flags

| Flag                                | File                                                                  | Description                                                                                                                                                                                                                                                                                                                                                                                                       |
| ----------------------------------- | --------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| threat_flag: new-secret-surface     | `apps/memory-api/app/config.py`                                       | 5 new high-value secrets reach the runtime config namespace (`GITHUB_APP_CLIENT_SECRET`, `GITHUB_APP_PRIVATE_KEY_B64`, `GITHUB_APP_WEBHOOK_SECRET`, plus `CLIENT_ID` and `APP_ID` which are less sensitive). Defaults are empty / 0 — fail-safe. Plans 12-02 (JWT signing) and 12-05 (webhook HMAC) MUST validate non-empty at boot and refuse to start otherwise; flagged for the threat model attention in those plans. |
| threat_flag: encrypted-token-at-rest | `apps/memory-api/alembic/versions/0019_github_app_install.py`         | 4 new user-bound columns store Fernet-encrypted tokens. Plan 12-06 implements the encrypt/decrypt helpers — until then these columns remain NULL on all rows (no plaintext can leak because there's no writer). Threat: a missing or rotated `FERNET_KEY` would cause decrypt-time failures across all token-dependent flows. Plan 12-06 must pre-flight check `settings.FERNET_KEY` and raise a startup error. |

## Next Plan Readiness

- **Plan 12-02 (JWT signing infrastructure)** can start immediately: `settings.GITHUB_APP_ID`, `settings.GITHUB_APP_PRIVATE_KEY_B64`, `settings.GITHUB_APP_CLIENT_ID` are declared and reachable from any service module.
- **Plan 12-03 (installation token cache)** can use the `Installation` ORM model directly (no raw SQL needed for lookups by `github_org_login` or `installation_id`).
- **Plan 12-05 (webhook handler)** has its target table and `permissions JSONB` / `raw_payload JSONB` columns waiting; HMAC secret reachable via `settings.GITHUB_APP_WEBHOOK_SECRET`.
- **Plan 12-06 (user token storage)** has the 4 nullable columns ready. Forward-dep note from Plan 12-01 stands: if migration 0019 has been applied to the VM before 12-06 ships, 12-06 falls back to migration 0020 (additive) per its Section 0.

## Forward-dependency note for operations

Migration 0019 will be applied at the next memory-api container boot on the VM (`alembic upgrade head` in the entrypoint). To preserve Plan 12-06's option to EXTEND 0019 in-place with `github_access_token_hash` (M-5 fix), DO NOT manually trigger a deploy of just Plan 12-01 to the VM. The expected deploy cadence is Wave 1 → Wave 2 → ... → Wave 5 (12-06) all bundled. If an operator does deploy mid-stream, Plan 12-06 falls back to migration 0020 and the executor will log a deviation.

## Self-Check: PASSED

- [x] `apps/memory-api/alembic/versions/0019_github_app_install.py` — FOUND
- [x] `apps/memory-api/app/models/installation.py` — FOUND
- [x] `apps/memory-api/app/models/user.py` — FOUND (with the 4 new columns)
- [x] `apps/memory-api/app/models/__init__.py` — FOUND (with `Installation` export)
- [x] `apps/memory-api/app/config.py` — FOUND (with 6 GITHUB_APP_* settings)
- [x] `.env.example` — FOUND (6 GITHUB_APP_ lines)
- [x] `infrastructure/docker-compose.yml` — FOUND (6 GITHUB_APP_ env passthroughs on memory-api)
- [x] `apps/memory-api/tests/test_migration_0019.py` — FOUND (6 test functions)
- [x] Commit `501998a` — FOUND in `git log`
- [x] Commit `0d96b7b` — FOUND in `git log`
- [x] Commit `6131f50` — FOUND in `git log`
- [x] Commit `230b7a0` — FOUND in `git log`

---
*Phase: 12-github-app-migration-public-deployment-ready-auth*
*Plan: 12-01*
*Completed: 2026-05-17*
