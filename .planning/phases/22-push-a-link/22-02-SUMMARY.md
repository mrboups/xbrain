---
phase: 22-push-a-link
plan: 02
subsystem: ui
tags: [chrome-extension, notifications, consent, esm, node-test, tdd]

# Dependency graph
requires:
  - phase: 22-push-a-link (Plan 01)
    provides: "server nudge endpoint publishing {type:'open_url', url, from, team_id, team_slug} to user:<sub>"
provides:
  - "nudge_open.js — pure isSafeHttpUrl(url) + handleOpenUrl(data, deps) consent-gated receiver with NO browser-tab capability"
  - "allowOpenLinkRequests recipient opt-out setting (default ON) in settings.js DEFAULT_SETTINGS + _SCHEMA"
  - "Options UI checkbox #opt-allow-open-link wired to chrome.storage.sync"
  - "Node proof (test_nudge_open.mjs) of consent-gate + opt-out + bad-scheme drop + structural no-tabs assertion"
affects: [22-push-a-link Plan 03 (notification click → tab open), 22-push-a-link Plan 04 (chat send-link affordance)]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Capability-denial by construction: the receive handler imports no chrome.* and never names the tab-opening API; a node grep asserts the absence"
    - "Dependency injection for chrome.* side effects (getSettings/notify/persistPending) so the handler is testable in plain node"

key-files:
  created:
    - chrome-extension/nudge_open.js
    - chrome-extension/tests/test_nudge_open.mjs
  modified:
    - chrome-extension/settings.js
    - chrome-extension/options.html
    - chrome-extension/options.js
    - chrome-extension/tests/test_settings.mjs

key-decisions:
  - "handleOpenUrl has zero browser-tab capability — auto-open is structurally impossible (D-22-02), enforced by a source grep in the node test"
  - "Client re-validates http/https via new URL as defense-in-depth over the server check (D-22-03)"
  - "Recipient opt-out is client-only for v1 (the recipient's own extension enforces it), default ON (D-22-04)"

patterns-established:
  - "Pattern: receive-side handlers deny dangerous capabilities structurally, not by convention, and prove it with a grep test"
  - "Pattern: chrome.* effects injected as deps → the pure logic runs under `node` with no jsdom/polyfill"

requirements-completed: [NUDGE-01]

# Metrics
duration: 21min
completed: 2026-07-19
---

# Phase 22 Plan 02: Extension consent-gated open_url receiver + recipient opt-out Summary

**A pure, node-tested `handleOpenUrl` that turns an incoming open_url nudge into a notification carrying the sender + full URL, opens no tab (no tabs capability at all), and is suppressed entirely by an `allowOpenLinkRequests` opt-out that defaults ON.**

## Performance

- **Duration:** 21 min
- **Started:** 2026-07-19T04:49:00Z
- **Completed:** 2026-07-19T05:10:54Z
- **Tasks:** 2 (both TDD)
- **Files modified:** 6 (2 created, 4 modified)

## Accomplishments
- `nudge_open.js`: `isSafeHttpUrl` (http/https only) + `handleOpenUrl(data, deps)` that gates on opt-out, then on scheme, then builds a notification with the sender name as title and the FULL literal URL as message — and returns the notification id (stashed via `persistPending` for the Plan-03 click).
- Structural no-auto-open guarantee (D-22-02 / T-22-08): the module imports nothing from `chrome.*` and never names the tab-opening capability; a node test greps the source for the substring "tabs" and fails if present.
- `allowOpenLinkRequests` recipient opt-out (D-22-04), default ON, in `DEFAULT_SETTINGS` + `_SCHEMA`, round-tripping through `chrome.storage.sync`, with an Options checkbox (`#opt-allow-open-link`) wired exactly like `opt-side-panel`.
- Full extension suite stays green: 12/12 test files pass (new `test_nudge_open.mjs` 7/7, extended `test_settings.mjs` 11/11, `test_popup_contract.mjs` 90/90 untouched).

## Task Commits

Each task was committed atomically (TDD RED → GREEN):

1. **Task 1 (RED): failing test for consent-gated open_url receiver** - `975e5b5` (test)
2. **Task 1 (GREEN): consent-gated open_url receiver (no tabs capability)** - `6f3a5eb` (feat)
3. **Task 2 (RED): failing assertions for allowOpenLinkRequests default ON** - `752c7f1` (test)
4. **Task 2 (GREEN): recipient allowOpenLinkRequests toggle (default ON)** - `3b58f75` (feat)

**Plan metadata:** committed with this SUMMARY (docs: complete plan)

## Files Created/Modified
- `chrome-extension/nudge_open.js` - Pure `isSafeHttpUrl` + `handleOpenUrl` consent-gated receiver; no chrome.* imports, no tab capability.
- `chrome-extension/tests/test_nudge_open.mjs` - 7 cases: notify-once-with-full-url, sender fallbacks, opt-out suppression, bad-scheme drop, wrong-type drop, isSafeHttpUrl unit rows, structural no-"tabs" assertion.
- `chrome-extension/settings.js` - `allowOpenLinkRequests: true` added to `DEFAULT_SETTINGS` and `["boolean"]` to `_SCHEMA` (no other keys touched).
- `chrome-extension/options.html` - `#opt-allow-open-link` checkbox with English label "Allow open-link requests" + help text, alongside the existing toggles.
- `chrome-extension/options.js` - init + change wiring for the new checkbox, guarded with `if (cb)`, mirroring `opt-side-panel`.
- `chrome-extension/tests/test_settings.mjs` - 4 new assertions (default ON, explicit-false kept, non-boolean type-guarded to true, omitted key → true); existing empty-storage deepEqual updated to include the new default.

## Decisions Made
- **No browser-tab capability in the receiver** — the tab-open lives only in the notification click handler (Plan 03). Enforced structurally (grep test), not by convention.
- **Client-side scheme re-validation** via `new URL` — defense-in-depth over the server's `is_safe_nudge_url`; `javascript:`/`data:`/`file:`/`mailto:`/malformed are dropped with no notification.
- **Opt-out defaults ON and is client-only for v1** — the recipient's own extension enforces it (per D-22-04 discretion). Noted here as the recorded discretion choice; a server-stored preference remains a possible follow-up.

## Deviations from Plan

None - plan executed exactly as written. (One in-loop correction, not a plan deviation: my initial `nudge_open.js` doc comment referenced `chrome.tabs`, which the structural no-"tabs" test correctly caught; reworded the comment to "browser-tab API" so the source contains zero "tabs" occurrences. The guard did its job.)

## Issues Encountered
- **ESM module resolution for `.js` files under `.claude/`.** The extension's `.js` modules use `export`, but there is no `package.json` with `type:module` in the tree, and `.claude/package.json` forces `commonjs`. Per the plan's constraint, tests were run from a copy in the scratchpad (outside `.claude/`) with a `{"type":"module"}` marker so `node` resolves the `.js` modules as ESM. This is a test-run mechanic only — nothing was added to the committed extension.

## Threat Register Coverage
- **T-22-08 (unwanted navigation):** mitigated — no tab capability; grep-asserted absence of "tabs".
- **T-22-09 (unsafe scheme reaches user):** mitigated — `isSafeHttpUrl` drops non-http(s) client-side.
- **T-22-10 (social-engineering):** mitigated — notification shows sender (title) + full literal URL (message), no shortening.
- **T-22-11 (nuisance / consent withdrawal):** mitigated — `allowOpenLinkRequests` OFF returns before notifying; client-enforced for v1.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Plan 03 can now subscribe the extension to `user:<sub>`, route the `open_url` event into `handleOpenUrl`, provide the real `notify` (chrome.notifications.create) + `persistPending` deps, and wire `notifications.onClicked` → tab open (the only place the tab-open lives).
- No blockers. The receiver, opt-out, and their node proofs are in place and green.

## Self-Check: PASSED
- All 7 created/modified files present on disk.
- All 4 task commits (975e5b5, 6f3a5eb, 752c7f1, 3b58f75) present in git history.
- STATE.md / ROADMAP.md untouched (per parallel-executor constraint).

---
*Phase: 22-push-a-link*
*Completed: 2026-07-19*
