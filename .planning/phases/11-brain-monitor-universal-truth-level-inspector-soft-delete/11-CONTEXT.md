# Phase 11: Brain Monitor — Universal Truth-Level Inspector + Soft Delete — Context

**Gathered:** 2026-05-14
**Status:** Ready for planning
**Source:** Direct context capture (AskUserQuestion structural lock — 6 questions, all locked, no discuss-phase needed)

<domain>
## Phase Boundary

**What this phase delivers:**
- A single-page **Brain Monitor UI** at `app-site/account/teams/[slug]/brain/` that streams everything currently entering the team's brain (memories, facts, conversations, transcripts Granola, tasks, contacts CRM, team messages) in a unified, filterable, paginated event feed.
- A **universal `truth_level` contract** — the tagging contract from CLAUDE.md now applies to **every** entity type written by xbrain, not just the memory layer. Migration backfills sensible defaults per entity type.
- **Soft delete** with 30-day retention on every entity, enforced at retrieval across all existing routes (memory search, tasks list, CRM list, etc.), with a daily janitor cron that hard-purges Postgres + Qdrant + Neo4j after retention expires.
- **Author + admin authorization** — author can edit/delete their own items; team admin can edit/delete any item in the team.

**What this phase does NOT deliver:**
- No extension popup UI (web app-site only — locked by user).
- No real-time SSE/WebSocket stream — polling at 30 s is the v1 mechanism.
- No truth_level promotion *automation* (manual edits only — automated promotion stays in memory layer Phase 2 logic).
- No multi-team aggregated view — strictly scoped to one team via `X-Team-Scope`.
- No export/download of brain data (out of scope; could be a v2 add-on).
- No analytics / aggregate charts of brain activity (just a list/feed, not a dashboard).
- No tracker for what was *deleted by whom* beyond the `deleted_by` column (no full audit feed UI — but `audit_log` already captures it backend-side).

</domain>

<decisions>
## Implementation Decisions

### Scope — entities covered (LOCKED via AskUserQuestion)
- **Option C selected:** All entity types covered. The monitor is universal:
  - `memory_items` (already has `truth_level` since Phase 2)
  - `facts` (extracted facts — already has `truth_level` if separate table; else covered by memory_items)
  - `conversations` (chat conversations from LibreChat / Open WebUI / pipeline)
  - `messages` or `team_messages` (per-message rows)
  - `granola_notes` (transcripts Granola ingested)
  - `tasks` (Phase 7)
  - `contacts` (CRM, Phase 7)
- Any new entity table added to xbrain after Phase 11 MUST include `truth_level` + `deleted_at` + `deleted_by` columns by default. Add to project conventions.

### Surface (LOCKED)
- **app-site only:** `app-site/account/teams/[slug]/brain/`
- No Chrome extension UI in Phase 11. Extension can read brain events via API in future phases if needed.
- Uses the existing Phase 5 / Phase 10 `/account/teams/[slug]/` shell (sidebar + Firebase Hosting bundle).

### Permissions model (LOCKED — option A)
- **Author can edit/delete their own items** (`created_by == principal.user.id` or equivalent author field per entity).
- **Team admin can edit/delete ALL items in the team** (`team_members.role = 'admin'`).
- Other team members can **view** all items but **cannot** edit/delete items they didn't create.
- Bridge JWTs (service identity) get admin-equivalent power (existing pattern from `_is_admin` in deps.py).
- 403 returned otherwise.

### Delete model (LOCKED — soft delete + cron purge)
- **Soft delete:** set `deleted_at = now()` + `deleted_by = principal.user.id`.
- **Retention:** **30 days** (locked) — single global value, not per-team configurable (option A chosen over C).
- **Janitor cron container** runs daily at 03:00 UTC, hard-purges:
  - Postgres: `DELETE` rows where `deleted_at < now() - INTERVAL '30 days'`.
  - Qdrant: point delete by `entity_type:entity_id` payload filter (memory_items + facts only — others have no vectors).
  - Neo4j: relation cleanup if node exists (memory_items + facts only — others are not in graph yet).
- **Restore endpoint:** valid only while `deleted_at > now() - 30 days`. Sets `deleted_at = NULL` + `deleted_by = NULL`.

### truth_level extension (LOCKED — option A)
- Migration **0017** (next after 0016 reserved by Phase 10) adds `truth_level TEXT NOT NULL DEFAULT '<entity-appropriate>'` to:
  - `tasks` → DEFAULT `'WORKING'`
  - `contacts` → DEFAULT `'WORKING'`
  - `team_messages` → DEFAULT `'WORKING'`
  - `conversations` → DEFAULT `'EPHEMERAL'`
  - `granola_notes` (or equivalent) → DEFAULT `'WORKING'`
- `memory_items` already has the column (Phase 2) — no change.
- CHECK constraint added per table: `truth_level IN ('EPHEMERAL','WORKING','VALIDATED','CANONICAL','PUBLIC')`.
- All ORM models updated in same migration cycle.

### Soft-delete column rollout
- Migration 0017 also adds `deleted_at TIMESTAMPTZ NULL` + `deleted_by UUID NULL REFERENCES users(id) ON DELETE SET NULL` to every targeted table that doesn't already have them.
- `team_messages` already has `deleted_at` (Phase 2 forward-compat) — keep, add `deleted_by`.
- Index `(team_id, deleted_at)` or `(team_scope, deleted_at)` per table for fast filtered retrieval.

### Endpoints (new — under `/v1/brain/...`)
- `GET /v1/brain/events` — paginated cursor-based list. Filters: `entity_type[]`, `truth_level[]`, `source[]`, `created_by`, `q` (text search on preview), `include_deleted=bool`, `since=ISO8601`. Team-scoped via `X-Team-Scope`.
- `PATCH /v1/brain/events/{entity_type}/{entity_id}` — body `{ truth_level }`. Author or admin only.
- `DELETE /v1/brain/events/{entity_type}/{entity_id}` — soft delete. Author or admin only.
- `POST /v1/brain/events/{entity_type}/{entity_id}/restore` — clear `deleted_at`. Author or admin only. Only valid within retention window.

### Universal event view
- A SQL view `v_brain_events` UNIONs the targeted tables with normalized columns:
  ```
  entity_type | entity_id | team_id | team_scope | created_at | created_by
  | truth_level | deleted_at | deleted_by | preview (LEFT(content/title/etc, 200))
  | source
  ```
- View definition lives in migration 0017 (or 0018 if split for readability).
- Backed by per-source indices on `(team_id, created_at DESC)` and `(team_id, deleted_at)` so the UNION+ORDER stays performant up to ~100k rows per team.

### Retrieval-side enforcement (regression risk)
- EVERY existing read path must add `WHERE deleted_at IS NULL` by default:
  - `POST /v1/memory/search` (already filters memory_items — confirm in deps + tests)
  - `GET /v1/tasks` + `GET /v1/tasks?since=...` (polling)
  - `GET /v1/crm/contacts`
  - `GET /v1/team-messages/...` (team_messages already supports deleted_at, but is the filter applied?)
  - Vector retrieval: payload filter `deleted_at IS NULL OR not-set` on Qdrant search
- A regression test per router confirms soft-deleted rows are invisible by default and visible with explicit `include_deleted=true`.

### app-site UI behavior
- **Route:** `app-site/account/teams/[slug]/brain/` — uses existing Firebase Hosting + same auth shell as `/account/teams/`.
- **Virtualized table** for 1000+ rows (library TBD by planner — likely `@tanstack/virtual` if React, or simple `IntersectionObserver` lazy load if vanilla like the rest of app-site).
- **Filters in side panel:** entity_type (multi), truth_level (multi), source (multi), date range, deleted/active toggle.
- **Row:** icon for entity_type, preview (200 chars), `truth_level` dropdown (5 options), source badge, created_at relative, action menu (Delete / Restore).
- **Inline edit:** changing `truth_level` dropdown fires PATCH immediately + optimistic UI + rollback on error.
- **Bulk select** (admin only): multi-row checkboxes, "Set truth_level to X" or "Delete selected".
- **Polling:** every 30 s with `?since={lastSeenCreatedAt}` cursor — append new rows at top.

### Janitor container
- Docker compose service `brain-janitor` (similar pattern to existing `granola-sync` from Phase 7).
- Python script: scans the 7 tables, finds `deleted_at < now() - 30 days`, hard-deletes Postgres + Qdrant + Neo4j relations.
- Idempotent. Writes an entry to `audit_log` per purge batch.
- Runs once at startup (for testing) then daily via internal scheduler (apscheduler) or cron-style sleep loop.

### Out-of-scope clarifications
- **No GDPR "right to be forgotten" hard-purge endpoint** — that's a separate user-account operation, not a brain-monitor concern. If user requests it, point to Phase 12+.
- **No restore-after-purge** — once janitor purges, data is gone. UI must warn before delete: "This will be purged permanently in 30 days unless restored."
- **No truth_level promotion via UI for non-memory entities in this phase** — the value can be set, but no automated graduation rules. Phase 2's memory-promotion logic stays untouched.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase boundary + scope
- `.planning/ROADMAP.md` — Phase 11 section (Goal, Depends on, BMO-01..09 requirements, Success Criteria)
- `CLAUDE.md` — tagging contract definition (truth_level levels) + OSS-only constraint

### Existing ORM + migrations (must read to plan migration 0017)
- `apps/memory-api/alembic/versions/0001_initial.py` — memory_items, conversations original schemas
- `apps/memory-api/alembic/versions/0002_memory_promotions.py` — truth_level enum + promotion machinery (memory layer reference)
- `apps/memory-api/alembic/versions/0009_crm_contacts.py` — contacts schema
- `apps/memory-api/alembic/versions/0010_tasks.py` — tasks schema
- `apps/memory-api/alembic/versions/0015_team_messages.py` — team_messages schema (already has `deleted_at` forward-compat)
- `apps/memory-api/app/models/team.py`, `app/models/conversation.py`, `app/models/team_message.py`, `app/models/audit.py` — ORM patterns

### Auth + scope enforcement
- `apps/memory-api/app/deps.py` — `get_current_principal`, `get_team_scope`, `_is_admin` patterns
- `apps/memory-api/app/repos/teams.py` — `get_membership` (returns role for admin check)

### Existing routers to retrofit with deleted_at filter
- `apps/memory-api/app/routes/memory.py` (POST /v1/memory/search)
- `apps/memory-api/app/routes/tasks.py` (GET /v1/tasks)
- `apps/memory-api/app/routes/crm.py` (GET /v1/crm/contacts)
- `apps/memory-api/app/routes/team_messages.py` (if exists)

### Cron container pattern reference
- `apps/granola-sync/` (Phase 7 — Dockerfile, pyproject, main loop pattern)
- `docker-compose.yml` (granola-sync service definition pattern)

### app-site UI patterns
- `app-site/account/teams/` (Phase 5 / Phase 10 — sidebar + auth shell)
- `app-site/css/` (existing brand)
- Likely vanilla-JS — check current bundler / no-bundler setup

### Tests + verify pattern
- `infrastructure/scripts/verify-phase10.sh` (or latest verify-phaseN.sh)
- `apps/memory-api/tests/` directory structure

</canonical_refs>

<specifics>
## Specific Ideas

### Migration approach (planner to confirm)
- Single migration `0017_brain_monitor` for all schema changes — cleaner rollback if needed.
- Or split: `0017_truth_level_universal` (truth_level + check constraints) + `0018_soft_delete_universal` (deleted_at + deleted_by) + `0019_brain_events_view` (view definition).
- Recommendation: single `0017_brain_monitor.py` — atomic intent, planner decides split if size justifies.

### View vs UNION-on-the-fly
- A SQL VIEW (`v_brain_events`) is cleaner for ORM but harder to filter dynamically on `entity_type`.
- Alternative: build the UNION query in Python with `sa.union_all(...)` per filter combination.
- Recommendation: SQL VIEW with `entity_type` as a column, filter at query time. Performance acceptable up to ~100k rows/team (xbrain currently has way fewer).

### Cursor pagination
- Cursor = `(created_at, entity_type, entity_id)` tuple, base64-encoded.
- Stable ordering: `ORDER BY created_at DESC, entity_type, entity_id` (tie-breaker on UUID).
- Default page size 50. Max 200.

### Authorization helper
- New `apps/memory-api/app/deps.py` helper `assert_can_edit_brain_event(principal, row)`:
  - True if `_is_admin(principal)` OR `row.created_by == principal.user.id`.
  - Else `HTTPException(403)`.
- Avoid duplicating logic across PATCH/DELETE/restore endpoints.

### Frontend libraries
- Existing app-site is mostly vanilla HTML/CSS/JS (Firebase Hosting bundle) — check for any framework before adding one.
- If pure vanilla: simple paginated table with manual virtualization (only render visible rows + buffer).
- If React/Vue: use existing setup (no new framework introduction).

### Verify script
- `infrastructure/scripts/verify-phase11.sh` — 10+ assertions covering BMO-01..09 + each success criterion.
- Must run inside the existing GCP VM (or local docker-compose) — same pattern as verify-phase7.sh / verify-phase10.sh.

</specifics>

<deferred>
## Deferred Ideas

- **Real-time SSE/WebSocket stream** — deferred to Phase 12+ if polling latency proves insufficient.
- **truth_level promotion automation for non-memory entities** — Phase 2's logic only applies to memory layer. Extending to tasks/contacts/messages is a separate design problem (what triggers a task being promoted from WORKING to VALIDATED?).
- **Aggregate analytics dashboard** (counts per entity_type, truth_level distribution) — out of scope; could be `/account/teams/[slug]/brain/stats` in v2.
- **Export brain to JSON/CSV** — out of scope.
- **Per-team configurable retention** — option C from question 6, rejected in favor of global 30 days. Can be revisited later.
- **Hard delete escape hatch on UI** — option C from question 4, rejected. All deletes go through soft delete.
- **Bulk import / restore from backup** — out of scope.
- **Multi-team aggregated view (xbrain admin)** — out of scope. The monitor is strictly per-team.

</deferred>

---

*Phase: 11-brain-monitor-universal-truth-level-inspector-soft-delete*
*Context gathered: 2026-05-14 via direct AskUserQuestion lock (6 questions, no discuss-phase needed)*
