---
phase: 26-collaborative-board
plan: 05
subsystem: ui
tags: [chrome-extension, popup, chrome.tabs.create, board, isSafeHttpUrl, contract-test]

# Dependency graph
requires:
  - phase: 26-collaborative-board (plan 26-02)
    provides: "POST /v1/teams/{team_id}/boards returning open_url (board token in the URL fragment), membership-gated, idempotent default board"
provides:
  - "btn-board header action in the extension popup — the phase's user-facing entry point to the collaborative board"
  - "openTeamBoard() — POST the team board, validate open_url with the existing isSafeHttpUrl, open via chrome.tabs.create, fail-soft, token never logged"
  - "popup contract test extended: btn-board frozen + section 7 pins the server-supplied / validated / never-logged shape of the handler"
affects: [26-04 board-web SPA (the open_url consumer), 26-07 non-mocked gate]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Header tab-opener actions register their listener directly in wireHeader() (mirrors btn-open-librechat) when they own no overlay/panel"
    - "A fetch->validate->chrome.tabs.create action reuses the existing isSafeHttpUrl guard from nudge_open.js — no parallel URL-safety machinery"
    - "Fail-soft on a bare header button via a transient label flash (no dedicated status id/overlay)"
    - "Contract-test source assertions scoped to the specific handler body (braceBlock of openTeamBoard) so a pre-existing unrelated log line is not a false positive"

key-files:
  created: []
  modified:
    - chrome-extension/popup.html
    - chrome-extension/popup.js
    - chrome-extension/tests/test_popup_contract.mjs

key-decisions:
  - "Listener registered inside wireHeader() (not a separate wireBoard()) — the board owns no overlay, so it belongs with the other bare header actions (btn-open-librechat / btn-add-to-memory)"
  - "The 'never logs open_url/xbt_token' contract assertion is scoped to the openTeamBoard() body, because a pre-existing nudge-handler log (`console.warn(\"[xbrain] open_url handling failed\", e)`) legitimately contains the static string 'open_url' and would otherwise be a false positive"

patterns-established:
  - "Server owns the board URL: the extension has no board-URL template (no #t=, no /?b=) and only opens the open_url the server returns"
  - "Scheme-validate any API-supplied URL with isSafeHttpUrl before chrome.tabs.create"

requirements-completed: [BOARD-01]

# Metrics
duration: 10min
completed: 2026-07-24
---

# Phase 26 Plan 05: Extension "Open board" Action Summary

**A `board` header button in the extension popup that POSTs `/v1/teams/{activeTeamId}/boards`, validates the returned `open_url` with the existing `isSafeHttpUrl`, and opens the team's collaborative board via `chrome.tabs.create` — fail-soft, token never logged, no new manifest permission — with the popup contract test extended to freeze the id and pin the handler's safe-URL shape.**

## Performance

- **Duration:** ~10 min
- **Started:** 2026-07-24T04:09:13Z (base commit db85255)
- **Completed:** 2026-07-24T04:19:07Z (Task 2 commit)
- **Tasks:** 2
- **Files modified:** 3 (0 created, 3 modified)

## Accomplishments

- **`btn-board` header button** sits beside `invite` in `.xb-header-right`, reusing the identical `xb-icon-btn xb-text-btn` class pair (no new CSS), English label + title.
- **`openTeamBoard()`** POSTs the active team's board endpoint with the caller's `xbt_token`, validates `data.open_url` with the existing `isSafeHttpUrl` (reused, not reinvented — the same guard as the Phase-22 nudge path), opens it with `chrome.tabs.create`, disables the button for the request duration against a double-click, and fails soft (a short English label flash) — the URL and token are never logged.
- **Contract test extended:** `btn-board` added to `FROZEN_IDS`, plus a section-7 block of four source-contract assertions pinning that the handler opens via `chrome.tabs.create`, builds no board URL client-side (no `#t=` / `/?b=`), validates with `isSafeHttpUrl(data.open_url)`, and never logs `open_url`/`xbt_token`. Negative check performed (below).
- **Zero new surface:** no bundler, no framework, no dependency, no new file type, no `manifest.json` change, no `chrome-extension/package.json`.

## Listener registration (per plan's output request)

Registered **inside `wireHeader()`** (not a separate `wireBoard()`), mirroring how `btn-open-librechat` and `btn-add-to-memory` are handled. Rationale: the board action owns no overlay/panel — it is a bare header button that opens a tab — so it belongs with the other header actions rather than in its own `wireX()` (which the invite/send-link overlays justify only because they own panels).

## Task Commits

Each task was committed atomically:

1. **Task 1: The 'board' header button and openTeamBoard()** — `a5c05a2` (feat)
2. **Task 2: Extend the popup contract test (freeze btn-board + section 7)** — `48a7c4d` (test)

**Plan metadata:** the SUMMARY metadata commit is the final commit of this plan.

## Files Created/Modified

- `chrome-extension/popup.html` — modified; added one `btn-board` button in `.xb-header-right` after `btn-invite`, shared classes, English label/title.
- `chrome-extension/popup.js` — modified; `btn-board` listener in `wireHeader()`, `openTeamBoard()` (POST boards -> isSafeHttpUrl -> chrome.tabs.create, double-click guard, fail-soft, no token logging), and a small `flashBoardError()` label helper.
- `chrome-extension/tests/test_popup_contract.mjs` — modified; `btn-board` in `FROZEN_IDS` and section 7 (four board-action source-contract assertions).

## Verification (real output — run from a copy outside `.claude/` with `{"type":"module"}`)

The worktree lives under `.claude/`, whose `package.json` is `{"type":"commonjs"}`; that would make `node --check popup.js` and the `.mjs` tests (which import ESM `.js` modules) treat the extension's ESM `.js` as CommonJS and fail. So all verification ran from a synced copy in the scratchpad with a `{"type":"module"}` package.json.

- `node --check popup.js` -> exit **0**.
- `node tests/run_tests.mjs` -> **12/12 test files passed**, suite exit **0**.
- `test_popup_contract.mjs` -> **173 passed, 0 failed** (was 168 at base: +1 `id contract: #btn-board` via `FROZEN_IDS`, +4 section-7 board-action assertions; the `referenced id exists: #btn-board` check was already added by Task 1's popup.js binding).
- All four section-7 assertions PASS:
  - `board action: openTeamBoard() opens the board with chrome.tabs.create`
  - `board action: the opened URL is server-supplied, never built in the extension`
  - `board action: the URL is scheme-validated before it is opened`
  - `board action: the board URL and token are never logged from the handler`

### Negative check (mandatory, PERFORMED)

Removing `id="btn-board"` from `popup.html` (in a throwaway copy — the worktree file was never touched) made `node tests/run_tests.mjs` exit **1**, with `test_popup_contract.mjs` reporting **171 passed, 2 failed**. Observed failure messages:

```
FAIL: id contract: #btn-board bound by popup.js AND present in popup.html
    popup.html is missing id="btn-board" — popup.js selector would break
FAIL: referenced id exists in popup.html: #btn-board
    popup.js binds #btn-board but popup.html has no id="btn-board" — getElementById("btn-board") would return null and break popup init
```

The guard is real: the suite passes with the button and fails without it.

### Acceptance greps (worktree files)

- `id="btn-board" class="xb-icon-btn xb-text-btn"` present in `popup.html`; `Open the team` present.
- `popup.js`: `/boards`, `btn-board`, `isSafeHttpUrl(data.open_url)`, `chrome.tabs.create` all present.
- `popup.js` (comment-stripped): **no** `#t=` and **no** `/?b=` — the extension builds no board URL.
- `manifest.json` is **not** in this plan's diff; `chrome-extension/package.json` does **not** exist.
- Test file: `"btn-board"`, `Plan 26-05`, `isSafeHttpUrl`, `open_url`, `chrome.tabs.create` all present.

## Decisions Made

- **Listener in `wireHeader()`** (see section above).
- **Scoped the "never logs" contract assertion to the `openTeamBoard()` body.** See Issues Encountered — a pre-existing nudge-handler log line contains the static string `open_url` and would otherwise trip a whole-file assertion.
- **Fail-soft via a transient label flash** (`board failed` for 2 s, then restore) rather than a new status id/overlay — the plan explicitly forbids adding overlays/panels/ids for the board action.

## Deviations from Plan

None — plan executed exactly as written. The handler follows the plan's skeleton (POST boards -> `isSafeHttpUrl(data.open_url)` -> `chrome.tabs.create`, double-click guard, fail-soft, no token logging); the test extends `FROZEN_IDS` and adds the three required source-contract assertion groups (server-supplied URL, validated-before-open, never-logged). The two implementation choices the plan left open (listener location; the fail-soft surface on a button that owns no status element) are documented under Decisions.

## Issues Encountered

- **Pre-existing whole-file grep collision.** The plan's Task-1 acceptance grep `grep -v '^\s*//' popup.js | grep -c 'console.*open_url\|console.*xbt_token'` is written to return 0, but `popup.js:956` — a **pre-existing** nudge-handler line `console.warn("[xbrain] open_url handling failed:", e)` — matches `console.*open_url`, so the literal grep returns **1**. That line predates this plan (it is in `handleUserPublication`, unrelated to the board), logs only a static description plus the error object `e`, and does **not** log any URL value; it is out of scope per the executor scope boundary. My board handler's own console line, `console.warn("[xbrain] open board failed:", err && err.message)`, carries neither `open_url` nor `xbt_token`, satisfying the intent (T-26-32). To keep the Task-2 contract test meaningful **and** green, its "never logs" assertion is scoped to the `openTeamBoard()` body (via `braceBlock`) rather than the whole file — with a comment explaining exactly why. The board handler body contains no `console.*open_url` / `console.*xbt_token`, so the scoped assertion passes and still fails if a future edit logs the token from the handler.
- **`.claude` forces CommonJS.** See Verification — resolved by running from a copied tree outside `.claude/` with `{"type":"module"}`.

## Known Stubs

None. The button is wired to the real `POST /v1/teams/{id}/boards` endpoint (26-02) and the real `chrome.tabs.create`; there is no placeholder data or hardcoded response. The board SPA the `open_url` points at is 26-04's surface, out of scope here.

## Threat Flags

None. All new surface (the API-supplied URL reaching `chrome.tabs.create`, the board token in the fragment, the double-click DoS, the silent-drop repudiation) is already enumerated in the plan's `<threat_model>` (T-26-31 … T-26-35) and mitigated as specified: `isSafeHttpUrl` gate (T-26-31), no URL/token logging (T-26-32), no client-side URL construction (T-26-33), button disabled for the request (T-26-34), `btn-board` frozen with a proven negative check (T-26-35).

## Next Phase Readiness

- The phase's user-facing entry point is live: a team member clicks `board` in the chat header and the active team's board opens in a tab.
- **26-04 (board-web SPA):** consumes the same `open_url` this button opens; the token-in-fragment contract is honoured end-to-end (the extension never touches or logs the fragment).
- **26-07 (non-mocked gate):** the real POST-boards round trip and the live `chrome.tabs.create` open are browser-runtime behaviours; the mechanical contract here pins the source shape but a live extension smoke belongs to the integration gate.

## Self-Check: PASSED

- All 3 modified files exist on disk (verified); no files created.
- Task commits `a5c05a2` (feat) and `48a7c4d` (test) present in `git log` (verified).

---
*Phase: 26-collaborative-board*
*Completed: 2026-07-24*
