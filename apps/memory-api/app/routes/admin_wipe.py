"""/v1/admin/wipe-* — superadmin destructive operations.

Two endpoints, both superadmin-only :

  POST /v1/admin/wipe-team/{team_slug}
      Body: {"confirm": "<team_slug>"} — must exactly match the URL param.
      Deletes ONE team and ALL its scoped data : memberships, conversations,
      messages, memory items, tasks, contacts, granola integrations, team_drive
      mappings, team_messages, Qdrant points filtered by team_scope, Neo4j
      nodes with matching team_scope property. User rows are NOT deleted —
      users can rejoin other teams.

  POST /v1/admin/wipe-database
      Body: {"confirm": "WIPE EVERYTHING"} — exact string match.
      Full nuke: TRUNCATE every data table in dependency order, wipe Qdrant
      collection, clear Neo4j graph, drop LibreChat MongoDB database, clear
      the MinIO xbrain-decks bucket, then re-seed agent_definitions with the
      meeting-recap agent. The fresh audit_log gets exactly one row after
      wipe : the wipe action itself (immutable evidence).

Security :
    Both endpoints require Depends(get_current_principal) + _is_admin().
    If ADMIN_USER_SUBS is unset/empty AND the caller is a real-user token,
    _is_admin() returns False → 403. Bridge JWTs (kind=service/bridge) are
    treated as admin (same convention as admin_projects.py, admin_drive.py).

External subsystems (Qdrant, Neo4j, Mongo, MinIO) are wiped on a best-effort
basis : a failure on one external subsystem is logged + reported but does NOT
block the PG TRUNCATE pipeline. The response carries a per-subsystem status
so the operator sees what worked.
"""
from __future__ import annotations

import os
from typing import Any

import sqlalchemy as sa
import structlog
from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from app.config import settings
from app.deps import (
    _is_admin,
    _user_id_from_principal,
    get_current_principal,
    get_session,
)
from app.embedders import get_embedding_dimension

# Import assert_is_superadmin lazily — older codebases (worktree base) only
# expose _is_admin(). When the dep exists we use it as the FastAPI gate so the
# route matches the Phase 11 convention; otherwise we fall back to an inline
# _is_admin() check inside each handler.
try:
    from app.deps import assert_is_superadmin  # type: ignore[attr-defined]
    _HAVE_SUPERADMIN_DEP = True
except ImportError:  # pragma: no cover — only triggered on pre-Phase-11 envs
    _HAVE_SUPERADMIN_DEP = False

    async def assert_is_superadmin(  # type: ignore[no-redef]
        principal: dict[str, Any] = Depends(get_current_principal),
    ) -> None:
        if not _is_admin(principal):
            raise HTTPException(403, "Superadmin access required")

log = structlog.get_logger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Tables wiped on per-team operation (in dependency-safe order).
# These are tables whose rows can be filtered by team_scope (string slug) OR
# team_id (FK to teams.id).
# ---------------------------------------------------------------------------

_PER_TEAM_SCOPE_TABLES = [
    # (table, scope_column) — scope_column is the column that stores the team slug
    ("messages", "team_scope"),
    ("conversations", "team_scope"),
    ("memory_items_history", "team_scope"),
    ("memory_items", "team_scope"),
    ("neo4j_outbox", "team_scope"),
    ("promotions", "team_scope"),
    ("tasks", "team_scope"),
    ("contacts", "team_scope"),
    ("granola_integrations", "team_scope"),
    ("granola_user_connections", "team_scope"),
    ("team_drive_mappings", "team_scope"),
]

# These tables key on team_id (UUID, FK to teams.id with ON DELETE CASCADE).
# Most will be auto-wiped by the final DELETE FROM teams WHERE slug=$slug,
# but we delete them up-front so the response can report per-table counts.
_PER_TEAM_ID_TABLES = [
    ("team_messages", "team_id"),
    ("team_join_requests", "team_id"),
    ("team_org_blocks", "team_id"),
    ("team_members", "team_id"),
    ("team_api_keys", "team_id"),
]

# Tables TRUNCATE'd on full database wipe (dependency-safe order, with CASCADE).
# alembic_version is intentionally excluded (schema state).
_FULL_WIPE_TRUNCATE_TABLES = [
    # leaf tables first
    "audit_log",
    "checkpoint_blobs",
    "checkpoint_migrations",
    "checkpoint_writes",
    "checkpoints",
    "memory_items_history",
    "memory_items",
    "promotions",
    "neo4j_outbox",
    "messages",
    "conversations",
    "team_messages",
    "team_join_requests",
    "team_org_blocks",
    "team_members",
    "team_api_keys",
    "team_drive_mappings",
    "drive_watch_channels",
    "granola_integrations",
    "granola_user_connections",
    "tasks",
    "contacts",
    "mcp_tool_registry",
    "user_api_tokens",
    "user_external_sessions",
    "installations",
    "agent_definitions",
    "teams",
    "users",
]


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class WipeTeamBody(BaseModel):
    confirm: str = Field(..., min_length=2, max_length=128)

    model_config = {"extra": "ignore"}


class WipeDatabaseBody(BaseModel):
    confirm: str = Field(..., min_length=2, max_length=128)

    model_config = {"extra": "ignore"}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _table_exists(session, name: str) -> bool:
    row = (
        await session.execute(
            sa.text("SELECT to_regclass(:n) AS r"),
            {"n": f"public.{name}"},
        )
    ).fetchone()
    return bool(row and row.r is not None)


async def _delete_where(session, table: str, column: str, value: Any) -> int:
    """Best-effort DELETE — returns row count, or 0 if table missing / errored."""
    if not await _table_exists(session, table):
        return 0
    try:
        # NOTE: table/column names come from a hardcoded allow-list above,
        # never from user input — safe to interpolate.
        result = await session.execute(
            sa.text(f"DELETE FROM {table} WHERE {column} = :v"),
            {"v": value},
        )
        return int(result.rowcount or 0)
    except Exception as exc:
        log.warning("admin_wipe.delete_failed", table=table, column=column, error=str(exc))
        await session.rollback()
        return 0


async def _wipe_qdrant_team(team_slug: str) -> dict[str, Any]:
    """Delete points from Qdrant where payload.team_scope == team_slug."""
    try:
        from qdrant_client import AsyncQdrantClient
        from qdrant_client.http.models import (
            FieldCondition,
            Filter,
            FilterSelector,
            MatchValue,
        )

        client = AsyncQdrantClient(
            url=settings.QDRANT_URL,
            api_key=settings.QDRANT_API_KEY or None,
        )
        try:
            await client.delete(
                collection_name=settings.QDRANT_COLLECTION,
                points_selector=FilterSelector(
                    filter=Filter(
                        must=[
                            FieldCondition(
                                key="team_scope",
                                match=MatchValue(value=team_slug),
                            )
                        ]
                    )
                ),
            )
            return {"status": "ok"}
        finally:
            await client.close()
    except Exception as exc:
        log.warning("admin_wipe.qdrant_team_failed", error=str(exc), team=team_slug)
        return {"status": "error", "error": str(exc)}


async def _wipe_qdrant_full() -> dict[str, Any]:
    """Delete + recreate the messages collection (empty, schema preserved)."""
    try:
        from qdrant_client import AsyncQdrantClient
        from qdrant_client.http.models import Distance, PayloadSchemaType, VectorParams

        client = AsyncQdrantClient(
            url=settings.QDRANT_URL,
            api_key=settings.QDRANT_API_KEY or None,
        )
        try:
            try:
                await client.delete_collection(collection_name=settings.QDRANT_COLLECTION)
            except Exception as exc:
                log.info("admin_wipe.qdrant_delete_collection_missing", error=str(exc))
            await client.create_collection(
                collection_name=settings.QDRANT_COLLECTION,
                vectors_config=VectorParams(size=get_embedding_dimension(), distance=Distance.COSINE),
            )
            await client.create_payload_index(
                collection_name=settings.QDRANT_COLLECTION,
                field_name="team_scope",
                field_schema=PayloadSchemaType.KEYWORD,
            )
            await client.create_payload_index(
                collection_name=settings.QDRANT_COLLECTION,
                field_name="truth_level",
                field_schema=PayloadSchemaType.KEYWORD,
            )
            return {"status": "ok", "collection": "messages"}
        finally:
            await client.close()
    except Exception as exc:
        log.warning("admin_wipe.qdrant_full_failed", error=str(exc))
        return {"status": "error", "error": str(exc)}


async def _wipe_neo4j_team(team_slug: str) -> dict[str, Any]:
    """Delete Neo4j nodes whose team_scope property matches."""
    try:
        from app.neo4j_client import get_driver

        driver = get_driver()
        if driver is None:
            return {"status": "skipped", "reason": "neo4j not configured"}
        async with driver.session() as s:
            res = await s.run(
                "MATCH (n) WHERE n.team_scope = $slug DETACH DELETE n",
                {"slug": team_slug},
            )
            summary = await res.consume()
            return {"status": "ok", "nodes_deleted": summary.counters.nodes_deleted}
    except Exception as exc:
        log.warning("admin_wipe.neo4j_team_failed", error=str(exc), team=team_slug)
        return {"status": "error", "error": str(exc)}


async def _wipe_neo4j_full() -> dict[str, Any]:
    """Delete every node + relationship in Neo4j."""
    try:
        from app.neo4j_client import get_driver

        driver = get_driver()
        if driver is None:
            return {"status": "skipped", "reason": "neo4j not configured"}
        async with driver.session() as s:
            res = await s.run("MATCH (n) DETACH DELETE n")
            summary = await res.consume()
            return {"status": "ok", "nodes_deleted": summary.counters.nodes_deleted}
    except Exception as exc:
        log.warning("admin_wipe.neo4j_full_failed", error=str(exc))
        return {"status": "error", "error": str(exc)}


async def _wipe_mongo_librechat() -> dict[str, Any]:
    """Drop the LibreChat MongoDB database. URI from LIBRECHAT_MONGO_URI env."""
    mongo_uri = os.environ.get("LIBRECHAT_MONGO_URI", "")
    if not mongo_uri:
        return {"status": "skipped", "reason": "LIBRECHAT_MONGO_URI not set"}
    try:
        from urllib.parse import urlparse

        from motor.motor_asyncio import AsyncIOMotorClient  # type: ignore

        parsed = urlparse(mongo_uri)
        db_name = (parsed.path or "/LibreChat").lstrip("/") or "LibreChat"
        client = AsyncIOMotorClient(mongo_uri, serverSelectionTimeoutMS=5000)
        try:
            await client.drop_database(db_name)
            return {"status": "ok", "database": db_name}
        finally:
            client.close()
    except Exception as exc:
        log.warning("admin_wipe.mongo_librechat_failed", error=str(exc))
        return {"status": "error", "error": str(exc)}


async def _wipe_minio_decks() -> dict[str, Any]:
    """Delete every object under the xbrain-decks MinIO bucket."""
    bucket = os.environ.get("MINIO_BUCKET_DECKS", "xbrain-decks")
    endpoint = os.environ.get("MINIO_ENDPOINT", "minio:9000")
    access_key = os.environ.get("MINIO_ACCESS_KEY") or os.environ.get("MINIO_ROOT_USER") or "minio"
    secret_key = (
        os.environ.get("MINIO_SECRET_KEY")
        or os.environ.get("MINIO_ROOT_PASSWORD")
        or ""
    )
    if not secret_key:
        return {"status": "skipped", "reason": "MinIO credentials not set"}
    try:
        from minio import Minio  # type: ignore
        from minio.deleteobjects import DeleteObject  # type: ignore

        client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=False,
        )
        if not client.bucket_exists(bucket):
            return {"status": "skipped", "reason": f"bucket {bucket} does not exist"}
        objs = client.list_objects(bucket, recursive=True)
        delete_iter = (DeleteObject(o.object_name) for o in objs)
        errs = list(client.remove_objects(bucket, delete_iter))
        if errs:
            return {"status": "partial", "errors": [str(e) for e in errs[:10]]}
        return {"status": "ok", "bucket": bucket}
    except Exception as exc:
        log.warning("admin_wipe.minio_failed", error=str(exc))
        return {"status": "error", "error": str(exc)}


# Inline copy of the meeting-recap seed (mirrors alembic 0012).
_MEETING_RECAP_SYSTEM_PROMPT = """Tu es un agent de résumé de réunion. \
À partir d'un transcript ou d'un résumé brut de meeting, génère un compte-rendu \
structuré au format Markdown contenant :

## Résumé exécutif
2-3 phrases sur l'essentiel discuté.

## Participants
Liste à puces des personnes présentes (nom, et email/rôle si mentionné).

## Décisions
Liste numérotée des décisions actées pendant la réunion.

## Action items
Liste à puces : "[Assignee] — Action à entreprendre — Échéance si mentionnée".

## Points ouverts
Sujets non tranchés à reprendre plus tard.

Reste factuel — n'invente pas de participants, de décisions ou d'actions \
absentes du texte source. Si une section est vide, écris "(aucun)" sous le titre."""


async def _reseed_agent_definitions(session) -> dict[str, Any]:
    """Re-insert the meeting-recap agent. Idempotent via ON CONFLICT (name)."""
    if not await _table_exists(session, "agent_definitions"):
        return {"status": "skipped", "reason": "agent_definitions missing"}
    try:
        await session.execute(
            sa.text(
                """
                INSERT INTO agent_definitions
                    (name, description, system_prompt, model, auto_trigger, enabled)
                VALUES
                    (:name, :desc, :prompt, :model, true, true)
                ON CONFLICT (name) DO NOTHING
                """
            ),
            {
                "name": "meeting-recap",
                "desc": (
                    "Génère un résumé structuré de réunion "
                    "(résumé exécutif, participants, décisions, action items, "
                    "points ouverts) depuis un transcript Granola."
                ),
                "prompt": _MEETING_RECAP_SYSTEM_PROMPT,
                "model": "claude-haiku-4-5-20251001",
            },
        )
        return {"status": "ok", "agent": "meeting-recap"}
    except Exception as exc:
        log.warning("admin_wipe.reseed_failed", error=str(exc))
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Endpoint: per-team wipe
# ---------------------------------------------------------------------------


@router.post("/wipe-team/{team_slug}")
async def wipe_team(
    body: WipeTeamBody,
    team_slug: str = Path(..., min_length=1, max_length=64),
    session=Depends(get_session),
    principal: dict[str, Any] = Depends(get_current_principal),
    _: None = Depends(assert_is_superadmin),
):
    """Delete ONE team and ALL its scoped data. Superadmin-only.

    Body must contain confirm == team_slug (type-to-confirm).
    Users are NOT deleted — only their membership in this team. They can
    rejoin/move to another team.
    """
    if body.confirm != team_slug:
        raise HTTPException(
            400,
            "confirm body must exactly match the team_slug URL parameter",
        )

    # Verify the team exists.
    row = (
        await session.execute(
            sa.text("SELECT id, slug FROM teams WHERE slug = :slug"),
            {"slug": team_slug},
        )
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"Team '{team_slug}' not found")
    team_id = row.id

    deleted: dict[str, int] = {}

    # 1. Per-team scoped tables (FK string slug).
    for table, col in _PER_TEAM_SCOPE_TABLES:
        deleted[table] = await _delete_where(session, table, col, team_slug)

    # 2. Per-team-id tables (UUID).
    for table, col in _PER_TEAM_ID_TABLES:
        deleted[table] = await _delete_where(session, table, col, team_id)

    # 3. Final: delete the team row itself (FK conversations.team_scope is
    # already wiped above so this won't be blocked).
    try:
        result = await session.execute(
            sa.text("DELETE FROM teams WHERE slug = :slug"),
            {"slug": team_slug},
        )
        deleted["teams"] = int(result.rowcount or 0)
    except Exception as exc:
        log.warning("admin_wipe.team_row_delete_failed", error=str(exc), team=team_slug)
        deleted["teams"] = 0

    # 4. External subsystems.
    qdrant_status = await _wipe_qdrant_team(team_slug)
    neo4j_status = await _wipe_neo4j_team(team_slug)

    # 5. Audit log entry.
    actor_user_id = _user_id_from_principal(principal)
    try:
        await session.execute(
            sa.text(
                """
                INSERT INTO audit_log
                    (actor_user_id, team_scope, action, target_id, payload)
                VALUES
                    (:actor, :team_scope, :action, :target, CAST(:payload AS JSONB))
                """
            ),
            {
                "actor": actor_user_id,
                "team_scope": None,  # team no longer exists
                "action": "superadmin_wipe_team",
                "target": team_slug,
                "payload": _jsonify(
                    {
                        "team_slug": team_slug,
                        "deleted": deleted,
                        "qdrant": qdrant_status,
                        "neo4j": neo4j_status,
                    }
                ),
            },
        )
    except Exception as exc:
        log.warning("admin_wipe.audit_team_failed", error=str(exc), team=team_slug)

    await session.commit()

    log.info(
        "admin_wipe.team_done",
        team=team_slug,
        deleted=deleted,
        actor=str(actor_user_id) if actor_user_id else "bridge",
    )

    return {
        "team_slug": team_slug,
        "deleted": deleted,
        "qdrant": qdrant_status,
        "neo4j": neo4j_status,
    }


# ---------------------------------------------------------------------------
# Endpoint: full-database wipe
# ---------------------------------------------------------------------------


@router.post("/wipe-database")
async def wipe_database(
    body: WipeDatabaseBody,
    session=Depends(get_session),
    principal: dict[str, Any] = Depends(get_current_principal),
    _: None = Depends(assert_is_superadmin),
):
    """Full nuke: TRUNCATE all data tables + clear Qdrant + Neo4j + Mongo + MinIO.

    Body must contain confirm == "WIPE EVERYTHING" exactly.
    Schema (alembic_version) is preserved. agent_definitions is re-seeded
    with the meeting-recap agent after the wipe.
    """
    if body.confirm != "WIPE EVERYTHING":
        raise HTTPException(
            400,
            "confirm body must be exactly the string 'WIPE EVERYTHING'",
        )

    actor_user_id = _user_id_from_principal(principal)
    actor_sub = principal.get("sub")

    # 1. TRUNCATE PG tables (best-effort per table, with CASCADE).
    truncated: list[str] = []
    failed: list[dict[str, str]] = []
    for table in _FULL_WIPE_TRUNCATE_TABLES:
        if not await _table_exists(session, table):
            continue
        try:
            await session.execute(sa.text(f"TRUNCATE TABLE {table} CASCADE"))
            truncated.append(table)
        except Exception as exc:
            log.warning("admin_wipe.truncate_failed", table=table, error=str(exc))
            failed.append({"table": table, "error": str(exc)})
            await session.rollback()
    await session.commit()

    # 2. External subsystems.
    qdrant_status = await _wipe_qdrant_full()
    neo4j_status = await _wipe_neo4j_full()
    mongo_status = await _wipe_mongo_librechat()
    minio_status = await _wipe_minio_decks()

    # 3. Re-seed agent_definitions (idempotent).
    reseed_status = await _reseed_agent_definitions(session)
    await session.commit()

    # 4. Write the FIRST audit_log row post-wipe : the wipe action itself.
    # actor_user_id no longer references a row (users got truncated) — set NULL.
    try:
        await session.execute(
            sa.text(
                """
                INSERT INTO audit_log
                    (actor_user_id, team_scope, action, target_id, payload)
                VALUES
                    (NULL, NULL, :action, :target, CAST(:payload AS JSONB))
                """
            ),
            {
                "action": "superadmin_wipe_database",
                "target": actor_sub or "unknown",
                "payload": _jsonify(
                    {
                        "actor_sub": actor_sub,
                        "actor_user_id": str(actor_user_id) if actor_user_id else None,
                        "truncated_tables": truncated,
                        "truncate_failed": failed,
                        "qdrant": qdrant_status,
                        "neo4j": neo4j_status,
                        "librechat_mongo": mongo_status,
                        "minio_decks": minio_status,
                        "agent_definitions_reseed": reseed_status,
                    }
                ),
            },
        )
        await session.commit()
    except Exception as exc:
        log.warning("admin_wipe.audit_database_failed", error=str(exc))

    log.info(
        "admin_wipe.database_done",
        truncated_count=len(truncated),
        failed_count=len(failed),
        actor_sub=actor_sub,
    )

    return {
        "truncated_tables": truncated,
        "truncate_failed": failed,
        "qdrant": qdrant_status,
        "neo4j": neo4j_status,
        "librechat_mongo": mongo_status,
        "minio_decks": minio_status,
        "agent_definitions": reseed_status,
    }


def _jsonify(obj: Any) -> str:
    import json

    def _default(o: Any) -> Any:
        try:
            return str(o)
        except Exception:
            return repr(o)

    return json.dumps(obj, default=_default)
