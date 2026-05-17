# Phase 11 SUMMARY

**Status:** SHIPPED | VERIFY-PASS | UAT-PASS — pending
**Verified:** _____________________
**verify-phase11.sh:** PASS: __ / 16 (SKIPPED: __)
**11-UAT.md:** __ / 8 steps PASS

## What shipped

### Schema and data model
- Migration `0017_brain_monitor_base` — `truth_level` + `deleted_at` + `deleted_by`
  columns on 5 new tables (conversations, messages, team_messages, tasks, contacts),
  with per-entity-type backfill defaults (`memory_item`/`granola_note` keep existing,
  `task` -> WORKING, `conversation`/`message`/`team_message` -> EPHEMERAL,
  `contact` -> VALIDATED). Chained from Phase 10's `0016_phase10_github_primary`.
- Migration `0018_brain_events_view` — `v_brain_events` SQL view fanning out 7
  entity types via UNION ALL with derived `entity_type` column.

### Memory layer
- Qdrant payload `deleted_at_ts` + `mark_deleted` / `mark_restored` helpers in
  the memory-api, applied synchronously on PATCH/DELETE so semantic search and
  the brain table stay consistent within a single request.
- One-shot Qdrant payload backfill script (plan 11-03).

### API surface — team-scoped
- `GET /v1/brain/events` — paginated, filterable feed with `entity_type`,
  `truth_level`, `source`, `created_by`, `q`, `include_deleted`, `since`,
  `cursor`, `limit` query params.
- `PATCH /v1/brain/events/{entity_type}/{entity_id}` — edit `truth_level`;
  author OR team-admin only; 403 with locked wording otherwise.
- `DELETE /v1/brain/events/{entity_type}/{entity_id}` — soft delete (PG +
  Qdrant payload) with audit_log row.
- `POST /v1/brain/events/{entity_type}/{entity_id}/restore` — restore within
  30-day window.

### Retrieval regression filters
- All existing list endpoints (`/v1/memory/search`, `/v1/tasks`,
  `/v1/crm/contacts`, `/v1/messages`, conversations) updated to exclude
  `deleted_at IS NOT NULL` rows by default.

### Background workers
- `xbrain-brain-janitor` container — daily 03:00 UTC cron that hard-purges
  rows past the 30-day retention window from Postgres, Qdrant, and Neo4j.
  Liveness via `/tmp/brain-janitor-alive` sentinel.

### App-site UI — team-scoped brain monitor
- `/account/teams/brain/?team=<slug>` — vanilla-JS page with 30-second
  since-prepend polling, inline truth_level edits, soft-delete + restore
  with "Show deleted" toggle, locked 403 toast wording.

### Superadmin scope (REVISION 2)
- `GET /v1/admin/brain/overview` — cross-team counts × entity_type ×
  truth_level matrix.
- `GET /v1/admin/brain/storage` — per-team PG rows + Qdrant points + MinIO
  bytes; Qdrant and MinIO fail soft (return None → dashboard renders N/A).
- `GET /v1/admin/brain/activity?days=N` — per-team events/day, zero-filled.
- `GET /v1/admin/brain/sources?days=N` — per-team source → count.
- `GET /v1/admin/brain/events?team_slug=X` — drill-down with synchronous
  audit_log write BEFORE the data query; 500 on audit failure (no unaudited
  read path).
- `assert_is_superadmin` FastAPI dependency wrapping `_is_admin()` (driven
  by `ADMIN_USER_SUBS` env). Bridge JWTs cannot pass the gate.

### App-site UI — superadmin dashboard
- `/account/admin/` — 4-section vertical dashboard (Brain Overview matrix,
  Storage table, Activity sparklines, Top Sources table).
- Inline SVG sparklines (no chart library — locked per CONTEXT.md).
- Drill-down toggles a yellow "Viewing as superadmin — this access is logged."
  banner and routes brain monitor reads through the audited admin endpoint.
- Probe-and-403 superadmin detection (no new `/v1/me` field needed).
- 403 fallback panel for non-superadmins with no further admin requests.

### Documentation
- Public `marketing-site/docs/brain-monitor.html` covering team-scoped UI +
  superadmin dashboard.
- Internal `.planning/KB/brain-monitor-architecture.md` (8 sections: schema,
  view, allow-list, auth rule, Qdrant pattern, janitor, limitations,
  superadmin model + storage fail-soft).
- `infrastructure/scripts/verify-phase11.sh` — 16 assertions.
- `11-UAT.md` — 8-step manual checklist.

## Verification

- **verify-phase11.sh:** _________ (paste final summary line; expected `PASS: 16 / 16` or `PASS: 15 / 16 (SKIPPED: 1)` if LOCKDOWN_TEST is not set)
- **11-UAT.md:** _________ (step-by-step results, including superadmin step 8a/8b/8c)

## Known issues / followups

- `memory_items`, `contacts`, `messages` have no `created_by` column → only
  team admins can edit those entity types. A future migration adding
  `created_by` lifts the restriction.
- Phase 11 hard-depends on Phase 10's migration `0016_phase10_github_primary`.
  Phase 10 cannot be reverted without re-planning Phase 11.
- Superadmin drill-down v1 grants full content visibility. No break-glass /
  opt-in approval. Phase 12+ candidate.
- Aggregate endpoints (overview / storage / activity / sources) do NOT write
  audit_log per call — only the drill-down events endpoint does. Revisit if
  aggregate access proves sensitive.
- Bulk operations (multi-select PATCH/DELETE) descoped from v1.
- Real-time streaming (WebSocket / SSE) descoped — v1 uses 30-second polling.
- No export (CSV / JSON download of a team's brain) — out of scope for v1.

## Next phase

- Phase 12 TBD. Candidate scope:
  - Break-glass / opt-in approval workflow before superadmin drill-down.
  - Materialised view for `/v1/admin/brain/storage` and `activity` if
    cross-team aggregation perf degrades past acceptable thresholds.
  - Date-range customisation on dashboard sparklines (currently fixed 30d).
  - `created_by` backfill across `memory_items` / `contacts` / `messages`.
  - Admin write endpoints (PATCH/DELETE/restore on `/v1/admin/brain/*`)
    enabling read-write superadmin drill-down.
  - Bulk operations bar in `/account/teams/brain/`.
