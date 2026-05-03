"""Embedding wrappers for backends that need explicit embedding."""

from openai import AsyncOpenAI

from app.config import settings

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        if not settings.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY not configured for embeddings")
        _client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


async def openai_embedder(text: str) -> list[float]:
    """Embed text via OpenAI's text-embedding-3-small (1536 dims)."""
    client = _get_client()
    r = await client.embeddings.create(
        model=settings.OPENAI_EMBEDDING_MODEL,
        input=text,
    )
    return r.data[0].embedding
