---
phase: 22-push-a-link
plan: 01
subsystem: api
tags: [fastapi, centrifugo, url-safety, ssrf, rate-limit, pydantic, testcontainers, tdd]

# Dependency graph
requires:
  - phase: quick-task-260512-tcr (team chat realtime)
    provides: "team_chat.py helpers (_resolve_team_and_check_membership, _require_user_principal), centrifugo_client.publish, the granted user:<source_user_id> channel"
  - phase: 18-local-auth
    provides: "app/services/rate_limit.py in-process check_rate + _storage singleton"
provides:
  - "POST /v1/teams/{team_id}/nudge-open — same-team push-a-link endpoint that publishes an open_url event to the target's user:<source_user_id> channel"
  - "app/services/url_safety.is_safe_nudge_url — pure lexical http/https guard (no network, SSRF-safe)"
  - "config knobs NUDGE_RATE_LIMIT + NUDGE_MAX_URL_LENGTH"
  - "test_nudge_open_gate.py — real-Postgres gate (publish captured, 403/422/429 provably publish nothing)"
affects: [22-02 extension client (subscribes user channel + handles open_url), 22-03 chat UI send-link affordance]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Pure lexical URL validation (urlsplit only) — SSRF ban enforced by importing zero network clients"
    - "Server-derived Centrifugo channel keyed by a verified target's source_user_id — request body never names a channel"
    - "Ordered guard chain where every rejection returns BEFORE scheduling the fire-and-forget publish"

key-files:
  created:
    - apps/memory-api/app/services/url_safety.py
    - apps/memory-api/tests/test_url_safety.py
    - apps/memory-api/tests/test_nudge_open_gate.py
  modified:
    - apps/memory-api/app/config.py
    - apps/memory-api/app/routes/team_chat.py

key-decisions:
  - "URL safety is purely lexical (urlsplit) — no DNS/fetch/shortener-expansion, so the recipient sees the literal URL (D-22-03 SSRF ban)"
  - "The publish channel is derived server-side from the resolved target's source_user_id; a single get_membership(target, team.slug) check rejects both non-members and cross-team members"
  - "Per-sender rate limit keys on sender.source_user_id via rate_limit.check_rate (NOT enforce_rate_limit, which keys on client IP)"
  - "settings.NUDGE_RATE_LIMIT / NUDGE_MAX_URL_LENGTH are read at REQUEST time so a monkeypatch of the singleton takes effect"

patterns-established:
  - "Pattern: server-owned channel selection — untrusted body carries a target_user_id, never a channel string (T-22-01)"
  - "Pattern: guard ordering as a security invariant — sender-403 -> target-403 -> url-422 -> rate-429 -> publish-202"

requirements-completed: [NUDGE-01]

# Metrics
duration: ~18min
completed: 2026-07-19
---

# Phase 22 Plan 01: Push-a-Link Server Nudge Endpoint Summary

**`POST /v1/teams/{team_id}/nudge-open` — a same-team-only endpoint that validates sender + target membership, a pure http/https URL guard, and a per-sender rate limit, then publishes an `open_url` event to the target's `user:<source_user_id>` Centrifugo channel — proven by a real-Postgres gate that captures the publish and shows 403/422/429 publish nothing.**

## Performance

- **Duration:** ~18 min
- **Started:** 2026-07-19T04:43:00Z (approx)
- **Completed:** 2026-07-19T05:01:00Z
- **Tasks:** 2 (both TDD)
- **Files modified:** 5 (3 created, 2 modified)

## Accomplishments
- Pure, lexical `is_safe_nudge_url(url, *, max_len)` guard — accepts only well-formed http/https, rejects `javascript:`/`data:`/`file:`/`mailto:`/`ftp:`/scheme-relative/malformed/over-long/non-str, and performs ZERO network I/O (SSRF ban grep-asserted).
- `POST /v1/teams/{team_id}/nudge-open` with the load-bearing ordering: sender-membership (403) → target same-team membership (403) → URL safety (422) → per-sender rate limit (429) → fire-and-forget publish (202). Every rejection returns before the `create_task`, so a rejected request provably schedules no publish.
- Channel is server-derived: `user:<target.source_user_id>` resolved from a verified membership — the request body never names a channel (T-22-01). Event shape `{type:'open_url', url, from:{display_name,sub}, team_id, team_slug}` carries the sender identity and the literal, un-expanded URL.
- Real-Postgres gate (`test_nudge_open_gate.py`, `@pytest.mark.integration`) monkeypatch-RECORDS `centrifugo_client.publish` (membership/URL/rate-limit NOT mocked) and asserts: same-team → publish to `user:carol-sub`; cross-team (bob) and unknown UUID → 403 no publish; `javascript:`/`file:` → 422 no publish; rate-limit exhausted → 429 no publish. RUNS GREEN under Docker.

## Task Commits

Each task was committed atomically (TDD → RED test, then GREEN implementation):

1. **Task 1 (RED): failing url_safety unit table** - `6d54240` (test)
2. **Task 1 (GREEN): pure is_safe_nudge_url + config knobs** - `6f5fa60` (feat)
3. **Task 2 (RED): failing real-Postgres nudge gate** - `5e7aec0` (test)
4. **Task 2 (GREEN): nudge-open route** - `22e03c9` (feat)

_STATE.md / ROADMAP.md deliberately NOT updated (parallel-executor rule)._

## Files Created/Modified
- `apps/memory-api/app/services/url_safety.py` - Pure lexical `is_safe_nudge_url` guard; imports only `urllib.parse.urlsplit` (no network → SSRF-safe).
- `apps/memory-api/app/config.py` - Added `NUDGE_RATE_LIMIT="10/minute"` and `NUDGE_MAX_URL_LENGTH=2048` next to `LOCAL_AUTH_RATE_LIMIT` (OSS defaults, no .env required).
- `apps/memory-api/app/routes/team_chat.py` - `PostNudgeBody` model + `nudge_open` route; new imports (`settings`, `users` repo, `rate_limit`, `url_safety`).
- `apps/memory-api/tests/test_url_safety.py` - 24-case unit table (accept/bad-scheme/malformed/non-str/too-long/boundary).
- `apps/memory-api/tests/test_nudge_open_gate.py` - Real-Postgres gate capturing the publish (SKIP=FAIL under Docker).

## Verification (real output)

- `python -m pytest tests/test_url_safety.py -q` → **24 passed**.
- `python -m pytest tests/test_nudge_open_gate.py -v` → **1 passed** (real Postgres testcontainer, ~16s) — `test_nudge_open_gate PASSED`.
- `python -m pytest tests/test_nudge_open_gate.py tests/test_url_safety.py -q` → **25 passed**.
- SSRF ban: `grep -n "requests\|httpx\|socket\|urlopen\|resolve" app/services/url_safety.py` → **nothing**.
- Config knobs: `grep -Ec '^\s*NUDGE_(RATE_LIMIT|MAX_URL_LENGTH)' app/config.py` → **2**.
- Route + channel: `grep -n "nudge-open"` and `grep -n 'f"user:{target'` both match; channel keyed by `target.source_user_id` (server-resolved), not client-supplied.

## Decisions Made
None beyond the plan — all locked decisions (D-22-01 endpoint shape, D-22-03 SSRF-safe lexical URL check, D-22-04 per-sender rate limit) implemented as specified. Added a bonus `403 no-publish` assertion for an unknown target UUID (strengthens T-22-02; still within the single membership check).

## Deviations from Plan
None - plan executed exactly as written.

## Known Stubs
None. The endpoint is fully wired: real membership resolution, real URL validation, real per-sender rate limiting, real publish (captured in the gate, live in production).

## Threat Flags
None. All security surface introduced (the nudge route + URL field + channel selection + rate limit) is already enumerated in the plan's `<threat_model>` (T-22-01 … T-22-07). No new endpoints/auth paths/schema changes beyond it.

## Issues Encountered
- Worktree isolation: initial file writes targeted the shared checkout path; redirected all writes to the worktree copy. The worktree (base 357c9a1) and `main` were byte-identical for the touched files, so the earlier reads remained accurate.
- A bare `python -c "import app.main"` fails on the required `OAUTH_RESOURCE_URL` env var (only set by conftest under pytest) — cosmetic; the passing integration gate proves `app.main` + the route import cleanly and return 202.

## User Setup Required
None - OSS-friendly defaults; no `.env` entry or external service configuration required.

## Next Phase Readiness
- Server contract is live for the extension client (Plan 22-02): subscribe to `user:<source_user_id>` and handle the `open_url` event `{type, url, from, team_id, team_slug}` (consent-gated notification → `chrome.tabs.create` on click, per D-22-02) plus the recipient "Allow open-link requests" toggle (D-22-04).
- The chat UI "send link to a member" affordance (Plan 22-03) can POST to this endpoint with `{target_user_id, url}`.
- No blockers.

## TDD Gate Compliance
Both tasks followed RED → GREEN. Git log shows, per task, a `test(22-01)` commit (RED, verified failing) followed by a `feat(22-01)` commit (GREEN, verified passing). No REFACTOR was needed. RED gates were genuine: Task 1 RED failed with `ModuleNotFoundError` (module absent); Task 2 RED failed with `404 == 202` (route absent).

## Self-Check: PASSED

- Created files verified on disk: `url_safety.py`, `test_url_safety.py`, `test_nudge_open_gate.py`, `22-01-SUMMARY.md`.
- Task commits verified in git log: `6d54240`, `6f5fa60`, `5e7aec0`, `22e03c9`.

---
*Phase: 22-push-a-link*
*Completed: 2026-07-19*
