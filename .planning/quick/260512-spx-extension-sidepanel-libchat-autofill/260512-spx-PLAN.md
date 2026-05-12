---
title: Extension side panel + LibreChat API key auto-fill
quick_id: 260512-spx
slug: extension-sidepanel-libchat-autofill
mode: validate
date: 2026-05-12
must_haves:
  truths:
    - "Right-click → Options opens a Settings page with two toggles: Open as side panel, Auto-fill API key in LibreChat. Both default ON."
    - "Toggling 'Open as side panel' makes the extension open in Chrome's side panel (Chrome 114+) instead of a floating popup. Side panel stays open during Google sign-in."
    - "When the user opens chat.grooveos.app and selects the Claude Pro/Max endpoint, the API key field is auto-filled from chrome.storage.local.xbt_token — no copy-paste."
    - "Auto-fill is gated by the autoFillLibreChat setting (OFF → content script no-ops)."
    - "Auto-fill never targets the wrong endpoint (Anthropic, OpenAI, xAI, Claude Reasoning) — anchored by the surrounding card text containing 'Claude Pro/Max'."
  artifacts:
    - chrome-extension/settings.js (new)
    - chrome-extension/options.html (new)
    - chrome-extension/options.js (new)
    - chrome-extension/manifest.json (sidePanel + options_ui + chat.grooveos.app)
    - chrome-extension/background.js (applyPanelBehavior)
    - chrome-extension/librechat_autofill.js (new content script)
    - chrome-extension/tests/test_settings.mjs (new)
    - chrome-extension/tests/test_librechat_autofill.mjs (new)
  key_links:
    - https://developer.chrome.com/docs/extensions/reference/api/sidePanel
    - .planning/quick/260512-eo1-extension-onboarding/260512-eo1-SUMMARY.md (parent task)
---

# Quick Task 260512-spx — Extension side panel + LibreChat auto-fill

## Goal

Two UX upgrades requested mid-UAT of 260512-eo1:

1. The extension popup closed during Google sign-in, forcing a second Connect click. Move to Chrome's side panel API (Chrome 114+) so the UI stays alive across OAuth windows.
2. The user still has to manually paste the xbt_ token into LibreChat's API key dialog. Auto-fill it from chrome.storage.local on `chat.grooveos.app`.

Both gated by toggleable settings (default ON), accessible via the standard extension Options page.

## Decisions (locked)

| Decision | Choice | Why |
|----------|--------|-----|
| Settings storage tier | `chrome.storage.sync` | Settings should sync across the user's devices via Chrome profile. The bridge token stays in `chrome.storage.local` (never synced). |
| Defaults | Both toggles ON | User explicitly asked for the new behavior; opt-out preserves backwards compat for power users. |
| Side panel API | `chrome.sidePanel.setPanelBehavior({openPanelOnActionClick})` | Same `popup.html` reused as the panel's `default_path` — no UI duplication. |
| Auto-fill heuristic | Match input + anchor card text "Claude Pro/Max" | Selector-free; survives LibreChat version bumps. Anchor prevents leaking the token to other endpoints' dialogs. |
| Auto-fill one-shot | Yes — set `filled = true` after first match, disconnect observer | User can clear/edit; we don't fight them. |
| React setter | Yes — use `Object.getOwnPropertyDescriptor(proto, "value").set.call(input, value)` then dispatch `input` event | Plain `input.value = x` is invisible to React's controlled component. Standard pattern for content scripts injecting into React apps. |

## Tasks

### Task A — Settings module + Options page

**files**: `chrome-extension/settings.js` (new), `chrome-extension/options.html` (new), `chrome-extension/options.js` (new)

**action**:
- `settings.js` exports `SETTINGS_KEY`, `DEFAULT_SETTINGS`, `loadSettings`, `saveSettings`, `mergeSettings`. Pure module — chrome.storage passed in as arg.
- `options.html`: simple page, 2 checkboxes with help text, auto-saves on change. Uses the `options_ui` manifest field with `open_in_tab: true`.
- `options.js` wires checkboxes ↔ saveSettings.

**verify**: open `chrome://extensions` → xbrain → Options → toggle, refresh page → toggle state persists.

**done**: Options page renders with both toggles, persistence works.

### Task B — Side panel wiring

**files**: `chrome-extension/manifest.json`, `chrome-extension/background.js`

**action**:
- manifest: bump version to 1.2.0, add `sidePanel` + `clipboardWrite` permissions, add `side_panel.default_path = "popup.html"`, add `options_ui`.
- background.js: import `loadSettings` + `SETTINGS_KEY`, add `applyPanelBehavior()` that calls `chrome.sidePanel.setPanelBehavior({openPanelOnActionClick})` based on the setting. Call on SW boot, `onStartup`, and `storage.onChanged` for area="sync".

**verify**: click toolbar icon → side panel opens (Chrome ≥ 114). Toggle setting OFF, reload extension → click → popup opens.

**done**: Side panel mode works in Chrome 114+; popup mode preserved as fallback.

### Task C — LibreChat auto-fill content script

**files**: `chrome-extension/manifest.json` (already updated in Task B), `chrome-extension/librechat_autofill.js` (new)

**action**:
- manifest: add `https://chat.grooveos.app/*` to `host_permissions` + new `content_scripts` entry running `librechat_autofill.js` at `document_idle`.
- script: IIFE that reads settings + token, attaches MutationObserver, applies the `shouldFillField` heuristic on every added node, calls `fillInputAsReact` on first match.
- Exposes `shouldFillField` and `fillInputAsReact` on `globalThis.xbrainLibreChatAutofill` for testing.

**verify**: open chat.grooveos.app, select Claude Pro/Max endpoint → API key dialog → field is pre-filled with the user's xbt_. Toggle autoFillLibreChat OFF, reload chat.grooveos.app → no auto-fill.

**done**: Auto-fill works for Claude Pro/Max only; OFF setting disables it.

### Task D — Tests

**files**: `chrome-extension/tests/test_settings.mjs` (new), `chrome-extension/tests/test_librechat_autofill.mjs` (new)

**action**:
- test_settings.mjs: 6 cases covering defaults, persistence, partial merge, unknown-key strip, null safety.
- test_librechat_autofill.mjs: 7 cases covering heuristic + React-style value setter wiring. Loads the content script via `new Function(SCRIPT)()` so its IIFE attaches helpers to globalThis without triggering the chrome.* bail-out.

**verify**: `node tests/run_tests.mjs` → 6/6 test files PASS.

**done**: All new tests pass; full suite still green.

### Task E — Artifacts + commit

**files**: `.planning/quick/260512-spx-.../260512-spx-PLAN.md`, `260512-spx-SUMMARY.md`, `.planning/STATE.md`

**action**: write PLAN.md, SUMMARY.md, add row to STATE.md Quick Tasks table, commit, push.

**done**: Quick task fully documented and on `main`.

## Out-of-band manual UAT

1. `git pull` locally.
2. Chrome → `chrome://extensions` → xbrain → ↻ (now v1.2.0).
3. Right-click extension icon → **Options** → confirm 2 toggles, both ON.
4. Click extension icon → side panel opens on the right (not a floating popup).
5. Click Connect → Google consent → side panel stays open the whole time → 🟢 + email appear without ever clicking Connect twice.
6. Open `https://chat.grooveos.app` → endpoint dropdown → **Claude Pro/Max** → API key dialog → field pre-filled with `xbt_...`.
7. Right-click → Options → toggle "Auto-fill API key in LibreChat" OFF → reload chat.grooveos.app → dialog now empty.

## Out of scope (deferred)

- The backend route alternative (LibreChat user JWT → bridge resolves token server-side). More elegant, but ~2-3h vs ~1h for this content-script approach. Track as a future `bridge-jwt-resolution` task if the heuristic ever breaks on a LibreChat upgrade.
- Toggle for `claudeApiVersionPin` (e.g. lock to a specific claude.ai capture version) — premature.
