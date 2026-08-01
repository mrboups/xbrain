/**
 * PWA boot module (Phase 27, Plan 27-05).
 *
 * Runs three things in order and then gets out of the way:
 *   1. stamp the theme before anything paints, so there is no light flash on a
 *      dark device;
 *   2. decide signed-in vs signed-out and show the matching surface;
 *   3. hand the chat over to chat.js, which owns everything from there.
 *
 * The service worker is registered by index.html, not here - see the comment on
 * that block for why it sits inline.
 *
 * NOTHING in this file may ask for notification access or touch a push
 * subscription. D-27-05 puts both behind an explicit click and push.js owns
 * the single call site. A prompt on load is the fastest way to make someone
 * block notifications permanently, and the block is not reversible from here.
 * This file only wires the button and asks push.js what is already true.
 */

import { THEME_STORAGE_KEY, resolveInitialTheme, applyTheme } from "./chat_core/theme.js";
import { createApi } from "./chat_core/api.js";
import { webPlatform } from "./platform_web.js";
import { MEMORY_API_BASE, getToken, getUserSub, signOut, mountSignIn } from "./auth.js";
import { bootChat } from "./chat.js";
import { wirePushButton, refreshPushButton, resyncPush } from "./push.js";

const el = (id) => document.getElementById(id);

/**
 * The client push.js talks to.
 *
 * chat.js builds its own from the same factory rather than exporting one, so
 * this is a second closure over the same two facts (where the API lives, and
 * how to get a token) - not a second client. The shared `createApi` is what
 * makes that safe: there is still exactly one place that builds an
 * Authorization header (D-27-04).
 */
const api = createApi({ baseUrl: MEMORY_API_BASE, getToken });

/** Stored choice wins over the OS preference; both are handled by theme.js. */
async function bootTheme() {
  const stored = await webPlatform.storage.get([THEME_STORAGE_KEY]);
  const prefersDark =
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-color-scheme: dark)").matches;
  const mode = resolveInitialTheme({
    storedTheme: stored[THEME_STORAGE_KEY] ?? null,
    prefersDark,
  });
  applyTheme(document.documentElement, mode);
}

function showSignedOut() {
  el("signin-card").hidden = false;
  el("chat-scroll").hidden = true;
  el("composer").hidden = true;
  el("btn-sign-out").hidden = true;
  el("btn-settings").hidden = true;
  el("settings-panel").hidden = true;
  // Every push endpoint is user-gated, so a signed-out click on this could only
  // produce an unexplained failure. Offering a control that cannot work is
  // worse than not offering it.
  el("btn-enable-push").hidden = true;
  el("push-hint").hidden = true;
}

function showSignedIn(identity) {
  el("signin-card").hidden = true;
  el("chat-scroll").hidden = false;
  el("composer").hidden = false;
  el("btn-sign-out").hidden = false;
  el("btn-settings").hidden = false;
  el("btn-enable-push").hidden = false;
  // The hint stays hidden until refreshPushButton decides there is something
  // worth saying; revealing it here would flash whatever it last held.
  el("push-hint").hidden = true;
  const who = el("account-identity");
  if (who) who.textContent = identity || "";
}

/**
 * The settings panel — open and shut, nothing more.
 *
 * It starts closed on every open. A panel that remembers being open would cover
 * the newest message every single launch, which is the opposite of what a chat
 * wants its first screen to be.
 */
function wireSettingsPanel() {
  const btn = el("btn-settings");
  const panel = el("settings-panel");
  if (!btn || !panel) return;
  btn.addEventListener("click", () => {
    const open = panel.hidden;
    panel.hidden = !open;
    btn.setAttribute("aria-expanded", open ? "true" : "false");
  });
}

/**
 * Light/dark, on the same key and the same helpers the extension uses, so a
 * person moving between the two products sees one product.
 *
 * The stored choice wins; an untouched install follows the OS. Persisting is
 * best-effort: a theme that could not be saved still applies for this session
 * rather than snapping back mid-click.
 */
function wireThemeToggle() {
  const lightBtn = el("btn-theme-light");
  const darkBtn = el("btn-theme-dark");
  if (!lightBtn || !darkBtn) return;

  const paint = (mode) => {
    applyTheme(document.documentElement, mode);
    lightBtn.setAttribute("aria-pressed", String(mode === "light"));
    darkBtn.setAttribute("aria-pressed", String(mode === "dark"));
  };

  const choose = async (mode) => {
    paint(mode);
    try {
      await webPlatform.storage.set({ [THEME_STORAGE_KEY]: mode });
    } catch (e) {
      console.warn("[xbrain] theme persist failed:", e);
    }
  };

  paint(document.documentElement.getAttribute("data-theme") || "light");
  lightBtn.addEventListener("click", () => choose("light"));
  darkBtn.addEventListener("click", () => choose("dark"));
}

/**
 * Show the sign-in card and wire it.
 *
 * Reached from two places: a boot with no token, and a boot whose stored token
 * the API rejected. chat.js calls it through the onSignedOut hook rather than
 * owning any sign-in markup itself.
 */
function showSignInCard() {
  showSignedOut();
  mountSignIn({
    slotEl: el("google-btn"),
    hintEl: el("google-hint"),
    formEl: el("signin-form"),
    emailEl: el("email"),
    passwordEl: el("password"),
    submitEl: el("signin-btn"),
    bannerEl: el("signin-banner"),
    onSignedIn: (identity) => startChat(identity),
  });
}

/**
 * Reveal the chat frame, then hand it to the surface that fills it.
 *
 * The push button is wired only after bootChat resolves, because every one of
 * these calls needs a token: /v1/push/config and the subscribe/unsubscribe
 * endpoints are all user-gated.
 */
async function startChat(identity) {
  showSignedIn(identity);
  await bootChat({ onSignedOut: showSignInCard });

  const pushBtn = el("btn-enable-push");
  const pushHint = el("push-hint");
  const pushState = el("settings-push-state");

  // Attaching a listener asks for nothing; the click does. Wiring twice (a
  // re-sign-in runs this again) is a no-op inside push.js.
  wirePushButton(api, pushBtn, pushHint, pushState);

  // Safe on load precisely because it only READS the permission and the
  // existing subscription. It never prompts and never writes.
  await refreshPushButton(api, pushBtn, pushHint, pushState);

  // Repair on open: a subscription the browser rotated while the app was
  // closed, or a server row that no longer exists. Does nothing at all unless
  // access is already granted and this device already holds a subscription, so
  // it cannot be the thing that raises a prompt. Fire-and-forget - a push
  // repair must never delay or break the chat.
  resyncPush(api)
    .then(() => refreshPushButton(api, pushBtn, pushHint, pushState))
    .catch(() => {});
}

async function boot() {
  await bootTheme();
  wireThemeToggle();
  wireSettingsPanel();

  const signOutBtn = el("btn-sign-out");
  if (signOutBtn) {
    signOutBtn.addEventListener("click", async () => {
      await signOut();
      window.location.reload();
    });
  }

  if (await getToken()) {
    await startChat(await getUserSub());
    return;
  }

  showSignInCard();
}

boot().catch((e) => {
  console.error("[xbrain] boot failed:", e);
  // A thrown boot leaves a blank page with no explanation, which reads as "the
  // site is down". Say something instead.
  const empty = el("chat-empty");
  if (empty) {
    empty.textContent = "Something went wrong loading the app. Reload to try again.";
    empty.hidden = false;
  }
});
