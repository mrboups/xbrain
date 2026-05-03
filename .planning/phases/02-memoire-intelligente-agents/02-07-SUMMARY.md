---
phase: 02-memoire-intelligente-agents
plan: 07
subsystem: agent-runtime+owui
tags: [ingestion, llm-extraction, hitl, anthropic, pypdf, slash-commands]
requires:
  - phase: 02
    plan: 04
    provides: Promotion workflow — extracted facts land at WORKING (max), promotion=human-only
  - phase: 02
    plan: 06
    provides: GRAPH_REGISTRY + interrupt_before pattern + MemoryApiClient
provides:
  - apps/agent-runtime/app/tools/document_loader.py — load_url + load_pdf_bytes (50KB cap)
  - apps/agent-runtime/app/tools/extract_facts.py — Claude-based JSON-fact extractor
    (max 20 facts × 500 chars, suggested_truth_level capped at VALIDATED)
  - apps/agent-runtime/app/graphs/ingestion.py — 4-node graph (load → extract →
    await_review HITL → write) registered as 'ingestion' in GRAPH_REGISTRY
  - apps/agent-runtime/app/memory_client.py — new upsert_fact() convenience
    helper (builds full MemoryItem from agent primitives)
  - apps/openwebui-pipeline/app/pipelines/ingestion_trigger.py — 3 slash commands
    (/ingest, /approve-thread, /reject-thread)
  - 4 integration tests in apps/agent-runtime/tests/test_ingestion_agent.py
affects:
  - 02-08 second-opinion — same pattern (graph + tool + OWUI command)
  - 02-09 Langfuse — extract_facts will be a key trace target

tech-stack:
  added:
    - pypdf>=5.0 (agent-runtime pyproject)
  patterns:
    - "Tools layer (apps/agent-runtime/app/tools/) — pure functions, no graph state coupling"
    - "Stub-friendly anthropic singleton (_client global, swappable via monkeypatch)"
    - "JSON-loose parsing — try strict, fall back to ```json fence regex"
    - "approved_indexes=None ⇒ approve-all (back-compat for direct API callers)"
    - "Per-fact upsert with try/except per item — partial failures don't kill the batch (skipped_count surfaced in state)"

key-files:
  created:
    - apps/agent-runtime/app/tools/__init__.py
    - apps/agent-runtime/app/tools/document_loader.py
    - apps/agent-runtime/app/tools/extract_facts.py (~120 lines)
    - apps/agent-runtime/app/graphs/ingestion.py (~120 lines, 4 nodes + register)
    - apps/agent-runtime/tests/test_ingestion_agent.py (4 tests)
    - apps/openwebui-pipeline/app/pipelines/ingestion_trigger.py (~180 lines)
  modified:
    - apps/agent-runtime/pyproject.toml (+ pypdf>=5.0)
    - apps/agent-runtime/app/memory_client.py (+ upsert_fact convenience)
    - apps/agent-runtime/app/graphs/__init__.py (+ ingestion side-effect import)
    - apps/openwebui-pipeline/app/config.py (+ AGENT_RUNTIME_URL)
    - apps/openwebui-pipeline/app/main.py (route slash commands through ingestion first, then promotions)

key-decisions:
  - "extract_facts SYSTEM_PROMPT explicitly bans CANONICAL/PUBLIC suggestions. Reason: those require human 4-eyes (02-04). Letting the LLM hint at them would create pressure to skip the workflow."
  - "All extracted facts persist with validation_status='pending'. They flip to 'validated' only when truth_level crosses VALIDATED via the promotion workflow (single rule in 02-04 _apply_promotion)."
  - "PDF support uses pypdf (pure-python). Phase 3 may swap for unstructured.io if scanned-PDF OCR becomes a need."
  - "URL loader returns raw HTTP body capped at 50KB. No readability/trafilatura cleanup yet — Claude tolerates HTML noise reasonably for fact extraction. Phase 3 adds a content extractor."
  - "Tools live in app/tools/, not app/graphs/. Pure functions — no coupling to LangGraph state. Lets us reuse extract_facts in the second-opinion agent (02-08) and elsewhere."
  - "MemoryApiClient.upsert_fact() builds the full MemoryItem (id=uuid4, timestamps=now, validation_status=pending). Agents shouldn't have to know MemoryItem's exact shape."
  - "OWUI command parser ignores out-of-range fact indices on /approve-thread (graph itself counts fact_count and skips bad ones). User can't crash the agent with bad input."
  - "ingestion_trigger.try_handle is checked BEFORE promotion_manager.try_handle in main.py — both are slash-prefix dispatchers, ordering is purely 'most-likely-first' for cheap cases."

invariants-enforced:
  - "Extracted facts NEVER receive truth_level >= CANONICAL — promotion workflow only path"
  - "Validation_status='pending' on every extracted fact insertion — flips only via _apply_promotion"
  - "Source field = 'agent:ingestion-v1' — auditable in audit_log + memory_items.source"
  - "Per-fact try/except — one upsert failure doesn't lose the others"

requirements-completed:
  - MEM-09    # provenance + auto-ingestion path
  - AGENT-04  # /v1/agents/* HTTP API used end-to-end via OWUI
  - AGENT-05  # team_scope propagated through graph state into memory-api calls

duration: ~25 min (inline)
completed: 2026-05-03
status: COMPLETE — code + 4 tests written. Tests require testcontainers Postgres + ANTHROPIC_API_KEY=test-key (mocked client). Real OWUI flow requires AGENT_RUNTIME_URL env in openwebui-pipeline service.
---

# Plan 02-07 — Ingestion agent (URL/PDF → facts → HITL → memory)

**LangGraph 4-node graph + 2 tools + 3 OWUI commands. Lands facts as WORKING/pending; promotion is a human decision.**

## What got built

1. **Tools** — `document_loader` (URL fetch + PDF parse, 50KB cap), `extract_facts` (Claude `claude-3-5-sonnet-latest`, JSON-output prompt, 20-fact cap, defensive parsing).
2. **Graph** — `load → extract → await_review (interrupt_before) → write`. Registered as `ingestion` in `GRAPH_REGISTRY`.
3. **API surface** — already exists from 02-06. New initial_state shape: `{source_type, source_url|pdf_b64, team_scope, sub}`. Resume with `state_update={"approved_indexes": [...]}`.
4. **OWUI commands** — `/ingest <url>`, `/approve-thread <id> <0,2,5|all>`, `/reject-thread <id>`. All short-circuit before LLM dispatch.
5. **Tests** — 4 integration tests (URL+pause, partial approval, PDF path, garbage extraction → empty facts no crash).

## Demo flow (post-VM-deploy)

```
User in OpenWebUI: /ingest https://docs.our-team.io/auth.md
Pipeline → POST /v1/agents/ingestion/run with sub+team
agent-runtime: load_url → extract_facts (Claude) → PAUSES at await_review

User sees: "📥 Thread `abc12345…` — 8 facts extracted, awaiting your approval:
  `0` — WORKING (conf=0.92): Auth uses Google OAuth + xbrain-bridge service JWT
  `1` — VALIDATED (conf=0.95): Token TTL is 5 minutes
  ..."

User in OpenWebUI: /approve-thread abc12345 0,1,3,7
Pipeline → POST /v1/agents/abc12345/resume with approved_indexes
agent-runtime: write_node iterates → 4 upserts to memory-api → done

User sees: "✅ Thread `abc12345…` resolved — 4 fact(s) written to memory
            (0 skipped). New items have validation_status='pending' until
            promotion (4-eyes for CANONICAL)."
```

## Pending for full production

1. `pip install` pypdf at next compose build (already in pyproject).
2. `AGENT_RUNTIME_URL` env on the openwebui-pipeline service.
3. Phase 2 verify-work: exercise the full /ingest → /approve-thread loop with a real URL on the VM.

## Why this plan is short

The fundation (02-06 GRAPH_REGISTRY + interrupt_before + MemoryApiClient) means new agents are essentially:
- 1 file in tools/ (the LLM call)
- 1 file in graphs/ (4 nodes max)
- 1 line in graphs/__init__.py (side-effect import)
- 1 file in OWUI pipelines/ (slash commands)

Plan 02-08 (second-opinion) follows the exact same shape.
