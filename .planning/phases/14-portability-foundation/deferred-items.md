# Deferred Items — Phase 14

Out-of-scope discoveries logged during plan execution (not fixed — scope boundary rule).

## From 14-01

| Item | Found during | Detail |
|------|--------------|--------|
| `apps/memory-api/tests/test_github_sync.py::test_sync_repo_multi_chunk_ids` FAILS on the current tree | Task 4 (pytest suite gate) | Pre-existing failure, unrelated to 14-01's files (`config.py`, `main.py`, `deps.py`, `notifications.py`, `waitlist.py`, conftest additions). Confirmed via `git diff f2f719a HEAD -- apps/memory-api/tests/test_github_sync.py apps/memory-api/app/services/github_sync.py` — zero diff, neither file touched by this plan. Assertion mismatch on a `uuid5`-derived chunk id (`item.id` vs. recomputed `expected_id`) — looks like a determinism/ordering bug in `github_sync.py`'s multi-chunk id generation, not caused by Phase 14's config changes. Task 4's own acceptance gate (`pytest -q --ignore=tests/test_mention_detector.py` exits 0) is satisfied by design via the ignore for the *known* stale file (`test_mention_detector.py`, repaired in Task 5) — this second, previously-undocumented failure was not anticipated by the plan. Flagging for a future fix pass; not blocking Phase 14's PORT-01 objective (config de-hardcoding).
