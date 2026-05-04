---
phase: 03-graphe-extraction-integrations
plan: "05"
subsystem: memory-api + agent-runtime
tags: [neo4j, graph-api, ner, entity-extraction, outbox-pattern, graceful-degrade]
dependency_graph:
  requires: ["03-04"]   # neo4j_client.get_driver() + NeoOutboxEntry table migration
  provides: ["03-06", "03-11"]   # graph endpoints queryable; drive-sync can emit entities
  affects: ["memory-api /v1/graph/*", "agent-runtime ingestion pipeline", "neo4j_outbox drain"]
tech_stack:
  added: []
  patterns:
    - "GET /v1/graph/* scoped by JWT team_scope via Depends(get_team_scope) — no raw Cypher exposure"
    - "503 graceful degrade: _require_driver() returns HTTPException 503 if Neo4j absent"
    - "Variable-length Cypher path *1..N with validated literal N (ge=1, le=4) — no $param in *min..max"
    - "NeoOutboxEntry inserted in same SQLAlchemy transaction as audit_log (one session.commit)"
    - "extract_facts() now returns dict{facts, entities} — ingestion.py embeds entities into metadata"
key_files:
  created:
    - apps/memory-api/app/routes/graph.py
    - apps/memory-api/app/models/neo4j_outbox.py
  modified:
    - apps/memory-api/app/main.py
    - apps/memory-api/app/routes/memory.py
    - apps/agent-runtime/app/tools/extract_facts.py
    - apps/agent-runtime/app/graphs/ingestion.py
decisions:
  - "Cypher variable-length path *1..{depth} uses validated integer literal (not $param) because Neo4j does not accept $param in path length expressions; depth is validated ge=1 le=4 before use"
  - "NeoOutboxEntry SQLAlchemy model created in app/models/ (not app/db/models.py which does not exist) — consistent with AuditLog pattern in app/models/audit.py"
  - "entities embedded into every fact's metadata in ingestion.py extract_node (shared list across all facts from same document) — simplest path; drive-sync plan 03-11 can refine per-fact entity scoping"
metrics:
  duration: "~20 minutes"
  completed: "2026-05-04"
  tasks_completed: 3
  files_changed: 6
---

# Phase 3 Plan 05: Graph Endpoints + Entity NER Extension Summary

**One-liner:** GET /v1/graph/traverse and /v1/graph/lineage endpoints (team-scoped, 503 graceful degrade) + Claude NER in extract_facts() feeding neo4j_outbox in the same atomic transaction as the memory upsert.

## What Was Built

### Task 1 — extract_facts.py NER extension + ingestion.py update (commit 3b9518c)

`apps/agent-runtime/app/tools/extract_facts.py`:
- `SYSTEM_PROMPT` extended to request `{"facts": [...], "entities": [...]}` from Claude
- `extract_facts()` return type changed from `list[dict]` to `dict[str, Any]` with keys `facts` and `entities`
- `_coerce_entity_type(raw)` helper normalises entity types to `person|org|project|technology|concept` (fallback: `concept`)
- Up to `MAX_ENTITIES = 15` entities returned per call
- Entity schema: `{"name": str, "type": str}` with `name` trimmed and capped at 256 chars

`apps/agent-runtime/app/graphs/ingestion.py` `extract_node`:
- Updated from `facts = await extract_facts(...)` to `extraction = await extract_facts(...); facts = extraction["facts"]`
- `entities = extraction.get("entities", [])` stored and embedded into each fact's `metadata.entities` — this is what memory-api reads to enqueue outbox rows

### Task 2 — /v1/graph/* endpoints (commit 383506c)

`apps/memory-api/app/routes/graph.py` — new file:
- `GET /graph/traverse?entity=X&depth=2`: returns `TraverseResult{root, depth, entities[]}` — all `Entity` nodes reachable via `[:DEPENDS_ON*1..N]` from the named root entity, scoped by JWT `team_scope`
- `GET /graph/lineage?fact_id=Y`: returns `LineageResult{fact_id, derived_from[]}` — all `[:DERIVED_FROM]` sources for a `Fact` node, scoped by JWT `team_scope`
- `_require_driver()` raises `HTTPException(503)` if Neo4j not connected — memory-api continues serving other routes normally
- Cypher uses `$name`, `$team_scope`, `$fact_id` parameters; `depth` is an integer literal embedded after validation (ge=1, le=4)

`apps/memory-api/app/main.py`:
- `from app.routes import graph` added to import block
- `app.include_router(graph.router, prefix="/v1", tags=["graph"])` added after promotions router

### Task 3 — Outbox INSERT in memory upsert path (commit b12ceaf)

`apps/memory-api/app/models/neo4j_outbox.py` — new SQLAlchemy ORM model:
- `NeoOutboxEntry` mapped to `neo4j_outbox` table (schema from migration 0004)
- Fields: `id UUID`, `cypher Text`, `params JSONB`, `processed bool`, `created_at`, `processed_at`, `error`

`apps/memory-api/app/routes/memory.py` `upsert_item` handler:
- After `item_id = await provider.upsert(body.item)`, reads `entities = (body.item.metadata or {}).get("entities", [])`
- For each entity: adds 2 `NeoOutboxEntry` rows to `session`:
  1. `MERGE (e:Entity {name: $name, team_scope: $team_scope}) ON CREATE SET e.type = $type`
  2. `MERGE (f:Fact ...) MERGE (e:Entity ...) MERGE (f)-[:MENTIONS]->(e)`
- The existing `await session.commit()` at end of handler commits both outbox rows + audit log atomically
- Import: `from app.models.neo4j_outbox import NeoOutboxEntry` added at top of file

**Outbox INSERT location:** `apps/memory-api/app/routes/memory.py`, lines inserted between `item_id = await provider.upsert(body.item)` and `actor_id = ...` (the audit write block). The outbox rows and the audit row share one `session.commit()`.

## Deviations from Plan

### Auto-added: NeoOutboxEntry SQLAlchemy model (Rule 2 — missing critical functionality)

The plan's Task 3 referenced `from app.db.models import NeoOutboxEntry` but `app/db/models.py` does not exist in the codebase (models live in `app/models/`). The ORM model was missing entirely.

- **Found during:** Task 3 pre-check
- **Fix:** Created `apps/memory-api/app/models/neo4j_outbox.py` with `NeoOutboxEntry` ORM, consistent with `app/models/audit.py` pattern (same Base, same JSONB/UUID dialect imports)
- **Import in memory.py:** Updated to `from app.models.neo4j_outbox import NeoOutboxEntry`
- **Commit:** b12ceaf (included in Task 3 commit)

### Cypher depth parameter approach

The plan's original `cypher_no_apoc` used `$depth` as a Cypher parameter in `*1..$depth`. Neo4j does not support parameters in path length expressions. Used validated integer literal `*1..{depth}` instead (depth is validated ge=1, le=4 by FastAPI Query before use — injection risk is zero).

## Known Stubs

None — no UI-facing stubs. Graph endpoints return live Neo4j data (or 503). The outbox INSERT is infrastructure that becomes active once Neo4j is running and entities start flowing through ingestion.

## Threat Flags

No new network surfaces beyond what the plan's threat model anticipated. All T-03-05-* threats mitigated as designed.

## Self-Check

Files exist:
- apps/memory-api/app/routes/graph.py — CREATED
- apps/memory-api/app/models/neo4j_outbox.py — CREATED
- apps/memory-api/app/main.py — MODIFIED
- apps/memory-api/app/routes/memory.py — MODIFIED
- apps/agent-runtime/app/tools/extract_facts.py — MODIFIED
- apps/agent-runtime/app/graphs/ingestion.py — MODIFIED

Commits:
- 3b9518c: feat(03-05): extend extract_facts() to emit entities NER list
- 383506c: feat(03-05): add /v1/graph/traverse and /v1/graph/lineage endpoints
- b12ceaf: feat(03-05): wire neo4j_outbox INSERT into memory upsert path

## Self-Check: PASSED
