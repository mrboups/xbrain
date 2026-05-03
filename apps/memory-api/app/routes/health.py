"""Liveness + readiness endpoints."""

from fastapi import APIRouter
from qdrant_client import AsyncQdrantClient
from sqlalchemy import text

from app.config import settings
from app.db.session import async_session_factory

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness probe — always returns 200 if the process is up."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz() -> dict:
    """Readiness probe — checks that DB + Qdrant are reachable."""
    deps: dict[str, str] = {}
    overall_ok = True

    # Postgres
    try:
        async with async_session_factory() as s:
            await s.execute(text("SELECT 1"))
        deps["postgres"] = "ok"
    except Exception as e:
        deps["postgres"] = f"fail: {type(e).__name__}"
        overall_ok = False

    # Qdrant
    qdrant = AsyncQdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY or None)
    try:
        await qdrant.get_collections()
        deps["qdrant"] = "ok"
    except Exception as e:
        deps["qdrant"] = f"fail: {type(e).__name__}"
        overall_ok = False
    finally:
        await qdrant.close()

    body = {"status": "ok" if overall_ok else "degraded", "deps": deps}
    if not overall_ok:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=503, content=body)
    return body
