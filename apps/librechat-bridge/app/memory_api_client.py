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
    async def get_system_prompt(
        self,
        *,
        sub: str,
        team_scope: str,
        query: str,
        project_scope: str | None = None,
        top_k: int | None = None,
    ) -> dict:
        """Fetch the team's CANONICAL-fact addendum for a given query (RAG)."""
        token = make_bridge_jwt(sub=sub, team_scope=team_scope)
        params: dict[str, str | int] = {"query": query[:500]}
        if project_scope:
            params["project_scope"] = project_scope
        if top_k is not None:
            params["top_k"] = top_k
        r = await self.client.get(
            f"{self.base}/v1/system-prompt",
            headers={"Authorization": f"Bearer {token}", "X-Team-Scope": team_scope},
            params=params,
        )
        if r.status_code >= 400:
            log.warning(
                "memory_api_get_system_prompt_failed",
                status=r.status_code,
                body=r.text[:200],
            )
        r.raise_for_status()
        return r.json()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=0.5, max=4),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    async def brain_ingest(
        self,
        *,
        sub: str,
        team_scope: str,
        content: str,
        source: str,
        metadata: dict | None = None,
        project_scope: str | None = None,
    ) -> dict:
        """POST /v1/brain/ingest -- fire-and-forget brain upsert via memory-api.

        Phase 13 plan 13-04: caller MUST invoke this inside an asyncio.create_task --
        this method itself is non-blocking but has 3 retries with backoff,
        so its total time-budget is bounded around ~7s worst-case (0.5+2+4).

        Idempotency: pass metadata['idempotency_key'] (e.g. f"librechat:{mongo_id}")
        so server-side uuid5(BRAIN_INGEST_NS, key) produces a deterministic
        MemoryItem.id under Mongo change-stream resume-token re-delivery.
        """
        token = make_bridge_jwt(sub=sub, team_scope=team_scope)
        body = {
            "content": content,
            "source": source,
            "metadata": metadata or {},
            "project_scope": project_scope,
        }
        r = await self.client.post(
            f"{self.base}/v1/brain/ingest",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Team-Scope": team_scope,
            },
            json=body,
        )
        if r.status_code >= 400:
            log.warning(
                "memory_api_brain_ingest_failed",
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
