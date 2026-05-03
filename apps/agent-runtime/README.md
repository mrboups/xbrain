# agent-runtime

xbrain Phase 2 — LangGraph host for stateful agents.

## What it does

Runs **LangGraph** agent graphs with **AsyncPostgresSaver** persistence and
**Human-in-the-Loop** (interrupt + resume across HTTP requests). Talks to
`memory-api` over HTTP via a service JWT — never directly to Postgres or Qdrant.

## Endpoints (all under `/v1/agents/`)

| Verb | Path | Purpose |
|------|------|---------|
| POST | `/{agent_name}/run` | Start a new thread or resume an existing one with `initial_state` |
| GET  | `/{thread_id}/state?agent_name=…` | Fetch current state + interrupt point |
| POST | `/{thread_id}/resume` | Apply optional `state_update`, then continue execution |

Auth: dual JWT (Google OIDC for users, bridge HMAC for service callers) — same
pattern as `memory-api`. Every call must carry `X-Team-Scope: <team-slug>`.

## How to add a new agent

1. Write the graph in `app/graphs/<name>.py`. Define a `StateGraph`, compile with
   `checkpointer=cp` and any `interrupt_before=[…]` you need.
2. Decorate the factory: `@register("my-agent")` (factory must accept `checkpointer`).
3. Import the module from `app/graphs/__init__.py` so the decorator runs at boot.
4. Done — `/v1/agents/my-agent/run` is now live.

## GRAPH_REGISTRY pattern

Single source of truth for which agents this runtime exposes. The map is
`name -> factory(checkpointer) -> CompiledStateGraph`. Agents in plans 02-07
(ingestion) and 02-08 (second-opinion) plug in here.

## Env vars

```
DATABASE_URL              # Postgres DSN (shared with memory-api, separate schema)
MEMORY_API_URL            # http://memory-api:8000
BRIDGE_SHARED_SECRET      # HS256 secret shared with memory-api + bridges
JWT_ALGORITHM             # default HS256
GOOGLE_CLIENT_ID          # for OIDC verification (optional in dev)
ANTHROPIC_API_KEY         # for Claude-powered agents
OPENAI_API_KEY            # for GPT-powered agents
LOG_LEVEL                 # default INFO
```

## Checkpointer tables

Created at boot by `AsyncPostgresSaver.setup()` (idempotent). Migration 0003
in memory-api is a deliberate no-op — those tables are owned by
`langgraph-checkpoint-postgres`, not by Alembic.
