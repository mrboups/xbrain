---
phase: 22-push-a-link
plan: 03
subsystem: ui
tags: [chrome-extension, centrifugo, notifications, websocket, nudge, shadcn]

# Dependency graph
requires:
  - phase: 22-01
    provides: POST /v1/teams/{team_id}/nudge-open (server publishes open_url to user:<sub>)
  - phase: 22-02
    provides: nudge_open.js consent-core receive handler (handleOpenUrl, isSafeHttpUrl) + allowOpenLinkRequests setting
provides:
  - popup subscribes to its own user:<source_user_id> Centrifugo channel and routes open_url to the consent-gated handler
  - background chrome.notifications.onClicked opens the nudge tab (the ONLY nudge tab-open path, the consent gesture)
  - "send link" chat affordance (member picker + URL) that POSTs the nudge-open endpoint with client isSafeHttpUrl pre-check
  - full referenced-id existence guard in the popup contract test (popup.js<->popup.html id mismatch goes RED)
  - docs/push-a-link.md feature doc incl. the D-22-06 offline residual and security posture
affects: [chrome-extension, push-a-link, notifications]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Direct-to-user delivery via a per-user Centrifugo channel (user:<source_user_id>) subscribed once, independent of team switches"
    - "Structural consent gate: receive handler has no tab API; tab-open lives solely in background notifications.onClicked (a user gesture)"
    - "Contract test asserts EVERY referenced id exists in popup.html, not just a frozen subset"

key-files:
  created:
    - docs/push-a-link.md
  modified:
    - chrome-extension/popup.js
    - chrome-extension/background.js
    - chrome-extension/popup.html
    - chrome-extension/popup.css
    - chrome-extension/tests/test_popup_contract.mjs

key-decisions:
  - "Send-link affordance is a header-triggered overlay (reuses the clip-overlay shadcn classes) — no navigation, minimal new surface"
  - "submitSendLink uses a raw fetch (not fetchJson) so 202 vs 403/422/429 map to distinct English status text; fetchJson hides the status code"
  - "Target picked by user_id (UUID) — the value the members endpoint already returns cheaply; the server keys the publish channel by source_user_id"
  - "Recipient opt-out stays client-only for v1 (enforced by the recipient's own extension), matching D-22-04 discretion"

patterns-established:
  - "user:<sub> subscription is idempotent (guarded by state.userSubscription) and reset on reconnect so a token refresh/team switch never double-subscribes"
  - "New status pills mirror the id-scoped #clip-status .loading/.success/.error rules rather than adding global classes"

requirements-completed: [NUDGE-01]

# Metrics
duration: 24min
completed: 2026-07-19
---

# Phase 22 Plan 03: Push-a-Link Wiring + Send Affordance Summary

**The extension now closes the push-a-link loop: it subscribes to its own `user:<sub>` channel and routes `open_url` to the consent-gated handler, opens the tab ONLY on the recipient's notification click (background service worker), and ships a "send link" popup control that POSTs the same-team nudge-open endpoint — with the offline residual documented.**

## Performance

- **Duration:** ~24 min
- **Started:** 2026-07-19T05:04Z
- **Completed:** 2026-07-19T05:28Z
- **Tasks:** 2
- **Files modified:** 5 (1 created, 4 modified)

## Accomplishments
- `popup.js` subscribes once to `user:${state.me.source_user_id}` after Centrifugo connects and routes every `open_url` frame to `nudge_open.handleOpenUrl` with chrome-backed deps (`getSettings` / `notify` / `persistPending`). The popup never opens a tab.
- `background.js` registers `chrome.notifications.onClicked` → reads `chrome.storage.session["nudge_<id>"]` → `chrome.tabs.create` → removes the key + clears the notification. This is the single nudge tab-open path and the required user gesture (T-22-12), with the pending url wiped immediately after (T-22-14).
- A "send link" header control opens an overlay: lazily fetches the active team's members, excludes self + blocked, validates the URL with `isSafeHttpUrl` client-side, then POSTs `/v1/teams/{id}/nudge-open`, surfacing 202/403/422/429 as clear English status (T-22-13: client check is UX-only; server is the boundary).
- The popup contract test now asserts **every** id `popup.js` binds also exists in `popup.html` (129 assertions, +39), so a `popup.js`↔`popup.html` id mismatch fails the test instead of throwing at popup init.
- `docs/push-a-link.md` documents the end-to-end flow, the security posture (same-team, consent-gated, URL-validated, per-sender rate-limited, recipient opt-out default ON), and the D-22-06 offline residual + deferred items.

## Task Commits

Each task was committed atomically:

1. **Task 1: Subscribe user:<sub> channel + background click→tab** - `fc9b52f` (feat)
2. **Task 2: "Send link to a member" chat affordance + residual doc** - `8457395` (feat)

## Files Created/Modified
- `chrome-extension/popup.js` - import `handleOpenUrl`/`isSafeHttpUrl`; `state.userSubscription`; `subscribeUserChannel()` + `handleUserPublication()`; the send-link overlay wiring (`wireSendLink`, `openSendLink`, `populateSendLinkMembers`, `submitSendLink`, `mapNudgeError`, `setSendLinkStatus`).
- `chrome-extension/background.js` - top-level `chrome.notifications.onClicked` listener (the consent gesture → tab open → session-key cleanup).
- `chrome-extension/popup.html` - `#btn-send-link` header control + `#sendlink-panel` overlay (member select, url input, status, submit/cancel/close), reusing the existing overlay/field/button markup.
- `chrome-extension/popup.css` - extended `.xb-field` inputs to cover `input[type="url"]` + `select`; added `#sendlink-status` pill states mirroring `#clip-status`.
- `chrome-extension/tests/test_popup_contract.mjs` - added the full referenced-id existence gate (section 1b).
- `docs/push-a-link.md` (created) - feature doc + security posture + offline residual (D-22-06) + deferred items.

## Decisions Made
- Overlay affordance over an inline composer control — reuses the proven clip-overlay shadcn classes (radius 0, Neutral tokens, no new fonts) and keeps the new surface minimal.
- Raw `fetch` for the nudge POST (not `fetchJson`) because `fetchJson` throws on non-2xx and hides the status code needed to map 403/422/429 to distinct English messages.
- Target identified by `user_id` (UUID) — the members endpoint returns it cheaply; the server re-resolves membership and keys the publish channel by `source_user_id`.

## Deviations from Plan

None - plan executed exactly as written. Task 1 added no new ids (pure wiring, as the plan anticipated); Task 2 added the explicit referenced-id existence check exactly as the corrected acceptance required.

## Issues Encountered
- **Test-runner module resolution under `.claude/`.** Running `node run_tests.mjs` inside the executor worktree (which lives at `.claude/worktrees/...`) fails 9/12 with `Named export ... not found ... CommonJS module`, because `.claude/package.json` (`{"type":"commonjs"}`) is an ancestor and disables Node 24's automatic ESM syntax-detection for the extension's `export`-style `.js` files. This is exactly the CONTEXT warning ("run the harness from OUTSIDE `.claude/`") and is NOT caused by this plan's changes. Verified faithfully by copying the worktree's `chrome-extension/` to the scratchpad (outside `.claude/`) and running there: **12/12 test files pass**, including `test_popup_contract.mjs` (129/129), `test_nudge_open.mjs` (7/7), and `test_settings.mjs` (11/11). ESM syntax of the edited `popup.js`/`background.js` was also verified directly via `node --check --input-type=module`.

## User Setup Required
None - no external service configuration required. (Manual acceptance of the live flow requires the deployed memory-api + Centrifugo and two same-team members; the client + contract are fully covered by the node suite.)

## Next Phase Readiness
- The push-a-link loop is complete client-side end to end: subscribe → notify → consent-click → tab.
- Follow-ups (explicitly deferred, documented in `docs/push-a-link.md`): offline/closed-browser delivery (pending-nudge persistence + Web Push), server-side shortener expansion, cross-team targeting, and a server-stored recipient opt-out.

## Self-Check: PASSED

- Created/modified files all present on disk: `popup.js`, `background.js`, `popup.html`, `popup.css`, `tests/test_popup_contract.mjs`, `docs/push-a-link.md`, `22-03-SUMMARY.md`.
- Task commits exist in git history: `fc9b52f` (Task 1), `8457395` (Task 2).
- Faithful test run (outside `.claude/`): 12/12 files, contract 129/129, nudge_open 7/7, settings 11/11.

---
*Phase: 22-push-a-link*
*Completed: 2026-07-19*
