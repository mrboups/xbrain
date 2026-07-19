/**
 * xbrain push-a-link — RECEIVE handler (Phase 22, Plan 22-02).
 *
 * Consent core on the client. When another team member nudges the user with a
 * URL, the server publishes an `open_url` event to the user's own Centrifugo
 * `user:<sub>` channel (Plan 22-01). This module turns that event into a native
 * OS notification carrying the sender's name + the FULL, un-shortened URL — and
 * NOTHING else.
 *
 * SECURITY INVARIANT (D-22-02 / T-22-08): this module has NO access to any
 * browser-tab API. It literally cannot open a browser tab. The tab-open is
 * deferred to an explicit notification CLICK, wired in background.js in Plan
 * 22-03. That makes "a message from another user never moves the recipient's
 * browser without their click" a structural guarantee, not a convention — the
 * code that could open a tab is not even reachable from this handler. Do NOT
 * add a browser-tab API call here; the node test greps this file for the
 * tab-opening capability name and fails if it appears.
 *
 * Pure module: imports NOTHING from chrome.*. All side effects flow through the
 * injected `deps` so the handler is testable in plain node (no jsdom, no chrome
 * polyfill). Used by:
 *   - chrome-extension/popup.js (subscribes user:<sub>, routes open_url here)
 *   - chrome-extension/tests/test_nudge_open.mjs
 */

/**
 * Client-side URL safety gate (defense-in-depth over the server's
 * is_safe_nudge_url — Plan 22-01, D-22-03). Accepts ONLY http/https; drops
 * javascript:, data:, file:, mailto:, malformed, empty, and non-string input.
 *
 * @param {unknown} url
 * @returns {boolean}
 */
export function isSafeHttpUrl(url) {
  if (typeof url !== "string" || url.trim() === "") return false;
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  return parsed.protocol === "http:" || parsed.protocol === "https:";
}

/**
 * Handle an incoming `open_url` event. Builds a notification ONLY after the
 * recipient's opt-out and the URL-scheme checks both pass; otherwise it returns
 * null and creates nothing. Never opens a tab (see module invariant above).
 *
 * @param {{type?: string, url?: string, from?: {display_name?: string|null, sub?: string|null}}|null|undefined} data
 *        The raw event payload published by the server.
 * @param {{
 *   getSettings: () => Promise<{allowOpenLinkRequests?: boolean}>,
 *   notify: (opts: {type: string, iconUrl: string, title: string, message: string, priority: number}) => Promise<string>,
 *   persistPending?: (notificationId: string, url: string) => Promise<void>|void,
 * }} deps
 * @returns {Promise<string|null>} the notification id when shown, else null.
 */
export async function handleOpenUrl(data, deps) {
  // Ignore anything that is not a well-formed open_url event.
  if (!data || data.type !== "open_url") return null;

  // Recipient opt-out (D-22-04). Default is ON; only an explicit `false`
  // suppresses. When OFF, the event is ignored entirely — no notification.
  const settings = await deps.getSettings();
  if (settings && settings.allowOpenLinkRequests === false) return null;

  // Defense-in-depth: drop unsafe/malformed schemes client-side. The server
  // already validated, but the extension re-checks before showing anything.
  if (!isSafeHttpUrl(data.url)) return null;

  const from = data.from || {};
  const sender = from.display_name || from.sub || "A teammate";

  // The notification shows exactly WHO and exactly WHERE: the sender in the
  // title, the full literal URL as the message (no shortening — T-22-10).
  const id = await deps.notify({
    type: "basic",
    iconUrl: "icon128.png",
    title: `${sender} wants to open a link`,
    message: data.url,
    priority: 2,
  });

  // Stash id → url so the click handler (Plan 22-03) knows which URL to open.
  if (deps.persistPending) await deps.persistPending(id, data.url);

  return id;
}
