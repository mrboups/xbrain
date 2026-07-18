---
phase: 17-ci-lockstep
plan: 04
subsystem: ci-verification
tags: [ci, github-actions, actionlint, lockstep, sc3, verification, honest-residual]
requires:
  - .github/workflows/ci-lockstep.yml (Plan 17-03 — the graph this plan parses)
  - infrastructure/scripts/verify-phase17-full.sh (Plan 17-02 — wrapped by a make target)
  - apps/memory-api/tests/test_migration_editions.py (Plan 17-01 — wrapped by a make target)
provides:
  - "The SC3 structural proof: a parse of the needs-graph that fails when an edge is removed"
  - "verify-phase17-workflow.sh — actionlint gate (SKIP=FAIL) + the graph proof"
  - "make verify-phase17{,-workflow,-full,-migrations} — every Phase-17 local gate"
  - "docs/ci-lockstep.md — proven-vs-residual boundary + operator enable steps"
affects:
  - "Any future edit to ci-lockstep.yml is now gated: weakening the lockstep fails a test"
tech-stack:
  added:
    - "actionlint 1.7.12 + shellcheck 0.10.0 as a local gate (cached outside the working tree)"
  patterns:
    - "Structural assertions over parsed config, not greps over comments"
    - "Prove-it-bites: the mutation test is permanent, not a one-off demo"
    - "Tool cache outside the repo so a downloaded binary cannot be committed"
key-files:
  created:
    - apps/memory-api/tests/test_ci_lockstep_graph.py
    - infrastructure/scripts/verify-phase17-workflow.sh
    - docs/ci-lockstep.md
  modified:
    - Makefile
    - .planning/phases/17-ci-lockstep/deferred-items.md
decisions:
  - "Assert lockstep both transitively (the real property) and as direct edges (the authored shape)"
  - "Scan the whole serialized job for rebuild markers — deploy-saas has zero run: steps"
  - "Handle PyYAML's YAML-1.1 on -> True coercion; a naive wf.get('on') asserts nothing"
  - "Cite verify-phase16's 23/23 to Plan 17-02 rather than re-claim it; not re-run here"
  - "Deferred bundling docs/ci-lockstep.md into the release — ci-lockstep.yml is 17-03's file"
metrics:
  duration: ~50 min
  completed: 2026-07-18
  tasks: 3
  commits: 3
---

# Phase 17 Plan 04: SC3 Structural Proof + Honest Residual Summary

Turned the lockstep pipeline from *authored* into *proven-as-far-as-it-can-be*: a 16-assertion
parse of `ci-lockstep.yml`'s `needs:` graph that **fails when an edge is removed**, an actionlint
gate that runs for real with shellcheck active, `make` targets for every Phase-17 gate, and a
residual doc that states plainly what has not happened.

## SC3 is proven by parsing the graph — and the proof bites

`apps/memory-api/tests/test_ci_lockstep_graph.py` loads the workflow with `yaml.safe_load`,
builds the job→`needs:` map, and computes the transitive closure of each ship job's ancestors.
Both `publish-oss-release` and `deploy-saas` are asserted to depend on all three test jobs
**transitively** (the actual lockstep property) **and** as **direct edges** (the authored shape,
so an accidental refactor surfaces for review rather than passing silently).

A structural test that cannot fail is decoration, so this was verified in both directions:

| Mutation | Result |
|---|---|
| Delete the `deploy-saas → test-migrations` edge in the real file | **3 tests FAIL** (`test_deploy_needs_all_three_tests`, `test_lockstep_is_direct_not_merely_transitive`, `test_assertions_detect_a_removed_edge`) |
| Typo the runner label to `ubunut-latest` | **actionlint gate FAILs, exit 1**; the graph proof correctly stays green |

Both mutations were applied to the real file, run, and reverted in a single atomic step;
`git diff --quiet` confirmed a clean restore each time. The second case is the useful one: it
shows the two gates cover **different failure classes** rather than duplicating each other.

`test_assertions_detect_a_removed_edge` makes the first row permanent — it feeds the helpers a
deliberately broken copy of the graph on every run, so the claim "these assertions have teeth"
is itself re-tested rather than asserted once in a summary.

### Two silent-pass traps found while writing it

Both would have produced a green gate that asserted nothing. Neither is hypothetical — both
were confirmed against the real file before being handled:

1. **`on:` is not the string `"on"` after parsing.** PyYAML implements YAML 1.1, where bare `on`
   is a **boolean**, so the trigger block lands under the key `True`. Confirmed:
   `top keys: ['name', True, 'permissions', ...]`. A no-`pull_request` check written the obvious
   way (`workflow.get("on", {})`) inspects `None` and passes while testing nothing. `_triggers()`
   resolves both spellings and raises if neither exists.
2. **`deploy-saas` has zero `run:` steps.** Its entire body is the `script:` input of
   `appleboy/ssh-action`. The plan specified checking that no `run:` string contains `--build`
   or `make deploy` — against this job that iterates an empty list and passes vacuously.
   `_job_text()` serializes the **whole job** instead, so the ssh `script:` is covered too.
   (Comments cannot cause false positives here: they do not survive the parse.)

Beyond the plan's 8 required assertions, the file also checks the graph is acyclic, every
`needs:` target exists, image consumers depend on `build`, exactly one job builds images, and
`publish-oss-release` is **not** gated on `SAAS_DEPLOY_ENABLED` — otherwise disarming the deploy
would silently stop publishing the OSS release too.

## actionlint runs for real (SKIP == FAIL)

`infrastructure/scripts/verify-phase17-workflow.sh`, final run:

```
(a) PASS: actionlint available: ~/.cache/xbrain/actionlint (1.7.12)
(b) PASS: actionlint: 0 findings on .github/workflows/ci-lockstep.yml
(c) PASS: shellcheck rule active — the run: blocks were genuinely shell-linted
(d) PASS: needs-graph proof: 16 passed
PASS: 4 / 4  (SKIP: 0)   EXIT=0
```

Neither binary was present on this host, so both were downloaded (actionlint 1.7.12 via the
upstream installer, shellcheck 0.10.0) rather than skipping the gate. Check (c) exists because
of a **silent weakening**: without shellcheck on PATH, actionlint disables shell linting of the
`run:` blocks — where the real deploy logic lives — and still reports "0 errors". The two runs
are otherwise indistinguishable, so the script now reports which mode it ran in. Evidence the
rule genuinely fired: runtime went 72 ms → 1825 ms, and `shellcheck` left the disabled list.

The lint is **scoped to `ci-lockstep.yml`** deliberately: `deploy-dashboard.yml` carries 2
pre-existing findings that Phase 17 must not touch, and a repo-wide lint would fail this gate on
unrelated debt.

The tool cache lives at `${XBRAIN_TOOL_CACHE:-~/.cache/xbrain}` — **outside the working tree**,
so a downloaded binary can never be accidentally committed and no `.gitignore` entry is needed.

## Make targets — and every one actually run

`make` is **absent on this host** (Windows/Git-Bash), which the plan anticipated. Rather than
stopping at the grep-level acceptance check, I verified the two things that could actually be
wrong and ran every recipe body directly (byte-equivalent to what `make` would invoke):

- **Tab indentation confirmed** via `cat -A` (`^I` on every recipe line). Space-indented recipes
  would break the Makefile for everyone with `make` installed, and no grep-based check catches it.
- `git diff --numstat Makefile` → **21 additions, 0 deletions** — no existing target modified.

| Target | Recipe body result |
|---|---|
| `verify-phase17-workflow` | 4/4 PASS, exit 0 |
| `verify-phase17-full` | 4/4 PASS — 32 services = 10 core + 22 tagged (derived), 18/18 build services remapped to GHCR |
| `verify-phase17-migrations` | **4 passed, 0 skipped** — real testcontainers Postgres, `alembic upgrade head` under `EDITION=oss` and `EDITION=saas`, same head, no branch |

The umbrella runs the static gates first and the Docker-dependent one last, so a fast failure
does not wait on containers.

## The residual, documented rather than faked

`docs/ci-lockstep.md` (191 lines) separates what was run from what was not, and **attributes
each proven row to the plan that ran it**. Notably `verify-phase16`'s 23/23 is cited to **Plan
17-02**, not re-claimed here — this plan did not re-run it (arm64 dev machine; the gate's value
is the amd64 path CI exercises). Logged as deferred item 4 so the gap is visible.

The five residual items, none claimed green: no live GitHub-Actions run (the amd64 build,
runtime and disk fit are all unmeasured); no GHCR push — **the first push creates all 18
packages PRIVATE** and self-hosters get `unauthorized` until an operator flips each one; no
full-profile boot-fit measurement (a `workflow_dispatch` follow-up); no SaaS deploy (VM stopped,
job disarmed via `SAAS_DEPLOY_ENABLED`); and the CI pip-install mirror path is unexecuted.

The `gh api ... /visibility` command is included **flagged as unverified** — it comes from
research, was never executed, and the doc points at the UI path as the reliable one. Claiming a
tested command would be exactly the overclaiming this doc exists to prevent.

Operator enable steps name secret **names** only (`VM_SSH_HOST`, `VM_SSH_USER`, `VM_SSH_KEY`),
state Secrets-never-Variables with the reason, and put "confirm the VM `.env` is current" first —
arming a deploy against a stale VM is worse than not deploying (T-17-04-01, T-17-04-04).

**LICENSE untouched.** The MIT vs. locked-AGPLv3+CLA discrepancy is recorded as a user decision
(D-17-07) with the reason it is cheap to fix later: the workflow ships `LICENSE` verbatim and
asserts no license identifier, so swapping the file needs no workflow change.

## Deviations from Plan

### 1. [Rule 2 — Missing critical functionality] The rebuild check would have passed vacuously

**Found during:** Task 1. The plan specified "no `run:` string in `deploy-saas` contains
`--build` or `make deploy`". `deploy-saas` has **no `run:` steps at all** — verified by parsing
the file — so that assertion iterates an empty list and passes without testing D-17-02.
**Fix:** assert over the whole serialized job (`yaml.safe_dump`), covering ssh-action's
`script:` input where the deploy logic actually lives. Applied to both ship jobs.
**Commit:** `6bb4c09`

### 2. [Rule 2 — Missing critical functionality] The `on:` check would have passed vacuously

**Found during:** Task 1. PyYAML's YAML-1.1 boolean coercion puts the trigger block under the
key `True`, not `"on"`. The natural implementation would have inspected `None` and passed.
**Fix:** `_triggers()` resolves `"on"` and `True`, normalizes str/list/dict forms, and **raises**
if neither key exists — so a future PyYAML change surfaces as a failure, not a silent pass.
**Commit:** `6bb4c09`

### 3. [Rule 2] Check (c): report whether actionlint's shellcheck rule actually ran

**Found during:** Task 2. Not in the plan. Without shellcheck on PATH, actionlint silently
disables shell linting and still prints "0 errors" — a materially weaker gate that looks
identical. The script now detects and reports it, and I downloaded shellcheck so this run got
the full rule set.
**Commit:** `6a5808f`

### 4. Scope: `docs/ci-lockstep.md` not added to the release bundle

The plan's "cross-reference: this doc is the residual bundled from publish-oss-release" would
require editing `ci-lockstep.yml`, which is **Plan 17-03's** declared file, not this plan's.
Taking it silently would put an undeclared change in this plan's diff. **Logged as deferred
item 3** (a one-line `files:` addition) instead. The doc cross-references the workflow throughout.

### 5. `make -n` could not be run (`make` absent on this host)

Anticipated by the plan's acceptance criteria. Compensated with the tab-indentation check and by
running every recipe body directly — see the Make targets section. The umbrella's `$(MAKE)`
recursion is therefore **unexecuted**; it follows standard recursive-make form and every target
it calls is individually verified.

## Threat Flags

None. This plan adds no network endpoint, auth path, or schema. The doc names secret **names**
only — no key value, host or credential appears anywhere in it (T-17-04-01).

## Known Stubs

None. Every assertion runs against the real workflow file; every make target invokes a real
script or test. Nothing is placeholdered or mocked.

## Deferred Issues

Logged to `.planning/phases/17-ci-lockstep/deferred-items.md`:
3. `docs/ci-lockstep.md` is not in the `publish-oss-release` bundle (one-line fix, 17-03's file).
4. `verify-phase16.sh` was not re-run by this plan; its 23/23 is cited to Plan 17-02.

## Self-Check: PASSED

- `apps/memory-api/tests/test_ci_lockstep_graph.py` — FOUND (310 lines, min 60)
- `infrastructure/scripts/verify-phase17-workflow.sh` — FOUND (276 lines, min 30)
- `docs/ci-lockstep.md` — FOUND (191 lines, min 50)
- `Makefile` — 4 `verify-phase17*` targets present; 21 insertions, 0 deletions
- Commit `6bb4c09` (Task 1) — FOUND
- Commit `6a5808f` (Task 2) — FOUND
- Commit `9f335b7` (Task 3) — FOUND
- `git diff --diff-filter=D 15b9c9d..HEAD` — no deletions
- `.planning/STATE.md` / `.planning/ROADMAP.md` — NOT modified, as instructed
