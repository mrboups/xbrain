/**
 * xbrain onboarding helpers — single-click mint + persistent storage.
 *
 * Pure module: every chrome.* dependency is injected via `deps` so this file
 * can be imported and tested in plain Node without polyfilling globalThis.chrome.
 *
 * Used by:
 *   - chrome-extension/background.js (production: deps default to chrome.storage / fetch)
 *   - chrome-extension/tests/run_onboarding_test.mjs (tests: pass in mocks)
 *
 * Storage tier decision (quick task 260512-eo1 PLAN.md):
 *   - Write: chrome.storage.local (persistent across browser restart).
 *   - Read: local first, fall back to session for legacy console-bootstrapped users.
 *
 * Never re-prefix this URL — it is the public ingress for memory-api and is
 * matched by manifest.json host_permissions.
 */

export const MEMORY_API_BASE = "https://api.grooveos.app";

/**
 * Read auth state from chrome.storage, preferring `local` (persistent) over
 * `session` (volatile). Returns nulls when neither tier has both fields set.
 *
 * @param {{local: any, session: any}} storage - injection seam for tests
 * @returns {Promise<{xbt_token: string|null, user_sub: string|null, api_token_id: string|null}>}
 */
export async function readStoredAuth(storage) {
  const local = await storage.local.get([
    "xbt_token",
    "user_sub",
    "api_token_id",
  ]);
  if (local.xbt_token && local.user_sub) {
    return {
      xbt_token: local.xbt_token,
      user_sub: local.user_sub,
      api_token_id: local.api_token_id || null,
    };
  }
  const session = await storage.session.get(["xbt_token", "user_sub"]);
  return {
    xbt_token: session.xbt_token || null,
    user_sub: session.user_sub || null,
    api_token_id: null,
  };
}

/**
 * Drive the full onboarding flow:
 *   1. Google ID token via deps.getIdToken
 *   2. POST /v1/me/api-token → {id, token}
 *   3. GET /v1/me → source_user_id
 *   4. storage.local.set({xbt_token, user_sub, api_token_id})
 *
 * Returns {ok: true, email, source_user_id} on success.
 * Returns {ok: false, error} on any failure, WITHOUT writing partial state.
 *
 * @param {{fetch: typeof fetch, getIdToken: () => Promise<string>, storage: any}} deps
 */
export async function mintAndConnect(deps) {
  const { fetch: fetchFn, getIdToken, storage } = deps;
  try {
    // Idempotence: if a token is already stored and still valid against
    // memory-api, return success without re-minting. This makes a stuck or
    // double-clicked Connect harmless and lets the popup recover state if
    // it was killed mid-flow during the Google consent window.
    const existing = await storage.get([
      "xbt_token",
      "user_sub",
      "api_token_id",
    ]);
    if (existing.xbt_token && existing.user_sub) {
      try {
        const meRes = await fetchFn(`${MEMORY_API_BASE}/v1/me`, {
          headers: { Authorization: `Bearer ${existing.xbt_token}` },
        });
        if (meRes.ok) {
          const me = await meRes.json();
          // Only short-circuit if the stored token still resolves to a user
          // with the same source_user_id (defensive: stale token from another
          // account would otherwise silently win).
          if (me.source_user_id === existing.user_sub) {
            return {
              ok: true,
              email: me.email || null,
              source_user_id: existing.user_sub,
              cached: true,
            };
          }
        }
        // Token rejected (revoked or 401) — fall through to fresh mint below.
      } catch {
        // Network error — try the full mint flow as a fallback.
      }
    }

    const idToken = await getIdToken();

    const mintRes = await fetchFn(`${MEMORY_API_BASE}/v1/me/api-token`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${idToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        team_scope: "",
        name: "chrome-extension",
      }),
    });
    if (!mintRes.ok) {
      const body = await mintRes.text().catch(() => "");
      return {
        ok: false,
        error: `mint failed: ${mintRes.status} ${body.slice(0, 200)}`,
      };
    }
    const mintJson = await mintRes.json();
    const xbt_token = mintJson.token;
    const api_token_id = mintJson.id;
    if (!xbt_token) {
      return { ok: false, error: "mint response missing token field" };
    }

    const meRes = await fetchFn(`${MEMORY_API_BASE}/v1/me`, {
      headers: { Authorization: `Bearer ${idToken}` },
    });
    if (!meRes.ok) {
      return { ok: false, error: `/v1/me failed: ${meRes.status}` };
    }
    const me = await meRes.json();
    const user_sub = me.source_user_id;
    if (!user_sub) {
      return {
        ok: false,
        error: "source_user_id missing in /v1/me response",
      };
    }

    await storage.set({ xbt_token, user_sub, api_token_id });
    return {
      ok: true,
      email: me.email || null,
      source_user_id: user_sub,
    };
  } catch (e) {
    return { ok: false, error: String(e && e.message ? e.message : e) };
  }
}

/**
 * Revoke the current xbt_ token (best-effort) and clear stored auth.
 * Always resolves to {ok: true} — disconnect must never throw.
 *
 * @param {{fetch: typeof fetch, storage: any, sessionStorage: any, closeWs?: () => void}} deps
 */
export async function disconnectAuth(deps) {
  const { fetch: fetchFn, storage, sessionStorage, closeWs } = deps;
  const stored = await storage.get(["xbt_token", "api_token_id"]);
  if (stored.xbt_token && stored.api_token_id) {
    try {
      await fetchFn(
        `${MEMORY_API_BASE}/v1/me/api-token/${encodeURIComponent(stored.api_token_id)}`,
        {
          method: "DELETE",
          headers: { Authorization: `Bearer ${stored.xbt_token}` },
        },
      );
    } catch (e) {
      // Best-effort revoke — surface but don't fail the disconnect.
      console.warn(
        "[xbrain] revoke failed (continuing local cleanup):",
        e && e.message ? e.message : e,
      );
    }
  }

  await storage.remove(["xbt_token", "user_sub", "api_token_id"]);
  // Also clear the legacy session tier so a stale token doesn't resurrect.
  await sessionStorage.remove(["xbt_token", "user_sub"]);

  if (typeof closeWs === "function") {
    try {
      closeWs();
    } catch {
      // ignore
    }
  }

  return { ok: true };
}
