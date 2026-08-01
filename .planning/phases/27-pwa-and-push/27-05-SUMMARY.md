---
phase: 27-pwa-and-push
plan: 05
subsystem: ui
tags: [pwa, service-worker, web-manifest, google-oauth, platform-shim, firebase-hosting, tdd]

# Dependency graph
requires:
  - phase: 27-pwa-and-push
    plan: 01
    provides: packages/chat-core — the platform shim contract, assertPlatform, theme.js, isSafeHttpUrl, and the generated app-site/app/chat_core/ copies
  - phase: 22-push-a-link
    provides: isSafeHttpUrl — reused verbatim by the web shim's openUrl
provides:
  - app-site/app/ — the installable PWA shell (manifest, icons, service worker, stylesheet, boot module)
  - webPlatform — the browser implementation of the chat-core platform shim
  - the /join/ Google sign-in flow as an importable module, on the canonical localStorage keys
  - scripts/gen-pwa-icons.mjs — deterministic, dependency-free PNG generation
  - chrome-extension/tests/test_pwa_shell.mjs — the shell's static contract
affects: [27-06 PWA chat surface, 27-07 push opt-in, 27-08/09 deployed-origin gates]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Hand-rolled PNG encoding (zlib + CRC-32) instead of a native image dependency — one script for arm64 dev and amd64 prod"
    - "Shell-only service worker: four independent fetch guards, zero runtime caching"
    - "Precache-ahead with a self-pruning exemption list for files later plans create"

key-files:
  created:
    - app-site/app/index.html
    - app-site/app/app.js
    - app-site/app/auth.js
    - app-site/app/app.css
    - app-site/app/platform_web.js
    - app-site/app/sw.js
    - app-site/app/manifest.webmanifest
    - app-site/app/icons/icon-192.png
    - app-site/app/icons/icon-512.png
    - app-site/app/icons/icon-maskable-512.png
    - scripts/gen-pwa-icons.mjs
    - chrome-extension/tests/test_pwa_shell.mjs
  modified:
    - app-site/firebase.json

key-decisions:
  - "The SW registration lives inline in index.html, not app.js — must_haves.key_links names index.html as the `from` side, and a failed module graph must not also cost the offline shell"
  - "STORAGE_TOKEN does double duty: its value is both the localStorage key and the /v1/auth/local/login response field, so it is spelled once and read through the constant on both sides"
  - "PENDING_SHELL_ENTRIES: precache entries for not-yet-shipped files are declared explicitly, and a second assertion fails once an exempted file ships, so the exemption cannot outlive its reason"
  - "bootChat() renders a plain sentence rather than mock message rows — fake rows would make a broken build look healthy in a screenshot"

requirements-completed: [PWA-01]

# Metrics
duration: 15min
completed: 2026-08-01
---

# Phase 27 Plan 05: PWA Shell Summary

**`/app/` is an installable PWA that signs a person in with `/join/`'s exact Google flow onto the same `xbt_token` / `user_sub` keys, behind a service worker that is structurally incapable of caching an authenticated response.**

## Performance

- **Duration:** ~15 min (first task commit 10:39:35+02:00 → last 10:52:26+02:00)
- **Tasks:** 3 (Task 3 under TDD)
- **Files created:** 12 · **Files modified:** 1 · **Files deleted:** 0
- **Test suite:** 14 → **15/15 test files**, +17 assertions

## Accomplishments

- **The service worker cannot leak a previous user's data, and that is checked mechanically.** Four guards — non-GET, cross-origin, `/v1/` path, `Authorization` header — each `return` before `respondWith`. The test strips comments first and then asserts all four appear *and* that each one's index is lower than the single `respondWith`'s: a guard that only exists in prose, or that runs after the response is already claimed, fails the suite. There is also **no runtime caching at all** — nothing is written to the cache outside `install`, so a response can only be served from cache if it was in `SHELL`.
- **Sign-in is the same identity, not a lookalike.** `auth.js` is `/join/`'s flow with its discipline intact: storage is written only after both calls return something usable, so a rejected mint leaves a pre-existing session untouched; 401 and 429 share generic copy; the GIS button retries ~5s then degrades to the password form. A behaviour probe with stubbed `fetch` + `localStorage` proved all seven behaviours, including the rejected-mint case.
- **A drifted Google client id now fails the build instead of the sign-in.** The test reads `app-site/join/index.html`, extracts its client id by pattern, and asserts `auth.js` contains that exact id — the one failure mode here that produces no error in our logs.
- **The icons are real PNGs and byte-identical on every run.** `gen-pwa-icons.mjs` encodes IHDR/IDAT/IEND by hand with `zlib.deflateSync` and a CRC-32 table. No native codec, so the arm64 dev machine and the amd64 target produce the same bytes; two consecutive runs were verified to hash identically. The 512 icon was rendered and visually confirmed.
- **Nothing on the shell can raise a permission prompt.** `requestPermission` and `pushManager` appear **zero** times across `index.html`, `app.js`, `auth.js` — and zero times in `sw.js` and `platform_web.js` too. `#btn-enable-push` ships disabled; 27-07 owns the single click-gated call site (D-27-05).

## Task Commits

1. **Task 1: manifest + icons + service worker + Firebase headers** — `6552c03` (feat)
2. **Task 2: web platform shim + shadcn Neutral stylesheet** — `cc6a2f4` (feat)
3. **Task 3: index.html + auth.js + the shell contract test** (TDD)
   - RED — `a36e817` (test): 11 failing assertions, all three files absent
   - GREEN — `d5ab89f` (feat): 17/17 passing

No REFACTOR commit — the GREEN implementation needed no cleanup pass.

## Files Created/Modified

**The shell**
- `app-site/app/index.html` — manifest link, `viewport-fit=cover`, `theme-color`, the GIS script tag, the sign-in card, the chat frame and composer, and the guarded inline SW registration pinned to scope `/app/`
- `app-site/app/app.js` — boot: theme before first paint → signed-in vs signed-out → `bootChat()` stub (27-06 replaces it); a failed boot writes an explanation instead of leaving a blank page
- `app-site/app/auth.js` — `MEMORY_API_BASE`, `GOOGLE_CLIENT_ID`, `STORAGE_TOKEN`, `STORAGE_EMAIL`, `getToken`, `getUserSub`, `signOut`, `signInWithGoogleCredential`, `signInWithPassword`, `mountSignIn`
- `app-site/app/app.css` — the extension's exact token values, dark palette declared twice so the toggle wins in both directions, `100dvh` phone shell with a single scroller and safe-area padding, and a rule for all 20 classes the shared renderer emits
- `app-site/app/platform_web.js` — `webPlatform`: wrapped localStorage, `openUrl` re-validated with `isSafeHttpUrl` and opened `noopener,noreferrer`, `notify` preferring the SW registration; `assertPlatform` at module load

**Installability + caching**
- `app-site/app/manifest.webmanifest` — `id`/`start_url`/`scope` all `/app/`, absolute icon paths
- `app-site/app/sw.js` — `SHELL` precache via `Promise.allSettled`, the four fetch guards, `push` + `notificationclick`
- `app-site/app/icons/{icon-192,icon-512,icon-maskable-512}.png`
- `scripts/gen-pwa-icons.mjs`
- `app-site/firebase.json` — `/app/sw.js` served `no-cache` with `Service-Worker-Allowed: /app/`, and the manifest with `application/manifest+json`, on **both** hosting targets

**Test**
- `chrome-extension/tests/test_pwa_shell.mjs` — 17 assertions

## Decisions Made

- **The SW registration sits inline in `index.html`, not in `app.js`.** The plan's action text is ambiguous — one bullet says `app.js` registers the worker, and the registration code block is listed under `index.html` — while the acceptance requires exactly **one** registration across the two files, so it cannot live in both. `must_haves.key_links` names `app-site/app/index.html` as the `from` side of the link to `sw.js`, so `index.html` wins. It is also the better placement on its own merits: an inline script survives a module-graph failure, so a broken import does not additionally cost the offline shell.
- **`STORAGE_TOKEN` is used to read the login response field.** The acceptance requires the canonical key literal to appear exactly once in `auth.js`, but `/v1/auth/local/login` returns its token in a field of the *same name* — `/join/` itself types the literal twice for this reason. Rather than obfuscate the string or add a second constant that would decouple two things which are genuinely one name for one thing, the response is read as `data[STORAGE_TOKEN]` with a comment stating the double duty explicitly. One literal, no hidden coupling.
- **`PENDING_SHELL_ENTRIES` instead of a loose precache check.** `sw.js` deliberately precaches six files that 27-02/27-06/27-07 create, which collides with the plan's requirement that *every* `SHELL` entry name a file on disk. The test therefore exempts a **named, commented** set — and a second assertion fails if an exempted entry has since shipped, forcing it back under the check. Everything already shipped is still typo-guarded.
- **`bootChat()` renders one honest sentence, not mock rows.** A placeholder conversation would make an unfinished build look complete in a screenshot, which is exactly the failure mode the phase's gate lesson warns about.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] The new test shadowed `join` from `node:path`**
- **Found during:** Task 3, RED run
- **Issue:** `const join = readFileSync(join(REPO_ROOT, …))` shadowed the imported `join`, so the client-id assertion died with `ReferenceError: Cannot access 'join' before initialization` instead of testing anything. A RED failing for the wrong reason is not a RED.
- **Fix:** Renamed the local to `joinPage`.
- **Files modified:** `chrome-extension/tests/test_pwa_shell.mjs`
- **Verification:** The assertion now fails on the real cause (`auth.js` absent) in RED and passes in GREEN.
- **Committed in:** `a36e817` (RED commit)

**2. [Rule 1 - Bug] `serviceWorker.register` split across a line break**
- **Found during:** Task 3, GREEN run
- **Issue:** The registration was formatted as `navigator.serviceWorker\n.register(...)`, so both the test's regex and the plan's own `grep -c 'serviceWorker.register'` acceptance found zero matches — the code worked, the contract check did not.
- **Fix:** Kept `serviceWorker.register` on one line.
- **Files modified:** `app-site/app/index.html`
- **Verification:** `grep -c 'serviceWorker.register'` → `index.html:1`, `app.js:0`, sum 1.
- **Committed in:** `d5ab89f` (GREEN commit)

### Gate hits fixed by rewording prose, not by loosening the gate

Twice, an acceptance grep fired on this plan's own **comments** — the same pattern 27-01 hit with its portability gate:

| Gate | Hit | Resolution |
|---|---|---|
| `grep -cE '@import\|fonts\.googleapis\|fonts\.gstatic' app.css == 0` | a comment saying the file uses no stylesheet include | reworded, and the comment now says *why* the word is absent |
| `grep -c 'xbt_token' auth.js == 1` | a comment quoting the key value | reworded to describe the value without spelling it |

In both cases the gate was correct and the source was prose. Neither gate was weakened.

### Scope note (not an auto-fix)

The dispatch brief said "touch ONLY `app-site/*`" for parallel-safety, while the plan's own `files_modified` requires `scripts/gen-pwa-icons.mjs` and `chrome-extension/tests/test_pwa_shell.mjs` — and Task 3 states the test must live with the others so `run_tests.mjs` picks it up. Resolved the way 27-01 resolved the mirror-image case: both are **new files** on surfaces no sibling plan owns (27-02 owns `packages/chat-core/`, 27-04 owns `apps/memory-api/`). No existing `chrome-extension/` file was modified, and `packages/chat-core/` and its generated copies were left untouched — the drift gate still reports `5 file(s) × 2 target(s)` in sync.

---

**Total deviations:** 2 auto-fixed bugs (both in this plan's own new code, both caught by the RED/GREEN cycle) + 2 prose rewordings.
**Impact on plan:** No acceptance criterion was relaxed; no scope added.

## Issues Encountered

- **Node cannot run these files from inside the worktree** — `.claude/package.json` declares `{"type":"commonjs"}`, which disables ESM detection for `.js` files, so `node --check app-site/app/platform_web.js` fails with `Cannot use import statement outside a module` from `.claude/worktrees/…`. Every node run in this plan was executed against a mirror of `app-site/`, `chrome-extension/`, `packages/` and `scripts/` copied into the scratchpad, outside `.claude/`. **This affects the verifier too**: running the suite from the worktree will fail on module resolution, not on a real defect. (Same finding as 27-01.)
- **`git` reports CRLF conversion** on the new text files. Harmless here — the icons are committed as binary and the drift gate does not cover `app-site/app/*` outside `chat_core/`.

## TDD Gate Compliance

Task 3 carried `tdd="true"`. Gate sequence in `git log`: **RED** `a36e817` (`test(...)`) → **GREEN** `d5ab89f` (`feat(...)`). Both present, in order.

**Honest note on the RED:** the RED run reported *6 passed, 11 failed*. The 6 passing assertions cover the manifest and `sw.js`, which Task 1 (a non-TDD task) had already shipped in the same plan — they *lock* a delivered contract rather than drive new code. All 11 failures traced to the three files Task 3 introduces. No assertion covering new code passed before that code existed.

## Verification Evidence

| Check | Result |
|---|---|
| `node chrome-extension/tests/run_tests.mjs` | exit 0 — **15/15 test files** (was 14/14) |
| `node chrome-extension/tests/test_pwa_shell.mjs` | exit 0 — **17 passed, 0 failed** |
| `node scripts/gen-pwa-icons.mjs` twice, sha256 compared | **IDEMPOTENT OK** — all three hashes identical |
| PNG signature + IHDR dimensions, all 3 icons | 192×192, 512×512, 512×512 — real PNGs |
| `icon-512.png` rendered visually | correct: `#FAFAFA` square centred on `#0A0A0A` |
| SW guards on **comment-stripped** source | all 4 present, all 4 at an index below the single `respondWith` |
| `grep -c 'requestPermission' sw.js` | **0** |
| `grep -cE 'requestPermission\|pushManager'` over index.html + app.js + auth.js | **0** |
| `grep -c 'serviceWorker.register'` index.html / app.js | **1 / 0** (sum 1) |
| `grep -c 'xbt_token'` / `'user_sub'` in auth.js | **1 / 1** |
| Google client id in `auth.js` vs `join/index.html` | identical (`50097563098-rdh24v05dcp0ees8o4kqviuuoi5sup3n…`) |
| `Service-Worker-Allowed` occurrences in firebase.json | **2** (both hosting targets); file parses |
| `node --check` auth.js / app.js / platform_web.js | all pass (from the mirror) |
| `webPlatform` imported in node | `assertPlatform` passes at import; storage + notify degrade to `{}` / `null` with no globals |
| auth.js behaviour probe (stubbed fetch + localStorage) | **7/7** — incl. rejected mint leaves storage untouched, 401/429 generic copy, no session written on failure |
| DOM id cross-check index.html ↔ app.js | every bound id declared; 27-06/27-07 contract ids present |
| `grep -iE 'VAPID\|PRIVATE KEY\|applicationServerKey'` over `app-site/` | **no matches** |
| `node scripts/sync-chat-core.mjs --check` | exit 0 — `5 file(s) × 2 target(s)`, undisturbed |

## Known Stubs

**`bootChat()` in `app.js` shows a placeholder line instead of the chat.** This is the plan's stated design — 27-06 owns the chat surface — and it does not block this plan's goal, which is installability plus identity. It is deliberately a plain sentence ("Signed in. The team chat arrives in the next update.") rather than fabricated message rows.

**`#btn-enable-push` ships disabled.** By design (D-27-05); 27-07 wires it and owns the only click-gated permission call site.

**Six `SHELL` entries do not exist yet** (`chat.js`, `push.js`, `vendor/centrifuge.js`, `chat_core/{render,publication,realtime}.js`). `install` uses `Promise.allSettled` so they log a miss instead of failing, and `PENDING_SHELL_ENTRIES` in the test forces them back under the existence check as soon as they ship.

## Threat Flags

None. Every surface this plan adds is covered by the plan's own register (T-27-05-01 … 07); no new network endpoint, auth path or schema was introduced — `auth.js` calls the two endpoints `/join/` already calls.

## User Setup Required

None. `https://grooveos.app` is already an Authorized JavaScript origin on the Google client and already matches the API's `CORS_ALLOWED_ORIGIN_REGEX`, so no console change was needed.

## Next Phase Readiness

**Ready for 27-06 and 27-07.**

- **27-06** binds to ids already in the DOM: `#team-selector`, `#message-list`, `#chat-empty`, `#composer-input`, `#btn-send`, `#chat-scroll`. It should replace `bootChat()` in `app.js` and drop `chat.js` + `vendor/centrifuge.js` at the paths `sw.js` already precaches, then remove those entries from `PENDING_SHELL_ENTRIES`.
- **27-07** owns `#btn-enable-push` (currently `disabled`) and `push.js`. It is the **only** place allowed to reach `Notification.requestPermission` / `pushManager.subscribe`; `test_pwa_shell.mjs` asserts their absence from `index.html`, `app.js` and `auth.js`, so that test will need its file list kept honest when `push.js` lands — the assertion deliberately does not cover `push.js`.
- **Not deployed.** Per the dispatch brief, this plan does not deploy; the deployed-origin gate (manifest + SW fetched over HTTPS, sign-in against the real API from `https://grooveos.app`) is 27-08/09. `app.css` has never been rendered in a browser — only asserted statically.

## Self-Check: PASSED

All 12 created files and the 1 modified file verified present on disk; all 4 commit hashes (`6552c03`, `cc6a2f4`, `a36e817`, `d5ab89f`) verified in `git log`; no file deletions in any commit; working tree clean; `.planning/STATE.md` and `.planning/ROADMAP.md` untouched, and no file outside `app-site/`, `scripts/` and `chrome-extension/tests/` was modified.

---
*Phase: 27-pwa-and-push*
*Completed: 2026-08-01*
