/**
 * The account block at the top of the settings panel: picture, name, bio.
 *
 * THE RESPONSE SHAPE THIS ASSUMES (GET /v1/me/profile, and the same body back
 * from PATCH). The server half is being written in parallel, so every field is
 * optional to this file and nothing here breaks if one is missing:
 *
 *   preferred_name  string|null  the name the person set on themselves. The ONLY
 *                                writable field — PATCH sends {preferred_name}
 *                                and nothing else.
 *   bio             string|null  free text, rendered read-only (see below).
 *   label           string       the RESOLVED name: preferred_name -> the
 *                                provider's display_name -> the email local
 *                                part. It is what teammates see, so it is the
 *                                placeholder in the name field: an empty box
 *                                showing "Your name" hides the fact that they
 *                                already have one.
 *   avatar_url      string|null  a media path. Relative is expected — the chat's
 *                                own media arrives as "/v1/media/{id}/img?t=..."
 *                                — and an absolute URL is passed through
 *                                untouched, so either convention works.
 *
 * `author_label` and `display_label` are accepted as aliases for `label`,
 * because the messages endpoint already calls the same resolved string
 * `author_label` and the two halves of this feature are landing separately. If
 * the server settles on one, the others cost nothing.
 *
 * WHY THE 404 PATH IS THE INTERESTING ONE. This ships before the API does. An
 * unguarded fetch would throw inside the settings panel's boot, and the person
 * would open Settings to change the theme and find an empty box. So a 404 — or
 * any other failure — puts the block in READ-ONLY mode, showing the identity
 * the shell already knows, with no error and no save wiring. Nothing is
 * invented: an absent name shows the account identity the app signed in with,
 * and an absent picture shows the same square initial the chat draws.
 *
 * BIO IS DISPLAY-ONLY, deliberately. The brief made the name the editable
 * field, and PATCHing a `bio` the server half may not accept yet would answer a
 * person's typing with a 422 they cannot act on. The element hides itself when
 * there is nothing in it, so nobody is shown an empty row that never fills.
 *
 * NOTHING here may ask for notification access or touch a push subscription
 * (D-27-05) — push.js owns the single click-gated call site.
 */

import { setStatusLine } from "./chat_core/dom.js";
import { MEMORY_API_BASE } from "./auth.js";

const el = (id) => document.getElementById(id);

/** The profile endpoint. One string, so a rename is one edit. */
const PROFILE_PATH = "/v1/me/profile";

/**
 * Absolute URL for a media path the server minted.
 *
 * Relative is the convention the chat already uses (the signed image path), so
 * it gets the API origin; anything already absolute is left alone rather than
 * being mangled into a double origin.
 *
 * @param {string} path
 * @returns {string}
 */
function mediaUrl(path) {
  return /^https?:\/\//i.test(path) ? path : `${MEMORY_API_BASE}${path}`;
}

/** The square initial the chat draws when there is no picture. */
function initialOf(text) {
  const trimmed = (text || "").trim();
  return (trimmed[0] || "?").toUpperCase();
}

/**
 * Fill the block and wire the name field.
 *
 * @param {{rawFetch: Function}} api the shared client — this module builds no
 *   Authorization header of its own
 * @param {string} [fallbackLabel] what the shell already knows this account is
 *   called. Used when the endpoint answers nothing usable, so the block still
 *   says something true instead of sitting empty.
 * @returns {Promise<{editable: boolean}>} whether the name field is live —
 *   returned for the tests, and for a caller that wants to know
 */
export async function mountProfile(api, fallbackLabel) {
  const block = el("settings-profile");
  const nameInput = el("profile-name");
  const bio = el("profile-bio");
  const avatar = el("profile-avatar");
  const status = el("profile-status");
  if (!block || !nameInput) return { editable: false };

  block.hidden = false;
  setStatusLine(status, "", "");

  let profile = null;
  try {
    const res = await api.rawFetch(PROFILE_PATH);
    // 404 is "this API is not deployed yet", 401/403 is "not this person's to
    // read". Neither is worth a message here: one is our own rollout order and
    // the other is already handled by the sign-in path.
    if (res.ok) profile = await res.json();
  } catch (e) {
    // A network failure is not a profile edit failing — say nothing.
    profile = null;
  }

  // The resolved label, under whichever of the three names the server sends.
  const resolved =
    (profile &&
      (profile.label || profile.author_label || profile.display_label)) ||
    fallbackLabel ||
    "";

  paint(profile, resolved);

  if (!profile) {
    // READ-ONLY. Quiet: the block shows who is signed in and refuses edits,
    // rather than accepting typing it cannot save.
    nameInput.readOnly = true;
    nameInput.disabled = true;
    return { editable: false };
  }

  nameInput.readOnly = false;
  nameInput.disabled = false;
  wireSave(api, nameInput, status, resolved);
  return { editable: true };

  /** Write the three fields. Every value reaches the DOM as text. */
  function paint(data, label) {
    nameInput.value = (data && data.preferred_name) || "";
    // The resolved label as the placeholder: an empty field over "Your name"
    // suggests they have no name, when in fact teammates already see one.
    nameInput.placeholder = label || "Your name";

    const bioText = (data && data.bio) || "";
    if (bio) {
      bio.textContent = bioText;
      // Hidden when empty — an always-blank row reads as something broken.
      bio.hidden = !bioText;
    }

    if (!avatar) return;
    while (avatar.firstChild) avatar.removeChild(avatar.firstChild);
    const src = data && data.avatar_url;
    if (src) {
      const img = document.createElement("img");
      img.className = "xb-profile-img";
      img.alt = "";
      img.src = mediaUrl(src);
      avatar.appendChild(img);
      avatar.className = "xb-profile-avatar has-image";
    } else {
      // Not an invention — the same derived square the chat already draws for
      // anyone without a picture.
      avatar.textContent = initialOf(label);
      avatar.className = "xb-profile-avatar";
    }
  }
}

/**
 * Save on blur and on Enter — not on every keystroke.
 *
 * A per-keystroke PATCH would send a request for every letter of a name and
 * race its own replies, so the last write to land could be a prefix of what
 * they typed. Nothing is sent when the value has not changed.
 */
function wireSave(api, nameInput, status, resolvedAtLoad) {
  let lastSaved = nameInput.value;
  let saving = false;

  async function commit() {
    const next = nameInput.value.trim();
    if (saving || next === lastSaved.trim()) return;
    saving = true;
    setStatusLine(status, "Saving...", "loading");
    try {
      const res = await api.rawFetch(PROFILE_PATH, {
        method: "PATCH",
        // The one writable field. Empty string clears it, which restores the
        // provider's own name — that is the documented way back, so it is sent
        // rather than suppressed.
        body: { preferred_name: next },
      });
      if (res.ok) {
        lastSaved = next;
        const body = await res.json().catch(() => null);
        const label =
          (body && (body.label || body.author_label || body.display_label)) ||
          next ||
          resolvedAtLoad;
        nameInput.placeholder = label || "Your name";
        setStatusLine(status, "Saved", "success");
      } else if (res.status === 404) {
        // The endpoint went away under us (a rollback between load and save).
        setStatusLine(status, "Names cannot be changed right now.", "error");
        nameInput.readOnly = true;
      } else if (res.status === 422) {
        setStatusLine(status, "That name is too long or not allowed.", "error");
      } else {
        setStatusLine(status, `Could not save your name (HTTP ${res.status}).`, "error");
      }
    } catch (e) {
      setStatusLine(status, "Network error - your name was not saved.", "error");
    } finally {
      saving = false;
    }
  }

  nameInput.addEventListener("blur", commit);
  nameInput.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    nameInput.blur(); // which commits, so there is one save path and not two
  });
}

/** Hide the block. Signing out must not leave somebody's name on the screen. */
export function hideProfile() {
  const block = el("settings-profile");
  if (block) block.hidden = true;
  const status = el("profile-status");
  setStatusLine(status, "", "");
}
