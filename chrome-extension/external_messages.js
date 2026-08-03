/**
 * What a WEB PAGE is allowed to ask this extension to do.
 *
 * `externally_connectable` opens a door from the open web into an extension
 * holding a personal API token, a claude.ai session and host permissions for
 * both. An extension that will do anything a page asks is a far worse bug than
 * any it was opened to fix, so the door is narrow by construction:
 *
 *   - ONE origin, compared as a whole string. Not a prefix, not a suffix, not a
 *     regex: `https://grooveos.app.evil.test` and `https://evil.grooveos.app`
 *     both start or end the right way and neither is us.
 *   - A CLOSED set of message types, and each one takes no parameters at all.
 *     There is nothing to validate because there is nothing to pass — the
 *     strongest version of validating every field.
 *   - Every action is idempotent and affects only this user's own bridge. None
 *     reads data, none sends anything anywhere, none can be made to act on
 *     another user.
 *
 * Pure: the caller injects the actions, so node tests exercise the decision
 * without chrome.* — which is the half that has to be right.
 */

/** The one web origin that may speak to this extension. */
export const ALLOWED_EXTERNAL_ORIGINS = Object.freeze(["https://grooveos.app"]);

/** Ask whether the bridge is live. Answers, changes nothing. */
export const MSG_BRIDGE_STATUS = "XBRAIN_BRIDGE_STATUS";

/** Ask for the bridge to be brought up if it is not. Idempotent. */
export const MSG_BRIDGE_ENSURE = "XBRAIN_BRIDGE_ENSURE";

/** Every type the extension will act on, in one place. */
export const EXTERNAL_MESSAGE_TYPES = Object.freeze([
  MSG_BRIDGE_STATUS,
  MSG_BRIDGE_ENSURE,
]);

/**
 * Is this sender the PWA?
 *
 * `sender.origin` is set by Chrome, not by the page, which is what makes it
 * trustworthy. A sender without one is refused rather than fallen back on:
 * deriving an origin from `sender.url` would accept whatever a caller could
 * talk Chrome into putting there.
 *
 * @param {{origin?: string}|null|undefined} sender
 * @param {string[]} [allowed]
 * @returns {boolean}
 */
export function isAllowedExternalSender(sender, allowed = ALLOWED_EXTERNAL_ORIGINS) {
  const origin = sender && sender.origin;
  if (typeof origin !== "string" || !origin) return false;
  return allowed.includes(origin);
}

/**
 * Decide what to do with one external message.
 *
 * Returns null when the message is refused — the caller then answers nothing at
 * all, so a page that guesses wrong learns only that it guessed wrong. Refusals
 * are deliberately indistinguishable from each other: a bad origin and a bad
 * type produce the same silence, so this cannot be probed for which types exist.
 *
 * @param {object} opts
 * @param {any} opts.message
 * @param {{origin?: string}} opts.sender
 * @param {{isLive: () => boolean, ensure: () => Promise<any>}} opts.actions
 * @param {string[]} [opts.allowedOrigins]
 * @returns {Promise<{ok: true, live: boolean}>|null}
 */
export function handleExternalMessage({ message, sender, actions, allowedOrigins }) {
  if (!isAllowedExternalSender(sender, allowedOrigins || ALLOWED_EXTERNAL_ORIGINS)) {
    return null;
  }
  if (!message || typeof message !== "object" || Array.isArray(message)) return null;
  const type = message.type;
  if (typeof type !== "string" || !EXTERNAL_MESSAGE_TYPES.includes(type)) return null;

  if (type === MSG_BRIDGE_STATUS) {
    return Promise.resolve({ ok: true, live: Boolean(actions.isLive()) });
  }
  // MSG_BRIDGE_ENSURE — bring the socket up if it is not already, then report.
  // The answer is what this extension can see from here, which is a different
  // and lesser thing than the server's answer: the PWA asks the SERVER whether
  // the subscription is connected, and only asks the extension to help make it
  // so. Two questions, one authority each.
  return Promise.resolve(actions.ensure()).then(() => ({
    ok: true,
    live: Boolean(actions.isLive()),
  }));
}
