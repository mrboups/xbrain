/**
 * The PWA shell-cache name is DERIVED, and this is the gate that runs.
 *
 * THE FAILURE THIS REPLACES. `CACHE` in app-site/app/sw.js was a hand-bumped
 * constant, and it was missed three times in three days. The last miss is the
 * one worth stating: two agents each reserved `v10` while working in separate
 * worktrees, and nothing revealed it until the merge — because a constant cannot
 * be reserved, and nothing tells you the number you picked is taken.
 *
 * The shell is cache-first, so every miss serves a returning reader the files
 * installed under the old name: a new module the cache never fetched, an old
 * chat against a new API. Both present as a broken feature rather than as a
 * caching problem, and that is what makes each one cost hours instead of minutes.
 *
 * scripts/shell-cache.mjs computes the name from the bytes of everything in
 * SHELL. This file asserts three things, and the second is the point:
 *
 *   1. sw.js declares the name that its content actually produces;
 *   2. the computation RESPONDS to content — change a precached file and the
 *      name changes, change nothing and it does not. Asserted against a scratch
 *      copy of the shell, so it is the mechanism being tested and not a fixture;
 *   3. the gate is wired where the other gates run, and the generated name has
 *      the shape sw.js's own tests expect.
 *
 * It lives in the extension's test directory because that is where run_tests.mjs
 * walks — which is also what makes the gate one that actually runs, rather than
 * an elegant scheme sitting in scripts/ that nobody invokes.
 */

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { cpSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import {
  CACHE_PREFIX,
  CACHE_HASH_LENGTH,
  computeCacheName,
  declaredCacheName,
  shellEntries,
  shellUrlToPath,
} from "../../scripts/shell-cache.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, "..", "..");
const APP_DIR = join(REPO_ROOT, "app-site", "app");
const SW_SOURCE = readFileSync(join(APP_DIR, "sw.js"), "utf8");

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

// ---- 1. The name in the file matches the content it precaches --------------

test("sw.js declares the cache name its own content produces", () => {
  const expected = computeCacheName(SW_SOURCE, APP_DIR);
  assert.equal(
    declaredCacheName(SW_SOURCE),
    expected,
    "a precached file changed and sw.js's CACHE did not — run `make shell-cache` and commit sw.js",
  );
});

test("the name is derived, not a survivor of the hand-bumped era", () => {
  const declared = declaredCacheName(SW_SOURCE);
  assert.ok(declared.startsWith(CACHE_PREFIX), `the name must start with ${CACHE_PREFIX}`);
  const suffix = declared.slice(CACHE_PREFIX.length);
  assert.match(
    suffix,
    new RegExp(`^[0-9a-f]{${CACHE_HASH_LENGTH}}$`),
    `"${suffix}" is not a ${CACHE_HASH_LENGTH}-character digest — a hand-written "v14" would land here`,
  );
});

test("sw.js says the value is generated, so the next reader does not edit it", () => {
  const block = SW_SOURCE.slice(0, SW_SOURCE.indexOf("const CACHE ="));
  assert.match(
    block.slice(-1400),
    /GENERATED/,
    "the constant must be labelled generated, or somebody will hand-bump it back",
  );
  assert.match(block.slice(-1400), /shell-cache\.mjs/, "and point at the script that writes it");
});

// ---- 2. THE MECHANISM: does it actually respond to content? ----------------
//
// The assertion above would pass forever against a computation that returned a
// constant. These run the real thing over a scratch copy of the shell and make
// the exact failure happen: change a precached file, leave the name alone.

/** A throwaway copy of app-site/app, so the checkout is never written to. */
function scratchShell() {
  const dir = mkdtempSync(join(tmpdir(), "xb-shell-"));
  cpSync(APP_DIR, dir, { recursive: true });
  return dir;
}

test("THE PROOF: changing a precached file changes the name", () => {
  const dir = scratchShell();
  try {
    const before = computeCacheName(SW_SOURCE, dir);
    assert.equal(before, declaredCacheName(SW_SOURCE), "the copy starts identical");

    // Exactly the missed-bump scenario: a precached module gains a line, and
    // nobody touches CACHE.
    const target = shellUrlToPath("/app/chat_core/markdown.js", dir);
    assert.ok(existsSync(target), "the file this proof edits must exist");
    writeFileSync(target, `${readFileSync(target, "utf8")}\n// a one-line change\n`);

    const after = computeCacheName(SW_SOURCE, dir);
    assert.notEqual(after, before, "THE GATE IS BLIND: a precached file changed and the name did not");
    // And that is precisely what the CLI's --check compares.
    assert.notEqual(
      after,
      declaredCacheName(SW_SOURCE),
      "`shell-cache.mjs --check` would go red here, which is the whole point",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("...and every OTHER precached file does the same, not just one lucky entry", () => {
  const dir = scratchShell();
  try {
    const baseline = computeCacheName(SW_SOURCE, dir);
    // Keyed by the file on disk, not by the URL: `/app/` and `/app/index.html`
    // are two entries answered by ONE document, so editing it legitimately
    // produces the same name twice. That is the aliasing working, not a collision.
    const seen = new Map();
    const visited = new Set();
    for (const url of shellEntries(SW_SOURCE)) {
      const path = shellUrlToPath(url, dir);
      if (!existsSync(path) || visited.has(path)) continue;
      visited.add(path);
      const original = readFileSync(path);
      writeFileSync(path, Buffer.concat([original, Buffer.from("\n/* x */\n")]));
      const mutated = computeCacheName(SW_SOURCE, dir);
      assert.notEqual(mutated, baseline, `editing ${url} did not change the cache name`);
      assert.ok(
        !seen.has(mutated),
        `editing ${url} produced the same name as editing ${seen.get(mutated)} — ` +
          "the digest is collapsing distinct files together",
      );
      seen.set(mutated, url);
      writeFileSync(path, original);
    }
    assert.ok(visited.size >= 30, `expected the whole shell to be exercised, checked ${visited.size}`);
    assert.equal(computeCacheName(SW_SOURCE, dir), baseline, "restoring every file restores the name");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("adding an entry to SHELL changes the name, even for a file already hashed", () => {
  // The URL is in the digest as well as the bytes. Without that, precaching an
  // existing file under a second URL would ship silently under the old name.
  const dir = scratchShell();
  try {
    const before = computeCacheName(SW_SOURCE, dir);
    const grown = SW_SOURCE.replace(
      '  "/app/index.html",',
      '  "/app/index.html",\n  "/app/",',
    );
    assert.notEqual(grown, SW_SOURCE, "the SHELL edit did not apply");
    assert.notEqual(computeCacheName(grown, dir), before);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("an entry that has not shipped yet still moves the name on the day it does", () => {
  // SHELL legitimately names files a later plan creates. Their absence must hash
  // to something stable, and their arrival must change the name.
  const dir = scratchShell();
  try {
    const pending = SW_SOURCE.replace(
      '  "/app/index.html",',
      '  "/app/index.html",\n  "/app/not-yet.js",',
    );
    const absent = computeCacheName(pending, dir);
    assert.equal(absent, computeCacheName(pending, dir), "an absent entry hashes stably");
    writeFileSync(join(dir, "not-yet.js"), "export const x = 1;\n");
    assert.notEqual(
      computeCacheName(pending, dir),
      absent,
      "a pending entry shipping must change the name",
    );

    // AND an absent entry still carries its URL. The rule is uniform — the name
    // is a function of the SHELL list and the bytes behind it, with no case
    // where a URL quietly stops counting. Without this, the absent branch could
    // return any constant at all and nothing here would notice.
    const other = SW_SOURCE.replace(
      '  "/app/index.html",',
      '  "/app/index.html",\n  "/app/different-name.js",',
    );
    assert.notEqual(
      computeCacheName(other, dir),
      absent,
      "two DIFFERENT pending entries produced the same name — the absent marker drops the URL",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("REORDERING SHELL changes nothing — a bump with no content behind it evicts for free", () => {
  const dir = scratchShell();
  try {
    const before = computeCacheName(SW_SOURCE, dir);
    // \r?\n because the checkout is CRLF on Windows and LF on the VM.
    const swapped = SW_SOURCE.replace(
      /( {2}"\/app\/app\.css",)(\r?\n)( {2}"\/app\/app\.js",)/,
      "$3$2$1",
    );
    assert.notEqual(swapped, SW_SOURCE, "the reorder did not apply");
    assert.equal(computeCacheName(swapped, dir), before);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("line endings do not change the name — Windows and the Linux VM must agree", () => {
  // This repo checks out CRLF on Windows and LF on the VM that deploys it.
  // Hashing raw bytes would make the check fail on the VM every time, for
  // nothing, and a gate that cries wolf is a gate that gets switched off.
  const dir = scratchShell();
  try {
    const before = computeCacheName(SW_SOURCE, dir);
    let touched = 0;
    for (const url of shellEntries(SW_SOURCE)) {
      const path = shellUrlToPath(url, dir);
      if (!existsSync(path) || !path.endsWith(".js")) continue;
      const text = readFileSync(path, "utf8");
      writeFileSync(path, text.replace(/\r\n/g, "\n").replace(/\n/g, "\r\n"));
      touched++;
    }
    assert.ok(touched > 5, `expected several text entries to re-line, did ${touched}`);
    assert.equal(computeCacheName(SW_SOURCE, dir), before, "a line-ending flip must not rename the cache");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("a binary entry is hashed as BYTES, not through a text decoder", () => {
  // Two icons that differ by one byte but decode to the SAME string: 0xff and
  // 0xfe are never valid UTF-8, so a text round-trip turns both into U+FFFD and
  // the two files become indistinguishable. Hashing bytes tells them apart;
  // hashing a decoded string does not — and an icon change would then ship under
  // the old cache name.
  const dir = scratchShell();
  try {
    const icon = shellUrlToPath("/app/icons/icon-192.png", dir);
    const a = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0xff, 0x01]);
    const b = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0xfe, 0x01]);
    assert.notEqual(Buffer.compare(a, b), 0, "the two icons must differ as bytes");
    assert.equal(a.toString("utf8"), b.toString("utf8"), "and be identical as text");

    writeFileSync(icon, a);
    const nameA = computeCacheName(SW_SOURCE, dir);
    writeFileSync(icon, b);
    const nameB = computeCacheName(SW_SOURCE, dir);
    assert.notEqual(nameA, nameB, "two different icons produced ONE cache name — the digest decodes text");

    // And an ordinary icon edit still moves the name, so the above is not the
    // only thing keeping this honest.
    writeFileSync(icon, Buffer.concat([b, Buffer.from([0x7a])]));
    assert.notEqual(computeCacheName(SW_SOURCE, dir), nameB);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

// ---- 3. The gate is wired where the other gates run ------------------------

test("the CLI the Makefile invokes agrees with the module this file imports", () => {
  // The gate the Makefile and verify-phase27.sh run is `shell-cache.mjs --check`,
  // and its EXIT CODE is the whole contract. Everything above tests the module;
  // this runs the command, so the two cannot drift into disagreeing about what
  // "up to date" means.
  const r = spawnSync(process.execPath, [join(REPO_ROOT, "scripts", "shell-cache.mjs"), "--check"], {
    cwd: REPO_ROOT,
    encoding: "utf8",
  });
  assert.equal(r.status, 0, `the check exited ${r.status}:\n${r.stdout}${r.stderr}`);
  assert.ok(
    r.stdout.includes(declaredCacheName(SW_SOURCE)),
    "the command must report the name it compared against",
  );
});

test("the Makefile carries the gate next to the chat-core drift check", () => {
  const makefile = readFileSync(join(REPO_ROOT, "Makefile"), "utf8");
  assert.match(makefile, /^check-shell-cache:/m, "the Makefile must expose check-shell-cache");
  assert.match(makefile, /node scripts\/shell-cache\.mjs --check/, "and run the check mode");
  assert.match(makefile, /^shell-cache:/m, "and a way to fix it in one command");
});

test("verify-phase27.sh runs the gate before a deploy, not after one", () => {
  const verify = readFileSync(
    join(REPO_ROOT, "infrastructure", "scripts", "verify-phase27.sh"),
    "utf8",
  );
  assert.ok(
    verify.includes("scripts/shell-cache.mjs --check"),
    "the phase verifier must run the shell-cache check — it already runs the chat-core one",
  );
});

test("sw.js is not precached by itself, or the worker would serve the old worker forever", () => {
  // Guarded here as well as in test_pwa_chat.mjs because THIS file is the one
  // that would produce a self-referential hash if it ever changed.
  assert.ok(
    !shellEntries(SW_SOURCE).includes("/app/sw.js"),
    "sw.js must never be in its own SHELL",
  );
});

test("computeCacheName refuses a service worker it cannot read", () => {
  assert.throws(() => computeCacheName("const CACHE = \"x\";", APP_DIR), /SHELL/);
  assert.throws(() => computeCacheName("const SHELL = [];", APP_DIR), /EMPTY/);
  assert.throws(() => declaredCacheName("const SHELL = [];"), /CACHE/);
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
