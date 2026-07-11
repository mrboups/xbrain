# Open-Core Edition Split — Design Blueprint

**Status:** Design / not yet executed. Turn into GSD phases when ready to build.
**Goal:** One codebase, two+ editions (OSS self-host / SaaS hosted / paid self-host "pro"),
so a single update flows to all editions automatically. No fork, ever.

## Golden rule

One repo, one `main`. Edition = a **deployment-time selection** (compose profile + config
flag + license), never a branch. You make each change **once**; the mechanism delivers it to
every edition. If you ever apply the same change twice, the architecture has been violated.

Current state (measured 2026-07-11):
- Compose profiles: **none yet** (0) → to add.
- Edition/feature-flag concept in memory-api config: **none yet** → to add.
- `.env.example`: **115 vars** → ~90% already externalized; finish de-hardcoding
  (28× `grooveos.app`, 15× `aibrussels`, 15× `default` team_scope) — PREREQUISITE.

---

## 1. Compose `profiles:` — the edition selector

A service with **no profile tag always runs** (= the OSS-light baseline). Tagged services
only run when their profile is active (`COMPOSE_PROFILES=integrations,pro`).

| Service | Profile tag | Runs in |
|---|---|---|
| memory-api | *(none)* | all |
| postgres | *(none)* | all |
| qdrant | *(none)* | all |
| centrifugo | *(none)* | all |
| nginx | *(none)* | all |
| minio (media/docs) | *(none)* | all |
| mcp-brain (ChatGPT/Claude-web connector) | *(none)* | all — headline OSS feature |
| mcp-gateway | *(none)* | all |
| mcp-scraper (clip URL fetch + agent browsing) | *(none)* | all — clip is a core goal |
| brain-janitor (soft-delete cron) | *(none)* | all |
| mcp-calendar | `integrations` | opt-in |
| mcp-drive-read + drive-sync | `integrations` | opt-in |
| mcp-deck | `integrations` | opt-in |
| mcp-github | `integrations` | opt-in |
| granola-sync | `integrations` | opt-in |
| searxng (web search) | `integrations` | opt-in |
| agent-runtime (multi-model 2nd opinion) | `integrations` | opt-in |
| neo4j | `pro` | licensed |
| graphiti-service | `pro` | licensed |
| langfuse (+ worker, clickhouse, redis, minio) | `pro` | licensed (observability) |
| xbrain-backup | `ops` | opt-in ops |
| session-bridge (Pro/Max routing) | `saas` | hosted only |
| librechat (+ mongo, meili) + librechat-bridge | `saas` | hosted only |
| openwebui (+ pipeline) | `saas` | hosted only |

- **OSS light** = untagged only (~10 services): chat + full brain + truth-levels + ChatGPT
  connector + clip.
- **Team activates** `integrations` for calendar/drive/github/deck/search.
- **`pro`** (graph + observability) = licensed. Natural moat line — already decoupled.
- **`saas`** = your hosted-only bits (bridge + the hidden frontends).

---

## 2. `EDITION` flag in memory-api (route/behavior gating)

Compose profiles pick *which services* run. The flag picks *which routes/behaviors* inside
the memory-api monolith. Both driven by env — no code branches.

```python
# config.py
EDITION: str = "oss"                 # oss | saas | pro   (env: XBRAIN_EDITION)
LICENSE_KEY: str = ""                # signed key (self-host pro)
ENABLE_TRIAL_CAPS: bool = False      # SaaS trial/quota (Grok cap)
ENABLE_MULTI_TENANT: bool = False    # SaaS
# entitlements resolved from LICENSE_KEY at boot -> set[str]
```

Router mounting (main.py):
```python
# ALWAYS (core / OSS): brain, chat, teams, memory, promotions (truth-levels),
#                      media, health, me, auth (google), oauth_* (ChatGPT connector)
if EDITION in ("saas", "pro"):
    # waitlist, multi-tenant admin, external_sessions/bridge routing, billing
if ENABLE_TRIAL_CAPS:
    # trial/quota enforcement (Grok $/message cap)
# pro features guarded by require_entitlement("graph" | "observability" | ...)
```

Key point: **brain / chat / retrieval / truth-levels are ALWAYS mounted** → a fix there
fixes every edition instantly. The connector (`oauth_*` + mcp-brain) is OSS, not gated.

---

## 3. License plan (to sell paid self-host "pro")

Offline-verifiable signed license — no phone-home.

- Vendor signs a payload with an **Ed25519 private key**:
  `{ customer, edition:"pro", entitlements:[graph, observability, seats:N], expires }`.
- App ships the **public key**; verifies the license signature at boot → yields the
  entitlement set. `require_entitlement()` gates pro features.
- No/invalid license → `edition=oss`, pro features locked.
- Expired → downgrade to oss (or read-only pro) — design choice.
- SaaS: you set `XBRAIN_EDITION=saas` on your infra directly (no license needed).

---

## 4. CI lockstep — the "updates land on both sides" engine

One pipeline per commit to `main`:
```
build images (1x) -> test BOTH profiles (oss subset AND full) ->
   |-> publish OSS release: tagged images + light compose + install docs
   |-> deploy SaaS (full profile) to your infra
```
Same commit SHA -> both editions in lockstep by construction. You never push twice.
Migrations must be **forward-only + edition-agnostic** so every edition upgrades cleanly.

---

## Execution sequence (GSD phases)

| Phase | Deliverable | Depends on |
|---|---|---|
| **A. Portability foundation** | De-hardcode (28 grooveos / 15 aibrussels / 15 default) → config; slim fillable OSS `.env.example` | — (enabler) |
| **B. Edition mechanics** | `profiles:` on all services + `EDITION` flag + router gating + entitlement layer + Ed25519 license verify | A |
| **C. OSS light packaging** | Light compose + install docs + clean-install test on a fresh VM + **extract standalone web chat UI** (the one new build, shared with the PWA) | A, B |
| **D. CI lockstep** | One pipeline builds/tests both profiles, publishes OSS images + deploys SaaS | B, C |

Separate tracks (not part of the split): **Email feature** (send + Gmail read/search/ingest —
currently absent), and the **Grok API-key fallback + message cap** (SaaS trial).

## Anti-patterns (do NOT)
- Fork into two repos / long-lived per-edition branches.
- Copy-paste shared logic between editions.
- Hardcode infra values (blocks edition-by-config — Phase A removes this).
