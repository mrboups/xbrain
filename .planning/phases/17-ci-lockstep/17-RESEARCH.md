# Phase 17: CI Lockstep - Research

**Researched:** 2026-07-18
**Domain:** GitHub Actions CI/CD — multi-service Docker build, gated test-before-publish/deploy pipelines, edition-agnostic Alembic migration validation
**Confidence:** MEDIUM-HIGH (mechanics of the pipeline are HIGH — verified against the live repo + GitHub API; the actual SaaS deploy target is a documented residual because the VM is stopped)

<user_constraints>
## User Constraints (from CONTEXT.md)

No `CONTEXT.md` exists for this phase (`has_context: false` per init). There are no
locked decisions or discretion notes from `/gsd-discuss-phase` to carry forward. The
only upstream constraints are ROADMAP.md's Phase 17 entry gate/success-criteria text
and CLAUDE.md's project-wide rules (both reproduced below). Treat ROADMAP SC1-SC5 as
the locked scope; there is no narrower or wider user steer beyond it.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| REL-01 | A single CI run per commit builds images once and tests both the OSS subset and the full profile before any release | §Architecture Patterns (job graph), §Code Examples (build + test jobs), §Validation Approach |
| REL-02 | One commit produces both the published OSS release (tagged images + light compose + install docs) AND the deployed SaaS full profile | §Architecture Patterns (publish/deploy jobs), §Common Pitfalls (rebuild-on-deploy pitfall), §Open Questions (VM-down residual) |
| REL-03 | An operator upgrades a running self-host install through a forward-only, edition-agnostic migration path | §Code Examples (migration test), §Don't Hand-Roll (testcontainers reuse), §Runtime facts (Alembic chain audit) |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

- **Open-source + self-hostable only; no managed-cloud-only services in the critical path.** GHCR (GitHub Container Registry) is acceptable — it is a free adjunct of the hosting platform the project already depends on for source control and CI, and self-hosters can always build from the Dockerfiles directly without ever touching a registry. Recommending GHCR does not violate this constraint.
- **Dev machine is ARM64, prod is amd64 — never `docker build` locally then deploy to the GCP VM.** CI is explicitly where the real amd64 build must happen (GitHub-hosted `ubuntu-latest` runners are amd64-native). This phase is the first place in the project where a genuine amd64 build-and-push exists outside the VM itself.
- **GSD workflow enforcement.** No direct repo edits outside a GSD command; this research feeds `/gsd-plan-phase 17`.
- **Language:** planning artifacts and all workflow/YAML/doc content this phase produces must be English-only (product/code rule) — conversation with the user stays French.
- **`.planning/config.json`**: `workflow.nyquist_validation: false` (explicit) — the standard "Validation Architecture" template section is skipped in favor of a phase-specific `## Validation Approach` section (still required, per the phase brief's explicit research question 6).

## Summary

xbrain currently has **zero CI test/build automation** — the only workflow in
`.github/workflows/` is `deploy-dashboard.yml`, a cron+push job that regenerates a
static dashboard and deploys it to Firebase Hosting; it never touches
`infrastructure/docker-compose.yml`, never builds an xbrain image, and never runs
`pytest`. Phase 17 is therefore a from-scratch pipeline, not an extension of an
existing one. `infrastructure/docker-compose.yml` defines 33 services across an
untagged core (10 services, 5 of them `build:`) plus three opt-in profiles
(`integrations`, `saas`, `ops` — 23 more services, 13 more `build:`), confirmed live
via `docker compose config --services`/`--profiles` in this session (works without a
running daemon — pure parse). 18 services in total have a `build:` block; the other
15 are pre-built upstream images pulled from Docker Hub / GHCR / Chainguard.

The repo already has the exact primitive REL-03 needs: `apps/memory-api/tests/conftest.py`'s
session-scoped `pg_url` fixture spins a real `testcontainers.postgres.PostgresContainer`,
runs `alembic upgrade head` via `alembic.command.upgrade()` in a worker thread (env.py
is async and cannot nest inside pytest-asyncio's loop), and yields the URL. Every one
of the 24 existing migrations already carries a `downgrade()` — the "forward-only"
requirement is a going-forward CI **contract** (never run/require `downgrade()` in
release validation), not a retrofit. No migration references `EDITION` today, so
edition-agnosticism is presently true by omission; the real risk is a *future*
migration silently branching on edition, which a parametrized `EDITION=oss` /
`EDITION=saas` variant of the existing fixture catches structurally.

The honest boundary this phase must respect: the SaaS deploy target (a GCP VM) is
**currently stopped** (per operator record, cost-saving measure since 2026-06-18) and
`make deploy` rebuilds the images a second time, on the VM itself, over SSH — which
would silently violate "build once" even if the VM were running. A plan that claims a
green, live `deploy-saas` job this phase would be lying about a run it cannot produce.
The honest deliverable is: author the deploy job for real (secrets, SSH, `docker
compose pull && up -d` against registry-tagged images, not a rebuild), gate it behind
an explicit repo variable that defaults off, and record the live run as a residual —
exactly the pattern Phase 16 already used for its own build-on-VM interim step.

**Primary recommendation:** One workflow, `.github/workflows/ci-lockstep.yml`,
triggered on push to `main`. A single `build` job uses `docker/bake-action` (or
`docker compose build` + a loop) to build all 18 `build:` services once on
`ubuntu-latest` (amd64-native — this is where the real prod-arch build happens),
tags every image `ghcr.io/<owner>/xbrain-<service>:<sha>`, and pushes. Two parallel
jobs — `test-oss-subset` (pulls images, `docker compose up` with `COMPOSE_PROFILES`
unset, reuses `verify-phase16.sh`'s pattern) and `test-full-profile` (same images,
`COMPOSE_PROFILES=integrations,saas,ops`, a new live-boot health gate) — plus a
`test-migrations` job (testcontainers Postgres, `alembic upgrade head` under both
`EDITION=oss` and `EDITION=saas`) all `need: [build]` or run independently, and two
terminal jobs — `publish-oss-release` and `deploy-saas` — both declare
`needs: [test-oss-subset, test-full-profile, test-migrations]`. That `needs:` list is
what makes SC3 structural: GitHub Actions does not run a job if any of its `needs:`
failed, full stop — no conditional logic to get wrong.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Image build (18 `build:` services) | CI runner (GitHub-hosted `ubuntu-latest`, amd64) | — | This is the ONLY place in the project's toolchain that natively produces the prod architecture (amd64) without QEMU emulation — the dev machine is arm64. |
| Image storage / distribution | Container registry (GHCR) | — | Build-once-use-many across separate CI jobs (each job is a fresh VM) requires the artifact to leave the build job; a registry is the standard mechanism, and the same tagged image doubles as the release artifact. |
| Test execution — OSS subset / full profile | CI runner (Docker Compose, live boot) | — | Matches the project's own established convention (`verify-phaseNN.sh`): a config-layer or mocked check is explicitly rejected in this codebase's own comments as insufficient ("the gate lesson"). |
| Migration validation | CI runner (testcontainers-Postgres) | Database tier (Postgres 17) | Already the pattern in `apps/memory-api/tests/conftest.py`; extending it, not inventing a new mechanism. |
| OSS release publish | CI runner -> GitHub Releases + GHCR | — | GitHub Releases is the natural home for "tagged images + light compose + install docs" bundles; no external release infra exists or is needed. |
| SaaS deploy | CI runner -> GCP VM (SSH) | VM / Docker Compose (deploy target) | The actual runtime lives on the VM; CI only triggers `docker compose pull && up -d` there — CI must NOT rebuild on the VM (that would be a second, untested build). |
| Edition gating logic (EDITION flag, profiles) | API / Backend (memory-api `app/main.py`, `docker-compose.yml` `profiles:`) | — | Already shipped in Phase 15 — Phase 17 tests it, does not implement it. |

## Standard Stack

### Core

| Library / Action | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `actions/checkout` | v7.0.0 | Checkout repo in every job | Official, universal first step. `[VERIFIED: gh api repos/actions/checkout/releases/latest]` |
| `docker/setup-buildx-action` | v4.2.0 | Provision Buildx builder (multi-platform, GHA cache backend) | Official Docker action, required for `bake`/`build-push-action` with `cache-to: type=gha`. `[VERIFIED: gh api]` |
| `docker/login-action` | v4.4.0 | Authenticate to `ghcr.io` using `GITHUB_TOKEN` | No new secret needed for GHCR — the built-in token works when the workflow declares `permissions: packages: write`. `[VERIFIED: gh api]` |
| `docker/bake-action` | v7.3.0 | Build all `docker-compose.yml` `build:` targets in one step via `docker buildx bake -f infrastructure/docker-compose.yml` | Purpose-built to consume a Compose file directly — avoids hand-rolling a build matrix or an 18-step loop; supports parallel build + GHA layer cache. `[VERIFIED: gh api repos/docker/bake-action]` |
| `docker/metadata-action` | v6.2.0 | Derive `ghcr.io/<owner>/xbrain-<service>:<sha>` tags consistently | Standard companion to build-push/bake actions; avoids hand-rolled tag string interpolation repeated 18 times. `[VERIFIED: gh api]` |
| `docker/build-push-action` | v7.3.0 | Fallback / per-image build if `bake-action`'s Compose ingestion proves awkward for any one service | Same team, same cache semantics as `bake-action`; safe substitute. `[VERIFIED: gh api]` |
| `testcontainers[postgres]` | already `>=4.8` in `apps/memory-api/pyproject.toml` (dev extra) | Real Postgres 17 container for migration validation | Already adopted by the codebase (`conftest.py::pg_url`); GitHub-hosted `ubuntu-latest` ships Docker Engine pre-installed, so this needs zero extra CI setup. `[VERIFIED: repo read + WebSearch cross-check]` |
| `rhysd/actionlint` | v1.7.12 (binary) | Lint the new workflow YAML — catches invalid `${{ }}` expressions, unknown contexts, shellcheck issues in `run:` blocks | Downloaded and RAN against the repo's existing `deploy-dashboard.yml` in this research session — genuinely found 2 pre-existing issues (GITHUB_-prefixed var names). `[VERIFIED: this session, `curl .../download-actionlint.bash \| bash` then executed]` |
| `softprops/action-gh-release` | v3.0.2 | Attach `docs/INSTALL.md`, the light compose file, and `chrome-extension.zip` to a GitHub Release | Standard, avoids hand-rolling `gh api` multipart upload calls. `[VERIFIED: gh api]` |
| `appleboy/ssh-action` | v1.2.5 | SSH into the VM for the (gated) `deploy-saas` job | Optional — the Makefile's raw `ssh -i $(SSH_KEY) -o BatchMode=yes` one-liner already works and needs no new dependency; only worth adding if the plan wants nicer known_hosts handling. `[VERIFIED: gh api]` |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `reviewdog/action-actionlint` | v1.72.0 | Actionlint-as-PR-annotation wrapper | Only if the plan wants inline PR comments instead of a plain job log; the raw binary (above) is sufficient for a push-to-main gate. `[VERIFIED: gh api]` |
| `actions/upload-artifact` / `actions/download-artifact` | v7.0.1 / v8.0.1 | Inter-job file passing (NOT for the 18 images — see Don't Hand-Roll) | Useful only for small artifacts like a verify-script log bundle, not for the multi-GB image set. `[VERIFIED: gh api]` |
| `python:3.13` + `pyyaml` | local | Cheap YAML syntax sanity check without a full actionlint install | Already proven available on the research machine; a fallback if the actionlint binary download is blocked in some environment. `[VERIFIED: this session]` |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| GHCR (`ghcr.io`) | Docker Hub | Docker Hub's anonymous pull rate limits (100 pulls/6h) can bite self-hosters and CI re-runs; GHCR has none for public images and is already the registry Open WebUI's own upstream image uses in this compose file, so operators need no new registry account to inspect the images. |
| Push-to-registry between jobs | `actions/upload-artifact` tar export | Works, but 18 images (several GB combined, one of them — `librechat` — bundling a full Node build) as job artifacts is slow to upload/download twice (once per parallel test job) and burns the repo's artifact storage quota for no benefit, since the pushed images ARE the release artifact anyway (double duty). |
| `docker/bake-action` reading `docker-compose.yml` | 18 individual `docker/build-push-action` steps | Functionally equivalent, but a hand-written 18-entry job matrix duplicates information already declared once in `docker-compose.yml` (context/dockerfile per service) — a new service added to compose would silently NOT get built in CI unless someone remembers to add an 19th matrix entry. Bake reading the compose file directly cannot drift this way. |
| Rebuilding on the VM at deploy time (current `make deploy` behavior) | `docker compose pull && up -d` against the CI-pushed, CI-tested images | `make deploy`'s `up -d --build` on the VM is a SECOND, untested build using the VM's own toolchain/cache state — it can silently diverge from what CI tested (dependency resolution at a different point in time, different base-image digest if not pinned). This is the single most important "don't hand-roll" catch of this phase. |
| A hand-rolled bash script driving `docker compose up` + curl assertions for the "full profile" test | Reuse the SAME pattern as `verify-phase16.sh`/`verify-phase15.sh` (this repo's own convention) | Not really an alternative — the project has independently arrived at "config-layer check is not a gate, only a real boot is" three times (Phase 14/15/16 postmortems say so explicitly in the scripts' own header comments). Inventing a different testing philosophy for Phase 17 would break consistency with 15 prior phases' verification style for no benefit. |

**Installation:** No new Python/Node packages. `testcontainers[postgres]` is already a
dev dependency of `apps/memory-api`. New GitHub Actions are referenced by tag in the
workflow YAML — nothing to `pip install`/`npm install`. The only new local prerequisite
for a developer to dry-run this pipeline's YAML before pushing is the `actionlint`
binary (downloadable via the official script, proven working in this session, ~2.4 MB,
no Go toolchain required).

**Version verification:** All action versions above were checked against the GitHub
API (`gh api repos/<owner>/<repo>/releases/latest`) in this research session, not
recalled from training data — see the `[VERIFIED: gh api]` tags. `docker/bake-action`
in particular is a genuine finding: it did not seem obviously present in general
Docker Action folklore before this session's check but is a real, current (v7.3.0),
actively-released action exactly suited to this phase's "build once, driven by the
existing compose file" requirement.

## Architecture Patterns

### System Architecture Diagram

```
 git push to main
        |
        v
 +-------------------+
 |   lint (fast)      |  actionlint on the new workflow YAML + ruff on 8 Python
 |   needs: []        |  services that carry a pyproject/ruff config
 +-------------------+
        |
        v  (does not gate anything downstream -- advisory)

 +-------------------+
 |   build            |  docker/bake-action against infrastructure/docker-compose.yml
 |   needs: []         |  --> builds all 18 `build:` services ONCE, amd64, on ubuntu-latest
 |                     |  --> tags ghcr.io/<owner>/xbrain-<service>:<sha>, pushes
 +---------+----------+
           |
    -------+-------------------------------------
    |               |                            |
    v               v                            v
+-----------+ +------------------+   +---------------------+
|test-oss-  | |test-full-profile |   |test-migrations       |
|subset     | |                  |   |                      |
|needs:     | |needs: [build]    |   |needs: [] (independent|
|[build]    | |COMPOSE_PROFILES= | | of image build --      |
|COMPOSE_   | |integrations,saas,| | runs alembic against a |
|PROFILES   | |ops -- 33 services| | real Postgres          |
|unset --   | |live boot + health| | testcontainer under    |
|10 services| |walk (NEW script) | | EDITION=oss AND        |
|live boot +| |                  | | EDITION=saas)          |
|SC#3 walk  | |                  |   |                      |
|(reuses    | |                  |   |                      |
|verify-    | |                  |   |                      |
|phase16.sh)| |                  |   |                      |
+-----+-----+ +--------+---------+   +----------+-----------+
      |                |                        |
      +--------+-------+------------------------+
               |
   needs: [test-oss-subset, test-full-profile, test-migrations]
               |
     +---------+----------+
     |                    |
     v                    v
+---------------+   +------------------+
|publish-oss-   |   |deploy-saas       |
|release        |   |                  |
|- retag :sha   |   |if: vars.SAAS_    |
|  -> release   |   |   DEPLOY_ENABLED |
|  tag          |   |   == 'true'      |
|- gh release   |   |- ssh VM          |
|  create +     |   |- docker compose  |
|  upload docs/ |   |  pull (registry  |
|  compose/     |   |  images, NOT     |
|  extension.zip|   |  --build)        |
+---------------+   |- up -d           |
                     +------------------+
```

A reader tracing the primary use case: a commit lands on `main` -> `build` produces
every image exactly once and pushes it to GHCR under the commit SHA -> three
independent verification jobs pull those SAME images and prove both editions boot and
both migration paths apply -> only if ALL THREE succeed do the two terminal jobs run,
and even then `deploy-saas` additionally requires an explicit operator-set repo
variable before it will attempt to touch the VM.

### Recommended Project Structure

```
.github/
├── workflows/
│   ├── ci-lockstep.yml          # the one pipeline this phase adds
│   └── deploy-dashboard.yml      # pre-existing, unrelated, untouched
└── workflow-templates/           # pre-existing, unrelated, untouched
infrastructure/
├── docker-compose.yml            # untouched by this phase -- CI reads it, doesn't fork it
└── scripts/
    ├── verify-phase16.sh         # REUSED as-is for the oss-subset test job
    └── verify-phase17-full.sh    # NEW -- the full-profile live-boot health gate
apps/memory-api/tests/
└── test_migration_editions.py    # NEW -- parametrized EDITION=oss/saas alembic upgrade test
                                   # (mirrors test_migration_0019.py's testcontainers pattern)
```

### Pattern 1: Build-once via `docker buildx bake` against the existing Compose file

**What:** A single job step invokes Buildx Bake, pointed directly at
`infrastructure/docker-compose.yml`, to build every `build:` service in one command
with shared layer caching, then tags and pushes each to GHCR.
**When to use:** Whenever a Compose file already enumerates the build contexts (this
repo's does) — avoids re-declaring 18 build targets in workflow YAML.
**Example:**
```yaml
# Source: docker/bake-action README (https://github.com/docker/bake-action) + this
# session's verified `docker compose config --services` output (10 core / 33 full)
- name: Set up Docker Buildx
  uses: docker/setup-buildx-action@v4.2.0

- name: Log in to GHCR
  uses: docker/login-action@v4.4.0
  with:
    registry: ghcr.io
    username: ${{ github.actor }}
    password: ${{ secrets.GITHUB_TOKEN }}

- name: Build every service once (bake reads infrastructure/docker-compose.yml)
  uses: docker/bake-action@v7.3.0
  with:
    workdir: infrastructure
    files: docker-compose.yml
    push: true
    set: |
      *.tags=ghcr.io/${{ github.repository_owner }}/xbrain-*:${{ github.sha }}
    # NOTE: docker-compose.yml's own `image:` fields (e.g. xbrain/memory-api:phase2)
    # are untouched -- `set:` here overrides the tag for THIS bake invocation only,
    # so local `make build`/`make up` behavior for developers does not change.
```

### Pattern 2: Reuse the built images across jobs via GHCR, not artifacts

**What:** Every downstream job authenticates to GHCR (`docker/login-action`) and does
a plain `docker pull ghcr.io/<owner>/xbrain-<service>:${{ github.sha }}` (or a
`docker compose pull` against a small override file that swaps `build:` for
`image: ghcr.io/...:${{ github.sha }}`) instead of rebuilding.
**When to use:** Any time an artifact must cross a job boundary in GitHub Actions
(each job is a fresh VM) and the artifact is a container image.
**Example:**
```yaml
# infrastructure/docker-compose.ci-images.yml (NEW, small override file)
# Source: standard Compose multi-file override pattern (docs.docker.com/compose)
services:
  memory-api:
    image: ghcr.io/${{ github.repository_owner }}/xbrain-memory-api:${{ github.sha }}
  mcp-gateway:
    image: ghcr.io/${{ github.repository_owner }}/xbrain-mcp-gateway:${{ github.sha }}
  # ... one entry per build: service, image ONLY (no build: key) --
  # `docker compose -f docker-compose.yml -f docker-compose.ci-images.yml pull`
  # then pulls the CI-built image instead of triggering a local build.
```

### Pattern 3: Edition-agnostic migration validation (extends the existing fixture)

**What:** Parametrize the existing `pg_url`-style fixture over `EDITION=oss` and
`EDITION=saas`, run `alembic upgrade head` against a fresh container for each, and
assert both reach the same head with no error — proving no migration branches on
edition today, and catching it structurally if one ever does.
**When to use:** REL-03 SC5 — "migrations validated in CI against both profiles
before release."
**Example:**
```python
# Source: mirrors apps/memory-api/tests/conftest.py::pg_url (verified in this session)
# and apps/memory-api/tests/test_migration_0019.py's assertion style.
import os
import pytest
from testcontainers.postgres import PostgresContainer

EDITIONS = ["oss", "saas"]

@pytest.mark.parametrize("edition", EDITIONS)
@pytest.mark.integration
async def test_alembic_upgrade_head_is_edition_agnostic(edition, monkeypatch):
    monkeypatch.setenv("EDITION", edition)
    pg = PostgresContainer("postgres:17", username="test", password="test", dbname="test")
    pg.start()
    try:
        asyncpg_url = pg.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql+asyncpg://"
        )
        os.environ["DATABASE_URL"] = asyncpg_url
        from alembic import command
        from alembic.config import Config
        cfg = Config("apps/memory-api/alembic.ini")
        cfg.set_main_option("sqlalchemy.url", asyncpg_url)
        cfg.set_main_option("script_location", "apps/memory-api/alembic")
        command.upgrade(cfg, "head")  # must not raise for EITHER edition
    finally:
        pg.stop()
```
This is a **new, small test file**, not a rewrite of `conftest.py` — the existing
`pg_url` fixture is session-scoped and shared by 20+ test files; a second,
function-scoped, EDITION-parametrized variant living in its own file avoids
destabilizing the rest of the suite.

### Anti-Patterns to Avoid

- **Re-running `make deploy` as the CI deploy step:** it triggers `docker compose ...
  up -d --build` ON THE VM over SSH — a second, untested build. Replace with a
  pull-only invocation against the SHA-tagged images CI already tested.
- **Gating `deploy-saas` with an `if:` that checks test job *conclusions* manually**
  (e.g. `if: needs.test-oss-subset.result == 'success'`): redundant and fragile —
  `needs:` already skips the job entirely on any upstream failure. Only add an `if:`
  for the SEPARATE concern of "is the VM even supposed to receive a deploy right now"
  (the `vars.SAAS_DEPLOY_ENABLED` gate).
  Reserve manual `result ==` checks for cases where a job must run even after a
  failure (rare; not needed here).
- **`cancel-in-progress: true` on the workflow's `concurrency:` group:** would kill an
  in-flight `deploy-saas` job if a second commit lands on `main` moments later,
  potentially leaving the VM mid-`up -d`. Use `cancel-in-progress: false` (queue,
  don't cancel) for this specific workflow.
- **Testing "the full profile" by rebuilding images a second time inside the test job:**
  defeats "build once" even if it happens to pass — the test job must PULL, never
  build.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Multi-service Docker build orchestration | An 18-entry job matrix or a hand-written parallel-build bash script | `docker/bake-action` reading `infrastructure/docker-compose.yml` directly | Bake already understands Compose's `build:`/`context:`/`args:` shape; a hand-rolled matrix duplicates that information and drifts silently when a service is added/removed from compose. |
| Cross-job artifact passing for container images | `actions/upload-artifact` tar export of 18 images | GHCR push (build job) + pull (every downstream job) | Registry push/pull is the standard mechanism for images specifically, and the pushed image doubles as the release artifact — no separate "export" step needed. |
| GHCR authentication / token refresh | A hand-minted PAT stored as a new secret | `docker/login-action` with the built-in `secrets.GITHUB_TOKEN` (declare `permissions: packages: write`) | The default token already has repo-scoped push rights to that repo's own GHCR namespace when granted the permission — no new secret to rotate or leak. |
| GitHub Release asset upload (multipart) | Raw `curl`/`gh api` multipart calls | `softprops/action-gh-release` | Handles the multipart upload, tag creation, and release-notes body in one step; used by thousands of OSS projects, well past its own edge cases. |
| Real-Postgres migration testing | A hand-rolled `docker run postgres` + polling script | `testcontainers.postgres.PostgresContainer` (already a dev dependency; already used by `conftest.py`) | Already solved in this exact repo — extending the existing fixture is strictly less work than inventing a parallel mechanism, and GitHub-hosted runners already have Docker pre-installed for it to use. |
| Workflow YAML correctness checking | Eyeballing the YAML / relying on the first live run to catch typos | `actionlint` (downloadable binary, proven in this session; found 2 real issues in the ONE existing workflow on first run) | Catches invalid `${{ }}` expressions, unknown contexts, and `run:`-block shell issues (via embedded shellcheck) BEFORE a push burns CI minutes on a workflow that fails at YAML-parse time. |

**Key insight:** every piece of this pipeline already has a well-maintained,
current-version, first-party or de-facto-standard GitHub Action — the only genuinely
new code this phase should write is (a) the new full-profile live-boot script (in the
project's own established bash-verify-script style) and (b) the small
EDITION-parametrized migration test file. Everything else is wiring existing actions
together via a `needs:` graph.

## Common Pitfalls

### Pitfall 1: GHCR packages default to PRIVATE on first push
**What goes wrong:** The very first `docker push` to a new `ghcr.io/<owner>/xbrain-*`
package name creates it as a PRIVATE package regardless of the source repo's
visibility or which token pushed it. A self-hoster following the published install
docs would get an authentication error trying to `docker pull` a "published" OSS
image.
**Why it happens:** GHCR package visibility is a separate setting from repo
visibility and is NOT configurable at push time — it must be changed after the fact,
per-package, in the package's own Settings page (or via the GitHub API).
**How to avoid:** After the FIRST successful `build` job push for each of the 18
image names, a one-time manual (or scripted, via `gh api
/user/packages/container/<name>/visibility`) step sets each package to Public.
Document this as an operator/maintainer one-time setup step, not something the
workflow can self-heal on every run.
**Warning signs:** `docker pull` from outside the org returns `unauthorized` even
though the repo itself is public. `[VERIFIED: WebSearch, cross-referenced against GitHub's own package-visibility docs and multiple independent write-ups]`

### Pitfall 2: The "full profile" live-boot test may not fit GitHub's free runner
**What goes wrong:** `ubuntu-latest` free-tier public-repo runners provide 4 vCPU /
16 GB RAM / **14 GB SSD**. The full profile is 33 services including Neo4j,
ClickHouse, LibreChat+Mongo+Meili, and 13 more `build:` images on top of the 5 core
ones — pulling/building all of that plus the images already produced by the `build`
job risks exceeding the 14 GB disk budget before a single container even starts.
**Why it happens:** CLAUDE.md's own VM sizing table put the "all services" tier at
`e2-standard-4` (16 GB RAM) specifically BECAUSE of this same service set; disk was
never the constraint on a VM with an attached persistent disk, but it is the binding
constraint on an ephemeral CI runner.
**How to avoid:** `docker system prune -af` between the `build` job and the
`test-full-profile` job's `docker compose pull`; consider whether `test-full-profile`
needs to assert full functional walks (like `verify-phase16.sh` does for the core) or
whether a boot-and-healthcheck-only assertion is sufficient for SC1/SC5 — the phase
brief's own language ("tests ... the full profile") does not require the SAME depth
of walk as the OSS-subset SC#3 flow, only that it boots and passes health.
**Warning signs:** `docker compose up` for the full profile fails with `no space left
on device` mid-pull, or ClickHouse/Neo4j fail their first-boot writes with disk errors.
`[VERIFIED: docs.github.com runner spec via WebFetch this session; disk-budget risk is this session's own inference from the repo's known service list, flagged as a risk not a certainty]`

### Pitfall 3: `xbrain-backup` is the one image that must NOT be built multi-arch
**What goes wrong:** A naive "build every image for both amd64 and arm64" policy
would fail specifically on `xbrain-backup` — its base image `google/cloud-sdk:slim`
has no arm64 manifest at all, and its Dockerfile additionally hardcodes a
`mongodb-database-tools-debian12-x86_64-100.10.0.deb` download.
**Why it happens:** This was already discovered and documented in Phase 15's own
research (`.planning/phases/15-edition-mechanics/15-RESEARCH.md`) — carried forward
here since Phase 17 is the first phase that actually builds this image anywhere but a
prod VM.
**How to avoid:** Build `xbrain-backup` amd64-only (which happens to be exactly what
a `ubuntu-latest` GitHub runner produces natively anyway — no special-casing needed
if the bake target simply doesn't request a multi-platform manifest for this one
service). `[VERIFIED: 15-RESEARCH.md direct quote, re-read this session]`
**Warning signs:** A `--platform linux/arm64,linux/amd64` bake invocation applied
uniformly to all 18 services fails specifically on `xbrain-backup`'s base-image pull.

### Pitfall 4: `make deploy`'s rebuild-on-VM breaks "build once" even when the VM is up
**What goes wrong:** Even setting aside that the VM is currently stopped, the
EXISTING `make deploy` target's final step is `docker compose ... up -d --build` —
run over SSH, ON the VM. If Phase 17's `deploy-saas` job simply SSH'd in and ran
`make deploy`, it would silently rebuild every image a second time, on different
hardware/toolchain state than the CI `build` job used, defeating REL-01's "build
images exactly once" guarantee even on a fully green pipeline.
**Why it happens:** `make deploy` predates this phase and was designed for a
developer-triggered manual deploy where "build on the target" was the only available
mechanism (no registry existed yet).
**How to avoid:** `deploy-saas` must use a DIFFERENT invocation — `docker compose -f
docker-compose.yml -f docker-compose.ci-images.yml pull && up -d` (pull the
CI-pushed, CI-tested images; never `--build`) — not a call to `make deploy` verbatim.
**Warning signs:** VM logs show a `docker build` step during what was supposed to be
a "deploy the already-tested image" run.

## Code Examples

### The `needs:` graph enforcing SC3 (lockstep by construction)
```yaml
# Source: this session's design, patterned on GitHub Actions' own `needs:` semantics
# (docs.github.com/actions/using-jobs/using-jobs-in-a-workflow) -- verified: a job
# listed in `needs:` is skipped entirely (not merely conditionally run) when any of
# its dependencies fail or are skipped, with no extra `if:` required.
jobs:
  build: { ... }
  test-oss-subset:   { needs: build, ... }
  test-full-profile: { needs: build, ... }
  test-migrations:   { ... }               # independent of `build` -- no images needed
  publish-oss-release:
    needs: [test-oss-subset, test-full-profile, test-migrations]
    ...
  deploy-saas:
    needs: [test-oss-subset, test-full-profile, test-migrations]
    if: vars.SAAS_DEPLOY_ENABLED == 'true'   # separate concern: "is deploy armed"
    ...
```

### Minimal permissions block (supply-chain hygiene)
```yaml
# Source: GitHub Actions default-token docs -- repos created after Feb 2023 default
# to read-only GITHUB_TOKEN; each job that needs more must declare it explicitly.
permissions:
  contents: read      # checkout
# per-job overrides, principle of least privilege:
jobs:
  build:
    permissions:
      contents: read
      packages: write   # push to GHCR
  publish-oss-release:
    permissions:
      contents: write   # create a GitHub Release + upload assets
      packages: write   # retag the release image
```

### Concurrency guard (don't cancel an in-flight deploy)
```yaml
# Source: docs.github.com/actions/using-jobs/using-concurrency
concurrency:
  group: ci-lockstep-${{ github.ref }}
  cancel-in-progress: false
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| No CI at all — `deploy-dashboard.yml` is the only workflow, unrelated to the main stack | A single build-test-gate-publish/deploy workflow (this phase) | This phase | First real CI for the product itself |
| `make deploy` rebuilds on the VM over SSH | CI builds once, VM only pulls + `up -d` | This phase (deploy-saas job) | Removes the "second untested build" gap; VM deploy becomes reproducible from a known SHA |
| Manual, ad-hoc image tags (`xbrain/memory-api:phase2` — phase-numbered, not semver, confirmed via Phase 16's own research grep) | SHA-tagged, registry-hosted images (`ghcr.io/<owner>/xbrain-memory-api:<sha>`) alongside the existing local dev tags (compose file itself is untouched) | This phase | Local `make build`/`make up` workflow for developers is UNCHANGED — the registry tagging is additive, applied only inside CI via `bake`'s `set:` override |
| `docker build` locally on an arm64 dev machine, manually, when a VM image needed refreshing (Phase 19's own "both-arch build" plan) | Native amd64 build on GitHub-hosted runners, no QEMU emulation needed for 17 of 18 images | This phase | Removes the slow QEMU cross-emulation path Phase 19 documented as "slow" for memory-api |
| GHCR arm64 GitHub-hosted runners: public preview (Jan 2025) | GA for public repos (Aug 2025); GA for private repos too (Jan 2026) | 2025-2026 | If the plan later wants TRUE multi-arch (matching the dev machine's arm64), the `ubuntu-24.04-arm` / `ubuntu-22.04-arm` free runner labels are now available with no QEMU emulation needed — out of scope for REL-01/02/03's stated success criteria (which only require a build exists and both profiles are tested), but worth flagging as a cheap future upgrade. `[VERIFIED: WebFetch of github.blog changelog]` |

**Deprecated/outdated:**
- QEMU-emulated cross-arch builds for CI purposes: superseded by native arm64
  GitHub-hosted runners for anything that still wants a true arm64 manifest, though
  this phase's actual requirement (produce the amd64 image prod needs) does not need
  either — `ubuntu-latest` alone suffices.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | The production GCP VM is currently stopped (cost-saving measure since 2026-06-18) | Summary, Open Questions, Pitfall 4 | Sourced from the operator's own session memory (dated, specific), not re-verified against a live `gcloud compute instances describe` call in this session. If the VM has since been restarted, the "gate the deploy job, don't force a live run" recommendation is still safe (it degrades gracefully either way) but the "residual" framing in the plan should be re-confirmed against current VM state before execution. |
| A2 | `docker compose config` service/profile counts (10 core / 33 full / 18 `build:`) are stable and will not have drifted between this research session and plan execution | Summary, Architecture Patterns, Pitfall 2 | Low risk — these are read directly from `infrastructure/docker-compose.yml` via a live `docker compose config` run in this session, not recalled; only wrong if the compose file changes before the plan executes (re-run the same command to reconfirm). |
| A3 | The 14 GB disk budget on GitHub's free `ubuntu-latest` runner is tight enough to require `docker system prune` between build and full-profile-test steps | Pitfall 2 | Medium risk if wrong in the OPTIMISTIC direction (i.e., if it turns out to fit comfortably) — the plan would add an unnecessary prune step, harmless. Risk if wrong in the PESSIMISTIC direction (doesn't fit even with pruning) is a genuine blocker requiring either a paid larger runner or scoping the full-profile test down (e.g., skip Langfuse+ClickHouse+Neo4j's own functional depth, boot+health only) — flagged as an Open Question below since it was not empirically tested in this session (would require an actual GitHub Actions run, not just local `docker compose config`). |

**If this table is empty:** N/A — see rows above. All other claims in this research
carry `[VERIFIED: ...]` or `[CITED: ...]` tags at their point of use (repo reads,
live `docker compose config` runs, `gh api` version checks, and WebFetch/WebSearch
citations for external facts).

## Open Questions

1. **Will the full-profile (33-service) live boot actually fit in 14 GB of runner disk?**
   - What we know: `ubuntu-latest` public-repo runners are 4 vCPU/16 GB RAM/14 GB SSD
     (verified via GitHub's own docs this session). The full profile includes
     ClickHouse, Neo4j, 3 Mongo/Meili-backed LibreChat services, and pulls/builds 18
     images total.
   - What's unclear: No live GitHub Actions run was performed in this research
     session (that requires an actual push to a real workflow) — this is inference
     from known image sizes and the VM sizing precedent in CLAUDE.md, not a measured
     fact.
   - Recommendation: The plan's first executed wave should include a throwaway
     `workflow_dispatch`-triggered dry run of just the `build` + `test-full-profile`
     jobs (no publish/deploy) to empirically confirm disk headroom BEFORE wiring the
     full pipeline with gated publish/deploy. If it doesn't fit, scope
     `test-full-profile` to boot+healthcheck only (not a full functional walk) or
     prune the `integrations` set tested (e.g., defer Langfuse's own UI reachability
     to a separate, non-blocking job).

2. **Is the production VM actually still stopped, and if restarted, what is its current `.env` drift risk?**
   - What we know: Operator memory (dated 2026-06-18) says the VM was terminated to
     cut cost from ~50 to ~9 EUR/month during a product pivot. STATE.md's own commit
     history does not independently confirm a restart since.
   - What's unclear: Whether the VM has been restarted at any point between then and
     now, and if so, whether its `.env` still matches what a `deploy-saas` job would
     assume (image tags, `EDITION=saas`, `COMPOSE_PROFILES` values).
   - Recommendation: Before the plan attempts any real (non-dry-run) invocation of
     `deploy-saas`, have the operator confirm current VM state out-of-band
     (`gcloud compute instances describe xbrain-phase1` or equivalent) — this is
     explicitly an operator action, not something CI can safely infer.

3. **Should the OSS release be tagged with a semver scheme, or is `:<sha>` sufficient for v2.0?**
   - What we know: Local dev images use phase-numbered tags today
     (`xbrain/memory-api:phase2`) — confirmed via Phase 16's own research grep, not
     semver. `README.md`'s "Status" section and the `.planning/` traceability tables
     track milestones as v1.0/v2.0, not per-commit semver.
   - What's unclear: Whether the "published OSS release" REL-02 wants should carry a
     human-facing version tag (`v2.0.0`, `latest`) in addition to the SHA, for
     self-hosters who want a stable "give me the current release" pull target rather
     than hunting for a specific commit SHA.
   - Recommendation: At minimum, also push/retag `:latest` alongside `:<sha>` when
     `publish-oss-release` runs — cheap, and matches how most self-hostable OSS
     projects (Immich, Nextcloud) let operators track "current stable" without
     memorizing a SHA. A full semver scheme can be deferred as a v2.1+ decision.

4. **Does the repo's LICENSE file (currently MIT) match the "AGPLv3 + CLA" locked decision recorded in REQUIREMENTS.md?**
   - What we know: `.planning/REQUIREMENTS.md`'s Milestone v2.0 header states "Code
     license = AGPLv3 + CLA" as a 2026-07-11 locked decision. `LICENSE` at the repo
     root, read directly in this session, is still the plain MIT template
     (Copyright GrooveOS).
   - What's unclear: Whether this is a stale decision that was reversed later, or a
     genuinely un-executed follow-up from the model-shift decision.
   - Recommendation: This is NOT a Phase 17 CI mechanic, but Phase 17's
     `publish-oss-release` job will literally bundle whatever `LICENSE` file exists
     into the public release — flag this discrepancy to the operator now so the
     correct license ships, rather than automating the distribution of a
     possibly-wrong license file.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Docker Engine + Compose v2 | Local dry-run of `docker compose config`, image builds | Partial | Docker 29.6.1 / Compose v5.2.0 installed; daemon was NOT running during this research session (`npipe` connection error) — `docker compose config` still works (pure parse, no daemon needed) but `docker compose up`/build require Docker Desktop running | Start Docker Desktop before any local dry-run of the build/boot steps |
| GitHub-hosted `ubuntu-latest` runner (CI) | The entire pipeline | Not locally testable | 4 vCPU / 16 GB RAM / 14 GB SSD, amd64, free-unlimited on this public repo | N/A — this IS the target environment, nothing to fall back to |
| `actionlint` | Workflow YAML validation | Yes (downloaded + verified this session) | v1.7.12 | Plain `python -c "import yaml; yaml.safe_load(...)"` syntax check (weaker — catches YAML errors, not GHA-expression/context errors) |
| `gh` CLI | Verifying action versions, later `gh release create` | Yes | v2.78.0 | N/A |
| `python` | Migration test authoring, YAML fallback check | Yes (as `python`, NOT `python3` on this Windows/Git-Bash host) | 3.13.7 | N/A |
| `node`/`npm` | Chrome extension packaging (`zip` step), no build tooling exists today | Yes | node v24.15.0 / npm 11.6.0 | N/A — extension has no package.json/build step; packaging is a plain `zip -r` |
| Go toolchain | NOT needed — actionlint ships as a precompiled binary via its official download script | Not installed | — | The official `download-actionlint.bash` script (proven working this session) obviates any need for `go install` |
| GHCR credentials in CI | `docker/login-action` | Not yet configured | — | None needed as a NEW secret — `secrets.GITHUB_TOKEN` (built-in) suffices once `permissions: packages: write` is declared |
| VM SSH secrets (`VM_SSH_HOST`/`VM_SSH_USER`/`VM_SSH_KEY` or similar) | `deploy-saas` job | Not yet configured as GitHub Secrets | — | Must be added as new repo secrets before `deploy-saas` can run for real; the job itself can be authored and actionlint-validated without them |

**Missing dependencies with no fallback:**
- A live GitHub Actions run (this session cannot push to `main` or trigger a workflow
  — the pipeline's actual execution is inherently only verifiable by a real run).
- GHCR + VM SSH secrets — must be provisioned by the operator before `deploy-saas`
  can execute for real (though the job can be fully authored and lint-checked without
  them).

**Missing dependencies with fallback:**
- Docker daemon not running locally this session — does not block the RESEARCH (all
  claims above were verified via `docker compose config`, which needs no daemon, plus
  direct `gh api` calls) but DOES block a local dry-run of `docker compose up`/builds;
  starting Docker Desktop resolves this trivially before plan execution.

## Validation Architecture

Skipped — `.planning/config.json` sets `workflow.nyquist_validation: false` explicitly.
See `## Validation Approach` below for the phase-specific equivalent the phase brief
requested directly.

## Validation Approach

This phase's own research question 6 requires being explicit about the honest
boundary. Two tiers:

### What a plan's acceptance CAN really assert locally, in this session's environment, right now

| Check | Command | Verified this session? |
|-------|---------|------------------------|
| Workflow YAML is syntactically + semantically valid GHA (bad expressions, unknown contexts, shellcheck issues in `run:` blocks) | `actionlint .github/workflows/ci-lockstep.yml` | YES — ran against the existing `deploy-dashboard.yml`, found 2 real (pre-existing, minor) issues, confirming the tool works end-to-end without network access to GitHub itself |
| The `needs:` graph structurally enforces lockstep (SC3) | Read the YAML `needs:` keys; no live run required to prove this is a STRUCTURAL guarantee — it's a GitHub Actions platform semantic, not application logic that could have a bug | YES, by design — this is the core reason the phase brief asks for `needs:`-based gating rather than `if:` conditionals: it needs no runtime proof, only correct YAML |
| The OSS-core (10 services) vs. full-profile (33 services) service partition is exactly what the plan assumes | `docker compose -f infrastructure/docker-compose.yml config --services` and `--profiles`, with/without `COMPOSE_PROFILES` set | YES — ran in this session, confirmed 10 / 33 exactly, no daemon required |
| Migration validation is edition-agnostic | `pytest apps/memory-api/tests/test_migration_editions.py` (new file) using `testcontainers.postgres` | NOT run this session (Docker daemon was down) but the EXACT pattern it extends (`conftest.py::pg_url` + `test_migration_0019.py`) already exists and is presumably exercised by the project's own existing test suite; starting Docker Desktop and running `pytest -m integration` would confirm this trivially, no GitHub Actions run needed |
| The OSS-subset test job's content is correct | `bash infrastructure/scripts/verify-phase16.sh` locally (needs Docker running) | NOT run this session (daemon down) but this is a PRE-EXISTING, already-passing gate from Phase 16 — reusing it is lower-risk than writing something new |

### What genuinely needs a real GitHub Actions run (documented residual, not claimed complete)

| Check | Why it can't be verified locally/now |
|-------|----------------------------------|
| The `build` job actually builds all 18 images successfully on a real `ubuntu-latest` runner within time/resource limits | Requires GitHub's actual hosted infrastructure — local Docker (even if the daemon were running) is arm64 (this dev machine), so a local build would not prove the amd64 path CI is specifically for |
| The full-profile (33-service) boot fits in the runner's 14 GB disk budget | Flagged as Open Question 1 — inference from known specs, not measured; only a real run measures it |
| GHCR push + first-push visibility flip actually works end-to-end | Requires real `GITHUB_TOKEN` permissions in a real Actions context, and the one-time manual visibility-to-Public step per package (Pitfall 1) — cannot be simulated by any local command |
| `deploy-saas` actually reaches and updates the VM | The VM's current live/stopped state was not independently re-verified this session (Assumption A1) — even if the job is perfectly authored, its real execution is explicitly deferred (gated behind `vars.SAAS_DEPLOY_ENABLED`) until the operator confirms the VM is up and its `.env` current |

**The plan must not claim a green, live CI run for this phase.** The honest
acceptance bar is: (1) the workflow YAML passes `actionlint` with zero findings
introduced by this phase's own new content, (2) the `needs:` graph is structurally
correct by inspection, (3) `test_migration_editions.py` passes locally against a real
testcontainers Postgres for both `EDITION` values, (4) `verify-phase16.sh` still
passes locally as the oss-subset content, and (5) a documented, explicit residual
entry records that the first real push-triggered run (build success, full-profile
disk fit, GHCR visibility flip, and the gated `deploy-saas` activation) is pending
operator action / a live run outside this session's reach — mirroring exactly how
Phase 16 handled its own "build-on-VM, amd64-only" residual.

## Security Domain

### Applicable ASVS categories

This phase adds no user-facing authentication/authorization surface — it is CI/CD
infrastructure. Most ASVS web-app categories (V2 Authentication, V3 Session
Management, V4 Access Control) do not apply directly. The relevant analog is
supply-chain / pipeline security:

| Concern | Applies | Standard Control |
|---------|---------|-----------------|
| Secrets handling (GHCR token, VM SSH key) | yes | `secrets.GITHUB_TOKEN` for GHCR (no new secret); dedicated repo Secrets (never Variables) for `VM_SSH_KEY`; never echo secrets into logs, never pass them via `run:` string interpolation that could leak into `::debug::` output |
| Least-privilege tokens | yes | Explicit `permissions:` block per job (read-only `contents` by default; `packages: write` only on jobs that push; `contents: write` only on the release job) — never a blanket `permissions: write-all` |
| Third-party Action supply chain | yes | Pin every third-party action to an exact release tag (as done throughout this research, e.g. `docker/build-push-action@v7.3.0`), not a floating `@main`/`@master`; first-party `docker/*` and `actions/*` actions are lower risk than smaller community actions (`appleboy/ssh-action`, `softprops/action-gh-release`) — for the latter, consider pinning to a commit SHA in addition to the tag if the plan wants maximum supply-chain hardening |
| Untrusted PR execution (fork PRs stealing secrets) | yes (future-proofing) | This workflow triggers on `push: branches: [main]` only (no `pull_request_target`), so fork PRs never see repo secrets — do not later add a `pull_request` trigger with `secrets:` access without switching to the documented `pull_request_target` + explicit checkout-of-base-ref pattern |
| Registry image integrity | partial | GHCR images are tagged by immutable commit SHA — retagging `:latest` on release is a pointer update, not a rebuild, so the underlying digest a self-hoster's `docker pull` resolves to is always traceable back to an exact commit |

### Known threat patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Leaked GHCR/SSH credentials via workflow log output | Information Disclosure | GitHub Actions automatically redacts registered secret VALUES from logs; never construct secret strings via concatenation in a way that bypasses this (e.g., never `echo "$SECRET" \| base64` for debugging) |
| A compromised third-party Action (supply-chain attack via a popular action's maintainer account) | Tampering | Pin to exact version tags (done throughout this research) and consider Dependabot/Renovate for controlled, reviewed action-version bumps rather than auto-tracking `@v1` floating tags |
| SSH key exposure enabling unauthorized VM access | Elevation of Privilege | Store the deploy key as a GitHub Secret (never in `.env.example` or committed anywhere — the project's existing `SSH_KEY ?= ~/.ssh/xbrain_key` Makefile default is a LOCAL developer convenience only, never the CI credential); restrict the VM-side authorized_keys entry to the minimum command set if feasible |

## Sources

### Primary (HIGH confidence)
- Direct file reads (this session): `CLAUDE.md`, `.planning/ROADMAP.md` (Phase 15/16/17/20 sections), `.planning/REQUIREMENTS.md`, `.planning/features/open-core-edition-design.md`, `.planning/config.json`, `.github/workflows/deploy-dashboard.yml`, `.github/workflow-templates/deploy-cloudrun.yml`, `.github/workflow-templates/deploy-firebase.yml`, `Makefile`, `infrastructure/docker-compose.yml` (full, both halves), `apps/memory-api/Dockerfile`, `apps/memory-api/pyproject.toml`, `apps/memory-api/alembic/env.py`, `apps/memory-api/alembic/versions/0024_local_credentials.py`, `apps/memory-api/tests/conftest.py`, `apps/memory-api/tests/test_migration_0019.py`, `apps/memory-api/tests/test_edition_gating.py`, `infrastructure/scripts/verify-phase16.sh`, `infrastructure/scripts/verify-phase15.sh`, `infrastructure/scripts/preflight-env.sh`, `docs/gitops-setup.md`, `docs/INSTALL.md`, `README.md`, `LICENSE`, `.planning/phases/15-edition-mechanics/15-RESEARCH.md`, `.planning/phases/16-oss-light-packaging/16-RESEARCH.md`, `.planning/phases/16-oss-light-packaging/16-03-PLAN.md`, `.planning/STATE.md`.
- Live command execution (this session): `docker compose -f infrastructure/docker-compose.yml config --services`/`--profiles` (with and without `COMPOSE_PROFILES`, confirming 10 core / 33 full / no daemon required); `gh api repos/<owner>/<repo>/releases/latest` for `actions/checkout`, `docker/setup-buildx-action`, `docker/login-action`, `docker/build-push-action`, `docker/metadata-action`, `docker/bake-action`, `actions/upload-artifact`, `actions/download-artifact`, `google-github-actions/auth`, `appleboy/ssh-action`, `rhysd/actionlint`, `reviewdog/action-actionlint`, `softprops/action-gh-release`, `actions/setup-python`, `actions/setup-node`; downloading and RUNNING the actionlint v1.7.12 binary against `.github/workflows/deploy-dashboard.yml` (found 2 real issues, proving the tool works); `python -c "import yaml"` availability check; repo-wide `grep`/`Grep` for `ghcr.io`, `build:`, `profiles:`, `chrome-extension.zip`, `EDITION`, `testcontainers`, existing CI workflow content (none beyond `deploy-dashboard.yml`).

### Secondary (MEDIUM confidence)
- WebFetch: `github.blog` changelog for arm64 hosted runner GA + exact runner labels (`ubuntu-24.04-arm`, `ubuntu-22.04-arm`); `docs.github.com/en/actions/reference/runners/github-hosted-runners` for exact `ubuntu-latest` spec (4 vCPU/16GB RAM/14GB SSD).
- WebSearch, cross-referenced against official docs in the same result set: GHCR default-private-on-first-push behavior; testcontainers-on-`ubuntu-latest`-needs-no-DinD-setup; build-once/reuse-across-jobs patterns (GHCR push vs. artifact tar export) for `docker/build-push-action`.

### Tertiary (LOW confidence)
- Operator session memory (`project_xbrain_vm_paused_cost.md`, dated 2026-06-18)
  regarding the production VM's stopped state — not independently re-verified against
  a live `gcloud` call in this session (see Assumption A1 / Open Question 2).

## Metadata

**Confidence breakdown:**
- Standard stack (GitHub Actions versions, GHCR, testcontainers): HIGH — every version number was checked against the live GitHub API in this session, not recalled from training data.
- Architecture (job graph, build-once-via-registry pattern): HIGH for the mechanics (these are GitHub Actions platform semantics, not opinions); MEDIUM for the specific full-profile disk-fit claim (inference, not measured — see Open Question 1).
- Pitfalls: HIGH for the 4 documented here — 3 are directly sourced from this repo's own prior-phase research/comments (xbrain-backup arch gap, make-deploy rebuild behavior, established verify-script convention) and 1 (GHCR visibility) is cross-verified across multiple independent external sources.
- SaaS deploy honest boundary: MEDIUM — the VM-stopped fact is operator-reported, not independently re-verified this session; the RECOMMENDED PATTERN (gate behind a repo variable, author for real, document as residual) is HIGH confidence regardless of the VM's actual current state, since it degrades gracefully either way.

**Research date:** 2026-07-18
**Valid until:** ~30 days for the GitHub Actions version pins (this ecosystem moves at a moderate pace; re-verify via `gh api` before executing the plan if more than a few weeks have elapsed) — but the ARCHITECTURAL recommendations (build-once-via-registry, `needs:`-graph lockstep, testcontainers-based migration validation) are stable and not time-sensitive.
