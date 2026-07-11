# Phase 14: Portability Foundation - Research

**Researched:** 2026-07-11
**Domain:** Config externalization / de-hardcoding refactor (no new feature surface)
**Confidence:** HIGH (all counts are fresh `rg`/Grep results against the live tree, not carried over from stale docs)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01 — Full cleanup scope (runtime AND docs) [Q1]:** De-hardcode `grooveos.app`, `aibrussels`, and the `"default"` team_scope across the WHOLE repo — runtime source **and** docs/planning/KB — NOT runtime-only. Runtime correctness is the blocking part; docs/KB/planning scrub is required but non-blocking for boot. Exception: the Haiku few-shot examples in `relevance_filter.py` may keep neutral example domains (`example.com`) — they are illustrative, not configuration.
- **D-02 — Public-URL config defaults → neutral, not brand [Q1]:** For public URL settings that are already env-driven (`MEMORY_API_EXTERNAL_URL`, `CENTRIFUGO_WS_URL_PUBLIC`, `SMTP_FROM`, `DRIVE_WEBHOOK_PUBLIC_URL`, etc.), replace the `*.grooveos.app` default with a neutral local default (e.g. `http://localhost:8000`, `noreply@example.com`) so a bare `docker compose up` boots without any config.
- **D-03 — OAuth identity URLs → empty + fail-fast [Q1]:** For identity-critical OAuth settings (`OAUTH_ISSUER_URL`, `OAUTH_RESOURCE_URL` in both `memory-api` and `mcp-brain`), default to empty string and **fail fast at boot with a clear error** when unset in a mode that needs them, rather than shipping a misleading `grooveos.app` default that breaks the connector silently.
- **D-04 — No auto-seed team [Q2-adjacent portability]:** Do NOT introduce an auto-created seed team at first boot. A fresh install has an empty brain; the first user creates their team via the existing `POST /v1/teams/self-solo` flow. Keep the `"default"` team_scope literal as a neutral fallback string for now (it is generic, not a brand, and not a portability blocker) — do not expand its use.
- **D-05 — True hardcodes get env fallbacks [Q1]:** The 2 real hardcodes with no env fallback — `notifications.py` `dashboard_url` default and the `noreply@grooveos.app` email footer — must read from config/env (reuse `SMTP_FROM` / add an `APP_PUBLIC_URL` setting) with neutral defaults.
- **D-06 — Slim, documented OSS `.env.example` [PORT-02]:** Produce a slim OSS `.env.example` an operator can fill without reading source: every required var documented with a one-line comment and a safe placeholder; group by concern (core / LLM keys / storage / optional integrations); mark which are required for a minimal boot vs optional. The current `.env.example` (~115 vars) is trimmed/reorganized for the OSS-light surface, not the full SaaS surface.
- **D-07 — Config-only portability is the acceptance bar [PORT-01]:** After this phase, pointing the whole stack at a new domain + new keys must require **zero source edits** — only `.env` / config changes. A grep for `grooveos.app` / `aibrussels` over runtime source returns zero configuration occurrences (comments/few-shot examples excepted per D-01).

### Claude's Discretion

- Exact new setting names (`APP_PUBLIC_URL` vs reusing existing), file-by-file edit order, and how the `.env.example` sections are grouped.
- Whether infra scripts (`clean-start.sh`, `verify-*.sh`) take the team_scope from an env var or keep the neutral `"default"` literal (both acceptable; env var preferred if cheap).
- How exhaustively to scrub `.planning/` history vs a forward-only convention (the planner may propose a pragmatic bound and log what it skips).

### Deferred Ideas (OUT OF SCOPE)

- **Q2** local email/password auth (OSS default) — own later phase.
- **Q3** local embeddings default + single-key (Anthropic/OpenAI/Grok) operation — own later phase.
- **Q4** web group-chat frontend; drop LibreChat/Open WebUI from OSS-light — Phase 16.
- **AGPLv3 LICENSE + CLA.md + trademark** — OSS packaging (Phase 16).
- **Compose `profiles:` + edition toggles** — Phase 15 (license/Ed25519 system DROPPED entirely).
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| PORT-01 | An operator can point the entire stack at their own domain and keys via config alone — no `grooveos.app`, `aibrussels`, or hardcoded `default` team_scope remains in source | Grounded Inventory tables (grooveos.app / aibrussels / default) enumerate every current occurrence with a fix classification; Landmines + Fail-Fast Risk sections cover the request/response-path leak points (Success Criterion #4); Config Pattern Research documents the two existing config mechanisms (Pydantic Settings, compose `${VAR:-default}`) plus LibreChat's native `${VAR}` engine and the gap surfaces with NO mechanism (nginx, chrome-extension, app-site) that need an explicit scope decision |
| PORT-02 | An operator can configure a fresh install from a slim, documented OSS `.env.example` without reading source code | `.env.example` — Current State and Proposal section documents all 5 existing env-template files, the undocumented-var gap (OAUTH_ISSUER_URL/OAUTH_RESOURCE_URL/MINIO_*/etc. missing from root `.env.example` entirely), and a proposed grouping (required-for-boot / LLM keys / domain-public-URLs / OAuth-identity / optional-integrations / SaaS-only) |
</phase_requirements>

## Summary

The phase's own `14-CONTEXT.md` inventory ("~14 files grooveos.app / 0 aibrussels-in-runtime / 1 default team_scope spot") and the ROADMAP entry ("28x/15x/15x") are both **stale and, in one case, wrong about which line is the actual hardcode**. Fresh repo-wide greps (this session, 2026-07-11) find:

- **`grooveos.app`: ~1,009 occurrences across 203 files repo-wide.** Runtime/infra source (`apps/**` + `infrastructure/**` + `chrome-extension/**` + `app-site/account/**`) accounts for ~123 occurrences across ~33 files — the rest (~886 occurrences / 170 files) is `.planning/` history (484 occ / 88 files), `app-site/docs` + historical `v0-v12` snapshots + `marketing-site/docs` (472 occ / 72 files combined), `docs/*.md` runbooks (38 occ / 6 files), and misc (`.github`, `projects-dashboard`).
- **`aibrussels`: 105 occurrences across 20 files.** Zero in `apps/**/app` (production code) confirmed — all occurrences are in `.planning/` history, test fixtures, and one KB doc. This part of the CONTEXT.md claim holds.
- **`"default"` / `'default'` team_scope-related literals: ~49 genuine occurrences** (after discarding 5 false positives — JSON-Schema `"default": 10` pagination values in `chatgpt-actions.json`, and an unrelated `user_api_tokens.name DEFAULT 'default'` column). **CONTEXT.md's cited single spot (`me.py:206`) is itself a false positive** — see Correction below. The real occurrences are Python function-parameter defaults (`team_scope: str = "default"`) in `mcp-gateway`, `mcp-github`, `agent-runtime`, `mcp-deck`, plus env-driven Pydantic Settings fields (`BRIDGE_DEFAULT_TEAM_SCOPE`, `PIPELINE_DEFAULT_TEAM_SCOPE`, `LIBRECHAT_DEFAULT_TEAM_SCOPE`) and hardcoded JWT-claim literals in shell/JS scripts.
- **Two new hardcode classes were found that are absent from CONTEXT.md's canonical_refs list**: (1) `apps/memory-api/app/knowledge/xbrain_product_kb.md` — a markdown file injected **verbatim into the @groove agent's live system prompt** at runtime, containing 5 absolute `grooveos.app` URLs the agent will confidently repeat to users of any self-hosted deployment; (2) `apps/librechat/patches/onboarding.js` — a client-side JS hardcode (`const MEMORY_API = 'https://api.grooveos.app'`) injected into LibreChat's `index.html`, not read from any env var.
- **A structurally different, much larger hardcode class exists in `chrome-extension/**` (4 files) and `app-site/account/**` (4 files)**: static client-side JS with **no existing runtime config mechanism at all** (`const MEMORY_API_BASE = "https://api.grooveos.app"` baked into `background.js`, `options.js`, `settings.js`, `popup.js`, `teams.js`, `admin.js`, `wipe.js`, `brain.js`). Server-side env-var patterns (Pydantic Settings, `${VAR:-default}`) do not apply here — these are pre-built browser artifacts. **This needs an explicit scope decision** (see Open Questions #1) — it is not in CONTEXT.md's canonical target list, and Phase 16 already plans a new standalone web-chat UI + is silent on the extension's OSS-light fate.
- **`infrastructure/nginx/conf.d/*.conf` (7 files) is 100% static `server_name` blocks with zero existing templating mechanism** — nginx does not read `.env`. This is also absent from CONTEXT.md's canonical_refs.

**Primary recommendation:** Treat this phase as three tiers of fix, not one:
1. **Tier A (mechanical, ~1 hour of work):** neutralize existing Pydantic Settings / `${VAR:-default}` defaults across `apps/**` and `infrastructure/docker-compose.yml`/`verify-*.sh` — the large majority of occurrences already have the right shape, only the literal value needs to change.
2. **Tier B (needs new plumbing):** `notifications.py` (2 spots), `waitlist.py` (`WAITLIST_FROM` default), `docker-compose.yml`'s `WEBUI_URL` (unwrapped, no `${VAR:-}`), `xbrain_product_kb.md`, `librechat/patches/onboarding.js`, `librechat.yaml` (`allowedDomains`, bridge `baseURL`, connector description link), and `nginx/conf.d/*.conf` — each needs a genuinely new env var + wiring, not just a value swap.
3. **Tier C (scope decision required before planning):** `chrome-extension/**` and `app-site/account/**` — static client bundles with no config mechanism; recommend explicitly deferring to Phase 16 (see Open Questions #1) unless the user wants Phase 14 to also solve browser-extension domain templating.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Server-side domain/URL config (memory-api, mcp-brain, drive-sync) | API / Backend | — | Already Pydantic `Settings` classes reading `.env` — pure value change |
| Compose-level env defaults (docker-compose.yml) | Deployment config | API / Backend | `${VAR:-default}` bash interpolation feeds every service's `environment:` block |
| nginx vhost domain names (`server_name`) | CDN / Reverse-proxy | — | No env mechanism today; needs envsubst templating or documented manual edit |
| LibreChat config (`librechat.yaml`) | Frontend Server (SSR-adjacent, Node process) | — | Has its OWN `${VAR}` substitution engine (LibreChat-native, resolved at container startup) — different mechanism from Pydantic |
| `xbrain_product_kb.md` (agent system-prompt content) | API / Backend (loaded by `team_chat_agent.py`) | — | Read verbatim at runtime, no templating applied — needs either domain-neutral rewrite or `.format()` pass |
| Chrome extension (`chrome-extension/**`) | Browser / Client | — | Static pre-built bundle; Chrome's extension model has no runtime `.env` — needs build-time templating or `chrome.storage`-backed Options UI |
| `app-site/account/**` static JS | Browser / Client (Firebase static hosting, no build step) | — | Same class of problem as the extension — literal JS constants, no server to inject config |
| Verify scripts (`verify-phase*.sh`) | Deployment / Test tooling | — | Already parameterized via `${VAR:-default}` bash pattern in most files — good existing pattern to keep |

## Correction to CONTEXT.md's Inventory

`14-CONTEXT.md` line 23 states the sole runtime `"default"` team_scope spot is `apps/memory-api/app/routes/me.py:206`, described as `Field(default="default")` "for team name." **Verified false** — reading the actual code:

```python
# apps/memory-api/app/routes/me.py:203-206
class ApiTokenCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    team_scope: str = Field(..., min_length=1, max_length=64)   # REQUIRED, no default
    name: str = Field(default="default", min_length=1, max_length=128)  # <- this is the field
```
`[VERIFIED: apps/memory-api/app/routes/me.py]`

Line 206's `default="default"` is the **personal API token's display name** (matches `user_api_tokens.name TEXT NOT NULL DEFAULT 'default'` in `apps/memory-api/alembic/versions/0013_api_tokens.py:26`) — i.e. "call this token 'default' if the user doesn't name it." It has nothing to do with team_scope fallback. The `team_scope` field on the same model is `Field(..., ...)` — **required, no fallback at all**. Do not "fix" this line; it is unrelated to portability. The real team_scope-fallback occurrences are listed in the inventory below.

## Grounded Inventory — `grooveos.app`

### Runtime/infra source (blocking scope — must be config-driven)

| File | Occ. | Classification | Notes |
|---|---|---|---|
| `apps/memory-api/app/config.py` | 5 | (a) env-driven Settings default | `MEMORY_API_EXTERNAL_URL`, `SMTP_FROM`, `CENTRIFUGO_WS_URL_PUBLIC`, `OAUTH_ISSUER_URL`, `OAUTH_RESOURCE_URL` |
| `apps/mcp-brain/app/config.py` | 2 | (a) | `OAUTH_ISSUER_URL`, `OAUTH_RESOURCE_URL` — mirror of memory-api |
| `apps/drive-sync/app/config.py` | 1 | (a) | comment only, referencing the pattern for `DRIVE_WEBHOOK_PUBLIC_URL` (actual default is already `""` — neutral, no change needed) |
| `apps/memory-api/app/services/notifications.py` | 2 | **(b) true hardcode** | `dashboard_url: str = "https://grooveos.app"` param default (func `send_member_autojoined_email`) + literal `f"— xbrain (noreply@grooveos.app)\n"` in the email body string (NOT sourced from `settings.SMTP_FROM`) |
| `apps/memory-api/app/routes/waitlist.py` | 1 | (a) but brand-baked | `WAITLIST_FROM = os.getenv("WAITLIST_FROM", "GrooveOS <waitlist@grooveos.app>")` — env-driven but the *default* bakes the brand; same treatment as D-02 public-URL defaults |
| `apps/memory-api/app/main.py` | 2 | (c) comment | French comment explaining CORS rationale, no functional impact |
| `apps/memory-api/app/deps.py` | 1 | (c) comment | Explanatory comment about GitHub sign-in convergence |
| `apps/memory-api/app/services/relevance_filter.py` | 2 | (c) few-shot example | Haiku classifier few-shot examples reference `langfuse.grooveos.app` and `grooveos.app` in illustrative INPUT text — **D-01 exception covers this file but says examples "may keep NEUTRAL example domains (example.com)"**, i.e. these specific occurrences should still be swapped to a neutral placeholder, not literally kept as `grooveos.app` |
| `apps/memory-api/app/knowledge/xbrain_product_kb.md` | 5 | **(b) true hardcode — NOT in CONTEXT.md's list** | Loaded verbatim by `team_chat_agent.py:68` (`_KB_PATH.read_text()`) and injected into the live @groove agent system prompt. References `chat.grooveos.app`, `mcp.grooveos.app`, `grooveos.app/account/teams/` as absolute URLs the agent recites to users. No `.format()`/templating applied to this file today (only `SYSTEM_PROMPT_PREAMBLE` gets `.format(team_slug=...)`, not the KB body) |
| `apps/memory-api/README.md` | 1 | (c)/(d) doc | Points to `grooveos.app/docs/claude-connector.html` as the end-user guide — legitimate SaaS reference, low priority |
| `apps/mcp-brain/chatgpt-actions.json` | 1 | (c)/(d) template artifact | `"servers": [{"url": "https://api.grooveos.app"}]` — a static OpenAPI-action manifest for manually configuring a ChatGPT Custom GPT; not loaded by any running service (not referenced anywhere in `apps/` code). Best treated as an operator template requiring manual edit, documented as such, rather than env-templated |
| `apps/librechat/patches/onboarding.js` | 3 | **(b) true hardcode — NOT in CONTEXT.md's list** | `const MEMORY_API = 'https://api.grooveos.app';` — client-side JS injected into LibreChat's `index.html` at container build/patch time. No env mechanism exists for this patch file today |
| `apps/session-bridge/README.md` | 1 | (d) doc | mentions `bridge.grooveos.app` as the fronting vhost name — descriptive, non-blocking |
| `apps/spike-mem0/test_data.json` | 4 | (e) test fixture | Phase-2 POC fixture; also uses **stale pre-migration subdomains** (`x.grooveos.app`, `ai.grooveos.app` — the actual current names are `chat.` / `adm.`), so it's doubly out of date. Low priority, may keep or scrub to `example.com` |
| `infrastructure/docker-compose.yml` | 14 | (a) mostly, **1 true hardcode** | 13 occurrences are `${VAR:-https://...grooveos.app}` (already the right shape); **`WEBUI_URL: https://adm.grooveos.app` (line 464) is NOT wrapped in `${...:-}` at all — true hardcode, no env override possible today** |
| `infrastructure/librechat/librechat.yaml` | 3 | **(b) true hardcode, but fixable via LibreChat's existing `${VAR}` engine** | `allowedDomains: - "grooveos.app"` (SSRF allow-list, registration domain), `baseURL: "https://bridge.grooveos.app/v1"` (Claude Pro/Max custom endpoint), and an inline HTML link to `grooveos.app/account/teams/` inside a `customUserVars.description` string. LibreChat resolves `${VAR_NAME}` in any string field of this YAML at container startup (confirmed — the file already uses this exact mechanism for `apiKey`, `X-Team-Scope`, `X-Internal-Secret`) `[CITED: librechat.ai/docs/configuration/librechat_yaml]` — so this is Tier-B, not a structural blocker, just needs 2-3 new vars threaded through `docker-compose.yml`'s `librechat.environment:` block |
| `infrastructure/centrifugo/config.json` | 2 | **(b) true hardcode** | `allowed_origins: ["https://chat.grooveos.app", "https://app.grooveos.app", ...]` — static JSON, no env substitution support in Centrifugo config files by default (would need an entrypoint envsubst step) |
| `infrastructure/nginx/conf.d/{10-xbrain,20-api,30-projects,40-mcp,50-bridge,60-centrifugo}.conf` | 18 total | **(b) true hardcode — NOT in CONTEXT.md's list** | Every `server_name` directive across 6 files is a literal `*.grooveos.app` subdomain. nginx does not read `.env`; the official `nginx:1.27-alpine` image (already in use) supports `envsubst`-based templating via `/etc/nginx/templates/*.template` → `/etc/nginx/conf.d/*.conf`, but these are currently plain `.conf` files mounted directly, not `.template` files `[CITED: Docker Official nginx image docs]` |
| `infrastructure/scripts/verify-phase{5,7,8,9,10,12,13}.sh` | 21 total | (a) | All use `${VAR:-https://...grooveos.app}` bash default-fallback — good existing pattern, just neutralize the fallback value |
| `infrastructure/scripts/brain-index.sh` | 3 | (a) | `${MEMORY_API_URL:-https://api.grooveos.app}` — GitHub Actions dashboard indexer script |
| `infrastructure/scripts/test-phase13-cross-frontend.py` | 2 | (a) | `os.environ.get("MEMAPI_HOST", "https://api.grooveos.app")` |
| `apps/mcp-brain/tests/test_oauth_resolve.py`, `apps/memory-api/tests/{test_oauth_as,test_phase12_auto_grant_regression}.py` | 9 | (e) test fixture | Literal `mcp.grooveos.app`/`grooveos.app` strings used as OAuth resource/redirect assertion values |
| `.env.example` (root) | 12 | (a) | Every occurrence is `VAR=https://...grooveos.app` — the actual PORT-02 deliverable target |

### Chrome extension + app-site (client bundles — Tier C, scope decision needed)

| File | Occ. | Classification |
|---|---|---|
| `chrome-extension/background.js` | 7 | **(b)** `MEMORY_API_URL`, `MEMORY_API_BASE`, `BRIDGE_WS_URL_TEMPLATE` — module-level `const` |
| `chrome-extension/manifest.json` | 4 | **(b)** `host_permissions` + `content_scripts.matches` — Chrome-enforced static allow-list, cannot be env-templated at install time by design of the platform |
| `chrome-extension/options.js`, `settings.js`, `librechat_autofill.js`, `popup.js`, `onboarding.js`, `options.html` | 7 | **(b)** same `MEMORY_API_BASE` const pattern repeated per-file |
| `app-site/account/teams/teams.js` | 3 | **(b)** `MEMORY_API_BASE` const + hardcoded `GOOGLE_CLIENT_ID` (the latter is arguably fine to keep visible client-side per OAuth convention, but is SaaS-specific) |
| `app-site/account/admin/admin.js`, `admin/wipe.js`, `teams/brain/brain.js` | 3 | **(b)** same pattern |
| `app-site/account/teams/index.html` | 1 | (c) hardcoded link in empty-state copy |

No build step exists for `app-site` today (`public: "."` in `firebase.json`, confirmed via STATE.md decision log) — there is genuinely no injection point without adding one. See Open Questions #1.

### Docs / marketing / planning prose (non-blocking, D-01 full-cleanup scope)

| Area | Files | Occ. | Notes |
|---|---|---|---|
| `.planning/**` | 88 | 484 | Phase history, quick-task summaries, ROADMAP, STATE, REQUIREMENTS. `.planning/features/open-core-edition-design.md`'s "Locked Decisions" table is the **explicit D-01 exception** — keep verbatim |
| `app-site/docs/**` (17 files) + `app-site/v0-v12/**` (historical snapshot dirs) | ~25 | ~150 | Public product-doc HTML for the LIVE grooveos.app site — legitimate hosted-product documentation, not OSS install docs. Recommend treating as out-of-scope for full cleanup (it documents the real SaaS, same logic as the README's "Hosted product: grooveos.app" line) rather than scrubbing |
| `marketing-site/docs/**` | 15 | 108 | Same category as above — separate Firebase target, same reasoning |
| `docs/*.md` (repo root `docs/`) | 6 | 38 | Operator runbooks tied to the specific GCP project (`xbrain-495115`) / Cloudflare zone — these are "how we run grooveos.app" runbooks, not generic OSS docs. Scrubbing would make them useless as runbooks; recommend relocating out of the path a future OSS docs-scrub would touch, or clearly labeling as SaaS-ops-only |
| `README.md` (root) | 3 | 3 | **Legitimate, keep** — "The open-source engine behind [GrooveOS](https://grooveos.app)" / "Hosted product: grooveos.app" is intentional OSS-project positioning, not a hardcode bug |
| `.github/**`, `projects-dashboard/**` | 5 | 8 | CI/dashboard scripts, `${VAR:-grooveos.app}` pattern already, low priority |

## Grounded Inventory — `aibrussels`

**Confirmed: zero occurrences in `apps/**/app` production code.** All 105 occurrences / 20 files are:

| Bucket | Files | Occ. |
|---|---|---|
| `.planning/**` (history, design doc, REQUIREMENTS/ROADMAP/STATE) | 11 | 33 |
| Test fixtures (`apps/memory-api/tests/{test_github_catalog,test_internal_resolve_team_scope}.py`, `apps/mcp-brain/tests/test_resolve.py`, `apps/librechat-bridge/tests/test_resolve_team_scope.py`) | 4 | 15 — literal `"aibrussels"` used as the fixture team slug in assertions; classification (e), may keep or rename to a neutral test-team slug |
| `apps/spike-mem0/test_data.json` | 1 | included in the file's grooveos.app count above (`"team_scope": "team-a"` — already neutral, unrelated to `aibrussels`) |
| `docs/session-bridge-review.md`, `infrastructure/scripts/PHASE13-HELPER-FIX-NOTES.md` | 2 | 2 — docs/runbook prose |
| KB doc referencing the real team (not code) | — | — |

`aibrussels` is a **real team row created via the normal `POST /v1/teams/self-solo` flow** — confirmed no seed/migration/fixture creates it in production code. D-04 (no auto-seed team) requires no change here; this part of the inventory is clean.

## Grounded Inventory — `"default"` team_scope

After discarding false positives (JSON-Schema `"default": N` keys in `chatgpt-actions.json`; the unrelated `user_api_tokens.name` column/field default), genuine team_scope-related `"default"` literals:

| File | Occ. | Classification | Notes |
|---|---|---|---|
| `apps/mcp-gateway/app/aggregate.py` | 4 | (a) intentional, keep | `_mint_bridge_jwt(team_scope="default")`, `X-Team-Scope: "default"` header — **by design**: the mcp-gateway aggregate (scraper/calendar/drive-read/deck) is deliberately team-neutral; `register-mcp-tools.sh:179` comment confirms: "The aggregate hardcodes team_scope=default, which would break sync [if a real team scope were used for GitHub sync]" |
| `apps/mcp-gateway/app/main.py` | 3 | (a) | `X-Team-Scope` header fallback default in auth resolution (`request.headers.get("X-Team-Scope", "default")`) |
| `apps/mcp-github/app/main.py`, `bridge_jwt.py` | 4 | (a) intentional, keep | GitHub read endpoints are explicitly org-scoped not team-scoped; `"default"` used as JWT `team_scope` claim for team-neutral read operations |
| `apps/mcp-deck/app/main.py` | 2 | (a) | Function parameter defaults `team_scope: str = "default"` |
| `apps/agent-runtime/app/tools/mcp_gateway_client.py` | 1 | (a) | `get_mcp_tools(team_scope: str = "default")` |
| `apps/librechat/patches/xbrain-routes.js` | 2 | (a) | `makeBridgeJwt(sub, 'default')` — placeholder team_scope for an endpoint that doesn't require one (`resolve-team-scope`, no `X-Team-Scope` required per its own comment) |
| `apps/librechat-bridge/app/config.py` | 1 | (a) env-driven | `BRIDGE_DEFAULT_TEAM_SCOPE: str = "default"` — Pydantic Settings field |
| `apps/openwebui-pipeline/app/config.py` | 1 | (a) env-driven | `PIPELINE_DEFAULT_TEAM_SCOPE: str = "default"` |
| `.env.example` (root), `apps/librechat-bridge/.env.example`, `apps/openwebui-pipeline/.env.example` | 3 | (a) | `BRIDGE_DEFAULT_TEAM_SCOPE=default`, `PIPELINE_DEFAULT_TEAM_SCOPE=default` |
| `infrastructure/docker-compose.yml` | 1 | (a) | `LIBRECHAT_DEFAULT_TEAM_SCOPE: ${LIBRECHAT_DEFAULT_TEAM_SCOPE:-default}` |
| `infrastructure/scripts/register-mcp-tools.sh` | 2 | (a) | JWT claim `'team_scope': 'default'` for internal admin tool-registration calls (harmless — endpoint gated by `BRIDGE_SHARED_SECRET`, not team-scoped) |
| `infrastructure/scripts/clean-start.sh` | 1 | (a) | JWT claim `team_scope:"default"` for the wipe-database endpoint (not team-scoped) |
| `infrastructure/scripts/verify-phase{2,3,4,11}.sh` | 18 | (a)/(e) | Test/verify fixtures using `team_scope='default'` (older tests predate the `aibrussels`/`dejavudev` slugs) |
| `apps/memory-api/tests/test_admin_wipe.py`, others | ~5 | (e) | Test fixtures |

**D-04 already covers this correctly**: "keep the `'default'` team_scope literal as a neutral fallback string ... do not expand its use." No code change is required for any row above except optionally wiring the shell-script/JWT-literal spots to read from an env var (Claude's Discretion per CONTEXT.md — "both acceptable; env var preferred if cheap").

## Landmines (domain/team leaking into request/response path)

Per Success Criterion #4 ("no residual xbrain-specific string leaks through anywhere in the request/response path"), these are the concrete leak points found:

1. **`xbrain_product_kb.md` → live agent responses.** The @groove agent will tell users of *any* self-hosted deployment to go to `chat.grooveos.app` and `mcp.grooveos.app`. This is a functional correctness bug, not just cosmetic — highest-priority Tier-B item.
2. **`notifications.py` email body** — `noreply@grooveos.app` is hardcoded into the *body text* of `send_member_autojoined_email`, separate from (and inconsistent with) the `SMTP_FROM` header which already reads from settings.
3. **mcp-brain's `_PROTECTED_RESOURCE_METADATA_URL`** (`apps/mcp-brain/app/main.py:43-52`) is computed **at module-import time** from `settings.OAUTH_RESOURCE_URL` via `urlparse()`, not lazily per-request. If `OAUTH_RESOURCE_URL` defaults to `""` (per D-03), `urlparse("")` silently returns empty `scheme`/`netloc` (no exception) → this constant becomes the literal string `"://.well-known/oauth-protected-resource"`, silently shipped in the `WWW-Authenticate` header on every 401. **A fail-fast check must run before this module-level line executes** — i.e., inside a Pydantic `field_validator`/`model_validator` on the `Settings` class itself (which runs at `Settings()` construction time, i.e. at `app.config` import, which happens before `app.main` imports it), not inside a FastAPI startup-event handler or a lazy per-request check. `[VERIFIED: apps/mcp-brain/app/main.py]`
4. **LibreChat's `librechat.yaml` `customUserVars.description`** embeds a raw `<a href='https://grooveos.app/account/teams/'>` link shown to every user configuring the `xbrain-memory` MCP server's personal token.
5. **`onboarding.js`** (LibreChat patch) posts directly to a hardcoded `api.grooveos.app` from the browser, bypassing whatever `MEMORY_API_EXTERNAL_URL` the operator configured server-side.
6. **`docker-compose.yml`'s `WEBUI_URL: https://adm.grooveos.app`** for `openwebui-pipeline` — Open WebUI uses this to construct citation/reference links back to itself; wrong domain on a fresh install produces broken links in the UI, not a crash (lower severity than #3 but still a real leak).

## Fail-Fast Risk — Callers of `OAUTH_ISSUER_URL` / `OAUTH_RESOURCE_URL`

Enumerated so the planner can scope the boot-time validator correctly. Both vars are consumed by **two separate services** (memory-api and mcp-brain), each with their own `Settings` class and their own default today (`https://api.grooveos.app` / `https://mcp.grooveos.app/mcp` respectively, duplicated verbatim in both `config.py` files).

| Service | Call site | Usage | Timing |
|---|---|---|---|
| mcp-brain | `app/main.py:26-33` `_protected_resource_metadata()` | RFC 9728 protected-resource metadata (`resource`, `authorization_servers`) | Per-request (route handler) |
| mcp-brain | `app/main.py:43-52` `_resource_host_root()` → `_PROTECTED_RESOURCE_METADATA_URL` | Derives host root for `WWW-Authenticate` header value | **Module import time** — highest-risk call site |
| mcp-brain | `app/oauth_verify.py` (audience/`aud` check, RFC 8707) | Fail-closed rejection of tokens minted for a different resource | Per-request, already fails safely (rejects) if mismatched — but an empty `OAUTH_RESOURCE_URL` would make `_normalize_resource("")` the "expected" value, potentially accepting tokens with empty/missing `aud` incorrectly. **Needs explicit test once fail-fast lands** |
| memory-api | `app/routes/oauth_metadata.py:21` `authorization_server_metadata()` | RFC 8414 AS metadata `issuer` + 5 derived endpoint URLs | Per-request |
| memory-api | `app/routes/oauth_authorize.py:112-113,130,164` | `resource` param default + GitHub-consent `redirect_uri` construction | Per-request |
| memory-api | `app/auth/oauth_store.py:297` | `"aud"` claim minted into issued access tokens | Per-request (token mint) |

**Recommendation:** since both services always mount the OAuth connector routes today (no `EDITION` flag exists yet — that's Phase 15), the simplest correct Phase-14 fix is an **unconditional fail-fast Pydantic validator** on both `Settings` classes: raise at object-construction time if `OAUTH_ISSUER_URL` or `OAUTH_RESOURCE_URL` is empty. Do NOT implement this as a lazy per-request check only — mcp-brain's module-level constant (item 3 above) would already be malformed by the time any request-time check ran.

```python
# Pattern (Pydantic v2, pydantic-settings >=2.6 — matches this repo's pin)
# Source: https://docs.pydantic.dev/latest/concepts/validators/
from pydantic import field_validator

class Settings(BaseSettings):
    OAUTH_ISSUER_URL: str = ""
    OAUTH_RESOURCE_URL: str = ""

    @field_validator("OAUTH_ISSUER_URL", "OAUTH_RESOURCE_URL")
    @classmethod
    def _require_oauth_urls(cls, v: str, info) -> str:
        if not v:
            raise ValueError(
                f"{info.field_name} is required — set it in .env "
                f"(e.g. OAUTH_ISSUER_URL=https://api.yourdomain.com)"
            )
        return v
```
`[CITED: docs.pydantic.dev/latest/concepts/validators]` — validated the mechanism exists and runs at model-construction time; the exact field name access via `info.field_name` matches Pydantic v2's `ValidationInfo` API present since 2.0 (training-knowledge confirmed, not independently re-verified against 2.10 changelog this session — flag LOW-effort risk, re-check on implementation).

## Config Pattern Research

Two coexisting patterns are in use today — the planner should pick ONE per new var, matching what's already there for that file:

| Pattern | Where used | Shape |
|---|---|---|
| **Pydantic `BaseSettings` class field with default** | `apps/{memory-api,mcp-brain,drive-sync,librechat-bridge,openwebui-pipeline}/app/config.py` | `VAR_NAME: str = "neutral-default"` — reads `.env` via `SettingsConfigDict(env_file=".env")`, validated at `Settings()` construction (module import time) |
| **Bare `os.getenv()` with default** | `apps/memory-api/app/routes/waitlist.py` only | `os.getenv("VAR", "default")` at module top-level — this is the ONE outlier not using the Settings class; not idiomatic for this codebase, but functionally equivalent. Recommend leaving as-is (not worth migrating to Settings for a single small route) but do change the *default value* |
| **Compose-level `${VAR:-default}`** | `infrastructure/docker-compose.yml` (all services), `infrastructure/scripts/verify-phase*.sh`, `infrastructure/scripts/brain-index.sh` | Standard bash/compose parameter expansion — this is what actually supplies the value INTO the Pydantic Settings above via `environment:` |
| **LibreChat-native `${VAR}` substitution** | `infrastructure/librechat/librechat.yaml` | LibreChat's own config loader resolves `${VAR_NAME}` in any string field at container startup, requiring a matching entry in the process env (passed through via `docker-compose.yml`'s `librechat.environment:` block). **No default-fallback syntax** (`${VAR:-x}` NOT supported by LibreChat itself) — the default must live in `docker-compose.yml`'s `${VAR:-default}` layer instead, then plain `${VAR}` in the YAML. `[CITED: librechat.ai/docs/configuration/librechat_yaml, deepwiki.com/LibreChat-AI/librechat.ai/2-configuration]` |
| **No mechanism** | `infrastructure/nginx/conf.d/*.conf`, `infrastructure/centrifugo/config.json`, `chrome-extension/**`, `app-site/account/**` | Static files with zero runtime templating today |

**Fail-fast validator placement:** confirmed via `grep` — **no `field_validator`/`model_validator` exists anywhere in the codebase today.** This will be the first use of the pattern; keep it simple (raise `ValueError` in a `field_validator`, which Pydantic wraps into a `ValidationError` that crashes `Settings()` construction — i.e., crashes at import, before Uvicorn binds a port. Confirmed correct behavior for a "fail fast at boot" requirement).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---|---|---|---|
| nginx domain templating | A custom sed/envsubst wrapper script | The official `nginx:1.27-alpine` image's built-in `docker-entrypoint.sh` template mechanism (`/etc/nginx/templates/*.template` → auto-envsubst → `/etc/nginx/conf.d/*.conf` at container start) | Zero new dependencies, already the image in use; just requires renaming `.conf` → `.template` and being careful to `envsubst` only the intended `$SERVER_DOMAIN`-style vars (nginx itself uses `$host`, `$remote_addr` etc. — must pass an explicit var list to `envsubst` to avoid mangling nginx's own runtime variables) `[CITED: hub.docker.com/_/nginx — "Using environment variables in nginx configuration"]` |
| Fail-fast config validation | Manual `if not X: sys.exit(1)` scattered in `main.py`/`lifespan()` | Pydantic `field_validator` on the `Settings` class | Runs automatically at every `Settings()` construction (tests, scripts, app boot alike) — one definition point, not N call sites to remember |
| Chrome-extension "config" | A custom fetch-config-on-load pattern reinvented from scratch | `chrome.storage.sync` + the existing (already-built) `options.html`/`options.js` Options page — the plumbing already exists for other per-user settings (side-panel toggle, LibreChat autofill toggle) | The extension already has a working Options-page + `chrome.storage` pattern for 2+ other settings; extending it to `memory_api_base` is additive, not new architecture |

## Common Pitfalls

### Pitfall 1: Module-import-time URL construction ignores empty defaults silently
**What goes wrong:** `urlparse("")` does not raise — code that does `urlparse(settings.SOME_URL).netloc` at import time will silently produce a malformed constant instead of crashing.
**Why it happens:** Python's `urllib.parse` is permissive by design.
**How to avoid:** Put the fail-fast check in a Pydantic validator on the `Settings` class (runs before any downstream module-level code can consume the value), not in the downstream module.
**Warning signs:** Any `settings.SOME_URL` referenced outside a function body (i.e., at module top-level) is a signal to check for this.

### Pitfall 2: "Runtime source" grep bar (D-07) will still show matches after a naive fix, because of test fixtures and comments
**What goes wrong:** A bare `grep -rn grooveos.app apps/` after the fix will still return ~15 hits (test fixtures + the relevance_filter few-shot examples + the design-doc reference) even after every functional hardcode is fixed.
**Why it happens:** D-01/D-07 both carve out explicit exceptions (test fixtures, few-shot examples, the Locked Decisions table).
**How to avoid:** The plan-checker / verifier should grep with an explicit exclude list (`--glob '!*/tests/*' --glob '!chatgpt-actions.json'` etc.), not a bare grep, and the acceptance criterion in the plan should say "zero matches outside the documented exceptions," matching CONTEXT.md's own D-07 wording.

### Pitfall 3: Two independent `OAUTH_ISSUER_URL`/`OAUTH_RESOURCE_URL` defaults exist (memory-api AND mcp-brain) — fixing one and not the other breaks the connector
**What goes wrong:** `mcp-brain`'s `oauth_verify.py` checks the introspected token's `aud` against ITS OWN `OAUTH_RESOURCE_URL`, independently minted by memory-api's `oauth_store.py`. If an operator sets the var in one service's env block but not the other (docker-compose has two separate `environment:` stanzas), tokens will be minted for one resource and rejected against another.
**How to avoid:** Plan must update BOTH services' env wiring in `docker-compose.yml` together, and the fail-fast validator must exist in both `config.py` files (currently near-identical duplicated blocks).
**Warning signs:** `verify-phase*.sh` OAuth-connector assertions (if any) failing with 401/audience-mismatch after a partial fix.

### Pitfall 4: `.env.example`'s "WAITLIST_FROM"/"SMTP_FROM" style defaults look neutral but aren't tested for RFC 5322 validity once brand is stripped
**What goes wrong:** Swapping `noreply@grooveos.app` → `noreply@example.com` is safe, but `WAITLIST_FROM=GrooveOS <waitlist@grooveos.app>` has a **display name** baked in too ("GrooveOS"), not just the domain — a naive domain-only substitution leaves the brand name in the visible sender field.
**How to avoid:** When neutralizing, check for display-name-plus-address patterns (`Name <email@domain>`), not just bare domains.

## Regression Safety — `verify-phase*.sh` Inventory

18 verify scripts exist (`verify-phase{2,3,4,5,7,8,9,10,11,12,13}.sh` + `test-phase13-cross-frontend.py`). Pattern census:

| Script | Domain/team override mechanism | Default value | Hardcoded literal (no override) |
|---|---|---|---|
| `verify-phase5.sh` | `${MEMORY_API_BASE:-...}` / `${APP_SITE_BASE:-...}` | `https://api.grooveos.app` / `https://grooveos.app` | — |
| `verify-phase7.sh`, `verify-phase8.sh` | `${MEMAPI_HOST:-...}` | `https://api.grooveos.app` | — |
| `verify-phase9.sh` | `${BRIDGE_HOST:-...}` | `bridge.grooveos.app` | — |
| `verify-phase10.sh` | `${MEMAPI_HOST:-...}` | `https://api.grooveos.app` | one inline comment `https://grooveos.app/account/teams/` referencing the required OAuth callback URL — informational only |
| `verify-phase12.sh` | `${MEMAPI_HOST:-...}` | `https://api.grooveos.app`; `TEST_GITHUB_ORG` explicitly documented as operator-supplied (example shown is the legacy `dejavudev`, not `aibrussels`) | — |
| `verify-phase13.sh` | `${MEMAPI_HOST:-...}` / `${LIBRECHAT_HOST:-...}` / `${TEST_TEAM_SCOPE:-dejavudev}` | `https://api.grooveos.app` / `https://chat.grooveos.app` / `dejavudev` | — |
| `verify-phase2.sh`, `verify-phase3.sh`, `verify-phase4.sh` | none for team_scope | — | `'team_scope': 'default'` / `'X-Team-Scope': 'default'` literal in Python payload construction — these predate the team-slug concept entirely (Phase 1-era tests) |
| `verify-phase11.sh` | none for team_scope | — | `team_scope='default'` throughout (10 occurrences) — comments explicitly say fixtures must exist "in team_scope='default'" |
| `clean-start.sh` | none | — | `team_scope:"default"` JWT claim — harmless (endpoint is a superadmin wipe, not team-scoped) |
| `register-mcp-tools.sh` | none | — | `'team_scope': 'default'` JWT claim for internal tool registration — harmless (not team-scoped) |

**For Success Criterion #2** ("setting the new config vars to CURRENT prod values reproduces prod bit-for-bit"): every script above ALREADY supports this via its documented env-var override — e.g. `MEMAPI_HOST=https://api.grooveos.app TEST_TEAM_SCOPE=aibrussels bash verify-phase13.sh` reproduces the current prod assertion set without any code change. **The regression risk is not in these scripts — it is in whether the application code they're testing still returns the same values once the *default* changes.** Concretely: after Phase 14, `verify-phase11.sh` run with no env override would test against a `team_scope='default'` team that may not exist on a fresh install (the historical fixtures assumed a team literally named `default` existed in the dev/test DB) — this is pre-existing test fragility, not something Phase 14 introduces, but worth flagging since the CONTEXT's Success Criterion #2 language implies these scripts are the regression gate.

**Recommendation:** the plan should NOT need to touch the verify-phase2/3/4/11 hardcoded `'default'` literals functionally (per D-04, `"default"` stays) — only `verify-phase{5,7,8,9,10,12,13}.sh`'s `grooveos.app` fallback values need neutralizing, and even that is optional (they're test tooling, not "runtime source" in the product sense — but they ARE in scope per D-01's "full cleanup" if read literally). Recommend treating these as Tier A (mechanical) work, batched together.

## `.env.example` — Current State and Proposal

**5 files exist today**, not 1:

| File | Vars | Role |
|---|---|---|
| `.env.example` (repo root) | **121** (not 115 as the design doc states — fresh count) | Canonical — consumed via `docker compose -f infrastructure/docker-compose.yml --env-file .env` (confirmed via `Makefile:9`) |
| `infrastructure/.env.example` | ~2 (mostly comments) | **Vestigial/misleading** — self-documents as "Canonical env template lives at REPO ROOT," only contains a duplicate Phase-9 section. Candidate for deletion as part of the D-06 cleanup rather than "slimming" |
| `apps/memory-api/.env.example` | 12 | Per-service subset for standalone local dev (running memory-api outside docker-compose) |
| `apps/librechat-bridge/.env.example` | 10 | Same purpose, librechat-bridge |
| `apps/openwebui-pipeline/.env.example` | 10 | Same purpose, openwebui-pipeline |

**Existing good pattern already in root `.env.example`:** `DOMAIN_URL=http://__VM_HOST__` — a neutral placeholder convention already used for the Phase-1 IP-based deploy. D-02's "neutral default" instinct already has precedent in this exact file; extend the same `__FILL__` / `__VM_HOST__` placeholder convention rather than inventing a new one.

**Gap found (relevant to PORT-02):** several `config.py` fields have NO corresponding line in `.env.example` at all, meaning operators can't discover them without reading source — and their *hidden* code-level defaults currently point at `grooveos.app`: `OAUTH_ISSUER_URL`, `OAUTH_RESOURCE_URL` (both services), `MINIO_URL`/`MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY`, `GITHUB_FALLBACK_TOKEN`, `AGENT_RUNTIME_INTERNAL_URL`, `QDRANT_COLLECTION`, `GITHUB_CATALOG_*`. This is itself a PORT-02 gap independent of the branding issue.

**Makefile's `env-check` target** (`Makefile:96-97`) already defines a minimal "critical vars" list: `POSTGRES_PASSWORD GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET BRIDGE_SHARED_SECRET MEILI_MASTER_KEY OPENWEBUI_SECRET_KEY` — useful starting point for the "required for minimal boot" vs "optional" split D-06 asks for, though it predates the OSS-light service set (Phase 15/16 will drop LibreChat/Open WebUI, which several of these vars belong to).

**Proposed grouping for the slim OSS `.env.example` (Claude's Discretion per CONTEXT.md D-06):**

```
# === Required — minimal boot ===
POSTGRES_PASSWORD, DATABASE_URL, BRIDGE_SHARED_SECRET, JWT_ALGORITHM,
QDRANT_URL, CENTRIFUGO_TOKEN_HMAC_SECRET, CENTRIFUGO_API_KEY

# === LLM provider keys (at least one required — chat is unusable without one) ===
ANTHROPIC_API_KEY, OPENAI_API_KEY, XAI_API_KEY

# === Domain / public URLs (neutral defaults ship working; set for real deploys) ===
MEMORY_API_EXTERNAL_URL, CENTRIFUGO_WS_URL_PUBLIC, APP_PUBLIC_URL (new, D-05),
SMTP_FROM

# === OAuth identity (empty = connector disabled + fail-fast; required if you
#     want the ChatGPT/Claude.ai connector) ===
OAUTH_ISSUER_URL, OAUTH_RESOURCE_URL, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET

# === Optional integrations (safe to leave blank — feature no-ops) ===
GITHUB_APP_*, SMTP_HOST/PORT/USER/PASSWORD, GRANOLA_API_BASE,
GOOGLE_CALENDAR_*, GOOGLE_DRIVE_*, RESEND_API_KEY, MINIO_*

# === SaaS-only / not part of OSS-light (candidates to move to a separate
#     infrastructure/.env.saas.example once Phase 15/16 define the split) ===
LANGFUSE_*, GCS_BACKUP_BUCKET, VM_HOST/VM_USER/SSH_KEY
```

Recommend explicitly deleting `infrastructure/.env.example` (superseded/misleading) as part of this phase's cleanup rather than trying to "slim" a file whose own header says it's not canonical.

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---|---|---|
| V2 Authentication | Indirect | OAuth issuer/resource URLs feed AS/PR metadata — a wrong default doesn't create an auth bypass (tokens are still cryptographically verified) but CAN create a silent connector outage or, per Pitfall/Landmine #3, a malformed `WWW-Authenticate` header |
| V4 Access Control | No change | `team_scope` isolation logic itself is untouched by this phase — only the *fallback string value* changes, never the enforcement path |
| V5 Input Validation | Yes | New Pydantic `field_validator`s are themselves input validation — reuse the existing `pydantic-settings>=2.6` stack, don't hand-roll |
| V9 Communications | Indirect | `OAUTH_RESOURCE_URL`/`OAUTH_ISSUER_URL` should arguably be validated as well-formed `https://` URLs (not just non-empty) to prevent an operator typo from producing a working-but-wrong AS metadata document — recommend the planner consider a stricter validator (`AnyHttpUrl` or a regex) beyond bare non-empty |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---|---|---|
| Wrong/empty `OAUTH_RESOURCE_URL` causing token audience confusion between multiple xbrain deployments sharing infra (e.g. a shared reverse proxy) | Spoofing | RFC 8707 audience binding already implemented (`oauth_verify.py` fail-closed on `aud` mismatch) — the fail-fast validator this phase adds is a **defense-in-depth improvement**, not a new mitigation; document it as such rather than as a new security control |
| Brand string leaking team/domain identity into agent responses (`xbrain_product_kb.md`) | Information Disclosure (low severity — reveals only the operator's OWN domain, not cross-tenant data) | Fix via domain-neutral rewrite or templated content |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|---|---|---|
| A1 | LibreChat's `${VAR}` substitution in `librechat.yaml` works for arbitrary string fields (not just the documented `apiKey`/header cases) — inferred from the file's existing use in `X-Team-Scope`/`X-Internal-Secret` headers plus web search confirmation, not independently tested against the pinned LibreChat version (`v0.8.2-rc2`, per CLAUDE.md) in this session | Landmine #4, Config Pattern Research | If wrong, `allowedDomains` and `baseURL` fields may need a different fix (e.g., an entrypoint sed/envsubst pass over `librechat.yaml` before LibreChat reads it) — low risk since the SAME mechanism is already proven working for other fields in this exact file |
| A2 | Pydantic v2's `field_validator` + `ValidationInfo.field_name` API is stable at the pinned version (`pydantic>=2.10,<3`) | Fail-Fast Risk / Config Pattern Research | Very low risk — this API has been stable since Pydantic 2.0 (mid-2023); worth a 30-second smoke test during implementation, not a research gap |
| A3 | `apps/mcp-brain/chatgpt-actions.json` is not loaded by any running service (confirmed via `grep -rn chatgpt-actions apps/` finding zero code references) — treated as a manual operator template rather than app config | Grounded Inventory — grooveos.app | If wrong (some undiscovered code path reads this file), it would need the same env-driven fix as `config.py`; low risk given the grep coverage |
| A4 | The official `nginx:1.27-alpine` image's envsubst-template mechanism (`.template` → `.conf`) is the right fix for `nginx/conf.d/*.conf`, as opposed to leaving nginx domain names as a documented manual-edit step | Landmine list, Don't Hand-Roll | If the planner decides manual-edit is acceptable for v1 of portability (nginx setup is inherently more "infra" than "app config" for most self-hosters), this whole sub-item could be descoped without violating D-07's "runtime source" grep bar, since nginx conf isn't Python/JS "source" in the strict sense — flagged as Open Question #2, not asserted as required |

## Open Questions

1. **Are `chrome-extension/**` and `app-site/account/**` in scope for Phase 14, or deferred to Phase 16?**
   - What we know: both contain hardcoded `MEMORY_API_BASE = "https://api.grooveos.app"` client-side constants with no existing config mechanism (static bundle / no-build-step Firebase site respectively). Neither is listed in CONTEXT.md's canonical_refs. Phase 16 (`PKG-02`) explicitly plans a NEW standalone web-chat UI and the design doc's Q4 decision drops LibreChat/OpenWebUI — the chrome extension's OSS-light role is undefined.
   - What's unclear: whether "the entire stack" in D-07 ("pointing the whole stack at a new domain... requires zero source edits") is meant to include these SaaS-specific client surfaces, or only the docker-compose-deployed backend stack.
   - Recommendation: default to **deferring both to Phase 16** (where the web-chat UI rebuild is already planned and could absorb a proper build-time config step), and have Phase 14 only add the missing env vars server-side + document the extension/app-site domains as a known, explicitly-scoped-out gap in the phase's own README/CONTEXT. This keeps Phase 14 mechanically tractable; revisit if the user wants full portability including the browser surfaces now.

2. **Is nginx `server_name` templating (envsubst) in scope, or is "edit `nginx/conf.d/*.conf` to your domain" an acceptable documented manual step for v1 portability?**
   - What we know: nginx has zero existing config-injection mechanism in this repo; the fix (envsubst templates) is a well-trodden Docker pattern but touches 6 files and changes the deploy/build flow (`.conf` → `.template` rename + entrypoint change).
   - What's unclear: whether D-07's bar covers infra config files at all, or only application source (`apps/**`).
   - Recommendation: include as Tier B work (moderate effort, high payoff for PORT-01's "point at your own domain" promise) but flag it explicitly in the plan so it can be descoped to "documented manual edit" if time-boxed.

3. **`xbrain_product_kb.md` fix approach: rewrite to domain-neutral prose, or template it with `.format()`?**
   - What we know: the file is read verbatim (`_KB_PATH.read_text()`), no templating applied today; it's Markdown injected into an LLM system prompt (not a technical config surface).
   - What's unclear: whether the agent NEEDS the literal domain in its guidance (e.g. "sign in at chat.grooveos.app") or whether relative/generic phrasing ("sign in via the LibreChat frontend or the web app") is equally useful and simpler to fix.
   - Recommendation: prefer relative/generic rewrite over templating — simpler, no new config surface, and arguably better prose regardless of portability.

4. **How exhaustively should `.planning/` history be scrubbed?** (Claude's Discretion per CONTEXT.md, explicitly deferred to the planner) — 484 occurrences across 88 files, almost all historical phase-completion records that are factually accurate descriptions of what WAS deployed at the time. Recommend a forward-only convention: leave historical phase/quick-task records untouched (they're an audit trail), scrub only currently-read-forward docs (`ROADMAP.md`'s "Current Position," `STATE.md`'s "Current focus," `PROJECT.md`'s stack/constraints tables) to domain-neutral language where they describe the PRODUCT rather than a historical event.

## Sources

### Primary (HIGH confidence — verified this session via direct file reads / grep)
- Every file/line cited in the Grounded Inventory tables — read directly via `Read`/`Grep` tools against the live repo, 2026-07-11.
- `apps/memory-api/pyproject.toml`, `apps/mcp-brain/pyproject.toml` — confirmed `pydantic>=2.10,<3` / `pydantic-settings>=2.6` / `pydantic-settings>=2.0.0` pins.
- `Makefile` — confirmed `--env-file .env` invocation pattern and `env-check` target's minimal-var list.
- `.planning/config.json` — confirmed `nyquist_validation: false` (Validation Architecture section omitted from this doc accordingly).

### Secondary (MEDIUM confidence — web-verified, not independently tested against this repo's exact pinned version)
- [LibreChat YAML — Configuration Custom Config](https://www.librechat.ai/docs/configuration/librechat_yaml) — `${VAR}` substitution mechanism for `librechat.yaml`.
- [Configuration System | LibreChat-AI/librechat.ai | DeepWiki](https://deepwiki.com/LibreChat-AI/librechat.ai/2-configuration) — timing (env resolved at startup, user placeholders at request time).
- [Validators | Pydantic Docs](https://docs.pydantic.dev/latest/concepts/validators/) — `field_validator` fail-fast pattern.
- [Settings Management | Pydantic Docs](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) — `extra=forbid` construction-time validation behavior.
- Docker Official `nginx` image docs (`hub.docker.com/_/nginx`, "Using environment variables in nginx configuration") — envsubst templating mechanism, referenced from training knowledge + general web corroboration, not fetched verbatim this session — treat as MEDIUM not HIGH.

### Tertiary (LOW confidence)
- None — all claims in this document are either directly verified against the repo or cited to an official doc source above.

## Metadata

**Confidence breakdown:**
- Inventory counts (grooveos.app / aibrussels / default): HIGH — fresh `rg`/Grep against the live tree this session, cross-checked file-count sums against directory-level counts for consistency.
- LibreChat `${VAR}` substitution scope: MEDIUM — confirmed the mechanism exists and is already used in this exact file for 3 fields; not independently tested for `allowedDomains`/`baseURL` fields specifically.
- nginx envsubst templating approach: MEDIUM — standard, well-documented Docker pattern; not tested against this repo's specific nginx config set this session.
- Fail-fast Pydantic validator pattern: HIGH — stable, well-documented API; matches existing pin.
- Chrome-extension / app-site scope boundary: this is a recommendation/open question, not a researched fact — flagged as such.

**Research date:** 2026-07-11
**Valid until:** Stable — this is a point-in-time grep inventory of the current codebase, not a fast-moving external dependency. Re-run the greps if significant code lands between now and `/gsd:plan-phase 14` execution.
