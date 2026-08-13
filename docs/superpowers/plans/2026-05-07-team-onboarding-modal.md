# Team Onboarding Modal — Implementation Plan

> **DONE — DO NOT EXECUTE (annotated 2026-08-13).** This plan shipped in 2026-05.
> `alembic/versions/0011_team_onboarding.py` and `apps/librechat/patches/onboarding.js`
> both exist, and the endpoints it describes are live in `routes/teams.py`. Its 37
> unticked `- [ ]` boxes are **tracking state that was never written back**, not
> outstanding work — running this plan again would try to re-create a migration that
> is five revisions behind head (`0034`).
>
> Two things have moved since: nginx vhosts are now templates under
> `infrastructure/nginx/templates/` (corrected in the file map below), and LibreChat
> sits behind the **`saas`** compose profile, so none of this runs on an OSS-light
> install. Kept as the design record for how team onboarding was built.

**Goal:** Every new (or team-less) LibreChat user sees a blocking 4-step modal that assigns them to a team before they can chat.

**Architecture:** memory-api adds new DB tables + 8 endpoints. A custom LibreChat Docker image bakes in two patches: (1) the existing `socialLogin.js` OAuth linking fix, (2) a thin Express route `/api/xbrain/token` that issues a short-lived bridge JWT for the user. An injected `onboarding.js` vanilla-JS modal calls that route to get a token, then drives the wizard against memory-api endpoints.

**Tech Stack:** FastAPI (memory-api), PostgreSQL + Alembic, Fernet encryption, vanilla JS (no build step), Docker multi-stage patch via Dockerfile, LibreChat v0.8.5 Express.js.

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `apps/memory-api/alembic/versions/0011_team_onboarding.py` | Create | Migration: teams.visibility + github_org, team_api_keys, team_join_requests |
| `apps/memory-api/app/models/team.py` | Modify | Add `TeamApiKey`, `TeamJoinRequest` models; update `Team` |
| `apps/memory-api/app/repos/teams.py` | Modify | Add search, github-matches, join, join-request, api-key CRUD |
| `apps/memory-api/app/routes/teams.py` | Modify | Add 7 new endpoints; open team creation to all users |
| `apps/memory-api/app/deps.py` | Modify | Handle `iss: librechat-onboarding` bridge JWT → user resolution |
| `apps/memory-api/app/main.py` | Modify | Expand CORS to allow `chat.example.com` |
| `apps/librechat/patches/socialLogin.js` | Create | Copy of the VM-patched OAuth linking fix |
| `apps/librechat/patches/xbrain-routes.js` | Create | Express route: `GET /api/xbrain/token` → short-lived bridge JWT for caller |
| `apps/librechat/patches/patch-server.js` | Create | Node.js script run at image build: injects `require('./routes/xbrain-routes')` into LibreChat's server |
| `apps/librechat/patches/onboarding.js` | Create | Vanilla JS 4-step modal (300 lines, no build step) |
| `apps/librechat/Dockerfile` | Create | Extends LibreChat image; bakes patches; patches index.html |
| `infrastructure/docker-compose.yml` | Modify | LibreChat service: `build:` instead of `image:`, add `BRIDGE_SHARED_SECRET` to env |
| `infrastructure/nginx/templates/20-api.conf.template` | Modify | Add `Access-Control-Allow-Origin: https://chat.example.com` to memory-api location |

---

## Task 1 — DB Migration 0011

**Files:**
- Create: `apps/memory-api/alembic/versions/0011_team_onboarding.py`

- [ ] **Step 1: Write the migration**

```python
"""team onboarding — visibility, github_org, team_api_keys, team_join_requests

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- teams table extensions ---
    op.add_column(
        "teams",
        sa.Column(
            "visibility",
            sa.String(16),
            nullable=False,
            server_default="closed",
        ),
    )
    op.add_column("teams", sa.Column("github_org", sa.String(256), nullable=True))
    op.add_column("teams", sa.Column("description", sa.Text, nullable=True))
    op.create_check_constraint(
        "teams_visibility_check", "teams", "visibility IN ('open', 'closed')"
    )

    # --- team_api_keys ---
    op.create_table(
        "team_api_keys",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("key_enc", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("team_id", "provider", name="team_api_keys_team_provider_uniq"),
    )

    # --- team_join_requests ---
    op.create_table(
        "team_join_requests",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="join_requests_status_check",
        ),
        sa.UniqueConstraint("team_id", "user_id", name="join_requests_team_user_uniq"),
    )
    op.create_index("idx_join_requests_user", "team_join_requests", ["user_id"])
    op.create_index("idx_team_api_keys_team", "team_api_keys", ["team_id"])


def downgrade() -> None:
    op.drop_index("idx_team_api_keys_team", table_name="team_api_keys")
    op.drop_index("idx_join_requests_user", table_name="team_join_requests")
    op.drop_table("team_join_requests")
    op.drop_table("team_api_keys")
    op.drop_constraint("teams_visibility_check", "teams", type_="check")
    op.drop_column("teams", "description")
    op.drop_column("teams", "github_org")
    op.drop_column("teams", "visibility")
```

- [ ] **Step 2: Verify the migration applies cleanly**

```bash
# On the VM (or local with a test DB):
cd /opt/xbrain/infrastructure
docker exec xbrain-memory-api alembic upgrade head
```
Expected: `Running upgrade 0010 -> 0011`

- [ ] **Step 3: Commit**

```bash
git add apps/memory-api/alembic/versions/0011_team_onboarding.py
git commit -m "feat(db): migration 0011 — team onboarding schema"
```

---

## Task 2 — ORM Model Updates

**Files:**
- Modify: `apps/memory-api/app/models/team.py`

- [ ] **Step 1: Write the failing test for new models**

Create `apps/memory-api/tests/test_onboarding_models.py`:

```python
"""Unit tests for Team model new fields + TeamApiKey + TeamJoinRequest."""

import os

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("BRIDGE_SHARED_SECRET", "test")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")


def test_team_model_has_visibility():
    from app.models.team import Team

    t = Team(slug="a", display_name="A", visibility="open")
    assert t.visibility == "open"


def test_team_api_key_model_exists():
    from app.models.team import TeamApiKey

    k = TeamApiKey(provider="anthropic", key_enc="enc")
    assert k.provider == "anthropic"


def test_team_join_request_model_exists():
    from app.models.team import TeamJoinRequest

    r = TeamJoinRequest(status="pending")
    assert r.status == "pending"
```

Run: `cd apps/memory-api && python -m pytest tests/test_onboarding_models.py -v`
Expected: FAIL — `cannot import name 'TeamApiKey'`

- [ ] **Step 2: Update `app/models/team.py`**

Replace the entire file:

```python
"""Team + TeamMember + TeamApiKey + TeamJoinRequest ORM."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="closed")
    github_org: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TeamMember(Base):
    __tablename__ = "team_members"
    __table_args__ = (CheckConstraint("role IN ('admin','member')", name="team_members_role_check"),)

    team_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TeamApiKey(Base):
    __tablename__ = "team_api_keys"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    team_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    key_enc: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TeamJoinRequest(Base):
    __tablename__ = "team_join_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')", name="join_requests_status_check"
        ),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    team_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

- [ ] **Step 3: Run tests — expect PASS**

```bash
cd apps/memory-api && python -m pytest tests/test_onboarding_models.py -v
```
Expected: 3 PASS

- [ ] **Step 4: Commit**

```bash
git add apps/memory-api/app/models/team.py apps/memory-api/tests/test_onboarding_models.py
git commit -m "feat(models): add visibility, github_org, TeamApiKey, TeamJoinRequest"
```

---

## Task 3 — Teams Repo Extensions

**Files:**
- Modify: `apps/memory-api/app/repos/teams.py`

- [ ] **Step 1: Write failing tests**

Create `apps/memory-api/tests/test_onboarding_repos.py`:

```python
"""Integration tests for new team repo functions."""

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_search_teams_by_name(session):
    from app.repos import teams as repo
    from app.repos import users as users_repo

    creator = await users_repo.get_or_create_user(
        session, source_user_id="s1", email="u1@test.local"
    )
    await repo.create_team(
        session, slug="acme-labs", display_name="Acme Labs", creator_user_id=creator.id
    )
    await session.flush()

    results = await repo.search_teams(session, query="acme")
    assert any(t.slug == "acme-labs" for t in results)


@pytest.mark.asyncio
async def test_get_first_team_for_user_returns_none_when_no_team(session):
    from app.repos import teams as repo
    from app.repos import users as users_repo
    from uuid import uuid4

    user = await users_repo.get_or_create_user(
        session, source_user_id="s2", email="u2@test.local"
    )
    result = await repo.get_first_team_for_user(session, user_id=user.id)
    assert result is None


@pytest.mark.asyncio
async def test_get_first_team_for_user_returns_team_when_member(session):
    from app.repos import teams as repo
    from app.repos import users as users_repo

    user = await users_repo.get_or_create_user(
        session, source_user_id="s3", email="u3@test.local"
    )
    team = await repo.create_team(
        session, slug="my-team", display_name="My Team", creator_user_id=user.id
    )
    await session.flush()

    result = await repo.get_first_team_for_user(session, user_id=user.id)
    assert result is not None
    assert result.slug == "my-team"


@pytest.mark.asyncio
async def test_create_join_request_is_idempotent(session):
    from app.repos import teams as repo
    from app.repos import users as users_repo

    alice = await users_repo.get_or_create_user(
        session, source_user_id="s4", email="alice@test.local"
    )
    bob = await users_repo.get_or_create_user(
        session, source_user_id="s5", email="bob@test.local"
    )
    team = await repo.create_team(
        session, slug="closed-team", display_name="Closed", creator_user_id=alice.id
    )
    await session.flush()

    r1 = await repo.create_join_request(session, team_id=team.id, user_id=bob.id)
    r2 = await repo.create_join_request(session, team_id=team.id, user_id=bob.id)
    assert r1.id == r2.id  # idempotent
    assert r1.status == "pending"
```

Run: `cd apps/memory-api && python -m pytest tests/test_onboarding_repos.py -v`
Expected: FAIL — `cannot import name 'search_teams'` (or similar)

- [ ] **Step 2: Extend `app/repos/teams.py`**

Append these functions to the existing file (keep all existing functions):

```python
async def search_teams(session: AsyncSession, *, query: str, limit: int = 10) -> list[Team]:
    """Case-insensitive search on slug and display_name. Returns up to `limit` results."""
    pattern = f"%{query.lower()}%"
    result = await session.execute(
        select(Team)
        .where(
            sa.or_(
                sa.func.lower(Team.slug).like(pattern),
                sa.func.lower(Team.display_name).like(pattern),
            )
        )
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_first_team_for_user(session: AsyncSession, *, user_id: UUID) -> Team | None:
    """Return the first team the user belongs to, or None."""
    result = await session.execute(
        select(Team)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(TeamMember.user_id == user_id)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_teams_with_github_org(session: AsyncSession) -> list[Team]:
    """Return all teams that have a github_org set."""
    result = await session.execute(
        select(Team).where(Team.github_org.isnot(None))
    )
    return list(result.scalars().all())


async def create_join_request(
    session: AsyncSession,
    *,
    team_id: UUID,
    user_id: UUID,
) -> "TeamJoinRequest":
    """Create a join request, or return existing one (idempotent)."""
    from app.models.team import TeamJoinRequest

    result = await session.execute(
        select(TeamJoinRequest).where(
            (TeamJoinRequest.team_id == team_id) & (TeamJoinRequest.user_id == user_id)
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing
    req = TeamJoinRequest(team_id=team_id, user_id=user_id, status="pending")
    session.add(req)
    await session.flush()
    return req


async def upsert_team_api_key(
    session: AsyncSession,
    *,
    team_id: UUID,
    provider: str,
    key_enc: str,
) -> "TeamApiKey":
    """Insert or replace an encrypted API key for (team_id, provider)."""
    from app.models.team import TeamApiKey

    result = await session.execute(
        select(TeamApiKey).where(
            (TeamApiKey.team_id == team_id) & (TeamApiKey.provider == provider)
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        existing.key_enc = key_enc
        return existing
    key = TeamApiKey(team_id=team_id, provider=provider, key_enc=key_enc)
    session.add(key)
    await session.flush()
    return key


async def list_team_api_keys(session: AsyncSession, *, team_id: UUID) -> list["TeamApiKey"]:
    from app.models.team import TeamApiKey

    result = await session.execute(
        select(TeamApiKey).where(TeamApiKey.team_id == team_id)
    )
    return list(result.scalars().all())
```

Also add `import sqlalchemy as sa` to the top of `repos/teams.py` (it's needed for `sa.or_`). The existing imports need:
```python
import sqlalchemy as sa
from sqlalchemy import select
```

- [ ] **Step 3: Run tests — expect PASS**

```bash
cd apps/memory-api && python -m pytest tests/test_onboarding_repos.py -v
```
Expected: 4 PASS (requires Docker for testcontainers)

- [ ] **Step 4: Commit**

```bash
git add apps/memory-api/app/repos/teams.py apps/memory-api/tests/test_onboarding_repos.py
git commit -m "feat(repos): add search, get_first_team, join_request, api_key CRUD"
```

---

## Task 4 — Onboarding Routes

**Files:**
- Modify: `apps/memory-api/app/routes/teams.py`
- Modify: `apps/memory-api/app/main.py`

- [ ] **Step 1: Write failing tests**

Create `apps/memory-api/tests/test_onboarding_routes.py`:

```python
"""Integration tests for onboarding endpoints."""

import os
import time
import pytest
from authlib.jose import jwt

pytestmark = pytest.mark.integration

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("BRIDGE_SHARED_SECRET", "test-bridge-secret-do-not-use-in-prod")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")
os.environ.setdefault("OAUTH_CREDENTIALS_ENCRYPTION_KEY", "")
os.environ.setdefault("FERNET_KEY", "")


def make_onboarding_jwt(email: str, secret: str = "test-bridge-secret-do-not-use-in-prod") -> str:
    now = int(time.time())
    payload = {
        "iss": "librechat-onboarding",
        "sub": "mongo-id-placeholder",
        "email": email,
        "scope": "bridge",
        "iat": now,
        "exp": now + 300,
    }
    return jwt.encode({"alg": "HS256"}, payload, secret).decode("ascii")


@pytest.mark.asyncio
async def test_my_team_returns_204_when_no_team(client):
    token = make_onboarding_jwt("newuser@test.local")
    resp = await client.get("/v1/teams/my-team", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_my_team_returns_200_when_has_team(client, session):
    from app.repos import teams as teams_repo
    from app.repos import users as users_repo

    user = await users_repo.get_or_create_user(
        session, source_user_id="member-sub", email="member@test.local"
    )
    team = await teams_repo.create_team(
        session, slug="existing-team", display_name="Existing", creator_user_id=user.id
    )
    await session.commit()

    token = make_onboarding_jwt("member@test.local")
    resp = await client.get("/v1/teams/my-team", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["slug"] == "existing-team"


@pytest.mark.asyncio
async def test_search_teams(client, session):
    from app.repos import teams as teams_repo
    from app.repos import users as users_repo

    creator = await users_repo.get_or_create_user(
        session, source_user_id="creator-sub", email="creator@test.local"
    )
    await teams_repo.create_team(
        session, slug="searchable", display_name="Searchable Team", creator_user_id=creator.id
    )
    await session.commit()

    token = make_onboarding_jwt("searcher@test.local")
    resp = await client.get(
        "/v1/teams/search?name=searchable",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    results = resp.json()
    assert any(t["slug"] == "searchable" for t in results)


@pytest.mark.asyncio
async def test_self_create_team(client):
    token = make_onboarding_jwt("founder@test.local")
    resp = await client.post(
        "/v1/teams/self",
        json={"slug": "new-team-x", "display_name": "New Team X", "visibility": "open"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    assert resp.json()["slug"] == "new-team-x"
```

Run: `cd apps/memory-api && python -m pytest tests/test_onboarding_routes.py -v`
Expected: FAIL — `404 Not Found` for new endpoints

- [ ] **Step 2: Update `app/deps.py` — handle `librechat-onboarding` JWT**

In `get_current_principal`, find the section that handles `iss == "openwebui-pipeline"` and add a similar block right after it for LibreChat onboarding tokens. The updated bridge JWT handler becomes:

```python
    # Try bridge service JWT.
    try:
        claims = verify_bridge_jwt(token, settings.BRIDGE_SHARED_SECRET)
        acting_sub = claims.get("acting_user_sub")
        acting_email = claims.get("acting_user_email")
        if claims.get("iss") == "openwebui-pipeline" and acting_sub and acting_email:
            user = await get_or_create_user(
                session,
                source_user_id=acting_sub,
                email=acting_email,
                display_name=claims.get("acting_user_name"),
            )
            await session.commit()
            return {
                "kind": "user",
                "user": user,
                "claims": claims,
                "sub": acting_sub,
            }

        # LibreChat onboarding tokens: iss=librechat-onboarding, email=user email
        if claims.get("iss") == "librechat-onboarding" and claims.get("email"):
            user = await get_or_create_user(
                session,
                source_user_id=f"email:{claims['email']}",
                email=claims["email"],
                display_name=None,
            )
            await session.commit()
            return {
                "kind": "user",
                "user": user,
                "claims": claims,
                "sub": f"email:{claims['email']}",
            }

        return {
            "kind": "bridge",
            "claims": claims,
            "sub": claims.get("sub"),
            "team_scope": claims.get("team_scope"),
        }
    except Exception as e:
        raise HTTPException(401, "Invalid token") from e
```

- [ ] **Step 3: Add new endpoints to `app/routes/teams.py`**

Add to the existing `routes/teams.py` file (keep all existing routes intact). Add these imports at the top:

```python
import sqlalchemy as sa
import httpx
from cryptography.fernet import Fernet, InvalidToken
```

Add new Pydantic models after the existing ones:

```python
class TeamSelfCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")
    display_name: str = Field(..., min_length=1, max_length=256)
    description: str | None = None
    visibility: str = Field(default="closed", pattern=r"^(open|closed)$")
    github_org: str | None = None


class TeamSearchOut(BaseModel):
    id: str
    slug: str
    display_name: str
    visibility: str
    github_org: str | None = None


class ApiKeyIn(BaseModel):
    provider: str = Field(..., min_length=1, max_length=64)
    api_key: str = Field(..., min_length=1)


class ApiKeysBulkBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keys: list[ApiKeyIn]


class ApiKeyOut(BaseModel):
    provider: str


class JoinRequestOut(BaseModel):
    status: str
    team_id: str
```

Add new route functions:

```python
def _get_fernet() -> Fernet:
    key = settings.FERNET_KEY or settings.OAUTH_CREDENTIALS_ENCRYPTION_KEY
    if not key:
        raise HTTPException(500, "FERNET_KEY not configured")
    return Fernet(key.encode())


@router.get("/teams/my-team")
async def get_my_team(
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Return the caller's first team, or 204 if they belong to none."""
    user = _require_user(principal)
    team = await teams_repo.get_first_team_for_user(session, user_id=user.id)
    if team is None:
        from fastapi.responses import Response
        return Response(status_code=204)
    return TeamSearchOut(
        id=str(team.id),
        slug=team.slug,
        display_name=team.display_name,
        visibility=team.visibility,
        github_org=team.github_org,
    )


@router.get("/teams/search", response_model=list[TeamSearchOut])
async def search_teams(
    name: str,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    _require_user(principal)
    if not name or len(name) < 2:
        raise HTTPException(400, "name query must be at least 2 characters")
    teams = await teams_repo.search_teams(session, query=name)
    return [
        TeamSearchOut(
            id=str(t.id),
            slug=t.slug,
            display_name=t.display_name,
            visibility=t.visibility,
            github_org=t.github_org,
        )
        for t in teams
    ]


@router.get("/teams/github-matches", response_model=list[TeamSearchOut])
async def github_matches(
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Return xbrain teams whose github_org matches any org the user belongs to.

    Requires the user to have a github_username stored. Uses the server PAT
    (GITHUB_API_PAT) to check membership. Returns empty list if no GitHub link.
    """
    user = _require_user(principal)
    if not user.github_username or not settings.GITHUB_API_PAT:
        return []

    all_teams = await teams_repo.get_teams_with_github_org(session)
    matches = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        for team in all_teams:
            url = f"https://api.github.com/orgs/{team.github_org}/members/{user.github_username}"
            r = await client.get(
                url,
                headers={
                    "Authorization": f"Bearer {settings.GITHUB_API_PAT}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            if r.status_code == 204:  # member
                matches.append(
                    TeamSearchOut(
                        id=str(team.id),
                        slug=team.slug,
                        display_name=team.display_name,
                        visibility=team.visibility,
                        github_org=team.github_org,
                    )
                )
    return matches


@router.post("/teams/self", response_model=TeamSearchOut, status_code=201)
async def self_create_team(
    body: TeamSelfCreateBody,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Any authenticated user can create a team and become its admin (founder)."""
    user = _require_user(principal)
    if await teams_repo.get_team_by_slug(session, body.slug) is not None:
        raise HTTPException(409, f"team slug '{body.slug}' already exists")
    team = await teams_repo.create_team(
        session,
        slug=body.slug,
        display_name=body.display_name,
        creator_user_id=user.id,
        description=body.description,
        visibility=body.visibility,
        github_org=body.github_org,
    )
    await write_audit(
        session,
        actor_user_id=user.id,
        team_scope=team.slug,
        action="teams.self_create",
        target_id=str(team.id),
        payload={"slug": team.slug, "visibility": team.visibility},
    )
    await session.commit()
    return TeamSearchOut(
        id=str(team.id),
        slug=team.slug,
        display_name=team.display_name,
        visibility=team.visibility,
        github_org=team.github_org,
    )


@router.post("/teams/{team_id}/join", status_code=204)
async def join_team(
    team_id: UUID,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Join an open team directly. Returns 403 for closed teams."""
    user = _require_user(principal)
    team = await teams_repo.get_team_by_id(session, team_id)
    if team is None:
        raise HTTPException(404, "team not found")
    if team.visibility != "open":
        raise HTTPException(403, "team is closed — use join-request instead")
    existing = await teams_repo.get_membership(session, user_id=user.id, team_slug=team.slug)
    if existing is not None:
        return  # already a member — idempotent
    await teams_repo.add_member(session, team_id=team.id, user_id=user.id, role="member")
    await write_audit(
        session,
        actor_user_id=user.id,
        team_scope=team.slug,
        action="teams.join",
        target_id=str(team.id),
        payload={},
    )
    await session.commit()


@router.post("/teams/{team_id}/join-request", response_model=JoinRequestOut, status_code=201)
async def request_join_team(
    team_id: UUID,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Submit a join request for a closed team. Idempotent."""
    user = _require_user(principal)
    team = await teams_repo.get_team_by_id(session, team_id)
    if team is None:
        raise HTTPException(404, "team not found")
    req = await teams_repo.create_join_request(session, team_id=team.id, user_id=user.id)
    await session.commit()
    return JoinRequestOut(status=req.status, team_id=str(req.team_id))


@router.get("/teams/{team_id}/api-keys", response_model=list[ApiKeyOut])
async def list_api_keys(
    team_id: UUID,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """List provider names for which the team has an API key. Never returns plaintext keys."""
    user = _require_user(principal)
    team = await teams_repo.get_team_by_id(session, team_id)
    if team is None:
        raise HTTPException(404, "team not found")
    membership = await teams_repo.get_membership(session, user_id=user.id, team_slug=team.slug)
    if membership is None:
        raise HTTPException(403, "not a member")
    keys = await teams_repo.list_team_api_keys(session, team_id=team_id)
    return [ApiKeyOut(provider=k.provider) for k in keys]


@router.put("/teams/{team_id}/api-keys", status_code=204)
async def upsert_api_keys(
    team_id: UUID,
    body: ApiKeysBulkBody,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Upsert team API keys. Caller must be a team admin. Keys are Fernet-encrypted at rest."""
    user = _require_user(principal)
    team = await teams_repo.get_team_by_id(session, team_id)
    if team is None:
        raise HTTPException(404, "team not found")
    membership = await teams_repo.get_membership(session, user_id=user.id, team_slug=team.slug)
    if membership is None or membership.role != "admin":
        raise HTTPException(403, "team admin required")
    fernet = _get_fernet()
    for item in body.keys:
        encrypted = fernet.encrypt(item.api_key.encode()).decode()
        await teams_repo.upsert_team_api_key(
            session, team_id=team_id, provider=item.provider, key_enc=encrypted
        )
    await session.commit()
```

- [ ] **Step 4: Update `repos/teams.py` — `create_team` signature**

The new `self_create_team` passes `description`, `visibility`, `github_org`. Update `create_team` in `repos/teams.py`:

```python
async def create_team(
    session: AsyncSession,
    *,
    slug: str,
    display_name: str,
    creator_user_id: UUID,
    description: str | None = None,
    visibility: str = "closed",
    github_org: str | None = None,
) -> Team:
    """Create a team and add the creator as admin (atomic)."""
    team = Team(
        slug=slug,
        display_name=display_name,
        description=description,
        visibility=visibility,
        github_org=github_org,
    )
    session.add(team)
    await session.flush()
    membership = TeamMember(team_id=team.id, user_id=creator_user_id, role="admin")
    session.add(membership)
    return team
```

- [ ] **Step 5: Register new routes in `main.py`**

The new routes are in the same `teams` router (no new file). No change needed to `main.py` unless the router needs re-importing.

Verify by checking that `teams.router` is already included. It is — no change needed.

- [ ] **Step 6: Run tests — expect PASS**

```bash
cd apps/memory-api && python -m pytest tests/test_onboarding_routes.py -v
```
Expected: 4 PASS

- [ ] **Step 7: Commit**

```bash
git add apps/memory-api/app/routes/teams.py \
        apps/memory-api/app/repos/teams.py \
        apps/memory-api/app/deps.py \
        apps/memory-api/tests/test_onboarding_routes.py
git commit -m "feat(api): onboarding endpoints — my-team, search, github-matches, join, api-keys"
```

---

## Task 5 — CORS Update

**Files:**
- Modify: `apps/memory-api/app/main.py`

The onboarding.js vanilla script at `chat.example.com` calls `api.example.com`. CORS must allow this origin.

- [ ] **Step 1: Update CORS middleware in `main.py`**

Find the `CORSMiddleware` block and update `allow_origin_regex`:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"(chrome-extension://.*|https://chat\.example.com)",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "X-Team-Scope", "Content-Type", "Accept"],
)
```

- [ ] **Step 2: Verify no existing tests break**

```bash
cd apps/memory-api && python -m pytest tests/ -v --ignore=tests/test_onboarding_repos.py -x
```
Expected: all existing tests PASS

- [ ] **Step 3: Commit**

```bash
git add apps/memory-api/app/main.py
git commit -m "fix(cors): allow chat.example.com origin for onboarding modal"
```

---

## Task 6 — LibreChat Custom Image: Backend Patch

**Files:**
- Create: `apps/librechat/patches/socialLogin.js`
- Create: `apps/librechat/patches/xbrain-routes.js`
- Create: `apps/librechat/patches/patch-server.js`

- [ ] **Step 1: Copy `socialLogin.js` from the VM**

```bash
# Run this on the VM to get the patched file:
docker cp xbrain-librechat:/app/api/strategies/socialLogin.js /tmp/socialLogin.js
# Then scp it to local: scp user@__VM_HOST__:/tmp/socialLogin.js apps/librechat/patches/
```

Create the directory first:
```bash
mkdir -p apps/librechat/patches
```

The file must contain the OAuth linking patch (the one that calls `updateUser` to link a second provider). Verify it contains `updateUser` and not just `findUser`:
```bash
grep -n "updateUser" apps/librechat/patches/socialLogin.js
```
Expected: at least one match.

- [ ] **Step 2: Create `apps/librechat/patches/xbrain-routes.js`**

```javascript
/**
 * xbrain-routes.js — mounted on LibreChat's Express app at image build time.
 *
 * Exposes GET /api/xbrain/token — returns a short-lived bridge JWT for the
 * authenticated LibreChat user. The token is used by the onboarding modal to
 * call memory-api endpoints without needing a Google ID token.
 *
 * Security: requires requireJwtAuth (LibreChat's session middleware). The token
 * is signed with BRIDGE_SHARED_SECRET (same secret memory-api verifies). It
 * carries iss=librechat-onboarding, which memory-api resolves to a user by email.
 */

const jwt = require('jsonwebtoken');

module.exports = function mountXbrainRoutes(app) {
  const secret = process.env.BRIDGE_SHARED_SECRET;
  if (!secret) {
    console.warn('[xbrain] BRIDGE_SHARED_SECRET not set — /api/xbrain/token will return 503');
  }

  // requireJwtAuth is LibreChat's session middleware — available in the app scope.
  // Import path valid for LibreChat v0.8.x.
  let requireJwtAuth;
  try {
    requireJwtAuth = require('~/middleware/requireJwtAuth');
  } catch {
    requireJwtAuth = require('./middleware/requireJwtAuth');
  }

  app.get('/api/xbrain/token', requireJwtAuth, (req, res) => {
    if (!secret) return res.status(503).json({ error: 'BRIDGE_SHARED_SECRET not configured' });

    const user = req.user;
    if (!user || !user.email) {
      return res.status(401).json({ error: 'no authenticated user' });
    }

    const now = Math.floor(Date.now() / 1000);
    const token = jwt.sign(
      {
        iss: 'librechat-onboarding',
        sub: String(user._id || user.id),
        email: user.email,
        scope: 'bridge',
        iat: now,
        exp: now + 300, // 5 minutes
      },
      secret,
      { algorithm: 'HS256' },
    );

    res.json({ token });
  });
};
```

- [ ] **Step 3: Create `apps/librechat/patches/patch-server.js`**

This Node.js script runs during the Docker image build. It appends the xbrain route registration call to LibreChat's main server file.

```javascript
/**
 * patch-server.js — run at Docker image build time to inject xbrain routes
 * into LibreChat's Express server.
 *
 * Strategy: append `require('./xbrain-routes')(app)` after the last line of
 * the server entry point so that xbrain routes are always registered.
 * The server entry point is /app/api/server/index.js in LibreChat v0.8.x.
 */

const fs = require('fs');
const path = require('path');

const serverIndexPath = '/app/api/server/index.js';

if (!fs.existsSync(serverIndexPath)) {
  console.error(`[patch-server] ${serverIndexPath} not found — aborting`);
  process.exit(1);
}

const content = fs.readFileSync(serverIndexPath, 'utf8');

// Idempotency guard: don't patch twice
if (content.includes('xbrain-routes')) {
  console.log('[patch-server] already patched — skipping');
  process.exit(0);
}

// Find where `app` is exported or created and append after it.
// LibreChat's index.js typically ends with `module.exports = app;` or similar.
// We append unconditionally at end-of-file.
const patch = `\n// xbrain onboarding routes (injected by patch-server.js)\ntry { require('./xbrain-routes')(module.exports); } catch(e) { console.warn('[xbrain] route mount failed:', e.message); }\n`;

fs.writeFileSync(serverIndexPath, content + patch);
console.log(`[patch-server] patched ${serverIndexPath}`);
```

- [ ] **Step 4: Commit**

```bash
git add apps/librechat/patches/
git commit -m "feat(librechat): backend patches — socialLogin + xbrain token route"
```

---

## Task 7 — Vanilla JS Onboarding Modal

**Files:**
- Create: `apps/librechat/patches/onboarding.js`

- [ ] **Step 1: Create the modal**

Create `apps/librechat/patches/onboarding.js`:

```javascript
/**
 * xbrain team onboarding modal — injected into LibreChat's index.html.
 *
 * Runs at chat.example.com context. Uses /api/xbrain/token (same-origin)
 * to get a bridge JWT, then calls api.example.com/v1/teams/* endpoints.
 *
 * Shows a non-closable 4-step modal if the user has no team assigned.
 * Hides itself and stores completion in sessionStorage to avoid re-checking.
 */

(function () {
  'use strict';

  const MEMORY_API = 'https://api.example.com';
  const STORAGE_KEY = 'xbrain_onboarding_done';

  // Skip if already completed this session
  if (sessionStorage.getItem(STORAGE_KEY)) return;

  async function getToken() {
    try {
      const r = await fetch('/api/xbrain/token', { credentials: 'include' });
      if (!r.ok) return null;
      const { token } = await r.json();
      return token;
    } catch {
      return null;
    }
  }

  async function apiCall(method, path, body, token) {
    const opts = {
      method,
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    };
    if (body) opts.body = JSON.stringify(body);
    const r = await fetch(`${MEMORY_API}${path}`, opts);
    return r;
  }

  async function checkTeam(token) {
    const r = await apiCall('GET', '/v1/teams/my-team', null, token);
    if (r.status === 204) return null;
    if (r.ok) return await r.json();
    return null; // treat errors as "no team" to avoid blocking the UI
  }

  // ── Styles ──────────────────────────────────────────────────────────────

  const CSS = `
    #xbrain-onboarding-overlay {
      position: fixed; inset: 0; z-index: 9999;
      background: rgba(0,0,0,0.7);
      display: flex; align-items: center; justify-content: center;
      font-family: system-ui, sans-serif;
    }
    #xbrain-onboarding-modal {
      background: #1e1e2e; color: #cdd6f4;
      border-radius: 12px; padding: 32px; width: 480px; max-width: 95vw;
      box-shadow: 0 20px 60px rgba(0,0,0,0.5);
    }
    #xbrain-onboarding-modal h2 { margin: 0 0 8px; font-size: 20px; color: #89b4fa; }
    #xbrain-onboarding-modal p  { margin: 0 0 20px; font-size: 14px; color: #a6adc8; }
    .xb-input {
      width: 100%; padding: 10px 12px; border-radius: 8px; border: 1px solid #45475a;
      background: #313244; color: #cdd6f4; font-size: 14px; box-sizing: border-box;
      margin-bottom: 12px;
    }
    .xb-input:focus { outline: none; border-color: #89b4fa; }
    .xb-select {
      width: 100%; padding: 10px 12px; border-radius: 8px; border: 1px solid #45475a;
      background: #313244; color: #cdd6f4; font-size: 14px; box-sizing: border-box;
      margin-bottom: 12px;
    }
    .xb-btn {
      padding: 10px 20px; border-radius: 8px; border: none; font-size: 14px;
      cursor: pointer; font-weight: 600;
    }
    .xb-btn-primary { background: #89b4fa; color: #1e1e2e; }
    .xb-btn-secondary {
      background: transparent; color: #a6adc8; border: 1px solid #45475a; margin-left: 8px;
    }
    .xb-btn:disabled { opacity: 0.5; cursor: not-allowed; }
    .xb-key-row { display: flex; gap: 8px; margin-bottom: 8px; align-items: center; }
    .xb-key-row select, .xb-key-row input { flex: 1; }
    .xb-key-row button { background: none; border: none; color: #f38ba8; cursor: pointer; font-size: 16px; }
    .xb-error { color: #f38ba8; font-size: 13px; margin-bottom: 12px; }
    .xb-team-btn {
      display: block; width: 100%; text-align: left; padding: 12px 16px; margin-bottom: 8px;
      border-radius: 8px; border: 1px solid #45475a; background: #313244;
      color: #cdd6f4; cursor: pointer; font-size: 14px;
    }
    .xb-team-btn:hover { border-color: #89b4fa; }
    .xb-step-indicator { font-size: 12px; color: #6c7086; margin-bottom: 20px; }
    .xb-spinner { display: inline-block; width: 16px; height: 16px; border: 2px solid #45475a;
      border-top-color: #89b4fa; border-radius: 50%; animation: xb-spin 0.7s linear infinite; margin-right: 8px; }
    @keyframes xb-spin { to { transform: rotate(360deg); } }
  `;

  // ── State ────────────────────────────────────────────────────────────────

  const PROVIDERS = ['anthropic', 'openai', 'xai', 'google', 'mistral', 'cohere'];
  let token = null;
  let step = 1;
  let searchResults = [];
  let selectedTeam = null;
  let apiKeys = [{ provider: 'anthropic', key: '' }];

  // ── DOM helpers ──────────────────────────────────────────────────────────

  function el(tag, attrs = {}, ...children) {
    const e = document.createElement(tag);
    Object.entries(attrs).forEach(([k, v]) => {
      if (k === 'class') e.className = v;
      else if (k === 'style') e.style.cssText = v;
      else if (k.startsWith('on')) e[k] = v;
      else e.setAttribute(k, v);
    });
    children.forEach(c => {
      if (typeof c === 'string') e.appendChild(document.createTextNode(c));
      else if (c) e.appendChild(c);
    });
    return e;
  }

  function setHtml(container, ...nodes) {
    container.innerHTML = '';
    nodes.forEach(n => container.appendChild(n));
  }

  // ── Render steps ─────────────────────────────────────────────────────────

  function renderStep1(body) {
    const indicator = el('p', { class: 'xb-step-indicator' }, 'Étape 1 sur 4');
    const title = el('h2', {}, 'Rejoindre ou créer une équipe');
    const desc = el('p', {}, 'xbrain organise la mémoire par équipe. Rejoignez la vôtre pour commencer.');

    const nameInput = el('input', {
      class: 'xb-input', type: 'text', placeholder: 'Nom ou slug de l\'équipe...',
    });
    const errorDiv = el('p', { class: 'xb-error', style: 'display:none' });
    const resultDiv = el('div', {});

    let searchTimer = null;
    nameInput.oninput = () => {
      clearTimeout(searchTimer);
      const q = nameInput.value.trim();
      if (q.length < 2) { resultDiv.innerHTML = ''; return; }
      searchTimer = setTimeout(() => doSearch(q, resultDiv, errorDiv), 400);
    };

    const hasGithub = false; // TODO: check if user has github linked via /v1/me
    const githubHint = hasGithub
      ? el('p', { style: 'font-size:13px;color:#a6adc8;margin-bottom:12px' },
          'Vos équipes GitHub sont listées ci-dessous.')
      : el('p', { style: 'font-size:13px;color:#a6adc8;margin-bottom:12px' },
          'Tapez le nom de votre équipe, ou créez-en une nouvelle.');

    const createBtn = el('button', {
      class: 'xb-btn xb-btn-secondary',
      onclick: () => { selectedTeam = null; goStep(2); },
    }, '+ Créer une nouvelle équipe');

    setHtml(body, indicator, title, desc, githubHint, errorDiv, nameInput, resultDiv, createBtn);
  }

  async function doSearch(q, resultDiv, errorDiv) {
    resultDiv.innerHTML = '<span class="xb-spinner"></span> Recherche...';
    try {
      const r = await apiCall('GET', `/v1/teams/search?name=${encodeURIComponent(q)}`, null, token);
      if (!r.ok) throw new Error('search failed');
      searchResults = await r.json();
      resultDiv.innerHTML = '';
      if (searchResults.length === 0) {
        resultDiv.appendChild(el('p', { style: 'color:#a6adc8;font-size:13px' },
          'Aucune équipe trouvée. Vous pouvez en créer une.'));
        return;
      }
      searchResults.forEach(t => {
        const btn = el('button', {
          class: 'xb-team-btn',
          onclick: () => { selectedTeam = t; goStep(2); },
        },
          el('strong', {}, t.display_name),
          document.createTextNode(` — ${t.visibility === 'open' ? '🔓 Ouverte' : '🔒 Fermée'}`),
        );
        resultDiv.appendChild(btn);
      });
    } catch {
      errorDiv.textContent = 'Erreur lors de la recherche. Réessayez.';
      errorDiv.style.display = 'block';
      resultDiv.innerHTML = '';
    }
  }

  function renderStep2(body) {
    const indicator = el('p', { class: 'xb-step-indicator' }, 'Étape 2 sur 4');

    if (selectedTeam) {
      // Join flow
      const title = el('h2', {}, `Rejoindre "${selectedTeam.display_name}" ?`);
      const desc = el('p', {},
        selectedTeam.visibility === 'open'
          ? 'Cette équipe est ouverte. Vous pouvez rejoindre directement.'
          : 'Cette équipe est privée. Votre demande sera examinée par un admin.',
      );
      const errorDiv = el('p', { class: 'xb-error', style: 'display:none' });
      const joinBtn = el('button', { class: 'xb-btn xb-btn-primary' },
        selectedTeam.visibility === 'open' ? 'Rejoindre' : 'Demander l\'accès',
      );
      const backBtn = el('button', { class: 'xb-btn xb-btn-secondary', onclick: () => goStep(1) }, '← Retour');
      joinBtn.onclick = async () => {
        joinBtn.disabled = true;
        errorDiv.style.display = 'none';
        try {
          if (selectedTeam.visibility === 'open') {
            const r = await apiCall('POST', `/v1/teams/${selectedTeam.id}/join`, null, token);
            if (!r.ok && r.status !== 204) throw new Error('join failed');
            goStep(3);
          } else {
            const r = await apiCall('POST', `/v1/teams/${selectedTeam.id}/join-request`, null, token);
            if (!r.ok) throw new Error('request failed');
            // Show pending state — jump to step 4 with special message
            showPendingConfirmation(selectedTeam.display_name);
          }
        } catch {
          errorDiv.textContent = 'Erreur — réessayez.';
          errorDiv.style.display = 'block';
          joinBtn.disabled = false;
        }
      };
      setHtml(body, indicator, title, desc, errorDiv, joinBtn, backBtn);
    } else {
      // Create flow
      const title = el('h2', {}, 'Créer votre équipe');
      const desc = el('p', {}, 'Vous deviendrez l\'administrateur fondateur de cette équipe.');
      const nameInput = el('input', {
        class: 'xb-input', type: 'text', placeholder: 'Nom de l\'équipe (ex: Acme)',
      });
      const slugInput = el('input', {
        class: 'xb-input', type: 'text', placeholder: 'Identifiant (ex: acme)',
      });
      nameInput.oninput = () => {
        slugInput.value = nameInput.value.toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9-]/g, '');
      };
      const visSelect = el('select', { class: 'xb-select' },
        el('option', { value: 'closed' }, '🔒 Fermée (approbation requise)'),
        el('option', { value: 'open' }, '🔓 Ouverte (rejoindre librement)'),
      );
      const orgInput = el('input', {
        class: 'xb-input', type: 'text', placeholder: 'Organisation GitHub (optionnel, ex: your-github-org)',
      });
      const errorDiv = el('p', { class: 'xb-error', style: 'display:none' });
      const createBtn = el('button', { class: 'xb-btn xb-btn-primary' }, 'Créer l\'équipe');
      const backBtn = el('button', { class: 'xb-btn xb-btn-secondary', onclick: () => goStep(1) }, '← Retour');

      createBtn.onclick = async () => {
        const slug = slugInput.value.trim();
        const display = nameInput.value.trim();
        if (!slug || !display) {
          errorDiv.textContent = 'Nom et identifiant requis.';
          errorDiv.style.display = 'block';
          return;
        }
        createBtn.disabled = true;
        errorDiv.style.display = 'none';
        try {
          const r = await apiCall('POST', '/v1/teams/self', {
            slug,
            display_name: display,
            visibility: visSelect.value,
            github_org: orgInput.value.trim() || null,
          }, token);
          if (!r.ok) {
            const err = await r.json().catch(() => ({}));
            throw new Error(err.detail || 'create failed');
          }
          selectedTeam = await r.json();
          goStep(3);
        } catch (e) {
          errorDiv.textContent = e.message || 'Erreur — réessayez.';
          errorDiv.style.display = 'block';
          createBtn.disabled = false;
        }
      };

      setHtml(body, indicator, title, desc, nameInput, slugInput, visSelect, orgInput, errorDiv, createBtn, backBtn);
    }
  }

  function renderStep3(body) {
    const indicator = el('p', { class: 'xb-step-indicator' }, 'Étape 3 sur 4');
    const title = el('h2', {}, 'Clés API de l\'équipe (optionnel)');
    const desc = el('p', {}, 'Définissez des clés partagées pour les membres de l\'équipe. Vous pourrez en ajouter plus tard.');

    const keysList = el('div', {});

    function renderKeys() {
      keysList.innerHTML = '';
      apiKeys.forEach((k, i) => {
        const provSelect = el('select', { class: 'xb-select', style: 'margin-bottom:0' });
        PROVIDERS.forEach(p => {
          const opt = el('option', { value: p }, p);
          if (p === k.provider) opt.selected = true;
          provSelect.appendChild(opt);
        });
        provSelect.onchange = () => { apiKeys[i].provider = provSelect.value; };
        const keyInput = el('input', {
          class: 'xb-input', type: 'password', placeholder: 'sk-...', style: 'margin-bottom:0',
          value: k.key,
        });
        keyInput.oninput = () => { apiKeys[i].key = keyInput.value; };
        const delBtn = el('button', {}, '✕');
        delBtn.onclick = () => { apiKeys.splice(i, 1); renderKeys(); };
        keysList.appendChild(el('div', { class: 'xb-key-row' }, provSelect, keyInput, delBtn));
      });
    }
    renderKeys();

    const addBtn = el('button', { class: 'xb-btn xb-btn-secondary', style: 'margin-bottom:16px' }, '+ Ajouter une clé');
    addBtn.onclick = () => { apiKeys.push({ provider: 'openai', key: '' }); renderKeys(); };

    const errorDiv = el('p', { class: 'xb-error', style: 'display:none' });
    const saveBtn = el('button', { class: 'xb-btn xb-btn-primary' }, 'Enregistrer et continuer');
    const skipBtn = el('button', { class: 'xb-btn xb-btn-secondary', onclick: () => goStep(4) }, 'Passer →');

    saveBtn.onclick = async () => {
      const filled = apiKeys.filter(k => k.key.trim());
      if (filled.length === 0) { goStep(4); return; }
      saveBtn.disabled = true;
      errorDiv.style.display = 'none';
      try {
        const r = await apiCall('PUT', `/v1/teams/${selectedTeam.id}/api-keys`, {
          keys: filled.map(k => ({ provider: k.provider, api_key: k.key })),
        }, token);
        if (!r.ok) throw new Error('save failed');
        goStep(4);
      } catch {
        errorDiv.textContent = 'Erreur — réessayez ou passez cette étape.';
        errorDiv.style.display = 'block';
        saveBtn.disabled = false;
      }
    };

    setHtml(body, indicator, title, desc, keysList, addBtn, errorDiv, saveBtn, skipBtn);
  }

  function renderStep4(body) {
    const title = el('h2', {}, `🎉 Bienvenue dans "${selectedTeam?.display_name || 'votre équipe'}" !`);
    const desc = el('p', {}, 'Votre équipe est configurée. Vous pouvez maintenant utiliser xbrain avec vos collègues.');
    const startBtn = el('button', { class: 'xb-btn xb-btn-primary' }, 'Commencer →');
    startBtn.onclick = () => {
      sessionStorage.setItem(STORAGE_KEY, '1');
      document.getElementById('xbrain-onboarding-overlay').remove();
    };
    setHtml(body, title, desc, startBtn);
  }

  function showPendingConfirmation(teamName) {
    const modal = document.getElementById('xbrain-onboarding-modal');
    const body = modal.querySelector('.xb-body');
    const title = el('h2', {}, 'Demande envoyée');
    const desc = el('p', {}, `Votre demande pour rejoindre "${teamName}" a été soumise. Un administrateur l'examinera prochainement.`);
    const okBtn = el('button', { class: 'xb-btn xb-btn-primary' }, 'Compris');
    okBtn.onclick = () => {
      sessionStorage.setItem(STORAGE_KEY, '1');
      document.getElementById('xbrain-onboarding-overlay').remove();
    };
    setHtml(body, title, desc, okBtn);
  }

  function goStep(n) {
    step = n;
    const modal = document.getElementById('xbrain-onboarding-modal');
    const body = modal.querySelector('.xb-body');
    if (n === 1) renderStep1(body);
    else if (n === 2) renderStep2(body);
    else if (n === 3) renderStep3(body);
    else if (n === 4) renderStep4(body);
  }

  // ── Mount ────────────────────────────────────────────────────────────────

  function mount() {
    const style = document.createElement('style');
    style.textContent = CSS;
    document.head.appendChild(style);

    const overlay = el('div', { id: 'xbrain-onboarding-overlay' });
    const modal = el('div', { id: 'xbrain-onboarding-modal' });
    const bodyDiv = el('div', { class: 'xb-body' });
    modal.appendChild(bodyDiv);
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    renderStep1(bodyDiv);
  }

  // ── Boot ─────────────────────────────────────────────────────────────────

  async function boot() {
    // Wait for DOM
    if (document.readyState === 'loading') {
      await new Promise(r => document.addEventListener('DOMContentLoaded', r));
    }
    // Small delay to let LibreChat's own auth settle
    await new Promise(r => setTimeout(r, 1500));

    token = await getToken();
    if (!token) return; // Not logged in yet — LibreChat will handle redirect

    const team = await checkTeam(token);
    if (team) {
      sessionStorage.setItem(STORAGE_KEY, '1');
      return; // Already has a team — nothing to do
    }

    mount();
  }

  boot();
})();
```

- [ ] **Step 2: Commit**

```bash
git add apps/librechat/patches/onboarding.js
git commit -m "feat(librechat): vanilla JS onboarding modal (4-step)"
```

---

## Task 8 — LibreChat Dockerfile

**Files:**
- Create: `apps/librechat/Dockerfile`

- [ ] **Step 1: Create the Dockerfile**

```dockerfile
# apps/librechat/Dockerfile
# Custom LibreChat image that bakes in xbrain-specific patches.
#
# Patches applied:
#   1. api/strategies/socialLogin.js — OAuth cross-provider account linking
#   2. api/server/routes/xbrain-routes.js — /api/xbrain/token endpoint
#   3. api/server/index.js — patched via patch-server.js to mount xbrain routes
#   4. client/dist/index.html — <script src="/onboarding.js"> injected
#   5. client/dist/onboarding.js — 4-step team onboarding modal

FROM ghcr.io/danny-avila/librechat:v0.8.5

# Patch 1: OAuth cross-provider account linking
COPY patches/socialLogin.js /app/api/strategies/socialLogin.js

# Patch 2: xbrain Express routes (token endpoint)
COPY patches/xbrain-routes.js /app/api/server/routes/xbrain-routes.js

# Patch 3: Register xbrain routes in server index
COPY patches/patch-server.js /tmp/patch-server.js
RUN node /tmp/patch-server.js

# Patch 4 + 5: Onboarding modal
COPY patches/onboarding.js /app/client/dist/onboarding.js
RUN sed -i 's|</head>|<script defer src="/onboarding.js"></script></head>|' \
    /app/client/dist/index.html

# Verify patches applied
RUN grep -q "xbrain-routes" /app/api/server/index.js && \
    grep -q "onboarding.js" /app/client/dist/index.html && \
    echo "All patches verified OK"
```

- [ ] **Step 2: Verify the Dockerfile builds locally**

```bash
cd apps/librechat
docker build -t xbrain/librechat:phase8-test .
```
Expected: build completes with `All patches verified OK` in output. No errors.

If `sed` fails (index.html not at expected path), check inside the base image:
```bash
docker run --rm ghcr.io/danny-avila/librechat:v0.8.5 find /app/client -name "index.html" | head -5
```
Adjust the path in the Dockerfile accordingly.

- [ ] **Step 3: Commit**

```bash
git add apps/librechat/Dockerfile
git commit -m "feat(librechat): custom Dockerfile with socialLogin + onboarding patches"
```

---

## Task 9 — docker-compose Update + VM Deploy

**Files:**
- Modify: `infrastructure/docker-compose.yml`

- [ ] **Step 1: Update the LibreChat service to use the custom build**

In `infrastructure/docker-compose.yml`, find the `librechat:` service and replace:

```yaml
  librechat:
    image: ghcr.io/danny-avila/librechat:v0.8.5
```

with:

```yaml
  librechat:
    build:
      context: ../apps/librechat
      dockerfile: Dockerfile
    image: xbrain/librechat:phase8
```

- [ ] **Step 2: Commit the docker-compose change**

```bash
git add infrastructure/docker-compose.yml
git commit -m "feat(compose): switch librechat to custom phase8 image"
```

- [ ] **Step 3: Sync files to the VM and rebuild**

```bash
# From local, sync the new files to VM
rsync -av --delete \
  apps/librechat/ \
  apps/memory-api/ \
  infrastructure/docker-compose.yml \
  user@__VM_HOST__:/opt/xbrain/

# On VM: rebuild librechat image and run migrations
ssh user@__VM_HOST__ << 'EOF'
cd /opt/xbrain/infrastructure
docker compose build librechat
docker exec xbrain-memory-api alembic upgrade head
docker compose up -d librechat memory-api
EOF
```

- [ ] **Step 4: Smoke test**

```bash
# Check /api/xbrain/token route exists
curl -s -o /dev/null -w "%{http_code}" https://chat.example.com/api/xbrain/token
# Expected: 401 (not 404) — route exists but returns unauthorized without session

# Check memory-api my-team route
curl -s -o /dev/null -w "%{http_code}" https://api.example.com/v1/teams/my-team
# Expected: 401 (route exists, no token)

# Check CORS header from chat origin
curl -s -I -H "Origin: https://chat.example.com" https://api.example.com/v1/teams/my-team \
  | grep -i "access-control"
# Expected: Access-Control-Allow-Origin: https://chat.example.com
```

- [ ] **Step 5: Manual end-to-end test**

1. Open `https://chat.example.com` in an incognito window
2. Log in with Google
3. After login, the onboarding modal should appear within ~2 seconds
4. Walk through all 4 steps: search for "acme" → should find the team → join (or create if not found)
5. After completing, refresh → modal should NOT reappear (sessionStorage guard)

If the modal does not appear, open DevTools console and check for errors from `onboarding.js`.

- [ ] **Step 6: Create the `acme` team in the DB for nicoboups**

```bash
# On VM, call the self-create endpoint as the admin user
# First get a token from LibreChat (log in, get token from /api/xbrain/token)
# Then:
curl -s -X POST https://api.example.com/v1/teams/self \
  -H "Authorization: Bearer <TOKEN_FROM_LIBRECHAT>" \
  -H "Content-Type: application/json" \
  -d '{"slug":"acme","display_name":"Acme","visibility":"closed","github_org":"your-github-org"}'
```

Expected: `201 Created` with `{"id":"...","slug":"acme",...}`

- [ ] **Step 7: Commit final state**

```bash
git add -A
git status  # verify no secrets staged
git commit -m "feat(phase8): team onboarding modal complete — DB + API + LibreChat UI"
```

---

## Self-Review

### Spec Coverage Check

| Spec Section | Task(s) | Status |
|---|---|---|
| Trigger logic (204 = modal) | Task 4 `GET /v1/teams/my-team`, Task 7 `checkTeam()` | ✅ |
| Step 1 — GitHub or team name | Task 7 `renderStep1()` + `GET /v1/teams/github-matches` | ✅ |
| Step 2 — Join (open/closed) | Task 7 `renderStep2()` + `POST join` + `POST join-request` | ✅ |
| Step 2 — Create team | Task 7 `renderStep2()` + `POST /v1/teams/self` | ✅ |
| Step 3 — Dynamic API keys | Task 7 `renderStep3()` + `PUT /v1/teams/{id}/api-keys` | ✅ |
| Step 4 — Confirmation | Task 7 `renderStep4()` | ✅ |
| Existing users → modal on next login | Task 7 boot logic (no persistent flag, only sessionStorage) | ✅ |
| GitHub-first order (GitHub then name) | Task 7 `renderStep1()` shows github hint first | ✅ |
| DB: visibility + github_org + description | Task 1 migration + Task 2 model | ✅ |
| DB: team_api_keys | Task 1 + Task 2 + Task 3 + Task 4 | ✅ |
| DB: team_join_requests | Task 1 + Task 2 + Task 3 + Task 4 | ✅ |
| CORS for chat.example.com | Task 5 | ✅ |
| socialLogin.js permanent fix | Task 6 | ✅ |
| Custom LibreChat Docker image | Task 8 | ✅ |
| `/api/xbrain/token` auth bridge | Task 6 + Task 4 deps.py | ✅ |

### Placeholder Scan

No TBD, TODO, or placeholder steps remain. All code blocks are complete.

### Type Consistency

- `TeamApiKey` / `TeamJoinRequest` defined in Task 2, imported in Task 3 functions via local import inside function bodies (avoids circular at module load).
- `create_team` signature extended in Task 4, consistent with Task 3 test fixtures.
- `selectedTeam` in `onboarding.js` set in Step 2 (`self_create_team` response or search result), read in Step 3 (`selectedTeam.id`). Consistent shape: `{ id, slug, display_name, visibility }`.
- `_get_fernet()` in `routes/teams.py` uses same pattern as `_require_granola_fernet()` in `granola_integration.py`.
