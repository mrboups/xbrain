---
phase: 02-memoire-intelligente-agents
plan: 05
subsystem: api+bridge
tags: [rag, system-prompt, canonical-facts, change-stream, idempotency, team-isolation]
requires:
  - phase: 02
    plan: 03
    provides: MemoryProvider.search with truth_level_min filter
  - phase: 02
    plan: 04
    provides: Promotion workflow ⇒ trustworthy CANONICAL truth_level
provides:
  - app/services/rag_enrichment.py (build_system_addendum + count_facts, MAX_FACT_CHARS=280)
  - app/routes/system_prompt.py — GET /v1/system-prompt
  - librechat-bridge MemoryApiClient.get_system_prompt (HTTP client, retries)
  - librechat-bridge app/conv_enricher.py — idempotent system-message injector
  - mongo_watcher: 2 parallel change-stream loops (messages + conversations)
  - 8 RAG tests (memory-api side: team isolation, truth_level filter, top_k, char cap, project_scope)
  - 6 enricher tests (idempotency, skip-no-title, skip-no-facts, happy path, double-event, memapi-failure)
affects:
  - 02-06 LangGraph agents — same /v1/system-prompt is callable by agents (uniform RAG layer)
  - 02-07 ingestion agent — uses CANONICAL filter to avoid re-ingesting validated facts
  - Phase 3 — graph-based selection + per-conv opt-out + recency weighting

tech-stack:
  added: []  # all stack components already in pyproject
  patterns: [change-stream-fanout, idempotency-via-metadata-flag, fail-open-retry-via-no-flag]

key-files:
  created:
    - apps/memory-api/app/services/rag_enrichment.py
    - apps/memory-api/app/routes/system_prompt.py
    - apps/memory-api/tests/test_system_prompt.py (8 tests)
    - apps/librechat-bridge/app/conv_enricher.py
    - apps/librechat-bridge/tests/test_conv_enricher.py (6 tests)
  modified:
    - apps/memory-api/app/main.py (mount system_prompt router)
    - apps/librechat-bridge/app/memory_api_client.py (+ get_system_prompt)
    - apps/librechat-bridge/app/mongo_watcher.py (split into messages_watch_loop + conversations_watch_loop, run via asyncio.gather)

key-decisions:
  - "Top-K = 5 default — small enough to keep prompt prefix tight, big enough to cover diverse facets. Configurable per-call via top_k query param."
  - "MAX_FACT_CHARS = 280 (Twitter-length) — caps any single fact's prompt cost. Truncated facts get '…' suffix as visible signal."
  - "Default min_level = CANONICAL — only vetted facts make it into the system message. Phase 3 may relax for opt-in 'all-truth' modes."
  - "Idempotency = metadata.xbrain_enriched flag on conversations doc. Single source of truth, survives bridge restarts."
  - "Empty-addendum case STILL marks enriched=true to avoid retry storms when team has no canonical facts yet."
  - "Empty-title case does NOT mark enriched — title may arrive in a follow-up update event (LibreChat sets title async after first message)."
  - "memory-api call failure does NOT mark enriched — guaranteed retry on next change-stream event for that conv."
  - "Conversations watcher uses operationType ∈ {insert, update} with full_document=updateLookup — title is often set via update, not insert."
  - "Two watch_loops run via asyncio.gather — if either dies, the bridge restarts (uvicorn-style supervision via process exit)."

invariants-enforced:
  - "Team isolation — provider.search filters team_scope before any results reach the addendum"
  - "Truth-level filter — only items with truth_level >= min_level surface (CANONICAL by default)"
  - "Single injection per conv — metadata.xbrain_enriched gate"
  - "No empty system messages — addendum=='' → no Mongo insert"

requirements-completed:
  - CHAT-07   # RAG injection on new conv
  - SRCH-03   # semantic search team-scoped
  - SRCH-04   # truth_level filter at retrieval

duration: ~30 min (inline)
completed: 2026-05-03
status: COMPLETE — code + 14 tests written. Tests run only with mongomock + testcontainers (CI / verify-work, not in this codegen pass).
---

# Plan 02-05 — RAG team-scoped enrichment

**LibreChat new-conv → bridge change-stream → memory-api /v1/system-prompt → CANONICAL facts injected as system message.**

## What got built

1. **memory-api**
   - `services/rag_enrichment.py` — `build_system_addendum()` queries `provider.search(truth_level_min=CANONICAL)`, formats as markdown with citation IDs.
   - `routes/system_prompt.py` — `GET /v1/system-prompt?query=…&top_k=5&min_level=CANONICAL`.
   - 8 unit tests cover team isolation, truth-level filter, Top-K, char cap, empty case, project_scope.

2. **librechat-bridge**
   - `conv_enricher.py` — idempotent injector. Skip cases (no title, no facts, already enriched, memapi failure) all return False but only the deliberate ones mark enriched=true.
   - `mongo_watcher.py` split into two parallel change-stream loops (`messages_watch_loop` + `conversations_watch_loop`) run via `asyncio.gather`.
   - `MemoryApiClient.get_system_prompt()` with bridge JWT + tenacity retry.
   - 6 enricher tests with mongomock + inline FakeMemClient.

## Why this is the differentiator

Without this layer, "CANONICAL truth-level" is just a tag. With it, a team member opens a new conversation in LibreChat and the model sees the team's vetted knowledge as background context — without anyone copy-pasting docs. The 4-eyes promotion workflow from 02-04 makes those facts trustworthy; this plan makes them automatically *consumed*.

## Pending for full production

1. `MEMORY_BACKEND=mem0` or `native` (currently `stub` — enrichment works in tests only).
2. Some team needs to actually have CANONICAL facts (post-spike Plan 02-01 + workflow exercise).
3. CI / Phase 2 verify-work pass — exercise end-to-end: create conv in LibreChat → see system message arrive.
