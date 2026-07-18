# Phase 16: OSS Light Packaging - Research

**Researched:** 2026-07-18
**Domain:** Docker Compose packaging/edition mechanics (already built in Phase 15), zero-external-key installability, OAuth-connector + browser-extension auth gaps, install documentation.
**Confidence:** HIGH (the service-set, env-var, and code-path claims below are VERIFIED by direct file reads and live `docker compose config` runs in this session, not inferred) / MEDIUM (RAM headroom on a real e2-medium, amd64 build timing) / LOW (none of the load-bearing claims — see Assumptions Log)

## Summary

Phase 16 looks like a documentation-and-compose-profile phase, but the investigation found **two concrete, code-verified blockers** that stand between the current codebase and the phase's own locked success criteria — not hypothetical risks, but exact file/line findings:

1. **`GET /oauth/authorize` (`apps/memory-api/app/routes/oauth_authorize.py:128-138`) unconditionally redirects into GitHub OAuth** using `settings.GITHUB_APP_CLIENT_ID`. There is no local-auth or Google branch. On a zero-external-key install (`GITHUB_APP_CLIENT_ID` empty), the ChatGPT-web/Claude.ai connector's sign-in leg is unreachable — it 302s to `https://github.com/login/oauth/authorize?client_id=&...`, which GitHub rejects. This is a **known, already-documented gap** (`docs/local-auth-recovery.md` §"Known limitations (D-18-07)": *"MCP / ChatGPT Custom-Connector sign-in is GitHub-only... A deployment with no GitHub App configured cannot yet use the Claude.ai / ChatGPT Custom Connector"*) — Phase 18 flagged it and explicitly left it unfixed. Phase 16 SC#3 requires exactly the thing D-18-07 says doesn't work yet.
2. **The Chrome extension's sign-in is hardcoded to Google OAuth and GitHub OAuth** (`chrome-extension/background.js:263` — a literal Google `CLIENT_ID`; `chrome-extension/popup.html` — only `btn-signin-github` and `btn-connect-xbrain` buttons exist). There is no local-auth option and no "paste an existing token" UI. Since "clip a web page into memory" (an explicit SC#3 action) is a Chrome-extension-only feature today, a zero-external-key install cannot drive it through the shipped UI.

Both are real code gaps this phase's own success criteria expose — not documentation problems. The research also found that **`.env.example`'s own structure has not been reorganized since Phase 15 introduced `COMPOSE_PROFILES`**: its "Required — minimal boot" section still conflates the ~10-service OSS-light core with LibreChat/Open WebUI's `saas`-only secrets (MEILI_MASTER_KEY, JWT_SECRET, CREDS_KEY, MONGO_URI, PIPELINE_API_KEY, etc. are all still marked `[required]`), while the **promoted-to-core MinIO's own credentials sit under a stale "=== SaaS-only / not part of OSS-light ===" header** and `MINIO_ROOT_PASSWORD` is tagged `[optional]` even though MinIO (a Phase-15 core service) will not start without a password ≥8 characters — an empty value crashloops the `minio` container. This directly contradicts SC#1's "no source reading" promise: an operator following the current `.env.example` literally would either fill in 15+ vars they don't need, or leave a genuinely-required one blank and get a silent core-service crashloop.

On the packaging-artifact question (SC#4): there is **no existing CI, no registry push, and no multi-arch build pipeline anywhere in this repo** for any `xbrain/*` image — every `build:` service compiles from source, tagged `xbrain/<service>:phaseN` (a phase-number tag, not a version). The **only established "artifact" path today is `make deploy`**, which rsyncs source to the VM and runs `docker compose build && up` there (build-on-target-host, not build-and-ship). Given the prod VM is currently **stopped** (cost-cutting, per project memory) and the dev host is arm64, the pragmatic SC#4 deliverable is to formalize and document that existing build-on-host path, explicitly deferring registry-hosted multi-arch tagged images to Phase 17 (CI Lockstep), whose own entry-gate text already anticipates this ("OSS release artifact shape... already exists (produced manually in Phases 16/20); CI now automates producing and publishing it").

**Primary recommendation:** Treat this phase as three real workstreams, not one — (1) fix the two SC#3 auth gaps (local-auth branch in the OAuth-AS authorize flow; a minimal manual-clip or local-auth connect path for the extension), (2) restructure `.env.example` so the OSS-light-core-required set is self-evident without source reading, and fix the `MINIO_ROOT_PASSWORD` mislabel, (3) write install docs + a `verify-phase16.sh` gate that does a REAL `docker compose build && up` of all 10 core services (not just the 5 pull-only ones Phase 15's gate covered) on the arm64 dev host as SC#1's proxy, with the amd64/real-VM run and the literal ChatGPT.com connector click-through documented as a follow-up UAT (the same pattern Phase 18 used for its browser UAT and Phase 19 used for its amd64 RSS number).

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| PKG-01 | A team can stand up the OSS-light edition (chat + full brain: doc analysis, ingest, keyless semantic retrieval, truth-levels, ChatGPT connector, clip) on a fresh VM from the install docs alone, with zero external keys | Exact 10-service core list verified live (§Standard Stack); the two SC#3 code blockers identified with file/line citations + a concrete fix pattern (§Architecture Patterns, §Common Pitfalls); `.env.example` OSS-light-required audit (§Common Pitfalls); build-artifact recommendation for SC#4 (§Architecture Patterns); a full Validation Approach reusing the Phase 15/18/19 "gate lesson" harness conventions (§Validation Approach). |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

- **Open-source + self-hostable only.** No managed-cloud-only service may become load-bearing for the OSS-light path. The 10-service core is already 100% self-hostable images/builds — nothing new to add here.
- **Dev host is ARM64, prod is amd64 — NEVER `docker build` locally then deploy to the GCP VM.** This governs the whole SC#1/SC#4 test design below: any local `docker compose build` on this machine is a *test proxy*, never a candidate for `docker push`-then-VM-pull unless built via `buildx --platform linux/amd64` (or built directly on the amd64 VM).
- **GSD workflow is mandatory** — this research feeds `/gsd-plan-phase 16`; no code was changed in this session.
- **Product/code in English only** — any new install docs, UI copy (a local-auth connect option in the extension, if built), and log messages must be English.
- **Don't start coding on architecture/scope messages** — this document is research input, not an implementation.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Compose profile selection (which containers run) | Ops/Deployment (Docker Compose `profiles:`) | — | Already built in Phase 15; Phase 16 only documents/tests it, does not change it |
| OAuth AS sign-in method (GitHub-only today) | API/Backend (`memory-api` — `app/routes/oauth_authorize.py`) | — | The connector's identity resolution is a backend routing decision; the fix belongs next to `auth_local.py`'s existing password-verification service, not in a new tier |
| Chrome-extension connect/sign-in | Browser/Client (`chrome-extension/`) | API/Backend (`/v1/auth/local/login` — already exists) | The extension is its own client tier; it already knows how to store and use an `xbt_` token (`chrome.storage.local`), it just has no UI path to obtain one via local auth |
| Install documentation | Docs (repo root `README.md` / new `docs/INSTALL.md`) | — | No runtime tier; consumed by a human operator before any service exists |
| Build/publish artifact (SC#4) | Ops/Deployment (`Makefile` `make deploy` — build-on-host) | CDN/Registry (deferred to Phase 17) | The existing `make deploy` recipe already builds on the target host over SSH; that pattern is the pragmatic Phase 16 deliverable, not a new CI/registry tier |
| `.env.example` correctness (which vars gate which profile) | Docs/Config (repo root `.env.example`) | API/Backend (`app/config.py` field_validators enforce the boot-fatal subset) | The doc and the code must agree; today they've drifted since Phase 15 |

## Standard Stack

This phase adds **no new libraries**. Everything needed already exists in the compose file, `Makefile`, and `apps/memory-api`; the work is packaging correctness, two auth-flow extensions, and documentation — not new dependencies.

### Core (existing, reused)

| Tool | Version | Purpose | Why Standard |
|------|---------|---------|---------------|
| Docker Compose | v5.2.0 [VERIFIED: `docker compose version` in this session] | Profile-gated service orchestration | Already the project's only orchestrator; `profiles:` mechanics were built and gated in Phase 15 |
| Docker | 29.6.1 [VERIFIED: `docker --version` in this session] | Container build/run | — |
| `argon2-cffi`, `authlib`, existing `local_credentials` repo | Already in `apps/memory-api/pyproject.toml` (Phase 18) | Local-auth password verification, reusable for the OAuth-AS local-auth branch | Phase 18 already built `app/services/password_hash.py::verify_password` + `app/repos/local_credentials.py` — the OAuth-AS fix is wiring, not new crypto |

### Supporting (if the SC#3 fixes are built this phase)

| Tool | Version | Purpose | When to Use |
|------|---------|---------|-------------|
| None new | — | — | The extension's manual-token or local-auth-connect option and the OAuth-AS local-auth branch are both pure wiring against code that already exists (Phase 18's `/v1/auth/local/login`) |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Build-on-target-host artifact (SC#4) | `docker buildx bake --platform linux/amd64,linux/arm64 --push` to a registry (e.g. GHCR) | Literally satisfies "tagged multi-arch images," but there is **zero existing CI/registry infra** in this repo (`grep` for `ghcr.io`/`buildx`/`docker push` across `.github/` and `Makefile` returns nothing beyond Open WebUI's own upstream image) [VERIFIED: repo grep, this session]. Building ~17 services under QEMU amd64 emulation from an arm64 host (Phase 19 already proved this works for memory-api, slowly) is a heavy, CI-shaped lift that duplicates Phase 17's explicit charter (REL-01/02). Recommend deferring full registry automation to Phase 17; optionally prove ONE `buildx --push` of the flagship `memory-api` image as a literal existence proof if time permits, but do not block SC#4 on it. |
| Rewriting `.env.example` from scratch | Reorganizing the existing file's sections + fixing the 4 identified mislabels | The file's content (secrets, defaults) is correct; only the *section headers and `[required]`/`[optional]` tags* have drifted from Phase 15's profile split. A full rewrite risks losing hard-won comments (e.g. the `MINIO_URL`/inline-comment gotcha already documented at lines 264-268) |
| A custom install wizard/script | Plain `docker compose up` + a documented `.env` generation step (`openssl rand` commands, already the project's convention) | The project has never built an installer beyond `make env-check`/`make deploy`; SC#1 asks for install-doc-driven manual steps, which is the standard OSS self-host pattern (matches every project in this space — Immich, Nextcloud, etc. all ship "copy .env.example, fill secrets, `docker compose up`") |

**Installation:** No new packages. If the OAuth-AS local-auth branch is built, it imports functions already present in `apps/memory-api/app/services/password_hash.py` and `apps/memory-api/app/repos/local_credentials.py` — no new dependency.

**Version verification:** N/A — no new third-party packages introduced by this phase.

## Architecture Patterns

### System Architecture Diagram — OSS-light core + the two SC#3 gaps

```
                                   OPERATOR (install docs, no source reading)
                                              │
                              provisions VM, clones repo, fills .env
                                              │
                                              ▼
                        ┌─────────────────────────────────────────────┐
                        │   docker compose (COMPOSE_PROFILES unset)     │
                        │   10 untagged CORE services (VERIFIED live)   │
                        │  nginx · postgres · qdrant · memory-api ·     │
                        │  minio · centrifugo · mcp-brain ·             │
                        │  mcp-gateway · mcp-scraper · brain-janitor    │
                        └─────────────────────┬─────────────────────────┘
                                               │
              ┌────────────────────────────────┼─────────────────────────────────┐
              ▼                                ▼                                 ▼
   POST /v1/auth/local/register     GET /v1/media/upload (doc)         GET /oauth/authorize
   POST /v1/auth/local/login        → memory_items + Qdrant vector      (ChatGPT/Claude.ai connector
   → xbt_ token (LAUTH-02:            (EMBEDDINGS_PROVIDER=local,        entry point)
     indistinguishable principal)      keyless, Phase 19)                      │
              │                                │                               ▼
              │                                ▼                    ┌───────────────────────┐
              │                     GET /v1/memory/search            │ TODAY: unconditional    │
              │                     (semantic retrieval, keyless)    │ redirect → GitHub OAuth │
              │                                                      │ (GITHUB_APP_CLIENT_ID)  │
              ▼                                                      │ ██ BLOCKS SC#3 on a     │
   chrome-extension (clip)                                           │    zero-key install ██  │
   ██ hardcoded Google/GitHub                                        │ NEEDS: local-auth branch│
      sign-in only — no local-                                       │ mirroring auth_local.py │
      auth / manual-token path ██                                    └───────────────────────┘
   NEEDS: a connect option that
   calls /v1/auth/local/login
   (or accepts a pasted xbt_)
```

A reader can trace: the operator boots the compose core (verified 10 services), then every SC#3 action is a call against `memory-api`. Local auth, document upload, and semantic retrieval are ALREADY zero-key end to end (Phase 18 + Phase 19 delivered them). The two red boxes are the concrete, verified gaps this phase must close (or explicitly descope with the user) before SC#3 can be claimed true.

### Recommended Project Structure

No new top-level directories. Changes are additive/corrective within the existing layout:

```
apps/memory-api/app/routes/
└── oauth_authorize.py       # ADD: a local-auth branch — when GITHUB_APP_CLIENT_ID is empty
                              #      (or always, as a second option), render a login form
                              #      instead of / alongside the GitHub redirect; converge into
                              #      the SAME _finalize_consent() used by the GitHub leg today.

chrome-extension/
├── popup.html                # ADD: a third connect option (local auth) alongside
│                              #      btn-signin-github / btn-connect-xbrain
└── background.js / popup.js  # ADD: MINT_AND_CONNECT variant that POSTs
                               #      /v1/auth/local/login instead of chrome.identity

.env.example                  # RESTRUCTURE: split "Required — minimal boot" into
                               #   "Required — OSS-light CORE boot" (10 services) vs.
                               #   "Required only if COMPOSE_PROFILES includes saas/integrations"
                               # FIX: move the whole MinIO block out of the stale
                               #   "SaaS-only / not part of OSS-light" section; mark
                               #   MINIO_ROOT_PASSWORD [required] (MinIO refuses to boot
                               #   below 8 chars — verified, not a soft default)

docs/INSTALL.md (new, or restructure README.md's Quickstart)
                               # prereqs → provision → clone → generate secrets →
                               # docker compose up → first-run register → verify

infrastructure/scripts/
└── verify-phase16.sh (new)   # mirrors verify-phase15.sh's hermetic-env-file +
                               # real-docker-compose-up pattern, extended to ALL 10
                               # core services (not just the 5 pull-only ones)
```

### Pattern 1: Extending the OAuth-AS authorize flow with a local-auth branch

**What:** `oauth_authorize.py`'s `GET /oauth/authorize` currently has exactly one path: build a signed state token, redirect to GitHub. The fix is to branch on `settings.GITHUB_APP_CLIENT_ID` (or offer both), render a login form for the local-auth case, verify against the same functions `auth_local.py` already uses, then converge into the EXISTING `_finalize_consent()` — no new consent/team-selection logic needed.
**When to use:** Any zero-external-key install where `GITHUB_APP_CLIENT_ID` is empty and the operator still wants the ChatGPT-web/Claude.ai connector to work.
**Example (sketch, based on the exact functions already imported by `auth_local.py`):**
```python
# apps/memory-api/app/routes/oauth_authorize.py — proposed addition
from app.services.password_hash import verify_password, verify_decoy
from app.repos import local_credentials as local_credentials_repo
from app.repos import users as users_repo

@router.get("/oauth/authorize", response_model=None)
async def authorize(...):
    ...
    if not settings.GITHUB_APP_CLIENT_ID:
        # No GitHub App configured — render local-auth login instead of
        # redirecting into a GitHub client_id that doesn't exist.
        return _render_local_login_form(state_token)
    # existing GitHub redirect unchanged for installs that DO have a GitHub App
    ...

@router.post("/oauth/authorize/local", response_model=None)
async def authorize_local_submit(email: str = Form(...), password: str = Form(...), state: str = Form(...), session=Depends(get_session)):
    st = _verify_state(state)
    user = await users_repo.get_by_email(session, email)
    creds = await local_credentials_repo.get(session, user.id) if user else None
    if not user or not creds or not verify_password(password, creds.password_hash):
        if not creds:
            await verify_decoy()  # timing-comparable, mirrors auth_local.py's login()
        return _error_page("Invalid email or password.", status=401)
    # Converge into the SAME post-auth stage the GitHub leg uses.
    post_state = _sign_state({**st, "user_id": str(user.id), "stage": "post_github"})
    return await _finalize_consent(session, state=post_state, team_scope=...)  # or team-selection page
```
This deliberately reuses `_finalize_consent`'s existing team-membership check and PKCE code-issuance — it is the SAME convergence pattern Phase 18's `set_password()` already established for "any authenticated principal reaches the same downstream shape" (D-18-05).

### Pattern 2: Minimal zero-key "clip" proof (if the full extension UI is descoped this phase)

**What:** Rather than building a full local-auth connect UI in the Chrome extension this phase (a real but bounded scope decision), a documented, scripted `curl` call against the already-core, already-keyless `POST /v1/memory/upsert` (or the media upload endpoint) with a local-auth-minted `xbt_` token is a legitimate, provable "content from a web page landed in memory with zero external keys" proof — since the underlying capability (write with a local-auth token) already works today.
**When to use:** If the CONTEXT-phase discussion with the user decides the polished, in-UI clip flow is Phase 20 scope (Phase 20 SC#3 already explicitly promises *"Clip-to-memory... reachable from the standalone web app, not only from the browser extension"* — implying the team already knows today's clip is extension-only and OAuth-gated).
**Example:**
```bash
XBT=$(curl -s -X POST http://localhost:8000/v1/auth/local/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"op@example.com","password":"..."}' | python -c "import json,sys;print(json.load(sys.stdin)['xbt_token'])")
curl -s -X POST http://localhost:8000/v1/memory/upsert \
  -H "Authorization: Bearer $XBT" -H "Content-Type: application/json" \
  -d '{"item": {..., "content": "<clipped page text>", "source": "manual-clip", ...}}'
```
This is an **Open Question for CONTEXT/discuss-phase**, not a research decision — see below.

### Anti-Patterns to Avoid

- **Testing the OSS-light boot by grepping `docker-compose.yml` for `profiles:` tags.** Phase 15's own gate-lesson framing already established this: a check that never traverses the real deployment path proves nothing. Phase 16's SC#1/SC#2 test MUST run a real `docker compose build && up` of all 10 core services (Phase 15's gate deliberately only booted the 5 pull-only ones and used a no-build harness for `memory-api` alone — Phase 16 needs the FULL 10, including the other 4 `build:` services it never exercised: `mcp-gateway`, `mcp-scraper`, `mcp-brain`, `brain-janitor`).
- **Declaring SC#3 done because the connector's `.well-known` metadata returns 200.** That only proves AS *discovery* works (already verified by Phase 15's check g). It does NOT prove a user can actually complete the sign-in leg with zero external keys — that requires walking `/oauth/authorize` through to a minted code, which today dead-ends at the GitHub redirect.
- **Building a full multi-arch CI/registry pipeline this phase.** That is explicitly Phase 17's charter (REL-01/02); duplicating it here is scope creep against the roadmap's own phase boundaries.
- **Treating `.env.example`'s `[required]`/`[optional]` tags as authoritative without cross-checking `app/config.py`.** They have already drifted once (MinIO); trust the field_validators and the compose file's actual variable usage over the doc's own tags until the doc is fixed.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|--------------|-----|
| Local-auth verification for the OAuth-AS branch | A second password-hashing/verification path | `app/services/password_hash.py::verify_password` + `verify_decoy` (Phase 18) | Already timing-safe, already argon2id, already tested (27 tests in `test_local_auth.py`) — a second implementation is a second attack surface |
| Secret generation guidance in install docs | A custom secret-generation script | The project's existing `openssl rand -hex 32` / `openssl rand -base64 48` convention (already used throughout `.env.example`'s inline comments and the README's Quickstart) | Consistent with what's already documented; no new tooling to explain |
| A "readiness" check for the install docs' final step | A custom polling script | `docker compose ps` (already a `make ps` target) + curl against `/v1/healthz`, mirroring `verify-phase15.sh`'s own polling pattern | Reuses an established, already-tested pattern instead of inventing a new one |
| TLS termination for a real deploy | A custom nginx TLS config or certbot integration inside the compose stack | Document the existing convention explicitly: nginx never listens on 443 in this compose file (verified — `infrastructure/docker-compose.yml`'s `nginx` service only publishes `80:80`); TLS terminates externally (Cloudflare, in production) which loops back to port 80. This is a **pre-existing, load-bearing fact** `verify-phase15.sh`'s own check (f) already documents as "structurally impossible to satisfy in ANY isolated environment... before DNS/TLS is configured" | Building TLS into the compose stack would be new infra Phase 16 doesn't need — self-hosters using a different TLS strategy (Caddy, a different CDN) just need this documented, not solved in-repo |

**Key insight:** every piece of "new" functionality this phase seems to need (local-auth verification, secret generation guidance, readiness checks) already has a working implementation somewhere in the codebase from Phases 14/15/18/19. The actual net-new work is wiring two auth entry points to reuse it, fixing documentation drift, and proving the whole thing boots — not building anything from scratch.

## Common Pitfalls

### Pitfall 1: The ChatGPT-web connector is GitHub-only today (SC#3 blocker)
**What goes wrong:** A zero-external-key install's `/oauth/authorize` redirects to `https://github.com/login/oauth/authorize?client_id=&...` — GitHub rejects the empty `client_id`, and the connector setup fails with no actionable error surfaced to the operator.
**Why it happens:** `apps/memory-api/app/routes/oauth_authorize.py:128-138` has exactly one code path, added when the connector was built (quick task 260604-glo) against the only auth method that existed then (GitHub App). Phase 18 added local auth afterward but never touched this file (explicitly, per its Scope Boundary rule — see `docs/local-auth-recovery.md`'s "Known limitations (D-18-07)").
**How to avoid:** Add a local-auth branch (see Architecture Patterns, Pattern 1) as an explicit Phase 16 task, OR get an explicit CONTEXT.md decision from the user to descope this specific piece of SC#3 (e.g., "the connector requires a GitHub App for now; zero-key install still gets local auth + doc analysis + retrieval + clip"). Do not silently claim SC#3 done without one or the other.
**Warning signs:** A `verify-phase16.sh` check that only hits `/.well-known/oauth-authorization-server` (200 OK) and calls the connector "verified" — this is the exact "check that never traverses the real path" failure mode this project's own gate-lesson culture warns against repeatedly (Phase 15, 18 headers both open with this warning).

### Pitfall 2: The Chrome extension cannot sign in with zero external keys (SC#3 blocker)
**What goes wrong:** "Clip a web page into memory" (an explicit SC#3 action) requires the Chrome extension to be signed in; the extension's only sign-in paths are Google `chrome.identity`/`launchWebAuthFlow` (hardcoded `CLIENT_ID` at `chrome-extension/background.js:263`) and GitHub `launchWebAuthFlow` (the GitHub App). Neither is available on a zero-key install.
**Why it happens:** The extension predates Phase 18's local auth by many phases; nobody has gone back to add a third connect option.
**How to avoid:** Either (a) add a minimal local-auth connect UI to the extension (real, bounded scope — a login form POSTing to `/v1/auth/local/login`, storing the returned `xbt_token` exactly like the existing flows do), or (b) get an explicit CONTEXT.md decision to prove the "clip" action via a documented `curl`/API call instead of the extension UI for this phase, deferring the polished in-extension flow to whenever Phase 20's "clip reachable from the standalone web app" ships. Phase 20's own SC#3 language already signals the team expects to fix "extension-only" clip later — but Phase 16's SC#3 needs SOME zero-key clip proof now.
**Warning signs:** A verify script that "clips" by directly calling the API with a hand-minted token and calling it done — technically proves the backend capability but not that a real operator following only install docs can do it through the shipped UI. Be honest in the RESEARCH/PLAN about which one is being claimed.

### Pitfall 3: `.env.example`'s "Required — minimal boot" section predates Phase 15's profile split
**What goes wrong:** An operator following the current file literally fills in `MEILI_MASTER_KEY`, `OPENWEBUI_SECRET_KEY`, `JWT_SECRET`, `JWT_REFRESH_SECRET`, `CREDS_KEY`, `CREDS_IV`, `MONGO_URI`, `MEILI_HOST`, `LIBRECHAT_MONGO_URI`, `PIPELINE_API_KEY`, `OPENAI_API_BASE_URL` — all `saas`-profile-only secrets (LibreChat/Open WebUI, which don't even run with `COMPOSE_PROFILES` unset) — believing them required for the OSS-light core. This directly undermines SC#1's "no source reading" promise: the doc itself is the misleading source.
**Why it happens:** `.env.example` was written in Phase 14 (Portability), before Phase 15 (Edition Mechanics) introduced `COMPOSE_PROFILES`. Phase 15 prepended a `COMPOSE_PROFILES` explainer comment block at the TOP of the file (lines 9-26, correctly listing the 10 core services) but never went back and re-tagged the REST of the file's `[required]`/`[optional]` markers to match.
**How to avoid:** Restructure `.env.example` into explicit sections: "Required for the OSS-light CORE (COMPOSE_PROFILES unset)" vs. "Required only if `saas` is enabled" vs. "Required only if `integrations` is enabled" — or add inline profile tags. This is squarely PORT-02/PKG-01 territory and should be an explicit Phase 16 task.
**Warning signs:** An install-docs "Fill your .env" step that takes 20+ minutes and asks for LibreChat secrets on a stack that will never run LibreChat.

### Pitfall 4: `MINIO_ROOT_PASSWORD` is mislabeled `[optional]` but MinIO crashloops without it
**What goes wrong:** `.env.example` lines 288-311 file the entire MinIO credential block under a **stale header, "=== SaaS-only / not part of OSS-light ==="**, even though Phase 15 explicitly promoted `minio` to the untagged core (`infrastructure/docker-compose.yml`'s own comment: *"MinIO is PROMOTED into the core... tagging it `integrations` would 503 `/v1/media/upload` in every OSS-light install"*). `MINIO_ROOT_PASSWORD` itself has no compose-level default (`${MINIO_ROOT_PASSWORD}`, no `:-` fallback) and is tagged `[optional]`. **MinIO refuses to start with a root password under 8 characters** [VERIFIED via web search — MinIO's own startup error: `"Access key length should be at least 3, and secret key length at least 8 characters"`, GitHub minio/minio discussions]. An operator who reads "[optional] = leave blank" (per the file's own legend) gets a silently crashlooping core `minio` container.
**Why it happens:** Same root cause as Pitfall 3 — the file wasn't re-scoped after MinIO's Phase 15 promotion.
**How to avoid:** Move the MinIO block into the "Required — OSS-light CORE boot" section and tag `MINIO_ROOT_PASSWORD` `[required]`. This is a one-paragraph fix with outsized correctness value.
**Warning signs:** `docker compose ps` shows `minio` in a restart loop; `docker logs xbrain-minio` shows the credential-length error; `memory-api`'s `/v1/media/upload` 503s (as the compose file's own comment predicts).

### Pitfall 5: `docker compose --env-file` does not reliably parse inline `# comments` after non-blank values
**What goes wrong:** Building a hermetic test `.env` file (for a `verify-phase16.sh` gate, mirroring Phase 15/18's pattern) by keeping `.env.example`'s own inline-commented lines verbatim can silently drop values compose actually needs, producing a wall of `"variable is not set"` warnings that mask which vars genuinely matter.
**Why it happens:** `.env.example` already has ONE documented instance of this exact class of bug (lines 264-268, re: `MINIO_URL`/`MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` — "docker compose's env-file parser does not strip an inline `#` comment on a genuinely blank value"). Empirically reproduced in this research session across a much broader set of vars when feeding a lightly-modified copy of `.env.example` to `docker compose ... --env-file` directly.
**How to avoid:** Follow `verify-phase15.sh`'s established pattern exactly: build the hermetic env file with `grep -vE` to strip lines you're overriding, then `cat >>` clean `KEY=value` lines with NO inline comments for anything the test needs to actually resolve. Do not feed `.env.example` to `docker compose --env-file` verbatim in a test harness.
**Warning signs:** A test that "passes" because it only checked `docker compose config --services` (which doesn't need variable VALUES to resolve, only variable NAMES to exist) but would fail the moment it tried an actual `up`.

### Pitfall 6: The `~8.5s` Neo4j DNS-timeout at `memory-api` startup is expected, not a defect
**What goes wrong:** A first-time operator (or an overly aggressive verify-script timeout) sees `memory-api` take longer than expected to become healthy on a Neo4j-absent OSS-light boot and assumes something is broken.
**Why it happens:** `infrastructure/docker-compose.yml`'s own comment (repeated at both `memory-api` and `brain-janitor`): removing the `depends_on: neo4j` edge (required for Compose to accept profile-gated dependencies) means `NEO4J_URI` is still set to `bolt://neo4j:7687` even when no `neo4j` container exists, so the driver's connection attempt DNS-times-out (~8.5s) before the app degrades cleanly. **"Not a defect — do not 'fix' it by re-adding the dependency."**
**How to avoid:** Document this explicitly in install docs and size healthcheck `start_period`/timeouts generously enough to not flap on it (the current `memory-api` healthcheck already has `start_period: 30s`, comfortably above the ~8.5s cost).
**Warning signs:** A verify script with an aggressive per-service boot timeout that intermittently fails only on `memory-api`, never on the pull-only services.

### Pitfall 7: Git Bash / Windows path translation breaks bind-mount-based test harnesses
**What goes wrong:** A no-build test harness (bind-mounting real source into a stock `python:3.12-slim` container, the pattern Phase 15/18 both used to test `memory-api` without an image build) silently mounts an EMPTY directory if the POSIX-looking `$PWD` path gets MSYS-rewritten.
**Why it happens:** Documented and hard-guarded in both `verify-phase15.sh` and `verify-phase18.sh`: "a POSIX `$PWD` path silently fails to bind-mount from Git Bash." Both existing gates use `cygpath -w` + `MSYS_NO_PATHCONV=1` and an explicit mount-guard assertion (`test -f /repo/apps/memory-api/app/main.py`) that FAILS (never skips) if the mount didn't land.
**How to avoid:** Reuse the exact `HOST_REPO="$(cygpath -w "$REPO_ROOT")"` + `MSYS_NO_PATHCONV=1 docker run ... -v "${HOST_REPO}:/repo:ro"` + mount-guard pattern verbatim in `verify-phase16.sh` if it needs a no-build harness for anything.
**Warning signs:** Every assertion downstream of the mount "passes" against an empty `/repo` — this is the single most repeated pitfall across every prior gate script in this repo.

### Pitfall 8: LICENSE file (MIT) contradicts the locked design decision (AGPLv3 + CLA)
**What goes wrong:** Install docs that cite the repo's license (a reasonable thing to do in a README) would state something the project's own locked decisions say is wrong.
**Why it happens:** `LICENSE` at repo root [VERIFIED: read in this session] still reads `MIT License / Copyright (c) 2026 GrooveOS`. `.planning/features/open-core-edition-design.md`'s locked decisions table (2026-07-11) states: *"License | Code license | **AGPLv3 + CLA.**"* Nobody has updated the `LICENSE` file since that decision.
**How to avoid:** Flag for the user/CONTEXT-phase — this is a legal/brand decision, not something research should silently resolve, but install docs must not propagate a stale claim. At minimum, note it as an open item; do not have the new install docs assert "MIT" without checking with the user first.
**Warning signs:** None at runtime — this is a documentation-accuracy risk, not a functional one.

## Code Examples

### Real, verified 10-service OSS-light core (this session, live `docker compose config`)
```bash
# Source: this research session — ran against the real infrastructure/docker-compose.yml
$ docker compose -f infrastructure/docker-compose.yml --env-file <hermetic-env> config --services | sort
brain-janitor
centrifugo
mcp-brain
mcp-gateway
mcp-scraper
memory-api
minio
nginx
postgres
qdrant

$ docker compose -f infrastructure/docker-compose.yml --env-file <hermetic-env> config --profiles | sort
integrations
ops
saas
```
This matches the ROADMAP/`.env.example`/design-doc claims of "10 untagged core services, no `pro` profile" exactly — HIGH confidence, independently re-verified (Phase 15's own gate already proved this once; this session reproduced it live rather than trusting the prior gate's PASS status).

### The Phase 15/18 hermetic-env-file pattern (reuse this, don't rebuild it)
```bash
# Source: infrastructure/scripts/verify-phase15.sh:104-115 — the established, working pattern
ENVF="$(mktemp)"
grep -vE '^(OAUTH_ISSUER_URL|OAUTH_RESOURCE_URL|POSTGRES_PASSWORD|DATABASE_URL|NEO4J_PASSWORD|MINIO_ROOT_PASSWORD|BRIDGE_SHARED_SECRET|XBRAIN_BASE_DOMAIN|EDITION|COMPOSE_PROFILES)=' .env.example > "$ENVF"
cat >> "$ENVF" <<'EOF'
OAUTH_ISSUER_URL=https://api.p16.test
OAUTH_RESOURCE_URL=https://mcp.p16.test/mcp
POSTGRES_PASSWORD=p16testpassword
DATABASE_URL=postgresql+asyncpg://xbrain:p16testpassword@postgres:5432/xbrain
NEO4J_PASSWORD=p16testpassword
MINIO_ROOT_PASSWORD=p16testpassword12   # >= 8 chars — Pitfall 4
BRIDGE_SHARED_SECRET=p16testbridgesecret
XBRAIN_BASE_DOMAIN=p16.test
EOF
```

### The GitHub-only OAuth-AS redirect (the exact code to change)
```python
# Source: apps/memory-api/app/routes/oauth_authorize.py:128-138 (verbatim, this session)
    # Redirect into GitHub sign-in using memory-api's OWN callback (never the
    # Claude.ai redirect_uri). Reuse the xbrain GitHub App client id.
    github_redirect = settings.OAUTH_ISSUER_URL.rstrip("/") + _GITHUB_REDIRECT_PATH
    gh_params = {
        "client_id": settings.GITHUB_APP_CLIENT_ID,   # EMPTY on a zero-key install
        "redirect_uri": github_redirect,
        "state": state_token,
        "scope": "read:user user:email",
    }
    gh_url = "https://github.com/login/oauth/authorize?" + urlencode(gh_params)
    return RedirectResponse(url=gh_url, status_code=302)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|-------------------|---------------|--------|
| README's Quickstart: "Requirements: an Ubuntu VM with Docker + Docker Compose, a Google OAuth client, and a GitHub App (for auth)" | Local email/password auth is the OSS default (Phase 18); Google/GitHub are opt-in | Phase 18 (2026-07-13) | README is now STALE and must be rewritten as part of Phase 16's install-docs work — it is the most likely home for those docs and currently states the opposite of the phase's own goal |
| `apps/memory-api/app/embedders.py` hard-raised without `OPENAI_API_KEY` | Local, keyless, in-container embeddings by default (`EMBEDDINGS_PROVIDER=local`) | Phase 19 (2026-07-18) | SC#3's "keyless semantic retrieval" is ALREADY delivered — nothing to build here, only to prove/document |
| `.env.example` had one flat "Required" list | Phase 15 added a `COMPOSE_PROFILES` explainer at the top, but the rest of the file was never re-scoped | Phase 15 (2026-07-13), never completed | The single biggest concrete documentation-drift finding of this research (Pitfalls 3-4) |
| OAuth-AS connector sign-in built against GitHub App only | Still true today — Phase 18's local auth never reached this file | Quick task 260604-glo (pre-Phase-18), unchanged since | Documented as a known gap (D-18-07) but not yet fixed — Phase 16 is the first phase whose own success criteria require fixing it |

**Deprecated/outdated:** The README's current Quickstart section (Google OAuth + GitHub App as hard requirements) is functionally deprecated by Phase 18 but has not been updated.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|----------------|
| A1 | MinIO refuses to boot with `MINIO_ROOT_PASSWORD` under 8 characters (crashloop, not a soft default). Verified via WebSearch citing a MinIO GitHub discussion's exact error string, not via a live boot test against THIS project's `cgr.dev/chainguard/minio` image in this session. | Common Pitfalls #4 | LOW-MEDIUM — even if the exact threshold differs slightly by MinIO version, the underlying finding (the var is mislabeled `[optional]` under a stale "SaaS-only" header while the service is core) stands regardless of the exact character count; the planner should still fix the label and could trivially re-verify the exact boot behavior with one `docker run` during Wave 0. |
| A2 | The recommended fix for Pitfall 1 (local-auth branch in `oauth_authorize.py`) is scoped correctly as "wiring, not new crypto" — based on reading `auth_local.py`'s imports and reasoning by analogy to Phase 18's D-18-05 convergence pattern, not by actually building and testing the branch in this session. | Architecture Patterns, Pattern 1 | MEDIUM — the sketch is directionally sound (reuses real, existing functions with real signatures verified by direct file read) but the planner/executor may find edge cases (e.g., CSRF on the login form, rate-limiting the new POST route, the multi-team consent-page interaction) that need their own design pass. Flag as a real task, not a copy-paste patch. |
| A3 | Building on the target host (VM) remains viable even though the VM is currently stopped — based on project memory (`project_xbrain_vm_paused_cost.md`, 2026-06-18) stating the VM was terminated for cost, not on a live check against the actual VM in this session (no VM credentials/access available to this research agent). | Summary, Architecture Patterns | LOW — if the VM has since been restarted or replaced, the `make deploy` build-on-host recommendation still holds structurally (it targets whatever `VM_HOST` resolves to); only the "VM is currently stopped, budget an arm64-local-boot proxy for SC#1" framing would need updating, which the planner can verify with one `ssh` check before committing to a test design. |

**Planning implication:** None of these block planning. A1 and A3 are both cheaply re-verifiable in Wave 0 (a single `docker run` for A1, a single `ssh`/`gcloud` check for A3). A2 should become its own explicit task in the plan, not be treated as "just docs."

## Open Questions

1. **Does "zero external keys" in SC#3 include `ANTHROPIC_API_KEY`, or only the OAuth-identity/embeddings keys (Google, GitHub App, OpenAI)?**
   - What we know: SC#3's own parenthetical explicitly names only *"no OpenAI, no Google, no GitHub App"* — it does not name Anthropic. The design doc's locked "Single-key operation" decision states *"One key — Anthropic OR OpenAI OR Grok — drives the whole system. Chat uses whichever key is set; embeddings run locally (keyless)... relevance filter falls back to heuristic when no Anthropic key"* — i.e., a chat-model key was never meant to be eliminated, only auth and embeddings keys.
   - What's unclear: whether the phase's own GOAL text ("chat via the existing surfaces... with ZERO external keys") is stricter than SC#3's own parenthetical, and whether "chat" in that context means the message-transport (team_chat post/receive via Centrifugo, provably zero-key — confirmed no LLM call in that path) or an actual `@agent`-generated reply (which needs one LLM key by design).
   - Recommendation: Design the SC#1 clean-install/zero-key test to exercise "chat" as plain message post/receive (proving the transport is zero-key, which it genuinely is), NOT the `@agent` mention (which by the project's own locked design intentionally still needs one LLM key). Get this confirmed explicitly in CONTEXT.md rather than assumed.

2. **Should Phase 16 itself fix the two SC#3 code gaps (oauth_authorize.py, chrome-extension), or descope them with a documented substitute proof?**
   - What we know: both gaps are real, verified, and block a literal reading of SC#3. Fixing the OAuth-AS gap is a contained, well-scoped task (Pattern 1). Fixing the extension gap is larger (new UI, new connect flow, extension re-packaging/reload for testers) and arguably overlaps Phase 20's explicit charter ("clip reachable from the standalone web app, not only the browser extension").
   - What's unclear: whether the user considers "prove clip works via a documented API call, defer the polished extension UI to Phase 20" an acceptable interpretation of SC#3, given Phase 20's own SC#3 language already signals awareness that today's clip is extension-only.
   - Recommendation: Surface this explicitly in CONTEXT.md/discuss-phase as a locked decision, not something the plan should silently resolve either way. This directly changes plan scope and wave structure.

3. **What is the actual, current state of the production VM (`VM_HOST`) — stopped, running, or replaced?**
   - What we know: project memory (2026-06-18) says the VM was terminated to cut cost during a pivot. `Makefile`'s `VM_HOST ?= __VM_HOST__` placeholder and `.env`'s `VM_HOST=__VM_HOST__` in `.env.example` suggest it may not even be configured in the checked-out `.env` right now.
   - What's unclear: whether SC#1's "fresh VM" clean-install test should target a newly-provisioned VM (closest to the literal SC#1 language: "provisions a fresh VM"), or accept the arm64-local-`docker compose up` proxy as sufficient for Phase 16, with a real-VM run as a documented follow-up (mirroring Phase 19's amd64-RSS deferral pattern).
   - Recommendation: Confirm VM state with the user before planning; if restarting a VM for this test is out of budget, explicitly document the arm64-local-boot-as-proxy decision in CONTEXT.md so the plan's acceptance criteria are honest about what was actually tested.

4. **Should `.env.example` be split into two files (e.g., `.env.oss-light.example` + `.env.example`) or reorganized in place?**
   - What we know: the existing single-file structure with corrected section headers/tags (recommended fix for Pitfalls 3-4) is the smaller, lower-risk change and matches PORT-02's original "slim, documented OSS `.env.example`" framing.
   - What's unclear: whether a SEPARATE, even-slimmer OSS-light-only file would better serve SC#1's "no source reading" test (an operator literally cannot get confused by seeing `saas`-only vars at all) versus the maintenance cost of keeping two files in sync.
   - Recommendation: Reorganize in place with clear section headers (lower risk, single source of truth) unless the planner/CONTEXT-phase has a strong reason to fork the file.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|--------------|------------|---------|-----------|
| Docker Engine | Local SC#1 proxy boot test | Yes [VERIFIED: `docker --version` this session] | 29.6.1 | — |
| Docker Compose v2 | All compose-based checks | Yes [VERIFIED: `docker compose version` this session] | v5.2.0 | — |
| Production VM (`VM_HOST`) | The literal "fresh VM" SC#1 test | **Unknown / likely stopped** per project memory (2026-06-18 cost-cutting note); not verified live in this session (no VM credentials available to this research agent) | — | arm64-local `docker compose up` as the SC#1 proxy (see Open Question 3), matching Phase 15/18/19's existing pattern of deferring the true amd64/prod-path proof to a documented follow-up |
| Container registry (GHCR or similar) for SC#4's "tagged multi-arch images" | Literal SC#4 wording | Not configured — no CI workflow, no `buildx`/`docker push` anywhere in the repo [VERIFIED: repo-wide grep this session] | — | Build-on-target-host via the existing `make deploy` recipe, satisfying SC#4's explicit "(or a documented build-on-VM path)" alternative |
| A real domain + TLS-terminating proxy (Cloudflare or equivalent) | The literal ChatGPT.com/Claude.ai connector click-through (not the automated proxy test) | Not applicable to the automated SC#1 test; required only for the real, manual UAT of the connector against a live URL | — | Document as a manual UAT step, not part of the automated gate (mirrors Phase 18's browser-UAT deferral to Phase 20) |

**Missing dependencies with no fallback:** None — every gap above has a documented, precedented fallback already used elsewhere in this project's own phase history.

**Missing dependencies with fallback:**
- Production VM → arm64-local `docker compose up` proxy test.
- Container registry → build-on-host (`make deploy`'s existing pattern).
- Real domain/TLS → manual UAT step, deferred from the automated gate.

## Validation Approach

> `.planning/config.json` has `workflow.nyquist_validation: false`, so the templated Nyquist Req→Test map is omitted. This project's own established convention (Phase 15/18/19's verify scripts) already IS a rigorous, "gate lesson"-driven test plan — the concrete checks below are what a `verify-phase16.sh` (or equivalent) should assert, reusing those scripts' proven harness patterns rather than inventing new ones.

### What "prove it" means for PKG-01, concretely

| # | Check | Against | Asserts |
|---|-------|---------|---------|
| 1 | `docker compose config --services` / `--profiles` (COMPOSE_PROFILES unset) | Real compose config, hermetic env file (Phase 15 pattern) | Exactly the 10 named core services, exactly `integrations ops saas` profiles — reproduces this session's live verification, catches any future service-classification drift |
| 2 | `docker compose build && up -d` of ALL 10 core services (not just the 5 pull-only ones Phase 15 tested) | Real Docker, arm64 dev host (documented as the SC#1 proxy per Open Question 3) | All 10 reach Docker `healthy`; this is the FIRST time `mcp-gateway`, `mcp-scraper`, `mcp-brain`, `brain-janitor` are compose-built and booted together as a set — Phase 15's gate deliberately never did this |
| 3 | `POST /v1/auth/local/register` → `POST /v1/auth/local/login` against the live-booted core | Real `memory-api` from check 2 | SC#3's "registers via local auth" — zero Google/GitHub/GitHub-App config present in the hermetic env |
| 4 | `POST /v1/media/upload` (a real small file) then `GET /v1/memory/search?q=...` | Same live core | SC#3's "uploads/analyzes a document... ingested and semantically retrieved" — with `EMBEDDINGS_PROVIDER=local` (default) and NO `OPENAI_API_KEY` set, proving the Phase 19 keyless path end to end in the FULL compose context (not just memory-api's own test suite) |
| 5 | `GET /.well-known/oauth-authorization-server` = 200; THEN a full `GET /oauth/authorize?...` walk with `GITHUB_APP_CLIENT_ID` empty | Same live core | If Pitfall 1's fix ships this phase: the local-auth branch reaches a minted authorization code. If NOT fixed this phase (per Open Question 2's resolution): this check should explicitly assert and DOCUMENT the current 302-to-broken-GitHub behavior as a known limitation, not silently skip it |
| 6 | The clip proof (either the extension UI walkthrough as a manual UAT, or the scripted `POST /v1/memory/upsert` proof per Pattern 2 — resolved by Open Question 2) | Same live core | SC#3's "clips a web page into memory" with zero external keys |
| 7 | `docker compose ps` — zero opt-in (`integrations`/`saas`/`ops`) containers present | Same live core | SC#2's "COMPOSE_PROFILES unset" boundary — reuses Phase 15's own deny-list pattern (`OPT_IN_CONTAINERS`) verbatim |
| 8 | `.env.example` static audit: every var referenced by the 10 core services' `environment:` blocks (via `docker compose config --format json`) has NO `[optional]` tag if it has no compose-level default AND the consuming service would fail health without it | `.env.example` content + `docker compose config --format json` | Directly catches regressions of Pitfalls 3/4 — a mechanical, repeatable check rather than a one-time manual fix |

### Wave 0 Gaps
- [ ] `infrastructure/scripts/verify-phase16.sh` (new) — does not exist yet; must be built following the Phase 15/18 hermetic-env-file + mount-guard conventions (Common Pitfalls #5, #7).
- [ ] A decision (CONTEXT.md) on Open Questions 1, 2, 3, 4 before the plan can commit to exact wave scope — these are genuine scope calls, not implementation details.
- [ ] If Pattern 1 (OAuth-AS local-auth branch) is in scope: no existing test file covers `oauth_authorize.py` at all today (only `test_oauth_resolve.py` exists, and that's in `apps/mcp-brain`, testing the resource-server side, not this authorize-flow file) — a new `test_oauth_authorize_local.py` in `apps/memory-api/tests/` would be needed.
- [ ] If Pattern 2 fallback (scripted clip proof) is chosen instead of extension UI work: no new test infra needed beyond check 6 above.

## Security Domain

`security_enforcement` is not set in `.planning/config.json` (absent = enabled).

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-------------------|
| V2 Authentication | Yes — directly, if Pattern 1 is built | Reuse `app/services/password_hash.py` (argon2id, already timing-safe via `verify_decoy`) — do NOT hand-roll a second verification path for the OAuth-AS branch |
| V3 Session Management | Yes | The OAuth-AS's existing signed-state-JWT pattern (`_sign_state`/`_verify_state`, HS256 via `BRIDGE_SHARED_SECRET`, 10-minute TTL) already exists and should be reused unchanged for a local-auth branch — do not invent a second state-token scheme |
| V4 Access Control | Yes | Team-membership check (`get_membership`) in `_finalize_consent` already applies regardless of which auth branch resolved `user_id` — the local-auth branch must converge into this SAME function, not duplicate its authorization check |
| V5 Input Validation | Yes (narrow) | A new `POST /oauth/authorize/local` form endpoint needs the same input bounds `auth_local.py`'s `LoginBody` already enforces (email/password length limits) — reuse the Pydantic model, don't redefine |
| V6 Cryptography | No new surface | No new crypto — reuses existing argon2id hashing and existing HS256 state-signing |

### Known Threat Patterns for this phase's stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| User enumeration via the new local-auth OAuth branch's error responses | Information Disclosure | Mirror `auth_local.py`'s existing generic `"Invalid email or password."` message + `verify_decoy()` timing-comparable branch — do NOT give a distinct error for "no such user" vs "wrong password" (the exact class of bug `T-18-03-02/03/04` already fixed once in `auth_local.py`; a new endpoint must not reintroduce it) |
| CSRF on a new `POST /oauth/authorize/local` form | Tampering | The existing signed `state` JWT (short TTL, HS256, server-issued) already provides CSRF-equivalent protection for the GitHub leg's consent POST — the local-auth POST must carry and verify the same `state` token, not skip it |
| Secrets shipped as recognizable placeholders in `.env.example` (`__FILL_RANDOM_32_CHARS__`, etc.) never rotated by a lazy operator | Tampering / Elevation of Privilege | Already an existing risk across the whole project, not new to this phase — install docs should explicitly instruct `openssl rand` generation for every `[required]` secret and warn against shipping the literal placeholder text, consistent with the README's existing Quickstart guidance |
| Open redirect via the connector's `redirect_uri` | Spoofing | Already mitigated — `oauth_store.is_redirect_uri_registered` checked BEFORE any redirect, both on `GET /oauth/authorize` and again defense-in-depth in `_finalize_consent`. No change needed; just don't weaken this when adding the local-auth branch |
| CORS wildcard misconfiguration in a fresh install | Spoofing / Information Disclosure | Already mitigated by `app/config.py`'s `_reject_wildcard_cors` field_validator (functionally probes the regex against a hostile origin) — install docs just need to explain what to set `CORS_ALLOWED_ORIGIN_REGEX` to for a real domain |

## Sources

### Primary (HIGH confidence)
- Direct file reads (this session): `CLAUDE.md`, `.planning/ROADMAP.md` (Phase 15/16/18/19/20 sections), `.planning/REQUIREMENTS.md`, `.planning/STATE.md`, `.planning/features/open-core-edition-design.md`, `.planning/phases/19-local-embeddings/19-RESEARCH.md`, `infrastructure/docker-compose.yml` (full, both halves), `.env.example` (full), `apps/memory-api/.env.example`, `apps/memory-api/app/config.py`, `apps/memory-api/app/main.py` (router registry), `apps/memory-api/app/routes/oauth_authorize.py` (full), `apps/memory-api/app/routes/auth_local.py`, `apps/memory-api/Dockerfile`, `apps/memory-api/pyproject.toml`, `apps/mcp-gateway/pyproject.toml`, `apps/mcp-scraper/pyproject.toml`, `apps/mcp-brain/pyproject.toml`, `apps/brain-janitor/pyproject.toml`, `infrastructure/scripts/verify-phase15.sh` (full), `infrastructure/scripts/verify-phase18.sh` (full), `infrastructure/scripts/preflight-env.sh` (full), `Makefile` (full), `README.md` (full), `LICENSE`, `docs/local-auth-recovery.md`, `docs/embeddings.md`, `chrome-extension/background.js`, `chrome-extension/popup.html`, `chrome-extension/popup.js`.
- Live command execution (this session): `docker --version`, `docker compose version`, `docker compose ... config --services`/`--profiles` against the real `infrastructure/docker-compose.yml` (reproducing and independently re-confirming Phase 15's own gate result), repo-wide `grep` for `ghcr.io`/`buildx`/`docker push`/registry usage (none found beyond Open WebUI's upstream image), repo-wide `grep` for `image: xbrain/` (confirms phase-numbered, not semver-tagged, local build tags).

### Secondary (MEDIUM confidence)
- WebSearch: MinIO `MINIO_ROOT_PASSWORD` minimum-length boot requirement (Pitfall 4 / Assumption A1) — confirmed via a MinIO GitHub discussion citing the server's own error string, not independently re-verified by booting THIS project's exact `cgr.dev/chainguard/minio` image in this session.

### Tertiary (LOW confidence)
- Project memory (`project_xbrain_vm_paused_cost.md`, 2026-06-18) regarding the production VM's stopped state — not independently re-verified against a live VM in this session (Assumption A3).

## Metadata

**Confidence breakdown:**
- Standard stack / no-new-deps claim: HIGH — this phase genuinely adds no new libraries; verified by reading every relevant `pyproject.toml`.
- 10-service core / 3-profile claim: HIGH — independently re-verified live via `docker compose config`, not just trusted from prior phase docs.
- The two SC#3 code blockers (oauth_authorize.py, chrome-extension): HIGH — both are direct file reads with exact line citations, not inferred from documentation.
- `.env.example` drift findings (Pitfalls 3-4): HIGH for the structural/labeling claim (directly read); MEDIUM for the exact MinIO password character threshold (WebSearch-sourced, not locally re-verified against this project's exact image — see A1).
- SC#4 artifact-shape recommendation: HIGH for "no existing registry/CI infra" (verified by repo-wide grep); MEDIUM for "build-on-VM is the right call" (a reasoned recommendation given the VM-stopped constraint, which is itself LOW-confidence/unverified live — see A3).
- Security domain: HIGH for "reuse existing patterns, don't hand-roll" (all cited patterns were directly read); the specific new-endpoint threat analysis (CSRF/enumeration) is a reasoned extension of already-proven Phase 18 patterns, not independently penetration-tested.

**Research date:** 2026-07-18
**Valid until:** ~14 days — this research is tightly coupled to the exact current state of `oauth_authorize.py`, `chrome-extension/`, and `.env.example`, all of which this same phase is expected to change. Re-verify the "current state" claims (not the architectural recommendations) if this research is consumed more than ~2 weeks after 2026-07-18, or immediately if any of Phases 15/18/19's shipped code has since been touched by an unrelated quick task.
