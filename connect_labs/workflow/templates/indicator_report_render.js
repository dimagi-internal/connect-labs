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
      var out = { org: {}, opp: {} };
      caseIndex.forEach(function (c) {
        var d = String(c.last_visit_date || '').slice(0, 10);
        if (!d) return;
        var o = String(c.opportunity_id);
        if (!out.opp[o] || d > out.opp[o]) out.opp[o] = d;
        var g = c.llo || orgOf(c.opportunity_id);
        if (g && (!out.org[g] || d > out.org[g])) out.org[g] = d;
      });
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
          name: f.username || f.flw,
          opp: f.opp,
          org: f.llo || orgOf(f.opp),
          ind: f.ind || {},
          reds: f.reds || 0,
          yellows: f.yellows || 0,
          cases: rows,
          n: rows.length || f.n || 0,
          last: last,
          startMonth: f.startMonth || null,
          caseload: f.caseloadLabel || null,
        };
      });
    },
    [payload],
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
          return {
            spec: t,
            entry: tileEntry(t.id),
            sub: tileSub(t),
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
  var notCredible = 'Not recorded credibly by this ' + ORG.name;
  function headCell(o) {
    var s = o.table ? sort.sortOf(o.table) : null;
    var on = !!(s && s.key === o.sortKey);
    return (
      <th
        key={o.key}
        title={o.title}
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
                (on ? 'text-indigo-600' : 'text-gray-300 hover:text-indigo-600')
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
  function ScorecardHead(props) {
    return (
      <thead>
        <tr className="text-[10px] uppercase tracking-wide text-gray-400 border-b border-gray-200">
          {props.lead.map(function (l, i) {
            return <th key={'gl' + i} className="px-3 py-1"></th>;
          })}
          {GROUPS.map(function (g) {
            return (
              <th
                key={g.label}
                className="px-1.5 py-1 text-center"
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
            });
          })}
          {props.trail.map(function (l, i) {
            return headCell({
              key: 't' + i,
              label: l.label,
              table: props.table,
              sortKey: l.sortKey,
            });
          })}
        </tr>
      </thead>
    );
  }
  function cells(ind) {
    return COLS.map(function (c, i) {
      return (
        <R.ScoreCell
          key={i}
          column={c}
          entry={ind && ind[c.id]}
          measure={M_BY_ID[c.id]}
          minDenominator={MIN_DEN}
          notCredibleTitle={notCredible}
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
  function lastCell(d) {
    var gap = d ? R.daysBetween(d, asOf) : null;
    var stale = gap !== null && gap > STALE;
    return (
      <td
        className={
          'px-1.5 py-2 text-right whitespace-nowrap tabular-nums ' +
          (stale ? 'text-red-700 font-semibold' : 'text-gray-600')
        }
        title={stale ? 'No visits for ' + gap + ' days' : undefined}
      >
        {d ? R.dateLbl(d) : '—'}
      </td>
    );
  }
  var TRAIL = [
    { label: 'Last visit', sortKey: 'last' },
    { label: 'Attention', sortKey: 'attn' },
  ];
  function Table(props) {
    return (
      <R.Card padded={false}>
        <div className="px-4 pt-3 pb-2">
          <R.SectionTitle right={props.right}>{props.title}</R.SectionTitle>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full text-xs">
            <ScorecardHead
              lead={props.lead}
              trail={TRAIL}
              table={props.table}
              scope={props.scope}
            />
            <tbody>{props.children}</tbody>
          </table>
        </div>
        <R.ScorecardLegend
          minDenominator={MIN_DEN}
          right="Click a column name for its definition · the arrow sorts"
        />
      </R.Card>
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
    return (
      <Table
        title={R.cap(ORG.plural)}
        right={
          'as of ' + R.dateLbl(asOf) + ' · click a row to open the ' + ORG.name
        }
        lead={[{ label: R.cap(ORG.name), sortKey: 'name' }]}
        table="orgs"
        scope="llo"
      >
        {rows.map(function (l) {
          var nWorkers = workerRows.filter(function (w) {
            return w.org === l.llo;
          }).length;
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
                <div className="text-gray-400">
                  {(l.opps || []).length +
                    ((l.opps || []).length === 1
                      ? ' opportunity'
                      : ' opportunities') +
                    ' · ' +
                    R.nounCount(nWorkers, WRK)}
                </div>
              </td>
              {cells(l.ind)}
              {lastCell(lastVisit.org[l.llo])}
              <R.AttentionCell reds={l.reds || 0} yellows={l.yellows || 0} />
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
      if (key === 'name') return Number(o.opp);
      if (key === 'size') return o.n || sizeOf(o.ind, 0);
      if (key === 'last') return lastVisit.opp[String(o.opp)] || null;
      if (key === 'attn') {
        var a = R.attention(o.ind);
        return a.reds * 1000 + a.yellows;
      }
      return colValue(key, o.ind);
    });
    return (
      <Table
        title="Opportunities"
        right={'click a row for its ' + WRK.plural}
        lead={[{ label: 'Opportunity', sortKey: 'name' }]}
        table="opps"
        scope="opportunity"
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
                  {'Opportunity ' + o.opp}
                </div>
                <div className="text-gray-400">
                  {(o.llo || orgOf(o.opp)
                    ? (o.llo || orgOf(o.opp)) + ' · '
                    : '') + R.nounCount(o.n, ENT)}
                </div>
              </td>
              {cells(o.ind)}
              {lastCell(lastVisit.opp[String(o.opp)])}
              <R.AttentionCell reds={a.reds} yellows={a.yellows} />
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
                <td className="px-2 py-1.5 font-mono text-gray-600">
                  {String(c.entity_id || '').slice(0, 10)}
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
    var width = 2 + COLS.length + 2;
    return (
      <Table
        title={R.cap(WRK.plural)}
        right={
          <span className="inline-flex items-center gap-2">
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
        lead={[
          { label: R.cap(WRK.name), sortKey: 'name' },
          { label: R.cap(ENT.plural), sortKey: 'size' },
        ]}
        table="workers"
        scope="flw"
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
                  <div className="text-gray-400">
                    {'opp ' + w.opp + (w.org ? ' · ' + w.org : '')}
                  </div>
                </td>
                <td className="px-1.5 py-2 text-right tabular-nums text-gray-600">
                  {R.nCount(w.n)}
                </td>
                {cells(w.ind)}
                {lastCell(w.last)}
                <R.AttentionCell reds={w.reds} yellows={w.yellows} />
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
    var trends = TILES.slice(0, 4);
    return (
      <div>
        <div className="grid grid-cols-1 lg:grid-cols-6 gap-3">
          <R.WeeklyActivityCard
            weeks={weeks}
            className="lg:col-span-2"
            registeredLabel={R.cap(ENT.plural) + ' registered'}
          />
          {trends.map(function (t) {
            var m = M_BY_ID[t.id] || {};
            return (
              <R.TrendCard
                key={t.id}
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
          })}
        </div>
        <p className="mt-2 text-xs text-gray-400">
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
                            {m.title || m.indicator}
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
        ranked: R.rankOrganisations(m, orgs.own, orgs.others || [], {
          entityPlural: ENT.plural,
        }),
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
    var GRID = '300px 120px 150px 280px minmax(0, 1fr)';
    return (
      <div className="space-y-3">
        <div className="text-sm text-gray-600 max-w-4xl">
          {ownLabel.replace(' (you)', '') +
            " against the programme's other " +
            ORG.plural +
            ', as of ' +
            R.dateLbl(built.meta.as_of || (bench.payload || {}).as_of) +
            '. Each small chart is every ' +
            ORG.name +
            ', best to worst: yours in blue, the others unnamed and re-sorted on every row. An outline has no usable figure; the coverage column says why.'}
        </div>
        <R.Card padded={false}>
          <div
            className="grid items-center gap-4 px-5 py-2.5 border-b border-gray-200 text-[11px] uppercase tracking-wide text-gray-500"
            style={{ gridTemplateColumns: GRID }}
          >
            <div>Indicator</div>
            <div>{ownOrg || 'Yours'}</div>
            <div>Rank</div>
            <div>Best → worst</div>
            <div>Coverage</div>
          </div>
          {built.groups.map(function (g) {
            return (
              <div key={g.name}>
                <div className="px-5 pt-2.5 pb-1 text-[11px] font-bold uppercase tracking-wide text-gray-700 bg-gray-50 border-b border-gray-100">
                  {g.name}
                </div>
                {g.rows.map(function (r) {
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
                      style={{ gridTemplateColumns: GRID }}
                    >
                      <div className="min-w-0">
                        <div className="text-sm font-semibold text-gray-900 truncate">
                          {r.m.title || id}
                        </div>
                        <div className="text-xs text-gray-500">
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
                            : 'text-gray-400')
                        }
                      >
                        {ok ? R.fmtValue(r.m, own.value) : '—'}
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
                      <R.MiniRankBars
                        ranked={r.ranked}
                        target={r.target}
                        width={280}
                      />
                      <div className="text-xs text-gray-500">
                        {r.ranked.coverage}
                      </div>
                    </button>,
                  ];
                  if (open)
                    out.push(
                      <div
                        key={id + ':detail'}
                        className="px-5 py-4 bg-indigo-50/40 border-b border-gray-200"
                      >
                        <R.RankedBars
                          measure={r.m}
                          ranked={r.ranked}
                          ownLabel={ownLabel}
                          target={r.target}
                        />
                        <div className="mt-3 text-xs text-gray-500">
                          This opportunity on its own:{' '}
                          <b className="text-gray-800">
                            {R.fmtValue(r.m, entryOf(P.programInd, id).value)}
                          </b>
                          . The {ORG.name} figure pools every opportunity it
                          runs in this programme.
                        </div>
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
    ? (ownOrg ? ownOrg + ' · ' : '') +
      (oppId !== null ? 'opportunity ' + oppId : 'this opportunity')
    : D.title || (definition && definition.name) || 'Programme report';
  var crumbs = OPP_MODE ? (
    <span>{(D.title ? D.title + ' · ' : '') + 'opportunity report'}</span>
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
      {selOpp !== null ? ' › opportunity ' + selOpp : null}
    </span>
  );
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
              ? 'Opportunity ' + selOpp
              : selOrg || title
        }
        subtitle={
          ready ? (
            <span>
              <b>
                {R.nounCount(
                  selOrg || selOpp !== null
                    ? sizeOf(scopeInd, meta.cases)
                    : meta.cases,
                  ENT,
                )}
              </b>{' '}
              · <b>{R.nCount(meta.visits)}</b> visits ·{' '}
              <b>{R.nounCount(workerRows.length, WRK)}</b>
              {HAS_ORGS ? (
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
            <R.Pill tone="muted">Report of {R.dateLbl(asOf)}</R.Pill>
            {isCompleted ? (
              <R.Pill tone="final">Final report</R.Pill>
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
              <WorkerTable />
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
          (selOrg && selOpp === null) ||
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
