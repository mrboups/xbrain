/**
 * The team API key, inside the PWA's settings sheet.
 *
 * WHY IT EXISTS AT ALL. The agent answers through the owner's Claude
 * subscription while some browser holds a live extension session, and falls
 * back to a team key when none does - which is exactly the case this app is
 * for: somebody on a phone, at 3am, with no browser running anywhere. When the
 * bridge drops, the notice tells them a team key is the other remedy. Until
 * now the only place to set one was the desktop admin page, and a standalone
 * PWA has no address bar to reach it with. The advice was a dead end in
 * precisely the situation it was given.
 *
 * EVERY DEFINITION IS chat-core's (D-27-04): the provider table, the
 * validation, the failure sentences, both requests, and the copy. This file is
 * the surface - which element is which, and what a settings sheet on a phone
 * looks like. app-site/account/teams/ renders the same facts in its own shape
 * from the same module, which is what stops the two from drifting.
 *
 * THE KEY IS WRITE-ONLY, and that is a property of the screen rather than a
 * detail of it. GET /v1/teams/{id}/api-keys answers [{provider}] and nothing
 * else, so there is nothing to mask and presence is all that is shown. A masked
 * value would be a lie twice over: the characters are not available, and
 * printing a shape implies the rest could be revealed. It cannot.
 *
 * The secret's whole lifetime is: the input element -> validateApiKey -> the
 * PUT body. Never a URL, never a status line, never module state, never a
 * console call, and the field is cleared the moment the server accepts it.
 *
 * STORING A KEY AND SELECTING A PROVIDER ARE DIFFERENT ACTIONS, and the sheet
 * shows them as two blocks for that reason. A team may hold three keys and
 * spend one.
 *
 * NOTHING here may ask for notification access or touch a push subscription
 * (D-27-05) - push.js owns the single click-gated call site.
 */

import { setStatusLine, clearChildren } from "./chat_core/dom.js";
import {
  API_KEY_PROVIDERS,
  PROVIDER_ANTHROPIC,
  apiKeyProvider,
  providerLabel,
  providerIsCallable,
  readApiKeyProviders,
  readFallbackSelection,
  fallbackSelectionBody,
  missingKeyForSelectionWarning,
  validateApiKey,
  describeApiKeyFailure,
  teamKeySavedMessage,
  unusedProviderWarning,
  TEAM_KEY_UNUSED_MARK,
  TEAM_KEY_COST_NOTE,
  TEAM_KEY_REPLACE_NOTE,
  TEAM_KEY_MEMBER_NOTE,
  TEAM_KEY_ROLE_UNKNOWN_NOTE,
  TEAM_KEY_FALLBACK_ONLY_NOTE,
  TEAM_KEY_STORE_IS_NOT_SELECT,
  TEAM_KEY_NO_SELECTOR_NOTE,
  TEAM_KEY_SET,
  TEAM_KEY_NOT_SET,
  TEAM_KEY_UNKNOWN,
} from "./chat_core/team_api_keys.js";

const el = (id) => document.getElementById(id);

/**
 * Wire the section once, and hand back a way to re-read it.
 *
 * READ ON OPEN, NOT ON BOOT. Two requests per launch for a block most people
 * never open is two requests wasted on every cold start of a phone app; and the
 * active team changes under this file, so anything captured at boot is the
 * wrong team by the first switch. refresh() is called when the sheet opens and
 * when the bridge notice sends somebody here.
 *
 * IT NEVER THROWS. A settings sheet that dies on open takes the theme, the
 * notification state and the sign-out button with it, over a block nobody came
 * here to use. Every failure below becomes a sentence.
 *
 * @param {Object} api the shared chat-core client
 * @param {{getTeamId: () => (string|null), getSelfUserId: () => (string|null)}} refs
 *   Both read LATE, for the reason above.
 * @returns {{refresh: () => Promise<void>, focus: () => void}}
 */
export function mountTeamKeys(api, refs = {}) {
  const getTeamId = typeof refs.getTeamId === "function" ? refs.getTeamId : () => null;
  const getSelfUserId =
    typeof refs.getSelfUserId === "function" ? refs.getSelfUserId : () => null;

  const section = el("settings-team-key");
  const heading = el("team-key-heading");
  const note = el("team-key-note");
  const useRow = el("team-key-use-row");
  const useSelect = el("team-key-use");
  const useValue = el("team-key-use-value");
  const useWarning = el("team-key-use-warning");
  const fallbackNote = el("team-key-fallback-note");
  const rows = el("team-key-rows");
  const storeNote = el("team-key-store-note");
  const form = el("team-key-form");
  const keyLabel = el("team-key-label");
  const input = el("team-key-input");
  const saveBtn = el("btn-team-key-save");
  const providerSelect = el("team-key-provider");
  const warning = el("team-key-warning");
  const hint = el("team-key-hint");
  const memberNote = el("team-key-member-note");
  const status = el("team-key-status");

  if (!section) return { refresh: async () => {}, focus: () => {} };

  /**
   * What the last read established. Not a cache anybody may trust across a team
   * switch - refresh() overwrites all of it, and every render reads from here
   * rather than from a closure captured at wiring time.
   */
  const view = {
    teamId: null,
    /** provider ids with a key stored, or null when that read FAILED. */
    stored: null,
    /** the team's fallback selection, or null when this build has no selector. */
    selection: null,
    /** true / false / null, where null is "we could not find out". */
    isAdmin: null,
  };

  let wired = false;

  /**
   * Fill the provider picker and the selection control from the shared table.
   *
   * Done in script rather than in the markup so the two surfaces cannot list
   * different providers, and so the "not called by the agent yet" marker
   * follows whatever the server says it supports.
   */
  function fillProviders(ids, supported) {
    if (providerSelect) {
      clearChildren(providerSelect);
      for (const p of API_KEY_PROVIDERS) {
        const opt = document.createElement("option");
        opt.value = p.id;
        // The marker rides in the option text too: the picker is where the
        // choice is made, and somebody reading a dropdown is not reading the
        // list above it.
        opt.textContent = providerIsCallable(p.id, supported)
          ? p.label
          : `${p.label} - ${TEAM_KEY_UNUSED_MARK}`;
        providerSelect.appendChild(opt);
      }
      providerSelect.value = PROVIDER_ANTHROPIC;
    }
    if (useSelect) {
      clearChildren(useSelect);
      for (const id of ids) {
        const opt = document.createElement("option");
        opt.value = id;
        opt.textContent = providerLabel(id);
        useSelect.appendChild(opt);
      }
    }
  }

  /** Every provider worth a row: the offered ones, plus anything the server named. */
  function allProviderIds() {
    const ids = API_KEY_PROVIDERS.map((p) => p.id);
    for (const extra of view.stored || []) {
      if (!ids.includes(extra)) ids.push(extra);
    }
    const selected = view.selection && view.selection.provider;
    if (selected && !ids.includes(selected)) ids.push(selected);
    return ids;
  }

  /** The stored-key list. Presence only, ever. */
  function paintRows(ids, supported) {
    if (!rows) return;
    clearChildren(rows);
    const present = new Set(view.stored || []);
    for (const id of ids) {
      const row = document.createElement("div");
      row.className = "xb-teamkey-row";

      const name = document.createElement("span");
      name.className = "xb-teamkey-provider";
      name.textContent = providerLabel(id);
      row.appendChild(name);

      // Which of these the agent can actually call, said on the row itself.
      if (!providerIsCallable(id, supported)) {
        const unused = document.createElement("span");
        unused.className = "xb-teamkey-unused";
        unused.textContent = TEAM_KEY_UNUSED_MARK;
        row.appendChild(unused);
      }

      const state = document.createElement("span");
      state.className = "xb-teamkey-state";
      if (view.stored === null) {
        // A failed read is NOT an absence. Claiming one invites an admin to
        // overwrite a key that was working.
        state.textContent = TEAM_KEY_UNKNOWN;
      } else if (present.has(id)) {
        state.textContent = TEAM_KEY_SET;
        state.classList.add("is-set");
      } else {
        state.textContent = TEAM_KEY_NOT_SET;
      }
      row.appendChild(state);

      rows.appendChild(row);
    }
  }

  /**
   * What the CURRENT selection means, said at selection time.
   *
   * Two ways a selection is a dead end and both are otherwise silent: the
   * provider has no key stored, or this build cannot call it at all.
   */
  function paintSelectionWarning(supported) {
    if (!useWarning) return;
    const current = useSelect && !useSelect.hidden
      ? useSelect.value
      : view.selection && view.selection.provider;
    let text = "";
    if (current && !providerIsCallable(current, supported)) {
      text = unusedProviderWarning(current, supported);
    } else if (current && view.stored !== null && !view.stored.includes(current)) {
      text = missingKeyForSelectionWarning(current);
    }
    useWarning.textContent = text;
    useWarning.hidden = text === "";
  }

  /** The warning under the key form, repainted whenever the picker changes. */
  function paintFormWarning(supported) {
    if (!warning || !providerSelect) return;
    const text = unusedProviderWarning(providerSelect.value, supported);
    warning.textContent = text;
    warning.hidden = text === "";
    if (saveBtn) {
      const present = new Set(view.stored || []);
      saveBtn.textContent = present.has(providerSelect.value) ? "Replace key" : "Save key";
    }
    if (input) {
      const p = apiKeyProvider(providerSelect.value);
      input.placeholder = p ? `${p.prefix}...` : "paste the key";
    }
    if (keyLabel) keyLabel.textContent = "Key";
  }

  /** Everything the section shows, from `view` and nothing else. */
  function paint() {
    const supported = (view.selection && view.selection.supported) || null;
    const selected = (view.selection && view.selection.provider) || null;
    const ids = allProviderIds();

    section.hidden = false;
    if (note) note.textContent = TEAM_KEY_COST_NOTE;

    fillProviders(ids, supported);

    // ---- which provider is spent ----
    const canChooseProvider = view.isAdmin === true && view.selection !== null;
    if (useRow) useRow.hidden = false;
    if (useSelect) {
      useSelect.hidden = !canChooseProvider;
      if (canChooseProvider && selected) useSelect.value = selected;
    }
    if (useValue) {
      useValue.hidden = canChooseProvider;
      if (view.selection === null) {
        // No selector on this server. Say what IS true rather than leaving a
        // gap where a control would be.
        useValue.textContent = providerLabel(PROVIDER_ANTHROPIC);
      } else {
        useValue.textContent = selected ? providerLabel(selected) : TEAM_KEY_UNKNOWN;
      }
    }
    if (fallbackNote) {
      fallbackNote.textContent =
        view.selection === null ? TEAM_KEY_NO_SELECTOR_NOTE : TEAM_KEY_FALLBACK_ONLY_NOTE;
    }
    paintSelectionWarning(supported);

    // ---- what is stored ----
    paintRows(ids, supported);
    if (storeNote) storeNote.textContent = TEAM_KEY_STORE_IS_NOT_SELECT;

    // ---- and who may change it ----
    //
    // The form is absent exactly when pressing it would 403: the server checks
    // membership.role === "admin" on this team, and that is the same row read
    // below. A form that always fails reads as the product being broken rather
    // than as permission being withheld.
    if (form) form.hidden = view.isAdmin !== true;
    if (hint) {
      hint.textContent = TEAM_KEY_REPLACE_NOTE;
      hint.hidden = view.isAdmin !== true;
    }
    if (memberNote) {
      memberNote.hidden = view.isAdmin === true;
      memberNote.textContent =
        view.isAdmin === false ? TEAM_KEY_MEMBER_NOTE : TEAM_KEY_ROLE_UNKNOWN_NOTE;
    }
    paintFormWarning(supported);
  }

  /**
   * Am I an admin of this team? From the membership row the server itself
   * checks, so the control and the permission cannot disagree.
   *
   * @returns {Promise<boolean|null>} null means the read failed - which is NOT
   *   "member". Rendering the member sentence over a failed read would tell an
   *   admin they are not one.
   */
  async function readIsAdmin(teamId) {
    const me = getSelfUserId();
    if (!me) return null;
    try {
      const res = await api.membersRaw(teamId);
      if (!res.ok) return null;
      const members = await res.json();
      if (!Array.isArray(members)) return null;
      const mine = members.find((m) => m && String(m.user_id) === String(me));
      return mine ? mine.role === "admin" : false;
    } catch (e) {
      return null;
    }
  }

  /** The stored providers, or null when we could not find out. */
  async function readStored(teamId) {
    try {
      const res = await api.listTeamApiKeysRaw(teamId);
      if (!res.ok) return null;
      return readApiKeyProviders(await res.json());
    } catch (e) {
      return null;
    }
  }

  /** The team's fallback selection, or null when this build has no selector. */
  async function readSelection(teamId) {
    try {
      const res = await api.teamFallbackProviderRaw(teamId);
      if (!res.ok) return null;
      return readFallbackSelection(await res.json());
    } catch (e) {
      return null;
    }
  }

  /** Validate, PUT, and forget. */
  async function save() {
    if (!input || !providerSelect) return;
    const teamId = view.teamId;
    if (!teamId) {
      setStatusLine(status, "Join a team before setting a key.", "error");
      return;
    }
    const provider = providerSelect.value;
    const verdict = validateApiKey(provider, input.value);
    if (!verdict.ok) {
      // Refused HERE, before a round-trip: the server takes any non-empty
      // string, so a fat-fingered paste would be stored and only surface later
      // as an agent that silently stops answering.
      setStatusLine(status, verdict.message, "error");
      return;
    }
    if (saveBtn) saveBtn.disabled = true;
    setStatusLine(status, "Saving...", "loading");
    try {
      const res = await api.putTeamApiKeysRaw(teamId, [
        { provider, api_key: verdict.key },
      ]);
      if (!res.ok) {
        setStatusLine(status, describeApiKeyFailure({ status: res.status }), "error");
        return;
      }
      // Accepted - drop it out of the DOM before anything else can read it.
      input.value = "";
      const supported = (view.selection && view.selection.supported) || null;
      const selected = (view.selection && view.selection.provider) || null;
      setStatusLine(
        status,
        teamKeySavedMessage(provider, { selected, supported }),
        "success",
      );
      if (view.stored !== null && !view.stored.includes(provider)) {
        view.stored = view.stored.concat([provider]);
      }
      paint();
    } catch (e) {
      // fetch() itself rejected. No status, because no response arrived, which
      // is its own sentence - and the caught error is never rendered: it is the
      // one string on this path that could carry the paste back.
      setStatusLine(status, describeApiKeyFailure(null), "error");
    } finally {
      if (saveBtn) saveBtn.disabled = false;
    }
  }

  /** Change WHICH stored key the agent falls back to. No secret involved. */
  async function chooseProvider() {
    if (!useSelect) return;
    const teamId = view.teamId;
    const provider = useSelect.value;
    // The warning repaints FIRST, so somebody choosing a provider with no key
    // sees why before the request even lands.
    paintSelectionWarning((view.selection && view.selection.supported) || null);
    if (!teamId) return;
    useSelect.disabled = true;
    setStatusLine(status, "Saving...", "loading");
    try {
      const res = await api.putTeamFallbackProviderRaw(
        teamId,
        fallbackSelectionBody(provider),
      );
      if (!res.ok) {
        // A 404 here does not mean the team vanished - it means this build has
        // no selector - so it gets its own sentence.
        setStatusLine(
          status,
          res.status === 404
            ? TEAM_KEY_NO_SELECTOR_NOTE
            : describeApiKeyFailure({ status: res.status }),
          "error",
        );
        return;
      }
      view.selection = {
        provider,
        supported: (view.selection && view.selection.supported) || null,
      };
      setStatusLine(
        status,
        `The agent will now fall back to the ${providerLabel(provider)} key.`,
        "success",
      );
      paint();
    } catch (e) {
      setStatusLine(status, describeApiKeyFailure(null), "error");
    } finally {
      useSelect.disabled = false;
    }
  }

  /** Listeners, attached once. mountTeamKeys runs again after a re-sign-in. */
  function wire() {
    if (wired) return;
    wired = true;
    if (saveBtn) saveBtn.addEventListener("click", () => save());
    if (input) {
      input.addEventListener("keydown", (event) =>
        event.key === "Enter" ? save() : undefined,
      );
    }
    if (providerSelect) {
      providerSelect.addEventListener("change", () =>
        paintFormWarning((view.selection && view.selection.supported) || null),
      );
    }
    if (useSelect) useSelect.addEventListener("change", () => chooseProvider());
  }

  wire();

  /**
   * A refresh already in flight, so a second caller joins it instead of firing
   * three more requests.
   *
   * TWO CALLERS REACH THIS ON THE SAME TAP: opening the sheet refreshes, and
   * the bridge notice opens the sheet AND wants to focus the section once the
   * answer lands. Without this, one press costs six requests and the two rounds
   * race each other to paint.
   */
  let inFlight = null;

  /**
   * Re-read everything for the team being read RIGHT NOW.
   *
   * The three reads go together rather than in series: opening a settings sheet
   * should not cost three round trips end to end.
   */
  function refresh() {
    if (inFlight) return inFlight;
    inFlight = readAll().finally(() => {
      inFlight = null;
    });
    return inFlight;
  }

  async function readAll() {
    const teamId = getTeamId();
    if (!teamId) {
      // No team, nothing to key. Hidden rather than shown empty: a block about
      // a team you are not in is a block that can only confuse.
      section.hidden = true;
      return;
    }
    // A team switch must not leave the previous team's answer on screen while
    // the new one loads.
    if (teamId !== view.teamId) {
      view.stored = null;
      view.selection = null;
      view.isAdmin = null;
      setStatusLine(status, "", "");
    }
    view.teamId = teamId;
    section.hidden = false;

    const [stored, selection, isAdmin] = await Promise.all([
      readStored(teamId),
      readSelection(teamId),
      readIsAdmin(teamId),
    ]);
    // A team switched under the reads: their answers belong to a team nobody is
    // looking at any more.
    if (getTeamId() !== teamId) return;
    view.stored = stored;
    view.selection = selection;
    view.isAdmin = isAdmin;
    paint();
  }

  /**
   * Put the caret on the section, for somebody sent here from the bridge notice.
   *
   * THE HEADING, NEVER THE FIELD. Focusing a text input raises the on-screen
   * keyboard before anybody asked to type - the same reason the settings sheet
   * takes its close button rather than the name field - and a member has no
   * field to focus at all.
   */
  function focus() {
    if (!heading || section.hidden) return;
    if (typeof heading.focus === "function") heading.focus();
    if (typeof heading.scrollIntoView === "function") {
      heading.scrollIntoView({ block: "start" });
    }
  }

  return { refresh, focus };
}

/** Hide the block. Signing out must not leave a team's key state on screen. */
export function hideTeamKeys() {
  const section = el("settings-team-key");
  if (section) section.hidden = true;
  const input = el("team-key-input");
  // Whatever was typed and not submitted goes with it: an unsent key left in a
  // detached input is a secret nothing will ever clean up.
  if (input) input.value = "";
  setStatusLine(el("team-key-status"), "", "");
}
