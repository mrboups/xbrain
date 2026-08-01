#!/usr/bin/env node
/**
 * Copy packages/chat-core/*.js into every surface that consumes it (Phase 27,
 * D-27-04), and — with `--check` — fail when a copy has drifted.
 *
 *   node scripts/sync-chat-core.mjs           write byte-identical copies
 *   node scripts/sync-chat-core.mjs --check   write nothing, exit 1 on drift
 *
 * The copies are byte-identical on purpose: it makes the drift check a raw
 * buffer comparison with no header/transform to reason about, and it means a
 * stack trace in the extension points at a line number that also exists in the
 * source. README.md is NOT copied — it documents the package, not the runtime.
 *
 * Dependency-free: plain node, no package.json, no install step.
 */

import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
} from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, "..");

const SOURCE_DIR = join(REPO_ROOT, "packages", "chat-core");
const TARGETS = [
  join("chrome-extension", "chat_core"),
  join("app-site", "app", "chat_core"),
];

const checkOnly = process.argv.includes("--check");

/**
 * Modules both surfaces are known to import.
 *
 * The file list below is discovered from disk, which is what keeps a NEW module
 * covered without touching this script. The failure that discovery cannot see is
 * the opposite one: a module deleted from the package stops being copied, the
 * orphan sweep removes it from both targets, and the check goes green while two
 * surfaces sit on a broken import. Naming the modules makes that a red gate.
 */
const REQUIRED_MODULES = [
  "api.js",
  "chat_stream.js",
  "nudge_open.js",
  "platform.js",
  "publication.js",
  "realtime.js",
  "render.js",
  "team_rail.js",
  "theme.js",
];

/** Every runtime module in the package, sorted for stable reporting. */
function sourceFiles() {
  if (!existsSync(SOURCE_DIR)) {
    console.error(`FATAL: source directory missing: ${SOURCE_DIR}`);
    process.exit(1);
  }
  const found = readdirSync(SOURCE_DIR)
    .filter((f) => f.endsWith(".js"))
    .sort();
  const absent = REQUIRED_MODULES.filter((m) => !found.includes(m));
  if (absent.length) {
    console.error(
      `FATAL: packages/chat-core is missing ${absent.join(", ")} — ` +
        "both surfaces import it. Remove it from REQUIRED_MODULES only together " +
        "with its import sites.",
    );
    process.exit(1);
  }
  return found;
}

/** `.js` files currently sitting in a target dir (may include stale copies). */
function targetFiles(absDir) {
  if (!existsSync(absDir)) return [];
  return readdirSync(absDir)
    .filter((f) => f.endsWith(".js"))
    .sort();
}

const files = sourceFiles();
if (files.length === 0) {
  console.error("FATAL: packages/chat-core contains no .js modules");
  process.exit(1);
}

const problems = [];
let removed = 0;

for (const target of TARGETS) {
  const absDir = join(REPO_ROOT, target);
  if (!checkOnly) mkdirSync(absDir, { recursive: true });

  // 1. Every source module must exist in the target, byte-for-byte.
  for (const name of files) {
    const src = join(SOURCE_DIR, name);
    const dst = join(absDir, name);

    if (checkOnly) {
      if (!existsSync(dst)) {
        problems.push(`MISSING  ${target}/${name}`);
        continue;
      }
      if (Buffer.compare(readFileSync(src), readFileSync(dst)) !== 0) {
        problems.push(`DRIFTED  ${target}/${name}`);
      }
      continue;
    }

    copyFileSync(src, dst);
  }

  // 2. No orphans: a module deleted from the source must not survive in a copy,
  //    or a surface would keep importing code the package no longer defines.
  for (const name of targetFiles(absDir)) {
    if (files.includes(name)) continue;
    if (checkOnly) {
      problems.push(`ORPHAN   ${target}/${name} (not in packages/chat-core)`);
    } else {
      rmSync(join(absDir, name));
      console.log(`removed stale copy ${target}/${name}`);
      removed++;
    }
  }
}

if (checkOnly) {
  if (problems.length) {
    console.error("chat-core drift detected:");
    for (const p of problems) console.error(`  ${p}`);
    console.error(
      "\npackages/chat-core is the ONLY editable copy — run `make sync-chat-core`.",
    );
    process.exit(1);
  }
  console.log(
    `chat-core in sync: ${files.length} file(s) × ${TARGETS.length} target(s)`,
  );
  process.exit(0);
}

console.log(`synced ${files.length} file(s) -> ${TARGETS.length} target(s)`);
if (removed) console.log(`removed ${removed} stale copy/copies`);
