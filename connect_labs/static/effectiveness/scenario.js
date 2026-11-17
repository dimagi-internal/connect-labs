/* The scenario in the browser: the same choices as effectiveness/scenario.py, kept in the link.
 *
 * The server reads the link, fills in everything the link leaves out, and embeds the whole thing in the page
 * (`data.state`). This module starts from that, changes it as the controls do, writes only what differs from the default
 * back to the address bar, and asks the API again. It owns the link's spelling (`<model>_<dial>`, `i.<model>.<input>`,
 * `target`, `min_place`, `max_countries`, `trust`, `place_risk=0`, `grain=territory`), so a page never builds a query string of its own. A dial's default is the
 * model's own (`data.dial_defaults`): Kangaroo Care starts at reaching half of a place's cases, the others at all. */
(function () {
  'use strict';

  var EVERYWHERE = 'all';
  var COUNTRY = 'country';

  function clone(o) {
    return JSON.parse(JSON.stringify(o));
  }

  function str(v) {
    return String(v);
  }

  function create(data) {
    var start = clone(data.state);
    var s = {
      models: start.models,
      indicator: start.indicator,
      threshold: start.threshold,
      iso: start.iso || '',
      bar: start.bar,
      target: start.target == null ? null : start.target,
      min_place: start.min_place == null ? null : start.min_place,
      max_countries: start.max_countries == null ? null : start.max_countries,
      max_conflict: start.max_conflict == null ? null : start.max_conflict,
      trust: start.trust == null ? 100 : start.trust,
      place_risk: start.place_risk !== false,
      grain: start.grain || COUNTRY,
      dials: start.dials,
      overrides: start.overrides || {},
      extras: start.extras || {},
    };
    // Things a page keeps in the link that are not the scenario (a map lens): written back, never sent to the model.
    var ui = {};
    var listeners = [];

    function dialDefault(model, key) {
      return data.dial_defaults[model][key];
    }
    function indicatorMeta(key) {
      return (
        data.indicators.filter(function (i) {
          return i.key === key;
        })[0] || data.indicators[0]
      );
    }
    if (s.threshold == null && s.indicator !== EVERYWHERE)
      s.threshold = indicatorMeta(s.indicator).threshold;

    function dialKeys() {
      return data.dials.map(function (d) {
        return d.key;
      });
    }
    function changed(model) {
      return {
        dials: dialKeys().filter(function (k) {
          return s.dials[model][k] !== dialDefault(model, k);
        }),
        assumptions: Object.keys(s.overrides[model] || {}),
      };
    }
    // Whether a programme in play has a baseline risk that scales to the place, and the switch is off.
    function riskOff() {
      return (
        !s.place_risk &&
        s.models.some(function (m) {
          return data.place_risk[m];
        })
      );
    }

    // How far the scenario is from the models as published; only programmes in play count.
    function changes() {
      var out = {
        dials: 0,
        assumptions: (riskOff() ? 1 : 0) + (s.trust !== 100 ? 1 : 0),
      };
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
      if (s.target != null && s.target > 0) p.set('target', str(s.target));
      if (s.min_place != null && s.min_place > 0)
        p.set('min_place', str(s.min_place));
      if (s.max_countries != null && s.max_countries > 0)
        p.set('max_countries', str(s.max_countries));
      if (s.max_conflict != null && s.max_conflict > 0)
        p.set('max_conflict', str(s.max_conflict));
      if (s.trust !== 100) p.set('trust', str(s.trust));
      if (!s.place_risk) p.set('place_risk', '0');
      if (s.grain !== COUNTRY) p.set('grain', s.grain);
      Object.keys(s.extras).forEach(function (k) {
        p.set(k, s.extras[k]);
      });
      Object.keys(s.dials).forEach(function (m) {
        dialKeys().forEach(function (k) {
          if (s.dials[m][k] !== dialDefault(m, k))
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

    // `kind` says what changed (places, models, dial, input, bar, target, risk, reset, ui) so a control redraws only when
    // it must: redrawing a field someone is typing in loses their place.
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
      setGrain: function (grain) {
        s.grain = grain;
        emit('grain');
      },
      toggleModel: function (model, on) {
        var at = s.models.indexOf(model);
        if (on && at < 0) s.models.push(model);
        if (!on && at >= 0 && s.models.length > 1) s.models.splice(at, 1);
        emit('models');
      },
      // How many people to reach; nothing (or zero) means everyone the programme can reach.
      setTarget: function (value) {
        s.target = value > 0 ? value : null;
        emit('target');
      },
      // The plan's limits: the smallest place worth working in (people) and the most countries; nothing means no limit.
      setLimit: function (key, value) {
        s[key] = value > 0 ? value : null;
        emit('target');
      },
      // How far to believe each place's own figures, in %: 100 fully; lower pulls them toward what is known a level up.
      setTrust: function (value) {
        s.trust = Math.min(100, Math.max(0, value));
        emit('trust');
      },
      setPlaceRisk: function (on) {
        s.place_risk = !!on;
        emit('risk');
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
      // The models as published: every dial at the model's own start, every assumption unchanged, risk scaled to the place.
      // The places and the target stay.
      reset: function () {
        Object.keys(s.dials).forEach(function (m) {
          dialKeys().forEach(function (k) {
            s.dials[m][k] = dialDefault(m, k);
          });
        });
        s.overrides = {};
        s.place_risk = true;
        s.trust = 100;
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

  window.EffScenario = {
    create: create,
    EVERYWHERE: EVERYWHERE,
    COUNTRY: COUNTRY,
  };
})();
