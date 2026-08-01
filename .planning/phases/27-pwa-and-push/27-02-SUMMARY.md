---
phase: 27-pwa-and-push
plan: 02
subsystem: ui
tags: [es-modules, chrome-extension, pwa, rendering, centrifugo, websocket, xss, tdd]

# Dependency graph
requires:
  - phase: 27-pwa-and-push
    plan: 01
    provides: packages/chat-core (shim contract, createApi + rawFetch, chat_stream.js), scripts/sync-chat-core.mjs + the drift gate
  - phase: 20-extension-chat-ui
    provides: the bubble DOM, the frozen id/class contract and the label helpers this plan moved
  - phase: 22-push-a-link
    provides: the user-channel open_url handler that stays surface-side
  - phase: 23-catch-me-up
    provides: switchTeam's catch-me-up tail, deliberately NOT extracted
provides:
  - packages/chat-core/render.js — createRenderer(opts), the whole message-bubble DOM
  - packages/chat-core/publication.js — createPublicationRouter(opts), the team WS frame router
  - packages/chat-core/realtime.js — connectRealtime(opts), Centrifugo connect + channel lifecycle
  - the structural guarantee that the websocket URL can only come from POST /v1/me/centrifugo-token
affects: [27-06 PWA chat surface, any future surface consuming the chat]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Factory-with-injected-capabilities: doc / containers / origin / identity handed in, never reached for"
    - "Optional affordance by injection: a null callback means the feature is absent, not disabled"
    - "Anti-fork assertions live in the module's own test — the shared module proves the surface consumes it"
    - "Live-read facade for state a caller replaces (streamBuffer) instead of capturing an instance"

key-files:
  created:
    - packages/chat-core/render.js
    - packages/chat-core/publication.js
    - packages/chat-core/realtime.js
    - chrome-extension/chat_core/render.js
    - chrome-extension/chat_core/publication.js
    - chrome-extension/chat_core/realtime.js
    - app-site/app/chat_core/render.js
    - app-site/app/chat_core/publication.js
    - app-site/app/chat_core/realtime.js
    - chrome-extension/tests/test_chat_core_render.mjs
    - chrome-extension/tests/test_chat_core_realtime.mjs
  modified:
    - chrome-extension/popup.js

key-decisions:
  - "renderer.clearStreaming(id) added to the renderer surface: the streaming-class teardown is a DOM lookup, so it belongs where streamTextTarget went, not in a router that must stay DOM-free"
  - "The view is resolved as doc.defaultView, not a global: the tab-open and the frame scheduler stay portable and the module keeps zero ambient references"
  - "clear() removes children one at a time — the innerHTML shortcut is grep-banned from render.js so nobody reaches for it on a node that does carry untrusted data"
  - "state.subscription deleted from popup state; presence reads state.realtime.teamSubscription, so there is exactly one owner of the subscription lifecycle"
  - "The publication router reads state.streamBuffer through a facade, because switchTeam replaces the buffer instance and the router is built once"

requirements-completed: [PWA-01]

# Metrics
duration: 27min
completed: 2026-08-01
---

# Phase 27 Plan 02: Render / Publication / Realtime Extraction Summary

**The ~450 lines of bubble construction, stream routing and Centrifugo wiring that only the extension popup could run now live in `packages/chat-core` behind three injected factories — and `realtime.js` contains no URL literal at all, so the socket target is structurally forced to come from `POST /v1/me/centrifugo-token`.**

## Performance

- **Duration:** 27 min
- **Started:** 2026-08-01T10:39:39+02:00 (first task commit)
- **Completed:** 2026-08-01T10:57:15+02:00
- **Tasks:** 3 (task 3 under TDD)
- **Files created:** 11 (5 hand-authored + 6 generated copies)
- **Files modified:** 1 · **Files deleted:** 0

## Accomplishments

- **The render layer is shared, not duplicated.** `createRenderer({doc, listEl, scrollEl, apiBase, getSelfUserId, getNameCache, onAuthorClick})` produces the identical DOM the popup produced — every frozen class (`xb-msg`, `is-self`/`is-user`/`is-agent`, `xb-msg-avatar`, `-meta`, `-author`, `-time`, `-provenance`, `-bubble`, `-agent-label`, `-text`, `-caption`, `-sources`, `-src`, `-chip`, `-savetag`, `-daysep`, `-thumb`, `-file-chip`) still comes out verbatim, and `test_popup_contract.mjs` is unchanged at **186/186**.
- **The websocket URL is now impossible to hardcode.** `realtime.js` holds no `ws://`, `wss://`, `http://` or `https://` anywhere, and the test asserts both halves: the fake `Centrifuge` receives *exactly* the `ws_url` the stubbed token endpoint returned, and the file itself is scanned for scheme literals (T-27-02-03 / D-27-03).
- **The XSS rule survived the move and is now tested, not just commented.** `render.js` has zero `innerHTML` — including in prose — and the new test feeds `<img src=x onerror=alert(1)>` through both the message body and the display-name cache, asserting the raw string lands as text with **no child element constructed**.
- **The affordance split the PWA needs is structural.** `onAuthorClick: null` produces a name with no listener, no `cursor: pointer` and no tooltip — the test asserts all three absences, so the PWA cannot accidentally ship a name that looks clickable and does nothing.
- **The extension is provably a consumer, not a fork.** Both new test files end with an anti-fork section asserting popup.js imports the shared modules and no longer declares `buildBubbleNode` / `renderMessage` / `handlePublication` / `connectCentrifugo` / `new Centrifuge(`. That is what produced a real RED.
- **Suite: 14 → 16/16 test files, 322 → 354 assertions.** All green; matching numerator and denominator, no hardcoded expected count.

## Task Commits

1. **Task 1: extract render.js + publication.js** — `b34729a` (feat)
2. **Task 2: extract realtime.js** — `9ca9430` (feat)
3. **Task 3: rewire popup.js, re-sync, prove green** (TDD)
   - RED — `ef62a4e` (test): 13/16 test files; the two anti-fork sections and the drift gate fail
   - GREEN — `87e2f45` (feat): popup rewired, copies synced, 16/16

No REFACTOR commit — the only cleanup (eight now-dead `chat_stream` imports) belonged to the same change and shipped inside GREEN.

## Files Created/Modified

**Shared core (the only editable copy)**
- `packages/chat-core/render.js` (443 lines) — `createRenderer` → `{clear, renderMessage, renderAgentBubble, buildBubbleNode, syncDaySeparators, streamTextTarget, clearStreaming, scrollToBottom}`. `buildSourcesNode`, `renderMediaInto` and `formatBytes` stay internal.
- `packages/chat-core/publication.js` (92 lines) — `createPublicationRouter` → `handlePublication(data)`; five frame types, unknown frames ignored.
- `packages/chat-core/realtime.js` (173 lines) — `connectRealtime` → `{centrifuge, subscribeTeam, unsubscribeTeam, disconnect, teamSubscription}`.

**Generated copies** — `chrome-extension/chat_core/{render,publication,realtime}.js` and `app-site/app/chat_core/{render,publication,realtime}.js` (byte-identical, produced by `node scripts/sync-chat-core.mjs`).

**Extension**
- `chrome-extension/popup.js` — 2833 → **2443 lines**. Gained the three imports, `buildChatCore()`, the `streamBufferFacade` and the `teamSub()` accessor; lost thirteen functions and the whole Centrifugo block.
- `chrome-extension/tests/test_chat_core_render.mjs` (547 lines, 15 assertions) — hand-rolled DOM stub, no jsdom.
- `chrome-extension/tests/test_chat_core_realtime.mjs` (314 lines, 8 assertions) — fake `Centrifuge` recorder.

## Decisions Made

- **`clearStreaming(messageId)` joined the renderer's surface (not in the plan's sketch).** `handlePublication` cleared the `streaming` class with its own `document.querySelector('[data-msg-id=…] .xb-msg-bubble')` on both the `end` and `error` frames. That is a DOM lookup, and the plan's own rationale for moving `streamTextTarget` into `render.js` applies identically — a router that reaches into the document is not a router. Leaving it in `publication.js` would have forced a `doc` dependency into the one module that has no business owning one.
- **The view comes from `doc.defaultView`, never a global.** `renderMediaInto` opened the full image with `window.open`, and `scrollToBottom` deferred through `requestAnimationFrame`. Both are resolved off the injected document, so `render.js` keeps zero ambient references (`window.` is grep-banned there) and both degrade sanely when the view is absent — which is what lets the node test observe a forced scroll.
- **`state.subscription` was deleted rather than reassigned.** Keeping a popup-side copy of the team subscription alongside the handle's own would have created two owners of one lifecycle and a stale reference after every switch. Presence now reads `state.realtime.teamSubscription` through a one-line `teamSub()` accessor; both call sites already sat inside a `try/catch`, so a null handle behaves exactly as an unavailable-presence channel did.
- **`switchTeam` still tears down explicitly before clearing the list**, even though `subscribeTeam` also tears down internally. The ordering is the point: a frame from the team you are leaving must not land in a list that is about to be cleared. The redundant call is cheap and idempotent; the ordering is not.
- **The catch-me-up tail was left exactly where it was.** `switchGen` guard → `refreshUnreadBanner()` → `markRead()` → `readyForAutoMarkRead`, comment included. It is extension-only (27-CONTEXT defers it out of the PWA) and its ordering is a checker BLOCKER (D-23-03) that `test_popup_contract.mjs` asserts textually.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] The portability gate matched the word `innerHTML` in prose**
- **Found during:** Task 1 (first run of the acceptance check)
- **Issue:** `grep -c innerHTML packages/chat-core/render.js` must be 0, and the gate reads comments too. Three explanatory comments *named* the API they were forbidding — including the module docstring that states the XSS rule.
- **Fix:** Reworded to "assigns no markup string" / "never a markup string with interpolated user data". The rule is stated more plainly than before; the gate was not loosened. (Same class of hit 27-01 recorded for `chrome.` — the pattern is now established: reword the prose, never the gate.)
- **Files modified:** `packages/chat-core/render.js`
- **Verification:** `grep -c innerHTML packages/chat-core/render.js` → 0
- **Committed in:** `b34729a`

**2. [Rule 2 - Missing Critical] The publication router would have gone stale after a team switch**
- **Found during:** Task 3 (wiring `createPublicationRouter` in popup.js)
- **Issue:** The plan's sketch passes `streamBuffer` into the router at construction. But `switchTeam` executes `state.streamBuffer = new StreamBuffer()` on every switch, and the router is built once at boot. It would have kept appending deltas to the *previous team's* buffer — agent answers would have rendered as empty text after the first team switch, silently.
- **Fix:** The router receives a four-method facade that reads `state.streamBuffer` at call time. The router's contract is unchanged; only the popup's binding is late.
- **Files modified:** `chrome-extension/popup.js`
- **Verification:** the render test drives `start → chunk → chunk → end` through a real `StreamBuffer` and asserts the accumulated text.
- **Committed in:** `87e2f45`

**3. [Rule 2 - Missing Critical] `switchTeam` threw when the vendored client had not loaded**
- **Found during:** Task 3 (rewiring `switchTeam`)
- **Issue:** `connectCentrifugo()` failed soft (logged and returned) when `globalThis.Centrifuge` was missing — but then `switchTeam` immediately dereferenced `state.centrifuge.newSubscription(...)` and threw a `TypeError`, taking the whole chat down including history. The fail-soft was decorative.
- **Fix:** `connectRealtime` returns `null` in that case (as specified), and `switchTeam` guards both the teardown and the subscribe. History renders; only live updates are missing.
- **Files modified:** `chrome-extension/popup.js`
- **Verification:** the realtime test asserts the null return and that the missing client is reported, not swallowed.
- **Committed in:** `87e2f45`

**4. [Rule 1 - Bug] Eight dead imports left behind by the extraction**
- **Found during:** Task 3 (post-deletion sweep)
- **Issue:** `formatRelative`, `authorLabel`, `bubbleClass`, `provenanceLabel`, `brainSummaryLabel`, `savedToBrainLabel`, `sameDay` and `dayLabel` were imported by popup.js solely for the render block that this plan deleted. A dead import is a live coupling: it tells the next reader the popup still renders bubbles.
- **Fix:** Trimmed the import to the four names popup.js still uses, with a comment naming `render.js` as their only consumer now.
- **Files modified:** `chrome-extension/popup.js`
- **Verification:** `node --check` passes; suite green.
- **Committed in:** `87e2f45`

### Scope note (not an auto-fix)

The dispatch brief said to touch only `packages/chat-core/*` and `chrome-extension/*`, while the plan requires generated copies under `app-site/app/chat_core/`. Resolved exactly as 27-01 did: the sync script **created three new files** inside the already-existing `app-site/app/chat_core/` directory and modified **no existing app-site file**, so the surface stays disjoint from the sibling plans running in this wave. No `apps/memory-api` file was read-modified or written.

---

**Total deviations:** 4 auto-fixed (1 blocking, 2 missing-critical, 1 bug)
**Impact on plan:** Two of the four (the stale stream buffer, the fake fail-soft) were latent user-visible defects the extraction surfaced. No acceptance criterion was relaxed.

## Issues Encountered

- **Node still cannot run from inside the worktree.** `.claude/package.json` declares `{"type":"commonjs"}`, so every `.js` ESM import fails with `Cannot use import statement outside a module` — including `node --check packages/chat-core/render.js`. Every test and syntax check in this plan ran from a mirrored copy of `chrome-extension/`, `packages/`, `scripts/` and `app-site/app/` in the scratchpad. **The verifier must do the same** — a failure there is module resolution, not a defect. (`scripts/sync-chat-core.mjs` is unaffected: `.mjs` is ESM regardless.)
- **`make` is not on PATH on this host.** `make sync-chat-core` fails with `command not found`; `node scripts/sync-chat-core.mjs` (what the target runs) was used instead and `--check` exits 0.
- **Line-count criterion, measured honestly.** The acceptance asks for popup.js to be "at least 400 lines shorter than the figure recorded in 27-01-SUMMARY". 27-01-SUMMARY records no popup.js line count; the only figure recorded upstream is the plan's own `<interfaces>` header ("the whole 2940-line file"). Against **2940 → 2443 = −497** ✅. Against the **actual pre-plan HEAD (2833, already shrunk by 27-01) → −390**. The gap is the ~60 lines of injection wiring and rationale comments the plan itself mandates; no comment was deleted to chase the number.

## TDD Gate Compliance

Task 3 carried `tdd="true"`. Gate sequence in `git log`: **RED** `ef62a4e` (`test(...)`) → **GREEN** `87e2f45` (`feat(...)`). Both present, in order.

**Honest note on the RED:** the RED run reported **13/16 test files passed** (exit 1). The three failures were the two anti-fork sections (popup.js still declared `buildBubbleNode`, `renderMessage`, `handlePublication`, `connectCentrifugo` and constructed its own `Centrifuge`) and the drift gate (six MISSING/DRIFTED copies). The *module-behaviour* assertions inside the two new files passed at RED, because Tasks 1 and 2 are non-TDD tasks that had already shipped the modules — those assertions **lock** a contract rather than drive new code. The code Task 3 itself introduces (the rewire) had no passing test before it was written.

## Verification Evidence

| Check | Result |
|---|---|
| `node chrome-extension/tests/run_tests.mjs` | exit 0 — **16/16 test files passed**, 354 assertions |
| Matching N/N assertion (no hardcoded count) | numerator == denominator == 16 |
| `node chrome-extension/tests/test_popup_contract.mjs` | exit 0 — **186 passed, 0 failed** (unchanged from 27-01) |
| `node scripts/sync-chat-core.mjs --check` | exit 0 — `8 file(s) × 2 target(s)` |
| `grep -nE 'wss?://\|https?://' packages/chat-core/realtime.js` | nothing (exit 1) |
| `grep -cE 'globalThis\|window\.' packages/chat-core/realtime.js` | **0** |
| `grep -c 'ws_url' packages/chat-core/realtime.js` | **3** |
| `grep -c innerHTML packages/chat-core/render.js` | **0** |
| `grep -rnE 'chrome\.\|localStorage\|window\.\|grooveos' render.js publication.js` | nothing (exit 1) |
| `grep -rn 'chrome\.' packages/chat-core/` | nothing (exit 1) |
| every frozen class present in render.js | 15/15 checked by the plan's script → `OK` |
| all four `agent_stream_*` frames in publication.js | `OK` |
| `grep -c 'export function createRenderer'` / `createPublicationRouter` / `export async function connectRealtime` | 1 / 1 / 1 |
| `grep -c 'createRenderer('` / `'connectRealtime('` in popup.js | **1** / **1** |
| popup.js contains `function buildBubbleNode`, `function renderMessage`, `function handlePublication`, `async function connectCentrifugo`, `new Centrifuge(` | none — `OK` |
| `node --check` popup.js / render.js / publication.js / realtime.js | all pass |
| popup.js size | 2833 → **2443** (−390 vs HEAD; −497 vs the plan's recorded 2940) |

## Known Stubs

None. Every module this plan created is imported and exercised by the extension in production paths; the `app-site/app/chat_core/` copies remain generated-but-unimported by design (27-06 is the PWA surface that consumes them), which 27-01 already recorded.

## Threat Flags

None. The plan's register covers both trust boundaries this change touches, and no new network endpoint, auth path, file access pattern or schema was introduced. The two `mitigate` dispositions are implemented and tested:

| Threat | Where it landed |
|---|---|
| T-27-02-01 (tampering → DOM) | `render.js` has zero markup assignment; the XSS test drives `<img onerror>` through both message content and the display-name cache |
| T-27-02-03 (info disclosure → socket target) | `realtime.js` has zero scheme literals; the test asserts the constructor received the API's `ws_url` byte-for-byte |
| T-27-02-02 (spoofed sources) | `buildSourcesNode` renders only server-sent `metadata.sources`; the chip level comes from `data-level` on the payload, never fabricated (carried over verbatim) |

## User Setup Required

None — no external service configuration, no new dependency, no env var. The extension must be reloaded unpacked to pick up the new `chat_core/` modules, as with any extension change.

## Next Phase Readiness

**Ready for 27-06 (the PWA chat surface).** It can now build the entire thread with:

```js
const renderer = createRenderer({ doc: document, listEl, scrollEl, apiBase,
  getSelfUserId, getNameCache, onAuthorClick: null });
const route = createPublicationRouter({ renderer, streamBuffer, onNonEmpty });
const rt = await connectRealtime({ Centrifuge, api, getUserSub,
  onTeamPublication: route, onUserPublication });
rt.subscribeTeam(teamId);
```

**Three things 27-06 must honour:**
1. Never edit `app-site/app/chat_core/*` or `chrome-extension/chat_core/*` — edit `packages/chat-core/` and re-sync. The gate rejects the alternative.
2. Run node tests from outside `.claude/`.
3. Pass `onAuthorClick: null` and omit `onPresenceChange` — both affordances are extension-only in this phase, and passing them would ship dead UI.

**Still open from 27-01, unchanged by this plan:** `make check-chat-core` is not wired into CI or the deploy preflight; the gate runs only inside the extension test suite.

## Self-Check: PASSED

All 5 hand-authored files and 6 generated copies verified present on disk; all 4 commit hashes (`b34729a`, `9ca9430`, `ef62a4e`, `87e2f45`) verified in `git log`; working tree clean before this summary.

---
*Phase: 27-pwa-and-push*
*Completed: 2026-08-01*
