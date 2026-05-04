---
phase: 03-graphe-extraction-integrations
plan: 02
subsystem: database
tags: [alembic, postgres, neo4j, drive-sync, outbox-pattern, migration]

# Dependency graph
requires:
  - phase: 02-memoire-intelligente-agents
    provides: memory_items table + alembic chain 0001-0003
provides:
  - neo4j_outbox table (id UUID, cypher TEXT, params JSONB, processed BOOL, created_at, processed_at, error)
  - team_drive_mappings table (id UUID, team_scope, folder_id, change_token, oauth_credentials_enc, updated_at)
  - idx_memory_source_team_unique UNIQUE(source, team_scope) on memory_items
  - Alembic chain extended to 0004 — memory-api auto-upgrades at boot
affects:
  - 03-04-outbox-worker
  - 03-11-drive-sync

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Outbox pattern: memory-api writes Cypher to neo4j_outbox, background worker drains async (eventually consistent)"
    - "Encrypted credential column: oauth_credentials_enc TEXT — schema signals intent, encryption implemented in 03-11"
    - "Partial unique index: idx_outbox_unprocessed covers only processed=false rows — O(1) index size"

key-files:
  created:
    - apps/memory-api/alembic/versions/0004_neo4j_outbox.py
  modified: []

key-decisions:
  - "processed_at and error columns added to neo4j_outbox beyond plan spec — needed for worker observability and retry logic (Rule 2)"
  - "idx_drive_mapping_team_unique is a plain UNIQUE on team_scope (not partial) — simpler, semantically correct: one mapping per team enforced unconditionally"
  - "oauth_credentials_enc nullable: mapping can be created before OAuth flow completes"

patterns-established:
  - "Outbox drain pattern: SELECT ... WHERE processed=false LIMIT 50 ORDER BY created_at, covered by idx_outbox_unprocessed partial index"
  - "Drive-sync upsert key: ON CONFLICT(source, team_scope) DO UPDATE — enabled by idx_memory_source_team_unique"

requirements-completed: [SRCH-05, INT-01, INT-02, INT-03]

# Metrics
duration: 8min
completed: 2026-05-04
---

# Phase 3 Plan 02: Neo4j Outbox + Drive Mappings Migration Summary

**Alembic migration 0004 adding neo4j_outbox (async Neo4j sync via outbox pattern), team_drive_mappings (per-team Drive folder config with encrypted OAuth credentials), and UNIQUE(source, team_scope) on memory_items for drive-sync idempotence.**

## Performance

- **Duration:** ~8 min
- **Started:** 2026-05-04T00:00:00Z
- **Completed:** 2026-05-04T00:08:00Z
- **Tasks:** 1
- **Files modified:** 1

## Accomplishments

- Migration 0004 created with full upgrade() and downgrade() paths
- neo4j_outbox: 7 columns including processed_at and error for worker observability
- Partial index idx_outbox_unprocessed keeps index size constant as rows accumulate
- team_drive_mappings: unique constraint on team_scope enforces one mapping per team
- idx_memory_source_team_unique enables ON CONFLICT idempotent upserts from drive-sync
- Alembic chain 0003 → 0004 correct; memory-api auto-runs at boot

## Task Commits

1. **Task 1: Alembic migration 0004** - `1b89105` (feat)

**Plan metadata:** (docs commit below)

## Files Created/Modified

- `apps/memory-api/alembic/versions/0004_neo4j_outbox.py` - Migration 0004: neo4j_outbox + team_drive_mappings + idx_memory_source_team_unique

## Table Schemas

### neo4j_outbox

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| id | UUID | NOT NULL | gen_random_uuid() | PK |
| cypher | TEXT | NOT NULL | — | Cypher statement to replay in Neo4j |
| params | JSONB | NOT NULL | {} | Cypher parameters |
| processed | BOOLEAN | NOT NULL | false | Drained by worker |
| created_at | TIMESTAMPTZ | NOT NULL | now() | Enqueue timestamp |
| processed_at | TIMESTAMPTZ | NULL | — | Set by worker on success |
| error | TEXT | NULL | — | Worker stores failure reason |

Index: `idx_outbox_unprocessed` on (created_at) WHERE processed = false

### team_drive_mappings

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| id | UUID | NOT NULL | gen_random_uuid() | PK |
| team_scope | VARCHAR(64) | NOT NULL | — | UNIQUE per team |
| folder_id | VARCHAR(256) | NOT NULL | — | Google Drive folder ID |
| change_token | TEXT | NULL | — | Drive changes.list page token |
| oauth_credentials_enc | TEXT | NULL | — | Encrypted OAuth refresh token (Fernet, impl in 03-11) |
| updated_at | TIMESTAMPTZ | NOT NULL | now() | Last poll tick |

Indexes: `idx_drive_mapping_team_unique` UNIQUE on (team_scope), `idx_drive_mapping_folder` on (folder_id)

### memory_items (modified)

New index: `idx_memory_source_team_unique` UNIQUE on (source, team_scope)

## Decisions Made

- processed_at + error columns added beyond plan spec: worker needs these for observability and retry decisions (Rule 2 — missing critical functionality)
- oauth_credentials_enc is nullable: admin can create the mapping first, then complete OAuth flow separately
- UNIQUE index on team_scope in team_drive_mappings is unconditional (not partial): one active mapping per team is the invariant regardless of state

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Added processed_at and error columns to neo4j_outbox**
- **Found during:** Task 1 (reviewing plan spec vs worker needs)
- **Issue:** Plan spec listed 5 columns (id, cypher, params, processed, created_at). The background worker (03-04) needs processed_at to compute drain latency and error to store failure reasons for retry/alerting — without these, worker observability is zero and debugging production drain failures is blind.
- **Fix:** Added processed_at TIMESTAMPTZ NULL and error TEXT NULL to neo4j_outbox
- **Files modified:** apps/memory-api/alembic/versions/0004_neo4j_outbox.py
- **Verification:** Syntax check passes; columns present in create_table call
- **Committed in:** 1b89105 (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 missing critical)
**Impact on plan:** Strictly additive — no must_haves changed. Worker plan 03-04 will rely on these columns.

## Issues Encountered

None. Single-task plan executed cleanly.

## Threat Surface Scan

No new network endpoints. No new auth paths. No new file access patterns. The `oauth_credentials_enc` column introduces a sensitive data field at the DB trust boundary — this is explicitly documented in the plan's threat model (T-03-02-01) and mitigated by 03-11 (Fernet encryption at write time). No additional threat flags.

## User Setup Required

None — migration runs automatically at memory-api boot via alembic upgrade head (configured in docker-compose CMD).

## Next Phase Readiness

- 03-04 (outbox worker) can now target neo4j_outbox — table schema is stable
- 03-11 (drive-sync) can target team_drive_mappings — including encrypted credential storage
- drive-sync upsert can use ON CONFLICT(source, team_scope) DO UPDATE on memory_items
- Alembic chain is linear: head = 0004

---
*Phase: 03-graphe-extraction-integrations*
*Completed: 2026-05-04*
