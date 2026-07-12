"""Phase 15 (EDIT-02) — EDITION router gating.

The load-bearing test here is the NEGATIVE one: an OSS boot must NOT expose the SaaS routes. Proving
"the core routes work" proves nothing — the core routes work today (ROADMAP SC#3 says exactly this).
And test_every_router_module_is_classified is the trap that keeps it true over time: a router added in a
future phase and classified nowhere FAILS the suite instead of silently shipping into every OSS install.
"""

import importlib
import pkgutil

import httpx
import pytest

import app.routes as routes_pkg
from app.config import Settings
from app.main import CORE_ROUTERS, SAAS_ONLY_ROUTERS, create_app

SAAS_ONLY_PATHS = ["/v1/waitlist", "/v1/me/external-sessions"]
# Core paths that must exist in EVERY edition. One per capability named in ROADMAP SC#2.
CORE_PATHS = [
    "/v1/healthz",
    "/v1/media/upload",
    "/.well-known/oauth-authorization-server",  # ChatGPT/Claude.ai web connector — core, never gated
]


def _paths(edition: str) -> set[str]:
    return {r.path for r in create_app(edition).routes}


def test_oss_does_not_mount_saas_routers():
    oss = _paths("oss")
    for p in SAAS_ONLY_PATHS:
        assert p not in oss, f"LEAK: {p} is mounted under EDITION=oss"


def test_saas_mounts_saas_routers():
    saas = _paths("saas")
    for p in SAAS_ONLY_PATHS:
        assert p in saas, f"{p} missing under EDITION=saas"


def test_core_routes_present_in_both_editions():
    for edition in ("oss", "saas"):
        paths = _paths(edition)
        for p in CORE_PATHS:
            assert p in paths, f"core route {p} missing under EDITION={edition}"


def test_oss_is_a_strict_subset_of_saas():
    """One image, no rebuild (D-15-05): saas ADDS routes, it never changes or removes them."""
    assert _paths("oss") < _paths("saas")


def test_every_router_module_is_classified():
    """A router in neither list would silently ship into every OSS install. Make that impossible."""
    classified = {id(r) for r, _, _ in [*CORE_ROUTERS, *SAAS_ONLY_ROUTERS]}
    unclassified = []
    for mod in pkgutil.iter_modules(routes_pkg.__path__):
        module = importlib.import_module(f"app.routes.{mod.name}")
        router = getattr(module, "router", None)
        if router is None:
            continue  # e.g. media_helpers.py — helpers, no router
        if id(router) not in classified:
            unclassified.append(mod.name)
    assert not unclassified, (
        f"router module(s) not classified in app/main.py: {sorted(unclassified)}. "
        "Add each to CORE_ROUTERS (the default — no product feature is paywalled) or, only if it is "
        "meaningless without the hosted control plane, to SAAS_ONLY_ROUTERS."
    )


@pytest.mark.parametrize("bad", ["pro", "OSS", "Saas", "", "enterprise"])
def test_settings_rejects_unknown_edition(monkeypatch, bad):
    """There is no `pro` edition (locked decision Q6). An unknown value must fail fast at boot."""
    monkeypatch.setenv("EDITION", bad)
    with pytest.raises(Exception, match="EDITION"):
        Settings()


def test_settings_edition_defaults_to_oss(monkeypatch):
    monkeypatch.delenv("EDITION", raising=False)
    assert Settings().EDITION == "oss"


@pytest.mark.asyncio
async def test_oss_returns_404_not_401_for_saas_routes():
    """404 (route not registered) is structurally different from 401 (route exists, auth rejected).

    Only 404 proves the router is ABSENT. A 401 would mean the SaaS surface shipped and merely
    happened to reject this caller. ROADMAP SC#3 requires exactly this assertion.
    """
    transport = httpx.ASGITransport(app=create_app("oss"))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.post("/v1/waitlist", json={})).status_code == 404
        assert (await c.get("/v1/me/external-sessions")).status_code == 404


@pytest.mark.asyncio
async def test_saas_reaches_the_routes_it_mounts():
    """Under saas the SAME routes must be REACHED — 422/401, never 404 (absent) and never 405
    (mounted but shadowed by an earlier core route).

    /v1/me/external-sessions' `Authorization` dependency is a required `Header(...)` with no
    default, so a request with NO Authorization header at all fails FastAPI's own parameter
    validation (422) before get_current_principal's body ever runs — that would prove the route
    is reachable but not that auth is actually enforced. Sending a syntactically-present but
    invalid bearer token skips past that parameter-validation 422 and exercises the real
    get_current_principal() rejection path (401 Invalid token), which is what "route exists,
    auth rejected" is supposed to mean here.
    """
    transport = httpx.ASGITransport(app=create_app("saas"))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.post("/v1/waitlist", json={})).status_code == 422        # validation, route exists
        r = await c.get(
            "/v1/me/external-sessions", headers={"Authorization": "Bearer not-a-real-token"}
        )
        assert r.status_code in (401, 403), r.text
