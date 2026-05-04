"""DB-backed tool registry — asyncpg, table mcp_tool_registry.

Note: the mcp_tool_registry table is NOT in a memory-api Alembic migration.
It is created at startup by mcp-gateway via CREATE TABLE IF NOT EXISTS.
This is a deliberate choice: mcp-gateway owns its own table in the shared Postgres DB.
This keeps mcp-gateway self-contained and avoids coupling to memory-api's migration timeline.
"""
from __future__ import annotations

import asyncpg
import structlog

log = structlog.get_logger(__name__)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS mcp_tool_registry (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tool_name TEXT NOT NULL UNIQUE,
    sidecar_url TEXT NOT NULL,
    description TEXT,
    enabled BOOLEAN NOT NULL DEFAULT true,
    registered_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
)
"""

_pool: asyncpg.Pool | None = None


async def init_registry(database_url: str) -> None:
    global _pool
    # asyncpg uses postgresql:// scheme (not postgresql+asyncpg://)
    url = database_url.replace("postgresql+asyncpg://", "postgresql://")
    _pool = await asyncpg.create_pool(url, min_size=1, max_size=3)
    async with _pool.acquire() as conn:
        await conn.execute(CREATE_TABLE_SQL)
    log.info("registry.initialized")


async def close_registry() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def list_tools() -> list[dict]:
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT tool_name, sidecar_url, description FROM mcp_tool_registry "
            "WHERE enabled=true ORDER BY tool_name"
        )
        return [dict(r) for r in rows]


async def get_tool(tool_name: str) -> dict | None:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT tool_name, sidecar_url FROM mcp_tool_registry "
            "WHERE tool_name=$1 AND enabled=true",
            tool_name,
        )
        return dict(row) if row else None


async def register_tool(tool_name: str, sidecar_url: str, description: str = "") -> dict:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO mcp_tool_registry(tool_name, sidecar_url, description)
               VALUES($1, $2, $3)
               ON CONFLICT(tool_name) DO UPDATE
               SET sidecar_url=EXCLUDED.sidecar_url,
                   description=EXCLUDED.description,
                   updated_at=now()
               RETURNING tool_name, sidecar_url, description""",
            tool_name,
            sidecar_url,
            description,
        )
        return dict(row)
