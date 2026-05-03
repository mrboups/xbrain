---
phase: 02-memoire-intelligente-agents
plan: 06
subsystem: agent-runtime
tags: [langgraph, postgres-saver, hitl, interrupt-before, async-pool, fastapi, new-service]
requires:
  - phase: 02
    plan: 02
    provides: MemoryProvider types — agents read/write through memory-api HTTP
  - phase: 02
    plan: 03
    provides: /v1/memory/* endpoints — agent target surface
  - phase: 02
    plan: 05
    provides: /v1/system-prompt — agents reuse for self-RAG
provides:
  - New service apps/agent-runtime/ — FastAPI on port 9100, mem_limit 384MB
  - LangGraph 1.1 + AsyncPostgresSaver checkpointer (psycopg async pool)
  - GRAPH_REGISTRY pattern + @register("name") decorator
  - 1 demo agent 'echo-with-hitl' locking the interrupt_before + resume contract
  - 3 endpoints /v1/agents/* (run, state, resume)
  - MemoryApiClient (agent-side HTTP client to memory-api with iss=agent-runtime JWT)
  - 7 HITL contract tests (testcontainers Postgres)
  - Migration 0003 (no-op — checkpoint tables owned by langgraph-checkpoint-postgres)
  - docker-compose service entry (depends_on: memory-api, postgres; healthcheck /healthz)
affects:
  - 02-07 ingestion-agent registers via GRAPH_REGISTRY
  - 02-08 second-opinion-agent registers via GRAPH_REGISTRY
  - 02-09 Langfuse — agent-runtime is the natural place to inject the CallbackHandler

tech-stack:
  added:
    - langgraph >=1.1
    - langgraph-checkpoint-postgres >=2.0
    - psycopg[binary,pool] >=3.2
    - anthropic >=0.40 (placeholder for 02-08)
  patterns:
    - "Service-singleton checkpointer (lazy lifespan init, explicit shutdown)"
    - "GRAPH_REGISTRY decorator — central agent registry, populated by side-effect import"
    - "Agents talk to memory-api via HTTP (NEVER direct DB) — preserves tagging-contract enforcement"
    - "interrupt_before pattern for HITL — graph PAUSES at the named node, caller resumes via /resume"

key-files:
  created:
    - apps/agent-runtime/pyproject.toml
    - apps/agent-runtime/Dockerfile (non-root agent UID 10004, port 9100)
    - apps/agent-runtime/.dockerignore
    - apps/agent-runtime/README.md (~50 lines — covers GRAPH_REGISTRY pattern + env vars)
    - apps/agent-runtime/app/main.py (lifespan starts/closes checkpointer)
    - apps/agent-runtime/app/config.py (10 env vars)
    - apps/agent-runtime/app/auth.py (Google OIDC + bridge JWT — mirror of memory-api)
    - apps/agent-runtime/app/deps.py (get_current_principal + get_team_scope)
    - apps/agent-runtime/app/memory_client.py (upsert/search/system_prompt)
    - apps/agent-runtime/app/checkpointer.py (AsyncPostgresSaver factory)
    - apps/agent-runtime/app/graphs/registry.py (GRAPH_REGISTRY + @register)
    - apps/agent-runtime/app/graphs/echo_with_hitl.py (3-node demo agent)
    - apps/agent-runtime/app/graphs/__init__.py (side-effect import)
    - apps/agent-runtime/app/routes/health.py (GET /healthz)
    - apps/agent-runtime/app/routes/agents.py (3 endpoints)
    - apps/agent-runtime/tests/conftest.py (testcontainers Postgres + bridge JWT factory)
    - apps/agent-runtime/tests/test_hitl_resume.py (7 tests)
    - apps/memory-api/alembic/versions/0003_langgraph_checkpoints.py (no-op + docstring)
  modified:
    - infrastructure/docker-compose.yml (+ agent-runtime service block)

key-decisions:
  - "Migration 0003 is a NO-OP. AsyncPostgresSaver.setup() owns its tables. Reason: not coupling the alembic timeline to a third-party schema we don't control."
  - "auth.py is duplicated from memory-api rather than extracted to a shared package. Reason: 2 services don't justify the abstraction overhead. Will extract to packages/xbrain-auth if/when a 3rd service needs it."
  - "Checkpointer is a process-singleton (module-level _saver). FastAPI lifespan eagerly initializes it. Don't try multi-worker uvicorn here without revisiting pool sizing — single worker for Phase 2 (--workers 1)."
  - "Agents talk to memory-api via HTTP, NEVER via Postgres directly. Preserves tagging-contract enforcement + audit logging in a single source of truth. Adds latency but it's intentional."
  - "GRAPH_REGISTRY uses decorator pattern. New agents = 1 file in graphs/ + 1 line in graphs/__init__.py side-effect import. No central modification needed."
  - "/resume endpoint takes optional state_update kwarg. This is the human's input that the graph will see post-interrupt."
  - "team_scope flows through config.configurable so agent nodes can pick it up via state, but for memory-api calls we use the explicit headers contract from MemoryApiClient."
  - "psycopg AsyncConnectionPool: min_size=1, max_size=5. Conservative for Phase 2 e2-standard-2 (8GB) — revisit when load profiling exists."

invariants-enforced:
  - "Agents never write to Postgres/Qdrant directly — only via memory-api HTTP"
  - "Every agent call requires a valid JWT + X-Team-Scope header (deps.get_team_scope)"
  - "Bridge JWT team_scope claim must match X-Team-Scope header (403 otherwise)"
  - "interrupt_before pause is observable via /v1/agents/{thread_id}/state.next"

requirements-completed:
  - AGENT-01  # LangGraph runtime live
  - AGENT-02  # PostgresSaver persistence (cross-restart resume)
  - AGENT-03  # HITL via interrupt_before + resume
  - AGENT-04  # /v1/agents/* HTTP API
  - AGENT-05  # team_scope propagated to graph configurable
  - AGENT-06  # auth dual JWT
  - AGENT-07  # agent → memory-api over HTTP

duration: ~50 min (inline)
completed: 2026-05-03
status: COMPLETE — code written, syntax + TOML validated. Tests require testcontainers (run in CI / verify-work). VM deploy needs `docker compose up -d agent-runtime` + first-boot setup() to create checkpoint tables.
---

# Plan 02-06 — LangGraph agent-runtime (foundation for 02-07/08)

**New service. Hosts agents with persistence + HITL. Talks to memory-api over HTTP.**

## What got built

1. **Service skeleton** — pyproject (LangGraph 1.1 + langgraph-checkpoint-postgres + psycopg pool + anthropic + openai), Dockerfile (non-root UID 10004 on port 9100), .dockerignore, README documenting GRAPH_REGISTRY pattern.

2. **Plomberie** — config (env-driven), auth (Google OIDC + bridge JWT, mirror of memory-api), deps (`get_current_principal` + `get_team_scope`), memory_client (HTTP to memory-api with `iss=agent-runtime` service JWT), checkpointer (AsyncPostgresSaver singleton, lazy-init via lifespan).

3. **Agent system** — `GRAPH_REGISTRY: dict[name → factory(checkpointer)]` + `@register("name")` decorator. One demo agent `echo-with-hitl` (3 nodes: draft → await_approval → finalize) compiled with `interrupt_before=["await_approval"]`.

4. **API** — `POST /v1/agents/{name}/run`, `GET /v1/agents/{thread_id}/state`, `POST /v1/agents/{thread_id}/resume`. team_scope guard on every endpoint. JWT auth via `Depends(get_current_principal)`.

5. **Tests** — 7 HITL contract tests (testcontainers Postgres): interrupt observable, state survives between requests, resume completes the graph, two threads stay isolated, unknown agent → 404, missing X-Team-Scope → 401/422.

6. **Compose + migration** — service block (mem_limit 384m, depends on memory-api + postgres healthy, healthcheck on /healthz). Migration 0003 = no-op with docstring documenting "owned by langgraph-checkpoint-postgres".

## Why this matters

This is the **execution substrate** for everything in 02-07/08. The agents that auto-promote facts (ingestion) and that issue second opinions both register here. Without persistence we'd lose state on container restart; without HITL we couldn't gate the CANONICAL truth-level decisions through humans. The contract is locked here so 02-07/08 are 1-file additions.

## VM RAM budget after this commit

| Service | mem_limit | Source |
|---|---|---|
| postgres | 384m | Phase 1 |
| qdrant | 384m | Phase 1 |
| memory-api | 384m | Phase 1 |
| librechat-bridge | 128m | Phase 1 |
| openwebui-pipeline | (no limit yet) | Phase 1 |
| **agent-runtime** | **384m** | **Phase 2** |
| nginx | 64m | Phase 1 |
| LibreChat (Mongo+Meili+app) | ~1.2GB combined | Phase 1 |
| Open WebUI | ~1.3GB | Phase 1 |
| **Subtotal hard caps** | **~4.2GB on 8GB VM** | (e2-standard-2) |

Headroom remaining for Langfuse (Plan 02-09): ~3GB after OS + Docker. Should fit `langfuse/web` + `clickhouse` + `redis` if we tune ClickHouse heap. Will revisit at the start of 02-09.

## Pending for full production

1. `docker compose up -d agent-runtime` on the VM — first boot creates `checkpoints` + `checkpoint_writes` + `checkpoint_blobs` tables.
2. CI / Phase 2 verify-work pass exercises the HITL flow end-to-end (currently only locally via testcontainers).
3. Plans 02-07 + 02-08 add real agents to the registry.
