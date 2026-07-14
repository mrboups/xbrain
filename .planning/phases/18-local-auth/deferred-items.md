# Deferred Items — Phase 18

Issues discovered during plan execution that are OUT OF SCOPE for the current task
(pre-existing, in an unrelated file) — logged, not fixed, per the executor's Scope
Boundary rule.

## From 18-02 (argon2id password hashing + rate limiter + xbt_ mint helper)

### `tests/test_github_sync.py::test_sync_repo_multi_chunk_ids` fails on `main`, unrelated to this plan

- **Discovered during:** full-suite regression run after Task 2 (`pytest tests/ -m "not integration"`).
- **Symptom:** `AssertionError` — the recomputed `uuid.uuid5(GITHUB_SYNC_NS, ...)` chunk id
  doesn't match the id the mocked `provider.upsert` received.
- **Scope:** `apps/memory-api/app/services/github_sync.py` /
  `apps/memory-api/tests/test_github_sync.py` — neither file is touched by 18-02
  (`git diff --name-only` confirms only `pyproject.toml` +
  `app/services/{password_hash,rate_limit,api_tokens}.py` +
  `tests/{test_password_hash,test_rate_limit}.py` changed).
- **Last touched:** commit `a34e7f7` ("fix(memory-api): github-sync idempotency key
  includes team_scope (team isolation)") — predates this plan entirely.
- **Action:** NOT fixed here (out of scope). Flagging for a dedicated fix outside
  Phase 18's local-auth scope.
