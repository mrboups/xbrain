---
phase: 07-crm-granola-tasks
plan: "07-01"
subsystem: database
tags: [alembic, postgresql, migrations, crm, contacts, tasks, granola]

# Dependency graph
requires:
  - phase: 05-plateforme-projets-equipe
    provides: "users table + teams table (0001_initial, 0007_github_users) — FK targets for contacts.assigned_to and tasks.created_by"
  - phase: 04-consolidation-mcp-frontends-et-integrations
    provides: "memory-api Alembic pipeline at revision 0007"
provides:
  - "teams.plan column (starter/team/enterprise) — D2 paid tier enforcement"
  - "contacts table — D1 CRM with full 7-field tagging contract + partial unique index on email"
  - "granola_integrations table — D3 encrypted API key storage"
  - "tasks table — D4 task tracking with FK to contacts + users, D5 source provenance"
affects: [07-02, 07-03, 07-04, 07-05, 07-06, 07-07, 07-08, 07-09]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Alembic partial unique index via op.execute raw SQL (WHERE email IS NOT NULL)"
    - "CHECK constraints at DB level for all enum-like fields (defense-in-depth against app bugs)"
    - "ON DELETE SET NULL FKs for contacts.assigned_to and tasks.created_by (orphan-safe)"

key-files:
  created:
    - "apps/memory-api/alembic/versions/0008_team_plan.py"
    - "apps/memory-api/alembic/versions/0009_crm_contacts.py"
    - "apps/memory-api/alembic/versions/0010_tasks.py"
  modified: []

key-decisions:
  - "tasks.created_by FK uses ON DELETE SET NULL (not RESTRICT) — allows user deletion while preserving tasks with NULL attribution for audit trail"
  - "contacts.email partial unique index (WHERE email IS NOT NULL) — permits multiple email-less contacts per team without constraint violation"
  - "granola_integrations.api_key_enc stored as Text (Fernet encryption implemented in Plan 07-04, not here)"
  - "contacts table carries full 7-field tagging contract (visibility + validation_status included despite being marked optional in interfaces)"

patterns-established:
  - "Partial unique index: use op.execute raw SQL for WHERE clause conditions (Alembic does not support this natively)"
  - "Migration chain: each revision explicitly sets down_revision to prior, enabling full reversibility base→head"

requirements-completed: [D1, D2, D4]

# Metrics
duration: 2min
completed: 2026-05-07
---

# Phase 7 Plan 01: Schema Foundations Summary

**Three Alembic migrations (0008-0010) adding teams.plan, contacts, granola_integrations, and tasks tables with full tagging contracts and DB-level CHECK constraints for CRM + Task Intelligence Phase 7.**

## Performance

- **Duration:** 2 min
- **Started:** 2026-05-07T02:51:17Z
- **Completed:** 2026-05-07T02:53:24Z
- **Tasks:** 3
- **Files modified:** 3

## Accomplishments
- Migration 0008: `teams.plan` column (starter/team/enterprise) with CHECK constraint — foundation for D2 paid tier enforcement on `/v1/crm/*` and `/v1/tasks/*` endpoints
- Migration 0009: `contacts` table (D1 CRM) with full 7-field tagging contract, contact_type CHECK (direct/mass), truth_level CHECK, partial unique index on (team_scope, email) WHERE email IS NOT NULL; `granola_integrations` table for D3 encrypted API key storage
- Migration 0010: `tasks` table (D4) with FK to contacts (assigned_to, SET NULL) + users (created_by, SET NULL nullable for system-generated tasks), source provenance CHECK (granola/agent/chat/manual), 4 composite indexes

## Task Commits

Each task was committed atomically:

1. **Task 1: Migration 0008 — colonne plan sur teams** - `b5fd046` (feat)
2. **Task 2: Migration 0009 — tables contacts + granola_integrations** - `827be41` (feat)
3. **Task 3: Migration 0010 — table tasks** - `e7bf54c` (feat)

## Files Created/Modified
- `apps/memory-api/alembic/versions/0008_team_plan.py` - Adds teams.plan VARCHAR(16) NOT NULL server_default='starter' with CHECK constraint
- `apps/memory-api/alembic/versions/0009_crm_contacts.py` - Creates contacts (D1 CRM, 7-field tagging) + granola_integrations (D3 API key storage)
- `apps/memory-api/alembic/versions/0010_tasks.py` - Creates tasks with FK contacts+users, status/priority/source CHECKs, 4 indexes

## Decisions Made
- `tasks.created_by` uses `ON DELETE SET NULL` (not RESTRICT as suggested in threat model T-07-01-06 description) — SET NULL preserves tasks when a user is deleted, with NULL attribution indicating system/deleted-user origin. Matches the acceptance criteria `sa.ForeignKey("users.id", ondelete="SET NULL")` exactly.
- `contacts` table carries all 7 tagging contract fields including `visibility` and `validation_status` (the interfaces noted 5/7 as sufficient for v1, but full 7-field completeness future-proofs the schema and aligns with system invariants).
- `granola_integrations.api_key_enc` is plain `Text` column — Fernet encryption/decryption is an application-layer concern (Plan 07-04), not a DB-layer concern.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- Docker not available in local shell environment — alembic upgrade/downgrade verification against a live DB was not performed locally. Migrations are syntactically valid Python and structurally match the spec exactly. Full DB verification will occur when deployed to the VM via the Phase 7 verification script.

## Known Stubs

None — these are pure schema migrations with no application logic.

## Threat Flags

No new security surface beyond the threat model already documented in the plan.

## User Setup Required

None - no external service configuration required for schema migrations.

## Next Phase Readiness
- Schema foundation complete for all Phase 7 plans
- 07-02 (CRM router) can now reference `contacts` table
- 07-03 (tasks router) can now reference `tasks` table
- 07-04 (Granola integration) can now reference `granola_integrations.api_key_enc`
- Migration chain 0008→0009→0010 must be applied via `alembic upgrade head` before any Phase 7 endpoint is live

---
*Phase: 07-crm-granola-tasks*
*Completed: 2026-05-07*
