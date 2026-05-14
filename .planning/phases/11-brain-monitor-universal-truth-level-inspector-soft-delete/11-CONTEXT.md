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
- **Superadmin dashboard at `app-site/account/admin/`** (ADDED 2026-05-14 via in-flight scope expansion) — 4 sections covering: (1) cross-team Counts × truth_level × entity_type matrix, (2) Storage size per team (Postgres rows + Qdrant points + MinIO bytes), (3) Activity time-series events/day per team on 30-day window, (4) Top sources breakdown per team (LibreChat / OWUI / Granola / agent / API). Superadmin can also drill-down into any team's brain monitor (full content visibility v1 — break-glass / opt-in workflow deferred to v2).

**What this phase does NOT deliver:**
- No extension popup UI (web app-site only — locked by user).
- No real-time SSE/WebSocket stream — polling at 30 s is the v1 mechanism.
- No truth_level promotion *automation* (manual edits only — automated promotion stays in memory layer Phase 2 logic).
- No export/download of brain data (out of scope; could be a v2 add-on).
- No tracker for what was *deleted by whom* beyond the `deleted_by` column (no full audit feed UI — but `audit_log` already captures it backend-side).
- **No break-glass / opt-in approval workflow for superadmin content drill-down** — v1 grants superadmin full content visibility unconditionally (every drill-down still writes an `audit_log` entry but team admins are NOT pre-notified). Break-glass model (option C from scope-expansion) deferred to a later phase.
- **No system-health metrics** (janitor lag, soft-delete backlog, orphan rows, MCP error rate) — Pack L was explicitly rejected; Pack M only.
- **No top-contributors metric** (top users/agents per team) — Pack L was rejected.

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
- ~~**Aggregate analytics dashboard**~~ — **PROMOTED IN-FLIGHT to Phase 11 scope (2026-05-14)** as the superadmin dashboard. See "Superadmin dashboard" decisions section below.
- **Export brain to JSON/CSV** — out of scope.
- **Per-team configurable retention** — option C from question 6, rejected in favor of global 30 days. Can be revisited later.
- **Hard delete escape hatch on UI** — option C from question 4, rejected. All deletes go through soft delete.
- **Bulk import / restore from backup** — out of scope.
- ~~**Multi-team aggregated view (xbrain admin)**~~ — **PROMOTED IN-FLIGHT to Phase 11 scope (2026-05-14)** as the superadmin dashboard at `/account/admin/`. See "Superadmin dashboard" decisions section below.

---

## Superadmin dashboard (in-flight scope addition — 2026-05-14)

### Identity model
- A **superadmin** is a principal whose `sub` (or post-Phase-10: `email`) is in the `ADMIN_USER_SUBS` env list. Reuses the existing `_is_admin()` helper at `apps/memory-api/app/deps.py:266-277` — already returns `True` for bridge JWTs (service trust) and for admin subs. **No new identity primitive needed.** Phase 10's identity merge makes Google `sub` ↔ GitHub login ↔ user-row interchangeable, so `_is_admin()` already covers GitHub-primary superadmins as long as their `sub` value (whatever its shape — `email:...` or `github:...` or Google numeric) is listed in the env var.
- Document in 11-RESEARCH addendum (and KB): admin onboarding for a new superadmin = add their `sub` to `ADMIN_USER_SUBS` and restart memory-api.

### Privacy decision (LOCKED — option B in v1, option C deferred)
- **V1:** Superadmin has **full content visibility** across all teams. Drill-down into any team's brain monitor + cross-team aggregates with no consent gate. Every read of another team's data writes an `audit_log` entry (`actor_id`, `target_team_id`, `endpoint`, `params`) — non-blocking but auditable after the fact.
- **V2 (deferred):** Break-glass / opt-in workflow where the superadmin requests access, the target team admin approves via UI/email, access expires 24 h. Tracked as a follow-up phase requirement — NOT in Phase 11.

### Surface (LOCKED — option A)
- New route: `app-site/account/admin/index.html` (gated server-side by 403 if non-superadmin hits `/v1/admin/brain/...`).
- 4 sub-sections rendered as accordions or tabs in a single page:
  1. **Brain Overview** — Counts × truth_level × entity_type matrix per team (table)
  2. **Storage** — PG rows + Qdrant points + MinIO bytes per team (table)
  3. **Activity** — events/day per team over 30 days (sparkline or simple line chart)
  4. **Top Sources** — breakdown per team (stacked bar or table: LibreChat / OWUI / Granola / agent / API counts last 30 days)
- A "Drill down" button per team row → opens the existing `/account/teams/[slug]/brain/` UI **with a superadmin badge banner** ("Viewing as superadmin — this access is logged.").

### Metrics pack (LOCKED — Pack M)
- **Pack M** = Counts + Storage + Activity + Top sources. Pack S (minus Activity + Sources) and Pack L (+ system health + top contributors) rejected.
- All metrics calculated on-the-fly from Postgres + Qdrant API; **no pre-aggregation table** in v1. Acceptable up to ~10 teams + ~100k events/team. If a team grows beyond that, plan a Phase 12 materialized view.

### Endpoints (new — under `/v1/admin/brain/...`, all gated by `assert_is_superadmin`)
- `GET /v1/admin/brain/overview` → `[{team_slug, team_id, counts: {entity_type: {truth_level: int}}}, ...]` for ALL teams
- `GET /v1/admin/brain/storage` → `[{team_slug, pg_rows: {table: int}, qdrant_points: int, minio_bytes: int}, ...]`
- `GET /v1/admin/brain/activity?days=30` → `[{team_slug, daily: [{date: 'YYYY-MM-DD', events: int}, ...]}, ...]`
- `GET /v1/admin/brain/sources?days=30` → `[{team_slug, sources: {source_label: int, ...}}, ...]`
- `GET /v1/admin/brain/events?team_slug=X&...filters` → identical to `/v1/brain/events` but bypasses the X-Team-Scope guard (superadmin can pass any slug — writes audit log entry on each call). For drill-down.

### Authorization helper
- New `apps/memory-api/app/deps.py` helper: `def assert_is_superadmin(principal) -> None` — wraps existing `_is_admin(principal)` with `HTTPException(403)` on False. Keep both: `_is_admin()` returns bool (cheap predicate), `assert_is_superadmin()` raises (FastAPI dependency).
- Reuse pattern: superadmin endpoints declare `_: None = Depends(assert_is_superadmin)`.

### Drill-down audit logging
- Every superadmin call to `/v1/admin/brain/events?team_slug=X` (or any per-team superadmin endpoint) writes to `audit_log` with `action='superadmin_brain_access'`, `actor_user_id=principal.user.id`, `target_team_slug=X`, `endpoint=...`, `query_params=...`. Existing `audit_log` table (Phase 5) — schema sufficient.

### What NOT to add in this scope expansion
- No system health metrics (janitor lag, soft-delete backlog, orphan rows, MCP error rate) — Pack L rejected.
- No top contributors (top users / agents) — Pack L rejected.
- No alerting / paging — out of scope.
- No graph / chart library if vanilla JS doesn't ship one — use simple inline SVG sparklines (lightweight).
- No new dependency on a chart library (Chart.js, D3, etc.) unless absolutely needed — try a 30-line inline SVG sparkline component first.

</deferred>

---

*Phase: 11-brain-monitor-universal-truth-level-inspector-soft-delete*
*Context gathered: 2026-05-14 via direct AskUserQuestion lock (6 questions, no discuss-phase needed)*
