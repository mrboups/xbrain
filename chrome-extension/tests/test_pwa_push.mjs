/**
 * Contract for the PWA's push opt-in at app-site/app/push.js (Phase 27,
 * Plan 27-07).
 *
 * It lives in the extension's test directory because that is where
 * run_tests.mjs walks; it reads ../../app-site/app/ and asserts nothing about
 * the extension itself.
 *
 * THE PROMPT GATE IS THE POINT OF THIS FILE. A browser gives each site exactly
 * one chance to ask for notification access. Ask on load, before the person has
 * any reason to say yes, and a large share of them press Block -- which no code
 * can ever undo, on any later visit, for any reason. It is not recoverable from
 * the app; it needs the browser's own site settings. D-27-05 therefore puts the
 * ask behind an explicit click, and this file is what stops that decision from
 * eroding: one call site, inside one function, reachable only from a click
 * listener.
 *
 * Two halves:
 *   1. BEHAVIOUR -- push.js is driven against stubbed browser globals, so the
 *      order of its checks (capability, then blocked, then server config, then
 *      and only then the ask) is exercised rather than described.
 *   2. STRUCTURE -- the source is read as text, with comments stripped first.
 *
 * SKIP = FAIL: nothing below is conditional on a file existing.
 */

import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, "..", "..");
const APP_DIR = join(REPO_ROOT, "app-site", "app");

let passed = 0;
let failed = 0;
function test(name, body) {
  try {
    body();
    console.log(`  PASS: ${name}`);
    passed++;
  } catch (e) {
    console.error(`  FAIL: ${name}`);
    console.error(`    ${e.stack || e.message}`);
    failed++;
  }
}

/** Same shape, for the async behaviour probes. */
const pending = [];
function testAsync(name, body) {
  pending.push([name, body]);
}

// ---- The module under test ----------------------------------------------

let push = null;
let importError = null;
try {
  push = await import(pathToFileURL(join(APP_DIR, "push.js")).href);
} catch (e) {
  importError = e;
}

/**
 * Importing must not need a browser: a module that touches `navigator` or
 * `Notification` at top level is a module that can act before a click.
 */
function mod() {
  assert.ok(
    push,
    `app-site/app/push.js could not be imported: ${importError && importError.message}`,
  );
  return push;
}

// ---- Browser stubs ------------------------------------------------------

/** A real VAPID public key (the throwaway example from the plan). */
const VAPID_KEY =
  "BEl62iUYgUivxIkv69yViEuiBIa-Ib9-SkvMeAtA3LFgDzkrxZJjSgSnfckjBJuBkr3qBUYIHBQFLXYp5Nksh8U";
const OTHER_KEY =
  "BMxT7hHnBHOKMBTdxnMKPHIILWBPMYCFHfDMkQiPPvBBnUXnXBIKfNzKKPqEDKKSAmYyGZKPfKmzGYINJEmXSDo";

function define(name, value) {
  Object.defineProperty(globalThis, name, {
    value,
    configurable: true,
    writable: true,
  });
}

function keyBytes(base64url) {
  const padded = base64url + "=".repeat((4 - (base64url.length % 4)) % 4);
  const raw = Buffer.from(padded.replace(/-/g, "+").replace(/_/g, "/"), "base64");
  return new Uint8Array(raw).buffer;
}

function makeSubscription(endpoint, key, log) {
  return {
    endpoint,
    options: { applicationServerKey: key ? keyBytes(key) : null },
    toJSON: () => ({
      endpoint,
      keys: { p256dh: `p256dh-${endpoint}`, auth: `auth-${endpoint}` },
    }),
    async unsubscribe() {
      log.push(`browser-unsubscribe:${endpoint}`);
      return true;
    },
  };
}

/**
 * Install a fake browser. Everything the module can observe is recorded in
 * `log` IN ORDER, because several of the guarantees here are ordering
 * guarantees: the server is told before the browser drops a subscription, and
 * nothing is asked of the user before the server says push is even configured.
 */
function browser(opts = {}) {
  const {
    permission = "default",
    decision = "granted",
    hasPushManager = true,
    hasServiceWorker = true,
    hasNotification = true,
    existing = null,
    userAgent = "Mozilla/5.0 (X11; Linux x86_64) TestBrowser/1.0",
    standalone = true,
    config = { enabled: true, vapid_public_key: VAPID_KEY },
    subscribeFails = false,
    configFails = false,
  } = opts;

  const log = [];
  const requests = [];
  const listeners = {};
  let current = null;

  /**
   * A real browser drops the subscription it just released, so the stub must
   * too: leaving it readable would let a test "pass" while the button reported
   * a device that is no longer subscribed.
   */
  function track(sub) {
    const inner = sub.unsubscribe.bind(sub);
    sub.unsubscribe = async () => {
      const result = await inner();
      if (current === sub) current = null;
      return result;
    };
    return sub;
  }

  if (existing) {
    current = track(makeSubscription(existing.endpoint, existing.key, log));
  }

  const registration = {
    pushManager: {
      async getSubscription() {
        return current;
      },
      async subscribe(options) {
        log.push(
          `browser-subscribe:userVisibleOnly=${options.userVisibleOnly}:keyBytes=${
            options.applicationServerKey ? options.applicationServerKey.length : 0
          }`,
        );
        current = track(
          makeSubscription("https://push.example.test/fresh", config.vapid_public_key, log),
        );
        return current;
      },
    },
  };

  define("Notification", hasNotification
    ? {
        get permission() {
          log.push("read-permission");
          return permission;
        },
        async requestPermission() {
          log.push("ask-permission");
          return decision;
        },
      }
    : undefined);

  const windowStub = { matchMedia: () => ({ matches: standalone }) };
  if (hasPushManager) windowStub.PushManager = function PushManager() {};
  define("window", windowStub);

  const navigatorStub = { userAgent, standalone };
  if (hasServiceWorker) {
    navigatorStub.serviceWorker = {
      ready: Promise.resolve(registration),
      addEventListener(type, fn) {
        log.push(`sw-listener:${type}`);
        listeners[type] = fn;
      },
    };
  }
  define("navigator", navigatorStub);

  const api = {
    async request(path, options = {}) {
      log.push(`request:${path}`);
      requests.push({ path, options });
      if (path === "/v1/push/config") {
        if (configFails) throw new Error("HTTP 503: down");
        return config;
      }
      if (path === "/v1/push/subscribe") {
        if (subscribeFails) throw new Error("HTTP 500: nope");
        return { status: "subscribed" };
      }
      if (path === "/v1/push/unsubscribe") return null;
      throw new Error(`unexpected path ${path}`);
    },
  };

  return { log, requests, listeners, api, get current() { return current; } };
}

function fakeEl() {
  const attrs = {};
  return {
    attrs,
    dataset: {},
    disabled: false,
    hidden: false,
    textContent: "",
    title: "",
    listeners: {},
    setAttribute(k, v) {
      attrs[k] = String(v);
    },
    getAttribute(k) {
      return Object.prototype.hasOwnProperty.call(attrs, k) ? attrs[k] : null;
    },
    addEventListener(type, fn) {
      this.listeners[type] = fn;
    },
  };
}

// ---- 1. urlBase64ToUint8Array -------------------------------------------

test("urlBase64ToUint8Array decodes a real VAPID public key to 65 bytes", () => {
  const arr = mod().urlBase64ToUint8Array(VAPID_KEY);
  assert.ok(arr instanceof Uint8Array, "must return a Uint8Array, not a string");
  assert.equal(
    arr.length,
    65,
    "a P-256 uncompressed point is 65 bytes; a wrong length means the browser rejects the subscribe call",
  );
  assert.equal(
    arr[0],
    0x04,
    "byte 0 of an uncompressed EC point is 0x04 -- anything else means the padding or alphabet swap is wrong",
  );
});

test("urlBase64ToUint8Array tolerates missing padding and the URL-safe alphabet", () => {
  const { urlBase64ToUint8Array } = mod();
  // A real VAPID key happens to contain "-" but not always "_", so the alphabet
  // swap is pinned against a fixture chosen to contain BOTH. Without the swap
  // atob throws or, worse, decodes to the wrong bytes on some inputs.
  const bytes = [0xfa, 0xfb, 0xfc, 0xfd, 0xfe];
  const b64url = "-vv8_f4";
  assert.ok(
    b64url.includes("-") && b64url.includes("_"),
    "the fixture must exercise both URL-safe characters or this test is inert",
  );
  assert.equal(b64url.length % 4, 3, "the fixture must be unpadded to exercise the pad step");
  assert.deepEqual([...urlBase64ToUint8Array(b64url)], bytes, "URL-safe alphabet + padding");
  assert.deepEqual(
    [...urlBase64ToUint8Array(`${b64url}=`)],
    bytes,
    "an already-padded input must decode to the same bytes",
  );
  assert.ok(VAPID_KEY.includes("-"), "the real-key fixture still exercises the '-' swap");
});

// ---- 2. enablePush: every refusal happens BEFORE the ask ----------------

testAsync("enablePush refuses an unsupported browser without asking anyone", async () => {
  const b = browser({ hasPushManager: false });
  const result = await mod().enablePush(b.api);
  assert.equal(result.ok, false);
  assert.equal(result.reason, "unsupported");
  assert.ok(
    !b.log.includes("ask-permission"),
    "a browser that cannot receive a push must never be asked for permission",
  );
  assert.ok(
    !b.log.some((e) => e.startsWith("read-permission")),
    "capability must be settled before Notification is touched at all",
  );
  assert.equal(b.requests.length, 0, "nothing should be sent to the server");
});

testAsync("enablePush tells an iOS user to install rather than showing a dead control", async () => {
  const b = browser({
    hasPushManager: false,
    standalone: false,
    userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) Safari/605.1.15",
  });
  const result = await mod().enablePush(b.api);
  assert.equal(result.ok, false);
  assert.equal(
    result.reason,
    "install_required",
    "iOS exposes push only to an app added to the Home Screen; 'unsupported' would be a lie the user cannot act on",
  );
  assert.ok(!b.log.includes("ask-permission"));
});

testAsync("enablePush does not re-ask when the browser has already blocked us", async () => {
  const b = browser({ permission: "denied" });
  const result = await mod().enablePush(b.api);
  assert.equal(result.ok, false);
  assert.equal(result.reason, "blocked");
  assert.ok(
    !b.log.includes("ask-permission"),
    "a denied permission cannot be re-requested by any code -- asking again is a no-op that hides the real fix",
  );
  assert.equal(b.requests.length, 0);
});

testAsync("enablePush refuses when the server has no signing key, without asking", async () => {
  const b = browser({ config: { enabled: false, vapid_public_key: "" } });
  const result = await mod().enablePush(b.api);
  assert.equal(result.ok, false);
  assert.equal(result.reason, "server_disabled");
  assert.ok(
    b.log.indexOf("request:/v1/push/config") >= 0,
    "the client must consult the server before spending the user's one-shot decision",
  );
  assert.ok(
    !b.log.includes("ask-permission"),
    "a mis-configured deployment must not burn a permission it can never use",
  );
});

testAsync("enablePush reports a declined prompt and posts nothing", async () => {
  const b = browser({ permission: "default", decision: "denied" });
  const result = await mod().enablePush(b.api);
  assert.equal(result.ok, false);
  assert.equal(result.reason, "denied");
  assert.equal(b.log.filter((e) => e === "ask-permission").length, 1, "exactly one ask");
  assert.ok(
    !b.requests.some((r) => r.path === "/v1/push/subscribe"),
    "a declined prompt must leave no server row",
  );
  assert.ok(
    !b.log.some((e) => e.startsWith("browser-subscribe")),
    "and no browser subscription either",
  );
});

// ---- 3. enablePush: the success path ------------------------------------

testAsync("enablePush registers endpoint, p256dh and auth with the server", async () => {
  const b = browser({ permission: "default", decision: "granted" });
  const result = await mod().enablePush(b.api);
  assert.equal(result.ok, true, `expected success, got ${JSON.stringify(result)}`);

  const subscribeCall = b.log.find((e) => e.startsWith("browser-subscribe"));
  assert.ok(subscribeCall, "the browser must actually be subscribed");
  assert.ok(
    subscribeCall.includes("userVisibleOnly=true"),
    "Chrome rejects a subscription that does not promise a visible notification",
  );
  assert.ok(
    subscribeCall.includes("keyBytes=65"),
    "applicationServerKey must be the decoded 65-byte key; Safari rejects a base64 string",
  );

  const post = b.requests.find((r) => r.path === "/v1/push/subscribe");
  assert.ok(post, "the subscription must be registered server-side");
  assert.equal(post.options.method, "POST");
  const body = post.options.body;
  assert.ok(body.endpoint, "endpoint is the mailbox address");
  assert.ok(body.keys.p256dh, "p256dh is required to encrypt the payload");
  assert.ok(body.keys.auth, "auth is required to encrypt the payload");
  assert.ok(
    typeof body.user_agent === "string" && body.user_agent.length <= 256,
    "user_agent labels the device and is capped",
  );
});

testAsync("a failed registration rolls the browser subscription back", async () => {
  const b = browser({ permission: "granted", subscribeFails: true });
  const result = await mod().enablePush(b.api);
  assert.equal(result.ok, false);
  assert.equal(result.reason, "server_error");
  assert.ok(
    b.log.some((e) => e.startsWith("browser-unsubscribe")),
    "a browser subscription the server does not know about is a device that silently receives nothing while the UI claims it is on",
  );
});

// ---- 4. Key rotation self-heal ------------------------------------------

testAsync("a subscription made with an old server key is replaced, not reused", async () => {
  const b = browser({
    permission: "granted",
    existing: { endpoint: "https://push.example.test/stale", key: OTHER_KEY },
  });

  const result = await mod().enablePush(b.api);
  assert.equal(result.ok, true, `expected a repaired subscription, got ${JSON.stringify(result)}`);
  assert.ok(
    b.log.includes("browser-unsubscribe:https://push.example.test/stale"),
    "the stale subscription must be dropped -- the server can never sign for it again",
  );
  assert.ok(
    b.log.some((e) => e.startsWith("browser-subscribe")),
    "and a fresh one taken with the current key",
  );
  const posted = b.requests.find((r) => r.path === "/v1/push/subscribe");
  assert.notEqual(
    posted.options.body.endpoint,
    "https://push.example.test/stale",
    "the server must be told the NEW endpoint",
  );
});

testAsync("a subscription that already matches the server key is not churned", async () => {
  const b = browser({
    permission: "granted",
    existing: { endpoint: "https://push.example.test/good", key: VAPID_KEY },
  });
  const result = await mod().enablePush(b.api);
  assert.equal(result.ok, true);
  assert.ok(
    !b.log.some((e) => e.startsWith("browser-subscribe")),
    "re-subscribing a healthy device would change its endpoint for no reason and lose anything in flight",
  );
  const posted = b.requests.find((r) => r.path === "/v1/push/subscribe");
  assert.equal(posted.options.body.endpoint, "https://push.example.test/good");
});

// ---- 5. disablePush: the server is told first ---------------------------

testAsync("disablePush prunes the server BEFORE dropping the browser subscription", async () => {
  const b = browser({
    permission: "granted",
    existing: { endpoint: "https://push.example.test/live", key: VAPID_KEY },
  });

  const result = await mod().disablePush(b.api);
  assert.equal(result.ok, true);

  const serverAt = b.log.indexOf("request:/v1/push/unsubscribe");
  const browserAt = b.log.indexOf("browser-unsubscribe:https://push.example.test/live");
  assert.ok(serverAt >= 0, "the server must be told, or the row survives and keeps being sent to");
  assert.ok(browserAt >= 0, "the browser subscription must be released");
  assert.ok(
    serverAt < browserAt,
    "server first: a row with no live subscription is pruned on the first 404/410, but a live subscription with no row is invisible and unfixable from the app",
  );
  const post = b.requests.find((r) => r.path === "/v1/push/unsubscribe");
  assert.equal(post.options.body.endpoint, "https://push.example.test/live");
});

testAsync("disablePush with nothing subscribed is a no-op, not an error", async () => {
  const b = browser({ permission: "granted", existing: null });
  const result = await mod().disablePush(b.api);
  assert.equal(result.ok, true);
  assert.equal(b.requests.length, 0);
});

// ---- 6. refreshPushButton reads, and only reads --------------------------

testAsync("refreshPushButton reflects the real state and never asks", async () => {
  const cases = [
    [{ hasPushManager: false }, "unsupported", "Notifications unavailable"],
    [{ permission: "denied" }, "blocked", "Notifications blocked in browser settings"],
    [{ permission: "default" }, "off", "Notifications off"],
    [
      {
        permission: "granted",
        existing: { endpoint: "https://push.example.test/on", key: VAPID_KEY },
      },
      "on",
      "Notifications on",
    ],
    [
      {
        hasPushManager: false,
        standalone: false,
        userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) Safari/605.1.15",
      },
      "install_required",
      "Notifications need the installed app",
    ],
  ];

  for (const [opts, expectedState, expectedLabel] of cases) {
    const b = browser(opts);
    const btn = fakeEl();
    const hint = fakeEl();
    await mod().refreshPushButton(b.api, btn, hint);
    assert.equal(
      btn.getAttribute("data-state"),
      expectedState,
      `state for ${JSON.stringify(opts)}`,
    );
    assert.equal(btn.textContent, expectedLabel, `label for ${expectedState}`);
    assert.ok(hint.textContent.length > 0, `a state with no explanation is a dead end (${expectedState})`);
    assert.ok(
      !b.log.includes("ask-permission"),
      "refreshPushButton runs on load -- it must never prompt",
    );
    assert.equal(
      b.requests.length,
      0,
      "refreshPushButton must not write anything; it reports what is already true",
    );
  }
});

testAsync("only a real, usable state leaves the button clickable", async () => {
  for (const [opts, clickable] of [
    [{ permission: "default" }, true],
    [{ permission: "denied" }, false],
    [{ hasPushManager: false }, false],
  ]) {
    const b = browser(opts);
    const btn = fakeEl();
    await mod().refreshPushButton(b.api, btn, fakeEl());
    assert.equal(
      btn.disabled,
      !clickable,
      `a ${btn.getAttribute("data-state")} button must ${clickable ? "not " : ""}be disabled`,
    );
  }
});

// ---- 7. wirePushButton: the click is the only door ----------------------

testAsync("the click handler toggles, and wiring twice does not double-toggle", async () => {
  const b = browser({ permission: "default", decision: "granted" });
  const btn = fakeEl();
  const hint = fakeEl();
  const { wirePushButton } = mod();

  wirePushButton(b.api, btn, hint);
  wirePushButton(b.api, btn, hint);
  assert.ok(btn.listeners.click, "a click listener must be attached");
  assert.equal(
    b.log.filter((e) => e === "sw-listener:message").length,
    1,
    "the rotation listener must be attached exactly once",
  );

  // Nothing may have happened yet: wiring is not consent.
  assert.ok(!b.log.includes("ask-permission"), "wiring must not prompt");
  assert.equal(b.requests.length, 0, "wiring must not talk to the server either");

  await btn.listeners.click();
  assert.equal(
    b.log.filter((e) => e === "ask-permission").length,
    1,
    "one click, one ask -- a doubled listener would enable then immediately disable",
  );
  assert.equal(btn.getAttribute("data-state"), "on");

  await btn.listeners.click();
  assert.ok(
    b.requests.some((r) => r.path === "/v1/push/unsubscribe"),
    "clicking an on button must turn it off on the server too",
  );
  assert.equal(btn.getAttribute("data-state"), "off");
});

// ---- Structural half: the gate itself -----------------------------------

/**
 * Comments out FIRST, everywhere below.
 *
 * The module docstring in push.js exists to explain the invariant, and prose
 * that explains a rule inevitably names the thing the rule is about. A gate
 * that counts its own documentation measures nothing: it would fire on a
 * correct file that describes itself, and it would stay silent on a file that
 * hides a real call inside a commented-out block. Over-stripping is the safe
 * direction - it can only shrink what these checks see, never invent a match.
 */
function stripComments(src) {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

/**
 * Blank out the CONTENTS of string and template literals, keeping length and
 * line breaks. A brace inside a string would otherwise move the depth counter
 * and make the top-level check report nonsense.
 */
function stripStringBodies(src) {
  let out = "";
  let i = 0;
  while (i < src.length) {
    const ch = src[i];
    if (ch === '"' || ch === "'" || ch === "`") {
      out += ch;
      i += 1;
      while (i < src.length) {
        if (src[i] === "\\") {
          out += "  ";
          i += 2;
          continue;
        }
        if (src[i] === ch) {
          out += ch;
          i += 1;
          break;
        }
        out += src[i] === "\n" ? "\n" : " ";
        i += 1;
      }
      continue;
    }
    out += ch;
    i += 1;
  }
  return out;
}

/** Brace depth at every character, on comment- and string-stripped source. */
function depthMap(code) {
  const depths = new Array(code.length).fill(0);
  let depth = 0;
  for (let i = 0; i < code.length; i += 1) {
    if (code[i] === "}") depth -= 1;
    depths[i] = depth;
    if (code[i] === "{") depth += 1;
  }
  return depths;
}

/** The body of `function NAME(...)`, by brace matching. */
function functionBody(code, name) {
  const decl = new RegExp(`function\\s+${name}\\s*\\(`).exec(code);
  assert.ok(decl, `could not find function ${name}`);
  const open = code.indexOf("{", decl.index);
  assert.ok(open > 0, `could not find the body of ${name}`);
  let depth = 0;
  for (let i = open; i < code.length; i += 1) {
    if (code[i] === "{") depth += 1;
    if (code[i] === "}") {
      depth -= 1;
      if (depth === 0) return { start: open, end: i, text: code.slice(open, i + 1) };
    }
  }
  throw new Error(`unbalanced braces in ${name}`);
}

/** Every .js directly in app-site/app/, plus the markup. */
function appSources() {
  const out = readdirSync(APP_DIR)
    .filter((f) => f.endsWith(".js"))
    .sort()
    .map((f) => ({ name: f, src: readFileSync(join(APP_DIR, f), "utf8") }));
  out.push({ name: "index.html", src: readFileSync(join(APP_DIR, "index.html"), "utf8") });
  return out;
}

const PROMPT_APIS = ["requestPermission", "pushManager.subscribe"];

test("the comment stripper is not inert, and the docstring is not the gate", () => {
  const raw = readFileSync(join(APP_DIR, "push.js"), "utf8");
  const code = stripComments(raw);
  assert.ok(
    code.length < raw.length * 0.75,
    "push.js is heavily commented; if stripping removed almost nothing, the stripper is broken and every check below is measuring prose",
  );
  const comments = [...raw.matchAll(/\/\*[\s\S]*?\*\//g), ...raw.matchAll(/^\s*\/\/.*$/gm)]
    .map((m) => m[0])
    .join("\n");
  assert.ok(comments.length > 1000, "expected substantial prose to have been removed");
  for (const api of PROMPT_APIS) {
    assert.ok(
      !comments.includes(api),
      `the removed comments contain "${api}" - the docstring deliberately does not spell either API name, so that a plain count over the raw file stays meaningful too`,
    );
  }
});

test("push.js is the ONLY file under app-site/app/ that can raise the prompt", () => {
  let owners = [];
  for (const { name, src } of appSources()) {
    const code = stripComments(src);
    if (PROMPT_APIS.some((api) => code.includes(api))) owners.push(name);
  }
  assert.deepEqual(
    owners,
    ["push.js"],
    `exactly one file may reach the permission prompt (D-27-05); found ${JSON.stringify(owners)}`,
  );
});

test("each prompt API appears exactly once in push.js, and never at top level", () => {
  const code = stripStringBodies(stripComments(readFileSync(join(APP_DIR, "push.js"), "utf8")));
  const depths = depthMap(code);
  for (const api of PROMPT_APIS) {
    const at = [...code.matchAll(new RegExp(api.replace(".", "\\."), "g"))].map((m) => m.index);
    assert.equal(
      at.length,
      1,
      `"${api}" must appear exactly once in push.js; found ${at.length}`,
    );
    assert.ok(
      depths[at[0]] > 0,
      `"${api}" sits at brace depth ${depths[at[0]]} - at top level it runs on import, which is a prompt on load`,
    );
  }
});

test("enablePush is called from exactly one place, inside a click listener", () => {
  const code = stripComments(readFileSync(join(APP_DIR, "push.js"), "utf8"));
  const calls = [...code.matchAll(/(?:^|[^.\w$])enablePush\s*\(/g)]
    .map((m) => m.index + m[0].length - "enablePush(".length)
    .filter((i) => !/function\s+$/.test(code.slice(Math.max(0, i - 24), i)));
  assert.equal(
    calls.length,
    1,
    `enablePush must be called exactly once; found ${calls.length} call site(s)`,
  );

  const wire = functionBody(code, "wirePushButton");
  assert.ok(
    calls[0] > wire.start && calls[0] < wire.end,
    "the one call site must live inside wirePushButton",
  );
  const clickAt = code.indexOf('addEventListener("click"', wire.start);
  assert.ok(
    clickAt > wire.start && clickAt < wire.end,
    "wirePushButton must attach a click listener",
  );
  assert.ok(
    calls[0] > clickAt,
    "the call must sit INSIDE the click listener - reachable by a user gesture and by nothing else",
  );
});

test("push.js talks to the three push endpoints and to nothing else", () => {
  const code = stripComments(readFileSync(join(APP_DIR, "push.js"), "utf8"));
  const paths = [...code.matchAll(/\/v1\/[A-Za-z0-9_/-]+/g)].map((m) => m[0]).sort();
  assert.deepEqual(
    paths,
    ["/v1/push/config", "/v1/push/subscribe", "/v1/push/unsubscribe"],
    "each of the three exactly once, and no other API path",
  );
});

test("push.js exports the surface the shell binds to", () => {
  const code = readFileSync(join(APP_DIR, "push.js"), "utf8");
  for (const name of [
    "urlBase64ToUint8Array",
    "enablePush",
    "disablePush",
    "refreshPushButton",
    "wirePushButton",
  ]) {
    assert.match(
      code,
      new RegExp(`export\\s+(async\\s+)?function\\s+${name}\\b`),
      `push.js must export ${name}`,
    );
  }
});

test("no VAPID private key material anywhere under app-site/app/", () => {
  for (const { name, src } of appSources()) {
    assert.ok(
      !/VAPID_PRIVATE|vapid_private|PRIVATE KEY/.test(src),
      `${name} must never carry private key material - the client only ever gets the public half`,
    );
  }
  // The public key is fetched, never baked in: a build-time constant would mean
  // a server key rotation needs a client release (27-CONTEXT).
  const push = readFileSync(join(APP_DIR, "push.js"), "utf8");
  assert.ok(
    !/B[A-Za-z0-9_-]{85,}/.test(stripComments(push)),
    "push.js appears to contain a literal VAPID public key - it must come from /v1/push/config",
  );
});

// ---- The worker's half of the rotation repair ---------------------------

test("sw.js reports a rotation and repairs nothing itself", () => {
  const sw = stripComments(readFileSync(join(APP_DIR, "sw.js"), "utf8"));
  assert.equal(
    (sw.match(/addEventListener\("pushsubscriptionchange"/g) || []).length,
    1,
    "exactly one rotation handler",
  );
  for (const api of PROMPT_APIS) {
    assert.ok(
      !sw.includes(api),
      `a worker cannot prompt and holds no token; it must not reference ${api}`,
    );
  }
  assert.ok(sw.includes("postMessage"), "the worker's only move is to tell an open client");
});

test("the rotation message literal is the same on both sides", () => {
  const push = stripComments(readFileSync(join(APP_DIR, "push.js"), "utf8"));
  const sw = stripComments(readFileSync(join(APP_DIR, "sw.js"), "utf8"));
  const declared = /PUSH_ROTATED_MESSAGE\s*=\s*"([^"]+)"/.exec(push);
  assert.ok(declared, "push.js must name the rotation message in one constant");
  assert.ok(
    sw.includes(`"${declared[1]}"`),
    `sw.js posts a different message type than push.js listens for; a typo here means a rotated device silently never repairs (client expects "${declared[1]}")`,
  );
});

// ---- The shell must not offer a signed-out person a token-gated control --

test("the push toggle and its hint are hidden on the signed-out surface", () => {
  const app = stripComments(readFileSync(join(APP_DIR, "app.js"), "utf8"));
  const out = functionBody(app, "showSignedOut").text;
  const inn = functionBody(app, "showSignedIn").text;
  for (const id of ["btn-enable-push", "push-hint"]) {
    assert.match(
      out,
      new RegExp(`el\\("${id}"\\)[\\s\\S]{0,40}?hidden\\s*=\\s*true`),
      `showSignedOut must hide #${id} - every push endpoint is user-gated, so a signed-out click can only produce an unexplained failure`,
    );
    assert.match(
      inn,
      new RegExp(`el\\("${id}"\\)[\\s\\S]{0,40}?hidden\\s*=\\s*false`),
      `showSignedIn must reveal #${id}`,
    );
  }
});

test("every id app.js reads is declared in index.html", () => {
  const app = readFileSync(join(APP_DIR, "app.js"), "utf8");
  const html = readFileSync(join(APP_DIR, "index.html"), "utf8");
  const ids = new Set([...app.matchAll(/\bel\(\s*["']([a-z0-9-]+)["']\s*\)/g)].map((m) => m[1]));
  assert.ok(ids.size >= 8, `expected app.js to bind several ids, found ${ids.size}`);
  for (const id of ids) {
    assert.ok(
      html.includes(`id="${id}"`),
      `app.js reads #${id}, which index.html does not declare - the binding silently does nothing`,
    );
  }
});

test("english-only: no accented Latin chars in push.js", () => {
  const hits = readFileSync(join(APP_DIR, "push.js"), "utf8").match(/[À-ÿ]/g) || [];
  assert.equal(
    hits.length,
    0,
    `push.js has ${hits.length} accented char(s) ${JSON.stringify([...new Set(hits)])} - product strings must be English`,
  );
});

// ---- Run the async probes ------------------------------------------------

for (const [name, body] of pending) {
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

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
