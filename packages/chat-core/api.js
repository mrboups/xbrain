/**
 * Transport-agnostic memory-api client (Phase 27, D-27-04).
 *
 * Replaces the per-surface `fetchJson` helper: ONE place builds the
 * Authorization header, ONE place turns a non-2xx into an Error, and every
 * endpoint the chat uses has a named method.
 *
 * The base origin is INJECTED, never hardcoded here (T-27-01-03) — the
 * extension passes its own constant, the PWA passes its own, and a shared file
 * can therefore never repoint one surface at the other's backend.
 *
 * Used by:
 *   - chrome-extension/popup.js (via chrome-extension/chat_core/)
 *   - app-site/app/ — the PWA (via app-site/app/chat_core/)
 */

/**
 * @param {{baseUrl: string, getToken: () => Promise<string|null>}} config
 *   baseUrl  — origin of memory-api, no trailing slash required
 *   getToken — async, returns the caller's bearer token or null when signed out
 */
export function createApi({ baseUrl, getToken } = {}) {
  if (typeof baseUrl !== "string" || baseUrl.trim() === "") {
    throw new TypeError("createApi requires a non-empty baseUrl");
  }
  if (typeof getToken !== "function") {
    throw new TypeError("createApi requires an async getToken()");
  }
  const base = baseUrl.replace(/\/+$/, "");

  /**
   * Perform the request and hand back the RAW Response — no throw, no parse.
   *
   * For the call sites that branch on a specific status code (202 accepted,
   * 409 conflict, 429 rate-limited, ...) where an exception would destroy the
   * information they need. They still get the Authorization header from one
   * place, which is the point of the client.
   *
   * @param {string} path absolute API path, e.g. "/v1/me"
   * @param {{method?: string, body?: Object|null, headers?: Object, formData?: any}} [opts]
   *   formData — a multipart payload, sent INSTEAD of a JSON body
   * @returns {Promise<Response>}
   */
  async function rawFetch(path, opts = {}) {
    const { method = "GET", body = null, headers = {}, formData = null } = opts;
    const finalHeaders = { ...headers };
    const token = await getToken();
    // A null token is not an error here: the server answers 401 and the caller
    // decides. Sending "Bearer null" would be worse — it looks like a credential.
    if (token) finalHeaders.Authorization = `Bearer ${token}`;
    const init = { method, headers: finalHeaders };
    if (formData) {
      // Content-Type is deliberately NOT set: only the browser knows the
      // multipart boundary it is about to generate, and naming the type by hand
      // produces a request the server cannot parse — with a 422 that blames the
      // file rather than the header.
      init.body = formData;
    } else if (body !== null && body !== undefined) {
      finalHeaders["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    return fetch(`${base}${path}`, init);
  }

  /**
   * Perform the request, throw on non-2xx, return the parsed JSON body.
   *
   * The thrown message is exactly the shape the surfaces already produced
   * (`HTTP 403: <first 200 chars>`), so existing catch/alert paths keep working
   * unchanged. A 204 or an empty body resolves to null instead of blowing up on
   * a JSON parse of "".
   *
   * @param {string} path
   * @param {{method?: string, body?: Object|null, headers?: Object}} [opts]
   * @returns {Promise<any|null>}
   */
  async function request(path, opts = {}) {
    const r = await rawFetch(path, opts);
    if (!r.ok) {
      const text = await r.text().catch(() => "");
      throw new Error(`HTTP ${r.status}: ${text.slice(0, 200)}`);
    }
    if (r.status === 204) return null;
    const text = await r.text();
    if (!text) return null;
    return JSON.parse(text);
  }

  return {
    rawFetch,
    request,

    // ---- Identity ----
    me: () => request("/v1/me"),
    myTeams: () => request("/v1/teams/my-teams"),

    // ---- Realtime ----
    // Returns {token, ws_url} — the surface uses the ws_url it is GIVEN and
    // never hardcodes a realtime origin (D-27-03).
    centrifugoToken: () => request("/v1/me/centrifugo-token", { method: "POST" }),

    // ---- Team chat ----
    listMessages: (teamId, { before = null, limit = 50 } = {}) =>
      request(
        before
          ? `/v1/teams/${teamId}/messages?before=${encodeURIComponent(before)}&limit=${limit}`
          : `/v1/teams/${teamId}/messages?limit=${limit}`,
      ),
    postMessage: (teamId, body) =>
      request(`/v1/teams/${teamId}/messages`, { method: "POST", body }),
    agentAliases: (teamId) => request(`/v1/teams/${teamId}/agent-aliases`),
    // Which model would answer right now, and who pays. The SERVER's answer:
    // the same resolution the agent itself runs, so a status and the behaviour
    // it describes cannot drift apart.
    agentRoute: (teamId) => request(`/v1/me/agent-route?team_id=${teamId}`),
    markRead: (teamId) =>
      request(`/v1/teams/${teamId}/mark-read`, { method: "POST" }),
    unreadSummary: (teamId) => request(`/v1/teams/${teamId}/unread-summary`),

    // ---- Roster ----
    // Two shapes on purpose. `members` throws on a bad status, which is what a
    // caller filling a name cache wants; `membersRaw` hands back the Response
    // for the people panel, which prints the status code it got.
    members: (teamId) => request(`/v1/teams/${teamId}/members`),
    membersRaw: (teamId) => rawFetch(`/v1/teams/${teamId}/members`),

    // ---- Link nudges (Phase 22) ----
    //
    // Raw: every caller branches on 202 vs 403 vs 422 vs 429 to say something
    // specific, and each of those means a different thing to the person who
    // pressed the button. An exception would collapse them into one sentence.
    nudgeOpenRaw: (teamId, targetUserId, url) =>
      rawFetch(`/v1/teams/${teamId}/nudge-open`, {
        method: "POST",
        body: { target_user_id: targetUserId, url },
      }),

    // ---- Growing a team (Phase 25, JOINCODE-01) ----
    //
    // All raw, all for the same reason: the SERVER is the authority on who may
    // do these things, and its rejection codes are the message. A 403 on a mint
    // is "you are not an admin", a 404 on an email invite is "that person has no
    // account yet, send them the link instead", and a 404 on a join is the
    // deliberate no-oracle answer for invalid/expired/revoked/used-up.
    mintInviteCodeRaw: (teamId) =>
      rawFetch(`/v1/teams/${teamId}/invite-codes`, { method: "POST", body: {} }),
    inviteByEmailRaw: (teamId, email, role = "member") =>
      rawFetch(`/v1/teams/${teamId}/invite`, {
        method: "POST",
        body: { email, role },
      }),
    joinByCodeRaw: (code) =>
      rawFetch("/v1/teams/join-by-code", { method: "POST", body: { code } }),
    createTeamRaw: (slug, displayName) =>
      rawFetch("/v1/teams/self", {
        method: "POST",
        body: { slug, display_name: displayName },
      }),

    // ---- Importing a past conversation ----
    //
    // ONE call site for the whole feature, and one place that spells the path,
    // the scope header and the body. The server half of this is being written
    // in parallel, so a rename on that side has to cost a single edit HERE —
    // not a hunt through a settings sheet, a share handler and a setup screen
    // that each learned the shape for themselves.
    //
    // Raw, for the reason the invite calls are raw: the SERVER decides what a
    // failure means, and the codes mean different things to the person holding
    // the file. A 404 is "this build has no importer yet", a 413 is a file they
    // can split, a 422 is a transcript that did not parse, a 403 is the wrong
    // team. Collapsing those into one thrown sentence leaves every one of them
    // reading "could not import".
    //
    // @param {string} teamSlug an import lands in ONE team; guessing is wrong
    // @param {{format: string, content: string, projectScope?: string|null,
    //          force?: boolean}} payload
    importTranscriptRaw: (teamSlug, { format, content, projectScope, force } = {}) =>
      rawFetch(IMPORT_TRANSCRIPT_PATH, {
        method: "POST",
        headers: { [IMPORT_TEAM_HEADER]: teamSlug },
        body: importTranscriptBody({ format, content, projectScope, force }),
      }),

    // The credential the iOS Shortcut carries, minted on request and returned in
    // plaintext exactly once. It is capability-scoped to importing and bound to
    // ONE team — minting requires membership of that team, which is why the
    // scope is a parameter and not an afterthought.
    //
    // Raw again, and for a sharper reason: a build whose API has not been
    // redeployed answers 404, and the setup screen must degrade to instructions
    // rather than throw inside the settings sheet.
    mintImportTokenRaw: (teamScope, name) =>
      rawFetch(IMPORT_TOKEN_PATH, {
        method: "POST",
        body: { team_scope: teamScope, name },
      }),

    // ---- Media ----
    uploadMediaRaw,

    /**
     * What text an uploaded attachment actually contributed to the brain.
     *
     * ONE request for either kind: the server resolves an image's description
     * child and a document's body chunks behind this path, so a surface never
     * stitches parent → child itself (which would be two round trips per hover,
     * per attachment). Team-scoped through the same X-Team-Scope header the
     * upload uses, so it takes the team SLUG rather than the chat's team id.
     *
     * Throws on a non-2xx, which is what the caller wants: a 403 (blocked
     * member), a 404 (wrong team / no such item) and a 500 are all "no answer",
     * and the tooltip says so rather than rendering an empty box.
     *
     * @param {string} teamSlug
     * @param {string} itemId the media item id from `metadata.media.item_id`
     * @returns {Promise<{state: string, kind: string|null, text: string,
     *                    truncated: boolean, chunk_total: number|null,
     *                    detail: string}>}
     */
    indexedText: (teamSlug, itemId) =>
      request(`/v1/media/${encodeURIComponent(itemId)}/indexed-text`, {
        headers: { "X-Team-Scope": teamSlug },
      }),

    /**
     * Upload a file and get the media item back.
     *
     * @param {string} teamSlug
     * @param {File|Blob} file
     * @param {string} sourceSurface which product sent it, for provenance
     * @returns {Promise<{item_id: string, mime: string, size: number}>}
     */
    uploadMedia: async (teamSlug, file, sourceSurface) => {
      const r = await uploadMediaRaw(teamSlug, file, {
        source_surface: sourceSurface,
        truth_level: "WORKING",
      });
      if (!r.ok) {
        const text = await r.text().catch(() => "");
        throw new Error(`HTTP ${r.status}: ${text.slice(0, 200)}`);
      }
      return r.json();
    },
  };

  /**
   * The ONE place in the product that builds a multipart upload.
   *
   * Returns the RAW Response: one caller branches on 413 vs 201 to say
   * something specific, and an exception would have thrown that away. The
   * throwing convenience wrapper sits on top of this rather than beside it.
   *
   * The team is identified by its SLUG, not its id: /v1/media/upload is
   * team-scoped through X-Team-Scope, unlike the chat endpoints which take the
   * id in the path. Passing the id uploads into a scope that does not exist.
   *
   * @param {string} teamSlug
   * @param {File|Blob} file
   * @param {Object<string,string>} [fields] extra multipart fields
   * @returns {Promise<Response>}
   */
  async function uploadMediaRaw(teamSlug, file, fields = {}) {
    const form = new FormData();
    form.append("file", file);
    for (const [k, v] of Object.entries(fields)) form.append(k, v);
    return rawFetch("/v1/media/upload", {
      method: "POST",
      headers: { "X-Team-Scope": teamSlug },
      formData: form,
    });
  }
}

/**
 * The upload ceiling, mirrored from the backend constant.
 *
 * Checked client-side so a 25 MB video fails in a tenth of a second with a
 * readable line instead of after a minute of upload and a 413 nobody surfaces.
 * It is NOT the enforcement — the server's copy is.
 */
export const MAX_MEDIA_BYTES = 25 * 1024 * 1024;

/* ==========================================================================
 * Importing a past conversation into a team brain
 *
 * EVERYTHING THE SERVER SEES IS DECLARED IN THIS BLOCK. The route, the header,
 * the two format names and the body shape — nothing else in either surface
 * spells any of them. That is the whole point: the server half is being built
 * in parallel and the names below are an ASSUMPTION, so reconciling them has to
 * be an edit here and a re-sync, never a search.
 * ======================================================================== */

/** Where a transcript is posted. */
export const IMPORT_TRANSCRIPT_PATH = "/v1/import/transcript";

/** Where the iOS Shortcut's dedicated credential is minted. */
export const IMPORT_TOKEN_PATH = "/v1/me/import-tokens";

/**
 * The scope header. An import lands in exactly one team, and the same header
 * the media path uses is what says which one — the body carries no team at all,
 * so there is no second place for the two to disagree.
 */
export const IMPORT_TEAM_HEADER = "X-Team-Scope";

/**
 * The formats the server parses. The client NEVER parses a transcript: the
 * parsers live server-side, are tested there, and a second implementation would
 * drift within a release. All this list does is name what may be declared.
 */
export const IMPORT_FORMATS = ["claude-code", "chatgpt"];

/**
 * The format the server works out for itself, and the default everywhere.
 *
 * Its detection is the same code that parses the file, so it cannot disagree
 * with the parser the way a client-side guess can. The named formats stay
 * available as an override for the case where a person knows better.
 */
export const IMPORT_FORMAT_AUTO = "auto";

/**
 * The URL for the OTHER accepted body shape: the transcript posted as raw text,
 * with the format in the query.
 *
 * This is the iOS Shortcut's path. Shortcuts can attach the Share Sheet input
 * to a request as text in one step; building a JSON object around it is three
 * more steps and the place people give up. Both shapes reach the same route, so
 * there is no second endpoint to keep alive.
 *
 * @param {string} [format]
 * @returns {string}
 */
export function importTranscriptTextPath(format = IMPORT_FORMAT_AUTO) {
  return `${IMPORT_TRANSCRIPT_PATH}?format=${encodeURIComponent(format)}`;
}

/**
 * The content type that selects that shape. Anything other than JSON makes the
 * whole body the transcript, and this is the honest name for a pasted chat.
 */
export const IMPORT_TEXT_CONTENT_TYPE = "text/plain";

/**
 * The transcript ceiling, mirrored from the server's own 413 threshold, and
 * checked BEFORE the file is read.
 *
 * A full ChatGPT export is tens of megabytes; reading one into a string on a
 * phone is how the tab gets killed with no message at all. Refusing on
 * `file.size` costs nothing and explains itself; a 413 after a minute of upload
 * does neither. It is NOT the enforcement — the server's copy is.
 */
export const MAX_IMPORT_BYTES = 25 * 1024 * 1024;

/**
 * The request body, in one place.
 *
 * `project_scope` is sent explicitly rather than omitted: it is part of the
 * tagging contract every stored item carries, and a field that only ever
 * appears by default is a field nobody remembers exists.
 *
 * `force` completes a half-finished import. It is safe to repeat — the server
 * dedupes at turn level, so this is an upsert and not a second copy — and it is
 * the only way out of a conversation that was interrupted partway through.
 *
 * @param {{format: string, content: string, projectScope?: string|null, force?: boolean}} payload
 * @returns {{format: string, content: string, project_scope: string|null, force: boolean}}
 */
export function importTranscriptBody({
  format,
  content,
  projectScope = null,
  force = false,
} = {}) {
  return { format, content, project_scope: projectScope, force: Boolean(force) };
}

/** First numeric value among `keys`, or null when the body names none of them. */
function firstCount(data, keys) {
  for (const key of keys) {
    const value = data[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return null;
}

/**
 * Read the 202 an import answers with.
 *
 * The server reports per-conversation verdicts plus a `totals` block:
 *
 *   {status, format, team_scope, truth_level, truncated,
 *    conversations: [{dedupe_key, status, turns, queued, import_id, title}],
 *    totals: {conversations, imported, duplicates, over_limit, turns, queued}}
 *
 * WHAT MUST SURVIVE THE READ: what was written, and what was skipped because it
 * was already there. Without the second number, "nothing happened" and "you
 * already imported this" are the same blank screen, and the person imports it a
 * third time. `over_limit` and `truncated` join them for the same reason — a
 * conversation silently dropped for being too long is worse than one refused
 * out loud.
 *
 * Totals are read first and the top level second, so a server that flattens the
 * shape later still reports rather than falling silent. `reported` is the
 * honest flag: false means nothing countable came back, which is NOT zero.
 *
 * @param {any} body the parsed JSON response
 * @returns {{status: string|null, conversations: number|null, imported: number|null,
 *            duplicates: number|null, overLimit: number|null, turns: number|null,
 *            queued: number|null, truncated: boolean, reported: boolean}}
 */
export function summarizeImport(body) {
  const data = body && typeof body === "object" ? body : {};
  const totals = data.totals && typeof data.totals === "object" ? data.totals : {};
  const pick = (keys) => {
    const inTotals = firstCount(totals, keys);
    return inTotals === null ? firstCount(data, keys) : inTotals;
  };

  const conversations = pick(["conversations"]);
  const imported = pick(["imported", "imported_conversations", "conversations_imported"]);
  const duplicates = pick(["duplicates", "skipped_duplicates", "skipped"]);
  const overLimit = pick(["over_limit", "over_limit_conversations"]);
  const turns = pick(["turns", "imported_turns", "turns_imported"]);
  const queued = pick(["queued"]);

  return {
    status: typeof data.status === "string" ? data.status : null,
    conversations,
    imported,
    duplicates,
    overLimit,
    turns,
    queued,
    truncated: data.truncated === true,
    reported:
      imported !== null ||
      duplicates !== null ||
      turns !== null ||
      conversations !== null,
  };
}

/**
 * Did the server refuse this as something it already holds?
 *
 * Separate from the counts because it drives a control, not a sentence: a
 * duplicate verdict is the one outcome with a next step (re-import with
 * `force`), and a conversation interrupted halfway through is otherwise stuck
 * looking exactly like one that imported cleanly.
 *
 * @param {{status: string|null, imported: number|null, duplicates: number|null}} summary
 * @returns {boolean}
 */
export function isDuplicateImport(summary) {
  if (!summary) return false;
  if (summary.status === "duplicate") return true;
  return (summary.duplicates || 0) > 0;
}
