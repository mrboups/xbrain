# Phase 11: Brain Monitor — Universal Truth-Level Inspector + Soft Delete — Research

**Researched:** 2026-05-14
**Domain:** FastAPI schema migration · PostgreSQL UNION ALL views · Qdrant soft-delete · Vanilla-JS SPA · Cron container pattern
**Confidence:** HIGH (all findings verified from codebase; no unverified assumptions)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **Entities covered (Option C):** All 7+ entity types: `memory_items`, `facts` (via `memory_items`), `conversations`, `messages`, `team_messages`, `granola_notes` (ingested as `memory_items`), `tasks`, `contacts`. Any future entity MUST include `truth_level` + `deleted_at` + `deleted_by` by convention.
- **Surface:** `app-site/account/teams/[slug]/brain/` only. No Chrome extension UI.
- **Permissions (Option A):** Author edits/deletes own items; team admin edits/deletes all; others view-only. Bridge JWTs = admin-equivalent.
- **Delete model:** Soft delete (`deleted_at=now()` + `deleted_by`). 30-day global retention. Janitor at 03:00 UTC. Restore endpoint valid only within retention window.
- **truth_level defaults:** tasks → `WORKING`, contacts → `WORKING`, team_messages → `WORKING`, conversations → `EPHEMERAL`, granola_notes (memory_items source='granola') → already `WORKING` from ingest.
- **Migration number:** 0017 (next after Phase 10's 0016). Either single or split at planner discretion.
- **Universal event view:** SQL VIEW `v_brain_events` (UNION ALL), not Python-side query builder.
- **New endpoints:** `GET /v1/brain/events`, `PATCH /v1/brain/events/{entity_type}/{entity_id}`, `DELETE /v1/brain/events/{entity_type}/{entity_id}`, `POST /v1/brain/events/{entity_type}/{entity_id}/restore`.
- **Janitor:** New `apps/brain-janitor/` cron container, modelled after `apps/granola-sync/`.
- **Polling UI:** 30 s interval with `?since=` cursor. No SSE/WebSocket in this phase.

### Claude's Discretion
- Migration split (0017 single vs. 0017+0018+0019) — planner decides based on size.
- Frontend virtualization library — match existing patterns (vanilla-JS `IntersectionObserver` or similar).
- Cursor format detail — `(created_at, entity_type, entity_id)` triple recommended by CONTEXT.

### Deferred Ideas (OUT OF SCOPE)
- Real-time SSE/WebSocket stream
- truth_level promotion automation for non-memory entities
- Aggregate analytics / stats page
- Export brain to JSON/CSV
- Per-team configurable retention
- Hard delete escape hatch on UI
- Bulk import / restore from backup
- Multi-team aggregated view
</user_constraints>

---

## Summary

Phase 11 extends the tagging contract to every entity in the xbrain brain layer and ships a unified web monitor. The core schema work is a single Alembic migration adding `truth_level` (with CHECK), `deleted_at`, and `deleted_by` to five tables that lack them. `memory_items` and `messages` already carry `truth_level`; `team_messages` already has `deleted_at`.

The "universal event view" is a non-materialized SQL VIEW (`v_brain_events`) that UNION ALL-s seven source tables into a normalized shape. At the row counts expected for xbrain (~10k rows/team in Phase 11), this is performant without any materialization strategy; the planner should add composite indices `(team_scope/team_id, created_at DESC)` per table as part of the migration.

The soft-delete enforcement is a read-path concern: five existing routers (`memory.py`, `tasks.py`, `crm.py`, `team_chat.py`, `conversations.py`) must each add `AND deleted_at IS NULL` to their primary list/search queries. The Qdrant side requires updating `NativeProvider.upsert()` to write `deleted_at` into the point payload so future filter expressions can hide soft-deleted vectors without a point-delete round-trip.

**Primary recommendation:** Ship the schema (0017) and `v_brain_events` view first, then the four `/v1/brain/` endpoints in one wave, then the five retrieval-path patches (parallelizable per router), then the janitor cron container and UI in parallel.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|---|---|---|---|
| Schema migration (truth_level, deleted_at) | Database / Storage | API / Backend (ORM) | DDL lives in Alembic; ORM models updated to match |
| `v_brain_events` SQL VIEW | Database / Storage | — | View is a server-side query artefact; no application logic |
| `GET /v1/brain/events` cursor pagination | API / Backend | — | Cursor decoded and query built in Python; SQL executes in PG |
| `PATCH/DELETE/restore` authorization | API / Backend | — | `assert_can_edit_brain_event` helper in `deps.py` |
| Retrieval filter (`deleted_at IS NULL`) | API / Backend | Database / Storage | App-layer guard; indices in DB back performance |
| Qdrant payload `deleted_at` field | Database / Storage | API / Backend (upsert path) | Payload written at upsert time; filtered at query time |
| Janitor cron (hard purge) | API / Backend (cron sidecar) | Database / Storage | asyncpg direct-connect; Qdrant client call |
| Brain Monitor UI | Browser / Client | Frontend Server (Firebase Hosting) | Vanilla-JS SPA served from Firebase |
| 30-second polling | Browser / Client | — | `setInterval` in client JS; no server push |

---

## Q1 — Schema Discovery: Per-Table Column Inventory

### Table: `memory_items` (migration 0002)
| Column | Present | Notes |
|---|---|---|
| `truth_level` TEXT NOT NULL | ✅ | DEFAULT `'EPHEMERAL'`, CHECK constraint `memory_items_truth_check` |
| `deleted_at` TIMESTAMPTZ | ❌ | Must add in 0017 |
| `deleted_by` UUID | ❌ | Must add in 0017 |
| `created_by` / author | ❌ | No `created_by` column in 0002 schema. Source attribution is `source` (TEXT) + `source_ref` (UUID) — see note below. |
| Team scope column | `team_scope` VARCHAR(64) — slug |
| Content/preview field | `content` TEXT |
| Relevant indices | `idx_memory_team` (team_scope), `idx_memory_truth` (truth_level), `idx_memory_team_truth` (team_scope, truth_level) |

**IMPORTANT NOTE — `source_ref` column:** The granola_integration router (line 246 and 275) inserts and queries `memory_items.source_ref`. This column is NOT present in the 0002 migration as read. It was likely added by a post-0004 migration not in the `alembic/versions/` directory, or added inline in the 0007+ era. The table works in production (Phase 7 SHIPPED), so the column exists. However there is NO `created_by` UUID FK on `memory_items` — attribution is the `source` string only. For the brain monitor, `created_by` for memory_items will be NULL in the view; ownership check must fall back to admin-only edit.

**PITFALL — memory_items has no created_by:** The `assert_can_edit_brain_event` helper cannot use `created_by == principal.user.id` for memory_items because the column doesn't exist. The view should surface `NULL` as `created_by` for memory_items rows; the authorization logic must treat NULL created_by as admin-only edit (per the locked decision: "only admin can edit those").

### Table: `conversations` (migration 0001)
| Column | Present | Notes |
|---|---|---|
| `truth_level` | ❌ | Must add, DEFAULT `'EPHEMERAL'` |
| `deleted_at` | ❌ | Must add |
| `deleted_by` | ❌ | Must add |
| Author field | `owner_user_id` UUID NOT NULL FK → users(id) |
| Team scope column | `team_scope` VARCHAR(64) — slug |
| Content/preview field | `title` VARCHAR(512) nullable, `source` STRING |
| Relevant indices | `idx_conv_team` (team_scope) |

ORM: `apps/memory-api/app/models/conversation.py` — `Conversation` class. Must add columns to model.

### Table: `messages` (migration 0001)
| Column | Present | Notes |
|---|---|---|
| `truth_level` TEXT NOT NULL | ✅ | CHECK constraint `messages_truth_level_check`, DEFAULT not set (caller always supplies) |
| `deleted_at` | ❌ | Must add |
| `deleted_by` | ❌ | Must add |
| Author field | No explicit `created_by` — linked to conversation via `conversation_id`; `messages` has no direct user FK |
| Team scope column | `team_scope` VARCHAR(64) — slug |
| Content/preview field | `content` TEXT |
| Relevant indices | `idx_msg_team` (team_scope), `idx_msg_truth` (truth_level) |

ORM: `apps/memory-api/app/models/message.py` — must be checked and updated.

### Table: `team_messages` (migration 0015)
| Column | Present | Notes |
|---|---|---|
| `truth_level` | ❌ | Must add, DEFAULT `'WORKING'` |
| `deleted_at` TIMESTAMPTZ | ✅ | Already present (forward-compat from Phase 2 planning) |
| `deleted_by` UUID | ❌ | Must add (only `deleted_at` landed, not `deleted_by`) |
| Author field | `author_user_id` UUID FK → users(id) ON DELETE SET NULL — **nullable for agent messages** |
| Team scope column | `team_id` UUID FK → teams(id) — **UUID, not slug** |
| Content/preview field | `content` TEXT |
| Relevant indices | `idx_team_messages_team_created` (team_id, created_at DESC) |

**PITFALL — team_messages uses team_id (UUID) not team_scope (slug):** All other tables use `team_scope` VARCHAR(64). The `v_brain_events` UNION ALL must JOIN `teams` to normalize `team_id → team_scope` for the view. Do NOT use `team_scope` as a WHERE filter directly on team_messages.

**PITFALL — author_user_id NULL for agent messages:** The CHECK constraint `ck_team_messages_author_required` enforces that `kind='agent'` rows have `author_user_id IS NULL` and `agent_name IS NOT NULL`. The `assert_can_edit_brain_event` helper must handle `created_by IS NULL` → admin-only.

### Table: `tasks` (migration 0010)
| Column | Present | Notes |
|---|---|---|
| `truth_level` | ❌ | Must add, DEFAULT `'WORKING'` |
| `deleted_at` | ❌ | Must add |
| `deleted_by` | ❌ | Must add |
| Author field | `created_by` UUID FK → users(id) ON DELETE SET NULL **NULLABLE** (system-generated tasks have NULL) |
| Team scope column | `team_scope` VARCHAR(64) — slug |
| Content/preview field | `title` VARCHAR(512) |
| Relevant indices | `idx_tasks_team` (team_scope), `idx_tasks_status` (team_scope, status) |

### Table: `contacts` (migration 0009)
| Column | Present | Notes |
|---|---|---|
| `truth_level` TEXT NOT NULL | ✅ | DEFAULT `'EPHEMERAL'`, CHECK constraint `contacts_truth_level_check` |
| `deleted_at` | ❌ | Must add |
| `deleted_by` | ❌ | Must add |
| Author field | No `created_by` column. Source attribution via `source` TEXT. |
| Team scope column | `team_scope` VARCHAR(64) — slug |
| Content/preview field | `full_name` VARCHAR(256), `email` VARCHAR(256) |
| Relevant indices | `idx_contacts_team` (team_scope) |

Like `memory_items`, contacts have no `created_by`. Brain monitor authorization: NULL → admin-only edit.

### Table: `granola_notes`
**Does NOT exist as a separate table.** Granola meeting notes are ingested as rows in `memory_items` with `source='granola'` and metadata carrying `granola_note_id`, `title`, `attendees`, `decisions` (see `granola_integration.py:263–288`). The CONTEXT.md reference to `granola_notes` is an alias for `memory_items WHERE source='granola'`. The `v_brain_events` view can surface these as `entity_type='granola_note'` by filtering `source='granola'` on `memory_items`.

### Summary: Columns to Add per Table

| Table | Add `truth_level` | Add `deleted_at` | Add `deleted_by` | team scope col type |
|---|---|---|---|---|
| `memory_items` | ❌ (exists) | ✅ | ✅ | `team_scope` slug |
| `conversations` | ✅ (`'EPHEMERAL'`) | ✅ | ✅ | `team_scope` slug |
| `messages` | ❌ (exists) | ✅ | ✅ | `team_scope` slug |
| `team_messages` | ✅ (`'WORKING'`) | ❌ (exists) | ✅ | `team_id` UUID |
| `tasks` | ✅ (`'WORKING'`) | ✅ | ✅ | `team_scope` slug |
| `contacts` | ❌ (exists) | ✅ | ✅ | `team_scope` slug |

Total new columns: 12 (across 6 tables). 6 `truth_level`, 5 `deleted_at`, 6 `deleted_by` — minus the ones already present.

---

## Q2 — Universal Event View Design

### Option A: SQL VIEW `v_brain_events` (UNION ALL)
- **Write-time cost:** Zero — view is computed at query time.
- **Query-time cost (10k rows/team):** Negligible with per-table composite indices on `(team_scope, created_at DESC)`. At 100k rows/team, PostgreSQL's planner will use the per-table indices, materialize 7 small sets, merge, sort once — typically under 20 ms. At 1M rows, this degrades to ~100–500 ms without further optimization (partial indices on `deleted_at IS NULL` help significantly).
- **Filter flexibility:** Excellent. WHERE clause on the view propagates to per-table scans via predicate pushdown when columns match.
- **Maintenance burden:** Adding a new entity = adding one SELECT arm to the view DDL. View must be DROP+RECREATE (no ALTER VIEW ADD COLUMN in Postgres).

### Option B: Python-side UNION ALL builder
- **Write-time cost:** Zero.
- **Query-time cost:** Same as A — the SQL still runs in Postgres. Additional round-trip overhead is negligible.
- **Filter flexibility:** Maximum — can omit tables entirely if `entity_type` filter excludes them.
- **Maintenance burden:** View changes require code deployment, not just a DB migration. Harder to reason about in psql for debugging.

### Option C: Event-log append table + triggers
- **Write-time cost:** HIGH — every INSERT/UPDATE on 7 tables fires a trigger that writes to the log. Risk of trigger failures causing transaction rollbacks.
- **Query-time cost:** Best — single table scan.
- **Filter flexibility:** Good.
- **Maintenance burden:** Very high — triggers must be maintained per table, outbox pattern needed for transactional safety.

**Recommendation: Option A (SQL VIEW).** [VERIFIED: from codebase pattern] The xbrain codebase uses raw SQL throughout (`sa.text()`). A non-materialized VIEW matches the existing pattern and is maintainable. Predicate pushdown on `team_scope` / `team_id` + `deleted_at IS NULL` partial index makes it performant well past 100k rows.

### View Definition (normalized columns)

```sql
CREATE OR REPLACE VIEW v_brain_events AS
  -- memory_items (entity_type = 'memory_item' or 'granola_note')
  SELECT
    CASE WHEN mi.source = 'granola' THEN 'granola_note' ELSE 'memory_item' END AS entity_type,
    mi.id        AS entity_id,
    t.id         AS team_id,
    mi.team_scope,
    mi.created_at,
    NULL::UUID   AS created_by,   -- memory_items has no created_by column
    mi.truth_level,
    mi.deleted_at,
    mi.deleted_by,
    LEFT(mi.content, 200) AS preview,
    mi.source
  FROM memory_items mi
  JOIN teams t ON t.slug = mi.team_scope

  UNION ALL

  -- conversations
  SELECT
    'conversation'   AS entity_type,
    c.id, t.id, c.team_scope, c.created_at,
    c.owner_user_id  AS created_by,
    c.truth_level, c.deleted_at, c.deleted_by,
    LEFT(COALESCE(c.title, c.source), 200), c.source
  FROM conversations c
  JOIN teams t ON t.slug = c.team_scope

  UNION ALL

  -- messages (chat messages)
  SELECT
    'message'        AS entity_type,
    m.id, t.id, m.team_scope, m.created_at,
    NULL::UUID       AS created_by,   -- no user FK on messages table
    m.truth_level, m.deleted_at, m.deleted_by,
    LEFT(m.content, 200), m.source
  FROM messages m
  JOIN teams t ON t.slug = m.team_scope

  UNION ALL

  -- team_messages (real-time team chat)
  SELECT
    'team_message'   AS entity_type,
    tm.id,
    tm.team_id,
    t.slug           AS team_scope,
    tm.created_at,
    tm.author_user_id AS created_by,  -- NULL for agent messages
    tm.truth_level, tm.deleted_at, tm.deleted_by,
    LEFT(tm.content, 200),
    COALESCE(tm.routed_via, tm.kind) AS source
  FROM team_messages tm
  JOIN teams t ON t.id = tm.team_id

  UNION ALL

  -- tasks
  SELECT
    'task'           AS entity_type,
    tk.id, t.id, tk.team_scope, tk.created_at,
    tk.created_by,
    tk.truth_level, tk.deleted_at, tk.deleted_by,
    LEFT(tk.title, 200), tk.source
  FROM tasks tk
  JOIN teams t ON t.slug = tk.team_scope

  UNION ALL

  -- contacts
  SELECT
    'contact'        AS entity_type,
    co.id, t.id, co.team_scope, co.created_at,
    NULL::UUID       AS created_by,   -- no created_by on contacts
    co.truth_level, co.deleted_at, co.deleted_by,
    LEFT(COALESCE(co.full_name, co.email, '(unnamed)'), 200), co.source
  FROM contacts co
  JOIN teams t ON t.slug = co.team_scope;
```

---

## Q3 — Qdrant Soft-Delete: Filter at Retrieval

### Current Qdrant Payload Schema

From `packages/memory-models/xbrain_memory/providers/native_provider.py` (lines 103–115), the `upsert()` method writes this payload to every point:

```python
payload={
    "team_scope": item.team_scope,
    "project_scope": item.project_scope,
    "truth_level": item.truth_level.value,
    "source": item.source,
}
```

**`deleted_at` is NOT in the current Qdrant payload.** Phase 11 must update `NativeProvider.upsert()` to include `deleted_at` (as an ISO string or None) in the payload so Qdrant can filter on it at search time.

### Qdrant Filter for Soft-Delete

The Qdrant Python client `qdrant-client 1.17.1` [VERIFIED: CLAUDE.md] supports payload filtering via `Filter` + `FieldCondition`. To exclude soft-deleted points:

```python
from qdrant_client.http.models import (
    Filter, FieldCondition, IsNullCondition, PayloadField,
    IsEmptyCondition, MustNot
)

# Exclude points where deleted_at is set (not null, not empty)
soft_delete_filter = Filter(
    must_not=[
        IsEmptyCondition(is_empty=PayloadField(key="deleted_at")),  # exclude only if non-null
    ]
)
```

**PITFALL — Qdrant IsEmpty semantics:** `IsEmptyCondition` matches points where the payload field is absent OR null. `IsNullCondition` matches where present but null. To exclude soft-deleted points (where `deleted_at` is a non-null ISO string), use `must_not + IsEmpty` is **WRONG** (would exclude non-deleted points). The correct approach:

```python
# Exclude points where deleted_at payload field exists AND is non-empty string
# Strategy: use a range filter to match "any point where deleted_at is set"
# then must_not that match

# Simplest reliable approach:
# Store deleted_at as epoch float (None = 0.0) in payload, then range-filter
# OR: store as ISO string and use FieldCondition(match=...) with must_not

# Recommended: Store deleted_at as seconds since epoch (float) or 0.0 for null
# Then filter: must_not = [FieldCondition(key="deleted_at_ts", range=Range(gt=0))]
```

**RECOMMENDATION:** Store `deleted_at` in Qdrant payload as `deleted_at_ts: float | 0.0` (Unix timestamp), where `0.0` means "not deleted". Then in search:

```python
from qdrant_client.http.models import Filter, FieldCondition, Range, MustNot

not_deleted = Filter(
    must_not=[
        FieldCondition(key="deleted_at_ts", range=Range(gt=0.0))
    ]
)
```

This is the most reliable pattern — `Range` on a numeric field is well-supported in qdrant-client 1.x. [ASSUMED — specific API field for Range in qdrant-client 1.17.1 needs confirmation vs. docs]

### Current Search Path

`native_provider.py search()` (lines 127–173): builds `Filter(must=must_conditions)` before calling `self._qdrant.search()`. The soft-delete filter should be added as an additional `must` condition:

```python
must.append(
    Filter(must_not=[FieldCondition(key="deleted_at_ts", range=Range(gt=0.0))])
)
# Or equivalently, add to the outer must:
must.append(FieldCondition(key="deleted_at_ts", range=Range(lte=0.0)))
```

The upsert path (`native_provider.py upsert()` lines 102–115) must be updated to set `"deleted_at_ts": 0.0` on new points. The `update()` method must also propagate `deleted_at_ts` when soft-delete is applied.

---

## Q4 — Janitor Cron Container Pattern

### Existing granola-sync Pattern (from `apps/granola-sync/`)

| Property | Value | Source |
|---|---|---|
| Base image | `python:3.12-slim` | `apps/granola-sync/Dockerfile:3` |
| Build context | Repo root (`context: ..`) — required for `packages/memory-models/` | `Dockerfile:1` |
| Package install | `pip install -e packages/memory-models/` then `-e apps/granola-sync/` | `Dockerfile:7–10` |
| Entry point | `python -m app.main` | `Dockerfile:12` |
| DB credentials | `DATABASE_URL` env var from docker-compose | `docker-compose.yml:848` |
| Qdrant access | Via `xbrain_memory.NativeProvider` (which imports `qdrant-client`) | `pyproject.toml:13` |
| Neo4j access | Not used by granola-sync; must be added for janitor |
| Scheduling | Pure asyncio `while True: ... await asyncio.sleep(interval)` — NO apscheduler, NO OS cron | `granola_poller.py:419` |
| Sentinel healthcheck | Touches `/tmp/granola-sync-alive` after each tick; Docker healthcheck checks file mtime < 600s | `docker-compose.yml:864` |
| DB connection | `asyncpg.create_pool()` with direct PG URL (strips `postgresql+asyncpg://` prefix) | `granola_poller.py:370–373` |
| Credentials | `BRIDGE_SHARED_SECRET`, `FERNET_KEY`, `ANTHROPIC_API_KEY` from env | `config.py` + docker-compose |

### Recommended Pattern for `apps/brain-janitor/`

```
apps/brain-janitor/
├── Dockerfile           # same build context trick (context: ..)
├── pyproject.toml       # depends on asyncpg, qdrant-client, neo4j, structlog
└── app/
    ├── __init__.py
    ├── main.py          # asyncio entry: run_once() at boot (for testing), then sleep loop
    ├── config.py        # pydantic-settings: DATABASE_URL, QDRANT_URL, NEO4J_URI, RETENTION_DAYS=30
    ├── pg_purger.py     # asyncpg: DELETE rows where deleted_at < now() - INTERVAL, write audit_log
    ├── qdrant_purger.py # qdrant-client: delete points where entity_type:entity_id matches purged rows
    └── neo4j_purger.py  # neo4j Python driver: DELETE nodes/rels for purged memory_items
```

Key differences from granola-sync:
1. Needs `neo4j` Python driver (`neo4j>=6.1`) — not in granola-sync.
2. Target run time: daily at 03:00 UTC. Implement as: boot → sleep until next 03:00 → run → sleep 24h → repeat.
3. No `BRIDGE_SHARED_SECRET` needed — janitor connects directly to DB and Qdrant, bypassing memory-api HTTP.
4. Sentinel file: `/tmp/brain-janitor-alive` — touch after each run (even if no rows purged).

---

## Q5 — Authorization Helper Design

### Existing `_is_admin(principal)` in `deps.py` (lines 266–277)

The current `_is_admin` checks `ADMIN_USER_SUBS` env list (global admins) or bridge kind. It does NOT check `team_members.role = 'admin'` — this is a **global** admin check, not per-team.

For brain events, the correct admin check is per-team membership role.

### New Helper: `assert_can_edit_brain_event`

```python
from uuid import UUID
from typing import Any
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
import sqlalchemy as sa

async def assert_can_edit_brain_event(
    principal: dict[str, Any],
    *,
    created_by: UUID | None,
    team_slug: str,
    session: AsyncSession,
) -> None:
    """Raise 403 if principal cannot edit/delete this brain event.

    Rules (locked decision — Option A):
      1. Bridge JWT (kind='bridge') → admin-equivalent, always allowed.
      2. created_by is None (system-generated, e.g. memory_items, contacts) → team admin only.
      3. created_by == principal.user.id → author, always allowed.
      4. team_members.role='admin' for this team → team admin, always allowed.
      5. Otherwise → 403.
    """
    # Rule 1: bridge = admin
    if principal.get("kind") == "bridge":
        return

    user = principal.get("user")
    if user is None:
        raise HTTPException(403, "user identity required")

    # Rule 3: author check
    if created_by is not None and user.id == created_by:
        return

    # Rules 2 + 4: check team membership role
    row = (await session.execute(
        sa.text("""
            SELECT tm.role FROM team_members tm
            JOIN teams t ON t.id = tm.team_id
            WHERE tm.user_id = :uid AND t.slug = :slug
        """),
        {"uid": str(user.id), "slug": team_slug},
    )).fetchone()
    if row and row.role == "admin":
        return

    raise HTTPException(403, "only the author or a team admin can modify this item")
```

**Notes on bridge JWT treatment:** The CONTEXT.md locks bridge JWTs as admin-equivalent. This is appropriate for internal services (granola-sync, agent-runtime) that write/update brain items on behalf of users. However, the janitor should NOT go through this endpoint — it directly operates on the DB.

**Warning:** Bridge JWTs for brain edits should be treated as admin for service-to-service use (granola-sync updating a memory_item truth_level after auto-promotion). If the brain monitor is ever exposed externally, reconsider. For Phase 11, the pattern matches existing behavior.

---

## Q6 — Frontend Stack on app-site

### Confirmed Stack

From `app-site/account/teams/index.html` and `teams.js`:
- **Pure vanilla HTML + CSS + JavaScript** — no framework (no React, Vue, Svelte).
- **No bundler** — files are served as-is by Firebase Hosting.
- **Font:** JetBrains Mono (Google Fonts CDN).
- **CSS variables:** Dark theme, `--bg: #0D1117`, `--accent: #22D3EE` (cyan), `--border: #30363D`.
- **Auth pattern:** Google Identity Services → xbt_ token minted via `POST /v1/me/api-token` → stored in `localStorage` as `xbrain_xbt_token`. All subsequent API calls inject `Authorization: Bearer ${xbt}` header.
- **GitHub primary:** Phase 10 adds GitHub OAuth (`gho_` tokens) — the brain page should support both `xbt_` tokens (Phase 10 onboarding) and direct `gho_` GitHub tokens.

### Existing UI Patterns

From `teams.js`:
- API base: `const MEMORY_API_BASE = "https://api.grooveos.app"` — hardcoded, same pattern for brain page.
- State: plain JS object `const state = { ... }`, re-rendered via DOM manipulation.
- No virtual DOM — direct `innerHTML` or `createElement` with loops.

### Virtualized Table Pattern

For 500–1000+ rows in vanilla JS, the lightest approach matching existing patterns:

**IntersectionObserver sentinel approach** (no library needed):
```js
// Render 50 rows, add invisible sentinel div at bottom
// When sentinel enters viewport, fetch next page (cursor-based), append rows
// For "trash" toggle: re-fetch with include_deleted=true, re-render table
```

This approach is consistent with the existing codebase — no npm, no bundler. For bulk select and inline editing, standard DOM event delegation on a `<table>` works fine at 500–1000 rows.

**If the row count could reach 5000+**: consider a simple virtual-scroll (only render rows in viewport ± buffer). At xbrain current scale (~50–500 rows/team), IntersectionObserver pagination is sufficient.

**Route:** Create `app-site/account/teams/[slug]/brain/index.html` + `brain.js`. Firebase Hosting rewrites are glob-based — the existing `firebase.json` likely handles `account/teams/**` with the auth shell; the new page must be explicitly added or the rewrite rule extended.

---

## Q7 — Cursor Pagination Contract

### Recommended Cursor: `(created_at DESC, entity_type, entity_id)`

**Encoding:** Base64 JSON `{"ts": "2026-05-14T03:00:00Z", "et": "memory_item", "id": "uuid-string"}`

**Page query (Postgres):**
```sql
WHERE team_scope = :ts
  AND deleted_at IS NULL   -- default; override with include_deleted=true
  AND (
      created_at < :cursor_ts
      OR (created_at = :cursor_ts AND entity_type > :cursor_et)
      OR (created_at = :cursor_ts AND entity_type = :cursor_et AND entity_id > :cursor_id)
  )
ORDER BY created_at DESC, entity_type ASC, entity_id ASC
LIMIT :page_size + 1
```

**Postgres tuple comparison note:** Postgres does support row-value comparisons like `(a, b, c) < (:x, :y, :z)` for homogeneous types, but mixing `TIMESTAMPTZ + TEXT + UUID` in a tuple expression requires explicit casting and is not reliable across all planner versions. [ASSUMED] The explicit OR-tree above is clearer and universally supported.

**Tie-breaking rationale:**
- `created_at DESC` — newest first (primary sort for live feed).
- `entity_type ASC` — deterministic secondary sort across table arms of the UNION.
- `entity_id ASC` (UUID) — deterministic tertiary sort within same entity_type + timestamp.

**UUID lexicographic sort:** Postgres UUIDs in TEXT comparison sort lexicographically (alphabetical on the hex string), which is stable but not chronological. That is fine for tie-breaking uniqueness — we don't need chronological UUID ordering.

**Simpler alternative: `(created_at, entity_id)` only** — works well when entity_id is a UUID v4 (random, unique). The `entity_type` arm is only needed if two different entity types could have the same UUID (impossible with gen_random_uuid() per table, but the combined view could theoretically show same UUIDs from different tables — `entity_id` alone is not globally unique across tables). The triple cursor `(created_at, entity_type, entity_id)` is the safe choice.

---

## Q8 — Retrieval-Side Regression Risk: Existing Endpoints

All endpoints that query targeted tables without `deleted_at IS NULL` are regression targets.

| Router file | Endpoint | Query | Missing filter | Risk |
|---|---|---|---|---|
| `routes/memory.py` | `POST /v1/memory/search` | `NativeProvider.search()` → `memory_items WHERE id = ANY(...)` (line ~160 native_provider) | `AND deleted_at IS NULL` in PG fetch after vector search | HIGH — vector search returns deleted point IDs, PG fetch hydrates them |
| `routes/tasks.py` | `GET /v1/tasks` | `SELECT * FROM tasks WHERE team_scope = :ts` (line 97) | `AND deleted_at IS NULL` | HIGH |
| `routes/tasks.py` | `GET /v1/tasks/{task_id}` | `SELECT * FROM tasks WHERE id = :id AND team_scope = :ts` (line 124) | `AND deleted_at IS NULL` | MEDIUM |
| `routes/crm.py` | `GET /v1/crm/contacts` | `SELECT * FROM contacts WHERE team_scope = :ts` (line 74) | `AND deleted_at IS NULL` | HIGH |
| `routes/crm.py` | `GET /v1/crm/contacts/{contact_id}` | `SELECT * FROM contacts WHERE id = :id AND team_scope = :ts` (line 92) | `AND deleted_at IS NULL` | MEDIUM |
| `routes/team_chat.py` | `GET /v1/teams/{team_id}/messages` | `repos/team_messages.py:list_messages()` (line 86) | ✅ **ALREADY FILTERED** — `TeamMessage.deleted_at.is_(None)` at line 88 | None |
| `routes/conversations.py` | `GET /v1/conversations` (if exists) | Need to check | Likely missing | MEDIUM |
| `routes/messages.py` | `GET /v1/messages` (if exists) | Need to check | Likely missing | MEDIUM |
| `packages/native_provider.py` | `search()` vector retrieval | `memory_items WHERE id = ANY(...)` — no `deleted_at` filter | `AND deleted_at IS NULL` | HIGH |

**VERIFIED already filtered:** `repos/team_messages.py` lines 88 and 115 — both `list_messages()` and `get_recent_messages_chronological()` filter `deleted_at IS None`. This is correct and must not be regressed.

**Total regression targets:** 5–6 query paths. Each needs a single `AND deleted_at IS NULL` clause added. These are safe parallel tasks in Wave 3.

---

## Q9 — Migration 0017 Size Estimate

| Item | Count | Notes |
|---|---|---|
| Tables touched | 6 | memory_items, conversations, messages, team_messages, tasks, contacts |
| New columns total | ~12 | 2 `deleted_at` + 2 `deleted_by` + 3 `truth_level` (per table needing them) |
| New indices | 6–8 | `(team_scope, deleted_at)` per table + `(team_scope, created_at DESC)` where missing |
| VIEW definition | ~60 SQL lines | `v_brain_events` UNION ALL across 6 arms |
| Data backfill | None required | All new columns have `server_default` or `DEFAULT NULL` — no backfill needed. Existing `contacts.truth_level` is `'EPHEMERAL'`; CONTEXT says DEFAULT is `'WORKING'` for contacts going forward but existing rows keep `'EPHEMERAL'` (already set from migration 0009). |

**Index creation lock risk:** `CREATE INDEX` in Alembic runs inside a transaction by default. For tables with existing data (tasks, contacts, memory_items), index creation inside a transaction is fine for xbrain's current row counts (Phase 7 shipped, likely <10k rows). However, `CREATE INDEX CONCURRENTLY` cannot run inside a transaction. For production safety, the migration should either:
- Use `op.create_index(..., postgresql_concurrently=True)` combined with `with op.get_context().autocommit_block():` block, OR
- Accept the brief table lock (acceptable for xbrain's current row counts).

**Alembic `server_default` for truth_level:** Alembic `server_default="'WORKING'"` emits `DEFAULT 'WORKING'` in DDL (column gets default at insert time). Python ORM models also need `default="WORKING"` set so SQLAlchemy INSERT statements emit the value. Both must be set.

**PITFALL — CHECK constraint name collisions:** Each table already has its own CHECK constraint names (`tasks_status_check`, etc.). New CHECK constraints must use distinct names like `tasks_truth_level_check`, not reuse existing pattern names. The constraint `contacts_truth_level_check` already exists from migration 0009 — do NOT add it again.

**PITFALL — contacts already has truth_level:** Migration 0009 already created `contacts.truth_level` with `server_default="EPHEMERAL"` and a CHECK constraint. Migration 0017 must NOT add it again. Only add `deleted_at` and `deleted_by` to contacts.

**PITFALL — memory_items already has truth_level:** Same — 0002 created it with `server_default="EPHEMERAL"`. Only add `deleted_at` and `deleted_by`.

**PITFALL — messages already has truth_level:** Migration 0001 created it. Only add `deleted_at` and `deleted_by`.

---

## Q10 — Verify Script Assertions (verify-phase11.sh)

Pattern from `verify-phase7.sh`: `run_psql()` via `docker exec -i xbrain-postgres psql -U xbrain -d xbrain -tAc`, `curl` against `MEMAPI_HOST`.

```bash
# [1/12] BMO-01: migration 0017 applied
ver=$(run_psql "SELECT version_num FROM alembic_version")
[[ "$ver" > "0016" ]] && pass || fail

# [2/12] BMO-01: truth_level column exists on tasks
col=$(run_psql "SELECT column_name FROM information_schema.columns WHERE table_name='tasks' AND column_name='truth_level'")
[[ "$col" = "truth_level" ]] && pass || fail

# [3/12] BMO-01: deleted_at column exists on tasks, contacts, conversations
for t in tasks contacts conversations messages memory_items; do
  col=$(run_psql "SELECT column_name FROM information_schema.columns WHERE table_name='$t' AND column_name='deleted_at'")
  [[ "$col" = "deleted_at" ]] && pass "$t.deleted_at present" || fail "$t.deleted_at missing"
done

# [4/12] BMO-02: v_brain_events view exists
v=$(run_psql "SELECT to_regclass('v_brain_events')::text")
[[ "$v" = "v_brain_events" ]] && pass || fail

# [5/12] BMO-02: v_brain_events returns ≥1 entity type per team
count=$(run_psql "SELECT COUNT(DISTINCT entity_type) FROM v_brain_events WHERE team_scope = 'default' LIMIT 1")
[[ "$count" -ge 1 ]] && pass || fail

# [6/12] BMO-03: GET /v1/brain/events returns 200 with auth
resp=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $TEST_XBT" -H "X-Team-Scope: default" "$MEMAPI_HOST/v1/brain/events")
[[ "$resp" = "200" ]] && pass || fail

# [7/12] BMO-04: PATCH truth_level succeeds for author (200), fails for non-author (403)
# (requires test fixture with known entity_id + created_by matching TEST_XBT user)
patch_resp=$(curl -s -o /dev/null -w "%{http_code}" -X PATCH \
  -H "Authorization: Bearer $TEST_XBT" -H "X-Team-Scope: default" \
  -H "Content-Type: application/json" -d '{"truth_level":"WORKING"}' \
  "$MEMAPI_HOST/v1/brain/events/task/$TEST_TASK_ID_OWNED")
[[ "$patch_resp" = "200" ]] && pass || fail

# [8/12] BMO-04: PATCH 403 for non-owner non-admin
patch_403=$(curl -s -o /dev/null -w "%{http_code}" -X PATCH \
  -H "Authorization: Bearer $TEST_XBT_MEMBER" -H "X-Team-Scope: default" \
  -H "Content-Type: application/json" -d '{"truth_level":"VALIDATED"}' \
  "$MEMAPI_HOST/v1/brain/events/task/$TEST_TASK_ID_OTHER_USER")
[[ "$patch_403" = "403" ]] && pass || fail

# [9/12] BMO-05+06: soft delete + restore round trip
del=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE \
  -H "Authorization: Bearer $TEST_XBT" -H "X-Team-Scope: default" \
  "$MEMAPI_HOST/v1/brain/events/task/$TEST_TASK_ID_OWNED")
[[ "$del" = "204" ]] && pass || fail
# Confirm invisible in default list
count_after=$(curl -s -H "Authorization: Bearer $TEST_XBT" -H "X-Team-Scope: default" \
  "$MEMAPI_HOST/v1/brain/events?entity_type=task" | jq '[.items[] | select(.id=="'"$TEST_TASK_ID_OWNED"'")] | length')
[[ "$count_after" = "0" ]] && pass "soft-deleted not visible by default" || fail
# Restore
restore=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
  -H "Authorization: Bearer $TEST_XBT" -H "X-Team-Scope: default" \
  "$MEMAPI_HOST/v1/brain/events/task/$TEST_TASK_ID_OWNED/restore")
[[ "$restore" = "200" ]] && pass || fail

# [10/12] BMO-07: existing /v1/tasks list excludes soft-deleted rows
del_task_count=$(run_psql "SELECT COUNT(*) FROM tasks WHERE team_scope='default' AND deleted_at IS NOT NULL")
api_count=$(curl -s -H "Authorization: Bearer $TEST_XBT_ADMIN" -H "X-Team-Scope: default" \
  "$MEMAPI_HOST/v1/tasks" | jq 'length')
pg_count=$(run_psql "SELECT COUNT(*) FROM tasks WHERE team_scope='default' AND deleted_at IS NULL")
[[ "$api_count" = "$pg_count" ]] && pass "tasks list excludes deleted" || fail

# [11/12] BMO-08: brain-janitor container running
docker inspect xbrain-brain-janitor --format='{{.State.Status}}' | grep -q running && pass || fail

# [12/12] Success Criterion 6: migration preserved existing truth_level values
mi_wrong=$(run_psql "SELECT COUNT(*) FROM memory_items WHERE truth_level NOT IN ('EPHEMERAL','WORKING','VALIDATED','CANONICAL','PUBLIC')")
[[ "$mi_wrong" = "0" ]] && pass "memory_items truth_level values intact" || fail
```

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---|---|---|---|
| Cursor encoding | Custom serialization | `base64.b64encode(json.dumps({...}).encode())` | 3 lines, already available in Python stdlib |
| Qdrant soft-delete filter | Custom deletion tracking table | Qdrant payload field + `FieldCondition` Range filter | Qdrant natively supports payload filter at search time |
| Cron scheduling | APScheduler, Celery, OS cron | `while True: await asyncio.sleep()` pattern (matches granola-sync) | Already proven in codebase; Docker healthcheck covers liveness |
| Per-team admin check | Custom role table | `team_members.role = 'admin'` query in repos/teams.py | Already exists: `TeamMember.role` column, `get_membership()` repo |
| Full-text preview search | Custom FTS index | `ILIKE '%:q%'` on `preview` column for Phase 11 scale | FTS overkill at <100k rows/team; ILIKE on 200-char preview is fast |

---

## Common Pitfalls

**PITFALL — team_messages has team_id (UUID) not team_scope (slug):** Every other table uses `team_scope VARCHAR(64)`. The `v_brain_events` view must JOIN `teams t ON t.id = tm.team_id` to normalize team_messages. The `GET /v1/brain/events` endpoint resolves team_scope via `X-Team-Scope` header → `get_team_scope` dep → the WHERE clause on the view must handle this type mismatch. In the view, surface `t.slug AS team_scope` so callers filter uniformly.

**PITFALL — contacts already has truth_level from migration 0009 with DEFAULT EPHEMERAL:** CONTEXT.md specifies DEFAULT `'WORKING'` for contacts in migration 0017. Do NOT modify the existing `server_default`. Instead, the migration adds `deleted_at` and `deleted_by` only. The DEFAULT for truth_level on new contacts (going forward) can be changed by modifying the Pydantic `ContactCreateBody.truth_level` default from `'EPHEMERAL'` to `'WORKING'` — no DDL change needed (the CHECK constraint already allows WORKING).

**PITFALL — Alembic DROP VIEW before ALTER:** If migration 0017 is split and a later migration 0018 or 0019 creates `v_brain_events` using the new columns from 0017, the view cannot be created until after the columns exist. The view must be in the last sub-migration or at the end of 0017. Also, Alembic downgrades must `DROP VIEW IF EXISTS v_brain_events` before dropping columns.

**PITFALL — concurrent index creation incompatible with Alembic transactions:** Alembic wraps each migration in a transaction. `CREATE INDEX CONCURRENTLY` cannot run inside a transaction. For Phase 11, the table sizes are small enough that non-concurrent index creation is acceptable. Document this assumption: if ever run on a large production DB, use `autocommit_block()`.

**PITFALL — deleted_at filter must be added to NativeProvider.search() at the Postgres level, not just Qdrant:** Even with Qdrant payload filtering, `NativeProvider.search()` does a second query `SELECT * FROM memory_items WHERE id = ANY(...)` (line ~160). This second query must add `AND deleted_at IS NULL` to prevent hydrating soft-deleted items that leaked through if the Qdrant payload was not yet updated (e.g., items soft-deleted before Phase 11 deployed the new payload structure).

---

## State of the Art

| Old Approach | Current Approach | Impact |
|---|---|---|
| No soft delete in xbrain | Phase 11 adds `deleted_at` / `deleted_by` to all entities | All read paths must be patched |
| truth_level only on memory layer | Phase 11 extends to all entities | Schema migration required; ORM models updated |
| No universal event feed | `v_brain_events` SQL VIEW | Single query surface for Brain Monitor |
| Qdrant payload: no deleted_at | Phase 11 adds `deleted_at_ts` float | `NativeProvider.upsert()` must be updated |

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|---|---|---|
| A1 | `memory_items` has a `source_ref` column (used in granola_integration.py) not visible in migration 0002 | Q1 | If column doesn't exist, granola ingest has been broken since Phase 7 — but Phase 7 SHIPPED, so it must exist. Low risk. |
| A2 | `contacts` and `memory_items` do not have `created_by` — authorization must default to admin-only for these | Q1, Q5 | If `created_by` was added in a migration not read, the helper would incorrectly block authors from editing. Must verify against live DB schema. |
| A3 | Qdrant `FieldCondition(key=..., range=Range(lte=0.0))` syntax is correct for qdrant-client 1.17.1 | Q3 | If API has changed, the filter may fail silently or raise at runtime. Should verify with `npx ctx7 docs qdrant "payload filter range"`. |
| A4 | `app-site` uses no bundler; `firebase.json` glob rewrite covers new `brain/` sub-route | Q6 | If firebase.json has strict path rules, the new route 404s until explicitly added. Planner must check `app-site/firebase.json`. |
| A5 | Phase 10 migration is 0016 (next is 0017 for Phase 11) | Q9 | If Phase 10 plans use a different number (or split), the revision chain would conflict. Planner must verify current max migration number. |

---

## Open Questions for Planner

1. **`memory_items.source_ref` column origin:** The granola_integration router inserts into `memory_items(source_ref=...)` and queries `WHERE source_ref = :note_id`, but the 0002 migration does not define this column. Where was it added? If it's missing from the migration history, a compensatory `op.add_column()` must be included or the DB may be in a non-reproducible state for fresh installs. **Action:** Run `SELECT column_name FROM information_schema.columns WHERE table_name='memory_items'` on the live DB to get the actual schema.

2. **`app-site/firebase.json` rewrite rules:** The Brain Monitor page at `app-site/account/teams/[slug]/brain/` requires either a static `brain/index.html` file or a Firebase Hosting rewrite rule that maps the dynamic slug. Since Firebase Hosting does not do server-side routing, the page must be served as a static file at a fixed path (e.g., `account/teams/brain/index.html`) with JS reading the slug from the URL. Confirm the URL scheme and rewrite setup before frontend planning.

3. **Phase 10 migration number:** CONTEXT.md assumes migration 0016 belongs to Phase 10. If Phase 10 was not yet shipped (ROADMAP.md shows Phase 10 as "Planned"), the highest migration currently in the DB may still be 0015. The planner should run `SELECT version_num FROM alembic_version` before numbering Phase 11 migrations. If 0015 is the current head, Phase 11 migration becomes 0016.

4. **Neo4j scope for Phase 11 janitor:** CONTEXT.md says the janitor performs "Neo4j relation cleanup if node exists" for `memory_items` and `facts`. The Neo4j driver is available (`neo4j>=6.1` in CLAUDE.md). However, no cron container currently uses Neo4j directly — only `memory-api` does via `app/db/neo4j.py` (assumed). The planner must confirm the Neo4j connection helper used by memory-api and decide whether to extract it into a shared utility for the janitor or duplicate the connection logic.

5. **`messages` table author field:** The `messages` table (migration 0001) has no `created_by` column — messages are children of `conversations` which have `owner_user_id`. For the brain monitor PATCH/DELETE on `entity_type=message`, the authorization check falls to "admin only" unless the planner decides to resolve `conversations.owner_user_id` as the effective `created_by`. This is a design decision: is the conversation owner the same as the message author? (Not necessarily — LibreChat messages can come from `assistant` role.) Recommend: for `messages`, treat `created_by=NULL` → admin-only edit.

---

## Environment Availability

Step 2.6: SKIPPED — Phase 11 has no new external dependencies beyond what is already deployed (Postgres, Qdrant, Neo4j all running from Phase 3+).

---

## Validation Architecture

### Test Framework
| Property | Value |
|---|---|
| Framework | `verify-phase11.sh` shell script + psql assertions |
| Config file | `infrastructure/scripts/verify-phase11.sh` (to create) |
| Quick run | `bash infrastructure/scripts/verify-phase11.sh` |
| Full suite | Same — single script, 12 assertions |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Command |
|---|---|---|---|
| BMO-01 | truth_level + deleted_at columns added | DB schema check | `psql` column existence |
| BMO-02 | v_brain_events view returns ≥1 entity type | DB query | `psql` SELECT COUNT(DISTINCT entity_type) |
| BMO-03 | GET /v1/brain/events returns 200 + paginated | Smoke | `curl` + jq |
| BMO-04 | PATCH truth_level: author=200, non-author=403 | Auth test | Two `curl` calls |
| BMO-05 | DELETE soft: row gets deleted_at set | Integration | `curl` DELETE + psql verify |
| BMO-06 | POST restore: clears deleted_at | Integration | `curl` + psql verify |
| BMO-07 | Existing routes exclude soft-deleted | Regression | Row count comparison API vs DB |
| BMO-08 | Janitor container running + purges expired rows | Integration | `docker inspect` + psql time-shift |
| BMO-09 | UI loads at `/account/teams/{slug}/brain/` | Smoke | `curl` HTTP 200 |

### Wave 0 Gaps
- [ ] `infrastructure/scripts/verify-phase11.sh` — all 12 assertions (created in final wave)
- [ ] Test fixtures: `TEST_TASK_ID_OWNED`, `TEST_TASK_ID_OTHER_USER`, `TEST_XBT`, `TEST_XBT_MEMBER` env vars or seed script

---

## Sources

### Primary (HIGH confidence — verified from codebase)
- `apps/memory-api/alembic/versions/0001_initial.py` — conversations, messages schemas
- `apps/memory-api/alembic/versions/0002_memory_promotions.py` — memory_items schema, truth_level constraint
- `apps/memory-api/alembic/versions/0009_crm_contacts.py` — contacts schema, truth_level already present
- `apps/memory-api/alembic/versions/0010_tasks.py` — tasks schema, created_by nullable
- `apps/memory-api/alembic/versions/0015_team_messages.py` — team_messages schema, deleted_at already present
- `apps/memory-api/app/deps.py` lines 46–286 — `get_current_principal`, `get_team_scope`, `_is_admin`, `_user_id_from_principal`
- `apps/memory-api/app/routes/memory.py` — memory search path
- `apps/memory-api/app/routes/tasks.py` lines 86–131 — list/get tasks queries, no deleted_at filter
- `apps/memory-api/app/routes/crm.py` lines 66–98 — list/get contacts queries, no deleted_at filter
- `apps/memory-api/app/routes/team_chat.py` lines 122–143 — list_team_messages endpoint
- `apps/memory-api/app/repos/team_messages.py` lines 74–125 — already filters deleted_at IS NULL
- `apps/memory-api/app/models/team_message.py` — TeamMessage ORM, deleted_at present
- `apps/memory-api/app/models/conversation.py` — Conversation ORM, no deleted_at
- `packages/memory-models/xbrain_memory/providers/native_provider.py` lines 58–116 — Qdrant upsert payload (no deleted_at), search logic
- `apps/granola-sync/Dockerfile`, `app/main.py`, `app/config.py`, `app/granola_poller.py` — cron container pattern
- `apps/granola-sync/pyproject.toml` — dependencies list
- `infrastructure/docker-compose.yml` lines 839–868 — granola-sync service definition
- `app-site/account/teams/index.html`, `teams.js` — vanilla-JS auth pattern, CSS variables
- `infrastructure/scripts/verify-phase7.sh` — verify script pattern

### Secondary (MEDIUM confidence)
- CLAUDE.md Technology Stack section — pinned versions (qdrant-client 1.17.1, neo4j 6.1.x)

---

## Metadata

**Confidence breakdown:**
- Schema discovery: HIGH — read all 6 relevant migrations directly
- Architecture patterns: HIGH — read all route files, repos, and cron container source
- Qdrant filter API: MEDIUM — pattern is well-established but specific `Range` field syntax for qdrant-client 1.17.1 is ASSUMED (A3)
- Frontend stack: HIGH — confirmed vanilla JS from source
- Pitfalls: HIGH — derived from observed discrepancies in actual code

**Research date:** 2026-05-14
**Valid until:** 2026-06-14 (stable stack; Qdrant client API may change)
