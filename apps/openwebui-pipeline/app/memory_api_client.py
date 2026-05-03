"""HTTP client to memory-api with pipeline service JWT (iss=openwebui-pipeline)."""

import time

import httpx
import structlog
from authlib.jose import jwt
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings

log = structlog.get_logger()


def make_pipeline_jwt(sub: str, team_scope: str, ttl_seconds: int = 300) -> str:
    """HS256 service JWT — iss differs from librechat-bridge for audit traceability."""
    now = int(time.time())
    return jwt.encode(
        {"alg": settings.JWT_ALGORITHM},
        {
            "iss": "openwebui-pipeline",
            "sub": sub,
            "team_scope": team_scope,
            "scope": "bridge",
            "iat": now,
            "exp": now + ttl_seconds,
        },
        settings.BRIDGE_SHARED_SECRET,
    ).decode("ascii")


def make_user_acting_jwt(
    *,
    user_sub: str,
    user_email: str,
    user_name: str | None,
    team_scope: str,
    ttl_seconds: int = 300,
) -> str:
    """Service JWT carrying the OWUI user identity — memory-api resolves to a real user.

    Required for /v1/promotions/* which rejects pure bridge principals.
    """
    now = int(time.time())
    payload: dict = {
        "iss": "openwebui-pipeline",
        "sub": user_sub,
        "team_scope": team_scope,
        "scope": "bridge",
        "iat": now,
        "exp": now + ttl_seconds,
        "acting_user_sub": user_sub,
        "acting_user_email": user_email,
    }
    if user_name:
        payload["acting_user_name"] = user_name
    return jwt.encode({"alg": settings.JWT_ALGORITHM}, payload, settings.BRIDGE_SHARED_SECRET).decode("ascii")


class MemoryApiClient:
    def __init__(self) -> None:
        self.base = settings.MEMORY_API_URL.rstrip("/")
        self.client = httpx.AsyncClient(timeout=10.0)

    async def aclose(self) -> None:
        await self.client.aclose()

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(min=0.3, max=2),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    async def post_message(
        self,
        *,
        sub: str,
        team_scope: str,
        conversation_id: str,
        role: str,
        content: str,
        source: str,
        metadata: dict | None = None,
    ) -> dict:
        token = make_pipeline_jwt(sub=sub, team_scope=team_scope)
        body = {
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "metadata": metadata or {},
            "tagging": {
                "team_scope": team_scope,
                "project_scope": None,
                "visibility": "team",
                "confidence": 1.0,
                "truth_level": "EPHEMERAL",
                "source": source,
                "validation_status": "pending",
            },
        }
        r = await self.client.post(
            f"{self.base}/v1/messages",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Team-Scope": team_scope,
            },
            json=body,
        )
        if r.status_code >= 400:
            log.warning(
                "memory_api_post_failed",
                status=r.status_code,
                body=r.text[:200],
                sub=sub,
                source=source,
            )
        r.raise_for_status()
        return r.json()

    async def list_pending_promotions(
        self, *, user_sub: str, user_email: str, user_name: str | None, team_scope: str
    ) -> list[dict]:
        token = make_user_acting_jwt(
            user_sub=user_sub, user_email=user_email, user_name=user_name, team_scope=team_scope
        )
        r = await self.client.get(
            f"{self.base}/v1/promotions/pending",
            headers={"Authorization": f"Bearer {token}", "X-Team-Scope": team_scope},
        )
        r.raise_for_status()
        return r.json()

    async def propose_promotion(
        self,
        *,
        user_sub: str,
        user_email: str,
        user_name: str | None,
        team_scope: str,
        item_id: str,
        target_level: str,
        rationale: str | None = None,
    ) -> dict:
        token = make_user_acting_jwt(
            user_sub=user_sub, user_email=user_email, user_name=user_name, team_scope=team_scope
        )
        r = await self.client.post(
            f"{self.base}/v1/promotions",
            headers={"Authorization": f"Bearer {token}", "X-Team-Scope": team_scope},
            json={"item_id": item_id, "target_level": target_level, "rationale": rationale},
        )
        r.raise_for_status()
        return r.json()

    async def approve_promotion(
        self,
        *,
        user_sub: str,
        user_email: str,
        user_name: str | None,
        team_scope: str,
        promotion_id: str,
    ) -> dict:
        token = make_user_acting_jwt(
            user_sub=user_sub, user_email=user_email, user_name=user_name, team_scope=team_scope
        )
        r = await self.client.post(
            f"{self.base}/v1/promotions/{promotion_id}/approve",
            headers={"Authorization": f"Bearer {token}", "X-Team-Scope": team_scope},
        )
        r.raise_for_status()
        return r.json()

    async def reject_promotion(
        self,
        *,
        user_sub: str,
        user_email: str,
        user_name: str | None,
        team_scope: str,
        promotion_id: str,
        reason: str,
    ) -> dict:
        token = make_user_acting_jwt(
            user_sub=user_sub, user_email=user_email, user_name=user_name, team_scope=team_scope
        )
        r = await self.client.post(
            f"{self.base}/v1/promotions/{promotion_id}/reject",
            headers={"Authorization": f"Bearer {token}", "X-Team-Scope": team_scope},
            json={"reason": reason},
        )
        r.raise_for_status()
        return r.json()

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(min=0.3, max=2),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    async def post_conversation(
        self,
        *,
        sub: str,
        team_scope: str,
        title: str,
        source: str,
    ) -> dict:
        token = make_pipeline_jwt(sub=sub, team_scope=team_scope)
        r = await self.client.post(
            f"{self.base}/v1/conversations",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Team-Scope": team_scope,
            },
            json={"title": title, "source": source},
        )
        if r.status_code >= 400:
            log.warning("memory_api_post_conv_failed", status=r.status_code, body=r.text[:200])
        r.raise_for_status()
        return r.json()
