/**
 * Centrifugo connection + channel subscriptions for the team chat
 * (Phase 27, D-27-03 / D-27-04).
 *
 * THE SOCKET URL IS NEVER A CONSTANT. The server owns it and hands it back on
 * POST /v1/me/centrifugo-token as `ws_url`, alongside the signed client token.
 * A URL baked in here would silently pin every surface to one deployment, and a
 * tampered static file could repoint a credentialed socket at another host
 * (T-27-02-03). This module therefore contains no scheme literal at all — a grep
 * and a test both assert that, so the rule survives the next edit.
 *
 * The Centrifuge constructor is INJECTED too: each surface loads its own vendored
 * client and passes it in, so nothing here reaches for an ambient global.
 *
 * Channel model (Centrifugo enforces the token's `channels` claim server-side, so
 * a client-built name for a foreign team is rejected at subscribe time):
 *   team:<team_id>          — chat messages + agent streams, re-created per switch
 *   user:<source_user_id>   — personal events (nudges, catch-me-up), once per
 *                             connection and untouched by team switches
 *
 * Used by:
 *   - chrome-extension/popup.js (via chrome-extension/chat_core/)
 *   - app-site/app/ — the PWA (via app-site/app/chat_core/)
 *   - chrome-extension/tests/test_chat_core_realtime.mjs
 */

// ---------- Where publications actually arrive ----------
//
// POST /v1/me/centrifugo-token returns a `channels` claim, so Centrifugo
// subscribes the connection SERVER-SIDE at connect time. By the time this module
// called `subscription.subscribe()` the channel was already live, Centrifugo
// answered 105 "already subscribed", and that Subscription never reached the
// subscribed state — so its `publication` handler never fired. Publications for
// a server-side subscription are delivered on the CLIENT-level `publication`
// event, which nothing here was listening to.
//
// The symptom was not a missing feature. It was a phone on which sending worked
// (HTTP) and nothing anyone else wrote ever appeared (websocket), until a reload.
// It looked like a connection problem and was not: the socket was connected the
// whole time, delivering to a listener that did not exist.
//
// So BOTH paths are wired. Which one a deployment uses depends on whether the
// token carries the channel, and that is a server decision this module should
// not have to predict.

/** Centrifugo's "you are already subscribed to this channel" reply. */
export const ALREADY_SUBSCRIBED_CODE = 105;

/**
 * Is this subscription error the ordinary consequence of a token that already
 * carries the channel?
 *
 * It fires for every channel on every connect, so a client that counts it as a
 * failure is in a permanent false failure state — which is exactly how the PWA
 * ended up showing "Reconnecting…" forever on a working connection.
 */
export function isAlreadySubscribed(ctx) {
  if (!ctx || typeof ctx !== "object") return false;
  // Centrifuge nests the code under `error` on a subscription context and puts
  // it at the top level on some client-level ones. Both shapes are accepted so
  // a client-library change cannot quietly turn 105 back into a failure.
  const nested = ctx.error && typeof ctx.error === "object" ? ctx.error.code : undefined;
  const code = nested === undefined ? ctx.code : nested;
  return code === ALREADY_SUBSCRIBED_CODE;
}

/**
 * A stable identity for a publication, or null when it has none.
 *
 * Used to drop a frame that reached the handler twice. Only frames carrying a
 * server-assigned id get a key: `agent_stream_chunk` deliberately does not,
 * because two chunks of a stream can legitimately be byte-identical (" the"
 * twice in one answer) and collapsing those would silently corrupt the reply.
 * Chunks are protected by the delivery-path ownership below instead, which is
 * structural rather than statistical.
 */
export function publicationKey(channel, data) {
  if (!data || typeof data !== "object") return null;
  const type = data.type;
  if (type === "message") {
    const id = data.message && data.message.id;
    return id ? `${channel}|message|${id}` : null;
  }
  if (
    type === "agent_stream_start" ||
    type === "agent_stream_end" ||
    type === "agent_stream_error"
  ) {
    return data.message_id ? `${channel}|${type}|${data.message_id}` : null;
  }
  return null;
}

/** How many recent publication keys to remember. Bounded so a long-lived tab
 *  cannot grow this without limit. */
const DEDUPE_WINDOW = 512;

/** A FIFO set of recently-seen keys. `seen(key)` returns true the second time. */
export function createPublicationDeduper(limit = DEDUPE_WINDOW) {
  const order = [];
  const set = new Set();
  return function seen(key) {
    if (key === null || key === undefined) return false;
    if (set.has(key)) return true;
    set.add(key);
    order.push(key);
    if (order.length > limit) set.delete(order.shift());
    return false;
  };
}

// ---------- Connection status ----------
//
// A BANNER MUST BE DRIVEN BY STATE, NEVER BY THE LAST INCIDENT SEEN.
//
// The PWA showed "Reconnecting..." permanently on a phone whose connection was
// perfectly fine — messages sent, history loaded, frames arrived. The banner was
// wired to `connected` and `error`, and those are not the two halves of
// anything. Centrifuge emits `error` for transient, non-fatal things (a
// subscribe that failed, a token refresh that hiccupped) INCLUDING while the
// socket is healthy and stays healthy. Only a new `connected` cleared it, and a
// connection that never dropped never emits `connected` again. One hiccup, and
// the app told the person it was broken for the rest of the session.
//
// So the state machine below has no input for an error at all. That is the
// structural version of the rule: an incident cannot leave a claim on screen
// because there is nowhere to put one.

/** What the person is told while a connection attempt is in flight. */
export const CONNECTION_RECONNECTING = "Reconnecting…";

/** What they are told when live updates are actually gone. */
export const CONNECTION_OFFLINE =
  "Live updates are off. Reload the page to reconnect.";

/**
 * How long `connecting` must persist before it is worth mentioning.
 *
 * Every normal load passes through `connecting` on its way to `connected`.
 * Rendering the banner immediately would flash "Reconnecting…" for a couple of
 * hundred milliseconds on every single open, which is its own defect — and it
 * teaches people to ignore the banner on the day it means something.
 */
export const CONNECTING_GRACE_MS = 1200;

/**
 * The banner's state machine: three transitions in, one string (or null) out.
 *
 * Pure of the DOM — the surface passes a `render` that puts the string
 * somewhere. Timers are injected so a test can drive the grace window without
 * waiting for it.
 *
 * @param {{
 *   render: (text: string|null) => void,
 *   graceMs?: number,
 *   setTimer?: Function,
 *   clearTimer?: Function
 * }} opts
 * @returns {{connecting: Function, connected: Function, disconnected: Function,
 *            offline: Function, current: Function, dispose: Function}}
 */
export function createConnectionStatus(opts) {
  const cfg = opts || {};
  const render = typeof cfg.render === "function" ? cfg.render : () => {};
  const graceMs = typeof cfg.graceMs === "number" ? cfg.graceMs : CONNECTING_GRACE_MS;
  const setTimer = cfg.setTimer || setTimeout;
  const clearTimer = cfg.clearTimer || clearTimeout;

  let pending = null;
  let shown = null;

  function cancelPending() {
    if (pending !== null) {
      clearTimer(pending);
      pending = null;
    }
  }

  function show(text) {
    cancelPending();
    if (shown === text) return;
    shown = text;
    render(text);
  }

  return {
    /** A connection attempt is in flight. Says so only if it lasts. */
    connecting() {
      // Already complaining, or already scheduled to: leave the timer alone so
      // a flapping connection does not keep pushing the message further away.
      if (shown === CONNECTION_RECONNECTING || pending !== null) return;
      pending = setTimer(() => {
        pending = null;
        shown = CONNECTION_RECONNECTING;
        render(CONNECTION_RECONNECTING);
      }, graceMs);
    },
    /** The socket is up. Nothing to say. */
    connected() {
      show(null);
    },
    /** The socket is down and not coming back on its own. */
    disconnected() {
      show(CONNECTION_OFFLINE);
    },
    /** No socket was ever established (the vendored client is missing, etc). */
    offline() {
      show(CONNECTION_OFFLINE);
    },
    /** What is on screen right now — for tests and for callers that ask. */
    current() {
      return shown;
    },
    dispose() {
      cancelPending();
    },
  };
}

/**
 * Connect to Centrifugo and return the handle the surface drives.
 *
 * @param {{
 *   Centrifuge: Function,
 *   api: {centrifugoToken: () => Promise<{token: string, ws_url: string}>},
 *   getUserSub: () => (string|null),
 *   onTeamPublication: (data: any) => void,
 *   onUserPublication: (data: any) => void,
 *   onPresenceChange?: () => void,
 *   onConnected?: () => void,
 *   onConnecting?: () => void,
 *   onDisconnected?: (ctx: any) => void,
 *   onError?: (e: any) => void,
 *   previous?: {centrifuge: any}|null
 * }} opts
 *   Centrifuge        — the client constructor, loaded by the surface
 *   api               — a chat-core api client; only centrifugoToken() is used
 *   getUserSub        — the caller's source_user_id, read late (it arrives with /v1/me)
 *   onTeamPublication — every frame on the active team channel
 *   onUserPublication — every frame on the caller's own user channel
 *   onPresenceChange  — optional; when omitted no join/leave/subscribed handlers
 *                       are wired, because a surface without a presence badge has
 *                       nothing to recompute
 *   previous          — an earlier handle to tear down first (e.g. token refresh)
 * @returns {Promise<{centrifuge: any, subscribeTeam: Function,
 *                    unsubscribeTeam: Function, disconnect: Function}|null>}
 *   null when no Centrifuge constructor was supplied — the same fail-soft the
 *   popup had, because a missing vendor file must not take the whole chat down.
 */
export async function connectRealtime(opts) {
  const cfg = opts || {};
  const Centrifuge = cfg.Centrifuge;
  const api = cfg.api;
  if (!api || typeof api.centrifugoToken !== "function") {
    throw new TypeError("connectRealtime requires opts.api.centrifugoToken()");
  }
  const getUserSub =
    typeof cfg.getUserSub === "function" ? cfg.getUserSub : () => null;
  const onTeamPublication =
    typeof cfg.onTeamPublication === "function" ? cfg.onTeamPublication : () => {};
  const onUserPublication =
    typeof cfg.onUserPublication === "function" ? cfg.onUserPublication : () => {};
  const onPresenceChange =
    typeof cfg.onPresenceChange === "function" ? cfg.onPresenceChange : null;

  const tokenInfo = await api.centrifugoToken();

  // Disconnect any prior centrifuge instance (e.g. after token refresh). Its
  // subscriptions belonged to that instance and are dropped with it, so the fresh
  // instance below rebuilds the user channel from scratch.
  if (cfg.previous && cfg.previous.centrifuge) {
    try {
      cfg.previous.centrifuge.disconnect();
    } catch {
      /* ignore */
    }
  }

  if (!Centrifuge) {
    console.error("[xbrain] Centrifuge constructor missing — the vendored client did not load");
    return null;
  }

  const centrifuge = new Centrifuge(tokenInfo.ws_url, {
    token: tokenInfo.token,
  });
  // The three that describe the connection's STATE. `connecting` and
  // `disconnected` were never wired, which is why the only things a surface
  // could react to were "connected once" and "something went wrong once".
  centrifuge.on("connected", () => {
    console.log("[xbrain] centrifuge connected");
    if (typeof cfg.onConnected === "function") cfg.onConnected();
  });
  centrifuge.on("connecting", (ctx) => {
    console.log("[xbrain] centrifuge connecting");
    if (typeof cfg.onConnecting === "function") cfg.onConnecting(ctx);
  });
  centrifuge.on("disconnected", (ctx) => {
    console.warn("[xbrain] centrifuge disconnected:", ctx);
    if (typeof cfg.onDisconnected === "function") cfg.onDisconnected(ctx);
  });
  // An INCIDENT, not a state. Still surfaced for logging, and deliberately not
  // routed into the status machine — see the comment above CONNECTION_RECONNECTING.
  centrifuge.on("error", (err) => {
    if (isAlreadySubscribed(err)) return; // normal, and it fires on every connect
    console.warn("[xbrain] centrifuge error:", err);
    if (typeof cfg.onError === "function") cfg.onError(err);
  });

  // ---- Delivery: two paths, one of them live per channel ----
  //
  // `ownedBySubscription` is the arbiter. A client-side Subscription that
  // actually reaches `subscribed` owns its channel and the client-level event
  // ignores it; a Subscription refused with 105 owns nothing and the client-level
  // event carries the traffic. That is exact rather than a guess about which
  // path a given deployment uses, and it is why a duplicate cannot normally
  // occur at all. The id-keyed deduper behind it covers the frames that carry an
  // id anyway, because "cannot normally occur" is not a guarantee.
  const ownedBySubscription = new Set();
  const seenPublication = createPublicationDeduper();
  const userSub = getUserSub();
  const userChannel = userSub ? `user:${userSub}` : null;
  // The ONE team channel this surface is currently showing. The token's
  // `channels` claim grants every team the person belongs to, so the
  // client-level event delivers all of them — routing on the `team:` prefix
  // alone would paint another team's messages into the open thread.
  let activeTeamChannel = null;

  function route(channel, data) {
    if (typeof channel !== "string") return;
    if (channel !== userChannel && channel !== activeTeamChannel) return;
    if (seenPublication(publicationKey(channel, data))) return;
    if (channel === userChannel) {
      onUserPublication(data);
    } else {
      onTeamPublication(data);
    }
  }

  // Server-side subscriptions (the token's `channels` claim) deliver HERE.
  centrifuge.on("publication", (ctx) => {
    if (!ctx || ownedBySubscription.has(ctx.channel)) return;
    route(ctx.channel, ctx.data);
  });

  /**
   * Wire one client-side Subscription.
   *
   * Kept even though the server usually pre-subscribes: presence (`join` /
   * `leave`) rides on a Subscription object and nothing else provides it, so
   * deleting these would take the extension's presence badge dark.
   */
  function wireSubscription(sub, channel) {
    sub.on("publication", (ctx) => route(channel, ctx.data));
    sub.on("subscribed", () => {
      ownedBySubscription.add(channel);
    });
    sub.on("error", (ctx) => {
      // 105 is the normal answer when the token already carries this channel.
      // It is not a failure, it must not reach the surface, and it must not be
      // logged — it fires once per channel per connect, for every user.
      if (isAlreadySubscribed(ctx)) {
        ownedBySubscription.delete(channel);
        return;
      }
      console.warn(`[xbrain] subscription error on ${channel}:`, ctx);
    });
    sub.on("unsubscribed", () => {
      ownedBySubscription.delete(channel);
    });
    return sub;
  }

  // Subscribe to the caller's OWN user channel. The centrifugo-token endpoint
  // already grants `user:<source_user_id>`, so this is just claiming a channel the
  // token authorizes. Built ONCE per connection and independent of team switches
  // (a team switch never touches this sub).
  let userSubscription = null;
  if (userChannel) {
    userSubscription = wireSubscription(
      centrifuge.newSubscription(userChannel),
      userChannel,
    );
    userSubscription.subscribe();
  }

  let teamSubscription = null;

  /** Tear down the active team subscription, if any. Safe to call repeatedly. */
  function unsubscribeTeam() {
    // Cleared even when there is no client-side Subscription object: with a
    // server-side subscription this is the ONLY thing that stops the previous
    // team's frames being routed into the newly-opened thread.
    if (activeTeamChannel) ownedBySubscription.delete(activeTeamChannel);
    activeTeamChannel = null;
    if (!teamSubscription) return;
    try {
      teamSubscription.unsubscribe();
      centrifuge.removeSubscription(teamSubscription);
    } catch {
      /* ignore */
    }
    teamSubscription = null;
  }

  /**
   * Point the connection at a team channel, replacing any prior one.
   * @param {string} teamId
   */
  function subscribeTeam(teamId) {
    unsubscribeTeam();
    const channel = `team:${teamId}`;
    activeTeamChannel = channel;
    teamSubscription = wireSubscription(
      centrifuge.newSubscription(channel),
      channel,
    );
    if (onPresenceChange) {
      teamSubscription.on("join", () => onPresenceChange());
      teamSubscription.on("leave", () => onPresenceChange());
      teamSubscription.on("subscribed", () => onPresenceChange());
    }
    teamSubscription.subscribe();
    return teamSubscription;
  }

  function disconnect() {
    unsubscribeTeam();
    if (userSubscription) {
      try {
        userSubscription.unsubscribe();
      } catch {
        /* ignore */
      }
      userSubscription = null;
    }
    try {
      centrifuge.disconnect();
    } catch {
      /* ignore */
    }
  }

  centrifuge.connect();

  return {
    centrifuge,
    subscribeTeam,
    unsubscribeTeam,
    disconnect,
    /** The active team subscription — presence stats are read off it. */
    get teamSubscription() {
      return teamSubscription;
    },
  };
}
