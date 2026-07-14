# Local-Auth Account Recovery (Operator Runbook)

**Audience:** the self-hosting operator with shell + database access.
**Scope:** how a locked-out or password-forgotten **local (email/password)**
account is recovered on an xbrain deployment.

> **There is no email-based "reset my password" link, and there never will be
> in the OSS-light default.** A reset-by-email flow requires an outbound SMTP
> server, and an out-of-the-box self-hostable install must not depend on one.
> Recovery for local accounts is therefore an **operator action against the
> database**, documented here. This runbook *is* the recovery story
> (Phase 18 SC#5) — the deliberate substitute for SMTP reset.

Google and GitHub sign-in are unaffected by any of this: those users recover
through their identity provider, not through xbrain. This document is only for
accounts created via `POST /v1/auth/local/register`.

---

## Where local credentials live

All local-auth state is one row per user in the `local_credentials` table
(migration `0024_local_credentials`):

| Column            | Meaning                                                        |
| ----------------- | ------------------------------------------------------------- |
| `user_id`         | PK / FK → `users.id` (`ON DELETE CASCADE`)                    |
| `password_hash`   | argon2id encoded hash (`$argon2id$...`). Never plaintext.     |
| `algo`            | KDF label, `argon2id`.                                        |
| `failed_attempts` | consecutive failed logins; resets to 0 on a successful login. |
| `locked_until`    | `TIMESTAMPTZ`; `NULL` = not locked. Set once the failure threshold is reached. |
| `created_at` / `updated_at` | audit timestamps.                                   |

Lockout is enforced by the login route reading `locked_until`
(`app/routes/auth_local.py`) and written by
`app/repos/local_credentials.py::record_failure`. The thresholds are config
(`LOCAL_AUTH_MAX_FAILED_ATTEMPTS`, default 5; `LOCAL_AUTH_LOCKOUT_MINUTES`,
default 15).

---

## Case 1 — Account locked out (too many failed logins)

The user knows their password but has tripped the lockout window and is now
getting the generic `Invalid email or password.` 401 even with the correct
password. Clear the counters so they can sign in immediately, without waiting
for `locked_until` to elapse.

Open a psql shell against the memory-api database (adjust host/db/user for
your compose or VM setup):

```bash
docker compose exec postgres psql -U xbrain -d xbrain
```

Then clear the lockout for that email:

```sql
UPDATE local_credentials
SET failed_attempts = 0,
    locked_until    = NULL,
    updated_at      = now()
WHERE user_id = (
    SELECT id FROM users WHERE lower(email) = lower('user@example.com')
);
```

The email match is case-insensitive to mirror how the app looks accounts up.
The user can now log in with their existing password. Nothing about their
`password_hash` was touched.

To confirm the lock is gone:

```sql
SELECT failed_attempts, locked_until
FROM local_credentials
WHERE user_id = (SELECT id FROM users WHERE lower(email) = lower('user@example.com'));
-- expect: 0 | NULL
```

---

## Case 2 — Forgotten password (no email reset available)

The user genuinely does not know their password. Because there is no SMTP
reset, the operator does **not** set a new password directly (there is no
plaintext-to-hash CLI shipped, and hand-crafting an argon2 hash by hand is
error-prone). Instead, **delete the credential row** so the account reverts to
"has no local password". The user then signs in by another method they still
control (Google or GitHub, if configured on this deployment) and **re-attaches
a fresh password themselves** through the authenticated set-password flow
(`POST /v1/auth/local/set-password`, described below).

```sql
DELETE FROM local_credentials
WHERE user_id = (
    SELECT id FROM users WHERE lower(email) = lower('user@example.com')
);
```

Deleting the `local_credentials` row does **not** delete the `users` row or any
of the user's data, teams, or memory — it only removes the email/password
login method. The account, its team memberships, and everything in the brain
are untouched.

### Re-attaching a password after a Case-2 delete

Once the user is signed in again (any method), the app can call the
authenticated endpoint on their behalf:

```
POST /v1/auth/local/set-password
Authorization: Bearer <their current session token>
Content-Type: application/json

{ "new_password": "their-new-password" }
```

Because the row was deleted, this is a **first-attach** — no `old_password` is
required. The endpoint writes **only the caller's own** `user_id` (identity is
taken from the authenticated session, never from the request body), and the new
password is stored as an argon2id hash. This is the same proof-of-ownership
convergence path a GitHub/Google user uses to add a password in the first place
(decision D-18-05): the account ends up with a working local login again,
proven by a live session, never granted cold.

> **What if the user has NO other sign-in method** (a local-only deployment
> with no Google/GitHub configured, and they forgot their only password)? Then
> the operator must both (a) delete the credential row as above **and**
> (b) hand the user a temporary way back in — the pragmatic option is for the
> operator to register a throwaway helper flow or, more simply, to treat the
> account as unrecoverable-by-self and re-provision it. There is intentionally
> no built-in "operator sets a known temporary password" command in the
> OSS-light default; if your deployment needs one, it belongs in a small admin
> CLI you add downstream, not in this recovery doc.

---

## What this runbook does NOT do (by design)

- **No SMTP.** No outbound email is sent, no reset link is generated, no
  `aiosmtplib`/mail server is required or contacted. Email-based password reset
  is deliberately **out of scope** for the OSS-light default (Phase 18 domain
  boundary). This document is the substitute.
- **No plaintext password storage or transport.** The operator never reads or
  writes a plaintext password into the database. Recovery is "clear the lock"
  or "remove the credential so the user re-attaches one" — never "type their
  new password into SQL".

---

## Known limitations for local-auth-only installs (D-18-07)

These are pre-existing platform behaviors that a local-auth-only operator
should be aware of. They are **flagged, not fixed** by Phase 18 — do not expect
this phase to have changed them:

1. **MCP / ChatGPT Custom-Connector sign-in is GitHub-only.** The connector's
   `/oauth/authorize` flow (`app/routes/oauth_authorize.py`) hardcodes a GitHub
   redirect as the only sign-in path. A deployment with **no** GitHub App
   configured cannot yet use the Claude.ai / ChatGPT Custom Connector — local
   email/password login does not (yet) have a branch there. Web and API access
   via a local-auth `xbt_` token work normally; only the external
   Custom-Connector handshake is affected.

2. **Some routes still gate strictly on `principal["kind"] == "user"`.** A
   handful of endpoints (e.g. `me.py`'s Granola-key handlers, `audit.py`,
   `promotions.py`) reject a `kind="user_api_token"` principal with a 403 even
   though it carries a full user identity. A local-auth user authenticating
   with an `xbt_` token hits exactly the same 403s that a Google/GitHub Chrome
   extension user (also `xbt_`) hits today. This is not a local-auth
   regression; it is a pre-existing platform gap tracked separately.
