---
phase: "07"
plan: "07-02"
subsystem: "memory-api/crm"
tags: ["crm", "paid-tier", "fastapi", "audit", "idor-protection"]
dependency_graph:
  requires: ["07-01"]
  provides: ["07-03", "07-04", "07-05"]
  affects: ["apps/memory-api"]
tech_stack:
  added: []
  patterns:
    - "require_paid_tier: Depends(get_team_scope) chained dependency for plan enforcement"
    - "IDOR protection: AND team_scope = :ts on every GET/PATCH/DELETE"
    - "Upsert on (team_scope, email) WHERE email IS NOT NULL DO UPDATE"
    - "write_audit before session.commit() — audit-in-transaction atomicity"
key_files:
  created:
    - "apps/memory-api/app/routes/crm.py"
  modified:
    - "apps/memory-api/app/deps.py"
    - "apps/memory-api/app/main.py"
decisions:
  - "require_paid_tier chains via Depends(get_team_scope) — membership + plan checked in same dependency tree"
  - "_user_id_from_principal placed in deps.py (not crm.py) — DRY for tasks.py and granola_integration.py (07-03, 07-04)"
  - "Dynamic SET clause in PATCH is SQL-injection-safe because keys come from Pydantic model field names (already validated)"
metrics:
  duration: "~15 minutes"
  completed: "2026-05-07T02:59:40Z"
  tasks_completed: 3
  files_changed: 3
---

# Phase 7 Plan 02: CRM Contacts Router Summary

**One-liner:** FastAPI CRM router with paid-tier gate — 5 CRUD endpoints over `contacts` table, IDOR-safe, audit-logged, upsert on email, plan enforcement via chained `require_paid_tier` dependency.

## What Was Built

### Task 1: `require_paid_tier` + `_user_id_from_principal` in `deps.py`

Added two helpers to `apps/memory-api/app/deps.py`:

- `require_paid_tier`: async FastAPI dependency that chains via `Depends(get_team_scope)` (inheriting membership + auth checks), then queries `SELECT plan FROM teams WHERE slug = :slug`. Raises HTTP 403 with message "CRM and task tracking require a Team or Enterprise plan" if plan is `starter` or team not found.

- `_user_id_from_principal`: sync helper returning `principal["user"].id` or `None` for bridge JWTs. Placed in `deps.py` for reuse by `tasks.py` (07-03) and `granola_integration.py` (07-04).

Also added `import sqlalchemy as sa` and `from uuid import UUID` at module level in `deps.py` (previously not needed, now required by `require_paid_tier`).

### Task 2: `apps/memory-api/app/routes/crm.py`

Created full CRUD router with 5 endpoints:

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/v1/crm/contacts` | `require_paid_tier` | List contacts for team, optional `contact_type` filter, pagination |
| GET | `/v1/crm/contacts/{id}` | `require_paid_tier` | Get single contact (404 if cross-team) |
| POST | `/v1/crm/contacts` | `require_paid_tier` | Create or upsert (email conflict increments `interaction_count`) |
| PATCH | `/v1/crm/contacts/{id}` | `require_paid_tier` | Partial update (dynamic SET clause, safe from injection) |
| DELETE | `/v1/crm/contacts/{id}` | `require_paid_tier` | Hard delete with audit log |

Security properties enforced:
- **IDOR protection**: every GET/PATCH/DELETE includes `AND team_scope = :ts` — cross-team IDs return 404 (not 403, preventing existence leak)
- **Input validation**: `ContactCreateBody` uses `ConfigDict(extra="forbid")` + Field patterns for `contact_type`, `truth_level`, `opt_in_status`
- **SQL injection**: all queries use `sa.text()` with params dict; PATCH dynamic `set_clause` is built from Pydantic field names (already validated)
- **Audit atomicity**: `write_audit()` called before `session.commit()` on every mutation — both in same transaction

### Task 3: `main.py` registration

Added `crm` to the routes import block (alphabetically between `conversations` and `drive_webhook`) and registered `app.include_router(crm.router, prefix="/v1", tags=["crm"])`. All 14 previously existing routers preserved.

## Deviations from Plan

None — plan executed exactly as written. The `sqlalchemy as sa` and `UUID` imports were added to `deps.py` as module-level imports (not inline `import sqlalchemy as sa` inside the function as shown in the plan comment, but functionally identical and cleaner).

## Threat Mitigations Applied

Per the plan's threat model, all STRIDE items marked `mitigate` were addressed:

- T-07-02-01 (Spoofing): `require_paid_tier` on all 5 endpoints
- T-07-02-02 (Tampering): `ConfigDict(extra="forbid")` + Field constraints
- T-07-02-03 (IDOR): `WHERE id = :id AND team_scope = :ts` on all per-resource endpoints
- T-07-02-04 (SQL Injection): dynamic SET clause keys from Pydantic field names; values via params dict
- T-07-02-05 (EoP): `require_paid_tier` non-bypassable dependency on every endpoint
- T-07-02-06 (Repudiation): `write_audit` before `session.commit()` on POST/PATCH/DELETE
- T-07-02-08 (DoS): `limit: int = Query(default=50, ge=1, le=200)` enforced

## Known Stubs

None. The router is fully functional — it reads/writes the `contacts` table created in migration 0009 (07-01).

## Threat Flags

None — no new network surfaces beyond `/v1/crm/contacts*` which is documented in the plan's threat model.

## Self-Check

- [x] `apps/memory-api/app/deps.py` modified — contains `async def require_paid_tier` and `def _user_id_from_principal`
- [x] `apps/memory-api/app/routes/crm.py` created — 5 endpoints, all use `Depends(require_paid_tier)`
- [x] `apps/memory-api/app/main.py` modified — `crm` imported and registered under `/v1`
- [x] All 3 files pass `python -m py_compile`
- [x] Commits: `56e582d` (T1), `93b323f` (T2), `5d06316` (T3)

## Self-Check: PASSED
