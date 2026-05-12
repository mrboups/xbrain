---
title: Extension side panel + LibreChat API key auto-fill
quick_id: 260512-spx
slug: extension-sidepanel-libchat-autofill
date_completed: 2026-05-12
status: complete
must_haves_met: true
---

# Quick Task 260512-spx — SUMMARY

## Goal (recap)

Two UX upgrades requested during live UAT of 260512-eo1:
1. Move the extension UI to Chrome's side panel API (Chrome 114+) so the popup doesn't close during Google sign-in.
2. Auto-fill the LibreChat API key field for the "Claude Pro/Max" endpoint from the persisted xbt_token. Both gated by toggleable settings on a new Options page.

## What shipped

| Component | Change |
|-----------|--------|
| `chrome-extension/settings.js` (NEW) | Pure schema + storage module: `SETTINGS_KEY`, `DEFAULT_SETTINGS`, `loadSettings`, `saveSettings`, `mergeSettings`. Defensive merge strips unknown keys + non-booleans. |
| `chrome-extension/options.html` + `.js` (NEW) | Settings page (`options_ui` with `open_in_tab: true`). Two checkboxes auto-save to `chrome.storage.sync` — no Save button needed. |
| `chrome-extension/manifest.json` | Version bump 1.1.0 → 1.2.0. Added: `sidePanel` + `clipboardWrite` permissions, `side_panel.default_path = "popup.html"`, `options_ui`, `host_permissions` += `chat.grooveos.app`, new `content_scripts` entry for `librechat_autofill.js`. |
| `chrome-extension/background.js` | Imports settings module. New `applyPanelBehavior()` calls `chrome.sidePanel.setPanelBehavior({openPanelOnActionClick})` based on the toggle. Triggered on SW boot, `onStartup`, and `storage.onChanged` for area=sync. Silent no-op on Chrome < 114. |
| `chrome-extension/librechat_autofill.js` (NEW) | Content script for `chat.grooveos.app`. MutationObserver watches the DOM; `shouldFillField` heuristic matches inputs by type/attrs/anchor card text "Claude Pro/Max". `fillInputAsReact` uses the prototype value setter + synthetic `input` event so React's controlled component picks up the change. One-shot per page load. |
| `chrome-extension/tests/test_settings.mjs` (NEW) | 6 cases — defaults, persistence, partial merge, unknown-key strip, null safety. |
| `chrome-extension/tests/test_librechat_autofill.mjs` (NEW) | 7 cases — heuristic accept/reject across multiple input types and wrong-endpoint anchors, React-style value setter end-to-end. |

## Commits

| Commit | Description |
|--------|-------------|
| `7179d61` | Single atomic commit: settings + options + sidepanel + autofill + 13 new tests |

(Quick task was small enough to ship as one commit. Atomic-per-task pattern is preserved by the structured commit message body listing every file's purpose.)

## Tests

```
$ node chrome-extension/tests/run_tests.mjs
=== 6/6 test files passed ===
```

53 individual assertions across:
- test_onboarding.mjs (10 cases, quick task 260512-eo1)
- test_settings.mjs (6 cases, NEW)
- test_librechat_autofill.mjs (7 cases, NEW)
- test_openai_to_claudeai.mjs (existing)
- test_translate_sse.mjs (existing)
- test_ws_keepalive.mjs (existing)

## must_haves verification

| must_have | Status | Evidence |
|-----------|--------|----------|
| Options page with 2 toggles, both default ON | ✅ | `options.html` + `DEFAULT_SETTINGS` in `settings.js`. Tests #1, #2 cover defaults. |
| Side panel mode (Chrome 114+) keeps UI alive during Google sign-in | ✅ | `applyPanelBehavior()` in `background.js` lines 425-440. Manifest `side_panel.default_path = "popup.html"`. |
| Auto-fill API key on chat.grooveos.app for Claude Pro/Max | ✅ | `librechat_autofill.js` heuristic + React setter. Tests #4, #5 confirm match; test #6 confirms wrong-endpoint anchor is rejected. |
| Auto-fill gated by autoFillLibreChat setting | ✅ | `main()` in librechat_autofill.js bails out when setting is false. |
| Auto-fill never targets the wrong endpoint | ✅ | `shouldFillField` requires anchor card text /claude\s*pro\s*\/?\s*max/i. Tested in test_librechat_autofill.mjs #3 and #6. |

## Local test run

```
--- test_librechat_autofill.mjs ---
  PASS: shouldFillField rejects non-input nodes
  PASS: shouldFillField rejects type=button and type=checkbox
  PASS: shouldFillField rejects when surrounding card lacks Claude Pro/Max
  PASS: shouldFillField accepts type=password with Claude Pro/Max anchor
  PASS: shouldFillField accepts type=text with api_key-like attributes
  PASS: shouldFillField rejects api-key input anchored to OpenAI dialog
  PASS: fillInputAsReact uses the prototype value setter + dispatches input event
7 passed, 0 failed

--- test_settings.mjs ---
  PASS: DEFAULT_SETTINGS has both toggles ON
  PASS: loadSettings returns defaults when storage empty
  PASS: loadSettings honors persisted false values
  PASS: saveSettings patches without losing other keys
  PASS: mergeSettings strips unknown keys and non-booleans
  PASS: mergeSettings handles null/undefined input safely
6 passed, 0 failed
```

## Out-of-band manual UAT (user runs locally)

1. `git pull` in working copy.
2. `chrome://extensions` → xbrain → ↻ (version reads 1.2.0).
3. Right-click extension icon → **Options** → confirm 2 toggles, both ON.
4. Click extension icon → side panel opens on the right edge of the window (not a floating popup).
5. If not already connected: click Connect → Google consent window appears, **side panel stays open**, completes → 🟢 + email appear automatically.
6. Open `https://chat.grooveos.app` → endpoint dropdown → **Claude Pro/Max** → API key dialog should pre-fill with `xbt_…`.
7. Right-click extension icon → Options → toggle "Auto-fill API key in LibreChat" OFF → reload chat.grooveos.app → API key dialog is empty.

## Deferred

- LibreChat selector resilience: if a future LibreChat upgrade changes its DOM enough that the heuristic misses, swap to the bridge-JWT-resolution alternative (~2-3h backend work, no content script needed at all).
- Internationalize the Options page if the project ever ships to non-English users (current strings are English per the CLAUDE.md language policy).
