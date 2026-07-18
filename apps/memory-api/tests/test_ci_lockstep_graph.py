"""Structural proof that .github/workflows/ci-lockstep.yml enforces SC3 lockstep.

WHY THIS FILE EXISTS (D-17-01)
    "publish and deploy only ship what the tests passed" is either a property of the
    `needs:` graph or it is a promise in a comment. Comments do not fail builds. This
    module PARSES the workflow and asserts the graph itself, so weakening the lockstep
    breaks a test instead of quietly shipping an untested edition.

    It is deliberately a pure YAML parse: no Docker, no network, no fixtures. That is
    what makes SKIP == FAIL honest here — these assertions CAN always run, so a skip
    could only ever mean the gate was dodged.

TWO SILENT-PASS TRAPS THIS FILE HANDLES (both verified against the real file)
    1. `on:` is NOT the string "on" after parsing. PyYAML implements YAML 1.1, where
       bare `on` is a BOOLEAN, so the trigger block lands under the key `True`.
       `wf.get("on")` returns None, and a no-pull_request check written against it
       passes while asserting nothing. `_triggers()` handles both spellings and raises
       if neither is present.
    2. `deploy-saas` contains no `run:` steps — its whole body is the `script:` input of
       appleboy/ssh-action. A rebuild check that only scans `run:` strings would inspect
       an empty list and pass vacuously. `_job_text()` serializes the ENTIRE job instead.

The lockstep checks are factored into pure helpers over the parsed `jobs` mapping so that
`test_assertions_detect_a_removed_edge` can feed them a deliberately BROKEN graph and
prove the assertions actually bite.
"""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

# apps/memory-api/tests/this_file.py -> apps/memory-api/tests -> apps/memory-api -> apps -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci-lockstep.yml"

BUILD_JOB = "build"
TEST_JOBS = frozenset({"test-oss-subset", "test-full-profile", "test-migrations"})
SHIP_JOBS = frozenset({"publish-oss-release", "deploy-saas"})

# A pinned action is `@vN...` or a full 40-char commit SHA. `@main` / `@master` / a bare
# branch means the action's contents can change under us between two runs of the same commit.
PINNED_REF = re.compile(r"@(?:v\d[\w.\-+]*|[0-9a-fA-F]{40})$")

# What a "this job rebuilt the images" defect looks like in practice (D-17-02): a compose
# build flag, or reuse of the make target whose remote `up -d` rebuilds ON the VM.
REBUILD_MARKERS = ("--build", "make deploy")


# === Loading + graph helpers (pure functions over the parsed mapping) ===


def _load_workflow() -> dict:
    assert WORKFLOW_PATH.is_file(), f"workflow not found at {WORKFLOW_PATH}"
    with WORKFLOW_PATH.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict), f"{WORKFLOW_PATH} did not parse into a mapping"
    return data


def _jobs(workflow: dict) -> dict:
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict) and jobs, "workflow has no `jobs:` mapping"
    return jobs


def _triggers(workflow: dict) -> dict:
    """Return the `on:` block, tolerating PyYAML's YAML-1.1 `on` -> True coercion."""
    for key in ("on", True):
        if key in workflow:
            block = workflow[key]
            # `on: [push]` (list) and `on: push` (str) are legal too; normalize to a mapping.
            if isinstance(block, dict):
                return block
            if isinstance(block, str):
                return {block: None}
            if isinstance(block, list):
                return dict.fromkeys(block)
    raise AssertionError(
        "no trigger block found under either 'on' or True — the `on:` key is missing, "
        "or PyYAML changed its YAML-1.1 boolean coercion and this helper needs updating"
    )


def _needs(job: dict) -> set[str]:
    """`needs:` may be absent, a bare string, or a list. Normalize all three to a set."""
    raw = job.get("needs")
    if raw is None:
        return set()
    if isinstance(raw, str):
        return {raw}
    return set(raw)


def _ancestors(jobs: dict, name: str) -> set[str]:
    """Every job `name` transitively depends on. Raises on a cycle (which GitHub rejects)."""
    seen: set[str] = set()
    stack = [(name, (name,))]
    while stack:
        current, path = stack.pop()
        for parent in _needs(jobs.get(current, {})):
            if parent in path:
                raise AssertionError(f"cycle in the needs: graph: {' -> '.join((*path, parent))}")
            if parent not in seen:
                seen.add(parent)
                stack.append((parent, (*path, parent)))
    return seen


def _missing_test_gates(jobs: dict, ship_job: str) -> set[str]:
    """Which of the three test jobs do NOT gate `ship_job`, transitively. Empty == locked."""
    return set(TEST_JOBS) - _ancestors(jobs, ship_job)


def _job_text(job: dict) -> str:
    """Serialize a whole job back to YAML.

    Scans every executable string a job can carry — `run:` blocks AND action inputs such
    as ssh-action's `script:` — instead of only `run:`. Comments are already gone (they do
    not survive the parse), so a match here is real content, never documentation.
    """
    return yaml.safe_dump(job, default_flow_style=False)


def _job_uses(job: dict) -> list[str]:
    return [
        step["uses"]
        for step in job.get("steps", []) or []
        if isinstance(step, dict) and "uses" in step
    ]


def _uses_refs(jobs: dict) -> list[tuple[str, str]]:
    return [
        (name, step["uses"])
        for name, job in jobs.items()
        for step in job.get("steps", []) or []
        if isinstance(step, dict) and "uses" in step
    ]


@pytest.fixture(scope="module")
def workflow() -> dict:
    return _load_workflow()


@pytest.fixture(scope="module")
def jobs(workflow: dict) -> dict:
    return _jobs(workflow)


# === The assertions ===


def test_expected_jobs_exist(jobs: dict) -> None:
    expected = {BUILD_JOB} | TEST_JOBS | SHIP_JOBS
    missing = expected - set(jobs)
    assert not missing, f"ci-lockstep.yml is missing job(s): {sorted(missing)}"


def test_build_is_single_and_unblocked(jobs: dict) -> None:
    """One build job, waiting on nothing — the 'build once' half of D-17-02."""
    assert BUILD_JOB in jobs, "no `build` job"
    assert not _needs(jobs[BUILD_JOB]), (
        f"`build` declares needs={sorted(_needs(jobs[BUILD_JOB]))}; it must start immediately "
        "so there is exactly one image-producing step at the head of the graph"
    )
    builders = [n for n, j in jobs.items() if any("bake" in u for u in _job_uses(j))]
    assert builders == [BUILD_JOB], (
        f"expected exactly one image-building job (`build`), found {builders} — "
        "a second builder would mean the tested images are not the published ones"
    )


def test_three_test_jobs_exist(jobs: dict) -> None:
    missing = TEST_JOBS - set(jobs)
    assert not missing, f"missing test job(s): {sorted(missing)}"


def test_image_consumers_depend_on_build(jobs: dict) -> None:
    """Jobs that test the built images must sit downstream of `build`."""
    for name in ("test-oss-subset", "test-full-profile"):
        assert BUILD_JOB in _ancestors(jobs, name), (
            f"`{name}` does not depend on `build`, so it would run against stale or absent images"
        )


def test_publish_needs_all_three_tests(jobs: dict) -> None:
    """SC3, half 1: no OSS release without all three gates green."""
    missing = _missing_test_gates(jobs, "publish-oss-release")
    assert not missing, (
        f"`publish-oss-release` is NOT gated by {sorted(missing)} — an untested commit "
        "could be published to GHCR and the Releases page"
    )


def test_deploy_needs_all_three_tests(jobs: dict) -> None:
    """SC3, half 2: no SaaS deploy without all three gates green."""
    missing = _missing_test_gates(jobs, "deploy-saas")
    assert not missing, (
        f"`deploy-saas` is NOT gated by {sorted(missing)} — an untested commit could reach prod"
    )


def test_lockstep_is_direct_not_merely_transitive(jobs: dict) -> None:
    """Both ship jobs name all three test jobs in their own `needs:`.

    Transitive gating is the real requirement, but the authored shape is direct edges.
    Asserting it separately means an accidental refactor that inserts an intermediate job
    surfaces as a failure to review rather than passing silently.
    """
    for ship in sorted(SHIP_JOBS):
        missing = TEST_JOBS - _needs(jobs[ship])
        assert not missing, (
            f"`{ship}` does not directly declare needs: {sorted(missing)} "
            f"(declared: {sorted(_needs(jobs[ship]))})"
        )


def test_needs_graph_is_acyclic(jobs: dict) -> None:
    """A cycle makes the closure meaningless (and GitHub refuses to run the workflow)."""
    for name in jobs:
        _ancestors(jobs, name)  # raises on a cycle


def test_every_needs_target_exists(jobs: dict) -> None:
    """A typo'd `needs:` name is a hard GitHub error, so it must never reach main."""
    for name, job in jobs.items():
        for dep in _needs(job):
            assert dep in jobs, f"`{name}` needs unknown job `{dep}`"


def test_deploy_is_gated_off(jobs: dict) -> None:
    """D-17-04: the deploy is armed by a repo variable, not by merging."""
    condition = jobs["deploy-saas"].get("if")
    assert condition, "`deploy-saas` has no `if:` — it would attempt a deploy on every push"
    assert "SAAS_DEPLOY_ENABLED" in str(condition), (
        f"`deploy-saas` if-condition is {condition!r}; it must be gated on SAAS_DEPLOY_ENABLED"
    )


def test_publish_is_not_variable_gated(jobs: dict) -> None:
    """The OSS release must not be silently disarmable the way the deploy is."""
    condition = jobs["publish-oss-release"].get("if")
    assert not condition or "SAAS_DEPLOY_ENABLED" not in str(condition), (
        "`publish-oss-release` is gated on the SaaS deploy switch — disarming the deploy "
        "would also silently stop publishing the OSS release"
    )


def test_deploy_never_rebuilds(jobs: dict) -> None:
    """D-17-02: deploy PULLS the tested images; a rebuild would ship something untested."""
    body = _job_text(jobs["deploy-saas"])
    found = [marker for marker in REBUILD_MARKERS if marker in body]
    assert not found, (
        f"`deploy-saas` contains rebuild marker(s) {found} — it must pull the SHA-tagged "
        "images built once upstream, never rebuild them on the VM"
    )


def test_ship_jobs_never_rebuild(jobs: dict) -> None:
    for ship in sorted(SHIP_JOBS):
        body = _job_text(jobs[ship])
        found = [marker for marker in REBUILD_MARKERS if marker in body]
        assert not found, f"`{ship}` contains rebuild marker(s) {found} (D-17-02)"


def test_all_actions_pinned(jobs: dict) -> None:
    """Supply chain: a floating `@main` can change between two runs of the same commit."""
    unpinned = [
        f"{job}: {ref}" for job, ref in _uses_refs(jobs) if not PINNED_REF.search(ref)
    ]
    assert not unpinned, (
        f"unpinned action reference(s): {unpinned} — pin to a version tag or a 40-char SHA"
    )


def test_no_pull_request_trigger(workflow: dict) -> None:
    """Fork-secret hygiene: a fork PR would run with GHCR push rights and the VM SSH key."""
    triggers = _triggers(workflow)
    forbidden = {"pull_request", "pull_request_target"} & set(triggers)
    assert not forbidden, (
        f"ci-lockstep.yml is triggered by {sorted(forbidden)} — a fork PR would then execute "
        "with access to this repo's secrets. Keep this workflow on trusted refs only."
    )
    assert "push" in triggers, f"expected a `push` trigger; found {sorted(map(str, triggers))}"


def test_assertions_detect_a_removed_edge(jobs: dict) -> None:
    """Prove the lockstep checks BITE.

    A structural test that cannot fail is decoration. This feeds the same helpers a copy of
    the real graph with one gating edge deleted and asserts they report it — so the proof
    that the assertions have teeth is itself a permanent, re-run-every-time test rather
    than a claim in a summary.
    """
    for ship in sorted(SHIP_JOBS):
        for victim in sorted(TEST_JOBS):
            broken = deepcopy(jobs)
            broken[ship]["needs"] = [n for n in _needs(broken[ship]) if n != victim]
            assert _missing_test_gates(broken, ship) == {victim}, (
                f"removing the {ship} -> {victim} edge did NOT trip the lockstep check; "
                "the assertion is not actually testing the graph"
            )
        # And the intact graph must still pass, so the check is not simply always-failing.
        assert not _missing_test_gates(deepcopy(jobs), ship)


# ---------------------------------------------------------------------------------------
# CR-01 regression guard: GHCR-touching jobs must declare the `packages` scope.
# ---------------------------------------------------------------------------------------
# Job-level `permissions:` REPLACE the workflow default rather than merging with it, so a
# job that logs into ghcr.io while declaring only `contents: read` gets a token with zero
# packages scope. `deploy-saas` shipped exactly that bug: disarmed, so it never failed —
# it would have blown up on its own `docker login` the first time it was armed. Nothing in
# the graph proof covered `permissions:`, so this class had no regression net at all.
def test_ghcr_jobs_declare_packages_scope(jobs: dict) -> None:
    offenders: list[str] = []
    for name, job in jobs.items():
        blob = yaml.safe_dump(job)  # whole job: `run:` steps AND ssh-action `script:` bodies
        if "ghcr.io" not in blob:
            continue
        perms = job.get("permissions") or {}
        if not isinstance(perms, dict) or "packages" not in perms:
            offenders.append(name)
    assert not offenders, (
        f"jobs {sorted(offenders)} reference ghcr.io but declare no `packages:` permission. "
        "Job-level permissions REPLACE the workflow default, so their GITHUB_TOKEN cannot "
        "authenticate to GHCR and `docker login`/`pull` will fail."
    )
