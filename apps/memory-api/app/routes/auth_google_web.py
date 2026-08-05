"""Google sign-in for a web app that has no usable popup.

THE BUG THIS FILE EXISTS FOR. Signing in to the installed PWA on an iPhone took
roughly twenty-three attempts: Google asked to verify the device on every one,
then answered "Something went wrong", then a bare HTTP 400. The cause is not in
our code at all — it is WHERE our code asks the question. `app-site` mounts the
Google Identity Services button with no `ux_mode`, and GIS defaults to `popup`.
In a browser tab that popup is a normal window that shares the browser's cookie
jar, so Google recognises the account and the device. In a home-screen web app
(`display: standalone` in the manifest) it is not: iOS opens it as a detached
in-app browser sheet with its own storage, Google finds no session there, demands
verification every time, and the verification is what fails.

So the fix is not a different button. It is a sign-in that happens in the app's
OWN browsing context, where a Google session cookie can be set once and found
again next time:

    /app/  --(top-level navigation)-->  GET /v1/auth/google/start
           --(302)-->  accounts.google.com  --(302 back)-->  GET /v1/auth/google/callback
           --(303)-->  https://.../app/#google_credential=<Google ID token>

The last hop lands INSIDE the manifest scope, which is the documented mechanism
by which iOS hands a navigation back to an installed web app rather than leaving
it in the sheet.

WHY THE CREDENTIAL AND NOT THE SESSION. The fragment carries the Google ID token,
not an `xbt_` — the client then exchanges it at POST /v1/me/api-token exactly as
the popup callback already did. That keeps this module out of the identity
business entirely (no user upsert, no token minting, no second way an account can
come into existence) and puts the SHORTER-lived of the two credentials in the URL:
a Google ID token expires within the hour and is bound to our client id, while an
`xbt_` is long-lived. A fragment is never sent to a server and never appears in a
Referer; app.js strips it before anything else runs.

WHY `state` IS ALSO A COOKIE. A signed-but-unbound state proves the flow started
here, which is not the property that matters — login CSRF needs the flow to have
started in THIS browser. `/start` sets a short-lived HttpOnly cookie holding the
same random value it puts in `state`, and `/callback` refuses any pair that does
not match. Google's redirect back is a top-level GET, so a SameSite=Lax cookie is
sent with it; nothing here depends on third-party cookies, which is the assumption
that broke the popup in the first place.

DISABLED BY DEFAULT, AND THAT IS LOAD-BEARING. `GOOGLE_WEB_SIGNIN_RETURN_URL` is
empty unless an operator sets it, and while it is empty this whole flow answers
`{"enabled": false}` and 503s. That is not timidity: the redirect URI below MUST
be registered on the Google client by hand, and a client that switched to this
flow before the registration existed would trade a sign-in that works on the
twenty-third try for one that fails on every try. The client asks
/auth/google/web-config first and keeps the popup until the answer is yes.
"""

from __future__ import annotations

import secrets
import urllib.parse

import httpx
import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from app.config import settings

router = APIRouter()
log = structlog.get_logger()

# The cookie that binds a callback to the browser that started the flow.
# Path-scoped to this flow so it is never sent anywhere else, and named with the
# `xb_` prefix the rest of the deployment uses.
STATE_COOKIE = "xb_google_web_state"

# Long enough for somebody to pick an account and pass a verification challenge,
# short enough that a stolen state is worthless by the time it is used.
STATE_TTL_S = 600

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

# What the browser is sent back to. `google_credential` is the success case;
# `google_error` is every failure, so the app can put a person back on the
# sign-in card with a sentence instead of stranding them on an API host with no
# way back. Both are FRAGMENT keys — never query parameters, which would put a
# credential in an access log.
CREDENTIAL_FRAGMENT_KEY = "google_credential"
ERROR_FRAGMENT_KEY = "google_error"


def redirect_uri() -> str:
    """The URI Google redirects back to. MUST be registered on the OAuth client.

    Built from MEMORY_API_EXTERNAL_URL exactly the way admin_drive.py builds its
    own, so a deployment that moves hosts moves both together.
    """
    return f"{settings.MEMORY_API_EXTERNAL_URL.rstrip('/')}/v1/auth/google/callback"


def is_enabled() -> bool:
    """True when an operator has configured every part this flow needs.

    All three are required and none has a safe default: without the client secret
    there is no code exchange, and without the return URL there is nowhere to send
    a person afterwards. Reported as one boolean because a client cannot act on
    which of them is missing — that belongs in the server log.
    """
    return bool(
        settings.GOOGLE_CLIENT_ID
        and settings.GOOGLE_CLIENT_SECRET
        and settings.GOOGLE_WEB_SIGNIN_RETURN_URL
    )


def _return_to(fragment_key: str, value: str) -> str:
    """The configured return URL with one fragment key on it.

    The base is operator configuration, never anything a caller sent, so there is
    no open redirect to guard against here — and deliberately no `next=` style
    parameter that would introduce one.
    """
    base = settings.GOOGLE_WEB_SIGNIN_RETURN_URL.split("#")[0]
    return f"{base}#{fragment_key}={urllib.parse.quote(value, safe='')}"


def _disabled() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "detail": (
                "Google web sign-in is not configured. Set GOOGLE_CLIENT_ID, "
                "GOOGLE_CLIENT_SECRET and GOOGLE_WEB_SIGNIN_RETURN_URL, and register "
                "the callback as an Authorized redirect URI on the OAuth client."
            )
        },
    )


@router.get("/auth/google/web-config")
async def google_web_config() -> JSONResponse:
    """Whether a browser may use the redirect flow instead of the GIS popup.

    Mirrors GET /v1/push/config: one boolean, no secrets, and the honest answer
    when nothing is configured. The client reads it BEFORE it decides which
    button to draw, so an unconfigured deployment keeps the popup rather than
    walking somebody into a redirect_uri_mismatch.
    """
    return JSONResponse({"enabled": is_enabled()})


@router.get("/auth/google/start")
async def google_web_start() -> Response:
    """Begin the authorization-code flow as a TOP-LEVEL navigation."""
    if not is_enabled():
        return _disabled()

    state = secrets.token_urlsafe(32)
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        # openid+email is the whole ask. No Drive, no offline access: this flow
        # authenticates a person and stores nothing on their behalf, so a refresh
        # token would be a credential we have no use for and would have to keep.
        "scope": "openid email profile",
        "state": state,
        # An account chooser rather than a silent sign-in. On a shared phone the
        # silent path signs somebody in as whoever used it last, and in a team
        # product that is the wrong brain.
        "prompt": "select_account",
    }
    response = RedirectResponse(
        f"{GOOGLE_AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}",
        status_code=302,
    )
    response.set_cookie(
        STATE_COOKIE,
        state,
        max_age=STATE_TTL_S,
        # Scoped to this flow. A cookie on "/" would ride along on every API call
        # the app makes for the next ten minutes for no reason.
        path="/v1/auth/google",
        httponly=True,
        # Google's redirect back is a top-level GET, which Lax allows. `strict`
        # would drop the cookie on exactly the request that needs it.
        samesite="lax",
        secure=settings.MEMORY_API_EXTERNAL_URL.startswith("https://"),
    )
    return response


@router.get("/auth/google/callback")
async def google_web_callback(request: Request) -> Response:
    """Google's redirect back. Exchanges the code and hands the ID token to the app.

    Every failure leaves by the same door as a success — a redirect to the app
    with `google_error` on it — because the alternative is an error page on an
    API host inside a standalone web app that has no back button.
    """
    if not is_enabled():
        return _disabled()

    def fail(slug: str, **fields) -> RedirectResponse:
        log.warning("google_web_signin.failed", reason=slug, **fields)
        out = RedirectResponse(_return_to(ERROR_FRAGMENT_KEY, slug), status_code=303)
        out.delete_cookie(STATE_COOKIE, path="/v1/auth/google")
        return out

    # Google's own refusal (the person cancelled, consent denied). Reported as
    # its own slug so "you closed the window" never reads as "the server broke".
    if request.query_params.get("error"):
        return fail("denied")

    state = request.query_params.get("state") or ""
    cookie = request.cookies.get(STATE_COOKIE) or ""
    # compare_digest, not ==: this is a secret comparison, and a short-circuiting
    # one leaks its prefix through timing.
    if not state or not cookie or not secrets.compare_digest(state, cookie):
        return fail("state_mismatch")

    code = request.query_params.get("code") or ""
    if not code:
        return fail("no_code")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            token_res = await client.post(
                GOOGLE_TOKEN_ENDPOINT,
                data={
                    "code": code,
                    "client_id": settings.GOOGLE_CLIENT_ID,
                    "client_secret": settings.GOOGLE_CLIENT_SECRET,
                    # Must be byte-identical to the one sent at /start, or Google
                    # rejects the exchange. Both read the same builder.
                    "redirect_uri": redirect_uri(),
                    "grant_type": "authorization_code",
                },
            )
    except httpx.HTTPError:
        return fail("token_endpoint_unreachable")

    if token_res.status_code != 200:
        # The body can carry the client secret back in an error echo, so the
        # status is logged and the body is not.
        return fail("token_exchange_failed", status=token_res.status_code)

    try:
        id_token = (token_res.json() or {}).get("id_token") or ""
    except ValueError:
        return fail("token_response_unparseable")
    if not id_token:
        return fail("no_id_token")

    # NOT verified here on purpose. This token came back over TLS from Google's
    # token endpoint in exchange for a code we asked for; re-checking its
    # signature would add a JWKS fetch to the redirect and prove nothing new. The
    # exchange it is about to be used for — POST /v1/me/api-token — verifies it
    # against the same client id before it mints anything, which is where the
    # check belongs and where it already exists.
    out = RedirectResponse(_return_to(CREDENTIAL_FRAGMENT_KEY, id_token), status_code=303)
    out.delete_cookie(STATE_COOKIE, path="/v1/auth/google")
    log.info("google_web_signin.completed")
    return out
