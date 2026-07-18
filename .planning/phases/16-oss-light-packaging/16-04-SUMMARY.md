---
phase: 16-oss-light-packaging
plan: 04
subsystem: verification
tags: [acceptance-gate, clean-install, oss-light, docker-compose, oauth, embeddings]
requires:
  - "16-01 (oauth_authorize local branch)"
  - "16-02 (oss-init.sh + profile-gated env-check)"
provides:
  - "infrastructure/scripts/verify-phase16.sh — the clean-install acceptance gate (config + .env-drift + oss-init + deploy-layer + REAL boot + SC#3 HTTP walk)"
  - "make verify-phase16"
affects:
  - "infrastructure/nginx/templates/10-xbrain.conf.template (defect fix)"
  - "infrastructure/docker-compose.yml (defect fix — brain-janitor ordering)"
tech-stack:
  added: []
  patterns:
    - "SKIP=FAIL structurally: downstream checks are recorded as FAILURES when the boot fails, never skipped"
    - "Negative-case assertions alongside positive ones (wrong password must not mint an OAuth code)"
    - "Row-level persistence assertions, not HTTP-status assertions"
key-files:
  created:
    - ".planning/phases/16-oss-light-packaging/16-04-SUMMARY.md"
  modified:
    - "infrastructure/scripts/verify-phase16.sh"
    - "infrastructure/nginx/templates/10-xbrain.conf.template"
    - "infrastructure/docker-compose.yml"
decisions:
  - "Fixed the two clean-install defects the gate found rather than reporting a red gate — both blocked the plan's own acceptance (SC#1)"
  - "The distinctive phrase is sent as the media `caption`, because media.py embeds `caption or filename`, not the file bytes — the gate asserts what actually runs"
  - "The saas env-check is driven with make's COMMAND-LINE override form, the only form that outranks the `-include .env` assignment"
metrics:
  tasks-completed: 1
  duration: "~2h"
  completed: 2026-07-18
---

# Phase 16 Plan 04: Clean-Install Acceptance Gate (Task 2) Summary

Appended the load-bearing half of `verify-phase16.sh` — deploy-layer `env-check`, a real
`up -d --build` of all 10 OSS-light core services from a generated zero-key `.env`, and the
full SC#3 walk over real HTTP through nginx. The gate immediately earned its keep: on its
first honest run it went **red on two genuine clean-install defects** that every prior
check had missed. Both are fixed; the gate is now **23/23, SKIP 0, exit 0**.

## What was built

Task 1's 340-line scaffold was **preserved verbatim** — the new checks were appended at the
`# TASK2_CHECKS_ANCHOR` marker, reusing its `ok/ko/skip` counters, EXIT trap, temp-file
slots, `host_path` helper and python-argv JSON convention.

| Check | What it proves | SC |
|-------|----------------|-----|
| (d0) | `make env-check` exits 0 on the zero-key `.env`, exits 1 for saas-without-saas-creds | SC#4 |
| (d)  | All 10 core reach **actual** `.State.Health.Status = healthy` from a real `up -d --build` | SC#1 |
| (e)  | register -> `xbt_` + `solo-<hex16>` scope; login -> `xbt_` (argon2 vs live Postgres) | SC#3 |
| (f)  | media upload 201, then **keyless** semantic retrieval carrying a `truth_level` | SC#3 |
| (g)  | `.well-known` 200, DCR, local consent form (no github 302), wrong-password mints nothing, right password mints a real `?code=` | SC#3 |
| (h)  | clip 201 **and** a real `memory_items` row | SC#3 |
| (i)  | zero opt-in containers running with `COMPOSE_PROFILES` unset | SC#2 |

Three design choices make the gate hard to fool:

- **SKIP=FAIL is structural, not a convention.** If `(d)` fails, `(e)`-`(i)` are emitted as
  `FAIL: ... not exercised: the core never booted` — there is no code path where a missing
  live core produces a green summary.
- **The search query is deliberately not the stored text.** `(f)` stores
  `"acme quarterly revenue was forty two million"` and queries
  `"how much revenue did acme make in the quarter"`. A substring match cannot satisfy it;
  only a real vector search can — and it ran with `EMBEDDINGS_PROVIDER=local` and no
  `OPENAI_API_KEY` anywhere (score 0.852).
- **`(g)` asserts the negative case first.** A wrong password must produce no code before
  the correct one is allowed to prove it does. A one-sided test would pass against an
  auth-bypass.

## Verbatim gate output (final run, post-fix)

```
=== Phase 16 Verification (OSS Light Packaging — PKG-01) ===

(a) CONFIG — bare core is exactly the 10 services BY NAME; profiles == integrations ops saas
  PASS: bare-core diff empty (10/10, by name): brain-janitor centrifugo mcp-brain mcp-gateway mcp-scraper memory-api minio nginx postgres qdrant
  PASS: profiles == 'integrations ops saas' (no pro)

(b) ENV-DRIFT — .env.example: MinIO pw [required], no stale SaaS-only header, saas secrets un-[required]
  PASS: MINIO_ROOT_PASSWORD is tagged [required] (not [optional]) — the core minio 8-char floor is honored
  PASS: stale 'SaaS-only' header is gone (grep -c == 0)
  PASS: the 'REQUIRED — core boot' section header exists
  PASS: MEILI_MASTER_KEY/JWT_SECRET/CREDS_KEY/CREDS_IV/MONGO_URI/PIPELINE_API_KEY no longer carry [required]

(c) OSS-INIT — oss-init.sh writes a bootable zero-external-key env
  PASS: generated env is zero-key + bootable (EDITION=oss, local embeddings, 1 worker, MinIO pw >= 8, DATABASE_URL pw == POSTGRES_PASSWORD, no __FILL__)

(d0) DEPLOY-LAYER — 'make env-check' passes zero-key, still fails saas-without-saas-creds
  NOTE: 'make' is absent on this host — executing the env-check recipe BODY directly
        (logic-equivalent, NOT a skip; see run_env_check).
  PASS: (d0) zero-key .env passes the deploy prerequisite with COMPOSE_PROFILES unset: All required env vars present (profile: []).
  PASS: (d0) saas profile still fails without saas creds, naming the var: MISSING: GOOGLE_CLIENT_ID (required for profile [saas]; GOOGLE_*/MEILI_MASTER_KEY/OPENWEBUI_SECRET_KEY are needed only under the saas profile)

(d) REAL BOOT — up -d --build the 10 core from the zero-key env; assert ACTUAL health state
      ingress: http://127.0.0.1:80 with Host: api.localhost (only nginx publishes a host port)
  PASS: (d) 'up -d --build' returned 0 (all 10 core services created from the zero-key env)
      brain-janitor: running/healthy
      centrifugo: running/healthy
      mcp-brain: running/healthy
      mcp-gateway: running/healthy
      mcp-scraper: running/healthy
      memory-api: running/healthy
      minio: running/healthy
      nginx: running/healthy
      postgres: running/healthy
      qdrant: running/healthy
  PASS: (d) all 10 core services reached healthy/running from a REAL build+boot (incl. the four build: services)

(e) SC#3 auth — POST /v1/auth/local/register -> xbt_ + solo- team_scope; login -> xbt_
  PASS: (e) register -> 200, xbt_ token minted, team_scope='solo-17227b2e4f4f499e'
  PASS: (e) login -> 200 with a fresh xbt_ token (argon2 verify against the live Postgres)

(f) SC#3 doc — /v1/media/upload then KEYLESS semantic retrieval with a truth_level
  PASS: (f) upload -> 201 item_id=2154d1de-c298-4bea-8c96-b142470b3ddc (bytes in MinIO, item embedded by the LOCAL provider)
  PASS: (f) KEYLESS semantic retrieval (query != stored text) returned the doc WITH a truth_level: matched content='acme quarterly revenue was forty two million' truth_level='WORKING' score=0.85230464

(g) SC#3 connector — .well-known 200; /oauth/authorize renders the LOCAL form; local POST mints a code
  PASS: (g) /.well-known/oauth-authorization-server -> 200 (AS discovery is core, never gated)
  PASS: (g) DCR (RFC 7591) minted a public client: client_id=oac_xN_TiEjdpYFhxRdqfs8ytXykiQvJTghm
  PASS: (g) /oauth/authorize -> 200 rendering the LOCAL login form (action=/oauth/authorize/local), NOT a 302 to github.com
  PASS: (g) wrong password does NOT mint a code (code=401, no redirect) — consent requires a real credential proof
  PASS: (g) POST /oauth/authorize/local -> 302 to the REGISTERED redirect_uri carrying a minted ?code= (zero-key connector consent works)

(h) SC#3 clip — POST /v1/memory/upsert source=manual-clip -> 201 + a real memory_items row
  PASS: (h) clip upsert -> 201 (id=a6122db5-5a9c-4726-a690-55ece558b14c)
  PASS: (h) memory_items row landed in Postgres: count(source='manual-clip') = 1

(i) SC#2 boundary — zero opt-in containers running with COMPOSE_PROFILES unset
  PASS: (i) none of the 22 opt-in containers are running (integrations/ops/saas stayed off)

=== Summary ===
PASS: 23 / 23  (SKIP: 0)
GATE_EXIT=0
```

## Per-SC status

| SC | Status | Evidence |
|----|--------|----------|
| SC#1 clean-install end-to-end | **GREEN** | (d) — 10/10 healthy from a real build+boot on clean Docker; required two defect fixes below |
| SC#2 COMPOSE_PROFILES-unset boundary | **GREEN** | (a) config diff + (i) zero of 22 opt-in containers actually running |
| SC#3 zero-key brain works | **GREEN** | (e)(f)(g)(h) — all over real HTTP, no external key in the environment |
| SC#4 deploy prerequisite passes zero-key | **GREEN** | (d0) — exit 0 zero-key, exit 1 saas-without-creds |

Nothing is BLOCKED. Every live check ran for real; `SKIP: 0`.

## The first (red) run — what the gate caught

The gate's first honest run ended `PASS: 10 / 16 (SKIP: 0)`, exit 1, with `(d)` reporting
`brain-janitor=starting nginx=unhealthy` and `(e)`-`(i)` recorded as failures. Both causes
were real, both were pre-existing, and neither is OSS-light-specific:

**Defect 1 — `xbrain-nginx` is `unhealthy` forever, in every install.** (commit `07e3910`)

The healthcheck probes `http://127.0.0.1/nginx-health`. That `Host` matches no
`server_name`, so it lands in the `default_server` block, whose **server-level**
`return 302 https://chat.$XBRAIN_BASE_DOMAIN$request_uri` executes in the
`NGX_HTTP_SERVER_REWRITE_PHASE` — which runs *before* `NGX_HTTP_FIND_CONFIG_PHASE` selects
a location. So it preempts every location in the block. `00-health.conf.template` could not
rescue it either: that block declares `server_name _` *without* `default_server`, which
matches nothing literally, making it unreachable dead config. The access log was
unambiguous: `"GET /nginx-health HTTP/1.1" 302`, forever.

Fix: keep the redirect but move it inside `location /`, and add an explicit
`location /nginx-health`. Real-traffic redirect behaviour is unchanged. This matters beyond
this gate — it poisons `docker compose up --wait` and any health-gated deploy.

**Defect 2 — `brain-janitor` races the migration on a clean install.** (commit `cc7df51`)

`memory-api` owns the schema (`alembic upgrade head` runs before uvicorn binds), but
`brain-janitor` only waited on postgres+qdrant. On genuinely clean Docker it won the race:

```
brain_janitor.boot          qdrant_collection=messages retention_days=30
brain_janitor.boot_run_failed  error='relation "memory_items" does not exist'
brain_janitor.sleep         next_run_utc=2026-07-19T03:00:00+00:00 seconds=55602
```

Because the alive-file its healthcheck stats is only touched after a *successful* cycle, the
janitor then slept until the next 03:00 UTC while reporting unhealthy (healthcheck interval
1h) for up to ~15h on a fresh install. Fix: `depends_on: memory-api: service_healthy`.
`memory-api` is untagged core, so unlike the deliberately-absent neo4j edge (D-15-03) this
one is profile-safe.

Both were invisible to every previous check because they only manifest on a genuinely clean
Docker. This is precisely the gate lesson the plan was written around.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] nginx `/nginx-health` unreachable behind a server-level `return 302`**
- **Found during:** Task 2, check (d), first real run
- **Issue:** `xbrain-nginx` permanently `unhealthy` in every install (detail above)
- **Fix:** moved the legacy redirect into `location /`; added `location /nginx-health`
- **Files modified:** `infrastructure/nginx/templates/10-xbrain.conf.template`
- **Commit:** `07e3910`

**2. [Rule 3 - Blocking] brain-janitor boots before the schema exists**
- **Found during:** Task 2, check (d), first real run
- **Issue:** clean-install race against `alembic upgrade head`; janitor unhealthy ~15h
- **Fix:** added `memory-api: { condition: service_healthy }` to its `depends_on`
- **Files modified:** `infrastructure/docker-compose.yml`
- **Commit:** `cc7df51`

**3. [Rule 1 - Bug] Task 1's EXIT trap silently leaked the whole stack**
- **Found during:** Task 2, while wiring the boot teardown
- **Issue:** `cleanup()` ran `docker compose down` under `MSYS_NO_PATHCONV=1`. That flag
  suppresses the MSYS rewrite that `--env-file` *depends on*, so docker resolved `/tmp/x`
  as `D:\tmp\x` and aborted with `couldn't find env file` — swallowed by the `>/dev/null`
  redirect. The teardown was a no-op and every gate run would have leaked 10 containers
  plus volumes. Confirmed empirically: an identical warm-up build failed the same way.
- **Fix:** dropped `MSYS_NO_PATHCONV=1` from the teardown (it is correct only for
  in-container paths, e.g. the `docker exec` in check (h)); documented the rule inline
- **Files modified:** `infrastructure/scripts/verify-phase16.sh`
- **Commit:** `e903ee4`
- **Verified:** after the final run — 0 containers, 0 volumes, no residual `.env`

### Plan contract adjusted to the real code path

**`(f)` sends the distinctive phrase as the media `caption`, not only as file bytes.**
The plan says "a small text file whose body contains a distinctive phrase". But
`media.py:111` embeds `caption or file.filename` — the uploaded bytes go to MinIO and are
*not* the embedded text; file-body text extraction is not implemented on this route. Writing
the phrase only into the file would have produced a check that either always failed or
silently proved nothing. The gate therefore sends the phrase as the caption **and** writes
it into the file, and says so in an inline comment. The keyless-embedding proof is
unaffected — the caption is embedded by the local provider and retrieved by a non-verbatim
semantic query.

**`(d0)` drives `env-check` with make's command-line override form.**
The plan and the Makefile comment both write `COMPOSE_PROFILES=saas make env-check`. That is
the *environment* form, and `Makefile:5` does `-include .env` — a makefile assignment beats
the environment, so the generated `.env`'s `COMPOSE_PROFILES=` line would win and the saas
case would pass instead of fail, silently voiding the assertion. The gate uses
`make env-check COMPOSE_PROFILES=saas` (command-line, highest precedence) and the no-make
fallback emulates exactly that precedence.

## Environment notes

- **`make` is not installed on this host.** Per the plan's constraint, `(d0)` detects this and
  executes the recipe body directly rather than skipping. The run prints a `NOTE:` line so the
  substitution is visible, never silent. The fallback reproduces both make semantics that
  matter (`$(COMPOSE_PROFILES)` expansion and command-line precedence).
- **Host paths vs in-container paths** (now documented inline in two places, because getting it
  backwards fails *silently*): `-f` / `--env-file` are host paths and MSYS **must** rewrite
  them; `MSYS_NO_PATHCONV=1` belongs only on commands carrying an in-container path.
- **`oss-init.sh` already emits `OAUTH_ISSUER_URL` / `OAUTH_RESOURCE_URL`** (lines 111-112), so
  the anticipated boot-fatal gap did not exist and no patch was needed. Verified by the core
  booting from the generated env with zero edits.
- The expected ~8.5s Neo4j DNS timeout during memory-api startup occurred and is not a failure
  (Neo4j is not in core).
- Host: Windows ARM64, Docker daemon `arm64/linux`, clean Docker (0 pre-existing containers).

## Known Stubs

None. Every check in the gate performs a real assertion against a live system; there are no
placeholder branches, mocked responses, or hardcoded pass values.

## Self-Check: PASSED

Files:
- FOUND: `infrastructure/scripts/verify-phase16.sh`
- FOUND: `infrastructure/nginx/templates/10-xbrain.conf.template`
- FOUND: `infrastructure/docker-compose.yml`
- FOUND: `.planning/phases/16-oss-light-packaging/16-04-SUMMARY.md`

Commits:
- FOUND: `07e3910` fix(16-04): serve /nginx-health from the default_server
- FOUND: `cc7df51` fix(16-04): order brain-janitor after memory-api
- FOUND: `e903ee4` feat(16-04): deploy-layer env-check + real core boot + SC#3 HTTP walk

Acceptance greps (all non-zero):
- `up -d --build`, `make env-check`, `/oauth/authorize/local`, `manual-clip`,
  `xbrain-postgres`, `MSYS_NO_PATHCONV`
- `bash -n infrastructure/scripts/verify-phase16.sh` -> syntax OK
- `bash infrastructure/scripts/verify-phase16.sh` -> `PASS: 23 / 23 (SKIP: 0)`, exit 0

State files: `STATE.md` and `ROADMAP.md` deliberately **not** modified (parallel-executor
constraint).
