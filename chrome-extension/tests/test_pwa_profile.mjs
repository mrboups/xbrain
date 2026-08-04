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
  /** What the two picture controls do to the file input: nothing but count. */
  click() {
    this.clicks = (this.clicks || 0) + 1;
  }
}

const IDS = [
  "settings-profile",
  "profile-avatar",
  "profile-name",
  "profile-bio",
  "profile-status",
  "profile-avatar-input",
  "btn-profile-avatar",
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
  assert.equal(nodes["profile-bio"].value, "Builds the engine.");
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

testAsync("an EMPTY bio still shows its field — that is how anyone finds it", async () => {
  // The regression this replaces: the row hid itself when there was nothing in
  // it. Defensible for a value you can only read, wrong for one you can write —
  // somebody who has never set a bio saw no field at all and had no way to
  // learn there was one.
  const nodes = installDocument();
  const api = makeApi({ status: 200, body: { preferred_name: "Ada", label: "Ada" } });
  await profile.mountProfile(api, null);
  assert.equal(nodes["profile-bio"].hidden, false);
  assert.equal(nodes["profile-bio"].value, "");
  assert.equal(nodes["profile-bio"].disabled, false, "and it accepts typing");
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
  assert.equal(
    nodes["profile-bio"].disabled,
    true,
    "read-only means the bio too — a field that takes typing it cannot save is worse than one that refuses it",
  );
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
    "one key per request: the route leaves an omitted field alone, so a body that also carried the bio could blank it with a stale copy",
  );
  assert.equal(patches[0].body.preferred_name, "Ada B");
  assert.equal(nodes["profile-status"].textContent, "Saved");
});

testAsync("blurring the BIO PATCHes bio, and only bio", async () => {
  const nodes = installDocument();
  const api = makeApi({ status: 200, body: FULL }, { status: 200, body: FULL });
  await profile.mountProfile(api, null);
  nodes["profile-bio"].value = "Writes the compiler.";
  await nodes["profile-bio"].blur();

  const patches = api.sent.filter((c) => c.method === "PATCH");
  assert.equal(patches.length, 1, "a bio-only edit is a bio-only request");
  assert.deepEqual(Object.keys(patches[0].body), ["bio"], "the name must not ride along");
  assert.equal(patches[0].body.bio, "Writes the compiler.");
  assert.equal(nodes["profile-status"].textContent, "Saved");
});

testAsync("clearing a bio sends an empty string, not an absent key", async () => {
  // "" is how the route clears a column; omitting the key means "leave it
  // alone". Getting this wrong is a bio nobody can remove — only replace.
  const nodes = installDocument();
  const api = makeApi({ status: 200, body: FULL }, { status: 200, body: {} });
  await profile.mountProfile(api, null);
  nodes["profile-bio"].value = "";
  await nodes["profile-bio"].blur();

  const patches = api.sent.filter((c) => c.method === "PATCH");
  assert.equal(patches.length, 1);
  assert.ok("bio" in patches[0].body, "the key must be present, or nothing is cleared");
  assert.equal(patches[0].body.bio, "");
});

testAsync("an unchanged bio sends nothing at all", async () => {
  const nodes = installDocument();
  const api = makeApi({ status: 200, body: FULL });
  await profile.mountProfile(api, null);
  await nodes["profile-bio"].blur();
  assert.deepEqual(api.sent.filter((c) => c.method === "PATCH"), []);
});

testAsync("a refused bio says which field, in words about that field", async () => {
  const nodes = installDocument();
  const api = makeApi({ status: 200, body: FULL }, { status: 422 });
  await profile.mountProfile(api, null);
  nodes["profile-bio"].value = "x".repeat(401);
  await nodes["profile-bio"].blur();
  assert.equal(nodes["profile-status"].className, "error");
  assert.match(
    nodes["profile-status"].textContent,
    /bio/i,
    "a message about 'your name' after editing a bio sends somebody to the wrong field",
  );
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

// ---- 3b. THE PICTURE -----------------------------------------------------
//
// An avatar is a MEDIA ITEM: it goes up through the same multipart call the
// composer's "+" uses, and the profile is then pointed at the id that came
// back. Two requests, and the second one is the fragile half — it carries
// X-Team-Scope, and the route resolves that header through the membership check
// the chat uses. Send the wrong slug, or none, and the picture silently does
// not change, which is the worst outcome this flow has.

/** A file, as much of one as this module ever touches. */
function makeFile(over = {}) {
  return { name: "me.png", type: "image/png", size: 64 * 1024, ...over };
}

const AVATAR_ITEM = { item_id: "11111111-2222-3333-4444-555555555555", mime: "image/png" };

/**
 * A client that records every call and answers each leg independently.
 *
 * @param {{
 *   profiles?: Array<{status: number, body?: Object}>,
 *   upload?: {status: number, body?: Object, throws?: boolean},
 *   attach?: {status: number, body?: Object, throws?: boolean}
 * }} opts
 *   profiles — answers for successive GETs. The last one repeats, so a test
 *     that does not care about the re-read passes exactly one.
 */
function makeMediaApi(opts = {}) {
  const answers = opts.profiles || [{ status: 200, body: FULL }];
  const queue = [...answers];
  const respond = (answer) => {
    if (answer.throws) throw new Error("network down");
    return {
      ok: answer.status >= 200 && answer.status < 300,
      status: answer.status,
      json: async () => answer.body,
    };
  };
  const api = {
    calls: [],
    uploads: [],
    rawFetch: async (path, o = {}) => {
      const method = o.method || "GET";
      api.calls.push({
        path,
        method,
        body: o.body || null,
        headers: o.headers || null,
      });
      if (method === "GET") return respond(queue.length > 1 ? queue.shift() : queue[0]);
      if (method === "PUT") return respond(opts.attach || { status: 200, body: {} });
      return respond({ status: 200, body: {} });
    },
    uploadMediaRaw: async (slug, file, fields) => {
      api.uploads.push({ slug, file, fields });
      return respond(opts.upload || { status: 201, body: AVATAR_ITEM });
    },
  };
  return api;
}

const TEAM = "aibrussels";

/** Mount with a live team, then hand over the picker to drive. */
async function mountWithTeam(api, slug = TEAM) {
  const nodes = installDocument();
  const result = await profile.mountProfile(api, null, { getTeamSlug: () => slug });
  return { nodes, result };
}

/** Choose a file, the way the OS picker reports it. */
async function choose(nodes, file) {
  await nodes["profile-avatar-input"].fire("change", {
    target: { files: [file], value: "C:\\fake\\me.png" },
  });
}

testAsync("a picture goes up as media, then the profile is pointed at it", async () => {
  const api = makeMediaApi({
    profiles: [
      { status: 200, body: FULL },
      { status: 200, body: { ...FULL, avatar_url: "/v1/media/new/img?t=fresh" } },
    ],
  });
  const { nodes } = await mountWithTeam(api);
  await choose(nodes, makeFile());

  assert.equal(api.uploads.length, 1, "exactly one upload, through the shared multipart call site");
  assert.equal(
    api.uploads[0].slug,
    TEAM,
    "the media path is team-scoped by SLUG — an id uploads into a scope that does not exist",
  );

  const put = api.calls.filter((c) => c.method === "PUT");
  assert.equal(put.length, 1);
  assert.equal(put[0].path, "/v1/me/profile/avatar");
  assert.deepEqual(
    Object.keys(put[0].body),
    ["media_item_id"],
    "the route forbids extra fields outright",
  );
  assert.equal(put[0].body.media_item_id, AVATAR_ITEM.item_id, "the id the upload returned");
  assert.equal(
    put[0].headers && put[0].headers["X-Team-Scope"],
    TEAM,
    "X-Team-Scope is not optional: the route resolves it through the membership check, and the item is looked up INSIDE that scope",
  );
});

testAsync("the new picture is READ back, not assumed", async () => {
  const api = makeMediaApi({
    profiles: [
      { status: 200, body: FULL },
      { status: 200, body: { ...FULL, avatar_url: "/v1/media/new/img?t=fresh" } },
    ],
  });
  const { nodes } = await mountWithTeam(api);
  await choose(nodes, makeFile());

  const gets = api.calls.filter((c) => c.method === "GET");
  assert.equal(gets.length, 2, "one at mount, one after the change");
  assert.ok(
    api.calls.indexOf(gets[1]) > api.calls.findIndex((c) => c.method === "PUT"),
    "the re-read must come AFTER the PUT, or it paints the old picture",
  );
  const img = nodes["profile-avatar"].children[0];
  assert.ok(img, "the square must hold an image now");
  assert.ok(
    img.src.endsWith("/v1/media/new/img?t=fresh"),
    `the freshly minted URL must be the one painted; got ${img.src}`,
  );
  assert.equal(nodes["profile-status"].className, "success");
});

testAsync("a changed picture does not overwrite a name being typed", async () => {
  const api = makeMediaApi({
    profiles: [
      { status: 200, body: FULL },
      { status: 200, body: { ...FULL, preferred_name: "Ada", avatar_url: "/v1/media/n/img" } },
    ],
  });
  const { nodes } = await mountWithTeam(api);
  nodes["profile-name"].value = "Ada Lovelace";
  await choose(nodes, makeFile());
  assert.equal(
    nodes["profile-name"].value,
    "Ada Lovelace",
    "repainting the whole block would throw away what they had typed but not yet saved",
  );
});

testAsync("a file that is not an image never leaves the device", async () => {
  const api = makeMediaApi();
  const { nodes } = await mountWithTeam(api);
  await choose(nodes, makeFile({ name: "contract.pdf", type: "application/pdf" }));
  assert.deepEqual(api.uploads, [], "no upload at all");
  assert.equal(nodes["profile-status"].className, "error");
  assert.match(nodes["profile-status"].textContent, /image/i);
});

testAsync("an oversize picture is refused BEFORE the upload, not after a 413", async () => {
  const api = makeMediaApi();
  const { nodes } = await mountWithTeam(api);
  await choose(nodes, makeFile({ size: 40 * 1024 * 1024 }));
  assert.deepEqual(
    api.uploads,
    [],
    "a phone camera file would spend a minute uploading to earn a 413 nobody surfaces",
  );
  assert.equal(nodes["profile-status"].className, "error");
  assert.match(nodes["profile-status"].textContent, /too big|MB/);
});

testAsync("with no team there is nowhere to put it, and it says so", async () => {
  const api = makeMediaApi();
  const { nodes } = await mountWithTeam(api, null);
  await choose(nodes, makeFile());
  assert.deepEqual(api.uploads, []);
  assert.equal(api.calls.filter((c) => c.method === "PUT").length, 0);
  assert.equal(nodes["profile-status"].className, "error");
  assert.match(nodes["profile-status"].textContent, /team/i);
});

testAsync("a failed upload says so, and points the profile at nothing", async () => {
  const api = makeMediaApi({ upload: { status: 413 } });
  const { nodes } = await mountWithTeam(api);
  await choose(nodes, makeFile());
  assert.equal(
    api.calls.filter((c) => c.method === "PUT").length,
    0,
    "there is no id to attach — a PUT here would 404 and confuse the message",
  );
  assert.equal(nodes["profile-status"].className, "error");
  assert.match(nodes["profile-status"].textContent, /too big|MB/);
});

testAsync("a rejected attach says so, and the picture on screen does not change", async () => {
  const api = makeMediaApi({ attach: { status: 403 } });
  const { nodes } = await mountWithTeam(api);
  const before = nodes["profile-avatar"].children[0].src;
  await choose(nodes, makeFile());
  assert.equal(nodes["profile-status"].className, "error");
  assert.match(nodes["profile-status"].textContent, /not allowed/i);
  assert.equal(
    nodes["profile-avatar"].children[0].src,
    before,
    "a picture that appears to change and did not is worse than one that plainly failed",
  );
});

testAsync("a 404 attach reads as 'not right now', not as a bad photo", async () => {
  // The endpoint may not be deployed, or the item may belong to another team —
  // the route answers 404 for both on purpose. Neither is fixed by picking a
  // different picture, so the message must not suggest it.
  const api = makeMediaApi({ attach: { status: 404 } });
  const { nodes } = await mountWithTeam(api);
  await choose(nodes, makeFile());
  assert.equal(nodes["profile-status"].className, "error");
  assert.match(nodes["profile-status"].textContent, /right now/i);
});

testAsync("a network failure mid-flight is reported, not swallowed", async () => {
  const api = makeMediaApi({ upload: { status: 0, throws: true } });
  const { nodes } = await mountWithTeam(api);
  await choose(nodes, makeFile());
  assert.equal(nodes["profile-status"].className, "error");
  assert.match(nodes["profile-status"].textContent, /Network/i);
  // And the controls come back: a failure that leaves them disabled is a
  // one-shot feature.
  assert.equal(nodes["btn-profile-avatar"].disabled, false);
});

testAsync("the change landed but the re-read did not: it must not read as failure", async () => {
  const api = makeMediaApi({
    profiles: [{ status: 200, body: FULL }, { status: 500 }],
  });
  const { nodes } = await mountWithTeam(api);
  await choose(nodes, makeFile());
  assert.equal(
    nodes["profile-status"].className,
    "success",
    "the PUT was accepted — telling them it failed makes them upload it again",
  );
  assert.match(nodes["profile-status"].textContent, /saved/i);
});

testAsync("the picture opens the picker — it IS the control now", async () => {
  const api = makeMediaApi();
  const { nodes, result } = await mountWithTeam(api);
  assert.equal(result.photoEditable, true);
  await nodes["btn-profile-avatar"].fire("click");
  assert.equal(nodes["profile-avatar-input"].clicks, 1, "tapping the picture must open the picker");
});

testAsync("mounting twice does not upload the picture twice", async () => {
  // startChat runs mountProfile again after a re-sign-in, on the SAME elements.
  // A second change listener would send the file twice and PUT it twice, and
  // nothing on screen would say why.
  const api = makeMediaApi();
  const nodes = installDocument();
  await profile.mountProfile(api, null, { getTeamSlug: () => TEAM });
  await profile.mountProfile(api, null, { getTeamSlug: () => TEAM });
  await choose(nodes, makeFile());
  assert.equal(api.uploads.length, 1, "one file chosen, one upload");
  assert.equal(api.calls.filter((c) => c.method === "PUT").length, 1);
});

testAsync("no endpoint means no picture controls either", async () => {
  const api = makeMediaApi({ profiles: [{ status: 404 }] });
  const { nodes, result } = await mountWithTeam(api);
  assert.equal(result.photoEditable, false);
  assert.equal(nodes["btn-profile-avatar"].disabled, true, "a control that can only fail is worse than an absent one");
  await choose(nodes, makeFile());
  assert.deepEqual(api.uploads, [], "and it is wired to nothing, not merely greyed out");
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

test("both the name and the bio are editable fields, and both are capped", () => {
  const nameEl = /<input[^>]*id="profile-name"[^>]*>/.exec(html);
  assert.ok(nameEl, "the name must be an editable input");
  assert.ok(/maxlength="\d+"/i.test(nameEl[0]), "a length cap belongs on the field, not only on the server");
  assert.ok(/aria-label="/.test(nameEl[0]), "the field has no visible label, so it needs an accessible one");

  const bioEl = /<textarea[^>]*id="profile-bio"[^>]*>/.exec(html);
  assert.ok(bioEl, "the bio must be a textarea — the server keeps newlines, so it is prose");
  assert.equal(
    /maxlength="(\d+)"/.exec(bioEl[0])[1],
    "400",
    "the cap must match the server's MAX_BIO_LENGTH, or a paragraph is refused after it is written rather than stopped while it is typed",
  );
  assert.ok(/placeholder="/.test(bioEl[0]), "an empty field with no placeholder is a field nobody knows is one");
  assert.ok(/aria-label="/.test(bioEl[0]), "it has no visible label either");
  assert.ok(!/\bhidden\b/.test(bioEl[0]), "and it is never hidden — that WAS the bug");
});

test("the picture is a real control, reachable without a mouse", () => {
  const btn = /<button[^>]*id="btn-profile-avatar"[\s\S]*?<\/button>/.exec(html);
  assert.ok(btn, "the avatar must be wrapped in a button — an image with a click handler is not reachable by keyboard and announces nothing");
  assert.match(btn[0], /aria-label="[^"]+"/, "it carries no text, so it needs an accessible name");
  assert.ok(btn[0].includes('id="profile-avatar"'), "the square itself belongs inside it");

  // The words that used to sit under the picture are gone, so everything they
  // carried has to be on the picture itself: an accessible name (above), a
  // visible focus ring, and a mark that says the thing is pressable at all.
  assert.ok(
    !/id="btn-profile-photo"/.test(html),
    "the separate photo button was removed — a second door to one picker",
  );
  assert.ok(
    btn[0].includes("xb-profile-avatar-edit"),
    "with no words beside it, the picture needs its own affordance — otherwise it is an image that is secretly clickable",
  );
  assert.match(
    css,
    /\.xb-profile-avatar-btn:focus-visible\s*\{[^}]*outline:/,
    "reachable by Tab means visible when reached",
  );
  assert.match(
    css,
    /\.xb-profile-avatar-edit svg\s*\{[^}]*width:\s*\d+px/,
    "an unsized inline SVG renders at ~300x150 and takes the sheet apart — it has happened here before",
  );

  const input = /<input[^>]*id="profile-avatar-input"[^>]*>/.exec(html);
  assert.ok(input, "index.html must declare the file input the buttons open");
  assert.match(input[0], /type="file"/);
  assert.match(
    input[0],
    /accept="image\/[^"]*"/,
    "the picker must offer images only — the server refuses anything else with a 422 nobody wants to explain",
  );
  assert.match(input[0], /\bhidden\b/, "the styled buttons are the control; the raw input is not");
});

test("the picture controls sit inside the account block, not somewhere else", () => {
  const block = /<div[^>]*id="settings-profile"[\s\S]*?\n        <\/div>/.exec(
    html.replace(/<!--[\s\S]*?-->/g, ""),
  );
  assert.ok(block, "index.html must declare #settings-profile");
  for (const id of ["btn-profile-avatar", "profile-avatar-input"]) {
    assert.ok(block[0].includes(`id="${id}"`), `#${id} belongs in the account block`);
  }
});

test("the avatar URL is read, painted and forgotten", () => {
  const code = profileJs.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  for (const sink of ["localStorage", "sessionStorage", "storage.set", "indexedDB"]) {
    assert.ok(
      !code.includes(sink),
      `profile.js writes the profile to ${sink} — the signature in an avatar URL expires, so a stored copy is a picture that works right up until it silently stops`,
    );
  }
  assert.ok(
    code.includes("readProfile("),
    "the fresh URL must come from a read, not from a value kept between calls",
  );
});

test("the upload goes through the shared multipart call site, not a new one", () => {
  const code = profileJs.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  assert.ok(
    !code.includes("new FormData("),
    "api.uploadMediaRaw is the one place in the product that builds a multipart body",
  );
  assert.ok(code.includes("uploadMediaRaw("), "profile.js must call the shared upload");
  assert.ok(
    code.includes("MAX_MEDIA_BYTES"),
    "the client-side cap must be clamped by the server's own ceiling, or it can drift above it",
  );
  assert.ok(
    /X-Team-Scope/.test(code),
    "the PUT must name the team the item was uploaded under",
  );
});

test("the shell hands the profile a LATE team slug, not one captured at boot", () => {
  assert.match(
    appJs,
    /mountProfile\(api, identity, \{ getTeamSlug: activeTeamSlug \}\)/,
    "a slug read once at boot is the wrong team by the first switch",
  );
  assert.match(
    appJs,
    /import \{ bootChat, activeTeamSlug \}/,
    "the active team belongs to chat.js — a second copy here would drift",
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
