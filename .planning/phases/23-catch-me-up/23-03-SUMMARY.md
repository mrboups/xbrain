---
phase: 23-catch-me-up
plan: 03
subsystem: testing
tags: [pytest, testcontainers, postgres, integration-gate, alembic-migration, team-chat, catch-me-up, monkeypatch]

# Dependency graph
requires:
  - phase: 23-01
    provides: "team_members.last_read_at cursor, teams.set_last_read, list_messages(after_created_at), count_unread_since(exclude_user_id), migration 0026"
  - phase: 23-02
    provides: "POST /mark-read, GET /unread-summary, POST /catch-me-up, team_chat_agent.catch_me_up() ephemeral summarizer"
provides:
  - "apps/memory-api/tests/test_catch_me_up_gate.py — the definitive real-Postgres catch-me-up gate + migration 0026 proof (GREEN under Docker)"
  - "Executable proof of SC#1 (cursor + count-excludes-own), SC#2 (exact since-window gather + 403 + team_scope isolation), and the ephemerality half of SC#3 (no persisted row, private-channel-only)"
  - "Executable proof migration 0026 is forward-only + edition-agnostic (oss AND saas)"
affects: [23-04, extension-catch-me-up-ui, release-migration-gate]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Cross-connection gate: the background task (catch_me_up) opens its OWN pooled connection and sees only COMMITTED data — seed+commit real rows against the testcontainer, tear down in finally; the rollback `session` fixture's savepoint commits are invisible cross-connection"
    - "Committing get_session override: re-override the client fixture's rollback session with a per-request committing session so the real routes run end-to-end AND each request releases its cursor lock immediately (rollback-session lock would deadlock committed cleanup)"
    - "Recorder-not-mock discipline: stub ONLY the terminal callers (_stream_via_anthropic_api recorder, _user_has_live_bridge->False, centrifugo publish recorder) — cursor/gather/count/isolation run for real"

key-files:
  created:
    - "apps/memory-api/tests/test_catch_me_up_gate.py — Group 1 real-Postgres gate + Group 2 migration 0026 forward-only proof (both @pytest.mark.integration, SKIP=FAIL)"
  modified: []

key-decisions:
  - "Seed+commit all fixture rows (teams/users/members/messages) to the real testcontainer with host-clock created_at, because catch_me_up's own async_session_factory() session only sees committed data — the rollback `session` fixture's commits are savepoint-only and invisible cross-connection (verified empirically before writing the gate)"
  - "Re-override get_session with a per-request committing session so mark-read commits+releases immediately; the default rollback session holds a cursor row-lock until fixture teardown, which deadlocks the committed cascade-delete cleanup"
  - "Repoint team_chat_agent.async_session_factory at the live db_session factory (plumbing, not a logic stub) so the background task's own session hits the SAME testcontainer regardless of suite import order"

patterns-established:
  - "Real-Postgres background-task gate: commit real rows + stub only terminal streaming/publish + await the terminal frame (catchup_stream_end) before asserting the gathered window and row-count invariance"
  - "Deterministic since-window timeline: Phase A pinned to a fixed past host-clock instant, cursor set by the REAL mark-read (now()), Phase B seeded after the cursor with host-clock created_at — all on one clock so `created_at > cursor` is strict"

requirements-completed: [CATCHUP-01]

# Metrics
duration: ~55min
completed: 2026-07-19
---

# Phase 23 Plan 03: Real-Postgres Catch-Me-Up Gate + Migration 0026 Proof Summary

**A single `@pytest.mark.integration` file that RUNS GREEN against a real Postgres testcontainer: it proves mark-read moves the caller's private cursor, the unread count == 2 (excludes own + agent), catch-me-up gathers EXACTLY the since-window (agent streaming stubbed to a recorder, the gather + cursor NOT mocked), a non-member gets 403 on all three endpoints, a different team's messages never appear (team_scope isolation), and NO team_messages row is persisted (streamed only to `user:<caller_sub>`) — plus migration 0026 is forward-only + edition-agnostic under oss AND saas, leaving a NULLABLE `team_members.last_read_at` TIMESTAMPTZ.**

## Performance

- **Duration:** ~55 min (includes empirical probes of the fixture's cross-connection semantics)
- **Started:** 2026-07-19T12:14Z (approx)
- **Completed:** 2026-07-19T13:08Z
- **Tasks:** 2 (delivered as one cohesive test-file artifact)
- **Files modified:** 1 created

## Accomplishments
- **Group 1 — `test_catch_me_up_gate`** runs green against a REAL Postgres testcontainer and asserts, with the agent streaming stubbed to a RECORDER (gather + cursor real):
  1. mark-read moved the cursor — `last_read_at` is non-NULL and sits strictly between the Phase-A and Phase-B timestamps (SC#1, T-23-04).
  2. unread-summary `count == 2` — only carol's two post-cursor USER messages; NOT alice's own, NOT the agent frame; `since == cursor.isoformat()`, `threshold == 10` (SC#1, D-23-02).
  3. Non-member (bob) → 403 on mark-read, unread-summary AND catch-me-up, and starts NO summary (recorder empty) (SC#2, T-23-01).
  4. catch-me-up gathers EXACTLY the since-window — `_stream_via_anthropic_api` invoked once; captured history CONTAINS `PHASE_B_1`/`PHASE_B_2`, EXCLUDES `TEAMB_1`/`TEAMB_2` (team_scope isolation, T-23-02) and `PHASE_A_1`/`PHASE_A_2` (strictly after the cursor), and drops the caller's own `PHASE_B_ALICE_OWN` (SC#2, D-23-04).
  5. EPHEMERAL — team_messages row count for team-a is UNCHANGED before/after (no persisted row), and every publish went to `user:<alice_sub>`, never `team:<team_a_id>` (SC#3, T-23-06).
  6. Empty-window no-op — a second mark-read advances the cursor past every message, then catch-me-up returns `200 nothing_to_summarize` with NO new LLM call (T-23-03).
- **Group 2 — `test_migration_0026_last_read_forward_only[oss|saas]`** spins a fresh Postgres container per edition, patches the config singleton directly, runs `alembic upgrade head` in a worker thread, and asserts exactly ONE `team_members.last_read_at` column with `is_nullable == "YES"` and `data_type == "timestamp with time zone"`. No reverse migration is ever invoked (T-23-07).

## Task Commits

Both plan tasks target the single deliverable artifact (`tests/test_catch_me_up_gate.py`) and were committed as one atomic `test` commit:

1. **Task 1 + Task 2: real-Postgres gate (Group 1) + migration 0026 proof (Group 2)** - `14be259` (test)

_Note: the plan's two tasks each list the same single file as their sole artifact; splitting one new file into two commits by content would produce artificial churn, so the cohesive gate file was committed atomically._

## Files Created/Modified
- `apps/memory-api/tests/test_catch_me_up_gate.py` (created, 540 lines) - Group 1 real-Postgres cursor/since-window/isolation/403/ephemerality gate + Group 2 migration 0026 forward-only edition-agnostic proof. `pytestmark = [pytest.mark.integration, pytest.mark.asyncio]`; SKIP=FAIL under Docker.

## Real test output (GREEN, not skipped)

```
tests/test_catch_me_up_gate.py::test_catch_me_up_gate PASSED
tests/test_catch_me_up_gate.py::test_migration_0026_last_read_forward_only[oss] PASSED
tests/test_catch_me_up_gate.py::test_migration_0026_last_read_forward_only[saas] PASSED
3 passed, 4 warnings in 24.04s
```

Docker was present, so this is a genuine green run against a real Postgres 17 testcontainer — not a clean skip.

## Decisions Made
- **Committed seeding over the rollback fixture (empirically driven).** Before writing the gate I probed the fixtures against a live container and confirmed: (a) the rollback `session` fixture's `commit()` is a SAVEPOINT release inside the still-open outer transaction — INVISIBLE to any other pooled connection (`PROBE_FRESH_SEES_NEW_COMMIT=False`); (b) a fresh `async_session_factory()` session DOES see rows committed by a separate connection via READ COMMITTED (`PROBE_ROLLBACK_SEES_COMMITTED=True`). So all fixture rows the background task must gather are committed to the real container and cleaned up in `finally`.
- **Per-request committing `get_session` override.** With the default rollback session, `mark-read`'s UPDATE holds a row-lock on the committed cursor row until fixture teardown, which deadlocks the committed cascade-delete cleanup (observed as a hard hang). Re-overriding `get_session` with a committing session makes each real route request commit and release immediately — the routes still run fully end-to-end against the real DB.
- **Single-clock timeline.** Phase-A messages are pinned to a fixed PAST host-clock instant (overriding the DDL `server_default=now()`, which is the container clock), the real `mark-read` sets the cursor to host `now()`, and Phase-B is seeded after the cursor with host-clock `created_at` — so every `created_at > cursor` comparison is strict and deterministic across the host/container clock boundary.

## Deviations from Plan

### Adjustments (test-harness plumbing to make the real path actually run — no change to what is proven)

**1. [Rule 3 - Blocking] Committed seeding + committing `get_session` override instead of the plan's `seeded_two_teams` + rollback `session`**
- **Found during:** Task 1 (writing the gate against the real path)
- **Issue:** The plan's SETUP wires `test_catch_me_up_gate(client, seeded_two_teams, session, monkeypatch)`. `catch_me_up` opens its OWN `async_session_factory()` session (a separate pooled connection). Empirically, `seeded_two_teams`/`session` commit via the rollback fixture, whose commits are savepoint-only and INVISIBLE cross-connection — so the background task's own session would see no team/messages and never start the summary. Additionally, `mark-read` on the rollback session holds a cursor row-lock that deadlocks committed cleanup (hard hang).
- **Fix:** Seed teams/users/members/messages via a dedicated committing `async_session_factory()` (distinct `cmu-*` slugs/subs), and re-override `get_session` with a per-request committing session so the real routes run end-to-end and release locks immediately. Rows are torn down in `finally`. This is exactly what the plan's own `<key_constraints>` mandated: "seed + commit against the real DB so the background task's own session reads it… do NOT invent a rollback-fixture approach that the background session can't see."
- **Files modified:** apps/memory-api/tests/test_catch_me_up_gate.py
- **Verification:** `test_catch_me_up_gate` runs GREEN (not skipped) against a real Postgres 17 testcontainer.
- **Committed in:** `14be259`

**2. [Rule 3 - Blocking] Repoint `team_chat_agent.async_session_factory` at the live db factory**
- **Found during:** Task 1
- **Issue:** `team_chat_agent` binds `async_session_factory` at import via `from app.db.session import async_session_factory`; if any test module imported it before the `pg_url` fixture rebound the container factory, the background task would target the wrong (localhost) engine.
- **Fix:** `monkeypatch.setattr("app.services.team_chat_agent.async_session_factory", db_session.async_session_factory)` — plumbing to guarantee the background task's own session hits the SAME testcontainer, independent of suite import order. This is NOT a logic stub: the real `list_messages`/`count_unread_since`/`set_last_read`/gather all still execute.
- **Files modified:** apps/memory-api/tests/test_catch_me_up_gate.py
- **Verification:** Probe confirmed `PROBE_TCA_FACTORY_SEES=True`; the gate's exact-since-window assertion passes.
- **Committed in:** `14be259`

---

**Total deviations:** 2 (both Rule 3 — test-harness plumbing so the REAL cursor/gather/count/isolation path executes against the real DB). What the gate PROVES is exactly the plan's Group-1 + Group-2 spec; nothing about the assertions was weakened. No scope creep.

## Issues Encountered
- **Hard hang on the first run.** The initial version used the plan's rollback `session` fixture for both seeding and route requests; the committed-cleanup `DELETE` blocked forever on the cursor row-lock held by the outer transaction. Diagnosed via reasoning about SQLAlchemy `join_transaction_mode="conditional_savepoint"` (SAVEPOINT semantics under `conn.begin()`), then confirmed by killing the run and switching to the committing-session design. Resolved fully — the file now runs green in ~24s.
- **Ruff on the new file.** After fixing import ordering (I001), the only remaining findings are `UP017` (`timezone.utc` vs `datetime.UTC`) and `RUF100` (the `# noqa: F401` on `import docker`) — both are pre-existing repo-wide conventions (`UP017` also fires on `app/repos/teams.py` and `app/services/team_chat_agent.py`; `RUF100` is present verbatim in the sibling `test_agent_aliases_gate.py`). No hook runs ruff. Kept for consistency with the codebase and the sibling gate the plan said to mirror.

## Known Stubs
None. The ONLY stubs are the deliberate terminal recorders the gate REQUIRES (`_stream_via_anthropic_api` recorder, `_user_has_live_bridge`->False, `centrifugo_client.publish` recorder). The cursor read, the `after_created_at` gather, the caller's-own filter, the unread count, the membership 403 check, and team_scope isolation all run for real — that is the whole point of the gate. Grep-verified: `list_messages`/`count_unread_since`/`set_last_read` are NOT stubbed.

## Threat Flags
None. No new security surface was introduced — this plan is test-only and asserts the mitigations already shipped in 23-01/23-02.

## User Setup Required
None - test-only; requires only Docker (already present in CI and locally). Without Docker the file skips cleanly (integration marker); under Docker a SKIP is a CI failure signal.

## Next Phase Readiness
- SC#1, SC#2 and the ephemerality/isolation half of SC#3 are now EXECUTABLE and green against a real Postgres. Migration 0026 is proven forward-only + edition-agnostic (oss AND saas).
- 23-04 (extension "Catch me up" UI) can build on a backend proven correct at the data/route layer.
- STATE.md / ROADMAP.md deliberately NOT updated (parallel-executor constraint).

## Self-Check: PASSED

- FOUND: `.planning/phases/23-catch-me-up/23-03-SUMMARY.md`
- FOUND: `apps/memory-api/tests/test_catch_me_up_gate.py`
- FOUND commit: `14be259` (test — the gate + migration proof)

---
*Phase: 23-catch-me-up*
*Completed: 2026-07-19*
