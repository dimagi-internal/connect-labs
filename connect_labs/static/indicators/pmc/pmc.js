/* PMC schedule explorer. One plain script, no modules (see targeting.html on why).

   State lives in the URL query (schedule, states, prices), so a link reopens the
   same view and the agent panel is told the same thing the visitor sees. The
   numbers all come from /labs/targeting/api/pmc/ -- this file only draws them. */
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
  // Large sums in a table cell: $1.33M, $808k. Totals keep the full figure.
  function usdShort(n) {
    if (n === null || n === undefined) return '—';
    if (n >= 1e6) return '$' + (n / 1e6).toFixed(2) + 'M';
    if (n >= 1e3) return '$' + Math.round(n / 1e3) + 'k';
    return usd(n);
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

  function load() {
    if (inflight) inflight.abort();
    var ctrl = new AbortController();
    inflight = ctrl;
    history.replaceState(
      null,
      '',
      window.location.pathname + '?' + params().toString(),
    );
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
        history.replaceState(
          null,
          '',
          window.location.pathname + '?' + params().toString(),
        );
        render();
        shareWithAgent();
      })
      .catch(function (err) {
        if (err && err.name === 'AbortError') return;
        el('pmc-states').innerHTML =
          '<tr><td class="l" colspan="11">Could not load: ' +
          esc(err.message) +
          '</td></tr>';
      });
  }

  function render() {
    el('pmc-per-dose').textContent = usd(data.costs.cost_per_dose, 2);
    renderSchedules();
    renderStates();
  }

  function renderSchedules() {
    var max = 0;
    data.schedules.forEach(function (s) {
      max = Math.max(max, s.averted_pct + s.averted_ci);
    });
    max = Math.max(max, 1);
    el('pmc-schedules').innerHTML = data.schedules
      .map(function (s) {
        var on = s.code === data.schedule;
        var best = s.code === data.best_schedule;
        var w = (Math.max(0, s.averted_pct) / max) * 100;
        var lo = (Math.max(0, s.averted_pct - s.averted_ci) / max) * 100;
        var hi = ((s.averted_pct + s.averted_ci) / max) * 100;
        var cls =
          'pmc-bar' +
          (s.channel === 'connect' ? ' connect' : '') +
          (best ? ' best' : '');
        var cost =
          s.cost_per_case_averted === null
            ? '<div class="pmc-cost muted">' +
              (s.too_noisy ? 'too noisy' : 'baseline') +
              '</div>'
            : '<div class="pmc-cost">' +
              usd(s.cost_per_case_averted, 2) +
              '</div>';
        return (
          '<div class="pmc-sched' +
          (on ? ' on' : '') +
          '" data-code="' +
          esc(s.code) +
          '" role="radio" aria-checked="' +
          on +
          '" tabindex="0">' +
          '<input type="radio" name="pmc-schedule" ' +
          (on ? 'checked' : '') +
          ' tabindex="-1">' +
          '<div><div class="name">' +
          esc(s.label) +
          (best ? ' <span class="pmc-chip best">best value</span>' : '') +
          '</div>' +
          '<div class="sub">' +
          esc(s.detail) +
          '</div></div>' +
          '<div style="display:flex; align-items:center; gap:10px">' +
          '<div class="pmc-track" style="flex:1"><div class="' +
          cls +
          '" style="width:' +
          w +
          '%"></div>' +
          '<div class="pmc-ci" style="left:' +
          lo +
          '%; width:' +
          Math.max(0, hi - lo) +
          '%"></div></div>' +
          '<div class="pmc-pct pmc-num' +
          (s.too_noisy ? ' muted' : '') +
          '" style="width:92px">' +
          pct(s.averted_pct) +
          ' <span style="font-weight:400; color:#78716c; font-size:12px">±' +
          num(s.averted_ci, 1) +
          '</span></div>' +
          '</div>' +
          '<div class="pmc-num" style="text-align:right">' +
          num(s.doses) +
          '</div>' +
          cost +
          '</div>'
        );
      })
      .join('');
    var chosen = data.schedules.filter(function (s) {
      return s.code === data.schedule;
    })[0];
    el('pmc-chosen').textContent = chosen ? chosen.label : '';
  }

  function renderStates() {
    var picked = {};
    state.states.forEach(function (n) {
      picked[n.toLowerCase()] = true;
    });
    var rows = data.states;
    var totals = {
      states: 0,
      children: 0,
      cases: 0,
      doses: 0,
      spend: 0,
      unprojected: 0,
    };
    el('pmc-states').innerHTML =
      rows
        .map(function (r) {
          var on = !!picked[r.name.toLowerCase()];
          var p = r.projection || {};
          if (on) {
            totals.states += 1;
            // A state the model cannot speak for is counted, never totalled:
            // its children with no cases averted would flatter the cost.
            if (!r.projection) totals.unprojected += 1;
            else totals.children += r.children_3_24m || 0;
            totals.cases += p.cases_averted_per_year || 0;
            totals.doses += p.doses_per_year || 0;
            totals.spend += p.spend_per_year || 0;
          }
          var fit = {
            near: 'near model',
            outside: 'outside range',
            unknown: 'no data',
          }[r.fit];
          return (
            '<tr class="' +
            (on ? 'on' : '') +
            '" data-name="' +
            esc(r.name) +
            '" style="cursor:pointer">' +
            '<td class="l"><input type="checkbox" ' +
            (on ? 'checked' : '') +
            ' tabindex="-1" aria-label="Select ' +
            esc(r.name) +
            '"></td>' +
            '<td class="l" style="font-weight:500; white-space:nowrap">' +
            esc(r.name) +
            '</td>' +
            '<td class="pmc-num" title="' +
            esc(r.sources.malaria_prevalence) +
            '">' +
            pct(r.malaria_prevalence) +
            '</td>' +
            '<td class="l"><span class="pmc-chip ' +
            esc(r.fit) +
            '">' +
            fit +
            '</span></td>' +
            '<td class="pmc-num">' +
            pct(r.rain_wettest_quarter) +
            '</td>' +
            '<td class="pmc-num">' +
            pct(r.zero_dose) +
            '</td>' +
            '<td class="pmc-num">' +
            pct(r.dpt3_vaccination) +
            '</td>' +
            '<td class="pmc-num">' +
            num(r.children_3_24m) +
            '</td>' +
            (r.projection
              ? '<td class="pmc-num">' +
                num(p.cases_averted_per_year) +
                '</td>' +
                '<td class="pmc-num">' +
                num(p.doses_per_year) +
                '</td>' +
                '<td class="pmc-num">' +
                usdShort(p.spend_per_year) +
                '</td>'
              : '<td class="l pmc-muted" colspan="3">needs its own model run</td>') +
            '</tr>'
          );
        })
        .join('') ||
      '<tr><td class="l" colspan="11">No Nigerian states are loaded in this environment.</td></tr>';

    el('pmc-total').innerHTML = totals.states
      ? stat('States', totals.states) +
        stat('Children 3–24 mo (projected)', num(totals.children)) +
        stat('Cases averted / yr', num(totals.cases)) +
        stat('Doses / yr', num(totals.doses)) +
        stat('Cost / yr', usd(totals.spend)) +
        stat(
          'Per case averted',
          totals.cases ? usd(totals.spend / totals.cases, 2) : '—',
        ) +
        (totals.unprojected
          ? stat('Need their own run', totals.unprojected)
          : '')
      : '<div class="v" style="font-size:14px; font-weight:500">Select states to total a programme. ' +
        'Projections assume each state behaves like the modelled setting.</div>';
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

  el('pmc-states').addEventListener('click', function (e) {
    var row = e.target.closest('tr[data-name]');
    if (!row) return;
    var name = row.getAttribute('data-name');
    var i = state.states
      .map(function (s) {
        return s.toLowerCase();
      })
      .indexOf(name.toLowerCase());
    if (i >= 0) state.states.splice(i, 1);
    else state.states.push(name);
    history.replaceState(
      null,
      '',
      window.location.pathname + '?' + params().toString(),
    );
    renderStates();
    shareWithAgent();
  });
  el('pmc-clear').addEventListener('click', function () {
    state.states = [];
    history.replaceState(
      null,
      '',
      window.location.pathname + '?' + params().toString(),
    );
    renderStates();
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
