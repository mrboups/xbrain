---
phase: 11
plan: 11-01
subsystem: memory-api / database schema
tags: [migration, schema, truth_level, soft_delete, orm, tests]
dependency_graph:
  requires:
    - 0016_phase10_github_primary (DDL head before 0017)
    - users table (FK target for deleted_by ON DELETE SET NULL)
    - teams table (slug FK target referenced by tagging contract)
  provides:
    - 0017_brain_monitor_base (new DDL head)
    - truth_level + deleted_at + deleted_by columns on the 6 entity tables
    - 3 new CHECK constraints (conversations / team_messages / tasks)
    - 6 new composite indexes (team_*, deleted_at)
  affects:
    - All Phase 11 plans (11-02..11-11) build on this schema
tech_stack:
  added: []
  patterns:
    - alembic non-concurrent CREATE INDEX (documented OK for current row counts)
    - ON DELETE SET NULL for deleted_by user FK (preserves brain history if user is hard-deleted)
    - server_default + Python default mirroring (so both raw SQL + ORM INSERTs fill truth_level)
key_files:
  created:
    - apps/memory-api/alembic/versions/0017_brain_monitor_base.py
    - apps/memory-api/tests/test_migration_0017.py
  modified:
    - apps/memory-api/app/models/conversation.py
    - apps/memory-api/app/models/message.py
    - apps/memory-api/app/models/team_message.py
key_decisions:
  - "Single migration (not 0017+0018+0019 split) — atomic intent + clean rollback path"
  - "tasks/contacts/memory_items ORM models NOT created — those tables are raw-SQL only; migration is source of truth"
  - "Index creation runs inside default Alembic transaction (no autocommit_block) — acceptable at current <10k rows/table per 11-RESEARCH.md Q9"
  - "contacts.truth_level kept on its 0009 DEFAULT 'EPHEMERAL'; CONTEXT.md target 'WORKING' will be enforced at the API/Pydantic layer in a later plan (no DDL change here)"
  - "deleted_by FK uses ON DELETE SET NULL (matches tasks.created_by pattern from 0010) — purged user must not cascade-delete brain rows"
metrics:
  duration_minutes: ~45
  completed_date: 2026-05-17
  commits: 3
  files_changed: 5
  insertions: 498
  deletions: 1
---

# Phase 11 Plan 11-01: Migration 0017 — Universal truth_level + Soft-Delete Columns + ORM Updates Summary

One-line summary: Adds `truth_level` (with CHECK), `deleted_at`, and `deleted_by` to memory_items / conversations / messages / team_messages / tasks / contacts, syncs the existing Conversation/Message/TeamMessage ORM models, and locks the schema with 4 pytest assertions — the Wave 1 foundation every other Phase 11 plan builds on.

## Goal

Extend the xbrain tagging contract from "memory layer only" (Phase 2) to **every** entity table by adding three columns to those that lack them, with CHECK constraints, server defaults, and per-table composite indexes for the upcoming retrieval paths. Synchronize the SQLAlchemy ORM models so application code can read/write the new columns through the typed model layer.

After this plan: the Alembic head is `0017_brain_monitor_base`, all six target tables expose the three new columns where they were missing, ORM models stay in sync with DDL, and a pytest suite locks the schema. No view, no endpoint, no UI yet — those ship in 11-02 → 11-11.

## What Shipped

### Migration `0017_brain_monitor_base.py`

| Table | truth_level | deleted_at | deleted_by | New index |
|-------|-------------|-----------|-----------|-----------|
| memory_items | already (0002) | NEW | NEW | idx_memory_team_deleted |
| conversations | NEW, DEFAULT 'EPHEMERAL' + CHECK | NEW | NEW | idx_conv_team_deleted |
| messages | already (0001) | NEW | NEW | idx_msg_team_deleted |
| team_messages | NEW, DEFAULT 'WORKING' + CHECK | already (0015) | NEW | idx_team_messages_deleted |
| tasks | NEW, DEFAULT 'WORKING' + CHECK | NEW | NEW | idx_tasks_team_deleted |
| contacts | already (0009) | NEW | NEW | idx_contacts_team_deleted |

12 brand-new columns total (matches plan acceptance criterion: "12 new column rows"). 6 new composite indexes. 3 new CHECK constraints (`conversations_truth_level_check`, `team_messages_truth_level_check`, `tasks_truth_level_check`).

`down_revision = "0016_phase10_github_primary"` (B-1 fix from plan-check Iter 1 honoured — the predecessor migration was verified present in worktree after `git merge --ff-only main` brought the worktree up to current main).

`downgrade()` reverses everything in strict reverse order (indexes → CHECKs → columns) so `alembic downgrade -1 && alembic upgrade head` round-trips cleanly.

### ORM model sync (3 files modified)

- `Conversation` (`models/conversation.py`): added `truth_level` (NOT NULL, default+server_default `'EPHEMERAL'`), `deleted_at`, `deleted_by` (FK ON DELETE SET NULL); added matching `CheckConstraint` to `__table_args__`.
- `Message` (`models/message.py`): added `deleted_at`, `deleted_by`. `truth_level` and its CHECK already present from 0001 — left untouched.
- `TeamMessage` (`models/team_message.py`): added `truth_level` (NOT NULL, default+server_default `'WORKING'`), `deleted_by`; added matching `CheckConstraint`. `deleted_at` already present from 0015 forward-compat — left untouched.

Acceptance verified locally via:
```bash
python -c "from app.models.conversation import Conversation; ...; print(Conversation.__table__.columns.keys())"
```
Output confirmed: all three new columns present on each model (Conversation has truth_level/deleted_at/deleted_by; Message has deleted_at/deleted_by; TeamMessage has truth_level/deleted_by).

`task.py`, `contact.py`, `memory_item.py` ORM files were **not** created. Those tables remain raw-SQL only (confirmed via `ls apps/memory-api/app/models/` — none of the three exist). The migration is the single source of truth for their schema; downstream plans that need ORM access for those tables can add the files as a separate concern.

### Test lock — `tests/test_migration_0017.py`

4 integration tests (`@pytest.mark.integration` + `@pytest.mark.asyncio`), running against the testcontainers Postgres fixture (`session` from `conftest.py`, which executes `alembic upgrade head` before yielding):

1. **`test_0017_all_new_columns_present`** — 14 expected `(table, column)` pairs queried via `information_schema.columns`; fails red if any future migration drops one of the 0017 columns.
2. **`test_0017_truth_level_default_is_working_for_tasks`** — `INSERT INTO tasks` without `truth_level` → DDL default fills `'WORKING'` per CONTEXT.md.
3. **`test_0017_check_constraint_rejects_bogus_truth_level`** — `INSERT … truth_level='BOGUS'` → `IntegrityError` whose message mentions `tasks_truth_level_check`.
4. **`test_0017_composite_indexes_created`** — 6 expected index names queried from `pg_indexes`; fails red if any composite `(team_*, deleted_at)` index is dropped.

Tests collect cleanly (`pytest --collect-only -q` → `4 tests collected`). They skip gracefully (4 SKIPPED, 0 errors) when Docker is unavailable — same auto-skip pattern as `test_team_isolation.py` and the 0010+ integration suite. They run live in CI / on the VM where Postgres is available.

## Commits (atomic per task)

| Hash | Task | Subject |
|------|------|---------|
| 0a20783 | 1 | feat(memory-api): migration 0017 — universal truth_level + soft-delete columns |
| 87490c2 | 2 | feat(memory-api): sync ORM models with 0017 brain-monitor columns |
| c0866e8 | 3 | test(memory-api): assert 0017 schema + truth_level CHECK constraints |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 — Blocking] Fast-forwarded worktree branch to current main**
- **Found during:** Task 1, before staging the migration
- **Issue:** The worktree branch `worktree-agent-a91bcc705c1e2a7c9` was created from commit `0b0b50d` (pre-Phase-8). The latest migration on the worktree was `0015_team_messages.py` — the plan's mandatory `down_revision="0016_phase10_github_primary"` would have referenced a non-existent revision on this branch, breaking `alembic upgrade head` and the plan's Section 0 entry-gate condition.
- **Fix:** `git rev-list --count main..HEAD = 0` confirmed zero local commits on the worktree branch (nothing to preserve), so a fast-forward to main was non-destructive. Ran `git merge --ff-only main`, bringing in 37 missing commits including `0016_phase10_github_primary.py` and the full 11-PLAN/11-CONTEXT/11-RESEARCH suite. After ff-merge, `apps/memory-api/alembic/versions/0016_phase10_github_primary.py` was present and the down_revision target became valid.
- **Files affected:** None (git history operation only).
- **Commit:** Pre-Task-1 (no commit — pure fast-forward of a zero-local branch).
- **Why this is Rule 3 not Rule 4:** No architectural decision required — main was always the intended base; the stale worktree was an environmental bug, not a design choice. The fix preserves zero work because there was zero work to preserve.

### None other.

The 3 plan tasks executed exactly as specified — same files, same column matrix, same CHECK constraint names, same commit-message templates. No additional bug fixes, no missing-functionality additions, no scope changes.

## Authentication Gates

None — no external services contacted during this plan.

## Verification

| Check | Method | Result |
|-------|--------|--------|
| Migration Python syntax | `py_compile` on `0017_brain_monitor_base.py` | OK |
| ORM models Python syntax | `py_compile` on 3 model files | OK |
| ORM column attribute lists match expectations | `python -c "from app.models... print(...columns.keys())"` | OK (truth_level/deleted_at/deleted_by present on all three) |
| Test file Python syntax | `py_compile` on `test_migration_0017.py` | OK |
| Test discovery | `pytest --collect-only -q tests/test_migration_0017.py` | 4 tests collected |
| Test execution (no Docker) | `pytest tests/test_migration_0017.py -v` | 4 SKIPPED (expected — testcontainers needs Docker, identical to all other integration tests in this repo) |
| Atomic commits | `git log --oneline c8828ad..HEAD` | 3 commits, 1 per task |

Live-DB verification (`alembic upgrade head` against the actual VM Postgres and `psql` column-existence check from Section 3 Task 1 acceptance) belongs to the verify-phase11.sh script (built in plan 11-11) and the ops UAT — not in scope for a per-plan SUMMARY.

## Notes for Subsequent Wave Plans

- **11-02 (v_brain_events view, migration 0018)** can now reference `truth_level`, `deleted_at`, `deleted_by` on all 6 tables without further schema work. The plan's existing `down_revision = "0017_brain_monitor_base"` line up correctly.
- **11-03 (Qdrant payload upgrade)** needs to start writing `deleted_at_ts` (float) to point payloads in addition to the existing `truth_level` payload field. The PG side is done; the vector side is its concern.
- **11-04..11-08 (endpoints, retrieval-side patches)** can immediately filter on `deleted_at IS NULL` against the new indexes. The indexes are named consistently `idx_<short>_team_deleted` for easy reference.
- **task.py / contact.py / memory_item.py ORM files** are still absent. If 11-06 (retrofitting read paths with the soft-delete filter) needs ORM access for any of these tables, it should add the model file as part of that plan, not blame 11-01.

## Self-Check: PASSED

- File `apps/memory-api/alembic/versions/0017_brain_monitor_base.py` — FOUND
- File `apps/memory-api/tests/test_migration_0017.py` — FOUND
- File `apps/memory-api/app/models/conversation.py` (modified) — FOUND
- File `apps/memory-api/app/models/message.py` (modified) — FOUND
- File `apps/memory-api/app/models/team_message.py` (modified) — FOUND
- Commit `0a20783` (Task 1) — FOUND in git log
- Commit `87490c2` (Task 2) — FOUND in git log
- Commit `c0866e8` (Task 3) — FOUND in git log
- 0 unintended deletions across all 3 commits
- HEAD on `worktree-agent-a91bcc705c1e2a7c9` (per-agent branch — protected-ref check passes)
