/**
 * Signing in to the INSTALLED app, where the Google popup is a dead end.
 *
 * THE BUG THIS FILE GUARDS. On an iPhone with the PWA on the home screen,
 * signing in took about twenty-three attempts: "Verify it's you", then
 * "Something went wrong", then a bare HTTP 400 from accounts.google.com. Google
 * Identity Services defaults to `ux_mode: "popup"`, and a popup opened by a
 * standalone web app on iOS is a DETACHED context with its own storage — Google
 * finds no session in it, asks to verify the device every single time, and that
 * verification is what fails.
 *
 * The remedy is a top-level navigation the app makes itself, and it has three
 * properties that are easy to lose and expensive to lose:
 *
 *   1. IT IS ASKED FOR, NOT ASSUMED. The redirect flow needs a callback
 *      registered by hand as an Authorized redirect URI on the Google client.
 *      Until the server says it is configured, this app keeps the popup — a
 *      sign-in that works on the twenty-third try beats one that fails on every
 *      try with redirect_uri_mismatch.
 *   2. IT ONLY REPLACES THE POPUP WHERE THE POPUP IS BROKEN. In a browser tab
 *      the popup shares the browser's cookie jar and works; taking a whole page
 *      load there would be a regression for everyone on a desktop.
 *   3. THE CREDENTIAL COMES BACK ON A FRAGMENT AND IS TAKEN STRAIGHT OFF IT.
 *      A fragment is never sent to a server, but it does stay in the session
 *      history until something removes it.
 *
 * Behavioural where it can be — the module is imported and driven — and static
 * only where the property is about ORDER inside app.js's boot, which cannot be
 * observed without a DOM. English-only. SKIP = FAIL: nothing here is conditional.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, "..", "..");
const APP_DIR = join(REPO_ROOT, "app-site", "app");

let passed = 0;
let failed = 0;
function test(name, body) {
  try {
    const out = body();
    if (out && typeof out.then === "function") {
      throw new Error("async test bodies must be awaited by the caller");
    }
    console.log(`  PASS: ${name}`);
    passed++;
  } catch (e) {
    console.error(`  FAIL: ${name}`);
    console.error(`    ${e.stack || e.message}`);
    failed++;
  }
}

async function atest(name, body) {
  try {
    await body();
    console.log(`  PASS: ${name}`);
    passed++;
  } catch (e) {
    console.error(`  FAIL: ${name}`);
    console.error(`    ${e.stack || e.message}`);
    failed++;
  }
}

const readApp = (rel) => readFileSync(join(APP_DIR, rel), "utf8");

// auth.js reaches for `window` through platform_web.js. A bare object is enough:
// every test below injects the view it wants to be measured.
globalThis.window = globalThis.window || { localStorage: null };

const auth = await import(pathToFileURL(join(APP_DIR, "auth.js")).href);

// ---- fakes ---------------------------------------------------------------

/** The smallest element the sign-in card actually uses. No jsdom required. */
function fakeEl(tag = "div") {
  const el = {
    tag,
    children: [],
    textContent: "",
    className: "",
    id: "",
    type: "",
    listeners: {},
    appendChild(child) {
      this.children.push(child);
      return child;
    },
    addEventListener(event, fn) {
      (this.listeners[event] = this.listeners[event] || []).push(fn);
    },
    click() {
      for (const fn of this.listeners.click || []) fn();
    },
  };
  el.ownerDocument = { createElement: (t) => fakeEl(t) };
  return el;
}

/** A window whose location and history record what was done to them. */
function fakeView({ hash = "", standalone, displayMode = false, matchMediaThrows = false } = {}) {
  const view = {
    navigator: standalone === undefined ? {} : { standalone },
    location: {
      hash,
      pathname: "/app/",
      search: "",
      assigned: null,
      assign(url) {
        this.assigned = url;
      },
    },
    history: {
      replacedWith: undefined,
      replaceState(_state, _title, url) {
        this.replacedWith = url;
      },
    },
    matchMedia(query) {
      if (matchMediaThrows) throw new Error("no matchMedia here");
      return { matches: displayMode && query.includes("standalone") };
    },
  };
  return view;
}

/** A fetch that answers one JSON body and records the URL it was asked for. */
function fakeFetch(body, { ok = true, throws = false } = {}) {
  const calls = [];
  const fn = async (url) => {
    calls.push(url);
    if (throws) throw new Error("offline");
    return { ok, json: async () => body };
  };
  fn.calls = calls;
  return fn;
}

// ---- 1. which surface is this? -------------------------------------------

test("navigator.standalone is read — it is the only signal older iOS has", () => {
  assert.equal(auth.isStandaloneDisplay(fakeView({ standalone: true })), true);
  assert.equal(auth.isStandaloneDisplay(fakeView({ standalone: false })), false);
});

test("display-mode: standalone is read too — it is the standard one", () => {
  assert.equal(auth.isStandaloneDisplay(fakeView({ displayMode: true })), true);
});

test("a browser tab is not standalone, and that is what keeps the popup", () => {
  assert.equal(auth.isStandaloneDisplay(fakeView()), false);
});

test("a browser that throws on the query is treated as a tab, not as installed", () => {
  // The popup is what shipped and what works in a tab. Guessing "installed" for
  // a browser we cannot classify would send it down a flow it may not complete.
  assert.equal(auth.isStandaloneDisplay(fakeView({ matchMediaThrows: true })), false);
});

// ---- 2. the server decides whether the flow exists at all -----------------

await atest("the flow is used only when the server says it is configured", async () => {
  const yes = fakeFetch({ enabled: true });
  assert.equal(await auth.googleWebSignInEnabled(yes), true);
  assert.equal(await auth.googleWebSignInEnabled(fakeFetch({ enabled: false })), false);
  assert.match(
    yes.calls[0],
    /^https:\/\/[^/]+\/v1\/auth\/google\/web-config$/,
    `the question must go to the API's web-config endpoint, went to ${yes.calls[0]}`,
  );
});

await atest("an unreachable or unhappy API keeps the popup rather than the redirect", async () => {
  assert.equal(await auth.googleWebSignInEnabled(fakeFetch({}, { throws: true })), false);
  assert.equal(await auth.googleWebSignInEnabled(fakeFetch({ enabled: true }, { ok: false })), false);
  assert.equal(await auth.googleWebSignInEnabled(fakeFetch(null)), false);
});

test("the start URL is on the API, not on this site", () => {
  assert.equal(
    auth.googleWebStartUrl(),
    `${auth.MEMORY_API_BASE}/v1/auth/google/start`,
    "a static host cannot answer this — the flow starts on the API",
  );
});

// ---- 3. coming back ------------------------------------------------------

test("the credential is read off the fragment AND taken off the URL", () => {
  const credential = "header.payload.signature";
  const view = fakeView({ hash: `#google_credential=${credential}` });
  const back = auth.takeGoogleRedirectResult(view);

  assert.equal(back.credential, credential);
  assert.equal(back.error, null);
  assert.equal(
    view.history.replacedWith,
    "/app/",
    "a credential left in location.hash stays in the session history",
  );
});

test("a percent-encoded credential comes back decoded", () => {
  // The callback encodes it, because a raw '+' in a JWT signature would arrive
  // as a space and the token would no longer verify.
  const raw = "a.b.sig+with/reserved=chars";
  const view = fakeView({ hash: `#google_credential=${encodeURIComponent(raw)}` });
  assert.equal(auth.takeGoogleRedirectResult(view).credential, raw);
});

test("an error comes back as an error, and is also stripped", () => {
  const view = fakeView({ hash: "#google_error=denied" });
  const back = auth.takeGoogleRedirectResult(view);
  assert.equal(back.error, "denied");
  assert.equal(back.credential, null);
  assert.equal(view.history.replacedWith, "/app/");
});

test("the query string survives the strip — only the fragment goes", () => {
  const view = fakeView({ hash: "#google_credential=x" });
  view.location.search = "?team=alpha";
  auth.takeGoogleRedirectResult(view);
  assert.equal(view.history.replacedWith, "/app/?team=alpha");
});

test("an ordinary load is left completely alone", () => {
  // Which is almost every load, including every load by somebody who is already
  // signed in. Returning null here is what keeps them out of this path.
  for (const hash of ["", "#", "#c=an-invite-code", "#something=else"]) {
    const view = fakeView({ hash });
    assert.equal(auth.takeGoogleRedirectResult(view), null, `hash ${JSON.stringify(hash)}`);
    assert.equal(
      view.history.replacedWith,
      undefined,
      `a fragment this flow did not write must not be rewritten (hash ${JSON.stringify(hash)})`,
    );
  }
});

test("a cancelled sign-in reads as cancelled, and anything else as a failure", () => {
  assert.match(auth.googleRedirectErrorMessage("denied"), /cancelled/i);
  assert.doesNotMatch(auth.googleRedirectErrorMessage("token_exchange_failed"), /cancelled/i);
  for (const slug of ["state_mismatch", "no_id_token", "token_endpoint_unreachable"]) {
    assert.doesNotMatch(
      auth.googleRedirectErrorMessage(slug),
      new RegExp(slug),
      "a slug is a log line, not a sentence somebody reads",
    );
  }
});

// ---- 4. which button the card actually draws -----------------------------

/**
 * Mount the sign-in card against fakes and report what landed in the slot.
 *
 * A FRESH module instance every time, via a cache-busting import. auth.js keeps
 * a module-level "already mounted" guard so a re-sign-in cannot stack two Google
 * buttons in one page — correct in a browser, and in one test file it would make
 * every mount after the first a no-op, so the second assertion would pass by
 * doing nothing at all.
 */
let mountSeq = 0;
async function mountAgainst({ view, enabled }) {
  const fresh = await import(
    `${pathToFileURL(join(APP_DIR, "auth.js")).href}?mount=${(mountSeq += 1)}`
  );
  const slotEl = fakeEl();
  const hintEl = fakeEl();
  const gisCalls = [];
  // Present so the popup path can complete synchronously instead of retrying
  // for five seconds against a `google` that will never arrive in node.
  globalThis.window.google = {
    accounts: {
      id: {
        initialize: (cfg) => gisCalls.push(["initialize", cfg]),
        renderButton: (el) => gisCalls.push(["renderButton", el]),
      },
    },
  };
  fresh.mountSignIn({
    slotEl,
    hintEl,
    formEl: fakeEl(),
    emailEl: fakeEl(),
    passwordEl: fakeEl(),
    submitEl: fakeEl(),
    bannerEl: fakeEl(),
    onSignedIn: () => {},
    view,
    fetchImpl: fakeFetch({ enabled }),
  });
  // Let the web-config question and the surface choice settle.
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  return { slotEl, hintEl, gisCalls };
}

await atest("installed + configured: the app draws its OWN button, and no popup", async () => {
  const { slotEl, gisCalls } = await mountAgainst({
    view: fakeView({ standalone: true }),
    enabled: true,
  });
  assert.equal(gisCalls.length, 0, "GIS must not be initialized on the surface its popup breaks");
  assert.equal(slotEl.children.length, 1, "the slot must hold exactly one control");
  const button = slotEl.children[0];
  assert.equal(button.tag, "button");
  assert.equal(button.type, "button", "a bare <button> in a form would submit it");
  assert.match(button.textContent, /google/i);
});

await atest("that button moves the browser only on a CLICK", async () => {
  const view = fakeView({ standalone: true });
  const { slotEl } = await mountAgainst({ view, enabled: true });

  assert.equal(
    view.location.assigned,
    null,
    "navigating on mount would send somebody who is only reading the page to an account chooser",
  );
  slotEl.children[0].click();
  assert.equal(view.location.assigned, auth.googleWebStartUrl());
});

await atest("installed but NOT configured: the popup stays, because it sometimes works", async () => {
  const { gisCalls } = await mountAgainst({
    view: fakeView({ standalone: true }),
    enabled: false,
  });
  assert.ok(
    gisCalls.some(([name]) => name === "renderButton"),
    "with the redirect URI unregistered, the redirect flow fails EVERY time — the popup does not",
  );
});

await atest("a browser tab keeps the popup, which is not broken there", async () => {
  const { gisCalls } = await mountAgainst({ view: fakeView(), enabled: true });
  assert.ok(
    gisCalls.some(([name]) => name === "renderButton"),
    "a full page load in a tab would be a regression for every desktop user",
  );
});

// ---- 5. the one thing only order can get right ---------------------------

test("app.js takes the credential off the URL BEFORE it reads the stored token", () => {
  // Both branches end the boot. If the stored-token branch ran first, a person
  // who is already signed in would keep the credential in their address for the
  // rest of the session, because nothing would ever strip it.
  const appJs = readApp("app.js");
  const strip = appJs.indexOf("takeGoogleRedirectResult(");
  const stored = appJs.indexOf("if (await getToken())");
  assert.ok(strip > 0, "app.js must consume the redirect result");
  assert.ok(stored > 0, "app.js must still read the stored token");
  assert.ok(
    strip < stored,
    "the fragment must be consumed and stripped before an early return can skip it",
  );
});

test("nothing on this surface puts a credential in a query string", () => {
  for (const file of ["auth.js", "app.js"]) {
    const src = readApp(file);
    assert.doesNotMatch(
      src,
      /[?&]google_credential=/,
      `${file} must never build a query parameter out of the credential — that is what a fragment avoids`,
    );
  }
});

test("english-only: no accented Latin chars in the sign-in path", () => {
  for (const file of ["auth.js", "app.js"]) {
    const bad = (readApp(file).match(/[À-ɏ]/g) || []).join("");
    assert.equal(bad, "", `${file} carries non-English characters: ${bad}`);
  }
});

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
