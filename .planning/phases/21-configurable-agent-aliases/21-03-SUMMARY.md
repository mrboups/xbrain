---
phase: 21-configurable-agent-aliases
plan: 03
subsystem: testing
tags: [pytest, testcontainers, postgres, alembic, integration, fastapi, asyncio]

# Dependency graph
requires:
  - phase: 21-01
    provides: "team-aware mention_detector (effective_aliases + detect), migration 0025 (teams.agent_aliases), team_chat.py:246-247 per-team resolution"
  - phase: 21-02
    provides: "repos.teams.set_agent_aliases, GET/PATCH agent-aliases endpoints, seeded_two_teams + principal-override test scaffolding"
provides:
  - "Real-Postgres per-team summon gate: custom alias summons its own team only, @agent every team, @claude no team — through the REAL POST -> detect -> enqueue path (no mock detector)"
  - "Migration 0025 forward-only + edition-agnostic proof: nullable teams.agent_aliases column present under EDITION=oss AND saas"
affects: [phase-21 ship gate, release verification, future agent-alias changes]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Summon-path integration gate: stub only the downstream fire-and-forget network callers (handle_claude_mention as a recorder, centrifugo publish, brain ingest); NEVER mock the mention decision"
    - "Fresh-container-per-edition migration proof reusing test_migration_editions.py's config-singleton patch (os.environ is inert — settings frozen at import)"

key-files:
  created:
    - apps/memory-api/tests/test_agent_aliases_gate.py
  modified: []

key-decisions:
  - "Deterministic summon signal = whether handle_claude_mention is scheduled, captured by a recorder appending team_id; asserted after await asyncio.sleep(0) flushes the fire-and-forget create_task"
  - "Asserted HTTP 201 (the route's declared status_code), not the plan's loose '200'"
  - "Migration proof asserts information_schema.columns is_nullable='YES' (additive) per edition; upgrade-only, no downgrade()"

patterns-established:
  - "Real detection, stubbed network: the mention DECISION runs against real Postgres; only Anthropic/Centrifugo/brain callers are inert"
  - "SKIP=FAIL discipline via @pytest.mark.integration so CI skip-grep captures the file"

requirements-completed: [ALIAS-01]

# Metrics
duration: ~18min
completed: 2026-07-19
---

# Phase 21 Plan 03: Real-Postgres Per-Team Summon Gate Summary

**A real POST to team_chat traverses the REAL effective_aliases + detect resolution against a testcontainer Postgres, proving a custom alias summons only its own team, @agent summons every team, @claude summons none — plus migration 0025 is forward-only and edition-agnostic under oss AND saas.**

## Performance

- **Duration:** ~18 min
- **Completed:** 2026-07-19
- **Tasks:** 2
- **Files modified:** 1 (created)

## Gate Result

**Both groups ran GREEN against real Postgres — Docker was AVAILABLE in the run environment (server 29.6.1, `docker.ping()` == True). No skips.**

Real command output:

- Summon gate — `python -m pytest tests/test_agent_aliases_gate.py -q -k summon`
  → `1 passed, 2 deselected in 19.76s`
- Migration proof — `python -m pytest tests/test_agent_aliases_gate.py -q -k migration`
  → `2 passed, 1 deselected in 14.06s` (parametrized `[oss]` and `[saas]`)
- Full file — `python -m pytest tests/test_agent_aliases_gate.py -q`
  → `3 passed in 32.74s`
- Full phase-21 suite — `python -m pytest tests/test_mention_detector.py tests/test_agent_aliases_api.py tests/test_agent_aliases_gate.py -q`
  → `44 passed in 31.66s`

## Accomplishments

- **Summon-path gate (SC#1, SC#2):** a real `POST /v1/teams/{id}/messages` runs the real `mention_detector.effective_aliases(team.agent_aliases)` + `detect` at team_chat.py:246-247, with only the three fire-and-forget network callers stubbed:
  - `team_chat_agent.handle_claude_mention` → an async RECORDER capturing `team_id` (never runs the real handler / no Anthropic call).
  - `centrifugo_client.publish` and `brain_ingest.ingest_team_message` → inert async no-ops.
  Assertions proven: (A) team-a's custom `@wizard` summons team-a; (B) `@wizard` on team-b (which never set it) summons nothing; (C) `@agent` summons BOTH teams (including team-b with no custom alias); (D) `@claude` summons NEITHER team.
- **Migration 0025 proof (D-21-03):** fresh Postgres 17 container per edition, config singleton patched directly to `oss` then `saas`, `alembic upgrade head`, then `information_schema.columns` asserts exactly one nullable (`is_nullable='YES'`) `teams.agent_aliases` text column under both editions. Upgrade-only (no `downgrade()`); container stopped + singleton/env restored in `finally`.

## Task Commits

Each task was committed atomically:

1. **Task 1: Real-path per-team summon gate (POST → detect → enqueue)** - `d7d5749` (test)
2. **Task 2: Migration 0025 forward-only + edition-agnostic column proof** - `602bf7b` (test)

## Files Created/Modified

- `apps/memory-api/tests/test_agent_aliases_gate.py` - Two integration groups: the real-Postgres per-team summon gate (`test_summon_per_team_gate`) and the forward-only/edition-agnostic migration proof (`test_migration_0025_agent_aliases_forward_only[oss|saas]`).

## Decisions Made

- **Recorder-based summon signal.** Because the agent handler, Centrifugo publish, and brain ingest are fire-and-forget `asyncio.create_task` calls, the deterministic signal is whether `handle_claude_mention` gets scheduled. A recorder replaces it (patched by module attribute path, exactly how team_chat.py references it), and each case flushes the loop with `await asyncio.sleep(0)` (x5, belt-and-braces) after the POST before asserting on a freshly-cleared recorder list.
- **Assert 201, not 200.** The route declares `status_code=201`; the plan's prose said "200" loosely. Assertions use 201 to match the real contract (not a deviation — the plan text was approximate).
- **Self-contained migration helper.** Mirrored (not imported) the `test_migration_editions.py` pattern — fresh container per edition + direct singleton patch (`os.environ` alone is inert because `settings` is frozen at import and `alembic/env.py` reads that singleton) — so this gate stands alone and the two editions are a genuine per-edition run.

## Deviations from Plan

None - plan executed exactly as written. (The plan's loose "200" was matched to the route's real `201` status; this is a contract match, not a code change or scope change.)

## Issues Encountered

- Initial `Write` targeted the shared-checkout path and was rejected; re-issued against the worktree copy. No impact on the result.

## Next Phase Readiness

- Phase 21's definitive integration gate is green on real Postgres: per-team summoning (SC#1/SC#2) and forward-only edition-agnostic migration (D-21-03) are undeniable at the integration boundary.
- No production code changed (test-only). No `STATE.md` / `ROADMAP.md` edits (owned by the orchestrator).
- The full phase-21 suite (44 tests across mention_detector, agent-aliases API, and this gate) passes together — Phase 21 is ready to ship pending the orchestrator's roll-up.

## Self-Check: PASSED

- FOUND: `apps/memory-api/tests/test_agent_aliases_gate.py`
- FOUND: `.planning/phases/21-configurable-agent-aliases/21-03-SUMMARY.md`
- FOUND commit `d7d5749` (Task 1), FOUND commit `602bf7b` (Task 2)

---
*Phase: 21-configurable-agent-aliases*
*Completed: 2026-07-19*
