/**
 * The team model-API-key vocabulary: which providers exist, which one the agent
 * actually spends, what a plausible key looks like, and every sentence either
 * surface says about it.
 *
 * WHY THIS IS A MODULE AND NOT A SECTION OF A PAGE. Two surfaces offer this
 * feature — the desktop admin page at app-site/account/teams/ and the PWA's
 * settings sheet — and the second one exists precisely because the first is
 * unreachable from a phone. A second copy of the provider table is a second
 * place to forget that only one of its rows is ever called; a second copy of the
 * failure text is two products that disagree about what a 403 means.
 *
 * WHAT IS NOT HERE: the request. That lives in api.js, behind
 * listTeamApiKeysRaw / putTeamApiKeysRaw, for the same reason — one place builds
 * the Authorization header. And no DOM: both surfaces draw their own controls,
 * because a settings sheet on a phone and a team card on a desktop are not the
 * same shape and pretending otherwise produces a component neither one wants.
 *
 * THE KEY ITSELF NEVER TOUCHES THIS FILE except as the argument to
 * validateApiKey, which returns it trimmed and keeps nothing. Nothing here
 * stores it, logs it, or puts it in a message: every string below is fixed text
 * or a provider label, so no failure path can echo a pasted secret back onto a
 * screen.
 */

/** The provider the agent calls today. Named once, referenced everywhere. */
export const PROVIDER_ANTHROPIC = "anthropic";

/**
 * The providers a team may store a key for — and, for each, whether this
 * deployment ever spends it.
 *
 * `spent` IS THE HONEST FIELD, and it is not decoration. The server accepts any
 * provider string and encrypts whatever it is given, but the agent's fallback
 * resolves `provider="anthropic"` and constructs an Anthropic client; a key
 * stored under any other name is accepted, encrypted, kept — and never called.
 * Somebody who buys one to unblock their team gets no answer and no explanation.
 * So the flag exists, both surfaces read it, and the copy below says it out
 * loud. Flipping one to true is a claim about the SERVER, not about this table:
 * it belongs in the same change that teaches the agent to call that provider.
 *
 * `team_api_keys.provider` is a plain 64-char column, so this list is the whole
 * definition of the OFFERED set, never of the possible one. A provider the
 * server reports that is absent here still gets a row on both surfaces.
 */
export const API_KEY_PROVIDERS = [
  { id: PROVIDER_ANTHROPIC, label: "Anthropic (Claude)", prefix: "sk-ant-", spent: true },
  { id: "openai", label: "OpenAI (GPT)", prefix: "sk-", spent: false },
  { id: "xai", label: "xAI (Grok)", prefix: "xai-", spent: false },
];

/** The table row for a provider id, or null when it is not one we list. */
export function apiKeyProvider(id) {
  return API_KEY_PROVIDERS.find((p) => p.id === id) || null;
}

/** Its human name, falling back to the raw id for a provider we do not list. */
export function providerLabel(id) {
  const p = apiKeyProvider(id);
  return p ? p.label : String(id);
}

/**
 * Does the agent actually call a key stored under this provider?
 *
 * FALSE for anything unknown, deliberately. A provider this build has never
 * heard of is not one the agent's fallback resolves, and guessing optimistically
 * here is exactly the failure the flag exists to prevent.
 */
export function providerIsSpent(id) {
  const p = apiKeyProvider(id);
  return Boolean(p && p.spent);
}

/** The offered providers whose keys are actually spent. */
export function spentProviders() {
  return API_KEY_PROVIDERS.filter((p) => p.spent);
}

/**
 * Read GET /v1/teams/{id}/api-keys down to a list of provider names.
 *
 * NARROWED BY CONSTRUCTION, which is the point of it being a function. The route
 * answers `[{provider}]` and its response model declares that one field, so
 * presence is all a client can ever know — and if a prefix, a "last four" or a
 * ciphertext ever joined the shape, this reader would drop it without anybody
 * having to remember to. A surface that destructured the row itself would pick
 * up the new field the day it appeared.
 *
 * @param {any} rows the parsed response body
 * @returns {string[]} provider names, empty when the body says nothing usable
 */
export function readApiKeyProviders(rows) {
  if (!Array.isArray(rows)) return [];
  const out = [];
  for (const row of rows) {
    const provider =
      row && typeof row.provider === "string" ? row.provider.trim() : "";
    if (provider) out.push(provider);
  }
  return out;
}

/**
 * Decide whether a pasted value is plausibly a key for `providerId`, BEFORE
 * spending a round-trip on it. The server takes any non-empty string, so a
 * fat-fingered paste would otherwise be stored and only surface later as an
 * agent that silently stops answering.
 *
 * Deliberately loose — prefix plus length, not a full-shape regex — because a
 * provider rotating its key format must not lock admins out of their own screen.
 *
 * @param {string} providerId
 * @param {string} raw what was typed
 * @returns {{ok: true, key: string}|{ok: false, message: string}} the trimmed
 *   value, or a message naming what was expected. The key is returned, never
 *   retained.
 */
export function validateApiKey(providerId, raw) {
  const key = String(raw == null ? "" : raw).trim();
  if (!key) return { ok: false, message: "Paste a key first." };
  if (/\s/.test(key)) {
    return {
      ok: false,
      message:
        "That key has a space or line break in it — copy it again as a single line.",
    };
  }
  const provider = apiKeyProvider(providerId);
  if (!provider) {
    // Unknown provider (one the server returned that we do not list). Length is
    // all we can honestly check.
    if (key.length < 12) {
      return { ok: false, message: "That key looks too short — copy the whole value." };
    }
    return { ok: true, key };
  }
  if (!key.startsWith(provider.prefix)) {
    return {
      ok: false,
      message: `That doesn't look like a key for ${provider.label} — it should start with "${provider.prefix}".`,
    };
  }
  if (key.length < 20) {
    return {
      ok: false,
      message: `That key for ${provider.label} looks truncated — copy the whole value.`,
    };
  }
  return { ok: true, key };
}

/**
 * Turn a failed save into something the person can act on.
 *
 * Every branch returns FIXED text. Nothing from the server's body is ever
 * interpolated, so a 422 that echoed the posted key back cannot re-render it —
 * and "something went wrong" is not one of the branches, because a message that
 * names no next step is a message that wastes the one moment somebody is willing
 * to read.
 *
 * @param {{status?: number}|null} [err] anything carrying the HTTP status. No
 *   status at all means no response arrived — a DNS failure, an offline radio,
 *   a TLS refusal — which is its own distinct outcome: nothing was sent.
 * @returns {string}
 */
export function describeApiKeyFailure(err) {
  const status = err && typeof err.status === "number" ? err.status : null;
  if (status === 401) {
    return "Your session expired — sign in again, then save the key.";
  }
  if (status === 403) {
    return "You're not an admin of this team, so you can't set its key. Ask a team admin.";
  }
  if (status === 404) {
    return "This team no longer exists — reload the page.";
  }
  if (status === 422) {
    return "The server rejected that key — check you copied the whole value.";
  }
  if (status === 500) {
    return "The server can't store keys right now (its encryption key isn't configured). Nothing was saved.";
  }
  if (status !== null) {
    return `The server refused the key (HTTP ${status}). Nothing was saved.`;
  }
  return "Couldn't reach the server, so nothing was saved. Check your connection and try again.";
}

/**
 * What a landed save says.
 *
 * Two sentences, because there are two truths to tell and which one applies
 * depends on the provider: a key the agent calls is now the fallback, and a key
 * it does not call is stored and inert. Saying "saved" for both is how somebody
 * pays for a key, sees a green line, and still gets no answer.
 *
 * @param {string} providerId
 * @returns {string}
 */
export function teamKeySavedMessage(providerId) {
  const label = providerLabel(providerId);
  if (providerIsSpent(providerId)) {
    return `${label} key saved. The agent will use it when the subscription is unreachable.`;
  }
  return `${label} key saved, but the agent does not call ${label} yet — this will not make it answer.`;
}

/**
 * The warning shown BEFORE a key for an unspent provider is pasted.
 *
 * Before, not after: the cost of finding out afterwards is a paid API key bought
 * for nothing.
 *
 * @param {string} providerId
 * @returns {string} empty when the provider IS spent, so a caller can render the
 *   return value unconditionally
 */
export function unusedProviderWarning(providerId) {
  if (providerIsSpent(providerId)) return "";
  return (
    `The agent only calls the ${providerLabel(PROVIDER_ANTHROPIC)} key. A key ` +
    `stored for ${providerLabel(providerId)} is kept for a later release and is ` +
    "never spent, so setting one now will not get your team an answer."
  );
}

/** The short marker beside an unspent provider's row. */
export const TEAM_KEY_UNUSED_MARK = "not called by the agent yet";

/**
 * What the key costs, in one sentence, before anybody pastes anything.
 *
 * One sentence on purpose. This is the fact that changes somebody's mind, and a
 * paragraph is where it goes unread.
 */
export const TEAM_KEY_COST_NOTE =
  "This key is billed to your team, and the agent only spends it when no " +
  "browser is sharing the Claude subscription.";

/**
 * What saving does, and what it cannot undo.
 *
 * THE SERVER HAS NO DELETE ROUTE and `api_key` has min_length=1, so a stored key
 * can be replaced and never removed. There is therefore no Remove control on
 * either surface — a button that cannot work is worse than an absent one — and
 * this sentence is what stops that absence from reading as an oversight.
 */
export const TEAM_KEY_REPLACE_NOTE =
  "Saving replaces whatever is stored for that provider — a key cannot be " +
  "removed here once set, only replaced. It is encrypted on arrival and can " +
  "never be read back, not here and not by us.";

/**
 * What a non-admin is told instead of being shown a form that would 403.
 *
 * Not nothing, which is the tempting version: somebody who has just been told
 * the subscription is gone and cannot fix it themselves needs the next step,
 * and "ask an admin" is a next step. The free remedy is named again because it
 * is the one they CAN act on alone.
 */
export const TEAM_KEY_MEMBER_NOTE =
  "Only a team admin can set this key. Ask one of yours, or reopen the browser " +
  "where the xbrain extension is signed in to keep using the subscription.";

/**
 * What is said when we could not find out whether this person is an admin.
 *
 * A separate state from "member" on purpose: rendering the member sentence over
 * a failed read would tell an admin they are not one.
 */
export const TEAM_KEY_ROLE_UNKNOWN_NOTE =
  "Couldn't check whether you can set this key. Try again in a moment.";

/** The three things a row can say about a provider. Presence only, ever. */
export const TEAM_KEY_SET = "Set";
export const TEAM_KEY_NOT_SET = "Not set";

/**
 * ...and the fourth, for a read that FAILED.
 *
 * Not "Not set". Claiming an absence we have not confirmed invites an admin to
 * overwrite a key that was working.
 */
export const TEAM_KEY_UNKNOWN = "Unknown";
