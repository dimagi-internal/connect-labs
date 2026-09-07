/* The left panel: where, level, delivery year, ranking, method, threshold.

   Every handler here does the same thing — describe the change and hand it to
   state.apply(). None of them decides what to redraw. That decision used to be
   duplicated in each handler and was wrong in a different way in most of them. */
(function () {
  'use strict';
  window.Targeting = window.Targeting || {};
  var T = window.Targeting;
  var util = T.util;
  var state = T.state;

  var LEVEL_NAMES = { 0: 'Country', 1: 'Regions', 2: 'Districts' };
  var slideTimer = null;

  // --- where ---------------------------------------------------------------

  function renderCountrySelect() {
    var S = state.get();
    var sel = document.getElementById('tg-country');
    sel.innerHTML = '<option value="">All of Africa</option>';
    ((S.scopeInfo && S.scopeInfo.countries) || []).forEach(function (c) {
      var o = document.createElement('option');
      o.value = c.iso;
      o.textContent =
        c.name + (c.supported ? '' : ' (no data for this method)');
      sel.appendChild(o);
    });
    sel.value = S.iso;
  }

  function renderLevelToggle() {
    var S = state.get();
    var wrap = document.getElementById('tg-level-wrap');
    var box = document.getElementById('tg-level');
    var note = document.getElementById('tg-level-note');
    var depth = S.scopeInfo && S.scopeInfo.depth;

    // A national method describes whole countries and ignores admin_level
    // entirely. The control used to stay visible there: clicking "Regions"
    // wrote admin_level=1 into the URL, changed nothing, and left the note
    // underneath still saying "Unpinned" — a control that looks like it works
    // and does not.
    if (!S.iso || !depth || currentResolution() === 'national') {
      wrap.classList.add('hidden');
      return;
    }
    wrap.classList.remove('hidden');
    box.innerHTML = '';

    Object.keys(depth)
      .filter(function (k) {
        return k !== '0';
      })
      .forEach(function (k) {
        var d = depth[k];
        var b = document.createElement('button');
        b.type = 'button';
        var on = String(S.level) === k;
        b.className =
          'flex-1 text-xs px-2 py-1.5 rounded-md ' +
          (on
            ? 'bg-white shadow-sm font-medium text-stone-900'
            : 'text-stone-600 hover:text-stone-900');
        b.innerHTML =
          (LEVEL_NAMES[k] || 'ADM' + k) +
          '<span class="block text-[10px] ' +
          (d.measured ? 'text-teal-700' : 'text-amber-700') +
          '">' +
          (d.measured
            ? util.fmtFull(d.measured) + ' measured'
            : util.fmtFull(d.units) + ' inherited') +
          '</span>';
        b.onclick = function () {
          state.apply({ level: parseInt(k, 10) });
        };
        box.appendChild(b);
      });

    // The trap, stated where the choice is made rather than in a caveat under
    // the table: a level with no readings of its own is not more detail, it is
    // the same information on a finer grid, and every unit ties with its
    // siblings.
    var pinned = depth[String(S.level)];
    if (pinned && !pinned.measured && pinned.units) {
      note.className = 'text-[11px] text-amber-700 mt-1.5';
      note.textContent =
        'No unit at this level carries a reading of its own — all ' +
        util.fmtFull(pinned.units) +
        ' inherit from a coarser level, so they will rank in flat ties. ' +
        'Ranking is only meaningful at a measured level.';
    } else if (S.level === null) {
      note.className = 'text-[11px] text-stone-500 mt-1.5';
      note.textContent =
        'Unpinned: the deepest level carrying any value is used, which is not ' +
        'always the deepest level measured.';
    } else {
      note.className = 'text-[11px] text-stone-500 mt-1.5';
      note.textContent = '';
    }

    var chip = document.getElementById('tg-depth-chip');
    if (chip) {
      var d1 = depth['1'] || {};
      chip.textContent = d1.units
        ? util.fmtFull(d1.units) + ' regions on file'
        : '';
    }
  }

  /* A pin only means something at a level this country actually has. Carried
     across a country or indicator change it silently produced an empty answer
     — the level buttons still showed a selection and the table showed nothing. */
  function pruneLevel() {
    var S = state.get();
    var depth = S.scopeInfo && S.scopeInfo.depth;
    if (S.level === null) return false;
    if (!S.iso || !depth || !depth[String(S.level)]) {
      // Written directly rather than through apply(): this runs INSIDE the
      // scope step of a refresh that is already going to redraw the map and
      // the selection, and re-entering the pipeline from inside itself would
      // cancel the run that called it.
      S.level = null;
      return true;
    }
    return false;
  }

  function renderYearSelect() {
    var sel = document.getElementById('tg-year');
    if (!sel || sel.options.length) return;
    var now = new Date().getFullYear();
    sel.innerHTML = '<option value="">As measured (no projection)</option>';
    for (var y = now; y <= now + 6; y++) {
      var o = document.createElement('option');
      o.value = String(y);
      o.textContent = String(y);
      sel.appendChild(o);
    }
  }

  // --- method + resolution -------------------------------------------------

  function methodsFor(resolution) {
    var S = state.get();
    return ((S.methodInfo.resolutions || {})[resolution] || []).map(
      function (code) {
        return Object.assign({ code: code }, S.methodInfo.methods[code]);
      },
    );
  }

  function currentResolution() {
    var S = state.get();
    var m = S.methodInfo.methods[S.method];
    return m ? m.resolution : 'subnational';
  }

  function renderResolutionToggle() {
    var S = state.get();
    var el = document.getElementById('tg-resolution');
    el.innerHTML = '';
    Object.keys(S.methodInfo.resolutions).forEach(function (res) {
      if (!S.methodInfo.resolutions[res].length) return;
      var active = res === currentResolution();
      var b = document.createElement('button');
      b.type = 'button';
      b.textContent = res === 'national' ? 'National' : 'Subnational';
      b.className =
        'flex-1 text-sm rounded-md py-1.5 transition ' +
        (active
          ? 'bg-white shadow-sm font-medium text-stone-900'
          : 'text-stone-600 hover:text-stone-900');
      b.onclick = function () {
        // Switching level picks that level's default method rather than trying
        // to carry one across — a national method has no subnational
        // equivalent, and pretending otherwise is how a national number gets
        // painted onto regions.
        var candidates = methodsFor(res);
        if (!candidates.length) return;
        var pick =
          candidates.filter(function (m) {
            return m.default;
          })[0] || candidates[0];
        state.apply({ method: pick.code, level: null });
      };
      el.appendChild(b);
    });
  }

  function renderMethodSelect() {
    var S = state.get();
    var sel = document.getElementById('tg-method');
    sel.innerHTML = '';
    methodsFor(currentResolution()).forEach(function (m) {
      var o = document.createElement('option');
      o.value = m.code;
      o.textContent = m.label;
      o.selected = m.code === S.method;
      sel.appendChild(o);
    });
  }

  function renderMethodNotes() {
    var S = state.get();
    var m = S.methodInfo.methods[S.method];
    if (!m) return;
    document.getElementById('tg-method-desc').textContent = m.description || '';
    document.getElementById('tg-method-caveat').textContent = m.caveat || '';

    var chip = document.getElementById('tg-method-chip');
    if (chip) {
      chip.textContent =
        m.countries_available + '/' + m.countries_total + ' countries';
    }
    var cover = document.getElementById('tg-method-cover');
    if (cover) {
      cover.textContent =
        m.unavailable && m.unavailable.length
          ? 'Cannot answer for ' +
            m.unavailable.length +
            ' countries, including ' +
            m.unavailable
              .slice(0, 4)
              .map(function (c) {
                return c;
              })
              .join(', ') +
            '.'
          : 'Answers every country with data loaded.';
    }
  }

  /* Choose a method that can answer this indicator, PREFERRING the resolution
     the reader is already in.

     It used to take Object.keys()[0] of whatever could answer. Switching from
     ORS coverage to malaria incidence therefore moved a reader looking at
     fifteen Liberian counties onto a national method without a word — and the
     page then reported "0 areas selected in Liberia at ADM1" for an answer
     that was neither zero nor at ADM1. Subnational_surface could have answered
     it for forty-five countries. */
  function bestMethodFor(info, want) {
    var usable = Object.keys(info.methods).filter(function (c) {
      return info.methods[c].countries_available > 0;
    });
    if (!usable.length) return null;
    var sameRes = usable.filter(function (c) {
      return info.methods[c].resolution === want;
    });
    var pool = sameRes.length ? sameRes : usable;
    var preferred = pool.filter(function (c) {
      return info.methods[c].default;
    });
    return preferred[0] || pool[0];
  }

  // --- threshold -----------------------------------------------------------

  function applyThresholdScale() {
    var S = state.get();
    var meta = S.indicatorMeta[S.indicator];
    if (!meta) return;
    var el = document.getElementById('tg-threshold');
    el.min = meta.threshold_min;
    el.max = meta.threshold_max;
    el.step = meta.threshold_max > 100 ? 5 : 1;
    document.getElementById('tg-scale-min').textContent = meta.per_1000
      ? (meta.threshold_min / 10).toFixed(0) + '%'
      : meta.threshold_min + '%';
    document.getElementById('tg-scale-max').textContent = meta.per_1000
      ? (meta.threshold_max / 10).toFixed(0) + '%'
      : meta.threshold_max + '%';
  }

  function renderThresholdLabels() {
    var S = state.get();
    var meta = S.indicatorMeta[S.indicator] || {};
    var label = document.getElementById('tg-threshold-label');
    var unit = document.getElementById('tg-threshold-unit');
    var chip = document.getElementById('tg-family-chip');
    var family = document.getElementById('tg-family');
    var lower = meta.lower_is_worse;

    if (label) {
      // The measure's own label, with its own casing. Lower-casing it turned
      // "ORS treatment coverage" into "ors treatment coverage" — and "people"
      // was wrong for anything not denominated in people, which the unit
      // already states precisely.
      var name = util.esc(meta.label || '');
      label.innerHTML = lower
        ? 'Show me where <b>' + name + '</b> is <b>below</b>'
        : 'Show me where <b>' + name + '</b> is <b>above</b>';
    }
    if (unit) unit.textContent = meta.unit || '';
    if (chip)
      chip.textContent = lower
        ? 'coverage · selects below'
        : 'burden · selects above';
    if (family) {
      family.textContent = lower
        ? 'This is a coverage measure: low is bad, so the threshold selects the ' +
          'places BELOW it and the fundable quantity is the unreached count.'
        : 'This is a burden measure: high is bad, so the threshold selects the ' +
          'places ABOVE it.';
    }
  }

  function updateThresholdLabels() {
    var S = state.get();
    var t = S.threshold;
    var meta = S.indicatorMeta[S.indicator] || {};

    // The headline is the threshold in the indicator's OWN unit. Dividing every
    // threshold by ten assumed per-1,000 and made a 50% sanitation threshold
    // read as 5.0%.
    document.getElementById('tg-threshold-pct').textContent = meta.per_1000
      ? String(t)
      : t + '%';

    // Announce the value in the measure's own unit. "80" alone is what a
    // screen reader would otherwise read for a threshold that means eighty per
    // 1,000 live births here and eighty per cent for most other measures.
    var slider = document.getElementById('tg-threshold');
    var name = meta.label || S.indicator;
    slider.setAttribute('aria-label', name + ' threshold');
    slider.setAttribute(
      'aria-valuetext',
      t +
        (meta.unit ? ' ' + meta.unit : '') +
        ', selects ' +
        (meta.lower_is_worse ? 'below' : 'above'),
    );

    var alt = document.getElementById('tg-threshold-alt');
    if (meta.per_1000) {
      alt.style.display = '';
      document.getElementById('tg-threshold-abs').textContent =
        (t / 10).toFixed(1) + '% of children die before five';
    } else {
      alt.style.display = 'none';
      document.getElementById('tg-threshold-abs').textContent = '';
    }
  }

  function renderPicker() {
    renderResolutionToggle();
    renderMethodSelect();
    renderMethodNotes();
    renderThresholdLabels();
    updateThresholdLabels();
  }

  // --- wiring --------------------------------------------------------------

  function init() {
    document.getElementById('tg-country').onchange = function (e) {
      // Naming a country changes the question from "does this country qualify"
      // to "which parts of it" — so ranking turns on, and a level pinned for
      // the previous country cannot mean anything here.
      state.apply({
        iso: e.target.value,
        level: null,
        rollup: !e.target.value,
      });
      document.getElementById('tg-rank').checked = !!e.target.value;
    };

    document.getElementById('tg-method').onchange = function (e) {
      state.apply({ method: e.target.value, level: null });
    };

    document.getElementById('tg-year').onchange = function (e) {
      state.apply({
        year: e.target.value ? parseInt(e.target.value, 10) : null,
      });
    };

    document.getElementById('tg-rank').onchange = function (e) {
      state.apply({ rollup: !e.target.checked });
    };

    var slider = document.getElementById('tg-threshold');
    slider.addEventListener('input', function () {
      // The label follows the handle immediately; the query waits, because a
      // drag across the scale would otherwise be forty selections.
      var S = state.get();
      S.threshold = parseFloat(slider.value);
      updateThresholdLabels();
      T.main.syncLinks();
      clearTimeout(slideTimer);
      slideTimer = setTimeout(function () {
        state.apply(
          { threshold: parseFloat(slider.value) },
          { channel: 'selection' },
        );
      }, 250);
    });
  }

  window.Targeting.controls = {
    init: init,
    renderCountrySelect: renderCountrySelect,
    renderLevelToggle: renderLevelToggle,
    renderYearSelect: renderYearSelect,
    renderPicker: renderPicker,
    applyThresholdScale: applyThresholdScale,
    updateThresholdLabels: updateThresholdLabels,
    currentResolution: currentResolution,
    bestMethodFor: bestMethodFor,
    pruneLevel: pruneLevel,
  };
})();
