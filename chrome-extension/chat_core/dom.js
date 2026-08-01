/**
 * The two DOM chores every panel in this product repeats (Phase 27, D-27-04).
 *
 * Small on purpose. They live here because both surfaces and every panel below
 * them need exactly these two behaviours, and the versions that were written
 * per-panel had already started to disagree: one cleared its status line by
 * hiding it, another by blanking the text, a third by both — so a stale error
 * could survive the next open on some panels and not others.
 *
 * Neither function assigns a markup string. `clearChildren` exists precisely so
 * that emptying a list never reaches for the "convenient" innerHTML version on a
 * node that carries names somebody typed.
 *
 * Used by:
 *   - packages/chat-core/people.js, invite.js, teams.js
 *   - both surfaces, through their generated chat_core/ copies
 */

/**
 * Paint a one-line status under a control.
 *
 * The element is HIDDEN and emptied when there is nothing to say, so a panel
 * that was closed on an error does not reopen still showing it. `type` becomes
 * the element's whole className ("loading" | "success" | "error" | ""), which is
 * what the existing stylesheets already key their pills off.
 *
 * @param {Element|null} el the status element; a missing one is a no-op
 * @param {string} text
 * @param {string} [type]
 */
export function setStatusLine(el, text, type) {
  if (!el) return;
  if (!text) {
    el.hidden = true;
    el.textContent = "";
    el.className = "";
    return;
  }
  el.hidden = false;
  // textContent, never markup: some of these lines carry a server string or a
  // team name somebody typed.
  el.textContent = text;
  el.className = type || "";
}

/**
 * Remove every child of an element.
 *
 * @param {Element|null} el
 */
export function clearChildren(el) {
  if (!el) return;
  while (el.firstChild) el.removeChild(el.firstChild);
}
