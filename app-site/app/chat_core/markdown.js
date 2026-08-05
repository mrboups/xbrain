/**
 * Markdown -> DOM NODES for chat message bodies.
 *
 * THE DEFECT THIS EXISTS FOR. The agent answers in markdown and the chat showed
 * the characters: `**Excalibur** is a competitive Web3 PvP game` arrived on
 * screen with the asterisks in it. render.js put the answer on screen with
 * `text.textContent = msg.content`, which is exactly right about safety and
 * exactly wrong about reading.
 *
 * SO THE SAFETY PROPERTY IS WHAT SHAPES THIS FILE. This product has already
 * shipped a stored-XSS-to-credential-theft bug, and message content is
 * attacker-influencable twice over: a teammate types it, or a model that read
 * a web page emits it. Every node below is built with createElement and every
 * character reaches the document through textContent / createTextNode. No
 * markup-string sink is named anywhere in this file, and no code path here
 * assembles markup for something else to parse.
 *
 * `test_chat_core_markdown.mjs` asserts that by scanning this source for the
 * three sink names, COMMENTS INCLUDED — which is why this paragraph describes
 * them instead of spelling them. A gate that a reworded comment can trip is
 * annoying; a gate that prose can satisfy is worthless. Do not "simplify" a
 * node build into a template string.
 *
 * NO DEPENDENCY, on purpose. The extension ships unbundled and this repo has no
 * build step, so a markdown library would be a supply-chain surface — thousands
 * of lines with their own HTML-sanitiser opinion — bolted onto the one path
 * that is also the XSS boundary. The subset an agent actually emits is one
 * screen of code with no HTML parser in it at all, which is a smaller thing to
 * audit than any library's allowlist.
 *
 * THE SUBSET, and nothing else: `**bold**`, `*italic*`, `` `code` ``, fenced
 * code blocks, `- ` and `1. ` lists, `#` headings, `[text](url)` links, and
 * blank-line paragraph breaks. Anything unrecognised stays as the characters
 * that produced it — an unsupported construct renders as what the person typed
 * and NEVER disappears. That rule is what makes the parser safe to be
 * conservative: refusing to parse something is always a legible outcome.
 *
 * LINKS ARE THE DANGEROUS PART and are handled in `safeHref` below.
 *
 * Used by:
 *   - packages/chat-core/render.js  (persisted agent messages)
 *   - packages/chat-core/publication.js  (the streaming agent answer)
 *   - both surfaces, through their generated chat_core/ copies
 */

/**
 * The only schemes a message may turn into a clickable anchor.
 *
 * An allowlist, not a blocklist: `javascript:` is the one everybody remembers,
 * and `data:`, `vbscript:`, `blob:`, `filesystem:` and whatever ships next are
 * the ones a blocklist forgets. Relative and protocol-relative targets are out
 * too — a chat message is not part of this app's navigation, so a link that
 * resolves against our own origin is either a mistake or an attempt.
 */
export const ALLOWED_LINK_SCHEMES = ["http:", "https:", "mailto:"];

const ALLOWED = new Set(ALLOWED_LINK_SCHEMES);

/**
 * Turn a markdown link target into an href that is safe to set, or null.
 *
 * TWO THINGS MAKE THIS SOUND and both are easy to get wrong:
 *
 *   1. The string that is VALIDATED is the string that is USED. A browser drops
 *      every C0 control character and space from a URL before it resolves it,
 *      so `java\nscript:alert(1)` navigates — validating the raw text and then
 *      assigning the raw text is how that bypass works. Stripping first, then
 *      testing the stripped value, then returning THAT value, removes the gap
 *      between what was checked and what ships.
 *
 *   2. The scheme test is anchored and case-folded. ` JavaScript:` with a lead
 *      space and mixed case is the same navigation as `javascript:`.
 *
 * A TARGET MUST CARRY AN ALLOWED SCHEME OF ITS OWN, which is one rule doing
 * three jobs. It refuses `javascript:` and its neighbours by not listing them.
 * It refuses a relative target like `/account/teams/`, because a chat message
 * is not part of this app's navigation and a link that resolves against our own
 * origin is either a mistake or an attempt. And it refuses a protocol-relative
 * `//evil.example`, which carries no scheme at all — it inherits ours, so a
 * reader has nothing to check before clicking.
 *
 * There is deliberately NO second branch for that last case. It was written,
 * and mutation testing showed nothing could make it fail: the scheme rule had
 * already refused every input it could see. A guard that cannot fail is a guard
 * the next reader trusts for a job it is not doing.
 *
 * A refusal is not an error: the caller renders the raw markdown as text, so a
 * rejected URL stays visible to the reader instead of vanishing.
 *
 * @param {string} raw the target between the parentheses of `[text](target)`
 * @returns {string|null} the href to set, or null when it must not be a link
 */
export function safeHref(raw) {
  if (typeof raw !== "string") return null;
  // Everything at or below U+0020, plus DEL — the set a URL parser discards.
  const url = raw.replace(/[\u0000-\u0020\u007f]/g, "");
  if (!url) return null;
  const scheme = /^([A-Za-z][A-Za-z0-9+.-]*):/.exec(url);
  if (!scheme) return null; // relative, or protocol-relative — see above
  if (!ALLOWED.has(`${scheme[1].toLowerCase()}:`)) return null;
  return url;
}

/**
 * The inline grammar, as ONE alternation so the leftmost construct wins.
 *
 * ORDER IS LOAD-BEARING. JavaScript tries alternatives left to right at each
 * start position, so `**bold**` has to offer the bold branch before the italic
 * one — otherwise the italic branch matches `*bold*` starting one character in
 * and the asterisks leak into the output.
 *
 * The emphasis branches require the content to begin AND end on a non-space.
 * That single rule is what keeps arithmetic readable: `2 * 3 * 4` has spaces
 * inside the candidate span, so it never becomes emphasis and stays as typed.
 *
 * THE STREAMING GUARANTEE — that a `**` still waiting for its closer cannot
 * swallow the rest of the answer — comes from two places, and neither is this
 * pattern's newline classes. It comes from every branch REQUIRING its closing
 * marker, so an unclosed one matches nothing and prints as the characters it
 * is; and from `appendInline` being handed ONE LINE at a time by the block
 * parser, so an open marker cannot outlive the line it was opened on. The
 * `[^\n]` classes are belt to that braces: they keep each branch correct on its
 * own, for a future caller that hands in more than a line.
 */
const INLINE_PATTERN =
  "(`[^`\\n]+`)" + // 1 code span — content is literal, never re-parsed
  "|(\\*\\*[^\\s*](?:[^\\n]*?[^\\s*])?\\*\\*)" + // 2 bold
  "|(\\*[^\\s*](?:[^\\n*]*?[^\\s*])?\\*)" + // 3 italic
  "|(\\[[^\\]\\n]*\\]\\([^()\\s]*\\))" + // 4 link
  "|(!\\[[^\\]\\n]*\\]\\([^()\\s]*\\))" + // 5 image — matched to be REFUSED
  "|(\\\\[\\\\`*_\\[\\]()#!~>+.-])"; // 6 escape — a marker the writer disarmed

/**
 * A backslash escape is matched FIRST at its position, and that is the point.
 *
 * `a\*escaped\*b` without this branch loses both asterisks: the italic branch
 * matches from the first `*` to the second and eats them, so a writer who
 * explicitly asked for a literal asterisk gets emphasis and no asterisk at all.
 * Consuming the pair here disarms the marker — it cannot open or close anything
 * — and emits the character the writer asked for. Nothing is lost either way,
 * which is the rule the whole parser is held to.
 */

/**
 * Images are matched only so they can be left alone.
 *
 * Not in the subset: loading a remote image from a chat message is a request to
 * a URL of the sender's choosing on every reader's machine, which is a tracking
 * pixel with extra steps. Without this branch `![alt](url)` would still match
 * the LINK branch one character in, and a reader would get `!` followed by an
 * anchor labelled with the alt text — a construct nobody wrote. Matching the
 * whole thing and emitting it verbatim keeps the promise: unsupported means
 * literal, never mangled and never dropped.
 */

/** `[label](target)`, re-read off a whole match to split its two halves. */
const LINK_PARTS = /^\[([^\]\n]*)\]\(([^()\s]*)\)$/;

/**
 * How deep emphasis may nest before the rest is left as plain text.
 *
 * `**a *b* c**` is worth one level. Beyond that the construct is not something
 * an agent emits, and a bound here means no message can drive this into a deep
 * recursion.
 */
const MAX_INLINE_DEPTH = 3;

/**
 * Append the inline content of one line to `parent`.
 *
 * The regex is built per call rather than shared: this function recurses into
 * emphasis, and a module-level /g/ regex carries `lastIndex` across the calls,
 * so one shared object would let an inner scan reset the outer one mid-line.
 *
 * @param {Document} doc
 * @param {Element} parent
 * @param {string} text
 * @param {number} [depth]
 */
function appendInline(doc, parent, text, depth = 0) {
  if (!text) return;
  if (depth >= MAX_INLINE_DEPTH) {
    parent.appendChild(doc.createTextNode(text));
    return;
  }
  const re = new RegExp(INLINE_PATTERN, "g");
  let cursor = 0;
  let m;
  while ((m = re.exec(text)) !== null) {
    const whole = m[0];
    if (m.index > cursor) {
      parent.appendChild(doc.createTextNode(text.slice(cursor, m.index)));
    }
    if (m[1] !== undefined) {
      const code = doc.createElement("code");
      code.className = "xb-md-code";
      // Deliberately NOT re-parsed: `**x**` inside backticks is two asterisks
      // and a word, which is the entire point of a code span.
      code.textContent = whole.slice(1, -1);
      parent.appendChild(code);
    } else if (m[2] !== undefined) {
      const strong = doc.createElement("strong");
      strong.className = "xb-md-strong";
      appendInline(doc, strong, whole.slice(2, -2), depth + 1);
      parent.appendChild(strong);
    } else if (m[3] !== undefined) {
      const em = doc.createElement("em");
      em.className = "xb-md-em";
      appendInline(doc, em, whole.slice(1, -1), depth + 1);
      parent.appendChild(em);
    } else if (m[5] !== undefined) {
      // An image: matched so it can be handed back exactly as it was written.
      parent.appendChild(doc.createTextNode(whole));
    } else if (m[6] !== undefined) {
      // The escaped character, without its backslash — and, crucially, without
      // the chance to act as a marker.
      parent.appendChild(doc.createTextNode(whole.slice(1)));
    } else {
      const parts = LINK_PARTS.exec(whole);
      const href = parts ? safeHref(parts[2]) : null;
      if (!href) {
        // A refused scheme stays as the characters that produced it. Not an
        // anchor, and not dropped either: a reader can see there was a link
        // and see where it pointed, which is more than a silent removal says.
        parent.appendChild(doc.createTextNode(whole));
      } else {
        const a = doc.createElement("a");
        a.className = "xb-md-a";
        a.setAttribute("href", href);
        a.setAttribute("target", "_blank");
        // noopener: the opened page must not reach back through window.opener.
        // noreferrer: a team's chat must not leak its URL to whatever it links.
        a.setAttribute("rel", "noopener noreferrer");
        appendInline(doc, a, parts[1], depth + 1);
        parent.appendChild(a);
      }
    }
    cursor = m.index + whole.length;
  }
  if (cursor < text.length) {
    parent.appendChild(doc.createTextNode(text.slice(cursor)));
  }
}

const FENCE_OPEN = /^ {0,3}```/;
const FENCE_CLOSE = /^ {0,3}```+\s*$/;
const HEADING = /^ {0,3}(#{1,6})\s+(.*)$/;
const BULLET = /^ {0,3}-\s+(.*)$/;
const NUMBERED = /^ {0,3}(\d{1,9})\.\s+(.*)$/;

/**
 * Parse a whole message into top-level block nodes.
 *
 * @param {Document} doc
 * @param {string} src
 * @returns {Element[]} the blocks, in order, attached to nothing yet
 */
function buildBlocks(doc, src) {
  const lines = src.split(/\r\n|\r|\n/);
  const out = [];
  let paragraph = [];
  let i = 0;

  /** Close the paragraph being accumulated, if any. */
  function flushParagraph() {
    if (paragraph.length === 0) return;
    const p = doc.createElement("div");
    p.className = "xb-md-p";
    for (let n = 0; n < paragraph.length; n++) {
      // A single newline inside a paragraph is a line break in a chat, not the
      // whitespace collapse markdown would do to it. A <br> makes that
      // structural instead of leaving it to a white-space declaration.
      if (n > 0) p.appendChild(doc.createElement("br"));
      appendInline(doc, p, paragraph[n]);
    }
    out.push(p);
    paragraph = [];
  }

  while (i < lines.length) {
    const line = lines[i];

    if (FENCE_OPEN.test(line)) {
      flushParagraph();
      i++;
      const body = [];
      while (i < lines.length && !FENCE_CLOSE.test(lines[i])) {
        body.push(lines[i]);
        i++;
      }
      // An UNCLOSED fence still makes a block, running to the end of the
      // message — CommonMark's rule, and the one that matters while streaming:
      // a half-arrived block would otherwise flip from literal text to a code
      // block the instant the closing fence lands.
      if (i < lines.length) i++;
      const pre = doc.createElement("pre");
      pre.className = "xb-md-pre";
      const code = doc.createElement("code");
      code.className = "xb-md-codeblock";
      // The info string (```js) is READ AND DISCARDED. Turning it into a class
      // would put a model-supplied token into the stylesheet's namespace for
      // no benefit — nothing here highlights syntax.
      code.textContent = body.join("\n");
      pre.appendChild(code);
      out.push(pre);
      continue;
    }

    if (!line.trim()) {
      flushParagraph();
      i++;
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      flushParagraph();
      // A DIV carrying its level, not an <h1>. A message is not a document
      // section: real headings from a model would join the page's outline and
      // rearrange what a screen reader announces as this app's structure.
      const h = doc.createElement("div");
      h.className = "xb-md-h";
      h.dataset.level = String(heading[1].length);
      appendInline(doc, h, heading[2].trim());
      out.push(h);
      i++;
      continue;
    }

    const numbered = NUMBERED.exec(line);
    const bulleted = numbered ? null : BULLET.exec(line);
    if (numbered || bulleted) {
      flushParagraph();
      const ordered = Boolean(numbered);
      const list = doc.createElement(ordered ? "ol" : "ul");
      list.className = ordered ? "xb-md-ol" : "xb-md-ul";
      if (ordered) {
        const first = Number(numbered[1]);
        // `3.` starts at three. A list that renumbers itself from 1 silently
        // contradicts an answer that refers to "step 3".
        if (Number.isFinite(first) && first !== 1) {
          list.setAttribute("start", String(first));
        }
      }
      while (i < lines.length) {
        const item = ordered ? NUMBERED.exec(lines[i]) : BULLET.exec(lines[i]);
        if (!item) break;
        const li = doc.createElement("li");
        li.className = "xb-md-li";
        appendInline(doc, li, ordered ? item[2] : item[1]);
        list.appendChild(li);
        i++;
      }
      out.push(list);
      continue;
    }

    paragraph.push(line);
    i++;
  }
  flushParagraph();
  return out;
}

/**
 * The source string each element was last rendered from.
 *
 * Streaming rewrites the same bubble on every chunk, and a chunk that adds
 * nothing (an empty delta, a repeated frame) would otherwise rebuild the whole
 * answer for no change. Keyed weakly so a rendered message stops being tracked
 * when its row is removed from the list.
 */
const lastSource = new WeakMap();

/**
 * Render `text` as markdown INTO `el`, replacing whatever it held.
 *
 * The nodes are all built BEFORE the first child is removed. That ordering is
 * what keeps a streaming answer from flickering: the swap is one synchronous
 * run with no await in it, so the browser never gets a frame showing an empty
 * bubble or a half-built one.
 *
 * `el` gets the `xb-md` class, which is how each surface's stylesheet knows
 * this body carries blocks rather than one run of pre-wrapped text.
 *
 * @param {Element|null} el the message-body element (`.xb-msg-text`)
 * @param {string} text raw markdown, exactly as it arrived
 * @returns {Element|null} `el`, for chaining
 */
export function renderMarkdownInto(el, text) {
  if (!el) return el;
  const doc = el.ownerDocument;
  if (!doc || typeof doc.createElement !== "function") {
    throw new TypeError("renderMarkdownInto needs an element with an ownerDocument");
  }
  const src = typeof text === "string" ? text : text == null ? "" : String(text);
  if (el.classList && typeof el.classList.add === "function") {
    el.classList.add("xb-md");
  }
  if (lastSource.get(el) === src) return el;

  const nodes = src ? buildBlocks(doc, src) : [];
  while (el.firstChild) el.removeChild(el.firstChild);
  for (const node of nodes) el.appendChild(node);
  lastSource.set(el, src);
  return el;
}
