# Phase 20: Extension Chat UI Polish (shadcn Neutral) — Context

**Gathered:** 2026-07-18 (autonomous — Option B rescope executed; design target already exists as a published mockup, so no discuss-phase)
**Status:** Ready for planning
**Source:** ROADMAP Phase 20 (RESCOPED to Option B, PKG-02 reframed) + the user's decision to finalize on the existing extension chat and just improve the UI, single group chat, no navigation. The concrete design target is `20-shadcn-mockup.html` in this phase dir.

<domain>
## Phase Boundary

Restyle the EXISTING Chrome-extension chat popup to the **shadcn "Neutral"** design system, preserving 100% of current behavior. It stays a **single group chat inside the extension, with no navigation** — no second screen, no standalone web app, no backend change.

**IN scope:** `chrome-extension/popup.css` (the `xb-*` classes — the bulk of the work), `chrome-extension/popup.html` (markup tweaks only where structure needs it, e.g. adding a theme-toggle control + an avatar element), and MINIMAL `chrome-extension/popup.js` (the light/dark toggle logic + any behavior-parity glue). An in-popup light/dark toggle. Migrating any legacy French popup strings to English on touch (CLAUDE.md rule).

**OUT of scope:** the standalone web app / extraction (DROPPED per Option B); any backend / `team_chat.py` change (it is already multi-frontend); new screens or navigation; changing the realtime/agent/clip/media *logic* (restyle only — every existing ID + event wiring in popup.js must keep working); the media-body-extraction gap (that is a separate backlog item).
</domain>

<the_current_ui>
## What exists today (chrome-extension/popup.{html,css,js})

- **popup.html (~242 lines):** `#app` → header (`#teamSelector` team dropdown, `#presenceBadge` + `#presenceCount`, `#btn-add-to-memory`, `#btn-settings` ⚙️, `#btn-open-librechat` 💬) → `#connection-card` (sign-in: `#btn-signin-github`, `#btn-connect-xbrain`, `#connect-status`, `#github-link-row`) → `#chat-body`/`#chat-scroll` (`#history-loader`, `#message-list`, `#chat-empty`) → composer (`#btn-clip` + `#file-picker`, `#composer-input`, `#btn-send`) → `#clip-overlay` dialog. Classes are `xb-*` (xb-icon-btn, xb-card, xb-chat-body, xb-chat-scroll, xb-clip-btn, xb-send-btn, xb-overlay, xb-presence, xb-team-select, …).
- **popup.css (~18 KB):** the current styling to REPLACE with the shadcn tokens.
- **popup.js (~1125 lines):** Centrifugo realtime, message rendering (own vs others, agent-with-sources, saved-to-brain, truth-level chips, media/attachments), optimistic send + dedup by id, `@`-mention streaming, history pagination, clip overlay. **This file's IDs, class hooks it toggles, and message-render markup are a hard contract — the restyle must keep every selector popup.js reads/writes working.** Read it before changing any class name it depends on; prefer restyling existing classes over renaming.

The extension popup form factor is ~400px wide. The mockup (`20-shadcn-mockup.html`) is the visual spec — reuse its exact structure for the message rows, agent block, saved-to-brain badge, composer, and header.
</the_current_ui>

<decisions>
## Design tokens (shadcn "Neutral", radius 0) — from the extracted preset

Define these as CSS custom properties on `:root` (light) + a dark override under `@media (prefers-color-scheme: dark)` AND a class/attribute the in-popup toggle stamps (so the toggle wins in both directions). Style all `xb-*` through the tokens.

**Light → Dark:**
- `--bg` #FFFFFF → #0A0A0A · `--fg` #0A0A0A → #FAFAFA
- `--card` #FFFFFF → #171717 · `--card-fg` #0A0A0A → #FAFAFA
- `--muted`/`--secondary`/`--accent` (subtle bg) #F5F5F5 → #262626 · `--muted-fg` #737373 → #A3A3A3
- `--primary` #0A0A0A → #FAFAFA · `--primary-fg` #FAFAFA → #171717
- `--border` #E5E5E5 → rgba(255,255,255,.10) · `--input` #E5E5E5 → rgba(255,255,255,.15) · `--ring` #A3A3A3 → #787878
- `--destructive` #E5322D → #FB5A57
- `--radius: 0` everywhere (sharp corners — the Neutral signal). Avatars square.
- `--sans: 'Geist','Inter',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif` · `--mono: 'Geist Mono',ui-monospace,'Cascadia Code',Consolas,monospace` (name the real faces first, system fallback — CSP-safe, no webfont fetch).

**Component mapping (match the mockup):**
- Own messages → `--primary` bg + `--primary-fg` text (black-in-light / white-in-dark, high contrast), radius 0.
- Others' messages → `--muted` bg + `--border`.
- Agent reply → a `--card` block with `--border`; a mono uppercase `agent · from your brain` label; a `<details>` sources list with truth-level chips rendered MONOCHROME (validated = filled `--primary` badge, working = outline badge) — honoring the colorless preset.
- saved-to-brain → a `--secondary` badge, mono micro-text.
- meta/timestamps/presence-count → `--mono`, `--muted-fg`.
- Header → square group avatar (`--primary`), team name/selector, presence dot, ghost icon buttons; keep `#teamSelector` (picking which team's chat is not "navigation" — it's the group picker) but style it into the header cleanly.
- Composer → bordered input (radius 0, `--ring` on focus) + a `--primary` Send button; the clip/file button stays.
- **Theme toggle:** a small control in the popup (header or settings row) that flips light/dark; init from `prefers-color-scheme`; persist to `chrome.storage.local` so it sticks across popup opens.

### Claude's Discretion
- Exactly where the theme toggle lives (header icon vs settings row) — keep it out of the message thread.
- Whether to keep the emoji glyphs (⚙️/💬/📎) or swap to inline SVG for a cleaner Neutral look — SVG is nicer but emoji is lower-risk; pick per effort.
- Motion: keep it minimal (the Neutral aesthetic + `prefers-reduced-motion`).
</decisions>

<canonical_refs>
## Canonical References — read before planning/executing
- `.planning/phases/20-extension-chat-shadcn/20-shadcn-mockup.html` — THE visual design target (message rows, agent block, badges, composer, header, the working light/dark toggle + tokens).
- `chrome-extension/popup.css` — the file to rewrite against the tokens.
- `chrome-extension/popup.html` — the markup (IDs are the popup.js contract).
- `chrome-extension/popup.js` — read to learn which classes/IDs it reads/writes and the message-render markup; DO NOT break its selectors.
- ROADMAP Phase 20 block (rescoped) — the 5 Success Criteria (the must_haves source).
- CLAUDE.md — product strings English-only (migrate legacy French popup strings on touch); the extension is a product surface.
</canonical_refs>

<specifics>
## The gate lesson applies (UI variant)
A screenshot-free "it looks right" claim proves nothing. Verification for this phase must actually LOAD the restyled popup in a real browser and confirm (a) it renders in shadcn Neutral with the toggle flipping light↔dark, (b) the existing behavior still works against a running stack (send a message → it appears optimistically → Centrifugo echo dedups; `@agent` streams; saved-to-brain + truth-level chips render; clip works). Prefer a driven-browser check (the extension can be loaded unpacked; the Phase-16 OSS-light stack boots the backend). Do NOT claim SC met from CSS inspection alone. A human UAT closes the Phase-18-deferred browser check.
</specifics>

<deferred>
- Standalone web app / extraction — DROPPED (Option B).
- app-site debrand / `XBRAIN_BASE_DOMAIN` (old SC#4 / D-01c) — stays a documented follow-up, not closed here.
- media-body extraction (media.py:111) — separate backlog item.
</deferred>

---
*Phase: 20-extension-chat-shadcn*
*Context gathered: 2026-07-18 (Option B rescope)*
