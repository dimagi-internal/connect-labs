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

  // What Umami stores is a ROUTE TEMPLATE, never the raw URL. Labs paths
  // and query strings carry frontline-worker identifiers (`?username=`,
  // `/users/<username>/`, `/kyc/<worker_id>/`) and record/opportunity ids,
  // and an identifier is not ours to keep in an analytics store. So:
  //   - the path becomes the Django URL pattern the server matched
  //     (`cfg.route`, e.g. `/labs/workflow/{workflow_id}/`) when it is the
  //     page the server rendered, else a heuristic template (ids -> `{id}`);
  //   - the query string is dropped, all of it;
  //   - the referrer is templated the same way (another site: origin only);
  //   - the page title is dropped (titles can name a worker or a record).

  // A segment right after one of these names an entity (a worker, a user,
  // a token) whatever it looks like, e.g. `/campaign/api/users/jdoe/role/`.
  var ID_PREFIXES = [
    'user',
    'users',
    'username',
    'worker',
    'workers',
    'flw',
    'flws',
    'kyc',
    'payments',
    'invite',
    'invites',
    'accept_invite',
    'token',
    'tokens',
    'coach-image',
    'image',
    'u',
    'r',
    't',
    'v',
    'c',
    'p',
    'o',
    'opp',
    'org',
    'rounds',
    'past',
    'portfolios',
    'catalogue',
    'reports',
    'job',
    'jobs',
    'clone',
    'status',
    'microplans',
    'activities',
    'settings',
    'application',
    'usage',
  ];

  function looksLikeId(segment) {
    var raw = segment;
    try {
      raw = decodeURIComponent(segment);
    } catch (e) {
      return true; // undecodable: treat as an identifier
    }
    if (/^\d+$/.test(raw)) return true; // numeric id
    if (
      /^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$/i.test(
        raw,
      )
    )
      return true; // uuid
    if (/^[0-9a-f]{8,}$/i.test(raw) && /\d/.test(raw)) return true; // hex token
    if (raw.length >= 20) return true; // long token / slug-with-id
    if (/\d/.test(raw) && raw.length >= 6) return true; // e.g. flw1234, +2547...
    if (/[^A-Za-z0-9._-]/.test(raw)) return true; // @, +, spaces: emails, phones, names
    return false;
  }

  function templatePath(pathname) {
    var segments = (pathname || '/').split('/');
    var out = [];
    for (var i = 0; i < segments.length; i++) {
      var seg = segments[i];
      if (seg === '') {
        out.push(seg);
        continue;
      }
      var prev = i > 0 ? segments[i - 1].toLowerCase() : '';
      if (ID_PREFIXES.indexOf(prev) !== -1 || looksLikeId(seg)) {
        out.push('{id}');
      } else {
        out.push(seg);
      }
    }
    return out.join('/') || '/';
  }

  var cfg = null;
  var el = document.getElementById('labs-analytics-data');
  if (el) {
    try {
      cfg = JSON.parse(el.textContent);
    } catch (e) {
      cfg = null;
    }
  }
  // The server's template describes the page it rendered — and only that
  // page, so a later client-side navigation falls back to the heuristic.
  var renderedPath = window.location.pathname;
  var serverRoute =
    cfg && typeof cfg.route === 'string' && cfg.route.charAt(0) === '/'
      ? cfg.route
      : null;

  function templateUrl(value, isReferrer) {
    if (!value) return value;
    var parsed;
    try {
      parsed = new URL(value, window.location.origin);
    } catch (e) {
      return ''; // unparseable: store nothing rather than risk an identifier
    }
    var absolute = /^[a-z][a-z0-9+.-]*:/i.test(value);
    if (parsed.origin !== window.location.origin) {
      // Another site's URL is not ours to template; keep where it came from.
      return isReferrer
        ? parsed.origin + '/'
        : parsed.origin + templatePath(parsed.pathname);
    }
    var path =
      !isReferrer && serverRoute && parsed.pathname === renderedPath
        ? serverRoute
        : templatePath(parsed.pathname);
    return absolute ? parsed.origin + path : path;
  }

  window.labsAnalyticsBeforeSend = function (type, payload) {
    if (payload) {
      if (payload.url) payload.url = templateUrl(payload.url, false);
      if (payload.referrer)
        payload.referrer = templateUrl(payload.referrer, true);
      if ('title' in payload) delete payload.title;
    }
    return payload;
  };
  window.labsAnalyticsTemplatePath = templatePath;

  if (!cfg || !cfg.websiteId || !cfg.hostUrl) return;

  var script = document.createElement('script');
  script.defer = true;
  script.src = cfg.hostUrl.replace(/\/+$/, '') + '/script.js';
  script.setAttribute('data-website-id', cfg.websiteId);
  // Every pageview and event goes through the route templating above:
  // no query string, no identifier path segments, no page title.
  script.setAttribute('data-before-send', 'labsAnalyticsBeforeSend');
  script.setAttribute('data-exclude-search', 'true');
  script.setAttribute('data-exclude-hash', 'true');
  // Web Vitals (LCP/INP/CLS/FCP/TTFB) feed Umami's Performance view —
  // timing numbers only, no page content. (Attribute name verified against
  // the Umami tracker source: `config('performance')`.)
  script.setAttribute('data-performance', 'true');
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
