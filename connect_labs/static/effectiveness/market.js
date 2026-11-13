(function () {
  'use strict';
  var U = window.EffUI;
  var h = U.h,
    put = U.put,
    usd = U.usd,
    num = U.num;
  function mult(v) {
    return v == null ? '–' : U.mult(v) + 'x';
  }
  var D = JSON.parse(document.getElementById('market-data').textContent);
  var $ = function (id) {
    return document.getElementById(id);
  };

  // ---- state, read from and written to the link
  var q = new URLSearchParams(location.search);
  var state = {
    models: (q.get('models') || 'chc').split(',').filter(function (m) {
      return D.models[m];
    }),
    indicator: q.get('indicator') || 'all',
    threshold: q.get('threshold'),
    bar: q.get('bar') || String(D.bar),
    iso: q.get('iso') || '',
    over: {},
  };
  if (!state.models.length) state.models = ['chc'];
  q.forEach(function (v, k) {
    var m = /^i\.(\w+)\.(\w+)$/.exec(k);
    // Only a known model and one of its own inputs: a crafted link cannot name `__proto__` or anything else.
    if (!m || !Object.prototype.hasOwnProperty.call(D.models, m[1])) return;
    var known = D.models[m[1]].inputs.some(function (i) {
      return i.key === m[2];
    });
    if (!known || isNaN(parseFloat(v))) return;
    state.over[m[1]] = state.over[m[1]] || {};
    state.over[m[1]][m[2]] = parseFloat(v);
  });
  function indicatorMeta() {
    return (
      D.indicators.filter(function (i) {
        return i.key === state.indicator;
      })[0] || D.indicators[0]
    );
  }
  if (state.threshold == null)
    state.threshold = String(
      indicatorMeta().threshold == null ? '' : indicatorMeta().threshold,
    );

  function params() {
    var p = new URLSearchParams({
      models: state.models.join(','),
      indicator: state.indicator,
      bar: state.bar,
    });
    if (state.indicator !== 'all') p.set('threshold', state.threshold);
    if (state.iso.trim()) p.set('iso', state.iso.replace(/\s+/g, ''));
    Object.keys(state.over).forEach(function (m) {
      if (state.models.indexOf(m) < 0) return;
      Object.keys(state.over[m]).forEach(function (k) {
        p.set('i.' + m + '.' + k, state.over[m][k]);
      });
    });
    return p;
  }

  // ---- controls
  put(
    $('indicator'),
    D.indicators.map(function (i) {
      return h('option', { value: i.key, text: i.label });
    }),
  );
  $('indicator').value = state.indicator;
  $('threshold').value = state.threshold;
  $('bar').value = state.bar;
  $('iso').value = state.iso;
  function showThreshold() {
    $('thr-wrap').hidden = state.indicator === 'all';
  }
  showThreshold();
  function drawModels() {
    put(
      $('models'),
      Object.keys(D.models).map(function (k) {
        var m = D.models[k],
          on = state.models.indexOf(k) >= 0;
        return h('label', { class: 'mchip' + (on ? ' on' : '') }, [
          h('input', {
            type: 'checkbox',
            data: { model: k },
            checked: on ? true : null,
          }),
          h('span', {}, [m.label]),
        ]);
      }),
    );
  }
  function inputRow(model, i) {
    var cur = (state.over[model] || {}).hasOwnProperty(i.key)
      ? state.over[model][i.key]
      : i.value;
    return h(
      'div',
      {
        class:
          'ir' +
          ((state.over[model] || {}).hasOwnProperty(i.key) ? ' changed' : ''),
      },
      [
        h('div', {}, [i.label, h('small', { text: 'model: ' + i.value })]),
        h('input', {
          type: 'number',
          step: 'any',
          value: String(cur),
          data: { model: model, key: i.key, def: String(i.value) },
        }),
      ],
    );
  }
  function drawInputs() {
    put(
      $('inputs'),
      state.models.map(function (k) {
        var m = D.models[k],
          kids = [h('summary', { text: m.label + ': inputs' })];
        D.blocks.forEach(function (b) {
          var rows = m.inputs.filter(function (i) {
            return i.group === b.key;
          });
          if (!rows.length) return;
          kids.push(h('div', { class: 'gt', text: b.title }));
          rows.forEach(function (i) {
            kids.push(inputRow(k, i));
          });
        });
        return h('details', { class: 'mi', open: true }, kids);
      }),
    );
  }
  drawModels();
  drawInputs();

  var timer = null,
    seq = 0,
    showAll = false,
    last = null;
  function go() {
    clearTimeout(timer);
    try {
      history.replaceState(
        null,
        '',
        location.pathname + '?' + params().toString(),
      );
    } catch (e) {
      /* sandboxed */
    }
    timer = setTimeout(function () {
      var mine = ++seq;
      fetch(D.api_url + '?' + params().toString(), {
        credentials: 'same-origin',
      })
        .then(function (r) {
          return r.json().then(function (j) {
            return { ok: r.ok, j: j };
          });
        })
        .then(function (res) {
          if (mine !== seq) return;
          var e = $('error');
          if (!res.ok) {
            e.hidden = false;
            e.textContent = res.j.error || 'That did not run.';
            return;
          }
          e.hidden = true;
          last = res.j;
          draw(res.j);
        })
        .catch(function () {
          var e = $('error');
          e.hidden = false;
          e.textContent = 'The server did not respond. Try again.';
        });
    }, 250);
  }

  function draw(r) {
    var all = 0,
      clear = 0,
      ppl = 0,
      floor = false,
      unm = 0;
    r.models.forEach(function (m) {
      all += m.totals.market_usd;
      clear += m.totals.clearing_usd;
      floor = floor || m.totals.is_floor;
      unm += m.totals.unmodelled_countries;
    });
    put($('tiles'), [
      h('div', { class: 'tile' }, [
        h('div', { class: 'big', text: usd(all) + (floor ? '+' : '') }),
        h('div', {
          class: 'cap',
          text:
            'a year, to reach everyone in these places' +
            (r.models.length > 1 ? ' (programmes added together)' : ''),
        }),
      ]),
      h('div', { class: 'tile' }, [
        h('div', { class: 'big', text: usd(clear) }),
        h('div', {
          class: 'cap',
          text: 'where the model clears ' + r.bar + "x GiveWell's benchmark",
        }),
      ]),
      h('div', { class: 'tile' }, [
        h('div', { class: 'big', text: String(r.selection.countries) }),
        h('div', {
          class: 'cap',
          text: r.selection.everywhere
            ? 'of ' + r.selection.africa_total + ' African countries have data'
            : 'countries with ' +
              r.selection.regions +
              ' regions above ' +
              r.selection.threshold +
              ' on ' +
              r.selection.indicator_label,
        }),
      ]),
    ]);
    var missing = r.selection.unsupported.length;
    put($('sel-note'), [
      (floor
        ? 'A plus means some places have no count, so this is a floor. '
        : '') +
        (unm
          ? unm +
            (unm === 1 ? ' country has' : ' countries have') +
            ' no model figures: counted in the market, never in the clearing part. '
          : ''),
      missing > 0
        ? h('details', { css: { display: 'inline' } }, [
            h('summary', {
              css: { display: 'inline', cursor: 'pointer' },
              text:
                missing +
                (missing === 1 ? ' country has' : ' countries have') +
                ' no data',
            }),
            ' ' + r.selection.unsupported.join(', ') + '.',
          ])
        : '',
    ]);
    put(
      $('by-model'),
      r.models.map(function (m) {
        var t = m.totals;
        return h('div', { class: 'mline' }, [
          h('b', { text: D.models[m.model].label }),
          h('span', { text: num(t.units) + ' ' + m.noun }),
          h('span', { text: usd(t.market_usd) + ' a year' }),
          h('span', {
            class: t.clearing_countries ? 'ok' : 'lo',
            text:
              t.clearing_countries +
              ' of ' +
              t.countries +
              ' countries clear the bar (' +
              usd(t.clearing_usd) +
              ')',
          }),
        ]);
      }),
    );
    var rows = [];
    r.models.forEach(function (m) {
      m.rows.forEach(function (x) {
        rows.push({ m: m.model, x: x });
      });
    });
    rows.sort(function (a, b) {
      return (b.x.market_usd || 0) - (a.x.market_usd || 0);
    });
    var shown = showAll ? rows : rows.slice(0, 12);
    $('more').hidden = rows.length <= 12;
    $('more').textContent = showAll
      ? 'Show fewer'
      : 'Show all ' + rows.length + ' rows';
    put($('rows'), [
      h('thead', {}, [
        h('tr', {}, [
          h('th', { text: 'Country' }),
          h('th', { text: 'Programme' }),
          h('th', { class: 'n', text: 'People' }),
          h('th', { class: 'n', text: 'A year, each' }),
          h('th', { class: 'n', text: 'Market' }),
          h('th', { class: 'n', text: 'Multiple' }),
        ]),
      ]),
      h(
        'tbody',
        {},
        shown.map(function (o) {
          var x = o.x;
          return h('tr', {}, [
            h('td', { text: x.country }),
            h('td', { text: D.models[o.m].label.split(' ')[0] }),
            h('td', {
              class: 'n',
              text: num(x.units) + (x.partial ? '+' : ''),
            }),
            h('td', { class: 'n', text: '$' + x.cost_per_person.toFixed(2) }),
            h('td', { class: 'n', text: usd(x.market_usd) }),
            h('td', {
              class: 'n ' + (x.clears_bar ? 'ok' : 'lo'),
              text: x.modelled ? mult(x.multiple) : 'no model figure',
            }),
          ]);
        }),
      ),
    ]);
  }
  $('more').addEventListener('click', function () {
    showAll = !showAll;
    if (last) draw(last);
  });

  // ---- events
  $('models').addEventListener('change', function (e) {
    var k = e.target.dataset.model;
    if (!k) return;
    var on = state.models.slice();
    var at = on.indexOf(k);
    if (e.target.checked && at < 0) on.push(k);
    if (!e.target.checked && at >= 0) on.splice(at, 1);
    if (!on.length) {
      e.target.checked = true;
      return;
    }
    state.models = on;
    drawModels();
    drawInputs();
    go();
  });
  $('indicator').addEventListener('change', function (e) {
    state.indicator = e.target.value;
    state.threshold = String(
      indicatorMeta().threshold == null ? '' : indicatorMeta().threshold,
    );
    $('threshold').value = state.threshold;
    showThreshold();
    go();
  });
  $('threshold').addEventListener('input', function (e) {
    state.threshold = e.target.value;
    go();
  });
  $('bar').addEventListener('input', function (e) {
    state.bar = e.target.value;
    go();
  });
  $('iso').addEventListener('input', function (e) {
    state.iso = e.target.value;
    go();
  });
  $('inputs').addEventListener('input', function (e) {
    var t = e.target;
    if (!t.dataset.key) return;
    var v = parseFloat(t.value);
    if (isNaN(v)) return;
    var o = (state.over[t.dataset.model] = state.over[t.dataset.model] || {});
    if (String(v) === t.dataset.def) delete o[t.dataset.key];
    else o[t.dataset.key] = v;
    t.parentNode.classList.toggle('changed', o.hasOwnProperty(t.dataset.key));
    go();
  });
  go();
})();
