---
phase: 20-extension-chat-shadcn
plan: 01
subsystem: ui
tags: [chrome-extension, shadcn, design-tokens, css-custom-properties, theme, dark-mode, prefers-color-scheme, chrome-storage, guardrail-test, geist]

# Dependency graph
requires:
  - phase: (extension chat popup as it exists pre-20)
    provides: popup.html/popup.css/popup.js chat surface with the xb-* class + id contract
provides:
  - "Single canonical shadcn Neutral token block in popup.css (light + dark + [data-theme] overrides, exact CONTEXT hex, --radius:0, Geist stacks)"
  - "Legacy --xb-* names aliased to the new tokens so existing rules keep resolving during Plans 02/03"
  - "theme.js — pure, node-tested resolve/apply module (stored choice wins over prefers-color-scheme)"
  - "Persisted in-popup light/dark segmented toggle wired via chrome.storage.local"
  - "test_popup_contract.mjs — selector-contract + token/font/English guardrail wired into run_tests.mjs"
affects: [20-02 (restyle components via var(--…)), 20-03 (message-thread restyle + extends the contract class list), 20-04]

# Tech tracking
tech-stack:
  added: [none — vanilla CSS custom properties + a dependency-free ES module]
  patterns:
    - "Tokens as CSS custom properties: :root light + @media(prefers-color-scheme:dark) + :root[data-theme] overrides so an explicit toggle out-specifies the OS preference in both directions"
    - "Pure impure-free theme module (theme.js) mirroring settings.js: caller injects storage value + root element, keeping it node-testable"
    - "Selector-contract guardrail test that greps popup.js bindings and asserts they still resolve in popup.html/popup.css"

key-files:
  created:
    - chrome-extension/theme.js
    - chrome-extension/tests/test_theme.mjs
    - chrome-extension/tests/test_popup_contract.mjs
  modified:
    - chrome-extension/popup.css
    - chrome-extension/popup.html
    - chrome-extension/popup.js

key-decisions:
  - "popup.css is now the single source of truth for tokens; popup.html no longer defines a --xb-* :root block"
  - "Theme toggle rendered icon-only (sun/moon SVG, no text label) to fit the ~400px popup header — CONTEXT left placement/label to Claude's discretion; kept out of the message thread"
  - "--xb-accent aliased to --primary (deliberate blue->monochrome shift per CONTEXT)"
  - "Did NOT mark requirement PKG-02 complete: 20-01 lands only the token/theme foundation; the component restyle spans Plans 02/03/04"

patterns-established:
  - "Toggle-wins-both-directions: [data-theme=\"dark\"]/[data-theme=\"light\"] attribute selectors out-specify the prefers-color-scheme media block"
  - "CSP-safe fonts: name real faces first with a system fallback, never @font-face/webfont fetch (asserted by the contract test)"
  - "Frozen id/class arrays at the top of the contract test; Plan 03 extends the class list with message-thread additions"

requirements-completed: []  # PKG-02 is associated with this plan but spans Plans 02/03/04; not completed by 20-01 alone.

# Metrics
duration: 13min
completed: 2026-07-18
---

# Phase 20 Plan 01: shadcn Neutral Token Foundation + Theme Toggle Summary

**Installed the canonical shadcn "Neutral" design-token system (monochrome, radius 0, Geist) in popup.css with light/dark/[data-theme] overrides, shipped a persisted in-popup light/dark toggle backed by a pure node-tested theme.js, and added a selector-contract guardrail that fails the instant a restyle breaks a popup.js id/class/token.**

## Performance

- **Duration:** ~13 min
- **Started:** 2026-07-18T16:27:39+02:00
- **Completed:** 2026-07-18T16:40:36+02:00
- **Tasks:** 3
- **Files modified:** 6 (3 created, 3 modified)

## Accomplishments
- popup.css owns the full shadcn Neutral palette (CONTEXT hex), `--radius:0px`, and the Geist/Geist Mono stacks; the dark palette is applied under both `@media (prefers-color-scheme: dark)` and `:root[data-theme="dark"]`, plus a `:root[data-theme="light"]` block so the toggle wins over a dark OS.
- Legacy `--xb-*` names are aliased to the new tokens (`--xb-accent` -> `--primary`), so every existing rule keeps resolving while Plans 02/03 restyle components — zero renamed selectors.
- `theme.js` is a pure, dependency-free module (`resolveInitialTheme`, `applyTheme`, `THEME_STORAGE_KEY`); the header segmented toggle persists the choice to `chrome.storage.local` and initialises from `prefers-color-scheme` with the stored choice winning.
- `test_popup_contract.mjs` locks the popup.js↔popup.html id contract, the popup.js↔popup.css class contract, the token/radius/Geist definitions, the no-webfont-fetch rule, and the English-only rule — proven to bite (renaming `id="message-list"` -> `message-listX` exits 1).

## Task Commits

1. **Task 1: shadcn Neutral token block + dark/toggle overrides, legacy aliases** - `85baad6` (feat)
2. **Task 2: theme.js pure module + persisted in-popup light/dark toggle** (TDD) - `646ddd9` (test, RED) -> `a840f6c` (feat, GREEN theme.js) -> `5214f40` (feat, toggle wiring)
3. **Task 3: selector-contract + token guardrail test** - `d27c760` (test)

**Plan metadata:** committed with this SUMMARY (docs).

## Files Created/Modified
- `chrome-extension/popup.css` - Added the canonical `:root` token block + `@media(prefers-color-scheme:dark)` + `:root[data-theme="dark"|"light"]` overrides + legacy `--xb-*` aliases; added `.seg` segmented-toggle styles (pressed = `--primary`, focus-visible = `--ring`); corrected the stale header comment.
- `chrome-extension/popup.html` - Removed the inline `--xb-*` `:root` token block; `color-scheme` meta -> `light dark`; added the header `.seg` toggle (`btn-theme-light`/`btn-theme-dark`, `role="group" aria-label="Theme"`, `aria-pressed`).
- `chrome-extension/popup.js` - Imported `theme.js`; added `wireTheme()` (reads/writes `chrome.storage.local`, resolves initial theme, stamps `data-theme`, reflects `aria-pressed`, persists on click); called before first paint in `DOMContentLoaded`.
- `chrome-extension/theme.js` - New pure module.
- `chrome-extension/tests/test_theme.mjs` - 8 cases (precedence, invalid-value fallback, apply, persistence round-trip).
- `chrome-extension/tests/test_popup_contract.mjs` - 64 assertions across id/class/token/font/English contracts.

## Decisions Made
- Theme toggle is icon-only (sun/moon SVG) in the header to fit the compact popup — CONTEXT explicitly left placement + label style to Claude's discretion, and requires it be kept out of the message thread.
- `--xb-accent` -> `--primary` (and `--xb-accent-2` -> `--primary`) is the deliberate blue→monochrome shift.
- Left `border-radius: 6px`/`14px` hardcodes in existing component rules untouched (Plan 02/03 restyle territory); only token-driven radii flip to 0 now via `--xb-radius:var(--radius)`.

## Deviations from Plan
None that changed product behavior — the plan was executed as written. Two verification-method adjustments were required by the environment (documented under Issues Encountered), and one minor doc-correctness touch was made (the stale popup.css header comment claiming tokens live in popup.html was updated to reflect the new single source of truth; a `@font-face` literal was removed from a CSS comment so the webfont guard reads cleanly without comment-stripping).

## Issues Encountered

1. **Plan verify command `node --check chrome-extension/popup.js` cannot pass as written.** popup.js is an ES module loaded via `<script type="module">`, but there is no `package.json` with `"type":"module"` next to it, so `node --check` on the `.js` file errors with "Cannot use import statement outside a module" (settings.js has the same property). This is pre-existing and unrelated to Plan 20-01. Syntax was verified the correct way: `node --check --input-type=module < chrome-extension/popup.js` (popup.js and theme.js both pass). Recommend future plans use that form or rename to `.mjs`.

2. **Worktree module-resolution artifact.** This executor ran inside `.claude/worktrees/agent-…/`, and `D:/VSC/xbrain/.claude/package.json` is `{"type":"commonjs"}`. Because that file is an ancestor of the worktree's `chrome-extension/`, Node classifies every `.js` there as CommonJS, so `.mjs` tests that import `.js` modules (test_theme, test_settings, etc.) fail with "Named export not found" *inside the worktree only*. In the real repo checkout (`D:/VSC/xbrain/chrome-extension/`) there is no such ancestor, so Node's module-syntax detection treats them as ESM and the tests pass — confirmed by running `test_settings.mjs` in the shared checkout (exit 0). To verify faithfully, the extension tree was copied to a temp dir outside `.claude` and the suite run there: `test_theme.mjs` and `test_popup_contract.mjs` both exit 0, and `run_tests.mjs` reports **10/11** files passing (the two new files included). `.claude/package.json` was left untouched (out of scope, shared tooling config). `test_popup_contract.mjs` itself imports no `.js` module (pure fs reads), so it also passes when run directly inside the worktree.

3. **Pre-existing unrelated test failure (out of scope).** `chrome-extension/tests/test_chat_stream.mjs` fails 3 `detectMentionClient` assertions (`@claude` mention detection) at the plan's base commit `9a376f4`, before any 20-01 change. It is the sole reason `run_tests.mjs` is not fully green. Logged to `.planning/phases/20-extension-chat-shadcn/deferred-items.md`; not fixed (scope boundary).

## TDD Gate Compliance
Task 2 followed RED -> GREEN: `646ddd9` (test — failed with module-not-found) then `a840f6c` (theme.js — test passes 8/8). Toggle wiring landed in a follow-up `feat` commit `5214f40`. No REFACTOR commit was needed.

## Known Stubs
None. `theme.js` contains real logic; no placeholder/empty-value data flows to the UI.

## User Setup Required
None - no external service configuration required. (A human UAT that loads the restyled popup in a real browser to visually confirm the toggle + Neutral look is the CONTEXT gate-lesson item; it is deferred to the phase-level verification, not this plan.)

## Next Phase Readiness
- The token layer + persisted theme runtime + guardrail are in place. Plans 02/03 can restyle components purely by consuming `var(--…)`; the contract test will catch any broken popup.js selector from the first change.
- Plan 03 should extend `FROZEN_CLASSES` in `test_popup_contract.mjs` with the new message-thread classes it introduces (a comment in the file flags this).
- No blockers. Note the two verification-method items above for anyone re-running the node tests inside a `.claude/worktrees/` checkout.

## Self-Check: PASSED

- Created files present: theme.js, test_theme.mjs, test_popup_contract.mjs, 20-01-SUMMARY.md, deferred-items.md.
- Modified files present: popup.css, popup.html, popup.js.
- Task commits verified in git log: `85baad6`, `646ddd9`, `a840f6c`, `5214f40`, `d27c760`.
- New tests pass with real-repo module semantics (run from outside `.claude`): test_theme 8/8, test_popup_contract 64/64; run_tests discovers both (10/11 files, only pre-existing test_chat_stream fails).

---
*Phase: 20-extension-chat-shadcn*
*Completed: 2026-07-18*
