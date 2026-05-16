/**
 * xbrain Web Clipper + Session Bridge — Background Service Worker (Manifest V3)
 *
 * Responsibilities (Phase 4/8 — Web Clipper):
 *   - Obtain a Google ID token via launchWebAuthFlow (Solution A — RESEARCH.md Q6)
 *   - Cache the token in chrome.storage.session (TTL 3600s)
 *   - POST clipped payloads to memory-api (https://api.grooveos.app/v1/memory/upsert)
 *
 * Responsibilities (Phase 9 — Session Bridge):
 *   - Maintain a persistent WebSocket to wss://bridge.grooveos.app/ws/{user_sub}
 *   - Send a `register` frame on open (provider/email_logged/org_id)
 *   - Dispatch every `chat_request` from the bridge to handleClaude (claude_ai_client.js)
 *   - Exponential reconnect with jitter, 20s keepalive ping, chrome.alarms watchdog
 *
 * Responsibilities (Quick task 260512-eo1 — onboarding):
 *   - One-click mint of an xbt_ personal API token + persistent storage in
 *     chrome.storage.local so the WS bridge survives browser restarts.
 *   - Disconnect flow that revokes the token and clears storage.
 *
 * Runtime message handlers:
 *   { type: "GET_ID_TOKEN" }           → { idToken } | { error }
 *   { type: "SEND_TO_BRAIN", payload } → { ok, result } | { error }
 *   { kind: "ws_status_query" }        → { readyState }
 *   { type: "MINT_AND_CONNECT" }       → { ok, email, source_user_id } | { ok: false, error }
 *   { type: "DISCONNECT" }             → { ok }
 */

import { handleClaude, getOrgId } from "./claude_ai_client.js";
import {
  computeBackoffMs,
  PING_INTERVAL_MS,
  WATCHDOG_PERIOD_MIN,
  MAX_ATTEMPT,
} from "./ws_keepalive.js";
import {
  readStoredAuth as readStoredAuthPure,
  mintAndConnect as mintAndConnectPure,
  disconnectAuth as disconnectAuthPure,
} from "./onboarding.js";
import { loadSettings, SETTINGS_KEY } from "./settings.js";

const MEMORY_API_URL = "https://api.grooveos.app/v1/memory/upsert";
// Phase 10 GHA-07 — base URL for memory-api. Used by linkGithubFlow,
// signinGithubFlow, and fetchUserTeams. Mirrors the constant in
// onboarding.js / popup.js / options.js; keep them in sync if you change
// the host. (Pre-existing Rule 3 fix: `MEMORY_API_BASE` was referenced at
// lines 120 and 887 but never declared in this file — this declaration
// removes the ReferenceError that prevented linkGithubFlow + fetchUserTeams
// from running.)
const MEMORY_API_BASE = "https://api.grooveos.app";
// =========================================================================
// GitHub OAuth — quick task 260512-glk (Link GitHub account).
// =========================================================================
//
// Same OAuth app as LibreChat's GitHub sign-in (see GITHUB_CLIENT_ID in
// memory-api settings). The redirect URI is the chromiumapp.org one Chrome
// reserves per extension ID — must be added to the GitHub OAuth app's
// "Authorization callback URL" list (manual one-time setup in
// https://github.com/settings/applications/...).

// VITE-style placeholder; production hardcodes the same value as memory-api.
// Replace with your GitHub OAuth App client_id. Keep the secret server-side.
const GITHUB_CLIENT_ID = "Ov23liy7tZekl0uEztoj";

/**
 * Run the GitHub OAuth user-authorization flow and return the `code` Chrome
 * received from the redirect. The code is exchanged for a `gho_` token
 * server-side by memory-api (POST /v1/me/link-github-with-code).
 */
async function getGithubAuthCode() {
  const redirectUri = `https://${chrome.runtime.id}.chromiumapp.org/`;
  const state =
    Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2);
  const u = new URL("https://github.com/login/oauth/authorize");
  u.searchParams.set("client_id", GITHUB_CLIENT_ID);
  u.searchParams.set("redirect_uri", redirectUri);
  u.searchParams.set("scope", "read:user read:org user:email");
  u.searchParams.set("state", state);

  const redirectUrl = await new Promise((resolve, reject) => {
    chrome.identity.launchWebAuthFlow(
      { url: u.toString(), interactive: true },
      (result) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
          return;
        }
        if (!result) {
          reject(new Error("github auth flow returned no redirect URL"));
          return;
        }
        resolve(result);
      },
    );
  });

  const parsed = new URL(redirectUrl);
  const returnedState = parsed.searchParams.get("state");
  if (returnedState !== state) {
    throw new Error("github oauth: state mismatch — possible CSRF");
  }
  const code = parsed.searchParams.get("code");
  if (!code) {
    const err =
      parsed.searchParams.get("error_description") ||
      parsed.searchParams.get("error") ||
      "no code in redirect URL";
    throw new Error("github oauth: " + err);
  }
  return { code, redirect_uri: redirectUri };
}

/**
 * Drive the full Link GitHub flow:
 *   1. Mint a Google ID token (silent first, interactive fallback).
 *   2. Launch GitHub OAuth → get authorization `code`.
 *   3. POST {code, redirect_uri} to memory-api which exchanges + links.
 *   4. Invalidate cached team list so the next context-menu refresh picks
 *      up newly-accessible GitHub-org teams.
 *
 * Returns {ok: true, github_username, github_id, is_org_member} on success
 * or {ok: false, error} on any failure. Never throws.
 */
async function linkGithubFlow() {
  try {
    const idToken = await getGoogleIdToken({ silent: false });
    const { code, redirect_uri } = await getGithubAuthCode();
    const r = await fetch(`${MEMORY_API_BASE}/v1/me/link-github-with-code`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${idToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ code, redirect_uri }),
    });
    if (!r.ok) {
      const body = await r.text().catch(() => "");
      return {
        ok: false,
        error: `link failed: ${r.status} ${body.slice(0, 200)}`,
      };
    }
    const payload = await r.json();
    // Force a context menu refresh so GitHub-org teams appear in the submenu.
    await chrome.storage.local.remove([TEAMS_CACHE_KEY, TEAMS_CACHE_TS_KEY]);
    refreshContextMenus();
    return { ok: true, ...payload };
  } catch (e) {
    return { ok: false, error: String(e && e.message ? e.message : e) };
  }
}

/**
 * Phase 10 GHA-07 — sign in via GitHub directly, no Google required.
 *
 *   1. Launch GitHub OAuth → get authorization `code` (reuses getGithubAuthCode).
 *   2. POST {code, redirect_uri, state} to memory-api /v1/auth/github/signin.
 *   3. Receive xbt_token + user info, store in chrome.storage.local UNDER
 *      THE CANONICAL KEYS xbt_token / user_sub / api_token_id (matches
 *      onboarding.js + every reader in the extension).
 *   4. Force context-menu refresh so org-derived teams appear.
 *
 * Returns {ok: true, source_user_id, email, github_username} or {ok: false, error}.
 *
 * REVISION 1 B-1 fix — CANONICAL chrome.storage keys.
 *
 * These EXACT key names are required by every reader in the extension:
 *   - background.js:884 reads xbt_token (fetchUserTeams)
 *   - background.js:653 reads xbt_token + user_sub (openBridgeWS)
 *   - background.js:794 watches changes.xbt_token (WS reconnect trigger)
 *   - popup.js (5 sites), options.js, librechat_autofill.js all read xbt_token
 *   - onboarding.js writes {xbt_token, user_sub, api_token_id}
 *
 * If we wrote `xbrain_xbt_token` etc., the WS reconnect listener would never
 * fire, the popup chat UI would stay unauthenticated, and right-click context
 * menus would not refresh. The endpoint would succeed but the UX would read
 * as broken. DO NOT rename to "xbrain_*" prefixes — see PLAN-CHECK.md B-1.
 *
 * user_sub semantics: the source_user_id of the user row ("github:<login>"
 * for GitHub-primary users). The bridge keys its WS connection on this value.
 *
 * Server contract: /v1/auth/github/signin response shape is currently
 *   { xbt_token, user: {id, email, display_name, github_username, teams_joined} }
 * — it does NOT yet surface source_user_id or api_token_id. We derive
 * user_sub from github_username (canonical "github:<login>" format) and
 * leave api_token_id null. If a future 10-02 patch surfaces these fields
 * directly, the code below picks them up automatically.
 */
async function signinGithubFlow() {
  try {
    const { code, redirect_uri } = await getGithubAuthCode();
    // getGithubAuthCode generates and validates its own state internally
    // (CSRF for the extension trust boundary). The state field on the
    // signin body is required by the memory-api Pydantic model but is not
    // used for re-validation server-side — generate a fresh one for the
    // wire payload (logged for audit only).
    const state =
      Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2);
    const r = await fetch(`${MEMORY_API_BASE}/v1/auth/github/signin`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, redirect_uri, state }),
    });
    if (!r.ok) {
      const body = await r.text().catch(() => "");
      return {
        ok: false,
        error: `signin failed: ${r.status} ${body.slice(0, 200)}`,
      };
    }
    const { xbt_token, user } = await r.json();
    if (!xbt_token) {
      return { ok: false, error: "signin response missing xbt_token" };
    }

    // === CANONICAL chrome.storage keys (DO NOT rename — see B-1 above). ===
    // Prefer server-provided source_user_id / api_token_id if present (future
    // 10-02 patch), else fall back to deriving from github_username.
    const user_sub =
      (user && user.source_user_id) ||
      (user && user.github_username ? `github:${user.github_username}` : null);
    const api_token_id = (user && user.api_token_id) || null;

    if (!user_sub) {
      return {
        ok: false,
        error: "signin response missing github_username (cannot derive user_sub)",
      };
    }

    await chrome.storage.local.set({
      xbt_token: xbt_token, // canonical — watched by background.js storage.onChanged
      user_sub: user_sub, // canonical — used for WS connection URL
      api_token_id: api_token_id, // canonical — used for revoke flow (null OK)
    });
    // Invalidate cached teams and refresh context menus so org-derived teams
    // (newly auto-granted via auto_grant_via_org_match) appear in the
    // right-click "CortX OS" submenu.
    await chrome.storage.local.remove([TEAMS_CACHE_KEY, TEAMS_CACHE_TS_KEY]);
    refreshContextMenus();
    return {
      ok: true,
      source_user_id: user_sub,
      email: user && user.email,
      github_username: user && user.github_username,
      teams_joined: (user && user.teams_joined) || [],
    };
  } catch (e) {
    return { ok: false, error: String(e && e.message ? e.message : e) };
  }
}

// Google OAuth client (web application type — works with launchWebAuthFlow).
// NOTE: chrome.identity.getAuthToken would require a "Chrome App" OAuth
// client_id in Google Cloud Console (different type, requires extra setup
// in the console). We use launchWebAuthFlow with a silent-first strategy to
// achieve the same zero-popup UX without that extra config.
const CLIENT_ID = "50097563098-rdh24v05dcp0ees8o4kqviuuoi5sup3n.apps.googleusercontent.com";
const TOKEN_CACHE_KEY = "xbrain_id_token";
const TOKEN_EXPIRY_KEY = "xbrain_id_token_expiry";
const TOKEN_TTL_MS = 3600 * 1000; // 1 hour

/**
 * Build a Google OAuth /authorize URL for the implicit ID-token flow.
 */
function buildGoogleAuthUrl({ promptMode }) {
  const redirectUri = `https://${chrome.runtime.id}.chromiumapp.org/`;
  const nonce =
    Math.random().toString(36).substring(2) +
    Math.random().toString(36).substring(2);
  const u = new URL("https://accounts.google.com/o/oauth2/v2/auth");
  u.searchParams.set("client_id", CLIENT_ID);
  u.searchParams.set("response_type", "id_token");
  u.searchParams.set("redirect_uri", redirectUri);
  u.searchParams.set("scope", "openid email profile");
  u.searchParams.set("nonce", nonce);
  if (promptMode) u.searchParams.set("prompt", promptMode);
  return u.toString();
}

/**
 * Run launchWebAuthFlow and extract the id_token from the redirect URL hash.
 * `interactive: false` runs silently — no window opens. If the user hasn't
 * granted consent yet (or hasn't been re-prompted since revoke), the call
 * fails synchronously and the caller can fall back to interactive mode.
 */
function launchAuthFlow({ interactive, promptMode }) {
  return new Promise((resolve, reject) => {
    chrome.identity.launchWebAuthFlow(
      { url: buildGoogleAuthUrl({ promptMode }), interactive },
      (redirectUrl) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
          return;
        }
        if (!redirectUrl) {
          reject(new Error("auth flow returned no redirect URL"));
          return;
        }
        const hash = new URL(redirectUrl).hash.substring(1);
        const params = new URLSearchParams(hash);
        const idToken = params.get("id_token");
        if (!idToken) {
          reject(new Error("redirect URL missing id_token"));
          return;
        }
        resolve(idToken);
      },
    );
  });
}

// Module-level in-flight promise for getGoogleIdToken. Two concurrent calls
// (e.g. Web Clipper team-load + auto-mint firing at popup open) used to each
// trigger their own launchWebAuthFlow → two consent popups. Sharing one
// promise makes them coalesce into a single auth flow.
// Reset to null in `finally` so cache misses on later calls work correctly.
let _pendingTokenPromise = null;

/**
 * Get a Google OIDC ID token (JWT, signed by Google).
 *
 * @param {Object} opts
 * @param {boolean} opts.silent  When true, only attempt the silent
 *   (interactive:false) auth flow. If silent fails, throw — DO NOT escalate
 *   to interactive. Used by auto-mint and the Web Clipper team-load so the
 *   extension never opens a consent popup without an explicit user click.
 *
 * Strategy:
 *   1. Check our chrome.storage.session cache (1h TTL).
 *   2. Try launchWebAuthFlow with `interactive: false` — no window opens.
 *      Succeeds if user is signed into Google in Chrome AND has previously
 *      granted consent to this extension.
 *   3. If `opts.silent` is true → throw on silent failure.
 *      Otherwise fall back to `interactive: true` (small Chrome popup).
 *
 * Concurrent calls share one in-flight promise (no duplicate consent popups).
 *
 * Memory-api accepts both Google ID tokens and OAuth2 access tokens since
 * 27e0a8d — we ship ID tokens here because launchWebAuthFlow returns them.
 */
async function getGoogleIdToken(opts = {}) {
  const silent = opts.silent === true;

  // 1. Cache hit — return synchronously without involving _pendingTokenPromise.
  const stored = await chrome.storage.session.get([
    TOKEN_CACHE_KEY,
    TOKEN_EXPIRY_KEY,
  ]);
  if (
    stored[TOKEN_CACHE_KEY] &&
    stored[TOKEN_EXPIRY_KEY] &&
    Date.now() < stored[TOKEN_EXPIRY_KEY]
  ) {
    return stored[TOKEN_CACHE_KEY];
  }

  // 2. Concurrent dedup — coalesce parallel auth flows into one.
  // If a silent call is already in flight and the new caller wants
  // interactive, we wait for the silent result; if it fails, the caller
  // sees the error (they can retry interactively).
  if (_pendingTokenPromise) {
    return _pendingTokenPromise;
  }

  _pendingTokenPromise = (async () => {
    let idToken = null;
    // 5-second timeout so a hung launchAuthFlow can't keep the popup's
    // loading state stuck indefinitely. Chrome MV3 has known edge cases where
    // interactive:false hangs instead of erroring; the timeout converts that
    // into a clean "silent failed" signal so the manual Connect button
    // surfaces immediately.
    const SILENT_TIMEOUT_MS = 5000;
    try {
      idToken = await Promise.race([
        launchAuthFlow({ interactive: false, promptMode: "none" }),
        new Promise((_, reject) =>
          setTimeout(
            () => reject(new Error("silent auth timeout")),
            SILENT_TIMEOUT_MS,
          ),
        ),
      ]);
    } catch {
      // Silent path failed or timed out — fall through.
    }

    if (!idToken) {
      if (silent) {
        throw new Error("silent auth failed (no consent yet, or not signed into Chrome)");
      }
      idToken = await launchAuthFlow({
        interactive: true,
        promptMode: "select_account",
      });
    }

    await chrome.storage.session.set({
      [TOKEN_CACHE_KEY]: idToken,
      [TOKEN_EXPIRY_KEY]: Date.now() + TOKEN_TTL_MS,
    });
    return idToken;
  })();

  try {
    return await _pendingTokenPromise;
  } finally {
    _pendingTokenPromise = null;
  }
}

/**
 * Clear the locally-cached Google ID token. Called on disconnect so a
 * subsequent silent attempt doesn't reuse a token tied to the prior account.
 *
 * NOTE: with launchWebAuthFlow there's no Chrome-side cache to revoke (unlike
 * getAuthToken's removeCachedAuthToken). The token only exists in our own
 * chrome.storage.session, so clearing that is sufficient.
 */
async function clearGoogleAuthToken() {
  await chrome.storage.session.remove([TOKEN_CACHE_KEY, TOKEN_EXPIRY_KEY]);
}

/**
 * Envoyer un payload de mémoire à api.grooveos.app.
 * Le payload doit contenir les champs du contrat de tagging xbrain.
 */
async function sendToBrain(idToken, payload) {
  const response = await fetch(MEMORY_API_URL, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${idToken}`,
      "X-Team-Scope": payload.team_scope,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      item: {
        content: payload.content,
        team_scope: payload.team_scope,
        project_scope: payload.project_scope || null,
        visibility: payload.visibility || "team",
        confidence: payload.confidence !== undefined ? payload.confidence : 1.0,
        truth_level: payload.truth_level || "EPHEMERAL",
        source: payload.source || "chrome:unknown",
        validation_status: payload.validation_status || "pending",
      },
    }),
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(`HTTP ${response.status}: ${body}`);
  }

  return response.json();
}

// ===========================================================================
// Quick task 260512-eo1 — onboarding glue
// Pure logic lives in ./onboarding.js so node tests can import it without
// polyfilling chrome.*. The wrappers below bind chrome.storage + global fetch.
// ===========================================================================

async function readStoredAuth() {
  return readStoredAuthPure(chrome.storage);
}

async function mintAndConnect({ silent = false } = {}) {
  return mintAndConnectPure({
    fetch,
    // When silent=true, never escalate to an interactive Chrome popup —
    // the caller (auto-mint on popup open) only wants the zero-click path.
    getIdToken: () => getGoogleIdToken({ silent }),
    storage: chrome.storage.local,
  });
}

async function disconnectAuth() {
  // Also drop Chrome's cached Google access token so a subsequent connect
  // can pick a different account (otherwise getAuthToken would silently
  // reuse the same identity).
  await clearGoogleAuthToken();
  return disconnectAuthPure({
    fetch,
    storage: chrome.storage.local,
    sessionStorage: chrome.storage.session,
    closeWs: () => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.close(1000, "user disconnected");
      }
    },
  });
}

/**
 * Runtime message dispatcher — listens for messages from the popup and other
 * extension components.
 */
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  // Threat T-09-03-02: reject any message not from this extension itself.
  if (sender && sender.id && sender.id !== chrome.runtime.id) {
    return false;
  }

  if (message.type === "GET_ID_TOKEN") {
    // Callers can opt into silent-only behavior (no consent popup) by
    // passing { silent: true }. Default is the legacy behavior (silent
    // first, interactive fallback) — preserves the manual Send-to-brain UX.
    const silent = message.silent === true;
    getGoogleIdToken({ silent })
      .then((idToken) => sendResponse({ idToken }))
      .catch((err) => sendResponse({ error: err.message }));
    return true; // sendResponse will be called asynchronously
  }

  if (message.type === "SEND_TO_BRAIN") {
    const { idToken, payload } = message;
    if (!idToken) {
      sendResponse({ error: "No ID token provided" });
      return true;
    }
    sendToBrain(idToken, payload)
      .then((result) => sendResponse({ ok: true, result }))
      .catch((err) => sendResponse({ error: err.message }));
    return true; // async
  }

  // Phase 9 — consumed by popup.js (plan 09-05) to render 🟢/🔴.
  if (message && message.kind === "ws_status_query") {
    sendResponse({
      readyState: ws ? ws.readyState : -1,
      last_open_ms: lastOpenAt,
    });
    return false; // synchronous reply
  }

  // Phase 10 GHA-07 — sign in via GitHub directly (no Google required).
  // Placed BEFORE MINT_AND_CONNECT so the GitHub-primary popup button has
  // priority dispatch. Mirrors the LINK_GITHUB shape (async sendResponse,
  // return true to keep the channel open).
  if (message && message.type === "SIGNIN_GITHUB") {
    signinGithubFlow().then(sendResponse);
    return true; // async
  }

  // Quick task 260512-eo1 — single-click onboarding.
  // Quick task 260512-cmu — silent flag forwarded so the popup's auto-mint
  // path never opens a consent popup without an explicit user click.
  if (message && message.type === "MINT_AND_CONNECT") {
    const silent = message.silent === true;
    mintAndConnect({ silent }).then(sendResponse);
    return true; // async
  }

  if (message && message.type === "DISCONNECT") {
    disconnectAuth().then(sendResponse);
    return true; // async
  }

  // Quick task 260512-glk — Link GitHub account to the Google-authenticated user.
  if (message && message.type === "LINK_GITHUB") {
    linkGithubFlow().then(sendResponse);
    return true; // async
  }

  // Settings page — read current claude.ai session info (email + subscription).
  if (message && message.type === "GET_CLAUDE_SESSION_INFO") {
    fetchClaudeSessionInfo().then(sendResponse);
    return true; // async
  }

  // Popup signals "I just created/joined a team — please rebuild the right-
  // click context menu so my new team shows up in the CortX OS submenu".
  if (message && message.type === "REFRESH_TEAMS_MENU") {
    refreshContextMenus()
      .then(() => sendResponse({ ok: true }))
      .catch((e) => sendResponse({ ok: false, error: String(e && e.message ? e.message : e) }));
    return true; // async
  }

  // Settings page — force the bridge WS to reconnect so the register frame
  // re-detects the active claude.ai session (after the user switched account).
  if (message && message.type === "REFRESH_CLAUDE_SESSION") {
    refreshClaudeSession().then(sendResponse);
    return true; // async
  }

  // Unknown message — don't block.
  return false;
});

// ===========================================================================
// Phase 9 — Session Bridge WebSocket layer
// ===========================================================================

const BRIDGE_WS_URL_TEMPLATE = "wss://bridge.grooveos.app/ws/{sub}?token={token}";
let ws = null;
let reconnectAttempt = 0;
let pingTimer = null;
let reconnectTimer = null;
let lastOpenAt = 0;

/**
 * Best-effort fetch of the email logged into claude.ai. Returns null on failure
 * (non-blocking — the register frame goes out with email_logged=null if the
 * request fails, e.g. when the user isn't logged into claude.ai yet).
 */
/**
 * Best-effort introspection of the user's claude.ai session.
 * Returns:
 *   {
 *     signed_in: bool,
 *     email: string|null,
 *     subscription: string ("Pro" / "Max" / "Max 5x" / ... / "Unknown"),
 *     organization_name: string|null,
 *     organization_uuid: string|null,
 *     debug: {                       // for Settings diagnostics
 *       account_status: number|"err"
 *       account_body_keys: string[]
 *       orgs_status: number|"err"
 *       orgs_first_keys: string[]
 *       capabilities: string[]
 *     }
 *   }
 * Never throws — every failure path surfaces enough info to debug from
 * the UI without opening the SW devtools.
 */
async function fetchClaudeSessionInfo() {
  const out = {
    signed_in: false,
    email: null,
    subscription: "Unknown",
    organization_name: null,
    organization_uuid: null,
    debug: {
      account_status: null,
      account_body_keys: [],
      orgs_status: null,
      orgs_first_keys: [],
      capabilities: [],
    },
  };
  // Some claude.ai endpoints require the platform header set on web; without
  // it some responses come back as 403 even with valid cookies.
  const headers = {
    Accept: "application/json",
    "anthropic-client-platform": "web_claude_ai",
  };

  // 1. Try /api/account (newer surface) then fall back to /api/auth/current_account
  for (const url of [
    "https://claude.ai/api/account",
    "https://claude.ai/api/auth/current_account",
  ]) {
    try {
      const r = await fetch(url, { credentials: "include", headers });
      out.debug.account_status = r.status;
      if (!r.ok) continue;
      const j = await r.json();
      out.debug.account_body_keys = Object.keys(j || {});
      const email =
        j.email_address ||
        j.email ||
        (j.account && (j.account.email_address || j.account.email)) ||
        (j.user && (j.user.email_address || j.user.email)) ||
        null;
      if (email) {
        out.email = email;
        out.signed_in = true;
        break;
      }
    } catch (e) {
      out.debug.account_status = "err:" + (e && e.message ? e.message : String(e));
    }
  }

  // 2. Subscription from /api/organizations capabilities
  try {
    const r = await fetch("https://claude.ai/api/organizations", {
      credentials: "include",
      headers,
    });
    out.debug.orgs_status = r.status;
    if (r.ok) {
      const orgs = await r.json();
      if (Array.isArray(orgs) && orgs.length) {
        const o = orgs[0];
        out.debug.orgs_first_keys = Object.keys(o);
        out.organization_uuid = o.uuid || o.id || null;
        out.organization_name = o.name || null;
        const caps = (o.capabilities || []).map((c) => String(c).toLowerCase());
        out.debug.capabilities = caps;
        // If we hadn't gotten an email from /api/account, fall back to the org's
        // billing_email or capabilities-based heuristic.
        if (!out.email && (o.billing_email || o.email)) {
          out.email = o.billing_email || o.email;
          out.signed_in = true;
        }
        // Most-specific match first.
        if (caps.some((c) => c.includes("max_20x"))) out.subscription = "Max 20x";
        else if (caps.some((c) => c.includes("max_5x"))) out.subscription = "Max 5x";
        else if (caps.some((c) => c.includes("max"))) out.subscription = "Max";
        else if (caps.some((c) => c.includes("pro"))) out.subscription = "Pro";
        else if (caps.some((c) => c.includes("team"))) out.subscription = "Team";
        else if (caps.some((c) => c.includes("enterprise"))) out.subscription = "Enterprise";
        else if (caps.length === 0 || caps.every((c) => c === "chat")) out.subscription = "Free";
        // If we have an org but still no email, assume signed_in (Workspace
        // sessions sometimes omit email at the account endpoint).
        if (!out.signed_in && out.organization_uuid) {
          out.signed_in = true;
        }
      }
    }
  } catch (e) {
    out.debug.orgs_status = "err:" + (e && e.message ? e.message : String(e));
  }
  return out;
}

/**
 * Tear down the current bridge WS, clear cached claude.ai introspection
 * state, then re-open the WS so the server-side register frame gets a
 * fresh email + org_id. Use after the user has logged into a different
 * claude.ai account in this Chrome window.
 */
async function refreshClaudeSession() {
  try {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      ws.close(1000, "user requested refresh");
    }
    ws = null;
    // Give the close a tick to flush before reconnecting.
    await new Promise((r) => setTimeout(r, 200));
    await openBridgeWS();
    return { ok: true };
  } catch (e) {
    return { ok: false, error: String(e && e.message ? e.message : e) };
  }
}

async function fetchClaudeEmail() {
  try {
    const r = await fetch("https://claude.ai/api/auth/current_account", {
      credentials: "include",
    });
    if (!r.ok) return null;
    const j = await r.json();
    return (
      j.email_address ||
      j.email ||
      (j.account && (j.account.email_address || j.account.email)) ||
      null
    );
  } catch {
    return null;
  }
}

/**
 * Open (or reuse) the WebSocket to session-bridge.
 * Idempotent — does not reopen if a socket is already OPEN/CONNECTING.
 */
async function openBridgeWS() {
  if (
    ws &&
    (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)
  ) {
    return; // idempotent
  }
  const { xbt_token, user_sub } = await readStoredAuth();
  if (!xbt_token || !user_sub) {
    console.warn("[xbrain] no token/sub yet, deferring WS open");
    return;
  }
  const url = BRIDGE_WS_URL_TEMPLATE
    .replace("{sub}", encodeURIComponent(user_sub))
    .replace("{token}", encodeURIComponent(xbt_token));

  console.log("[xbrain] opening WS");
  ws = new WebSocket(url);

  ws.onopen = async () => {
    console.log("[xbrain] WS open");
    reconnectAttempt = 0;
    lastOpenAt = Date.now();
    startPing();

    // Threat T-09-03-04: never throw — register is best-effort, nulls allowed.
    let org_id = null;
    let email_logged = null;
    try {
      org_id = await getOrgId();
    } catch (e) {
      console.warn("[xbrain] getOrgId failed", e && e.message ? e.message : e);
    }
    try {
      email_logged = await fetchClaudeEmail();
    } catch (e) {
      console.warn(
        "[xbrain] fetchClaudeEmail failed",
        e && e.message ? e.message : e,
      );
    }
    const extension_id = chrome.runtime.id || null;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(
        JSON.stringify({
          type: "register",
          provider: "claude",
          extension_id,
          email_logged,
          org_id,
        }),
      );
      console.log("[xbrain] register sent", {
        email_logged: email_logged ? "<set>" : null,
        org_id: org_id ? "<set>" : null,
      });
    }
  };

  ws.onmessage = async (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch {
      return;
    }
    if (msg.type === "chat_request") {
      const sendFrame = (envelope) => {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify(envelope));
        }
      };
      try {
        await handleClaude(msg, sendFrame);
      } catch (e) {
        sendFrame({
          request_id: msg.request_id,
          type: "error",
          detail: { message: String(e && e.message ? e.message : e) },
        });
      }
      return;
    }
    // type === "register_ack" | "ping" | "pong" | autre  → ignorer en silence
  };

  ws.onclose = (event) => {
    console.warn("[xbrain] WS closed", event.code, event.reason);
    stopPing();
    ws = null;
    // 4401/4403 = auth failure — don't retry until the token has been refreshed.
    if (event.code !== 4401 && event.code !== 4403) {
      scheduleReconnect();
    }
  };

  ws.onerror = (e) => {
    console.error("[xbrain] WS error", e && e.message ? e.message : e);
  };
}

function startPing() {
  if (pingTimer) return;
  pingTimer = setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "ping", ts: Date.now() }));
    }
  }, PING_INTERVAL_MS);
}

function stopPing() {
  if (pingTimer) {
    clearInterval(pingTimer);
    pingTimer = null;
  }
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectAttempt = Math.min(reconnectAttempt + 1, MAX_ATTEMPT);
  const delay = computeBackoffMs(reconnectAttempt);
  console.log(
    `[xbrain] reconnect in ${Math.round(delay)}ms (attempt ${reconnectAttempt})`,
  );
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    openBridgeWS();
  }, delay);
}

// chrome.alarms watchdog — reopens the WS if the SW was killed (MV3 idle).
chrome.alarms.create("xbrain_ws_watchdog", {
  periodInMinutes: WATCHDOG_PERIOD_MIN,
});
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name !== "xbrain_ws_watchdog") return;
  if (!ws || ws.readyState === WebSocket.CLOSED) {
    console.log("[xbrain] watchdog re-opening WS");
    openBridgeWS();
  }
});

// Reopen the WS whenever xbt_token or user_sub appears / changes in storage.
// Listen on both `local` (canonical since quick task 260512-eo1) and `session`
// (legacy bootstrap) so manually-bootstrapped sessions still trigger a reconnect.
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local" && area !== "session") return;
  if (
    (changes.xbt_token || changes.user_sub) &&
    (!ws || ws.readyState === WebSocket.CLOSED)
  ) {
    openBridgeWS();
  }
});

// Browser startup — chrome.runtime.onStartup fires BEFORE the SW is
// re-instantiated on idle, but we also trigger openBridgeWS() at top-level
// to handle install/upgrade.
chrome.runtime.onStartup.addListener(() => {
  openBridgeWS();
  applyPanelBehavior();
});

// Open on SW boot
openBridgeWS();

// ===========================================================================
// Quick task 260512-spx — side panel mode (Chrome 114+)
// ===========================================================================
//
// chrome.sidePanel.setPanelBehavior({openPanelOnActionClick: true}) makes
// clicking the toolbar icon open the side panel instead of the popup.
// When the user toggles the setting OFF, falling back to popup mode is
// achieved by setting openPanelOnActionClick: false (the action.default_popup
// in manifest.json then takes effect).

/**
 * Read the current setting and configure side panel behavior accordingly.
 * No-op (and silently caught) on Chrome < 114 where chrome.sidePanel is absent.
 */
async function applyPanelBehavior() {
  try {
    if (!chrome.sidePanel || !chrome.sidePanel.setPanelBehavior) return;
    const settings = await loadSettings(chrome.storage.sync);
    await chrome.sidePanel.setPanelBehavior({
      openPanelOnActionClick: settings.openInSidePanel === true,
    });
  } catch (e) {
    console.warn(
      "[xbrain] applyPanelBehavior failed:",
      e && e.message ? e.message : e,
    );
  }
}

// Apply once on SW boot.
applyPanelBehavior();

// React to settings changes (Options page writes to chrome.storage.sync).
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "sync") return;
  if (changes[SETTINGS_KEY]) {
    applyPanelBehavior();
  }
});

// ===========================================================================
// Right-click "CortX OS" context menu with per-team submenus
// (quick task 260512-cmu + 260512-csm dynamic submenu refresh)
// ===========================================================================
//
// Parent: "CortX OS"
//   ├─ Add selection to <Team A>   (id: xbrain_add_team:<slug-a>)
//   ├─ Add selection to <Team B>   (id: xbrain_add_team:<slug-b>)
//   └─ Connect xbrain account…     (id: xbrain_connect_first) [shown only when no teams]
//
// On click: stash {selectedText, url, title, team_scope} in
// chrome.storage.session.pending_selection. The popup reads + clears this on
// open and pre-selects the team in the dropdown.

const CONTEXT_MENU_PARENT_ID = "xbrain_parent";
const CONTEXT_MENU_CONNECT_ID = "xbrain_connect_first";
const CONTEXT_MENU_TEAM_PREFIX = "xbrain_add_team:";

const TEAMS_CACHE_KEY = "xbrain_teams_cache";
const TEAMS_CACHE_TS_KEY = "xbrain_teams_cache_ts";
const TEAMS_CACHE_TTL_MS = 24 * 3600 * 1000; // 24h — teams change rarely

/**
 * Fetch the user's teams from memory-api. Returns null on auth failure or
 * network error so the caller can fall back to a "connect first" menu.
 *
 * Uses the persistent xbt_ token from chrome.storage.local — survives
 * browser restarts where the in-memory Google ID token cache would be
 * empty. /v1/teams/my-teams accepts both Google ID tokens and xbt_
 * since commit 019d936.
 */
async function fetchUserTeams() {
  const { xbt_token } = await chrome.storage.local.get(["xbt_token"]);
  if (!xbt_token) return null;
  try {
    const r = await fetch(`${MEMORY_API_BASE}/v1/teams/my-teams`, {
      headers: { Authorization: `Bearer ${xbt_token}` },
    });
    if (!r.ok) return null;
    const teams = await r.json();
    if (!Array.isArray(teams)) return null;
    return teams.map((t) => ({
      slug: t.slug,
      display_name: t.display_name || t.slug,
    }));
  } catch {
    return null;
  }
}

/**
 * Load cached teams from chrome.storage.local. Returns null if cache is
 * empty or older than TEAMS_CACHE_TTL_MS.
 */
async function loadCachedTeams() {
  const stored = await chrome.storage.local.get([
    TEAMS_CACHE_KEY,
    TEAMS_CACHE_TS_KEY,
  ]);
  const ts = stored[TEAMS_CACHE_TS_KEY] || 0;
  if (Date.now() - ts > TEAMS_CACHE_TTL_MS) return null;
  return stored[TEAMS_CACHE_KEY] || null;
}

async function saveCachedTeams(teams) {
  await chrome.storage.local.set({
    [TEAMS_CACHE_KEY]: teams,
    [TEAMS_CACHE_TS_KEY]: Date.now(),
  });
}

/**
 * Build the context menu tree from a (possibly null) teams list.
 *
 * Always rebuild from scratch via removeAll() — contextMenus has no "update
 * the children" API and tracking individual ids gets fragile across reloads.
 */
// Three contexts handled — Chrome routes the click into our handler with the
// matching info shape:
//   page      → info.pageUrl, no selection / no media
//   selection → info.selectionText set
//   image     → info.mediaType === "image", info.srcUrl set
const CONTEXT_MENU_CONTEXTS = ["page", "selection", "image"];

async function rebuildContextMenu(teams) {
  if (!chrome.contextMenus || !chrome.contextMenus.create) return;
  await new Promise((resolve) =>
    chrome.contextMenus.removeAll(() => resolve()),
  );

  // Parent — visible on page (no selection / no image), text selection, OR image.
  chrome.contextMenus.create(
    {
      id: CONTEXT_MENU_PARENT_ID,
      title: "CortX OS",
      contexts: CONTEXT_MENU_CONTEXTS,
    },
    () => void chrome.runtime.lastError,
  );

  if (Array.isArray(teams) && teams.length > 0) {
    for (const t of teams) {
      chrome.contextMenus.create(
        {
          id: CONTEXT_MENU_TEAM_PREFIX + t.slug,
          parentId: CONTEXT_MENU_PARENT_ID,
          // Generic "Send to <Team>" — Chrome shows the same label across
          // all three contexts. The handler picks the right payload based
          // on info.mediaType / info.selectionText.
          title: `Send to ${t.display_name}`,
          contexts: CONTEXT_MENU_CONTEXTS,
        },
        () => void chrome.runtime.lastError,
      );
    }
  } else {
    chrome.contextMenus.create(
      {
        id: CONTEXT_MENU_CONNECT_ID,
        parentId: CONTEXT_MENU_PARENT_ID,
        title: "Connect xbrain account…",
        contexts: CONTEXT_MENU_CONTEXTS,
      },
      () => void chrome.runtime.lastError,
    );
  }
}

/**
 * Full refresh: render cached menu immediately for snappy UX, then kick off
 * a background fetch and rebuild with the live result.
 *
 * Safe to call from boot, onInstalled, or storage.onChanged.
 */
async function refreshContextMenus() {
  const cached = await loadCachedTeams();
  await rebuildContextMenu(cached);

  // Background refresh (fire-and-forget — never blocks the menu rebuild).
  fetchUserTeams().then(async (teams) => {
    if (!teams) return; // silent auth failed or network error — keep cached menu
    await saveCachedTeams(teams);
    await rebuildContextMenu(teams);
  });
}

chrome.runtime.onInstalled.addListener(() => {
  refreshContextMenus();
});

// SW boot path — onInstalled fires only on install/update; rebuild every boot.
refreshContextMenus();

// When the user just connected (xbt_token appears in storage.local), the
// silent Google auth path is now warm — re-fetch the team list so the menu
// updates from "Connect xbrain account…" to per-team items.
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local") return;
  if (changes.xbt_token) {
    refreshContextMenus();
  }
});

/**
 * Build the memory_items payload for a right-click clip event.
 * Picks the mode based on what the user actually right-clicked:
 *   image     → "Image: <srcUrl>\n\nFrom <title> <pageUrl>"     source=chrome:image:<host>
 *   selection → "From <title> <pageUrl>\n\n<selectionText>"     source=chrome:<host>
 *   page      → "<title>\n<pageUrl>"                            source=chrome:<host>
 * In all three modes the page URL is always attached so the memory item
 * is self-contained when listed later.
 */
function _buildRightClickPayload(info, tab) {
  const pageUrl = info.pageUrl || (tab && tab.url) || "";
  const pageTitle = (tab && tab.title) || "";
  let host = "unknown";
  try { host = new URL(pageUrl).hostname; } catch {}

  if (info.mediaType === "image" && info.srcUrl) {
    const header = pageTitle
      ? `From ${pageTitle} <${pageUrl}>`
      : `From ${pageUrl}`;
    return {
      content: `Image: ${info.srcUrl}\n\n${header}`,
      source: `chrome:image:${host}`,
      mode: "image",
    };
  }
  const selection = (info.selectionText || "").trim();
  if (selection) {
    const header = pageTitle
      ? `From ${pageTitle} <${pageUrl}>`
      : `From ${pageUrl}`;
    return {
      content: `${header}\n\n${selection}`,
      source: `chrome:${host}`,
      mode: "selection",
    };
  }
  return {
    content: pageTitle ? `${pageTitle}\n${pageUrl}` : pageUrl,
    source: `chrome:${host}`,
    mode: "page",
  };
}

function _notify(title, message) {
  if (!chrome.notifications || !chrome.notifications.create) return;
  chrome.notifications.create({
    type: "basic",
    iconUrl: "icon128.png",
    title,
    message,
    priority: 1,
  });
}

chrome.contextMenus &&
  chrome.contextMenus.onClicked &&
  chrome.contextMenus.onClicked.addListener(async (info, tab) => {
    const id = String(info.menuItemId || "");

    // Branch 1: "Connect xbrain account…" — surface the side panel.
    if (id === CONTEXT_MENU_CONNECT_ID) {
      await openPanelOrPopup(tab);
      return;
    }

    // Branch 2: a team item — direct-send to memory-api, then notify.
    if (id.startsWith(CONTEXT_MENU_TEAM_PREFIX)) {
      const teamScope = id.slice(CONTEXT_MENU_TEAM_PREFIX.length);
      const payload = _buildRightClickPayload(info, tab);
      if (!payload.content.trim()) {
        _notify("xbrain — nothing to clip", "No URL, title, selection or image found.");
        return;
      }

      // Need a Google ID token for /v1/memory/upsert (kind=user requirement).
      // Use silent path so the right-click never opens a Google consent popup
      // unexpectedly. If silent fails (not connected), fall back to opening
      // the panel so the user can sign in — selection is stashed so it isn't
      // lost.
      let idToken = null;
      try {
        idToken = await getGoogleIdToken({ silent: true });
      } catch {
        idToken = null;
      }
      if (!idToken) {
        await chrome.storage.session.set({
          pending_selection: {
            selectedText: payload.content,
            team_scope: teamScope,
            url: info.pageUrl || (tab && tab.url) || "",
            title: (tab && tab.title) || "",
            captured_at: Date.now(),
          },
        });
        await openPanelOrPopup(tab);
        return;
      }

      // Pull clip defaults (project + truth_level) from sync settings.
      const settings = await loadSettings(chrome.storage.sync);

      try {
        await sendToBrain(idToken, {
          content: payload.content,
          team_scope: teamScope,
          project_scope: settings.clipDefaultProject || null,
          visibility: "team",
          confidence: 1.0,
          truth_level: settings.clipDefaultTruthLevel || "EPHEMERAL",
          source: payload.source,
          validation_status: "pending",
        });
        const modeLabel =
          payload.mode === "image"
            ? "Image"
            : payload.mode === "selection"
            ? "Selection"
            : "Page";
        _notify(`xbrain — ${modeLabel} clipped ✓`, `Sent to ${teamScope}`);
      } catch (e) {
        console.warn("[xbrain] right-click clip failed:", e);
        _notify("xbrain — clip failed", String(e && e.message ? e.message : e));
      }
      return;
    }
  });

/**
 * Open the side panel (if user opted in) or the toolbar popup. Falls back to
 * a notification on Chrome < 127 where neither programmatic open API exists.
 *
 * Extracted from the previous inline copy so both context-menu branches
 * (team click + connect-first click) share the same surface logic.
 */
async function openPanelOrPopup(tab) {
  try {
    const settings = await loadSettings(chrome.storage.sync);
    if (
      settings.openInSidePanel &&
      chrome.sidePanel &&
      chrome.sidePanel.open &&
      tab &&
      tab.id
    ) {
      await chrome.sidePanel.open({ tabId: tab.id });
      return;
    }
    if (chrome.action && chrome.action.openPopup) {
      await chrome.action.openPopup();
      return;
    }
  } catch (e) {
    console.warn(
      "[xbrain] context menu open failed:",
      e && e.message ? e.message : e,
    );
  }
  if (chrome.notifications && chrome.notifications.create) {
    chrome.notifications.create({
      type: "basic",
      iconUrl: "icon128.png",
      title: "xbrain — selection captured",
      message: "Click the xbrain icon to send it to your brain.",
      priority: 1,
    });
  }
}
