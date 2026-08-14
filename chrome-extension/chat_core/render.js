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
 *
 * That rule survives markdown: an agent's answer is parsed by markdown.js into
 * nodes THIS process constructs, so `<img src=x onerror=...>` in a message is
 * still characters, not an element. The parser never sees HTML and never emits
 * a string for anything else to parse.
 */

import {
  authorLabel,
  bubbleClass,
  provenanceLabel,
  brainSummaryLabel,
  indexedAttachment,
  indexedTooltipText,
  agentFailureText,
  isAgentUnavailable,
  formatRelative,
  sameDay,
  dayLabel,
} from "./chat_stream.js";
import { renderMarkdownInto } from "./markdown.js";
import { BUBBLE_LABEL, TOOLTIP } from "./brain_tag.js";

/**
 * Does a message body get parsed as markdown, or shown exactly as typed?
 *
 * AGENT ANSWERS YES, PEOPLE NO — and the asymmetry is the decision, not an
 * oversight. The agent emits markdown deliberately, on every turn, so leaving
 * it unparsed is a guaranteed defect: `**Excalibur**` reached the screen with
 * the asterisks in it. A teammate types into a plain box with no preview and no
 * escape hatch, so parsing THEIR text is an occasional silent corruption they
 * cannot see coming and cannot undo — `2 * 3 * 4` losing its middle to italics,
 * a `#hashtag` turning into a heading, `__init__` losing its underscores.
 *
 * Fidelity is the tie-breaker. This chat is team-shared: every member reads
 * every message, and a message that renders differently from how it was typed
 * is a small, repeated lie about what somebody said. The agent has no such
 * claim on its own characters.
 *
 * The cost is real and accepted: a teammate who pastes a bulleted list sees the
 * dashes. That is what they typed.
 *
 * @param {{kind?: string}} msg
 * @returns {boolean}
 */
export function rendersMarkdown(msg) {
  return Boolean(msg) && msg.kind === "agent";
}

/**
 * How far from the end of a thread still counts as reading the newest message.
 *
 * A tolerance rather than an equality: a scroller lands on fractional offsets,
 * an image finishing its decode moves the end by a few pixels, and a person who
 * nudged the list by a line has not stopped following the conversation.
 */
export const NEAR_BOTTOM_PX = 120;

/**
 * Is this scroller at its end — or WAS it, before `absorbed` pixels of it were
 * taken away?
 *
 * THE ONE DEFINITION of "at the bottom" in this product. Auto-scrolling on a new
 * message and re-anchoring after the viewport changes are the same question
 * asked twice, and two copies of the arithmetic drift into two different answers
 * for the same thread.
 *
 * `absorbed` is what makes it usable from a viewport handler. When the on-screen
 * keyboard opens, the shell shrinks and the scroller absorbs the loss: its
 * scrollTop and scrollHeight are untouched while its clientHeight drops, so the
 * measured gap grows by exactly the pixels that went away and a reader who WAS
 * at the end now measures as hundreds of pixels short of it. Subtracting the
 * loss asks the question the caller actually means — "was this person at the
 * bottom before the keyboard took the room" — instead of a question whose answer
 * is always no.
 *
 * @param {{scrollHeight: number, scrollTop: number, clientHeight: number}|null} el
 * @param {number} [absorbed] pixels of viewport the scroller has just lost
 * @returns {boolean}
 */
export function isNearBottom(el, absorbed = 0) {
  if (!el) return false;
  const lost = Number.isFinite(absorbed) ? absorbed : 0;
  return el.scrollHeight - el.scrollTop - el.clientHeight - lost < NEAR_BOTTOM_PX;
}

/**
 * A handler that keeps the newest message in view when the viewport changes
 * size — and only for a reader who was already there.
 *
 * THE BUG IT EXISTS FOR. Tap the composer on a phone and the shell loses about
 * 300px to the on-screen keyboard. The scroller keeps the scrollTop it had, so
 * the message somebody was replying to ends up behind the composer: they tapped
 * the field to answer it and it disappeared. Nothing reacted to a viewport
 * change at all — auto-scroll ran on send, on receive and on load, and a
 * keyboard is none of those.
 *
 * WHAT IT MUST NOT DO is drag somebody back to the present because they tapped
 * the field while reading history. That would be a worse bug than the one being
 * fixed and a harder one to describe, so the decision is `isNearBottom` — the
 * same question, from the same place, that auto-scroll asks.
 *
 * By the time a viewport handler runs, the shell has already shrunk and the
 * scroller has already absorbed the loss: the raw gap reads ~300px even for
 * somebody sitting exactly at the end. `previousHeight` is what makes the answer
 * mean anything — subtracting the pixels that just went away asks about the
 * position they were in a moment ago, which is the only one that expresses
 * intent.
 *
 * @param {{getScrollEl: () => (Element|null), scrollToBottom: Function}} refs
 * @returns {(change: {height: number, previousHeight: number}) => boolean}
 *   the handler; true when it re-anchored, so the decision is observable.
 */
export function createViewportAnchor(refs = {}) {
  const getScrollEl =
    typeof refs.getScrollEl === "function" ? refs.getScrollEl : () => null;
  const scrollToBottom =
    typeof refs.scrollToBottom === "function" ? refs.scrollToBottom : () => {};

  return function viewportChanged(change) {
    const { height = 0, previousHeight = 0 } = change || {};
    const el = getScrollEl();
    if (!el) return false;
    const absorbed = Math.max(0, (previousHeight || height) - height);
    if (!isNearBottom(el, absorbed)) return false;
    // force, because the unforced path re-asks the question against the exact
    // geometry the keyboard has just invalidated, and answers no.
    scrollToBottom({ force: true });
    return true;
  };
}

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
 *   onAuthorClick: ((userId: string) => void)|null,
 *   fetchIndexedText: ((itemId: string) => Promise<Object>)|null
 * }} opts
 *   doc              — the document the nodes are created in
 *   listEl           — the message list container (rows are appended here)
 *   scrollEl         — the scrolling viewport that wraps listEl
 *   apiBase          — memory-api origin, prefixed onto the server-minted relative
 *                      media path. No origin literal lives in this module.
 *   getSelfUserId    — the signed-in user's id, read late (it arrives after boot)
 *   getNameCache     — author_user_id -> display name map, read late
 *   onAuthorClick    — optional affordance: click a teammate's name to act on them.
 *                      `null` means the surface does not ship it.
 *   fetchIndexedText — resolves GET /v1/media/{id}/indexed-text for one item.
 *                      `null` means the surface ships no reveal, and the indexed
 *                      marker is then rendered inert rather than as a control
 *                      that does nothing.
 * @returns {{clear: Function, renderMessage: Function, renderAgentBubble: Function,
 *            buildBubbleNode: Function, syncDaySeparators: Function,
 *            streamTextTarget: Function, writeStreamText: Function,
 *            scrollToBottom: Function}}
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
  const fetchIndexedText =
    typeof cfg.fetchIndexedText === "function" ? cfg.fetchIndexedText : null;

  /**
   * item_id -> the in-flight or settled Promise for its indexed text.
   *
   * The PROMISE is cached, not the value, and that is the whole de-dupe: a
   * pointer crossing the marker fires mouseenter and focus in quick succession,
   * and caching only on resolution would let both start a request. Cached on
   * rejection too — a chat that keeps failing must not retry on every hover.
   *
   * Lives in the renderer closure, so it survives re-renders of the same thread
   * and dies with the surface.
   */
  const indexedTextCache = new Map();

  /** Fetch (at most once per item) and hand back the settled payload, or null. */
  function loadIndexedText(itemId) {
    if (!indexedTextCache.has(itemId)) {
      indexedTextCache.set(
        itemId,
        Promise.resolve()
          .then(() => fetchIndexedText(itemId))
          .catch(() => null), // a failed request is a STATE, rendered as one
      );
    }
    return indexedTextCache.get(itemId);
  }

  /**
   * The one indexed-text tooltip that may be open, and the listeners that close
   * it.
   *
   * WHY A STATE AND NOT JUST :hover. Hover is a desktop mechanism and it works
   * there. On a touch screen a tap fires a synthetic mouseenter and iOS then
   * KEEPS :hover on that element until something else is tapped, so the reveal
   * opens and stays — there is no gesture that closes it, because the one that
   * would (a tap elsewhere) does not reliably reach it. So the stylesheet gates
   * hover behind `@media (hover: hover)` and this drives the touch path
   * explicitly: tap to open, tap again, tap outside, or Escape to close.
   *
   * ONE AT A TIME, and the listeners exist only while one is open. A document
   * handler per rendered marker would grow with the thread and outlive every
   * message in it; two handlers that come and go with the tooltip cannot.
   */
  let openTip = null;
  let tipListeners = null;

  function closeIndexedTip() {
    if (tipListeners) {
      doc.removeEventListener("pointerdown", tipListeners.outside, true);
      doc.removeEventListener("keydown", tipListeners.escape, true);
      tipListeners = null;
    }
    if (!openTip) return;
    const tag = openTip;
    openTip = null;
    tag.classList.remove("is-open");
    // The keyboard path shows the tooltip from :focus-visible, which no class
    // change can override. Escape has to actually give the focus back, or it
    // dismisses the state and leaves the tooltip on screen.
    if (doc.activeElement === tag && typeof tag.blur === "function") tag.blur();
  }

  function openIndexedTip(tag) {
    if (openTip === tag) return;
    closeIndexedTip();
    openTip = tag;
    tag.classList.add("is-open");
    tipListeners = {
      // Capture, so a tap that some other handler stops still closes this.
      outside: (event) => {
        const target = event && event.target;
        // Inside the marker OR inside its tooltip (a child of it): scrolling a
        // long extract must not dismiss the thing being scrolled.
        if (target && typeof tag.contains === "function" && tag.contains(target)) return;
        closeIndexedTip();
      },
      escape: (event) => {
        if (event && event.key === "Escape") closeIndexedTip();
      },
    };
    doc.addEventListener("pointerdown", tipListeners.outside, true);
    doc.addEventListener("keydown", tipListeners.escape, true);
  }

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
    // Before the nodes go: an open tooltip holds a document listener, and a
    // reference to an element that is about to stop existing.
    closeIndexedTip();
    while (listEl.firstChild) listEl.removeChild(listEl.firstChild);
  }

  function renderMessage(msg, { prepend = false } = {}) {
    if (rowFor(msg.id)) return; // de-dupe
    const wrapper = buildBubbleNode(msg);
    if (prepend) listEl.insertBefore(wrapper, listEl.firstChild);
    else listEl.appendChild(wrapper);
    // Grouping depends on the rows AROUND this one, so inserting anywhere can
    // change a neighbour's answer. Reconciled here rather than left to the
    // caller: a live arrival that forgot to re-run it would strand the avatar
    // on the second-to-last bubble of the run, permanently — and now the NAME
    // on the wrong bubble too.
    syncRunGrouping();
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
    // syncRunGrouping so grouping stays correct for rows that were built
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

    const prov = provenanceLabel(msg.routed_via);
    if (prov) {
      const provSpan = doc.createElement("span");
      provSpan.className = `xb-msg-provenance ${prov.cls}`;
      provSpan.textContent = prov.text;
      meta.appendChild(provSpan);
    }

    // The brain tag (migration 0034). A row only reaches a renderer after the
    // server decided this viewer may see it, so this marker never grants or
    // withholds anything — it explains why a message the author can see is
    // absent from everyone else's thread.
    //
    // The words are deliberately about the CHAT and not about secrecy: the note
    // is in the team's brain and any teammate can still find it by searching or
    // by asking the agent. A marker reading "private" would be the product
    // telling the lie the feature must not tell.
    if (msg.private) {
      const hidden = doc.createElement("span");
      hidden.className = "xb-msg-provenance xb-msg-hidden";
      // Both strings come from brain_tag.js so the marker, the composer icon
      // and the first-use sheet cannot drift apart. They went through the
      // wr + verify-copy pass; the placeholder they replace was mine.
      hidden.textContent = BUBBLE_LABEL;
      hidden.title = TOOLTIP;
      meta.appendChild(hidden);
    }
    // Body — the bubble (own = --primary, others = --muted, agent = --card).
    const body = doc.createElement("div");
    body.className = "xb-msg-bubble";

    // Did this agent turn fail, and did anything real arrive before it did?
    // Both answers come off the persisted row, so a reload is as truthful as the
    // live frame was.
    const failure =
      msg.kind === "agent" && msg.metadata && msg.metadata.agent_failure
        ? msg.metadata.agent_failure
        : null;
    const failedNoOutput = Boolean(failure) && failure.partial !== true;

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
      // A failed turn that produced NO output has the server's failure sentence
      // sitting in its content column. Printing it here would put it where the
      // agent's answers go, in the agent's voice — the exact lie this feature
      // exists to stop. The failure line below says it instead, as a failure.
      // A turn that produced PARTIAL text keeps it: that part is real output.
      const value = failedNoOutput ? "" : msg.content || "";
      // Agent answers arrive in markdown and are parsed into NODES; a person's
      // message is shown exactly as they typed it. See `rendersMarkdown`.
      if (rendersMarkdown(msg)) renderMarkdownInto(text, value);
      else text.textContent = value;
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

    // A turn that failed says so, in its own node, below whatever did arrive.
    // Reading it off the PERSISTED row (not only off the live frame) is what
    // makes it survive a reload and reach everyone who was not connected when it
    // happened — otherwise the same row reads as an ordinary answer.
    if (failure) {
      body.classList.add("is-failed");
      body.appendChild(buildFailureNode(failure));
    }

    // Time — INSIDE the bubble, pinned bottom-right, on EVERY message including
    // the un-named middle ones of a run. Identity is per-run; time is per
    // message, and a run of six with one timestamp answers "when did they say
    // THAT one" for exactly one of them.
    //
    // Two nodes, and the second is the load-bearing one. `.xb-msg-time` is taken
    // out of flow by CSS so it can sit in the bubble's bottom corner; on its own
    // that means a long final word slides underneath it and becomes unreadable.
    // `.xb-msg-timespace` is an inline box carrying the SAME string, made
    // invisible — so the width reserved on the last line is exactly the width
    // that will be painted over it, for every label formatRelative can produce
    // ("just now" and "5m" are not the same size). It is the LAST child of the
    // bubble, which puts it on whatever the final line turns out to be: beside
    // the text for a text message, on its own line under an image or under the
    // agent's sources row.
    const timeText = formatRelative(msg.created_at);

    const spacer = doc.createElement("span");
    spacer.className = "xb-msg-timespace";
    spacer.textContent = timeText;
    body.appendChild(spacer);

    const time = doc.createElement("span");
    time.className = "xb-msg-time";
    time.textContent = timeText;
    time.title = new Date(msg.created_at).toLocaleString();
    body.appendChild(time);

    wrapper.appendChild(body);

    // Indexed-attachment marker — only on a genuine signal. An absent marker is
    // correct and load-bearing: it is how a reader tells an attachment the brain
    // holds from one it does not. A fabricated marker would be a spoof.
    const attachment = indexedAttachment(msg);
    if (attachment) {
      wrapper.appendChild(buildIndexedMarker(attachment));
    }

    return wrapper;
  }

  /**
   * The small marker that reveals what was indexed.
   *
   * It used to be a sentence about the mechanism ("saved to brain · image
   * indexed"), which told a reader that something happened and never what. The
   * marker is now a mark, and the CONTENT is what appears on it — the actual text
   * the brain holds for that attachment.
   *
   * Three things it is careful about:
   *
   *   FOCUSABLE. A <button>, not a hover-only span, so the reveal is reachable by
   *   Tab. CSS shows the tooltip on :hover AND :focus-visible; a tooltip that only
   *   answers to a pointer does not exist for a keyboard.
   *
   *   LAZY. Nothing is fetched while the row is built. A thread of fifty images
   *   would otherwise fire fifty requests on load, for text nobody has asked to
   *   see. The first hover or focus starts the one request, and
   *   `indexedTextCache` keys it so a second never happens.
   *
   *   HONEST WHILE EMPTY. The tooltip starts at "Loading…" and lands on a real
   *   sentence for every outcome — indexing in flight, deliberately skipped,
   *   failed, or a request that never came back. It is never blank.
   *
   * @param {{itemId: string, kind: string}} attachment
   * @returns {HTMLElement}
   */
  function buildIndexedMarker(attachment) {
    // No fetcher on this surface: keep the marker (the indexed signal is real)
    // but make it inert. A control that looks pressable and answers nothing is
    // worse than a plain mark — the same rule the author name follows.
    const tag = doc.createElement(fetchIndexedText ? "button" : "span");
    tag.className = "xb-msg-savetag";
    if (fetchIndexedText) {
      tag.type = "button";
      tag.setAttribute("aria-label", "Show the text indexed from this attachment");
    }

    // The mark. aria-hidden because the button already has a name; without that
    // a screen reader reads the glyph as punctuation on top of the label.
    const mark = doc.createElement("span");
    mark.className = "xb-savetag-mark";
    mark.setAttribute("aria-hidden", "true");
    mark.textContent = "≡"; // three stacked rules — lines of text
    tag.appendChild(mark);

    if (!fetchIndexedText) return tag;

    const tip = doc.createElement("span");
    tip.className = "xb-savetag-tip";
    tip.setAttribute("role", "tooltip");
    tip.textContent = "Loading…";
    tag.appendChild(tip);

    let started = false;
    const reveal = () => {
      if (started) return; // the cache would answer anyway; this saves the churn
      started = true;
      loadIndexedText(attachment.itemId).then((payload) => {
        // textContent, never markup: this string is a model's description of a
        // file a teammate uploaded, i.e. attacker-influencable twice over.
        tip.textContent = indexedTooltipText(payload);
      });
    };
    tag.addEventListener("mouseenter", reveal);
    tag.addEventListener("focus", reveal);
    // The touch path. A tap implies a toggle, so a second one closes what the
    // first opened; the fetch still happens at most once, from the cache above.
    tag.addEventListener("click", () => {
      reveal();
      if (openTip === tag) closeIndexedTip();
      else openIndexedTip(tag);
    });

    return tag;
  }

  /**
   * The failure line for a turn the agent could not complete.
   *
   * Its words come from `agentFailureText`, keyed on the failure CODE — never
   * from a text field the frame carries. A failure payload is exactly where a
   * provider's own error text ends up when something upstream regresses, and one
   * of those reached a team chat naming the vendor and the account's billing
   * state. Rendering from a closed vocabulary means no payload can produce words
   * this client does not already contain.
   *
   * @param {{code?: string}} failure
   * @returns {HTMLElement}
   */
  function buildFailureNode(failure) {
    const node = doc.createElement("div");
    // An absence and a malfunction are not the same news, so they are not the
    // same line. Both classes exist; only one is applied.
    node.className = isAgentUnavailable(failure)
      ? "xb-msg-failure xb-msg-unavailable"
      : "xb-msg-failure";
    // Announced, because a failure that only exists visually is a message a
    // screen-reader user simply never receives.
    node.setAttribute("role", "status");
    node.textContent = agentFailureText(failure);
    return node;
  }

  /**
   * Mark a live agent turn as failed (the `agent_stream_error` frame).
   *
   * Deliberately NOT written into `.xb-msg-text`. Appending the failure to the
   * answer made it read as the last thing the agent said, which is a lie about
   * what happened — and it left the text node holding two different kinds of
   * thing, so a late chunk could interleave with it.
   *
   * Idempotent: a duplicate frame does not stack a second line.
   *
   * @param {string} messageId
   * @param {{code?: string}} failure
   */
  function renderAgentFailure(messageId, failure) {
    const row = rowFor(messageId);
    const bubble = row ? row.querySelector(".xb-msg-bubble") : null;
    if (!bubble) return;
    bubble.classList.remove("streaming");
    if (bubble.querySelector(".xb-msg-failure")) return;
    bubble.classList.add("is-failed");
    bubble.appendChild(buildFailureNode(failure || {}));
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
    syncRunGrouping();
  }

  /**
   * Reconcile Telegram-style run grouping across the whole thread.
   *
   * TWO RULES, ONE PASS. Both boundaries of a run of consecutive messages from
   * one author are decided here, because both are answers about NEIGHBOURS and
   * a second reconciler would be a second chance to disagree with this one:
   *
   *   HEAD — the sender's NAME appears once, on the FIRST message of the run.
   *          The name answers "who is talking"; asking it again three bubbles
   *          later is noise, and it is the same noise six times in a row when
   *          somebody sends six lines.
   *   TAIL — the AVATAR appears once, on the LAST message of the run: the
   *          newest bubble, at the bottom of the group, where the eye already
   *          is.
   *
   * The two land on opposite ends on purpose. The name introduces the run from
   * the top, the face closes it at the bottom, and the bubbles between them
   * carry neither — which is what makes a long run read as one turn instead of
   * six.
   *
   * Recomputed from the DOM rather than tracked incrementally, for the same
   * reason syncDaySeparators is: one function has to be correct for the append
   * path, the prepend pagination path AND live inserts, and the answer for any
   * row depends on rows that may not have existed when it was built. A message
   * arriving over the websocket therefore re-evaluates its neighbours here
   * rather than stranding a name or a face on the wrong bubble.
   *
   * A day separator ENDS a run on both sides. Two messages either side of
   * midnight are not visually consecutive, so the one below the heading gets
   * its name back and the one above keeps its avatar.
   *
   * Grouped rows keep their gutter (the avatar is hidden, not removed) so every
   * bubble in a run stays on the same left edge. Removing it would shift the
   * whole run left and turn a quiet visual grouping into a jagged one.
   */
  function syncRunGrouping() {
    const rows = Array.from(listEl.children);
    for (let i = 0; i < rows.length; i += 1) {
      const row = rows[i];
      if (!row.dataset || !row.dataset.msgId) continue; // a separator
      // The neighbours decide. A separator, or the end of the list, is not a
      // neighbour — which is what makes a date heading break the run.
      const before = rows[i - 1];
      const prev = before && before.dataset && before.dataset.msgId ? before : null;
      const after = rows[i + 1];
      const next = after && after.dataset && after.dataset.msgId ? after : null;

      const isHead = !prev || prev.dataset.authorKey !== row.dataset.authorKey;
      const isTail = !next || next.dataset.authorKey !== row.dataset.authorKey;

      if (isTail) row.classList.remove("is-grouped");
      else row.classList.add("is-grouped");
      // `is-run-follower` — everything after the first bubble of a run. CSS
      // hides the name on these; the class is named for the fact rather than
      // for the effect, so the stylesheet stays free to answer it differently.
      if (isHead) row.classList.remove("is-run-follower");
      else row.classList.add("is-run-follower");
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
   * Write the answer accumulated so far into a streaming agent bubble.
   *
   * THE WHOLE BUFFER, every time, not the delta — because markdown is not a
   * stream of independent pieces. `**Excalibur**` arrives as `**Exc`, `alibur`,
   * `**`, and only the third chunk turns the first two into a word in bold. So
   * the parse runs against the full text on each frame and replaces the body.
   *
   * That re-parse is cheap and, more importantly, atomic: `renderMarkdownInto`
   * builds every node before it removes the first one, inside a single
   * synchronous run, so no frame is ever painted showing a half-built answer.
   *
   * A `**` that has not been closed yet renders as two literal asterisks and
   * the rest of the answer keeps arriving beneath it — the emphasis grammar is
   * newline-free and requires its closer, so an open marker cannot swallow what
   * comes after it.
   *
   * @param {string} messageId
   * @param {string} text the full answer so far
   * @param {{partial?: boolean}} [opts] `partial: true` while chunks are still
   *   arriving. It reaches exactly one decision — a bare URL at the very end of
   *   the buffer is not linked yet, because half a URL is a link to the wrong
   *   place. The router renders once more without it at `agent_stream_end`.
   * @returns {HTMLElement|null} the body element, or null if the row is gone
   */
  function writeStreamText(messageId, text, opts) {
    const el = streamTextTarget(messageId);
    if (!el) return null;
    renderMarkdownInto(el, text, opts);
    return el;
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
    if (isNearBottom(el)) {
      el.scrollTop = el.scrollHeight;
    }
  }

  return {
    clear,
    renderMessage,
    renderAgentBubble,
    renderAgentFailure,
    buildBubbleNode,
    syncDaySeparators,
    syncRunGrouping,
    streamTextTarget,
    writeStreamText,
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
