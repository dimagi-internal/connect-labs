/**
 * Labs analytics shim — self-hosted Umami.
 *
 * Exposes window.labsTrack(eventName, eventData) everywhere. Events queue
 * until the Umami tracker loads; when analytics is unconfigured (empty
 * websiteId/hostUrl) labsTrack stays a silent no-op queue, so call sites
 * never need to guard.
 *
 * PHI rule: event names and data must carry opaque identifiers only —
 * never names, form answers, or free text.
 */
(function () {
  'use strict';

  var queue = [];

  window.labsTrack = function (eventName, eventData) {
    if (window.umami && typeof window.umami.track === 'function') {
      window.umami.track(eventName, eventData || {});
    } else {
      queue.push([eventName, eventData || {}]);
    }
  };

  var el = document.getElementById('labs-analytics-data');
  if (!el) return;

  var cfg;
  try {
    cfg = JSON.parse(el.textContent);
  } catch (e) {
    return;
  }
  if (!cfg || !cfg.websiteId || !cfg.hostUrl) return;

  var script = document.createElement('script');
  script.defer = true;
  script.src = cfg.hostUrl.replace(/\/+$/, '') + '/script.js';
  script.setAttribute('data-website-id', cfg.websiteId);
  // HIPAA-bar: labs query strings carry expressive workflow params (usernames,
  // entity ids, scope filters). Strip them client-side so analytics only ever
  // receives paths — same identifiers-not-content standard as the audit trail.
  script.setAttribute('data-exclude-search', 'true');
  script.setAttribute('data-exclude-hash', 'true');
  script.onload = function () {
    if (!window.umami) return;
    if (cfg.username && typeof window.umami.identify === 'function') {
      window.umami.identify({
        username: cfg.username,
        is_dimagi: !!cfg.isDimagi,
      });
    }
    while (queue.length) {
      var item = queue.shift();
      window.umami.track(item[0], item[1]);
    }
  };
  document.head.appendChild(script);
})();
