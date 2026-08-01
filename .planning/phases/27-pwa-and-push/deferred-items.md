# Phase 27 — deferred items

Out-of-scope discoveries logged during execution. Not fixed here (scope boundary: only
issues caused by the current task's changes are auto-fixed).

## test_github_sync.py::test_sync_repo_multi_chunk_ids — PRE-EXISTING failure

**Found during:** plan 27-03, Task 3 full-suite regression check (2026-08-01).

**Symptom:** the test recomputes each chunk's `uuid5(GITHUB_SYNC_NS,
f"{repo}:{path}:{sha}:{chunk_idx}")` and compares it to the id the sync code produced.
They disagree:

```
E   AssertionError: assert 'e8b04edb-605...-9fc177372eb8' == '4001b7dd-b8f...-14f519203d59'
E     - 4001b7dd-b8fd-5387-a0d9-14f519203d59
E     + e8b04edb-605f-5497-89ae-9fc177372eb8
```

**Why it is out of scope for 27-03:** plan 27-03 touches only the push surface. Neither
`app/services/github_sync.py` nor `tests/test_github_sync.py` appears in this plan's
changed-file set, and the test is fully deterministic (uuid5 over fixed strings, mocked
GitHub I/O — no DB, no network, no ordering dependence). It fails identically when run
alone and inside the full suite.

**Reading:** the id-derivation string in `sync_repo` has drifted from the one the test
asserts (a component was added, removed or reordered). Whichever side is wrong, the
consequence is that a re-sync of an unchanged file would write NEW vector ids instead of
overwriting the old ones — duplicate chunks accumulating in Qdrant. Worth a real look,
not a test edit to match the code.

**Baseline:** 1 failed, 406 passed, 281 skipped (skips are the Docker-gated integration
tests; Docker is not running on this executor host).

**Still failing after 27-04**, unchanged: 1 failed, 449 passed, 281 skipped.

## Mention fan-out opens one DB session per notified member — OBSERVATION

**Found during:** plan 27-04, Task 3 (2026-08-01). Not a defect in this plan's changed
set; the shape is exactly what 27-04 specifies (`asyncio.create_task` per mentioned
member, each `send_to_user_bg` opening its own session).

**Observation:** a message mentioning N members spawns N background tasks, each checking
out a session from a pool sized 10 + 5 overflow. A message that mentions a large team
would briefly queue real requests behind the notification fan-out. There is no rate limit
on `POST /v1/teams/{id}/messages` today, so the multiplier is not bounded by anything
except team size.

**Why it is left alone here:** the plan's `key_links` pins the per-member create_task
shape and an acceptance criterion counts the call sites, so batching the fan-out into one
task is a structural change, not a fix. It is also cheap to do later — `send_to_user_bg`
would take a list of user_ids and loop inside one session.

**Where it belongs:** a chat-post rate limit (the real bound), or a batched sender.
Worth raising at the 27-09 gate rather than mid-wave.

## The memory-api runtime image cannot host its own test suite — OBSERVATION

**Found during:** plan 27-08, Task 3 (2026-08-01), while implementing gate check (j).

**Fact:** `apps/memory-api/Dockerfile`'s `runtime` stage COPYs `app/` and `alembic/` but
not `tests/`, and `pip install --target=/build/deps -e .` installs the project's runtime
dependencies only — `pytest` lives in `[project.optional-dependencies].dev`. So
`docker compose exec -T memory-api python -m pytest tests/...` fails twice over: no test
tree, no runner.

**Consequence for the gate:** check (j) probes for a test-capable image and uses one when
present, and otherwise runs the same four files against the checkout, printing which
runner it used. That is not a hole — check (g) proves the deployment's OWN dependency set
by driving real pywebpush inside the real container, and no checkout can satisfy it.

**Where it belongs:** a `test` stage in `apps/memory-api/Dockerfile` (installing `[dev]`
and COPYing `tests/`), mirroring what `apps/hocuspocus` already does for the Phase 26
gate (`docker build --target gate`). Then check (j)'s container branch lights up on its
own with no edit to the gate. Out of scope for 27-08, whose changed-file set is the gate
and its two probes.
