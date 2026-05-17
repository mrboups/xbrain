# Brain Monitor — Architecture (Phase 11)

Internal dev KB. Written 2026-05-17 alongside plan 11-09 (Phase 11 shipping plan).
Audience: future devs touching the Brain Monitor or extending it.

Public user-facing docs live at `marketing-site/docs/brain-monitor.html`. This file
describes the *implementation* — schema, view, auth rule, Qdrant pattern,
brain-janitor, superadmin identity model, and storage endpoint fail-soft behaviour.

---

## 1. Schema — migrations 0017 and 0018

**Migration `0017_brain_monitor_base`** adds three columns to every "brainable" table:

```
truth_level     enum   (NOT NULL on memory_items already; defaults to 'EPHEMERAL'
                       on the other 5 tables — backfilled per-entity-type at
                       migration time, see RESEARCH §Q4)
deleted_at      timestamptz   nullable
deleted_by      uuid          nullable, FK -> users.id ON DELETE SET NULL
```

Tables touched by 0017:
- `conversations`
- `messages`
- `team_messages`
- `tasks`
- `contacts`
- (memory_items already had `truth_level` since Phase 2 and `deleted_at` was added
  in an earlier migration — 0017 only patches `deleted_by` here for consistency.)

The backfill defaults per entity_type (locked in plan 11-01):
- `memory_item`, `granola_note`: keep existing `truth_level` (no backfill needed)
- `task`: `WORKING` (most are actively being worked)
- `conversation`, `message`, `team_message`: `EPHEMERAL` (transient by nature)
- `contact`: `VALIDATED` (a contact in CRM was put there deliberately)

Verify with assertion 12 of `verify-phase11.sh`:

```sql
SELECT COUNT(*) FROM memory_items
WHERE truth_level IS NOT NULL
  AND truth_level NOT IN ('EPHEMERAL','WORKING','VALIDATED','CANONICAL','PUBLIC');
-- expected: 0
```

**Migration `0018_brain_events_view`** creates the read-side view:

```sql
CREATE VIEW v_brain_events AS
  SELECT 'memory_item'  AS entity_type, id AS entity_id, team_scope,
         truth_level, deleted_at, deleted_by,
         source, created_by, created_at,
         /* content preview */, NULL::uuid AS conversation_id
    FROM memory_items
    WHERE source <> 'granola' OR source IS NULL
  UNION ALL
  SELECT 'granola_note', id, team_scope, truth_level, deleted_at, deleted_by,
         source, created_by, created_at, /* preview */, NULL
    FROM memory_items WHERE source = 'granola'
  UNION ALL
  SELECT 'conversation', id, team_scope, truth_level, deleted_at, deleted_by,
         NULL::text AS source, created_by, created_at, /* title */, id
    FROM conversations
  UNION ALL
  SELECT 'message', id, team_scope, ... conversation_id FROM messages
  UNION ALL
  SELECT 'team_message', id, team_scope, ... NULL FROM team_messages
  UNION ALL
  SELECT 'task', id, team_scope, ... NULL FROM tasks
  UNION ALL
  SELECT 'contact', id, team_scope, ... NULL FROM contacts;
```

Read path notes:
- `entity_type` is **derived inside the view** — never written. The closed
  allow-list lives both in the view body (the `UNION ALL` arms) and in
  `apps/memory-api/app/repos/brain.py::ENTITY_TABLE_MAP`. The two must stay in
  lockstep — if you add an arm to the view, add it to `ENTITY_TABLE_MAP` too,
  or PATCH/DELETE/restore will 400 with `unknown entity_type` even though GET
  returns the row.
- The view is filtered post-hoc on `team_scope` by the route layer. No row-level
  security policy is involved; isolation is enforced at the WHERE clause.

---

## 2. The closed allow-list — `ENTITY_TABLE_MAP`

Defined in `apps/memory-api/app/repos/brain.py`:

```python
ENTITY_TABLE_MAP: dict[str, str] = {
    "memory_item":  "memory_items",
    "granola_note": "memory_items",   # filtered on source='granola'
    "conversation": "conversations",
    "message":      "messages",
    "team_message": "team_messages",
    "task":         "tasks",
    "contact":      "contacts",
}
ALLOWED_ENTITY_TYPES = frozenset(ENTITY_TABLE_MAP.keys())
```

Why the map matters: the PATCH / DELETE / restore route handlers
(`update_truth_level`, `soft_delete_entity`, `restore_entity` in `repos/brain.py`)
interpolate the table name into raw SQL with an f-string — asyncpg cannot
parameterise a table identifier. The allow-list is the only sanitiser between
the URL path (`/v1/brain/events/{entity_type}/{entity_id}`) and the SQL string,
so any change to it must be reviewed for SQL-injection surface.

Adding a new entity in a future phase: add to the map, add a UNION ALL arm to
`v_brain_events` (bump migration), add columns (`truth_level`, `deleted_at`,
`deleted_by`) to the underlying table.

---

## 3. Authorization rule — `assert_can_edit_brain_event`

Defined in the route layer (`apps/memory-api/app/routes/brain.py`). For
PATCH / DELETE / restore:

```
allow if   team_members.role(principal.user.id, team_scope) == 'admin'
       OR  row.created_by == principal.user.id
deny  otherwise → HTTP 403 with exact wording:
      "You can only edit items you created. Contact a team admin to modify
       items created by others."
```

The 403 wording is **locked** — `brain.js` matches on it for the toast.
UAT step 7 exercises the verbatim string. If you reword, update the toast
match AND `11-UAT.md`.

Entities without `created_by` (`memory_item`, `granola_note`, `message`,
`contact` per the schema audit) fall through to the admin-only branch
automatically — there is no author check that can succeed for a non-admin.

---

## 4. Qdrant payload soft-delete + backfill ordering

Only vector-backed entities (`memory_item`, `granola_note`) have a Qdrant payload.
Soft delete flips `deleted_at_ts` in the payload **synchronously** so semantic
search stops returning the row in the same instant the DB UPDATE commits:

```python
async def soft_delete_entity(...):
    # 1) Postgres UPDATE first — the source of truth.
    await session.execute(sa.text(f"""
        UPDATE {table} SET deleted_at = NOW(), deleted_by = :uid
        WHERE id = :id AND team_scope = :team
        RETURNING deleted_at
    """), {...})
    # 2) Qdrant payload mark — best-effort, but blocks the response if it fails.
    if entity_type in _QDRANT_BACKED_ENTITY_TYPES:
        await qdrant_mark_deleted(qdrant, point_id, deleted_at_ts)
    # 3) Commit only after both succeed.
    await session.commit()
```

Ordering rationale: the DB UPDATE is the source of truth, but if the Qdrant call
fails *after* the commit, the row is invisible in lists yet still surfaced by
`/v1/memory/search`. Synchronous-then-commit keeps the two backends consistent.

**Backfill ordering** (plan 11-03 one-shot script): the migration runs PG-side
first to set `deleted_at_ts` in the payload for any row already soft-deleted in
PG. Order matters because retrieval routes read PG to compute the filter, then
ask Qdrant. The backfill is idempotent — re-runs are safe.

---

## 5. brain-janitor cron — daily 03:00 UTC

Container: `xbrain-brain-janitor`. Image: `xbrain/brain-janitor:phase11`. Source:
`apps/brain-janitor/`. Runs an APScheduler job daily; the job:

1. Selects every row with `deleted_at < NOW() - INTERVAL '30 days'` from each
   of the 6 brain tables.
2. Hard-DELETEs from Postgres first.
3. Deletes the corresponding Qdrant points (vector-backed entity types only).
4. Removes the corresponding Neo4j nodes (entities that were graphed —
   `memory_item` mainly).
5. Touches `/tmp/brain-janitor-alive` as a liveness sentinel.

Liveness check (verify-phase11.sh assertion 11): the sentinel mtime must be less
than 25 hours old. 24h is the cadence; 25h gives a 1-hour grace window for the
job to run, including DST transitions and brief container restarts.

If the sentinel is missing, the container is running but the first cycle hasn't
fired yet (cold start). The verify script SKIPs (not FAILs) in that case.

---

## 6. Known limitations

- **No `created_by` on `memory_items`, `contacts`, `messages`.** The author check
  cannot run for those entity types, so editing falls through to the admin-only
  branch. A future migration adding `created_by` (or backfilling from
  `source.startswith('user:')`) would lift the restriction.
- **No real-time stream.** v1 is polling at 30 s. WebSocket or SSE was rejected
  in CONTEXT.md to keep the page's complexity low — switching is a Phase 12+
  consideration.
- **No bulk operations.** Each row is edited / deleted / restored one at a time.
  Multi-select + bulk PATCH was descoped (CONTEXT.md "no bulk action bar in
  v1").
- **No export.** Exporting a team's brain to CSV / JSON is out of scope for
  Phase 11.

---

## 7. Superadmin identity model (Phase 11 in-flight scope addition)

The single source of truth is `_is_admin(principal)` in `apps/memory-api/app/deps.py`:

```python
def _is_admin(principal: dict) -> bool:
    """True iff principal.sub is in the comma-separated env ADMIN_USER_SUBS."""
    sub = principal.get("sub") or ""
    return sub in {s.strip() for s in os.getenv("ADMIN_USER_SUBS","").split(",") if s.strip()}
```

`assert_is_superadmin` is the FastAPI dependency that wraps it:

```python
async def assert_is_superadmin(principal = Depends(get_current_principal)) -> None:
    if not _is_admin(principal):
        raise HTTPException(403, "superadmin only")
```

All 5 `/v1/admin/brain/*` endpoints (`overview`, `storage`, `activity`,
`sources`, `events`) declare `_: None = Depends(assert_is_superadmin)`. Removing
that dependency from any of them is a critical bug (Rule 1) — verify-phase11.sh
assertion 14b is the regression guard (non-admin must get 403 on `overview`).

### Bridge JWT trust

Bridge service JWTs (`actor_kind='bridge'`) are not in `ADMIN_USER_SUBS` and
therefore never pass `_is_admin`. This is correct — the bridge has no business
calling admin endpoints. The audit_log payload's `actor_sub` field still records
the bridge identity if it *did* somehow reach the audit-write code path
(MAJOR-1 invariant from 11-10 — bridge JWTs must remain auditable). The
defence-in-depth: even if the gate were bypassed, the audit row would name the
bridge.

### Drill-down audit invariant

`/v1/admin/brain/events` writes its audit_log row **synchronously and before**
the data query:

```python
try:
    await write_audit(session, ..., action='superadmin_brain_access', ...)
    await session.flush()    # surface DB errors NOW, not at commit
except Exception:
    await session.rollback()
    raise HTTPException(500, "audit log write failed; access denied")

items, next_cursor = await _build_list_query(...)
await session.commit()
return BrainEventListOut(items=..., next_cursor=...)
```

Three guarantees:
1. No data is returned without a flushed audit row.
2. If the audit write fails the response is 500, never a data leak.
3. The audit row's `team_scope` column = target team (not actor's team) — this
   is the WHERE clause used by verify-phase11.sh assertion 15.

Aggregate endpoints (`overview` / `storage` / `activity` / `sources`) do NOT
write audit_log per call. Rationale (CONTEXT.md): they return cross-team
counts only, no per-team content. Revisit if aggregate access proves sensitive
to a future privacy review.

### Lockdown semantics

`ADMIN_USER_SUBS=""` (empty or unset) — `_is_admin` returns `False` for every
principal including previously-listed subs. All 5 admin endpoints return 403.
This is the kill-switch. To take it, edit `infrastructure/.env` on the VM and
`docker compose restart memory-api` — the env is read at startup, not per request.

Manual verification is documented in the `verify-phase11.sh` header; the
automated assertion 16 is gated by `LOCKDOWN_TEST=1` because it requires the
memory-api to be running with the env blanked (which makes the rest of the
admin scope unreachable). Operators set the gate, run the script, restore the
env, restart, re-run with the gate off.

---

## 8. Storage endpoint fail-soft behaviour

`GET /v1/admin/brain/storage` returns per-team:

- `pg_rows` — `COUNT(*)` per brain table, summed. Always non-null (Postgres is
  the single source of truth, and a failure here is a 500).
- `qdrant_points` — `count` per Qdrant collection scoped by `team_scope`. Returns
  `None` if Qdrant is unreachable or the collection doesn't exist.
- `minio_bytes` — sum of object sizes under the team's MinIO prefix. Returns
  `None` if MinIO is unreachable or `MINIO_*` env vars are unset.

The repo (`apps/memory-api/app/repos/brain_metrics.py::get_storage_per_team`)
swallows exceptions from the Qdrant and MinIO clients and returns `None` for
those cells. The dashboard renders `N/A` for `None` values and shows a footnote
that totals exclude N/A cells. **Never** propagate an exception from Qdrant /
MinIO out of the storage endpoint — the rest of the dashboard must still load.

Required env vars (verified by the dashboard via N/A rendering):
- `MINIO_URL` (e.g. `http://xbrain-minio:9000`)
- `MINIO_ACCESS_KEY`
- `MINIO_SECRET_KEY`
- `MINIO_BUCKET` (single bucket — per-team prefix inside it)

If any of these is unset, the storage cell for MinIO is N/A for all teams. The
endpoint does not raise — N/A is the documented degraded state.
