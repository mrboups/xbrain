# Phase 17: CI Lockstep — Context

**Gathered:** 2026-07-18 (autonomous — scope decisions by the driver, resolving the researcher's Open Questions; no discuss-phase)
**Source:** ROADMAP Phase 17 (REL-01/02/03, SC1..SC5) + 17-RESEARCH.md.

<domain>
## Phase Boundary

Author ONE GitHub Actions pipeline that, per commit to `main`, builds images ONCE, tests BOTH the OSS-core subset AND the full profile, and only then — gated by those tests passing (`needs:`) — publishes the OSS release AND deploys the SaaS full profile. Plus a forward-only, edition-agnostic migration path validated in CI against both editions.

**IN scope:** the workflow file(s) under `.github/workflows/`; the job graph that makes lockstep STRUCTURAL (publish/deploy `needs:` all test jobs); multi-arch build → registry (GHCR) tagged by SHA; the OSS-subset test (reuse `verify-phase16.sh`) and the full-profile test; a migration-both-editions test (extend the existing testcontainer `alembic upgrade head` pattern); the SaaS deploy job authored honestly (pull-not-build, gated off while the VM is down); `actionlint` validation of the authored workflows.

**OUT of scope:** a real green end-to-end CI run on GitHub's hosted runners (needs a push + secrets + a live VM — the documented residual); flipping the project LICENSE (a user decision — see D-17-07); restarting/redeploying the stopped prod VM.
</domain>

<decisions>
## Implementation Decisions (locked by the driver)

### D-17-01 — Lockstep is STRUCTURAL via the job `needs:` graph (SC3).
One workflow, jobs roughly: `build` (once) → { `test-oss-subset`, `test-full-profile`, `test-migrations-both-editions` } (all depend on `build`) → `publish-oss-release` AND `deploy-saas` (BOTH depend on ALL three test jobs). Because publish/deploy declare `needs: [test-oss-subset, test-full-profile, test-migrations-both-editions]`, a single failing test job blocks BOTH shipping steps by construction — not by developer discipline. The `needs:` edges ARE the SC3 proof and are `actionlint`-inspectable + assertable by parsing the YAML.

### D-17-02 — Build ONCE, use many; CI is where the real amd64 build lands (REL-01).
The `build` job builds the repo's `build:` images ONCE (multi-arch via `docker/build-push-action` + buildx) and pushes them to **GHCR** (`ghcr.io/<owner>/xbrain-<svc>:${{ github.sha }}`). Downstream test/deploy jobs `pull` that SHA tag — they NEVER `--build`. `make deploy` (which rebuilds on the VM over SSH) MUST NOT be reused verbatim as the deploy step — that would silently rebuild and break "build once". CI runners are amd64, so this is where the real amd64 image (deferred from Phases 16/19) is actually produced. `xbrain-backup` stays amd64-only (its base has no arm64 manifest) — build it single-arch, document why.

### D-17-03 — "Test the full profile" ≠ boot all 33 containers (runner-disk reality).
The 33-service full profile may not fit GitHub's ~14 GB hosted-runner disk. So:
- **OSS-subset test** = `infrastructure/scripts/verify-phase16.sh` (the proven real 10-core boot + SC#3 HTTP walk) run in CI against the CI-built images.
- **Full-profile test** = the memory-api test suite run under the full/`saas` config + `docker compose config` validation of the full profile (all 33 resolve) + the migration test under saas — NOT a full 33-container boot. A `workflow_dispatch` dry-run to MEASURE whether a full boot fits the runner is a documented follow-up, not a blocking gate.

### D-17-04 — SaaS deploy job authored for real, GATED OFF while the VM is down (honest SC2).
Author a real `deploy-saas` job (SSH to the VM, `docker compose pull` the SHA-tagged images, `up -d` — NEVER `--build`), but guard it behind a repo variable (e.g. `vars.SAAS_DEPLOY_ENABLED`) that DEFAULTS OFF, because the prod VM is stopped and CI cannot SSH a stopped host. The job exists, is lint-clean, and is wired into the lockstep graph; it no-ops (or is skipped) until the operator restarts the VM, sets the variable, and provides SSH secrets. Do NOT fake a live deploy. Document the exact "to enable" steps.

### D-17-05 — REL-03 migration test: forward-only + edition-agnostic, locally verifiable (SC4/SC5).
`apps/memory-api/tests/conftest.py::pg_url` ALREADY spins a real `testcontainers.postgres.PostgresContainer` and runs `alembic upgrade head`. Extend it: a NEW test that runs `alembic upgrade head` against a fresh DB under `EDITION=oss` AND under `EDITION=saas`, asserting both reach the same head with no down-migration required (forward-only) and no edition-specific breakage (edition-agnostic). This is REAL and runs locally (testcontainers) AND in CI — it is the load-bearing, non-mocked proof for REL-03.

### D-17-06 — Tags: immutable `:${sha}` + a moving pointer.
Publish `ghcr.io/<owner>/xbrain-<svc>:${{ github.sha }}` (immutable, what deploy pulls) plus a moving `:latest` (and/or `:oss`) for humans. The deploy job pulls the SHA tag, never `:latest`, so one commit = one deployed image set.

### D-17-07 — LICENSE discrepancy is a USER decision — do NOT auto-flip. (Open Question 4)
`LICENSE` is still **MIT**, but REQUIREMENTS records a locked "**AGPLv3 + CLA**" decision. Changing a project's license is legally significant and is NOT something to resolve autonomously. Phase 17's `publish-oss-release` ships whatever `LICENSE` file exists and does NOT assert a license string anywhere. The MIT→AGPLv3+CLA reconciliation is surfaced to the user as a required follow-up BEFORE any real public OSS release; the CI is authored so that swapping the LICENSE file later needs no workflow change.

### Claude's Discretion
- One workflow file vs a couple (e.g. a reusable build workflow) — keep it as few files as cleanly possible.
- Exact GHCR image-name scheme + whether to matrix the `build:` services.
- Whether `actionlint` runs as a CI job too (self-lint) or only locally during this phase.
</decisions>

<canonical_refs>
## Canonical References — read before planning/executing
- `.github/workflows/deploy-dashboard.yml` — the existing GH Actions conventions + secrets/vars in use (GH_API_PAT, FIREBASE_TOKEN, XBRAIN_BRIDGE_JWT, project xbrain-495115).
- `infrastructure/docker-compose.yml` — the 18 `build:` services + core/integrations/saas/ops profiles (10 core / 33 full).
- `Makefile` — build / test / lint / `deploy`=env-check+preflight+sync (the VM-SSH path CI must NOT reuse verbatim); `oss-init` / `env-check` / `preflight`.
- `infrastructure/scripts/verify-phase16.sh` — the OSS-subset test CI reuses (real 10-core boot + SC#3 walk).
- `apps/memory-api/tests/conftest.py` (pg_url) + `apps/memory-api/alembic/` — the migration chain + the testcontainer `alembic upgrade head` pattern to extend for REL-03.
- `apps/memory-api/Dockerfile` — the multi-stage build incl. the Phase-19 model bake (CI builds multi-arch).
- `17-RESEARCH.md` — verified action versions, the job-graph design, the honest local-vs-runner boundary, the disk-fit open question.
</canonical_refs>

<specifics>
## The gate lesson applies — and its CI-specific honest boundary
A check that never traverses the real deployment path proves nothing. For CI the *real* path is a push + GitHub-hosted runners + secrets + a live deploy target — which cannot be fully exercised autonomously (no push, VM stopped). So the plan MUST split proof honestly:
- **Locally/really verifiable now (do these, SKIP=FAIL):** `actionlint` passes on every authored workflow; the `needs:` graph is parsed and asserted so lockstep (SC3) is proven structural; the migration-both-editions test RUNS against a real Postgres testcontainer under EDITION=oss AND saas; the OSS-subset `verify-phase16.sh` still passes; `docker compose config` resolves the full 33-service profile.
- **Documented residual (NOT claimed green):** the actual end-to-end GitHub Actions run, the GHCR publish, and the live SaaS deploy — these need a push + secrets + a restarted VM. Document the exact enable steps; do NOT fabricate a green run. Same deferral discipline as Phase-16's amd64-VM run and Phase-20's live-backend UAT.
</specifics>

<deferred>
- A real green end-to-end CI run (push + hosted runners + secrets) + live GHCR publish + live SaaS deploy — residual, documented enable-steps.
- The full-33-container-boot-fits-the-runner measurement — a `workflow_dispatch` dry-run follow-up.
- LICENSE MIT→AGPLv3+CLA reconciliation — a user decision (D-17-07).
- Restarting the stopped prod VM + confirming its `.env` currency — operator step.
</deferred>

---
*Phase: 17-ci-lockstep*
*Context gathered: 2026-07-18 (autonomous scope resolution)*
