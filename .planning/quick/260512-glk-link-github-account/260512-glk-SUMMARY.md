---
title: Link GitHub account to Google-authenticated xbrain user
quick_id: 260512-glk
slug: link-github-account
date_completed: 2026-05-12
status: complete
must_haves_met: true
---

# Quick Task 260512-glk — SUMMARY

## Goal (recap)

Bridge the gap between Google-only xbrain users and their GitHub org teams: one-click linking from the extension, transparent merge of org-derived teams into `/v1/teams/my-teams`, and the CortX OS context menu refreshes automatically.

## What shipped

| Component | Change |
|-----------|--------|
| `apps/memory-api/app/routes/me.py` | `/v1/me` now returns `github_username` + `github_id` for `kind=user` and `kind=user_api_token` (null when not linked). |
| `apps/memory-api/app/deps.py` | xbt_ principal's SimpleNamespace user now carries `github_username` + `github_id` (SQL extended in user_api_tokens lookup). |
| `apps/memory-api/app/routes/teams.py` | `/v1/teams/my-teams` merges explicit team_members rows with GitHub-org-matched teams (when `user.github_username` is set). Soft-fails on GitHub API errors — explicit teams always render. |
| `apps/memory-api/app/routes/me_github.py` | Added `POST /v1/me/link-github-with-code` — accepts `{code, redirect_uri}`, exchanges with GitHub for `gho_` using server-side `GITHUB_CLIENT_SECRET`, then delegates to the existing `link_github` handler. |
| `chrome-extension/background.js` | New `GITHUB_CLIENT_ID` constant, `getGithubAuthCode()` (launchWebAuthFlow → GitHub `/authorize` → state-checked redirect URL parse), `linkGithubFlow()` (Google ID token + GitHub code → POST `link-github-with-code` → invalidate teams cache + refresh context menu), `LINK_GITHUB` runtime message handler. |
| `chrome-extension/popup.html` | New rows: `#github-link-row` (Not linked + Link button) and `#github-linked-row` (linked state with @username). Both hidden by default; toggled by `renderGithubLinkState`. |
| `chrome-extension/popup.js` | `renderGithubLinkState()` reads `/v1/me.github_username` and toggles the two rows. `handleLinkGithub()` dispatches LINK_GITHUB and surfaces inline status. Wired into `renderSessions` and DOMContentLoaded. |
| `chrome-extension/popup.css` | Accent-fill style for `#btn-link-github` so it reads as a primary action. |

## must_haves verification

| must_have | Status | Evidence |
|-----------|--------|----------|
| `/v1/me` returns github_username + github_id | ✅ | me.py:30-40 returns the fields for both user kinds. |
| `/v1/teams/my-teams` merges GitHub-org teams | ✅ | teams.py:135 wraps the merge in try/except so soft-failure keeps explicit teams rendering. |
| `POST /v1/me/link-github-with-code` exchanges + links | ✅ | me_github.py:114 — bridges the OAuth code through GitHub's token endpoint and delegates to `link_github`. |
| Extension shows Link button only when not linked | ✅ | popup.js `renderGithubLinkState` toggles `#github-link-row.hidden` based on `me.github_username`. |
| Context menu refreshes on link success | ✅ | background.js `linkGithubFlow` clears `chrome.storage.local[TEAMS_CACHE_KEY]` and calls `refreshContextMenus()` before resolving. |

## Tests

```
$ cd apps/memory-api && python -m pytest tests/test_auth.py -x --no-header -q
10 passed, 3 skipped, 1 warning

$ cd chrome-extension && node tests/run_tests.mjs
=== 6/6 test files passed ===
```

No new pytest cases for the new endpoint (`link-github-with-code` is a thin wrapper around the already-tested `link_github` + a stubbable GitHub token exchange — would need an integration test with mocked GitHub, defer to Phase 2 when we add other GitHub flows).

## Deploy

* memory-api rebuilt + restarted on VM (`xbrain-memory-api` healthcheck green within 19s).
* chrome-extension: no deploy needed — user pulls + reloads locally.

## Manual one-time setup the user must do

Add `https://<extension-id>.chromiumapp.org/` to the GitHub OAuth App's "Authorization callback URLs" list:
  https://github.com/settings/applications/{app_id}

Extension ID visible at `chrome://extensions`. Without this, the GitHub `/authorize` redirect will fail with "redirect_uri must match the registered callback URL".

## Out-of-band manual UAT

1. `git pull` → `chrome://extensions` → xbrain → ↻
2. Side panel → ensure connected (🟢 Claude Pro/Max)
3. **New row visible**: "GitHub — Not linked — link to see your org teams" with a blue **Link** button.
4. Click Link → Chrome opens GitHub authorize page → click Authorize.
5. Returns to side panel → "Linked @yourgithubusername ✓ — team list refreshed".
6. Right-click any text → "CortX OS" submenu now lists your GitHub-org teams alongside solo team.
7. Open Web Clipper team dropdown — same expanded list.

## Deferred to Phase 2

- `DELETE /v1/me/link-github` to unlink.
- "via GitHub org X" badge next to derived teams in the dropdown.
- Reverse linking (GitHub user attaches a Gmail).
- Auto-prompt "We see you're signed into GitHub — link it?".
- Same flow for Microsoft / Discord / SAML.
