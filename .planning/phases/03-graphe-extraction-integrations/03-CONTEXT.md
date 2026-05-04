---
phase: 3
phase_name: Graphe + Extraction + Intégrations
phase_slug: graphe-extraction-integrations
date: 2026-05-04
goal_from_roadmap: |
  La mémoire d'équipe est connectée au monde extérieur — Drive sync incrémental,
  outils internes en MCP, et le graphe Neo4j rend les relations entre entités
  et le lineage des faits queryables.
requirements_in_scope:
  - SRCH-05  # graph-traversal queries via memory-api (no direct Cypher)
  - MCP-01   # MCP gateway service registers tools
  - MCP-02   # team_scope + user_id injected into every tool call
  - MCP-03   # tool call audit logged in memory-api
  - MCP-04   # at least 3 MCP tools registered
  - MCP-05   # tool call from LibreChat works
  - MCP-06   # tool call from agent-runtime graph works
  - MCP-07   # gateway can register a new MCP server without infra restart
  - INT-01   # Drive folder mapped to team, incremental sync
  - INT-02   # synced docs go through ingestion-agent (extract → HITL → memory-api)
  - INT-03   # agent can write back to Drive with explicit user opt-in
  - INT-04   # all Drive write actions appear in audit log
---

# Phase 3 — Graphe + Extraction + Intégrations — CONTEXT

## Locked Decisions

### Architecture & Infrastructure
- **VM stays `e2-standard-2` (8GB).** No upgrade. Real-usage analysis showed 4.1GB free post-Phase 2 (not 2GB as initially mis-estimated), enough for Phase 3 services with conservative configs. Upgrade to `e2-standard-4` is reserved as a fallback if `docker stats` shows pressure.
- **No new VM, no Langfuse split.** Cost stays ~49€/mo (Phase 2 baseline).
- **Memori is OUT (decided in Phase 2).** No Memori POC. Extraction = Claude-based pattern from `apps/agent-runtime/app/tools/extract_facts.py`.

### Drive sync architecture
- **Pre-sync into memory-api** (NOT live read at query time). The differentiator of xbrain is the persistent team-scoped memory layer. Live Drive reads at query time would defeat truth-level promotion, semantic search, and team isolation enforcement.
- **Polling cron 5min** via `drive-sync` Python sidecar service. Uses Google Drive `changes.list` with team-scoped change tokens (incremental — only modified files re-fetched).
- **1 Drive folder mapped per team** (configurable via memory-api admin endpoint). Multi-folder per team is deferred to Phase 4.
- **File types in scope:** Google Docs, Sheets, Slides (via `files.export`), PDFs (via pypdf, reusing `apps/agent-runtime/app/tools/document_loader.py`), Markdown. Images and binary blobs skipped (Phase 4: OCR via vision LLM).
- **Sync flow:** drive-sync fetches → calls existing `ingestion-agent` (Phase 2) for extraction + HITL gate → writes facts to memory-api with `source: "drive:{file_id}"` and `metadata.drive_revision_id`.
- **Live read escape hatch:** exposed as `drive-read` MCP tool (see MCP tools below) — explicit opt-in per query, not the default path.
- **Acceptable freshness lag:** ~3min average between save and queryable (5min poll = avg 2.5min + extraction + HITL latency). User confirmed this is acceptable.

### MCP gateway
- **Build custom Python sidecar** (FastAPI + MCP protocol). NOT mcp-proxy, NOT direct endpoints in memory-api.
- **~150 lines target.** Endpoints:
  - `POST /tools/{tool_name}/call` — forwards to registered tool, injects `X-Team-Scope` and `X-User-Sub` (or `acting_user_sub` from bridge JWT) headers.
  - `GET /tools` — lists registered tools (for agent-runtime + LibreChat discovery).
  - `POST /admin/register` — register a new MCP server URL dynamically (DB-backed registry). Admin-only (memory-api admin JWT).
- **Auth pattern:** mirrors agent-runtime — accepts Google OIDC user JWTs and bridge service JWTs. The acting_user_sub propagation pattern from Phase 2 (`apps/openwebui-pipeline/app/pipelines/promotion_manager.py`) applies here.
- **Audit:** every tool call logged via memory-api `POST /v1/audit-log` (Phase 2 audit infrastructure).

### MCP tools — Phase 3 scope
**3 tools shipped in Phase 3** (deck-service deferred to Phase 4):

1. **`scraper`** — URL → text. Reuses `apps/agent-runtime/app/tools/document_loader.py:load_url()`. Cap 50KB body. No JS rendering (out of scope).

2. **`drive-read`** — Live read of one Drive file by ID. Returns text via `files.export`. Bypasses memory-api cache. Use case: "I just saved a doc 30s ago, read it now without waiting for sync."

3. **`calendar`** — Google Calendar read-only. Lists user's events for date range (default: today + next 7 days). Use case: agent contextualizes chat with "you have a meeting with Acme tomorrow."

OAuth scopes — additive to existing Google OAuth client:
- `drive.readonly` (Drive sync + drive-read tool)
- `drive.file` (drive write-back per INT-03 — opt-in only)
- `calendar.readonly` (calendar tool)

### Neo4j data model
- **Rich model:** 4 node types — `Entity`, `Fact`, `User`, `Conversation`.
- **Edges:**
  - `(:Entity)-[:DEPENDS_ON]->(:Entity)` — extracted from facts
  - `(:Fact)-[:MENTIONS]->(:Entity)` — fact contains entity
  - `(:User)-[:PROPOSED]->(:Fact)` — promotion proposer
  - `(:User)-[:APPROVED]->(:Fact)` — promotion approver (4-eyes)
  - `(:Fact)-[:DERIVED_FROM]->(:Conversation|:Drive_Document)` — provenance
- **Sync trigger:** outbox pattern. Every memory-api write that affects graph → row in `neo4j_outbox` table → background worker drains async. Eventually consistent. Avoids dual-write coupling.
- **Entity extraction:** Claude NER on each new fact (extends `extract_facts.py` to emit entities alongside facts). No spaCy / no separate NER service — keeps the LLM-based pattern.
- **Bidirectional consistency:** facts in Postgres are SoT; Neo4j is a read-replica for graph queries. If Neo4j corrupted/lost, can be rebuilt from outbox + replay.

### Memory budget (forecasted)
| Service Phase 3 | mem_limit | Real expected |
|---|---|---|
| Neo4j Community (heap 512m + page cache 256m + JVM overhead) | 1024m | ~800 MB |
| drive-sync (Python sidecar) | 192m | ~100 MB |
| mcp-gateway (Python FastAPI) | 192m | ~80 MB |
| mcp-scraper (Python sidecar) | 128m | ~50 MB |
| mcp-drive-read (Python sidecar) | 128m | ~80 MB |
| mcp-calendar (Python sidecar) | 128m | ~80 MB |
| **Phase 3 total** | **~1.8 GB hard caps** | **~1.2 GB real** |

Combined with Phase 2 real usage (3.7 GB) → **~4.9 GB total used on 7.8 GB VM**, leaves ~2.9 GB headroom. Upgrade only if pressure observed.

## Canonical Refs

- `.planning/ROADMAP.md` — Phase 3 goal + success criteria + entry gate
- `.planning/REQUIREMENTS.md` — definitions of SRCH-05, MCP-01..07, INT-01..04
- `.planning/phases/02-memoire-intelligente-agents/02-07-SUMMARY.md` — ingestion-agent pattern (re-used by drive-sync)
- `.planning/phases/02-memoire-intelligente-agents/02-04-SUMMARY.md` — promotions API + audit log (re-used by MCP gateway)
- `apps/agent-runtime/app/tools/document_loader.py` — re-used by `scraper` MCP tool
- `apps/agent-runtime/app/tools/extract_facts.py` — re-used by drive-sync extraction step
- `apps/openwebui-pipeline/app/pipelines/promotion_manager.py` — auth pattern (acting_user_sub) for MCP gateway
- `~/.claude/projects/D--VSC-xbrain/memory/project_xbrain_memory_layer_decision.md` — confirms Memori is OUT
- `~/.claude/projects/D--VSC-xbrain/memory/project_xbrain_phase2_code_complete.md` — Phase 2 SHIPPED state
- `https://neo4j.com/docs/operations-manual/2025/configuration/configuration-settings/` — Neo4j Community memory tuning
- `https://github.com/sparfenyuk/mcp-proxy` — alternative we DID NOT pick (reference for why custom)
- `https://developers.google.com/drive/api/guides/manage-changes` — Drive `changes.list` API used by drive-sync polling

## Code Context — Reusable Assets

| Asset | Reuse for |
|---|---|
| `apps/agent-runtime/app/tools/document_loader.py:load_url()` | `mcp-scraper` tool |
| `apps/agent-runtime/app/tools/document_loader.py:load_pdf_bytes()` | `drive-sync` PDF handling |
| `apps/agent-runtime/app/tools/extract_facts.py:extract_facts()` | drive-sync extraction step (extend to emit entities for Neo4j) |
| `apps/agent-runtime/app/graphs/ingestion.py` | drive-sync delegates to this graph |
| `apps/agent-runtime/app/auth.py` (`make_bridge_jwt`, `verify_bridge_jwt`) | mcp-gateway service auth |
| `apps/openwebui-pipeline/app/pipelines/promotion_manager.py` (acting_user_sub pattern) | mcp-gateway user identity propagation |
| `infrastructure/docker-compose.yml` (langfuse-minio block + healthcheck patterns) | Neo4j service + 4 MCP services definitions |
| Phase 2 alembic migrations style | New migration `0004_neo4j_outbox.py` for outbox table |

## New Services to Build

1. **`apps/drive-sync/`** — Python sidecar (cron-style or FastAPI scheduled), Google Drive watcher
2. **`apps/mcp-gateway/`** — FastAPI service, MCP protocol gateway with team_scope injection
3. **`apps/mcp-scraper/`** — MCP server (URL → text)
4. **`apps/mcp-drive-read/`** — MCP server (Drive file ID → text, live)
5. **`apps/mcp-calendar/`** — MCP server (Google Calendar read-only)
6. **`infrastructure/docker-compose.yml`** — add Neo4j + 5 new services + 1 volume (`neo4j_data`)

## Deferred to Phase 4 (or later)

- **deck-service MCP tool** (slide generator)
- **Multi-folder per team** Drive mapping
- **Push webhooks** for Drive (vs current polling)
- **OCR for scanned PDFs** (vision LLM)
- **Drive write-back** beyond simple text append (rich formatting, tables, etc.)
- **MCP tool discovery from external registries** (e.g., Anthropic's tool gallery)
- **Apache AGE migration** if Neo4j becomes a RAM bottleneck
- **mcp-proxy adoption** if custom gateway becomes a maintenance burden

## Entry Gate Status

| Original gate | Status |
|---|---|
| Décision VM Phase 3 | ✅ Made: stays e2-standard-2, fallback to e2-standard-4 if `docker stats` shows pressure |
| POC Memori BYODB | ✅ N/A: Memori retired in Phase 2, Claude-based pattern adopted |

**Phase 3 ready for `/gsd:plan-phase 3`.**

## Open Questions for Planner (research will resolve)

- Best Python lib for Google Drive `changes.list` polling? (likely `google-api-python-client`)
- Best MCP protocol library for FastAPI? (`mcp` Python SDK — official Anthropic? community wrapper?)
- Neo4j Python driver async vs sync (we use asyncpg/asyncio everywhere — neo4j supports async since v5.0)
- How to handle Drive file deletions in the sync (mark facts as `validation_status='archived'` vs hard delete)
- Outbox worker: deploy as separate container or in-process in memory-api?
