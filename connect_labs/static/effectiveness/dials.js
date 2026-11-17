/* The scenario's controls: where, which programmes, how many people, the bar, three dials per programme, and every
 * assumption behind them.
 *
 * One panel, mounted by Market size and by the workspace, so the controls are the same and a change on one page is the
 * same change on the next. It changes the scenario (`EffScenario`) and draws nothing else. */
(function () {
  'use strict';
  var U = window.EffUI;
  var h = U.h,
    put = U.put;

  function field(label, control, hint) {
    return h('label', { class: 'f' }, [
      h('span', { text: label }),
      control,
      hint ? h('small', { class: 'hint', text: hint }) : null,
    ]);
  }

  function number(value, on, attrs) {
    var el = h(
      'input',
      Object.assign(
        { type: 'number', step: 'any', value: String(value) },
        attrs,
      ),
    );
    el.addEventListener('input', function () {
      var v = parseFloat(el.value);
      if (!isNaN(v)) on(v, el);
    });
    return el;
  }

  function mount(root, scn, data) {
    var s = scn.state;
    var summary = h('p', { class: 'muted', css: { margin: '8px 0 0' } });
    var places = h('div');
    var grainBox = h('div', { class: 'seg' });
    var chips = h('div', { class: 'models' });
    var targetBox = h('div');
    var riskBox = h('div');
    var dialsBox = h('div');
    var inputsBox = h('div');
    var EVERYWHERE = window.EffScenario.EVERYWHERE;

    // ---- where
    function drawPlaces() {
      var ind = h(
        'select',
        {},
        data.indicators.map(function (i) {
          return h('option', {
            value: i.key,
            text: i.label,
            selected: i.key === s.indicator ? true : null,
          });
        }),
      );
      ind.value = s.indicator;
      ind.addEventListener('change', function () {
        scn.setIndicator(ind.value);
        thr.hidden = s.indicator === EVERYWHERE;
        thrInput.value = s.threshold == null ? '' : String(s.threshold);
      });
      var thrInput = number(
        s.threshold == null ? '' : s.threshold,
        function (v) {
          scn.set({ threshold: v });
        },
      );
      var thr = field('Above', thrInput);
      thr.hidden = s.indicator === EVERYWHERE;
      var iso = h('input', {
        type: 'text',
        value: s.iso,
        placeholder: 'NGA, NER, TCD',
      });
      iso.addEventListener('input', function () {
        scn.set({ iso: iso.value });
      });
      put(places, [
        field('Where', ind),
        thr,
        field('Countries (ISO codes, blank for all Africa)', iso),
      ]);
    }

    // Countries are what the models hold figures for; territories (states and provinces) are where the best places show.
    var GRAIN_LABEL = { country: 'Countries', territory: 'Territories' };
    function drawGrain() {
      put(
        grainBox,
        data.grains.map(function (g) {
          var b = h('button', {
            type: 'button',
            class: 'btn' + (g === s.grain ? ' on' : ''),
            text: GRAIN_LABEL[g],
          });
          b.addEventListener('click', function () {
            if (s.grain !== g) {
              scn.setGrain(g);
              drawGrain();
            }
          });
          return b;
        }),
      );
    }

    // ---- programmes, how many people, and each one's dials
    function drawChips() {
      put(
        chips,
        Object.keys(data.models).map(function (k) {
          var on = s.models.indexOf(k) >= 0;
          var box = h('input', {
            type: 'checkbox',
            checked: on ? true : null,
          });
          box.addEventListener('change', function () {
            scn.toggleModel(k, box.checked);
            // The last programme cannot be switched off: put the box back.
            box.checked = s.models.indexOf(k) >= 0;
          });
          return h('label', { class: 'mchip' + (on ? ' on' : '') }, [
            box,
            h('span', {}, [data.models[k].label]),
          ]);
        }),
      );
    }

    function drawTarget() {
      var noun = data.models[s.models[0]].noun;
      var input = number(
        s.target == null ? '' : s.target,
        function (v) {
          scn.setTarget(v);
        },
        { min: '0', placeholder: 'everyone it can reach' },
      );
      input.addEventListener('input', function () {
        if (input.value.trim() === '') scn.setTarget(null);
      });
      put(targetBox, [
        field(
          'People to reach' +
            (s.models.length > 1 ? ' (each programme, in its own unit)' : ''),
          input,
          noun +
            '. Leave blank for everyone it can reach; give a number and the best places are filled first.',
        ),
      ]);
    }

    // Only a programme whose baseline risk can be scaled to the place has this switch (Kangaroo Care).
    function drawRisk() {
      var scaled = s.models.filter(function (m) {
        return data.place_risk[m];
      });
      if (!scaled.length) return put(riskBox, []);
      var r = data.place_risk[scaled[0]];
      var box = h('input', {
        type: 'checkbox',
        checked: s.place_risk ? true : null,
      });
      box.addEventListener('change', function () {
        scn.setPlaceRisk(box.checked);
      });
      put(riskBox, [
        h('label', { class: 'f chk' }, [
          box,
          " Scale baseline risk to each place's " + r.label,
        ]),
        h('small', {
          class: 'hint',
          text:
            'The model itself uses one figure for every place (' +
            String(
              Math.round(
                data.models[scaled[0]].inputs.filter(function (i) {
                  return i.key === r.input;
                })[0].value * 100,
              ),
            ) +
            '%). Cost-effectiveness is proportional to it, so by default it is scaled to local ' +
            r.label +
            ", from the model's own country scenarios. Switch off for the model's flat figures.",
        }),
      ]);
    }

    function drawDials() {
      put(
        dialsBox,
        s.models.map(function (m) {
          return h(
            'div',
            { class: 'dials' },
            [h('div', { class: 'gt', text: data.models[m].label })].concat(
              data.dials.map(function (d) {
                var start = scn.dialDefault(m, d.key);
                var row = h('div', { class: 'ir' });
                var input = number(s.dials[m][d.key], function (v) {
                  scn.setDial(m, d.key, v);
                  row.classList.toggle('changed', v !== start);
                });
                row.classList.toggle('changed', s.dials[m][d.key] !== start);
                put(row, [
                  h('div', { title: d.hint }, [
                    d.label,
                    h('small', {
                      text:
                        d.unit +
                        (start !== d.default
                          ? ' · this programme starts at ' + start
                          : ''),
                    }),
                  ]),
                  input,
                ]);
                return row;
              }),
            ),
          );
        }),
      );
    }

    // ---- every assumption behind the dials, by the four questions that decide an answer
    function inputRow(model, i) {
      var over = s.overrides[model] || {};
      var row = h('div', {
        class: 'ir' + (over.hasOwnProperty(i.key) ? ' changed' : ''),
      });
      var cur = over.hasOwnProperty(i.key) ? over[i.key] : i.value;
      put(row, [
        h('div', {}, [i.label, h('small', { text: 'model: ' + i.value })]),
        number(cur, function (v) {
          scn.setInput(model, i.key, v, i.value);
          row.classList.toggle(
            'changed',
            (s.overrides[model] || {}).hasOwnProperty(i.key),
          );
        }),
      ]);
      return row;
    }

    function drawInputs() {
      put(
        inputsBox,
        s.models.map(function (k) {
          var m = data.models[k],
            kids = [h('summary', { text: m.label + ': all assumptions' })];
          data.blocks.forEach(function (b) {
            var rows = m.inputs.filter(function (i) {
              return i.group === b.key;
            });
            if (!rows.length) return;
            kids.push(h('div', { class: 'gt', text: b.title }));
            rows.forEach(function (i) {
              kids.push(inputRow(k, i));
            });
          });
          return h('details', { class: 'mi' }, kids);
        }),
      );
    }

    function drawSummary() {
      var c = scn.changes(),
        n = c.dials + c.assumptions;
      var kids = [
        n
          ? n +
            (n === 1 ? ' change' : ' changes') +
            ' from the models as published'
          : 'The models as published',
      ];
      if (n) {
        var undo = h('button', {
          type: 'button',
          class: 'btn',
          text: 'Reset',
          css: { marginLeft: '8px' },
        });
        undo.addEventListener('click', function () {
          scn.reset();
        });
        kids.push(undo);
      }
      put(summary, kids);
    }

    var barInput = number(s.bar, function (v) {
      scn.set({ bar: v }, 'bar');
    });
    function drawAll() {
      drawPlaces();
      drawGrain();
      drawChips();
      drawTarget();
      drawRisk();
      drawDials();
      drawInputs();
      drawSummary();
      barInput.value = String(s.bar);
    }

    put(root, [
      h('h2', { text: 'Places' }),
      places,
      grainBox,
      h('h2', { text: 'Programmes', css: { marginTop: '16px' } }),
      chips,
      targetBox,
      riskBox,
      h('h2', { text: 'Dials', css: { marginTop: '16px' } }),
      dialsBox,
      field("GiveWell's bar (x)", barInput),
      summary,
      inputsBox,
    ]);
    drawAll();
    scn.onChange(function (_, kind) {
      drawSummary();
      if (kind === 'models' || kind === 'reset') drawAll();
    });
  }

  window.EffDials = { mount: mount };
})();
