---
quick_id: 260601-3is
status: done
commits:
  - cbb79cf  fix(mcp-gateway): resolve real sidecar tool name for single-tool sidecars
  - 451e41d  feat(memory-api): @claude mention pre-fetches URLs via scraper
  - f063521  fix(scripts): register-mcp-tools smoke test checks isError
---

# Quick Task 260601-3is — SUMMARY

## Files changed

- `apps/mcp-gateway/app/main.py` — added `list_tools()` resolution block inside `call_tool` between `initialize()` and `call_tool()`; resolves only when name is absent and sidecar has exactly 1 tool.
- `apps/memory-api/app/config.py` — added `MCP_GATEWAY_URL: str = "http://mcp-gateway:8080"`.
- `apps/memory-api/app/services/team_chat_agent.py` — added `import re`, helpers `_extract_urls`, `_fetch_url_via_scraper`, `_build_fetched_web_block`; injected `web_block` append in `_do_handle` after `chat_history_block` is built.
- `apps/memory-api/tests/test_extract_urls.py` — 8 unit tests for `_extract_urls` (pure function; self-contained).
- `infrastructure/scripts/register-mcp-tools.sh` — smoke-test body uses real tool name `"scrape"`; failure detection extended to `"isError":true` and `"Unknown tool"`; output says PASS/SKIP (not OK).

## Commit hashes

| # | Hash    | Message |
|---|---------|---------|
| 1 | cbb79cf | fix(mcp-gateway): resolve real sidecar tool name for single-tool sidecars (260601-3is) |
| 2 | 451e41d | feat(memory-api): @claude mention pre-fetches URLs via scraper (260601-3is) |
| 3 | f063521 | fix(scripts): register-mcp-tools smoke test checks isError (260601-3is) |

## Verification status

- `python -m py_compile` — PASS on all 3 changed `.py` files (mcp-gateway/main.py, memory-api/config.py, memory-api/team_chat_agent.py).
- `pytest apps/memory-api/tests/test_extract_urls.py` — 8/8 PASS (Python 3.13, no venv needed — pure regex function replicated in test file; no external deps required).
- Live integration (gateway POST scraper, @claude URL inject) — NOT tested locally (no Docker env); requires VM deploy by orchestrator.

## Deviations from PLAN

None. All three tasks implemented exactly as specified. The `_fetch_url_via_scraper` uses the real tool name `"scrape"` in the POST body as directed (works regardless of whether Task 1 gateway fix is live). The `_sign_bridge_jwt_acting("team-chat-agent")` reuse pattern is identical to the existing call in `_stream_via_promax`. The `web_block` is appended to the uncached `chat_history_block`, not to `cached_memory_block` — Anthropic prompt cache is unaffected.
