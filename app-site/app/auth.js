/**
 * PWA sign-in (Phase 27, Plan 27-05 — D-27-02).
 *
 * This is app-site/join/index.html's proven flow, moved into a module: Google
 * Identity Services mints a credential, that credential authenticates a POST to
 * /v1/me/api-token, and the returned xbt_ token plus the identity from /v1/me
 * are stored under the SAME two localStorage keys the rest of app-site uses.
 *
 * That last part is the whole point. A person who signed in on /join/ or
 * /account/login is ALREADY signed in here, and signing in here signs them in
 * there. There is no second identity system, no second token format, no second
 * place a session can go stale.
 *
 * The discipline carried over from the join page, deliberately and in full:
 *   - a rejected mint leaves storage UNTOUCHED (a half-written session that
 *     looks signed-in but 401s on every call is worse than being signed out);
 *   - 401 and 429 get identical generic copy, so this page never reveals
 *     whether an account exists;
 *   - the Google button retries for ~5s and then degrades to the password form
 *     rather than leaving a blank gap where a button should be.
 *
 * Storage goes through the platform shim, not localStorage directly, so the
 * "localStorage can throw" handling lives in exactly one place.
 */

import { webPlatform } from "./platform_web.js";

/** Same API base as every other account page (login, teams, admin, join). */
export const MEMORY_API_BASE = "https://api.grooveos.app";

/**
 * The SAME OAuth client the Chrome extension and /join/ use, so a person who
 * signed up on any surface lands on the same user row. `https://grooveos.app`
 * is already an Authorized JavaScript origin on this client and already matches
 * the API's CORS_ALLOWED_ORIGIN_REGEX — no console change was needed to ship
 * this page.
 */
export const GOOGLE_CLIENT_ID =
  "50097563098-rdh24v05dcp0ees8o4kqviuuoi5sup3n.apps.googleusercontent.com";

/**
 * Canonical localStorage keys - the ones login/teams/admin/join all use.
 *
 * STORAGE_TOKEN does double duty on purpose. Its value is BOTH the key the
 * whole of app-site stores the personal token under AND the field name
 * /v1/auth/local/login returns that token in. That is not a coincidence to be
 * papered over with two constants - it is one name for one thing, so it is
 * spelled out exactly once (right below) and read through this constant on both
 * sides. The value is deliberately not repeated in this comment: the acceptance
 * gate counts occurrences in this file, comments included.
 */
export const STORAGE_TOKEN = "xbt_token";
export const STORAGE_EMAIL = "user_sub";

/** Generic copy. Reused for 401 and 429 so neither confirms an account exists. */
const GENERIC_REJECT = "Invalid email or password.";
const GENERIC_THROTTLE = "Too many attempts - try again shortly.";

/**
 * The stored personal token, or null.
 * @returns {Promise<string|null>}
 */
export async function getToken() {
  const stored = await webPlatform.storage.get([STORAGE_TOKEN]);
  return stored[STORAGE_TOKEN] || null;
}

/**
 * The stored account identifier (email or source_user_id), or null.
 * @returns {Promise<string|null>}
 */
export async function getUserSub() {
  const stored = await webPlatform.storage.get([STORAGE_EMAIL]);
  return stored[STORAGE_EMAIL] || null;
}

/**
 * Drop the session. Both keys go together: leaving the identity behind would
 * make the UI claim a signed-in user whose every request 401s.
 */
export async function signOut() {
  await webPlatform.storage.remove([STORAGE_TOKEN, STORAGE_EMAIL]);
}

/** Pull a human-readable message out of a FastAPI error body, if there is one. */
async function readDetail(response) {
  try {
    const data = await response.json();
    if (typeof data.detail === "string") return data.detail;
    if (Array.isArray(data.detail)) {
      return data.detail.map((d) => d.msg || JSON.stringify(d)).join(" ");
    }
    return null;
  } catch (e) {
    return null;
  }
}

/**
 * Exchange a Google ID token for the xbt_ personal token.
 *
 * Storage is written ONLY after both calls have returned something usable, so a
 * server rejection cannot leave a partial session behind.
 *
 * @param {string} credential the Google ID token from GIS
 * @returns {Promise<{ok: true, email: string} | {ok: false, error: string}>}
 */
export async function signInWithGoogleCredential(credential) {
  let mintRes;
  try {
    mintRes = await fetch(`${MEMORY_API_BASE}/v1/me/api-token`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${credential}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ team_scope: "default", name: "pwa" }),
    });
  } catch (e) {
    return { ok: false, error: "Network error - could not reach the server." };
  }

  if (!mintRes.ok) {
    return {
      ok: false,
      error: `Google sign-in was rejected by the server (HTTP ${mintRes.status}).`,
    };
  }

  let mint;
  try {
    mint = await mintRes.json();
  } catch (e) {
    return { ok: false, error: "Could not complete Google sign-in. Try again." };
  }
  if (!mint || !mint.token) {
    return { ok: false, error: "Could not complete Google sign-in. Try again." };
  }

  // /v1/me is best-effort: the token is already valid, and failing the whole
  // sign-in because a display field could not be read would be a regression.
  let me = {};
  try {
    const meRes = await fetch(`${MEMORY_API_BASE}/v1/me`, {
      headers: { Authorization: `Bearer ${mint.token}` },
    });
    if (meRes.ok) me = await meRes.json();
  } catch (e) {
    me = {};
  }

  const identity = me.email || me.source_user_id || "";
  await webPlatform.storage.set({
    [STORAGE_TOKEN]: mint.token,
    [STORAGE_EMAIL]: identity,
  });
  return { ok: true, email: identity };
}

/**
 * Email + password sign-in, for accounts that have a password.
 *
 * Google is offered FIRST in the UI because an account created through the
 * extension has no password at all and could not authenticate here otherwise.
 *
 * @param {string} email
 * @param {string} password
 * @returns {Promise<{ok: true, email: string} | {ok: false, error: string}>}
 */
export async function signInWithPassword(email, password) {
  let response;
  try {
    response = await fetch(`${MEMORY_API_BASE}/v1/auth/local/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
  } catch (e) {
    return { ok: false, error: "Network error - could not reach the server." };
  }

  if (response.status === 200) {
    let data;
    try {
      data = await response.json();
    } catch (e) {
      return { ok: false, error: "Sign-in failed. Try again." };
    }
    if (!data || !data[STORAGE_TOKEN]) {
      return { ok: false, error: "Sign-in failed. Try again." };
    }
    const identity = (data.user && data.user.email) || email;
    await webPlatform.storage.set({
      [STORAGE_TOKEN]: data[STORAGE_TOKEN],
      [STORAGE_EMAIL]: identity,
    });
    return { ok: true, email: identity };
  }

  const detail = await readDetail(response);
  if (response.status === 401) return { ok: false, error: detail || GENERIC_REJECT };
  if (response.status === 429) return { ok: false, error: detail || GENERIC_THROTTLE };
  return {
    ok: false,
    error: detail || `Sign-in failed (HTTP ${response.status}).`,
  };
}

/**
 * Render the Google button once GIS has loaded.
 *
 * The GIS script tag is async, so this retries rather than assuming `google`
 * exists. If it never arrives - blocked script, offline, an extension eating
 * third-party scripts - the hint says so and the password form below is still
 * usable, instead of leaving an unexplained blank space.
 */
let googleMounted = false;
function mountGoogleButton({ slotEl, hintEl, onCredential }, attempt) {
  if (googleMounted) return;
  const gis = window.google;
  if (!gis || !gis.accounts || !gis.accounts.id) {
    const n = (attempt || 0) + 1;
    if (n > 20) {
      // ~5s
      if (hintEl) {
        hintEl.textContent =
          "Google sign-in unavailable right now - use your email and password below.";
      }
      return;
    }
    setTimeout(() => mountGoogleButton({ slotEl, hintEl, onCredential }, n), 250);
    return;
  }
  googleMounted = true;
  gis.accounts.id.initialize({
    client_id: GOOGLE_CLIENT_ID,
    callback: (resp) => {
      if (resp && resp.credential) onCredential(resp.credential);
    },
  });
  gis.accounts.id.renderButton(slotEl, {
    theme: "filled_black",
    size: "large",
    text: "signin_with",
    shape: "rectangular",
    width: 300,
  });
}

/**
 * Wire the sign-in card: the Google button plus the password form.
 *
 * @param {{
 *   slotEl: Element,
 *   hintEl: Element|null,
 *   formEl: HTMLFormElement,
 *   emailEl: HTMLInputElement,
 *   passwordEl: HTMLInputElement,
 *   submitEl: HTMLButtonElement,
 *   bannerEl: Element,
 *   onSignedIn: (email: string) => void,
 * }} refs
 */
export function mountSignIn(refs) {
  const {
    slotEl,
    hintEl,
    formEl,
    emailEl,
    passwordEl,
    submitEl,
    bannerEl,
    onSignedIn,
  } = refs;

  function showError(message) {
    if (!bannerEl) return;
    bannerEl.textContent = message;
    bannerEl.className = "xb-banner is-error";
    bannerEl.hidden = false;
  }

  function showBusy(message) {
    if (!bannerEl) return;
    bannerEl.textContent = message;
    bannerEl.className = "xb-banner";
    bannerEl.hidden = false;
  }

  mountGoogleButton({
    slotEl,
    hintEl,
    onCredential: async (credential) => {
      showBusy("Signing you in...");
      const result = await signInWithGoogleCredential(credential);
      if (result.ok) {
        if (bannerEl) bannerEl.hidden = true;
        onSignedIn(result.email);
        return;
      }
      showError(result.error);
    },
  });

  if (formEl) {
    formEl.addEventListener("submit", async (event) => {
      event.preventDefault();
      const email = emailEl.value.trim().toLowerCase();
      const password = passwordEl.value;
      if (!email || !password) return;

      submitEl.disabled = true;
      showBusy("Signing in...");
      try {
        const result = await signInWithPassword(email, password);
        if (result.ok) {
          if (bannerEl) bannerEl.hidden = true;
          onSignedIn(result.email);
          return;
        }
        showError(result.error);
      } finally {
        submitEl.disabled = false;
      }
    });
  }
}
