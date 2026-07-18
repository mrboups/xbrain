---
phase: 19-local-embeddings
plan: 02
subsystem: infra
tags: [docker, fastembed, onnxruntime, embeddings, offline, hf-hub-offline, uvicorn, docker-compose, e2-medium]

# Dependency graph
requires:
  - phase: 19-local-embeddings (plan 01)
    provides: fastembed dependency, settings.EMBEDDING_CACHE_DIR (/app/model_cache), local_embedder + get_embedder() provider selector, provider-derived Qdrant dimension
provides:
  - memory-api image bakes BAAI/bge-small-en-v1.5 into /app/model_cache at build time (network-available), HF_HUB_OFFLINE=1 at runtime
  - proven zero-network embedding — `docker run --network none` produces a 384-dim vector (OFFLINE_384_OK)
  - both-arch image build proven — linux/arm64 (native) + linux/amd64 (buildx cross-build) resolve onnxruntime cp312 wheels and bake the model
  - configurable uvicorn worker count (ENV UVICORN_WORKERS, default 2) so the in-process local model is not duplicated in RAM
  - compose + both .env.example templates expose EMBEDDINGS_PROVIDER / LOCAL_EMBEDDING_MODEL / UVICORN_WORKERS with safe defaults; memory-api mem_limit 768m -> 896m for the single-worker model load
affects: [19-03, phase-16-install-packaging, deployment-vm-sizing]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Bake-then-offline: download HF model at docker build time into settings.EMBEDDING_CACHE_DIR, COPY --chown=xbrain:xbrain before USER, force HF_HUB_OFFLINE=1 at runtime"
    - "Env-configurable uvicorn worker count via shell-form CMD + exec (ENV UVICORN_WORKERS) so an in-process model isn't duplicated under --workers 2"

key-files:
  created:
    - .planning/phases/19-local-embeddings/19-02-SUMMARY.md
  modified:
    - apps/memory-api/Dockerfile
    - infrastructure/docker-compose.yml
    - .env.example
    - apps/memory-api/.env.example

key-decisions:
  - "mem_limit bumped 768m -> 896m: the ~256 MB (arm64) / ~366 MB (amd64-QEMU) baked-model RSS loads in-process under OSS-light's single worker on top of the already-768m-justified signin base; +128m gives that first lazy-load headroom without OOM churn, while staying a cap (not a reservation) so nothing is starved on the e2-medium box."
  - "Model bake runs with PYTHONPATH=/build/deps (fastembed installed there by the editable install) rather than a separate `pip install fastembed` in the builder — one dependency source, no duplicate install."
  - "CMD switched to shell form with exec so ${UVICORN_WORKERS} is substituted at runtime while uvicorn still becomes PID 1 and receives signals; default stays 2 so SaaS is unregressed."

patterns-established:
  - "Pattern: bake-then-offline model provisioning (build-time HF fetch -> baked layer -> HF_HUB_OFFLINE=1 runtime), proven authoritatively by docker run --network none"

requirements-completed: [EMBED-01]

# Metrics
duration: ~20 min
completed: 2026-07-18
---

# Phase 19 Plan 02: Bake Local Embedding Model + Offline/Both-Arch Proof Summary

**memory-api image now bakes BAAI/bge-small-en-v1.5 into /app/model_cache with HF_HUB_OFFLINE=1, proven to embed a 384-dim vector under `docker run --network none` (OFFLINE_384_OK) on both arm64 and amd64, with an env-configurable worker count and compose/.env wiring for keyless OSS-light.**

## Performance

- **Duration:** ~20 min
- **Started:** ~2026-07-18T03:15:00Z
- **Completed:** 2026-07-18T03:35:12Z
- **Tasks:** 3 (2 code, 1 verification-only)
- **Files modified:** 4 (+1 created: this SUMMARY)

## Accomplishments
- Dockerfile bakes the local embedding model at build time and forces cache-only at runtime — a zero-network install works with no HuggingFace fetch and no API key.
- Authoritative D-19-01 proof: the baked image produced a 384-dim vector with the network fully disabled (`--network none` -> `OFFLINE_384_OK`).
- Both-arch proof (D-19-04): native `docker build` (linux/arm64) and `docker buildx --platform linux/amd64` both succeed — onnxruntime cp312 wheels resolve and the model bakes on each arch.
- Worker count is env-configurable (`UVICORN_WORKERS`, default 2) so OSS-light runs 1 worker and loads the in-process model once instead of duplicating it under `--workers 2`.
- Compose passes the three new knobs to memory-api with safe defaults; both `.env.example` templates document them; mem_limit raised to 896m with the measured model RSS cited inline.

## Task Commits

Each code task was committed atomically:

1. **Task 1: Bake the model, force offline, make workers configurable** - `7d45437` (feat)
2. **Task 2: Wire EMBEDDINGS_PROVIDER + worker/RAM budget through compose and .env** - `dccc3b8` (feat)
3. **Task 3: Prove zero-network offline (arm64) + both-arch build (amd64)** - verification-only, no code change (Dockerfile needed no fix); results recorded below. No empty commit created.

**Plan metadata:** committed with this SUMMARY (docs).

## Files Created/Modified
- `apps/memory-api/Dockerfile` - Builder bakes `BAAI/bge-small-en-v1.5` into `/build/model_cache` via `PYTHONPATH=/build/deps python -c "..."`; runtime `COPY --from=builder --chown=xbrain:xbrain /build/model_cache /app/model_cache` before `USER xbrain`; `ENV HF_HUB_OFFLINE=1`; `ENV UVICORN_WORKERS=2` + shell-form `CMD exec python -m uvicorn ... --workers ${UVICORN_WORKERS}`.
- `infrastructure/docker-compose.yml` - memory-api `environment:` gains `EMBEDDINGS_PROVIDER`/`LOCAL_EMBEDDING_MODEL`/`UVICORN_WORKERS` (safe defaults); `mem_limit` 768m -> 896m with a comment citing the ~256/366 MB baked-model RSS and the OSS-light (local + workers=1) guidance.
- `.env.example` - New "Embeddings (Phase 19)" section documenting the three knobs; `OPENAI_API_KEY` comment updated to note embeddings are local by default and OpenAI embeddings are opt-in.
- `apps/memory-api/.env.example` - Mirrors the three knobs in a Phase 19 section.

## Docker Verification Results (Task 3)

All builds were LOCAL VERIFICATION ONLY — no image was tagged for prod, pushed, or deployed (honoring the arm64-dev / amd64-prod constraint; the prod image is built on the VM or via CI).

| Check | Command (context = repo root, MSYS_NO_PATHCONV=1) | Result |
|-------|----------------------------------------------------|--------|
| arm64 build | `docker build -f apps/memory-api/Dockerfile -t xbrain/memory-api:phase19-verify .` | PASS (exit 0) — bake step present, model baked |
| Zero-network embed | `docker run --rm --network none --entrypoint python xbrain/memory-api:phase19-verify -c "...TextEmbedding(cache_dir='/app/model_cache')...assert len(v)==384..."` | PASS — printed `OFFLINE_384_OK` live (not cached), network disabled |
| amd64 cross-build | `docker buildx build --platform linux/amd64 -f apps/memory-api/Dockerfile -t xbrain/memory-api:phase19-amd64-check --load .` | PASS (exit 0) — onnxruntime amd64 cp312 wheel installs, model bakes under QEMU |
| compose parses | `docker compose -f infrastructure/docker-compose.yml config` | PASS (exit 0) |

The `docker run --network none` step executed live and printed `OFFLINE_384_OK` — this is the authoritative D-19-01 proof that the model is baked and the non-root uid-10001 user reads it offline with zero outbound calls.

## mem_limit Decision (SC#4 — measured footprint)

- **Chosen value:** `896m` (was `768m`).
- **Measured baked-model RSS it was judged against:** ~256 MB on arm64-native, ~366 MB under amd64-QEMU emulation (RESEARCH §6 / Assumption A2). The amd64 number is emulated — treat it as "confirmed installable + offline-capable" and re-measure once on the real amd64 e2-medium VM before finalizing (Assumption A2, a follow-up, not a blocker).
- **Rationale:** OSS-light runs `EMBEDDINGS_PROVIDER=local` + `UVICORN_WORKERS=1`; the model loads lazily in-process on first embed. Adding ~256-366 MB on top of the already-768m-justified signin base warranted +128m of headroom to avoid OOM churn on that first load. `mem_limit` is a cap, not a reservation, so raising it does not consume RAM the process doesn't need, and it stays within the e2-medium budget. SaaS keeps `UVICORN_WORKERS=2` + `EMBEDDINGS_PROVIDER=openai`, so the in-process model never loads there and 896m is not tighter than 768m was for that path.

## Decisions Made
See `key-decisions` in frontmatter. Summary: mem_limit -> 896m with cited RSS; PYTHONPATH-based bake (single dependency source); shell-form CMD + exec for signal-correct configurable workers.

## Deviations from Plan
None - plan executed exactly as written. Task 3's build surfaced no fix, so the Dockerfile from Task 1 was left unchanged (as the plan permits). The PYTHONPATH-based bake (the plan's primary option) worked on both arches — the `pip install fastembed` fallback was not needed.

## Issues Encountered
- **Worktree started on the wrong base.** The orchestrator's base hash `5a034927` did not resolve (the real 19-01 merge commit is `5a03492a...` — an 8th-char typo, `a` not `7`), so the startup base-reset silently no-op'd and the worktree HEAD was left on an unrelated pre-phase-14 lineage (`c4492a1`, planning dir only through phase 13) with no phase-19 files. Resolved by verifying the intended base (`5a03492a`) contains the plan + Wave 1 work (config.py `EMBEDDING_CACHE_DIR=/app/model_cache`, embedders.py, fastembed in pyproject) and running the intended startup `git reset --hard 5a03492a`. All three plan tasks then executed against the correct base. Not a self-recovery of a protected ref — the reset targeted the per-agent worktree branch only.
- **Build layers were CACHED** from a prior identical build (an earlier executor produced byte-identical Task 1/2 commits on a separate branch). A cached successful build is still a successful build; crucially the offline `docker run --network none` was NOT cached and ran live to produce `OFFLINE_384_OK`.

## Known Stubs
None. All changes are build/config wiring; no placeholder values or unwired data paths were introduced.

## User Setup Required
None - no external service configuration required. OSS-light works keyless with the defaults; operators who want the OpenAI path set `EMBEDDINGS_PROVIDER=openai` + `OPENAI_API_KEY`.

## Next Phase Readiness
- Plan 19-03 can proceed on top of a baked, offline-proven, both-arch image.
- Follow-up (not a blocker): measure the real amd64 model RSS on the prod e2-medium VM to confirm or refine the 896m mem_limit (RESEARCH Assumption A2).

## Self-Check: PASSED

- Created: `19-02-SUMMARY.md` — FOUND
- Modified: `apps/memory-api/Dockerfile`, `infrastructure/docker-compose.yml`, `.env.example`, `apps/memory-api/.env.example` — all FOUND
- Commits: `7d45437`, `dccc3b8` — both FOUND

---
*Phase: 19-local-embeddings*
*Completed: 2026-07-18*
