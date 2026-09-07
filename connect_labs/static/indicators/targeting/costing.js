/* What a unit price buys over the current selection. */
(function () {
  'use strict';
  window.Targeting = window.Targeting || {};
  var T = window.Targeting;
  var util = T.util;
  var state = T.state;
  var api = T.api;

  function renderControls() {
    var S = state.get();
    var presetSel = document.getElementById('tg-preset');
    var basisSel = document.getElementById('tg-basis');
    if (!S.costInfo) return;

    presetSel.innerHTML = '<option value="">— choose your own —</option>';
    S.costInfo.interventions.forEach(function (i) {
      var o = document.createElement('option');
      o.value = i.slug;
      o.textContent = i.label + ' ($' + i.unit_cost_usd.toFixed(2) + ')';
      o.selected = i.slug === S.preset;
      presetSel.appendChild(o);
    });

    basisSel.innerHTML = '';
    S.costInfo.bases.forEach(function (b) {
      var o = document.createElement('option');
      o.value = b.code;
      o.textContent =
        b.label +
        (b.available_for_indicator
          ? ''
          : ' — not available for this indicator');
      o.disabled = !b.available_for_indicator;
      o.selected = b.code === S.basis;
      basisSel.appendChild(o);
    });
  }

  function render(d) {
    var el = document.getElementById('tg-absorb');
    var detail = document.getElementById('tg-absorb-detail');
    var caveat = document.getElementById('tg-absorb-caveat');
    if (d.error) {
      el.textContent = '—';
      detail.textContent = d.error;
      caveat.textContent = '';
      return;
    }
    el.textContent =
      d.absorbable_usd === null ? '—' : '$' + util.fmt(d.absorbable_usd);
    detail.textContent =
      util.fmtFull(d.units) +
      ' ' +
      d.basis.noun_plural +
      ' — ' +
      d.basis.measure_label;
    var notes = [];
    if (!d.complete) {
      notes.push(
        'A floor: ' +
          (d.unit_coverage.of - d.unit_coverage.with_value) +
          ' of ' +
          d.unit_coverage.of +
          ' regions have no count.',
      );
    }
    if (d.intervention && d.intervention.caveat)
      notes.push(d.intervention.caveat);
    caveat.textContent = notes.join(' ');
  }

  function refresh() {
    var S = state.get();
    if (!S.basis || S.unitCost === null || isNaN(S.unitCost))
      return Promise.resolve();
    var mine = state.ticket('costing');
    return api
      .scenario()
      .then(function (d) {
        if (!state.isCurrent('costing', mine)) return;
        render(d);
      })
      .catch(function () {
        if (!state.isCurrent('costing', mine)) return;
        document.getElementById('tg-absorb-detail').textContent =
          'The costing could not be loaded.';
      });
  }

  function init() {
    var presetSel = document.getElementById('tg-preset');
    var basisSel = document.getElementById('tg-basis');
    var costEl = document.getElementById('tg-unitcost');

    presetSel.onchange = function () {
      var S = state.get();
      var pick = S.costInfo.interventions.filter(function (i) {
        return i.slug === presetSel.value;
      })[0];
      if (!pick) {
        state.apply({ preset: null });
        return;
      }
      costEl.value = pick.unit_cost_usd;
      basisSel.value = pick.basis;
      // A preset carries the indicator it is meant for. Without this, picking
      // "ORS" while targeting mortality silently costs expected deaths rather
      // than untreated diarrhoea — the right arithmetic on the wrong quantity.
      if (pick.targets && pick.targets !== S.indicator) {
        state.apply({
          preset: pick.slug,
          basis: pick.basis,
          unitCost: pick.unit_cost_usd,
        });
        return T.main.selectIndicator(pick.targets);
      }
      return state.apply({
        preset: pick.slug,
        basis: pick.basis,
        unitCost: pick.unit_cost_usd,
      });
    };

    basisSel.onchange = function () {
      presetSel.value = '';
      state.apply({ basis: basisSel.value, preset: null });
    };

    costEl.oninput = function () {
      clearTimeout(window.__costT);
      window.__costT = setTimeout(function () {
        var v = parseFloat(costEl.value);
        // A blank or unparseable box used to leave the previous total sitting
        // beside it — the field empty, "$1,025,768.8B" still on screen, and
        // nothing saying the two no longer belonged together.
        if (isNaN(v)) {
          document.getElementById('tg-absorb').textContent = '—';
          document.getElementById('tg-absorb-detail').textContent =
            'Enter a unit cost.';
          document.getElementById('tg-absorb-caveat').textContent = '';
          return;
        }
        // Negative money is not a price. The input carries min="0", which the
        // browser does not enforce on typed text, so -5 produced an
        // "absorbable spend" of minus five million.
        if (v < 0) {
          v = 0;
          costEl.value = '0';
        }
        state.apply({ unitCost: v });
      }, 350);
    };
  }

  window.Targeting.costing = {
    init: init,
    renderControls: renderControls,
    refresh: refresh,
  };
})();
