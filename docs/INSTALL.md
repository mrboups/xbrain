# Install xbrain (OSS-light) — zero external keys

**Audience:** an operator standing up the OSS-light edition on a fresh host.
**Promise:** you can go from a bare Ubuntu VM to a registered account with a
working team brain **using this document alone, reading no source code, and
providing zero external integration keys** — no OpenAI, no Google OAuth client,
no GitHub App.

This is the self-hoster's happy path. Everything below runs from one
`docker compose` project against a single `.env` you generate locally in one
command.

---

## 1. What you get with zero external keys

A fresh OSS-light boot (the 10-service core, `COMPOSE_PROFILES` unset) gives you
the whole memory plane with **no external keys at all**:

- **Register + a team.** `POST /v1/auth/local/register` creates an email/password
  account and a private solo team (`My Workspace`) and returns an `xbt_` token —
  no Google, no GitHub, no SMTP.
- **Document upload + analysis.** `POST /v1/media/upload` stores a file in MinIO
  and creates a tagged `media` memory item.
- **Keyless local embeddings + semantic retrieval.** Embeddings run **in-process
  inside `memory-api`** (`EMBEDDINGS_PROVIDER=local`, `BAAI/bge-small-en-v1.5`,
  baked into the image at build). Ingest with `POST /v1/memory/upsert`, retrieve
  with `GET /v1/memory/search` — no embeddings key, no network call at runtime.
- **The tagging contract + truth levels.** Every record carries `team_scope`,
  `project_scope`, `visibility`, `confidence`, `truth_level`
  (`EPHEMERAL → WORKING → VALIDATED → CANONICAL → PUBLIC`), `source`, and
  `validation_status`, enforced at the API boundary.
- **The ChatGPT-web / Claude.ai connector**, signed in through **local auth**
  (see §8), plus **API-level clip-to-memory** — a `curl` `POST /v1/memory/upsert`
  under your `xbt_` token lands clipped page content in memory, keyless.

### The one optional key (D-16-01)

A **single LLM key — Anthropic OR OpenAI OR Grok — is OPTIONAL**. It is **not**
needed for anything in the list above. Adding one key only enables:

- **LLM-based extraction** (Haiku relevance filter, entity/fact extraction), and
- **the in-chat `@agent` reply.**

Doc ingest, local embeddings, and semantic retrieval stay **keyless** whether or
not you set an LLM key. Leave all three LLM keys blank for the pure zero-key core.

> The polished **in-extension zero-key sign-in UI is Phase 20.** Today the
> clip-to-memory flow is proven **at the API level** (a `curl` call under a local
> `xbt_` token) — the backend clip path already works with zero external keys.

---

## 2. Prerequisites

- **A host:** Ubuntu 24.04 LTS is the reference target, but any Docker host works.
- **Docker Engine + Docker Compose v2** (`docker compose version` ≥ v2).
- **~4 GB RAM** free for the OSS-light core (10 services). Opt-in profiles need more
  (see §10).
- **`openssl`** on PATH (used to mint secrets — pre-installed on Ubuntu).
- **One inbound port: 80** (nginx). TLS terminates **externally** — see §9.

You do **not** need a Google OAuth client, a GitHub App, an OpenAI key, or an
SMTP server for the OSS-light core.

---

## 3. Provision + clone

Provision your host, then:

```bash
git clone https://github.com/mrboups/xbrain.git
cd xbrain
```

Everything below is run from the repo root.

---

## 4. Generate secrets (zero external keys)

The fastest path mints a complete, bootable `.env` for you — random secrets, no
key to paste:

```bash
make oss-init          # writes ./.env (refuses to clobber an existing one)
# make oss-init ARGS=--force   # overwrite an existing ./.env
```

`make oss-init` writes only the **[REQUIRED — core boot]** set from
`.env.example`, with every secret drawn from a CSPRNG (`openssl rand` / Fernet).
It sets `EDITION=oss`, leaves `COMPOSE_PROFILES` empty (the 10-service core), and
emits **no** OpenAI / Google / GitHub / Anthropic key. The keyless
doc-ingest → local-embeddings → semantic-retrieval path works off this file alone.

### Manual alternative (edit `.env.example` by hand)

If you prefer to fill secrets yourself, copy the template and set every
`[required]` var in the **[REQUIRED — core boot]** section:

```bash
cp .env.example .env
# then generate each secret with, e.g.:
openssl rand -hex 32     # 64-hex secrets (BRIDGE_SHARED_SECRET, CENTRIFUGO_*)
openssl rand -hex 24     # POSTGRES_PASSWORD (keep it in sync inside DATABASE_URL)
openssl rand -hex 16     # MINIO_ROOT_PASSWORD (>= 8 chars — see below)
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # OAUTH_CREDENTIALS_ENCRYPTION_KEY
```

> **Never ship the literal `__FILL__` / `__FILL_RANDOM_*__` placeholders.** They
> are not secrets — a deployment that keeps them is trivially compromised. Rotate
> every one.
>
> **`MINIO_ROOT_PASSWORD` must be ≥ 8 characters.** The core `minio` container
> refuses to start below that floor and will crash-loop; `memory-api`'s media
> uploads then 503. `make oss-init` already generates a 32-hex value.

Verify nothing is missing before booting:

```bash
make env-check         # confirms every required core var is set (zero-key passes)
```

---

## 5. Boot the core (OSS-light deploy)

The OSS-light deploy is a **direct `docker compose ... up -d --build` on the
target host** — zero external keys, no SSH, no rsync. This first boot builds the
local images (`memory-api`, `mcp-gateway`, `mcp-scraper`, `mcp-brain`,
`brain-janitor`) and pulls the rest:

```bash
docker compose -f infrastructure/docker-compose.yml --env-file .env up -d --build
```

For subsequent starts (images already built) the Make shortcuts are equivalent:

```bash
make build             # (re)build the local images
make up                # docker compose up -d
make logs              # tail all logs
```

This starts the **10-service core**: `nginx`, `postgres`, `qdrant`, `memory-api`,
`minio`, `centrifugo`, `mcp-brain`, `mcp-gateway`, `mcp-scraper`, `brain-janitor`.

> **Expected, not a failure:** on a core boot with no Neo4j container, `memory-api`
> takes an extra **~8.5 s** at startup — `NEO4J_URI` is set but the host is absent,
> so the driver DNS-times-out once before the app degrades cleanly (Neo4j is opt-in;
> Phase 15). Do not "fix" this by re-adding a Neo4j dependency. The `memory-api`
> healthcheck's 30 s `start_period` already absorbs it.

> `make deploy` is a **different, remote path** — the SaaS/hosted-team route that
> rsyncs to a separate `VM_HOST` over SSH and builds on that VM, gated by
> `env-check` + `preflight`. Use it only when deploying to a remote VM, **not** for
> this single-host OSS-light install. See §11.

---

## 6. Verify

**Primary check — container health.** The compose healthchecks are the source of
truth (each core service defines one; `memory-api`'s runs
`curl http://localhost:8000/v1/healthz` *inside* its container):

```bash
make ps                # or: docker compose -f infrastructure/docker-compose.yml ps
```

Wait until every core service reports `healthy`.

**From the host — reach the API through nginx.** Only `nginx` is published to the
host (port 80). `memory-api` listens on port 8000 **inside its container** (the
address in its healthcheck and in the default `OAUTH_ISSUER_URL`); it is **not**
bound to a host port. Reach it through the nginx `api.<XBRAIN_BASE_DOMAIN>` vhost —
with the default `XBRAIN_BASE_DOMAIN=localhost` that is `api.localhost`:

```bash
curl -fsS http://api.localhost/v1/healthz
```

On Ubuntu 24.04 `*.localhost` resolves to loopback automatically. If your resolver
does not, use a DNS-free form that sends the vhost via the `Host` header:

```bash
curl -fsS -H 'Host: api.localhost' http://localhost/v1/healthz
# or: curl -fsS --resolve api.localhost:80:127.0.0.1 http://api.localhost/v1/healthz
```

**Automated proof.** The phase gate boots the real core and drives this flow over
HTTP:

```bash
bash infrastructure/scripts/verify-phase16.sh
```

---

## 7. First-run: register

Create the first account (password **≥ 10 characters**):

```bash
curl -X POST http://api.localhost/v1/auth/local/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"a-strong-passphrase"}'
```

The response contains an `xbt_` token and a solo `team_scope` (a private
`My Workspace`). Use the token as `Authorization: Bearer <xbt_...>` for every
subsequent API call. Sign in again later with the same body against
`POST /v1/auth/local/login`.

There is **no email-based password reset** in the OSS-light default (it would
require SMTP). Lockout and forgotten-password recovery are operator actions
against the database — see [`local-auth-recovery.md`](./local-auth-recovery.md).

---

## 8. Connect the ChatGPT-web / Claude.ai connector (zero-key)

The brain is reachable as an OAuth 2.1 remote connector. Point the connector at
your deployment's discovery document:

```
https://<your-api-host>/.well-known/oauth-authorization-server
```

On a zero-key install (no GitHub App configured), the connector's sign-in leg now
presents a **local email/password form** (Phase 16, D-16-02) instead of forcing a
GitHub redirect. Consent is still bound to one authenticated user and one
`team_scope`. If a GitHub App *is* configured, the GitHub sign-in path keeps
working — the local-auth branch is additive.

> The connector must reach your `OAUTH_ISSUER_URL` over the public network. Set it
> to your real domain for a real deploy (see §9); the `localhost` default is for
> single-host local testing only.

---

## 9. Real-deploy notes

Before exposing the deployment on a real domain:

- **Domain / OAuth identity.** Set `XBRAIN_BASE_DOMAIN`, `OAUTH_ISSUER_URL`, and
  `OAUTH_RESOURCE_URL` to your real domain. These have no safe default —
  `memory-api` and `mcp-brain` fail fast at boot if `OAUTH_ISSUER_URL` /
  `OAUTH_RESOURCE_URL` are empty, by design.
- **CORS.** Anchor `CORS_ALLOWED_ORIGIN_REGEX` to *your* origins. **Never set it to
  `.*`** — `memory-api` rejects a wildcard at boot, because it would let any site
  make credentialed calls.
- **TLS terminates externally.** In this compose file `nginx` listens on **:80
  only** — it never binds :443. Terminate TLS in front of it (Cloudflare, Caddy, or
  your own reverse proxy) and loop back to nginx on :80. This is deliberate; do not
  try to add certificates inside the compose stack.

---

## 10. Opt-in profiles

The 10-service core is everything a self-hoster needs. **Four** profiles add more, at
a RAM cost — set them in `.env` via `COMPOSE_PROFILES`:

- `COMPOSE_PROFILES=integrations` — Neo4j + Graphiti + Langfuse (with its ClickHouse
  and Redis) + SearXNG + Drive/Granola sync + `agent-runtime` and the
  `drive-read` / `calendar` / `deck` / `github` MCP sidecars (~+4 GB RAM).
- `COMPOSE_PROFILES=saas` — the bundled LibreChat / Open WebUI / session-bridge
  frontends and their Mongo + MeiliSearch. **Also set `EDITION=saas`** (session-bridge
  needs the saas-only routes; `EDITION=oss` would 404 them).
- `COMPOSE_PROFILES=board` — the collaborative Excalidraw board (`xbrain-board` +
  `xbrain-hocuspocus`). Adds roughly 320 MB of RAM on top of the core (64 MB for the
  static SPA + 256 MB for the Yjs WebSocket server) and one new vhost,
  `board.<your-domain>`. Point that subdomain at the same host as the API.
- `COMPOSE_PROFILES=ops` — the `xbrain-backup` container, and nothing else (~256 MB).
  A cron at **02:00 UTC** dumps PostgreSQL, snapshots every Qdrant collection, tars
  the LibreChat/Open WebUI volumes, dumps LibreChat's Mongo **if the `saas` profile
  is on** (it skips cleanly when it is not), uploads the lot to
  `gs://$GCS_BACKUP_BUCKET/<date>/` and deletes anything older than
  `BACKUP_RETENTION_DAILY` (default 7) days. **It is Google Cloud Storage only** —
  it shells out to `gsutil` and authenticates through a VM-attached service account,
  so on any other host you need your own backup path. **Turn it on for any install
  you would be upset to lose:** the core boots perfectly well without it and backs up
  nothing at all.

Combine them comma-separated, e.g. `COMPOSE_PROFILES=integrations,ops`.

### Board knobs (`board` profile)

The board is reached from the Chrome extension's `board` header button, which mints a
short-lived token and opens the board URL. Four optional `.env` knobs tune it, all with
safe defaults:

- `BOARD_PUBLIC_BASE_URL` — public base URL of the board SPA (used to build the open-board link).
- `BOARD_WS_URL_PUBLIC` — public Yjs WebSocket URL (`ws(s)://board.<domain>/collab`).
- `BOARD_TOKEN_TTL_S` — board access-token lifetime in seconds (default `3600`).
- `BOARD_MAX_DOC_BYTES` — hard cap on a stored board document (default 16 MB).

---

## 11. `make deploy` — the SaaS/hosted-team remote path

`make deploy` is **not** the OSS-light path. It is the hosted-team remote route:
it rsyncs the repo to a separate `VM_HOST` over SSH and runs
`docker compose build && up` **on that VM**, gated by `make env-check` +
`make preflight`. It requires `VM_HOST` + an SSH key configured.

For a single-host OSS-light install you do **not** need it — the direct
`docker compose ... --env-file .env up -d --build` from §5 is the whole story.

---

*OSS-light install guide — Phase 16 (PKG-01). Zero-external-key core.*
