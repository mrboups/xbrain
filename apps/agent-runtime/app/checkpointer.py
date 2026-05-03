"""LangGraph AsyncPostgresSaver factory — process-singleton.

Tables (`checkpoints`, `checkpoint_writes`, `checkpoint_blobs`) are created at
first boot by `setup()` and are owned by `langgraph-checkpoint-postgres`, not
by Alembic. Migration 0003 is a deliberate no-op.
"""

from __future__ import annotations

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

from app.config import settings

_pool: AsyncConnectionPool | None = None
_saver: AsyncPostgresSaver | None = None


def _normalize_dsn(url: str) -> str:
    """Strip SQLAlchemy driver prefix — psycopg wants a plain libpq URL."""
    return (
        url.replace("postgresql+asyncpg://", "postgresql://")
        .replace("postgresql+psycopg://", "postgresql://")
    )


async def get_checkpointer() -> AsyncPostgresSaver:
    """Return the process-wide AsyncPostgresSaver, lazily initializing the pool."""
    global _pool, _saver
    if _saver is not None:
        return _saver
    dsn = _normalize_dsn(settings.DATABASE_URL)
    _pool = AsyncConnectionPool(
        dsn, min_size=1, max_size=5, kwargs={"autocommit": True}, open=False
    )
    await _pool.open()
    _saver = AsyncPostgresSaver(_pool)
    await _saver.setup()  # idempotent — creates checkpoint tables on first run
    return _saver


async def close_checkpointer() -> None:
    global _pool, _saver
    if _pool is not None:
        await _pool.close()
    _pool = None
    _saver = None
