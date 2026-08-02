/**
 * Importing a past conversation into a team brain (app-site/app/import.js,
 * index.html, app.css, sw.js, manifest.webmanifest).
 *
 * It lives in the extension's test directory because that is where
 * run_tests.mjs walks; it reads ../../app-site/app/.
 *
 * WHAT THIS GUARDS. Every assertion below stands for a failure that is silent
 * in a browser and expensive in a team brain:
 *
 *   - a format guessed wrong sends a transcript to the wrong parser and comes
 *     back as a 422 nobody can act on. So detection has to answer "I do not
 *     know" out loud, and be overridable;
 *   - a size check that runs AFTER the read is not a size check. A full ChatGPT
 *     export is tens of megabytes and a phone kills the tab reading one, with
 *     no message at all;
 *   - a result that shows only what was imported makes "nothing happened" and
 *     "you already imported this" the same blank screen, and the person sends
 *     it again;
 *   - a share that writes into a team with no confirmation is a share that
 *     writes into the WRONG team;
 *   - a share navigation answered from the shell cache shows the previous page
 *     with the shared text nowhere.
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
const HTML = html.replace(/<!--[\s\S]*?-->/g, "");
const css = readFileSync(join(APP_DIR, "app.css"), "utf8").replace(
  /\/\*[\s\S]*?\*\//g,
  "",
);
const importSrc = readFileSync(join(APP_DIR, "import.js"), "utf8");
const appJs = readFileSync(join(APP_DIR, "app.js"), "utf8");
const sw = readFileSync(join(APP_DIR, "sw.js"), "utf8");

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

/** Comments out, so prose is never mistaken for code. */
function stripComments(src) {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

/** Body of the brace block opening at/after `from`. Handles nesting. */
function braceBlock(src, from) {
  const open = src.indexOf("{", from);
  if (open === -1) return null;
  let depth = 0;
  for (let i = open; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}" && --depth === 0) return src.slice(open + 1, i);
  }
  return null;
}

/** Body of the first rule whose selector is exactly `sel`. */
function selectorBlock(src, sel) {
  const esc = sel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/ +/g, "\\s+");
  const m = new RegExp(`(^|[};])\\s*${esc}\\s*\\{`, "m").exec(src);
  return m ? braceBlock(src, m.index) : null;
}

/** `prop: value` pairs declared directly in a block body. */
function props(block) {
  const out = {};
  for (const chunk of (block || "").split(";")) {
    const m = /^\s*([a-z-]+)\s*:\s*(.+?)\s*$/is.exec(chunk);
    if (m) out[m[1].trim()] = m[2].replace(/\s+/g, " ").trim();
  }
  return out;
}

// The module under test. Imported dynamically so a syntax error is reported as
// one failure with a stack, not as an unhandled crash of the whole file.
let mod = null;
let importError = null;
try {
  mod = await import(pathToFileURL(join(APP_DIR, "import.js")).href);
} catch (e) {
  importError = e;
}

test("import.js loads in a plain module context", () => {
  assert.equal(
    importError,
    null,
    `app-site/app/import.js failed to import: ${importError && importError.message}`,
  );
  for (const name of [
    "detectTranscriptFormat",
    "importSizeRefusal",
    "describeImportResult",
    "mountImport",
    "isImportOpen",
    "hideImport",
  ]) {
    assert.equal(typeof mod[name], "function", `import.js must export ${name}`);
  }
});

// ---- 1. Format detection, including the honest "I do not know" -----------

const CLAUDE_CODE_JSONL = [
  '{"type":"user","uuid":"a1","sessionId":"s1","message":{"role":"user","content":"hello"}}',
  '{"type":"assistant","uuid":"a2","parentUuid":"a1","message":{"role":"assistant","content":"hi"}}',
  '{"type":"user","uuid":"a3","parentUuid":"a2","message":{"role":"user","content":"thanks"}}',
].join("\n");

const CHATGPT_EXPORT = JSON.stringify([
  {
    title: "A conversation",
    create_time: 1,
    mapping: {
      root: { id: "root", message: null, parent: null, children: ["m1"] },
      m1: { id: "m1", message: { author: { role: "user" } }, parent: "root", children: [] },
    },
  },
]);

test("a JSONL session reads as Claude Code", () => {
  assert.equal(mod.detectTranscriptFormat(CLAUDE_CODE_JSONL), "claude-code");
  // Trailing newline, CRLF and a blank line between entries are all normal on
  // a file that came off a disk.
  assert.equal(
    mod.detectTranscriptFormat(`${CLAUDE_CODE_JSONL.replace(/\n/g, "\r\n")}\r\n`),
    "claude-code",
  );
  assert.equal(mod.detectTranscriptFormat(`\n\n${CLAUDE_CODE_JSONL}\n`), "claude-code");
});

test("a conversations.json with a mapping reads as ChatGPT", () => {
  assert.equal(mod.detectTranscriptFormat(CHATGPT_EXPORT), "chatgpt");
  // Pretty-printed is the shape people actually open and re-save.
  assert.equal(
    mod.detectTranscriptFormat(JSON.stringify(JSON.parse(CHATGPT_EXPORT), null, 2)),
    "chatgpt",
  );
  // A single conversation, not the whole export.
  assert.equal(
    mod.detectTranscriptFormat(JSON.stringify(JSON.parse(CHATGPT_EXPORT)[0])),
    "chatgpt",
  );
});

test("JSONL wins over the word 'mapping' quoted inside a tool result", () => {
  // A Claude Code entry can contain ANY text, including the key that identifies
  // the other format. Line structure is the stronger signal and is checked first.
  const withTrap =
    '{"type":"assistant","uuid":"b1","message":{"content":"the export has a \\"mapping\\": key"}}\n' +
    '{"type":"user","uuid":"b2","parentUuid":"b1","message":{"content":"right"}}';
  assert.equal(mod.detectTranscriptFormat(withTrap), "claude-code");
});

test("an ambiguous file is reported as unknown, not guessed", () => {
  for (const [label, sample] of [
    ["prose", "User: hello\nAssistant: hi there\nUser: thanks"],
    ["a JSON array of plain objects", '[\n  {"a": 1},\n  {"b": 2}\n]'],
    ["a compact array of plain objects", '[{"a":1},{"b":2}]'],
    ["half-broken JSONL", '{"type":"user"}\nnot json at all\n{"type":"user"}'],
    ["a bare number", "42"],
    ["nothing", "   \n  "],
    ["a link to somewhere else", "https://example.test/some/page"],
  ]) {
    assert.equal(
      mod.detectTranscriptFormat(sample),
      null,
      `${label} must read as unknown - a confident wrong guess sends it to the wrong parser`,
    );
  }
  assert.equal(mod.detectTranscriptFormat(undefined), null);
  assert.equal(mod.detectTranscriptFormat(null), null);
});

test("a bare link is recognised as a link, and refused before it is sent", () => {
  // This is what an Android share from the ChatGPT app usually delivers, and
  // the server answers it with a 400 ON PURPOSE: a service that fetches a
  // user-supplied URL can be pointed at the cloud metadata address, which on
  // the deployment VM hands out service-account credentials. So it is refused
  // here too, one moment earlier and with the same answer.
  for (const url of [
    "https://chatgpt.com/share/68000000-0000-4000-8000-000000000000",
    "https://chat.openai.com/share/abc123",
    "http://example.test/anything",
  ]) {
    assert.equal(mod.isBareShareLink(url), true, url);
    assert.equal(
      mod.detectTranscriptFormat(url),
      null,
      "a link has no format - it is not a transcript at all",
    );
  }
  for (const notALink of [
    "look at https://chatgpt.com/share/x",
    "just some text",
    '{"type":"user"}',
    "",
    "   ",
  ]) {
    assert.equal(
      mod.isBareShareLink(notALink),
      false,
      `${JSON.stringify(notALink)} is content, not a bare link - refusing it would block a real import`,
    );
  }
  const code = stripComments(importSrc);
  assert.ok(
    /isBareShareLink\(armed\.content\)/.test(code),
    "the refusal must be wired to what is armed, not merely exported",
  );
  assert.ok(
    /sendBtn\.disabled = !armed\.content \|\| bareLink/.test(code),
    "and it must disable Import - a send we know will 400 is a round trip that teaches nothing",
  );
  assert.ok(
    !/fetch\(|XMLHttpRequest/.test(code),
    "and the client must not fetch the link either - the same reasoning applies on this side",
  );
});

// ---- 2. The size gate runs BEFORE the read ------------------------------

test("an oversize transcript is refused, with a number and a way forward", () => {
  const overCap = 60 * 1024 * 1024;
  const refusal = mod.importSizeRefusal(overCap, "conversations.json");
  assert.ok(refusal, "60 MB must be refused");
  assert.match(refusal, /conversations\.json/, "name the file - a person has several");
  assert.match(refusal, /\b60 MB\b/, "say how big it actually is");
  assert.match(refusal, /\b25 MB\b/, "and what the limit is - the server's own 413 threshold");
  assert.match(
    refusal,
    /single conversation/i,
    "a refusal with no way forward is a dead end - a whole export is not the only option",
  );
  assert.equal(mod.importSizeRefusal(1024, "small.jsonl"), null, "1 KB is fine");
  assert.equal(
    mod.importSizeRefusal(24 * 1024 * 1024, "big.json"),
    null,
    "under the server's ceiling must not be refused here - a stricter client limit rejects files the server would take",
  );
  assert.equal(mod.importSizeRefusal(0, "empty.jsonl") === null, false, "0 bytes is not importable");
});

test("the refusal is decided on file.size, never after a read (source check)", () => {
  const code = stripComments(importSrc);
  const gate = code.indexOf("importSizeRefusal(file.size");
  assert.ok(
    gate > 0,
    "takeFile must call importSizeRefusal(file.size, ...) - a check on the string it just read is not a check",
  );
  const read = code.indexOf("await readTextFile(file)");
  assert.ok(read > 0, "the file is read through readTextFile");
  assert.ok(
    gate < read,
    "the size gate must run BEFORE the read; after it, the tab is already dead on a phone",
  );
  assert.ok(
    !/readAsBinaryString|FileReaderSync|\.arrayBuffer\(\)/.test(code),
    "nothing here may read a file synchronously or into a buffer - it must not block the page",
  );
});

test("the file is never poured into the paste box", () => {
  // A ten-megabyte string in a textarea is the freeze this path exists to
  // avoid. The source line names it instead.
  const code = stripComments(importSrc);
  const takeFile = code.slice(code.indexOf("async function takeFile"));
  const body = takeFile.slice(0, takeFile.indexOf("\nasync function fillTeamChooser"));
  assert.ok(
    /paste\.value = ""/.test(body),
    "picking a file must CLEAR the paste box, not fill it",
  );
  assert.ok(
    !/paste\.value = text/.test(body),
    "the file's contents must never be assigned into the textarea",
  );
});

// ---- 3. The result says both numbers ------------------------------------

test("the result distinguishes imported, skipped, nothing, and no answer", () => {
  assert.equal(
    mod.describeImportResult({ imported: 1, turns: 42, reported: true }),
    "Imported 1 conversation, 42 turns.",
    "both counts - one conversation of four hundred turns and one of two are the same sentence otherwise",
  );
  assert.match(
    mod.describeImportResult({ imported: 1, turns: 1, reported: true }),
    /1 conversation, 1 turn\./,
    "singular, so no sentence has to hedge with (s)",
  );
  const both = mod.describeImportResult({ imported: 2, duplicates: 3, turns: 80, reported: true });
  assert.match(both, /Imported 2 conversations/);
  assert.match(
    both,
    /3 conversations skipped as already imported/,
    "the skipped count is the evidence a re-import did not double the brain",
  );

  const allDupes = mod.describeImportResult({ imported: 0, duplicates: 9, reported: true });
  assert.match(allDupes, /already/i, "'you already imported this' must not read as a failure");
  assert.match(allDupes, /\b9 conversations\b/);

  const empty = mod.describeImportResult({ imported: 0, duplicates: 0, reported: true });
  assert.match(empty, /Nothing was imported/i);

  const silent = mod.describeImportResult({ imported: null, duplicates: null, reported: false });
  assert.match(
    silent,
    /reported no counts/i,
    "a server that said nothing countable must not be reported as zero - they are different facts",
  );
  assert.notEqual(silent, empty);
});

test("silent data loss is said out loud", () => {
  const dropped = mod.describeImportResult({ imported: 2, overLimit: 1, turns: 30, reported: true });
  assert.match(
    dropped,
    /too long and was not imported/i,
    "a conversation the server refused for length must be reported, or two of three imports look like three",
  );
  const cut = mod.describeImportResult({ imported: 5, turns: 200, truncated: true, reported: true });
  assert.match(
    cut,
    /only the start of it was taken/i,
    "a file cut short is missing history nobody would know to look for",
  );
});

test("a duplicate verdict offers the way through", () => {
  // An import interrupted partway through reports as a duplicate for ever
  // after. Without force, that conversation is stuck half-imported.
  assert.ok(HTML.includes('id="btn-import-force"'), "index.html must carry the re-import control");
  const forceBtn = /<button[^>]*id="btn-import-force"[\s\S]*?<\/button>/.exec(HTML)[0];
  assert.match(forceBtn, /\bhidden\b/, "it must be hidden until there is a duplicate to act on");
  assert.match(
    forceBtn.replace(/<[^>]*>/g, " "),
    /Re-import anyway/i,
    "the label must say what it does",
  );
  const code = stripComments(importSrc);
  const send = braceBlock(code, code.indexOf("async function sendImport"));
  assert.ok(
    /isDuplicateImport\(summary\)/.test(send),
    "the verdict comes from the shared reader, not from re-deriving it here",
  );
  assert.ok(
    /forceBtn\.hidden = false/.test(send),
    "the control appears only after a duplicate verdict",
  );
  assert.ok(
    /force: Boolean\(force\)/.test(send),
    "and the re-send must actually carry force",
  );
  assert.ok(
    /lastSent = payload/.test(send),
    "what was sent has to survive the answer, or there is nothing to retry",
  );
});

test("every server rejection gets its own sentence", () => {
  const code = stripComments(importSrc);
  const fn = braceBlock(code, code.indexOf("async function importError"));
  assert.ok(fn, "import.js must map statuses to copy");
  for (const status of ["400", "401", "403", "404", "413"]) {
    assert.ok(
      fn.includes(`=== ${status}`),
      `HTTP ${status} means something specific to the person holding the file and must not fall into the generic line`,
    );
  }
  assert.ok(
    /serverDetail\(res\)/.test(fn),
    "a 400 covers an unreadable file, a bare link and an empty body - only the server knows which, so its own sentence is preferred",
  );
  const detail = braceBlock(code, code.indexOf("async function serverDetail"));
  assert.ok(
    /body\.detail/.test(detail) && /Array\.isArray\(detail\)/.test(detail),
    "it must read both the plain string and the validation-error list shapes",
  );
});

// ---- 4. One place talks to the server -----------------------------------

test("import.js names no endpoint of its own", () => {
  const code = stripComments(importSrc);
  const paths = [...code.matchAll(/\/v1\/[A-Za-z0-9_/-]+/g)].map((m) => m[0]);
  assert.deepEqual(
    paths,
    [],
    `import.js spells an API path (${paths.join(", ")}) - the route, the header and the body live in chat_core/api.js so a rename is one edit`,
  );
  assert.ok(
    !code.includes("://"),
    "import.js must carry no origin literal - the base comes from auth.js",
  );
  assert.ok(
    /importTranscriptRaw\(/.test(code),
    "the send must go through the shared client's one import function",
  );
});

test("the client does not parse the transcript it is sending", () => {
  const code = stripComments(importSrc);
  // JSON.parse appears only inside detection, which reads at most five short
  // head lines to guess a format. Parsing the whole document would be a second
  // implementation of the server's parsers, drifting from the day it shipped.
  const parses = (code.match(/JSON\.parse\(/g) || []).length;
  assert.ok(
    parses <= 2,
    `import.js calls JSON.parse ${parses} times - detection needs one or two, a parser needs many`,
  );
  assert.ok(
    !/JSON\.parse\(\s*(?:armed\.content|text)\s*\)/.test(code),
    "the whole transcript must never be parsed client-side",
  );
});

// ---- 5. The markup, and the sheet model it must not fight ---------------

test("the settings sheet offers the door, before sign out", () => {
  const body = /<div[^>]*class="xb-settings-body"[\s\S]*?\n      <\/div>/.exec(HTML);
  assert.ok(body, "the settings sheet must have a .xb-settings-body");
  const open = body[0].indexOf('id="btn-open-import"');
  const signOut = body[0].indexOf('id="btn-sign-out"');
  assert.ok(open !== -1, "#btn-open-import must live in the settings sheet");
  assert.ok(
    open < signOut,
    "the import door must come before sign out - sign out is the last control in that sheet",
  );
});

test("the import view is a dialog with a name, and starts closed", () => {
  const open = /<div[^>]*id="import-panel"[^>]*>/.exec(HTML);
  assert.ok(open, "index.html must declare #import-panel");
  assert.match(open[0], /role="dialog"/);
  assert.match(open[0], /aria-modal="true"/);
  const labelled = /aria-labelledby="([^"]+)"/.exec(open[0]);
  assert.ok(labelled, "a dialog with no accessible name is announced as nothing");
  assert.ok(HTML.includes(`id="${labelled[1]}"`), `#${labelled[1]} does not exist`);
  assert.match(open[0], /\bhidden\b/, "it must start closed");
});

test("it reuses the settings sheet's frame instead of copying it", () => {
  // The measured height, the safe areas and the single scroller are the rules
  // that stop the on-screen keyboard from pushing a sheet's own header off the
  // top. A second copy of them is a second copy to get wrong.
  const panel = /<div[^>]*id="import-panel"[\s\S]*?\n    <\/div>/.exec(HTML)[0];
  assert.ok(
    /id="import-panel" class="xb-settings-overlay"/.test(panel),
    "#import-panel must wear .xb-settings-overlay",
  );
  assert.ok(panel.includes('class="xb-settings-sheet"'), "and hold a .xb-settings-sheet");
  assert.ok(
    panel.includes('class="xb-settings-sheet-head"'),
    "with the same head, so the close control sits where it does everywhere else",
  );
  assert.ok(
    panel.includes('class="xb-settings-body xb-import-body"'),
    "the body must carry .xb-settings-body so it inherits the one scroller and the bottom safe area",
  );
  const sheet = props(selectorBlock(css, ".xb-settings-sheet"));
  assert.match(
    sheet.height || "",
    /var\(--xb-viewport-height, 100dvh\)/,
    "the shared sheet rule is what makes this true for the import view too",
  );
});

test("it sits above settings, which stays open behind it", () => {
  const importZ = Number(props(selectorBlock(css, "#import-panel"))["z-index"]);
  const settingsZ = Number(props(selectorBlock(css, ".xb-settings-overlay"))["z-index"]);
  assert.ok(
    importZ > settingsZ,
    `#import-panel (${importZ}) must outrank the settings overlay (${settingsZ}), or it opens underneath the sheet that opened it`,
  );
});

test("Escape belongs to the sheet on top", () => {
  assert.ok(
    appJs.includes("isImportOpen()"),
    "app.js must ask whether the import sheet is open before closing settings under it",
  );
  const wire = braceBlock(appJs, appJs.indexOf("function wireSettingsPanel()"));
  const escapeAt = wire.indexOf('event.key !== "Escape"');
  const guardAt = wire.indexOf("isImportOpen()");
  assert.ok(escapeAt > 0 && guardAt > escapeAt, "the guard belongs in the Escape handler");
  assert.ok(
    /if \(view\.isOpen\)|!view\.isOpen/.test(stripComments(importSrc)),
    "import.js must own its own Escape, and only while it is open",
  );
});

test("open takes focus, close hands it back", () => {
  const code = stripComments(importSrc);
  const openFn = braceBlock(code, code.indexOf("function openImport"));
  assert.ok(
    /closeBtn\.focus\(\)/.test(openFn),
    "focus must move INTO the sheet, and onto the close button - focusing a text field would raise the keyboard before anybody asked to type",
  );
  assert.ok(
    !/import-paste|import-team/.test(openFn),
    "focus on open must not land in a field",
  );
  const closeFn = braceBlock(code, code.indexOf("function closeImport"));
  assert.ok(
    /back\.focus\(\)/.test(closeFn),
    "closing must hand focus back to whatever opened it, or a keyboard user is dropped at the top of the document",
  );
});

test("the team is chosen, never assumed", () => {
  const panel = /<div[^>]*id="import-panel"[\s\S]*?\n    <\/div>/.exec(HTML)[0];
  assert.ok(panel.includes('id="import-team"'), "there must be a team chooser");
  assert.ok(
    panel.indexOf('id="import-team"') < panel.indexOf('id="import-drop"'),
    "the team comes first - it is the one choice a second press cannot undo",
  );
  const code = stripComments(importSrc);
  const send = braceBlock(code, code.indexOf("async function sendImport"));
  assert.ok(
    /importTranscriptRaw\(payload\.slug\b/.test(send),
    "the chosen slug is what is sent, not the active team read at mount",
  );
  assert.ok(
    /slug: chooser && chooser\.value/.test(send),
    "and that slug comes from the chooser at the moment of sending",
  );
  assert.ok(
    /if \(!payload\.content \|\| !payload\.format \|\| !payload\.slug\) return;/.test(send),
    "no team, no format or no content must stop the send outright",
  );
});

test("both doors exist, and the drop zone actually accepts a drop", () => {
  const panel = /<div[^>]*id="import-panel"[\s\S]*?\n    <\/div>/.exec(HTML)[0];
  assert.ok(panel.includes('id="import-file"'), "a file picker must exist");
  assert.ok(panel.includes('id="import-paste"'), "and a paste box");
  const picker = /<input[^>]*id="import-file"[^>]*>/.exec(panel)[0];
  for (const ext of [".jsonl", ".json"]) {
    assert.ok(picker.includes(ext), `the picker must accept ${ext}`);
  }
  const code = stripComments(importSrc);
  assert.ok(
    /addEventListener\("dragover"[\s\S]{0,120}preventDefault\(\)/.test(code),
    "without preventDefault on dragover the browser navigates to the dropped file and the app is gone",
  );
  assert.ok(/addEventListener\("drop"/.test(code), "and a drop handler to read it");
});

test("auto is the default, and the chooser is the override", () => {
  const chooser = /<select[^>]*id="import-format"[\s\S]*?<\/select>/.exec(HTML);
  assert.ok(chooser, "index.html must declare #import-format");
  const values = [...chooser[0].matchAll(/value="([^"]+)"/g)].map((m) => m[1]);
  assert.deepEqual(
    values,
    ["auto", "claude-code", "chatgpt"],
    "auto first, then BOTH formats by name - a detection with no override is a guess a person cannot correct",
  );
  const code = stripComments(importSrc);
  const chosen = braceBlock(code, code.indexOf("function chosenFormat"));
  assert.ok(
    /IMPORT_FORMAT_AUTO/.test(chosen),
    "auto is a real value the server accepts, sent as such - its detection is the same code that parses the file, which no client-side guess can match",
  );
  const refresh = braceBlock(code, code.indexOf("function refreshArmed"));
  assert.ok(
    !/!format/.test(refresh),
    "an undetected format must NOT block the send any more - the server works it out",
  );
});

// ---- 6. Android's share sheet -------------------------------------------

test("the manifest declares a share target inside the app's scope", () => {
  const manifest = JSON.parse(readFileSync(join(APP_DIR, "manifest.webmanifest"), "utf8"));
  const share = manifest.share_target;
  assert.ok(share, "manifest.webmanifest must declare share_target, or the PWA never appears in Android's share sheet");
  assert.ok(
    share.action.startsWith(manifest.scope),
    `the action ${share.action} must sit inside the manifest scope ${manifest.scope}, or Chrome rejects the whole declaration`,
  );
  assert.equal(
    share.method.toUpperCase(),
    "GET",
    "GET, so the share arrives as an ordinary navigation the app can read - a POST target needs the worker to intercept it and hand back a synthesised page",
  );
  assert.deepEqual(
    share.params,
    { title: "title", text: "text", url: "url" },
    "all three: Android apps disagree about which field a shared link goes in",
  );
});

test("the three files that must agree about the share path do", () => {
  const manifest = JSON.parse(readFileSync(join(APP_DIR, "manifest.webmanifest"), "utf8"));
  const action = manifest.share_target.action;
  assert.ok(
    stripComments(importSrc).includes(`SHARE_TARGET_PATH = "${action}"`),
    `import.js must recognise ${action} as the share arrival, or the shared text is read on no page at all`,
  );
  assert.ok(
    stripComments(sw).includes(`SHARE_TARGET_PATH = "${action}"`),
    `sw.js must know ${action} to keep it out of the cache`,
  );
  const hosting = JSON.parse(
    readFileSync(join(REPO_ROOT, "app-site", "firebase.json"), "utf8"),
  ).hosting;
  for (const target of hosting) {
    const rewrite = (target.rewrites || []).find((r) => r.source === action);
    assert.ok(
      rewrite,
      `hosting target "${target.target}" has no rewrite for ${action} - nothing exists there on disk, so every share would land on a 404`,
    );
    assert.equal(rewrite.destination, "/app/index.html");
  }
});

test("the worker never answers a share navigation from cache", () => {
  const code = stripComments(sw);
  const guard = code.indexOf("url.pathname === SHARE_TARGET_PATH");
  assert.ok(
    guard > 0,
    "sw.js must return before respondWith for a share navigation - the conversation rides in that query string, and the cached shell does not carry it",
  );
  const respond = code.indexOf("respondWith");
  assert.ok(respond > 0);
  assert.ok(guard < respond, "the guard must run BEFORE respondWith or it gates nothing");
  assert.equal(
    (code.match(/respondWith/g) || []).length,
    1,
    "a second respondWith would need this guard repeated",
  );
  // And the share path must never be precached, which would make the guard the
  // only thing standing between a share and a stale page.
  const shell = /const SHELL = \[([\s\S]*?)\];/.exec(sw)[1];
  assert.ok(
    !shell.includes("share-target"),
    "the share path must not be in SHELL - a precached share URL is a share answered from last week",
  );
});

test("a share is read out of the query, both fields, without duplicating a link", () => {
  const at = (search) => mod.readSharedTranscript({ pathname: "/app/share-target", search });
  assert.equal(at("?text=hello%20there").content, "hello there");
  assert.equal(at("?url=https%3A%2F%2Fchatgpt.com%2Fshare%2Fx").content, "https://chatgpt.com/share/x");
  assert.equal(
    at("?title=A%20chat&text=look%20at%20this&url=https%3A%2F%2Fchatgpt.com%2Fshare%2Fx").content,
    "look at this\nhttps://chatgpt.com/share/x",
    "text and url are different things when both are sent",
  );
  assert.equal(
    at("?text=see%20https%3A%2F%2Fchatgpt.com%2Fshare%2Fx&url=https%3A%2F%2Fchatgpt.com%2Fshare%2Fx").content,
    "see https://chatgpt.com/share/x",
    "several Android apps send the same link twice - sending it twice to the server is not the fix",
  );
  assert.equal(at("?title=Only%20a%20title"), null, "a title alone is not a conversation");
  assert.equal(at(""), null);
});

test("only the declared share path counts as a share", () => {
  // Otherwise any link carrying ?text= would pre-fill the import view out of
  // nowhere, on a normal open.
  assert.equal(
    mod.readSharedTranscript({ pathname: "/app/", search: "?text=hello" }),
    null,
  );
  assert.equal(
    mod.readSharedTranscript({ pathname: "/app/share-target/", search: "?text=hello" }),
    null,
    "the trailing slash is a different path, and it is not the one declared",
  );
  assert.equal(mod.readSharedTranscript(null), null);
});

test("a share is NEVER imported on arrival", () => {
  // A share that writes into a team brain with no confirmation is a share that
  // writes into the wrong team, and a brain has no undo.
  const code = stripComments(importSrc);
  const calls = [...code.matchAll(/importTranscriptRaw\(/g)].map((m) => m.index);
  assert.equal(calls.length, 1, `importTranscriptRaw must be called exactly once; found ${calls.length}`);
  const send = code.indexOf("async function sendImport");
  const afterSend = code.indexOf("\nfunction openImport", send);
  assert.ok(
    calls[0] > send && calls[0] < afterSend,
    "the one call site must live inside sendImport",
  );
  const sendCalls = [...code.matchAll(/(?:^|[^.\w$])sendImport\s*\(/g)]
    .map((m) => m.index)
    .filter((i) => !/function\s+$/.test(code.slice(Math.max(0, i - 24), i + 1)));
  assert.equal(
    sendCalls.length,
    2,
    `sendImport must be called from exactly two places - Import and Re-import anyway; found ${sendCalls.length}`,
  );
  for (const at of sendCalls) {
    const clickAt = code.lastIndexOf('addEventListener("click"', at);
    assert.ok(
      clickAt > 0 && at - clickAt < 120,
      "every call must sit inside a click listener - reachable by a press and by nothing else",
    );
  }
  const consume = braceBlock(code, code.indexOf("function consumeShare"));
  assert.ok(consume, "import.js must handle the arrival in consumeShare");
  assert.ok(
    !/importTranscriptRaw|sendImport/.test(consume),
    "the arrival handler must not send anything",
  );
  assert.ok(
    /openImport\(/.test(consume),
    "it must open the view so the person can confirm",
  );
});

test("the share query is dropped once it has been read", () => {
  const code = stripComments(importSrc);
  const consume = braceBlock(code, code.indexOf("function consumeShare"));
  assert.ok(
    /replaceState\(/.test(consume),
    "a reload must not present the same share again as if it were new",
  );
  assert.ok(
    !/pushState\(/.test(consume),
    "replaceState, not pushState - Back must not walk into the share URL again",
  );
});

// ---- 7. The iPhone Shortcut setup screen --------------------------------

test("an iPhone and an iPad are recognised, a desktop is not", () => {
  assert.equal(mod.isShortcutPlatform({ userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0)" }), true);
  assert.equal(mod.isShortcutPlatform({ userAgent: "Mozilla/5.0 (iPad; CPU OS 18_0)" }), true);
  // iPadOS 13+ reports itself as a Mac; the touch count is what gives it away.
  assert.equal(
    mod.isShortcutPlatform({ userAgent: "Mozilla/5.0 (Macintosh)", platform: "MacIntel", maxTouchPoints: 5 }),
    true,
    "an iPad pretending to be a Mac still needs the Shortcut - it still has no share_target",
  );
  assert.equal(
    mod.isShortcutPlatform({ userAgent: "Mozilla/5.0 (Macintosh)", platform: "MacIntel", maxTouchPoints: 0 }),
    false,
  );
  assert.equal(mod.isShortcutPlatform({ userAgent: "Mozilla/5.0 (Windows NT 10.0)" }), false);
  assert.equal(mod.isShortcutPlatform({ userAgent: "Mozilla/5.0 (Linux; Android 14)" }), false);
  assert.equal(mod.isShortcutPlatform(null), false);
});

test("the screen is honest about what it is", () => {
  const section = /<section[^>]*id="import-shortcut"[\s\S]*?<\/section>/.exec(HTML)[0];
  const words = section.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ");
  assert.match(
    words,
    /iOS does not let a web app join the share sheet/i,
    "it must say plainly that this app cannot join the iOS share sheet - Safari has no share_target and claiming one is a promise the browser will not keep",
  );
  assert.match(words, /one-time setup/i, "and that the Shortcut is what makes it possible");
  assert.match(words, /Shortcuts/, "by name, since that is the app they have to open");
});

test("the steps match what the Shortcuts app actually shows", () => {
  const section = /<section[^>]*id="import-shortcut"[\s\S]*?<\/section>/.exec(HTML)[0];
  const steps = [...section.matchAll(/<li[\s\S]*?<\/li>/g)].map((m) =>
    m[0].replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim(),
  );
  assert.ok(steps.length >= 6, `expected a real procedure, found ${steps.length} step(s)`);
  const all = steps.join(" | ");
  for (const phrase of [
    "Show in Share Sheet",
    "Get Contents of URL",
    "Method",
    "POST",
    "Headers",
    "Request Body",
    "Shortcut Input",
  ]) {
    assert.ok(
      all.includes(phrase),
      `the steps must name "${phrase}" - a step that does not match a control in Shortcuts is a step people give up on`,
    );
  }
});

test("every value the Shortcut needs is on screen, and copyable", () => {
  const section = /<section[^>]*id="import-shortcut"[\s\S]*?<\/section>/.exec(HTML)[0];
  for (const id of [
    "import-endpoint",
    "import-header-auth",
    "import-header-team",
    "import-header-type",
  ]) {
    assert.ok(section.includes(`id="${id}"`), `#${id} must be shown`);
    assert.ok(
      section.includes(`data-xb-copy="${id}"`),
      `#${id} needs its own copy control - retyping an endpoint or a scope on a phone is where this goes wrong`,
    );
  }
  const code = stripComments(importSrc);
  assert.ok(
    /getAttribute\("data-xb-copy"\)/.test(code),
    "one delegated handler must serve every copy control",
  );
  assert.ok(
    /clipboard\.writeText/.test(code) && /selectNodeContents/.test(code),
    "and fall back to selecting the whole value where writing is not permitted - a half-copied token fails with a 401 that explains nothing",
  );
});

test("the values are built from the shared definitions, not retyped", () => {
  // A literal here would keep teaching last month's request long after the
  // code changed, and the shortcut built from it would fail on a phone.
  const code = stripComments(importSrc);
  const paint = braceBlock(code, code.indexOf("function paintShortcut"));
  assert.ok(paint, "import.js must declare paintShortcut");
  assert.ok(
    /importTranscriptTextPath\(\)/.test(paint),
    "the URL must come from the shared path builder",
  );
  assert.ok(
    /MEMORY_API_BASE/.test(paint),
    "and the origin from auth.js, which is the one module that knows where the API lives",
  );
  assert.ok(
    /\$\{IMPORT_TEAM_HEADER\}/.test(paint),
    "the scope header must be named from the shared constant",
  );
  assert.ok(
    /IMPORT_TEXT_CONTENT_TYPE/.test(paint),
    "and the content type too - it is what selects the raw-text body shape",
  );
  assert.ok(
    !/v1\/import|X-Team-Scope:/.test(paint),
    "no route or header name may be retyped in this file",
  );
});

test("the token is bound to the chosen team, and shown once", () => {
  const code = stripComments(importSrc);
  const mint = braceBlock(code, code.indexOf("async function mintImportToken"));
  assert.ok(
    /mintImportTokenRaw\(slug,/.test(mint),
    "the token is issued for ONE team; minting without the chosen slug would bind it to the wrong one",
  );
  assert.ok(
    /Choose a team above first/.test(mint),
    "with no team chosen it must say so, not send a request that 403s on a field the person never saw",
  );
  const section = /<section[^>]*id="import-shortcut"[\s\S]*?<\/section>/.exec(HTML)[0];
  const words = section.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ");
  assert.match(words, /Shown once/i, "the screen must say the token is shown once");
  assert.match(
    words,
    /press the button again|create another/i,
    "and that a person who left without copying can mint another - being stuck is not an option",
  );
  assert.match(
    words,
    /cannot read the chat/i,
    "and that it is capability-scoped to importing, which is why it is safe on a phone",
  );
  assert.ok(
    /clearMintedToken\(\)/.test(braceBlock(code, code.indexOf("function closeImport"))),
    "closing must drop the minted token out of the DOM - it is a bearer secret",
  );
});

test("the screen degrades when the token endpoint is absent", () => {
  const code = stripComments(importSrc);
  const mint = braceBlock(code, code.indexOf("async function mintImportToken"));
  assert.ok(
    /res\.status === 404/.test(mint),
    "an API that has not been redeployed answers 404 - exactly when somebody is on this screen",
  );
  assert.ok(
    /not available on this server yet/.test(mint),
    "it must say the step is unavailable rather than throwing inside the settings sheet",
  );
  assert.ok(
    /Every other step above is still correct/.test(mint),
    "the other eight steps are still true, and hiding them would waste the visit",
  );
  assert.ok(
    /classList\.add\("is-unavailable"\)/.test(mint),
    "the step marks itself, so the procedure keeps its numbering",
  );
  assert.ok(
    /catch \(e\)[\s\S]{0,200}Network error - no token was created/.test(mint),
    "a throw must land as a sentence, not as an unhandled rejection",
  );
  assert.ok(
    props(selectorBlock(css, ".xb-import-steps li.is-unavailable"))["opacity"],
    "app.css must dim an unavailable step, or the class says nothing on screen",
  );
});

test("it is shown on iOS, and reachable everywhere else", () => {
  const code = stripComments(importSrc);
  assert.ok(
    /isShortcutPlatform\(navigator\)/.test(code),
    "the section must open itself on a phone",
  );
  assert.ok(
    HTML.includes('id="btn-import-shortcut-show"'),
    "and be reachable from a laptop, because that is where people set their phone up",
  );
  const open = /<section[^>]*id="import-shortcut"[^>]*>/.exec(HTML)[0];
  assert.match(open, /\bhidden\b/, "it must start closed - the markup cannot know the platform");
});

// ---- 8. The shell ships it ----------------------------------------------

test("import.js is precached with the rest of the shell", () => {
  const block = /const SHELL = \[([\s\S]*?)\];/.exec(sw);
  assert.ok(block, "sw.js must declare a SHELL array");
  assert.ok(
    block[1].includes('"/app/import.js"'),
    "/app/import.js ships, so it belongs in SHELL or it is missing offline with no error",
  );
});

test("english-only: no accented Latin chars in import.js", () => {
  const hits = importSrc.match(/[À-ÿ]/g) || [];
  assert.equal(
    hits.length,
    0,
    `import.js has ${hits.length} accented char(s) ${JSON.stringify([...new Set(hits)])}`,
  );
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
