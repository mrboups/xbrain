---
phase: 12-github-app-migration-public-deployment-ready-auth
plan: 12-05
subsystem: memory-api / webhooks
tags: [github-app, webhook, hmac, sha256, installations, fastapi, raw-body, pitfall-5, pitfall-8]

# Dependency graph
requires:
  - phase: 12
    provides: Installation ORM + alembic 0019_github_app_install (Plan 12-01)
  - phase: 12
    provides: GITHUB_APP_WEBHOOK_SECRET in Settings + env (Plan 12-02 scaffolding)
provides:
  - "POST /v1/webhooks/github/installation — receives installation + installation_repositories + ping events from the xbrain GitHub App"
  - "_verify_signature(body, sig_header) — pure-function HMAC-SHA256 verifier with timing-safe hmac.compare_digest + fail-closed 503 on missing secret"
  - "app.repos.installations.upsert_installation / revoke_installation / suspend_installation / unsuspend_installation / update_installation_permissions — single-file SQL surface for the installations table"
  - "18-test suite covering all 5 installation actions + signature gate (7 pure-unit always-on + 11 integration)"
affects: [12-06-user-to-server-flow, 12-07-install-ux, 12-11-verify-phase12-sanity-ping]

# Tech tracking
tech-stack:
  added: []  # No new runtime deps — uses fastapi.Request.body(), stdlib hmac/hashlib/json, structlog, sqlalchemy postgresql dialect (pg_insert.on_conflict_do_update)
  patterns:
    - "Raw body read via Request.body() BEFORE any Pydantic parsing — HMAC is over exact bytes; Pydantic re-serialization (field reordering, whitespace, escape changes) would always break verification (RESEARCH §Pitfall 5)"
    - "Timing-safe HMAC comparison via hmac.compare_digest — never use == (RESEARCH §Pitfall 8); locked by source-inspection unit test"
    - "Fail closed on misconfiguration — missing GITHUB_APP_WEBHOOK_SECRET → 503 not silent 401 so operator notices vs blending into signature-failure noise"
    - "Always return 200 on signature-verified payloads (even unhandled actions / events / non-dict payloads) to prevent GitHub's 5x exponential-backoff retry storm"
    - "pg INSERT ... ON CONFLICT (installation_id) DO UPDATE for upsert path — handles duplicate webhook delivery + races between webhook arrival and on-demand backfill (Plan 12-03) in a single SQL trip"
    - "No IP allowlist (RESEARCH §Pitfall 8) — GitHub publishes a list but it changes and breaks reliability; HMAC is sufficient + canonical guidance"
    - "Two-tier test split (per project convention): pure-unit file for security boundary that runs everywhere + integration file for full route exercised via testcontainers Postgres (auto-skip on Docker-less hosts)"

key-files:
  created:
    - apps/memory-api/app/repos/installations.py (Task 1 — commit 01c9038)
    - apps/memory-api/app/routes/webhooks_github.py (Task 2 — commit daf5a1a)
    - apps/memory-api/tests/test_phase12_webhook.py (Task 3 — commit e67705b, 11 integration tests)
    - apps/memory-api/tests/test_phase12_webhook_signature_unit.py (Task 3 — commit e67705b, 7 pure-unit tests, Rule 2 deviation)
  modified:
    - apps/memory-api/app/main.py (Task 2 — added webhooks_github import + include_router under prefix /v1/webhooks/github)

key-decisions:
  - "Used the JSONB-flag-free schema approach end-to-end — Migration 0019 already shipped with the dedicated suspended_at TIMESTAMPTZ column, so no `permissions JSONB.suspended` overload was needed (one of the three options the executor prompt listed)"
  - "Test file named test_phase12_webhook.py (matches PLAN frontmatter files_modified + commit message convention) — NOT test_phase12_webhooks_github.py (which the orchestrator prompt's success-criteria block suggested). PLAN is authoritative for filenames"
  - "Added a second test file test_phase12_webhook_signature_unit.py as a Rule 2 deviation — the integration tests in test_phase12_webhook.py auto-skip on Docker-less hosts, leaving the HMAC security gate with zero runnable coverage during local development; the pure-unit file exercises _verify_signature directly + locks hmac.compare_digest usage via source inspection (defends against a timing-attack regression that wouldn't surface until CI)"
  - "Subscriptions stay minimal v1: installation + installation_repositories + ping. installation_target (org rename / account transfer) NOT subscribed — per CONTEXT decision + RESEARCH §Q13 (defer to Phase 13)"
  - "installation_repositories.added/.removed logged only, no installations row mutation — we have no repo permissions in v1; future-proofs the subscription so we can add a real handler without re-registering the webhook"
  - "Unknown installation.action values get logged + 200'd, not 400'd — GitHub adds new actions occasionally (e.g. new_permissions_accepted was added mid-2024); 400 would trigger retry storms and never converge"
  - "Each repo helper commits its own transaction (webhooks are single-event) — keeps the route thin and pushes per-action atomicity into the SQL surface; if a future bulk-replay tool needs different transaction boundaries it can use the underlying sa.update() / pg_insert() statements directly without going through these helpers"

patterns-established:
  - "Webhook receivers MUST read raw body via await request.body() BEFORE json.loads — applies to any future webhook integration (Stripe, GitHub PR, etc.) where the sender signs the exact payload bytes"
  - "Webhook signature verifier as a standalone pure function (not a FastAPI Depends) — keeps it unit-testable without spinning the full app + fixture chain"

requirements-completed: []  # PLAN frontmatter declares no `requirements:` field — pure infrastructure plan, requirements land via 12-07 (install UX) and 12-11 (verify-phase12)

metrics:
  duration: "~8 min"
  completed: "2026-05-17T11:53:54Z"
  tasks_completed: 3
  files_modified: 1
  files_created: 4
  commits: 3
---

# Phase 12 Plan 12-05: GitHub App Webhook Handler Summary

**One-liner:** Stood up `POST /v1/webhooks/github/installation` with timing-safe X-Hub-Signature-256 HMAC verification + raw-body-before-Pydantic discipline + a clean repo surface (`upsert_installation` / `revoke_installation` / `suspend_installation` / `unsuspend_installation` / `update_installation_permissions`) handling all 5 installation actions; 11 integration tests + 7 always-on unit tests lock the security boundary.

## What shipped

### Task 1 — installations repo helpers (commit `01c9038`)

Created `apps/memory-api/app/repos/installations.py` with 5 async helpers, each owning its own commit boundary (webhooks are single-event):

| Helper | Webhook trigger | SQL action |
|--------|-----------------|------------|
| `upsert_installation(...)` | `installation.created` | `pg_insert(...).on_conflict_do_update(...)` on `installation_id` PK — also explicitly clears `revoked_at` + `suspended_at` so a re-install resurrects an old soft-deleted row cleanly |
| `revoke_installation(installation_id)` | `installation.deleted` | `UPDATE installations SET revoked_at = now() WHERE installation_id = :id` — row is NEVER hard-deleted (audit + future FK survival) |
| `suspend_installation(installation_id)` | `installation.suspend` | `UPDATE installations SET suspended_at = now() ...` |
| `unsuspend_installation(installation_id)` | `installation.unsuspend` | `UPDATE installations SET suspended_at = NULL ...` |
| `update_installation_permissions(...)` | `installation.new_permissions_accepted` | `UPDATE installations SET permissions = :perms, raw_payload = :payload ...` |

The `ON CONFLICT DO UPDATE` path doubles as the safety net for two non-webhook races:
1. Duplicate webhook delivery (GitHub retries on 5xx) — second call is a no-op update.
2. On-demand backfill from Plan 12-03's hybrid lookup landing AT THE SAME TIME as the webhook — the webhook UPSERT wins cleanly without either side raising IntegrityError.

### Task 2 — webhook route + HMAC verify (commit `daf5a1a`)

Created `apps/memory-api/app/routes/webhooks_github.py` with one POST endpoint and registered it in `main.py` under prefix `/v1/webhooks/github` (final mount: `/v1/webhooks/github/installation`).

**Request flow:**

1. `raw_body = await request.body()` — read the EXACT bytes BEFORE any JSON parsing. This is the RESEARCH §Pitfall 5 lock — FastAPI's Pydantic auto-parsing would consume the body stream and a re-serialization wouldn't match GitHub's exact JSON (field ordering, whitespace, escape differences make the HMAC always fail).
2. `_verify_signature(raw_body, request.headers.get("X-Hub-Signature-256"))` — pure-function HMAC-SHA256 verifier. Uses `hmac.compare_digest` for timing-safe comparison (RESEARCH §Pitfall 8 — direct `==` is a CVE pattern). Fails closed (503) if `GITHUB_APP_WEBHOOK_SECRET` is unset, 401 on missing header, 401 on mismatch — no body details leaked.
3. `payload = json.loads(raw_body)` — only after signature verified. Bad JSON → 400 (legitimate caller would never send malformed JSON; GitHub will retry on 5xx but won't retry on 4xx, which matches the bad-input semantic).
4. Dispatch on `X-GitHub-Event` header:
   - `installation` + action ∈ {created, deleted, suspend, unsuspend, new_permissions_accepted} → corresponding repo helper.
   - `installation` + unknown action → log + 200 (GitHub adds new actions periodically; 400 would trigger a retry storm that never converges).
   - `installation_repositories.added/.removed` → log only in v1 (we have no repo permissions yet, but staying subscribed future-proofs the install).
   - `ping` → 200 (GitHub sends one immediately when the webhook URL is configured).
   - Anything else → log + 200.
5. Return `{"ok": True, "delivery_id": ..., "event": ...}` — small diagnostic body. The `delivery_id` from `X-GitHub-Delivery` is echoed to help operators correlate retries.

**No IP allowlist** — per RESEARCH §Pitfall 8, GitHub publishes a webhook IP list but it changes and is brittle in production. HMAC is the auth + canonical GitHub guidance.

### Task 3 — Tests covering signature + all dispatches (commit `e67705b`)

Two-tier coverage matching the project's documented pattern (conftest.py lines 7-8 explicitly call out the strategy):

**`tests/test_phase12_webhook.py` — 11 integration tests** (auto-skip on Docker-less hosts via the `pg_url` testcontainer gate):
- Signature missing → 401.
- Signature invalid → 401.
- Signature invalid does NOT touch the DB (safety net asserting the verify-before-parse order survives refactors).
- `installation.created` → row INSERT with `revoked_at IS NULL`, `installed_by_github_id` from `sender.id`, permissions snapshot.
- `installation.deleted` → existing row gets `revoked_at = now()`.
- `installation.suspend` → `suspended_at = now()`; `installation.unsuspend` → `suspended_at = NULL`.
- `installation.new_permissions_accepted` → permissions JSONB refreshed.
- `installation_repositories.added` event → 200, no row inserted.
- `ping` event → 200.
- Duplicate `installation.created` delivery → idempotent (exactly one row).
- Unknown `installation.action` → 200 (no row, no retry storm).

**`tests/test_phase12_webhook_signature_unit.py` — 7 pure-unit tests** (run on every host, no Docker needed — Rule 2 deviation, see below). All PASS locally:
- Correct HMAC accepted (round-trip).
- Wrong secret rejected (401).
- Tampered body rejected (401).
- Missing header rejected (401).
- Wrong prefix (`sha256=` missing) rejected (401).
- Missing `GITHUB_APP_WEBHOOK_SECRET` → 503 (fail closed).
- Source-inspection guard: `inspect.getsource(_verify_signature)` MUST contain `hmac.compare_digest` and MUST NOT contain `expected == signature_header` — locks the timing-safe comparison invariant against a future refactor.

## Verification

```bash
# Module-level imports clean (no DB needed):
$ python -c "from app.routes.webhooks_github import router; print([r.path for r in router.routes])"
Route paths: ['/installation']
Route methods: [['POST']]

# Pure-unit tests on the security boundary:
$ python -m pytest tests/test_phase12_webhook_signature_unit.py -v
7 passed, 1 warning in 1.71s

# Integration tests (collect-only — full run requires Docker for testcontainers Postgres):
$ python -m pytest tests/test_phase12_webhook.py --collect-only -q
11 tests collected
```

The 11 integration tests run on any host with Docker (CI, the VM). They auto-skip on dev hosts where Docker isn't available — same pattern as `test_phase12_org_membership.py` and `test_migration_0019.py`.

## Deviations from Plan

### Auto-fixed / auto-added

**1. [Rule 2 — Security coverage gap] Added `tests/test_phase12_webhook_signature_unit.py` (NOT in plan's files list)**

- **Found during:** Task 3 implementation, when I noticed the plan's signature-related assertions (`test_signature_missing_returns_401` etc.) all flow through the `client` fixture which depends on `pg_url` → testcontainers Postgres → Docker.
- **Issue:** On a Docker-less dev host, the entire HMAC security boundary has zero runnable test coverage. A future refactor swapping `hmac.compare_digest` for `==` (RESEARCH §Pitfall 8 — well-known timing-attack CVE pattern) wouldn't surface until CI; worse, a typo invalidating the prefix-comparison wouldn't show up at all if the timing-attack invariant tests are also DB-dependent.
- **Fix:** Created a parallel pure-unit test file that exercises `_verify_signature` directly (no FastAPI client, no DB session needed). 7 tests covering: correct HMAC accepted; wrong secret / tampered body / missing header / wrong prefix all rejected; missing secret → 503 (fail closed); source-inspection assertion locking `hmac.compare_digest` usage.
- **Files modified:** `apps/memory-api/tests/test_phase12_webhook_signature_unit.py` (NEW)
- **Commit:** `e67705b` (same commit as Task 3's plan-spec integration tests)

### Deferred / not applicable

The "options for suspend handling" preamble in the executor prompt (JSONB flag approach vs schema change) was moot — Migration 0019 (Plan 12-01) already shipped with the dedicated `suspended_at TIMESTAMPTZ` column, so the executor's recommended JSONB-flag workaround wasn't needed. The repo helpers use the clean dedicated-column path directly.

### Naming clarification

The executor prompt's success-criteria block called the test file `test_phase12_webhooks_github.py`; the PLAN frontmatter `files_modified` list and Section 2 + 3 + commit message all say `test_phase12_webhook.py`. The PLAN is authoritative for in-repo filenames (it's checked into git and consumed by `roadmap update-plan-progress` etc.). Used `test_phase12_webhook.py`.

## Authentication gates

None. The webhook handler is a public endpoint (GitHub calls it from arbitrary IPs); auth is HMAC-only. No operator credentials needed during this plan.

## Risks remaining (deferred to other plans)

- **nginx buffering of POST bodies** — out of scope for code, validated by Plan 12-11's verify script. Default nginx config does NOT modify POST bodies; `proxy_request_buffering off;` is only needed for streaming uploads.
- **Body stream re-read by middleware** — currently no FastAPI middleware in the app calls `Request.body()`. If a future logger / observability middleware does, this route's `_verify_signature` will see an empty body and 401 every payload. Plan 12-11's verify script does an end-to-end roundtrip that would catch this.
- **Webhook secret rotation** — requires a `memory-api` restart (pydantic-settings loads once). Documented in Plan 12-11 KB.
- **GitHub App webhook subscription configuration** — operator step. Plan 12-11 UAT covers the manual check.
- **The on-demand backfill from Plan 12-03 self-heals missed webhooks** — if GitHub's 5x retry exhausts (or if memory-api was down longer than that), the first user sign-in for that org will populate the `installations` row through the hybrid lookup path. Combined with this plan's UPSERT idempotency, the convergence is guaranteed.

## Self-Check: PASSED

**Files created (verified on disk):**
- `apps/memory-api/app/repos/installations.py` — FOUND
- `apps/memory-api/app/routes/webhooks_github.py` — FOUND
- `apps/memory-api/tests/test_phase12_webhook.py` — FOUND
- `apps/memory-api/tests/test_phase12_webhook_signature_unit.py` — FOUND

**Files modified (verified on disk):**
- `apps/memory-api/app/main.py` — FOUND, import + include_router both present

**Commits (verified in git log):**
- `01c9038` feat(memory-api): installations repo helpers (upsert/revoke/suspend/perms) — FOUND
- `daf5a1a` feat(memory-api): POST /v1/webhooks/github/installation handler + HMAC verify — FOUND
- `e67705b` test(memory-api): webhook signature + dispatch coverage (11 integration + 7 unit) — FOUND
