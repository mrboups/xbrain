"""promotion-manager pipeline — slash commands inside Open WebUI chat.

Supported commands (must be the first non-whitespace token of the *last user* message):
    /promotions-pending                              List the team's pending promotions
    /propose <item_id> <TARGET_LEVEL> [reason...]    Propose a truth-level promotion
    /approve <promotion_id>                          Approve (1 of N — admin only)
    /reject  <promotion_id> <reason>                 Reject with a reason (admin only)

If the message is not a recognized command, returns None — main.py routes the
message to the LLM as usual.
"""

from __future__ import annotations

import re
from typing import Optional

import httpx
import structlog

from app.memory_api_client import MemoryApiClient

log = structlog.get_logger()

_VALID_LEVELS = {"WORKING", "VALIDATED", "CANONICAL", "PUBLIC"}
_TOKEN_RE = re.compile(r"\s+")


def _format_pending(items: list[dict]) -> str:
    if not items:
        return "_No pending promotions in this team._"
    lines = ["**Pending promotions:**", ""]
    for p in items:
        line = (
            f"- `{p['id'][:8]}…` — `{p['memory_item_id'][:8]}…` "
            f"`{p['from_level']} → {p['to_level']}` "
            f"(proposed by {str(p['proposed_by'])[:8]}…)"
        )
        if p.get("rationale"):
            line += f" — _{p['rationale']}_"
        lines.append(line)
    return "\n".join(lines)


def _http_error_to_msg(e: httpx.HTTPStatusError) -> str:
    try:
        detail = e.response.json().get("detail", e.response.text[:200])
    except Exception:
        detail = e.response.text[:200]
    return f"❌ memory-api {e.response.status_code}: {detail}"


def _phase2_promotions_message() -> str:
    return (
        "⚠️ **Les commandes promotions dépendent des endpoints "
        "`/v1/promotions/*` du `memory-api` (xbrain Phase 2) qui ne sont "
        "pas encore déployés.**\n\n"
        "`/promotions-pending`, `/propose`, `/approve`, `/reject` seront "
        "actifs une fois la stack Phase 2 ship'd.\n\n"
        "En attendant, envoie un message normal pour parler à l'LLM."
    )


async def try_handle(
    *,
    mem: MemoryApiClient,
    user_message: str,
    user_sub: str,
    user_email: str,
    user_name: str | None,
    team_scope: str,
) -> Optional[str]:
    """Return assistant content if the message is a recognized command, else None."""
    stripped = user_message.strip()
    if not stripped.startswith("/"):
        return None
    parts = _TOKEN_RE.split(stripped, maxsplit=4)
    cmd = parts[0].lower()

    try:
        if cmd == "/promotions-pending":
            items = await mem.list_pending_promotions(
                user_sub=user_sub, user_email=user_email, user_name=user_name,
                team_scope=team_scope,
            )
            return _format_pending(items)

        if cmd == "/propose":
            # /propose <item_id> <TARGET> [rationale...]
            if len(parts) < 3:
                return "Usage: `/propose <item_id> <WORKING|VALIDATED|CANONICAL|PUBLIC> [reason]`"
            item_id, target = parts[1], parts[2].upper()
            if target not in _VALID_LEVELS:
                return f"Invalid target_level `{target}`. Allowed: {sorted(_VALID_LEVELS)}"
            rationale = " ".join(parts[3:]).strip() or None
            promo = await mem.propose_promotion(
                user_sub=user_sub, user_email=user_email, user_name=user_name,
                team_scope=team_scope,
                item_id=item_id, target_level=target, rationale=rationale,
            )
            status = promo["status"]
            if status == "auto":
                return f"✅ Auto-promoted `{item_id[:8]}…` → **{target}** (no approval required)."
            return (
                f"📝 Proposed `{item_id[:8]}…` → **{target}**. "
                f"Promotion `{promo['id'][:8]}…` is **pending** — needs admin approval."
            )

        if cmd == "/approve":
            if len(parts) < 2:
                return "Usage: `/approve <promotion_id>`"
            promotion_id = parts[1]
            promo = await mem.approve_promotion(
                user_sub=user_sub, user_email=user_email, user_name=user_name,
                team_scope=team_scope, promotion_id=promotion_id,
            )
            if promo["status"] == "approved":
                return (
                    f"✅ Approved — item `{str(promo['memory_item_id'])[:8]}…` "
                    f"is now **{promo['to_level']}**."
                )
            return (
                f"☑️ Your approval is recorded but the promotion still needs another "
                f"distinct admin approval to apply (CANONICAL = 4-eyes)."
            )

        if cmd == "/reject":
            if len(parts) < 3:
                return "Usage: `/reject <promotion_id> <reason>`"
            promotion_id = parts[1]
            reason = " ".join(parts[2:]).strip()
            promo = await mem.reject_promotion(
                user_sub=user_sub, user_email=user_email, user_name=user_name,
                team_scope=team_scope, promotion_id=promotion_id, reason=reason,
            )
            return (
                f"🚫 Rejected promotion `{promo['id'][:8]}…` — item stays at "
                f"**{promo['from_level']}**.\nReason: _{reason}_"
            )

    except httpx.HTTPStatusError as e:
        log.warning("promotion_manager_http_error", cmd=cmd, status=e.response.status_code)
        # 404 = route inexistante = endpoints Phase 2 pas encore déployés sur ce memory-api
        if e.response.status_code == 404:
            return _phase2_promotions_message()
        return _http_error_to_msg(e)
    except httpx.HTTPError as e:
        log.warning("promotion_manager_network_error", cmd=cmd, err=str(e))
        return _phase2_promotions_message()

    # Unknown / not a promotion command → let the LLM handle it
    return None
