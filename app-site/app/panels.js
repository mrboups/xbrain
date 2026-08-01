/**
 * The PWA's members, invite and add-a-team surfaces.
 *
 * Every behaviour here is packages/chat-core's (D-27-04) — the member list and
 * its presence dots, the nudge fan-out, the file push, the mint/copy/join/email
 * flows, the slug rule and the 409 retry. This file is the surface: which
 * element is which, which button opens what, and what "a team just appeared"
 * means for a web page. The extension's popup.js does exactly the same job with
 * its own ids, which is what stops the two products from drifting.
 *
 * It lives beside chat.js rather than inside it for one reason: chat.js is the
 * chat, and a contract test reads it as text to prove it stays that. Overlays
 * that grow their own state belong in their own module.
 *
 * NOTHING here may ask for notification access or touch a push subscription
 * (D-27-05) — push.js owns the single click-gated call site, and a test asserts
 * it is the only file under app-site/app/ that names either API.
 */

import { createPeoplePanel } from "./chat_core/people.js";
import { createInvitePanel } from "./chat_core/invite.js";
import { renderTeamStarter } from "./chat_core/teams.js";
import { webPlatform } from "./platform_web.js";
import { MEMORY_API_BASE } from "./auth.js";

const el = (id) => document.getElementById(id);

/**
 * The two overlays and the header controls that open them.
 *
 * Built once per session and handed everything it needs as a late-read
 * function, because the team list and the active team both change after boot.
 *
 * @param {{
 *   api: Object,
 *   getActiveTeamId: () => (string|null),
 *   getTeams: () => Array<Object>,
 *   getTeamSubscription: () => (Object|null),
 *   onTeamsChanged: (preferTeamId?: string) => Promise<void>,
 *   onStarterDismissed: () => void
 * }} refs
 *   getTeamSubscription — the live team channel, for presence. Best effort: a
 *     channel that exposes none still lists every member, without a dot.
 *   onTeamsChanged — the chat re-reads /v1/teams/my-teams and points itself at
 *     the new team. This module does not know what "switch team" means.
 *   onStarterDismissed — put the chat's own empty-thread copy back. It lives in
 *     chat.js because chat.js is where that sentence is written; a second copy
 *     of the string here is a second copy to forget about.
 * @returns {{openPeople: Function, openStarter: Function, reveal: Function, hide: Function}}
 */
export function bootPanels(refs) {
  const {
    api,
    getActiveTeamId,
    getTeams,
    getTeamSubscription,
    onTeamsChanged,
    onStarterDismissed,
  } = refs;

  const people = createPeoplePanel({
    doc: document,
    api,
    apiBase: MEMORY_API_BASE,
    // currentPageUrl() answers null on the web. The panel then writes the
    // "paste a link" hint and leaves the field empty, which is the honest
    // answer: this page can see nothing but itself, and offering to send THAT
    // would be a control that always sends the wrong thing.
    platform: webPlatform,
    els: {
      panel: el("people-panel"),
      list: el("people-list"),
      urlInput: el("people-url"),
      status: el("people-status"),
      hint: el("people-hint"),
      filePicker: el("people-file"),
    },
    getActiveTeamId,
    getTeams,
    getTeamSubscription,
  });

  const invite = createInvitePanel({
    doc: document,
    api,
    apiBase: MEMORY_API_BASE,
    els: {
      panel: el("invite-panel"),
      codeRow: el("invite-code-row"),
      codeOutput: el("invite-code-output"),
      status: el("invite-status"),
      emailInput: el("invite-email"),
      emailStatus: el("invite-email-status"),
      joinInput: el("invite-join-code"),
      joinStatus: el("invite-join-status"),
      mintBtn: el("btn-invite-mint"),
      emailBtn: el("btn-invite-email"),
      joinBtn: el("btn-invite-join"),
    },
    getActiveTeamId,
    onJoined: () => onTeamsChanged(),
  });

  /**
   * The create-or-join panel, rendered into the chat's empty box.
   *
   * Both doors are first-class: someone who was INVITED must be able to act
   * right here rather than hunting for another overlay. Which is also why the
   * "+" beside the rail opens this and not a create-only form.
   *
   * @param {boolean} [dismissible] true when this was opened by the "+" over a
   *   chat somebody was reading. Without a way back, that "+" is a one-way
   *   door; on the no-teams screen there is nothing behind it, so no control is
   *   drawn and "Back to chat" cannot lead to an empty room.
   */
  function openStarter(dismissible) {
    const host = el("chat-empty");
    if (!host) return;
    host.hidden = false;
    renderTeamStarter({
      doc: document,
      hostEl: host,
      api,
      onCancel:
        dismissible && typeof onStarterDismissed === "function"
          ? () => onStarterDismissed()
          : null,
      // A team of one is useless, so creating one goes STRAIGHT to inviting,
      // with a link already minted. Making that a second click after a "team
      // created" message is how people end up alone in an empty room.
      onTeamCreated: async (team) => {
        await onTeamsChanged(team && team.id);
        await invite.openAndMint();
      },
      onTeamJoined: async (result) => {
        await onTeamsChanged(result && result.team_id);
      },
    });
  }

  const wire = (id, handler) => {
    const node = el(id);
    if (node) node.addEventListener("click", handler);
  };

  wire("btn-people", () => people.open());
  wire("btn-people-close", () => people.close());
  wire("btn-people-cancel", () => people.close());
  wire("btn-people-send-all", () => people.sendToEveryone(el("btn-people-send-all")));

  wire("btn-invite", () => invite.open());
  wire("btn-invite-close", () => invite.close());
  wire("btn-invite-cancel", () => invite.close());
  wire("btn-invite-mint", () => invite.mint());
  wire("btn-invite-copy", () => invite.copy());
  wire("btn-invite-join", () => invite.join());
  wire("btn-invite-email", () => invite.addByEmail());

  // Dismissible: this one is opened over a chat.
  wire("btn-team-add", () => openStarter(true));

  /** Show the header controls. Called once the person is in at least one team. */
  function reveal() {
    for (const id of ["btn-people", "btn-invite", "btn-team-add"]) {
      const node = el(id);
      if (node) node.hidden = false;
    }
  }

  /**
   * Hide them again, and shut both overlays.
   *
   * Closing matters as much as hiding: a minted code and a pasted code are both
   * bearer secrets, and invite.close() is what clears them out of the DOM.
   */
  function hide() {
    for (const id of ["btn-people", "btn-invite", "btn-team-add"]) {
      const node = el(id);
      if (node) node.hidden = true;
    }
    people.close();
    invite.close();
  }

  return {
    /** Open the members list with one person's row highlighted. */
    openPeople: (focusUserId) => people.open({ focusUserId }),
    openStarter,
    reveal,
    hide,
  };
}
