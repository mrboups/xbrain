---
phase: 23-catch-me-up
plan: 04
subsystem: ui
tags: [chrome-extension, popup, centrifugo, catch-me-up, read-cursor, shadcn, xss-safe]

# Dependency graph
requires:
  - phase: 23-catch-me-up (23-02)
    provides: "mark-read / unread-summary / catch-me-up endpoints + catchup_stream_* frame shapes"
  - phase: 20-extension-chat-restyle
    provides: "shadcn Neutral token system + the popup selector-contract test"
  - phase: 22-nudge-open
    provides: "user:<sub> channel + handleUserPublication router + wireSendLink POST/error idiom"
provides:
  - "Extension client for Catch me up: mark-read on focus/scroll, threshold-gated opt-in banner, ephemeral streamed summary render"
  - "A durable switchTeam ordering gate in the popup contract test (refreshUnreadBanner before markRead)"
affects: [catch-me-up UAT, future extension chat features, popup contract]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Stale-cursor capture: read the unread banner against the pre-visit cursor BEFORE advancing it with mark-read"
    - "Server-authoritative threshold: banner shows only when count >= threshold, both from the response (mirrors refreshAgentAliases)"
    - "Ephemeral stream render into a dedicated text node via textContent (XSS-safe), never into #message-list, never persisted"

key-files:
  created: []
  modified:
    - chrome-extension/popup.html
    - chrome-extension/popup.css
    - chrome-extension/popup.js
    - chrome-extension/tests/test_popup_contract.mjs

key-decisions:
  - "Render the streamed summary into a dedicated #catchup-summary-text node inside #catchup-summary so the close button is never wiped by textContent"
  - "Route catchup_stream_error too (not just start/chunk/end) so a stream failure surfaces in the panel instead of hanging on the placeholder"
  - "Encode the ORDERING BLOCKER as a real contract-test gate (comment-stripped) that goes RED if refreshUnreadBanner/markRead are swapped"

patterns-established:
  - "Contract-test ordering assertion: extract a function body via brace-matching, strip comments, assert call index order"

requirements-completed: [CATCHUP-01]

# Metrics
duration: 13min
completed: 2026-07-19
---

# Phase 23 Plan 04: Catch Me Up — Extension Client Summary

**Chrome-extension popup now marks the thread read on focus/scroll, shows a dismissible shadcn-Neutral "Catch me up" banner only at/above the server threshold, and renders the opt-in ephemeral streamed summary via textContent on the caller's own Centrifugo channel — never auto-running the paid summary.**

## Performance

- **Duration:** ~13 min
- **Started:** 2026-07-19T13:12:00Z
- **Completed:** 2026-07-19T13:25:03Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- Dismissible, threshold-gated catch-me-up banner + ephemeral summary panel at the top of `#chat-body`, styled with existing shadcn Neutral tokens (radius 0, `--ring` focus, English-only).
- `markRead()` POSTs `/mark-read` (fail-soft, in-flight dedupe) and fires on scroll-to-bottom, window focus, and once after the banner is captured on team open.
- `refreshUnreadBanner()` GETs `/unread-summary` and shows the banner only when `count >= threshold` (both server-provided); a dismissed `since` window suppresses re-nag.
- The run button is the ONLY caller of `POST /catch-me-up` (never auto-run); 202 reveals the panel, 200 hides quietly, 429 surfaces an English rate-limit status.
- `handleUserPublication` routes `catchup_stream_start/chunk/end/error` into `#catchup-summary` via `textContent` (XSS-safe), preserving the `open_url` branch; nothing is inserted into `#message-list` or persisted.
- Contract test extended: six new frozen ids + a durable switchTeam ordering gate; full suite stays green (12/12 files, 143/143 in the popup contract).

## Task Commits

Each task was committed atomically:

1. **Task 1: Banner markup + shadcn-Neutral styling** - `97371fc` (feat)
2. **Task 2: Wire mark-read + threshold banner + ephemeral render + extend contract test** - `d09dcdf` (feat)

## Files Created/Modified
- `chrome-extension/popup.html` - Added hidden `#catchup-banner` (text + run + dismiss) and `#catchup-summary` (head + close + `#catchup-summary-text`) at the top of `#chat-body`, above `#chat-scroll`.
- `chrome-extension/popup.css` - `.xb-catchup-banner` / `.xb-catchup-summary` + control rules using only existing tokens (`--card`/`--border`/`--muted-fg`/`--primary`), `border-radius: var(--radius)`, `--ring` focus-visible; no raw hex, no 50%, no `@font-face`.
- `chrome-extension/popup.js` - `catchup` state; `markRead()`, `refreshUnreadBanner()`, `runCatchMeUp()`, `showCatchupSummary()`, `wireCatchup()`; load-bearing switchTeam ordering (`refreshUnreadBanner()` before `markRead()`); scroll-listener + window-focus mark-read; `catchup_stream_*` routing in `handleUserPublication`.
- `chrome-extension/tests/test_popup_contract.mjs` - Six new frozen ids + a comment-stripped switchTeam ordering assertion.

## Decisions Made
- **Dedicated summary text node.** The streamed summary renders into `#catchup-summary-text` (a child of `#catchup-summary`) rather than the container itself, so `textContent` writes never wipe the close button. `#catchup-summary` is the shown/hidden panel; `#catchup-summary-text` is the write target. Both ids exist in `popup.html` (enforced by the referenced-id existence loop).
- **Route the error frame too.** In addition to start/chunk/end, `catchup_stream_error` appends a short `(error: …)` line so a backend stream failure is visible instead of leaving the "Summarizing…" placeholder forever. Harmless if the server never emits it.
- **Ordering gate is a real contract test, not just a grep.** The assertion extracts the `switchTeam()` body via brace-matching, strips comments (so the inline rationale that mentions `markRead()` can't be mistaken for a call site), and asserts `refreshUnreadBanner(` precedes `markRead(`. Verified it goes RED (142/143) when the two calls are swapped and GREEN (143/143) when correct.

## Deviations from Plan

None - plan executed exactly as written. The two engineering refinements above (dedicated text node, routing the error frame) are faithful implementations of the plan's intent, not scope changes.

## Issues Encountered
- **CRLF + comment false-positive in the ordering test.** The first draft of the ordering assertion used `indexOf("markRead(")` on the raw `switchTeam` body; the inline comment that documents the ordering rationale contains the literal `markRead()`, so it matched the comment before the real call and the test failed (142/143). Fixed by stripping block + line comments from the extracted body before comparing call indices. The switch/restore verification (CRLF-aware) then confirmed the gate fails on reorder and passes when correct.

## Verification (real output)
- `node --check popup.js` → SYNTAX OK (run from a copy outside `.claude/` with `{"type":"module"}`, since `.claude/package.json` forces commonjs and would mis-parse the ESM `import`s).
- `node tests/test_popup_contract.mjs` → **143 passed, 0 failed** (jsdom absent → optional DOM smoke noted, gate ran in full).
- `node tests/run_tests.mjs` → **12/12 test files passed**.
- Ordering gate negative check: swapping `refreshUnreadBanner()`/`markRead()` in `switchTeam` → **142 passed, 1 failed** (gate is real); restored → 143/0.
- Acceptance greps: `mark-read` POST in `markRead()` called from scroll-bottom + `window` focus; `unread-summary` gated on `count >= threshold`; `catch-me-up` POSTed ONLY inside `runCatchMeUp` (wired only to the `#btn-catchup-run` click — no auto-run); `catchup_stream_start/chunk/end/error` routed via `textContent`; all six new ids in `FROZEN_IDS`.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Client half of Catch me up is complete against the 23-02/23-03 endpoint + frame contract. Behavioral loop (a real streamed summary rendering) needs the extension loaded unpacked against a running stack — a documented residual like the Phase-20 UAT, not a blocker for the contract gate.
- No STATE.md / ROADMAP.md edits performed (per parallel-executor instructions).

## Self-Check: PASSED

- Files: popup.html, popup.css, popup.js, tests/test_popup_contract.mjs, 23-04-SUMMARY.md all present.
- Commits: `97371fc` (Task 1), `d09dcdf` (Task 2) both present in git.

---
*Phase: 23-catch-me-up*
*Completed: 2026-07-19*
