/* What a scenario's market looks like in words and numbers: the tiles, the plan, a line per programme, a table of places.
 *
 * Market size draws these and the workspace draws them beside its map, so neither has a figure of its own: every
 * number is the API's (`effectiveness.sizing`). `byPlace` is the one reshaping the map and the detail card share. */
(function () {
  'use strict';
  var U = window.EffUI;
  var h = U.h,
    put = U.put,
    usd = U.usd,
    num = U.num;

  // One decimal always: 10.3x and 15.5x are different answers, and "10x" for both hides it.
  function mult(v) {
    return v == null ? '–' : v.toFixed(1) + 'x';
  }
  function pct(v, d) {
    return v == null ? '–' : (v * 100).toFixed(d == null ? 1 : d) + '%';
  }
  // What a reader needs to judge a place's baseline risk: whose figure it is, where it came from, and whether it is far off.
  function riskTitle(x) {
    if (x.risk_basis !== 'place')
      return "the model's own figure, not scaled to this place";
    return (
      "scaled to this place's newborn mortality of " +
      x.measure.toFixed(1) +
      (x.measure_country
        ? ' (its country: ' + x.measure_country.toFixed(1) + ')'
        : '') +
      (x.measure_how ? ', ' + x.measure_how : '') +
      (x.far_from_country
        ? ". More than twice (or under half) its country's: check the source."
        : '')
    );
  }
  function placeLabel(x) {
    return x.name === x.country ? x.name : x.name + ', ' + x.country;
  }

  // One entry per place across the chosen programmes: the market added together, the best multiple and which programme
  // gives it, what the plan puts there, and whether any programme clears the bar.
  function byPlace(r) {
    var out = {};
    r.models.forEach(function (m) {
      m.rows.forEach(function (x) {
        var c =
          out[x.key] ||
          (out[x.key] = {
            key: x.key,
            name: x.name,
            country: x.country,
            market_usd: null,
            planned: 0,
            best: null,
            clears: false,
            partial: false,
            rows: [],
          });
        c.rows.push({ model: m.model, noun: m.noun, row: x });
        if (x.market_usd != null)
          c.market_usd = (c.market_usd || 0) + x.market_usd;
        c.planned += x.planned || 0;
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
      planAll = false,
      last = null,
      selected = null,
      sortChosen = null;

    function noun(r) {
      return r.models.length === 1 ? r.models[0].noun : 'people';
    }

    function tiles(r) {
      var o = r.overall,
        floor = o.is_floor;
      put(els.tiles, [
        h('div', { class: 'tile' }, [
          h('div', {
            class: 'big',
            text: usd(o.cost) + (floor && !o.planned ? '+' : ''),
          }),
          h('div', {
            class: 'cap',
            text: o.planned
              ? 'a year, to reach ' + num(o.people) + ' ' + noun(r)
              : 'a year, to reach what it can in these places (' +
                num(o.people) +
                ' ' +
                noun(r) +
                ')',
          }),
        ]),
        h('div', { class: 'tile' }, [
          h('div', { class: 'big', text: num(o.deaths_averted) }),
          h('div', {
            class: 'cap',
            text:
              'deaths averted a year' +
              (o.cost_per_death
                ? ' · ' + usd(o.cost_per_death) + ' per death averted'
                : ''),
          }),
        ]),
        h('div', { class: 'tile' }, [
          h('div', {
            class:
              'big ' +
              (o.multiple != null && o.multiple >= r.bar ? 'ok' : 'lo'),
            text: mult(o.multiple),
          }),
          h('div', {
            class: 'cap',
            text:
              "of GiveWell's benchmark" +
              (r.models.length > 1 ? ', all together' : '') +
              ' · the bar is ' +
              r.bar +
              'x',
          }),
        ]),
        h('div', { class: 'tile' }, [
          h('div', { class: 'big', text: String(r.selection.places) }),
          h('div', {
            class: 'cap',
            text:
              (r.selection.grain === 'territory'
                ? 'territories in ' + r.selection.countries + ' countries'
                : r.selection.everywhere
                  ? 'of ' +
                    r.selection.africa_total +
                    ' African countries have data'
                  : 'countries with ' +
                    r.selection.regions +
                    ' regions above ' +
                    r.selection.threshold +
                    ' on ' +
                    r.selection.indicator_label) +
              (o.planned ? ', ' + o.clearing_places + ' clear the bar' : ''),
          }),
        ]),
      ]);
    }

    // What a reader must know to trust the figures: what is only a floor, what has no model figure, and the one thing
    // that makes the places differ (or does not).
    function notes(r) {
      var lines = [];
      var floor = false,
        unm = 0;
      r.models.forEach(function (m) {
        floor = floor || m.totals.is_floor;
        unm += m.totals.unmodelled_countries;
      });
      if (floor)
        lines.push(
          'A plus means some places have no count, so this is a floor.',
        );
      if (unm)
        lines.push(
          unm +
            (unm === 1 ? ' place has' : ' places have') +
            ' no model figures: counted in the market, never in the clearing part, never in a plan.',
        );
      r.models.forEach(function (m) {
        var label = data.models[m.model].label,
          pr = m.place_risk;
        if (m.flat) {
          lines.push(
            pr.applies && !pr.on
              ? label +
                  ': every place has the same multiple because scaling to local ' +
                  data.place_risk[m.model].label +
                  ' is off. Switch it on to see where it is worth more.'
              : pr.applies && pr.scaled === 0
                ? label +
                  ': no place has a ' +
                  data.place_risk[m.model].label +
                  " figure, so every place gets the model's own risk and the same multiple."
                : label +
                  ': this model has no place detail, so every place has the same multiple: where you work cannot change it.',
          );
        } else if (pr.applies && pr.on) {
          var without = m.rows.length - pr.scaled;
          lines.push(
            label +
              ": baseline risk is scaled to each place's " +
              data.place_risk[m.model].label +
              ' (a Labs reading; the model itself uses one figure everywhere).' +
              (without
                ? ' ' +
                  without +
                  " places have no figure and use the model's own risk."
                : ''),
          );
        }
      });
      r.models.forEach(function (m) {
        var label = data.models[m.model].label,
          pr = m.place_risk;
        if (pr.on && pr.local_inputs)
          lines.push(
            label +
              ': ' +
              pr.local_inputs +
              " territories use their own coverage and mortality figures where Labs has them, and the model's country figure where it does not.",
          );
        if (pr.far_from_country)
          lines.push(
            pr.far_from_country +
              (pr.far_from_country === 1
                ? ' place has a figure'
                : ' places have a figure') +
              ' more than twice (or under half) its country\'s: as often a survey artefact as a real difference, so the table marks them. Lower "trust" to see what changes without them.',
          );
      });
      var missing = r.selection.unsupported.length;
      put(
        els.note,
        lines
          .map(function (t) {
            return h('p', { class: 'muted note', text: t });
          })
          .concat(
            missing > 0
              ? [
                  h('details', { class: 'muted note' }, [
                    h('summary', {
                      css: { cursor: 'pointer' },
                      text:
                        missing +
                        (missing === 1 ? ' country has' : ' countries have') +
                        ' no data',
                    }),
                    r.selection.unsupported.join(', ') + '.',
                  ]),
                ]
              : [],
          ),
      );
    }

    // The plan: the best places first, each up to the most that can be reached there.
    function plan(r) {
      var withPlan = r.models.filter(function (m) {
        return m.plan;
      });
      els.plan.hidden = !withPlan.length;
      if (!withPlan.length) return;
      put(
        els.plan,
        withPlan.map(function (m) {
          var p = m.plan,
            t = p.totals,
            kids = [
              h('h2', {
                text:
                  'Plan: reach ' +
                  num(p.target) +
                  ' ' +
                  m.noun +
                  ' (' +
                  data.models[m.model].label +
                  ')',
              }),
            ];
          if (!p.feasible)
            kids.push(
              h('p', {
                class: 'err',
                text:
                  'These places can give at most ' +
                  num(p.capacity) +
                  ' ' +
                  m.noun +
                  ' at ' +
                  m.dials.reach +
                  '% of their cases: ' +
                  num(p.shortfall) +
                  ' short. Widen the places, or raise the most you can reach.',
              }),
            );
          var lim = p.limits || {},
            limitWords = [];
          if (lim.min_place)
            limitWords.push(
              'places that can give under ' +
                num(lim.min_place) +
                ' are left out (' +
                lim.left_out_small +
                ' of them)',
            );
          if (lim.max_conflict)
            limitWords.push(
              'places with more than ' +
                lim.max_conflict +
                ' reported conflict deaths per 100,000 a year are left out (' +
                lim.left_out_conflict +
                ' of them; a place with no figure stays in)',
            );
          if (lim.max_countries)
            limitWords.push(
              'at most ' +
                lim.max_countries +
                (lim.max_countries === 1 ? ' country' : ' countries'),
            );
          if (limitWords.length)
            kids.push(
              h('p', {
                class: 'muted',
                text: 'Limits: ' + limitWords.join('; ') + '.',
              }),
            );
          if (
            !p.feasible &&
            p.countries_needed &&
            p.limits &&
            p.limits.max_countries &&
            p.limits.max_countries < p.countries_needed
          )
            kids.push(
              h('p', {
                class: 'muted',
                text:
                  'It takes at least ' +
                  p.countries_needed +
                  ' countries to reach ' +
                  num(p.target) +
                  ' at all.',
              }),
            );
          kids.push(
            h('p', { class: 'planline' }, [
              h('b', {
                text:
                  num(t.people) +
                  ' reached in ' +
                  t.places +
                  (t.places === 1 ? ' place' : ' places'),
              }),
              ' for ' +
                usd(t.cost) +
                ' a year: ' +
                num(t.deaths_averted) +
                ' deaths averted, ' +
                usd(t.cost_per_death) +
                ' per death averted, ',
              h('b', {
                class: t.multiple >= r.bar ? 'ok' : 'lo',
                text: mult(t.multiple),
              }),
              " of GiveWell's benchmark.",
            ]),
          );
          if (p.even && p.gain) {
            var g = p.gain.deaths_averted_pct;
            kids.push(
              h('p', {
                class: 'muted',
                text:
                  g < 0.5
                    ? 'Every place is alike here, so choosing places changes nothing: spreading the same people evenly averts the same deaths.'
                    : 'Choosing the places is worth ' +
                      g.toFixed(0) +
                      '% more deaths averted for the same people: spreading ' +
                      num(p.target) +
                      ' evenly over all ' +
                      p.even.places +
                      ' places instead would avert ' +
                      num(p.even.deaths_averted) +
                      ' for ' +
                      usd(p.even.cost) +
                      ', at ' +
                      usd(p.even.cost_per_death) +
                      ' per death.',
              }),
            );
          }
          if (p.check) {
            var k = p.check;
            kids.push(
              h('p', {
                class:
                  k.kept_share != null && k.kept_share < 0.9 ? 'lo' : 'muted',
                text:
                  'If the place figures are only half to be believed, these places would still avert ' +
                  num(k.kept_deaths) +
                  ' deaths: ' +
                  pct(k.kept_share, 0) +
                  ' of the ' +
                  num(k.best_deaths) +
                  ' the best plan for those figures would. ' +
                  k.places_in_common +
                  ' of the ' +
                  k.places +
                  ' places are in both.' +
                  (k.kept_share != null && k.kept_share < 0.9
                    ? ' This plan leans on the figures being right.'
                    : ''),
              }),
            );
          }
          if (p.reliability) {
            var rel = p.reliability,
              dd = rel.deaths;
            var shaky = p.places.filter(function (q) {
              var pr = rel.p_in_plan[q.key];
              return pr != null && pr < 0.8;
            }).length;
            kids.push(
              h('p', {
                class: 'muted reliability',
                title:
                  "Each place's newborn mortality is redrawn within its published interval " +
                  rel.draws +
                  ' times and the plan rebuilt each time.',
                text:
                  'How far to trust it: with newborn mortality allowed to vary within its published intervals, the plan averts ' +
                  num(dd.p05) +
                  ' to ' +
                  num(dd.p95) +
                  ' deaths (90% range), ' +
                  (rel.overlap * 100).toFixed(0) +
                  '% of its people are in the best plan on a typical redraw, and ' +
                  shaky +
                  (shaky === 1 ? ' place is' : ' places are') +
                  ' chosen in fewer than 80% of redraws.',
              }),
            );
          }
          if (p.countries && p.countries.length > 1)
            kids.push(
              h(
                'details',
                { class: 'muted', open: p.countries.length <= 6 ? true : null },
                [
                  h('summary', {
                    text: 'By country (' + p.countries.length + ')',
                  }),
                  h(
                    'div',
                    { class: 'cl' },
                    p.countries
                      .slice(0, 12)
                      .map(function (c) {
                        return h('span', { class: 'cc' }, [
                          h('b', { text: c.country }),
                          ' ' +
                            num(c.reached) +
                            ' · ' +
                            c.places +
                            (c.places === 1 ? ' place' : ' places') +
                            ' · ' +
                            usd(c.cost) +
                            ' · ' +
                            mult(c.multiple),
                        ]);
                      })
                      .concat(
                        p.countries.length > 12
                          ? [
                              h('span', {
                                class: 'cc',
                                text:
                                  '+ ' + (p.countries.length - 12) + ' more',
                              }),
                            ]
                          : [],
                      ),
                  ),
                ],
              ),
            );
          if (opts.csvHref)
            kids.push(
              h('p', { css: { margin: '4px 0' } }, [
                h('a', {
                  class: 'link',
                  href: opts.csvHref(),
                  text: 'Download this plan (CSV)',
                }),
              ]),
            );
          var shown = planAll ? p.places : p.places.slice(0, 12);
          kids.push(
            h('div', { class: 'scroll' }, [
              h('table', {}, [
                h('thead', {}, [
                  h('tr', {}, [
                    h('th', { text: '#' }),
                    h('th', { text: 'Place' }),
                    h('th', { class: 'n', text: 'Reached' }),
                    h('th', { class: 'n', text: 'Of its cases' }),
                    h('th', { class: 'n', text: 'Cost' }),
                    h('th', { class: 'n', text: 'Deaths averted' }),
                    h('th', { class: 'n', text: 'Multiple' }),
                  ]),
                ]),
                h(
                  'tbody',
                  {},
                  shown.map(function (x, i) {
                    var tr = h(
                      'tr',
                      {
                        class:
                          x.key === selected
                            ? 'sel'
                            : opts.onSelect
                              ? 'pick'
                              : '',
                        data: { key: x.key },
                      },
                      [
                        h('td', { text: String(i + 1) }),
                        h('td', { text: placeLabel(x) }),
                        h('td', { class: 'n', text: num(x.reached) }),
                        h('td', { class: 'n', text: pct(x.share_of_cases, 0) }),
                        h('td', { class: 'n', text: usd(x.cost) }),
                        h('td', { class: 'n', text: num(x.deaths_averted) }),
                        h('td', {
                          class: 'n ' + (x.multiple >= r.bar ? 'ok' : 'lo'),
                          text: mult(x.multiple),
                        }),
                      ],
                    );
                    if (opts.onSelect)
                      tr.addEventListener('click', function () {
                        opts.onSelect(x.key);
                      });
                    return tr;
                  }),
                ),
              ]),
            ]),
          );
          if (p.places.length > 12) {
            var more = h('button', {
              type: 'button',
              class: 'btn',
              text: planAll
                ? 'Show fewer'
                : 'Show all ' + p.places.length + ' places',
            });
            more.addEventListener('click', function () {
              planAll = !planAll;
              if (last) plan(last);
            });
            kids.push(h('p', { css: { margin: '8px 0 0' } }, [more]));
          }
          return h('div', {}, kids);
        }),
      );
    }

    function programmes(r) {
      put(
        els.byModel,
        r.models.map(function (m) {
          var t = m.totals;
          return h('div', { class: 'mline' }, [
            h('b', { text: data.models[m.model].label }),
            h('span', {
              text:
                num(t.units) +
                ' ' +
                m.noun +
                ' (' +
                m.dials.reach +
                '% of their cases at most)',
            }),
            h('span', { text: usd(t.market_usd) + ' a year' }),
            h('span', { text: num(t.deaths_averted) + ' deaths averted' }),
            h('span', { text: mult(t.multiple) + ' blended' }),
            h('span', {
              class: t.clearing_countries ? 'ok' : 'lo',
              text:
                t.clearing_countries +
                ' of ' +
                t.places +
                ' places clear the bar (' +
                usd(t.clearing_usd) +
                ')',
            }),
          ]);
        }),
      );
    }

    function sorter(hasPlan, flat) {
      var options = [
        ['multiple', 'Highest multiple'],
        ['market', 'Biggest market'],
      ];
      if (hasPlan) options.unshift(['plan', 'In the plan first']);
      var keys = options.map(function (o) {
        return o[0];
      });
      var current =
        keys.indexOf(sortChosen) >= 0
          ? sortChosen
          : hasPlan
            ? 'plan'
            : flat
              ? 'market'
              : 'multiple';
      if (els.sort) {
        put(
          els.sort,
          options.map(function (o) {
            return h('option', {
              value: o[0],
              text: o[1],
              selected: o[0] === current ? true : null,
            });
          }),
        );
        els.sort.value = current;
      }
      return current;
    }

    function table(r) {
      var rows = [],
        hasPlan = r.models.some(function (m) {
          return m.plan;
        }),
        anyRisk = r.models.some(function (m) {
          return m.rows.some(function (x) {
            return x.risk != null;
          });
        }),
        anyConflict = r.models.some(function (m) {
          return m.rows.some(function (x) {
            return x.conflict_rate != null;
          });
        });
      r.models.forEach(function (m) {
        m.rows.forEach(function (x) {
          rows.push({ m: m.model, x: x });
        });
      });
      var how = sorter(
        hasPlan,
        r.models.every(function (m) {
          return m.flat;
        }),
      );
      var by = {
        plan: function (a) {
          return [a.x.planned || 0, a.x.multiple || 0];
        },
        multiple: function (a) {
          return [
            a.x.multiple == null ? -1 : a.x.multiple,
            a.x.market_usd || 0,
          ];
        },
        market: function (a) {
          return [a.x.market_usd || 0, a.x.multiple || 0];
        },
      }[how];
      rows.sort(function (a, b) {
        var u = by(a),
          v = by(b);
        return v[0] - u[0] || v[1] - u[1];
      });
      var shown = showAll ? rows : rows.slice(0, 12);
      els.more.hidden = rows.length <= 12;
      els.more.textContent = showAll
        ? 'Show fewer'
        : 'Show all ' + rows.length + ' rows';
      // The programme is only worth a column when there is more than one.
      var many = r.models.length > 1;
      var head = (many ? ['Place', 'Programme'] : ['Place']).concat(
        hasPlan ? ['Planned'] : [],
        ['People', 'A year, each', 'Market', 'Deaths a year'],
        anyRisk ? ['Baseline risk'] : [],
        anyConflict ? ['Conflict'] : [],
        ['Multiple'],
      );
      put(els.rows, [
        h('thead', {}, [
          h(
            'tr',
            {},
            head.map(function (t, i) {
              return h('th', { class: i < (many ? 2 : 1) ? '' : 'n', text: t });
            }),
          ),
        ]),
        h(
          'tbody',
          {},
          shown.map(function (o) {
            var x = o.x;
            var cells = [h('td', { text: placeLabel(x) })];
            if (many)
              cells.push(
                h('td', { text: data.models[o.m].label.split(' ')[0] }),
              );
            if (hasPlan)
              cells.push(
                h('td', { class: 'n', text: x.planned ? num(x.planned) : '–' }),
              );
            cells.push(
              h('td', {
                class: 'n',
                text: num(x.units) + (x.partial ? '+' : ''),
              }),
              h('td', { class: 'n', text: '$' + x.cost_per_person.toFixed(2) }),
              h('td', { class: 'n', text: usd(x.market_usd) }),
              h('td', { class: 'n', text: num(x.deaths_averted) }),
            );
            if (anyRisk)
              cells.push(
                h('td', {
                  class: 'n' + (x.far_from_country ? ' far' : ''),
                  title: riskTitle(x),
                  text:
                    x.risk == null
                      ? '–'
                      : pct(x.risk) +
                        (x.risk_basis === 'place' ? '' : '*') +
                        (x.far_from_country ? ' ⚠' : ''),
                }),
              );
            if (anyConflict)
              cells.push(
                h('td', {
                  class: 'n',
                  title:
                    'Reported conflict deaths a year per 100,000 people (UCDP, latest three years). Reported, not all: a low figure where few report is weaker evidence.',
                  text:
                    x.conflict_rate == null ? '–' : x.conflict_rate.toFixed(1),
                }),
              );
            cells.push(
              h('td', {
                class: 'n ' + (x.clears_bar ? 'ok' : 'lo'),
                title:
                  x.multiple_low != null
                    ? '90% range from its newborn mortality interval: ' +
                      mult(x.multiple_low) +
                      ' to ' +
                      mult(x.multiple_high) +
                      (x.risk_raw != null
                        ? ' (the published figure, ' +
                          x.risk_raw.toFixed(1) +
                          " per 1,000, was pulled toward the country's)"
                        : '')
                    : null,
                text: x.modelled ? mult(x.multiple) : 'no model figure',
              }),
            );
            var tr = h(
              'tr',
              {
                class:
                  (x.key === selected ? 'sel ' : opts.onSelect ? 'pick ' : '') +
                  (x.planned ? 'inplan' : ''),
                data: { key: x.key },
              },
              cells,
            );
            if (opts.onSelect)
              tr.addEventListener('click', function () {
                opts.onSelect(x.key);
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
      plan(r);
      programmes(r);
      table(r);
    }

    els.more.addEventListener('click', function () {
      showAll = !showAll;
      if (last) table(last);
    });
    if (els.sort)
      els.sort.addEventListener('change', function () {
        sortChosen = els.sort.value;
        if (last) table(last);
      });

    return {
      draw: draw,
      // The place a page is looking at: its table rows are marked.
      select: function (key) {
        selected = key;
        if (last) {
          plan(last);
          table(last);
        }
      },
    };
  }

  window.EffMarketView = {
    create: create,
    byPlace: byPlace,
    mult: mult,
    pct: pct,
    placeLabel: placeLabel,
    riskTitle: riskTitle,
  };
})();
