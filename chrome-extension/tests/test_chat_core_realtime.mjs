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

import {
  connectRealtime,
  createConnectionStatus,
  createPublicationDeduper,
  isAlreadySubscribed,
  publicationKey,
  ALREADY_SUBSCRIBED_CODE,
  CONNECTION_RECONNECTING,
  CONNECTION_OFFLINE,
  CONNECTING_GRACE_MS,
} from "../../packages/chat-core/realtime.js";

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
  // join/leave are presence and nothing else, so a surface without a badge must
  // not ship them. `subscribed` is NO LONGER in that set: it now decides which
  // of the two delivery paths owns a channel, which every surface needs — so it
  // is wired unconditionally and is asserted below rather than forbidden here.
  for (const evt of ["join", "leave"]) {
    assert.equal(
      plain.handlers[evt],
      undefined,
      `a surface with no presence badge must not wire "${evt}"`,
    );
  }
  assert.ok(
    plain.handlers.subscribed && plain.handlers.subscribed.length === 1,
    "`subscribed` is wired for every surface — it is what marks the channel as delivered by this path",
  );

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
  for (const evt of ["join", "leave"]) {
    assert.equal(wired.handlers[evt].length, 1);
    wired.handlers[evt][0]();
  }
  // `subscribed` carries two listeners now: delivery ownership, then presence.
  assert.equal(wired.handlers.subscribed.length, 2);
  wired.handlers.subscribed.forEach((fn) => fn({}));
  assert.equal(presence, 3, "presence still recomputes on join, leave and subscribed");
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

// ---------------------------------------------------------------------------
// Connection status — the banner is a STATE, never the last incident seen
// ---------------------------------------------------------------------------
//
// The bug: "Reconnecting..." stayed on screen permanently on a phone whose
// connection was fine — messages sent, history loaded, frames arrived. The
// banner was wired to `error`, Centrifuge emits that for transient things on a
// healthy socket, and only a NEW `connected` cleared it. A socket that never
// dropped never re-emits `connected`, so the claim was permanent.

/** A controller with hand-driven timers, so the grace window costs no wall time. */
function statusHarness({ graceMs = CONNECTING_GRACE_MS } = {}) {
  const rendered = [];
  let nextId = 1;
  const timers = new Map();
  const status = createConnectionStatus({
    render: (text) => rendered.push(text),
    graceMs,
    setTimer: (fn) => {
      const id = nextId++;
      timers.set(id, fn);
      return id;
    },
    clearTimer: (id) => timers.delete(id),
  });
  return {
    status,
    rendered,
    /** Fire every timer that is still armed. */
    tick() {
      const due = [...timers.values()];
      timers.clear();
      due.forEach((fn) => fn());
    },
    pending: () => timers.size,
  };
}

await test("an error while connected leaves the banner clear", () => {
  // THE regression. There is no `error` input on the machine at all, so this is
  // asserted the only way it can be: connected, then anything at all happens
  // that is not a state change, and the banner is still clear.
  const h = statusHarness();
  h.status.connecting();
  h.status.connected();
  h.tick(); // any timer armed before `connected` must have been cancelled
  assert.equal(h.status.current(), null, "a healthy socket shows no banner");
  assert.ok(
    !h.rendered.includes(CONNECTION_RECONNECTING),
    "an incident on a healthy socket must never render the reconnecting banner",
  );
});

await test("the status machine has no input an incident could use", () => {
  // Structural version of the same rule: if there is nowhere to route an error,
  // no future edit can wire one in by accident.
  const h = statusHarness();
  const surface = Object.keys(h.status);
  assert.deepEqual(
    surface.sort(),
    ["connected", "connecting", "current", "disconnected", "dispose", "offline"],
    "the connection status exposes states only — an `error` method would be a way back to the bug",
  );
});

await test("a real disconnect says live updates are off", () => {
  const h = statusHarness();
  h.status.connected();
  h.status.disconnected();
  assert.equal(h.status.current(), CONNECTION_OFFLINE);
  assert.ok(
    /reload/i.test(CONNECTION_OFFLINE),
    "a terminal disconnect must say what would make it work again",
  );
});

await test("a normal connect never flashes the banner", () => {
  // connecting -> connected inside the grace window. Rendering immediately
  // would flash "Reconnecting..." on every single page load.
  const h = statusHarness();
  h.status.connecting();
  assert.equal(h.status.current(), null, "nothing is claimed during the grace window");
  h.status.connected();
  h.tick();
  assert.deepEqual(
    h.rendered,
    [],
    "a clean connect touches the banner zero times — it was already clear, and the reconnecting text never gets written",
  );
  assert.equal(h.status.current(), null);
});

await test("a connecting state that persists is reported", () => {
  const h = statusHarness();
  h.status.connecting();
  h.tick(); // the grace window elapses with no `connected`
  assert.equal(h.status.current(), CONNECTION_RECONNECTING);
});

await test("a flapping connection does not push the message further away", () => {
  // Each `connecting` re-arming the timer would mean a socket retrying every
  // second never says anything at all.
  const h = statusHarness();
  h.status.connecting();
  h.status.connecting();
  h.status.connecting();
  assert.equal(h.pending(), 1, "repeat connecting must not stack or reset the timer");
  h.tick();
  assert.equal(h.status.current(), CONNECTION_RECONNECTING);
});

await test("recovery clears the banner", () => {
  const h = statusHarness();
  h.status.connecting();
  h.tick();
  assert.equal(h.status.current(), CONNECTION_RECONNECTING);
  h.status.connected();
  assert.equal(h.status.current(), null);
});

await test("the same state twice renders once", () => {
  const h = statusHarness();
  h.status.disconnected();
  h.status.disconnected();
  h.status.connected();
  h.status.connected();
  assert.deepEqual(
    h.rendered,
    [CONNECTION_OFFLINE, null],
    "a repeated state must not repaint — the banner is not an event log",
  );
});

await test("connecting and disconnected are wired to the client", async () => {
  const record = makeRecord();
  const seen = [];
  const handle = await connectRealtime({
    Centrifuge: makeCentrifuge(record),
    api,
    getUserSub: () => "user-1",
    onTeamPublication: () => {},
    onUserPublication: () => {},
    onConnecting: () => seen.push("connecting"),
    onConnected: () => seen.push("connected"),
    onDisconnected: () => seen.push("disconnected"),
  });
  const inst = handle.centrifuge;
  for (const evt of ["connecting", "connected", "disconnected"]) {
    assert.ok(
      Array.isArray(inst.handlers[evt]) && inst.handlers[evt].length > 0,
      `realtime.js must wire "${evt}" — without it a surface can only see incidents`,
    );
    inst.handlers[evt].forEach((fn) => fn({}));
  }
  assert.deepEqual(seen, ["connecting", "connected", "disconnected"]);
});

await test("the PWA drives the banner from state and not from error", () => {
  const chatJs = readFileSync(join(REPO_ROOT, "app-site", "app", "chat.js"), "utf8");
  assert.ok(
    !/onError:\s*\(\)\s*=>\s*setConnectionBanner/.test(chatJs),
    "onError must not write the banner — that is the permanent-Reconnecting bug",
  );
  assert.ok(
    !/onError[^\n]*Reconnecting/.test(chatJs),
    "no reconnecting text may be reachable from an error callback",
  );
  for (const cb of ["onConnecting", "onConnected", "onDisconnected"]) {
    assert.ok(chatJs.includes(cb), `chat.js must react to ${cb}`);
  }
  assert.ok(
    chatJs.includes("createConnectionStatus"),
    "chat.js must use the shared state machine rather than re-deriving one",
  );
});

// ---------------------------------------------------------------------------
// Delivery paths — the bug that made a phone stop receiving anything
// ---------------------------------------------------------------------------
//
// POST /v1/me/centrifugo-token returns a `channels` claim, so Centrifugo
// subscribes the connection SERVER-SIDE at connect time. The client-side
// subscribe that followed was answered 105 "already subscribed", that
// Subscription never reached the subscribed state, and its `publication`
// handler therefore never fired. Publications arrived on the CLIENT-level
// event, which nothing was listening to.
//
// On the owner's iPhone that read as: sending works (HTTP), and nothing anyone
// else writes ever appears (websocket), until a reload. Centrifugo logged 105
// for every channel of both accounts.

/** Connect, claim a team, and hand back the fakes plus what got routed. */
async function connected({ teamId = "team-1", userSub = "user-1" } = {}) {
  const record = makeRecord();
  const team = [];
  const user = [];
  const handle = await connectRealtime({
    Centrifuge: makeCentrifuge(record),
    api,
    getUserSub: () => userSub,
    onTeamPublication: (d) => team.push(d),
    onUserPublication: (d) => user.push(d),
  });
  handle.subscribeTeam(teamId);
  const inst = handle.centrifuge;
  const fire = (evt, ...args) => (inst.handlers[evt] || []).forEach((f) => f(...args));
  const subFor = (channel) => inst.subscriptions.find((s) => s.channel === channel);
  const fireSub = (channel, evt, ...args) => {
    const s = subFor(channel);
    (s.handlers[evt] || []).forEach((f) => f(...args));
  };
  return { handle, inst, team, user, fire, fireSub, subFor };
}

const MSG = (id) => ({ type: "message", message: { id, content: "hi" } });

await test("a publication delivered ONLY on the client-level event reaches the handler", async () => {
  // THE regression, exactly: the channel is subscribed server-side, the
  // client-side subscribe is refused 105, and the message must still arrive.
  const c = await connected();
  c.fireSub("team:team-1", "error", { error: { code: ALREADY_SUBSCRIBED_CODE } });
  c.fire("publication", { channel: "team:team-1", data: MSG("m1") });
  assert.equal(c.team.length, 1, "a server-side publication must reach onTeamPublication");
  assert.equal(c.team[0].message.id, "m1");
});

await test("the user channel works the same way", async () => {
  const c = await connected();
  c.fireSub("user:user-1", "error", { error: { code: ALREADY_SUBSCRIBED_CODE } });
  c.fire("publication", { channel: "user:user-1", data: { type: "nudge_open" } });
  assert.equal(c.user.length, 1);
});

await test("the per-subscription path still delivers when it does subscribe", async () => {
  // A deployment whose token does NOT carry the channel takes this path, and
  // presence rides on the Subscription object either way.
  const c = await connected();
  c.fireSub("team:team-1", "subscribed", {});
  c.fireSub("team:team-1", "publication", { data: MSG("m2") });
  assert.equal(c.team.length, 1);
});

await test("the same message on both paths renders once", async () => {
  const c = await connected();
  c.fireSub("team:team-1", "subscribed", {});
  c.fireSub("team:team-1", "publication", { data: MSG("dup") });
  c.fire("publication", { channel: "team:team-1", data: MSG("dup") });
  assert.equal(c.team.length, 1, "a message arriving on both paths must be handled once");
});

await test("a subscribed channel owns its delivery, so the client-level event stands down", async () => {
  const c = await connected();
  c.fireSub("team:team-1", "subscribed", {});
  c.fire("publication", { channel: "team:team-1", data: { type: "agent_stream_chunk", message_id: "a", delta: "x" } });
  assert.equal(c.team.length, 0, "the owning path is the only one that delivers");
});

await test("chunks are never deduplicated by content", async () => {
  // Two identical deltas in one answer are ordinary (" the" twice). Collapsing
  // them would silently corrupt the reply, so a chunk gets no dedupe key at all
  // and is protected by path ownership instead.
  const c = await connected();
  c.fireSub("team:team-1", "error", { error: { code: ALREADY_SUBSCRIBED_CODE } });
  const chunk = { type: "agent_stream_chunk", message_id: "a", delta: " the" };
  c.fire("publication", { channel: "team:team-1", data: chunk });
  c.fire("publication", { channel: "team:team-1", data: { ...chunk } });
  assert.equal(c.team.length, 2, "identical deltas must both be appended");
  assert.equal(publicationKey("team:team-1", chunk), null, "a chunk has no stable identity");
});

await test("another team's frames never reach the open thread", async () => {
  // The token grants EVERY team the person belongs to, so the client-level
  // event carries all of them. Routing on the "team:" prefix alone would paint
  // one team's messages into another's thread.
  const c = await connected({ teamId: "team-1" });
  c.fireSub("team:team-1", "error", { error: { code: ALREADY_SUBSCRIBED_CODE } });
  c.fire("publication", { channel: "team:other-team", data: MSG("elsewhere") });
  assert.equal(c.team.length, 0, "only the active team channel may be rendered");
});

await test("a DELETION from another team never reaches the open thread either", async () => {
  // Stated for this frame type rather than inherited from the one above. A
  // deletion is the frame with the worst failure mode if the channel guard is
  // ever special-cased away: it takes something OFF the screen, and the frame
  // that did it came from a team the person is not currently looking at.
  const c = await connected({ teamId: "team-1" });
  c.fireSub("team:team-1", "error", { error: { code: ALREADY_SUBSCRIBED_CODE } });
  c.fire("publication", {
    channel: "team:other-team",
    data: { type: "message_deleted", message_id: "m1", scope: "message" },
  });
  assert.equal(c.team.length, 0, "only the active team channel may remove a row");
  c.fire("publication", {
    channel: "team:team-1",
    data: { type: "message_deleted", message_id: "m1", scope: "message" },
  });
  assert.equal(c.team.length, 1, "the open team's deletion must still arrive");
  assert.equal(c.team[0].message_id, "m1");
});

await test("a re-delivered deletion is dropped, not applied twice", async () => {
  const c = await connected({ teamId: "team-1" });
  c.fireSub("team:team-1", "error", { error: { code: ALREADY_SUBSCRIBED_CODE } });
  const frame = { type: "message_deleted", message_id: "m7", scope: "message" };
  c.fire("publication", { channel: "team:team-1", data: frame });
  c.fire("publication", { channel: "team:team-1", data: frame });
  assert.equal(c.team.length, 1, "the id-keyed deduper covers this frame too");
});

await test("a team switch stops the previous team being routed", async () => {
  const c = await connected({ teamId: "team-1" });
  c.handle.subscribeTeam("team-2");
  c.fire("publication", { channel: "team:team-1", data: MSG("stale") });
  assert.equal(c.team.length, 0, "the team left behind must stop delivering");
  c.fire("publication", { channel: "team:team-2", data: MSG("fresh") });
  assert.equal(c.team.length, 1);
});

await test("a foreign user channel is ignored", async () => {
  const c = await connected({ userSub: "user-1" });
  c.fire("publication", { channel: "user:someone-else", data: { type: "nudge_open" } });
  assert.equal(c.user.length, 0);
});

await test("105 is not an error and never reaches the surface", async () => {
  // It fires once per channel per connect for every user. A client that counts
  // it as a failure is permanently in a false failure state — which is what
  // pinned "Reconnecting..." on screen.
  const record = makeRecord();
  const errors = [];
  const handle = await connectRealtime({
    Centrifuge: makeCentrifuge(record),
    api,
    getUserSub: () => "user-1",
    onTeamPublication: () => {},
    onUserPublication: () => {},
    onError: (e) => errors.push(e),
  });
  (handle.centrifuge.handlers.error || []).forEach((f) =>
    f({ error: { code: ALREADY_SUBSCRIBED_CODE, message: "already subscribed" } }),
  );
  assert.deepEqual(errors, [], "105 must not be reported as a failure");

  assert.ok(isAlreadySubscribed({ error: { code: 105 } }), "nested shape");
  assert.ok(isAlreadySubscribed({ code: 105 }), "flat shape");
  assert.ok(!isAlreadySubscribed({ error: { code: 109 } }), "a real error is still an error");
  assert.ok(!isAlreadySubscribed(null));
});

await test("a genuine error still reaches the surface", async () => {
  const record = makeRecord();
  const errors = [];
  const handle = await connectRealtime({
    Centrifuge: makeCentrifuge(record),
    api,
    getUserSub: () => "user-1",
    onTeamPublication: () => {},
    onUserPublication: () => {},
    onError: (e) => errors.push(e),
  });
  (handle.centrifuge.handlers.error || []).forEach((f) => f({ error: { code: 109, message: "token expired" } }));
  assert.equal(errors.length, 1);
});

await test("the deduper is bounded and order-preserving", () => {
  const seen = createPublicationDeduper(2);
  assert.equal(seen("a"), false);
  assert.equal(seen("a"), true);
  assert.equal(seen("b"), false);
  assert.equal(seen("c"), false); // evicts "a"
  assert.equal(seen("a"), false, "the oldest key is forgotten, not remembered forever");
  assert.equal(seen(null), false, "a frame with no identity is never treated as a duplicate");
});

await test("publicationKey names only frames that carry a server-assigned id", () => {
  assert.equal(publicationKey("team:t", MSG("m1")), "team:t|message|m1");
  assert.equal(
    publicationKey("team:t", { type: "agent_stream_start", message_id: "x" }),
    "team:t|agent_stream_start|x",
  );
  assert.equal(publicationKey("team:t", { type: "agent_stream_chunk", message_id: "x" }), null);
  assert.equal(publicationKey("team:t", { type: "message", message: {} }), null);
  assert.equal(publicationKey("team:t", null), null);
});

await test("both surfaces get this fix — neither forks the module", () => {
  for (const rel of [
    join("chrome-extension", "chat_core", "realtime.js"),
    join("app-site", "app", "chat_core", "realtime.js"),
  ]) {
    const copy = readFileSync(join(REPO_ROOT, rel), "utf8");
    assert.ok(
      copy.includes('centrifuge.on("publication"'),
      `${rel} must carry the client-level publication handler — the extension may only have been working by timing luck`,
    );
  }
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
