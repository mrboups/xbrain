# Phase 3 — Plan Index

**Phase:** 03-graphe-extraction-integrations
**Total plans:** 12 plans in 4 waves
**Requirements covered:** SRCH-05, MCP-01..07, INT-01..04 (12/12)

---

## Wave Structure

| Wave | Plans | Rationale |
|------|-------|-----------|
| 1 | 03-01, 03-02, 03-03 | Foundations — fully independent, can run in parallel |
| 2 | 03-04, 03-05 | Neo4j integration in memory-api — depends on 03-01 (service), 03-02 (migration) |
| 3 | 03-06, 03-07, 03-08, 03-09 | MCP infra — fully independent of Wave 2, all 4 parallel |
| 4 | 03-10, 03-11, 03-12 | Integration layer — Drive admin, drive-sync, tool registration |

---

## Plans

| Plan | Wave | Depends On | Requirements | Est. Duration | Files Touched |
|------|------|------------|--------------|---------------|---------------|
| [03-01](03-01-PLAN.md) Neo4j compose service | 1 | — | SRCH-05 | 15 min | 2 |
| [03-02](03-02-PLAN.md) Alembic migration 0004 | 1 | — | SRCH-05, INT-01, INT-02, INT-03 | 20 min | 1 |
| [03-03](03-03-PLAN.md) OAuth scope upgrade runbook | 1 | — | INT-01, INT-03, INT-04, MCP-06 | 15 min | 1 |
| [03-04](03-04-PLAN.md) Neo4j driver + outbox worker | 2 | 03-01, 03-02 | SRCH-05 | 45 min | 6 |
| [03-05](03-05-PLAN.md) Graph endpoints + NER extension | 2 | 03-01, 03-02, 03-04 | SRCH-05 | 50 min | 4 |
| [03-06](03-06-PLAN.md) mcp-gateway service | 3 | — | MCP-01, MCP-02, MCP-03, MCP-04, MCP-07 | 60 min | 10 |
| [03-07](03-07-PLAN.md) mcp-scraper sidecar | 3 | — | MCP-05 | 25 min | 4 |
| [03-08](03-08-PLAN.md) mcp-drive-read sidecar | 3 | — | MCP-05, INT-04 | 35 min | 7 |
| [03-09](03-09-PLAN.md) mcp-calendar sidecar | 3 | — | MCP-06 | 30 min | 7 |
| [03-10](03-10-PLAN.md) Drive admin endpoint in memory-api | 4 | 03-02, 03-04 | INT-01, INT-03 | 40 min | 3 |
| [03-11](03-11-PLAN.md) drive-sync service | 4 | 03-04, 03-05, 03-06, 03-10 | INT-01, INT-02, INT-03, INT-04, MCP-04 | 90 min | 9 |
| [03-12](03-12-PLAN.md) MCP tool registration + E2E | 4 | 03-06, 03-07, 03-08, 03-09 | MCP-01, MCP-02, MCP-05, MCP-06, MCP-07 | 20 min | 2 |

---

## Requirements Coverage

| Requirement | Covered By |
|-------------|-----------|
| SRCH-05 | 03-01, 03-02, 03-04, 03-05 |
| MCP-01 | 03-06, 03-12 |
| MCP-02 | 03-06, 03-12 |
| MCP-03 | 03-06 |
| MCP-04 | 03-06, 03-11 |
| MCP-05 | 03-07, 03-12 |
| MCP-06 | 03-09, 03-12 |
| MCP-07 | 03-06, 03-12 |
| INT-01 | 03-02, 03-10, 03-11 |
| INT-02 | 03-02, 03-11 |
| INT-03 | 03-02, 03-10, 03-11 |
| INT-04 | 03-08, 03-11 |

**All 12 Phase 3 requirements covered. No gaps.**

---

## New Services Summary

| Service | Port (internal) | mem_limit | Build Context | Description |
|---------|----------------|-----------|---------------|-------------|
| neo4j | 7474/7687 | 1024m | n/a (official image) | Graph store (Wave 1) |
| mcp-gateway | 8080 | 192m | apps/mcp-gateway | MCP tool router (Wave 3) |
| mcp-scraper | 8100 | 128m | apps/mcp-scraper | URL → text (Wave 3) |
| mcp-drive-read | 8101 | 128m | apps/mcp-drive-read | Drive live read/write (Wave 3) |
| mcp-calendar | 8102 | 128m | apps/mcp-calendar | Calendar events (Wave 3) |
| drive-sync | — | 192m | .. (repo root) | Drive incremental sync (Wave 4) |

**New volumes:** `neo4j_data`

**Phase 3 total mem cap (new services):** ~1.8 GB hard caps, ~1.2 GB real expected.

---

## Key Non-Obvious Decisions

1. **mcp-gateway is NOT a FastMCP host** (plans 03-06). It's a plain FastAPI proxy that speaks the MCP wire protocol. FastMCP runs only in the 3 tool sidecars (issue #1367 — cannot mount FastMCP inside a parent FastAPI). The gateway has ~150 lines and no `mcp` package dependency.

2. **mcp-drive-read and mcp-calendar are separate plans** (03-08, 03-09) despite both using Google APIs. Reason: they have different OAuth scopes (drive.readonly vs calendar.readonly), different env vars, and different tool signatures. Merging them would create a 200+ line plan exceeding context budget and violate single-concern rule.

3. **Drive admin endpoint (03-10) is a separate Wave 4 plan** from drive-sync (03-11). Reason: 03-10 is a memory-api extension (modifies memory-api), while 03-11 is a new service. They modify different files and can be coded independently. 03-11 depends on 03-10 completing so the team_drive_mappings table has data for the poller.

4. **extract_facts.py extension is in plan 03-05** (not 03-11 or a separate plan). Reason: The entity extraction is logically part of the graph integration (Neo4j population), not the Drive sync. The outbox gets populated via metadata.entities from the fact upsert — wiring logic lives closest to the Neo4j integration.

5. **OAuth runbook is a separate plan** (03-03) because Google Cloud Console changes are human-only actions that cannot be automated. Having a dedicated plan with a concrete checklist ensures this blocker is addressed before Wave 4 starts.

6. **drive-sync uses repo-root build context** (`context: ..`) in docker-compose, same as memory-api. Critical Phase 2 lesson: any service importing `packages/memory-models` needs the repo root in its Docker build context.

7. **Soft-archive vs hard-delete** (RESEARCH.md Q4): Facts at WORKING+ are never silently deleted — they get `validation_status='archived'`. Only EPHEMERAL facts (never promoted, no audit trail) are hard-deleted. This preserves the audit invariant from Phase 2.
