/**
 * Import a past conversation into a team brain.
 *
 * A Claude Code session (JSONL on the disk of the machine you code on) or a
 * ChatGPT export lands in ONE team, tagged as an import, through the single
 * request declared in chat_core/api.js. This file is the surface: which element
 * is which, what is armed to be sent, and what the person is told afterwards.
 *
 * THREE RULES THIS FILE EXISTS TO KEEP.
 *
 * 1. THE CLIENT NEVER PARSES A TRANSCRIPT. The parsers live server-side and are
 *    tested there; a second implementation here would drift within a release
 *    and would disagree about what a "turn" is. So this file reads the file,
 *    sends it, and shows what came back. It DETECTS the format — which is a
 *    glance at the first few lines, not a parse — because making a person
 *    classify their own export is asking them to know our vocabulary. The
 *    detection is a suggestion and can be overridden.
 *
 * 2. THE SIZE IS CHECKED BEFORE THE READ. A full ChatGPT export is tens of
 *    megabytes. Reading one into a string on a phone gets the tab killed by the
 *    OS, and a killed tab explains nothing. So the refusal happens on
 *    `file.size`, before a single byte is read, and the read itself is async so
 *    the UI never freezes on it.
 *
 * 3. THE RESULT SAYS BOTH NUMBERS. "Nothing happened" and "you already imported
 *    this" are the same blank screen unless the skipped count is shown, and a
 *    person who cannot tell them apart imports the same file a third time.
 *
 * The view is a full-screen sheet built from the SAME rules as the settings
 * sheet it opens from: sized off the measured viewport (so the on-screen
 * keyboard cannot push its header off), its body is the only scroller, focus
 * moves in on open and back to the control that opened it on close, and Escape
 * closes it. It sits ABOVE settings rather than replacing it, so closing it
 * lands back where it was opened from.
 *
 * NOTHING here may ask for notification access or touch a push subscription
 * (D-27-05) — push.js owns the single click-gated call site.
 */

import { setStatusLine, clearChildren } from "./chat_core/dom.js";
import {
  IMPORT_TEAM_HEADER,
  IMPORT_TEXT_CONTENT_TYPE,
  MAX_IMPORT_BYTES,
  importTranscriptTextPath,
  summarizeImport,
} from "./chat_core/api.js";
import { MEMORY_API_BASE } from "./auth.js";

const el = (id) => document.getElementById(id);

/** The same ceiling in the words the refusal uses. */
const MAX_IMPORT_MB = Math.round(MAX_IMPORT_BYTES / (1024 * 1024));

/* ==========================================================================
 * Pure logic — no DOM, no network. Everything below the fold in this block is
 * exercised directly by the tests, which is why it takes its input as an
 * argument rather than reading the document.
 * ======================================================================== */

/**
 * Up to `max` non-empty lines from the start of `text`.
 *
 * Deliberately NOT `text.split("\n")`: a ten-megabyte transcript would allocate
 * a hundred thousand strings to answer a question about its first five lines.
 *
 * @param {string} text
 * @param {number} max
 * @returns {string[]}
 */
function headLines(text, max) {
  const out = [];
  let start = 0;
  while (out.length < max && start < text.length) {
    const nl = text.indexOf("\n", start);
    const line = (nl === -1 ? text.slice(start) : text.slice(start, nl)).trim();
    if (line) out.push(line);
    if (nl === -1) break;
    start = nl + 1;
  }
  return out;
}

/** True when `line` is a complete JSON object — not an array, not a scalar. */
function isJsonObjectText(line) {
  if (!line.startsWith("{")) return false;
  try {
    const value = JSON.parse(line);
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
  } catch (e) {
    return false;
  }
}

/**
 * Which of the two formats this content looks like, or null when it is not
 * clear enough to guess.
 *
 * WHY NULL IS A FIRST-CLASS ANSWER. A wrong guess that is presented as fact
 * sends a file to the wrong parser and produces a 422 the person cannot act on.
 * An honest "we could not tell" costs one tap on a chooser that is right there.
 *
 * The order matters:
 *   1. a bare share link from the ChatGPT app — the single most common thing an
 *      Android share delivers, and the one case where the content is not a
 *      transcript at all;
 *   2. one JSON object per line, checked on a SAMPLE of the head — Claude Code;
 *   3. a JSON document mentioning `mapping` — the ChatGPT export's conversation
 *      tree. Checked after (2) because a Claude Code entry can quote anything,
 *      including that word, inside a tool result;
 *   4. a single JSON object carrying Claude Code's own keys — a one-entry
 *      session, which (2) cannot see because it needs two lines to be sure.
 *
 * @param {string} text
 * @returns {"claude-code"|"chatgpt"|null}
 */
export function detectTranscriptFormat(text) {
  const trimmed = String(text || "").trim();
  if (!trimmed) return null;

  if (looksLikeSharedChatUrl(trimmed)) return "chatgpt";

  const lines = headLines(trimmed, 5);
  if (lines.length >= 2 && lines.every(isJsonObjectText)) return "claude-code";

  if (trimmed.startsWith("[") || trimmed.startsWith("{")) {
    if (/"mapping"\s*:/.test(trimmed)) return "chatgpt";
    if (lines.length === 1 && isJsonObjectText(lines[0])) {
      const entry = JSON.parse(lines[0]);
      const claudeCodeKeys = ["sessionId", "parentUuid", "uuid", "cwd"];
      if (claudeCodeKeys.some((key) => key in entry)) return "claude-code";
      if ("type" in entry && "message" in entry) return "claude-code";
    }
  }

  return null;
}

/**
 * A single ChatGPT share link and nothing else.
 *
 * Parsed rather than pattern-matched so `chatgpt.com.evil.test` cannot pass as
 * `chatgpt.com`, and so a paragraph that merely contains a link is not mistaken
 * for one.
 *
 * @param {string} text
 * @returns {boolean}
 */
function looksLikeSharedChatUrl(text) {
  if (/\s/.test(text)) return false;
  let parsed;
  try {
    parsed = new URL(text);
  } catch (e) {
    return false;
  }
  if (parsed.protocol !== "https:") return false;
  const host = parsed.hostname.toLowerCase();
  return (
    host === "chatgpt.com" ||
    host === "chat.openai.com" ||
    host.endsWith(".chatgpt.com")
  );
}

/**
 * Why this transcript cannot be sent, in words a person can act on — or null
 * when it can.
 *
 * Called with `file.size` BEFORE the file is read. That ordering is the whole
 * value of this function: the alternative is reading tens of megabytes into a
 * string first and finding out afterwards, by which time a phone has already
 * killed the tab.
 *
 * @param {number} bytes
 * @param {string} [label] the file's name, when there is one
 * @returns {string|null}
 */
export function importSizeRefusal(bytes, label) {
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return `${label || "That transcript"} is empty - there is nothing to import.`;
  }
  if (bytes <= MAX_IMPORT_BYTES) return null;
  const mb = Math.max(1, Math.round(bytes / (1024 * 1024)));
  return (
    `${label || "That transcript"} is about ${mb} MB, over the ${MAX_IMPORT_MB} MB limit. ` +
    "A whole ChatGPT export holds every conversation you ever had - share or " +
    "export the single conversation you want instead."
  );
}

/**
 * Where Android's share sheet lands.
 *
 * Declared as `share_target` in manifest.webmanifest and rewritten to the app
 * shell by app-site/firebase.json — three files that must agree, which is why
 * the string is spelled in each of them and checked against the others by a
 * test rather than being passed around.
 *
 * iOS is NOT here and cannot be: Safari implements the Web Share API for
 * sharing OUT, never for receiving. The Shortcuts route is what covers a phone.
 */
export const SHARE_TARGET_PATH = "/app/share-target";

/**
 * The conversation an Android share just handed us, or null.
 *
 * A GET share target arrives as an ordinary navigation with the shared fields
 * in the query, so this reads a location rather than intercepting anything.
 *
 * `text` and `url` are BOTH read because Android apps disagree about which one
 * a link goes in, and several send the same link twice — once inside a sentence
 * and once on its own. Joining them blindly would send the link twice.
 *
 * @param {{pathname: string, search: string}} where
 * @returns {{content: string, title: string}|null}
 */
export function readSharedTranscript(where) {
  if (!where || where.pathname !== SHARE_TARGET_PATH) return null;
  const params = new URLSearchParams(where.search || "");
  const text = (params.get("text") || "").trim();
  const url = (params.get("url") || "").trim();
  const title = (params.get("title") || "").trim();
  let content = text;
  if (url && !text.includes(url)) content = content ? `${content}\n${url}` : url;
  if (!content) return null;
  return { content, title };
}

/** "1 turn" / "12 turns", so no sentence below has to hedge with "(s)". */
function countLabel(n, noun) {
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}

/**
 * The sentence shown after a successful import.
 *
 * Four outcomes, and they are NOT interchangeable:
 *   - the server reported nothing countable — say so, do not invent a zero;
 *   - zero and zero — the transcript parsed but held nothing we recognised;
 *   - zero imported with skips — already in the brain, which is a SUCCESS and
 *     must not read like a failure;
 *   - anything imported — both numbers, because the skipped ones are the
 *     evidence that a re-import did not double the brain.
 *
 * @param {{imported: number|null, skipped: number|null, reported: boolean}} summary
 * @returns {string}
 */
export function describeImportResult(summary) {
  const { imported, skipped, reported } = summary || {};
  if (!reported) {
    return "The server accepted the transcript but reported no counts. Check the team's brain before sending it again.";
  }
  const kept = imported || 0;
  const dupes = skipped || 0;
  if (kept === 0 && dupes === 0) {
    return "Nothing was imported and nothing was skipped - this transcript held no turns the server recognised.";
  }
  if (kept === 0) {
    return `Nothing new: all ${countLabel(dupes, "turn")} were already in this team's brain.`;
  }
  if (dupes === 0) {
    return `Imported ${countLabel(kept, "turn")}.`;
  }
  return `Imported ${countLabel(kept, "turn")}, and skipped ${countLabel(dupes, "turn")} already in this team's brain.`;
}

/**
 * Is this a device where the share sheet is reached through Shortcuts?
 *
 * Safari does not implement `share_target` — it has the Web Share API for
 * sharing OUT, never for receiving — so an iPhone cannot put this app in its
 * share sheet at all. Apple's Shortcuts app can, and the setup screen is how
 * that gets built. Nothing here claims otherwise.
 *
 * Takes the navigator as an argument so this is testable, and reads
 * maxTouchPoints because an iPad on iPadOS 13+ reports itself as a Mac.
 *
 * @param {{userAgent?: string, platform?: string, maxTouchPoints?: number}} nav
 * @returns {boolean}
 */
export function isShortcutPlatform(nav) {
  if (!nav) return false;
  if (/iPhone|iPad|iPod/.test(String(nav.userAgent || ""))) return true;
  return (
    String(nav.platform || "") === "MacIntel" && Number(nav.maxTouchPoints || 0) > 1
  );
}

/** What the server's rejection means to the person holding the file. */
function importError(status) {
  if (status === 404) return "This server has no importer yet. Nothing was sent anywhere.";
  if (status === 413) return `That transcript is too large for the server - keep it under ${MAX_IMPORT_MB} MB.`;
  if (status === 415 || status === 422) {
    return "The server could not read that transcript in the format you chose. Try the other format, or check you picked the right file.";
  }
  if (status === 403) return "You are not a member of that team.";
  if (status === 429) return "Too many imports just now - wait a moment and try again.";
  return `The import failed (HTTP ${status}).`;
}

/* ==========================================================================
 * The surface
 * ======================================================================== */

/**
 * What is armed to be sent, and where it came from.
 *
 * The file and the paste box are two doors onto one thing: whichever was used
 * LAST is what gets sent, and the other is visibly cleared. Two armed sources
 * with one Import button is how somebody sends the file they abandoned.
 */
const armed = {
  content: "",
  label: "",
  origin: "",
  detected: null,
};

/** Panel state that outlives a single open. */
const view = {
  isOpen: false,
  opener: null,
  wired: false,
  teams: [],
};

/** Is the import sheet on screen? Read by app.js, which owns Escape. */
export function isImportOpen() {
  return view.isOpen;
}

/**
 * Read a file as text without blocking the page.
 *
 * `File.text()` where it exists, FileReader everywhere else (Safari before 14
 * has no `text()`). Never a synchronous read: on a phone that is the difference
 * between a progress line and a killed tab.
 *
 * @param {File|Blob} file
 * @returns {Promise<string>}
 */
function readTextFile(file) {
  if (typeof file.text === "function") return file.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error("read failed"));
    reader.readAsText(file);
  });
}

/** The format that will actually be declared: the chooser, or the detection. */
function chosenFormat() {
  const chooser = el("import-format");
  const value = chooser ? chooser.value : "auto";
  if (value && value !== "auto") return value;
  return armed.detected;
}

/**
 * Repaint everything that depends on what is armed: the source line, the
 * detection note, and whether Import can be pressed at all.
 *
 * One function, called after every change, so the button's enabled state can
 * never disagree with what is actually loaded.
 */
function refreshArmed() {
  const source = el("import-source");
  const note = el("import-format-note");
  const sendBtn = el("btn-import-send");
  const teamChooser = el("import-team");

  if (source) {
    source.textContent = armed.content ? armed.label : "";
    source.hidden = !armed.content;
  }

  const format = chosenFormat();
  if (note) {
    if (!armed.content) {
      note.textContent = "";
      note.hidden = true;
    } else if (format) {
      note.textContent =
        format === armed.detected
          ? `Detected as ${formatLabel(format)}. Change it above if that is wrong.`
          : `Sending as ${formatLabel(format)}.`;
      note.hidden = false;
    } else {
      note.textContent =
        "We could not tell which format this is. Pick one above before importing.";
      note.hidden = false;
    }
  }

  const hasTeam = Boolean(teamChooser && teamChooser.value);
  if (sendBtn) sendBtn.disabled = !armed.content || !format || !hasTeam;
}

/** The format's name in the words the chooser uses. */
function formatLabel(format) {
  if (format === "claude-code") return "a Claude Code session";
  if (format === "chatgpt") return "a ChatGPT conversation";
  return format;
}

/**
 * Arm a piece of text, from wherever it came.
 *
 * @param {string} text
 * @param {string} label what to show as the source
 * @param {string} origin "file" | "paste" | "share"
 */
function armContent(text, label, origin) {
  armed.content = text;
  armed.label = label;
  armed.origin = origin;
  armed.detected = detectTranscriptFormat(text);
  refreshArmed();
}

/** Nothing loaded. Both doors cleared, so neither can be sent by accident. */
function disarmContent() {
  armed.content = "";
  armed.label = "";
  armed.origin = "";
  armed.detected = null;
  refreshArmed();
}

/**
 * A file, from the picker or from a drop.
 *
 * The size gate runs FIRST — see the module docstring. Everything after it is
 * asynchronous and reports progress, because a ten-megabyte read on a phone is
 * long enough that a silent UI reads as a broken one.
 *
 * @param {File} file
 */
async function takeFile(file) {
  const status = el("import-status");
  const paste = el("import-paste");
  const refusal = importSizeRefusal(file.size, file.name);
  if (refusal) {
    disarmContent();
    setStatusLine(status, refusal, "error");
    return;
  }

  setStatusLine(status, `Reading ${file.name}...`, "loading");
  let text = "";
  try {
    text = await readTextFile(file);
  } catch (e) {
    disarmContent();
    setStatusLine(status, `Could not read ${file.name}.`, "error");
    return;
  }
  if (!text.trim()) {
    disarmContent();
    setStatusLine(status, `${file.name} is empty.`, "error");
    return;
  }

  // The paste box is NOT filled with the file: a ten-megabyte string in a
  // textarea is the freeze this whole path exists to avoid. The source line
  // says what is loaded instead.
  if (paste) paste.value = "";
  const mb = file.size / (1024 * 1024);
  const size = mb >= 0.1 ? `${mb.toFixed(1)} MB` : `${Math.max(1, Math.round(file.size / 1024))} KB`;
  armContent(text, `${file.name} - ${size}`, "file");
  setStatusLine(status, "", "");
}

/** Fill the team chooser. An import goes into one team; guessing is wrong. */
async function fillTeamChooser(api, getTeamSlug) {
  const chooser = el("import-team");
  const hint = el("import-team-hint");
  if (!chooser) return;

  let teams = [];
  try {
    const answer = await api.myTeams();
    teams = Array.isArray(answer) ? answer : (answer && answer.teams) || [];
  } catch (e) {
    teams = [];
  }
  view.teams = teams;

  clearChildren(chooser);
  for (const team of teams) {
    if (!team || !team.slug) continue;
    const option = document.createElement("option");
    option.value = team.slug;
    option.textContent = team.display_name || team.name || team.slug;
    chooser.appendChild(option);
  }

  const active = typeof getTeamSlug === "function" ? getTeamSlug() : null;
  if (active && teams.some((t) => t && t.slug === active)) chooser.value = active;

  const empty = chooser.options.length === 0;
  chooser.disabled = empty;
  if (hint) {
    hint.textContent = empty
      ? "You are not in a team yet. Create or join one first - an import has to land somewhere."
      : "The conversation lands in this team's brain, and everyone in it will see it.";
  }
  refreshArmed();
}

/**
 * Send it.
 *
 * The button is disabled for the whole round trip: a second press would import
 * the same transcript twice, and although the server's own dedupe should catch
 * that, "should" is not a thing to hand a person's history to.
 */
async function sendImport(api) {
  const status = el("import-status");
  const result = el("import-result");
  const sendBtn = el("btn-import-send");
  const chooser = el("import-team");
  const format = chosenFormat();
  const slug = chooser && chooser.value;

  if (!armed.content || !format || !slug) return;

  if (result) {
    result.textContent = "";
    result.hidden = true;
  }
  if (sendBtn) sendBtn.disabled = true;
  setStatusLine(status, "Importing...", "loading");

  try {
    const res = await api.importTranscriptRaw(slug, {
      format,
      content: armed.content,
    });
    if (!res.ok) {
      setStatusLine(status, importError(res.status), "error");
      return;
    }
    const body = await res.json().catch(() => null);
    const summary = summarizeImport(body);
    setStatusLine(status, "", "");
    if (result) {
      result.textContent = describeImportResult(summary);
      result.hidden = false;
    }
    // Disarmed on success: leaving it loaded invites the second press that
    // sends the same thing again.
    const paste = el("import-paste");
    if (paste) paste.value = "";
    disarmContent();
  } catch (e) {
    setStatusLine(status, "Network error - nothing was imported.", "error");
  } finally {
    if (sendBtn) sendBtn.disabled = false;
    refreshArmed();
  }
}

/** Open the sheet, remembering what to give focus back to. */
function openImport(opener) {
  const panel = el("import-panel");
  if (!panel) return;
  view.opener = opener || el("btn-settings");
  panel.hidden = false;
  view.isOpen = true;
  const closeBtn = el("btn-import-close");
  // The close button, never a text field: focusing an input here would raise
  // the on-screen keyboard before anybody asked to type.
  if (closeBtn && typeof closeBtn.focus === "function") closeBtn.focus();
}

/** Close it, and hand focus back to whatever opened it. */
function closeImport() {
  const panel = el("import-panel");
  if (!panel || panel.hidden) return;
  panel.hidden = true;
  view.isOpen = false;
  // A minted token is a bearer secret sitting in the DOM. It leaves with the
  // sheet, the way the invite panel drops its code.
  clearMintedToken();
  const back = view.opener;
  if (back && !back.hidden && typeof back.focus === "function") back.focus();
  view.opener = null;
}

/* ---- The iPhone Shortcut setup screen ---------------------------------- */

/**
 * Fill in the exact values the Shortcuts app needs, from the SAME definitions
 * the app itself posts with.
 *
 * If any of these were retyped here as literals, the screen would go on
 * teaching last month's request long after the code changed — and a shortcut
 * built from it fails on a phone, silently, with the person having no idea the
 * instructions were the stale part.
 */
function paintShortcut() {
  const chooser = el("import-team");
  const slug = (chooser && chooser.value) || "your-team";

  const endpoint = el("import-endpoint");
  // The raw-text URL, not the JSON one: the same route accepts the transcript
  // as the whole body with the format in the query, and that is one step in
  // Shortcuts instead of four.
  if (endpoint) {
    endpoint.textContent = `${MEMORY_API_BASE}${importTranscriptTextPath()}`;
  }

  const auth = el("import-header-auth");
  // The header NAME and the "Bearer " prefix, with the token left as a blank to
  // paste into: printing them apart is how somebody ends up sending a bare
  // token with no scheme and getting a 401 that explains nothing.
  if (auth) auth.textContent = "Authorization: Bearer <your import token>";

  const team = el("import-header-team");
  if (team) team.textContent = `${IMPORT_TEAM_HEADER}: ${slug}`;

  const type = el("import-header-type");
  // Not decoration: this header is what selects the raw-text body shape. Sent
  // as JSON, the transcript would be parsed as a request object and rejected.
  if (type) type.textContent = `Content-Type: ${IMPORT_TEXT_CONTENT_TYPE}`;
}

/**
 * Copy one of those values.
 *
 * The value comes from the element's own text, so what is copied is exactly
 * what is on screen — there is no second string to fall out of step with the
 * one being read.
 *
 * @param {Element} btn a control carrying data-xb-copy="<element id>"
 */
async function copyValue(btn) {
  const source = el(btn.getAttribute("data-xb-copy"));
  if (!source) return;
  const text = source.textContent || "";
  const label = btn.textContent;
  try {
    await navigator.clipboard.writeText(text);
    btn.textContent = "Copied";
  } catch (e) {
    // No permission, or an insecure context. Select it instead so one gesture
    // still gets the whole value — a half-copied token fails with a 401 that
    // says nothing about why.
    try {
      const range = document.createRange();
      range.selectNodeContents(source);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      btn.textContent = "Selected - copy it";
    } catch (e2) {
      btn.textContent = "Copy failed";
    }
  }
  window.setTimeout(() => {
    btn.textContent = label;
  }, 1600);
}

/** Forget a minted token. Called on close: it is a bearer secret in the DOM. */
function clearMintedToken() {
  const row = el("import-token-row");
  const value = el("import-token-value");
  if (value) value.textContent = "";
  if (row) row.hidden = true;
}

/**
 * Mint the credential the shortcut carries.
 *
 * WHY A DEDICATED TOKEN. A shortcut lives on the phone and can be shared like
 * any other shortcut. Putting the account token in it would hand the whole
 * account to whoever receives it; this one can do nothing but import.
 *
 * SHOWN ONCE, and the screen says so. If the endpoint is not deployed yet the
 * step degrades to "unavailable" and every other instruction above it stays
 * correct — a setup screen that throws takes the import view down with it, over
 * a step that is one of nine.
 */
async function mintImportToken(api) {
  const status = el("import-shortcut-status");
  const btn = el("btn-import-token");
  const row = el("import-token-row");
  const value = el("import-token-value");
  const chooser = el("import-team");
  const slug = chooser && chooser.value;

  if (!slug) {
    // The token is bound to one team and minting requires membership of it, so
    // there is nothing to mint against yet. Saying so beats a 403 from a field
    // the person never knew was involved.
    setStatusLine(status, "Choose a team above first - the token is bound to one.", "error");
    return;
  }

  if (btn) btn.disabled = true;
  setStatusLine(status, "Creating a token...", "loading");
  try {
    const res = await api.mintImportTokenRaw(slug, "iPhone Shortcut");
    if (res.status === 404 || res.status === 405 || res.status === 501) {
      // Not deployed here. Not an error the person caused, and not something a
      // second press will fix.
      setStatusLine(
        status,
        "Import tokens are not available on this server yet. Every other step above is still correct - come back for this one.",
        "error",
      );
      const step = el("import-step-token");
      if (step) step.classList.add("is-unavailable");
      return;
    }
    if (!res.ok) {
      setStatusLine(
        status,
        res.status === 403
          ? "You are not a member of that team, so no token can be issued for it."
          : `Could not create a token (HTTP ${res.status}).`,
        "error",
      );
      if (btn) btn.disabled = false;
      return;
    }
    const body = await res.json().catch(() => null);
    const token = body && (body.token || body.api_token || body.value);
    if (!token) {
      setStatusLine(status, "The server created a token but did not return it.", "error");
      if (btn) btn.disabled = false;
      return;
    }
    if (value) value.textContent = token;
    if (row) row.hidden = false;
    setStatusLine(status, "", "");
    // A person who navigates away without copying must be able to mint another
    // rather than be stuck with a step they cannot complete.
    if (btn) {
      btn.textContent = "Create another token";
      btn.disabled = false;
    }
  } catch (e) {
    setStatusLine(status, "Network error - no token was created.", "error");
    if (btn) btn.disabled = false;
  }
}

/**
 * A share just arrived: pre-fill the view, open it, and STOP.
 *
 * NOTHING IS IMPORTED HERE. A share that writes into a team brain on arrival is
 * a share that writes into the wrong team — the sharer picked an app, not a
 * destination, and there is no undo for a brain. So this arms the content,
 * names where it came from, and hands the decision back: pick the team, check
 * the format, press Import.
 *
 * The query is dropped from the address afterwards so a reload does not present
 * the same share a second time as if it were new. The content is already in
 * memory by then; the URL was only ever the delivery.
 *
 * @param {{content: string, title: string}} shared
 */
function consumeShare(shared) {
  const status = el("import-status");
  const refusal = importSizeRefusal(shared.content.length, "That share");
  if (refusal) {
    setStatusLine(status, refusal, "error");
  } else {
    const paste = el("import-paste");
    if (paste) paste.value = shared.content;
    armContent(shared.content, shared.title ? `shared: ${shared.title}` : "shared from another app", "share");
    setStatusLine(
      status,
      "Shared from another app. Choose the team, check the format, then press Import.",
      "loading",
    );
  }

  openImport(el("btn-settings"));

  // The address bar, not the content. Best effort: an environment without
  // history.replaceState still has the share armed and on screen.
  try {
    if (window.history && typeof window.history.replaceState === "function") {
      window.history.replaceState(null, "", "/app/");
    }
  } catch (e) {
    // A blocked history write is not worth a message; the share still works.
  }
}

/**
 * Shut it and forget what was loaded.
 *
 * Called on sign-out. A transcript somebody armed is their content, and leaving
 * it in the box for whoever signs in next on the same device is the same class
 * of mistake as leaving their name on the screen.
 */
export function hideImport() {
  const panel = el("import-panel");
  if (panel) panel.hidden = true;
  view.isOpen = false;
  view.opener = null;
  const paste = el("import-paste");
  if (paste) paste.value = "";
  const openBtn = el("btn-open-import");
  if (openBtn) openBtn.hidden = true;
  const result = el("import-result");
  if (result) {
    result.textContent = "";
    result.hidden = true;
  }
  setStatusLine(el("import-status"), "", "");
  clearMintedToken();
  disarmContent();
}

/**
 * Wire the whole surface. Idempotent: mountImport runs again after a
 * re-sign-in, on the same elements, and a second set of listeners would send
 * every import twice.
 *
 * @param {{myTeams: Function, importTranscriptRaw: Function}} api the shared client
 * @param {{getTeamSlug?: () => (string|null)}} [refs]
 * @returns {Promise<{teams: number}>}
 */
export async function mountImport(api, refs = {}) {
  const panel = el("import-panel");
  const openBtn = el("btn-open-import");
  if (!panel) return { teams: 0 };

  if (!view.wired) {
    view.wired = true;

    if (openBtn) {
      openBtn.addEventListener("click", () => {
        openImport(openBtn);
      });
    }
    const closeBtn = el("btn-import-close");
    if (closeBtn) closeBtn.addEventListener("click", closeImport);
    // The scrim beside the sheet, on a wide window. Same rule as settings.
    panel.addEventListener("click", (event) => {
      if (event.target === panel) closeImport();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || !view.isOpen) return;
      // This sheet is on top, so it takes Escape first; app.js checks
      // isImportOpen() before closing settings underneath it.
      event.stopPropagation();
      closeImport();
    });

    const picker = el("import-file");
    const pickBtn = el("btn-import-pick");
    if (pickBtn && picker) pickBtn.addEventListener("click", () => picker.click());
    if (picker) {
      picker.addEventListener("change", async (event) => {
        const target = event.target || {};
        const file = (target.files && target.files[0]) || null;
        // Cleared first, so picking the same file twice in a row still fires.
        target.value = "";
        if (file) await takeFile(file);
      });
    }

    const drop = el("import-drop");
    if (drop) {
      // preventDefault on dragover is what makes a drop possible at all; without
      // it the browser navigates to the file and the app is gone.
      drop.addEventListener("dragover", (event) => {
        event.preventDefault();
        drop.classList.add("is-dragover");
      });
      drop.addEventListener("dragleave", () => drop.classList.remove("is-dragover"));
      drop.addEventListener("drop", async (event) => {
        event.preventDefault();
        drop.classList.remove("is-dragover");
        const transfer = event.dataTransfer;
        if (!transfer) return;
        const file = (transfer.files && transfer.files[0]) || null;
        if (file) {
          await takeFile(file);
          return;
        }
        const text = transfer.getData ? transfer.getData("text") : "";
        if (text && text.trim()) {
          const paste = el("import-paste");
          if (paste) paste.value = text;
          armContent(text, "dropped text", "paste");
        }
      });
    }
    // A file dropped anywhere ELSE in the sheet must not navigate the tab away
    // from a half-filled form.
    panel.addEventListener("dragover", (event) => event.preventDefault());
    panel.addEventListener("drop", (event) => event.preventDefault());

    const paste = el("import-paste");
    if (paste) {
      paste.addEventListener("input", () => {
        const text = paste.value;
        if (text.trim()) {
          const refusal = importSizeRefusal(text.length, "That text");
          if (refusal) {
            disarmContent();
            setStatusLine(el("import-status"), refusal, "error");
            return;
          }
          setStatusLine(el("import-status"), "", "");
          armContent(text, "pasted text", "paste");
        } else if (armed.origin === "paste") {
          disarmContent();
        }
      });
    }

    const format = el("import-format");
    if (format) format.addEventListener("change", refreshArmed);
    const teamChooser = el("import-team");
    if (teamChooser) {
      teamChooser.addEventListener("change", () => {
        refreshArmed();
        // The Shortcut's team header is whatever is selected here; a stale one
        // would build a shortcut that quietly posts into the wrong team.
        paintShortcut();
      });
    }

    const sendBtn = el("btn-import-send");
    if (sendBtn) sendBtn.addEventListener("click", () => sendImport(api));

    // Every "Copy" in the setup screen, through one delegated listener, so a
    // new value row needs no new wiring.
    panel.addEventListener("click", (event) => {
      const target = event.target;
      const btn = target && target.closest ? target.closest("[data-xb-copy]") : null;
      if (btn) copyValue(btn);
    });

    const tokenBtn = el("btn-import-token");
    if (tokenBtn) tokenBtn.addEventListener("click", () => mintImportToken(api));
    const tokenCopy = el("btn-import-token-copy");
    if (tokenCopy) {
      tokenCopy.setAttribute("data-xb-copy", "import-token-value");
    }

    // On an iPhone the setup screen is the whole point, so it is open. Anywhere
    // else it is behind a control, because people set their phone up from a
    // laptop and hiding it there would mean it could not be reached at all.
    const shortcut = el("import-shortcut");
    const shortcutBtn = el("btn-import-shortcut-show");
    if (shortcut) {
      const onPhone = isShortcutPlatform(navigator);
      shortcut.hidden = !onPhone;
      if (shortcutBtn) {
        shortcutBtn.hidden = onPhone;
        shortcutBtn.addEventListener("click", () => {
          shortcut.hidden = false;
          shortcutBtn.hidden = true;
        });
      }
    }
  }

  if (openBtn) openBtn.hidden = false;
  await fillTeamChooser(api, refs.getTeamSlug);
  refreshArmed();
  // After the team is known: every value on the setup screen depends on it.
  paintShortcut();

  // Last, and only after the team chooser is filled: a share that opened the
  // view before there was a team to pick would ask for a confirmation the
  // person cannot give.
  const shared = readSharedTranscript(window.location);
  if (shared) consumeShare(shared);

  return { teams: view.teams.length, shared: Boolean(shared) };
}
