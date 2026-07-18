---
phase: 17-ci-lockstep
plan: 03
subsystem: ci-infrastructure
tags: [ci, github-actions, ghcr, lockstep, release, deploy-gate]
requires:
  - infrastructure/docker-compose.yml (18 build: services, core/integrations/saas/ops profiles)
  - infrastructure/docker-compose.ci-images.yml (Plan 17-02 — GHCR image names, single source of truth)
  - infrastructure/scripts/verify-phase16.sh (Plan 17-02 — VERIFY16_NO_BUILD / VERIFY16_EXTRA_COMPOSE hooks)
  - infrastructure/scripts/verify-phase17-full.sh (Plan 17-02 — full-profile graph + override gate)
  - apps/memory-api/tests/test_migration_editions.py (Plan 17-01 — both-edition migration proof)
provides:
  - "ci-lockstep.yml: build-once -> three test gates -> gated publish + deploy, in one workflow"
  - "The needs: graph that makes SC3 lockstep structural and machine-parsable (Plan 17-04 asserts on it)"
  - "An authored-but-disarmed deploy-saas job with documented enable steps"
affects:
  - "Plan 17-04 parses this file's needs: edges and documents the residual/enable steps"
tech-stack:
  added:
    - "GitHub Actions (first CI for the product itself; deploy-dashboard.yml was unrelated)"
    - "GHCR as the build-once artifact store"
  patterns:
    - "Lockstep by needs: edges, not if: conclusion checks — the platform skips, so it cannot be forgotten"
    - "Image names derived from the compose override, never restated in the workflow"
    - "SKIP == FAIL enforced explicitly where a test self-skips on missing Docker"
    - "Ship jobs gated by a repo variable (deploy armed?) kept separate from test gating (tests green?)"
key-files:
  created:
    - .github/workflows/ci-lockstep.yml
  modified: []
decisions:
  - "Tags come from docker-compose.ci-images.yml's image: keys, not a bake *.tags= wildcard (wildcards only expand left of the =)"
  - "bake-action v7 has no workdir input — repo-root-relative files: paths instead (unknown inputs are silently ignored)"
  - "publish moves :latest with imagetools create (manifest copy), so :latest and :<sha> are provably the same bytes"
  - "deploy-saas authored in full but disarmed via vars.SAAS_DEPLOY_ENABLED (D-17-04)"
  - "License file shipped verbatim; no license identifier appears anywhere in the workflow (D-17-07)"
metrics:
  duration: ~55 min
  completed: 2026-07-18
  tasks: 3
  commits: 3
---

# Phase 17 Plan 03: The CI Lockstep Workflow Summary

Authored `.github/workflows/ci-lockstep.yml` — one pipeline where a push to `main` builds every
`build:` service exactly once into GHCR, proves it with three independent gates, and only then
runs both ship jobs. Lockstep is structural: `publish-oss-release` and `deploy-saas` each
declare `needs: [test-oss-subset, test-full-profile, test-migrations]`, so one red test blocks
both by platform semantics rather than by anyone remembering to check.

## The lockstep graph (SC3 — the whole point)

Parsed straight out of the authored YAML:

```
build                    needs=none                                          if=-
test-oss-subset          needs=build                                          if=-
test-full-profile        needs=build                                          if=-
test-migrations          needs=none                                           if=-
publish-oss-release      needs=[test-oss-subset, test-full-profile, test-migrations]  if=-
deploy-saas              needs=[test-oss-subset, test-full-profile, test-migrations]  if=vars.SAAS_DEPLOY_ENABLED == 'true'
```

GitHub Actions skips a job outright when anything in its `needs:` failed or was skipped, so no
`if: needs.*.result == 'success'` appears anywhere — that would be redundant with `needs:` and
would only add a way to get it wrong. `test-migrations` deliberately has no `needs:`: it proves
the migration chain, not the images, so it runs immediately in parallel while still gating both
ship jobs.

## What Was Built

### Task 1 — header + build job (commit `f4b9c17`)

- Triggers: `push: branches: [main]` + `workflow_dispatch`. **No `pull_request`** — a fork PR
  would otherwise run with GHCR push rights and the VM SSH key in scope.
- `permissions: contents: read` at top level; jobs escalate explicitly (`packages: write` only
  on build/publish, `contents: write` only on publish).
- `concurrency: cancel-in-progress: false` — cancelling could kill an in-flight deploy mid
  `up -d` and leave the VM half-updated.
- `build` bakes both compose files with `push: true` and `*.platform=linux/amd64`, producing
  `ghcr.io/<owner>/xbrain-<svc>:<sha>` for all 18 `build:` services in one pass.

### Task 2 — the three test gates (commit `6bebf45`)

- **`test-oss-subset`** (`needs: build`): GHCR login → `docker system prune -af` (14 GB runner
  disk) → explicit `pull` → `verify-phase16.sh` with `VERIFY16_NO_BUILD=1` and
  `VERIFY16_EXTRA_COMPOSE=…ci-images.yml`. Reuses Phase 16's proven real-boot gate against the
  exact images that will be published — the no-build mode is what makes "tested == shipped"
  true rather than aspirational.
- **`test-full-profile`** (`needs: build`): `verify-phase17-full.sh` (graph + override
  completeness, daemon-free) plus the memory-api integration suite under `EDITION=saas`.
  Not a 32-container boot, per D-17-03.
- **`test-migrations`** (no `needs:`): `alembic upgrade head` per edition against real Postgres.

### Task 3 — the two ship jobs (commit `d1de8ff`)

- **`publish-oss-release`**: moves `:latest` with `docker buildx imagetools create` — a manifest
  copy, not a rebuild, so `:latest` and `:<sha>` are the same digest and `:latest` stays
  traceable to an exact commit. The image list is derived by running `compose config --images`
  with all profiles on and filtering for the GHCR prefix, so it can never drift from the
  override file. Release bundle: `docs/INSTALL.md`, both compose files, `chrome-extension.zip`,
  and the repo's license file verbatim.
- **`deploy-saas`**: SSH → guard that the override file is present → `preflight-env.sh` against
  the VM's own `.env` → registry login via stdin → `pull` → `up -d` → `ps`. Disarmed by
  `if: vars.SAAS_DEPLOY_ENABLED == 'true'`.

## Verification — what is actually proven

### actionlint: RUN, zero findings

Real output, `actionlint` v1.7.12 with **shellcheck v0.10.0 on PATH** (I downloaded shellcheck
specifically so the `run:` blocks — which contain the real deploy logic — were genuinely
linted, not skipped):

```
verbose: Linting .github/workflows/ci-lockstep.yml
verbose: Using project at D:\VSC\xbrain\.claude\worktrees\agent-afbcc6e8d570063c8
verbose: Found 0 parse errors in 1 ms for .github/workflows/ci-lockstep.yml
verbose: Rule "pyflakes" was disabled: exec: "pyflakes": executable file not found in %PATH%
verbose: Found total 0 errors in 358 ms for .github/workflows/ci-lockstep.yml
```

Note the `shellcheck` rule is absent from the disabled list — it ran. Only `pyflakes` stayed
disabled, which is irrelevant here (no inline `python -c` blocks in this workflow).

Repo-wide baseline: `actionlint` over all workflows reports exactly 2 findings, both
pre-existing in `deploy-dashboard.yml` (`GITHUB_`-prefixed variable names), zero in this
phase's file. Logged to `deferred-items.md`; not fixed (out of scope).

### Structural assertions (all passed)

| Assertion | Result |
|---|---|
| `build` has no `needs:` | PASS |
| The 3 test jobs exist; oss-subset + full-profile `needs: build`; migrations independent | PASS |
| BOTH ship jobs `needs:` all three test jobs | PASS |
| `deploy-saas` gated on `SAAS_DEPLOY_ENABLED` | PASS |
| No `pull_request` / `pull_request_target` trigger | PASS (`['push', 'workflow_dispatch']`) |
| No `--build` / `make deploy` in executable YAML | PASS |
| No license identifier (word-boundary `MIT`/`AGPL`/`SPDX`) | PASS |
| Every third-party action pinned to the researched tag | PASS |

### NOT proven — the documented residual

This workflow **has never executed**, and nothing below should be read as green:

1. **No live run.** The workflow lands with the commit that creates it; the first real run
   happens on the next push to `main`. Build success on a hosted runner, total runtime, and
   the disk fit of the OSS-subset boot are all unmeasured.
2. **No GHCR push, no visibility flip.** First push creates each of the 18 packages as
   **private** regardless of repo visibility; a self-hoster following INSTALL.md would get
   `unauthorized` until each is flipped to public (one-time, per package).
3. **No SaaS deploy.** The prod VM is stopped and `SAAS_DEPLOY_ENABLED` is unset, so the job
   is skipped on every run. Its SSH path, the VM `.env` currency, and the assumption that the
   deploy directory holds this commit's compose files are all unverified against a live host.
4. **The pip install path is unexecuted.** The `/app/packages/memory-models` mirror step is
   reasoned from the pyproject's declared dependency, not run on a runner.

To arm the deploy: start the VM and confirm its `.env`; sync the repo to the deployed commit;
add secrets `VM_SSH_HOST` / `VM_SSH_USER` / `VM_SSH_KEY`; set variable
`SAAS_DEPLOY_ENABLED=true`. Unsetting the variable disarms it again with no file change.
Plan 17-04 writes these into `docs/ci-lockstep.md`.

## Deviations from Plan

### 1. [Rule 1 — Bug] `bake-action` `workdir:` input does not exist in v7.3.0

**Found during:** Task 1. Both 17-RESEARCH.md and the plan specified `workdir: infrastructure`.
Fetching `docker/bake-action@v7.3.0`'s real `action.yml` shows inputs `source`, `files`, `set`,
`targets`, `push`, … and **no `workdir`** (it was replaced by `source` in an earlier major).
GitHub Actions **silently ignores unknown inputs** — no error, no warning — so the build would
have run from the repo root with `files: docker-compose.yml` unresolvable. actionlint does not
catch this either.
**Fix:** repo-root-relative `files:` paths. Compose resolves each service's relative build
context against the compose file's own directory, so `context: ../apps/librechat` still works.
**Commit:** `f4b9c17`

### 2. [Rule 1 — Bug] The researched `*.tags=` wildcard would produce invalid tags

**Found during:** Task 1. The research example was
`set: *.tags=ghcr.io/<owner>/xbrain-*:${{ github.sha }}`. Bake expands `*` only on the **left**
of the `=` (the target pattern); the right side is a literal. Every one of the 18 targets would
have been tagged with one identical string containing a literal `*`.
**Fix:** drop the `set:` tag override entirely and let bake read the tags from
`docker-compose.ci-images.yml`'s `image:` keys (compose→bake maps `image:` to target tags).
Strictly better than the plan's approach: the override file is already the declared single
source of truth for those names, so build / test / publish / deploy cannot drift onto different
strings — the exact defect that would let a service silently rebuild.
**Commit:** `f4b9c17`

### 3. [Rule 1 — Bug] Unquoted colon in a step name broke YAML parsing

**Found during:** Task 1, caught by the first actionlint run:
`name: Build all build: services once…` → `could not parse as YAML: mapping values are not
allowed in this context`. Quoted the step name. This is the gate doing its job.
**Commit:** `f4b9c17`

### 4. [Rule 3 — Blocking] memory-api's absolute path dependency breaks pip install on a runner

**Found during:** Task 2. `apps/memory-api/pyproject.toml` declares
`xbrain-memory @ file:///app/packages/memory-models` — a path that exists only inside the
Docker build context, so `pip install -e "apps/memory-api[dev]"` fails on a CI runner.
**Fix:** mirror the container layout before installing
(`sudo cp -r packages/memory-models /app/packages/memory-models`), with a comment explaining
why. The durable fix is a packaging change in memory-api, out of scope here — logged in
`deferred-items.md`.
**Commit:** `6bebf45`

### 5. [Rule 2 — Missing critical functionality] SKIP == FAIL enforced on the migration gate

**Found during:** Task 2. `test_migration_editions.py` self-skips when Docker is absent — right
on a laptop, a lie in CI, where Docker is always present. A plain `pytest` invocation would
report green having executed nothing, silently voiding REL-03's load-bearing proof (the test's
own docstring instructs Plan 17-03 to treat a skip as failure).
**Fix:** the step tees pytest output and fails the job with a `::error::` annotation if the
summary reports any skips.
**Commit:** `6bebf45`

### 6. Acceptance-criteria greps replaced with equivalents that test intent, not prose

Two of the plan's literal acceptance commands are false-positive generators against a
well-commented file. I ran stricter variants that test the actual requirement, and both pass:

- `! grep -nE 'up -d --build|make deploy'` — matched my own explanatory comments describing why
  those are forbidden. **Ran instead:** the same check over comment-stripped executable YAML
  (PASS, zero matches). I also reworded the comments so the literal grep now passes too, so
  Plan 17-04 will not hit a false alarm either way.
- `! grep -niE 'MIT|AGPL|license.*='` — case-insensitive `MIT` matches **`commit`**, a word this
  file uses constantly and legitimately ("one commit = one image set"). Satisfying it literally
  would mean gutting the documentation. **Ran instead:** a word-boundary check
  `\b(MIT|AGPL|AGPLv3|SPDX)\b` (PASS, zero matches), which is what D-17-07 actually requires:
  no license identifier asserted anywhere in the pipeline.

### 7. `actions/setup-python@v5` is a major-version pin, not an exact tag

Every other action is pinned to the exact researched release. `setup-python@v5` is a
major-version tag, kept deliberately: it is what the plan's interfaces list specifies and what
the existing `deploy-dashboard.yml` already uses, and it is a first-party `actions/*` action.
Flagging it as a known, accepted inconsistency rather than leaving it silent.

## Threat Flags

None. This plan adds no new network endpoint, auth path, or schema. The security-relevant
surface it does add (registry credentials, VM SSH key, fork-PR exposure, token scope) was all
enumerated in the plan's threat register and mitigated as specified: `GITHUB_TOKEN` only (no
minted PAT), secrets forwarded via `envs:` and read from stdin (never interpolated or echoed),
no `pull_request` trigger, least-privilege per-job `permissions:`, pinned actions, immutable
SHA tags, and a disarmed-by-default deploy.

## Known Stubs

None. Every job body invokes real scripts and real tests. `deploy-saas` is fully implemented —
it is *disarmed*, not stubbed: the difference is that setting one repo variable makes it
execute the code as written, with no placeholder to fill in.

## Self-Check: PASSED

- `.github/workflows/ci-lockstep.yml` — FOUND (400 lines, exceeds the 120-line minimum)
- `.planning/phases/17-ci-lockstep/deferred-items.md` — FOUND
- Commit `f4b9c17` (Task 1) — FOUND
- Commit `6bebf45` (Task 2) — FOUND
- Commit `d1de8ff` (Task 3) — FOUND
- `.planning/STATE.md` / `.planning/ROADMAP.md` — NOT modified, as instructed
