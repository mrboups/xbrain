---
title: Context menu — "CortX OS" parent with per-team submenus
quick_id: 260512-csm
slug: context-submenu-teams
date_completed: 2026-05-12
status: complete
must_haves_met: true
---

# Quick Task 260512-csm — SUMMARY

## Goal (recap)

Replace the flat "Add selection to xbrain" right-click menu with a hierarchical "CortX OS" parent and one child per team the user belongs to. Click a team child → side panel opens with selection pre-filled AND the team dropdown already set.

## What shipped

| Component | Change |
|-----------|--------|
| `chrome-extension/background.js` | New module-level constants for parent/child menu IDs and the 24h cache keys. New helpers: `fetchUserTeams` (silent Google auth → GET /v1/teams/my-teams), `loadCachedTeams` / `saveCachedTeams` (chrome.storage.local, 24h TTL), `rebuildContextMenu` (removeAll + recreate parent + children), `refreshContextMenus` (render cached + background fetch). New `openPanelOrPopup` helper extracted so both team-click and connect-first branches share the same panel-opening logic. Storage.onChanged listener on `xbt_token` triggers a refresh when the user just connects. |
| `chrome-extension/popup.js` | DOMContentLoaded reads `pending_selection.team_scope` alongside `selectedText`. After `loadUserTeams()` populates the dropdown, the requested team is auto-selected by matching `option.value === team_scope`. Falls back silently if the team isn't in the user's list (rare — cached menu out of sync). |

## Commit (atomic)

| Commit | Description |
|--------|-------------|
| (next) | Single feat commit covering background.js refresh logic + popup.js team-scope consumer + artifacts |

## must_haves verification

| must_have | Status | Evidence |
|-----------|--------|----------|
| CortX OS parent + per-team children | ✅ | `rebuildContextMenu` creates parent + iterates `teams` array. `chrome.contextMenus.create({parentId: CONTEXT_MENU_PARENT_ID, ...})`. |
| Click stashes team_scope | ✅ | `onClicked` extracts the slug from `id.slice(CONTEXT_MENU_TEAM_PREFIX.length)` and writes it to `pending_selection.team_scope`. |
| Disconnected fallback | ✅ | When `teams` is empty/null, a single `xbrain_connect_first` child is created; its click handler skips selection processing and just opens the panel. |
| Auto-refresh on connect | ✅ | `chrome.storage.onChanged` listener fires `refreshContextMenus()` when `xbt_token` changes. |
| 24h cache | ✅ | `loadCachedTeams` returns null when `Date.now() - ts > TEAMS_CACHE_TTL_MS`. |

## Tests

```
$ cd chrome-extension && node tests/run_tests.mjs
=== 6/6 test files passed ===
```

The new code is SW-only chrome.* glue + a popup DOM interaction (post-loadUserTeams pre-select). No new unit tests — the pure-helper tests (onboarding, settings, librechat_autofill, translate_sse, ws_keepalive, openai-to-claudeai) remain authoritative.

## Out-of-band manual UAT

1. `git pull` → `chrome://extensions` → xbrain → ↻
2. Right-click any text on any page → expand "CortX OS" submenu.
3. **Connected case**: see one child per team you belong to (e.g. "Add selection to xbrain", "Add selection to Acme", etc).
4. **Not-connected case**: see a single "Connect xbrain account…" item.
5. Click a team child → side panel opens with text pre-filled in Content AND dropdown set to the chosen team → click Send to brain → upsert lands in the right team_scope.
6. **Connect freshness test**: disconnect, right-click → shows "Connect xbrain account…" → click → connect via the panel button → re-open right-click → submenu now lists your teams (refreshed via the storage.onChanged listener).

## Deferred

- Truth-level submenus (CortX OS → Team → EPHEMERAL/WORKING/VALIDATED). Could be a future quick task.
- Project-scope picker — requires a `/v1/projects` endpoint.
- Team color/icon in menu — chrome.contextMenus is text-only.
