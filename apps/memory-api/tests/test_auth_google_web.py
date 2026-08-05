"""The popup-free Google sign-in flow (app/routes/auth_google_web.py).

WHAT THIS GATE IS FOR. Signing in to the installed PWA on an iPhone took about
twenty-three attempts, because Google Identity Services defaults to a POPUP and a
popup opened by a standalone web app on iOS is a detached context with its own
storage — Google never finds a session there. The remedy is a top-level
authorization-code flow that runs in the app's own context, and the remedy has
two properties that must not rot:

  1. IT IS OFF UNTIL AN OPERATOR TURNS IT ON. The callback URI has to be
     registered by hand as an Authorized redirect URI on the Google client.
     Shipping a client that switched to this flow before that registration
     existed would replace "works on the twenty-third try" with "fails every
     try". Every assertion about the disabled state below exists for that.
  2. THE CREDENTIAL LEAVES ON A FRAGMENT, NEVER A QUERY. A query parameter is
     written to hosting access logs and travels in a Referer; a fragment is
     neither sent to a server nor logged.

No database is touched by any of this, so none of these tests are Docker-gated —
they run in every environment, which is what a security gate on a sign-in path
has to do. English-only.
"""

from __future__ import annotations

import pathlib
import urllib.parse

import httpx
import pytest

from app.config import settings
from app.routes import auth_google_web as mod

# Deliberately an https external URL: the state cookie is marked Secure off the
# back of it, and the test client below uses an https base_url so httpx will
# actually carry a Secure cookie. A test that quietly ran the flow over http
# would never exercise the flag that matters in production.
EXTERNAL = "https://api.test.example"
RETURN_URL = "https://app.test.example/app/"
CLIENT_ID = "test-client-id.apps.googleusercontent.com"
CALLBACK = f"{EXTERNAL}/v1/auth/google/callback"


def _client() -> httpx.AsyncClient:
    from app.main import create_app

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app("oss")),
        base_url="https://test",
        follow_redirects=False,
    )


@pytest.fixture
def enabled(monkeypatch):
    """Everything an operator has to set, set."""
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr(settings, "GOOGLE_WEB_SIGNIN_RETURN_URL", RETURN_URL)
    monkeypatch.setattr(settings, "MEMORY_API_EXTERNAL_URL", EXTERNAL)


@pytest.fixture
def disabled(monkeypatch):
    """The shipped default: a client id, but nowhere to come back to."""
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr(settings, "GOOGLE_WEB_SIGNIN_RETURN_URL", "")
    monkeypatch.setattr(settings, "MEMORY_API_EXTERNAL_URL", EXTERNAL)


class _FakeTokenEndpoint:
    """Stands in for Google's token endpoint, and records what was sent to it."""

    def __init__(self, status=200, payload=None, raise_transport=False, bad_json=False):
        self.status = status
        self.payload = payload if payload is not None else {"id_token": "header.payload.sig"}
        self.raise_transport = raise_transport
        self.bad_json = bad_json
        self.sent: dict | None = None
        self.url: str | None = None

    def install(self, monkeypatch):
        endpoint = self

        class FakeResponse:
            status_code = endpoint.status

            def json(self):
                if endpoint.bad_json:
                    raise ValueError("not json")
                return endpoint.payload

            @property
            def text(self):
                return "the-client-secret-must-not-reach-a-redirect"

        class FakeClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, data=None, **kw):
                if endpoint.raise_transport:
                    raise httpx.ConnectError("boom")
                endpoint.url = url
                endpoint.sent = dict(data or {})
                return FakeResponse()

        # The module's OWN `httpx` name is replaced, not an attribute on the real
        # httpx module: patching the latter would also replace the client this
        # test file drives the app with, and the test would be testing the fake.
        class FakeHttpx:
            AsyncClient = FakeClient
            HTTPError = httpx.HTTPError

        monkeypatch.setattr(mod, "httpx", FakeHttpx)
        return self


def _fragment(location: str) -> dict[str, str]:
    """The fragment of a redirect, parsed. Asserts there IS one."""
    assert "#" in location, f"no fragment on {location}"
    return dict(urllib.parse.parse_qsl(location.split("#", 1)[1]))


async def _start(client) -> tuple[httpx.Response, str]:
    res = await client.get("/v1/auth/google/start")
    state = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(res.headers["location"]).query))[
        "state"
    ]
    return res, state


# --------------------------------------------------------------------------
# 1. Disabled by default — the property that keeps this from being a regression
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_config_reports_disabled_until_an_operator_configures_it(disabled):
    async with _client() as c:
        res = await c.get("/v1/auth/google/web-config")
    assert res.status_code == 200
    assert res.json() == {"enabled": False}, (
        "the client draws the GIS popup button off a false here; a true it cannot "
        "act on sends people into a redirect_uri_mismatch"
    )


@pytest.mark.asyncio
async def test_web_config_reports_enabled_once_it_is(enabled):
    async with _client() as c:
        res = await c.get("/v1/auth/google/web-config")
    assert res.json() == {"enabled": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/auth/google/start", "/v1/auth/google/callback"])
async def test_both_endpoints_refuse_while_unconfigured(disabled, path):
    async with _client() as c:
        res = await c.get(path)
    assert res.status_code == 503
    assert "GOOGLE_WEB_SIGNIN_RETURN_URL" in res.json()["detail"], (
        "the refusal must name what is missing — an operator reading a log needs the "
        "variable, not 'not configured'"
    )


@pytest.mark.asyncio
async def test_a_missing_client_secret_alone_disables_the_flow(enabled, monkeypatch):
    # Without it there is no code exchange, so advertising the flow would be a lie.
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "")
    async with _client() as c:
        res = await c.get("/v1/auth/google/web-config")
    assert res.json() == {"enabled": False}


# --------------------------------------------------------------------------
# 2. /start — the authorization request itself
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_redirects_to_google_with_a_well_formed_request(enabled):
    async with _client() as c:
        res = await c.get("/v1/auth/google/start")

    assert res.status_code == 302
    url = urllib.parse.urlparse(res.headers["location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == mod.GOOGLE_AUTH_ENDPOINT
    q = dict(urllib.parse.parse_qsl(url.query))

    assert q["client_id"] == CLIENT_ID
    assert q["response_type"] == "code", "the implicit flow would put a token in a URL"
    assert q["redirect_uri"] == CALLBACK
    assert "openid" in q["scope"] and "email" in q["scope"]
    assert "drive" not in q["scope"], "this flow authenticates; it must not ask for data"
    assert "access_type" not in q, (
        "offline access mints a refresh token this flow has no use for and would "
        "then have to store"
    )
    assert q["prompt"] == "select_account", (
        "a silent sign-in on a shared phone signs somebody in as whoever used it last"
    )
    assert len(q["state"]) >= 32


@pytest.mark.asyncio
async def test_start_binds_the_flow_to_this_browser_with_a_cookie(enabled):
    async with _client() as c:
        res = await c.get("/v1/auth/google/start")

    state = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(res.headers["location"]).query))[
        "state"
    ]
    raw = res.headers["set-cookie"]
    assert raw.startswith(f"{mod.STATE_COOKIE}={state}"), (
        "the cookie must carry the SAME value as `state` — comparing them at the "
        "callback is the whole CSRF defence"
    )
    assert "HttpOnly" in raw, "script-readable is a state value an attacker's page can lift"
    assert "Secure" in raw, "an https deployment must not put this on the wire in the clear"
    assert "samesite=lax" in raw.lower(), (
        "Strict drops the cookie on Google's top-level redirect back, which is the one "
        "request it exists for"
    )
    assert "Path=/v1/auth/google" in raw, "a cookie on / rides along on every API call"
    assert f"Max-Age={mod.STATE_TTL_S}" in raw


@pytest.mark.asyncio
async def test_the_cookie_is_not_marked_secure_on_a_plain_http_deployment(enabled, monkeypatch):
    # A Secure cookie is never stored over http, so a local http deployment would
    # fail state validation on every attempt.
    monkeypatch.setattr(settings, "MEMORY_API_EXTERNAL_URL", "http://localhost:8000")
    async with _client() as c:
        res = await c.get("/v1/auth/google/start")
    assert "Secure" not in res.headers["set-cookie"]


@pytest.mark.asyncio
async def test_two_starts_do_not_share_a_state(enabled):
    async with _client() as c:
        _, first = await _start(c)
        _, second = await _start(c)
    assert first != second, "a reused state is a state worth stealing"


# --------------------------------------------------------------------------
# 3. /callback — the CSRF gate
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_without_the_cookie_hands_back_no_credential(enabled, monkeypatch):
    token = _FakeTokenEndpoint().install(monkeypatch)
    async with _client() as c:
        res = await c.get("/v1/auth/google/callback", params={"code": "abc", "state": "forged"})

    assert res.status_code == 303
    assert _fragment(res.headers["location"]) == {
        mod.ERROR_FRAGMENT_KEY: "state_mismatch"
    }
    assert token.sent is None, "an unbound callback must never reach the token endpoint"


@pytest.mark.asyncio
async def test_callback_with_a_state_that_is_not_the_cookie_is_refused(enabled, monkeypatch):
    token = _FakeTokenEndpoint().install(monkeypatch)
    async with _client() as c:
        await c.get("/v1/auth/google/start")  # cookie jar now holds a real state
        res = await c.get(
            "/v1/auth/google/callback", params={"code": "abc", "state": "not-the-cookie"}
        )

    assert _fragment(res.headers["location"])[mod.ERROR_FRAGMENT_KEY] == "state_mismatch"
    assert token.sent is None


@pytest.mark.asyncio
async def test_callback_without_a_code_is_refused_after_the_state_matches(enabled, monkeypatch):
    token = _FakeTokenEndpoint().install(monkeypatch)
    async with _client() as c:
        _, state = await _start(c)
        res = await c.get("/v1/auth/google/callback", params={"state": state})

    assert _fragment(res.headers["location"])[mod.ERROR_FRAGMENT_KEY] == "no_code"
    assert token.sent is None


@pytest.mark.asyncio
async def test_a_cancelled_sign_in_says_so_and_is_not_an_error(enabled, monkeypatch):
    _FakeTokenEndpoint().install(monkeypatch)
    async with _client() as c:
        _, state = await _start(c)
        res = await c.get(
            "/v1/auth/google/callback", params={"error": "access_denied", "state": state}
        )
    assert _fragment(res.headers["location"])[mod.ERROR_FRAGMENT_KEY] == "denied"


# --------------------------------------------------------------------------
# 4. /callback — the happy path, and what it may put in a URL
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_completed_flow_returns_the_id_token_on_the_fragment(enabled, monkeypatch):
    id_token = "eyJhbGciOi.eyJzdWIiOi.signature+with/reserved=chars"
    token = _FakeTokenEndpoint(payload={"id_token": id_token}).install(monkeypatch)

    async with _client() as c:
        _, state = await _start(c)
        res = await c.get("/v1/auth/google/callback", params={"code": "the-code", "state": state})

    assert res.status_code == 303
    location = res.headers["location"]
    before_fragment, fragment = location.split("#", 1)

    assert before_fragment == RETURN_URL, "the app is the only place this may send anybody"
    assert _fragment(location) == {mod.CREDENTIAL_FRAGMENT_KEY: id_token}
    assert id_token not in before_fragment, (
        "a credential before the '#' is a credential in the hosting access log"
    )
    assert "?" not in before_fragment, "no query parameters at all on the way back"
    # The reserved characters survived the round trip, which is what percent-encoding
    # the value is for — a raw '+' would come back as a space.
    assert urllib.parse.quote(id_token, safe="") in fragment

    assert token.url == mod.GOOGLE_TOKEN_ENDPOINT
    assert token.sent["grant_type"] == "authorization_code"
    assert token.sent["code"] == "the-code"
    assert token.sent["client_secret"] == "test-client-secret"
    assert token.sent["redirect_uri"] == CALLBACK, (
        "Google rejects an exchange whose redirect_uri differs by a byte from the "
        "one sent at /start"
    )


@pytest.mark.asyncio
async def test_the_state_cookie_is_cleared_once_it_has_been_spent(enabled, monkeypatch):
    _FakeTokenEndpoint().install(monkeypatch)
    async with _client() as c:
        _, state = await _start(c)
        res = await c.get("/v1/auth/google/callback", params={"code": "x", "state": state})
    raw = res.headers["set-cookie"]
    assert f"{mod.STATE_COOKIE}=" in raw and (
        'Max-Age=0' in raw or "1970" in raw
    ), f"the spent state must not stay in the jar: {raw}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "slug"),
    [
        (_FakeTokenEndpoint(status=400), "token_exchange_failed"),
        (_FakeTokenEndpoint(payload={}), "no_id_token"),
        (_FakeTokenEndpoint(bad_json=True), "token_response_unparseable"),
        (_FakeTokenEndpoint(raise_transport=True), "token_endpoint_unreachable"),
    ],
)
async def test_every_exchange_failure_returns_to_the_app_rather_than_stranding_anybody(
    enabled, monkeypatch, endpoint, slug
):
    # A standalone web app has no address bar. An error page served from the API
    # host is a dead end with no way back to the app.
    endpoint.install(monkeypatch)
    async with _client() as c:
        _, state = await _start(c)
        res = await c.get("/v1/auth/google/callback", params={"code": "x", "state": state})

    assert res.status_code == 303
    assert res.headers["location"].startswith(RETURN_URL + "#")
    assert _fragment(res.headers["location"]) == {mod.ERROR_FRAGMENT_KEY: slug}


@pytest.mark.asyncio
async def test_a_failed_exchange_never_echoes_googles_response_body(enabled, monkeypatch):
    # Google's error bodies can quote the request back, client_secret included.
    _FakeTokenEndpoint(status=400).install(monkeypatch)
    async with _client() as c:
        _, state = await _start(c)
        res = await c.get("/v1/auth/google/callback", params={"code": "x", "state": state})
    assert "client_secret" not in res.headers["location"]
    assert "must-not-reach-a-redirect" not in res.headers["location"]


# --------------------------------------------------------------------------
# 5. The one string an operator has to register by hand
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_registered_redirect_uri_is_one_string_used_in_both_places(enabled, monkeypatch):
    # The URI at /start and the URI in the exchange must be the same string, and
    # both must be the one an operator was told to register. Two builders is how
    # they drift.
    token = _FakeTokenEndpoint().install(monkeypatch)
    async with _client() as c:
        res, state = await _start(c)
        await c.get("/v1/auth/google/callback", params={"code": "x", "state": state})

    at_start = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(res.headers["location"]).query))[
        "redirect_uri"
    ]
    assert at_start == token.sent["redirect_uri"] == mod.redirect_uri() == CALLBACK


def test_the_state_comparison_is_constant_time():
    # A source scan because the property is not observable from a response: `==`
    # and compare_digest agree on every input, and differ only in how long they
    # take to disagree. A short-circuiting compare over a secret leaks its prefix
    # a byte at a time, and swapping one in would otherwise pass every test above.
    text = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    assert "secrets.compare_digest(state, cookie)" in text, (
        "the state/cookie comparison must go through secrets.compare_digest"
    )
    assert "state == cookie" not in text and "cookie == state" not in text


def test_the_return_url_may_not_be_chosen_by_a_caller():
    # No `next=` parameter anywhere: an operator-configured destination cannot be
    # an open redirect, and a caller-supplied one is one by default.
    text = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    assert "query_params.get(\"next\")" not in text
    assert "request.query_params" in text  # it does read code/state/error
    for caller_controlled in ("return_to=", "redirect_to=", "next="):
        assert f'query_params.get("{caller_controlled.rstrip("=")}")' not in text
