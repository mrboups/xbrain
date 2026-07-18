---
phase: 20-extension-chat-shadcn
plan: 03
subsystem: ui
tags: [chrome-extension, shadcn, css-tokens, chat-ui, xss-safety, tdd]

# Dependency graph
requires:
  - phase: 20-extension-chat-shadcn (plan 20-01)
    provides: shadcn Neutral design tokens on :root + dark/light overrides + the selector-contract test
  - phase: 20-extension-chat-shadcn (plan 20-02)
    provides: header, composer, connection card and clip overlay already restyled to the tokens
provides:
  - Message thread restyled to the mockup — own (--primary) / others (--muted+--border) / agent (--card) bubbles, square avatars, mono meta, radius 0
  - Agent "agent · from your brain" label + <details> sources disclosure driven by the real metadata.memory_items count
  - Saved-to-brain badge driven by the real metadata.media indexed signal
  - Monochrome truth-level chip styling (validated filled / working outline), design-complete and future-proof
  - Day separators reconciled from DOM order (correct for initial load, pagination and live inserts)
  - Three pure, node-tested helpers: brainSummaryLabel, savedToBrainLabel, sameDay (+ dayLabel)
affects: [20-04 verification/UAT, extension chat, team_chat_agent metadata follow-up]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Provenance labels derive only from server-sent fields — absent data renders nothing, never a placeholder"
    - "Untrusted message data reaches the DOM via createElement/textContent only; innerHTML is reserved for static markup"
    - "Day separators recomputed from DOM order rather than tracked incrementally"

key-files:
  created: []
  modified:
    - chrome-extension/popup.css
    - chrome-extension/popup.js
    - chrome-extension/chat_stream.js
    - chrome-extension/tests/test_chat_stream.mjs
    - chrome-extension/tests/test_popup_contract.mjs
    - .planning/phases/20-extension-chat-shadcn/deferred-items.md

key-decisions:
  - "sameDay compares UTC calendar days (not local) so the helper is deterministic across timezones; dayLabel formats in UTC too so grouping and label always agree"
  - "Message text moved into a .xb-msg-text span and the agent stream writes there — writing to .xb-msg-bubble.textContent would have wiped the agent label and sources sharing the bubble"
  - "Source rows render only when metadata.sources exists (it does not today) — chip styling ships design-complete, zero fabricated rows or truth levels"
  - "The saved-to-brain badge keys off metadata.media because the backend only writes it once the attachment is ingested; plain text messages get no badge"
  - "syncDaySeparators reconciles from DOM order at batch boundaries instead of per-message, avoiding O(n^2) churn on a 50-message history load"

patterns-established:
  - "No-fabrication rule: a UI affordance for data the backend does not send stays styled-but-unpopulated, and the gap is logged as a backend follow-up"
  - "Contract test guards behavior, not just existence: XSS guard + fabricated-provenance guard + monochrome chip spec are asserted, not assumed"

requirements-completed: [PKG-02]

# Metrics
duration: 41min
completed: 2026-07-18
---

# Phase 20 Plan 03: Message Thread Restyle Summary

**Message thread rebuilt on shadcn Neutral tokens (own `--primary` / others `--muted` / agent `--card` bubbles, square avatars, mono meta, radius 0) with the agent brain block, saved-to-brain badge and day separators wired to real backend fields only — no backend change, no invented provenance.**

## Performance

- **Duration:** 41 min
- **Started:** 2026-07-18T00:00:00Z (approx — worktree session)
- **Completed:** 2026-07-18
- **Tasks:** 2 (Task 2 was TDD: RED → GREEN → REFACTOR)
- **Files modified:** 6

## Accomplishments

- **Message rows match the mockup on tokens.** Own messages are a `--primary` fill, others `--muted` + `--border`, the agent reply a `--card` block — all radius 0. Avatars are square (the Neutral signal); the agent avatar flips to `--primary`. Meta, timestamps and provenance are mono monochrome — the green/blue provenance tints and the hardcoded `rgba` lavender/blue washes are gone.
- **The agent block is real, not decorative.** The `<details>` summary reads "N sources from the brain" from `metadata.memory_items`, which `team_chat_agent.py` genuinely persists (verified in source, not assumed).
- **Truth-level chips ship monochrome and design-complete** — `validated` is a filled `--primary` badge, `working` an outline badge, selected via `data-level`. They populate only if `metadata.sources` arrives; today it never does, so nothing renders. Nothing is invented.
- **Saved-to-brain badge keys off the real indexed signal** (`metadata.media`) and is absent on plain text.
- **Day separators** are reconciled from DOM order, so they are correct on initial load, on prepend pagination, and on live inserts alike; they carry no `data-msg-id`, so the de-dupe never sees them.
- **Streaming survived the restyle.** Text moved into a `.xb-msg-text` span and the stream writer follows it — otherwise the first chunk would have erased the agent label and sources.
- **Contract test hardened from 66 to 75 assertions**, now including an XSS guard and a fabricated-provenance guard.

## Task Commits

1. **Task 1: Restyle message rows** — `6a724ef` (feat)
2. **Task 2 RED: failing specs for the brain-aware elements** — `0628e8e` (test)
3. **Task 2 GREEN: helpers + render glue + CSS** — `5b95571` (feat)
4. **Task 2 REFACTOR: stale layout comment** — `01fb6a7` (refactor)

## Files Created/Modified

- `chrome-extension/popup.css` — message-thread rules rebuilt on tokens: `.xb-msg` grid, own/others/agent bubbles, square `.xb-msg-avatar`, mono meta/time/provenance, `.xb-msg-agent-label`, `.xb-msg-sources`/`summary`/`.xb-msg-src`, `.xb-msg-chip[data-level]`, `.xb-msg-savetag`, `.xb-msg-daysep`, token-styled thumb/file-chip, reduced-motion guards, `--ring` focus outlines
- `chrome-extension/popup.js` — agent label, sources disclosure (`buildSourcesNode`), saved-to-brain badge, `syncDaySeparators`, `streamTextTarget`; `created_at` stamped on each row
- `chrome-extension/chat_stream.js` — `brainSummaryLabel`, `savedToBrainLabel`, `sameDay`, `dayLabel`
- `chrome-extension/tests/test_chat_stream.mjs` — 10 new assertions for the three helpers
- `chrome-extension/tests/test_popup_contract.mjs` — 7 new frozen classes, monochrome chip spec, XSS guard, fabricated-provenance guard
- `.planning/phases/20-extension-chat-shadcn/deferred-items.md` — three out-of-scope discoveries logged

## Decisions Made

- **UTC day boundaries.** The plan's `sameDay("…T09:00:00Z","…T23:00:00Z") === true` spec is only satisfiable in UTC (in UTC+2 the second timestamp is the next local day). Chose UTC and made `dayLabel` format in UTC too, so the grouping and the visible label can never disagree. Trade-off: for a user near midnight the separator follows the UTC date.
- **Dedicated text span.** Placing the agent label inside the bubble collides with `bodyEl.textContent = …` in the stream handler. Introducing `.xb-msg-text` and pointing the writer at it was the minimal fix that keeps the mockup's card structure.
- **Batch-boundary separator sync** rather than per-message, to avoid remove/re-insert churn across a 50-message history load.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Agent stream would have erased the label and sources**

- **Found during:** Task 2 (agent block wiring)
- **Issue:** The plan places the agent label and `<details>` sources inside the bubble, but `handlePublication` assigns `bodyEl.textContent` on `.xb-msg-bubble` for every `agent_stream_chunk`. The first chunk would have wiped both — a silent regression that CSS review would not catch.
- **Fix:** Added a `.xb-msg-text` span for message text and a `streamTextTarget()` resolver; the chunk and error handlers write to the span (falling back to the bubble for legacy rows). Re-anchored the streaming caret to `.xb-msg-bubble.streaming .xb-msg-text::after` so it trails the streamed characters instead of the sources block.
- **Files modified:** `chrome-extension/popup.js`, `chrome-extension/popup.css`
- **Verification:** `.xb-msg-text` added to the frozen class contract; suite 75/75; the streaming bubble always has the span (`renderAgentBubble` passes empty content and no media, so the text branch always runs).
- **Committed in:** `5b95571`

**2. [Rule 2 - Missing Critical] Day separators on the pagination path**

- **Found during:** Task 2 (day separators)
- **Issue:** The plan says insert a separator "when a message's day differs from the previous rendered message's day". A previous-message cursor is correct for appends but wrong for `loadOlderPage`, which prepends — and that loop's insertion order is already unusual, so a cursor would have produced separators in wrong places or none at all on older pages.
- **Fix:** `syncDaySeparators()` recomputes every separator from current DOM order at each batch boundary; order-independent by construction.
- **Files modified:** `chrome-extension/popup.js`
- **Verification:** Called at all four render entry points (initial history, older page before scroll re-anchor, live message, agent bubble); separators carry no `data-msg-id`, verified against the de-dupe selector.
- **Committed in:** `5b95571`

---

**Total deviations:** 2 auto-fixed (1 bug, 1 missing critical)
**Impact on plan:** Both were necessary for the planned features to actually work at runtime. No scope creep — no backend touched, no class renamed, no selector broken.

## Issues Encountered

- **Pre-existing test failures (out of scope, not fixed).** `test_chat_stream.mjs` fails 3 `detectMentionClient` assertions. Confirmed present at this plan's base commit `3a68285` with zero changes applied, so `run_tests.mjs` exits non-zero independently of this plan (10/11 files pass, same as baseline). Root cause identified and logged: `MENTION_RE` was rebranded to `@(grooveos|groove|gr|g)` while the tests still assert `@claude`/`@c`/`@cl` — the tests are stale, not the regex. Left untouched per the executor scope boundary; logged in `deferred-items.md`.
- **Backend emits no per-source data.** Confirmed by reading `team_chat.py::_serialize_message` (metadata passthrough, `media` the only rich field) and `team_chat_agent.py` (persists `memory_items` + `memory_cached`, no source rows). This is why source rows and per-source truth levels render only when `metadata.sources` appears — threat register `T-20-03-03`, disposition accept.

## Known Stubs

| Stub | File | Reason |
|------|------|--------|
| `.xb-msg-src` rows + `.xb-msg-chip[data-level]` never populate | `chrome-extension/popup.js` (`buildSourcesNode`), `chrome-extension/popup.css` | The backend does not emit `metadata.sources`. Styling is design-complete per CONTEXT; rendering is gated on the array actually existing. **Intentional and plan-mandated** (scope note + `T-20-03-03`). Resolving it requires a `team_chat_agent.py` change to persist retrieved bundle items — logged in `deferred-items.md`. The plan's goal (thread matches the mockup, no fabricated data) is met without it. |

## Threat Flags

None — no new network endpoint, auth path, file access pattern or schema change. The plan's own boundaries (untrusted message content and future `metadata.sources` labels into the DOM) are both mitigated via `createElement`/`textContent` and asserted by the contract test's XSS guard.

## Verification Performed

Run from a copy outside `.claude/`:

- `node tests/test_popup_contract.mjs` — **75 passed, 0 failed** (IDs, classes, tokens, chip spec, XSS guard, no-fabrication guard, English-only)
- `node tests/test_chat_stream.mjs` — **30 passed, 3 failed** (all 10 new assertions pass; the 3 failures are the documented pre-existing `@claude` drift)
- `node tests/run_tests.mjs` — **10/11 files pass** (same as baseline; only the pre-existing file fails)
- `node --check popup.js` — passes
- Grep gates: `.xb-msg.is-self .xb-msg-bubble` uses `var(--primary)`/`var(--primary-fg)`; `.is-user` uses `var(--muted)` + `1px solid var(--border)`; no `border-radius: 50%` on `.xb-msg-avatar`; `prefers-reduced-motion` present; `:focus-visible` on thumb + file chip; no `innerHTML` carrying `msg.content`/`delta`; no hardcoded sources array or truth level

**Not verified here (deliberately):** the popup has NOT been loaded in a real browser in this plan. Per the CONTEXT gate lesson, a "it looks right" claim from CSS inspection proves nothing — the driven-browser / human UAT that confirms the restyled thread renders correctly and that send / `@agent` streaming / clip still work against a running stack belongs to the phase verification step (plan 20-04). Treat the visual result as unconfirmed until then.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- The thread, header, composer, connection card and clip overlay are now all on the tokens — the extension restyle is code-complete and ready for the phase verification pass.
- **Carry into 20-04:** load the unpacked extension, flip the theme toggle both ways, and exercise send → optimistic → Centrifugo dedup, `@agent` streaming (confirm the label and caret behave with the new `.xb-msg-text` span), an image and a document attachment (confirm the saved-to-brain badge), and a history spanning two days (confirm separators).
- **Backend follow-ups (not blockers):** persist per-source rows + truth levels so the chip styling populates; merge late-arriving agent metadata into the streamed row so sources appear without a reload. Both in `deferred-items.md`.

## Self-Check: PASSED

All 6 modified files exist on disk; all 4 task commits (`6a724ef`, `0628e8e`, `5b95571`, `01fb6a7`) exist in git. No STATE.md or ROADMAP.md edits (parallel-executor constraint).

---
*Phase: 20-extension-chat-shadcn*
*Completed: 2026-07-18*
</content>
</invoke>
