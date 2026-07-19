---
phase: 25-team-join-by-code
plan: 02
subsystem: api
tags: [fastapi, invite-codes, bearer-secret, rate-limit, idempotency, team-scope, sha256]

# Dependency graph
requires:
  - phase: 25-team-join-by-code (Plan 01)
    provides: team_invite_codes table + repo (mint_code/get_by_hash/redeem_atomic/revoke_code/list_codes) + JOIN_CODE_* config knobs
  - phase: 18-local-auth
    provides: app/services/rate_limit.enforce_rate_limit (per-IP in-process limiter)
  - phase: oauth-connector
    provides: app/auth/oauth_tokens.hash_token (sha256-at-rest helper, reused for the join lookup)
provides:
  - POST /v1/teams/{team_id}/invite-codes (team-admin mint, plaintext returned ONCE, hash never leaked)
  - GET /v1/teams/{team_id}/invite-codes (team-admin list, InviteCodeOut carries no code_hash/plaintext)
  - DELETE /v1/teams/{team_id}/invite-codes/{code_id} (team-admin soft-revoke, team-scoped)
  - POST /v1/teams/join-by-code (any-auth, rate-limited, idempotent, generic-404, same-user-race-guarded, team-bound)
  - pydantic models InviteCodeMintBody/InviteCodeMintOut/InviteCodeOut/JoinByCodeBody/JoinByCodeOut
affects: [25-03-postgres-gate, 25-04-extension-ui]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Bearer-secret HTTP boundary: mint reveals plaintext once in the response body; every other endpoint serialises metadata + code_prefix only"
    - "No-oracle rejection: unknown/revoked/expired/exhausted all raise the SAME generic 404 so the endpoint never leaks which codes exist or why one failed"
    - "Idempotency-before-consume: get_membership short-circuits to a 200 no-op WITHOUT calling redeem_atomic (uses untouched)"
    - "Same-user double-submit race guard: add_member wrapped in try/except IntegrityError -> rollback + already_member=True (never a 500)"

key-files:
  created: []
  modified:
    - apps/memory-api/app/routes/teams.py

key-decisions:
  - "Reused app.auth.oauth_tokens.hash_token for the join lookup (DRY) so the submitted-code hash matches exactly how Plan 01's repo stores it"
  - "join-by-code registered in the static-path section (above /{team_id}) to honour the file's own 'static routes MUST come before /{team_id}' divider"
  - "mint expiry/max_uses resolution: explicit body override -> config default -> None; JOIN_CODE_DEFAULT_MAX_USES of 0 maps to None (unlimited)"

patterns-established:
  - "Admin CRUD triad mirrors org-blocks: get_team_by_id (404) -> _require_team_admin (403) -> repo -> write_audit -> commit"
  - "Audit payloads on mint/join/revoke carry code_prefix + role/metadata only — never the plaintext or the sha256 hash"

requirements-completed: [JOINCODE-01]

# Metrics
duration: ~12min
completed: 2026-07-19
---

# Phase 25 Plan 02: Invite-Code Endpoints Summary

**The four invite-code HTTP endpoints on routes/teams.py — team-admin mint (plaintext revealed once), team-admin list (no hash by construction) and soft-revoke, plus a public join-by-code that is rate-limited, idempotent, same-user-race-guarded, and returns a single generic 404 on every invalid/revoked/expired/exhausted code.**

## Performance

- **Duration:** ~12 min
- **Started:** 2026-07-19T16:29:00Z (approx)
- **Completed:** 2026-07-19T16:41:08Z
- **Tasks:** 2
- **Files modified:** 1 (routes/teams.py)

## Accomplishments
- Three admin invite-code endpoints beside the org-blocks CRUD triad, each gated by `_require_team_admin` after `get_team_by_id`, each audited without leaking the secret.
- Mint returns the plaintext exactly once via `InviteCodeMintOut.code`; the DB (Plan 01) stores only the sha256 hash — the one-time reveal (D-25-01).
- `InviteCodeOut` has NO `code_hash` and NO `code` field, so the list can never serialise a usable secret (D-25-03 / T-25-07) — enforced by construction.
- `POST /teams/join-by-code` in the static-path section: any authenticated caller, per-IP rate-limited before any DB work, idempotent already-member no-op, atomic redeem-then-add-member to the code's bound team only, uniform generic 404 on every failure, and a same-user double-submit race guard.

## The 4 routes + models (build Plans 03/04 against these exactly)

### `POST /v1/teams/{team_id}/invite-codes` -> 201 `InviteCodeMintOut`
- Guard: `_require_team_admin` (403 non-admin) after `get_team_by_id` (404 unknown team).
- Body `InviteCodeMintBody`: `role: str = "member"` (pattern `^(admin|member)$`), `expires_in_days: int|None` (ge=1, le=365), `max_uses: int|None` (ge=1, le=100000). `extra="forbid"`.
- Expiry resolution: `expires_in_days` override -> `JOIN_CODE_DEFAULT_EXPIRY_DAYS` (if >0) -> None. max_uses: body override -> `JOIN_CODE_DEFAULT_MAX_USES or None` (0 -> unlimited).
- Returns `InviteCodeMintOut`: `id, code (plaintext — ONCE), code_prefix, role, expires_at: str|None, max_uses: int|None`.
- Audit `invite_code.mint`, payload `{code_prefix, role, expires_at, max_uses}` — no plaintext/hash.

### `GET /v1/teams/{team_id}/invite-codes` -> `list[InviteCodeOut]`
- Guard: `_require_team_admin`.
- `InviteCodeOut`: `id, code_prefix, role, uses, max_uses: int|None, expires_at: str|None, revoked_at: str|None, created_at`. NO `code_hash`, NO `code`.

### `DELETE /v1/teams/{team_id}/invite-codes/{code_id}` -> 204
- Guard: `_require_team_admin`. `code_id: UUID` path param.
- Calls `revoke_code(team_id=team.id, code_id=...)` (team-scoped soft-revoke); 404 `"invite code not found"` if `None`.
- Audit `invite_code.revoke`, payload `{}`. Returns `Response(status_code=204)`.

### `POST /v1/teams/join-by-code` -> `JoinByCodeOut` (static-path section)
- Signature: `body: JoinByCodeBody, request: Request, principal, session`.
- Body `JoinByCodeBody`: `code: str` (min_length=1, max_length=128), `extra="forbid"`.
- Flow (D-25-02 order): `_require_user` -> `enforce_rate_limit(request, settings.JOIN_CODE_RATE_LIMIT, "join-by-code")` -> `hash_token(code)` -> `get_by_hash` (None -> generic 404) -> `get_team_by_id(row.team_id)` (None -> same generic 404) -> `get_membership` (exists -> commit + `already_member=True`, uses untouched) -> `redeem_atomic` (None -> same generic 404) -> `add_member(team_id=row.team_id, role=redeemed.role)` in `try/except IntegrityError` (-> rollback + `already_member=True`) -> audit `teams.join_by_code` (payload `{code_prefix, role}`) -> commit.
- `JoinByCodeOut`: `team_id, slug, display_name, already_member: bool`.
- Generic 404 message everywhere: `"invalid or expired invite code"` (3 occurrences — no oracle).

## Task Commits

Each task was committed atomically:

1. **Task 1: Admin endpoints — mint (plaintext once) + list (no hash) + revoke (soft)** — `77e8960` (feat)
2. **Task 2: join-by-code — any-auth, rate-limited, idempotent, generic-404, team-bound** — `0200cb6` (feat)

## Files Created/Modified
- `apps/memory-api/app/routes/teams.py` — added the 4 invite-code endpoints + 5 pydantic models; new imports (`datetime`/`timedelta`/`timezone`, `Request`, `IntegrityError`, `hash_token`, `enforce_rate_limit`, `team_invite_codes as invite_codes_repo`).

## Decisions Made
- Reused `hash_token` (sha256 hex) for the join lookup rather than inlining `hashlib` — guarantees the submitted-code hash matches how Plan 01's repo persisted it, and matches the file's established bearer-secret discipline.
- Placed `join-by-code` in the static-path section per the file's own "static routes MUST come before /{team_id}" divider (it would resolve either way — there is no bare `/teams/{team_id}` route — but clarity + convention win).
- On the same-user race the loser's `session.rollback()` unwinds its own transaction (its redeem increment + failed insert); the winner commits once, so the caller ends up a member exactly once — the invariant that matters. A negligible over-count under a same-user double-click is accepted (documented inline, per the plan).

## Deviations from Plan
None - plan executed exactly as written.

## Known Stubs
None - all four endpoints are fully wired to the Plan 01 repo; no placeholder data or empty returns.

## Threat Flags
None - no new security surface beyond the plan's `<threat_model>` (T-25-06..T-25-11 all mapped to the code as written).

## Issues Encountered
- A bare top-level `import` of `app.routes.teams` fails on `Settings()` requiring `.env` vars (DATABASE_URL, OAUTH_ISSUER_URL, etc.) — pre-existing behavior of `app.config` (noted in the Plan 01 summary), NOT a defect in this plan. Syntax is proven clean via `ast.parse` + `python -m py_compile`; the full route+repo behavior is exercised against real Postgres in Plan 03.

## Verification (real output)
- `ast.parse` + `python -m py_compile app/routes/teams.py` -> clean.
- Task 1 greps: `invite-codes` present, `InviteCodeMintOut` present, `/teams/{team_id}/invite-codes` count 3, `invite-codes/{code_id}` present, `_require_team_admin` used by all three admin routes, `InviteCodeOut` has no `code_hash`/bare `code:`, mint returns `code=plaintext`.
- Task 2 greps: `@router.post("/teams/join-by-code"` present with `request: Request`, `JOIN_CODE_RATE_LIMIT` referenced, `already_member=True` present, generic-404 message count = 3, `except IntegrityError` present, `redeem_atomic` precedes `add_member` (uses `row.team_id`).

## Next Phase Readiness
- Plan 03 (real-Postgres gate) can prove mint->join->revoke + every guard (403 non-admin, idempotent no-op, revoked/expired/exhausted 404, racing double-redeem <= max_uses, team-A-code-can't-add-to-team-B) against the exact routes/models above.
- Plan 04 (extension UI) can build the "create invite link" (copy the one-time `code`) + "paste a code to join" surfaces against the mint/list/join response shapes above.
- No blockers. No STATE.md / ROADMAP.md changes made (deferred to the orchestrator per this plan's scope).

## Self-Check: PASSED
25-02-SUMMARY.md present; both task commits (77e8960, 0200cb6) in the log; routes/teams.py present and parses.

---
*Phase: 25-team-join-by-code*
*Completed: 2026-07-19*
