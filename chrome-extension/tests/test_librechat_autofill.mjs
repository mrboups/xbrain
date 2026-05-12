/**
 * Tests for chrome-extension/librechat_autofill.js (quick task 260512-spx).
 *
 * The content script runs in a browser/extension context. To exercise the
 * pure heuristic in node we stub a minimal DOM via globalThis (Node ≥ 18
 * provides Event/EventTarget natively but no document) and load the script
 * in a Function() so its IIFE exposes the helpers on globalThis.
 *
 * We DON'T test the MutationObserver wiring (browser-only). We test:
 *   1. shouldFillField rejects non-input nodes.
 *   2. shouldFillField rejects inputs of irrelevant types (button, checkbox).
 *   3. shouldFillField requires the surrounding card to mention Claude Pro/Max.
 *   4. shouldFillField accepts type=password inputs when anchor matches.
 *   5. shouldFillField accepts type=text inputs whose attributes scream "api key".
 *   6. shouldFillField rejects an api-key input anchored to a different endpoint
 *      (Anthropic, OpenAI) — proves we won't fill the wrong dialog.
 *   7. fillInputAsReact uses the native setter (so React picks up the change).
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const SCRIPT = readFileSync(
  join(__dirname, "..", "librechat_autofill.js"),
  "utf8",
);

// ---------- minimal DOM mock ----------

// Node 20+ provides Event/EventTarget/HTMLInputElement-shape via undici but
// not the full DOM. Build a duck-typed substitute that's enough for the
// helpers under test — they only inspect attributes/types and dispatch
// generic Event instances.

function makeNode(tag, attrs = {}) {
  const ev = new EventTarget();
  const node = {
    tagName: tag.toUpperCase(),
    nodeType: 1,
    children: [],
    parent: null,
    textContent: "",
    name: attrs.name || "",
    id: attrs.id || "",
    type: attrs.type || "",
    placeholder: attrs.placeholder || "",
    value: attrs.value || "",
    _attrs: { ...attrs },
    closest(selector) {
      // Very dumb closest — walks up looking for tagName matches in the selector.
      let cur = node;
      while (cur) {
        if (selector.includes(cur.tagName.toLowerCase())) return cur;
        cur = cur.parent;
      }
      return cur || node.parent;
    },
    getAttribute(k) {
      return node._attrs[k] != null ? String(node._attrs[k]) : null;
    },
    dispatchEvent(e) {
      return ev.dispatchEvent(e);
    },
    addEventListener: ev.addEventListener.bind(ev),
  };
  return node;
}

// Patch the prototype lookup so fillInputAsReact's `Object.getPrototypeOf(input)`
// returns an object with a `value` setter we can verify.
function makeInputWithReactSetter(attrs) {
  let internalValue = attrs.value || "";
  const proto = {};
  Object.defineProperty(proto, "value", {
    get() {
      return internalValue;
    },
    set(v) {
      internalValue = v;
    },
    configurable: true,
  });
  const inp = makeNode("input", attrs);
  // Remove the own-property `value` so the prototype setter is the only path.
  // Mirrors how React patches HTMLInputElement.prototype on real DOM nodes.
  delete inp.value;
  Object.setPrototypeOf(inp, proto);
  return inp;
}

// Stub HTMLInputElement.prototype as a last-resort lookup for the helper.
globalThis.HTMLInputElement = function () {};
globalThis.HTMLInputElement.prototype = {};

// Provide an extension-safe stub so the script's main() bail-out
// (typeof chrome === undefined) skips chrome.storage.local.get.
delete globalThis.chrome;

// ---------- load the content script ----------

// The script's IIFE runs and (since chrome is undefined) won't call main().
// It still attaches helpers to globalThis.xbrainLibreChatAutofill.
new Function(SCRIPT)();
const helpers = globalThis.xbrainLibreChatAutofill;
assert(helpers, "helpers not exposed on globalThis");

// ---------- test runner ----------

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

// ---------- 1. non-input rejection ----------

test("shouldFillField rejects non-input nodes", () => {
  const div = makeNode("div");
  const card = makeNode("dialog");
  card.textContent = "Enter API key for Claude Pro/Max";
  assert.equal(helpers.shouldFillField(div, card), false);
});

// ---------- 2. irrelevant input types ----------

test("shouldFillField rejects type=button and type=checkbox", () => {
  const card = makeNode("dialog");
  card.textContent = "Claude Pro/Max";
  const btn = makeNode("input", { type: "button" });
  const cb = makeNode("input", { type: "checkbox" });
  assert.equal(helpers.shouldFillField(btn, card), false);
  assert.equal(helpers.shouldFillField(cb, card), false);
});

// ---------- 3. anchor required ----------

test("shouldFillField rejects when surrounding card lacks Claude Pro/Max", () => {
  const card = makeNode("dialog");
  card.textContent = "API key — Anthropic"; // wrong endpoint
  const inp = makeNode("input", {
    type: "password",
    name: "apiKey",
  });
  assert.equal(helpers.shouldFillField(inp, card), false);
});

// ---------- 4. password input + matching anchor ----------

test("shouldFillField accepts type=password with Claude Pro/Max anchor", () => {
  const card = makeNode("dialog");
  card.textContent = "Set API Key for Claude Pro/Max";
  const inp = makeNode("input", { type: "password" });
  assert.equal(helpers.shouldFillField(inp, card), true);
});

// ---------- 5. text input with telltale attributes ----------

test("shouldFillField accepts type=text with api_key-like attributes", () => {
  const card = makeNode("dialog");
  card.textContent = "Claude Pro/Max — set credentials";
  const inp = makeNode("input", {
    type: "text",
    "aria-label": "API Key",
    placeholder: "Paste bridge token here",
  });
  assert.equal(helpers.shouldFillField(inp, card), true);
});

// ---------- 6. wrong-endpoint dialog should NOT match ----------

test("shouldFillField rejects api-key input anchored to OpenAI dialog", () => {
  const card = makeNode("dialog");
  card.textContent = "Set API Key for OpenAI";
  const inp = makeNode("input", { type: "password" });
  assert.equal(helpers.shouldFillField(inp, card), false);
});

// ---------- 7. native setter is used ----------

test("fillInputAsReact uses the prototype value setter + dispatches input event", () => {
  const inp = makeInputWithReactSetter({ type: "password" });
  let inputEventFired = false;
  inp.addEventListener("input", () => {
    inputEventFired = true;
  });
  helpers.fillInputAsReact(inp, "xbt_abc123");
  assert.equal(inp.value, "xbt_abc123");
  assert.equal(inputEventFired, true);
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
