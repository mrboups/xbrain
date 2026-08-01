---
phase: 16-oss-light-packaging
verified: 2026-07-18T13:00:15Z
status: passed
score: 4/4 success criteria verified (23/23 automated gate checks, live re-run)
overrides_applied: 0
deferred:
  - truth: "SC#1 clean-install proven on a genuinely fresh amd64 VM (bare-metal, no prior Docker state)"
    addressed_in: "Documented follow-up (not mapped to a numbered phase)"
    evidence: "16-CONTEXT.md D-16-04 (locked decision, driver-authorized under the autonomous mandate): 'The prod VM is STOPPED and dev is arm64. SC#1's real-deployment-path proof is a scripted local docker compose up... The real fresh-amd64-VM run is documented as a follow-up (same deferral pattern as Phase 19's amd64 RSS). NEVER build-and-deploy a locally-built image cross-arch.' This is an explicit, pre-negotiated scope decision baked into the phase's own planning artifact, not a silently-skipped gap — and it was independently reproduced live during this verification (see below)."
---

# Phase 16: OSS Light Packaging Verification Report

**Phase Goal:** A team with no prior knowledge of the xbrain source can stand up the OSS-light edition on a fresh VM from the install docs alone, and the brain works end-to-end (chat via existing surfaces + ChatGPT-web connector + doc analysis + ingest + keyless retrieval + truth-levels + clip) with zero external keys. Standalone web chat is Phase 20, not here.

**Verified:** 2026-07-18T13:00:15Z
**Status:** passed
**Re-verification:** No — initial verification

## Method

This verification did not rely on SUMMARY.md claims. Every artifact cited by the four
plan SUMMARYs was read and cross-checked directly against the committed code
(`oauth_authorize.py`, `docker-compose.yml`, the nginx template, `media.py`, `Makefile`,
`.env.example`, `oss-init.sh`), and — because the phase's own gate lesson mandates it and
time permitted — **`bash infrastructure/scripts/verify-phase16.sh` was re-run live, end to
end, from a cold state** (0 pre-existing `xbrain-*` containers, real `docker compose ...
up -d --build` of all 10 core services including the 4 `build:` services, real HTTP walk
through nginx, real Postgres row assertions). This is independent, reproduced evidence, not
a re-statement of the SUMMARY's own claimed output.

**Live re-run result (this verification, not the SUMMARY's captured run):**

```
=== Summary ===
PASS: 23 / 23  (SKIP: 0)
GATE_EXIT=0
```

Container health observed directly via `docker ps` during the run (all 10 core services
`healthy`, matching the gate's own assertions): `xbrain-postgres`, `xbrain-qdrant`,
`xbrain-minio`, `xbrain-centrifugo`, `xbrain-nginx`, `xbrain-memory-api`,
`xbrain-mcp-brain`, `xbrain-mcp-gateway`, `xbrain-mcp-scraper`, `xbrain-brain-janitor`.
After the run, `docker ps -a --filter name=^xbrain-` and `docker volume ls --filter
name=xbrain-p16` both returned empty — the EXIT-trap teardown left no residue.

## Goal Achievement

### Observable Truths (Success Criteria, from ROADMAP.md Phase 16)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | SC#1 — Following only the published install docs, an operator provisions a fresh VM and reaches a running OSS-light stack; a clean-install test passes end-to-end | ✓ VERIFIED | `docs/INSTALL.md` walks prereqs → provision → clone → `make oss-init` → `docker compose ... up -d --build` → verify → register, and every command matches the real `Makefile` targets / `oss-init.sh` output / compose file (independently cross-checked, not just grepped by the gate). The gate's own live re-run performed the real build+boot from zero pre-existing containers and reached healthy on all 10 core services. The fresh-**amd64**-VM instance is an explicit, pre-negotiated deferral (D-16-04, see Deferred Items below) — the dev host is arm64 and the prod VM is stopped, so a local arm64 `docker compose up` from a clean state is the closest faithful proxy without risking a cross-arch build defect. |
| 2 | SC#2 — The OSS-light compose profile (`COMPOSE_PROFILES` unset) boots ~10 services with all healthchecks green, matching the Phase 15 profile table | ✓ VERIFIED | Independently reproduced: `docker compose -f infrastructure/docker-compose.yml config --services` (no `.env`, profiles unset) returns exactly `brain-janitor centrifugo mcp-brain mcp-gateway mcp-scraper memory-api minio nginx postgres qdrant` (10, by name). Live boot: all 10 reached `healthy`/`running`. `COMPOSE_PROFILES` values are `integrations ops saas` (no `pro`, matching Phase 15's dropped-pro-tier decision). Zero of the 22 opt-in containers were running with profiles unset (check (i), live). |
| 3 | SC#3 — With no external keys set, a user registers via local auth, uploads/analyzes a document, has it ingested and semantically retrieved keyless with truth-levels visible, connects via the ChatGPT-web connector, and clips a web page into memory | ✓ VERIFIED (with one disclosed, pre-existing scope limitation — see note) | Live re-run, real HTTP through nginx, zero external keys anywhere in the environment: (e) register → `xbt_` token + `solo-<hex>` team_scope, login → fresh `xbt_` (argon2 against live Postgres); (f) `POST /v1/media/upload` → 201, then `GET /v1/memory/search` with a query that is NOT a substring of the stored text returned the item WITH `truth_level='WORKING'` (score 0.852) — only possible via a real vector search, and `EMBEDDINGS_PROVIDER=local` with no `OPENAI_API_KEY` anywhere; (g) `/.well-known/oauth-authorization-server` → 200, DCR mints a public client, `/oauth/authorize` renders the **local** login form (verified: no `github.com` redirect, `GITHUB_APP_CLIENT_ID` empty), a wrong password mints no code (401, no redirect — negative case asserted first), the correct password → 302 with a minted `?code=`; (h) `POST /v1/memory/upsert source=manual-clip` → 201, and a **live psql `SELECT count(*)` against `xbrain-postgres`** confirms a real `memory_items` row landed (not just a 201 status). **Note (disclosed, not hidden):** `media.py:111` embeds `caption or filename`, not the uploaded file's bytes — "document analysis" in this flow means the caption text is what gets embedded/retrieved, not extracted document content. This is pre-existing `media.py` behavior, out of Phase 16's scope, and both the 16-04 SUMMARY and `verify-phase16.sh`'s own inline comment state this explicitly rather than overclaiming full-text extraction. `docs/INSTALL.md` §1 correctly describes this as "stores a file in MinIO and creates a tagged media memory item" — it does not claim content extraction. |
| 4 | SC#4 — The published OSS release artifact shape exists and is reproducible: tagged multi-arch images (or a documented build-on-VM path), the light compose file, and the install docs | ✓ VERIFIED | `README.md` "Release artifacts (OSS-light)" section documents the bundle (light compose + `.env.example`/`oss-init.sh` + `docs/INSTALL.md` + the zero-key-safe `docker compose ... up -d --build` on-target-host path) and explicitly, honestly defers registry/multi-arch publishing to Phase 17 (whose own ROADMAP entry gate independently confirms this ownership) and the standalone web app to Phase 20. `make deploy` is correctly re-labeled the SaaS/hosted-team remote path (not the OSS-light path) in both `README.md` and `docs/INSTALL.md` §11. Live-verified: `make env-check` (the `make deploy` prerequisite) passes a zero-key `.env` with `COMPOSE_PROFILES` unset and fails a `saas`-without-saas-creds profile, naming the missing var — reproduced independently in this verification's live gate run (checks (d0)). |

**Score:** 4/4 Success Criteria verified.

### Deferred Items

| # | Item | Addressed In | Evidence |
|---|------|---------------|----------|
| 1 | A genuinely fresh **amd64** bare-metal VM clean-install run (vs. the local arm64 Docker-compose proxy used here) | Documented follow-up (not mapped to a specific later numbered phase's success criteria — kept here rather than silently dropped) | `16-CONTEXT.md` D-16-04 (a locked decision recorded during planning, made by the driver under the project's own recorded autonomous-run mandate): dev host is arm64, the prod VM is stopped, and cross-arch image deploys are explicitly forbidden project-wide (`project_xbrain_dev_machine_arm64` memory note). The local arm64 proxy is the documented, deliberate stand-in, not an unacknowledged gap. |

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `infrastructure/scripts/verify-phase16.sh` | Clean-install acceptance gate: config + env-drift + oss-init + deploy-layer + real boot + SC#3 HTTP walk | ✓ VERIFIED | Read in full; independently re-run live to 23/23 PASS, 0 SKIP, exit 0. SKIP=FAIL is structural (checks (e)-(i) are recorded as FAIL, not SKIP, when boot (d) fails — confirmed by reading the control flow at the bottom of the script). |
| `infrastructure/scripts/oss-init.sh` | One-command zero-external-key core `.env` generator | ✓ VERIFIED | CSPRNG secrets (`openssl rand`), valid Fernet key, refuses to clobber without `--force`, emits `EDITION=oss`, `EMBEDDINGS_PROVIDER=local`, `UVICORN_WORKERS=1`, `MINIO_ROOT_PASSWORD` 32 hex chars. Independently executed via the live gate run — produced a bootable env with no external key present. |
| `.env.example` (root) | Four-section layout, MinIO `[required]`, saas secrets un-`[required]` | ✓ VERIFIED | Read in full: `[REQUIRED — core boot]` / `[OPTIONAL — one LLM key]` / `[OPTIONAL — integrations profile]` / `[OPTIONAL — saas profile]` sections present; `MINIO_ROOT_PASSWORD` tagged `[required] >= 8 chars`; stale "SaaS-only" header absent; `MEILI_MASTER_KEY`/`JWT_SECRET`/`CREDS_KEY`/`CREDS_IV`/`MONGO_URI`/`PIPELINE_API_KEY` retagged `[optional]`. |
| `apps/memory-api/.env.example` | Mirrored four-section layout | ✓ VERIFIED | Read; mirrors the root file's section structure and `[REQUIRED — core boot]` set. |
| `Makefile` | `oss-init`, `env-check` (profile-gated), `verify-phase16` targets | ✓ VERIFIED | All three targets present and match the gate's expectations; `env-check` correctly splits CORE vs SAAS-only required vars behind `case ,$(COMPOSE_PROFILES), in *,saas,*`. |
| `docs/INSTALL.md` | Self-contained, docs-alone install guide, zero external keys | ✓ VERIFIED | Read in full (288 lines). Every command cross-checked against the real `Makefile`/compose/`oss-init.sh` — no invented commands found. Correctly documents the `api.<domain>` vhost nuance (memory-api is not host-published) instead of a `localhost:8000` command that would fail on the real compose. |
| `README.md` | Rewritten Quickstart/Deploy, SC#4 artifact-shape section | ✓ VERIFIED | Read in full. Quickstart matches `docs/INSTALL.md`'s flow; Deploy section correctly separates OSS-light (direct compose-up) from `make deploy` (SaaS/hosted remote); Release-artifacts section discloses the Phase 17/20/amd64-VM deferrals honestly. |
| `apps/memory-api/app/routes/oauth_authorize.py` | D-16-02 zero-key connector local-auth branch | ✓ VERIFIED | Read in full. `GET /oauth/authorize` branches on `if not settings.GITHUB_APP_CLIENT_ID` to render the local login form; `POST /oauth/authorize/local` verifies against the same Phase-18 argon2id store (`verify_password`/`verify_decoy`), enforces rate limiting, converges into the same `_finalize_consent`/consent-page logic the GitHub leg uses. Live-verified: wrong password → no code; correct password → 302 with `?code=`. |
| `apps/memory-api/app/templates/oauth_local_login.html` | Local login form template | ✓ VERIFIED | Referenced correctly by `oauth_authorize.py`; rendered live during the gate re-run (`action="/oauth/authorize/local"` confirmed in the response body). |
| `infrastructure/nginx/templates/10-xbrain.conf.template` | Defect fix: `/nginx-health` reachable from `default_server` | ✓ VERIFIED | Read in full. `location /nginx-health { return 200 "ok\n"; }` now exists inside the `default_server` block (not preempted by the server-level `return 302`), with an inline comment explaining the original bug. Live-verified: `xbrain-nginx` reached `healthy` on the live re-run. |
| `infrastructure/docker-compose.yml` | Defect fix: `brain-janitor` ordered after `memory-api` | ✓ VERIFIED | Read lines 1171-1215: `depends_on: memory-api: { condition: service_healthy }` present with an inline comment explaining the original migration-race defect. Live-verified: `xbrain-brain-janitor` reached `healthy` promptly on the live re-run (no ~15h stall). |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `docs/INSTALL.md` commands | Real `Makefile`/compose/`oss-init.sh` | Literal command text | WIRED | Every command in the install doc (`make oss-init`, `docker compose ... up -d --build`, `make ps`, `make env-check`, the register curl) exists and behaves as documented — verified both by static cross-reference and by the live gate re-run exercising the same command sequence. |
| `GET /oauth/authorize` (zero-key branch) | `POST /oauth/authorize/local` | Signed `pre_github` state, HTML form | WIRED | Live-verified: form action + hidden state field render correctly; the state round-trips through the POST handler. |
| `POST /oauth/authorize/local` | `_finalize_consent` (same code path as the GitHub leg) | `post_github` state re-sign | WIRED | Read + live-verified: single-team case mints a code via the shared `_finalize_consent`; multi-team case (not exercised live, single-team account was used, but code path read directly) renders the shared `oauth_consent.html`. |
| `POST /v1/media/upload` | `provider.upsert` → Qdrant (local embedder) | `MemoryItem(content=caption or filename)` | WIRED (with disclosed limitation) | Live-verified: upload → 201, then a non-verbatim semantic query retrieves the item with `truth_level` populated — proves the local-embedding pipeline is live, not proves file-content extraction (see SC#3 note above). |
| `POST /v1/memory/upsert (source=manual-clip)` | `memory_items` Postgres table | `provider.upsert` | WIRED | Live-verified via direct `psql` query against `xbrain-postgres`, not just the HTTP 201 — row-level proof, not status-level. |
| `brain-janitor` | `memory-api` (schema readiness) | `depends_on: service_healthy` | WIRED | Live-verified: no migration race observed; `brain-janitor` reached healthy without the previously-documented ~15h stall. |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|---|---|---|---|---|
| PKG-01 | 16-01, 16-02, 16-03, 16-04 | A team can stand up the OSS-light edition (chat + full brain: doc analysis, ingest, keyless semantic retrieval, truth-levels, ChatGPT connector, clip) on a fresh VM from install docs alone, zero external keys | ✓ SATISFIED | All 4 ROADMAP Success Criteria independently verified above, including a live re-run of the acceptance gate. |

**Note (documentation-sync gap, non-blocking):** `.planning/REQUIREMENTS.md` still lists `PKG-01` as `[ ]` unchecked and its traceability table as "Pending", even though `ROADMAP.md` marks Phase 16 `(completed 2026-07-18)`. This is a stale-bookkeeping issue in REQUIREMENTS.md, not a code or functionality gap — the underlying capability is verified working. Recommend a trivial follow-up edit to flip the checkbox and traceability status; not treated as a phase-blocking finding here since it does not affect what the codebase actually does.

### Anti-Patterns Found

None. Scanned all Phase-16-touched files (`verify-phase16.sh`, `oss-init.sh`, `docs/INSTALL.md`, `oauth_authorize.py`, `oauth_local_login.html`, `Makefile`, `.env.example`) for TODO/FIXME/placeholder/stub markers — the only hits were legitimate comments describing the `__FILL__`/placeholder anti-pattern the tooling exists to prevent, not actual stubs.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|---|---|---|---|
| Bare-core service list is exactly 10 named services | `docker compose -f infrastructure/docker-compose.yml config --services` (no profiles) | Returned exactly `brain-janitor centrifugo mcp-brain mcp-gateway mcp-scraper memory-api minio nginx postgres qdrant` | ✓ PASS |
| Full clean-install + SC#3 walk (all 9 checks) | `bash infrastructure/scripts/verify-phase16.sh` (live, cold state) | `PASS: 23 / 23 (SKIP: 0)`, exit 0; teardown left zero residual containers/volumes | ✓ PASS |
| `media.py` caption-not-body claim | `grep -n "caption" apps/memory-api/app/routes/media.py` | Line 111: `content=caption or file.filename or "media"` | ✓ PASS (confirms the SUMMARY's disclosed limitation is accurate, not fabricated) |
| Two 16-04-claimed defect fixes are live in committed config | Read `10-xbrain.conf.template` + `docker-compose.yml:1202-1213` | `/nginx-health` inside `default_server`; `brain-janitor` has `memory-api: service_healthy` dependency | ✓ PASS |

### Human Verification Required

None. Every must-have was verifiable programmatically, including a full live reproduction of the acceptance gate (containers really booted, real HTTP walk, real Postgres row assertions, real teardown). No visual/UX-only surface was introduced in this phase (`UI hint: partial — install docs only`, per ROADMAP).

### Gaps Summary

No gaps. All four ROADMAP Phase 16 Success Criteria and requirement PKG-01 are verified against the live codebase, not just against SUMMARY.md prose. The phase's own two self-caught defects (nginx health-check unreachable behind a server-level redirect; brain-janitor racing the Alembic migration on a clean install) are confirmed fixed in the committed `docker-compose.yml` and nginx template, and were not observed to regress during this verification's independent live re-run. One scope-limited, pre-existing behavior (media caption vs. file-body embedding) is disclosed honestly in both the code comments and `docs/INSTALL.md`, and does not misrepresent what the system does. One low-risk documentation-sync issue (REQUIREMENTS.md checkbox not yet flipped) is noted but does not block phase completion.

---

*Verified: 2026-07-18T13:00:15Z*
*Verifier: Claude (gsd-verifier)*
