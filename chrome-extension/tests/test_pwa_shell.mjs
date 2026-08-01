/**
 * Static-analysis contract for the PWA shell at app-site/app/ (Phase 27,
 * Plan 27-05).
 *
 * It lives in the extension's test directory rather than beside app-site
 * because that is where run_tests.mjs walks; it reads ../../app-site/app/*
 * and asserts nothing about the extension itself.
 *
 * Every assertion here guards something that fails SILENTLY in a browser:
 *   - a typo'd precache path only shows up as a mysterious install failure;
 *   - a drifted Google client id is a sign-in outage with no error in our logs;
 *   - a permission prompt on load is invisible in code review and permanent for
 *     the user who clicks "Block" (D-27-05);
 *   - an icon path that 404s makes the app silently uninstallable.
 *
 * SKIP = FAIL: nothing below is conditional on a file existing.
 */

import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, "..", "..");
const APP_DIR = join(REPO_ROOT, "app-site", "app");

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

/** Read a shell file, failing the assertion (not the process) when absent. */
function readApp(relPath) {
  const full = join(APP_DIR, relPath);
  assert.ok(existsSync(full), `app-site/app/${relPath} must exist`);
  return readFileSync(full, "utf8");
}

/**
 * Shell entries that a LATER plan in this phase creates. 27-05 precaches them
 * ahead of time (a service worker install must not need editing twice), so
 * their absence today is expected — but the list is declared here rather than
 * hidden in a loose check, and the test below fails if an entry on it has since
 * shipped. That way the exemption cannot quietly outlive its reason.
 */
const PENDING_SHELL_ENTRIES = new Set([
  // Empty since 27-07 shipped /app/push.js. Kept rather than deleted: the
  // mechanism is what stops the NEXT precache-ahead entry from becoming
  // permanent.
]);

/** Map a shell URL onto the file it must resolve to on disk. */
function shellUrlToPath(url) {
  const rel = url.replace(/^\/app\//, "");
  // A bare directory URL is served by its index document.
  return join(APP_DIR, rel === "" ? "index.html" : rel);
}

// ---- 1. index.html: manifest, scoped SW registration, no prompt ----------

test("index.html links the web manifest", () => {
  const html = readApp("index.html");
  assert.match(
    html,
    /<link[^>]+rel=["']manifest["'][^>]+href=["']\/app\/manifest\.webmanifest["']/,
    "index.html must link /app/manifest.webmanifest or the app is not installable",
  );
});

test("index.html registers the service worker at scope /app/", () => {
  const html = readApp("index.html");
  assert.match(html, /serviceWorker\.register/, "no service worker registration found");
  assert.match(
    html,
    /serviceWorker\.register\(\s*["']\/app\/sw\.js["']\s*,\s*\{\s*scope:\s*["']\/app\/["']/,
    "the worker must be registered for /app/sw.js at scope /app/ — a wider scope would let it intercept /account/*",
  );
  assert.match(
    html,
    /["']serviceWorker["']\s+in\s+navigator/,
    "registration must be guarded so an unsupported browser does not throw on load",
  );
});

test("the SW is registered exactly once across index.html and app.js", () => {
  const total = ["index.html", "app.js"]
    .map((f) => (readApp(f).match(/serviceWorker\.register/g) || []).length)
    .reduce((a, b) => a + b, 0);
  assert.equal(total, 1, "exactly one registration call must exist across the shell");
});

test("no shell file can raise a permission prompt on load (D-27-05)", () => {
  for (const file of ["index.html", "app.js", "auth.js"]) {
    const src = readApp(file);
    assert.ok(
      !/requestPermission/.test(src),
      `${file} must not reference the notification permission request — 27-07 owns the one click-gated call site`,
    );
    assert.ok(
      !/pushManager/.test(src),
      `${file} must not touch pushManager — subscribing implies a prompt`,
    );
  }
});

// ---- 2. auth.js: the /join/ flow, reused verbatim ------------------------

test("auth.js uses the canonical app-site storage keys", () => {
  const auth = readApp("auth.js");
  for (const key of ["xbt_token", "user_sub"]) {
    const hits = (auth.match(new RegExp(key, "g")) || []).length;
    assert.equal(
      hits,
      1,
      `auth.js must name "${key}" exactly once (the constant) — found ${hits}`,
    );
  }
});

test("auth.js mints through POST /v1/me/api-token", () => {
  const auth = readApp("auth.js");
  assert.ok(
    auth.includes("/v1/me/api-token"),
    "auth.js must exchange the Google credential at /v1/me/api-token, like /join/ does",
  );
});

test("auth.js's Google client id matches app-site/join/index.html", () => {
  const joinPage = readFileSync(
    join(REPO_ROOT, "app-site", "join", "index.html"),
    "utf8",
  );
  const auth = readApp("auth.js");
  const idPattern = /[0-9]+-[a-z0-9]+\.apps\.googleusercontent\.com/;
  const joinId = (joinPage.match(idPattern) || [])[0];
  assert.ok(joinId, "could not find a Google client id in app-site/join/index.html");
  assert.ok(
    auth.includes(joinId),
    `auth.js must use the SAME Google client id as /join/ (${joinId}); a divergence is a silent sign-in outage`,
  );
});

test("auth.js exports the surface the shell binds to", () => {
  const auth = readApp("auth.js");
  for (const name of ["getToken", "signOut", "mountSignIn"]) {
    assert.match(
      auth,
      new RegExp(`export\\s+(async\\s+)?function\\s+${name}\\b`),
      `auth.js must export ${name}`,
    );
  }
});

// ---- 3. manifest: parses, and every icon resolves ------------------------

test("manifest.webmanifest parses and is scoped to /app/", () => {
  const manifest = JSON.parse(readApp("manifest.webmanifest"));
  assert.equal(manifest.start_url, "/app/");
  assert.equal(manifest.scope, "/app/");
  assert.ok(manifest.icons.length >= 3, "need 192 + 512 + maskable");
});

test("every manifest icon names a file that exists on disk", () => {
  const manifest = JSON.parse(readApp("manifest.webmanifest"));
  for (const icon of manifest.icons) {
    assert.ok(
      icon.src.startsWith("/app/"),
      `icon src ${icon.src} must be an absolute /app/ path so it resolves from /app/ and /app/index.html alike`,
    );
    assert.ok(
      existsSync(shellUrlToPath(icon.src)),
      `manifest icon ${icon.src} does not exist on disk`,
    );
  }
});

test("a maskable icon is declared", () => {
  const manifest = JSON.parse(readApp("manifest.webmanifest"));
  assert.ok(
    manifest.icons.some((i) => String(i.purpose || "").includes("maskable")),
    "without a maskable icon Android draws the app icon inside a white plate",
  );
});

// ---- 4. sw.js: precache paths resolve, guards present --------------------

function shellEntries() {
  const sw = readApp("sw.js");
  const block = sw.match(/const SHELL = \[([\s\S]*?)\];/);
  assert.ok(block, "sw.js must declare a SHELL array");
  return [...block[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]);
}

test("every shipped SHELL entry names a file that exists on disk", () => {
  for (const url of shellEntries()) {
    if (PENDING_SHELL_ENTRIES.has(url)) continue;
    assert.ok(
      existsSync(shellUrlToPath(url)),
      `sw.js precaches ${url}, which does not exist — a typo here fails the install with no visible error`,
    );
  }
});

test("the pending-entry exemption list has not gone stale", () => {
  const entries = new Set(shellEntries());
  for (const url of PENDING_SHELL_ENTRIES) {
    assert.ok(
      entries.has(url),
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
  assert.ok(entries.has("/app/push.js"), "/app/push.js must still be precached");
  assert.ok(
    !PENDING_SHELL_ENTRIES.has("/app/push.js"),
    "/app/push.js shipped in 27-07 — it must be checked, not exempted",
  );
});

test("sw.js cannot cache an authenticated response", () => {
  const sw = readApp("sw.js");
  // Comments are stripped first: a guard that only exists in prose is not a guard.
  const code = sw
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");
  for (const guard of [
    'req.method !== "GET"',
    "url.origin !== self.location.origin",
    'url.pathname.startsWith("/v1/")',
    'req.headers.has("Authorization")',
  ]) {
    assert.ok(code.includes(guard), `sw.js is missing the guard: ${guard}`);
  }
  const respondAt = code.indexOf("respondWith");
  assert.ok(respondAt > 0, "sw.js must call respondWith for the shell");
  assert.equal(
    (code.match(/respondWith/g) || []).length,
    1,
    "a second respondWith would need its own four guards",
  );
  for (const guard of [
    'req.method !== "GET"',
    "url.origin !== self.location.origin",
    'url.pathname.startsWith("/v1/")',
    'req.headers.has("Authorization")',
  ]) {
    assert.ok(
      code.indexOf(guard) < respondAt,
      `guard "${guard}" must run BEFORE respondWith or it does not gate anything`,
    );
  }
});

test("sw.js has the push hooks and cannot prompt", () => {
  const sw = readApp("sw.js");
  assert.equal((sw.match(/addEventListener\("push"/g) || []).length, 1);
  assert.equal((sw.match(/addEventListener\("notificationclick"/g) || []).length, 1);
  assert.ok(
    !/requestPermission/.test(sw),
    "a worker cannot ask for notification access, and must not appear to try",
  );
});

// ---- 5. English-only product strings ------------------------------------

test("english-only: no accented Latin chars in index.html / app.js / auth.js", () => {
  for (const file of ["index.html", "app.js", "auth.js"]) {
    const hits = readApp(file).match(/[À-ÿ]/g) || [];
    assert.equal(
      hits.length,
      0,
      `${file} has ${hits.length} accented char(s) ${JSON.stringify([
        ...new Set(hits),
      ])} — product strings must be English`,
    );
  }
});

// ---- 6. No secret may reach the client ----------------------------------

test("no VAPID private key material anywhere in app-site/app", () => {
  for (const file of ["index.html", "app.js", "auth.js", "sw.js", "platform_web.js"]) {
    const src = readApp(file);
    assert.ok(
      !/VAPID_PRIVATE|vapid_private|PRIVATE KEY/.test(src),
      `${file} must never carry private key material — the client only ever gets the public key`,
    );
  }
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
