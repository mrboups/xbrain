---
phase: 17-ci-lockstep
reviewed: 2026-07-18T21:12:53Z
depth: deep
files_reviewed: 9
files_reviewed_list:
  - .github/workflows/ci-lockstep.yml
  - infrastructure/docker-compose.ci-images.yml
  - infrastructure/scripts/verify-phase17-workflow.sh
  - infrastructure/scripts/verify-phase17-full.sh
  - infrastructure/scripts/verify-phase16.sh
  - apps/memory-api/tests/test_ci_lockstep_graph.py
  - apps/memory-api/tests/test_migration_editions.py
  - Makefile
  - docs/ci-lockstep.md
findings:
  critical: 1
  warning: 2
  info: 1
  total: 4
status: issues_found
---

# Phase 17: Code Review Report

**Reviewed:** 2026-07-18T21:12:53Z
**Depth:** deep
**Files Reviewed:** 9
**Status:** issues_found

## Summary

Reviewed the full Phase 17 diff (`63bb48a..HEAD`): the `ci-lockstep.yml` pipeline, the GHCR
override compose file, both new verify scripts, the two new pytest modules, the `verify-phase16.sh`
NO_BUILD hook, the `Makefile` targets, and `docs/ci-lockstep.md`.

The bulk of this phase is unusually well-built for a never-executed pipeline: the `needs:` graph is
correctly wired and independently proven by `test_ci_lockstep_graph.py` (including two real PyYAML
traps — the `on:` → `True` boolean coercion and `deploy-saas`'s no-`run:`-steps body — both handled
correctly); the GHCR override was cross-checked against the base compose file and is byte-complete
(18/18 `build:` services, no drift, no double-prefix); the `verify-phase16.sh` NO_BUILD/EXTRA_COMPOSE
hooks are additive and provably inert when unset (diffed against the pre-Phase-17 version); secrets
handling in the deploy step (`--password-stdin`, no CLI interpolation) is correct; and there is no
fork-PR trigger.

One real functional bug was found in the code as authored: **`deploy-saas` is missing the
`packages: read` permission it needs to authenticate to GHCR**, which will make the job's own
`docker login` fail the first time it is armed — not a hypothetical, this is the same operation
`test-oss-subset` performs correctly two jobs earlier with the permission present. Two further
issues degrade the pipeline's honesty/robustness guarantees without being outright wrong today.

## Critical Issues

### CR-01 (BLOCKER): `deploy-saas` cannot authenticate to GHCR — missing `packages: read`

**File:** `.github/workflows/ci-lockstep.yml:343-350` (permissions block), pull executed at `:389,396`

**Issue:** The `deploy-saas` job declares:

```yaml
  deploy-saas:
    ...
    permissions:
      contents: read
```

and then, inside the SSH `script:`, does:

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin
...
docker compose $COMPOSE_FILES --env-file .env pull
```

`GHCR_TOKEN` is `secrets.GITHUB_TOKEN`. When a job declares its own `permissions:` block, that block
*replaces* the workflow-level default for that job — it does not merge with it — so every scope not
listed (including `packages`) is minted as `none`. `test-oss-subset`, two jobs earlier, performs the
exact same `docker login` + pull operation against GHCR and correctly declares `packages: read`
(line 121-123) for it; `deploy-saas` does not. Without `packages: read`, the `docker login` step
itself is expected to fail with `denied: denied` (GHCR validates package-scope claims on the token at
login, not only at pull), and the job aborts under `set -euo pipefail` before it ever reaches
`docker compose pull`. This is independent of the documented package-visibility residual
(`docs/ci-lockstep.md` §2) — even once the packages are flipped public, a token with zero `packages`
scope still fails GHCR's own login handshake in most tested configurations; and even if login somehow
succeeded, an unscoped token would still be denied on `docker compose pull` for a repo-owned package.

Because `deploy-saas` is currently disarmed (`SAAS_DEPLOY_ENABLED` unset), this has not surfaced as a
live failure — but it is a genuine defect in code that is "authored for real" per the job's own
header comment, and it will break the very first live deploy attempt. Note also that
`test_ci_lockstep_graph.py`'s otherwise-thorough SC3 proof checks `needs:`, pinning, rebuild markers
and the fork-PR trigger, but never asserts anything about `permissions:` blocks — so this class of
bug has no regression guard today.

**Fix:**
```yaml
  deploy-saas:
    name: "ship: deploy SaaS (gated)"
    needs: [test-oss-subset, test-full-profile, test-migrations]
    if: vars.SAAS_DEPLOY_ENABLED == 'true'
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: read   # required: the SSH script's `docker login ghcr.io` + `pull` need it
    steps:
      ...
```
Consider also adding a `test_ship_jobs_have_packages_read_if_they_touch_ghcr`-style assertion to
`test_ci_lockstep_graph.py` (grep the job body for `docker login` / `ghcr.io` and assert
`packages` is `read` or `write` in that job's `permissions:` mapping) so this class of drift is
caught structurally, the same way the rebuild-marker and pinning checks are.

## Warnings

### WR-01: `test-full-profile`'s integration-suite step has no SKIP==FAIL guard, unlike every other gate in this phase

**File:** `.github/workflows/ci-lockstep.yml:201-205`

**Issue:** Every other gate authored in Phase 17 explicitly treats a skip as a failure and says so
loudly: `test-migrations` greps its own log for `skipped` and exits 1 (`ci-lockstep.yml:242-245`),
and both `verify-phase17-workflow.sh` and `verify-phase17-full.sh` bake "SKIP=FAIL" into nearly every
check. `test-full-profile`'s step does not:

```yaml
      - name: Run the memory-api integration suite under EDITION=saas
        working-directory: apps/memory-api
        env:
          EDITION: saas
        run: pytest -m integration -q
```

`pytest -m integration -q` exits 0 as long as no test *fails* — a partially-skipped run (e.g. a
transient Docker hiccup on the runner mid-suite skipping one `@pytest.mark.integration` test via its
own internal Docker-availability check) still reports success, and this job is one of the three gates
that `publish-oss-release` and `deploy-saas` both depend on. This directly contradicts the phase's own
stated design principle ("a skip could only mean the gate was dodged") and the honesty this phase
otherwise goes out of its way to prove elsewhere.

**Fix:** Mirror the `test-migrations` job's own pattern:
```yaml
      - name: Run the memory-api integration suite under EDITION=saas
        working-directory: apps/memory-api
        env:
          EDITION: saas
        run: |
          set -o pipefail
          pytest -m integration -q -rs 2>&1 | tee full-profile-suite.log
          if grep -qiE '[0-9]+ skipped' full-profile-suite.log; then
            echo "::error::test-full-profile integration suite reported SKIPS under EDITION=saas. ubuntu-latest always has Docker, so a skip means the gate did not run."
            exit 1
          fi
```

### WR-02: `GHCR_OWNER` is used without lowercasing `github.repository_owner`

**File:** `.github/workflows/ci-lockstep.yml:52`, consumed by `infrastructure/docker-compose.ci-images.yml:47-87`

**Issue:**
```yaml
env:
  GHCR_OWNER: ${{ github.repository_owner }}
```
GitHub account/org names preserve the case they were registered with (they are only
case-*insensitive* for lookups) and `github.repository_owner` returns that exact case. Docker/OCI
image reference name components must be strictly lowercase
(`ghcr.io/<owner>/xbrain-<svc>:<tag>` is validated against `[a-z0-9]+(?:[._-][a-z0-9]+)*`). If this
repository's owner account ever has a mixed-case login, `docker/bake-action`'s push, the GHCR pull in
`test-oss-subset`, the `imagetools create` retag in `publish-oss-release`, and the VM-side pull in
`deploy-saas` all break simultaneously, with an error that will not obviously point back to this line.
Currently harmless only because the actual owner (`mrboups`) happens to already be lowercase — this is
incidental, not enforced.

**Fix:** Normalize once, at the top of the workflow, and reuse everywhere:
```yaml
env:
  GHCR_OWNER: ${{ github.repository_owner }}
```
→
```yaml
jobs:
  build:
    steps:
      - name: Lowercase the GHCR owner
        id: owner
        run: echo "owner=${GHCR_OWNER,,}" >> "$GITHUB_OUTPUT"
        env:
          GHCR_OWNER: ${{ github.repository_owner }}
```
or simpler, compute it once as a workflow-level `env:` using `github.repository_owner` piped through
`tr` in a preliminary step and referenced via `vars`/`env` from there — the key requirement is that
every job (`build`, `test-oss-subset`, `publish-oss-release`, `deploy-saas`) reads the *same*,
already-lowercased value, since `docker-compose.ci-images.yml`'s interpolation is the single source of
truth this phase is built around.

## Info

### IN-01: `PyYAML` is an undeclared, purely-transitive dependency of the new graph test

**File:** `apps/memory-api/tests/test_ci_lockstep_graph.py:35`, `apps/memory-api/pyproject.toml`

**Issue:** `test_ci_lockstep_graph.py` does `import yaml` at module scope, but `PyYAML` appears
nowhere in `pyproject.toml` (neither core `dependencies` nor the `dev` extra). It currently resolves
only because `fastembed` (a core, non-optional dependency added in Phase 19) pulls in
`huggingface_hub`, which pulls in `pyyaml` — confirmed by inspecting the resolved dependency tree.
This works today but is a silent, incidental dependency: if `fastembed` ever drops or changes that
transitive chain (e.g. swaps its model-download backend), this test starts failing at collection with
`ModuleNotFoundError: No module named 'yaml'` with no obvious connection to the actual change that
broke it. This test is not wired into the CI pipeline itself (it is a local/manual gate via
`make verify-phase17-workflow`), which is why this has not surfaced as a live failure, but it is still
worth declaring explicitly for anyone running the gate in a minimal environment.

**Fix:** Add `pyyaml` explicitly to the `dev` extra in `apps/memory-api/pyproject.toml`:
```toml
dev = [
  "pytest>=8.3",
  "pytest-asyncio>=0.25",
  "pyyaml>=6.0",
  "ruff>=0.8",
  "mypy>=1.13",
  "testcontainers[postgres,qdrant]>=4.8",
  "respx>=0.21",
]
```

---

_Reviewed: 2026-07-18T21:12:53Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
