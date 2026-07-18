---
phase: 17-ci-lockstep
plan: 01
subsystem: testing
tags: [alembic, testcontainers, postgres, migrations, edition, oss, saas, pytest]

# Dependency graph
requires:
  - phase: 15-oss-saas-split
    provides: "EDITION flag (oss|saas) on app.config.Settings; pro tier dropped (EDIT-03)"
provides:
  - "Real-Postgres, non-mocked REL-03 proof: `alembic upgrade head` runs green under EDITION=oss AND EDITION=saas"
  - "Forward-only guard: applied DB head == ScriptDirectory.get_current_head() (no downgrade, no hardcoded head)"
  - "Edition-agnostic guard: oss and saas reach the IDENTICAL head"
  - "Static guard: no alembic/versions/*.py branches schema on the EDITION flag"
affects: [17-03-ci-workflow, test-migrations-job, release-gating]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Function-scoped, one-container-per-edition testcontainer (distinct from conftest's session-scoped pg_url) so EDITION is switched before each upgrade"
    - "Switch edition by patching the app.config.settings SINGLETON directly, not os.environ — Settings() is built once at import and env.py reads that frozen singleton"
    - "Read the Alembic head dynamically via ScriptDirectory.get_current_head() — never hardcode a revision string"

key-files:
  created:
    - apps/memory-api/tests/test_migration_editions.py
  modified: []

key-decisions:
  - "Two editions only (oss, saas). No pro — config.py _validate_edition raises ValueError for it (EDIT-03). ROADMAP SC4's 'or pro' wording is stale and was deliberately NOT encoded."
  - "Self-contained test file rather than extending conftest.py::pg_url (session-scoped, shared by 20+ files, upgrades one container once)."
  - "Restore the settings singleton + os.environ in finally so this test never pollutes EDITION/DATABASE_URL for other session tests."

patterns-established:
  - "Singleton-patch-then-upgrade: mirror conftest.py ~82-96 to make a per-edition migration run REAL, not a config parse."
  - "Dynamic head assertion: applied DB head (SELECT version_num FROM alembic_version) == ScriptDirectory head."

requirements-completed: [REL-03]

# Metrics
duration: ~12min
completed: 2026-07-18
---

# Phase 17 Plan 01: Forward-Only Edition-Agnostic Migration Test Summary

**Real-Postgres testcontainer test that runs `alembic upgrade head` under EDITION=oss AND EDITION=saas (singleton-patched, not os.environ), asserts both reach the identical dynamically-read head forward-only, plus a static guard that no migration branches schema on the EDITION flag.**

## Performance

- **Duration:** ~12 min
- **Completed:** 2026-07-18
- **Tasks:** 2
- **Files modified:** 1 created

## Accomplishments
- Authored `apps/memory-api/tests/test_migration_editions.py` — the load-bearing, non-mocked REL-03 / SC4+SC5 proof.
- **Ran it for real** against Postgres 17 testcontainers: **4 passed in 22.44s**, both oss and saas green.
- Edition is switched by patching the `app.config.settings` singleton DIRECTLY (`app_config.settings.EDITION = edition`, `.DATABASE_URL = asyncpg_url`), mirroring conftest.py ~82-96 — the exact fix for the checker's blocker 2 (os.environ alone is inert once `Settings()` is built once at import; env.py re-reads the frozen singleton).
- Head asserted dynamically via `ScriptDirectory.get_current_head()` (today `0024_local_credentials`, appearing only in a docstring, never hardcoded as an expectation).
- Static guard confirms zero `alembic/versions/*.py` reference `EDITION` today — turns "true by omission" into "asserted" for any future migration.

## Real Test Output (Task 2 — the "RUNS for real" gate)

Command (from `apps/memory-api`):
```
python -m pytest -m integration tests/test_migration_editions.py -v
```

Result:
```
collected 4 items

tests/test_migration_editions.py::test_alembic_upgrade_head_per_edition[oss] PASSED  [ 25%]
tests/test_migration_editions.py::test_alembic_upgrade_head_per_edition[saas] PASSED [ 50%]
tests/test_migration_editions.py::test_all_editions_reach_same_head PASSED           [ 75%]
tests/test_migration_editions.py::test_no_migration_branches_on_edition PASSED       [100%]

======================= 4 passed, 9 warnings in 22.44s ========================
```

**Exact counts: 4 passed, 0 failed, 0 skipped.** Docker was present (Docker Desktop daemon up), so no SKIP — SKIP would have been a real failure signal in CI, not a pass. Four fresh Postgres containers were spun and torn down (2 for the parametrized per-edition test, 2 for the same-head test).

## Task Commits

Each task was committed atomically:

1. **Task 1: Author test_migration_editions.py** - `8b93010` (test)
2. **Task 2: Run it for real and record the result** - no file change (the authored file already carried the run; result recorded above and in this SUMMARY)

**Plan metadata:** committed with this SUMMARY (docs).

## Files Created/Modified
- `apps/memory-api/tests/test_migration_editions.py` — Parametrized EDITION=oss/saas real-Postgres alembic-upgrade-head test (singleton-patched, per-edition fresh container, dynamic head equality) + edition-agnostic same-head test + static EDITION-branch guard.

## Decisions Made
- **Two editions, no pro.** `Settings._validate_edition` allows only `{"oss","saas"}`; `pro` raises ValueError (EDIT-03, 2026-07-11). ROADMAP SC4's "or pro" is stale — not encoded as an executable expectation (this matches base commit 63bb48a's own ROADMAP fix).
- **Singleton patch is load-bearing.** Setting `os.environ["EDITION"]`/`["DATABASE_URL"]` alone would leave every run on the frozen default `oss` and migrate the wrong DB, because `settings = Settings()` is built once at import and `alembic/env.py` reads that same singleton (and re-reads `settings.DATABASE_URL`, overriding the Config's `sqlalchemy.url`). The helper patches the singleton directly. os.environ is also set for completeness but is not what switches the run.
- **Dynamic head, never `0024`.** Head read via `ScriptDirectory.get_current_head()` so the test does not go brittle when a later plan appends `0025_*`.
- **Restore-in-finally.** The helper saves and restores `settings.EDITION`/`.DATABASE_URL` and the env vars in `finally`, plus stops every container in `finally`, so this test leaves no residue for the shared session singleton and leaks no containers (mitigates T-17-01-01).

## Deviations from Plan

None - plan executed exactly as written. Both tasks completed; the test was authored to the plan's spec and runs green for real.

## Issues Encountered

- **Cosmetic pytest warning (not a failure):** the synchronous static-guard test `test_no_migration_branches_on_edition` inherits the module-level `pytestmark = [pytest.mark.integration, pytest.mark.asyncio]` (which the plan explicitly dictates and which mirrors `test_migration_0019.py`). pytest emits `PytestWarning: marked with '@pytest.mark.asyncio' but it is not an async function`. It does not affect the result (test PASSES) — `asyncio_mode=auto` in pytest.ini already drives the async tests, so the mark is functionally redundant for them. Kept the plan-specified module mark rather than deviate; the warning is harmless noise.
- **Env-var prerequisite:** a bare `python -c "import app.config"` fails without DATABASE_URL/OAUTH_* set, but under pytest `conftest.py`'s module-level `os.environ.setdefault(...)` runs first, so `import app.config` inside the helper resolves cleanly. No change needed.

## User Setup Required

None - no external service configuration required (throwaway `test/test` credentials on ephemeral localhost containers; no real secret referenced — T-17-01-02 accepted).

## Next Phase Readiness
- This exact file is ready to become the CI `test-migrations` job in Plan 17-03. In CI, Docker IS present, so the correct wiring must treat a SKIP as a job failure (SKIP != PASS).
- No blockers. Head is `0024_local_credentials` today; the test tracks it dynamically.

## Self-Check: PASSED

- FOUND: `apps/memory-api/tests/test_migration_editions.py`
- FOUND: `.planning/phases/17-ci-lockstep/17-01-SUMMARY.md`
- FOUND: commit `8b93010` (Task 1 test)

---
*Phase: 17-ci-lockstep*
*Completed: 2026-07-18*
