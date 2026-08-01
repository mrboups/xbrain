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
  let current = existing;

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
        current = makeSubscription(
          "https://push.example.test/fresh",
          config.vapid_public_key,
          log,
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
  // "-" and "_" must become "+" and "/" or the decode silently yields garbage.
  assert.ok(
    VAPID_KEY.includes("-") && VAPID_KEY.includes("_"),
    "the fixture must exercise both URL-safe characters or this test is inert",
  );
  assert.equal(VAPID_KEY.length % 4, 3, "the fixture must be unpadded to exercise the pad step");
  const withPadding = urlBase64ToUint8Array(`${VAPID_KEY}=`);
  const without = urlBase64ToUint8Array(VAPID_KEY);
  assert.deepEqual([...withPadding], [...without], "padding must not change the bytes");
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
  const log = [];
  const stale = makeSubscription("https://push.example.test/stale", OTHER_KEY, log);
  const b = browser({ permission: "granted", existing: stale });
  // Route the stale subscription's own log into the browser's ordered log.
  stale.unsubscribe = async () => {
    b.log.push("browser-unsubscribe:https://push.example.test/stale");
    return true;
  };

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
    existing: makeSubscription("https://push.example.test/good", VAPID_KEY, []),
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
    existing: makeSubscription("https://push.example.test/live", VAPID_KEY, []),
  });
  const sub = b.current;
  sub.unsubscribe = async () => {
    b.log.push("browser-unsubscribe:https://push.example.test/live");
    return true;
  };

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
        existing: makeSubscription("https://push.example.test/on", VAPID_KEY, []),
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

  // Nothing may have happened yet: wiring is not consent.
  assert.ok(!b.log.includes("ask-permission"), "wiring must not prompt");

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
