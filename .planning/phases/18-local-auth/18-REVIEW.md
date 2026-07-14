---
phase: 18-local-auth
reviewed: 2026-07-14T08:01:04Z
depth: standard
files_reviewed: 20
files_reviewed_list:
  - apps/memory-api/app/routes/auth_local.py
  - apps/memory-api/app/services/password_hash.py
  - apps/memory-api/app/services/rate_limit.py
  - apps/memory-api/app/services/api_tokens.py
  - apps/memory-api/app/repos/local_credentials.py
  - apps/memory-api/alembic/versions/0024_local_credentials.py
  - apps/memory-api/app/config.py
  - apps/memory-api/app/main.py
  - app-site/account/register/index.html
  - app-site/account/login/index.html
  - app-site/account/password/index.html
  - apps/memory-api/tests/test_local_auth.py
  - apps/memory-api/tests/test_local_auth_set_password.py
  - apps/memory-api/tests/test_local_credentials_repo.py
  - apps/memory-api/tests/test_password_hash.py
  - apps/memory-api/tests/test_rate_limit.py
  - infrastructure/scripts/verify-phase18.sh
  - docs/local-auth-recovery.md
  - app-site/docs/auth.html
  - apps/memory-api/pyproject.toml
findings:
  critical: 2
  warning: 1
  info: 1
  total: 4
status: issues_found
---

# Phase 18: Code Review Report

**Reviewed:** 2026-07-14T08:01:04Z
**Depth:** standard
**Files Reviewed:** 20
**Status:** issues_found

## Summary

The core enumeration-oracle design holds up: `login()`'s absent-email, locked-account,
and wrong-password branches all return the byte-identical generic 401 (`auth_local.py:186,194,199`),
the absent/locked branches genuinely call `verify_decoy()` (real argon2 CPU cost, not a
short-circuit), `set_password()`'s identity comes exclusively from the resolved principal
with no user_id/email field on `SetPasswordBody`, `register()` is a single transaction/single
commit with the email-collision check hitting the real `users.email` column, the `xbt_` mint
helper (`api_tokens.py`) is a byte-for-byte match of `auth_github.py`'s `_mint_xbt_for_user`,
`password_hash.py` uses unmodified `PasswordHasher()` defaults (RFC_9106_LOW_MEMORY), and
migration 0024 is structurally correct (down_revision, CASCADE, downgrade, no illegitimate
email-uniqueness constraint). The static UI pages use `textContent` throughout — no XSS.

Two real gaps were found, one of them serious: **`set-password` has no rate limiting or
lockout integration on its `old_password` check**, which reopens exactly the brute-force
attack the DB-backed lockout was built to close, on a path that turns a stolen session into
a discovered/overwritten password. Separately, a **pre-existing bug in `merge.py` referencing
a nonexistent `memory_promotions` table** is confirmed reachable from a live production
code path (`auth_github.py`'s GitHub sign-in account-convergence branch) that Phase 18 makes
measurably easier to trigger, since local registration is now a common way to create the
"orphan GitHub row + email-matched user" precondition that path requires.

## Critical Issues

### CR-01: `set-password` has no rate limit and never engages the DB lockout — a stolen session can brute-force the real password

**File:** `apps/memory-api/app/routes/auth_local.py:233-238` (route registration, no rate-limit dependency) and `apps/memory-api/app/routes/auth_local.py:258-266` (old_password check, no `record_failure` call)

**Issue:** `register` and `login` are both wrapped in `dependencies=[Depends(_rl_register)]` / `dependencies=[Depends(_rl_login)]` (lines 117, 174). `set_password` has no such dependency:

```python
@router.post("/auth/local/set-password")
async def set_password(
    body: SetPasswordBody,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
```

Worse, the CHANGE branch's `old_password` check never touches the DB-backed lockout counters at all:

```python
else:
    if not body.old_password or not verify_password(
        row["password_hash"], body.old_password
    ):
        raise HTTPException(400, "Current password is incorrect.")
    await local_credentials_repo.update_hash(...)
```

A wrong guess just returns 400 — `local_credentials_repo.record_failure` (the function that
increments `failed_attempts` and eventually sets `locked_until`) is only ever called from
`login()`, never from `set_password()`.

**Concrete failure scenario:** An attacker who obtains a victim's live `xbt_` session token
(the token is stored in plaintext in `localStorage["xbt_token"]` per every account page —
readable by any XSS, a malicious browser extension, or a shared/unlocked device) does not
know the victim's actual password. Using only the stolen token, the attacker calls
`POST /v1/auth/local/set-password` in a loop, varying `old_password` against a dictionary
of candidates (the `LOCAL_AUTH_MIN_PASSWORD_LENGTH` floor is 10 chars with no complexity
requirement, so a targeted passphrase/common-password dictionary is realistic). There is no
per-IP rate limit and no per-account lockout on this endpoint, so the attacker can send an
unbounded number of guesses (bounded only by argon2's own ~100ms CPU cost per guess, which
does not stop a determined or distributed attacker). The moment a guess succeeds, the
request body's own `old_password` field IS the victim's real password — the attacker has
now learned a secret with value beyond the stolen session (commonly reused elsewhere) — and
in the same request the account's password has already been overwritten to the attacker's
`new_password`, locking the real owner out. This completely defeats the purpose of D-18-06's
lockout mechanism, which Phase 18 built specifically so account guessing can't be
brute-forced even across multiple uvicorn workers — that defense is simply never engaged
on this route.

**Fix:** Add the same rate-limit dependency used by register/login, and make a wrong
`old_password` guess increment/consult the same `local_credentials` lockout counters
`login()` uses:

```python
async def _rl_set_password(request: Request) -> None:
    await enforce_rate_limit(request, settings.LOCAL_AUTH_RATE_LIMIT, "auth_local_set_password")


@router.post("/auth/local/set-password", dependencies=[Depends(_rl_set_password)])
async def set_password(...):
    ...
    else:
        if row["locked_until"] is not None and row["locked_until"] > datetime.now(tz=timezone.utc):
            raise HTTPException(400, "Current password is incorrect.")
        if not body.old_password or not verify_password(row["password_hash"], body.old_password):
            await local_credentials_repo.record_failure(session, user.id)
            await session.commit()
            raise HTTPException(400, "Current password is incorrect.")
        await local_credentials_repo.reset_failures(session, user.id)
        await local_credentials_repo.update_hash(session, user.id, hash_password(body.new_password))
```

### CR-02: `merge.py`'s `memory_promotions` reference is a live, reachable bug — and Phase 18 makes it easier to hit

**File:** `apps/memory-api/app/repos/merge.py:82-85` (bug), called from `apps/memory-api/app/routes/auth_github.py:304` (live production call site)

**Issue:** `merge_user_rows()` re-parents FK rows across five tables including:

```python
"UPDATE memory_promotions SET proposed_by = :survivor WHERE proposed_by = :orphan",
"UPDATE memory_promotions SET approved_by_1 = :survivor WHERE approved_by_1 = :orphan",
"UPDATE memory_promotions SET approved_by_2 = :survivor WHERE approved_by_2 = :orphan",
```

but the actual table (`apps/memory-api/app/models/promotion.py:14`) is `__tablename__ = "promotions"` —
`memory_promotions` does not exist in the schema. This is documented in this repo's
`test_phase10_auth.py` failure list as pre-existing and out of Phase 18's scope to fix
(confirmed — `merge.py` is untouched by this diff). Per the review brief, flagging it here
because it is reachable in a live production code path, not just a stale test fixture.

**Concrete failure scenario:** `merge_user_rows` is invoked from `auth_github.py`'s GitHub
sign-in flow (`auth_github.py:270-308`) whenever a user signs in with GitHub using an email
that already belongs to an existing, non-GitHub-linked user row, AND an orphan GitHub-only
row already exists for that same GitHub login. Before Phase 18, the "existing email, no
github_id" precondition was reachable only via Google sign-in. Phase 18 adds a second,
much more common way to create exactly that precondition: any self-hoster who registers via
`POST /v1/auth/local/register` now has a `users` row with a real email and `github_id IS NULL`.
If that same person (or anyone who later controls that email/GitHub login pairing) then signs
in with GitHub, `merge_user_rows` fires and raises `UndefinedTable` (`relation "memory_promotions"
does not exist`) partway through the FK re-parenting loop, aborting the transaction and
turning what should be a successful account-convergence sign-in into a 500. This is exactly the
kind of "one account, two ways in" convergence flow Phase 18's own docs (`app-site/docs/auth.html`
`#converged`) advertise as safe and expected.

**Fix:** One-line table-name correction in `apps/memory-api/app/repos/merge.py:82-85`
(`memory_promotions` → `promotions`). This file is outside Phase 18's owned file set, so it
should not be silently patched inside this phase's diff — but given local-auth registration
measurably increases how often this path is hit, it should be filed and fixed as an immediate
fast-follow rather than left in `deferred-items.md` at the same priority as the two
test-fixture-only pre-existing failures.

## Warnings

### WR-01: Timing side-channel in `login()` — wrong-password responses do more DB work than absent/locked responses

**File:** `apps/memory-api/app/routes/auth_local.py:182-199`

**Issue:** All three losing branches of `login()` return the byte-identical `401` body
(confirmed by `test_no_enumeration_oracle`), but they are not equal-cost:

- absent email (line 182-186): `verify_decoy()` (CPU only) → raise. No DB write.
- locked account (line 188-194): `verify_decoy()` (CPU only) → raise. No DB write.
- wrong password, existing & unlocked account (line 196-199): `verify_password()` (real
  argon2 verify, CPU only — timing-matched) **followed by** `local_credentials_repo.record_failure()`
  (an `UPDATE ... RETURNING`) **and** `session.commit()` — two extra network/DB round-trips
  that the other two branches never perform.

**Concrete failure scenario:** A network-adjacent attacker (same datacenter, same Docker
network, or simply someone doing careful statistical timing over many samples) who wants to
know whether a given email has a local-auth account sends repeated login attempts with a
random password. Requests against an absent or already-locked email consistently skip the
UPDATE+COMMIT round-trip; requests against an existing, unlocked account consistently pay for
it. Response bodies stay identical (so `test_no_enumeration_oracle`, which only asserts
status+JSON equality, does not catch this), but the response-time distribution differs in a
statistically distinguishable way, reopening the account-enumeration question the byte-identical
body was specifically designed to close.

**Fix:** Make the three branches cost-symmetric — either perform an equivalent no-op DB
round-trip (e.g. a cheap `SELECT 1`) on the absent/locked branches, or move `record_failure`'s
write off the request's critical path (e.g. `await` it but don't gate the response on
`session.commit()` completing before the 401 is returned — accepting eventual consistency
of the failure counter in exchange for removing the timing signal from the response path).

## Info

### IN-01: `register()`'s email format check is a bare substring test

**File:** `apps/memory-api/app/routes/auth_local.py:122-124`

**Issue:** The only server-side email shape validation is:

```python
email = body.email.strip().lower()
if "@" not in email:
    raise HTTPException(422, "Invalid email address.")
```

This accepts clearly-invalid addresses (`"a@"`, `"@b"`, `"a@b@c"`, `"@@@"`) as long as one
`@` character is present anywhere in the string. Not a security issue (the value is only
ever used as an opaque lookup/storage key, never interpreted as a mailto target or rendered
unescaped), but it's a weaker guarantee than the `type="email"` + required-field validation
already present client-side, and other parts of the codebase (invite-by-email flows) assume
a plausible email shape.

**Fix:** Use Pydantic's `EmailStr` (already a transitive dependency via `pydantic[email]` if
installed) on `RegisterBody.email`, or a minimal regex (`^[^@\s]+@[^@\s]+\.[^@\s]+$`) instead
of the bare `"@" not in email` check.

---

_Reviewed: 2026-07-14T08:01:04Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
