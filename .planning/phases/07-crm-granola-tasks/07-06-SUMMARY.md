---
phase: "07"
plan: "07-06"
subsystem: "memory-api/hooks"
tags: ["crm", "tasks", "background-tasks", "notifications", "anthropic", "smtp"]
dependency_graph:
  requires: ["07-01", "07-02", "07-03", "07-04"]
  provides: ["auto-contact-extraction", "auto-task-creation", "email-notifications"]
  affects: ["memory-api", "tasks", "contacts"]
tech_stack:
  added: ["aiosmtplib>=3.0.0", "anthropic>=0.50.0"]
  patterns: ["asyncio.create_task fire-and-forget", "fail-soft try/except", "async_session_factory for background DB"]
key_files:
  created:
    - apps/memory-api/app/services/notifications.py
  modified:
    - apps/memory-api/pyproject.toml
    - apps/memory-api/app/routes/memory.py
    - apps/memory-api/app/routes/tasks.py
decisions:
  - "async_session_factory used in background tasks (not get_session which is request-scoped)"
  - "Claude Haiku model hard-coded (claude-3-5-haiku-20241022) for cost efficiency"
  - "created_by = NULL for system-generated tasks (migration 0010 made column nullable)"
  - "Content capped at 10000 chars for contact extraction, 8000 for task extraction"
  - "Lazy import of aiosmtplib inside send function body (avoids ImportError at module load)"
  - "assignee_email detection in _maybe_create_task_from_action triggers immediate email notification"
metrics:
  duration: "~25 min"
  completed: "2026-05-07"
  tasks_completed: 4
  files_changed: 4
---

# Phase 07 Plan 06: Background CRM Extraction, Auto-Task Creation, and Email Notifications Summary

**One-liner:** Three fail-soft background hooks wired into memory-api: Claude Haiku extracts CRM contacts and auto-creates tasks from action items in memory upserts, plus aiosmtplib email notifications fire on task assignment.

## Tasks Completed

| # | Task | Commit | Files |
|---|------|--------|-------|
| 1 | Add aiosmtplib + anthropic deps | dd1944e | apps/memory-api/pyproject.toml |
| 2 | Create notifications.py service | 604b955 | apps/memory-api/app/services/notifications.py |
| 3 | Add _extract_crm_contacts + _maybe_create_task_from_action | d7b6691 | apps/memory-api/app/routes/memory.py |
| 4 | Wire notification hooks in tasks.py POST + PATCH | f321a9f | apps/memory-api/app/routes/tasks.py |

## What Was Built

### `_extract_crm_contacts` (memory.py background task)
- Triggered via `asyncio.create_task()` on every memory upsert with content >= 50 chars
- Calls Claude Haiku with strict JSON prompt to extract `[{name, email}]` from content
- Upserts into `contacts` table: confidence=0.6 (with email), 0.4 (name only); truth_level='EPHEMERAL'
- `ON CONFLICT (team_scope, email) WHERE email IS NOT NULL DO UPDATE` for idempotency
- Content capped at 10,000 chars; max 20 contacts per item
- Fail-soft: ANTHROPIC_API_KEY absent → warning + return; any error → log.warning("crm.extract_skipped")

### `_maybe_create_task_from_action` (memory.py background task)
- Triggers when `metadata.contains_action == True` OR regex `\b(TODO|à faire|action required|action requise|to do)\b` (case-insensitive)
- Calls Claude Haiku to extract `{title, description, assignee_email}` from content
- Inserts into `tasks` with `created_by = NULL` (system attribution), `source` = 'agent' or 'chat'
- If Claude returns `assignee_email` and contact exists in DB, sets `assigned_to` + fires email notification
- Fail-soft: no key → warning + return; any error → log.warning("tasks.auto_skipped")

### `send_task_notification_email` (services/notifications.py)
- async function, called fire-and-forget via `asyncio.create_task()`
- 3-level fail-soft: SMTP_HOST empty → warning + return; recipient empty → warning + return; send exception → log.warning
- aiosmtplib lazy-imported inside function body (avoids ImportError if not installed)
- Email subject: "New task assigned: {title}"; body includes team_scope, task_id, optional dashboard_url
- `start_tls=settings.SMTP_TLS`, `timeout=20` cap

### tasks.py notification hooks
- **POST /tasks**: after commit, if `body.assigned_to` set, looks up contact email and fires notification
- **PATCH /tasks/{id}**: SELECT now reads `status, assigned_to` to detect assignment changes; notification fires only when `assigned_to` changes to a new non-null value

## Integration with 07-09 (librechat-bridge)
Plan 07-09 implements the `librechat-bridge` extension that sets `metadata.contains_action=true` on memory_items originating from LibreChat chats with detected task intent. This plan's `_maybe_create_task_from_action` hook then picks up those memory_items and silently creates tasks server-side. **No logic duplication**: 07-09 detects intent, 07-06 creates tasks. The regex fallback (`TODO`, `à faire`, etc.) also catches direct agent posts without waiting for 07-09.

## Deviations from Plan

None — plan executed exactly as written.

The `noqa: E402` comment was added to the `from anthropic import AsyncAnthropic` import since it appears after the `_GRAPHITI_URL` module-level constant and after other imports within the same file block; this is a cosmetic lint suppressor only and does not affect behavior.

## Known Stubs

None. All functions are fully wired. `dashboard_url=None` is intentional — the plan specifies "dashboard_url=None for now" and notes that when added it must point to `https://*.dejavu.cat/...`.

## Threat Flags

No new network endpoints introduced. All SQL is parameterized. Background tasks use internal DB only. Email SMTP is outbound only via configured relay.

## Self-Check: PASSED

Files verified present:
- apps/memory-api/app/services/notifications.py — created
- apps/memory-api/app/routes/memory.py — contains `_extract_crm_contacts`, `_maybe_create_task_from_action`, `_ACTION_RE`, `async_session_factory` import, both `asyncio.create_task` wiring calls
- apps/memory-api/app/routes/tasks.py — contains `import asyncio`, `send_task_notification_email` import, 2 notification call sites, updated `SELECT status, assigned_to FROM tasks`
- apps/memory-api/pyproject.toml — contains `aiosmtplib>=3.0.0` and `anthropic>=0.50.0`

Commits verified:
- dd1944e (T1), 604b955 (T2), d7b6691 (T3), f321a9f (T4) — all present in git log
