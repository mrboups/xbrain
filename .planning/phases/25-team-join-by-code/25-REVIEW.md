---
phase: 25-team-join-by-code
reviewed: 2026-07-24T00:00:00Z
depth: deep
files_reviewed: 11
files_reviewed_list:
  - apps/memory-api/app/models/team.py
  - apps/memory-api/alembic/versions/0027_team_invite_codes.py
  - apps/memory-api/app/repos/team_invite_codes.py
  - apps/memory-api/app/routes/teams.py
  - apps/memory-api/app/config.py
  - apps/memory-api/tests/test_invite_code_repo_unit.py
  - apps/memory-api/tests/test_join_by_code_gate.py
  - chrome-extension/popup.js
  - chrome-extension/popup.html
  - chrome-extension/popup.css
  - chrome-extension/tests/test_popup_contract.mjs
findings:
  critical: 1
  warning: 2
  info: 3
  total: 6
status: issues_found
---

# Phase 25: Code Review Report — Team Join-by-Code

**Reviewed:** 2026-07-24
**Depth:** deep (cross-file, plus an empirical repro of the async-session race path)
**Files Reviewed:** 11
**Status:** issues_found

## Summary

The hash-at-rest discipline (D-25-01) is genuinely solid: `generate_code()` is pure,
`mint_code()` persists only `sha256(plaintext)` + a non-secret prefix, `InviteCodeOut`
has no field for the hash so it cannot leak by construction, and `write_audit` payloads
carry only `code_prefix`/role/expiry metadata — never the plaintext or the hash. The
`redeem_atomic()` conditional `UPDATE ... WHERE revoked_at IS NULL AND (expires_at IS
NULL OR expires_at > :now) AND (max_uses IS NULL OR uses < max_uses) RETURNING ...` is a
correct single-statement, row-locked guard — I verified with the real distinct-user race
test (`test_double_spend_race_cannot_exceed_max_uses`) that two different callers racing
a `max_uses=1` code cannot both win. Mint/list/revoke are correctly gated by
`_require_team_admin` and `revoke_code`/`list_codes` are correctly scoped by `team_id`,
so one team cannot list or revoke another team's codes. The client renders the one-time
code via `textContent` only, never `innerHTML`, and CSS uses shadcn Neutral tokens
throughout (no raw hex, no `50%` radius).

However, I found and **empirically reproduced** a genuine BLOCKER: the same-user
double-submit race guard (the `except IntegrityError: await session.rollback()` branch
in `join_by_code`) crashes with `sqlalchemy.exc.MissingGreenlet` instead of returning the
intended idempotent `200`, because it reads ORM attributes off a `Team` object that
`rollback()` unconditionally expires — this is the opposite of what the code's own
comment claims it does. Two further correctness gaps and three lower-severity issues are
below.

## Critical Issues

### CR-01: Same-user double-submit race crashes with `MissingGreenlet` instead of returning the documented idempotent 200

**File:** `apps/memory-api/app/routes/teams.py:449-460`

**Issue:** When two concurrent `POST /v1/teams/join-by-code` requests from the *same*
user race on the *same* code (double-click, a client retry, two open tabs — anything
that fires two requests before the first commits), both can pass the earlier
`get_membership` check, and both `redeem_atomic()` calls can succeed (this happens
whenever `max_uses` is `None` or `> 1`; see CR-01's sibling WR-01 below for the
`max_uses=1` case). The second `add_member()` then hits the `team_members (team_id,
user_id)` PK/unique violation, which is caught:

```python
try:
    await teams_repo.add_member(
        session, team_id=row.team_id, user_id=user.id, role=redeemed.role
    )
except IntegrityError:  # the concurrent-insert loser
    await session.rollback()
    return JoinByCodeOut(
        team_id=str(team.id),
        slug=team.slug,
        display_name=team.display_name,
        already_member=True,
    )
```

`team` is a `Team` ORM instance loaded earlier in this same session via
`teams_repo.get_team_by_id`. `Session.rollback()` in SQLAlchemy **unconditionally**
expires every object's attributes in the identity map — this is independent of
`expire_on_commit=False` (that flag only governs behavior after a successful `commit()`,
not `rollback()`; see `sqlalchemy.orm.session.SessionTransaction._restore_snapshot`,
called with `dirty_only=False` for a non-nested rollback). Accessing an expired ORM
attribute triggers an implicit refresh query, and under `AsyncSession` a *synchronous*
attribute access outside of an already-awaited greenlet context raises
`sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called ...`.

I reproduced this exact failure mode against a minimal `async_sessionmaker(...,
expire_on_commit=False)` + `aiosqlite` engine: loading a row, calling
`await session.rollback()`, then reading `row.id` / `row.slug` raises `MissingGreenlet`
every time. `team.id`, `team.slug`, and `team.display_name` are all read in this except
block, so the very first line of the "graceful idempotent" return raises — FastAPI's
error middleware turns this into an unhandled `500`, not the documented `200
already_member=True`.

This directly breaks D-25-04 ("Already-a-member → 200 no-op") for the one case the code
was explicitly written to handle gracefully — a same-user concurrent redeem — turning it
into a crash instead of a no-op.

**Fix:** Capture the plain string values from `team` into local variables *before* the
`try/except` (this function already re-reads `team.id`/`team.slug`/`team.display_name`
in three separate branches, so this also removes duplication), or re-fetch `team` after
the rollback:

```python
team = await teams_repo.get_team_by_id(session, row.team_id)
if team is None:
    raise HTTPException(404, "invalid or expired invite code")
team_id_str, team_slug, team_display_name = str(team.id), team.slug, team.display_name
...
try:
    await teams_repo.add_member(
        session, team_id=row.team_id, user_id=user.id, role=redeemed.role
    )
except IntegrityError:
    await session.rollback()
    return JoinByCodeOut(
        team_id=team_id_str,
        slug=team_slug,
        display_name=team_display_name,
        already_member=True,
    )
```

(and use the same locals in the other two `JoinByCodeOut(...)` return sites for
consistency).

## Warnings

### WR-01: Same-user double-submit on a `max_uses=1` code returns a misleading "invalid or expired" 404 instead of idempotent success

**File:** `apps/memory-api/app/routes/teams.py:406-438`

**Issue:** For a code minted with `max_uses=1`, two concurrent same-user requests don't
even reach CR-01's `IntegrityError` path. `redeem_atomic()`'s row lock serializes the two
`UPDATE`s; the winner's request proceeds normally (`uses` 0→1, member added, `200`). The
loser's `UPDATE` re-evaluates `uses < max_uses` as `1 < 1` → **false** → matches zero rows
→ `redeem_atomic` returns `None` → the route takes the generic-404 branch:

```python
redeemed = await invite_codes_repo.redeem_atomic(...)
if redeemed is None:
    # revoked / expired / exhausted -> SAME generic 404, no oracle (D-25-02).
    raise HTTPException(404, "invalid or expired invite code")
```

So a user who double-clicks "Join" on a single-use code gets told the code is "invalid,
expired, or used up" — even though *they themselves* are, at that exact moment, already a
legitimate member of the team via their own other in-flight request. No security or data
issue (the membership invariant "exactly one row" still holds), but it's an incorrect,
confusing response for a real client interaction pattern (the extension's `joinByCode()`
does disable the button while in flight, but a fast double-tap or a network retry can
still fire this).

**Fix:** Re-check membership before surfacing the generic 404 when `redeemed is None`:

```python
if redeemed is None:
    existing_after = await teams_repo.get_membership(
        session, user_id=user.id, team_slug=team.slug
    )
    if existing_after is not None:
        await session.commit()
        return JoinByCodeOut(
            team_id=str(team.id), slug=team.slug,
            display_name=team.display_name, already_member=True,
        )
    raise HTTPException(404, "invalid or expired invite code")
```

### WR-02: Pasted join-code left in the DOM input after the invite overlay is closed

**File:** `chrome-extension/popup.js:368-376`

**Issue:** `closeInvite()` clears the one-time *minted* code (`invite-code-output`) but
not the *pasted* join code the user typed/pasted into `#invite-join-code`:

```javascript
function closeInvite() {
  const panel = $("invite-panel");
  if (panel) panel.hidden = true;
  // Clear the one-time revealed code so it does not persist across opens (T-25-19).
  const out = $("invite-code-output");
  if (out) out.textContent = "";
  const row = $("invite-code-row");
  if (row) row.hidden = true;
}
```

`joinByCode()` does clear `input.value = ""` on a *successful* join, but if the user
pastes a code, then closes the overlay via ✕/Cancel without clicking Join (e.g. they
change their mind, or hit a transient error and give up), the bearer secret they just
typed remains sitting in the `<input>` element's value. The header comment for this
section explicitly states the intent ("The revealed code is cleared on close so the
one-time secret does not linger in the DOM across opens (T-25-19)") but the cleanup is
asymmetric — it only covers the *minted* reveal, not the *pasted* join field. This module
is shared between the transient popup and the persistent side-panel surface (per the
existing `focusComposer()` / "Side panel never grabs focus" comments elsewhere in this
file), so in the side-panel case the stale value can persist for the life of the panel,
not just a single popup open.

**Fix:** Clear the join input in `closeInvite()` (and defensively in `openInvite()` too):

```javascript
function closeInvite() {
  const panel = $("invite-panel");
  if (panel) panel.hidden = true;
  const out = $("invite-code-output");
  if (out) out.textContent = "";
  const row = $("invite-code-row");
  if (row) row.hidden = true;
  const joinInput = $("invite-join-code");
  if (joinInput) joinInput.value = "";
}
```

## Info

### IN-01: Coarse timing oracle between "code never existed" and "code exists but is dead"

**File:** `apps/memory-api/app/routes/teams.py:406-438`, `apps/memory-api/app/repos/team_invite_codes.py:97-127`

**Issue:** The 404 *message* is identical for garbage/revoked/expired/exhausted codes
(verified — `test_join_by_code_gate` asserts this), but the *work done* before returning
it is not: a garbage code short-circuits after one `SELECT` (`get_by_hash` misses), while
a revoked/expired/exhausted code additionally does a team lookup, a membership lookup,
and a row-locked conditional `UPDATE` before returning the same 404. This is a real, if
coarse, timing side-channel that partially undermines the stated no-oracle goal ("no
timing oracle on the plaintext" — D-25-01's stated rationale for hash-based lookup).
Given real-world network jitter this is a weak oracle, not a practical break, but it's
worth naming since the design explicitly calls out timing as a threat vector.

**Fix:** Not necessarily worth the complexity of constant-time padding for a `10/minute`
rate-limited endpoint; if it matters, consider doing a cheap `now()`/revoked check in
Python against the already-fetched `row` before touching `redeem_atomic`, so the "extra"
work is the same order of magnitude regardless of code state. Low priority — flagging for
visibility, not requesting a blocking fix.

### IN-02: `join_by_code` does not trim the submitted code before hashing

**File:** `apps/memory-api/app/routes/teams.py:411`

**Issue:** `code_hash = hash_token(body.code)` hashes the raw request value with no
`.strip()`. The Chrome extension already trims client-side (`input.value.trim()` in
`joinByCode()`), so this is invisible in practice through the shipped UI — but per
CLAUDE.md's multi-frontend invariant, `memory-api` is meant to be the authoritative
contract other frontends (LibreChat, Open WebUI, a future `/join/<code>` page, direct API
callers) build against. A caller that doesn't trim (e.g. a copy-paste that grabs a
trailing newline) gets the same generic "invalid or expired" 404 as a genuinely bad code.

**Fix:** `code_hash = hash_token(body.code.strip())` — cheap, and matches the leniency
the extension already assumes is safe to rely on.

### IN-03: No client affordance to mint a limited-use / short-lived code — every minted code defaults to unlimited uses for 7 days

**File:** `chrome-extension/popup.js:380-417` (`mintInvite`), `apps/memory-api/app/config.py:286-288`

**Issue:** `mintInvite()` always `POST`s an empty body (`JSON.stringify({})`), so every
code minted through the shipped extension UI takes the server defaults:
`JOIN_CODE_DEFAULT_EXPIRY_DAYS=7` and `JOIN_CODE_DEFAULT_MAX_USES=0` (→ unlimited uses).
There is no UI control for `expires_in_days` or `max_uses`, even though the mint endpoint
already accepts and validates both. For a value explicitly documented as "a BEARER SECRET
to the team-scoped brain," the only code an admin can ever produce from the extension is
unlimited-use for a week — maximizing blast radius if it's pasted somewhere it shouldn't
be (a public channel, a screenshot, etc.) with no way to dial it back down from the
client that ships.

**Fix:** Not a defect in what shipped (the API supports tighter codes; this is a client
gap), but worth a follow-up: expose `max_uses` (e.g. a "single use" checkbox) in the mint
UI, or have the client default its own request to something tighter than the server
default (e.g. `{"max_uses": 1}` for a "quick invite" affordance) rather than relying on
server-side defaults tuned for API callers in general.

---

_Reviewed: 2026-07-24_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
