---
title: Context menu — "CortX OS" parent with per-team submenus
quick_id: 260512-csm
slug: context-submenu-teams
mode: validate
date: 2026-05-12
must_haves:
  truths:
    - "Right-click on a selection shows a 'CortX OS' parent menu with one child per team the user belongs to."
    - "Clicking a team child stashes the selection + team_scope so the popup opens with that team pre-selected."
    - "When the user has no xbt_token (not connected), the parent menu shows a 'Connect xbrain account…' fallback that opens the side panel."
    - "Menu refreshes automatically when the user just connects (storage.onChanged on xbt_token)."
    - "Team list is cached for 24h in chrome.storage.local so the menu renders instantly on boot."
  artifacts:
    - chrome-extension/background.js (refreshContextMenus, fetchUserTeams, rebuildContextMenu, openPanelOrPopup helper)
    - chrome-extension/popup.js (consumes pending_selection.team_scope and pre-selects in dropdown)
  key_links:
    - https://developer.chrome.com/docs/extensions/reference/api/contextMenus#method-create
    - apps/memory-api/app/routes/teams.py:135 (GET /v1/teams/my-teams)
    - .planning/quick/260512-cmu-extension-context-menu-silent-ux/260512-cmu-SUMMARY.md (parent task with the flat menu)
---

# Quick Task 260512-csm — Context menu submenus per team

## Goal

Upgrade the flat "Add selection to xbrain" context menu (built in 260512-cmu) into a hierarchical:

```
CortX OS
├─ Add selection to <Team A>
├─ Add selection to <Team B>
└─ Add selection to <Team C>
```

When the user clicks a team child, the side panel opens with the selection in Content AND the dropdown pre-selected to that team — one right-click, one Send to brain.

## Decisions (locked)

| Decision | Choice | Why |
|----------|--------|-----|
| Parent label | `"CortX OS"` | User's brand naming. |
| Auth source for team fetch | `getGoogleIdToken({silent: true})` | `/v1/teams/my-teams` requires `kind=user` (Google ID token); xbt_ has `kind=user_api_token` and is rejected. Silent path means no consent popup just to refresh the menu. |
| Cache | `chrome.storage.local`, 24h TTL | Team memberships change rarely. Cached menu renders instantly; background refresh updates if stale. |
| Disconnected fallback | Single child "Connect xbrain account…" that opens the panel | Users still get a visual entry point without leaking a broken menu. |
| Menu rebuild strategy | `removeAll()` + recreate | `contextMenus` has no batched update API and tracking individual ids gets fragile across SW restarts. |
| Refresh triggers | onInstalled + SW boot + `chrome.storage.onChanged.local.xbt_token` | xbt_token appearance = user just connected = good time to refresh the team list. |

## Tasks

| # | Files | Description |
|---|-------|-------------|
| 1 | background.js | refreshContextMenus + fetchUserTeams + rebuildContextMenu helpers; storage.onChanged listener for xbt_token |
| 2 | popup.js | pending_selection.team_scope consumer — pre-select in the team dropdown after loadUserTeams |
| 3 | PLAN.md + SUMMARY.md + STATE.md | Artifacts + push |

## Out-of-band manual UAT

1. `git pull` → `chrome://extensions` → xbrain → ↻
2. Right-click any text → "CortX OS" → see per-team children if connected, or "Connect xbrain account…" if not.
3. Click a team child → side panel opens with text pre-filled AND dropdown pre-set to that team → Send to brain → check the upsert lands in the right team_scope.
4. Connect (if not already) → the menu refreshes automatically — open right-click again, the submenu now lists your teams.

## Out of scope (deferred)

- Truth-level submenus (CortX OS → Team → EPHEMERAL/WORKING/VALIDATED). User can still set truth-level in the popup before sending.
- Project-scope submenus (CortX OS → Team → Project A / Project B). Project is currently a free-text field in the popup; adding a project picker would require a `/v1/projects` endpoint.
- Surfacing the team's icon/color in the menu — chrome.contextMenus only takes a text title.
