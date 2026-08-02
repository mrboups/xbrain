/**
 * The account block at the top of the settings sheet: picture, name, bio.
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
 * THE PICTURE IS A MEDIA ITEM, not a second upload path. There is no avatar
 * upload endpoint and there should not be one: the file goes through the same
 * POST /v1/media/upload the composer's "+" uses, and the profile is then
 * pointed at the id that comes back. A second path would mean a second size
 * cap, a second set of failure codes and a second thing to keep in step with
 * the server.
 *
 * AND THE URL IS NEVER KEPT. `avatar_url` is minted per read and its signature
 * expires within the hour, so it is read, painted and forgotten. A cached or
 * persisted copy is a picture that works right up until it silently stops.
 *
 * NOTHING here may ask for notification access or touch a push subscription
 * (D-27-05) — push.js owns the single click-gated call site.
 */

import { setStatusLine } from "./chat_core/dom.js";
import { MAX_MEDIA_BYTES } from "./chat_core/api.js";
import { MEMORY_API_BASE } from "./auth.js";

const el = (id) => document.getElementById(id);

/** The profile endpoint. One string, so a rename is one edit. */
const PROFILE_PATH = "/v1/me/profile";

/** Where the profile is pointed at an already-uploaded media item. */
const AVATAR_PATH = "/v1/me/profile/avatar";

/**
 * The picture ceiling, enforced BEFORE the upload rather than after it.
 *
 * A phone camera produces files far larger than any 38px square needs, and
 * discovering that after a minute of upload — from a 413 nobody surfaced — is
 * the worst version of this. Clamped by the server's own media ceiling so this
 * number can only ever be the stricter of the two.
 */
const AVATAR_MAX_BYTES = Math.min(4 * 1024 * 1024, MAX_MEDIA_BYTES);

/** The same number in the words the message uses. */
const AVATAR_MAX_MB = Math.round(AVATAR_MAX_BYTES / (1024 * 1024));

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
 * Read the profile, or null.
 *
 * 404 is "this API is not deployed yet", 401/403 is "not this person's to
 * read", a throw is the network. None of them is worth a message: one is our
 * own rollout order and the other two are already handled elsewhere. The caller
 * decides what an absent profile means.
 *
 * @param {{rawFetch: Function}} api
 * @returns {Promise<Object|null>}
 */
async function readProfile(api) {
  try {
    const res = await api.rawFetch(PROFILE_PATH);
    if (!res.ok) return null;
    return await res.json();
  } catch (e) {
    return null;
  }
}

/**
 * Fill the block and wire the name field and the picture.
 *
 * @param {{rawFetch: Function, uploadMediaRaw: Function}} api the shared client
 *   — this module builds no Authorization header and no multipart body of its
 *   own
 * @param {string} [fallbackLabel] what the shell already knows this account is
 *   called. Used when the endpoint answers nothing usable, so the block still
 *   says something true instead of sitting empty.
 * @param {{getTeamSlug?: () => (string|null)}} [refs]
 *   getTeamSlug — the team the picture is uploaded under. Read LATE, at the
 *   moment of upload: the active team changes, and a slug captured at mount is
 *   the wrong team by the first switch.
 * @returns {Promise<{editable: boolean, photoEditable: boolean}>} whether the
 *   name field and the picture controls are live — returned for the tests, and
 *   for a caller that wants to know
 */
export async function mountProfile(api, fallbackLabel, refs = {}) {
  const block = el("settings-profile");
  const nameInput = el("profile-name");
  const bio = el("profile-bio");
  const avatar = el("profile-avatar");
  const status = el("profile-status");
  const picker = el("profile-avatar-input");
  const avatarBtn = el("btn-profile-avatar");
  const photoBtn = el("btn-profile-photo");
  if (!block || !nameInput) return { editable: false, photoEditable: false };

  block.hidden = false;
  setStatusLine(status, "", "");

  const profile = await readProfile(api);

  // The resolved label, under whichever of the three names the server sends.
  const resolved =
    (profile &&
      (profile.label || profile.author_label || profile.display_label)) ||
    fallbackLabel ||
    "";

  paint(profile, resolved);

  if (!profile) {
    // READ-ONLY. Quiet: the block shows who is signed in and refuses edits,
    // rather than accepting typing it cannot save. The picture goes with it —
    // an endpoint that is not there cannot accept a photo either, and a control
    // that can only fail is worse than an absent one.
    nameInput.readOnly = true;
    nameInput.disabled = true;
    if (avatarBtn) avatarBtn.disabled = true;
    if (photoBtn) photoBtn.hidden = true;
    return { editable: false, photoEditable: false };
  }

  nameInput.readOnly = false;
  nameInput.disabled = false;
  if (avatarBtn) avatarBtn.disabled = false;
  if (photoBtn) photoBtn.hidden = false;
  wireSave(api, nameInput, status, resolved);
  wireAvatar(api, {
    picker,
    avatarBtn,
    photoBtn,
    status,
    getTeamSlug:
      typeof refs.getTeamSlug === "function" ? refs.getTeamSlug : () => null,
    onChanged: (fresh) => paintAvatar(fresh, resolved),
  });
  return { editable: true, photoEditable: Boolean(picker) };

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

    paintAvatar(data, label);
  }

  /**
   * The picture alone.
   *
   * Separate from paint() because a picture that just changed must NOT take the
   * name field with it: re-running the whole block would overwrite whatever the
   * person had typed but not yet saved.
   */
  function paintAvatar(data, label) {
    if (!avatar) return;
    while (avatar.firstChild) avatar.removeChild(avatar.firstChild);
    const src = data && data.avatar_url;
    if (src) {
      const img = document.createElement("img");
      img.className = "xb-profile-img";
      img.alt = "";
      // Straight from the read that produced it, and kept nowhere: the
      // signature in this URL expires.
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

/**
 * The picture, from the file picker to the server and back.
 *
 * TWO REQUESTS, IN THIS ORDER, AND BOTH CARRY THE SAME TEAM:
 *   1. POST /v1/media/upload with X-Team-Scope — the shared multipart call site
 *      in chat-core, the same one the composer's "+" uses. It answers the media
 *      item.
 *   2. PUT /v1/me/profile/avatar with {media_item_id} AND the same
 *      X-Team-Scope. That header is not decoration: the route resolves it
 *      through the membership check the chat uses, which refuses a non-member
 *      and a blocked member, and the item is looked up INSIDE that scope — a
 *      different team's slug is a 404, not a silent success.
 *
 * Then the profile is read again and the picture swapped in place. Read, not
 * assumed: the fresh URL comes from the server that just accepted the change,
 * and it is never stored anywhere.
 *
 * EVERY FAILURE SAYS SOMETHING. A photo that silently does not change is the
 * worst outcome here — the person has no way to tell a rejected file from a
 * broken app, and will try again with the same file.
 *
 * @param {Object} api the shared client
 * @param {Object} refs picker/buttons/status + getTeamSlug + onChanged
 */
function wireAvatar(api, refs) {
  const { picker, avatarBtn, photoBtn, status, getTeamSlug, onChanged } = refs;
  // No picker in the markup: the two buttons would open nothing, so they are
  // not wired at all rather than wired to a dead end.
  if (!picker) return;

  const openPicker = () => picker.click();
  if (avatarBtn) avatarBtn.addEventListener("click", openPicker);
  if (photoBtn) photoBtn.addEventListener("click", openPicker);

  let busy = false;

  picker.addEventListener("change", async (event) => {
    const target = event.target || {};
    const file = (target.files && target.files[0]) || null;
    // Cleared FIRST, so picking the same file twice in a row still fires.
    target.value = "";
    if (!file || busy) return;
    await upload(file);
  });

  /** Both controls disabled while a picture is in flight, and back after. */
  function setBusy(value) {
    busy = value;
    if (avatarBtn) avatarBtn.disabled = value;
    if (photoBtn) photoBtn.disabled = value;
  }

  async function upload(file) {
    // Refused HERE, before a byte leaves the device: a round trip that ends in
    // a 413 costs a minute of upload to say what a size check says instantly.
    if (!String(file.type || "").startsWith("image/")) {
      setStatusLine(status, "Pick an image file - a photo, not a document.", "error");
      return;
    }
    if (file.size > AVATAR_MAX_BYTES) {
      setStatusLine(status, `That picture is too big - pick one under ${AVATAR_MAX_MB} MB.`, "error");
      return;
    }
    const slug = getTeamSlug();
    if (!slug) {
      // The media path is team-scoped end to end. Without a team there is
      // nowhere to put the file, and saying so beats a 403 from a header the
      // person never knew existed.
      setStatusLine(status, "Join a team before adding a picture.", "error");
      return;
    }

    setBusy(true);
    setStatusLine(status, "Uploading your picture...", "loading");
    try {
      const uploaded = await api.uploadMediaRaw(slug, file, {
        source_surface: "pwa-avatar",
        truth_level: "WORKING",
      });
      if (!uploaded.ok) {
        setStatusLine(status, uploadError(uploaded.status), "error");
        return;
      }
      const item = await uploaded.json();
      const itemId = item && (item.item_id || item.id);
      if (!itemId) {
        setStatusLine(status, "The upload came back without a file id.", "error");
        return;
      }

      const attached = await api.rawFetch(AVATAR_PATH, {
        method: "PUT",
        // The team the item was just uploaded under. The same slug twice, on
        // purpose: the item and the profile row must agree about which team
        // holds the picture.
        headers: { "X-Team-Scope": slug },
        body: { media_item_id: itemId },
      });
      if (!attached.ok) {
        setStatusLine(status, attachError(attached.status), "error");
        return;
      }

      const fresh = await readProfile(api);
      if (fresh) {
        onChanged(fresh);
        setStatusLine(status, "Picture updated", "success");
      } else {
        // The change landed — the PUT said so. Only the read back failed, and
        // claiming failure here would have them upload it a second time.
        setStatusLine(status, "Picture saved - reload to see it.", "success");
      }
    } catch (e) {
      setStatusLine(status, "Network error - your picture was not changed.", "error");
    } finally {
      setBusy(false);
    }
  }
}

/** What went wrong on the upload, in words the person can act on. */
function uploadError(httpStatus) {
  if (httpStatus === 413) return `That picture is too big - pick one under ${AVATAR_MAX_MB} MB.`;
  if (httpStatus === 415 || httpStatus === 422) return "That file is not an image this app can show.";
  if (httpStatus === 403) return "You are not allowed to upload to this team.";
  return `Could not upload your picture (HTTP ${httpStatus}).`;
}

/** ... and on pointing the profile at it, which fails for different reasons. */
function attachError(httpStatus) {
  // 404 covers both "the endpoint is not deployed" and the route's deliberate
  // no-oracle answer for an item in another team. Neither is something the
  // person can fix by picking a different photo.
  if (httpStatus === 404) return "Pictures cannot be changed right now.";
  if (httpStatus === 403) return "You are not allowed to change this in this team.";
  if (httpStatus === 422) return "That file is not an image this app can show.";
  return `Could not save your picture (HTTP ${httpStatus}).`;
}

/** Hide the block. Signing out must not leave somebody's name on the screen. */
export function hideProfile() {
  const block = el("settings-profile");
  if (block) block.hidden = true;
  const status = el("profile-status");
  setStatusLine(status, "", "");
}
