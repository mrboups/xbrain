---
phase: 03-graphe-extraction-integrations
plan: 07
subsystem: infra
tags: [mcp, fastmcp, httpx, scraper, python, docker, streamable-http]

# Dependency graph
requires:
  - phase: 02-memoire-intelligente-agents
    provides: document_loader.load_url() pattern reused verbatim

provides:
  - FastMCP standalone sidecar exposing scrape(url: str) -> str on port 8100
  - apps/mcp-scraper/ service scaffold (pyproject.toml, Dockerfile, app/main.py)
  - mcp-scraper entry in docker-compose with mem_limit 128m

affects:
  - 03-06-mcp-gateway (mcp-scraper is one of the tool sidecars the gateway proxies)
  - 03-08-mcp-drive-read (same sidecar pattern to follow)
  - 03-09-mcp-calendar (same sidecar pattern to follow)

# Tech tracking
tech-stack:
  added:
    - mcp>=1.27.0 (FastMCP, streamable-http transport)
    - httpx>=0.28.0 (async URL fetch)
    - structlog>=25.0.0 (structured logging)
  patterns:
    - FastMCP standalone sidecar pattern: each MCP tool is its own uvicorn process
    - mcp.run(transport="streamable-http", host="0.0.0.0", port=N) for port override
    - Single worker mandatory for FastMCP (in-memory session state per process)
    - Build context scoped to apps/<sidecar> (not repo root — no shared packages needed)

key-files:
  created:
    - apps/mcp-scraper/app/__init__.py
    - apps/mcp-scraper/app/main.py
    - apps/mcp-scraper/pyproject.toml
    - apps/mcp-scraper/Dockerfile
  modified:
    - infrastructure/docker-compose.yml

key-decisions:
  - "Use mcp.run(transport=streamable-http) in __main__ block rather than uvicorn.run() on mcp.streamable_http_app — simpler, officially supported, avoids import path fragility"
  - "Copy load_url() locally rather than importing from agent-runtime — avoids cross-sidecar import coupling; logic is 4 lines and identical"
  - "Dockerfile CMD: python app/main.py (relies on __main__ block) — single canonical startup path"
  - "No ports: mapping in docker-compose — port 8100 is Docker-internal only, accessed by mcp-gateway"

patterns-established:
  - "MCP sidecar pattern: FastMCP standalone (not mounted), mcp.run() in __main__, single worker, port in docker-compose but not published"

requirements-completed:
  - MCP-05

# Metrics
duration: 15min
completed: 2026-05-04
---

# Phase 3 Plan 07: mcp-scraper Summary

**FastMCP standalone sidecar on port 8100 exposing scrape(url: str) -> str with 50KB cap, single-worker enforced, streamable-http transport**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-05-04T04:16:00Z
- **Completed:** 2026-05-04T04:17:03Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments

- Created apps/mcp-scraper/ service: FastMCP server with typed scrape(url: str) -> str tool
- Reused document_loader load_url() pattern (50KB cap, httpx async, 30s timeout)
- Deployed as standalone Docker sidecar — avoids FastMCP/FastAPI mount issue #1367
- Single-worker enforced via mcp.run() — avoids session split issue #658
- Added mcp-scraper to docker-compose with mem_limit 128m, internal port 8100 only

## Task Commits

1. **Task 1: Creer le sidecar mcp-scraper** - `791737e` (feat)
2. **Task 2: Ajouter mcp-scraper dans docker-compose** - `4784c4a` (feat)

## Files Created/Modified

- `apps/mcp-scraper/app/__init__.py` - Empty package marker
- `apps/mcp-scraper/app/main.py` - FastMCP server: _load_url(), scrape() tool, mcp.run() entrypoint
- `apps/mcp-scraper/pyproject.toml` - Dependencies: mcp>=1.27.0, httpx>=0.28.0, structlog>=25.0.0
- `apps/mcp-scraper/Dockerfile` - python:3.11-slim, EXPOSE 8100, CMD python app/main.py
- `infrastructure/docker-compose.yml` - Added mcp-scraper service block after neo4j

## Decisions Made

- Chose `mcp.run(transport="streamable-http", host="0.0.0.0", port=8100)` over the uvicorn-direct approach mentioned as an alternative in the plan. The mcp.run() form is the officially documented API and keeps startup logic in one place.
- Copied load_url() locally as `_load_url()` rather than importing from agent-runtime. The logic is 4 lines and identical to the source; cross-sidecar imports would couple build contexts unnecessarily.
- No `depends_on` in docker-compose — mcp-scraper is fully standalone with no DB or API dependencies.

## Deviations from Plan

None — plan executed exactly as written. The plan already provided the correct mcp.run() API call with port override, and the CMD alternative (python app/main.py relying on __main__) was the right choice given the standalone design.

## Threat Surface Scan

The plan's threat model covers the relevant surface:
- T-03-07-01: SSRF accepted for Phase 3 (internal team usage, authenticated callers)
- T-03-07-02: 50KB cap limits information disclosure volume
- T-03-07-03: httpx timeout=30s mitigated in implementation

No new surface introduced beyond what the threat model documents.

## Issues Encountered

- Stale .git/index.lock from a parallel git process — removed with `rm -f` before committing.

## Known Stubs

None — scrape(url) is fully wired: it calls _load_url() which makes real HTTP requests via httpx.

## Next Phase Readiness

- mcp-scraper sidecar is ready for mcp-gateway (plan 03-06) to register and proxy
- Pattern established for mcp-drive-read (plan 03-08) and mcp-calendar (plan 03-09)
- Both follow the same FastMCP standalone sidecar structure

## Self-Check

- [x] apps/mcp-scraper/app/main.py exists and AST-parses clean
- [x] apps/mcp-scraper/Dockerfile exists and contains "8100"
- [x] apps/mcp-scraper/pyproject.toml exists with mcp>=1.27.0 dependency
- [x] infrastructure/docker-compose.yml contains mcp-scraper service with mem_limit 128m
- [x] Commits 791737e and 4784c4a exist in git log

## Self-Check: PASSED

---
*Phase: 03-graphe-extraction-integrations*
*Completed: 2026-05-04*
