"""FastAPI app entrypoint with lifespan startup."""

import asyncio
import logging
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.neo4j_client import close_driver, init_driver
from app.outbox_worker import drain_outbox
from app.qdrant_setup import ensure_collections
from app.routes import (
    admin_brain,
    admin_drive,
    admin_projects,
    admin_wipe,
    agents,
    audit,
    auth_github,
    brain,
    conversations,
    crm,
    drive_webhook,
    external_sessions,
    github_repos,
    granola_integration,
    graph,
    health,
    internal,
    internal_github,
    me,
    me_github,
    media,
    memory,
    messages,
    oauth_authorize,
    oauth_introspect,
    oauth_metadata,
    oauth_register,
    oauth_token,
    promotions,
    system_prompt,
    tasks,
    team_chat,
    teams,
    waitlist,
    webhooks_github,
)

logging.basicConfig(level=settings.LOG_LEVEL)
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log = structlog.get_logger()
    log.info("memory_api_startup")
    try:
        await ensure_collections()
    except Exception as e:
        log.warning("qdrant_setup_skipped", err=str(e))

    # Init Neo4j driver (graceful degrade if NEO4J_URI/NEO4J_PASSWORD not set)
    await init_driver()

    # Start outbox worker background task (drains neo4j_outbox every 2s)
    _outbox_task = asyncio.create_task(drain_outbox(settings.DATABASE_URL))

    yield

    # Clean shutdown: cancel worker, then close Neo4j driver
    _outbox_task.cancel()
    try:
        await _outbox_task
    except asyncio.CancelledError:
        pass
    await close_driver()
    log.info("memory_api_shutdown")


app = FastAPI(title="xbrain memory-api", version="0.1.0", lifespan=lifespan)

# CORS — browser clients (Chrome extension, web app) call this API cross-origin.
# Allowed origins come from CORS_ALLOWED_ORIGIN_REGEX (.env), never a hardcoded
# domain. Bearer-token auth remains the real access control (T-05-04-03 accepted).
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=settings.CORS_ALLOWED_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "X-Team-Scope", "Content-Type", "Accept"],
)

app.include_router(health.router, prefix="/v1", tags=["health"])
app.include_router(internal.router, prefix="/v1", tags=["internal"])
app.include_router(internal_github.router, prefix="/v1", tags=["internal-github"])
app.include_router(me.router, prefix="/v1", tags=["me"])
app.include_router(me_github.router, prefix="/v1", tags=["me"])
app.include_router(auth_github.router, prefix="/v1", tags=["auth"])
app.include_router(teams.router, prefix="/v1", tags=["teams"])
app.include_router(conversations.router, prefix="/v1", tags=["conversations"])
app.include_router(messages.router, prefix="/v1", tags=["messages"])
app.include_router(audit.router, prefix="/v1", tags=["audit"])
app.include_router(media.router, prefix="/v1", tags=["media"])
app.include_router(memory.router, prefix="/v1", tags=["memory"])
app.include_router(promotions.router, prefix="/v1", tags=["promotions"])
app.include_router(graph.router, prefix="/v1", tags=["graph"])
app.include_router(system_prompt.router, prefix="/v1", tags=["system-prompt"])
app.include_router(admin_drive.router, prefix="/v1", tags=["admin-drive"])
app.include_router(admin_projects.router, prefix="/v1/admin", tags=["admin"])
app.include_router(crm.router, prefix="/v1", tags=["crm"])
app.include_router(tasks.router, prefix="/v1", tags=["tasks"])
app.include_router(drive_webhook.router, prefix="/v1", tags=["drive-webhook"])
app.include_router(github_repos.router, prefix="/v1", tags=["github"])
app.include_router(granola_integration.router, prefix="/v1", tags=["granola"])
app.include_router(waitlist.router, prefix="/v1", tags=["waitlist"])
app.include_router(agents.router, prefix="/v1", tags=["agents"])
app.include_router(external_sessions.router, prefix="/v1", tags=["external-sessions"])
app.include_router(team_chat.router, prefix="/v1", tags=["team-chat"])
app.include_router(brain.router, prefix="/v1", tags=["brain"])
app.include_router(admin_brain.router, prefix="/v1", tags=["admin-brain"])
app.include_router(admin_wipe.router, prefix="/v1/admin", tags=["admin-wipe"])
app.include_router(
    webhooks_github.router,
    prefix="/v1/webhooks/github",
    tags=["webhooks-github"],
)

# === OAuth 2.1 Authorization Server (quick-260604-glo) ===
# Mounted with NO prefix so the public paths are exactly
# /.well-known/oauth-authorization-server and /oauth/{register,introspect,...}.
# Claude.ai's Custom Connector discovers + drives these from browser context;
# CORS for those origins is owned by the CORSMiddleware above (claude.ai is in
# allow_origin_regex), NOT by nginx.
app.include_router(oauth_metadata.router, tags=["oauth"])
app.include_router(oauth_register.router, tags=["oauth"])
app.include_router(oauth_introspect.router, tags=["oauth"])
app.include_router(oauth_authorize.router, tags=["oauth"])
app.include_router(oauth_token.router, tags=["oauth"])
