---
phase: 16-oss-light-packaging
plan: 03
subsystem: docs
tags: [docker-compose, oss-light, install-docs, zero-key, local-auth, embeddings, oauth-connector, readme]

# Dependency graph
requires:
  - phase: 16-oss-light-packaging (plan 02)
    provides: "restructured .env.example ([REQUIRED — core boot] section) + make oss-init zero-key secret generator + env-check saas-gating"
  - phase: 18-local-auth
    provides: "POST /v1/auth/local/register|login (email/password, no OAuth); docs/local-auth-recovery.md"
  - phase: 19-local-embeddings
    provides: "keyless in-container embeddings (EMBEDDINGS_PROVIDER=local) — keyless semantic retrieval"
provides:
  - "docs/INSTALL.md — self-contained OSS-light install guide (docs alone, zero external keys)"
  - "Rewritten README Quickstart/Deploy — stale Google-OAuth+GitHub-App requirement removed"
  - "SC#4 release-artifact shape documented (bundle: light compose + .env.example/oss-init + INSTALL.md + zero-key-safe compose-up), with Phase 17 (buildx/CI) + Phase 20 (web app/extension UI) + amd64-VM deferrals"
affects: [16-04 (verify-phase16 gate references INSTALL.md flow), 17-ci-lockstep (registry/multi-arch deferral), 20-frontend (extension zero-key sign-in deferral)]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Docs-alone install path: prereqs -> clone -> make oss-init -> compose up -d --build -> local register -> verify, zero external keys"
    - "OSS-light deploy = direct compose-up on target host; make deploy = SaaS/hosted-team remote (rsync+build-on-VM) — clearly separated in docs"

key-files:
  created:
    - "docs/INSTALL.md"
  modified:
    - "README.md"

key-decisions:
  - "Documented the truthful from-host API path (nginx api.<XBRAIN_BASE_DOMAIN> vhost) instead of the plan's literal localhost:8000, because the shipped compose publishes only nginx :80 — memory-api is not host-bound"
  - "Left the README MIT assertion intact and added a flagged 'license under review (AGPLv3+CLA)' open-item note rather than silently rewriting it (threat T-16-03-03: accept/surface)"
  - "Asserted no license in docs/INSTALL.md (open item)"

patterns-established:
  - "Install docs cite only real Makefile targets / compose commands / oss-init.sh — no invented commands"

requirements-completed: [PKG-01]

# Metrics
duration: 16min
completed: 2026-07-18
---

# Phase 16 Plan 03: OSS-light Install Docs + SC#4 Artifact Shape Summary

**A self-contained `docs/INSTALL.md` that stands up the zero-external-key OSS-light core from docs alone, plus a rewritten README Quickstart/Deploy that drops the stale Google-OAuth+GitHub-App requirement and documents the SC#4 release-artifact bundle with honest Phase-17/20 deferrals.**

## Performance

- **Duration:** ~16 min
- **Started:** 2026-07-18T10:29Z (approx, worktree base)
- **Completed:** 2026-07-18T10:45Z
- **Tasks:** 2
- **Files modified:** 2 (1 created, 1 modified)

## Accomplishments
- **`docs/INSTALL.md` (288 lines):** an 11-section, copy-pasteable walkthrough — what-you-get-zero-key (the D-16-01 split), prerequisites (no Google/GitHub required), provision+clone, `make oss-init` secret generation (+ the manual `openssl rand` alternative and the `MINIO_ROOT_PASSWORD ≥ 8` / no-`__FILL__` warnings), the zero-key-safe `docker compose … up -d --build` boot (with the expected ~8.5 s Neo4j DNS-timeout note), verify (`make ps` + nginx-routed healthz), first-run local register, connector local-auth sign-in, real-deploy CORS/TLS notes, opt-in profiles, and `make deploy` as the labeled remote path.
- **README rewrite:** Quickstart requirements now read "one optional LLM key … no Google OAuth client and no GitHub App"; the flow is `make oss-init → compose up -d --build → make ps → local register`; the Deploy section makes the OSS-light direct compose-up primary and labels `make deploy` the SaaS/hosted-team remote (build-on-VM) path.
- **SC#4 documented honestly:** a new "Release artifacts (OSS-light)" subsection describes the reproducible bundle and explicitly defers registry/multi-arch (`buildx … --push`) to Phase 17, the web app + in-extension zero-key sign-in to Phase 20, and the amd64-VM run to a follow-up — with the never-build-cross-arch rule stated.

## Task Commits

Each task was committed atomically:

1. **Task 1: Write docs/INSTALL.md — the docs-alone OSS-light install guide** — `98b4803` (docs)
2. **Task 2: Rewrite README Quickstart + Deploy + document the SC#4 release-artifact shape** — `45be6aa` (docs)

_Metadata commit (this SUMMARY) is separate and follows._

## Files Created/Modified
- `docs/INSTALL.md` (created) — the self-contained OSS-light install guide (zero external keys).
- `README.md` (modified) — Quickstart/Deploy rewrite + Release artifacts subsection + flagged license note.

## Decisions Made
- **From-host API URL corrected to the nginx vhost.** The plan's task text used `http://localhost:8000/...` for the healthz and register curls, but the shipped `infrastructure/docker-compose.yml` publishes only `nginx` on `:80`; `memory-api` is not bound to a host port (port 8000 is its in-container listener — the address in its own healthcheck and the default `OAUTH_ISSUER_URL`). Documenting `localhost:8000` from the host would have been an invented command that fails against the real compose, which the plan's own key-constraints forbid. The docs therefore use `http://api.localhost/v1/...` (the `api.<XBRAIN_BASE_DOMAIN>` vhost, `20-api.conf.template`) with DNS-free `-H 'Host: api.localhost'` / `--resolve` fallbacks, and explain the localhost:8000 nuance. Acceptance greps (`v1/healthz`, `/v1/auth/local/register`) still pass, since those paths appear verbatim in the vhost URLs.
- **First-boot command uses explicit `up -d --build`, not `make up`.** `make up` is `docker compose up -d` with no `--build`, so it fails on a fresh clone with no images. Docs lead with `docker compose -f infrastructure/docker-compose.yml --env-file .env up -d --build` and present `make build` + `make up` as the already-built equivalent.
- **License left as MIT with a flagged open-item note** (not rewritten) — matches threat T-16-03-03 (accept/surface) and the plan's "optional, only if flagged" allowance.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Corrected the from-host API URL to the real nginx-routed path**
- **Found during:** Task 1 (INSTALL.md verify/register steps) and applied consistently in Task 2 (README register step)
- **Issue:** The plan's task text specified `curl http://localhost:8000/v1/healthz` and `POST http://localhost:8000/v1/auth/local/register`, but the shipped compose file does not publish `memory-api` to the host — only `nginx` binds `:80`. A literal `localhost:8000` command would fail on a real single-host boot, contradicting the "every command must match the real compose file / do not fabricate" constraint.
- **Fix:** Documented the truthful nginx `api.<XBRAIN_BASE_DOMAIN>` vhost path (`http://api.localhost/v1/…`) with `Host`-header / `--resolve` DNS-free fallbacks, plus an explicit "how memory-api is reached" note clarifying that `localhost:8000` is the in-container listener (referenced by the healthcheck and `OAUTH_ISSUER_URL`). Primary verify remains `make ps` (compose healthchecks), which needs no host port.
- **Files modified:** docs/INSTALL.md (§6, §7), README.md (Quickstart register)
- **Verification:** Path fragments `v1/healthz` and `/v1/auth/local/register` present (acceptance greps pass); URLs correspond to `20-api.conf.template`'s `location /v1/ → memory-api` on `server_name api.${XBRAIN_BASE_DOMAIN}`.
- **Committed in:** `98b4803` (Task 1), `45be6aa` (Task 2)

---

**Total deviations:** 1 auto-fixed (1 documentation-accuracy bug)
**Impact on plan:** Necessary for truthfulness — the alternative was documenting a command that does not work against the shipped compose. No scope creep; both tasks otherwise executed as written. The underlying gap (memory-api not host-published while `OAUTH_ISSUER_URL` defaults to `localhost:8000`) is a runtime/compose concern that is out of scope for this docs-only plan and is left for a follow-up (see Next Phase Readiness).

## Issues Encountered
- **Worktree isolation:** initial Write targeted the shared-checkout path; re-issued against the worktree copy (`.claude/worktrees/agent-…/`). No content impact.

## Known Stubs
None — both deliverables are prose documentation; no code stubs, empty data sources, or placeholder UI introduced.

## Threat Flags
None — no new network endpoints, auth paths, file access, or schema surface introduced (documentation only). Existing threat-model mitigations (T-16-03-01 `__FILL__`/oss-init secret guidance; T-16-03-02 CORS-never-`.*` + external-TLS) are reflected in the docs; T-16-03-03 (license) surfaced as a flagged open item per its `accept` disposition.

## User Setup Required
None — no external service configuration required to consume these docs.

## Next Phase Readiness
- **SC#1 (install docs alone) is documented:** `docs/INSTALL.md` walks prereqs → provision → clone → `make oss-init` → `docker compose … up -d --build` → local register → verify, with zero external keys. Plan 16-04's `verify-phase16.sh` is the automated proof this doc points to.
- **SC#4 (release-artifact shape) is documented** with honest deferrals (Phase 17 registry/CI, Phase 20 web app/extension UI, amd64-VM follow-up).
- **Follow-up flagged (out of scope here, runtime/compose concern):** on a single-host boot the shipped compose does not publish `memory-api` to the host, yet `make oss-init` defaults `OAUTH_ISSUER_URL=http://localhost:8000`. Reaching the API from the host currently requires the nginx `api.<domain>` vhost. A future runtime change could either publish `memory-api` on a loopback host port for local dev or align the local `OAUTH_ISSUER_URL` default with the nginx `api.localhost` vhost. Documented, not solved, in this docs-only plan.

## Self-Check: PASSED

- FOUND: docs/INSTALL.md
- FOUND: README.md (modified)
- FOUND: .planning/phases/16-oss-light-packaging/16-03-SUMMARY.md
- FOUND commit: 98b4803 (Task 1)
- FOUND commit: 45be6aa (Task 2)

---
*Phase: 16-oss-light-packaging*
*Completed: 2026-07-18*
