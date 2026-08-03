/**
 * Nudging the xbrain extension's bridge awake, when there is one to nudge.
 *
 * WHAT THIS IS. A desktop PWA sitting in the same browser as the extension can
 * ask it to bring its socket up before an agent turn, instead of discovering it
 * was asleep afterwards. That is the entire scope.
 *
 * WHAT THIS IS NOT. It is not how this app reaches the Claude subscription. The
 * bridge is keyed by USER, not by device: a message sent from a phone already
 * routes through whatever browser that person has open somewhere, with no
 * extension on the phone at all. Nothing here grants anything that does not
 * already exist — it only shortens the gap on the co-located case.
 *
 * It is also not a second bridge. A web page cannot execute against claude.ai
 * with somebody's cookies; that cross-origin boundary is the whole reason the
 * extension exists, and any attempt to reimplement it here would be a security
 * hole where a feature was wanted.
 *
 * SILENCE IS THE CONTRACT. On a phone there is no extension, ever. There must
 * be no error, no prompt, no console noise and no UI difference — this is an
 * enhancement that is simply unavailable, and an enhancement that announces its
 * own absence reads as a broken feature. Every failure path below returns the
 * same shape as "no extension here", because to this app they are the same
 * thing.
 */

/**
 * The extension's stable id, derived from the `key` field in its manifest.
 * See .planning/KB/chrome-extension-key.md.
 */
export const XBRAIN_EXTENSION_ID = "anigikcnmldoklcmogffmgcojdhhficb";

/** Message types the extension accepts. Its list is the authority; this mirrors it. */
export const MSG_BRIDGE_STATUS = "XBRAIN_BRIDGE_STATUS";
export const MSG_BRIDGE_ENSURE = "XBRAIN_BRIDGE_ENSURE";

/** How long to wait before giving up on a reply. */
const REPLY_TIMEOUT_MS = 2000;

/** The answer when there is nothing to ask, or the asking did not work out. */
const UNAVAILABLE = Object.freeze({ available: false, live: false });

/**
 * Send one message to the extension, or resolve `UNAVAILABLE`.
 *
 * @param {string} type one of the two constants above
 * @param {{runtime?: any}} [env] injected for tests; defaults to the page's own chrome
 * @returns {Promise<{available: boolean, live: boolean}>} never rejects
 */
export function askExtension(type, env) {
  const chromeApi = env === undefined ? globalThis.chrome : env;
  const runtime = chromeApi && chromeApi.runtime;
  if (!runtime || typeof runtime.sendMessage !== "function") {
    // The ordinary case on a phone, in Firefox, in Safari, and in Chrome with
    // the extension not installed. Not a failure — an absence.
    return Promise.resolve(UNAVAILABLE);
  }

  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    // An extension that is installed but wedged would otherwise leave this
    // pending forever, and a caller awaiting it before a send would hang the
    // composer on a nicety.
    const timer = setTimeout(() => finish(UNAVAILABLE), REPLY_TIMEOUT_MS);

    try {
      runtime.sendMessage(XBRAIN_EXTENSION_ID, { type }, (reply) => {
        clearTimeout(timer);
        // READ, always. An unread runtime.lastError is printed to the console
        // by Chrome itself — which is exactly the noise this module promises
        // not to make, on every page load, for everyone without the extension.
        const failed = runtime.lastError;
        if (failed || !reply || reply.ok !== true) {
          finish(UNAVAILABLE);
          return;
        }
        finish({ available: true, live: Boolean(reply.live) });
      });
    } catch {
      // sendMessage throws synchronously in some browsers when the id is
      // unknown. Same absence, same answer.
      clearTimeout(timer);
      finish(UNAVAILABLE);
    }
  });
}

/**
 * Ask the extension to make sure its bridge is up.
 *
 * Call on load and before an agent turn. Nothing depends on the result: the
 * app's own view of whether the subscription is connected comes from the
 * SERVER, which is the only party that knows about bridges held in other
 * browsers. This is a nudge, not a source of truth.
 *
 * @returns {Promise<{available: boolean, live: boolean}>} never rejects
 */
export function ensureBridge(env) {
  return askExtension(MSG_BRIDGE_ENSURE, env);
}

/**
 * Ask whether the extension in THIS browser currently holds the socket.
 *
 * Note what this cannot tell anyone: `live: false` means "not in this browser",
 * which is not the same as "this person has no bridge" and must never be shown
 * as though it were. Only the server can answer the second one.
 *
 * @returns {Promise<{available: boolean, live: boolean}>} never rejects
 */
export function bridgeStatusHere(env) {
  return askExtension(MSG_BRIDGE_STATUS, env);
}
