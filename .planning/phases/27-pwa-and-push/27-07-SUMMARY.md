---
phase: 27-pwa-and-push
plan: 07
subsystem: ui
tags: [pwa, web-push, vapid, permission-gate, service-worker, static-analysis, tdd]

# Dependency graph
requires:
  - phase: 27-pwa-and-push
    plan: 03
    provides: GET /v1/push/config (public key + an `enabled` flag derived from the private half), POST /v1/push/subscribe with UNIQUE(endpoint) ownership transfer, POST /v1/push/unsubscribe
  - phase: 27-pwa-and-push
    plan: 04
    provides: the send path, and the 404/410 prune that makes a stale server row self-cleaning
  - phase: 27-pwa-and-push
    plan: 05
    provides: the shell - index.html, app.js, sw.js and its precache list, and #btn-enable-push shipped disabled
  - phase: 27-pwa-and-push
    plan: 06
    provides: bootChat, and the two PENDING_SHELL_ENTRIES lists this plan was designed to shrink
  - phase: 27-pwa-and-push
    plan: 01
    provides: createApi - the one client that builds an Authorization header
provides:
  - app-site/app/push.js - the app's only call site for the notification permission and for taking a push subscription
  - the rotation self-heal, split across sw.js (report) and push.js (repair)
  - chrome-extension/tests/test_pwa_push.mjs - 28 assertions, 16 behavioural and 12 structural
  - a directory-walking prompt gate in test_pwa_shell.mjs that covers files nobody has written yet
affects: [27-08 deploy, 27-09 deployed-origin gate]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Refuse before you ask: every rejection path returns before the one-shot permission prompt, so a mis-configured deployment cannot spend a decision it can never use"
    - "Report, do not repair: a context without credentials (the service worker) posts a message instead of attempting an unauthenticated write"
    - "Brace-depth static gate: an API is not merely counted, its nesting is checked, so a call cannot migrate to module top level"

key-files:
  created:
    - app-site/app/push.js
    - chrome-extension/tests/test_pwa_push.mjs
  modified:
    - app-site/app/app.js
    - app-site/app/index.html
    - app-site/app/app.css
    - app-site/app/sw.js
    - chrome-extension/tests/test_pwa_shell.mjs
    - chrome-extension/tests/test_pwa_chat.mjs

key-decisions:
  - "The service worker reports a rotation and repairs nothing: a worker holds no bearer token, and it is not the one file allowed to take a subscription - the plan's own acceptance forbids both APIs in sw.js, which settles a contradiction in its sketch"
  - "A subscription whose applicationServerKey the browser will not expose is treated as MATCHING, not as rotated - treating unknown as a mismatch would churn a healthy device's endpoint on every app open"
  - "refreshPushButton makes no request at all; the network repair lives in a separate resyncPush, so 'never prompts, never writes' is true by construction rather than by review"
  - "app.js builds its own createApi closure rather than importing chat.js's - a second closure over the same two facts, not a second client, and it keeps this plan out of 27-06's file"
  - "The hint strip is shown only for states the app cannot fix itself (blocked, install_required, unsupported) and after a failed toggle - a permanent line of copy under the header for the default state is noise people learn to skip"

requirements-completed: [PUSH-01]

# Metrics
duration: 21min
completed: 2026-08-01
---

# Phase 27 Plan 07: PWA Push Opt-In Summary

**The browser's one-shot notification prompt now lives in exactly one function, reachable from exactly one click, behind a static gate that fails the suite the moment a prompt appears anywhere else or at any file's top level.**

## Performance

- **Duration:** ~21 min (first task commit 11:41:58+02:00 -> last 12:02:28+02:00)
- **Tasks:** 3 (Tasks 1 and 3 under TDD)
- **Files created:** 2 - **Files modified:** 6 - **Files deleted:** 0
- **Test suite:** 18 -> **19/19 test files**; files reporting an explicit tally went **374 -> 403 assertions** (+29: 28 in the new file, +1 in the shell test)

## Accomplishments

- **The prompt is structurally unreachable without a click, and that is proven by making it fail.** Appending `Notification.requestPermission();` at column 0 of `app.js` takes `test_pwa_push.mjs` from exit 0 to **exit 1**, and `test_pwa_shell.mjs` with it. Three more injections were run: moving push.js's single call to module top level (the brace-depth check fires), calling `enablePush` from outside the click listener (the call-site check fires), and typo'ing the rotation message in `sw.js` (the cross-file literal check fires). Every gate was exercised against a real violation rather than assumed to work.
- **Every refusal happens before the ask.** `enablePush` walks capability -> iOS-install -> already-blocked -> server config, and only then prompts. A deployment with no VAPID keypair answers `enabled: false` and the person is never asked - which matters because a permission spent on a channel that cannot deliver is spent forever. Five behaviour probes assert each refusal reaches neither `requestPermission` nor the server.
- **A rotated server key self-heals instead of failing silently.** This is the failure mode with no symptom: the browser keeps a perfectly valid subscription, the button keeps saying on, and every send is rejected because the signature no longer matches the key the push service holds. `ensureSubscription` compares `sub.options.applicationServerKey` against the key `/v1/push/config` returns, and on a mismatch drops the subscription on **both** sides before taking a fresh one. A matching subscription is deliberately not churned.
- **The two states cannot diverge in the direction that is unfixable.** A failed subscribe POST rolls the browser subscription back; `disablePush` tells the server **before** releasing the browser's subscription, asserted by index ordering in a recorded log rather than by reading the code. The asymmetry is the point: a server row with no live subscription is pruned on its first 404/410 (27-04), while a live subscription with no row is invisible from the app and cannot be cleaned up from anywhere.
- **The worker reports; it does not repair.** The plan's sketch had `pushsubscriptionchange` re-subscribing locally, but the same task's mechanical verify requires zero `pushManager.subscribe` in `sw.js`. Both cannot hold. It was resolved toward the gate: the worker posts a message and nothing else, because it holds no bearer token and is not the one file allowed to take a subscription. `push.js` runs the authenticated repair; if no window is open, the next app open does.
- **Both PENDING_SHELL_ENTRIES lists shrank, on schedule, because they fired.** `/app/push.js` landing turned the suite red in `test_pwa_shell.mjs` and `test_pwa_chat.mjs` within the same task - exactly what 27-05 designed the self-pruning exemption to do. Emptying a list makes its loop vacuous, so both files gained an explicit assertion that `/app/push.js` is now precached **and** no longer exempted; without it, deleting the entry and deleting the file would look identical to the test.
- **The shell's prompt gate no longer names files by hand.** It walked three named files, which was correct while nothing owned the prompt and would have gone quietly out of date the day a fourth file appeared. It now walks `app-site/app/*.js` plus the markup, excluding `push.js` - and asserts push.js holds exactly one of each, so the exclusion cannot be hiding an empty set.

## Task Commits

1. **Task 1: push.js - one click-gated call site, subscribe and unsubscribe** (TDD)
   - RED - `93e24a5` (test): 0 passed, **16 failed**
   - GREEN - `881d7d0` (feat): 16/16, suite 19/19
2. **Task 2: wire the button, add pushsubscriptionchange, style the states** - `f5b7c0e` (feat)
3. **Task 3: the no-prompt-on-load gate as a test** (TDD)
   - RED - `e101518` (test): 27 passed, **1 failed**
   - GREEN - `0658c71` (fix): 28/28, suite 19/19

No REFACTOR commit in either cycle - neither GREEN left anything to clean up.

## Files Created/Modified

**The opt-in**
- `app-site/app/push.js` (535 lines) - `urlBase64ToUint8Array`, `enablePush`, `disablePush`, `resyncPush`, `refreshPushButton`, `wirePushButton`; internals `isPushCapable` / `isIosLike` / `isStandalone` / `unavailableReason` / `getRegistration` / `currentSubscription` / `loadPushConfig` / `registerSubscription` / `dropSubscription` / `keyMatches` / `ensureSubscription` / `readPushState` / `paintButton`.

**The shell it plugs into**
- `app-site/app/app.js` - a `createApi` closure for push; `startChat` wires the button after `bootChat` resolves, refreshes read-only, then repairs in the background; `showSignedOut` / `showSignedIn` now own the toggle's visibility.
- `app-site/app/index.html` - `#btn-enable-push` no longer `disabled`, starts `data-state="off"` and `hidden` like every other signed-in control; `#push-hint` added beneath the header.
- `app-site/app/sw.js` - the `pushsubscriptionchange` handler; the SHELL docstring updated now that every entry ships.
- `app-site/app/app.css` - `#btn-enable-push` capped and ellipsized (the widest label is ~4x the narrowest), the on state filled like the send button, the three unavailable states muted and dashed, and `.xb-push-hint`. No new colour: `git diff` on the file yields zero hex or `rgb()` literals.

**Tests**
- `chrome-extension/tests/test_pwa_push.mjs` (889 lines, 28 assertions - 16 behavioural, 12 structural).
- `chrome-extension/tests/test_pwa_shell.mjs` - directory-walking prompt gate + the non-vacuous push.js counter-assertion; exemption list emptied. 17 -> 18 assertions.
- `chrome-extension/tests/test_pwa_chat.mjs` - exemption list emptied, staleness test given a real assertion. 15 assertions, unchanged count.

## Decisions Made

- **The service worker reports a rotation and repairs nothing.** The plan's Task 2 sketch says the worker should "re-subscribe with the SAME applicationServerKey", while the same task's `<verify>` block fails if `sw.js` contains `pushManager.subscribe` at all. Resolved toward the gate, and it is the better design on its own merits: a worker holds no bearer token, so the only call it could make to memory-api is an unauthenticated one that would be rejected; and taking a subscription there would be a second call site for an API the whole plan exists to keep singular. The worker posts `{type: "xbrain-push-rotated", oldEndpoint}` to every open window; `push.js` does the authenticated repair. A test asserts the message literal matches on both sides, because a typo there is a device that silently never repairs.
- **An unreadable `applicationServerKey` counts as matching.** `keyMatches` returns true when the browser will not expose the key the subscription was taken with. Treating unknown as a mismatch would unsubscribe and re-subscribe a healthy device on **every** app open, changing its endpoint each time and dropping anything in flight - a certain, recurring bug traded against a hypothetical one. Every browser that ships web push exposes `PushSubscription.options` (Chrome 54+, Firefox 46+, Safari 16.4+, and iOS web push requires 16.4), so the branch is defensive rather than live. Toggling off then on is the manual repair, and it is stated in the function's docstring rather than left for someone to rediscover.
- **`refreshPushButton` makes no request; `resyncPush` is a separate export.** The plan's comment sketch has `refreshPushButton` repairing a rotated subscription on the next app open. Folding a network write into the function that runs on load would have made "never prompts, never writes" a claim to be checked rather than a fact - and it would have raced with the click handler's own refresh in the test harness. Splitting them keeps the load-time function trivially safe and gives the repair an explicit, awaitable call site. `app.js` calls both, one line each, each saying why.
- **`app.js` builds its own `createApi` closure.** `chat.js` creates one but does not export it, and `chat.js` is 27-06's file, not this plan's. The alternative - exporting from `chat.js` - would have put this plan into a file it does not own for a value that is a two-line closure over `MEMORY_API_BASE` and `getToken`. It is a second closure, not a second client: `createApi` is still the only thing that builds an Authorization header (D-27-04).
- **`refreshPushButton` keeps an `api` parameter it does not use.** All four exports then take the same first argument, so no call site has to remember which one needs it. The JSDoc says plainly that the parameter is accepted and unused, and why the function deliberately makes no request - an undocumented unused parameter is a bug waiting to be "fixed" by adding a fetch.
- **The hint strip is shown only when the person has to act.** Every state writes its sentence (the `title` carries it, and assistive tech reads it), but it is only revealed for `blocked`, `install_required` and `unsupported`, plus after a failed toggle. `off` is the state almost everyone is in; a permanent line of copy under the header for it is noise people learn to skip, and the button's own label already says what it is.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] The push toggle was offered to a signed-out person**

- **Found during:** Task 3 (this is the defect its RED was driven by)
- **Issue:** `showSignedOut()` hid the team selector and the sign-out button but not `#btn-enable-push`. Every push endpoint is user-gated, so a click while signed out could only reach `/v1/push/config` without a token and come back 401 - surfacing as "Could not reach the server", which is both wrong and unactionable. The same applied to a `#push-hint` left over from a previous session.
- **Fix:** `showSignedOut` hides both; `showSignedIn` reveals the toggle and leaves the hint for `push.js` to decide; the button now starts `hidden` in the markup like every other signed-in control, so it cannot flash before boot decides.
- **Files modified:** `app-site/app/app.js`, `app-site/app/index.html`
- **Verification:** the assertion that drove it passes; 28/28 in the file, 19/19 suite.
- **Committed in:** `0658c71`

**2. [Rule 2 - Missing Critical] `navigator.serviceWorker.ready` can hang forever**

- **Found during:** Task 1
- **Issue:** `ready` never settles when no worker ever activates - a failed registration, a non-secure context, a browser with the feature turned off. `refreshPushButton` awaits it on load, so the button would freeze at whatever the markup said, permanently, with nothing in the console to explain it. The plan's interface sketch awaits it bare.
- **Fix:** `getRegistration()` races it against a 5s timer and resolves to `null`, with the timer cleared in a `finally` so nothing lingers. Every caller already handles a null registration.
- **Files modified:** `app-site/app/push.js`
- **Verification:** exercised indirectly by the whole behavioural suite (the stub resolves `ready` immediately, so the race settles on the real value); the null path is the same one the `unsupported` probes take.
- **Committed in:** `881d7d0`

**3. [Rule 2 - Missing Critical] Wiring the button twice would toggle twice per click**

- **Found during:** Task 1
- **Issue:** `startChat()` runs again after a re-sign-in (`onSignedOut` -> `mountSignIn` -> `onSignedIn` -> `startChat`), so `wirePushButton` would attach a second click listener. One click would then enable and immediately disable, which reads as the button doing nothing.
- **Fix:** an idempotence guard on `btnEl.dataset.pushWired`, covering both the click listener and the service-worker message listener.
- **Files modified:** `app-site/app/push.js`
- **Verification:** the wire probe calls `wirePushButton` twice and asserts one ask per click and exactly one `message` listener.
- **Committed in:** `881d7d0`

**4. [Rule 3 - Blocking] Both PENDING_SHELL_ENTRIES lists went red the moment push.js landed**

- **Found during:** Task 1 (GREEN)
- **Issue:** `test_pwa_shell.mjs` and `test_pwa_chat.mjs` both exempted `/app/push.js` as not-yet-shipped, with a second assertion that fails once an exempted entry exists. Creating the file fired both, taking the suite to 17/19. This is the mechanism working, not a defect - but Task 1 could not commit green without honouring it.
- **Fix:** emptied both lists (keeping the mechanism and its comment for the next precache-ahead entry) and added, in each file, a real assertion that `/app/push.js` is now in SHELL and no longer exempted - so an empty list is a checked fact rather than a vacuous loop.
- **Files modified:** `chrome-extension/tests/test_pwa_shell.mjs`, `chrome-extension/tests/test_pwa_chat.mjs`
- **Verification:** both files green; suite 19/19.
- **Committed in:** `881d7d0`

### Contradictions in the plan, resolved toward the mechanical gate

| Where | The contradiction | Resolution |
|---|---|---|
| Task 1 action vs. acceptance | The suggested module docstring spells `Notification.requestPermission` and `pushManager.subscribe`, while the acceptance requires exactly **one** occurrence of each in the file | The docstring describes both without naming either, and says so explicitly. A test asserts the stripped comments contain neither, so a plain count over the raw file stays meaningful. Fourth plan in this phase to hit this class (27-01 `chrome.`, 27-05 `@import`/`xbt_token`, 27-06 the channel name) and, as in all of them, the gate was not narrowed. |
| Task 2 action vs. verify | The sketch has `sw.js` re-subscribing on `pushsubscriptionchange`; the verify rejects `pushManager.subscribe` in `sw.js` | The worker reports and does not repair. See Decisions. |
| Task 2 acceptance vs. verify | `grep -c 'wirePushButton' app.js == 1`, but an ES module must both import and call the symbol - two lines, so `grep -c` gives 2 | The plan's own verify script requires `>= 1`, which is the satisfiable reading. The meaningful invariant - **one call site** - is asserted mechanically and holds. |

### One wrong-reason failure in each RED, fixed before the commit

- **Task 1 RED:** the fixture assertion required the example VAPID key to contain both `-` and `_`; a real key contains `-` but need not contain `_`, so it failed on the fixture rather than on the code. Replaced with a fixture chosen to contain both (`-vv8_f4` -> `[0xfa,0xfb,0xfc,0xfd,0xfe]`), pinned by a round-trip. Separately, the stub's `unsubscribe` did not clear the subscription the way a real browser does, which would have let a test pass while the button reported a device that is no longer subscribed. Both were harness defects; no assertion was weakened.
- **Task 3 RED:** the "docstring is not the gate" assertion extracted comments as `raw.replace(code, "")`, which is not a valid way to isolate them - it silently measured the whole file. Rewritten to collect the comment matches directly. Caught and fixed **before** the RED was committed, so the committed RED failed for one real reason only.

---

**Total deviations:** 4 auto-fixed (1 bug, 2 missing-critical, 1 blocking) + 3 plan contradictions resolved + 3 wrong-reason failures corrected pre-commit.
**Impact on plan:** no acceptance criterion was relaxed; no scope added or dropped.

## Issues Encountered

- **Node still cannot run from inside the worktree.** `.claude/package.json` declares `{"type":"commonjs"}`, so every `.js` ESM import under `.claude/worktrees/...` fails with `Cannot use import statement outside a module`. Every node invocation here ran against a mirror of `app-site/`, `chrome-extension/`, `packages/` and `scripts/` in the scratchpad, outside `.claude/`. **The verifier must do the same**; a failure there is module resolution, not a defect. (Fourth plan in this phase to record it.)
- **All four gate demonstrations were run in the mirror only.** No injected violation ever touched the worktree, and the working tree is clean at every commit.
- **`git` reports CRLF conversion** on the new text files, as in 27-05 and 27-06. Harmless - every comparison in the tests reads through `fs.readFileSync`.
- **Nothing here was rendered in a browser, and no real push was ever received.** Every assertion is static or runs against stubbed globals. `app.css`'s five button states have never been painted; `navigator.serviceWorker.ready`, a real `pushManager`, and an actual `pushsubscriptionchange` have never executed. Docker was down on this host and nothing in this plan needed it.

## TDD Gate Compliance

Two cycles, both complete and in order in `git log`:

| Task | RED | GREEN |
|---|---|---|
| 1 | `93e24a5` `test(...)` - 0 passed, 16 failed | `881d7d0` `feat(...)` - 16/16 |
| 3 | `e101518` `test(...)` - 27 passed, 1 failed | `0658c71` `fix(...)` - 28/28 |

**Task 1's RED is a true RED:** every one of the 16 assertions failed because `push.js` did not exist, and all 16 cover code the GREEN wrote.

**Task 3's RED is honest about its shape.** 27 of its 28 assertions passed on arrival - they lock a contract Tasks 1 and 2 had already delivered in the same plan, which makes them a regression fence rather than a driver (the same shape 27-05 and 27-06 recorded for their final tasks). One assertion failed for a real defect and drove the GREEN: the push toggle was still being offered to a signed-out person. For the assertions that drive nothing today, the gate was exercised deliberately against four injected violations - see below.

## Verification Evidence

| Check | Result |
|---|---|
| `node chrome-extension/tests/run_tests.mjs` | exit 0 - **19/19 test files passed** (18/18 at base) |
| Matching N/N assertion (no hardcoded count) | numerator == denominator == 19 |
| Assertions across files reporting a tally | **403 passed, 0 failed** (374 at base) |
| `node chrome-extension/tests/test_pwa_push.mjs` | exit 0 - **28 passed, 0 failed** |
| `node chrome-extension/tests/test_pwa_shell.mjs` | exit 0 - **18 passed, 0 failed** (was 17) |
| `node chrome-extension/tests/test_pwa_chat.mjs` | exit 0 - **15 passed, 0 failed** |
| **Gate fires:** `Notification.requestPermission();` at column 0 of app.js | push test **exit 1**, shell test **exit 1**; removed -> both **exit 0** |
| **Gate fires:** push.js's one call moved to module top level | **exit 1**, brace-depth assertion named; the module also fails to import, which is the bug itself |
| **Gate fires:** `enablePush` called outside the click listener | **exit 1**, call-site assertion named |
| **Gate fires:** rotation message literal typo'd in sw.js | **exit 1**, cross-file literal assertion named |
| `requestPermission` / `pushManager.subscribe` in push.js | **1 / 1** |
| Same two across index.html, app.js, chat.js, auth.js, platform_web.js, sw.js | **0 in every file** |
| Top-level (column 0) occurrences in push.js | **0** |
| `/v1/push/config` / `/v1/push/subscribe` / `/v1/push/unsubscribe` in push.js | **1 / 1 / 1** |
| Any other `/v1/` path in push.js | **none** |
| `userVisibleOnly: true` in push.js | **1** |
| `node --check` push.js / app.js / sw.js | all pass (from the mirror) |
| `wirePushButton` call sites in app.js | **1** (plus the import) |
| `id="btn-enable-push"` / `id="push-hint"` in index.html | **1 / 1**, and the button is not `disabled` |
| `pushsubscriptionchange` in sw.js | **1**; `requestPermission` **0**; `pushManager` **0** |
| `data-state` rules in app.css | **5** |
| New colour literal in the app.css diff | **none** |
| Accented Latin chars in push.js | **0** |
| Literal VAPID key or private-key material anywhere under app-site/app/ | **none** |
| `node scripts/sync-chat-core.mjs --check` | exit 0 - `8 file(s) x 2 target(s)`, undisturbed |
| File deletions across all five commits | **none** |
| `.planning/STATE.md`, `.planning/ROADMAP.md` | untouched |

## Known Stubs

None. Every export is wired to a real endpoint or a real browser API; no hardcoded empty value, mock row or placeholder string reaches the UI. The five button labels are all reachable states, each derived from a fact the browser reports.

Two things are absent by scope, not stubbed:

- **No push has ever been received on this device.** The gate that matters - a real subscription stored server-side, a send that the worker turns into a visible notification, and a 404/410 that prunes the row - is 27-09's, and it needs a deployed origin plus a real VAPID keypair (27-08's operator step, documented in 27-03).
- **`app.css`'s five push states have never been rendered.** Asserted structurally only, like every other style in this phase.

## Threat Flags

None. No new network endpoint, auth path or schema - the three endpoints called here all shipped in 27-03. All six `mitigate` dispositions in the plan's register are implemented and checked:

| Threat | Where it landed |
|---|---|
| T-27-07-01 prompt farming on load | one call site in `enablePush`, inside the click listener; the comment-stripped gate checks count, brace depth, call site and every other file - and was demonstrated to fire four different ways |
| T-27-07-02 claiming another device's endpoint | the endpoint comes from `sub.toJSON()` and the server binds it to the authenticated caller (27-03); `resyncPush` re-posts on open, which is what makes the ownership transfer actually happen when a second account signs in on a shared browser |
| T-27-07-03 subscription keys in transit | `p256dh`/`auth` go only to `MEMORY_API_BASE` over HTTPS through `createApi`; they are never logged and never written to storage - `push.js` touches no storage API at all |
| T-27-07-04 state divergence | failed subscribe POST rolls the browser back; disable posts before unsubscribing (asserted by log ordering); rotation re-registers from both the worker message and the next app open |
| T-27-07-05 silent enablement | `data-state`, the label, `aria-pressed` and the hint all derive from the real permission and the real subscription; the toggle is hidden entirely when signed out |
| T-27-07-06 user_agent stored server-side | accepted per the plan; capped at 256 chars, sent only on subscribe |

## User Setup Required

None in this plan. The operator step carried from 27-03 still stands and belongs to 27-08: mint a VAPID keypair into the VM `.env` as `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` plus a real `VAPID_SUBJECT`. Until then `/v1/push/config` answers `enabled: false`, the button refuses without prompting, and the hint says notifications are not configured on the server yet - which is the correct behaviour, not a failure.

## Next Phase Readiness

**Ready for 27-08 and 27-09.**

- **27-08** deploys. Two things it owns that this plan depends on: the VAPID keypair in the VM `.env`, and `/app/sw.js` continuing to be served `no-cache` (already in `firebase.json` from 27-05) - a cached worker would keep the old one without the rotation handler.
- **27-09** is the gate that matters: an installed PWA on a real device, a click that produces a real prompt, a row in `push_subscriptions`, a send that raises a visible notification, and a revoked subscription that prunes on 404/410. Nothing here proves any of that. What it proves is that the prompt cannot fire without a click, that the two subscription states cannot diverge in the unfixable direction, and that a rotated key repairs itself.
- **Still open from 27-01, unchanged:** `make check-chat-core` is not wired into CI or the deploy preflight; the drift gate runs only inside the extension test suite.
- **For a future device list:** `user_agent` is already stored per subscription (27-03), and `resyncPush` keeps the row fresh on every open, so the data is there when a UI wants it.

## Self-Check: PASSED

Both created files and all six modified files verified present on disk; all five commit hashes (`93e24a5`, `881d7d0`, `f5b7c0e`, `e101518`, `0658c71`) verified in `git log`; no file deletions in any commit; working tree clean with no untracked files; `.planning/STATE.md` and `.planning/ROADMAP.md` untouched; no file outside `app-site/app/` and `chrome-extension/tests/` was modified.

---
*Phase: 27-pwa-and-push*
*Completed: 2026-08-01*
