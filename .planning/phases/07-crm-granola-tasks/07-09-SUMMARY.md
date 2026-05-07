---
phase: "07"
plan: "07-09"
subsystem: "librechat-bridge"
tags: ["task-intent", "D5-trigger-3", "librechat", "bridge", "claude-detection"]
dependency_graph:
  requires: ["07-03", "07-06", "07-08"]
  provides: ["D5-trigger-3-librechat-chat", "metadata.contains_action-flag"]
  affects: ["librechat-bridge", "memory-api/_maybe_create_task_from_action"]
tech_stack:
  added: ["anthropic>=0.50.0 (librechat-bridge dep)"]
  patterns: ["fail-soft Claude detection", "metadata flag delegation", "lazy import"]
key_files:
  created:
    - "apps/librechat-bridge/app/task_intent_detector.py"
  modified:
    - "apps/librechat-bridge/app/config.py"
    - "apps/librechat-bridge/app/mongo_watcher.py"
    - "apps/librechat-bridge/pyproject.toml"
    - "infrastructure/docker-compose.yml"
decisions:
  - "Bridge sets metadata.contains_action=true rather than calling /v1/tasks directly (07-03 rejects bridge JWT)"
  - "Default TASK_INTENT_DETECTION=false — opt-in kill-switch"
  - "Lazy anthropic import so module loads without package installed"
  - "4000-char cap on message content sent to Claude (cost + T-07-09-08)"
metrics:
  duration: "216 seconds"
  completed: "2026-05-07"
  tasks_completed: 3
  files_created: 1
  files_modified: 4
---

# Phase 7 Plan 09: LibreChat Task Intent Detector Summary

**One-liner:** D5 trigger 3 via fail-soft Claude Haiku detection in librechat-bridge; delegates task creation to 07-06 hook via metadata.contains_action flag.

## What Was Built

Three changes implement D5 trigger 3 (LibreChat chat message → auto-task creation):

1. **`task_intent_detector.py`** — new module with `async detect_task_intent(message_content)`. Calls Claude (Haiku by default) with a structured JSON prompt that returns `{has_task, title, description, assignee_email, assignee_name}`. Fail-soft at 4 levels: disabled config, missing API key, short content (<8 chars), any exception. Never raises.

2. **`mongo_watcher.py` + `config.py` + `pyproject.toml`** — the hook is inserted in `messages_watch_loop` AFTER `map_message`, BEFORE `mem.post_message`. User-authored messages only (guard: `payload.get("role") == "user"`). When `has_task=True`, sets `payload["metadata"]["contains_action"] = True` plus optional `task_intent_title` and `task_intent_assignee_email`. Logs `task_intent.detected` with sub/conv/title (no email logged, per T-07-09-05).

3. **`docker-compose.yml`** — 3 env vars added to the existing `librechat-bridge` service block: `TASK_INTENT_DETECTION` (default `false`), `ANTHROPIC_API_KEY` (pass-through), `ANTHROPIC_TASK_INTENT_MODEL` (default `claude-3-5-haiku-20241022`).

## Why metadata.contains_action Instead of POST /v1/tasks

The bridge JWT is rejected by `POST /v1/tasks` (plan 07-03). That endpoint calls `_user_id_from_principal` which raises HTTP 401 for bridge principals — it requires a real user identity to populate `created_by`. The bridge has no user identity to present.

The correct delegation path is:
1. Bridge detects intent → sets `metadata.contains_action = True` on the memory_item payload
2. Bridge forwards the payload to memory-api via `POST /v1/messages` (already trusted — bridge JWT accepted here since Phase 1)
3. memory-api's `_maybe_create_task_from_action` background hook (plan 07-06) sees `contains_action=True`, runs its own Claude extraction (re-validation), and creates the task with `created_by = NULL` (nullable since migration 0010) and `source = 'chat'`

This approach:
- Keeps a single source of truth for task creation logic (07-06 hook)
- Provides full audit chain: chat message → memory_item (logged) → task (audit_log with source_ref)
- Avoids duplicating contact resolution logic (bridge has no access to `/v1/crm/contacts`)

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| T1 | 3628d38 | feat(bridge): add task_intent_detector.py |
| T2 | b866c99 | feat(bridge): wire hook into mongo_watcher + config + dep |
| T3 | 01c26f9 | feat(bridge): env vars in docker-compose librechat-bridge |

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — the detection module is wired end-to-end. The `TASK_INTENT_DETECTION=false` default is an intentional kill-switch, not a stub.

## Threat Flags

No new threat surface beyond what is documented in the plan's STRIDE register (T-07-09-01 through T-07-09-10). Notably:
- Outbound HTTPS to api.anthropic.com (same trust posture as 07-05/07-06, accepted)
- `TASK_INTENT_DETECTION=false` kill-switch lets ops disable per environment

## Self-Check: PASSED

- `apps/librechat-bridge/app/task_intent_detector.py` exists and imports cleanly
- `apps/librechat-bridge/app/config.py` contains `TASK_INTENT_DETECTION: bool = False`
- `apps/librechat-bridge/app/mongo_watcher.py` contains the hook at correct position
- `infrastructure/docker-compose.yml` YAML valid (Python yaml.safe_load confirmed)
- All 3 task commits exist: 3628d38, b866c99, 01c26f9
