/* Every request the page makes, and the one place the query string is built.

   Four hand-assembled parameter lists is how the map came to show one thing
   while the table showed another, and how the download link came to carry a
   different year from the page it was on. There is one builder, and the
   address bar is written from the same builder, so the link you copy is the
   question you are looking at. */
(function () {
  'use strict';
  window.Targeting = window.Targeting || {};
  var state = window.Targeting.state;

  function query(extra) {
    var S = state.get();
    var p = [];
    p.push('indicator=' + encodeURIComponent(S.indicator));
    if (S.method) p.push('method=' + encodeURIComponent(S.method));
    if (S.iso) p.push('iso=' + encodeURIComponent(S.iso));
    if (S.level !== null) p.push('admin_level=' + S.level);
    if (S.year) p.push('target_year=' + S.year);
    if (!S.rollup) p.push('rollup=0');
    (extra || []).forEach(function (kv) {
      p.push(kv);
    });
    return '?' + p.join('&');
  }

  function withThreshold(extra) {
    var S = state.get();
    return query(['threshold=' + S.threshold].concat(extra || []));
  }

  function getJSON(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error(r.status + ' from ' + url.split('?')[0]);
      return r.json();
    });
  }

  var urls = window.TG.urls;

  window.Targeting.api = {
    query: query,
    withThreshold: withThreshold,
    getJSON: getJSON,
    methods: function (indicator) {
      return getJSON(
        urls.methods + '?indicator=' + encodeURIComponent(indicator),
      );
    },
    scope: function () {
      var S = state.get();
      return getJSON(
        urls.scope +
          '?indicator=' +
          encodeURIComponent(S.indicator) +
          (S.method ? '&method=' + encodeURIComponent(S.method) : '') +
          (S.iso ? '&iso=' + encodeURIComponent(S.iso) : ''),
      );
    },
    map: function () {
      return getJSON(urls.map + query());
    },
    selection: function () {
      return getJSON(urls.selection + withThreshold());
    },
    methodology: function (resolution) {
      return getJSON(
        urls.methodology +
          withThreshold(['resolution=' + encodeURIComponent(resolution)]),
      );
    },
    scenario: function () {
      var S = state.get();
      return getJSON(
        urls.scenario +
          withThreshold(
            [
              'basis=' + encodeURIComponent(S.basis),
              'unit_cost=' + S.unitCost,
            ].concat(
              S.preset ? ['intervention=' + encodeURIComponent(S.preset)] : [],
            ),
          ),
      );
    },
    interventions: function (indicator) {
      return getJSON(
        urls.interventions + '?indicator=' + encodeURIComponent(indicator),
      );
    },
    downloadHref: function () {
      return urls.download + withThreshold();
    },
  };
})();
