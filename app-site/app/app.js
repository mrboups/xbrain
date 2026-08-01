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
 * subscription. D-27-05 puts both behind an explicit click and plan 27-07 owns
 * the single call site. A prompt on load is the fastest way to make someone
 * block notifications permanently, and the block is not reversible from here.
 */

import { THEME_STORAGE_KEY, resolveInitialTheme, applyTheme } from "./chat_core/theme.js";
import { webPlatform } from "./platform_web.js";
import { getToken, getUserSub, signOut, mountSignIn } from "./auth.js";
import { bootChat } from "./chat.js";

const el = (id) => document.getElementById(id);

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
  el("team-selector").hidden = true;
}

function showSignedIn(identity) {
  el("signin-card").hidden = true;
  el("chat-scroll").hidden = false;
  el("composer").hidden = false;
  el("btn-sign-out").hidden = false;
  el("team-selector").hidden = false;
  const who = el("account-identity");
  if (who) who.textContent = identity || "";
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

/** Reveal the chat frame, then hand it to the surface that fills it. */
async function startChat(identity) {
  showSignedIn(identity);
  await bootChat({ onSignedOut: showSignInCard });
}

async function boot() {
  await bootTheme();

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
