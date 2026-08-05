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
 * code blocks, `- ` and `1. ` lists, `#` headings, `[text](url)` links, BARE
 * URLs, and blank-line paragraph breaks. Anything unrecognised stays as the
 * characters that produced it — an unsupported construct renders as what the
 * person typed and NEVER disappears. That rule is what makes the parser safe to
 * be conservative: refusing to parse something is always a legible outcome.
 *
 * LINKS ARE THE DANGEROUS PART and are handled in `safeHref` below. A bare URL
 * is the same danger arriving without the `[text](url)` wrapper, so it goes
 * through the same function and the same anchor builder — see `AUTOLINK`.
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
 * A BARE URL, as the agent actually writes one.
 *
 * The defect: an answer that says
 *   `🔗 Pitch deck: https://pitch.com/v/excalibur-proposal-pybqby`
 * rendered as readable, unclickable text, because only `[label](target)` was a
 * link. The agent emits the bare form constantly.
 *
 * THE SCHEME IS THE WHOLE GATE. This pattern matches `http://`, `https://` and
 * `mailto:` and nothing else, spelled out character class by character class so
 * `HTTPS://` is caught without an `i` flag that would also apply to every other
 * branch of the alternation. A token with no scheme is not a link: `www.x.test`,
 * `x.test` and `nico@x.test` all stay plain text, which is the difference
 * between rendering a URL and guessing at one. `javascript:`, `data:` and
 * `ftp://` are refused by simply not being listed — the same allowlist argument
 * `safeHref` makes, made a second time at the point of recognition.
 *
 * THE BODY EXCLUDES the characters that delimit the constructs around it —
 * whitespace, `<` `>`, both quotes, the backtick, `[` `]` and the backslash — so
 * a URL can never eat the code span, the link or the escape that follows it.
 * `(` and `)` are deliberately ALLOWED through, because a wikipedia-style
 * `..._(disambiguation)` is a real URL; the tail trimmer below is what decides
 * which closing paren was the writer's and which was the prose's.
 */
const AUTOLINK_SCHEME = "(?:[Hh][Tt][Tt][Pp][Ss]?://|[Mm][Aa][Ii][Ll][Tt][Oo]:)";
const AUTOLINK_BODY = "[^\\s<>\"'`\\[\\]\\\\]";
const AUTOLINK = AUTOLINK_SCHEME + AUTOLINK_BODY + "+";

/**
 * Punctuation that ends a SENTENCE rather than a URL.
 *
 * `see https://x.test/a.` must not put the full stop inside the href — the link
 * would 404 and the reader would be told nothing about why. `>`, `]` and both
 * quotes are absent on purpose: `AUTOLINK_BODY` already refuses them, so listing
 * them here would be a rule that can never run.
 */
const URL_TAIL_PUNCTUATION = ".,:;!?*_~";

/**
 * Give back the part of a matched URL that is actually the URL.
 *
 * Two rules, applied until neither fires:
 *   1. trailing sentence punctuation is prose, not path;
 *   2. a trailing `)` belongs to the URL only if something inside it opened —
 *      `(see https://x.test/a)` ends at `a`, while
 *      `https://x.test/Foo_(bar)` keeps its pair. Counting is what separates
 *      them; a flat "always strip a closing paren" would break every wikipedia
 *      link an agent has ever pasted.
 *
 * Whatever is trimmed is NOT discarded — the caller emits it as the text it is,
 * so the reader still sees the full stop they typed.
 *
 * @param {string} url the raw match
 * @returns {string} the href-worthy prefix, possibly the whole thing
 */
export function trimAutolinkTail(url) {
  let end = url.length;
  while (end > 0) {
    const ch = url[end - 1];
    if (URL_TAIL_PUNCTUATION.includes(ch)) {
      end -= 1;
      continue;
    }
    if (ch === ")") {
      let opens = 0;
      let closes = 0;
      for (let i = 0; i < end; i++) {
        if (url[i] === "(") opens += 1;
        else if (url[i] === ")") closes += 1;
      }
      if (closes > opens) {
        end -= 1;
        continue;
      }
    }
    break;
  }
  return url.slice(0, end);
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
  "|(\\\\[\\\\`*_\\[\\]()#!~>+.-])" + // 6 escape — a marker the writer disarmed
  "|(" + AUTOLINK + ")"; // 7 bare URL

/**
 * WHY THE BARE-URL BRANCH CANNOT DOUBLE-LINK ANYTHING.
 *
 * The engine finds the LEFTMOST match and only then picks a branch, so a URL is
 * reached by branch 7 exactly when no earlier construct started before it:
 *
 *   [docs](https://x.test)   the `[` is at a lower index — branch 4 takes the
 *                            whole thing and the target becomes the href once,
 *                            through `safeHref`, never re-scanned as text;
 *   `see https://x.test`     the backtick is lower — branch 1, and a code span's
 *                            content is emitted verbatim by definition;
 *   ```fenced```             never reaches this function at all, because the
 *                            block parser owns fences and writes their text
 *                            straight into a <code>.
 *
 * The one case position cannot settle is a URL used as a link's LABEL —
 * `[https://x.test](https://y.test)` — because that text is re-entered through
 * `appendInline`. An anchor inside an anchor is not a thing a browser can
 * render, so the recursion carries `inAnchor` and branch 7 stands down.
 */

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
 * Build the one anchor shape this file is allowed to produce.
 *
 * ONE builder for both link forms, because the attributes are the safety
 * argument and a second copy of them is a second place to forget `rel`. The
 * caller has already run the target through `safeHref`; this only assembles.
 *
 * @param {Document} doc
 * @param {string} href a value `safeHref` returned
 * @returns {Element}
 */
function buildAnchor(doc, href) {
  const a = doc.createElement("a");
  a.className = "xb-md-a";
  a.setAttribute("href", href);
  a.setAttribute("target", "_blank");
  // noopener: the opened page must not reach back through window.opener.
  // noreferrer: a team's chat must not leak its URL to whatever it links.
  a.setAttribute("rel", "noopener noreferrer");
  return a;
}

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
 * @param {{depth?: number, inAnchor?: boolean, openEnded?: boolean}} [opts]
 *   depth      — emphasis nesting bound, see MAX_INLINE_DEPTH
 *   inAnchor   — this text is a link's label, so branch 7 must not build a
 *                second anchor inside the first
 *   openEnded  — this line is the growing edge of a streaming answer, so a URL
 *                that runs to the end of it may still be half-arrived
 */
function appendInline(doc, parent, text, opts = {}) {
  if (!text) return;
  const depth = opts.depth || 0;
  const inAnchor = Boolean(opts.inAnchor);
  const openEnded = Boolean(opts.openEnded);
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
      // openEnded stops here and at every other recursion: this content is
      // bounded by a closing marker that has demonstrably ARRIVED, so nothing
      // inside it is still growing.
      appendInline(doc, strong, whole.slice(2, -2), { depth: depth + 1, inAnchor });
      parent.appendChild(strong);
    } else if (m[3] !== undefined) {
      const em = doc.createElement("em");
      em.className = "xb-md-em";
      appendInline(doc, em, whole.slice(1, -1), { depth: depth + 1, inAnchor });
      parent.appendChild(em);
    } else if (m[7] !== undefined) {
      appendAutolink(doc, parent, whole, {
        inAnchor,
        // The RAW match is what must not touch the growing edge. Measuring the
        // trimmed URL instead would link `https://x.test/a.` the moment a full
        // stop arrived, and then relink it when the rest of the path followed.
        atEdge: openEnded && m.index + whole.length === text.length,
      });
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
        const a = buildAnchor(doc, href);
        appendInline(doc, a, parts[1], { depth: depth + 1, inAnchor: true });
        parent.appendChild(a);
      }
    }
    cursor = m.index + whole.length;
  }
  if (cursor < text.length) {
    parent.appendChild(doc.createTextNode(text.slice(cursor)));
  }
}

/**
 * Turn one bare-URL match into an anchor, or into the text it already was.
 *
 * THE HREF MUST EQUAL WHAT THE READER SEES. `safeHref` deletes every character
 * a URL parser would delete before resolving — that is what closes the
 * `java\nscript:` gap for `[text](target)`, where the reader sees a label and
 * cannot inspect the target. A bare URL is the opposite situation: the target
 * IS the label, so a href that differs from the visible characters would mean
 * the destination is not the thing on screen. Rather than pick which one to
 * trust, this refuses the whole construct — an autolink happens only when
 * stripping changes nothing, and the URL stays plain text otherwise.
 *
 * That single comparison also carries the scheme refusal, so there is no second
 * branch here that could never fire.
 *
 * @param {Document} doc
 * @param {Element} parent
 * @param {string} whole the raw match, punctuation and all
 * @param {{inAnchor: boolean, atEdge: boolean}} state
 */
function appendAutolink(doc, parent, whole, state) {
  const url = trimAutolinkTail(whole);
  const href = safeHref(url);
  // A label inside a link, or the growing edge of a stream: text, for now.
  if (state.inAnchor || state.atEdge || href !== url) {
    parent.appendChild(doc.createTextNode(whole));
    return;
  }
  const a = buildAnchor(doc, href);
  // createTextNode, not a re-parse: a URL's own characters are its content, and
  // an `*` or a `_` in a path is part of the path.
  a.appendChild(doc.createTextNode(url));
  parent.appendChild(a);
  // The full stop the writer ended their sentence with, outside the link.
  if (whole.length > url.length) {
    parent.appendChild(doc.createTextNode(whole.slice(url.length)));
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
 * @param {boolean} [partial] the answer is still arriving, so the LAST line of
 *   `src` may be half a line. Only that line is treated as open-ended: a line
 *   with a newline after it is finished by definition, however the message ends.
 * @returns {Element[]} the blocks, in order, attached to nothing yet
 */
function buildBlocks(doc, src, partial = false) {
  const lines = src.split(/\r\n|\r|\n/);
  const lastLine = lines.length - 1;
  /** Is the line at `index` the one still being typed into? */
  const growing = (index) => partial && index === lastLine;
  const out = [];
  /** @type {Array<{text: string, index: number}>} */
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
      appendInline(doc, p, paragraph[n].text, {
        openEnded: growing(paragraph[n].index),
      });
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
      appendInline(doc, h, heading[2].trim(), { openEnded: growing(i) });
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
        appendInline(doc, li, ordered ? item[2] : item[1], {
          openEnded: growing(i),
        });
        list.appendChild(li);
        i++;
      }
      out.push(list);
      continue;
    }

    paragraph.push({ text: line, index: i });
    i++;
  }
  flushParagraph();
  return out;
}

/**
 * What each element was last rendered from — the source AND the mode.
 *
 * Streaming rewrites the same bubble on every chunk, and a chunk that adds
 * nothing (an empty delta, a repeated frame) would otherwise rebuild the whole
 * answer for no change. Keyed weakly so a rendered message stops being tracked
 * when its row is removed from the list.
 *
 * THE MODE IS PART OF THE KEY and it has to be. The last chunk of an answer
 * ending in a URL renders open-ended (text), and the finishing pass renders the
 * SAME string closed (a link). Keying on the string alone would make that second
 * pass a no-op and the final URL would stay unclickable until a reload — the
 * exact bug this memo is otherwise there to avoid causing.
 */
const lastSource = new WeakMap();

/** The memo key: the mode, then a NUL, then the text. */
function renderKey(src, partial) {
  return `${partial ? "1" : "0"}\u0000${src}`;
}

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
 * @param {{partial?: boolean}} [opts]
 *   partial — `text` is a stream so far, not a whole answer. It costs exactly
 *   one thing: a bare URL sitting at the very end stays plain text, because
 *   half of `https://pitch.com/v/excalibur-proposal-pybqby` is a link to
 *   somewhere else. The caller that knows the answer is finished renders again
 *   without it, and the URL becomes an anchor once, on that pass.
 * @returns {Element|null} `el`, for chaining
 */
export function renderMarkdownInto(el, text, opts = {}) {
  if (!el) return el;
  const doc = el.ownerDocument;
  if (!doc || typeof doc.createElement !== "function") {
    throw new TypeError("renderMarkdownInto needs an element with an ownerDocument");
  }
  const src = typeof text === "string" ? text : text == null ? "" : String(text);
  const partial = Boolean(opts && opts.partial);
  if (el.classList && typeof el.classList.add === "function") {
    el.classList.add("xb-md");
  }
  const key = renderKey(src, partial);
  if (lastSource.get(el) === key) return el;

  const nodes = src ? buildBlocks(doc, src, partial) : [];
  while (el.firstChild) el.removeChild(el.firstChild);
  for (const node of nodes) el.appendChild(node);
  lastSource.set(el, key);
  return el;
}
