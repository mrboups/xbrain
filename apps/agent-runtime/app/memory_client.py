"""HTTP client to memory-api — used by agents to read/write the shared memory layer.

Agents talk to memory-api over HTTP with a service JWT (`iss=agent-runtime`).
They MUST NOT bypass memory-api to write/read Postgres or Qdrant directly —
that would skip the tagging contract enforcement and audit logging.
"""

import time

import httpx
import structlog
from authlib.jose import jwt
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings

log = structlog.get_logger()


def make_agent_jwt(*, agent_sub: str, team_scope: str, ttl_seconds: int = 300) -> str:
    """HS256 service JWT — `iss=agent-runtime` for audit traceability."""
    now = int(time.time())
    return jwt.encode(
        {"alg": settings.JWT_ALGORITHM},
        {
            "iss": "agent-runtime",
            "sub": agent_sub,
            "team_scope": team_scope,
            "scope": "bridge",
            "iat": now,
            "exp": now + ttl_seconds,
        },
        settings.BRIDGE_SHARED_SECRET,
    ).decode("ascii")


class MemoryApiClient:
    def __init__(self) -> None:
        self.base = settings.MEMORY_API_URL.rstrip("/")
        self.client = httpx.AsyncClient(timeout=15.0)

    async def aclose(self) -> None:
        await self.client.aclose()

    def _headers(self, *, agent_sub: str, team_scope: str) -> dict[str, str]:
        token = make_agent_jwt(agent_sub=agent_sub, team_scope=team_scope)
        return {"Authorization": f"Bearer {token}", "X-Team-Scope": team_scope}

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=0.3, max=3),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    async def upsert_memory(self, *, agent_sub: str, team_scope: str, item: dict) -> dict:
        r = await self.client.post(
            f"{self.base}/v1/memory/upsert",
            headers=self._headers(agent_sub=agent_sub, team_scope=team_scope),
            json={"item": item},
        )
        r.raise_for_status()
        return r.json()

    async def upsert_fact(
        self,
        *,
        agent_sub: str,
        team_scope: str,
        content: str,
        truth_level: str = "WORKING",
        confidence: float = 0.7,
        source: str = "agent:ingestion-v1",
        project_scope: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """Convenience wrapper — builds a full MemoryItem from agent-side primitives.

        Agents producing facts call this instead of constructing a MemoryItem
        themselves. ID is generated server-side; created_at/updated_at default to now.
        """
        import uuid
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        item = {
            "id": str(uuid.uuid4()),
            "team_scope": team_scope,
            "project_scope": project_scope,
            "content": content,
            "metadata": metadata or {},
            "embedding": None,
            "visibility": "team",
            "truth_level": truth_level,
            "confidence": confidence,
            "source": source,
            "validation_status": "pending",
            "created_at": now,
            "updated_at": now,
        }
        return await self.upsert_memory(
            agent_sub=agent_sub, team_scope=team_scope, item=item
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=0.3, max=3),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    async def search_memory(
        self,
        *,
        agent_sub: str,
        team_scope: str,
        query: str,
        truth_level_min: str | None = None,
        project_scope: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        params: dict[str, str | int] = {"q": query, "limit": limit}
        if truth_level_min:
            params["truth_level_min"] = truth_level_min
        if project_scope:
            params["project_scope"] = project_scope
        r = await self.client.get(
            f"{self.base}/v1/memory/search",
            headers=self._headers(agent_sub=agent_sub, team_scope=team_scope),
            params=params,
        )
        r.raise_for_status()
        return r.json()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=0.3, max=3),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    async def get_system_prompt(
        self, *, agent_sub: str, team_scope: str, query: str
    ) -> dict:
        r = await self.client.get(
            f"{self.base}/v1/system-prompt",
            headers=self._headers(agent_sub=agent_sub, team_scope=team_scope),
            params={"query": query[:500]},
        )
        r.raise_for_status()
        return r.json()
