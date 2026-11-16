/* The scenario in the browser: the same choices as effectiveness/scenario.py, kept in the link.
 *
 * The server reads the link, fills in everything the link leaves out, and embeds the whole thing in the page
 * (`data.state`). This module starts from that, changes it as the controls do, writes only what differs from the default
 * back to the address bar, and asks the API again. It owns the link's spelling (`<model>_<dial>`, `i.<model>.<input>`),
 * so a page never builds a query string of its own. */
(function () {
  'use strict';

  var EVERYWHERE = 'all';

  function clone(o) {
    return JSON.parse(JSON.stringify(o));
  }

  function str(v) {
    return String(v);
  }

  function create(data) {
    var start = clone(data.state);
    var dialDefault = {};
    data.dials.forEach(function (d) {
      dialDefault[d.key] = d.default;
    });
    var s = {
      models: start.models,
      indicator: start.indicator,
      threshold: start.threshold,
      iso: start.iso || '',
      bar: start.bar,
      dials: start.dials,
      overrides: start.overrides || {},
      extras: start.extras || {},
    };
    // Things a page keeps in the link that are not the scenario (a map lens): written back, never sent to the model.
    var ui = {};
    var listeners = [];

    function indicatorMeta(key) {
      return (
        data.indicators.filter(function (i) {
          return i.key === key;
        })[0] || data.indicators[0]
      );
    }
    if (s.threshold == null && s.indicator !== EVERYWHERE)
      s.threshold = indicatorMeta(s.indicator).threshold;

    function changed(model) {
      return {
        dials: Object.keys(dialDefault).filter(function (k) {
          return s.dials[model][k] !== dialDefault[k];
        }),
        assumptions: Object.keys(s.overrides[model] || {}),
      };
    }

    // How far the scenario is from the models as published; only programmes in play count.
    function changes() {
      var out = { dials: 0, assumptions: 0 };
      s.models.forEach(function (m) {
        var c = changed(m);
        out.dials += c.dials.length;
        out.assumptions += c.assumptions.length;
      });
      return out;
    }

    // Only what differs from the default, so the default scenario is the bare page.
    function params() {
      var p = new URLSearchParams();
      p.set('models', s.models.join(','));
      if (s.indicator !== EVERYWHERE) {
        p.set('indicator', s.indicator);
        if (s.threshold !== '' && s.threshold != null)
          p.set('threshold', str(s.threshold));
      }
      if (s.iso.trim()) p.set('iso', s.iso.replace(/\s+/g, ''));
      if (s.bar !== data.bar) p.set('bar', str(s.bar));
      Object.keys(s.extras).forEach(function (k) {
        p.set(k, s.extras[k]);
      });
      Object.keys(s.dials).forEach(function (m) {
        Object.keys(dialDefault).forEach(function (k) {
          if (s.dials[m][k] !== dialDefault[k])
            p.set(m + '_' + k, str(s.dials[m][k]));
        });
      });
      Object.keys(s.overrides).forEach(function (m) {
        Object.keys(s.overrides[m]).forEach(function (k) {
          p.set('i.' + m + '.' + k, str(s.overrides[m][k]));
        });
      });
      return p;
    }

    function link() {
      var p = params();
      Object.keys(ui).forEach(function (k) {
        if (ui[k] != null) p.set(k, ui[k]);
      });
      return p;
    }

    // `kind` says what changed (places, models, dial, input, bar, reset, ui) so a control redraws only when it must:
    // redrawing a field someone is typing in loses their place.
    function emit(kind) {
      listeners.forEach(function (fn) {
        fn(api, kind);
      });
    }

    var api = {
      state: s,
      ui: ui,
      dialDefault: dialDefault,
      indicatorMeta: indicatorMeta,
      changed: changed,
      changes: changes,
      params: params,
      link: link,
      onChange: function (fn) {
        listeners.push(fn);
      },
      set: function (patch, kind) {
        Object.keys(patch).forEach(function (k) {
          s[k] = patch[k];
        });
        emit(kind || 'places');
      },
      setIndicator: function (key) {
        s.indicator = key;
        s.threshold = key === EVERYWHERE ? null : indicatorMeta(key).threshold;
        emit('places');
      },
      toggleModel: function (model, on) {
        var at = s.models.indexOf(model);
        if (on && at < 0) s.models.push(model);
        if (!on && at >= 0 && s.models.length > 1) s.models.splice(at, 1);
        emit('models');
      },
      setDial: function (model, key, value) {
        s.dials[model][key] = value;
        emit('dial');
      },
      // An input back at the model's own value is no change, so it leaves the link.
      setInput: function (model, key, value, modelValue) {
        var o = (s.overrides[model] = s.overrides[model] || {});
        if (value === modelValue) delete o[key];
        else o[key] = value;
        emit('input');
      },
      setUi: function (key, value) {
        ui[key] = value;
        emit('ui');
      },
      // The models as published: every dial and assumption back to the default. The places stay.
      reset: function () {
        Object.keys(s.dials).forEach(function (m) {
          Object.keys(dialDefault).forEach(function (k) {
            s.dials[m][k] = dialDefault[k];
          });
        });
        s.overrides = {};
        s.bar = data.bar;
        emit('reset');
      },
      // Keep the address bar and the answer in step with the scenario. `onData` gets each answer; a superseded
      // request never paints over a newer one.
      follow: function (apiUrl, onData, onError) {
        var timer = null,
          seq = 0;
        function writeLink() {
          try {
            history.replaceState(
              null,
              '',
              location.pathname + '?' + link().toString(),
            );
          } catch (e) {
            /* a sandboxed frame */
          }
        }
        function ask() {
          clearTimeout(timer);
          timer = setTimeout(function () {
            var mine = ++seq;
            fetch(apiUrl + '?' + params().toString(), {
              credentials: 'same-origin',
            })
              .then(function (r) {
                return r.json().then(function (j) {
                  return { ok: r.ok, j: j };
                });
              })
              .then(function (res) {
                if (mine !== seq) return;
                if (!res.ok) onError(res.j.error || 'That did not run.');
                else onData(res.j);
              })
              .catch(function () {
                if (mine === seq)
                  onError('The server did not respond. Try again.');
              });
          }, 250);
        }
        listeners.push(function (_, kind) {
          writeLink();
          // A page's own view settings (the map lens) are in the link but change no number.
          if (kind !== 'ui') ask();
        });
        writeLink();
        ask();
      },
    };
    return api;
  }

  window.EffScenario = { create: create, EVERYWHERE: EVERYWHERE };
})();
