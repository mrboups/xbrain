---
phase: 21-configurable-agent-aliases
plan: 02
subsystem: api
tags: [fastapi, pydantic, mention-detection, agent-aliases, authz, audit, sqlalchemy, regex, testcontainers]

# Dependency graph
requires:
  - phase: 21-01
    provides: "effective_aliases(custom_csv) resolver, team-aware detect(content, aliases), Team.agent_aliases column"
  - phase: 09-team-chat
    provides: "team_chat.py POST summon site + _resolve_team_and_check_membership"
  - phase: 10-github-primary-auth
    provides: "_require_team_admin / _require_user / get_membership + write_audit team-admin mutation pattern"
provides:
  - "GET /v1/teams/{id}/agent-aliases — any team MEMBER reads the team's EFFECTIVE alias list (one source of truth for the client)"
  - "PATCH /v1/teams/{id}/agent-aliases — TEAM ADMIN sets custom alias(es); validated, audited, no-restart effect"
  - "teams_repo.set_agent_aliases() persistence helper (None clears custom back to env defaults)"
  - "_validate_aliases() edge validation: charset [A-Za-z0-9_-], 1-32 chars, <=8, strip leading '@', dedup, reject empty + reserved 'claude' -> 422"
  - "team_chat.py summon site resolves effective_aliases(team.agent_aliases) -> detect(), closing cross-team leakage"
  - "brain_ingest.py agent-command skip-prefixes derived from effective_aliases(None); all @claude references removed"
affects: [21-03-summon-gate, 21-04-extension-client, chrome-extension-settings-ui, team_chat]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Admin gate BEFORE payload validation on PATCH — a non-admin never learns whether their input was well-formed (403 precedes 422)"
    - "Edge validation (_validate_aliases 422) layered on top of the resolver's re.escape + reserved-token filter (defense in depth)"
    - "GET returns the SAME effective_aliases() the summon site uses — client and server derive from one resolver, no desync"
    - "Real-Postgres endpoint tests drive a specific seeded user via a get_current_principal dependency override"

key-files:
  created:
    - apps/memory-api/tests/test_agent_aliases_api.py
    - .planning/phases/21-configurable-agent-aliases/21-02-SUMMARY.md
    - .planning/phases/21-configurable-agent-aliases/deferred-items.md
  modified:
    - apps/memory-api/app/routes/team_chat.py
    - apps/memory-api/app/routes/teams.py
    - apps/memory-api/app/repos/teams.py
    - apps/memory-api/app/services/brain_ingest.py

key-decisions:
  - "Dedicated /agent-aliases sub-route (GET+PATCH) rather than folding into the team-settings PATCH — small, consistent with existing /teams/{id}/* routes, and the client reads exactly one endpoint"
  - "PATCH runs _require_team_admin BEFORE _validate_aliases so authZ failures (403) are not leaked as validation detail (422) to non-admins"
  - "brain_ingest skip-prefixes are DERIVED from effective_aliases(None) (env defaults) at import — the mention vocabulary lives in one place and can never drift back to @claude"
  - "PATCH returns the resulting EFFECTIVE list (not just the stored csv) so the caller/client can refresh its regex immediately (D-21-05 no-restart)"

patterns-established:
  - "Per-team config PATCH: load team (404) -> team-admin gate (403) -> validate (422) -> persist -> write_audit -> commit -> return effective view"
  - "Member-readable GET: _require_user -> load team (404) -> get_membership (403 if None) -> return resolver output"

requirements-completed: [ALIAS-01]

# Metrics
duration: 12min
completed: 2026-07-19
---

# Phase 21 Plan 02: Summon Wiring + Agent-Aliases Endpoints Summary

**The summon site now detects on each team's effective alias list, and two endpoints (member GET / admin PATCH) make the client and server share one validated, audited, no-restart source of truth — `@agent` always fires, `@claude` is rejected as reserved, and one team's custom name never summons on another.**

## Performance

- **Duration:** 12 min
- **Started:** 2026-07-19T01:14:54Z
- **Completed:** 2026-07-19T01:28:24Z
- **Tasks:** 2
- **Files modified:** 4 modified + 3 created (test + 2 planning docs)

## Accomplishments
- `team_chat.py` summon site is TEAM-SCOPED: resolves `effective_aliases(team.agent_aliases)` and passes it to `detect()`, so detection uses each team's own list (closes the cross-team-leakage class).
- `GET /v1/teams/{id}/agent-aliases` — any team MEMBER reads the team's EFFECTIVE list (the exact list the extension will build its MENTION_RE from). `403` for non-members.
- `PATCH /v1/teams/{id}/agent-aliases` — TEAM ADMIN only. Validates each alias (charset/length/count, strips leading `@`, dedups, rejects empty and reserved `claude` -> `422`), persists via `set_agent_aliases()`, writes a `team.agent_aliases.set` audit row, commits, and returns the resulting effective list. `403` for non-admin members and non-member admins.
- `brain_ingest.py` agent-command skip-prefixes now DERIVE from `effective_aliases(None)`; every `@claude`/`@c`/`@cl` literal removed (docstrings + comments included) so the acceptance grep is 0.
- Real-Postgres API tests (5 cases) prove member-GET, admin-PATCH-no-restart, non-admin/non-member 403, cross-team isolation, and 422 validation incl. reserved `claude`.

## Task Commits

Each task was committed atomically:

1. **Task 1: Wire team_chat + GET/PATCH endpoints + validation + repo + brain_ingest align** - `bf53151` (feat)
2. **Task 2: API tests — member GET, admin PATCH round-trip, 403, 422 (real Postgres)** - `c834b07` (test)

**Plan metadata:** committed separately with this SUMMARY.

## Files Created/Modified
- `apps/memory-api/app/routes/team_chat.py` - Summon site resolves + passes the team's effective aliases to `detect()`; `@claude` comments realigned to the agent vocabulary.
- `apps/memory-api/app/routes/teams.py` - `AgentAliasesBody`, `_validate_aliases()`, `GET`+`PATCH /teams/{id}/agent-aliases`; imports `re` + `mention_detector`.
- `apps/memory-api/app/repos/teams.py` - `set_agent_aliases(session, *, team_id, aliases_csv)` persistence helper.
- `apps/memory-api/app/services/brain_ingest.py` - `_AGENT_COMMAND_PREFIXES` derived from `effective_aliases(None)`; all `@claude` references removed.
- `apps/memory-api/tests/test_agent_aliases_api.py` - 5 real-Postgres endpoint tests.
- `.planning/phases/21-configurable-agent-aliases/deferred-items.md` - logs a pre-existing, out-of-scope test breakage (see Issues).

## Decisions Made
- Dedicated `/agent-aliases` GET+PATCH sub-route (not folded into a broader team-settings PATCH) — keeps the client's read path to a single endpoint and mirrors existing `/teams/{id}/*` routes.
- Admin gate precedes payload validation on PATCH so a non-admin gets `403` (not `422`) regardless of input shape.
- `brain_ingest` derives its skip-prefixes from the env-default resolver at import, keeping the mention vocabulary in one place.
- PATCH returns the effective list (not the raw stored csv) so the client refreshes its regex immediately (no-restart, D-21-05).

## Deviations from Plan

None - plan executed exactly as written. (Two `@claude` references living in `#`-comment lines of `brain_ingest.py` beyond the plan-cited docstrings/line 53 were also cleaned so the vocabulary is fully consistent — within the task's stated "remove every @claude literal" instruction, not a scope change.)

## Issues Encountered
- **Pre-existing (out-of-scope) test breakage discovered, not fixed.** Running the broader integration set surfaced 6 failures in `tests/test_team_context_cache.py` (5) and `tests/test_soft_delete_regression.py` (1), all `TypeError: 'Team' object is not subscriptable`. Root cause: those tests access `seeded_two_teams["team_a"]["slug"]` (dict style) but the `seeded_two_teams` fixture returns ORM `Team`/`User` objects — a mismatch that already exists at the plan base commit `313ce0f`. None of `conftest.py`, `test_team_context_cache.py`, or `test_soft_delete_regression.py` are touched by this plan (verified via `git diff --name-only 313ce0f..HEAD`). Logged to `deferred-items.md` per the SCOPE BOUNDARY rule; the fix is a trivial attribute-access swap left for a dedicated cleanup.

## Verification (real output)

- `python -m pytest tests/test_agent_aliases_api.py -q` -> **5 passed** (real Postgres testcontainer; NOT skipped — Docker available).
- `python -m pytest tests/test_agent_aliases_api.py tests/test_mention_detector.py -q` -> **41 passed** (5 API + 36 detector).
- `ast.parse` clean on `team_chat.py`, `teams.py`, `repos/teams.py`, `brain_ingest.py`, and the new test file.
- `grep -q "effective_aliases(team.agent_aliases)" app/routes/team_chat.py` -> match.
- `grep -q "def set_agent_aliases" app/repos/teams.py` -> match.
- `grep -vn '^\s*#' app/services/brain_ingest.py | grep -c "@claude"` -> **0** (no @claude in executable lines; also 0 in comments).
- Route registration: `GET /v1/teams/{team_id}/agent-aliases` + `PATCH /v1/teams/{team_id}/agent-aliases` present on the app.
- `_validate_aliases` (real calls): `['a*b']`/`['x'*33]`/`['']`/`['claude']`/`['CLAUDE']` -> 422; `['wizard']` -> `wizard`; `['@wizard']` -> `wizard`; `['Wizard','wizard','WIZARD']` -> `Wizard` (dedup); `[]` -> `None`.

## Known Stubs
None - the endpoints are fully wired to `teams.agent_aliases` (real DB read/write) and the resolver. No placeholder data or unwired sources introduced.

## User Setup Required
None for the code path. (Ops note carried from 21-01 still applies: set the VM `.env` `AGENT_MENTION_ALIASES=agent,chad,a` on next deploy to match the code default — a `.env` change only.)

## Next Phase Readiness
- **21-03 (summon gate):** the team-scoped summon path is live at `team_chat.py:243`; the end-to-end real-Postgres gate (custom alias summons for its team only, `@claude` never, `@agent` always) can now assert against the wired path.
- **21-04 (extension client):** the contract is fixed — `GET /v1/teams/{id}/agent-aliases` returns `{"aliases": [str, ...]}` (effective list, `@agent` included, `@claude` excluded); `PATCH` body is `{"aliases": [str, ...]}` (<=8 items, no leading `@` needed) and returns the same effective shape. The client builds its MENTION_RE from the GET list (JS-escape each alias, longest-first) and re-fetches after a successful PATCH.
- No blockers.

## Self-Check: PASSED

All created files present (`test_agent_aliases_api.py`, `21-02-SUMMARY.md`, `deferred-items.md`) and all modified files present on disk; both task commits (`bf53151`, `c834b07`) exist in git history.

---
*Phase: 21-configurable-agent-aliases*
*Completed: 2026-07-19*
