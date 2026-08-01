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
  centrifuge.on("connected", () => {
    console.log("[xbrain] centrifuge connected");
    if (typeof cfg.onConnected === "function") cfg.onConnected();
  });
  centrifuge.on("error", (err) => {
    console.warn("[xbrain] centrifuge error:", err);
    if (typeof cfg.onError === "function") cfg.onError(err);
  });

  // Subscribe to the caller's OWN user channel. The centrifugo-token endpoint
  // already grants `user:<source_user_id>`, so this is just claiming a channel the
  // token authorizes. Built ONCE per connection and independent of team switches
  // (a team switch never touches this sub).
  let userSubscription = null;
  const sub = getUserSub();
  if (sub) {
    userSubscription = centrifuge.newSubscription(`user:${sub}`);
    userSubscription.on("publication", (ctx) => onUserPublication(ctx.data));
    userSubscription.subscribe();
  }

  let teamSubscription = null;

  /** Tear down the active team subscription, if any. Safe to call repeatedly. */
  function unsubscribeTeam() {
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
    teamSubscription = centrifuge.newSubscription(`team:${teamId}`);
    teamSubscription.on("publication", (ctx) => onTeamPublication(ctx.data));
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
