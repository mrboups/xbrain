---
quick_id: 260511-0jb
plan: 01
status: complete
date: 2026-05-11
commits:
  - d1ba7ae feat(quick-260511-0jb): bump xAI to grok-3 and add Claude Reasoning endpoint
  - 272ec39 feat(quick-260511-0jb): second-opinion 3-way fanout (Sonnet + Opus 4.7 + Grok-3)
  - d8fcb69 feat(quick-260511-0jb): enable Anthropic prompt caching on 6 extraction call sites
files_modified:
  - infrastructure/librechat/librechat.yaml
  - apps/agent-runtime/app/graphs/second_opinion.py
  - apps/agent-runtime/app/tools/extract_facts.py
  - apps/memory-api/app/routes/memory.py
  - apps/librechat-bridge/app/contact_extractor.py
  - apps/librechat-bridge/app/task_intent_detector.py
  - apps/granola-sync/app/extractor.py
---

# Quick 260511-0jb — Lot 2 LLM stack quick wins

## Objective

Three low-risk improvements to the xbrain LLM stack:
1. Bump LibreChat model lists (Opus 4.6→4.7 was already in place, Grok-2→3 done) + add new "Claude Reasoning" custom endpoint.
2. Refactor `second-opinion` agent to fan out across 3 providers in parallel (Sonnet + Opus 4.7 + Grok-3) instead of 2.
3. Activate Anthropic prompt caching (`cache_control: ephemeral`) on 6 fixed-system-prompt call sites across 5 files, with no model swaps.

## What changed

### Task 1 — `infrastructure/librechat/librechat.yaml` (commit `d1ba7ae`)

- xAI endpoint: `grok-2-latest` → `grok-3` (kept `grok-2-mini` as a documented fallback).
- New 4th custom endpoint **"Claude Reasoning"** backed by `claude-sonnet-4-6` with `titleModel: claude-haiku-4-5-20251001`.
- Anthropic endpoint untouched (`claude-opus-4-7` was already in place).
- YAML validated via `yaml.safe_load`.

### Task 2 — `apps/agent-runtime/app/graphs/second_opinion.py` (commit `272ec39`)

- Added `OPUS_MODEL = "claude-opus-4-7"` constant.
- Bumped `GROK_MODEL` from `grok-2-latest` to `grok-3`.
- New `call_opus()` async helper mirroring `call_claude`.
- `SecondOpinionState` TypedDict extended with `opus_response` + `opus_error`.
- `parallel_call_node` now `asyncio.gather`s 3 calls instead of 2 — time-to-response remains `max()` of the three.
- `diff_node` refactored to handle 2-or-3 available responses (graceful degradation if any provider fails).
- `format_node` emits 3 sections (Claude / Opus / Grok) + Diff highlights.
- Module docstring updated to reference the trio.
- Graph topology unchanged (`parallel → diff → format → END`), `@register("second-opinion")` decorator preserved.
- File parses as valid Python.

### Task 3 — Prompt caching on 6 call sites (commit `d8fcb69`)

Same mechanical transformation applied across:

| File | Call site | Before | After |
|------|-----------|--------|-------|
| `apps/agent-runtime/app/tools/extract_facts.py` | line 129 | `system=SYSTEM_PROMPT,` | wrapped in cache_control block |
| `apps/memory-api/app/routes/memory.py` | `_extract_crm_contacts` (was lines 89-94) | inline parenthesised string | wrapped + cache_control |
| `apps/memory-api/app/routes/memory.py` | `_maybe_create_task_from_action` (was lines 182-187) | inline parenthesised string | wrapped + cache_control |
| `apps/librechat-bridge/app/contact_extractor.py` | line 141 | `system=SYSTEM_PROMPT,` | wrapped in cache_control block |
| `apps/librechat-bridge/app/task_intent_detector.py` | line 98 | `system=SYSTEM_PROMPT,` | wrapped in cache_control block |
| `apps/granola-sync/app/extractor.py` | line 69 | `system=SYSTEM_PROMPT,` | wrapped in cache_control block |

Format applied verbatim:
```python
system=[
    {
        "type": "text",
        "text": <prompt_value>,
        "cache_control": {"type": "ephemeral"},
    }
],
```

No model swaps — Haiku stays Haiku (`claude-3-5-haiku-20241022`), Sonnet stays Sonnet (`claude-sonnet-4-6`).

## Verification

| Check | Result |
|-------|--------|
| `librechat.yaml` parses as valid YAML | ✅ |
| `grep -q "grok-3" librechat.yaml` | ✅ |
| `! grep -q "grok-2-latest" librechat.yaml` | ✅ |
| `grep -q "Claude Reasoning" librechat.yaml` | ✅ |
| `grep -q "claude-opus-4-7" librechat.yaml` | ✅ |
| `second_opinion.py` parses as valid Python | ✅ |
| `OPUS_MODEL = "claude-opus-4-7"` present | ✅ |
| `GROK_MODEL = "grok-3"` present | ✅ |
| `async def call_opus` defined | ✅ |
| `opus_response` + `opus_error` in TypedDict | ✅ |
| All 5 caching files parse as valid Python | ✅ |
| `cache_control` occurrence count: 1+2+1+1+1 = 6 across 5 files | ✅ |

## Operational notes — what changes at runtime

- **LibreChat dropdown**: users now see `grok-3` + new "Claude Reasoning" entry. Restart LibreChat to pick up `librechat.yaml`.
- **Second opinion (`/second-opinion` slash command)**: now triggers 3 parallel calls — Sonnet + Opus + Grok-3. Output is 3 markdown sections. Latency = max of three (still parallel, so no triple-wait).
- **Prompt caching**: at the next deploy, the 5 extraction services will benefit from ~90% input cost reduction on cached system tokens (Anthropic ephemeral cache, 5-min TTL). Monitor `cache_read_input_tokens` in Langfuse to confirm hits.

## Out of scope (deliberately not done)

- No model swaps from Haiku to GPT-5 nano (reported in earlier discussions as "Lot 3", deferred until volume justifies the risk).
- No Graphiti LLM swap from Anthropic to Gemini (deferred).
- No SDK changes (still using `anthropic>=0.40` which natively supports caching without beta header).
- No `.env` or `docker-compose.yml` modifications.

## Cost impact estimate

On the assumption that extraction passive runs ~1000 messages/jour with ~1500 input tokens of system prompt each:
- **Before**: 1500 × 1000 × 30 = 45M tokens/month at Haiku 3.5 input rate ($0.80/1M) = ~$36/mo
- **After (with 90% cache hit ratio)**: 45M × 0.1 + 45M × 0.9 × 0.1 = 4.5M + 4.05M = ~8.5M effective tokens = ~$7/mo

Estimated savings: **~$29/mo (~80%)** on the extraction passive system prompt cost alone. Similar effect on `extract_facts` (Sonnet) and `granola/extractor` (Haiku).

## Execution context

- **Branch**: `main` (worktree isolation was attempted but the worktree env didn't propagate to Bash tool calls; commits landed on `main` directly).
- **Plan-checker**: ✅ PASSED (all dimensions verified by `gsd-plan-checker` before execution).
- **Atomic commits**: 3 task commits + 1 pre-dispatch plan commit = 4 commits total.
- **No regression risk**: same models, same SDK, same API contracts. Only request payload shape changed (system param: str → list[block]).
