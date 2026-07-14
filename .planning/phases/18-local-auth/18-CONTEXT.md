# Phase 18: Local Auth (OSS default) — Context

**Gathered:** 2026-07-13
**Status:** Ready for planning
**Source:** Locked decision **Q2** of `.planning/features/open-core-edition-design.md`; the ROADMAP Phase 18 block (SC#1–SC#6); a read-only auth-architecture audit run 2026-07-13; and one user decision taken during this planning run.

<domain>
## Phase Boundary

A self-hoster creates an account and signs in with **email + password**, with ZERO external OAuth setup — no Google Cloud project, no GitHub App, no callback URLs. Google OAuth and the GitHub App stay available and unchanged when configured; the local path is a **default, not a replacement**.

**IN scope:** a `local_credentials` store + password hashing; `register` / `login` / `logout` / `set-password` (change) routes; abuse resistance (rate-limit + lockout) on the credential endpoints; a minimal registration + sign-in + set-password UI surface.

**OUT of scope:**
- **Email verification and email-based password RESET** (SC#5) — they need outbound SMTP, which an OSS-light install must not require. Document the recovery story instead (operator-level: reset via DB / an admin CLI, not via email).
- The web group-chat frontend itself (Phase 16) — Phase 18 adds the *auth screens*, not the chat app.
- The MCP/ChatGPT Custom-Connector `/oauth/authorize` flow, which today hardcodes a GitHub redirect. See D-18-07 — flagged, not fixed here.
</domain>

<the_single_most_important_finding>
## The session mechanism ALREADY EXISTS. Reuse it. Do not invent a JWT session scheme.

The audit established this against the live code, and it collapses most of the perceived risk of this phase:

**Every human login today — Google AND GitHub — ends in the same place: an opaque, DB-backed `xbt_` token.**
- GitHub: `POST /v1/auth/github/signin` mints one via `_mint_xbt_for_user` (`apps/memory-api/app/routes/auth_github.py:350-367`) — `raw = "xbt_" + secrets.token_urlsafe(32)`, `sha256` into `user_api_tokens.token_hash`, returned once.
- Google: the extension calls `POST /v1/me/api-token` (`app/routes/me.py:225-260`) and mints the same shape, then discards the Google token (`chrome-extension/onboarding.js:140`).
- Every subsequent authenticated call sends `Authorization: Bearer xbt_...`; `get_current_principal` resolves it at `deps.py:225-272` via a SHA-256 hash lookup — **not a JWT, not the `BRIDGE_SHARED_SECRET` HS256 path** (that is a separate internal-services mechanism).

**Therefore local auth mints an `xbt_` token exactly the same way, and NO new branch is added to `get_current_principal`.** The `xbt_` branch already exists and is agnostic to *how* the underlying user row was created. This is precisely what makes SC#3 ("indistinguishable principal") almost free rather than a rewrite.
</the_single_most_important_finding>

<decisions>
## Implementation Decisions

### D-18-01 — Session = mint an `xbt_` token. Reuse, do not reinvent.

Register and login both END by minting an `xbt_` token via the existing pattern (`_mint_xbt_for_user` / `create_api_token`) and returning the raw token once. No JWT session scheme, no new `principal["kind"]`, no change to `get_current_principal`. A local-auth user is a `kind="user_api_token"` (or `kind="user"`) principal like everyone else.

### D-18-02 — `source_user_id = f"email:{normalized_email}"`.

Local accounts use the **existing** `email:<addr>` convention already minted by the `librechat-onboarding` bridge path (`deps.py:314`). Normalize the email (trim + lowercase) before both the `source_user_id` and any lookup. This convention converges for free with the existing GitHub-merge logic (`auth_github.py` Step B, `get_user_by_email` at `repos/users.py:60-73`, case-insensitive) — reuse it, write no new merge code.

### D-18-03 — Store credentials in a NEW `local_credentials` table, not columns on `users`.

`users` is already overloaded with GitHub-token columns. A dedicated table keeps the credential and its lockout counters together and out of the hot `users` row:

```
local_credentials(
  user_id UUID PK/FK -> users.id ON DELETE CASCADE,
  password_hash TEXT NOT NULL,
  algo VARCHAR(32) NOT NULL,          -- e.g. 'argon2id'
  failed_attempts INT NOT NULL DEFAULT 0,
  locked_until TIMESTAMPTZ,           -- NULL = not locked
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
```
New alembic migration. `users` gets no new column.

### D-18-04 — KDF = argon2id (via `argon2-cffi`).

SC#2 names argon2id first, and it is the modern default. Add `argon2-cffi` to `apps/memory-api/pyproject.toml` — the repo has **no** password-hashing dependency today (`cryptography` is Fernet/reversible, not a KDF; PyJWT/authlib are for tokens). Store the full argon2 encoded hash (it carries its own params/salt); persist `algo='argon2id'` so a future re-hash/upgrade is detectable. **Never** plaintext, never a bare SHA (SC#2).

### D-18-05 — Convergence via the AUTHENTICATED path, never via cold register. (User decision 2026-07-13, implemented safely.)

The user chose "converge — one account, two ways in" over "reject". Delivered as follows, because cold-register convergence without email verification is an account-takeover vector (anyone knowing a victim's GitHub email could set a password on their account):

- `register(email, password)` with a **brand-new** email → create user (`get_or_create_user`, `source_user_id=email:<addr>`) + `local_credentials` row + bootstrap the first team + mint `xbt_`, all in ONE transaction with ONE commit at the end. **CORRECTION (research, 2026-07-13): call `teams_repo.create_team` DIRECTLY, not the `POST /v1/teams/self-solo` route — that route commits internally (`teams.py:485`), which would break the atomic boundary.** `create_team` only `flush()`es (`repos/teams.py`), leaving the transaction open for the caller, which is exactly what a single-commit register needs.
- `register` with an email that **already has an account** (GitHub/Google/local) → **409, does NOT grant access.** Message: "This email already has an account — sign in with your existing method, then add a password in settings." This is the only cold-path behavior that is safe without email verification.
- `set-password` — an **authenticated** endpoint (part of SC#5's change-password surface). A user already signed in (via GitHub, Google, or an existing local password) attaches/updates a password on **their own** row. THIS is the convergence: a GitHub user ends up with both login methods on one unified account, proven by their existing session — no takeover window.

Net end-state = exactly the user's chosen "one converged account", reached through proof-of-ownership. If the user later wants cold-register to converge immediately, that requires email verification first (out of scope here).

**Recovery story (SC#5, since reset-by-email is out of scope):** document that a locked-out local user recovers via an operator action — a documented `alembic`/psql credential reset or a small admin CLI — NOT via email. Write this in the install docs stub, do not build SMTP.

### D-18-06 — Abuse resistance = in-process limiter + DB lockout. No Redis.

There is **no** rate-limiting anywhere today — not in nginx (`20-api.conf.template` has no `limit_req`; `auth_github.py:36`'s "rate-limited by nginx" docstring is FALSE), not in the app, no Redis in the dependency graph. So SC#6 is net-new and must be pragmatic:
- **Lockout** (persistent, survives restarts): `failed_attempts` / `locked_until` on `local_credentials`. N consecutive failures → lock for a cooldown. Successful login resets the counter. This is per-account and DB-backed.
- **Rate-limit** (cheap, in-process): a token bucket keyed by client IP (and/or email) on the credential routes, to blunt spray/enumeration. In-process is acceptable for a single-instance OSS-light default; note in the plan that a multi-replica hosted deployment would want a shared store — but do NOT add Redis as a Phase-18 dependency.
- **No user-enumeration oracle:** register-collision and login-failure responses must not reveal whether an email exists (same generic message + comparable timing). Login should still run a dummy KDF verify on a non-existent email to equalize timing.

### D-18-07 — Adjacencies found by the audit: flagged, NOT silently bundled.

The "indistinguishable principal" is already imperfect for `xbt_` tokens today, independent of Phase 18. Local-auth users inherit these exactly as Google/GitHub extension users already do. The plan should NOT try to fix them all, but must not be surprised by them:

1. **`conversations.py:92` is a live cross-user privacy leak** (see below). Handled OUTSIDE Phase 18 — backlogged for its own fix. Do not fold it into the auth plan.
2. Several routes strictly gate `principal["kind"] == "user"` and 403 a `user_api_token` even with a full `user` (`me.py:76-83`, `audit.py:34`, `promotions.py:58-63`). Pre-existing; not Phase 18's job. A local user hits the same 403s a Google/GitHub extension user hits today.
3. The MCP Custom-Connector `/oauth/authorize` (`oauth_authorize.py:128-138`) hardcodes a GitHub redirect as the only sign-in. A local-auth-only self-hoster cannot use the Claude.ai/ChatGPT connector until this grows a local-auth branch. OUT of Phase 18 core scope; flag in the plan so it is a known limitation, not a late surprise.

### Claude's Discretion
- Exact route paths (`/v1/auth/local/register` etc. mirrors `auth_github.py`'s structure), request/response schemas, lockout thresholds/cooldown values, argon2 cost params (use argon2-cffi's sane defaults; do not hand-roll).
- Whether `set-password` and "change password" (old→new for an existing local user) are one endpoint or two.
- The exact UI framing — but it MUST be plain (no framework assumption beyond what the existing account surface uses); this phase ships auth screens, not the chat app.
</decisions>

<canonical_refs>
## Canonical References — downstream agents MUST read these

### The auth machinery to REUSE (not reinvent)
- `apps/memory-api/app/deps.py:46-333` — `get_current_principal`; the `xbt_` branch is `:225-272`. The new path must produce a principal it already accepts.
- `apps/memory-api/app/routes/auth_github.py:218-367` — `_resolve_or_merge_user` + `_mint_xbt_for_user`; the exact structure `register`/`login` mirror.
- `apps/memory-api/app/routes/me.py:225-260` — `create_api_token`, the other `xbt_` mint site.
- `apps/memory-api/app/repos/users.py:12-73` — `get_or_create_user` (race-safe ON CONFLICT) + `get_user_by_email` (case-insensitive).
- `apps/memory-api/app/routes/teams.py:445-492` + `repos/teams.py:13-34` — `self-solo` first-team bootstrap; call in-process at end of register.
- `apps/memory-api/app/deps.py:336-396` (`get_team_scope`) + `repos/teams.py:118-130` (`get_membership`) — what authorizes a principal; no change needed.

### The schema
- `apps/memory-api/alembic/versions/0001_initial.py:26-54` — `users`, `teams`, `team_members`. `users.email` has **no unique constraint** anywhere (confirmed across all migrations).
- `apps/memory-api/alembic/versions/0009_crm_contacts.py:87-89` — the partial-unique-index pattern to copy if an email uniqueness guard is wanted.
- Latest migration is 0023 — the new one is **0024_local_credentials**. Its `down_revision` MUST be the head's FULL revision string `"0023_tasks_source_connector"` — NOT `"0023"`. Since migration 0016 the `revision` id is the full filename stem, not the short number (verified 2026-07-13 by reading the `revision =` line inside 0023, not just the file listing — the exact mistake the research made).

### Boot / config (confirms a no-OAuth install is already clean)
- `apps/memory-api/app/config.py:15,47-77` — `GOOGLE_CLIENT_ID`/`GITHUB_*` default to `""`/`0`, no validator. `:151-166` — `OAUTH_ISSUER_URL`/`OAUTH_RESOURCE_URL` DO fail-fast, but those are the MCP connector's AS vars, NOT social login. A clean install with no Google/GitHub set boots fine; only those two social-login *endpoints* 503 at request time.
- `apps/memory-api/pyproject.toml` — deps to reuse (`cryptography`, `authlib`, `aiosmtplib`) and where `argon2-cffi` gets added.

### The locked decision + roadmap
- `.planning/ROADMAP.md` Phase 18 block — SC#1–SC#6 (authoritative).
- `.planning/features/open-core-edition-design.md` — Q2, and Q4 (why removing LibreChat removes the product's only password login, which this phase re-establishes natively).
</canonical_refs>

<specifics>
## The gate lesson — it applies here too

Five defects across Phases 14–15 all had one cause: **a check that never traversed the real deployment path.** Auth is where that failure mode is most dangerous — a login test that mocks the principal proves nothing about whether a real `xbt_` token, minted by the real route, is accepted by the real `get_current_principal` and authorizes against the real `team_members` table.

So Phase 18 verification MUST, against a **real Postgres** (testcontainers, as 15-02/15-06 did — Docker is available):
- Register a brand-new email → get an `xbt_` → use it on a **real team-scoped route** (e.g. `/v1/memory/*`) and get 200, proving the principal is genuinely indistinguishable (SC#3).
- Prove the persisted row is an **argon2id hash**, never plaintext, by reading `local_credentials` (SC#2).
- Prove login lockout actually triggers after N failures against the real table (SC#6), and that a correct password after lockout-expiry works.
- Prove a **clean boot with NO Google/GitHub/OAuth-social vars set** reaches a working register→login→authorized-request loop end to end (SC#1) — the whole point of the phase.
- Prove the negative: register with an already-existing email → 409, no access granted (D-18-05).
- Prove Google/GitHub still resolve unchanged when configured (SC#4) — do not regress the existing paths.

Docker realities carried over: host is ARM64 (don't build images); Git Bash host mounts need `MSYS_NO_PATHCONV=1` + a Windows path or they silently mount nothing.
</specifics>

<deferred>
## Deferred / adjacent (NOT Phase 18)

- **`conversations.py:92` cross-user conversation leak** — a live production privacy bug: any `xbt_`-authenticated user (i.e. every extension user today) sees *all* team members' conversations, because `owner_filter` becomes `None` for `kind != "user"`. Backlogged for its own dedicated fix; surfaced to the user 2026-07-13. NOT in Phase 18 scope.
- **Q3 — local embeddings by default** — still unmapped (REQUIREMENTS.md). A self-hoster still needs an embeddings key for the brain to work. The next unmapped gap after auth.
- **MCP Custom-Connector local-auth branch** (`oauth_authorize.py`) — a local-auth-only install can't use the Claude.ai/ChatGPT connector. Separate scope.
- **Email verification + reset-by-email** — deliberately out (needs SMTP). Enables cold-register convergence if ever added.
</deferred>

---

*Phase: 18-local-auth*
*Context gathered: 2026-07-13*
