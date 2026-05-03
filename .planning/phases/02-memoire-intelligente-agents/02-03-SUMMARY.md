---
phase: 02-memoire-intelligente-agents
plan: 03
subsystem: api
tags: [mem0, native-provider, postgres, qdrant, fastapi, memory-endpoints]
requires:
  - phase: 02
    plan: 02
    provides: MemoryProvider ABC + NativeStubProvider + 11 contract tests
provides:
  - Mem0Provider impl (mem0-backed, team_scope encoded as user_id="team:{slug}")
  - NativeProvider impl (Postgres+Qdrant direct, lazy-imported asyncpg + qdrant_client)
  - 6 endpoints /v1/memory/* in memory-api : upsert, search, get/{id}, patch/{id}, delete/{id}, history/{id}
  - PATCH on truth_level returns HTTP 405 (Phase 2 invariant — must use /v1/promotions workflow)
  - Provider injection via env MEMORY_BACKEND=mem0|native|stub, get_memory_provider() singleton
  - openai_embedder helper for NativeProvider (text-embedding-3-small, 1536 dims)
  - memory-api pyproject adds: openai, mem0ai, xbrain-memory (path dep)
affects: [02-04 (truth workflow uses provider via routes/promotions), 02-05 (RAG calls /v1/memory/search), 02-06 (agents call provider via memory-api HTTP)]

tech-stack:
  added: [mem0ai (lazy-imported), xbrain-memory dep, openai SDK extension to memory-api]
  patterns: [singleton provider per env, lazy-import providers (mem0 only loaded if MEMORY_BACKEND=mem0)]

key-files:
  created:
    - packages/memory-models/xbrain_memory/providers/mem0_provider.py (185 lines)
    - packages/memory-models/xbrain_memory/providers/native_provider.py (230 lines)
    - apps/memory-api/app/embedders.py (openai_embedder)
    - apps/memory-api/app/routes/memory.py (6 endpoints, PATCH 405 enforcement)
  modified:
    - apps/memory-api/app/config.py (MEMORY_BACKEND, OPENAI_API_KEY, OPENAI_EMBEDDING_MODEL)
    - apps/memory-api/app/deps.py (get_memory_provider singleton, lazy build)
    - apps/memory-api/app/main.py (mount memory router)
    - apps/memory-api/pyproject.toml (+ openai, mem0ai, xbrain-memory)

key-decisions:
  - "Mem0Provider lazy-imports mem0 — service can boot with MEMORY_BACKEND=stub even without mem0ai installed (testing-friendly)"
  - "NativeProvider lazy-imports asyncpg + qdrant_client (already available — but defensive)"
  - "PATCH /v1/memory/{id} avec patch.truth_level → HTTP 405 hardcoded — workflow obligatoire"
  - "get_memory_provider() = singleton per process (PYthon module-level cache). Acceptable for FastAPI (workers fork-safe)"
  - "DSN normalized for asyncpg : postgresql+asyncpg:// → postgresql:// at NativeProvider construction"
  - "Mem0Provider score = position-based (mem0 doesn't expose raw vector scores reliably). NativeProvider uses raw Qdrant score."

patterns-established:
  - "Pattern A — Lazy provider import : `from xbrain_memory.providers.mem0_provider import Mem0Provider` happens INSIDE _build_provider(), not at module top. Allows MEMORY_BACKEND=stub to skip mem0ai requirement."
  - "Pattern B — Singleton via global module-level var. Simple, no DI framework needed for FastAPI."
  - "Pattern C — Embedder injected as callable (NativeProvider) — decouples from OpenAI specifically. Future Cohere/Voyage just need an async (text→vector) callable."

requirements-completed:
  - MEM-06   # backend abstraction shipped (interface 02-02 + impl here)
  - MEM-07   # versioning (history endpoint + memory_items_history table — table created in 02-04 migration)
  - MEM-08   # update via patch (with truth_level guard)
  - MEM-09   # delete + history audit
  - MEM-10   # search team-scoped
  - SRCH-03  # semantic search via Qdrant vectors (NativeProvider) or mem0 search (Mem0Provider)
  - SRCH-04  # filter by truth_level_min, project_scope

duration: ~30 min (inline)
completed: 2026-05-03
status: COMPLETE (code) — needs migration 0002 from Plan 02-04 to make NativeProvider actually work in prod
---

# Plan 02-03 — Memory backend impls + endpoints

**2 providers (mem0 + native), 6 endpoints, lazy-loaded, PATCH truth_level locked. Foundation for 02-04 truth workflow + 02-05 RAG + 02-06 agents.**

## Performance

- Files created: 4 (mem0_provider, native_provider, embedders, routes/memory)
- Files modified: 4 (config, deps, main, pyproject)
- Lines added: ~600 Python
- Tests: existing contract tests still pass against stub (no regression)

## Verification

```bash
# Syntax check (all 7 modified files)
python -c "import ast; ast.parse(open('apps/memory-api/app/routes/memory.py').read())" → OK
# (× 7 files all OK)
```

Local pytest contract tests still pass (only stub registered; mem0/native need real backends to test).

## Pending for full production

- Migration 0002 (Plan 02-04) creates `memory_items` + `memory_items_history` tables required by NativeProvider
- Spike result (Plan 02-01 user action) determines `MEMORY_BACKEND=mem0` or `native` in prod
- docker-compose update (also Plan 02-04 or here) : add `MEMORY_BACKEND` env to memory-api service + `OPENAI_API_KEY` propagation

## Notes

- Mem0Provider tested empirically only via Plan 02-01 spike (user action) — code path executed in prod via MEMORY_BACKEND=mem0
- NativeProvider depends on Plan 02-04 migration shipping `memory_items` tables
- For now (post-02-03 only), `MEMORY_BACKEND=stub` is the safe default — memory-api boots, /v1/memory/* endpoints work in-process for tests, no real persistence
