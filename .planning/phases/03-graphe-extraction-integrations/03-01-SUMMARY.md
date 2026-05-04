---
phase: 03-graphe-extraction-integrations
plan: "01"
subsystem: infrastructure
tags: [neo4j, docker-compose, graph-store, phase3-foundation]
dependency_graph:
  requires: []
  provides: [neo4j-service, neo4j_data-volume, NEO4J_env_vars]
  affects: [infrastructure/docker-compose.yml, .env.example]
tech_stack:
  added: [neo4j:2026.04.0-community]
  patterns: [healthcheck-start_period, internal-network-only, env-sourced-credentials]
key_files:
  modified:
    - infrastructure/docker-compose.yml
    - .env.example
decisions:
  - "NEO4J_AUTH format: neo4j/${NEO4J_PASSWORD} — Community Edition fixed username + env password"
  - "No host port exposure (7474/7687) — internal xbrain_net only, Bolt access via service name"
  - "start_period 60s mandatory — Neo4j JVM takes 20-40s to initialize before HTTP/Bolt ready"
  - "127.0.0.1 in healthcheck wget (not localhost) — consistent with existing pattern, avoids IPv6"
  - "NEO4J_PLUGINS=[] — disables plugin download on boot, Community Edition needs none for Phase 3"
metrics:
  duration: "~5 minutes"
  completed: "2026-05-04"
  tasks_completed: 2
  tasks_total: 2
  files_modified: 2
---

# Phase 3 Plan 01: Neo4j Infrastructure — Summary

Neo4j Community 2026.04.0 added to docker-compose as internal-only graph store with heap 512m + page cache 256m, persistent volume, and env-sourced credentials.

## Tasks Completed

| Task | Commit | Files |
|------|--------|-------|
| 1 — Service neo4j + volume dans docker-compose | 5878bda | infrastructure/docker-compose.yml |
| 2 — Variables NEO4J_* dans .env.example | 1a5b427 | .env.example |

## What Was Built

**docker-compose.yml changes:**
- `neo4j_data: { name: neo4j_data }` added to the `volumes:` block
- `neo4j` service added after the Langfuse stack (last service in the file)
  - `image: neo4j:2026.04.0-community`
  - Memory tuned: heap initial/max 512m, page cache 256m, mem_limit 1024m
  - `NEO4J_AUTH: neo4j/${NEO4J_PASSWORD}` — no hardcoded credentials
  - `NEO4J_PLUGINS: "[]"` — no plugin download at boot
  - No `ports:` — bolt (7687) and browser (7474) internal only
  - `networks: [xbrain_net]`
  - healthcheck: wget on 127.0.0.1:7474 + grep `neo4j`, interval 30s, retries 10, start_period 60s

**.env.example changes:**
- New section `=== Neo4j Community (plan 03-01) ===` after the Langfuse block
- `NEO4J_URI=bolt://neo4j:7687` — driver uses Bolt, not HTTP
- `NEO4J_USER=neo4j` — fixed username in Community Edition
- `NEO4J_PASSWORD=__FILL_RANDOM_32__` — with inline doc: `openssl rand -base64 24`

## Verification Results

All 4 checks passed:
- `neo4j` in `doc['services']`
- `NEO4J_PASSWORD` present in .env.example (non-comment line)
- YAML parses without exception
- No `ports:` key on neo4j service

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None. This plan is infrastructure-only; no application code, no data-binding.

## Threat Flags

No new network surface beyond what the plan's threat model already covers (T-03-01-01..03). Bolt port 7687 is not exposed to the host.

## Self-Check

- [x] `infrastructure/docker-compose.yml` — file exists and YAML-valid
- [x] `.env.example` — NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD present
- [x] Commit 5878bda exists (Task 1)
- [x] Commit 1a5b427 exists (Task 2)
- [x] No neo4j ports exposed on host

## Self-Check: PASSED
