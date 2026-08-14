/**
 * The brain tag — one message, kept out of the team chat.
 *
 * WHY THE WORDS LIVE HERE. This control is an icon with no label, so every word
 * a person ever reads about it comes from these strings. Both surfaces import
 * them so the extension and the web app cannot drift into describing the same
 * feature differently.
 *
 * THE ONE RULE THEY EXIST TO KEEP. The tag keeps a message out of the CHAT. It
 * does NOT make it secret: the note lands in the team's brain at full length,
 * any teammate can find it, and the assistant will quote it when someone else
 * asks a question it answers. So the words never say private, secret or
 * confidential — naming those, even to deny them, is what plants the idea. The
 * closed-eye icon already whispers "hidden"; the copy is what stops that from
 * becoming a promise the product cannot keep.
 *
 * Approved by the owner 2026-08-13 after the wr + verify-copy pass. The earlier
 * draft claimed "a search for any phrase you used will land on it" — cut,
 * because the knowledge-base text search matches the opening of a note, not all
 * of it, and a reassurance that is only mostly true is the kind this feature
 * cannot afford.
 */

/** Hover text on the icon. The only words most people will ever see. */
export const TOOLTIP =
  "Ask without posting to the chat. Still saved to the team knowledge base.";

/** Shown once, the first time the tag is armed, before the message goes. */
export const FIRST_USE = {
  title: "Off the chat, still in the team knowledge base",
  body:
    "Only you see this message in the chat, and only you see the reply. It is " +
    "still saved to the team knowledge base, so when a teammate asks the " +
    "assistant something your message touches on, the assistant can quote it " +
    "back to them, word for word. Write it as if they will read it.",
  confirm: "Send off the chat",
  cancel: "Keep editing",
};

/** Sits beside a tagged message, so the author knows why nobody replied. */
export const BUBBLE_LABEL =
  "Only you see this in the chat. Saved to the team knowledge base.";

/** Storage key for "they have read the explanation". */
export const FIRST_USE_SEEN_KEY = "xbrainBrainTagExplained";

/**
 * Has the first-use explanation already been shown?
 *
 * Takes the platform's storage rather than reaching for one: the two surfaces
 * back it with different APIs, and naming either of them here would make this
 * module unportable. Both shims expose the same shape —
 * `get([key]) -> object`, `set({key: value})`.
 */
export async function firstUseSeen(storage) {
  try {
    const got = await storage.get([FIRST_USE_SEEN_KEY]);
    return Boolean(got && got[FIRST_USE_SEEN_KEY]);
  } catch {
    // A storage read that fails must not block sending. Showing the
    // explanation twice is a small cost; swallowing a message is not.
    return false;
  }
}

export async function markFirstUseSeen(storage) {
  try {
    await storage.set({ [FIRST_USE_SEEN_KEY]: true });
  } catch {
    /* see above — never block the send */
  }
}
