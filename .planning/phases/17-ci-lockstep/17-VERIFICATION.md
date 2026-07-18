---
phase: 17-ci-lockstep
verified: 2026-07-18T21:11:21Z
status: passed
score: 5/5 roadmap success criteria verified (within the documented honest boundary)
overrides_applied: 0
---

# Phase 17: CI Lockstep Verification Report

**Phase Goal:** One CI pipeline per commit builds images ONCE, tests BOTH the OSS subset AND the full profile, then — gated by those tests — publishes the OSS release AND deploys the SaaS full profile. Forward-only, edition-agnostic migrations validated in CI.

**Verified:** 2026-07-18T21:11:21Z
**Status:** passed
**Re-verification:** No — initial verification

## Method

This is not a read-and-trust verification. Every claim in the four SUMMARYs was independently
re-checked against the committed code, and every locally-runnable gate was **executed for real**
in this session (not re-quoted from the SUMMARY), including negative/mutation tests to prove the
gates actually bite rather than pass tautologically:

- Ran `pytest tests/test_ci_lockstep_graph.py` — 16/16 passed against the real workflow.
- Ran `pytest -m integration tests/test_migration_editions.py` — 4/4 passed against real Postgres
  17 testcontainers (Docker was up on this host).
- Ran `bash infrastructure/scripts/verify-phase17-full.sh` — 4/4 passed (daemon-free graph checks).
- Ran `bash infrastructure/scripts/verify-phase17-workflow.sh` — 4/4 passed (actionlint 1.7.12 +
  shellcheck 0.10.0 both genuinely active, 0 findings; graph proof 16 passed).
- **Mutation test 1:** deleted the `deploy-saas -> test-migrations` `needs:` edge in the real
  file → 3 tests in the graph suite went red (`test_deploy_needs_all_three_tests`,
  `test_lockstep_is_direct_not_merely_transitive`, `test_assertions_detect_a_removed_edge`).
  Reverted via `git checkout --`; re-ran clean → 16/16 again.
- **Mutation test 2:** typo'd the build job's `runs-on: ubuntu-latest` → `ubunut-latest` → the
  workflow gate dropped from 4/4 to **3/4** with a real actionlint finding; the graph proof stayed
  green (proving the two gates cover different failure classes, as claimed). Reverted; re-ran
  clean → 4/4 again.
- **Mutation test 3:** hid `shellcheck.exe` from the tool cache → check (c) correctly detected
  "Rule \"shellcheck\" was disabled" and reported it as `SKIPPED` with a loud NOTE rather than
  silently staying green — confirms the exact silent-weakening trap named in the verification
  brief is caught, not papered over. Restored the binary; re-ran clean → 4/4 again.
- Independently parsed `.github/workflows/ci-lockstep.yml` myself with `yaml.safe_load` (not the
  test file's helpers) and confirmed the PyYAML YAML-1.1 `on:` → `True` coercion is real on this
  file (`top-level keys: ['name', True, 'permissions', ...]`), and that the shipped `_triggers()`
  helper is what makes `test_no_pull_request_trigger` non-vacuous.
- Confirmed `deploy-saas` has zero `run:` steps by reading the job body directly (its only step
  is `appleboy/ssh-action` with a `script:` input) — matches the claim that a naive `run:`-only
  scan would miss it, and that `_job_text()` (whole-job YAML dump) is what catches it.
- Confirmed `app.config.Settings._validate_edition` allows only `{"oss", "saas"}` and raises
  `ValueError` for anything else (including `pro`) — REL-03's two-edition scope is real, not
  just claimed in a docstring.
- Dry-collected the `-m integration` suite (244/501 tests, 0 collection errors) to sanity-check
  the `test-full-profile` job's `pytest -m integration -q` step won't fail on import before it
  ever reaches a CI runner.
- Confirmed all cited commit hashes (`8b93010`, `f6aa63e`, `f0bda22`, `e48a563`, `f4b9c17`,
  `6bebf45`, `d1de8ff`, `6bb4c09`, `6a5808f`, `9f335b7`) exist as real commits via `git cat-file -t`.
- Confirmed `packages/memory-models` exists at the repo root, so the CI pip-install workaround
  (`sudo cp -r packages/memory-models /app/packages/memory-models`) is at least structurally sound
  even though it is unexecuted on a real runner (documented residual).
- Working tree left byte-clean after all mutation tests (`git status --short` confirmed no residue
  beyond the pre-existing untracked files from before this session).

## Goal Achievement

### Observable Truths (ROADMAP Success Criteria)

| # | Truth (ROADMAP SC) | Status | Evidence |
|---|---|---|---|
| 1 | A single CI run, triggered by one commit to `main`, builds images exactly once and runs the test suite against both the OSS subset and the full profile before any publish or deploy step executes. | VERIFIED | `build` (no `needs:`) is the sole job using `docker/bake-action` (`test_build_is_single_and_unblocked`, re-run: PASS). `test-oss-subset` and `test-full-profile` both declare `needs: build` (`test_image_consumers_depend_on_build`, PASS). Both test jobs pull GHCR images with `VERIFY16_NO_BUILD=1` / explicit `docker compose pull` — no `--build` anywhere downstream of `build` (`test_deploy_never_rebuilds`, `test_ship_jobs_never_rebuild`, PASS; independently grepped the deploy `script:` block myself, zero `--build`/`make deploy` matches). |
| 2 | That same commit's CI run produces the published OSS release AND deploys the SaaS full profile to production — one commit SHA, both editions shipped, never a manual second push. | VERIFIED (within the honest boundary) | `publish-oss-release` and `deploy-saas` are both authored in the same workflow, both consume `${{ github.sha }}`-tagged images from the same `build` job, both gate on the identical `needs:` list. `deploy-saas` is real (SSH, `preflight-env.sh`, pull-not-build, `up -d`) but gated `if: vars.SAAS_DEPLOY_ENABLED == 'true'` (D-17-04) — confirmed by `test_deploy_is_gated_off` (PASS) and by reading the `if:` line directly. This is the CONTEXT.md-locked, honest scope: the live deploy is explicitly OUT of scope for this phase (VM stopped) and is documented as residual in `docs/ci-lockstep.md`, not claimed green. |
| 3 | If either the OSS-subset tests or the full-profile tests fail, neither the OSS release nor the SaaS deploy proceeds — lockstep is enforced by the pipeline, not by developer discipline. | VERIFIED | Independently parsed the YAML myself (not just re-running the shipped test): both `publish-oss-release` and `deploy-saas` declare `needs: [test-oss-subset, test-full-profile, test-migrations]`. GitHub Actions skips a job when any `needs:` entry fails/skips — this is platform semantics, not an `if:` a developer could get wrong (confirmed no `if: needs.*.result` pattern exists). Proven to actually bite via mutation test 1 above (deleting an edge trips 3 assertions). |
| 4 | An operator running a self-hosted install applies a released migration and upgrades cleanly with a single command — forward-only (no down-migrations) and edition-agnostic (same migration for oss/saas, no `pro`). | VERIFIED | `test_migration_editions.py` run for real: 4/4 passed in 17.19s against fresh Postgres 17 testcontainers under `EDITION=oss` and `EDITION=saas` (config singleton patched directly — confirmed `os.environ` alone is inert because `alembic/env.py` re-reads the frozen `settings` singleton). Applied head asserted == `ScriptDirectory.get_current_head()` (dynamic, never hardcoded). `test_no_migration_branches_on_edition` statically confirms zero `alembic/versions/*.py` files reference `EDITION`. `pro` is confirmed dropped: `Settings._validate_edition` raises `ValueError` for anything outside `{"oss","saas"}`. |
| 5 | Migrations are validated in CI against both profiles before release — a migration that would break one edition never reaches release. | VERIFIED | `test-migrations` job has no `needs:` (runs immediately, doesn't wait on `build`) but IS one of the three jobs both `publish-oss-release` and `deploy-saas` require — confirmed directly in the YAML and by the passing `test_publish_needs_all_three_tests` / `test_deploy_needs_all_three_tests` (both re-run, PASS). The job also enforces SKIP==FAIL: `grep -qiE '[0-9]+ skipped' migration-gate.log` exits 1 with an `::error::` annotation if the migration test self-skips (Docker-absent case), which is right for CI where Docker is always present on `ubuntu-latest`. |

**Score:** 5/5 truths verified.

### Required Artifacts

| Artifact | Expected | Status | Details |
|---|---|---|---|
| `.github/workflows/ci-lockstep.yml` | The single lockstep pipeline (build/test/publish/deploy) | VERIFIED | 401 lines, actionlint-clean (0 findings, shellcheck active), needs-graph independently parsed and confirmed correct. |
| `apps/memory-api/tests/test_migration_editions.py` | Real-Postgres forward-only/edition-agnostic migration proof | VERIFIED | 242 lines, 4/4 tests genuinely re-run against fresh testcontainers this session. |
| `apps/memory-api/tests/test_ci_lockstep_graph.py` | SC3 structural proof (parses `needs:` graph) | VERIFIED | 311 lines, 16/16 genuinely re-run, and independently mutation-tested to confirm it bites. |
| `infrastructure/docker-compose.ci-images.yml` | GHCR override for all `build:` services | VERIFIED | 87 lines, 18/18 build services remapped to `ghcr.io/<owner>/xbrain-*`, no double-prefix, `xbrain-backup` amd64-only note present — confirmed by `verify-phase17-full.sh` check (c), re-run PASS. |
| `infrastructure/scripts/verify-phase17-full.sh` | Full-profile graph + override gate | VERIFIED | 335 lines, 4/4 genuinely re-run (32 = 10 core + 22 tagged, derived not hardcoded). |
| `infrastructure/scripts/verify-phase17-workflow.sh` | actionlint (SKIP=FAIL) + graph proof gate | VERIFIED | 276 lines, 4/4 genuinely re-run, and its shellcheck-detection logic (check c) independently confirmed to correctly downgrade to SKIP when shellcheck is hidden. |
| `infrastructure/scripts/verify-phase16.sh` (NO_BUILD hook) | Opt-in `VERIFY16_NO_BUILD` / `VERIFY16_EXTRA_COMPOSE` hooks, default path unchanged | VERIFIED (by code inspection, not re-booted) | Read the `DC_LIVE` array construction directly: with both env vars unset it expands to exactly the pre-Phase-17 invocation (`docker compose -p $PROJECT -f infrastructure/docker-compose.yml --env-file $OSS_ENV`), and `boot_desc`/`boot_kind` default to the original strings. Phase 16 was independently re-verified live (23/23, real 10-core boot) the same day (`16-VERIFICATION.md`, 2026-07-18T13:00:15Z) — after this exact modification landed, since Plan 17-02 committed `e48a563` (the hook) then Plan 16 was reconfirmed. A full 10-container re-boot was not re-run in this session (heavy, ~5-10 min); code-level inspection plus the same-day 23/23 independent Phase-16 pass give high confidence there is no regression. |
| `Makefile` (`verify-phase17*` targets) | 4 targets: workflow/full/migrations/umbrella | VERIFIED | Confirmed present, real tab indentation (`cat -A` shows `^I`, not spaces), 21 insertions / 0 deletions per `git log`. |
| `docs/ci-lockstep.md` | Honest proven-vs-residual boundary doc | VERIFIED | 191 lines. Reads honestly: explicit "Residual — NOT claimed green" section (5 items: no live run, packages PRIVATE on first push, boot-fit unmeasured, no SaaS deploy, pip-install path unexecuted). No overclaiming found. |

### Key Link Verification

| From | To | Via | Status | Details |
|---|---|---|---|---|
| `publish-oss-release` | `test-oss-subset`, `test-full-profile`, `test-migrations` | `needs:` | WIRED | Confirmed by direct YAML read + re-run test + mutation test. |
| `deploy-saas` | `test-oss-subset`, `test-full-profile`, `test-migrations` | `needs:` | WIRED | Same as above. |
| `deploy-saas` | `vars.SAAS_DEPLOY_ENABLED` | `if:` | WIRED (armed=false) | Gate present and correctly evaluates false while the var is unset — deploy is disarmed by design, not broken. |
| `test-oss-subset` / `test-full-profile` | `build` | `needs:` + GHCR pull | WIRED | Both consume the SHA-tagged images `build` pushes; neither job contains a `--build` marker. |
| `test-migrations` | real Postgres testcontainer | `alembic upgrade head` | WIRED + FLOWING | Verified for real this session: applied head read from `SELECT version_num FROM alembic_version` in the actual DB, not mocked. |

### Data-Flow Trace (Level 4)

Not applicable in the conventional sense — this phase has no UI/dynamic-rendering surface. The
equivalent trace here is "does the gate output real derived numbers, not hardcoded ones," which
was independently checked: `verify-phase17-full.sh`'s 32/10/22 counts come from two independent
`docker compose config` readings that are cross-checked against each other at runtime (confirmed
by reading the script's check (a) logic and by re-running it — output showed the derivation, not
a literal).

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|---|---|---|---|
| SC3 graph proof runs and passes | `pytest tests/test_ci_lockstep_graph.py -v` | 16 passed | PASS |
| SC3 proof actually bites (edge removed) | mutation test 1 | 3 failed as expected | PASS |
| Workflow lint gate bites (typo'd runner) | mutation test 2 | 3/4 (was 4/4) | PASS |
| Silent shellcheck-disable is detected, not hidden | mutation test 3 | correctly SKIPPED + NOTE | PASS |
| Migration test runs for real under both editions | `pytest -m integration tests/test_migration_editions.py -v` | 4 passed in 17.19s | PASS |
| Full-profile graph gate | `bash infrastructure/scripts/verify-phase17-full.sh` | 4/4 | PASS |
| Workflow gate (actionlint + graph) | `bash infrastructure/scripts/verify-phase17-workflow.sh` | 4/4 | PASS |
| Integration suite collects cleanly (pre-CI sanity) | `pytest -m integration --collect-only -q` | 244/501 collected, 0 errors | PASS |
| Edition validation rejects `pro` | read `app/config.py::_validate_edition` | raises `ValueError` for non-`{oss,saas}` | PASS |

### Requirements Coverage

| Requirement | Source Plan(s) | Description | Status | Evidence |
|---|---|---|---|---|
| REL-01 | 17-02, 17-03, 17-04 | Single CI run builds once, tests both profiles before release | SATISFIED | Build-once job + two profile-test jobs, both gating both ship jobs — re-verified live. |
| REL-02 | 17-03, 17-04 | One commit produces OSS release AND SaaS deploy | SATISFIED (within honest boundary) | Both ship jobs authored, gated identically, consuming the same SHA-tagged images; SaaS deploy honestly disarmed (VM down), documented not faked. |
| REL-03 | 17-01, 17-02, 17-04 | Forward-only, edition-agnostic migration path validated in CI | SATISFIED | Real-Postgres test re-run 4/4 green; static no-EDITION-branch guard passes; wired into the CI graph as a required gate. |

No orphaned requirements — REL-01/02/03 are each declared in at least one plan's `requirements:` frontmatter and REQUIREMENTS.md maps all three to Phase 17.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|---|---|---|---|---|
| `.planning/phases/17-ci-lockstep/deferred-items.md` #3 | — | `docs/ci-lockstep.md` not included in `publish-oss-release`'s release bundle | ℹ️ Info | Not a roadmap SC — the bundle only needs to carry `docs/INSTALL.md` + light compose + install docs (which it does). The operational CI doc's absence from the bundle means a self-hoster confused by a `docker pull unauthorized` (private GHCR package) won't find the explanation inside the release itself. Correctly logged as deferred, not silently dropped. |
| `.planning/phases/17-ci-lockstep/deferred-items.md` #2 | — | `pip install -e "apps/memory-api[dev]"` on a CI runner needs a workaround for `file:///app/packages/memory-models` | ℹ️ Info | Verified `packages/memory-models` exists at the repo root the workaround references, so the mirror step is structurally sound, but genuinely unexecuted on a real runner (residual, honestly documented). |
| `.planning/STATE.md` / `.planning/ROADMAP.md` / `.planning/REQUIREMENTS.md` | — | Still show Phase 17 as "Planned" / "Pending" / unchecked | ℹ️ Info | Expected — per the plan SUMMARYs, these were deliberately left untouched during parallel plan execution and are synced by the orchestrator after verification passes. Not a phase-goal gap. |

No blocker or warning-severity anti-patterns found. No placeholder/TODO/stub code in any Phase 17 artifact — every gate invokes a real script or a real test with no mocked assertions.

### Human Verification Required

None. This phase produces CI/release infrastructure with no user-facing surface (`UI hint: no` per ROADMAP). The items that cannot be verified in this session — a live GitHub Actions run, the GHCR push and package-visibility flip, and the actual SaaS deploy — are not "things a human can quickly test"; they require a push to `main` plus secrets configuration plus a restarted production VM, all of which are explicitly OUT of scope for this phase per `17-CONTEXT.md`'s own `<domain>` boundary and are documented as residual (not claimed complete) in `docs/ci-lockstep.md`. Treating this residual as a blocking human-verification item would misapply the category — the phase's own locked scope decision (D-17-01 through D-17-07) already carves this out honestly, and the verification task explicitly asked to confirm the phase is honest about that boundary rather than requiring it be closed.

### Gaps Summary

No gaps found. All five ROADMAP success criteria for Phase 17 are independently verified against
the actual codebase, not just against SUMMARY.md prose:

- The lockstep graph is real, machine-checked, and proven to bite via three separate mutation
  tests performed in this session (not merely re-running the SUMMARY's own claimed numbers).
- The migration-both-editions proof is real (fresh Postgres testcontainers, singleton-patched
  edition, dynamic head assertion) and was re-run live with a matching 4/4 result.
- The two silent-pass traps named in the verification brief (`PyYAML on: -> True` coercion, and
  `deploy-saas` having zero `run:` steps so a naive scan misses its rebuild risk) are both
  genuinely handled in the shipped code, independently confirmed by parsing the YAML myself.
- The SaaS-deploy and GHCR-publish residual is honestly bounded: authored for real, disarmed by a
  repo variable, never claimed as a live deploy anywhere in the workflow comments, the SUMMARYs,
  or `docs/ci-lockstep.md`.
- The one thing not re-executed in this session — a full 10-container `verify-phase16.sh` boot
  under the new `VERIFY16_NO_BUILD` hook — was assessed by code inspection instead (the hook's
  default-off expansion is byte-identical to the pre-Phase-17 invocation) plus reliance on the
  same-day independent Phase 16 re-verification (23/23) recorded in `16-VERIFICATION.md`. This is
  a reasonable evidentiary substitute, not a trust-the-SUMMARY shortcut, and does not change the
  overall verdict.

---

*Verified: 2026-07-18T21:11:21Z*
*Verifier: Claude (gsd-verifier)*
