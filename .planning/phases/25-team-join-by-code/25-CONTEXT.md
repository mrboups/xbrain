# Phase 25: Team Join-by-Code — Context

**Gathered:** 2026-07-19 (autonomous — backlog feature #5, the last of the user's "then you can ship" list before the Excalidraw board).
**Source:** BACKLOG "Team join-by-code (Slack/Discord-style invite link)".

<domain>
## Phase Boundary

Let a team admin mint a shareable **invite code**; anyone who submits that code joins the team chat, no individual invite needed. The current model has NO code/token concept — only direct self-join of `open` teams (gate is knowing the team_id), admin-approved join-requests for `closed` teams, and invite-by-email of an already-existing xbrain user. This adds the Slack/Discord-style shared secret.

**IN scope:** a new `team_invite_codes` table (a TABLE, not a column — supports multiple live codes, per-code role, expiry, max-uses, revocation); `POST /v1/teams/{id}/invite-codes` (admin → mint, return the plaintext code ONCE); `POST /v1/teams/join-by-code {code}` (any authenticated user → resolve → `add_member(caller)`); `DELETE /v1/teams/{id}/invite-codes/{code_id}` (admin → revoke); `GET /v1/teams/{id}/invite-codes` (admin → list live codes WITHOUT the plaintext); the full bearer-secret security discipline (unguessable, hashed at rest, revocable, expiring, max-uses); a real-Postgres gate proving mint→join→revoke + every guard. A minimal admin UI action ("create invite link" + copy) in the extension Settings surface.

**OUT of scope:** email-delivery of codes (the admin copies + shares the link themselves); deep-link auto-redeem UX / a hosted `/join/<code>` landing page (a follow-up — the API is the deliverable; the extension gets a paste-code field); changing the existing open-team self-join / closed-team join-request / invite-by-email flows (all stay); the app-site (`app-site/account/teams`) UI (extension Settings is the in-scope surface; the web UI is a fast-follow once the API + extension land).
</domain>

<the_hard_facts_from_live_code>
1. **`add_member` is ready.** `apps/memory-api/app/repos/teams.py::add_member(session, *, team_id, user_id, role)` inserts a `team_members` row (role ∈ {'admin','member'}, CHECK-constrained). Join-by-code resolves the code → `add_member(caller_user_id, code.role)`. Idempotent-guard: check `get_membership` first (already a member → 200 no-op, don't double-insert).
2. **The admin guard exists.** `apps/memory-api/app/routes/teams.py::_require_team_admin(...)` — caller must have role='admin' in THIS team (global env-admin `is_admin` is an accepted ops backdoor). Mint / revoke / list use it. Join-by-code does NOT (any authenticated user may redeem a code they hold).
3. **The bearer-secret pattern is established (mirror it exactly).** `app/auth/oauth_tokens.py`: `secrets.token_urlsafe(32)` to generate, `hashlib.sha256(raw).hexdigest()` to store — the DB holds only the HASH; the plaintext is returned to the caller ONCE and never persisted. Reuse this for invite codes (a `xbi_` prefix + `secrets.token_urlsafe(24)` ≈ 192 bits — enumeration infeasible; a DB leak exposes no usable code).
4. **Rate-limiting is available.** `app/services/rate_limit.py::enforce_rate_limit(request, settings.X_RATE_LIMIT, "bucket")` (Phase 18, `limits`). The join-by-code endpoint is rate-limited (defense in depth even though the code is high-entropy).
5. **Migration head is `0026_team_member_last_read`.** The new migration is `0027`, `down_revision="0026_team_member_last_read"`, forward-only, edition-agnostic (NO EDITION branch — `team_invite_codes` exists in both oss AND saas).
6. **`team_api_keys` / `team_join_requests` are the model precedents** in `app/models/team.py` (CASCADE FK to teams.id, PgUUID pk, timezone-aware created_at). Follow their column conventions.
7. **team_scope is the team's `slug`.** A code binds to ONE `team_id`; redeeming it can only ever add the caller to THAT team — the code carries no cross-team reach.
</the_hard_facts>

<decisions>
## Implementation Decisions (locked)

### D-25-01 — Store the code HASHED, return the plaintext exactly once (bearer-secret at rest).
`team_invite_codes.code_hash` = `sha256(plaintext)` (UNIQUE, indexed — the join lookup is by hash, no timing oracle on the plaintext). The plaintext (`xbi_<token_urlsafe(24)>`) is generated at mint, returned in the mint response body ONE time, and NEVER persisted or logged. A short non-secret `code_prefix` (e.g. first 8 chars) MAY be stored for admin display ("code xbi_ab12…"). A DB compromise must not yield usable codes.

### D-25-02 — The table carries revoke + expiry + max-uses; every one is enforced at join.
`team_invite_codes(id PK, team_id FK CASCADE, code_hash UNIQUE, code_prefix, role CHECK IN ('admin','member') default 'member', created_by_user_id FK, expires_at TIMESTAMPTZ NULL, max_uses INT NULL, uses INT NOT NULL default 0, revoked_at TIMESTAMPTZ NULL, created_at TIMESTAMPTZ default now())`. Join validates, in order: code_hash resolves → NOT revoked (`revoked_at IS NULL`) → NOT expired (`expires_at IS NULL OR expires_at > now()`) → uses-left (`max_uses IS NULL OR uses < max_uses`). A failed check returns a generic 404/410 (don't leak WHICH check failed beyond expired-vs-invalid where useful). On success, `uses` is incremented atomically in the same transaction as the `add_member` (no double-spend race under concurrency — increment-and-check under a row lock or a conditional UPDATE … WHERE uses < max_uses).

### D-25-03 — Mint / revoke / list are team-admin-gated; join is any-authenticated-user.
`POST /invite-codes`, `DELETE /invite-codes/{id}`, `GET /invite-codes` → `_require_team_admin`. `POST /teams/join-by-code` → any authenticated principal (the code IS the authorization to join). Revoke sets `revoked_at` (soft — an audit trail; the row stays). List returns metadata + `code_prefix` + counts, NEVER `code_hash` and never a plaintext.

### D-25-04 — Join is idempotent + rate-limited; redeemer still needs an xbrain account.
Already-a-member → 200 no-op (don't increment uses, don't error). `enforce_rate_limit` on the join endpoint (brute-force defense in depth). The redeemer must be authenticated (Phase-18 email/password or GitHub) — a code is join-authorization, not identity. team_scope integrity: the caller is added to the code's team_id ONLY.

### D-25-05 — Forward-only, edition-agnostic migration.
`0027`, down_revision `0026_team_member_last_read`, additive (new table + indexes), NO EDITION branch, no down-data-loss. Validated under EDITION=oss AND saas (mirror the Phase-17 migration-both-editions discipline).

### Claude's Discretion
- Default expiry / max-uses (e.g. no expiry + unlimited uses by default, or a sane 7-day / unlimited) — pick sane defaults, make them request-overridable at mint.
- The exact 404-vs-410 (expired) response shaping — keep it from leaking which live code exists.
- Whether the atomic uses-increment is a `UPDATE … WHERE uses < max_uses RETURNING` or a row-locked read-modify-write — either, as long as it's race-safe.
- The extension Settings UI copy/layout for "create invite link" + "paste a code to join" (English-only, shadcn Neutral to match Phase 20).
</decisions>

<canonical_refs>
## Canonical References — read before planning/executing
- `apps/memory-api/app/models/team.py` (Team / TeamMember / TeamApiKey / TeamJoinRequest — the column conventions + CASCADE FK the new model mirrors).
- `apps/memory-api/app/repos/teams.py` (`add_member`, `get_membership`, `get_team_by_id` — the join path; add a new `repos/team_invite_codes.py` or extend teams.py).
- `apps/memory-api/app/routes/teams.py` (`_require_team_admin`, the existing invite/join endpoints to sit beside + mirror the response-model style).
- `apps/memory-api/app/auth/oauth_tokens.py` (the `secrets.token_urlsafe` + `sha256`-at-rest bearer-secret pattern to copy).
- `apps/memory-api/app/services/rate_limit.py` (`enforce_rate_limit`) + `app/config.py` (add a `JOIN_CODE_RATE_LIMIT` knob + any code defaults).
- `apps/memory-api/alembic/versions/0026_team_member_last_read.py` (the latest migration — chain `0027` off it; mirror its forward-only, no-EDITION shape).
- `apps/memory-api/tests/conftest.py` (real-Postgres `pg_url` testcontainer) + `tests/test_catch_me_up_gate.py` (a recent real-PG route+repo gate to mirror for the mint→join→revoke proof).
- `chrome-extension/popup.js` / `popup.html` / `popup.css` (the Settings surface + shadcn Neutral tokens; `test_popup_contract.mjs` — extend it for any new bound ids).
- CLAUDE.md — English-only (the extension still has legacy French; new strings English); dev arm64 / prod amd64.
</canonical_refs>

<specifics>
## The gate lesson applies — this guards the team brain
A join-code is a bearer secret to the team-scoped memory (the product's sensitive core). "It works" is not enough — the SECURITY guards must be PROVEN against a real Postgres (testcontainers), non-mocked:
- Mint returns a plaintext ONCE; the DB stores only the sha256 hash (assert the plaintext is NOT in the row; assert the hash matches).
- Join with a valid code adds the caller to THAT team (a `team_members` row) and increments `uses`.
- **Revoked** code → join REJECTED. **Expired** code → REJECTED. **max-uses reached** → REJECTED (and a concurrent double-redeem cannot exceed max_uses — prove the atomic increment under two racing joins).
- A code for team A can NEVER add the caller to team B (team_scope integrity — a decoy team proves isolation).
- Non-admin caller → mint/revoke/list 403. Already-a-member → 200 no-op, `uses` unchanged.
- A wrong/garbage code → generic 404, no oracle. Migration `0027` upgrades clean under EDITION=oss AND saas.
SKIP=FAIL (Docker is up on this host — the gate must actually run and pass). Do NOT mock the repo/DB on the security-bearing paths. Git Bash docker needs MSYS_NO_PATHCONV=1. English-only.
</specifics>

<deferred>
- Email-delivery of invite codes — the admin shares the code themselves.
- A hosted `/join/<code>` deep-link landing page / auto-redeem UX — follow-up (the extension gets a paste-code field).
- The app-site web UI (`app-site/account/teams`) invite-link management — fast-follow after the API + extension.
- Per-code analytics beyond a `uses` counter — out.
</deferred>

---
*Phase: 25-team-join-by-code*
*Context gathered: 2026-07-19 (autonomous)*
