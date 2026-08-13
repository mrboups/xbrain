"""Who is allowed to become a user here.

Until 2026-08-13 anyone on the internet could create an account in one request:
`POST /v1/auth/local/register` was unauthenticated by design, and — less
obviously — every Google or GitHub sign-in path calls `get_or_create_user`, so a
valid token from either provider minted an account on first use. Closing the
local route alone would have left two doors open.

WHAT THIS DOES NOT DO. It does not gate sign-IN. An account that exists keeps
working exactly as before, including on the very first request after the policy
flips. Only the creation of a NEW account is refused.

WHAT IT DELIBERATELY DOES NOT TOUCH. The bridge and the OpenWebUI pipeline also
create users (deps.py), resolving identities they were already trusted to
assert with the shared secret. Gating those would break LibreChat and Open WebUI
for people who already have access, which is not what "no public signup" means.
"""
from __future__ import annotations

import structlog

from app.config import settings

log = structlog.get_logger(__name__)

#: `open` — anyone who authenticates gets an account (the pre-2026-08-13
#: behaviour, still the right default for a single-tenant OSS install).
#: `closed` — only an address on the allowlist may create one; everyone else is
#: refused and pointed at the access request.
POLICIES = ("open", "closed")


def _allowlist() -> tuple[str, ...]:
    raw = (settings.SIGNUP_ALLOWLIST or "").replace(";", ",")
    return tuple(e.strip().lower() for e in raw.split(",") if e.strip())


def account_creation_allowed(email: str | None) -> bool:
    """True when `email` may become a NEW account.

    An entry starting with `@` matches a whole domain (`@example.com`), anything
    else matches one address exactly. Both are compared lowercased, because an
    allowlist that is case-sensitive is an allowlist that silently fails.

    A missing or blank email under a closed policy is refused: an identity we
    cannot name is one we cannot have decided to admit.
    """
    if settings.SIGNUP_POLICY == "open":
        return True

    addr = (email or "").strip().lower()
    if not addr:
        return False

    for entry in _allowlist():
        if entry.startswith("@"):
            if addr.endswith(entry):
                return True
        elif addr == entry:
            return True
    return False


def refusal_detail() -> str:
    """The one message every refused path returns.

    Deliberately identical everywhere and free of detail: it must not reveal
    whether an address is already registered, on the allowlist, or neither.
    """
    return (
        "This installation is not open for signup. Ask an administrator for "
        "access."
    )
