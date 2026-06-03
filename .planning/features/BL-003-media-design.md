# BL-003 — Media & documents: design

**Goal:** images + documents sent via LibreChat / extension / clipper are stored as real blobs
(not local paths), rendered inline (images) / as clickable files (docs) in LibreChat, the
extension @claude chat, and the Brain Monitor; the extension can upload media directly.

## Architecture

**Storage — MinIO (already deployed).** Bucket `xbrain-media`, object key `media/{team_scope}/{uuid}{ext}`.
- ⚠️ Config reconciliation needed: `get_minio_client()` reads `MINIO_URL/ACCESS_KEY/SECRET_KEY`
  (currently EMPTY) while the deck path uses `MINIO_ENDPOINT/ROOT_USER/ROOT_PASSWORD` (SET).
  Slice 1 maps them: `MINIO_URL=http://${MINIO_ENDPOINT}`, `MINIO_ACCESS_KEY=${MINIO_ROOT_USER}`,
  `MINIO_SECRET_KEY=${MINIO_ROOT_PASSWORD}`, `MINIO_BUCKET=${MINIO_MEDIA_BUCKET:-xbrain-media}`.

**Data model — no schema change.** A media item is a normal `memory_item` whose `metadata.media`
carries `{key, mime, size, filename, width?, height?}`. `content` = caption/filename. The 7-field
tagging contract still applies (team_scope, truth_level, source="upload:<surface>", etc.).

**Serving — the key decision (affects render slices):**
MinIO is internal-only (not exposed via nginx), so a presigned URL would point at
`langfuse-minio:9000` — unreachable from the browser. Two options for `<img src>`:
- (A) Expose MinIO publicly (nginx vhost) + presigned URLs. Simple but opens MinIO read.
- (B) **memory-api proxy + short-lived signed token in the query**: `GET /v1/media/{id}/raw?t=<sig>`
  — `<img>` can't send a Bearer header, so a signed token in the URL authorizes the specific item;
  memory-api validates + streams the object. No public MinIO. **Recommended (OSS/self-host, scoped).**
- Slice 1 ships the Bearer-authed proxy (`GET /v1/media/{id}/raw`) for authed fetch; the signed-token
  variant for raw `<img src>` is decided + added when the first render surface needs it (slice 2).

## Endpoints (memory-api)
- `POST /v1/media/upload` (multipart) → put to MinIO + create media memory_item → `{item_id, key, mime, raw_path}`.
- `GET /v1/media/{item_id}/raw` → team-scoped auth → stream the object (Content-Type = mime).
- (later) `GET /v1/media/{item_id}/raw?t=<signed>` → token-authed variant for `<img src>`.

## Slices
1. ✅ **Foundation (SHIPPED, 260603-3et):** config reconciliation + `POST /upload` + `GET /raw` (Bearer) + media memory_item + tests. Verified live (PNG upload→201, /raw→200 image/png).
2. ✅ **Brain Monitor render (SHIPPED, 260603-40g):** images as thumbnails, docs as clickable file chips. **Serving decision: Option B** — memory-api proxy + short-lived HS256 signed token in the query (no public MinIO). New `GET /v1/media/{id}/img?t=<token>` (no Bearer, token-gated); `_enrich_event` strips the raw MinIO key and emits `{mime,size,filename,url}`. Frontend `mediaCellHtml()` deployed to Firebase. Verified live through nginx: valid token→200 image/png, tampered→403, missing→422. v_brain_events gained a `media` column (alembic 0021). **Deploy note:** migration 0020 had to be shipped alongside 0021 — surgical deploys must include every new migration in the chain or alembic crash-loops on a missing down_revision.
3. ✅ **Extension upload + UI reorg (SHIPPED, 260603):** 📎 repurposed → "Attach file" (direct upload to `/v1/media/upload`, multipart, Bearer xbt_token + X-Team-Scope); clipper launch moved to the header as a text button "add to memory". `chrome-extension.zip` refreshed (user reloads the extension).
4. ✅ **Extension @claude render (SHIPPED, 260603):** team-chat messages carry a structured `metadata.media` (no migration — `team_messages.metadata_` already existed); `_serialize_message` mints a fresh signed `url` per read (same Slice-2 token pattern). Extension `renderMediaInto()` shows image thumbnails (`<img src>` = server-minted `/v1/media/{id}/img?t=`) + doc chips. Verified live (memory-api healthy, `PostMessageBody.media` loads).
5. ✅ **LibreChat render (SHIPPED, 260603):** **Build complet** — recall surfaces media through the assistant's reply. `rag_enrichment._format_addendum` emits a markdown image (`![](…)`) for image hits / link for docs, pointing at a STABLE same-origin URL `/api/xbrain/media/{id}` + a one-line instruction nudging the model to echo it. New LibreChat proxy route `GET /api/xbrain/media/:itemId` (`xbrain-routes.js`, `requireJwtAuth`) resolves the caller's team_scope + bridge-JWT-auths to memory-api `/v1/media/{id}/raw` → solves CSP (same origin) + token expiry (mints per request). Verified live: Node bridge JWT accepted (200), `/raw` returns image bytes (200 PNG), wrong-scope→404 (team isolation). **Two bugs caught in review + fixed:** (a) `_human_size` double-divided → "0.0 MB" for 1 MB; (b) the proxy resolved scope by `user.email` only, diverging from the bridge's `googleId||email||id` derivation (`mongo_watcher.py:86`) → would 404 media for Google-signed-in users. **Browser-level end-to-end (recall→assistant render) is the user's final check.**

## Open decisions — RESOLVED
- Serving: **Option B** (signed-token proxy) for Brain Monitor/extension; **same-origin LibreChat proxy** for LibreChat render. · Docs: clickable link/download (no preview). · Upload cap: 25 MB, broad mime accept. · Media items are NOT relevance-filtered (explicit uploads always kept).

## Cross-service invariant (learned)
Any service that resolves a LibreChat user's `team_scope` MUST derive the sub the same way the bridge does — `googleId || email || str(user_id)` (`apps/librechat-bridge/app/mongo_watcher.py:86`). Resolving by email alone diverges for Google users and for phantom `*@bridged.local` duplicate rows whose `source_user_id` is the bare email.
