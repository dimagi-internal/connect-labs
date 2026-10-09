/* PMC schedule explorer. One plain script, no modules (see targeting.html on why).

   Rank  -- every (state, design) pair among the states picked, by cost per
            under-5 death averted against GiveWell's bar, each state modelled
            in its own fitted setting (/labs/targeting/api/pmc/rank/, the
            agent's targeting_pmc_rank);
   Try   -- one custom schedule in one state, a live EMOD run;
   How   -- the older comparison in one illustrative setting, kept folded.
   The states picked come from the URL (`states=`), which is how a targeting
   selection arrives -- usually via the agent panel; with none, all states.

   State lives in the URL (states, prices, deaths basis), so a link reopens the
   same view and the agent panel is told what the visitor sees. Every number
   comes from the server -- this file only draws them. */
(function () {
  'use strict';

  var cfg = window.PMC;
  var el = function (id) {
    return document.getElementById(id);
  };
  var data = null;
  var inflight = null;
  var liveRows = [];
  var gridStates = JSON.parse(
    (el('pmc-grid-states') || {}).textContent || '[]',
  );
  var BASES = ['prevalence_scaled', 'map'];
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
      deaths_basis:
        BASES.indexOf(q.get('deaths_basis')) >= 0
          ? q.get('deaths_basis')
          : BASES[0],
      top_n:
        [10, 20, 50].indexOf(num('top_n', 10)) >= 0 ? num('top_n', 10) : 10,
    };
  }

  function params() {
    var p = new URLSearchParams();
    if (state.schedule) p.set('schedule', state.schedule);
    if (state.states.length) p.set('states', state.states.join(','));
    p.set('cost_per_visit', state.cost_per_visit);
    p.set('platform_fee', state.platform_fee);
    p.set('dose_rate', state.dose_rate);
    if (state.deaths_basis !== BASES[0])
      p.set('deaths_basis', state.deaths_basis);
    if (state.top_n !== 10) p.set('top_n', state.top_n);
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
        deaths_basis: state.deaths_basis,
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
        var first = !data;
        data = d;
        if (first) fillLiveControls();
        state.schedule = d.schedule;
        syncUrl();
        render();
        shareWithAgent();
      })
      .catch(function (err) {
        if (err && err.name === 'AbortError') return;
        el('pmc-how').textContent = 'Could not load: ' + err.message;
      });
    loadRank();
  }

  function render() {
    el('pmc-per-dose').textContent = usd(data.costs.cost_per_dose, 2);
    renderHow();
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

    var shown = data.schedules.concat(liveRows);
    var max = 1;
    shown.forEach(function (s) {
      max = Math.max(max, s.averted_pct + s.averted_ci);
    });
    el('pmc-schedules').innerHTML = shown
      .map(function (s) {
        var on = !s.live && s.code === data.schedule;
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
          '"' +
          (s.live ? '' : ' data-code="' + esc(s.code) + '"') +
          ' title="' +
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
          (s.live
            ? ' <span class="pmc-chip live">' + esc(s.tag) + '</span>'
            : '') +
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

  /* ---- rank: states x designs ------------------------------------------- */

  var rankInflight = null;
  var rankData = null;

  // The picker's chips: every state the per-state grid holds, the year-round
  // ones marked. Nothing picked = every state, as the agent tool reads it.
  function picked(name) {
    var want = name.toLowerCase();
    return state.states.some(function (n) {
      return n.toLowerCase() === want;
    });
  }

  function renderPicker() {
    el('pmc-pick-states').innerHTML = gridStates
      .map(function (s) {
        var on = picked(s.name);
        var tip =
          'Child malaria prevalence ' +
          pct(s.pfpr * 100) +
          ' · ' +
          pct(s.rain_wettest_quarter) +
          ' of the rain in the wettest quarter';
        return (
          '<button type="button" class="pmc-pill" data-state="' +
          esc(s.name) +
          '" aria-pressed="' +
          on +
          '" title="' +
          esc(tip) +
          '">' +
          (s.perennial ? '<span class="dot"></span>' : '') +
          esc(s.name) +
          '</button>'
        );
      })
      .join('');
    el('pmc-rank-scope').textContent = state.states.length
      ? state.states.length + ' states picked'
      : 'All ' + gridStates.length + ' states';
    document.querySelectorAll('.pmc-seg [data-basis]').forEach(function (b) {
      b.setAttribute(
        'aria-pressed',
        String(b.getAttribute('data-basis') === state.deaths_basis),
      );
    });
    el('pmc-topn').value = String(state.top_n);
  }

  function rankChanged() {
    syncUrl();
    renderPicker();
    shareWithAgent();
    loadRank();
  }

  el('pmc-pick-states').addEventListener('click', function (e) {
    var b = e.target.closest('[data-state]');
    if (!b) return;
    var name = b.getAttribute('data-state');
    // From "every state" (nothing picked), a click starts a pick of one.
    state.states = picked(name)
      ? state.states.filter(function (n) {
          return n.toLowerCase() !== name.toLowerCase();
        })
      : state.states.concat([name]);
    rankChanged();
  });
  document.querySelectorAll('[data-preset]').forEach(function (b) {
    b.addEventListener('click', function () {
      var preset = b.getAttribute('data-preset');
      state.states =
        preset === 'perennial'
          ? gridStates
              .filter(function (s) {
                return s.perennial;
              })
              .map(function (s) {
                return s.name;
              })
          : [];
      rankChanged();
    });
  });
  document.querySelectorAll('.pmc-seg [data-basis]').forEach(function (b) {
    b.addEventListener('click', function () {
      state.deaths_basis = b.getAttribute('data-basis');
      rankChanged();
    });
  });
  el('pmc-topn').addEventListener('change', function () {
    state.top_n = parseInt(this.value, 10) || 10;
    rankChanged();
  });

  function loadRank() {
    if (rankInflight) rankInflight.abort();
    var ctrl = new AbortController();
    rankInflight = ctrl;
    var p = params();
    p.delete('schedule');
    p.set('deaths_basis', state.deaths_basis);
    p.set('top_n', state.top_n);
    fetch(cfg.rankUrl + '?' + p.toString(), {
      signal: ctrl.signal,
      credentials: 'same-origin',
    })
      .then(function (r) {
        return r.json().then(function (j) {
          if (!r.ok) throw new Error(j.error || 'HTTP ' + r.status);
          return j;
        });
      })
      .then(function (d) {
        rankData = d;
        renderRank();
        fillLiveStates();
        renderLiveResults();
      })
      .catch(function (err) {
        if (err && err.name === 'AbortError') return;
        el('pmc-rank-answer').textContent = 'Could not rank: ' + err.message;
        el('pmc-rank-rows').innerHTML = '';
      });
  }

  function times(n) {
    return n === null || n === undefined ? '—' : num(n, 1) + '×';
  }

  function barChip(p, bar) {
    if (p.multiple_of_benchmark === undefined) return '';
    return p.clears_bar
      ? ' <span class="pmc-chip clears">clears ' + num(bar) + '×</span>'
      : ' <span class="pmc-chip below">below ' + num(bar) + '×</span>';
  }

  function renderRank() {
    var d = rankData;
    var total = el('pmc-rank-total');
    if (!d.available) {
      el('pmc-rank-answer').textContent = d.message;
      el('pmc-rank-rows').innerHTML = '';
      total.classList.add('hidden');
      el('pmc-rank-note').textContent = '';
      return;
    }
    var byDeath = d.ranked_by === 'cost per death averted';
    var rows = d.ranked || [];
    var best = rows[0];
    var perDose = usd(d.costs.cost_per_dose, 2);
    var considered = d.best_per_state.length;
    if (!best) {
      el('pmc-rank-answer').textContent =
        'None of these states can be ranked; see why below.';
    } else if (byDeath) {
      el('pmc-rank-answer').innerHTML =
        'At ' +
        perDose +
        ' a dose, <b>' +
        d.states_clearing_bar +
        ' of ' +
        considered +
        '</b> states clear GiveWell’s ' +
        num(d.bar) +
        '× bar with their best design. Best value: <b>' +
        esc(best.state_design) +
        '</b>, ' +
        usd(best.cost_per_death_averted) +
        ' per under-5 death averted, ' +
        times(best.multiple_of_benchmark) +
        ' GiveWell’s benchmark.';
    } else {
      el('pmc-rank-answer').innerHTML =
        'At ' +
        perDose +
        ' a dose, best value: <b>' +
        esc(best.state_design) +
        '</b>, ' +
        usd(best.cost_per_case_averted, 2) +
        ' per case averted.';
    }

    var t = d.best_per_state_totals || {};
    total.innerHTML = considered
      ? stat('States ranked', considered) +
        (byDeath
          ? stat('Clear the ' + num(d.bar) + '× bar', d.states_clearing_bar) +
            stat(
              'Deaths averted / yr',
              '≈' + num(approx(t.deaths_averted_per_year, 3)),
            )
          : stat(
              'Cases averted / yr',
              '≈' + num(approx(t.cases_averted_per_year, 3)),
            )) +
        stat('Cost / yr', '≈' + usdShort(approx(t.spend_per_year, 3))) +
        (byDeath && t.deaths_averted_per_year
          ? stat(
              'Blended per death',
              usd(approx(t.spend_per_year / t.deaths_averted_per_year, 2)),
            )
          : '')
      : '';
    total.classList.toggle('hidden', !considered);
    // The totals are each state's best design, not the rows below.
    total.title = 'Each ranked state at its own best design';

    el('pmc-rank-rows').innerHTML =
      rows
        .map(function (p) {
          return (
            '<tr title="' +
            esc(p.fit_note || '') +
            '">' +
            '<td class="l pmc-num">' +
            p.rank +
            '</td>' +
            '<td class="l" style="font-weight:500">' +
            esc(p.state) +
            '</td>' +
            '<td class="design">' +
            esc(p.design_label) +
            (p.kind === 'smc' ? ' <span class="pmc-chip smc">SMC</span>' : '') +
            '</td>' +
            '<td class="pmc-num">' +
            (byDeath
              ? num(p.deaths_averted_per_year)
              : num(p.cases_averted_per_year) + ' cases') +
            '</td>' +
            '<td class="pmc-num pmc-hide-sm">' +
            usdShort(p.spend_per_year) +
            '</td>' +
            '<td class="pmc-num" style="font-weight:600">' +
            (byDeath
              ? usd(p.cost_per_death_averted)
              : usd(p.cost_per_case_averted, 2) + ' / case') +
            '</td>' +
            '<td class="pmc-num" style="white-space:nowrap">' +
            (byDeath
              ? times(p.multiple_of_benchmark) + barChip(p, d.bar)
              : '—') +
            '</td>' +
            '</tr>'
          );
        })
        .join('') || '<tr><td class="l" colspan="7">Nothing to rank.</td></tr>';

    var notes = [];
    if (d.note) notes.push(d.note);
    if (d.excluded && d.excluded.length)
      notes.push(
        'Not ranked: ' +
          d.excluded
            .map(function (x) {
              return x.state + ' (' + x.reason + ')';
            })
            .join(', ') +
          '.',
      );
    notes.push(d.costs_line);
    notes = notes.concat(d.caveats || []);
    el('pmc-rank-note').innerHTML = notes
      .map(function (n) {
        return esc(n);
      })
      .join('<br>');
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

  /* ---- try another schedule: a live EMOD run ----------------------------- */

  var MONTHS = [
    'January',
    'February',
    'March',
    'April',
    'May',
    'June',
    'July',
    'August',
    'September',
    'October',
    'November',
    'December',
  ];
  var POLL_MS = 3000;
  var liveTimer = null;
  var tickTimer = null;

  // The token is in the page's form (base.html), not a cookie: the cookie is
  // not readable here (sessions hold the token and the cookie is HttpOnly).
  function csrfToken() {
    var input = document.querySelector('[name=csrfmiddlewaretoken]');
    return (input && input.value) || cfg.csrf || '';
  }

  function fillLiveControls() {
    el('pmc-live-start').innerHTML = MONTHS.map(function (m, i) {
      return (
        '<option value="' +
        (i + 1) +
        '"' +
        (i === 4 ? ' selected' : '') +
        '>' +
        m +
        '</option>'
      );
    }).join('');
    if (!gridStates.length) fillLiveStatesFromSweep();
  }

  // Before the per-state grid: the states matching the one modelled setting,
  // matches first (best-ranked on top), then lower-confidence fits; Ondo first.
  function fillLiveStatesFromSweep() {
    var ok = data.states.filter(function (r) {
      return r.fit === 'near' || r.fit === 'prevalence_differs';
    });
    ok.sort(function (a, b) {
      return (
        (a.fit === 'near' ? 0 : 1) - (b.fit === 'near' ? 0 : 1) ||
        (a.rank || 999) - (b.rank || 999)
      );
    });
    var pick =
      ok.filter(function (r) {
        return r.name === 'Ondo';
      })[0] || ok[0];
    setLiveStates(
      ok.map(function (r) {
        return r.name;
      }),
      pick && pick.name,
    );
  }

  // With the grid: every fitted state, each run in its own setting. The
  // default is the ranking's top state, until the visitor picks one.
  var liveStateTouched = false;
  function fillLiveStates() {
    if (!gridStates.length || liveStateTouched) return;
    var top = rankData && rankData.ranked && rankData.ranked[0];
    setLiveStates(
      gridStates.map(function (s) {
        return s.name;
      }),
      top ? top.state : gridStates[0].name,
    );
  }

  function setLiveStates(names, pick) {
    el('pmc-live-state').innerHTML = names
      .map(function (n) {
        return (
          '<option' +
          (n === pick ? ' selected' : '') +
          '>' +
          esc(n) +
          '</option>'
        );
      })
      .join('');
    el('pmc-live-run').disabled = !names.length;
  }
  el('pmc-live-state').addEventListener('change', function () {
    liveStateTouched = true;
  });

  function liveSpec() {
    var spec = {};
    if (el('pmc-live-mode').value === 'year') {
      spec.rounds_per_year = parseInt(el('pmc-live-rpy').value, 10);
    } else {
      var start = parseInt(el('pmc-live-start').value, 10);
      var n = Math.min(
        12,
        Math.max(1, parseInt(el('pmc-live-n').value, 10) || 1),
      );
      spec.months = [];
      for (var i = 0; i < n; i++) spec.months.push(((start - 1 + i) % 12) + 1);
    }
    spec.age_min_months = parseInt(el('pmc-live-amin').value, 10);
    spec.age_max_months = parseInt(el('pmc-live-amax').value, 10);
    spec.coverage = parseFloat(el('pmc-live-cov').value) / 100;
    return spec;
  }

  function liveMsg(text, isError) {
    var m = el('pmc-live-msg');
    m.textContent = text;
    m.classList.toggle('err', !!isError);
  }

  function mmss(sec) {
    var s = Math.max(0, Math.round(sec));
    return Math.floor(s / 60) + ':' + ('0' + (s % 60)).slice(-2);
  }

  function stopLive() {
    clearTimeout(liveTimer);
    clearInterval(tickTimer);
    liveTimer = tickTimer = null;
    el('pmc-live-run').disabled = false;
  }

  var RUN_CAP_MS = 8 * 60 * 1000;
  var liveSeq = 0;

  function startLive() {
    // Nothing from an earlier run may fire into this one.
    clearTimeout(liveTimer);
    clearInterval(tickTimer);
    var stateName = el('pmc-live-state').value;
    var started = Date.now();
    var etaS = null;
    var failures = 0;
    var finished = false;
    if (
      liveRows.some(function (r) {
        return r.state !== stateName;
      })
    ) {
      liveRows = [];
      renderHow();
    }
    el('pmc-live-run').disabled = true;
    liveMsg('Starting…');

    function showRunning() {
      if (finished) return;
      liveMsg(
        'Running IDM’s EMOD model… ' +
          mmss((Date.now() - started) / 1000) +
          (etaS
            ? ' (about ' + Math.max(1, Math.round(etaS / 60)) + ' min)'
            : ''),
      );
    }
    function end() {
      finished = true;
      stopLive();
    }
    function fail(text) {
      end();
      liveMsg(text, true);
    }
    function done(payload) {
      end();
      if (!payload.result)
        return liveMsg('The model returned no result.', true);
      addLiveResult(payload.result, stateName);
    }
    function later(id) {
      if (finished) return;
      if (Date.now() - started > RUN_CAP_MS)
        return fail(
          'The model is taking longer than expected; try again in a few minutes.',
        );
      liveTimer = setTimeout(function () {
        poll(id);
      }, POLL_MS);
    }
    // Transport (fetch + JSON) is handled apart from what the response says,
    // so a bug in rendering a result can never read as a lost connection.
    function request(url, opts) {
      return fetch(
        url,
        Object.assign({ credentials: 'same-origin' }, opts || {}),
      ).then(function (r) {
        return r.json().then(function (j) {
          return { status: r.status, body: j };
        });
      });
    }
    function guarded(fn) {
      return function (r) {
        if (finished) return;
        try {
          fn(r);
        } catch (e) {
          fail('The result could not be shown: ' + e.message);
        }
      };
    }
    function poll(id) {
      if (finished) return;
      request(
        cfg.statusUrl.replace('{id}', id) +
          '?state=' +
          encodeURIComponent(stateName),
      ).then(
        guarded(function (r) {
          failures = 0;
          var b = r.body;
          if (b.status === 'completed') return done(b);
          if (b.status === 'failed' || r.status >= 400)
            return fail(b.error || 'The model run failed.');
          etaS = b.eta_s;
          later(id);
        }),
        function () {
          if (finished) return;
          if (++failures >= 5) return fail('Lost contact with the server.');
          later(id);
        },
      );
    }

    request(cfg.runUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken(),
      },
      body: JSON.stringify({ state: stateName, schedule: liveSpec() }),
    }).then(
      guarded(function (r) {
        var b = r.body;
        if (r.status === 200) return done(b);
        if (r.status === 202) {
          etaS = b.eta_s;
          showRunning();
          tickTimer = setInterval(showRunning, 1000);
          return poll(b.run_id);
        }
        fail(
          b.error ||
            (r.status === 429
              ? 'The live model is busy with other runs; try again in a few minutes.'
              : 'The model could not be started.'),
        );
      }),
      function () {
        fail('Could not reach the server.');
      },
    );
  }

  // One finished run: a row in the schedules list (same rendering as the
  // grid's, costed at the chosen state) and a line with its numbers.
  function addLiveResult(res, stateName) {
    var eff = res.effect || {};
    var proj = res.projection;
    var val = res.value;
    var precomputed = /precomputed/.test(res.label || '');
    liveRows.push({
      code: 'live_' + ++liveSeq,
      state: stateName,
      live: true,
      tag: (precomputed ? 'precomputed' : 'live run') + ' · ' + stateName,
      label: (res.schedule && res.schedule.description) || 'Custom schedule',
      detail: (res.label || '') + ' · costed at ' + stateName + '.',
      channel: 'connect',
      averted_pct: eff.averted_pct,
      averted_ci: eff.averted_ci,
      too_noisy: !!eff.too_noisy,
      cost_per_case_averted: proj ? proj.cost_per_case_averted : null,
      value: val,
      spend: proj ? proj.spend_per_year : null,
    });
    liveRows = liveRows.slice(-3);
    renderHow();
    renderLiveResults();
    liveMsg(
      stateName +
        ': ' +
        (val
          ? '≈' +
            num(val.deaths_averted_per_year) +
            ' under-5 deaths averted a year at ' +
            usd(val.cost_per_death_averted) +
            ' each, ' +
            times(val.multiple_of_benchmark) +
            ' GiveWell’s benchmark (' +
            (val.clears_bar ? 'clears' : 'below') +
            ' the ' +
            num(val.bar) +
            '× bar)'
          : pct(eff.averted_pct) +
            ' of cases averted' +
            (proj
              ? ', ≈' +
                num(proj.cases_averted_per_year) +
                ' cases a year at ' +
                usd(proj.cost_per_case_averted, 2) +
                ' each'
              : ', too uncertain to cost')) +
        ' · ' +
        (res.label || 'illustrative · live model run'),
    );
  }

  // The runs so far, each beside its state's best ranked design (when that
  // state is in the ranking on screen), so a custom schedule reads against it.
  function renderLiveResults() {
    var best = {};
    ((rankData && rankData.best_per_state) || []).forEach(function (p) {
      best[p.state] = p;
    });
    var rows = [];
    liveRows.forEach(function (r) {
      var v = r.value;
      rows.push(
        '<tr><td class="l" style="font-weight:500">' +
          esc(r.state) +
          '</td><td class="design">' +
          esc(r.label) +
          ' <span class="pmc-chip live">' +
          esc(r.tag.split(' · ')[0]) +
          '</span></td><td class="pmc-num">' +
          (v ? num(v.deaths_averted_per_year) : '—') +
          '</td><td class="pmc-num pmc-hide-sm">' +
          usdShort(r.spend) +
          '</td><td class="pmc-num" style="font-weight:600">' +
          (v ? usd(v.cost_per_death_averted) : '—') +
          '</td><td class="pmc-num" style="white-space:nowrap">' +
          (v ? times(v.multiple_of_benchmark) + barChip(v, v.bar) : '—') +
          '</td></tr>',
      );
      var b = best[r.state];
      if (b && b.multiple_of_benchmark !== undefined)
        rows.push(
          '<tr class="unranked"><td class="l"></td><td class="design">Best ranked design: ' +
            esc(b.design_label) +
            '</td><td class="pmc-num">' +
            num(b.deaths_averted_per_year) +
            '</td><td class="pmc-num pmc-hide-sm">' +
            usdShort(b.spend_per_year) +
            '</td><td class="pmc-num">' +
            usd(b.cost_per_death_averted) +
            '</td><td class="pmc-num">' +
            times(b.multiple_of_benchmark) +
            '</td></tr>',
        );
    });
    el('pmc-live-results').innerHTML = rows.length
      ? '<table class="pmc-table"><thead><tr><th class="l">State</th><th class="l">Schedule</th>' +
        '<th>Deaths averted / yr</th><th class="pmc-hide-sm">Cost / yr</th><th>Per death averted</th>' +
        '<th>&times; GiveWell</th></tr></thead><tbody>' +
        rows.join('') +
        '</tbody></table>'
      : '';
  }

  el('pmc-live-mode').addEventListener('change', function () {
    var year = this.value === 'year';
    el('pmc-live-months').classList.toggle('hidden', year);
    el('pmc-live-year').classList.toggle('hidden', !year);
  });
  el('pmc-live-run').addEventListener('click', startLive);

  renderPicker();
  load();
})();
