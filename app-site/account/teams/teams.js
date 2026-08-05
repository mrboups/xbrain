/**
 * teams.js — app-site/account/teams page.
 *
 * Phase 10 (GHA-08) — GitHub-primary sign-in via Option B full-page redirect:
 *   1. Click "Sign in with GitHub" → window.location.href = github.com/login/oauth/authorize
 *      with state CSRF token in sessionStorage.
 *   2. GitHub redirects back to /account/teams/?code=...&state=...
 *   3. JS POSTs { code, redirect_uri, state } to /v1/auth/github/signin (server-side
 *      code-exchange; client_secret never touches the browser).
 *   4. Stores returned xbt_token in localStorage under the canonical key "xbt_token".
 *   5. Strips ?code&?state from the URL via history.replaceState.
 *   6. Renders authenticated UI + 4-state auth header banners (RESEARCH.md Q7):
 *        UNAUTHENTICATED → AUTHENTICATED_GITHUB_ONLY / AUTHENTICATED_GOOGLE_ONLY
 *                          / AUTHENTICATED_BOTH.
 *
 * Google sign-in is retained as a secondary "or use Google (legacy)" option.
 *
 * Canonical localStorage keys (shared with Chrome extension):
 *   - xbt_token   (the API token)
 *   - user_sub    (the user's email or sub identifier)
 *
 * IMPORTANT — OAuth setup (one-time, manual):
 *   - GitHub OAuth App `Ov23liVqXmHkS6JdYpcN` must list
 *     `https://grooveos.app/account/teams/` as an Authorization callback URL.
 *   - Google OAuth client must list `https://grooveos.app` (and
 *     `https://xbrain-495115.web.app` if used) in its "Authorized JavaScript origins".
 *
 * IT IS A MODULE NOW, and the import below is why. The team API key is set from
 * two places -- this page and the PWA's settings sheet, which exists because
 * this page is unreachable from a phone with no address bar. Both consume
 * packages/chat-core: the same provider table, the same validation, the same
 * failure sentences, the same two requests. A `<script src>` cannot import, so
 * index.html loads this with type="module" -- deferred, which is harmless
 * because everything here starts from the `load` event anyway.
 *
 * The specifier reaches ../../app/chat_core/, the PWA's generated copy, rather
 * than adding a third sync target for one page. On disk and over HTTP that path
 * resolves identically (app-site/ IS the web root), and the drift gate keeps
 * that copy byte-identical to packages/chat-core.
 */

import { createApi } from "../../app/chat_core/api.js";
import {
  API_KEY_PROVIDERS,
  PROVIDER_ANTHROPIC,
  apiKeyProvider,
  providerLabel,
  providerIsCallable,
  readApiKeyProviders,
  readFallbackSelection,
  fallbackSelectionBody,
  missingKeyForSelectionWarning,
  validateApiKey,
  describeApiKeyFailure,
  teamKeySavedMessage,
  unusedProviderWarning,
  TEAM_KEY_UNUSED_MARK,
  TEAM_KEY_COST_NOTE,
  TEAM_KEY_REPLACE_NOTE,
  TEAM_KEY_MEMBER_NOTE,
  TEAM_KEY_SELECTION_LABEL,
  TEAM_KEY_FALLBACK_ONLY_NOTE,
  TEAM_KEY_STORE_IS_NOT_SELECT,
  TEAM_KEY_NO_SELECTOR_NOTE,
  TEAM_KEY_SET,
  TEAM_KEY_NOT_SET,
  TEAM_KEY_UNKNOWN,
} from "../../app/chat_core/team_api_keys.js";

(function () {
  "use strict";

  const MEMORY_API_BASE = "https://api.grooveos.app";
  const GOOGLE_CLIENT_ID = "50097563098-rdh24v05dcp0ees8o4kqviuuoi5sup3n.apps.googleusercontent.com";
  // Phase 12 — GitHub App client_id (replaces the legacy OAuth App).
  // Multi-callback URL support means the same client_id works for both the
  // web app and the Chrome extension — see 12-RESEARCH.md §Q11.
  const GITHUB_CLIENT_ID = "Iv23liVnZvIN0Lo6isof";
  const GITHUB_REDIRECT_URI = window.location.origin + "/account/teams/";
  const GITHUB_OAUTH_SCOPES = "read:user user:email read:org";

  // Canonical keys — shared with the Chrome extension (background.js, onboarding.js).
  const STORAGE_TOKEN = "xbt_token";
  const STORAGE_EMAIL = "user_sub";
  // Legacy keys — migrated forward on load (kept for one-time read-only fallback).
  const LEGACY_STORAGE_TOKEN = "xbrain_xbt_token";
  const LEGACY_STORAGE_EMAIL = "xbrain_user_email";
  // GitHub OAuth CSRF state (sessionStorage, never localStorage).
  const STORAGE_OAUTH_STATE = "xbrain_github_oauth_state";

  const state = {
    me: null,             // /v1/me result
    teams: [],
    expanded: new Set(),
  };

  /**
   * The shared client, for the API-key calls only.
   *
   * NOT a replacement for xbtFetch, which every other call on this page still
   * uses and which is this page's own session policy (drop the token on 401,
   * throw an Error carrying .status). Migrating the rest is a separate change
   * with its own failure surface; what had to move now is the pair of requests
   * the PWA also makes, so that the two surfaces cannot spell the route, the
   * method or the body differently.
   *
   * The base origin is INJECTED, like every other surface's: chat-core hardcodes
   * no host, and a test asserts it.
   */
  const chatCoreApi = createApi({
    baseUrl: MEMORY_API_BASE,
    getToken: async () => localStorage.getItem(STORAGE_TOKEN),
  });

  // ── Initialization ────────────────────────────────────────────────────────

  window.addEventListener("load", init);

  /**
   * Migrate any legacy `xbrain_xbt_token` / `xbrain_user_email` into the
   * canonical `xbt_token` / `user_sub` keys exactly once. Idempotent.
   */
  function migrateLegacyKeys() {
    try {
      if (!localStorage.getItem(STORAGE_TOKEN)) {
        const legacy = localStorage.getItem(LEGACY_STORAGE_TOKEN);
        if (legacy) localStorage.setItem(STORAGE_TOKEN, legacy);
      }
      if (!localStorage.getItem(STORAGE_EMAIL)) {
        const legacyEmail = localStorage.getItem(LEGACY_STORAGE_EMAIL);
        if (legacyEmail) localStorage.setItem(STORAGE_EMAIL, legacyEmail);
      }
      // Drop the legacy keys once migration succeeded (one-time cleanup).
      if (localStorage.getItem(STORAGE_TOKEN) && localStorage.getItem(LEGACY_STORAGE_TOKEN)) {
        localStorage.removeItem(LEGACY_STORAGE_TOKEN);
      }
      if (localStorage.getItem(STORAGE_EMAIL) && localStorage.getItem(LEGACY_STORAGE_EMAIL)) {
        localStorage.removeItem(LEGACY_STORAGE_EMAIL);
      }
    } catch { /* localStorage unavailable — ignore */ }
  }

  async function init() {
    migrateLegacyKeys();

    // ── Post-install redirect (Plan 12-07) ──────────────────────────────────
    // If we're returning from GitHub's install consent
    // (`?installation_id=…&setup_action=install`) and have a token from a
    // prior sign-in, try to load the teams UI directly. On success we're
    // done. On failure, fall through to the normal flow (sign-in button
    // OR cached-token re-render).
    if (await handlePostInstallRedirect()) return;

    // ── Detect a GitHub OAuth callback (Option B full-page redirect) ──
    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");
    const cbState = params.get("state");
    const ghError = params.get("error");
    const ghErrorDesc = params.get("error_description");

    if (ghError) {
      // GitHub bounced us back with ?error=...&error_description=... — surface it.
      window.history.replaceState({}, "", "/account/teams/");
      sessionStorage.removeItem(STORAGE_OAUTH_STATE);
      showSignIn();
      setSigninError(
        "GitHub returned: " + ghError +
        (ghErrorDesc ? " — " + decodeURIComponent(ghErrorDesc.replace(/\+/g, " ")) : "")
      );
      return;
    }

    if (code && cbState) {
      await handleGithubCallback(code, cbState);
      // handleGithubCallback either fully signs us in (loadAuthenticatedUI ran)
      // or surfaced an error and reverted to showSignIn().
      return;
    }

    const xbt = localStorage.getItem(STORAGE_TOKEN);
    if (xbt) {
      // Already signed in — go straight to the teams UI.
      try {
        await loadAuthenticatedUI();
        return;
      } catch {
        // Stored token rejected — fall through to sign-in.
        localStorage.removeItem(STORAGE_TOKEN);
      }
    }
    showSignIn();
  }

  function showSignIn() {
    document.getElementById("signin-section").hidden = false;
    document.getElementById("auth-section").hidden = true;
    document.getElementById("hdr-user").hidden = true;
    // Plan 12-07 — hide any leftover install banner. Otherwise signOut() or
    // a session-expired fall-through would leave the install warning visible
    // alongside the sign-in card.
    hideInstallBanner();

    // Wire the GitHub primary button (idempotent — only bound once).
    const ghBtn = document.getElementById("github-signin-btn");
    if (ghBtn && !ghBtn.__bound) {
      ghBtn.__bound = true;
      ghBtn.addEventListener("click", initiateGithubSignin);
    }

    // Render the Google button as fallback. GIS may not have loaded yet —
    // wait for the global.
    function tryInit() {
      if (window.google && google.accounts && google.accounts.id) {
        google.accounts.id.initialize({
          client_id: GOOGLE_CLIENT_ID,
          callback: handleGoogleCredential,
          auto_select: false,
          ux_mode: "popup",
        });
        google.accounts.id.renderButton(
          document.getElementById("google-signin-btn"),
          { theme: "filled_black", size: "medium", shape: "rectangular", text: "signin_with" },
        );
      } else {
        setTimeout(tryInit, 200);
      }
    }
    tryInit();
  }

  function setSigninError(msg) {
    const box = document.getElementById("signin-error");
    const txt = document.getElementById("signin-error-msg");
    if (!box || !txt) return;
    if (!msg) {
      box.hidden = true;
      txt.textContent = "";
      return;
    }
    txt.textContent = msg;
    box.hidden = false;
  }

  // ── Install-required banner (Plan 12-07, GHAPP-06) ────────────────────────
  //
  // Shown when POST /v1/auth/github/signin returns ``install_required=true``.
  // The xbt_token is already minted (the user has a valid session), but the
  // primary GitHub org has not yet installed the xbrain GitHub App, so
  // auto-grant cannot match the user against org-gated teams. The user must:
  //   1. Click "Install xbrain" → GitHub install consent flow (new tab).
  //   2. After install, GitHub redirects back here with
  //      ``?installation_id=N&setup_action=install``.
  //   3. The user (or auto-handler) clicks "Try again" → re-run the whole
  //      sign-in flow so ``auto_grant_via_org_match`` runs against an org
  //      that now has the App installed → memberships granted, banner dismissed.
  function showInstallBanner({ installUrl, orgLogin }) {
    const banner = document.getElementById("install-banner");
    const button = document.getElementById("install-banner-button");
    const orgSpan = document.getElementById("install-banner-org");
    const retry = document.getElementById("install-banner-retry");
    if (!banner || !button || !orgSpan) return;
    orgSpan.textContent = orgLogin || "your organization";
    // ``install_url`` is server-built; fall back to "#" if absent (defensive —
    // shouldn't happen because install_required=true implies install_url=set).
    button.href = installUrl || "#";
    if (retry && !retry.__bound) {
      retry.__bound = true;
      retry.addEventListener("click", () => {
        // "Try again" re-runs the full GitHub sign-in so auto-grant fires
        // against an org that now (post-install) lets memory-api check
        // membership via the installation token.
        hideInstallBanner();
        initiateGithubSignin();
      });
    }
    banner.removeAttribute("hidden");
  }

  function hideInstallBanner() {
    const banner = document.getElementById("install-banner");
    if (banner) banner.setAttribute("hidden", "");
  }

  // ── Post-install redirect handler ──────────────────────────────────────────
  //
  // Called from init() BEFORE any sign-in branching runs. Detects the
  // ``?installation_id=…&setup_action=install`` query params GitHub appends
  // after the install consent. If we have an xbt_token from a previous
  // sign-in, attempt to load the teams list directly — the install webhook
  // should have populated the ``installations`` row by now, and
  // auto-grant ran the first time. If teams are available, the user is
  // good: clean the URL and proceed to the authenticated UI. Otherwise,
  // surface the install banner again so the user can hit "Try again"
  // (re-runs sign-in → triggers a fresh auto-grant pass).
  //
  // Returns true when the caller should skip the rest of init() (we already
  // rendered the auth UI). Returns false when init() should continue
  // normally.
  async function handlePostInstallRedirect() {
    const params = new URLSearchParams(window.location.search);
    if (params.get("setup_action") !== "install") return false;

    // Strip the GitHub install query params unconditionally — never leave
    // them in the URL bar (refresh would otherwise re-trigger).
    window.history.replaceState({}, "", "/account/teams/");

    const xbt = localStorage.getItem(STORAGE_TOKEN);
    if (!xbt) {
      // No prior session — fall through to the normal sign-in flow.
      return false;
    }

    // Probe /v1/teams/my-teams. If 2xx, the token is valid; show whatever
    // we get (might be empty if auto-grant still hasn't matched, but the
    // common case after install is that membership now resolves).
    try {
      const r = await fetch(`${MEMORY_API_BASE}/v1/teams/my-teams`, {
        headers: { Authorization: `Bearer ${xbt}` },
      });
      if (r.ok) {
        hideInstallBanner();
        await loadAuthenticatedUI();
        return true;
      }
      // 401 → stale token; fall through so showSignIn() runs.
      if (r.status === 401) {
        localStorage.removeItem(STORAGE_TOKEN);
        return false;
      }
    } catch {
      // Network glitch — let normal flow handle it.
    }
    return false;
  }

  // ── GitHub OAuth (Option B full-page redirect) ──────────────────────────────

  function initiateGithubSignin() {
    // Generate a CSRF state token, store in sessionStorage, redirect to GitHub.
    const csrf = (
      Math.random().toString(36).slice(2) +
      Math.random().toString(36).slice(2)
    );
    try {
      sessionStorage.setItem(STORAGE_OAUTH_STATE, csrf);
    } catch {
      setSigninError("Browser blocked sessionStorage — sign-in unavailable.");
      return;
    }
    setSigninError(null);
    const u = new URL("https://github.com/login/oauth/authorize");
    u.searchParams.set("client_id", GITHUB_CLIENT_ID);
    u.searchParams.set("redirect_uri", GITHUB_REDIRECT_URI);
    u.searchParams.set("scope", GITHUB_OAUTH_SCOPES);
    u.searchParams.set("state", csrf);
    window.location.href = u.toString();
  }

  async function handleGithubCallback(code, cbState) {
    const expected = sessionStorage.getItem(STORAGE_OAUTH_STATE);
    // Always strip the params from the URL — never leave them in the bar.
    window.history.replaceState({}, "", "/account/teams/");
    sessionStorage.removeItem(STORAGE_OAUTH_STATE);

    if (!expected || cbState !== expected) {
      showSignIn();
      setSigninError("Sign-in failed: invalid state. Please retry.");
      return;
    }

    try {
      const r = await fetch(`${MEMORY_API_BASE}/v1/auth/github/signin`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          code,
          redirect_uri: GITHUB_REDIRECT_URI,
          state: cbState,
        }),
      });
      if (r.status === 403) {
        showSignIn();
        setSigninError(
          "You're not a member of any xbrain team yet — contact your admin."
        );
        return;
      }
      if (!r.ok) {
        const text = await r.text().catch(() => "");
        throw new Error(`HTTP ${r.status} ${text.slice(0, 200)}`);
      }
      const data = await r.json();
      const xbtToken = data && data.xbt_token;
      if (!xbtToken) throw new Error("response missing xbt_token");
      localStorage.setItem(STORAGE_TOKEN, xbtToken);
      const u = data.user || {};
      // Prefer real email; fall back to github_username if email is noreply.
      if (u.email) {
        localStorage.setItem(STORAGE_EMAIL, u.email);
      } else if (u.github_username) {
        localStorage.setItem(STORAGE_EMAIL, "@" + u.github_username);
      }
      setSigninError(null);

      // Plan 12-07 — if memory-api signals the App is not installed on the
      // user's primary org, surface the install banner and stop here. The
      // ``install_url`` is the GitHub install-consent deep link; ``org_login``
      // is the org name to show in the banner copy. After the user installs,
      // GitHub redirects back to this page with ``?setup_action=install``;
      // ``handlePostInstallRedirect`` picks that up on next load.
      if (data.install_required && data.install_url) {
        // Hide the sign-in card — the token IS valid (we just stored it),
        // but the user can't usefully see the teams list yet.
        document.getElementById("signin-section").hidden = true;
        document.getElementById("auth-section").hidden = true;
        showInstallBanner({
          installUrl: data.install_url,
          orgLogin: data.org_login,
        });
        return;
      }
      hideInstallBanner();
      await loadAuthenticatedUI();
    } catch (e) {
      showSignIn();
      setSigninError("GitHub sign-in failed: " + (e && e.message ? e.message : e));
    }
  }

  // ── Google sign-in (legacy fallback) ────────────────────────────────────────

  async function handleGoogleCredential(resp) {
    const idToken = resp && resp.credential;
    if (!idToken) {
      setSigninError("Sign-in failed — no credential returned.");
      return;
    }
    try {
      const r = await fetch(`${MEMORY_API_BASE}/v1/me/api-token`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${idToken}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ team_scope: "", name: "web-teams-page" }),
      });
      if (!r.ok) {
        const text = await r.text().catch(() => "");
        throw new Error(`mint failed: HTTP ${r.status} ${text.slice(0, 200)}`);
      }
      const { token } = await r.json();
      localStorage.setItem(STORAGE_TOKEN, token);

      // Decode the ID token payload (no verification — display only).
      try {
        const payload = JSON.parse(atob(idToken.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
        if (payload.email) localStorage.setItem(STORAGE_EMAIL, payload.email);
      } catch { /* ignore */ }

      setSigninError(null);
      await loadAuthenticatedUI();
    } catch (e) {
      setSigninError(`Sign-in failed: ${e.message}`);
    }
  }

  async function loadAuthenticatedUI() {
    document.getElementById("signin-section").hidden = true;
    document.getElementById("auth-section").hidden = false;
    document.getElementById("hdr-user").hidden = false;

    const email = localStorage.getItem(STORAGE_EMAIL) || "signed in";
    document.getElementById("hdr-email").textContent = email;

    const btnSignout = document.getElementById("btn-signout");
    if (btnSignout && !btnSignout._wired) {
      btnSignout._wired = true;
      btnSignout.addEventListener("click", signOut);
    }

    await renderTeamsList();
  }

  function signOut() {
    if (!confirm("Sign out from the Teams page?")) return;
    localStorage.removeItem(STORAGE_TOKEN);
    localStorage.removeItem(STORAGE_EMAIL);
    try {
      if (window.google && google.accounts && google.accounts.id) {
        google.accounts.id.disableAutoSelect();
      }
    } catch { /* ignore */ }
    state.me = null;
    state.teams = [];
    state.expanded.clear();
    document.getElementById("teams-list").innerHTML = "";
    showSignIn();
  }

  // ── Memory-api fetch helper ────────────────────────────────────────────

  async function xbtFetch(path, { method = "GET", body = null } = {}) {
    const token = localStorage.getItem(STORAGE_TOKEN);
    if (!token) throw new Error("not signed in");
    const opts = {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
    };
    if (body !== null) opts.body = JSON.stringify(body);
    const r = await fetch(`${MEMORY_API_BASE}${path}`, opts);
    if (r.status === 401) {
      localStorage.removeItem(STORAGE_TOKEN);
      throw new Error("session expired — please sign in again");
    }
    if (!r.ok) {
      const text = await r.text().catch(() => "");
      const err = new Error(`HTTP ${r.status}: ${text.slice(0, 300)}`);
      err.status = r.status;
      err.body = text;
      throw err;
    }
    if (r.status === 204) return null;
    return r.json();
  }

  // ── Auth header state machine (GHA-07) ──────────────────────────────────────

  function renderAuthHeader(me) {
    if (!me) return;
    const hasGithub = !!me.github_id;
    const hasRealEmail =
      !!me.email && !String(me.email).endsWith("@users.noreply.github.com");

    const showLinkGoogle = hasGithub && !hasRealEmail;
    const showLinkGithub = !hasGithub;
    const showBoth = hasGithub && hasRealEmail;

    const ctaG = document.getElementById("cta-link-google");
    const ctaH = document.getElementById("cta-link-github");
    const both = document.getElementById("status-both-connected");
    const ghHandle = document.getElementById("status-gh-handle");

    if (ctaG) ctaG.hidden = !showLinkGoogle;
    if (ctaH) ctaH.hidden = !showLinkGithub;
    if (both) both.hidden = !showBoth;
    if (ghHandle && me.github_username) {
      ghHandle.textContent = "@" + me.github_username;
    }

    // Hook "Link GitHub" — same flow as primary sign-in.
    const linkBtn = document.getElementById("btn-link-github");
    if (linkBtn && !linkBtn.__bound) {
      linkBtn.__bound = true;
      linkBtn.addEventListener("click", initiateGithubSignin);
    }
    // Hook "Link Google" — re-trigger the Google One-Tap prompt.
    const linkGoogle = document.getElementById("btn-link-google");
    if (linkGoogle && !linkGoogle.__bound) {
      linkGoogle.__bound = true;
      linkGoogle.addEventListener("click", () => {
        try {
          if (window.google && google.accounts && google.accounts.id) {
            google.accounts.id.prompt();
          }
        } catch { /* ignore */ }
      });
    }
  }

  // ── Teams list ────────────────────────────────────────────────────────

  async function renderTeamsList() {
    const list = document.getElementById("teams-list");
    const empty = document.getElementById("teams-empty");
    const loading = document.getElementById("teams-loading");

    list.innerHTML = "";
    empty.hidden = true;
    loading.hidden = false;

    try {
      state.me = await xbtFetch("/v1/me");
      renderAuthHeader(state.me);
      state.teams = await xbtFetch("/v1/teams/my-teams");
    } catch (e) {
      loading.hidden = true;
      if (e.message && e.message.includes("session expired")) {
        showSignIn();
        return;
      }
      const card = document.createElement("div");
      card.className = "card";
      card.innerHTML = `<p style="color:var(--red);font-size:13px;margin:0">
        Couldn't load teams: ${escapeHtml(e.message || "unknown error")}.
      </p>`;
      list.appendChild(card);
      return;
    }

    loading.hidden = true;
    if (!state.teams || state.teams.length === 0) {
      empty.hidden = false;
      return;
    }
    for (const t of state.teams) {
      list.appendChild(buildTeamCard(t));
    }
  }

  function buildTeamCard(team) {
    const card = document.createElement("div");
    card.className = "team-card";
    card.dataset.teamId = team.id;

    const header = document.createElement("div");
    header.className = "team-card__header";
    header.innerHTML = `
      <div class="team-card__title">
        <strong>${escapeHtml(team.display_name || team.slug)}</strong>
        <small>${escapeHtml(team.slug)}${team.github_org ? " · github:" + escapeHtml(team.github_org) : ""}</small>
      </div>
      <span class="role-pill" data-role-pill>…</span>
      <span class="chevron" aria-hidden="true">▾</span>
    `;
    card.appendChild(header);

    const body = document.createElement("div");
    body.className = "team-card__body";
    body.hidden = true;
    card.appendChild(body);

    header.addEventListener("click", () => toggleCard(team, header, body));

    if (state.expanded.has(team.id)) {
      header.classList.add("is-open");
      body.hidden = false;
      void fillTeamBody(team, body);
    }
    return card;
  }

  async function toggleCard(team, header, body) {
    const opening = body.hidden;
    body.hidden = !opening;
    header.classList.toggle("is-open", opening);
    if (opening) {
      state.expanded.add(team.id);
      await fillTeamBody(team, body);
    } else {
      state.expanded.delete(team.id);
    }
  }

  async function fillTeamBody(team, body) {
    body.innerHTML = `<div class="loading">Loading members…</div>`;

    // Fired alongside the members read rather than after it — opening a card
    // shouldn't cost two serial round-trips. The .catch is attached here, on
    // the same tick, so a rejection can never surface as unhandled; null means
    // "we don't know", which the section renders as such — and a non-2xx is
    // exactly that, which is why this reads the Response rather than throwing.
    //
    // readApiKeyProviders is what narrows the body: the route answers
    // [{provider}] and nothing else, and taking the field through the shared
    // reader means a leakier response could not widen this page by accident.
    const keysPromise = chatCoreApi
      .listTeamApiKeysRaw(team.id)
      .then((res) => (res.ok ? res.json() : null))
      .then((rows) => (rows === null ? null : readApiKeyProviders(rows)))
      .catch(() => null);

    // WHICH of those keys the agent spends — a different fact, from a different
    // route, and one this build's server may not have yet. null means "no
    // selection to show", which the section renders as the static truth about
    // what gets called rather than as a control that would 404.
    const selectionPromise = chatCoreApi
      .teamFallbackProviderRaw(team.id)
      .then((res) => (res.ok ? res.json() : null))
      .then((body) => (body === null ? null : readFallbackSelection(body)))
      .catch(() => null);

    let members;
    try {
      members = await xbtFetch(`/v1/teams/${team.id}/members`);
    } catch (e) {
      body.innerHTML = `<p style="color:var(--red);font-size:13px;margin:0">
        Couldn't load members: ${escapeHtml(e.message)}
      </p>`;
      return;
    }

    const meMember = members.find((m) => m.user_id === state.me.id);
    const myRole = meMember ? meMember.role : "member";
    const isAdmin = myRole === "admin";

    // Update header pill + subtitle
    const card = body.parentElement;
    const pill = card.querySelector("[data-role-pill]");
    if (pill) {
      pill.textContent = myRole;
      pill.className = `role-pill ${isAdmin ? "is-admin" : "is-member"}`;
    }
    const sub = card.querySelector(".team-card__title small");
    if (sub) {
      sub.textContent =
        `${team.slug}${team.github_org ? " · github:" + team.github_org : ""}` +
        ` · ${members.length} member${members.length === 1 ? "" : "s"}`;
    }

    body.innerHTML = "";

    // Members
    const lbl = document.createElement("p");
    lbl.className = "members-label";
    lbl.textContent = "Members";
    body.appendChild(lbl);

    const adminCount = members.filter((m) => m.role === "admin").length;

    for (const m of members) {
      const row = document.createElement("div");
      row.className = "member-row";

      const av = document.createElement("div");
      av.className = "member-avatar";
      av.textContent = ((m.display_name || m.email || "?").charAt(0) || "?").toUpperCase();
      row.appendChild(av);

      const info = document.createElement("div");
      info.className = "member-info";
      const isMe = m.user_id === state.me.id;
      const label = m.display_name || (m.email ? m.email.split("@")[0] : "Member");
      info.innerHTML = `
        <strong>${escapeHtml(label)}${isMe ? " (you)" : ""}</strong>
        <small>${escapeHtml(m.email || m.source_user_id || m.user_id)}</small>
      `;
      row.appendChild(info);

      const role = document.createElement("span");
      role.className = "member-role";
      role.textContent = m.role;
      row.appendChild(role);

      if (isAdmin && !isMe) {
        const rmBtn = document.createElement("button");
        rmBtn.type = "button";
        rmBtn.className = "btn btn-sm btn-danger";
        rmBtn.textContent = "Remove";
        if (m.role === "admin" && adminCount <= 1) {
          rmBtn.disabled = true;
          rmBtn.title = "Can't remove the last admin";
        }
        rmBtn.addEventListener("click", () => removeMember(team, m));
        row.appendChild(rmBtn);
      }
      body.appendChild(row);
    }

    // Invite form (admin)
    if (isAdmin) {
      const iLbl = document.createElement("p");
      iLbl.className = "members-label";
      iLbl.style.marginTop = "16px";
      iLbl.textContent = "Invite by email";
      body.appendChild(iLbl);

      const form = document.createElement("div");
      form.className = "invite-form";
      form.innerHTML = `
        <input type="email" placeholder="teammate@example.com" autocomplete="off" />
        <select>
          <option value="member" selected>member</option>
          <option value="admin">admin</option>
        </select>
        <button type="button" class="btn btn-primary">Invite</button>
      `;
      body.appendChild(form);
      const input = form.querySelector("input");
      const sel = form.querySelector("select");
      const btn = form.querySelector("button");
      btn.addEventListener("click", () => inviteMember(team, input, sel, btn));
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") inviteMember(team, input, sel, btn);
      });
    }

    // Model API key — every member sees whether one is set and which one the
    // agent spends; only an admin, which is what the server enforces on both
    // PUTs, gets the controls.
    body.appendChild(
      buildApiKeysSection({
        team,
        providers: await keysPromise,
        selection: await selectionPromise,
        isAdmin,
      }).el,
    );

    // Status line
    const statusEl = document.createElement("div");
    statusEl.className = "action-status";
    statusEl.dataset.teamStatus = team.id;
    body.appendChild(statusEl);

    // Leave button
    const leaveBtn = document.createElement("button");
    leaveBtn.type = "button";
    leaveBtn.className = "btn btn-danger";
    leaveBtn.style.marginTop = "12px";
    leaveBtn.textContent = "Leave team";
    if (isAdmin && adminCount <= 1) {
      leaveBtn.disabled = true;
      leaveBtn.title = "You're the only admin — promote another member first";
    }
    leaveBtn.addEventListener("click", () => leaveTeam(team, leaveBtn, statusEl));
    body.appendChild(leaveBtn);
  }

  async function inviteMember(team, input, sel, btn) {
    const email = (input.value || "").trim();
    if (!email) {
      setStatus(team.id, "Enter an email first", "error");
      return;
    }
    btn.disabled = true;
    setStatus(team.id, "Inviting…");
    try {
      await xbtFetch(`/v1/teams/${team.id}/invite`, {
        method: "POST",
        body: { email, role: sel.value },
      });
      setStatus(team.id, `Invited ${email}`, "success");
      input.value = "";
      const card = document.querySelector(`[data-team-id="${team.id}"]`);
      const body = card && card.querySelector(".team-card__body");
      if (body) await fillTeamBody(team, body);
    } catch (e) {
      const msg =
        e.status === 404
          ? `${email} isn't registered yet — ask them to sign in once first.`
          : `Invite failed: ${e.message}`;
      setStatus(team.id, msg, "error");
    } finally {
      btn.disabled = false;
    }
  }

  // ── Team model API key ────────────────────────────────────────────────
  //
  // The team chat agent answers through the owner's Claude subscription while
  // some browser holds a live extension session, and falls back to a key when
  // none does. This section is how a team supplies that key.
  //
  // A key here is WRITE-ONLY, and that is a property of the screen, not a
  // detail of it. GET /v1/teams/{id}/api-keys returns `[{provider}]` and
  // nothing else — no ciphertext, no prefix, no suffix — so there is nothing to
  // mask and the UI shows presence only. A masked value like `sk-ant-••••4f2a`
  // would be a lie twice over: the characters aren't available, and printing a
  // shape implies the rest could be revealed. It cannot.
  //
  // The secret's whole lifetime is: the input element → validateApiKey →
  // the PUT body. It is never put in a URL, an error string, a dataset
  // attribute, module state, or a console call, and the field is cleared the
  // moment the server accepts it.
  //
  // THE PROVIDER TABLE, THE VALIDATION AND THE FAILURE SENTENCES NOW LIVE IN
  // packages/chat-core/team_api_keys.js. They were declared here until the PWA
  // needed the same feature, at which point the choice was one definition or
  // two products that disagree about what a 403 means. What is left below is
  // this page's own shape: a section inside a team card, built out of the
  // classes the invite form above it uses.

  function setApiKeyStatus(el, text, type) {
    if (!el) return;
    el.textContent = text;
    el.className =
      "action-status" +
      (type === "success" ? " is-success" : "") +
      (type === "error" ? " is-error" : "");
  }

  /**
   * Validate, PUT, and forget. Resolves {ok} — callers never see the key.
   *
   * The request is chat-core's `putTeamApiKeysRaw`, which spells the route, the
   * method and the server's body shape — {keys:[{provider, api_key}]}, a bulk
   * upsert answering 204 — in the one place the PWA reads them from too. The key
   * travels in that body and nowhere else.
   *
   * RAW, so the status survives. Every code means something different to the
   * person holding the key, and describeApiKeyFailure turns each into its own
   * sentence; a thrown Error carrying an interpolated body would collapse them
   * AND would be the one string on this path that could echo the paste back.
   */
  async function saveTeamApiKey(ctx) {
    const { team, provider, input, button, status } = ctx;
    const verdict = validateApiKey(provider, input ? input.value : ctx.raw);
    if (!verdict.ok) {
      setApiKeyStatus(status, verdict.message, "error");
      return { ok: false, reason: "invalid" };
    }
    if (button) button.disabled = true;
    setApiKeyStatus(status, "Saving…");
    try {
      const res = await chatCoreApi.putTeamApiKeysRaw(team.id, [
        { provider, api_key: verdict.key },
      ]);
      if (!res.ok) {
        // 401 is this page's session policy, not the key's problem: the stored
        // token is dead and keeping it would fail every later call the same
        // way. Dropped here, exactly as xbtFetch drops it everywhere else.
        if (res.status === 401) localStorage.removeItem(STORAGE_TOKEN);
        setApiKeyStatus(status, describeApiKeyFailure({ status: res.status }), "error");
        return { ok: false, reason: "server" };
      }
      // Accepted — drop it out of the DOM before anything else can read it.
      if (input) input.value = "";
      setApiKeyStatus(
        status,
        teamKeySavedMessage(provider, {
          selected: ctx.selected,
          supported: ctx.supported,
        }),
        "success",
      );
      if (typeof ctx.onSaved === "function") ctx.onSaved(provider);
      return { ok: true };
    } catch (e) {
      // fetch() itself rejected — DNS, offline, CORS, TLS. No status, because
      // no response arrived, which is its own sentence.
      setApiKeyStatus(status, describeApiKeyFailure(null), "error");
      return { ok: false, reason: "network" };
    } finally {
      if (button) button.disabled = false;
    }
  }

  /**
   * Change WHICH stored key the agent falls back to. No secret involved — the
   * body is a provider name — but the same admin gate and the same distinct
   * failures.
   */
  async function saveFallbackProvider(ctx) {
    const { team, provider, select, status } = ctx;
    if (select) select.disabled = true;
    setApiKeyStatus(status, "Saving…");
    try {
      const res = await chatCoreApi.putTeamFallbackProviderRaw(
        team.id,
        fallbackSelectionBody(provider),
      );
      if (!res.ok) {
        if (res.status === 401) localStorage.removeItem(STORAGE_TOKEN);
        // A 404 here does not mean the team vanished — it means this build has
        // no selector — so it gets its own sentence instead of the key write's.
        setApiKeyStatus(
          status,
          res.status === 404
            ? TEAM_KEY_NO_SELECTOR_NOTE
            : describeApiKeyFailure({ status: res.status }),
          "error",
        );
        return { ok: false };
      }
      setApiKeyStatus(
        status,
        `The agent will now fall back to the ${providerLabel(provider)} key.`,
        "success",
      );
      if (typeof ctx.onSaved === "function") ctx.onSaved(provider);
      return { ok: true };
    } catch (e) {
      setApiKeyStatus(status, describeApiKeyFailure(null), "error");
      return { ok: false };
    } finally {
      if (select) select.disabled = false;
    }
  }

  /**
   * Build the section. `providers` is the array of provider ids the server says
   * have a key, or null when that read failed (rendered as "unknown" rather
   * than as "not set" — claiming absence we haven't confirmed would invite an
   * admin to overwrite a working key).
   *
   * `selection` is {provider, supported, available} from the agent-provider route, or
   * null when this build has no such route (or the read failed). Null is NOT
   * "Anthropic": a team that had selected OpenAI must not be told it is on
   * Claude because a request failed.
   *
   * `isAdmin` comes from this user's membership row, which is the same fact the
   * server checks on both PUTs, so the controls are absent exactly when pressing
   * them would 403.
   */
  function buildApiKeysSection({ team, providers, selection, isAdmin }) {
    const el = document.createElement("div");
    el.className = "apikey-section";

    const heading = document.createElement("p");
    heading.className = "members-label";
    heading.textContent = "Model API key";
    el.appendChild(heading);

    const note = document.createElement("p");
    note.className = "apikey-note";
    note.textContent = TEAM_KEY_COST_NOTE;
    el.appendChild(note);

    const present = new Set(providers || []);
    const supported = (selection && selection.supported) || null;
    let selected = (selection && selection.provider) || null;

    const ids = API_KEY_PROVIDERS.map((p) => p.id);
    for (const extra of providers || []) {
      if (!ids.includes(extra)) ids.push(extra);
    }
    if (selected && !ids.includes(selected)) ids.push(selected);

    // ── Which one is spent ────────────────────────────────────────────────
    //
    // FIRST, and separate from the form below, because storing a key and
    // selecting a provider are different actions and an interface that runs
    // them together is one where somebody pastes a key and waits for an answer
    // it was never going to give.
    const useRow = document.createElement("div");
    useRow.className = "apikey-userow";
    const useLabel = document.createElement("label");
    useLabel.className = "apikey-label";
    useLabel.textContent = TEAM_KEY_SELECTION_LABEL;
    useRow.appendChild(useLabel);

    let useSelect = null;
    let useValue = null;
    const useWarning = document.createElement("p");
    useWarning.className = "apikey-warning";
    useWarning.hidden = true;

    if (selection === null) {
      // No selector on this server. Say what IS true rather than leaving a gap.
      useValue = document.createElement("span");
      useValue.className = "apikey-usevalue";
      useValue.textContent = providerLabel(PROVIDER_ANTHROPIC);
      useRow.appendChild(useValue);
    } else if (isAdmin) {
      const useId = `apikey-use-${team.id}`;
      useLabel.setAttribute("for", useId);
      useSelect = document.createElement("select");
      useSelect.id = useId;
      for (const id of ids) {
        const opt = document.createElement("option");
        opt.value = id;
        opt.textContent = providerLabel(id);
        useSelect.appendChild(opt);
      }
      if (selected) useSelect.value = selected;
      useRow.appendChild(useSelect);
    } else {
      useValue = document.createElement("span");
      useValue.className = "apikey-usevalue";
      useValue.textContent = selected ? providerLabel(selected) : TEAM_KEY_UNKNOWN;
      useRow.appendChild(useValue);
    }
    el.appendChild(useRow);
    el.appendChild(useWarning);

    const fallbackNote = document.createElement("p");
    fallbackNote.className = "apikey-hint";
    fallbackNote.textContent =
      selection === null ? TEAM_KEY_NO_SELECTOR_NOTE : TEAM_KEY_FALLBACK_ONLY_NOTE;
    el.appendChild(fallbackNote);

    // ── What is stored ────────────────────────────────────────────────────

    const stateEls = new Map();
    for (const id of ids) {
      const row = document.createElement("div");
      row.className = "apikey-row";
      const name = document.createElement("span");
      name.className = "apikey-provider";
      name.textContent = providerLabel(id);
      row.appendChild(name);
      // Which of these the agent can actually call, said on the row itself.
      // Without it, three rows in one list read as three equivalent choices,
      // and two of them are keys this deployment stores and never spends.
      if (!providerIsCallable(id, supported)) {
        const unused = document.createElement("span");
        unused.className = "apikey-unused";
        unused.textContent = TEAM_KEY_UNUSED_MARK;
        row.appendChild(unused);
      }
      const st = document.createElement("span");
      st.className = "apikey-state";
      row.appendChild(st);
      stateEls.set(id, st);
      el.appendChild(row);
    }

    const status = document.createElement("div");
    status.className = "action-status";
    status.setAttribute("aria-live", "polite");

    let select = null;
    let input = null;
    let button = null;
    let warning = null;

    function paintStates() {
      for (const [id, st] of stateEls) {
        if (providers === null) {
          st.textContent = TEAM_KEY_UNKNOWN;
          st.className = "apikey-state";
        } else {
          const set = present.has(id);
          st.textContent = set ? TEAM_KEY_SET : TEAM_KEY_NOT_SET;
          st.className = "apikey-state" + (set ? " is-set" : "");
        }
      }
      if (button && select) {
        button.textContent = present.has(select.value) ? "Replace key" : "Save key";
      }
      if (input && select) {
        const p = apiKeyProvider(select.value);
        input.placeholder = p ? p.prefix + "…" : "paste the key";
      }
      // BEFORE the paste, not after the save: the whole cost of picking a
      // provider the agent never calls is a key somebody bought for nothing.
      if (warning && select) {
        const text = unusedProviderWarning(select.value, supported);
        warning.textContent = text;
        warning.hidden = text === "";
      }
      paintSelection();
    }

    /**
     * What the CURRENT selection means, said at selection time.
     *
     * Two ways a selection is a dead end, and both are silent otherwise: the
     * provider has no key stored (the server reports it unavailable rather than
     * reaching for another), or this build cannot call it at all.
     */
    function paintSelection() {
      const current = useSelect ? useSelect.value : selected;
      let text = "";
      if (current && !providerIsCallable(current, supported)) {
        text = unusedProviderWarning(current, supported);
      } else if (current && providers !== null && !present.has(current)) {
        text = missingKeyForSelectionWarning(current);
      }
      useWarning.textContent = text;
      useWarning.hidden = text === "";
    }

    if (isAdmin) {
      const form = document.createElement("div");
      form.className = "apikey-form";

      const selectId = `apikey-provider-${team.id}`;
      const inputId = `apikey-key-${team.id}`;

      const selLabel = document.createElement("label");
      selLabel.className = "apikey-label";
      selLabel.setAttribute("for", selectId);
      selLabel.textContent = "Provider";
      form.appendChild(selLabel);

      select = document.createElement("select");
      select.id = selectId;
      for (const p of API_KEY_PROVIDERS) {
        const opt = document.createElement("option");
        opt.value = p.id;
        // The marker rides in the option text as well as on the row, because
        // the picker is where the choice is actually made and a person reading
        // a dropdown is not reading the list above it.
        opt.textContent = providerIsCallable(p.id, supported)
          ? p.label
          : `${p.label} — ${TEAM_KEY_UNUSED_MARK}`;
        select.appendChild(opt);
      }
      // The one the agent calls, selected by default. Anything else has to be
      // chosen deliberately, past a warning.
      select.value = PROVIDER_ANTHROPIC;
      form.appendChild(select);

      const keyLabel = document.createElement("label");
      keyLabel.className = "apikey-label";
      keyLabel.setAttribute("for", inputId);
      keyLabel.textContent = "Key";
      form.appendChild(keyLabel);

      input = document.createElement("input");
      input.id = inputId;
      input.type = "password";
      input.setAttribute("autocomplete", "off");
      input.setAttribute("spellcheck", "false");
      form.appendChild(input);

      button = document.createElement("button");
      button.type = "button";
      button.className = "btn btn-primary";
      form.appendChild(button);

      el.appendChild(form);

      // Between the form and the hint, so it sits directly under the field it
      // is about. Painted by paintStates(), which runs on every change of the
      // picker as well as at build time.
      warning = document.createElement("p");
      warning.className = "apikey-warning";
      warning.hidden = true;
      el.appendChild(warning);

      const hint = document.createElement("p");
      hint.className = "apikey-hint";
      hint.textContent = TEAM_KEY_REPLACE_NOTE;
      el.appendChild(hint);

      const submit = () =>
        saveTeamApiKey({
          team,
          provider: select.value,
          input,
          button,
          status,
          selected,
          supported,
          onSaved: (id) => {
            present.add(id);
            if (!stateEls.has(id)) return;
            paintStates();
          },
        });

      button.addEventListener("click", submit);
      // The promise is RETURNED, not dropped: the click path hands `submit`
      // straight to the listener and therefore returns it, and a keyboard path
      // that swallowed it would be a save nothing can await. That asymmetry is
      // invisible in a browser and is exactly how the Enter path ends up
      // untested.
      input.addEventListener("keydown", (e) => (e.key === "Enter" ? submit() : undefined));
      select.addEventListener("change", paintStates);

      if (useSelect) {
        // A selection changes what the team spends, so it is written on change
        // rather than collected behind a second button nobody presses. The
        // warning repaints FIRST: somebody who picks a provider with no key
        // sees why before the request even lands.
        useSelect.addEventListener("change", () => {
          paintSelection();
          saveFallbackProvider({
            team,
            provider: useSelect.value,
            select: useSelect,
            status,
            onSaved: (id) => {
              selected = id;
              paintSelection();
            },
          });
        });
      }
    } else {
      const hint = document.createElement("p");
      hint.className = "apikey-hint";
      hint.textContent = TEAM_KEY_MEMBER_NOTE;
      el.appendChild(hint);
    }

    // Beside the form and never above it: a team can hold a key per provider,
    // and only the selected one is spent.
    const storeNote = document.createElement("p");
    storeNote.className = "apikey-hint";
    storeNote.textContent = TEAM_KEY_STORE_IS_NOT_SELECT;
    el.appendChild(storeNote);

    paintStates();
    el.appendChild(status);
    return { el, select, input, button, status, useSelect, useWarning };
  }

  async function removeMember(team, m) {
    const label = m.display_name || m.email || "this member";
    if (!confirm(`Remove ${label} from ${team.display_name || team.slug}?`)) return;
    setStatus(team.id, `Removing ${label}…`);
    try {
      await xbtFetch(`/v1/teams/${team.id}/members/${m.user_id}`, { method: "DELETE" });
      setStatus(team.id, `${label} removed`, "success");
      const card = document.querySelector(`[data-team-id="${team.id}"]`);
      const body = card && card.querySelector(".team-card__body");
      if (body) await fillTeamBody(team, body);
    } catch (e) {
      setStatus(team.id, `Remove failed: ${e.message}`, "error");
    }
  }

  async function leaveTeam(team, btn, statusEl) {
    if (!confirm(`Leave ${team.display_name || team.slug}? You'll lose access to its chat and memory.`)) return;
    btn.disabled = true;
    setStatus(team.id, "Leaving…");
    try {
      await xbtFetch(`/v1/teams/${team.id}/members/me`, { method: "DELETE" });
      setStatus(team.id, "Left", "success");
      setTimeout(() => renderTeamsList(), 600);
    } catch (e) {
      const msg =
        e.status === 409
          ? "You're the only admin — promote another member first."
          : `Leave failed: ${e.message}`;
      setStatus(team.id, msg, "error");
      btn.disabled = false;
    }
  }

  function setStatus(teamId, text, type) {
    const el = document.querySelector(`[data-team-status="${teamId}"]`);
    if (!el) return;
    el.textContent = text;
    el.classList.remove("is-success", "is-error");
    if (type === "success") el.classList.add("is-success");
    if (type === "error") el.classList.add("is-error");
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // Expose the API-key surface for the node suite, which imports this module
  // against a stubbed document. Functions only: no token, no key, no state.
  // Nothing here grants a capability a page script doesn't already have, since
  // the token it would need is in this origin's localStorage.
  //
  // The shared definitions are re-exposed alongside the page's own two, so the
  // suite drives THIS page through exactly the objects it uses at runtime
  // rather than through a second import of the package.
  if (typeof globalThis !== "undefined") {
    globalThis.xbrainTeamApiKeys = {
      API_KEY_PROVIDERS,
      providerLabel,
      validateApiKey,
      describeApiKeyFailure,
      readApiKeyProviders,
      buildApiKeysSection,
      saveTeamApiKey,
    };
  }
})();
