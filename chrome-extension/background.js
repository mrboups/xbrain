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
    try {
      idToken = await launchAuthFlow({
        interactive: false,
        promptMode: "none",
      });
    } catch {
      // Silent path failed — fall through.
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
// Quick task 260512-cmu — "Add to xbrain" context menu (right-click selection)
// ===========================================================================
//
// Right-click any selected text on a web page → "Add to xbrain" → the
// selection lands in the popup/side panel's Content field, ready to send.
//
// Flow:
//   1. User right-clicks selection → onClicked fires with info.selectionText.
//   2. We stash {selectedText, url, title} in chrome.storage.session under
//      key "pending_selection". The popup reads + clears this on open.
//   3. We open the side panel (or popup, per the user setting). For the
//      side panel path we use chrome.sidePanel.open({tabId}) which requires
//      a user gesture — the context menu click counts.
//   4. If the user isn't connected yet, the popup shows the standard
//      Connect button as usual; the pending selection is preserved until
//      they connect.

const CONTEXT_MENU_ID = "xbrain_add_to_brain";

function registerContextMenu() {
  if (!chrome.contextMenus || !chrome.contextMenus.create) return;
  // create() is fail-loud on duplicates → wrap in try.
  try {
    chrome.contextMenus.create(
      {
        id: CONTEXT_MENU_ID,
        title: "Add selection to xbrain",
        contexts: ["selection"],
      },
      () => {
        // Swallow "duplicate id" error after extension reload.
        void chrome.runtime.lastError;
      },
    );
  } catch (e) {
    console.warn(
      "[xbrain] registerContextMenu failed:",
      e && e.message ? e.message : e,
    );
  }
}

chrome.runtime.onInstalled.addListener(() => {
  registerContextMenu();
});

// SW boot path — onInstalled fires only on install/update; recreate the menu
// on every boot in case the SW was killed.
registerContextMenu();

chrome.contextMenus &&
  chrome.contextMenus.onClicked &&
  chrome.contextMenus.onClicked.addListener(async (info, tab) => {
    if (info.menuItemId !== CONTEXT_MENU_ID) return;
    const selectedText = (info.selectionText || "").trim();
    if (!selectedText) return;

    // Stash the selection so the popup reads it on init.
    await chrome.storage.session.set({
      pending_selection: {
        selectedText,
        url: (tab && tab.url) || info.pageUrl || "",
        title: (tab && tab.title) || "",
        captured_at: Date.now(),
      },
    });

    // Open side panel if the user opted in, otherwise the toolbar popup.
    // chrome.action.openPopup() needs a user gesture — the context menu
    // click is a valid gesture per Chrome's policy. sidePanel.open requires
    // either a tabId or windowId (we have tabId from the event).
    try {
      const settings = await loadSettings(chrome.storage.sync);
      if (settings.openInSidePanel && chrome.sidePanel && chrome.sidePanel.open) {
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

    // Last-resort fallback — show a notification telling the user to click
    // the toolbar icon to finish. Happens on Chrome < 127 (no
    // chrome.action.openPopup) or when both APIs fail.
    if (chrome.notifications && chrome.notifications.create) {
      chrome.notifications.create({
        type: "basic",
        iconUrl: "icon128.png",
        title: "xbrain — selection captured",
        message: "Click the xbrain icon to send it to your brain.",
        priority: 1,
      });
    }
  });
