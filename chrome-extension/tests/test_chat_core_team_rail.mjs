/**
 * Contract for packages/chat-core/team_rail.js — the team rail both surfaces
 * render (D-27-04).
 *
 * It was extracted from popup.js, so the risk this file exists to cover is a
 * SILENT behaviour change during that move: an ordering rule that now puts new
 * teams first, a badge that renders "0", a drag that saves the arrangement it
 * had before the drop instead of after it. None of those throw, none of them
 * show up in a diff review, and all of them are visible to the user on day one.
 *
 * The module builds DOM through an injected document, so the whole thing runs
 * against a tiny hand-rolled document stub — no jsdom, no dependency.
 *
 * SKIP = FAIL: nothing below is conditional on a file existing.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import {
  TEAM_ORDER_KEY,
  teamInitials,
  orderTeams,
  createTeamRail,
} from "../../packages/chat-core/team_rail.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, "..", "..");
const SOURCE = readFileSync(
  join(REPO_ROOT, "packages", "chat-core", "team_rail.js"),
  "utf8",
);

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

// ---- A minimal document ---------------------------------------------------

/**
 * Just enough element for this module: children, classes, dataset, attributes,
 * listeners, and the one geometry call the drop handler makes.
 *
 * Deliberately NOT jsdom. jsdom is not a repo dependency, and a stub is also a
 * second reading of the contract: every DOM call the module makes has to be
 * declared here, so a new one cannot slip in unnoticed.
 */
function makeElement(tag) {
  const el = {
    tagName: String(tag).toUpperCase(),
    children: [],
    parentNode: null,
    className: "",
    dataset: {},
    attrs: {},
    listeners: {},
    style: {},
    textContent: "",
    title: "",
    type: "",
    draggable: false,
    // A fixed 30px box starting at x=0 — the drop handler only compares
    // clientX against the midpoint, so a constant rect is enough to drive
    // both the "before" and the "after" branch.
    getBoundingClientRect: () => ({ left: 0, width: 30, top: 0, height: 30 }),
    setAttribute(k, v) {
      this.attrs[k] = String(v);
    },
    getAttribute(k) {
      return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null;
    },
    addEventListener(type, fn) {
      (this.listeners[type] = this.listeners[type] || []).push(fn);
    },
    async fire(type, event) {
      for (const fn of this.listeners[type] || []) await fn(event);
    },
    get firstChild() {
      return this.children[0] || null;
    },
    get nextSibling() {
      const p = this.parentNode;
      if (!p) return null;
      return p.children[p.children.indexOf(this) + 1] || null;
    },
    appendChild(child) {
      if (child.parentNode) child.parentNode.removeChild(child);
      child.parentNode = this;
      this.children.push(child);
      return child;
    },
    insertBefore(child, ref) {
      if (child.parentNode) child.parentNode.removeChild(child);
      child.parentNode = this;
      const at = ref ? this.children.indexOf(ref) : -1;
      if (at === -1) this.children.push(child);
      else this.children.splice(at, 0, child);
      return child;
    },
    removeChild(child) {
      const at = this.children.indexOf(child);
      if (at !== -1) this.children.splice(at, 1);
      child.parentNode = null;
      return child;
    },
    remove() {
      if (this.parentNode) this.parentNode.removeChild(this);
    },
    querySelector(sel) {
      const want = String(sel).replace(/^\./, "");
      return this.children.find((c) => String(c.className).split(/\s+/).includes(want)) || null;
    },
    classList: {
      add(c) {
        const set = new Set(String(el.className).split(/\s+/).filter(Boolean));
        set.add(c);
        el.className = [...set].join(" ");
      },
      remove(c) {
        const set = new Set(String(el.className).split(/\s+/).filter(Boolean));
        set.delete(c);
        el.className = [...set].join(" ");
      },
      contains: (c) => String(el.className).split(/\s+/).includes(c),
    },
  };
  return el;
}

const doc = { createElement: (tag) => makeElement(tag) };

/** A storage shim that records what was written, in the shape the real one gets. */
function makeStorage(initial = {}) {
  const store = { ...initial };
  return {
    store,
    writes: [],
    async get(keys) {
      const out = {};
      for (const k of Array.isArray(keys) ? keys : [keys]) {
        if (k in store) out[k] = store[k];
      }
      return out;
    },
    async set(obj) {
      this.writes.push(obj);
      Object.assign(store, obj);
    },
  };
}

const TEAMS = [
  { id: "t1", display_name: "Alpha Squad" },
  { id: "t2", display_name: "Beta" },
  { id: "t3", slug: "gamma-crew" },
];

/** A rail wired to mutable state, the way a real surface wires it. */
function harness({ teams = TEAMS, active = "t1", storage = makeStorage(), unread = null } = {}) {
  const railEl = makeElement("div");
  const state = { teams, active, selected: [] };
  const rail = createTeamRail({
    doc,
    railEl,
    storage,
    getTeams: () => state.teams,
    getActiveTeamId: () => state.active,
    onSelectTeam: (id) => state.selected.push(id),
    getUnreadCount: unread,
  });
  return { rail, railEl, state, storage };
}

const idsOf = (railEl) => railEl.children.map((c) => c.dataset.teamId);

// ---- 1. Initials ----------------------------------------------------------

await test("teamInitials takes one letter from each of the first two words", () => {
  assert.equal(teamInitials("Alpha Squad"), "AS");
  assert.equal(teamInitials("alpha squad beta"), "AS", "only the first two words count");
});

await test("teamInitials falls back to two characters, then to '?'", () => {
  assert.equal(teamInitials("Beta"), "BE");
  assert.equal(teamInitials("x"), "X", "a one-character name must not throw");
  assert.equal(teamInitials(""), "?");
  assert.equal(teamInitials("   "), "?");
  assert.equal(teamInitials(null), "?", "a nameless team still has to render a square");
  assert.equal(teamInitials(undefined), "?");
});

// ---- 2. Ordering ----------------------------------------------------------

await test("orderTeams keeps the API order when nothing was arranged", () => {
  assert.deepEqual(orderTeams(TEAMS, []).map((t) => t.id), ["t1", "t2", "t3"]);
  assert.deepEqual(orderTeams(TEAMS, null).map((t) => t.id), ["t1", "t2", "t3"]);
});

await test("orderTeams honours a saved arrangement", () => {
  assert.deepEqual(orderTeams(TEAMS, ["t3", "t1", "t2"]).map((t) => t.id), ["t3", "t1", "t2"]);
});

await test("a team not in the saved order goes LAST, never into the middle", () => {
  // The whole point of the rule: a team joined today must appear to the RIGHT of
  // the ones already arranged, not wherever /my-teams happened to sort it.
  const withNew = [...TEAMS, { id: "t4", display_name: "New Team" }];
  assert.deepEqual(
    orderTeams(withNew, ["t3", "t2", "t1"]).map((t) => t.id),
    ["t3", "t2", "t1", "t4"],
  );
  // Two unknowns keep the API's own relative order rather than swapping.
  const twoNew = [...TEAMS, { id: "t4" }, { id: "t5" }];
  assert.deepEqual(
    orderTeams(twoNew, ["t2"]).map((t) => t.id).slice(-3),
    ["t3", "t4", "t5"],
  );
});

await test("orderTeams does not mutate its input", () => {
  const input = TEAMS.slice();
  orderTeams(input, ["t3", "t2", "t1"]);
  assert.deepEqual(input.map((t) => t.id), ["t1", "t2", "t3"]);
});

// ---- 3. Rendering ---------------------------------------------------------

await test("render paints one square per team, marked up for a tablist", async () => {
  const { rail, railEl } = harness();
  await rail.render();
  assert.equal(railEl.children.length, 3);
  const [first] = railEl.children;
  assert.equal(first.tagName, "BUTTON");
  assert.equal(first.type, "button", "a button inside a form must not submit it");
  assert.equal(first.className, "xb-team-icon");
  assert.equal(first.getAttribute("role"), "tab");
  assert.equal(first.textContent, "AS");
  assert.equal(first.title, "Alpha Squad");
  assert.equal(first.getAttribute("aria-label"), "Alpha Squad", "the initials alone say nothing to a screen reader");
  assert.equal(first.dataset.teamId, "t1");
  assert.equal(first.draggable, true);
});

await test("only the active square is aria-selected", async () => {
  const { rail, railEl } = harness({ active: "t2" });
  await rail.render();
  assert.deepEqual(
    railEl.children.map((c) => c.getAttribute("aria-selected")),
    ["false", "true", "false"],
  );
});

await test("a team with no display_name renders from its slug", async () => {
  const { rail, railEl } = harness();
  await rail.render();
  assert.equal(railEl.children[2].textContent, "GA", "gamma-crew is one word -> two chars");
  assert.equal(railEl.children[2].title, "gamma-crew");
});

await test("a team name reaches the DOM as text, never as markup", async () => {
  const evil = [{ id: "x", display_name: '<img src=x onerror=alert(1)>' }];
  const { rail, railEl } = harness({ teams: evil, active: "x" });
  await rail.render();
  // The initials are derived, so the payload cannot survive in the label; what
  // matters is that the raw string only ever lands in textContent/attributes.
  assert.equal(railEl.children[0].textContent, "<S", "initials of '<img' and 'src=x'");
  assert.equal(railEl.children[0].title, evil[0].display_name);
  assert.ok(
    !/innerHTML|outerHTML|insertAdjacentHTML/.test(SOURCE),
    "team_rail.js must never assign markup — a team name is a string somebody typed",
  );
});

await test("render replaces the previous squares instead of appending to them", async () => {
  const { rail, railEl } = harness();
  await rail.render();
  await rail.render();
  assert.equal(railEl.children.length, 3, "a second render must not double the rail");
});

// ---- 4. Click to switch ---------------------------------------------------

await test("clicking an inactive square asks the surface to switch", async () => {
  const { rail, railEl, state } = harness({ active: "t1" });
  await rail.render();
  await railEl.children[1].fire("click");
  assert.deepEqual(state.selected, ["t2"]);
});

await test("clicking the ACTIVE square does nothing", async () => {
  // Re-entering the team you are in would tear down the socket subscription and
  // reload the history for no reason.
  const { rail, railEl, state } = harness({ active: "t1" });
  await rail.render();
  await railEl.children[0].fire("click");
  assert.deepEqual(state.selected, []);
});

// ---- 5. Drag to rearrange -------------------------------------------------

/** A DataTransfer stub carrying one payload. */
function dragEvent(draggedId, clientX) {
  return {
    clientX,
    preventDefault() {},
    dataTransfer: {
      effectAllowed: "",
      dropEffect: "",
      _data: { "text/plain": draggedId },
      setData(type, v) {
        this._data[type] = v;
      },
      getData(type) {
        return this._data[type] || "";
      },
    },
  };
}

await test("dropping on the LEFT half inserts before the target", async () => {
  const { rail, railEl } = harness();
  await rail.render();
  // Drag t3 onto the left half of t1.
  await railEl.children[0].fire("drop", dragEvent("t3", 5));
  assert.deepEqual(idsOf(railEl), ["t3", "t1", "t2"]);
});

await test("dropping on the RIGHT half inserts after the target", async () => {
  const { rail, railEl } = harness();
  await rail.render();
  // Drag t3 onto the right half of t1 (midpoint is 15).
  await railEl.children[0].fire("drop", dragEvent("t3", 25));
  assert.deepEqual(idsOf(railEl), ["t1", "t3", "t2"]);
});

await test("a drop persists the order the rail shows AFTER the move", async () => {
  // The bug this pins: reading the order before insertBefore saves the previous
  // arrangement, so the rail snaps back on the next open and nothing reports it.
  const storage = makeStorage();
  const { rail, railEl } = harness({ storage });
  await rail.render();
  await railEl.children[0].fire("drop", dragEvent("t3", 5));
  assert.equal(storage.writes.length, 1, "exactly one write per drop");
  assert.deepEqual(JSON.parse(storage.store[TEAM_ORDER_KEY]), ["t3", "t1", "t2"]);
});

await test("dropping a square on itself changes and saves nothing", async () => {
  const storage = makeStorage();
  const { rail, railEl } = harness({ storage });
  await rail.render();
  await railEl.children[0].fire("drop", dragEvent("t1", 25));
  assert.deepEqual(idsOf(railEl), ["t1", "t2", "t3"]);
  assert.equal(storage.writes.length, 0);
});

await test("dragstart flags the square and carries its id", async () => {
  const { rail, railEl } = harness();
  await rail.render();
  const e = dragEvent("", 0);
  await railEl.children[0].fire("dragstart", e);
  assert.equal(e.dataTransfer.getData("text/plain"), "t1");
  assert.equal(e.dataTransfer.effectAllowed, "move");
  assert.ok(railEl.children[0].classList.contains("is-dragging"));
  await railEl.children[0].fire("dragend");
  assert.ok(!railEl.children[0].classList.contains("is-dragging"));
});

// ---- 6. Persistence across the two storage backends -----------------------

await test("a saved order is applied on the next render", async () => {
  const storage = makeStorage({ [TEAM_ORDER_KEY]: JSON.stringify(["t2", "t3", "t1"]) });
  const { rail, railEl } = harness({ storage });
  await rail.render();
  assert.deepEqual(idsOf(railEl), ["t2", "t3", "t1"]);
});

await test("an order stored as a raw ARRAY still works (the extension's old shape)", async () => {
  // chrome.storage.local round-trips arrays, so every existing extension user
  // has one stored. Refusing it would silently reset everybody's rail.
  const storage = makeStorage({ [TEAM_ORDER_KEY]: ["t3", "t2", "t1"] });
  const { rail, railEl } = harness({ storage });
  await rail.render();
  assert.deepEqual(idsOf(railEl), ["t3", "t2", "t1"]);
});

await test("the order is WRITTEN as a string, so localStorage round-trips it", async () => {
  // localStorage stringifies whatever it is handed: an array would come back as
  // "t3,t1,t2" and parse to nothing, resetting the PWA's rail on every open.
  const storage = makeStorage();
  const { rail, railEl } = harness({ storage });
  await rail.render();
  await railEl.children[0].fire("drop", dragEvent("t3", 5));
  const written = storage.store[TEAM_ORDER_KEY];
  assert.equal(typeof written, "string", "a non-string breaks the localStorage-backed shim");
  assert.deepEqual(JSON.parse(written), ["t3", "t1", "t2"]);
});

await test("garbage in storage degrades to the API order rather than throwing", async () => {
  for (const junk of ["not json", "{}", 42, null, ["t2", 7, "t1"]]) {
    const storage = makeStorage({ [TEAM_ORDER_KEY]: junk });
    const { rail, railEl } = harness({ storage });
    await rail.render();
    assert.equal(railEl.children.length, 3, `render survived ${JSON.stringify(junk)}`);
  }
  // The mixed array keeps its usable entries: t2 first, the rest appended.
  const storage = makeStorage({ [TEAM_ORDER_KEY]: ["t2", 7, "t1"] });
  const { rail, railEl } = harness({ storage });
  await rail.render();
  assert.deepEqual(idsOf(railEl), ["t2", "t1", "t3"]);
});

await test("a storage that throws costs the arrangement, never the rail", async () => {
  const broken = {
    async get() {
      throw new Error("storage blocked");
    },
    async set() {
      throw new Error("storage blocked");
    },
  };
  const { rail, railEl } = harness({ storage: broken });
  await rail.render();
  assert.deepEqual(idsOf(railEl), ["t1", "t2", "t3"]);
  await railEl.children[0].fire("drop", dragEvent("t3", 5));
  assert.deepEqual(idsOf(railEl), ["t3", "t1", "t2"], "the move still happens on screen");
});

// ---- 7. Unread badges -----------------------------------------------------

await test("an inactive team with unread messages gets a badge", async () => {
  const { rail, railEl } = harness({
    active: "t1",
    unread: async (id) => (id === "t2" ? 4 : 0),
  });
  await rail.render();
  assert.equal(railEl.children[1].querySelector(".xb-team-badge").textContent, "4");
  assert.equal(railEl.children[2].querySelector(".xb-team-badge"), null);
});

await test("the ACTIVE team is never badged", async () => {
  // You are looking at it, and the surface marks it read a moment later — a
  // badge there would flash and vanish.
  const { rail, railEl } = harness({ active: "t1", unread: async () => 9 });
  await rail.render();
  assert.equal(railEl.children[0].querySelector(".xb-team-badge"), null);
  assert.equal(railEl.children[1].querySelector(".xb-team-badge").textContent, "9");
});

await test("zero is shown as no badge, not as a '0'", async () => {
  const { rail, railEl } = harness({ active: "t1", unread: async () => 0 });
  await rail.render();
  assert.equal(railEl.children[1].querySelector(".xb-team-badge"), null);
});

await test("a count over 99 is capped at '99+'", async () => {
  const { rail, railEl } = harness({ active: "t1", unread: async () => 1234 });
  await rail.render();
  assert.equal(railEl.children[1].querySelector(".xb-team-badge").textContent, "99+");
  const at99 = harness({ active: "t1", unread: async () => 99 });
  await at99.rail.render();
  assert.equal(at99.railEl.children[1].querySelector(".xb-team-badge").textContent, "99");
});

await test("one team's badge lookup failing does not stop the others", async () => {
  const { rail, railEl } = harness({
    active: "t1",
    unread: async (id) => {
      if (id === "t2") throw new Error("HTTP 500");
      return 3;
    },
  });
  await rail.render();
  assert.equal(railEl.children[1].querySelector(".xb-team-badge"), null);
  assert.equal(railEl.children[2].querySelector(".xb-team-badge").textContent, "3");
});

await test("a surface with no unread source gets no badges at all", async () => {
  // Better than a badge that can never clear: this surface has no read cursor.
  const { rail, railEl } = harness({ active: "t1", unread: null });
  await rail.render();
  for (const c of railEl.children) assert.equal(c.querySelector(".xb-team-badge"), null);
});

await test("refreshBadges clears a badge once the count drops to zero", async () => {
  let count = 5;
  const { rail, railEl } = harness({ active: "t1", unread: async () => count });
  await rail.render();
  assert.equal(railEl.children[1].querySelector(".xb-team-badge").textContent, "5");
  count = 0;
  await rail.refreshBadges();
  assert.equal(railEl.children[1].querySelector(".xb-team-badge"), null, "a stale badge is a lie");
});

await test("refreshBadges reuses the badge node instead of stacking them", async () => {
  const { rail, railEl } = harness({ active: "t1", unread: async () => 2 });
  await rail.render();
  await rail.refreshBadges();
  await rail.refreshBadges();
  const badges = railEl.children[1].children.filter((c) => c.className === "xb-team-badge");
  assert.equal(badges.length, 1);
});

// ---- 8. Construction guards ------------------------------------------------

await test("createTeamRail refuses to build without a document or a container", () => {
  assert.throws(() => createTeamRail({ railEl: makeElement("div") }), /opts\.doc/);
  assert.throws(() => createTeamRail({ doc }), /opts\.railEl/);
});

// ---- 9. Portability --------------------------------------------------------

await test("team_rail.js reaches for no browser global and no extension API", () => {
  const code = SOURCE.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  for (const token of [
    "chrome.",
    "browser.",
    "localStorage",
    "window.",
    "document.",
    "CSS.escape",
    "fetch(",
  ]) {
    assert.ok(
      !code.includes(token),
      `team_rail.js references ${token} — everything it needs is injected, so it must run unchanged on both surfaces`,
    );
  }
});

await test("english-only: no accented Latin chars in team_rail.js", () => {
  const hits = SOURCE.match(/[À-ÿ]/g) || [];
  assert.equal(hits.length, 0, `found ${JSON.stringify([...new Set(hits)])}`);
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
