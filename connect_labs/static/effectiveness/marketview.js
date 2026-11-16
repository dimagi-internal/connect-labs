/* What a scenario's market looks like in words and numbers: the tiles, a line per programme, a table by country.
 *
 * Market size draws these and the workspace draws them beside its map, so neither has a figure of its own: every
 * number is the API's (`effectiveness.sizing`). `byCountry` is the one reshaping the map and the detail card share. */
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

  // One entry per country across the chosen programmes: the market added together, the best multiple and which
  // programme gives it, and whether any programme clears the bar there.
  function byCountry(r) {
    var out = {};
    r.models.forEach(function (m) {
      m.rows.forEach(function (x) {
        var c =
          out[x.iso3] ||
          (out[x.iso3] = {
            iso: x.iso3,
            country: x.country,
            market_usd: null,
            best: null,
            clears: false,
            partial: false,
            rows: [],
          });
        c.rows.push({ model: m.model, noun: m.noun, row: x });
        if (x.market_usd != null)
          c.market_usd = (c.market_usd || 0) + x.market_usd;
        if (x.clears_bar) c.clears = true;
        if (x.partial) c.partial = true;
        if (
          x.multiple != null &&
          (c.best == null || x.multiple > c.best.multiple)
        )
          c.best = { model: m.model, multiple: x.multiple };
      });
    });
    return out;
  }

  function create(els, data, opts) {
    opts = opts || {};
    var showAll = false,
      last = null,
      selected = null;

    function tiles(r) {
      var all = 0,
        clear = 0,
        floor = false;
      r.models.forEach(function (m) {
        all += m.totals.market_usd;
        clear += m.totals.clearing_usd;
        floor = floor || m.totals.is_floor;
      });
      put(els.tiles, [
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
              ? 'of ' +
                r.selection.africa_total +
                ' African countries have data'
              : 'countries with ' +
                r.selection.regions +
                ' regions above ' +
                r.selection.threshold +
                ' on ' +
                r.selection.indicator_label,
          }),
        ]),
      ]);
    }

    function notes(r) {
      var floor = false,
        unm = 0;
      r.models.forEach(function (m) {
        floor = floor || m.totals.is_floor;
        unm += m.totals.unmodelled_countries;
      });
      var missing = r.selection.unsupported.length;
      put(els.note, [
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
    }

    function programmes(r) {
      put(
        els.byModel,
        r.models.map(function (m) {
          var t = m.totals;
          return h('div', { class: 'mline' }, [
            h('b', { text: data.models[m.model].label }),
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
    }

    function table(r) {
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
      els.more.hidden = rows.length <= 12;
      els.more.textContent = showAll
        ? 'Show fewer'
        : 'Show all ' + rows.length + ' rows';
      put(els.rows, [
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
            var tr = h(
              'tr',
              {
                class:
                  x.iso3 === selected ? 'sel' : opts.onSelect ? 'pick' : '',
                data: { iso: x.iso3 },
              },
              [
                h('td', { text: x.country }),
                h('td', { text: data.models[o.m].label.split(' ')[0] }),
                h('td', {
                  class: 'n',
                  text: num(x.units) + (x.partial ? '+' : ''),
                }),
                h('td', {
                  class: 'n',
                  text: '$' + x.cost_per_person.toFixed(2),
                }),
                h('td', { class: 'n', text: usd(x.market_usd) }),
                h('td', {
                  class: 'n ' + (x.clears_bar ? 'ok' : 'lo'),
                  text: x.modelled ? mult(x.multiple) : 'no model figure',
                }),
              ],
            );
            if (opts.onSelect)
              tr.addEventListener('click', function () {
                opts.onSelect(x.iso3);
              });
            return tr;
          }),
        ),
      ]);
    }

    function draw(r) {
      last = r;
      tiles(r);
      notes(r);
      programmes(r);
      table(r);
    }

    els.more.addEventListener('click', function () {
      showAll = !showAll;
      if (last) table(last);
    });

    return {
      draw: draw,
      // The country a page is looking at: its table rows are marked.
      select: function (iso) {
        selected = iso;
        if (last) table(last);
      },
    };
  }

  window.EffMarketView = { create: create, byCountry: byCountry, mult: mult };
})();
