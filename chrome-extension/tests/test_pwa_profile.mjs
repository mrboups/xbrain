/**
 * The account block at the top of the PWA's settings panel
 * (app-site/app/profile.js).
 *
 * It lives in the extension's test directory because that is where
 * run_tests.mjs walks; it reads ../../app-site/app/.
 *
 * THE 404 IS THE POINT OF THIS FILE. This UI ships before its endpoint does, and
 * the two halves are being written in parallel. An unguarded fetch would throw
 * inside the settings panel's boot, so a person who opened Settings to change
 * the theme would find an empty box instead — the failure would be nowhere near
 * the field that caused it, and nothing in a browser would say so. The block
 * therefore has to answer a missing API by going quietly read-only, and that is
 * asserted here rather than described in a comment.
 *
 * The module is driven against a stubbed document and a stubbed API client, so
 * the ORDER of what it does is exercised: read first, then decide whether the
 * field is live, and never accept typing it cannot save.
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

const html = readFileSync(join(APP_DIR, "index.html"), "utf8");
const appJs = readFileSync(join(APP_DIR, "app.js"), "utf8");
const profileJs = readFileSync(join(APP_DIR, "profile.js"), "utf8");
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
// A document stub with the ids profile.js binds. Hand-rolled, house style.
// ---------------------------------------------------------------------------

class El {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.className = "";
    this.children = [];
    this.listeners = {};
    this.hidden = false;
    this.value = "";
    this.placeholder = "";
    this.textContent = "";
    this.readOnly = false;
    this.disabled = false;
  }
  get firstChild() {
    return this.children[0] || null;
  }
  appendChild(n) {
    this.children.push(n);
    return n;
  }
  removeChild(n) {
    const i = this.children.indexOf(n);
    if (i !== -1) this.children.splice(i, 1);
    return n;
  }
  addEventListener(type, fn) {
    (this.listeners[type] = this.listeners[type] || []).push(fn);
  }
  async fire(type, event = {}) {
    for (const fn of this.listeners[type] || []) await fn(event);
  }
  /** blur() is what commits, so the stub routes it to the listener. */
  async blur() {
    await this.fire("blur");
  }
}

const IDS = [
  "settings-profile",
  "profile-avatar",
  "profile-name",
  "profile-bio",
  "profile-status",
];

function installDocument() {
  const nodes = Object.fromEntries(IDS.map((id) => [id, new El("div")]));
  globalThis.document = {
    getElementById: (id) => nodes[id] || null,
    createElement: (tag) => new El(tag),
  };
  return nodes;
}

/**
 * A client that answers GET with `get` and records every PATCH.
 *
 * @param {{status: number, body?: Object}} get
 * @param {{status: number, body?: Object}} [patch]
 */
function makeApi(get, patch = { status: 200, body: {} }) {
  const sent = [];
  return {
    sent,
    rawFetch: async (path, opts = {}) => {
      const method = opts.method || "GET";
      sent.push({ path, method, body: opts.body || null });
      const answer = method === "GET" ? get : patch;
      if (answer.throws) throw new Error("network down");
      return {
        ok: answer.status >= 200 && answer.status < 300,
        status: answer.status,
        json: async () => answer.body,
      };
    },
  };
}

const profile = await import(pathToFileURL(join(APP_DIR, "profile.js")).href);

const FULL = {
  preferred_name: "Ada",
  bio: "Builds the engine.",
  label: "Ada",
  avatar_url: "/v1/media/abc/img?t=tok",
};

// ---- 1. The happy path ---------------------------------------------------

testAsync("a profile fills the name, the bio and the picture", async () => {
  const nodes = installDocument();
  const api = makeApi({ status: 200, body: FULL });
  const result = await profile.mountProfile(api, "fallback@example.com");

  assert.equal(result.editable, true);
  assert.equal(nodes["settings-profile"].hidden, false);
  assert.equal(nodes["profile-name"].value, "Ada");
  assert.equal(nodes["profile-bio"].textContent, "Builds the engine.");
  assert.equal(nodes["profile-bio"].hidden, false);
  const img = nodes["profile-avatar"].children[0];
  assert.ok(img, "an avatar_url must produce an image");
  assert.ok(
    img.src.endsWith("/v1/media/abc/img?t=tok"),
    `a relative media path gets the API origin; got ${img.src}`,
  );
  assert.ok(/^https?:\/\//.test(img.src), `the src must be absolute; got ${img.src}`);
});

testAsync("an absolute avatar URL is passed through, not double-prefixed", async () => {
  const nodes = installDocument();
  const api = makeApi({
    status: 200,
    body: { ...FULL, avatar_url: "https://cdn.example.test/a.png" },
  });
  await profile.mountProfile(api, null);
  assert.equal(nodes["profile-avatar"].children[0].src, "https://cdn.example.test/a.png");
});

testAsync("no picture falls back to the square initial, not to an empty box", async () => {
  const nodes = installDocument();
  const api = makeApi({ status: 200, body: { preferred_name: "", bio: "", label: "grace" } });
  await profile.mountProfile(api, null);
  assert.equal(nodes["profile-avatar"].children.length, 0);
  assert.equal(nodes["profile-avatar"].textContent, "G");
});

testAsync("an empty bio hides its row instead of leaving a blank line", async () => {
  const nodes = installDocument();
  const api = makeApi({ status: 200, body: { preferred_name: "Ada", label: "Ada" } });
  await profile.mountProfile(api, null);
  assert.equal(nodes["profile-bio"].hidden, true);
});

testAsync("the resolved label becomes the placeholder when no name is set", async () => {
  // An empty field over "Your name" suggests they have none, when teammates
  // already see one.
  const nodes = installDocument();
  const api = makeApi({ status: 200, body: { preferred_name: null, label: "nicoboups" } });
  await profile.mountProfile(api, null);
  assert.equal(nodes["profile-name"].value, "");
  assert.equal(nodes["profile-name"].placeholder, "nicoboups");
});

testAsync("author_label and display_label are accepted as the same field", async () => {
  for (const key of ["author_label", "display_label"]) {
    const nodes = installDocument();
    const api = makeApi({ status: 200, body: { preferred_name: null, [key]: "Ada L" } });
    await profile.mountProfile(api, null);
    assert.equal(nodes["profile-name"].placeholder, "Ada L", `${key} was ignored`);
  }
});

// ---- 2. THE 404 PATH -----------------------------------------------------

testAsync("a 404 degrades to read-only and takes nothing else down", async () => {
  const nodes = installDocument();
  const api = makeApi({ status: 404 });
  const result = await profile.mountProfile(api, "nico@example.test");

  assert.equal(result.editable, false, "there is no endpoint to save to");
  assert.equal(
    nodes["settings-profile"].hidden,
    false,
    "the block still shows who is signed in — the panel around it must keep working",
  );
  assert.equal(nodes["profile-name"].disabled, true);
  assert.equal(nodes["profile-name"].readOnly, true);
  assert.equal(
    nodes["profile-status"].hidden,
    true,
    "and says nothing: our own rollout order is not the reader's problem",
  );
  assert.equal(
    nodes["profile-name"].placeholder,
    "nico@example.test",
    "it falls back to the identity the shell already has, rather than sitting empty",
  );
  assert.equal(nodes["profile-bio"].hidden, true);
});

testAsync("a read-only field is wired to nothing — typing cannot fire a save", async () => {
  const nodes = installDocument();
  const api = makeApi({ status: 404 });
  await profile.mountProfile(api, "nico@example.test");
  nodes["profile-name"].value = "typed anyway";
  await nodes["profile-name"].blur();
  const patches = api.sent.filter((c) => c.method === "PATCH");
  assert.deepEqual(patches, [], "a field that cannot save must not pretend to");
});

testAsync("a 500 and a network failure degrade the same quiet way", async () => {
  for (const answer of [{ status: 500 }, { status: 0, throws: true }]) {
    const nodes = installDocument();
    const result = await profile.mountProfile(makeApi(answer), "nico@example.test");
    assert.equal(result.editable, false);
    assert.equal(nodes["profile-name"].disabled, true);
    assert.equal(nodes["profile-status"].hidden, true);
  }
});

testAsync("mountProfile never throws, whatever the endpoint does", async () => {
  // The settings panel calls this on open. A throw here would take the theme
  // switch and the notification state with it.
  for (const answer of [{ status: 404 }, { status: 500 }, { status: 0, throws: true }]) {
    installDocument();
    await profile.mountProfile(makeApi(answer), null);
  }
  // A body that is not JSON at all.
  installDocument();
  const api = makeApi({ status: 200, body: undefined });
  await profile.mountProfile(api, null);
});

// ---- 3. Saving -----------------------------------------------------------

testAsync("blurring the field PATCHes preferred_name, and only that", async () => {
  const nodes = installDocument();
  const api = makeApi({ status: 200, body: FULL }, { status: 200, body: { label: "Ada B" } });
  await profile.mountProfile(api, null);
  nodes["profile-name"].value = "Ada B";
  await nodes["profile-name"].blur();

  const patches = api.sent.filter((c) => c.method === "PATCH");
  assert.equal(patches.length, 1);
  assert.equal(patches[0].path, "/v1/me/profile");
  assert.deepEqual(
    Object.keys(patches[0].body),
    ["preferred_name"],
    "the name is the only writable field; sending a bio the server may not accept yet answers typing with a 422",
  );
  assert.equal(patches[0].body.preferred_name, "Ada B");
  assert.equal(nodes["profile-status"].textContent, "Saved");
});

testAsync("an unchanged name sends nothing", async () => {
  const nodes = installDocument();
  const api = makeApi({ status: 200, body: FULL });
  await profile.mountProfile(api, null);
  await nodes["profile-name"].blur();
  await nodes["profile-name"].blur();
  assert.deepEqual(api.sent.filter((c) => c.method === "PATCH"), []);
});

testAsync("clearing the name is a real edit — it restores the provider's", async () => {
  const nodes = installDocument();
  const api = makeApi({ status: 200, body: FULL }, { status: 200, body: { label: "ada" } });
  await profile.mountProfile(api, null);
  nodes["profile-name"].value = "";
  await nodes["profile-name"].blur();
  const patches = api.sent.filter((c) => c.method === "PATCH");
  assert.equal(patches.length, 1, "an empty string is how a person takes their override off");
  assert.equal(patches[0].body.preferred_name, "");
  assert.equal(nodes["profile-name"].placeholder, "ada");
});

testAsync("a rejected save says so, in words, and does not lose what they typed", async () => {
  const nodes = installDocument();
  const api = makeApi({ status: 200, body: FULL }, { status: 422 });
  await profile.mountProfile(api, null);
  nodes["profile-name"].value = "x".repeat(500);
  await nodes["profile-name"].blur();
  assert.equal(nodes["profile-status"].className, "error");
  assert.match(nodes["profile-status"].textContent, /too long|not allowed/);
  assert.equal(nodes["profile-name"].value, "x".repeat(500), "their text stays in the field");
});

testAsync("a save that 404s locks the field rather than dropping edits silently", async () => {
  const nodes = installDocument();
  const api = makeApi({ status: 200, body: FULL }, { status: 404 });
  await profile.mountProfile(api, null);
  nodes["profile-name"].value = "Ada B";
  await nodes["profile-name"].blur();
  assert.equal(nodes["profile-status"].className, "error");
  assert.equal(nodes["profile-name"].readOnly, true);
});

testAsync("Enter commits through blur, so there is one save path and not two", async () => {
  const nodes = installDocument();
  const api = makeApi({ status: 200, body: FULL }, { status: 200, body: {} });
  await profile.mountProfile(api, null);
  nodes["profile-name"].value = "Ada B";
  let prevented = false;
  await nodes["profile-name"].fire("keydown", {
    key: "Enter",
    preventDefault: () => {
      prevented = true;
    },
  });
  assert.ok(prevented, "Enter must not submit anything or insert a newline");
  assert.equal(api.sent.filter((c) => c.method === "PATCH").length, 1);
});

testAsync("hideProfile takes the block away at sign-out", async () => {
  const nodes = installDocument();
  await profile.mountProfile(makeApi({ status: 200, body: FULL }), null);
  assert.equal(nodes["settings-profile"].hidden, false);
  profile.hideProfile();
  assert.equal(
    nodes["settings-profile"].hidden,
    true,
    "whoever signs in next on this device must not find the last person's name",
  );
});

// ---- 4. Where it sits, and what it is made of ---------------------------

test("the profile block is the FIRST thing in the settings panel", () => {
  const HTML = html.replace(/<!--[\s\S]*?-->/g, "");
  const panel = /<div[^>]*id="settings-panel"[\s\S]*?\n    <\/div>/.exec(HTML);
  assert.ok(panel, "index.html must declare #settings-panel");
  const body = panel[0];
  const profileAt = body.indexOf('id="settings-profile"');
  assert.ok(profileAt !== -1, "the profile block belongs inside the panel");
  for (const [id, what] of [
    ["btn-theme-light", "the theme switch"],
    ["settings-push-state", "the notification state"],
    ["btn-sign-out", "sign out"],
  ]) {
    const at = body.indexOf(`id="${id}"`);
    assert.ok(at !== -1, `#${id} must still be in the panel`);
    assert.ok(
      profileAt < at,
      `the profile block must come before ${what} — everything else here is a preference, this is the account they belong to`,
    );
  }
  for (const id of ["profile-avatar", "profile-name", "profile-bio"]) {
    assert.ok(body.includes(`id="${id}"`), `#${id} belongs in the profile block`);
  }
});

test("the name is an input and the bio is not — one field is editable", () => {
  const nameEl = /<input[^>]*id="profile-name"[^>]*>/.exec(html);
  assert.ok(nameEl, "the name must be an editable input");
  assert.ok(/maxlength="\d+"/i.test(nameEl[0]), "a length cap belongs on the field, not only on the server");
  assert.ok(/aria-label="/.test(nameEl[0]), "the field has no visible label, so it needs an accessible one");
  assert.ok(
    !/<input[^>]*id="profile-bio"/.test(html),
    "the bio is display-only for now — a PATCH the server half may not accept yet answers typing with a 422",
  );
});

test("the avatar is square, like every other avatar in the product", () => {
  const m = /\.xb-profile-avatar\s*\{([^}]*)\}/.exec(css);
  assert.ok(m, "app.css has no .xb-profile-avatar rule");
  assert.ok(!/50%/.test(m[1]), "Neutral avatars are square");
  assert.ok(m[1].includes("var(--radius)"), "radius must be token-driven (0)");
});

test("the read-only field looks settled, not broken", () => {
  // A greyed-out box invites a click that does nothing and reads as a bug.
  const m = /\.xb-profile-name:disabled\s*\{([^}]*)\}/.exec(css);
  assert.ok(m, "app.css must style the read-only state");
  assert.ok(/opacity:\s*1/.test(m[1]), `it must keep full contrast (got ${m[1].trim()})`);
});

test("the shell mounts the block and hides it on sign-out", () => {
  assert.ok(appJs.includes("mountProfile("), "app.js must mount the profile block");
  assert.ok(appJs.includes("hideProfile()"), "app.js must clear it when signing out");
  assert.ok(
    /mountProfile\([^)]*\)\s*\.catch\(/.test(appJs),
    "the mount must be caught at the call site too — the settings panel is not allowed to fail on open",
  );
});

test("profile.js builds no Authorization header and names no origin of its own", () => {
  const code = profileJs.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  assert.ok(!code.includes("Authorization"), "the shared client is the only place that does");
  assert.ok(
    !/https?:\/\//.test(code),
    "the API origin comes from auth.js; a literal here would pin the app to one deployment",
  );
  assert.ok(
    code.includes("MEMORY_API_BASE"),
    "the avatar path needs the injected origin, not a guessed one",
  );
});

test("profile.js cannot raise the notification prompt (D-27-05)", () => {
  for (const api of ["requestPermission", "pushManager.subscribe"]) {
    assert.ok(!profileJs.includes(api), `profile.js must never reach ${api}`);
  }
});

test("english-only: no accented Latin chars in profile.js", () => {
  const hits = profileJs.match(/[À-ÿ]/g) || [];
  assert.equal(hits.length, 0, `profile.js has ${JSON.stringify([...new Set(hits)])}`);
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
