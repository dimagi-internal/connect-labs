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
  // ══ One worker, read from the programme report ═══════════════════════════════
  // This workflow computes no indicator. Every graded figure on this page is the
  // SAME payload the KMC Programme Metrics page shows: the run this page was
  // opened from (`?source_run=`), read through the same preview endpoint that
  // page reads, or -- opened on its own -- the newest saved programme report.
  // What this page adds is what a per-worker review needs and the programme page
  // must not carry: the worker's cases with their full weight series (from the
  // live pipelines both workflows share), a growth chart per case, and an audit
  // of the worker's recent images.
  var cfg = (definition && definition.config) || {};
  var FLW_SEP = '::';
  var MIN_DEN = 20; // the registry's defaults.min_denominator

  var search = String(window.location.search || '');
  function qp(name) {
    var m = search.match(new RegExp('[?&]' + name + '=([^&]*)'));
    return m ? decodeURIComponent(m[1].replace(/\+/g, ' ')) : '';
  }
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
  // This workflow's own id, for the endpoints keyed by definition. The
  // `definition` prop is the record's `data` blob and the pk travels beside it
  // as `definition_id` -- it is never written into `data`, so `definition.id`
  // is undefined and both row fetches went to `/api/undefined/pipeline-rows/`.
  // The run knows its definition, and so does the URL (/labs/workflow/<id>/run/).
  // Same chain as the programme page's `definitionId()`.
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

  // ══ Rows, with the fetch's own progress ══════════════════════════════════════
  // The server answers the same query two ways: `pipeline-rows` in one shot, or
  // `pipeline-rows/stream/` as SSE with the paginated Connect read's progress
  // ahead of the identical payload. A cold read is 40-60s -- measured live on
  // 2026-09-16, three back to back for one worker -- and in one shot that is a
  // minute of a page that looks broken rather than busy. So: prefer the stream.
  //
  // It falls back to the one-shot endpoint whenever the stream cannot deliver
  // (no EventSource, a buffering proxy, a dropped connection). Progress is a
  // courtesy; the rows are not, and must never depend on the nicer transport.
  //
  // Returns {promise, cancel}. `onProgress(message)` fires per progress event;
  // the promise resolves with the final {rows, metadata}.
  function fetchRowsWithProgress(url, onProgress) {
    var cancelled = false;
    var es = null;
    // A 502/503/504 is the gateway, not the query: a rolling deploy answers
    // them for the seconds a task is draining, and a reviewer who opened a case
    // then was told "failed -- reload the page" for data that was fine (seen
    // 2026-09-24, mid-deploy). Retry those twice before calling it a failure;
    // any other status is a real answer and fails at once.
    function plain(attempt) {
      attempt = attempt || 0;
      return fetch(url, { credentials: 'same-origin' }).then(function (r) {
        if (!r.ok) {
          if (
            attempt < 2 &&
            !cancelled &&
            [502, 503, 504].indexOf(r.status) >= 0
          ) {
            return new Promise(function (res) {
              window.setTimeout(res, attempt ? 5000 : 2000);
            }).then(function () {
              return plain(attempt + 1);
            });
          }
          throw new Error('HTTP ' + r.status);
        }
        return r.json();
      });
    }
    var promise = new Promise(function (resolve, reject) {
      if (typeof window.EventSource !== 'function') {
        plain().then(resolve, reject);
        return;
      }
      var settled = false;
      var streamUrl = url.replace('/pipeline-rows/', '/pipeline-rows/stream/');
      try {
        es = new window.EventSource(streamUrl, { withCredentials: true });
      } catch (e) {
        plain().then(resolve, reject);
        return;
      }
      function finish(fn, arg) {
        if (settled || cancelled) return;
        settled = true;
        try {
          es.close();
        } catch (e) {}
        fn(arg);
      }
      es.onmessage = function (ev) {
        if (cancelled) return;
        var d;
        try {
          d = JSON.parse(ev.data);
        } catch (e) {
          return;
        }
        if (d.error) {
          // A server-side fault in the STREAM must not cost the page its rows.
          // The one-shot endpoint answers the same query by a different code
          // path, so retry there: on the first warm read in production the
          // stream raised a TypeError and this rejected, turning a working
          // case table into "Could not load this worker's cases" -- strictly
          // worse than the static label it replaced. Progress is the courtesy;
          // the rows are not. The server logs the real fault either way.
          settled = true;
          try {
            es.close();
          } catch (e) {}
          console.warn('[pipeline-rows] stream failed, falling back:', d.error);
          plain().then(resolve, reject);
          return;
        }
        if (d.data && d.data.rows) {
          finish(resolve, d.data);
          return;
        }
        if (d.message && onProgress) onProgress(d.message);
      };
      es.onerror = function () {
        if (settled || cancelled) return;
        // The stream died before it delivered anything usable. Retry once on the
        // plain endpoint rather than failing the page: a transport that never
        // worked here must not read as "this worker has no cases".
        settled = true;
        try {
          es.close();
        } catch (e) {}
        plain().then(resolve, reject);
      };
    });
    return {
      promise: promise,
      cancel: function () {
        cancelled = true;
        if (es) {
          try {
            es.close();
          } catch (e) {}
        }
      },
    };
  }

  var sourceRun = qp('source_run');
  var sKey = React.useState(qp('flw'));
  var selKey = sKey[0],
    setSelKey = sKey[1];
  var sCase = React.useState(null);
  var selCase = sCase[0],
    setSelCase = sCase[1];
  // A case is addressable too (`?case=<entity_id>`), so a demo or a review note
  // can link straight to one baby. The URL follows the selection either way.
  var caseParam = qp('case');
  function openCase(c) {
    setSelCase(c);
    try {
      var u = new URL(window.location.href);
      if (c) u.searchParams.set('case', c.entity_id);
      else u.searchParams.delete('case');
      window.history.replaceState(null, '', u.toString());
    } catch (e) {
      // an older browser keeps the state without the URL
    }
  }

  // ── The report this page reads ───────────────────────────────────────────────
  var sReport = React.useState({ status: 'loading' });
  var report = sReport[0],
    setReport = sReport[1];
  React.useEffect(
    function () {
      var cancelled = false;
      var sp = scopeParams();
      function readRun(runId) {
        return fetch(
          '/labs/workflow/api/run/' + runId + '/snapshot/preview/' + sp,
          { credentials: 'same-origin' },
        )
          .then(function (r) {
            return r.json().then(function (j) {
              return { ok: r.ok, j: j };
            });
          })
          .then(function (res) {
            if (!res.ok)
              throw new Error(
                res.j.message || res.j.error || 'could not read the report',
              );
            var snap = res.j.snapshot || {};
            var st = (snap.state && snap.state.snapshot) || {};
            if (!cancelled)
              setReport({
                status: 'ready',
                runId: Number(runId),
                source: res.j.source,
                payload: st,
                cache: res.j.cache || null,
              });
          });
      }
      function newestSaved() {
        var src = cfg.source_workflow_id;
        if (!src)
          return Promise.reject(
            new Error(
              'This workflow is not linked to a programme report yet ' +
                '(config.source_workflow_id).',
            ),
          );
        return fetch(
          '/labs/workflow/api/' +
            src +
            '/runs/history/' +
            sp +
            (sp ? '&' : '?') +
            'keys=snapshot.meta.as_of',
          { credentials: 'same-origin' },
        )
          .then(function (r) {
            return r.json();
          })
          .then(function (j) {
            var runs = (j.runs || []).filter(function (r) {
              return r.state && r.state['snapshot.meta.as_of'];
            });
            if (!runs.length)
              throw new Error('No saved programme report to read from yet.');
            return readRun(runs[runs.length - 1].id);
          });
      }
      (sourceRun ? readRun(sourceRun) : newestSaved()).catch(function (e) {
        if (!cancelled)
          setReport({ status: 'error', error: String((e && e.message) || e) });
      });
      return function () {
        cancelled = true;
      };
    },
    [sourceRun, cfg.source_workflow_id],
  );

  // ── Runs saved before the indicator set was unified (#2004) ────────────────
  // Until 2026-09 the registry carried two families with coded ids -- the
  // workbook's C01..C31 and the demo compute spec's N01..N15 -- and a saved run
  // froze both: C at the top level, N under `series.N`. The set is one family of
  // named indicators now, and a completed run is write-protected, so an old run
  // is translated HERE, on read. Only an indicator whose definition did not
  // change carries across: every N id, plus the workbook ids that were the same
  // rule under another name. The rest -- the workbook's growth, loss-to-follow-up
  // and care figures, computed on a one-visit "started" -- are not comparable
  // with today's and are left out rather than shown under a name that now means
  // something else. The page says so (`P.legacyIds`).
  //
  // A COPY of the programme report's translation (kmc_programme_metrics_render.js):
  // this page reads that report's saved runs, and renders cannot import one
  // another. test_kmc_flw_review pins the two copies identical.
  var LEGACY_ID = {
    N01: 'total_cases',
    N02: 'registered_cases',
    N03: 'started_cases',
    N04: 'cumulative_svns_reached',
    N05: 'median_gestational_age',
    N06: 'median_birthweight',
    N07: 'visits_per_case',
    N08: 'pct_enrolled_within_3d',
    N09: 'pct_slow_growth',
    N10: 'pct_healthy_growth',
    N11: 'pct_fast_growth',
    N12: 'pct_incomplete_growth_data',
    N13: 'mortality',
    N14: 'weight_rounding_rate',
    N15: 'pct_impossible_weight_changes',
    C01: 'registered_cases',
    C05: 'total_cases',
    C28: 'birth_copy_rate',
    C31: 'weight_rounding_rate',
  };
  var LEGACY_CODE = /^[CN]\d\d$/;
  function legacyCells(ind) {
    if (!ind) return ind;
    var out = {};
    Object.keys(ind).forEach(function (k) {
      if (!LEGACY_CODE.test(k)) {
        out[k] = ind[k];
        return;
      }
      var to = LEGACY_ID[k];
      if (to && !(to in out)) out[to] = Object.assign({}, ind[k], { id: to });
    });
    return out;
  }
  function fromLegacyIds(p) {
    var N = p.series && p.series.N;
    if (!N) return p;
    // N first: where a workbook id and a scorecard id name the same rule, the
    // scorecard's cell is the one graded with today's thresholds.
    function merge(cInd, nInd) {
      var out = legacyCells(nInd || {});
      var c = legacyCells(cInd || {});
      Object.keys(c).forEach(function (k) {
        if (!(k in out)) out[k] = c[k];
      });
      return out;
    }
    function tally(entry, ind) {
      var reds = 0;
      var yellows = 0;
      Object.keys(ind).forEach(function (k) {
        if (ind[k] && ind[k].band === 'red') reds++;
        if (ind[k] && ind[k].band === 'yellow') yellows++;
      });
      return Object.assign({}, entry, {
        ind: ind,
        reds: reds,
        yellows: yellows,
      });
    }
    function find(list, field, value) {
      return (list || []).filter(function (x) {
        return String(x[field]) === String(value);
      })[0];
    }
    function rollup(cList, nList, field) {
      return (cList || []).map(function (e) {
        var n = find(nList, field, e[field]);
        var out = tally(e, merge(e.ind, n && n.ind));
        if (e.opps)
          out.opps = e.opps.map(function (o) {
            var no = find(N.byOpp, 'opp', o.opp);
            return tally(o, merge(o.ind, no && no.ind));
          });
        return out;
      });
    }
    function months(cList, nList) {
      return (cList || nList || []).map(function (pt) {
        var n = find(nList, 'month', pt.month);
        var c = find(cList, 'month', pt.month);
        return Object.assign({}, pt, {
          ind: merge(c && c.ind, n && n.ind),
          pooled: legacyCells((n && n.pooled) || {}),
        });
      });
    }
    var byScope = {};
    var nByScope = N.monthlyByScope || {};
    Object.keys(p.monthlyByScope || nByScope).forEach(function (k) {
      byScope[k] = months((p.monthlyByScope || {})[k], nByScope[k]);
    });
    var seen = {};
    var measures = []
      .concat(N.measures || [], p.cMeasures || [])
      .map(function (m) {
        var to = LEGACY_ID[m.indicator];
        return to ? Object.assign({}, m, { id: to, indicator: to }) : null;
      })
      .filter(function (m) {
        if (!m || seen[m.indicator]) return false;
        seen[m.indicator] = true;
        return true;
      });
    return Object.assign({}, p, {
      legacyIds: true,
      cMeasures: measures,
      programInd: merge(p.programInd, N.programme),
      byLLO: rollup(p.byLLO, N.byLLO, 'llo'),
      byOpp: rollup(p.byOpp, N.byOpp, 'opp'),
      byFLW: rollup(p.byFLW, N.byFLW, 'key'),
      // The old pooled figure is the workbook's one-visit mortality: not
      // comparable, so the headline falls back to the scope's own cell.
      pooledOverCredible: {},
      credibility: legacyCells(p.credibility || {}),
      monthly: months(p.monthly, N.monthly),
      monthlyByScope: byScope,
      series: {},
    });
  }

  var P = React.useMemo(
    function () {
      return fromLegacyIds(report.payload || {});
    },
    [report.payload],
  );
  var LLO_OF = (P.deployment && P.deployment.llo_map) || {};
  function oppLabel(o) {
    return LLO_OF[o] ? LLO_OF[o] + ' · opp ' + o : 'opp ' + o;
  }

  // The display contract travels with the payload, as on the programme page.
  var N_LIST = React.useMemo(
    function () {
      return (P.cMeasures || [])
        .filter(function (m) {
          return m && m.indicator;
        })
        .map(function (m) {
          return {
            id: m.indicator,
            name: m.title,
            unit: m.unit,
            kind: m.kind,
            minDen: m.min_denominator,
          };
        });
    },
    [P],
  );
  var byFLW = P.byFLW || [];
  var nByKey = React.useMemo(
    function () {
      var m = {};
      (P.byFLW || []).forEach(function (f) {
        m[f.key] = f;
      });
      return m;
    },
    [P],
  );
  var flw =
    byFLW.filter(function (f) {
      return f.key === selKey;
    })[0] || null;
  var nFLW = nByKey[selKey] || null;
  var SC = {
    programme: P.programInd || null,
    byLLO: P.byLLO || [],
    byOpp: P.byOpp || [],
  };
  var lloRow =
    flw && SC
      ? (SC.byLLO || []).filter(function (r) {
          return r.llo === flw.llo;
        })[0]
      : null;
  var oppRow =
    flw && SC
      ? (SC.byOpp || []).filter(function (r) {
          return String(r.opp) === String(flw.opp);
        })[0]
      : null;

  // The figures here are the programme report's own rows, so their definitions
  // are read from the programme workflow's bound registry, not from this one.
  function explainDefId() {
    return cfg.source_workflow_id || null;
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
    return colSort[table] || null;
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
  function blankSortValue(v) {
    return (
      v === null ||
      v === undefined ||
      v === '' ||
      (typeof v === 'number' && isNaN(v))
    );
  }
  // Stable. A row with no value sits at the bottom in BOTH directions: "no
  // data" is not a low score.
  function sortRows(table, rows, valueOf) {
    var s = sortOf(table);
    if (!s) return rows;
    var dir = s.dir === 'asc' ? 1 : -1;
    return rows
      .map(function (r, i) {
        return { r: r, i: i, v: valueOf(r, s.key) };
      })
      .sort(function (a, b) {
        var an = blankSortValue(a.v),
          bn = blankSortValue(b.v);
        if (an && bn) return a.i - b.i;
        if (an) return 1;
        if (bn) return -1;
        if (a.v === b.v) return a.i - b.i;
        return a.v < b.v ? -dir : dir;
      })
      .map(function (x) {
        return x.r;
      });
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

  // Keep the address bar shareable: a picked worker is a link, not a click.
  function pickWorker(key) {
    setSelKey(key);
    setSelCase(null);
    try {
      var u = new URL(window.location.href);
      u.searchParams.set('flw', key);
      u.searchParams.delete('case');
      window.history.replaceState(null, '', u.toString());
    } catch (e) {
      // an older browser keeps the state without the URL
    }
  }

  // ── Formatting, as the programme page formats ────────────────────────────────
  function nCount(value) {
    if (value === null || value === undefined) return '—';
    var num = Number(value);
    if (isNaN(num)) return String(value);
    return Math.round(num).toLocaleString('en-US');
  }
  function entryOf(map, id) {
    return (map && map[id]) || { id: id, n: 0, value: null, band: 'nodata' };
  }
  function fmt(ind, e) {
    if (!e || e.value === null || e.value === undefined) return '—';
    if (ind.unit === '%') return (100 * e.value).toFixed(1) + '%';
    if (ind.unit === 'g' || ind.kind === 'count') return nCount(e.value);
    return Number(e.value).toFixed(1);
  }
  function bandLabel(e, ind) {
    if (!e) return 'no data';
    if (e.band === 'notinapp') return 'not in this app';
    if (e.band === 'unrecorded') return 'no value reaches this row';
    if (e.band === 'notcredible') return 'shown, not credible';
    if (e.band === 'insufficient')
      return 'n<' + ((ind && ind.minDen) || MIN_DEN);
    if (e.band === 'nodata') return 'no data';
    return e.band;
  }
  var BAND_CLS = {
    green: 'bg-green-100 text-green-800',
    yellow: 'bg-amber-100 text-amber-800',
    red: 'bg-red-100 text-red-800',
    unbanded: 'bg-gray-100 text-gray-500',
    insufficient: 'bg-gray-50 text-gray-400',
    nodata: 'bg-gray-50 text-gray-300',
    notcredible: 'bg-slate-100 text-slate-500',
    notinapp: 'bg-slate-100 text-slate-400 italic',
    unrecorded: 'bg-slate-100 text-slate-400 italic',
  };
  function dateOnly(d) {
    return d ? String(d).slice(0, 10) : '—';
  }
  function daysBetween(a, b) {
    var da = new Date(String(a).slice(0, 10)),
      db = new Date(String(b).slice(0, 10));
    if (isNaN(da.getTime()) || isNaN(db.getTime())) return null;
    return Math.round((db - da) / 86400000);
  }

  // ── Cases: the report's slim index for this worker, joined to the live rows ──
  // The snapshot keeps identity, dates and the first/last weights per case; the
  // children pipeline behind both workflows carries the rest (danger signs,
  // referrals, KMC status, discharge), and the visits pipeline carries every
  // weighing. Keyed on (opportunity, entity_id): a synthetic cohort reuses entity
  // ids across cloned opportunities.
  // Fetched for THIS worker, not streamed for the whole cohort: the page needs
  // one worker's ~250 cases and one case's weighings, and the framework default
  // handed it every opportunity's rows (~30 MB on the KMC cohort) to filter in
  // the browser. `config.noPipelineStream` turns that stream off; these two
  // fetches replace it, through the `pipeline-rows` endpoint which filters where
  // the data already is.
  var sChildRows = React.useState({ status: 'idle', rows: [] });
  var childState = sChildRows[0],
    setChildState = sChildRows[1];
  var childRows = childState.rows;
  var pipelinesLoaded = childState.status === 'ready';
  var sVisitRows = React.useState({ status: 'idle', key: null, rows: [] });
  var visitState = sVisitRows[0],
    setVisitState = sVisitRows[1];
  React.useEffect(
    function () {
      if (!flw) return;
      var cancelled = false;
      setChildState({ status: 'loading', rows: [], message: '' });
      var sp = scopeParams();
      var req = fetchRowsWithProgress(
        '/labs/workflow/api/' +
          definitionId() +
          '/pipeline-rows/' +
          sp +
          (sp ? '&' : '?') +
          // `scopeParams()` already carries this workflow's own scope. The opp
          // whose rows we want is a DIFFERENT question and needs its own name:
          // a second `opportunity_id` here wins `QueryDict.get` and re-scopes
          // the definition lookup to an opp that does not own it.
          'alias=children&rows_opportunity_id=' +
          encodeURIComponent(flw.opp) +
          '&username=' +
          encodeURIComponent(flw.flw),
        function (message) {
          if (!cancelled)
            setChildState({ status: 'loading', rows: [], message: message });
        },
      );
      req.promise
        .then(function (j) {
          if (!cancelled)
            setChildState({ status: 'ready', rows: j.rows || [] });
        })
        .catch(function (e) {
          // Never 'ready' with no rows on a failure: that is the shape that let a
          // 404 read as a worker with no danger signs, referrals or weighings.
          if (!cancelled)
            setChildState({
              status: 'error',
              rows: [],
              error: String((e && e.message) || e),
            });
        });
      return function () {
        cancelled = true;
        req.cancel();
      };
    },
    [
      definition && definition.id,
      instance && instance.definition_id,
      flw && flw.opp,
      flw && flw.flw,
    ],
  );
  var childByKey = React.useMemo(
    function () {
      var m = {};
      childRows.forEach(function (r) {
        m[r.opportunity_id + '|' + r.entity_id] = r;
      });
      return m;
    },
    [childRows],
  );
  var cases = React.useMemo(
    function () {
      if (!flw) return [];
      var idx = P.cases || [];
      var out = (flw.rows || [])
        .map(function (pos) {
          return idx[pos];
        })
        .filter(Boolean);
      if (!out.length) {
        // A report saved before the case index existed: the live rows still
        // know the worker's cases.
        var parts = String(selKey).split(FLW_SEP);
        out = childRows.filter(function (r) {
          return (
            String(r.opportunity_id) === parts[0] &&
            (r.username || '(unassigned)') === parts[1]
          );
        });
      }
      return out
        .map(function (c) {
          var live = childByKey[c.opportunity_id + '|' + c.entity_id] || {};
          var merged = {};
          Object.keys(live).forEach(function (k) {
            merged[k] = live[k];
          });
          Object.keys(c).forEach(function (k) {
            if (c[k] !== null && c[k] !== undefined) merged[k] = c[k];
          });
          return merged;
        })
        .sort(function (a, b) {
          return String(b.last_visit_date || '').localeCompare(
            String(a.last_visit_date || ''),
          );
        });
    },
    [P, flw, childByKey, childRows, selKey],
  );
  React.useEffect(
    function () {
      if (selCase || !caseParam || !cases.length) return;
      var hit = cases.filter(function (c) {
        return String(c.entity_id) === caseParam;
      })[0];
      if (hit) setSelCase(hit);
    },
    [cases, caseParam, selCase],
  );
  var caseKey = selCase
    ? selCase.opportunity_id + '|' + selCase.entity_id
    : null;
  React.useEffect(
    function () {
      if (!selCase) return;
      var cancelled = false;
      var key = selCase.opportunity_id + '|' + selCase.entity_id;
      setVisitState({ status: 'loading', key: key, rows: [], message: '' });
      var sp = scopeParams();
      var req = fetchRowsWithProgress(
        '/labs/workflow/api/' +
          definitionId() +
          '/pipeline-rows/' +
          sp +
          (sp ? '&' : '?') +
          // See the cases fetch: the rows opp travels under its own name.
          'alias=visits&rows_opportunity_id=' +
          encodeURIComponent(selCase.opportunity_id) +
          '&case_ids=' +
          encodeURIComponent(selCase.entity_id),
        function (message) {
          if (!cancelled)
            setVisitState({
              status: 'loading',
              key: key,
              rows: [],
              message: message,
            });
        },
      );
      req.promise
        .then(function (j) {
          if (!cancelled)
            setVisitState({ status: 'ready', key: key, rows: j.rows || [] });
        })
        .catch(function (e) {
          // See the cases fetch: a failure must not render as "No weighings
          // recorded." on a case that has five of them.
          if (!cancelled)
            setVisitState({
              status: 'error',
              key: key,
              rows: [],
              error: String((e && e.message) || e),
            });
        });
      return function () {
        cancelled = true;
        req.cancel();
      };
    },
    [definition && definition.id, instance && instance.definition_id, caseKey],
  );
  var weighingsLoaded =
    visitState.status === 'ready' && visitState.key === caseKey;
  var caseVisits = React.useMemo(
    function () {
      if (!selCase || !weighingsLoaded) return [];
      return (visitState.rows || [])
        .filter(function (v) {
          return v.visit_date;
        })
        .sort(function (a, b) {
          return String(a.visit_date).localeCompare(String(b.visit_date));
        });
    },
    [selCase, visitState, weighingsLoaded],
  );
  // The weighing photos. Visit rows carry the visit id; the framework's
  // visit-images endpoint returns each visit's images (blob ids), and the audit
  // image route serves them -- the same route the audit review pages use.
  var sImages = React.useState({});
  var imagesByVisit = sImages[0],
    setImagesByVisit = sImages[1];
  var sImagesLoading = React.useState(false);
  var imagesLoading = sImagesLoading[0],
    setImagesLoading = sImagesLoading[1];
  React.useEffect(
    function () {
      if (!selCase) return;
      var ids = caseVisits
        .map(function (v) {
          return v.id;
        })
        .filter(Boolean)
        .slice(0, 100);
      if (!ids.length) {
        setImagesByVisit({});
        return;
      }
      var cancelled = false;
      setImagesLoading(true);
      // The page's own scope travels with the call so the context middleware
      // does not redirect it; the opportunity the images belong to is in the path.
      var sp = scopeParams();
      fetch(
        '/labs/workflow/api/' +
          selCase.opportunity_id +
          '/visit-images/' +
          sp +
          (sp ? '&' : '?') +
          'visit_ids=' +
          ids.join(','),
        { credentials: 'same-origin' },
      )
        .then(function (r) {
          return r.ok ? r.json() : { visit_images: {} };
        })
        .then(function (j) {
          if (!cancelled) setImagesByVisit(j.visit_images || {});
        })
        .catch(function () {
          if (!cancelled) setImagesByVisit({});
        })
        .then(function () {
          if (!cancelled) setImagesLoading(false);
        });
      return function () {
        cancelled = true;
      };
    },
    [selCase, caseVisits],
  );
  function photoUrl(v) {
    var imgs = imagesByVisit[String(v.id)] || [];
    // Prefer the weight photo; fall back to whatever the visit carries.
    var pick =
      imgs.filter(function (i) {
        return /weight/i.test(String(i.question_id || i.name || ''));
      })[0] || imgs[0];
    return pick && pick.blob_id
      ? '/audit/image/' +
          selCase.opportunity_id +
          '/' +
          encodeURIComponent(pick.blob_id) +
          '/'
      : null;
  }

  // ── Audit of recent images ───────────────────────────────────────────────────
  // The same action the programme page offers, with the window fixed to the
  // worker's RECENT work: the last `audit_recent_days` before their latest visit.
  var AGENT_BY_LLO = cfg.scale_agent_by_llo || {};
  var UNVERIFIED_SCALE = cfg.scale_unverified_llos || [];
  var sAudit = React.useState({});
  var audit = sAudit[0],
    setAudit = sAudit[1];
  function recentWindow() {
    var last = cases
      .map(function (c) {
        return c.last_visit_date;
      })
      .filter(Boolean)
      .sort()
      .pop();
    last = dateOnly(
      last || (P.meta && P.meta.as_of) || new Date().toISOString(),
    );
    var end = new Date(last);
    var start = new Date(
      end.getTime() - (cfg.audit_recent_days || 60) * 86400000,
    );
    return { start: start.toISOString().slice(0, 10), end: last };
  }
  function auditRecent() {
    if (!flw) return;
    var range = recentWindow();
    var agent = AGENT_BY_LLO[flw.llo];
    setAudit({ status: 'running' });
    actions
      .createAudit({
        opportunities: [{ id: flw.opp, name: oppLabel(flw.opp) }],
        criteria: {
          audit_type: 'date_range',
          granularity: 'per_flw',
          title: 'KMC image review — ' + flw.flw + ' (' + flw.llo + ')',
          start_date: range.start,
          end_date: range.end,
          count_per_flw: cfg.audit_count_per_flw || 25,
          related_fields: [
            {
              image_path:
                cfg.weight_image_path || 'anthropometric/upload_weight_image',
              field_path:
                cfg.weight_value_path || 'anthropometric/child_weight_visit',
              label: 'Weight entered',
              filter_by_image: cfg.audit_images_only === true,
              filter_by_field: false,
            },
          ],
          selected_flw_user_ids: [flw.flw],
        },
        workflow_run_id: instance && instance.id,
        ai_agent_id: agent || undefined,
      })
      .then(function (result) {
        if (!result || !result.success)
          throw new Error((result && result.error) || 'audit creation failed');
        setAudit({ status: 'created', taskId: result.task_id, range: range });
      })
      .catch(function (err) {
        setAudit({
          status: 'error',
          message: String((err && err.message) || err),
        });
      });
  }

  // ── Growth chart ─────────────────────────────────────────────────────────────
  // Weight against age: postmenstrual age when gestational age is known (the axis
  // a preterm standard uses), else days since birth, else days since the first
  // weighing. Every weighing is a point with its value; the dashed line is the
  // 15 g/kg/day target the early growth rate is graded against, compounding from
  // the first weight.
  // The weight entered at registration (birth weight, hollow marker) is the
  // series' first point and the line runs through it: it used to float apart
  // from the weighings, which read as a gap in the record rather than a
  // reported-not-measured value (Jon, 2026-09-10). The hollow marker keeps that
  // distinction. A loss inside the first week is called out as expected, not
  // painted red.
  function weighingPoints(visits, c) {
    var weighed = visits.filter(function (v) {
      return (
        v.weight_g !== null &&
        v.weight_g !== undefined &&
        !isNaN(Number(v.weight_g))
      );
    });
    var origin = (c && c.dob) || (weighed[0] && weighed[0].visit_date);
    if (!origin) return { pts: [], origin: null, dob: false };
    var pts = weighed
      .map(function (v) {
        return {
          x: daysBetween(origin, v.visit_date),
          y: Number(v.weight_g),
          date: dateOnly(v.visit_date),
          v: v,
        };
      })
      .filter(function (p) {
        return p.x !== null;
      });
    if (c && c.birth_weight_g && c.dob)
      pts.unshift({
        x: 0,
        y: Number(c.birth_weight_g),
        date: dateOnly(c.dob),
        birth: true,
      });
    // Velocity since the previous point, g/kg/day.
    for (var i = 1; i < pts.length; i++) {
      var d = pts[i].x - pts[i - 1].x;
      pts[i].vel =
        d > 0 && pts[i - 1].y > 0
          ? (pts[i].y - pts[i - 1].y) / ((pts[i - 1].y / 1000) * d)
          : null;
      pts[i].days = d;
    }
    return { pts: pts, origin: origin, dob: !!(c && c.dob) };
  }
  function GrowthChart(props) {
    var pts = props.pts;
    var gaDays = props.gaWks ? Math.round(Number(props.gaWks) * 7) : null;
    var pma = !!(gaDays && props.dob);
    if (!pts.length)
      return (
        <div className="text-xs text-gray-400 py-10 text-center">
          No dated weights for this case.
        </div>
      );
    var W = 900,
      H = 320,
      PL = 56,
      PR = 64,
      PT = 22,
      PB = 40;
    var xs = pts.map(function (p) {
      return p.x;
    });
    var ys = pts.map(function (p) {
      return p.y;
    });
    var xmin = Math.min(0, Math.min.apply(null, xs));
    var xmax = Math.max(28, Math.max.apply(null, xs) + 3);
    var w0 = pts[0].y,
      x0 = pts[0].x;
    var ref = [];
    var stepDays = Math.max(1, Math.ceil((xmax - x0) / 80));
    for (var d = x0; d <= xmax; d += stepDays)
      ref.push({ x: d, y: w0 * Math.pow(1.015, d - x0) });
    var ymax =
      Math.max.apply(
        null,
        ys.concat(
          ref.map(function (p) {
            return p.y;
          }),
        ),
      ) * 1.08;
    var ymin = Math.max(0, Math.min.apply(null, ys) * 0.88);
    function X(x) {
      return PL + ((x - xmin) / (xmax - xmin || 1)) * (W - PL - PR);
    }
    function Y(y) {
      return PT + (1 - (y - ymin) / (ymax - ymin || 1)) * (H - PT - PB);
    }
    var yticks = [];
    for (var i = 0; i <= 4; i++) yticks.push(ymin + ((ymax - ymin) * i) / 4);
    var xstep = xmax - xmin > 120 ? 28 : xmax - xmin > 56 ? 14 : 7;
    var xticks = [];
    for (var t = Math.ceil(xmin / xstep) * xstep; t <= xmax; t += xstep)
      xticks.push(t);
    function xlabel(dayN) {
      if (!pma) return 'day ' + dayN;
      var total = gaDays + dayN;
      return Math.floor(total / 7) + 'w' + (total % 7 ? '+' + (total % 7) : '');
    }
    var early = pts.filter(function (p) {
      return !p.birth && p.vel !== null && p.vel < 0 && p.x <= 7;
    })[0];
    return (
      <svg
        viewBox={'0 0 ' + W + ' ' + H}
        className="w-full"
        style={{ height: 'auto' }}
      >
        {yticks.map(function (v, i) {
          return (
            <g key={'y' + i}>
              <line x1={PL} x2={W - PR} y1={Y(v)} y2={Y(v)} stroke="#f1f5f9" />
              <text
                x={PL - 8}
                y={Y(v) + 3}
                fontSize="10"
                fill="#94a3b8"
                textAnchor="end"
              >
                {(v / 1000).toFixed(1)} kg
              </text>
            </g>
          );
        })}
        <line x1={PL} x2={W - PR} y1={H - PB} y2={H - PB} stroke="#e2e8f0" />
        {xticks.map(function (v) {
          return (
            <text
              key={'x' + v}
              x={X(v)}
              y={H - PB + 16}
              fontSize="10"
              fill="#94a3b8"
              textAnchor="middle"
            >
              {xlabel(v)}
            </text>
          );
        })}
        <text
          x={W - PR}
          y={H - 6}
          fontSize="10"
          fill="#94a3b8"
          textAnchor="end"
        >
          {pma
            ? 'postmenstrual age (' + Math.floor(gaDays / 7) + 'w at birth)'
            : props.dob
              ? 'days since birth'
              : 'days since first weighing'}
        </text>
        <polyline
          fill="none"
          stroke="#94a3b8"
          strokeWidth="1.5"
          strokeDasharray="5 4"
          points={ref
            .map(function (p) {
              return X(p.x) + ',' + Y(p.y);
            })
            .join(' ')}
        />
        <text
          x={X(ref[ref.length - 1].x) + 4}
          y={Y(ref[ref.length - 1].y) + 3}
          fontSize="10"
          fill="#94a3b8"
        >
          15 g/kg/day
        </text>
        <polyline
          fill="none"
          stroke="#4f46e5"
          strokeWidth="2.5"
          strokeLinejoin="round"
          points={pts
            .map(function (p) {
              return X(p.x) + ',' + Y(p.y);
            })
            .join(' ')}
        />
        {pts.map(function (p, i) {
          var last = i === pts.length - 1;
          return (
            <g key={i}>
              {last && (
                <circle
                  cx={X(p.x)}
                  cy={Y(p.y)}
                  r="10"
                  fill="none"
                  stroke="#4f46e5"
                  strokeWidth="1"
                  opacity="0.45"
                />
              )}
              <circle
                cx={X(p.x)}
                cy={Y(p.y)}
                r={p.birth ? 4.5 : 4}
                fill={p.birth ? '#ffffff' : '#4f46e5'}
                stroke="#4f46e5"
                strokeWidth="2"
              >
                <title>
                  {(p.birth ? 'birth · ' : '') + p.date + ' · ' + p.y + ' g'}
                </title>
              </circle>
              <text
                x={X(p.x)}
                y={Y(p.y) - 11}
                fontSize="10"
                fontWeight="500"
                fill={p === early ? '#b42318' : '#4f46e5'}
                textAnchor="middle"
              >
                {nCount(p.y)}
              </text>
            </g>
          );
        })}
        {early && (
          <g>
            <line
              x1={X(early.x)}
              x2={X(early.x)}
              y1={Y(early.y) + 8}
              y2={Y(early.y) + 30}
              stroke="#b42318"
              strokeWidth="1"
            />
            <text
              x={X(early.x)}
              y={Y(early.y) + 42}
              fontSize="10"
              fill="#b42318"
              textAnchor="middle"
            >
              {nCount(early.y - pts[pts.indexOf(early) - 1].y) +
                ' g · expected loss'}
            </text>
          </g>
        )}
      </svg>
    );
  }

  // ── The scorecard, in the programme report's own header ────────────────────
  // Same fifteen columns, same groups, same order as the programme page, so a
  // worker's row reads against the programme, organisation and opportunity rows
  // without relearning the table. The case table below reuses the header with
  // case-level labels.
  var SCORECARD = [
    {
      id: 'total_cases',
      label: 'Total',
      caseLabel: 'Counted',
      title: 'Total cases',
    },
    {
      id: 'registered_cases',
      label: 'Reg',
      caseLabel: 'Reg',
      title: 'Registered',
    },
    {
      id: 'started_cases',
      label: 'Started',
      caseLabel: 'Started',
      title: 'Started — two or more follow-up visits',
    },
    {
      id: 'median_gestational_age',
      label: 'Med GA',
      caseLabel: 'GA',
      title: 'Median gestational age, weeks',
    },
    {
      id: 'median_birthweight',
      label: 'Med BW',
      caseLabel: 'BW',
      title: 'Median birthweight, g',
    },
    {
      id: 'visits_per_case',
      label: 'Visits/case',
      caseLabel: 'Visits',
      title: 'Mean visits per case',
    },
    {
      id: 'pct_enrolled_within_3d',
      label: '%1st≤3d',
      caseLabel: '1st≤3d',
      title: '% first visit within 3 days of discharge',
    },
    {
      id: 'pct_slow_growth',
      label: 'Qual N',
      caseLabel: 'Qual',
      title:
        'Qualifying SVNs — the shared denominator of the four growth-quality columns',
      denOnly: true,
    },
    {
      id: 'pct_slow_growth',
      label: '%slow',
      caseLabel: 'Slow',
      title: '% slow growth, of qualifying SVNs',
    },
    {
      id: 'pct_healthy_growth',
      label: '%healthy',
      caseLabel: 'Healthy',
      title: '% healthy growth, of qualifying SVNs',
    },
    {
      id: 'pct_fast_growth',
      label: '%fast',
      caseLabel: 'Fast',
      title: '% fast growth, of qualifying SVNs',
    },
    {
      id: 'pct_incomplete_growth_data',
      label: '%incompl',
      caseLabel: 'Incompl',
      title: '% incomplete growth data, of qualifying SVNs',
    },
    {
      id: 'mortality',
      label: 'Mortality',
      caseLabel: 'Outcome',
      title: 'Mortality — shown only where death recording is credible',
    },
    {
      id: 'weight_rounding_rate',
      label: 'Round%',
      caseLabel: 'Rounded',
      title: 'Weight rounding rate',
    },
    {
      id: 'pct_impossible_weight_changes',
      label: '%imposs',
      caseLabel: 'Implausible',
      title: '% impossible weight changes',
    },
  ];
  var SCORECARD_GROUPS = [
    { label: 'Scale', span: 3 },
    { label: 'Cohort', span: 2 },
    { label: 'Enrolment & visits', span: 3 },
    { label: 'Growth quality (of Qual N)', span: 4 },
    { label: 'Outcome', span: 1 },
    { label: 'Data quality', span: 2 },
  ];
  var N_BY_ID = React.useMemo(
    function () {
      var m = {};
      N_LIST.forEach(function (x) {
        m[x.id] = x;
      });
      return m;
    },
    [N_LIST],
  );
  function tintFor(e) {
    if (!e) return '';
    if (e.band === 'green') return 'bg-green-50 text-green-800';
    if (e.band === 'yellow') return 'bg-amber-50 text-amber-800';
    if (e.band === 'red') return 'bg-red-50 text-red-800';
    if (e.band === 'notcredible') return 'text-slate-400 italic';
    if (e.band === 'insufficient') return 'text-gray-400';
    return '';
  }
  function scoreCell(c, ind) {
    var e = ind && ind[c.id];
    if (!e) return '—';
    if (c.denOnly) return e.n ? nCount(e.n) : '—';
    var m = N_BY_ID[c.id] || {};
    if (e.band === 'insufficient')
      return <span className="text-gray-400">n&lt;{m.minDen || MIN_DEN}</span>;
    if (e.value === null || e.value === undefined) return '—';
    var v = Number(e.value);
    if (m.unit === '%') return (100 * v).toFixed(1) + '%';
    if (m.unit === 'g') return nCount(v);
    if (m.unit === 'wks') return String(Math.round(v * 10) / 10);
    if (c.id === 'visits_per_case') return v.toFixed(1);
    return nCount(v);
  }
  // `table` makes the header sortable (the cases table); the scope scorecard
  // passes none, because its rows are a fixed programme -> worker ladder. Every
  // indicator's definition is compiled at worker scope: this page is a worker.
  // Scorecard columns sort by position (`col<i>`): N09 is two columns.
  function ScorecardHead(props) {
    var lead = props.lead || [];
    var forCases = !!props.forCases;
    var table = props.table || null;
    return (
      <thead>
        <tr className="text-[10px] uppercase tracking-wide text-gray-400 border-b border-gray-200">
          {lead.map(function (l, i) {
            return <th key={'l' + i} className="px-3 py-1"></th>;
          })}
          {SCORECARD_GROUPS.map(function (g) {
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
        </tr>
        <tr className="text-xs text-gray-500 border-b border-gray-100">
          {lead.map(function (l, i) {
            var col = typeof l === 'string' ? { label: l } : l;
            return headCell({
              key: 'l' + i,
              label: col.label,
              align: 'left',
              table: col.sortKey ? table : null,
              sortKey: col.sortKey,
              className:
                (i === 0 ? 'px-3' : 'px-1.5') +
                ' py-2 text-left font-semibold text-gray-600 whitespace-nowrap',
            });
          })}
          {SCORECARD.map(function (c, i) {
            return headCell({
              key: i,
              label: forCases ? c.caseLabel : c.label,
              sub: c.id,
              title: c.title,
              def: c.id,
              scope: 'flw',
              table: table,
              sortKey: 'col' + i,
              className:
                'px-1.5 py-2 text-right whitespace-nowrap font-semibold text-gray-600',
            });
          })}
        </tr>
      </thead>
    );
  }
  function scorecardRow(label, ind, highlight) {
    return (
      <tr
        key={label}
        className={
          'border-t border-gray-100 ' +
          (highlight ? 'bg-indigo-50 font-semibold' : '')
        }
      >
        <td className="px-3 py-2 text-left text-gray-900 whitespace-nowrap">
          {label}
        </td>
        {SCORECARD.map(function (c, i) {
          var e = ind && ind[c.id];
          return (
            <td
              key={i}
              className={
                'px-1.5 py-2 text-right tabular-nums ' +
                (c.denOnly ? '' : tintFor(e))
              }
            >
              {scoreCell(c, ind)}
            </td>
          );
        })}
      </tr>
    );
  }

  // ── Each case's contribution: the same measures at the `case` scope ────────
  // The registry evaluated one grouping level further down, for this worker's
  // visits only, as of the report's date -- served by the semantic endpoint of
  // the programme workflow this page reads, so a case row and the worker's row
  // above it are the same computation. At case scope a rate is 0 or 100 with a
  // denominator of 0 or 1 (in the denominator, and whether it counted), a median
  // is the baby's own value, visits-per-case is the baby's count. Nothing is
  // re-derived here.
  var sCaseRows = React.useState({ status: 'idle', byCase: {}, error: null });
  var caseRows = sCaseRows[0],
    setCaseRows = sCaseRows[1];
  // Retry is a new request, not a re-render: bumping this re-runs the effect.
  var sCaseTry = React.useState(0);
  var caseTry = sCaseTry[0],
    setCaseTry = sCaseTry[1];
  // Seconds since the request went out, so a slow table says it is working
  // rather than looking empty.
  var sCaseElapsed = React.useState(0);
  var caseElapsed = sCaseElapsed[0],
    setCaseElapsed = sCaseElapsed[1];
  var flwKeyForRows = flw ? flw.key : null;
  var asOfForRows = (P.meta && P.meta.as_of) || '';
  React.useEffect(
    function () {
      var src = cfg.source_workflow_id;
      if (!flwKeyForRows || report.status !== 'ready') return;
      if (!src) {
        setCaseRows({
          status: 'error',
          byCase: {},
          error:
            'config.source_workflow_id is not set, so case contributions cannot be read',
        });
        return;
      }
      var cancelled = false;
      setCaseRows({ status: 'loading', byCase: {}, error: null });
      setCaseElapsed(0);
      var started = Date.now();
      var tick = window.setInterval(function () {
        if (!cancelled)
          setCaseElapsed(Math.round((Date.now() - started) / 1000));
      }, 1000);
      var sp = scopeParams();
      fetch(
        '/labs/workflow/api/' +
          src +
          '/semantic/' +
          sp +
          (sp ? '&' : '?') +
          'scopes=case&flw=' +
          encodeURIComponent(flwKeyForRows) +
          (asOfForRows ? '&as_of=' + asOfForRows : ''),
        { credentials: 'same-origin' },
      )
        .then(function (r) {
          // A gateway timeout comes back as an HTML page, not JSON; say what
          // happened instead of surfacing a JSON parse error.
          return r
            .json()
            .catch(function () {
              return {
                error:
                  r.status === 504 || r.status === 502
                    ? 'the server took too long to compute the case figures (HTTP ' +
                      r.status +
                      ')'
                    : 'the server answered HTTP ' + r.status,
              };
            })
            .then(function (j) {
              return { ok: r.ok, j: j };
            });
        })
        .then(function (res) {
          if (cancelled) return;
          if (!res.ok)
            throw new Error(res.j.error || 'could not read case contributions');
          var m = {};
          (res.j.rows || []).forEach(function (r) {
            if (r.scope === 'case' && r.case_id) m[String(r.case_id)] = r;
          });
          window.clearInterval(tick);
          setCaseRows({ status: 'ready', byCase: m, error: null });
        })
        .catch(function (e) {
          window.clearInterval(tick);
          if (!cancelled)
            setCaseRows({
              status: 'error',
              byCase: {},
              error: String((e && e.message) || e),
            });
        });
      return function () {
        cancelled = true;
        window.clearInterval(tick);
      };
    },
    [
      flwKeyForRows,
      cfg.source_workflow_id,
      report.status,
      asOfForRows,
      caseTry,
    ],
  );
  function caseScopeRow(c) {
    return caseRows.byCase[c.opportunity_id + '|' + c.entity_id] || null;
  }
  // What a case cell sorts on: what `contrib` shows it as. A tick sorts above a
  // cross; a case outside the denominator has nothing to rank and sorts last.
  function caseSortValue(c, key) {
    if (key === 'case') return String(c.entity_id || '');
    if (key === 'reg')
      return c.reg_date ? String(c.reg_date).slice(0, 10) : null;
    if (key === 'last')
      return c.last_visit_date ? String(c.last_visit_date).slice(0, 10) : null;
    var col = SCORECARD[Number(String(key).slice(3))];
    var row = caseScopeRow(c);
    if (!col || !row) return null;
    var m = col.id.toLowerCase();
    var v = row[m];
    var den = row[m + '_denominator'];
    var has = den !== null && den !== undefined && Number(den) > 0;
    if (col.denOnly) return has ? 1 : null;
    if (col.id === 'total_cases') return 1;
    if (col.id === 'median_gestational_age' || col.id === 'median_birthweight')
      return v === null || v === undefined ? null : Number(v);
    if (col.id === 'visits_per_case')
      return has ? Number(v) : Number(c.total_visits) || null;
    if (col.id === 'weight_rounding_rate') return has ? Number(v) : null;
    return has ? (Number(v) > 0 ? 1 : 0) : null;
  }
  var YES = <span className="text-green-700 font-semibold">✓</span>;
  var NO = <span className="text-red-700 font-semibold">✗</span>;
  var DASH = <span className="text-gray-300">—</span>;
  var DOT = <span className="text-indigo-600 font-bold">●</span>;
  function contrib(c, row, rec) {
    if (!row)
      return caseRows.status === 'loading' ? (
        <span className="text-gray-300">…</span>
      ) : (
        DASH
      );
    var m = c.id.toLowerCase();
    var v = row[m];
    var den = row[m + '_denominator'];
    var has = den !== null && den !== undefined && Number(den) > 0;
    var pos = has && Number(v) > 0;
    if (c.denOnly) return has ? YES : DASH;
    if (c.id === 'total_cases') return YES;
    if (
      c.id === 'registered_cases' ||
      c.id === 'started_cases' ||
      c.id === 'pct_enrolled_within_3d'
    )
      return has ? (pos ? YES : NO) : DASH;
    if (c.id === 'median_gestational_age')
      return v === null || v === undefined
        ? DASH
        : String(Math.round(Number(v) * 10) / 10);
    if (c.id === 'median_birthweight')
      return v === null || v === undefined ? DASH : nCount(v);
    if (c.id === 'visits_per_case')
      return has ? (
        nCount(v)
      ) : (
        <span
          className="text-gray-400"
          title="not yet 42 days since the first visit"
        >
          {rec.total_visits || '—'}
        </span>
      );
    if (
      c.id === 'pct_slow_growth' ||
      c.id === 'pct_healthy_growth' ||
      c.id === 'pct_fast_growth' ||
      c.id === 'pct_incomplete_growth_data'
    )
      return has ? (pos ? DOT : DASH) : '';
    if (c.id === 'mortality')
      return has ? (
        pos ? (
          <span className="text-red-700 font-semibold">died</span>
        ) : (
          <span className="text-gray-500">alive</span>
        )
      ) : (
        DASH
      );
    if (c.id === 'weight_rounding_rate')
      return has
        ? Math.round((Number(v) / 100) * Number(den)) + '/' + den
        : DASH;
    if (c.id === 'pct_impossible_weight_changes')
      return has ? (
        pos ? (
          <span className="text-red-700 font-semibold">yes</span>
        ) : (
          <span className="text-gray-500">no</span>
        )
      ) : (
        DASH
      );
    return DASH;
  }

  // ── Views ────────────────────────────────────────────────────────────────────
  var backHref = cfg.source_workflow_id
    ? '/labs/workflow/' +
      cfg.source_workflow_id +
      '/run/?run_id=' +
      report.runId +
      (scopeParams() ? '&' + scopeParams().slice(1) : '')
    : null;

  if (report.status === 'loading')
    return (
      <div className="p-6 text-sm text-gray-500">
        Reading the programme report…
      </div>
    );
  if (report.status === 'error')
    return (
      <div className="p-6">
        <div className="bg-red-50 border border-red-200 text-red-800 rounded-xl p-4 text-sm">
          {report.error}
        </div>
      </div>
    );

  function WorkerPicker() {
    var rows = sortRows(
      'picker',
      byFLW.slice().sort(function (a, b) {
        return b.reds - a.reds || b.n - a.n;
      }),
      function (f, key) {
        if (key === 'worker') return String(f.flw || '').toLowerCase();
        if (key === 'org') return String(oppLabel(f.opp)).toLowerCase();
        if (key === 'cases') return Number(f.n) || 0;
        return Number(f.reds) || 0;
      },
    );
    function pickerTh(label, key, align) {
      return headCell({
        key: key,
        label: label,
        align: align,
        table: 'picker',
        sortKey: key,
        className:
          'px-3 py-2 ' + (align === 'left' ? 'text-left' : 'text-right'),
      });
    }
    return (
      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <div className="px-4 py-3 border-b border-gray-100">
          <div className="font-medium text-gray-900">Choose a worker</div>
          <div className="text-xs text-gray-500">
            {rows.length} workers in the report as of{' '}
            {(P.meta && P.meta.as_of) || '—'}. Opening a worker from the
            programme report brings you here directly.
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 text-gray-500">
              <tr>
                {pickerTh('Worker', 'worker', 'left')}
                {pickerTh('Organisation', 'org', 'left')}
                {pickerTh('Cases', 'cases', 'right')}
                {pickerTh('Red', 'reds', 'right')}
              </tr>
            </thead>
            <tbody>
              {rows.map(function (f) {
                return (
                  <tr
                    key={f.key}
                    className="border-t border-gray-100 cursor-pointer hover:bg-indigo-50"
                    onClick={function () {
                      pickWorker(f.key);
                    }}
                  >
                    <td className="px-3 py-2 font-medium text-gray-900">
                      {f.flw || '(unassigned)'}
                    </td>
                    <td className="px-3 py-2 text-gray-600">
                      {oppLabel(f.opp)}
                    </td>
                    <td className="px-3 py-2 text-right">{f.n}</td>
                    <td className="px-3 py-2 text-right">
                      {f.reds ? (
                        <span className="px-2 py-0.5 rounded text-xs bg-red-100 text-red-800">
                          {f.reds}
                        </span>
                      ) : (
                        '0'
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  function CaseDetail(props) {
    var c = props.c;
    var built = weighingPoints(caseVisits, c);
    var pts = built.pts;
    var weighed = pts.filter(function (p) {
      return !p.birth;
    });
    var idx = cases.indexOf(c);
    var prev = idx > 0 ? cases[idx - 1] : null;
    var next = idx >= 0 && idx < cases.length - 1 ? cases[idx + 1] : null;
    // Early growth: the velocity over the first 21 days after the first
    // weighing, per kg of the mean weight in that window -- the rule the
    // registry's mean_early_growth_rate averages.
    var first = weighed[0];
    var earlyEnd = weighed.filter(function (p) {
      return first && p.x - first.x <= 21;
    });
    var earlyLast = earlyEnd[earlyEnd.length - 1];
    var earlyMean = earlyEnd.length
      ? earlyEnd.reduce(function (a, p) {
          return a + p.y;
        }, 0) / earlyEnd.length
      : null;
    // The registry's own figure when the baby is in it (qualifying, with good
    // weight data); the descriptive window here is only a fallback.
    var scopeRow = caseScopeRow(c);
    var growthRate =
      scopeRow && Number(scopeRow.mean_early_growth_rate_denominator) > 0
        ? Number(scopeRow.mean_early_growth_rate)
        : null;
    var earlyVel =
      growthRate !== null
        ? growthRate
        : first &&
            earlyLast &&
            earlyLast !== first &&
            earlyMean > 0 &&
            earlyLast.x > first.x
          ? (earlyLast.y - first.y) /
            ((earlyMean / 1000) * (earlyLast.x - first.x))
          : null;
    var gain =
      first && weighed.length > 1
        ? weighed[weighed.length - 1].y - first.y
        : null;
    var span =
      first && weighed.length > 1
        ? weighed[weighed.length - 1].x - first.x
        : null;
    var rounded = weighed.filter(function (p) {
      return p.y % 100 === 0;
    }).length;
    var implausible = weighed.filter(function (p) {
      return p.vel !== null && (p.vel > 50 || (p.vel < 0 && p.x > 7));
    });
    var earlyLoss = weighed.filter(function (p) {
      return p.vel !== null && p.vel < 0 && p.x <= 7;
    })[0];
    var photos = weighed.filter(function (p) {
      return photoUrl(p.v);
    }).length;
    // The cards below are computed from the weight series. Until it arrives --
    // or when it failed -- they have nothing to say, and "needs two weighings"
    // under a "—" misdescribed a failed request on a case with five.
    var seriesNote = weighingsLoaded
      ? ''
      : visitState.status === 'error'
        ? 'weight series not loaded'
        : 'loading weighings…';
    // Discharge, skin-to-skin, danger signs, referrals and alive-at-last-visit
    // exist only on the live `children` rows; the report's case index does not
    // carry them. The panel opens from the index, so these can be absent because
    // the rows are still coming or because the read failed -- and a bare "—"
    // said "not recorded" in both cases. Say which it is.
    function live(v) {
      if (childState.status === 'loading') return 'loading…';
      if (childState.status === 'error') return 'not loaded';
      return v;
    }
    var facts = [
      [
        'Born',
        dateOnly(c.dob) +
          (c.gestational_age_wks ? ' · ' + c.gestational_age_wks + ' wks' : ''),
      ],
      [
        'Birth weight',
        c.birth_weight_g ? nCount(c.birth_weight_g) + ' g' : '—',
      ],
      ['Sex', c.gender || '—'],
      [
        'Discharged',
        c.hospital_discharge_date
          ? dateOnly(c.hospital_discharge_date)
          : live('—'),
      ],
      ['Registered', dateOnly(c.reg_date)],
      null,
      ['KMC status', c.last_kmc_status || '—'],
      [
        'Skin-to-skin',
        c.kmc_hours_mean
          ? Number(c.kmc_hours_mean).toFixed(1) + ' h/day'
          : live('—'),
      ],
      [
        'Danger signs',
        c.danger_visits === undefined
          ? live('—')
          : c.danger_visits + (c.danger_visits === 1 ? ' visit' : ' visits'),
      ],
      [
        'Referrals',
        c.referral_visits === undefined ? live('—') : c.referral_visits,
      ],
      [
        'Alive at last visit',
        c.alive_last === undefined || c.alive_last === null
          ? live('—')
          : String(c.alive_last),
      ],
      [
        'Visits',
        (c.total_visits || caseVisits.length || '—') +
          ' · last ' +
          dateOnly(c.last_visit_date),
      ],
    ];
    function velChip(p) {
      if (p.vel === null || p.vel === undefined) return null;
      var cls =
        p.vel < 0 && p.x <= 7
          ? 'bg-amber-100 text-amber-800'
          : p.vel < 0 || p.vel > 50
            ? 'bg-red-100 text-red-800'
            : p.vel >= 10
              ? 'bg-green-100 text-green-800'
              : 'bg-gray-100 text-gray-600';
      return (
        <span
          className={'px-1.5 py-0.5 rounded text-[10px] font-semibold ' + cls}
          title="g/kg/day since the previous weighing"
        >
          {(p.vel > 0 ? '+' : '') + p.vel.toFixed(1)}
        </span>
      );
    }
    return (
      <div className="bg-white border border-indigo-200 rounded-xl overflow-hidden">
        <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between gap-3 flex-wrap">
          <div className="text-sm">
            <button
              type="button"
              className="text-indigo-600 hover:underline"
              onClick={function () {
                openCase(null);
              }}
            >
              close
            </button>
            <span className="text-gray-300 mx-2">·</span>
            <span className="font-medium text-gray-900">
              Case {String(c.entity_id).slice(0, 8)}
            </span>
            <span className="text-gray-400 ml-2 text-xs font-mono">
              {c.entity_id}
            </span>
            <span className="text-gray-400 ml-2 text-xs">
              {oppLabel(c.opportunity_id)}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={!prev}
              className={
                'px-2.5 py-1 rounded text-xs border ' +
                (prev
                  ? 'border-gray-200 text-gray-700 hover:bg-gray-50'
                  : 'border-gray-100 text-gray-300')
              }
              onClick={function () {
                if (prev) openCase(prev);
              }}
            >
              ← Previous
            </button>
            <button
              type="button"
              disabled={!next}
              className={
                'px-2.5 py-1 rounded text-xs border ' +
                (next
                  ? 'border-gray-200 text-gray-700 hover:bg-gray-50'
                  : 'border-gray-100 text-gray-300')
              }
              onClick={function () {
                if (next) openCase(next);
              }}
            >
              Next →
            </button>
          </div>
        </div>
        <div
          className="grid"
          style={{ gridTemplateColumns: 'minmax(0, 1fr) 300px' }}
        >
          <div>
            <div className="px-4 pt-4">
              <div className="flex items-baseline justify-between gap-3 flex-wrap mb-1">
                <div className="text-sm font-medium text-gray-900">
                  {built.dob && c.gestational_age_wks
                    ? 'Weight for postmenstrual age'
                    : 'Weight for age'}
                </div>
                <div className="flex gap-4 text-[11px] text-gray-500">
                  <span>
                    <i className="inline-block w-3 border-t-2 border-indigo-600 align-middle mr-1"></i>
                    weighings
                  </span>
                  <span>
                    <i className="inline-block w-3 border-t-2 border-dashed border-gray-400 align-middle mr-1"></i>
                    15 g/kg/day
                  </span>
                </div>
              </div>
              {weighingsLoaded ? (
                <GrowthChart
                  pts={pts}
                  gaWks={c.gestational_age_wks}
                  dob={built.dob}
                />
              ) : visitState.status === 'error' ? (
                <div className="text-xs text-red-600 py-10 text-center">
                  Could not load the weight series
                  {visitState.error ? ' (' + visitState.error + ')' : ''}. This
                  is a failed request, not an empty case — reload the page.
                </div>
              ) : (
                <div className="text-xs text-gray-400 py-10 text-center flex items-center justify-center gap-2">
                  <i className="fa-solid fa-spinner fa-spin" />
                  <span>
                    {visitState.message || 'Loading the weight series…'}
                  </span>
                </div>
              )}
            </div>
            <div className="px-4 pb-2">
              <div className="text-xs text-gray-500 mb-2">
                Photos
                <span className="text-gray-400">
                  {imagesLoading
                    ? ' · loading photos…'
                    : ' · ' +
                      photos +
                      ' of ' +
                      weighed.length +
                      ' weighings photographed'}
                </span>
              </div>
              <div
                className="grid gap-2"
                style={{
                  gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))',
                }}
              >
                {weighed.map(function (p, i) {
                  var url = photoUrl(p.v);
                  return (
                    <div
                      key={i}
                      className="bg-gray-50 border border-gray-200 rounded-lg p-1.5"
                    >
                      {url ? (
                        <a href={url} target="_blank" rel="noopener">
                          <img
                            src={url}
                            alt={'weighing ' + p.date}
                            loading="lazy"
                            className="w-full aspect-square object-cover rounded"
                          />
                        </a>
                      ) : (
                        <div className="w-full aspect-square rounded bg-gray-100 flex items-center justify-center text-[10px] text-gray-400">
                          {imagesLoading ? '…' : 'no photo'}
                        </div>
                      )}
                      <div className="flex items-center justify-between mt-1 text-[11px]">
                        <span className="text-gray-500">day {p.x}</span>
                        <span className="font-semibold text-gray-900">
                          {nCount(p.y)}
                        </span>
                      </div>
                      <div className="flex items-center justify-between text-[10px] text-gray-400">
                        <span>{p.date.slice(5)}</span>
                        {velChip(p)}
                      </div>
                    </div>
                  );
                })}
                {!weighed.length && (
                  <div className="col-span-6 text-xs text-gray-400 py-4 text-center">
                    {visitState.status === 'error'
                      ? 'Could not load weighings — reload the page.'
                      : weighingsLoaded
                        ? 'No weighings recorded.'
                        : 'Loading…'}
                  </div>
                )}
              </div>
            </div>
            <div
              className="grid gap-2 px-4 pb-4 pt-2"
              style={{
                gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
              }}
            >
              <div className="bg-gray-50 border border-gray-200 rounded-lg px-3 py-2">
                <div className="text-[10px] uppercase tracking-wide text-gray-400">
                  Early growth
                </div>
                <div className="text-lg font-semibold text-gray-900">
                  {earlyVel === null ? '—' : earlyVel.toFixed(1) + ' g/kg/day'}
                </div>
                <div className="text-[10px] text-gray-400">
                  {growthRate !== null
                    ? 'target 15'
                    : seriesNote
                      ? seriesNote
                      : first && earlyLast && earlyLast !== first
                        ? 'days ' +
                          first.x +
                          '–' +
                          earlyLast.x +
                          ' · not yet graded'
                        : 'needs two weighings'}
                </div>
              </div>
              <div className="bg-gray-50 border border-gray-200 rounded-lg px-3 py-2">
                <div className="text-[10px] uppercase tracking-wide text-gray-400">
                  Gain
                </div>
                <div className="text-lg font-semibold text-gray-900">
                  {gain === null
                    ? '—'
                    : (gain > 0 ? '+' : '') + nCount(gain) + ' g'}
                </div>
                <div className="text-[10px] text-gray-400">
                  {seriesNote ||
                    (span === null ? '' : 'over ' + span + ' days')}
                </div>
              </div>
              <div className="bg-gray-50 border border-gray-200 rounded-lg px-3 py-2">
                <div className="text-[10px] uppercase tracking-wide text-gray-400">
                  Rounded
                </div>
                <div className="text-lg font-semibold text-gray-900">
                  {weighed.length ? rounded + ' / ' + weighed.length : '—'}
                </div>
                <div className="text-[10px] text-gray-400">
                  {seriesNote || 'readings ending in 00'}
                </div>
              </div>
              <div className="bg-gray-50 border border-gray-200 rounded-lg px-3 py-2">
                <div className="text-[10px] uppercase tracking-wide text-gray-400">
                  Implausible
                </div>
                <div className="text-lg font-semibold text-gray-900">
                  {weighed.length ? implausible.length : '—'}
                </div>
                <div className="text-[10px] text-gray-400">
                  {seriesNote || 'losses after day 7 or >50 g/kg/day'}
                </div>
              </div>
            </div>
          </div>
          <div className="border-l border-gray-100 p-4">
            {earlyLoss && (
              <div className="border-l-4 border-amber-400 bg-amber-50 rounded-r-lg px-3 py-2 text-xs text-amber-900 mb-3">
                <b>Day {earlyLoss.x}:</b>{' '}
                {nCount(earlyLoss.y - pts[pts.indexOf(earlyLoss) - 1].y)} g in{' '}
                {earlyLoss.days} days ({earlyLoss.vel.toFixed(1)} g/kg/day).
                Loss in the first week is expected after birth.
              </div>
            )}
            {implausible.length > 0 && (
              <div className="border-l-4 border-red-500 bg-red-50 rounded-r-lg px-3 py-2 text-xs text-red-900 mb-3">
                <b>Check the readings:</b>{' '}
                {implausible
                  .map(function (p) {
                    return (
                      'day ' +
                      p.x +
                      ' (' +
                      (p.vel > 0 ? '+' : '') +
                      p.vel.toFixed(1) +
                      ' g/kg/day)'
                    );
                  })
                  .join(', ')}
                . A loss after the first week or a gain above 50 g/kg/day is
                usually a mis-read scale.
              </div>
            )}
            <div className="text-[10px] uppercase tracking-wide text-gray-400 mb-1">
              Record
            </div>
            <table className="min-w-full text-sm">
              <tbody>
                {facts.map(function (f, i) {
                  return f ? (
                    <tr key={i} className="border-t border-gray-100">
                      <td className="py-1.5 pr-3 text-gray-500 align-top">
                        {f[0]}
                      </td>
                      <td className="py-1.5 text-gray-900 text-right">
                        {f[1]}
                      </td>
                    </tr>
                  ) : (
                    <tr key={i}>
                      <td colSpan="2" className="py-1"></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <div className="text-[10px] uppercase tracking-wide text-gray-400 mt-4 mb-1">
              Worker
            </div>
            <div className="text-sm text-gray-900">
              {flw.flw || '(unassigned)'}
              <span className="text-gray-400"> · {flw.n} cases</span>
              {flw.reds ? (
                <span className="ml-2 px-2 py-0.5 rounded text-xs bg-red-100 text-red-800">
                  {flw.reds} red
                </span>
              ) : null}
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (!flw)
    return (
      <div className="space-y-4">
        {selKey ? (
          <div className="bg-amber-50 border border-amber-200 text-amber-800 rounded-xl p-3 text-sm">
            Worker {selKey} is not in this report.
          </div>
        ) : null}
        <WorkerPicker />
        {colDefModal()}
      </div>
    );

  var agent = AGENT_BY_LLO[flw.llo];
  var unverified = UNVERIFIED_SCALE.indexOf(flw.llo) !== -1;

  return (
    <div className="space-y-4">
      {colDefModal()}
      <div className="bg-white border border-gray-200 rounded-xl p-4">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            {backHref && (
              <a
                href={backHref}
                className="text-xs text-indigo-600 hover:underline"
              >
                ← Programme report
              </a>
            )}
            <div className="text-xl font-semibold text-gray-900 mt-1">
              {flw.flw || '(unassigned)'}
            </div>
            <div className="text-sm text-gray-500">
              {oppLabel(flw.opp)} · {flw.n} cases · as of{' '}
              {(P.meta && P.meta.as_of) || '—'}
              {flw.reds ? (
                <span className="ml-2 px-2 py-0.5 rounded text-xs bg-red-100 text-red-800">
                  {flw.reds} red
                </span>
              ) : (
                <span className="ml-2 px-2 py-0.5 rounded text-xs bg-green-100 text-green-800">
                  no red
                </span>
              )}
            </div>
          </div>
          {cfg.audit_enabled !== false && (
            <div className="text-right">
              <button
                type="button"
                disabled={audit.status === 'running'}
                onClick={auditRecent}
                className={
                  'px-3 py-1.5 rounded text-sm font-medium ' +
                  (audit.status === 'running'
                    ? 'bg-gray-200 text-gray-500'
                    : 'bg-indigo-600 text-white hover:bg-indigo-700')
                }
              >
                {audit.status === 'running'
                  ? 'Opening audit…'
                  : 'Audit recent images'}
              </button>
              <div className="text-xs text-gray-500 mt-1">
                last {cfg.audit_recent_days || 60} days, up to{' '}
                {cfg.audit_count_per_flw || 25} visits
                {agent
                  ? ' · ' +
                    (agent === 'scale_dial_read' ? 'dial' : 'digital') +
                    ' scale reader'
                  : ' · no scale reader for this organisation'}
                {unverified ? ' (hardware unconfirmed)' : ''}
              </div>
              {audit.status === 'created' && (
                <div className="mt-1 text-xs text-green-700">
                  Audit queued for {flw.flw} ({audit.range.start} to{' '}
                  {audit.range.end}). It appears under Audits for{' '}
                  {oppLabel(flw.opp)} once the sessions finish building.
                </div>
              )}
              {audit.status === 'error' && (
                <div className="mt-1 text-xs text-red-700">
                  Could not open the audit: {audit.message}
                </div>
              )}
            </div>
          )}
        </div>
        <div className="text-xs text-gray-400 mt-2">
          Figures are this worker's rows of the programme report
          {report.source === 'stored' ? ' (saved report)' : ' (live)'}; n is
          small per worker, so many indicators read n&lt;{MIN_DEN}.
        </div>
      </div>

      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <div className="px-4 py-3 border-b border-gray-100 font-medium text-gray-900">
          Scorecard
          <span className="ml-2 text-xs font-normal text-gray-400">
            the programme report&rsquo;s fifteen columns &mdash; this
            worker&rsquo;s row under the programme, organisation and opportunity
            rows
          </span>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <ScorecardHead lead={['Scope']} />
            <tbody>
              {scorecardRow('Programme', SC && SC.programme)}
              {scorecardRow(flw.llo || 'Organisation', (lloRow || {}).ind)}
              {scorecardRow(oppLabel(flw.opp), (oppRow || {}).ind)}
              {scorecardRow(flw.flw || '(unassigned)', nFLW && nFLW.ind, true)}
            </tbody>
          </table>
        </div>
      </div>

      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <div className="px-4 py-3 border-b border-gray-100 font-medium text-gray-900">
          Cases
          <span className="ml-2 text-xs font-normal text-gray-400">
            {cases.length} &middot; one row per case &middot; each cell is the
            case&rsquo;s contribution to the column above &middot; click a case
            to open it &middot; click a column name for its definition, the
            arrow to sort
          </span>
        </div>
        {caseRows.status === 'loading' && (
          <div className="px-4 py-2 text-sm bg-indigo-50 text-indigo-800 border-b border-indigo-100 flex items-center gap-2">
            <span
              className="inline-block w-3 h-3 rounded-full border-2 border-indigo-300 animate-spin"
              style={{ borderTopColor: '#4338ca' }}
            />
            Computing each case&rsquo;s figures for this worker&hellip;{' '}
            <span className="tabular-nums text-indigo-500">{caseElapsed}s</span>
          </div>
        )}
        {caseRows.status === 'error' && (
          <div className="px-4 py-2 text-sm bg-red-50 text-red-800 border-b border-red-100 flex items-center justify-between gap-3">
            <span>Could not load the case figures: {caseRows.error}</span>
            <button
              type="button"
              className="px-2.5 py-1 rounded-md text-xs font-medium border border-red-200 bg-white text-red-700 hover:bg-red-100"
              onClick={function () {
                setCaseTry(caseTry + 1);
              }}
            >
              Retry
            </button>
          </div>
        )}
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <ScorecardHead
              lead={[
                { label: 'Case', sortKey: 'case' },
                { label: 'Registered', sortKey: 'reg' },
                { label: 'Last visit', sortKey: 'last' },
              ]}
              forCases={true}
              table="cases"
            />
            <tbody>
              {sortRows('cases', cases, caseSortValue).map(function (c) {
                var row = caseScopeRow(c);
                // Match by key, not identity: the case index is rebuilt as the
                // pipeline stream delivers rows (the page renders while it loads),
                // so the object a click selected is not the object rendered next.
                var open =
                  !!selCase &&
                  selCase.entity_id === c.entity_id &&
                  String(selCase.opportunity_id) === String(c.opportunity_id);
                var out = [
                  <tr
                    key={c.opportunity_id + '|' + c.entity_id}
                    className={
                      'border-t border-gray-100 cursor-pointer hover:bg-indigo-50 ' +
                      (open ? 'bg-indigo-50' : '')
                    }
                    onClick={function () {
                      openCase(open ? null : c);
                    }}
                  >
                    <td
                      className="px-3 py-2 font-mono text-xs text-gray-700 whitespace-nowrap"
                      title={c.entity_id}
                    >
                      {String(c.entity_id).slice(0, 8)}…
                    </td>
                    <td className="px-1.5 py-2 text-gray-500 whitespace-nowrap">
                      {dateOnly(c.reg_date)}
                    </td>
                    <td className="px-1.5 py-2 text-gray-500 whitespace-nowrap">
                      {dateOnly(c.last_visit_date)}
                    </td>
                    {SCORECARD.map(function (col, i) {
                      return (
                        <td
                          key={i}
                          className="px-1.5 py-2 text-right tabular-nums"
                        >
                          {contrib(col, row, c)}
                        </td>
                      );
                    })}
                  </tr>,
                ];
                if (open)
                  out.push(
                    <tr key={c.opportunity_id + '|' + c.entity_id + '|detail'}>
                      <td
                        colSpan={3 + SCORECARD.length}
                        className="p-0 bg-indigo-50/40"
                      >
                        <div className="p-3">
                          <CaseDetail c={c} />
                        </div>
                      </td>
                    </tr>,
                  );
                return out;
              })}
              {!cases.length && (
                <tr>
                  <td
                    colSpan={3 + SCORECARD.length}
                    className="px-3 py-6 text-center text-xs text-gray-400"
                  >
                    {childState.status === 'error' ? (
                      'Could not load this worker’s cases' +
                      (childState.error ? ' (' + childState.error + ')' : '') +
                      '. This is a failed request, not an empty cohort — reload the page.'
                    ) : pipelinesLoaded ? (
                      'No cases for this worker in the report.'
                    ) : (
                      // Not just "Loading": a cold visit cache is a 40-60s read
                      // from Connect, and a static label for a minute is what
                      // made this page look broken rather than busy. The
                      // message is the server's own fetch progress when the
                      // stream is carrying it.
                      <span className="inline-flex items-center gap-2">
                        <i className="fa-solid fa-spinner fa-spin" />
                        <span>{childState.message || 'Loading cases…'}</span>
                      </span>
                    )}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="px-4 py-2 text-xs text-gray-400 border-t border-gray-100 flex items-center gap-4 flex-wrap">
          <span>
            <span className="text-green-700 font-semibold">✓</span> counts
            toward the numerator
          </span>
          <span>
            <span className="text-red-700 font-semibold">✗</span> in the
            denominator only
          </span>
          <span>
            <span className="text-gray-300">—</span> not in the denominator (not
            yet eligible, or not recorded)
          </span>
          <span>
            <span className="text-indigo-600 font-bold">●</span> this
            case&rsquo;s growth class
          </span>
        </div>
      </div>
    </div>
  );
}
