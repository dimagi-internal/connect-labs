(function () {
  'use strict';
  var U = window.EffUI;
  var h = U.h,
    put = U.put,
    safeUrl = U.safeUrl,
    fmt = U.fmt,
    mult = U.mult;
  var D = JSON.parse(document.getElementById('explore-data').textContent);
  var overrides = Object.assign({}, D.overrides);
  var byKey = {};
  D.inputs.forEach(function (i) {
    byKey[i.key] = i;
  });
  var $ = function (id) {
    return document.getElementById(id);
  };

  // Everything is built as DOM nodes with text set through textContent: nothing from the model data is ever
  // parsed as HTML, so there is nothing to escape.

  // ---- model chips, source note
  put(
    $('model-chips'),
    Object.keys(D.models).map(function (k) {
      var m = D.models[k];
      return h(
        'a',
        {
          class: 'chip' + (k === D.model ? ' on' : ''),
          href: '?model=' + encodeURIComponent(k),
        },
        [m.label],
      );
    }),
  );
  var M = D.models[D.model];
  $('h-unit').textContent = M.unit;
  put($('source-note'), [
    h('a', {
      href: safeUrl(M.sheet),
      target: '_blank',
      rel: 'noopener',
      text: 'Live Sheet',
    }),
    ' · v' +
      String(D.spec.version || '').replace(/^v/i, '') +
      ' · ' +
      String(D.spec.commit || '').slice(0, 7),
  ]);

  // ---- inputs form: one row per input, with its sensitivity switch and bar
  var included = {};
  D.sensitivity.rows.forEach(function (r) {
    included[r.key] = r.included;
  });
  var defaultOn = D.sensitivity.rows
    .map(function (r) {
      return r.key;
    })
    .sort()
    .join(',');
  function currentValue(i) {
    return overrides.hasOwnProperty(i.key) ? overrides[i.key] : i.value;
  }
  function caseButton(i, which) {
    return h('button', {
      type: 'button',
      data: { set: i.key, v: String(i[which]) },
      text: fmt(i[which], i.format),
    });
  }
  function inputRow(i) {
    var isSwitch = i.unit === 'switch';
    var ranged = i.low != null && i.high != null && !isSwitch;
    var field = isSwitch
      ? h('input', {
          type: 'checkbox',
          data: { key: i.key },
          checked: currentValue(i) ? true : null,
        })
      : h('input', {
          type: 'number',
          step: 'any',
          data: { key: i.key },
          value: String(currentValue(i)),
        });
    var cases = ranged
      ? h('span', { class: 'cases' }, [
          'low ',
          caseButton(i, 'low'),
          ' high ',
          caseButton(i, 'high'),
        ])
      : null;
    var more = null;
    if (i.meaning || i.where || i.range) {
      var paras = [];
      if (i.meaning) paras.push(h('p', { text: i.meaning }));
      if (i.where)
        paras.push(h('p', {}, [h('b', { text: 'Source: ' }), i.where]));
      if (i.range)
        paras.push(h('p', {}, [h('b', { text: 'Range: ' }), i.range]));
      more = h('details', {}, [h('summary', { text: 'More' })].concat(paras));
    }
    var sw = ranged
      ? h('input', {
          type: 'checkbox',
          class: 'sens-on',
          data: { sens: i.key },
          title: 'Include in the sensitivity',
          'aria-label': 'Include ' + i.label + ' in the sensitivity',
          checked: included[i.key] ? true : null,
        })
      : h('span', { css: { width: '13px', flex: 'none' } });
    return h(
      'div',
      {
        class: 'inp' + (ranged && included[i.key] ? '' : ' off'),
        data: { row: i.key },
      },
      [
        h('div', { class: 'top' }, [sw, h('label', { text: i.label })]),
        h('div', { class: 'row', css: { marginLeft: '24px' } }, [
          field,
          h('span', { class: 'unit', text: i.unit }),
          h('span', { class: 'def', text: 'model: ' + fmt(i.value, i.format) }),
          cases,
        ]),
        ranged ? h('div', { class: 'mini', data: { mini: i.key } }) : null,
        h('div', { css: { marginLeft: '24px' } }, [more]),
      ],
    );
  }
  put(
    $('inputs'),
    D.blocks
      .map(function (b) {
        var items = D.inputs.filter(function (i) {
          return i.group === b.key;
        });
        if (!items.length) return null;
        return h(
          'details',
          { class: 'grp', open: b.key !== 'other' ? true : null },
          [
            h('summary', {}, [
              h('span', { class: 'gt', text: b.title }),
              h('span', { class: 'gh', text: b.hint }),
              h('span', { class: 'gs', data: { gs: b.key } }),
            ]),
          ].concat(items.map(inputRow)),
        );
      })
      .filter(Boolean),
  );

  function syncRows() {
    D.inputs.forEach(function (i) {
      var row = document.querySelector('[data-row="' + i.key + '"]');
      if (!row) return;
      var inp = row.querySelector('input[data-key]');
      var v = currentValue(i);
      if (inp.type === 'checkbox') inp.checked = !!v;
      else if (document.activeElement !== inp) inp.value = v;
      row.classList.toggle('changed', overrides.hasOwnProperty(i.key));
    });
  }

  // ---- calls
  function includedKeys() {
    return Object.keys(included).filter(function (k) {
      return included[k];
    });
  }
  function query(extra) {
    var p = new URLSearchParams({ model: D.model });
    Object.keys(overrides).forEach(function (k) {
      p.set('i_' + k, overrides[k]);
    });
    Object.keys(extra || {}).forEach(function (k) {
      p.set(k, extra[k]);
    });
    return p;
  }
  function writeUrl() {
    var p = query();
    var on = includedKeys().sort().join(',');
    if (on !== defaultOn) p.set('s', on);
    try {
      history.replaceState(null, '', location.pathname + '?' + p.toString());
    } catch (e) {
      /* sandboxed frame */
    }
  }
  function showError(msg) {
    var e = $('error');
    e.hidden = !msg;
    e.textContent = msg || '';
  }
  function call(extra) {
    var q = query(extra);
    q.set('s', includedKeys().join(','));
    return fetch(D.run_url + '?' + q.toString(), {
      credentials: 'same-origin',
    }).then(function (r) {
      return r.json().then(function (j) {
        return { ok: r.ok, j: j };
      });
    });
  }
  var timer = null,
    seq = 0;
  function refresh() {
    syncRows();
    writeUrl();
    clearTimeout(timer);
    timer = setTimeout(function () {
      var mine = ++seq;
      call()
        .then(function (res) {
          if (mine !== seq) return;
          if (!res.ok) {
            showError(res.j.error || 'That did not run.');
            return;
          }
          showError('');
          draw(res.j.run, res.j.sensitivity);
        })
        .catch(function () {
          showError('The server did not respond. Try again.');
        });
    }, 200);
  }

  // ---- drawing
  function drawHeadline(run) {
    var x = run.headline;
    $('h-x').textContent = mult(x.multiple) + 'x';
    $('h-price').textContent = '$' + x.price_per_person.toFixed(2);
    $('h-deaths').textContent = x.deaths_per_100k.toFixed(1);
    $('h-bar').textContent = x.bar;
    var pill = $('h-pill');
    pill.textContent = x.clears_bar ? 'Clears the bar' : 'Below the bar';
    pill.className = 'pill ' + (x.clears_bar ? 'ok' : 'lo');
  }
  function scale(vals) {
    var max = Math.max.apply(null, vals) * 1.05,
      min = Math.min(0, Math.min.apply(null, vals));
    return {
      pos: function (v) {
        return (((v - min) / (max - min)) * 100).toFixed(2) + '%';
      },
      width: function (a, b) {
        return ((Math.abs(b - a) / (max - min)) * 100).toFixed(2) + '%';
      },
    };
  }
  function track(sc, base, lo, hi) {
    var seg = function (cls, v) {
      return h('div', {
        class: 'seg ' + cls,
        css: { left: sc.pos(Math.min(v, base)), width: sc.width(v, base) },
      });
    };
    return h('div', { class: 'track' }, [
      seg('lo', lo),
      seg('hi', hi),
      h('div', { class: 'barline', css: { left: sc.pos(D.bar) } }),
      h('div', { class: 'mark', css: { left: sc.pos(base) } }),
    ]);
  }
  function drawSensitivity(sn) {
    var c = sn.combined,
      vals = [sn.base, D.bar];
    sn.rows.forEach(function (r) {
      if (r.included) vals.push(r.multiple_at_low, r.multiple_at_high);
    });
    if (c) vals.push(c.worst, c.best);
    var sc = scale(vals);
    if (!c) {
      put($('overall'), [
        h('div', {
          class: 'lab',
          text: 'No input is switched on for sensitivity.',
        }),
      ]);
    } else {
      put($('overall'), [
        h('div', { class: 'lab' }, [
          h('b', { text: 'All ' + c.n + ' switched-on inputs together' }),
        ]),
        track(sc, sn.base, c.worst, c.best),
        h(
          'div',
          {
            class: 'nums',
            css: {
              display: 'flex',
              justifyContent: 'space-between',
              flexWrap: 'wrap',
              gap: '2px 12px',
              fontSize: '12px',
              color: '#6b7280',
              marginTop: '2px',
            },
          },
          [
            h('span', { text: mult(c.worst) + 'x if all go the worst way' }),
            h('span', { text: mult(c.best) + 'x if all go the best way' }),
          ],
        ),
      ]);
    }
    sn.rows.forEach(function (r) {
      var mini = document.querySelector('[data-mini="' + r.key + '"]'),
        row = document.querySelector('[data-row="' + r.key + '"]');
      if (!mini || !row) return;
      row.classList.toggle('off', !r.included);
      var cb = row.querySelector('input[data-sens]');
      if (cb) cb.checked = !!r.included;
      if (!r.included) {
        put(mini, []);
        return;
      }
      put(mini, [
        track(sc, sn.base, r.multiple_at_low, r.multiple_at_high),
        h(
          'div',
          {
            class: 'nums',
            css: { display: 'flex', justifyContent: 'space-between' },
          },
          [
            h('span', {
              text:
                fmt(r.low, r.format) + ' → ' + mult(r.multiple_at_low) + 'x',
            }),
            h('span', {
              text:
                fmt(r.high, r.format) + ' → ' + mult(r.multiple_at_high) + 'x',
            }),
          ],
        ),
      ]);
    });
    document.querySelectorAll('[data-gs]').forEach(function (el) {
      var g = sn.groups[el.dataset.gs];
      el.textContent = g ? mult(g.worst) + 'x to ' + mult(g.best) + 'x' : '';
    });
  }
  function drawChain(run) {
    var step = null,
      body = [];
    run.chain.forEach(function (r) {
      if (r.step !== step) {
        step = r.step;
        body.push(
          h('tr', { class: 'step' }, [
            h('td', { colspan: '2', text: step || '' }),
          ]),
        );
      }
      body.push(
        h('tr', {}, [
          h('td', { text: r.label }),
          h('td', { class: 'n', text: fmt(r.value, r.format) }),
        ]),
      );
    });
    put($('chain'), [
      h('thead', {}, [
        h('tr', {}, [
          h('th', { text: 'Row' }),
          h('th', { class: 'n', text: 'Value' }),
        ]),
      ]),
      h('tbody', {}, body),
    ]);
  }
  function draw(run, sn) {
    drawHeadline(run);
    drawSensitivity(sn);
    drawChain(run);
  }
  draw(D.run, D.sensitivity);

  // ---- scenarios and countries
  function head(cols) {
    return h('thead', {}, [
      h(
        'tr',
        {},
        cols.map(function (c) {
          return h('th', { class: c[1] || '', text: c[0] });
        }),
      ),
    ]);
  }
  var tableHead = [
    ['Scenario'],
    ['Multiple', 'n'],
    ['Price', 'n'],
    ['Deaths / 100k', 'n'],
    [''],
  ];
  put($('scenarios'), [
    head(tableHead),
    h(
      'tbody',
      {},
      D.scenarios.map(function (s, n) {
        return h('tr', {}, [
          h('td', { text: s.name }),
          h('td', { class: 'n', text: mult(s.multiple) + 'x' }),
          h('td', { class: 'n', text: '$' + s.price_per_person.toFixed(2) }),
          h('td', { class: 'n', text: s.deaths_per_100k.toFixed(1) }),
          h('td', {}, [
            h('button', {
              type: 'button',
              class: 'btn',
              data: { scn: String(n) },
              text: 'Run',
            }),
          ]),
        ]);
      }),
    ),
  ]);
  if (!D.places.length) $('places-card').hidden = true;
  function drawPlaces() {
    var q = ($('place-q').value || '').toLowerCase();
    var rows = D.places
      .map(function (p, n) {
        return [p, n];
      })
      .filter(function (pn) {
        return !q || pn[0].name.toLowerCase().indexOf(q) >= 0;
      });
    put($('places'), [
      head([
        ['Country'],
        ['Multiple', 'n'],
        ['Price', 'n'],
        ['Deaths / 100k', 'n'],
        [''],
      ]),
      h(
        'tbody',
        {},
        rows.map(function (pn) {
          var p = pn[0];
          return h('tr', {}, [
            h('td', { text: p.name }),
            h('td', { class: 'n', text: mult(p.x) + 'x' }),
            h('td', { class: 'n', text: '$' + p.price.toFixed(2) }),
            h('td', { class: 'n', text: p.deaths_per_100k.toFixed(1) }),
            h('td', {}, [
              h('button', {
                type: 'button',
                class: 'btn',
                data: { place: String(pn[1]) },
                text: 'Use',
              }),
            ]),
          ]);
        }),
      ),
    ]);
  }
  drawPlaces();
  $('place-q').addEventListener('input', drawPlaces);

  // ---- presets
  var presetOpts = [h('option', { value: '', text: "The model's own inputs" })];
  D.scenarios.forEach(function (s, n) {
    presetOpts.push(
      h('option', { value: 's' + n, text: 'Scenario: ' + s.name }),
    );
  });
  D.places.forEach(function (p, n) {
    presetOpts.push(
      h('option', { value: 'p' + n, text: 'Country: ' + p.name }),
    );
  });
  put($('preset'), presetOpts);
  function load(sets) {
    overrides = {};
    Object.keys(sets || {}).forEach(function (k) {
      if (byKey[k] && sets[k] !== byKey[k].value) overrides[k] = sets[k];
    });
    refresh();
  }
  $('preset').addEventListener('change', function (e) {
    var v = e.target.value;
    if (!v) load({});
    else if (v[0] === 's') load(D.scenarios[+v.slice(1)].sets);
    else load(D.places[+v.slice(1)].inputs);
  });
  $('reset').addEventListener('click', function () {
    $('preset').value = '';
    load({});
  });

  // ---- input changes, low/high buttons, run buttons
  function setOverride(key, v) {
    if (v === '' || v == null || isNaN(v) || !byKey[key]) return;
    if (v === byKey[key].value) delete overrides[key];
    else overrides[key] = v;
    refresh();
  }
  $('inputs').addEventListener('change', function (e) {
    var el = e.target;
    if (!el.dataset.sens) return;
    included[el.dataset.sens] = el.checked;
    refresh();
  });
  function setAll(on) {
    Object.keys(included).forEach(function (k) {
      included[k] = on;
    });
    refresh();
  }
  $('sens-all').addEventListener('click', function () {
    setAll(true);
  });
  $('sens-none').addEventListener('click', function () {
    setAll(false);
  });
  $('inputs').addEventListener('input', function (e) {
    var el = e.target;
    if (!el.dataset.key) return;
    setOverride(
      el.dataset.key,
      el.type === 'checkbox' ? (el.checked ? 1 : 0) : parseFloat(el.value),
    );
  });
  document.addEventListener('click', function (e) {
    var t = e.target;
    if (t.dataset && t.dataset.set)
      setOverride(t.dataset.set, parseFloat(t.dataset.v));
    if (t.dataset && t.dataset.scn) {
      $('preset').value = 's' + t.dataset.scn;
      load(D.scenarios[+t.dataset.scn].sets);
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }
    if (t.dataset && t.dataset.place) {
      $('preset').value = 'p' + t.dataset.place;
      load(D.places[+t.dataset.place].inputs);
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }
  });

  syncRows();
})();
