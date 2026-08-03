/**
 * The door `externally_connectable` opens, and how narrow it has to be.
 *
 * This extension holds a personal API token, a claude.ai session and host
 * permissions for both. Letting a web page talk to it is worth doing — a
 * desktop PWA next to it can wake the bridge before an agent turn — and an
 * extension that will do anything a page asks is a far worse bug than the one
 * it was opened to fix.
 *
 * Both halves are here: the extension's side refusing anything it was not
 * built for, and the PWA's side going quiet when there is nothing to talk to.
 * The second is not a nicety — on a phone there is no extension, ever, and an
 * enhancement that announces its own absence reads as a broken feature.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, join } from "node:path";

import {
  handleExternalMessage,
  isAllowedExternalSender,
  ALLOWED_EXTERNAL_ORIGINS,
  EXTERNAL_MESSAGE_TYPES,
  MSG_BRIDGE_STATUS,
  MSG_BRIDGE_ENSURE,
} from "../external_messages.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, "..", "..");

let passed = 0;
let failed = 0;
async function test(name, body) {
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

const PWA = { origin: "https://grooveos.app" };

function actions({ live = true } = {}) {
  const calls = { isLive: 0, ensure: 0 };
  return {
    calls,
    isLive: () => {
      calls.isLive++;
      return live;
    },
    ensure: async () => {
      calls.ensure++;
    },
  };
}

// ---------------------------------------------------------------------------
// The origin fence
// ---------------------------------------------------------------------------

await test("the PWA origin is accepted", () => {
  assert.equal(isAllowedExternalSender(PWA), true);
});

await test("an origin that merely looks right is refused", () => {
  // Every one of these starts or ends the way a prefix or suffix check would
  // want, and none of them is us. This is why the comparison is whole-string.
  for (const origin of [
    "https://grooveos.app.evil.test",
    "https://evil.grooveos.app",
    "https://grooveos.appx",
    "http://grooveos.app", // no TLS
    "https://grooveos.app:8443", // a different origin, by definition
    "https://GROOVEOS.APP",
    "https://xn--grooveos-app.test",
    "null",
    "",
  ]) {
    assert.equal(
      isAllowedExternalSender({ origin }),
      false,
      `${origin} must not be able to speak to the extension`,
    );
  }
});

await test("a sender with no origin is refused, not guessed at", () => {
  // Chrome sets `origin`; a page cannot. Deriving one from sender.url would
  // accept whatever a caller could talk Chrome into putting there.
  for (const sender of [
    null,
    undefined,
    {},
    { url: "https://grooveos.app/app/" },
    { origin: null },
    { origin: 7 },
  ]) {
    assert.equal(isAllowedExternalSender(sender), false);
  }
});

await test("a message from an unknown origin gets no reply at all", () => {
  const a = actions();
  const out = handleExternalMessage({
    message: { type: MSG_BRIDGE_ENSURE },
    sender: { origin: "https://evil.test" },
    actions: a,
  });
  assert.equal(out, null, "a refused message must not be answered");
  assert.equal(a.calls.ensure, 0, "and must not have run anything first");
  assert.equal(a.calls.isLive, 0);
});

// ---------------------------------------------------------------------------
// The closed set of types
// ---------------------------------------------------------------------------

await test("an unknown message type is refused", () => {
  const a = actions();
  for (const message of [
    { type: "XBRAIN_MINT_TOKEN" },
    { type: "SEND_TO_BRAIN", payload: {} },
    { type: "GET_ID_TOKEN" },
    { type: "DISCONNECT" },
    { type: "SIGNIN_GITHUB" },
    { type: "xbrain_bridge_ensure" }, // case matters
    { type: MSG_BRIDGE_ENSURE + " " },
  ]) {
    assert.equal(
      handleExternalMessage({ message, sender: PWA, actions: a }),
      null,
      `${message.type} must not be reachable from a web page`,
    );
  }
  assert.equal(a.calls.ensure, 0);
});

await test("the internal message surface is not reachable from the web", () => {
  // The types the popup uses mint tokens, read Google identity and disconnect
  // the account. None may be in the external set.
  for (const internal of [
    "GET_ID_TOKEN",
    "SEND_TO_BRAIN",
    "MINT_AND_CONNECT",
    "DISCONNECT",
    "LINK_GITHUB",
    "SIGNIN_GITHUB",
    "GET_CLAUDE_SESSION_INFO",
    "REFRESH_CLAUDE_SESSION",
    "REFRESH_TEAMS_MENU",
  ]) {
    assert.ok(
      !EXTERNAL_MESSAGE_TYPES.includes(internal),
      `${internal} must never be externally callable`,
    );
  }
  assert.deepEqual([...EXTERNAL_MESSAGE_TYPES].sort(), [
    MSG_BRIDGE_ENSURE,
    MSG_BRIDGE_STATUS,
  ].sort());
});

await test("a malformed message is refused", () => {
  const a = actions();
  for (const message of [null, undefined, "XBRAIN_BRIDGE_ENSURE", 7, [], [{ type: MSG_BRIDGE_ENSURE }], {}]) {
    assert.equal(handleExternalMessage({ message, sender: PWA, actions: a }), null);
  }
});

await test("extra fields on an accepted message change nothing", () => {
  // The actions take no parameters at all, so there is nothing a page can
  // steer. This asserts the property rather than the absence of a bug.
  const a = actions({ live: true });
  const out = handleExternalMessage({
    message: {
      type: MSG_BRIDGE_STATUS,
      user_sub: "github:someone-else",
      url: "wss://evil.test/ws",
      token: "xbt_stolen",
    },
    sender: PWA,
    actions: a,
  });
  assert.ok(out, "a known type from the right origin is still served");
  assert.equal(a.calls.ensure, 0, "a status query must not open anything");
});

// ---------------------------------------------------------------------------
// The two things it will do
// ---------------------------------------------------------------------------

await test("a status query reports the socket and opens nothing", async () => {
  const a = actions({ live: false });
  const reply = await handleExternalMessage({
    message: { type: MSG_BRIDGE_STATUS },
    sender: PWA,
    actions: a,
  });
  assert.deepEqual(reply, { ok: true, live: false });
  assert.equal(a.calls.ensure, 0);
});

await test("an ensure brings the socket up, then reports", async () => {
  const a = actions({ live: true });
  const reply = await handleExternalMessage({
    message: { type: MSG_BRIDGE_ENSURE },
    sender: PWA,
    actions: a,
  });
  assert.equal(a.calls.ensure, 1);
  assert.deepEqual(reply, { ok: true, live: true });
});

await test("ensure is idempotent — repeating it is harmless", async () => {
  const a = actions({ live: true });
  for (let i = 0; i < 3; i++) {
    await handleExternalMessage({
      message: { type: MSG_BRIDGE_ENSURE },
      sender: PWA,
      actions: a,
    });
  }
  assert.equal(a.calls.ensure, 3, "each call is served, and each is a no-op on a healthy socket");
});

// ---------------------------------------------------------------------------
// The manifest and the wiring
// ---------------------------------------------------------------------------

await test("the manifest names exactly one origin, with no host wildcard", () => {
  const manifest = JSON.parse(
    readFileSync(join(REPO_ROOT, "chrome-extension", "manifest.json"), "utf8"),
  );
  const matches = manifest.externally_connectable.matches;
  assert.deepEqual(matches, ["https://grooveos.app/*"]);
  for (const pattern of matches) {
    const host = pattern.replace(/^https?:\/\//, "").split("/")[0];
    assert.ok(!host.includes("*"), `${pattern} wildcards the HOST — any subdomain could talk to us`);
    assert.ok(pattern.startsWith("https://"), `${pattern} is not TLS`);
    assert.ok(
      ALLOWED_EXTERNAL_ORIGINS.includes(`https://${host}`),
      `${pattern} is in the manifest but not in the code's allow-list — the ` +
        "manifest lets it connect and the handler must independently agree",
    );
  }
});

await test("background.js delegates the decision rather than inlining one", () => {
  const bg = readFileSync(join(REPO_ROOT, "chrome-extension", "background.js"), "utf8");
  assert.ok(bg.includes("onMessageExternal"), "the external listener must exist");
  assert.ok(
    bg.includes("handleExternalMessage"),
    "background.js must use the tested handler, not a second inline check",
  );
  // The internal listener's own guard must survive: it rejects anything not
  // from this extension, and external senders have no id.
  assert.ok(
    bg.includes("sender.id !== chrome.runtime.id"),
    "the internal message listener must still refuse foreign senders",
  );
});

// ---------------------------------------------------------------------------
// The PWA side: absence is not an error
// ---------------------------------------------------------------------------

const bridgeLink = await import(
  pathToFileURL(join(REPO_ROOT, "app-site", "app", "bridge_link.js")).href
);

/** Capture anything written to the console while a body runs. */
async function withQuietConsole(body) {
  const noise = [];
  const original = { log: console.log, warn: console.warn, error: console.error };
  for (const level of ["log", "warn", "error"]) {
    console[level] = (...args) => noise.push([level, args.join(" ")]);
  }
  try {
    return { result: await body(), noise };
  } finally {
    Object.assign(console, original);
  }
}

await test("no extension: the PWA reports unavailable and says nothing", async () => {
  // A phone. This is the ordinary case, not a failure.
  for (const env of [undefined, {}, { runtime: {} }, { runtime: { sendMessage: null } }]) {
    const { result, noise } = await withQuietConsole(() => bridgeLink.ensureBridge(env));
    assert.deepEqual(result, { available: false, live: false });
    assert.deepEqual(noise, [], "an absent extension must produce no console output");
  }
});

await test("a rejected send is silent too, and lastError is READ", async () => {
  // An unread chrome.runtime.lastError is printed by Chrome itself — on every
  // page load, for everyone without the extension. Reading it is the fix.
  let read = false;
  const env = {
    runtime: {
      get lastError() {
        read = true;
        return { message: "Could not establish connection." };
      },
      sendMessage: (_id, _msg, cb) => cb(undefined),
    },
  };
  const { result, noise } = await withQuietConsole(() => bridgeLink.ensureBridge(env));
  assert.deepEqual(result, { available: false, live: false });
  assert.equal(read, true, "runtime.lastError must be read, or Chrome logs it for us");
  assert.deepEqual(noise, []);
});

await test("a sendMessage that throws is an absence, not a crash", async () => {
  const env = {
    runtime: {
      lastError: undefined,
      sendMessage: () => {
        throw new Error("Invalid extension id");
      },
    },
  };
  const { result, noise } = await withQuietConsole(() => bridgeLink.ensureBridge(env));
  assert.deepEqual(result, { available: false, live: false });
  assert.deepEqual(noise, []);
});

await test("a garbled reply is an absence", async () => {
  for (const reply of [undefined, null, {}, { ok: false }, { ok: "true", live: true }, "yes"]) {
    const env = {
      runtime: { lastError: undefined, sendMessage: (_i, _m, cb) => cb(reply) },
    };
    assert.deepEqual(await bridgeLink.ensureBridge(env), { available: false, live: false });
  }
});

await test("a real reply is passed through", async () => {
  const sent = [];
  const env = {
    runtime: {
      lastError: undefined,
      sendMessage: (id, msg, cb) => {
        sent.push([id, msg]);
        cb({ ok: true, live: true });
      },
    },
  };
  assert.deepEqual(await bridgeLink.ensureBridge(env), { available: true, live: true });
  assert.equal(sent[0][0], bridgeLink.XBRAIN_EXTENSION_ID);
  assert.deepEqual(sent[0][1], { type: MSG_BRIDGE_ENSURE });
});

await test("the PWA never sends a type the extension does not accept", () => {
  for (const type of [bridgeLink.MSG_BRIDGE_ENSURE, bridgeLink.MSG_BRIDGE_STATUS]) {
    assert.ok(
      EXTERNAL_MESSAGE_TYPES.includes(type),
      `${type} is sent by the PWA and refused by the extension — the two lists have drifted`,
    );
  }
});

await test("the PWA builds no bridge of its own", () => {
  // A web page cannot execute against claude.ai with somebody's cookies. That
  // boundary is why the extension exists, and reimplementing it here would be a
  // security hole where a feature was wanted.
  // Comments stripped first: the file EXPLAINS at length why it does not do
  // this, and a check that banned the words would forbid the explanation.
  const src = readFileSync(join(REPO_ROOT, "app-site", "app", "bridge_link.js"), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/.*$/gm, "$1");
  assert.ok(!src.includes("claude.ai"), "the PWA must not reach for claude.ai");
  assert.ok(!/new WebSocket\(/.test(src), "the PWA must not open a bridge socket");
  assert.ok(!src.includes("bridge.grooveos.app"), "the PWA must not talk to session-bridge");
  assert.ok(!/fetch\s*\(/.test(src), "the PWA's nudge makes no network call of its own");
});

await test("chat.js nudges the bridge without depending on the answer", () => {
  const chatJs = readFileSync(join(REPO_ROOT, "app-site", "app", "chat.js"), "utf8");
  assert.ok(chatJs.includes("ensureBridge()"), "the nudge must happen");
  assert.ok(
    !/await\s+ensureBridge\(/.test(chatJs),
    "a nudge must never delay a send — on a phone it can only ever be a no-op",
  );
  assert.ok(
    !/ensureBridge\([^)]*\)\s*\.then/.test(chatJs),
    "nothing may branch on the nudge's result — the SERVER says whether the subscription is connected",
  );
});

await test("bridge_link.js is precached like every other shipped file", () => {
  const sw = readFileSync(join(REPO_ROOT, "app-site", "app", "sw.js"), "utf8");
  assert.ok(
    sw.includes('"/app/bridge_link.js"'),
    "a shipped file missing from SHELL is broken offline with no error to explain why",
  );
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
