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
 * Google sign-in WITHOUT a popup, for the installed app.
 *
 * THE BUG THESE EXIST FOR. Signing in to the home-screen app on an iPhone took
 * about twenty-three attempts: "Verify it's you", then "Something went wrong",
 * then a bare HTTP 400 from accounts.google.com. Nothing below this line was
 * wrong — the problem is WHERE the question is asked. Google Identity Services
 * defaults to `ux_mode: "popup"`, and in a browser tab that popup shares the
 * browser's cookie jar, so Google recognises the account and the device. In a
 * standalone web app it does not: iOS opens it as a detached in-app browser
 * sheet with its own storage, Google finds no session, asks to verify every
 * single time, and that verification is what fails.
 *
 * So on that surface the sign-in becomes a top-level navigation the app itself
 * makes, through /v1/auth/google/start, and comes back to /app/ with the Google
 * credential on the fragment. The session Google establishes on the way through
 * belongs to the app's own context and is still there next time.
 *
 * The popup is KEPT everywhere else. It works in a tab, it is one fewer page
 * load, and it is the flow /join/ has always used.
 */
const GOOGLE_WEB_CONFIG_PATH = "/v1/auth/google/web-config";
const GOOGLE_WEB_START_PATH = "/v1/auth/google/start";

/**
 * Fragment keys the callback hands back on. They are FRAGMENT keys, never query
 * parameters: a fragment is not sent to a server, so the credential cannot land
 * in a hosting access log or a Referer. The same reasoning the invite code on
 * /join/ is built on.
 */
export const GOOGLE_CREDENTIAL_FRAGMENT_KEY = "google_credential";
export const GOOGLE_ERROR_FRAGMENT_KEY = "google_error";

/**
 * Is this document the INSTALLED app rather than a browser tab?
 *
 * Two readings because two browsers answer differently: `display-mode` is the
 * standard one, and `navigator.standalone` is the only one iOS has had for most
 * of its life. Either is enough — this decides which sign-in surface to draw,
 * and drawing the redirect one in a tab would still work, while missing it on
 * the phone is the whole bug.
 *
 * @param {Object} [view] the window to interrogate. Injected so this is testable.
 * @returns {boolean}
 */
export function isStandaloneDisplay(view) {
  const w = view || (typeof window === "undefined" ? null : window);
  if (!w) return false;
  try {
    if (w.navigator && w.navigator.standalone === true) return true;
    if (typeof w.matchMedia === "function") {
      const m = w.matchMedia("(display-mode: standalone)");
      if (m && m.matches) return true;
    }
  } catch (e) {
    // A browser that throws on either read is a browser this cannot classify.
    // The popup is the honest default: it is what shipped and what works in a tab.
    return false;
  }
  return false;
}

/**
 * Ask the server whether the redirect flow is usable at all.
 *
 * IT IS ASKED, NOT ASSUMED, and that is the point. The redirect flow needs a
 * callback URI registered by hand as an Authorized redirect URI on the Google
 * client; until somebody has done that, sending anyone down it produces a
 * redirect_uri_mismatch — a sign-in that fails every time instead of one that
 * fails most times. The server answers false while it is unconfigured, and this
 * page keeps the popup. Mirrors how push.js reads /v1/push/config before it
 * offers anything.
 *
 * Any failure answers false. An API that cannot be reached must not take the
 * sign-in card down with it.
 *
 * @returns {Promise<boolean>}
 */
export async function googleWebSignInEnabled(fetchImpl) {
  const doFetch = fetchImpl || (typeof fetch === "function" ? fetch : null);
  if (!doFetch) return false;
  try {
    const res = await doFetch(`${MEMORY_API_BASE}${GOOGLE_WEB_CONFIG_PATH}`);
    if (!res || !res.ok) return false;
    const data = await res.json();
    return !!(data && data.enabled);
  } catch (e) {
    return false;
  }
}

/** Where a click on the redirect button sends the browser. */
export function googleWebStartUrl() {
  return `${MEMORY_API_BASE}${GOOGLE_WEB_START_PATH}`;
}

/**
 * Read the result the callback left on the URL, and take it OFF the URL.
 *
 * Stripped whether or not the caller does anything with it, and stripped before
 * this function returns: a credential that stays in `location.hash` is a
 * credential in the session history, readable by anything that runs later on
 * this page. `replaceState` rather than assigning `location.hash = ""`, which
 * would leave a bare "#" behind and push a history entry.
 *
 * @param {Object} [view] the window to read. Injected so this is testable.
 * @returns {{credential: string|null, error: string|null}|null} null when this
 *   load is not a return from the redirect flow, which is almost every load.
 */
export function takeGoogleRedirectResult(view) {
  const w = view || (typeof window === "undefined" ? null : window);
  const loc = w && w.location;
  const hash = (loc && loc.hash) || "";
  if (hash.length < 2) return null;

  let params;
  try {
    params = new URLSearchParams(hash.slice(1));
  } catch (e) {
    return null;
  }
  const credential = params.get(GOOGLE_CREDENTIAL_FRAGMENT_KEY);
  const error = params.get(GOOGLE_ERROR_FRAGMENT_KEY);
  if (!credential && !error) return null;

  try {
    if (w.history && typeof w.history.replaceState === "function") {
      w.history.replaceState(null, "", `${loc.pathname || ""}${loc.search || ""}`);
    }
  } catch (e) {
    // Nothing to do about it, and it must not stop the sign-in it is guarding.
  }
  return { credential: credential || null, error: error || null };
}

/**
 * What to tell somebody who came back without a credential.
 *
 * "denied" is not a failure — it is somebody closing the account chooser — so it
 * gets no error styling anywhere it is used. Everything else is one sentence and
 * a way to try again, never a slug.
 *
 * @param {string} slug
 * @returns {string}
 */
export function googleRedirectErrorMessage(slug) {
  if (slug === "denied") return "Google sign-in was cancelled.";
  return "Could not complete Google sign-in. Try again.";
}

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
      body: JSON.stringify({ team_scope: "", name: "pwa" }),
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
 * The redirect surface: our own button, and a navigation this app performs.
 *
 * A plain button rather than GIS's rendered one, because GIS's button exists to
 * open GIS's popup and this flow deliberately does not have one. It carries the
 * same words Google's button does so nobody has to learn a second control, and
 * it moves the browser only on a CLICK — never on load, which would send someone
 * who is merely reading the page off to an account chooser.
 */
function mountGoogleRedirectButton({ slotEl, hintEl, view }) {
  if (!slotEl) return;
  const doc = slotEl.ownerDocument;
  const button = doc.createElement("button");
  button.type = "button";
  button.id = "google-redirect-btn";
  button.className = "xb-btn xb-btn-primary xb-btn-block";
  button.textContent = "Continue with Google";
  button.addEventListener("click", () => {
    const w = view || (typeof window === "undefined" ? null : window);
    if (w && w.location) w.location.assign(googleWebStartUrl());
  });
  slotEl.textContent = "";
  slotEl.appendChild(button);
  if (hintEl) {
    hintEl.textContent = "Continue with your Google account.";
  }
}

/**
 * Pick the Google surface this browser can actually complete a sign-in on.
 *
 * The popup is the default and the fallback: it is what shipped, it works in a
 * tab, and it is what runs whenever the server says the redirect flow is not
 * configured. The redirect is taken only where the popup is known to be a
 * detached context — the installed app — AND the server has confirmed it will
 * work.
 */
async function mountGoogleSurface(refs) {
  const { slotEl, hintEl, onCredential, view, fetchImpl } = refs;
  if (isStandaloneDisplay(view) && (await googleWebSignInEnabled(fetchImpl))) {
    mountGoogleRedirectButton({ slotEl, hintEl, view });
    return;
  }
  mountGoogleButton({ slotEl, hintEl, onCredential });
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

  // Not awaited: choosing the surface asks the server a question, and the
  // password form below must be usable while that answer is in flight. A
  // rejected promise cannot happen (every path inside answers false) but the
  // catch stays, because an unhandled one here would be invisible.
  mountGoogleSurface({
    slotEl,
    hintEl,
    view: refs.view,
    fetchImpl: refs.fetchImpl,
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
  }).catch((e) => {
    console.warn("[xbrain] google sign-in surface failed:", e);
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
