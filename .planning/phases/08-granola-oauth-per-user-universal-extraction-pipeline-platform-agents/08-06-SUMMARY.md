---
phase: 08-granola-oauth-per-user-universal-extraction-pipeline-platform-agents
plan: 06
status: complete
completed: 2026-05-09
---

# Summary: Contact Extractor — LibreChat Bridge (Plan 08-06)

## Pattern
Mirror of task_intent_detector.py (Phase 7 plan 07-09). Same lazy AsyncAnthropic singleton, fail-soft, opt-in env var.

## Decision: both user + assistant messages
Processes ALL messages (user and assistant). Assistant responses also mention people ("Alice approved the deck"). Symmetric extraction prevents one-way CRM gaps.

## Default OFF
CONTACT_EXTRACTION=false by default — no Anthropic spend until explicitly enabled. Set CONTACT_EXTRACTION=true in docker-compose env to activate.

## Architecture
1. mongo_watcher detects new message → forwards to memory-api → THEN fire-and-forget create_task(extract_contacts_from_message)
2. contact_extractor calls Claude haiku → parses JSON array of person mentions → upserts each via POST /v1/crm/contacts with bridge JWT
3. truth_level=EPHEMERAL (lower confidence than Granola attendees), confidence=0.6

## Rebuild required
`docker compose up -d --build librechat-bridge` on VM to activate.
