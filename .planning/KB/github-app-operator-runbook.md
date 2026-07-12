# xbrain GitHub App — operator registration runbook

**Status:** Reference for operator-side manual setup. Plans 12-08 and 12-09
Section 0 contain inline checklists that mirror Steps 1-3 here.

**Owner:** Personal account `mrboups` (per `12-CONTEXT.md` locked decision —
matches the OAuth Apps pattern in place since Phase 5; transferring to a
dedicated org later is possible per GitHub docs without breaking installs).

**When to run:** Once at Phase 12 ship-out (already done 2026-05-17 per
`project_xbrain_phase12_operator_prep`). Re-run only when migrating to a new
App (e.g. transferring to a dedicated org in a future phase).

---

## Step 1 — Generate Chrome extension keypair (FIRST — derives the callback URL)

Per `.planning/KB/chrome-extension-key.md`. Capture:

- (A) Base64-encoded public key → goes into `manifest.json` `"key"` field.
- (B) Derived extension ID → goes into the App's callback URL.

For Phase 12, the derived ID is `anigikcnmldoklcmogffmgcojdhhficb`.

**This step MUST come first.** The chrome extension callback URL must be
registered on the App during Step 2 — if you generate the keypair after
registering the App, you have to come back and edit the App's callback URLs.

---

## Step 2 — Register the GitHub App

1. Visit https://github.com/settings/apps → **New GitHub App**.
2. Fill in:
   - **GitHub App name:** `xbrain` (or `xbrain-app` if `xbrain` is taken by
     an old App on the same account)
   - **Homepage URL:** https://example.com
   - **Callback URLs** (one per line — both required for Phase 12):
     ```
     https://example.com/account/teams/
     https://anigikcnmldoklcmogffmgcojdhhficb.chromiumapp.org/
     ```
   - **Setup URL (optional):** leave blank
   - **Webhook URL:** https://api.example.com/v1/webhooks/github/installation
   - **Webhook secret:** generate via `openssl rand -hex 32` → SAVE this
     value for `GITHUB_APP_WEBHOOK_SECRET`.
3. **Permissions** (per `12-CONTEXT.md` minimal scope decision):
   - **Repository permissions:** NONE
   - **Organization permissions:** Members = Read
   - **Account permissions:** Email addresses = Read, Profile = Read
4. **Subscribe to events:**
   - Check `Installation`
   - Check `Installation repositories`
   - UNCHECK everything else, especially `installation_target` (deferred
     per `12-RESEARCH.md` §Q13)
5. **Where can this GitHub App be installed?** Any account.
6. **Optional features (CRITICAL):** check **User-to-server token expiration**
   (Opt-in). Without this, user tokens are unbounded and no refresh tokens
   are issued — Phase 12 mandates expiration.
7. Save. GitHub redirects to the App settings page.

---

## Step 3 — Capture secrets

From the App settings page, capture and store securely (one-time costs only —
do NOT commit to git):

| What                  | Where to find it                                     | Env var                       |
| --------------------- | ---------------------------------------------------- | ----------------------------- |
| **App ID** (numeric)  | Top of App settings page                             | `GITHUB_APP_ID`               |
| **App URL slug**      | Visible in App URL (e.g. `xbrain`)                   | `GITHUB_APP_SLUG`             |
| **Client ID**         | "Client ID: Iv23li..." in General tab                | `GITHUB_APP_CLIENT_ID`        |
| **Client secret**     | "Generate a new client secret" → save the value      | `GITHUB_APP_CLIENT_SECRET`    |
| **Private key**       | "Generate a private key" → downloads `<slug>.PEM`    | `GITHUB_APP_PRIVATE_KEY_B64`  |
| **Webhook secret**    | Whatever you set in Step 2                           | `GITHUB_APP_WEBHOOK_SECRET`   |

The private key must be base64-encoded as a single line for the env var:

```
base64 -w 0 < <slug>.<date>.private-key.pem
```

(On macOS without `-w`: `base64 -i <slug>.<date>.private-key.pem | tr -d '\n'`.)

For Phase 12 the resulting values were:
- `GITHUB_APP_ID = 3743573`
- `GITHUB_APP_SLUG = xbrain`
- `GITHUB_APP_CLIENT_ID = Iv23liVnZvIN0Lo6isof`
- Other secrets stored in `~/.config/xbrain/secrets/xbrain-github-app.txt` + the PEM
  file (per `project_xbrain_phase12_operator_prep`).

Before running `/gsd-execute-phase 12`, the executor shell needed
`NEW_GITHUB_APP_CLIENT_ID=Iv23liVnZvIN0Lo6isof` exported so Plans 12-08 and
12-09 could swap the constant in `teams.js` and `background.js`.

---

## Step 4 — Update `.env` on the VM

```bash
ssh xbrain-vm
sudo nano /opt/xbrain/.env  # or wherever .env lives — check `docker compose config`
```

Add the 6 GITHUB_APP_* vars from Step 3.

Remove `GITHUB_API_PAT` and `GITHUB_ORG_PAT` if present (no-op for the
code path after Plan 12-04 shipped, but cleaner env).

Restart memory-api: `docker compose restart memory-api`.

Verify the env loaded with `docker exec xbrain-memory-api printenv | grep ^GITHUB_APP_`
— count must be exactly 6.

---

## Step 5 — Install the App on dejavudev

1. Visit https://github.com/apps/xbrain (or whatever your `GITHUB_APP_SLUG` is).
2. Click **Configure** (or **Install** if you're not logged in as the App owner).
3. Select the `dejavudev` org.
4. Install on All repositories (or Only select — doesn't matter, no repo perms
   used in v1).
5. Accept consent.

Within 30s, the webhook should fire and create the installations row:

```
docker exec -i xbrain-postgres psql -U xbrain -d xbrain -c \
  "SELECT installation_id, github_org_login, installed_at FROM installations WHERE github_org_login='dejavudev'"
```

A single row with `revoked_at IS NULL` is the expected state.

---

## Step 6 — Verify deploy

```bash
cd /opt/xbrain  # or wherever the repo is on the VM
bash infrastructure/scripts/verify-phase12.sh
```

Required env vars (export before running):

```bash
export MEMAPI_HOST=https://api.example.com
export GITHUB_APP_CLIENT_ID=Iv23liVnZvIN0Lo6isof
export GITHUB_APP_WEBHOOK_SECRET=<from Step 3>
export TEST_INSTALLATION_ID=<from Step 5 — read from psql>
export TEST_GITHUB_ORG=dejavudev
```

Target PASS rate: ≥ 15 / 18. Allowed SKIPs:
- Assertion 12 (webhook signed ping) — SKIPs if `GITHUB_APP_WEBHOOK_SECRET` not set
- Assertion 13 (installations row) — SKIPs if `TEST_GITHUB_ORG` not set
- Assertion 18 (SC-5 regression) — SKIPs unless `TEST_BLOCKED_LOGIN` is prepped
  (a separate fixture user with a `team_org_blocks` row — primary coverage is
  the unit test `tests/test_phase12_auto_grant_regression.py`)

A FAIL on any other assertion is a ship blocker.

---

## Step 7 — UAT

Execute `.planning/phases/12-github-app-migration-public-deployment-ready-auth/12-UAT.md`
Steps 1-8.

Step 9 (OAuth App revocation) is deferred 24h+ per the gate.

---

## Step 8 — Revoke legacy OAuth App (24h+ after UAT pass)

Per `.planning/KB/oauth-app-revocation.md`. The runbook is gated on:

- Phase 12 LIVE ≥ 24h
- mrboups signed in successfully via the new GitHub App from web + chrome ext
- `verify-phase12.sh` PASS ≥ 15 / 18
- Zero auth-related errors in memory-api logs over a 6h window

DO NOT REVOKE if any of these fail.

The LibreChat OAuth App (`xbrain LibreChat`, Client ID
`Ov23li0XHV3NL8Git7Dk`) is **explicitly preserved** — it powers
LibreChat's own GitHub social login + `/api/xbrain/github-repos` proxy
and is out of scope per `12-CONTEXT.md`.

---

## Troubleshooting

| Symptom                                                       | Likely cause                                                              | Fix                                                                                       |
| ------------------------------------------------------------- | ------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Webhook delivers but installations row absent                 | `GITHUB_APP_WEBHOOK_SECRET` mismatch between App settings and `.env`     | Recent Deliveries tab shows 401 — copy the correct secret to `.env` + restart memory-api  |
| Sign-in returns "GitHub App OAuth not configured"             | `GITHUB_APP_CLIENT_ID` or `GITHUB_APP_CLIENT_SECRET` empty on VM         | Re-source the secret from Step 3, edit `.env`, restart memory-api                         |
| `mint_app_jwt` fails with "PEM does not look like"            | `GITHUB_APP_PRIVATE_KEY_B64` is multi-line                                | Re-encode with `base64 -w 0` (or `tr -d '\n'`) — single line required                     |
| `chromiumapp.org` redirect_uri_mismatch in chrome ext         | Computed extension ID doesn't match what's registered on the App         | Re-derive the ID, check `manifest.json` `"key"` deployed correctly, edit App callbacks   |
| `link-github` (LibreChat) returns 500                         | `check_github_org_membership` signature mismatch (B-2 fix not deployed) | Verify Plan 12-04 shipped on the VM — `me_github.py` must call with `(session, login)` signature |
| `verify-phase12.sh` assertion 6 fails (GITHUB_API_PAT refs)   | A test file still imports or sets the var                                | grep the offending file, remove the ref (it's dead post-Plan 12-04)                       |
| All sign-ins return install_required even when org installed  | webhook never fired or DB row got deleted                                 | Run `find_installation_for_org` manually via Python REPL — confirms hybrid lookup works; if so, webhook config wrong |
| User token refresh returns 401 with `bad_refresh_token`       | Single-use refresh token already consumed on a prior call                | User must re-authorize (Step 1 of UAT). Document as expected if it happens at 6mo mark    |

---

## Future maintenance

- **App ownership transfer** to a dedicated org (e.g. `xbrain-app`):
  GitHub supports App transfer without breaking existing installations
  per the [transfer docs](https://docs.github.com/en/apps/maintaining-github-apps/transferring-ownership-of-a-github-app).
  Re-derive callback URLs only if the App slug changes.
- **Private key rotation:** generate a new PEM, base64-encode, swap
  the env var, restart memory-api. The old PEM is then invalidated.
  Document the rotation date.
- **Webhook secret rotation:** edit on GitHub App settings AND `.env`
  simultaneously. There's a brief window where mid-flight deliveries
  may fail — re-deliver from Recent Deliveries if needed.
- **`FERNET_KEY` rotation:** invalidates ALL stored GitHub user tokens
  AND hashes. Every user re-authorizes. Communicate this to users
  before rotating.

---

## References

- `12-CONTEXT.md` (locked decisions)
- `12-RESEARCH.md` §Q2 (refresh token enablement), §Q3 (installation lookup)
- `.planning/KB/github-app-architecture.md` (architectural overview)
- `.planning/KB/oauth-app-revocation.md` (legacy OAuth App cleanup)
- `.planning/KB/chrome-extension-key.md` (extension ID derivation)
- Plans 12-08 + 12-09 (chrome ext + app-site client_id swap)
- `infrastructure/scripts/verify-phase12.sh` (automated assertions)
