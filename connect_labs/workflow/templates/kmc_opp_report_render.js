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
  // ══ ONE opportunity's KMC report, for the people who run it ════════════════
  //
  // The programme report's content for this one opportunity -- headline tiles,
  // activity by week, indicator trends across saved reports, a row per field
  // worker on the programme's scorecard -- plus where it sits among anonymous
  // peers. Drawn with the shared report library (window.LabsReport), which is
  // the programme report's own look.
  //
  // ONE PAYLOAD, as on the programme report: the graded output of the
  // semantic-snapshot builder, over this opportunity. A completed run reads it
  // off the run record -- either one this report saved, or one HANDED DOWN from
  // the programme report when that report saved its week (workflow/hand_down.py;
  // `meta.handed_down_from` says which). An in-progress run fetches the same
  // payload as a live preview, which fills this opportunity's visit cache itself
  // if it has gone cold. This file grades nothing.
  //
  // WHY THE WORKER TABLE NAMES WORKERS AND THE PEER SECTION DOES NOT. An
  // opportunity owns its own workers' data, so naming them to the people running
  // it is correct. Peer figures come only from the benchmark store, which exists
  // to cross an opportunity boundary and never carries a worker.
  //
  // ES5 dialect throughout -- no arrows, no destructuring, no computed keys --
  // like every KMC render.
  var R = window.LabsReport;
  var cfg = (definition && definition.config) || {};
  // The registry's `defaults.min_denominator` (spec section 0), for a measure
  // that declares none of its own.
  var MIN_DEN = Number(cfg.min_denominator_default) || 20;
  var COLUMNS = cfg.scorecard_columns || [];
  var GROUPS = cfg.scorecard_groups || [];

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
  // This workflow's own id. The `definition` prop is the record's `data` blob and
  // does not always carry `id`; the run knows its definition, and so does the URL.
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
  function opportunityId() {
    var m = search.match(/[?&]opportunity_id=(\d+)/);
    if (m) return Number(m[1]);
    if (instance && instance.opportunity_id)
      return Number(instance.opportunity_id);
    var ids = (definition && definition.opportunity_ids) || [];
    return ids.length ? Number(ids[0]) : null;
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
        setLive({
          status: 'error',
          payload: null,
          cache: null,
          error: 'could not determine the run id from the page',
        });
        return;
      }
      var cancelled = false;
      setLive({ status: 'loading', payload: null, cache: null, error: null });
      fetch(
        '/labs/workflow/api/run/' +
          runId +
          '/snapshot/preview/' +
          scopeParams(),
        { credentials: 'same-origin' },
      )
        .then(function (r) {
          // A gateway timeout comes back as HTML; say what happened instead of
          // surfacing a JSON parse error.
          return r.json().catch(function () {
            return {
              error:
                r.status === 504 || r.status === 502
                  ? 'the server took too long to compute these figures (HTTP ' +
                    r.status +
                    ')'
                  : 'the server answered HTTP ' + r.status,
            };
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
        // A failed read is an ERROR, never an empty report.
        .catch(function (e) {
          if (!cancelled)
            setLive({
              status: 'error',
              payload: null,
              cache: null,
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

  var MEASURES = (P.cMeasures || []).filter(function (m) {
    return m && m.indicator;
  });
  var M_BY_ID = React.useMemo(
    function () {
      var out = {};
      MEASURES.forEach(function (m) {
        out[m.indicator] = m;
      });
      return out;
    },
    [payload],
  );
  function measureOf(id) {
    return M_BY_ID[id] || { indicator: id, title: id };
  }
  function entryOf(map, id) {
    return (map && map[id]) || { id: id, n: 0, value: null, band: 'nodata' };
  }
  var ind = P.programInd || {};
  var oppId = opportunityId();
  var LLO_MAP = (P.deployment && P.deployment.llo_map) || {};
  var llo = oppId !== null ? LLO_MAP[oppId] || LLO_MAP[String(oppId)] : null;
  var meta = P.meta || {};
  var handedDown = meta.handed_down_from || null;
  var isCompleted = !!(view && view.isCompleted);
  var asOf =
    meta.as_of ||
    (view && view.asOf ? String(view.asOf).slice(0, 10) : '') ||
    new Date().toISOString().slice(0, 10);

  // ══ Saved-report history, for the change lines and the trends ═════════════
  // null while in flight: "one report so far" before it arrives would read as
  // a fact about the data.
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
      var keys = ['programInd', 'meta']
        .map(function (k) {
          return 'snapshot.' + k;
        })
        .join(',');
      var sp = scopeParams();
      fetch(
        '/labs/workflow/api/' +
          defId +
          '/runs/history/' +
          sp +
          (sp ? '&' : '?') +
          'keys=' +
          keys,
        { credentials: 'same-origin' },
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
  // One point per as-of date. A handed-down week and a week saved here can
  // share a date; the later completion wins, as on the programme report. The
  // run in view is a point too.
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
        if (!d || !st.programInd || !Object.keys(st.programInd).length) return;
        byDate[d] = { date: d, ind: st.programInd };
      });
      if (ready && !byDate[asOf])
        byDate[asOf] = { date: asOf, ind: ind, current: true };
      return Object.keys(byDate)
        .sort()
        .map(function (d) {
          return byDate[d];
        });
    },
    [history, payload],
  );

  // The definitions come from the workflow that computes the figures: this one.
  function explainDefId() {
    return definitionId();
  }
  // ══ Column headers: sort, and the definition behind each number ════════════
  // Clicking an indicator column's NAME opens its definition: the plain-English
  // meaning and the SQL that recomputes it, read from the same reader as the
  // MCP tool semantic_registry_explain (api/<id>/indicator-definitions/), one
  // indicator at a time and cached. Nothing is derived here. The arrow beside
  // the name sorts; a column that is not an indicator sorts from its name too.
  //
  // All of it is WorkflowUI state, never a table's own: every table here is a
  // function defined inside WorkflowUI, so each state change remounts it and
  // anything it held itself would be thrown away.
  var sColSort = React.useState({});
  var colSort = sColSort[0],
    setColSort = sColSort[1];
  var sColDef = React.useState(null);
  var colDef = sColDef[0],
    setColDef = sColDef[1];
  var sDefCache = React.useState({});
  var defCache = sDefCache[0],
    setDefCache = sDefCache[1];
  var sDefFull = React.useState(false);
  var defFull = sDefFull[0],
    setDefFull = sDefFull[1];
  var sDefConsts = React.useState(false);
  var defConsts = sDefConsts[0],
    setDefConsts = sDefConsts[1];
  React.useEffect(
    function () {
      if (!colDef) return;
      function onKey(ev) {
        if (ev.key === 'Escape') setColDef(null);
      }
      window.addEventListener('keydown', onKey);
      return function () {
        window.removeEventListener('keydown', onKey);
      };
    },
    [colDef],
  );
  function sortOf(table) {
    if (colSort[table]) return colSort[table];
    // The worker table opens busiest first.
    if (table === 'workers') return { key: 'cases', dir: 'desc' };
    return null;
  }
  function setSortFor(table, key, dir) {
    setColSort(function (prev) {
      var next = Object.assign({}, prev);
      next[table] = { key: key, dir: dir };
      return next;
    });
  }
  function toggleSort(table, key) {
    var cur = sortOf(table);
    setSortFor(
      table,
      key,
      cur && cur.key === key && cur.dir === 'desc' ? 'asc' : 'desc',
    );
  }
  function sortRows(table, rows, valueOf) {
    return R.sortRows(rows, sortOf(table), valueOf);
  }
  var DEF_SCOPE_LABEL = {
    programme: 'the programme',
    llo: 'organisation',
    opportunity: 'opportunity',
    flw: 'worker',
  };
  function explainColUrl(id, scope, fmt, download) {
    var sp = scopeParams();
    return (
      '/labs/workflow/api/' +
      explainDefId() +
      '/indicator-definitions/' +
      sp +
      (sp ? '&' : '?') +
      'indicators=' +
      encodeURIComponent(id) +
      '&scope=' +
      scope +
      '&format=' +
      fmt +
      (download ? '&download=1' : '')
    );
  }
  function openColDef(d) {
    setColDef(d);
    setDefFull(false);
    var k = d.scope + '|' + d.id;
    var have = defCache[k];
    if (have && have.status !== 'error') return;
    if (!explainDefId()) {
      setDefCache(function (prev) {
        var next = Object.assign({}, prev);
        next[k] = {
          status: 'error',
          error:
            'this page does not know which workflow computes its figures, so it cannot read their definitions',
        };
        return next;
      });
      return;
    }
    function put(v) {
      setDefCache(function (prev) {
        var next = Object.assign({}, prev);
        next[k] = v;
        return next;
      });
    }
    put({ status: 'loading' });
    fetch(explainColUrl(d.id, d.scope, 'json', false), {
      credentials: 'same-origin',
    })
      .then(function (r) {
        return r.json().catch(function () {
          return { error: 'the server answered HTTP ' + r.status };
        });
      })
      .then(function (j) {
        if (j.error) throw new Error(j.error);
        var e = (j.indicators || [])[0];
        if (!e) throw new Error('no definition came back for ' + d.id);
        put({ status: 'ready', e: e });
      })
      .catch(function (err) {
        put({ status: 'error', error: String((err && err.message) || err) });
      });
  }
  function copyDefText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text);
    }
  }
  function defBlock(title, text) {
    return (
      <div className="mt-3">
        <div className="flex items-center justify-between">
          <span className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">
            {title}
          </span>
          <button
            type="button"
            className="text-xs text-indigo-600 hover:underline"
            onClick={function () {
              copyDefText(text);
            }}
          >
            Copy
          </button>
        </div>
        <pre className="mt-1 text-xs bg-gray-50 border border-gray-200 rounded-md p-2 overflow-x-auto whitespace-pre max-h-72">
          {text}
        </pre>
      </div>
    );
  }
  // One indicator's definition, top to bottom in the order a reader needs it:
  // the authored sentence; how it is counted (which babies it is out of, what
  // it counts among them, when it is shown); what each term means; the
  // thresholds, folded away; then the SQL. All of it from the explain reader
  // (semantic/explain.py), nothing derived here.
  function conditionList(where) {
    if (!where || !where.length)
      return <span className="text-gray-500">all babies</span>;
    return (
      <ul className="space-y-0.5">
        {where.map(function (c) {
          return (
            <li key={c} className="flex gap-1.5">
              <span className="text-gray-400">•</span>
              <span>{c}</span>
            </li>
          );
        })}
      </ul>
    );
  }
  function howRow(label, body) {
    return (
      <div className="grid grid-cols-[6rem_1fr] gap-2 py-1.5 border-t border-gray-100 first:border-t-0">
        <div className="text-[11px] uppercase tracking-wide text-gray-500 font-semibold pt-0.5">
          {label}
        </div>
        <div className="text-sm text-gray-800">{body}</div>
      </div>
    );
  }
  function howBlock(h) {
    var cap = function (t) {
      return t ? t.charAt(0).toUpperCase() + t.slice(1) : t;
    };
    var rows = [];
    if (h.kind === 'value') {
      rows.push(
        howRow(
          'Value',
          h.value === 'babies' ? 'Number of babies' : cap(h.value),
        ),
      );
      rows.push(howRow('Over', conditionList(h.base.where)));
    } else {
      rows.push(
        howRow(
          'Out of',
          <div>
            <div className="text-gray-600 mb-0.5">{cap(h.base.what)} where</div>
            {conditionList(h.base.where)}
          </div>,
        ),
      );
      rows.push(
        howRow(
          h.kind === 'percent' ? 'Counts' : 'Divides',
          <div>
            <div className="text-gray-600 mb-0.5">
              {h.counts.where && h.counts.where.length
                ? 'Those ' +
                  (h.counts.what === 'babies' ? 'babies' : h.counts.what) +
                  ' where'
                : cap(h.counts.what)}
            </div>
            {h.counts.where && h.counts.where.length
              ? conditionList(h.counts.where)
              : null}
          </div>,
        ),
      );
    }
    if (h.shown_when)
      rows.push(
        howRow(
          'Shown',
          h.kind === 'value'
            ? 'Only when there are ' + h.shown_when + '.'
            : 'Only when ' +
                h.shown_when.replace(
                  'in the base',
                  'are in the “out of” group',
                ) +
                '.',
        ),
      );
    return (
      <div className="mt-3 rounded-lg border border-gray-200 bg-gray-50 px-3 py-1">
        {rows}
      </div>
    );
  }
  function definitionBody(e) {
    var en = e.english || {};
    var measureSql = [
      '-- ' + e.measure,
      (e.expression && e.expression.compiled) || '',
    ]
      .concat(
        (e.components || []).map(function (c) {
          return '-- ' + c.name + '\n' + (c.compiled || c.sql || '');
        }),
      )
      .join('\n');
    var propSql = (e.properties || [])
      .map(function (p) {
        return (
          (p.means ? '-- ' + p.name + ': ' + p.means + '\n' : '') +
          p.name +
          ' = ' +
          p.sql
        );
      })
      .concat(
        ((e.weight_series && e.weight_series.derived) || []).map(function (d) {
          return d.name + ' = ' + d.sql;
        }),
      )
      .join('\n');
    var consts = Object.keys(e.constants || {})
      .map(function (k) {
        return k + ' = ' + e.constants[k];
      })
      .join(', ');
    var reads = (en.reads || []).filter(function (r) {
      return r.means;
    });
    return (
      <div>
        {en.plain ? (
          <p className="text-base text-gray-900 leading-snug">{en.plain}</p>
        ) : null}
        {en.how ? (
          howBlock(en.how)
        ) : en.definition ? (
          <p className="text-gray-600 text-sm mt-2">{en.definition}</p>
        ) : null}
        {reads.length ? (
          <div className="mt-3">
            <div className="text-[11px] uppercase tracking-wide text-gray-500 font-semibold mb-1">
              Terms
            </div>
            <dl className="text-sm space-y-1.5">
              {reads.map(function (r) {
                return (
                  <div key={r.name}>
                    <dt className="font-medium text-gray-900 inline">
                      {r.label || r.name}
                    </dt>
                    <dd className="text-gray-600 inline">
                      {' \u2014 ' + r.means}
                    </dd>
                  </div>
                );
              })}
            </dl>
          </div>
        ) : null}
        {consts ? (
          <div className="mt-3 text-xs">
            <button
              type="button"
              className="text-indigo-600 hover:underline"
              onClick={function () {
                setDefConsts(!defConsts);
              }}
            >
              {(defConsts ? 'Hide' : 'Show') +
                ' the thresholds it uses (' +
                Object.keys(e.constants || {}).length +
                ')'}
            </button>
            {defConsts ? (
              <div className="mt-1 font-mono text-gray-600 break-words">
                {consts}
              </div>
            ) : null}
          </div>
        ) : null}
        {defBlock('Measure', measureSql)}
        {propSql
          ? defBlock('Properties and window derivations it reads', propSql)
          : null}
      </div>
    );
  }
  // One header cell. `def` is the indicator whose definition the name opens;
  // `table` is the sort space (omit it on a table whose rows have a fixed
  // order, like a scorecard of scopes).
  function headCell(o) {
    var s = o.table ? sortOf(o.table) : null;
    var on = !!(s && s.key === o.sortKey);
    function sort() {
      toggleSort(o.table, o.sortKey);
    }
    var onName = o.def
      ? function () {
          openColDef({
            id: o.def,
            title: o.title,
            label: o.label,
            scope: o.scope,
            table: o.table || null,
            sortKey: o.sortKey,
          });
        }
      : o.table
        ? sort
        : null;
    return (
      <th
        key={o.key}
        title={
          o.def ? (o.title || o.def) + ' — click for the definition' : o.title
        }
        className={
          (o.className || '') +
          (on
            ? ' bg-indigo-100 text-indigo-900 border-b-2 border-indigo-600'
            : '')
        }
      >
        <span
          className={
            'inline-flex items-center gap-1' +
            (o.align === 'left' ? '' : ' justify-end')
          }
        >
          {onName ? (
            <button
              type="button"
              onClick={onName}
              className={
                'font-semibold hover:text-indigo-700 ' +
                (o.def
                  ? 'underline decoration-dotted decoration-gray-300 underline-offset-2'
                  : '')
              }
            >
              {o.label}
            </button>
          ) : (
            o.label
          )}
          {o.table ? (
            <button
              type="button"
              onClick={sort}
              aria-label={'Sort by ' + (o.title || o.label)}
              title="Sort"
              className={
                'text-[10px] ' +
                (on ? 'text-indigo-600' : 'text-gray-300 hover:text-indigo-600')
              }
            >
              {on ? (s.dir === 'asc' ? '▲' : '▼') : '↕'}
            </button>
          ) : null}
        </span>
        {o.sub ? (
          <div className="font-mono text-[10px] font-normal text-gray-300">
            {o.sub}
          </div>
        ) : null}
      </th>
    );
  }
  function colDefModal() {
    if (!colDef) return null;
    var st = defCache[colDef.scope + '|' + colDef.id] || { status: 'loading' };
    var e = st.e;
    var s = colDef.table ? sortOf(colDef.table) : null;
    function close() {
      setColDef(null);
    }
    function sortBtn(dir, label) {
      var on = !!(s && s.key === colDef.sortKey && s.dir === dir);
      return (
        <button
          type="button"
          className={
            'px-2.5 py-1 rounded-md text-xs font-medium border ' +
            (on
              ? 'bg-indigo-600 border-indigo-600 text-white'
              : 'bg-white border-gray-200 text-gray-700 hover:bg-indigo-50')
          }
          onClick={function () {
            setSortFor(colDef.table, colDef.sortKey, dir);
            close();
          }}
        >
          {label}
        </button>
      );
    }
    return (
      <div
        className="fixed inset-0 z-50 flex items-start justify-center bg-gray-900/40 p-4 overflow-y-auto"
        onClick={close}
      >
        <div
          role="dialog"
          aria-modal="true"
          className="bg-white rounded-xl shadow-xl w-full max-w-3xl mt-12 text-sm text-left"
          onClick={function (ev) {
            ev.stopPropagation();
          }}
        >
          <div className="px-5 py-3 border-b border-gray-100 flex items-start justify-between gap-3">
            <div>
              <div className="font-mono text-xs text-gray-400">
                {colDef.id}
                {e && e.measure ? ' · ' + e.measure : ''}
              </div>
              <div className="text-base font-semibold text-gray-900">
                {colDef.title || (e && e.title) || colDef.label}
              </div>
            </div>
            <button
              type="button"
              aria-label="Close"
              className="text-gray-400 hover:text-gray-700 text-xl leading-none"
              onClick={close}
            >
              {'×'}
            </button>
          </div>
          <div className="px-5 py-4">
            {st.status === 'loading' ? (
              <div className="text-xs text-gray-400">
                Reading the definition…
              </div>
            ) : st.status === 'error' ? (
              <div className="text-xs text-red-700">
                Could not read the definition: {st.error}
              </div>
            ) : (
              <div>
                {definitionBody(e)}
                <div className="mt-3">
                  <button
                    type="button"
                    className="text-xs text-indigo-600 hover:underline"
                    onClick={function () {
                      setDefFull(!defFull);
                    }}
                  >
                    {defFull
                      ? 'Hide the full statement'
                      : 'Show the full statement, grouped by ' +
                        (DEF_SCOPE_LABEL[colDef.scope] || colDef.scope)}
                  </button>
                  {defFull
                    ? defBlock(
                        'Full statement · replace pipeline_visit_rows with the pipeline query',
                        e.compiled_sql || '',
                      )
                    : null}
                </div>
              </div>
            )}
          </div>
          <div className="px-5 py-3 border-t border-gray-100 flex items-center gap-2 flex-wrap text-xs">
            {colDef.table ? (
              <span className="flex items-center gap-2">
                <span className="text-gray-500">Sort the table</span>
                {sortBtn('desc', 'High → low')}
                {sortBtn('asc', 'Low → high')}
              </span>
            ) : null}
            <a
              className="ml-auto text-indigo-600 hover:underline"
              href={explainColUrl(colDef.id, colDef.scope, 'sql', true)}
            >
              Download SQL
            </a>
            <a
              className="text-indigo-600 hover:underline"
              href={explainColUrl(colDef.id, colDef.scope, 'md', false)}
              target="_blank"
              rel="noopener"
            >
              Open as text
            </a>
          </div>
        </div>
      </div>
    );
  }

  // ══ Workers ════════════════════════════════════════════════════════════════
  // byFLW carries each worker's cases as POSITIONS into the case index; resolve
  // them once so the table, the last-visit column and the case list see cases.
  var workerRows = React.useMemo(
    function () {
      var allCases = P.cases || [];
      return (P.byFLW || []).map(function (f) {
        var rows = (f.rows || [])
          .map(function (i) {
            return typeof i === 'number' ? allCases[i] : i;
          })
          .filter(Boolean);
        var last = '';
        rows.forEach(function (c) {
          var d = String(c.last_visit_date || '').slice(0, 10);
          if (d > last) last = d;
        });
        return {
          key: f.key || f.opp + '::' + (f.username || f.flw),
          name: f.username || f.flw,
          ind: f.ind || {},
          reds: f.reds || 0,
          yellows: f.yellows || 0,
          cases: rows,
          n:
            rows.length ||
            (f.ind && f.ind.total_cases && f.ind.total_cases.n) ||
            0,
          last: last,
        };
      });
    },
    [payload],
  );
  var sortedWorkers = sortRows('workers', workerRows, function (w, key) {
    if (key === 'worker') return String(w.name || '').toLowerCase();
    if (key === 'cases') return w.n;
    if (key === 'last') return w.last || null;
    if (key === 'attn') return w.reds * 1000 + w.yellows;
    var c = COLUMNS[Number(String(key).slice(3))];
    return R.scoreSortValue(c, c && w.ind[c.id]);
  });
  var sOpenWorker = React.useState(null);
  var openWorker = sOpenWorker[0],
    setOpenWorker = sOpenWorker[1];

  // The worker's own review page, where a report is configured with one. A
  // handed-down run reads the same way the programme report's does.
  var FLW_REVIEW = cfg.flw_review || null;
  function flwReviewUrl(w) {
    if (!FLW_REVIEW || !FLW_REVIEW.workflow_id || !FLW_REVIEW.run_id)
      return null;
    var sp = scopeParams();
    return (
      '/labs/workflow/' +
      FLW_REVIEW.workflow_id +
      '/run/?run_id=' +
      FLW_REVIEW.run_id +
      (sp ? '&' + sp.slice(1) : '') +
      '&flw=' +
      encodeURIComponent(w.key) +
      '&source_run=' +
      (instance && instance.id)
    );
  }

  // ══ Headline tiles ═════════════════════════════════════════════════════════
  var TILES = [
    {
      id: 'started_cases',
      label: 'Started cases',
      count: true,
      sub: 'two or more visits',
    },
    {
      id: 'pct_healthy_growth',
      label: 'Healthy growth',
      pct: true,
      target: 0.7,
      sub: 'target 70% · of qualifying babies',
    },
    {
      id: 'mean_early_growth_rate',
      label: 'Early growth rate',
      unit: 'g/kg/day',
      target: 15,
      sub: 'target 15',
    },
    {
      id: 'mortality',
      label: 'Mortality',
      pct: true,
      target: 0.04,
      sub: 'two-sided',
    },
    {
      id: 'lost_by_day_28',
      label: 'Lost by day 28',
      pct: true,
      target: 0.1,
      sub: 'target 10%',
    },
  ];
  function Tiles() {
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
            entry: entryOf(ind, t.id),
            previous: prev ? prev.ind && prev.ind[t.id] : null,
            current: cur ? (cur.ind && cur.ind[t.id]) || null : null,
            previousDate: prev ? prev.date : null,
            minDenominator: MIN_DEN,
          };
        })}
      />
    );
  }

  // ══ Activity and trends ════════════════════════════════════════════════════
  function Trend(props) {
    var m = measureOf(props.id);
    return (
      <R.TrendCard
        label={props.label}
        title={m.title}
        pct={props.pct}
        target={props.target}
        loading={history === null}
        format={function (e) {
          return R.fmtValue(m, e.value);
        }}
        points={historyPoints.map(function (p) {
          return { date: p.date, entry: (p.ind && p.ind[props.id]) || null };
        })}
      />
    );
  }
  function ChartsRow() {
    return (
      <div>
        <div className="grid grid-cols-1 lg:grid-cols-6 gap-3">
          <R.WeeklyActivityCard
            weeks={(P.weekly || {}).all || []}
            className="lg:col-span-2"
          />
          <Trend
            id="pct_healthy_growth"
            label="Healthy growth"
            pct={true}
            target={0.7}
          />
          <Trend
            id="mean_early_growth_rate"
            label="Growth rate"
            pct={false}
            target={15}
          />
          <Trend id="mortality" label="Mortality" pct={true} target={0.04} />
          <Trend
            id="lost_by_day_28"
            label="Lost by d28"
            pct={true}
            target={0.1}
          />
        </div>
        <p className="mt-2 text-xs text-gray-400">
          Activity is counted in the week it happened, to {R.dateLbl(asOf)}.
          Each indicator point is this opportunity's figure as of a saved report
          {history === null ? ' (loading the saved reports…)' : ''}; a gap is a
          report with too few cases to score, not a zero. Dashed line = target.
        </p>
      </div>
    );
  }

  // ══ The benchmark ══════════════════════════════════════════════════════════
  // Anonymous peer values for the cohorts this opportunity belongs to. An EMPTY
  // payload is a normal state -- no cohort published, or the disclosure rules
  // withheld everything -- and is explained in words. Only a failed REQUEST is
  // an error.
  var benchmarkEmptyMessage =
    'No benchmark is published for this opportunity yet. Either it is not in a ' +
    'benchmark cohort, or the disclosure rules withheld every indicator.';
  var sBench = React.useState({ status: 'loading' });
  var bench = sBench[0],
    setBench = sBench[1];
  React.useEffect(
    function () {
      var cancelled = false;
      if (oppId === null) {
        setBench({
          status: 'error',
          error: 'this page could not resolve which opportunity it is about',
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
              return { error: 'the server answered HTTP ' + r.status };
            })
            .then(function (j) {
              return { ok: r.ok, j: j };
            });
        })
        .then(function (res) {
          if (cancelled) return;
          if (!res.ok)
            throw new Error(res.j.error || 'could not read the benchmark');
          setBench({ status: 'ready', payload: res.j });
        })
        .catch(function (e) {
          if (!cancelled)
            setBench({ status: 'error', error: String((e && e.message) || e) });
        });
      return function () {
        cancelled = true;
      };
    },
    [oppId],
  );
  // What may stand on a peer chart. Mirrors the publisher's PUBLISHABLE_BANDS
  // (benchmarks/publish.py): anything else is the registry or its gates saying
  // the figure must not stand on its own.
  function publishableValue(cell) {
    if (!cell) return null;
    if (
      cell.band !== 'green' &&
      cell.band !== 'yellow' &&
      cell.band !== 'red' &&
      cell.band !== 'unbanded'
    )
      return null;
    return cell.value;
  }
  function Benchmark() {
    if (bench.status === 'loading')
      return <R.Loading height={120}>Loading the benchmark…</R.Loading>;
    if (bench.status === 'error')
      return (
        <R.Notice tone="error">
          The benchmark could not be read: {bench.error}. This is a failed
          request, not an empty benchmark.
        </R.Notice>
      );
    var bp = bench.payload || {};
    var cohorts = bp.cohorts || {};
    var ids = Object.keys(cohorts);
    if (!ids.length)
      return <R.Notice tone="muted">{benchmarkEmptyMessage}</R.Notice>;
    return (
      <div className="space-y-4">
        {ids.map(function (cid) {
          var cmeta = cohorts[cid] || {};
          var byFamily = (bp.indicators || {})[cid] || {};
          var shown = MEASURES.filter(function (m) {
            var e = (byFamily[m.series] || {})[m.indicator];
            return (
              e &&
              ((e.peers || []).length || Object.keys(e.series || {}).length > 1)
            );
          });
          return (
            <div key={cid}>
              <div className="flex items-baseline justify-between mb-2">
                <div className="text-sm font-semibold text-gray-800">
                  {cmeta.name || 'Cohort ' + cid}
                </div>
                <div className="text-xs text-gray-400">
                  peers as of {R.dateLbl(cmeta.as_of || bp.as_of) || 'unknown'}
                </div>
              </div>
              {shown.length ? (
                <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
                  {shown.map(function (m) {
                    return (
                      <R.PeerCard
                        key={m.indicator}
                        measure={m}
                        entry={(byFamily[m.series] || {})[m.indicator]}
                        own={publishableValue(ind[m.indicator])}
                      />
                    );
                  })}
                </div>
              ) : (
                <R.Notice tone="muted">
                  Nothing published for these indicators in this cohort.
                </R.Notice>
              )}
            </div>
          );
        })}
      </div>
    );
  }

  // ══ The worker table ═══════════════════════════════════════════════════════
  // The lead column stays put while the scorecard columns scroll under it.
  var STICK = 'sticky left-0 z-10 bg-white';
  function GroupHead(props) {
    return (
      <tr className="text-[10px] uppercase tracking-wide text-gray-400 border-b border-gray-200">
        {props.lead.map(function (l, i) {
          return <th key={'g' + i} className="px-3 py-1"></th>;
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
          return <th key={'t' + i} className="px-1.5 py-1"></th>;
        })}
      </tr>
    );
  }
  function WorkerTable() {
    var lead = [
      { label: 'Worker', sortKey: 'worker' },
      { label: 'Cases', sortKey: 'cases' },
    ];
    var trail = [
      { label: 'Last visit', sortKey: 'last' },
      { label: 'Attention', sortKey: 'attn' },
    ];
    var width = lead.length + COLUMNS.length + trail.length;
    return (
      <R.Card padded={false}>
        <div className="px-4 pt-3 pb-2">
          <R.SectionTitle
            right={
              workerRows.length +
              ' workers · as of ' +
              R.dateLbl(asOf) +
              ' · click a worker for their cases'
            }
          >
            Field workers
          </R.SectionTitle>
        </div>
        {!workerRows.length ? (
          <div className="px-4 pb-4 text-sm text-gray-500">
            No worker has a case in this report.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <GroupHead lead={lead} trail={trail} />
                <tr className="text-xs text-gray-500 border-b border-gray-100">
                  {lead.map(function (l, i) {
                    return headCell({
                      key: 'l' + i,
                      label: l.label,
                      align: 'left',
                      table: 'workers',
                      sortKey: l.sortKey,
                      className:
                        (i === 0 ? 'px-3 ' + STICK : 'px-1.5') +
                        ' py-2 text-left font-semibold text-gray-600 whitespace-nowrap',
                    });
                  })}
                  {COLUMNS.map(function (c, i) {
                    return headCell({
                      key: 'c' + i,
                      label: c.label,
                      title: c.title,
                      def: c.id,
                      scope: 'flw',
                      table: 'workers',
                      sortKey: 'col' + i,
                      className:
                        'px-1.5 py-2 text-right whitespace-nowrap font-semibold text-gray-600',
                    });
                  })}
                  {trail.map(function (l, i) {
                    return headCell({
                      key: 't' + i,
                      label: l.label,
                      table: 'workers',
                      sortKey: l.sortKey,
                      className:
                        'px-1.5 py-2 text-right whitespace-nowrap font-semibold text-gray-600',
                    });
                  })}
                </tr>
              </thead>
              <tbody>
                {sortedWorkers.map(function (w) {
                  var open = openWorker === w.key;
                  var review = flwReviewUrl(w);
                  var stale = w.last ? R.daysBetween(w.last, asOf) : null;
                  var out = [
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
                      <td
                        className={
                          'px-3 py-2 text-left whitespace-nowrap ' +
                          STICK +
                          (open ? ' bg-indigo-50' : '')
                        }
                      >
                        <span className="font-semibold text-indigo-700">
                          {w.name}
                        </span>
                        {review ? (
                          <a
                            href={review}
                            className="ml-2 text-xs text-indigo-600 hover:underline"
                            onClick={function (ev) {
                              ev.stopPropagation();
                            }}
                          >
                            Review →
                          </a>
                        ) : null}
                      </td>
                      <td className="px-1.5 py-2 text-right tabular-nums text-gray-600">
                        {R.nCount(w.n)}
                      </td>
                      {COLUMNS.map(function (c, i) {
                        return (
                          <R.ScoreCell
                            key={i}
                            column={c}
                            entry={w.ind[c.id]}
                            measure={M_BY_ID[c.id]}
                            minDenominator={MIN_DEN}
                          />
                        );
                      })}
                      <td
                        className={
                          'px-1.5 py-2 text-right whitespace-nowrap tabular-nums ' +
                          (stale !== null && stale > 14
                            ? 'text-red-700 font-semibold'
                            : 'text-gray-600')
                        }
                        title={
                          stale !== null
                            ? stale + ' days before the report date'
                            : ''
                        }
                      >
                        {w.last ? R.dateLbl(w.last) : '—'}
                      </td>
                      <R.AttentionCell reds={w.reds} yellows={w.yellows} />
                    </tr>,
                  ];
                  if (open)
                    out.push(
                      <tr key={w.key + ':cases'} className="bg-indigo-50/40">
                        <td colSpan={width} className="px-3 py-3">
                          <CaseList worker={w} />
                        </td>
                      </tr>,
                    );
                  return out;
                })}
              </tbody>
            </table>
          </div>
        )}
        <R.ScorecardLegend
          minDenominator={MIN_DEN}
          right="Click a column name for its definition · the arrow sorts"
        />
      </R.Card>
    );
  }

  // One worker's cases, from this report's own case index.
  function CaseList(props) {
    var cases = (props.worker.cases || []).slice().sort(function (a, b) {
      return String(b.last_visit_date || '') < String(a.last_visit_date || '')
        ? -1
        : 1;
    });
    if (!cases.length)
      return (
        <div className="text-xs text-gray-500">No cases in this report.</div>
      );
    return (
      <div className="overflow-x-auto">
        <table className="min-w-full text-xs bg-white rounded-lg border border-gray-200">
          <thead>
            <tr className="text-gray-500 border-b border-gray-100">
              {[
                'Case',
                'Registered',
                'First visit',
                'Last visit',
                'Visits',
                'Birth weight',
                'Latest weight',
                'Status',
              ].map(function (h, i) {
                return (
                  <th
                    key={h}
                    className={
                      'px-2 py-1.5 font-semibold ' +
                      (i ? 'text-right' : 'text-left')
                    }
                  >
                    {h}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {cases.map(function (c, i) {
              return (
                <tr key={c.entity_id || i} className="border-t border-gray-100">
                  <td className="px-2 py-1.5 font-mono text-gray-600">
                    {String(c.entity_id || '').slice(0, 8)}
                  </td>
                  <td className="px-2 py-1.5 text-right">
                    {R.dateLbl(c.reg_date) || '—'}
                  </td>
                  <td className="px-2 py-1.5 text-right">
                    {R.dateLbl(c.first_visit_date) || '—'}
                  </td>
                  <td className="px-2 py-1.5 text-right">
                    {R.dateLbl(c.last_visit_date) || '—'}
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums">
                    {c.total_visits === undefined
                      ? '—'
                      : R.nCount(c.total_visits)}
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums">
                    {c.birth_weight_g ? R.nCount(c.birth_weight_g) + ' g' : '—'}
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums">
                    {c.last_weight_g ? R.nCount(c.last_weight_g) + ' g' : '—'}
                  </td>
                  <td className="px-2 py-1.5 text-right text-gray-600">
                    {c.last_kmc_status || '—'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  }

  // ══ Every indicator, collapsed ═════════════════════════════════════════════
  var sAllOpen = React.useState(false);
  var allOpen = sAllOpen[0],
    setAllOpen = sAllOpen[1];
  function AllIndicators() {
    return (
      <R.Card padded={false}>
        <details
          open={allOpen}
          onToggle={function (ev) {
            setAllOpen(ev.currentTarget.open);
          }}
        >
          <summary className="px-4 py-3 cursor-pointer flex items-baseline justify-between gap-2">
            <span className="text-sm font-semibold text-gray-900">
              All indicators · this opportunity
            </span>
            <span className="text-xs text-gray-500">
              value, n and band for every indicator · click a name for its
              definition
            </span>
          </summary>
          <table className="min-w-full text-sm border-t border-gray-100">
            <tbody>
              {MEASURES.map(function (m) {
                var e = entryOf(ind, m.indicator);
                return (
                  <tr key={m.indicator} className="border-t border-gray-100">
                    <td className="px-4 py-2">
                      <button
                        type="button"
                        className="text-left font-medium text-gray-900 hover:text-indigo-700 underline decoration-dotted decoration-gray-300 underline-offset-2"
                        onClick={function () {
                          openColDef({
                            id: m.indicator,
                            title: m.title,
                            label: m.title,
                            scope: 'opportunity',
                          });
                        }}
                      >
                        {m.title || m.indicator}
                      </button>
                      <div className="text-xs text-gray-400">{m.category}</div>
                    </td>
                    <td className="px-2 py-2 text-right tabular-nums">
                      <R.ScoreCellText
                        column={{ id: m.indicator, label: m.title }}
                        entry={e}
                        measure={m}
                        minDenominator={MIN_DEN}
                      />
                    </td>
                    <td className="px-2 py-2 text-right text-xs text-gray-500 tabular-nums">
                      n = {R.nCount(e.n)}
                    </td>
                    <td className="px-4 py-2 text-right">
                      {R.BAND_WORD[e.band] ? (
                        <span
                          className={
                            'px-2 py-0.5 rounded text-xs ' + R.BAND_CLS[e.band]
                          }
                        >
                          {R.BAND_WORD[e.band]}
                        </span>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </details>
      </R.Card>
    );
  }

  // ══ Render ═════════════════════════════════════════════════════════════════
  function saveRun() {
    if (!view || !view.complete) return;
    view.complete({
      confirm:
        'Save this week? Its figures become final and load from the saved ' +
        'report. Re-running later creates a new run; this one stays in the history.',
    });
  }
  var title =
    (llo ? llo + ' · ' : '') +
    (oppId !== null ? 'opportunity ' + oppId : 'this opportunity');
  var source = handedDown ? (
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
  ) : isCompleted ? (
    <R.Pill tone="source">Saved here</R.Pill>
  ) : (
    <R.Pill tone="current">Live · not saved</R.Pill>
  );
  var cache = live.cache || {};

  if (!R)
    return (
      <div className="p-6 text-sm text-red-800">
        The report library did not load. Reload the page; if this persists, the
        runner bundle is older than this report.
      </div>
    );

  return (
    <div className="p-4 space-y-4 bg-gray-50">
      {colDefModal()}
      <R.ReportHeader
        crumbs="KMC opportunity report"
        title={title}
        subtitle={
          ready ? (
            <span>
              <b>{R.nCount(meta.cases)}</b> babies ·{' '}
              <b>{R.nCount(meta.visits)}</b> visits ·{' '}
              <b>{R.nCount(workerRows.length)}</b> workers · figures as of{' '}
              {R.dateLbl(asOf)}
            </span>
          ) : null
        }
        badges={
          <span className="flex flex-wrap items-center gap-2">
            <R.Pill tone="muted">Report of {R.dateLbl(asOf)}</R.Pill>
            {isCompleted ? <R.Pill tone="final">Final report</R.Pill> : null}
            {source}
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
          <R.Loading height={220}>
            Computing this opportunity's figures — a cold start downloads its
            visits first…
          </R.Loading>
        )
      ) : (
        <div className="space-y-4">
          {/* The programme report's order -- tiles, the table, the charts --
              with the peers after them: seventeen peer cards above the worker
              table pushed it three screens down. */}
          <Tiles />
          <WorkerTable />
          <ChartsRow />
          <div>
            <R.SectionTitle sub="Anonymous peers, re-sorted per indicator: a bar cannot be followed from one chart to the next. This opportunity is the blue bar and the blue line; a trend runs on each opportunity's own weeks of delivering, so week 1 is week 1 for everybody, and ends where that opportunity's figures stopped changing.">
              Against its peers
            </R.SectionTitle>
            <Benchmark />
          </div>
          <AllIndicators />
        </div>
      )}
    </div>
  );
}
