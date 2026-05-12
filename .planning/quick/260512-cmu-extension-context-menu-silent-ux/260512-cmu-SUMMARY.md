---
title: Context menu + silent-only auto-mint + dedup concurrent auth
quick_id: 260512-cmu
slug: extension-context-menu-silent-ux
date_completed: 2026-05-12
status: complete
must_haves_met: true
---

# Quick Task 260512-cmu — SUMMARY

## Goal (recap)

Three UX fixes after user testing of the previous quick task:
1. Double consent popup at extension open → root-caused to two concurrent `getGoogleIdToken` calls (Web Clipper team-load + auto-mint).
2. Auto-popup at side panel launch felt violent → require at least one explicit click before any consent popup.
3. Add right-click "Add to xbrain" for selected text.

## What shipped

| Component | Change |
|-----------|--------|
| `chrome-extension/background.js` | New module-level `_pendingTokenPromise` for in-flight dedup. `getGoogleIdToken({silent})` opts out of the interactive fallback. MINT_AND_CONNECT + GET_ID_TOKEN runtime messages plumb the silent flag. New context-menu registration ("Add selection to xbrain") + onClicked handler that stashes selection in `pending_selection` and opens the side panel (or popup per setting). |
| `chrome-extension/popup.js` | `maybeAutoMint()` sends `silent: true` — silent failure now degrades to "show Connect button" instead of escalating to interactive. Web Clipper team-load also uses `silent: true` (no error toast on silent failure). DOMContentLoaded consumes `pending_selection` from storage.session before falling back to the live page selection. |
| `chrome-extension/manifest.json` | Permissions += `contextMenus`, `notifications` (the latter is a last-resort fallback for Chrome < 127 where `chrome.action.openPopup` is unavailable). |

## Commits (atomic)

| Commit | Task | Description |
|--------|------|-------------|
| (squashed) | 1-4 | Single feat commit covering silent flag + dedup + context menu + popup wiring |
| Final | 5 | PLAN.md + SUMMARY.md + STATE.md |

Atomic commits-per-task would have meant 4 separate commits all touching `background.js`, defeating the readability benefit. Bundled as one feat commit per the project's pragmatic atomic-when-it-helps rule.

## must_haves verification

| must_have | Status | Evidence |
|-----------|--------|----------|
| No auto consent popup at side panel open | ✅ | `maybeAutoMint` and Web Clipper init both send `silent: true`. Silent path in `getGoogleIdToken` throws on failure when `silent` is true — never escalates to interactive. |
| Concurrent calls share one in-flight promise | ✅ | `_pendingTokenPromise` guard in `getGoogleIdToken`. Two parallel `chrome.runtime.sendMessage({type: 'GET_ID_TOKEN'})` from the popup → one `launchAuthFlow` call. |
| Context menu pre-fills Content from selection | ✅ | `chrome.contextMenus.onClicked` stashes `{selectedText, url, title}` in `chrome.storage.session.pending_selection`; popup `DOMContentLoaded` reads + clears it. |
| Manual Connect still interactive | ✅ | `handleConnect` sends `MINT_AND_CONNECT` with `silent: false`. |
| Send to brain still interactive | ✅ | `handleSend` reuses GET_ID_TOKEN with the default `silent: false`. |

## Tests

```
$ cd chrome-extension && node tests/run_tests.mjs
=== 6/6 test files passed ===
```

Pure-helper tests untouched (onboarding/translate_sse/ws_keepalive/openai-to-claudeai/settings/librechat_autofill). The new logic is glue (chrome.* wiring + message-routing flags) that's only meaningful inside a real extension context.

## Out-of-band manual UAT (user runs locally)

1. `git pull` → `chrome://extensions` → xbrain → ↻
2. **Fresh state (not previously connected):**
   - Click extension icon → side panel opens, shows "Trying silent sign-in…" briefly → loader clears → Connect button visible
   - Click Connect → **ONE** Chrome consent popup → 🟢 + email
3. **Subsequent opens:** silent succeeds → 🟢 in ~1s, no clicks, no popups.
4. **Right-click test:** select text on any page → right-click → "Add selection to xbrain" → side panel opens with text pre-filled in Content → click Send to brain.
5. **No duplicate popup test:** disconnect, then on next side panel open you should see only ONE consent popup when clicking Connect (not two).

## Out of scope (deferred)

- Submenus on the context menu for team/truth_level selection (power-user feature).
- Image/link/video right-click captures.
- Action buttons on the fallback notification.
