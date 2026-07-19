---
phase: 25-team-join-by-code
plan: 01
subsystem: database
tags: [postgres, sqlalchemy, alembic, bearer-secret, sha256, invite-codes, team-scope]

# Dependency graph
requires:
  - phase: 23-catch-me-up
    provides: migration head 0026_team_member_last_read (0027 chains off it)
  - phase: oauth-connector
    provides: app/auth/oauth_tokens.hash_token (sha256-at-rest bearer-secret helper, reused DRY)
provides:
  - team_invite_codes TABLE (code_hash/code_prefix/role/expires_at/max_uses/uses/revoked_at) + UNIQUE index on code_hash
  - TeamInviteCode ORM model (CASCADE team FK, SET NULL creator FK, role CHECK)
  - migration 0027_team_invite_codes (forward-only, additive, no EDITION branch)
  - repos/team_invite_codes.py — generate_code/mint_code/get_by_hash/redeem_atomic/revoke_code/list_codes
  - JOIN_CODE_RATE_LIMIT + JOIN_CODE_DEFAULT_EXPIRY_DAYS + JOIN_CODE_DEFAULT_MAX_USES config knobs
affects: [25-02-endpoints, 25-03-postgres-gate, 25-04-extension-ui]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Bearer-secret at rest: mint returns plaintext once, DB stores only sha256(plaintext)"
    - "Race-safe consume via a single conditional UPDATE ... WHERE uses < max_uses RETURNING (row-lock serialized)"
    - "Forward-only edition-agnostic migration (raw SQL, IF NOT EXISTS, gen_random_uuid)"

key-files:
  created:
    - apps/memory-api/alembic/versions/0027_team_invite_codes.py
    - apps/memory-api/app/repos/team_invite_codes.py
    - apps/memory-api/tests/test_invite_code_repo_unit.py
  modified:
    - apps/memory-api/app/models/team.py
    - apps/memory-api/app/config.py

key-decisions:
  - "Reused app.auth.oauth_tokens.hash_token for code hashing (DRY) instead of inlining hashlib"
  - "code_prefix = plaintext[:12] (xbi_ + 8 chars) — non-secret display label"
  - "redeem_atomic returns a sa.Row (id, team_id, role, uses) or None — no ORM identity-map staleness on the atomic path"

patterns-established:
  - "Hash-at-rest mint: generate_code() is PURE (unit-testable); mint_code() persists hash+prefix only"
  - "Atomic redeem re-checks revoked_at/expires_at/max_uses INSIDE the same UPDATE (no TOCTOU window)"

requirements-completed: [JOINCODE-01]

# Metrics
duration: ~20min
completed: 2026-07-19
---

# Phase 25 Plan 01: Team Invite-Code Schema + Repo Summary

**team_invite_codes table + migration 0027 + TeamInviteCode ORM + a repo that mints a hashed-at-rest `xbi_` bearer secret and redeems it with a single race-safe conditional UPDATE — the security primitives Plans 02/03/04 build against.**

## Performance

- **Duration:** ~20 min
- **Started:** 2026-07-19T16:12:00Z (approx)
- **Completed:** 2026-07-19T16:32:34Z
- **Tasks:** 2
- **Files modified:** 5 (2 modified, 3 created)

## Accomplishments
- `TeamInviteCode` ORM model with the exact D-25-02 columns, CASCADE FK to teams, SET NULL FK to the creator, and a `role IN ('admin','member')` CHECK.
- Migration `0027_team_invite_codes` chained off `0026_team_member_last_read`: additive `CREATE TABLE IF NOT EXISTS` + UNIQUE index on `code_hash` + index on `team_id`, forward-only, **no EDITION branch** (passes `test_no_migration_branches_on_edition`).
- `repos/team_invite_codes.py`: hash-at-rest mint (plaintext returned once, only the sha256 hash + non-secret prefix persisted) and a single race-safe `redeem_atomic` conditional UPDATE that cannot exceed `max_uses` under concurrency.
- Three `JOIN_CODE_` config knobs with safe defaults and no `field_validator` (zero-key OSS boot).
- Pure unit test (5 assertions groups) proving the hash-at-rest contract — real run output: **5 passed**.

## Contracts for Plans 02/03 (build against these exactly)

### Config knobs (app/config.py)
```python
JOIN_CODE_RATE_LIMIT: str = "10/minute"        # per-IP, in-process (rate_limit.py --workers 2 caveat)
JOIN_CODE_DEFAULT_EXPIRY_DAYS: int = 7         # 0 -> no expiry (expires_at NULL) at mint
JOIN_CODE_DEFAULT_MAX_USES: int = 0            # 0 -> unlimited (max_uses NULL) at mint
```

### Repo signatures (app/repos/team_invite_codes.py)
```python
def generate_code() -> tuple[str, str, str]:
    # returns (plaintext, code_hash, code_prefix); PURE, no DB/I/O
    # plaintext = "xbi_" + secrets.token_urlsafe(24); code_hash = sha256 hex; code_prefix = plaintext[:12]

async def mint_code(session, *, team_id: UUID, created_by_user_id: UUID | None,
                    role: str, expires_at: datetime | None, max_uses: int | None
                    ) -> tuple[TeamInviteCode, str]:
    # persists ONLY hash+prefix; returns (row, plaintext) — plaintext ONCE, never logged. Flushes.

async def get_by_hash(session, *, code_hash: str) -> TeamInviteCode | None:
    # lookup by hash (no plaintext timing oracle)

async def redeem_atomic(session, *, code_id: UUID, now: datetime) -> sa.Row | None:
    # single conditional UPDATE; returns Row(id, team_id, role, uses) or None. Does NOT add_member, does NOT commit.

async def revoke_code(session, *, team_id: UUID, code_id: UUID) -> TeamInviteCode | None:
    # team-scoped soft-revoke (revoked_at = Python now(utc)); idempotent; None if not in this team. Flushes.

async def list_codes(session, *, team_id: UUID) -> list[TeamInviteCode]:
    # newest-first; endpoint must serialize metadata only — NEVER expose code_hash
```

### The double-spend guard SQL (redeem_atomic — D-25-02)
```sql
UPDATE team_invite_codes
   SET uses = uses + 1
 WHERE id = :code_id
   AND revoked_at IS NULL
   AND (expires_at IS NULL OR expires_at > :now)
   AND (max_uses IS NULL OR uses < max_uses)
 RETURNING id, team_id, role, uses
```
Plan 02's join route: resolve `get_by_hash(sha256(submitted))` → `redeem_atomic(code_id, now)`; on a **non-None** Row call `repos.teams.add_member(team_id=row.team_id, user_id=caller, role=row.role)` then commit; on **None** return a generic 404/410. Idempotency (already-a-member → 200 no-op, `uses` unchanged) is enforced BEFORE `redeem_atomic` via `get_membership`. Plan 03 proves the racing double-redeem cannot exceed `max_uses`.

## Task Commits

1. **Task 1: team_invite_codes schema — model + migration 0027 + config knobs** — `f223048` (feat)
2. **Task 2: invite-code repo — hash-at-rest mint + atomic redeem + unit test** — `f006a95` (feat)

## Files Created/Modified
- `apps/memory-api/app/models/team.py` — added `TeamInviteCode` (imported `Integer`).
- `apps/memory-api/alembic/versions/0027_team_invite_codes.py` — additive table + unique code_hash index.
- `apps/memory-api/app/config.py` — Phase 25 JOIN_CODE_ knob block.
- `apps/memory-api/app/repos/team_invite_codes.py` — the six repo functions.
- `apps/memory-api/tests/test_invite_code_repo_unit.py` — pure hash-at-rest unit test.

## Decisions Made
- Reused `hash_token` from `app.auth.oauth_tokens` (DRY) rather than inlining `hashlib` — the same helper backs the OAuth bearer tokens, so the invite-code discipline matches the established pattern exactly.
- `redeem_atomic` returns a raw `sa.Row` (not an ORM instance) — the atomic path is a bare UPDATE ... RETURNING, so there is no ORM identity-map row to go stale; the route reads `row.team_id`/`row.role` directly.
- Defaults chosen (D-25 discretion): 7-day expiry, unlimited uses, 10/minute join rate — sane bounded defaults, all request/env-overridable, `0 -> NULL` mapping documented for the mint route.

## Deviations from Plan
None - plan executed exactly as written.

## Issues Encountered
- A bare `python -c "from app.repos.team_invite_codes import ..."` outside pytest fails on `Settings()` requiring `.env` vars (DATABASE_URL, OAUTH_ISSUER_URL, etc.). This is pre-existing behavior of `app.config` (import of `app.auth` loads settings at module time), not a defect in this plan's code — the module imports cleanly under the pytest harness (conftest sets env), proven by all 5 unit tests passing at the top-level import. No change needed.

## Verification (real output)
- `pytest tests/test_invite_code_repo_unit.py -q` → **5 passed**.
- `pytest tests/test_migration_editions.py` → **9 passed** (0027 reaches the same head under every edition; no EDITION branch).
- `ast.parse` clean on model, migration, config, repo.
- `grep` acceptance: 7 model columns present, tablename OK, `CREATE UNIQUE INDEX` on `code_hash` present, `EDITION` count 0, 3 JOIN_CODE_ non-comment fields, all six repo symbols present, redeem UPDATE carries `revoked_at IS NULL` + expiry + `uses < max_uses`.

## Next Phase Readiness
- Plan 02 (endpoints) can build directly against the repo signatures + redeem SQL above — no exploration needed.
- Plan 03 (real-Postgres gate) has the migration + repo to prove mint→join→revoke + the racing double-spend against a live Postgres.
- No blockers. No STATE.md / ROADMAP.md changes made (deferred to the orchestrator per this plan's scope).

## Self-Check: PASSED
All 6 files present; both task commits (f223048, f006a95) in the log.

---
*Phase: 25-team-join-by-code*
*Completed: 2026-07-19*
