/**
 * xbrain Web Clipper + Session Bridge — Background Service Worker (Manifest V3)
 *
 * Responsabilités (Phase 4/8 — Web Clipper) :
 *   - Obtenir un ID token Google via launchWebAuthFlow (Solution A — RESEARCH.md Q6)
 *   - Stocker le token en cache dans chrome.storage.session (TTL 3600s)
 *   - Envoyer le payload à memory-api (https://api.grooveos.app/v1/memory/upsert)
 *
 * Responsabilités (Phase 9 — Session Bridge) :
 *   - Maintenir une WebSocket persistante vers wss://bridge.grooveos.app/ws/{user_sub}
 *   - Envoyer une frame `register` à l'ouverture (provider/email_logged/org_id)
 *   - Dispatcher chaque `chat_request` du bridge vers handleClaude (claude_ai_client.js)
 *   - Reconnexion exponentielle avec jitter, keepalive ping 20s, chrome.alarms watchdog
 *
 * Messages écoutés via chrome.runtime.onMessage :
 *   { type: "GET_ID_TOKEN" }           → retourne { idToken: "..." } ou { error: "..." }
 *   { type: "SEND_TO_BRAIN", payload } → retourne { ok: true } ou { error: "..." }
 *   { kind: "ws_status_query" }        → retourne { readyState: ws ? ws.readyState : -1 }
 */

import { handleClaude, getOrgId } from "./claude_ai_client.js";
import {
  computeBackoffMs,
  PING_INTERVAL_MS,
  WATCHDOG_PERIOD_MIN,
  MAX_ATTEMPT,
} from "./ws_keepalive.js";

const MEMORY_API_URL = "https://api.grooveos.app/v1/memory/upsert";
// Remplacer __GOOGLE_CLIENT_ID__ par le même client_id que LibreChat Google OAuth
// Format attendu : "XXXXXXXXXX.apps.googleusercontent.com"
const CLIENT_ID = "50097563098-rdh24v05dcp0ees8o4kqviuuoi5sup3n.apps.googleusercontent.com";
const TOKEN_CACHE_KEY = "xbrain_id_token";
const TOKEN_EXPIRY_KEY = "xbrain_id_token_expiry";
const TOKEN_TTL_MS = 3600 * 1000; // 1 heure en millisecondes

/**
 * Obtenir un ID token Google via launchWebAuthFlow avec response_type=id_token.
 * Utilise chrome.storage.session pour le cache (TTL 3600s).
 * L'ID token retourné est un JWT signé par Google, compatible avec
 * verify_google_id_token dans apps/memory-api/app/auth.py.
 *
 * Pré-requis Google Cloud Console :
 *   Ajouter https://<chrome.runtime.id>.chromiumapp.org/ dans les
 *   Authorized redirect URIs du client OAuth.
 */
async function getGoogleIdToken() {
  // 1. Vérifier le cache
  const stored = await chrome.storage.session.get([TOKEN_CACHE_KEY, TOKEN_EXPIRY_KEY]);
  const cachedToken = stored[TOKEN_CACHE_KEY];
  const cachedExpiry = stored[TOKEN_EXPIRY_KEY];

  if (cachedToken && cachedExpiry && Date.now() < cachedExpiry) {
    return cachedToken;
  }

  // 2. Obtenir un nouveau token via launchWebAuthFlow
  const redirectUri = `https://${chrome.runtime.id}.chromiumapp.org/`;
  const nonce = Math.random().toString(36).substring(2) + Math.random().toString(36).substring(2);

  const authUrl = new URL("https://accounts.google.com/o/oauth2/v2/auth");
  authUrl.searchParams.set("client_id", CLIENT_ID);
  authUrl.searchParams.set("response_type", "id_token");
  authUrl.searchParams.set("redirect_uri", redirectUri);
  authUrl.searchParams.set("scope", "openid email profile");
  authUrl.searchParams.set("nonce", nonce);
  // prompt=select_account force la sélection de compte si plusieurs comptes connectés
  authUrl.searchParams.set("prompt", "select_account");

  return new Promise((resolve, reject) => {
    chrome.identity.launchWebAuthFlow(
      { url: authUrl.toString(), interactive: true },
      async (redirectUrl) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
          return;
        }
        if (!redirectUrl) {
          reject(new Error("Auth flow cancelled or no redirect URL returned"));
          return;
        }

        // Extraire l'ID token du fragment (#id_token=...)
        const hash = new URL(redirectUrl).hash.substring(1); // supprimer le '#'
        const params = new URLSearchParams(hash);
        const idToken = params.get("id_token");

        if (!idToken) {
          reject(new Error("No id_token found in redirect URL fragment"));
          return;
        }

        // 3. Mettre en cache dans chrome.storage.session
        const expiry = Date.now() + TOKEN_TTL_MS;
        await chrome.storage.session.set({
          [TOKEN_CACHE_KEY]: idToken,
          [TOKEN_EXPIRY_KEY]: expiry,
        });

        resolve(idToken);
      }
    );
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

/**
 * Écouter les messages depuis le popup et les autres composants de l'extension.
 */
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  // Threat T-09-03-02: refuser tout message ne provenant pas de l'extension elle-même.
  if (sender && sender.id && sender.id !== chrome.runtime.id) {
    return false;
  }

  if (message.type === "GET_ID_TOKEN") {
    getGoogleIdToken()
      .then((idToken) => sendResponse({ idToken }))
      .catch((err) => sendResponse({ error: err.message }));
    return true; // indique que sendResponse sera appelé de manière asynchrone
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
    return true; // asynchrone
  }

  // Phase 9 — consommé par popup.js (plan 09-05) pour afficher 🟢/🔴.
  if (message && message.kind === "ws_status_query") {
    sendResponse({
      readyState: ws ? ws.readyState : -1,
      last_open_ms: lastOpenAt,
    });
    return false; // réponse synchrone
  }

  // Message inconnu — ne pas bloquer
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
 * Best-effort fetch de l'email loggé sur claude.ai. Renvoie null sur échec
 * (non-bloquant — la frame register part avec email_logged=null si la requête
 * échoue, p.ex. si l'utilisateur n'est pas encore connecté à claude.ai).
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
 * Ouvre (ou réutilise) la WebSocket vers session-bridge.
 * Idempotent : ne ré-ouvre pas si une socket est déjà OPEN/CONNECTING.
 */
async function openBridgeWS() {
  if (
    ws &&
    (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)
  ) {
    return; // idempotent
  }
  const { xbt_token, user_sub } = await chrome.storage.session.get([
    "xbt_token",
    "user_sub",
  ]);
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

    // Threat T-09-03-04: jamais throw — register en best-effort, nulls autorisés.
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
    // 4401/4403 = auth failure — ne pas retry tant que le token n'a pas été refreshé.
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

// chrome.alarms watchdog — ré-ouvre la WS si le SW a été tué (MV3 idle).
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

// Ré-ouvre la WS dès que xbt_token ou user_sub apparaît / change dans le storage.
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "session") return;
  if (
    (changes.xbt_token || changes.user_sub) &&
    (!ws || ws.readyState === WebSocket.CLOSED)
  ) {
    openBridgeWS();
  }
});

// Démarrage navigateur — chrome.runtime.onStartup tire AVANT que le SW soit
// ré-instancié sur idle, mais on déclenche openBridgeWS() au top-level aussi
// pour gérer le cas d'install/upgrade.
chrome.runtime.onStartup.addListener(() => {
  openBridgeWS();
});

// Open on SW boot
openBridgeWS();
