/* The workspace: one scenario, drawn as a map with the numbers beside it.
 *
 * The state, the controls, the figures and the map are the shared parts; this decides what the map shows (a lens),
 * which country is being looked at, and the card that opens when one is picked. It does no arithmetic. */
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

  // ---- what the map shows
  // market: dollars a year to reach everyone. worth: the best multiple of the benchmark, outlined where it clears the bar.
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
  };
  var lens = new URLSearchParams(location.search).get('lens');
  if (!LENSES[lens]) lens = 'market';
  scn.ui.lens = lens;

  var last = null,
    countries = {},
    selected = null;

  function drawLensButtons() {
    put(
      $('lens'),
      Object.keys(LENSES).map(function (k) {
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
    tooltip: function (iso, name) {
      var c = countries[iso];
      var L = LENSES[lens];
      return h('div', { class: 'tip' }, [
        h('b', { text: name }),
        c
          ? usd(c.market_usd) +
            ' a year · ' +
            (c.best
              ? MV.mult(c.best.multiple) + ' of the benchmark'
              : 'no model figure')
          : 'no figure here',
      ]);
    },
    onSelect: choose,
  });
  fetch(D.shapes_url, { credentials: 'same-origin' })
    .then(function (r) {
      return r.json();
    })
    .then(map.load);

  function paint() {
    if (!last) return;
    var L = LENSES[lens];
    var isos = Object.keys(countries);
    var r = M.ramp(
      isos.map(function (i) {
        return L.value(countries[i]);
      }),
    );
    var out = {};
    isos.forEach(function (i) {
      var fill = r.color(L.value(countries[i]));
      if (fill)
        out[i] = { fill: fill, edge: lens === 'worth' && countries[i].clears };
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
        : [],
    );
  }

  // ---- the country being looked at
  function modelLink(model, overrides) {
    var q = new URLSearchParams({ model: model });
    Object.keys(overrides || {}).forEach(function (k) {
      q.set('i_' + k, overrides[k]);
    });
    return D.models_url + '?' + q.toString();
  }

  function drawDetail() {
    var el = $('detail');
    var c = selected && countries[selected];
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
      [h('h2', {}, [h('span', { text: c.country }), close])].concat(
        c.rows.map(function (o) {
          var x = o.row;
          return h('div', { class: 'mline' }, [
            h('b', { text: D.models[o.model].label }),
            h('span', {
              text: num(x.units) + (x.partial ? '+' : '') + ' ' + o.noun,
            }),
            h('span', {
              text: '$' + x.cost_per_person.toFixed(2) + ' each, a year',
            }),
            h('span', { text: usd(x.market_usd) + ' a year' }),
            h('span', {
              class: x.clears_bar ? 'ok' : 'lo',
              text: x.modelled
                ? MV.mult(x.multiple) +
                  ' of the benchmark' +
                  (x.clears_bar ? ': clears ' + last.bar + 'x' : '')
                : 'no model figure for this country',
            }),
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

  function choose(iso) {
    selected = iso === selected ? null : iso;
    map.select(selected);
    view.select(selected);
    drawDetail();
  }

  var view = MV.create(
    {
      tiles: $('tiles'),
      note: $('sel-note'),
      byModel: $('by-model'),
      rows: $('rows'),
      more: $('more'),
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
  drawLensButtons();
  scn.follow(
    D.api_url,
    function (r) {
      say('');
      last = r;
      countries = MV.byCountry(r);
      view.draw(r);
      paint();
      drawDetail();
    },
    say,
  );
})();
