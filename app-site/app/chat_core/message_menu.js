/**
 * The per-message actions overlay: right-click on a desktop, long-press on a
 * phone, and reachable from the keyboard on both.
 *
 * It lives outside render.js on purpose. The renderer's job is turning a message
 * into a bubble; this is a separate concern that only ever READS rows the
 * renderer produced (`data-msg-id`, `data-author-key`) and never participates in
 * drawing one. That separation is also what lets the two evolve independently —
 * a change to how text is rendered cannot break the menu, and vice versa.
 *
 * ONE ACTION TODAY, and the shape is built for more. `registerAction` exists
 * because starring a message is already asked for and this is where it will
 * land; adding it must not mean re-opening the overlay, the long-press guards or
 * the keyboard path.
 *
 * WHAT THE SERVER DECIDES. Whether a person may delete a message is the server's
 * answer, always. This module draws the control from the same rule the server
 * applies — your own message, or a team where the server told you `can_moderate`
 * — so it neither offers something that will be refused nor hides something that
 * would be allowed. If the two ever disagree, the server wins and the refusal is
 * shown as text; the UI is not the gate.
 *
 * Used by:
 *   - chrome-extension/popup.js (via chrome-extension/chat_core/)
 *   - app-site/app/ — the PWA (via app-site/app/chat_core/)
 *   - chrome-extension/tests/test_chat_core_message_menu.mjs
 */

/** Remove the bubble only. The team's memory keeps what was said. */
export const DELETE_SCOPE_MESSAGE = "message";

/** Remove the bubble AND the memory items the message seeded. */
export const DELETE_SCOPE_MESSAGE_AND_BRAIN = "message_and_brain";

/**
 * How long a touch must be held before it is a press rather than a tap.
 *
 * 500ms is the platform convention. Shorter starts firing on taps that happened
 * to linger; longer stops feeling like a press at all.
 */
export const LONG_PRESS_MS = 500;

/**
 * How far a finger may drift and still count as a press, in CSS pixels.
 *
 * The whole point: a thread is a scrolling surface, and the first frames of a
 * flick look exactly like the first frames of a press. Past this the gesture is
 * a scroll and the pending menu is abandoned — a menu that opens under a moving
 * finger is worse than no menu.
 */
export const LONG_PRESS_MOVE_TOLERANCE_PX = 10;

/* ==========================================================================
 * What the two outcomes are called, in one place
 *
 * "Removed" is honest and "deleted forever" is not: both halves are soft deletes
 * with a 30-day window before the janitor purges. Copy that oversells the second
 * option would make people avoid the one they need; copy that undersells it
 * would let them believe they had removed something they had not.
 * ======================================================================== */

export const MENU_LABEL_DELETE = "Delete message…";
export const CONFIRM_TITLE = "Remove this message?";
export const CONFIRM_MESSAGE_LABEL = "Remove from the chat";
export const CONFIRM_MESSAGE_NOTE =
  "It leaves the chat for everyone. What was said stays in the team's memory, " +
  "so the agent can still answer from it.";
export const CONFIRM_BRAIN_LABEL = "Remove from the chat and the memory";
export const CONFIRM_BRAIN_NOTE =
  "It leaves the chat and the team's memory. Recoverable for 30 days, then gone.";
export const CONFIRM_CANCEL_LABEL = "Keep it";
export const BUSY_TEXT = "Removing…";

/**
 * What the person is told when the server refuses.
 *
 * From the STATUS CODE and this closed vocabulary — never from the response
 * body. A server's error text is not written for the person holding the phone,
 * and rendering it here is how an internal detail reaches a teammate's screen.
 *
 * @param {number} status
 * @returns {string}
 */
export function deleteErrorText(status) {
  if (status === 403) {
    return "You can only remove your own messages. A team admin can remove any of them.";
  }
  if (status === 404) return "That message is already gone.";
  if (status === 401) return "You are signed out. Sign in and try again.";
  return `Could not remove it (HTTP ${status}).`;
}

/**
 * Take one message row out of the list.
 *
 * Exported on its own because the realtime path needs it without any of the
 * menu: a deletion someone ELSE performed arrives as a frame and has no gesture,
 * no overlay and no confirmation behind it.
 *
 * Returns whether a row was actually removed, so a caller can skip the
 * separator/grouping reconcile when nothing changed.
 *
 * @param {any} listEl the message list element
 * @param {string} messageId
 * @returns {boolean}
 */
export function removeMessageRow(listEl, messageId) {
  if (!listEl || !messageId) return false;
  const row = listEl.querySelector(`[data-msg-id="${messageId}"]`);
  if (!row) return false;
  if (typeof row.remove === "function") row.remove();
  else if (row.parentNode) row.parentNode.removeChild(row);
  return true;
}

/**
 * May this row's owner be acted on by this viewer?
 *
 * Read off the row rather than off a message object, because the menu opens from
 * a DOM event and the object that built the row is long gone. `data-author-key`
 * is the renderer's own `user:<id>` / `agent:<name>` string.
 *
 * An AGENT row is never "yours" — nobody authored it — so only a moderator can
 * remove one. That is the same answer the server gives.
 *
 * @param {any} row
 * @param {{selfUserId?: string|null, canModerate?: boolean}} viewer
 * @returns {boolean}
 */
export function canDeleteRow(row, viewer = {}) {
  if (!row || !row.dataset) return false;
  const key = row.dataset.authorKey || "";
  const self = viewer.selfUserId;
  if (self && key === `user:${self}`) return true;
  return Boolean(viewer.canModerate);
}

/**
 * Build the actions overlay and wire it to a message list.
 *
 * @param {{
 *   doc: any,
 *   listEl: any,
 *   scrollEl?: any,
 *   getActiveTeamId: () => (string|null),
 *   getSelfUserId: () => (string|null|undefined),
 *   getViewerCanModerate?: () => boolean,
 *   deleteMessage: (teamId: string, messageId: string, scope: string) => Promise<any>,
 *   onDeleted?: (messageId: string) => void,
 *   longPressMs?: number,
 *   setTimer?: Function,
 *   clearTimer?: Function,
 *   getSelectionText?: () => string
 * }} opts
 *   deleteMessage — returns the RAW Response; the codes mean different things and
 *                   an exception would collapse them into one sentence
 *   onDeleted     — the surface takes the row out and reconciles its separators;
 *                   this module does not own the thread's layout
 * @returns {{attach: Function, dispose: Function, open: Function, close: Function,
 *            isOpen: Function, registerAction: Function}}
 */
export function createMessageMenu(opts) {
  const cfg = opts || {};
  const doc = cfg.doc;
  const listEl = cfg.listEl;
  if (!doc || typeof doc.createElement !== "function") {
    throw new TypeError("createMessageMenu requires opts.doc");
  }
  if (!listEl) throw new TypeError("createMessageMenu requires opts.listEl");
  if (typeof cfg.deleteMessage !== "function") {
    throw new TypeError("createMessageMenu requires opts.deleteMessage()");
  }

  const scrollEl = cfg.scrollEl || null;
  const getActiveTeamId =
    typeof cfg.getActiveTeamId === "function" ? cfg.getActiveTeamId : () => null;
  const getSelfUserId =
    typeof cfg.getSelfUserId === "function" ? cfg.getSelfUserId : () => null;
  const getViewerCanModerate =
    typeof cfg.getViewerCanModerate === "function"
      ? cfg.getViewerCanModerate
      : () => false;
  const onDeleted = typeof cfg.onDeleted === "function" ? cfg.onDeleted : () => {};
  const longPressMs =
    typeof cfg.longPressMs === "number" ? cfg.longPressMs : LONG_PRESS_MS;
  const setTimer = cfg.setTimer || ((fn, ms) => setTimeout(fn, ms));
  const clearTimer = cfg.clearTimer || ((t) => clearTimeout(t));
  const view = doc.defaultView || null;

  /**
   * Whatever the person currently has selected, as text.
   *
   * Injected so a test can drive it: a real Selection object is not something a
   * DOM stub should have to reimplement, and this is the guard that decides
   * whether a long-press over a highlighted paragraph opens a menu.
   */
  const getSelectionText =
    typeof cfg.getSelectionText === "function"
      ? cfg.getSelectionText
      : () => {
          const sel =
            (doc.getSelection && doc.getSelection()) ||
            (view && view.getSelection && view.getSelection());
          return sel ? String(sel) : "";
        };

  /* ---------------------------------------------------------------------
   * The action list
   *
   * Each entry: {id, label, danger?, isVisible(ctx), run(ctx)}. `ctx` carries
   * {row, messageId, teamId, close, showConfirm}. Starring will be one more
   * entry here and nothing else — that is what "carry more actions later" has to
   * mean to be worth anything.
   * ------------------------------------------------------------------- */
  const actions = [
    {
      id: "delete",
      label: MENU_LABEL_DELETE,
      isVisible: (ctx) => ctx.canDelete,
      run: (ctx) => ctx.showConfirm(),
    },
  ];

  /** Add an action. Later entries render below earlier ones. */
  function registerAction(action) {
    if (!action || typeof action.run !== "function" || !action.label) {
      throw new TypeError("registerAction requires {label, run}");
    }
    actions.push(action);
  }

  // ---- open state -------------------------------------------------------

  /** The single open overlay, or null. One at a time, always. */
  let open = null;
  /** A long-press that has started but not yet fired. */
  let pending = null;

  function rowFrom(node) {
    let el = node;
    let guard = 0;
    while (el && el !== listEl && guard < 40) {
      if (el.dataset && el.dataset.msgId) return el;
      el = el.parentNode;
      guard += 1;
    }
    return null;
  }

  function cancelPending() {
    if (!pending) return;
    clearTimer(pending.timer);
    pending = null;
  }

  function close() {
    cancelPending();
    if (!open) return;
    const state = open;
    open = null;
    doc.removeEventListener("keydown", state.onKeydown, true);
    if (state.scrim && typeof state.scrim.remove === "function") state.scrim.remove();
    else if (state.scrim && state.scrim.parentNode) {
      state.scrim.parentNode.removeChild(state.scrim);
    }
    // Give the focus back where it came from, or the keyboard user is stranded
    // at the top of the document after every menu.
    if (state.returnFocusTo && typeof state.returnFocusTo.focus === "function") {
      state.returnFocusTo.focus();
    }
  }

  function button(className, label) {
    const b = doc.createElement("button");
    b.className = className;
    b.setAttribute("type", "button");
    b.setAttribute("role", "menuitem");
    b.textContent = label;
    return b;
  }

  function note(text) {
    const p = doc.createElement("p");
    p.className = "xb-msg-menu-note";
    p.textContent = text;
    return p;
  }

  function focusFirst(container) {
    const first = container.querySelector(".xb-msg-menu-item");
    if (first && typeof first.focus === "function") first.focus();
  }

  /** Move focus between the items of whichever panel is showing. */
  function moveFocus(container, delta) {
    const items = container.querySelectorAll(".xb-msg-menu-item");
    if (!items.length) return;
    const active = doc.activeElement;
    let index = -1;
    for (let i = 0; i < items.length; i += 1) if (items[i] === active) index = i;
    const next = (index + delta + items.length) % items.length;
    if (typeof items[next].focus === "function") items[next].focus();
  }

  function position(menu, x, y) {
    // `position: fixed` in the stylesheet; the numbers are viewport coordinates.
    // Clamped so a right-click near the edge does not draw the menu off-screen,
    // which on a phone is indistinguishable from the menu never opening.
    const w = view && typeof view.innerWidth === "number" ? view.innerWidth : 0;
    const h = view && typeof view.innerHeight === "number" ? view.innerHeight : 0;
    const left = w ? Math.max(4, Math.min(x, w - 232)) : x;
    const top = h ? Math.max(4, Math.min(y, h - 180)) : y;
    menu.style.left = `${Math.round(left)}px`;
    menu.style.top = `${Math.round(top)}px`;
  }

  /**
   * Open the overlay for one row.
   *
   * @param {any} row
   * @param {{x?: number, y?: number, viaKeyboard?: boolean}} [where]
   */
  function openFor(row, where = {}) {
    close();
    if (!row || !row.dataset || !row.dataset.msgId) return null;
    const messageId = row.dataset.msgId;
    const canDelete = canDeleteRow(row, {
      selfUserId: getSelfUserId(),
      canModerate: getViewerCanModerate(),
    });

    const scrim = doc.createElement("div");
    scrim.className = "xb-msg-menu-scrim";
    const menu = doc.createElement("div");
    menu.className = "xb-msg-menu";
    menu.setAttribute("role", "menu");
    menu.setAttribute("aria-label", "Message actions");
    scrim.appendChild(menu);

    const state = {
      scrim,
      menu,
      row,
      messageId,
      returnFocusTo:
        where.viaKeyboard && typeof row.focus === "function" ? row : null,
      onKeydown: null,
    };

    function clearMenu() {
      while (menu.firstChild) menu.removeChild(menu.firstChild);
    }

    function setStatus(text, isError) {
      let line = menu.querySelector(".xb-msg-menu-status");
      if (!line) {
        line = doc.createElement("p");
        line.className = "xb-msg-menu-status";
        menu.appendChild(line);
      }
      line.textContent = text;
      if (isError) line.classList.add("is-error");
      else line.classList.remove("is-error");
    }

    async function runDelete(scope, pressed) {
      const teamId = getActiveTeamId();
      if (!teamId) {
        setStatus("No team is open.", true);
        return;
      }
      for (const b of menu.querySelectorAll(".xb-msg-menu-item")) b.disabled = true;
      setStatus(BUSY_TEXT, false);
      let response = null;
      try {
        response = await cfg.deleteMessage(teamId, messageId, scope);
      } catch {
        // A dead network is not a refusal, and it must not read like one.
        setStatus("Could not reach the server. Try again.", true);
        for (const b of menu.querySelectorAll(".xb-msg-menu-item")) b.disabled = false;
        return;
      }
      const status = response && typeof response.status === "number" ? response.status : 0;
      const ok = response && (response.ok === true || (status >= 200 && status < 300));
      if (!ok) {
        setStatus(deleteErrorText(status), true);
        for (const b of menu.querySelectorAll(".xb-msg-menu-item")) b.disabled = false;
        return;
      }
      // The server also publishes to the team channel, and this client is on it —
      // so the row usually disappears through the realtime path. Removing it here
      // too is what keeps the feature working on a dropped socket, and both paths
      // are idempotent because removal is keyed on the id.
      close();
      onDeleted(messageId);
      if (pressed) { /* nothing else — the row is gone */ }
    }

    function showConfirm() {
      clearMenu();
      const title = doc.createElement("p");
      title.className = "xb-msg-menu-title";
      title.textContent = CONFIRM_TITLE;
      menu.appendChild(title);

      // The two outcomes, in the order that makes the safer one the near one.
      const justMessage = button("xb-msg-menu-item", CONFIRM_MESSAGE_LABEL);
      justMessage.addEventListener("click", () =>
        runDelete(DELETE_SCOPE_MESSAGE, justMessage),
      );
      menu.appendChild(justMessage);
      menu.appendChild(note(CONFIRM_MESSAGE_NOTE));

      const withBrain = button("xb-msg-menu-item is-danger", CONFIRM_BRAIN_LABEL);
      withBrain.addEventListener("click", () =>
        runDelete(DELETE_SCOPE_MESSAGE_AND_BRAIN, withBrain),
      );
      menu.appendChild(withBrain);
      menu.appendChild(note(CONFIRM_BRAIN_NOTE));

      // Cancel is LAST in the DOM and FIRST in the focus order. Removing the
      // memory is not undoable by the person doing it, so the keyboard's default
      // answer is "no" — the wider outcome is always a deliberate second press.
      const cancel = button("xb-msg-menu-item is-quiet", CONFIRM_CANCEL_LABEL);
      cancel.addEventListener("click", () => close());
      menu.appendChild(cancel);
      if (typeof cancel.focus === "function") cancel.focus();
    }

    function showActions() {
      clearMenu();
      const ctx = { row, messageId, canDelete, close, showConfirm };
      let drawn = 0;
      for (const action of actions) {
        if (typeof action.isVisible === "function" && !action.isVisible(ctx)) continue;
        const b = button(
          `xb-msg-menu-item${action.danger ? " is-danger" : ""}`,
          action.label,
        );
        b.addEventListener("click", () => action.run(ctx));
        menu.appendChild(b);
        drawn += 1;
      }
      if (drawn === 0) {
        // Nothing this person may do to this message. Said out loud rather than
        // drawn as an empty box or, worse, as a control that answers 403.
        const empty = doc.createElement("p");
        empty.className = "xb-msg-menu-note";
        empty.textContent = "No actions for this message.";
        menu.appendChild(empty);
      }
      focusFirst(menu);
    }

    state.onKeydown = (event) => {
      if (!event) return;
      if (event.key === "Escape") {
        if (typeof event.preventDefault === "function") event.preventDefault();
        close();
        return;
      }
      if (event.key === "ArrowDown") {
        if (typeof event.preventDefault === "function") event.preventDefault();
        moveFocus(menu, 1);
        return;
      }
      if (event.key === "ArrowUp") {
        if (typeof event.preventDefault === "function") event.preventDefault();
        moveFocus(menu, -1);
      }
    };
    doc.addEventListener("keydown", state.onKeydown, true);

    // A tap anywhere off the menu closes it. The scrim covers the viewport, so
    // there is no document-level listener to leak and no way for a tap to miss.
    scrim.addEventListener("pointerdown", (event) => {
      const target = event && event.target;
      if (target && menu.contains && menu.contains(target)) return;
      close();
    });
    // Some engines deliver only `mousedown` for a synthetic outside click.
    scrim.addEventListener("mousedown", (event) => {
      const target = event && event.target;
      if (target && menu.contains && menu.contains(target)) return;
      close();
    });

    open = state;
    if (doc.body && typeof doc.body.appendChild === "function") {
      doc.body.appendChild(scrim);
    } else {
      listEl.appendChild(scrim);
    }
    position(menu, Number(where.x) || 0, Number(where.y) || 0);
    showActions();
    return state;
  }

  // ---- gestures ---------------------------------------------------------

  function onContextMenu(event) {
    const row = rowFrom(event && event.target);
    if (!row) return;
    // Ours now. Without this the browser's own menu covers it, and on Android the
    // long-press would open both.
    if (typeof event.preventDefault === "function") event.preventDefault();
    cancelPending();
    const hasCoords = Number(event.clientX) > 0 || Number(event.clientY) > 0;
    openFor(row, {
      x: hasCoords ? event.clientX : 0,
      y: hasCoords ? event.clientY : 0,
      // No coordinates means the ContextMenu key or Shift+F10 — a keyboard
      // invocation, which is where the focus has to be given back.
      viaKeyboard: !hasCoords,
    });
  }

  /** Touch and pen only. A mouse has a right button and uses it. */
  function isTouchLike(event) {
    const type = event && event.pointerType;
    return type === "touch" || type === "pen";
  }

  function onPointerDown(event) {
    if (open) return;
    if (!isTouchLike(event)) return;
    const row = rowFrom(event.target);
    if (!row) return;
    cancelPending();
    const startX = Number(event.clientX) || 0;
    const startY = Number(event.clientY) || 0;
    pending = {
      row,
      startX,
      startY,
      timer: setTimer(() => {
        const held = pending;
        pending = null;
        if (!held) return;
        // Checked at FIRE time, not at press time: on touch the platform's own
        // long-press starts a text selection at roughly this moment, and opening
        // a menu on top of someone highlighting a quote is the thing this guard
        // exists to prevent.
        if (getSelectionText().trim() !== "") return;
        openFor(held.row, { x: held.startX, y: held.startY });
      }, longPressMs),
    };
  }

  function onPointerMove(event) {
    if (!pending) return;
    const dx = (Number(event.clientX) || 0) - pending.startX;
    const dy = (Number(event.clientY) || 0) - pending.startY;
    if (Math.abs(dx) > LONG_PRESS_MOVE_TOLERANCE_PX ||
        Math.abs(dy) > LONG_PRESS_MOVE_TOLERANCE_PX) {
      cancelPending();
    }
  }

  /**
   * A scroll cancels a pending press outright.
   *
   * Belt and braces with the movement tolerance: a momentum scroll can carry the
   * thread without further pointermove events, and the finger that started it is
   * not asking for a menu.
   */
  function onScroll() {
    cancelPending();
  }

  function onSelectionChange() {
    if (!pending) return;
    if (getSelectionText().trim() !== "") cancelPending();
  }

  // ---- keyboard on the thread ------------------------------------------
  //
  // The list itself is the tab stop, and rows are focused from it. Doing it this
  // way keeps render.js out of the keyboard story entirely: nothing has to put a
  // tabindex on a row at build time, so a row that arrives over the websocket is
  // reachable on exactly the same terms as one that arrived in the history.

  function rows() {
    const out = [];
    for (const child of listEl.children || []) {
      if (child.dataset && child.dataset.msgId) out.push(child);
    }
    return out;
  }

  function focusRow(row) {
    if (!row) return;
    row.tabIndex = -1;
    if (typeof row.setAttribute === "function") row.setAttribute("tabindex", "-1");
    if (typeof row.focus === "function") row.focus();
  }

  function onListKeydown(event) {
    if (open) return; // the overlay owns the keyboard while it is up
    const key = event && event.key;
    if (!key) return;
    const all = rows();
    if (!all.length) return;
    const current = rowFrom(event.target);

    if (key === "ArrowUp" || key === "ArrowDown") {
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (!current) {
        // Entering the thread from the list itself: the newest message is where
        // the eye already is, so that is where the keyboard lands.
        focusRow(key === "ArrowUp" ? all[all.length - 1] : all[0]);
        return;
      }
      const index = all.indexOf(current);
      const next = key === "ArrowUp" ? index - 1 : index + 1;
      if (next >= 0 && next < all.length) focusRow(all[next]);
      return;
    }
    if (key === "Enter" || key === " " || key === "Spacebar" || key === "ContextMenu") {
      if (!current) return;
      if (typeof event.preventDefault === "function") event.preventDefault();
      openFor(current, { viaKeyboard: true });
    }
  }

  let attached = false;

  function attach() {
    if (attached) return;
    attached = true;
    // The list becomes a single tab stop rather than one per message: a thread of
    // four hundred bubbles must not be four hundred presses of Tab to get past.
    listEl.tabIndex = 0;
    if (typeof listEl.setAttribute === "function") {
      listEl.setAttribute("tabindex", "0");
    }
    listEl.addEventListener("contextmenu", onContextMenu);
    listEl.addEventListener("pointerdown", onPointerDown);
    listEl.addEventListener("pointermove", onPointerMove);
    listEl.addEventListener("pointerup", cancelPending);
    listEl.addEventListener("pointercancel", cancelPending);
    listEl.addEventListener("pointerleave", cancelPending);
    listEl.addEventListener("keydown", onListKeydown);
    if (scrollEl && typeof scrollEl.addEventListener === "function") {
      scrollEl.addEventListener("scroll", onScroll);
    }
    if (typeof doc.addEventListener === "function") {
      doc.addEventListener("selectionchange", onSelectionChange);
    }
  }

  function dispose() {
    close();
    if (!attached) return;
    attached = false;
    listEl.removeEventListener("contextmenu", onContextMenu);
    listEl.removeEventListener("pointerdown", onPointerDown);
    listEl.removeEventListener("pointermove", onPointerMove);
    listEl.removeEventListener("pointerup", cancelPending);
    listEl.removeEventListener("pointercancel", cancelPending);
    listEl.removeEventListener("pointerleave", cancelPending);
    listEl.removeEventListener("keydown", onListKeydown);
    if (scrollEl && typeof scrollEl.removeEventListener === "function") {
      scrollEl.removeEventListener("scroll", onScroll);
    }
    if (typeof doc.removeEventListener === "function") {
      doc.removeEventListener("selectionchange", onSelectionChange);
    }
  }

  return {
    attach,
    dispose,
    open: openFor,
    close,
    isOpen: () => open !== null,
    registerAction,
  };
}
