/**
 * xbrain-login.js - GrooveOS login-page rebrand.
 *
 * On the auth pages only (/login, /register, /), it:
 *   - sets the heading to "GrooveOS" and adds a "Join Team or Sign Up" subtitle,
 *   - hides the email/password login form (keeps social / GitHub + Google),
 *   - removes the "OR" divider,
 *   - hides the "Don't have an account? Sign up" prompt,
 *   - hides the LibreChat logo image.
 *
 * Client-side so it is CDN-cache-proof. SPA-safe via MutationObserver.
 */
(function () {
  var STYLE_ID = 'xbrain-login-style';
  var SUB_ID = 'xbrain-login-subtitle';
  var TITLE = 'GrooveOS';
  var SUBTITLE = 'Join Team or Sign Up';
  var SUBTITLE_COLOR = '#10b981';
  var CSS =
    'form:has(input[name="email"]){display:none!important}' +
    'img[src*="logo.svg"]{display:none!important}';

  // Keep letters only. The "OR" divider is padded with non-breaking spaces /
  // decorative dashes that String.trim() does not remove, so a plain === 'OR'
  // check fails. Stripping everything but A-Z is ASCII-safe and bulletproof.
  function lettersOnly(s) {
    return (s || '').replace(/[^A-Za-z]/g, '');
  }

  function onAuthPage() {
    var p = location.pathname;
    return p === '/login' || p === '/register' || p === '/';
  }

  function cleanup() {
    var st = document.getElementById(STYLE_ID);
    if (st) st.remove();
    var sub = document.getElementById(SUB_ID);
    if (sub) sub.remove();
  }

  function apply() {
    if (!onAuthPage()) {
      cleanup();
      return;
    }
    if (!document.getElementById(STYLE_ID) && document.head) {
      var s = document.createElement('style');
      s.id = STYLE_ID;
      s.textContent = CSS;
      document.head.appendChild(s);
    }

    var all = document.querySelectorAll('*');
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      if (el.id === SUB_ID || el.id === STYLE_ID) continue;
      var raw = (el.textContent || '').trim();
      var leaf = el.children.length === 0;

      // Heading + subtitle
      if (leaf && (raw === 'Welcome back' || raw === 'Login Groove OS' || raw === 'Groove OS')) {
        if (el.textContent !== TITLE) el.textContent = TITLE;
        if (!document.getElementById(SUB_ID)) {
          var sub = document.createElement('div');
          sub.id = SUB_ID;
          sub.textContent = SUBTITLE;
          sub.style.cssText =
            'text-align:center;margin-top:6px;font-size:15px;font-weight:600;color:' +
            SUBTITLE_COLOR + ';';
          el.insertAdjacentElement('afterend', sub);
        }
        continue;
      }

      // "OR" divider: an element whose visible text reduces to exactly "OR".
      // Guard on a short raw length so a long string never matches. Climb to the
      // outermost "OR-only" element (the divider row, decorative lines included).
      if (raw.length <= 8 && lettersOnly(raw).toUpperCase() === 'OR') {
        var n = el;
        while (
          n.parentElement &&
          lettersOnly(n.parentElement.textContent).toUpperCase() === 'OR'
        ) {
          n = n.parentElement;
        }
        n.style.display = 'none';
        continue;
      }

      // "Don't have an account? Sign up"
      if (/^Don't have an account\?/i.test(raw) && el.querySelector && el.querySelector('a')) {
        el.style.display = 'none';
      }
    }
  }

  function start() {
    apply();
    try {
      new MutationObserver(apply).observe(document.documentElement, {
        childList: true,
        subtree: true,
        characterData: true,
      });
    } catch (e) {}
  }

  if (document.body) start();
  else document.addEventListener('DOMContentLoaded', start);
})();
