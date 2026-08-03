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
 *       target and then drops the streaming class; a failed turn becomes a
 *       FAILURE STATE whose words come from the client's own closed vocabulary,
 *       so no text a frame carries can reach a rendered message;
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
import {
  StreamBuffer,
  AGENT_FAILURE_TEXT,
  AGENT_FAILURE_FALLBACK,
} from "../../packages/chat-core/chat_stream.js";

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
    this.attributes = {};
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

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name)
      ? this.attributes[name]
      : null;
  }

  /** Fire every listener registered for `type`. No bubbling — none is needed. */
  dispatch(type) {
    for (const fn of this.listeners[type] || []) fn({ type, target: this });
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
  fetchIndexedText = null,
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
    fetchIndexedText,
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

test("router: an error frame becomes a failure state, and stops the streaming", () => {
  const { listEl, renderer } = makeHarness();
  const route = createPublicationRouter({
    renderer,
    streamBuffer: new StreamBuffer(),
    onNonEmpty: () => {},
  });
  route({ type: "agent_stream_start", message_id: "s2", agent_name: "agent" });
  route({ type: "agent_stream_chunk", message_id: "s2", delta: "partial" });
  route({ type: "agent_stream_error", message_id: "s2", code: "unavailable" });

  const bubble = listEl.querySelector(".xb-msg-bubble");
  assert.ok(!bubble.classList.contains("streaming"));
  assert.ok(bubble.classList.contains("is-failed"), "the bubble must read as failed");

  // Whatever DID arrive is still the agent's, and stays in the answer.
  assert.equal(renderer.streamTextTarget("s2").textContent, "partial");
  // The failure is NOT the answer, so it lives in its own node.
  const note = bubble.querySelector(".xb-msg-failure");
  assert.ok(note, "a failed turn must carry a failure node");
  assert.equal(note.textContent, AGENT_FAILURE_TEXT.unavailable);
});

test("router: a duplicate error frame does not stack a second failure line", () => {
  const { listEl, renderer } = makeHarness();
  const route = createPublicationRouter({
    renderer,
    streamBuffer: new StreamBuffer(),
    onNonEmpty: () => {},
  });
  route({ type: "agent_stream_start", message_id: "s3", agent_name: "agent" });
  route({ type: "agent_stream_error", message_id: "s3", code: "timeout" });
  route({ type: "agent_stream_error", message_id: "s3", code: "timeout" });
  assert.equal(
    listEl.querySelector(".xb-msg-bubble").querySelectorAll(".xb-msg-failure").length,
    1,
  );
});

// The property this whole change exists for. Asserted on the SHAPE — for ANY
// frame, whatever it carries — rather than on the one sample string that
// prompted it, because the next provider error will be worded differently.
test("router: no text a frame carries can reach the rendered message", () => {
  const allowed = new Set([...Object.values(AGENT_FAILURE_TEXT), AGENT_FAILURE_FALLBACK]);
  const hostileFrames = [
    { error: "Error code: 400 - {'message': 'Your credit balance is too low'}" },
    { error: "401 Unauthorized: invalid x-api-key sk-ant-api03-XXXX" },
    { message: "request_id req_011abc failed at api.anthropic.com" },
    { detail: "<script>alert(1)</script>" },
    { error: "boom", code: "unavailable" },
    { code: "a_code_from_a_newer_server", error: "raw text from the future" },
    { code: 42, error: { nested: "object" } },
    {},
  ];
  for (const extra of hostileFrames) {
    const { listEl, renderer } = makeHarness();
    const route = createPublicationRouter({
      renderer,
      streamBuffer: new StreamBuffer(),
      onNonEmpty: () => {},
    });
    route({ type: "agent_stream_start", message_id: "h1", agent_name: "agent" });
    route({ type: "agent_stream_error", message_id: "h1", ...extra });

    const bubble = listEl.querySelector(".xb-msg-bubble");
    const note = bubble.querySelector(".xb-msg-failure");
    assert.ok(note, `no failure node for ${JSON.stringify(extra)}`);
    assert.ok(
      allowed.has(note.textContent),
      `the rendered failure is not from the client's own vocabulary: ${JSON.stringify(
        note.textContent,
      )}`,
    );
    // And nothing leaked anywhere else in the row either.
    const whole = listEl.textContent;
    for (const value of Object.values(extra)) {
      const raw = typeof value === "string" ? value : "";
      if (raw.length > 3) {
        assert.ok(!whole.includes(raw), `frame text reached the DOM: ${raw}`);
      }
    }
  }
});

test("row: a persisted failure renders as one on RELOAD, not as the agent's reply", () => {
  // The live frame is gone by then. Everyone who was not connected when it
  // happened sees only this row, and it used to read as an ordinary answer.
  const { renderer } = makeHarness();
  const node = renderer.buildBubbleNode({
    id: "f1",
    kind: "agent",
    agent_name: "agent",
    content: "The agent could not answer just now. Worth trying again.",
    created_at: NOW,
    metadata: { agent_failure: { code: "unavailable", retryable: true, partial: false } },
  });
  const bubble = node.querySelector(".xb-msg-bubble");
  assert.ok(bubble.classList.contains("is-failed"));
  assert.equal(
    bubble.querySelector(".xb-msg-text").textContent,
    "",
    "a failure that produced nothing must not print in the agent's voice",
  );
  assert.equal(
    bubble.querySelector(".xb-msg-failure").textContent,
    AGENT_FAILURE_TEXT.unavailable,
  );
});

test("row: a failure that produced PARTIAL output keeps it — that part is real", () => {
  const { renderer } = makeHarness();
  const node = renderer.buildBubbleNode({
    id: "f2",
    kind: "agent",
    agent_name: "agent",
    content: "Half an ans",
    created_at: NOW,
    metadata: { agent_failure: { code: "timeout", retryable: true, partial: true } },
  });
  const bubble = node.querySelector(".xb-msg-bubble");
  assert.equal(bubble.querySelector(".xb-msg-text").textContent, "Half an ans");
  assert.equal(bubble.querySelector(".xb-msg-failure").textContent, AGENT_FAILURE_TEXT.timeout);
});

test("row: an ordinary agent answer gets NO failure node", () => {
  const { renderer } = makeHarness();
  const node = renderer.buildBubbleNode({
    id: "ok1",
    kind: "agent",
    agent_name: "agent",
    content: "Here is the answer.",
    created_at: NOW,
    metadata: { memory_items: 2 },
  });
  const bubble = node.querySelector(".xb-msg-bubble");
  assert.ok(!bubble.classList.contains("is-failed"));
  assert.equal(bubble.querySelector(".xb-msg-failure"), null);
});

test("both stylesheets give a failed turn a failure look", () => {
  for (const rel of [
    join("chrome-extension", "popup.css"),
    join("app-site", "app", "app.css"),
  ]) {
    const css = readFileSync(join(REPO_ROOT, rel), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
    const rule = /(^|[};])\s*\.xb-msg-failure\s*\{([^}]*)\}/.exec(css);
    assert.ok(rule, `${rel} has no .xb-msg-failure rule`);
    assert.match(
      rule[2],
      /color:\s*var\(--destructive\)/,
      `${rel}: a failed turn must not be styled like an answer`,
    );
    assert.ok(
      /\.xb-msg-bubble\.is-failed\b/.test(css),
      `${rel} never styles the .is-failed bubble`,
    );
  }
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

// ===========================================================================
// Avatar rules — Telegram grouping.
//
// This is layout logic, which is exactly the kind that regresses silently: a
// stranded avatar or a shifted bubble looks like "the CSS is a bit off" rather
// than like a bug, so nobody files it and nobody fixes it. Both rules are
// pinned here.
//
//   1. Your OWN messages carry no avatar at all.
//   2. A run of consecutive messages from one author shows the avatar ONCE, on
//      the LAST message of the run.
// ===========================================================================

/** A user message, minimal. `t` is minutes past NOW, for ordering/day tests. */
function msg(id, author, t = 0) {
  return {
    id,
    kind: "user",
    author_user_id: author,
    content: id,
    created_at: new Date(Date.parse(NOW) + t * 60000).toISOString(),
  };
}

/** Render a list in order and report, per row, whether its avatar is grouped. */
function renderRun(messages, opts = {}) {
  const h = makeHarness(opts);
  for (const m of messages) h.renderer.renderMessage(m, { prepend: false });
  return h;
}

/** The rows (not the day separators), in DOM order. */
function rowsOf(listEl) {
  return listEl.children.filter((c) => c.dataset && c.dataset.msgId);
}

/** true where the row's avatar is suppressed by grouping. */
function groupedFlags(listEl) {
  return rowsOf(listEl).map((r) => r.classList.contains("is-grouped"));
}

test("your own messages carry NO avatar element at all", () => {
  const { renderer } = makeHarness();
  const node = renderer.buildBubbleNode(msg("m1", "u-self"));
  assert.equal(node.className, "xb-msg is-self");
  assert.equal(
    node.querySelector(".xb-msg-avatar"),
    null,
    "right alignment already says the row is yours; a second marker on every line is noise",
  );
});

test("a teammate's message still carries one", () => {
  const { renderer } = makeHarness();
  const node = renderer.buildBubbleNode(msg("m1", "u-mate"));
  const avatar = node.querySelector(".xb-msg-avatar");
  assert.ok(avatar, "other people's rows need a face");
  assert.equal(avatar.textContent, "M", "first letter of the cached display name");
});

test("the agent's message carries its own avatar", () => {
  const { renderer } = makeHarness();
  const node = renderer.buildBubbleNode({
    id: "a1",
    kind: "agent",
    agent_name: "chad",
    content: "hi",
    created_at: NOW,
  });
  assert.equal(node.querySelector(".xb-msg-avatar").textContent, "🤖");
});

test("a run of six from one teammate shows the avatar ONCE, on the last row", () => {
  const { listEl } = renderRun([
    msg("m1", "u-mate", 0),
    msg("m2", "u-mate", 1),
    msg("m3", "u-mate", 2),
    msg("m4", "u-mate", 3),
    msg("m5", "u-mate", 4),
    msg("m6", "u-mate", 5),
  ]);
  assert.deepEqual(
    groupedFlags(listEl),
    [true, true, true, true, true, false],
    "the avatar belongs at the BOTTOM of the group, beside the newest bubble",
  );
});

test("a single message is its own run and keeps its avatar", () => {
  const { listEl } = renderRun([msg("m1", "u-mate")]);
  assert.deepEqual(groupedFlags(listEl), [false]);
});

test("a different author ends the run", () => {
  const { listEl } = renderRun([
    msg("m1", "u-mate", 0),
    msg("m2", "u-mate", 1),
    msg("m3", "u-other", 2),
    msg("m4", "u-mate", 3),
  ]);
  // m1 is followed by the same author, so it hides; m2 is the tail of that run;
  // m3 and m4 are runs of one.
  assert.deepEqual(groupedFlags(listEl), [true, false, false, false]);
});

test("two agents answering in sequence do not group together", () => {
  // Keyed by agent NAME, so a second agent's reply keeps its own avatar.
  const { listEl } = renderRun([
    { id: "a1", kind: "agent", agent_name: "chad", content: "x", created_at: NOW },
    { id: "a2", kind: "agent", agent_name: "chad", content: "y", created_at: NOW },
    { id: "a3", kind: "agent", agent_name: "other", content: "z", created_at: NOW },
  ]);
  assert.deepEqual(groupedFlags(listEl), [true, false, false]);
});

test("a live arrival RE-EVALUATES the row before it", () => {
  // THE FAILURE THIS PINS: grouping depends on the NEXT row's author, which did
  // not exist when this row was built. Without reconciliation on insert, the
  // avatar stays stranded on the second-to-last bubble of the run forever.
  const h = makeHarness();
  h.renderer.renderMessage(msg("m1", "u-mate", 0), { prepend: false });
  assert.deepEqual(groupedFlags(h.listEl), [false], "alone, it is the tail");

  h.renderer.renderMessage(msg("m2", "u-mate", 1), { prepend: false });
  assert.deepEqual(
    groupedFlags(h.listEl),
    [true, false],
    "the new arrival takes over as the tail, and m1 gives up its avatar",
  );

  h.renderer.renderMessage(msg("m3", "u-other", 2), { prepend: false });
  assert.deepEqual(
    groupedFlags(h.listEl),
    [true, false, false],
    "a different author ends the run at m2, which keeps its avatar",
  );
});

test("prepending an older page re-evaluates the join between the pages", () => {
  const h = makeHarness();
  h.renderer.renderMessage(msg("m3", "u-mate", 2), { prepend: false });
  // An older message from the SAME author, prepended: it is now the row before
  // m3, so it must give up its avatar.
  h.renderer.renderMessage(msg("m2", "u-mate", 1), { prepend: true });
  assert.deepEqual(groupedFlags(h.listEl), [true, false]);
  // An older one from someone else does not group.
  h.renderer.renderMessage(msg("m1", "u-other", 0), { prepend: true });
  assert.deepEqual(groupedFlags(h.listEl), [false, true, false]);
});

test("a day separator BREAKS the run", () => {
  // Two messages either side of midnight are not visually consecutive, and an
  // avatar stranded above a date heading reads as a rendering fault.
  const yesterday = "2026-07-31T22:00:00Z";
  const today = "2026-08-01T09:00:00Z";
  const h = makeHarness();
  h.renderer.renderMessage(
    { id: "m1", kind: "user", author_user_id: "u-mate", content: "a", created_at: yesterday },
    { prepend: false },
  );
  h.renderer.renderMessage(
    { id: "m2", kind: "user", author_user_id: "u-mate", content: "b", created_at: today },
    { prepend: false },
  );
  h.renderer.syncDaySeparators();
  assert.equal(
    h.listEl.querySelectorAll(".xb-msg-daysep").length,
    2,
    "one separator per day, so the fixture really does span midnight",
  );
  assert.deepEqual(
    groupedFlags(h.listEl),
    [false, false],
    "the last row of yesterday keeps its avatar even though today continues the same author",
  );
});

test("grouping survives syncDaySeparators being run after a whole batch", () => {
  // The batch path renders every row first and inserts separators afterwards.
  // Reconciling only at insert time would leave the pre-separator state.
  const h = makeHarness();
  for (const m of [msg("m1", "u-mate", 0), msg("m2", "u-mate", 1), msg("m3", "u-mate", 2)]) {
    h.renderer.renderMessage(m, { prepend: false });
  }
  h.renderer.syncDaySeparators();
  assert.deepEqual(groupedFlags(h.listEl), [true, true, false]);
});

test("your own run needs no grouping work — there is nothing to hide", () => {
  const { listEl } = renderRun([msg("m1", "u-self", 0), msg("m2", "u-self", 1)]);
  for (const row of rowsOf(listEl)) {
    assert.equal(row.querySelector(".xb-msg-avatar"), null);
  }
});

test("a grouped row HIDES its avatar, it does not remove it", () => {
  // The gutter must stay reserved or every bubble in the run shifts left.
  const { listEl } = renderRun([msg("m1", "u-mate", 0), msg("m2", "u-mate", 1)]);
  const first = rowsOf(listEl)[0];
  assert.ok(first.classList.contains("is-grouped"));
  assert.ok(
    first.querySelector(".xb-msg-avatar"),
    "the element must still be there — CSS hides it with visibility, keeping the column",
  );
});

test("both stylesheets hide the grouped avatar with visibility, never display", () => {
  for (const rel of [
    join("chrome-extension", "popup.css"),
    join("app-site", "app", "app.css"),
  ]) {
    const css = readFileSync(join(REPO_ROOT, rel), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
    const m = /\.xb-msg\.is-grouped\s+\.xb-msg-avatar\s*\{([^}]*)\}/.exec(css);
    assert.ok(m, `${rel} has no .xb-msg.is-grouped .xb-msg-avatar rule`);
    assert.match(
      m[1],
      /visibility:\s*hidden/,
      `${rel} must hide the avatar with visibility`,
    );
    assert.ok(
      !/display:\s*none/.test(m[1]),
      `${rel} uses display:none — that collapses the gutter and shifts every bubble in the run left`,
    );
  }
});

// ---- (h) the OTHER end of the run: the name -------------------------------
//
// The avatar closes a run at the bottom; the name introduces it at the top. Both
// answers depend on a NEIGHBOUR, which is why they are decided in one pass — a
// second reconciler would be a second chance to disagree with the first.

/** true where the row is a follower (its sender's name is suppressed). */
function followerFlags(listEl) {
  return rowsOf(listEl).map((r) => r.classList.contains("is-run-follower"));
}

test("a run of six shows the NAME once, on the FIRST row", () => {
  const { listEl } = renderRun([
    msg("m1", "u-mate", 0),
    msg("m2", "u-mate", 1),
    msg("m3", "u-mate", 2),
    msg("m4", "u-mate", 3),
    msg("m5", "u-mate", 4),
    msg("m6", "u-mate", 5),
  ]);
  assert.deepEqual(
    followerFlags(listEl),
    [false, true, true, true, true, true],
    "the name introduces the run from the top; repeating it five more times is noise",
  );
  // The two ends really are opposite ends, not the same row twice.
  assert.deepEqual(groupedFlags(listEl), [true, true, true, true, true, false]);
});

test("every bubble in a run still carries its own timestamp", () => {
  // Identity is per-run, time is per-message. A run of six with one timestamp
  // answers "when did they say THAT one" for exactly one of them.
  const { listEl } = renderRun([
    msg("m1", "u-mate", 0),
    msg("m2", "u-mate", 1),
    msg("m3", "u-mate", 2),
  ]);
  for (const row of rowsOf(listEl)) {
    const time = row.querySelector(".xb-msg-time");
    assert.ok(time, `${row.dataset.msgId} lost its timestamp`);
    assert.ok(time.textContent.length > 0, `${row.dataset.msgId} has an empty timestamp`);
  }
  // ... including the middle one, which carries no name and no avatar.
  const middle = rowsOf(listEl)[1];
  assert.ok(middle.classList.contains("is-run-follower"));
  assert.ok(middle.classList.contains("is-grouped"));
  assert.ok(middle.querySelector(".xb-msg-time"));
});

test("the timestamp lives INSIDE the bubble, not on the meta line above it", () => {
  const { renderer } = makeHarness();
  const node = renderer.buildBubbleNode(msg("m1", "u-mate"));
  const bubble = node.querySelector(".xb-msg-bubble");
  assert.ok(bubble.querySelector(".xb-msg-time"), "the time belongs to the bubble");
  assert.equal(
    node.querySelector(".xb-msg-meta").querySelector(".xb-msg-time"),
    null,
    "it must have left the meta row — that row is identity, and it disappears on a follower",
  );
});

test("the bubble reserves the timestamp's own width at the end of its text", () => {
  // THE FAILURE THIS PINS: the timestamp is taken out of flow by CSS. Without a
  // spacer of exactly its width as the last thing in the bubble, a long final
  // word runs underneath it and neither can be read.
  const { renderer } = makeHarness();
  const node = renderer.buildBubbleNode(msg("m1", "u-mate"));
  const bubble = node.querySelector(".xb-msg-bubble");
  const spacer = bubble.querySelector(".xb-msg-timespace");
  const time = bubble.querySelector(".xb-msg-time");
  assert.ok(spacer, "the bubble must reserve room for the timestamp");
  assert.equal(
    spacer.textContent,
    time.textContent,
    'the reserved width must be the SAME string — "just now" and "5m" are not the same size',
  );
  // Last in flow, so it lands on whatever the final line turns out to be.
  const flow = bubble.children.filter((c) => c.className !== "xb-msg-time");
  assert.equal(
    flow[flow.length - 1],
    spacer,
    "the spacer must be the last thing in the bubble's flow",
  );
});

test("a live arrival re-evaluates the NAME on its neighbour too", () => {
  // Same failure as the avatar's, at the other end of the run: the second
  // message must give up its own name when it turns out to continue the first.
  const h = makeHarness();
  h.renderer.renderMessage(msg("m1", "u-mate", 0), { prepend: false });
  assert.deepEqual(followerFlags(h.listEl), [false], "alone, it is its own head");

  h.renderer.renderMessage(msg("m2", "u-mate", 1), { prepend: false });
  assert.deepEqual(
    followerFlags(h.listEl),
    [false, true],
    "the arrival continues m1's run, so it carries no name of its own",
  );

  h.renderer.renderMessage(msg("m3", "u-other", 2), { prepend: false });
  assert.deepEqual(
    followerFlags(h.listEl),
    [false, true, false],
    "a different author starts a new run and is named",
  );
});

test("prepending an older page moves the name UP to the new first row", () => {
  const h = makeHarness();
  h.renderer.renderMessage(msg("m2", "u-mate", 1), { prepend: false });
  assert.deepEqual(followerFlags(h.listEl), [false]);
  h.renderer.renderMessage(msg("m1", "u-mate", 0), { prepend: true });
  assert.deepEqual(
    followerFlags(h.listEl),
    [false, true],
    "the older message is now the head of the run and m2 gives its name up",
  );
});

test("a day separator gives the name back on the far side of midnight", () => {
  const h = makeHarness();
  h.renderer.renderMessage(
    { id: "m1", kind: "user", author_user_id: "u-mate", content: "a", created_at: "2026-07-31T22:00:00Z" },
    { prepend: false },
  );
  h.renderer.renderMessage(
    { id: "m2", kind: "user", author_user_id: "u-mate", content: "b", created_at: "2026-08-01T09:00:00Z" },
    { prepend: false },
  );
  h.renderer.syncDaySeparators();
  assert.deepEqual(
    followerFlags(h.listEl),
    [false, false],
    "two messages either side of a date heading are not one run",
  );
});

test("both stylesheets suppress the name on a follower and on your own row", () => {
  for (const rel of [
    join("chrome-extension", "popup.css"),
    join("app-site", "app", "app.css"),
  ]) {
    const css = readFileSync(join(REPO_ROOT, rel), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
    assert.match(
      css,
      /\.xb-msg\.is-run-follower\s+\.xb-msg-author\s*,\s*\.xb-msg\.is-self\s+\.xb-msg-author\s*\{[^}]*display:\s*none/,
      `${rel} must hide the author on a follower row AND on your own row`,
    );
    // The timestamp is only allowed out of flow if the space is reserved.
    const timeRule = /(^|[};])\s*\.xb-msg-time\s*\{([^}]*)\}/.exec(css);
    assert.ok(timeRule, `${rel} has no .xb-msg-time rule`);
    assert.match(
      timeRule[2],
      /position:\s*absolute/,
      `${rel}: the timestamp must sit in the bubble's corner, not on the meta line`,
    );
    const spacerRule = /(^|[};])\s*\.xb-msg-timespace\s*\{([^}]*)\}/.exec(css);
    assert.ok(
      spacerRule,
      `${rel} has no .xb-msg-timespace rule — the reserved width would be zero and the last word would run under the time`,
    );
    assert.match(
      spacerRule[2],
      /visibility:\s*hidden/,
      `${rel}: the spacer reserves width and must not be readable — visibility, so it is out of the a11y tree too`,
    );
    assert.ok(
      !/display:\s*none/.test(spacerRule[2]),
      `${rel}: display:none reserves nothing, which is the whole failure this element exists to stop`,
    );
    assert.ok(
      /\.xb-msg-bubble\s*\{[^}]*position:\s*relative/.test(css),
      `${rel}: .xb-msg-bubble must be the positioning context, or the time escapes to the page corner`,
    );
  }
});

// ===========================================================================
// Indexed-attachment marker + its reveal.
//
// The badge used to spell the mechanism ("saved to brain · image indexed"),
// which told a reader that something had happened and never what. The marker now
// reveals the TEXT the brain holds — fetched from the server, because the
// description is written after the message exists and is not in its payload.
//
// Three properties are load-bearing and each has its own assertion:
//   - nothing is fetched at render time (a thread of fifty images would
//     otherwise fire fifty requests for text nobody asked to see);
//   - the fetch happens ONCE per item, no matter how often it is hovered or
//     focused, and the answer is cached;
//   - the tooltip is never blank — every state gets its own sentence.
// ===========================================================================

/** Async sibling of test(). Awaited at the call site (top-level await). */
async function atest(name, body) {
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

/** A message carrying a real media attachment (the indexed signal). */
function mediaMsg(id = "mm1", mime = "image/png") {
  return {
    id,
    kind: "user",
    author_user_id: "u-mate",
    content: "shot.png",
    created_at: NOW,
    metadata: { media: { item_id: "item-9", mime, url: "/v1/media/item-9/img?t=x" } },
  };
}

/** A fetcher that counts its calls and resolves with `payload`. */
function countingFetcher(payload) {
  const calls = [];
  return {
    calls,
    fn: (itemId) => {
      calls.push(itemId);
      return Promise.resolve(payload);
    },
  };
}

test("marker: a message with no attachment gets NO marker (absence is the signal)", () => {
  const { renderer } = makeHarness({ fetchIndexedText: () => Promise.resolve({}) });
  const node = renderer.buildBubbleNode({
    id: "t1",
    kind: "user",
    author_user_id: "u-mate",
    content: "just text",
    created_at: NOW,
  });
  assert.equal(node.querySelector(".xb-msg-savetag"), null);
});

test("marker: an attachment gets a focusable button, not a hover-only span", () => {
  const { renderer } = makeHarness({ fetchIndexedText: () => Promise.resolve({}) });
  const node = renderer.buildBubbleNode(mediaMsg());
  const tag = node.querySelector(".xb-msg-savetag");
  assert.ok(tag, "an indexed attachment must carry a marker");
  assert.equal(tag.tagName, "BUTTON", "a keyboard user must be able to reach the reveal");
  assert.equal(tag.type, "button");
  assert.ok(
    tag.getAttribute("aria-label"),
    "the marker needs an accessible name — its glyph is aria-hidden",
  );
});

test("marker: the visible label no longer narrates the mechanism", () => {
  const { renderer } = makeHarness({ fetchIndexedText: () => Promise.resolve({}) });
  const tag = renderer.buildBubbleNode(mediaMsg()).querySelector(".xb-msg-savetag");
  assert.ok(
    !/saved to brain|indexed/i.test(tag.textContent),
    `the marker must not spell out what the system did (got: ${JSON.stringify(tag.textContent)})`,
  );
});

test("marker: NOTHING is fetched while the row is built", () => {
  const f = countingFetcher({ state: "indexed", text: "hi" });
  const { renderer } = makeHarness({ fetchIndexedText: f.fn });
  for (let i = 0; i < 50; i += 1) {
    renderer.renderMessage(mediaMsg(`bulk-${i}`), { prepend: false });
  }
  assert.deepEqual(
    f.calls,
    [],
    "fifty attachments rendered fifty requests for text nobody asked to see",
  );
});

test("marker: without a fetcher the mark is inert, not a dead control", () => {
  const { renderer } = makeHarness({ fetchIndexedText: null });
  const tag = renderer.buildBubbleNode(mediaMsg()).querySelector(".xb-msg-savetag");
  assert.ok(tag, "the indexed signal is real even where the reveal is not shipped");
  assert.equal(tag.tagName, "SPAN");
  assert.equal(tag.querySelector(".xb-savetag-tip"), null);
});

await atest("reveal: the first hover fetches and writes the indexed text", async () => {
  const f = countingFetcher({ state: "indexed", text: "A deploy pipeline diagram." });
  const { renderer } = makeHarness({ fetchIndexedText: f.fn });
  const tag = renderer.buildBubbleNode(mediaMsg()).querySelector(".xb-msg-savetag");
  const tip = tag.querySelector(".xb-savetag-tip");
  assert.equal(tip.textContent, "Loading…", "the tooltip is never blank, not even before the answer");

  tag.dispatch("mouseenter");
  await new Promise((r) => setTimeout(r, 0));

  assert.deepEqual(f.calls, ["item-9"]);
  assert.equal(tip.textContent, "A deploy pipeline diagram.");
});

await atest("reveal: hovering and focusing repeatedly fetches exactly ONCE", async () => {
  const f = countingFetcher({ state: "indexed", text: "cached" });
  const { renderer } = makeHarness({ fetchIndexedText: f.fn });
  const tag = renderer.buildBubbleNode(mediaMsg()).querySelector(".xb-msg-savetag");

  tag.dispatch("mouseenter");
  tag.dispatch("focus");
  tag.dispatch("mouseenter");
  await new Promise((r) => setTimeout(r, 0));
  tag.dispatch("mouseenter");
  await new Promise((r) => setTimeout(r, 0));

  assert.deepEqual(f.calls, ["item-9"], "a pointer crossing the mark must not re-request");
});

await atest("reveal: the cache is per ITEM and shared across rows", async () => {
  // The same attachment forwarded twice is one item, so it is one request.
  const f = countingFetcher({ state: "indexed", text: "same item" });
  const { renderer } = makeHarness({ fetchIndexedText: f.fn });
  const a = renderer.buildBubbleNode(mediaMsg("row-a")).querySelector(".xb-msg-savetag");
  const b = renderer.buildBubbleNode(mediaMsg("row-b")).querySelector(".xb-msg-savetag");

  a.dispatch("mouseenter");
  await new Promise((r) => setTimeout(r, 0));
  b.dispatch("mouseenter");
  await new Promise((r) => setTimeout(r, 0));

  assert.deepEqual(f.calls, ["item-9"]);
  assert.equal(b.querySelector(".xb-savetag-tip").textContent, "same item");
});

for (const [label, payload, expected] of [
  ["still being written", { state: "pending" }, "Indexing…"],
  [
    "deliberately skipped",
    { state: "not_indexed", detail: "This image is too large to index." },
    "This image is too large to index.",
  ],
  ["broken", { state: "failed" }, "Indexing failed."],
]) {
  await atest(`reveal: an attachment ${label} says so instead of showing nothing`, async () => {
    const f = countingFetcher(payload);
    const { renderer } = makeHarness({ fetchIndexedText: f.fn });
    const tag = renderer.buildBubbleNode(mediaMsg()).querySelector(".xb-msg-savetag");
    tag.dispatch("focus");
    await new Promise((r) => setTimeout(r, 0));
    assert.equal(tag.querySelector(".xb-savetag-tip").textContent, expected);
  });
}

await atest("reveal: a rejected request reads as a load failure, and is not retried", async () => {
  let calls = 0;
  const { renderer } = makeHarness({
    fetchIndexedText: () => {
      calls += 1;
      return Promise.reject(new Error("HTTP 403: not a member of this team"));
    },
  });
  const tag = renderer.buildBubbleNode(mediaMsg()).querySelector(".xb-msg-savetag");

  tag.dispatch("mouseenter");
  await new Promise((r) => setTimeout(r, 0));
  const shown = tag.querySelector(".xb-savetag-tip").textContent;

  assert.equal(shown, "The indexed text could not be loaded.");
  assert.ok(
    !shown.includes("403") && !shown.includes("not a member"),
    "a transport error's own words must not land in the thread",
  );

  tag.dispatch("mouseenter");
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(calls, 1, "a chat that keeps failing must not re-request on every hover");
});

await atest("reveal: the indexed text lands as TEXT, never as markup", async () => {
  const f = countingFetcher({
    state: "indexed",
    text: '<img src=x onerror="alert(1)">',
  });
  const { renderer } = makeHarness({ fetchIndexedText: f.fn });
  const tag = renderer.buildBubbleNode(mediaMsg()).querySelector(".xb-msg-savetag");
  tag.dispatch("mouseenter");
  await new Promise((r) => setTimeout(r, 0));

  const tip = tag.querySelector(".xb-savetag-tip");
  assert.equal(tip.textContent, '<img src=x onerror="alert(1)">');
  assert.equal(tip.children.length, 0, "no element may be created from the payload");
});

test("both stylesheets style the marker and open its reveal on hover AND focus", () => {
  for (const rel of [
    join("chrome-extension", "popup.css"),
    join("app-site", "app", "app.css"),
  ]) {
    const css = readFileSync(join(REPO_ROOT, rel), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
    assert.ok(/\.xb-savetag-tip\s*\{/.test(css), `${rel} has no .xb-savetag-tip rule`);
    assert.match(
      css,
      /\.xb-msg-savetag:hover\s+\.xb-savetag-tip\s*,\s*\.xb-msg-savetag:focus-visible\s+\.xb-savetag-tip\s*\{[^}]*display:\s*block/,
      `${rel}: the reveal must open on keyboard focus too — a hover-only tooltip does not exist for a keyboard`,
    );
    assert.match(
      css,
      /\.xb-msg-savetag:focus-visible\s*\{[^}]*outline:[^};]*var\(--ring\)/,
      `${rel}: the marker is tabbable, so it needs a visible focus ring`,
    );
    const tip = /(^|[};])\s*\.xb-savetag-tip\s*\{([^}]*)\}/.exec(css);
    assert.match(
      tip[2],
      /white-space:\s*pre-wrap/,
      `${rel}: the server sends a paragraph break between the text and its caveat`,
    );
  }
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
