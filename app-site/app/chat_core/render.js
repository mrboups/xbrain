/**
 * Message-bubble rendering for the team chat (Phase 27, D-27-04).
 *
 * Moved out of the extension popup so both surfaces build the SAME DOM from the
 * same code. Nothing here knows which surface it runs on: the document, the two
 * container elements, the media origin and the identity lookups are all injected
 * through `createRenderer(opts)`.
 *
 * Used by:
 *   - chrome-extension/popup.js (via chrome-extension/chat_core/)
 *   - app-site/app/ — the PWA (via app-site/app/chat_core/)
 *   - chrome-extension/tests/test_chat_core_render.mjs
 *
 * XSS discipline (T-20-03-01 / T-27-02-01): message content, agent names,
 * filenames and source rows are attacker-influencable strings. EVERY one of them
 * reaches the document through createElement + textContent / createTextNode.
 * This file assigns no markup string anywhere — a grep asserts it, and a test
 * asserts that a payload like `<img src=x onerror=...>` lands as text. Do not
 * "simplify" a node build into a template string.
 */

import {
  authorLabel,
  bubbleClass,
  provenanceLabel,
  brainSummaryLabel,
  savedToBrainLabel,
  formatRelative,
  sameDay,
  dayLabel,
} from "./chat_stream.js";

/**
 * Build the renderer for one chat surface.
 *
 * @param {{
 *   doc: Document,
 *   listEl: Element,
 *   scrollEl: Element,
 *   apiBase: string,
 *   getSelfUserId: () => (string|undefined),
 *   getNameCache: () => Object,
 *   onAuthorClick: ((userId: string) => void)|null
 * }} opts
 *   doc            — the document the nodes are created in
 *   listEl         — the message list container (rows are appended here)
 *   scrollEl       — the scrolling viewport that wraps listEl
 *   apiBase        — memory-api origin, prefixed onto the server-minted relative
 *                    media path. No origin literal lives in this module.
 *   getSelfUserId  — the signed-in user's id, read late (it arrives after boot)
 *   getNameCache   — author_user_id -> display name map, read late
 *   onAuthorClick  — optional affordance: click a teammate's name to act on them.
 *                    `null` means the surface does not ship it.
 * @returns {{clear: Function, renderMessage: Function, renderAgentBubble: Function,
 *            buildBubbleNode: Function, syncDaySeparators: Function,
 *            streamTextTarget: Function, scrollToBottom: Function}}
 */
export function createRenderer(opts) {
  const cfg = opts || {};
  const doc = cfg.doc;
  const listEl = cfg.listEl;
  const scrollEl = cfg.scrollEl;
  if (!doc || typeof doc.createElement !== "function") {
    throw new TypeError("createRenderer requires opts.doc");
  }
  if (!listEl) throw new TypeError("createRenderer requires opts.listEl");

  const apiBase = typeof cfg.apiBase === "string" ? cfg.apiBase : "";
  const getSelfUserId =
    typeof cfg.getSelfUserId === "function" ? cfg.getSelfUserId : () => undefined;
  const getNameCache =
    typeof cfg.getNameCache === "function" ? cfg.getNameCache : () => ({});
  const onAuthorClick =
    typeof cfg.onAuthorClick === "function" ? cfg.onAuthorClick : null;

  // The surface's view, resolved from the injected document rather than a global
  // so this module holds no reference to the ambient browser object. Both the
  // frame scheduler and the "open the full image" action hang off it, and both
  // degrade to something sane when it is absent (node tests, detached document).
  const view = doc.defaultView || null;
  const raf =
    view && typeof view.requestAnimationFrame === "function"
      ? (fn) => view.requestAnimationFrame(fn)
      : (fn) => fn();

  /** The row for a given message id, scoped to this surface's list. */
  function rowFor(messageId) {
    return listEl.querySelector(`[data-msg-id="${messageId}"]`);
  }

  /**
   * Empty the message list.
   *
   * Removes children one by one rather than assigning a markup string: this
   * module is grep-asserted free of markup assignment, so nobody can later reach
   * for the "convenient" version on a node that DOES carry untrusted data.
   */
  function clear() {
    while (listEl.firstChild) listEl.removeChild(listEl.firstChild);
  }

  function renderMessage(msg, { prepend = false } = {}) {
    if (rowFor(msg.id)) return; // de-dupe
    const wrapper = buildBubbleNode(msg);
    if (prepend) listEl.insertBefore(wrapper, listEl.firstChild);
    else listEl.appendChild(wrapper);
    // Grouping depends on the row AFTER this one, so inserting anywhere can
    // change a neighbour's answer. Reconciled here rather than left to the
    // caller: a live arrival that forgot to re-run it would strand the avatar
    // on the second-to-last bubble of the run, permanently.
    syncAvatarGrouping();
  }

  function renderAgentBubble({ id, agent_name, routed_via, streaming }) {
    if (rowFor(id)) return;
    const wrapper = buildBubbleNode({
      id,
      kind: "agent",
      agent_name,
      routed_via,
      content: "",
      created_at: new Date().toISOString(),
    });
    if (streaming) {
      wrapper.querySelector(".xb-msg-bubble").classList.add("streaming");
    }
    listEl.appendChild(wrapper);
    syncDaySeparators(); // which reconciles the grouping too
  }

  function buildBubbleNode(msg) {
    // Row layout (mockup .row) — avatar | (meta + bubble [+ savetag]).
    // .is-self mirrors the columns so the bubble hugs the right edge.
    const self = getSelfUserId();
    const nameCache = getNameCache();
    const rowClass = bubbleClass(msg, self); // is-self / is-user / is-agent
    const wrapper = doc.createElement("div");
    wrapper.className = `xb-msg ${rowClass}`;
    wrapper.dataset.msgId = msg.id;
    // Day-separator input (syncDaySeparators reads this off the DOM).
    if (msg.created_at) wrapper.dataset.createdAt = msg.created_at;
    // Who this row belongs to, for run detection. Read off the DOM by
    // syncAvatarGrouping so grouping stays correct for rows that were built
    // before the ones around them arrived.
    wrapper.dataset.authorKey = authorKey(msg);

    // Avatar — letter for users, 🤖 for agents.
    //
    // YOUR OWN messages get none at all. They are right-aligned, and that
    // alignment already says whose they are; a second marker on every row is
    // noise, and it is your own face on every line of your own chat.
    if (rowClass !== "is-self") {
      const avatar = doc.createElement("div");
      avatar.className = "xb-msg-avatar";
      if (msg.kind === "agent") {
        avatar.textContent = "🤖";
      } else {
        const label = authorLabel({ msg, selfUserId: self, nameCache });
        avatar.textContent = (label[0] || "?").toUpperCase();
      }
      wrapper.appendChild(avatar);
    }

    // Meta row (sender + time + provenance)
    const meta = doc.createElement("div");
    meta.className = "xb-msg-meta";

    const author = doc.createElement("span");
    author.className = "xb-msg-author";
    if (msg.kind === "agent") author.classList.add("is-agent");
    author.textContent = authorLabel({ msg, selfUserId: self, nameCache });
    // Click a teammate's name to act on THEM: opens the people overlay with their row
    // highlighted, so sending a link or a file starts from the message you are reading
    // instead of reopening a list and finding them again. Skipped for the agent and for
    // your own messages — neither is someone you send things to.
    //
    // A surface that ships no people overlay passes onAuthorClick: null, and then the
    // name gets NO cursor, NO title and NO listener — a name that looks clickable and
    // does nothing is worse than a plain one.
    if (
      onAuthorClick &&
      msg.kind !== "agent" &&
      msg.author_user_id &&
      msg.author_user_id !== self
    ) {
      author.style.cursor = "pointer";
      author.title = "Send them a link or a file";
      author.addEventListener("click", () => onAuthorClick(msg.author_user_id));
    }
    meta.appendChild(author);

    const time = doc.createElement("span");
    time.className = "xb-msg-time";
    time.textContent = formatRelative(msg.created_at);
    time.title = new Date(msg.created_at).toLocaleString();
    meta.appendChild(time);

    const prov = provenanceLabel(msg.routed_via);
    if (prov) {
      const provSpan = doc.createElement("span");
      provSpan.className = `xb-msg-provenance ${prov.cls}`;
      provSpan.textContent = prov.text;
      meta.appendChild(provSpan);
    }
    // Body — the bubble (own = --primary, others = --muted, agent = --card).
    const body = doc.createElement("div");
    body.className = "xb-msg-bubble";

    // Telegram-style: the name + timestamp live INSIDE the bubble, not floating on a
    // line above it. Previously the meta row sat in its own grid row, which left the
    // bubble visually detached from the name it belonged to.
    body.appendChild(meta);

    // Agent block label (mockup .agent-bubble .who) — mono uppercase, styled by
    // CSS. Lives inside the bubble but OUTSIDE .xb-msg-text so the streaming
    // writer can replace the text without wiping the label.
    if (msg.kind === "agent") {
      const who = doc.createElement("div");
      who.className = "xb-msg-agent-label";
      who.textContent = "agent · from your brain";
      body.appendChild(who);
    }

    // BL-003 Slice 4 — render media inline when the message carries a media
    // attachment. The URL is a server-minted signed path (/v1/media/{id}/img?t=...)
    // so no Bearer header is needed for the <img src>.
    if (msg.metadata && msg.metadata.media && msg.metadata.media.item_id) {
      renderMediaInto(body, msg.metadata.media);
      // Also show the caption/filename as small text below the attachment.
      if (msg.content) {
        const caption = doc.createElement("div");
        caption.className = "xb-msg-caption";
        caption.textContent = msg.content;
        body.appendChild(caption);
      }
    } else {
      // Text lives in its own span — the target the agent stream writes into.
      const text = doc.createElement("span");
      text.className = "xb-msg-text";
      text.textContent = msg.content || "";
      body.appendChild(text);
    }

    // Sources disclosure — driven by the REAL memory_items count the agent
    // pipeline persisted. Renders nothing when the agent used no brain items.
    if (msg.kind === "agent") {
      const summaryLabel = brainSummaryLabel(msg.metadata);
      if (summaryLabel) {
        body.appendChild(buildSourcesNode(summaryLabel, msg.metadata.sources));
      }
    }

    wrapper.appendChild(body);

    // Saved-to-brain badge — only on a genuine indexed-attachment signal. An
    // absent badge is correct; a fabricated one would be a spoof.
    const savedLabel = savedToBrainLabel(msg);
    if (savedLabel) {
      const tag = doc.createElement("span");
      tag.className = "xb-msg-savetag";
      tag.textContent = savedLabel;
      wrapper.appendChild(tag);
    }

    return wrapper;
  }

  /**
   * Build the agent's `<details>` sources disclosure.
   *
   * The summary line is the real memory_items count. Individual source rows are
   * rendered ONLY when the server actually sends `metadata.sources` — the backend
   * does not emit it today, so this stays future-proof and fabricates nothing
   * (threat T-20-03-02 / T-27-02-02). Truth levels come from the payload; the chip
   * styling is selected by data-level, never hardcoded.
   *
   * @param {string} summaryLabel - e.g. "2 sources from the brain"
   * @param {Array<object>|undefined} sources - server-sent rows, if any
   * @returns {HTMLDetailsElement}
   */
  function buildSourcesNode(summaryLabel, sources) {
    const details = doc.createElement("details");
    details.className = "xb-msg-sources";

    const summary = doc.createElement("summary");
    summary.textContent = summaryLabel;
    details.appendChild(summary);

    if (Array.isArray(sources)) {
      for (const s of sources) {
        const row = doc.createElement("div");
        row.className = "xb-msg-src";

        const level =
          s && typeof s.truth_level === "string" ? s.truth_level.toLowerCase() : null;
        if (level) {
          const chip = doc.createElement("span");
          chip.className = "xb-msg-chip";
          chip.dataset.level = level;
          chip.textContent = level;
          row.appendChild(chip);
        }

        const label = doc.createElement("span");
        label.textContent = (s && (s.text || s.title)) || "";
        row.appendChild(label);

        details.appendChild(row);
      }
    }

    return details;
  }

  /**
   * Reconcile day separators across the whole thread.
   *
   * Recomputed from the rows currently in the DOM (rather than tracked
   * incrementally) so it stays correct for the append path, the prepend
   * pagination path, and live inserts alike. Separators carry no data-msg-id, so
   * the de-dupe lookup in renderMessage never sees them.
   */
  function syncDaySeparators() {
    for (const sep of Array.from(listEl.querySelectorAll(".xb-msg-daysep"))) {
      sep.remove();
    }
    let prevIso = null;
    for (const row of Array.from(listEl.children)) {
      const iso = row.dataset ? row.dataset.createdAt : null;
      if (!iso) continue;
      if (!prevIso || !sameDay(prevIso, iso)) {
        const sep = doc.createElement("div");
        sep.className = "xb-msg-daysep";
        sep.textContent = dayLabel(iso);
        listEl.insertBefore(sep, row);
      }
      prevIso = iso;
    }
    // Separators BREAK runs, and they are inserted here — so grouping has to be
    // recomputed after they land, not before. Without this, a batch that
    // rendered rows first and separators second would leave the last row above
    // a date heading grouped, with its avatar stranded on the row below it.
    syncAvatarGrouping();
  }

  /**
   * Reconcile Telegram-style avatar grouping across the whole thread.
   *
   * THE RULE: a run of consecutive messages from one author shows the avatar
   * ONCE, on the LAST message of the run — the newest bubble, at the bottom of
   * the group, where the eye already is. Six messages from one teammate render
   * five avatar-less rows and one with the avatar, not six identical faces.
   *
   * Recomputed from the DOM rather than tracked incrementally, for the same
   * reason syncDaySeparators is: one function has to be correct for the append
   * path, the prepend pagination path AND live inserts, and the answer for any
   * row depends on the row that comes AFTER it — which may not have existed
   * when that row was built.
   *
   * A day separator ENDS a run. Two messages either side of midnight are not
   * visually consecutive, and stranding the group's only avatar above a date
   * heading reads as a rendering fault.
   *
   * Grouped rows keep their gutter (the avatar is hidden, not removed) so every
   * bubble in a run stays on the same left edge. Removing it would shift the
   * whole run left and turn a quiet visual grouping into a jagged one.
   */
  function syncAvatarGrouping() {
    const rows = Array.from(listEl.children);
    for (let i = 0; i < rows.length; i += 1) {
      const row = rows[i];
      if (!row.dataset || !row.dataset.msgId) continue; // a separator
      // The next element decides. A separator, or the end of the list, means
      // this row is the tail of its run.
      const after = rows[i + 1];
      const next = after && after.dataset && after.dataset.msgId ? after : null;
      const isTail = !next || next.dataset.authorKey !== row.dataset.authorKey;
      if (isTail) row.classList.remove("is-grouped");
      else row.classList.add("is-grouped");
    }
  }

  /**
   * Render a media attachment (image or document chip) into an existing element.
   * All DOM construction uses createElement/createTextNode — never a markup
   * string with interpolated user data — to stay XSS-safe.
   *
   * @param {HTMLElement} el   - Target container element (the bubble body div).
   * @param {object}      media - {item_id, mime, size, filename, url?}
   */
  function renderMediaInto(el, media) {
    // Build the absolute URL from the server-minted relative path. The origin is
    // the injected apiBase — this module names no host of its own.
    const src = media.url ? `${apiBase}${media.url}` : null;

    if (media.mime && media.mime.startsWith("image/") && src) {
      // Image: render a thumbnail that opens the full image in a new tab.
      const img = doc.createElement("img");
      img.className = "xb-msg-thumb";
      img.alt = media.filename || "image";
      img.src = src;
      img.addEventListener("click", () => {
        if (view && typeof view.open === "function") {
          view.open(src, "_blank", "noopener");
        }
      });
      el.appendChild(img);
    } else {
      // Document: render a file chip with filename + size.
      const chip = doc.createElement("a");
      chip.className = "xb-msg-file-chip";
      chip.href = src || "#";
      chip.target = "_blank";
      chip.rel = "noopener";

      const icon = doc.createTextNode("📄 ");
      chip.appendChild(icon);

      const nameNode = doc.createTextNode(media.filename || "file");
      chip.appendChild(nameNode);

      if (media.size) {
        const sizeNode = doc.createTextNode(` (${formatBytes(media.size)})`);
        chip.appendChild(sizeNode);
      }
      el.appendChild(chip);
    }
  }

  /**
   * Resolve where an agent stream should write its text.
   *
   * The bubble now also holds the agent label and the sources disclosure, so the
   * stream writes into the dedicated .xb-msg-text span instead of replacing the
   * whole bubble's textContent (which would wipe them). Falls back to the bubble
   * itself for any row built before the span existed.
   *
   * @param {string} messageId
   * @returns {HTMLElement|null}
   */
  function streamTextTarget(messageId) {
    const row = rowFor(messageId);
    const bubble = row ? row.querySelector(".xb-msg-bubble") : null;
    if (!bubble) return null;
    return bubble.querySelector(".xb-msg-text") || bubble;
  }

  /**
   * Drop the streaming class on a finished/failed agent bubble.
   * @param {string} messageId
   */
  function clearStreaming(messageId) {
    const row = rowFor(messageId);
    const bubble = row ? row.querySelector(".xb-msg-bubble") : null;
    if (bubble) bubble.classList.remove("streaming");
  }

  function scrollToBottom({ force = false } = {}) {
    const el = scrollEl;
    if (!el) return;
    // Force=true: always pin to bottom (use this on initial chat load so the
    // user lands on the latest message right above the composer).
    // Force=false (default): only auto-scroll if the user is near the bottom —
    // don't yank them up if they scrolled history to read something older.
    if (force) {
      // Defer to next frame so layout has settled (avatar grid + body sizes).
      raf(() => {
        el.scrollTop = el.scrollHeight;
      });
      return;
    }
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    if (nearBottom) {
      el.scrollTop = el.scrollHeight;
    }
  }

  return {
    clear,
    renderMessage,
    renderAgentBubble,
    buildBubbleNode,
    syncDaySeparators,
    syncAvatarGrouping,
    streamTextTarget,
    clearStreaming,
    scrollToBottom,
  };
}

/**
 * Who a row belongs to, for run detection.
 *
 * The prefix matters: a teammate whose user id happened to equal an agent's
 * name would otherwise group with it. Agents are keyed by NAME so two different
 * agents answering in sequence each keep their own avatar.
 *
 * @param {{kind?: string, agent_name?: string, author_user_id?: string}} msg
 * @returns {string}
 */
function authorKey(msg) {
  if (msg.kind === "agent") return `agent:${msg.agent_name || "agent"}`;
  return `user:${msg.author_user_id || ""}`;
}

/**
 * Format a byte count into a compact human-readable string (KB / MB).
 * @param {number} n - byte count
 * @returns {string}
 */
function formatBytes(n) {
  if (n == null || n < 0) return "?";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}
