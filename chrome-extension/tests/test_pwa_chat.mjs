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
import { fileURLToPath } from "node:url";
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

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
