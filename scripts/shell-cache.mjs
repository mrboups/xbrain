#!/usr/bin/env node
/**
 * Derive the PWA's shell-cache name from the CONTENT it precaches.
 *
 *   node scripts/shell-cache.mjs           rewrite CACHE in sw.js
 *   node scripts/shell-cache.mjs --check   write nothing, exit 1 when it is stale
 *
 * WHY THIS EXISTS. `CACHE` in app-site/app/sw.js used to be hand-bumped, and it
 * was missed three times in three days. The last miss was the instructive one:
 * two agents each reserved `v10` while working in separate worktrees, and only
 * the merge revealed it. A hand-bumped constant cannot be RESERVED — there is no
 * step at which anybody finds out that the number they picked is taken.
 *
 * And every miss looks the same from the outside. The shell is cache-first, so a
 * returning reader gets whatever files were installed under the old name: the new
 * render.js importing a module the cache never fetched, the old chat talking to
 * the new API. Both present as a broken feature rather than a caching problem,
 * which is why each one cost hours.
 *
 * A DERIVED NAME REMOVES THE STEP instead of guarding it. The name is a hash of
 * every precached file's bytes, so changing any of them changes it, and changing
 * none of them does not. There is nothing left to forget, nothing to reserve, and
 * two agents editing different files in different worktrees produce two different
 * names that merge into a third — correctly.
 *
 * WHAT IS HASHED, exactly: every URL in `SHELL`, paired with the bytes of the
 * file it resolves to. The URL is in the digest as well as the content, so
 * ADDING an entry changes the name even when the file it points at is already
 * hashed under another URL. sw.js itself is deliberately NOT hashed — it is
 * never precached (a cache-first worker that cached itself would serve the old
 * worker forever), and a browser already byte-compares the worker on every
 * update check, so a change to this file ships on its own terms.
 *
 * LINE ENDINGS ARE NORMALISED for text files, and that matters more than it
 * looks. This repo checks out CRLF on Windows and LF on the Linux VM that
 * deploys it, so hashing raw bytes would give the two machines different names
 * for identical content — and the check below would fail on the VM, every time,
 * for nothing. Binary entries (the icons) are hashed as they are.
 *
 * Dependency-free: node's own crypto, no package.json, no install step.
 */

import { createHash } from "node:crypto";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, "..");
const APP_DIR = join(REPO_ROOT, "app-site", "app");
const SW_PATH = join(APP_DIR, "sw.js");

/** The prefix every generated name carries, so an old hand-bumped one is obvious. */
export const CACHE_PREFIX = "xb-app-shell-";

/**
 * How many hex characters of the digest end up in the name.
 *
 * 12 is 48 bits: far past any accidental collision across a project's lifetime,
 * and short enough that the whole name still fits in a devtools column.
 */
export const CACHE_HASH_LENGTH = 12;

/** Extensions whose bytes are text, and therefore line-ending sensitive. */
const TEXT_EXTENSIONS = [".js", ".css", ".html", ".json", ".webmanifest", ".svg", ".txt", ".map"];

/** The `const CACHE = "..."` declaration, as it is read and as it is rewritten. */
const CACHE_DECLARATION = /const CACHE = "([^"]*)";/;

/** Read sw.js. Exported so the check and the writer cannot disagree about it. */
export function readServiceWorker(swPath = SW_PATH) {
  if (!existsSync(swPath)) {
    throw new Error(`service worker not found: ${swPath}`);
  }
  return readFileSync(swPath, "utf8");
}

/**
 * The precache list, in the order sw.js declares it.
 *
 * Parsed out of the source rather than imported: sw.js is a worker script that
 * names `self` at load, so importing it in node would throw before the array
 * could be read.
 *
 * @param {string} source sw.js
 * @returns {Array<string>}
 */
export function shellEntries(source) {
  const block = source.match(/const SHELL = \[([\s\S]*?)\];/);
  if (!block) throw new Error("sw.js declares no SHELL array");
  return [...block[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]);
}

/** Map a shell URL onto the file that answers it. A bare directory is its index. */
export function shellUrlToPath(url, appDir = APP_DIR) {
  const rel = url.replace(/^\/app\//, "");
  return join(appDir, rel === "" ? "index.html" : rel);
}

/**
 * One entry's contribution to the digest.
 *
 * A MISSING file hashes as a stable marker rather than throwing. Entries are
 * legitimately precached ahead of the plan that ships them, and the name still
 * has to change on the day one appears — which it does, because the marker is
 * not the file.
 *
 * The marker KEEPS THE URL, so the rule stays one rule: the name is a function
 * of the SHELL list and the bytes behind it, with no case in which a URL quietly
 * stops counting. Renaming a not-yet-shipped entry therefore renames the cache,
 * which costs one re-download of a shell nobody had a different version of.
 *
 * The URL and the digest are joined by a NUL, spelled as an escape so it is
 * visible in a diff. A separator that cannot occur in either half is what stops
 * two different (url, digest) pairs from concatenating into the same line.
 */
function digestEntry(url, appDir) {
  const path = shellUrlToPath(url, appDir);
  if (!existsSync(path)) return `${url}\u0000<absent>`;
  const isText = TEXT_EXTENSIONS.some((ext) => path.toLowerCase().endsWith(ext));
  const bytes = isText
    ? Buffer.from(readFileSync(path, "utf8").replace(/\r\n/g, "\n"), "utf8")
    : readFileSync(path);
  return `${url}\u0000${createHash("sha256").update(bytes).digest("hex")}`;
}

/**
 * The cache name this shell's content deserves.
 *
 * Entries are SORTED before hashing: reordering the SHELL array changes nothing
 * about what a reader is served, so it must not change the name either — a bump
 * with no content behind it evicts everybody's shell for nothing.
 *
 * @param {string} source sw.js
 * @param {string} [appDir]
 * @returns {string}
 */
export function computeCacheName(source, appDir = APP_DIR) {
  const entries = shellEntries(source);
  if (entries.length === 0) throw new Error("sw.js declares an EMPTY SHELL array");
  const digest = createHash("sha256");
  for (const line of entries.map((url) => digestEntry(url, appDir)).sort()) {
    digest.update(line);
    digest.update("\n");
  }
  return `${CACHE_PREFIX}${digest.digest("hex").slice(0, CACHE_HASH_LENGTH)}`;
}

/** The name sw.js currently declares. */
export function declaredCacheName(source) {
  const found = source.match(CACHE_DECLARATION);
  if (!found) throw new Error('sw.js declares no `const CACHE = "..."`');
  return found[1];
}

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

/** True when this file was run, rather than imported by the test. */
const RUN_DIRECTLY = process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url));

if (RUN_DIRECTLY) {
  const checkOnly = process.argv.includes("--check");
  let source;
  try {
    source = readServiceWorker();
  } catch (e) {
    console.error(`FATAL: ${e.message}`);
    process.exit(1);
  }

  let expected;
  let declared;
  try {
    expected = computeCacheName(source);
    declared = declaredCacheName(source);
  } catch (e) {
    console.error(`FATAL: ${e.message}`);
    process.exit(1);
  }

  if (declared === expected) {
    console.log(`shell cache up to date: ${expected} (${shellEntries(source).length} entries)`);
    process.exit(0);
  }

  if (checkOnly) {
    console.error("THE PRECACHED SHELL CHANGED AND THE CACHE NAME DID NOT.");
    console.error(`  declared in sw.js : ${declared}`);
    console.error(`  content says      : ${expected}`);
    console.error("");
    console.error(
      "A returning reader would be served the files installed under the old name:\n" +
        "  a new module the cache never fetched, an old chat against a new API.\n" +
        "Both look like a broken fix rather than a caching problem.\n" +
        "\n" +
        "Run `make shell-cache` (node scripts/shell-cache.mjs) and commit sw.js.",
    );
    process.exit(1);
  }

  writeFileSync(SW_PATH, source.replace(CACHE_DECLARATION, `const CACHE = "${expected}";`));
  console.log(`shell cache: ${declared} -> ${expected}`);
}
