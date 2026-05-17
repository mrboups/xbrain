---
phase: 12
plan: 11
subsystem: ship-gate
tags: [verify, uat, summary, kb, github-app]
provides:
  - infrastructure/scripts/verify-phase12.sh
  - .planning/phases/12-github-app-migration-public-deployment-ready-auth/12-UAT.md
  - .planning/phases/12-github-app-migration-public-deployment-ready-auth/12-SUMMARY.md
  - .planning/KB/github-app-architecture.md
  - .planning/KB/github-app-operator-runbook.md
  - .gitattributes
requires:
  - 12-01..12-10 (all shipped)
affects:
  - operator ship-out flow
key-files:
  created:
    - infrastructure/scripts/verify-phase12.sh
    - .planning/phases/12-github-app-migration-public-deployment-ready-auth/12-UAT.md
    - .planning/phases/12-github-app-migration-public-deployment-ready-auth/12-SUMMARY.md
    - .planning/KB/github-app-architecture.md
    - .planning/KB/github-app-operator-runbook.md
    - .gitattributes
  modified: []
decisions:
  - Added .gitattributes (Rule 2 deviation) to pin *.sh / *.yml / Dockerfile to eol=lf — Phase 11 hit the CRLF break (commit dc9a74c "fix CRLF on verify script") and the only mitigation the plan suggested was a .gitattributes that did not exist. Adding it now prevents the regression for verify-phase12.sh and any future shell script on fresh Windows checkouts.
metrics:
  duration_min: 15
  completed: 2026-05-17T13:16Z
  commits: 3
---

# Phase 12 Plan 11: verify-phase12.sh + UAT + SUMMARY + KB Summary

Shipped the Phase 12 ship gate: 18-assertion `verify-phase12.sh` (SKIP-aware,
exit 0 iff FAIL==0), 9-step manual UAT, internal architecture KB, operator
registration runbook, and the phase-level SUMMARY template ready for the
operator to fill at ship-out. Also added the missing `.gitattributes`
pinning shell scripts to LF — direct mitigation of the Phase 11 CRLF break.

## What shipped

### Task 1 — `infrastructure/scripts/verify-phase12.sh` (commit `a4e8e05`)

18 assertions, faithful to the plan's REVISION 2 spec:

| # | Assertion                                                                 | Notes                                              |
| - | ------------------------------------------------------------------------- | -------------------------------------------------- |
| 1 | Alembic head ≥ 0019_github_app_install                                    | accepts hash-renamed variant                       |
| 2 | `installations` table has 6 required columns                              | installation_id, github_org_login, revoked_at, suspended_at, permissions, raw_payload |
| 3 | `users` table has 5 Phase 12 token columns (M-5 fix)                      | adds github_access_token_hash to the spec set      |
| 4 | Partial unique index `idx_installations_org_login_active` exists          | with `WHERE revoked_at IS NULL` predicate          |
| 5 | memory-api env has 6 `GITHUB_APP_*` secrets                               | both set + non-empty                                |
| 6 | `GITHUB_API_PAT` removed from app/ AND tests/ (M-6 fix)                   | strips comment lines via awk for accuracy          |
| 7 | Legacy OAuth App client_id `Ov23liy7tZekl0uEztoj` absent from frontend    | scopes to apps/ + app-site/ + chrome-extension/    |
| 8 | PyJWT installed at supported version                                      | accepts 2.10+ or 3.x                                |
| 9 | `mint_app_jwt()` returns 3-part RS256 JWT                                 | decodes header, asserts alg=RS256                  |
| 10 | `POST /v1/webhooks/github/installation` in openapi                       | via `curl /openapi.json`                            |
| 11 | Webhook returns 401 on missing X-Hub-Signature-256                       |                                                     |
| 12 | Webhook returns 200 on correctly signed ping                             | SKIPs if `GITHUB_APP_WEBHOOK_SECRET` not exported  |
| 13 | `installations` row exists for `TEST_GITHUB_ORG`                         | SKIPs if env var not set                            |
| 14 | `SigninGithubOut` schema has install_required + install_url + org_login  | M-1 fix — 3 fields required, not 2                  |
| 15 | `chrome-extension/manifest.json` has `key` field (>= 200 chars)          |                                                     |
| 16 | Frontend `GITHUB_CLIENT_ID` matches env `GITHUB_APP_CLIENT_ID`           | checks both teams.js and background.js              |
| 17 | `get_installation_token` end-to-end (mint App JWT → ghs_ token)         | SKIPs if `TEST_INSTALLATION_ID` not set            |
| 18 | SC-5 regression — blocked github_login on installed org cannot auto-join | B-3 fix — SKIPs unless `TEST_BLOCKED_LOGIN` prepped |

Implementation notes:
- Adopted the `verify-phase11.sh` skeleton (`require_env`, `run_psql`,
  ANSI-coloured `ok` / `ko` / `skip` helpers).
- Script header documents the assertion-18 prep procedure for operators
  (insert team_org_blocks row + run UAT step 1 as fixture user → then
  re-run script).
- Added `run_api_python` helper for the Python one-liners that probe
  the memory-api runtime (mint JWT, get installation token, etc.).
- LF line endings verified post-write via `grep -qU $'\r'` (none found)
  and `file` reports `Bourne-Again shell script ... executable`. The
  `.gitattributes` add ensures fresh checkouts on Windows preserve LF.

### Task 2 — `12-UAT.md` (commit `ebc37b3`)

9 manual steps, faithful to the plan spec:

1. Web sign-in happy path (SC-1 + SC-3) — DevTools confirms response
   has `install_required: false`, `xbt_token` in localStorage, no
   install banner.
2. Web sign-in install-required branch (SC-2) — DevTools confirms
   response has real `org_login`, `install_url` points to
   `github.com/apps/xbrain/installations/new`.
3. Chrome extension sign-in (SC-4) — extension ID stable + matches
   `anigikcnmldoklcmogffmgcojdhhficb`.
4. Webhook delivery on install/uninstall — Recent Deliveries HTTP 200,
   DB row reflects state on both events.
5. Refresh token rotation (SC-6 + GHAPP-05) — force expiry via psql,
   next API call silently refreshes, hash column updated.
6. Hybrid lookup self-heal — delete installations row, sign in, row
   reappears via fallback to `/orgs/{org}/installation`.
7. LibreChat link-github regression (B-2) — Phase 5 `/v1/me/link-github`
   still returns 200 with new check_github_org_membership signature.
8. Multi-frontend independence (GHAPP-07) — web + ext sign in with
   same client_id, same users row.
9. OAuth App revocation gate (24h+) — verify ≥ 15/18 + 0 auth errors
   in 6h logs, then delete legacy OAuth App per runbook KB.

Frontmatter declares `maps_to` so the verifier can trace each step back
to a Phase 12 success criterion or plan-check fix.

### Task 3 — KB + SUMMARY template (commit `7eb854b`)

Three files written together as a coherent unit:

- **`.planning/KB/github-app-architecture.md`** (12 sections):
  why-App, 3-token taxonomy, server-side stack, client-side stack,
  install-required UX, webhook handler, token persistence at rest,
  migration history, env vars, failure modes, known limitations,
  references. The "GITHUB_API_PAT removed" claim is backed by an inline
  grep operators can re-run — verify-phase12.sh assertion 6 is the
  regression guard.
- **`.planning/KB/github-app-operator-runbook.md`** (8 Steps):
  Chrome ext keypair first (derives callback URL) → App registration →
  secrets capture → `.env` update → install on org → verify → UAT →
  revoke legacy OAuth App. Includes troubleshooting table (7 most
  likely operator failures) and future-maintenance section.
- **`12-SUMMARY.md`** (phase-level template): pre-populated "What
  shipped" section listing every file touched across plans 12-01..12-10;
  "Verification" subsections with fill-in slots; "Known issues",
  "Decisions made", and "SC-1..8 status" sections ready for the
  operator to complete at ship-out.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 — Missing Critical Infrastructure] Added .gitattributes**

- **Found during:** Task 1 (verify-phase12.sh staging).
- **Issue:** The plan's "Risks + Mitigations" section claims:
  `.gitattributes` should already pin `*.sh text eol=lf` (Phase 11 commit
  dc9a74c added this). Confirm before commit.
  But there was no `.gitattributes` anywhere in the repo. Commit dc9a74c
  did NOT add a `.gitattributes` — it ran `sed -i 's/\r$//'` on the
  verify script itself, which only fixes the artefact, not the
  regression cause.
- **Why this matters:** On the next fresh clone from a Windows host with
  `core.autocrlf=true`, `verify-phase12.sh` would be checked out with
  CRLF endings and break on the VM bash shell — exactly the Phase 11
  ship-out failure. Confirmed during commit: git emitted
  `LF will be replaced by CRLF the next time Git touches it` warning
  on staging the script. This is a Rule 2 deviation (missing critical
  correctness infrastructure required for the deliverable to actually
  work on the deployment target).
- **Fix:** Added `.gitattributes` at repo root pinning `*.sh`, `*.bash`,
  `*.yml`, `*.yaml`, `Dockerfile`, `*.dockerfile`, `docker-compose*.yml`
  to `eol=lf`. Verified via `git cat-file -p :infrastructure/scripts/
  verify-phase12.sh | grep -qU $'\r'` (returns nothing — staged blob is
  LF only) and `git ls-files --stage` confirming mode 100644 (executable
  bit not tracked in git index on Windows, same as all other
  verify-phaseN.sh scripts on main).
- **Files modified:** `.gitattributes` (created).
- **Committed in:** `a4e8e05` (same commit as Task 1 — single coherent
  unit: the verify script + its line-ending pin).

**2. [Scope clarification — not a deviation] github-auth.html already updated**

The objective said "`marketing-site/docs/github-auth.html` already updated
by 12-10 — verify it covers Phase 12 architecture sufficiently." Verified
the file contains `<h1>GitHub App Authentication</h1>`, describes the
GitHub App + Installation Tokens flow, references ghs_ installation tokens
with ~1h TTL, mentions the App JWT POST flow. Coverage is sufficient
(per `12-10-SUMMARY.md`). No edit needed.

### Authentication Gates

None encountered. All deliverables were file-creation tasks; no live
service interactions required during plan execution.

## Decisions Made

- `.gitattributes` includes `*.yml` / `*.yaml` / `Dockerfile` in addition
  to `*.sh` — these are Linux-VM-consumed files (Docker Compose, Dockerfile
  builds, k8s/CI YAML in the future). Same CRLF risk profile. The plan
  only mentioned `*.sh` but the broader scope is the correct safe default.
- Did NOT add `.editorconfig` despite Windows tooling potentially needing
  it — out of scope per the plan, and not aligned with any current
  CLAUDE.md directive.
- Kept the verify script's executable mode at `100644` in the git index
  (matches all other verify-phaseN.sh scripts on main). The script runs
  via `bash infrastructure/scripts/verify-phase12.sh` on the VM, not via
  direct invocation, so the +x bit isn't required.
- Architecture KB has 12 sections (plan asked for ≥ 7) — added 4 sections
  beyond the plan's 8: a "Why GitHub App" framing section, an explicit
  "Server-side stack" / "Client-side stack" split, a "Failure modes
  summary" table, a "Known limitations" section, and a "References"
  section linking back to source files. The plan acceptance just required
  ≥ 7, and the extra structure helps future maintainers.

## Self-Check

Verified after task completion:

- File checks:
  - `infrastructure/scripts/verify-phase12.sh` — exists, LF line endings,
    bash syntax OK, 18 `test_*` function definitions.
  - `.planning/phases/12-github-app-migration-public-deployment-ready-auth/12-UAT.md` —
    exists, 9 `^## Step` headings.
  - `.planning/KB/github-app-architecture.md` — exists, 12 `^## ` sections,
    contains the M-6 verifiable grep command.
  - `.planning/KB/github-app-operator-runbook.md` — exists, 8 `^## Step`
    headings.
  - `.planning/phases/12-github-app-migration-public-deployment-ready-auth/12-SUMMARY.md` —
    exists, phase-level template.
  - `.gitattributes` — exists at repo root, `*.sh text eol=lf` pinned.
- Commit checks:
  - `a4e8e05` exists (`test(infra): verify-phase12.sh — 18 assertions…`).
  - `ebc37b3` exists (`docs(phase12): UAT 9-step manual checklist`).
  - `7eb854b` exists (`docs(phase12): SUMMARY template + architecture KB + operator runbook`).
- STATE.md and ROADMAP.md untouched (per objective constraint).

## Self-Check: PASSED

## Next steps

1. Operator merges this plan branch to main.
2. Operator marks Phase 12 LIVE in ROADMAP after running:
   - `bash infrastructure/scripts/verify-phase12.sh` on the VM
   - `12-UAT.md` Steps 1-8 (Step 9 deferred 24h+)
3. Standing order kicks in (per memory `project_xbrain_phase12_post_ship_integration_check`):
   auto-run cross-system integration check after Phase 12 marked LIVE,
   WITHOUT asking — 8 sections (backend/auth/brain/pipeline/frontends/
   observability/cleanup/report).
4. 24h+ after LIVE: execute Step 9 of UAT (OAuth App revocation per
   `.planning/KB/oauth-app-revocation.md`).
5. Phase 13 planning — `/gsd:plan-phase 13` after Phase 12 marked LIVE
   AND the standing-order integration check completes clean.
