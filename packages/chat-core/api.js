/**
 * Transport-agnostic memory-api client (Phase 27, D-27-04).
 *
 * Replaces the per-surface `fetchJson` helper: ONE place builds the
 * Authorization header, ONE place turns a non-2xx into an Error, and every
 * endpoint the chat uses has a named method.
 *
 * The base origin is INJECTED, never hardcoded here (T-27-01-03) — the
 * extension passes its own constant, the PWA passes its own, and a shared file
 * can therefore never repoint one surface at the other's backend.
 *
 * Used by:
 *   - chrome-extension/popup.js (via chrome-extension/chat_core/)
 *   - app-site/app/ — the PWA (via app-site/app/chat_core/)
 */

/**
 * @param {{baseUrl: string, getToken: () => Promise<string|null>}} config
 *   baseUrl  — origin of memory-api, no trailing slash required
 *   getToken — async, returns the caller's bearer token or null when signed out
 */
export function createApi({ baseUrl, getToken } = {}) {
  if (typeof baseUrl !== "string" || baseUrl.trim() === "") {
    throw new TypeError("createApi requires a non-empty baseUrl");
  }
  if (typeof getToken !== "function") {
    throw new TypeError("createApi requires an async getToken()");
  }
  const base = baseUrl.replace(/\/+$/, "");

  /**
   * Perform the request and hand back the RAW Response — no throw, no parse.
   *
   * For the call sites that branch on a specific status code (202 accepted,
   * 409 conflict, 429 rate-limited, ...) where an exception would destroy the
   * information they need. They still get the Authorization header from one
   * place, which is the point of the client.
   *
   * @param {string} path absolute API path, e.g. "/v1/me"
   * @param {{method?: string, body?: Object|null, headers?: Object}} [opts]
   * @returns {Promise<Response>}
   */
  async function rawFetch(path, opts = {}) {
    const { method = "GET", body = null, headers = {} } = opts;
    const finalHeaders = { ...headers };
    const token = await getToken();
    // A null token is not an error here: the server answers 401 and the caller
    // decides. Sending "Bearer null" would be worse — it looks like a credential.
    if (token) finalHeaders.Authorization = `Bearer ${token}`;
    const init = { method, headers: finalHeaders };
    if (body !== null && body !== undefined) {
      finalHeaders["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    return fetch(`${base}${path}`, init);
  }

  /**
   * Perform the request, throw on non-2xx, return the parsed JSON body.
   *
   * The thrown message is exactly the shape the surfaces already produced
   * (`HTTP 403: <first 200 chars>`), so existing catch/alert paths keep working
   * unchanged. A 204 or an empty body resolves to null instead of blowing up on
   * a JSON parse of "".
   *
   * @param {string} path
   * @param {{method?: string, body?: Object|null, headers?: Object}} [opts]
   * @returns {Promise<any|null>}
   */
  async function request(path, opts = {}) {
    const r = await rawFetch(path, opts);
    if (!r.ok) {
      const text = await r.text().catch(() => "");
      throw new Error(`HTTP ${r.status}: ${text.slice(0, 200)}`);
    }
    if (r.status === 204) return null;
    const text = await r.text();
    if (!text) return null;
    return JSON.parse(text);
  }

  return {
    rawFetch,
    request,

    // ---- Identity ----
    me: () => request("/v1/me"),
    myTeams: () => request("/v1/teams/my-teams"),

    // ---- Realtime ----
    // Returns {token, ws_url} — the surface uses the ws_url it is GIVEN and
    // never hardcodes a realtime origin (D-27-03).
    centrifugoToken: () => request("/v1/me/centrifugo-token", { method: "POST" }),

    // ---- Team chat ----
    listMessages: (teamId, { before = null, limit = 50 } = {}) =>
      request(
        before
          ? `/v1/teams/${teamId}/messages?before=${encodeURIComponent(before)}&limit=${limit}`
          : `/v1/teams/${teamId}/messages?limit=${limit}`,
      ),
    postMessage: (teamId, body) =>
      request(`/v1/teams/${teamId}/messages`, { method: "POST", body }),
    agentAliases: (teamId) => request(`/v1/teams/${teamId}/agent-aliases`),
    markRead: (teamId) =>
      request(`/v1/teams/${teamId}/mark-read`, { method: "POST" }),
    unreadSummary: (teamId) => request(`/v1/teams/${teamId}/unread-summary`),
  };
}
