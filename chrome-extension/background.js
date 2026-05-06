/**
 * xbrain Web Clipper — Background Service Worker (Manifest V3)
 *
 * Responsabilités :
 *   - Obtenir un ID token Google via launchWebAuthFlow (Solution A — RESEARCH.md Q6)
 *   - Stocker le token en cache dans chrome.storage.session (TTL 3600s)
 *   - Envoyer le payload à memory-api (https://api.dejavu.cat/v1/memory/upsert)
 *
 * Messages écoutés via chrome.runtime.onMessage :
 *   { type: "GET_ID_TOKEN" }           → retourne { idToken: "..." } ou { error: "..." }
 *   { type: "SEND_TO_BRAIN", payload } → retourne { ok: true } ou { error: "..." }
 */

const MEMORY_API_URL = "https://api.dejavu.cat/v1/memory/upsert";
// Remplacer __GOOGLE_CLIENT_ID__ par le même client_id que LibreChat Google OAuth
// Format attendu : "XXXXXXXXXX.apps.googleusercontent.com"
const CLIENT_ID = "__GOOGLE_CLIENT_ID__";
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
 * Envoyer un payload de mémoire à api.dejavu.cat.
 * Le payload doit contenir les champs du contrat de tagging xbrain.
 */
async function sendToBrain(idToken, payload) {
  const response = await fetch(MEMORY_API_URL, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${idToken}`,
      "X-Team-Scope": payload.team_scope || "acme",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      item: {
        content: payload.content,
        team_scope: payload.team_scope || "acme",
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
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
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

  // Message inconnu — ne pas bloquer
  return false;
});
