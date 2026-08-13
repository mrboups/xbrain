"""/v1/admin/projects — endpoint pour enregistrer et lister les projets déployés.

Flow :
  1. brain-index.sh appelle POST /v1/admin/projects après un deploy réussi.
  2. L'endpoint stocke le projet dans memory_items avec source="admin:project".
  3. GET /v1/admin/projects?team_scope=acme retourne la liste des projets.

Stockage :
  Utilise la table memory_items existante (migration 0002) avec :
    - source = "admin:project"
    - content = "projet:<slug>" (identifiant unique)
    - metadata (JSONB) = {url, deploy_target, type, name}
    - team_scope, project_scope = champs stricts du contrat de tagging
    - truth_level = "CANONICAL" (les projets déployés sont des faits établis)
  Pas de migration Alembic supplémentaire requise.

Sécurité :
  POST est admin-only (Bridge JWT kind=service/bridge ou sub dans ADMIN_USER_SUBS).
  GET exige d'être membre (non bloqué) de la team demandée — ou admin/bridge.
  T-05-03-01 mitigé : guard _is_admin copié depuis admin_drive.py.
"""
from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.deps import get_current_principal, get_session
from app.repos.teams import get_membership

log = structlog.get_logger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Admin guard (copié de admin_drive.py — T-05-03-01 mitigated)
# ---------------------------------------------------------------------------


def _is_admin(principal: dict[str, Any]) -> bool:
    """Return True for bridge service JWTs (kind=service/bridge) or listed admin subs.

    Bridge tokens (kind='bridge') are emitted by internal services and implicitly
    trusted as admin for configuration endpoints. Real-user tokens (kind='user')
    require the caller's sub to appear in ADMIN_USER_SUBS.
    """
    from app.config import settings

    if principal.get("kind") in ("service", "bridge"):
        return True
    sub = principal.get("sub", "")
    admin_subs = [s.strip() for s in (settings.ADMIN_USER_SUBS or "").split(",") if s.strip()]
    return sub in admin_subs


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

_VALID_DEPLOY_TARGETS = {"firebase", "cloudrun"}
_VALID_TYPES = {"static", "service", "mcp_tool"}


class ProjectBody(BaseModel):
    slug: str = Field(..., min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(..., min_length=1, max_length=128)
    url: str = Field(..., min_length=1, max_length=256)
    team_scope: str = Field(..., min_length=1, max_length=64)
    project_scope: str = Field(..., min_length=1, max_length=64)
    deploy_target: str = Field(...)
    type: str = Field(...)

    model_config = {"extra": "ignore"}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/projects", status_code=201)
async def create_project(
    body: ProjectBody,
    session=Depends(get_session),
    principal: dict[str, Any] = Depends(get_current_principal),
):
    """Enregistre ou met à jour un projet déployé dans la registry xbrain.

    Appelé par brain-index.sh après un deploy réussi (GitHub Actions).
    Stocke le projet dans memory_items avec source='admin:project'.
    Idempotent : deux appels avec le même slug + team_scope écrasent le précédent.

    Auth : Bridge JWT (admin-only) — T-05-03-01 mitigated.
    """
    if not _is_admin(principal):
        raise HTTPException(403, "Admin access required")

    if body.deploy_target not in _VALID_DEPLOY_TARGETS:
        raise HTTPException(
            422,
            f"deploy_target must be one of {sorted(_VALID_DEPLOY_TARGETS)}, got '{body.deploy_target}'",
        )
    if body.type not in _VALID_TYPES:
        raise HTTPException(
            422,
            f"type must be one of {sorted(_VALID_TYPES)}, got '{body.type}'",
        )

    # Identifier unique : "project:<team_scope>:<slug>"
    content_key = f"project:{body.team_scope}:{body.slug}"

    metadata = json.dumps(
        {
            "url": body.url,
            "deploy_target": body.deploy_target,
            "type": body.type,
            "name": body.name,
            "slug": body.slug,
        }
    )

    # Upsert dans memory_items — ON CONFLICT sur (team_scope, source, content)
    # Comme memory_items n'a pas de UNIQUE(team_scope, source, content), on utilise
    # DELETE + INSERT pour rester simple (fréquence faible : 1 appel par deploy).
    # Une alternative propre serait une migration Alembic 0008 avec UNIQUE(source, content)
    # mais cela est déféré pour garder ce plan sans migration DB.
    result = await session.execute(
        sa.text(
            """
            INSERT INTO memory_items
                (team_scope, project_scope, content, metadata, visibility,
                 truth_level, validation_status, confidence, source)
            VALUES
                (:team_scope, :project_scope, :content, :metadata::jsonb, 'team',
                 'CANONICAL', 'validated', 1.0, 'admin:project')
            ON CONFLICT DO NOTHING
            RETURNING id
            """
        ),
        {
            "team_scope": body.team_scope,
            "project_scope": body.project_scope,
            "content": content_key,
            "metadata": metadata,
        },
    )
    row = result.fetchone()

    if row is None:
        # Ligne déjà existante (ON CONFLICT DO NOTHING) → mettre à jour metadata
        await session.execute(
            sa.text(
                """
                UPDATE memory_items
                SET metadata = :metadata::jsonb,
                    updated_at = now()
                WHERE team_scope = :team_scope
                  AND source = 'admin:project'
                  AND content = :content
                """
            ),
            {
                "team_scope": body.team_scope,
                "content": content_key,
                "metadata": metadata,
            },
        )
        await session.commit()
        action = "updated"
    else:
        await session.commit()
        action = "created"

    log.info(
        "admin_project.upserted",
        action=action,
        slug=body.slug,
        team=body.team_scope,
        deploy_target=body.deploy_target,
    )

    return {
        "slug": body.slug,
        "name": body.name,
        "url": body.url,
        "team_scope": body.team_scope,
        "project_scope": body.project_scope,
        "deploy_target": body.deploy_target,
        "type": body.type,
        "action": action,
    }


@router.get("/projects")
async def list_projects(
    team_scope: str = Query(..., max_length=64),
    session=Depends(get_session),
    principal: dict[str, Any] = Depends(get_current_principal),
):
    """Liste les projets déployés pour une team.

    Auth : membre (non bloqué) de `team_scope`, ou admin/bridge.
    Retourne une liste de projets avec leurs URL, deploy_target et type.
    """
    # The `team_scope` query param used to go straight into the WHERE clause
    # below, with a comment calling a team's deploy registry "non-sensitive".
    # It is the list of every internal URL a team ships, and the param is
    # client-supplied: any authenticated account could read any team's by
    # guessing a slug. Membership is not an admin restriction, it is the
    # difference between "your team's projects" and "everyone's".
    #
    # POST on this same router (:84) was already admin-gated — the read was the
    # half nobody closed.
    if not _is_admin(principal):
        user = principal.get("user")
        if user is None:
            raise HTTPException(403, "not a member of this team")
        pinned = principal.get("api_token_team_scope")
        if pinned and pinned != team_scope:
            raise HTTPException(403, "API token team_scope mismatch")
        membership = await get_membership(
            session, user_id=user.id, team_slug=team_scope
        )
        if membership is None or membership.blocked_at is not None:
            raise HTTPException(403, "not a member of this team")

    # Phase 11 (BMO-07) — exclude soft-deleted admin:project rows from the
    # listing. If an admin removes a project via the brain monitor (sets
    # deleted_at via PATCH/DELETE on the memory_item), it should disappear
    # from this listing immediately rather than waiting for the 30-day
    # janitor purge.
    rows = await session.execute(
        sa.text(
            """
            SELECT content, metadata, project_scope, created_at, updated_at
            FROM memory_items
            WHERE team_scope = :team_scope
              AND source = 'admin:project'
              AND deleted_at IS NULL
            ORDER BY created_at DESC
            """
        ),
        {"team_scope": team_scope},
    )
    results = rows.fetchall()

    projects = []
    for r in results:
        try:
            meta = r.metadata if isinstance(r.metadata, dict) else json.loads(r.metadata or "{}")
        except (json.JSONDecodeError, TypeError):
            meta = {}

        projects.append(
            {
                "slug": meta.get("slug") or r.content.split(":")[-1],
                "name": meta.get("name", ""),
                "url": meta.get("url", ""),
                "team_scope": team_scope,
                "project_scope": r.project_scope,
                "deploy_target": meta.get("deploy_target", ""),
                "type": meta.get("type", ""),
                "created_at": str(r.created_at) if r.created_at else None,
                "updated_at": str(r.updated_at) if r.updated_at else None,
            }
        )

    return projects
