# Phase 10 — RESEARCH

**Researched:** 2026-05-13
**Phase:** 10 — GitHub-Primary Auth + Org-Driven Team Membership
**Scope:** Plan-time research (consumed by gsd-planner)

---

## Executive Summary

- **Option B (full-page redirect) is the correct OAuth flow for the static app-site.** No callback page needed; code arrives as a query param, is exchanged by memory-api, and the URL is cleaned with `history.replaceState`. [HIGH confidence — verified against GitHub OAuth docs]
- **`read:user user:email read:org` scopes are already requested by the Chrome extension** (`background.js:69`). No new OAuth app config is required; the same scopes will be added to the app-site flow. [HIGH — code verified]
- **The auto-merge involves 10 FK references to `users.id`** spread across 8 tables. `CASCADE` and `SET NULL` policies already set mean only `team_members`, `conversations`, `promotions`, `team_join_requests`, `granola_user_connections`, `user_api_tokens`, `user_external_sessions` need explicit UPDATE-then-soft-delete; audit rows and `team_messages.author_user_id` are `SET NULL` (no action needed). [HIGH — grep verified]
- **Block enforcement belongs in `get_team_scope`, not `get_membership`** — the membership row must survive intact (with `blocked_at` set) so audit history and future unblock work without data surgery. [MEDIUM — design recommendation]
- **`/user/orgs` requires `read:org` scope and returns BOTH public and private orgs for the authenticated user, with pagination (max 100/page).** The server already uses a PAT for org-member checks; the new auto-grant flow will call `/user/orgs` with the user's own token (not the PAT) to get org list. [HIGH — verified via GitHub docs]

---

## Q1: GitHub OAuth web flow from static site

### Comparison

| | Option A — Popup + postMessage | Option B — Full-page redirect | Option C — Server-side 302 |
|---|---|---|---|
| **Secret exposure** | code handled JS-side before POST to API | code arrives at frontend, immediately POSTed to API | never touches browser JS |
| **Callback page needed** | Yes — `grooveos.app/auth/github/callback` must be Firebase-deployed | No — landing page is `grooveos.app/account/teams/` itself | No |
| **State param / CSRF** | Must be stored in sessionStorage before popup, verified after postMessage | Must be stored in sessionStorage before redirect, verified on return | Server generates + validates internally |
| **Back button / UX** | Popup blocked on mobile and some browsers | Clean redirect — standard behavior everywhere | Clean |
| **Firebase Hosting complexity** | Requires deploying a second page | None beyond query-param handling | Requires new memory-api routes (`/v1/auth/github/login`, `/v1/auth/github/callback`) and a cookie-based session |
| **SPA / static standard** | Less common — more wiring | Industry standard for SPA on Vercel/Netlify/Firebase | Standard for server-rendered apps with session cookies |
| **Fits current architecture** | Neutral | Yes — keeps client_secret on memory-api, no cookie/session | Over-engineered for this page; adds 2 new routes |

**Recommendation: Option B.**

The app-site is already a single-page JS file with localStorage-based auth (`STORAGE_TOKEN`). The pattern:
1. Store CSRF `state` in `sessionStorage`.
2. Redirect to `https://github.com/login/oauth/authorize?client_id=...&redirect_uri=https://grooveos.app/account/teams/&scope=read:user+user:email+read:org&state=...`.
3. On return, detect `?code=...&state=...` in URL, verify state, POST `{code, redirect_uri}` to `POST /v1/auth/github/signin` on memory-api (new endpoint, analogous to the existing `link-github-with-code` but returns an `xbt_` directly).
4. Store `xbt_` in localStorage, remove query params with `history.replaceState({}, '', '/account/teams/')`.

**client_secret never leaves memory-api.** The `redirect_uri` must be registered in the GitHub OAuth App settings (currently only `https://<extension-id>.chromiumapp.org/` is registered — the VM `.env` `GITHUB_CALLBACK_URL` shows `/oauth/github/callback` which is LibreChat's callback). Adding `https://grooveos.app/account/teams/` as an additional authorized redirect URI in the GitHub OAuth App settings is required. [ASSUMED — needs confirmation that mrboups has access to the OAuth app settings for `Ov23liVqXmHkS6JdYpcN`]

**New memory-api endpoint needed:** `POST /v1/auth/github/signin` (no current auth required, public endpoint):
- Body: `{code: str, redirect_uri: str}`
- Exchange code for `gho_` token (same as `link-github-with-code` exchange logic)
- Call `/user` and `/user/emails` to get `github_id`, `login`, primary verified email
- Call `get_or_create_user` with `source_user_id = "github:{login}"`, follow `merged_into_user_id` if set
- Auto-grant org membership (GHA-02)
- Check `team_org_blocks` (GHA-04)
- Mint and return `xbt_`

This reuses the existing `POST /v1/me/api-token` mint logic internally.

[CITED: https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps]

---

## Q2: GitHub `/user/orgs` API

**Scope required:** `read:org` (or broader `user` scope). Fine-grained PATs return an empty list — use classic PAT or OAuth token. [CITED: https://docs.github.com/en/rest/orgs/orgs#list-organizations-for-the-authenticated-user]

**Public vs. private orgs:** The endpoint returns all orgs "that your authorization allows you to operate on in some way" — this includes **private** org memberships for the authenticated user's own token. [CITED: same]

**Pagination:** Supports `per_page` (default 30, max 100) and `page` (default 1). A user with more than 100 orgs needs multiple calls. Implementation must loop until response length < `per_page`.

**Rate limits:** GitHub REST API: 5,000 requests/hour per authenticated user (OAuth token). The PAT used for org-member checks already consumes from the server PAT quota; the new `/user/orgs` call uses the **user's own token**, so it draws from the user's own 5,000/h quota — no collision with server PAT.

**Recommended headers:**
```
Accept: application/vnd.github+json
Authorization: Bearer {gho_token}
X-GitHub-Api-Version: 2022-11-28
```

**Response shape (abbreviated):**
```json
[
  {"login": "your-github-org", "id": 123456, ...},
  {"login": "another-org", "id": 789012, ...}
]
```

**Auto-grant logic:** After `/user/orgs` returns the list of org logins, query `teams WHERE github_org = ANY(org_logins_array) AND github_org IS NOT NULL`. For each matching team, check `team_org_blocks` and existing membership before INSERT into `team_members`.

[CITED: https://docs.github.com/en/rest/orgs/orgs?apiVersion=2022-11-28#list-organizations-for-the-authenticated-user]

---

## Q3: Auto-merge schema

### Complete FK references to `users.id` (verified by grep)

| Table | Column | ON DELETE | Action at merge |
|-------|--------|-----------|-----------------|
| `team_members` | `user_id` | CASCADE | UPDATE to survivor's id |
| `team_join_requests` | `user_id` | CASCADE | UPDATE to survivor's id |
| `conversations` | `owner_user_id` | (no action) | UPDATE to survivor's id |
| `audit_log` | `actor_user_id` | (no action) | Leave as-is (orphan acceptable — audit is immutable history) |
| `memory_promotions` | `proposed_by`, `approved_by_1`, `approved_by_2` | (no action) | UPDATE to survivor's id |
| `tasks` | `created_by` | SET NULL | UPDATE to survivor's id (optional — SET NULL on delete means history preserved) |
| `granola_user_connections` | `user_id` | CASCADE | UPDATE to survivor's id |
| `user_api_tokens` | `user_id` | CASCADE | UPDATE to survivor's id |
| `user_external_sessions` | `user_id` | CASCADE | UPDATE to survivor's id |
| `agent_definitions` | `created_by` | SET NULL | UPDATE to survivor's id (optional) |
| `team_messages` | `author_user_id` | SET NULL | Leave as-is (history) |

Source: grep on `apps/memory-api/app/models/*.py` + `apps/memory-api/alembic/versions/*.py` [VERIFIED: codebase]

### Merge flow

**Key invariant:** When a GitHub-primary login arrives and `github_id` is already on a different user row (the Google-linked row), we must pick one row as the **survivor** and soft-delete the other as the **orphan**.

**Survivor selection:** The Google-linked row (has real email, potential existing team memberships) should be the survivor. The GitHub-only row (orphan: `source_user_id = "github:{login}"`) gets soft-deleted.

**New column on `users` table (migration 0016):**
```sql
ALTER TABLE users ADD COLUMN merged_into_user_id UUID REFERENCES users(id) NULL;
CREATE INDEX idx_users_merged_into ON users(merged_into_user_id) WHERE merged_into_user_id IS NOT NULL;
```

**Migration SQL pattern (inside a transaction):**
```sql
-- survivor_id = the Google-linked row (has github_id already set)
-- orphan_id   = the GitHub-only row (source_user_id = "github:login")

-- 1. Migrate membership rows that DON'T already exist on survivor
INSERT INTO team_members (team_id, user_id, role, joined_at)
SELECT team_id, :survivor_id, role, joined_at
FROM team_members
WHERE user_id = :orphan_id
  AND (team_id, :survivor_id) NOT IN (SELECT team_id, user_id FROM team_members WHERE user_id = :survivor_id)
ON CONFLICT DO NOTHING;

DELETE FROM team_members WHERE user_id = :orphan_id;

-- 2. Migrate other FKs
UPDATE conversations SET owner_user_id = :survivor_id WHERE owner_user_id = :orphan_id;
UPDATE user_api_tokens SET user_id = :survivor_id WHERE user_id = :orphan_id;
UPDATE user_external_sessions SET user_id = :survivor_id WHERE user_id = :orphan_id;
UPDATE granola_user_connections SET user_id = :survivor_id WHERE user_id = :orphan_id;
UPDATE team_join_requests SET user_id = :survivor_id WHERE user_id = :orphan_id;
-- tasks.created_by and agent_definitions.created_by: SET NULL on delete; update anyway for accuracy
UPDATE tasks SET created_by = :survivor_id WHERE created_by = :orphan_id;
-- audit_log and team_messages: leave as historical record (no update)

-- 3. Soft-delete orphan
UPDATE users SET merged_into_user_id = :survivor_id WHERE id = :orphan_id;
```

### deps.py follow-pointer pattern

In `get_or_create_user` (or in the GitHub signin handler), after finding a user by `source_user_id`:

```python
if user.merged_into_user_id is not None:
    # Follow the merge pointer to the survivor
    result = await session.execute(select(User).where(User.id == user.merged_into_user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(500, "Merge pointer references deleted user — data integrity error")
```

The merge pointer is followed at **sign-in time**, not at query time for every request. Once the caller has the survivor's User object, normal flow resumes.

[VERIFIED: codebase — no `merged_into_user_id` column exists yet; migration needed]

---

## Q4: GitHub email retrieval

**Scope needed:** `user:email` [CITED: https://docs.github.com/en/rest/users/emails?apiVersion=2022-11-28]

**Endpoint:** `GET https://api.github.com/user/emails` (authenticated with user's OAuth token)

**Response:**
```json
[
  {"email": "johndoe@example.com", "primary": true, "verified": true, "visibility": "private"},
  {"email": "johndoe@users.noreply.github.com", "primary": false, "verified": true, "visibility": null}
]
```

**How to get primary verified email:** Filter the list for `primary: true AND verified: true`. This is always present if the user has confirmed their GitHub account email.

**Current problem in `deps.py:118`:** `email = gh.get("email") or f"{gh['login']}@github.noreply"`. The `GET /user` endpoint only returns `email` if the user has made it public. With `user:email` scope, `GET /user/emails` always returns all emails including private ones.

**Implementation:** In `check_github_org_membership` (or the new `signin_github` handler), make a second call to `/user/emails` and pick `next((e["email"] for e in emails if e["primary"] and e["verified"]), None)`. Fallback if all denied: use `f"{login}@users.noreply.github.com"` (the canonical GitHub noreply format, more standard than the current `@github.noreply`).

**Privacy edge case:** GitHub allows users to block email disclosure. With `user:email` scope, `/user/emails` will still return the noreply address even if the user has opted out of public email. The `@users.noreply.github.com` address is usable as a unique identifier but won't receive email. Surface in UI: "xbrain will read your email to identify your account and send team notifications." No special UI prompt needed beyond the GitHub OAuth consent screen.

[CITED: https://docs.github.com/en/rest/users/emails?apiVersion=2022-11-28]

---

## Q5: Email notification helper

### What exists

- `apps/memory-api/app/services/notifications.py` — `send_task_notification_email()` [VERIFIED: codebase]
- `aiosmtplib>=3.0.0` is in `pyproject.toml:23` [VERIFIED: codebase]
- `aiosmtplib.send()` accepts a `recipients: list[str]` parameter for multi-recipient delivery [CITED: https://aiosmtplib.readthedocs.io/en/latest/usage.html]

### New function signature

```python
async def send_member_autojoined_email(
    *,
    admin_emails: list[str],
    team_name: str,
    team_slug: str,
    new_member_login: str,
    new_member_display: str,
    dashboard_url: str,
) -> None:
```

### Body sketch

Subject: `New member auto-joined: {new_member_login} in {team_name}`

Body:
```
{new_member_display} (@{new_member_login}) has auto-joined {team_name}
via GitHub org membership.

To block this user, click the link below:
  {dashboard_url}/account/teams/?focus={team_slug}&action=block&login={new_member_login}

— xbrain (noreply@grooveos.app)
```

### Multi-recipient pattern

```python
msg = EmailMessage()
msg["From"] = settings.SMTP_FROM
msg["To"] = ", ".join(admin_emails)   # comma-separated for display
msg["Subject"] = subject
msg.set_content(body)

await aiosmtplib.send(
    msg,
    hostname=settings.SMTP_HOST,
    port=settings.SMTP_PORT,
    username=settings.SMTP_USER or None,
    password=settings.SMTP_PASSWORD or None,
    start_tls=settings.SMTP_TLS,
    recipients=admin_emails,   # explicit list — ensures all recipients get it
    timeout=20,
)
```

### Getting admin emails

Need a helper to query team admin emails: `SELECT u.email FROM users u JOIN team_members tm ON tm.user_id = u.id WHERE tm.team_id = :team_id AND tm.role = 'admin'`. Call this before `send_member_autojoined_email`. If the list is empty or SMTP not configured, log warning and continue (fail-soft).

### Files to touch

- `apps/memory-api/app/services/notifications.py` — add `send_member_autojoined_email`

---

## Q6: Block enforcement

### Recommended layering

**Do NOT change `get_membership` return semantics.** It should return the `TeamMember` row even if blocked. The block check belongs explicitly in `get_team_scope` (and in the auto-grant path).

**Schema addition needed (migration 0016 or 0017):**
```sql
ALTER TABLE team_members ADD COLUMN blocked_at TIMESTAMPTZ NULL;
ALTER TABLE team_members ADD COLUMN blocked_by UUID REFERENCES users(id) NULL;
```

**Updated `get_team_scope` pattern:**
```python
membership = await get_membership(session, user_id=user.id, team_slug=x_team_scope)
if membership is None:
    raise HTTPException(403, f"Not a member of team {x_team_scope}")
if membership.blocked_at is not None:
    raise HTTPException(403, "Member is blocked from this team")
return x_team_scope
```

**New `team_org_blocks` table (GHA-04) — for pre-blocking a GitHub login before they sign in:**
```sql
CREATE TABLE team_org_blocks (
    team_id      UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    github_login VARCHAR(256) NOT NULL,
    blocked_by   UUID REFERENCES users(id) ON DELETE SET NULL,
    blocked_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (team_id, github_login)
);
```

**Auto-grant guard (in `auto_grant_via_org_match()`):**
```python
# 1. Check team_org_blocks (pre-block before first sign-in)
block_row = await check_pre_block(session, team_id=team.id, github_login=github_login)
if block_row:
    continue

# 2. Check existing membership with blocked_at
existing = await get_membership(session, user_id=user.id, team_slug=team.slug)
if existing is not None and existing.blocked_at is not None:
    continue  # was blocked after a previous auto-grant — don't re-grant

# 3. Insert (idempotent)
if existing is None:
    await add_member(session, team_id=team.id, user_id=user.id, role="member")
    granted_teams.append(team)
```

**Block endpoint (GHA-03):** `POST /v1/teams/{slug}/members/{user_id}/block` — admin only. Sets `team_members.blocked_at = now()`, `blocked_by = caller.id`.

**Unblock endpoint:** `DELETE /v1/teams/{slug}/members/{user_id}/block` — sets `blocked_at = NULL`, `blocked_by = NULL`.

**`get_membership` should NOT filter out blocked members** — the caller decides what to do. This preserves the audit trail and the unblock path.

---

## Q7: app-site auth state machine

### States

```
State 0: UNAUTHENTICATED
  - localStorage has no xbt_
  - DOM: signin-section visible, auth-section hidden
  - Buttons: [Sign in with GitHub] (primary, black) + [Sign in with Google] (secondary, ghost)
  
State 1: AUTHENTICATED_GITHUB_ONLY
  - xbt_ present, /v1/me returns github_id set, no google email (email is noreply)
  - DOM: auth-section visible, hdr-user shows "@{github_login}"
  - Banner below header: "Link Google account for Drive sync" (CTA button: [Link Google])
  
State 2: AUTHENTICATED_GOOGLE_ONLY (legacy)
  - xbt_ present, /v1/me returns github_id = null
  - DOM: auth-section visible, hdr-user shows email
  - Banner: "Link GitHub to join org-based teams automatically" (CTA button: [Link GitHub])
  
State 3: AUTHENTICATED_BOTH
  - xbt_ present, /v1/me returns github_id set AND email is real (not noreply)
  - DOM: auth-section visible, hdr-user shows "@{github_login}" + "✓ Google linked"
  - No banner
```

### Button visibility rules

```javascript
function renderAuthHeader(me) {
  const hasGithub = !!me.github_id;
  const hasRealEmail = me.email && !me.email.endsWith('@users.noreply.github.com');

  // Primary sign-in buttons (signin-section)
  // github-signin-btn: always shown in signin-section (primary)
  // google-signin-btn: always shown in signin-section (secondary)

  // Post-auth CTAs
  const showLinkGoogle = hasGithub && !hasRealEmail;
  const showLinkGithub = !hasGithub;
  const showBothConnected = hasGithub && hasRealEmail;

  document.getElementById('cta-link-google').hidden = !showLinkGoogle;
  document.getElementById('cta-link-github').hidden = !showLinkGithub;
  document.getElementById('status-both-connected').hidden = !showBothConnected;
}
```

### Files to touch

- `app-site/account/teams/index.html` — add GitHub sign-in button in `#signin-section`, add CTA banner slots (`#cta-link-google`, `#cta-link-github`, `#status-both-connected`)
- `app-site/account/teams/teams.js` — add `GITHUB_CLIENT_ID` const, `initiateGithubSignin()`, `handleGithubCallback()`, `renderAuthHeader()`, update `init()` to detect `?code=` on load

---

## Q8: GitHub OAuth scopes

### Current state

**Chrome extension** (`background.js:69`): requests `"read:user read:org user:email"` [VERIFIED: codebase]

**LibreChat** (`infrastructure/librechat/librechat.yaml:13`): `socialLogins: ["google", "github"]` — LibreChat handles its own GitHub OAuth via the shared client ID `Ov23liVqXmHkS6JdYpcN`. LibreChat requests its own scopes during its OAuth flow; we don't control what it requests.

**Key point:** OAuth scope is **per-call**, not per-app configuration. Each `/authorize` request specifies its own `scope` parameter. Different flows using the same `client_id` can request different scopes. GitHub will show a consent screen only for scopes not previously granted. [CITED: https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps]

### Required scopes for Phase 10

| Scope | Why needed | Already requested |
|-------|-----------|-------------------|
| `read:user` | GET /user — login, github_id, public email | Yes (extension) |
| `user:email` | GET /user/emails — primary verified email | Yes (extension) |
| `read:org` | GET /user/orgs — org list for auto-grant | Yes (extension) |

**App-site `/authorize` call:** Request all three scopes. If the user already granted them (LibreChat session or extension), GitHub skips the consent screen.

**No OAuth app configuration change needed** for scopes. The only configuration change is adding `https://grooveos.app/account/teams/` as an authorized redirect URI in the GitHub OAuth App settings page. [ASSUMED — GitHub App settings access needed from mrboups]

---

## Pitfalls + Gotchas

**1. `client_secret` shared between xbrain and LibreChat on the same OAuth app.**
`Ov23liVqXmHkS6JdYpcN` is LibreChat's GitHub OAuth app. The `GITHUB_CLIENT_SECRET` in `infrastructure/docker-compose.yml` is passed to both LibreChat and memory-api. This is fine — both use the same secret. But if LibreChat rotates the client secret, memory-api breaks. Document this dependency. Affected file: `infrastructure/docker-compose.yml` env block for `memory-api`.

**2. `redirect_uri` must be pre-registered in GitHub OAuth App settings.**
Adding `https://grooveos.app/account/teams/` as an authorized callback URL requires a one-time manual change in the GitHub OAuth App settings. Until this is done, the app-site flow will return `redirect_uri_mismatch`. The extension uses `https://<extension-id>.chromiumapp.org/` which is registered. This is a deploy-time prerequisite, not a code prerequisite. Needs to happen before the phase is verified.

**3. The `gho_` flow in `deps.py` does not call `/user/emails`.**
`deps.py:118` uses `gh.get("email") or f"{gh['login']}@github.noreply"`. After Phase 10, GitHub-primary users who sign in via the new `POST /v1/auth/github/signin` will have real emails (from `/user/emails`). But existing `gho_` bearer token auth (used by nobody in production currently, but accessible) still goes through the old path. If left untouched, the old path creates users with noreply emails. Either patch `check_github_org_membership` to also call `/user/emails`, or document that `gho_` direct-bearer auth is only for legacy and will sunset. Affected file: `apps/memory-api/app/auth.py:130-187`.

**4. `unique` constraint on `users.github_id` blocks the merge transaction.**
`0007_github_users.py:27`: `idx_users_github_id` is a unique index. During the merge, survivor row already has `github_id` set; orphan row also has `github_id` set. No conflict here since both have the same `github_id` value (that's how we detect them). BUT: when `get_or_create_user` tries to create the GitHub-primary row (`source_user_id = "github:login"`) for a user whose `github_id` already belongs to a Google-linked row, the INSERT will fail on the unique index. The detection must happen BEFORE `session.flush()` — the new `POST /v1/auth/github/signin` handler must do a `SELECT * FROM users WHERE github_id = :github_id` BEFORE calling `get_or_create_user`. Affected: new `signin_github` route.

**5. `team_members` has composite PK `(team_id, user_id)`.**
During merge, if both orphan and survivor are already in the same team (e.g., survivor was auto-granted via org match in an earlier session before Google linking), the `INSERT ... ON CONFLICT DO NOTHING` in the merge SQL handles it. But if survivor has `role = "admin"` and orphan has `role = "member"` for the same team, the INSERT is skipped and the admin role is preserved — correct behavior. If orphan has `role = "admin"` but survivor has `role = "member"`, the admin role is lost. Decision: keep the **higher** role. The merge SQL needs a role priority check: `GREATEST(orphan.role, survivor.role)` — but role is a string. Use explicit: if orphan.role = 'admin' and existing survivor.role = 'member', UPDATE role to 'admin'.

**6. `aiosmtplib.send()` recipients parameter vs. `To` header.**
The `To` header in `EmailMessage` is for display. The actual envelope recipients must be passed via the `recipients=` kwarg to `aiosmtplib.send()`. Omitting `recipients=` causes the library to infer from `To`/`Cc`/`Bcc` headers, which works for a simple list but is less explicit. Pass both for correctness.

**7. App-site Firebase Hosting: callback URL and SPA routing.**
Firebase Hosting serves static files. When the browser navigates back to `https://grooveos.app/account/teams/?code=...&state=...`, Firebase serves `index.html` (because there's no file called `?code=...`). This works correctly — `teams.js` detects the query params on DOMContentLoaded. No `firebase.json` rewrite rules needed. But if the URL path were different (e.g., `/auth/callback`), a rewrite would be required. Using the same path (Option B) avoids this entirely.

---

## Files To Touch (preview for planner)

### Migrations (memory-api)
- `apps/memory-api/alembic/versions/0016_github_auth_primary.py` — adds:
  - `users.merged_into_user_id UUID REFERENCES users(id) NULL`
  - `team_members.blocked_at TIMESTAMPTZ NULL`
  - `team_members.blocked_by UUID REFERENCES users(id) NULL`
  - New table `team_org_blocks (team_id, github_login, blocked_by, blocked_at)`

### memory-api models
- `apps/memory-api/app/models/user.py` — add `merged_into_user_id` column
- `apps/memory-api/app/models/team.py` — add `blocked_at`, `blocked_by` to `TeamMember`; add `TeamOrgBlock` model

### memory-api routes (new)
- `apps/memory-api/app/routes/auth_github.py` — `POST /v1/auth/github/signin` (GHA-01), public endpoint
- `apps/memory-api/app/routes/teams.py` — add `POST /v1/teams/{slug}/members/{user_id}/block` (GHA-03), `DELETE /v1/teams/{slug}/members/{user_id}/block`, `POST /v1/teams/{slug}/org-blocks` (GHA-04), `DELETE /v1/teams/{slug}/org-blocks/{github_login}`

### memory-api repos
- `apps/memory-api/app/repos/teams.py` — add `check_pre_block()`, `upsert_org_block()`, `delete_org_block()`
- `apps/memory-api/app/repos/users.py` — add `get_user_by_github_id()`, `merge_users()`

### memory-api services
- `apps/memory-api/app/services/notifications.py` — add `send_member_autojoined_email()`
- `apps/memory-api/app/services/github_org_autogrant.py` — new file, `auto_grant_via_org_match(session, user, github_orgs)` — called by the signin route (GHA-02)

### memory-api deps + auth
- `apps/memory-api/app/deps.py` — `get_team_scope` adds `blocked_at` check (Q6); optionally follow `merged_into_user_id` in xbt_ path
- `apps/memory-api/app/auth.py` — `check_github_org_membership` or a new helper to call `/user/emails` (Q4)
- `apps/memory-api/app/config.py` — no new env vars needed (all GitHub config already present)

### Chrome extension
- `chrome-extension/popup.html` — move GitHub sign-in to primary position (GHA-07)
- `chrome-extension/popup.js` — update sign-in flow order (GHA-07)

### app-site
- `app-site/account/teams/index.html` — add GitHub sign-in button (primary), Google (secondary), CTA banner slots (GHA-08)
- `app-site/account/teams/teams.js` — `initiateGithubSignin()`, `handleGithubCallback()`, state machine (Q7) (GHA-08)

### Verify script
- `verify-phase10.sh` (new at repo root or `infrastructure/`) — tests: signin endpoint returns xbt_, auto-grant fires on org match, block prevents re-grant, merge correctly redirects orphan sign-in

---

## Sources

### Primary (HIGH confidence)

- [GitHub OAuth scopes](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) — `read:user`, `user:email`, `read:org` verified
- [GitHub authorizing OAuth apps](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps) — authorization code flow, state param, code exchange endpoint
- [GitHub REST: list orgs for authenticated user](https://docs.github.com/en/rest/orgs/orgs?apiVersion=2022-11-28#list-organizations-for-the-authenticated-user) — `read:org` scope, pagination, private org visibility
- [GitHub REST: list email addresses](https://docs.github.com/en/rest/users/emails?apiVersion=2022-11-28) — `user:email` scope, response shape, primary+verified flags
- [aiosmtplib usage docs](https://aiosmtplib.readthedocs.io/en/latest/usage.html) — `recipients=` list parameter
- `chrome-extension/background.js:69` — scope `"read:user read:org user:email"` [VERIFIED: codebase]
- `infrastructure/librechat/librechat.yaml:13` — LibreChat uses same client_id [VERIFIED: codebase]
- `apps/memory-api/app/models/user.py` — User ORM columns [VERIFIED: codebase]
- `apps/memory-api/app/models/team.py` — TeamMember ORM, no `blocked_at` yet [VERIFIED: codebase]
- `apps/memory-api/alembic/versions/0007_github_users.py` — unique index on `github_id` [VERIFIED: codebase]
- FK grep across `apps/memory-api/app/models/*.py` + `alembic/versions/*.py` [VERIFIED: codebase]

### Assumptions log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `Ov23liVqXmHkS6JdYpcN` GitHub OAuth App allows additional redirect URIs to be added | Q1 | App-site flow returns `redirect_uri_mismatch`; fallback = create a separate OAuth app |
| A2 | `mrboups` has Owner access to the GitHub OAuth App settings for this client ID | Q1 | Same as A1 |
| A3 | The `@users.noreply.github.com` noreply format is the right fallback email format | Q4 | Minor — only affects display, not auth |

**Research date:** 2026-05-13
**Valid until:** 2026-06-13 (GitHub API surface is stable; scope rules rarely change)
