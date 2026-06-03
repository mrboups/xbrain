/**
 * xbrain-login.js — GrooveOS login-page rebrand.
 *
 * On the auth pages only (/login, /register, /), it:
 *   - sets the heading to "GrooveOS" and adds a "Join Team or Sign Up" subtitle,
 *   - hides the email/password login form (keeps social / GitHub + Google),
 *   - removes the "—— OR ——" divider,
 *   - hides the "Don't have an account? Sign up" prompt,
 *   - hides the LibreChat logo image.
 *
 * Done client-side so it is CDN-cache-proof (the hashed locale bundle that holds
 * "Welcome back" is cached by Cloudflare; overriding the DOM here wins).
 * SPA-safe: re-applies on every DOM mutation and cleans up off the auth pages.
 */
(function () {
  var STYLE_ID = 'xbrain-login-style';
  var SUB_ID = 'xbrain-login-subtitle';
  var TITLE = 'GrooveOS';
  var SUBTITLE = 'Join Team or Sign Up';
  var SUBTITLE_COLOR = '#10b981'; // GrooveOS green — readable on the dark bg
  var CSS =
    'form:has(input[name="email"]){display:none!important}' +
    'img[src*="logo.svg"]{display:none!important}';

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

    var all = document.querySelectorAll('h1, h2, h3, a, p, span, div, section');
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      var txt = (el.textContent || '').trim();
      var leaf = el.children.length === 0;

      if (leaf && (txt === 'Welcome back' || txt === 'Login Groove OS' || txt === 'Groove OS')) {
        // Rename the heading + add a subtitle right after it (once).
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
      } else if (txt === 'OR') {
        // The "—— OR ——" divider: the OUTERMOST element whose full text is
        // exactly "OR" (its other children are the decorative lines, which carry
        // no text) is the divider row. No leaf requirement — works whether "OR"
        // is a span, a bare text node, or the lines are pseudo-elements.
        var parentTxt = el.parentElement ? (el.parentElement.textContent || '').trim() : '';
        if (parentTxt !== 'OR') el.style.display = 'none';
      } else if (/^Don't have an account\?/i.test(txt) && el.querySelector('a')) {
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
      });
    } catch (e) {}
  }

  if (document.body) start();
  else document.addEventListener('DOMContentLoaded', start);
})();
