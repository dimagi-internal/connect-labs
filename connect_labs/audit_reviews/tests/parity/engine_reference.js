/* Reference copy of the browser engine from workflow 30017's render code (render_code_version 3),
 * kept verbatim so the Python port (connect_labs/audit_reviews/engine.py) can be checked against it.
 * Do not edit: when the rules change, change engine.py and its tests. */
var AR = (function () {
  var NEGATIVE_DEFAULT = [
    'new_yellow',
    'new_red',
    'worsened_to_yellow',
    'worsened_to_red',
    'stayed_yellow',
    'stayed_red',
    'became_data_gap',
    'stayed_data_gap',
  ];
  var WORSE = [
    'new_yellow',
    'new_red',
    'worsened_to_yellow',
    'worsened_to_red',
    'became_data_gap',
  ];
  var BETTER = ['improved_to_yellow', 'improved_to_green'];
  var STUCK_CH = ['stayed_yellow', 'stayed_red', 'stayed_data_gap'];
  var AUTO_CLS = ['nochange', 'stuck', 'improved', 'worsened', 'absent'];
  var LEVELS = ['program', 'llo', 'opportunity', 'flw'];
  var ACTION_KEYS = [
    'investigate',
    'share_feedback',
    'assess_progress',
    'formal_warning',
    'suspend',
  ];
  var LEVEL_ACTIONS_DEFAULT = {
    program: ['investigate', 'share_feedback', 'assess_progress'],
    llo: ACTION_KEYS,
    opportunity: [],
    flw: ACTION_KEYS,
  };

  function has(arr, x) {
    return arr.indexOf(x) >= 0;
  }
  function bandCount(m) {
    return (m && m.bands && m.bands.length) || 0;
  }

  /* % values arrive as fractions while bands are in points: work out the factor per metric. */
  function detectScales(D) {
    var pf = {};
    Object.keys(D.metrics).forEach(function (id) {
      var m = D.metrics[id];
      if (m.unit !== '%') {
        pf[id] = 1;
        return;
      }
      var mx = 0;
      D.runs.forEach(function (r) {
        var c = r.programme && r.programme[id];
        if (c && typeof c.value === 'number')
          mx = Math.max(mx, Math.abs(c.value));
      });
      var bandsBig = (m.bands || []).some(function (b) {
        return b > 1.5;
      });
      pf[id] = mx <= 1.0001 && bandsBig ? 100 : 1;
    });
    return pf;
  }

  function grade(D, pf, id, value, n) {
    var m = D.metrics[id];
    if (!m) return 'unbanded';
    if (!n) return 'nodata';
    if (n < (m.min_den == null ? 10 : m.min_den)) return 'insufficient';
    if (value == null) return 'nodata';
    if (bandCount(m) < 2) return 'unbanded';
    var v = value * (pf[id] || 1),
      g = m.bands[0],
      y = m.bands[1];
    if ((m.direction || 'higher') === 'lower')
      return v <= g ? 'green' : v <= y ? 'yellow' : 'red';
    return v >= g ? 'green' : v >= y ? 'yellow' : 'red';
  }

  function bandToState(band, hadDataBefore) {
    if (band === 'green' || band === 'yellow' || band === 'red') return band;
    if (band === 'unrecorded') return 'data_gap';
    if (band === 'nodata') return hadDataBefore ? 'data_gap' : 'na';
    if (band === 'insufficient') return 'too_few';
    return 'na';
  }

  /* prev = the last coloured state in an unbroken run of weeks; a week with no colour band breaks it. */
  function changeType(prev, curr) {
    if (curr === 'too_few') return 'too_few';
    if (curr === 'na') return 'na';
    if (curr === 'data_gap')
      return prev === 'data_gap' ? 'stayed_data_gap' : 'became_data_gap';
    if (!prev || prev === 'data_gap')
      return curr === 'green'
        ? prev
          ? 'improved_to_green'
          : 'stayed_green'
        : 'new_' + curr;
    var rank = { green: 0, yellow: 1, red: 2 };
    if (curr === prev) return 'stayed_' + curr;
    if (rank[curr] > rank[prev]) return 'worsened_to_' + curr;
    return 'improved_to_' + curr;
  }

  function oppInfo(D, id) {
    var list = (D.source && D.source.opportunities) || [];
    for (var i = 0; i < list.length; i++)
      if (String(list[i].id) === String(id)) return list[i];
    return { id: id, name: 'Opportunity ' + id, llo: '?' };
  }

  function defaultFlwKey(w) {
    return w.username || String(w.key).split('::').pop();
  }

  /* Entities in one run: program, LLOs, opportunities, and FLWs as people pooled across opportunities. */
  function buildRunEntities(D, pf, run, flwKey) {
    var out = new Map();
    var ids = Object.keys(D.metrics);
    var POOL = D.pool || {};
    out.set('program', {
      type: 'program',
      key: 'program',
      name: (D.source && D.source.program_name) || 'KMC program',
      parent: null,
      cells: run.programme || {},
    });
    Object.keys(run.llo || {}).forEach(function (llo) {
      out.set('llo:' + llo, {
        type: 'llo',
        key: 'llo:' + llo,
        name: llo,
        parent: 'program',
        cells: run.llo[llo],
      });
    });
    Object.keys(run.opportunity || {}).forEach(function (oid) {
      var o = oppInfo(D, oid);
      out.set('opp:' + oid, {
        type: 'opportunity',
        key: 'opp:' + oid,
        name: o.name,
        parent: 'llo:' + o.llo,
        cells: run.opportunity[oid],
        llo: o.llo,
      });
    });
    var byUser = new Map();
    (run.worker || []).forEach(function (w) {
      var u = flwKey(w);
      if (!byUser.has(u)) byUser.set(u, []);
      byUser.get(u).push(w);
    });
    byUser.forEach(function (rows, u) {
      var llo = rows[0].llo;
      var cells = {};
      if (rows.length === 1) cells = rows[0].cells || {};
      else
        ids.forEach(function (id) {
          var pool = POOL[id];
          if (pool) {
            var num = 0,
              den = 0,
              any = false;
            rows.forEach(function (r) {
              var c = r.cells && r.cells[pool];
              if (c && c.n) {
                num += c.value || 0;
                den += c.n;
                any = true;
              }
            });
            if (!any) {
              cells[id] = { band: 'nodata', value: null, n: 0 };
              return;
            }
            var val = den ? num / den : null;
            var m = D.metrics[id] || {};
            var valScaled = m.unit === '%' && pf[id] === 1 ? val * 100 : val;
            cells[id] = {
              band: grade(D, pf, id, valScaled, den),
              value: valScaled,
              n: den,
              pooled: rows.length,
            };
          } else {
            var order = {
              red: 5,
              yellow: 4,
              unrecorded: 3,
              green: 2,
              insufficient: 1,
              nodata: 0,
            };
            var best = null;
            rows.forEach(function (r) {
              var c = r.cells && r.cells[id];
              var oc = c ? (order[c.band] == null ? -1 : order[c.band]) : -2;
              var ob = best
                ? order[best.band] == null
                  ? -1
                  : order[best.band]
                : -2;
              if (c && (!best || oc > ob)) best = c;
            });
            if (best)
              cells[id] = Object.assign({}, best, { pooled: rows.length });
          }
        });
      out.set('flw:' + u, {
        type: 'flw',
        key: 'flw:' + u,
        name: rows[0].name || u,
        username: u,
        parent: 'llo:' + llo,
        cells: cells,
        llo: llo,
        opps: rows.map(function (r) {
          return r.opp;
        }),
      });
    });
    return out;
  }

  /* Starting settings. `saved` (the stored settings) wins wherever it says something. */
  function defaultConfig(D, saved) {
    saved = saved || {};
    var SUPPORTING = D.supporting || [];
    var PARENT = D.parents || {};
    var metrics = {};
    Object.keys(D.metrics).forEach(function (id) {
      var m = D.metrics[id];
      if (bandCount(m) < 2) return;
      var known = saved.knownMetrics || D.knownMetrics || null;
      var isKnown = known ? has(known, id) : true;
      var role = !isKnown ? 'detail' : has(SUPPORTING, id) ? 'detail' : 'main';
      var levels =
        m.flw_applicable === false
          ? LEVELS.filter(function (l) {
              return l !== 'flw';
            })
          : LEVELS.slice();
      metrics[id] = {
        role: role,
        parent: PARENT[id] || null,
        levels: levels,
        initiateOn: NEGATIVE_DEFAULT.slice(),
        persistence: 1,
        pool: (D.pool || {})[id] ? 'counts' : 'worst_band',
        autoAdded: !isKnown,
        confirmed: isKnown,
      };
      var s = saved.metrics && saved.metrics[id];
      if (s)
        Object.keys(s).forEach(function (k) {
          metrics[id][k] = s[k];
        });
    });
    var levelActions = {};
    LEVELS.forEach(function (l) {
      levelActions[l] =
        (saved.levelActions && saved.levelActions[l]) ||
        LEVEL_ACTIONS_DEFAULT[l].slice();
    });
    return {
      metrics: metrics,
      levelActions: levelActions,
      closeAfter: saved.closeAfter == null ? 2 : saved.closeAfter,
      autoApply: !!saved.autoApply,
    };
  }

  function greenStreak(arr) {
    var n = 0,
      want = null;
    for (var i = (arr || []).length - 1; i >= 0; i--) {
      var x = arr[i];
      if (x.state !== 'green' || (want !== null && x.ri !== want)) break;
      n++;
      want = x.ri - 1;
    }
    return n;
  }
  function badStreakArr(arr) {
    var n = 0,
      want = null;
    for (var i = (arr || []).length - 1; i >= 0; i--) {
      var x = arr[i];
      if (
        !(
          x.state === 'yellow' ||
          x.state === 'red' ||
          x.state === 'data_gap'
        ) ||
        (want !== null && x.ri !== want)
      )
        break;
      n++;
      want = x.ri - 1;
    }
    return n;
  }

  function classify(p) {
    if (p.kind === 'initiate') return 'open';
    if (p.kind === 'absent') return 'absent';
    if (p.suggestClose) return 'close';
    var ch = p.metrics.map(function (m) {
      return p.changes[m] && p.changes[m].change;
    });
    if (
      (p.added || []).length ||
      ch.some(function (c) {
        return has(WORSE, c);
      })
    )
      return 'worsened';
    if (
      ch.some(function (c) {
        return has(BETTER, c);
      })
    )
      return 'improved';
    if (
      ch.some(function (c) {
        return has(STUCK_CH, c);
      })
    )
      return 'stuck';
    return 'nochange';
  }

  /* Replays every run through the rules.
   *   D         {source, metrics, pool, parents, supporting, runs:[{run_id, period_end, programme, llo, opportunity, worker}]}
   *   CFG       defaultConfig(...)
   *   decisions {opens:{<run_id>:{<entKey>:{num, by, at}}}, closes:{<run_id>:{<entKey>:{status, by, at}}},
   *              dismissed:{<run_id>:{<entKey>:{by, at}}}}
   *             A close may also be recorded for a review at ANY run (Suspend, Closed – other): it takes effect
   *             after that run is processed.
   *   opts      {goLiveRunId, flwKey(w), runTime(run) -> ms}
   */
  function replay(D, CFG, decisions, opts) {
    opts = opts || {};
    decisions = decisions || {};
    var opens = decisions.opens || {},
      closes = decisions.closes || {},
      dismissed = decisions.dismissed || {};
    var flwKey = opts.flwKey || defaultFlwKey;
    var runTime =
      opts.runTime ||
      function (run) {
        return (
          new Date(
            String(run.period_end || run.as_of).slice(0, 10) + 'T00:00:00Z',
          ).getTime() +
          86400000 +
          6 * 3600e3
        );
      };
    var pf = detectScales(D);
    var runs = D.runs;
    var goLiveIdx = 0;
    if (opts.goLiveRunId != null) {
      goLiveIdx = runs.length;
      for (var g = 0; g < runs.length; g++)
        if (String(runs[g].run_id) === String(opts.goLiveRunId)) {
          goLiveIdx = g;
          break;
        }
    } else if (opts.goLiveIndex != null) goLiveIdx = opts.goLiveIndex;

    var ents = runs.map(function (r) {
      return buildRunEntities(D, pf, r, flwKey);
    });
    var entityInfo = new Map();
    ents.forEach(function (m) {
      m.forEach(function (e, k) {
        entityInfo.set(k, e);
      });
    });
    var history = new Map(),
      lastReal = new Map(),
      hadData = new Map();
    var reviews = [],
      openBy = new Map(),
      lastClosed = new Map(),
      byNum = new Map();
    var proposalsByRun = [];
    var metricIds = Object.keys(CFG.metrics);
    var mains = metricIds.filter(function (id) {
      return CFG.metrics[id].role === 'main';
    });
    function levelOn(l) {
      return (CFG.levelActions[l] || []).length > 0;
    }

    function persists(arr, m) {
      if (m.persistence <= 1) return true;
      var last = arr.slice(-m.persistence);
      return (
        last.length === m.persistence &&
        last.every(function (x) {
          return has(m.initiateOn, x.change);
        })
      );
    }
    function closeReview(rv, t, status, actor, ri) {
      if (rv.status !== 'open') return;
      rv.status = status;
      rv.closedAt = t;
      rv.closedRi = ri;
      rv.events.push({
        t: t,
        type: 'status_change',
        actor: actor,
        ri: ri,
        payload: { to: status },
      });
      openBy.delete(rv.ent);
      lastClosed.set(rv.ent, rv);
    }
    function applyOne(p, ri, t, actor) {
      var e = entityInfo.get(p.ent);
      if (p.kind === 'initiate') {
        var d = p.decision;
        var prev = lastClosed.get(p.ent);
        var rv = {
          id: d.num,
          ent: p.ent,
          type: e.type,
          name: e.name,
          llo: e.type === 'llo' ? e.name : e.llo,
          status: 'open',
          openedRi: ri,
          openedRunId: runs[ri].run_id,
          openedAt: d.at || t,
          openedBy: d.by || actor,
          closedAt: null,
          metrics: p.metrics.slice(),
          prevId: prev ? prev.id : null,
          events: [],
        };
        rv.events.push({
          t: t,
          type: 'opened',
          actor: d.by || actor,
          ri: ri,
          payload: {
            metrics: p.metrics.map(function (m) {
              return Object.assign({ m: m }, p.changes[m]);
            }),
          },
        });
        if (prev)
          rv.events.push({
            t: t,
            type: 'linked',
            actor: 'system',
            payload: { prevId: prev.id },
          });
        reviews.push(rv);
        openBy.set(p.ent, rv);
        byNum.set(String(rv.id), rv);
        p.openedId = rv.id;
      } else if (p.kind === 'update' || p.kind === 'absent') {
        var r = byNum.get(String(p.review));
        if (!r) return;
        if (p.kind === 'absent') {
          r.events.push({
            t: t,
            type: 'absent_from_run',
            actor: 'system',
            ri: ri,
            payload: {},
          });
          return;
        }
        r.events.push({
          t: t,
          type: 'run_update',
          actor: actor,
          ri: ri,
          payload: {
            metrics: p.metrics.map(function (m) {
              return Object.assign({ m: m }, p.changes[m]);
            }),
            auto: !!p.auto,
            cls: p.cls,
          },
        });
        (p.added || []).forEach(function (m) {
          r.metrics.push(m);
          r.events.push({
            t: t,
            type: 'metric_added',
            actor: actor,
            ri: ri,
            payload: {
              m: m,
              change: p.changes[m] && p.changes[m].change,
              state: p.changes[m] && p.changes[m].state,
            },
          });
        });
        if (p.suggestClose)
          r.events.push({
            t: t,
            type: 'close_suggested',
            actor: 'system',
            ri: ri,
            payload: {},
          });
      }
    }

    runs.forEach(function (run, ri) {
      var t = runTime(run);
      var rid = String(run.run_id);
      history.forEach(function (_v, k) {
        if (!ents[ri].has(k))
          metricIds.forEach(function (id) {
            lastReal.delete(k + '|' + id);
          });
      });
      var live = ri >= goLiveIdx;
      var props = [];
      ents[ri].forEach(function (e, k) {
        if (!history.has(k)) history.set(k, {});
        var H = history.get(k);
        var rowChanges = {};
        metricIds.forEach(function (id) {
          if (CFG.metrics[id].role === 'off') return;
          var c = e.cells && e.cells[id];
          var band = c ? c.band : 'nodata';
          var lk = k + '|' + id;
          var state = bandToState(band, hadData.get(lk));
          var prev = lastReal.get(lk);
          var ch = changeType(prev, state);
          (H[id] = H[id] || []).push({
            ri: ri,
            band: band,
            state: state,
            value: c && c.value != null ? c.value : null,
            n: (c && c.n) || 0,
            change: ch,
            pooled: c && c.pooled,
          });
          if (
            state === 'green' ||
            state === 'yellow' ||
            state === 'red' ||
            state === 'data_gap'
          )
            lastReal.set(lk, state);
          else lastReal.delete(lk);
          if (state === 'green' || state === 'yellow' || state === 'red')
            hadData.set(lk, true);
          rowChanges[id] = { prev: prev, state: state, change: ch };
        });
        if (!live) return;
        var open = openBy.get(k);
        var trig = !levelOn(e.type)
          ? []
          : mains.filter(function (id) {
              var m = CFG.metrics[id];
              return (
                has(m.levels, e.type) &&
                rowChanges[id] &&
                has(m.initiateOn, rowChanges[id].change) &&
                persists(H[id], m)
              );
            });
        if (open) {
          var tracked = open.metrics.slice();
          trig.forEach(function (id) {
            if (!has(tracked, id)) tracked.push(id);
          });
          var allGreen = tracked.every(function (id) {
            return greenStreak(H[id]) >= CFG.closeAfter;
          });
          props.push({
            ri: ri,
            kind: 'update',
            ent: k,
            metrics: tracked,
            added: trig.filter(function (id) {
              return !has(open.metrics, id);
            }),
            changes: rowChanges,
            suggestClose: allGreen,
            review: open.id,
          });
        } else if (trig.length) {
          props.push({
            ri: ri,
            kind: 'initiate',
            ent: k,
            metrics: trig,
            changes: rowChanges,
          });
        }
      });
      if (live)
        openBy.forEach(function (rv, k) {
          if (!ents[ri].has(k))
            props.push({
              ri: ri,
              kind: 'absent',
              ent: k,
              review: rv.id,
              metrics: rv.metrics,
              changes: {},
            });
        });
      proposalsByRun[ri] = props;
      if (!live) return;
      props.forEach(function (p) {
        p.cls = classify(p);
      });
      props.forEach(function (p) {
        if (p.cls === 'open') {
          var d = opens[rid] && opens[rid][p.ent];
          if (d) {
            p.decision = d;
            p.decided = 'confirmed';
            applyOne(p, ri, t, d.by);
          } else if (dismissed[rid] && dismissed[rid][p.ent])
            p.decided = 'dismissed';
          else p.pending = true;
        } else if (p.cls === 'close') {
          // The run update is logged either way; the review closes only if someone confirmed it.
          var c = closes[rid] && closes[rid][p.ent];
          applyOne(p, ri, t, 'system');
          if (c) {
            p.decided = 'confirmed';
          } else if (dismissed[rid] && dismissed[rid][p.ent])
            p.decided = 'dismissed';
          else p.pending = true;
        } else {
          p.auto = true;
          applyOne(p, ri, t, 'system');
        }
      });
      // Closes recorded at this run (confirmed "ready to close", Suspend, Closed – other).
      Object.keys(closes[rid] || {}).forEach(function (k) {
        var rv = openBy.get(k);
        var c = closes[rid][k];
        if (rv)
          closeReview(
            rv,
            c.at || t,
            c.status || 'closed_improved',
            c.by || 'system',
            ri,
          );
      });
    });

    reviews.forEach(function (r) {
      r.events.sort(function (a, b) {
        return a.t - b.t;
      });
    });
    var latestRi = runs.length - 1;
    return {
      runs: runs,
      ents: ents,
      entityInfo: entityInfo,
      history: history,
      reviews: reviews,
      openBy: openBy,
      byNum: byNum,
      proposalsByRun: proposalsByRun,
      latestRi: latestRi,
      goLiveIdx: goLiveIdx,
      pf: pf,
      greenRun: function (ent, m) {
        var h = history.get(ent);
        return greenStreak(h && h[m]);
      },
      badStreak: function (ent, m) {
        var h = history.get(ent);
        return badStreakArr(h && h[m]);
      },
    };
  }

  /* KC Pulse saved run (fields read with ?keys=) -> the run shape above. */
  function fromKcRun(r) {
    var st = r.state || {};
    var get = function (k) {
      return st['snapshot.' + k] != null ? st['snapshot.' + k] : null;
    };
    var W = (st.snapshot &&
      st.snapshot.series &&
      st.snapshot.series.WINDOW) || {
      programme: get('series.WINDOW.programme'),
      byLLO: get('series.WINDOW.byLLO'),
      byOpp: get('series.WINDOW.byOpp'),
      byFLW: get('series.WINDOW.byFLW'),
      measures: get('series.WINDOW.measures'),
    };
    if (!W.programme || !W.byFLW) return null;
    var llo = {},
      opp = {},
      opps = [];
    (W.byLLO || []).forEach(function (x) {
      llo[x.llo] = x.ind || {};
    });
    (W.byOpp || []).forEach(function (x) {
      opp[String(x.opp)] = x.ind || {};
      opps.push({ id: x.opp, llo: x.llo });
    });
    return {
      run_id: r.id != null ? r.id : r.run_id,
      period_start: r.period_start,
      period_end: r.period_end,
      programme: W.programme,
      llo: llo,
      opportunity: opp,
      measures: W.measures || null,
      opps: opps,
      worker: (W.byFLW || []).map(function (f) {
        return {
          key: f.key,
          username: f.flw,
          name: f.name,
          opp: String(f.opp),
          llo: f.llo,
          cells: f.ind || {},
        };
      }),
    };
  }

  return {
    NEGATIVE_DEFAULT: NEGATIVE_DEFAULT,
    AUTO_CLS: AUTO_CLS,
    LEVELS: LEVELS,
    detectScales: detectScales,
    grade: grade,
    bandToState: bandToState,
    changeType: changeType,
    buildRunEntities: buildRunEntities,
    defaultConfig: defaultConfig,
    replay: replay,
    classify: classify,
    fromKcRun: fromKcRun,
  };
})();
module.exports = AR;
