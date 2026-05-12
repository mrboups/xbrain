---
phase: 9
plan: 02
plan_id: 09-02
status: complete
subsystem: chrome-extension
tags: [chrome-extension, claude-ai, sse, translator, mv3]
requires:
  - 09-RESEARCH.md §Pattern 4 (claude.ai client shape)
  - chrome.storage.session (Phase 5 pattern, untouched)
provides:
  - chrome-extension/translate_sse.js (pure SSE + body translator)
  - chrome-extension/claude_ai_client.js (credentialed fetch + streaming dispatch)
  - 09-CAPTURE.md (assumed claude.ai contract with A1-A10 status)
affects:
  - plan 09-03 (will import these modules into background.js)
  - plan 09-06 (UAT will flip A1-A10 PENDING → CONFIRMED/DIVERGED)
tech-stack:
  added:
    - Vanilla ES modules with globalThis fallback for MV3 importScripts
  patterns:
    - SSE buffer split on \n\n
    - Dual-branch SSE translator (legacy completion + Messages-style content_block_delta)
    - Single point of fragility via CLAUDE_AI_API_VERSION constant
key-files:
  created:
    - chrome-extension/translate_sse.js
    - chrome-extension/claude_ai_client.js
    - chrome-extension/tests/test_translate_sse.mjs
    - chrome-extension/tests/test_openai_to_claudeai.mjs
    - chrome-extension/tests/run_tests.mjs
    - .planning/phases/09-session-bridge-pro-max-routing-via-chrome-extension/09-CAPTURE.md
  modified: []
decisions:
  - CLAUDE_AI_API_VERSION = "2026-05-capture" (bump on every observed format change)
  - Translator handles BOTH legacy and Messages-style SSE so a one-sided server-side migration does not break us
  - sendFrame is dependency-injected (no chrome.* in claude_ai_client.js) so it stays node-testable
  - Body shape locked to 11 keys per RESEARCH.md §Pattern 4; extra fields require new capture + version bump
commits:
  - d9fe2db docs(phase-9-02): seed 09-CAPTURE.md with assumed claude.ai contract (A1-A10 PENDING)
  - cb51d58 feat(phase-9-02): translate_sse.js + node tests for claude.ai SSE translator
  - bc9d1a3 feat(phase-9-02): claude_ai_client.js — credentialed claude.ai fetch + SSE relay
files_modified:
  - chrome-extension/translate_sse.js (created)
  - chrome-extension/claude_ai_client.js (created)
  - chrome-extension/tests/test_translate_sse.mjs (created)
  - chrome-extension/tests/test_openai_to_claudeai.mjs (created)
  - chrome-extension/tests/run_tests.mjs (created)
  - .planning/phases/09-session-bridge-pro-max-routing-via-chrome-extension/09-CAPTURE.md (created)
metrics:
  duration_minutes: 4
  completed: 2026-05-12
  task_count: 4
  file_count: 6
  test_assertions: 12
---

# Phase 9 Plan 02: claude.ai Client + SSE Translator — Summary

**One-liner:** Ships the extension-side claude.ai bridge: credentialed `getOrgId`/`createConversation`/`handleClaude` plus a dual-branch SSE translator (legacy `event: completion` + Messages-style `content_block_delta`), pinned by `CLAUDE_AI_API_VERSION` = `2026-05-capture` and covered by 12 node assertions.

## What was built

Three production files plus three test files plus the assumed-contract capture document:

1. **`chrome-extension/translate_sse.js`** — pure ES module, no DOM, no chrome.*.
   - `CLAUDE_AI_API_VERSION` constant (`2026-05-capture`) — single string to bump on every observed claude.ai format change.
   - `parseSSE(block)` — handles SSE comments (`:keepalive`), `event:` and `data:` lines, JSON-parses payloads with raw-string fallback.
   - `translateClaudeAiSSE(evt, id, model, created)` — translates parsed event into an OpenAI ChatCompletion streaming chunk. Handles BOTH:
     - Legacy: `{"completion": "...", "stop_reason": null|"end_turn"}`
     - Messages-style: `{"type":"content_block_delta", "delta":{"text":"..."}}`, `{"type":"message_stop"}`, `{"type":"message_delta","delta":{"stop_reason":"..."}}`
   - `openaiToClaudeAi(body, conv, parent)` — collapses OpenAI messages into the claude.ai web prompt format with `[System]`/`[Human]`/`[Assistant]` labels; produces the 11-key locked body shape.
   - `mapModel(openaiModel)` — `claude-opus-4-7` and `claude-sonnet-4-6` pass through; everything else falls back to sonnet.
   - `globalThis.xbrainTranslateSSE` namespace fallback for MV3 SW environments that load via `importScripts`.

2. **`chrome-extension/claude_ai_client.js`** — credentialed claude.ai client.
   - `getOrgId()` — `GET https://claude.ai/api/organizations`, returns `orgs[0].uuid` (A8 fallback to `.id`).
   - `createConversation(orgId)` — `POST .../chat_conversations` with a fresh client UUID.
   - `handleClaude(msg, sendFrame)` — drives the full request_id-tagged streaming flow: getOrgId → createConversation → `openaiToClaudeAi` → POST `.../completion` with `credentials: 'include'` + `anthropic-client-platform: web_claude_ai`, reads `r.body.getReader()`, splits on `\n\n`, parses + translates + relays each chunk via injected `sendFrame`. Error bodies are capped at 500 chars per threat T-09-02-01.
   - `sendFrame` is dependency-injected (no `chrome.*` in this module) — keeps it node-testable.
   - `globalThis.xbrainClaudeAiClient` namespace fallback.

3. **`chrome-extension/tests/`** — vanilla `node:assert/strict` tests, no jest.
   - `test_translate_sse.mjs` — 8 assertions (legacy, Messages-style, message_stop, comments, empty events, stop_reason via legacy or message_delta, CLAUDE_AI_API_VERSION exported).
   - `test_openai_to_claudeai.mjs` — 4 assertions (full canonical shape with 11 keys, mapModel, explicit parent UUID, multi-turn labelling).
   - `run_tests.mjs` — walks `test_*.mjs`, runs each in a fresh node process, exits non-zero on any failure.

4. **`09-CAPTURE.md`** — assumed-contract document with the 10 assumption rows (A1–A10) all marked PENDING/CONFIRMED/N/A. `## Decisions for plan 09-02` section locks endpoint URLs, header set, body keys, SSE branches, and `CLAUDE_AI_API_VERSION` value. `## Divergence Patches` table is empty at ship time — gets populated when the user runs the live capture during UAT.

## Assumptions baked in (A1-A10 from 09-RESEARCH)

All A1-A10 from RESEARCH.md were treated as **PENDING** at code-time and baked into the implementation verbatim:

| # | Baked-in value | Risk if DIVERGED |
|---|----------------|------------------|
| A1 | `POST https://api.claude.ai/api/organizations/{org_id}/chat_conversations/{conv_uuid}/completion` | 404 → one-URL-template fix in `claude_ai_client.js` |
| A2 | Header `anthropic-client-platform: web_claude_ai` | 401/403 → header object fix + paste live `anthropic-client-version` |
| A3 | Translator handles both legacy AND Messages-style — already future-proof | Only fails if a THIRD format ships |
| A4 | N/A — verified in plan 09-05 (LibreChat side) | — |
| A5 | N/A — out of scope | — |
| A6 | Rolling string `2026-05-capture` is enough | None — convention-level decision |
| A7 | claude.ai cookies SameSite=Lax assumed | Strict would invalidate the whole bridge architecture; surface as kill switch |
| A8 | `orgs[0].uuid` with `.id` fallback | Already covered by fallback |
| A9 | N/A — operational TLS-fingerprint risk | Monitored via session-bridge logs |
| A10 | First message uses nil UUID `00000000-0000-4000-8000-000000000000` | `createConversation` already returns conv data — 3-line patch to extract root_message_uuid if needed |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 — Blocking] Task 1 was `checkpoint:human-action` but user pre-authorised execution without blocking**
- **Found during:** plan start
- **Issue:** Plan task 1 requires a live DevTools capture by the user. User's spawning prompt explicitly said "DO NOT BLOCK — code using assumptions A1-A10 from RESEARCH.md verbatim. Mark divergence risks in 09-CAPTURE.md as RISK lines."
- **Fix:** Seeded 09-CAPTURE.md with the EXPECTED claude.ai contract derived from RESEARCH.md §Pattern 4. All A1-A10 marked PENDING (not CONFIRMED) with explicit RISK lines for each. User UAT (09-06) flips PENDING → CONFIRMED/DIVERGED.
- **Files modified:** `.planning/phases/09-session-bridge-pro-max-routing-via-chrome-extension/09-CAPTURE.md`
- **Commit:** d9fe2db

**2. [Rule 2 — Missing critical functionality] Trailing SSE block flush**
- **Found during:** Task 4 review
- **Issue:** Plan's Pattern 4 sketch only flushes complete `\n\n`-terminated blocks. If the response ends WITHOUT a trailing `\n\n`, the last event would be silently dropped — caused observed bugs in `claude-code-router` per RESEARCH.md Pitfall 3.
- **Fix:** After the read loop ends, `handleClaude` now also parses any residual `buffer.length > 0` content before emitting `type: "end"`.
- **Files modified:** `chrome-extension/claude_ai_client.js`
- **Commit:** bc9d1a3

No Rule 1 bugs, no Rule 4 architectural questions.

## Verification

- `node chrome-extension/tests/run_tests.mjs` → exit 0, 2/2 test files passed, 12 assertions PASS.
- `node --check chrome-extension/claude_ai_client.js` → exit 0.
- Plan Task 2 automated grep check: `09-CAPTURE.md` has 10 assumption rows + CLAUDE_AI_API_VERSION + `## Decisions for plan 09-02` section → OK.
- Plan Task 4 automated grep check: `credentials: 'include'` + `CLAUDE_AI_API_VERSION` + `globalThis.xbrainClaudeAiClient` + `09-CAPTURE.md` reference all present → OK.

All success criteria from the plan met:
- [x] 7+ node assertions pass (12 delivered)
- [x] `CLAUDE_AI_API_VERSION` matches 09-CAPTURE.md decision
- [x] All 10 assumptions resolved (PENDING with explicit RISK is the accepted resolution per user's "do not block" instruction)

## Self-Check: PASSED

- FOUND: chrome-extension/translate_sse.js
- FOUND: chrome-extension/claude_ai_client.js
- FOUND: chrome-extension/tests/test_translate_sse.mjs
- FOUND: chrome-extension/tests/test_openai_to_claudeai.mjs
- FOUND: chrome-extension/tests/run_tests.mjs
- FOUND: .planning/phases/09-session-bridge-pro-max-routing-via-chrome-extension/09-CAPTURE.md
- FOUND: d9fe2db (docs commit)
- FOUND: cb51d58 (translate_sse + tests commit)
- FOUND: bc9d1a3 (claude_ai_client commit)

## USER ACTION REQUIRED

To flip `09-CAPTURE.md`'s 6 PENDING assumption rows (A1, A2, A3, A7, A8, A10) to CONFIRMED or DIVERGED, perform the following live DevTools capture in your own browser. This is the only true human-action step in Phase 9 and is normally walked during 09-06 UAT — you can do it any time before then.

### Capture procedure

1. **Open Chrome → `https://claude.ai`** → sign in (Pro/Max preferred, free OK for format capture).
2. **Open DevTools → Network tab** → tick "Preserve log" + filter "Fetch/XHR".
3. **Start a new chat.** Send the message: `test capture for xbrain phase 9`.
4. **Capture the completion request:**
   - In Network tab find the request to `api.claude.ai/api/organizations/.../completion`.
   - Right-click → `Copy → Copy as cURL (bash)`.
   - Paste under `## Raw curl` in `09-CAPTURE.md`. Replace any `Cookie:`, `anthropic-anonymous-id`, `cf-*`, or other token values with `<REDACTED>`.
5. **Capture the SSE response:**
   - Click the same request → Response tab → select all → copy.
   - Paste the first ≥ 10 SSE events under `## Raw SSE response`.
6. **Capture organizations response:**
   - Find `GET claude.ai/api/organizations` in Network tab.
   - Click → Response tab → copy the JSON array.
   - Paste under `## Organizations response`. Note whether the first item has `uuid` or `id` (or both).
7. **Verify cookie SameSite policy (A7):**
   - Visit `chrome://settings/cookies/detail?site=claude.ai`.
   - Note the SameSite value on the main session cookie (look for `sessionKey`, `__Secure-next-auth.session-token`, `lastActiveOrg`).
   - **If SameSite = Strict, STOP and flag** — the bridge architecture cannot work; surface to architecture review.
8. **Fill in the `## Assumption verification table`** in 09-CAPTURE.md — for each of A1, A2, A3, A7, A8, A10, change `PENDING` → `CONFIRMED` or `DIVERGED`.
9. **For any DIVERGED:**
   - Add a row to `## Divergence Patches` listing what changed.
   - Patch `chrome-extension/claude_ai_client.js` and/or `chrome-extension/translate_sse.js`.
   - Bump `CLAUDE_AI_API_VERSION` in `translate_sse.js` (format `YYYY-MM-capture`).
   - Re-run `node chrome-extension/tests/run_tests.mjs` to make sure translator still passes.
10. **Commit** the updated capture + any patches as `docs(phase-9-02): UAT capture A1-A10` (no plan re-run needed).

### Critical items to capture verbatim

| Item | Where to paste | Why |
|------|----------------|-----|
| Full curl with all headers | `## Raw curl` | Reveals required `anthropic-client-platform`, possibly `anthropic-client-version`, `anthropic-anonymous-id`, etc. |
| Exact `event:` names | `## Raw SSE response` | A3 — confirms whether legacy, Messages-style, or both shipped |
| Body's `parent_message_uuid` value | inline check | A10 — confirms nil UUID acceptable for first message |
| `orgs[0]` field name (`uuid` vs `id`) | `## Organizations response` | A8 — confirms fallback isn't actually needed (or which path is canonical) |
| Cookie SameSite | A7 row + screenshot | A7 — Strict invalidates the whole phase |
