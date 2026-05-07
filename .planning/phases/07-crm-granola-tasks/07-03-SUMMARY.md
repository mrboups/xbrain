---
phase: "07"
plan: "07-03"
subsystem: "memory-api/tasks"
tags: ["tasks", "crud", "paid-tier", "audit", "polling"]
dependency_graph:
  requires: ["07-01 (migration 0010 — tasks table)", "07-02 (require_paid_tier + crm pattern)"]
  provides: ["POST /v1/tasks", "GET /v1/tasks", "GET /v1/tasks/{id}", "PATCH /v1/tasks/{id}", "DELETE /v1/tasks/{id}"]
  affects: ["apps/memory-api/app/routes/tasks.py", "apps/memory-api/app/main.py"]
tech_stack:
  added: []
  patterns: ["FastAPI APIRouter + require_paid_tier chain", "parameterized SQL with sa.text()", "audit differentiation (status_changed vs updated)", "polling via since= filter on updated_at"]
key_files:
  created:
    - "apps/memory-api/app/routes/tasks.py"
  modified:
    - "apps/memory-api/app/main.py"
decisions:
  - "Bridge JWTs rejected at POST /v1/tasks with 401 (created_by NOT NULL invariant — T-07-03-01)"
  - "PATCH status audit differentiation: task.status_changed (with from/to) vs task.updated"
  - "_validate_assignee helper: rejects cross-team assigned_to with 422 before INSERT/UPDATE (T-07-03-02)"
  - "since= polling filter uses WHERE updated_at > :since for dashboard diff queries (not >=)"
metrics:
  duration: "~8 minutes"
  completed_date: "2026-05-07"
  tasks_completed: 2
  files_changed: 2
requirements_completed: ["D2", "D4", "D6"]
---

# Phase 07 Plan 03: Tasks Router (/v1/tasks) Summary

**One-liner:** FastAPI tasks CRUD router with paid-tier gate, cross-team assignee validation, and differentiated audit logging (status_changed vs updated).

## Tasks Completed

| # | Task | Commit | Files |
|---|------|--------|-------|
| 1 | Router /v1/tasks — CRUD + filters + polling | fa5a523 | apps/memory-api/app/routes/tasks.py (created, 256 lines) |
| 2 | Register tasks router in main.py | 4deb8f2 | apps/memory-api/app/main.py (+2 lines) |

## What Was Built

### `apps/memory-api/app/routes/tasks.py`

Five endpoints under `/v1/tasks`:

| Method | Path | Status | Description |
|--------|------|--------|-------------|
| GET | /v1/tasks | 200 | List with filters: status, assigned_to, project_scope, since, limit, offset |
| GET | /v1/tasks/{task_id} | 200/404 | Get single task — 404 if wrong team (no existence leak) |
| POST | /v1/tasks | 201/401/422 | Create — 401 if bridge JWT, 422 if assignee cross-team |
| PATCH | /v1/tasks/{task_id} | 200/400/404 | Update — audit differentiates status_changed vs updated |
| DELETE | /v1/tasks/{task_id} | 204/404 | Hard delete with audit |

All 5 endpoints use `Depends(require_paid_tier)` — starter teams receive 403.

**Key behaviors:**
- `_validate_assignee`: runs `SELECT 1 FROM contacts WHERE id = :id AND team_scope = :ts` before INSERT/UPDATE — prevents T-07-03-02 (cross-team contact leak)
- `since=` filter: `WHERE updated_at > :since` — enables dashboard polling diff without full table scan
- PATCH audit logic: compares `current.status` to `body.status`; emits `task.status_changed` with `from_status`/`to_status` payload, otherwise `task.updated`
- SQL injection mitigation: SET clause built from Pydantic model field names only (ConfigDict extra="forbid"), values via bound params (T-07-03-06)

### `apps/memory-api/app/main.py`

Added `tasks` to the routes import block and registered:
```python
app.include_router(tasks.router, prefix="/v1", tags=["tasks"])
```
16 total routers registered, all pre-existing routers preserved.

## Deviations from Plan

None — plan executed exactly as written. The code in the plan's `<action>` block was correct and complete; applied verbatim.

## Threat Mitigations Applied

| Threat ID | Status |
|-----------|--------|
| T-07-03-01 | Mitigated: Bridge JWT → 401 at POST |
| T-07-03-02 | Mitigated: `_validate_assignee` before INSERT/UPDATE |
| T-07-03-03 | Mitigated: `WHERE id = :id AND team_scope = :ts` on all reads |
| T-07-03-04 | Mitigated: Audit with from_status/to_status on every PATCH |
| T-07-03-05 | Mitigated: `Depends(require_paid_tier)` on all 5 endpoints |
| T-07-03-06 | Mitigated: SET clause from validated Pydantic field names only |
| T-07-03-07 | Mitigated: limit ≤ 200, since filter available |
| T-07-03-08 | Accepted: source_ref is a UUID, reading memory_item requires its own team check |
| T-07-03-09 | Mitigated: FK ON DELETE SET NULL (migration 0010) + _validate_assignee rejects deleted |

## Threat Flags

None — no new network endpoints, auth paths, or schema changes beyond what the plan's threat model covers.

## Known Stubs

None — all endpoints are fully wired to the tasks table via parameterized SQL.

## Self-Check: PASSED

- `apps/memory-api/app/routes/tasks.py` exists: FOUND
- Commit fa5a523 exists: FOUND
- `apps/memory-api/app/main.py` contains `tasks.router`: FOUND
- Commit 4deb8f2 exists: FOUND
- All 5 endpoints present, require_paid_tier applied 5 times: VERIFIED
- `_user_id_from_principal` imported (not redefined): VERIFIED
