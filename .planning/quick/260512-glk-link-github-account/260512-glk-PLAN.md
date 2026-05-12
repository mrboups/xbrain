---
title: Link GitHub account to Google-authenticated xbrain user
quick_id: 260512-glk
slug: link-github-account
mode: validate
date: 2026-05-12
must_haves:
  truths:
    - "/v1/me returns github_username + github_id (null when not linked) for both kind=user and kind=user_api_token."
    - "/v1/teams/my-teams merges GitHub-org-derived teams when user.github_username is set — no explicit invitation needed."
    - "POST /v1/me/link-github-with-code accepts a GitHub OAuth code, exchanges with client_secret server-side, and links github_username + github_id."
    - "Extension shows a 'Link GitHub' button in the connected session card only when github_username is null."
    - "On link success, the CortX OS context menu cache invalidates and the team submenus refresh with newly-accessible GitHub-org teams."
  artifacts:
    - apps/memory-api/app/routes/me.py (add github_username + github_id to /v1/me)
    - apps/memory-api/app/routes/teams.py (merge GitHub-org matches into /v1/teams/my-teams)
    - apps/memory-api/app/routes/me_github.py (add POST /v1/me/link-github-with-code)
    - apps/memory-api/app/deps.py (xbt_ principal carries github_username + github_id)
    - chrome-extension/background.js (getGithubAuthCode, linkGithubFlow, LINK_GITHUB message handler, GITHUB_CLIENT_ID)
    - chrome-extension/popup.html (Link GitHub row + Linked GitHub row)
    - chrome-extension/popup.js (renderGithubLinkState, handleLinkGithub)
    - chrome-extension/popup.css (Link button accent fill)
  key_links:
    - apps/memory-api/app/models/user.py (github_username + github_id columns)
    - apps/memory-api/alembic/versions/0007_github_users.py (column migration)
    - apps/memory-api/app/routes/teams.py:224 (existing /teams/github-matches endpoint that we mirror)
    - https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps
---

# Quick Task 260512-glk — Link GitHub account

## Goal

When a user signs in via Google, they currently see only their solo team. If they also have a GitHub account (with org memberships), those org-based teams should become accessible via the extension after a one-click linking step.

Backend infrastructure for this already exists (`users.github_id`, `users.github_username`, `/v1/me/link-github`, `/teams/github-matches`) but nothing surfaces it to the Chrome extension yet.

## Decisions (locked)

| Decision | Choice | Why |
|----------|--------|-----|
| OAuth flow | Server-side code exchange | client_secret stays on memory-api. Extension only passes the `code` from `launchWebAuthFlow` — never sees the access token until it's already linked. |
| Endpoint shape | `POST /v1/me/link-github-with-code {code, redirect_uri}` | Mirrors GitHub's `/login/oauth/access_token` contract and delegates to the existing `link_github` handler. |
| Auth on `link-github-with-code` | Standard Google ID token via Authorization header | Same as `link-github` — requires `kind=user` (no bridge JWTs). |
| GitHub-org team visibility | Always-on for linked users | `/v1/teams/my-teams` transparently merges explicit team_members + matching github_org teams. No `?include_github=true` flag — keeps the extension contract simple. |
| Failure mode | Soft-fail in `/v1/teams/my-teams` | A GitHub API hiccup must not break the team dropdown. The merge is wrapped in try/except — explicit team_members always render. |
| Extension client_id | Hardcoded matching memory-api's `GITHUB_CLIENT_ID` | Single OAuth app. Adds the chromiumapp.org redirect URI as a manual one-time setup step in the GitHub OAuth app config. |

## Tasks

| # | Files | Description |
|---|-------|-------------|
| A | memory-api/app/routes/me_github.py | Already existed — added `link-github-with-code` server-side code exchange endpoint |
| B | memory-api/app/routes/{me,teams}.py, deps.py | Expose github_username/github_id on /v1/me; merge GitHub-org-derived teams in /v1/teams/my-teams |
| C | chrome-extension/{background,popup}.{js,html,css} | GitHub OAuth flow, Link button UI, renderGithubLinkState, LINK_GITHUB message handler |
| D | scp + rebuild memory-api on VM; PLAN/SUMMARY/STATE | Deploy + finalize |

## Manual one-time setup

The user must add `https://<extension-id>.chromiumapp.org/` to the GitHub OAuth App's "Authorization callback URLs" at:
  https://github.com/settings/applications/{app_id}

The extension ID is visible at `chrome://extensions` under xbrain. Until this is done, the OAuth flow will fail with "The redirect_uri MUST match the registered callback URL".

## Out-of-band manual UAT

1. `git pull` → `chrome://extensions` → xbrain → ↻
2. Side panel → confirm you're connected (🟢 Claude Pro/Max)
3. New "GitHub — Not linked" row should appear below.
4. Click **Link** → Chrome shows the GitHub OAuth authorize page → Authorize.
5. Returns to side panel → "Linked @yourgithubusername ✓ — team list refreshed".
6. Right-click any text → "CortX OS" submenu now lists GitHub-org-derived teams alongside solo team.
7. Open Web Clipper team dropdown — same expanded list.

## Out of scope (Phase 2)

- Unlink GitHub button (`DELETE /v1/me/link-github`).
- Show "via GitHub" badge next to org-derived teams in the dropdown.
- Reverse linking (a GitHub user linking a Gmail account).
- Auto-suggest "We noticed you're signed into GitHub in Chrome — link it?".
- Same-flow for Microsoft / Discord / SAML.
