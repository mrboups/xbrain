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
/**
 * Get a Google OAuth2 access token via chrome.identity.getAuthToken.
 *
 * Silent when:
 *   - The user is already signed into Chrome with a Google profile.
 *   - The extension's required scopes (declared in manifest.oauth2.scopes)
 *     have been granted previously.
 * Opens a Chrome-native consent prompt only on the very first call. After
 * that, getAuthToken returns the cached token from Chrome's identity provider
 * with transparent refresh — no need for our own chrome.storage.session cache.
 *
 * Memory-api accepts both Google ID tokens (legacy launchWebAuthFlow path)
 * AND OAuth2 access tokens since quick task 260512-zca (commit 27e0a8d) —
 * verify_google_access_token() calls /oauth2/v3/userinfo to resolve identity.
 *
 * Returns the access token string. Throws on user-cancel or chrome.identity
 * errors.
 */
async function getGoogleIdToken() {
  return new Promise((resolve, reject) => {
    chrome.identity.getAuthToken({ interactive: true }, (result) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
        return;
      }
      // Chrome 116+ returns { token, grantedScopes }; older Chrome returns the
      // token string directly. Normalize both shapes.
      const token =
        result && typeof result === "object" ? result.token : result;
      if (!token) {
        reject(new Error("getAuthToken returned no token"));
        return;
      }
      resolve(token);
    });
  });
}

/**
 * Revoke and clear Chrome's cached Google access token. Called from the
 * disconnect flow so a subsequent connect doesn't silently reuse the stale
 * token (which is harmless on Chrome's side but prevents account switching).
 */
async function clearGoogleAuthToken() {
  return new Promise((resolve) => {
    if (!chrome.identity || !chrome.identity.getAuthToken) {
      resolve();
      return;
    }
    chrome.identity.getAuthToken({ interactive: false }, (result) => {
      if (chrome.runtime.lastError) {
        resolve();
        return;
      }
      const token =
        result && typeof result === "object" ? result.token : result;
      if (!token) {
        resolve();
        return;
      }
      chrome.identity.removeCachedAuthToken({ token }, () => resolve());
    });
  });
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

async function mintAndConnect() {
  return mintAndConnectPure({
    fetch,
    getIdToken: getGoogleIdToken,
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
    getGoogleIdToken()
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
  if (message && message.type === "MINT_AND_CONNECT") {
    mintAndConnect().then(sendResponse);
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
