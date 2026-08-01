/**
 * Tests for the shared renderer + publication router (Phase 27, Plan 27-02).
 *
 * Subject: packages/chat-core/render.js and packages/chat-core/publication.js —
 * the SOURCE, not a generated copy, so behaviour is proven where it is edited
 * (test_chat_core_sync.mjs separately proves the copies match).
 *
 * What is locked here:
 *   (a) the row classes the popup CSS hangs off — xb-msg + is-user / is-self /
 *       is-agent — and the author label living inside .xb-msg-meta;
 *   (b) an agent row emits .xb-msg-agent-label and never gets an author click
 *       listener (the agent is not someone you send a file to);
 *   (c) onAuthorClick: null leaves a teammate's name with NO listener, NO cursor
 *       and NO title — a surface without the people overlay must not ship a name
 *       that looks clickable and does nothing;
 *   (d) XSS (T-20-03-01 / T-27-02-01): `<img src=x onerror=...>` lands as TEXT.
 *       Two assertions, not one — the text node carries the raw string AND no
 *       child element was created from it;
 *   (e) the router calls renderMessage exactly once per `message` frame, and
 *       start -> chunk -> chunk -> end accumulates the deltas into the stream
 *       target and then drops the streaming class;
 *   (f) ANTI-FORK (D-27-04): the extension imports the shared modules instead of
 *       keeping its own copy of the render/route code.
 *
 * Pure node test — a hand-rolled DOM stub (no jsdom, no dependency), mirroring
 * the house style. Picked up by run_tests.mjs (file name starts with test_).
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { createRenderer } from "../../packages/chat-core/render.js";
import { createPublicationRouter } from "../../packages/chat-core/publication.js";
import { StreamBuffer } from "../../packages/chat-core/chat_stream.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, "..", "..");

let passed = 0;
let failed = 0;
function test(name, body) {
  try {
    body();
    console.log(`  PASS: ${name}`);
    passed++;
  } catch (e) {
    console.error(`  FAIL: ${name}`);
    console.error(`    ${e.stack || e.message}`);
    failed++;
  }
}

// ---------------------------------------------------------------------------
// Minimal DOM stub — an object graph with just the surface render.js touches.
// Deliberately hand-rolled: jsdom is not a repo dependency, and a stub this
// small makes it obvious WHICH DOM calls the renderer is allowed to make.
// ---------------------------------------------------------------------------

class TextNode {
  constructor(text) {
    this.nodeType = 3;
    this.textContent = String(text);
  }
}

class El {
  constructor(doc, tag) {
    this.ownerDocument = doc;
    this.tagName = String(tag).toUpperCase();
    this.className = "";
    this.dataset = {};
    this.style = {};
    this.children = [];
    this.parentNode = null;
    this.listeners = {};
    this._text = "";
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
    if (!ref) {
      this.children.push(node);
      return node;
    }
    const i = this.children.indexOf(ref);
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

  matches(sel) {
    if (sel.startsWith(".")) return this.classList.contains(sel.slice(1));
    const m = /^\[([\w-]+)="([^"]*)"\]$/.exec(sel);
    if (m) {
      const key = m[1]
        .replace(/^data-/, "")
        .replace(/-([a-z])/g, (_, c) => c.toUpperCase());
      return this.dataset[key] === m[2];
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

/** The recorded calls to the view's tab-opening capability. */
const opened = [];

function makeDoc() {
  const doc = {
    createElement: (tag) => new El(doc, tag),
    createTextNode: (t) => new TextNode(t),
    defaultView: {
      // Run the callback inline so a forced scroll is observable in a test.
      requestAnimationFrame: (fn) => fn(),
      open: (...args) => opened.push(args),
    },
  };
  return doc;
}

function makeHarness({
  onAuthorClick = null,
  self = "u-self",
  nameCache = { "u-mate": "Mate" },
} = {}) {
  const doc = makeDoc();
  const listEl = new El(doc, "div");
  const scrollEl = new El(doc, "div");
  scrollEl.scrollTop = 0;
  scrollEl.scrollHeight = 1000;
  scrollEl.clientHeight = 400;
  const renderer = createRenderer({
    doc,
    listEl,
    scrollEl,
    apiBase: "https://api.example.test",
    getSelfUserId: () => self,
    getNameCache: () => nameCache,
    onAuthorClick,
  });
  return { doc, listEl, scrollEl, renderer };
}

const NOW = "2026-08-01T10:00:00Z";

// ---- (a) row classes + author label placement ----

test("buildBubbleNode(userMsg): class `xb-msg is-user`, author label in .xb-msg-meta", () => {
  const { renderer } = makeHarness();
  const node = renderer.buildBubbleNode({
    id: "m1",
    kind: "user",
    author_user_id: "u-mate",
    content: "hello",
    created_at: NOW,
  });
  assert.equal(node.className, "xb-msg is-user");
  const meta = node.querySelector(".xb-msg-meta");
  assert.ok(meta, "row must contain a .xb-msg-meta");
  const author = meta.querySelector(".xb-msg-author");
  assert.ok(author, ".xb-msg-author must live inside .xb-msg-meta");
  assert.equal(author.textContent, "Mate");
});

test("buildBubbleNode(selfMsg): class `xb-msg is-self` when author === selfUserId", () => {
  const { renderer } = makeHarness();
  const node = renderer.buildBubbleNode({
    id: "m2",
    kind: "user",
    author_user_id: "u-self",
    content: "mine",
    created_at: NOW,
  });
  assert.equal(node.className, "xb-msg is-self");
});

// ---- (b) agent row ----

test("buildBubbleNode(agentMsg): emits .xb-msg-agent-label and no author listener", () => {
  const clicks = [];
  const { renderer } = makeHarness({ onAuthorClick: (id) => clicks.push(id) });
  const node = renderer.buildBubbleNode({
    id: "m3",
    kind: "agent",
    agent_name: "agent",
    author_user_id: "u-mate", // present but irrelevant: the agent is not a teammate
    content: "",
    created_at: NOW,
  });
  assert.equal(node.className, "xb-msg is-agent");
  assert.ok(
    node.querySelector(".xb-msg-agent-label"),
    "an agent row must carry the .xb-msg-agent-label block",
  );
  const author = node.querySelector(".xb-msg-author");
  assert.equal(
    (author.listeners.click || []).length,
    0,
    "the agent name must never be clickable — there is nobody to send a file to",
  );
});

// ---- (c) the affordance is opt-in ----

test("onAuthorClick supplied: a teammate's name gets a listener, a cursor and a title", () => {
  const clicks = [];
  const { renderer } = makeHarness({ onAuthorClick: (id) => clicks.push(id) });
  const node = renderer.buildBubbleNode({
    id: "m4",
    kind: "user",
    author_user_id: "u-mate",
    content: "hi",
    created_at: NOW,
  });
  const author = node.querySelector(".xb-msg-author");
  assert.equal((author.listeners.click || []).length, 1);
  assert.equal(author.style.cursor, "pointer");
  assert.ok(author.title);
  author.listeners.click[0]();
  assert.deepEqual(clicks, ["u-mate"]);
});

test("onAuthorClick null: NO listener, NO cursor, NO title on a teammate's name", () => {
  const { renderer } = makeHarness({ onAuthorClick: null });
  const node = renderer.buildBubbleNode({
    id: "m5",
    kind: "user",
    author_user_id: "u-mate",
    content: "hi",
    created_at: NOW,
  });
  const author = node.querySelector(".xb-msg-author");
  assert.equal(
    (author.listeners.click || []).length,
    0,
    "a surface without the people overlay must not attach a click listener",
  );
  assert.equal(author.style.cursor, undefined, "no cursor style");
  assert.equal(author.title, undefined, "no tooltip promising an action");
});

// ---- (d) XSS ----

test("xss: `<img src=x onerror=alert(1)>` lands as TEXT, creating no child element", () => {
  const payload = '<img src=x onerror=alert(1)>';
  const { renderer } = makeHarness();
  const node = renderer.buildBubbleNode({
    id: "m6",
    kind: "user",
    author_user_id: "u-mate",
    content: payload,
    created_at: NOW,
  });
  const text = node.querySelector(".xb-msg-text");
  assert.ok(text, "message body must live in .xb-msg-text");
  assert.equal(text.textContent, payload, "the raw string must survive verbatim");
  assert.equal(
    text.children.length,
    0,
    "no element may be constructed from message content — textContent only",
  );
  // The same rule for a display name — the name cache is populated from server
  // data, so it is untrusted too.
  const hostile = makeHarness({ nameCache: { "u-x": payload } }).renderer;
  const row = hostile.buildBubbleNode({
    id: "m7",
    kind: "user",
    author_user_id: "u-x",
    content: "x",
    created_at: NOW,
  });
  const authorEl = row.querySelector(".xb-msg-author");
  assert.equal(authorEl.textContent, payload);
  assert.equal(authorEl.children.length, 0);
});

// ---- de-dupe + day separators (the reason those comments exist) ----

test("renderMessage de-dupes by data-msg-id and syncDaySeparators inserts one .xb-msg-daysep per day", () => {
  const { listEl, renderer } = makeHarness();
  renderer.renderMessage({
    id: "d1",
    kind: "user",
    author_user_id: "u-mate",
    content: "a",
    created_at: "2026-07-30T10:00:00Z",
  });
  renderer.renderMessage({
    id: "d1",
    kind: "user",
    author_user_id: "u-mate",
    content: "a",
    created_at: "2026-07-30T10:00:00Z",
  });
  assert.equal(listEl.children.length, 1, "the same id must not render twice");
  renderer.renderMessage({
    id: "d2",
    kind: "user",
    author_user_id: "u-mate",
    content: "b",
    created_at: "2026-07-31T10:00:00Z",
  });
  renderer.syncDaySeparators();
  assert.equal(listEl.querySelectorAll(".xb-msg-daysep").length, 2);
  // Idempotent: a second reconcile must not stack separators.
  renderer.syncDaySeparators();
  assert.equal(listEl.querySelectorAll(".xb-msg-daysep").length, 2);
});

test("clear() empties the list without assigning a markup string", () => {
  const { listEl, renderer } = makeHarness();
  renderer.renderMessage({
    id: "c1",
    kind: "user",
    author_user_id: "u-mate",
    content: "a",
    created_at: NOW,
  });
  assert.equal(listEl.children.length, 1);
  renderer.clear();
  assert.equal(listEl.children.length, 0);
});

test("media: the thumbnail src is apiBase + the server-minted relative path", () => {
  const { renderer } = makeHarness();
  const node = renderer.buildBubbleNode({
    id: "m8",
    kind: "user",
    author_user_id: "u-mate",
    content: "photo.png",
    created_at: NOW,
    metadata: {
      media: {
        item_id: "i1",
        mime: "image/png",
        url: "/v1/media/i1/img?t=sig",
        filename: "photo.png",
      },
    },
  });
  const img = node.querySelector(".xb-msg-thumb");
  assert.ok(img, "an image attachment must render a .xb-msg-thumb");
  assert.equal(img.src, "https://api.example.test/v1/media/i1/img?t=sig");
  assert.ok(
    node.querySelector(".xb-msg-caption"),
    "the caption renders alongside the attachment",
  );
});

// ---- (e) the publication router ----

test("router: a `message` frame calls renderMessage exactly once and reports non-empty", () => {
  const calls = [];
  let nonEmpty = 0;
  const renderer = {
    renderMessage: (msg, o) => calls.push([msg, o]),
    renderAgentBubble: () => {},
    syncDaySeparators: () => calls.push(["sync"]),
    streamTextTarget: () => null,
    clearStreaming: () => {},
    scrollToBottom: () => {},
  };
  const route = createPublicationRouter({
    renderer,
    streamBuffer: new StreamBuffer(),
    onNonEmpty: () => nonEmpty++,
  });
  route({ type: "message", message: { id: "r1", content: "hi" } });
  const rendered = calls.filter((c) => c[0] && c[0].id === "r1");
  assert.equal(rendered.length, 1, "renderMessage must fire exactly once");
  assert.deepEqual(rendered[0][1], { prepend: false });
  assert.equal(nonEmpty, 1);
});

test("router: start -> chunk -> chunk -> end accumulates the deltas into the stream target", () => {
  const { listEl, renderer } = makeHarness();
  const route = createPublicationRouter({
    renderer,
    streamBuffer: new StreamBuffer(),
    onNonEmpty: () => {},
  });
  route({ type: "agent_stream_start", message_id: "s1", agent_name: "agent" });
  const bubble = listEl.querySelector(".xb-msg-bubble");
  assert.ok(bubble.classList.contains("streaming"), "the bubble streams while open");
  route({ type: "agent_stream_chunk", message_id: "s1", delta: "Hel" });
  route({ type: "agent_stream_chunk", message_id: "s1", delta: "lo!" });
  assert.equal(renderer.streamTextTarget("s1").textContent, "Hello!");
  route({ type: "agent_stream_end", message_id: "s1" });
  assert.ok(
    !bubble.classList.contains("streaming"),
    "the streaming class is dropped when the answer completes",
  );
  assert.equal(
    renderer.streamTextTarget("s1").textContent,
    "Hello!",
    "the finished text stays — the agent label and sources are not wiped",
  );
});

test("router: an error frame appends the reason and stops the streaming state", () => {
  const { listEl, renderer } = makeHarness();
  const route = createPublicationRouter({
    renderer,
    streamBuffer: new StreamBuffer(),
    onNonEmpty: () => {},
  });
  route({ type: "agent_stream_start", message_id: "s2", agent_name: "agent" });
  route({ type: "agent_stream_chunk", message_id: "s2", delta: "partial" });
  route({ type: "agent_stream_error", message_id: "s2", error: "boom" });
  const text = renderer.streamTextTarget("s2");
  assert.ok(text.textContent.includes("partial"));
  assert.ok(text.textContent.includes("(error: boom)"));
  assert.ok(!listEl.querySelector(".xb-msg-bubble").classList.contains("streaming"));
});

test("router: an unknown frame type is ignored, not thrown on", () => {
  const route = createPublicationRouter({
    renderer: {
      renderMessage: () => assert.fail("must not render"),
      renderAgentBubble: () => assert.fail("must not render"),
      syncDaySeparators: () => {},
      streamTextTarget: () => null,
      clearStreaming: () => {},
      scrollToBottom: () => {},
    },
    streamBuffer: new StreamBuffer(),
  });
  route({ type: "some_future_frame", message_id: "x" });
  route(null);
  route({});
});

// ---- (f) anti-fork: the extension consumes the shared modules ----

const popupJs = readFileSync(join(REPO_ROOT, "chrome-extension", "popup.js"), "utf8");

test("anti-fork: popup.js imports the shared renderer + router instead of keeping its own", () => {
  assert.ok(
    /import\s*\{[^}]*createRenderer[^}]*\}\s*from\s*"\.\/chat_core\/render\.js"/.test(
      popupJs,
    ),
    "popup.js must import createRenderer from ./chat_core/render.js (D-27-04: extract, do not fork)",
  );
  assert.ok(
    /import\s*\{[^}]*createPublicationRouter[^}]*\}\s*from\s*"\.\/chat_core\/publication\.js"/.test(
      popupJs,
    ),
    "popup.js must import createPublicationRouter from ./chat_core/publication.js",
  );
});

test("anti-fork: popup.js no longer defines the moved render/route functions", () => {
  for (const decl of [
    "function buildBubbleNode",
    "function buildSourcesNode",
    "function renderMessage",
    "function renderAgentBubble",
    "function clearMessageList",
    "function syncDaySeparators",
    "function renderMediaInto",
    "function formatBytes",
    "function scrollToBottom",
    "function streamTextTarget",
    "function handlePublication",
  ]) {
    assert.ok(
      !popupJs.includes(decl),
      `popup.js still declares \`${decl}\` — a second copy of the render layer is exactly what D-27-04 forbids`,
    );
  }
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
