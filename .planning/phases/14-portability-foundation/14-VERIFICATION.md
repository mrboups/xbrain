---
phase: 14-portability-foundation
verified: 2026-07-12T05:59:20Z
status: passed
score: 17/17 must-haves verified
overrides_applied: 0
deferred:
  - truth: "SC#2b — live regression suite (verify-phase1..13.sh) passes against a running deployment with prod values"
    addressed_in: "Next real deploy (VM currently TERMINATED — cost pause)"
    evidence: "14-06-SUMMARY.md contains the literal '## DEFERRED GATE — run at the next real deploy' section with the full 5-step runbook and all 5 mandatory .env vars with prod values; ROADMAP.md SC#2 text itself states this is an amended, explicitly deferred gate, not a pass/fail criterion for this verification."
---

# Phase 14: Portability Foundation Verification Report

**Phase Goal:** An operator can point the entire stack at their own domain and keys via config alone — no source edit — and can configure a fresh install from a slim, documented OSS `.env.example` without reading source code.
**Verified:** 2026-07-12T05:59:20Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

This verification does **not** trust SUMMARY.md claims. Every truth below was re-derived from the
current codebase: greps against source, independent `pytest` runs, an independent run of
`verify-phase14.sh` and `preflight-env.sh`, and live Docker container runs of the nginx image,
the centrifugo image, and the LibreChat entrypoint's envsubst logic (mounted with Windows-style
paths + `MSYS_NO_PATHCONV=1` per the task's guidance, so the checks exercised the real templates
on disk, not the stock image defaults).

The task flagged two already-known, already-fixed critical defects from `14-REVIEW.md`
(CR-01: `AGENT_MENTION_ALIASES` not reaching `memory-api`; CR-02: `30-projects.conf.template`
hardcoding the maintainer's Firebase project). Both were independently re-verified as fixed in
commit `fa15388`, with functional Docker proof, not just a grep. The adversarial hunt for
"the same class of bug elsewhere" turned up two additional, narrower findings (documented below
as WARNING-level anti-patterns) — neither blocks the phase goal.

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | SC#1 — repo-wide `grooveos`/`aibrussels` scan (bare token) returns zero matches in backend runtime source, infra, and technical docs outside documented exemptions | VERIFIED | Independent scan across `apps/`, `infrastructure/`, `Makefile`, `.github/`, `CLAUDE.md`, `docs/`, `.planning/` (minus exempt dirs) found only: verifier's own pattern literals (`verify-phase14.sh:53,176` — self-documented exclusion), `deploy-dashboard.yml` (explicit D-01e-EXTENDED exemption), test fixtures (`test_mention_detector.py` — D-01 neutral-placeholder exemption, asserts prod-parity alias list), and gitignored/untracked artifacts (`.env`, `.pytest_cache`, `.remember/`). `git ls-files` confirms none of the untracked hits are committed. |
| 2 | CR-01 fix — `AGENT_MENTION_ALIASES` reaches **both** `memory-api` and `librechat` containers | VERIFIED | `infrastructure/docker-compose.yml:137` (memory-api) and `:461` (librechat) both set `AGENT_MENTION_ALIASES: ${AGENT_MENTION_ALIASES:-agent}`. Functional proof: Docker run of the real entrypoint logic against `librechat.yaml.template` renders `mention @chad` when `AGENT_MENTION_ALIASES=chad,agent` and `mention @agent` when unset — matching the alias set `mention_detector.py` (`apps/memory-api/app/services/mention_detector.py:23-43`) would actually resolve, since both containers now read the same var. |
| 3 | CR-02 fix — `30-projects.conf.template` no longer hardcodes the maintainer's Firebase project | VERIFIED | `infrastructure/nginx/templates/30-projects.conf.template:38-42` now reads `${XBRAIN_PROJECTS_FALLBACK_URL}` and returns 404 when empty. `docker-compose.yml:51` wires `XBRAIN_PROJECTS_FALLBACK_URL: ${XBRAIN_PROJECTS_FALLBACK_URL:-}` with the `^XBRAIN_` envsubst filter. Docker render of the real nginx image with the var empty produced `set $projects_fallback ""; ... return 404;` — verified by direct container run, not just grep. `.env.example:87` documents it. |
| 4 | SC#4 / OAuth fail-fast — empty `OAUTH_ISSUER_URL`/`OAUTH_RESOURCE_URL` crashes `Settings()` in **both** memory-api and mcp-brain at boot | VERIFIED | `apps/memory-api/app/config.py:158-166` and `apps/mcp-brain/app/config.py` both carry the `field_validator`. Re-ran `verify-phase14.sh` check (b) independently: both `Settings()` constructions raise non-zero on empty OAuth vars. |
| 5 | SC#4 / CORS is env-sourced, not hardcoded | VERIFIED | `apps/memory-api/app/main.py:95` reads `allow_origin_regex=settings.CORS_ALLOWED_ORIGIN_REGEX` (confirmed by direct read, zero `grooveos` in the file). WR-03 hardening also confirmed: `config.py:168-201` rejects a `.*`-equivalent CORS regex functionally (`pattern.fullmatch("https://attacker.example")` probe), closing the review's `.*` + `allow_credentials=True` risk. |
| 6 | SC#4 / nginx `server_name` is driven by one var (`XBRAIN_BASE_DOMAIN`) and the rendered config is provably bootable | VERIFIED | Docker run of `nginx:1.27-alpine` against the real templates (`-v D:\...\nginx\templates:/etc/nginx/templates:ro`, `MSYS_NO_PATHCONV=1`) with `XBRAIN_BASE_DOMAIN=acme.example` → `nginx -t` exits 0 ("syntax is ok" / "test is successful"). Re-rendered with `XBRAIN_BASE_DOMAIN=grooveos.app` → the resulting `server_name` set is byte-identical (sorted) to the pre-phase `conf.d` set: `_` (×2), `adm/api/bridge/centrifugo/chat/lang/mcp/projects.grooveos.app` — SC#2 regression proof independently reproduced, not merely re-quoted from the SUMMARY. |
| 7 | SC#3 — `.env.example` is slim, grouped, documents every now-required var, and states the OAuth boot-crash consequence | VERIFIED | 6 `# === ` section headers present; `XBRAIN_BASE_DOMAIN`, `APP_PUBLIC_URL`, `CORS_ALLOWED_ORIGIN_REGEX`, `AGENT_MENTION_ALIASES`, `OAUTH_ISSUER_URL`, `OAUTH_RESOURCE_URL` all present with `[required]`/`[optional]` tags (`.env.example:77-122`); OAuth section header literally reads "CRASH AT BOOT" (`.env.example:117`). `grep -c grooveos .env.example` = 0. |
| 8 | Deploy cannot silently crash-loop — `make env-check`/`preflight-env.sh` hard-fail on any of the 5 now-mandatory vars, `deploy` depends on both, and the VM guard checks the same 5 | VERIFIED | Independently ran `preflight-env.sh` three times: missing all 5 → `FATAL: OAUTH_ISSUER_URL...` exit 1; missing `XBRAIN_BASE_DOMAIN` only → `FATAL: XBRAIN_BASE_DOMAIN is not set... TOTAL INGRESS OUTAGE` exit 1; all 5 present → `PREFLIGHT OK` exit 0. `Makefile:65` — `deploy: env-check preflight sync`; `Makefile:71` — remote SSH loop checks the same 5 vars against the VM `.env`. |
| 9 | Centrifugo `allowed_origins` is env-driven and `chrome-extension://*` survives by default | VERIFIED | `infrastructure/centrifugo/config.json` `client.allowed_origins` is `[]` (key kept, value emptied); `docker-compose.yml:978` supplies `CENTRIFUGO_CLIENT_ALLOWED_ORIGINS: ${CENTRIFUGO_ALLOWED_ORIGINS:-chrome-extension://* http://localhost:8080}`. Independent Docker run of `centrifugo/centrifugo:v6` with the real config: allowed origin → WS upgrade `101`; disallowed origin → `403`. |
| 10 | `librechat.yaml` resolves `LIBRECHAT_ALLOWED_DOMAINS`/`BRIDGE_BASE_URL`/`APP_TEAMS_URL` from env, `gmail.com` preserved, internal `mcpSettings.allowedDomains` untouched | VERIFIED | `infrastructure/librechat/librechat.yaml.template:28,84,211` use `${VAR}`; `gmail.com` still present (line 29); `mcp-gateway:8081` internal host untouched (line 165). The entrypoint (`apps/librechat/patches/render-config-entrypoint.sh`) is wired as the Dockerfile `ENTRYPOINT` (`apps/librechat/Dockerfile:85`) and its `envsubst` allow-list names all 4 vars including `AGENT_MENTION_PRIMARY`. Directly re-ran the entrypoint's exact envsubst call against the real template in a container — substitution works. |
| 11 | 5 LibreChat `promptPrefix` strings say `@agent` by default, not `@groove`; `GrooveOS` product name (title-case) is byte-identical, unchanged | VERIFIED | `grep -c '@groove' librechat.yaml.template` = 0; `grep -c '@${AGENT_MENTION_PRIMARY}'` = 5; `grep -c 'GrooveOS'` = 5 (matches pre-task count per 14-07's own before/after check). Docker-rendered proof: unset alias → `mention @agent`; `chad,agent` → `mention @chad`. |
| 12 | KB (`xbrain_product_kb.md`), relevance-filter few-shots, and `onboarding.js` carry no domain leak and no brand mention alias | VERIFIED | `grep -c grooveos` = 0 on all three; KB documents `@agent` + `AGENT_MENTION_ALIASES` configurability (lines 142-146); relevance_filter uses `example.com` (lines 293, 325); `onboarding.js` uses the `__XBRAIN_MEMORY_API_BASE__` build-time placeholder with a same-origin fallback (lines 14-15), wired via `Dockerfile:39,41` (`ARG MEMORY_API_BASE_URL` + `sed`). |
| 13 | Both pytest suites (memory-api, mcp-brain) — plus librechat-bridge, scrubbed by 14-05 — collect and pass with the fail-fast validator active | VERIFIED | Independently re-ran all three: memory-api `1 failed, 198 passed, 202 skipped` (the 1 failure is `test_github_sync.py::test_sync_repo_multi_chunk_ids`, confirmed pre-existing against base commit `f2f719a` — zero diff on the file, out of scope per the task's known-issues list); mcp-brain `21 passed`; librechat-bridge `57 passed`. |
| 14 | Test fixtures / illustrative examples are neutralized to a placeholder, not deleted, and prod-parity assertions still hold | VERIFIED | `test_mention_detector.py` retains the full legacy alias set (`agent,grooveos,groove,gr,g`) as an explicit backward-compat test case (lines 68-101) — this is the D-01-sanctioned exemption, not a leak; it is excluded from `verify-phase14.sh`'s scan and from this verification's brand scan. `apps/mcp-brain/chatgpt-actions.json` uses `https://api.example.com`, still valid JSON. `apps/spike-mem0/test_data.json` still valid JSON. |
| 15 | `verify-phase14.sh` actually proves PORT-01/PORT-02 (not merely reports green) | VERIFIED | Independently re-ran the full script (not trusted from SUMMARY): `PASS: 8 / 8 (SKIP: 0)`, exit 0. All 7 lettered checks individually re-derived above (rows 1, 4, 5, 6, 7) rather than taken on the script's own word. |
| 16 | WR-01/WR-02/WR-04 hardening from `14-REVIEW.md` (blank-alias bare-`@` match, quote-injection into rendered YAML, unescaped HTML in the waitlist endpoint) | VERIFIED | `mention_detector.py:31-37` falls back to `["agent"]` on an empty/blank alias list (was: bare `@` match). `render-config-entrypoint.sh:38` now uses `tr -cd 'A-Za-z0-9_-'` (charset whitelist, not blacklist) — a `"` in an alias can no longer corrupt the rendered YAML. `waitlist.py:39-41` HTML-escapes `body.name`/`body.email`/`body.plan` before interpolation. |
| 17 | SC#2b — deferred live-regression gate is recorded as an explicit, actionable runbook (not silently dropped) | VERIFIED (deferred by design) | `14-06-SUMMARY.md` contains the literal `## DEFERRED GATE — run at the next real deploy` heading with the 5-step runbook, all 5 mandatory vars + prod values, and states SC#2b is DEFERRED, not passed. Per the task's explicit instruction, this is not a gap. |

**Score:** 17/17 truths verified (0 overrides needed — no truth required an override; the one deferred item is explicitly sanctioned, not a failure).

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `apps/memory-api/app/config.py` | Neutral defaults + `field_validator` + `APP_PUBLIC_URL` + `CORS_ALLOWED_ORIGIN_REGEX` | VERIFIED | Confirmed all fields, both validators (OAuth fail-fast + CORS `.*` rejection). |
| `apps/mcp-brain/app/config.py` | Empty OAuth defaults + fail-fast validator | VERIFIED | Confirmed via `verify-phase14.sh` check (b), independently re-run. |
| `infrastructure/nginx/templates/*.conf.template` | `XBRAIN_BASE_DOMAIN`-driven vhosts, real render proof | VERIFIED | 8 templates present; Docker render + `nginx -t` pass; regression proof reproduces prod exactly. |
| `infrastructure/docker-compose.yml` | Zero brand, `AGENT_MENTION_ALIASES` + `XBRAIN_PROJECTS_FALLBACK_URL` wired to both consumers | VERIFIED | Confirmed line-by-line for memory-api, librechat, nginx, centrifugo, mcp-brain service blocks. |
| `.env.example` | Slim, grouped, documents every required var | VERIFIED | 6 sections, 11 new vars + 2 previously-hidden vars present, OAuth crash-consequence stated. |
| `infrastructure/scripts/verify-phase14.sh` | PORT-01/PORT-02 acceptance gate | VERIFIED | Re-run independently: 8/8 PASS, exit 0. |
| `infrastructure/scripts/preflight-env.sh` | Pre-deploy crashloop guard | VERIFIED | Re-run independently with 3 test cases; correct exit codes and messages. |
| `apps/memory-api/app/knowledge/xbrain_product_kb.md` | Domain-neutral KB | VERIFIED | Zero `grooveos`, documents `@agent` + `AGENT_MENTION_ALIASES`. |
| `infrastructure/librechat/librechat.yaml.template` | `${VAR}`-resolved brand strings, `@${AGENT_MENTION_PRIMARY}` mentions | VERIFIED | Confirmed via grep + Docker-rendered entrypoint proof. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `docker-compose.yml` (memory-api env) | `mention_detector.py` | `AGENT_MENTION_ALIASES` env passthrough | WIRED | Line 137 — the CR-01 fix; confirmed present in both consuming containers. |
| `docker-compose.yml` (nginx env) | `30-projects.conf.template` | `XBRAIN_PROJECTS_FALLBACK_URL` + `NGINX_ENVSUBST_FILTER=^XBRAIN_` | WIRED | Line 51; the CR-02 fix; Docker-rendered proof shows correct 404-by-default behavior. |
| `apps/memory-api/app/main.py` (CORSMiddleware) | `settings.CORS_ALLOWED_ORIGIN_REGEX` | direct attribute read | WIRED | `main.py:95`. |
| `docker-compose.yml` (nginx env) | `infrastructure/nginx/templates/*.template` | volume mount `/etc/nginx/templates` + `XBRAIN_BASE_DOMAIN` | WIRED | Confirmed by Docker render against the real mounted templates. |
| `docker-compose.yml` (librechat build.args) | `apps/librechat/patches/onboarding.js` | `MEMORY_API_BASE_URL` build-time `sed` | WIRED | `docker-compose.yml:410`, `Dockerfile:39,41`. |
| `docker-compose.yml` (librechat env) | `librechat.yaml.template` | `render-config-entrypoint.sh` envsubst | WIRED | Confirmed end-to-end via direct container run reproducing the entrypoint's exact substitution call. |
| `docker-compose.yml` (centrifugo env) | `infrastructure/centrifugo/config.json` | `CENTRIFUGO_CLIENT_ALLOWED_ORIGINS` overlay | WIRED | Confirmed via WS-origin smoke test against the real image + real config file. |

### Data-Flow Trace (Level 4) — CR-01-class hunt beyond the two already-fixed defects

Per the task's explicit instruction to enumerate every config var each service's `config.py`
declares and check it is actually delivered by that service's compose `environment:` block, the
full `memory-api` Settings surface (60 fields) was diffed against `docker-compose.yml`'s
`memory-api` environment block (lines 105-203). Two additional vars — **neither part of any
Phase-14 plan's declared must-haves, neither promised by ROADMAP SC#1-4, and neither a domain/key
hardcode** — were found undelivered:

| Var | Declared in | Wired to memory-api compose? | Consumed where | Verdict |
|-----|-------------|-------------------------------|-----------------|---------|
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_TLS` | `.env.example:157-161` (`[optional]`, pre-existing, predates Phase 14) | **No** — absent from `docker-compose.yml` memory-api block entirely | `apps/memory-api/app/services/notifications.py:27,56-60,89,123-127` | Fail-soft by design (`if not settings.SMTP_HOST: log.warning(...); return`), so no crash — but setting these in `.env` has **zero effect**; email notifications never send regardless of `.env` content. Confirmed pre-existing: `git show f2f719a:infrastructure/docker-compose.yml \| grep SMTP` returns nothing — this predates Phase 14 and is not part of its stated scope. |
| `QDRANT_COLLECTION` | `.env.example:213` (**newly added by 14-04** — "Qdrant collection name memory-api actually writes to") | **No** — only `brain-janitor` receives it (`docker-compose.yml:1108`, with a *different* default `memory_items` vs. memory-api's code default `messages`) | `apps/memory-api/app/repos/brain_metrics.py:119` (metrics read path only) | The var is documented by a Phase-14 deliverable as controlling what "memory-api actually writes to" — but (a) memory-api never receives it via compose, and (b) even `Settings.QDRANT_COLLECTION` would not change the write path: `apps/memory-api/app/qdrant_setup.py:11,25-38` bootstraps the collection from a **separately hardcoded** `COLLECTION_NAME = "messages"` constant that never reads `settings.QDRANT_COLLECTION`. An operator following the `.env.example` comment to isolate collections per environment gets no effect at all. |

**Disposition:** WARNING, not BLOCKER. Neither var is a domain reference, a credential URL, or an
OAuth/CORS/nginx/mention-alias surface — the four concerns SC#4 explicitly names. `SMTP_HOST`
predates the phase and was never in its declared scope. `QDRANT_COLLECTION` is a genuine "CR-01
class" gap introduced by a Phase-14 artifact (`.env.example`'s new documentation of it), but it is
narrow (one non-critical operational knob, not "domain and keys"), does not crash boot, does not
leak the brand, and does not block an operator from pointing the stack at their own domain — it
blocks a much smaller promise (per-environment collection naming) that no plan's must-haves or
ROADMAP SC claimed. Recorded here per the task's explicit hunt instruction; recommend a follow-up
quick-fix (wire `QDRANT_COLLECTION` into the memory-api compose block and have `qdrant_setup.py`
read `settings.QDRANT_COLLECTION` instead of its own hardcoded constant) rather than reopening
Phase 14.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| memory-api OAuth fail-fast | `OAUTH_ISSUER_URL= OAUTH_RESOURCE_URL= python -c "from app.config import Settings; Settings()"` | non-zero exit, error names `OAUTH_ISSUER_URL` | PASS |
| mcp-brain OAuth fail-fast | same, in `apps/mcp-brain` | non-zero exit | PASS |
| nginx real-image render (`acme.example`) | `docker run ... nginx:1.27-alpine nginx -t` | "syntax is ok" / "test is successful" | PASS |
| nginx regression proof (`grooveos.app`) | rendered `server_name` set vs. pre-phase `conf.d` set | byte-identical (sorted) | PASS |
| Centrifugo WS origin gate | allowed origin → 101, disallowed → 403 | as expected | PASS |
| LibreChat entrypoint alias derivation | unset → `mention @agent`; `chad,agent` → `mention @chad` | as expected | PASS |
| `preflight-env.sh` — missing all 5 vars | exit 1, `FATAL: OAUTH_ISSUER_URL...` | as expected | PASS |
| `preflight-env.sh` — missing only `XBRAIN_BASE_DOMAIN` | exit 1, `FATAL: XBRAIN_BASE_DOMAIN... TOTAL INGRESS OUTAGE` | as expected | PASS |
| `preflight-env.sh` — all 5 present | exit 0, `PREFLIGHT OK` | as expected | PASS |
| `verify-phase14.sh` full run | `bash infrastructure/scripts/verify-phase14.sh` | `PASS: 8 / 8`, exit 0 | PASS |
| memory-api pytest | `python -m pytest -q` | `1 failed, 198 passed, 202 skipped` | PASS (pre-existing failure confirmed out of scope) |
| mcp-brain pytest | `python -m pytest -q` | `21 passed` | PASS |
| librechat-bridge pytest | `python -m pytest -q` | `57 passed` | PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|--------------|--------|----------|
| PORT-01 | 14-01, 14-02, 14-03a, 14-03b, 14-05, 14-06, 14-07 | Operator can point the entire stack at their own domain and keys via config alone | SATISFIED | All source/infra/doc scan checks, OAuth fail-fast, CORS env-wiring, nginx templating, agent-mention config-drive, KB/onboarding neutralization all independently re-verified above. |
| PORT-02 | 14-04, 14-06 | Operator can configure a fresh install from a slim, documented OSS `.env.example` without reading source | SATISFIED | `.env.example` rewrite independently confirmed (6 sections, all required vars, OAuth crash-consequence stated); `verify-phase14.sh` check (f) independently re-run and passed. |

**Note (documentation hygiene, not a phase-14 gap):** `.planning/REQUIREMENTS.md:16` still reads
"no `example.com`, `your-team`, or hardcoded `default` team_scope remains in source" — this
literally contradicts the locked, user-approved ROADMAP amendment (D-04: `"default"` team_scope is
explicitly KEPT; `example.com`/`your-team` are the deliberate *replacement* placeholders that are
supposed to remain). This wording predates the 2026-07-12 ROADMAP amendment and was not touched by
any of the 8 phase-14 plans (none list `REQUIREMENTS.md` in `files_modified`). Not a code gap —
flagged for a future doc-sync pass. `.planning/STATE.md` also still reads "Executing Phase 14",
which is orchestrator bookkeeping updated post-verification, not a phase-14 deliverable.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `apps/memory-api/app/config.py` / `docker-compose.yml` | `.env.example:213`, `qdrant_setup.py:11` | `QDRANT_COLLECTION` documented as operator-configurable by a Phase-14 artifact but never wired to memory-api and never read by the actual collection-bootstrap code | WARNING | Setting this var per the shipped documentation has zero effect. Narrow scope (not domain/keys); does not block PORT-01/PORT-02's core promise. Recommend follow-up quick-fix. |
| `apps/memory-api/app/services/notifications.py` / `docker-compose.yml` | notifications.py:27,89 | `SMTP_HOST`/`PORT`/`USER`/`PASSWORD`/`TLS` documented `[optional]` in `.env.example` but never wired to memory-api's compose environment block | WARNING (pre-existing, out of Phase-14 scope) | Fail-soft — no crash, but email notifications never functionally work regardless of `.env`. Confirmed present before Phase 14 (`f2f719a`); not introduced by this phase, not claimed by any of its plans. |
| `.planning/BACKLOG.md` | 104 | One contextual mention of `grooveos.app` inside a note documenting a future rebrand decision (`teamchad.ai`), added 2026-07-12 (same day, mid-phase) | INFO | Narrative/decision context, same reasoning as the exempted design doc / `14-CONTEXT.md`. Not a leak; not in any plan's file scope. |
| `.planning/backups/site-2026-05-17-pre-repositioning/**`, `.planning/backups/site-repositioning-2026-05/**` | many | Dead HTML/markdown snapshots of the old marketing site, predating Phase 14 by ~2 months | INFO | Mirrors the D-01e exemption reasoning (backups of the exempt hosted-marketing surface); never in any plan's file scope; does not ship, does not run. |

### Human Verification Required

None. Every truth in this phase was verifiable programmatically (grep, `pytest`, and live Docker
container runs of the real nginx/centrifugo images plus the LibreChat entrypoint logic), so no
item is routed to human testing.

### Gaps Summary

No BLOCKER-level gaps found. Both critical defects the code review caught (CR-01: `AGENT_MENTION_ALIASES`
missing from memory-api's compose env; CR-02: `30-projects.conf.template` hardcoding the
maintainer's Firebase project) are confirmed fixed in commit `fa15388`, with functional Docker
proof — not merely a re-read of the fix commit's diff. All four hardening warnings from the review
(WR-01 through WR-04) are also confirmed present in the current tree.

The adversarial "enumerate every config.py var against every service's compose environment block"
hunt (the exact CR-01-class search the task requested) turned up two additional undelivered vars —
`QDRANT_COLLECTION` (newly exposed by 14-04, genuinely misleading since its own `.env.example`
description overstates what it controls) and `SMTP_HOST`/`PORT`/`USER`/`PASSWORD`/`TLS`
(pre-existing, predates the phase, fail-soft). Neither is a domain, key, OAuth, CORS, nginx, or
mention-alias surface — the four concerns the amended ROADMAP SC#4 explicitly names — and neither
was declared as a must-have truth by any of the 8 phase-14 plans or by the ROADMAP Success
Criteria. They are reported as WARNING-level findings for a follow-up quick-fix, not as phase-14
gaps, since fixing them was never part of this phase's contract.

SC#2b (the live regression suite against a running deployment) remains an explicitly DEFERRED
gate — the production VM is TERMINATED and cannot run it — recorded verbatim in
`14-06-SUMMARY.md`'s `## DEFERRED GATE` section with the full runbook, per the task's own
instruction that this is not a gap.

Phase 14's goal — "an operator can point the entire stack at their own domain and keys via config
alone... and can configure a fresh install from a slim, documented OSS `.env.example` without
reading source code" — is achieved and independently verified in the current codebase, not merely
claimed by SUMMARY.md.

---

_Verified: 2026-07-12T05:59:20Z_
_Verifier: Claude (gsd-verifier)_
