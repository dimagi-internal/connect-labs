/* PMC schedule explorer. One plain script, no modules (see targeting.html on why).

   Two answers, in the order a programme is designed:
     How   -- which delivery schedule is the best value (IDM's EMOD comparison);
     Where -- the states considered, ranked by cost per case averted under it.
   The states considered come from the URL (`states=`), which is how a targeting
   selection arrives -- usually via the agent panel; with none, all states.

   State lives in the URL (schedule, states, prices), so a link reopens the same
   view and the agent panel is told what the visitor sees. Every number comes
   from /labs/targeting/api/pmc/ -- this file only draws them. */
(function () {
  'use strict';

  var cfg = window.PMC;
  var el = function (id) {
    return document.getElementById(id);
  };
  var data = null;
  var inflight = null;
  var state = readUrl();

  function readUrl() {
    var q = new URLSearchParams(window.location.search);
    var num = function (k, d) {
      var v = parseFloat(q.get(k));
      return isNaN(v) ? d : v;
    };
    return {
      schedule: q.get('schedule') || '',
      states: (q.get('states') || '').split(',').filter(Boolean),
      cost_per_visit: num('cost_per_visit', cfg.defaults.cost_per_visit),
      platform_fee: num('platform_fee', cfg.defaults.platform_fee),
      dose_rate: num('dose_rate', cfg.defaults.dose_rate),
    };
  }

  function params() {
    var p = new URLSearchParams();
    if (state.schedule) p.set('schedule', state.schedule);
    if (state.states.length) p.set('states', state.states.join(','));
    p.set('cost_per_visit', state.cost_per_visit);
    p.set('platform_fee', state.platform_fee);
    p.set('dose_rate', state.dose_rate);
    return p;
  }

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
  function num(n, digits) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    return Number(n).toLocaleString('en-US', {
      maximumFractionDigits: digits || 0,
      minimumFractionDigits: digits || 0,
    });
  }
  function usd(n, digits) {
    return n === null || n === undefined ? '—' : '$' + num(n, digits);
  }
  // Large sums: $1.33M, $808k.
  function usdShort(n) {
    if (n === null || n === undefined) return '—';
    if (n >= 1e6) return '$' + (n / 1e6).toFixed(2) + 'M';
    if (n >= 1e3) return '$' + Math.round(n / 1e3) + 'k';
    return usd(n);
  }
  // Two significant figures, as the server rounds projections: an estimate
  // summed from estimates should not read as a census count.
  function approx(n, figures) {
    return n ? Number(Number(n).toPrecision(figures || 2)) : 0;
  }
  function pct(n) {
    return n === null || n === undefined ? '—' : num(n, 1) + '%';
  }

  /* ---- the agent panel ------------------------------------------------- */

  var baseFilters = null;
  function shareWithAgent() {
    var host = window.canopyHost;
    if (!host || typeof host.updatePageState !== 'function') return;
    if (baseFilters === null)
      baseFilters = Object.assign({}, (host.pageState() || {}).filters || {});
    host.updatePageState({
      filters: Object.assign({}, baseFilters, {
        schedule: state.schedule,
        states: state.states.join(','),
        cost_per_visit: String(state.cost_per_visit),
        platform_fee: String(state.platform_fee),
        dose_rate: String(state.dose_rate),
      }),
    });
  }
  document.addEventListener('canopy:ready', shareWithAgent);

  /* ---- fetch + render -------------------------------------------------- */

  function syncUrl() {
    history.replaceState(
      null,
      '',
      window.location.pathname + '?' + params().toString(),
    );
  }

  function load() {
    if (inflight) inflight.abort();
    var ctrl = new AbortController();
    inflight = ctrl;
    syncUrl();
    fetch(cfg.url + '?' + params().toString(), {
      signal: ctrl.signal,
      credentials: 'same-origin',
    })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (d) {
        data = d;
        state.schedule = d.schedule;
        syncUrl();
        render();
        shareWithAgent();
      })
      .catch(function (err) {
        if (err && err.name === 'AbortError') return;
        el('pmc-states').innerHTML =
          '<tr><td class="l" colspan="8">Could not load: ' +
          esc(err.message) +
          '</td></tr>';
      });
  }

  function render() {
    el('pmc-per-dose').textContent = usd(data.costs.cost_per_dose, 2);
    renderHow();
    renderWhere();
  }

  function chosen() {
    return data.schedules.filter(function (s) {
      return s.code === data.schedule;
    })[0];
  }

  function renderHow() {
    var costed = data.schedules
      .filter(function (s) {
        return s.cost_per_case_averted !== null;
      })
      .sort(function (a, b) {
        return a.cost_per_case_averted - b.cost_per_case_averted;
      });
    var best = costed[0];
    var next = costed[1];
    el('pmc-how').innerHTML = best
      ? 'Best value: <b>' +
        esc(best.label) +
        '</b>, averting ' +
        pct(best.averted_pct) +
        ' of cases at ' +
        usd(best.cost_per_case_averted, 0) +
        ' each' +
        (data.reference_incidence
          ? ' (at Nigeria\u2019s malaria incidence, ' +
            num(data.reference_incidence.value) +
            ' per 1,000)'
          : '') +
        (next
          ? '. Next: ' +
            esc(next.label) +
            ', ' +
            pct(next.averted_pct) +
            ' at ' +
            usd(next.cost_per_case_averted, 0) +
            '.'
          : '.')
      : '';

    var max = 1;
    data.schedules.forEach(function (s) {
      max = Math.max(max, s.averted_pct + s.averted_ci);
    });
    el('pmc-schedules').innerHTML = data.schedules
      .map(function (s) {
        var on = s.code === data.schedule;
        var isBest = best && s.code === best.code;
        var w = (Math.max(0, s.averted_pct) / max) * 100;
        var lo = (Math.max(0, s.averted_pct - s.averted_ci) / max) * 100;
        var hi = ((s.averted_pct + s.averted_ci) / max) * 100;
        var bar =
          'pmc-bar' +
          (s.channel === 'connect' ? ' connect' : '') +
          (isBest ? ' best' : '');
        var share =
          s.code === 'none'
            ? '<span class="pmc-muted">baseline</span>'
            : pct(s.averted_pct);
        var cost =
          s.cost_per_case_averted === null
            ? '<span class="pmc-muted">' +
              (s.too_noisy ? 'too noisy' : '—') +
              '</span>'
            : usd(s.cost_per_case_averted, 2);
        return (
          '<div class="pmc-sched' +
          (on ? ' on' : '') +
          '" data-code="' +
          esc(s.code) +
          '" title="' +
          esc(s.detail + ' Range across seeds: ±' + s.averted_ci + ' points.') +
          '" role="radio" aria-checked="' +
          on +
          '" tabindex="0">' +
          '<input type="radio" name="pmc-schedule" ' +
          (on ? 'checked' : '') +
          ' tabindex="-1">' +
          '<div class="name">' +
          esc(s.label) +
          (isBest ? ' <span class="pmc-chip best">best value</span>' : '') +
          '</div>' +
          '<div class="pmc-track"><div class="' +
          bar +
          '" style="width:' +
          w +
          '%"></div><div class="pmc-ci" style="left:' +
          lo +
          '%; width:' +
          Math.max(0, hi - lo) +
          '%"></div></div>' +
          '<div class="pmc-r pmc-num">' +
          share +
          '</div>' +
          '<div class="pmc-r pmc-num">' +
          cost +
          '</div>' +
          '</div>'
        );
      })
      .join('');
  }

  // The states considered: the ones handed over (from a targeting selection),
  // else every state.
  function considered() {
    if (!state.states.length) return data.states;
    var want = {};
    state.states.forEach(function (n) {
      want[n.toLowerCase()] = true;
    });
    return data.states.filter(function (r) {
      return want[r.name.toLowerCase()];
    });
  }

  var FIT = {
    near: ['near', 'matches'],
    prevalence_differs: ['lower', 'prevalence differs'],
    more_seasonal: ['out', 'more seasonal: SMC, not PMC'],
    less_seasonal: ['out', 'less seasonal'],
    unknown: ['unknown', 'no data'],
  };

  function renderWhere() {
    var rows = considered();
    var ranked = rows.filter(function (r) {
      return r.rank;
    });
    var s = chosen();
    // Rank within the states considered, not among all of Nigeria's: a
    // selection of 22 reads 1 to 9, not 2, 3, 5, ... .
    var localRank = {};
    ranked.forEach(function (r, i) {
      localRank[r.name] = i + 1;
    });
    el('pmc-scope').textContent = state.states.length
      ? rows.length + ' states from your selection'
      : 'All ' + rows.length + ' states';
    el('pmc-all').classList.toggle('hidden', !state.states.length);

    var top = ranked.slice(0, 3).map(function (r) {
      return esc(r.name);
    });
    el('pmc-where').innerHTML = ranked.length
      ? 'Most cost-effective under ' +
        esc(s ? s.label : 'this schedule') +
        ': <b>' +
        top.join(', ') +
        '</b>. ' +
        ranked.length +
        ' of ' +
        rows.length +
        ' can be ranked' +
        (rows.length > ranked.length
          ? '; the rest are too seasonal for this model.'
          : '.')
      : 'None of these states matches the modelled setting.';

    var t = { cases: 0, spend: 0, children: 0 };
    ranked.forEach(function (r) {
      t.cases += r.projection.cases_averted_per_year || 0;
      t.spend += r.projection.spend_per_year || 0;
      t.children += r.children_3_24m || 0;
    });
    el('pmc-total').innerHTML = ranked.length
      ? stat('States ranked', ranked.length) +
        // Totals of rounded rows, kept to three figures: two would turn a column
        // that sums to 215,000 into 220,000, and a reader adds the column up.
        stat('Children 3–24 mo', '≈' + num(approx(t.children, 3))) +
        stat('Cases averted / yr', '≈' + num(approx(t.cases, 3))) +
        stat('Cost / yr', '≈' + usdShort(approx(t.spend, 3))) +
        // Blended across the ranked states, so it differs from the schedule's
        // single-setting figure above; the label says so.
        stat('Blended per case', t.cases ? usd(t.spend / t.cases, 2) : '—')
      : '';
    el('pmc-total').classList.toggle('hidden', !ranked.length);

    el('pmc-states').innerHTML =
      rows
        .map(function (r) {
          var f = FIT[r.fit] || FIT.unknown;
          var p = r.projection;
          var tip =
            'Prevalence ' +
            pct(r.malaria_prevalence) +
            ' · rain in wettest quarter ' +
            pct(r.rain_wettest_quarter) +
            ' (model: ' +
            data.setting.wettest_quarter_pct +
            '%)';
          return (
            '<tr class="' +
            (r.rank ? '' : 'unranked') +
            '">' +
            '<td class="l pmc-num">' +
            (localRank[r.name] || '') +
            '</td>' +
            '<td class="l" style="font-weight:500">' +
            esc(r.name) +
            '</td>' +
            '<td class="pmc-num">' +
            num(r.malaria_incidence) +
            '</td>' +
            '<td class="l"><span class="pmc-chip ' +
            f[0] +
            '" title="' +
            esc(tip) +
            '">' +
            esc(f[1]) +
            '</span></td>' +
            '<td class="pmc-num pmc-hide-sm">' +
            num(r.children_3_24m) +
            '</td>' +
            '<td class="pmc-num">' +
            (p ? num(p.cases_averted_per_year) : '—') +
            '</td>' +
            '<td class="pmc-num pmc-hide-sm">' +
            (p ? usdShort(p.spend_per_year) : '—') +
            '</td>' +
            '<td class="pmc-num" style="font-weight:600">' +
            (p ? usd(p.cost_per_case_averted, 2) : '—') +
            '</td>' +
            '</tr>'
          );
        })
        .join('') ||
      '<tr><td class="l" colspan="8">No Nigerian states are loaded in this environment.</td></tr>';
  }

  function stat(k, v) {
    return (
      '<div><div class="k">' +
      k +
      '</div><div class="v pmc-num">' +
      v +
      '</div></div>'
    );
  }

  /* ---- events ------------------------------------------------------------ */

  function choose(code) {
    if (code && code !== state.schedule) {
      state.schedule = code;
      load();
    }
  }
  el('pmc-schedules').addEventListener('click', function (e) {
    var row = e.target.closest('.pmc-sched[data-code]');
    if (row) choose(row.getAttribute('data-code'));
  });
  el('pmc-schedules').addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    var row = e.target.closest('.pmc-sched[data-code]');
    if (row) {
      e.preventDefault();
      choose(row.getAttribute('data-code'));
    }
  });
  el('pmc-all').addEventListener('click', function () {
    state.states = [];
    syncUrl();
    renderWhere();
    shareWithAgent();
  });

  function bindPrice(id, key, scale) {
    var input = el(id);
    input.value = +(state[key] * scale).toFixed(2);
    var timer = null;
    input.addEventListener('input', function () {
      var v = parseFloat(input.value);
      if (isNaN(v)) return;
      state[key] = v / scale;
      clearTimeout(timer);
      timer = setTimeout(load, 250);
    });
  }
  bindPrice('pmc-cost', 'cost_per_visit', 1);
  bindPrice('pmc-fee', 'platform_fee', 100);
  bindPrice('pmc-rate', 'dose_rate', 100);

  load();
})();
