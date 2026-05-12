/**
 * xbrain — LibreChat API key auto-fill content script.
 *
 * Runs on chat.grooveos.app. When the user selects the "Claude Pro/Max"
 * custom endpoint, LibreChat opens an API-key dialog with a text input.
 * This script:
 *   1. Reads xbt_token from chrome.storage.local.
 *   2. Reads autoFillLibreChat setting from chrome.storage.sync.
 *   3. Watches the DOM for an input field that looks like an API key entry
 *      tied to the Claude Pro/Max endpoint.
 *   4. Fills the field on first match — once per page load, so the user can
 *      still clear and type a different key without us fighting them.
 *
 * Design constraints:
 *   - LibreChat DOM selectors are not stable across versions. Heuristic
 *     match (input[type=password] OR input with name/aria-label containing
 *     "api" + nearest text mentioning "Claude Pro/Max") is more robust than
 *     a brittle CSS selector.
 *   - Token NEVER reaches window.postMessage or page scripts — only the
 *     specific <input> value is set, then a synthetic `input` event is
 *     dispatched so React picks up the change.
 *   - If autoFillLibreChat=false, the script just exits — no DOM access.
 *
 * Helpers exported via globalThis.xbrainLibreChatAutofill for testability:
 *   shouldFillField(input, root) → boolean
 *   fillInputAsReact(input, value) → void
 */

(function () {
  "use strict";

  let filled = false;
  let observer = null;
  let cachedToken = null;
  let cachedSetting = null;

  /**
   * Returns true when the given <input> looks like the API key field for
   * the Claude Pro/Max endpoint. Used by the MutationObserver and by tests.
   *
   * Heuristic (in priority order):
   *   1. Input is type=password OR type=text with name/aria-label/placeholder
   *      mentioning "api key", "api_key", "apiKey", "bridge", or "token".
   *   2. Nearest visible ancestor card contains the literal text "Claude Pro/Max"
   *      (case-insensitive). This anchors us to the right endpoint so we don't
   *      fill the Anthropic / OpenAI / xAI dialogs by accident.
   */
  function shouldFillField(input, root) {
    if (!input || input.tagName !== "INPUT") return false;
    const type = (input.type || "").toLowerCase();
    if (type !== "password" && type !== "text") return false;

    const haystack = [
      input.name,
      input.id,
      input.placeholder,
      input.getAttribute("aria-label"),
      input.getAttribute("autocomplete"),
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();

    const looksLikeApiKey =
      type === "password" ||
      /\bapi[_-]?key\b/.test(haystack) ||
      /\bbridge\b/.test(haystack) ||
      /\btoken\b/.test(haystack);

    if (!looksLikeApiKey) return false;

    // Anchor: only fill when the surrounding card mentions Claude Pro/Max.
    const scope = root || document.body;
    if (!scope || !scope.textContent) return false;
    const textHit = /claude\s*pro\s*\/?\s*max/i.test(scope.textContent);
    return textHit;
  }

  /**
   * Set an input's value in a way React's controlled component picks up.
   * Plain `input.value = x` is invisible to React because it caches the
   * value on the fiber; we have to call the native setter then dispatch
   * an `input` event so React schedules a re-render.
   */
  function fillInputAsReact(input, value) {
    const proto = Object.getPrototypeOf(input);
    const desc =
      Object.getOwnPropertyDescriptor(proto, "value") ||
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");
    if (desc && desc.set) {
      desc.set.call(input, value);
    } else {
      input.value = value;
    }
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  /**
   * Walk newly-added nodes, check each <input>, and fill the first match.
   */
  function scan(rootNode) {
    if (filled || !cachedToken) return;
    if (!rootNode || rootNode.nodeType !== Node.ELEMENT_NODE) return;

    // First check the rootNode itself, then its descendants.
    const candidates = [rootNode];
    if (rootNode.querySelectorAll) {
      candidates.push(...rootNode.querySelectorAll("input"));
    }
    for (const node of candidates) {
      if (!node || node.tagName !== "INPUT") continue;
      // Find the nearest dialog/modal/card-like ancestor for the anchor text check.
      const ancestor =
        node.closest(
          "[role='dialog'], dialog, [class*='modal'], [class*='Dialog'], [class*='card'], form",
        ) || document.body;
      if (shouldFillField(node, ancestor)) {
        fillInputAsReact(node, cachedToken);
        filled = true;
        // Stop observing once we've done our one-shot fill.
        if (observer) {
          observer.disconnect();
          observer = null;
        }
        return;
      }
    }
  }

  async function main() {
    try {
      const settingsRaw = await chrome.storage.sync.get(["xbrain_settings_v1"]);
      const settings =
        (settingsRaw && settingsRaw["xbrain_settings_v1"]) || {};
      cachedSetting = settings.autoFillLibreChat !== false; // default ON
      if (!cachedSetting) return;

      const stored = await chrome.storage.local.get(["xbt_token"]);
      cachedToken = stored.xbt_token || null;
      if (!cachedToken) {
        // No token yet — listen for one appearing so we can fill on next dialog.
        chrome.storage.onChanged.addListener((changes, area) => {
          if (area !== "local") return;
          if (changes.xbt_token && changes.xbt_token.newValue) {
            cachedToken = changes.xbt_token.newValue;
            // Re-scan the current DOM in case the dialog is already open.
            scan(document.body);
          }
        });
        // Still attach the observer so we catch dialogs opened later.
      }

      // Initial scan (covers SSR-rendered DOM and dialogs that were open before
      // our script attached).
      scan(document.body);

      // MutationObserver covers the common case: user clicks the endpoint
      // dropdown and the dialog mounts dynamically.
      observer = new MutationObserver((mutations) => {
        if (filled) return;
        for (const m of mutations) {
          for (const node of m.addedNodes || []) {
            scan(node);
            if (filled) return;
          }
        }
      });
      observer.observe(document.body, { childList: true, subtree: true });
    } catch (e) {
      // Never crash the LibreChat page if the extension state is weird.
      console.warn(
        "[xbrain] LibreChat autofill init failed:",
        e && e.message ? e.message : e,
      );
    }
  }

  // Expose pure helpers for the test harness (no side effects on attach).
  if (typeof globalThis !== "undefined") {
    globalThis.xbrainLibreChatAutofill = {
      shouldFillField,
      fillInputAsReact,
    };
  }

  // Only run inside a real Chrome extension context (skip during node tests).
  if (
    typeof chrome !== "undefined" &&
    chrome.storage &&
    chrome.storage.local
  ) {
    main();
  }
})();
