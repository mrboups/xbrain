# 260603-3et — Media foundation: MinIO upload + raw-serve endpoints (BL-003 slice 1)

## Files changed

| File | Change |
|------|--------|
| `apps/memory-api/app/config.py` | `MINIO_BUCKET` default changed from `"xbrain"` → `"xbrain-media"` |
| `infrastructure/docker-compose.yml` | Added 4 new env vars to memory-api block (see MinIO config mapping below) |
| `apps/memory-api/app/routes/media_helpers.py` | **NEW** — pure helpers: `_MAX_UPLOAD_BYTES`, `derive_key_and_mime()` |
| `apps/memory-api/app/routes/media.py` | **NEW** — FastAPI router: `POST /media/upload` + `GET /media/{item_id}/raw` |
| `apps/memory-api/app/main.py` | Wired `media.router` with `prefix="/v1", tags=["media"]` |
| `apps/memory-api/tests/test_media.py` | **NEW** — 10 unit tests (all pass) + 5 integration stubs |

## Endpoint paths and payloads

### POST /v1/media/upload
- **Method:** POST, multipart/form-data
- **Auth:** Bearer `<token>` + `X-Team-Scope: <slug>`
- **Form fields:**
  - `file` (required) — binary blob, ≤ 25 MB
  - `caption` (optional, str) — used as `content` in the memory_item
  - `project_scope` (optional, str)
  - `truth_level` (optional, str, default `"WORKING"`)
  - `source_surface` (optional, str, default `"extension"`)
- **Success 201:**
  ```json
  {
    "item_id": "<uuid>",
    "key": "media/<team_scope>/<uuid><ext>",
    "mime": "image/png",
    "size": 1234,
    "raw_path": "/v1/media/<uuid>/raw"
  }
  ```
- **Error responses:** 503 (MinIO not configured / bucket/put failed), 413 (file > 25 MB)

### GET /v1/media/{item_id}/raw
- **Auth:** Bearer `<token>` + `X-Team-Scope: <slug>`
- **Success 200:** raw bytes with `Content-Type: <mime>` and `Content-Disposition: inline; filename="<filename>"`
- **Error responses:** 404 (item not found in team, or no `metadata.media`), 503 (MinIO not configured / storage error)

## MinIO config mapping (docker-compose.yml — memory-api environment block)

```yaml
MINIO_URL: ${MINIO_URL:-http://${MINIO_ENDPOINT:-langfuse-minio:9000}}
MINIO_ACCESS_KEY: ${MINIO_ACCESS_KEY:-${MINIO_ROOT_USER:-minio}}
MINIO_SECRET_KEY: ${MINIO_SECRET_KEY:-${MINIO_ROOT_PASSWORD}}
MINIO_BUCKET: ${MINIO_MEDIA_BUCKET:-xbrain-media}
```

Maps `MINIO_ENDPOINT/ROOT_USER/ROOT_PASSWORD` (already set in `.env`) into the vars
that `get_minio_client()` reads (`MINIO_URL/ACCESS_KEY/SECRET_KEY`). No new secrets
needed — same MinIO instance serves decks and media.

## Verification status

- **py_compile:** PASS — all 5 changed/new `.py` files compile clean
- **Unit tests:** 10/10 PASS (`pytest -m "not integration"`)
- **Integration tests:** 5 stubs declared with `@pytest.mark.integration` + `@pytest.mark.asyncio` — skipped locally (no Docker). Require live MinIO + PostgreSQL + Qdrant on the VM.

## Deviations from spec

1. **`media_helpers.py` added** — pure helpers (`derive_key_and_mime`, `_MAX_UPLOAD_BYTES`) split into a separate module so unit tests can import them without triggering FastAPI route registration (which requires `python-multipart`, not installed locally). The router still imports from `media_helpers.py`; no functional change.
2. **`botocore` imports are lazy** — `from botocore.exceptions import ClientError` placed inside the functions that use it (`_ensure_bucket`, `serve_media_raw`) to avoid a top-level `ModuleNotFoundError` in the local dev env where `boto3`/`botocore` are not installed. Same pattern as `app/db/minio.py` (`import boto3` inside the try block).
3. **Signed-token `<img src>` variant** — deferred to slice 2 per spec. The current `GET /raw` endpoint requires a Bearer header (not usable as a bare `<img src>`).
