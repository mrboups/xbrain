# Phase 7: CRM + Granola + Task Intelligence - Pattern Map

**Mapped:** 2026-05-07
**Files analyzed:** 14 new/modified files
**Analogs found:** 13 / 14

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `apps/granola-sync/app/main.py` | service | event-driven/poll | `apps/drive-sync/app/main.py` | exact |
| `apps/granola-sync/app/config.py` | config | — | `apps/drive-sync/app/config.py` | exact |
| `apps/granola-sync/app/granola_poller.py` | service | poll → transform | `apps/drive-sync/app/drive_poller.py` | exact |
| `apps/granola-sync/app/memory_client.py` | utility | request-response | `apps/drive-sync/app/ingestion_client.py` | exact |
| `apps/granola-sync/Dockerfile` | config | — | `apps/drive-sync/Dockerfile` | exact |
| `apps/granola-sync/pyproject.toml` | config | — | `apps/drive-sync/pyproject.toml` | exact |
| `apps/memory-api/alembic/versions/0008_team_plan.py` | migration | CRUD | `apps/memory-api/alembic/versions/0007_github_users.py` | exact |
| `apps/memory-api/alembic/versions/0009_crm_contacts.py` | migration | CRUD | `apps/memory-api/alembic/versions/0006_drive_watch_channels.py` | exact |
| `apps/memory-api/alembic/versions/0010_tasks.py` | migration | CRUD | `apps/memory-api/alembic/versions/0006_drive_watch_channels.py` | exact |
| `apps/memory-api/app/routes/crm.py` | router | CRUD | `apps/memory-api/app/routes/teams.py` | exact |
| `apps/memory-api/app/routes/tasks.py` | router | CRUD | `apps/memory-api/app/routes/teams.py` | exact |
| `apps/memory-api/app/routes/granola_integration.py` | router | request-response | `apps/memory-api/app/routes/drive_webhook.py` + `admin_drive.py` | role-match |
| `apps/memory-api/app/main.py` (modified) | config | — | itself | exact |
| `apps/memory-api/app/config.py` (modified) | config | — | itself | exact |
| `apps/memory-api/app/deps.py` (modified) | middleware | request-response | itself | exact |
| `infrastructure/nginx/conf.d/10-xbrain.conf` (modified) | config | — | itself | exact |
| `infrastructure/docker-compose.yml` (modified) | config | — | drive-sync service block | exact |
| `apps/memory-api/app/routes/memory.py` (modified) | router | CRUD | itself | exact |
| Dashboard tasks page (`tasks/index.html` on Firebase) | component | request-response | No analog in repo — static HTML | no analog |

---

## Pattern Assignments

### `apps/granola-sync/app/main.py` (service entry point, poll loop)

**Analog:** `apps/drive-sync/app/main.py`

**Full file to copy from** (lines 1-33):
```python
"""drive-sync entry point -- runs the polling loop and webhook server concurrently."""
import asyncio

import structlog
import uvicorn

from app.config import settings
from app.drive_poller import run_poll_loop
from app.webhook_server import webhook_app

log = structlog.get_logger(__name__)


async def main():
    """Start webhook server (port 8200) and poll loop concurrently."""
    webhook_config = uvicorn.Config(
        webhook_app,
        host="0.0.0.0",
        port=8200,
        log_level=settings.LOG_LEVEL.lower(),
    )
    webhook_server = uvicorn.Server(webhook_config)

    log.info("drive_sync.boot", poll_interval=settings.POLL_INTERVAL_SECONDS)
    await asyncio.gather(
        webhook_server.serve(),
        run_poll_loop(settings.DATABASE_URL),
    )


if __name__ == "__main__":
    asyncio.run(main())
```

**Adaptation for granola-sync:** Remove the `webhook_server` — Granola has no webhooks. Replace `asyncio.gather(webhook_server.serve(), run_poll_loop(...))` with a plain `await run_poll_loop(settings.DATABASE_URL)`. No uvicorn server needed.

```python
"""granola-sync entry point -- runs the polling loop only (Granola has no webhooks)."""
import asyncio
import structlog
from app.config import settings
from app.granola_poller import run_poll_loop

log = structlog.get_logger(__name__)

async def main():
    log.info("granola_sync.boot", poll_interval=settings.GRANOLA_POLL_INTERVAL_SECONDS)
    await run_poll_loop(settings.DATABASE_URL)

if __name__ == "__main__":
    asyncio.run(main())
```

---

### `apps/granola-sync/app/config.py` (settings)

**Analog:** `apps/drive-sync/app/config.py` (lines 1-27)

```python
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = ""
    MEMORY_API_URL: str = "http://memory-api:8000"
    AGENT_RUNTIME_URL: str = "http://agent-runtime:9100"
    BRIDGE_SHARED_SECRET: str = ""
    JWT_ALGORITHM: str = "HS256"
    POLL_INTERVAL_SECONDS: int = 300  # 5 minutes
    LOG_LEVEL: str = "INFO"
    # Drive push webhook settings
    DRIVE_WEBHOOK_PUBLIC_URL: str = ""
    DRIVE_WEBHOOK_SECRET: str = ""

    class Config:
        env_file = ".env"

settings = Settings()
```

**Adaptation:** Replace Drive-specific fields with:
- `GRANOLA_POLL_INTERVAL_SECONDS: int = 300`
- `ANTHROPIC_API_KEY: str = ""` (for Claude extraction)
- `FERNET_KEY: str = ""` (same pattern as `OAUTH_CREDENTIALS_ENCRYPTION_KEY` in drive-sync — for encrypting Granola API keys at rest)
- Remove `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `DRIVE_WEBHOOK_*`

---

### `apps/granola-sync/app/granola_poller.py` (poll loop, transform, ingest)

**Analog:** `apps/drive-sync/app/drive_poller.py`

**Poll loop skeleton** (lines 361-447 of drive_poller.py):
```python
async def run_poll_loop(database_url: str) -> None:
    pg_url = database_url.replace("postgresql+asyncpg://", "postgresql://")...
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    log.info("poll_loop.started", interval=settings.POLL_INTERVAL_SECONDS)

    while True:
        try:
            async with pool.acquire() as conn:
                mappings = await conn.fetch(
                    "SELECT id, team_scope, ..."
                    " FROM team_drive_mappings"
                )
                for row in mappings:
                    try:
                        await poll_team(conn, row)
                    except Exception as exc:
                        log.error("poll.team_error", team=row["team_scope"], error=str(exc))
            pathlib.Path("/tmp/drive-sync-alive").touch()  # sentinel for healthcheck
        except Exception as exc:
            log.error("poll_loop.error", error=str(exc))

        await asyncio.sleep(settings.POLL_INTERVAL_SECONDS)
```

**Adaptation for granola_poller.py:**
- Query table `granola_integrations` instead of `team_drive_mappings`
- Replace `pathlib.Path("/tmp/drive-sync-alive")` with `/tmp/granola-sync-alive`
- Per-team function: decrypt API key (Fernet, same as `_decrypt_credentials`), call `GET /v1/notes?created_after=<last_polled_at>`, parse each note, call Claude for extraction, POST to memory-api

**Fernet decrypt pattern** (lines 46-49 of drive_poller.py):
```python
def _decrypt_credentials(enc: str) -> dict:
    """Decrypt Fernet-encrypted OAuth credentials. Key must be set via env var."""
    f = Fernet(settings.OAUTH_CREDENTIALS_ENCRYPTION_KEY.encode())
    return json.loads(f.decrypt(enc.encode()).decode())
```

**Adaptation:** Replace `OAUTH_CREDENTIALS_ENCRYPTION_KEY` with `FERNET_KEY`. Return the raw API key string rather than a dict.

**Token persist before processing** (lines 193-200 of drive_poller.py):
```python
# CRITICAL: Persist token BEFORE processing -- idempotent on crash restart (RISK-04)
if new_token:
    await conn.execute(
        "UPDATE team_drive_mappings SET change_token=$1, updated_at=now() WHERE id=$2",
        new_token,
        mapping_id,
    )
```

**Adaptation:** Replace with:
```python
await conn.execute(
    "UPDATE granola_integrations SET last_polled_at=now() WHERE id=$1",
    integration_id,
)
```
Persist `last_polled_at` BEFORE processing notes to ensure idempotency on crash.

**Error backoff pattern** (lines 110-124 of drive_poller.py):
```python
def _with_backoff(fn, max_retries: int = 5) -> Any:
    for n in range(max_retries):
        try:
            return fn()
        except HttpError as exc:
            if exc.resp.status not in (429, 500, 503):
                raise
            wait = min(2**n + random.random(), 64)
            log.warning("poll.backoff", ...)
            time.sleep(wait)
    raise RuntimeError("Drive API: max retries exceeded")
```

**Adaptation:** Granola uses rate limit 5 req/s. Replace `HttpError` with `httpx.HTTPStatusError`, check status 429. Use `await asyncio.sleep(wait)` (async) instead of sync `time.sleep`.

---

### `apps/granola-sync/app/memory_client.py` (HTTP client to memory-api)

**Analog:** `apps/drive-sync/app/ingestion_client.py`

**Bridge JWT generation** (lines 18-29):
```python
def _make_bridge_jwt() -> str:
    """Generate a short-lived bridge service JWT."""
    payload = {
        "sub": "drive-sync",
        "scope": "bridge",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    return jose_jwt.encode(
        {"alg": settings.JWT_ALGORITHM},
        payload,
        settings.BRIDGE_SHARED_SECRET,
    ).decode()
```

**Adaptation:** Change `"sub": "drive-sync"` to `"sub": "granola-sync"`.

**POST to ingest endpoint** (lines 33-84):
```python
async def send_to_ingestion_agent(
    text: str,
    file_id: str,
    file_name: str,
    team_scope: str,
    project_scope: str | None = None,
) -> str | None:
    token = _make_bridge_jwt()
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{settings.AGENT_RUNTIME_URL}/v1/agents/ingest",
                json={...},
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Team-Scope": team_scope,
                },
            )
        ...
    except Exception as exc:
        log.error("ingestion.exception", file_id=file_id, error=str(exc))
        return None
```

**Adaptation for memory_client.py:** Replace `AGENT_RUNTIME_URL` with `MEMORY_API_URL`. The granola-sync posts directly to `POST /v1/integrations/granola/ingest` (memory-api), not to agent-runtime. Same bridge JWT auth + `X-Team-Scope` header pattern.

---

### `apps/granola-sync/Dockerfile`

**Analog:** `apps/drive-sync/Dockerfile` (lines 1-14):
```dockerfile
# Build context: REPO ROOT (not apps/drive-sync) -- needed for packages/memory-models
FROM python:3.12-slim
WORKDIR /app
COPY packages/memory-models/ ./packages/memory-models/
RUN pip install --no-cache-dir -e packages/memory-models/
COPY apps/drive-sync/pyproject.toml ./apps/drive-sync/pyproject.toml
RUN pip install --no-cache-dir -e apps/drive-sync/
COPY apps/drive-sync/app/ ./apps/drive-sync/app/
WORKDIR /app/apps/drive-sync
CMD ["python", "-m", "app.main"]
```

**Adaptation:** Replace `drive-sync` with `granola-sync` throughout. Same repo-root build context for `packages/memory-models`.

---

### `apps/granola-sync/pyproject.toml`

**Analog:** `apps/drive-sync/pyproject.toml` (lines 1-19):
```toml
[project]
name = "drive-sync"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "google-api-python-client>=2.195.0",
    "google-auth-oauthlib>=1.3.1",
    "google-auth-httplib2>=0.2.0",
    "httpx>=0.28.0",
    "asyncpg>=0.30.0",
    "structlog>=25.0.0",
    "pypdf>=5.0.0",
    "cryptography>=42.0.0",
    "authlib>=1.3.0",
    "pydantic-settings>=2.6",
    "fastapi>=0.115.0",
    "uvicorn>=0.32.0",
    "xbrain-memory",
]
```

**Adaptation:** Remove Google API packages, `pypdf`, `fastapi`, `uvicorn` (no webhook server). Add `anthropic>=0.50.0` (Claude extraction). Keep `httpx`, `asyncpg`, `structlog`, `cryptography`, `authlib`, `pydantic-settings`.

---

### `apps/memory-api/alembic/versions/0008_team_plan.py` (migration, add column)

**Analog:** `apps/memory-api/alembic/versions/0007_github_users.py`

**Header + revision chain pattern** (lines 1-27):
```python
"""github_users — add github_username + github_id to users table

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("github_username", sa.String(256), nullable=True))
    op.add_column("users", sa.Column("github_id", sa.BigInteger(), nullable=True))
    op.create_index("idx_users_github_username", "users", ["github_username"])
    op.create_index("idx_users_github_id", "users", ["github_id"], unique=True)


def downgrade() -> None:
    op.drop_index("idx_users_github_id", table_name="users")
    op.drop_index("idx_users_github_username", table_name="users")
    op.drop_column("users", "github_id")
    op.drop_column("users", "github_username")
```

**Adaptation for 0008_team_plan.py:**
- `revision = "0008"`, `down_revision = "0007"`
- `upgrade()`: `op.add_column("teams", sa.Column("plan", sa.String(16), nullable=False, server_default="starter"))` + `op.create_check_constraint("teams_plan_check", "teams", "plan IN ('starter','team','enterprise')")`
- `downgrade()`: `op.drop_constraint("teams_plan_check", "teams")` + `op.drop_column("teams", "plan")`

---

### `apps/memory-api/alembic/versions/0009_crm_contacts.py` (migration, new table)

**Analog:** `apps/memory-api/alembic/versions/0006_drive_watch_channels.py`

**New table creation pattern** (lines 22-70):
```python
def upgrade() -> None:
    op.create_table(
        "drive_watch_channels",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("channel_id", sa.String(256), nullable=False, unique=True),
        sa.Column(
            "mapping_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("team_drive_mappings.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("channel_token", sa.String(256), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_watch_channel_expires", "drive_watch_channels", ["expires_at"])
    op.create_index("idx_watch_channel_mapping", "drive_watch_channels", ["mapping_id"])


def downgrade() -> None:
    op.drop_index("idx_watch_channel_mapping", table_name="drive_watch_channels")
    op.drop_index("idx_watch_channel_expires", table_name="drive_watch_channels")
    op.drop_table("drive_watch_channels")
```

**Also apply CheckConstraint pattern from** `0001_initial.py` (lines 87-92):
```python
sa.CheckConstraint(
    "truth_level IN ('EPHEMERAL','WORKING','VALIDATED','CANONICAL','PUBLIC')",
    name="messages_truth_level_check"
),
```

**Adaptation for 0009_crm_contacts.py:**
- `revision = "0009"`, `down_revision = "0008"`
- Create table `contacts` with UUID PK, tagging contract columns (`team_scope`, `truth_level`, `confidence`, `source`, `project_scope`), `contact_type`, identity fields, mass contact fields, interaction tracking, `created_at`/`updated_at`
- Also create table `granola_integrations(id, team_scope, api_key_enc, last_polled_at, created_at)`
- Add `CheckConstraint` for `contact_type IN ('direct','mass')` and `truth_level IN (...)`
- Create partial unique index: `CREATE UNIQUE INDEX idx_contacts_team_email ON contacts(team_scope, email) WHERE email IS NOT NULL`

---

### `apps/memory-api/alembic/versions/0010_tasks.py` (migration, new table)

**Analog:** `apps/memory-api/alembic/versions/0006_drive_watch_channels.py` (same structure as 0009)

**Adaptation for 0010_tasks.py:**
- `revision = "0010"`, `down_revision = "0009"`
- Create table `tasks` with UUID PK, tagging contract columns (`team_scope`, `project_scope`), content fields (`title`, `description`, `status`, `priority`, `due_date`), assignment (`assigned_to` FK → `contacts.id` ON DELETE SET NULL, `created_by` FK → `users.id`), provenance (`source`, `source_ref`), `created_at`/`updated_at`
- `CheckConstraint` for `status IN ('todo','in_progress','done','cancelled')`, `priority IN ('low','normal','high','urgent')`, `source IN ('granola','agent','chat','manual')`
- Indexes: `idx_tasks_team`, `idx_tasks_assigned`, `idx_tasks_status`

---

### `apps/memory-api/app/routes/crm.py` (router, CRUD contacts)

**Analog:** `apps/memory-api/app/routes/teams.py`

**Router + Pydantic models pattern** (lines 1-55):
```python
"""/v1/teams — admin-managed team CRUD + membership."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import write_audit
from app.auth import is_admin
from app.deps import get_current_principal, get_session
from app.repos import teams as teams_repo

router = APIRouter()


class TeamCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")
    display_name: str = Field(..., min_length=1, max_length=256)


class TeamOut(BaseModel):
    id: str
    slug: str
    display_name: str
```

**POST with audit pattern** (lines 57-78):
```python
@router.post("/teams", response_model=TeamOut, status_code=201)
async def create_team(
    body: TeamCreateBody,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    user = _require_admin_user(principal)
    ...
    await write_audit(
        session,
        actor_user_id=user.id,
        team_scope=team.slug,
        action="teams.create",
        target_id=str(team.id),
        payload={"slug": team.slug},
    )
    await session.commit()
    return TeamOut(...)
```

**GET list pattern** (lines 117-132):
```python
@router.get("/teams/{team_id}/members", response_model=list[MemberOut])
async def list_members(
    team_id: UUID,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    user = _require_user(principal)
    ...
    members = await teams_repo.list_members(session, team_id=team_id)
    return [MemberOut(...) for m in members]
```

**Adaptation for crm.py:**
- Import `require_paid_tier` from `app.deps` (new dependency) instead of `get_team_scope`
- `ContactOut` model with `model_config = ConfigDict(from_attributes=True)` (used in promotions.py, line 47)
- `GET /crm/contacts` → list with pagination (`limit: int = Query(default=50, ge=1, le=200)`) — copy from memory.py `search_memory` Query pattern (line 137)
- `POST /crm/contacts` → insert + audit (action=`crm.contact.created`)
- `GET /crm/contacts/{contact_id}` → single fetch
- `PATCH /crm/contacts/{contact_id}` → update + audit
- `DELETE /crm/contacts/{contact_id}` → 204 + audit

**SQL upsert pattern** from `admin_drive.py` (lines 130-144):
```python
result = await session.execute(
    sa.text(
        """INSERT INTO team_drive_mappings(team_scope, folder_id, project_scope)
           VALUES(:team_scope, :folder_id, :project_scope)
           ON CONFLICT(team_scope, folder_id) DO UPDATE
           SET project_scope=EXCLUDED.project_scope, updated_at=now()
           RETURNING id"""
    ),
    {"team_scope": body.team_scope, ...},
)
```

**Adaptation:** Use for contact upsert-on-email: `ON CONFLICT(team_scope, email) WHERE email IS NOT NULL DO UPDATE SET interaction_count = contacts.interaction_count + 1, updated_at = now()`.

---

### `apps/memory-api/app/routes/tasks.py` (router, CRUD tasks)

**Analog:** `apps/memory-api/app/routes/teams.py` (same pattern as crm.py)

**Key differences from crm.py:**
- Filter endpoints: `GET /tasks?status=todo&assigned_to=<contact_id>&team_scope=...`
- `since` query param for dashboard polling: `GET /tasks?since=<iso_datetime>`
- `PATCH /tasks/{task_id}` → status transition (todo→in_progress→done)
- Audit actions: `task.created`, `task.status_changed`, `task.assigned`
- Use `require_paid_tier` dependency on all endpoints

---

### `apps/memory-api/app/routes/granola_integration.py` (router, ingest + admin)

**Analog:** `apps/memory-api/app/routes/drive_webhook.py` (ingest endpoint) + `apps/memory-api/app/routes/admin_drive.py` (admin endpoint)

**Webhook/ingest receiver pattern** from `drive_webhook.py` (lines 38-95):
```python
@router.post("/drive/webhook", status_code=200)
async def drive_webhook(
    x_goog_channel_id: str | None = Header(None, alias="X-Goog-Channel-ID"),
    x_goog_channel_token: str | None = Header(None, alias="X-Goog-Channel-Token"),
    x_goog_resource_state: str | None = Header(None, alias="X-Goog-Resource-State"),
    session=Depends(get_session),
):
    ...
    row = (await session.execute(sa.text("""
        SELECT dwc.channel_token, tdm.team_scope
        FROM drive_watch_channels dwc
        JOIN team_drive_mappings tdm ON dwc.mapping_id = tdm.id
        WHERE dwc.channel_id = :channel_id
    """), {"channel_id": x_goog_channel_id})).fetchone()

    if row is None:
        raise HTTPException(404, "Unknown channel")
    if row.channel_token != x_goog_channel_token:
        raise HTTPException(401, "Invalid channel token")
    ...
    return Response(status_code=200)
```

**Admin endpoint pattern** from `admin_drive.py` (lines 108-167):
```python
@router.post("/admin/drive-mapping", status_code=201)
async def create_drive_mapping(
    body: DriveMappingBody,
    session=Depends(get_session),
    principal: dict[str, Any] = Depends(get_current_principal),
):
    if not _is_admin(principal):
        raise HTTPException(403, "Admin access required")
    ...
    result = await session.execute(sa.text("INSERT INTO ... RETURNING id"), {...})
    await session.commit()
    return {...}
```

**Adaptation for granola_integration.py:**
- `POST /integrations/granola/ingest` — receives note payload from granola-sync (bridge JWT auth). Processes: write memory_item (source=`granola`), trigger background task `_extract_crm_contacts`, trigger background task `_maybe_create_tasks_from_granola`. Auth: bridge principal check only (internal service endpoint)
- `POST /admin/granola-integration` — register a Granola API key for a team. Admin-only. Fernet-encrypt the key, insert into `granola_integrations`. Copy `_is_admin` + `_require_fernet` pattern from `admin_drive.py` (lines 47-56 and 59-70)
- `GET /admin/granola-integration` — list integrations for a team (omit encrypted key from response, same as admin_drive.py line 201: `"oauth_configured": r.oauth_credentials_enc is not None`)

---

### `apps/memory-api/app/deps.py` (modified — add `require_paid_tier`)

**Analog:** itself, specifically the `get_team_scope` dependency (lines 116-136):

```python
async def get_team_scope(
    principal: dict[str, Any] = Depends(get_current_principal),
    x_team_scope: str = Header(..., alias="X-Team-Scope"),
    session: AsyncSession = Depends(get_session),
) -> str:
    """Verify the principal is allowed to operate within X-Team-Scope. Returns the slug."""
    if principal["kind"] == "bridge":
        if principal["team_scope"] != x_team_scope:
            raise HTTPException(403, "Bridge JWT team_scope mismatch with header")
        return x_team_scope
    ...
    membership = await get_membership(session, user_id=user.id, team_slug=x_team_scope)
    if membership is None:
        raise HTTPException(403, f"Not a member of team {x_team_scope}")
    return x_team_scope
```

**New dependency to add after `get_team_scope`:**
```python
async def require_paid_tier(
    session: AsyncSession = Depends(get_session),
    team_scope: str = Depends(get_team_scope),  # inherits membership check
) -> str:
    """Raises 403 if team plan is 'starter'. Used on /v1/crm/* and /v1/tasks/*."""
    import sqlalchemy as sa
    row = (await session.execute(
        sa.text("SELECT plan FROM teams WHERE slug = :slug"),
        {"slug": team_scope}
    )).fetchone()
    if row is None or row.plan == "starter":
        raise HTTPException(403, "CRM and task tracking require a Team or Enterprise plan")
    return team_scope
```

---

### `apps/memory-api/app/main.py` (modified — register new routers)

**Analog:** itself (lines 15-96):

**Import + include_router pattern** (lines 15-31 and 83-96):
```python
from app.routes import (
    admin_drive,
    admin_projects,
    audit,
    conversations,
    drive_webhook,
    ...
)
...
app.include_router(health.router, prefix="/v1", tags=["health"])
app.include_router(admin_drive.router, prefix="/v1", tags=["admin-drive"])
app.include_router(drive_webhook.router, prefix="/v1", tags=["drive-webhook"])
```

**Adaptation:** Add three new imports and three new `include_router` calls:
```python
from app.routes import crm, tasks, granola_integration
...
app.include_router(crm.router, prefix="/v1", tags=["crm"])
app.include_router(tasks.router, prefix="/v1", tags=["tasks"])
app.include_router(granola_integration.router, prefix="/v1", tags=["integrations"])
```

---

### `apps/memory-api/app/config.py` (modified — add SMTP + Anthropic + Fernet)

**Analog:** itself (lines 1-50):

**Pattern:** Add new env vars to the `Settings` class with sensible defaults:
```python
# Phase 7 — Granola + CRM + Tasks
ANTHROPIC_API_KEY: str = ""
FERNET_KEY: str = ""  # same as OAUTH_CREDENTIALS_ENCRYPTION_KEY pattern
# SMTP for task notifications (fail-soft when SMTP_HOST not set)
SMTP_HOST: str = ""
SMTP_PORT: int = 587
SMTP_USER: str = ""
SMTP_PASSWORD: str = ""
SMTP_FROM: str = "noreply@dejavu.cat"
SMTP_TLS: bool = True
```

---

### `apps/memory-api/app/routes/memory.py` (modified — add background tasks)

**Analog:** itself (lines 126-133):

**Background task pattern** (lines 126-133):
```python
# Phase 5 plan 05-01: Enrich Graphiti graph with the new memory item.
# create_task so we don't block the HTTP response — _enrich_with_graphiti is fail-soft.
if body.item.content:
    asyncio.create_task(
        _enrich_with_graphiti(body.item.content, team_scope)
    )
```

**Adaptation:** Add two more `asyncio.create_task` calls after the existing graphiti task. Copy the `_enrich_with_graphiti` function structure for new background functions:

```python
async def _extract_crm_contacts(content: str, team_scope: str, session: AsyncSession) -> None:
    """Optional: extract person entities -> upsert contacts. Fail-soft like graphiti."""
    try:
        # Call Claude via anthropic SDK, upsert into contacts table
        ...
    except Exception as exc:
        log.warning("crm.extract_skipped", error=str(exc), team_scope=team_scope)


async def _maybe_create_task(item: MemoryItem, team_scope: str) -> None:
    """Optional: auto-create task if memory_item has contains_action metadata. Fail-soft."""
    try:
        if not (item.metadata or {}).get("contains_action"):
            return
        # Call Claude to extract task title + assignee, insert into tasks table
        ...
    except Exception as exc:
        log.warning("tasks.auto_create_skipped", error=str(exc), team_scope=team_scope)
```

---

### `infrastructure/nginx/conf.d/10-xbrain.conf` (modified — new routes)

**Analog:** itself (lines 43-63):

**Proxy pass to memory-api pattern** (lines 43-52):
```nginx
location /memapi/ {
  set $memory_api_upstream http://memory-api:8000;
  rewrite ^/memapi/(.*)$ /$1 break;
  proxy_pass $memory_api_upstream;
  proxy_set_header Host $host;
  proxy_set_header X-Real-IP $remote_addr;
  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  proxy_set_header X-Forwarded-Proto https;
  proxy_set_header Authorization $http_authorization;
  proxy_read_timeout 60s;
}
```

**Adaptation:** `/v1/crm/*` and `/v1/tasks/*` are handled through the existing `/memapi/` block (rewrite strips the prefix). No new location block needed — the existing block routes everything via `rewrite ^/memapi/(.*)$ /$1 break`. If direct `/v1/crm/*` routing is needed (bypassing LibreChat root), copy the OAuth callback location pattern (lines 55-63):

```nginx
location /v1/crm/ {
  set $memory_api_upstream http://memory-api:8000;
  proxy_pass $memory_api_upstream;
  proxy_set_header Host $host;
  proxy_set_header X-Real-IP $remote_addr;
  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  proxy_set_header X-Forwarded-Proto https;
  proxy_set_header Authorization $http_authorization;
  proxy_read_timeout 60s;
}
```

Duplicate for `/v1/tasks/` and `/v1/integrations/granola/`.

---

### `infrastructure/docker-compose.yml` (modified — granola-sync service)

**Analog:** drive-sync service block (lines 693-728):

```yaml
# === drive-sync (plan 03-11) — incremental Google Drive sync sidecar ===
drive-sync:
  build:
    context: ..   # repo root -- needs packages/memory-models
    dockerfile: apps/drive-sync/Dockerfile
  image: xbrain/drive-sync:phase3
  container_name: xbrain-drive-sync
  restart: unless-stopped
  environment:
    DATABASE_URL: ${DATABASE_URL}
    MEMORY_API_URL: http://memory-api:8000
    AGENT_RUNTIME_URL: http://agent-runtime:9100
    BRIDGE_SHARED_SECRET: ${BRIDGE_SHARED_SECRET}
    JWT_ALGORITHM: ${JWT_ALGORITHM:-HS256}
    GOOGLE_CLIENT_ID: ${GOOGLE_CLIENT_ID}
    ...
    LOG_LEVEL: ${LOG_LEVEL:-INFO}
  networks: [xbrain_net]
  mem_limit: 192m
  depends_on:
    postgres: { condition: service_healthy }
    memory-api: { condition: service_healthy }
    agent-runtime: { condition: service_healthy }
  healthcheck:
    test: ["CMD-SHELL", "test -f /tmp/drive-sync-alive && [ $(($(date +%s) - $(stat -c %Y /tmp/drive-sync-alive))) -lt 600 ] || exit 1"]
    interval: 60s
    timeout: 10s
    retries: 3
    start_period: 30s
```

**Adaptation for granola-sync:**
```yaml
granola-sync:
  build:
    context: ..
    dockerfile: apps/granola-sync/Dockerfile
  image: xbrain/granola-sync:phase7
  container_name: xbrain-granola-sync
  restart: unless-stopped
  environment:
    DATABASE_URL: ${DATABASE_URL}
    MEMORY_API_URL: http://memory-api:8000
    BRIDGE_SHARED_SECRET: ${BRIDGE_SHARED_SECRET}
    JWT_ALGORITHM: ${JWT_ALGORITHM:-HS256}
    ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
    FERNET_KEY: ${FERNET_KEY}
    GRANOLA_POLL_INTERVAL_SECONDS: ${GRANOLA_POLL_INTERVAL_SECONDS:-300}
    LOG_LEVEL: ${LOG_LEVEL:-INFO}
  networks: [xbrain_net]
  mem_limit: 128m   # lighter than drive-sync (no Google SDK, no uvicorn)
  depends_on:
    postgres: { condition: service_healthy }
    memory-api: { condition: service_healthy }
  healthcheck:
    test: ["CMD-SHELL", "test -f /tmp/granola-sync-alive && [ $(($(date +%s) - $(stat -c %Y /tmp/granola-sync-alive))) -lt 600 ] || exit 1"]
    interval: 60s
    timeout: 10s
    retries: 3
    start_period: 30s
```

---

## Shared Patterns

### Authentication — Bridge JWT (service-to-service)

**Source:** `apps/drive-sync/app/ingestion_client.py` lines 18-29
**Apply to:** `granola-sync/app/memory_client.py`, `granola_integration.py` ingest endpoint validation

```python
def _make_bridge_jwt() -> str:
    payload = {
        "sub": "granola-sync",
        "scope": "bridge",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    return jose_jwt.encode(
        {"alg": settings.JWT_ALGORITHM},
        payload,
        settings.BRIDGE_SHARED_SECRET,
    ).decode()
```

### Authentication — Admin check (user endpoints)

**Source:** `apps/memory-api/app/routes/admin_drive.py` lines 59-70
**Apply to:** `granola_integration.py` admin endpoints, any admin-only config endpoint

```python
def _is_admin(principal: dict[str, Any]) -> bool:
    if principal.get("kind") in ("service", "bridge"):
        return True
    sub = principal.get("sub", "")
    admin_subs = [s.strip() for s in (settings.ADMIN_USER_SUBS or "").split(",") if s.strip()]
    return sub in admin_subs
```

### Authentication — Paid tier guard

**Source:** `apps/memory-api/app/deps.py` lines 116-136 (pattern for new `require_paid_tier`)
**Apply to:** all endpoints in `crm.py` and `tasks.py`

```python
# Dependency chain: require_paid_tier → get_team_scope → get_current_principal
# Single dep call on each endpoint, no duplicate DB queries
async def require_paid_tier(
    session: AsyncSession = Depends(get_session),
    team_scope: str = Depends(get_team_scope),
) -> str:
    ...
```

### Error Handling — HTTP exceptions

**Source:** `apps/memory-api/app/routes/teams.py` lines 63-78
**Apply to:** all new routers (`crm.py`, `tasks.py`, `granola_integration.py`)

```python
# Raise early — no bare except catching
if resource is None:
    raise HTTPException(404, "contact not found in this team")
if not _is_admin(principal):
    raise HTTPException(403, "Admin access required")
```

### Error Handling — Fail-soft background tasks

**Source:** `apps/memory-api/app/routes/memory.py` lines 33-51
**Apply to:** `_extract_crm_contacts`, `_maybe_create_task` in memory.py; all background tasks in granola-sync

```python
async def _enrich_with_graphiti(content: str, group_id: str) -> None:
    """Fail-soft: if service is down, memory-api continues normally."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(...)
            r.raise_for_status()
    except Exception as exc:
        log.warning("graphiti.enrich_skipped", error=str(exc), group_id=group_id)
```

### Audit Log

**Source:** `apps/memory-api/app/audit.py` (all 20 lines) + usage in `teams.py` lines 69-76
**Apply to:** all state-mutating endpoints in `crm.py` and `tasks.py`

```python
await write_audit(
    session,
    actor_user_id=actor_id,
    team_scope=team_scope,
    action="crm.contact.created",   # or "task.created", "task.status_changed"
    target_id=str(contact.id),
    payload={"source": contact.source, "truth_level": contact.truth_level},
)
await session.commit()  # always commit after write_audit
```

### Fernet Encryption (API key at rest)

**Source:** `apps/memory-api/app/routes/admin_drive.py` lines 47-56
**Apply to:** `granola_integration.py` admin endpoint (store encrypted Granola API key) + `granola_poller.py` (decrypt before use)

```python
def _require_fernet():
    if not settings.OAUTH_CREDENTIALS_ENCRYPTION_KEY:
        raise HTTPException(500, "FERNET_KEY not configured")
    from cryptography.fernet import Fernet
    return Fernet(settings.OAUTH_CREDENTIALS_ENCRYPTION_KEY.encode())
```

**Usage in granola_integration.py:**
```python
fernet = _require_fernet()
encrypted = fernet.encrypt(body.api_key.encode()).decode()
# Store encrypted in granola_integrations.api_key_enc
```

### Pydantic models — response_model ConfigDict

**Source:** `apps/memory-api/app/routes/promotions.py` lines 44-56
**Apply to:** `ContactOut` in crm.py, `TaskOut` in tasks.py (SQLAlchemy model → Pydantic)

```python
class PromotionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")
    id: UUID
    team_scope: str
    ...
```

### Structured logging

**Source:** `apps/drive-sync/app/drive_poller.py` lines 106-107 and 191-192
**Apply to:** all new files

```python
log = structlog.get_logger(__name__)
log.info("poll.changes_fetched", team=team_scope, count=len(notes))
log.warning("poll.export_error", note_id=note_id, status=exc.response.status_code)
log.error("poll.team_error", team=row["team_scope"], error=str(exc))
```

### Tagging contract enforcement

**Source:** `apps/memory-api/alembic/versions/0001_initial.py` lines 75-91 (messages table constraints)
**Apply to:** `contacts` and `tasks` table migrations (0009, 0010)

```python
sa.CheckConstraint(
    "truth_level IN ('EPHEMERAL','WORKING','VALIDATED','CANONICAL','PUBLIC')",
    name="contacts_truth_level_check"
),
```

---

## No Analog Found

| File | Role | Data Flow | Reason |
|---|---|---|---|
| Dashboard tasks (`tasks/index.html` Firebase) | component | request-response (polling) | No HTML dashboard files in repo. Pattern from Phase 6 is inferred but not present on disk. Planner should use RESEARCH.md pattern (static HTML + fetch API) |

---

## Metadata

**Analog search scope:** `apps/drive-sync/`, `apps/memory-api/`, `infrastructure/`
**Files scanned:** 25 source files read directly
**Pattern extraction date:** 2026-05-07

**Key patterns summary:**
1. All new FastAPI routers follow the `teams.py` pattern: `APIRouter`, Pydantic `BaseModel` with `ConfigDict(extra="forbid")`, `Depends(get_session)` + `Depends(get_current_principal)`, `write_audit` + `await session.commit()` on every mutation
2. `granola-sync` is a structural copy of `drive-sync` with Google API replaced by httpx + Granola REST API + Claude SDK; sentinel file healthcheck identical
3. Paid tier enforcement is a new chained dependency `require_paid_tier → get_team_scope` injected on all CRM/tasks endpoints
4. Background tasks (`_extract_crm_contacts`, `_maybe_create_task`) follow the `_enrich_with_graphiti` fail-soft pattern exactly: `asyncio.create_task(...)` after `session.commit()`, catch-all `except Exception` with `log.warning`
5. Alembic migrations follow a strict sequential numbering: 0008→0009→0010, each with full `upgrade()` + `downgrade()`, revision chain via `down_revision`
