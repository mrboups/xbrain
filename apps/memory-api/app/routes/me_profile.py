"""/v1/me/profile — the profile a person reads and writes for themselves.

    GET    /v1/me/profile          → the caller's own profile
    PATCH  /v1/me/profile          → set or clear preferred_name and/or bio
    PUT    /v1/me/profile/avatar   → point the profile at an uploaded media item
    DELETE /v1/me/profile/avatar   → remove the avatar

**There is no route here that names another user, and that is the design.**
Every handler resolves its row from `principal["user"].id` and nothing else —
no path parameter, no query parameter, no field in any body. A person cannot
read or write another person's profile because there is no way to ask. That
property is asserted structurally in tests/test_me_profile_isolation.py (the
route table is inspected for a user-identifying parameter) as well as
behaviourally, because an authorisation check that is merely correct today can
be edited; a route surface with nowhere to put someone else's id cannot.

The row is re-read from the database on every request rather than trusted from
the principal. `get_current_principal` loads the user for authentication; using
its copy of `preferred_name` here would return a value the caller may have just
changed, and would write from a snapshot instead of the current row.

Mounted in CORE_ROUTERS (app/main.py). A profile is not a paid feature, and the
gating test in tests/test_edition_gating.py fails for any router classified in
neither list — this one is core in every edition, OSS included.

Audit: profile edits are logged via structlog, not written to `audit_log`.
Every existing write_audit() call carries a team_scope, and a rename is not
team-scoped — it is one person editing their own account. Inventing a scope to
satisfy the column would put a fiction in the audit trail. The avatar PUT does
have a real scope but is left consistent with the other three rather than being
the odd one out.
"""
from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_current_principal, get_session
from app.models.user import User
from app.services.user_profile import (
    normalize_bio,
    normalize_preferred_name,
    profile_payload,
)

log = structlog.get_logger(__name__)
router = APIRouter()


# ── Authorisation ───────────────────────────────────────────────────────────


def _require_self(principal: dict[str, Any]) -> Any:
    """Return the caller's OWN user object, or refuse.

    `user_api_token` is accepted alongside `user`: an xbt_ token is minted by a
    person for themselves and represents the same account, exactly as it does for
    POST /v1/me/link-github. A `bridge` principal is a service, not a person, and
    has no profile to read — it is refused rather than silently given an empty one.

    This helper takes the principal and returns the identity in it. It has no
    parameter through which a different user could be requested, which is the
    whole reason the profile routes have no authorisation branch of their own.
    """
    if principal.get("kind") not in ("user", "user_api_token"):
        raise HTTPException(403, "User authentication required")
    user = principal.get("user")
    if user is None:
        raise HTTPException(403, "User authentication required")
    return user


async def _load_own_row(session: AsyncSession, principal: dict[str, Any]) -> User:
    """Load the caller's own `users` row, fresh.

    `session.get(User, pk)` takes a primary key and nothing else — there is no
    filter expression a future edit could widen into "or this other id". The pk
    is always the authenticated principal's.
    """
    identity = _require_self(principal)
    row = await session.get(User, identity.id)
    if row is None:
        # The principal authenticated against a row that no longer exists (an
        # account deleted mid-session). 404 rather than 500.
        raise HTTPException(404, "Profile not found")
    return row


# ── Bodies ──────────────────────────────────────────────────────────────────


class ProfilePatchBody(BaseModel):
    """Any subset of the editable fields. Absent means "leave alone".

    `extra="forbid"` is load-bearing here, not hygiene: it is what makes
    `PATCH {"email": "..."}` or `PATCH {"user_id": "<someone else>"}` a 422
    instead of a silently ignored field that a client author might believe took
    effect. The only two writable columns are the two declared below.

    Three inputs, two outcomes: an ABSENT key means "do not touch this column",
    while both `null` and `""` mean "clear it". The handler tells absence apart
    from an explicit null through `model_fields_set`, which pydantic populates
    with the keys the client actually sent — a default of None alone could not
    distinguish the two.

    Validation runs here so a bad value is refused with pydantic's standard 422
    envelope — `detail[].loc` names the offending field, which is what a profile
    form needs to put the message next to the right input.
    """

    model_config = ConfigDict(extra="forbid")

    preferred_name: str | None = None
    bio: str | None = None

    @field_validator("preferred_name")
    @classmethod
    def _check_preferred_name(cls, v: Any) -> str | None:
        return normalize_preferred_name(v)

    @field_validator("bio")
    @classmethod
    def _check_bio(cls, v: Any) -> str | None:
        return normalize_bio(v)


# ── GET /v1/me/profile ──────────────────────────────────────────────────────


@router.get("/me/profile")
async def get_my_profile(
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return the caller's own profile.

    Includes `label` — the string chat actually renders for this person, resolved
    through the one ladder — so the profile screen and the chat bubble can never
    disagree, and `avatar_url`, minted fresh on every read (the token expires; a
    stored URL is a link that silently stops working).

    Auth: the caller's own principal. There is no parameter for another user.
    """
    row = await _load_own_row(session, principal)
    return profile_payload(row)


# ── PATCH /v1/me/profile ────────────────────────────────────────────────────


@router.patch("/me/profile")
async def patch_my_profile(
    body: ProfilePatchBody,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Set or clear `preferred_name` and/or `bio` on the caller's own profile.

    Sending `""` (or `null`) for a field CLEARS it: the column goes to NULL and
    the label ladder falls back to the provider's name, then the email local part.
    That is the undo path for a rename, and it needs no admin and no support.

    Omitting a field leaves it untouched — a client that only edits the bio does
    not have to send the name back, and cannot blank it by forgetting to.

    Returns the full profile after the change, so the client re-renders from the
    server's version of the truth rather than from what it hoped it wrote.

    Auth: the caller's own principal. There is no parameter for another user.
    """
    row = await _load_own_row(session, principal)

    sent = body.model_fields_set
    if "preferred_name" in sent:
        row.preferred_name = body.preferred_name
    if "bio" in sent:
        row.bio = body.bio

    await session.commit()
    log.info(
        "me.profile.updated",
        user_id=str(row.id),
        fields=sorted(sent),
        # The VALUES are deliberately absent: a log line is not a place to copy a
        # person's chosen name or their bio. Which fields changed is enough to
        # investigate with.
        preferred_name_cleared=("preferred_name" in sent and row.preferred_name is None),
        bio_cleared=("bio" in sent and row.bio is None),
    )
    return profile_payload(row)
