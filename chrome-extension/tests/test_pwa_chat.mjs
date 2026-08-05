/**
 * Static contract for the PWA's chat surface at app-site/app/ (Phase 27,
 * Plan 27-06).
 *
 * It lives in the extension's test directory because that is where
 * run_tests.mjs walks; it reads ../../app-site/app/ and ../../packages/chat-core/
 * and asserts nothing about the extension itself.
 *
 * THE FORK CHECK IS THE POINT OF THIS FILE. D-27-04 says the two surfaces share
 * one chat core rather than keeping two copies, and the failure mode is not a
 * crash — it is a second implementation that works fine on the day it is written
 * and drifts from the extension's within a week. Nothing in a browser reports
 * that, and nothing in review reliably catches it either, because a forked
 * helper looks exactly like a helper. So it is checked here: no file in
 * app-site/app/ may DECLARE a name that packages/chat-core declares. Importing
 * them is the whole point; redefining one is the regression.
 *
 * The rest guards things that also fail silently on a static host:
 *   - a typo'd import specifier is a blank page in production and nothing
 *     anywhere else — there is no bundler here to reject it;
 *   - a precache list that has drifted from what actually ships either misses a
 *     file (broken offline) or names a ghost (a warning nobody reads);
 *   - a vendored client that diverges between the two surfaces means a realtime
 *     bug reproduces on one and not the other;
 *   - a hardcoded socket URL or team id pins the app to one deployment.
 *
 * SKIP = FAIL: nothing below is conditional on a file existing.
 */

import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, join, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, "..", "..");
const APP_DIR = join(REPO_ROOT, "app-site", "app");
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

/**
 * Comments out, so prose that merely NAMES a function is not mistaken for a
 * declaration of it. Over-stripping is the safe direction here: it can only
 * shrink what the checks below see, never invent a match.
 */
function stripComments(src) {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

/** Every .js directly in app-site/app/ — not chat_core/ (that IS the shared
 *  core) and not vendor/ (a third-party build nobody here wrote). */
function appModules() {
  return readdirSync(APP_DIR)
    .filter((f) => f.endsWith(".js"))
    .sort()
    .map((f) => ({ name: f, src: readFileSync(join(APP_DIR, f), "utf8") }));
}

function coreFiles() {
  return readdirSync(CORE_DIR)
    .filter((f) => f.endsWith(".js"))
    .sort();
}

/**
 * Every function and class packages/chat-core DECLARES — exported or not.
 *
 * Deliberately not just the export list. The forks that actually matter are
 * chat-core's INTERNALS: `buildBubbleNode` and `handlePublication` are returned
 * as part of a factory's surface rather than exported by name, so an export-only
 * check would wave through a hand-rolled copy of the bubble builder, which is
 * precisely the duplication D-27-04 exists to stop.
 */
function coreDeclaredNames() {
  const names = new Set();
  for (const file of coreFiles()) {
    const code = stripComments(readFileSync(join(CORE_DIR, file), "utf8"));
    for (const m of code.matchAll(
      /(?:^|[^.\w$])(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(/g,
    )) {
      names.add(m[1]);
    }
    for (const m of code.matchAll(/(?:^|[^.\w$])class\s+([A-Za-z_$][\w$]*)\b/g)) {
      names.add(m[1]);
    }
  }
  return names;
}

/** The three ways a name gets (re)declared in a module. */
function declarationPatterns(name) {
  const n = name.replace(/[$]/g, "\\$&");
  return [
    new RegExp(`(?:^|[^.\\w$])(?:async\\s+)?function\\s+${n}\\s*\\(`),
    new RegExp(`(?:^|[^.\\w$])class\\s+${n}\\b`),
    new RegExp(`(?:^|[^.\\w$])(?:const|let|var)\\s+${n}\\s*=\\s*(?:async\\s*)?(?:function\\b|\\()`),
  ];
}

// ---- 1. The fork check --------------------------------------------------

test("the chat-core name extractor sees the names it is supposed to guard", () => {
  const names = coreDeclaredNames();
  // If the extractor silently matched nothing, every assertion below would
  // "pass" while checking absolutely nothing. Pin a few known names.
  for (const expected of [
    "createApi",
    "createRenderer",
    "createPublicationRouter",
    "connectRealtime",
    "buildBubbleNode",
    "handlePublication",
    "StreamBuffer",
    "isSafeHttpUrl",
  ]) {
    assert.ok(
      names.has(expected),
      `the extractor did not find "${expected}" in packages/chat-core — the fork check would be inert`,
    );
  }
  assert.ok(names.size >= 25, `expected a substantial name set, got ${names.size}`);
});

test("no file in app-site/app/ redeclares a name chat-core declares (D-27-04)", () => {
  const names = coreDeclaredNames();
  for (const mod of appModules()) {
    const code = stripComments(mod.src);
    for (const name of names) {
      for (const pattern of declarationPatterns(name)) {
        assert.ok(
          !pattern.test(code),
          `app-site/app/${mod.name} declares "${name}", which packages/chat-core already declares — import it instead of forking it (D-27-04)`,
        );
      }
    }
  }
});

test("no app module reintroduces a per-surface fetch helper", () => {
  // `fetchJson` is the helper each surface used to carry before chat-core's
  // createApi replaced it. Its return would mean two places build the
  // Authorization header again.
  for (const mod of appModules()) {
    assert.ok(
      !/(?:^|[^.\w$])(?:async\s+)?function\s+fetchJson\s*\(/.test(stripComments(mod.src)),
      `app-site/app/${mod.name} declares fetchJson — createApi is the one client`,
    );
  }
});

test("chat.js consumes the shared modules rather than reimplementing them", () => {
  const chat = readFileSync(join(APP_DIR, "chat.js"), "utf8");
  for (const spec of [
    "./chat_core/api.js",
    "./chat_core/render.js",
    "./chat_core/publication.js",
    "./chat_core/realtime.js",
  ]) {
    assert.ok(chat.includes(spec), `chat.js must import ${spec}`);
  }
  for (const call of [
    "createApi(",
    "createRenderer(",
    "createPublicationRouter(",
    "connectRealtime(",
  ]) {
    assert.equal(
      (chat.match(new RegExp(call.replace("(", "\\("), "g")) || []).length,
      1,
      `chat.js must call ${call}) exactly once`,
    );
  }
});

// ---- 2. Every import specifier resolves ---------------------------------

test("every module specifier in app-site/app/ resolves to a file on disk", () => {
  let total = 0;
  for (const mod of appModules()) {
    const code = stripComments(mod.src);
    const specs = [
      ...code.matchAll(/\bfrom\s+["']([^"']+)["']/g),
      ...code.matchAll(/\bimport\s*\(\s*["']([^"']+)["']\s*\)/g),
    ].map((m) => m[1]);
    // Not every file here is a module: sw.js is a classic worker script and
    // legitimately imports nothing. The inert-extractor guard is therefore on
    // the total below, not on each file.
    total += specs.length;
    for (const spec of specs) {
      assert.ok(
        spec.startsWith("./") || spec.startsWith("../"),
        `app-site/app/${mod.name} imports the bare specifier "${spec}" — there is no bundler and no import map here, so the browser cannot resolve it`,
      );
      const target = resolve(APP_DIR, spec);
      assert.ok(
        existsSync(target),
        `app-site/app/${mod.name} imports "${spec}", which does not exist — a typo here is a blank page in production and an error nowhere else`,
      );
    }
  }
  assert.ok(total > 0, "found no import specifiers at all — this check is inert");
});

// ---- 3. One vendored realtime client, not two ---------------------------

test("app-site/app/vendor/centrifuge.js is byte-identical to the extension's", () => {
  const pwa = join(APP_DIR, "vendor", "centrifuge.js");
  const ext = join(REPO_ROOT, "chrome-extension", "vendor", "centrifuge.js");
  assert.ok(existsSync(pwa), "the PWA must ship the vendored Centrifugo client");
  assert.ok(existsSync(ext), "the extension's vendored client is missing");
  assert.ok(
    readFileSync(pwa).equals(readFileSync(ext)),
    "the two surfaces must run the SAME client build, or a realtime bug reproduces on one and not the other",
  );
});

// ---- 4. Nothing about a deployment is baked into chat.js ----------------

test("chat.js contains no socket or origin literal (D-27-03)", () => {
  const chat = readFileSync(join(APP_DIR, "chat.js"), "utf8");
  const hits = chat.match(/wss?:\/\/|https?:\/\//g) || [];
  assert.equal(
    hits.length,
    0,
    `chat.js names ${JSON.stringify(hits)} — the socket URL comes from POST /v1/me/centrifugo-token and the API origin from auth.js`,
  );
  assert.ok(
    !/["'`]team:/.test(chat),
    "chat.js must not build a channel name — chat-core's realtime module owns the channel model",
  );
});

test("chat.js hardcodes no team id and no team slug", () => {
  const chat = readFileSync(join(APP_DIR, "chat.js"), "utf8");
  const uuid = chat.match(
    /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i,
  );
  assert.equal(
    uuid,
    null,
    `chat.js contains the literal id ${uuid && uuid[0]} — every team comes from /v1/teams/my-teams`,
  );
  // A team-scoped call taking a string literal is the same bug wearing a slug.
  const literalArg =
    /(?:subscribeTeam|switchTeam|listMessages|postMessage|agentAliases)\(\s*["'`]/.exec(chat);
  assert.equal(
    literalArg,
    null,
    `chat.js passes a literal team to ${literalArg && literalArg[0]} — the active team is state, not a constant`,
  );
});

test("chat.js cannot open a teammate's link without a click (D-22-02)", () => {
  const chat = readFileSync(join(APP_DIR, "chat.js"), "utf8");
  assert.ok(
    !chat.includes("openDirect"),
    "chat.js must supply no auto-open dependency to handleOpenUrl — the PWA has no auto-open path at all",
  );
  assert.ok(
    chat.includes("isSafeHttpUrl"),
    "the nudge URL must be re-validated at the point of action, not only on arrival",
  );
});

test("chat.js is the chat, and the other surfaces live elsewhere", () => {
  // The board, the catch-me-up banner and the web clipper are still out of the
  // PWA. The members and invite overlays are IN — but they belong to panels.js,
  // which owns their ids and their state. A chat file that starts binding
  // #invite-code-row is a chat file on its way to owning everything.
  const chat = readFileSync(join(APP_DIR, "chat.js"), "utf8");
  for (const marker of ["catchup", "invite-code", "/boards", "clip-overlay", "people-list"]) {
    assert.ok(!chat.includes(marker), `chat.js references "${marker}"`);
  }
  assert.ok(
    chat.includes('from "./panels.js"'),
    "chat.js must reach the overlays through panels.js rather than reimplementing them",
  );
});

// ---- 5. The precache list matches what actually ships -------------------

/**
 * sw.js is the ONE shipped file that must never be precached.
 *
 * A cache-first worker that caches itself serves the old worker forever, which
 * is also why firebase.json sends /app/sw.js with no-cache. Every other file
 * under app-site/app/ belongs in SHELL.
 */
const NEVER_PRECACHED = new Set(["/app/sw.js"]);

/**
 * Precached ahead of shipping, by a later plan in this phase. Same self-pruning
 * shape test_pwa_shell.mjs uses: the entry must still be in SHELL, and it must
 * still be absent — the day it lands, this list has to shrink.
 */
const PENDING_SHELL_ENTRIES = new Set([
  // Empty since 27-07 shipped /app/push.js. Kept rather than deleted: the
  // mechanism is what stops the NEXT precache-ahead entry from becoming
  // permanent.
]);

function shellEntries() {
  const sw = readFileSync(join(APP_DIR, "sw.js"), "utf8");
  const block = sw.match(/const SHELL = \[([\s\S]*?)\];/);
  assert.ok(block, "sw.js must declare a SHELL array");
  return [...block[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]);
}

function shippedFiles(dir = APP_DIR, prefix = "/app/") {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      out.push(...shippedFiles(join(dir, entry.name), `${prefix}${entry.name}/`));
    } else {
      out.push(`${prefix}${entry.name}`);
    }
  }
  return out;
}

function shellUrlToPath(url) {
  const rel = url.replace(/^\/app\//, "");
  return join(APP_DIR, rel === "" ? "index.html" : rel);
}

test("SHELL covers every file app-site/app/ actually ships", () => {
  const shell = new Set(shellEntries());
  assert.ok(shell.has("/app/"), "SHELL must include the bare /app/ start_url");
  for (const url of shippedFiles()) {
    if (NEVER_PRECACHED.has(url)) continue;
    assert.ok(
      shell.has(url),
      `${url} ships but is not in SHELL — it would be missing offline, with no error to explain why`,
    );
  }
});

test("SHELL names no file that does not exist", () => {
  for (const url of shellEntries()) {
    if (url === "/app/" || PENDING_SHELL_ENTRIES.has(url)) continue;
    assert.ok(
      existsSync(shellUrlToPath(url)),
      `sw.js precaches ${url}, which does not exist — a typo here fails the install with no visible error`,
    );
  }
});

test("the pending-entry exemption list has not gone stale", () => {
  const shell = new Set(shellEntries());
  for (const url of PENDING_SHELL_ENTRIES) {
    assert.ok(
      shell.has(url),
      `${url} is exempted but no longer in SHELL — drop it from PENDING_SHELL_ENTRIES`,
    );
    assert.ok(
      !existsSync(shellUrlToPath(url)),
      `${url} has shipped — remove it from PENDING_SHELL_ENTRIES so it is checked like every other entry`,
    );
  }
  // An empty list makes the loop above vacuous, so state what emptying it
  // bought: the last exemption is now under the existence check like anything
  // else. Without this, deleting the entry and deleting the file would look the
  // same to this test.
  assert.ok(shell.has("/app/push.js"), "/app/push.js must still be precached");
  assert.ok(
    !PENDING_SHELL_ENTRIES.has("/app/push.js"),
    "/app/push.js shipped in 27-07 — it must be checked, not exempted",
  );
});

// ---- 5b. The routing status is the server's answer, never a guess -------

test("the PWA reads its routing status from the server", () => {
  const chatJs = readFileSync(join(APP_DIR, "chat.js"), "utf8");
  assert.ok(
    chatJs.includes("api.agentRoute("),
    "the status must come from GET /v1/me/agent-route — the same resolution the agent runs",
  );
  assert.ok(
    !/chrome\.runtime\.sendMessage[\s\S]{0,200}(route|subscription_connected)/.test(chatJs),
    "the routing must not be inferred from whether an extension answered — a " +
      "client that decides this eventually disagrees with the thing that routes the turn",
  );
});

test("the routing poll stops when nobody is looking", () => {
  // A status check every few seconds, for a condition that changes rarely, is a
  // phone battery spent on an answer that is almost always the same one.
  const chatJs = readFileSync(join(APP_DIR, "chat.js"), "utf8");
  assert.ok(
    chatJs.includes("visibilityState"),
    "polling must be gated on the page being visible",
  );
  const interval = chatJs.match(/ROUTE_POLL_MS\s*=\s*([0-9_]+)/);
  assert.ok(interval, "the poll interval must be a named constant");
  assert.ok(
    Number(interval[1].replace(/_/g, "")) >= 30_000,
    "a poll under 30s is aggressive for something that changes this rarely",
  );
});

test("both new surfaces exist in the markup they are bound to", () => {
  const html = readFileSync(join(APP_DIR, "index.html"), "utf8");
  for (const id of ["agent-route-status", "subscription-notice"]) {
    assert.ok(html.includes(`id="${id}"`), `#${id} is bound by chat.js but absent from the markup`);
  }
});

// ---- 6. English-only product strings ------------------------------------

test("english-only: no accented Latin chars in index.html or chat.js", () => {
  for (const file of ["index.html", "chat.js"]) {
    const hits = readFileSync(join(APP_DIR, file), "utf8").match(/[À-ÿ]/g) || [];
    assert.equal(
      hits.length,
      0,
      `app-site/app/${file} has ${hits.length} accented char(s) ${JSON.stringify([
        ...new Set(hits),
      ])} — product strings must be English`,
    );
  }
});

// ---- 7. The ids chat.js binds exist in the markup -----------------------

test("every id chat.js reads is declared in index.html", () => {
  const chat = readFileSync(join(APP_DIR, "chat.js"), "utf8");
  const html = readFileSync(join(APP_DIR, "index.html"), "utf8");
  const ids = new Set(
    [...chat.matchAll(/\bel\(\s*["']([a-z0-9-]+)["']\s*\)/g)].map((m) => m[1]),
  );
  assert.ok(ids.size >= 8, `expected chat.js to bind several ids, found ${ids.size}`);
  for (const id of ids) {
    assert.ok(
      html.includes(`id="${id}"`),
      `chat.js reads #${id}, which index.html does not declare — the binding silently does nothing`,
    );
  }
});

// ===========================================================================
// The agent toggle, on THIS surface too.
//
// The brief asks for both surfaces, and "both" is exactly the thing that gets
// half-done: the extension has a contract test that would go red, this one did
// not. The properties pinned here are the same ones — where it sits, that its
// armed state is visible AND announced, and that it summons through the mention
// rather than a second parallel mechanism.
// ===========================================================================

const pwaHtml = readFileSync(join(APP_DIR, "index.html"), "utf8").replace(
  /<!--[\s\S]*?-->/g,
  "",
);
const pwaChatJs = readFileSync(join(APP_DIR, "chat.js"), "utf8");
const pwaCss = readFileSync(join(APP_DIR, "app.css"), "utf8").replace(
  /\/\*[\s\S]*?\*\//g,
  "",
);

test("agent toggle: #btn-agent is the control immediately left of #btn-send", () => {
  const start = pwaHtml.indexOf('class="xb-composer-pill"');
  assert.ok(start !== -1, "index.html has no .xb-composer-pill");
  // Bounded at the pill's own close so a button elsewhere on the page cannot
  // stand in for one inside the composer.
  const open = pwaHtml.lastIndexOf("<div", start);
  const re = /<div\b|<\/div>/g;
  re.lastIndex = open;
  let depth = 0;
  let end = -1;
  let m;
  while ((m = re.exec(pwaHtml)) !== null) {
    depth += m[0] === "</div>" ? -1 : 1;
    if (depth === 0) {
      end = m.index + m[0].length;
      break;
    }
  }
  assert.ok(end !== -1, "the composer pill is not closed");
  const ids = [...pwaHtml.slice(open, end).matchAll(/<button[^>]*id="([^"]+)"/g)].map(
    (x) => x[1],
  );
  assert.deepEqual(
    ids.slice(-2),
    ["btn-agent", "btn-send"],
    `the toggle must be immediately left of send (pill order: ${ids.join(" -> ")})`,
  );
});

test("agent toggle: it is a toggle for the eye AND for a screen reader", () => {
  const tag = /<button[^>]*id="btn-agent"[^>]*>/.exec(pwaHtml);
  assert.ok(tag, "index.html has no #btn-agent");
  assert.match(tag[0], /aria-pressed="false"/);
  assert.match(tag[0], /data-state="off"/);
  assert.match(
    tag[0],
    /class="[^"]*\bxb-icon-btn\b/,
    "the toggle belongs to the .xb-icon-btn family (shadcn Neutral, radius 0, 15px svg)",
  );

  const fn = /function\s+setAgentArmed\s*\([^)]*\)\s*\{[\s\S]*?\n\}/.exec(pwaChatJs);
  assert.ok(fn, "chat.js has no setAgentArmed()");
  assert.match(fn[0], /dataset\.state\s*=/, "must write [data-state] for the stylesheet");
  assert.match(fn[0], /aria-pressed/, "must write [aria-pressed] for a screen reader");
});

test("agent toggle: the armed state is filled --primary, not a whisper", () => {
  assert.match(
    pwaCss,
    /\.xb-agent-btn\[data-state="on"\]\s*\{[^}]*background:\s*var\(--primary\)/,
    "app.css must give the armed toggle the filled --primary look",
  );
});

test("agent toggle: ONE summon mechanism — the mention, never a parallel flag", () => {
  const fn = /async\s+function\s+sendMessage\s*\([^)]*\)\s*\{[\s\S]*?\n\}/.exec(pwaChatJs);
  assert.ok(fn, "chat.js has no sendMessage()");
  const body = stripComments(fn[0]);
  assert.ok(
    body.includes("withAgentMention("),
    "the toggle must compose the outgoing TEXT — that is what the server's detector reads",
  );
  for (const flag of ["summon", "to_agent", "toAgent", "is_agent", "mention_agent"]) {
    assert.ok(
      !body.includes(flag),
      `sendMessage sends a "${flag}" field — a second summon authority that can disagree with the mention`,
    );
  }
  assert.ok(
    body.includes("setAgentArmed(false)"),
    "a successful send must disarm the toggle — this is a shared team chat",
  );
});

// ---- The composer must not close the keyboard it is being typed into -----
//
// Stated as FOCUS OWNERSHIP, which is the provable half. A stub has no keyboard,
// so an assertion about one would be theatre; what actually broke is that focus
// left the textarea, and that is visible here. The unprovable half — that iOS
// runs the focus change as mousedown's default action, and that a keyboard once
// closed cannot be reopened from script — is named in dom.js and in the report.

const dom = await import(pathToFileURL(join(CORE_DIR, "dom.js")).href);

/** The smallest document that can lose focus: elements, listeners, activeElement. */
function makeDoc() {
  const doc = { activeElement: null };
  const make = (id) => {
    const handlers = {};
    const node = {
      id,
      disabled: false,
      addEventListener: (type, fn) => {
        (handlers[type] = handlers[type] || []).push(fn);
      },
      removeEventListener: (type, fn) => {
        const list = handlers[type] || [];
        const i = list.indexOf(fn);
        if (i !== -1) list.splice(i, 1);
      },
      focus: () => {
        doc.activeElement = node;
      },
      blur: () => {
        if (doc.activeElement === node) doc.activeElement = null;
      },
      count: (type) => (handlers[type] || []).length,
      /**
       * A press, as a browser performs one: mousedown, then the default action
       * that moves focus HERE — unless somebody cancelled it — then click.
       */
      press: () => {
        let prevented = false;
        const event = {
          type: "mousedown",
          preventDefault: () => {
            prevented = true;
          },
        };
        for (const fn of handlers.mousedown || []) fn(event);
        if (!prevented) doc.activeElement = node;
        for (const fn of handlers.click || []) fn({ type: "click" });
      },
    };
    return node;
  };
  doc.make = make;
  return doc;
}

test("the stub can show the bug: an unguarded press takes focus off the field", () => {
  // Without this, the next test would pass against a stub that could not move
  // focus at all — an assertion that cannot fail for the reason the bug happens
  // is not evidence.
  const doc = makeDoc();
  const input = doc.make("composer-input");
  const button = doc.make("btn-send");
  let clicks = 0;
  button.addEventListener("click", () => clicks++);

  input.focus();
  button.press();
  assert.equal(doc.activeElement, button, "this IS the defect: focus moved, and on iOS the keyboard follows it");
  assert.equal(clicks, 1);
});

test("...and keepFocusOnPress changes exactly that, and nothing else", () => {
  const doc = makeDoc();
  const input = doc.make("composer-input");
  const button = doc.make("btn-send");
  let clicks = 0;
  button.addEventListener("click", () => clicks++);
  const detach = dom.keepFocusOnPress(button);

  input.focus();
  button.press();
  assert.equal(
    doc.activeElement,
    input,
    "the field must still hold focus after the send control is pressed — losing it is what closed the keyboard",
  );
  assert.equal(clicks, 1, "the control must still DO its job; a swallowed click sends nothing");

  detach();
  assert.equal(button.count("mousedown"), 0, "the guard comes off cleanly");
});

test("disabling the button mid-send cannot take focus, because it never had it", () => {
  // The second cause: disabling an element that holds focus moves focus to the
  // body. Order matters here only if the first cause was left in place.
  const doc = makeDoc();
  const input = doc.make("composer-input");
  const button = doc.make("btn-send");
  dom.keepFocusOnPress(button);
  input.focus();

  button.press();
  button.disabled = true; // in flight
  button.disabled = false; // finally
  assert.equal(doc.activeElement, input, "focus never left, so there was nothing for the disable to steal");
});

test("keepFocusOnPress survives being handed nothing", () => {
  assert.equal(typeof dom.keepFocusOnPress(), "function");
  assert.equal(typeof dom.keepFocusOnPress(null)(), "undefined");
});

test("EVERY control in the pill is guarded, not just the send button", () => {
  const body = stripComments(pwaChatJs);
  assert.match(
    body,
    /import \{ keepFocusOnPress \} from "\.\/chat_core\/dom\.js"/,
    "the guard is shared code, not a local preventDefault somebody will delete",
  );
  const loop = /for \(const control of \[([^\]]*)\]\) keepFocusOnPress\(control\)/.exec(body);
  assert.ok(loop, "chat.js must guard its composer controls");
  for (const name of ["sendBtn", "agentBtn", "clipBtn"]) {
    assert.ok(
      loop[1].includes(name),
      `${name} is unguarded — pressing it drops the keyboard mid-compose, and the agent toggle is pressed BEFORE the message it applies to`,
    );
  }
});

test("the recovery focus() stays, and is not mistaken for the fix", () => {
  const fn = /async\s+function\s+sendMessage\s*\([^)]*\)\s*\{[\s\S]*?\n\}/.exec(pwaChatJs);
  assert.ok(fn, "chat.js has no sendMessage()");
  assert.match(
    stripComments(fn[0]),
    /input\.focus\(\)/,
    "correct on desktop, where a click genuinely does move focus",
  );
});

// ---- The thread's own furniture: no scrollbar, one way back to the end ----

test("the thread hides its scrollbar across all three engines", () => {
  const scroll = /#chat-scroll\s*\{([^}]*)\}/.exec(pwaCss);
  assert.ok(scroll, "app.css has no #chat-scroll rule");
  assert.match(scroll[1], /scrollbar-width:\s*none/, "Firefox");
  assert.match(scroll[1], /-ms-overflow-style:\s*none/, "old Edge");
  assert.match(
    pwaCss,
    /#chat-scroll::-webkit-scrollbar\s*\{[^}]*display:\s*none/,
    "WebKit — and without it the bar is still there on the phone this is for",
  );
  assert.match(
    scroll[1],
    /overflow-y:\s*auto/,
    "hiding the indicator must not hide the overflow: the thread still scrolls",
  );
});

test("the jump control exists, is a real control, and starts hidden", () => {
  const btn = /<button[^>]*id="btn-jump-latest"[^>]*>/.exec(pwaHtml);
  assert.ok(btn, "index.html has no #btn-jump-latest");
  assert.match(btn[0], /\bhidden\b/, "at the bottom of a thread there is nothing to jump to");
  assert.match(
    btn[0],
    /aria-label="[^"]+"/,
    "an icon-only button with no accessible name is a control only sighted pointer users have",
  );
  assert.match(btn[0], /type="button"/, "inside no form, but never a submit");
  assert.match(
    pwaCss,
    /\.xb-jump:focus-visible\s*\{[^}]*outline:/,
    "keyboard-reachable means keyboard-VISIBLE; a focusable control with no focus ring is reachable and untraceable",
  );
  assert.match(
    pwaCss,
    /\.xb-jump svg\s*\{[^}]*width:\s*15px/,
    "an inline SVG with no intrinsic size renders at ~300x150 — it has already bitten this project once",
  );
});

test("the jump control is anchored by layout, not by a measured offset", () => {
  const slot = /\.xb-jump-slot\s*\{([^}]*)\}/.exec(pwaCss);
  assert.ok(slot, "app.css has no .xb-jump-slot");
  assert.match(
    slot[1],
    /height:\s*0/,
    "a zero-height slot is what keeps the button a fixed distance above the composer's TOP edge — through a grown textarea, an upload error, the safe-area inset and the keyboard alike",
  );
  assert.match(slot[1], /position:\s*relative/, "the button positions against it");
  const idx = pwaHtml.indexOf('class="xb-jump-slot"');
  assert.ok(idx > pwaHtml.indexOf('id="chat-scroll"'), "it belongs after the thread");
  assert.ok(idx < pwaHtml.indexOf('id="composer"'), "and before the composer it sits above");
});

test("the jump control agrees with the rest of the app about where the bottom is", () => {
  const body = stripComments(pwaChatJs);
  assert.match(
    body,
    /function syncJumpLatest\(\)[\s\S]*?isNearBottom\(scrollEl\)/,
    "a second threshold here would show the button while the app believes it is at the bottom — and pressing it would then do nothing",
  );
  assert.match(
    body,
    /scrollEl\.addEventListener\("scroll",[\s\S]*?syncJumpLatest\(\)/,
    "it is driven by the scroll it describes",
  );
  assert.match(
    body,
    /syncJumpLatest\(\);/,
    "and re-asked after a team switch, or it survives into a thread it does not describe",
  );
  assert.match(
    body,
    /if \(btn\.hidden !== atBottom\) btn\.hidden = atBottom;/,
    "a scroll handler writes only when the answer changed",
  );
});

// ---- Swipe to switch team: the wiring, which is where this can go wrong ----
//
// swipe_nav.js is tested on its own. What cannot be tested there is the thing
// that actually matters here: that the gesture borrows the RAIL's order and the
// RAIL's switch. A swipe that changed the view without changing the websocket
// subscription is the worst available outcome, because it looks like it worked.

test("the swipe goes through the rail — its order AND its switch", () => {
  const body = stripComments(pwaChatJs);
  assert.match(
    body,
    /import \{ createSwipeNavigator \} from "\.\/chat_core\/swipe_nav\.js"/,
    "the gesture is shared code, not a set of touch handlers written into the surface",
  );
  assert.match(
    body,
    /teamRail\.selectAdjacent\(/,
    "the swipe must ask the rail which team is next — a second order computed here " +
      "would be right until the first drag-to-reorder, and silently wrong after it",
  );
  // A swipe must not reach switchTeam, the team list or storage on its own. Each
  // of those would be a second path into the same state, and the two would drift.
  const handler = /onSwipe:\s*\(direction\)\s*=>\s*\{([\s\S]*?)\n  \},/.exec(pwaChatJs);
  assert.ok(handler, "chat.js has no onSwipe handler to check");
  for (const forbidden of ["switchTeam(", "state.teams", "state.activeTeamId", "storage."]) {
    assert.ok(
      !handler[1].includes(forbidden),
      `the swipe handler touches ${forbidden} directly — everything must go through selectAdjacent`,
    );
  }
});

test("the swipe is bound to the thread, and refuses the places it must not claim", () => {
  const body = stripComments(pwaChatJs);
  const call = /createSwipeNavigator\(\{([\s\S]*?)\n\}\);/.exec(body);
  assert.ok(call, "chat.js has no createSwipeNavigator call");
  assert.match(call[1], /surfaceEl:\s*el\("chat-scroll"\)/, "the gesture belongs to the thread");
  for (const selector of ["#composer", "#team-rail"]) {
    assert.ok(
      call[1].includes(selector),
      `a touch starting in ${selector} must not be a team switch — it is typing, or the rail's own drag`,
    );
  }
  assert.equal(
    (body.match(/createSwipeNavigator\(/g) || []).length,
    1,
    "exactly one navigator; a second would switch two teams per swipe",
  );
});

test("no mouse or wheel handler leaked into the surface either — desktop is untouched", () => {
  const body = stripComments(pwaChatJs);
  for (const banned of ['"wheel"', '"mousedown"', '"mousemove"', '"pointerdown"']) {
    assert.ok(
      !body.includes(`addEventListener(${banned}`),
      `chat.js binds ${banned} — a trackpad flick or a text selection would change team`,
    );
  }
});

test("the swipe transition exists ONLY under prefers-reduced-motion: no-preference", () => {
  // Not an animation that a later rule switches off — one that does not exist
  // for a reader who asked for less motion. There is no second place to forget.
  // Closed on a `}` in column ONE — the nested keyframes are indented, so this
  // takes the media query's own brace rather than the first one it meets.
  const block = /@media \(prefers-reduced-motion: no-preference\) \{([\s\S]*?)^\}/m.exec(pwaCss);
  assert.ok(block, "app.css declares no no-preference block");
  assert.match(block[1], /data-swipe="next"/, "the forward slide lives inside it");
  assert.match(block[1], /data-swipe="previous"/, "and so does the backward one");
  assert.match(block[1], /@keyframes xb-swipe-from-right/);
  assert.match(block[1], /@keyframes xb-swipe-from-left/);

  // And nothing about the swipe animates outside that block.
  const outside = pwaCss.replace(block[0], "");
  assert.ok(
    !/data-swipe/.test(outside),
    "a data-swipe rule outside the no-preference block would move for everybody",
  );
  assert.ok(
    !/xb-swipe-from-(right|left)/.test(outside),
    "the keyframes must not be reachable from outside the guard either",
  );
});

test("the swipe writes a state the stylesheet reads, and clears it again", () => {
  const body = stripComments(pwaChatJs);
  const fn = /function markSwipeDirection\([^)]*\)\s*\{[\s\S]*?\n\}/.exec(body);
  assert.ok(fn, "chat.js has no markSwipeDirection()");
  assert.match(fn[0], /dataset\.swipe\s*=/, "it must write the attribute the CSS keys off");
  assert.match(
    fn[0],
    /delete\s+scrollEl\.dataset\.swipe/,
    "and remove it — an attribute left behind replays the animation on the next repaint",
  );
});

test("swipe_nav.js is precached, or the PWA loads chat.js against a module the cache never fetched", () => {
  const sw = readFileSync(join(APP_DIR, "sw.js"), "utf8");
  assert.ok(sw.includes('"/app/chat_core/swipe_nav.js"'), "sw.js must precache swipe_nav.js");
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
