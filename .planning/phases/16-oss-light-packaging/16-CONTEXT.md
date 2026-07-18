# Phase 16: OSS Light Packaging — Context

**Gathered:** 2026-07-18 (autonomous — scope decisions made by the driver per the standing autonomous mandate, resolving the researcher's 4 Open Questions without a discuss-phase)
**Status:** Ready for planning
**Source:** ROADMAP Phase 16 (PKG-01, SC#1..SC#4) + 16-RESEARCH.md findings.

<domain>
## Phase Boundary

Real packaging of the **OSS-light edition** so an operator can stand it up from install docs alone with **zero external integration keys** and the brain works end-to-end (register → doc analysis → keyless ingest+retrieval → truth-levels → ChatGPT-web connector → clip).

**IN scope:** the light compose story (core profile = 10 services, `COMPOSE_PROFILES` unset); a restructured `.env.example` that boots the core cleanly zero-key; generated-secrets bootstrap; install docs ("docs alone, no source reading"); a **scripted clean-install test** that boots the core locally (arm64) with a minimal generated `.env` and asserts all healthchecks green + runs the SC#3 flow over HTTP; the **connector OAuth-AS local-auth fix** (below); the SC#4 release-artifact shape (documented build path + light compose + docs).

**OUT of scope:** the standalone web chat frontend (Phase 20); registry-hosted multi-arch image publishing + CI (Phase 17); the Chrome-extension zero-key **sign-in** UI (Phase 20 — see D-16-03).
</domain>

<decisions>
## Implementation Decisions (locked by the driver)

### D-16-01 — "Zero external keys" = zero of {OpenAI, Google, GitHub App}. (OQ1)
The SC#3 flow (register via local auth, upload/analyze a doc, keyless ingest+semantic retrieval, truth-levels, ChatGPT-web connector, clip) MUST complete with NO OpenAI, NO Google, NO GitHub-App credentials. The single LLM key (Anthropic — the "one key: Anthropic OR OpenAI OR Grok" promise) is **optional** for the SC#3 flow itself: doc ingest + local embedding + semantic retrieval are keyless (Phase 18/19); LLM-based extraction and the in-chat agent are enhancements that consume the one Anthropic key but are NOT part of the SC#3 zero-external-key proof. Install docs must state this split clearly.

### D-16-02 — FIX the connector OAuth-AS zero-key blocker. (OQ2, in scope)
`apps/memory-api/app/routes/oauth_authorize.py` (~L128-138) unconditionally redirects the ChatGPT-web/Claude.ai connector's sign-in into GitHub OAuth (known gap D-18-07). SC#3 explicitly requires "connects via the ChatGPT-web connector" zero-key, so this MUST be fixed: when GitHub is not configured (zero-key install), the connector's OAuth consent authenticates the user via the **Phase-18 local-auth session/login** instead of forcing GitHub. This is security-sensitive (it is the connector's authN) — the plan MUST carry a `<threat_model>` for it (no auth bypass, consent still bound to one authenticated user + one team_scope, no open redirect). Keep the GitHub path working when GitHub IS configured (additive, not a replacement).

### D-16-03 — DESCOPE the extension zero-key sign-in; prove clip at the API level. (OQ2)
The Chrome extension's sign-in (`chrome-extension/background.js` ~L263, `popup.html`) is hardcoded to Google/GitHub OAuth with no local-auth/manual-token path. Reworking the extension's auth UI belongs to **Phase 20** (the frontend/auth-surface phase). For Phase 16, prove SC#3's "clip a web page into memory" **at the API level**: a scripted HTTP call to the clip/ingest endpoint under a local-auth `xbt_` token lands a memory_item keyless (the backend clip path already works zero-key). Install docs note the extension-UI zero-key sign-in is Phase 20.

### D-16-04 — Clean-install test = local arm64 docker-compose proxy; real amd64 VM deferred. (OQ3)
The prod VM is STOPPED and dev is arm64. SC#1's real-deployment-path proof is a **scripted local `docker compose up`** (core profile, `COMPOSE_PROFILES` unset, a freshly generated minimal `.env` with zero external keys) that asserts every core healthcheck goes green and then drives the SC#3 flow over HTTP. The real fresh-amd64-VM run is documented as a follow-up (same deferral pattern as Phase 19's amd64 RSS). NEVER build-and-deploy a locally-built image cross-arch.

### D-16-05 — Restructure `.env.example` IN PLACE (single file, clear sections). (OQ4)
Fix the Phase-15 drift: MinIO is core (Phase 15) and refuses to boot below an 8-char `MINIO_ROOT_PASSWORD` — it is currently mistagged `[optional]` under a stale "SaaS-only" header, so a literal reading crashloops a core service. LibreChat/Open WebUI `saas`-only secrets are conflated into "Required — minimal boot". Restructure into clear sections: **[REQUIRED — core boot]** (generated secrets: JWT/Fernet/session, MinIO root creds ≥8 chars, EDITION, the boot-fatal OAUTH_ISSUER_URL/RESOURCE_URL), **[OPTIONAL — one LLM key]** (Anthropic/OpenAI/Grok), **[OPTIONAL — integrations profile]**, **[OPTIONAL — saas profile]**. One file, not a fork.

### D-16-06 — SC#4 release artifact = documented build-on-VM path + light compose + install docs. (OQ from research)
No CI/registry infra exists for any `xbrain/*` image (repo-wide grep). The only established path is `make deploy`'s build-on-VM-over-SSH. Formalize THAT as the SC#4 deliverable (documented, reproducible); registry-hosted multi-arch image publishing is deferred to Phase 17 per its own entry-gate wording. Document the buildx multi-arch command as the future path.

### Claude's Discretion
- The exact clean-install test script location/shape (mirror existing `infrastructure/scripts/verify-phaseNN.sh` conventions) and how it generates the minimal `.env` (a `make oss-init` / script that emits random secrets).
- Whether the secret-generation is a Makefile target, a shell script, or documented `openssl rand` one-liners in the install doc — pick the lowest-friction "docs alone" option.
- Install doc location (`docs/INSTALL.md` vs README section) matching existing `docs/` conventions.
</decisions>

<canonical_refs>
## Canonical References — read before planning
- `infrastructure/docker-compose.yml` — the 10 core (untagged) services + integrations/ops/saas profiles; memory-api `command:` (just fixed to honor UVICORN_WORKERS); healthchecks + start_period.
- `.env.example` + `apps/memory-api/.env.example` — the templates to restructure (D-16-05).
- `apps/memory-api/app/routes/oauth_authorize.py` — the connector OAuth-AS to fix (D-16-02); `app/config.py` boot-fatal validators (OAUTH_ISSUER_URL etc.); the Phase-18 local-auth login path to authenticate connector consent against.
- `docs/local-auth-recovery.md` — where D-18-07 (this connector gap) was documented.
- `infrastructure/scripts/*.sh` (verify-phaseNN.sh, clean-start.sh) + `Makefile` (`make deploy` build-on-VM path) — the gate-script + build conventions to mirror.
- `.planning/phases/16-oss-light-packaging/16-RESEARCH.md` — the live-verified core service list, the .env drift specifics, the SC#3 blockers with line citations, the Validation Approach.
- ROADMAP Phase 16 block — the 4 Success Criteria (the must_haves source).
</canonical_refs>

<specifics>
## The gate lesson applies (hard)
Seven+ defects across P14/15/18 — and the Phase-19 CR-01 blocker (a compose `command:` that hardcoded `--workers 2`, nullifying the RAM knob, which passed verification because the check only confirmed the env var was *present*, never that the running command *read* it) — all shared one cause: **a check that never traversed the real deployment path.** For Phase 16 that means: the clean-install proof MUST be a REAL `docker compose up` of the core profile with a REAL zero-external-key `.env`, asserting actual healthcheck state and driving the SC#3 flow over real HTTP — NOT a `docker compose config` parse, NOT a mocked boot. SKIP=FAIL. Docker is available (arm64); Git Bash docker needs `MSYS_NO_PATHCONV=1` + Windows paths. A ~8.5s Neo4j DNS timeout at boot when NEO4J_URI is set-but-absent is EXPECTED (Phase 15), not a defect.
</specifics>

<deferred>
- Standalone web chat frontend + extension zero-key sign-in UI — Phase 20.
- Registry-hosted multi-arch image publishing + CI pipeline — Phase 17.
- Real fresh-amd64-VM clean-install run — documented follow-up (VM currently stopped).
</deferred>

---
*Phase: 16-oss-light-packaging*
*Context gathered: 2026-07-18 (autonomous scope resolution)*
