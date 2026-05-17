---
phase: 11
plan: 11-02
subsystem: memory-api / database schema / view
tags: [migration, view, union-all, brain-monitor, pagination, indexes, tests]
dependency_graph:
  requires:
    - 0017_brain_monitor_base (provides truth_level + deleted_at + deleted_by on the 6 base tables)
    - teams table (slug + id needed for the per-arm JOINs that normalise team_scope vs team_id)
  provides:
    - 0018_brain_events_view (new alembic head)
    - v_brain_events SQL view (UNION ALL across 6 tables → 7 entity_types)
    - 5 new composite (team_scope, created_at DESC) indexes
  affects:
    - 11-03 (Qdrant payload upgrade) — independent vector side, no PG dependency
    - 11-04 (BMO-02 endpoint /v1/brain/events) — hard depends on this view
    - 11-05..11-11 — all read on top of this universal projection
tech_stack:
  added: []
  patterns:
    - "UNION ALL view as universal event projection (entity_type CASE branch on memory_items.source for granola_note)"
    - "JOIN to teams in every arm to normalise team_scope (slug) ↔ team_id (UUID)"
    - "View DDL kept as a module-level string constant in the migration for easy diffing on future arm-additions"
    - "Postgres-native CREATE OR REPLACE VIEW + DROP VIEW IF EXISTS round-trip pattern (no ALTER VIEW ADD COLUMN — drop-and-recreate is mandatory for column changes)"
    - "Idempotent CREATE INDEX IF NOT EXISTS for composite (team, created_at DESC) on 5 of 6 tables (team_messages already has its equivalent from 0015)"
key_files:
  created:
    - apps/memory-api/alembic/versions/0018_brain_events_view.py
    - apps/memory-api/tests/test_migration_0018.py
  modified: []
key_decisions:
  - "Single migration (not 0018-indexes + 0019-view) — view DDL is small enough that splitting adds revision-chain ceremony without rollback benefit"
  - "5 new (team_scope, created_at DESC) indexes created idempotently with IF NOT EXISTS — defensive against partial-apply scenarios and out-of-band ops"
  - "team_messages composite index NOT recreated — it already exists from migration 0015 as (team_id, created_at DESC); recreating with same name would error, recreating with different name would duplicate"
  - "Granola notes surfaced via CASE WHEN mi.source='granola' THEN 'granola_note' ELSE 'memory_item' END — no separate granola_notes table per 11-RESEARCH §Q1; this is the canonical decision"
  - "created_by NULL for memory_items / messages / contacts arms — those tables have no author FK; the 11-04 authorization helper must treat NULL → admin-only edit (Option A from CONTEXT)"
  - "Partial indexes WHERE deleted_at IS NULL deliberately NOT added — plan flagged them as nice-to-have; at <10k rows/team they're premature optimisation, can be added in a later phase when row counts justify"
metrics:
  duration_minutes: ~21
  completed_date: 2026-05-17
  commits: 2
  files_changed: 2
  insertions: 636
---

# Phase 11 Plan 11-02: Migration 0018 — v_brain_events SQL view + Composite Indexes Summary

One-line summary: Creates the universal `v_brain_events` Postgres VIEW that UNION ALLs `memory_items` (with granola_note CASE-branch), `conversations`, `messages`, `team_messages`, `tasks`, and `contacts` into a single 7-entity_type normalised projection, plus 5 new composite `(team_scope, created_at DESC)` indexes — the foundation Wave 3b plan 11-04 will query with a single `WHERE team_scope = :slug ORDER BY created_at DESC LIMIT 50` to power BMO-02 brain monitor feed.

## Goal

After 11-01 added the missing `truth_level / deleted_at / deleted_by` columns to all 6 entity tables, the brain monitor still needs a way to read them as ONE stream — different tables, different team-scope column types (slug vs UUID), different content/title fields. Plan 11-02 closes that gap with one Postgres VIEW that exposes the normalised projection mandated by 11-CONTEXT (entity_type / entity_id / team_id / team_scope / created_at / created_by / truth_level / deleted_at / deleted_by / preview / source), so any caller can query the universal feed with the trivial WHERE/ORDER BY/LIMIT shape that backs cursor pagination.

After this plan: `SELECT * FROM v_brain_events WHERE team_scope = 'default' ORDER BY created_at DESC LIMIT 50` returns a unified, normalised event stream across all 7 entity types. BMO-02 is satisfied at the database layer. Plan 11-04 builds the HTTP wrapper.

## What Shipped

### Migration `0018_brain_events_view.py`

| Concern | Delivered |
|---------|-----------|
| Revision id | `0018_brain_events_view` |
| Down revision | `0017_brain_monitor_base` (single alembic head verified) |
| View created | `v_brain_events` (UNION ALL across 6 tables → 7 distinct entity_types) |
| New composite indexes | `idx_memory_team_created`, `idx_conv_team_created`, `idx_msg_team_created`, `idx_tasks_team_created`, `idx_contacts_team_created` (5 total — team_messages already has its equivalent from 0015) |
| Idempotency | All `CREATE INDEX` use `IF NOT EXISTS`; `CREATE OR REPLACE VIEW` for the view itself |
| Downgrade | `DROP VIEW IF EXISTS v_brain_events` then drop the 5 new indexes in reverse order; team_messages index NOT touched (created by 0015) |

### View shape (every arm exposes identical column types)

```
entity_type   TEXT   ('memory_item' | 'granola_note' | 'conversation' | 'message' | 'team_message' | 'task' | 'contact')
entity_id     UUID   (primary key of source row)
team_id       UUID   (teams.id, surfaced via JOIN for every arm)
team_scope    TEXT   (teams.slug — uniform across all 7 types; team_messages gets it via the same JOIN)
created_at    TIMESTAMPTZ
created_by    UUID   (NULL for memory_items / messages / contacts — no author FK; owner_user_id for conversations; author_user_id for team_messages; created_by for tasks)
truth_level   VARCHAR(16) (validated by base-table CHECK constraints)
deleted_at    TIMESTAMPTZ NULL
deleted_by    UUID NULL
preview       TEXT   (LEFT(content/title/full_name COALESCE, 200))
source        TEXT   (raw source string per arm; team_messages uses COALESCE(routed_via, kind))
```

The `granola_note` distinction is a `CASE WHEN mi.source = 'granola' THEN 'granola_note' ELSE 'memory_item' END` on the `memory_items` arm — granola notes are stored as `memory_items` rows with `source='granola'` (see 11-RESEARCH.md §Q1), not a separate table.

### Test lock — `tests/test_migration_0018.py`

6 integration tests (`@pytest.mark.integration` + `@pytest.mark.asyncio`), running against the testcontainers Postgres fixture from `conftest.py`:

1. **`test_0018_view_exists`** — `pg_views.viewname = 'v_brain_events'` after `alembic upgrade head`. Goes red if the migration didn't run or a downstream migration dropped the view without recreating it.
2. **`test_0018_view_returns_all_7_entity_types`** — seeds one row in each of the 6 base tables (plus an extra `memory_items` row with `source='granola'`), asserts `SELECT DISTINCT entity_type FROM v_brain_events WHERE team_scope = :slug` returns exactly `{memory_item, granola_note, conversation, message, team_message, task, contact}`. Adding a UNION arm in a later phase requires updating BOTH the migration AND this test — coupling is intentional.
3. **`test_0018_view_preview_truncates_to_200_chars`** — inserts a 250-char content body, asserts the surfaced `preview` is exactly 200 chars. Catches the `LEFT(..., 100)` typo and the no-truncation regression.
4. **`test_0018_view_deleted_at_passes_through_as_null_by_default`** — pins the soft-delete contract at the view layer so 11-04 endpoint can rely on `WHERE deleted_at IS NULL` to default-hide soft-deleted rows without juggling NULL-vs-empty payload semantics.
5. **`test_0018_view_team_messages_arm_normalises_team_scope`** — inserts a `team_message` (which natively uses `team_id`), asserts it's selectable by slug in the view via `team_scope = :slug`. The critical glue that lets brain-monitor callers filter all 7 entity types uniformly by slug.
6. **`test_0018_composite_indexes_created`** — checks `pg_indexes` for the 5 new `idx_*_team_created` names. Goes red if any future migration drops one of them.

Tests collect cleanly (`pytest --collect-only -q tests/test_migration_0018.py` → `6 tests collected`). They skip gracefully when Docker is unavailable (`6 SKIPPED, 0 errors`) — identical pattern to `test_migration_0017.py` and the rest of the integration suite. They will run live in CI / on the VM where Postgres is available.

## Commits (atomic per task)

| Hash | Task | Subject |
|------|------|---------|
| 8a05aab | 1 | feat(memory-api): migration 0018 — v_brain_events universal event view |
| 81199d3 | 2 | test(memory-api): v_brain_events returns all 7 entity types |

Both commits passed all hooks. Each commit touches exactly one file (the migration / the test). Zero unintended deletions across both (`git diff --diff-filter=D --name-only HEAD~2 HEAD` returns empty).

## Decisions Made

- **Single migration, not split:** The plan offered a 0018-indexes + 0019-view split. Rejected — the view DDL is small (140 lines including formatting comments) and the indexes are conceptually paired with the view (they exist BECAUSE the view's read pattern needs predicate pushdown). Splitting adds revision-chain ceremony without rollback benefit, since downgrading the view always requires downgrading the indexes that backed it.
- **No partial indexes WHERE deleted_at IS NULL:** Plan called them out as nice-to-have. At <10k rows/team they're premature optimisation, and the partial-index condition couples the index to the soft-delete contract — if 11-05 changes the soft-delete semantics, the partial indexes would need rebuilding. Defer to a later perf-tuning phase when row counts justify the rebuild risk.
- **IF NOT EXISTS for all new indexes:** Defensive against partial-apply scenarios (CI re-runs, manual psql ops, idempotent re-deploys). Adds zero cost in the happy path; saves debugging time in the unhappy path.
- **View DDL as a module-level string constant:** Future migrations that add an entity arm can import this constant for diffing without re-parsing the file. Documents the "drop-and-recreate" pattern in code, not just in comments.

## Deviations from Plan

### Rebase to inherit Wave 1 commits

The worktree was created at commit `0b0b50d` (pre-Wave-1), 41 commits behind `main`. Wave 1 (commits `0a20783..d22b3ac` adding migration 0017) was on `main` but not on the worktree branch — the predecessor migration check would have failed.

**Action:** Ran `git rebase main` (zero local commits on the worktree branch, so the rebase was a pure fast-forward equivalent). After rebase, `apps/memory-api/alembic/versions/0017_brain_monitor_base.py` was present and the migration chain was complete (verified `0018_brain_events_view → 0017_brain_monitor_base → ... → 0001` single head).

**Classification:** Rule 3 (blocking environmental issue, not a design decision). Same pattern as the 11-01 deviation but via `git rebase` instead of `git merge --ff-only` (rebase is the safer operation when the branch has no local commits — it's identical to ff-merge in that case).

**Impact:** None on code. No commits affected (rebase resulted in zero new objects in the worktree branch graph since there were no local commits to replay).

### None other.

The 2 plan tasks executed exactly as specified — same filename, same revision id, same down_revision, same view DDL shape (every column from plan §3 Task 1 SQL block reproduced verbatim), same 5 indexes, same test fixture pattern. No additional bug fixes, no missing-functionality additions, no scope changes.

## Authentication Gates

None — no external services contacted during this plan.

## Verification

| Check | Method | Result |
|-------|--------|--------|
| Migration Python syntax | `py_compile` on `0018_brain_events_view.py` | OK |
| Test Python syntax | `py_compile` on `test_migration_0018.py` | OK |
| Test discovery | `pytest --collect-only -q tests/test_migration_0018.py` | 6 tests collected |
| Test execution (no Docker) | `pytest tests/test_migration_0018.py -v` | 6 SKIPPED (expected — testcontainers needs Docker, identical to all integration tests in this repo) |
| Alembic chain integrity | Walk all `revision`/`down_revision` pairs from versions/*.py | Single head: `0018_brain_events_view → 0017_brain_monitor_base → … → 0001` |
| Atomic commits | `git log --oneline d22b3ac..HEAD` | 2 commits, 1 per task |
| No unintended deletions | `git diff --diff-filter=D --name-only HEAD~2 HEAD` | empty |

Live-DB verification belongs to plan 11-11 (`verify-phase11.sh`) and the ops UAT — not in scope for a per-plan SUMMARY. The plan's Section 3 Task 1 acceptance criterion (`psql -c "SELECT entity_type, COUNT(*) FROM v_brain_events WHERE team_scope='default' GROUP BY entity_type"` returns rows without error; `EXPLAIN ANALYZE` shows Index Scan plans) will be executed against the running VM Postgres in the verify-phase11 wave.

## Notes for Subsequent Wave Plans

- **11-03 (Qdrant payload upgrade):** Independent of this plan. The PG side is now ready for `WHERE deleted_at IS NULL` filtering; the Qdrant side must add `deleted_at_ts` to point payloads (its own concern, no PG dependency).
- **11-04 (BMO-02 `/v1/brain/events` endpoint):** Hard depends on this view. The endpoint can now run `SELECT entity_type, entity_id, team_id, team_scope, created_at, created_by, truth_level, deleted_at, deleted_by, preview, source FROM v_brain_events WHERE team_scope = :ts AND deleted_at IS NULL ORDER BY created_at DESC LIMIT :n` straight against the view — no Python-side UNION builder, no per-arm fanout. Cursor pagination uses the `(created_at, entity_type, entity_id)` triple per 11-RESEARCH §Q7.
- **11-04 authorization helper (`assert_can_edit_brain_event`):** Must handle `created_by IS NULL` (memory_items, messages, contacts arms) by falling back to "team admin only" per Option A from 11-CONTEXT. The view's NULL-projection makes this NULL handling explicit and testable.
- **11-05 / 11-06 (retrieval-side patches):** The new `(team_scope, created_at DESC)` indexes cover the cursor pagination path. The `(team_scope, deleted_at)` indexes from 0017 cover the default `deleted_at IS NULL` filter. Both index families coexist without overlap — Postgres will pick whichever has a better selectivity for a given query shape.
- **Future arm-addition pattern:** When a new entity table joins the brain, the migration MUST `DROP VIEW IF EXISTS v_brain_events` then `CREATE OR REPLACE VIEW v_brain_events AS ...` with the new arm appended. Postgres doesn't support `ALTER VIEW ADD COLUMN`. The test `test_0018_view_returns_all_7_entity_types` must also be updated to include the new entity_type — that coupling is intentional and is the project convention for view evolution.

## Self-Check: PASSED

- File `apps/memory-api/alembic/versions/0018_brain_events_view.py` — FOUND
- File `apps/memory-api/tests/test_migration_0018.py` — FOUND
- Commit `8a05aab` (Task 1) — FOUND in git log
- Commit `81199d3` (Task 2) — FOUND in git log
- Single alembic head (`0018_brain_events_view`) — VERIFIED via revision-chain walk
- Down revision points to `0017_brain_monitor_base` — VERIFIED via `revision`/`down_revision` parsing
- 0 unintended deletions across both commits
- HEAD on `worktree-agent-ac7d436dbf4f575ec` (per-agent branch — protected-ref check passes)
- STATE.md / ROADMAP.md NOT touched per parallel-executor protocol
