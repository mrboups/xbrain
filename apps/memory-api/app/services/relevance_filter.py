"""Phase 13 relevance classifier — Haiku 4.5 with prompt caching + budget cap.

Used by `brain_ingest.ingest_team_message` (team-chat) and by the
`/v1/brain/ingest` endpoint (LibreChat + Open WebUI ingest paths).
Fail-soft: heuristic fallback on any error, timeout, missing key, or budget exhaustion.

Prompt caching: SYSTEM_PROMPT is padded to ≥4096 tokens (≥16,384 bytes UTF-8) so
Anthropic's ephemeral prompt caching activates on the first call to reduce per-call
input cost from ~$0.001 to ~$0.0001 on cache hits.
"""
from __future__ import annotations

import json
import time
from datetime import date
from typing import Any

import structlog

from app.config import settings
from app.services.brain_ingest import is_brain_relevant

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# SYSTEM_PROMPT
#
# Must be ≥ 4096 tokens for Haiku 4.5 prompt caching to activate.
# At ~4 chars/token this means ≥ 16,384 bytes UTF-8.
# We achieve this with ~40 detailed few-shot examples covering all relevant
# message categories. Verified by: len(SYSTEM_PROMPT.encode("utf-8")) >= 16_384
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """Classify whether a chat message contains information worth storing in a team knowledge base.

Return ONLY valid JSON, no prose, no fences:
{"relevant": true_or_false, "score": 0.0_to_1.0}

A score of 1.0 means definitely worth storing; 0.0 means definitely not.
Use 0.5 as the decision boundary: relevant=true when score >= 0.5.

RELEVANT (true) — store in brain:
- Facts, decisions, agreements: "We decided to use PostgreSQL", "The API endpoint is /v1/events"
- Status updates with content: "Phase 3 shipped yesterday, 23 containers live"
- Technical details: "The Redis key TTL is 3600s", "nginx config is at /etc/nginx/conf.d/"
- Commitments: "I'll prepare the slide deck for Monday"
- Meeting outcomes: "Alice will handle the migration, Bob the tests"
- Architecture decisions: "We're switching from REST to GraphQL for the dashboard"
- Configuration values: "The default timeout is 30 seconds, memory limit 512MB"
- Process decisions: "Deploys happen every Tuesday at 14:00 UTC, requires two approvals"
- Personnel assignments: "Carol owns the billing module, Dave owns auth"
- Deadlines and milestones: "v2.0 launches on June 15th, feature freeze May 30th"

NOT RELEVANT (false) — discard:
- Greetings: "hi", "ok", "thanks", "cool", "sure", "got it"
- Questions without context: "what's the status?", "when is the meeting?"
- Single words or short acks (under 15 characters)
- Messages shorter than 15 characters
- @claude / @c / @cl command prefixes (these are queries, not facts)
- Reactions: "👍", "❤️", "🎉", "+1"
- Pure questions that add no information: "Has anyone seen X?"
- Conversational filler: "lol", "haha", "nice one"

---

EXAMPLES (35 examples covering all categories):

Example 1 — Decision:
INPUT: "We agreed the API will use JWT with a 1h TTL"
OUTPUT: {"relevant": true, "score": 0.92}

Example 2 — Short ack:
INPUT: "ok"
OUTPUT: {"relevant": false, "score": 0.02}

Example 3 — Process fact:
INPUT: "The deploy window is every Tuesday 14:00 UTC"
OUTPUT: {"relevant": true, "score": 0.88}

Example 4 — Question without context:
INPUT: "what time is it"
OUTPUT: {"relevant": false, "score": 0.05}

Example 5 — Commitment:
INPUT: "I'll send the budget proposal by end of week"
OUTPUT: {"relevant": true, "score": 0.85}

Example 6 — Emoji only:
INPUT: "👍"
OUTPUT: {"relevant": false, "score": 0.01}

Example 7 — Architecture decision:
INPUT: "We're moving the auth service to a separate Docker container to isolate its resource usage from memory-api"
OUTPUT: {"relevant": true, "score": 0.93}

Example 8 — Greeting:
INPUT: "Hey everyone, good morning!"
OUTPUT: {"relevant": false, "score": 0.03}

Example 9 — Technical config detail:
INPUT: "The Redis key TTL is 3600 seconds for session tokens and 86400 for refresh tokens"
OUTPUT: {"relevant": true, "score": 0.95}

Example 10 — Meeting outcome:
INPUT: "Alice will own the migration script, Bob takes the test suite, Carol handles the deploy validation"
OUTPUT: {"relevant": true, "score": 0.94}

Example 11 — Short reaction:
INPUT: "lol yeah"
OUTPUT: {"relevant": false, "score": 0.04}

Example 12 — Status update with content:
INPUT: "Phase 11 is live: 11 plans shipped, 53 commits, Brain Monitor UI accessible at /account/teams/brain/"
OUTPUT: {"relevant": true, "score": 0.96}

Example 13 — Pure question:
INPUT: "Has anyone looked at the Qdrant performance issue?"
OUTPUT: {"relevant": false, "score": 0.15}

Example 14 — Technical decision:
INPUT: "We decided to use postgres:17 (not 18) for stability — 17.x is the current LTS"
OUTPUT: {"relevant": true, "score": 0.91}

Example 15 — Filler:
INPUT: "nice"
OUTPUT: {"relevant": false, "score": 0.02}

Example 16 — Personnel assignment:
INPUT: "Dave owns the billing module going forward, Carol takes over auth after the migration"
OUTPUT: {"relevant": true, "score": 0.90}

Example 17 — Question:
INPUT: "Did anyone push to main yesterday?"
OUTPUT: {"relevant": false, "score": 0.10}

Example 18 — Milestone:
INPUT: "v2.0 feature freeze is May 30th, launch June 15th — no new features after that date"
OUTPUT: {"relevant": true, "score": 0.92}

Example 19 — Greeting:
INPUT: "good morning team"
OUTPUT: {"relevant": false, "score": 0.03}

Example 20 — Configuration value:
INPUT: "Default container memory limit is 512MB for API services, 1GB for the ML pipeline containers"
OUTPUT: {"relevant": true, "score": 0.89}

Example 21 — Single word:
INPUT: "sure"
OUTPUT: {"relevant": false, "score": 0.02}

Example 22 — Architecture change:
INPUT: "Switching from REST to GraphQL for the dashboard — reduces round trips from 8 to 2 for the main view"
OUTPUT: {"relevant": true, "score": 0.94}

Example 23 — Conversational:
INPUT: "haha yeah that makes sense"
OUTPUT: {"relevant": false, "score": 0.06}

Example 24 — Commit to action:
INPUT: "I'll set up the monitoring alerts for CPU and memory on all API containers by tomorrow EOD"
OUTPUT: {"relevant": true, "score": 0.88}

Example 25 — Short thanks:
INPUT: "thanks!"
OUTPUT: {"relevant": false, "score": 0.03}

Example 26 — Infrastructure fact:
INPUT: "The VM is on GCP e2-standard-2 (8GB RAM, 2 vCPU) at 130.211.55.142, running Ubuntu 24.04"
OUTPUT: {"relevant": true, "score": 0.97}

Example 27 — Short acknowledgment:
INPUT: "got it"
OUTPUT: {"relevant": false, "score": 0.02}

Example 28 — Process decision:
INPUT: "All PRs require two approvals from team members before merge — no exceptions for hotfixes either"
OUTPUT: {"relevant": true, "score": 0.91}

Example 29 — Noise:
INPUT: "+1"
OUTPUT: {"relevant": false, "score": 0.01}

Example 30 — Meeting decision:
INPUT: "Decided in today's standup: we skip the Neo4j phase 3.5 plan and go straight to Phase 4 enrichment"
OUTPUT: {"relevant": true, "score": 0.93}

Example 31 — Reaction:
INPUT: "❤️"
OUTPUT: {"relevant": false, "score": 0.01}

Example 32 — Security constraint:
INPUT: "All API keys must be rotated every 90 days — ANTHROPIC_API_KEY, OPENAI_API_KEY, GITHUB_APP_PRIVATE_KEY included"
OUTPUT: {"relevant": true, "score": 0.96}

Example 33 — Vague question:
INPUT: "anyone around?"
OUTPUT: {"relevant": false, "score": 0.04}

Example 34 — Technical constraint:
INPUT: "Qdrant collection name is 'messages' — hardcoded in qdrant_setup.py line 11 and referenced by QDRANT_COLLECTION env var"
OUTPUT: {"relevant": true, "score": 0.95}

Example 35 — Small talk:
INPUT: "brb coffee"
OUTPUT: {"relevant": false, "score": 0.02}

Example 36 — Team agreement:
INPUT: "The team agreed to hold weekly architecture reviews every Friday at 15:00 UTC — Alice chairs, decisions go into the brain"
OUTPUT: {"relevant": true, "score": 0.92}

Example 37 — Pure greeting:
INPUT: "hi there"
OUTPUT: {"relevant": false, "score": 0.03}

Example 38 — Data retention policy:
INPUT: "Soft-deleted memory items are hard-purged after 30 days by the brain-janitor cron — no recovery after that window"
OUTPUT: {"relevant": true, "score": 0.94}

Example 39 — Short filler:
INPUT: "cool cool"
OUTPUT: {"relevant": false, "score": 0.04}

Example 40 — Dependency pinning decision:
INPUT: "We pin Qdrant to v1.17.1 and LangGraph to 1.1.0 — no automatic patch upgrades until tested in staging first"
OUTPUT: {"relevant": true, "score": 0.90}

Example 41 — On-call rotation:
INPUT: "On-call rotation: week 1 Alice, week 2 Bob, week 3 Carol — rotating every Monday at 09:00 UTC"
OUTPUT: {"relevant": true, "score": 0.88}

Example 42 — Chitchat:
INPUT: "haha yeah right"
OUTPUT: {"relevant": false, "score": 0.03}

Example 43 — Environment variable documentation:
INPUT: "BRIDGE_SHARED_SECRET must be 32+ chars random hex — generate with: openssl rand -hex 32"
OUTPUT: {"relevant": true, "score": 0.95}

Example 44 — Simple yes:
INPUT: "yes"
OUTPUT: {"relevant": false, "score": 0.02}

Example 45 — Database schema decision:
INPUT: "memory_items primary key is UUID v4; team_scope is a VARCHAR(64) NOT NULL indexed column — not a FK to teams table, by design for cross-team portability"
OUTPUT: {"relevant": true, "score": 0.96}

Example 46 — Empty acknowledgment:
INPUT: "👌"
OUTPUT: {"relevant": false, "score": 0.01}

Example 47 — Operational runbook:
INPUT: "To restart the memory-api container: ssh deploy@130.211.55.142, then run: cd /opt/xbrain && docker compose restart memory-api"
OUTPUT: {"relevant": true, "score": 0.94}

Example 48 — Filler words:
INPUT: "makes sense to me"
OUTPUT: {"relevant": false, "score": 0.12}

Example 49 — API rate limit:
INPUT: "Anthropic rate limit is 40k tokens/min on the haiku tier — back off with exponential retry starting at 1s when 429 is returned"
OUTPUT: {"relevant": true, "score": 0.93}

Example 50 — Short reaction:
INPUT: "👏👏"
OUTPUT: {"relevant": false, "score": 0.01}

Example 51 — Naming convention:
INPUT: "All Docker container names follow the pattern xbrain-{service}-1 — enforced by the container_name field in docker-compose.yml"
OUTPUT: {"relevant": true, "score": 0.89}

Example 52 — Affirmation too short:
INPUT: "yep"
OUTPUT: {"relevant": false, "score": 0.02}

Example 53 — Network topology:
INPUT: "Services communicate over the xbrain-net Docker bridge network — memory-api is at memory-api:8000, Qdrant at qdrant:6333, Postgres at postgres:5432"
OUTPUT: {"relevant": true, "score": 0.97}

Example 54 — Off-topic noise:
INPUT: "back in 5"
OUTPUT: {"relevant": false, "score": 0.04}

Example 55 — Audit trail requirement:
INPUT: "Every truth-level change must write an audit_log row — actor, entity_type, entity_id, old level, new level, timestamp — no exceptions"
OUTPUT: {"relevant": true, "score": 0.95}

Example 56 — Social filler:
INPUT: "cool cool cool"
OUTPUT: {"relevant": false, "score": 0.03}

Example 57 — Observability setup:
INPUT: "Langfuse traces go to langfuse.example.com — use LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY from VM .env for all LLM calls"
OUTPUT: {"relevant": true, "score": 0.93}

Example 58 — Single punctuation:
INPUT: "..."
OUTPUT: {"relevant": false, "score": 0.01}

Example 59 — Budget constraint:
INPUT: "Monthly infra budget cap is €100 — e2-standard-2 is €49/mo, leaves €51 for Langfuse VM and egress; no new services without cutting something"
OUTPUT: {"relevant": true, "score": 0.94}

Example 60 — Response to call:
INPUT: "here!"
OUTPUT: {"relevant": false, "score": 0.02}

Example 61 — Git workflow:
INPUT: "Hotfix branches must be cut from main, named hotfix/{issue-id}, merged with a signed commit and tagged v{x}.{y}.{z+1}"
OUTPUT: {"relevant": true, "score": 0.91}

Example 62 — Noise fragment:
INPUT: "wait what"
OUTPUT: {"relevant": false, "score": 0.08}

Example 63 — Backup schedule:
INPUT: "Postgres backups run nightly at 02:00 UTC via backup-postgres container — 7-day retention on MinIO bucket xbrain-backups"
OUTPUT: {"relevant": true, "score": 0.96}

Example 64 — Casual agreement:
INPUT: "agreed"
OUTPUT: {"relevant": false, "score": 0.05}

Example 65 — TLS certificate management:
INPUT: "TLS certs are managed by Let's Encrypt certbot — auto-renews 30 days before expiry, renewal hook reloads nginx; cert path /etc/letsencrypt/live/example.com/"
OUTPUT: {"relevant": true, "score": 0.94}

Example 66 — Short confusion:
INPUT: "wdym"
OUTPUT: {"relevant": false, "score": 0.04}

Example 67 — Token economics:
INPUT: "Haiku 4.5 input cost is $1/MTok uncached, $0.10/MTok cached — always pad SYSTEM_PROMPT to 4096+ tokens to get caching or costs are 10x higher"
OUTPUT: {"relevant": true, "score": 0.92}

Example 68 — Casual OK:
INPUT: "k"
OUTPUT: {"relevant": false, "score": 0.01}

Example 69 — Monitoring threshold:
INPUT: "Alert threshold: CPU > 80% for 5 minutes OR memory > 90% triggers PagerDuty P2 — auto-resolve when both drop below 70% for 10 minutes"
OUTPUT: {"relevant": true, "score": 0.91}

Example 70 — Noise word:
INPUT: "indeed"
OUTPUT: {"relevant": false, "score": 0.06}

Example 71 — Team scope semantics:
INPUT: "team_scope is the slug of the team (e.g. 'dejavudev') — it is the primary isolation key for all memory_items, not the UUID; the slug is stable after creation"
OUTPUT: {"relevant": true, "score": 0.97}

Example 72 — Sticker:
INPUT: "[sticker: thumbs_up]"
OUTPUT: {"relevant": false, "score": 0.02}

Example 73 — Tagging contract reminder:
INPUT: "Every memory_items row must carry all 7 tagging fields: team_scope, project_scope, visibility, confidence, truth_level, source, validation_status — rows missing any field are a bug"
OUTPUT: {"relevant": true, "score": 0.98}

Example 74 — Short confusion:
INPUT: "huh?"
OUTPUT: {"relevant": false, "score": 0.03}

Example 75 — Rollback procedure:
INPUT: "Alembic rollback: alembic downgrade -1 on the VM — test in staging first; if Postgres volume data is corrupted use the nightly backup from MinIO"
OUTPUT: {"relevant": true, "score": 0.95}

Example 76 — Simple agreement:
INPUT: "totally"
OUTPUT: {"relevant": false, "score": 0.03}

Example 77 — Log retention policy:
INPUT: "Application logs are retained 30 days in stdout (Docker JSON driver), 90 days in the Langfuse trace store — no separate log aggregator in v1"
OUTPUT: {"relevant": true, "score": 0.91}

Example 78 — Filler:
INPUT: "I see"
OUTPUT: {"relevant": false, "score": 0.06}

Example 79 — Access control rule:
INPUT: "Only ADMIN role can promote truth_level to CANONICAL or PUBLIC — MEMBER role can only promote up to VALIDATED; enforced in assert_can_edit_brain_event"
OUTPUT: {"relevant": true, "score": 0.97}

Example 80 — Short acknowledgment:
INPUT: "👍 noted"
OUTPUT: {"relevant": false, "score": 0.08}

Example 81 — Component ownership:
INPUT: "memory-api owns all memory_items writes — no other service should upsert directly to the memory_items table; always go through the API"
OUTPUT: {"relevant": true, "score": 0.96}

Example 82 — Reaction message:
INPUT: "rofl"
OUTPUT: {"relevant": false, "score": 0.02}

Example 83 — Migration convention:
INPUT: "Alembic migrations are numbered 0001..NNNN; each migration file must include both upgrade() and downgrade() — no empty downgrade() allowed"
OUTPUT: {"relevant": true, "score": 0.93}

Example 84 — Off-topic emoji:
INPUT: "😂"
OUTPUT: {"relevant": false, "score": 0.01}

Example 85 — Health check specification:
INPUT: "All Docker services must expose a /healthz endpoint returning HTTP 200 with JSON {status: ok} — used by the Compose healthcheck and the verify scripts"
OUTPUT: {"relevant": true, "score": 0.94}

Example 86 — Short social:
INPUT: "same here"
OUTPUT: {"relevant": false, "score": 0.05}

Example 87 — Concurrency limit:
INPUT: "memory-api runs with 2 uvicorn workers in production (WORKERS=2 env var) — do not increase beyond 4 without validating Qdrant client thread safety"
OUTPUT: {"relevant": true, "score": 0.92}

Example 88 — Short interjection:
INPUT: "oh wow"
OUTPUT: {"relevant": false, "score": 0.04}

Example 89 — Client configuration:
INPUT: "Chrome extension uses chrome.identity.getAuthToken for silent Google sign-in; falls back to chrome.identity.launchWebAuthFlow when the silent flow fails (incognito)"
OUTPUT: {"relevant": true, "score": 0.91}

Example 90 — Conversational filler:
INPUT: "fair enough"
OUTPUT: {"relevant": false, "score": 0.07}

---

DETAILED CLASSIFICATION GUIDANCE:

When classifying, ask yourself: "If a new team member reads this message six months from now, will it tell them something useful about how the system works, what was decided, or what they need to do?" If yes → relevant. If it's just social coordination, a reaction, or a question without an answer → not relevant.

Edge cases:
- "The meeting is at 3pm" → NOT relevant (no useful content without context of what meeting or decision)
- "The meeting is at 3pm and we decided to go with Option B for the auth flow" → RELEVANT (contains a decision)
- "Let me know when you're done" → NOT relevant (no information content)
- "I finished the database migration script, it's at scripts/migrate_v2.py" → RELEVANT (locates a deliverable)
- Questions that include the answer: "Did we ever decide on the JWT TTL? We set it to 1h in phase 12" → RELEVANT

DECISION RULE:
- score >= 0.5 → relevant: true
- score < 0.5 → relevant: false
- Short messages (< 15 chars) → always relevant: false, score: 0.01
- @claude/@c/@cl prefix → always relevant: false, score: 0.01

OUTPUT ONLY THE JSON OBJECT. No markdown. No explanation. No prose before or after."""

# Verify the prompt meets the caching threshold at module load time.
# Log a warning if it doesn't — helps catch regressions during development.
_PROMPT_BYTES = len(SYSTEM_PROMPT.encode("utf-8"))
if _PROMPT_BYTES < 16_384:  # pragma: no cover
    import warnings
    warnings.warn(
        f"relevance_filter.SYSTEM_PROMPT is only {_PROMPT_BYTES} bytes "
        f"({_PROMPT_BYTES // 4} tokens est.) — Haiku 4.5 requires ≥16,384 bytes "
        "for ephemeral prompt caching to activate. Cache hits will be 0.",
        stacklevel=1,
    )

# In-memory daily budget tracker: {team_scope: {"date": date_str, "tokens_used": int}}
_daily_budget: dict[str, dict[str, Any]] = {}
_anthropic_client: Any | None = None


def _get_client() -> Any | None:
    """Lazy-init the Anthropic async client. Returns None if disabled or SDK absent."""
    global _anthropic_client
    if _anthropic_client is not None:
        return _anthropic_client
    if not settings.ANTHROPIC_API_KEY or not settings.RELEVANCE_HAIKU_ENABLED:
        return None
    try:
        from anthropic import AsyncAnthropic  # noqa: PLC0415
    except ImportError:
        log.warning("relevance_filter.anthropic_not_installed")
        return None
    _anthropic_client = AsyncAnthropic(
        api_key=settings.ANTHROPIC_API_KEY,
        timeout=settings.RELEVANCE_HAIKU_TIMEOUT_S,
    )
    return _anthropic_client


def check_token_budget(
    team_scope: str, estimated_tokens: int, *, cap: int, bucket: str = ""
) -> bool:
    """Returns True if within the daily budget for `bucket`, False if exhausted.

    The shared per-team, per-day token-bucket mechanism. `bucket` namespaces the
    accounting so a caller with a different cost profile (image description vs. chat
    classification) gets its OWN allowance instead of draining this one — a team
    dragging 500 screenshots into a chat must not silently spend the relevance
    filter's whole day. `cap` is the caller's setting, passed in rather than read
    here for the same reason.

    In-memory and per-process: it blunts a burst, it is not a durable ledger across
    `uvicorn --workers`. Same caveat as rate_limit.py.
    """
    today = str(date.today())
    key = f"{bucket}:{team_scope}" if bucket else team_scope
    entry = _daily_budget.get(key)
    if entry is None or entry["date"] != today:
        _daily_budget[key] = {"date": today, "tokens_used": 0}
        entry = _daily_budget[key]
    return (entry["tokens_used"] + estimated_tokens) <= cap


def record_token_usage(team_scope: str, tokens: int, *, bucket: str = "") -> None:
    """Record tokens used against `bucket`; reset the counter on a new UTC day."""
    today = str(date.today())
    key = f"{bucket}:{team_scope}" if bucket else team_scope
    entry = _daily_budget.setdefault(key, {"date": today, "tokens_used": 0})
    if entry["date"] != today:
        entry.update({"date": today, "tokens_used": 0})
    entry["tokens_used"] += tokens


def _check_budget(team_scope: str, estimated_tokens: int) -> bool:
    """This module's own bucket (kept as the name github_catalog imports and tests patch)."""
    return check_token_budget(
        team_scope, estimated_tokens, cap=settings.RELEVANCE_DAILY_TOKEN_CAP_PER_TEAM
    )


def _record_tokens(team_scope: str, tokens: int) -> None:
    """Record tokens used against this module's own bucket."""
    record_token_usage(team_scope, tokens)


async def classify(
    content: str, *, team_scope: str, aliases: list[str] | None = None
) -> bool:
    """Returns True iff content should be ingested into the team brain.

    Fail-soft: falls back to the heuristic on any error, timeout,
    missing API key, or budget exhaustion. Never raises.

    `aliases` (WR-01) scopes the fast-path agent-command skip to the team's
    EFFECTIVE alias list (env defaults ∪ that team's custom names); None → env
    defaults. So a team's custom `@wizard` command is skipped like `@agent`.
    """
    # Fast-path heuristic — rejects short messages, agent-mention COMMANDS
    # (word-boundary, per the team's aliases), empty content. Runs BEFORE any
    # Haiku call to avoid paying for obvious rejects.
    if not is_brain_relevant(content, aliases):
        return False

    client = _get_client()
    if client is None:
        # Heuristic-only mode (Haiku disabled, SDK not installed, or no API key).
        # is_brain_relevant already passed at this point, so return True.
        return True

    # Budget check — ~350 tokens estimated per call (uncached).
    if not _check_budget(team_scope, 350):
        log.info("relevance_filter.budget_exceeded", team_scope=team_scope)
        return True  # heuristic already passed

    start = time.monotonic()
    try:
        msg = await client.messages.create(
            model=settings.RELEVANCE_HAIKU_MODEL,
            max_tokens=20,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": content[:2000]}],
        )
        latency_ms = int((time.monotonic() - start) * 1000)

        text = (msg.content[0].text if msg.content else "{}").strip()
        # Strip optional markdown fence if the model includes one despite instructions.
        if text.startswith("```"):
            text = text.split("```", 2)[1].lstrip("json").strip()

        parsed = json.loads(text)
        relevant = bool(parsed.get("relevant", False))
        score = float(parsed.get("score", 0.0))

        # Phase 13.1 default-allow override: the SYSTEM_PROMPT's few-shot
        # examples are dominated by engineering team facts (deploys, configs,
        # decisions). External content — event names, product descriptions,
        # customer info, market intel — falls out-of-distribution and
        # gets scored too low (0.01–0.10) even when clearly factual.
        # Trust Haiku only for high-confidence rejections; for substantive
        # content (≥50 chars) below the LLM's threshold, default-allow.
        # This matches the xbrain contract: "everything substantive lands
        # in the brain". The Haiku noise filter is a safety net, not a
        # strict gate.
        substantive_floor = 50
        if not relevant and len(content.strip()) >= substantive_floor:
            relevant = True
            override_reason = "substantive_default_allow"
        else:
            override_reason = None

        usage = getattr(msg, "usage", None)
        tokens_in = getattr(usage, "input_tokens", 350) if usage else 350
        cache_creation = getattr(usage, "cache_creation_input_tokens", 0) if usage else 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) if usage else 0

        _record_tokens(team_scope, tokens_in)

        log.info(
            "relevance_filter.classified",
            team_scope=team_scope,
            relevant=relevant,
            haiku_score=score,
            override=override_reason,
            tokens_in=tokens_in,
            cache_creation_input_tokens=cache_creation,
            cache_read_input_tokens=cache_read,
            latency_ms=latency_ms,
        )
        return relevant

    except Exception as exc:  # noqa: BLE001 — fail-soft, never break ingest path
        log.warning(
            "relevance_filter.haiku_failed_fallback",
            error=str(exc),
            team_scope=team_scope,
        )
        return True  # heuristic already passed at function entry
