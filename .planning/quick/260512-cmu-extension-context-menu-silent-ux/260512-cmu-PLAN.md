---
title: Context menu + silent-only auto-mint + dedup concurrent auth
quick_id: 260512-cmu
slug: extension-context-menu-silent-ux
mode: validate
date: 2026-05-12
must_haves:
  truths:
    - "Opening the side panel/popup NEVER opens a Google consent popup automatically — silent-only zero-click path."
    - "Concurrent calls to getGoogleIdToken share one in-flight promise (no duplicate consent windows when Web Clipper team-load + auto-mint fire together)."
    - "Right-click any selected text on a web page → 'Add selection to xbrain' → side panel/popup opens with the text pre-filled in Content."
    - "Manual Connect button still opens the interactive consent (explicit user action)."
    - "Web Clipper Send to brain still triggers interactive auth on click (explicit user action)."
  artifacts:
    - chrome-extension/background.js (silent flag, promise dedup, context menu registration + handler)
    - chrome-extension/popup.js (maybeAutoMint silent: true, team-load silent: true, pending_selection consumer)
    - chrome-extension/manifest.json (contextMenus + notifications permissions)
  key_links:
    - https://developer.chrome.com/docs/extensions/reference/api/contextMenus
    - https://developer.chrome.com/docs/extensions/reference/api/sidePanel#method-open
    - .planning/quick/260512-zca-extension-zero-click-auth/260512-zca-SUMMARY.md (parent task)
---

# Quick Task 260512-cmu — Context menu + silent-only UX + dedup

## Goal

User feedback on the previous task (`260512-zca`):
1. The Google consent popup loaded **twice** at extension open.
2. Opening a consent popup with **zero interaction** at side panel launch felt "violent".
3. Add a right-click "Add to xbrain" submenu when selecting text.

## Decisions (locked)

| Decision | Choice | Why |
|----------|--------|-----|
| Auto-mint policy | **Silent only**, never interactive | User wanted at least one explicit click before any Google consent popup. |
| Concurrent auth | One in-flight promise shared between callers | Avoids duplicate consent windows when team-load and auto-mint fire in parallel on popup open. |
| Manual Connect button | Still interactive | Explicit user click = OK to open consent. |
| Send to brain | Still interactive | User clicks Send → consent is expected if not signed in. |
| Context menu UX | Stash selection in `chrome.storage.session.pending_selection`, open side panel/popup | Survives across popup opens until the user clicks Send. Falls back to a notification on Chrome < 127. |
| Context menu surface | Right-click selected text only (`contexts: ["selection"]`) | No noise in the page/link/image contexts; matches Web Clipper's selection-only flow. |

## Tasks

| # | Files | Description |
|---|-------|-------------|
| 1 | background.js | Add `silent` option to `getGoogleIdToken`; concurrent calls share `_pendingTokenPromise` |
| 2 | background.js + popup.js | Plumb `silent` through MINT_AND_CONNECT + GET_ID_TOKEN; auto-mint sends silent: true |
| 3 | popup.js | Web Clipper team-load uses silent: true, no error toast on silent failure |
| 4 | manifest.json + background.js + popup.js | contextMenus + notifications permissions; "Add selection to xbrain" menu, handler stashes selection + opens panel; popup consumes pending_selection |
| 5 | PLAN.md + SUMMARY.md + STATE.md | Artifacts + push |

## Out-of-band manual UAT

1. `git pull` → `chrome://extensions` → xbrain → ↻
2. **First click on extension icon (not previously connected)**:
   - Side panel opens
   - "Trying silent sign-in…" shows briefly
   - Silent fails (no prior consent) → loader clears, Connect button visible
   - Click Connect → ONE consent popup → 🟢 + email
3. **Subsequent opens**: silent succeeds → 🟢 in ~1s, zero clicks, zero popups.
4. **Right-click test**: select any text on any web page → right-click → "Add selection to xbrain" → side panel opens with the selection pre-filled in Content. Click Send to brain.
5. **Concurrent dedup test**: open the side panel for the FIRST time on a fresh Chrome session — verify ONLY ONE consent popup appears even though team-load + auto-mint fire in parallel.

## Out of scope (deferred)

- Per-team or per-truth-level submenus on the context menu (e.g. "Add to xbrain → Team A → VALIDATED"). Could be a nice power-user upgrade later.
- Image / link / video right-click captures. Selection-only is the minimum viable UX.
- Notification action buttons (Chrome's notifications API supports them but adds complexity; user reads "click the icon" works for the fallback).
