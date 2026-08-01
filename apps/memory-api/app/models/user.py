"""User ORM — maps OIDC `sub` claim to internal user id."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    source_user_id: Mapped[str] = mapped_column(String(256), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(256), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Phase 5 plan 05-02 — GitHub account linking (optional, nullable)
    # Populated via POST /v1/me/link-github or on first GitHub OAuth login.
    github_username: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    github_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, unique=True)
    # Phase 10 — orphan-row soft-merge target. Set when this row was the orphan
    # that got merged into another user (Google ↔ GitHub auto-merge, GHA-06).
    # Sign-in flow follows the pointer via repos.users.follow_merge_pointer.
    merged_into_user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Phase 12 — GitHub App user-to-server token storage (Fernet-encrypted at rest).
    # Populated/rotated by app/services/github_user_token.py (Plan 12-06). The
    # `_enc` suffix is mandatory naming so future readers can tell at a glance
    # the column holds Fernet base64 bytes, never raw token text.
    # github_access_token_expires_at: ~8h after issue (GitHub default).
    # github_refresh_expires_at: ~6 months after issue (GitHub default).
    # github_access_token_hash: HMAC-SHA256(FERNET_KEY, plaintext) — used by
    # deps.py for O(log n) lookup of the user owning an incoming ghu_ token.
    # See app/services/token_crypto.py:token_lookup_hash (Plan 12-06 M-5 fix).
    github_access_token_enc: Mapped[str | None] = mapped_column(String, nullable=True)
    github_refresh_token_enc: Mapped[str | None] = mapped_column(String, nullable=True)
    github_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    github_refresh_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # REVISION 2 (Plan 12-06 M-5) — indexed via partial idx_users_github_access_token_hash
    # (migration 0019). Always rotated alongside github_access_token_enc.
    github_access_token_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    # === Profile (migration 0030) ===
    # preferred_name is the ONLY name column a user may write, and it is written
    # exclusively by PATCH /v1/me/profile for the caller's own row. display_name
    # above stays the identity provider's value and is never overwritten by a user
    # edit — clearing preferred_name restores it (see app/services/user_label.py
    # for the precedence ladder both chat and notifications resolve through).
    preferred_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    bio: Mapped[str | None] = mapped_column(String(400), nullable=True)
    # No ForeignKey by design — the media item lives in the pluggable MemoryProvider,
    # which only reaches Postgres `memory_items` under the `native` backend. See the
    # 0030_user_profile docstring. Existence is validated at write time instead.
    avatar_media_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    # The team_scope the avatar was uploaded under — required to mint a readable
    # signed URL for a person who belongs to several teams.
    avatar_media_team_scope: Mapped[str | None] = mapped_column(String(64), nullable=True)
