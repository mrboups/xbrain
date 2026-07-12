---
phase: 12
plan: 11
type: uat
generated: 2026-05-17
maps_to: Phase 12 ROADMAP success criteria GHAPP-01..08 + REVISION 2 fixes (B-2 link-github regression, B-3 SC-5 auto-grant regression, M-6 PAT removal)
---

# Phase 12 — UAT (Manual checklist)

| Field         | Value                                                                |
| ------------- | -------------------------------------------------------------------- |
| Verifier      | _______________________ (default: mrboups — sole xbrain user pre-launch) |
| Date          | _______________________                                              |
| VM IP         | __VM_HOST__                                                       |
| App host      | https://app.example.com                                             |
| Account host  | https://example.com                                                 |
| API host      | https://api.example.com                                             |
| Marketing     | https://example.com/docs/github-auth.html                           |
| GitHub App    | `xbrain` — Client ID `Iv23liVnZvIN0Lo6isof` (App ID 3743573)         |
| Install slug  | https://github.com/apps/xbrain                                       |

This is the manual checklist for the things `verify-phase12.sh` cannot
automate — UI flows, GitHub consent screens, install banners, webhook
deliveries through GitHub's UI, and the OAuth App revocation gate.

Walk it after `verify-phase12.sh` exits 0 (or with only fixture SKIPs).
SKIPPED never blocks; only FAIL == 0 is required.

---

## Pre-checks (must all be true before starting)

- [ ] `bash infrastructure/scripts/verify-phase12.sh` returned `PASS: N / N (SKIPPED: M)` with `FAIL == 0`
- [ ] You have the GitHub App `xbrain` registered on `mrboups` account per `.planning/KB/github-app-operator-runbook.md`
- [ ] The App is installed on at least one org you can sign in with (`dejavudev` for mrboups)
- [ ] `docker ps` on the VM shows `xbrain-memory-api` and `xbrain-postgres` running
- [ ] `docker exec xbrain-memory-api alembic current` returns `0019_github_app_install` (or hash-renamed equivalent)
- [ ] `docker exec xbrain-memory-api printenv | grep -c '^GITHUB_APP_'` returns `6` (all secrets configured)
- [ ] `app-site` and the Chrome extension have been redeployed with the new GitHub App `client_id` (`Iv23liVnZvIN0Lo6isof`)
- [ ] **For step 5 (refresh):** you can SSH into the VM to run psql commands (`gcloud compute ssh` or stored key)

---

## Step 1 — Web sign-in (happy path, SC-1 + SC-3)

1. Open https://example.com/account/teams/ in a **fresh Incognito** window (no cookies / localStorage).
2. Click **Sign in with GitHub**.
3. Observe the GitHub consent screen — confirm:
   - Title reads **"Authorize xbrain"** (the GitHub App, NOT the legacy OAuth App).
   - Permissions listed: **Email addresses (Read)**, **Profile (Read)**, **Members of organizations (Read)**.
   - If consent screen says "xbrain LibreChat" or shows different perms, **STOP** — the wrong app is wired in `teams.js`.
4. Click **Authorize**.
5. Land back on `example.com/account/teams/` with at least one team visible (`dejavudev` for mrboups).
6. In DevTools → Network panel, confirm the `POST /v1/auth/github/signin` response body contains:
   - `"xbt_token": "xbt_..."` (non-null, saved to `localStorage.xbt_token`)
   - `"install_required": false`
   - `"install_url": null`
   - `"org_login": null`
7. The yellow install banner MUST NOT be visible on the page.

PASS criteria: teams render, no install banner, `xbt_token` in `localStorage`, response shape matches.

---

## Step 2 — Web sign-in install-required branch (SC-2)

This step needs a **SECOND** GitHub user whose primary org has NOT installed the xbrain App. If no second user is available, **SKIP this step** — primary coverage is the unit test `tests/test_phase12_signin_install_flow.py`.

1. Sign out of example.com (or open a new Incognito window).
2. Sign in as the second user.
3. The yellow install banner appears at the top of the teams page reading:
   > Install xbrain on `<org_login>` to continue
   (the actual `org_login` substituted, not the literal placeholder).
4. The banner has an **Install** button.
5. In DevTools → Network panel, the `/v1/auth/github/signin` response shows:
   - `"install_required": true`
   - `"install_url"` is a `https://github.com/apps/xbrain/installations/new?state=...` URL
   - `"org_login": "<user_primary_org>"` (NOT null, NOT "your organization" generic)
6. Click **Install**.
7. GitHub install consent screen appears. The org admin path:
   - If the user IS the org admin → consent + Install on All repositories (or Only select — doesn't matter, no repo perms used). Redirect back to `example.com/account/teams/?installation_id=...&setup_action=install`.
   - If the user is NOT the org admin → GitHub shows "approval required" → user sends request to admin. Banner stays visible. **This is still a PASS** for step 2 (the surface works).
8. After install succeeds and the user lands back on the teams page:
   - Within 5 seconds the install banner disappears.
   - The teams list renders with the user's team(s).

PASS criteria: banner appeared with correct `org_login`, click took the user to the GitHub install URL, post-install redirect resumed sign-in successfully.

---

## Step 3 — Chrome extension sign-in (SC-4)

1. Open `chrome://extensions` in Chrome → "Developer mode" ON.
2. If xbrain is not loaded as unpacked, **Load unpacked** → point to `chrome-extension/`.
3. If it's already loaded, click the **reload** icon to pick up the new `manifest.json` `key` field and the new `client_id` in `background.js`.
4. After reload, hover the extension entry → the **ID** value must match the deterministic ID stored in `.planning/KB/chrome-extension-key.md` (expected: `anigikcnmldoklcmogffmgcojdhhficb`). If it does NOT match, the manifest `key` was changed or not pushed — **STOP** and re-verify Plan 12-08 ship.
5. Click the xbrain extension icon → popup opens.
6. Click **Sign in with GitHub** in the popup.
7. Consent screen shows the **xbrain GitHub App** (the same consent UI as Step 1).
8. Authorize. Popup closes the GitHub tab and returns to xbrain with the teams list visible.
9. In the extension service-worker DevTools (right-click extension icon → Inspect popup → Application → Local Storage), confirm the `xbt_token` is stored.

PASS criteria: extension ID is deterministic + matches KB, consent shows the GitHub App, sign-in succeeds, `xbt_token` saved.

---

## Step 4 — Webhook delivery on install / uninstall (SC-3 + GHAPP-03)

This step requires GitHub UI access on the App owner's account (`mrboups`).

1. Open https://github.com/settings/apps/xbrain → **Advanced** tab → **Recent Deliveries**.
2. Note the current count of `installation` event deliveries (e.g. 3).
3. On the VM, snapshot the `installations` table:
   ```
   docker exec -i xbrain-postgres psql -U xbrain -d xbrain -c \
     "SELECT installation_id, github_org_login, suspended_at, revoked_at FROM installations ORDER BY installation_id"
   ```
4. **Uninstall xbrain** from `dejavudev` (Settings → GitHub Apps → xbrain → Configure → Uninstall) OR pick another non-prod org. Confirm the uninstall.
5. Within 30s, refresh the GitHub Recent Deliveries — a new `installation` event with `action: deleted` appears. HTTP status MUST be 200 (or 204).
6. Re-query the `installations` table:
   - The row for the uninstalled org now has `revoked_at` set to a recent timestamp.
   - The other rows are unchanged.
7. **Re-install xbrain** on the same org (same setup as KB operator runbook Step 5).
8. Within 30s, refresh Recent Deliveries — a new `installation` event with `action: created` appears, HTTP 200.
9. Re-query the table:
   - A NEW row appears with a NEW `installation_id` (GitHub generates fresh IDs on re-install).
   - `revoked_at IS NULL` on the new row.
   - The old (revoked) row is preserved with its `revoked_at` set.

PASS criteria: both deliveries showed HTTP 200, DB reflects state on both events, no duplicate active rows for the same org.

---

## Step 5 — Refresh token rotation (SC-6 + GHAPP-05)

This is the **silent refresh** check. Requires SSH access to the VM.

1. Sign in as mrboups via Step 1 (web app). Confirm `xbt_token` in `localStorage`.
2. SSH to the VM → run:
   ```
   docker exec -i xbrain-postgres psql -U xbrain -d xbrain -c \
     "SELECT github_username, github_token_expires_at, github_access_token_hash IS NOT NULL as has_hash \
      FROM users WHERE github_username='mrboups'"
   ```
   Confirm: `github_token_expires_at` is roughly `NOW() + 8h`, `has_hash = t`.
3. Force the next API call to trigger refresh:
   ```
   docker exec -i xbrain-postgres psql -U xbrain -d xbrain -c \
     "UPDATE users SET github_token_expires_at = NOW() - interval '1 minute' WHERE github_username='mrboups'"
   ```
4. Back in the browser, navigate to any page that triggers `/v1/me` or `/v1/me/teams` (e.g. refresh the teams page).
5. The page should render without prompting for re-auth (the refresh happened silently server-side).
6. Re-run the SELECT from step 2 — `github_token_expires_at` is now `NOW() + 8h` (fresh expiry), `has_hash = t` (hash updated alongside the encrypted token).
7. In memory-api logs:
   ```
   docker compose logs memory-api --since 5m | grep -E "refresh|github_user_token" | head -20
   ```
   Expect log lines like `github_user_token.refreshed` or equivalent (the exact wording depends on Plan 12-06 implementation — any non-error log line proving the refresh path fired is acceptable).

PASS criteria: token transparently refreshed, hash column populated, no user-visible interruption.

---

## Step 6 — Hybrid installation lookup self-heal (GHAPP-03 reconciliation)

Simulates a missed webhook to verify the hybrid lookup (`/orgs/{org}/installation` fallback) backfills the row.

1. Confirm there's currently an active installations row for `dejavudev`:
   ```
   docker exec -i xbrain-postgres psql -U xbrain -d xbrain -c \
     "SELECT installation_id FROM installations WHERE github_org_login='dejavudev' AND revoked_at IS NULL"
   ```
2. Delete the row to simulate a missed webhook:
   ```
   docker exec -i xbrain-postgres psql -U xbrain -d xbrain -c \
     "DELETE FROM installations WHERE github_org_login='dejavudev' AND revoked_at IS NULL"
   ```
3. Sign out + sign in via web app (Step 1).
4. Re-query the table — a row should be back, with `revoked_at IS NULL` and an `installation_id` matching the live GitHub install (NOT a fresh re-install — same id).
5. In memory-api logs `docker compose logs memory-api --since 2m | grep -E "installation.*backfill|hybrid_lookup"` — expect one log line proving the fallback fired.

PASS criteria: signin worked without re-installing the App, DB row self-healed within a single request.

---

## Step 7 — LibreChat link-github regression (B-2 fix preserved)

The Phase 5 `/v1/me/link-github` path uses the new `check_github_org_membership` signature post-Plan 12-04. This step proves that flow did NOT break.

1. Visit https://chat.example.com/ → sign in via LibreChat (using LibreChat's own OAuth — the `xbrain LibreChat` OAuth App `Ov23li0XHV3NL8Git7Dk`, which is intentionally NOT migrated).
2. After landing in LibreChat, open https://example.com/account/onboarding/ in another tab while still logged in to LibreChat.
3. Follow the "link GitHub" flow if it appears, OR call the endpoint directly to confirm wiring:
   ```
   curl -X POST https://api.example.com/v1/me/link-github \
     -H "Authorization: Bearer <librechat_jwt>" \
     -H "Content-Type: application/json" \
     -d '{"code":"<code_from_callback>","redirect_uri":"https://chat.example.com/api/auth/callback/github","state":"x"}'
   ```
   (`<librechat_jwt>` comes from LibreChat's `Authorization` header — inspect via DevTools.)
4. Response: 200 with `{"github_username": "mrboups", "github_id": <int>}` (or similar). NOT a 500.

PASS criteria: link-github returned 200, NO `check_github_org_membership` signature error in memory-api logs (`docker compose logs memory-api --since 2m | grep -E "check_github_org_membership|TypeError"` returns empty).

---

## Step 8 — Multi-frontend independence (GHAPP-07)

Two sign-ins from two surfaces against the same user row, same `client_id`.

1. Sign in as mrboups via the web app (Step 1) — `xbt_token_web` saved in browser `localStorage`.
2. Without signing out the web app, sign in via the Chrome extension (Step 3) — `xbt_token_ext` saved in extension `localStorage`.
3. Both tokens are valid (different `xbt_` values, both decode to the same `principal.sub`).
4. Confirm both surfaces show the same `dejavudev` team and brain content.
5. In `psql`:
   ```
   SELECT id, github_username, github_id FROM users WHERE github_username='mrboups'
   ```
   Exactly ONE row (NOT two — the GitHub App's multi-callback support means same client_id → same `github_id` → same user row).

PASS criteria: same user across two frontends, no duplicate user rows, both tokens functional.

---

## Step 9 — OAuth App revocation gate (24h+ after Phase 12 LIVE)

This step is **gated on the prior 8 steps all passing AND 24h elapsed**. Do NOT execute on the same day Phase 12 ships.

1. Re-run `verify-phase12.sh` on the VM with all fixture env vars exported. Target PASS rate ≥ 15/18 (allowed SKIPs: 12, 13, 18 — the fixtures that may not be set in a public CI run).
2. Review memory-api logs for the last 6 hours:
   ```
   docker compose logs memory-api --since 6h | grep -E "auth\.github|github_app" | grep -iE "error|exception|traceback|fail" | wc -l
   ```
   Expected count: 0 (zero auth-related errors in last 6h).
3. If both 1 and 2 pass → proceed with revocation per `.planning/KB/oauth-app-revocation.md`:
   - Visit https://github.com/settings/applications
   - Find OAuth App `xbrain` (Client ID `Ov23liy7tZekl0uEztoj`)
   - Edit → Delete application → confirm
4. **CRITICAL — do NOT delete `xbrain LibreChat` (Client ID `Ov23li0XHV3NL8Git7Dk`)** — separate OAuth App, used by LibreChat, untouched by Phase 12.
5. After revocation, re-test Step 1 (web sign-in) and Step 3 (chrome extension sign-in). Both must still succeed.
6. Verify no production code path references the revoked client_id:
   ```
   grep -rn "Ov23liy7tZekl0uEztoj" apps/ app-site/ chrome-extension/ \
     --include="*.js" --include="*.py" --include="*.html" --include="*.json" 2>/dev/null \
     | grep -v __pycache__ | wc -l
   ```
   Expected: 0 (already enforced by `verify-phase12.sh` assertion 7).

PASS criteria: legacy OAuth App revoked, post-revocation sign-ins (web + ext) still succeed, no production code grep hit.

---

## Sign-off

- [ ] Steps 1 through 8 all PASS (Step 2 may SKIP if no second user available — see covering unit tests)
- [ ] Step 9 deferred 24h+ and executed after green-light from steps 1-8
- [ ] Any FAIL or SKIP recorded below: ______________________________________________
- [ ] Verifier signature: ______________________________________________
- [ ] Date completed: ______________________________________________

When all 9 steps are signed off, reply `uat-pass` to the orchestrator and
record the result in `12-SUMMARY.md`. On a FAIL, reply `uat-fail: step-N`
with a one-line description; a gap-closure plan will follow.
