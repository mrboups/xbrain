/**
 * Tests for the markdown renderer — packages/chat-core/markdown.js.
 *
 * Subject: the SOURCE, not a generated copy (test_chat_core_sync.mjs proves the
 * copies match), plus the two call sites in render.js and publication.js.
 *
 * THIS PATH IS THE XSS BOUNDARY. The message list carries text a teammate typed
 * and text a model produced after reading a web page — both hostile by default,
 * and this product has already shipped a stored-XSS-to-credential-theft bug. So
 * the assertions here are written to BITE, and each one was checked by breaking
 * the implementation and watching it go red. An assertion on a rendering path
 * that cannot fail is worse than none: it invites the next person to trust it.
 *
 * What is locked:
 *   (a) every supported construct produces the element it claims to;
 *   (b) unsupported syntax survives as the characters that were typed — an
 *       unrecognised construct is never mangled and never dropped;
 *   (c) every refused URL scheme renders as text, never as an anchor, including
 *       the uppercase, whitespace-padded and control-character-split spellings;
 *   (d) THE PROPERTY: innerHTML / outerHTML / insertAdjacentHTML appear nowhere
 *       in the renderer, and a payload that looks like HTML lands as visible
 *       text rather than as an element;
 *   (e) partial input — every prefix of a streaming answer — parses into whole
 *       nodes, and an unterminated `**` does not swallow the rest;
 *   (f) the DECISION: agent answers are parsed, a person's message is not.
 *
 * Pure node test — the same hand-rolled DOM stub the sibling render test uses,
 * no jsdom, no dependency. Picked up by run_tests.mjs (name starts with test_).
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import {
  renderMarkdownInto,
  safeHref,
  ALLOWED_LINK_SCHEMES,
} from "../../packages/chat-core/markdown.js";
import { createRenderer, rendersMarkdown } from "../../packages/chat-core/render.js";
import { createPublicationRouter } from "../../packages/chat-core/publication.js";
import { StreamBuffer } from "../../packages/chat-core/chat_stream.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, "..", "..");
const CORE_DIR = join(REPO_ROOT, "packages", "chat-core");

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
// DOM stub — only what markdown.js is ALLOWED to touch. Deliberately narrow:
// it has no innerHTML, no insertAdjacentHTML and no HTML parser of any kind, so
// a renderer that reached for one would throw here rather than quietly work.
// ---------------------------------------------------------------------------

class TextNode {
  constructor(text) {
    this.nodeType = 3;
    this.tagName = "#text";
    this.textContent = String(text);
    this.children = [];
  }
}

class El {
  constructor(doc, tag) {
    this.ownerDocument = doc;
    this.nodeType = 1;
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
    const doc = this.ownerDocument;
    if (doc && doc.activeElement === this) doc.activeElement = null;
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

function makeDoc() {
  const doc = {
    activeElement: null,
    createElement: (tag) => new El(doc, tag),
    createTextNode: (t) => new TextNode(t),
    addEventListener: () => {},
    removeEventListener: () => {},
    defaultView: { requestAnimationFrame: (fn) => fn() },
  };
  return doc;
}

/** Render `src` into a fresh element and hand back the element. */
function md(src) {
  const doc = makeDoc();
  const el = doc.createElement("span");
  renderMarkdownInto(el, src);
  return el;
}

/** Every descendant, in document order — the shape assertions read this. */
function descendants(node, out = []) {
  for (const c of node.children || []) {
    out.push(c);
    descendants(c, out);
  }
  return out;
}

/** The tag names of every ELEMENT descendant (text nodes excluded). */
function tags(node) {
  return descendants(node)
    .filter((n) => n.nodeType === 1)
    .map((n) => n.tagName);
}

/**
 * What a reader actually sees.
 *
 * `textContent` is not that: a soft line break is a <br>, which is the newline
 * made structural, and textContent reports it as nothing at all. Counting it as
 * the "\n" it renders is what lets the literal-preservation assertions compare
 * against the source string the person typed.
 */
function visibleText(node) {
  if (node.nodeType === 3) return node.textContent;
  if (node.tagName === "BR") return "\n";
  if (!node.children || node.children.length === 0) return node.textContent || "";
  return node.children.map(visibleText).join("");
}

// ===========================================================================
// (a) Every supported construct produces the element it claims to
// ===========================================================================

test("`**bold**` produces a STRONG carrying just the words", () => {
  const el = md("**Excalibur** is a game");
  const strong = el.querySelector(".xb-md-strong");
  assert.ok(strong, "bold must produce a .xb-md-strong");
  assert.equal(strong.tagName, "STRONG");
  assert.equal(strong.textContent, "Excalibur");
  // The markers are consumed, not printed — this is the whole defect.
  assert.equal(el.textContent, "Excalibur is a game");
  assert.ok(!el.textContent.includes("*"), "no asterisk may survive into the text");
});

test("`*italic*` produces an EM", () => {
  const el = md("that is *emphatic* indeed");
  const em = el.querySelector(".xb-md-em");
  assert.ok(em, "italic must produce a .xb-md-em");
  assert.equal(em.tagName, "EM");
  assert.equal(em.textContent, "emphatic");
  assert.equal(el.textContent, "that is emphatic indeed");
});

test("`` `code` `` produces a CODE whose content is never re-parsed", () => {
  const el = md("run `npm **install**` first");
  const code = el.querySelector(".xb-md-code");
  assert.ok(code, "a code span must produce a .xb-md-code");
  assert.equal(code.tagName, "CODE");
  // Asterisks inside backticks are asterisks. That is what a code span is FOR.
  assert.equal(code.textContent, "npm **install**");
  assert.equal(
    code.querySelector(".xb-md-strong"),
    null,
    "a code span must not be parsed for emphasis",
  );
});

test("a fenced block produces PRE > CODE holding the lines verbatim", () => {
  const el = md("before\n```js\nconst a = 1;\n  indented();\n```\nafter");
  const pre = el.querySelector(".xb-md-pre");
  assert.ok(pre, "a fence must produce a .xb-md-pre");
  assert.equal(pre.tagName, "PRE");
  const code = pre.querySelector(".xb-md-codeblock");
  assert.ok(code, "the block's text lives in a CODE inside the PRE");
  assert.equal(code.tagName, "CODE");
  assert.equal(code.textContent, "const a = 1;\n  indented();");
  // The info string is read and DISCARDED — no model-supplied token becomes a
  // class name.
  assert.ok(
    !/js/.test(code.className) && !/js/.test(pre.className),
    "the fence's language must not reach a class name",
  );
  // The paragraphs either side survive the block.
  const paras = el.querySelectorAll(".xb-md-p");
  assert.equal(paras.length, 2);
  assert.equal(paras[0].textContent, "before");
  assert.equal(paras[1].textContent, "after");
});

test("`- ` produces a UL of LI, one per line", () => {
  const el = md("- Skill-based rooms\n- Stablecoin payouts\n- No house edge");
  const ul = el.querySelector(".xb-md-ul");
  assert.ok(ul, "a dash list must produce a .xb-md-ul");
  assert.equal(ul.tagName, "UL");
  const items = ul.querySelectorAll(".xb-md-li");
  assert.equal(items.length, 3);
  assert.equal(items[0].tagName, "LI");
  assert.deepEqual(
    items.map((li) => li.textContent),
    ["Skill-based rooms", "Stablecoin payouts", "No house edge"],
  );
  assert.ok(!el.textContent.includes("- "), "the marker is consumed, not printed");
});

test("`1. ` produces an OL, and a list that starts at 3 says 3", () => {
  const first = md("1. one\n2. two");
  const ol = first.querySelector(".xb-md-ol");
  assert.ok(ol, "a numbered list must produce a .xb-md-ol");
  assert.equal(ol.tagName, "OL");
  assert.equal(ol.querySelectorAll(".xb-md-li").length, 2);
  assert.equal(ol.getAttribute("start"), null, "a list starting at 1 needs no start");

  // An answer that says "step 3" must not be renumbered into "step 1".
  const later = md("3. third\n4. fourth");
  assert.equal(later.querySelector(".xb-md-ol").getAttribute("start"), "3");
});

test("`#` headings produce a levelled .xb-md-h, and never an <h1>", () => {
  const el = md("# Title\n## Sub\n###### Deep");
  const hs = el.querySelectorAll(".xb-md-h");
  assert.equal(hs.length, 3);
  assert.deepEqual(
    hs.map((h) => h.dataset.level),
    ["1", "2", "6"],
  );
  assert.deepEqual(
    hs.map((h) => h.textContent),
    ["Title", "Sub", "Deep"],
  );
  // A message is not a document section: real heading tags from a model would
  // join the page outline and rearrange what a screen reader calls structure.
  for (const tag of tags(el)) {
    assert.ok(!/^H[1-6]$/.test(tag), `a message must not emit ${tag}`);
  }
});

test("a blank line splits paragraphs; a single newline is a line break", () => {
  const split = md("first para\n\nsecond para");
  const two = split.querySelectorAll(".xb-md-p");
  assert.equal(two.length, 2, "a blank line ends a paragraph");
  assert.deepEqual(
    two.map((p) => p.textContent),
    ["first para", "second para"],
  );

  const soft = md("line one\nline two");
  const one = soft.querySelectorAll(".xb-md-p");
  assert.equal(one.length, 1, "a single newline is not a paragraph break");
  assert.equal(
    tags(one[0]).filter((t) => t === "BR").length,
    1,
    "a soft break must be a <br> — structural, not a white-space declaration",
  );
  assert.equal(visibleText(soft), "line one\nline two");
});

test("a markdown link produces an A with an allowed href, target and rel", () => {
  const el = md("see [the docs](https://example.test/a?b=1#c) for more");
  const a = el.querySelector(".xb-md-a");
  assert.ok(a, "a link must produce a .xb-md-a");
  assert.equal(a.tagName, "A");
  assert.equal(a.getAttribute("href"), "https://example.test/a?b=1#c");
  assert.equal(a.getAttribute("target"), "_blank");
  const rel = a.getAttribute("rel") || "";
  assert.ok(rel.includes("noopener"), "rel must carry noopener");
  assert.ok(rel.includes("noreferrer"), "rel must carry noreferrer");
  assert.equal(a.textContent, "the docs");
  assert.equal(el.textContent, "see the docs for more");
});

test("http and mailto links are allowed too — the allowlist is the three", () => {
  assert.deepEqual(ALLOWED_LINK_SCHEMES, ["http:", "https:", "mailto:"]);
  assert.equal(
    md("[x](http://example.test/)").querySelector(".xb-md-a").getAttribute("href"),
    "http://example.test/",
  );
  assert.equal(
    md("[mail](mailto:team@example.test)").querySelector(".xb-md-a").getAttribute("href"),
    "mailto:team@example.test",
  );
});

test("emphasis nests one level inside emphasis and inside a link label", () => {
  const el = md("**bold with *italic* inside**");
  const strong = el.querySelector(".xb-md-strong");
  assert.ok(strong);
  assert.ok(strong.querySelector(".xb-md-em"), "emphasis nests");
  assert.equal(strong.textContent, "bold with italic inside");

  const link = md("[**loud** label](https://example.test/)");
  assert.ok(link.querySelector(".xb-md-a .xb-md-strong") || link.querySelector(".xb-md-strong"));
  assert.equal(link.querySelector(".xb-md-a").textContent, "loud label");
});

test("the real-world answer from the bug report renders with no asterisks left", () => {
  const el = md(
    "**Excalibur** is a competitive Web3 PvP game where players battle to earn " +
      "**USD stablecoins**\n\n**Key details:**\n- Skill-based rooms from **$2 to $1,000**",
  );
  assert.ok(!el.textContent.includes("**"), "no `**` may reach the screen");
  assert.equal(el.querySelectorAll(".xb-md-strong").length, 4);
  assert.equal(el.querySelectorAll(".xb-md-ul").length, 1);
  assert.equal(el.querySelectorAll(".xb-md-li").length, 1);
});

// ---------------------------------------------------------------------------
// Renderer harness — a real createRenderer over the stub document. Declared
// HERE rather than beside the tests that use it: sections (a2) and (f) both
// drive it, and `const NOW` is not hoisted, so a definition further down would
// be a temporal-dead-zone error at the moment the first test body runs.
// ---------------------------------------------------------------------------

function makeHarness() {
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
    getSelfUserId: () => "u-self",
    getNameCache: () => ({ "u-mate": "Mate" }),
    onAuthorClick: null,
    fetchIndexedText: null,
  });
  return { doc, listEl, renderer };
}

const NOW = "2026-08-05T10:00:00Z";

// ===========================================================================
// (a2) BARE URLs — the autolinker
//
// Same boundary as (c) below, arriving without the `[text](target)` wrapper. A
// bare URL is the form the agent actually emits ("🔗 Pitch deck: https://…"),
// and it used to render as readable, unclickable text.
//
// Every assertion here was checked by breaking the implementation:
//   - drop the tail trimmer          -> the punctuation tests go red
//   - drop the `inAnchor` guard      -> the nested-anchor test goes red
//   - widen the scheme pattern       -> the "looks like a link" tests go red
//   - drop the href===text rule      -> the control-character test goes red
//   - drop `partial`                 -> the streaming test goes red
// ===========================================================================

/** The one anchor an autolink is allowed to produce, unpacked for assertions. */
function onlyAnchor(el) {
  const anchors = el.querySelectorAll(".xb-md-a");
  assert.equal(anchors.length, 1, `expected exactly one anchor, got ${anchors.length}`);
  return anchors[0];
}

test("the bug report's own answer: two bare URLs, both clickable", () => {
  const el = md(
    "🔗 Pitch deck: https://pitch.com/v/excalibur-proposal-pybqby\n" +
      "🌐 Website: https://launch.excalibur.game",
  );
  const anchors = el.querySelectorAll(".xb-md-a");
  assert.equal(anchors.length, 2, "both bare URLs must become links");
  assert.deepEqual(
    anchors.map((a) => a.getAttribute("href")),
    ["https://pitch.com/v/excalibur-proposal-pybqby", "https://launch.excalibur.game"],
  );
  // A bare URL links to exactly the characters it shows.
  assert.deepEqual(
    anchors.map((a) => a.textContent),
    ["https://pitch.com/v/excalibur-proposal-pybqby", "https://launch.excalibur.game"],
  );
  for (const a of anchors) {
    assert.equal(a.getAttribute("target"), "_blank");
    const rel = a.getAttribute("rel") || "";
    assert.ok(rel.includes("noopener"), "an autolink carries noopener");
    assert.ok(rel.includes("noreferrer"), "an autolink carries noreferrer");
  }
  // Nothing was consumed: the labels either side are still readable.
  assert.equal(
    visibleText(el),
    "🔗 Pitch deck: https://pitch.com/v/excalibur-proposal-pybqby\n" +
      "🌐 Website: https://launch.excalibur.game",
  );
});

test("http, https and mailto autolink; the surrounding words are untouched", () => {
  for (const [src, href] of [
    ["go to https://x.test/a now", "https://x.test/a"],
    ["go to http://x.test/a now", "http://x.test/a"],
    ["write to mailto:team@x.test now", "mailto:team@x.test"],
    // The scheme is case-folded by the browser, so it is matched that way too.
    ["go to HTTPS://X.TEST/A now", "HTTPS://X.TEST/A"],
    ["go to Https://X.test/a now", "Https://X.test/a"],
  ]) {
    const el = md(src);
    assert.equal(onlyAnchor(el).getAttribute("href"), href, `href for ${src}`);
    assert.equal(visibleText(el), src, `${src} must read exactly as typed`);
  }
});

test("trailing punctuation is the sentence's, not the URL's", () => {
  // The failure this prevents: a href with a full stop on the end 404s, and the
  // reader is told nothing about why.
  for (const [src, href, tail] of [
    ["see https://x.test/a.", "https://x.test/a", "."],
    ["see https://x.test/a,", "https://x.test/a", ","],
    ["see https://x.test/a:", "https://x.test/a", ":"],
    ["see https://x.test/a;", "https://x.test/a", ";"],
    ["see https://x.test/a!", "https://x.test/a", "!"],
    ["see https://x.test/a?", "https://x.test/a", "?"],
    ["see https://x.test/a...", "https://x.test/a", "..."],
    ["see https://x.test/a).", "https://x.test/a", ")."],
  ]) {
    const el = md(src);
    assert.equal(onlyAnchor(el).getAttribute("href"), href, `href for ${src}`);
    assert.equal(onlyAnchor(el).textContent, href, `the link text for ${src}`);
    assert.ok(
      visibleText(el).endsWith(tail),
      `${src}: the punctuation must stay on screen, outside the link`,
    );
    assert.equal(visibleText(el), src, `${src} must read exactly as typed`);
  }
});

test("a closing paren belongs to the URL only when something inside it opened", () => {
  // The prose's paren.
  const prose = md("(see https://en.wikipedia.test/wiki/Foo)");
  assert.equal(onlyAnchor(prose).getAttribute("href"), "https://en.wikipedia.test/wiki/Foo");
  assert.equal(visibleText(prose), "(see https://en.wikipedia.test/wiki/Foo)");

  // The URL's own pair — the wikipedia case a flat "strip the paren" breaks.
  const balanced = md("see https://en.wikipedia.test/wiki/Foo_(bar) now");
  assert.equal(
    onlyAnchor(balanced).getAttribute("href"),
    "https://en.wikipedia.test/wiki/Foo_(bar)",
    "a balanced pair is part of the path",
  );

  // Both at once: one pair is the URL's, the outer one is the sentence's.
  const both = md("(see https://en.wikipedia.test/wiki/Foo_(bar))");
  assert.equal(
    onlyAnchor(both).getAttribute("href"),
    "https://en.wikipedia.test/wiki/Foo_(bar)",
  );
  assert.equal(visibleText(both), "(see https://en.wikipedia.test/wiki/Foo_(bar))");
});

test("a query string keeps every character a trimmer might mistake for prose", () => {
  const el = md("open https://x.test/a?b=1&c=2#frag now");
  assert.equal(onlyAnchor(el).getAttribute("href"), "https://x.test/a?b=1&c=2#frag");
});

test("NO SCHEME, NO LINK — a thing that merely looks like a URL stays text", () => {
  for (const src of [
    "visit www.example.test today",
    "visit example.test/path today",
    "mail nico@example.test today",
    "run ftp://files.test/a today",
    "see file:///etc/passwd today",
    "see javascript:alert(1) today",
    "see data:text/html,<b>x</b> today",
    "see vbscript:msgbox(1) today",
    "see chrome-extension://abc/popup.html today",
    "see //evil.test/steal today",
    "see s3://bucket/key today",
    // the scheme with nothing behind it is not a destination
    "see https:// today",
    "see mailto: today",
    "see https: today",
  ]) {
    const el = md(src);
    assert.equal(
      el.querySelector(".xb-md-a"),
      null,
      `${JSON.stringify(src)} must not become an anchor`,
    );
    for (const node of descendants(el)) {
      if (node.nodeType !== 1) continue;
      assert.equal(
        node.getAttribute && node.getAttribute("href"),
        null,
        `an href was set for ${JSON.stringify(src)}`,
      );
    }
    assert.equal(visibleText(el), src, `${JSON.stringify(src)} must read as typed`);
  }
});

test("a control character inside a bare URL refuses the link entirely", () => {
  // THE ONE THAT MATTERS. A browser deletes these before it resolves a URL, so
  // the href would not be the string on screen — and for a bare URL the string
  // on screen IS the only thing the reader can inspect before clicking. There is
  // nothing to choose between: the construct is refused and stays text.
  for (const src of [
    // What a reader sees ends at good.test; what a browser fetches is
    // evil.test, because the NUL is deleted and the rest becomes userinfo.
    "go to https://good.test\u0000@evil.test/ now",
    "go to https://goo\u0001d.test/a now",
    "go to https://good.test/\u001fa now",
    "go to https://good.test/a\u007f now",
  ]) {
    const el = md(src);
    assert.equal(
      el.querySelector(".xb-md-a"),
      null,
      `${JSON.stringify(src)} must not become an anchor`,
    );
    assert.equal(visibleText(el), src, "and nothing is dropped");
  }
  // The same URL without the control character IS linked — otherwise the
  // assertion above would be passing for the wrong reason.
  assert.equal(
    onlyAnchor(md("go to https://good.test/a now")).getAttribute("href"),
    "https://good.test/a",
  );
});

test("an autolink's href is byte-for-byte its own visible text", () => {
  // The property the control-character rule buys, stated directly: for a bare
  // URL there is no label to hide behind, so a destination that differs from the
  // characters on screen is a lie the reader cannot detect.
  for (const src of [
    "https://x.test/a",
    "see https://x.test/a?q=%20&r=1#z.",
    "(https://x.test/Foo_(bar))",
    "mailto:team@x.test,",
  ]) {
    const a = onlyAnchor(md(src));
    assert.equal(
      a.getAttribute("href"),
      a.textContent,
      `${JSON.stringify(src)}: the href and the text must be the same string`,
    );
  }
});

test("NO DOUBLE-LINKING: a URL already inside a link stays exactly one link", () => {
  // (i) the target of a markdown link.
  const target = md("read [the docs](https://x.test/a) now");
  const a = onlyAnchor(target);
  assert.equal(a.getAttribute("href"), "https://x.test/a");
  assert.equal(a.textContent, "the docs");

  // (ii) the LABEL of a markdown link. This is the one position cannot settle:
  // the label is re-parsed, and an anchor inside an anchor is not renderable.
  const label = md("[https://x.test/a](https://y.test/b)");
  const outer = onlyAnchor(label);
  assert.equal(outer.getAttribute("href"), "https://y.test/b", "the target wins");
  assert.equal(outer.textContent, "https://x.test/a", "the label reads as the URL it is");
  for (const node of descendants(outer)) {
    assert.notEqual(node.tagName, "A", "an anchor must never contain another anchor");
  }

  // (iii) both halves being URLs must not produce three anchors either.
  const emphasised = md("[**https://x.test/a**](https://y.test/b)");
  assert.equal(emphasised.querySelectorAll(".xb-md-a").length, 1);
});

test("NO DOUBLE-LINKING: a URL in code is an example, not a destination", () => {
  const span = md("run `curl https://x.test/a` first");
  assert.equal(span.querySelector(".xb-md-a"), null, "a code span must not autolink");
  assert.equal(span.querySelector(".xb-md-code").textContent, "curl https://x.test/a");

  const block = md("```sh\ncurl https://x.test/a\n```");
  assert.equal(block.querySelector(".xb-md-a"), null, "a fenced block must not autolink");
  assert.equal(block.querySelector(".xb-md-codeblock").textContent, "curl https://x.test/a");

  // A URL AFTER the code span on the same line is still linked — otherwise the
  // two assertions above could pass by the autolinker being off entirely.
  const mixed = md("run `curl` against https://x.test/a now");
  assert.equal(onlyAnchor(mixed).getAttribute("href"), "https://x.test/a");
});

test("an autolink inside emphasis, a list and a heading — one link, no leakage", () => {
  for (const src of [
    "**https://x.test/a**",
    "*https://x.test/a*",
    "- https://x.test/a",
    "1. https://x.test/a",
    "# https://x.test/a",
    "> https://x.test/a",
  ]) {
    const el = md(src);
    assert.equal(
      onlyAnchor(el).getAttribute("href"),
      "https://x.test/a",
      `href inside ${JSON.stringify(src)}`,
    );
  }
  // The emphasis is still emphasis, and the anchor is inside it. Walked in two
  // steps because the stub's querySelector takes one class, not a descendant
  // combinator — a chained selector here would return null and read as a bug.
  const bold = md("**https://x.test/a**").querySelector(".xb-md-strong");
  assert.ok(bold, "the bold markers still produce a STRONG");
  assert.ok(bold.querySelector(".xb-md-a"), "and the link nests inside it");
});

test("a bare URL cannot smuggle an element in, whatever it carries", () => {
  for (const src of [
    'https://x.test/<img src=x onerror=alert(1)>',
    'https://x.test/a"onmouseover="alert(1)',
    "https://x.test/a'onmouseover='alert(1)",
  ]) {
    const el = md(src);
    for (const tag of tags(el)) {
      assert.ok(
        ["DIV", "SPAN", "P", "BR", "STRONG", "EM", "CODE", "PRE", "UL", "OL", "LI", "A"].includes(tag),
        `a ${tag} was constructed from ${JSON.stringify(src)}`,
      );
    }
    // Every character still reaches the screen, whether inside the link or beside it.
    assert.equal(visibleText(el), src);
    const a = el.querySelector(".xb-md-a");
    if (a) {
      const href = a.getAttribute("href");
      assert.ok(
        href.startsWith("https://"),
        `an autolinked href must carry an allowed scheme, got ${JSON.stringify(href)}`,
      );
      assert.equal(href, a.textContent, "and it still equals what is on screen");
    }
  }
});

test("STREAMING: a URL still arriving is not linked, and is linked once when it is", () => {
  // The failure: the buffer holds `https://pitch.com/v/excalibur-prop`, that is
  // rendered as a clickable link to a page that does not exist, and the next
  // chunk replaces it with a different link. `partial` is what says "the last
  // character is not the last character".
  const doc = makeDoc();
  const el = doc.createElement("span");
  const full = "Pitch deck: https://pitch.com/v/excalibur-proposal-pybqby";

  for (let n = 13; n < full.length; n++) {
    renderMarkdownInto(el, full.slice(0, n), { partial: true });
    assert.equal(
      el.querySelector(".xb-md-a"),
      null,
      `prefix ${n} linked a URL that is still arriving`,
    );
    assert.equal(visibleText(el), full.slice(0, n), `prefix ${n} must read as typed`);
  }

  // The finishing pass renders the SAME string, and now it is a link.
  renderMarkdownInto(el, full, { partial: true });
  assert.equal(el.querySelector(".xb-md-a"), null, "still open-ended, still text");
  renderMarkdownInto(el, full);
  const a = el.querySelector(".xb-md-a");
  assert.ok(a, "the finishing pass must link it — the memo cannot swallow that render");
  assert.equal(a.getAttribute("href"), "https://pitch.com/v/excalibur-proposal-pybqby");
  assert.equal(visibleText(el), full);
});

test("STREAMING: only the growing edge waits — a URL with text after it links now", () => {
  // Otherwise "do not link half-formed" would mean "do not link while streaming",
  // and an answer's earlier links would appear only at the end.
  const doc = makeDoc();
  const el = doc.createElement("span");
  renderMarkdownInto(el, "see https://x.test/a and also htt", { partial: true });
  const a = el.querySelector(".xb-md-a");
  assert.ok(a, "a URL followed by more text has finished arriving");
  assert.equal(a.getAttribute("href"), "https://x.test/a");

  // A newline ends a line whatever comes next, so a URL on an earlier line is done.
  const two = doc.createElement("span");
  renderMarkdownInto(two, "https://x.test/a\nhttps://x.test/b", { partial: true });
  assert.equal(
    two.querySelectorAll(".xb-md-a").length,
    1,
    "the finished line links, the growing one does not",
  );
  assert.equal(two.querySelector(".xb-md-a").getAttribute("href"), "https://x.test/a");
});

test("STREAMING: a whole answer never renders a link it then changes", () => {
  // The property across EVERY prefix: whatever href exists at prefix n must
  // still exist, unchanged, at prefix n+1. A URL that got linked half-formed and
  // relinked breaks this at the boundary where it grew.
  const answer =
    "Two things.\n\n" +
    "- Deck: https://pitch.com/v/excalibur-proposal-pybqby\n" +
    "- Site: https://launch.excalibur.game\n\n" +
    "Ask me anything.";
  const doc = makeDoc();
  const el = doc.createElement("span");
  let previous = [];
  for (let n = 0; n <= answer.length; n++) {
    renderMarkdownInto(el, answer.slice(0, n), { partial: true });
    const hrefs = el.querySelectorAll(".xb-md-a").map((a) => a.getAttribute("href"));
    for (let k = 0; k < previous.length; k++) {
      assert.equal(
        hrefs[k],
        previous[k],
        `prefix ${n}: link ${k} changed from ${previous[k]} to ${hrefs[k]}`,
      );
    }
    for (const a of el.querySelectorAll(".xb-md-a")) {
      assert.equal(a.getAttribute("href"), a.textContent, `prefix ${n}: href drifted from text`);
    }
    previous = hrefs;
  }
  // And the finished answer carries both.
  renderMarkdownInto(el, answer);
  assert.equal(el.querySelectorAll(".xb-md-a").length, 2);
});

test("the router streams partial and finishes closed — the wiring, not just the parser", () => {
  const { renderer, listEl } = makeHarness();
  const route = createPublicationRouter({
    renderer,
    streamBuffer: new StreamBuffer(),
    onNonEmpty: () => {},
  });
  route({ type: "agent_stream_start", message_id: "u1", agent_name: "agent" });
  route({ type: "agent_stream_chunk", message_id: "u1", delta: "Deck: https://pitch.com" });
  assert.equal(
    renderer.streamTextTarget("u1").querySelector(".xb-md-a"),
    null,
    "mid-stream, the URL at the edge is not a link yet",
  );
  route({ type: "agent_stream_chunk", message_id: "u1", delta: "/v/excalibur" });
  assert.equal(renderer.streamTextTarget("u1").querySelector(".xb-md-a"), null);

  route({ type: "agent_stream_end", message_id: "u1" });
  const a = listEl.querySelector(".xb-md-a");
  assert.ok(a, "agent_stream_end must render once more, closed — or the URL stays dead text");
  assert.equal(a.getAttribute("href"), "https://pitch.com/v/excalibur");
  assert.equal(
    renderer.streamTextTarget("u1").textContent,
    "Deck: https://pitch.com/v/excalibur",
  );
});

test("a teammate's message is still not parsed — no autolink there either", () => {
  // The DECISION is unchanged by this feature: a person types into a plain box.
  // Autolinking their message would be markdown parsing by another name.
  const { renderer } = makeHarness();
  const node = renderer.buildBubbleNode({
    id: "u-url",
    kind: "user",
    author_user_id: "u-mate",
    content: "have a look at https://x.test/a",
    created_at: NOW,
  });
  const text = node.querySelector(".xb-msg-text");
  assert.equal(text.textContent, "have a look at https://x.test/a");
  assert.equal(text.children.length, 0, "a person's message builds no elements at all");
});

// ===========================================================================
// (b) Unsupported syntax stays as the characters that were typed
// ===========================================================================

test("unsupported constructs render literally — nothing is mangled or dropped", () => {
  const cases = [
    // arithmetic must survive: spaces inside the span refuse the emphasis
    "2 * 3 * 4 = 24",
    "a * b",
    // markdown this subset does not carry
    "~~struck~~",
    "> a block quote",
    "| a | table |",
    "term\n: definition",
    "[ref][1]",
    "<https://autolink.test>",
    "$$math$$",
    "#nospace is not a heading",
    "-nospace is not a list",
    "1.nospace is not a list",
    "___underscored___",
    "foo_bar_baz",
    "50% * 2",
  ];
  for (const src of cases) {
    const el = md(src);
    assert.equal(
      visibleText(el),
      src,
      `\`${src}\` must reach the screen exactly as typed`,
    );
  }
});

test("a backslash disarms a marker instead of letting it eat the text", () => {
  // Without this the italic branch runs from the first `*` to the second and
  // BOTH asterisks vanish — a writer who explicitly asked for a literal
  // asterisk would get emphasis and no asterisk at all.
  const el = md("a \\*escaped\\* b");
  assert.equal(el.querySelector(".xb-md-em"), null, "an escaped marker opens nothing");
  assert.equal(visibleText(el), "a *escaped* b");

  const inside = md("**bold with \\* inside**");
  assert.equal(inside.querySelector(".xb-md-strong").textContent, "bold with * inside");

  // An escaped `[` disarms the LINK, and that is what this asserts: the label
  // never becomes an anchor's text, and every character survives. The bare URL
  // left standing in the prose is autolinked like any other bare URL — the
  // escape was about the bracket, and a clickable URL that reads as itself
  // takes nothing away from the reader.
  const escaped = md("\\[not a link](https://example.test/)");
  assert.equal(visibleText(escaped), "[not a link](https://example.test/)");
  const escapedAnchor = escaped.querySelector(".xb-md-a");
  assert.notEqual(
    escapedAnchor && escapedAnchor.textContent,
    "not a link",
    "the escaped bracket must not have produced a markdown link",
  );
  assert.equal(
    escapedAnchor && escapedAnchor.getAttribute("href"),
    "https://example.test/",
    "the bare URL that remains is autolinked, and links to exactly itself",
  );

  assert.equal(visibleText(md("a \\\\ backslash")), "a \\ backslash");
  // A backslash before something that is not a marker is just a backslash.
  assert.equal(visibleText(md("C:\\Users\\name")), "C:\\Users\\name");
});

test("an image is refused as an image AND refused as a link — it stays text", () => {
  const src = "![alt text](https://tracker.test/pixel.gif)";
  const el = md(src);
  assert.equal(el.textContent, src, "the whole construct stays verbatim");
  assert.equal(el.querySelector(".xb-md-a"), null, "no anchor is minted from an image");
  for (const tag of tags(el)) {
    assert.notEqual(tag, "IMG", "a message may never create an IMG");
    assert.notEqual(tag, "A", "`![x](y)` must not become `!` plus a link");
  }
});

test("an unterminated marker stays literal and does not swallow the rest", () => {
  for (const src of [
    "**never closed and then more text",
    "*also never closed, more text",
    "`code never closed, more text",
    "[label](https://example.test/ never closed",
    "[label without a target",
  ]) {
    const el = md(src);
    assert.equal(el.textContent, src, `\`${src}\` must survive whole`);
  }
});

test("an open `**` dies with its line — it cannot reach a closer further down", () => {
  // The real mechanism, pinned: inline parsing is handed ONE LINE at a time, so
  // a marker opened on line 1 cannot pair with one on line 3 and eat the two
  // lines between them. Break that (join a paragraph's lines before parsing
  // them) and this goes red.
  const el = md("**opened on line one\nan innocent middle line\nand later **closed** here");
  const strong = el.querySelectorAll(".xb-md-strong");
  assert.equal(strong.length, 1, "exactly one emphasis, and it is the closed pair");
  assert.equal(strong[0].textContent, "closed");
  assert.equal(
    visibleText(el),
    "**opened on line one\nan innocent middle line\nand later closed here",
    "the open marker prints as typed and the middle line is untouched",
  );
});

// ===========================================================================
// (c) Every refused URL scheme
// ===========================================================================

const REFUSED_TARGETS = [
  "javascript:alert(1)",
  "JavaScript:alert(1)",
  "JAVASCRIPT:alert(1)",
  "jAvAsCrIpT:alert(1)",
  " javascript:alert(1)",
  "\tjavascript:alert(1)",
  "\njavascript:alert(1)",
  "  \t\n javascript:alert(1)",
  // a browser strips control characters before it resolves the URL, so a
  // scheme split by one is still that scheme
  "java\tscript:alert(1)",
  "java\nscript:alert(1)",
  "java\rscript:alert(1)",
  "jav\u0000ascript:alert(1)",
  "java\u007fscript:alert(1)",
  "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
  "DATA:text/html,<script>alert(1)</script>",
  "vbscript:msgbox(1)",
  "VBScript:msgbox(1)",
  "blob:https://example.test/uuid",
  "filesystem:https://example.test/temporary/x",
  "file:///etc/passwd",
  "chrome-extension://abc/popup.html",
  "about:blank",
  // protocol-relative: inherits OUR scheme, wearing none a reader could check
  "//evil.test/steal",
  "\\\\evil.test\\share",
  // relative: a chat message is not part of this app's navigation
  "/account/teams/",
  "popup.html",
  "#anchor",
  "?q=1",
  "",
];

test("safeHref refuses every scheme outside the allowlist", () => {
  for (const target of REFUSED_TARGETS) {
    assert.equal(
      safeHref(target),
      null,
      `safeHref must refuse ${JSON.stringify(target)}`,
    );
  }
  assert.equal(safeHref(null), null);
  assert.equal(safeHref(undefined), null);
  assert.equal(safeHref(12), null);
});

test("safeHref returns the STRIPPED url, so what was checked is what ships", () => {
  // The gap this closes: validating the raw string and then assigning the raw
  // string lets `java\nscript:` through, because the browser strips it AFTER
  // the check. The value returned here is the value that was tested.
  assert.equal(safeHref("  https://example.test/a  "), "https://example.test/a");
  assert.equal(safeHref("https://exa\nmple.test/a"), "https://example.test/a");
  assert.equal(safeHref("HTTPS://Example.test/A"), "HTTPS://Example.test/A");
});

test("a refused target renders as TEXT — no anchor, no href, nothing lost", () => {
  for (const target of REFUSED_TARGETS) {
    const src = `click [here](${target}) now`;
    const el = md(src);
    assert.equal(
      el.querySelector(".xb-md-a"),
      null,
      `${JSON.stringify(target)} must not become an anchor`,
    );
    for (const node of descendants(el)) {
      if (node.nodeType !== 1) continue;
      assert.notEqual(node.tagName, "A", `an A was built for ${JSON.stringify(target)}`);
      assert.equal(
        node.getAttribute && node.getAttribute("href"),
        null,
        `an href was set for ${JSON.stringify(target)}`,
      );
    }
    // Whitespace inside the target breaks the link grammar itself, so those
    // spellings stay literal for a second reason. Either way the reader sees
    // the characters that were written; nothing disappears.
    assert.ok(
      el.textContent.includes("click ") && el.textContent.includes(" now"),
      `the surrounding words survived for ${JSON.stringify(target)}`,
    );
  }
});

// ===========================================================================
// (d) THE PROPERTY: no markup string, ever
// ===========================================================================

const MARKUP_SINKS = ["innerHTML", "outerHTML", "insertAdjacentHTML"];
const RENDER_PATH = ["markdown.js", "render.js", "publication.js"];

test("the renderer names no HTML-parsing sink — the safety argument itself", () => {
  // Written to FAIL: adding `el.innerHTML = x` to any of the three files below
  // turns this red. It is a source scan rather than a behavioural probe because
  // the sink is dangerous the moment it EXISTS, not only when a test reaches it.
  for (const name of RENDER_PATH) {
    const source = readFileSync(join(CORE_DIR, name), "utf8");
    for (const sink of MARKUP_SINKS) {
      assert.ok(
        !source.includes(sink),
        `packages/chat-core/${name} names \`${sink}\` — every character in a ` +
          `message must reach the document through textContent / createTextNode`,
      );
    }
  }
});

test("the scan actually covers the file it claims to — guard against a silent miss", () => {
  // The failure this exists for: a scan pointed at a path that does not resolve
  // passes forever. Each file is read, is non-trivial, and contains the node
  // construction the property is about.
  for (const name of RENDER_PATH) {
    const source = readFileSync(join(CORE_DIR, name), "utf8");
    assert.ok(source.length > 500, `${name} read back as ${source.length} bytes`);
  }
  const markdown = readFileSync(join(CORE_DIR, "markdown.js"), "utf8");
  assert.ok(
    markdown.includes("createElement") && markdown.includes("createTextNode"),
    "markdown.js must build nodes, which is what makes the absence of a sink mean something",
  );
});

test("a payload that looks like HTML lands as visible TEXT, creating no element", () => {
  const payloads = [
    '<img src=x onerror=alert(1)>',
    '<script>alert(document.cookie)</script>',
    '<svg/onload=alert(1)>',
    '<iframe src="javascript:alert(1)"></iframe>',
    '<a href="javascript:alert(1)">click</a>',
    "<style>body{display:none}</style>",
  ];
  for (const payload of payloads) {
    const el = md(payload);
    assert.equal(el.textContent, payload, "the raw string must be visible, verbatim");
    for (const tag of tags(el)) {
      assert.ok(
        ["DIV", "SPAN", "P", "BR", "STRONG", "EM", "CODE", "PRE", "UL", "OL", "LI"].includes(tag),
        `a ${tag} was constructed from message content`,
      );
    }
  }
});

test("HTML inside every construct is still text — the payload cannot ride a marker", () => {
  const payload = '<img src=x onerror=alert(1)>';
  for (const src of [
    `**${payload}**`,
    `*${payload}*`,
    `\`${payload}\``,
    `- ${payload}`,
    `1. ${payload}`,
    `# ${payload}`,
    "```\n" + payload + "\n```",
    `[${payload}](https://example.test/)`,
  ]) {
    const el = md(src);
    assert.ok(
      el.textContent.includes(payload),
      `the payload must stay visible inside \`${src.slice(0, 20)}…\``,
    );
    for (const tag of tags(el)) {
      assert.notEqual(tag, "IMG", `an IMG was constructed from \`${src.slice(0, 20)}…\``);
      assert.notEqual(tag, "SCRIPT", "a SCRIPT was constructed from message content");
    }
  }
});

// ===========================================================================
// (e) Streaming — every prefix of an answer parses into whole nodes
// ===========================================================================

const STREAMED_ANSWER =
  "**Excalibur** is a competitive Web3 PvP game.\n\n" +
  "**Key details:**\n" +
  "- Rooms from **$2** to **$1,000**\n" +
  "- Payout in `USDC`\n\n" +
  "```py\nprint('hi')\n```\n\n" +
  "See [the docs](https://example.test/).";

/** Is `needle` obtainable from `hay` by deleting characters? */
function isSubsequence(needle, hay) {
  let i = 0;
  for (const ch of hay) if (i < needle.length && needle[i] === ch) i++;
  return i === needle.length;
}

test("every prefix of a streaming answer renders into whole, well-formed nodes", () => {
  // The bubble is rewritten from the WHOLE buffer on every chunk, so the parser
  // meets each of these prefixes for real. Walking all of them is the only way
  // to catch the one boundary that produces a half-built node — the frame where
  // a fence has opened, a marker is dangling, or a list has one item so far.
  let checked = 0;
  for (let n = 0; n <= STREAMED_ANSWER.length; n++) {
    const prefix = STREAMED_ANSWER.slice(0, n);
    const el = md(prefix);
    for (const node of descendants(el)) {
      if (node.nodeType === 3) continue;
      assert.equal(node.nodeType, 1, `a stray node type at prefix length ${n}`);
      assert.ok(
        node.tagName === "BR" || /(^|\s)xb-md-/.test(node.className),
        `element ${node.tagName} at prefix ${n} carries no xb-md class: "${node.className}"`,
      );
      // A node with no class would also mean an element built from a path this
      // parser does not own — the shape a markup sink would produce.
      assert.ok(
        ["DIV", "SPAN", "BR", "STRONG", "EM", "CODE", "PRE", "UL", "OL", "LI", "A"].includes(
          node.tagName,
        ),
        `element ${node.tagName} at prefix ${n} is not in the grammar`,
      );
    }
    // NOTHING INVENTED: every visible character came from the answer, in order.
    // A parser that emitted a marker it had consumed, doubled a run of text, or
    // leaked a URL into the body would fail here.
    const visible = visibleText(el).replace(/\s+/g, "");
    assert.ok(
      isSubsequence(visible, prefix.replace(/\s+/g, "")),
      `prefix ${n} rendered text that is not in the source: ${JSON.stringify(visible)}`,
    );
    checked++;
  }
  assert.equal(checked, STREAMED_ANSWER.length + 1, "every prefix must have been checked");
  assert.ok(checked > 150, "the sample answer is too short to be a streaming test");
});

test("a marker-free answer streams character-for-character, losing nothing", () => {
  // The other half of the property: with no construct to consume, every prefix
  // must come back exactly as it went in. This is what would catch a parser
  // that silently dropped a line it did not recognise.
  const plain = "Excalibur is a competitive game.\nRooms are skill based.\n\nThat is all.";
  for (let n = 0; n <= plain.length; n++) {
    const prefix = plain.slice(0, n);
    // Paragraph boundaries are blocks, so compare block by block.
    const blocks = md(prefix).children.map(visibleText);
    assert.equal(
      blocks.join("\n\n"),
      prefix.replace(/\n{2,}/g, "\n\n").replace(/\n+$/, ""),
      `prefix ${n} did not survive intact`,
    );
  }
});

test("a `**` that has not closed yet does not swallow the rest of the answer", () => {
  // The exact mid-stream frame: the opener has arrived, the closer has not.
  const el = md("Here is **Excalibur, a competitive Web3 PvP game where players");
  assert.equal(el.querySelector(".xb-md-strong"), null, "an open marker is not emphasis");
  assert.ok(
    el.textContent.includes("competitive Web3 PvP game where players"),
    "everything after the open marker is still rendered",
  );
  assert.ok(el.textContent.startsWith("Here is **Excalibur"), "the marker shows as typed");
});

test("an unterminated fence still renders its lines instead of hiding them", () => {
  const el = md("look:\n```py\nprint('one')\nprint('two')");
  const code = el.querySelector(".xb-md-codeblock");
  assert.ok(code, "a fence that has not closed yet is still a block");
  assert.equal(code.textContent, "print('one')\nprint('two')");
});

test("re-rendering the same text leaves the DOM untouched — no churn per chunk", () => {
  const doc = makeDoc();
  const el = doc.createElement("span");
  renderMarkdownInto(el, "**hello** world");
  const before = el.children.slice();
  renderMarkdownInto(el, "**hello** world");
  assert.deepEqual(el.children, before, "an identical re-render must not rebuild");
  renderMarkdownInto(el, "**hello** world!");
  assert.notDeepEqual(el.children, before, "a changed answer must rebuild");
});

test("the body element is tagged `xb-md` so the stylesheet can find it", () => {
  const doc = makeDoc();
  const el = doc.createElement("span");
  el.className = "xb-msg-text";
  renderMarkdownInto(el, "hi");
  assert.ok(el.classList.contains("xb-msg-text"), "the existing class survives");
  assert.ok(el.classList.contains("xb-md"), "the markdown class is added");
  // Empty content leaves NO children, which is what keeps
  // `.xb-msg-bubble.is-failed .xb-msg-text:empty { display: none }` working.
  const empty = doc.createElement("span");
  renderMarkdownInto(empty, "");
  assert.equal(empty.children.length, 0);
});

// ===========================================================================
// (f) THE DECISION: agent answers are parsed, a person's message is not
// ===========================================================================

test("rendersMarkdown: the agent yes, a teammate no, your own message no", () => {
  assert.equal(rendersMarkdown({ kind: "agent" }), true);
  assert.equal(rendersMarkdown({ kind: "user" }), false);
  assert.equal(rendersMarkdown({ kind: undefined }), false);
  assert.equal(rendersMarkdown(null), false);
});

test("an AGENT message is parsed — the answer arrives formatted", () => {
  const { renderer } = makeHarness();
  const node = renderer.buildBubbleNode({
    id: "a1",
    kind: "agent",
    agent_name: "agent",
    content: "**Excalibur** is a game",
    created_at: NOW,
  });
  const text = node.querySelector(".xb-msg-text");
  assert.ok(text.classList.contains("xb-md"), "an agent body renders markdown");
  assert.ok(text.querySelector(".xb-md-strong"), "the emphasis became an element");
  assert.equal(text.textContent, "Excalibur is a game");
});

test("a TEAMMATE's message is NOT parsed — it reads exactly as they typed it", () => {
  // The decision, pinned: a person types into a plain box with no preview and
  // no escape hatch, so `2 * 3 * 4` keeps its asterisks and a pasted `- ` list
  // keeps its dashes. What they typed is what everyone reads.
  const { renderer } = makeHarness();
  for (const content of ["2 * 3 * 4 = 24", "**not bold**", "- not a list", "# not a heading"]) {
    const node = renderer.buildBubbleNode({
      id: `u-${content.length}`,
      kind: "user",
      author_user_id: "u-mate",
      content,
      created_at: NOW,
    });
    const text = node.querySelector(".xb-msg-text");
    assert.equal(text.textContent, content, "a person's characters are theirs");
    assert.ok(!text.classList.contains("xb-md"), "no markdown class on a person's message");
    assert.equal(text.children.length, 0, "a person's message builds no elements at all");
  }
});

test("a failed agent turn with no output still renders nothing in the answer slot", () => {
  const { renderer } = makeHarness();
  const node = renderer.buildBubbleNode({
    id: "a2",
    kind: "agent",
    content: "The provider said your credit balance is too low",
    created_at: NOW,
    metadata: { agent_failure: { code: "unavailable" } },
  });
  const text = node.querySelector(".xb-msg-text");
  assert.equal(text.textContent, "", "the server's failure sentence is not the answer");
  assert.equal(text.children.length, 0);
});

test("the stream router renders markdown as the answer accumulates", () => {
  const { listEl, renderer } = makeHarness();
  const route = createPublicationRouter({
    renderer,
    streamBuffer: new StreamBuffer(),
    onNonEmpty: () => {},
  });
  route({ type: "agent_stream_start", message_id: "s1", agent_name: "agent" });

  // Mid-stream: the opener has arrived and the closer has not.
  route({ type: "agent_stream_chunk", message_id: "s1", delta: "**Exc" });
  let body = renderer.streamTextTarget("s1");
  assert.equal(body.querySelector(".xb-md-strong"), null, "no half-open emphasis");
  assert.equal(body.textContent, "**Exc", "the characters so far are on screen");

  // The closer lands and the same buffer becomes formatted text.
  route({ type: "agent_stream_chunk", message_id: "s1", delta: "alibur** wins" });
  body = renderer.streamTextTarget("s1");
  assert.ok(body.querySelector(".xb-md-strong"), "the closed marker became an element");
  assert.equal(body.textContent, "Excalibur wins");

  route({ type: "agent_stream_end", message_id: "s1" });
  assert.equal(
    listEl.querySelector(".xb-msg-text").textContent,
    "Excalibur wins",
    "the finished answer stays formatted",
  );
});

test("a streamed answer cannot build an element out of an HTML payload", () => {
  const { renderer } = makeHarness();
  const route = createPublicationRouter({
    renderer,
    streamBuffer: new StreamBuffer(),
    onNonEmpty: () => {},
  });
  route({ type: "agent_stream_start", message_id: "s2", agent_name: "agent" });
  route({ type: "agent_stream_chunk", message_id: "s2", delta: '<img src=x ' });
  route({ type: "agent_stream_chunk", message_id: "s2", delta: 'onerror=alert(1)>' });
  const body = renderer.streamTextTarget("s2");
  assert.equal(body.textContent, '<img src=x onerror=alert(1)>');
  for (const tag of tags(body)) assert.notEqual(tag, "IMG");
});

// ---- the two surfaces actually ship the module ----

test("both surfaces load markdown.js — a shared module nobody imports is dead", () => {
  const render = readFileSync(join(CORE_DIR, "render.js"), "utf8");
  assert.ok(
    /import\s*\{[^}]*renderMarkdownInto[^}]*\}\s*from\s*"\.\/markdown\.js"/.test(render),
    "render.js must import renderMarkdownInto from ./markdown.js",
  );
  const sw = readFileSync(join(REPO_ROOT, "app-site", "app", "sw.js"), "utf8");
  assert.ok(
    sw.includes('"/app/chat_core/markdown.js"'),
    "sw.js must precache markdown.js, or the PWA loads render.js against a module the cache never fetched",
  );
  // A shell whose CONTENT changed under an unchanged name serves a mix of old
  // and new files to everyone who already has it. This has been missed twice.
  const cache = /const CACHE = "([^"]+)"/.exec(sw);
  assert.ok(cache, "sw.js must declare a CACHE name");
  assert.notEqual(
    cache[1],
    "xb-app-shell-v9",
    "CACHE must be bumped past v9 — the shell's content changed",
  );
});

// ---- the stylesheets carry the rules these nodes need ----

test("both stylesheets style the markdown nodes, in a code block that cannot widen a bubble", () => {
  for (const rel of [
    join("chrome-extension", "popup.css"),
    join("app-site", "app", "app.css"),
  ]) {
    const css = readFileSync(join(REPO_ROOT, rel), "utf8");
    for (const cls of [
      ".xb-md-p",
      ".xb-md-h",
      ".xb-md-ul",
      ".xb-md-ol",
      ".xb-md-li",
      ".xb-md-strong",
      ".xb-md-em",
      ".xb-md-code",
      ".xb-md-pre",
      ".xb-md-a",
    ]) {
      assert.ok(css.includes(cls), `${rel} styles no ${cls}`);
    }
    // The phone rule: a long line scrolls inside the code box, and the box
    // never grows past the bubble.
    const pre = /\.xb-md-pre\s*\{([^}]*)\}/.exec(css);
    assert.ok(pre, `${rel} has no .xb-md-pre rule`);
    assert.ok(/overflow-x:\s*auto/.test(pre[1]), `${rel}: .xb-md-pre must scroll horizontally`);
    assert.ok(/max-width:\s*100%/.test(pre[1]), `${rel}: .xb-md-pre must not exceed the bubble`);
    // The bubble is pre-wrap for plain text; a markdown body must opt out or
    // every source newline prints twice.
    const body = /\.xb-msg-text\.xb-md\s*\{([^}]*)\}/.exec(css);
    assert.ok(body, `${rel} has no .xb-msg-text.xb-md rule`);
    assert.ok(/white-space:\s*normal/.test(body[1]), `${rel}: the markdown body must not inherit pre-wrap`);
    assert.ok(/display:\s*block/.test(body[1]), `${rel}: the markdown body holds blocks`);
    // The common case must not get taller: a one-paragraph answer goes back to
    // inline flow so the timestamp keeps sharing its last line.
    assert.ok(
      /\.xb-msg-text\.xb-md:has\(>\s*\.xb-md-p:only-child\)\s*\{[^}]*display:\s*inline/.test(css),
      `${rel}: a single-paragraph answer must lay out inline, or every agent bubble grows a line`,
    );
    // Tokens only — both themes come free, and neither surface hardcodes a hex.
    const section = css.slice(css.indexOf(".xb-msg-text.xb-md"));
    const mdRules = section.slice(0, section.indexOf(".xb-md-a:focus-visible"));
    assert.ok(
      !/#[0-9a-fA-F]{3,8}\b/.test(mdRules),
      `${rel}: the markdown rules hardcode a colour instead of a theme token`,
    );
  }
});

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
