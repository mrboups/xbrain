/**
 * Tests for the per-message actions overlay and the delete path around it.
 *
 * Subject: packages/chat-core/message_menu.js — the SOURCE, not a generated copy
 * (test_chat_core_sync.mjs separately proves the copies match), plus the frame
 * routing in publication.js / realtime.js and the request shape in api.js.
 *
 * What is locked here:
 *   (a) THE GESTURE GUARDS. A long-press abandons itself on drift, on a scroll,
 *       on a selection present when it fires, and on a selection that appears
 *       mid-press — a menu that opens under a moving finger, or on top of
 *       somebody highlighting a quote, is worse than no menu. A mouse never arms
 *       it at all; it has a right button.
 *   (b) DISMISSAL. Escape closes, a tap on the scrim closes, a tap INSIDE does
 *       not, and only one overlay is ever open.
 *   (c) NO POINTER NEEDED. The list is one tab stop, arrows walk the rows, Enter
 *       opens the same overlay, and the focus goes back to the row afterwards.
 *   (d) THE CONTROL MATCHES THE SERVER'S RULE. Your own message always; someone
 *       else's only where the server said `can_moderate`; an agent frame is
 *       nobody's own. Where nothing is allowed the menu says so rather than
 *       drawing a button that answers 403.
 *   (e) THE TWO OUTCOMES ARE DIFFERENT AND LABELLED. Two scopes, two request
 *       values, distinct copy, only the wider one styled destructive, and the
 *       focus on "Keep it" — removing the memory is not undoable by the person
 *       doing it.
 *   (f) A REFUSAL IS THE STATUS CODE'S WORDS, NEVER THE BODY'S. A server's error
 *       text is not written for the person holding the phone.
 *   (g) THE DELETION REACHES A SECOND CLIENT. A `message_deleted` frame takes the
 *       row off a screen that never opened a menu, and a repeat frame is a no-op.
 *   (h) BOTH SURFACES DRIVE THE SHARED MODULE, the PWA precaches it, and the
 *       shell cache name moved (a missed bump looks exactly like a broken fix).
 *
 * Pure node test — a hand-rolled DOM stub with real event bubbling, mirroring the
 * house style. Picked up by run_tests.mjs (file name starts with test_).
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import {
  CONFIRM_BRAIN_LABEL,
  CONFIRM_CANCEL_LABEL,
  CONFIRM_MESSAGE_LABEL,
  CONFIRM_TITLE,
  DELETE_SCOPE_MESSAGE,
  DELETE_SCOPE_MESSAGE_AND_BRAIN,
  LONG_PRESS_MOVE_TOLERANCE_PX,
  MENU_LABEL_DELETE,
  canDeleteRow,
  createMessageMenu,
  deleteErrorText,
  removeMessageRow,
} from "../../packages/chat-core/message_menu.js";
import { createPublicationRouter } from "../../packages/chat-core/publication.js";
import { publicationKey } from "../../packages/chat-core/realtime.js";
import { createApi } from "../../packages/chat-core/api.js";

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
// DOM stub — small, and with REAL bubbling, because the whole module is event
// delegation on the list. A stub that fired only on the target would let a
// broken delegation pass.
// ---------------------------------------------------------------------------

class El {
  constructor(doc, tag) {
    this.ownerDocument = doc;
    this.tagName = String(tag).toUpperCase();
    this.className = "";
    this.dataset = {};
    this.style = {};
    this.attributes = {};
    this.children = [];
    this.parentNode = null;
    this.listeners = {};
    this._text = "";
    this.disabled = false;
  }

  get classList() {
    const self = this;
    return {
      add(...names) {
        const cur = self.className.split(/\s+/).filter(Boolean);
        for (const n of names) if (!cur.includes(n)) cur.push(n);
        self.className = cur.join(" ");
      },
      remove(...names) {
        self.className = self.className
          .split(/\s+/)
          .filter(Boolean)
          .filter((c) => !names.includes(c))
          .join(" ");
      },
      contains(n) {
        return self.className.split(/\s+/).filter(Boolean).includes(n);
      },
    };
  }

  get textContent() {
    if (this.children.length === 0) return this._text;
    return this._text + this.children.map((c) => c.textContent).join("");
  }

  set textContent(v) {
    this._text = String(v);
    this.children = [];
  }

  get firstChild() {
    return this.children[0] || null;
  }

  appendChild(node) {
    node.parentNode = this;
    this.children.push(node);
    return node;
  }

  insertBefore(node, ref) {
    node.parentNode = this;
    const i = ref ? this.children.indexOf(ref) : -1;
    if (i === -1) this.children.push(node);
    else this.children.splice(i, 0, node);
    return node;
  }

  removeChild(node) {
    const i = this.children.indexOf(node);
    if (i !== -1) this.children.splice(i, 1);
    node.parentNode = null;
    return node;
  }

  remove() {
    if (this.parentNode) this.parentNode.removeChild(this);
  }

  addEventListener(type, fn) {
    (this.listeners[type] = this.listeners[type] || []).push(fn);
  }

  removeEventListener(type, fn) {
    const list = this.listeners[type] || [];
    const i = list.indexOf(fn);
    if (i !== -1) list.splice(i, 1);
  }

  contains(node) {
    if (node === this) return true;
    for (const c of this.children || []) {
      if (c === node || (c.contains && c.contains(node))) return true;
    }
    return false;
  }

  focus() {
    if (this.ownerDocument) this.ownerDocument.activeElement = this;
  }

  blur() {
    if (this.ownerDocument && this.ownerDocument.activeElement === this) {
      this.ownerDocument.activeElement = null;
    }
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name)
      ? this.attributes[name]
      : null;
  }

  matches(sel) {
    for (const part of sel.split(",").map((s) => s.trim())) {
      if (part.startsWith(".") && this.classList.contains(part.slice(1))) return true;
      const m = /^\[([\w-]+)="([^"]*)"\]$/.exec(part);
      if (m) {
        const key = m[1]
          .replace(/^data-/, "")
          .replace(/-([a-z])/g, (_, c) => c.toUpperCase());
        if (this.dataset[key] === m[2]) return true;
      }
    }
    return false;
  }

  querySelectorAll(sel) {
    const out = [];
    const walk = (node) => {
      for (const c of node.children || []) {
        if (c.matches && c.matches(sel)) out.push(c);
        walk(c);
      }
    };
    walk(this);
    return out;
  }

  querySelector(sel) {
    return this.querySelectorAll(sel)[0] || null;
  }
}

function makeDoc() {
  const docListeners = {};
  const doc = {
    activeElement: null,
    createElement: (tag) => new El(doc, tag),
    addEventListener: (type, fn) => {
      (docListeners[type] = docListeners[type] || []).push(fn);
    },
    removeEventListener: (type, fn) => {
      const list = docListeners[type] || [];
      const i = list.indexOf(fn);
      if (i !== -1) list.splice(i, 1);
    },
    countListeners: (type) => (docListeners[type] || []).length,
    fire: (type, event) => {
      for (const fn of [...(docListeners[type] || [])]) fn({ type, ...event });
    },
    defaultView: { innerWidth: 400, innerHeight: 700 },
  };
  doc.body = new El(doc, "body");
  return doc;
}

/** Fire `type` at `el` and let it bubble to every ancestor, target intact. */
function dispatch(el, type, props = {}) {
  let prevented = false;
  const event = {
    type,
    target: el,
    preventDefault: () => {
      prevented = true;
    },
    ...props,
  };
  let node = el;
  let guard = 0;
  while (node && guard < 50) {
    for (const fn of [...(node.listeners[type] || [])]) fn(event);
    node = node.parentNode;
    guard += 1;
  }
  return { prevented };
}

/**
 * A thread with three rows: one mine, one a teammate's, one the agent's.
 * `timers` is a manual clock — the long-press fires only when the test says so.
 */
function makeHarness(overrides = {}) {
  const doc = makeDoc();
  const listEl = new El(doc, "div");
  const scrollEl = new El(doc, "div");
  doc.body.appendChild(listEl);

  function addRow(id, authorKey) {
    const row = new El(doc, "div");
    row.className = "xb-msg";
    row.dataset.msgId = id;
    row.dataset.authorKey = authorKey;
    const bubble = new El(doc, "div");
    bubble.className = "xb-msg-bubble";
    row.appendChild(bubble);
    listEl.appendChild(row);
    return { row, bubble };
  }

  const mine = addRow("m-mine", "user:me-1");
  const theirs = addRow("m-theirs", "user:other-1");
  const agent = addRow("m-agent", "agent:chad");

  const timers = [];
  const deleted = [];
  const removedLocally = [];
  let selection = "";
  let nextResponse = { ok: true, status: 200 };

  const menu = createMessageMenu({
    doc,
    listEl,
    scrollEl,
    getActiveTeamId: () => "team-1",
    getSelfUserId: () => "me-1",
    getViewerCanModerate: () => Boolean(harness.canModerate),
    getSelectionText: () => selection,
    setTimer: (fn) => {
      timers.push(fn);
      return timers.length - 1;
    },
    clearTimer: (id) => {
      timers[id] = null;
    },
    deleteMessage: async (teamId, messageId, scope) => {
      deleted.push({ teamId, messageId, scope });
      return nextResponse;
    },
    onDeleted: (id) => removedLocally.push(id),
    ...overrides,
  });
  menu.attach();

  const harness = {
    doc,
    listEl,
    scrollEl,
    mine,
    theirs,
    agent,
    menu,
    deleted,
    removedLocally,
    canModerate: false,
    setSelection: (s) => {
      selection = s;
    },
    setResponse: (r) => {
      nextResponse = r;
    },
    /** Run every pending long-press timer. */
    tick: () => {
      const pending = timers.slice();
      timers.length = 0;
      for (const fn of pending) if (fn) fn();
    },
    panel: () => doc.body.querySelector(".xb-msg-menu"),
    items: () =>
      (doc.body.querySelector(".xb-msg-menu") || { querySelectorAll: () => [] })
        .querySelectorAll(".xb-msg-menu-item")
        .map((b) => b.textContent),
    itemNamed: (label) =>
      (doc.body.querySelector(".xb-msg-menu") || { querySelectorAll: () => [] })
        .querySelectorAll(".xb-msg-menu-item")
        .find((b) => b.textContent === label),
  };
  return harness;
}

// ---------------------------------------------------------------------------
// (a) the gesture guards
// ---------------------------------------------------------------------------

await test("a long press on a bubble opens the overlay", () => {
  const h = makeHarness();
  dispatch(h.mine.bubble, "pointerdown", {
    pointerType: "touch",
    clientX: 40,
    clientY: 300,
  });
  assert.equal(h.menu.isOpen(), false, "nothing may open before the press lands");
  h.tick();
  assert.equal(h.menu.isOpen(), true);
  assert.ok(h.panel(), "the overlay must be in the document");
});

await test("a long press that drifts is a scroll, and opens nothing", () => {
  const h = makeHarness();
  dispatch(h.mine.bubble, "pointerdown", {
    pointerType: "touch",
    clientX: 40,
    clientY: 300,
  });
  dispatch(h.mine.bubble, "pointermove", {
    clientX: 40,
    clientY: 300 + LONG_PRESS_MOVE_TOLERANCE_PX + 1,
  });
  h.tick();
  assert.equal(h.menu.isOpen(), false);
  assert.equal(h.panel(), null);
});

await test("a press that stays within the tolerance still opens", () => {
  const h = makeHarness();
  dispatch(h.mine.bubble, "pointerdown", {
    pointerType: "touch",
    clientX: 40,
    clientY: 300,
  });
  dispatch(h.mine.bubble, "pointermove", {
    clientX: 40 + LONG_PRESS_MOVE_TOLERANCE_PX - 1,
    clientY: 300,
  });
  h.tick();
  assert.equal(h.menu.isOpen(), true, "a fingertip does not hold perfectly still");
});

await test("a scroll of the thread abandons a pending press", () => {
  const h = makeHarness();
  dispatch(h.mine.bubble, "pointerdown", {
    pointerType: "touch",
    clientX: 40,
    clientY: 300,
  });
  // Momentum scrolling can carry the thread without another pointermove.
  dispatch(h.scrollEl, "scroll", {});
  h.tick();
  assert.equal(h.menu.isOpen(), false);
});

await test("a press over selected text opens nothing", () => {
  const h = makeHarness();
  h.setSelection("the signed contract");
  dispatch(h.mine.bubble, "pointerdown", {
    pointerType: "touch",
    clientX: 40,
    clientY: 300,
  });
  h.tick();
  assert.equal(
    h.menu.isOpen(),
    false,
    "somebody highlighting a quote is not asking for a menu",
  );
});

await test("a selection that appears mid-press abandons it THERE, not at the end", () => {
  const h = makeHarness();
  dispatch(h.mine.bubble, "pointerdown", {
    pointerType: "touch",
    clientX: 40,
    clientY: 300,
  });
  // The finger drags through the text: a selection appears...
  h.setSelection("half a sentence");
  h.doc.fire("selectionchange", {});
  // ...and then collapses again before the press would have fired. The press was
  // already spent on the selection gesture, so re-checking at fire time is not
  // enough — it has to be abandoned at the moment the selection appeared.
  h.setSelection("");
  h.tick();
  assert.equal(
    h.menu.isOpen(),
    false,
    "a press consumed by a selection gesture must not open a menu afterwards",
  );
});

await test("lifting the finger early abandons the press", () => {
  const h = makeHarness();
  dispatch(h.mine.bubble, "pointerdown", {
    pointerType: "touch",
    clientX: 40,
    clientY: 300,
  });
  dispatch(h.mine.bubble, "pointerup", {});
  h.tick();
  assert.equal(h.menu.isOpen(), false, "a tap is not a press");
});

await test("a mouse never arms the long press — it has a right button", () => {
  const h = makeHarness();
  dispatch(h.mine.bubble, "pointerdown", {
    pointerType: "mouse",
    clientX: 40,
    clientY: 300,
  });
  h.tick();
  assert.equal(h.menu.isOpen(), false);
});

await test("right-click opens it and takes the browser's own menu away", () => {
  const h = makeHarness();
  const { prevented } = dispatch(h.mine.bubble, "contextmenu", {
    clientX: 120,
    clientY: 200,
  });
  assert.equal(h.menu.isOpen(), true);
  assert.equal(prevented, true, "otherwise the native menu covers ours");
});

await test("a right-click outside any row opens nothing", () => {
  const h = makeHarness();
  const { prevented } = dispatch(h.listEl, "contextmenu", {
    clientX: 10,
    clientY: 10,
  });
  assert.equal(h.menu.isOpen(), false);
  assert.equal(prevented, false, "the page keeps its own menu where we have none");
});

// ---------------------------------------------------------------------------
// (b) dismissal
// ---------------------------------------------------------------------------

await test("Escape closes it", () => {
  const h = makeHarness();
  dispatch(h.mine.bubble, "contextmenu", { clientX: 10, clientY: 10 });
  assert.equal(h.menu.isOpen(), true);
  h.doc.fire("keydown", { key: "Escape", preventDefault() {} });
  assert.equal(h.menu.isOpen(), false);
  assert.equal(h.panel(), null);
});

await test("a tap outside closes it, a tap inside does not", () => {
  const h = makeHarness();
  dispatch(h.mine.bubble, "contextmenu", { clientX: 10, clientY: 10 });
  const scrim = h.doc.body.querySelector(".xb-msg-menu-scrim");
  const panel = h.panel();

  dispatch(panel, "pointerdown", {});
  assert.equal(h.menu.isOpen(), true, "a tap on the menu must not dismiss it");

  dispatch(scrim, "pointerdown", {});
  assert.equal(h.menu.isOpen(), false);
});

await test("closing takes the document keydown listener with it", () => {
  const h = makeHarness();
  const before = h.doc.countListeners("keydown");
  dispatch(h.mine.bubble, "contextmenu", { clientX: 10, clientY: 10 });
  assert.equal(h.doc.countListeners("keydown"), before + 1);
  h.doc.fire("keydown", { key: "Escape", preventDefault() {} });
  assert.equal(
    h.doc.countListeners("keydown"),
    before,
    "a handler that outlives its overlay is a leak per opened menu",
  );
});

await test("only one overlay is ever open", () => {
  const h = makeHarness();
  dispatch(h.mine.bubble, "contextmenu", { clientX: 10, clientY: 10 });
  dispatch(h.mine.bubble, "contextmenu", { clientX: 30, clientY: 30 });
  assert.equal(
    h.doc.body.querySelectorAll(".xb-msg-menu-scrim").length,
    1,
    "two scrims means one of them can never be dismissed",
  );
});

// ---------------------------------------------------------------------------
// (c) reachable without a pointer
// ---------------------------------------------------------------------------

await test("the list is one tab stop, not one per message", () => {
  const h = makeHarness();
  assert.equal(h.listEl.getAttribute("tabindex"), "0");
  assert.equal(
    h.mine.row.getAttribute("tabindex"),
    null,
    "four hundred bubbles must not be four hundred presses of Tab",
  );
});

await test("arrows walk the rows and Enter opens the same overlay", () => {
  const h = makeHarness();
  // Entering from the list itself: ArrowUp lands on the newest message.
  dispatch(h.listEl, "keydown", { key: "ArrowUp" });
  assert.equal(h.doc.activeElement, h.agent.row);
  dispatch(h.agent.row, "keydown", { key: "ArrowUp" });
  assert.equal(h.doc.activeElement, h.theirs.row);
  dispatch(h.theirs.row, "keydown", { key: "ArrowDown" });
  assert.equal(h.doc.activeElement, h.agent.row);

  dispatch(h.agent.row, "keydown", { key: "Enter" });
  assert.equal(h.menu.isOpen(), true, "the keyboard reaches the same actions");
});

await test("a keyboard-opened menu gives the focus back to its row", () => {
  const h = makeHarness();
  dispatch(h.listEl, "keydown", { key: "ArrowDown" });
  assert.equal(h.doc.activeElement, h.mine.row);
  dispatch(h.mine.row, "keydown", { key: "Enter" });
  h.doc.fire("keydown", { key: "Escape", preventDefault() {} });
  assert.equal(
    h.doc.activeElement,
    h.mine.row,
    "otherwise every menu strands the keyboard at the top of the document",
  );
});

await test("arrows move between the items of an open menu", () => {
  const h = makeHarness();
  dispatch(h.mine.bubble, "contextmenu", { clientX: 10, clientY: 10 });
  const first = h.doc.activeElement;
  assert.ok(first && first.classList.contains("xb-msg-menu-item"));
  h.doc.fire("keydown", { key: "ArrowDown", preventDefault() {} });
  // One action today, so the ring is a single item — the assertion is that the
  // handler runs and leaves the focus on a menu item, not on the document.
  assert.ok(h.doc.activeElement.classList.contains("xb-msg-menu-item"));
});

// ---------------------------------------------------------------------------
// (d) the control matches the server's rule
// ---------------------------------------------------------------------------

await test("canDeleteRow: yours always, someone else's only with moderation", () => {
  const doc = makeDoc();
  const mine = new El(doc, "div");
  mine.dataset.authorKey = "user:me-1";
  const theirs = new El(doc, "div");
  theirs.dataset.authorKey = "user:other-1";
  const agent = new El(doc, "div");
  agent.dataset.authorKey = "agent:chad";

  assert.equal(canDeleteRow(mine, { selfUserId: "me-1" }), true);
  assert.equal(canDeleteRow(theirs, { selfUserId: "me-1" }), false);
  assert.equal(
    canDeleteRow(theirs, { selfUserId: "me-1", canModerate: true }),
    true,
  );
  assert.equal(
    canDeleteRow(agent, { selfUserId: "me-1" }),
    false,
    "an agent frame is nobody's own message",
  );
  assert.equal(
    canDeleteRow(agent, { selfUserId: "me-1", canModerate: true }),
    true,
  );
  assert.equal(canDeleteRow(mine, {}), false, "no identity means no ownership");
});

await test("a teammate's message offers no delete until the server allows it", () => {
  const h = makeHarness();
  dispatch(h.theirs.bubble, "contextmenu", { clientX: 10, clientY: 10 });
  assert.deepEqual(h.items(), [], "no control the server would refuse");
  assert.ok(
    h.panel().textContent.includes("No actions for this message."),
    "an empty box is worse than a sentence",
  );

  h.menu.close();
  h.canModerate = true;
  dispatch(h.theirs.bubble, "contextmenu", { clientX: 10, clientY: 10 });
  assert.deepEqual(h.items(), [MENU_LABEL_DELETE]);
});

await test("your own message always offers it", () => {
  const h = makeHarness();
  dispatch(h.mine.bubble, "contextmenu", { clientX: 10, clientY: 10 });
  assert.deepEqual(h.items(), [MENU_LABEL_DELETE]);
});

// ---------------------------------------------------------------------------
// (e) the two outcomes
// ---------------------------------------------------------------------------

function openConfirm(h) {
  dispatch(h.mine.bubble, "contextmenu", { clientX: 10, clientY: 10 });
  dispatch(h.itemNamed(MENU_LABEL_DELETE), "click", {});
}

await test("the confirm step names both outcomes and asks the question", () => {
  const h = makeHarness();
  openConfirm(h);
  const text = h.panel().textContent;
  assert.ok(text.includes(CONFIRM_TITLE));
  assert.deepEqual(h.items(), [
    CONFIRM_MESSAGE_LABEL,
    CONFIRM_BRAIN_LABEL,
    CONFIRM_CANCEL_LABEL,
  ]);
  assert.ok(
    text.includes("stays in the team's memory"),
    "message-only has to say what it LEAVES, or nobody can choose between them",
  );
  assert.ok(
    text.includes("30 days"),
    "'removed' is honest and 'deleted forever' is not — say which",
  );
});

await test("the wider outcome is the marked one, and not the default", () => {
  const h = makeHarness();
  openConfirm(h);
  assert.equal(
    h.itemNamed(CONFIRM_BRAIN_LABEL).classList.contains("is-danger"),
    true,
  );
  assert.equal(
    h.itemNamed(CONFIRM_MESSAGE_LABEL).classList.contains("is-danger"),
    false,
  );
  assert.equal(
    h.doc.activeElement.textContent,
    CONFIRM_CANCEL_LABEL,
    "removing the memory is not undoable by the person doing it, so the "
      + "keyboard's default answer is no",
  );
});

await test("cancel closes without asking the server anything", () => {
  const h = makeHarness();
  openConfirm(h);
  dispatch(h.itemNamed(CONFIRM_CANCEL_LABEL), "click", {});
  assert.equal(h.menu.isOpen(), false);
  assert.deepEqual(h.deleted, []);
});

await test("each option sends its OWN scope", async () => {
  const h = makeHarness();
  openConfirm(h);
  dispatch(h.itemNamed(CONFIRM_MESSAGE_LABEL), "click", {});
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(h.deleted, [
    { teamId: "team-1", messageId: "m-mine", scope: DELETE_SCOPE_MESSAGE },
  ]);

  const h2 = makeHarness();
  openConfirm(h2);
  dispatch(h2.itemNamed(CONFIRM_BRAIN_LABEL), "click", {});
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(h2.deleted, [
    {
      teamId: "team-1",
      messageId: "m-mine",
      scope: DELETE_SCOPE_MESSAGE_AND_BRAIN,
    },
  ]);
  assert.notEqual(
    DELETE_SCOPE_MESSAGE,
    DELETE_SCOPE_MESSAGE_AND_BRAIN,
    "two outcomes, two values",
  );
});

await test("a successful removal closes the menu and reports the id once", async () => {
  const h = makeHarness();
  openConfirm(h);
  dispatch(h.itemNamed(CONFIRM_MESSAGE_LABEL), "click", {});
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(h.menu.isOpen(), false);
  assert.deepEqual(h.removedLocally, ["m-mine"]);
});

// ---------------------------------------------------------------------------
// (f) a refusal speaks the client's own words
// ---------------------------------------------------------------------------

await test("a 403 is explained, and the server's body never reaches the screen", async () => {
  const h = makeHarness();
  h.setResponse({
    ok: false,
    status: 403,
    text: async () => "psycopg2.errors.InsufficientPrivilege on team_messages",
  });
  openConfirm(h);
  dispatch(h.itemNamed(CONFIRM_BRAIN_LABEL), "click", {});
  await new Promise((r) => setTimeout(r, 0));

  assert.equal(h.menu.isOpen(), true, "a refusal leaves the menu up to be read");
  const text = h.panel().textContent;
  assert.ok(text.includes("You can only remove your own messages"));
  assert.ok(
    !text.includes("psycopg2"),
    "a server's error text is not written for the person holding the phone",
  );
  assert.deepEqual(h.removedLocally, [], "nothing may leave the screen");
});

await test("every refusal has its own sentence, and none of them is a stack trace", () => {
  assert.match(deleteErrorText(403), /your own messages/);
  assert.match(deleteErrorText(404), /already gone/);
  assert.match(deleteErrorText(401), /signed out/);
  assert.match(deleteErrorText(500), /HTTP 500/);
  assert.notEqual(deleteErrorText(403), deleteErrorText(404));
});

await test("a dead network reads as a network problem, not as a refusal", async () => {
  const h = makeHarness({
    deleteMessage: async () => {
      throw new TypeError("Failed to fetch");
    },
  });
  openConfirm(h);
  dispatch(h.itemNamed(CONFIRM_MESSAGE_LABEL), "click", {});
  await new Promise((r) => setTimeout(r, 0));
  assert.ok(h.panel().textContent.includes("Could not reach the server"));
  assert.deepEqual(h.removedLocally, []);
});

// ---------------------------------------------------------------------------
// (g) the deletion reaches a second client
// ---------------------------------------------------------------------------

await test("removeMessageRow takes the row out, and repeats do nothing", () => {
  const doc = makeDoc();
  const listEl = new El(doc, "div");
  const row = new El(doc, "div");
  row.dataset.msgId = "m-1";
  listEl.appendChild(row);

  assert.equal(removeMessageRow(listEl, "m-1"), true);
  assert.equal(listEl.children.length, 0);
  assert.equal(removeMessageRow(listEl, "m-1"), false);
  assert.equal(removeMessageRow(listEl, "nope"), false);
});

await test("a message_deleted frame takes the row off a screen that never opened a menu", () => {
  const seen = [];
  const renderer = {
    renderMessage() {},
    renderAgentBubble() {},
    renderAgentFailure() {},
    syncDaySeparators() {},
    streamTextTarget: () => null,
    clearStreaming() {},
    scrollToBottom() {},
  };
  const route = createPublicationRouter({
    renderer,
    streamBuffer: { start() {}, append() {}, get: () => "", finalize() {} },
    onMessageDeleted: (id) => seen.push(id),
  });
  route({ type: "message_deleted", message_id: "m-9", scope: "message" });
  route({ type: "message_deleted", message_id: "m-9", scope: "message" });
  assert.deepEqual(seen, ["m-9", "m-9"], "the router reports; the surface dedupes");

  // A frame with no id, and a surface that ships no handler: neither may throw.
  route({ type: "message_deleted" });
  const bare = createPublicationRouter({
    renderer,
    streamBuffer: { start() {}, append() {}, get: () => "", finalize() {} },
  });
  bare({ type: "message_deleted", message_id: "m-9" });
  assert.deepEqual(seen, ["m-9", "m-9"]);
});

await test("a re-delivered delete frame is dropped by the id-keyed deduper", () => {
  const key = publicationKey("team:t1", {
    type: "message_deleted",
    message_id: "m-9",
  });
  assert.equal(key, "team:t1|message_deleted|m-9");
  assert.notEqual(
    key,
    publicationKey("team:t2", { type: "message_deleted", message_id: "m-9" }),
    "the same message id in two teams is two frames",
  );
  assert.equal(
    publicationKey("team:t1", { type: "message_deleted" }),
    null,
    "no id, no dedupe key",
  );
});

await test("deleteMessageRaw sends a DELETE with the scope in the query", async () => {
  const calls = [];
  const original = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    return { ok: true, status: 200, text: async () => "" };
  };
  try {
    const api = createApi({
      baseUrl: "https://api.example",
      getToken: async () => "tok",
    });
    await api.deleteMessageRaw("t1", "m 1/2", DELETE_SCOPE_MESSAGE_AND_BRAIN);
  } finally {
    globalThis.fetch = original;
  }
  assert.equal(calls.length, 1);
  assert.equal(calls[0].init.method, "DELETE");
  assert.equal(calls[0].init.headers.Authorization, "Bearer tok");
  assert.equal(
    calls[0].url,
    "https://api.example/v1/teams/t1/messages/m%201%2F2?scope=message_and_brain",
    "the id is encoded and the scope travels in the query, not a DELETE body",
  );
});

// ---------------------------------------------------------------------------
// (h) both surfaces drive it, and the shell knows about it
// ---------------------------------------------------------------------------

function read(...parts) {
  return readFileSync(join(REPO_ROOT, ...parts), "utf8");
}

await test("both surfaces import the shared module rather than forking it", () => {
  for (const [file, src] of [
    ["chrome-extension/popup.js", read("chrome-extension", "popup.js")],
    ["app-site/app/chat.js", read("app-site", "app", "chat.js")],
  ]) {
    assert.ok(
      /import\s*\{[^}]*createMessageMenu[^}]*\}\s*from\s*"\.\/chat_core\/message_menu\.js"/.test(
        src,
      ),
      `${file} must drive chat_core/message_menu.js`,
    );
    assert.ok(
      src.includes("onMessageDeleted"),
      `${file} must handle the deletion somebody else performed`,
    );
    assert.ok(
      src.includes("can_moderate"),
      `${file} must read the server's own answer about who may moderate`,
    );
  }
});

await test("the PWA precaches the module and moved its shell cache", () => {
  const sw = read("app-site", "app", "sw.js");
  assert.ok(
    sw.includes('"/app/chat_core/message_menu.js"'),
    "a shipped module missing from SHELL is a broken import when offline",
  );
  const name = /const CACHE = "([^"]+)"/.exec(sw);
  assert.ok(name, "sw.js must declare a CACHE name");
  assert.notEqual(
    name[1],
    "xb-app-shell-v9",
    "chat.js, app.css and three chat_core modules changed — without the bump a "
      + "returning phone serves the old chat against the new API",
  );
});

await test("the sync script names the module, so deleting it is a red gate", () => {
  const sync = read("scripts", "sync-chat-core.mjs");
  const block = /const REQUIRED_MODULES = \[([\s\S]*?)\];/.exec(sync);
  assert.ok(block, "sync-chat-core.mjs must declare REQUIRED_MODULES");
  assert.ok(
    block[1].includes('"message_menu.js"'),
    "an unnamed module can be deleted and the drift check would stay green",
  );
});

await test("the overlay is styled on both surfaces, in tokens not literals", () => {
  for (const [file, css] of [
    ["chrome-extension/popup.css", read("chrome-extension", "popup.css")],
    ["app-site/app/app.css", read("app-site", "app", "app.css")],
  ]) {
    for (const rule of [
      ".xb-msg-menu-scrim",
      ".xb-msg-menu-item",
      ".xb-msg-menu-item.is-danger",
      ".xb-msg-menu-note",
    ]) {
      assert.ok(css.includes(rule), `${file} is missing ${rule}`);
    }
    const block = css.slice(css.indexOf(".xb-msg-menu-scrim"));
    assert.ok(
      block.includes("var(--radius)") && block.includes("var(--destructive)"),
      `${file} must draw the overlay from the theme tokens, both themes at once`,
    );
    assert.ok(
      !/#[0-9a-fA-F]{6}/.test(block),
      `${file} must not hardcode a colour in the overlay`,
    );
  }
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
