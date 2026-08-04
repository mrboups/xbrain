/**
 * The team model-API-key section of app-site/account/teams/teams.js.
 *
 * It lives in the extension's test directory because that is where
 * run_tests.mjs walks; it reads ../../app-site/account/teams/.
 *
 * THE SECRET IS THE SUBJECT. Everything else on this page can be re-read from
 * the server if the UI mishandles it; a pasted API key cannot. The server takes
 * it once, encrypts it, and will never hand it back, so any copy this page
 * leaves behind — in a status line, in a URL, in a console call, in the input
 * after the request lands — is a copy that exists nowhere else and that nothing
 * will ever clean up. None of that is visible in a browser: a key sitting in a
 * detached input reads exactly like an empty one until someone opens devtools.
 * So it is asserted here.
 *
 * The other half is the 403. The server lets any member READ which providers
 * have a key and lets only a team admin WRITE one, and the page must not offer
 * a control to someone the server would refuse — a form that always fails is
 * worse than no form, because it reads as the product being broken rather than
 * as permission being withheld.
 *
 * teams.js is a MODULE now — it imports the shared provider table, validation,
 * failure sentences and requests from packages/chat-core, because the PWA's
 * settings sheet offers the same feature and two copies of that vocabulary is
 * two products that disagree about what a 403 means. So it is imported here
 * rather than run in a Function(), against stubs installed BEFORE the import.
 * Its IIFE still publishes the section's surface on globalThis.
 *
 * That the import resolves at all is part of the contract: the specifier
 * reaches ../../app/chat_core/, the PWA's generated copy, and the drift gate
 * keeps that byte-identical to packages/chat-core. A rename on either side
 * fails here before it reaches a browser, where it would be a blank page.
 *
 * SKIP = FAIL: nothing below is conditional on a file existing.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, "..", "..");
const PAGE_DIR = join(REPO_ROOT, "app-site", "account", "teams");

const teamsJs = readFileSync(join(PAGE_DIR, "teams.js"), "utf8");
const pageHtml = readFileSync(join(PAGE_DIR, "index.html"), "utf8");

// A real-shaped Anthropic key. Every assertion below hunts for this string.
const SECRET = "sk-ant-api03-" + "Zq7hT2vK9pR4mB1nX6wL8cJ0dY5sG3fA" + "-aXbY7z";

// ---------------------------------------------------------------------------
// Console recorder. Installed before teams.js loads, so anything it prints
// during a save is captured and can be searched for the secret.
// ---------------------------------------------------------------------------

const CONSOLE_SINK = [];
for (const level of ["log", "warn", "error", "info", "debug", "trace"]) {
  const original = console[level].bind(console);
  console[level] = (...args) => {
    CONSOLE_SINK.push(args.map((a) => String(a)).join(" "));
    original(...args);
  };
}

// ---------------------------------------------------------------------------
// DOM stub. Hand-rolled, house style — enough for a section built out of
// createElement/appendChild/textContent, and no more.
// ---------------------------------------------------------------------------

class El {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.className = "";
    this.id = "";
    this.type = "";
    this.value = "";
    this.placeholder = "";
    this.textContent = "";
    this.disabled = false;
    this.hidden = false;
    this.children = [];
    this.attrs = {};
    this.listeners = {};
  }
  appendChild(n) {
    this.children.push(n);
    return n;
  }
  setAttribute(k, v) {
    this.attrs[k] = String(v);
  }
  getAttribute(k) {
    return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null;
  }
  addEventListener(type, fn) {
    (this.listeners[type] = this.listeners[type] || []).push(fn);
  }
  async fire(type, event = {}) {
    for (const fn of this.listeners[type] || []) await fn(event);
  }
  /** Depth-first walk, self included. */
  *walk() {
    yield this;
    for (const c of this.children) yield* c.walk();
  }
  /**
   * Every string this subtree would put on screen — but NOT input values. What
   * an admin has typed and not yet submitted is theirs; the question these
   * assertions ask is whether the page ever renders a key it was told about.
   */
  renderedText() {
    return [...this.walk()].map((n) => `${n.textContent} ${n.placeholder}`).join("\n");
  }
  find(pred) {
    for (const n of this.walk()) if (pred(n)) return n;
    return null;
  }
}

globalThis.document = {
  createElement: (tag) => new El(tag),
  // teams.js's setStatus()/renderTeamsList() reach for these; the API-key
  // section never does. Returning null is the honest answer for a page that
  // was never rendered.
  getElementById: () => null,
  querySelector: () => null,
};

globalThis.window = {
  location: { origin: "https://grooveos.app", search: "" },
  addEventListener: () => {},
  history: { replaceState: () => {} },
};

const STORE = new Map([["xbt_token", "test-token"]]);
globalThis.localStorage = {
  getItem: (k) => (STORE.has(k) ? STORE.get(k) : null),
  setItem: (k, v) => STORE.set(k, String(v)),
  removeItem: (k) => STORE.delete(k),
};
globalThis.sessionStorage = globalThis.localStorage;

// ---------------------------------------------------------------------------
// fetch stub — records every call, replays a queued outcome.
// ---------------------------------------------------------------------------

const CALLS = [];
let nextOutcome = () => ({ status: 204 });

function respond(outcome) {
  nextOutcome = typeof outcome === "function" ? outcome : () => outcome;
}

globalThis.fetch = async (url, opts = {}) => {
  CALLS.push({ url: String(url), opts });
  const out = nextOutcome();
  if (out instanceof Error) throw out;
  const status = out.status;
  const bodyText = out.body == null ? "" : typeof out.body === "string" ? out.body : JSON.stringify(out.body);
  return {
    ok: status >= 200 && status < 300,
    status,
    async text() {
      return bodyText;
    },
    async json() {
      return bodyText ? JSON.parse(bodyText) : null;
    },
  };
};

// ---------------------------------------------------------------------------
// Load the page module. Stubs above are already installed, which is the whole
// reason this is an import and not a require: the module body runs on import.
// ---------------------------------------------------------------------------

await import(pathToFileURL(join(PAGE_DIR, "teams.js")).href);
const api = globalThis.xbrainTeamApiKeys;
assert.ok(api, "teams.js did not publish xbrainTeamApiKeys — the suite cannot drive the section");

const TEAM = { id: "11111111-2222-3333-4444-555555555555", slug: "acme", display_name: "Acme" };

/**
 * The selection the fallback-provider route reports.
 *
 * Anthropic selected, Anthropic the only thing this build streams — which is
 * what the server says today. `null` instead means "this build has no selector",
 * which several tests below drive on purpose.
 */
const SELECTION = { provider: "anthropic", supported: ["anthropic"] };

// ---------------------------------------------------------------------------
// runner
// ---------------------------------------------------------------------------

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
const pending = [];
function testAsync(name, body) {
  pending.push([name, body]);
}

/** A rendered section plus a typed-in key, ready to submit. */
function mount({
  providers = [],
  isAdmin = true,
  typed = SECRET,
  selection = SELECTION,
} = {}) {
  // Every mount starts signed in. The 401 case below is real — the save drops
  // the token when the server rejects it — and without this the tests that run
  // after it would all fail for a reason that has nothing to do with what they
  // assert.
  STORE.set("xbt_token", "test-token");
  const ui = api.buildApiKeysSection({ team: TEAM, providers, selection, isAdmin });
  if (ui.input) ui.input.value = typed;
  return ui;
}

// ---- 1. The key is write-only, in both directions --------------------------

test("the read is presence-only: the section renders no value, ever", () => {
  // What the live route returns today: provider names and nothing else.
  const ui = api.buildApiKeysSection({
    team: TEAM,
    providers: ["anthropic"],
    selection: SELECTION,
    isAdmin: true,
  });
  const shown = ui.el.renderedText();
  assert.ok(shown.includes("Set"), "a stored key must be visible as present");
  assert.ok(
    !/[•*]{3}/.test(shown) && !/•/.test(shown),
    "a masked value implies the real one could be revealed — it cannot be",
  );
  assert.ok(
    !shown.includes("sk-ant-api03"),
    "nothing key-shaped may reach the screen from a read",
  );
});

test("the read is narrowed to provider names, so a leakier route can't widen it", () => {
  // GET returns [{provider}] today. If a field ever joins it — a prefix, a
  // ciphertext, a "last four" — the page must ignore it by CONSTRUCTION rather
  // than by nobody having written the line that renders it yet. So the page
  // takes the body through the shared reader, and the reader is what is driven
  // here: hand it a leakier row and nothing but the name survives.
  const narrowed = api.readApiKeyProviders([
    { provider: "anthropic", key_prefix: "sk-ant-api03", last_four: "4f2a", key_enc: SECRET },
  ]);
  assert.deepEqual(narrowed, ["anthropic"], "the reader let a second field through");
  assert.ok(
    !JSON.stringify(narrowed).includes("4f2a"),
    "a 'last four' reached a surface — presence is all a client may know",
  );
  // ...and the page must actually use it, rather than destructuring the row
  // itself somewhere the reader cannot narrow.
  assert.match(
    teamsJs,
    /listTeamApiKeysRaw\(team\.id\)[\s\S]{0,320}?readApiKeyProviders\(rows\)/,
    "the api-keys read no longer goes through readApiKeyProviders",
  );
  assert.ok(
    !/\brows\.map\(|\br\.provider\b/.test(teamsJs),
    "the page reads the row shape itself again — that is the line that grows a field",
  );
});

test("provider names become text, never markup", () => {
  // provider is a free 64-char column. Whatever ends up in it renders as
  // characters — the section builds nodes, it does not build HTML.
  const section = teamsJs.slice(
    teamsJs.indexOf("function buildApiKeysSection"),
    teamsJs.indexOf("async function removeMember"),
  );
  assert.ok(section.length > 500, "buildApiKeysSection moved");
  assert.ok(!section.includes("innerHTML"), "the section builds markup from server strings");
});

test("an unlisted provider still gets a row", () => {
  // provider is a column, not an assumption: a key set by another surface for a
  // provider this page doesn't offer must not be invisible.
  const ui = api.buildApiKeysSection({
    team: TEAM,
    providers: ["mistral"],
    selection: SELECTION,
    isAdmin: true,
  });
  assert.ok(ui.el.renderedText().includes("mistral"), "unlisted provider has no row");
});

test("a failed read says unknown rather than claiming not-set", () => {
  const ui = api.buildApiKeysSection({
    team: TEAM,
    providers: null,
    selection: SELECTION,
    isAdmin: true,
  });
  const shown = ui.el.renderedText();
  assert.ok(shown.includes("Unknown"), "a failed read must not be reported as absence");
  assert.ok(!shown.includes("Not set"), "absence we haven't confirmed is a lie");
});

// ---- 2. The field is cleared once the save is accepted ---------------------

testAsync("a 204 clears the field", async () => {
  respond({ status: 204 });
  const ui = mount();
  await ui.button.fire("click");
  assert.equal(ui.input.value, "", "the key was left in the DOM after the server took it");
});

testAsync("a rejected save keeps the field, so a retry doesn't need a re-paste", async () => {
  respond({ status: 503, body: "upstream down" });
  const ui = mount();
  await ui.button.fire("click");
  assert.equal(ui.input.value, SECRET, "a transient failure discarded the admin's paste");
});

testAsync("Enter in the field saves, and clears it too", async () => {
  respond({ status: 204 });
  const ui = mount();
  await ui.input.fire("keydown", { key: "Enter" });
  assert.equal(CALLS.at(-1).opts.method, "PUT", "Enter did not submit");
  assert.equal(ui.input.value, "", "the keyboard path skipped the clear");
});

// ---- 3. The request shape, and where the key is allowed to appear ----------

testAsync("the save is a PUT with the server's body shape", async () => {
  respond({ status: 204 });
  const ui = mount();
  await ui.button.fire("click");
  const call = CALLS.at(-1);
  assert.equal(call.opts.method, "PUT");
  assert.equal(call.url, `https://api.grooveos.app/v1/teams/${TEAM.id}/api-keys`);
  const body = JSON.parse(call.opts.body);
  assert.deepEqual(body, { keys: [{ provider: "anthropic", api_key: SECRET }] });
});

testAsync("the key is never in the URL", async () => {
  respond({ status: 204 });
  const ui = mount();
  await ui.button.fire("click");
  for (const c of CALLS) {
    assert.ok(!c.url.includes(SECRET), `key leaked into a URL: ${c.url}`);
    assert.ok(!c.url.includes("sk-ant"), `key fragment leaked into a URL: ${c.url}`);
  }
});

testAsync("a surrounding whitespace paste is trimmed, not rejected", async () => {
  respond({ status: 204 });
  const ui = mount({ typed: `  ${SECRET}\n` });
  await ui.button.fire("click");
  const body = JSON.parse(CALLS.at(-1).opts.body);
  assert.equal(body.keys[0].api_key, SECRET, "a trailing newline from a copy broke the save");
});

testAsync("nothing about the save reaches a console call", async () => {
  CONSOLE_SINK.length = 0;
  respond({ status: 204 });
  const ui = mount();
  await ui.button.fire("click");
  const leaked = CONSOLE_SINK.filter((line) => line.includes(SECRET) || line.includes("sk-ant"));
  assert.deepEqual(leaked, [], "the key was printed");
});

testAsync("a failure doesn't reach a console call either", async () => {
  CONSOLE_SINK.length = 0;
  respond(new TypeError("Failed to fetch"));
  const ui = mount();
  await ui.button.fire("click");
  const leaked = CONSOLE_SINK.filter((line) => line.includes(SECRET) || line.includes("sk-ant"));
  assert.deepEqual(leaked, [], "the error path printed the key");
});

test("teams.js contains no console call at all", () => {
  // The cheap version of the two assertions above, and the one that survives a
  // debugging session: a console.log added 'just to see the value' is exactly
  // how a key ends up in a support screenshot.
  const hits = teamsJs.match(/console\s*\.\s*[a-z]+/g) || [];
  assert.deepEqual(hits, [], `teams.js logs: ${hits.join(", ")}`);
});

testAsync("a server error that echoes the key back cannot re-render it", async () => {
  // Nothing today echoes the input, but the page must not be the reason it
  // would matter if something started to.
  respond({ status: 422, body: JSON.stringify({ detail: [{ input: SECRET }] }) });
  const ui = mount();
  await ui.button.fire("click");
  assert.ok(
    !ui.status.textContent.includes(SECRET),
    "the server's echo of the key was rendered into the page",
  );
  assert.ok(!ui.el.renderedText().includes(SECRET), "the echo reached the section");
});

// ---- 4. Each failure gets its own answer -----------------------------------

const FAILURES = [
  {
    what: "not permitted",
    outcome: { status: 403, body: "team admin required" },
    expect: /admin/i,
  },
  {
    what: "server can't encrypt",
    outcome: { status: 500, body: "FERNET_KEY not configured" },
    expect: /encryption key/i,
  },
  {
    what: "server rejected the body",
    outcome: { status: 422, body: "unprocessable" },
    expect: /rejected/i,
  },
  {
    what: "team is gone",
    outcome: { status: 404, body: "team not found" },
    expect: /no longer exists/i,
  },
  {
    what: "network is down",
    outcome: new TypeError("Failed to fetch"),
    expect: /couldn't reach the server/i,
  },
  {
    what: "session expired",
    outcome: { status: 401, body: "" },
    expect: /session expired/i,
  },
];

for (const f of FAILURES) {
  testAsync(`${f.what} renders its own message`, async () => {
    respond(f.outcome);
    const ui = mount();
    await ui.button.fire("click");
    assert.match(ui.status.textContent, f.expect);
    assert.ok(
      !/something went wrong/i.test(ui.status.textContent),
      "a generic message is not a message",
    );
    assert.ok(ui.status.className.includes("is-error"), "a failure must read as one");
  });
}

test("no two failures share a message", () => {
  const messages = [
    api.describeApiKeyFailure(Object.assign(new Error("x"), { status: 403 })),
    api.describeApiKeyFailure(Object.assign(new Error("x"), { status: 404 })),
    api.describeApiKeyFailure(Object.assign(new Error("x"), { status: 422 })),
    api.describeApiKeyFailure(Object.assign(new Error("x"), { status: 500 })),
    api.describeApiKeyFailure(Object.assign(new Error("x"), { status: 502 })),
    api.describeApiKeyFailure(new TypeError("Failed to fetch")),
    api.describeApiKeyFailure({ status: 401 }),
  ];
  assert.equal(new Set(messages).size, messages.length, `collided: ${messages.join(" | ")}`);
  for (const m of messages) {
    assert.ok(m.length > 20, `too terse to act on: ${m}`);
  }
});

test("the failure text never carries the server's words", () => {
  // The server's body is the one string on this path that could contain
  // anything at all, including whatever was posted to it.
  const err = Object.assign(new Error(`HTTP 400: ${SECRET}`), {
    status: 400,
    body: SECRET,
  });
  const msg = api.describeApiKeyFailure(err);
  assert.ok(!msg.includes(SECRET), "the error body was interpolated into the message");
});

testAsync("a save that lands says so, and says what the key is for", async () => {
  respond({ status: 204 });
  const ui = mount();
  await ui.button.fire("click");
  assert.ok(ui.status.className.includes("is-success"), "success must read as success");
  assert.match(ui.status.textContent, /saved/i);
  assert.match(ui.status.textContent, /subscription/i, "the success line should say when it is spent");
});

testAsync("a stored key flips the row to Set without a reload", async () => {
  respond({ status: 204 });
  const ui = mount({ providers: [] });
  assert.ok(ui.el.renderedText().includes("Not set"));
  await ui.button.fire("click");
  assert.ok(ui.el.renderedText().includes("Set"), "the row still claims no key is stored");
  assert.equal(ui.button.textContent, "Replace key", "the button still offers a first save");
});

// ---- 5. Format checks, before a round-trip is spent ------------------------

test("an empty field is its own message, not a request", () => {
  const v = api.validateApiKey("anthropic", "   ");
  assert.equal(v.ok, false);
  assert.match(v.message, /paste a key/i);
});

test("the wrong provider's key is named as such", () => {
  const v = api.validateApiKey("anthropic", "xai-abcdefghijklmnopqrstuvwxyz");
  assert.equal(v.ok, false);
  assert.match(v.message, /sk-ant-/, "the message must say what was expected");
  assert.match(v.message, /Anthropic/);
});

test("a truncated key is named as truncated, not as wrong", () => {
  const v = api.validateApiKey("anthropic", "sk-ant-abc");
  assert.equal(v.ok, false);
  assert.match(v.message, /truncated|short/i);
});

test("a key with a line break in it is named as such", () => {
  const v = api.validateApiKey("anthropic", "sk-ant-api03-aaaa\nbbbbcccccddddd");
  assert.equal(v.ok, false);
  assert.match(v.message, /space|line break/i);
});

test("every offered provider accepts its own key shape", () => {
  const samples = {
    anthropic: "sk-ant-api03-" + "a".repeat(40),
    openai: "sk-proj-" + "a".repeat(40),
    xai: "xai-" + "a".repeat(40),
  };
  for (const p of api.API_KEY_PROVIDERS) {
    const sample = samples[p.id];
    assert.ok(sample, `no sample for provider ${p.id} — the table grew, this test didn't`);
    const v = api.validateApiKey(p.id, sample);
    assert.equal(v.ok, true, `${p.id} rejected its own key: ${v.message}`);
  }
});

test("a provider the page doesn't list is still savable", () => {
  const v = api.validateApiKey("mistral", "some-long-enough-key-value");
  assert.equal(v.ok, true, "an unlisted provider must not be unsavable");
});

testAsync("a malformed paste never reaches the network", async () => {
  const before = CALLS.length;
  const ui = mount({ typed: "definitely-not-a-key" });
  await ui.button.fire("click");
  assert.equal(CALLS.length, before, "a client-side rejection still spent a round-trip");
  assert.ok(ui.status.className.includes("is-error"));
});

// ---- 6. Permission: the control matches what the server would allow --------

test("a member gets the status but no form", () => {
  const ui = api.buildApiKeysSection({
    team: TEAM,
    providers: ["anthropic"],
    selection: SELECTION,
    isAdmin: false,
  });
  assert.equal(ui.input, null, "a non-admin was given a field the server would 403");
  assert.equal(ui.button, null, "a non-admin was given a save button");
  assert.equal(ui.select, null, "a non-admin was given a provider picker");
  const shown = ui.el.renderedText();
  assert.ok(shown.includes("Set"), "a member may still see that a key exists");
  assert.match(shown, /only a team admin/i, "and must be told why there's no form");
});

test("a member's section has no input element anywhere in it", () => {
  const ui = api.buildApiKeysSection({
    team: TEAM,
    providers: [],
    selection: SELECTION,
    isAdmin: false,
  });
  const inputish = ui.el.find((n) => n.tagName === "INPUT" || n.tagName === "SELECT");
  assert.equal(inputish, null, `a non-admin section contains a ${inputish && inputish.tagName}`);
});

test("the page reads admin from the membership row the server checks", () => {
  // Server-side, PUT requires membership.role === "admin" on this team — not
  // the global env-admin list. fillTeamBody derives isAdmin from exactly that
  // row, so the two agree by construction.
  assert.match(
    teamsJs,
    /const isAdmin = myRole === "admin";/,
    "the admin fact moved — re-check it still matches the server's rule",
  );
  assert.match(
    teamsJs,
    /buildApiKeysSection\(\{[\s\S]{0,240}?providers: await keysPromise,[\s\S]{0,160}?\bisAdmin,\s*\}\)/,
    "the section is no longer passed the membership-derived admin flag",
  );
  // ...and the selection it renders comes from the server too, never from a
  // client-side default. A team on OpenAI must not be shown "Anthropic"
  // because a read failed.
  assert.match(
    teamsJs,
    /selection: await selectionPromise,/,
    "the section no longer receives the server's fallback selection",
  );
});

// ---- 7. Reachable, labelled, and visibly focused --------------------------

test("both controls carry a real label bound to their id", () => {
  const ui = mount();
  const labels = [...ui.el.walk()].filter((n) => n.tagName === "LABEL");
  const forAttrs = labels.map((l) => l.getAttribute("for"));
  assert.ok(forAttrs.includes(ui.input.id), "the key field has no label pointing at it");
  assert.ok(forAttrs.includes(ui.select.id), "the provider picker has no label pointing at it");
  for (const l of labels) assert.ok(l.textContent.trim().length > 0, "an empty label is no label");
});

test("ids are per-team, so two open cards can't collide", () => {
  const a = api.buildApiKeysSection({
    team: TEAM,
    providers: [],
    selection: SELECTION,
    isAdmin: true,
  });
  const b = api.buildApiKeysSection({
    team: { ...TEAM, id: "99999999-8888-7777-6666-555555555555" },
    providers: [],
    selection: SELECTION,
    isAdmin: true,
  });
  assert.notEqual(a.input.id, b.input.id, "two team cards would share one input id");
  assert.notEqual(a.select.id, b.select.id, "two team cards would share one select id");
});

test("the field is a password field that no autofill will touch", () => {
  const ui = mount();
  assert.equal(ui.input.type, "password", "a secret in a plain text input is shoulder-readable");
  assert.equal(ui.input.getAttribute("autocomplete"), "off");
  assert.equal(ui.input.getAttribute("spellcheck"), "false");
});

test("the save button is a button, not a div someone can't tab to", () => {
  const ui = mount();
  assert.equal(ui.button.tagName, "BUTTON");
  assert.equal(ui.button.type, "button", "a bare button in a form would submit the page");
});

test("the status line announces itself to a screen reader", () => {
  const ui = mount();
  assert.equal(ui.status.getAttribute("aria-live"), "polite");
});

test("focus is visible on every control the section adds", () => {
  const css = pageHtml.replace(/\/\*[\s\S]*?\*\//g, "");
  for (const sel of [
    '.apikey-form input[type="password"]:focus-visible',
    ".apikey-form select:focus-visible",
    ".apikey-form .btn:focus-visible",
  ]) {
    assert.ok(css.includes(sel), `no focus rule for ${sel} — keyboard users lose their place`);
  }
  assert.match(css, /\.apikey-form[^}]*focus-visible[\s\S]{0,160}outline:\s*2px/, "the focus ring isn't an outline");
});

test("the section blends into the page it was added to", () => {
  const css = pageHtml.replace(/\/\*[\s\S]*?\*\//g, "");
  // Same tokens as the invite form directly above it. A hard-coded colour here
  // is what stops a future light theme from reaching this section.
  const block = css.slice(css.indexOf(".apikey-section"), css.indexOf(".action-status {"));
  assert.ok(block.length > 200, "the api-key CSS block moved or vanished");
  const literals = block.match(/#[0-9a-fA-F]{3,8}\b|rgba?\(/g) || [];
  assert.deepEqual(literals, [], `hard-coded colours won't follow the theme: ${literals.join(", ")}`);
});

// ---- 8. The copy says what it costs ---------------------------------------

test("the cost is stated once, plainly, before anyone pastes anything", () => {
  const ui = mount();
  const note = ui.el.find((n) => n.className === "apikey-note");
  assert.ok(note, "the section has no cost line");
  assert.match(note.textContent, /billed to your team/i, "who pays isn't stated");
  assert.match(note.textContent, /subscription/i, "that it's a fallback isn't stated");
  // WHO CAN SPEND IT. resolve_fallback_key takes team_id and no user, so a
  // stored key is drawn on by every member's messages, including somebody who
  // joins tomorrow and never sees this screen. A sentence that says only
  // "billed to your team" reads as an accounting note rather than as a shared
  // budget, and the person pasting the key is the one who needs the difference.
  // "member ... spend", in that order, in one clause: an alternation over three
  // hopeful phrasings would pass on a sentence that names members and never
  // says what they can do with the money.
  assert.match(
    note.textContent,
    /member[^.]*spend/i,
    "the cost line does not say that any member's message can spend it",
  );
  const sentences = note.textContent.split(/\.\s|\.$/).filter((s) => s.trim());
  assert.equal(sentences.length, 1, `one sentence, not ${sentences.length}: ${note.textContent}`);
});

// ---- 9. Storing a key is not selecting a provider --------------------------
//
// A team may hold three keys and spend exactly one. Every assertion here exists
// because an interface that blurs the two produces somebody who buys a key,
// pastes it, reads "saved", and waits for an answer it was never going to give.

test("the provider the agent uses is shown separately from what is stored", () => {
  const ui = mount({ providers: ["anthropic"] });
  assert.ok(ui.useSelect, "an admin has no control over which provider is spent");
  assert.equal(ui.useSelect.value, "anthropic", "the server's selection was not applied");
  const shown = ui.el.renderedText();
  assert.match(shown, /used by the agent/i, "the selection is unlabelled");
  assert.match(
    shown,
    /storing a key does not switch/i,
    "nothing says that pasting a key is not what activates it",
  );
});

test("the selection is described as the fallback, not as what always answers", () => {
  const shown = mount().el.renderedText();
  assert.match(shown, /only chooses the fallback/i, "the selection reads as a global model switch");
  assert.match(shown, /subscription/i, "the free path is not named beside the choice");
});

test("a selection with no key stored says so at selection time", () => {
  // The server answers unavailability NAMING that provider rather than quietly
  // using another, so the dead end is silent unless it is said here.
  const ui = mount({
    providers: [],
    selection: { provider: "anthropic", supported: ["anthropic"] },
  });
  assert.ok(ui.useWarning, "there is nowhere to say it");
  assert.equal(ui.useWarning.hidden, false, "a selected provider with no key was not flagged");
  assert.match(ui.useWarning.textContent, /no key is stored/i);
  assert.match(ui.useWarning.textContent, /unavailable/i, "what the agent will do is not stated");
});

test("...and a selection that IS backed by a key says nothing", () => {
  const ui = mount({ providers: ["anthropic"] });
  assert.equal(ui.useWarning.hidden, true, `warned anyway: ${ui.useWarning.textContent}`);
});

testAsync("changing the selection writes it, and nothing else", async () => {
  respond({ status: 204 });
  const ui = mount({ providers: ["anthropic", "openai"] });
  const before = CALLS.length;
  ui.useSelect.value = "openai";
  await ui.useSelect.fire("change");
  assert.equal(CALLS.length, before + 1, "the selection did not reach the server exactly once");
  const call = CALLS.at(-1);
  assert.equal(call.opts.method, "PUT");
  assert.equal(
    call.url,
    `https://api.grooveos.app/v1/teams/${TEAM.id}/fallback-provider`,
    "the selection went to the key route",
  );
  assert.deepEqual(JSON.parse(call.opts.body), { provider: "openai" });
  assert.ok(!call.opts.body.includes("api_key"), "a selection must not carry a key");
});

testAsync("a build with no selector says so instead of offering a dead control", async () => {
  // GET /fallback-provider 404s until the server half ships. A settings screen
  // must degrade to the truth, not to a select that fails on change.
  const ui = mount({ providers: ["anthropic"], selection: null });
  assert.equal(ui.useSelect, null, "a control was offered for a route that does not exist");
  const shown = ui.el.renderedText();
  assert.match(shown, /not available in this build/i);
  assert.match(shown, /Anthropic/, "what the agent actually falls back to is not named");
});

test("a member sees which provider is spent but cannot change it", () => {
  const ui = api.buildApiKeysSection({
    team: TEAM,
    providers: ["anthropic"],
    selection: { provider: "openai", supported: ["anthropic", "openai"] },
    isAdmin: false,
  });
  assert.equal(ui.useSelect, null, "a non-admin was given a control the server would 403");
  assert.match(ui.el.renderedText(), /OpenAI/, "a member cannot see what the team is spending");
  const inputish = ui.el.find((n) => n.tagName === "SELECT" || n.tagName === "INPUT");
  assert.equal(inputish, null, `a non-admin section contains a ${inputish && inputish.tagName}`);
});

test("the member note does not read as being locked out of the agent", () => {
  const shown = api.buildApiKeysSection({
    team: TEAM,
    providers: ["anthropic"],
    selection: SELECTION,
    isAdmin: false,
  }).el.renderedText();
  // The fallback resolves per team, so a member already spends the stored key
  // without configuring anything. What they cannot do is change it.
  assert.match(shown, /only a team admin/i, "who can act is not named");
  assert.match(
    shown,
    /already[^.]{0,40}the team's key/i,
    "a member is left believing the agent is an admin feature",
  );
});

// ---- 10. A key that this deployment will never spend ----------------------

test("a provider the agent cannot call is marked as such, on the row", () => {
  const ui = mount({ providers: [] });
  const marks = [...ui.el.walk()].filter((n) => n.className === "apikey-unused");
  assert.ok(marks.length >= 2, "OpenAI and xAI are offered with nothing saying they are inert");
  for (const m of marks) assert.match(m.textContent, /not called by the agent/i);
  // ...and Anthropic, which IS called, must NOT carry the mark.
  const rows = [...ui.el.walk()].filter((n) => n.className === "apikey-row");
  const anthropicRow = rows.find((r) =>
    [...r.walk()].some((n) => n.textContent === "Anthropic (Claude)"),
  );
  assert.ok(anthropicRow, "no Anthropic row");
  assert.ok(
    ![...anthropicRow.walk()].some((n) => n.className === "apikey-unused"),
    "the one provider the agent does call is marked as inert",
  );
});

test("picking one of them warns BEFORE the paste, not after the save", () => {
  const ui = mount();
  const warning = ui.el.find((n) => n.className === "apikey-warning" && n !== ui.useWarning);
  assert.ok(warning, "the key form has no warning slot");
  assert.equal(warning.hidden, true, "Anthropic is callable and must not be warned about");
  ui.select.value = "openai";
  ui.select.listeners.change.forEach((fn) => fn({}));
  assert.equal(warning.hidden, false, "choosing an uncallable provider said nothing");
  assert.match(warning.textContent, /cannot call/i);
  assert.match(warning.textContent, /never spent|not get your team an answer/i);
});

testAsync("saving a key the agent cannot call does not say it will work", async () => {
  respond({ status: 204 });
  const ui = mount({ typed: "sk-" + "b".repeat(48) });
  ui.select.value = "openai";
  await ui.button.fire("click");
  assert.match(ui.status.textContent, /saved/i, "the save still happened and must be reported");
  assert.match(
    ui.status.textContent,
    /cannot call/i,
    "a green line for a key that will never answer is how somebody pays for silence",
  );
});

testAsync("saving a key for a provider that is NOT selected says it is not spent", async () => {
  respond({ status: 204 });
  const ui = mount({
    providers: ["openai"],
    selection: { provider: "openai", supported: ["anthropic", "openai"] },
  });
  ui.select.value = "anthropic";
  await ui.button.fire("click");
  assert.match(ui.status.textContent, /saved/i);
  assert.match(
    ui.status.textContent,
    /not spent until|is set to use/i,
    "a stored-but-unselected key was reported as if it were now the fallback",
  );
});

test("the server's own supported list beats the table when it disagrees", () => {
  // The day the agent streams all three, the server says so and no client-side
  // table gets to keep claiming otherwise.
  const ui = mount({
    providers: [],
    selection: { provider: "openai", supported: ["anthropic", "openai", "xai"] },
  });
  const marks = [...ui.el.walk()].filter((n) => n.className === "apikey-unused");
  assert.deepEqual(marks, [], "a build that streams all three still marks two as inert");
});

test("the section is English, like the rest of the product", () => {
  const ui = mount();
  const text = ui.el.renderedText();
  assert.ok(
    !/\b(clé|équipe|enregistrer|remplacer|Sauvegarder)\b/i.test(text),
    "French leaked into the UI — CLAUDE.md says the product ships in English",
  );
});

// ---------------------------------------------------------------------------

for (const [name, body] of pending) {
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

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
