/**
 * The PWA's members, invite and add-a-team surfaces (app-site/app/panels.js),
 * and the one platform difference underneath them.
 *
 * It lives in the extension's test directory because that is where
 * run_tests.mjs walks; it reads ../../app-site/app/ and ../../packages/chat-core/.
 *
 * THE BRANCH NOBODY EXERCISES BY HAND is the reason for the behavioural half.
 * An extension can read the tab you are looking at, so "send this page to a
 * teammate" needs no typing and every manual test of that panel starts with the
 * field already filled. A web page can see nothing but itself:
 * platform_web.currentPageUrl() answers null, and the panel has to ASK for a
 * link instead. Nobody developing on the extension will ever hit that path, and
 * the two failure modes it guards against — a field that is silently empty with
 * no explanation, and a "send current page" control that sends nothing — both
 * look like working code from the outside.
 *
 * The static half guards that the two products read as ONE: same icons, same
 * ids bound to elements that exist, and the invite code still travelling in the
 * URL fragment where no server log can see it.
 *
 * SKIP = FAIL: nothing below is conditional on a file existing.
 */

import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { createPeoplePanel } from "../../packages/chat-core/people.js";
import { buildJoinLink } from "../../packages/chat-core/invite.js";
import { renderTeamStarter } from "../../packages/chat-core/teams.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, "..", "..");
const APP_DIR = join(REPO_ROOT, "app-site", "app");

const html = readFileSync(join(APP_DIR, "index.html"), "utf8");
const panelsJs = readFileSync(join(APP_DIR, "panels.js"), "utf8");
const popupHtml = readFileSync(join(__dirname, "..", "popup.html"), "utf8");
const css = readFileSync(join(APP_DIR, "app.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

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
// A DOM stub with just the surface people.js touches. Hand-rolled, like the
// render tests': jsdom is not a repo dependency, and a stub this small makes it
// obvious which DOM calls the panel is allowed to make.
// ---------------------------------------------------------------------------

class El {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.className = "";
    this.children = [];
    this.listeners = {};
    this.hidden = false;
    this.value = "";
    this.disabled = false;
    this.textContent = "";
  }
  get classList() {
    const self = this;
    return {
      add(...n) {
        const cur = self.className.split(/\s+/).filter(Boolean);
        for (const x of n) if (!cur.includes(x)) cur.push(x);
        self.className = cur.join(" ");
      },
      remove(...n) {
        self.className = self.className.split(/\s+/).filter((c) => c && !n.includes(c)).join(" ");
      },
      contains: (n) => self.className.split(/\s+/).includes(n),
    };
  }
  get firstChild() {
    return this.children[0] || null;
  }
  appendChild(node) {
    this.children.push(node);
    return node;
  }
  removeChild(node) {
    const i = this.children.indexOf(node);
    if (i !== -1) this.children.splice(i, 1);
    return node;
  }
  addEventListener(type, fn) {
    (this.listeners[type] = this.listeners[type] || []).push(fn);
  }
  click() {
    for (const fn of this.listeners.click || []) fn();
  }
  /** Depth-first search by class name — enough for these assertions. */
  find(cls) {
    for (const c of this.children) {
      if (c.className && c.className.split(/\s+/).includes(cls)) return c;
      const hit = c.find ? c.find(cls) : null;
      if (hit) return hit;
    }
    return null;
  }
  findAll(cls, out = []) {
    for (const c of this.children) {
      if (c.className && c.className.split(/\s+/).includes(cls)) out.push(c);
      if (c.findAll) c.findAll(cls, out);
    }
    return out;
  }
}

const doc = { createElement: (tag) => new El(tag) };

const MEMBERS = [
  { user_id: "u-1", source_user_id: "s-1", display_name: "Ada" },
  { user_id: "u-2", source_user_id: "s-2", display_name: "Grace" },
];

/**
 * Build a panel over a recording API.
 *
 * @param {string|null} currentPage what this surface's currentPageUrl() answers
 */
function makePanel(currentPage) {
  const calls = [];
  const els = {
    panel: new El("div"),
    list: new El("ul"),
    urlInput: new El("input"),
    status: new El("p"),
    hint: new El("p"),
    filePicker: null, // this fixture ships none; a File button must not appear
  };
  const api = {
    membersRaw: async () => {
      calls.push("members");
      return { ok: true, status: 200, json: async () => MEMBERS };
    },
    nudgeOpenRaw: async (teamId, target, url) => {
      calls.push(`nudge:${target}:${url}`);
      return { status: 202 };
    },
    uploadMediaRaw: async () => {
      calls.push("upload");
      return { status: 201, json: async () => ({ signed_url: "/v1/media/1/img" }) };
    },
  };
  const panel = createPeoplePanel({
    doc,
    api,
    apiBase: "https://api.example.test",
    platform: { currentPageUrl: async () => currentPage },
    els,
    getActiveTeamId: () => "t-1",
    getTeams: () => [{ id: "t-1", slug: "team" }],
    getTeamSubscription: () => null,
  });
  return { panel, els, calls };
}

// ---- 1. The web branch: no current page, so the panel ASKS ----------------

testAsync("on the web the URL field stays empty and the hint says to paste one", async () => {
  const { panel, els } = makePanel(null);
  await panel.open();
  assert.equal(
    els.urlInput.value,
    "",
    "a web page can see nothing but itself; inventing a URL would send the wrong thing every time",
  );
  assert.match(
    els.hint.textContent,
    /^Paste the link/,
    "an empty field with no explanation reads as a broken app rather than as the reader's turn",
  );
  assert.ok(
    !/page you are on/i.test(els.hint.textContent),
    "the web surface must not claim to know which page you are on",
  );
});

testAsync("on the web, sending with nothing typed reports it and posts NOTHING", async () => {
  const { panel, els, calls } = makePanel(null);
  await panel.open();
  const row = els.list.children[0];
  row.find("xb-btn-secondary").click();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(els.status.textContent, "Enter a link to send.");
  assert.equal(els.status.className, "error");
  assert.ok(
    !calls.some((c) => c.startsWith("nudge:")),
    "a send with no link must not reach the server — a 422 blamed on the recipient is not an explanation",
  );
});

testAsync("send to everyone with nothing typed also posts nothing", async () => {
  const { panel, calls } = makePanel(null);
  await panel.open();
  await panel.sendToEveryone(null);
  assert.ok(!calls.some((c) => c.startsWith("nudge:")), "same rule for the whole team");
});

testAsync("a pasted link is what actually gets sent on the web", async () => {
  const { panel, els, calls } = makePanel(null);
  await panel.open();
  els.urlInput.value = "https://example.com/a";
  els.list.children[1].find("xb-btn-secondary").click();
  await new Promise((r) => setTimeout(r, 0));
  assert.ok(
    calls.includes("nudge:u-2:https://example.com/a"),
    `expected the pasted link to reach the second member; got ${JSON.stringify(calls)}`,
  );
  assert.match(els.status.textContent, /Sent to Grace/);
});

testAsync("a link that is not http(s) is refused before it leaves the page", async () => {
  const { panel, els, calls } = makePanel(null);
  await panel.open();
  els.urlInput.value = "javascript:alert(1)";
  els.list.children[0].find("xb-btn-secondary").click();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(els.status.textContent, "Enter a valid http or https link.");
  assert.ok(!calls.some((c) => c.startsWith("nudge:")));
});

// ---- 2. The extension branch, for contrast -------------------------------

testAsync("the extension branch pre-fills the field and says so", async () => {
  const { panel, els } = makePanel("https://example.com/page");
  await panel.open();
  assert.equal(els.urlInput.value, "https://example.com/page");
  assert.match(els.hint.textContent, /Defaults to the page you are on/);
});

testAsync("a field the person already typed into is never overwritten", async () => {
  const { panel, els } = makePanel("https://example.com/page");
  els.urlInput.value = "https://mine.example/keep";
  await panel.open();
  assert.equal(els.urlInput.value, "https://mine.example/keep");
});

// ---- 3. Every member is listed, with a presence dot ----------------------

testAsync("every member gets a row and a presence dot", async () => {
  const { panel, els } = makePanel(null);
  await panel.open();
  assert.equal(els.list.children.length, MEMBERS.length);
  const names = els.list.findAll("xb-people-name").map((n) => n.textContent);
  assert.deepEqual(names, ["Ada", "Grace"]);
  for (const dot of els.list.findAll("xb-people-dot")) {
    // No subscription in this fixture, so nobody is known to be online — and an
    // invented "offline" would be worse than an absent indicator.
    assert.equal(dot.className, "xb-people-dot");
    assert.equal(dot.title, "Not active");
  }
});

testAsync("a surface with no file input gets no File button", async () => {
  const { panel, els } = makePanel(null);
  await panel.open();
  const labels = els.list.children[0].children.map((c) => c.textContent);
  assert.ok(labels.includes("Link"), "the link push is always offered");
  assert.ok(
    !labels.includes("File"),
    "a control that opens nothing is worse than an absent one",
  );
});

// ---- 4. The invite code never reaches a server log -----------------------

test("the join link carries the code in the FRAGMENT, never the query string", () => {
  const link = buildJoinLink("https://api.example.test", "xbi_secret");
  assert.ok(link.includes("#c=xbi_secret"), `expected a #c= fragment, got ${link}`);
  assert.ok(
    !/[?&]c=/.test(link),
    "a query string is sent to the server and rides along in the Referer — the code is a bearer secret",
  );
  // The origin is derived from the api base, so a rebrand carries the link with
  // it instead of silently pointing at the old domain.
  assert.ok(link.startsWith("https://example.test/join/"), link);
});

// ---- 4b. The "+" is not a one-way door ----------------------------------
//
// The same panel serves two situations. As the NO-TEAMS screen there is nothing
// behind it, so there is nowhere to go back to. Opened by the "+", it covers a
// chat somebody was reading — and it fills #chat-empty, which sits over the
// thread, so without a way back the only escape is closing the whole surface.

/** Every button label the starter rendered, in order. */
function starterButtons(host) {
  return host.findAll("xb-starter-btn").map((b) => b.textContent);
}

test("the no-teams screen offers the two doors and no way back", () => {
  const host = new El("div");
  renderTeamStarter({ doc, hostEl: host, api: {}, autofocus: false });
  assert.deepEqual(starterButtons(host), ["Create team", "Join team"]);
  assert.equal(
    host.find("xb-starter-cancel"),
    null,
    '"Back to chat" here would lead to an empty room — there is no chat behind this screen',
  );
});

test('opened over a chat, the starter draws a way back that actually fires', () => {
  const host = new El("div");
  let dismissed = 0;
  renderTeamStarter({
    doc,
    hostEl: host,
    api: {},
    autofocus: false,
    onCancel: () => {
      dismissed += 1;
    },
  });
  const back = host.find("xb-starter-cancel");
  assert.ok(back, 'the "+" must not be a one-way door');
  assert.equal(back.textContent, "Back to chat");
  back.click();
  assert.equal(dismissed, 1);
});

test('the PWA passes that way back only for the "+", and owns the copy it restores', () => {
  assert.match(
    panelsJs,
    /wire\("btn-team-add",\s*\(\)\s*=>\s*openStarter\(true\)\)/,
    'the "+" must open the starter in its dismissible form',
  );
  const chatJs = readFileSync(join(APP_DIR, "chat.js"), "utf8");
  assert.ok(
    chatJs.includes("onStarterDismissed"),
    "chat.js must supply what to restore — the empty-thread sentence is written there",
  );
  assert.ok(
    !panelsJs.includes("No messages in this team"),
    "a second copy of that string here is a second copy to forget about",
  );
  for (const rel of [join("app-site", "app", "app.css"), join("chrome-extension", "popup.css")]) {
    const sheet = readFileSync(join(REPO_ROOT, rel), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
    assert.ok(
      /\.xb-starter-cancel\s*\{/.test(sheet),
      `${rel} has no .xb-starter-cancel rule — the control would render unstyled`,
    );
  }
});

// ---- 5. The two products ship the same icons ----------------------------

/** The `d` attributes of every path/circle inside one button's markup. */
function iconShape(source, id) {
  const btn = new RegExp(`<button[^>]*id="${id}"[\\s\\S]*?</button>`).exec(source);
  assert.ok(btn, `no #${id} button found`);
  return [...btn[0].matchAll(/<(?:path|circle)\b[^>]*>/g)]
    .map((m) => m[0].replace(/\s+/g, " "))
    .join("|");
}

test("the members and invite icons are the extension's, path for path", () => {
  // popup.html's #btn-send-link is the people icon; the PWA calls it #btn-people
  // because the id is per-surface, but the drawing must not drift.
  assert.equal(
    iconShape(html, "btn-people"),
    iconShape(popupHtml, "btn-send-link"),
    "the members icon differs between the two products",
  );
  assert.equal(
    iconShape(html, "btn-invite"),
    iconShape(popupHtml, "btn-invite"),
    "the invite icon differs between the two products",
  );
});

test('the "+" sits at the right-hand end of the rail, as in the extension', () => {
  const header = /<header[^>]*class="xb-header"[\s\S]*?<\/header>/.exec(html)[0];
  const rail = header.indexOf('id="team-rail"');
  const add = header.indexOf('id="btn-team-add"');
  const right = header.indexOf('class="xb-header-right"');
  assert.ok(add > rail, "the + must follow the rail, not precede it");
  assert.ok(add < right, "it belongs to the left group, beside the squares");
  const btn = /<button[^>]*id="btn-team-add"[\s\S]*?<\/button>/.exec(header)[0];
  assert.ok(btn.includes('class="xb-team-add"'), "same class as the extension's");
  assert.equal(btn.replace(/<[^>]*>/g, "").trim(), "+");
});

test("the header icons are given an explicit size, like every other one", () => {
  // A viewBox is not a size. This already blew the header apart once.
  for (const id of ["btn-people", "btn-invite"]) {
    const btn = new RegExp(`<button[^>]*id="${id}"[\\s\\S]*?</button>`).exec(html)[0];
    assert.ok(btn.includes('class="xb-icon-btn"'), `#${id} must be an .xb-icon-btn`);
    assert.equal(
      btn.replace(/<[^>]*>/g, "").trim(),
      "",
      `#${id} must be icon-only — text beside the glyph wraps the header`,
    );
    assert.ok(/aria-label="/.test(btn), `#${id} is icon-only and needs an accessible name`);
  }
  assert.match(
    css,
    /\.xb-icon-btn svg\s*\{[^}]*width:\s*15px/,
    "app.css must size every header SVG at 15px",
  );
});

// ---- 6. No control that would send nothing ------------------------------

test("the PWA ships no 'send current page' control", () => {
  // The honest web answer to "what page is the user on" is null, so a control
  // promising to send it could only ever send this app's own URL, or nothing.
  for (const [name, src] of [["index.html", html], ["panels.js", panelsJs]]) {
    assert.ok(
      !/current page|this page|send page/i.test(src.replace(/<!--[\s\S]*?-->/g, "").replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "")),
      `${name} offers to send the current page, which on the web is nothing`,
    );
  }
});

// ---- 7. Every id panels.js binds exists in the markup -------------------

test("every id panels.js binds is declared in index.html", () => {
  const ids = new Set(
    [
      ...panelsJs.matchAll(/\bel\(\s*["']([a-z0-9-]+)["']\s*\)/g),
      ...panelsJs.matchAll(/\bwire\(\s*["']([a-z0-9-]+)["']/g),
      ...panelsJs.matchAll(/["']([a-z-]*btn-[a-z0-9-]+)["']/g),
    ].map((m) => m[1]),
  );
  assert.ok(ids.size >= 15, `expected a substantial binding set, found ${ids.size}`);
  for (const id of ids) {
    assert.ok(
      html.includes(`id="${id}"`),
      `#${id} is bound but not declared — the binding silently does nothing`,
    );
  }
});

test("panels.js cannot raise the notification prompt (D-27-05)", () => {
  for (const api of ["requestPermission", "pushManager.subscribe"]) {
    assert.ok(!panelsJs.includes(api), `panels.js must never reach ${api}`);
  }
});

test("english-only: no accented Latin chars in panels.js", () => {
  const hits = panelsJs.match(/[À-ÿ]/g) || [];
  assert.equal(hits.length, 0, `panels.js has ${JSON.stringify([...new Set(hits)])}`);
});

// ---- Run the async probes, then report ----------------------------------

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
