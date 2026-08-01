---
phase: 27-pwa-and-push
plan: 04
subsystem: api
tags: [web-push, vapid, pywebpush, mentions, notifications, fire-and-forget, prune]

# Dependency graph
requires:
  - phase: 27-pwa-and-push
    provides: "27-03 — push_subscriptions table + repo (list_for_user / delete_by_endpoint / touch), the six VAPID/push config knobs, settings.vapid_is_signable"
  - phase: 22-nudge
    provides: "nudge_open + the D-22-02 consent gate the nudge push must not bypass"
  - phase: 21-agent-aliases
    provides: "mention_detector._regex_for — the cached, boundary-anchored regex the human path reuses"
provides:
  - "app/services/web_push.py — VAPID send, off-loop, fire-and-forget, prune on 404/410 only"
  - "PRUNE_STATUSES = frozenset({404, 410}) — the one source of truth for 'the mailbox is gone'"
  - "build_mention_payload / build_nudge_payload — capped preview, no credential, app-origin url"
  - "mention_detector.detect_user_mentions / user_mention_tokens — human @mentions on the agent detector's boundary rule"
  - "Exactly two push send sites in team_chat.py (an @mention and a nudge), asserted structurally"
affects: [27-05-service-worker, 27-06-pwa-client, 27-08-deploy, 27-09-gate]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Prune-on-gone, keep-on-transient: deletion of a stored endpoint requires POSITIVE evidence (404/410), never an inference from a failure"
    - "Structural (AST) assertions for product decisions: the COUNT of push call sites and the absence of secrets in log calls are parsed, not grepped"
    - "Mutation-verified wiring tests: each wiring assertion was proven to fail when the wiring is disabled"

key-files:
  created:
    - apps/memory-api/app/services/web_push.py
    - apps/memory-api/tests/test_web_push_send.py
    - apps/memory-api/tests/test_user_mention_detector.py
  modified:
    - apps/memory-api/app/services/mention_detector.py
    - apps/memory-api/app/routes/team_chat.py
    - .planning/phases/27-pwa-and-push/deferred-items.md

key-decisions:
  - "The prune matrix is asserted in BOTH directions — deleting on a 5xx would silently unsubscribe a live device because the push service had a bad minute, which is the more expensive failure"
  - "vapid_claims is rebuilt on every send because pywebpush MUTATES the dict it is given (stamping aud/exp); a shared dict would carry the first push service's audience into the second's request"
  - "The nudge push's url is the app origin and the link travels as the notification BODY — the recipient sees the destination but the tap cannot bypass the D-22-02 consent gate"
  - "Handle tokens outside [a-z0-9_-] are DROPPED, not stripped to fit: mangling 'José' into 'jos' would invent a handle matching text the person never chose"
  - "The inline mention block is wrapped in try/except — the message is already committed and published, so a notification problem must never turn a delivered message into a 500"

patterns-established:
  - "Positive-evidence deletion: a stored third-party resource is removed only on a status that means 'gone', never on a status that merely means 'failed'"
  - "Product decisions get structural tests: 'push fires on exactly two events' is a call-site count in the AST, so a third site fails a test rather than shipping"

requirements-completed: [PUSH-01]

# Metrics
duration: 40min
completed: 2026-08-01
---

# Phase 27 Plan 04: Web Push Send + Human Mention Detection Summary

**A push now fires on exactly two events — an @mention of a member and a Phase-22 nudge — through a sender that runs off the event loop, deletes an endpoint only when the push service says it is gone, and cannot fail the request that triggered it.**

## Performance

- **Duration:** ~40 min
- **Started:** 2026-08-01T10:33:00Z (local +02:00)
- **Completed:** 2026-08-01T11:13:00Z
- **Tasks:** 3 (Tasks 1 and 2 were TDD: RED then GREEN)
- **Files modified:** 6 (3 created, 3 modified)

## Accomplishments

- **The prune matrix is proven in both directions without Docker.** 404 and 410 delete the row; 429, 500, 502, 503, a transport error with no response at all, and an unrecognised exception all KEEP it. Both halves matter: the first stops a dead device costing one doomed HTTPS request per notification forever, the second stops a live device being silently unsubscribed because FCM had a bad minute.
- **`detect_user_mentions` inherits the agent detector's non-matches instead of re-deriving them.** `alice@groove.com` does not notify Ada and `@adalovelace2` does not match `adalovelace`, for exactly the same reason those are non-matches for `@agent` — the human path calls `_regex_for`, the same cached, `re.escape`'d, boundary-anchored compiler. A structural test parses `detect_user_mentions` and fails if it ever compiles its own pattern.
- **Exactly two code paths send a push**, and that is asserted by parsing `team_chat.py` and counting `send_to_user_bg` call sites in the AST. D-27-06 is a product decision, so it is defended by a test that fails when a third site appears rather than by a comment asking people not to add one.
- **The wiring tests were verified non-vacuous by mutation** (see below) — disabling the mention branch fails 3 of them, pointing the nudge notification at the teammate-supplied URL fails another.
- **43 tests added; all 43 pass here.** Full suite went from 406 to 449 passing with the same single pre-existing failure.

## Task Commits

1. **Task 1 RED: failing send/prune tests** — `76b5945` (test)
2. **Task 1 GREEN: `web_push.py`** — `907efba` (feat)
3. **Task 2 RED: failing human-mention tests** — `877e67e` (test)
4. **Task 2 GREEN: `detect_user_mentions` + `user_mention_tokens`** — `bd23b49` (feat)
5. **Task 3: wiring + 7 wiring tests** — `e169a94` (feat)

No REFACTOR commits — neither GREEN implementation needed cleanup.

## Test Results (real output)

```
$ python -m pytest tests/test_web_push_send.py tests/test_user_mention_detector.py -q
43 passed, 1 warning in 4.18s

$ python -m pytest tests/test_web_push_send.py tests/test_nudge_open_gate.py \
                   tests/test_catch_me_up_gate.py tests/test_mention_detector.py \
                   tests/test_user_mention_detector.py -q
79 passed, 4 skipped, 1 warning in 4.99s

$ python -m pytest -q          # full suite, AFTER
1 failed, 449 passed, 281 skipped, 36 warnings in 39.30s

$ python -m pytest -q          # full suite, BEFORE (baseline captured at plan start)
1 failed, 406 passed, 281 skipped, 36 warnings in 45.98s
```

The single failure is the same pre-existing `test_github_sync.py::test_sync_repo_multi_chunk_ids` logged in `deferred-items.md` by 27-03; the skip count is unchanged (281 → 281), so nothing new gate-skipped.

Acceptance greps, re-run against the final tree:

```
PRUNE_STATUSES = frozenset({404, 410})   in web_push.py     -> 1
asyncio.to_thread                        in web_push.py     -> 1
vapid_private_key=                       in web_push.py     -> 1   (the pywebpush kwarg)
xbt_ / Bearer                            in web_push.py     -> absent
def detect_user_mentions                 in mention_detector.py -> 1
def user_mention_tokens                  in mention_detector.py -> 1
_regex_for                               in mention_detector.py -> 7   (>= 3)
web_push.send_to_user_bg                 in team_chat.py    -> 2
detect_user_mentions                     in team_chat.py    -> 1
blocked_at is None                       in team_chat.py    -> 1
tests/test_mention_detector.py           -> 36 passed (unchanged, none edited)
tests/test_web_push_send.py              -> 29 tests (>= 9)
tests/test_user_mention_detector.py      -> 14 tests (>= 9)
```

## The mutation check — proving the wiring tests are not vacuous

A wiring test that drives a route through stubs can pass because the code ran and did the
right thing, or because the code never ran at all. Both mutations were applied to
`team_chat.py`, the suite re-run, and the file restored byte-for-byte:

```
# 1. Disable the mention branch:  if "@" in body.content ...  ->  if False:
3 failed, 26 passed
  FAILED test_a_message_mentioning_a_member_pushes_exactly_that_member
  FAILED test_mentioning_yourself_pushes_nobody
  FAILED test_a_blocked_member_is_never_a_push_target

# 2. Point the nudge notification at the teammate-supplied link: app_url=body.url
1 failed, 28 passed
  FAILED test_the_nudge_pushes_the_target_and_opens_the_app

# restored:
29 passed
```

The two negative tests are also written so they cannot pass by accident: the
self-mention test mentions Bob in the same sentence and asserts Bob IS pushed, and the
blocked-member test mentions a non-blocked Carol and asserts Carol IS pushed. Silence
alone would otherwise be indistinguishable from a detector that failed on the whole
message.

## Files Created/Modified

- `apps/memory-api/app/services/web_push.py` — `PRUNE_STATUSES`, `_preview`, the two payload builders, `push_is_configured`, `_host`, `_send_one`, `send_to_user`, `send_to_user_bg`
- `apps/memory-api/app/services/mention_detector.py` — `user_mention_tokens` + `detect_user_mentions` added next to the agent detector (existing functions untouched)
- `apps/memory-api/app/routes/team_chat.py` — the `web_push` import plus the two send sites
- `apps/memory-api/tests/test_web_push_send.py` — 29 tests: payload shape, the prune matrix, fan-out, the background entrypoint, the two wiring points, and two structural gates
- `apps/memory-api/tests/test_user_mention_detector.py` — 14 tests
- `.planning/phases/27-pwa-and-push/deferred-items.md` — one new observation (below)

## Decisions Made

- **Deletion requires positive evidence.** `PRUNE_STATUSES` is pinned by a test that asserts the exact set, because both edits are dangerous in opposite directions: widening it starts deleting live subscriptions on transient failures, narrowing it re-introduces the forever-retry. The docstring states which failure each one causes.
- **`vapid_claims` is rebuilt per send.** pywebpush mutates the claims dict it receives, stamping `aud` (derived from the endpoint's origin) and `exp` into it. A module-level dict would carry FCM's audience into the next request to Mozilla and get every subsequent delivery rejected — a bug that would only appear for users with two devices on different push services.
- **The nudge notification opens the app; the link is the body.** The recipient must see where they would be going (that is the nudge), but the OS must not navigate there on a tap. Sending them into the app puts them back on the existing Phase-22 consent surface, which is what D-22-02 requires.
- **Unusable handle shapes are dropped, not repaired.** `José` yields no display-name token rather than `jos`; a dotted email local (`ada.lovelace@x.com`) yields none rather than a reshaped one. An invented handle matches text the person never chose, which is a false notification with no way for them to opt out. Members reached this way keep their other identities (GitHub username, display name).
- **An `"@"` pre-check guards the member query.** Nearly every message mentions nobody, and the detector cannot match without an `@`, so the common path never pays for a `list_members_with_user_info` round-trip.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] `pywebpush` was not installed in the local test environment**

- **Found during:** Task 1, before writing any code
- **Issue:** 27-03 declared `pywebpush>=2.3,<3` in `pyproject.toml`, but this host's interpreter had never installed it, so `import pywebpush` failed and no test touching the sender could run.
- **Fix:** `python -m pip install "pywebpush>=2.3,<3"` (resolved `pywebpush 2.3.0`, `py-vapid 1.9.4`, `http-ece 1.2.1` — the exact `-any` wheels 27-03's dual-arch proof pinned). Environment only; no dependency declaration was changed.
- **Files modified:** none
- **Note:** the import in `web_push.py` is deliberately top-level and unguarded. A `try/except ImportError` fallback would turn a missing wheel in a container into push silently never working, which nobody would notice.

**2. [Rule 2 - Missing Critical] The inline mention block could 500 a message that was already sent**

- **Found during:** Task 3
- **Issue:** the plan's block runs INLINE in `post_team_message` (only the sends are `create_task`). `list_members_with_user_info` and the payload construction are ordinary awaited code executing AFTER `session.commit()` and after the Centrifugo publish. Any failure there — a DB hiccup, a member row with an unexpected shape — would raise a 500 to the sender for a message that was already stored and already delivered to everyone watching. The client would then plausibly retry and duplicate it.
- **Fix:** wrapped the block in `try/except Exception` with a `team_chat.mention_push_failed` warning. The constraint from the plan and the phase context is explicit — sending must never break the request that triggered it — and `create_task` alone does not deliver that for the inline half.
- **Files modified:** `apps/memory-api/app/routes/team_chat.py`
- **Committed in:** `e169a94`

### Intentional variations (plan pseudo-code vs. shipped)

- **`web_push.push_is_configured()` instead of `settings.PUSH_ENABLED and settings.VAPID_PRIVATE_KEY`** at both the route gate and the sender gate. One predicate, so the route and the sender cannot drift into a state where one thinks push is on and the other does not; it also keeps the private key named in only two modules (`config.py` and the one module that signs), continuing 27-03's containment pattern.
- **Loop variable named `mentioned_id`, not `uid`** — `uid` next to `user_id` and `user.id` in the same block reads as an abbreviation of whichever one you expect.
- **The log-leak test is structural, not a substring scan.** The first version asserted `"endpoint=sub.endpoint" not in source` and false-positived on the legitimate `push_repo.touch(session, endpoint=sub.endpoint)` call. It now parses the module, walks every `log.*()` call, erases the one sanctioned wrapper (`_host(...)`) and fails on any secret still named. This was fixed during GREEN, before the Task 1 implementation was committed.

**Total deviations:** 1 blocking (environment), 1 missing-critical (fixed), 3 intentional variations.
**Impact on plan:** none on scope. Deviation 2 is the only behavioural change, and it enforces a constraint the plan itself states.

## What Docker's absence leaves unproven

Docker is not running on this host, so the `integration`-marked tests gate-skip (281 skips
before and after — this plan added none). What that does and does not cost:

**Fully proven here, no database required:**
- the entire prune matrix, in both directions, including the `exc.response is None` transport case;
- the fan-out surviving one dead device mid-list, and the delivery stamps that follow;
- `send_to_user_bg` swallowing a sender error, a broken session factory, and opening no session at all when push is off;
- both payload shapes, the preview cap, and the nudge URL substitution;
- both route wiring points, mutation-verified.

**NOT proven here — for 27-08/27-09 to run:**
1. **A real encrypted delivery.** `_send_one` is the seam every test replaces, so pywebpush's actual VAPID signature and payload encryption have never executed. This needs a real keypair (minted by the operator at deploy time, per 27-03) and a real browser subscription. **A push that reaches an actual phone is the only proof this plan works** — the phase context says so, and nothing here substitutes for it.
2. **`list_for_user` → `touch` → `delete_by_endpoint` in one session against real Postgres**, and the single commit at the end of a fan-out. The repo semantics have their own Docker-gated tests from 27-03 (also skipped here); this plan adds the sequencing on top.
3. **A genuine 410 from a push service.** The prune is proven against a stubbed exception carrying a real `WebPushException` shape; that the shape matches what FCM/Mozilla actually produce is a 27-08 observation (unsubscribe a device in the browser, send to it, confirm the row disappears).

Under Docker, a skip in any of those is a failure signal, not a pass.

## Issues Encountered

**Ruff reports pre-existing findings on both touched modules** (`B008` on FastAPI
`Depends` defaults, `RUF006` on every `asyncio.create_task`, `RUF003` on a `∪` in an
existing docstring). `team_chat.py` was at 24 before this plan and is at 27 after: +2
`RUF006` for the two new `create_task` calls and +1 `RUF100` for a `# noqa: BLE001` that
matches `team_chat_agent.py`'s house style. No CI workflow runs ruff, and every added
finding is the same class the surrounding code already carries — deliberately consistent
rather than a local exception.

## Known Stubs

None. Every function is wired to real behaviour; nothing returns placeholder data. The
only replaceable seam is `_send_one`, which is the real pywebpush call in production and
is only stubbed inside tests.

## Threat Flags

None. The surface added is exactly what the plan's `<threat_model>` enumerated — no new
endpoint, no new auth path, no schema change. All seven `mitigate` dispositions are
implemented and each has a test:

| Threat | Mitigation | Test |
|--------|-----------|------|
| T-27-04-01 payload disclosure | capped preview, no credential-shaped field | `test_payloads_carry_no_credential_material`, `test_mention_payload_caps_the_preview_and_marks_the_cut` |
| T-27-04-02 notification opens a teammate URL | notification `url` is the app origin | `test_nudge_payload_points_at_the_app_not_the_supplied_url`, `test_the_nudge_pushes_the_target_and_opens_the_app` (mutation-verified) |
| T-27-04-03 dead endpoint retried forever | `PRUNE_STATUSES` deletes on 404/410 | `test_a_gone_subscription_is_deleted[404/410]`, `test_prune_statuses_are_exactly_404_and_410` |
| T-27-04-04 mention fan-out amplification | team-scoped member list, self-mention skipped, independent create_tasks | `test_mentioning_yourself_pushes_nobody`, `test_a_message_mentioning_nobody_sends_no_push` |
| T-27-04-05 blocked member as target | `blocked_at is None` filter | `test_a_blocked_member_is_never_a_push_target` |
| T-27-04-06 forged author label | label read from the authenticated sender's row | `test_a_message_mentioning_a_member_pushes_exactly_that_member` (asserts the title) |
| T-27-04-07 log leakage of subscription secrets | only `_host(endpoint)` reaches structlog | `test_the_sender_never_logs_subscription_secrets` (AST scan) |

One observation was logged to `deferred-items.md` rather than fixed: the per-member
fan-out opens one DB session per notified member, and `POST /messages` has no rate limit,
so a large-team mention briefly queues real requests behind notifications. Batching the
fan-out into one task is a structural change the plan's `key_links` and an acceptance
criterion both pin, so it belongs at the 27-09 gate, not mid-wave.

## Next Phase Readiness

Ready for **27-05/27-06** (service worker + PWA client): the payload contract is fixed and
stable — `{kind, title, body, url, tag}`, where `kind` is `"mention"` or `"nudge"`, `url`
is always a first-party app path to navigate to on click, and `tag` collapses repeat
notifications (`mention:<team-slug>` or `nudge`). The service worker's `push` handler can
be written against exactly those five keys.

Ready for **27-08** (deploy): the operator step is unchanged from 27-03 — mint the VAPID
keypair into the VM `.env`. Once `VAPID_PRIVATE_KEY`, `VAPID_PUBLIC_KEY` and a real
`VAPID_SUBJECT` are set, the send path activates with no code change; until then
`push_is_configured()` is false and every send returns `{"sent": 0, "pruned": 0,
"skipped": True}` without opening a session.

Carried to the gate (**27-09**): the three unproven items above, and the fan-out
observation.

## Self-Check: PASSED

All 3 created files and 3 modified files exist on disk with the expected content; all 5
commit hashes (`76b5945`, `907efba`, `877e67e`, `bd23b49`, `e169a94`) resolve in
`git log`. Every test count and grep count quoted above was re-run against the final tree.
No `STATE.md` or `ROADMAP.md` edit was made, and no file outside `apps/memory-api/` and
this phase's planning directory was touched.

---
*Phase: 27-pwa-and-push*
*Completed: 2026-08-01*
