---
phase: 11
plan: 11-10
subsystem: memory-api
tags: [admin, superadmin, brain-monitor, audit-log, qdrant, minio, postgres, aggregation]
requires:
  - 0017_brain_monitor_base
  - 0018_brain_events_view
  - assert_can_edit_brain_event (from 11-04)
  - write_audit (existing helper)
  - _is_admin / ADMIN_USER_SUBS (existing mechanism)
provides:
  - assert_is_superadmin (FastAPI dep)
  - GET /v1/admin/brain/overview
  - GET /v1/admin/brain/storage
  - GET /v1/admin/brain/activity
  - GET /v1/admin/brain/sources
  - GET /v1/admin/brain/events (drill-down with synchronous audit)
  - app/db/minio.py — get_minio_client (lazy lru_cache)
  - app/db/qdrant.py — get_qdrant_client (lazy lru_cache)
  - app/repos/brain_metrics.py — 4 aggregation queries
  - app/routes/brain.py::_build_list_query (factored out for reuse)
  - audit_log.action='superadmin_brain_access' rows
affects:
  - apps/memory-api/app/routes/brain.py (factored _build_list_query)
  - apps/memory-api/app/main.py (router registration)
tech-stack:
  added:
    - boto3>=1.35 (S3-compatible client for MinIO)
  patterns:
    - lru_cache singleton for boto3 + qdrant clients (lazy + cached)
    - Fail-soft external deps -> None -> UI displays "N/A"
    - Synchronous audit-write-then-flush BEFORE data read (no-data-without-audit)
    - Shared _build_list_query helper between team-scoped + cross-team callers
key-files:
  created:
    - apps/memory-api/app/db/minio.py (47 lines)
    - apps/memory-api/app/db/qdrant.py (34 lines)
    - apps/memory-api/app/repos/brain_metrics.py (266 lines)
    - apps/memory-api/app/routes/admin_brain.py (186 lines)
    - apps/memory-api/app/schemas/admin_brain.py (76 lines)
    - apps/memory-api/tests/test_admin_brain.py (531 lines)
  modified:
    - apps/memory-api/app/deps.py (+41 lines)
    - apps/memory-api/app/config.py (+13 lines)
    - apps/memory-api/app/main.py (+2 lines)
    - apps/memory-api/app/routes/brain.py (factored 65-line helper)
    - apps/memory-api/pyproject.toml (+1 line)
decisions:
  - QDRANT_COLLECTION default = "messages" (matches xbrain qdrant_setup.py)
  - actor_sub is FIRST payload key (MAJOR-1 bridge-identity capture)
  - Audit row written BEFORE data; flush() awaited; failure -> 500 + rollback
  - Aggregate endpoints (overview/storage/activity/sources) do NOT write audit
  - Lockdown default ADMIN_USER_SUBS empty -> bridges still pass (service trust)
  - _build_list_query factored from routes/brain.py to share with admin_brain
  - app/db/qdrant.py + app/db/minio.py created (no app/memory.py exists)
metrics:
  duration_minutes: 75
  tasks_completed: 6
  files_created: 6
  files_modified: 5
  tests_added: 10
  lines_added: 1253
  lines_deleted: 9
  commits: 6
  completed_date: 2026-05-17
---

# Phase 11 Plan 11-10: Superadmin Endpoints /v1/admin/brain/* Summary

Cross-team superadmin read surface for the Brain Monitor — 5 endpoints under `/v1/admin/brain/*` covering counts x truth_level x entity_type matrix, storage size per team (PG + Qdrant + MinIO), 30-day activity series, top-sources breakdown, and drill-down events with synchronous audit-log enforcement. Gated by `assert_is_superadmin` which wraps the existing `_is_admin()` predicate against `ADMIN_USER_SUBS`. Delivers BMO-10 + BMO-11.

## Tasks Completed

| Task | Description | Commit |
|------|-------------|--------|
| 1 | `assert_is_superadmin` FastAPI dependency in `deps.py` | `40f46e3` |
| 2 | `repos/brain_metrics.py` aggregation queries (4 functions) | `7aa4328` |
| 3 | `app/db/minio.py` + `app/db/qdrant.py` + config + boto3 dep | `45aec70` |
| 4 | `routes/admin_brain.py` (5 endpoints) + schemas + `_build_list_query` factoring | `aaf5a76` |
| 5 | Register `admin_brain.router` in `main.py` | `f22295f` |
| 6 | `tests/test_admin_brain.py` (10 integration cases) | `4e3733d` |

## Endpoints Shipped

| Method | Path | Response | Audit? |
|--------|------|----------|--------|
| GET | `/v1/admin/brain/overview` | `list[TeamOverviewOut]` | No |
| GET | `/v1/admin/brain/storage` | `list[TeamStorageOut]` | No |
| GET | `/v1/admin/brain/activity?days=N` | `list[TeamActivityOut]` | No |
| GET | `/v1/admin/brain/sources?days=N` | `list[TeamSourcesOut]` | No |
| GET | `/v1/admin/brain/events?team_slug=X&...` | `BrainEventListOut` | Yes — synchronous |

## Audit Invariants (drill-down endpoint)

1. **team_slug is REQUIRED** — `Query(..., min_length=1, max_length=64)` returns 422 if absent.
2. **Audit row written BEFORE data read** — `session.flush()` awaited; surfaces DB errors immediately.
3. **Audit failure -> 500 + rollback** — no data is served if the audit write or flush raises.
4. **payload.actor_sub is FIRST key** — captures bridge service identity (granola-sync, agent-runtime, etc.) when actor_user_id IS NULL for bridge JWTs. Without this, bridge cross-team access would be unauditable (MAJOR-1 fix per plan Revision 3).

Operator review query for bridge access:

```sql
SELECT ts, team_scope, payload->>'actor_sub', payload->>'actor_kind'
FROM audit_log
WHERE action='superadmin_brain_access' AND actor_user_id IS NULL
ORDER BY ts DESC LIMIT 50;
```

## Fail-Soft External Deps

| Client | Fail mode | Effect |
|--------|-----------|--------|
| MinIO (boto3) | MINIO_URL empty OR boto3 init raises | `get_minio_client()` returns None; storage endpoint returns `minio_bytes=None`; UI shows "N/A" |
| Qdrant (qdrant_client) | Client init raises OR `count()` raises | `get_qdrant_client()` returns None or count -> None; storage endpoint returns `qdrant_points=None`; UI shows "N/A" |
| Lockdown ADMIN_USER_SUBS="" | Real-user principals -> 403; bridges still pass | Per CONTEXT.md BMO-10(i) — onboarding a superadmin requires explicit env edit |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Plan referenced `app.memory.get_qdrant_client` which does not exist**

- **Found during:** Task 4 (writing routes/admin_brain.py imports)
- **Issue:** Plan section 3 Task 4 imports `from app.memory import get_qdrant_client`. There is no `apps/memory-api/app/memory.py` in the codebase. Qdrant clients are constructed inline in `qdrant_setup.py:21` and `routes/health.py:35`.
- **Fix:** Created `apps/memory-api/app/db/qdrant.py` with a lazy lru_cache-d `get_qdrant_client()` following the same pattern as the new `app/db/minio.py`. Both share the `db/` subpackage convention (already used by `db/base.py`, `db/session.py`).
- **Files modified:** Created `apps/memory-api/app/db/qdrant.py`; the new client is imported by `routes/admin_brain.py`
- **Commit:** `45aec70`

**2. [Rule 2 - Missing critical functionality] Plan referenced `settings.QDRANT_COLLECTION or "memory_items"` but `QDRANT_COLLECTION` was not defined in `app/config.py`**

- **Found during:** Task 2 (writing repos/brain_metrics.py)
- **Issue:** Plan Task 2 code uses `settings.QDRANT_COLLECTION` for the Qdrant count call, but `Settings` class had no such attribute. The actual collection name in xbrain is `messages` (per `qdrant_setup.py:11`) — the `memory_items` fallback in the plan would have queried a non-existent collection in production.
- **Fix:** Added `QDRANT_COLLECTION: str = "messages"` to `Settings` so the default matches what xbrain actually uses; operators can override via the docker-compose env (already set to `memory_items` in the compose file, which now resolves correctly via the setting). Documented the default in the comment.
- **Commit:** `45aec70`

**3. [Rule 3 - Blocking] Plan Task 4 expected `_build_list_query` already factored from `routes/brain.py`**

- **Found during:** Task 4 (writing routes/admin_brain.py)
- **Issue:** Plan Task 4 says "Reuse 11-04's `_build_list_query`" but 11-04 did not factor that helper — the SQL was inline in `list_brain_events`. The plan explicitly anticipated this case ("If 11-04 does not factor out a helper, the executor extracts the query-build block into a shared module-private function").
- **Fix:** Refactored `routes/brain.py::list_brain_events` to delegate to a new module-private `_build_list_query` async helper that takes `team_scope` as a kwarg. Both endpoints now share one implementation; filter semantics cannot drift between team-scoped reads and cross-team superadmin drill-downs.
- **Files modified:** `apps/memory-api/app/routes/brain.py` (+65 / -9 lines)
- **Commit:** `aaf5a76`

### Authentication Gates

None — all 5 endpoints gated by `assert_is_superadmin` which uses the existing `ADMIN_USER_SUBS` mechanism. No new identity primitive added.

## Tests

10 integration cases collected in `apps/memory-api/tests/test_admin_brain.py`:

| # | Name | What it locks |
|---|------|---------------|
| 1 | `test_non_superadmin_403_all_endpoints` | Non-admin user -> 403 on all 5 endpoints |
| 2 | `test_overview_superadmin_returns_nested_counts` | Nested `{entity_type: {truth_level: int}}` shape |
| 3 | `test_storage_pg_rows_present_minio_none_when_unconfigured` | 6 PG tables in pg_rows; minio_bytes=None when MINIO_URL empty |
| 4 | `test_activity_zero_fill_30_days` | daily is always exactly 30 entries (zero-filled invariant) |
| 5 | `test_sources_pivoted_per_team` | sources pivoted dict: librechat=3, granola=2 |
| 6 | `test_drilldown_writes_audit_row` | 1 audit row per call; payload keys: actor_sub, actor_kind, endpoint, query_params, target_team_slug |
| 7 | `test_drilldown_missing_team_slug_422` | FastAPI required-Query validation -> 422 |
| 8 | `test_drilldown_audit_failure_returns_500_no_data` | Defensive: monkey-patch write_audit to raise -> 500 + audit count unchanged |
| 9 | `test_drilldown_bridge_jwt_audit_captures_actor_sub` | MAJOR-1: bridge JWT -> actor_user_id IS NULL + payload.actor_sub='granola-sync' + payload.actor_kind='bridge' |
| 10 | `test_lockdown_admin_user_subs_empty` | ADMIN_USER_SUBS="" -> users 403; bridges still pass |

Pytest collection: `10 tests collected in 0.48s`. Full integration run requires Docker + Postgres testcontainer (skipped on local dev without Docker).

## Smoke Verification (local, no Docker)

```
OK: assert_is_superadmin: <function assert_is_superadmin at 0x...>
OK: get_minio_client (no MINIO_URL): None
OK: get_qdrant_client returns: NoneType   (qdrant_client lib not installed locally — fail-soft works)
OK: brain_metrics 4 functions imported
OK: admin_brain router: /admin/brain tags=['admin-brain']
    /admin/brain/overview {'GET'}
    /admin/brain/storage {'GET'}
    /admin/brain/activity {'GET'}
    /admin/brain/sources {'GET'}
    /admin/brain/events {'GET'}
```

## Coordination Notes (Wave 4 parallel)

This plan ran in Wave 4 concurrent with 11-06 finish + 11-07 finish. Zero file-tree overlap:

- 11-06 (retrieval regression): touched legacy route files (memory.py, tasks.py, crm.py, etc.) — no conflict with new admin_brain.py.
- 11-07 (brain-janitor): in `apps/brain-janitor/` — different package — no conflict.
- 11-10 (this plan): all new files under `apps/memory-api/app/repos/`, `app/db/`, `app/routes/`, `app/schemas/`, `tests/`, plus disjoint appends to deps.py, config.py, pyproject.toml, and a localised refactor of routes/brain.py (no edits to existing logic, only extraction).

STATE.md and ROADMAP.md untouched per parallel-executor protocol.

## Acceptance Mapping (Phase 11 Success Criteria)

| Criterion | Source | Status |
|-----------|--------|--------|
| BMO-10 cross-team aggregate endpoints | Plan section 1 | Covered by /overview, /storage, /activity, /sources |
| BMO-11 drill-down events with audit | Plan section 1 | Covered by /events + synchronous audit + tests 6, 8, 9 |
| Superadmin lockdown by default | CONTEXT.md BMO 10(i) | Covered by test 10 |
| Bridge JWT auditable identity capture | Plan rev 3 MAJOR-1 | Covered by test 9 — payload.actor_sub non-NULL invariant |
| 5 endpoints registered | Task 5 acceptance | OpenAPI introspection: 5 routes under /v1/admin/brain/ |
| 422 on missing team_slug | Plan acceptance | Covered by test 7 |
| Lockdown — users 403 when ADMIN_USER_SUBS empty | BMO 10(i) | Covered by test 10 |

## Self-Check

- [x] `apps/memory-api/app/deps.py` modified — `assert_is_superadmin` appended (commit 40f46e3)
- [x] `apps/memory-api/app/repos/brain_metrics.py` created (commit 7aa4328)
- [x] `apps/memory-api/app/db/minio.py` created (commit 45aec70)
- [x] `apps/memory-api/app/db/qdrant.py` created (commit 45aec70)
- [x] `apps/memory-api/app/config.py` modified — MinIO + QDRANT_COLLECTION settings (commit 45aec70)
- [x] `apps/memory-api/pyproject.toml` modified — boto3 dependency (commit 45aec70)
- [x] `apps/memory-api/app/schemas/admin_brain.py` created (commit aaf5a76)
- [x] `apps/memory-api/app/routes/admin_brain.py` created (commit aaf5a76)
- [x] `apps/memory-api/app/routes/brain.py` modified — `_build_list_query` factored (commit aaf5a76)
- [x] `apps/memory-api/app/main.py` modified — router registered (commit f22295f)
- [x] `apps/memory-api/tests/test_admin_brain.py` created (commit 4e3733d)
- [x] All 6 commits present on branch (verified via `git log --oneline main..HEAD` shows 6 commits)
- [x] STATE.md / ROADMAP.md NOT touched (parallel-executor protocol)

## Self-Check: PASSED
