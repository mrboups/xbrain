---
phase: 02-memoire-intelligente-agents
plan: 08
subsystem: agent-runtime+owui
tags: [second-opinion, parallel-llm, claude, grok, xai, asyncio-gather, read-only-agent]
requires:
  - phase: 02
    plan: 06
    provides: GRAPH_REGISTRY + agent-runtime HTTP API
provides:
  - apps/agent-runtime/app/graphs/second_opinion.py — read-only fan-out agent
    (Claude || Grok via asyncio.gather, naive diff heuristic, markdown formatter)
  - apps/openwebui-pipeline/app/pipelines/second_opinion_trigger.py — `/second-opinion <prompt>`
  - 5 tests (both succeed, claude-fails, both-fail, length-delta diff, no-divergence diff)
  - XAI_API_KEY env wiring (agent-runtime config + docker-compose)
affects:
  - Phase 3 will swap diff_node naive heuristic for Claude-as-judge structured diff

tech-stack:
  added: []  # anthropic + openai already in agent-runtime pyproject
  patterns:
    - "Read-only agent — NEVER touches memory_client, just LLM fan-out + format"
    - "asyncio.gather for parallel LLM calls — time-to-response = max(latency_a, latency_b)"
    - "Function-level seam (call_claude/call_grok) for testability — monkeypatch the function, not the SDK client"
    - "Per-provider try/except — single-API failures degrade gracefully with `_unavailable: <error>` notes"

key-files:
  created:
    - apps/agent-runtime/app/graphs/second_opinion.py (~140 lines)
    - apps/agent-runtime/tests/test_second_opinion.py (5 tests)
    - apps/openwebui-pipeline/app/pipelines/second_opinion_trigger.py (~80 lines)
  modified:
    - apps/agent-runtime/app/config.py (+ XAI_API_KEY)
    - apps/agent-runtime/app/graphs/__init__.py (+ second_opinion side-effect import)
    - apps/openwebui-pipeline/app/main.py (route slash through ingestion → second-opinion → promotions)
    - infrastructure/docker-compose.yml (+ XAI_API_KEY env on agent-runtime service)

key-decisions:
  - "No checkpointer interrupt. Second opinion is fire-and-forget — no HITL needed. The graph still uses the checkpointer (compile arg) for restart-safety, but no node has interrupt_before."
  - "xAI Grok consumed via OpenAI-compat API (AsyncOpenAI with base_url=https://api.x.ai/v1). Avoids a third SDK dependency."
  - "Diff heuristic is intentionally naive (length delta + disagreement-keyword counts). Phase 3 spec calls for Claude-as-judge — but until we have telemetry on what actually trips users up, naive is fine."
  - "DISAGREE_WORDS list is English-only. Acceptable for v1 — French agents in Phase 3 get a localized variant or LLM judge."
  - "Function-level test seam: tests monkeypatch `so.call_claude` and `so.call_grok` directly. SDK-client mocking would be brittle (the AsyncAnthropic constructor happens INSIDE call_claude), and the contract we care about is at the function boundary anyway."
  - "Read-only agent: NEVER calls memory_client. This is a comparison helper, not a knowledge-producer. If a user wants to save the comparison, they paste it back into a normal LibreChat conv where the bridge captures it as EPHEMERAL."

invariants-enforced:
  - "Second-opinion never writes to memory-api — pure read-only call surface"
  - "Single-API failure does NOT crash the graph (per-provider try/except returns (text, error) tuple)"
  - "asyncio.gather contract — both calls run concurrently, total wall time ≤ slower of the two"

requirements-completed:
  - CHAT-06   # parallel second-opinion across multiple LLMs

duration: ~20 min (inline)
completed: 2026-05-03
status: COMPLETE — code + 5 tests written. Tests use monkeypatch (no real API calls). Production needs ANTHROPIC_API_KEY + XAI_API_KEY in agent-runtime env.
---

# Plan 02-08 — Second-opinion agent (Claude ‖ Grok)

**3 nodes, no HITL, no memory writes. Read-only LLM-fanout helper.**

## What got built

1. **Graph** — `parallel → diff → format`. `parallel_call_node` dispatches Claude + Grok via `asyncio.gather`. `diff_node` runs naive heuristics (length delta + disagreement-keyword counts). `format_node` builds 3-section markdown.
2. **Trigger** — `/second-opinion <prompt>` slash command in OWUI Pipeline. Returns `final_markdown` straight to the user.
3. **Tests** — 5 tests: happy path (both succeed), Claude-fails-Grok-succeeds (note + remaining response), both-fail (twin diagnostics), length-delta diff trigger, no-divergence path.

## Demo flow (post-VM-deploy)

```
User in OpenWebUI: /second-opinion explain quantum entanglement in 3 sentences
Pipeline → POST /v1/agents/second-opinion/run with prompt
agent-runtime: asyncio.gather(call_claude, call_grok) → diff → format

User sees:
  ## Claude (claude-3-5-sonnet-latest)
  Quantum entanglement is a phenomenon...

  ## Grok (grok-2-latest)
  Entanglement happens when two particles...

  ## Diff highlights
  - No obvious surface-level divergences (read both for nuance)
```

## Why this plan was short

The 02-06 substrate (GRAPH_REGISTRY + checkpointer) made adding this agent trivial:
- 1 file in graphs/
- 1 line in graphs/__init__.py
- 1 file in OWUI pipelines/
- 1 line in pipelines dispatcher chain in main.py
- 1 env var (XAI_API_KEY) in config + compose

That's the proof-of-value of the abstraction.

## Pending for full production

1. `ANTHROPIC_API_KEY` + `XAI_API_KEY` in `.env` on the VM (Anthropic already there from 02-07, xAI needs to be added).
2. Phase 2 verify-work: actually run `/second-opinion` in OWUI on the VM and read both responses.

## What's NOT in this plan

- LLM-as-judge for diff_node — Phase 3
- Persisting the comparison to memory — out of scope (user pastes if they want it captured)
- Streaming the responses — also Phase 3 (current impl waits for both completions)
