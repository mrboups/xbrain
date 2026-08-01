/**
 * The invite panel — mint a join link, add someone by email, redeem a code
 * (Phase 27, D-27-04; the behaviour is Phase 25, JOINCODE-01).
 *
 * Lifted out of chrome-extension/popup.js so the PWA drives the SAME code. Three
 * properties here are security-bearing and would be re-derived differently by a
 * second author on a second day:
 *
 *   1. THE CODE TRAVELS IN THE FRAGMENT. `buildJoinLink` emits `#c=<code>`,
 *      never `?c=`. A fragment is never sent to a server, so the invite secret
 *      cannot land in hosting logs or in a Referer header. A query string would
 *      look identical to a reader and leak the secret to every hop.
 *   2. THE ORIGIN IS DERIVED, not written twice. It comes from the api base with
 *      a leading `api.` dropped, so a rebrand of that one constant carries the
 *      join link with it instead of silently pointing at the old domain.
 *   3. THE SERVER IS THE AUTHORITY. The client cannot tell admin status from the
 *      my-teams payload, so it offers the action to everyone and surfaces the
 *      server's 403 as a sentence (T-25-16). The one-time code is rendered with
 *      textContent (T-25-17) and cleared on close (T-25-19), and the join
 *      endpoint's single 404 for invalid/expired/revoked/used-up is mirrored
 *      without guessing which it was — no oracle.
 *
 * Nothing here knows which surface it runs on: the document, the elements, the
 * API client, the api origin and the clipboard are injected.
 */

import { setStatusLine } from "./dom.js";

/**
 * Build the shareable join link for an invite code.
 *
 * @param {string} apiBase memory-api origin, e.g. the surface's own constant
 * @param {string} code the one-time invite code
 * @returns {string|null} null when the base cannot be parsed — the caller then
 *   tells the user to copy the bare code instead of copying something broken
 */
export function buildJoinLink(apiBase, code) {
  let origin;
  try {
    const u = new URL(apiBase);
    u.hostname = u.hostname.replace(/^api\./, "");
    origin = u.origin;
  } catch (e) {
    return null;
  }
  // The code rides in the FRAGMENT. See property 1 in the module docstring.
  return `${origin}/join/#c=${encodeURIComponent(code)}`;
}

/** Map the mint endpoint's rejection codes to clear English. */
export function mapMintError(status) {
  if (status === 403) return "Only a team admin can create invite codes.";
  if (status === 429) return "Too many requests — wait a moment.";
  if (status === 404) return "Team not found.";
  return `Could not create a code (HTTP ${status}).`;
}

/**
 * Map the join endpoint's rejection codes.
 *
 * The server returns ONE generic 404 for invalid / expired / revoked /
 * exhausted so a caller cannot probe which codes exist. The client mirrors that
 * deliberately: a more "helpful" message here would rebuild the oracle the
 * server refuses to be.
 */
export function mapJoinError(status) {
  if (status === 404) return "That code is invalid, expired, or used up.";
  if (status === 429) return "Too many attempts — wait a moment.";
  return `Could not join (HTTP ${status}).`;
}

/**
 * Build the invite panel for one surface.
 *
 * @param {{
 *   doc: Document,
 *   api: Object,
 *   apiBase: string,
 *   els: {panel: Element, codeRow: Element, codeOutput: Element, status: Element,
 *         emailInput?: Element|null, emailStatus?: Element|null,
 *         joinInput?: Element|null, joinStatus?: Element|null,
 *         mintBtn?: Element|null, emailBtn?: Element|null, joinBtn?: Element|null},
 *   getActiveTeamId: () => (string|null),
 *   onJoined?: (() => Promise<void>|void)|null,
 *   copyText?: ((text: string) => Promise<void>)|null
 * }} opts
 *   onJoined — called after a successful redeem, so the surface can refresh its
 *              team list. The panel does not know what "refresh" means here.
 *   copyText — injected so the copy path is testable; defaults to the clipboard.
 * @returns {{open: Function, close: Function, mint: Function, copy: Function,
 *            join: Function, addByEmail: Function, openAndMint: Function}}
 */
export function createInvitePanel(opts) {
  const cfg = opts || {};
  const api = cfg.api;
  const els = cfg.els || {};
  if (!api) throw new TypeError("createInvitePanel requires opts.api");

  const apiBase = typeof cfg.apiBase === "string" ? cfg.apiBase : "";
  const getActiveTeamId =
    typeof cfg.getActiveTeamId === "function" ? cfg.getActiveTeamId : () => null;
  const onJoined = typeof cfg.onJoined === "function" ? cfg.onJoined : null;
  const copyText =
    typeof cfg.copyText === "function"
      ? cfg.copyText
      : (text) => navigator.clipboard.writeText(text);

  const sayMint = (text, type) => setStatusLine(els.status, text, type);
  const sayEmail = (text, type) => setStatusLine(els.emailStatus, text, type);
  const sayJoin = (text, type) => setStatusLine(els.joinStatus, text, type);

  /**
   * ADMIN — mint an invite code and reveal the plaintext ONCE.
   *
   * @returns {Promise<boolean>} whether a code was produced (the create-a-team
   *   flow opens this panel and mints immediately, and wants to know)
   */
  async function mint() {
    if (!getActiveTeamId()) {
      sayMint("No active team.", "error");
      return false;
    }
    const btn = els.mintBtn;
    if (btn) btn.disabled = true;
    sayMint("Creating...", "loading");
    try {
      const res = await api.mintInviteCodeRaw(getActiveTeamId());
      if (res.status === 201) {
        const j = await res.json();
        // textContent — never markup — the code is an untrusted server string.
        if (els.codeOutput) els.codeOutput.textContent = j.code;
        if (els.codeRow) els.codeRow.hidden = false;
        sayMint("Code created — copy it now (shown once).", "success");
        return true;
      }
      sayMint(mapMintError(res.status), "error");
      return false;
    } catch (e) {
      // Never log the response: a successful mint's body IS the secret.
      console.warn("[xbrain] mint invite failed");
      sayMint("Network error — try again.", "error");
      return false;
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  /**
   * Copy the shareable LINK, not the bare code — the recipient clicks it and
   * joins, with no code to paste. The code stays visible as the manual fallback.
   */
  async function copy() {
    const code = els.codeOutput ? els.codeOutput.textContent : "";
    if (!code) {
      sayMint("Create a code first.", "error");
      return;
    }
    const link = buildJoinLink(apiBase, code);
    if (!link) {
      sayMint("Could not build the link — copy the code instead.", "error");
      return;
    }
    try {
      await copyText(link);
      sayMint("Invite link copied ✓ — share it, it joins in one click.", "success");
    } catch (e) {
      // Never log the link or the code — both carry the invite secret.
      console.warn("[xbrain] copy invite link failed");
      sayMint("Copy failed — select the code manually.", "error");
    }
  }

  /**
   * ADMIN — add someone by email.
   *
   * The endpoint resolves an EXISTING user row, so it 404s for anyone who has
   * never signed in. That is not an error to paper over: the copy points
   * straight back at the invite link, which is the path that works for a
   * brand-new teammate.
   */
  async function addByEmail() {
    if (!getActiveTeamId()) {
      sayEmail("No active team.", "error");
      return;
    }
    const email = els.emailInput ? els.emailInput.value.trim() : "";
    if (!email) {
      sayEmail("Enter an email address.", "error");
      return;
    }
    const btn = els.emailBtn;
    if (btn) btn.disabled = true;
    sayEmail("Adding...", "loading");
    try {
      const res = await api.inviteByEmailRaw(getActiveTeamId(), email);
      if (res.status === 201) {
        if (els.emailInput) els.emailInput.value = "";
        sayEmail(`${email} added to the team ✓`, "success");
      } else if (res.status === 404) {
        sayEmail(
          "No xbrain account for that email — send them the invite link above instead.",
          "error",
        );
      } else if (res.status === 403) {
        sayEmail("Only a team admin can add members.", "error");
      } else if (res.status === 409) {
        sayEmail("They are already on this team.", "success");
      } else {
        sayEmail(`Could not add them (HTTP ${res.status}).`, "error");
      }
    } catch (e) {
      console.warn("[xbrain] invite by email failed");
      sayEmail("Network error — try again.", "error");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  /** ANY MEMBER — redeem a pasted code, then let the surface refresh itself. */
  async function join() {
    const code = els.joinInput ? els.joinInput.value.trim() : "";
    if (!code) {
      sayJoin("Paste a code first.", "error");
      return;
    }
    const btn = els.joinBtn;
    if (btn) btn.disabled = true;
    sayJoin("Joining...", "loading");
    try {
      const res = await api.joinByCodeRaw(code);
      if (res.status === 200) {
        const j = await res.json();
        if (j.already_member) {
          sayJoin("You're already on that team.", "success");
        } else {
          // j.display_name is a server string; setStatusLine renders it via
          // textContent.
          sayJoin(`Joined ${j.display_name} ✓`, "success");
        }
        if (els.joinInput) els.joinInput.value = "";
        if (onJoined) await onJoined();
      } else {
        sayJoin(mapJoinError(res.status), "error");
      }
    } catch (e) {
      console.warn("[xbrain] join-by-code failed");
      sayJoin("Network error — try again.", "error");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function open() {
    if (!els.panel) return;
    els.panel.hidden = false;
    sayMint("", "");
    sayJoin("", "");
    sayEmail("", "");
    if (els.codeRow) els.codeRow.hidden = true;
  }

  /**
   * Close, and take the secrets with it.
   *
   * A minted code is shown once; a PASTED code is a bearer secret too. Neither
   * may survive a close, or the next person to open the panel on a shared screen
   * inherits them.
   */
  function close() {
    if (els.panel) els.panel.hidden = true;
    if (els.codeOutput) els.codeOutput.textContent = "";
    if (els.codeRow) els.codeRow.hidden = true;
    if (els.joinInput) els.joinInput.value = "";
    if (els.emailInput) els.emailInput.value = "";
    sayMint("", "");
    sayJoin("", "");
    sayEmail("", "");
  }

  /**
   * Open with a link already minted.
   *
   * This is what a team of one needs the instant it is created: the next thing
   * to do is bring somebody in, and making that a second click after a "team
   * created" message is how people end up alone in an empty room.
   */
  async function openAndMint() {
    open();
    return mint();
  }

  return { open, close, mint, copy, join, addByEmail, openAndMint };
}
