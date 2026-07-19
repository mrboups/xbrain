---
phase: 21-configurable-agent-aliases
plan: 04
subsystem: ui
tags: [chrome-extension, mention-detection, agent-aliases, regex, re-escape, popup, options, settings]

# Dependency graph
requires:
  - phase: 21-02
    provides: "GET /v1/teams/{id}/agent-aliases (member, effective list) + PATCH (admin, validated) — the one source of truth the client builds its regex from"
  - phase: 20-01
    provides: "popup selector/token contract (test_popup_contract.mjs) + shadcn Neutral styling the composer hint must not break"
provides:
  - "chat_stream.js buildMentionRegex(aliases) — JS-escapes each alias (mirror of server re.escape), sorts longest-first, filters reserved 'claude', falls back to ['agent']"
  - "chat_stream.js detectMentionClient(text, aliasesOrRegex) — accepts a prebuilt RegExp or an alias array; defaults to the ['agent'] regex, never the stale vocabulary"
  - "popup.js refreshAgentAliases() — fetches + caches the effective list on team switch + on xbt_token storage change, rebuilds the client regex (fail-soft)"
  - "popup.js optimistic composer hint ('Will summon @<alias>') driven by the cached, server-derived regex"
  - "options.js admin-only 'Agent name' field — GET-prefill + PATCH /agent-aliases + re-fetch, 403/422 surfaced cleanly, no restart"
affects: [chrome-extension-settings-ui, team-chat-mention-ux]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Client mention vocabulary is BUILT from the server's effective alias list, never hardcoded — one source of truth, desync class closed permanently"
    - "Client-side re.escape parity: escapeAlias() mirrors the server's re.escape so a hostile/garbled alias can never inject regex behaviour (ReDoS/catch-all defense in depth)"
    - "Fail-soft alias refresh: any fetch error keeps the previous regex and never throws into the popup UI (server is authoritative anyway)"
    - "Composer hint + Settings field built dynamically in JS with inline shadcn tokens — no popup.html/popup.css/options.html contract touched, so the frozen selector/token gate stays green"

key-files:
  created:
    - .planning/phases/21-configurable-agent-aliases/21-04-SUMMARY.md
  modified:
    - chrome-extension/chat_stream.js
    - chrome-extension/popup.js
    - chrome-extension/options.js
    - chrome-extension/tests/test_chat_stream.mjs

key-decisions:
  - "buildMentionRegex filters 'claude' case-insensitively as defense in depth on top of the server never storing it — @claude can never be a client trigger even if a bad list is fetched"
  - "The optimistic composer hint and admin Settings field are created dynamically in JS (inline-styled with existing --muted-fg/--xb-text-mute tokens) so popup.html/popup.css and options.html are untouched and the Phase-20 contract test stays green"
  - "options.js Save splits the input on commas and strips leading '@' before PATCH; the server re-validates, dedupes, and always re-adds the defaults (@agent included), so sending the whole effective list back is safe and idempotent"
  - "storage.onChanged already reboots via boot()->switchTeam->refreshAgentAliases; an explicit refreshAgentAliases() is added on token change as belt-and-suspenders for the currently active team"

patterns-established:
  - "Client detector parity test: buildMentionRegex(list) must match the same aliases the server would summon for that list AND reject @claude, with JS-escape + longest-first proven (the client half of the gate lesson)"

requirements-completed: [ALIAS-01]

# Metrics
duration: 12min
completed: 2026-07-19
---

# Phase 21 Plan 04: Extension Client Build-From-List + Admin Settings Field Summary

**The Chrome extension now builds its `@mention` regex from the server's effective alias list (JS-escaped, longest-first) instead of the stale hardcoded `@(grooveos|groove|gr|g)` — fetched and cached on team switch / re-auth, exposed as an admin-only "Agent name" field that PATCHes with no restart, and `@claude` is gone from every client path.**

## Performance

- **Duration:** ~12 min
- **Started:** 2026-07-19T01:43:00Z
- **Completed:** 2026-07-19T01:55:21Z
- **Tasks:** 2
- **Files modified:** 4 (3 code + 1 test); 1 planning doc created

## Accomplishments
- `chat_stream.js` no longer hardcodes a mention vocabulary. `buildMentionRegex(aliases)` JS-escapes each alias (mirror of the server's `re.escape`), de-dupes case-insensitively, sorts longest-first, filters the reserved `claude`, and falls back to `["agent"]` on an empty list — building the SAME boundary pattern the server uses. `detectMentionClient(text, aliasesOrRegex)` takes a prebuilt RegExp or an alias array and defaults to the `["agent"]` regex (never the stale token). The `@(grooveos|groove|gr|g)` literal is deleted.
- `popup.js` fetches `GET /v1/teams/{id}/agent-aliases`, caches it in `state.agentAliases`, and rebuilds `state.mentionRe` — on team switch (inside `switchTeam`) and on `xbt_token` storage change — fail-soft (keeps the prior regex on error). An optimistic composer hint ("Will summon @&lt;alias&gt;") toggles off the input event using the cached, server-derived regex. **The composer hint was wired.**
- `options.js` gains an admin-only "Agent name" field: it GET-prefills the effective list, PATCHes `/v1/teams/{id}/agent-aliases`, and re-fetches so a changed name reflects immediately (no restart, D-21-05). 403 (non-admin) and 422 (validation) are surfaced cleanly; the field is rendered only under `isAdmin`.
- Client parity tests: `buildMentionRegex(["agent","chad","a","wizard"])` matches `@agent/@chad/@a/@wizard`, `@claude` is always `null` (even when smuggled into the list), and a hostile `".*"` alias matches only the literal `@.*`. Wired into `run_tests.mjs`.

## Task Commits

Each task was committed atomically:

1. **Task 1: chat_stream build-from-list + popup fetch/cache/refresh + options admin field** - `22dab2d` (feat)
2. **Task 2: JS gate — client regex matches the server list + @claude rejected + escape parity** - `493815d` (test)

**Plan metadata:** committed separately with this SUMMARY.

## Files Created/Modified
- `chrome-extension/chat_stream.js` - Deleted the hardcoded `MENTION_RE`; added `escapeAlias()` + `buildMentionRegex()` + a build-from-list `detectMentionClient()`; header docstring now says the regex is BUILT FROM the server list.
- `chrome-extension/popup.js` - Import `buildMentionRegex`; `state.agentAliases`/`state.mentionRe`; `refreshAgentAliases()` (team switch + storage change, fail-soft); dynamic composer hint element + `updateMentionHint()`.
- `chrome-extension/options.js` - Admin-only "Agent name" section inside `fillTeamBody`'s `isAdmin` block; `loadAgentAliases()` (GET prefill) + `saveAgentAliases()` (PATCH + re-fetch, 403/422 handling).
- `chrome-extension/tests/test_chat_stream.mjs` - Replaced the stale `@claude/@c/@cl` positive-trigger tests with build-from-list, custom-alias, `@claude`-rejected, boundary-parity, escape-parity, longest-first, and empty/degenerate cases (36 in-file assertions).

## Decisions Made
- `buildMentionRegex` filters `claude` case-insensitively as defense in depth on top of the server never storing it — `@claude` can never become a client trigger even from a garbled fetch.
- Composer hint + Settings field are built dynamically in JS (inline-styled with existing shadcn tokens), so `popup.html`, `popup.css`, and `options.html` are untouched and the Phase-20 selector/token/a11y contract test (`test_popup_contract.mjs`) stays green (90/90).
- Save splits the input on commas and strips a leading `@`; the server re-validates, dedupes, and re-adds the defaults, so sending the whole effective list back is safe/idempotent.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- **Module-type gotcha (environment, not the code):** this executor runs inside a git worktree under `.claude/`, and `.claude/package.json` declares `{"type":"commonjs"}`, which disables Node 24's automatic ESM detection and makes `node --check`/the `.mjs` tests fail on `chat_stream.js`'s `export` when run in-place. Resolved exactly as the plan anticipated — verification (`node --check` + `run_tests.mjs`) was run from a copy of `chrome-extension/` in the scratchpad **outside** `.claude/`, where Node auto-detects the ESM `.js` files. This is a test-harness location constraint only; the shipped files are unchanged.

## Verification (real output)
- `node --check chat_stream.js && node --check popup.js && node --check options.js` (run outside `.claude/`) -> `check-ok`.
- `node tests/test_chat_stream.mjs` -> **36 passed, 0 failed** (exit 0). All 9 mention cases PASS, incl. `@claude` -> null, `.*` escaped, longest-first `grove`.
- `node tests/run_tests.mjs` -> **11/11 test files passed** (exit 0), including `test_popup_contract.mjs` -> **90 passed, 0 failed** (selector/token/a11y contract intact).
- `grep 'grooveos|groove|gr|g' chat_stream.js` -> **no match** (stale literal deleted).
- `grep "export function buildMentionRegex" chat_stream.js` -> match.
- `grep -c "agent-aliases"` -> popup.js **3**, options.js **3**; `grep 'method: "PATCH"' options.js` -> match.
- `grep "@claude" chat_stream.js popup.js options.js` -> **no match**; in the test file every `@claude` reference is a negative (`=== null` / `=== false`) assertion.

## Known Stubs
None - the client reads the real `GET /v1/teams/{id}/agent-aliases` endpoint and PATCHes the real endpoint; no placeholder data or hardcoded alias vocabulary introduced. The composer hint is a UX-only optimistic label derived from the same server list; the server remains authoritative for the actual summon.

## User Setup Required
None for the code path. **Operational note:** the user must **reload the unpacked extension** (chrome://extensions -> reload) to pick up these `chat_stream.js`/`popup.js`/`options.js` changes — a browser-side action, no rebuild/deploy. (Carried-over ops note from 21-01/21-02 still applies: set the VM `.env` `AGENT_MENTION_ALIASES=agent,chad,a` on next deploy to match the server default.)

## Next Phase Readiness
- The client/server mention desync class is closed: both derive from one list (`GET /agent-aliases`). A team admin sets the name in the extension Settings; server detection reads the DB per message (immediate) and the popup re-fetches on team switch / re-auth, so the change takes effect with no restart.
- Ready for the phase-level verification/ship: the extension client half of the gate lesson is proven green alongside the 21-02/21-03 server-side real-Postgres gates.
- No blockers.

## Self-Check: PASSED

All created/modified files present on disk (`21-04-SUMMARY.md`, `chat_stream.js`, `popup.js`, `options.js`, `test_chat_stream.mjs`); both task commits (`22dab2d`, `493815d`) exist in git history.

---
*Phase: 21-configurable-agent-aliases*
*Completed: 2026-07-19*
