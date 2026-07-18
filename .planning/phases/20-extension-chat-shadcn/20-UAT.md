# Phase 20 — Browser UAT record (plan 20-04 Task 2)

**Driven by:** the orchestrator (the executor has no browser tooling), 2026-07-18.
**How:** `chrome-extension/` served over local HTTP (`python -m http.server 8971`) and loaded in a real
browser via Playwright at the popup form factor (420×700). Computed styles were read from the live
document — not grepped from CSS.

## What was PROVEN (real rendering, not static inspection)

| Check | Light | Dark (`data-theme="dark"`) | Verdict |
|---|---|---|---|
| `--bg` | `#FFFFFF` | `#0A0A0A` | ✅ tokens resolve |
| `--fg` | `#0A0A0A` | `#FAFAFA` | ✅ |
| `--primary` | `#0A0A0A` | `#FAFAFA` | ✅ inverts |
| `--radius` | `0px` | `0px` | ✅ sharp corners |
| `body` computed | `rgb(255,255,255)` / `rgb(10,10,10)` | `rgb(10,10,10)` / `rgb(250,250,250)` | ✅ actually applied |
| `#btn-send` | bg `rgb(10,10,10)`, radius `0px` | bg `rgb(250,250,250)`, fg `rgb(23,23,23)` | ✅ inverts correctly |
| Theme flip back to light | restores every token | — | ✅ both directions |

Selector contract live in the DOM: `#message-list`, `#composer-input`, `#btn-send`, `#clip-overlay`,
`#teamSelector`, and a theme control — all present.
Font resolves to `system-ui` because Geist is not installed locally — this is the intended CSP-safe
"name the real face first, system fallback" behavior, not a defect.

Screenshots: `phase20-popup-light.png`, `phase20-popup-dark.png` (repo root, viewport 420×700).

## What the VISUAL check caught that the static gate could not

**1. (FIXED here) The UI told users the wrong agent alias.** The composer placeholder read
*"Message your team — use **@claude** to ask the brain"* and the empty-state read
*"mention `@claude`"* — but the server answers `AGENT_MENTION_ALIASES` (`agent`, deployed `agent,chad`).
That is a **fourth** mention vocabulary (client regex says `@grooveos…`, tests say `@claude`, server says
`@agent`) and the only one users actually read — it actively instructed people to type something that does
not summon the agent. Corrected to `@agent` (the server's documented default). **This is a stopgap**: it is
still a hardcoded alias. The durable fix — the client reading the server's alias list — is the standing
backlog item "Client/server agent-mention desync".

**2. (OPEN, cosmetic) Emoji glyphs render in colour inside a strictly monochrome design.** `↑` (send),
`⚙️` (settings), `💬` (LibreChat), `📎` (clip) are text/emoji glyphs, so the system paints them in full
colour — visible in both screenshots against the otherwise pure black/white Neutral surface. The mockup
used inline SVG. `20-CONTEXT.md` listed "emoji vs inline SVG" as Claude's Discretion, so this is a
deviation from the mockup's look, not a broken requirement. Recommended follow-up: swap these four glyphs
to inline monochrome SVG inheriting `currentColor`.

## What is STILL NOT proven (honest boundary)

The popup was loaded **standalone over HTTP**, so `chrome.*` APIs are absent (3 expected console errors)
and there is **no backend session**. Therefore these SC#2 behaviours are NOT verified by this run:

- optimistic send → Centrifugo echo dedup
- `@agent` mention → streamed agent reply (incl. the `.xb-msg-text` span fix from 20-03 that keeps the
  agent label + sources from being wiped by `handlePublication`)
- clip-to-memory landing a `memory_items` row
- saved-to-brain badge / truth-level chips against real payloads

Those need the extension **loaded unpacked in Chrome against a running stack** (the Phase-16 OSS-light
compose boots the backend). That remains the one genuine human/driven step to close SC#5 — it is
deliberately NOT claimed here. Everything above is what a real browser actually rendered.
