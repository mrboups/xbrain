# CI Lockstep — what is proven, and what is not

`.github/workflows/ci-lockstep.yml` is the pipeline behind REL-01/02/03: one push to `main`
builds every image **once**, proves it with **three** independent test gates, and only then
runs **both** ship jobs — the public OSS release and the SaaS deploy.

This document exists because the pipeline is **authored but never executed**. Everything below
is split into what has actually been run and what has not. A green `make verify-phase17` says
the pipeline is well-formed and correctly wired. It does **not** say the pipeline works. Do not
read a green lint as a green run.

## The graph

```
build ─┬─> test-oss-subset ────┬─> publish-oss-release
       └─> test-full-profile ──┤
           test-migrations ────┴─> deploy-saas   (if: vars.SAAS_DEPLOY_ENABLED == 'true')
```

Lockstep is **structural** (D-17-01). `publish-oss-release` and `deploy-saas` each declare
`needs: [test-oss-subset, test-full-profile, test-migrations]`, and GitHub Actions skips a job
outright when anything in its `needs:` failed or was skipped. One red test blocks **both** ships
by platform semantics — there is no `if:` to get wrong and no reviewer to forget. Those edges
are the SC3 proof, and they are machine-checked (see below) rather than trusted.

`test-migrations` deliberately has no `needs:`: it proves the migration chain, not the images,
so it starts immediately in parallel while still gating both ship jobs.

## What is proven locally now (SKIP == FAIL)

Every check below CAN always run — no daemon for the static ones, no network, no live runner.
That is what makes treating a skip as a failure honest: a skip could only mean the gate was
dodged. All results are from real runs, with the plan that ran them noted.

| Gate | Command | Result | Ran by |
|---|---|---|---|
| Workflow lint | `make verify-phase17-workflow` | actionlint 1.7.12, **0 findings** on `ci-lockstep.yml`, with the shellcheck rule **active** (the `run:` blocks were genuinely shell-linted, not silently skipped) | Plan 17-04 |
| SC3 needs-graph | `pytest tests/test_ci_lockstep_graph.py` | **16 assertions pass** against the real YAML | Plan 17-04 |
| Migrations, both editions | `make verify-phase17-migrations` | **4 passed, 0 skipped** — `alembic upgrade head` on a real testcontainers Postgres under `EDITION=oss` AND `EDITION=saas`, both reaching the same head with no branch | Plan 17-04 |
| Full-profile graph | `make verify-phase17-full` | **4/4** — 32 services resolve as 10 core + 22 profile-tagged (derived, not hardcoded); 18/18 `build:` services remapped to `ghcr.io/<owner>/xbrain-*` | Plan 17-04 |
| OSS-subset boot | `make verify-phase16` | **23/23**, a real 10-core boot + SC#3 HTTP walk | Plan 17-02 (**not** re-run by 17-04) |

### The SC3 proof is a parse, and it bites

`apps/memory-api/tests/test_ci_lockstep_graph.py` loads the workflow with `yaml.safe_load`,
builds the job→`needs:` map, and asserts that both ship jobs depend on all three test jobs —
transitively (the real lockstep property) **and** as direct edges (the authored shape). It also
asserts `build` is single and unblocked, `deploy-saas` is `SAAS_DEPLOY_ENABLED`-gated and
contains no rebuild marker, every `uses:` is pinned, there is no fork-PR trigger, the graph is
acyclic, and every `needs:` target exists.

It was verified to fail, not just to pass. Deleting the `deploy-saas → test-migrations` edge in
the real file turns 3 tests red; the typo'd runner label `ubunut-latest` — which the parser
cannot see — drives the actionlint gate to exit 1. The two checks cover different failure
classes, and `test_assertions_detect_a_removed_edge` re-proves the graph checks bite on every
run by feeding the helpers a deliberately broken copy.

Two traps that would have made these gates pass while asserting nothing, both handled:

- **`on:` is not the string `"on"` after parsing.** PyYAML implements YAML 1.1, where bare `on`
  is a boolean, so the trigger block lands under the key `True`. A no-`pull_request` check
  written against `wf.get("on")` inspects `None` and passes silently.
- **`deploy-saas` has zero `run:` steps** — its entire body is the `script:` input of
  `appleboy/ssh-action`. A rebuild check that scans only `run:` strings inspects an empty list
  and passes vacuously. The test serializes the whole job instead.

## Residual — NOT claimed green

None of the following has happened. They need a push, real runner infrastructure, registry
permissions, or a live VM, and none is claimed complete by Phase 17.

### 1. No live GitHub Actions run

The workflow lands with the commit that creates it; the first real run happens on the next push
to `main`. **Unmeasured:** whether the `build` job successfully builds all 18 images for amd64
on a hosted runner, total pipeline runtime, and whether the OSS-subset boot fits the runner's
disk. CI is also the only place in this toolchain that natively produces amd64 — the dev machine
is arm64 — so the amd64 image path itself is still unproven.

### 2. No GHCR push, and the packages will be PRIVATE on first push

The first push to each `ghcr.io/<owner>/xbrain-*` name creates that package as **private**,
regardless of the source repository's visibility, and package visibility is not settable at push
time. Until an operator flips each one, a self-hoster following `docs/INSTALL.md` gets
`unauthorized` from `docker pull` on an image the Releases page calls published.

This is a **one-time, per-package** step covering all 18 images, and the workflow cannot
self-heal it. Via the UI: each package's own Settings → Danger Zone → Change visibility →
Public. The API form recorded in `17-RESEARCH.md` is:

```bash
gh api --method PATCH "/user/packages/container/xbrain-<service>/visibility" -f visibility=public
```

That command has **not been executed or verified** — treat it as a starting point and confirm
against current GitHub API docs; the UI path is the reliable one.

### 3. Full-profile boot fit is unmeasured

Per D-17-03, `test-full-profile` is a daemon-free graph + override resolve plus the memory-api
suite under `EDITION=saas` — deliberately **not** a 32-container boot, which may not fit a
hosted runner's ~14 GB disk. Measuring whether it does is a `workflow_dispatch` dry-run of
`build` + `test-full-profile`, a documented follow-up rather than a gate.

### 4. No SaaS deploy

The prod VM is stopped (cost measure) and `SAAS_DEPLOY_ENABLED` is unset, so `deploy-saas` is
skipped on every run. Its SSH path, the VM `.env`'s currency, and the assumption that the deploy
directory holds this commit's compose files are all unverified against a live host. The job is
**disarmed, not stubbed**: setting one repo variable makes it execute the code as written.

### 5. The CI pip-install path is unexecuted

`test-full-profile` and `test-migrations` mirror the container layout
(`sudo cp -r packages/memory-models /app/packages/memory-models`) before `pip install`, because
`apps/memory-api/pyproject.toml` declares `xbrain-memory @ file:///app/packages/memory-models` —
an absolute path that exists only inside the Docker build context. That workaround is reasoned
from the declared dependency, not run on a runner. The durable fix is a memory-api packaging
change; see `.planning/phases/17-ci-lockstep/deferred-items.md`.

## Enable steps for `deploy-saas` (operator)

Work through these in order. Steps 1–2 are the ones that matter: arming a deploy against a
stale VM is worse than not deploying at all.

1. **Start the prod VM and confirm its `.env` is current.** This project has a history of VM
   `.env` variables going missing (`GITHUB_APP_*` has vanished before). The job runs
   `preflight-env.sh` against the VM's own `.env` and fails loudly rather than booting a
   half-configured stack, but confirm it first.
2. **Sync the deploy directory to the commit being deployed** — specifically that
   `infrastructure/docker-compose.ci-images.yml` is present. Without that override, `up -d`
   falls back to the base file's `build:` keys and **rebuilds on the VM**, defeating build-once.
   The job hard-fails if the file is missing rather than silently rebuilding.
3. **Add the repo SECRETS** (Settings → Secrets and variables → Actions → *Secrets*):
   `VM_SSH_HOST`, `VM_SSH_USER`, `VM_SSH_KEY`. These must be **Secrets, never Variables** —
   Variables are readable in plain text in logs and by anyone with read access to the settings.
   No registry secret is needed: the built-in `GITHUB_TOKEN` covers GHCR for this repo.
4. **Set the repo VARIABLE** `SAAS_DEPLOY_ENABLED=true` (same page, *Variables* tab). This one
   is a Variable by design — it is a switch, not a credential.

The job then pulls the immutable `:${{ github.sha }}` images and runs `up -d` — never `--build`,
and never the `make deploy` target, whose remote `up -d` rebuilds on the VM. **Unsetting the
variable disarms deploys again** with no change to the workflow file.

## LICENSE follow-up (D-17-07)

`LICENSE` in this repo is still **MIT**, while `REQUIREMENTS.md` records a locked
**AGPLv3 + CLA** decision. That discrepancy is unresolved and is **not** Phase 17's to resolve:
changing a project's license is legally significant and belongs to the owner.

The pipeline is authored so this costs nothing to fix later. `publish-oss-release` attaches the
`LICENSE` file exactly as it exists on disk and asserts no license identifier anywhere — the
strings `MIT`, `AGPL` and `SPDX` appear nowhere in the workflow. **Swapping the file later needs
no workflow change.**

**This must be reconciled BEFORE any real public OSS release.** The first published release
distributes whatever `LICENSE` file is on disk at that moment, under those terms, to whoever
downloads it — and that is not easily walked back.

## Future upgrade (optional)

The `build` job is **amd64-only**, on purpose: `xbrain-backup`'s base image
(`google/cloud-sdk:slim`) publishes no arm64 manifest, so a uniform multi-platform build would
fail on exactly that service. amd64 is the prod architecture REL-01 needs, and the runner builds
it natively without QEMU.

True multi-arch is available via the now-GA `ubuntu-24.04-arm` hosted runners (a real second
build, not emulation), which would help contributors on arm64 dev machines — this project's
included. `xbrain-backup` would stay amd64-only regardless. Not a Phase 17 requirement.

## Running the gates locally

```bash
make verify-phase17              # the whole locally-verifiable set (below, in order)
make verify-phase17-workflow     # actionlint + the SC3 needs-graph proof   (static, fast)
make verify-phase17-full         # full-profile graph + GHCR override       (no daemon)
make verify-phase17-migrations   # alembic under both editions              (needs Docker)
```

`make` is absent on some Windows dev setups; the recipe bodies are plain one-liners and can be
run directly (`bash infrastructure/scripts/verify-phase17-workflow.sh`) byte-equivalently.

The workflow gate downloads `actionlint` on first use into `${XBRAIN_TOOL_CACHE:-~/.cache/xbrain}`
— outside the working tree, so a downloaded binary can never be committed by accident. Installing
`shellcheck` alongside it is worth it: without it, actionlint silently disables shell linting of
the `run:` blocks while still reporting "0 errors". The gate tells you which mode it ran in.

---

*Phase 17 — CI Lockstep. See `.planning/phases/17-ci-lockstep/` for the plans, the decision
record (D-17-01…D-17-07) and `deferred-items.md`.*
