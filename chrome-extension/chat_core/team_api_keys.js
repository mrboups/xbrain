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
 * deployment's agent can actually call it.
 *
 * `callable` IS THE HONEST FIELD, and it is not decoration. The server accepts
 * any provider string and encrypts whatever it is given, but today the agent's
 * fallback resolves `provider="anthropic"` and constructs an Anthropic client;
 * a key stored under any other name is accepted, encrypted, kept — and never
 * called. Somebody who buys one to unblock their team gets no answer and no
 * explanation. So the flag exists, both surfaces read it, and the copy below
 * says it out loud.
 *
 * IT IS A DEFAULT, NOT THE AUTHORITY. Real OpenAI and xAI streaming is being
 * built in parallel, and when the selection endpoint reports a `supported` list
 * that list wins (see providerIsCallable) — so the day the server can call all
 * three, this table stops mattering rather than having to be right. Flipping one
 * by hand is a claim about the SERVER and belongs in the same change that
 * teaches the agent to stream it.
 *
 * `team_api_keys.provider` is a plain 64-char column, so this list is the whole
 * definition of the OFFERED set, never of the possible one. A provider the
 * server reports that is absent here still gets a row on both surfaces.
 */
export const API_KEY_PROVIDERS = [
  { id: PROVIDER_ANTHROPIC, label: "Anthropic (Claude)", prefix: "sk-ant-", callable: true },
  { id: "openai", label: "OpenAI (GPT)", prefix: "sk-", callable: false },
  { id: "xai", label: "xAI (Grok)", prefix: "xai-", callable: false },
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
 * CAN the agent call a key stored under this provider?
 *
 * Not "does it right now" — that is the team's selection, which is a different
 * question with a different answer (see readFallbackSelection). This one is
 * about the deployment: whether a key stored here could ever be spent at all.
 *
 * @param {string} id
 * @param {string[]|null} [supported] the server's own list, when it has told us
 *   one. AUTHORITATIVE when present: a build that streams all three says so, and
 *   no table in a client gets to disagree with it.
 * @returns {boolean} FALSE for anything unknown, deliberately. A provider this
 *   build has never heard of is not one the fallback resolves, and guessing
 *   optimistically is exactly the failure the flag exists to prevent.
 */
export function providerIsCallable(id, supported = null) {
  if (Array.isArray(supported)) return supported.includes(id);
  const p = apiKeyProvider(id);
  return Boolean(p && p.callable);
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

/* ==========================================================================
 * WHICH stored key the agent falls back to
 *
 * STORING A KEY AND SELECTING A PROVIDER ARE DIFFERENT ACTIONS. A team may hold
 * three keys and spend exactly one; pasting a fourth changes nothing about which
 * one answers. Every sentence in this block exists because an interface that
 * blurs the two produces somebody who pastes a key, sees "saved", and waits for
 * an answer that was never going to come from it.
 *
 * THE SERVER HALF IS BEING BUILT IN PARALLEL and its route shape is an
 * ASSUMPTION, declared here and nowhere else so that reconciling it costs one
 * edit. What is assumed:
 *
 *   GET  /v1/teams/{id}/fallback-provider  — any member.
 *        -> {provider: "anthropic", supported: ["anthropic"]}
 *           `provider` is the team's current selection. `supported` is optional;
 *           when present it is the set this build can actually stream.
 *   PUT  /v1/teams/{id}/fallback-provider  — admin only, like the key write.
 *        body {provider: "openai"} -> 204
 *
 * UNTIL IT EXISTS, both routes answer 404, and both surfaces must degrade to
 * "no selection control, and the static table's word on what gets called"
 * rather than throwing inside a settings sheet. That is why the api.js calls
 * are raw.
 * ======================================================================== */

/**
 * Read the selection endpoint down to the two facts a surface may use.
 *
 * Narrowed like readApiKeyProviders, and for the same reason: a body that grows
 * a field must not silently grow the screen.
 *
 * @param {any} body the parsed response, or null when the read failed
 * @returns {{provider: string|null, supported: string[]|null}} provider is null
 *   when the body names none — which a surface must render as "we do not know",
 *   never as Anthropic. Assuming the default here would tell a team that had
 *   selected OpenAI that it is on Claude.
 */
export function readFallbackSelection(body) {
  const data = body && typeof body === "object" ? body : {};
  const provider =
    typeof data.provider === "string" && data.provider.trim()
      ? data.provider.trim()
      : null;
  const supported = Array.isArray(data.supported)
    ? data.supported.filter((p) => typeof p === "string" && p.trim()).map((p) => p.trim())
    : null;
  return { provider, supported };
}

/**
 * The body of the selection write, in one place.
 *
 * @param {string} providerId
 * @returns {{provider: string}}
 */
export function fallbackSelectionBody(providerId) {
  return { provider: String(providerId) };
}

/**
 * What a person is told about a provider they have SELECTED but stored no key
 * for.
 *
 * A real state, and one the server answers by naming that provider as
 * unavailable rather than quietly reaching for another. Said at selection time,
 * because the alternative is finding out when the agent goes silent.
 *
 * @param {string} providerId
 * @returns {string}
 */
export function missingKeyForSelectionWarning(providerId) {
  const label = providerLabel(providerId);
  return (
    `No key is stored for ${label}, so the agent has nothing to fall back to — ` +
    `it will report ${label} as unavailable rather than quietly using another ` +
    "provider. Store one below, or select a provider that already has a key."
  );
}

/**
 * What the selection does NOT do.
 *
 * The subscription is preferred and free whenever a browser holds the bridge;
 * this choice only decides what happens when none does. Somebody must not leave
 * this screen believing they have just picked which model always answers them.
 */
export const TEAM_KEY_FALLBACK_ONLY_NOTE =
  "This only chooses the fallback. While a browser is sharing the Claude " +
  "subscription that is what answers, free, whichever provider is selected here.";

/**
 * ...and the other half of the same confusion, said beside the key form.
 */
export const TEAM_KEY_STORE_IS_NOT_SELECT =
  "Storing a key does not switch the agent to it. A team can hold one key per " +
  "provider; the agent spends the selected one.";

/** The label above the selection control on both surfaces. */
export const TEAM_KEY_SELECTION_LABEL = "Used by the agent";

/**
 * What is shown where the selection control would be, on a build whose server
 * has no selector yet.
 *
 * Names what IS true today rather than leaving a gap: the agent calls the one
 * provider it can call, and nothing on this screen changes that.
 */
export const TEAM_KEY_NO_SELECTOR_NOTE =
  `Choosing the provider is not available in this build — the agent falls back ` +
  `to the ${providerLabel(PROVIDER_ANTHROPIC)} key.`;

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
 * THREE OUTCOMES, because "saved" alone is how somebody pays for a key, sees a
 * green line, and still gets no answer:
 *   - it is the selected provider, so it is now the fallback;
 *   - it is stored while another provider is selected, so it sits unspent;
 *   - the agent cannot call this provider at all in this build.
 *
 * @param {string} providerId the provider just saved
 * @param {{selected?: string|null, supported?: string[]|null}} [ctx]
 *   selected — the team's current fallback selection, or null when this build
 *   has no selector to ask. Null falls back to the callable question, which is
 *   the only one that has an answer without it.
 * @returns {string}
 */
export function teamKeySavedMessage(providerId, ctx = {}) {
  const label = providerLabel(providerId);
  const supported = ctx.supported === undefined ? null : ctx.supported;
  const selected = ctx.selected === undefined ? null : ctx.selected;
  if (!providerIsCallable(providerId, supported)) {
    return `${label} key saved, but the agent cannot call ${label} yet — this will not make it answer.`;
  }
  if (selected && selected !== providerId) {
    return (
      `${label} key saved. The agent is set to use ${providerLabel(selected)}, ` +
      `so this key is stored and not spent until ${label} is selected.`
    );
  }
  return `${label} key saved. The agent will use it when the subscription is unreachable.`;
}

/**
 * The warning shown BEFORE a key for an uncallable provider is pasted.
 *
 * Before, not after: the cost of finding out afterwards is a paid API key bought
 * for nothing.
 *
 * @param {string} providerId
 * @param {string[]|null} [supported] the server's list, when known
 * @returns {string} empty when the provider IS callable, so a caller can render
 *   the return value unconditionally
 */
export function unusedProviderWarning(providerId, supported = null) {
  if (providerIsCallable(providerId, supported)) return "";
  return (
    `The agent cannot call ${providerLabel(providerId)} in this build. A key ` +
    "stored for it is kept, encrypted, and never spent, so setting one now " +
    "will not get your team an answer."
  );
}

/** The short marker beside an uncallable provider's row. */
export const TEAM_KEY_UNUSED_MARK = "not called by the agent yet";

/**
 * What the key costs, in one sentence, before anybody pastes anything.
 *
 * WHO CAN SPEND IT is the half that is easy to leave out. The server resolves
 * the fallback on team_id with no user parameter, so the moment a key is stored
 * every member of that team spends it — including one who joins tomorrow, on a
 * phone, with nothing to configure and no idea a key exists. "Billed to your
 * team" alone reads as an accounting detail; it is a shared budget any member's
 * message can draw on, and the person pasting it is the one who needs to know
 * that.
 *
 * One sentence on purpose. This is the fact that changes somebody's mind, and a
 * paragraph is where it goes unread.
 */
export const TEAM_KEY_COST_NOTE =
  "This key is billed to your team and shared by it — any member's message can " +
  "spend it, and only when no browser is sharing the Claude subscription.";

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
 * and "ask an admin" is a next step.
 *
 * AND IT MUST NOT READ AS BEING LOCKED OUT. The fallback resolves per team, so a
 * member already spends whatever the team has stored without configuring
 * anything; what they cannot do is CHANGE it. A sentence that only says "only an
 * admin can" leaves somebody believing the agent is an admin feature.
 */
export const TEAM_KEY_MEMBER_NOTE =
  "You already use the team's key automatically — every member does. Only a " +
  "team admin can store or change one, so ask one of yours if the agent says " +
  "it is unavailable.";

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
