function WorkflowUI({
  definition,
  instance,
  workers,
  pipelines,
  links,
  actions,
  onUpdateState,
  view,
}) {
  // ══ A programme's indicator report, for ANY semantic registry ══════════════
  //
  // ONE render for two templates. `indicator_programme_report` (many
  // opportunities) drills programme -> organisation -> opportunity -> worker;
  // `indicator_opp_report` (one opportunity, for its network manager) opens
  // straight on that opportunity's workers and adds a Benchmarks tab placing its
  // organisation among the programme's others, anonymously. The payload shape is
  // the same -- a handed-down slice IS a programme payload cut to one
  // opportunity (workflow/hand_down.py) -- so one view reads both.
  //
  // The KMC programme report's cascade -- programme -> organisation ->
  // opportunity -> worker -> case -- with nothing programme-specific in it.
  // Every number is the semantic-snapshot builder's graded payload over the
  // bound registry (a completed run stores it; a live run previews it), and
  // every WORD the reader sees about the programme -- which indicators are the
  // headline, their targets and labels, the scorecard's columns and category
  // groups, "baby" or "community" or "beneficiary" -- is the payload's
  // `display` block, resolved from the registry (semantic/display.py). The
  // shared report library (window.LabsReport) draws it.
  //
  // To change what this page shows for a programme, edit its REGISTRY
  // (semantic_registry_set_indicator_meta / semantic_registry_update), never
  // this file. ES5 dialect throughout.
  var R = window.LabsReport;
  if (!R || !R.displayOf)
    return (
      <div className="p-6 text-sm text-red-800">
        The report library did not load (or is older than this report). Reload
        the page.
      </div>
    );
  var cfg = (definition && definition.config) || {};
  var STALE = Number(cfg.stale_after_days) || 14;
  var OPP_MODE =
    cfg.templateType === 'indicator_opp_report' || cfg.multi_opp === false;
  var FLW_SEP = '::';
  var search = String(window.location.search || '');

  function scopeParams() {
    var out = [];
    var m = search.match(/[?&]opportunity_id=(\d+)/);
    if (m) out.push('opportunity_id=' + m[1]);
    else if (instance && instance.opportunity_id)
      out.push('opportunity_id=' + instance.opportunity_id);
    var pgm = search.match(/[?&]owning_program_id=(\d+)/);
    if (pgm) out.push('owning_program_id=' + pgm[1]);
    else if (instance && instance.program_id)
      out.push('owning_program_id=' + instance.program_id);
    return out.length ? '?' + out.join('&') : '';
  }
  function withParams(url, extra) {
    var sp = scopeParams();
    return url + sp + (extra ? (sp ? '&' : '?') + extra : '');
  }
  function definitionId() {
    var pathMatch = String(window.location.pathname || '').match(
      /\/workflow\/(\d+)\//,
    );
    return (
      (definition && (definition.id || definition.definition_id)) ||
      (instance && instance.definition_id) ||
      (pathMatch && Number(pathMatch[1])) ||
      null
    );
  }

  // ══ The payload ════════════════════════════════════════════════════════════
  var snapshot =
    view && view.isCompleted && view.state && view.state.snapshot
      ? view.state.snapshot
      : null;
  var sLive = React.useState({
    status: snapshot ? 'ready' : 'loading',
    payload: null,
    cache: null,
    error: null,
  });
  var live = sLive[0],
    setLive = sLive[1];
  var sTry = React.useState(0);
  var tries = sTry[0],
    setTries = sTry[1];
  React.useEffect(
    function () {
      if (snapshot) return;
      var runId = instance && instance.id;
      if (!runId) {
        setLive({ status: 'error', error: 'no run id on this page' });
        return;
      }
      var cancelled = false;
      setLive({ status: 'loading', payload: null, cache: null, error: null });
      fetch(
        withParams('/labs/workflow/api/run/' + runId + '/snapshot/preview/'),
        {
          credentials: 'same-origin',
        },
      )
        .then(function (r) {
          return r.json().catch(function () {
            return { error: 'the server answered HTTP ' + r.status };
          });
        })
        .then(function (data) {
          if (cancelled) return;
          if (data.error) throw new Error(data.error);
          var st = (data.snapshot || {}).state || {};
          if (!st.snapshot)
            throw new Error('the preview carried no snapshot payload');
          setLive({
            status: 'ready',
            payload: st.snapshot,
            cache: data.cache || null,
            error: null,
          });
        })
        .catch(function (e) {
          if (!cancelled)
            setLive({
              status: 'error',
              payload: null,
              error: String((e && e.message) || e),
            });
        });
      return function () {
        cancelled = true;
      };
    },
    [Boolean(snapshot), instance && instance.id, tries],
  );
  var payload = snapshot || live.payload;
  var P = payload || {};
  var ready = !!payload;
  var D = React.useMemo(
    function () {
      return R.displayOf(P);
    },
    [payload],
  );
  var MEASURES = (P.cMeasures || []).filter(function (m) {
    return m && m.indicator;
  });
  var M_BY_ID = {};
  MEASURES.forEach(function (m) {
    M_BY_ID[m.indicator] = m;
  });
  var LAYOUT = React.useMemo(
    function () {
      return R.scorecardLayout(P, D);
    },
    [payload],
  );
  var TILES = React.useMemo(
    function () {
      return R.headlineSpecs(P, D);
    },
    [payload],
  );
  var ENT = D.entity,
    WRK = D.worker,
    ORG = D.organisation;
  // A case whose work is FINISHED (`display.entity.done_property`) has no visit
  // due, so it is never stale -- nor is a worker, opportunity or organisation
  // whose cases are all finished. A display with no done property finishes
  // nothing, and the staleness rule reads exactly as before.
  function isDone(c) {
    return R.caseDone ? R.caseDone(D, c) : false;
  }
  var FINISHED_TIP = 'Every ' + ENT.name + ' here is finished: no visit is due';
  // The registry's default floor (display.min_denominator); a measure's own
  // min_denominator still wins cell by cell.
  var MIN_DEN =
    Number(cfg.min_denominator_default) || Number(D.min_denominator) || 20;
  function entryOf(map, id) {
    return (map && map[id]) || { id: id, n: 0, value: null, band: 'nodata' };
  }
  var meta = P.meta || {};
  var isCompleted = !!(view && view.isCompleted);
  var asOf =
    meta.as_of ||
    (view && view.asOf ? String(view.asOf).slice(0, 10) : '') ||
    new Date().toISOString().slice(0, 10);
  var LLO_MAP = (P.deployment && P.deployment.llo_map) || {};
  var HAS_ORGS = (P.byLLO || []).length > 0;
  function orgOf(opp) {
    return LLO_MAP[opp] || LLO_MAP[String(opp)] || null;
  }
  // What a reader calls each opportunity. The run's own names first -- resolved
  // when it was built from the opportunity's record, or the registry's
  // `deployment.opportunity_labels` (snapshot_builders.opportunity_labels) --
  // then the viewer's own opportunity list on this page, for runs saved before
  // names were carried. Never invented: an unnamed one reads "Opportunity <id>".
  // Two carriers of the same list: the multi-opp run page's `user-opportunities`
  // and the header context selector's `opportunity-data`, which is the only one
  // on a single-opportunity page (the opportunity report).
  var PAGE_OPP_NAMES = React.useMemo(function () {
    var m = {};
    ['user-opportunities', 'opportunity-data'].forEach(function (id) {
      try {
        var el = document.getElementById(id);
        if (el)
          (JSON.parse(el.textContent) || []).forEach(function (o) {
            var k =
              o && o.id !== null && o.id !== undefined ? String(o.id) : null;
            if (k && o.name && !m[k]) m[k] = String(o.name);
          });
      } catch (e) {
        console.error('Indicator report: could not read ' + id, e);
      }
    });
    return m;
  }, []);
  var OPP_LABELS = (P.deployment && P.deployment.opportunity_labels) || {};
  function oppName(opp) {
    if (opp === null || opp === undefined) return null;
    return OPP_LABELS[String(opp)] || PAGE_OPP_NAMES[String(opp)] || null;
  }
  function oppLabel(opp) {
    return oppName(opp) || 'Opportunity ' + opp;
  }

  // ══ Drill state ═════════════════════════════════════════════════════════════
  var sOrg = React.useState(null);
  var selOrg = sOrg[0],
    setSelOrg = sOrg[1];
  var sOpp = React.useState(null);
  var selOpp = sOpp[0],
    setSelOpp = sOpp[1];
  var sOpenWorker = React.useState(null);
  var openWorker = sOpenWorker[0],
    setOpenWorker = sOpenWorker[1];
  var sCohort = React.useState('none');
  var cohortDim = sCohort[0],
    setCohortDim = sCohort[1];
  var sort = R.useTableSort({
    orgs: { key: 'size', dir: 'desc' },
    opps: { key: 'size', dir: 'desc' },
    workers: { key: 'size', dir: 'desc' },
  });

  var orgRow = selOrg
    ? (P.byLLO || []).filter(function (l) {
        return l.llo === selOrg;
      })[0] || null
    : null;
  var oppRow =
    selOpp !== null
      ? (P.byOpp || []).filter(function (o) {
          return String(o.opp) === String(selOpp);
        })[0] || null
      : null;
  var scopeInd = oppRow ? oppRow.ind : orgRow ? orgRow.ind : P.programInd || {};
  var scopeKey = oppRow ? 'opp:' + selOpp : orgRow ? 'llo:' + selOrg : 'all';

  // ══ Cases, resolved; last visit per row ════════════════════════════════════
  var caseIndex = P.cases || [];
  var lastVisit = React.useMemo(
    function () {
      // `open` counts each group's cases still under way; a group with cases
      // and none open is finished.
      var out = {
        org: {},
        opp: {},
        open: { org: {}, opp: {} },
        n: { org: {}, opp: {} },
      };
      caseIndex.forEach(function (c) {
        var o = String(c.opportunity_id);
        var g = c.llo || orgOf(c.opportunity_id);
        var open = isDone(c) ? 0 : 1;
        out.n.opp[o] = (out.n.opp[o] || 0) + 1;
        out.open.opp[o] = (out.open.opp[o] || 0) + open;
        if (g) {
          out.n.org[g] = (out.n.org[g] || 0) + 1;
          out.open.org[g] = (out.open.org[g] || 0) + open;
        }
        var d = String(c.last_visit_date || '').slice(0, 10);
        if (!d) return;
        if (!out.opp[o] || d > out.opp[o]) out.opp[o] = d;
        if (g && (!out.org[g] || d > out.org[g])) out.org[g] = d;
      });
      out.doneOf = function (kind, key) {
        return !!out.n[kind][key] && !out.open[kind][key];
      };
      return out;
    },
    [payload],
  );
  var workerRows = React.useMemo(
    function () {
      return (P.byFLW || []).map(function (f) {
        var rows = (f.rows || [])
          .map(function (i) {
            return typeof i === 'number' ? caseIndex[i] : i;
          })
          .filter(Boolean);
        var last = '';
        rows.forEach(function (c) {
          var d = String(c.last_visit_date || '').slice(0, 10);
          if (d > last) last = d;
        });
        return {
          key: f.key || f.opp + FLW_SEP + (f.username || f.flw),
          // The display name the run was built with; the key stays the username.
          name: f.name || f.username || f.flw,
          opp: f.opp,
          org: f.llo || orgOf(f.opp),
          ind: f.ind || {},
          reds: f.reds || 0,
          yellows: f.yellows || 0,
          cases: rows,
          n: rows.length || f.n || 0,
          last: last,
          done: rows.length > 0 && rows.every(isDone),
          startMonth: f.startMonth || null,
          caseload: f.caseloadLabel || null,
        };
      });
    },
    [payload],
  );

  // ══ What the agent panel sees ═══════════════════════════════════════════════
  // When the workflow shares its runs (config.agent.share), the embedded agent
  // is told what is on screen: the workers in the drilled scope and the scope
  // itself. Keys only; the agent reads their indicators itself, as the visitor.
  // A no-op when the workflow does not share.
  React.useEffect(
    function () {
      if (!view || !view.shareSelection) return;
      var keys = workerRows
        .filter(function (w) {
          if (openWorker) return w.key === openWorker;
          if (selOpp !== null) return String(w.opp) === String(selOpp);
          if (selOrg) return w.org === selOrg;
          return true;
        })
        .map(function (w) {
          return w.key;
        });
      view.shareSelection({
        visible_ids: keys,
        drilled: {
          organisation: selOrg || null,
          opportunity_id: selOpp !== null ? selOpp : null,
          worker: openWorker || null,
        },
      });
    },
    [workerRows, selOrg, selOpp, openWorker],
  );

  // ══ History of saved reports: deltas and trends ════════════════════════════
  var sHistory = React.useState(null);
  var history = sHistory[0],
    setHistory = sHistory[1];
  React.useEffect(
    function () {
      var defId = definitionId();
      if (!defId) {
        setHistory([]);
        return;
      }
      var cancelled = false;
      var keys = ['programInd', 'byLLO', 'byOpp', 'meta']
        .map(function (k) {
          return 'snapshot.' + k;
        })
        .join(',');
      fetch(
        withParams(
          '/labs/workflow/api/' + defId + '/runs/history/',
          'keys=' + keys,
        ),
        {
          credentials: 'same-origin',
        },
      )
        .then(function (r) {
          return r.ok ? r.json() : { runs: [] };
        })
        .then(function (j) {
          if (!cancelled) setHistory(j.runs || []);
        })
        .catch(function () {
          if (!cancelled) setHistory([]);
        });
      return function () {
        cancelled = true;
      };
    },
    [definition && definition.id, instance && instance.definition_id],
  );
  function indFromState(st) {
    if (selOpp !== null) {
      var o = (st.byOpp || []).filter(function (x) {
        return String(x.opp) === String(selOpp);
      })[0];
      return o ? o.ind : null;
    }
    if (selOrg) {
      var l = (st.byLLO || []).filter(function (x) {
        return x.llo === selOrg;
      })[0];
      return l ? l.ind : null;
    }
    return st.programInd || null;
  }
  var historyPoints = React.useMemo(
    function () {
      var byDate = {};
      (history || []).forEach(function (r) {
        var st = {};
        Object.keys(r.state || {}).forEach(function (k) {
          st[k.replace(/^snapshot\./, '')] = r.state[k];
        });
        var d =
          (st.meta && st.meta.as_of) || String(r.period_end || '').slice(0, 10);
        var ind = indFromState(st);
        if (!d || !ind || !Object.keys(ind).length) return;
        byDate[d] = { date: d, ind: ind };
      });
      if (ready && !byDate[asOf])
        byDate[asOf] = { date: asOf, ind: scopeInd, current: true };
      return Object.keys(byDate)
        .sort()
        .map(function (d) {
          return byDate[d];
        });
    },
    [history, payload, selOrg, selOpp],
  );

  // ══ Definitions, from the explain reader ═══════════════════════════════════
  var sDef = React.useState(null);
  var colDef = sDef[0],
    setColDef = sDef[1];
  var sDefCache = React.useState({});
  var defCache = sDefCache[0],
    setDefCache = sDefCache[1];
  function explainUrl(id, scope, fmt, download) {
    return withParams(
      '/labs/workflow/api/' + definitionId() + '/indicator-definitions/',
      'indicators=' +
        encodeURIComponent(id) +
        '&scope=' +
        scope +
        '&format=' +
        fmt +
        (download ? '&download=1' : ''),
    );
  }
  function openDef(id, scope) {
    scope = scope || 'programme';
    setColDef({ id: id, scope: scope });
    var k = scope + '|' + id;
    if (defCache[k] && defCache[k].status !== 'error') return;
    function put(v) {
      setDefCache(function (prev) {
        var next = Object.assign({}, prev);
        next[k] = v;
        return next;
      });
    }
    put({ status: 'loading' });
    fetch(explainUrl(id, scope, 'json', false), { credentials: 'same-origin' })
      .then(function (r) {
        return r.json().catch(function () {
          return { error: 'HTTP ' + r.status };
        });
      })
      .then(function (j) {
        if (j.error) throw new Error(j.error);
        var e = (j.indicators || [])[0];
        if (!e) throw new Error('no definition came back for ' + id);
        put({ status: 'ready', entry: e });
      })
      .catch(function (err) {
        put({ status: 'error', error: String((err && err.message) || err) });
      });
  }
  function DefModal() {
    if (!colDef) return null;
    var m = M_BY_ID[colDef.id] || {};
    return (
      <R.DefinitionModal
        id={colDef.id}
        title={m.title}
        state={
          defCache[colDef.scope + '|' + colDef.id] || { status: 'loading' }
        }
        entityPlural={ENT.plural}
        onClose={function () {
          setColDef(null);
        }}
        downloadUrl={explainUrl(colDef.id, colDef.scope, 'sql', true)}
        textUrl={explainUrl(colDef.id, colDef.scope, 'md', false)}
      />
    );
  }

  // ══ Headline tiles ═════════════════════════════════════════════════════════
  // Undrilled, an indicator the registry gates on credibility shows the figure
  // pooled over the organisations that record it credibly (the builder's
  // `pooledOverCredible`), never the all-organisation pool.
  function tileEntry(id) {
    var pooled = (P.pooledOverCredible || {})[id];
    if (!selOrg && selOpp === null && pooled && pooled.ind) return pooled.ind;
    return entryOf(scopeInd, id);
  }
  function tileSub(t) {
    var pooled = (P.pooledOverCredible || {})[t.id];
    if (!selOrg && selOpp === null && pooled && pooled.ind)
      return (
        (pooled.llos && pooled.llos.length
          ? pooled.llos.join(' + ') + ' only'
          : 'no credible recorder') + (t.sub ? ' · ' + t.sub : '')
      );
    return t.sub;
  }
  var TILE_BAR = { green: '#15803d', yellow: '#b45309', red: '#b91c1c' };
  function Tiles() {
    if (!TILES.length) return null;
    var prev =
      historyPoints.length >= 2
        ? historyPoints[historyPoints.length - 2]
        : null;
    var cur = prev ? historyPoints[historyPoints.length - 1] : null;
    return (
      <R.HeadlineTiles
        tiles={TILES.map(function (t) {
          var e = tileEntry(t.id);
          var m = M_BY_ID[t.id] || {};
          // A rate tile gets a value bar: its band's colour when banded,
          // neutral grey otherwise, with a tick where a target exists.
          var scored = !!(
            t.pct &&
            e &&
            e.value !== null &&
            e.value !== undefined &&
            e.band !== 'insufficient'
          );
          return {
            spec: t,
            entry: e,
            progress: t.pct
              ? scored
                ? 100 * Math.max(0, Math.min(1, Number(e.value)))
                : 0
              : null,
            progressColour: (scored && TILE_BAR[e.band]) || '#6b7280',
            progressTarget:
              t.pct && t.target !== undefined && t.target !== null
                ? 100 * Number(t.target)
                : null,
            direction: m.direction || null,
            onLabelClick: function () {
              openDef(t.id, 'programme');
            },
            // An untargeted tile fills the same second-line slot as
            // "target X", so every tile in the row lines up.
            sub:
              tileSub(t) ||
              (t.target === undefined || t.target === null ? 'no target' : ''),
            previous: prev ? prev.ind && prev.ind[t.id] : null,
            current: cur ? (cur.ind && cur.ind[t.id]) || null : null,
            previousDate: prev ? prev.date : null,
            minDenominator: (M_BY_ID[t.id] || {}).min_denominator || MIN_DEN,
          };
        })}
      />
    );
  }

  // ══ Scorecard tables ═══════════════════════════════════════════════════════
  var COLS = LAYOUT.columns;
  var GROUPS = LAYOUT.groups;

  // ── Legend, attention cell and trend card, drawn here for legibility ──────
  // Presentation only: every band is still the engine's grade. The legend
  // states each banded column's rule from its measure's own `bands` (the
  // thresholds the builder grades on), so it reads the same for any registry.
  function bandNum(m, v) {
    var u = m && m.unit;
    return String(Number(v)) + (u === '%' ? '%' : u ? ' ' + u : '');
  }
  function bandRule(c) {
    var m = M_BY_ID[c.id] || {};
    var b = m.bands;
    if (!b || b.length < 2 || b[0] === null || b[1] === null) return null;
    var hi = bandNum(m, b[0]),
      lo = bandNum(m, b[1]);
    if (m.direction === 'lower')
      return {
        watch: c.label + ' ' + hi + '–' + lo + ' (target ≤ ' + hi + ')',
        off: c.label + ' > ' + lo,
      };
    return {
      watch: c.label + ' ' + lo + '–' + hi + ' (target ≥ ' + hi + ')',
      off: c.label + ' < ' + lo,
    };
  }
  // Each band's rule rides on its legend chip as a tooltip, built from the
  // measures' own thresholds.
  var BAND_RULES = COLS.map(bandRule).filter(Boolean);
  var WATCH_RULE = BAND_RULES.length
    ? BAND_RULES.map(function (b) {
        return b.watch;
      }).join(' · ')
    : undefined;
  var OFF_RULE = BAND_RULES.length
    ? BAND_RULES.map(function (b) {
        return b.off;
      }).join(' · ')
    : undefined;
  function Legend(props) {
    return (
      <div className="px-4 py-2 text-sm text-gray-700 border-b border-gray-100 space-y-1">
        <div className="flex items-center gap-x-4 gap-y-1 flex-wrap">
          <span
            className={'whitespace-nowrap' + (OFF_RULE ? ' cursor-help' : '')}
            title={OFF_RULE}
          >
            <span className="inline-block w-2.5 h-2.5 rounded-sm bg-red-100 border border-red-400 mr-1 align-middle"></span>
            Off target
          </span>
          <span
            className="whitespace-nowrap cursor-help"
            title={
              'Short of target, not yet off target' +
              (WATCH_RULE ? ' — ' + WATCH_RULE : '')
            }
          >
            <span className="inline-block w-2.5 h-2.5 rounded-sm bg-amber-100 border border-amber-400 mr-1 align-middle"></span>
            Watch
          </span>
          <span className="whitespace-nowrap">
            Uncoloured: on target or no target
          </span>
          <span className="whitespace-nowrap">
            {'n<' + props.minDenominator + ': too few records'}
          </span>
          {props.marks && props.marks.stale ? (
            <span
              className="whitespace-nowrap cursor-help"
              title={
                'Last visit more than ' +
                STALE +
                ' days before ' +
                R.dateLbl(asOf)
              }
            >
              <span className="text-red-700 font-semibold mr-1">
                {R.dateLbl(props.marks.stale)}
              </span>
              {'no visit > ' + STALE + ' days'}
            </span>
          ) : null}
          {props.marks && props.marks.done ? (
            <span
              className="whitespace-nowrap cursor-help"
              title={FINISHED_TIP}
            >
              <span className="text-emerald-700 font-semibold mr-1">
                finished
              </span>
              {'every ' + ENT.name + ' done, no visit due'}
            </span>
          ) : null}
          {props.marks && props.marks.dash ? (
            <span className="whitespace-nowrap">
              <span className="mr-1">—</span>no data yet
            </span>
          ) : null}
          <span className="ml-auto whitespace-nowrap">{props.right}</span>
        </div>
      </div>
    );
  }
  function AttnCell(props) {
    if (props.reds || props.yellows)
      return <R.AttentionCell reds={props.reds} yellows={props.yellows} />;
    return (
      <td className="px-1.5 py-2 text-right">
        <span className="text-gray-600">0</span>
      </td>
    );
  }
  // The library's trend card at a legible label size: same points, band word
  // and point tooltips ("As of <date>: <value> (n = <n>)").
  function TrendCardL(e) {
    var r = !!e.pct;
    var a = e.target !== null && e.target !== undefined;
    var o = a ? Number(e.target) : 0;
    var pts = e.points;
    var W = e.wide;
    var l = (pts || []).map(function (p) {
      var t = p.entry;
      if (!t || t.value === null || t.value === undefined) return null;
      if (t.band === 'insufficient' || t.band === 'notcredible') return null;
      return { v: Number(t.value), e: t, date: p.date, n: t.n };
    });
    var s = l.length;
    var u = l.filter(Boolean);
    var last = u.length ? u[u.length - 1].e : null;
    var body;
    if (pts === null || e.loading)
      body = (
        <div
          className="relative rounded bg-gray-50 animate-pulse"
          style={{ height: 112 }}
          aria-busy="true"
        >
          <div className="absolute inset-x-3 top-1/2 border-t border-dashed border-gray-200"></div>
          <div className="absolute inset-0 flex items-center justify-center text-xs text-gray-500">
            Loading the trend across saved reports…
          </div>
        </div>
      );
    else if (u.length < 2)
      body = (
        <div className="text-xs text-gray-500 py-8 text-center">
          {u.length
            ? 'One report so far — the line builds as reports are saved weekly.'
            : 'No report has enough cases to score this yet.'}
        </div>
      );
    else {
      var lo = a ? o : u[0].v,
        hi = a ? o : u[0].v;
      u.forEach(function (x) {
        lo = Math.min(lo, x.v);
        hi = Math.max(hi, x.v);
      });
      if (r) {
        lo = Math.max(0, Math.floor(10 * (lo - 0.05)) / 10);
        hi = Math.min(1, Math.ceil(10 * (hi + 0.05)) / 10);
      } else {
        lo = Math.floor(lo - 1);
        hi = Math.ceil(hi + 1);
      }
      if (hi <= lo) hi = lo + 1;
      // Wide (one indicator in focus): a date tick per saved report.
      var L = W ? 48 : 34,
        RR = W ? 952 : 166,
        TOP = W ? 20 : 14,
        BOT = W ? 160 : 90,
        FS = 11;
      var px = function (i) {
        return L + (s > 1 ? ((RR - L) * i) / (s - 1) : (RR - L) / 2);
      };
      var py = function (v) {
        return BOT - ((v - lo) / (hi - lo)) * (BOT - TOP);
      };
      var fmtAx = function (v) {
        return r ? Math.round(100 * v) + '%' : Number(v).toFixed(0);
      };
      var path = '',
        on = false;
      l.forEach(function (x, i) {
        if (x) {
          path +=
            (on ? 'L' : 'M') +
            px(i).toFixed(1) +
            ' ' +
            py(x.v).toFixed(1) +
            ' ';
          on = true;
        } else on = false;
      });
      var b = u[u.length - 1];
      var bi = l.lastIndexOf(b);
      var ticks = [lo, (lo + hi) / 2, hi];
      var xt = W
        ? l.map(function (x, i) {
            return i;
          })
        : [0, Math.floor((s - 1) / 2), s - 1];
      var col = R.bandColour(b.e.band);
      body = (
        <svg
          viewBox={W ? '0 0 960 184' : '0 0 170 112'}
          className="w-full h-auto block"
          role="img"
          aria-label={e.label}
        >
          {ticks.map(function (v) {
            return (
              <g key={'y' + v}>
                <line x1={L} x2={RR} y1={py(v)} y2={py(v)} stroke="#eeeef4" />
                <text
                  x={L - 4}
                  y={py(v) + 4}
                  fontSize={FS}
                  fill="#6b7280"
                  textAnchor="end"
                >
                  {fmtAx(v)}
                </text>
              </g>
            );
          })}
          {a ? (
            <line
              x1={L}
              x2={RR}
              y1={py(o)}
              y2={py(o)}
              stroke="#9ca3af"
              strokeDasharray="3 3"
            />
          ) : null}
          {a ? (
            <text
              x={RR}
              y={py(o) - 3}
              fontSize={FS}
              fill="#6b7280"
              textAnchor="end"
            >
              {'target ' + fmtAx(o)}
            </text>
          ) : null}
          <path
            d={path}
            fill="none"
            stroke="#4f46e5"
            strokeWidth="2"
            strokeLinejoin="round"
          />
          {l.map(function (x, i) {
            if (!x) return null;
            var big = i === bi;
            return (
              <circle
                key={x.date}
                cx={px(i)}
                cy={py(x.v)}
                r={big ? (W ? 6 : 4.5) : W ? 4 : 3}
                fill={big ? col : '#4f46e5'}
                stroke="#fff"
                strokeWidth="1.5"
              >
                <title>
                  {'As of ' +
                    R.dateLbl(x.date) +
                    ': ' +
                    e.format(x.e) +
                    ' (n = ' +
                    R.nCount(x.n) +
                    ')'}
                </title>
              </circle>
            );
          })}
          {W
            ? (function () {
                // Wide only: a small value label above each point so a still
                // frame reads the series. Always keep the first, the first
                // point below target and the last; skip any other label that
                // would collide with one already placed.
                var fb = -1;
                if (a)
                  l.forEach(function (x, i) {
                    if (x && fb < 0 && x.v < o) fb = i;
                  });
                var fi = l.indexOf(u[0]);
                var cand = [];
                l.forEach(function (x, i) {
                  if (!x) return;
                  var txt = e.format(x.e);
                  var w = String(txt).length * 6.2;
                  var anc = i === fi ? 'start' : i === bi ? 'end' : 'middle';
                  var x0 =
                    anc === 'start'
                      ? px(i) - 4
                      : anc === 'end'
                        ? px(i) + 4 - w
                        : px(i) - w / 2;
                  var y = py(x.v) - 9;
                  if (
                    a &&
                    i === bi &&
                    px(i) + 4 - w < RR &&
                    Math.abs(y - (py(o) - 3)) < 12
                  )
                    y = py(x.v) + 18;
                  cand.push({
                    i: i,
                    t: txt,
                    anc: anc,
                    x:
                      anc === 'start'
                        ? px(i) - 4
                        : anc === 'end'
                          ? px(i) + 4
                          : px(i),
                    x0: x0,
                    x1: x0 + w,
                    y: y,
                    must: i === fi || i === fb || i === bi,
                  });
                });
                var placed = [];
                var fits = function (c) {
                  return placed.every(function (p) {
                    return (
                      c.x1 + 4 < p.x0 ||
                      c.x0 > p.x1 + 4 ||
                      Math.abs(c.y - p.y) >= 12
                    );
                  });
                };
                cand.forEach(function (c) {
                  if (c.must) placed.push(c);
                });
                cand.forEach(function (c) {
                  if (!c.must && fits(c)) placed.push(c);
                });
                return placed.map(function (c) {
                  return (
                    <text
                      key={'v' + c.i}
                      x={c.x}
                      y={c.y}
                      fontSize={10.5}
                      fill="#6b7280"
                      textAnchor={c.anc}
                      style={{ pointerEvents: 'none' }}
                    >
                      {c.t}
                    </text>
                  );
                });
              })()
            : null}
          {xt.map(function (i, k) {
            return (
              <text
                key={'x' + k}
                x={px(i)}
                y={W ? 180 : 107}
                fontSize={FS}
                fill="#6b7280"
                textAnchor={i === 0 ? 'start' : i === s - 1 ? 'end' : 'middle'}
              >
                {R.dateLbl((pts || [])[i] && pts[i].date)}
              </text>
            );
          })}
        </svg>
      );
    }
    return (
      <div
        className={
          'bg-white border border-gray-200 rounded-xl px-4 pt-3 pb-2' +
          (W ? ' col-span-full' : '')
        }
      >
        <div className="flex flex-wrap items-baseline justify-between gap-x-2">
          <div
            className="text-sm font-semibold text-gray-900 whitespace-nowrap"
            title={e.title}
          >
            {e.label}
          </div>
          {last && last.band && R.BAND_WORD[last.band] ? (
            <span
              className={
                'text-xs font-semibold whitespace-nowrap ' +
                R.BAND_TEXT[last.band]
              }
            >
              {R.BAND_WORD[last.band]}
            </span>
          ) : null}
        </div>
        {body}
      </div>
    );
  }
  var notCredible = 'Not recorded credibly by this ' + ORG.name;
  function headCell(o) {
    var s = o.table ? sort.sortOf(o.table) : null;
    var on = !!(s && s.key === o.sortKey);
    return (
      <th
        key={o.key}
        title={o.title}
        data-constant-ok={o.constantOk ? 'indicator' : undefined}
        className={
          (o.className ||
            'px-1.5 py-2 text-right font-semibold text-gray-600 whitespace-nowrap') +
          (on ? ' bg-indigo-100 text-indigo-900' : '')
        }
      >
        <span className="inline-flex items-center gap-1">
          {o.def ? (
            <button
              type="button"
              className="font-semibold hover:text-indigo-700 underline decoration-dotted decoration-gray-300 underline-offset-2"
              onClick={function () {
                openDef(o.def, o.scope);
              }}
            >
              {o.label}
            </button>
          ) : (
            o.label
          )}
          {o.table ? (
            <button
              type="button"
              aria-label={'Sort by ' + o.label}
              className={
                'text-[10px] ' +
                (on ? 'text-indigo-600' : 'text-gray-500 hover:text-indigo-600')
              }
              onClick={function () {
                sort.toggle(o.table, o.sortKey);
              }}
            >
              {on ? (s.dir === 'asc' ? '▲' : '▼') : '↕'}
            </button>
          ) : null}
        </span>
      </th>
    );
  }
  // The indicator a row is flagged on: its first Off-target column, else its
  // first Watch column (the engine's bands), or null.
  function flagOf(ind) {
    var hit = null;
    ['red', 'yellow'].some(function (band) {
      return COLS.some(function (c) {
        var e = ind && ind[c.id];
        if (e && e.band === band) hit = { col: c, entry: e };
        return !!hit;
      });
    });
    return hit;
  }
  function ScorecardHead(props) {
    var cap = props.caption;
    return (
      <thead>
        {cap ? (
          <tr className="border-b border-gray-200 bg-gray-50">
            <th
              colSpan={props.lead.length + COLS.length + props.trail.length}
              className="px-3 py-2 text-left text-sm font-normal text-gray-700 whitespace-nowrap"
            >
              <span className="font-semibold text-gray-900">{cap.label}</span>
              {' · ' + cap.count}
              {cap.flag ? (
                <span>
                  {' · ' + cap.flag.label + ' '}
                  <span className="font-semibold text-gray-900">
                    {cap.flag.value}
                  </span>{' '}
                  <span
                    className={'font-semibold ' + R.BAND_TEXT[cap.flag.band]}
                  >
                    {R.BAND_WORD[cap.flag.band]}
                  </span>
                </span>
              ) : null}
              {cap.each ? ' · ' + cap.each : null}
            </th>
          </tr>
        ) : null}
        <tr className="text-[10px] uppercase tracking-wide text-gray-500 border-b border-gray-200">
          {props.lead.map(function (l, i) {
            return <th key={'gl' + i} className="px-3 py-1"></th>;
          })}
          {GROUPS.map(function (g) {
            return (
              <th
                key={g.label}
                className="px-1.5 py-1 text-center border-l border-gray-200"
                colSpan={g.span}
              >
                {g.label}
              </th>
            );
          })}
          {props.trail.map(function (l, i) {
            return <th key={'gt' + i} className="px-1.5 py-1"></th>;
          })}
        </tr>
        <tr className="text-xs text-gray-500 border-b border-gray-100">
          {props.lead.map(function (l, i) {
            return headCell({
              key: 'l' + i,
              label: l.label,
              table: props.table,
              sortKey: l.sortKey,
              className:
                (i === 0 ? 'px-3' : 'px-1.5') +
                ' py-2 text-left font-semibold text-gray-600 whitespace-nowrap',
            });
          })}
          {COLS.map(function (c, i) {
            return headCell({
              key: 'c' + i,
              label: c.label,
              title: c.title,
              def: c.id,
              scope: props.scope,
              table: props.table,
              sortKey: 'col' + i,
              // Everyone on the same figure is a finding, not a redundancy.
              constantOk: true,
            });
          })}
          {props.trail.map(function (l, i) {
            return headCell({
              key: 't' + i,
              label: l.label,
              title: l.title,
              table: props.table,
              sortKey: l.sortKey,
            });
          })}
        </tr>
      </thead>
    );
  }
  // A rate's counts behind it, as the cell's tooltip: "<k> of <n>".
  function countsTitle(m, e) {
    if (!m || m.unit !== '%' || !e || !e.n) return undefined;
    if (e.value === null || e.value === undefined) return undefined;
    if (e.band === 'insufficient') return undefined;
    return (
      R.nCount(Math.round(Number(e.value) * Number(e.n))) +
      ' of ' +
      R.nCount(e.n)
    );
  }
  function cells(ind) {
    return COLS.map(function (c, i) {
      var e = ind && ind[c.id];
      return (
        <R.ScoreCell
          key={i}
          column={c}
          entry={e}
          measure={M_BY_ID[c.id]}
          minDenominator={MIN_DEN}
          notCredibleTitle={notCredible}
          title={c.denOnly ? undefined : countsTitle(M_BY_ID[c.id], e)}
        />
      );
    });
  }
  function colValue(key, ind) {
    var c = COLS[Number(String(key).slice(3))];
    return R.scoreSortValue(c, c && ind && ind[c.id]);
  }
  function sizeOf(ind, fallback) {
    // A group's size is its first count indicator (the registry's own scale
    // measure), else the fallback.
    for (var i = 0; i < MEASURES.length; i++) {
      var e = ind && ind[MEASURES[i].indicator];
      if (
        MEASURES[i].kind === 'count' &&
        e &&
        e.value !== null &&
        e.value !== undefined
      )
        return Number(e.value);
    }
    return fallback;
  }
  // `done`: every case behind the row is finished, so an old last visit is the
  // end of the work, not a lapse -- shown in grey and labelled, never red.
  function lastCell(d, done) {
    var gap = d ? R.daysBetween(d, asOf) : null;
    var stale = !done && gap !== null && gap > STALE;
    return (
      <td
        className={
          'px-1.5 py-2 text-right whitespace-nowrap tabular-nums ' +
          (stale ? 'text-red-700 font-semibold' : 'text-gray-600')
        }
        data-finished={done ? '1' : undefined}
        title={
          done
            ? FINISHED_TIP
            : stale
              ? 'No visits for ' + gap + ' days'
              : undefined
        }
      >
        {d ? R.dateLbl(d) : '—'}
        {done ? (
          <span className="ml-1 text-emerald-700 font-semibold">finished</span>
        ) : null}
      </td>
    );
  }
  var TRAIL = [
    {
      label: 'Last visit',
      sortKey: 'last',
      title:
        'Red: no visit for more than ' +
        STALE +
        ' days' +
        (ENT.done_property
          ? ', unless every ' + ENT.name + ' is finished'
          : ''),
    },
    {
      label: 'Attention',
      sortKey: 'attn',
      title:
        'Indicators Off target (red count); if none, indicators on Watch (amber count)',
    },
  ];
  // Which marks a table's rows actually carry, so the legend lists only those:
  // the oldest red Last-visit date, and whether any cell shows "—".
  function dashEntry(c, e) {
    if (!e) return true;
    if (c.denOnly) return !e.n;
    if (e.band === 'insufficient') return false;
    return e.value === null || e.value === undefined;
  }
  function marksOf(items) {
    var m = { stale: null, dash: false, done: false };
    items.forEach(function (it) {
      var gap = it.last ? R.daysBetween(it.last, asOf) : null;
      if (it.done) m.done = true;
      else if (gap !== null && gap > STALE && (!m.stale || it.last < m.stale))
        m.stale = it.last;
      if (it.lastCell && !it.last) m.dash = true;
      if (
        COLS.some(function (c) {
          return dashEntry(c, it.ind && it.ind[c.id]);
        })
      )
        m.dash = true;
    });
    return m;
  }
  function Table(props) {
    return (
      <R.Card padded={false}>
        <div className="px-4 pt-3 pb-2">
          <R.SectionTitle right={props.right}>{props.title}</R.SectionTitle>
        </div>
        {/* The legend sits above the table, in the same view as the cells
            it explains. */}
        <Legend
          minDenominator={MIN_DEN}
          marks={props.marks}
          right="Click a column name for its definition · ↕ sorts"
        />
        <div className="overflow-x-auto">
          <table className="min-w-full text-xs">
            <ScorecardHead
              caption={props.caption}
              lead={props.lead}
              trail={TRAIL}
              table={props.table}
              scope={props.scope}
            />
            <tbody>{props.children}</tbody>
          </table>
        </div>
      </R.Card>
    );
  }

  function allSame(a) {
    return (
      a.length > 1 &&
      a.every(function (x) {
        return x === a[0];
      })
    );
  }
  function OrgTable() {
    var rows = sort.sortOf('orgs')
      ? R.sortRows(P.byLLO || [], sort.sortOf('orgs'), function (l, key) {
          if (key === 'name') return String(l.llo || '').toLowerCase();
          if (key === 'size') return sizeOf(l.ind, (l.opps || []).length);
          if (key === 'last') return lastVisit.org[l.llo] || null;
          if (key === 'attn') return (l.reds || 0) * 1000 + (l.yellows || 0);
          return colValue(key, l.ind);
        })
      : P.byLLO || [];
    function orgSub(l) {
      var nWorkers = workerRows.filter(function (w) {
        return w.org === l.llo;
      }).length;
      return (
        (l.opps || []).length +
        ((l.opps || []).length === 1 ? ' opportunity' : ' opportunities') +
        ' · ' +
        R.nounCount(nWorkers, WRK)
      );
    }
    // A subline that reads the same on every row says nothing about any row.
    var orgSubSame = allSame(rows.map(orgSub));
    return (
      <Table
        title={R.cap(ORG.plural)}
        right={
          'as of ' + R.dateLbl(asOf) + ' · click a row to open the ' + ORG.name
        }
        lead={[{ label: R.cap(ORG.name), sortKey: 'name' }]}
        table="orgs"
        scope="llo"
        marks={marksOf(
          rows
            .map(function (l) {
              return {
                ind: l.ind,
                last: lastVisit.org[l.llo] || null,
                done: lastVisit.doneOf('org', l.llo),
                lastCell: true,
              };
            })
            .concat([{ ind: P.programInd }]),
        )}
      >
        {rows.map(function (l) {
          return (
            <tr
              key={l.llo}
              className="border-t border-gray-100 cursor-pointer hover:bg-indigo-50"
              onClick={function () {
                setSelOrg(l.llo);
                setSelOpp(null);
                setOpenWorker(null);
              }}
            >
              <td className="px-3 py-2 whitespace-nowrap">
                <div className="font-semibold text-indigo-700">{l.llo}</div>
                {orgSubSame ? null : (
                  <div className="text-gray-500">{orgSub(l)}</div>
                )}
              </td>
              {cells(l.ind)}
              {lastCell(lastVisit.org[l.llo], lastVisit.doneOf('org', l.llo))}
              <AttnCell reds={l.reds || 0} yellows={l.yellows || 0} />
            </tr>
          );
        })}
        <tr className="border-t-2 border-gray-200 bg-gray-50 font-semibold">
          <td className="px-3 py-2 whitespace-nowrap">{'All ' + ORG.plural}</td>
          {cells(P.programInd)}
          <td></td>
          <td></td>
        </tr>
      </Table>
    );
  }

  function OppTable() {
    var list = (P.byOpp || []).filter(function (o) {
      return !selOrg || (o.llo || orgOf(o.opp)) === selOrg;
    });
    var rows = R.sortRows(list, sort.sortOf('opps'), function (o, key) {
      if (key === 'name') return oppLabel(o.opp).toLowerCase();
      if (key === 'size') return o.n || sizeOf(o.ind, 0);
      if (key === 'last') return lastVisit.opp[String(o.opp)] || null;
      if (key === 'attn') {
        var a = R.attention(o.ind);
        return a.reds * 1000 + a.yellows;
      }
      return colValue(key, o.ind);
    });
    function oppSub(o) {
      var org = o.llo || orgOf(o.opp);
      return org && oppLabel(o.opp).indexOf(org) === -1
        ? org + ' · ' + R.nounCount(o.n, ENT)
        : R.nounCount(o.n, ENT);
    }
    var oppSubSame = allSame(rows.map(oppSub));
    return (
      <Table
        title="Opportunities"
        right={'click a row for its ' + WRK.plural}
        lead={[{ label: 'Opportunity', sortKey: 'name' }]}
        table="opps"
        scope="opportunity"
        marks={marksOf(
          rows.map(function (o) {
            return {
              ind: o.ind,
              last: lastVisit.opp[String(o.opp)] || null,
              done: lastVisit.doneOf('opp', String(o.opp)),
              lastCell: true,
            };
          }),
        )}
      >
        {rows.map(function (o) {
          var a = R.attention(o.ind);
          return (
            <tr
              key={o.opp}
              className={
                'border-t border-gray-100 cursor-pointer ' +
                (String(selOpp) === String(o.opp)
                  ? 'bg-indigo-50'
                  : 'hover:bg-indigo-50')
              }
              onClick={function () {
                setSelOpp(String(selOpp) === String(o.opp) ? null : o.opp);
                setOpenWorker(null);
              }}
            >
              <td className="px-3 py-2 whitespace-nowrap">
                <div className="font-semibold text-indigo-700">
                  {oppLabel(o.opp)}
                </div>
                {oppSubSame ? null : (
                  <div className="text-gray-500">{oppSub(o)}</div>
                )}
              </td>
              {cells(o.ind)}
              {lastCell(
                lastVisit.opp[String(o.opp)],
                lastVisit.doneOf('opp', String(o.opp)),
              )}
              <AttnCell reds={a.reds} yellows={a.yellows} />
            </tr>
          );
        })}
      </Table>
    );
  }

  // ── Workers: peer cohorts from the builder (start month, caseload band) ────
  var REVIEW = cfg.worker_review || null;
  function reviewUrl(w) {
    if (!REVIEW || !REVIEW.workflow_id || !REVIEW.run_id) return null;
    return withParams(
      '/labs/workflow/' + REVIEW.workflow_id + '/run/',
      'run_id=' +
        REVIEW.run_id +
        '&flw=' +
        encodeURIComponent(w.key) +
        '&source_run=' +
        (instance && instance.id),
    );
  }
  // A case's human label (`display.entity.label_field`), else its id.
  function caseLabel(c, n) {
    if (R.caseLabel) return R.caseLabel(D, c, n);
    return String((c && c.entity_id) || '').slice(0, n);
  }
  function caseLabelClass(c) {
    var lf = ENT.label_field;
    return lf && c && c[lf] ? 'text-gray-900' : 'font-mono text-gray-600';
  }
  function CaseList(props) {
    var cs = (props.cases || []).slice().sort(function (a, b) {
      return String(b.last_visit_date || '') < String(a.last_visit_date || '')
        ? -1
        : 1;
    });
    if (!cs.length)
      return (
        <div className="text-xs text-gray-500">
          {'No ' + ENT.plural + ' in this report.'}
        </div>
      );
    return (
      <table className="min-w-full text-xs bg-white rounded-lg border border-gray-200">
        <thead>
          <tr className="text-gray-500 border-b border-gray-100">
            <th className="px-2 py-1.5 text-left font-semibold">
              {R.cap(ENT.name)}
            </th>
            {D.case_fields.map(function (f) {
              return (
                <th
                  key={f.field}
                  className="px-2 py-1.5 text-right font-semibold"
                >
                  {f.label}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {cs.map(function (c, i) {
            return (
              <tr key={i} className="border-t border-gray-100">
                <td className={'px-2 py-1.5 ' + caseLabelClass(c)}>
                  {caseLabel(c, 10)}
                  {isDone(c) ? (
                    <span
                      data-finished="1"
                      className="ml-2 font-sans font-semibold text-emerald-700"
                      title={
                        'This ' + ENT.name + ' is finished: no visit is due'
                      }
                    >
                      finished
                    </span>
                  ) : null}
                </td>
                {D.case_fields.map(function (f) {
                  return (
                    <td
                      key={f.field}
                      className="px-2 py-1.5 text-right tabular-nums"
                    >
                      {R.fmtCaseField(f, c[f.field])}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    );
  }
  // ══ The workflow's own actions ══════════════════════════════════════════════
  // Whatever this workflow declares (config.actions -- e.g. "Initiate AI coach"),
  // one button each: for every worker in view, and on each worker's row. The
  // runner previews the action and runs it only on the person's confirm; an agent
  // runs the same actions through the labs MCP (workflow/actions.py).
  var WF_ACTIONS = (view && view.workflowActions) || [];
  // A coaching action (start_ocs_outreach) is only for workers with something to
  // coach: an indicator that makes sense per worker (meta.flw_applicable not
  // false) graded red or yellow. The server briefs each one from the same cells
  // and leaves out anyone with nothing off target.
  function isCoaching(a) {
    return a && a.type === 'start_ocs_outreach';
  }
  function coachable(w) {
    var ind = (w && w.ind) || {};
    return Object.keys(ind).some(function (id) {
      var m = M_BY_ID[id];
      var band = ind[id] && ind[id].band;
      return (
        !!m &&
        m.flw_applicable !== false &&
        (band === 'red' || band === 'yellow')
      );
    });
  }
  function actionRows(a, rows) {
    return isCoaching(a) ? rows.filter(coachable) : rows;
  }
  function runWorkflowAction(key, rows) {
    if (!actions || !actions.runAction || !rows.length) return;
    actions
      .runAction(key, {
        workers: rows.map(function (w) {
          return { key: w.key };
        }),
      })
      .catch(function (e) {
        window.alert(e && e.message ? e.message : String(e));
      });
  }

  function WorkerTable() {
    var list = workerRows.filter(function (w) {
      if (selOpp !== null) return String(w.opp) === String(selOpp);
      if (selOrg) return w.org === selOrg;
      return true;
    });
    var sorted = R.sortRows(list, sort.sortOf('workers'), function (w, key) {
      if (key === 'name') return String(w.name || '').toLowerCase();
      if (key === 'size') return w.n;
      if (key === 'last') return w.last || null;
      if (key === 'attn') return w.reds * 1000 + w.yellows;
      return colValue(key, w.ind);
    });
    var groups = [{ label: null, rows: sorted }];
    // Every row in one opportunity: its name on each row says nothing the
    // header does not, and doubles the row height.
    var oneOpp = list.every(function (w) {
      return list.length && String(w.opp) === String(list[0].opp);
    });
    if (cohortDim !== 'none') {
      var by = {};
      var order = [];
      sorted.forEach(function (w) {
        var k =
          (cohortDim === 'start' ? w.startMonth : w.caseload) || 'Not placed';
        if (!by[k]) {
          by[k] = [];
          order.push(k);
        }
        by[k].push(w);
      });
      order.sort();
      groups = order.map(function (k) {
        return {
          label: (cohortDim === 'start' ? 'Started ' : 'Caseload: ') + k,
          rows: by[k],
        };
      });
    }
    // Every row has the same size: the size column says nothing per row, so it
    // moves into the caption (drilled) or the title ("· 1 community each").
    var sameN =
      list.length > 1 &&
      list.every(function (w) {
        return w.n === list[0].n;
      });
    var width = (sameN ? 1 : 2) + COLS.length + 2;
    // Drilled to one organisation or opportunity: a caption row names it, its
    // size and the indicator it is flagged on.
    var caption = null;
    if (selOpp !== null || selOrg) {
      var fl = flagOf(scopeInd);
      var fm = fl ? M_BY_ID[fl.col.id] : null;
      caption = {
        label: selOpp !== null ? oppLabel(selOpp) : selOrg,
        count: R.nounCount(list.length, WRK),
        flag: fl
          ? {
              label: fl.col.label,
              value: R.fmtValue(fm, fl.entry.value),
              band: fl.entry.band,
            }
          : null,
        each: sameN ? R.nounCount(list[0].n, ENT) + ' each' : null,
      };
    }
    return (
      <Table
        caption={caption}
        title={
          R.cap(WRK.plural) +
          (sameN && !caption
            ? ' · ' + R.nounCount(list[0].n, ENT) + ' each'
            : '')
        }
        right={
          <span className="inline-flex items-center gap-2">
            {WF_ACTIONS.map(function (a) {
              var targets = actionRows(a, list);
              if (!targets.length) return null;
              return (
                <button
                  key={'act:' + a.key}
                  type="button"
                  title={a.description}
                  className="rounded border border-indigo-200 bg-indigo-50 px-2 py-0.5 text-xs font-semibold text-indigo-700 hover:bg-indigo-100"
                  onClick={function () {
                    runWorkflowAction(a.key, targets);
                  }}
                >
                  {a.label +
                    ' · ' +
                    R.nounCount(targets.length, WRK) +
                    (isCoaching(a) ? ' off target or on watch' : '')}
                </button>
              );
            })}
            <span>{R.nounCount(list.length, WRK) + ' · compare with'}</span>
            <select
              className="border border-gray-200 rounded px-1 py-0.5 text-xs"
              value={cohortDim}
              onChange={function (e) {
                setCohortDim(e.target.value);
              }}
            >
              <option value="none">everyone</option>
              <option value="start">started the same month</option>
              <option value="caseload">similar caseload</option>
            </select>
          </span>
        }
        lead={
          sameN
            ? [{ label: R.cap(WRK.name), sortKey: 'name' }]
            : [
                { label: R.cap(WRK.name), sortKey: 'name' },
                { label: R.cap(ENT.plural), sortKey: 'size' },
              ]
        }
        table="workers"
        scope="flw"
        marks={marksOf(
          list.map(function (w) {
            return {
              ind: w.ind,
              last: w.last || null,
              done: w.done,
              lastCell: true,
            };
          }),
        )}
      >
        {groups.map(function (g) {
          var out = [];
          if (g.label)
            out.push(
              <tr key={'g:' + g.label} className="bg-gray-50">
                <td
                  colSpan={width}
                  className="px-3 py-1.5 text-[11px] font-bold uppercase tracking-wide text-gray-600"
                >
                  {g.label + ' · ' + g.rows.length}
                </td>
              </tr>,
            );
          g.rows.forEach(function (w) {
            var open = openWorker === w.key;
            var url = reviewUrl(w);
            out.push(
              <tr
                key={w.key}
                className={
                  'border-t border-gray-100 cursor-pointer ' +
                  (open ? 'bg-indigo-50' : 'hover:bg-gray-50')
                }
                onClick={function () {
                  setOpenWorker(open ? null : w.key);
                }}
              >
                <td className="px-3 py-2 whitespace-nowrap">
                  <span className="font-semibold text-indigo-700">
                    {w.name}
                  </span>
                  {url ? (
                    <a
                      href={url}
                      className="ml-2 text-xs text-indigo-600 hover:underline"
                      onClick={function (ev) {
                        ev.stopPropagation();
                      }}
                    >
                      Review →
                    </a>
                  ) : null}
                  {WF_ACTIONS.map(function (a) {
                    if (isCoaching(a) && !coachable(w)) return null;
                    return (
                      <button
                        key={'act:' + a.key}
                        type="button"
                        title={a.description}
                        className="ml-2 text-xs text-indigo-600 hover:underline"
                        onClick={function (ev) {
                          ev.stopPropagation();
                          runWorkflowAction(a.key, [w]);
                        }}
                      >
                        {a.label}
                      </button>
                    );
                  })}
                  {oneOpp ? null : (
                    <div className="text-gray-500">
                      {oppLabel(w.opp) +
                        (w.org && oppLabel(w.opp).indexOf(w.org) === -1
                          ? ' · ' + w.org
                          : '')}
                    </div>
                  )}
                </td>
                {sameN ? null : (
                  <td className="px-1.5 py-2 text-right tabular-nums text-gray-600">
                    {R.nCount(w.n)}
                  </td>
                )}
                {cells(w.ind)}
                {lastCell(w.last, w.done)}
                <AttnCell reds={w.reds} yellows={w.yellows} />
              </tr>,
            );
            if (open)
              out.push(
                <tr key={w.key + ':cases'} className="bg-indigo-50/40">
                  <td colSpan={width} className="px-3 py-3">
                    <CaseList cases={w.cases} />
                  </td>
                </tr>,
              );
          });
          return out;
        })}
      </Table>
    );
  }

  // ══ Activity and trends ════════════════════════════════════════════════════
  function ChartsRow() {
    var weeks = (P.weekly || {})[scopeKey] || [];
    // Drilled: the headline indicator the scope is flagged on gets a full-width
    // trend with a tick per saved report; the other cards stay as they are.
    var fl = selOrg || selOpp !== null ? flagOf(scopeInd) : null;
    var focus = fl
      ? TILES.filter(function (t) {
          return t.id === fl.col.id;
        })[0] || null
      : null;
    var trends = TILES.filter(function (t) {
      return t !== focus;
    }).slice(0, 4);
    if (focus) trends = [focus].concat(trends);
    return (
      <div>
        <div className="grid grid-cols-1 lg:grid-cols-6 gap-3">
          {focus ? null : (
            <R.WeeklyActivityCard
              weeks={weeks}
              className="lg:col-span-2"
              registeredLabel={R.cap(ENT.plural) + ' registered'}
            />
          )}
          {trends.map(function (t, ti) {
            var m = M_BY_ID[t.id] || {};
            var card = (
              <TrendCardL
                key={t.id}
                wide={t === focus}
                label={t.label}
                title={m.title}
                pct={t.pct}
                target={t.target === undefined ? null : t.target}
                loading={history === null}
                format={function (e) {
                  return R.fmtValue(m, e.value);
                }}
                points={historyPoints.map(function (p) {
                  return {
                    date: p.date,
                    entry: (p.ind && p.ind[t.id]) || null,
                  };
                })}
              />
            );
            if (t !== focus) return card;
            return [
              card,
              <R.WeeklyActivityCard
                key="weekly"
                weeks={weeks}
                className="lg:col-span-2"
                registeredLabel={R.cap(ENT.plural) + ' registered'}
              />,
            ];
          })}
        </div>
        <p className="mt-2 text-xs text-gray-500">
          Activity is counted in the week it happened, to {R.dateLbl(asOf)}.
          Each indicator point is the figure as of a saved report; a gap is a
          report with too few {ENT.plural} to score, not a zero. Dashed line =
          target.
          {D.targets_note ? ' ' + D.targets_note : ''}
        </p>
      </div>
    );
  }

  // ══ Every indicator, grouped by category, with its definition ══════════════
  var sDefsOpen = React.useState(false);
  var defsOpen = sDefsOpen[0],
    setDefsOpen = sDefsOpen[1];
  function Definitions() {
    return (
      <R.Card padded={false}>
        <details
          open={defsOpen}
          onToggle={function (ev) {
            setDefsOpen(ev.currentTarget.open);
          }}
        >
          <summary className="px-4 py-3 cursor-pointer flex items-baseline justify-between gap-2">
            <span className="text-sm font-semibold text-gray-900">
              Indicators and their definitions
            </span>
            <span className="text-xs text-gray-500">
              {MEASURES.length +
                ' indicators · click a name for how it is counted'}
            </span>
          </summary>
          <table className="min-w-full text-sm border-t border-gray-100">
            <tbody>
              {D.categories.map(function (cat) {
                var ms = MEASURES.filter(function (m) {
                  return (m.category || 'Other') === cat;
                }).sort(function (a, b) {
                  return (
                    Number((D.indicators[a.indicator] || {}).order || 0) -
                    Number((D.indicators[b.indicator] || {}).order || 0)
                  );
                });
                if (!ms.length) return null;
                return [
                  <tr key={'c:' + cat} className="bg-gray-50">
                    <td
                      colSpan={4}
                      className="px-4 py-1.5 text-[11px] font-bold uppercase tracking-wide text-gray-600"
                    >
                      {cat}
                    </td>
                  </tr>,
                ].concat(
                  ms.map(function (m) {
                    var d = D.indicators[m.indicator] || {};
                    var e = entryOf(scopeInd, m.indicator);
                    var t = R.targetValue(m, d);
                    return (
                      <tr
                        key={m.indicator}
                        className="border-t border-gray-100"
                      >
                        <td className="px-4 py-2">
                          <button
                            type="button"
                            className="text-left font-medium text-gray-900 hover:text-indigo-700 underline decoration-dotted decoration-gray-300 underline-offset-2"
                            onClick={function () {
                              openDef(m.indicator, 'programme');
                            }}
                          >
                            {d.label || m.title || m.indicator}
                          </button>
                          {d.plain ? (
                            <div className="text-xs text-gray-500">
                              {d.plain}
                            </div>
                          ) : null}
                        </td>
                        <td className="px-2 py-2 text-right tabular-nums">
                          <R.ScoreCellText
                            column={{ id: m.indicator, label: m.title }}
                            entry={e}
                            measure={m}
                            minDenominator={MIN_DEN}
                          />
                        </td>
                        <td className="px-2 py-2 text-right text-xs text-gray-500 whitespace-nowrap">
                          {t !== null ? 'target ' + R.fmtValue(m, t) : ''}
                        </td>
                        <td className="px-4 py-2 text-right">
                          {R.BAND_WORD[e.band] ? (
                            <span
                              className={
                                'px-2 py-0.5 rounded text-xs ' +
                                R.BAND_CLS[e.band]
                              }
                            >
                              {R.BAND_WORD[e.band]}
                            </span>
                          ) : null}
                        </td>
                      </tr>
                    );
                  }),
                );
              })}
            </tbody>
          </table>
        </details>
      </R.Card>
    );
  }

  // ══ Benchmarks (opportunity report): its organisation among the others ═════
  // Read from the benchmark store (/labs/benchmarks/api/<opp>/), which is
  // anonymous by construction: the reader's own organisation apart, the others
  // unnamed. An empty store is a normal state, said in words; only a failed
  // request is an error.
  var oppId = React.useMemo(
    function () {
      var m = search.match(/[?&]opportunity_id=(\d+)/);
      if (m) return Number(m[1]);
      if (instance && instance.opportunity_id)
        return Number(instance.opportunity_id);
      var ids = (definition && definition.opportunity_ids) || [];
      return ids.length ? Number(ids[0]) : null;
    },
    [instance && instance.id],
  );
  var sTab = React.useState('report');
  var tab = sTab[0],
    setTab = sTab[1];
  var sBench = React.useState({ status: 'idle' });
  var bench = sBench[0],
    setBench = sBench[1];
  React.useEffect(
    function () {
      if (!OPP_MODE || tab !== 'benchmarks' || bench.status !== 'idle') return;
      if (oppId === null) {
        setBench({
          status: 'error',
          error: 'this page could not resolve its opportunity',
        });
        return;
      }
      setBench({ status: 'loading' });
      fetch('/labs/benchmarks/api/' + oppId + '/', {
        credentials: 'same-origin',
      })
        .then(function (r) {
          return r
            .json()
            .catch(function () {
              return { error: 'HTTP ' + r.status };
            })
            .then(function (j) {
              return { ok: r.ok, j: j };
            });
        })
        .then(function (res) {
          if (!res.ok)
            throw new Error(res.j.error || 'could not read the benchmark');
          setBench({ status: 'ready', payload: res.j });
        })
        .catch(function (e) {
          setBench({ status: 'error', error: String((e && e.message) || e) });
        });
    },
    [tab, oppId],
  );
  var sOpenRow = React.useState(null);
  var openRow = sOpenRow[0],
    setOpenRow = sOpenRow[1];
  var ownOrg = oppId !== null ? orgOf(oppId) : null;
  var ownLabel = (ownOrg || 'Your ' + ORG.name) + ' (you)';
  // The status-band rule a row is graded by, for a hover title: the registry's
  // bands are [target, watch floor] in the measure's own unit.
  function benchBandRule(m, tgt) {
    var b = (m && m.bands) || [];
    var dir = m && m.direction;
    if (tgt === null || (dir !== 'higher' && dir !== 'lower')) return '';
    var t = R.fmtValue(m, tgt);
    var w = b.length > 1 ? Number(b[1]) : NaN;
    if (isNaN(w)) return 'Target ' + t + ', ' + dir + ' is better';
    var wv = R.fmtValue(m, m.unit === '%' ? w / 100 : w);
    return dir === 'higher'
      ? 'On target ≥ ' +
          t +
          ' · Watch ' +
          wv +
          '–' +
          t +
          ' · Off target < ' +
          wv
      : 'On target ≤ ' +
          t +
          ' · Watch ' +
          t +
          '–' +
          wv +
          ' · Off target > ' +
          wv;
  }

  function benchmarkRows(bp) {
    var cohorts = bp.cohorts || {};
    var cid = Object.keys(cohorts).filter(function (id) {
      var fam = (bp.indicators || {})[id] || {};
      return Object.keys(fam).some(function (sname) {
        return Object.keys(fam[sname]).some(function (i) {
          return fam[sname][i].organisations;
        });
      });
    })[0];
    if (!cid) return { cid: null, groups: [] };
    var fam = bp.indicators[cid] || {};
    var groups = [];
    var byCat = {};
    D.categories.forEach(function (c) {
      byCat[c] = { name: c, rows: [] };
      groups.push(byCat[c]);
    });
    MEASURES.forEach(function (m) {
      var e = (fam[m.series] || {})[m.indicator];
      var orgs = e && e.organisations;
      if (!orgs) return;
      var cat = m.category || 'Other';
      if (!byCat[cat]) {
        byCat[cat] = { name: cat, rows: [] };
        groups.push(byCat[cat]);
      }
      byCat[cat].rows.push({
        m: m,
        orgs: orgs,
        ranked: tiesYoursFirst(
          R.rankOrganisations(m, orgs.own, orgs.others || [], {
            entityPlural: ENT.plural,
          }),
        ),
        target: R.targetValue(m, D.indicators[m.indicator]),
      });
    });
    return {
      cid: cid,
      groups: groups.filter(function (g) {
        return g.rows.length;
      }),
      meta: cohorts[cid] || {},
    };
  }
  // The expanded row's every-organization bars. Drawn here rather than with the
  // library's RankedBars so the label column fits a full "<org> (you)" name and
  // the dashed target line carries its value; same figures, same order.
  // Within a group of equal figures, yours is drawn first: a joint rank shows
  // at its tied position instead of trailing the organizations it ties with.
  function tiesYoursFirst(rk) {
    var ord = (rk.ordered || []).slice();
    var mi = -1;
    ord.forEach(function (o, i) {
      if (o.mine && R.drawable(o.figure)) mi = i;
    });
    if (mi > 0) {
      var v = Number(ord[mi].figure.value);
      var at = mi;
      while (
        at > 0 &&
        R.drawable(ord[at - 1].figure) &&
        Number(ord[at - 1].figure.value) === v
      )
        at--;
      if (at < mi) ord.splice(at, 0, ord.splice(mi, 1)[0]);
    }
    return Object.assign({}, rk, { ordered: ord });
  }
  // One true axis from zero: a percentage indicator always spans 0-100%, so
  // bars and the dashed target sit at their real values, not stretched to the
  // largest figure on the row.
  function axisMax(m, vals, tgt) {
    var mx = Math.max.apply(
      null,
      vals.concat(tgt !== null && tgt !== undefined ? [tgt] : []).concat([0]),
    );
    if (m && m.unit === '%') mx = Math.max(mx, 1);
    return mx > 0 ? mx : 1;
  }
  function isRankedRow(r) {
    return r.m.direction === 'higher' || r.m.direction === 'lower';
  }
  // Scale ticks under the expanded bars: 0 / half / full for a percentage
  // axis, 0 / max otherwise. Labels reuse the indicator's own formatter.
  function axisTicks(m, max) {
    var t = m && m.unit === '%' ? [0, max / 2, max] : [0, max];
    return t.map(function (v) {
      return {
        pct: (100 * v) / max,
        label: String(R.fmtValue(m, v)).replace(/\.0+(?=\D*$)/, ''),
      };
    });
  }
  function MiniBars(props) {
    var W = props.width || 260;
    var H = props.height || 44;
    var rows = props.ranked.ordered || [];
    var vals = rows
      .filter(function (o) {
        return R.drawable(o.figure);
      })
      .map(function (o) {
        return Number(o.figure.value);
      });
    var tgt = props.target;
    var hasTgt = tgt !== null && tgt !== undefined;
    var max = axisMax(props.measure, vals, tgt);
    var n = Math.max(rows.length, 1);
    var bw = Math.min(34, (W - 8 * (n - 1)) / n);
    var u = H - 4;
    return (
      <svg
        width={W}
        height={H}
        viewBox={'0 0 ' + W + ' ' + H}
        role="img"
        aria-label={
          R.cap(ORG.plural) +
          (props.unranked ? ', not ranked' : ', best to worst')
        }
        style={props.unranked ? { opacity: 0.45 } : undefined}
      >
        <line
          x1="0"
          x2={W}
          y1={H - 1.5}
          y2={H - 1.5}
          stroke="#d6d4cc"
          strokeWidth="1"
        />
        {rows.map(function (o, i) {
          var x = i * (bw + 8);
          if (!R.drawable(o.figure))
            return (
              <rect
                key={i}
                x={x + 0.75}
                y={H - 12}
                width={bw - 1.5}
                height={10}
                rx="3"
                fill="none"
                stroke="#b9b7ae"
                strokeWidth="1.5"
                strokeDasharray="3 3"
              />
            );
          var h = Math.max(3, (u * Number(o.figure.value)) / max);
          return (
            <rect
              key={i}
              x={x}
              y={H - 2 - h}
              width={bw}
              height={h}
              rx="3"
              fill={o.mine ? '#4f46e5' : '#c7c5bc'}
            >
              <title>
                {R.fmtValue(props.measure, o.figure.value) +
                  (o.mine && R.BAND_WORD[o.figure.band]
                    ? ' (' + R.BAND_WORD[o.figure.band] + ')'
                    : '')}
              </title>
            </rect>
          );
        })}
        {hasTgt ? (
          <line
            x1="0"
            x2={W}
            y1={H - 2 - (u * tgt) / max}
            y2={H - 2 - (u * tgt) / max}
            stroke="#1d1d24"
            strokeWidth="1.5"
            strokeDasharray="5 4"
          />
        ) : null}
      </svg>
    );
  }
  var BAR_REASON = {
    insufficient: 'too few ' + ENT.plural,
    notcredible: 'not credible',
    notinapp: 'not collected',
    unrecorded: 'not recorded',
    nodata: 'no data',
  };
  function BenchBars(props) {
    var m = props.measure;
    var rows = props.ranked.ordered || [];
    var vals = rows
      .filter(function (o) {
        return R.drawable(o.figure);
      })
      .map(function (o) {
        return Number(o.figure.value);
      });
    var tgt = props.target;
    var max = axisMax(m, vals, tgt);
    var LABEL_W = 230;
    var VALUE_W = 120;
    var GAP = 12;
    var lineLeft =
      'calc(' +
      (LABEL_W + GAP) +
      'px + (100% - ' +
      (LABEL_W + VALUE_W + 2 * GAP) +
      'px) * ' +
      (tgt !== null && tgt !== undefined ? tgt / max : 0) +
      ')';
    var hasTgt = tgt !== null && tgt !== undefined;
    var mineFig = (
      rows.filter(function (o) {
        return o.mine;
      })[0] || {}
    ).figure;
    var mineCls = (mineFig && R.BAND_TEXT[mineFig.band]) || '';
    var mineWord = (mineFig && R.BAND_WORD[mineFig.band]) || '';
    var mineFill = '#4f46e5';
    return (
      <div>
        {hasTgt ? (
          <div style={{ position: 'relative', height: 18 }}>
            <div
              className="text-[11px] font-semibold text-gray-800 whitespace-nowrap"
              style={{
                position: 'absolute',
                left: lineLeft,
                bottom: 2,
                transform: 'translateX(-50%)',
              }}
            >
              {'target ' + R.fmtValue(m, tgt)}
            </div>
          </div>
        ) : null}
        <div style={{ position: 'relative' }} className="flex flex-col gap-2">
          {hasTgt ? (
            <div
              style={{
                position: 'absolute',
                top: -2,
                bottom: 4,
                left: lineLeft,
                borderLeft: '2px dashed #1d1d24',
                zIndex: 1,
              }}
            />
          ) : null}
          {rows.map(function (o, i) {
            var ok = R.drawable(o.figure);
            var w = ok
              ? Math.max(1.5, (100 * Number(o.figure.value)) / max)
              : 0;
            return (
              <div
                key={i}
                className="grid items-center"
                style={{
                  gridTemplateColumns:
                    LABEL_W + 'px minmax(0, 1fr) ' + VALUE_W + 'px',
                  columnGap: GAP,
                  height: 24,
                }}
              >
                <div
                  className={
                    'text-sm whitespace-nowrap ' +
                    (o.mine ? 'font-bold text-indigo-700' : 'text-gray-600')
                  }
                >
                  {o.mine ? props.ownLabel : 'Another ' + ORG.name}
                </div>
                <div className="h-4 rounded bg-gray-100 relative">
                  <div
                    className={'absolute left-0 top-0 bottom-0 rounded'}
                    style={{
                      width: w + '%',
                      background: o.mine ? mineFill : '#c7c5bc',
                    }}
                  />
                </div>
                <div
                  className={
                    'text-sm tabular-nums ' +
                    (ok
                      ? o.mine
                        ? 'font-bold ' + (mineCls || 'text-indigo-700')
                        : 'text-gray-700'
                      : 'italic text-gray-600')
                  }
                >
                  {ok
                    ? o.mine && mineWord
                      ? [
                          R.fmtValue(m, o.figure.value),
                          <span
                            key="st"
                            className={
                              'ml-1.5 px-1.5 py-0.5 rounded text-[11px] font-semibold ' +
                              (R.BAND_CLS[mineFig.band] || '')
                            }
                            title={
                              hasTgt
                                ? 'Status against the target (' +
                                  R.fmtValue(m, tgt) +
                                  ')'
                                : 'Status'
                            }
                          >
                            {mineWord}
                          </span>,
                        ]
                      : R.fmtValue(m, o.figure.value)
                    : (o.figure.value !== null && o.figure.value !== undefined
                        ? R.fmtValue(m, o.figure.value) + ' · '
                        : '') + (BAR_REASON[o.figure.band] || 'no figure')}
                </div>
              </div>
            );
          })}
        </div>
        <div
          className="grid"
          style={{
            gridTemplateColumns:
              LABEL_W + 'px minmax(0, 1fr) ' + VALUE_W + 'px',
            columnGap: GAP,
            height: 18,
            marginTop: 2,
          }}
          aria-hidden="true"
        >
          <div />
          <div style={{ position: 'relative', borderTop: '1px solid #d6d4cc' }}>
            {axisTicks(m, max).map(function (t, i, all) {
              var last = i === all.length - 1;
              return (
                <div
                  key={i}
                  style={{
                    position: 'absolute',
                    top: 0,
                    left: t.pct + '%',
                    transform:
                      i === 0
                        ? 'none'
                        : last
                          ? 'translateX(-100%)'
                          : 'translateX(-50%)',
                    textAlign: i === 0 ? 'left' : last ? 'right' : 'center',
                  }}
                >
                  <div
                    style={{
                      width: 1,
                      height: 4,
                      background: '#b9b7ae',
                      marginLeft: i === 0 ? 0 : 'auto',
                      marginRight: last ? 0 : 'auto',
                    }}
                  />
                  <div
                    className="tabular-nums whitespace-nowrap"
                    style={{
                      fontSize: 10,
                      lineHeight: '12px',
                      color: '#6b7280',
                    }}
                  >
                    {t.label}
                  </div>
                </div>
              );
            })}
          </div>
          <div />
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-gray-600">
          <span className="inline-flex items-center gap-1.5">
            <span
              className="inline-block w-3 h-3 rounded-sm"
              style={{ background: mineFill }}
            />
            {'Yours'}
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span
              className="inline-block w-3 h-3 rounded-sm"
              style={{ background: '#c7c5bc' }}
            />
            {'Another ' + ORG.name}
          </span>
          {hasTgt ? (
            <span className="inline-flex items-center gap-1.5">
              <span
                className="inline-block"
                aria-hidden="true"
                style={{
                  width: 2,
                  height: 14,
                  background:
                    'repeating-linear-gradient(to bottom, #1d1d24 0px, #1d1d24 4px, transparent 4px, transparent 7px)',
                }}
              />
              {'Target'}
            </span>
          ) : null}
        </div>
      </div>
    );
  }
  // Yours against the target, signed: a percentage in points, any other
  // unit in its own unit ("−14.0 points to target").
  function benchGap(m, value, tgt) {
    var d = Number(value) - Number(tgt);
    if (isNaN(d)) return '';
    var s = d > 0 ? '+' : d < 0 ? '−' : '±';
    var pct = /%$/.test(R.fmtValue(m, 0.5));
    return (
      (pct
        ? s + Math.abs(d * 100).toFixed(1) + ' points'
        : s + R.fmtValue(m, Math.abs(d))) + ' to target'
    );
  }
  function Benchmark() {
    if (bench.status === 'idle' || bench.status === 'loading')
      return <R.Loading height={160}>Loading the benchmark…</R.Loading>;
    if (bench.status === 'error')
      return (
        <R.Notice tone="error">
          The benchmark could not be read: {bench.error}. This is a failed
          request, not an empty benchmark.
        </R.Notice>
      );
    var built = benchmarkRows(bench.payload || {});
    if (!built.cid)
      return (
        <R.Notice tone="muted">
          {'No ' +
            ORG.name +
            ' benchmark is published for this opportunity yet: it is not in a benchmark ' +
            'cohort, or its cohort has not been published since its programme report saved a week.'}
        </R.Notice>
      );
    var covVals = [];
    built.groups.forEach(function (g) {
      g.rows.forEach(function (r) {
        covVals.push(
          String(r.ranked.coverage == null ? '' : r.ranked.coverage),
        );
      });
    });
    var sameCov =
      covVals.length > 0 &&
      covVals.every(function (c) {
        return c === covVals[0];
      });
    var oneCov = sameCov ? covVals[0] : null;
    var GRID = sameCov
      ? '300px 120px 150px 280px'
      : '300px 120px 150px 280px minmax(0, 1fr)';
    return (
      <div className="space-y-3">
        <div className="text-sm text-gray-600 max-w-4xl">
          {ownLabel.replace(' (you)', '') +
            ' against the other ' +
            ORG.plural +
            ', as of ' +
            R.dateLbl(built.meta.as_of || (bench.payload || {}).as_of) +
            (sameCov && oneCov
              ? ' · ' +
                (oneCov.indexOf('all ') === 0
                  ? oneCov + ' covered'
                  : 'coverage ' + oneCov)
              : '') +
            '.'}
        </div>
        <R.Card padded={false}>
          <div
            className="grid items-center gap-4 px-5 py-2.5 border-b border-gray-200 text-[11px] uppercase tracking-wide text-gray-600"
            style={{ gridTemplateColumns: GRID }}
          >
            <div>Indicator</div>
            <div>{ownOrg || 'Yours'}</div>
            <div>Rank</div>
            <div>Best → worst</div>
            {sameCov ? null : <div>Coverage</div>}
          </div>
          {built.groups.map(function (g) {
            return (
              <div key={g.name}>
                <div className="px-5 pt-2.5 pb-1 text-[11px] font-bold uppercase tracking-wide text-gray-700 bg-gray-50 border-b border-gray-100">
                  {g.name}
                </div>
                {g.rows
                  .filter(isRankedRow)
                  .concat(
                    g.rows.filter(function (x) {
                      return !isRankedRow(x);
                    }),
                  )
                  .map(function (r, ri, arr) {
                    var id = r.m.indicator;
                    var open = openRow === id;
                    var own = r.orgs.own;
                    var ok = R.drawable(own);
                    var out = [
                      <button
                        key={id}
                        type="button"
                        aria-expanded={open}
                        onClick={function () {
                          setOpenRow(open ? null : id);
                        }}
                        className={
                          'w-full text-left grid items-center gap-4 px-5 py-2.5 border-b border-gray-100 hover:bg-gray-50 ' +
                          (open ? 'bg-indigo-50/60' : '')
                        }
                        style={{
                          gridTemplateColumns: GRID,
                          position: 'relative',
                        }}
                      >
                        <svg
                          aria-hidden="true"
                          viewBox="0 0 10 10"
                          width="10"
                          height="10"
                          className="text-gray-500"
                          style={{
                            position: 'absolute',
                            left: 6,
                            top: '50%',
                            marginTop: -5,
                            transform: open ? 'rotate(90deg)' : 'none',
                            transition: 'transform 150ms',
                          }}
                        >
                          <path
                            d="M3 1.5 L7 5 L3 8.5"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="1.8"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                          />
                        </svg>
                        <div className="min-w-0">
                          <div
                            className="text-sm font-semibold text-gray-900 truncate"
                            title={r.m.title || id}
                          >
                            {(D.indicators[id] || {}).label || r.m.title || id}
                          </div>
                          <div
                            className="text-xs text-gray-600"
                            title={benchBandRule(r.m, r.target) || undefined}
                          >
                            {(r.m.direction === 'lower'
                              ? 'lower is better'
                              : r.m.direction === 'higher'
                                ? 'higher is better'
                                : 'not ranked') +
                              (r.target !== null
                                ? ' · target ' + R.fmtValue(r.m, r.target)
                                : '')}
                          </div>
                        </div>
                        <div
                          className={
                            'text-lg font-bold tabular-nums ' +
                            (ok
                              ? R.BAND_TEXT[own.band] || 'text-gray-900'
                              : 'text-gray-500')
                          }
                        >
                          {ok ? R.fmtValue(r.m, own.value) : '—'}
                          {ok && r.target !== null ? (
                            <div
                              className="text-xs font-semibold text-gray-700 whitespace-nowrap"
                              data-target-gap="1"
                            >
                              {benchGap(r.m, own.value, r.target)}
                            </div>
                          ) : null}
                        </div>
                        <div className="text-sm text-gray-700">
                          {!own
                            ? '—'
                            : r.ranked.rank === null
                              ? 'no usable figure'
                              : !r.ranked.ranked
                                ? '—'
                                : (r.ranked.tied ? 'joint ' : '') +
                                  R.ordinal(r.ranked.rank) +
                                  ' of ' +
                                  r.ranked.scored}
                        </div>
                        <MiniBars
                          unranked={!isRankedRow(r)}
                          measure={r.m}
                          ranked={r.ranked}
                          target={r.target}
                          width={280}
                        />
                        {sameCov ? null : (
                          <div className="text-xs text-gray-600">
                            {r.ranked.coverage}
                          </div>
                        )}
                      </button>,
                    ];
                    if (
                      !isRankedRow(r) &&
                      (ri === 0 || isRankedRow(arr[ri - 1]))
                    )
                      out.unshift(
                        <div
                          key={id + ':unranked-hdr'}
                          className="px-5 pt-2 pb-1 text-[11px] uppercase tracking-wide text-gray-500 border-b border-gray-100"
                          title={
                            'These indicators have no better or worse direction, so ' +
                            ORG.plural +
                            ' are shown for comparison, not ranked'
                          }
                        >
                          {'Not ranked · for comparison only'}
                        </div>,
                      );
                    if (open)
                      out.push(
                        <div
                          key={id + ':detail'}
                          className="px-5 py-4 bg-indigo-50/40 border-b border-gray-200"
                          style={{
                            borderTop: '1px solid #c7d2fe',
                            boxShadow: 'inset 0 2px 3px rgba(17, 24, 39, 0.04)',
                          }}
                        >
                          <BenchBars
                            measure={r.m}
                            ranked={r.ranked}
                            ownLabel={ownLabel}
                            target={r.target}
                          />
                          <div className="mt-3">
                            <button
                              type="button"
                              className="rounded border border-indigo-200 bg-indigo-50 px-2 py-0.5 text-xs font-semibold text-indigo-700 hover:bg-indigo-100"
                              title={
                                'Open the Report tab with its ' +
                                WRK.name +
                                ' table sorted by this indicator, worst first'
                              }
                              onClick={function () {
                                var ci = COLS.findIndex(function (c) {
                                  return c.id === id;
                                });
                                if (ci >= 0)
                                  sort.set('workers', {
                                    key: 'col' + ci,
                                    dir:
                                      r.m.direction === 'lower'
                                        ? 'desc'
                                        : 'asc',
                                  });
                                setTab('report');
                                setTimeout(function () {
                                  var el =
                                    document.getElementById('ir-worker-table');
                                  if (el) el.scrollIntoView({ block: 'start' });
                                }, 60);
                              }}
                            >
                              {'See ' + WRK.plural + ' →'}
                            </button>
                          </div>
                          {R.fmtValue(r.m, entryOf(P.programInd, id).value) ===
                          R.fmtValue(r.m, (own || {}).value) ? null : (
                            <div className="mt-3 text-xs text-gray-600">
                              This opportunity on its own:{' '}
                              <b className="text-gray-800">
                                {R.fmtValue(
                                  r.m,
                                  entryOf(P.programInd, id).value,
                                )}
                              </b>
                              . The {ORG.name} figure pools every opportunity it
                              runs in this programme.
                            </div>
                          )}
                        </div>,
                      );
                    return out;
                  })}
              </div>
            );
          })}
        </R.Card>
      </div>
    );
  }

  // ══ Render ═════════════════════════════════════════════════════════════════
  function saveRun() {
    if (!view || !view.complete) return;
    view.complete({
      confirm:
        'Save this week? Its figures become final, and every opportunity report that follows this report receives its own slice.',
    });
  }
  var handedDown = meta.handed_down_from || null;
  var title = OPP_MODE
    ? oppName(oppId) ||
      (ownOrg ? ownOrg + ' · ' : '') +
        (oppId !== null ? 'opportunity ' + oppId : 'this opportunity')
    : D.title || (definition && definition.name) || 'Programme report';
  var crumbs = OPP_MODE ? (
    <span>
      {(D.title ? D.title + ' · ' : '') +
        (ownOrg && oppName(oppId) ? ownOrg + ' · ' : '') +
        'opportunity report'}
    </span>
  ) : (
    <span>
      <button
        type="button"
        className={
          selOrg || selOpp !== null ? 'text-indigo-600 hover:underline' : ''
        }
        onClick={function () {
          setSelOrg(null);
          setSelOpp(null);
          setOpenWorker(null);
        }}
      >
        Programme
      </button>
      {selOrg ? (
        <span>
          {' › '}
          <button
            type="button"
            className={selOpp !== null ? 'text-indigo-600 hover:underline' : ''}
            onClick={function () {
              setSelOpp(null);
            }}
          >
            {selOrg}
          </button>
        </span>
      ) : null}
      {selOpp !== null ? ' › ' + oppLabel(selOpp) : null}
    </span>
  );
  // The header counts the scope on screen: drilled into a partner or an
  // opportunity it counts THAT scope's cases, visits and workers, never the
  // programme's. Cases from the case index, visits from the scope's weekly
  // activity (the same cut hand_down.py uses for an opportunity's slice).
  var DRILLED = !!selOrg || selOpp !== null;
  function inScope(opp, org) {
    if (selOpp !== null) return String(opp) === String(selOpp);
    if (selOrg) return (org || orgOf(opp)) === selOrg;
    return true;
  }
  var scopeCases = DRILLED
    ? caseIndex.filter(function (c) {
        return inScope(c.opportunity_id, c.llo);
      }).length
    : meta.cases;
  var scopeWeeks = (P.weekly || {})[scopeKey];
  var scopeVisits = !DRILLED
    ? meta.visits
    : scopeWeeks
      ? scopeWeeks.reduce(function (a, w) {
          return a + (Number(w.visits) || 0);
        }, 0)
      : null;
  var scopeWorkers = workerRows.filter(function (w) {
    return inScope(w.opp, w.org);
  }).length;
  var scopeOppList = selOrg
    ? (P.byOpp || []).filter(function (o) {
        return (o.llo || orgOf(o.opp)) === selOrg;
      })
    : [];
  var scopeOpps = scopeOppList.length;
  // A partner with exactly one opportunity: that opportunity IS the partner's
  // slice, so a one-row table of it repeats the tiles. The header names it and
  // the workers table sits directly under the tiles.
  var soleOpp =
    selOrg && selOpp === null && scopeOpps === 1 ? scopeOppList[0] : null;
  var OPP_NOUN = { name: 'opportunity', plural: 'opportunities' };
  var cache = live.cache || {};
  return (
    <div className="p-4 space-y-4 bg-gray-50">
      <DefModal />
      <R.ReportHeader
        crumbs={crumbs}
        title={
          OPP_MODE
            ? title
            : selOpp !== null
              ? oppLabel(selOpp)
              : selOrg || title
        }
        subtitle={
          ready ? (
            <span>
              <b>{R.nounCount(scopeCases, ENT)}</b>
              {scopeVisits !== null && scopeVisits !== undefined ? (
                <span>
                  {' · '}
                  <b>{R.nCount(scopeVisits)}</b> visits
                </span>
              ) : null}
              {' · '}
              <b>{R.nounCount(scopeWorkers, WRK)}</b>
              {selOrg && selOpp === null ? (
                <span>
                  {' · '}
                  <b>{R.nounCount(scopeOpps, OPP_NOUN)}</b>
                  {soleOpp ? ': ' + oppLabel(soleOpp.opp) : null}
                </span>
              ) : HAS_ORGS && !DRILLED && !OPP_MODE ? (
                <span>
                  {' · '}
                  <b>{R.nounCount((P.byLLO || []).length, ORG)}</b>
                </span>
              ) : null}
              {' · figures as of ' + R.dateLbl(asOf)}
            </span>
          ) : null
        }
        badges={
          <span className="flex flex-wrap items-center gap-2">
            {isCompleted ? (
              <R.Pill tone="muted">Final report</R.Pill>
            ) : (
              <R.Pill tone="current">Live · not saved</R.Pill>
            )}
            {handedDown ? (
              <R.Pill
                tone="source"
                title={
                  'Handed down from programme report ' +
                  handedDown.workflow_id +
                  ', run ' +
                  handedDown.run_id
                }
              >
                From the programme report
              </R.Pill>
            ) : null}
            {meta.synthetic ? (
              <R.Pill tone="muted">Built on synthetic data</R.Pill>
            ) : null}
          </span>
        }
        actions={
          !isCompleted && view && view.complete ? (
            <R.Button primary={true} onClick={saveRun} disabled={!ready}>
              Save this week
            </R.Button>
          ) : null
        }
      />
      {!isCompleted && (cache.cold_cache || cache.partial_cache) ? (
        <R.Notice tone="warn">
          These live figures were computed from an incomplete visit cache;
          reload in a minute for the full set.
        </R.Notice>
      ) : null}
      {!ready ? (
        live.status === 'error' ? (
          <R.Notice
            tone="error"
            onRetry={function () {
              setTries(tries + 1);
            }}
          >
            The figures could not be read: {live.error}
          </R.Notice>
        ) : (
          <R.Loading height={220}>Computing the programme's figures…</R.Loading>
        )
      ) : OPP_MODE ? (
        <div className="space-y-4">
          <R.Tabs
            tabs={[
              { id: 'report', label: 'Report' },
              { id: 'benchmarks', label: 'Benchmarks' },
            ]}
            active={tab}
            onChange={setTab}
          />
          {tab === 'benchmarks' ? (
            <Benchmark />
          ) : (
            <div className="space-y-4">
              <Tiles />
              <div id="ir-worker-table">
                <WorkerTable />
              </div>
              <ChartsRow />
              <Definitions />
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-4">
          <Tiles />
          {!selOrg && selOpp === null && HAS_ORGS ? <OrgTable /> : null}
          {(!HAS_ORGS && selOpp === null) ||
          (selOrg && selOpp === null && !soleOpp) ||
          selOpp !== null ? (
            <OppTable />
          ) : null}
          {selOrg || selOpp !== null || !HAS_ORGS ? <WorkerTable /> : null}
          <ChartsRow />
          <Definitions />
        </div>
      )}
    </div>
  );
}
