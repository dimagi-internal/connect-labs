/* Boot, and the five channel handlers the state pipeline calls.

   Nothing else in the app decides what to refresh. A control describes what
   changed; state.apply() works out which of these run, in this order, under a
   ticket that stops a superseded run from painting over a newer one. */
(function () {
  'use strict';
  window.Targeting = window.Targeting || {};
  var T = window.Targeting;
  var state = T.state;
  var api = T.api;

  // --- the address bar is the state ---------------------------------------

  var URL_KEYS = {
    indicator: 'indicator',
    method: 'method',
    iso: 'iso',
    admin_level: 'level',
    target_year: 'year',
  };

  function readUrl() {
    var q = new URLSearchParams(window.location.search);
    var wanted = {};
    Object.keys(URL_KEYS).forEach(function (k) {
      if (q.has(k)) wanted[URL_KEYS[k]] = q.get(k);
    });
    if (q.get('rollup') === '0') wanted.rollup = false;
    if (q.has('threshold')) wanted.threshold = parseFloat(q.get('threshold'));
    return wanted;
  }

  function syncLinks() {
    var href = api.downloadHref();
    var a = document.getElementById('tg-download');
    var b = document.getElementById('tg-download-md');
    if (a) a.href = href;
    if (b) b.href = href;
    if (window.history && window.history.replaceState) {
      window.history.replaceState(
        null,
        '',
        window.location.pathname + api.withThreshold(),
      );
    }
  }

  // --- channels ------------------------------------------------------------

  state.register('methods', function (S) {
    return api.methods(S.indicator).then(function (info) {
      S.methodInfo = info;
      T.menu.index();
      // Prefer a method at the resolution the reader is already in.
      var want = info.methods[S.method]
        ? info.methods[S.method].resolution
        : 'subnational';
      if (
        !info.methods[S.method] ||
        !info.methods[S.method].countries_available
      ) {
        var pick = T.controls.bestMethodFor(info, want);
        if (pick) S.method = pick;
      }
      T.menu.renderTrigger();
      T.controls.applyThresholdScale();

      // Reset the threshold here rather than in a second apply(). Doing it
      // afterwards meant every indicator change ran the map, selection,
      // methodology and costing requests twice — once on the old threshold and
      // again on the new one — and briefly painted the answer to a question
      // nobody asked.
      if (S.thresholdFor !== S.indicator) {
        var meta = S.indicatorMeta[S.indicator] || {};
        if (meta.threshold_default !== undefined) {
          S.threshold = meta.threshold_default;
          document.getElementById('tg-threshold').value = S.threshold;
        }
        S.thresholdFor = S.indicator;
      }
      T.controls.renderPicker();

      // Which bases a costing can use is a property of the INDICATOR — a
      // per-case basis needs that indicator's case count. Fetched once at boot
      // and never again, the panel kept under-5 mortality's answer: looking at
      // ORS coverage, "per case (a year of cases)" was greyed out as "not
      // available for this indicator" when it is exactly the basis that
      // indicator is for.
      return api.interventions(S.indicator).then(function (info) {
        S.costInfo = info;
        var basis = info.bases.filter(function (b) {
          return b.code === S.basis;
        })[0];
        if (!basis || !basis.available_for_indicator) {
          var usable = info.bases.filter(function (b) {
            return b.available_for_indicator;
          });
          if (usable.length) S.basis = usable[usable.length - 1].code;
          S.preset = null;
        }
        T.costing.renderControls();
      });
    });
  });

  state.register('scope', function (S) {
    return api.scope().then(function (info) {
      S.scopeInfo = info;
      T.controls.pruneLevel();
      T.controls.renderCountrySelect();
      T.controls.renderLevelToggle();
      T.controls.renderPicker();
    });
  });

  state.register('map', function (S) {
    T.map.clear();
    syncLinks();
    if (!T.map.isReady()) return null;
    return api.map().then(function (data) {
      T.map.paint(data);
    });
  });

  state.register('selection', function (S) {
    syncLinks();
    document.getElementById('tg-births').textContent = '…';
    var mine = state.ticket();
    return api
      .selection()
      .then(function (data) {
        if (!state.isCurrent(mine)) return;
        T.table.render(data);
        T.map.applySelection(data.selected_pks);
        T.methodology.refresh();
      })
      .catch(function (err) {
        if (!state.isCurrent(mine)) return;
        document.getElementById('tg-births').textContent = 'error';
        document.getElementById('tg-rows').innerHTML =
          '<tr><td colspan="10" class="px-5 py-8 text-center text-red-600">' +
          'Could not load the selection: ' +
          T.util.esc(String(err)) +
          '</td></tr>';
      });
  });

  state.register('costing', function () {
    return T.costing.refresh();
  });

  // --- the one way the indicator changes -----------------------------------

  function selectIndicator(code) {
    var S = state.get();
    if (!code || code === S.indicator) return Promise.resolve();
    // One pass. The threshold follows inside the methods channel, which is
    // the first point at which the new indicator's own scale is known.
    return state.apply({ indicator: code, level: null });
  }

  // --- boot ----------------------------------------------------------------

  document.addEventListener('DOMContentLoaded', function () {
    var wanted = readUrl();
    var S = state.get();

    S.indicator = wanted.indicator || window.TG.indicator;
    S.method = wanted.method || window.TG.defaultMethod;
    S.iso = wanted.iso || '';
    S.level = wanted.level !== undefined ? parseInt(wanted.level, 10) : null;
    S.year = wanted.year ? parseInt(wanted.year, 10) : null;
    S.rollup = wanted.rollup !== false;

    T.popovers.init();
    T.menu.init();
    T.controls.init();
    T.costing.init();
    T.controls.renderYearSelect();
    if (S.year) document.getElementById('tg-year').value = String(S.year);
    if (!S.rollup) document.getElementById('tg-rank').checked = true;

    api
      .methods(S.indicator)
      .then(function (info) {
        S.methodInfo = info;
        T.menu.index();
        var meta = S.indicatorMeta[S.indicator] || {};
        S.threshold =
          wanted.threshold !== undefined && !isNaN(wanted.threshold)
            ? Math.min(
                meta.threshold_max !== undefined
                  ? meta.threshold_max
                  : wanted.threshold,
                Math.max(
                  meta.threshold_min !== undefined
                    ? meta.threshold_min
                    : wanted.threshold,
                  wanted.threshold,
                ),
              )
            : meta.threshold_default !== undefined
            ? meta.threshold_default
            : window.TG.defaultThreshold;
        S.thresholdFor = S.indicator;
        T.controls.applyThresholdScale();
        document.getElementById('tg-threshold').value = S.threshold;
        T.menu.renderTrigger();
        T.controls.renderPicker();
        return T.map.init();
      })
      .then(function () {
        return api.interventions(S.indicator);
      })
      .then(function (info) {
        S.costInfo = info;
        var preset = info.interventions[0];
        S.basis = preset ? preset.basis : 'person';
        S.preset = preset ? preset.slug : null;
        S.unitCost = preset ? preset.unit_cost_usd : 1;
        document.getElementById('tg-unitcost').value = S.unitCost;
        T.costing.renderControls();
        // One full pass from the widest channel, so the first paint goes
        // through exactly the same path as every later change.
        return state.apply({}, { channel: 'scope' });
      })
      .catch(function (err) {
        console.error('targeting: load failed', err);
      });
  });

  window.Targeting.main = {
    selectIndicator: selectIndicator,
    syncLinks: syncLinks,
  };
})();
