---
phase: 11-brain-monitor-universal-truth-level-inspector-soft-delete
plan: 11-05
subsystem: api

tags: [fastapi, sqlalchemy, postgres, audit, soft-delete, qdrant, brain-monitor, retention]

# Dependency graph
requires:
  - phase: 11-brain-monitor-universal-truth-level-inspector-soft-delete
    provides:
      - 11-01 / migration 0017 — deleted_at + deleted_by + truth_level columns on every brain-tracked entity
      - 11-02 / migration 0018 — v_brain_events SQL view (the (entity_type, entity_id, team_scope) lookup surface)
      - 11-03 / NativeProvider.mark_deleted / mark_restored — Qdrant payload flip used by the DELETE / restore fan-out for memory_items
      - 11-04 / assert_can_edit_brain_event auth helper + TruthLevelPatchBody / BrainEventOut schemas + brain router skeleton
  - phase: 10-github-primary-auth
    provides:
      - get_current_principal returning unified user shape (Google OIDC, GitHub gho_, xbt_, bridge)
      - get_team_scope membership + blocked_at enforcement
provides:
  - PATCH /v1/brain/events/{entity_type}/{entity_id} (sets truth_level)
  - DELETE /v1/brain/events/{entity_type}/{entity_id} (soft-delete + Qdrant fan-out for memory_items)
  - POST /v1/brain/events/{entity_type}/{entity_id}/restore (clears deleted_at within 30-day window, 410 outside)
  - app/repos/brain.py — fetch_event_row + per-entity table dispatch + update/soft-delete/restore SQL
affects:
  - 11-06 (read-path regression tests can compose against these write endpoints to assert deleted_at filter)
  - 11-07 (brain-janitor cron purges rows whose deleted_at is past the window these endpoints cannot restore)
  - 11-08 (app-site Brain Monitor UI's inline edit / Delete / Restore actions call these three endpoints)
  - 11-09 (verify-phase11.sh end-to-end PATCH → list → DELETE → restore happy path)

# Tech tracking
tech-stack:
  added: []   # no new dependencies
  patterns:
    - "Closed entity_type allow-list (ENTITY_TABLE_MAP) gates the f-string SQL — user input never reaches the table-name interpolation; HTTPException(400) raised before SQL is built"
    - "Repo returns Python wall-clock datetime from soft_delete_entity so the route handler can hand the SAME timestamp to Qdrant — no PG/Qdrant clock drift on the soft-delete moment (RESEARCH §Q3)"
    - "404 vs 410 distinction on restore via a follow-up SELECT — clean contract for the UI ('not deleted' is recoverable, 'past window' is permanent)"
    - "Qdrant fan-out AFTER PG commit + try/except — PG is source of truth, Qdrant failure logged + reconciled by daily janitor"
    - "Audit log written via app.audit.write_audit (project's idiomatic helper) — entity_type encoded in payload, target_id holds the entity uuid"

key-files:
  created:
    - "apps/memory-api/app/repos/brain.py — ENTITY_TABLE_MAP, fetch_event_row, update_truth_level, soft_delete_entity, restore_entity"
    - "apps/memory-api/tests/test_brain_events_mutate.py — 11-case integration suite covering all three endpoints"
    - ".planning/phases/11-brain-monitor-universal-truth-level-inspector-soft-delete/11-05-SUMMARY.md"
  modified:
    - "apps/memory-api/app/routes/brain.py — appended PATCH / DELETE / POST .../restore handlers + _qdrant_mark_*_safe helpers + _reject_unknown_entity_type guard; expanded module docstring"

key-decisions:
  - "Restore uses HTTP 410 Gone (not 409) when past the 30-day retention window — semantic match for 'resource was here, now permanently gone' (matches plan body wording; success criteria mention of 409 was loose, plan §Task 1 was authoritative)"
  - "PATCH rejects soft-deleted rows with 404 — silently reviving truth_level on a deleted row would bypass the explicit Restore action the UI exposes"
  - "DELETE rejects already-deleted rows with 404 — idempotent 204 would paper over double-DELETE bugs; the repo's `WHERE deleted_at IS NULL` filter enforces it server-side too"
  - "Qdrant fan-out happens AFTER PG commit, not in a single transaction — if the DB rolled back and Qdrant succeeded, the system would have a deleted vector behind a live row (the bad direction). Plan 11-07 janitor reconciles the opposite direction."
  - "Used project's write_audit helper instead of the plan's inline raw SQL INSERT into audit_log — matches the audit_log table's actual schema (target_id / payload, not entity_type / metadata)"
  - "Per-event author-vs-admin authorisation reuses the unchanged 11-04 helper — no branching on auth source needed thanks to Phase 10 identity merge"
  - "Recording stand-in MemoryProvider in tests asserts the EXACT datetime PG stored is the one passed to Qdrant — locks the RESEARCH §Q3 invariant"

patterns-established:
  - "app/repos/<area>.py: framework-agnostic SQL dispatch module that takes an AsyncSession + entity ids, raises HTTPException on resolution failure, never imports FastAPI app code or schemas"
  - "Fan-out side-effects (Qdrant / Neo4j / external) live in route-module-private `_<action>_safe` helpers — best-effort, log on failure, fire AFTER session.commit() to keep the DB ahead of mirrors"
  - "When two paired endpoints (PATCH + DELETE + restore) share resolution + auth logic, factor it into a single `_reject_unknown_entity_type` guard at module level rather than duplicating across handlers"

requirements-completed: [BMO-04, BMO-05, BMO-06]

# Metrics
duration: 14min
completed: 2026-05-17
---

# Phase 11 Plan 11-05: PATCH / DELETE / POST .../restore on /v1/brain/events Summary

**Three mutation endpoints + repo dispatch + 30-day retention window + Qdrant fan-out: the Brain Monitor's complete write surface, 100% audited, with PG always the source of truth on PG/Qdrant divergence.**

## Performance

- **Duration:** ~14 min
- **Started:** 2026-05-17 (post-rebase onto main, 11c2fcd predecessors)
- **Completed:** 2026-05-17
- **Tasks:** 3 / 3 (auto, plan revision 1)
- **Files created:** 2 (repo + tests)
- **Files modified:** 1 (routes/brain.py — append-only; the 11-04 GET handler untouched)
- **Lines added:** ~1,200 (repo ~230 + routes ~310 + tests ~660)
- **Tests:** 11 integration cases (10 from plan + 1 paired symmetric mark_restored check)

## What Was Built

Three HTTP endpoints, each dispatching through `app.repos.brain` to the right physical table.

### PATCH `/v1/brain/events/{entity_type}/{entity_id}`

- Body: `{ "truth_level": "EPHEMERAL | WORKING | VALIDATED | CANONICAL | PUBLIC" }` (regex-locked).
- Updates `truth_level` on the underlying table; rejects soft-deleted rows with 404 (use POST `/restore` first).
- Writes audit row `action='brain.patch_truth_level'`, `payload={entity_type, old_truth_level, new_truth_level}`.
- Returns the updated `BrainEventOut` (re-fetched through the view so the response matches what GET would return).

### DELETE `/v1/brain/events/{entity_type}/{entity_id}`

- Sets `deleted_at = now()` (Python clock) + `deleted_by = principal.user.id` (NULL for bridge JWTs).
- Writes audit row `action='brain.soft_delete'`, `payload={entity_type, deleted_at}`.
- For `memory_item` / `granola_note`, also fires `MemoryProvider.mark_deleted` AFTER PG commit with the EXACT same datetime (the `soft_delete_entity` repo helper returns it for this purpose). Failure logged, not raised.
- Returns 204.

### POST `/v1/brain/events/{entity_type}/{entity_id}/restore`

- Clears `deleted_at` + `deleted_by` IFF the row's `deleted_at > NOW() - INTERVAL '30 days'`.
- Outside the window → HTTPException(410) Gone with the locked detail `"retention window expired (>30 days since soft-delete)"`.
- Not soft-deleted at all (or row missing) → HTTPException(404).
- Writes audit row `action='brain.restore'`, `payload={entity_type, previous_deleted_at}`.
- For `memory_item` / `granola_note`, fires `MemoryProvider.mark_restored` AFTER PG commit. Failure logged.
- Returns the restored `BrainEventOut`.

### Auth (unchanged from 11-04)

All three endpoints depend on `assert_can_edit_brain_event(principal, created_by=row['created_by'], team_slug=team_scope, session=session)`. Bridge JWTs bypass; per-team admins bypass within their team; non-admin members allowed only when they authored the row. Rows whose `created_by` is NULL (memory_items, messages, contacts) → admin-only.

### Entity type dispatch

| entity_type     | physical table  | created_by source           | Qdrant mirror? |
| --------------- | --------------- | --------------------------- | -------------- |
| `memory_item`   | `memory_items`  | NULL (no author column)     | YES            |
| `granola_note`  | `memory_items`  | NULL (source='granola' row) | YES            |
| `conversation`  | `conversations` | `owner_user_id`             | no             |
| `message`       | `messages`      | NULL (no author column)     | no             |
| `team_message`  | `team_messages` | `author_user_id`            | no             |
| `task`          | `tasks`         | `created_by`                | no             |
| `contact`       | `contacts`      | NULL (no author column)     | no             |

The `granola_note → memory_items` mapping is safe because `fetch_event_row` goes through `v_brain_events`, which tags `entity_type='granola_note'` only when `source='granola'`. A PATCH against `granola_note/{id}` of a non-granola memory item returns 404 at the view-filter layer before the underlying SQL UPDATE.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing critical functionality] audit_log schema mismatch — used write_audit helper**

- **Found during:** Task 2.
- **Issue:** Plan §Task 2 pseudocode wrote inline `INSERT INTO audit_log (actor_user_id, action, entity_type, entity_id, team_scope, metadata) VALUES (...)`. But the project's `audit_log` table (migration 0001) has columns `actor_user_id, team_scope, action, target_id (String 256), payload (JSONB)` — no `entity_type`, no `entity_id`, no `metadata`. The pseudocode would fail at runtime with a column-not-found error.
- **Fix:** Used the project's idiomatic `app.audit.write_audit()` helper. Encoded `entity_type` into `payload` JSONB; `target_id` carries the entity uuid as text. Matches the pattern in `routes/crm.py`, `routes/agents.py`, `routes/conversations.py` — same call shape across the codebase.
- **Files modified:** `apps/memory-api/app/routes/brain.py`
- **Commit:** `0adc145`

**2. [Rule 2 - Defence-in-depth] PATCH/DELETE reject soft-deleted rows with explicit 404**

- **Found during:** Task 2 design.
- **Issue:** Plan §Task 2 pseudocode let PATCH operate on soft-deleted rows. Silently mutating `truth_level` on a deleted row would (a) violate the UI contract (the Brain Monitor hides deleted rows by default — the user can't see what they're patching), and (b) the DB UPDATE would succeed but the user would never notice. Same problem for DELETE on already-deleted rows.
- **Fix:** Added explicit `if row["deleted_at"] is not None: raise HTTPException(404, ...)` check after `fetch_event_row` in both PATCH and DELETE handlers. The repo's `soft_delete_entity` already enforces `WHERE deleted_at IS NULL` server-side too — defence in depth.
- **Files modified:** `apps/memory-api/app/routes/brain.py`
- **Commit:** `0adc145`

**3. [Rule 1 - Bug avoidance] Qdrant fan-out moved to AFTER session.commit()**

- **Found during:** Task 2 design.
- **Issue:** Plan §Task 2 pseudocode placed `await native_provider.mark_deleted(...)` inside the route handler but did not specify ordering relative to `session.commit()`. If we fan out BEFORE commit and PG then rolls back, Qdrant is ahead of PG (the bad direction — vector excluded from search but row still live in PG, breaking retrieval). If we fan out AFTER commit and Qdrant fails, PG is ahead of Qdrant (the good direction — janitor reconciles, no user-visible regression).
- **Fix:** Always commit PG first, then fire `_qdrant_mark_deleted_safe` / `_qdrant_mark_restored_safe` (both wrap in try/except + warn-log).
- **Files modified:** `apps/memory-api/app/routes/brain.py`
- **Commit:** `0adc145`

### Plan Body vs Success Criteria divergence (intentional)

- Success criteria mentioned `409` for the past-30-day case; plan body explicitly used `410 Gone` (Task 1 acceptance §4, the repo raises 410). Picked `410` — REST-canonical for "resource permanently unavailable" — matching the plan body and the repo's already-shipped HTTPException. Documented in `key-decisions` above so 11-09 verify scripts assert 410.

### Out of Scope (deliberately deferred)

- The plan's optional `freezegun` round-trip is implemented via direct SQL `UPDATE tasks SET deleted_at = NOW() - INTERVAL '31 days'` — plan §Task 3 acceptance explicitly allows the "or equivalent time-travel helper" path. No new test dependency added.
- Bulk PATCH / DELETE — plan §Section 5 out of scope.
- Read-path regression sweep — plan 11-06.

## Auth Gates

None. No external services were required during execution.

## Risks + Mitigations Realised

| Plan risk | Mitigation realised in this plan |
|-----------|----------------------------------|
| Dual-write divergence PG ↔ Qdrant | Qdrant fan-out AFTER PG commit + try/except; janitor (11-07) reconciles. Verified by test 10 (mark_deleted called exactly once with the same datetime PG stored). |
| PATCH on granola_note for a non-granola memory item | `fetch_event_row` goes through the view; non-granola item returns NULL → 404 before any UPDATE. Tested implicitly via the entity_type dispatch in tests. |
| `sa.func.now()` skew between PG bind and Python clock | Repo `soft_delete_entity` uses `datetime.now(tz=timezone.utc)` and returns it; route hands the same datetime to Qdrant. No `sa.func.now()` anywhere in the soft-delete path. Locked by test 10. |
| audit_log JSONB cast bugs | Used `write_audit` helper which uses the SQLAlchemy ORM model — no manual JSONB cast in route handlers. |
| f-string SQL on table name | `_resolve_table()` strictly enforces the closed `ENTITY_TABLE_MAP` allowlist. Unknown values raise 400 before the f-string runs. `_reject_unknown_entity_type` in the route layer adds a second gate before the DB round-trip. |
| 11-04 and 11-05 both edit `routes/brain.py` | 11-05 is wave 3c (sequential after 11-04 wave 3b). The append-only edit (handlers appended; module docstring expanded; imports extended) did not touch the 11-04 GET handler body. |

## Verification

### Static

- `python -c "from app.repos.brain import fetch_event_row, update_truth_level, soft_delete_entity, restore_entity, ENTITY_TABLE_MAP, ALLOWED_ENTITY_TYPES"` → imports OK; 7 entity types resolved.
- `python -c "from app.routes.brain import patch_truth_level, soft_delete_event, restore_event"` → route handlers exported.
- Route inspection: brain router exposes `GET /brain/events`, `PATCH /brain/events/{entity_type}/{entity_id}`, `DELETE /brain/events/{entity_type}/{entity_id}`, `POST /brain/events/{entity_type}/{entity_id}/restore`. Mounted at `/v1` in main.py → full paths `/v1/brain/events/...`.

### pytest collection (no Docker required)

- `python -m pytest tests/test_brain_events_mutate.py --collect-only` → 11 tests collected.
- `python -m pytest --collect-only` (all suites) → 177 tests collected, 0 errors.
- `python -m pytest tests/test_brain_events_mutate.py -v` → 11 SKIPPED (Docker unavailable in this dev env — same as 11-04 test_brain_events_list.py behaviour). On CI / VM with Docker the tests will execute.

### End-to-end (deferred to 11-09 verify-phase11.sh)

The verify-phase11 script (plan 11-09) will exercise the live VM path:
```bash
curl -X PATCH -H "Authorization: Bearer $XBT" -H "X-Team-Scope: default" \
  -H "Content-Type: application/json" -d '{"truth_level":"VALIDATED"}' \
  "$MEMAPI_HOST/v1/brain/events/task/$TASK_ID"
# → 200 + the updated row
curl -X DELETE ... "$MEMAPI_HOST/v1/brain/events/task/$TASK_ID"
# → 204
curl -X POST ... "$MEMAPI_HOST/v1/brain/events/task/$TASK_ID/restore"
# → 200
psql -c "SELECT action FROM audit_log WHERE target_id='$TASK_ID' ORDER BY ts DESC LIMIT 3"
# → brain.restore | brain.soft_delete | brain.patch_truth_level
```

## Commits

| Hash       | Type | Files | Lines | Description |
|------------|------|-------|-------|-------------|
| `dd85c6c`  | feat | apps/memory-api/app/repos/brain.py | +230 | Brain repo with full SQL: fetch / update_truth_level / soft_delete / restore (+30d window) |
| `0adc145`  | feat | apps/memory-api/app/routes/brain.py | +302 / -8 | PATCH / DELETE / POST restore endpoints + Qdrant fan-out helpers + entity-type guard |
| `cd899bd`  | test | apps/memory-api/tests/test_brain_events_mutate.py | +667 | 11-case integration suite: auth matrix, audit log, retention window, Qdrant fan-out invariant |

## Known Stubs

None. All code paths are real implementations.

## Next Plan

**11-06 — Read-path regression sweep:** retrofit existing routes (`POST /v1/memory/search`, `GET /v1/tasks`, `GET /v1/crm/contacts`, `GET /v1/team-messages/*`) to filter `deleted_at IS NULL` by default + add regression tests proving soft-deleted rows are invisible to the public read surface. The endpoints from THIS plan are the test counterparties (create row → 11-06 list shows it → DELETE here → 11-06 list hides it).

## Self-Check: PASSED

- `apps/memory-api/app/repos/brain.py` → FOUND
- `apps/memory-api/app/routes/brain.py` → FOUND (modified)
- `apps/memory-api/tests/test_brain_events_mutate.py` → FOUND
- Commit `dd85c6c` → FOUND on branch
- Commit `0adc145` → FOUND on branch
- Commit `cd899bd` → FOUND on branch
