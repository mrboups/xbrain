---
phase: 02-memoire-intelligente-agents
plan: 09
subsystem: observability
tags: [langfuse, clickhouse, redis, traces, langgraph-callback, chat-completion-trace, self-hosted]
requires:
  - phase: 02
    plan: 06
    provides: agent-runtime service + GRAPH_REGISTRY (Langfuse callback hooks here)
provides:
  - 4 new docker-compose services — langfuse, langfuse-worker, langfuse-clickhouse, langfuse-redis
  - 2 new volumes — langfuse_clickhouse_data, langfuse_redis_data
  - nginx server block — langfuse.x.dejavu.cat → langfuse:3000
  - infrastructure/scripts/init-langfuse-db.sh — idempotent CREATE DATABASE langfuse
  - apps/agent-runtime/app/observability.py — make_callback_handler() (None when unconfigured)
  - apps/agent-runtime/app/routes/agents.py wired with handler in run + resume paths
  - apps/openwebui-pipeline/app/observability.py — trace_chat_completion() helper
  - apps/openwebui-pipeline/app/main.py — 4 instrumentation points (anthropic stream/non-stream + openai stream/non-stream)
  - .env.example extended with 8 LANGFUSE_* vars and bootstrap notes
  - Cross-service env wiring (LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST → agent-runtime + openwebui-pipeline)
affects:
  - End of Phase 2 — observability completes the picture (no more flying blind on agents/LLM costs)
  - Phase 3 may add Langfuse SDK in librechat-bridge for memory-api side traces

tech-stack:
  added:
    - clickhouse-server:24.8 (Langfuse OLAP store)
    - redis:7-alpine (Langfuse queue / cache)
    - langfuse:3 (web UI + worker images)
    - langfuse>=2.55,<3 in agent-runtime + openwebui-pipeline pyprojects
  patterns:
    - "Lazy-import Langfuse SDK — observability is dead code when keys aren't set"
    - "Per-run CallbackHandler with session_id=thread_id — UI groups traces by thread"
    - "Best-effort tracing — try/except + log.warning, NEVER block the user response"
    - "trace_chat_completion is called AFTER yielding [DONE] in stream paths so latency does not include the trace push"

key-files:
  created:
    - apps/agent-runtime/app/observability.py (~60 lines)
    - apps/openwebui-pipeline/app/observability.py (~100 lines)
    - infrastructure/scripts/init-langfuse-db.sh
    - .planning/phases/02-memoire-intelligente-agents/02-09-SUMMARY.md
  modified:
    - infrastructure/docker-compose.yml (+ 4 services, + 2 volumes, + LANGFUSE_* env on agent-runtime + openwebui-pipeline)
    - infrastructure/nginx/conf.d/10-xbrain.conf (+ langfuse.x.dejavu.cat server block)
    - .env.example (+ 8 LANGFUSE_* vars with bootstrap recipe)
    - apps/agent-runtime/pyproject.toml (+ langfuse>=2.55,<3)
    - apps/agent-runtime/app/config.py (+ LANGFUSE_PUBLIC_KEY / SECRET_KEY / HOST)
    - apps/agent-runtime/app/routes/agents.py (handler wired in run + resume; principal sub now flows through)
    - apps/openwebui-pipeline/pyproject.toml (+ langfuse>=2.55,<3)
    - apps/openwebui-pipeline/app/config.py (+ LANGFUSE_PUBLIC_KEY / SECRET_KEY / HOST)
    - apps/openwebui-pipeline/app/main.py (+ 4 trace_chat_completion calls + time tracking)

key-decisions:
  - "langfuse 2.x SDK pinned (>=2.55,<3). Reason: 2.x exposes the LangChain CallbackHandler at langfuse.callback (the API the plan expects). 3.x is OTEL-based and would require switching to OpenTelemetry instrumentation everywhere — out of scope for Phase 2. Phase 3 may migrate."
  - "Langfuse stack DB shares the same Postgres instance via a separate logical database (`langfuse`). Pros: one less running container, shared backups. Cons: heavy Langfuse migrations could slow the same instance memory-api uses. Acceptable at Phase 2 scale; will revisit at >100k traces/day."
  - "ClickHouse mem_limit=2g is the floor. Below this Langfuse migrations OOM-kill on first boot. This is the single biggest VM-budget hit of Phase 2."
  - "Redis password protected (--requirepass) — Phase 2 net is internal but defense-in-depth matters when ClickHouse exposes ports."
  - "LANGFUSE_PUBLIC_KEY / SECRET_KEY are deliberately empty in .env.example. They are obtained AFTER first login via the Langfuse UI → Settings → API Keys. Code degrades gracefully — agent-runtime + openwebui-pipeline boot fine without them."
  - "trace_chat_completion is called AFTER yielding `data: [DONE]` in streaming paths. Reason: the trace push is non-zero latency (HTTP to Langfuse). Pushing it AFTER the user has already seen [DONE] keeps perceived latency unchanged."
  - "agent-runtime callback handler uses session_id=thread_id. This is what makes the Langfuse UI group all traces from one /run + N /resume calls together — critical for HITL agents like ingestion."
  - "Bootstrap admin user is created by Langfuse via LANGFUSE_INIT_USER_EMAIL/_NAME on first boot only. Subsequent boots ignore those vars (Langfuse intentional behaviour)."

invariants-enforced:
  - "Observability NEVER blocks user response — try/except + log.warning around every Langfuse call"
  - "Empty LANGFUSE_PUBLIC_KEY OR LANGFUSE_SECRET_KEY ⇒ instrumentation off, no warnings, no crashes"
  - "trace_chat_completion runs AFTER stream completion so latency measurement is wall-clock-accurate"
  - "Per-team filtering possible via metadata.team_scope (set on every trace)"

requirements-completed:
  - OBS-02   # LangGraph callback handler instruments every agent run
  - OBS-03   # chat completion trace per LLM call (Anthropic + OpenAI paths, stream + non-stream)
  - OBS-05   # admin can filter Langfuse by team_scope to see per-team consumption

duration: ~35 min (inline)
completed: 2026-05-03
status: CODE COMPLETE — service stack + instrumentation written. Production deploy requires the 6 USER ACTIONS below before traces will appear.

user_actions_required:
  1: |
    Add Cloudflare DNS record:
        langfuse.x.dejavu.cat  A  __VM_HOST__  (orange cloud / proxy on)
  2: |
    Generate the 5 Langfuse secrets and paste into .env on the VM:
        openssl rand -base64 32  # → LANGFUSE_CLICKHOUSE_PASSWORD
        openssl rand -base64 32  # → LANGFUSE_REDIS_PASSWORD
        openssl rand -base64 64  # → LANGFUSE_SALT
        openssl rand -hex 32     # → LANGFUSE_ENCRYPTION_KEY
        openssl rand -base64 64  # → LANGFUSE_NEXTAUTH_SECRET
    Also set LANGFUSE_POSTGRES_URL using the same POSTGRES_PASSWORD.
  3: |
    Create the Langfuse Postgres database:
        bash infrastructure/scripts/init-langfuse-db.sh
    (or directly: docker exec xbrain-postgres psql -U xbrain -c "CREATE DATABASE langfuse OWNER xbrain;")
  4: |
    Deploy the Langfuse stack:
        docker compose pull langfuse langfuse-worker langfuse-clickhouse langfuse-redis
        docker compose up -d langfuse-clickhouse langfuse-redis
        # wait ~30s for clickhouse healthcheck
        docker compose up -d langfuse-worker langfuse
        # wait ~60s for langfuse healthcheck (first-boot migrations)
  5: |
    Visit https://langfuse.x.dejavu.cat → first-boot login uses LANGFUSE_INIT_USER_EMAIL.
    Project "xbrain" is auto-created. Go to Settings → API Keys → New API Key.
    Paste both keys into .env on the VM:
        LANGFUSE_PUBLIC_KEY=pk-lf-...
        LANGFUSE_SECRET_KEY=sk-lf-...
  6: |
    Recreate agent-runtime + openwebui-pipeline so they pick up the keys:
        docker compose up -d --force-recreate agent-runtime openwebui-pipeline
    Then send a test message in OWUI — verify a new trace shows up at
    https://langfuse.x.dejavu.cat under Traces within ~30s.
---

# Plan 02-09 — Langfuse self-hosted observability

**Last Phase 2 plan. 4 new services, 2 instrumentation surfaces, 8 env vars, 6 user actions.**

## What got built

1. **Stack** — `langfuse` (web UI :3000) + `langfuse-worker` + `langfuse-clickhouse` (OLAP) + `langfuse-redis` (queue). Mem total ~3.6 GB. Postgres DB lives in the shared instance via a separate logical database `langfuse`.

2. **Network** — nginx server block routes `langfuse.x.dejavu.cat` → `langfuse:3000` (Cloudflare Flexible SSL like the rest).

3. **agent-runtime instrumentation** — `make_callback_handler()` returns a Langfuse `CallbackHandler` (per-thread session_id, tags `team:X` + `agent:Y`). Wired into the `/v1/agents/{name}/run` and `/v1/agents/{thread_id}/resume` endpoints. Graceful degrade — `None` when unconfigured.

4. **openwebui-pipeline instrumentation** — `trace_chat_completion()` helper. Called from all 4 LLM paths (Anthropic stream + non-stream, OpenAI stream + non-stream). Captures latency_ms, input/output tokens (when the SDK exposes them), team_scope, model, sub. Streaming paths trace AFTER `data: [DONE]` so the user-perceived latency is unchanged.

5. **Cross-service env** — `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` propagated to both services in compose. Empty defaults so the stack boots fine before the first-login API-key generation.

## VM RAM budget after Phase 2 complete

| Service | mem_limit |
|---|---|
| postgres | 384m |
| qdrant | 384m |
| memory-api | 384m |
| librechat-bridge | 128m |
| openwebui-pipeline | 192m |
| agent-runtime | 384m |
| nginx | 64m |
| LibreChat (Mongo + Meili + app) | ~1200m |
| Open WebUI | ~1280m |
| backup container (cron-style) | 256m |
| **Subtotal Phase 2 services** | **~4.6 GB** |
| langfuse-clickhouse | 2048m |
| langfuse-worker | 768m |
| langfuse | 768m |
| langfuse-redis | 96m |
| **Subtotal Langfuse** | **~3.7 GB** |
| **Phase 2 grand total hard caps** | **~8.3 GB on 8 GB VM** |

⚠️ **Tight on e2-standard-2 (8 GB).** OS + Docker overhead = ~600 MB → we are at the edge. Two mitigation paths:
- **Path A (preferred)**: bump VM to e2-standard-4 (16 GB, ~98€/mo) at the start of Phase 3.
- **Path B (cheaper)**: Langfuse-only on a separate e2-small VM (2 GB) and leave xbrain on e2-standard-2 — but that wastes RAM on the small VM.

**Recommendation**: stay on e2-standard-2 for now, monitor with `docker stats` after deploy. If ClickHouse + Langfuse start hitting their limits, immediate upgrade to e2-standard-4.

## What's NOT in this plan

- librechat-bridge instrumentation — bridge writes to memory-api, not directly to LLMs, so it'd just trace HTTP calls. Phase 3 if useful.
- Langfuse Phase-3 advanced features (eval, prompts, datasets) — those are UI-only setup, no code change.
- Migrating to langfuse 4.x OTEL SDK — big refactor, defer to Phase 3.
- LibreChat's own LLM calls — they bypass openwebui-pipeline. Phase 3 may add a LibreChat plugin to forward traces.

## Why this matters

Phase 2 added 3 agents, a multi-LLM fanout, RAG enrichment on every new chat, and a 4-eyes promotion workflow. Without Langfuse we'd be flying blind: no per-team cost breakdown, no error rate visibility, no agent-run replay. With it, the admin gets one dashboard answering "what did our team spend on Claude vs GPT this week?" and "which agent run failed yesterday?" — which is the difference between Phase 2 being a demo and Phase 2 being a system.
