---
phase: 06-marketing-docs
plan: "06"
subsystem: marketing-site/docs
tags: [docs, langgraph, graphiti, api-reference, agents, hitl]
dependency_graph:
  requires: [06-01]
  provides: [agents-docs, graphiti-docs, api-reference-docs]
  affects: [marketing-site/docs]
tech_stack:
  added: []
  patterns: [sidebar-14-links, breadcrumb, code-block, callout, docs-table]
key_files:
  created:
    - marketing-site/docs/agents.html
    - marketing-site/docs/graphiti.html
    - marketing-site/docs/api-reference.html
  modified: []
decisions:
  - "api-reference.html uses h3 per endpoint group and h2 per category for dense but navigable structure"
  - "27-row quick reference table added to api-reference.html as developer cheat sheet"
  - "graphiti.html uses ASCII flow diagram in code-block to show fail-soft async pipeline"
metrics:
  duration: "~12 minutes"
  completed: "2026-05-06T18:46:03Z"
  tasks_completed: 3
  tasks_total: 3
  files_created: 3
  lines_written: 1907
---

# Phase 06 Plan 06: Agents + Graphiti + API Reference Documentation Summary

**One-liner:** Three dense technical docs pages: LangGraph HITL agent-runtime (499 lines), graphiti temporal fact extraction with fail-soft architecture (440 lines), and complete memory-api reference covering all 27 endpoints (968 lines).

## Tasks Completed

| Task | Name | Commit | Files | Lines |
|------|------|--------|-------|-------|
| 1 | docs/agents.html — LangGraph Agents + HITL | a32b7ff | marketing-site/docs/agents.html | 499 |
| 2 | docs/graphiti.html — Graphiti Temporal Fact Extraction | 79a46ef | marketing-site/docs/graphiti.html | 440 |
| 3 | docs/api-reference.html — Complete API Reference | 30daf27 | marketing-site/docs/api-reference.html | 968 |

## Content Summary

### agents.html (499 lines)

Documents the LangGraph agent-runtime (port 9100) with:
- What is agent-runtime: property table (LangGraph 1.1.0, port, PostgresSaver, Langfuse, mcp_gateway_client.py)
- LangGraph architecture: full StateGraph example with add_node, add_conditional_edges, compile with AsyncPostgresSaver
- HITL interrupt(): complete Python example showing state persistence to PostgreSQL, admin approval flow in Open WebUI
- Agent resume: graph.ainvoke with thread_id from PostgresSaver checkpoint
- MCP tools: MCPGatewayClient usage from agent nodes (scrape_url + list_events examples)
- CANONICAL facts injection: GET /v1/system-prompt in system_prompt_builder
- Langfuse observability: CallbackHandler setup with internal Docker DNS
- Available agents table: doc_ingestion, promotion_workflow, rag_conversation, github_sync

### graphiti.html (440 lines)

Documents the graphiti-service FastAPI wrapper (port 8300) with:
- What is Graphiti: fail-soft async enrichment on every memory upsert
- Why temporal facts: concrete examples of temporal queries ("who was tech lead in Q3 2025?")
- Architecture ASCII flow diagram: memory-api → asyncio.create_task → graphiti-service → graphiti-core → Neo4j
- Service API: GET /v1/healthz and POST /v1/ingest with full curl examples and request field table
- Configuration table: NEO4J_*, OPENAI_API_KEY, ANTHROPIC_API_KEY, SEMAPHORE_LIMIT, GRAPHITI_SERVICE_URL
- Warning callout: OPENAI_API_KEY required for text-embedding-3-small even with Anthropic LLM
- Contradiction detection: temporal edge example (Alice WAS_TECH_LEAD / Bob IS_TECH_LEAD)
- Graph query via GET /v1/graph/neighbors with response example
- lifespan() initialization note: graphiti_client must initialize within asyncio event loop

### api-reference.html (968 lines)

Documents all 27 memory-api endpoints across 15 route modules with:
- Base URL, auth headers (Authorization: Bearer + X-Team-Scope), auth mechanisms (Google/GitHub/Bridge JWT)
- Health: GET /v1/healthz (no auth)
- User profile: GET /v1/me, GET /v1/me/github
- Memory: POST /v1/memory/upsert (all 7 tagging fields, curl example), GET /v1/memory/search (query params table), GET/PATCH/DELETE /v1/memory/{id}
- 405 documented: PATCH truth_level blocked with example response pointing to promotions API
- 422 documented: missing required tagging field error
- Conversations: POST, GET list, GET single
- Messages: POST (upsert-silent pattern for bridge services)
- Promotions: POST request, GET list (admin), PATCH approve/reject (admin)
- System prompt: GET /v1/system-prompt with CANONICAL facts response example
- Graph: GET /v1/graph/neighbors with temporal relationship response
- Audit: GET /v1/audit with full query params table
- Teams: GET /v1/teams, POST /v1/admin/teams
- Drive: GET /v1/admin/drive/auth, POST/GET /v1/admin/drive/mappings, POST /v1/drive/webhook
- Projects: POST/GET /v1/admin/projects
- Error codes table: 400, 401, 403, 404, 405, 422, 500 with causes
- 27-row quick reference table with method, path, auth, description

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None. All content is substantive and sourced from the actual implementation.

## Threat Flags

None. api-reference.html documents auth requirements (Bearer + X-Team-Scope) explicitly for every endpoint, including the admin-only restriction pattern. No new security surface introduced — documentation only.

## Self-Check

- [x] marketing-site/docs/agents.html exists (499 lines, > 120 minimum)
- [x] marketing-site/docs/graphiti.html exists (440 lines, > 100 minimum)
- [x] marketing-site/docs/api-reference.html exists (968 lines, > 250 minimum)
- [x] agents.html contains: LangGraph, HITL, interrupt, PostgresSaver, mcp_gateway_client, Langfuse, code-block
- [x] graphiti.html contains: graphiti-service, graphiti-core, OPENAI_API_KEY, SEMAPHORE_LIMIT, temporal, contradiction, code-block
- [x] api-reference.html contains: POST /v1/memory/upsert, GET /v1/memory/search, POST /v1/promotions, GET /v1/graph/neighbors, GET /v1/system-prompt, 405, 422, 27 code-blocks
- [x] 32 HTTP method+path occurrences in api-reference.html (>= 15 required)
- [x] All 3 pages: sidebar with 14 links, active class correct, breadcrumb, footer present
- [x] Commits: a32b7ff (agents), 79a46ef (graphiti), 30daf27 (api-reference)

## Self-Check: PASSED
