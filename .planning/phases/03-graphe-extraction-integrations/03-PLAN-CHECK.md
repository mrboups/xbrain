---
verdict: PASS_WITH_REVISIONS
reviewer: gsd-plan-checker
date: 2026-05-04
---

# Phase 3 -- Plan Check

## Verdict: PASS_WITH_REVISIONS

## Summary

The 12-plan set is architecturally coherent and covers all 12 Phase 3 requirements.
All 4 success criteria have credible delivery paths, the wave structure is sound, the 4
Phase 2 pitfalls are explicitly addressed, and critical design constraints (FastMCP
standalone, repo-root build context, internal DNS) are properly wired throughout.

Three blockers require revision before execution can proceed.

BLOCKER 1: No plan modifies the memory-api fact upsert handler to INSERT rows into
neo4j_outbox. Driver, worker, and graph endpoints are built, but Neo4j stays empty
because POST /v1/memory/upsert never enqueues Cypher rows. SC1 fails.

BLOCKER 2: Plan 03-10 admin_drive.py references settings.ADMIN_USER_SUBS but no plan
declares this field in memory-api Settings. AttributeError on first call to
POST /v1/admin/drive-mapping blocks all Drive sync setup.

BLOCKER 3: INT-04 requires audit log action drive_write. Plan 03-06 logs
mcp.tool_call.drive-read for all calls including writes. The distinction is never made.
---

## Goal Coverage

| Success Criterion | Plans | Status |
|---|---|---|
| SC1: graph traversal via memory-api (SRCH-05) | 03-01 03-02 03-04 03-05 | GAP BLOCKER 1 |
| SC2: Drive sync incremental + WORKING tagging | 03-02 03-03 03-10 03-11 | COVERED |
| SC3: Register MCP without restart, team_scope/user_id injected | 03-06 03-12 | COVERED |
| SC4: Drive write-back opt-in + audit (INT-04) | 03-08 03-06 | PARTIAL GAP BLOCKER 3 |

---

## Requirement Coverage

| Requirement | Covered By | Gap |
|---|---|---|
| SRCH-05 | 03-01 03-02 03-04 03-05 | BLOCKER 1 Neo4j stays empty |
| MCP-01 | 03-06 03-12 | None |
| MCP-02 | 03-06 03-12 | None |
| MCP-03 | 03-06 | None |
| MCP-04 | 03-06 03-11 | None |
| MCP-05 | 03-07 03-12 | None |
| MCP-06 | 03-09 03-12 | None |
| MCP-07 | 03-06 03-12 | None |
| INT-01 | 03-02 03-10 03-11 | None |
| INT-02 | 03-02 03-11 | None |
| INT-03 | 03-02 03-10 03-11 | BLOCKER 2 admin endpoint broken |
| INT-04 | 03-08 03-11 | BLOCKER 3 wrong audit action key |

All 12 Phase 3 requirements listed. No orphans.
---

## Critical Gaps

### BLOCKER 1 -- Outbox INSERT missing from memory-api upsert path

Plans 03-04 and 03-05 build the Neo4j driver, outbox worker, and graph endpoints.
Plan 03-05 extends extract_facts() to emit entities and states memory-api will
read metadata.entities on upsert to enqueue Cypher. But neither plan adds INSERT
INTO neo4j_outbox inside the upsert handler. files_modified for both plans does not
include apps/memory-api/app/routes/memory.py or native_provider.py.

Result: outbox stays empty, worker drains nothing, Neo4j has no nodes,
GET /v1/graph/traverse returns empty results. SC1 fails.

Fix: Plan 03-05 must add apps/memory-api/app/routes/memory.py to files_modified and
add a step patching the upsert handler: after successful upsert when metadata.entities
is non-empty, INSERT Cypher rows for MERGE Entity, MERGE Fact, CREATE MENTIONS edge.

### BLOCKER 2 -- ADMIN_USER_SUBS undeclared in memory-api Settings

Plan 03-10 admin_drive.py _is_admin() calls settings.ADMIN_USER_SUBS. Plan 03-10
adds GOOGLE_CLIENT_SECRET, OAUTH_CREDENTIALS_ENCRYPTION_KEY, MEMORY_API_EXTERNAL_URL
to config.py but not ADMIN_USER_SUBS. First POST /v1/admin/drive-mapping call
raises AttributeError, blocking all Drive sync setup.

Fix: Add ADMIN_USER_SUBS with empty string default to config.py in plan 03-10 Task 1.
Document in .env.example as comma-separated Google sub IDs.

### BLOCKER 3 -- INT-04 audit action is not drive_write

REQUIREMENTS.md INT-04 requires the write-back to be traceable. CONTEXT.md labels it
action=drive_write. Plan 03-06 log_tool_call() uses action=mcp.tool_call.TOOL_NAME.
For tool_name drive-read this becomes mcp.tool_call.drive-read for reads and writes.

Fix: Plan 03-06 call_tool() must inspect JSON-RPC body params.name. When it equals
write_drive_file, use action=drive_write in log_tool_call().
---

## Wave Dependency Audit

| Dependency | Valid? |
|---|---|
| 03-04 depends on 03-01 (neo4j service) | CORRECT |
| 03-04 depends on 03-02 (outbox table) | CORRECT |
| 03-05 depends on 03-04 (driver singleton) | CORRECT |
| 03-06 Wave 3 depends_on empty (independent) | CORRECT |
| 03-11 depends on 03-10 (team_drive_mappings) | CORRECT |
| 03-11 depends on 03-05 (extract_facts extended) | CORRECT |
| 03-12 depends on 03-06 03-07 03-08 03-09 | CORRECT |

No circular dependencies. No hidden forward references.

---

## Phase 2 Pitfalls

| Pitfall | Plan | Evidence |
|---|---|---|
| Build context for packages/memory-models | 03-11 | Dockerfile REPO ROOT comment; compose context: ..; verify asserts context eq ..; PLAN-INDEX key decision 6 |
| Cloudflare 502 on POST use internal DNS | 03-11 03-06 | MEMORY_API_URL http://memory-api:8000 hardcoded; THREAT T-03-11-05 names Phase 2 lesson |
| IPv4 healthcheck 127.0.0.1 not localhost | 03-01 to 03-09 | All healthchecks use http://127.0.0.1:<port>; 03-01 Task 1 states rule |
| FastMCP mount bug issue 1367 | 03-06 to 03-09 | 03-06 plain FastAPI proxy without mcp package; sidecars standalone; PLAN-INDEX key decision 1 |

All 4 pitfalls addressed.
---

## Per-Plan Smell Test

| Plan | Has verify? | Files specific? | Duration realistic? | Notes |
|---|---|---|---|---|
| 03-01 | YES | YES 2 files | YES 15 min | Clean |
| 03-02 | YES | YES 1 file | YES 20 min | Clean |
| 03-03 | YES | YES 1 file | YES 15 min | Clean |
| 03-04 | YES | YES 6 files | YES 45 min | BLOCKER 1 missing outbox INSERT |
| 03-05 | YES | YES 3 files | YES 50 min | BLOCKER 1 memory.py not in files_modified |
| 03-06 | YES | YES 10 files | TIGHT 60 min | BLOCKER 3 audit action wrong for drive_write |
| 03-07 | YES | YES 4 files | YES 25 min | Clean |
| 03-08 | YES | YES 7 files | YES 35 min | Clean |
| 03-09 | YES | YES 7 files | YES 30 min | Clean |
| 03-10 | YES | YES 2 files | YES 40 min | BLOCKER 2 missing ADMIN_USER_SUBS in Settings |
| 03-11 | YES | YES 9 files | TIGHT 90 min | WARNING sync Drive API calls block asyncio event loop |
| 03-12 | YES | YES 2 files | YES 20 min | Clean |

---

## MCP Architecture Audit

| Check | Status |
|---|---|
| mcp-gateway does NOT import mcp package | CONFIRMED pyproject.toml excludes it; gateway is plain HTTP proxy |
| mcp-scraper drive-read calendar run standalone FastMCP | CONFIRMED all use mcp.run(transport=streamable-http) standalone |
| No app.mount() for FastMCP | CONFIRMED no mount() call in plans 03-06 to 03-09 |
| Single worker per sidecar enforced | CONFIRMED Dockerfiles and must_haves truths both state single worker |
| MCP-Protocol-Version 2025-06-18 injected | CONFIRMED constant defined and added to forwarded headers |
| GET /mcp SSE hang avoided | CONFIRMED gateway forwards only POST tool calls |

FastMCP-not-mountable constraint fully honored across plans 03-06 through 03-09.
---

## Outbox and Neo4j Sync Coherence

| Edge Type | Planned? |
|---|---|
| Fact MENTIONS Entity | NOT IMPLEMENTED -- BLOCKER 1 |
| Entity DEPENDS_ON Entity | Not in Phase 3 scope -- requires cross-entity inference |
| User PROPOSED Fact | Out of Phase 3 scope -- Phase 2 promotion workflow |
| User APPROVED Fact | Out of Phase 3 scope |
| Fact DERIVED_FROM Drive_Document | Not explicitly planned -- no plan creates Drive_Document node |

The minimum viable graph for SC1 (MENTIONS edges) is blocked by BLOCKER 1.
DERIVED_FROM is a secondary gap the executor should address in 03-11 or 03-05.

---

## Build Context Spot-Check

| Service | Needs packages/memory-models? | Compose context | Correct? |
|---|---|---|---|
| drive-sync | YES xbrain-memory dep in pyproject.toml | context: .. repo root | CORRECT |
| mcp-gateway | NO | context: ../apps/mcp-gateway | CORRECT |
| mcp-scraper | NO | context: ../apps/mcp-scraper | CORRECT |
| mcp-drive-read | NO | context: ../apps/mcp-drive-read | CORRECT |
| mcp-calendar | NO | context: ../apps/mcp-calendar | CORRECT |

---

## Recommended Revisions

1. BLOCKER 1 -- Plan 03-05: add apps/memory-api/app/routes/memory.py to files_modified.
   In Task 2 action, patch the upsert handler to INSERT Cypher rows into neo4j_outbox
   when metadata.entities is non-empty. Minimum: MERGE Entity per entity, MERGE Fact,
   CREATE MENTIONS edge. Add verify: grep neo4j_outbox memory.py returns >= 1 line.

2. BLOCKER 2 -- Plan 03-10 Task 1: add ADMIN_USER_SUBS with empty string default to
   config.py additions. Add ADMIN_USER_SUBS= to .env.example (comma-separated Google sub IDs).

3. BLOCKER 3 -- Plan 03-06 Task 2: in call_tool() after reading request body, inspect
   JSON-RPC params.name. When it equals write_drive_file, use action=drive_write
   in log_tool_call() instead of the generic mcp.tool_call.TOOL_NAME.

4. WARNING -- Plan 03-11 Task 2: wrap synchronous _export_file_text() and _with_backoff()
   with asyncio.get_event_loop().run_in_executor() inside async poll_team().
   Replace time.sleep in _with_backoff with asyncio.sleep.
---

## Recheck — 2026-05-04

**Verdict: PASS**

### Blocker Status

| Blocker | Closed? | Evidence |
|---------|---------|----------|
| BLOCKER 1 — memory.py in files_modified + outbox INSERT | CLOSED | 03-05 files_modified line 11 includes memory.py. Task 3 adds NeoOutboxEntry INSERT (MERGE Entity + MERGE Fact + MENTIONS) inside existing SQLAlchemy transaction. must_haves.truths updated to mention outbox INSERT + same-transaction guarantee. |
| BLOCKER 2 — ADMIN_USER_SUBS in config.py | CLOSED | 03-10 files_modified includes config.py. Task 1 action adds `ADMIN_USER_SUBS: str = Field(default="", ...)` with Pydantic v2 syntax. must_haves.truths declares the field explicitly. |
| BLOCKER 3 — distinct audit action for read vs write | CLOSED | 03-08 exposes two separate `@mcp.tool()` decorators: `read_drive_file` and `write_drive_file`. 03-06 audit action is `mcp.tool_call.{tool_name}` — gateway sees distinct tool_name values from registry, yielding `mcp.tool_call.read_drive_file` vs `mcp.tool_call.write_drive_file`. Both must_haves.truths confirm the distinction. |

### Spot-Checks

- Index file counts: 03-05 listed as 4 files (matches files_modified: graph.py, main.py, extract_facts.py, memory.py). 03-10 listed as 3 files (matches: admin_drive.py, main.py, config.py). Consistent.
- Wave dependency: 03-05 only WRITEs to neo4j_outbox; 03-04 READs/drains it. 03-05 depends_on 03-04 (for NeoOutboxEntry model + driver). No ordering violation.

### New Issues

None.
