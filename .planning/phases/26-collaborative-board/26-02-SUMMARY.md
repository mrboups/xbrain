---
phase: 26-collaborative-board
plan: 02
subsystem: api
tags: [fastapi, jwt, authlib, sqlalchemy, alembic, postgres, yjs, hocuspocus, board, team-scope]

# Dependency graph
requires:
  - phase: 25-collaborative-board (plan 25-03/04 lineage — team membership + bridge auth)
    provides: _resolve_team_and_check_membership + kind=bridge principal + BRIDGE_SHARED_SECRET
provides:
  - "migration 0028 (boards + board_docs) — additive, forward-only, no EDITION branch"
  - "Board / BoardDoc ORM models mirroring the DDL column-for-column"
  - "mint_board_token / verify_board_token — the media-token shape with board_id, HS256 over BRIDGE_SHARED_SECRET"
  - "boards repo: idempotent default-board get-or-create + verbatim Y.Doc upsert/fetch"
  - "boards router in CORE_ROUTERS: membership-gated create/list, membership-rechecked mint (no-oracle 404), bridge-only doc GET/PUT with a size cap"
  - "four board config knobs (BOARD_PUBLIC_BASE_URL / BOARD_WS_URL_PUBLIC / BOARD_TOKEN_TTL_S / BOARD_MAX_DOC_BYTES)"
affects: [26-03 hocuspocus onAuthenticate, 26-04 board-web SPA, 26-06 compose/nginx, 26-07 non-mocked gate]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Board token = media-token shape with board_id in place of item_id; same authlib HS256 signing, same BRIDGE_SHARED_SECRET, same no-router helper module so unit tests import without FastAPI route registration"
    - "Sibling blob table (board_docs.state bytea) so list queries never drag the blob through TOAST"
    - "No-oracle 404: 'no such board' and 'not your team' collapse to one identical answer (Phase-25 discipline)"
    - "Bridge-only internal endpoints via a single shared _require_bridge_principal dependency used by both GET and PUT"
    - "Token in the URL fragment (#t=), never the query string, so it cannot reach nginx/Referer/proxy logs"

key-files:
  created:
    - apps/memory-api/alembic/versions/0028_boards.py
    - apps/memory-api/app/models/board.py
    - apps/memory-api/app/repos/boards.py
    - apps/memory-api/app/routes/board_helpers.py
    - apps/memory-api/app/routes/boards.py
    - apps/memory-api/tests/test_board_token.py
  modified:
    - apps/memory-api/app/config.py
    - apps/memory-api/app/main.py

key-decisions:
  - "Token claim set frozen as a cross-plan contract (26-03 decodes it byte-for-byte)"
  - "Mint endpoint returns a generic 404 'board not found' for both missing-board and not-a-member — no existence oracle"
  - "The two internal doc endpoints share one _require_bridge_principal dependency rather than duplicating the kind!=bridge check"

patterns-established:
  - "Board token mint/verify mirrors media_helpers with board_id; lazy authlib import; no hand-rolled crypto"
  - "get_or_create_default_board is idempotent and savepoint-guarded against the concurrent-create race"

requirements-completed: [BOARD-01]

# Metrics
duration: 15min
completed: 2026-07-24
---

# Phase 26 Plan 02: memory-api Board Schema, Token, and Team-Scope Gates Summary

**Migration 0028 (boards + board_docs), a media-token-shaped board JWT (HS256 over BRIDGE_SHARED_SECRET carrying board_id), and a CORE-mounted boards router whose create/list and mint are membership-gated with a no-oracle 404 and whose bridge-only doc GET/PUT moves the Y.Doc as verbatim octet-stream under a 16 MB cap.**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-07-24T05:47:26Z (base commit)
- **Completed:** 2026-07-24T06:02:36Z (Task 3 commit)
- **Tasks:** 3
- **Files modified:** 8 (6 created, 2 modified)

## Accomplishments

- **Migration 0028** chains off `0027_team_invite_codes`, creates `boards` (soft-delete + a PARTIAL unique index `ux_boards_team_default` that allows many boards per team but at most one live default) and `board_docs` (`state bytea`, one compacted Y.Doc update per board). Additive, forward-only, no EDITION branch.
- **`Board` / `BoardDoc` ORM models** mirror the DDL column-for-column; `BoardDoc.state` is `LargeBinary` stored/returned verbatim.
- **`mint_board_token` / `verify_board_token`** implement the media-token shape with `board_id`, signed with the existing `BRIDGE_SHARED_SECRET` via authlib (no new secret, no hand-rolled HMAC). Pinned by 16 unit assertions.
- **Boards router** (in `CORE_ROUTERS`): idempotent create + list (gate 1), membership-rechecked mint with a generic 404 (gate 2), and two bridge-only internal doc endpoints for the Hocuspocus database extension.

## Task Commits

Each task was committed atomically:

1. **Task 1: Migration 0028 + ORM models + four config knobs** — `162cf69` (feat)
2. **Task 2: Board token mint/verify + boards repo + unit test** — `80291cf` (feat)
3. **Task 3: Boards router (create/list, mint, internal doc GET/PUT) + CORE_ROUTERS registration** — `42ef4a6` (feat)

_The SUMMARY + deferred-items metadata commit is the final commit of this plan._

## Cross-Plan Contract — the board token (26-03 MUST match byte-for-byte)

**Algorithm / secret:** HS256 over `settings.BRIDGE_SHARED_SECRET` (the SAME secret `mint_media_token` uses — do NOT invent a new one).

**Claim set — exact keys, in this order:**

```json
{
  "scope": "board",
  "board_id": "<uuid str>",       // MUST equal the Hocuspocus documentName
  "team_scope": "<team slug>",    // the ONLY source of team scope on the socket
  "team_id": "<uuid str>",
  "sub": "<source_user_id>",
  "display_name": "<str>",        // "" when absent
  "read_only": false,
  "iat": <unix>,
  "exp": <unix + BOARD_TOKEN_TTL_S>   // BOARD_TOKEN_TTL_S default = 3600
}
```

The unit test asserts the claim key set is EXACTLY `{scope, board_id, team_scope, team_id, sub, display_name, read_only, iat, exp}` — no stray keys — so 26-03's Node `onAuthenticate` verifier can be written against precisely this shape.

**`verify_board_token(token, board_id) -> dict`** mirrors `verify_media_token`: authlib decode + `claims.validate()` (exp/iat/nbf) inside a try/except → `HTTPException(403, "invalid or expired board token: …")`, then:
- `scope != "board"` → `HTTPException(403, "board token has wrong scope")`
- `claims["board_id"] != board_id` → `HTTPException(403, "board token board_id mismatch")`
- `not claims["team_scope"]` → `HTTPException(403, "board token missing team_scope")`

The third gate (`claims.board_id === documentName`) is 26-03's; this plan proves the equivalent at the Python level (a token minted for board A raises 403 when verified against board B).

## Internal doc endpoint shape (26-03's Hocuspocus database extension calls these)

| Method | Path | Auth | Success | Notable failures |
|--------|------|------|---------|------------------|
| GET | `/v1/internal/boards/{board_id}/doc` | bridge-only | `200` — raw `application/octet-stream`, the bytea VERBATIM | `403` non-bridge; `404` "board document not found" (extension treats as new board → null) |
| PUT | `/v1/internal/boards/{board_id}/doc` | bridge-only | `204` (empty body) | `403` non-bridge; `413` "board document too large" (> `BOARD_MAX_DOC_BYTES`, logged `board_doc_rejected_oversize`); `400` "empty board document"; `404` "board not found" (never auto-creates a board) |

The two bridge checks are a **single shared dependency** — `_require_bridge_principal(principal)` — called by both endpoints, so the literal `principal.get("kind") != "bridge"` appears once in the module (inside the helper) rather than twice. This is the "equivalent single shared dependency used by both" allowance in the acceptance criteria.

## User-facing endpoint shape

| Method | Path | Gate | Success | Failure |
|--------|------|------|---------|---------|
| POST | `/v1/teams/{team_id}/boards` | gate 1 (membership) | `201` `{id, team_id, title, is_default, created_at, open_url, expires_at}` — idempotent | `404` team not found / `403` not a member (via the shared helper) |
| GET | `/v1/teams/{team_id}/boards` | gate 1 (membership) | `200` `{boards: [...]}` — no doc bytes, no tokens | same |
| POST | `/v1/boards/{board_id}/token` | gate 2 (membership re-check) | `200` `{token, board_id, ws_url, expires_at}` | **`404` `"board not found"` for BOTH missing-board AND not-your-team** |

**No-oracle 404 (T-26-07):** the mint endpoint's "board does not exist" branch and its "not your team" branch return the IDENTICAL status (`404`) AND the IDENTICAL detail string, sourced from a single module constant `_BOARD_NOT_FOUND = "board not found"`. The membership helper's own `404`/`403` is caught and collapsed into that one answer, so an outsider can never confirm a board id exists and belongs to someone else.

**Handoff URL:** `POST /teams/{id}/boards` returns `open_url = f"{BOARD_PUBLIC_BASE_URL}/?b={board.id}#t={token}"` — the token rides in the URL **fragment** (`#t=`), never the query string, so it cannot reach nginx access logs or a Referer header (T-26-11). `expires_at` is decoded from the freshly-minted token's own `exp` claim so the client-cached value can never drift from what the socket enforces.

## config.py field_validator count

- **Before this plan:** 8 lines matching `field_validator` (3 `@field_validator` decorators + 5 comment lines that say "NO field_validator").
- **After this plan:** 8 — unchanged. The four board knobs were added with **no validator** (26-CONTEXT: safe defaults, no validator).

`list_boards`' SELECT references `Board` ONLY — it never joins `board_docs` (the whole reason the blob is a sibling table). The 6 `BoardDoc` references in `repos/boards.py` are all inside the two doc functions (`get_doc`, `upsert_doc`).

## Files Created/Modified

- `apps/memory-api/alembic/versions/0028_boards.py` — created; boards + board_docs DDL, additive/forward-only, no EDITION branch.
- `apps/memory-api/app/models/board.py` — created; `Board` + `BoardDoc` ORM.
- `apps/memory-api/app/repos/boards.py` — created; default-board get-or-create (idempotent, savepoint-guarded race), list, get, verbatim doc upsert/fetch.
- `apps/memory-api/app/routes/board_helpers.py` — created; `mint_board_token` / `verify_board_token` (no router symbol).
- `apps/memory-api/app/routes/boards.py` — created; the router (5 routes).
- `apps/memory-api/tests/test_board_token.py` — created; 16 unit assertions pinning the wire contract + all rejections at 403.
- `apps/memory-api/app/config.py` — modified; four board knobs, no validator.
- `apps/memory-api/app/main.py` — modified; `boards` import + `(boards.router, "/v1", ["boards"])` in `CORE_ROUTERS`.

## Decisions Made

- **Idempotent default-board create is savepoint-guarded (Rule 2 hardening).** The plan specified `get_or_create_default_board` as "get, else create" but did not address the concurrent-create race: two members opening the board at once both miss the SELECT and both INSERT, and the partial unique index `ux_boards_team_default` would then raise an `IntegrityError` that poisons the whole session (a 500). I wrapped the INSERT in `session.begin_nested()` (SAVEPOINT) and, on conflict, re-read the winner's row. This keeps the endpoint's idempotency promise under concurrency. See Deviations.
- **`display_name`/`read_only` claim defaults** are `""` and `false`, matching the interface contract; `expires_at` is decoded from the token rather than recomputed so it can never drift from the enforced `exp`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Savepoint-guarded the idempotent default-board create against a concurrent-create race**
- **Found during:** Task 2 (boards repo — `get_or_create_default_board`)
- **Issue:** The plan's "get, else create" is a read-then-write with a TOCTOU window. The migration's partial unique index (`ux_boards_team_default`) is correct and necessary, but it means two simultaneous first-opens of a team's board both pass the SELECT, both attempt the INSERT, and the loser hits an `IntegrityError` that — uncaught — aborts the caller's transaction and surfaces as a 500 instead of the intended idempotent 201.
- **Fix:** Wrapped the INSERT in `async with session.begin_nested()` (a SAVEPOINT) and caught `IntegrityError` to re-read and return the winner's row. Only the failed INSERT statement rolls back; the caller's transaction survives.
- **Files modified:** `apps/memory-api/app/repos/boards.py`
- **Verification:** The unit gate (`test_board_token.py`) is green; the real concurrent-create proof against Postgres is 26-07's job (the non-mocked gate). Documented so 26-07 knows to exercise it.
- **Committed in:** `80291cf` (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 missing-critical hardening)
**Impact on plan:** Necessary for the idempotency the plan explicitly requires ("calling it twice returns the same board") to hold under concurrency. No scope creep — same public behaviour, no new endpoint or schema.

## Issues Encountered

- **The Task-1 and Task-3 `python -c` verify snippets fail without env vars** because `app.config.settings` is instantiated at import and `DATABASE_URL` / `BRIDGE_SHARED_SECRET` / the two `OAUTH_*` fields are required. Under pytest the `conftest.py` env defaults cover this; run outside pytest, the snippets need those four env vars set. I ran the checks with the conftest-equivalent env exported. Not a code problem — the verify commands assume the pytest-style environment.

## Deferred / Out-of-Scope

- **`tests/test_github_sync.py::test_sync_repo_multi_chunk_ids` fails** — a pre-existing uuid5-determinism mismatch in the GitHub-sync path (`app/services/github_sync.py`), entirely unrelated to this plan (neither file is in the 26-02 diff). Logged to `.planning/phases/26-collaborative-board/deferred-items.md`; NOT fixed here. All other non-integration tests pass (324 passed, 1 pre-existing failure).

## Known Stubs

None. Every endpoint is wired to the repo and the real token minter; there are no hardcoded empty responses or placeholder data. The Hocuspocus server (26-03) and the SPA (26-04) are the consumers of this contract and are out of scope for this plan.

## Threat Flags

None. All new surface (the board token, the membership gates, the bridge-only internal endpoints, the size cap, the fragment-only handoff) is already enumerated in the plan's `<threat_model>` (T-26-05 … T-26-12) and mitigated as specified.

## Verification (real output)

- `pytest tests/test_board_token.py -q` → **16 passed** (the wire contract + wrong-board / wrong-scope / wrong-secret / expired / garbage all 403).
- `pytest tests/test_edition_gating.py -q` → **13 passed** (boards.router classified in CORE_ROUTERS; OSS/SaaS route diff intact).
- `pytest tests/test_migration_editions.py -q` → **4 passed** (0028 does not branch on EDITION).
- Combined plan gate `pytest tests/test_board_token.py tests/test_edition_gating.py tests/test_migration_editions.py -q` → **33 passed, no skips**.
- Task-1 structural check (`ast.parse` + chain + no-EDITION + four knobs) → **ok**.
- Task-3 router check (boards.router in CORE_ROUTERS, not SAAS, all five route paths present) → **ok** (`['/teams/{team_id}/boards', '/teams/{team_id}/boards', '/boards/{board_id}/token', '/internal/boards/{board_id}/doc', '/internal/boards/{board_id}/doc']`).
- Comment-filtered greps: no hand-rolled crypto in `board_helpers.py`, no base64 in the doc path, no `?t=` query-string token, no `EDITION` branch in the migration or the router — **all confirmed**.
- **Deferred to 26-07 (the non-mocked gate):** migration 0028 applying cleanly under EDITION=oss AND saas against a real Postgres; the concurrent-create race; the real bridge round-trip persistence.

## Next Phase Readiness

- **26-03 (Hocuspocus / Yjs server):** the token claim set and the two internal doc endpoint shapes above are frozen. `onAuthenticate` verifies the same HS256/`BRIDGE_SHARED_SECRET` signature and asserts `claims.board_id === documentName`. The database extension calls `GET`/`PUT /v1/internal/boards/{board_id}/doc` with the bridge principal and `application/octet-stream`.
- **26-04 (board-web SPA):** consumes `open_url` (token in `#t=`), strips the fragment with `history.replaceState`, and re-mints via `POST /v1/boards/{board_id}/token`.
- **Blocker/concern:** the pre-existing `test_github_sync` failure is unrelated and must not block this plan; a reviewer should confirm it is red on the base commit too.

## Self-Check: PASSED

- All 6 created code/test files + the SUMMARY + deferred-items.md exist on disk (verified).
- Task commits `162cf69`, `80291cf`, `42ef4a6` present in `git log` (verified).

---
*Phase: 26-collaborative-board*
*Completed: 2026-07-24*
