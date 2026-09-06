/* Formatting and escaping. No state, no DOM lookups by id — the only file
   here that can be reasoned about without knowing anything else. */
(function () {
  'use strict';
  window.Targeting = window.Targeting || {};

  function fmt(n) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    if (n >= 1e9) return (n / 1e9).toFixed(1) + 'B';
    if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
    if (n >= 1e3) return Math.round(n / 1e3) + 'k';
    return String(Math.round(n));
  }

  function fmtFull(n) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    return Math.round(n).toLocaleString('en-US');
  }

  // Row values are author-supplied strings from external sources and land in
  // innerHTML. They are escaped at the boundary rather than trusted.
  function esc(v) {
    if (v === null || v === undefined) return '';
    return String(v).replace(/[&<>"']/g, function (c) {
      return {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
      }[c];
    });
  }

  function hostOf(url) {
    try {
      return new URL(url).hostname.replace(/^www\./, '');
    } catch (e) {
      return 'source';
    }
  }

  // "1 rows" is the kind of thing a reader notices and quietly distrusts the
  // rest of the page for.
  function plural(n, one, many) {
    return n === 1 ? one : many || one + 's';
  }

  window.Targeting.util = {
    fmt: fmt,
    fmtFull: fmtFull,
    esc: esc,
    hostOf: hostOf,
    plural: plural,
  };
})();
