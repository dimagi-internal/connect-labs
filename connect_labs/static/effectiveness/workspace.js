/* The workspace: one scenario, drawn as a map with the numbers beside it.
 *
 * The state, the controls, the figures and the map are the shared parts; this decides what the map shows (a lens),
 * which place is being looked at, and the card that opens when one is picked. It does no arithmetic. */
(function () {
  'use strict';
  var U = window.EffUI,
    h = U.h,
    put = U.put,
    usd = U.usd,
    num = U.num;
  var MV = window.EffMarketView,
    M = window.EffMap;
  var D = JSON.parse(document.getElementById('scenario-data').textContent);
  var $ = function (id) {
    return document.getElementById(id);
  };

  var scn = window.EffScenario.create(D);
  window.EffScenarios.mount($('scenarios'), scn, D);
  window.EffDials.mount($('controls'), scn, D);

  // The grain is in the address bar always: a link with none means the page's own start (territories here), so a country
  // grain has to say so.
  function syncGrain() {
    scn.ui.grain = scn.state.grain;
  }
  syncGrain();
  scn.onChange(syncGrain);

  // ---- what the map shows
  // market: dollars a year to reach everyone it can. worth: the best multiple of the benchmark, outlined where it clears
  // the bar. plan: only the places the plan reaches, painted by their multiple.
  var LENSES = {
    market: {
      label: 'Market',
      value: function (c) {
        return c.market_usd;
      },
      fmt: usd,
    },
    worth: {
      label: 'Worth it',
      value: function (c) {
        return c.best && c.best.multiple;
      },
      fmt: function (v) {
        return v == null ? '–' : MV.mult(v);
      },
    },
    plan: {
      label: 'Plan',
      value: function (c) {
        return c.planned > 0 && c.best ? c.best.multiple : null;
      },
      fmt: function (v) {
        return v == null ? '–' : MV.mult(v);
      },
    },
  };
  var lens = new URLSearchParams(location.search).get('lens');
  if (!LENSES[lens]) lens = D.state.target ? 'plan' : 'market';
  scn.ui.lens = lens;

  var last = null,
    places = {},
    selected = null;

  function hasPlan() {
    return (
      !!last &&
      last.models.some(function (m) {
        return m.plan;
      })
    );
  }

  function drawLensButtons() {
    var keys = Object.keys(LENSES).filter(function (k) {
      return k !== 'plan' || hasPlan();
    });
    if (keys.indexOf(lens) < 0) lens = keys[0];
    put(
      $('lens'),
      keys.map(function (k) {
        var b = h('button', {
          type: 'button',
          class: 'btn' + (k === lens ? ' on' : ''),
          text: LENSES[k].label,
        });
        b.addEventListener('click', function () {
          lens = k;
          scn.setUi('lens', k);
          drawLensButtons();
          paint();
        });
        return b;
      }),
    );
  }

  // ---- the map
  var map = M.create($('map'), {
    tooltip: function (key, name, country) {
      var c = places[key];
      var label =
        name && country && name !== country ? name + ', ' + country : name;
      return h('div', { class: 'tip' }, [
        h('b', { text: label }),
        c
          ? usd(c.market_usd) +
            ' a year · ' +
            (c.best
              ? MV.mult(c.best.multiple) + ' of the benchmark'
              : 'no model figure') +
            (c.planned ? ' · in the plan: ' + num(c.planned) : '')
          : 'no figure here',
      ]);
    },
    onSelect: choose,
  });

  // The outlines follow the grain: countries, or states and provinces.
  var shapesFor = {},
    shapesShown = null;
  function showShapes(grain) {
    if (shapesShown === grain) return;
    shapesShown = grain;
    (shapesFor[grain] =
      shapesFor[grain] ||
      fetch(D.shapes_url + '?grain=' + grain, {
        credentials: 'same-origin',
      }).then(function (r) {
        return r.json();
      })).then(function (g) {
      if (shapesShown === grain) {
        map.load(g);
        paint();
      }
    });
  }
  showShapes(scn.state.grain);

  function paint() {
    if (!last) return;
    var L = LENSES[lens];
    var keys = Object.keys(places);
    var r = M.ramp(
      keys.map(function (k) {
        return L.value(places[k]);
      }),
    );
    var out = {};
    keys.forEach(function (k) {
      var fill = r.color(L.value(places[k]));
      if (fill)
        out[k] = {
          fill: fill,
          edge:
            (lens === 'worth' && places[k].clears) ||
            (lens === 'plan' && places[k].planned > 0),
        };
    });
    map.paint(out);
    M.legend(
      $('legend'),
      r,
      L.fmt,
      lens === 'worth'
        ? [
            h('span', { class: 'lg' }, [
              h('i', { class: 'out' }),
              'clears ' + last.bar + 'x',
            ]),
          ]
        : lens === 'plan'
          ? [
              h('span', { class: 'lg' }, [
                h('i', { class: 'out' }),
                'in the plan; the rest are not reached',
              ]),
            ]
          : [],
    );
  }

  // ---- the place being looked at
  function modelLink(model, overrides) {
    var q = new URLSearchParams({ model: model });
    Object.keys(overrides || {}).forEach(function (k) {
      q.set('i_' + k, overrides[k]);
    });
    return D.models_url + '?' + q.toString();
  }

  function drawDetail() {
    var el = $('detail');
    var c = selected && places[selected];
    el.hidden = !c;
    if (!c) return;
    var close = h('button', { type: 'button', class: 'btn', text: 'Close' });
    close.addEventListener('click', function () {
      choose(selected);
    });
    var overrides = {};
    last.models.forEach(function (m) {
      overrides[m.model] = m.overrides;
    });
    put(
      el,
      [h('h2', {}, [h('span', { text: MV.placeLabel(c) }), close])].concat(
        c.rows.map(function (o) {
          var x = o.row;
          return h('div', { class: 'mline' }, [
            h('b', { text: D.models[o.model].label }),
            h('span', {
              text:
                num(x.units) +
                (x.partial ? '+' : '') +
                ' ' +
                o.noun +
                ' it can reach (of ' +
                num(x.units_all) +
                ')',
            }),
            h('span', {
              text:
                '$' +
                x.cost_per_person.toFixed(2) +
                ' each, ' +
                usd(x.market_usd) +
                ' a year',
            }),
            h('span', {
              text:
                num(x.deaths_averted) +
                ' deaths averted' +
                (x.cost_per_death
                  ? ', ' + usd(x.cost_per_death) + ' each'
                  : ''),
            }),
            x.risk != null
              ? h('span', {
                  text:
                    'baseline risk ' +
                    MV.pct(x.risk) +
                    (x.risk_basis === 'place'
                      ? ', scaled to this place'
                      : ", the model's own"),
                })
              : null,
            h('span', {
              class: x.clears_bar ? 'ok' : 'lo',
              text: x.modelled
                ? MV.mult(x.multiple) +
                  ' of the benchmark' +
                  (x.clears_bar ? ': clears ' + last.bar + 'x' : '')
                : 'no model figure for this place',
            }),
            x.planned
              ? h('span', {
                  class: 'ok',
                  text: 'in the plan: ' + num(x.planned) + ' reached',
                })
              : null,
            h('a', {
              class: 'link',
              href: modelLink(o.model, overrides[o.model]),
              text: 'Open in CEA models',
            }),
          ]);
        }),
      ),
    );
  }

  function choose(key) {
    selected = key === selected ? null : key;
    map.select(selected);
    view.select(selected);
    drawDetail();
  }

  var view = MV.create(
    {
      tiles: $('tiles'),
      note: $('sel-note'),
      plan: $('plan'),
      byModel: $('by-model'),
      rows: $('rows'),
      more: $('more'),
      sort: $('sort'),
    },
    D,
    { onSelect: choose },
  );

  function say(text) {
    $('error').hidden = !text;
    $('error').textContent = text || '';
  }
  if (D.error) {
    $('notice').hidden = false;
    $('notice').textContent = D.error + ' Showing the models as published.';
  }
  scn.onChange(function (_, kind) {
    if (kind === 'grain') {
      selected = null;
      showShapes(scn.state.grain);
    }
  });
  drawLensButtons();
  scn.follow(
    D.api_url,
    function (r) {
      say('');
      last = r;
      places = MV.byPlace(r);
      if (selected && !places[selected]) selected = null;
      view.draw(r);
      drawLensButtons();
      paint();
      drawDetail();
    },
    say,
  );
})();
