---
phase: 03-graphe-extraction-integrations
plan: "04"
subsystem: memory-api
tags: [neo4j, outbox-pattern, background-worker, graceful-degrade, asyncpg]
dependency_graph:
  requires: ["03-01"]   # neo4j_outbox migration (table must exist)
  provides: ["03-05"]   # graph endpoints need a live driver + populated Neo4j
  affects: ["memory-api lifespan", "docker-compose.yml memory-api service"]
tech_stack:
  added: ["neo4j>=6.1.0 (async driver, lazy-imported)"]
  patterns:
    - "Singleton driver with graceful degrade (NEO4J_URI/PASSWORD empty → no-op)"
    - "SELECT FOR UPDATE SKIP LOCKED for multi-worker outbox drain"
    - "asyncio.create_task + cancel/await in FastAPI lifespan for background workers"
key_files:
  created:
    - apps/memory-api/app/neo4j_client.py
    - apps/memory-api/app/outbox_worker.py
  modified:
    - apps/memory-api/app/config.py
    - apps/memory-api/app/main.py
    - apps/memory-api/pyproject.toml
    - infrastructure/docker-compose.yml
decisions:
  - "Lazy import of neo4j.AsyncGraphDatabase: avoids ImportError if package not yet installed in dev; degrade is structural not import-level"
  - "CancelledError re-raised in inner loop and caught in outer try/except for clean pool.close() in finally block"
  - "neo4j depends_on added to memory-api in docker-compose.yml (service_healthy) — ensures bolt port is ready before worker starts draining"
metrics:
  duration: "~15 minutes"
  completed: "2026-05-04"
  tasks_completed: 2
  files_changed: 6
---

# Phase 3 Plan 04: Neo4j Driver + Outbox Worker Summary

**One-liner:** Async Neo4j driver singleton with graceful degrade + SKIP LOCKED outbox worker wired into FastAPI lifespan, connecting Postgres to Neo4j for eventually-consistent graph sync.

## What Was Built

### Task 1 — neo4j_client.py + config.py + pyproject.toml (commit c1d7083)

`apps/memory-api/app/neo4j_client.py` — new file providing:
- `init_driver()`: creates `AsyncGraphDatabase.driver()`, calls `verify_connectivity()`, sets global `_driver`. On connectivity failure, logs error and sets `_driver = None` (no crash).
- `close_driver()`: cleanly closes the driver on shutdown.
- `get_driver()`: returns the live driver or `None`.
- Graceful degrade: if `NEO4J_URI` or `NEO4J_PASSWORD` is empty (default in dev), skips driver init entirely with a warning log. memory-api boots normally.

`apps/memory-api/app/config.py` — added three optional fields:
```python
NEO4J_URI: str = ""
NEO4J_USER: str = "neo4j"
NEO4J_PASSWORD: str = ""
```

`apps/memory-api/pyproject.toml` — added `neo4j>=6.1.0` dependency.

### Task 2 — outbox_worker.py + main.py + docker-compose.yml (commit c8b8b9e)

`apps/memory-api/app/outbox_worker.py` — new file providing:
- `drain_outbox(pg_dsn)`: long-running coroutine. Creates its own asyncpg pool (min=1, max=2) separate from FastAPI's pool.
- Per tick: `SELECT id, cypher, params FROM neo4j_outbox WHERE processed=false ORDER BY created_at LIMIT 50 FOR UPDATE SKIP LOCKED`
- Executes Cypher via `driver.execute_query(cypher, database_="neo4j", **params)`.
- On success: `UPDATE neo4j_outbox SET processed=true, processed_at=now()`.
- On Cypher error: marks `processed=true` with `error=<message[:2000]>` to avoid infinite retry.
- `asyncio.CancelledError` propagates to outer try/except; `finally` always calls `pool.close()`.

`apps/memory-api/app/main.py` lifespan now:
1. Calls `await init_driver()` after qdrant setup.
2. Starts `asyncio.create_task(drain_outbox(settings.DATABASE_URL))`.
3. On shutdown (after yield): cancels task, awaits it, calls `close_driver()`.

`infrastructure/docker-compose.yml` — memory-api service gains:
- `NEO4J_URI: bolt://neo4j:7687`
- `NEO4J_USER: neo4j`
- `NEO4J_PASSWORD: ${NEO4J_PASSWORD}`
- `depends_on: neo4j: { condition: service_healthy }`

## Deviations from Plan

None — plan executed exactly as written.

Minor implementation note: `CancelledError` handling was restructured slightly for correctness. The plan's original code placed `CancelledError` in the `except` block inside the `while True` loop with a `break`. The implementation uses a nested try/except that re-raises `CancelledError` to the outer handler, ensuring `pool.close()` runs via `finally`. This is equivalent behavior, more robust.

## Known Stubs

None — this plan contains no UI-facing stubs. The outbox drain is a background infrastructure component; it will produce no visible output until plan 03-01's `neo4j_outbox` migration table exists and plan 03-05 starts writing rows into it.

## Threat Flags

No new network endpoints introduced. The outbox worker connects Bolt inward (Neo4j internal service). No new public API surface.

## Self-Check

Files exist:
- apps/memory-api/app/neo4j_client.py — CREATED
- apps/memory-api/app/outbox_worker.py — CREATED
- apps/memory-api/app/config.py — MODIFIED
- apps/memory-api/app/main.py — MODIFIED
- apps/memory-api/pyproject.toml — MODIFIED
- infrastructure/docker-compose.yml — MODIFIED

Commits:
- c1d7083: feat(03-04): neo4j_client.py driver factory + graceful degrade
- c8b8b9e: feat(03-04): outbox_worker + lifespan wiring + docker-compose NEO4J vars

## Self-Check: PASSED
