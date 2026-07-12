# xbrain GitHub App — architecture (Phase 12)

Internal dev KB. Written 2026-05-17 alongside Plan 12-11 (Phase 12 shipping
plan). Audience: future devs touching GitHub auth or extending it.

Public user-facing docs live at `marketing-site/docs/github-auth.html`.
This file describes the *implementation* — schema, the 3-token taxonomy,
hybrid installation lookup, refresh flow, webhook handler shape, token
persistence + indexed lookup, env naming, and the OAuth-App→GitHub-App
migration history.

---

## 1. Why a GitHub App (vs OAuth App)

xbrain was on OAuth App `xbrain` (Client ID `Ov23liy7tZekl0uEztoj`) from
Phase 5 through Phase 11. OAuth Apps have three structural limits that
broke Phase 12-era requirements:

| OAuth App limit                                      | Phase 12 requirement                                |
| ---------------------------------------------------- | --------------------------------------------------- |
| ONE callback URL per App                             | Web app + Chrome extension + (future) MCP frontends |
| User token lifetime is unbounded (no refresh)        | Public deployment needs explicit token expiry      |
| Org membership reads via a long-lived PAT in env     | Per-installation short-lived tokens with rate limit |
| No installation events / webhooks                    | Org admins want "Install xbrain on our org" UX     |

GitHub Apps natively support multi-callback, installation tokens, refresh
tokens, and installation webhooks — see [GitHub docs](https://docs.github.com/en/apps/creating-github-apps/about-creating-github-apps/about-creating-github-apps).
The Phase 12 migration is a clean break (no dual-auth), with the single
existing user (mrboups) re-authorizing once via the new App.

The legacy OAuth App `xbrain LibreChat` (Client ID `Ov23li0XHV3NL8Git7Dk`)
stays untouched — it's a separate App used by LibreChat for its own
GitHub social login and is explicitly out of scope per `12-CONTEXT.md`.

---

## 2. The three token types (CRITICAL)

Per `12-RESEARCH.md` §Pattern 1 — these tokens are NOT interchangeable.
Confusing them is the #1 source of bugs in GitHub-App-backed services.

| Token                       | Prefix       | TTL         | Use                                                                  |
| --------------------------- | ------------ | ----------- | -------------------------------------------------------------------- |
| App JWT                     | `eyJ...`     | 10 min      | Mint installation tokens; `/orgs/{org}/installation`; `/user/{id}`   |
| Installation access token   | `ghs_...`    | 1 hour      | Server-to-server: org membership, repo writes (unused in v1)         |
| User-to-server access token | `ghu_...`    | 8 hours     | Act as a user: `/user`, `/user/orgs` (private orgs visible)          |
| User-to-server refresh      | `ghr_...`    | 6 months    | Single-use refresh to mint a fresh `ghu_` + new `ghr_` pair          |

Helper modules (all under `apps/memory-api/app/services/`):

- `github_app_jwt.mint_app_jwt()` — RS256 JWT, signed by
  `GITHUB_APP_PRIVATE_KEY_B64`, `iss = GITHUB_APP_CLIENT_ID`, `iat-60`
  for clock skew, `exp = iat + 600`.
- `github_installation.get_installation_token(installation_id)` — cached
  ghs_ token; bypasses cache with `force_refresh=True`.
- `github_installation.get_installation_token_for_org(session, org, force_refresh=False)`
  — convenience wrapper that runs the hybrid lookup first.
- `github_user_token.refresh_user_token_if_needed(session, user)` —
  ghu_ rotation behind a per-user `asyncio.Lock`.

The hard rule: **never use an App JWT where the API expects an
installation token, or vice versa**. GitHub returns 401 with a
non-obvious error string if you do.

---

## 3. Server-side stack

- **JWT library:** `PyJWT[crypto]>=2.10` (pinned in
  `apps/memory-api/pyproject.toml`). Verified by `verify-phase12.sh`
  assertion 8.
- **Installation token cache:** in-process dict keyed on `installation_id`,
  per-instance. Multi-instance memory-api would race on first-mint — see
  §Known limitations.
- **Refresh race:** per-user `asyncio.Lock` ensures one refresh per user
  per process under concurrent requests (e.g. two API calls hitting the
  expiry boundary simultaneously). The lock is re-entrant safe because
  `refresh_user_token_if_needed` re-checks `expires_at` inside the lock
  before issuing the network call. Reference: `12-RESEARCH.md` Pitfall 6.
- **HTTP client:** `httpx.AsyncClient` (already in stack). Webhook
  signature verification uses `hmac.compare_digest` (timing-safe).

Wiring summary — `apps/memory-api/app/routes/auth_github.py::signin_github`:

```
1. Exchange OAuth code for ghu_ access token + ghr_ refresh token.
2. GET /user with the ghu_ token → resolve github_id + github_username.
3. UPSERT users row keyed on github_id (cross-frontend identity).
4. Encrypt + hash both tokens, write to users.github_*_enc / _hash.
5. GET /user/orgs with the ghu_ token → list of orgs user can see.
6. For each org, find_installation_for_org(...) (hybrid lookup).
   - If org has installation AND user is member AND not in
     team_org_blocks → auto-grant team membership.
   - Else mark install_required = True, set install_url + org_login.
7. Mint xbrain xbt_ token for this user → return.
```

---

## 4. Client-side stack

The chrome extension and the web app share the same GitHub App
`client_id` because GitHub Apps support multiple callback URLs (the
single OAuth App constraint that drove Phase 12 in the first place).

```
GITHUB_APP_CLIENT_ID = "Iv23liVnZvIN0Lo6isof"
  ↓
app-site/account/teams/teams.js     →  const GITHUB_CLIENT_ID = "Iv23liVnZvIN0Lo6isof"
chrome-extension/background.js       →  const GITHUB_CLIENT_ID = "Iv23liVnZvIN0Lo6isof"
```

The chrome extension uses `manifest.json` `"key"` to pin the
`chrome.runtime.id` to `anigikcnmldoklcmogffmgcojdhhficb` across all
unpacked installs. This is critical: the App's callback URL must be a
literal `https://<ext-id>.chromiumapp.org/` — if the ID drifts across
dev machines, the callback fails with `redirect_uri_mismatch`.

Procedure to (re-)derive the extension ID lives in
`.planning/KB/chrome-extension-key.md`.

---

## 5. Install-required UX flow

When `signin_github` discovers the user's primary org has NOT installed
the xbrain App, the response shape is:

```json
{
  "xbt_token": null,
  "install_required": true,
  "install_url": "https://github.com/apps/xbrain/installations/new?state=<...>",
  "org_login": "<org_login>"
}
```

The web app renders a yellow banner with the literal org_login
("Install xbrain on `dejavudev`") and an Install button pointing to
`install_url`. The banner DOM slot lives in `app-site/account/teams/
index.html`; the JS wiring is in `app-site/account/teams/teams.js`.

After the GitHub install consent screen (or "approval required" if the
user is not an org admin), GitHub redirects to:

```
https://example.com/account/teams/?installation_id=<int>&setup_action=install&state=<orig>
```

The page re-fires the signin flow. The `find_installation_for_org`
hybrid lookup may still need to backfill the row if the webhook hasn't
arrived yet — see §6.

---

## 6. Webhook handler

`POST /v1/webhooks/github/installation` (`apps/memory-api/app/routes/
webhooks_github.py`):

1. Read the raw body BEFORE any Pydantic parsing — HMAC verification
   needs the exact byte sequence GitHub signed (RESEARCH Pitfall 5:
   pydantic re-serializes JSON with stable ordering that may differ
   from the raw payload).
2. Compute `sha256=` HMAC with `GITHUB_APP_WEBHOOK_SECRET`.
3. `hmac.compare_digest` against `X-Hub-Signature-256`. Mismatch → 401.
4. Dispatch on `X-GitHub-Event` + `payload.action`:
   - `installation.created` / `installation.new_permissions_accepted` →
     `upsert_installation(installation_id, github_org_login,
     permissions, raw_payload)`.
   - `installation.deleted` → `mark_revoked(installation_id)` (sets
     `revoked_at = NOW()`).
   - `installation.suspend` → set `suspended_at = NOW()`.
   - `installation.unsuspend` → set `suspended_at = NULL`.
   - `installation_repositories` → log only (no repo perms used in v1).
5. Return 200/204 on success, 401 on signature mismatch.

Repo helpers live in `apps/memory-api/app/repos/installations.py`.

### Hybrid installation lookup (self-heal)

`find_installation_for_org(session, org_login)`:

```
1. DB lookup: SELECT FROM installations WHERE github_org_login=$1
              AND revoked_at IS NULL LIMIT 1.
2. On hit:  return id.
3. On miss: mint App JWT → GET /orgs/{org}/installation.
              - 200 → upsert the row (backfill) + return id.
              - 404 → return None (caller sets install_required).
```

This self-heals missed webhooks (RESEARCH Pitfall 2). If the webhook
arrives later, the upsert is idempotent (same `installation_id` →
preserves the row).

---

## 7. Token persistence at rest

`users` columns added by migration `0019_github_app_install`:

| Column                           | Type        | Purpose                                                                 |
| -------------------------------- | ----------- | ----------------------------------------------------------------------- |
| `github_access_token_enc`        | TEXT        | Fernet-encrypted ghu_ token (base64-encoded ciphertext)                 |
| `github_refresh_token_enc`       | TEXT        | Fernet-encrypted ghr_ refresh token                                     |
| `github_token_expires_at`        | TIMESTAMPTZ | Expiry of the ghu_ token (used by refresh-needs-fire predicate)         |
| `github_refresh_expires_at`      | TIMESTAMPTZ | Expiry of the ghr_ refresh token (6 months from issuance)               |
| `github_access_token_hash`       | TEXT        | HMAC-SHA256 hex of the plaintext ghu_ token — 64 chars, indexed         |

A partial unique index `idx_users_github_access_token_hash_active`
(`WHERE github_access_token_hash IS NOT NULL`) provides O(log n) lookup
in `apps/memory-api/app/deps.py` when an incoming request carries a
`ghu_` Bearer token. The HMAC keyspace is 2^256 → collision probability
is negligible; the deps.py path then decrypts the encrypted column as
defence-in-depth and rejects on plaintext mismatch.

The `FERNET_KEY` env (introduced in Phase 4 for drive-sync) is shared
for both token encryption AND the hash HMAC. Rotating `FERNET_KEY`
invalidates ALL stored GitHub user tokens and hashes — every user
re-authorizes. This is acceptable but the operator runbook documents it
as a "do not rotate without comms" step.

**Verified:** `grep -r "GITHUB_API_PAT" apps/memory-api/app/ apps/memory-api/tests/
--include="*.py"` (excluding comment lines) returns 0 matches.
`verify-phase12.sh` assertion 6 re-runs this check on every deploy.

---

## 8. Migration history (OAuth App → GitHub App)

```
Phase 5  →  OAuth App "xbrain" created (Client ID Ov23liy7tZekl0uEztoj)
            One callback: https://example.com/account/teams/
            GITHUB_API_PAT in env for /orgs/{org}/members/{username}
            Chrome extension flow broken (single-callback limit)

Phase 10 →  Auto-grant + org membership checks formalized
            blocked_at + team_org_blocks added (Phase 10 success #5)
            GITHUB_API_PAT still in env, still long-lived

Phase 12 →  GitHub App "xbrain" created (Client ID Iv23liVnZvIN0Lo6isof)
            Multi-callback: https://example.com/account/teams/
                          + https://<ext-id>.chromiumapp.org/
            App JWT + installation tokens replace GITHUB_API_PAT
            Refresh token flow (ghu_/ghr_) for user-to-server tokens
            Webhook handler /v1/webhooks/github/installation
            mrboups re-authorized once (the only user pre-launch)
            Chrome extension manifest "key" pins the extension ID
            OAuth App "xbrain" revocation runbook gated 24h post-LIVE
```

The chrome extension's `chrome.runtime.id`
(`anigikcnmldoklcmogffmgcojdhhficb`) was derived from the public key in
`manifest.json` `"key"`. Replacing the keypair regenerates the ID, which
invalidates the callback URL registered on the App. The recovery is
operator-side: re-derive the ID and edit the App's callback URLs.

---

## 9. Env vars (Phase 12)

| Var                          | Purpose                                                            |
| ---------------------------- | ------------------------------------------------------------------ |
| `GITHUB_APP_ID`              | Numeric App registration ID (Phase 12 = 3743573)                   |
| `GITHUB_APP_SLUG`            | URL slug for `github.com/apps/<slug>/installations/new`            |
| `GITHUB_APP_CLIENT_ID`       | `Iv23li...` — used as JWT `iss` AND OAuth client_id                |
| `GITHUB_APP_CLIENT_SECRET`   | Paired OAuth secret (signin code-exchange)                         |
| `GITHUB_APP_PRIVATE_KEY_B64` | Base64-encoded PEM (single line) for RS256 signing                 |
| `GITHUB_APP_WEBHOOK_SECRET`  | HMAC-SHA256 secret for `X-Hub-Signature-256` verification          |

REMOVED in Phase 12 (Plan 12-04 — verified by `verify-phase12.sh` assertion 6):

- `GITHUB_API_PAT` — replaced by App JWT + installation tokens.
- `GITHUB_ORG_PAT` — same.

PRESERVED but LibreChat-only (NOT part of Phase 12 migration):

- `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` — the `xbrain LibreChat`
  OAuth App. Consumed exclusively by `app/routes/me_github.py` (the
  `/v1/me/link-github` flow). Phase 13+ may migrate LibreChat too.

---

## 10. Failure modes summary

| Failure                                | User-visible            | Recovery                                          |
| -------------------------------------- | ----------------------- | ------------------------------------------------- |
| Refresh token expired (6mo)            | 401 "re-authorize"      | User clicks Sign In again                         |
| App uninstalled mid-session            | Membership check 404    | Banner shows install_required                     |
| Missed installation webhook            | install_required on first signin | Hybrid lookup backfills row within 1 request |
| Installation token revoked mid-use     | 401 on /orgs/...        | One force_refresh + retry, then surface as 401    |
| FERNET_KEY rotated                     | All sessions invalidated | Single re-authorization per user                  |
| Webhook signature mismatch (rotation)  | Silent webhook drop     | Operator updates `GITHUB_APP_WEBHOOK_SECRET`      |
| Chrome ext ID drift (lost keypair)     | redirect_uri_mismatch   | Re-derive ID, edit App callbacks, redeploy ext    |

---

## 11. Known limitations (deferred to Phase 13+)

- **In-process installation token cache.** Multi-instance memory-api
  would race on first-mint per installation. Phase 13 candidate:
  Postgres advisory lock or Redis-backed cache.
- **`installation_target` event NOT subscribed.** If an org renames
  itself on GitHub, `installations.github_org_login` rows orphan.
  Deferred per `12-RESEARCH.md` §Q13. Mitigation: periodic reconciliation
  script that calls `/installation/repositories` for each row.
- **`my_github_orgs` uses App JWT** (`/users/{username}/orgs`) which
  only returns PUBLIC org memberships. Users in private orgs see an
  incomplete list. Phase 13: switch to user `ghu_` token for this path.
- **No bulk operations** on installations admin surface (revoke many at
  once, etc.). Single-row only in v1.
- **LibreChat OAuth App un-migrated.** `xbrain LibreChat` still uses
  OAuth App + manual link-github flow. Phase 13 candidate.

---

## 12. References

- `marketing-site/docs/github-auth.html` — public-facing description
- `.planning/KB/github-app-operator-runbook.md` — manual setup steps
- `.planning/KB/oauth-app-revocation.md` — legacy OAuth App cleanup
- `.planning/KB/chrome-extension-key.md` — chrome ext ID derivation
- `12-RESEARCH.md` — full research artefact (3-token taxonomy, 7 pitfalls)
- `12-CONTEXT.md` — locked decisions (App ownership, perms, sequencing)
- `infrastructure/scripts/verify-phase12.sh` — 18 automated assertions
- `12-UAT.md` — 9-step manual checklist
