---
phase: 27-pwa-and-push
plan: 06
subsystem: ui
tags: [pwa, chat, centrifugo, websocket, realtime, anti-fork, static-analysis, tdd]

# Dependency graph
requires:
  - phase: 27-pwa-and-push
    plan: 02
    provides: createRenderer, createPublicationRouter, connectRealtime — and the guarantee that the socket URL can only come from the API
  - phase: 27-pwa-and-push
    plan: 05
    provides: the PWA shell — index.html, app.js, auth.js, app.css, platform_web.js, sw.js and its precache list
  - phase: 27-pwa-and-push
    plan: 01
    provides: createApi + rawFetch, the platform shim, isSafeHttpUrl, scripts/sync-chat-core.mjs
  - phase: 22-push-a-link
    provides: handleOpenUrl — the consent core the PWA reuses without an opener
provides:
  - app-site/app/chat.js — the PWA chat surface (bootChat), built entirely on chat-core
  - app-site/app/vendor/centrifuge.js — the same vendored client the extension runs
  - chrome-extension/tests/test_pwa_chat.mjs — the anti-fork, import-resolution and precache-coverage contract
  - the in-page nudge banner, for people who have not granted notification access
affects: [27-07 push opt-in, 27-08/09 deployed-origin gates]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Anti-fork by name: a surface may not DECLARE any name the shared core declares, internals included"
    - "Live facade onto replaceable state (streamBuffer), so a long-lived router cannot strand itself on a stale instance"
    - "Capability withheld rather than disabled: no opener is passed, so no auto-open path exists to be misconfigured"

key-files:
  created:
    - app-site/app/chat.js
    - app-site/app/vendor/centrifuge.js
    - chrome-extension/tests/test_pwa_chat.mjs
  modified:
    - app-site/app/app.js
    - app-site/app/index.html
    - app-site/app/app.css
    - chrome-extension/tests/test_pwa_shell.mjs

key-decisions:
  - "The anti-fork banned set is chat-core's INTERNAL declarations, not its exports: buildBubbleNode and handlePublication are exactly the forks D-27-04 exists to stop, and neither is exported by name"
  - "sw.js is named as the one file that must never be precached — a cache-first worker that caches itself serves the old worker forever"
  - "No opener is supplied to handleOpenUrl, so the PWA holds no auto-open capability at all; the extension's opt-in fast path is a capability the web surface simply does not have"
  - "A realtime failure is caught and downgraded to a banner, so a token-mint outage costs live updates instead of history and sending"
  - "The last-read team is remembered per device but re-validated against my-teams on every boot"

requirements-completed: [PWA-01]

# Metrics
duration: 9min
completed: 2026-08-01
---

# Phase 27 Plan 06: PWA Chat Surface Summary

**`/app/` is now the same team chat the extension shows — history, send, live incoming messages — running on the shared chat-core modules, with a test that fails the build if the PWA ever declares a name chat-core already declares.**

## Performance

- **Duration:** ~9 min (first task commit 11:15:38+02:00 → last 11:24:32+02:00)
- **Tasks:** 3 (Task 3 under TDD)
- **Files created:** 3 · **Files modified:** 4 · **Files deleted:** 0
- **Test suite:** 17 → **18/18 test files**; the files reporting an explicit tally went **359 → 374 assertions** (+15, all in the new file)

## Accomplishments

- **The PWA runs the extension's chat, not a second one.** `chat.js` is 570 lines of state, DOM wiring and calls into `chat_core/` — every bubble is built by `render.js`, every websocket frame is routed by `publication.js`, every socket is opened by `realtime.js`, every request goes through `createApi`. `test_pwa_chat.mjs` proves it structurally rather than by inspection: it extracts every function and class `packages/chat-core` declares and asserts no file in `app-site/app/` declares any of them.
- **The fork gate demonstrably fires.** Appending `function buildBubbleNode(){}` to `chat.js` takes the test from exit 0 to **exit 1** with the right message; removing it returns exit 0. That is the plan's own demonstration, run and recorded.
- **The banned set is chat-core's internals, not its export list.** `buildBubbleNode` and `handlePublication` are returned as part of a factory's surface, not exported by name — an export-only check would have waved through a hand-rolled bubble builder, which is the single most likely fork. The extractor is itself pinned: the test asserts it finds eight known names and at least 25 in total, so it can never pass by matching nothing.
- **No deployment fact is baked into the surface.** `chat.js` contains **zero** scheme literals, no origin (it imports `MEMORY_API_BASE` from `auth.js`), no team UUID, no quoted channel name, and no team-scoped call taking a string literal. The socket target arrives on `POST /v1/me/centrifugo-token` and is used as given (D-27-03).
- **A teammate's link cannot move this browser.** `handleOpenUrl` is called with **no opener at all**, so the PWA does not merely disable auto-open — it has no code path capable of it. The URL is re-validated with `isSafeHttpUrl` when the banner's Open button is clicked, and the shim validates a third time before `window.open`. The banner shows the sender and the **full, unshortened** URL, wrapping rather than truncating (T-22-10).
- **A nudge no longer vanishes for people without notification access.** `webPlatform.notify` returns null unless access was already granted, and this surface never prompts (D-27-05 gives that click to 27-07). The in-page banner is the fallback, and it is shown only after the URL passes validation a second time — a rejected URL produces nothing rather than a banner offering to open it.
- **Every import specifier is checked to resolve.** There is no bundler on a static host: a typo'd `from "./chat_core/realtme.js"` is a blank page in production and an error in no log anywhere. The test walks all specifiers in `app-site/app/*.js`, rejects bare specifiers outright, and resolves the rest against disk.
- **The precache list is now checked in both directions.** Every file that ships is in `SHELL`, and every `SHELL` entry exists — with `sw.js` named as the one deliberate exclusion and `/app/push.js` as the one self-pruning exemption.

## Task Commits

1. **Task 1: chat.js — boot, team picker, history, composer** — `a66a9c7` (feat)
2. **Task 2: realtime wiring + markup + styles** — `d573067` (feat)
3. **Task 3: the anti-fork contract** (TDD)
   - RED — `27560a6` (test): 14 passed, **1 failed**
   - GREEN — `c517a53` (feat): 15/15, suite 18/18

No REFACTOR commit — GREEN was a four-line prose change with nothing to clean up after it.

## Files Created/Modified

**The surface**
- `app-site/app/chat.js` (570 lines) — `bootChat({onSignedOut})`; team selector with a remembered team; `switchTeam` (teardown → clear → subscribe → members → history); `loadInitialHistory` / `loadOlderPage` with the scroll re-anchor arithmetic; `wireComposer` (Enter sends, Shift+Enter is a newline, `autoResize`, scroll-above-80px paging); `sendMessage` with optimistic render from the POST response; `handleUserPublication` + `showNudgeBanner`; `refreshNameCache`.
- `app-site/app/vendor/centrifuge.js` — 54 043 bytes, byte-identical to `chrome-extension/vendor/centrifuge.js`.

**The shell it plugs into**
- `app-site/app/index.html` — `#connection-banner`, `#nudge-banner`, `#history-loader`, `#composer-error`, and the classic `<script src="./vendor/centrifuge.js">` ahead of the module script.
- `app-site/app/app.js` — the 27-05 stub replaced: one `showSignInCard()` reachable from both a missing token and a rejected one, then `startChat()` → `bootChat()`.
- `app-site/app/app.css` — rules for the four new elements on the existing tokens; no new token, no new palette.

**Tests**
- `chrome-extension/tests/test_pwa_chat.mjs` (401 lines, 15 assertions).
- `chrome-extension/tests/test_pwa_shell.mjs` — `PENDING_SHELL_ENTRIES` reduced from six entries to one.

## Decisions Made

- **The anti-fork set is every function and class chat-core DECLARES, exported or not.** The plan's behaviour text names `buildBubbleNode`, `handlePublication`, `StreamBuffer` and `fetchJson` as the forks to catch — and of those, only `StreamBuffer` is an export. `buildBubbleNode` and `handlePublication` live inside `createRenderer` and `createPublicationRouter`, so a check built from the export list would have been inert against exactly the two duplications that matter most. The extractor strips comments first, so prose that merely names a function is not read as declaring one.
- **`sw.js` is explicitly excluded from the precache-coverage check.** The plan asks for "no missing entry, no dangling entry, excluding nothing else", but a cache-first worker that precaches itself serves the stale worker forever — which is why `firebase.json` already sends `/app/sw.js` with `no-cache`. The exclusion is a named one-element set carrying that reason, not a silent skip.
- **`/app/push.js` keeps a self-pruning exemption, in both test files.** 27-07 has not shipped it yet. The exemption uses 27-05's shape: the entry must still be in `SHELL` **and** must still be absent, so the day it lands the list is forced to shrink.
- **The publication router reads the stream buffer through a facade.** `switchTeam` replaces `state.streamBuffer` on every switch while the router is built once at boot. Passing the instance directly would append agent deltas to the *previous* team's buffer, rendering answers as empty text from the first switch onwards — silently. 27-02 hit this exact bug in the extension; the PWA does not repeat it.
- **A realtime failure is caught, not propagated.** `connectRealtime` awaits a network call (`POST /v1/me/centrifugo-token`); letting it throw would abort `bootChat` before history loads, so a token-mint hiccup would present as an empty chat. It is caught, `state.realtime` stays null, and every use site is already guarded — history renders, sending works, only live updates are missing, and the banner says so.
- **The last-read team is remembered, and re-validated.** A phone user opens this from a home-screen icon many times a day. The id is stored in localStorage and only honoured if it is still in `myTeams` — membership can be revoked between two launches, and a stale id would otherwise 403 on every history load with no way back to a working team (T-27-06-06).
- **The sign-in card stayed in `app.js`.** `chat.js` reports a dead token through an `onSignedOut` hook instead of owning sign-in markup, so there is exactly one place that knows what the card looks like and one place that mounts the Google button.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] A failed Centrifugo token mint would have taken the whole chat down**
- **Found during:** Task 2
- **Issue:** The plan's sketch calls `await connectRealtime(...)` bare. That call performs `POST /v1/me/centrifugo-token`; on any network error or non-2xx it throws, which would abort `bootChat` *before* `switchTeam` — so a realtime outage would present as a chat with no messages at all, not as a chat without live updates. The module's documented `null` fail-soft only covers a missing vendored client, not a failed mint.
- **Fix:** Wrapped in `try/catch`; `state.realtime` falls back to null, which every use site already guards, and the connection banner says live updates are off.
- **Files modified:** `app-site/app/chat.js`
- **Verification:** history, send and the composer all sit behind guarded `state.realtime` reads; `node --check` passes and the suite is green.
- **Committed in:** `d573067`

**2. [Rule 3 - Blocking] `test_pwa_shell.mjs` was already failing at HEAD**
- **Found during:** Task 1 (baseline run, before writing any code)
- **Issue:** The suite was **16/17** on the plan's base commit. 27-02 and 27-05 ran in parallel waves: 27-05's `PENDING_SHELL_ENTRIES` exempted `/app/chat_core/{render,publication,realtime}.js` as not-yet-shipped, but 27-02 shipped them in the same wave. The staleness assertion fired on the merge, exactly as designed — the exemption had outlived its reason. A red baseline also makes it impossible to attribute any later failure to this plan's work.
- **Fix:** Removed the five entries that have now shipped (27-02's three, plus `chat.js` and `vendor/centrifuge.js` which this plan lands), leaving only `/app/push.js` for 27-07.
- **Files modified:** `chrome-extension/tests/test_pwa_shell.mjs`
- **Verification:** `test_pwa_shell.mjs` 17/17; suite 17/17 files before Task 3 added the 18th.
- **Committed in:** `a66a9c7`

### One wrong-reason RED, removed before the RED commit

The first draft of `test_pwa_chat.mjs` asserted that every `app-site/app/*.js` file imports at least one module, as an inert-extractor guard. `sw.js` is a **classic worker script** and legitimately imports nothing, so that assertion failed on a non-defect. Per 27-05's lesson ("a RED failing for the wrong reason is not a RED"), the guard was moved onto the *total* specifier count across all modules and the run repeated **before** the RED was committed. The RED then failed for one real reason only.

### Gate hit fixed by rewording prose, not by loosening the gate

| Gate | Hit | Resolution |
|---|---|---|
| the D-27-03 channel-name check | a doc comment quoting the channel token while explaining the router | reworded; the comment now also states *why* the name is absent, so the next edit does not put it back |

Fourth plan in a row to hit this class (27-01 `chrome.`, 27-02 `innerHTML`, 27-05 `@import` / `xbt_token`). The gate was not narrowed in any of them.

---

**Total deviations:** 2 auto-fixed (1 missing-critical, 1 blocking) + 1 wrong-reason RED corrected pre-commit + 1 prose rewording.
**Impact on plan:** No acceptance criterion was relaxed; no scope added or dropped.

## Issues Encountered

- **Node still cannot run from inside the worktree.** `.claude/package.json` declares `{"type":"commonjs"}`, so every `.js` ESM import under `.claude/worktrees/…` fails with `Cannot use import statement outside a module` — including `node --check app-site/app/chat.js`. Every node invocation in this plan ran against a mirror of `app-site/`, `chrome-extension/`, `packages/` and `scripts/` in the scratchpad, outside `.claude/`. **The verifier must do the same**; a failure there is module resolution, not a defect. (Third plan in this phase to record it.)
- **`git` reports CRLF conversion** on the new text files, as in 27-05. Harmless: the vendored client was compared with `cmp` against the extension's copy *in the same checkout*, and the byte-identity test reads both through `fs.readFileSync`.
- **Nothing here was rendered in a browser.** Every assertion in this plan is static. `app.css` has still never been painted, and the realtime path has never opened a real socket — the deployed-origin gate (two clients, one sends, the other receives without a reload) is 27-08/09's, per the dispatch brief. Docker was down on this host and nothing in this plan needed it.

## TDD Gate Compliance

Task 3 carried `tdd="true"`. Gate sequence in `git log`: **RED** `27560a6` (`test(...)`) → **GREEN** `c517a53` (`feat(...)`). Both present, in order.

**Honest note on the RED:** the RED run reported **14 passed, 1 failed**. The one failure was real and drove the GREEN change. The 14 passing assertions lock a contract Tasks 1 and 2 had already delivered in the same plan — they are a regression fence, not a driver, which is the same shape 27-02 and 27-05 recorded for their final tasks. The assertion that genuinely *drives* nothing today is the fork check, so it was exercised deliberately: injecting `function buildBubbleNode(){}` into `chat.js` moved the file from exit 0 to **exit 1**, and removing it returned exit 0.

## Verification Evidence

| Check | Result |
|---|---|
| `node chrome-extension/tests/run_tests.mjs` | exit 0 — **18/18 test files passed** (was 16/17 at base) |
| Matching N/N assertion (no hardcoded count) | numerator == denominator == 18 |
| `node chrome-extension/tests/test_pwa_chat.mjs` | exit 0 — **15 passed, 0 failed** |
| `node chrome-extension/tests/test_pwa_shell.mjs` | exit 0 — **17 passed, 0 failed** |
| `node chrome-extension/tests/test_popup_contract.mjs` | exit 0 — **186 passed, 0 failed** (unchanged) |
| Fork gate fires: `function buildBubbleNode(){}` appended to chat.js | **exit 1**, correct message; removed → **exit 0** |
| `grep -cE 'wss?://\|https?://' app-site/app/chat.js` | **0** |
| `cmp chrome-extension/vendor/centrifuge.js app-site/app/vendor/centrifuge.js` | exit 0 — identical |
| `grep -c 'createRenderer(' / 'createApi(' / 'onAuthorClick: null'` in chat.js | **1 / 1 / 1** |
| `grep -c 'connectRealtime(' / 'createPublicationRouter('` in chat.js | **1 / 1** |
| `grep -c 'isSafeHttpUrl'` in chat.js | **3** |
| `grep -c 'alert('` in chat.js | **0** |
| `grep -c 'openDirect'` / `'catchup_stream'` in chat.js | **0 / 0** |
| `grep -cE 'catch-me-up\|catchup\|invite-code\|/boards\|clip-overlay'` in chat.js | **0** |
| `grep -c 'vendor/centrifuge.js'` in index.html, and its position | **1**, and its index precedes `type="module"` |
| all nine required ids present in index.html | yes — plus every id `chat.js` binds, cross-checked by the test |
| `node --check` chat.js / app.js / auth.js / platform_web.js | all pass (from the mirror) |
| `node scripts/sync-chat-core.mjs --check` | exit 0 — `8 file(s) × 2 target(s)`, undisturbed |
| UUID / quoted channel / literal team argument in chat.js | none |

## Known Stubs

None in this plan's own surface. Three things are absent **by the plan's own scope**, not stubbed:

- **`#btn-enable-push` is still `disabled`** — 27-07 owns it (D-27-05), and `/app/push.js` is still precached-but-absent.
- **The board, invite, people, summary and clipper surfaces do not exist here** — 27-CONTEXT defers them, and the test fails if any marker for them appears in `chat.js`.
- **Mark-read and the unread banner are not wired** — the plan explicitly excludes them; the extension keeps them.

No hardcoded empty array, mock row or placeholder string flows into the message list: an empty thread says so in one honest sentence, and a failed history load says that instead.

## Threat Flags

None. The two `mitigate` dispositions in the plan's register are implemented and checked:

| Threat | Where it landed |
|---|---|
| T-27-06-01 (tampering → DOM) | the PWA adds no rendering path of its own; the fork test enforces that structurally, and `render.js` is already `innerHTML`-free (27-02). The two nodes this surface builds by hand — the team `<option>` and the nudge line — are written with `textContent`. |
| T-27-06-02 (open_url moves the browser) | no opener is supplied, so no auto-open path exists; `isSafeHttpUrl` runs at click time and again in the shim; the banner shows the full literal URL |
| T-27-06-05 (paging loop) | `historyPaging` guard plus the 80 px threshold, both carried over verbatim |

No new network endpoint, auth path or schema. `GET /v1/teams/{id}/members` is an existing endpoint the extension already calls, and it enforces membership server-side.

## User Setup Required

None. No new dependency, no env var, no console change — `https://grooveos.app` is already an allowed CORS origin and the realtime host is already public and already served to clients.

## Next Phase Readiness

**Ready for 27-07 and 27-08/09.**

- **27-07** owns `#btn-enable-push` and `app-site/app/push.js`. When it lands, **two** lists must shrink: `PENDING_SHELL_ENTRIES` in `test_pwa_shell.mjs` *and* in `test_pwa_chat.mjs` — both fail on the day the file appears, deliberately. `push.js` will also come under this plan's fork check and its import-resolution check automatically, since both walk `app-site/app/*.js`.
- **27-08/09** own the gate that matters: the manifest and worker fetched over HTTPS from the deployed origin, sign-in against the real API with CORS, and **two clients on one team where the OTHER one receives without a reload**. Nothing in this plan proves that; it proves the code cannot be a fork, cannot hardcode the socket, and cannot open a link without a click.
- **Still open from 27-01, unchanged:** `make check-chat-core` is not wired into CI or the deploy preflight; the drift gate runs only inside the extension test suite.

## Self-Check: PASSED

All 3 created files and all 4 modified files verified present on disk; all 4 commit hashes (`a66a9c7`, `d573067`, `27560a6`, `c517a53`) verified in `git log`; no file deletions in any commit; working tree clean; `.planning/STATE.md` and `.planning/ROADMAP.md` untouched; no file outside `app-site/app/` and `chrome-extension/tests/` was modified.

---
*Phase: 27-pwa-and-push*
*Completed: 2026-08-01*
