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

  // Mirror of audit_trail.service.FREE_TEXT_PARAMS: parameters whose values
  // are typed free text (can contain PHI content). Identifier params
  // (username, entity_id, status, scope ids...) pass through — they are the
  // meaning of labs URLs and aggregate as distinct pages.
  var FREE_TEXT_PARAMS = [
    'q',
    'query',
    'search',
    'term',
    'notes',
    'note',
    'text',
    'message',
    'comment',
    'title',
  ];

  function redactUrl(value) {
    if (!value || value.indexOf('?') === -1) return value;
    try {
      var parts = value.split('?');
      var params = new URLSearchParams(parts.slice(1).join('?'));
      FREE_TEXT_PARAMS.forEach(function (key) {
        if (params.has(key)) params.set(key, '[redacted]');
      });
      var qs = params.toString();
      return qs ? parts[0] + '?' + qs : parts[0];
    } catch (e) {
      return value.split('?')[0]; // parse failure: drop params rather than risk content
    }
  }

  window.labsAnalyticsBeforeSend = function (type, payload) {
    if (payload) {
      if (payload.url) payload.url = redactUrl(payload.url);
      if (payload.referrer) payload.referrer = redactUrl(payload.referrer);
    }
    return payload;
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
  // HIPAA-bar: query params flow through the beforeSend redaction above
  // (identifier params kept — they carry the page's meaning; typed free-text
  // values replaced). Same identifiers-not-content standard as the audit trail.
  script.setAttribute('data-before-send', 'labsAnalyticsBeforeSend');
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
