/**
 * Tests for the shared Centrifugo wiring (Phase 27, Plan 27-02, D-27-03).
 *
 * Subject: packages/chat-core/realtime.js — the SOURCE, not a generated copy.
 *
 * The load-bearing claim is that the websocket URL is structurally impossible to
 * hardcode. Two assertions carry it, and both must stay:
 *   (a) BEHAVIOUR — the first argument handed to the Centrifuge constructor is
 *       byte-for-byte the `ws_url` the stubbed POST /v1/me/centrifugo-token
 *       returned. A fake Centrifuge records it; the test compares it to the
 *       stub's value, not to a pattern;
 *   (b) STRUCTURE — the module file contains no scheme literal at all, so there
 *       is nothing for a tampered static file to redirect a credentialed socket
 *       to (T-27-02-03).
 *
 * Also locked: a falsy Centrifuge returns null instead of throwing (a missing
 * vendored client must not take the whole chat down), the user channel is claimed
 * once per connection, a team switch tears the previous team subscription down
 * before creating the next, presence handlers are wired ONLY when the surface
 * asked for them, and the extension consumes this module instead of forking it.
 *
 * Pure node test — no browser, no network, no deps. Picked up by run_tests.mjs.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { connectRealtime } from "../../packages/chat-core/realtime.js";

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

// ---------------------------------------------------------------------------
// Fakes: a Centrifuge constructor that records what it was built with, and an
// api client whose centrifugoToken() is the ONLY source of the ws url.
// ---------------------------------------------------------------------------

const WS_URL = "wss://example.test/connection/websocket";

function makeCentrifuge(record) {
  return function FakeCentrifuge(url, options) {
    record.constructed.push([url, options]);
    const inst = {
      handlers: {},
      subscriptions: [],
      connected: false,
      disconnected: 0,
      on(type, fn) {
        (this.handlers[type] = this.handlers[type] || []).push(fn);
      },
      newSubscription(channel) {
        const sub = {
          channel,
          handlers: {},
          subscribed: false,
          unsubscribed: 0,
          on(type, fn) {
            (this.handlers[type] = this.handlers[type] || []).push(fn);
            return this;
          },
          subscribe() {
            this.subscribed = true;
          },
          unsubscribe() {
            this.unsubscribed++;
            this.subscribed = false;
          },
        };
        this.subscriptions.push(sub);
        record.subscriptions.push(sub);
        return sub;
      },
      removeSubscription(sub) {
        record.removed.push(sub.channel);
        this.subscriptions = this.subscriptions.filter((s) => s !== sub);
      },
      connect() {
        this.connected = true;
      },
      disconnect() {
        this.disconnected++;
        this.connected = false;
      },
    };
    record.instances.push(inst);
    return inst;
  };
}

function makeRecord() {
  return { constructed: [], instances: [], subscriptions: [], removed: [] };
}

const api = {
  tokenCalls: 0,
  async centrifugoToken() {
    api.tokenCalls++;
    return { token: "signed.client.jwt", ws_url: WS_URL };
  },
};

// ---- (a) the ws url is the API's, not a constant ----

await test("connectRealtime passes tokenInfo.ws_url as the first Centrifuge argument", async () => {
  const record = makeRecord();
  const handle = await connectRealtime({
    Centrifuge: makeCentrifuge(record),
    api,
    getUserSub: () => "gh|42",
    onTeamPublication: () => {},
    onUserPublication: () => {},
  });
  assert.ok(handle, "a connection handle must be returned");
  assert.equal(record.constructed.length, 1);
  assert.equal(
    record.constructed[0][0],
    WS_URL,
    "the socket URL must be exactly the ws_url the API returned — never a client constant",
  );
  assert.deepEqual(record.constructed[0][1], { token: "signed.client.jwt" });
  assert.ok(record.instances[0].connected, "connect() must be called");
});

// ---- (b) the module holds no URL to hardcode ----

await test("realtime.js contains no scheme literal (D-27-03, T-27-02-03)", () => {
  const src = readFileSync(
    join(REPO_ROOT, "packages", "chat-core", "realtime.js"),
    "utf8",
  );
  const hits = src.match(/wss?:\/\/|https?:\/\//g) || [];
  assert.equal(
    hits.length,
    0,
    `realtime.js must contain no URL literal — found ${JSON.stringify(hits)}. ` +
      "A hardcoded socket URL would pin every surface to one deployment and give a " +
      "tampered static file somewhere to redirect a credentialed socket.",
  );
  assert.ok(
    /centrifugoToken\(\)/.test(src) && /ws_url/.test(src),
    "the URL must come from api.centrifugoToken()'s ws_url",
  );
});

// ---- fail-soft ----

await test("connectRealtime returns null when opts.Centrifuge is undefined", async () => {
  const errs = [];
  const realError = console.error;
  console.error = (...a) => errs.push(a.join(" "));
  try {
    const handle = await connectRealtime({
      Centrifuge: undefined,
      api,
      getUserSub: () => "gh|42",
      onTeamPublication: () => {},
      onUserPublication: () => {},
    });
    assert.equal(handle, null, "a missing client must fail soft, not throw");
  } finally {
    console.error = realError;
  }
  assert.ok(errs.length > 0, "the missing client must be reported, not swallowed");
});

// ---- channels ----

await test("the user channel is claimed once per connection, as user:<sub>", async () => {
  const record = makeRecord();
  const frames = [];
  const handle = await connectRealtime({
    Centrifuge: makeCentrifuge(record),
    api,
    getUserSub: () => "gh|42",
    onTeamPublication: () => {},
    onUserPublication: (d) => frames.push(d),
  });
  const userSubs = record.subscriptions.filter((s) => s.channel.startsWith("user:"));
  assert.equal(userSubs.length, 1);
  assert.equal(userSubs[0].channel, "user:gh|42");
  assert.ok(userSubs[0].subscribed);
  // Two team switches must not touch it.
  handle.subscribeTeam("t1");
  handle.subscribeTeam("t2");
  assert.equal(
    record.subscriptions.filter((s) => s.channel.startsWith("user:")).length,
    1,
    "a team switch must never re-subscribe the user channel",
  );
  userSubs[0].handlers.publication[0]({ data: { type: "open_url" } });
  assert.deepEqual(frames, [{ type: "open_url" }]);
});

await test("subscribeTeam tears down the previous team subscription before creating the next", async () => {
  const record = makeRecord();
  const frames = [];
  const handle = await connectRealtime({
    Centrifuge: makeCentrifuge(record),
    api,
    getUserSub: () => "gh|42",
    onTeamPublication: (d) => frames.push(d),
    onUserPublication: () => {},
  });
  const first = handle.subscribeTeam("team-a");
  assert.equal(first.channel, "team:team-a");
  const second = handle.subscribeTeam("team-b");
  assert.equal(second.channel, "team:team-b");
  assert.equal(first.unsubscribed, 1, "the prior team sub must be unsubscribed");
  assert.deepEqual(record.removed, ["team:team-a"]);
  second.handlers.publication[0]({ data: { type: "message", message: { id: "x" } } });
  assert.deepEqual(frames, [{ type: "message", message: { id: "x" } }]);
});

await test("presence handlers are wired ONLY when onPresenceChange is supplied", async () => {
  const withoutRec = makeRecord();
  const without = await connectRealtime({
    Centrifuge: makeCentrifuge(withoutRec),
    api,
    getUserSub: () => "gh|42",
    onTeamPublication: () => {},
    onUserPublication: () => {},
  });
  const plain = without.subscribeTeam("t");
  for (const evt of ["join", "leave", "subscribed"]) {
    assert.equal(
      plain.handlers[evt],
      undefined,
      `a surface with no presence badge must not wire "${evt}"`,
    );
  }

  const withRec = makeRecord();
  let presence = 0;
  const withPresence = await connectRealtime({
    Centrifuge: makeCentrifuge(withRec),
    api,
    getUserSub: () => "gh|42",
    onTeamPublication: () => {},
    onUserPublication: () => {},
    onPresenceChange: () => presence++,
  });
  const wired = withPresence.subscribeTeam("t");
  for (const evt of ["join", "leave", "subscribed"]) {
    assert.equal(wired.handlers[evt].length, 1);
    wired.handlers[evt][0]();
  }
  assert.equal(presence, 3);
});

await test("a prior connection handed in as `previous` is disconnected first", async () => {
  const record = makeRecord();
  const Centrifuge = makeCentrifuge(record);
  const first = await connectRealtime({
    Centrifuge,
    api,
    getUserSub: () => "gh|42",
    onTeamPublication: () => {},
    onUserPublication: () => {},
  });
  await connectRealtime({
    Centrifuge,
    api,
    getUserSub: () => "gh|42",
    onTeamPublication: () => {},
    onUserPublication: () => {},
    previous: first,
  });
  assert.equal(
    first.centrifuge.disconnected,
    1,
    "the stale instance must be disconnected so its subscriptions die with it",
  );
  assert.equal(record.constructed.length, 2);
});

// ---- anti-fork: the extension consumes this module ----

const popupJs = readFileSync(join(REPO_ROOT, "chrome-extension", "popup.js"), "utf8");

await test("anti-fork: popup.js imports connectRealtime and builds no Centrifuge of its own", () => {
  assert.ok(
    /import\s*\{[^}]*connectRealtime[^}]*\}\s*from\s*"\.\/chat_core\/realtime\.js"/.test(
      popupJs,
    ),
    "popup.js must import connectRealtime from ./chat_core/realtime.js (D-27-04)",
  );
  assert.ok(
    !popupJs.includes("new Centrifuge("),
    "popup.js must not construct a Centrifuge — the shared module owns the connection and its URL",
  );
  assert.ok(
    !/async function connectCentrifugo/.test(popupJs),
    "popup.js must not keep its own connectCentrifugo() — that is the fork D-27-04 forbids",
  );
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
