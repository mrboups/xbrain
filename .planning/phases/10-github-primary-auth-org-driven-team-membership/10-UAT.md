# Phase 10 — UAT playbook

Manual end-to-end test scenarios for the GitHub-Primary Auth + Org-Driven Team
Membership feature set. Run after `verify-phase10.sh` passes (automated suite)
but before declaring Phase 10 closed.

**Prerequisites**
- memory-api deployed with migration 0016 applied (`alembic current` → `0016`).
- app-site deployed at `https://grooveos.app/`.
- Chrome extension built and loaded as unpacked OR pulled from the released
  CRX bundle.
- GitHub OAuth app `Ov23liVqXmHkS6JdYpcN` authorized-callback list includes
  `https://grooveos.app/account/teams/`.
- Optional: `SMTP_HOST` configured on the VM for Scenario C verification.
- A spare GitHub test account that you can revoke / re-authorize on demand.

## Scenarios

### A. Brand-new user signs in via GitHub (web)

1. Open incognito window. Visit https://grooveos.app/account/teams/.
2. Click "Sign in with GitHub". Authorize on GitHub.
3. **Expected:** redirected back to `/account/teams/` with a clean URL (no
   `?code=` or `?state=` left over after the post-auth redirect).
4. **Expected:** teams page renders the orgs-matched teams. `xbt_…` is set in
   `localStorage` under the expected key.
5. **Backend check:**
   ```
   psql … -c "SELECT email, github_id, github_username, source_user_id \
              FROM users WHERE github_username = '<your-login>'"
   ```
   Row exists; `email` is your real verified GitHub email (not `…@users.noreply.github.com`).

### B. Brand-new user signs in via GitHub (extension)

1. Load extension in Chrome (unpacked from `chrome-extension/`).
2. Click extension icon → popup → "Sign in with GitHub".
3. Authorize on GitHub. Popup reloads after redirect.
4. **Expected:** team dropdown populated. Right-click context menu on any page
   shows the orgs-matched teams under the xbrain submenu.
5. **B-1 storage-key check.** In DevTools (service-worker console), run:
   ```js
   chrome.storage.local.get(["xbt_token", "user_sub", "api_token_id"], console.log)
   ```
   All three keys must be present and non-null. Regression check for the
   storage-key bug fixed in commit 3e0855c.

### C. Org auto-grant emails admins

1. As admin: ensure a team's `github_org` matches your test user's GitHub org.
2. Test user signs in via web OR extension.
3. **Expected:** admin receives email `"New member auto-joined <team>"`.
4. **Fail-soft check:** if `SMTP_HOST` is empty, check memory-api container
   logs for `email.smtp_not_configured` — the auto-grant itself must still
   succeed (test user appears in the team's members list).

### D. Admin blocks a member

1. As admin on `/account/teams/`, find the auto-joined test user in the team
   members list.
2. Click "Block" (extension Options tab OR app-site Settings card).
3. As the blocked test user: try a team-scoped action — call any memory route
   with `?team=<slug>` or use the extension's clip-to-team flow.
4. **Expected:** HTTP 403 with body `"Member blocked from team …"`.
5. Click "Unblock" → access restored.
6. **B-3 regression check:** as the blocked user, present a pre-block-time
   `xbt_…` token to a team-scoped endpoint → still 403. The xbt_ branch in
   `deps.py` must enforce `blocked_at`, not just the bridge JWT branch
   (fix: commit 4b22796).

### E. Pre-block

1. Admin creates a pre-block via the Settings card:
   `github_login = "test-impostor"`.
2. Confirm via API:
   ```
   curl -H "Authorization: Bearer <admin-xbt>" \
        https://api.grooveos.app/v1/teams/<team_id>/org-blocks
   ```
   Response includes the new entry.
3. As GitHub user `test-impostor`: sign in.
4. **Expected:** auto-grant skips the protected team. Other org-matched teams
   still get the auto-grant.

### F. Identity merge — Google sign-in first, then GitHub

1. User signs in with Google first. Team membership granted via email invite
   or org match.
2. Same user later signs in with GitHub (verified email matches the Google
   email).
3. **Expected:** no new user row created; `github_id` and `github_username`
   attached to the existing Google row.
4. **B-2 regression check:** if the user previously had a GitHub-only orphan
   row carrying that `github_id`, the merge succeeds with HTTP 200 from
   `/v1/auth/github/signin` (NOT a 500 IntegrityError on
   `users.github_id`'s unique constraint) and the orphan is soft-deleted.

### G. Identity merge — GitHub orphan, then Google

1. Sign in with GitHub-only via extension (no Google linkage yet) — creates
   the orphan row with `email = <login>@users.noreply.github.com`.
2. Sign in with Google using the real verified email later.
3. Sign in with GitHub again from the extension.
4. **Expected:** orphan row's `merged_into_user_id` is set; `team_members`,
   `conversations`, `memory_items`, `user_api_tokens`, `drive_*`, and the
   other 7 FK tables migrate from orphan to survivor.
5. **M-3 regression check:** use the ORIGINAL pre-merge `xbt_…` token (still
   saved in the extension's `chrome.storage.local`) to call
   `GET /v1/me` → response carries the SURVIVOR identity (`id`, `email`),
   NOT the orphan's. This is the SC-7(h) end-to-end check; the automated
   counterpart is `test_orphan_token_lands_on_survivor`.

## Acceptance

All 7 scenarios pass + `bash infrastructure/scripts/verify-phase10.sh` returns
`PASS: 8 / 8`. Record the PASS/FAIL outcome of each scenario in the phase
SUMMARY.md alongside the date the run was performed.
