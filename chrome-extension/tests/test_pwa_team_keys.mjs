/**
 * The team API key inside the PWA's settings sheet (app-site/app/team_keys.js).
 *
 * It lives in the extension's test directory because that is where
 * run_tests.mjs walks; it reads ../../app-site/app/ and ../../packages/chat-core/.
 *
 * WHY THIS SECTION EXISTS, AND THEREFORE WHAT MUST BE TRUE OF IT. The agent
 * falls back to a team key when no browser is sharing the Claude subscription,
 * and the notice that says so used to name a remedy set only on a desktop admin
 * page — unreachable from a standalone app with no address bar, in exactly the
 * situation the notice appears in. So the last assertions here are about the
 * ROUTE: a section nobody can reach is the dead end this closes.
 *
 * THE SECRET IS THE SUBJECT of the first half. Everything else on this screen
 * can be re-read from the server if the UI mishandles it; a pasted API key
 * cannot. The server takes it once, encrypts it, and never hands it back, so
 * any copy this file leaves behind — in a status line, a URL, a console call,
 * or the input after the request lands — exists nowhere else and nothing will
 * ever clean it up. None of that is visible in a browser: a key sitting in a
 * detached input reads exactly like an empty one.
 *
 * And the 403 is the other half. The server lets any member READ which
 * providers have a key and lets only a team admin WRITE, so a member must get
 * the ask-an-admin sentence rather than a form that always fails — which reads
 * as the product being broken instead of as permission being withheld.
 *
 * SKIP = FAIL: nothing below is conditional on a file existing.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, "..", "..");
const APP_DIR = join(REPO_ROOT, "app-site", "app");

const teamKeysJs = readFileSync(join(APP_DIR, "team_keys.js"), "utf8");
const appJs = readFileSync(join(APP_DIR, "app.js"), "utf8");
const chatJs = readFileSync(join(APP_DIR, "chat.js"), "utf8");
const html = readFileSync(join(APP_DIR, "index.html"), "utf8");
const css = readFileSync(join(APP_DIR, "app.css"), "utf8").replace(
  /\/\*[\s\S]*?\*\//g,
  "",
);

/** A real-shaped Anthropic key. Every assertion below hunts for this string. */
const SECRET = "sk-ant-api03-" + "Wm4kQ8vB2nT6xR9pL3cH5jD7sY1gF0aZ" + "-qNbV3x";

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

// ---------------------------------------------------------------------------
// Console recorder, installed BEFORE the module loads.
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
// getElementById/createElement/textContent, and no more.
// ---------------------------------------------------------------------------

class El {
  constructor(tag, id = "") {
    this.tagName = String(tag).toUpperCase();
    this.id = id;
    this.className = "";
    this.type = "";
    this.value = "";
    this.placeholder = "";
    this.textContent = "";
    this.disabled = false;
    this.hidden = false;
    this.focused = 0;
    this.scrolled = 0;
    this.children = [];
    this.attrs = {};
    this.listeners = {};
    this.classList = {
      add: (c) => {
        this.className = `${this.className} ${c}`.trim();
      },
    };
  }
  appendChild(n) {
    this.children.push(n);
    n.parent = this;
    return n;
  }
  removeChild(n) {
    this.children = this.children.filter((c) => c !== n);
    return n;
  }
  get firstChild() {
    return this.children[0] || null;
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
  focus() {
    this.focused++;
  }
  scrollIntoView() {
    this.scrolled++;
  }
  *walk() {
    yield this;
    for (const c of this.children) yield* c.walk();
  }
  /** Every string this subtree would put on screen. NOT input values. */
  renderedText() {
    return [...this.walk()].map((n) => `${n.textContent} ${n.placeholder}`).join("\n");
  }
}

/**
 * The ids the module actually reads, taken from its own source.
 *
 * Derived rather than listed, so a NEW binding is covered on the day it is
 * written. It also means the stub can never quietly drift from the markup — the
 * assertion below checks the same set against index.html.
 */
const BOUND_IDS = [
  ...new Set([...teamKeysJs.matchAll(/\bel\(\s*"([a-z0-9-]+)"\s*\)/g)].map((m) => m[1])),
];

const NODES = new Map();
function node(id) {
  if (!NODES.has(id)) NODES.set(id, new El(id.startsWith("btn-") ? "button" : "div", id));
  return NODES.get(id);
}

/**
 * Fresh elements for every mount, so no test inherits another's DOM.
 *
 * Everything the section binds is hung UNDER #settings-team-key, mirroring the
 * markup closely enough for renderedText() to walk the whole block: an
 * assertion about what somebody sees has to see every line of it, not the one
 * element a test happened to name.
 */
function resetDom() {
  NODES.clear();
  const root = node("settings-team-key");
  for (const id of BOUND_IDS) {
    if (id === "settings-team-key") continue;
    node(id);
  }
  // The real controls, so their tag names and types are right.
  NODES.set("team-key-input", new El("input", "team-key-input"));
  NODES.get("team-key-input").type = "password";
  NODES.set("team-key-provider", new El("select", "team-key-provider"));
  NODES.set("team-key-use", new El("select", "team-key-use"));
  NODES.set("btn-team-key-save", new El("button", "btn-team-key-save"));
  for (const [id, n] of NODES) {
    if (id !== "settings-team-key") root.appendChild(n);
  }
}

globalThis.document = {
  getElementById: (id) => (NODES.has(id) ? NODES.get(id) : null),
  createElement: (tag) => new El(tag),
};

// ---------------------------------------------------------------------------
// A stubbed chat-core client. The REAL api.js is not used here on purpose: what
// is under test is what this surface does with the answers, and the request
// shapes are asserted in the extension's own api contract.
// ---------------------------------------------------------------------------

const CALLS = [];
let handlers = {};

function res(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() {
      return body;
    },
    async text() {
      return body == null ? "" : JSON.stringify(body);
    },
  };
}

const api = {
  membersRaw: async (teamId) => {
    CALLS.push({ what: "members", teamId });
    return handlers.members ? handlers.members() : res(200, []);
  },
  listTeamApiKeysRaw: async (teamId) => {
    CALLS.push({ what: "list-keys", teamId });
    return handlers.list ? handlers.list() : res(200, []);
  },
  putTeamApiKeysRaw: async (teamId, keys) => {
    CALLS.push({ what: "put-keys", teamId, keys });
    return handlers.put ? handlers.put() : res(204, null);
  },
  teamFallbackProviderRaw: async (teamId) => {
    CALLS.push({ what: "get-selection", teamId });
    return handlers.selection ? handlers.selection() : res(404, null);
  },
  putTeamFallbackProviderRaw: async (teamId, body) => {
    CALLS.push({ what: "put-selection", teamId, body });
    return handlers.putSelection ? handlers.putSelection() : res(204, null);
  },
};

const TEAM_ID = "11111111-2222-3333-4444-555555555555";
const ME = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";

const mod = await import(pathToFileURL(join(APP_DIR, "team_keys.js")).href);

/**
 * Mount, refresh, and hand back the live nodes.
 *
 * @param {{role?: string|null, stored?: string[]|null, selection?: Object|null,
 *          teamId?: string|null}} [opts]
 *   role — "admin" / "member", or null to make the members read FAIL, which is
 *   a third state and not the same as "member".
 */
async function mount(opts = {}) {
  const {
    role = "admin",
    stored = [],
    selection = null,
    teamId = TEAM_ID,
  } = opts;
  resetDom();
  CALLS.length = 0;
  handlers = {
    members: () => (role === null ? res(500, null) : res(200, [{ user_id: ME, role }])),
    list: () => (stored === null ? res(403, null) : res(200, stored.map((p) => ({ provider: p })))),
    selection: () => (selection === null ? res(404, null) : res(200, selection)),
  };
  const ui = mod.mountTeamKeys(api, {
    getTeamId: () => teamId,
    getSelfUserId: () => ME,
  });
  await ui.refresh();
  return ui;
}

const section = () => NODES.get("settings-team-key");
const shown = () => section().renderedText();

// ---------------------------------------------------------------------------
// 1. The key never becomes something anybody can read back
// ---------------------------------------------------------------------------

testAsync("a stored key is presence only — no value, and no mask either", async () => {
  await mount({ stored: ["anthropic"] });
  const text = shown();
  assert.ok(text.includes("Set"), "a stored key must be visible as present");
  assert.ok(!/•/.test(text) && !/[*]{3}/.test(text), "a masked value implies one could be revealed");
  // The field's placeholder IS "sk-ant-...", which is a format hint and not a
  // value; what may never appear is key material. `sk-ant-api03` is the
  // account segment of a real key and is absent from any hint.
  assert.ok(!text.includes("sk-ant-api03"), "something key-shaped reached the screen from a read");
  assert.ok(!text.includes(SECRET), "the key itself reached the screen");
});

testAsync("a read that FAILED says Unknown, never Not set", async () => {
  await mount({ stored: null });
  assert.ok(shown().includes("Unknown"), "a failed read was reported as a confirmed absence");
  assert.ok(
    !shown().includes("Not set"),
    "claiming an absence we did not confirm invites overwriting a working key",
  );
});

testAsync("a 204 clears the field", async () => {
  await mount();
  const input = NODES.get("team-key-input");
  input.value = SECRET;
  await NODES.get("btn-team-key-save").fire("click");
  assert.equal(input.value, "", "the key was left in the DOM after the server took it");
});

testAsync("a rejected save KEEPS the field, so a retry needs no re-paste", async () => {
  await mount();
  handlers.put = () => res(503, "upstream down");
  const input = NODES.get("team-key-input");
  input.value = SECRET;
  await NODES.get("btn-team-key-save").fire("click");
  assert.equal(input.value, SECRET, "a transient failure discarded the paste");
});

testAsync("Enter in the field saves, and clears it too", async () => {
  await mount();
  const input = NODES.get("team-key-input");
  input.value = SECRET;
  await input.fire("keydown", { key: "Enter" });
  assert.ok(
    CALLS.some((c) => c.what === "put-keys"),
    "the keyboard path never reached the server",
  );
  assert.equal(input.value, "", "the keyboard path skipped the clear");
});

testAsync("the key travels in the body and reaches nothing else", async () => {
  await mount();
  NODES.get("team-key-input").value = SECRET;
  await NODES.get("btn-team-key-save").fire("click");
  const put = CALLS.find((c) => c.what === "put-keys");
  assert.deepEqual(put.keys, [{ provider: "anthropic", api_key: SECRET }]);
  // Not the path, and not a query: the team id is all that identifies the call.
  assert.ok(!String(put.teamId).includes(SECRET));
  assert.ok(!shown().includes(SECRET), "the key was rendered somewhere in the section");
  assert.ok(
    !NODES.get("team-key-status").textContent.includes(SECRET),
    "the key reached the status line",
  );
});

testAsync("nothing about a save — landed or failed — reaches a console call", async () => {
  CONSOLE_SINK.length = 0;
  await mount();
  NODES.get("team-key-input").value = SECRET;
  await NODES.get("btn-team-key-save").fire("click");
  handlers.put = () => {
    throw new TypeError("Failed to fetch");
  };
  NODES.get("team-key-input").value = SECRET;
  await NODES.get("btn-team-key-save").fire("click");
  const leaked = CONSOLE_SINK.filter((l) => l.includes(SECRET) || l.includes("sk-ant"));
  assert.deepEqual(leaked, [], "the key was printed");
});

test("team_keys.js contains no console call at all", () => {
  // The cheap version of the assertion above, and the one that survives a
  // debugging session: a console.log added 'just to see the value' is exactly
  // how a key ends up in a support screenshot.
  const hits = teamKeysJs.match(/console\s*\.\s*[a-z]+/g) || [];
  assert.deepEqual(hits, [], `team_keys.js logs: ${hits.join(", ")}`);
});

testAsync("a server error that echoes the key back cannot re-render it", async () => {
  await mount();
  handlers.put = () => res(422, { detail: [{ input: SECRET }] });
  NODES.get("team-key-input").value = SECRET;
  await NODES.get("btn-team-key-save").fire("click");
  assert.ok(!shown().includes(SECRET), "the server's echo of the key was rendered");
  assert.ok(!NODES.get("team-key-status").textContent.includes(SECRET));
});

testAsync("a malformed paste never reaches the network", async () => {
  await mount();
  NODES.get("team-key-input").value = "definitely-not-a-key";
  await NODES.get("btn-team-key-save").fire("click");
  assert.ok(
    !CALLS.some((c) => c.what === "put-keys"),
    "a client-side rejection still spent a round-trip",
  );
  assert.equal(NODES.get("team-key-status").className, "error");
});

testAsync("a paste REFUSED before it is sent is not echoed back onto the screen", async () => {
  // The nastiest version of the leak: a real key, refused here for the wrong
  // provider, printed into the status line as "we could not accept X". Nothing
  // left the device, and the key is now rendered on it.
  await mount();
  NODES.get("team-key-provider").value = "xai";
  NODES.get("team-key-input").value = SECRET;
  await NODES.get("btn-team-key-save").fire("click");
  const line = NODES.get("team-key-status");
  assert.equal(line.className, "error", "a mismatched key was accepted");
  assert.ok(!line.textContent.includes(SECRET), "the refusal printed the key");
  assert.ok(!shown().includes(SECRET), "the refusal reached the section some other way");
  assert.match(line.textContent, /xAI|xai-/, "the message does not say what was expected");
});

test("the field is a password field that no autofill or autocorrect will touch", () => {
  const field = /<input[^>]*id="team-key-input"[^>]*>/.exec(html);
  assert.ok(field, "index.html declares no #team-key-input");
  assert.match(field[0], /type="password"/, "a secret in a plain input is shoulder-readable");
  assert.match(field[0], /autocomplete="off"/);
  assert.match(field[0], /spellcheck="false"/);
  assert.match(field[0], /autocapitalize="off"/, "a phone would capitalise the first character");
  assert.match(field[0], /autocorrect="off"/, "a phone would 'fix' a key it does not recognise");
});

test("signing out takes the section AND anything typed into it away", () => {
  const body = /export function hideTeamKeys\(\)[\s\S]*?\n\}/.exec(teamKeysJs);
  assert.ok(body, "team_keys.js exports no hideTeamKeys");
  assert.match(body[0], /input\.value = ""/, "an unsent key survives a sign-out");
  assert.match(appJs, /hideTeamKeys\(\)/, "the shell never calls it");
});

// ---------------------------------------------------------------------------
// 2. The control matches what the server would allow
// ---------------------------------------------------------------------------

testAsync("a member gets the ask-an-admin sentence, not a form", async () => {
  await mount({ role: "member", stored: ["anthropic"] });
  assert.equal(NODES.get("team-key-form").hidden, true, "a non-admin got a form the server would 403");
  assert.equal(NODES.get("team-key-member-note").hidden, false, "and was told nothing instead");
  const text = NODES.get("team-key-member-note").textContent;
  assert.match(text, /only a team admin/i, "who CAN act is not named");
  // ...and it must not read as being locked out of the agent: the fallback
  // resolves per team, so a member already spends whatever is stored.
  assert.match(text, /already[^.]{0,40}the team's key/i, "a member is left thinking they get nothing");
  // A member may still see WHAT is stored — that is a read the server allows.
  assert.ok(shown().includes("Set"), "a member cannot see that the team has a key at all");
});

testAsync("a members read that FAILED is not treated as 'member'", async () => {
  // Rendering the member sentence over a failed read would tell an admin they
  // are not one, and they would go looking for somebody to ask.
  await mount({ role: null });
  assert.equal(NODES.get("team-key-form").hidden, true, "a form was drawn on a permission we never read");
  assert.match(
    NODES.get("team-key-member-note").textContent,
    /couldn't check/i,
    "an unread permission was reported as a denied one",
  );
});

testAsync("the admin fact comes from the membership row the server checks", async () => {
  await mount({ role: "admin" });
  assert.ok(CALLS.some((c) => c.what === "members"), "the section never read the membership row");
  assert.equal(NODES.get("team-key-form").hidden, false, "an admin got no form");
});

testAsync("with no team there is no section at all", async () => {
  const ui = await mount({ teamId: null });
  assert.equal(section().hidden, true, "a block about a team you are not in can only confuse");
  assert.deepEqual(CALLS, [], "it read the server for a team that does not exist");
  // focus() must be a no-op rather than throwing at whoever routed here.
  ui.focus();
});

// ---------------------------------------------------------------------------
// 3. Each failure gets its own answer
// ---------------------------------------------------------------------------

const FAILURES = [
  { what: "not permitted", outcome: () => res(403, "team admin required"), expect: /admin/i },
  { what: "server cannot encrypt", outcome: () => res(500, "no FERNET_KEY"), expect: /encryption key/i },
  { what: "server rejected the body", outcome: () => res(422, "unprocessable"), expect: /rejected/i },
  { what: "team is gone", outcome: () => res(404, "team not found"), expect: /no longer exists/i },
  { what: "session expired", outcome: () => res(401, ""), expect: /session expired/i },
  {
    what: "network is down",
    outcome: () => {
      throw new TypeError("Failed to fetch");
    },
    expect: /couldn't reach the server/i,
  },
];

for (const f of FAILURES) {
  testAsync(`${f.what} renders its own text`, async () => {
    await mount();
    handlers.put = f.outcome;
    NODES.get("team-key-input").value = SECRET;
    await NODES.get("btn-team-key-save").fire("click");
    const line = NODES.get("team-key-status");
    assert.match(line.textContent, f.expect);
    assert.ok(!/something went wrong/i.test(line.textContent), "a generic message is not a message");
    assert.equal(line.className, "error", "a failure must read as one");
    assert.equal(line.hidden, false, "the failure was written into a hidden element");
  });
}

testAsync("no two failures leave the same sentence on screen", async () => {
  const seen = [];
  for (const f of FAILURES) {
    await mount();
    handlers.put = f.outcome;
    NODES.get("team-key-input").value = SECRET;
    await NODES.get("btn-team-key-save").fire("click");
    seen.push(NODES.get("team-key-status").textContent);
  }
  assert.equal(new Set(seen).size, seen.length, `collided: ${seen.join(" | ")}`);
});

testAsync("a save that lands says so, and says when the key is spent", async () => {
  await mount();
  NODES.get("team-key-input").value = SECRET;
  await NODES.get("btn-team-key-save").fire("click");
  const line = NODES.get("team-key-status");
  assert.equal(line.className, "success");
  assert.match(line.textContent, /saved/i);
  assert.match(line.textContent, /subscription/i, "when it gets spent is not stated");
});

testAsync("a save does not silently move the picker off what was chosen", async () => {
  // paint() runs again after a save. Resetting the picker there would leave
  // "OpenAI key saved" on screen above a form pointing at Anthropic, which is
  // how the next paste lands under the wrong provider.
  await mount();
  const picker = NODES.get("team-key-provider");
  picker.value = "openai";
  NODES.get("team-key-input").value = "sk-" + "c".repeat(48);
  await NODES.get("btn-team-key-save").fire("click");
  assert.match(NODES.get("team-key-status").textContent, /OpenAI/, "the save named another provider");
  assert.equal(picker.value, "openai", "the picker jumped back under the person's hands");
});

testAsync("a saved key flips its row to Set without a reload", async () => {
  await mount({ stored: [] });
  assert.ok(shown().includes("Not set"));
  NODES.get("team-key-input").value = SECRET;
  await NODES.get("btn-team-key-save").fire("click");
  assert.ok(shown().includes("Set"), "the row still claims no key is stored");
});

// ---------------------------------------------------------------------------
// 4. What it costs, and who spends it
// ---------------------------------------------------------------------------

testAsync("the cost is stated before anybody pastes anything", async () => {
  await mount();
  const note = NODES.get("team-key-note").textContent;
  assert.match(note, /billed to your team/i, "who pays is not stated");
  assert.match(note, /subscription/i, "that it is a fallback is not stated");
  // The fallback resolves on team_id with no user, so a stored key is drawn on
  // by every member's messages — including somebody who joins tomorrow and
  // never opens this screen. That is a shared budget, not an accounting note.
  assert.match(note, /member[^.]*spend/i, "it does not say that any member's message spends it");
});

testAsync("storing a key is not presented as activating it", async () => {
  await mount({ selection: { provider: "anthropic", supported: ["anthropic"] } });
  assert.match(shown(), /storing a key does not switch/i, "the two actions read as one");
  assert.match(shown(), /only chooses the fallback/i, "the selection reads as a global model switch");
});

testAsync("a provider the agent cannot call is marked, on its row", async () => {
  await mount();
  const marks = [...section().walk()].filter((n) => n.className === "xb-teamkey-unused");
  assert.ok(marks.length >= 2, "OpenAI and xAI are listed with nothing saying they are inert");
  for (const m of marks) assert.match(m.textContent, /not called by the agent/i);
});

testAsync("a build with no selector says what the agent falls back to", async () => {
  await mount({ selection: null });
  assert.equal(NODES.get("team-key-use").hidden, true, "a control was offered for a route that 404s");
  assert.match(shown(), /not available in this build/i);
  assert.match(shown(), /Anthropic/, "what actually gets called is not named");
});

testAsync("selecting a provider with no key stored says so at selection time", async () => {
  await mount({
    stored: ["anthropic"],
    selection: { provider: "anthropic", supported: ["anthropic", "openai"] },
  });
  const warn = NODES.get("team-key-use-warning");
  assert.equal(warn.hidden, true, "a backed selection was warned about anyway");
  const use = NODES.get("team-key-use");
  use.value = "openai";
  await use.fire("change");
  assert.equal(warn.hidden, false, "picking a provider with no key said nothing");
  assert.match(warn.textContent, /no key is stored/i);
  assert.match(warn.textContent, /unavailable/i, "what the agent will do instead is not stated");
});

testAsync("changing the selection writes the selection, and carries no key", async () => {
  await mount({
    stored: ["anthropic", "openai"],
    selection: { provider: "anthropic", supported: ["anthropic", "openai"] },
  });
  const use = NODES.get("team-key-use");
  use.value = "openai";
  await use.fire("change");
  const put = CALLS.find((c) => c.what === "put-selection");
  assert.ok(put, "the selection never reached the server");
  assert.deepEqual(put.body, { provider: "openai" });
  assert.ok(!JSON.stringify(put.body).includes("api_key"), "a selection carried a key");
});

// ---------------------------------------------------------------------------
// 5. The route: the notice must reach this section
// ---------------------------------------------------------------------------

test("the notice carries a control, and it leads to the settings sheet", () => {
  assert.match(
    chatJs,
    /SUBSCRIPTION_NOTICE_ACTION/,
    "the notice names the remedy with no control to reach it — the dead end this closes",
  );
  const render = /function renderSubscriptionNotice\([\s\S]*?\n\}/.exec(chatJs);
  assert.ok(render, "chat.js no longer builds the notice");
  assert.match(render[0], /onOpenTeamKeys\(\)/, "the control leads nowhere");
  assert.match(
    render[0],
    /addEventListener\("click"[\s\S]{0,60}?onOpenTeamKeys/,
    "the route must be a click, not something that fires on its own",
  );
});

test("...and the shell answers that hook with the team-key section", () => {
  assert.match(
    appJs,
    /onOpenTeamKeys: openTeamKeys/,
    "bootChat is never handed a destination, so the notice's control does nothing",
  );
  const open = /function openTeamKeys\(\)[\s\S]*?\n\}/.exec(appJs);
  assert.ok(open, "app.js declares no openTeamKeys");
  assert.match(open[0], /settingsSheet\.open\(\)/, "it does not open the sheet");
  assert.match(open[0], /teamKeys\.focus\(\)/, "it does not put the caret on the section");
});

test("nothing opens the sheet by itself", () => {
  // A bridge drops whenever a laptop sleeps. A sheet appearing over a
  // half-typed message, repeatedly, is how people learn to dismiss things
  // without reading them — and a member would get a form they cannot submit.
  const calls = [...appJs.matchAll(/openTeamKeys\(\)/g)].length;
  assert.equal(calls, 1, "openTeamKeys is called somewhere other than its own declaration");
  assert.ok(
    !/setTimeout[\s\S]{0,80}openTeamKeys/.test(appJs),
    "the section opens on a timer",
  );
  assert.ok(
    !/openTeamKeys/.test(chatJs),
    "chat.js reaches the sheet directly instead of through the hook it was given",
  );
});

test("the focus lands on the heading, never on the key field", () => {
  // Focusing a text input raises the on-screen keyboard before anybody asked to
  // type — the same reason the settings sheet takes its close button rather
  // than the name field — and a member has no field to focus at all.
  const focus = /function focus\(\)[\s\S]*?\n  \}/.exec(teamKeysJs);
  assert.ok(focus, "team_keys.js has no focus()");
  assert.match(focus[0], /heading\.focus\(\)/);
  assert.ok(!/input\.focus\(\)/.test(teamKeysJs), "the key field is focused somewhere");
  assert.match(
    html,
    /id="team-key-heading"[^>]*tabindex="-1"/,
    "the heading cannot take focus, so the route lands nowhere",
  );
});

testAsync("opening the section twice on one tap costs one round of reads", async () => {
  // The sheet refreshes on open AND the notice's route wants the fresh answer
  // before it focuses. Without collapsing, one press is six requests racing to
  // paint.
  await mount();
  CALLS.length = 0;
  const ui = mod.mountTeamKeys(api, { getTeamId: () => TEAM_ID, getSelfUserId: () => ME });
  await Promise.all([ui.refresh(), ui.refresh()]);
  assert.equal(CALLS.length, 3, `expected one read of each, got ${CALLS.length}`);
});

// ---------------------------------------------------------------------------
// 6. It lives inside the sheet's model, not beside it
// ---------------------------------------------------------------------------

test("every id team_keys.js binds is declared in index.html", () => {
  assert.ok(BOUND_IDS.length >= 10, `expected the section to bind several ids, found ${BOUND_IDS.length}`);
  for (const id of BOUND_IDS) {
    assert.ok(
      html.includes(`id="${id}"`),
      `team_keys.js reads #${id}, which index.html does not declare — the binding silently does nothing`,
    );
  }
});

test("the section sits inside the sheet's single scroller", () => {
  // The sheet is sized off the MEASURED viewport so the keyboard cannot push
  // its header off the top. A block with its own scroller — or fixed position —
  // reintroduces that bug one surface further in, and pasting a key is exactly
  // when the keyboard is up.
  const body = html.indexOf('class="xb-settings-body"');
  const at = html.indexOf('id="settings-team-key"');
  assert.ok(body !== -1 && at > body, "the section is not inside .xb-settings-body");
  const block = css.slice(css.indexOf(".xb-teamkey {"), css.indexOf(".xb-settings-action"));
  assert.ok(block.length > 400, "the team-key CSS block moved or vanished");
  assert.ok(!/overflow(-y)?:\s*(auto|scroll)/.test(block), "a second scroller inside the sheet");
  assert.ok(!/position:\s*fixed/.test(block), "fixed positioning ignores the measured viewport");
});

test("the field and the picker cannot make iOS zoom the sheet", () => {
  // Safari zooms into any control under 16px, which shoves the sheet's own
  // header off screen — indistinguishable from the layout being broken.
  for (const sel of [".xb-teamkey-input", ".xb-teamkey-select"]) {
    const m = new RegExp(`\\${sel}\\s*\\{([^}]*)\\}`).exec(css);
    assert.ok(m, `app.css has no ${sel} rule`);
    const size = /font-size:\s*(\d+(?:\.\d+)?)px/.exec(m[1]);
    assert.ok(size, `${sel} sets no font-size`);
    assert.ok(Number(size[1]) >= 16, `${sel} is ${size[1]}px — Safari will zoom on focus`);
  }
});

test("the section follows the theme, and the product's radius", () => {
  const block = css.slice(css.indexOf(".xb-teamkey {"), css.indexOf(".xb-settings-action"));
  const literals = block.match(/#[0-9a-fA-F]{3,8}\b|rgba?\(/g) || [];
  assert.deepEqual(literals, [], `hard-coded colours won't follow the theme: ${literals.join(", ")}`);
  assert.ok(!/border-radius:\s*[1-9]/.test(block), "Neutral at radius 0 — no rounded corners");
});

test("the status line announces itself to a screen reader", () => {
  const line = /<p[^>]*id="team-key-status"[^>]*>/.exec(html);
  assert.ok(line, "index.html declares no #team-key-status");
  assert.match(line[0], /aria-live="polite"/);
  assert.match(line[0], /\bhidden\b/, "it must start empty rather than reserving a blank row");
});

test("both controls carry a real label bound to their id", () => {
  for (const id of ["team-key-input", "team-key-provider", "team-key-use"]) {
    assert.ok(
      new RegExp(`<label[^>]*for="${id}"`).test(html),
      `#${id} has no label pointing at it`,
    );
  }
});

test("the save control is a real button, not a div nobody can tab to", () => {
  const btn = /<button[^>]*id="btn-team-key-save"[^>]*>/.exec(html);
  assert.ok(btn, "#btn-team-key-save is not a <button>");
  assert.match(btn[0], /type="button"/, "a bare button would submit whatever form it lands in");
});

test("the section reimplements nothing chat-core already defines (D-27-04)", () => {
  for (const name of [
    "validateApiKey",
    "describeApiKeyFailure",
    "readApiKeyProviders",
    "providerLabel",
    "teamKeySavedMessage",
  ]) {
    assert.ok(
      !new RegExp(`function\\s+${name}\\s*\\(`).test(teamKeysJs),
      `team_keys.js declares ${name}, which packages/chat-core already declares`,
    );
    assert.ok(
      teamKeysJs.includes(name),
      `team_keys.js does not use ${name} — the shared definition is not actually consumed`,
    );
  }
  assert.match(
    teamKeysJs,
    /from "\.\/chat_core\/team_api_keys\.js"/,
    "the section does not import the shared vocabulary at all",
  );
});

test("english-only, like the rest of the product", () => {
  const hits = teamKeysJs.match(/[À-ÿ]/g) || [];
  assert.deepEqual([...new Set(hits)], [], "product strings must be English");
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
