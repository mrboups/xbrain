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
