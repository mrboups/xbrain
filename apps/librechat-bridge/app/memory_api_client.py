"""HTTP client to memory-api with bridge JWT, retries, and graceful logging."""

import httpx
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.bridge_token import make_bridge_jwt
from app.config import settings

log = structlog.get_logger()


class MemoryApiClient:
    def __init__(self) -> None:
        self.base = settings.MEMORY_API_URL.rstrip("/")
        self.client = httpx.AsyncClient(timeout=10.0)

    async def aclose(self) -> None:
        await self.client.aclose()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=0.5, max=4),
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
        token = make_bridge_jwt(sub=sub, team_scope=team_scope)
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

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=0.5, max=4),
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
        project_scope: str | None = None,
    ) -> dict:
        token = make_bridge_jwt(sub=sub, team_scope=team_scope)
        body = {"title": title, "source": source, "project_scope": project_scope}
        r = await self.client.post(
            f"{self.base}/v1/conversations",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Team-Scope": team_scope,
            },
            json=body,
        )
        if r.status_code >= 400:
            log.warning(
                "memory_api_post_conv_failed", status=r.status_code, body=r.text[:200]
            )
        r.raise_for_status()
        return r.json()
