# Phase 12 — Context Decisions

**Phase:** 12 — GitHub App Migration (Public-Deployment-Ready Auth)
**Date created:** 2026-05-17
**Status:** Locked decisions captured before research + planning

---

## Locked decisions (2026-05-17)

### Sequencing (locked 2026-05-17)

- **Phase 12 executes AFTER Phase 11** ✅ Phase 11 LIVE 2026-05-17 (commit `dc9a74c`). Entry gate satisfied.
- **Clean break** to GitHub App (NO dual-auth) — only 1 existing user (mrboups), acceptable to re-authorize once via the new GitHub App.

### App ownership

- **Owner:** Personal account `mrboups` (matches the OAuth Apps pattern in place since Phase 5)
- **Why:** Cohérent avec les OAuth Apps actuels (`xbrain`, `xbrain LibreChat`). Owner contrôle l'installation sur ses orgs (ex: `dejavudev`). Transfert vers une org dédiée possible plus tard sans casser les installs (per GitHub docs).
- **NOT chosen:** Dedicated org `xbrain-app` (overhead now, defer to public-launch phase), reuse `dejavudev` (couples app to team org — bad for branding).

### Permissions granularity

- **Choice:** Minimal — `read:org` + `user:email` + `read:user` (match Phase 10 scope set exactly)
- **Why:** Aucune régression de feature. User consent screen reste minimal/familier. La migration ne demande PAS de nouveaux droits à l'utilisateur.
- **NOT chosen:** Fine-grained per-resource (defer to compliance-driven later phase), broader `repo:read` (no use case yet, scope-creep risk).
- **GitHub App permissions mapping** (fine-grained equivalent — to be confirmed during research):
  - Account permissions: Email addresses (Read), Profile (Read)
  - Organization permissions: Members (Read)
  - No Repository permissions in v1

### Chrome extension stable ID strategy

- **Choice:** **Manifest `key` field NOW** (deterministic ID for dev + test users) **+ Chrome Web Store publish DEFERRED** (Phase 13+ marketing/launch)
- **Why:** `key` field guarantees a stable `chrome.runtime.id` for all unpacked installs across machines, so the registered chromiumapp.org callback URL works for everyone immediately. Web Store publish requires review (~3-5j), branding, privacy policy — too early.
- **How:** Generate a 2048-bit RSA keypair, derive the Chrome extension ID from the public key SHA256, add `"key": "<base64-encoded-pub-key>"` to `manifest.json`. The resulting `chrome.runtime.id` will be deterministic.
- **Reference:** [Chrome docs on extension ID generation](https://developer.chrome.com/docs/extensions/reference/manifest/key)

### mrboups migration

- **Choice:** Force re-authorize on next sign-in via new GitHub App (clean, one-click for the single existing user)
- **Why:** Trivial UX cost for 1 user vs. complexity of dual-auth code paths.
- **Data preservation:** `users.github_id` is the PK — mrboups same `github_id` = same user row, same teams, same brain data. Zero data loss.

---

## Inherited from ROADMAP (Phase 12 description)

### Goal (from ROADMAP line 257-258)

Migrate xbrain authentication from OAuth App to GitHub App so the platform is ready for public deployment. GitHub Apps support multiple callback URLs natively (eliminating the per-frontend OAuth App proliferation), use short-lived installation tokens (eliminating long-lived `GITHUB_API_PAT` and unbounded user tokens), enable org-level installation (canonical "Install xbrain on our org" UX instead of per-user authorization with global org-read scope), and unlock higher rate limits per installation. Clean break — no dual-auth maintained; the single existing user (mrboups) re-authorizes once via the new GitHub App.

### Depends on

Phase 11 (Brain Monitor ships first per ordering decision 2026-05-17) ✅ DONE.

### Entry gate

- ✅ Phase 11 SHIPPED.
- ✅ OAuth App `xbrain` (Client ID `Ov23liy7tZekl0uEztoj`) currently authorizes web sign-in only; Chrome extension flow is broken (single-callback constraint) — this is the explicit pain Phase 12 fixes.
- ✅ Existing users: 1 (mrboups).
- ✅ `users.github_id` UNIQUE constraint already in place (Phase 10).
- ✅ `GITHUB_API_PAT` currently used for `/orgs/{org}/members/{username}` checks — must be replaced by installation token.
- ✅ No tests or production users depend on long-lived GitHub OAuth tokens (8h TTL acceptable with refresh token flow).

### Requirements (GHAPP-01 to GHAPP-08, per ROADMAP)

- **GHAPP-01:** Create new GitHub App on `mrboups` account with multi-callback URLs registered: `https://grooveos.app/account/teams/` (web) + `https://<ext-id>.chromiumapp.org/` (Chrome extension stable ID via manifest `key`). Permissions: minimal (per decision above). Generate private key (PEM), store securely server-side.
- **GHAPP-02:** Backend JWT signing infrastructure — load private key from secret, mint JWT signed with RS256 for GitHub App authentication, exchange JWT for installation tokens per installation_id. Cache installation tokens (1h TTL, refresh-on-401).
- **GHAPP-03:** New `installations` table (`installation_id INT PK`, `github_org_login TEXT`, `installed_at TIMESTAMPTZ`, `installed_by_github_id BIGINT`, `permissions JSONB`, `revoked_at TIMESTAMPTZ NULL`) + webhook handler `/v1/webhooks/github/installation` for `installation` and `installation_repositories` events. Sync source-of-truth from GitHub.
- **GHAPP-04:** Migrate `/orgs/{org}/members/{username}` org membership check from `GITHUB_API_PAT` to installation token (lookup installation by `github_org_login`, use cached installation token, fall back to "org not installed → user cannot join team" error). Remove `GITHUB_API_PAT` from `.env.example` and runtime config.
- **GHAPP-05:** User-to-server token expiration handling — implement refresh token flow per [GitHub docs](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/refreshing-user-access-tokens). Store `github_access_token`, `github_refresh_token`, `github_token_expires_at` on users row (migration 0019 or higher). Refresh transparently before any `/user/*` call when token is < 5 min from expiry.
- **GHAPP-06:** Install flow UI — when a user signs in but their primary org has not installed the GitHub App, redirect to GitHub's install URL (`https://github.com/apps/{app_slug}/installations/new`) with `state` for return URL. After install webhook arrives, user can complete team join. Banner messaging in `app-site/account/teams/index.html` and `chrome-extension/popup.html`.
- **GHAPP-07:** Update frontend client_id constants — `app-site/account/teams/teams.js:34` and `chrome-extension/background.js:63` to new GitHub App client_id. With GitHub App's multi-callback support, the same client_id serves both flows (no per-frontend dispatch in memory-api). Add fixed `key` in `chrome-extension/manifest.json` so `chrome.runtime.id` is deterministic.
- **GHAPP-08:** Remove OAuth App `xbrain` (Client ID `Ov23liy7tZekl0uEztoj`) from active code path. Delete OAuth-App-specific dispatch logic in `apps/memory-api/app/routes/auth_github.py`. Document migration in `docs/auth.html`. (LibreChat-specific OAuth App `xbrain LibreChat` Client ID `Ov23li0XHV3NL8Git7Dk` remains untouched — separate concern.)

### Success criteria (8, per ROADMAP)

See ROADMAP §"Phase 12: GitHub App Migration" — verbatim.

---

## Open questions for research

These should be resolved by the gsd-phase-researcher (RESEARCH.md) before planning:

1. **GitHub App permission mapping** — confirm the exact fine-grained permission names that map to the requested OAuth scopes (`read:org` + `user:email` + `read:user`). GitHub's mapping doc may have edge cases.
2. **Refresh token enablement** — GitHub Apps support user-to-server refresh tokens only when "Expire user authorization tokens" is enabled in app settings. Confirm + document.
3. **Installation lookup latency** — how to discover installation_id for a given org_login efficiently (via webhook cache OR via `/orgs/{org}/installation` endpoint with app JWT). Trade-offs.
4. **Webhook signature verification** — `X-Hub-Signature-256` HMAC pattern. Confirm Python implementation reference.
5. **JWT signing library choice** — `pyjwt` vs `cryptography` direct usage. Stack consistency with rest of project.
6. **Chrome extension key generation** — exact steps to derive the `chrome.runtime.id` from a generated public key (32-char hex prefix, lowercase a-p alphabet). Confirm formula.
7. **Migration order on the live VM** — must old OAuth App stay registered + functional during the deploy window? Or can it be revoked immediately? (Per decision: clean break, so revoke after deploy.)
8. **First-install UX for mrboups** — when mrboups signs in post-deploy, does the GitHub App need to be "installed on `dejavudev`" before he can land on his team page? Or does individual user authorization suffice for the team-membership read? Critical for the install flow UX.
9. **Existing `auth_github.py` blast radius** — how much of the OAuth-App code path is reusable for GitHub-App user-to-server flow (same `/login/oauth/authorize` + `/login/oauth/access_token` endpoints), vs how much must be rewritten?
10. **Test fixtures** — how to mock a GitHub App JWT + installation token in pytest without hitting real GitHub. Pattern reference (existing tests use httpx mocks).

---

## Out of scope for Phase 12

- LibreChat OAuth App migration (`xbrain LibreChat` Client ID `Ov23li0XHV3NL8Git7Dk`) — separate concern, untouched.
- Chrome Web Store publication — deferred to a later phase (marketing/launch).
- Repository-level access (`repo:read`) — deferred until a feature actually needs it (sync brain from repo, brain.yaml extraction).
- GitHub App branding (logo, description for the install screen) — minimal placeholder OK for v1; polish during public-launch phase.
- Migration of the `dejavudev` org's auto-grant to use installation events — Phase 10 logic preserved (poll `/user/orgs` at sign-in time using user token). Deferred webhook-driven sync.
