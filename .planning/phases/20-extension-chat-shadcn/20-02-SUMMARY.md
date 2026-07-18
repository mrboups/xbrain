---
phase: 20-extension-chat-shadcn
plan: 02
subsystem: ui
tags: [chrome-extension, css, shadcn, design-tokens, accessibility, monochrome]

# Dependency graph
requires:
  - phase: 20-extension-chat-shadcn (plan 01)
    provides: shadcn Neutral token block on :root (--bg/--fg/--card/--muted/--primary/--border/--input/--ring/--destructive/--radius/--sans/--mono), dark + [data-theme] overrides, the persisted in-popup light/dark toggle, and the popup selector-contract test
provides:
  - Header restyled to the mockup — square --primary group avatar, token-styled #teamSelector group picker, monochrome presence dot, ghost icon buttons with --ring focus-visible
  - Composer restyled to the mockup .field/.send — radius-0 bordered pill on --input with a --ring focus-within ring, --primary Send button, ghost clip button
  - Connection card fully tokenised — GitHub sign-in no longer carries hardcoded brand hex (#24292f/#1f2328/#2f363d/#333)
  - Clip overlay restyled monochrome/radius 0 with --primary checked truth-level pills and visible focus states
affects: [20-03 message thread restyle, extension UAT / browser verification, any future extension UI work]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "All extension chrome consumes the shadcn Neutral tokens directly (var(--primary|--border|--input|--ring|--muted-fg|--radius)) rather than the legacy --xb-* aliases"
    - "Focus affordance convention: outline 2px solid var(--ring) on :focus-visible for buttons; border-color var(--ring) + 3px color-mix ring on text-entry surfaces"
    - "Status pills are monochrome — loading on --muted, success on --secondary, only error keeps --destructive"
    - "Filled primary actions dim via opacity 0.9 on hover instead of swapping to a second accent color"

key-files:
  created:
    - .planning/phases/20-extension-chat-shadcn/20-02-SUMMARY.md
  modified:
    - chrome-extension/popup.css
    - chrome-extension/popup.html

key-decisions:
  - "Kept the emoji glyphs (settings/librechat/clip) instead of swapping to inline SVG — CONTEXT explicitly left this to discretion and emoji is the lower-risk option; aria-labels/titles preserved"
  - "Filled buttons (Send, GitHub, connect, link, clip-send) hover via opacity 0.9 rather than a second color token — the monochrome preset has no distinct hover accent"
  - "Modal scrim kept at rgba(0,0,0,0.7) — a backdrop shade is not a themed surface, so it stays outside the token set"
  - "Checked truth-level radio pills fill solid --primary/--primary-fg, mirroring the mockup .chip.validated badge, so selection reads without color"
  - "Left the .xb-msg* message-thread rules (and their remaining hardcoded colors) untouched — explicitly Plan 03 scope"

patterns-established:
  - "Token-first restyle: no new hex may be introduced in extension chrome; every surface/border/text resolves through a :root token so the light/dark toggle drives it"
  - "Restyle-without-rename: popup.js ids/classes are a frozen contract enforced by tests/test_popup_contract.mjs on every change"

requirements-completed: [PKG-02]

# Metrics
duration: 21min
completed: 2026-07-18
---

# Phase 20 Plan 02: Extension Chrome — shadcn Neutral Restyle Summary

**The extension popup's app frame (header, composer, connection card, clip overlay) now renders monochrome at radius 0 entirely through the shadcn Neutral tokens — square --primary group avatar, --ring focus-within composer ring, --primary Send, and a GitHub sign-in button with zero hardcoded brand hex — with every popup.js selector intact.**

## Performance

- **Duration:** ~21 min (Task 2 execution + summary; Task 1 shipped in a prior wave)
- **Tasks:** 2/2
- **Files modified:** 2 (`chrome-extension/popup.css`, `chrome-extension/popup.html`)

## Accomplishments

- **Header (Task 1)** — emoji logo replaced by a 34px square `--primary` group avatar (`.xb-group-avatar`, radius 0); `#teamSelector` styled into the header as the group picker via `--card`/`--border`/`--radius` with a `--ring` focus; presence dot went monochrome (`currentColor` on `--muted-fg`, green glow dropped); icon buttons became 32px ghost squares with `--muted` hover and `--ring` `:focus-visible`.
- **Composer (Task 2)** — `.xb-composer-pill` is now the mockup `.field`: `--bg` inner, `1px solid var(--input)`, radius 0, and on `:focus-within` it lifts `border-color` to `--ring` plus a `0 0 0 3px color-mix(in srgb, var(--ring) 30%, transparent)` ring. `.xb-send-btn` split out of its shared 50%-radius rule into a `--primary`/`--primary-fg` square with a `--primary` border; `.xb-clip-btn` became a ghost token button. `#composer-input` moved to `--fg`/`--sans`/`--muted-fg` placeholder with its min/max-height and `.is-overflowing` behavior untouched.
- **Connection card (Task 2)** — the GitHub primary button was de-branded to `--primary`/`--primary-fg`, killing `#24292f`, `#1f2328`, `#2f363d` and the `#333` secondary hover; Google became a `--secondary`/`--border` outline; `.connect-btn` (renderEmptyTeams CTA) and `.link-btn` moved to `--primary`; `#connect-status` went monochrome with only `error` keeping `--destructive`.
- **Clip overlay (Task 2)** — card/header/footer on `--card`/`--border` at radius 0; preview panel on `--muted`; text inputs and radio pills gained `--ring` focus affordances; `.xb-radio-pill:has(input:checked)` now fills solid `--primary`/`--primary-fg` for colorless selection; `#clip-status` mirrors the connect-status monochrome treatment.
- **Contract held** — `tests/test_popup_contract.mjs` stayed green at 64/64 across both tasks (id contract, class contract, token contract, CSP-safe fonts, English-only).

## Task Commits

1. **Task 1: Restyle the header** — `c7c4f9a` (feat) — shipped and merged in a prior wave
2. **Task 2: Restyle composer, connection card, clip overlay** — `c79cf44` (feat)

## Files Created/Modified

- `chrome-extension/popup.css` — header, composer, connection-card and clip-overlay rules rewritten onto the shadcn tokens (radius 0, monochrome, `--ring` focus states); `.xb-upload-error` tokenised off its hardcoded rgba red
- `chrome-extension/popup.html` — Task 1 added the `.xb-group-avatar` span to `.xb-header-left`; Task 2 rewrote the inline `<style>` GitHub/Google sign-in rules onto tokens. **No markup, id, or class was renamed in Task 2** — the diff is confined to the inline stylesheet
- `.planning/phases/20-extension-chat-shadcn/20-02-SUMMARY.md` — this file

## Decisions Made

- **Emoji glyphs kept** over inline SVG for the icon buttons — CONTEXT left this to discretion; emoji is lower-risk and the aria-labels/titles already carry the accessible names.
- **Opacity-based hover for filled buttons.** A monochrome preset has no "second accent" to hover into, so `--primary` fills dim to `opacity: 0.9` (matching the mockup `.send:hover`) rather than swapping tokens.
- **Modal scrim left as `rgba(0,0,0,0.7)`.** A backdrop shade is not a themed surface; tokenising it would make the overlay wash out in light mode.
- **Checked radio pills fill solid `--primary`.** Mirrors the mockup's `.chip.validated` badge so truth-level selection is legible without relying on color.
- **`--input` vs `--border` split honored** — entry surfaces (composer pill, text inputs, radio pills, secondary button) use `--input`; structural chrome (card edges, dividers, header/footer rules) uses `--border`.
- **Message thread deliberately untouched.** `.xb-msg*` still carries hardcoded colors (`#4b5563`, `#7c3aed`, `#c4b5fd`, `rgba(59,130,246,…)`, `rgba(196,181,253,…)`); the plan scopes the thread to Plan 03.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Tokenised two hardcoded colors the plan did not enumerate**
- **Found during:** Task 2
- **Issue:** The plan's acceptance only asserted `#24292f == 0`, but the same connection-card block carried `#1f2328` (GitHub border), `#2f363d` (GitHub hover) and `#333` (Google secondary hover). Leaving them would have broken the monochrome/light-mode contract the task exists to deliver — the button would render a near-black border and a dark hover on a white card. The composer-adjacent `.xb-upload-error` likewise pinned `rgba(239,68,68,0.3)` instead of `--destructive`.
- **Fix:** Replaced all four with tokens (`--primary` for the GitHub border, `opacity` hover for both filled/secondary buttons, `--muted` for the Google hover, `--destructive` for the upload-error border).
- **Files modified:** `chrome-extension/popup.html`, `chrome-extension/popup.css`
- **Verification:** `grep -nE "#[0-9a-fA-F]{3,6}|rgba?\("` over `popup.html` returns nothing; over `popup.css` returns only the Plan-03 `.xb-msg*` rules and the modal scrim.
- **Committed in:** `c79cf44` (Task 2 commit)

**2. [Rule 2 - Missing Critical] Added `border-top` to `.xb-composer` and focus states the plan implied but did not spell out**
- **Found during:** Task 2
- **Issue:** The mockup's `.composer` has `border-top: 1px solid var(--border)` separating it from the thread; the existing `.xb-composer` had none, so at radius 0 the composer visually bled into the message list. Separately, the plan required "visible focus states" as a must_have but only named `:focus-visible` for `.xb-send-btn` — the clip button, both connection-card buttons, `.connect-btn`, `.link-btn`, `.xb-btn-primary/secondary` and the radio pills had no keyboard affordance.
- **Fix:** Added the composer `border-top`, plus `:focus-visible` outlines (`2px solid var(--ring)`) on every interactive control in the restyled surfaces and `:focus-within` on `.xb-radio-pill`.
- **Files modified:** `chrome-extension/popup.css`, `chrome-extension/popup.html`
- **Verification:** Contract test green; focus rules present for each control (grep-confirmed).
- **Committed in:** `c79cf44` (Task 2 commit)

**3. [Rule 1 - Bug] Tokenised the scrollbar-thumb hover (`#4a4a4a`)**
- **Found during:** Task 2
- **Issue:** `::-webkit-scrollbar-thumb:hover` was pinned to a dark-theme-only grey, which is invisible-on-invisible in light mode after the Neutral flip.
- **Fix:** Swapped to `var(--muted-fg)`.
- **Files modified:** `chrome-extension/popup.html`
- **Verification:** No hardcoded color remains anywhere in `popup.html`.
- **Committed in:** `c79cf44` (Task 2 commit)

---

**Total deviations:** 3 auto-fixed (2 missing-critical, 1 bug)
**Impact on plan:** All three serve the plan's own must_haves (monochrome chrome, radius 0, visible focus states). No scope creep — the message thread and popup.js were not touched.

## Issues Encountered

- **`.xb-clip-btn` and `.xb-send-btn` shared one rule** with `border-radius: 50%`. Restyling Send to a `--primary` square while keeping the clip button ghost required splitting the shared declaration into two independent rules. Done without touching either id, so `#btn-clip` / `#file-picker` wiring is unaffected.
- **The contract test cannot be run in place.** `.claude/package.json` forces CommonJS, which breaks the `.mjs` ESM imports. Ran it from a copy outside `.claude/` (`cp -r chrome-extension /tmp/xtest && cd /tmp/xtest && node tests/test_popup_contract.mjs`), per the plan's instruction.

## Deferred Issues

- **`tests/test_chat_stream.mjs` fails 3 assertions** (`detectMentionClient`: `@claude` matched, `@c`/`@cl` short aliases, case insensitive) — so the full suite reports **10/11 files passing**. This is **pre-existing and out of scope**: it exercises `popup.js` mention-alias detection, and `git status` confirms this plan modified only `popup.css` and `popup.html`. Flagged for the phase verifier / a follow-up; likely related to the `AGENT_MENTION_ALIASES` rebrand work (`@agent` → `@chad`), not to this restyle.

## Verification

| Check | Result |
|---|---|
| `node tests/test_popup_contract.mjs` | **64 passed, 0 failed** |
| `#24292f` across popup.css + popup.html (comments stripped) | **0** |
| Composer/clip-overlay ids (`clip-overlay`, `clip-project`, `clip-preview-mode`, `clip-preview-detail`, `clip-status`, `btn-clip-close`, `btn-clip-cancel`, `btn-clip-send`, `clip-use-defaults`, `composer-input`, `btn-send`, `btn-clip`, `file-picker`) | **all present** |
| Header ids (`teamSelector`, `presenceBadge`, `presenceCount`, `btn-add-to-memory`, `btn-settings`, `btn-open-librechat`) | **all present** |
| `.xb-composer-pill:focus-within` → `border-color: var(--ring)` | **present** (popup.css:575-576) |
| `.xb-send-btn` → `background: var(--primary)` + `color: var(--primary-fg)` | **present** (popup.css:618-619) |
| `.xb-radio-pill:has(input:checked)` → `var(--primary)` | **present** (popup.css:802-803) |
| `input[name="clipTruthLevel"]` radios | **4** |
| `.xb-group-avatar` → `--primary` + radius 0, inside `.xb-header-left` | **present** (popup.css:174-175, popup.html:86) |
| `.xb-icon-btn:focus-visible` | **present** (popup.css:240) |
| `.xb-presence-dot` monochrome (no `--xb-success`/glow) | **`background: currentColor`** |
| `node tests/run_tests.mjs` | **10/11 files** — the 1 failure is the pre-existing `test_chat_stream.mjs` (see Deferred Issues) |

**Not verified here (gate lesson applies):** per `20-CONTEXT.md` the phase's real acceptance requires *loading the restyled popup in a browser* and confirming it renders Neutral, the toggle flips light↔dark, and send/`@agent`/clip still work against a running stack. This plan's evidence is static (grep + node contract test) — it proves the tokens and the selector contract, **not** the rendered result. The browser/UAT check remains open for phase verification.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- **Ready for Plan 03 (message thread).** The chrome is done and the token vocabulary is settled; Plan 03 restyles `.xb-msg*` — which is where the remaining hardcoded colors (`#4b5563`, `#7c3aed`, `#c4b5fd`, the blue self-bubble rgba, the lavender agent wash) live, plus the mockup's bubble/agent-block/savetag/truth-chip structure.
- **Concern:** the `.xb-msg` block currently mixes a WhatsApp-style self bubble (radius `14px 14px 4px 14px`) with LibreChat-flat rows — Plan 03 will need to reconcile that with the mockup's radius-0 `.bubble`/`.agent-bubble` model.
- **Blocker for phase sign-off:** the browser UAT above, plus the pre-existing `test_chat_stream.mjs` failure.

## Self-Check: PASSED

- Files verified present: `chrome-extension/popup.css`, `chrome-extension/popup.html`, `.planning/phases/20-extension-chat-shadcn/20-02-SUMMARY.md`
- Commits verified in git: `c7c4f9a` (Task 1, header), `c79cf44` (Task 2, composer/connection card/clip overlay)

---
*Phase: 20-extension-chat-shadcn*
*Completed: 2026-07-18*
