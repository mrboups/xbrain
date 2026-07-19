---
phase: 25-team-join-by-code
plan: 04
subsystem: ui
tags: [chrome-extension, invite-codes, overlay, shadcn-neutral, xss-safe, contract-test]

# Dependency graph
requires:
  - phase: 25-team-join-by-code (Plan 02)
    provides: POST /v1/teams/{id}/invite-codes (mint, plaintext once) + POST /v1/teams/join-by-code (redeem) response shapes
  - phase: 22-nudge-open (send-link overlay)
    provides: the xb-overlay markup + wireSendLink()/setSendLinkStatus() pattern this overlay mirrors
  - phase: 20-extension-restyle
    provides: shadcn Neutral tokens (var(--mono)/--radius/--muted/--border) + popup contract test harness
provides:
  - In-popup invite overlay (#invite-panel) with an admin "Create invite code" mint+reveal-once+copy surface
  - A "paste a code to join" field that redeems join-by-code and refreshes the team selector
  - Twelve new invite ids frozen in the popup contract test (test_popup_contract.mjs)
affects: [25-05-app-site-invite-ui, future extension restyles]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "One-time secret reveal in the DOM via textContent (never innerHTML), cleared on overlay close so it does not linger"
    - "Client presents the server's authoritative 403 as the admin gate (no client-side admin fabrication) — the my-teams payload carries no role, so mint is shown to all and 403 is surfaced"
    - "Post-join team-list refresh: full boot() when the user had no active team (first join), else a lightweight my-teams re-fetch + renderTeamSelector()"

key-files:
  created: []
  modified:
    - chrome-extension/popup.html
    - chrome-extension/popup.css
    - chrome-extension/popup.js
    - chrome-extension/tests/test_popup_contract.mjs

key-decisions:
  - "Two single-target status helpers (setInviteStatus / setInviteJoinStatus) instead of the plan's one shared setInviteStatus(id,...) — so both #invite-status and #invite-join-status are bound by literal $() calls, which the contract's FROZEN_IDS REFERENCED check requires"
  - "Admin gating is best-effort = none client-side: my-teams returns no role, so the mint action is visible to everyone and the server 403 is the authoritative gate (matches threat T-25-16)"
  - "closeInvite clears #invite-code-output.textContent + re-hides the code row so the one-time plaintext does not persist across opens (T-25-19)"

patterns-established:
  - "Invite overlay mirrors the send-link overlay 1:1 (xb-overlay structure, wireX/openX/closeX, single-target status helper) — the established extension overlay idiom"
  - ".xb-invite-code is a token-driven monospace chip (var(--mono)/--muted/--border/--radius, user-select:all) — HTML-applied, so it needs a css rule but not a FROZEN_CLASSES entry"

requirements-completed: [JOINCODE-01]

# Metrics
duration: ~20min
completed: 2026-07-19
---

# Phase 25 Plan 04: Extension Invite-Code Overlay Summary

**An in-popup invite overlay that mirrors the send-link overlay: an admin mints an invite code and reveals the plaintext ONCE (textContent, XSS-safe) with a Copy button, and any member pastes a code to join a team — English-only, shadcn Neutral, with the twelve new ids frozen in the popup contract test.**

## Performance

- **Duration:** ~20 min
- **Started:** 2026-07-19T16:33:00Z (approx)
- **Completed:** 2026-07-19T16:52:52Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- A header "invite" trigger + `#invite-panel` overlay reusing the `xb-overlay` structure, added beside the send-link overlay.
- Admin mint surface: `POST /v1/teams/{activeTeamId}/invite-codes` → the returned plaintext `code` is revealed ONCE via `textContent`, with a Copy-to-clipboard button and a "shown once" message. The revealed code is cleared on close so it never lingers in the DOM.
- Join surface: `POST /v1/teams/join-by-code {code}` → surfaces `already_member` vs `Joined <name>`, clears the input, and refreshes the team list (full `boot()` on first join, else a `my-teams` re-fetch + `renderTeamSelector()`).
- Fail-soft English status mapping for 403 (admin-only), 429 (rate-limit), 404 (generic invalid/expired/used-up), and network errors — the server 403 is the authoritative admin gate.
- Contract test extended: twelve invite ids frozen in `FROZEN_IDS`; `node tests/test_popup_contract.mjs` → **167 passed, 0 failed**; full suite `node tests/run_tests.mjs` → **12/12 test files passed**.

## Task Commits

Each task was committed atomically:

1. **Task 1: Invite overlay — HTML markup + CSS + popup.js wiring (mint+copy admin, paste-code join)** — `94a0225` (feat)
2. **Task 2: Freeze the twelve invite ids in the popup contract test + run the full suite** — `5991677` (test)

## Files Created/Modified
- `chrome-extension/popup.html` — header `#btn-invite` trigger + the `#invite-panel` overlay (admin mint/reveal/copy field + paste-code/join field), mirroring the send-link overlay markup.
- `chrome-extension/popup.css` — `.xb-invite-code` monospace reveal chip + `.xb-invite-code-row` flex layout + `#invite-status`/`#invite-join-status` monochrome status pills (all token-driven, radius 0).
- `chrome-extension/popup.js` — `wireInvite()` (called from the DOMContentLoaded boot path beside `wireSendLink()`), `openInvite`/`closeInvite`, `mintInvite`+`mapMintError`, `copyInvite`, `joinByCode`+`mapJoinError`, `refreshTeamsAfterJoin`, and two single-target status helpers. Code rendered via `textContent`; no hardcoded code/team data.
- `chrome-extension/tests/test_popup_contract.mjs` — `FROZEN_IDS` += the twelve invite ids under a `// Plan 25-04` comment.

## Decisions Made
- **Two status helpers instead of one shared `setInviteStatus(id,...)`.** The contract's section-1 FROZEN_IDS check requires every frozen id to appear in `REFERENCED`, and `REFERENCED` is derived from literal `$("id")` / `getElementById("id")` / `"#id"` matches. A shared helper resolving `$(id)` from a variable would leave `#invite-status` and `#invite-join-status` un-referenced and fail the freeze. Two single-target helpers (`setInviteStatus`, `setInviteJoinStatus`) each bind their id literally — the same shape as the existing `setSendLinkStatus`.
- **No client-side admin gating.** `/v1/teams/my-teams` returns `{id, slug, display_name, github_org}` with no `role`, so the client cannot tell admin status. The mint action is shown to everyone and the server's 403 is surfaced as "Only a team admin can create invite codes." — the server is the authoritative authZ boundary (threat T-25-16), and this is the plan's own `mintInvite` spec.
- **`.xb-invite-code-row` is a new HTML-applied class**, given a css rule but NOT added to `FROZEN_CLASSES` (popup.js never toggles it via `classList`), per the plan's FROZEN_CLASSES guidance.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Split the shared status helper into two single-target helpers so the FROZEN_IDS freeze resolves**
- **Found during:** Task 1 (popup.js wiring), confirmed necessary in Task 2 (freezing the ids)
- **Issue:** The plan specified one `setInviteStatus(id, text, type)` helper. But the contract test's FROZEN_IDS section requires each frozen id to be present in `REFERENCED`, which is built ONLY from literal `$("id")` / `getElementById("id")` / `"#id"` occurrences. A single helper calling `$(id)` with a *variable* would leave `invite-status` and `invite-join-status` absent from `REFERENCED`, failing their freeze in Task 2.
- **Fix:** Implemented `setInviteStatus(text, type)` (binds `$("invite-status")`) and `setInviteJoinStatus(text, type)` (binds `$("invite-join-status")`) — mirroring the existing single-target `setSendLinkStatus`. Both ids now appear as literal `$()` calls.
- **Files modified:** chrome-extension/popup.js
- **Verification:** `node tests/test_popup_contract.mjs` → 167 passed, 0 failed (both ids resolve in FROZEN_IDS section 1 and section 1b).
- **Committed in:** `94a0225` (Task 1) + frozen in `5991677` (Task 2)

**2. [Rule 2 - Missing Critical] Added `#invite-status` / `#invite-join-status` CSS status-pill rules**
- **Found during:** Task 1 (popup.css)
- **Issue:** The plan's CSS step called for only the `.xb-invite-code` reveal rule, but the two status divs set `className = "loading|success|error"` at runtime (mirroring send-link) and had no matching monochrome pill rules — they would render unstyled.
- **Fix:** Added `#invite-status`/`#invite-join-status` `.loading`/`.success`/`.error` rules identical to the established `#sendlink-status` block (token-driven, radius 0).
- **Files modified:** chrome-extension/popup.css
- **Verification:** Contract test token/font/a11y guards still green (167 passed).
- **Committed in:** `94a0225` (Task 1)

---

**Total deviations:** 2 auto-fixed (1 blocking, 1 missing-critical)
**Impact on plan:** Both were required to satisfy the plan's own acceptance criteria (FROZEN_IDS freeze + styled status) and match the established send-link idiom. No scope creep — same two surfaces, same overlay pattern, English-only, token-driven.

## Known Stubs
None — both surfaces are fully wired to the Plan 02 endpoints (mint + join-by-code) and render only server responses; no placeholder data.

## Threat Flags
None — no new security surface beyond the plan's `<threat_model>`. All four dispositions are mitigated: server-authoritative 403 (T-25-16), textContent-only render (T-25-17), no fabricated code/team (T-25-18), close-clears-the-code (T-25-19). Backend untouched (25-03 owns apps/memory-api).

## Issues Encountered
- **`node --check popup.js` and the ESM `.mjs` tests cannot run in-tree.** The worktree is physically nested under `D:/VSC/xbrain/.claude/`, whose `package.json` is `{"type":"commonjs"}` — the nearest ancestor package.json — so node parses `popup.js` (ESM `import`) as CommonJS and the `.js` sibling imports fail. Resolved per the plan's instruction: verification ran from a copy of `chrome-extension/` outside `.claude/` (scratchpad) with a `{"type":"module"}` package.json. Real counts recorded above (167 contract, 12/12 suite). This affects tooling only — the extension itself loads as an ES module in Chrome via `<script src="popup.js" type="module">`.

## Verification (real output, from the outside-.claude module copy)
- `node --check popup.js` → valid JS (loaded as ESM).
- Task 1 greps: all 11 required ids present in popup.html; `wireInvite(` count = 2 (definition + call site); mint targets `/invite-codes`, join targets `/join-by-code`; `invite-code-output").textContent` present; `grep -nP '[\x{00C0}-\x{00FF}]'` on popup.html/popup.js → no accented chars; `.xb-invite-code` uses `var(--mono)` + `var(--radius)`.
- Task 2: all twelve invite ids in `FROZEN_IDS`; `node tests/test_popup_contract.mjs` → **167 passed, 0 failed** (exit 0); `node tests/run_tests.mjs` → **12/12 test files passed** (exit 0). No existing guard weakened (XSS, no-fabricated-data, English-only, token resolution, a11y, catch-me-up ordering all still green).

## Next Phase Readiness
- The extension now covers the in-scope Settings surface (admin mint+copy + paste-code join). Deferred items honored: no email-delivery, no hosted `/join/<code>` landing page, no app-site web UI.
- Reload the extension unpacked to see the new overlay. Backend untouched — no deploy needed for this plan.
- No STATE.md / ROADMAP.md changes made (parallel-execution constraint; deferred to the orchestrator).
- Ran in parallel with Plan 03 (real-PG gate) on disjoint files — extension only, no `apps/memory-api/*` touched.

## Self-Check: PASSED
25-04-SUMMARY.md present; all 4 modified files present; both task commits (94a0225, 5991677) in the log.

---
*Phase: 25-team-join-by-code*
*Completed: 2026-07-19*
