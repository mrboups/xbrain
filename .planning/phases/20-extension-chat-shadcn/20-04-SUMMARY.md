---
phase: 20-extension-chat-shadcn
plan: 04
subsystem: testing
tags: [chrome-extension, shadcn, css-tokens, accessibility, csp, node-test, gate]

# Dependency graph
requires:
  - phase: 20-extension-chat-shadcn (plans 01, 02, 03)
    provides: the restyled popup.css / popup.html / popup.js / theme.js the gate asserts against
provides:
  - Automated mechanical gate proving the shadcn Neutral restyle (token resolution per theme block, radius 0, Geist + system fallback, zero webfont fetch, focus-visible on every interactive control, reduced-motion, popup.js selector contract, English-only)
  - Mutation-proof that the gate fails rather than skips (6 mutations, each exit 1)
affects: [phase-20 verification, extension UI regressions, future restyles of chrome-extension popup]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "CSS token RESOLUTION testing: parse each theme block's declarations and diff against the CONTEXT palette, instead of substring-matching a token anywhere in the file"
    - "Mutation-proof the gate: deliberately break each asserted rule and record the non-zero exit, so a green gate means something"

key-files:
  created: []
  modified:
    - chrome-extension/tests/test_popup_contract.mjs

key-decisions:
  - "Assert token RESOLUTION per theme block (4 blocks x 13 tokens) rather than presence-anywhere — a drifted dark override can no longer hide behind a correct light one"
  - "focus-visible assertions require an actual --ring outline; `outline: none` fails the gate"
  - "reduced-motion block must contain `animation: none`, not merely exist — an empty block suppresses nothing"
  - "jsdom DOM smoke kept strictly additive (can add failures, never mask them) so its absence is not a gate skip"
  - "Did NOT fix the pre-existing chat_stream mention-desync: its designed fix is a client/server contract change already captured in BACKLOG.md, and a quick regex patch would add a fourth hardcoded alias vocabulary"

patterns-established:
  - "SKIP=FAIL: every gate assertion reads file bytes unconditionally; conditional code is labelled ENHANCEMENT and is additive-only"

requirements-completed: []  # PKG-02 NOT closed — Task 2 (browser UAT) still open

# Metrics
duration: ~25min
completed: 2026-07-18
---

# Phase 20 Plan 04: Restyle Verification Gate Summary

**Automated mechanical gate for the shadcn Neutral restyle — 90 assertions covering per-theme-block token resolution, focus-visible/reduced-motion accessibility, zero-webfont CSP safety and square avatars — proven to go red under 6 deliberate mutations. The real-browser UAT (Task 2) is NOT done and remains open for the orchestrator.**

## Status: 1 of 2 tasks complete

| Task | Status | Owner |
|------|--------|-------|
| Task 1 — automated mechanical gate | **DONE** (`625aa29`) | this executor |
| Task 2 — real-browser UAT | **OPEN / NOT STARTED** | orchestrator (needs a real Chrome + running backend; this executor has no browser tools) |

## Performance

- **Duration:** ~25 min
- **Tasks:** 1 of 2 (Task 2 is a blocking human-verify checkpoint, deliberately not attempted)
- **Files modified:** 1

## Accomplishments

- Extended `test_popup_contract.mjs` from 43 to **90 assertions**, all green.
- **Token resolution, not token presence.** The gate now extracts the declaration bodies of all four theme blocks (`:root`, `@media (prefers-color-scheme: dark) :root`, `:root[data-theme="dark"]`, `:root[data-theme="light"]`) via brace-matching and diffs each against the CONTEXT palette (13 tokens x 4 blocks, exact hex incl. `rgba(255,255,255,.10)`).
- **Accessibility that bites.** All 6 tab-reachable controls (`.seg button`, `.xb-icon-btn`, `.xb-send-btn`, `.xb-clip-btn`, `.xb-msg-file-chip`, `.xb-team-select`) must have a `:focus-visible` rule drawing a `var(--ring)` outline — `outline: none` fails. The `prefers-reduced-motion: reduce` block must actually set `animation: none`.
- **CSP-safe fonts, counted.** Webfont fetch count must be **0** across popup.css *and* popup.html (comments stripped from both): `@font-face`, `fonts.googleapis`, `fonts.gstatic`, `@import url(`, and any remote `.woff/.woff2/.ttf/.otf/.eot` URL. Plus: `--sans` names `'Geist'` first *and* carries a system fallback (Geist is never fetched, so the fallback is what most users actually render).
- **Radius 0 where it signals.** `.xb-msg-avatar` and `.xb-group-avatar` must be `var(--radius)`-driven and not `50%`. (`.xb-presence-dot` is deliberately left round — it is a status dot, not an avatar; the plan scoped this check to the two avatars.)
- **Proved the gate can fail** — see the mutation matrix below.

## Verified Output (verbatim)

### Gate run — clean tree

Run from a copy outside `.claude/` (its `package.json` forces commonjs):

```
  PASS: token resolution: :root (base / light) resolves the full CONTEXT palette
  PASS: token resolution: @media (prefers-color-scheme: dark) :root resolves the full CONTEXT palette
  PASS: token resolution: :root[data-theme="dark"] resolves the full CONTEXT palette
  PASS: token resolution: :root[data-theme="light"] resolves the full CONTEXT palette
  PASS: a11y: .seg button:focus-visible draws a visible outline (theme toggle)
  PASS: a11y: .xb-icon-btn:focus-visible draws a visible outline (header icon buttons)
  PASS: a11y: .xb-send-btn:focus-visible draws a visible outline (send button)
  PASS: a11y: .xb-clip-btn:focus-visible draws a visible outline (clip / attach button)
  PASS: a11y: .xb-msg-file-chip:focus-visible draws a visible outline (file chip)
  PASS: a11y: .xb-team-select:focus-visible draws a visible outline (team selector)
  PASS: a11y: @media (prefers-reduced-motion: reduce) suppresses animation
  PASS: font safety: zero webfont fetches in popup.css + popup.html
  PASS: font safety: Geist named first with a system fallback (no fetch needed)
  PASS: radius 0: .xb-msg-avatar is square (no border-radius: 50%)
  PASS: radius 0: .xb-group-avatar is square (no border-radius: 50%)
  NOTE: jsdom not installed — optional DOM smoke unavailable. This is an ENHANCEMENT, not a gate assertion; the fs/regex contract above ran in full.

90 passed, 0 failed
EXIT=0
```

### Mutation matrix — proof the gate bites

Each mutation applied to a scratch copy, gate re-run, then reverted:

```
### M1 remove prefers-reduced-motion block -> EXIT=1
  FAIL: a11y: @media (prefers-reduced-motion: reduce) suppresses animation
    popup.css has no @media (prefers-reduced-motion: reduce) block

### M2 drift --primary in [data-theme=dark] -> EXIT=1
  FAIL: token resolution: :root[data-theme="dark"] resolves the full CONTEXT palette
    :root[data-theme="dark"] palette drift: --primary=#FF00FF (want #FAFAFA)

### M3 add @font-face + gstatic webfont -> EXIT=1
  FAIL: font safety: zero webfont fetches in popup.css + popup.html
    webfont fetch count must be 0 (CSP-safe) — found: popup.css: @font-face x1; popup.css: fonts.gstatic x1; popup.css: remote font file URL x1

### M4 make .xb-msg-avatar a circle (50%) -> EXIT=1
  FAIL: radius 0: .xb-msg-avatar is square (no border-radius: 50%)
    .xb-msg-avatar must not be a circle — border-radius: 50% (Neutral avatars are square)

### M5 .seg button:focus-visible outline:none -> EXIT=1
  FAIL: a11y: .seg button:focus-visible draws a visible outline (theme toggle)
    .seg button:focus-visible must draw an outline (got: outline: none;)

### M6 remove [data-theme=light] block -> EXIT=1
  FAIL: token resolution: :root[data-theme="light"] resolves the full CONTEXT palette
    popup.css has no :root[data-theme="light"] block — theme would not resolve

=== restored, re-run clean ===
clean EXIT=0
```

### Plan's inline webfont verify command

```
csp-safe fonts ok
EXIT=0
```

### Full suite — `node tests/run_tests.mjs`

```
--- test_chat_stream.mjs ---
  FAIL: detectMentionClient: @claude matched
  FAIL: detectMentionClient: @c and @cl short aliases
  FAIL: detectMentionClient: case insensitive
30 passed, 3 failed
FAIL: test_chat_stream.mjs exited 1
--- test_claude_ai_client.mjs ---   3 passed, 0 failed
--- test_librechat_autofill.mjs --- 7 passed, 0 failed
--- test_manifest_key.mjs ---       3 passed, 0 failed
--- test_onboarding.mjs ---        10 passed, 0 failed
--- test_openai_to_claudeai.mjs --- (all passed)
--- test_popup_contract.mjs ---    90 passed, 0 failed
--- test_settings.mjs ---           7 passed, 0 failed
--- test_theme.mjs ---              8 passed, 0 failed
--- test_translate_sse.mjs ---     (all passed)
--- test_ws_keepalive.mjs ---       6 passed, 0 failed

=== 10/11 test files passed ===
SUITE EXIT=1
```

## Acceptance Criteria — honest status

| Criterion | Status |
|-----------|--------|
| Contract test asserts `:focus-visible`, `prefers-reduced-motion`, `prefers-color-scheme: dark`, `[data-theme="dark"]` | **MET** |
| Verified by breaking a rule in a scratch copy → exits 1 | **MET** (6 mutations, all exit 1; documented above) |
| Webfont guard passes | **MET** (gate + plan's inline command) |
| Every assertion prints PASS/FAIL, no conditional skip except the jsdom ENHANCEMENT | **MET** |
| `node chrome-extension/tests/run_tests.mjs` exits 0 with every `test_*.mjs` green **including chat_stream** | **NOT MET — pre-existing, out of scope.** See below. |

### Why the suite is not green (not caused by this plan)

`test_chat_stream.mjs` fails 3 `detectMentionClient` assertions. This is **pre-existing and already documented**: the baseline run at HEAD `f8e08b9`, *before* any edit in this plan, showed the identical `10/11` with the identical 3 failures. The HEAD commit itself is `docs(backlog): client/server agent-mention desync found by the Phase 20 restyle`, which root-causes it: `chat_stream.js` matches `@grooveos|groove|gr|g`, the server answers `AGENT_MENTION_ALIASES` (`agent,chad`), and the test asserts `@claude` — three vocabularies.

Not fixed here, deliberately:
- **Out of this plan's file scope** (Task 1 declares `test_popup_contract.mjs` only) and unrelated to the restyle.
- The BACKLOG's designed fix is *"make the client stop hardcoding aliases — have it read the alias list the server already exposes"* — a client/server contract change (**Rule 4, architectural**), not a test tweak.
- Patching the regex to `agent|chad` would create a **fourth** hardcoded vocabulary, which is precisely what the backlog item exists to eliminate.

The plan's acceptance criterion named `chat_stream` presumably assuming it was green. It was not, and this is recorded rather than papered over.

## Task Commits

1. **Task 1: Finalise the automated mechanical gate** — `625aa29` (test)

Task 2 has no commit — it was not executed.

## Files Created/Modified

- `chrome-extension/tests/test_popup_contract.mjs` — +230 lines: section 5 (token resolution per theme block, focus-visible, reduced-motion, webfont count, square avatars) plus the additive jsdom DOM smoke.

## Decisions Made

- **Token resolution over token presence.** Section 3's existing `popupCss.includes("--bg:#FFFFFF")` proves a string exists somewhere. Section 5 parses each theme block and diffs the whole palette, so a corrupted dark block cannot pass on the strength of a correct light one. Both kept — section 3 is the cheap canary, section 5 the real check.
- **`.xb-presence-dot` stays round.** It is a 6px status dot, not an avatar. The plan scoped the radius check to `.xb-msg-avatar` / `.xb-group-avatar`; a blanket `50%` ban would have failed a legitimate rule.
- **`--card-fg` added to the asserted palette** (CONTEXT lists it; the pre-existing `TOKENS_LIGHT` map omitted it). `--accent` is *not* asserted — CONTEXT mentions it descriptively but popup.css never defines it, and inventing a requirement is not the gate's job.

## Deviations from Plan

None requiring an auto-fix. No Rule 1/2/3 fixes were needed — the restyle from Plans 01-03 satisfied every assertion on the first run.

One **deliberate non-action** (documented above, not a deviation in the auto-fix sense): the pre-existing `chat_stream.js` mention desync was left alone as out-of-scope and architecturally Rule-4-shaped.

## Issues Encountered

- **Worktree was behind.** On start, HEAD sat at `c4492a1`, an ancestor of the declared base `f8e08b9` — the Phase 20 directory did not exist yet. Resolved with the prescribed `git reset --hard f8e08b9` from the branch-check step. No work lost (nothing was committed on the branch).
- **Node tests cannot run in-tree.** `.claude/package.json` forces commonjs, so the suite was run from a copy in the scratchpad, per the plan constraint.

## Known Stubs

None. No placeholder values, empty returns or TODO markers were introduced.

## OPEN WORK — Task 2: real-browser UAT (handed to the orchestrator)

**This is the blocking checkpoint and it is NOT satisfied.** `20-UAT.md` does **not** exist. No browser was opened; this executor has no browser tooling. Nothing in this summary should be read as evidence about how the popup *renders*.

Per the plan and 20-CONTEXT's gate lesson: **a screenshot-free "looks right" is rejected. SKIP = FAIL.** Task 1 proves the CSS/markup *contract*; it proves nothing about pixels, paint order, or live behavior.

The orchestrator must drive, against a running backend (start the GCP VM per the runbook, or boot the Phase-16 OSS-light compose):

1. Load unpacked `D:/VSC/xbrain/chrome-extension` via `chrome://extensions` (Developer mode).
2. Confirm the popup renders shadcn Neutral — monochrome, sharp corners, Geist-ish type, square header avatar. Must not look like the old blue/rounded theme.
3. Theme toggle flips the whole surface light↔dark, and the choice **persists** across close/reopen (`chrome.storage.local`).
4. Send a message — optimistic `--primary` bubble appears immediately; the Centrifugo echo does **not** duplicate it.
5. `@agent` / `@chad` — typing indicator, then a streamed reply in the `--card` agent block with the mono `agent · from your brain` label and, if brain context was used, the sources summary. *(Note: the mention desync above means the client-side optimistic affordance may not fire for these aliases even though the server answers — worth observing during UAT.)*
6. Attach a file — media chip/thumbnail renders, "saved to brain" badge appears, clip overlay opens monochrome and sends.
7. Tab through header + composer — focus outlines visibly render; OS reduced-motion suppresses the caret/rise animation.

Record per-step PASS/FAIL **with evidence** (at minimum: the render, the toggle flip, an `@agent` reply) into `.planning/phases/20-extension-chat-shadcn/20-UAT.md`.

## Next Phase Readiness

- **PKG-02 is NOT closed.** It needs Task 2. `requirements-completed` is intentionally empty.
- **The Phase-18-deferred browser UAT is NOT closed.** It closes only when `20-UAT.md` records a real render.
- Mechanically, the restyle is locked in: any future regression to the tokens, avatars, focus states, motion or font safety now turns the suite red.
- STATE.md and ROADMAP.md were **not** touched (parallel-executor rule) — the orchestrator owns those updates.

## Self-Check: PASSED

- `chrome-extension/tests/test_popup_contract.mjs` — FOUND
- `.planning/phases/20-extension-chat-shadcn/20-04-SUMMARY.md` — FOUND
- Commit `625aa29` — FOUND in git log
- `20-UAT.md` — correctly ABSENT (Task 2 not executed; no fabricated UAT artifact)

---
*Phase: 20-extension-chat-shadcn*
*Task 1 completed: 2026-07-18 — Task 2 OPEN*
