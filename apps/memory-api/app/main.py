"""FastAPI app entrypoint with lifespan startup."""

import asyncio
import logging
from contextlib import asynccontextmanager

import structlog
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.neo4j_client import close_driver, init_driver, reconnect_loop
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

    # Init Neo4j driver (graceful degrade if NEO4J_URI/NEO4J_PASSWORD not set or unreachable)
    await init_driver()

    # Phase 15: keep retrying in the BACKGROUND if that first attempt failed. memory-api no longer
    # `depends_on` neo4j (Compose forbids an untagged service depending on a profile-tagged one), so on
    # a cold `--profile integrations up` we can legitimately start before Neo4j is healthy. Bounded and
    # non-blocking: /v1/healthz answers 200 throughout, and an OSS-light install gives up quietly.
    _reconnect_task = asyncio.create_task(reconnect_loop())

    # Start outbox worker background task (drains neo4j_outbox every 2s)
    _outbox_task = asyncio.create_task(drain_outbox(settings.DATABASE_URL))

    yield

    # Clean shutdown: cancel workers, then close the Neo4j driver
    _reconnect_task.cancel()
    _outbox_task.cancel()
    for _t in (_reconnect_task, _outbox_task):
        try:
            await _t
        except asyncio.CancelledError:
            pass
    await close_driver()
    log.info("memory_api_shutdown")


# === Edition-gated router registry (Phase 15, EDIT-02) ===
# D-15-05: gating is ADDITIVE and EXPLICIT. Every router module under app/routes/ appears in
# EXACTLY ONE of the two lists below. A router in neither is a bug — tests/test_edition_gating.py
# ::test_every_router_module_is_classified FAILS until it is classified. That test is the whole
# point: "a router that is forgotten defaults to mounted", so forgetting one must be loud.
#
# The default is CORE. No product feature is paywalled (locked decision Q6 — the paid self-host
# tier and its Ed25519 license were dropped, requirement EDIT-03 is cancelled). The ONLY routers
# withheld from an OSS install are the ones that are meaningless without the HOSTED CONTROL PLANE.
CORE_ROUTERS: list[tuple[APIRouter, str, list[str]]] = [
    (health.router, "/v1", ["health"]),
    (internal.router, "/v1", ["internal"]),
    (internal_github.router, "/v1", ["internal-github"]),
    (me.router, "/v1", ["me"]),
    (me_github.router, "/v1", ["me"]),
    (auth_github.router, "/v1", ["auth"]),
    (teams.router, "/v1", ["teams"]),
    (conversations.router, "/v1", ["conversations"]),
    (messages.router, "/v1", ["messages"]),
    (audit.router, "/v1", ["audit"]),
    (media.router, "/v1", ["media"]),
    (memory.router, "/v1", ["memory"]),
    (promotions.router, "/v1", ["promotions"]),
    (graph.router, "/v1", ["graph"]),
    (system_prompt.router, "/v1", ["system-prompt"]),
    (admin_drive.router, "/v1", ["admin-drive"]),
    (admin_projects.router, "/v1/admin", ["admin"]),
    (crm.router, "/v1", ["crm"]),
    (tasks.router, "/v1", ["tasks"]),
    (drive_webhook.router, "/v1", ["drive-webhook"]),
    (github_repos.router, "/v1", ["github"]),
    (granola_integration.router, "/v1", ["granola"]),
    (agents.router, "/v1", ["agents"]),
    (team_chat.router, "/v1", ["team-chat"]),
    (brain.router, "/v1", ["brain"]),
    (admin_brain.router, "/v1", ["admin-brain"]),
    (admin_wipe.router, "/v1/admin", ["admin-wipe"]),
    (webhooks_github.router, "/v1/webhooks/github", ["webhooks-github"]),
    # OAuth 2.1 Authorization Server (quick-260604-glo) — NO prefix, so the public paths are exactly
    # /.well-known/oauth-authorization-server and /oauth/{register,introspect,authorize,token}.
    # This is the Claude.ai / ChatGPT-web Custom Connector surface. ROADMAP SC#2 names the
    # ChatGPT-web connector as CORE explicitly — it ships in every edition, gated by nothing.
    (oauth_metadata.router, "", ["oauth"]),
    (oauth_register.router, "", ["oauth"]),
    (oauth_introspect.router, "", ["oauth"]),
    (oauth_authorize.router, "", ["oauth"]),
    (oauth_token.router, "", ["oauth"]),
]

# SAAS-ONLY — mounted ONLY when EDITION == "saas". Exactly two, and they are the total leak surface:
#   waitlist          — proxies the HOSTED landing page's signup to Resend. Needs RESEND_API_KEY and a
#                       marketing site; a self-hoster has no waitlist.
#   external_sessions — the Phase-9 session-bridge registry. Its only writer is the `session-bridge`
#                       container, which lives in the `saas` compose profile.
# COUPLING: if COMPOSE_PROFILES includes `saas`, EDITION MUST be `saas` — session-bridge POSTs
# /v1/me/external-sessions and would get a silent 404 otherwise. preflight-env.sh rejects that
# combination at deploy time (plan 15-04).
SAAS_ONLY_ROUTERS: list[tuple[APIRouter, str, list[str]]] = [
    (waitlist.router, "/v1", ["waitlist"]),
    (external_sessions.router, "/v1", ["external-sessions"]),
]


def create_app(edition: str | None = None) -> FastAPI:
    """Build the FastAPI app for a given edition. `edition=None` reads settings.EDITION.

    A factory (rather than a module-scope app) so tests can construct BOTH editions in one process
    and diff their route tables — which is what proves D-15-05's "one image, no rebuild".
    """
    ed = edition if edition is not None else settings.EDITION
    application = FastAPI(title="xbrain memory-api", version="0.1.0", lifespan=lifespan)

    # CORS — browser clients (Chrome extension, web app) call this API cross-origin.
    # Allowed origins come from CORS_ALLOWED_ORIGIN_REGEX (.env), never a hardcoded domain.
    # Bearer-token auth remains the real access control (T-05-04-03 accepted).
    application.add_middleware(
        CORSMiddleware,
        allow_origin_regex=settings.CORS_ALLOWED_ORIGIN_REGEX,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "X-Team-Scope", "Content-Type", "Accept"],
    )

    for router, prefix, tags in CORE_ROUTERS:
        application.include_router(router, prefix=prefix, tags=tags)

    if ed == "saas":
        for router, prefix, tags in SAAS_ONLY_ROUTERS:
            application.include_router(router, prefix=prefix, tags=tags)

    return application


app = create_app()
