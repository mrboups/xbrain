# Phase 14: Portability Foundation - Context

**Gathered:** 2026-07-11
**Status:** Ready for planning
**Source:** Locked decisions from open-core → OSS pivot discussion (see `.planning/features/open-core-edition-design.md` "Locked Decisions — 2026-07-11")

<domain>
## Phase Boundary

**This phase delivers:** an xbrain codebase that an operator can point at their own
domain and keys via **config alone** — no `grooveos.app`, no `aibrussels`, no hardcoded
`default` team_scope baked into source — plus a slim, documented OSS `.env.example` that a
fresh operator can fill without reading source code. (PORT-01, PORT-02)

**Grounded inventory (measured 2026-07-11, runtime source only, excluding tests):**
- `grooveos.app` — ~14 files. Most are already Pydantic `Settings` defaults read from env
  (functionally portable, but the default bakes the brand); the rest are comments/docstrings,
  the Haiku few-shot examples in `relevance_filter.py`, and 2 true hardcodes in
  `notifications.py` (`dashboard_url` default + `noreply@grooveos.app` email footer).
- `aibrussels` — **0 occurrences in runtime source.** Only in `apps/spike-mem0/test_data.json`,
  tests, docs, and as a real team row created via the normal sign-up flow. There is **no seed
  team hardcoded in prod**; the real fallback string is `"default"`.
- `"default"` team_scope — 1 runtime code spot (`apps/memory-api/app/routes/me.py:206`,
  a Pydantic `Field(default="default")` for team name) plus infra scripts
  (`clean-start.sh`, `register-mcp-tools.sh`, `verify-*.sh`).

**Explicitly OUT of scope for this phase** (later phases — do NOT plan them here):
- Local email/password auth for OSS (Q2) — later phase.
- Local embeddings default / single-key operation (Q3) — later phase.
- Web group-chat frontend, dropping LibreChat/Open WebUI (Q4) — later phase (Phase 16).
- AGPLv3 `LICENSE` + `CLA.md` + trademark (OSS packaging) — later phase (Phase 16).
- Compose `profiles:` / edition toggles (Phase 15).

</domain>

<decisions>
## Implementation Decisions

### D-01 — Full cleanup scope (runtime AND docs AND .planning history) [Q1]
De-hardcode `grooveos.app`, `aibrussels`, and the `"default"` team_scope across the WHOLE
repo — runtime source **and** docs/KB/marketing **and** the `.planning/` history — NOT
runtime-only. (User confirmed 2026-07-11/12: "je ferai quand même le nettoyage total… on
nettoie l'historique aussi.") Replace occurrences with neutral placeholders (e.g.
`example.com`, a `your-team`-style token). Runtime correctness is the blocking part;
docs/KB/planning scrub is required but non-blocking for boot, and is **mechanical** — the bulk
is a scriptable find/replace; a **Sonnet executor** handles the judgment cases. Real magnitude
(measured 2026-07-11, see 14-RESEARCH.md): `grooveos.app` ~1009 occ / 203 files (only ~123 in
runtime/infra), `aibrussels` 105 occ, `"default"` team_scope ~49 genuine.

**Keep-as-is exceptions (do NOT scrub these):**
- The design doc `.planning/features/open-core-edition-design.md` "Locked Decisions — 2026-07-11"
  table — it *names* grooveos.app/aibrussels as the record of what is being removed; rewriting it
  makes the decision record meaningless. (Aligns with ROADMAP SC#1 "outside … the design doc".)
- This `14-CONTEXT.md` and `14-RESEARCH.md` (same reason — they document the cleanup).
- Test fixtures + illustrative few-shot examples (e.g. Haiku examples in `relevance_filter.py`,
  `apps/spike-mem0/test_data.json`) → neutral placeholder, not deletion.

### D-01b — Factual corrections from research (supersede earlier CONTEXT claims)
- `apps/memory-api/app/routes/me.py:206` (`Field(default="default")`) is a **FALSE POSITIVE** —
  it is a personal-API-token `name` default, NOT a team_scope fallback. Do not "fix" it.
- Two runtime hardcodes NOT in the original canonical_refs, both in scope for Phase 14:
  `apps/memory-api/app/knowledge/xbrain_product_kb.md` (injected verbatim into the live @groove
  agent system prompt — a **functional** domain leak → rewrite domain-neutral) and
  `apps/librechat/patches/onboarding.js` (hardcoded API base URL).
- **Fail-fast landmine (D-03):** `apps/mcp-brain/app/main.py` computes
  `_PROTECTED_RESOURCE_METADATA_URL` from `OAUTH_RESOURCE_URL` at **module-import time** — the
  empty-URL fail-fast MUST be a Pydantic `field_validator` on the `Settings` class, not a
  per-request check (too late).

### D-01c — Frontend static JS deferred to Phase 16 [research Open Q1]
`chrome-extension/**` and `app-site/account/**` also hardcode `grooveos.app` in static client
bundles with no config mechanism today. These are **deferred to Phase 16** (where the new
web-chat UI is built and the extension is opt-in). Phase 14 = backend + infra + config only.

### D-01d — nginx templating IN scope [research Open Q2]
`infrastructure/nginx/conf.d/*.conf` (7 files, 18 `server_name` hardcodes) must become
env-driven via the official nginx image's `envsubst` template mechanism — otherwise "point at
your own domain via config alone" (PORT-01) is violated (operator would still edit source).

### D-02 — Public-URL config defaults → neutral, not brand [Q1]
For public URL settings that are already env-driven (`MEMORY_API_EXTERNAL_URL`,
`CENTRIFUGO_WS_URL_PUBLIC`, `SMTP_FROM`, `DRIVE_WEBHOOK_PUBLIC_URL`, etc.), replace the
`*.grooveos.app` default with a neutral local default (e.g. `http://localhost:8000`,
`noreply@example.com`) so a bare `docker compose up` boots without any config.

### D-03 — OAuth identity URLs → empty + fail-fast [Q1]
For identity-critical OAuth settings (`OAUTH_ISSUER_URL`, `OAUTH_RESOURCE_URL` in both
`memory-api` and `mcp-brain`), default to empty string and **fail fast at boot with a clear
error** when unset in a mode that needs them, rather than shipping a misleading `grooveos.app`
default that breaks the connector silently.

### D-04 — No auto-seed team [Q2-adjacent portability]
Do NOT introduce an auto-created seed team at first boot. A fresh install has an empty brain;
the first user creates their team via the existing `POST /v1/teams/self-solo` flow. Reason:
a hardcoded/auto-seed team creates orphan-membership bugs (the exact `default`-team class of
bug already fixed multiple times). Keep the `"default"` team_scope literal as a neutral
fallback string for now (it is generic, not a brand, and not a portability blocker) — do not
expand its use.

### D-05 — True hardcodes get env fallbacks [Q1]
The 2 real hardcodes with no env fallback — `notifications.py` `dashboard_url` default and
the `noreply@grooveos.app` email footer — must read from config/env (reuse `SMTP_FROM` /
add an `APP_PUBLIC_URL` setting) with neutral defaults.

### D-06 — Slim, documented OSS `.env.example` [PORT-02]
Produce a slim OSS `.env.example` an operator can fill without reading source: every required
var documented with a one-line comment and a safe placeholder; group by concern
(core / LLM keys / storage / optional integrations); mark which are required for a minimal
boot vs optional. The current `.env.example` (~115 vars) is trimmed/reorganized for the
OSS-light surface, not the full SaaS surface.

### D-07 — Config-only portability is the acceptance bar [PORT-01]
After this phase, pointing the whole stack at a new domain + new keys must require **zero
source edits** — only `.env` / config changes. A grep for `grooveos.app` / `aibrussels` over
runtime source returns zero configuration occurrences (comments/few-shot examples excepted
per D-01).

### Claude's Discretion
- Exact new setting names (`APP_PUBLIC_URL` vs reusing existing), file-by-file edit order,
  and how the `.env.example` sections are grouped.
- Whether infra scripts (`clean-start.sh`, `verify-*.sh`) take the team_scope from an env var
  or keep the neutral `"default"` literal (both acceptable; env var preferred if cheap).
- How exhaustively to scrub `.planning/` history vs a forward-only convention (the planner
  may propose a pragmatic bound and log what it skips).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Locked decisions & model
- `.planning/features/open-core-edition-design.md` — "Locked Decisions — 2026-07-11" table is
  authoritative; supersedes the older open-core blueprint in the same file.

### Requirements
- `.planning/REQUIREMENTS.md` — milestone v2.0 section (PORT-01, PORT-02); note the model-shift
  banner (EDIT-03 dropped).

### Config surfaces to de-hardcode (primary targets)
- `apps/memory-api/app/config.py` — Pydantic Settings; the main default surface.
- `apps/mcp-brain/app/config.py` — `OAUTH_ISSUER_URL`, `OAUTH_RESOURCE_URL`.
- `apps/drive-sync/app/config.py` — `DRIVE_WEBHOOK_PUBLIC_URL`.
- `apps/memory-api/app/services/notifications.py` — 2 true hardcodes.
- `apps/memory-api/app/routes/me.py:206` — `Field(default="default")`.
- `apps/memory-api/app/routes/waitlist.py` — already env-driven (reference for the pattern).
- `infrastructure/scripts/clean-start.sh`, `register-mcp-tools.sh`, `verify-*.sh` — `default` team_scope.
- `.env.example` (repo root / infrastructure) — slim OSS rewrite target (PORT-02).

</canonical_refs>

<specifics>
## Specific Ideas

- The single most valuable early task is an **exhaustive grep inventory** (runtime + docs) of
  every `grooveos.app`, `aibrussels`, and `"default"` team_scope occurrence, classified as:
  (a) env-driven default → neutralize, (b) true hardcode → add env fallback, (c) comment/doc/
  example → scrub or keep-if-illustrative. The research step should produce this table.
- Acceptance is verifiable by grep: after the phase, `grep -rn "grooveos.app" apps/**/app` and
  `grep -rn "aibrussels" apps/**/app` return only illustrative/comment matches (documented).

</specifics>

<deferred>
## Deferred Ideas

- **Q2** local email/password auth (OSS default) — own later phase.
- **Q3** local embeddings default + single-key (Anthropic/OpenAI/Grok) operation — own later phase.
- **Q4** web group-chat frontend; drop LibreChat/Open WebUI from OSS-light — Phase 16.
- **AGPLv3 LICENSE + CLA.md + trademark** — OSS packaging (Phase 16).
- **Compose `profiles:` + edition toggles** — Phase 15 (license/Ed25519 system DROPPED entirely).

</deferred>

---

*Phase: 14-portability-foundation*
*Context gathered: 2026-07-11 from locked open-core→OSS pivot decisions (not via /gsd-discuss-phase)*
