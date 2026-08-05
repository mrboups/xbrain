/**
 * app-site/join/index.html — the invite landing page.
 *
 * It lives in the extension's test directory because that is where
 * run_tests.mjs walks; it reads ../../app-site/join/.
 *
 * THIS PAGE IS THE FRONT DOOR. It is the first screen a new member sees, it is
 * usually opened on a phone, and three of its properties are load-bearing in a
 * way nothing about the rendered page reveals:
 *
 *   1. THE CODE TRAVELS IN THE FRAGMENT, NEVER THE QUERY STRING, and is stripped
 *      from history on arrival. A fragment is not sent to a server, so it cannot
 *      land in hosting logs or in a Referer header; a query string does both.
 *      Moving `#c=` to `?c=` would look identical in a browser and would leak a
 *      bearer secret to every log on the path. So it is asserted here, by
 *      RUNNING the page's own script rather than by grepping for a substring.
 *
 *   2. GOOGLE COMES BEFORE THE PASSWORD FORM. An account created through the
 *      Chrome extension has no password at all, so a page offering only
 *      email/password could not authenticate those people — and they are the
 *      majority of the people who get invited.
 *
 *   3. THE PAGE HAS AN EXIT. It used to have none: after joining, the chat it
 *      had just granted access to was unreachable from it, which on a phone in a
 *      standalone PWA is a wall.
 *
 * And the styling gate: every colour is a token at the top of the <style> block
 * and every rule below resolves through a var(). A hard-coded colour is what
 * stops a theme change from reaching part of the page — the same assertion the
 * account page's API-key section carries, for the same reason.
 *
 * SKIP = FAIL: nothing below is conditional on a file existing.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, "..", "..");
const PAGE = join(REPO_ROOT, "app-site", "join", "index.html");

const html = readFileSync(PAGE, "utf8");

// The code every assertion below hunts for. Percent-encoded in the fragment so
// the decodeURIComponent path is exercised too.
const CODE = "xbi_" + "K9pR4mB1nX6wL8cJ0dY5sG3fA";
const CODE_ENCODED = "xbi_%4B9pR4mB1nX6wL8cJ0dY5sG3fA".replace("%4B", "K");

// ---------------------------------------------------------------------------
// The page's inline script, extracted rather than re-typed. Two inline scripts
// ship: the theme stamp in <head> and the page logic at the end of <body>.
// ---------------------------------------------------------------------------

function inlineScripts() {
  const out = [];
  const re = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g;
  let m;
  while ((m = re.exec(html)) !== null) out.push(m[1]);
  return out;
}

const SCRIPTS = inlineScripts();
const PAGE_SCRIPT = SCRIPTS.find((s) => s.includes("readAndStripCode"));
const THEME_SCRIPT = SCRIPTS.find((s) => s.includes("xbrain_theme_v1"));

// ---------------------------------------------------------------------------
// DOM stub, built FROM THE REAL MARKUP. Every element with an id becomes a node
// carrying that tag's real class/href/hidden/value, in document order — so an
// assertion about an href or about what is hidden is an assertion about the file
// on disk, not about a fixture typed next to it.
// ---------------------------------------------------------------------------

class El {
  constructor(tag, attrs, order) {
    this.tagName = String(tag).toUpperCase();
    this.order = order;
    this.attrs = attrs || {};
    this.id = this.attrs.id || "";
    this.className = this.attrs.class || "";
    this.href = this.attrs.href || "";
    this.type = this.attrs.type || "";
    this.value = this.attrs.value || "";
    this.placeholder = this.attrs.placeholder || "";
    this.hidden = Object.prototype.hasOwnProperty.call(this.attrs, "hidden");
    this.textContent = "";
    this.disabled = false;
    this.focused = false;
    this.listeners = {};
    this.children = [];
  }
  appendChild(n) { this.children.push(n); return n; }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === "href") this.href = String(v); }
  getAttribute(k) {
    return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null;
  }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  dispatch(type, ev) {
    for (const fn of this.listeners[type] || []) fn(ev || { preventDefault() {} });
  }
  focus() { this.focused = true; }
}

/** Parse every id-bearing tag in the page into a node, in document order. */
function buildDom() {
  const byId = new Map();
  const re = /<([a-zA-Z][\w-]*)((?:\s+[^\s=>]+(?:="[^"]*")?)*)\s*\/?>/g;
  let m;
  let order = 0;
  while ((m = re.exec(html)) !== null) {
    const attrText = m[2] || "";
    const attrs = {};
    const ar = /([\w-]+)(?:="([^"]*)")?/g;
    let a;
    while ((a = ar.exec(attrText)) !== null) attrs[a[1]] = a[2] === undefined ? "" : a[2];
    if (!attrs.id) continue;
    byId.set(attrs.id, new El(m[1], attrs, order++));
  }
  return byId;
}

const DOM_TEMPLATE = buildDom();

/**
 * Run the page's script against a fresh stub.
 *
 * @param {{hash?: string, search?: string, token?: string|null,
 *          fetchImpl?: Function, theme?: string|null, google?: boolean}} opts
 */
function boot(opts = {}) {
  const byId = buildDom();

  const store = new Map();
  if (opts.token) store.set("xbt_token", opts.token);
  // Every write, not just the surviving ones: a value written and then removed
  // was still on the disk of whoever is holding the phone.
  const writes = [];
  const localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => {
      writes.push([k, String(v)]);
      store.set(k, String(v));
    },
    removeItem: (k) => store.delete(k),
  };

  const location = {
    hash: opts.hash === undefined ? "" : opts.hash,
    search: opts.search === undefined ? "" : opts.search,
    pathname: "/join/",
  };

  const replaceStateCalls = [];
  const history = {
    replaceState(state, title, url) {
      replaceStateCalls.push(url);
      // What a browser does: the entry loses its fragment.
      location.hash = "";
    },
  };

  const root = new El("html", opts.theme ? { "data-theme": opts.theme } : {}, -1);
  const document = {
    documentElement: root,
    getElementById: (id) => byId.get(id) || null,
  };

  const fetchCalls = [];
  const fetchImpl =
    opts.fetchImpl ||
    (async () => ({ ok: true, status: 200, json: async () => ({}) }));
  const fetch = (url, init) => {
    fetchCalls.push({ url, init });
    return fetchImpl(url, init, fetchCalls.length);
  };

  const googleCalls = [];
  const google =
    opts.google === false
      ? undefined
      : {
          accounts: {
            id: {
              initialize: (cfg) => googleCalls.push({ fn: "initialize", cfg }),
              renderButton: (slot, cfg) =>
                googleCalls.push({ fn: "renderButton", slot, cfg }),
            },
          },
        };

  const window = {
    google,
    matchMedia: (q) => ({ matches: /dark/.test(q) && opts.prefersDark === true }),
  };

  const run = new Function(
    "window",
    "document",
    "location",
    "history",
    "localStorage",
    "fetch",
    "setTimeout",
    PAGE_SCRIPT,
  );
  run(window, document, location, history, localStorage, fetch, setTimeout);

  return { byId, location, replaceStateCalls, fetchCalls, googleCalls, store, writes, root };
}

/** Let the page's awaits settle. */
async function settle() {
  for (let i = 0; i < 50; i++) await Promise.resolve();
  await new Promise((r) => setTimeout(r, 0));
  for (let i = 0; i < 50; i++) await Promise.resolve();
}

/** The join-by-code request, if one was made. */
function joinCall(ctx) {
  return ctx.fetchCalls.find((c) => String(c.url).includes("/v1/teams/join-by-code"));
}

// ---------------------------------------------------------------------------
// Tiny runner, house style.
// ---------------------------------------------------------------------------

let passed = 0;
let failed = 0;
const queue = [];
function test(name, fn) { queue.push([name, fn]); }

// ===========================================================================
// 1. The fragment carries the code, and the fragment is stripped
// ===========================================================================

test("the code arrives in the fragment and reaches the join request", async () => {
  const ctx = boot({
    hash: "#c=" + CODE_ENCODED,
    token: "xbt_live",
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      json: async () => ({ display_name: "Aibrussels" }),
    }),
  });
  await settle();
  const call = joinCall(ctx);
  assert.ok(call, "no join-by-code request was made for a code in the fragment");
  assert.equal(
    JSON.parse(call.init.body).code,
    CODE,
    "the code that reached the server is not the one in the fragment",
  );
});

test("a code in the QUERY STRING is not read — the fragment is the only source", async () => {
  // The whole privacy property in one assertion: if this page ever learns to
  // read ?c=, the code starts landing in hosting logs and in Referer headers,
  // and nothing about the rendered page would show it.
  const ctx = boot({ search: "?c=" + CODE, token: "xbt_live" });
  await settle();
  assert.equal(
    joinCall(ctx),
    undefined,
    "a query-string code was redeemed — the secret would be in every log on the path",
  );
  assert.ok(
    !ctx.byId.get("code-card").hidden,
    "with no fragment the page must fall back to asking for the code",
  );
});

test("the fragment is stripped from the address bar on arrival", async () => {
  const ctx = boot({ hash: "#c=" + CODE, token: "xbt_live" });
  await settle();
  assert.equal(ctx.location.hash, "", "the invite code is still sitting in the URL");
  assert.equal(ctx.replaceStateCalls.length, 1, "history.replaceState was not called once");
  assert.ok(
    !String(ctx.replaceStateCalls[0]).includes(CODE),
    "the replacement URL still carries the code",
  );
});

test("a malformed fragment is still stripped — the finally is what guarantees it", async () => {
  // decodeURIComponent throws on a lone '%'. The strip lives in a `finally`, so
  // the throw must not leave the secret in the address bar. Move the strip into
  // the try body and this is the assertion that notices.
  const ctx = boot({ hash: "#c=%E0%A4%A", token: "xbt_live" });
  await settle();
  assert.equal(ctx.location.hash, "", "a malformed code was left in the URL");
  assert.equal(ctx.replaceStateCalls.length, 1, "the strip did not run on the throwing path");
});

test("the strip survives a replaceState that throws", async () => {
  // Some embedded webviews refuse replaceState. The fallback clears the hash
  // directly; without it the code stays visible in a shared screen.
  const byId = buildDom();
  const location = { hash: "#c=" + CODE, search: "", pathname: "/join/" };
  const history = { replaceState() { throw new Error("SecurityError"); } };
  const store = new Map([["xbt_token", "xbt_live"]]);
  const run = new Function(
    "window", "document", "location", "history", "localStorage", "fetch", "setTimeout",
    PAGE_SCRIPT,
  );
  run(
    { google: undefined, matchMedia: () => ({ matches: false }) },
    { documentElement: new El("html", {}, -1), getElementById: (id) => byId.get(id) || null },
    location,
    history,
    {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => store.set(k, v),
      removeItem: (k) => store.delete(k),
    },
    async () => ({ ok: true, status: 200, json: async () => ({}) }),
    setTimeout,
  );
  await settle();
  assert.equal(location.hash, "", "replaceState threw and the code stayed in the URL");
});

test("the code is never written to storage, on any path that writes", async () => {
  // Checked on the paths that actually touch storage, not only on the one where
  // nothing does — an assertion that inspects an untouched store is an assertion
  // that cannot fail.
  const check = (ctx, label) => {
    for (const [k, v] of [...ctx.writes, ...ctx.store]) {
      assert.ok(
        !String(v).includes(CODE),
        `the invite code was persisted under "${k}" during ${label} — nothing will ` +
          "ever clean that up, and it is a bearer secret for a team's brain",
      );
    }
  };

  // (a) already signed in: straight to redeem.
  const direct = boot({
    hash: "#c=" + CODE,
    token: "xbt_live",
    fetchImpl: async () => ({ ok: true, status: 200, json: async () => ({ slug: "aib" }) }),
  });
  await settle();
  assert.ok(joinCall(direct), "the direct path did not redeem — this scenario proves nothing");
  check(direct, "a direct redemption");

  // (b) Google sign-in, which DOES write two keys.
  const google = boot({
    hash: "#c=" + CODE,
    token: null,
    fetchImpl: async (url) => {
      if (String(url).includes("/v1/me/api-token")) {
        return { ok: true, status: 200, json: async () => ({ token: "xbt_minted" }) };
      }
      if (String(url).endsWith("/v1/me")) {
        return { ok: true, status: 200, json: async () => ({ email: "nico@example.com" }) };
      }
      return { ok: true, status: 200, json: async () => ({ slug: "aib" }) };
    },
  });
  await settle();
  google.googleCalls.find((c) => c.fn === "initialize").cfg.callback({ credential: "gid" });
  await settle();
  assert.ok(google.writes.length >= 2, "the Google path wrote nothing — this scenario proves nothing");
  check(google, "a Google sign-in");

  // (c) password sign-in, which writes the same two keys.
  const local = boot({
    hash: "#c=" + CODE,
    token: null,
    fetchImpl: async (url) => {
      if (String(url).includes("/v1/auth/local/login")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ xbt_token: "xbt_local", user: { email: "nico@example.com" } }),
        };
      }
      return { ok: true, status: 200, json: async () => ({ slug: "aib" }) };
    },
  });
  await settle();
  local.byId.get("email").value = "nico@example.com";
  local.byId.get("password").value = "hunter2";
  local.byId.get("signin-form").dispatch("submit");
  await settle();
  assert.ok(local.writes.length >= 2, "the password path wrote nothing — this scenario proves nothing");
  check(local, "a password sign-in");

  // (d) typed by hand into the rescue form.
  const typed = boot({
    hash: "",
    token: "xbt_live",
    fetchImpl: async () => ({ ok: true, status: 200, json: async () => ({ slug: "aib" }) }),
  });
  await settle();
  typed.byId.get("code-input").value = CODE;
  typed.byId.get("code-form").dispatch("submit");
  await settle();
  assert.ok(joinCall(typed), "the typed code was never redeemed — this scenario proves nothing");
  check(typed, "a hand-typed code");
});

// ===========================================================================
// 2. Google before the password path
// ===========================================================================

test("Google is offered above the email/password form", () => {
  const card = html.slice(
    html.indexOf('id="signin-card"'),
    html.indexOf('id="result-card"'),
  );
  assert.ok(card.length > 200, "the sign-in card moved or vanished");
  const google = card.indexOf('id="google-btn"');
  const email = card.indexOf('id="email"');
  const password = card.indexOf('id="password"');
  assert.ok(google !== -1, "there is no Google slot in the sign-in card");
  assert.ok(email !== -1 && password !== -1, "the email/password fallback is gone");
  assert.ok(
    google < email && email < password,
    "the password form comes before Google — an extension account has no password " +
      "and the person would read the only path they cannot use first",
  );
});

test("asking someone to sign in mounts the Google button", async () => {
  // Signed out with a code in hand: the page must reach for Google, not just
  // render a form.
  const ctx = boot({ hash: "#c=" + CODE, token: null });
  await settle();
  assert.ok(!ctx.byId.get("signin-card").hidden, "the sign-in card was not shown");
  const rendered = ctx.googleCalls.find((c) => c.fn === "renderButton");
  assert.ok(rendered, "the Google button was never rendered");
  assert.equal(
    rendered.slot,
    ctx.byId.get("google-btn"),
    "the Google button was rendered somewhere other than its slot",
  );
  const init = ctx.googleCalls.find((c) => c.fn === "initialize");
  assert.match(
    init.cfg.client_id,
    /^50097563098-rdh24v05dcp0ees8o4kqviuuoi5sup3n\.apps\.googleusercontent\.com$/,
    "not the OAuth client the extension uses — a person who signed up there would " +
      "land on a different user row",
  );
});

test("a Google credential mints the same two keys the rest of app-site reads", async () => {
  const ctx = boot({
    hash: "#c=" + CODE,
    token: null,
    fetchImpl: async (url) => {
      if (String(url).includes("/v1/me/api-token")) {
        return { ok: true, status: 200, json: async () => ({ token: "xbt_minted" }) };
      }
      if (String(url).endsWith("/v1/me")) {
        return { ok: true, status: 200, json: async () => ({ email: "nico@example.com" }) };
      }
      return { ok: true, status: 200, json: async () => ({ display_name: "Aibrussels" }) };
    },
  });
  await settle();
  const cb = ctx.googleCalls.find((c) => c.fn === "initialize").cfg.callback;
  cb({ credential: "google-id-token" });
  await settle();
  assert.equal(ctx.store.get("xbt_token"), "xbt_minted", "the personal token was not stored");
  assert.equal(ctx.store.get("user_sub"), "nico@example.com", "the identity key was not stored");
  assert.ok(joinCall(ctx), "signing in with Google did not go on to redeem the invite");
});

test("the password path still works for accounts that have one", async () => {
  const ctx = boot({
    hash: "#c=" + CODE,
    token: null,
    fetchImpl: async (url) => {
      if (String(url).includes("/v1/auth/local/login")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ xbt_token: "xbt_local", user: { email: "nico@example.com" } }),
        };
      }
      return { ok: true, status: 200, json: async () => ({ display_name: "Aibrussels" }) };
    },
  });
  await settle();
  ctx.byId.get("email").value = "NICO@example.com";
  ctx.byId.get("password").value = "hunter2";
  ctx.byId.get("signin-form").dispatch("submit");
  await settle();
  assert.equal(ctx.store.get("xbt_token"), "xbt_local", "a password sign-in stored no token");
  assert.ok(joinCall(ctx), "a password sign-in did not go on to redeem the invite");
});

// ===========================================================================
// 3. The exit — on the joined path AND on the not-yet-joined one
// ===========================================================================

test("the exit to the chat is in the markup, on a plain anchor", () => {
  const exit = html.slice(html.indexOf('id="page-exit"'), html.indexOf("</nav>"));
  assert.ok(exit.length > 40, "the exit nav moved or vanished");
  assert.match(
    exit,
    /<a\s+href="\/app\/"/,
    "the exit does not point at /app/ — the chat somebody just joined",
  );
});

test("the exit survives every state, joined and not", async () => {
  // Four states, one assertion each. The exit is outside the cards and outside
  // hideAll() precisely so no state can take it away; put it inside either and
  // one of these fails.
  const states = [
    ["no code in the link", { hash: "", token: null }],
    ["signed out with a code", { hash: "#c=" + CODE, token: null }],
    [
      "joined",
      {
        hash: "#c=" + CODE,
        token: "xbt_live",
        fetchImpl: async () => ({
          ok: true,
          status: 200,
          json: async () => ({ display_name: "Aibrussels" }),
        }),
      },
    ],
    [
      "invite refused",
      {
        hash: "#c=" + CODE,
        token: "xbt_live",
        fetchImpl: async () => ({ ok: false, status: 404, json: async () => ({}) }),
      },
    ],
  ];
  for (const [label, opts] of states) {
    const ctx = boot(opts);
    await settle();
    const exit = ctx.byId.get("page-exit");
    assert.ok(exit, `there is no exit nav at all (${label})`);
    assert.equal(exit.hidden, false, `the exit was hidden in the "${label}" state`);
    assert.equal(
      ctx.byId.get("exit-app").href,
      "/app/",
      `the exit stopped pointing at the chat (${label})`,
    );
  }
});

test("a successful join offers the chat as the primary next step", async () => {
  const ctx = boot({
    hash: "#c=" + CODE,
    token: "xbt_live",
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      json: async () => ({ display_name: "Aibrussels" }),
    }),
  });
  await settle();
  const card = ctx.byId.get("result-card");
  const cta = ctx.byId.get("result-cta");
  assert.equal(card.hidden, false, "the outcome was never shown");
  assert.match(ctx.byId.get("result-title").textContent, /joined/i, "the outcome does not say so");
  assert.equal(cta.hidden, false, "the joined path offers no way into the chat");
  assert.equal(cta.href, "/app/", "the primary control does not open the chat");
  assert.match(cta.className, /btn-primary/, "the next step is not the primary control");
});

test("already-a-member is also a way in, not a shrug", async () => {
  const ctx = boot({
    hash: "#c=" + CODE,
    token: "xbt_live",
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      json: async () => ({ display_name: "Aibrussels", already_member: true }),
    }),
  });
  await settle();
  assert.equal(ctx.byId.get("result-cta").hidden, false, "an existing member is left with nothing");
  assert.equal(ctx.byId.get("result-cta").href, "/app/", "and no route to the chat");
});

test("the confirmation is shown, not redirected past", async () => {
  // The decision this page makes, asserted so a later 'friendlier' patch has to
  // argue with it: the outcome stays on screen and the chat is OFFERED. Nothing
  // here may navigate on its own — on a phone, in a standalone PWA, there is no
  // back button to recover the confirmation an automatic jump erased.
  const navigations = [];
  const byId = buildDom();
  const location = {
    hash: "#c=" + CODE,
    search: "",
    pathname: "/join/",
    get href() { return "https://grooveos.app/join/"; },
    set href(v) { navigations.push(v); },
    assign: (v) => navigations.push(v),
    replace: (v) => navigations.push(v),
  };
  const store = new Map([["xbt_token", "xbt_live"]]);
  const run = new Function(
    "window", "document", "location", "history", "localStorage", "fetch", "setTimeout",
    PAGE_SCRIPT,
  );
  run(
    { google: undefined, matchMedia: () => ({ matches: false }) },
    { documentElement: new El("html", {}, -1), getElementById: (id) => byId.get(id) || null },
    location,
    { replaceState() { location.hash = ""; } },
    {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => store.set(k, v),
      removeItem: (k) => store.delete(k),
    },
    async () => ({ ok: true, status: 200, json: async () => ({ display_name: "Aibrussels" }) }),
    setTimeout,
  );
  await settle();
  assert.deepEqual(
    navigations,
    [],
    `the page navigated on its own to ${navigations.join(", ")} — the confirmation is gone ` +
      "before it can be read",
  );
  assert.equal(byId.get("result-card").hidden, false, "and the confirmation was never shown");
  assert.ok(
    !/location\.(assign|replace)\s*\(|location\.href\s*=|window\.location\s*=|meta[^>]*http-equiv="refresh"/i.test(
      PAGE_SCRIPT,
    ),
    "the page contains a navigation it did not take on this path",
  );
});

// ===========================================================================
// 4. No literal colour below the tokens
// ===========================================================================

const STYLE = html.slice(
  html.indexOf("<style>") + "<style>".length,
  html.indexOf("</style>"),
);
const TOKEN_MARKER = "===== TOKENS END";
// Comments stripped. Every POSITIVE assertion about the stylesheet reads this
// one: prose about a rule is not the rule, and a grep that cannot tell them
// apart passes on a page where the rule was deleted and the comment survived.
const STYLE_CODE = STYLE.replace(/\/\*[\s\S]*?\*\//g, "");

test("the stylesheet is split into a palette and rules that resolve through it", () => {
  assert.ok(STYLE.length > 500, "the <style> block moved or vanished");
  const marker = STYLE.indexOf(TOKEN_MARKER);
  assert.ok(marker !== -1, "the tokens-end marker is gone — the gate below has nothing to slice on");

  // Nothing but token declarations above the marker, so a literal cannot be
  // smuggled into a real rule up there and escape the gate below.
  const above = STYLE.slice(0, marker).replace(/\/\*[\s\S]*?\*\//g, "");
  const selectors = above.match(/(^|\})\s*([^{}@]+)\{/g) || [];
  for (const sel of selectors) {
    const name = sel.replace(/^[}\s]*/, "").replace(/\s*\{$/, "").trim();
    assert.match(
      name,
      /^:root(\[data-theme="(dark|light)"\])?$/,
      `"${name}" is a real rule above the token marker — colours there dodge the gate`,
    );
  }
});

test("no literal colour survives below the tokens", () => {
  // Same gate the account page's API-key section carries. A hard-coded colour
  // here is what stops a theme change from reaching part of the page — and this
  // page ships BOTH themes, so half of it going stale is invisible to whoever
  // made the change.
  const below = STYLE.slice(STYLE.indexOf(TOKEN_MARKER)).replace(/\/\*[\s\S]*?\*\//g, "");
  assert.ok(below.length > 500, "the rules section is suspiciously short");

  const hexes = below.match(/#[0-9a-fA-F]{3,8}\b/g) || [];
  assert.deepEqual(hexes, [], `hard-coded colours won't follow the theme: ${hexes.join(", ")}`);

  const funcs = below.match(/\b(rgba?|hsla?|color-mix|lab|lch|oklch|oklab)\s*\(/g) || [];
  assert.deepEqual(funcs, [], `computed colours bypass the palette: ${funcs.join(", ")}`);

  // Named colours are the ones a hex regex waves through. `transparent` and
  // `currentColor` are not palette values and stay allowed.
  const NAMED =
    "white|black|red|green|blue|yellow|orange|purple|pink|brown|gray|grey|silver|" +
    "gold|cyan|magenta|lime|navy|teal|olive|maroon|aqua|fuchsia|indigo|violet|beige|ivory";
  const named = [];
  for (const decl of below.match(/[\w-]+\s*:\s*[^;{}]+/g) || []) {
    const value = decl.slice(decl.indexOf(":") + 1);
    const hit = value.match(new RegExp(`\\b(${NAMED})\\b`, "gi"));
    if (hit) named.push(decl.trim());
  }
  assert.deepEqual(named, [], `named colours won't follow the theme: ${named.join(" | ")}`);
});

/** Pull `--name: value;` pairs out of a declaration block. */
function declarations(block) {
  const out = {};
  for (const m of block.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) out[m[1]] = m[2].trim();
  return out;
}

/** The four palette blocks, each sliced from its own selector to its own close. */
function paletteBlocks(css) {
  const cut = (startRe) => {
    const m = startRe.exec(css);
    if (!m) return null;
    const open = css.indexOf("{", m.index);
    const end = css.indexOf("}", open);
    return end === -1 ? null : css.slice(open + 1, end);
  };
  return {
    // The bare `:root {` — first one only, and NOT :root[...] or the one nested
    // in the media query (which is preceded by the @media line).
    rootLight: cut(/(^|\n)\s*:root\s*\{/),
    mediaDark: cut(/@media\s*\(prefers-color-scheme:\s*dark\)\s*\{\s*:root\s*\{/),
    attrDark: cut(/:root\[data-theme="dark"\]\s*\{/),
    attrLight: cut(/:root\[data-theme="light"\]\s*\{/),
  };
}

test("the palette is the shadcn Neutral set the PWA uses, in every block", () => {
  const appCss = readFileSync(join(REPO_ROOT, "app-site", "app", "app.css"), "utf8");
  const tokens = STYLE.slice(0, STYLE.indexOf(TOKEN_MARKER));

  // Value for value against app.css. Drift here is exactly the defect this page
  // had: a member's first screen looking like a different product.
  //
  // Checked in EVERY block that defines them, not once across the file: the same
  // token is declared up to twice per theme, and a regex that stops at the first
  // hit passes while the block a phone actually resolves has gone stale.
  const LIGHT = {
    "--bg": "#FFFFFF",
    "--fg": "#0A0A0A",
    "--card": "#FFFFFF",
    "--muted": "#F5F5F5",
    "--muted-fg": "#737373",
    "--secondary": "#F5F5F5",
    "--secondary-fg": "#171717",
    "--primary": "#0A0A0A",
    "--primary-fg": "#FAFAFA",
    "--border": "#E5E5E5",
    "--input": "#E5E5E5",
    "--ring": "#A3A3A3",
    "--destructive": "#E5322D",
  };
  const DARK = {
    "--bg": "#0A0A0A",
    "--fg": "#FAFAFA",
    "--card": "#171717",
    "--muted": "#262626",
    "--muted-fg": "#A3A3A3",
    "--secondary": "#262626",
    "--secondary-fg": "#FAFAFA",
    "--primary": "#FAFAFA",
    "--primary-fg": "#171717",
    "--border": "rgba(255,255,255,.10)",
    "--input": "rgba(255,255,255,.15)",
    "--ring": "#787878",
    "--destructive": "#FB5A57",
  };

  const blocks = paletteBlocks(tokens);
  const appBlocks = paletteBlocks(appCss);
  for (const [name, block] of Object.entries(blocks)) {
    assert.ok(block, `the ${name} palette block is missing — one theme cannot resolve`);
  }

  for (const [blockName, expected] of [
    ["rootLight", LIGHT],
    ["attrLight", LIGHT],
    ["mediaDark", DARK],
    ["attrDark", DARK],
  ]) {
    const got = declarations(blocks[blockName]);
    const appGot = declarations(appBlocks[blockName] || "");
    for (const [token, value] of Object.entries(expected)) {
      assert.equal(
        (got[token] || "").toLowerCase(),
        value.toLowerCase(),
        `${blockName}: ${token} is "${got[token]}", not the Neutral value ${value}`,
      );
      assert.equal(
        (appGot[token] || "").toLowerCase(),
        value.toLowerCase(),
        `${blockName}: ${token} is no longer ${value} in app.css — this page copied a stale palette`,
      );
    }
  }

  assert.match(blocks.rootLight, /--radius\s*:\s*0px\s*;/, "radius 0 is the whole shape language");
  assert.match(
    tokens,
    /@media\s*\(prefers-color-scheme:\s*dark\)/,
    "no dark palette behind the OS preference",
  );
});

test("radius comes from the token, never from a literal corner", () => {
  const below = STYLE.slice(STYLE.indexOf(TOKEN_MARKER)).replace(/\/\*[\s\S]*?\*\//g, "");
  const radii = (below.match(/border-radius\s*:\s*([^;]+);/g) || []).filter(
    (d) => !/var\(--radius\)/.test(d),
  );
  assert.deepEqual(radii, [], `rounded corners are back: ${radii.join(", ")}`);
});

test("the page pulls in no stylesheet and no webfont", () => {
  // app.css's rule, applied to the front door: nothing renders after a round
  // trip to a font CDN on a phone connection.
  assert.ok(
    !/<link[^>]+rel="stylesheet"/i.test(html),
    "an external stylesheet is back — this page is one static file",
  );
  assert.ok(
    !/fonts\.(googleapis|gstatic)\.com/i.test(html),
    "the Google Fonts webfont is back; the Geist/system stack is the product's",
  );
  assert.match(STYLE_CODE, /--sans\s*:\s*'Geist'/, "the sans stack is not the product's");
  assert.match(STYLE_CODE, /--mono\s*:\s*'Geist Mono'/, "the mono stack is not the product's");
});

// ===========================================================================
// 5. Theme, and the phone
// ===========================================================================

test("the page is not nailed to one theme", () => {
  assert.ok(
    !/<html[^>]*data-theme="/i.test(html),
    "data-theme is hardcoded on <html> — one of the two themes can never appear",
  );
  assert.ok(THEME_SCRIPT, "nothing stamps the stored theme before paint");
  assert.match(
    THEME_SCRIPT,
    /"xbrain_theme_v1"/,
    "a second theme key — the choice made in the app would not be honoured here",
  );
  assert.match(
    THEME_SCRIPT,
    /===\s*"light"\s*\|\|\s*\w+\s*===\s*"dark"|===\s*"dark"\s*\|\|\s*\w+\s*===\s*"light"/,
    "an untrusted stored value reaches setAttribute",
  );
});

test("a stored light choice is honoured, and anything else is not", () => {
  const stamp = (stored) => {
    const root = new El("html", {}, -1);
    new Function("window", "document", THEME_SCRIPT)(
      { localStorage: { getItem: () => stored } },
      { documentElement: root },
    );
    return root.getAttribute("data-theme");
  };
  assert.equal(stamp("light"), "light", "a stored light choice was ignored");
  assert.equal(stamp("dark"), "dark", "a stored dark choice was ignored");
  assert.equal(stamp("DARK"), null, "a near-miss value was stamped onto the root");
  assert.equal(stamp('" onload="x'), null, "an untrusted value reached the DOM");
  assert.equal(stamp(null), null, "no choice must fall through to the OS preference in CSS");
});

test("blocked storage does not take the page down", () => {
  const root = new El("html", {}, -1);
  assert.doesNotThrow(() => {
    new Function("window", "document", THEME_SCRIPT)(
      {
        get localStorage() {
          throw new Error("storage partitioned");
        },
      },
      { documentElement: root },
    );
  }, "a partitioned localStorage throws before the page paints");
});

test("fields are 16px or larger — anything less makes iOS zoom and stay zoomed", () => {
  const below = STYLE.slice(STYLE.indexOf(TOKEN_MARKER)).replace(/\/\*[\s\S]*?\*\//g, "");
  const block = below.slice(below.indexOf(".field input"), below.indexOf(".btn {"));
  const size = block.match(/font-size\s*:\s*([\d.]+)px/);
  assert.ok(size, "the input has no font-size of its own");
  assert.ok(
    Number(size[1]) >= 16,
    `inputs are ${size[1]}px — iOS Safari zooms the page in on focus and never zooms out`,
  );
});

test("every input is labelled and every interactive thing shows focus", () => {
  for (const id of ["code-input", "email", "password"]) {
    assert.ok(
      new RegExp(`<label[^>]*for="${id}"`).test(html),
      `#${id} has no label — a screen reader announces an unnamed box`,
    );
  }
  for (const sel of [".field input:focus-visible", ".btn:focus-visible", "a:focus-visible"]) {
    assert.ok(STYLE_CODE.includes(sel), `no focus rule for ${sel} — keyboard users lose their place`);
  }
  assert.match(
    STYLE_CODE,
    /:focus-visible\s*\{[^}]*outline:\s*2px/,
    "the focus ring is not an outline",
  );
});

test("the safe areas are respected — this link is opened on a phone", () => {
  // Read the META, not the file: the prose above it names viewport-fit too, and
  // a whole-file match happily passes on a page that dropped the directive and
  // kept the comment explaining why it needed it.
  const meta = html.match(/<meta\s+name="viewport"\s+content="([^"]*)"/i);
  assert.ok(meta, "the page has no viewport meta at all");
  assert.match(
    meta[1],
    /viewport-fit=cover/,
    `env(safe-area-inset-*) resolves to nothing without it; content="${meta[1]}"`,
  );
  assert.match(meta[1], /width=device-width/, "the page is not sized to the device");
  // Same trap on the CSS side — the padding, not a sentence about the padding.
  const css = STYLE.replace(/\/\*[\s\S]*?\*\//g, "");
  assert.match(css, /padding-top:\s*calc\([^)]*env\(safe-area-inset-top\)/, "content can sit under the notch");
  assert.match(
    css,
    /padding-bottom:\s*calc\([^)]*env\(safe-area-inset-bottom\)/,
    "content can sit under the home indicator",
  );
});

// ===========================================================================
// 6. English only, and the shared constants
// ===========================================================================

test("the invite code never leaves this document, so sign-in may not either", () => {
  // /app/ moved its Google sign-in off the GIS popup and onto a top-level
  // redirect, because in a HOME-SCREEN web app the popup is a detached context
  // Google cannot recognise. Copying that here would break the front door rather
  // than fix it, and this asserts the two properties that make it so.
  //
  // 1. The code lives in the fragment and is moved into a variable on load. A
  //    redirect leaves the document; the variable dies with it and the fragment
  //    has already been stripped, so the invitee comes back signed in to nothing.
  // 2. This page is outside the manifest scope (/app/), so it is never the
  //    surface the popup is broken on in the first place.
  assert.match(
    PAGE_SCRIPT,
    /location\.hash/,
    "the invite code must still be read from the fragment",
  );
  assert.ok(
    !/\/v1\/auth\/google\/(start|web-config)/.test(PAGE_SCRIPT),
    "this page must not enter the redirect sign-in flow — leaving the document loses the invite code",
  );
  assert.ok(
    !/location\.assign\(|location\.href\s*=\s*["'`]https:\/\/(accounts\.google|api\.)/.test(PAGE_SCRIPT),
    "no top-level navigation away from this page before the code has been redeemed",
  );

  const manifest = JSON.parse(
    readFileSync(join(REPO_ROOT, "app-site", "app", "manifest.webmanifest"), "utf8"),
  );
  assert.equal(
    manifest.scope,
    "/app/",
    "if the installed app's scope ever covered /join/, this page WOULD run standalone and the popup would break here too",
  );
});

test("the shared constants are neither renamed nor duplicated", () => {
  assert.ok(html.includes('"xbt_token"'), "the canonical token key was renamed");
  assert.ok(html.includes('"user_sub"'), "the canonical identity key was renamed");
  assert.ok(
    html.includes('"https://api.grooveos.app"'),
    "the API base moved — this page would talk to a different server than the rest of app-site",
  );
});

test("the page is in English", () => {
  // CLAUDE.md: everything a user reads ships in English.
  const visible = html
    .replace(/<style>[\s\S]*?<\/style>/g, "")
    .replace(/<script[\s\S]*?<\/script>/g, "")
    .replace(/<!--[\s\S]*?-->/g, "");
  const FRENCH = /\b(vous|votre|équipe|rejoindre|connexion|compte|mot de passe|invitation)\b/i;
  assert.ok(!FRENCH.test(visible), "French copy on a page that ships in English");
  assert.match(html, /<html lang="en"/, "the page does not declare English");
});

// ---------------------------------------------------------------------------

for (const [name, fn] of queue) {
  try {
    await fn();
    console.log(`  PASS: ${name}`);
    passed++;
  } catch (e) {
    console.error(`  FAIL: ${name}\n        ${e.message}`);
    failed++;
  }
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
