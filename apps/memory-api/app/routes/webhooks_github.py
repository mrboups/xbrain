"""Phase 12 — GitHub App webhook receiver.

Single endpoint: POST /v1/webhooks/github/installation
Receives `installation` and `installation_repositories` events from the
xbrain GitHub App. Verifies the X-Hub-Signature-256 HMAC, dispatches on
event + action, updates the installations table via app.repos.installations.

CRITICAL: signature is over the RAW BYTES of the request body. We MUST read
the body via Request.body() (bytes) BEFORE any Pydantic parsing — see
RESEARCH §Pitfall 5. Using a Pydantic model parameter would consume the
body stream and a re-serialization wouldn't match GitHub's exact JSON
(field ordering, whitespace, escaping differ — HMAC would always fail).

Subscriptions configured in GitHub App settings (operator runbook in 12-11):
  - installation                  (created / deleted / suspend / unsuspend /
                                   new_permissions_accepted)
  - installation_repositories     (added / removed — logged only in v1)

NOT subscribed: installation_target (org rename / account transfer).
Deferred per RESEARCH §Q13 — minimal v1.

Security:
  - HMAC-SHA256 with hmac.compare_digest (timing-safe — RESEARCH §Pitfall 8).
  - No IP allowlisting (HMAC is the auth — GitHub's IP list changes and is
    brittle for prod, RESEARCH §Pitfall 8).
  - Return 401 on signature mismatch with no body details (no info leak).
  - Fail closed (503) if GITHUB_APP_WEBHOOK_SECRET is unset (misconfiguration).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.deps import get_session
from app.repos.installations import (
    revoke_installation,
    suspend_installation,
    unsuspend_installation,
    update_installation_permissions,
    upsert_installation,
)

router = APIRouter()
log = structlog.get_logger(__name__)


def _verify_signature(payload_body: bytes, signature_header: str | None) -> None:
    """Raise HTTPException(401) if signature is missing or mismatches.

    Reference:
    https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries
    """
    if not settings.GITHUB_APP_WEBHOOK_SECRET:
        # Misconfiguration — fail closed (don't accept anything).
        log.error("github_webhook.no_secret_configured")
        raise HTTPException(503, "Webhook secret not configured")
    if not signature_header:
        raise HTTPException(401, "Missing X-Hub-Signature-256")
    expected = "sha256=" + hmac.new(
        settings.GITHUB_APP_WEBHOOK_SECRET.encode("utf-8"),
        payload_body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature_header):
        log.warning(
            "github_webhook.signature_mismatch",
            header_prefix=signature_header[:16],
        )
        raise HTTPException(401, "Signature mismatch")


async def _handle_installation_event(
    session: AsyncSession,
    payload: dict[str, Any],
) -> None:
    """Dispatch an `installation` event to the appropriate repo helper.

    Unhandled actions are logged and ignored — GitHub may add new actions
    in the future and we want to return 200 so they don't retry.
    """
    action = payload.get("action")
    install_obj = payload.get("installation") or {}
    installation_id = install_obj.get("id")
    if not isinstance(installation_id, int):
        log.warning("github_webhook.installation.no_id", action=action)
        return
    account = install_obj.get("account") or {}
    sender = payload.get("sender") or {}

    if action == "created":
        await upsert_installation(
            session,
            installation_id=installation_id,
            github_org_login=account.get("login") or "",
            github_account_type=account.get("type") or "Organization",
            installed_by_github_id=sender.get("id"),
            permissions=install_obj.get("permissions") or {},
            raw_payload=payload,
        )
    elif action == "deleted":
        await revoke_installation(session, installation_id)
    elif action == "suspend":
        await suspend_installation(session, installation_id)
    elif action == "unsuspend":
        await unsuspend_installation(session, installation_id)
    elif action == "new_permissions_accepted":
        await update_installation_permissions(
            session,
            installation_id=installation_id,
            permissions=install_obj.get("permissions") or {},
            raw_payload=payload,
        )
    else:
        log.info(
            "github_webhook.installation.unhandled_action",
            action=action,
            installation_id=installation_id,
        )


async def _handle_installation_repositories_event(
    payload: dict[str, Any],
) -> None:
    """v1: log only (we have no repo permissions). Future-proofs subscription."""
    action = payload.get("action")
    install_obj = payload.get("installation") or {}
    log.info(
        "github_webhook.installation_repositories",
        installation_id=install_obj.get("id"),
        action=action,
        added_count=len(payload.get("repositories_added") or []),
        removed_count=len(payload.get("repositories_removed") or []),
    )


@router.post("/installation", status_code=200)
async def github_installation_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Receive installation + installation_repositories webhooks.

    Always returns 200 on signature-verified payloads (even unhandled
    actions / events) so GitHub doesn't retry. Returns 401 on signature
    failure, 400 on malformed JSON, 503 on misconfigured secret.
    """
    # 1. Read raw body BEFORE any parsing — HMAC is over the exact bytes.
    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    _verify_signature(raw_body, signature)

    # 2. Parse only after signature verified — never trust unverified input.
    event = request.headers.get("X-GitHub-Event", "")
    delivery_id = request.headers.get("X-GitHub-Delivery", "")
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        log.error("github_webhook.bad_json", delivery_id=delivery_id)
        raise HTTPException(400, "Invalid JSON") from exc

    log.info(
        "github_webhook.received",
        event=event,
        action=payload.get("action") if isinstance(payload, dict) else None,
        delivery_id=delivery_id,
    )

    # 3. Dispatch on X-GitHub-Event.
    if event == "installation" and isinstance(payload, dict):
        await _handle_installation_event(session, payload)
    elif event == "installation_repositories" and isinstance(payload, dict):
        await _handle_installation_repositories_event(payload)
    elif event == "ping":
        # GitHub sends a ping immediately when the webhook is configured.
        # Payload contains `zen` + `hook_id`. Nothing to persist.
        log.info("github_webhook.ping", delivery_id=delivery_id)
    else:
        log.info(
            "github_webhook.unhandled_event",
            event=event,
            delivery_id=delivery_id,
        )

    return {"ok": True, "delivery_id": delivery_id, "event": event}
