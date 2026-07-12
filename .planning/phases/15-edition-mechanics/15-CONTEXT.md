# Phase 15: Edition Mechanics — Context

**Gathered:** 2026-07-12
**Status:** Ready for planning
**Source:** Locked decisions in `.planning/features/open-core-edition-design.md` (Q1–Q7, 2026-07-11) + user decisions taken during this planning run (2026-07-12) + findings from the Phase 14 wiring audit.

<domain>
## Phase Boundary

Phase 15 makes **edition a deployment-time selection**, never a branch and never a separate image:

- **`COMPOSE_PROFILES`** decides *which containers run*.
- **`EDITION`** decides *which memory-api routers mount*.

The core (brain, chat, retrieval, truth-levels, media, teams, auth, ChatGPT-web connector) is always mounted in every edition — a fix there ships everywhere automatically.

**IN scope:** compose `profiles:` tags, the `EDITION` flag + router gating in memory-api, making Neo4j genuinely opt-in, and proving a profile flip cannot change data identity.

**OUT of scope:**
- **Any license / entitlement / paid-tier work.** See D-15-01.
- The web group-chat frontend (Phase 16).
- Local email/password auth (Phase 18).
- Billing, multi-tenant provisioning, trial caps (deliberately deferred — design doc "Timing: Monetization Deferred").

</domain>

<decisions>
## Implementation Decisions

### D-15-01 — NO license, NO entitlements, NO `pro` tier. LOCKED.

Locked decision **Q6** dropped the Ed25519 license and the paid self-host tier entirely, and with it requirement **EDIT-03**. Nothing in the product is paywalled; the only closed surface is the hosted control plane (billing, multi-tenant provisioning, trial caps).

**This must be stated because the ROADMAP actively contradicted it until today.** Phase 15's success criteria SC#4 and SC#5 still demanded "a paying customer installs a valid Ed25519-signed license" and `require_entitlement()` checks. Both were rewritten on 2026-07-12. **Do not plan license, signing, entitlement, or `require_entitlement()` work.** If any source artifact still asks for it, it is stale — this decision wins.

### D-15-02 — `pro` is DELETED. Four profiles remain: *(unset)* / `integrations` / `saas` / `ops`.

**AMENDED 2026-07-12 after research (user-approved).** The first draft of this decision said "three profiles" and listed ~24 services. Research proved it wrong in three ways, each of which would have shipped a broken OSS-light install. The corrected table is authoritative:

| `COMPOSE_PROFILES` | Services |
|---|---|
| *(unset)* — OSS-light core, always runs | memory-api, postgres, qdrant, centrifugo, nginx, **minio**, mcp-brain, mcp-gateway, mcp-scraper, brain-janitor |
| `integrations` | neo4j, graphiti-service, langfuse (+ its clickhouse/redis deps), mcp-calendar, mcp-drive-read, mcp-deck, mcp-github, granola-sync, drive-sync, searxng, agent-runtime |
| `saas` | session-bridge, librechat, **librechat-mongo, librechat-meili, librechat-bridge**, openwebui, **openwebui-pipeline** |
| `ops` | **xbrain-backup** |

**`pro` stays deleted** — that is the actual user decision and it is unchanged. With EDIT-03 gone there is no paid tier for it to unlock, and Q5 already says Neo4j is *"open source, opt-in — the only reason it's not default is ~1 GB RAM. Not paywalled."* A profile named `pro` would advertise a commercial tier that does not exist. Neo4j / Graphiti / Langfuse live in `integrations`.

**The three corrections, and why each was forced:**

1. **`ops` is restored — it was never the user's to drop; the first draft simply lost it.** `EDIT-01` names four opt-in profiles verbatim: *"`integrations` / `pro` / `saas` / `ops` are opt-in"* (`.planning/REQUIREMENTS.md:21`). Only `pro` was dropped. `xbrain-backup` goes here. This also has a practical payoff: `xbrain-backup` is the **only** service in the entire compose file with no arm64 image (`google/cloud-sdk:slim` is amd64-only, plus a hardcoded x86_64 mongodb-tools `.deb` in its Dockerfile), so isolating it in `ops` keeps every *other* profile verifiable on this arm64 dev host.

2. **MinIO is PROMOTED into the core and renamed.** There is exactly one MinIO in the compose file and it is called `langfuse-minio` (line 583) — but `memory-api` points its media storage at it: `MINIO_ENDPOINT: ${MINIO_ENDPOINT:-langfuse-minio:9000}` (line 190). `media` is a **promised always-on core capability**. Tagging that container `integrations` (the natural reading, since Langfuse brought it) would make `/v1/media/upload` return 503 in *every* OSS-light install. So it becomes an untagged core service named `minio`, and Langfuse — when `integrations` is on — reuses that same instance. Do NOT stand up a second MinIO: that is ~256 MB of duplicated RAM against the whole point of OSS-light.

3. **Five services the first draft never named** would have leaked into the core by omission. A service with **no** profile tag always runs, so *forgetting* a service is not a no-op — it silently enlarges the default install. `librechat-mongo`, `librechat-meili`, `librechat-bridge` and `openwebui-pipeline` are LibreChat/Open-WebUI dependencies and belong in `saas` (Q4 removes those frontends from the default install). Research proved the omission live: a faithful application of the first draft's table yielded **15** services in the bare-compose core, not ~10.

**The trap to internalise:** the OSS-light baseline is defined by the *absence* of a tag. Every service must be explicitly accounted for. An untagged service is a shipped service.

### D-15-03 — Neo4j must stop being a hard boot dependency. TWO edges, not one. The work is compose-only.

**AMENDED 2026-07-12 after research.** The first draft named one blocker and assumed the app code would need fixing. Both were wrong.

**There are TWO hard `depends_on: neo4j: {condition: service_healthy}` edges**, and both are on services that sit in the *untagged core*:

- `memory-api` — `infrastructure/docker-compose.yml`
- **`brain-janitor`** — `infrastructure/docker-compose.yml:1131` (missed by the first draft)

Docker Compose's rule here is unforgiving: **a service with no profile may not `depends_on` a service that has one.** Research proved the failure live — applying the profile table without removing these edges makes `docker compose config` abort outright:

```
service "brain-janitor" depends on undefined service "neo4j": invalid compose project
```

So this is not a nice-to-have cleanup. It is a **Wave-0 prerequisite**: until both edges are gone, the compose file does not even parse with profiles applied, and no other task in the phase can be verified.

**The application code is ALREADY correct — do not "fix" it.** Research booted memory-api live with no Neo4j container reachable: `/v1/healthz` returned **200**, exactly one `neo4j.connectivity_failed` ERROR log line, no crash, no log spam. The only cost was a ~8.5s DNS-timeout delay at startup, which is worth a look but is not a defect. Three independent graceful-degrade implementations already exist in this codebase and are a good precedent to copy for router gating.

**Therefore D-15-03's real work is removing two compose edges** — plus deciding whether that 8.5s DNS timeout on a Neo4j-less boot should be shortened. Do not plan application-code changes to the Neo4j path.

### D-15-04 — A profile flip must never change what a service believes about its data.

This is the lesson of the bug fixed in commit `215882b`, and it is now success criterion SC#5.

`brain-janitor` was passed `QDRANT_COLLECTION=memory_items` — the name of the **Postgres table**, not the **Qdrant collection** (`messages`) that memory-api actually writes. It therefore purged a collection that did not exist; `qdrant_purger.py` swallowed the error and the run reported success. The vector hard-delete had been a **silent no-op since Phase 11**.

The generalisation, and the trap this phase must not fall into: **turning a profile on or off may change which containers run and which routers mount — it must never change which collection, which team_scope, or which schema a running service resolves.** Any var that two services must agree on has to resolve identically in every edition. Plans that introduce per-profile config forks are wrong by construction.

### D-15-05 — One image, no rebuild.

The identical `memory-api` image serves every edition. `EDITION=oss|saas` is an env flip at boot; it mounts or omits routers. No per-edition image build, no conditional import at module scope that would break the other edition's import graph.

Router gating must be **additive and explicit**: name the always-on core routers, name the saas-only routers. A router that is forgotten defaults to *mounted*, which would leak a SaaS surface into an OSS install — so the plan must state the default and test the negative case (an OSS boot must NOT expose the saas-only routes).

### Claude's Discretion

- Exact mechanism of router gating (registry dict, list-of-tuples, decorator) — pick what matches the existing `apps/memory-api/app/main.py` `include_router` style.
- Whether `EDITION` is a plain `str` or a `Literal`/enum on `Settings` — but it MUST be validated (an unknown value should fail fast at boot, consistent with the OAuth validator this codebase established in Phase 14).
- How Langfuse's own bundled dependencies (clickhouse, redis) get tagged — they should follow langfuse into `integrations`.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### The locked decisions (authoritative — supersede anything older)
- `.planning/features/open-core-edition-design.md` — Q1–Q7 locked decisions, the original profile table (§1) and `EDITION` sketch (§2). **Its §1 profile table still shows a `pro` profile — that is superseded by D-15-02 above.**

### The infrastructure this phase edits
- `infrastructure/docker-compose.yml` — every service. Currently has **zero** `profiles:` tags. Note Phase 14 just rewrote large parts of it (neutral fallbacks, nginx templates, `AGENT_MENTION_ALIASES`, `QDRANT_COLLECTION`) — read it fresh, do not work from memory.
- `apps/memory-api/app/main.py` — `include_router` calls (the gating surface) and the lifespan that calls `init_driver` (Neo4j).
- `apps/memory-api/app/config.py` — `Settings`. Phase 14 added the fail-fast `field_validator` pattern here; follow it for `EDITION`.
- `apps/memory-api/app/neo4j_client.py` — what happens when Neo4j is absent.

### Phase 14 (just shipped — its config surface is what profiles/flags read)
- `.planning/phases/14-portability-foundation/14-VERIFICATION.md` — verified state, plus the two WARNING findings (`QDRANT_COLLECTION`, `SMTP_*` undelivered vars).
- `.planning/phases/14-portability-foundation/14-REVIEW.md` — the two critical defects and why the phase's own gate could not see them.

</canonical_refs>

<specifics>
## Specific Ideas

**The gate lesson from Phase 14 — apply it here.** Three defects in Phase 14 were invisible to its own acceptance gate, all for the same reason: *the check never traversed the real deployment path.* The gate tested `Settings()` directly, so it never noticed that `docker-compose.yml` failed to pass `AGENT_MENTION_ALIASES` to memory-api at all.

Phase 15 is **entirely about docker-compose and boot-time wiring**, so a gate that does not actually bring containers up proves almost nothing. Verification for this phase should use `docker compose config --profiles`, `docker compose --profile X config --services` and real `docker compose up` runs — not greps of the YAML.

**Docker is available locally** (Docker Desktop, daemon up). Note the host is **ARM64** (`linux/aarch64`) while prod is amd64: pulling and running upstream multi-arch images is fine, but **do not build images locally** — an arm64 build is useless for the prod VM. If a service in a profile has no arm64 image, say so rather than working around it.

</specifics>

<deferred>
## Deferred Ideas

- **Local embeddings by default (Q3)** — still has no requirement and no phase. Flagged in `.planning/REQUIREMENTS.md` under "Still unmapped". It contradicts the "one key — Anthropic OR OpenAI OR Grok" promise, since a self-hoster currently needs an embeddings key for the brain to work at all. Not Phase 15's job, but it is the next unmapped gap.
- Billing / multi-tenant provisioning / trial caps — deliberately deferred by the design doc.
- `SMTP_*` vars documented in `.env.example` but never passed to memory-api by compose (pre-existing, fail-soft — Phase 14 verifier WARNING). Cheap to fold in here since this phase is already editing compose, but it is not an EDIT-01/EDIT-02 requirement.

</deferred>

---

*Phase: 15-edition-mechanics*
*Context gathered: 2026-07-12*
