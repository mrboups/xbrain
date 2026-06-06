"""OAuth 2.1 authorize + consent flow (quick-260604-glo).

Mounted WITHOUT the /v1 prefix. Three routes:

  GET  /oauth/authorize        — entry point Claude.ai redirects the browser to.
                                  Validates the client + that redirect_uri is
                                  registered for it (open-redirect defense),
                                  requires S256, normalizes resource, signs the
                                  in-flight params into a short-lived state token,
                                  then redirects into GitHub sign-in.
  GET  /oauth/github-callback  — GitHub returns here. Exchanges the code, resolves
                                  the xbrain user (reusing auth_github helpers),
                                  lists teams, auto-skips selection for a single
                                  team, else renders the English-only consent page.
  POST /oauth/authorize        — consent submit. Re-validates redirect_uri,
                                  verifies the user is a member of the chosen
                                  team, creates a one-time PKCE-bound auth code,
                                  and 302-redirects to the ORIGINAL registered
                                  Claude.ai redirect_uri with ?code=...&state=...

The in-flight state is an HS256 JWT signed with BRIDGE_SHARED_SECRET (no DB row
needed); it carries the authorize params and, after github-callback, the
resolved user_id. The GitHub leg uses memory-api's OWN callback
(/oauth/github-callback) — never Claude.ai's redirect_uri.
"""
from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import urlencode

import structlog
from authlib.jose import jwt as authlib_jwt
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import oauth_store
from app.auth.oauth_tokens import normalize_resource
from app.config import settings
from app.deps import get_session

router = APIRouter()
log = structlog.get_logger(__name__)

_CONSENT_TEMPLATE = Path(__file__).parent.parent / "templates" / "oauth_consent.html"

# In-flight state TTL — the browser leg (GitHub sign-in + consent) is interactive.
_STATE_TTL = 600  # 10 minutes
_GITHUB_REDIRECT_PATH = "/oauth/github-callback"


def _sign_state(payload: dict) -> str:
    now = int(time.time())
    claims = {**payload, "iss": "xbrain-oauth-as", "iat": now, "exp": now + _STATE_TTL}
    tok = authlib_jwt.encode({"alg": "HS256"}, claims, settings.BRIDGE_SHARED_SECRET)
    return tok.decode("ascii") if isinstance(tok, bytes) else tok


def _verify_state(token: str) -> dict | None:
    try:
        claims = authlib_jwt.decode(token, settings.BRIDGE_SHARED_SECRET)
        claims.validate()  # checks exp/iat
        if claims.get("iss") != "xbrain-oauth-as":
            return None
        return dict(claims)
    except Exception:
        return None


def _error_page(message: str, status: int = 400) -> HTMLResponse:
    # English-only, no redirect — used when redirect_uri cannot be trusted.
    html = (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        "<title>Authorization error</title></head><body style='font-family:system-ui;"
        "background:#0f1115;color:#e7e9ee;padding:40px'>"
        f"<h1>Authorization error</h1><p>{message}</p></body></html>"
    )
    return HTMLResponse(content=html, status_code=status)


@router.get("/oauth/authorize", response_model=None)
async def authorize(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse | RedirectResponse:
    q = request.query_params
    response_type = q.get("response_type")
    client_id = q.get("client_id")
    redirect_uri = q.get("redirect_uri")
    code_challenge = q.get("code_challenge")
    code_challenge_method = q.get("code_challenge_method")
    state = q.get("state") or ""
    resource = q.get("resource") or ""
    scope = q.get("scope") or "brain:read brain:write"

    if response_type != "code":
        return _error_page("Only response_type=code is supported.")
    if not client_id or not redirect_uri or not code_challenge:
        return _error_page("Missing required authorization parameters.")
    if code_challenge_method != "S256":
        return _error_page("code_challenge_method must be S256.")

    # Open-redirect / code-exfiltration defense: the client must exist AND the
    # redirect_uri must be one of its registered URIs. On failure return a 400
    # page — do NOT redirect anywhere.
    if not await oauth_store.is_redirect_uri_registered(
        session, client_id=client_id, redirect_uri=redirect_uri
    ):
        return _error_page("Unknown client or unregistered redirect_uri.")

    norm_resource = normalize_resource(resource) or normalize_resource(
        settings.OAUTH_RESOURCE_URL
    )

    state_token = _sign_state(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "resource": norm_resource,
            "scope": scope,
            "claude_state": state,
            "stage": "pre_github",
        }
    )

    # Redirect into GitHub sign-in using memory-api's OWN callback (never the
    # Claude.ai redirect_uri). Reuse the xbrain GitHub App client id.
    github_redirect = settings.OAUTH_ISSUER_URL.rstrip("/") + _GITHUB_REDIRECT_PATH
    gh_params = {
        "client_id": settings.GITHUB_APP_CLIENT_ID,
        "redirect_uri": github_redirect,
        "state": state_token,
        "scope": "read:user user:email",
    }
    gh_url = "https://github.com/login/oauth/authorize?" + urlencode(gh_params)
    return RedirectResponse(url=gh_url, status_code=302)


@router.get("/oauth/github-callback", response_model=None)
async def github_callback(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse | RedirectResponse:
    q = request.query_params
    gh_code = q.get("code")
    state_token = q.get("state") or ""
    if not gh_code:
        return _error_page("GitHub did not return an authorization code.")

    st = _verify_state(state_token)
    if st is None or st.get("stage") != "pre_github":
        return _error_page("Authorization state is invalid or has expired.")

    # Reuse the existing GitHub App sign-in helpers from auth_github.py.
    from app.routes.auth_github import (
        _exchange_code_for_token,
        _fetch_github_profile,
        _resolve_or_merge_user,
    )
    from app.repos.teams import list_teams_for_user

    github_redirect = settings.OAUTH_ISSUER_URL.rstrip("/") + _GITHUB_REDIRECT_PATH
    token_body = await _exchange_code_for_token(gh_code, github_redirect)
    profile = await _fetch_github_profile(token_body["access_token"])
    user = await _resolve_or_merge_user(
        session,
        github_id=profile["github_id"],
        login=profile["login"],
        display_name=profile["display_name"],
        email=profile["email"],
    )
    await session.commit()

    teams = await list_teams_for_user(session, user_id=user.id)
    if not teams:
        return _error_page("Your GitHub account is not a member of any team yet.")

    # Carry the original authorize params + resolved user through a fresh state.
    post_state = _sign_state(
        {
            "client_id": st["client_id"],
            "redirect_uri": st["redirect_uri"],
            "code_challenge": st["code_challenge"],
            "resource": st["resource"],
            "scope": st["scope"],
            "claude_state": st.get("claude_state", ""),
            "user_id": str(user.id),
            "stage": "post_github",
        }
    )

    # Single team → skip selection, issue the code immediately.
    if len(teams) == 1:
        return await _finalize_consent(
            session, state=post_state, team_scope=teams[0].slug
        )

    # Multiple teams → render the English-only consent page with radio options.
    client = await oauth_store.get_client(session, st["client_id"])
    client_name = (client or {}).get("client_name") or "this application"
    options_html = "\n".join(
        f'<div class="team"><input type="radio" id="t_{t.slug}" name="team_scope" '
        f'value="{t.slug}"{" checked" if i == 0 else ""}>'
        f'<label for="t_{t.slug}">{t.display_name} ({t.slug})</label></div>'
        for i, t in enumerate(teams)
    )
    html = (
        _CONSENT_TEMPLATE.read_text(encoding="utf-8")
        .replace("{{client_name}}", str(client_name))
        .replace("{{scopes}}", str(st["scope"]))
        .replace("{{state}}", post_state)
        .replace("{{team_options}}", options_html)
    )
    return HTMLResponse(content=html)


@router.post("/oauth/authorize", response_model=None)
async def authorize_submit(
    state: str = Form(...),
    team_scope: str = Form(...),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse | RedirectResponse:
    return await _finalize_consent(session, state=state, team_scope=team_scope)


async def _finalize_consent(
    session: AsyncSession, *, state: str, team_scope: str
) -> HTMLResponse | RedirectResponse:
    st = _verify_state(state)
    if st is None or st.get("stage") != "post_github":
        return _error_page("Authorization state is invalid or has expired.")

    user_id = st.get("user_id")
    if not user_id:
        return _error_page("Authorization state is missing the user identity.")

    # Defense in depth: re-verify the redirect_uri is still registered.
    if not await oauth_store.is_redirect_uri_registered(
        session, client_id=st["client_id"], redirect_uri=st["redirect_uri"]
    ):
        return _error_page("Unknown client or unregistered redirect_uri.")

    # Strict isolation: the user must actually be a member of the chosen team.
    from uuid import UUID

    from app.repos.teams import get_membership

    membership = await get_membership(
        session, user_id=UUID(user_id), team_slug=team_scope
    )
    if membership is None:
        return _error_page("You are not a member of the selected team.")

    code = await oauth_store.create_auth_code(
        session,
        client_id=st["client_id"],
        user_id=UUID(user_id),
        team_scope=team_scope,
        resource=st["resource"],
        code_challenge=st["code_challenge"],
        redirect_uri=st["redirect_uri"],
        scope=st["scope"],
    )
    await session.commit()

    params = {"code": code}
    if st.get("claude_state"):
        params["state"] = st["claude_state"]
    redirect_to = st["redirect_uri"] + ("&" if "?" in st["redirect_uri"] else "?") + urlencode(params)
    log.info("oauth.authorize.code_issued", team_scope=team_scope)
    return RedirectResponse(url=redirect_to, status_code=302)
