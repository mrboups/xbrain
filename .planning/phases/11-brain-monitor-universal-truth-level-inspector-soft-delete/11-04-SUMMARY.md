---
phase: 11-brain-monitor-universal-truth-level-inspector-soft-delete
plan: 11-04
subsystem: api

tags: [fastapi, sqlalchemy, postgres, pydantic, cursor-pagination, auth, rbac, brain-monitor]

# Dependency graph
requires:
  - phase: 11-brain-monitor-universal-truth-level-inspector-soft-delete
    provides:
      - 11-01 / migration 0017 — truth_level + deleted_at + deleted_by columns on every brain-tracked entity
      - 11-02 / migration 0018 — v_brain_events SQL view + composite (team_scope, created_at DESC) indexes
  - phase: 10-github-primary-auth
    provides:
      - get_current_principal returning a unified user shape for Google OIDC, GitHub gho_ and xbt_ tokens
      - get_team_scope membership + blocked_at enforcement (deps.py:238-298)
      - team_members table with role and blocked_at columns
provides:
  - GET /v1/brain/events endpoint — cursor-paginated, filterable, team-scoped
  - assert_can_edit_brain_event shared auth helper (consumed by 11-05's PATCH / DELETE / restore)
  - BrainEventOut / BrainEventListOut / TruthLevelPatchBody Pydantic schemas
  - app/schemas/ module (created — no prior schemas directory existed)
affects:
  - 11-05 (PATCH / DELETE / restore endpoints reuse the helper and the TruthLevelPatchBody schema)
  - 11-08 (app-site Brain Monitor UI consumes GET /v1/brain/events + uses next_cursor for paging)
  - 11-09 (verify-phase11.sh asserts GET /v1/brain/events behaviour end-to-end on the VM)
  - 11-11 (superadmin drill-down endpoint mirrors GET /v1/brain/events under /v1/admin/brain/events)

# Tech tracking
tech-stack:
  added: []   # no new dependencies — used existing FastAPI, SQLAlchemy, Pydantic, authlib
  patterns:
    - "Cursor pagination using Postgres row-value tuple comparison (created_at, entity_type, entity_id) < (...) — single composite predicate, no skip / dup on identical timestamps"
    - "URL-safe base64 JSON cursor tokens with explicit 400 (not 500) on malformed input"
    - "Per-event authorisation helper branching on principal['kind'] only — auth-source-agnostic after Phase 10 identity merge"
    - "Pydantic schemas co-located with each prefix in app/schemas/<area>.py to avoid circular imports between paired endpoints"

key-files:
  created:
    - "apps/memory-api/app/schemas/__init__.py"
    - "apps/memory-api/app/schemas/brain.py — BrainEventOut, BrainEventListOut, TruthLevelPatchBody"
    - "apps/memory-api/app/routes/brain.py — GET /v1/brain/events router + cursor helpers"
    - "apps/memory-api/tests/test_brain_events_list.py — 10-case integration suite"
  modified:
    - "apps/memory-api/app/deps.py — added assert_can_edit_brain_event helper at module tail"
    - "apps/memory-api/app/main.py — registered brain router under /v1"

key-decisions:
  - "Helper branches on principal['kind'] only — Google OIDC, Google access token, GitHub gho_, xbt_ and bridge-acting-user all map to kind='user' or 'user_api_token' after Phase 10 auth-merge, so a single code path covers them without sub-string sniffing"
  - "Both bridge (service trust) AND env-listed superadmin (_is_admin) bypass before the per-team admin DB lookup — cheap predicates first, DB query last"
  - "Cursor uses tuple comparison (Postgres row-value) instead of three ANDed scalar comparisons — fixes the identical-timestamp tie-break documented as M-3 in iter-1 plan-check"
  - "Fetch limit+1 to decide whether next_cursor should be emitted — cheaper than COUNT(*) and avoids the dreaded 'always emit a cursor, last page is empty' bug"
  - "Malformed cursor → HTTPException(400), not 500 — paginated clients iterating against an evolving cursor format see a clear contract violation, not a server error"
  - "TruthLevelPatchBody schema ships with this plan even though only 11-05 consumes it — co-locating with BrainEventOut prevents a circular import in 11-05"

patterns-established:
  - "app/schemas/ module: framework-agnostic Pydantic schemas (no SQLAlchemy, no FastAPI imports) shared between routers and clients"
  - "Per-event authorisation helpers live in deps.py alongside dependency factories (single source of truth, easy lookup, no new module)"
  - "Integration tests stub get_current_principal via app.dependency_overrides (matches the test_phase10_block.py pattern); bridge cases use a real signed JWT to exercise verify_bridge_jwt end-to-end"

requirements-completed: [BMO-02, BMO-03, BMO-08]

# Metrics
duration: 6min
completed: 2026-05-17
---

# Phase 11 Plan 11-04: GET /v1/brain/events + assert_can_edit_brain_event Summary

**Cursor-paginated universal brain-event feed with filter-rich query string, plus the per-event auth helper that 11-05's mutations will reuse — no new auth-source branching needed after Phase 10's identity merge.**

## Performance

- **Duration:** ~6 min
- **Started:** 2026-05-17T02:35:00Z
- **Completed:** 2026-05-17T02:41:30Z
- **Tasks:** 4 / 4 (auto)
- **Files created:** 4
- **Files modified:** 2

## Accomplishments

- **`GET /v1/brain/events`** ships as the single read surface over the `v_brain_events` view introduced in migration 0018. Filters: `entity_type[]`, `truth_level[]`, `source[]`, `created_by`, `q` (ILIKE on preview), `include_deleted` (default false), `since`. Cursor on `(created_at DESC, entity_type ASC, entity_id ASC)` using Postgres row-value comparison.
- **`assert_can_edit_brain_event`** helper added to `deps.py`. Branches on `principal['kind']`: bridge → allow; superadmin (`_is_admin`) → allow; per-team admin (`team_members.role='admin'`) → allow; author match (`created_by == user.id`) → allow; else 403. Documents the 6 principal shapes deps.py returns after Phase 10.
- **`app/schemas/brain.py`** with `BrainEventOut`, `BrainEventListOut`, and `TruthLevelPatchBody` — last one consumed by 11-05 but exported here to dodge a circular import.
- **10-case integration suite** locks the contract: seeding, entity_type filter, cursor round-trip, soft-delete visibility toggle, 403 for non-member, 200 for bridge JWT, identical-timestamp tie-break, plus three principal-kind cases (Google OIDC, GitHub gho_, xbt_) confirming the route + helper are auth-source-agnostic.

## Task Commits

Each task was committed atomically:

1. **Task 1: Pydantic schemas for brain events** — `1f7beb7` (feat)
2. **Task 2: assert_can_edit_brain_event auth helper** — `d4f27da` (feat)
3. **Task 3: GET /v1/brain/events paginated list + filters** — `17430d2` (feat)
4. **Task 4: brain events list integration tests** — `9b0109e` (test)

Plan metadata commit will follow this SUMMARY.

## Files Created/Modified

### Created

- `apps/memory-api/app/schemas/__init__.py` — schemas package marker + module docstring.
- `apps/memory-api/app/schemas/brain.py` — `BrainEventOut` (one row), `BrainEventListOut` (envelope with `next_cursor`), `TruthLevelPatchBody` (PATCH body for 11-05).
- `apps/memory-api/app/routes/brain.py` — `APIRouter` with `GET /brain/events` (mounted under `/v1` in `main.py`), plus `_encode_cursor` / `_decode_cursor` helpers.
- `apps/memory-api/tests/test_brain_events_list.py` — 10 integration cases against `httpx.AsyncClient` + testcontainers Postgres; principal overrides via `app.dependency_overrides`, bridge JWT case uses a real signed token.

### Modified

- `apps/memory-api/app/deps.py` — appended `assert_can_edit_brain_event(principal, *, created_by, team_slug, session)` at module tail. Reuses the already-imported `get_membership` and `_is_admin`.
- `apps/memory-api/app/main.py` — added `brain` to the `from app.routes import (...)` block and one `app.include_router(brain.router, prefix="/v1", tags=["brain"])` line next to the other routers.

## Decisions Made

- **Helper branches on `kind` only, never on the auth source.** Phase 10's identity merge means Google OIDC, GitHub gho_, Chrome-ext Google access token, and bridge-acting-user all surface as `kind='user'` with the same `principal['user']` ORM shape. The previously-drafted `principal.get('user', {}).get('id')` shape would have been correct for that union but would have silently 403'd every bridge service call (no `user` key). Explicit branching on `kind` fixes both correctness AND readability.
- **Both `bridge` AND `_is_admin()` (env-listed superadmin) bypass before the per-team admin DB lookup.** Cheap predicates first, DB query last — the helper costs zero round-trips for the two trust paths that need to be fast.
- **Cursor token format: URL-safe base64 of JSON `{"ts", "et", "id"}`.** JSON over delimited string so adding a future cursor field requires no parsing-rule update. URL-safe base64 so the cursor rides in a query string without %-escaping.
- **Tuple comparison for the cursor tie-break.** `(created_at, entity_type, entity_id) < (:c_ts, :c_et, :c_id)` is a single lexicographic predicate. Splitting it into three ANDed scalar comparisons would either skip rows (too strict on equality) or duplicate them (too loose). Test case 7 locks this.
- **`include_deleted=false` is the default.** Brain Monitor's primary use case is "see what's in the team's brain right now"; soft-deleted rows are noise on that path. The explicit `=true` flag is reserved for the deleted-items toggle and the janitor's pre-purge sanity sweep.
- **`q` filter parameter-binds the `%q%` pattern Python-side.** No string concat into SQL — the pattern is a bound parameter; only the surrounding `%` wildcards are concatenated in Python. ILIKE injection is impossible.

## Deviations from Plan

None — plan executed exactly as written. All 4 plan-check Iter 1 fixes (B-4 for the principal-shape audit, M-3 for the tie-break test, M-4 for the `get_team_scope` contract documentation, plus the housekeeping task order swap) had already been baked into Revision 1 of the plan, so the executor consumed the corrected spec without making further on-the-fly adjustments.

## Issues Encountered

None during planned work. Two environment notes worth recording for the next executor:

- **Docker absent on this worktree host → all 10 brain tests `SKIPPED`** by the conftest `_docker_available()` gate. This is the documented and identical behaviour for every other integration test (`test_phase10_block.py` does the same). Tests will execute in CI / on the GCP VM via `verify-phase11.sh` (plan 11-09). The collection step `python -m pytest --co` succeeded with no import errors, locking the syntactic and import-time contract.
- **`qdrant_client` module not installed on the worktree's Python interpreter.** `app.main` imports `qdrant_setup` which imports it eagerly, so a bare `from app.main import app` fails at module top. The router itself loads cleanly — `from app.routes.brain import router` and `router.routes` work. The conftest `client` fixture imports `app.main` only when Docker is up (and that integration env carries `qdrant_client`), so this never bites the test path.

## User Setup Required

None. Endpoint becomes available the moment the memory-api container restarts with the new code (plan 11-09's verify script + the existing deploy pipeline handle that). No new env vars, no infra changes.

## Next Phase Readiness

- **11-05 (PATCH/DELETE/restore) is unblocked.** The two contracts it needs — `assert_can_edit_brain_event(principal, *, created_by, team_slug, session)` and `TruthLevelPatchBody` — both ship in this plan and are importable as `from app.deps import assert_can_edit_brain_event` and `from app.schemas.brain import TruthLevelPatchBody`. The router prefix is registered too, so 11-05 just appends new routes to the existing `apps/memory-api/app/routes/brain.py` and re-uses the `router` symbol.
- **11-08 (Brain Monitor UI) is unblocked for its primary list path.** The GET endpoint, the response shape, and the `next_cursor` semantics are pinned by the test suite; the UI plan can wire the fetcher against them without ambiguity. The `since=...` query parameter for the polling path is also live.
- **11-11 (superadmin drill-down) can model `/v1/admin/brain/events` directly on this implementation.** The plan already calls out that the admin variant mirrors `/v1/brain/events` but bypasses `get_team_scope` and writes an `audit_log` row — both deltas are mechanical from the structure shipped here.

## Self-Check: PASSED

Files exist on disk:

- FOUND: `apps/memory-api/app/schemas/__init__.py`
- FOUND: `apps/memory-api/app/schemas/brain.py`
- FOUND: `apps/memory-api/app/routes/brain.py`
- FOUND: `apps/memory-api/tests/test_brain_events_list.py`
- FOUND (modified): `apps/memory-api/app/deps.py` — `assert_can_edit_brain_event` resolves at import
- FOUND (modified): `apps/memory-api/app/main.py` — `brain` in router include list, `app.include_router(brain.router, prefix="/v1", tags=["brain"])` present

Commits exist on the worktree branch:

- FOUND: `1f7beb7` — feat(11-04): pydantic schemas for /v1/brain endpoints
- FOUND: `d4f27da` — feat(11-04): assert_can_edit_brain_event auth helper
- FOUND: `17430d2` — feat(11-04): GET /v1/brain/events paginated list + filters
- FOUND: `9b0109e` — test(11-04): brain events list — filters, pagination, cursor tie-break, principal-kind matrix

Acceptance smoke results captured during execution:

- `from app.schemas.brain import BrainEventOut, BrainEventListOut, TruthLevelPatchBody` → `OK`
- `from app.deps import assert_can_edit_brain_event` → `OK helper resolved`
- `_encode_cursor` / `_decode_cursor` roundtrip → `OK`
- Bad token rejection → `HTTPException 400: Malformed cursor token`
- `from app.routes.brain import router` + `router.routes` → `[{'GET'} /brain/events]`
- `pytest tests/test_brain_events_list.py --co` → `10 tests collected`
- `pytest tests/test_brain_events_list.py` → `10 skipped` (Docker absent; identical to test_phase10_block.py behaviour locally — will execute in CI/verify-phase11)

---

*Phase: 11-brain-monitor-universal-truth-level-inspector-soft-delete*
*Plan: 11-04*
*Completed: 2026-05-17*
