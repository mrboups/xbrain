# Phase 11 Plan-Check -- Iteration 1

**Phase:** 11 -- Brain Monitor (Universal Truth-Level Inspector + Soft Delete)
**Date:** 2026-05-14
**Checker:** gsd-plan-checker (Revision Gate, max 3 iterations)
**Plans checked:** 11-01 through 11-09 (9 plans)
**Verdict:** REVISE -- 4 blockers, 5 majors must be fixed before execution

---

## 1. Goal-Backward Trace

### Phase Goal (from ROADMAP.md)
Ship a universal truth-level inspector and soft-delete mechanism across all xbrain entity types, with a browser UI for admins/authors to view, edit, and soft-delete brain events, plus automated 30-day purge.

### Success Criteria Coverage

| SC | Criterion | Plans | Status |
|----|-----------|-------|--------|
| SC-1 | Admin/author can change truth_level on any entity type | 11-04, 11-05, 11-08 | Covered |
| SC-2 | Soft-delete hides rows from default list endpoints | 11-05, 11-06 | Partially covered -- see B-3 |
| SC-3 | Restore returns row to normal state | 11-05, 11-08 | Covered |
| SC-4 | brain-janitor purges rows older than 30 days | 11-07 | Covered |
| SC-5 | v_brain_events view returns all 7+ entity types | 11-02 | Covered |
| SC-6 | No data loss -- existing truth_level values preserved | 11-01, 11-09 | Covered |
| SC-7 | verify-phase11.sh PASS: 12/12 | 11-09 | Covered |

### BMO Requirement Coverage

| Req | Description | Plans | Status |
|-----|-------------|-------|--------|
| BMO-01 | truth_level + deleted_at columns on all 6 tables | 11-01 | Covered but see B-2 (down_revision broken) |
| BMO-02 | v_brain_events UNION ALL view | 11-02 | Covered |
| BMO-03 | Qdrant payload deleted_at_ts + helpers | 11-03 | Covered but see B-5 (backfill in risk section only) |
| BMO-04 | GET /v1/brain/events with filters + cursor pagination | 11-04 | Covered but see B-4, M-3, M-4 |
| BMO-05 | PATCH/DELETE/restore endpoints | 11-05 | Covered but see M-1 |
| BMO-06 | brain-janitor cron (daily, 30-day window) | 11-07 | Covered |
| BMO-07 | Retrieval regression: soft-deleted rows excluded from all reads | 11-06 | Partially covered -- see B-3 |
| BMO-08 | assert_can_edit_brain_event per-event auth | 11-04 | Covered but see B-4 |
| BMO-09 | app-site Brain Monitor UI | 11-08 | Covered but see M-2, M-5 |

---
## 2. Findings

### BLOCKERS (must fix before execution)

---

**B-2 [11-01] Migration chain broken -- wrong down_revision**

Plan 11-01 Task 1 specifies down_revision = "0015_team_messages".

Phase 10 (GitHub-primary auth + org-driven team membership) is listed as the Phase 11 entry gate in ROADMAP.md and explicitly reserves migration 0016 (0016_team_members_blocks). If Phase 10 ships before Phase 11 (as the roadmap intends), then migration 0017 must chain from 0016, not 0015.

The plan proposed mitigation -- "if Phase 10 ships after Phase 11, renumber Phase 10 to 0019" -- is architecturally unsound. Renumbering a shipped migration after the fact breaks Alembic version graph and any existing deployment that already ran 0016.

The plan must use down_revision = "0016_team_members_blocks" unconditionally. If Phase 10 has not shipped at execution time, the executor must wait; this is an entry gate precondition, not a phase-internal decision.

**Fix:** Change 11-01 Task 1 to set down_revision = "0016_team_members_blocks" and remove the renumbering mitigation. Add a pre-execution check: confirm 0016 exists in alembic_version history before running 0017.

**Dimension:** dependency_correctness / migration_chain  
**Severity:** BLOCKER

---

**B-3 [11-06] routes/messages.py absent from retrieval regression scope**

Plan 11-06 patches: tasks.py, crm.py, conversations.py, native_provider.py.

RESEARCH.md Q8 explicitly lists apps/memory-api/app/routes/messages.py as a regression target with classification "Likely missing, MEDIUM risk." BMO-07 requires ALL existing read paths to exclude soft-deleted rows. Skipping messages.py means soft-deleted messages continue to surface in the messages endpoint after Phase 11.

Plan 11-09 verify-phase11.sh (assertion 10) checks tasks count regression but has no assertion for messages. This means the gap would not be caught by automated verification either.

**Fix:**
- Add apps/memory-api/app/routes/messages.py to 11-06 Section 2 (Files touched) and Task 1 scope.
- Add the AND deleted_at IS NULL patch to the messages list endpoint.
- Add a regression test case for messages in test_soft_delete_regression.py Task 3.
- Add assertion 13 to verify-phase11.sh in 11-09 OR include messages in assertion 10 loop.

**Dimension:** requirement_coverage (BMO-07)  
**Severity:** BLOCKER

---

**B-4 [11-04] assert_can_edit_brain_event not verified against Phase 10 principal shapes**

Plan 11-04 Task 2 implements assert_can_edit_brain_event using:
  user_id = principal.get("user", {}).get("id")

Phase 10 introduced GitHub-primary authentication. The Phase 10 principal shape for GitHub OAuth tokens may differ from the existing email/password principal shape. If principal.get("user") returns None for GitHub OAuth principals (e.g., if Phase 10 stores the user under a different key like sub or github_id), then user_id is None for ALL Phase 10 users, causing every PATCH/DELETE/restore call to fail with 403 for non-admins -- even for the content original author.

The plan contains no task to verify the helper against actual Phase 10 principal shapes, no test case for GitHub principal, and no reference to Phase 10 auth implementation.

**Fix:**
- Add a task or sub-step in 11-04 to read Phase 10 principal construction code (likely in apps/memory-api/app/dependencies.py or equivalent) and confirm the exact key path for user ID.
- Add a test case in test_brain_events.py that uses a GitHub-primary token fixture and asserts the owner gets 200 (not 403) on their own item.
- If Phase 10 uses a different principal shape, update the helper to handle both shapes (fallback chain).

**Dimension:** task_completeness / context_compliance (Phase 10 dependency)  
**Severity:** BLOCKER

---

**B-5 [11-03] Qdrant backfill for existing points is in Risk section only -- not in task action**

Plan 11-03 Section 4 (Risks + Mitigations) acknowledges that existing Qdrant points have no deleted_at_ts payload field and Range(lte=0.0) does NOT match points where the field is absent.

RESEARCH.md Q3 Critical Pitfall confirms: Range filters on absent fields return no results for those points. This means after Plan 11-03 ships, the Qdrant filter Range(lte=0.0) will exclude ALL pre-Phase-11 memory points from search results -- effectively zeroing out semantic memory for all content created before Phase 11.

The backfill is mentioned only as a risk note. It does not appear in any task action, files list, verify step, or acceptance criterion. There is no task that iterates existing Qdrant collections and sets deleted_at_ts=0.0 on all points.

**Fix:**
- Add a Task 4 (or sub-step in Task 2) to 11-03: "Backfill existing Qdrant points -- set deleted_at_ts=0.0 on all points in all xbrain collections where the payload field is absent."
- Include the backfill script in files touched (e.g., infrastructure/scripts/backfill_qdrant_deleted_at.py).
- Add to verify: after backfill, POST /v1/memory/search returns at least one pre-Phase-11 result (prove existing content not zeroed out).
- The backfill must run BEFORE Plan 11-06 ships (or simultaneously), since 11-06 depends on 11-03 and the Qdrant filter being correct.

**Dimension:** task_completeness / key_links_planned  
**Severity:** BLOCKER

---
### MAJORS (should fix -- execution will produce degraded or incorrect results)

---

**M-1 [11-05] soft_delete and restore are one-line stubs in repos/brain.py**

Plan 11-05 Task 1 shows soft_delete() and restore() as function stubs with only ellipsis bodies. These are stubs with no implementation. The sa.func.now() warning about Qdrant sync is in Task 2 (route handler), not Task 1 where the stubs live. A stub that returns a bool without executing SQL will pass type-checking but produce silent failures (the DELETE endpoint returns 204 without actually soft-deleting anything).

**Fix:**
- Task 1 must include the full body of both functions: the UPDATE ... SET deleted_at = sa.func.now(), deleted_by = :deleted_by WHERE entity_type = :et AND entity_id = :eid pattern for soft_delete, and UPDATE ... SET deleted_at = NULL, deleted_by = NULL WHERE ... for restore.
- Add an acceptance criterion for Task 1 (not just Task 2): after calling soft_delete() directly, a SELECT confirms deleted_at IS NOT NULL.

**Dimension:** task_completeness  
**Severity:** MAJOR

---

**M-2 [11-08] Polling semantics contradiction -- append vs. replace**

Plan 11-08 Task 2 startPolling() description says "Prepend new rows at top" but the pseudocode shows loadEvents({ append: false, cursor: null }). The append: false flag triggers a full replace of state.items, which wipes any rows the user has scrolled to. The correct behavior for a live-feed poll is to fetch only rows newer than the newest seen item and prepend them, not replace the entire list.

The risk section acknowledges the activeEditRowId guard but this does not resolve the replace-vs-prepend contradiction.

**Fix:**
- Change startPolling() to use a since-based fetch that prepends only new rows, keeping existing rows intact.
- OR implement a dedicated pollForNewItems() function with GET /v1/brain/events?since=<newest_ts>&limit=50 that prepends only new rows.
- Update the acceptance criterion to match the correct semantics.

**Dimension:** task_completeness / scope_reduction  
**Severity:** MAJOR

---

**M-3 [11-04] No test for identical created_at cursor tie-breaking**

Plan 11-04 implements triple-cursor pagination (created_at DESC, entity_type ASC, entity_id ASC) to handle ties. The test suite in Task 3 does not include a fixture that seeds two rows with identical created_at values to exercise the tie-break logic. If the OR-tree cursor predicate is wrong, pages will overlap or skip rows -- a subtle correctness bug that only manifests with concurrent writes.

**Fix:**
- Add test case: seed 3 rows with identical created_at timestamps, page with limit=2, verify page 2 returns exactly 1 row and the union of pages is exactly the 3 seeded rows (no duplicates, no gaps).

**Dimension:** task_completeness  
**Severity:** MAJOR

---

**M-4 [11-04] X-Team-Scope membership enforcement mechanism not specified**

Plan 11-04 Task 2 describes get_team_scope as a dependency that "validates the caller is a member of the requested team." However, the task action does not specify where get_team_scope is defined, which database table it queries, what it returns, or what HTTP status code it raises on non-membership.

Without this specification, the executor has no contract to implement against, and the brain.py route handler and test fixtures cannot be written coherently.

**Fix:**
- Add a specification block to Task 2: exact function signature, table queried, return type, error behavior, and whether it is a new function or an import from Phase 10 auth module.

**Dimension:** task_completeness  
**Severity:** MAJOR

---

**M-5 [11-08] 403 rollback toast lacks permission-denied reason**

Plan 11-08 Task 2 specifies "On 4xx rollback + toast error." A 403 from the PATCH endpoint means the user does not own the item and is not a team admin. Showing a generic error toast when the real cause is a permissions violation will generate user confusion. The delete button should not be visible for non-owner/non-admin rows (handled by canEdit()), but the dropdown change event could still fire if the user manipulates the DOM.

**Fix:**
- Add a case in the error handler: if PATCH/DELETE returns 403, show a specific toast: "You can only edit items you created, or contact a team admin."
- Add a defensive check: if canEdit(item) returns false, disable the change event handler on the dropdown (not just the disabled attribute, which can be bypassed).

**Dimension:** task_completeness  
**Severity:** MAJOR

---

### MINORS

**MINOR-1 [11-01]** contacts.truth_level already exists from migration 0009. Migration must use ALTER COLUMN ... SET DEFAULT not ADD COLUMN. Verify the migration uses IF NOT EXISTS or a column-existence check before ALTER.

**MINOR-2 [11-02]** granola_note CASE WHEN not validated against actual schema. Add verify step: SELECT DISTINCT entity_type FROM v_brain_events must include granola_note.

**MINOR-3 [11-09]** verify-phase11.sh requires .env.test fixtures but no .env.test.example file is planned. Add infrastructure/.env.test.example to files touched for operator onboarding.

---
## 3. Wave-Order Validation

| Wave | Plans | Parallel Safety | Assessment |
|------|-------|-----------------|------------|
| 1 | 11-01 | N/A (solo) | Valid |
| 2 | 11-02 | N/A (solo) | Valid -- depends on 11-01 correctly |
| 3a/3b | 11-03, 11-04 | Safe -- disjoint files | Valid |
| 3c | 11-05 | N/A (solo) | Valid -- depends on 11-03, 11-04 correctly |
| 4 | 11-06, 11-07, 11-08 | Safe -- confirmed disjoint | Valid |
| 5 | 11-09 | N/A (solo) | Valid -- depends on all prior |

Wave 4 parallelism confirmed SAFE:
- 11-06: routes/tasks.py, crm.py, conversations.py, memory_models/native_provider.py
- 11-07: apps/brain-janitor/ (new directory), docker-compose.yml, infrastructure/
- 11-08: app-site/account/teams/brain/, app-site/css/brain.css
No file overlap. All three can execute concurrently.

---

## 4. Migration Chain Validation

Expected Alembic chain for Phase 11:

  0015_team_messages -> 0016_team_members_blocks (Phase 10) -> 0017_brain_monitor_columns (Phase 11) -> 0018_brain_events_view (Phase 11)

Plan 11-01 current state: down_revision = "0015_team_messages" -- BROKEN (skips Phase 10)
Plan 11-02 current state: down_revision = "0017_brain_monitor_columns" -- Correct (depends on 11-01)

Fix required in 11-01 only. After fix, the full chain is valid.

---

## 5. Dimension Results

| Dimension | Result | Notes |
|-----------|--------|-------|
| D1: Requirement Coverage | FAIL | BMO-07 not fully covered (B-3) |
| D2: Task Completeness | FAIL | B-5, M-1, M-3, M-4 |
| D3: Dependency Correctness | FAIL | B-2 (migration chain), B-4 (Phase 10 principal) |
| D4: Key Links Planned | FAIL | B-5 (backfill not wired to task) |
| D5: Scope Sanity | PASS | All plans within 3-task target |
| D6: Verification Derivation | PASS | must_haves truths are user-observable |
| D7: Context Compliance | PASS | All locked decisions honored |
| D7b: Scope Reduction | PASS | No v1/static/placeholder language found |
| D7c: Architectural Tier | PASS | Auth in API tier, UI in app-site |
| D8: Nyquist Compliance | SKIPPED | nyquist_validation=false in config.json |
| D9: Cross-Plan Data Contracts | PASS | No conflicting transforms identified |
| D10: CLAUDE.md Compliance | PASS | Vanilla JS, no framework, OSS stack |
| D11: Research Resolution | PASS | RESEARCH.md Open Questions all resolved |
| D12: Pattern Compliance | SKIPPED | No PATTERNS.md for this phase |

---
## 6. Structured Issues (YAML)

issues:

  - plan: "11-01"
    dimension: "dependency_correctness"
    severity: "blocker"
    description: "down_revision set to 0015_team_messages, skipping Phase 10 migration 0016_team_members_blocks"
    task: 1
    fix_hint: "Change down_revision to 0016_team_members_blocks. Add entry gate check: 0016 must exist in alembic_version before running 0017."

  - plan: "11-06"
    dimension: "requirement_coverage"
    severity: "blocker"
    description: "routes/messages.py absent from soft-delete regression scope. BMO-07 requires all read paths. RESEARCH Q8 explicitly flags it."
    task: 1
    fix_hint: "Add messages.py to files touched, patch the list endpoint, add regression test case, add verify-phase11.sh assertion."

  - plan: "11-04"
    dimension: "task_completeness"
    severity: "blocker"
    description: "assert_can_edit_brain_event uses principal.get(user) without verifying Phase 10 GitHub principal shape. GitHub users may get 403 on all edits."
    task: 2
    fix_hint: "Read Phase 10 principal construction code, verify key path, add GitHub-principal test fixture, handle both principal shapes."

  - plan: "11-03"
    dimension: "task_completeness"
    severity: "blocker"
    description: "Qdrant backfill for existing points (set deleted_at_ts=0.0) is only in Risk section, not in any task action. Without it, all pre-Phase-11 memory search results return zero."
    task: null
    fix_hint: "Add Task 4: backfill script iterating all Qdrant collections, setting deleted_at_ts=0.0 on points missing the field. Add acceptance: memory search still returns pre-Phase-11 content after backfill."

  - plan: "11-05"
    dimension: "task_completeness"
    severity: "major"
    description: "soft_delete() and restore() in repos/brain.py shown as one-line stubs with no SQL body."
    task: 1
    fix_hint: "Provide full function bodies with UPDATE SQL. Add Task 1 acceptance criterion: direct call to soft_delete() results in deleted_at IS NOT NULL in DB."

  - plan: "11-08"
    dimension: "task_completeness"
    severity: "major"
    description: "startPolling() calls loadEvents with append=false (full replace) but description says prepend new rows. Semantics contradiction."
    task: 2
    fix_hint: "Implement since-based poll that prepends only new rows, keeping existing rows intact. Update acceptance criterion."

  - plan: "11-04"
    dimension: "task_completeness"
    severity: "major"
    description: "No test fixture for identical created_at timestamps. Triple-cursor tie-break logic unverified."
    task: 3
    fix_hint: "Add test: 3 rows with same created_at, page with limit=2, verify union of pages = 3 rows with no duplicates."

  - plan: "11-04"
    dimension: "task_completeness"
    severity: "major"
    description: "get_team_scope dependency not specified: no function signature, table, return type, or error behavior documented."
    task: 2
    fix_hint: "Add specification block: exact function signature, table queried, return type, HTTP error code on non-membership."

  - plan: "11-08"
    dimension: "task_completeness"
    severity: "major"
    description: "403 from PATCH/DELETE shows generic error toast. Should show permission-denied reason."
    task: 2
    fix_hint: "Add 403-specific toast: You can only edit items you created, or contact a team admin."

  - plan: "11-01"
    dimension: "task_completeness"
    severity: "minor"
    description: "contacts.truth_level already exists from migration 0009. Migration must use ALTER COLUMN not ADD COLUMN."
    task: 1
    fix_hint: "Confirm migration uses IF NOT EXISTS or column-existence check before ALTER DEFAULT."

  - plan: "11-02"
    dimension: "verification_derivation"
    severity: "minor"
    description: "granola_note CASE WHEN not verified. No acceptance criterion confirms granola_note appears in DISTINCT entity_type from view."
    task: null
    fix_hint: "Add verify step: SELECT DISTINCT entity_type FROM v_brain_events includes granola_note."

  - plan: "11-09"
    dimension: "task_completeness"
    severity: "minor"
    description: "verify-phase11.sh requires .env.test fixtures but no .env.test.example is planned."
    task: 1
    fix_hint: "Add infrastructure/.env.test.example to files touched with all required fixture variable names documented."

---

## 7. Verdict

**REVISE**

4 blockers must be resolved before execution:
1. B-2 -- Fix down_revision in 11-01 (0015 -> 0016_team_members_blocks)
2. B-3 -- Add routes/messages.py to 11-06 retrieval regression scope
3. B-4 -- Verify assert_can_edit_brain_event against Phase 10 GitHub principal shapes in 11-04
4. B-5 -- Promote Qdrant backfill from Risk section to task action in 11-03

5 majors should be resolved in the same revision pass:
- M-1 -- Fill in soft_delete/restore stub bodies in 11-05
- M-2 -- Fix polling semantics contradiction in 11-08
- M-3 -- Add cursor tie-break test in 11-04
- M-4 -- Specify get_team_scope contract in 11-04
- M-5 -- Add 403-specific toast in 11-08

Returning to planner. This is Iteration 1 of max 3.

---

# Iteration 2 - 2026-05-14

**Verdict:** PASS

## Blocker resolution table

| ID | Status | Evidence | Notes |
|----|--------|----------|-------|
| B-1 (migration chain) | Resolved | 11-01 Section 0 + Task 1 | down_revision fixed to 0016_phase10_github_primary. Entry gate with psql check + hard ABORT instruction present. Renumber fallback explicitly rescinded. 11-02 down_revision = 0017_brain_monitor_base (correct). No plan references 0017_brain_monitor_columns or 0015_team_messages as a down_revision. |
| B-2 (messages.py regression) | Resolved | 11-06 Section 2 + Task 1 + Task 3; 11-09 Task 1 assertion 13 | routes/messages.py and repos/messages.py added to files touched. Task 1 patches the messages SELECT with AND deleted_at IS NULL plus per-file grep gates. Task 3 adds cases 6+7 covering the messages list and single-row 404. 11-09 assertion 13 seeds 2 messages, soft-deletes one, asserts API_COUNT == DB_COUNT == 1. |
| B-3 (auth helper) | Resolved | 11-04 Task 2 principal-shape table + helper body | Table documents 6 principal shapes; Google OIDC, Google access token, and GitHub gho_ all map to kind=user with same principal[user] ORM shape post Phase 10 auth-merge. Helper branches only on kind (not on claims[iss]). bridge short-circuits immediately. Task 4 cases 8-10 cover Google/GitHub/xbt_ paths. 12-case test matrix specified. |
| B-4 (Qdrant backfill) | Resolved | 11-03 Section 1b + Task 1 | Backfill promoted to Task 1. Full script at infrastructure/scripts/backfill_qdrant_deleted_at.py with before_count == after_count assertion in script body and acceptance. Section 1b deployment gate: deploy Task 2 then run Task 1 backfill then merge Task 3. Task 3 carries a hard blocker note against merging before backfill confirmation. |

## Major resolution table

| ID | Status | Evidence |
|----|--------|----------|
| M-1 | Resolved | 11-05 Task 1 -- full SQL bodies for soft_delete_entity (UPDATE SET deleted_at/deleted_by WHERE deleted_at IS NULL RETURNING id) and restore_entity (UPDATE SET deleted_at=NULL WHERE deleted_at IS NOT NULL AND deleted_at > NOW() - INTERVAL 30 days RETURNING id). 404/410 distinction explicit. 5 direct repo-unit acceptance checks including freezegun 31-day case. |
| M-2 | Resolved | 11-08 Task 2 -- dedicated pollForNewItems() using ?since={state.items[0]?.created_at}. Prepend: state.items = [...response.items, ...state.items]. startPolling() calls pollForNewItems not loadEvents. state.nextCursor left untouched. Active state.filters forwarded in poll query. Acceptance covers filter+poll interaction. |
| M-3 | Resolved | 11-04 Task 4 case 7 -- 3 rows with identical created_at, paged with limit=2, asserts page 1 returns 2 rows with next_cursor, page 2 returns 1 row, union = 3 with no overlap. Exercises tuple comparison (created_at, entity_type, entity_id). |
| M-4 | Resolved | 11-04 Task 2 get_team_scope contract subsection -- exact signature, table queried (team_members via repos.teams.get_membership), return type (validated team slug string), error behavior (HTTPException 403 per mismatch case), citation deps.py:216-242, explicit no-new-code statement. |
| M-5 | Resolved | 11-08 Task 2 -- exact 403 toast in patchTruthLevel, softDelete, restoreItem: You can only edit items you created. Contact a team admin to modify items created by others. Defensive gate: change handler short-circuits on !canEdit(item) as first line regardless of DOM state. 11-09 UAT step 7 locks verbatim wording. Wording matches assert_can_edit_brain_event HTTPException detail in 11-04 Task 2. |

## New findings (from re-audit)

**Wave-order re-validation:** 11-03 (wave 3a, depends_on 11-01) completes as a plan before 11-04 (wave 3b, depends_on 11-02) starts. The intra-plan deployment gate in 11-03 Section 1b is an operational ordering constraint within the executor's commit sequence, not a change to the plan dependency graph. 11-05 (wave 3c, depends_on 11-03 + 11-04) correctly waits for both. Wave order is intact.

**Migration head consistency:** Confirmed no plan contains 0015_team_messages or 0017_brain_monitor_columns as a down_revision field. Full chain is: 0016_phase10_github_primary -> 0017_brain_monitor_base -> 0018_brain_events_view.

**Phase 10 hard prerequisite:** 11-01 Section 0 specifies a psql check against alembic_version and a hard ABORT instruction if Phase 10 has not shipped. This is a pre-execution gate, not advisory.

**Carry-over MINORs (unaddressed, do not block execution):**
- MINOR-1 (11-01): contacts.truth_level IF NOT EXISTS guard not added; risk section warns against touching the existing CHECK constraint. Acceptable.
- MINOR-2 (11-02): No standalone verify step confirming granola_note appears in DISTINCT entity_type from v_brain_events; covered implicitly by Task 2 fixture if seeded correctly.
- MINOR-3 (11-09): infrastructure/.env.test.example not planned; verify-phase11.sh documents required vars in script header instead.

## Final verdict (Iter 2)

PASS -- all 4 blockers resolved, all 5 majors resolved, plans ready for /gsd:execute-phase 11.

---

# Iteration 3 — 2026-05-14 (Scope-expansion delta — superadmin dashboard)

**Plans audited:** 11-10 (NEW), 11-11 (NEW), 11-09 (revised — Revision 2 section)
**Plans NOT re-audited:** 11-01..11-08 (PASS in iter 2)
**Initial verdict:** REVISE — 1 BLOCKER (B-1) + 1 MAJOR (MAJOR-1) + minors

## Coverage table

| ID | Delivered by | Status |
|----|--------------|--------|
| BMO-10 | 11-10 Task 1/4/5 | ✓ |
| BMO-11 | 11-10 Task 2/4 | ✓ |
| BMO-12 | 11-11 Tasks 1-6 | ✓ |
| SC-8 | 11-10 Task 6 + 11-11 Task 1 + verify-14 | ✓ |
| SC-9 | 11-10 Task 4 + 11-11 Task 6 + verify-15 | ✗ → ✓ after B-1 fix |
| SC-10 | 11-09 Revision 2 assertions 14-16 | ✓ |

## Blocker found

**B-1 [11-11 Task 6] — Drill-down UI called team-scoped `/v1/brain/events` instead of `/v1/admin/brain/events` — audit_log never written, breaks SC-9.**

The Task 6 spec left the API endpoint choice open ("if the simpler path works..."). In reality `get_team_scope` (deps.py:216-242) bypasses ONLY for `kind='bridge'`, so a `kind='user'` superadmin not member of the target team gets 403 on `/v1/brain/events`. The admin endpoint is the only path that (a) bypasses membership check (b) writes audit_log.

## Major found

**MAJOR-1 [11-10 Task 1] — Bridge JWTs implicitly inherit superadmin via `_is_admin()`, with `actor_user_id=None`. Without explicit `actor_sub` capture in payload, bridge cross-team access is unauditable.**

## Revision applied (post-iter-3)

Planner revised in place — see `## Revision 3 (2026-05-14)` sections at the bottom of:
- `11-10-PLAN.md` (MAJOR-1 fix: SECURITY NOTE in docstring + `actor_sub` capture in Task 4 payload + pytest case 9 enforces bridge identity invariant)
- `11-11-PLAN.md` (B-1 fix: `buildBrainEventsRequest` helper substitutes admin endpoint when `IS_SUPERADMIN_VIEW=true`, no `X-Team-Scope` header, edit/delete buttons hidden in superadmin view, polling preserves admin endpoint, 5 acceptance criteria including audit_log row delta check)

## Final verdict (Iter 3 post-revision)

PASS — all 11 plans ready for /gsd:execute-phase 11. Revision applied without a 4th plan-check iteration because the fix was mechanically well-specified (replace Task 6 wholesale + add docstring lines) and spot-verified via grep against the modified PLAN files.
