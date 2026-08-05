"""Team + TeamMember ORM."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = (
        CheckConstraint("visibility IN ('open', 'closed')", name="teams_visibility_check"),
        CheckConstraint(
            "agent_provider IN ('anthropic', 'openai', 'xai')",
            name="teams_agent_provider_check",
        ),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="closed")
    github_org: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # Phase 21 (ALIAS-01, D-21-03) — comma-separated CUSTOM mention aliases only,
    # no leading '@'. NULL/empty => env AGENT_MENTION_ALIASES defaults only. The
    # effective list a team summons on (defaults union custom, '@agent' always) is
    # resolved server-side by mention_detector.effective_aliases().
    agent_aliases: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Which provider the agent FALLS BACK to when no live browser bridge exists
    # (migration 0033). NOT the subscription: a live bridge answers through the
    # user's Claude Pro/Max session whatever this says, costs the team nothing,
    # and stays preferred. This column decides only what gets SPENT.
    #
    # One of app.services.team_keys.SUPPORTED_PROVIDERS, enforced by a CHECK
    # constraint as well as by the route — a column that can hold free text
    # eventually does, and this one is read on the path that picks a vendor to
    # bill. Defaults to 'anthropic' so every team that predates the column keeps
    # exactly today's behaviour.
    agent_provider: Mapped[str] = mapped_column(
        String(32), nullable=False, default="anthropic", server_default="anthropic"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("visibility", "closed")
        kwargs.setdefault("agent_provider", "anthropic")
        super().__init__(**kwargs)


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
    # Phase 23 — private per-member read cursor for "Catch me up" (CATCHUP-01,
    # D-23-01). NULL = never read (a first-time visitor). Set via
    # repos.teams.set_last_read using a Python datetime (NOT a DDL server_default,
    # which fires only on INSERT). This is NOT a "seen by" social read-receipt —
    # it is per-member and never exposed cross-member.
    last_read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Phase 10 — per-member soft-block (GHA-04). NULL = active member.
    # blocked_at MUST be set via Python datetime in repos.teams.block_member —
    # the DDL server_default=func.now() is consulted only on INSERT.
    blocked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    blocked_by: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
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

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("status", "pending")
        super().__init__(**kwargs)


class TeamOrgBlock(Base):
    """Phase 10 — pre-block table (GHA-05).

    Stores (team_id, github_login) entries for users who have NOT yet signed
    up with GitHub. On their first GitHub sign-in, the auto-grant flow checks
    this table BEFORE granting org-derived membership and skips matching teams.
    """

    __tablename__ = "team_org_blocks"

    team_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        primary_key=True,
    )
    github_login: Mapped[str] = mapped_column(String(256), primary_key=True)
    blocked_by: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    blocked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TeamInviteCode(Base):
    """Phase 25 — Slack/Discord-style shared join code for a team (JOINCODE-01).

    A join code is a BEARER SECRET to the team-scoped brain: whoever holds it can
    redeem it to become a member. Per D-25-01, only the sha256 HASH of the plaintext
    is ever stored (``code_hash``, UNIQUE-indexed — the join lookup is by hash, so
    there is no timing oracle on the plaintext). The plaintext (``xbi_`` +
    ``secrets.token_urlsafe(24)`` ≈ 192 bits) is generated at mint, returned to the
    admin in the mint response body EXACTLY ONCE, and NEVER persisted or logged. A
    short, non-secret ``code_prefix`` is kept for admin display only. A DB compromise
    therefore yields no usable code.

    Per D-25-02 the row carries the full revoke + expiry + max-uses lifecycle:
    ``revoked_at`` (soft-revoke; the row stays for audit), ``expires_at`` (NULL =
    never expires), ``max_uses`` (NULL = unlimited), and ``uses`` (monotonically
    incremented atomically at each redeem so a concurrent double-redeem can never
    exceed ``max_uses`` — see repos.team_invite_codes.redeem_atomic).
    """

    __tablename__ = "team_invite_codes"
    __table_args__ = (
        CheckConstraint(
            "role IN ('admin','member')", name="team_invite_codes_role_check"
        ),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    team_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    # sha256 hex is 64 chars — store ONLY this, never the plaintext (D-25-01).
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Non-secret display prefix (e.g. "xbi_ab12cd34") for admin list UIs.
    code_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="member")
    # Keep the code if its creator is deleted (audit-safe): SET NULL, not CASCADE.
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # NULL = never expires.
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # NULL = unlimited redemptions.
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Soft-revoke; the row stays for audit. NULL = live.
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("role", "member")
        super().__init__(**kwargs)
