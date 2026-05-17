# Phase 12 SUMMARY

**Status:** SHIPPED | VERIFY-PASS | UAT-PASS — pending operator ship-out
**Verified:** _____________________
**verify-phase12.sh:** PASS: __ / 18 (SKIPPED: __)
**12-UAT.md:** __ / 9 steps PASS
**LIVE in ROADMAP marked by:** _____________________

This file is a **template**. The operator running ship-out fills the
fields above and the verification subsections below after `verify-phase12.sh`
and `12-UAT.md` complete.

---

## What shipped

### Schema and data model

- Migration `0019_github_app_install` — `installations` table (10 columns
  including `installation_id` PK, `github_org_login`, `revoked_at`,
  `suspended_at`, `permissions JSONB`, `raw_payload JSONB`) + 5 new
  token columns on `users` (`github_access_token_enc`,
  `github_refresh_token_enc`, `github_token_expires_at`,
  `github_refresh_expires_at`, `github_access_token_hash`). Chained
  from Phase 11 head `0018_brain_events_view`.
- Partial unique index `idx_installations_org_login_active`
  (`WHERE revoked_at IS NULL`) — ensures a single active installation
  per org while preserving revoked history.
- Partial unique index on `users.github_access_token_hash`
  (`WHERE github_access_token_hash IS NOT NULL`) — O(log n) lookup for
  the deps.py `ghu_` Bearer branch.

### Memory-api dependencies

- `PyJWT[crypto] >= 2.10` added to `apps/memory-api/pyproject.toml` for
  RS256 JWT signing (mints App JWT).

### Services and helpers

- `apps/memory-api/app/services/github_app_jwt.py` — `mint_app_jwt()`
  helper. Loads `GITHUB_APP_PRIVATE_KEY_B64`, signs RS256 JWT with
  `iss=GITHUB_APP_CLIENT_ID`, 10-minute TTL, `iat-60` clock skew.
- `apps/memory-api/app/services/github_installation.py` —
  `get_installation_token(id, force_refresh=False)`,
  `get_installation_token_for_org(session, org, force_refresh=False)`,
  `find_installation_for_org(session, org)` (hybrid DB+API lookup).
  In-process cache keyed on `installation_id`.
- `apps/memory-api/app/services/github_user_token.py` —
  `refresh_user_token_if_needed(session, user)` with per-user
  `asyncio.Lock` for refresh race protection. Rotates both encrypted
  tokens AND `github_access_token_hash` atomically.
- `apps/memory-api/app/services/token_crypto.py` — Fernet encrypt /
  decrypt helpers + `token_lookup_hash(plaintext)` HMAC-SHA256 hex.
  Reuses Phase 4 `FERNET_KEY`.

### Routes

- `apps/memory-api/app/routes/webhooks_github.py` — `POST /v1/webhooks/
  github/installation` with HMAC-SHA256 verification (`hmac.compare_digest`,
  raw body BEFORE Pydantic parsing per RESEARCH Pitfall 5). Dispatches
  on `X-GitHub-Event` + `payload.action` for `installation.created`,
  `installation.deleted`, `installation.suspend`, `installation.unsuspend`,
  `installation.new_permissions_accepted`. Logs `installation_repositories`
  (no repo perms used in v1).
- `apps/memory-api/app/routes/auth_github.py::signin_github` — rewritten
  to 10 steps including the install-required branch. Returns
  `SigninGithubOut` with `install_required`, `install_url`, `org_login`
  populated when the user's primary org has NOT installed the App.
- `apps/memory-api/app/routes/me_github.py::link_github` — migrated to
  the new `check_github_org_membership(session, login)` signature
  (Phase 5 LibreChat link-github flow preserved — B-2 regression fix).
- `apps/memory-api/app/routes/teams.py` — 4 endpoints migrated from
  `GITHUB_API_PAT` to App JWT + installation tokens.
- `apps/memory-api/app/auth.py::check_github_org_membership` — rewritten
  to fetch the installation, mint the installation token, call
  `/orgs/{org}/members/{username}` with the ghs_ token, retry on 401
  with `force_refresh=True`. `GITHUB_API_PAT` no longer in the path.

### Repos

- `apps/memory-api/app/repos/installations.py` — `upsert_installation`,
  `mark_revoked`, `mark_suspended`, `mark_unsuspended`, `update_perms`,
  `find_by_org_login`. Helpers for webhook handler + hybrid lookup.

### Deps.py

- `apps/memory-api/app/deps.py` — `ghu_` Bearer branch: HMAC-hash lookup
  against `github_access_token_hash` (O(log n)) + defence-in-depth
  decrypt-and-compare. Transparent refresh triggered if `expires_at <
  NOW() + 5min`.

### Front-end — app-site

- `app-site/account/teams/teams.js` — `GITHUB_CLIENT_ID` constant swapped
  to `Iv23liVnZvIN0Lo6isof`. Install banner UX added (reads
  `install_required` / `install_url` / `org_login` from response).
- `app-site/account/teams/index.html` — install-banner DOM slot added.
- Firebase deploy on the `app` target.

### Front-end — Chrome extension

- `chrome-extension/manifest.json` — added 2048-bit RSA public key
  (`"key"` field) to pin `chrome.runtime.id` to
  `anigikcnmldoklcmogffmgcojdhhficb`.
- `chrome-extension/background.js` — `GITHUB_CLIENT_ID` constant swapped
  to `Iv23liVnZvIN0Lo6isof`.

### Configuration

- `.env.example` — added 6 `GITHUB_APP_*` vars. Comment annotations marking
  `GITHUB_API_PAT` / `GITHUB_ORG_PAT` as DEPRECATED.
- `docker-compose.yml` — wires the new vars into the `memory-api`
  container.
- `apps/memory-api/app/config.py::Settings` — added 6 GITHUB_APP_*
  fields; raises if any required field is empty at startup.

### Documentation

- `marketing-site/docs/github-auth.html` — updated by Plan 12-10 to
  describe the Phase 12 GitHub App architecture (3-token model,
  installation tokens, multi-callback).
- `.planning/KB/github-app-architecture.md` — internal architecture KB
  (12 sections: why-App, 3-token taxonomy, server stack, client stack,
  install-required UX, webhook handler, token persistence, migration
  history, env vars, failure modes, known limitations, references).
- `.planning/KB/github-app-operator-runbook.md` — manual setup runbook
  (8 steps + troubleshooting + future maintenance).
- `.planning/KB/oauth-app-revocation.md` — legacy OAuth App `xbrain`
  revocation runbook, gated 24h post-LIVE.
- `.planning/KB/chrome-extension-key.md` — manifest key generation
  procedure (created in Plan 12-08 ship).
- `infrastructure/scripts/verify-phase12.sh` — 18 automated assertions
  (schema, env, code gates, runtime probes, SC-5 regression).
- `infrastructure/.gitattributes` (root) — pins `*.sh` to `eol=lf` so
  the Phase 11 CRLF break (commit `dc9a74c`) cannot recur on fresh
  Windows checkouts.
- `.planning/phases/12-github-app-migration-public-deployment-ready-auth/12-UAT.md`
  — 9-step manual checklist.

### Tests (memory-api)

- `tests/test_phase12_jwt.py` — mint_app_jwt RS256 sanity.
- `tests/test_phase12_installation.py` — cache hit / miss / force_refresh.
- `tests/test_phase12_hybrid_lookup.py` — DB hit, DB miss + GitHub 200
  backfills, GitHub 404 returns None.
- `tests/test_phase12_webhook.py` — HMAC reject on missing/wrong sig,
  accept on correct sig, upsert installation row.
- `tests/test_phase12_signin_install_flow.py` — install_required branch
  shape (M-1 + SC-2 coverage).
- `tests/test_phase12_auto_grant_regression.py` — SC-5 regression
  (blocked github_login on installed org cannot auto-join, B-3 fix).
- `tests/test_phase12_refresh.py` — refresh rotation + per-user lock
  + at-rest encryption + hash update.
- `tests/test_phase12_deps_ghu_lookup.py` — O(log n) hash-indexed
  lookup + defence-in-depth decrypt.

---

## Verification

### verify-phase12.sh

Paste the final summary line here after running on the VM:

```
PASS: __ / __ (SKIPPED: __)
```

Expected: `PASS: 18 / 18` with all fixtures exported, OR
`PASS: 15 / 18 (SKIPPED: 3)` if `TEST_BLOCKED_LOGIN`, `TEST_INSTALLATION_ID`,
`TEST_GITHUB_ORG`, or `GITHUB_APP_WEBHOOK_SECRET` are not set.

### 12-UAT.md

Step-by-step results (fill after the operator walks the checklist):

| Step | Description                                    | Result   | Notes                                           |
| ---- | ---------------------------------------------- | -------- | ----------------------------------------------- |
| 1    | Web sign-in happy path                         | ____     |                                                 |
| 2    | Web sign-in install-required branch            | ____     | SKIP if no second user available                |
| 3    | Chrome extension sign-in                       | ____     |                                                 |
| 4    | Webhook delivery (install/uninstall cycle)     | ____     |                                                 |
| 5    | Refresh token rotation                         | ____     |                                                 |
| 6    | Hybrid lookup self-heal                        | ____     |                                                 |
| 7    | LibreChat link-github regression               | ____     | Phase 5 flow must remain green (B-2)            |
| 8    | Multi-frontend independence                    | ____     |                                                 |
| 9    | OAuth App revocation gate (24h+)               | ____     | Deferred — do NOT run on the same day as ship   |

---

## Known issues / followups

- `_github_installation_cache` is in-process — multi-instance memory-api
  would race on first-mint per installation. Phase 13+ candidate:
  Postgres advisory lock or Redis-backed cache.
- `installation_target` (org rename) event NOT subscribed — org renames
  would orphan `installations.github_org_login` rows. Deferred per
  `12-RESEARCH.md` §Q13. Mitigation: periodic reconciliation script.
- LibreChat OAuth App (`xbrain LibreChat`, Client ID
  `Ov23li0XHV3NL8Git7Dk`) NOT migrated — separate concern. The
  `/v1/me/link-github` flow on `app/routes/me_github.py` still uses
  the legacy `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` env vars.
- Phase 4 `FERNET_KEY` is reused for token encryption AND hash HMAC.
  Rotating it invalidates ALL stored GitHub user tokens AND hashes —
  users re-authorize. Documented in the operator runbook.
- `GITHUB_ORG` env still defaults to `your-github-org` in `config.py`
  despite the real org being `dejavudev`. Cosmetic — actual value is
  set in `.env` on the VM. Deferred cleanup.
- `my_github_orgs` (in `teams.py`) uses App JWT for `/users/{username}/orgs`
  — returns only PUBLIC orgs. For users in private orgs, Phase 13
  should switch to the user `ghu_` token for that endpoint.
- Chrome Web Store publication deferred (manifest "key" pins the
  unpacked-install ID for now). Phase 13+ marketing/launch.
- OAuth App `xbrain` (Client ID `Ov23liy7tZekl0uEztoj`) revocation
  deferred 24h post-LIVE per the runbook gate.

---

## Decisions made

- **GitHub App ownership:** Personal account `mrboups` (matches OAuth
  Apps pattern since Phase 5; transfer to dedicated org deferred).
- **Permissions granularity:** Minimal — Email (Read) + Profile (Read)
  + Members (Read). No repository perms in v1.
- **Clean break migration:** No dual-auth code path; mrboups re-authorized
  once via the new GitHub App. Acceptable UX cost for the single
  pre-launch user.
- **Chrome extension ID strategy:** Manifest `"key"` field for
  deterministic ID on unpacked installs. Web Store publish deferred.
- **Refresh token expiration:** Enabled in App settings ("Optional features
  → User-to-server token expiration"). Required for Phase 12 (otherwise
  no `ghr_` refresh tokens are issued).
- **Token persistence:** Fernet at rest + HMAC-SHA256 hash for O(log n)
  indexed lookup. Defence-in-depth decrypt-and-compare in deps.py.
- **Per-user `asyncio.Lock` for refresh races:** Single-instance OK;
  multi-instance is a Phase 13 concern.
- **Hybrid installation lookup:** DB first, App-JWT fallback. Self-heals
  missed webhooks within a single request.

---

## Phase 12 success criteria — final status

| SC | Description                                                            | Met? | Evidence                                                            |
| -- | ---------------------------------------------------------------------- | ---- | ------------------------------------------------------------------- |
| 1  | New GitHub App live; mrboups can sign in via web                       | ____ | UAT Step 1                                                          |
| 2  | Install banner surfaces when org not installed                         | ____ | UAT Step 2 (or unit test if no 2nd user)                            |
| 3  | Webhook handler creates installations row on org install               | ____ | UAT Step 4 + verify assertion 13                                    |
| 4  | Chrome extension flow works with deterministic ID                      | ____ | UAT Step 3 + verify assertion 15                                    |
| 5  | Blocked github_login cannot auto-join even on installed org            | ____ | UAT Step 2 (negative) + unit test + verify assertion 18 (B-3 fix)   |
| 6  | User-to-server token refresh transparently rotates                     | ____ | UAT Step 5                                                          |
| 7  | `GITHUB_API_PAT` removed from runtime config + code                    | ____ | verify assertion 6 (M-6 fix)                                        |
| 8  | OAuth App `xbrain` revocation runbook written + executed (24h+)        | ____ | UAT Step 9 + `.planning/KB/oauth-app-revocation.md`                 |

---

## Next phase

Phase 13 TBD. Candidate scope (none committed):

- LibreChat OAuth App migration (`xbrain LibreChat` → second GitHub App,
  or unification under the Phase 12 App's installation tokens for
  `/api/xbrain/github-repos`).
- Chrome Web Store publication of the extension (replace manifest `"key"`
  with the Web Store-issued public key).
- Multi-instance memory-api — Postgres advisory locks or Redis-backed
  cache for refresh + installation token races.
- `my_github_orgs` private-org support (switch to user `ghu_` token).
- `installation_target` event subscription for org renames.
- `created_by` backfill on `memory_items` / `contacts` / `messages`
  (Phase 11 leftover lifting the admin-only edit restriction on those
  entity types).
- Aggregate audit_log writes on `/v1/admin/brain/*` (Phase 11 leftover).
