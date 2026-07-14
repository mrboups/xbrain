# Phase 18: Local Auth (OSS default) - Research

**Researched:** 2026-07-14
**Domain:** Password authentication (argon2id KDF) bolted onto an existing multi-principal FastAPI auth system; in-process abuse resistance; Alembic migration conventions
**Confidence:** HIGH (code-path claims — all read directly from the live repo) / MEDIUM (external crypto/library recommendations — cross-verified against official docs + PyPI registry)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-18-01** — Session = mint an `xbt_` token. Reuse, do not reinvent. Register and
  login both END by minting an `xbt_` token via the existing pattern
  (`_mint_xbt_for_user` / `create_api_token`) and returning the raw token once. No
  JWT session scheme, no new `principal["kind"]`, no change to `get_current_principal`.
  A local-auth user is a `kind="user_api_token"` (or `kind="user"`) principal like
  everyone else.
- **D-18-02** — `source_user_id = f"email:{normalized_email}"`. Local accounts use
  the existing `email:<addr>` convention already minted by the `librechat-onboarding`
  bridge path (`deps.py:314`). Normalize the email (trim + lowercase) before both the
  `source_user_id` and any lookup. This convention converges for free with the
  existing GitHub-merge logic — reuse it, write no new merge code.
- **D-18-03** — Store credentials in a NEW `local_credentials` table, not columns on
  `users`: `local_credentials(user_id UUID PK/FK -> users.id ON DELETE CASCADE,
  password_hash TEXT NOT NULL, algo VARCHAR(32) NOT NULL, failed_attempts INT NOT
  NULL DEFAULT 0, locked_until TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT
  now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())`. New alembic migration.
  `users` gets no new column.
- **D-18-04** — KDF = argon2id (via `argon2-cffi`). Add `argon2-cffi` to
  `apps/memory-api/pyproject.toml`. Store the full argon2 encoded hash (self-describing);
  persist `algo='argon2id'` so a future re-hash/upgrade is detectable. Never plaintext,
  never a bare SHA.
- **D-18-05** — Convergence via the AUTHENTICATED path, never via cold register.
  `register(email, password)` with a brand-new email creates the account + mints
  `xbt_` + bootstraps the first team via `self-solo`-equivalent logic. `register`
  with an email that already has an account (GitHub/Google/local) -> **409, does NOT
  grant access.** Message: "This email already has an account — sign in with your
  existing method, then add a password in settings." `set-password` is an
  AUTHENTICATED endpoint where an already-signed-in user (via GitHub, Google, or an
  existing local password) attaches/updates a password on their OWN row — this is the
  only convergence path. Recovery story (no SMTP): document an operator-level
  `alembic`/psql credential reset or a small admin CLI — NOT email.
- **D-18-06** — Abuse resistance = in-process limiter + DB lockout. No Redis.
  Lockout (persistent, DB-backed `failed_attempts`/`locked_until` on
  `local_credentials`) + rate-limit (in-process token bucket keyed by client IP
  and/or email, on the credential routes). In-process is acceptable for a
  single-instance OSS-light default; note in the plan that a multi-replica hosted
  deployment would want a shared store, but do NOT add Redis. No user-enumeration
  oracle: register-collision and login-failure responses must not reveal whether an
  email exists (same generic message + comparable timing); login must run a dummy
  KDF verify on a non-existent email to equalize timing.
- **D-18-07** — Adjacencies found by the audit are FLAGGED, not fixed here:
  (1) `conversations.py:92` cross-user privacy leak — backlogged separately, do NOT
  fold into this phase; (2) several routes strictly gate `principal["kind"] ==
  "user"` and 403 a `user_api_token` even with a full `user` — pre-existing, not
  Phase 18's job; (3) the MCP Custom-Connector `/oauth/authorize` hardcodes a GitHub
  redirect — a local-auth-only self-hoster cannot use it yet; flag as a known
  limitation, do not fix.

### Claude's Discretion

- Exact route paths (`/v1/auth/local/register` etc. mirrors `auth_github.py`'s
  structure), request/response schemas, lockout thresholds/cooldown values, argon2
  cost params (use argon2-cffi's sane defaults; do not hand-roll).
- Whether `set-password` and "change password" (old→new for an existing local user)
  are one endpoint or two.
- The exact UI framing — but it MUST be plain (no framework assumption beyond what
  the existing account surface uses); this phase ships auth screens, not the chat
  app.

### Deferred Ideas (OUT OF SCOPE)

- **Email verification and email-based password RESET** (SC#5) — needs outbound
  SMTP, which an OSS-light install must not require. Document the recovery story
  instead (operator-level: reset via DB / an admin CLI, not via email).
- The web group-chat frontend itself (Phase 16) — Phase 18 adds the auth screens,
  not the chat app.
- The MCP/ChatGPT Custom-Connector `/oauth/authorize` flow (hardcodes a GitHub
  redirect) — see D-18-07, flagged not fixed.
- `conversations.py:92` cross-user conversation leak — backlogged for its own
  dedicated fix, NOT in Phase 18 scope.
- Q3 — local embeddings by default — still unmapped, separate future phase.
- MCP Custom-Connector local-auth branch (`oauth_authorize.py`) — separate scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-------------------|
| LAUTH-01 | A user registers and signs in with email + password on an install that has NO Google OAuth and NO GitHub App configured — zero third-party setup required. Passwords stored only as a salted memory-hard KDF hash (argon2id/bcrypt). | Q1 (argon2-cffi hashing API + verified cost defaults), Q4 (exact register/login call sequence reusing `_mint_xbt_for_user`/`teams_repo.create_team`), Q5 (migration 0024 shape for `local_credentials`), Environment Availability (confirms argon2-cffi has wheels for both host architectures, no OAuth env vars required to boot) |
| LAUTH-02 | The local-auth principal is indistinguishable downstream — `get_current_principal` returns the same shape and every team-scoped route authorizes it identically, with no per-route special case. Google OAuth and the GitHub App keep working unchanged when configured. | "The single most important finding" reused from CONTEXT.md + Q4 (mint via the EXISTING `xbt_` INSERT, zero changes to `deps.py`'s `xbt_` branch); Validation Architecture (test asserting a local-register `xbt_` authorizes a real team-scoped route, and a regression run of the existing `test_phase10_auth.py` proving GitHub/Google are unchanged) |
</phase_requirements>

## Summary

This phase adds a sixth branch to a `get_current_principal` resolver that already
juggles five (Google ID token, Google access token, `ghu_` GitHub App token, legacy
`gho_` GitHub OAuth token, `xbt_` personal API token, bridge JWT) — but the new branch
is not really a new branch at all. `register`/`login` terminate by minting an `xbt_`
token through the exact same `user_api_tokens` INSERT that `auth_github.py` and
`me.py` already use, so `get_current_principal`'s existing `xbt_` branch
(`deps.py:225-272`) needs zero changes. The entire net-new surface is: one small
table (`local_credentials`), one KDF (argon2id via `argon2-cffi`), two routes
(`register`, `login`) plus a `set-password` route, and an in-process rate
limiter — no new dependency on Redis, no new principal kind, no JWT scheme.

Two corrections to 18-CONTEXT.md's canonical refs surfaced during this research and
must be applied before planning:

1. **Migration numbering is stale.** CONTEXT.md says "Latest migration is 0021 — the
   new one is 0022." Two migrations landed after CONTEXT.md was written
   (`0022_oauth_as_tables.py`, `0023_tasks_source_connector.py`, confirmed via a
   linear, unbranched `down_revision` chain). **The new Phase 18 migration is
   `0024`, not `0022`.**
2. **`argon2-cffi`'s own shipped defaults are the correct cost parameters** — this
   phase does not need to choose OWASP's raw `m`/`t`/`p` numbers by hand. See Q1.

Primary recommendation: reuse `_mint_xbt_for_user` and `teams_repo.create_team`
verbatim inside a new `app/routes/auth_local.py`, following the exact
one-transaction/one-commit shape of `auth_github.py`'s `signin_github`; hash
passwords with `argon2.PasswordHasher()` unmodified (no custom cost params); rate
limit with the `limits` package's in-memory `MovingWindowRateLimiter` (pure-Python,
no C extension, no Redis) rather than hand-rolling a dict+lock; and write the
Alembic migration in the raw-`op.execute()` SQL style of `0013_api_tokens.py`,
the closest sibling table.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Password hashing (argon2id) | API / Backend | — | Never in browser/client; `argon2-cffi` runs server-side only inside `memory-api` |
| Credential storage (`local_credentials`) | Database / Storage | API / Backend | Postgres table; only `memory-api` reads/writes it, no direct DB access from other services (matches existing `user_api_tokens` pattern) |
| Session minting (`xbt_`) | API / Backend | Database / Storage | `_mint_xbt_for_user` — existing pattern, unchanged |
| Rate limiting (in-process) | API / Backend | — | Per-worker in-memory state inside the `memory-api` FastAPI process; explicitly NOT at the nginx/CDN tier (no `limit_req` exists today — see Q3) |
| Lockout (persistent) | Database / Storage | API / Backend | `local_credentials.failed_attempts`/`locked_until` — survives restarts, unlike rate limiting |
| Register/Sign-in/Set-password UI | Browser / Client | — | Static vanilla-JS pages under `app-site/account/`, same pattern as `app-site/account/teams/index.html` — no framework, no SSR |
| Recovery (locked-out user) | Database / Storage | — | Operator-run `psql`/Alembic-adjacent script or admin CLI; explicitly NOT email (SMTP out of scope) |

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `argon2-cffi` | 25.1.0 [VERIFIED: PyPI registry, `pypi.org/pypi/argon2-cffi/json`, 2026-07-14] | argon2id password hashing | OWASP's 2024+ recommended default KDF [CITED: OWASP Password Storage Cheat Sheet]; already named by D-18-04. `argon2-cffi` is the de facto standard Python binding (maintained by hynek, used by Django's built-in Argon2PasswordHasher). |
| `argon2-cffi-bindings` | 25.1.0 (transitive dep of `argon2-cffi`) [VERIFIED: PyPI registry] | C bindings to the reference Argon2 implementation | Ships prebuilt wheels — see "Installation" below. Not a direct pyproject dependency; pulled in automatically. |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `limits` | 5.8.0 [VERIFIED: PyPI registry] | In-process rate limiting (token bucket / moving window / fixed window strategies) | Register/login/set-password routes — see Q3. Pure Python (`py3-none-any` wheel), the base install pulls **no** Redis/Memcached client — those are optional `extras` (`redis`, `async-redis`, `memcached`, etc.) that this phase must NOT add. |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `argon2-cffi` | `bcrypt` | D-18-04 already locks argon2id; bcrypt has a 72-byte password truncation footgun and no memory-hardness — inferior for new code per OWASP. Not evaluated further — decision is locked. |
| `limits` (in-memory backend) | Hand-rolled `dict[str, list[float]]` + `asyncio.Lock` | `limits` is a maintained, tested library implementing the standard strategies (fixed window, moving window, sliding window counter, token bucket) correctly, including window-boundary edge cases a hand-rolled version would get wrong (burst-at-boundary double-counting). Zero extra runtime dependency risk (pure Python, no C extension) — there is no dependency-weight argument for hand-rolling here. Recommended over hand-rolling. |
| `limits` | `slowapi` | `slowapi` is a FastAPI-specific wrapper *around* `limits` (adds a decorator API + `Request`-based key funcs) but has had lower maintenance velocity historically and pulls in the same `limits` core anyway. Since this phase needs a small number of hand-placed dependency checks (not global middleware), using `limits` directly (as shown in Q3) is simpler and avoids an extra layer. Not independently verified for this session — flagged LOW confidence, not adopted. |

**Installation:**
```bash
# apps/memory-api/pyproject.toml — add to `dependencies = [...]`
"argon2-cffi>=25.1.0",
"limits>=5.8.0",
```

**Version verification:** confirmed live against the PyPI JSON API on 2026-07-14 (`curl -s https://pypi.org/pypi/argon2-cffi/json`, `.../limits/json`, `.../argon2-cffi-bindings/json`). `argon2-cffi` itself requires only `argon2-cffi-bindings` + `typing-extensions`, no other new transitive deps. `limits` requires `deprecated`, `packaging`, `typing-extensions` — all small, pure-Python, no arch-specific wheels needed.

## Architecture Patterns

### System Architecture Diagram

```
Browser (app-site/account/*.html, vanilla JS)
   |
   |  POST /v1/auth/local/register  { email, password }
   |  POST /v1/auth/local/login     { email, password }
   |  POST /v1/me/set-password      { new_password } (Authorization: Bearer xbt_...)
   v
memory-api (FastAPI, uvicorn --workers 2)
   |
   |-- [rate-limit dependency, in-process, keyed by IP] --> 429 if exceeded
   |
   |-- register:
   |     1. normalize email (trim + lower)
   |     2. get_user_by_email(session, email) -- collision check (ANY source: local/github/google)
   |          -> found?  409, no writes, generic-enough message (D-18-05)
   |          -> not found: continue
   |     3. get_or_create_user(source_user_id=f"email:{email}", email=email)  [flush, no commit]
   |     4. INSERT local_credentials(user_id, password_hash=argon2id-hash, algo='argon2id')
   |     5. teams_repo.create_team(slug=f"solo-{user.id.hex[:16]}", ...)  [flush, no commit]
   |     6. _mint_xbt_for_user(session, user.id)  [flush, no commit]
   |     7. session.commit()   <-- SINGLE commit, matches auth_github.py Step 8
   |     8. return { xbt_token, user }
   |
   |-- login:
   |     1. normalize email
   |     2. row = get_user_by_email + local_credentials JOIN
   |          -> not found OR no local_credentials row:
   |               run ph.verify(DECOY_HASH, password) [swallow result] -- equalizes timing
   |               return 401 generic
   |          -> found, locked_until > now():
   |               run ph.verify(DECOY_HASH, password) [swallow result] -- equalizes timing
   |               return 401 generic (NOT 423 -- see Open Question 1, avoids a distinct lockout oracle)
   |          -> found, not locked:
   |               ph.verify(row.password_hash, password)
   |                 success -> reset failed_attempts=0, locked_until=NULL,
   |                            check_needs_rehash() -> re-hash+persist if stale,
   |                            _mint_xbt_for_user, commit, return 200
   |                 failure -> failed_attempts += 1;
   |                            if failed_attempts >= N: locked_until = now()+cooldown
   |                            commit, return 401 generic
   |
   v
Postgres: users, local_credentials (NEW), user_api_tokens, teams, team_members
   |
   v
xbt_ token returned to browser -> stored client-side -> sent as
`Authorization: Bearer xbt_...` on every subsequent call -> resolved by the
EXISTING deps.py:225-272 xbt_ branch (zero code changes) -> get_team_scope
authorizes against team_members exactly like a GitHub/Google principal.
```

### Recommended Project Structure

```
apps/memory-api/app/
├── routes/
│   └── auth_local.py       # NEW — register / login / (optionally) set-password
├── repos/
│   └── local_credentials.py  # NEW — get/create/update local_credentials row, lockout helpers
├── services/
│   └── password_hash.py    # NEW — thin wrapper around argon2.PasswordHasher, decoy-hash constant
alembic/versions/
└── 0024_local_credentials.py  # NEW migration (see Q5 — NOT 0022)
app-site/account/
├── register/index.html     # NEW — mirrors teams/index.html's inline-CSS vanilla-JS pattern
├── login/index.html        # NEW
└── (set-password screen — could live inside teams/index.html's existing account surface, or its own page — Claude's Discretion per CONTEXT.md)
```

### Pattern 1: Single-transaction, single-commit route (mirror `auth_github.py`)

**What:** Every mutating step (user creation, credential insert, team bootstrap,
token mint, audit write) happens on the SAME `AsyncSession`, using `flush()` (not
`commit()`) between steps, with exactly one `await session.commit()` at the very
end.
**When to use:** `register`. This is the load-bearing pattern the whole phase rests
on — it is what makes "call self-solo in-process" simultaneously safe and cheap.
**Example:**
```python
# Source: apps/memory-api/app/routes/auth_github.py:370-530 (signin_github),
# apps/memory-api/app/routes/teams.py:445-492 (self_create_solo_team) — the
# register route should call teams_repo.create_team() directly (the repo
# function), NOT the /v1/teams/self-solo HTTP route, because that route
# performs its OWN internal session.commit() (teams.py:485) which would break
# the single-commit invariant this pattern depends on.
async def register(body: RegisterBody, session: AsyncSession = Depends(get_session)):
    email = body.email.strip().lower()
    if await users_repo.get_user_by_email(session, email) is not None:
        raise HTTPException(409, "This email already has an account — sign in "
                                  "with your existing method, then add a "
                                  "password in settings.")
    user = await users_repo.get_or_create_user(
        session, source_user_id=f"email:{email}", email=email,
    )
    await local_credentials_repo.create(
        session, user_id=user.id, password_hash=ph.hash(body.password), algo="argon2id",
    )
    slug = f"solo-{user.id.hex[:16]}"
    team = await teams_repo.create_team(
        session, slug=slug, display_name="My Workspace",
        creator_user_id=user.id, visibility="closed", github_org=None,
    )
    xbt = await _mint_xbt_for_user(session, user.id)  # from auth_github.py, import directly
    await session.commit()
    return {"xbt_token": xbt, "user": {...}}
```

### Pattern 2: Decoy-hash timing equalization

**What:** A module-level, precomputed argon2id hash of an arbitrary fixed string,
verified (and discarded) whenever the real lookup path would otherwise short-circuit
(email not found, or account locked). This makes the CPU cost of "user does not
exist" and "user exists, wrong password" observably similar.
**When to use:** `login`, in every branch that does NOT reach a real
`ph.verify(row.password_hash, ...)` call.
**Example:**
```python
# Source: pattern is well-established (Django's `authenticate()` does the
# equivalent via `Argon2PasswordHasher().hash(password, salt="stub")` on a
# nonexistent user); argon2-cffi's own README does not name it, but the
# equalization technique is documented at https://cheatsheetseries.owasp.org/
# cheatsheets/Authentication_Cheat_Sheet.html ("code that will go through the
# same process no matter what the user or password is").
_DECOY_HASH = PasswordHasher().hash("xbrain-local-auth-decoy-2026")  # computed once at import time

async def login(body: LoginBody, session: AsyncSession = Depends(get_session)):
    email = body.email.strip().lower()
    row = await local_credentials_repo.get_by_email(session, email)
    if row is None or (row.locked_until and row.locked_until > utcnow()):
        with contextlib.suppress(Exception):
            ph.verify(_DECOY_HASH, body.password)
        raise HTTPException(401, "Invalid email or password.")
    try:
        ph.verify(row.password_hash, body.password)
    except VerifyMismatchError:
        await local_credentials_repo.record_failure(session, row.user_id)
        raise HTTPException(401, "Invalid email or password.")
```

### Anti-Patterns to Avoid

- **Calling `POST /v1/teams/self-solo` over HTTP (or via a TestClient-style
  in-process ASGI call) from inside `register`:** that route commits its own
  transaction (`teams.py:485`) and writes its own audit row — nesting it inside
  `register`'s transaction either double-commits or silently orphans the
  team-creation audit entry outside the atomic boundary. Call `teams_repo.create_team`
  directly instead (see Pattern 1).
- **Returning a distinct status/message for "account locked" vs "wrong password" on
  `/login`:** turns lockout state into a second user-enumeration oracle (an attacker
  who gets a different response for a locked account than for a wrong password on a
  nonexistent one has just learned the account exists AND is currently locked).
- **Overriding `PasswordHasher()`'s constructor params by hand:** D-18-03/D-18-04's
  own discretion note says "use argon2-cffi's sane defaults; do not hand-roll" — see
  Q1 for why the shipped default is already the right choice for this box size.
- **Adding a plain `UNIQUE` index on `users.email` (or `lower(email)`) as part of
  this migration:** see Q5 — likely to fail outright against existing data and is
  not needed for D-18-05's correctness.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Password hashing | A custom PBKDF2/SHA loop, or manual salt+hash concatenation | `argon2.PasswordHasher()` (default params) | Argon2's parameter tuning, timing-safe verify, and self-describing encoded format are exactly the things a hand implementation gets wrong. D-18-04 already locks this. |
| Rehash-on-upgrade detection | Manually comparing stored params to "current" constants | `ph.check_needs_rehash(hash)` | Handles the encoded-string parsing and comparison correctly; built into the exact library already chosen. |
| Rate limiting logic | `dict[key] = deque(timestamps)` + manual window math | `limits.strategies.MovingWindowRateLimiter` + `limits.storage.MemoryStorage` | Window-boundary correctness (burst-at-boundary, monotonic clock handling) is a solved, tested problem in `limits`; hand-rolling reproduces known FastAPI-blog-post bugs. |
| Timing-safe string comparison for tokens | `token == stored_token` | Already unnecessary here — `user_api_tokens` lookup is a SHA-256 hash+equality DB query (existing pattern, `deps.py:226`), and password verification goes through argon2's own constant-time compare inside `ph.verify()`. No new hand-rolled comparison needed. |

**Key insight:** Every piece of this phase that looks like it needs custom code
(hashing, rehash detection, rate limiting) already has a narrowly-scoped, small,
well-maintained library that does exactly that one thing — the only genuinely new
code is the glue (routes + one repo module) wiring them into the existing
`get_or_create_user` / `create_team` / `_mint_xbt_for_user` call chain.

## Common Pitfalls

### Pitfall 1: Relying on `source_user_id` uniqueness alone for D-18-05's collision check

**What goes wrong:** Checking `SELECT ... WHERE source_user_id = 'email:<addr>'`
before insert looks race-safe (it mirrors `get_or_create_user`'s `ON CONFLICT
(source_user_id) DO NOTHING` pattern) but it does **not** catch the case where the
same real-world email already belongs to a GitHub or Google account under a
DIFFERENT `source_user_id` convention (`github:<login>`, or the raw OIDC `sub`).
**Why it happens:** `source_user_id` is a convention-per-auth-method key, not a
canonical identity key. Only `users.email` is (loosely) canonical, and it has no
DB-level uniqueness (see Q5).
**How to avoid:** The D-18-05 collision check MUST be `get_user_by_email(session,
normalized_email)` (the existing, case-insensitive helper at
`repos/users.py:60-73`), not a `source_user_id` lookup. This is what Pattern 1's
example does.
**Warning signs:** A test where a GitHub user with `email=alice@x.com` is followed by
a local `register(email="alice@x.com", ...)` returns 200 instead of 409 — that is
this pitfall live.

### Pitfall 2: Case mismatch between the pre-existing `email:<addr>` bridge convention and Phase 18's normalization

**What goes wrong:** `deps.py:314` (the `librechat-onboarding` bridge branch) mints
`source_user_id=f"email:{claims['email']}"` **without** lowercasing. If that path
already created a row `source_user_id="email:Alice@Example.com"`, and Phase 18's
`register` normalizes to `alice@example.com` before building
`source_user_id="email:alice@example.com"`, the two are DIFFERENT strings at the
`source_user_id`-uniqueness level — but `get_user_by_email`'s
`func.lower(User.email) == email.strip().lower()` comparison (Pitfall 1's fix) DOES
still catch this, because it compares the `email` column value, not
`source_user_id`. This is exactly why Pitfall 1's fix (email-column check, not
source_user_id check) is mandatory, not merely a style preference.
**How to avoid:** Always resolve collisions via `get_user_by_email`, never via
constructing the candidate `source_user_id` and checking that.

### Pitfall 3: Multi-worker rate-limit bypass (verified against this repo's actual deploy config)

**What goes wrong:** `apps/memory-api/Dockerfile:30` runs
`uvicorn ... --workers 2`. An in-process (single-Python-process) rate limiter —
whether hand-rolled or via `limits`' `MemoryStorage` — keeps its counters in the
memory of ONE worker process. With 2 workers behind uvicorn's OS-level socket
load-balancing, a burst of N login attempts is split roughly evenly between the two
processes; each worker independently allows its own configured threshold before
tripping, so the EFFECTIVE limit an attacker experiences is close to 2x the
configured per-process limit, not the configured limit.
**Why it happens:** `limits.storage.MemoryStorage` (and any hand-rolled dict) is
process-local by construction; there is no cross-process coordination without a
shared store (Redis, Memcached — explicitly out of scope per D-18-06).
**How to avoid:** This cannot be fully "avoided" without adding Redis, which
D-18-06 explicitly forbids. The correct response per D-18-06's own text ("note in
the plan that a multi-replica hosted deployment would want a shared store") is to
**document the limitation explicitly in the plan and in code comments**, and treat
the DB-backed per-account `lockout` (which is NOT per-process — it lives in
Postgres, shared across all workers) as the actually-durable defense; the in-process
rate limiter is a coarse first line of defense against unsophisticated spam, not a
hard guarantee.
**Warning signs:** A verification test that asserts "exactly N requests get through"
against a real multi-worker deployment (rather than the single-process test client)
would flake or fail — the plan's verification should test the DB lockout (durable,
deterministic) as the load-bearing abuse-resistance proof, and treat the rate
limiter's own test as "fires within a single process," not "fires exactly at N
across the whole deployment."

### Pitfall 4: A hard `UNIQUE` index on `users.email` breaking the migration outright

**What goes wrong:** `CREATE UNIQUE INDEX ... ON users (lower(email))` (no `WHERE`
clause) fails migration entirely — not silently, not partially — if even one
duplicate (case-insensitive) email already exists in the table. Given the
`librechat-onboarding` bridge path (Pitfall 2) does not normalize case before
writing `users.email`, and `users.email` has never had any uniqueness constraint
since `0001_initial.py:30`, duplicates are plausible on any deployment with real
history (this could not be verified against live data in this research session —
no DB was queried — see Assumptions Log A1).
**Why it happens:** Postgres validates a `CREATE UNIQUE INDEX` against the FULL
existing table at creation time; there is no "skip conflicting rows" mode for a
plain unique index (only `CREATE UNIQUE INDEX CONCURRENTLY` changes lock behavior,
not the duplicate-rejection behavior).
**How to avoid:** Don't add it. See Q5 — it is not needed for D-18-05's correctness
because the collision check is already enforced at the application layer via
`get_user_by_email` (Pitfall 1/2's fix), and `source_user_id`'s existing UNIQUE
constraint already prevents the one case a DB constraint COULD help with (two
concurrent same-normalized-email local registrations racing each other).

## Code Examples

### Q1 — Hashing and verifying with argon2-cffi (the exact API)

```python
# Source: https://argon2-cffi.readthedocs.io/en/stable/howto.html [CITED]
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

ph = PasswordHasher()                      # module-level singleton — cheap to construct
encoded = ph.hash("correct horse battery staple")
# encoded == "$argon2id$v=19$m=65536,t=3,p=4$<salt>$<digest>"  -- self-describing

try:
    ph.verify(encoded, "correct horse battery staple")   # True on success
except VerifyMismatchError:
    ...  # wrong password
except (VerificationError, InvalidHashError):
    ...  # malformed / foreign hash — treat as a hard failure, not "wrong password"

if ph.check_needs_rehash(encoded):
    new_encoded = ph.hash("correct horse battery staple")
    # persist new_encoded, update algo tag if the TYPE changed (it won't here —
    # algo stays 'argon2id' unless a future migration explicitly changes KDF)
```
`ph.hash()`/`ph.verify()`/`ph.check_needs_rehash()` signatures and exception types
[CITED: https://argon2-cffi.readthedocs.io/en/stable/api.html].

### Q4 — `_mint_xbt_for_user`, verbatim, to import and reuse

```python
# Source: apps/memory-api/app/routes/auth_github.py:350-367 [VERIFIED: read directly]
async def _mint_xbt_for_user(session: AsyncSession, user_id) -> str:
    raw = "xbt_" + secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    await session.execute(sa.text("""
        INSERT INTO user_api_tokens (id, user_id, token_hash, team_scope, name, created_at)
        VALUES (gen_random_uuid(), :user_id, :hash, '', 'github-signin', now())
    """), {"user_id": user_id, "hash": token_hash})
    return raw
```
This function is defined in `auth_github.py`, not a shared `services/` module.
The plan should either (a) import it directly (`from app.routes.auth_github import
_mint_xbt_for_user` — works, Python doesn't enforce route-module privacy, but is a
slightly unusual import direction), or (b) move it to a shared location (e.g.
`app/services/api_tokens.py`) and re-export/import from both `auth_github.py` and
the new `auth_local.py`. **Recommendation: option (b)** — it's a 6-line function,
moving it once now avoids a growing pile of near-duplicate mint functions (there
are already two near-identical mints: this one and `me.py:237-238`'s
`create_api_token`, which additionally accepts a caller-supplied `team_scope` and
`name`). This is a planning decision, not a hard requirement from CONTEXT.md — flag
as Claude's Discretion territory the CONTEXT.md doesn't explicitly cover.

The `name` field hardcodes `'github-signin'` in the existing function — the local-auth
mint should NOT reuse that literal; either parameterize `name` or write a second
near-identical insert with `name='local-register'` / `'local-login'`.

### Q3 — `limits` in-process rate limiting

```python
# Source: limits library public API, PyPI description + README pattern
# [CITED: https://pypi.org/project/limits/, cross-checked against WebSearch example]
from limits import parse
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter

_storage = MemoryStorage()                              # module-level singleton
_limiter = MovingWindowRateLimiter(_storage)
_login_rate = parse("10/minute")                         # per-key budget

async def rate_limit_login(request: Request) -> None:
    key = request.client.host if request.client else "unknown"
    if not _limiter.hit(_login_rate, "login", key):
        raise HTTPException(429, "Too many attempts — try again shortly.")

# wired as a FastAPI dependency:
# @router.post("/auth/local/login", dependencies=[Depends(rate_limit_login)])
```
`limits.hit()` is synchronous (in-memory dict operations, no I/O) — safe to call
from an async route without blocking the event loop for any meaningful duration.
Confirmed no Redis/Memcached client is imported unless the corresponding storage
backend class is explicitly constructed (`RedisStorage`, etc.) — `MemoryStorage`
alone pulls zero optional extras [VERIFIED: PyPI `requires_dist`, extras are
conditional on `extra == "..."` markers].

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|---------------|--------|
| bcrypt/scrypt/PBKDF2 as default KDF choice | argon2id | OWASP made Argon2id the top recommendation starting with the 2021 Password Storage Cheat Sheet revision, reaffirmed through 2024+ updates [CITED: OWASP Password Storage Cheat Sheet] | D-18-04 already reflects this — no action needed, just confirms the locked decision is current best practice, not stale. |
| Manual cost-parameter tuning | Library-shipped RFC 9106 profiles (`argon2.profiles.RFC_9106_LOW_MEMORY` / `RFC_9106_HIGH_MEMORY`) | `argon2-cffi` v21.2.0 switched its `PasswordHasher` default to `RFC_9106_LOW_MEMORY` [CITED: argon2-cffi changelog via readthedocs] | This phase should NOT hand-pick `m`/`t`/`p` — see Q1. |

**Deprecated/outdated:** None specific to this phase — this is new-ground code, not
a migration off something old.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `users.email` may already contain case-insensitive duplicates in the live/production dataset | Pitfall 4, Q5 | If wrong (no duplicates exist today), a partial or full unique index would actually succeed — but the recommendation to skip it stands regardless, because D-18-05's correctness does not depend on a DB constraint (see Q5's full reasoning), so this assumption being wrong does not change the recommendation, only its urgency. No DB was queried in this research session to confirm either way. |
| A2 | `argon2.PasswordHasher()`'s default `p=4` (parallelism/lanes) does not cause problems on a 1-2 vCPU box beyond "somewhat serialized, still correct and still fast enough" | Q1 | If wrong (e.g. some obscure contention/OOM interaction under heavy concurrent login load), the fix is a one-line override (`PasswordHasher(parallelism=1)`) discoverable at load-test time; low blast radius. Standard Argon2/RFC 9106 engineering knowledge, not independently load-tested in this session. |
| A3 | Moving `_mint_xbt_for_user` to a shared `services/` module (rather than importing it from `auth_github.py`) is the right call | Q4, Code Examples | Purely a code-organization choice with no functional risk either way; flagged as Claude's Discretion, not a locked requirement. |

## Open Questions

> **RESOLVED during planning (2026-07-14).** These were settled by the Phase 18 plans; kept below as a
> historical record, not open items:
> - Q1 (locked-but-correct login response shape) -> generic 401 always, never 423 — settled in **18-03**.
> - Q2 (lockout threshold/cooldown) -> 5 attempts / 15 minutes, env-overridable — settled in **18-01**.
> - Q3 (set-password one endpoint vs two) -> one endpoint that branches (first-attach vs old-pw change) — settled in **18-04**.

1. **Lockout HTTP status/response shape for a locked-but-correct-password login attempt**
   - What we know: D-18-06 requires "no user-enumeration oracle" and "same generic
     message" for login failures. Pitfall 3's Pattern 2 example applies this by
     returning a generic 401 even when the account is locked (never 423).
   - What's unclear: whether the FRONTEND (the minimal register/login UI, SC#5)
     needs ANY way to tell a real user "you're locked out, try again in N minutes"
     vs. "wrong password" — a generic 401 forever is bad UX for a legitimately
     locked-out user who then just retries the same wrong-looking error N times.
   - Recommendation: keep the API response generic (security requirement, from
     D-18-06, non-negotiable) but consider a SEPARATE authenticated-adjacent
     endpoint or a client-side cooldown-timer heuristic (e.g. after 3 failed
     attempts, the UI itself starts showing "if this is your account, wait a few
     minutes" without the SERVER confirming account existence) — this is a UI/UX
     decision for the planner, not a security one.

2. **Exact lockout threshold (`N` failed attempts) and cooldown duration**
   - What we know: CONTEXT.md's "Claude's Discretion" explicitly leaves "lockout
     thresholds/cooldown values" open.
   - What's unclear: no specific number was researched/verified against an
     authoritative source in this session.
   - Recommendation: OWASP's Credential Stuffing Prevention guidance and common
     practice (Django's `django-axes`, GitHub's own lockout) commonly use 5-10
     failed attempts before a 5-15 minute lockout. Suggest **5 attempts / 15
     minute cooldown** as a defensible, documented default, configurable via env
     (matching the existing `RELEVANCE_HAIKU_TIMEOUT_S`-style env-configurable
     pattern in `config.py`) — this is a suggestion for the planner to lock in
     CONTEXT-equivalent, not independently verified against an authoritative
     source this session.

3. **Whether `set-password` is one endpoint (upsert) or two (`set` for first-time,
   `change` requiring old password)**
   - What we know: CONTEXT.md leaves this as Claude's Discretion explicitly.
   - What's unclear: nothing technical blocks either design — this is pure product
     shape.
   - Recommendation: given D-18-05's convergence design (a GitHub/Google user
     "attaches" a password for the first time via `set-password` while already
     authenticated), the FIRST-TIME-ATTACH case has no old password to check by
     definition. A SUBSEQUENT change (existing local user changing their own
     password) arguably should require the current password as a defense against
     session-hijack-then-silent-takeover. Recommend **one endpoint that branches
     internally**: if `local_credentials` row doesn't exist yet, `old_password` is
     not required; if it exists, `old_password` is required and verified before
     the update. This keeps the API surface small (matches SC#5's "minimal") while
     still closing the "hijacked session silently locks out the real owner" gap.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Docker | Verification (testcontainers-Postgres) | check at execute time — repo's existing test suite already gates on `_docker_available()` (`tests/conftest.py:56-64`) and skips integration tests gracefully if absent | — | Tests self-skip with `pytest.skip("Docker not available...")` — no plan-level fallback needed, this is already handled by the existing fixture. |
| `argon2-cffi-bindings` prebuilt wheel (manylinux aarch64 + x86_64) | Docker image build (host is ARM64, prod is amd64) | ✓ | 25.1.0 has `manylinux_2_26_aarch64.manylinux_2_28_aarch64` AND `manylinux_2_26_x86_64.manylinux_2_28_x86_64` wheels, both `cp39-abi3` (covers the repo's `python:3.12-slim` base image) [VERIFIED: PyPI registry file listing] | No fallback needed — this is a hard requirement per the phase's hard_constraints, and it is satisfied. |
| `limits` wheel | Same | ✓ | `py3-none-any` — architecture-independent, pure Python [VERIFIED: PyPI registry] | N/A |
| C compiler / build-essential in Docker image | argon2-cffi-bindings build (IF no prebuilt wheel matched) | Not present — `Dockerfile`'s builder stage is `python:3.12-slim` with no `apt-get install gcc`/`build-essential` [VERIFIED: read `apps/memory-api/Dockerfile:4-11`] | — | Not needed — the prebuilt wheel match above means `pip install` never falls back to source build. If this ever changed (e.g. a future Python version with no wheel yet), the build would fail loudly at `docker build` time, not silently at runtime — acceptable fail-fast behavior, no action needed now. |

**Missing dependencies with no fallback:** None.

**Missing dependencies with fallback:** None — both new dependencies have confirmed prebuilt wheels for both target architectures.

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.3+ / pytest-asyncio 0.25+ (existing, `apps/memory-api/pyproject.toml:34-35`) |
| Config file | `apps/memory-api/pyproject.toml` (`[tool.pytest.ini_options]`, `asyncio_mode = "auto"`, `testpaths = ["tests"]`) |
| Quick run command | `cd apps/memory-api && pytest tests/test_local_auth.py -x` (unit-shaped tests, no `@pytest.mark.integration`, no Docker) |
| Full suite command | `cd apps/memory-api && pytest tests/ -x` (includes `@pytest.mark.integration` tests that spin `testcontainers.postgres.PostgresContainer` — gated on `_docker_available()`) |

The repo's existing integration pattern is exactly what CONTEXT.md's "gate lesson"
demands: `tests/conftest.py`'s `pg_url` fixture (session-scoped) starts a real
`postgres:17` container, runs `alembic upgrade head` against it via
`asyncio.to_thread(command.upgrade, cfg, "head")`, and the `client` fixture builds a
real `httpx.AsyncClient` bound to the real FastAPI `app` with only `get_session`
overridden (everything else — `get_current_principal`, `get_team_scope`, the actual
route handlers — runs unmodified). `test_phase10_auth.py` is the closest existing
precedent: it uses `respx` to mock the GitHub HTTP calls but drives the REAL
`/v1/auth/github/signin` route against the REAL testcontainers Postgres. Phase 18's
local-auth tests need no `respx` mocking at all (no external HTTP calls in the
register/login path) — they are even more end-to-end than the GitHub tests by
default.

### Phase Requirements -> Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| LAUTH-01 | Register with brand-new email on a clean install (NO `GOOGLE_CLIENT_ID`/`GITHUB_APP_*` set) succeeds, returns `xbt_` token | integration | `pytest tests/test_local_auth.py::test_register_new_email -x` | ❌ Wave 0 |
| LAUTH-01 | Login with correct email+password succeeds, returns `xbt_` token | integration | `pytest tests/test_local_auth.py::test_login_success -x` | ❌ Wave 0 |
| LAUTH-01 | Persisted `local_credentials.password_hash` is argon2id, never plaintext (row-level assertion) | integration | `pytest tests/test_local_auth.py::test_password_hash_is_argon2id -x` | ❌ Wave 0 |
| LAUTH-02 | `xbt_` token from local register authorizes a REAL team-scoped route (e.g. `/v1/memory/search` or any `CORE_ROUTERS` route requiring `get_team_scope`) with 200, proving the principal is genuinely indistinguishable | integration | `pytest tests/test_local_auth.py::test_local_xbt_authorizes_team_route -x` | ❌ Wave 0 |
| LAUTH-02 | Google/GitHub sign-in still resolves unchanged when configured (regression) | integration | `pytest tests/test_phase10_auth.py -x` (existing file — re-run, not modify) | ✅ (existing) |
| D-18-05 | Register with an email that already has an account (GitHub/Google/local) -> 409, no `xbt_` returned, no new row created | integration | `pytest tests/test_local_auth.py::test_register_collision_409 -x` | ❌ Wave 0 |
| D-18-06 | Lockout fires after N consecutive failures against the REAL table; a correct password after `locked_until` expiry succeeds | integration | `pytest tests/test_local_auth.py::test_lockout_then_recovery -x` | ❌ Wave 0 |
| D-18-06 | Login failure for a nonexistent email and login failure for a wrong password on an existing email return the SAME status + SAME generic message body | unit or integration | `pytest tests/test_local_auth.py::test_no_enumeration_oracle -x` | ❌ Wave 0 |
| SC#1 | Full register -> login -> authorized-request loop succeeds on a clean boot with NO Google/GitHub vars set | integration | `pytest tests/test_local_auth.py::test_clean_boot_no_oauth_e2e -x` | ❌ Wave 0 |
| SC#6 (edition gating) | `auth_local.router` is classified in `CORE_ROUTERS` (not forgotten) | unit | `pytest tests/test_edition_gating.py::test_every_router_module_is_classified -x` (existing file — will auto-catch an unclassified new router) | ✅ (existing, auto-covers new router) |

### Sampling Rate

- **Per task commit:** `cd apps/memory-api && pytest tests/test_local_auth.py -x` (skips Docker-gated tests automatically if Docker unavailable, per existing `_docker_available()` fixture behavior)
- **Per wave merge:** `cd apps/memory-api && pytest tests/ -x` (full suite, including the pre-existing `test_edition_gating.py` and `test_phase10_auth.py` regression coverage)
- **Phase gate:** Full suite green before `/gsd-verify-work`, including the explicit regression run of `test_phase10_auth.py` and `test_edition_gating.py` — these prove SC#4 (Google/GitHub unchanged) and the router-classification trap respectively, without writing any new test code for those two guarantees.

### Wave 0 Gaps

- [ ] `tests/test_local_auth.py` — new file, covers LAUTH-01, LAUTH-02, D-18-05, D-18-06, SC#1 (all rows above marked Wave 0)
- [ ] `tests/conftest.py` — no new fixtures strictly required; `seeded_two_teams` (existing) is reusable for the register-collision test (seed an existing GitHub-style user via `users_repo.get_or_create_user` with `source_user_id="github:someone"` before attempting a colliding local register)
- [ ] Framework install: none — `pytest`/`pytest-asyncio`/`testcontainers[postgres]`/`respx` are already dev dependencies (`pyproject.toml:33-40`)

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-------------------|
| V2 Authentication | yes | `argon2-cffi` (`PasswordHasher`, default RFC 9106 LOW_MEMORY profile) for storage; lockout (`local_credentials.failed_attempts`/`locked_until`) for brute-force resistance |
| V3 Session Management | yes (reused, not new) | `xbt_` opaque token, SHA-256 hashed at rest in `user_api_tokens.token_hash` — existing mechanism, unchanged by this phase |
| V4 Access Control | yes (reused, not new) | `get_team_scope` / `team_members` membership check — existing mechanism, unchanged |
| V5 Input Validation | yes | Pydantic `BaseModel` request bodies (matches every other route in this codebase — `SigninGithubBody`, `ApiTokenCreateBody`, etc.); email format + password minimum length validated via `pydantic.Field` constraints |
| V6 Cryptography (password storage specifically called out under ASVS V6.2/V2.4) | yes | `argon2-cffi` — never hand-rolled hashing, never a bare SHA/MD5 |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|----------------------|
| User enumeration via login response difference (status/message) | Information Disclosure | Generic 401 + same message text for "no such email," "wrong password," and "locked" cases (Pattern 2, Pitfall 3-adjacent) |
| User enumeration via login TIMING difference | Information Disclosure | Decoy-hash `ph.verify()` call on every short-circuit branch (Pattern 2) |
| Credential stuffing / brute force | Elevation of Privilege | DB-backed per-account lockout (durable across restarts/workers) + in-process per-IP rate limit (coarse first line, explicitly documented as NOT durable across the 2-worker deployment — Pitfall 3) |
| Account takeover via cold-register convergence (register with a victim's known email attaches to their existing account) | Spoofing | D-18-05's locked design: cold register on an existing email is REJECTED (409, no access), convergence only happens via the AUTHENTICATED `set-password` path, which requires proof of ownership via an existing valid session |
| Registration-endpoint enumeration (409 on collision reveals the email is registered) | Information Disclosure | **Accepted, locked risk** — D-18-05 explicitly specifies the 409 + message. This is standard industry practice for registration flows specifically (GitHub, Google, Slack all reveal "email already in use" at registration) and is a materially lower-value oracle than a login-endpoint one (it costs an attacker one request per guess with no credential-stuffing payoff, and is covered by the same rate limiter as login). Not re-litigated here per the phase's hard_constraints — documented as a conscious, industry-normal tradeoff rather than an oversight. |
| Timing-safe secret comparison for `xbt_` tokens | Tampering | Already handled by the existing SHA-256-hash-then-DB-equality lookup pattern (`deps.py:226-234`) — unchanged by this phase, not a new surface |

## Sources

### Primary (HIGH confidence)

- `apps/memory-api/app/deps.py:46-396` — `get_current_principal`, `get_team_scope` (read directly)
- `apps/memory-api/app/routes/auth_github.py:1-531` — full file, `_mint_xbt_for_user`, `_resolve_or_merge_user`, `signin_github` (read directly)
- `apps/memory-api/app/routes/me.py:1-280` — `create_api_token`, `_require_user` (read directly)
- `apps/memory-api/app/repos/users.py`, `apps/memory-api/app/repos/teams.py` — full files (read directly)
- `apps/memory-api/app/config.py` — full file, `Settings` + `field_validator` pattern (read directly)
- `apps/memory-api/pyproject.toml` — dependency list (read directly)
- `apps/memory-api/alembic/versions/0001_initial.py`, `0009_crm_contacts.py`, `0013_api_tokens.py`, `0016_phase10_github_primary.py`, `0020`-`0023` — migration conventions and revision chain (read directly, chain verified via `grep` on `revision`/`down_revision`)
- `apps/memory-api/app/models/user.py` — `User` ORM model (read directly, confirms `email` has no `unique=True`)
- `apps/memory-api/app/main.py:98-191`, `tests/test_edition_gating.py` — router classification mechanism (read directly)
- `apps/memory-api/tests/conftest.py`, `tests/test_phase10_auth.py` — testcontainers + respx integration test pattern (read directly)
- `apps/memory-api/Dockerfile:1-31` — confirms `--workers 2`, no build-essential in either stage, `python:3.12-slim` base (read directly)
- `app-site/account/teams/index.html`, `teams.js` — vanilla-JS/inline-CSS UI pattern (read directly)
- PyPI JSON registry (`pypi.org/pypi/argon2-cffi/json`, `/argon2-cffi-bindings/json`, `/limits/json`) — fetched live 2026-07-14, confirms versions and wheel availability for both target architectures

### Secondary (MEDIUM confidence)

- [Password Storage - OWASP Cheat Sheet Series](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html) — Argon2id parameter recommendations, fetched via WebFetch
- [Authentication - OWASP Cheat Sheet Series](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html) — user enumeration / timing guidance, via WebSearch summary (not directly WebFetched — flagged accordingly)
- [argon2-cffi howto](https://argon2-cffi.readthedocs.io/en/stable/howto.html) and [api reference](https://argon2-cffi.readthedocs.io/en/stable/api.html) — fetched via WebFetch, confirms `PasswordHasher` defaults, `RFC_9106_LOW_MEMORY`/`HIGH_MEMORY` profile values, exception types
- [limits on PyPI](https://pypi.org/project/limits/) — package description, via WebSearch + PyPI JSON registry cross-check

### Tertiary (LOW confidence)

- The exact `MovingWindowRateLimiter` usage example in Q3's Code Examples was assembled from a WebSearch summary (not independently WebFetched against `limits`' own official docs site, `limits.readthedocs.io`) — the general shape (parse a rate string, construct `MemoryStorage`, call `.hit()`) is consistent across multiple independent sources found, but the exact keyword arguments should be spot-checked against `limits.readthedocs.io` by the executor before finalizing the plan's code.
- `slowapi` maintenance-velocity claim in "Alternatives Considered" — asserted from general knowledge, not independently verified this session.

## Metadata

**Confidence breakdown:**
- Standard stack (argon2-cffi, limits versions/wheels): HIGH — verified live against PyPI registry JSON, both packages confirmed to have wheels for the required architectures
- Architecture / call sequence (register/login flow, xbt_ mint reuse, transaction boundaries): HIGH — every claim traced to a specific file:line in the live repo
- Migration conventions: HIGH — read 6 migration files directly, confirmed the linear revision chain via grep, corrected the stale "0022" reference from CONTEXT.md
- Pitfalls (email collision, multi-worker rate-limit gap, unique-index risk): HIGH for the mechanism (verified against live code — `Dockerfile:30`, `deps.py:314`, `0001_initial.py:30`), MEDIUM for "duplicates likely exist" (A1, unverified against live data)
- Cryptography parameter recommendations (OWASP Argon2id numbers, RFC 9106 profiles): MEDIUM — WebFetch against official docs, cross-referenced, but not independently verified against the RFC text itself
- Rate-limiting library API details: MEDIUM/LOW — package identity and wheel-availability HIGH (PyPI-verified), exact API usage example LOW (WebSearch-only, flagged for executor spot-check)

**Research date:** 2026-07-14
**Valid until:** ~30 days for the architectural/code-path claims (stable unless the repo's auth machinery changes); ~90 days for the crypto library recommendations (argon2id/OWASP guidance moves slowly); re-verify PyPI wheel availability if planning is delayed past a few weeks (package versions increment frequently but wheel-architecture coverage is unlikely to regress).
