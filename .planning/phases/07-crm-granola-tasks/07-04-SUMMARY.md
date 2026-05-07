---
phase: "07"
plan: "07-04"
subsystem: "memory-api/routes/granola_integration"
tags: ["granola", "ingest", "fernet", "admin", "bridge-jwt", "atomicity", "dedup"]
dependency_graph:
  requires: ["07-01", "07-02", "07-03"]
  provides: ["granola-admin-crud", "granola-ingest-endpoint"]
  affects: ["memory-api", "granola_integrations table", "memory_items", "contacts", "tasks"]
tech_stack:
  added: ["cryptography.fernet (Fernet encryption for Granola API keys)"]
  patterns:
    - "Bridge JWT service-to-service auth (_is_bridge)"
    - "Fernet-encrypted secrets with FERNET_KEY + OAUTH_CREDENTIALS_ENCRYPTION_KEY fallback"
    - "Idempotent ingest via source_ref dedup query"
    - "Atomic multi-table write (single session.commit)"
    - "_is_admin moved to deps.py (DRY shared helper)"
key_files:
  created:
    - "apps/memory-api/app/routes/granola_integration.py"
  modified:
    - "apps/memory-api/app/config.py"
    - "apps/memory-api/app/main.py"
    - "apps/memory-api/app/deps.py"
    - "apps/memory-api/app/routes/admin_drive.py"
decisions:
  - "FERNET_KEY uses OAUTH_CREDENTIALS_ENCRYPTION_KEY as fallback — single key source for all Fernet encryption in the system"
  - "created_by = NULL for system-generated tasks (migration 0010 nullable) — distinguishes auto-generation from user creates"
  - "_is_admin moved from admin_drive.py to deps.py — single definition, imported by both admin_drive.py and granola_integration.py"
  - "Name-only participants inserted without email dedup (confidence=0.5) — no unique constraint possible without email"
metrics:
  duration: "~8 minutes"
  completed: "2026-05-07T03:07:50Z"
  tasks_completed: 3
  files_modified: 5
  files_created: 1
---

# Phase 07 Plan 04: Granola Integration Router Summary

**One-liner:** Fernet-encrypted Granola API key admin CRUD + atomic bridge-JWT ingest endpoint that writes memory_item + contacts + tasks in a single transaction with source_ref dedup.

## Tasks Completed

| # | Name | Commit | Files |
|---|------|--------|-------|
| 1 | Extend config.py with FERNET_KEY + ANTHROPIC_API_KEY + SMTP_* | bd89c1e | apps/memory-api/app/config.py |
| 2 | Create granola_integration.py router (admin CRUD + ingest) | b7db467 | apps/memory-api/app/routes/granola_integration.py, apps/memory-api/app/deps.py, apps/memory-api/app/routes/admin_drive.py |
| 3 | Register granola_integration router in main.py | 666d76d | apps/memory-api/app/main.py |

## What Was Built

### config.py — 7 new env vars
- `ANTHROPIC_API_KEY: str = ""`
- `FERNET_KEY: str = ""` (fallback to OAUTH_CREDENTIALS_ENCRYPTION_KEY)
- `SMTP_HOST: str = ""`, `SMTP_PORT: int = 587`, `SMTP_USER: str = ""`, `SMTP_PASSWORD: str = ""`, `SMTP_FROM: str = "noreply@dejavu.cat"`, `SMTP_TLS: bool = True`

### granola_integration.py — 4 endpoints
1. `POST /v1/admin/granola-integration` — upsert Granola API key (Fernet-encrypted) for a team. Admin-only.
2. `GET /v1/admin/granola-integration?team_scope=...` — list integrations without exposing api_key_enc. Admin-only.
3. `DELETE /v1/admin/granola-integration/{id}` — remove integration. Admin-only.
4. `POST /v1/integrations/granola/ingest` — bridge-JWT-only atomic ingest:
   - Dedup check via `source_ref = :note_id AND source = 'granola'` (T-07-04-12)
   - Insert `memory_items` (source='granola', truth_level='WORKING', confidence=0.9)
   - Upsert `contacts` by (team_scope, email) — increments interaction_count
   - Insert `tasks` with `created_by = NULL` (system-generated, migration 0010)
   - Single `session.commit()` at end (full atomicity)
   - Audit log `granola.ingest` with counts

### Security threats mitigated
| Threat | Mitigation |
|--------|-----------|
| T-07-04-01 | Fernet.encrypt before INSERT; 500 if key missing |
| T-07-04-02 | GranolaIntegrationOut excludes api_key_enc |
| T-07-04-03 | _is_bridge(principal) check — kind='bridge' required |
| T-07-04-04 | bridge_team != body.team_scope → 403 |
| T-07-04-05 | _is_admin check on all 3 admin endpoints |
| T-07-04-06 | write_audit(action='granola.integration.upserted') with actor |
| T-07-04-08 | All SQL via sa.text() with named params |
| T-07-04-11 | created_by=NULL signals system attribution; audit log entry |
| T-07-04-12 | SELECT id FROM memory_items WHERE source_ref=:note_id before insert |

## Deviations from Plan

### Auto-applied DRY refactor (Rule 2 — missing critical functionality)
**Found during:** Task 2 setup

**Issue:** `_is_admin` was defined only in `admin_drive.py`. The plan explicitly required `from app.deps import _is_admin` in `granola_integration.py`, making it impossible to import without first moving the function.

**Fix:**
1. Added `_is_admin` to `apps/memory-api/app/deps.py` (single canonical definition)
2. Updated `apps/memory-api/app/routes/admin_drive.py` to `from app.deps import _is_admin, ...` (removed local definition)
3. `granola_integration.py` imports cleanly from `deps.py`

**Files modified:** `deps.py`, `admin_drive.py` (included in Task 2 commit `b7db467`)

**Impact:** Zero behavioral change — `_is_admin` logic is identical. All 3 admin callers (admin_drive POST/GET/DELETE) continue working. DRY principle now enforced.

## Known Stubs

None. All endpoints fully wired to DB tables (granola_integrations, memory_items, contacts, tasks).

## Threat Flags

None beyond the plan's threat_model. No new network endpoints, auth paths, or schema changes beyond plan scope.

## Self-Check: PASSED

Files exist:
- `apps/memory-api/app/routes/granola_integration.py` — FOUND
- `apps/memory-api/app/config.py` (modified) — FOUND
- `apps/memory-api/app/main.py` (modified) — FOUND
- `apps/memory-api/app/deps.py` (modified) — FOUND

Commits verified:
- bd89c1e — feat(granola): extend config.py with FERNET_KEY + ANTHROPIC_API_KEY + SMTP_* [07-04-T1]
- b7db467 — feat(granola): create granola_integration.py router — admin CRUD + ingest [07-04-T2]
- 666d76d — feat(granola): register granola_integration router in main.py [07-04-T3]
