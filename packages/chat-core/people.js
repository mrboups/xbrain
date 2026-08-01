/**
 * The people panel — who is on the team, who is connected right now, and the one
 * place a link or a file is pushed to somebody (Phase 27, D-27-04).
 *
 * Lifted out of chrome-extension/popup.js so the PWA drives the SAME code. The
 * behaviour here is not obvious enough to survive being written twice: presence
 * is best-effort and must never fail the list, a self-nudge is a 422 that is
 * EXPECTED rather than an error, an upload is nudged by its signed URL because
 * the recipient's browser cannot send a Bearer header, and every rejection code
 * means something different to the person who pressed the button.
 *
 * Nothing here knows which surface it runs on. The document, the elements, the
 * API client, the media origin, the platform shim and the team lookups are all
 * injected.
 *
 * THE ONE REAL PLATFORM DIFFERENCE lives in `platform.currentPageUrl()`. In the
 * extension it is the tab the user is looking at, so "send this page to that
 * person" needs no typing. On the web it is null, and the panel then asks for a
 * link instead of pretending to have one. Both paths are visible in the copy the
 * panel writes, so nobody is offered a button that sends nothing.
 *
 * XSS discipline: member names, emails and server strings all reach the document
 * through createElement + textContent. This file assigns no markup string.
 */

import { isSafeHttpUrl } from "./nudge_open.js";
import { setStatusLine, clearChildren } from "./dom.js";

/**
 * Map the nudge-open endpoint's rejection codes to clear English.
 *
 * Every one of these is actionable, which is why they are not collapsed into
 * "could not send": 403 means the roster is stale, 422 means the link itself was
 * refused (or the target is you), 429 means wait.
 *
 * @param {number} status
 * @returns {string}
 */
export function mapNudgeError(status) {
  if (status === 403) return "That member is not on this team.";
  if (status === 422)
    return "Rejected — that is you, or the link is not a plain http/https URL.";
  if (status === 429) return "Too many link requests — wait a moment and retry.";
  if (status === 404) return "Team not found.";
  if (status === 0) return "Network error — try again.";
  return `Could not send (HTTP ${status}).`;
}

/**
 * Read the connected user ids off a Centrifugo team subscription.
 *
 * Best effort by design: a channel that does not expose presence still renders
 * every row, without a dot. An invented "offline" is worse than an absent one.
 *
 * @param {{presence: () => Promise<{clients?: Object}>}|null} subscription
 * @returns {Promise<Set<string>>}
 */
export async function presentUserIds(subscription) {
  const online = new Set();
  if (!subscription || typeof subscription.presence !== "function") return online;
  try {
    const p = await subscription.presence();
    for (const c of Object.values((p && p.clients) || {})) {
      if (c && c.user) online.add(String(c.user));
    }
  } catch (e) {
    /* presence unavailable on this channel — rows render without a dot */
  }
  return online;
}

/**
 * Build the people panel for one surface.
 *
 * @param {{
 *   doc: Document,
 *   api: Object,
 *   apiBase: string,
 *   platform: {currentPageUrl: () => Promise<string|null>},
 *   els: {panel: Element, list: Element, urlInput: Element, status: Element,
 *         hint?: Element|null, filePicker?: Element|null},
 *   getActiveTeamId: () => (string|null),
 *   getTeams: () => Array<{id: string, slug: string}>,
 *   getTeamSubscription?: (() => Object|null)|null
 * }} opts
 *   apiBase             — memory-api origin, prefixed onto the signed media path
 *                         the upload returns. No origin literal lives here.
 *   els.hint            — optional line under the URL field. The panel writes it,
 *                         because what it should say depends on whether this
 *                         surface can see a current page.
 *   els.filePicker      — optional hidden <input type="file">. A surface that
 *                         does not ship one gets no File button rather than a
 *                         button that does nothing.
 *   getTeamSubscription — optional. The live team subscription, for presence.
 * @returns {{open: Function, close: Function, render: Function, sendToEveryone: Function}}
 */
export function createPeoplePanel(opts) {
  const cfg = opts || {};
  const doc = cfg.doc;
  const api = cfg.api;
  const els = cfg.els || {};
  if (!doc || typeof doc.createElement !== "function") {
    throw new TypeError("createPeoplePanel requires opts.doc");
  }
  if (!api) throw new TypeError("createPeoplePanel requires opts.api");

  const apiBase = typeof cfg.apiBase === "string" ? cfg.apiBase : "";
  const platform = cfg.platform || null;
  const getActiveTeamId =
    typeof cfg.getActiveTeamId === "function" ? cfg.getActiveTeamId : () => null;
  const getTeams = typeof cfg.getTeams === "function" ? cfg.getTeams : () => [];
  const getTeamSubscription =
    typeof cfg.getTeamSubscription === "function" ? cfg.getTeamSubscription : null;

  const say = (text, type) => setStatusLine(els.status, text, type);

  /** The URL to send, trimmed. Empty means the person has not given one. */
  function urlValue() {
    return ((els.urlInput && els.urlInput.value) || "").trim();
  }

  /**
   * One nudge POST. Returns the HTTP status so callers map it themselves; a
   * transport failure is reported as 0, which mapNudgeError names.
   */
  async function nudge(targetUserId, url) {
    try {
      const res = await api.nudgeOpenRaw(getActiveTeamId(), targetUserId, url);
      return res.status;
    } catch (e) {
      return 0;
    }
  }

  /**
   * Fill the URL field with the page the user is on, when the surface HAS one.
   *
   * On the web `currentPageUrl()` answers null and the field stays empty — with
   * the hint below it saying so, because a person looking at an empty field with
   * no explanation assumes the app is broken rather than that it is their turn.
   */
  async function primeUrlField() {
    let current = null;
    if (platform && typeof platform.currentPageUrl === "function") {
      try {
        current = await platform.currentPageUrl();
      } catch (e) {
        current = null;
      }
    }
    if (current && els.urlInput && !els.urlInput.value) {
      els.urlInput.value = current;
    }
    if (els.hint) {
      els.hint.textContent = current
        ? "Defaults to the page you are on. Sending shows the recipient a notification; it only opens if they allowed that."
        : "Paste the link you want to send. Sending shows the recipient a notification; it only opens if they allowed that.";
    }
  }

  /**
   * Send the composed link to ONE member.
   *
   * The scheme is checked here so an obviously bad link fails instantly with a
   * sentence that names the problem. The SERVER re-validates and is the actual
   * boundary — this check is UX, not security.
   */
  async function sendToMember(member, btn) {
    const url = urlValue();
    if (!url) {
      say("Enter a link to send.", "error");
      return;
    }
    if (!isSafeHttpUrl(url)) {
      say("Enter a valid http or https link.", "error");
      return;
    }
    if (btn) btn.disabled = true;
    try {
      const status = await nudge(member.user_id || member.id, url);
      say(
        status === 202
          ? `Sent to ${member.display_name || "them"} ✓`
          : mapNudgeError(status),
        status === 202 ? "success" : "error",
      );
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  /**
   * Send to the whole team.
   *
   * TODO(server): this fans out ONE request per member from the client because
   * /v1/teams/{id}/nudge-open takes a single target_user_id. Fine for a small
   * team, but it burns the caller's rate-limit budget and is not atomic — a
   * proper team-wide nudge belongs server-side. Tracked in .planning/BACKLOG.md.
   */
  async function sendToEveryone(btn) {
    const url = urlValue();
    if (!url) {
      say("Enter a link to send.", "error");
      return;
    }
    if (!isSafeHttpUrl(url)) {
      say("Enter a valid http or https link.", "error");
      return;
    }
    if (btn) btn.disabled = true;
    say("Sending…", "loading");
    try {
      const res = await api.membersRaw(getActiveTeamId());
      const members = res.ok ? await res.json() : [];
      let sent = 0;
      let failed = 0;
      for (const m of members) {
        const id = m.user_id || m.id;
        if (!id) continue;
        const status = await nudge(id, url);
        // The server 422s a self-nudge. That is expected here — you are in your
        // own team's member list — and counting it as a failure would report a
        // successful send to everyone as partially broken, every time.
        if (status === 202) sent += 1;
        else if (status !== 422) failed += 1;
      }
      say(
        failed
          ? `Sent to ${sent}, ${failed} failed.`
          : `Sent to ${sent} teammate${sent === 1 ? "" : "s"} ✓`,
        failed ? "error" : "success",
      );
    } catch (e) {
      say("Network error — try again.", "error");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  /**
   * Send a FILE to one member: upload it to the team, then nudge the signed URL
   * the upload returns.
   *
   * The file goes through the ordinary team media path — it becomes a team
   * memory_item like any other upload — and the recipient gets the same consent
   * notification a link gets. `signed_url` is used rather than `raw_path`
   * because the recipient's browser follows a bare URL and cannot send the
   * Bearer header `raw_path` requires.
   */
  function pickFileFor(member) {
    const picker = els.filePicker;
    if (!picker) return;
    picker.value = ""; // re-picking the same file must re-fire
    picker.onchange = async () => {
      const file = picker.files && picker.files[0];
      if (!file) return;
      // X-Team-Scope is the team SLUG, not its id.
      const team = getTeams().find((t) => t.id === getActiveTeamId());
      if (!team) {
        say("No active team.", "error");
        return;
      }
      say(`Uploading ${file.name}…`, "loading");
      try {
        // The RAW variant, because the branch below needs the status code: an
        // exception would collapse 413 ("too large" — actionable) and 500 into
        // one message.
        const up = await api.uploadMediaRaw(team.slug, file, { caption: file.name });
        if (up.status !== 201) {
          say(
            up.status === 413
              ? "That file is too large (25 MB max)."
              : `Upload failed (HTTP ${up.status}).`,
            "error",
          );
          return;
        }
        const { signed_url } = await up.json();
        if (!signed_url) {
          say("Upload succeeded but returned no shareable link.", "error");
          return;
        }
        const status = await nudge(
          member.user_id || member.id,
          `${apiBase}${signed_url}`,
        );
        say(
          status === 202
            ? `Sent ${file.name} to ${member.display_name || "them"} ✓`
            : mapNudgeError(status),
          status === 202 ? "success" : "error",
        );
      } catch (e) {
        say("Network error — try again.", "error");
      }
    };
    picker.click();
  }

  /**
   * List the team's members, marking who is connected right now.
   *
   * @param {string} [focusUserId] highlight this member's row — the panel was
   *   opened by clicking their name in the chat, so it answers "that one"
   *   instead of making the reader find them again.
   */
  async function render(focusUserId) {
    const list = els.list;
    if (!list) return;
    clearChildren(list);

    let members = [];
    try {
      const res = await api.membersRaw(getActiveTeamId());
      if (!res.ok) {
        say(`Could not load members (HTTP ${res.status}).`, "error");
        return;
      }
      members = await res.json();
    } catch (e) {
      say("Network error loading members.", "error");
      return;
    }

    const online = await presentUserIds(
      getTeamSubscription ? getTeamSubscription() : null,
    );

    for (const m of members) {
      const row = doc.createElement("li");
      row.className = "xb-people-row";
      if (focusUserId && String(m.user_id || m.id) === String(focusUserId)) {
        row.classList.add("is-focused");
      }

      const isOnline = online.has(String(m.source_user_id));
      const dot = doc.createElement("span");
      dot.className = "xb-people-dot" + (isOnline ? " is-online" : "");
      dot.title = isOnline ? "Active now" : "Not active";
      row.appendChild(dot);

      const name = doc.createElement("span");
      name.className = "xb-people-name";
      // textContent — member names are user-supplied strings.
      name.textContent = m.display_name || m.email || m.source_user_id || "member";
      row.appendChild(name);

      const linkBtn = doc.createElement("button");
      linkBtn.type = "button";
      linkBtn.className = "xb-btn-secondary";
      linkBtn.textContent = "Link";
      linkBtn.title = "Send the link above to this person";
      linkBtn.addEventListener("click", () => sendToMember(m, linkBtn));
      row.appendChild(linkBtn);

      // A surface with no file input gets no File button. A control that opens
      // nothing is worse than an absent one.
      if (els.filePicker) {
        const fileBtn = doc.createElement("button");
        fileBtn.type = "button";
        fileBtn.className = "xb-btn-secondary";
        fileBtn.textContent = "File";
        fileBtn.title = "Upload a file and send it to this person";
        fileBtn.addEventListener("click", () => pickFileFor(m));
        row.appendChild(fileBtn);
      }

      list.appendChild(row);
    }

    if (!members.length) say("No members yet.", "");
  }

  /**
   * Show the panel.
   * @param {{focusUserId?: string}} [openOpts]
   */
  async function open(openOpts) {
    const panel = els.panel;
    if (!panel) return;
    if (!getActiveTeamId()) {
      say("No active team.", "error");
      return;
    }
    panel.hidden = false;
    say("", "");
    await primeUrlField();
    await render(openOpts && openOpts.focusUserId);
  }

  function close() {
    if (els.panel) els.panel.hidden = true;
    say("", "");
  }

  return { open, close, render, sendToEveryone };
}
