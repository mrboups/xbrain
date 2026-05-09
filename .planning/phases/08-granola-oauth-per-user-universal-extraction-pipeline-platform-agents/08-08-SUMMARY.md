---
phase: 08-granola-oauth-per-user-universal-extraction-pipeline-platform-agents
plan: 08
status: complete
completed: 2026-05-09
---

# Summary: Verify Phase 8 + .env.example (Plan 08-08)

## Test mapping (ROADMAP criteria -> test numbers)
- Criterion 1 (Granola per-user revocable): Tests 2 + 6
- Criterion 5 (agent_definitions + admin CRUD): Tests 1 + 3 + 4
- Criterion 6 (invoke endpoint): Test 5
- Criterion 7 (GitHub repos dynamic): Test 7

## Containers to rebuild on VM
```
docker compose up -d --build memory-api granola-sync librechat librechat-bridge
```

## Migration order
Apply migration 0012 BEFORE rebuilding memory-api:
```
docker exec xbrain-memory-api python -m alembic upgrade head
```
(Or copy the migration file first if memory-api image does not have it)

## Running the verify
```
bash infrastructure/scripts/verify-phase8.sh
```
Expected: `PASS: 7 / 7` after all rebuilds complete.

## Files created/modified

- `infrastructure/scripts/verify-phase8.sh` — 7-test verification script mirroring verify-phase7.sh pattern
- `.env.example` — Phase 8 section appended (CONTACT_EXTRACTION, ANTHROPIC_CONTACT_MODEL, Granola per-user notes)

## Commits

- `ee1440e`: feat(scripts): add verify-phase8.sh — 7 tests Phase 8 (plan 08-08)
- `4e60658`: docs(env): add Phase 8 section — CONTACT_EXTRACTION + LIBRECHAT notes (plan 08-08)

## Self-Check: PASSED
