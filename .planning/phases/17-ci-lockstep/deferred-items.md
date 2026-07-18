# Phase 17 — Deferred / out-of-scope items

Discovered while executing Phase 17 plans, deliberately NOT fixed because they fall outside
the plan's `files_modified` scope. Logged so they are not silently lost.

## From Plan 17-03 (ci-lockstep.yml authoring)

### 1. Pre-existing actionlint findings in `.github/workflows/deploy-dashboard.yml`

Running `actionlint` across the whole `.github/workflows/` directory reports 2 findings, both
in the pre-existing dashboard workflow, none in `ci-lockstep.yml`:

```
deploy-dashboard.yml:26:27: configuration variable name "github_org" must not start with the
  GITHUB_ prefix (case insensitive) [expression]
deploy-dashboard.yml:27:28: configuration variable name "github_user" must not start with the
  GITHUB_ prefix (case insensitive) [expression]
```

GitHub silently refuses to create repo variables named `GITHUB_*`, so `vars.GITHUB_ORG` /
`vars.GITHUB_USER` can never be set and those steps always fall back to their hardcoded
defaults (`dejavudev` / `mrboups`). The workflow still works today only because the fallbacks
happen to be the intended values. Fix = rename to e.g. `vars.DASHBOARD_GITHUB_ORG`.

Out of scope: Phase 17 must not modify `deploy-dashboard.yml` (Plan 17-03 declares only
`ci-lockstep.yml` as modified). Already noted independently in 17-RESEARCH.md.

### 2. `memory-api` declares an absolute, container-only path dependency

`apps/memory-api/pyproject.toml` declares:

```
"xbrain-memory @ file:///app/packages/memory-models",  # path mounted in compose build
```

That absolute path only exists inside the Docker build context, so `pip install -e
"apps/memory-api[dev]"` fails on any host that is not the container — including CI runners and
a developer laptop. Plan 17-03 worked around it inside the workflow by mirroring the layout
(`sudo cp -r packages/memory-models /app/packages/memory-models`) before installing.

The durable fix is a packaging change in `memory-api` (a relative path dependency, or a
`[tool.uv.sources]` / editable-install entry), which is out of scope for a CI-authoring plan
and would need the Docker build re-verified. Until then, every non-container consumer must
repeat the mirror step.

## From Plan 17-04 (graph proof + residual doc)

### 3. `docs/ci-lockstep.md` is not in the `publish-oss-release` bundle

The release bundle in `ci-lockstep.yml` attaches `docs/INSTALL.md`, both compose files,
`chrome-extension.zip` and `LICENSE` — but not `docs/ci-lockstep.md`, which is where the
honest status of the pipeline lives. A self-hoster downloading a release therefore does not
get the note explaining that GHCR packages are private until an operator flips them, which is
the single most likely thing to confuse them (`docker pull` returns `unauthorized`).

Fix = add `docs/ci-lockstep.md` to that job's `files:` list (one line).

Out of scope: Plan 17-04 declares only the graph test, the workflow verify script, `Makefile`
and `docs/ci-lockstep.md` as modified — `ci-lockstep.yml` belongs to Plan 17-03. Deferred
rather than taken silently. Worth folding into whichever plan next touches the workflow.

### 4. `verify-phase16.sh` was not re-run by Plan 17-04

`docs/ci-lockstep.md` cites the 23/23 OSS-subset boot from **Plan 17-02's** run, not a fresh
one. Re-running it needs a full 10-container build+boot, and this dev machine is arm64 while
the gate's value is in the amd64 path CI exercises. The citation is attributed in the doc so
the provenance is not lost; a re-run belongs to the first real CI run, which runs it anyway
as `test-oss-subset`.
