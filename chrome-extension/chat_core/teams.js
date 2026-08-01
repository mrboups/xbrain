/**
 * Starting a team, and joining one (Phase 27, D-27-04).
 *
 * The product is a TEAM chat, so a person with no team has exactly two things to
 * do: create one, or redeem the invite they were sent. Both are first-class here
 * — burying the second in a hint that points at another overlay makes the
 * invited half of the audience hunt for the one thing they came to do.
 *
 * Lifted out of chrome-extension/popup.js so the PWA offers the same two doors.
 * The slug rule in particular is not re-derivable: the API accepts
 * `^[a-z][a-z0-9-]*$`, a name written in another script has to produce a valid
 * slug anyway, and two people naming their team the same thing must not dead-end
 * on a 409 they cannot act on.
 *
 * XSS discipline: team names and server strings reach the document through
 * createElement + textContent. This file assigns no markup string.
 */

import { setStatusLine, clearChildren } from "./dom.js";
import { mapJoinError } from "./invite.js";

/**
 * Turn a typed team name into a slug the API accepts (`^[a-z][a-z0-9-]*$`, <=64).
 *
 * Falls back to a generic base when the name has no usable ASCII letters, so a
 * name written entirely in another script still produces a valid slug instead of
 * a 422 the person cannot decode.
 *
 * @param {string} name
 * @returns {string}
 */
export function slugifyTeamName(name) {
  let s = (name || "")
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "") // strip accents
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64);
  if (!/^[a-z]/.test(s)) s = "team" + (s ? "-" + s : "");
  return s.slice(0, 64).replace(/-+$/, "");
}

/**
 * Create a team, retrying ONCE on a slug collision with a short random suffix.
 *
 * @param {Object} api chat-core api client
 * @param {string} displayName what the person typed
 * @returns {Promise<Object>} the created team as the server returned it
 * @throws {Error} `HTTP <status>` — the caller decides what to say about it
 */
export async function createTeam(api, displayName) {
  const name = (displayName || "").trim() || "My team";
  const base = slugifyTeamName(name);
  let res = null;
  for (const slug of [base, `${base}-${Math.random().toString(36).slice(2, 6)}`]) {
    res = await api.createTeamRaw(slug, name);
    if (res.status !== 409) break;
  }
  if (!res || res.status !== 201) {
    throw new Error(`HTTP ${res ? res.status : "network"}`);
  }
  return res.json();
}

/**
 * Render the create-or-join panel into a host element.
 *
 * Every node is built here rather than declared in each surface's markup: the
 * two surfaces would otherwise carry two copies of the same four controls, and
 * the copy that says what this product IS ("everyone shares one memory") would
 * drift on one of them.
 *
 * @param {{
 *   doc: Document,
 *   hostEl: Element,
 *   api: Object,
 *   onTeamCreated?: ((team: Object) => Promise<void>|void)|null,
 *   onTeamJoined?: ((result: Object) => Promise<void>|void)|null,
 *   autofocus?: boolean
 * }} opts
 * @returns {{focus: Function}}
 */
export function renderTeamStarter(opts) {
  const cfg = opts || {};
  const doc = cfg.doc;
  const hostEl = cfg.hostEl;
  const api = cfg.api;
  if (!doc || typeof doc.createElement !== "function") {
    throw new TypeError("renderTeamStarter requires opts.doc");
  }
  if (!hostEl) throw new TypeError("renderTeamStarter requires opts.hostEl");
  if (!api) throw new TypeError("renderTeamStarter requires opts.api");
  const onTeamCreated =
    typeof cfg.onTeamCreated === "function" ? cfg.onTeamCreated : null;
  const onTeamJoined =
    typeof cfg.onTeamJoined === "function" ? cfg.onTeamJoined : null;

  clearChildren(hostEl);

  const wrapper = doc.createElement("div");
  wrapper.className = "xb-starter";

  const msg = doc.createElement("p");
  msg.className = "xb-starter-text";
  msg.textContent =
    "Start your team chat. Create a team, then invite your teammates — everyone shares one memory, and @agent answers from it.";
  wrapper.appendChild(msg);

  const nameInput = doc.createElement("input");
  nameInput.type = "text";
  nameInput.className = "xb-starter-input";
  nameInput.placeholder = "Team name";
  nameInput.autocomplete = "off";
  nameInput.maxLength = 64;
  wrapper.appendChild(nameInput);

  const createBtn = doc.createElement("button");
  createBtn.type = "button";
  createBtn.className = "xb-starter-btn";
  createBtn.textContent = "Create team";
  wrapper.appendChild(createBtn);

  const divider = doc.createElement("p");
  divider.className = "xb-starter-divider";
  divider.textContent = "— or join a team you were invited to —";
  wrapper.appendChild(divider);

  const codeInput = doc.createElement("input");
  codeInput.type = "text";
  codeInput.className = "xb-starter-input";
  codeInput.placeholder = "Invite code (xbi_…)";
  codeInput.autocomplete = "off";
  codeInput.spellcheck = false;
  wrapper.appendChild(codeInput);

  const joinBtn = doc.createElement("button");
  joinBtn.type = "button";
  joinBtn.className = "xb-starter-btn";
  joinBtn.textContent = "Join team";
  wrapper.appendChild(joinBtn);

  const hint = doc.createElement("p");
  hint.className = "xb-starter-hint";
  hint.textContent =
    "If you were sent an invite link, opening it joins you automatically.";
  wrapper.appendChild(hint);

  // The status line belongs to this dynamically-built panel, so it is held in a
  // closure rather than looked up by id — it has no place in either surface's
  // static markup, and the popup contract test rightly refuses ids that are
  // referenced but never declared there.
  const status = doc.createElement("p");
  status.className = "xb-starter-status";
  status.hidden = true;
  wrapper.appendChild(status);

  hostEl.appendChild(wrapper);

  /** Run `work` with the button locked and a transient label. */
  async function withButton(btn, busyLabel, work) {
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = busyLabel;
    try {
      await work();
    } finally {
      if (btn.textContent === busyLabel) btn.textContent = original;
      btn.disabled = false;
    }
  }

  async function doCreate() {
    if (createBtn.disabled) return;
    setStatusLine(status, "", "");
    await withButton(createBtn, "Creating…", async () => {
      try {
        const team = await createTeam(api, nameInput.value);
        createBtn.textContent = "Ready ✓";
        if (onTeamCreated) await onTeamCreated(team);
      } catch (e) {
        setStatusLine(
          status,
          "Could not create the team — check your connection and try again.",
          "error",
        );
      }
    });
  }

  async function doJoin() {
    const code = codeInput.value.trim();
    if (!code) {
      codeInput.focus();
      return;
    }
    setStatusLine(status, "", "");
    await withButton(joinBtn, "Joining…", async () => {
      try {
        const res = await api.joinByCodeRaw(code);
        if (res.status === 200) {
          const j = await res.json();
          joinBtn.textContent = "Joined ✓";
          setStatusLine(
            status,
            `You are in ${j.display_name || j.slug || "the team"}.`,
            "success",
          );
          codeInput.value = ""; // a redeemed code is still a secret
          if (onTeamJoined) await onTeamJoined(j);
          return;
        }
        setStatusLine(status, mapJoinError(res.status), "error");
      } catch (e) {
        console.warn("[xbrain] join from the starter panel failed");
        setStatusLine(status, "Network error — try again.", "error");
      }
    });
  }

  createBtn.addEventListener("click", doCreate);
  nameInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") doCreate();
  });
  joinBtn.addEventListener("click", doJoin);
  codeInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") doJoin();
  });

  if (cfg.autofocus !== false && typeof nameInput.focus === "function") {
    nameInput.focus();
  }

  return { focus: () => nameInput.focus() };
}
