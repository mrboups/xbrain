---
phase: 15-edition-mechanics
plan: 02
subsystem: api
tags: [fastapi, edition-flag, router-gating, neo4j, outbox, pydantic-settings, pytest, testcontainers]

# Dependency graph
requires:
  - phase: 14-portability-foundation
    provides: the fail-fast field_validator pattern on Settings (OAUTH_ISSUER_URL precedent), copied for EDITION
provides:
  - "Settings.EDITION (oss default / saas, fails fast on anything else incl. 'pro')"
  - "app.main.create_app(edition) factory + CORE_ROUTERS (33) / SAAS_ONLY_ROUTERS (2) explicit registry"
  - "app.routes.memory.upsert_item's neo4j_outbox INSERT gated on get_driver() is not None"
  - "tests/test_edition_gating.py — the oss/saas negative-case + unclassified-router trap"
  - "tests/test_outbox_neo4j_guard.py — proof against real Postgres that the outbox guard fires correctly"
  - "working Docker/testcontainers integration-test path in this dev environment (was previously silently skipped end-to-end)"
affects: [15-03, 15-04, 15-05]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Two-list explicit router registry (CORE_ROUTERS / SAAS_ONLY_ROUTERS) + create_app(edition) factory, gated by a validated Settings.EDITION flag"
    - "Gate on the LIVE runtime signal (get_driver()), not on static config presence, when config can be truthy without the backing service being reachable"
    - "Lazy (per-test) imports of app.config/app.main/app.db.session in test files — required to avoid freezing module-level Settings/engine singletons on the wrong DATABASE_URL during pytest collection"

key-files:
  created:
    - apps/memory-api/tests/test_edition_gating.py
    - apps/memory-api/tests/test_outbox_neo4j_guard.py
  modified:
    - apps/memory-api/app/config.py
    - apps/memory-api/app/main.py
    - apps/memory-api/app/routes/crm.py
    - apps/memory-api/app/routes/tasks.py
    - apps/memory-api/alembic/versions/0008_team_plan.py
    - apps/memory-api/app/routes/memory.py
    - apps/memory-api/tests/conftest.py

key-decisions:
  - "EDITION gate is on Settings, validated with the Phase-14 field_validator pattern; unknown values (including 'pro') raise at construction time — no silent fallback to oss."
  - "Router gating is additive/explicit: CORE_ROUTERS (33, always mounted) + SAAS_ONLY_ROUTERS (2: waitlist, external_sessions, mounted only under EDITION=saas) — a router classified nowhere fails test_every_router_module_is_classified instead of shipping silently."
  - "neo4j_outbox guard checks app.neo4j_client.get_driver() is not None — the exact signal outbox_worker.drain_outbox() reads — NOT settings.NEO4J_URI/NEO4J_PASSWORD, which are both truthy in the real default OSS-light install (compose passes a bare-literal NEO4J_URI; .env.example ships a fill-me NEO4J_PASSWORD) and would never discriminate."
  - "COMPOSE_PROFILES=saas requires EDITION=saas — session-bridge POSTs /v1/me/external-sessions and would get a silent 404 under EDITION=oss otherwise. Not enforced here (memory-api must not learn about compose profiles); plan 15-04's preflight-env.sh owns that gate."

patterns-established:
  - "Test files that import app.config/app.main/app.db.session must do so lazily (inside test functions), never at module top-level — those modules hold module-level singletons (Settings, the SQLAlchemy engine) frozen at first import, and a top-level import during pytest's collection phase freezes them on the pre-testcontainers DATABASE_URL before any fixture gets a chance to correct it."

requirements-completed: [EDIT-02]

# Metrics
duration: ~100min
completed: 2026-07-12
---

# Phase 15 Plan 02: Edition Mechanics — Router Gating + Outbox Guard Summary

**Validated `EDITION` flag (oss/saas, no `pro`) driving an explicit two-list router registry in a `create_app()` factory (33 core + 2 saas-only routers, zero route regressions), plus a `neo4j_outbox` write guard moved from dead static-config check to the live `get_driver()` signal — both proven against real Postgres via testcontainers after fixing three previously-undiscovered test-infrastructure blockers.**

## Performance

- **Duration:** ~100 min
- **Completed:** 2026-07-12T14:08:31Z
- **Tasks:** 3 (all completed)
- **Files modified:** 9 (2 created, 7 modified)

## Accomplishments

- `Settings.EDITION` (default `oss`, accepts `saas`, fails fast on `pro`/anything else) using the Phase-14 `field_validator` fail-fast pattern.
- `app/main.py` rewritten from 35 flat `include_router()` calls into `CORE_ROUTERS` (33) + `SAAS_ONLY_ROUTERS` (2: `waitlist`, `external_sessions`) + `create_app(edition)` factory. Route-set diff against the pre-change `main.py` is exactly empty (0 removed, 0 added) — every prefix/tag preserved verbatim.
- The three stale "paid tier" references from the cancelled license design fixed: `crm.py` and `tasks.py` docstrings, and `0008_team_plan.py`'s docstring only (migration body/revision untouched — confirmed via diff grep on `revision|down_revision|def |op\.`).
- `test_edition_gating.py`: 11 tests locking the negative case (`EDITION=oss` 404s on both SaaS-only paths, not 401), the strict-subset invariant, and `test_every_router_module_is_classified` — verified live to actually fail (naming `audit` by name) when a router is dropped from `CORE_ROUTERS`, then restored to green.
- `neo4j_outbox` INSERT in `memory.py` gated on `get_driver() is not None` instead of the (rejected, dead) `settings.NEO4J_URI and settings.NEO4J_PASSWORD` check. Proven against a real Postgres testcontainer: with `NEO4J_URI`/`NEO4J_PASSWORD` both set but no reachable driver, an upsert with `metadata.entities` returns 201, writes the memory item, and adds zero `neo4j_outbox` rows; with a live driver, rows are still enqueued. Both the bare `if entities:` guard and the rejected config-based guard were manually injected and confirmed to make the decisive test **FAIL**, then reverted.
- Fixed three previously-undiscovered, pre-existing test-infrastructure bugs that had silently made every Docker/testcontainers-backed integration test in this repo a no-op skip in this dev environment (see Deviations) — required to satisfy the plan's "a SKIPPED test counts as a FAILURE here" constraint for Task 3.

## Task Commits

Each task was committed atomically:

1. **Task 1: EDITION setting + explicit router registry + create_app factory** — `0818a1e` (feat)
2. **Task 2: Pin the negative case and trap unclassified routers** — `903b86a` (test)
3. **Task 3: Guard the neo4j_outbox INSERT on the live driver** — `bfee1b3` (fix, includes test-infra deviations)
4. **Follow-up: reword main.py comment self-tripping the stale-tier grep** — `b758cb1` (fix)

_No TDD gate applies — plan type is `execute`, not `tdd`._

## Route counts (input to 15-04's live gate)

| Edition | Route count |
|---|---|
| `oss` | 91 |
| `saas` | 94 |

`CORE_ROUTERS` = 33 tuples, `SAAS_ONLY_ROUTERS` = 2 tuples (`waitlist`, `external_sessions`). `oss` route set is a strict subset of `saas`'s (verified via `oss < saas` on the path sets, and via a pre/post diff against the original flat `main.py`'s route set showing 0 removals / 0 additions).

## Injected-regression results (all three required to FAIL — confirmed)

| Injection | Target test | Result |
|---|---|---|
| Removed `(audit.router, ...)` from `CORE_ROUTERS` | `test_every_router_module_is_classified` | **FAILED**, naming `audit` in the assertion message. Restored → passed again. |
| Reverted guard to bare `if entities:` | `test_no_outbox_rows_when_neo4j_unreachable` | **FAILED** with "neo4j_outbox grew with no reachable Neo4j...". Reverted → passed again. |
| Replaced guard with rejected `if entities and settings.NEO4J_URI and settings.NEO4J_PASSWORD:` | `test_no_outbox_rows_when_neo4j_unreachable` | **FAILED** identically — proves the test discriminates "config present" from "Neo4j reachable", not just "config present". Reverted → passed again. |

## `saas`-profile ⇒ `EDITION=saas` coupling (for 15-04)

`session-bridge` (a `saas`-profile compose service) POSTs to `/v1/me/external-sessions`. If `COMPOSE_PROFILES` includes `saas` but `EDITION` is left at the default `oss`, that router isn't mounted and session-bridge's register frame gets a silent 404. This plan does not enforce the coupling (memory-api must not learn about `COMPOSE_PROFILES`, an orchestrator-level concept) — plan 15-04's `preflight-env.sh` is the intended enforcement point, per `15-CONTEXT.md`'s threat register (T-15-02-05, disposition `transfer`).

## Files Created/Modified

- `apps/memory-api/app/config.py` — `EDITION` field + `_validate_edition` field_validator
- `apps/memory-api/app/main.py` — `CORE_ROUTERS` / `SAAS_ONLY_ROUTERS` / `create_app(edition)` / module-scope `app = create_app()`
- `apps/memory-api/app/routes/crm.py` — docstring fix only
- `apps/memory-api/app/routes/tasks.py` — docstring fix only
- `apps/memory-api/alembic/versions/0008_team_plan.py` — docstring fix only (body untouched)
- `apps/memory-api/app/routes/memory.py` — `get_driver()` import + outbox guard condition
- `apps/memory-api/tests/test_edition_gating.py` — new, 11 tests
- `apps/memory-api/tests/test_outbox_neo4j_guard.py` — new, 2 tests
- `apps/memory-api/tests/conftest.py` — 3 test-infra fixes (see Deviations)

## Decisions Made

- Kept `EDITION` a plain `str` (not `Literal`/enum) per Claude's Discretion in `15-CONTEXT.md`, validated via `field_validator` — matches the existing `OAUTH_ISSUER_URL`/`CORS_ALLOWED_ORIGIN_REGEX` pattern in the same file rather than introducing a new validation idiom.
- Did not touch `app/deps.py`'s `require_paid_tier` — see "Out-of-scope finding" below.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Plan's own literal code self-tripped its "no paid tier" grep gate — twice**
- **Found during:** Task 1 acceptance criterion 3 (`grep -rni "paid tier|pro tier" apps/` must return nothing)
- **Issue:** The plan's literal `EDITION` field_validator `ValueError` text ("...There is no `pro` edition; the paid tier was dropped.") and the `CORE_ROUTERS` comment block ("...the paid tier and its Ed25519 license were dropped...") both contain the literal substring `"paid tier"`, which the plan's own acceptance grep is defined to reject. The plan explicitly warns "Do not use the words 'paid tier' in any replacement text" but its own provided code violates that in two places.
- **Fix:** Reworded both to "paid self-host tier" (same meaning, breaks the substring match).
- **Files modified:** `apps/memory-api/app/config.py`, `apps/memory-api/app/main.py`
- **Verification:** `grep -rni "paid tier|pro tier" apps/` exits 1 (no matches).
- **Committed in:** `0818a1e` (config.py instance, caught before commit), `b758cb1` (main.py instance, caught in self-check after Task 1's commit had already landed — see note below)

**2. [Rule 1 - Bug] Same self-tripping pattern in the neo4j_outbox guard's explanatory comment**
- **Found during:** Task 3 acceptance criterion 1 (`grep -n "settings.NEO4J_URI and settings.NEO4J_PASSWORD" ... echo exit=$?` must be exit=1)
- **Issue:** The plan's literal explanatory comment for the guard contains the exact rejected-guard expression as prose ("NOT `settings.NEO4J_URI and settings.NEO4J_PASSWORD`: compose passes..."), which the plan's own acceptance grep for "the rejected guard must not appear" then matches.
- **Fix:** Reworded to describe the same reasoning without the literal contiguous expression.
- **Files modified:** `apps/memory-api/app/routes/memory.py`
- **Committed in:** `bfee1b3`

**3. [Rule 3 - Blocking] `pytest-asyncio` fixture nests `asyncio.run()` inside a running loop**
- **Found during:** Task 3, first attempt to actually run an integration test against real Docker (previously always silently skipped — `docker`/`testcontainers` packages were declared as optional test deps in `pyproject.toml` but never installed in this dev environment)
- **Issue:** `alembic/env.py`'s `run_migrations_online()` calls `asyncio.run(run_async_migrations())`. `tests/conftest.py`'s `pg_url` fixture called `command.upgrade(cfg, "head")` directly from inside its own `async def`, which pytest-asyncio runs under an already-active event loop — `asyncio.run()` cannot be called from a running event loop and raised `RuntimeError`.
- **Fix:** Wrapped the call in `await asyncio.to_thread(command.upgrade, cfg, "head")` so it runs in a worker thread with no running loop of its own.
- **Files modified:** `apps/memory-api/tests/conftest.py`
- **Committed in:** `bfee1b3`

**4. [Rule 3 - Blocking] `settings`/`engine` are module-level singletons frozen at first import, desynced from testcontainers' `DATABASE_URL`**
- **Found during:** Task 3, after fixing #3, running `test_edition_gating.py` before `test_outbox_neo4j_guard.py` in the same session cascaded into `ConnectionRefusedError` for `test_outbox_neo4j_guard.py`'s Alembic setup, and (in a full-suite run) `RuntimeError: Event loop is closed` for the second+ test in any multi-test integration file (reproduced independently in the pre-existing `test_admin_brain.py`, unmodified by this plan).
- **Issue:** Two related bugs in `app.config.settings` (a `pydantic_settings.BaseSettings` singleton) and `app.db.session.engine` (a `create_async_engine` singleton), both evaluated once at first import, reading `DATABASE_URL` from the environment at that instant:
  (a) `tests/conftest.py`'s `pg_url` fixture only set `os.environ["DATABASE_URL"]`; any test module that had already imported `app.config`/`app.main` (even lazily, inside a test body executed earlier in the session) had already frozen `settings.DATABASE_URL` on the pre-testcontainers default. `alembic/env.py` then read that stale value and silently overwrote the correct URL `pg_url` had explicitly set on the Alembic `Config` object moments earlier.
  (b) A pooled `asyncpg` connection checked back into `engine`'s pool at one test's teardown stays bound to that test's (per-function) event loop; the next test's fresh loop then fails to reuse or cleanly terminate it.
- **Fix:** `pg_url` now patches `app_config.settings.DATABASE_URL` in place and recreates `app.db.session.engine`/`async_session_factory` immediately after starting the container (before running Alembic). `conftest.py`'s `session` fixture now calls `await engine.dispose()` at teardown so the next test opens fresh connections on its own loop instead of inheriting a stale one.
- **Files modified:** `apps/memory-api/tests/conftest.py`
- **Verification:** Re-ran `test_edition_gating.py` + `test_outbox_neo4j_guard.py` + `test_soft_delete_regression.py` + `test_tagging_contract.py` together (the exact combination that reproduced the cascade) — no more `ConnectionRefusedError`/`Event loop is closed`; remaining failures in that combination are the unrelated pre-existing `test_soft_delete_regression.py` bug documented below.
- **Committed in:** `bfee1b3`

**5. [Rule 1 - Bug] `test_edition_gating.py`'s top-level imports were the actual trigger for #4(a)**
- **Found during:** Same investigation as #4
- **Issue:** The plan's own literal code for `test_edition_gating.py` imports `app.config`/`app.main`/`app.routes` at module top-level. Because this file collects/executes before any Postgres-backed test, those top-level imports froze `settings`/`engine` on the pre-testcontainers `DATABASE_URL` before `pg_url` ever ran — this file was the practical trigger for deviation #4, not merely a bystander.
- **Fix:** Switched to lazy, per-test imports, matching the codebase's own established convention documented in `test_health.py` and `test_brain_events_list.py`'s `_get_app_and_dep()` helper ("Lazy import so test collection stays light when integration deps aren't present").
- **Files modified:** `apps/memory-api/tests/test_edition_gating.py`
- **Committed in:** `bfee1b3`

**6. [Rule 2 - Missing critical] Test-suite default `MEMORY_BACKEND=stub` never touches Postgres**
- **Found during:** Task 3, writing the decisive test
- **Issue:** The plan's `_memory_item_count(session, item_id)` helper asserts a real `memory_items` row exists after upsert. The test-suite default backend (`NativeStubProvider`, an in-process dict) never writes to Postgres at all, so this assertion would be untestable as specified.
- **Fix:** `test_outbox_neo4j_guard.py` overrides `get_memory_provider` with a minimal `_PgOnlyMemoryProvider` that writes `memory_items` via the same `AsyncSession` the test's `client` fixture already uses for `get_session` — no Qdrant, no embedder — keeping the upsert inside the same rolled-back transaction as the rest of the test.
- **Files modified:** `apps/memory-api/tests/test_outbox_neo4j_guard.py`
- **Committed in:** `bfee1b3`

**7. [Rule 3 - Blocking] `upsert_item`'s fire-and-forget background tasks leaked across tests**
- **Found during:** Task 3, after fixing #4, `test_outbox_rows_written_when_driver_is_live` (the 2nd test in the file) still intermittently errored at setup with the same connection-pool symptom as #4(b)
- **Issue:** `upsert_item()` fires three `asyncio.create_task()` calls (Graphiti enrichment, CRM contact extraction, auto-task creation) whenever `body.item.content` is non-empty — a valid `MemoryItem` per the 7-field tagging contract requires non-empty `content`. These are fail-soft and orthogonal to the outbox guard, but being fire-and-forget they can outlive the test's event loop and leave a broken pooled connection behind for the next test.
- **Fix:** `test_outbox_neo4j_guard.py` monkeypatches `app.routes.memory._enrich_with_graphiti`, `_extract_crm_contacts`, and `_maybe_create_task_from_action` to no-ops for the duration of each test.
- **Files modified:** `apps/memory-api/tests/test_outbox_neo4j_guard.py`
- **Committed in:** `bfee1b3`

---

**Total deviations:** 7 auto-fixed (2 Rule 1 grep self-trips, 1 Rule 2 missing test infra, 4 Rule 3 blocking test-infra bugs)
**Impact on plan:** All fixes were necessary to make Task 3's decisive tests actually run against real Postgres/Docker (rather than silently skip, which the environment explicitly treats as a failure) and to keep the plan's own literal acceptance grep gates passing. No production behavior changed beyond what Tasks 1–3 specify; all fixes are confined to `tests/conftest.py` and the two new test files, plus the two grep-self-trip comment rewordings in already-in-scope production files.

## Out-of-scope finding (not fixed — flagged for the user/orchestrator)

**`app/deps.py`'s `require_paid_tier` dependency is live and currently 403s `/v1/crm/*` and `/v1/tasks/*` for every team whose `teams.plan = 'starter'`.** Since nothing in the shipped codebase ever sets a team's `plan` away from the `'starter'` default (`0008_team_plan.py`'s `server_default="starter"`; confirmed via `grep -rn "plan = 'team'\|SET plan" apps/` — no matches outside migrations and one test helper), this dependency currently blocks CRM and Tasks for every team in every environment, including production. This directly contradicts locked decision Q6 ("no product feature is paywalled") in the same way the three stale docstrings this plan fixes did, but at the enforcement-logic layer rather than the comment layer.

This was **not fixed** here: it is not in this plan's `files_modified` (only `crm.py`/`tasks.py` docstrings were in scope), fixing it would require editing `app/deps.py` (removing/replacing `require_paid_tier` across 10 `Depends()` call sites in `crm.py`/`tasks.py`) and touching `tests/test_soft_delete_regression.py`'s `_upgrade_team_to_paid` test helper — a materially larger, policy-level change (Rule 4: architectural) that no other plan in Phase 15 currently names either (`grep -rln require_paid_tier .planning/phases/15-edition-mechanics/*.md` — no matches). Flagging for a follow-up plan or quick task.

## Pre-existing test suite findings (not fixed — out of scope)

Enabling real Docker/testcontainers integration testing in this dev environment for the first time (the `docker`/`testcontainers` Python packages were declared as optional `pyproject.toml` test deps but had never actually been installed here) surfaced **56 pre-existing test failures across 14 files**, none of which this plan's `files_modified` touches:

`test_admin_brain.py`, `test_admin_wipe.py`, `test_brain_events_list.py`, `test_external_sessions.py`, `test_media.py`, `test_migration_0019.py`, `test_phase10_auth.py`, `test_phase10_repos.py`, `test_phase12_auto_grant_regression.py`, `test_phase12_org_membership.py`, `test_phase12_refresh_token.py`, `test_phase12_webhook.py`, `test_soft_delete_regression.py`, `test_team_context_cache.py`.

Spot-checked root causes (representative, not exhaustive):
- `test_soft_delete_regression.py` hardcodes `validation_status='unverified'`, which violates the `memory_items_validation_check` CHECK constraint (`IN ('pending','validated','rejected','n/a')`, migration 0002) — the test itself is stale.
- `test_phase12_webhook.py`'s failures trace into `app/routes/webhooks_github.py`'s `log.info("github_webhook.received", event=event, ...)` — `structlog` reserves the `event` kwarg internally, so passing an additional `event=` collides: `TypeError: ... got multiple values for argument 'event'`.
- `test_migration_0019.py::test_0019_alembic_head_is_current` asserts the Alembic head is `0019_github_app_install`; the actual head is `0023_tasks_source_connector` — a stale assertion, unrelated to this plan (no migrations added here).
- `test_media.py` failures are `503` (MinIO/S3 not configured in this dev env) — environment-config-dependent, not a code defect.
- `test_phase10_*`/`test_phase12_auto_grant_regression.py`/`test_phase12_org_membership.py`/`test_phase12_refresh_token.py` failures were not individually triaged past confirming their files are absent from this plan's diff.

None of these 56 failures are in files this plan modifies, and `git diff 14263e2 HEAD --stat` confirms only the 9 files listed above under Files Created/Modified were touched. This plan's own new/modified test coverage (`test_edition_gating.py`: 13/13, `test_outbox_neo4j_guard.py`: 2/2) is 100% green, run both in isolation and together in the combination that previously triggered the cascading connection-pool failure (deviation #4). Recommend a dedicated audit/cleanup pass — this is a large, cross-phase backlog (spans phases 7, 9–12) disproportionate to Phase 15's scope.

The one **known** pre-existing failure flagged by Phase 14's own `deferred-items.md` (`test_github_sync.py::test_sync_repo_multi_chunk_ids`) is unchanged and was excluded from full-suite runs via `--ignore` per that precedent.

## Known Stubs

None — no UI/data stubs introduced by this plan.

## Threat Flags

None beyond what `15-CONTEXT.md`'s threat register already names (T-15-02-01 through T-15-02-05, all `mitigate`/`transfer` and addressed by Tasks 1–3 as specified). No new network endpoints, auth paths, or schema changes were introduced.

## Issues Encountered

Covered above under Deviations — all were test-infrastructure blockers, not design ambiguities, and all resolved without needing a checkpoint/user decision.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- `EDITION` flag and router registry are in place and tested; 15-03 (compose wiring for `EDITION`) and 15-04 (live deploy gate / `preflight-env.sh`) can build on `create_app()`, `CORE_ROUTERS`/`SAAS_ONLY_ROUTERS`, and the route counts recorded above.
- `docker-compose.yml` was **not** touched by this plan (owned by the parallel 15-01 worktree) — no conflict expected.
- `neo4j_client.py`, `outbox_worker.py`, `routes/graph.py` are untouched, confirmed via `git diff --name-only` — 15-05's reconnect-loop work has a clean starting point.
- Flag for follow-up: the `require_paid_tier` finding and the 56 pre-existing test failures (both above) are not blockers for 15-03/15-04/15-05 but should be triaged before this repo is considered "tests green" in any comprehensive sense.

---
*Phase: 15-edition-mechanics*
*Completed: 2026-07-12*

## Self-Check: PASSED

All 9 created/modified source files and the SUMMARY.md itself confirmed present on disk. All 4 commit
hashes (`0818a1e`, `903b86a`, `bfee1b3`, `b758cb1`) confirmed present in `git log --oneline --all`.
