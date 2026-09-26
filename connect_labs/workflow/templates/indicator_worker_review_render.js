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
  // ══ One worker, from an indicator report, for ANY semantic registry ════════
  //
  // Opened from a worker row (`?flw=<opp>::<username>&source_run=<run>`). It
  // computes nothing: the worker's indicators and case list are the source
  // report's own payload -- the run it was opened from, or the newest saved run
  // of `config.source_workflow_id` -- and each case's visits come from the shared
  // visit pipeline, one case at a time, through `pipeline-rows`. Nouns, the
  // scorecard, the case table's columns and the reading series are the
  // payload's `display` block (semantic/display.py). ES5 dialect.
  var R = window.LabsReport;
  if (!R || !R.displayOf)
    return (
      <div className="p-6 text-sm text-red-800">
        The report library did not load (or is older than this page). Reload the
        page.
      </div>
    );
  var cfg = (definition && definition.config) || {};
  var FLW_SEP = '::';
  var search = String(window.location.search || '');
  function qp(name) {
    var m = search.match(new RegExp('[?&]' + name + '=([^&]*)'));
    return m ? decodeURIComponent(m[1].replace(/\+/g, ' ')) : null;
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
  function getJson(url) {
    return fetch(url, { credentials: 'same-origin' }).then(function (r) {
      return r
        .json()
        .catch(function () {
          return { error: 'HTTP ' + r.status };
        })
        .then(function (j) {
          if (!r.ok || j.error)
            throw new Error(j.error || j.message || 'HTTP ' + r.status);
          return j;
        });
    });
  }

  // ══ The source report's payload ═════════════════════════════════════════════
  var sourceRun = qp('source_run');
  var sReport = React.useState({ status: 'loading' });
  var report = sReport[0],
    setReport = sReport[1];
  React.useEffect(
    function () {
      var cancelled = false;
      function readRun(runId) {
        return getJson(
          withParams('/labs/workflow/api/run/' + runId + '/snapshot/preview/'),
        ).then(function (j) {
          var st = ((j.snapshot || {}).state || {}).snapshot;
          if (!st) throw new Error('the report carried no figures');
          if (!cancelled)
            setReport({
              status: 'ready',
              runId: Number(runId),
              payload: st,
              source: j.source,
            });
        });
      }
      function newestSaved() {
        var src = cfg.source_workflow_id;
        if (!src)
          return Promise.reject(
            new Error(
              'This review is not linked to a report (config.source_workflow_id).',
            ),
          );
        return getJson(
          withParams(
            '/labs/workflow/api/' + src + '/runs/history/',
            'keys=snapshot.meta.as_of',
          ),
        ).then(function (j) {
          var runs = (j.runs || []).filter(function (r) {
            return r.state && r.state['snapshot.meta.as_of'];
          });
          if (!runs.length)
            throw new Error('The report has no saved run to read yet.');
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
  var payload = report.payload;
  var P = payload || {};
  var D = React.useMemo(
    function () {
      return R.displayOf(P);
    },
    [payload],
  );
  var ENT = D.entity,
    WRK = D.worker;
  var MEASURES = (P.cMeasures || []).filter(function (m) {
    return m && m.indicator;
  });
  var M_BY_ID = {};
  MEASURES.forEach(function (m) {
    M_BY_ID[m.indicator] = m;
  });
  var LAYOUT = R.scorecardLayout(P, D);
  var MIN_DEN = 20;
  MEASURES.forEach(function (m) {
    if (m.min_denominator && MIN_DEN === 20) MIN_DEN = m.min_denominator;
  });
  var asOf = (P.meta && P.meta.as_of) || '';

  // ══ The worker ══════════════════════════════════════════════════════════════
  var sKey = React.useState(qp('flw'));
  var selKey = sKey[0],
    setSelKey = sKey[1];
  var flw = (P.byFLW || []).filter(function (f) {
    return f.key === selKey;
  })[0];
  var cases = React.useMemo(
    function () {
      if (!flw) return [];
      var idx = P.cases || [];
      return (flw.rows || [])
        .map(function (i) {
          return typeof i === 'number' ? idx[i] : i;
        })
        .filter(Boolean)
        .sort(function (a, b) {
          return String(b.last_visit_date || '').localeCompare(
            String(a.last_visit_date || ''),
          );
        });
    },
    [payload, selKey],
  );
  // Peers: the worker's own cohort as the builder banded it.
  var peers = React.useMemo(
    function () {
      if (!flw) return { start: [], caseload: [] };
      var all = P.byFLW || [];
      return {
        start: flw.startMonth
          ? all.filter(function (f) {
              return f.startMonth === flw.startMonth;
            })
          : [],
        caseload:
          flw.caseloadBand !== null && flw.caseloadBand !== undefined
            ? all.filter(function (f) {
                return f.caseloadBand === flw.caseloadBand;
              })
            : [],
      };
    },
    [payload, selKey],
  );
  function median(list, id) {
    var vs = list
      .map(function (f) {
        var e = f.ind && f.ind[id];
        return R.drawable(e) ? Number(e.value) : null;
      })
      .filter(function (v) {
        return v !== null && !isNaN(v);
      })
      .sort(function (a, b) {
        return a - b;
      });
    if (!vs.length) return null;
    var mid = Math.floor(vs.length / 2);
    return vs.length % 2 ? vs[mid] : (vs[mid - 1] + vs[mid]) / 2;
  }

  // ══ One case's visits, from the shared visit pipeline ═══════════════════════
  var VISITS = D.visits_pipeline || 'visits';
  var sCase = React.useState(null);
  var selCase = sCase[0],
    setSelCase = sCase[1];
  var sVisits = React.useState({ status: 'idle', rows: [] });
  var visits = sVisits[0],
    setVisits = sVisits[1];
  React.useEffect(
    function () {
      if (!selCase) return;
      var cancelled = false;
      setVisits({ status: 'loading', rows: [] });
      getJson(
        withParams(
          '/labs/workflow/api/' + definitionId() + '/pipeline-rows/',
          'alias=' +
            encodeURIComponent(VISITS) +
            '&rows_opportunity_id=' +
            encodeURIComponent(selCase.opportunity_id) +
            '&case_ids=' +
            encodeURIComponent(selCase.entity_id) +
            (ENT.key ? '&case_key=' + encodeURIComponent(ENT.key) : ''),
        ),
      )
        .then(function (j) {
          if (cancelled) return;
          var rows = (j.rows || [])
            .filter(function (v) {
              return v.visit_date;
            })
            .sort(function (a, b) {
              return String(a.visit_date).localeCompare(String(b.visit_date));
            });
          setVisits({ status: 'ready', rows: rows });
        })
        .catch(function (e) {
          if (!cancelled)
            setVisits({
              status: 'error',
              rows: [],
              error: String((e && e.message) || e),
            });
        });
      return function () {
        cancelled = true;
      };
    },
    [selCase && selCase.opportunity_id, selCase && selCase.entity_id],
  );
  // Images only where the visit rows carry them.
  var hasImages = visits.rows.some(function (v) {
    return v.images && v.images.length;
  });
  var sImages = React.useState({});
  var imagesByVisit = sImages[0],
    setImagesByVisit = sImages[1];
  React.useEffect(
    function () {
      if (!selCase || !hasImages) {
        setImagesByVisit({});
        return;
      }
      var ids = visits.rows
        .map(function (v) {
          return v.id;
        })
        .filter(Boolean)
        .slice(0, 100);
      if (!ids.length) return;
      var cancelled = false;
      getJson(
        withParams(
          '/labs/workflow/api/' + selCase.opportunity_id + '/visit-images/',
          'visit_ids=' + ids.join(','),
        ),
      )
        .then(function (j) {
          if (!cancelled) setImagesByVisit(j.visit_images || {});
        })
        .catch(function () {
          if (!cancelled) setImagesByVisit({});
        });
      return function () {
        cancelled = true;
      };
    },
    [visits, hasImages],
  );
  function photoUrls(v) {
    return (imagesByVisit[String(v.id)] || [])
      .filter(function (i) {
        return i && i.blob_id;
      })
      .map(function (i) {
        return (
          '/audit/image/' +
          selCase.opportunity_id +
          '/' +
          encodeURIComponent(i.blob_id) +
          '/'
        );
      });
  }

  // ══ Render ══════════════════════════════════════════════════════════════════
  if (report.status === 'loading')
    return (
      <div className="p-4">
        <R.Loading height={200}>Reading the report…</R.Loading>
      </div>
    );
  if (report.status === 'error')
    return (
      <div className="p-4">
        <R.Notice tone="error">
          The report could not be read: {report.error}
        </R.Notice>
      </div>
    );
  if (!flw)
    return (
      <div className="p-4 space-y-3">
        <R.Notice tone="muted">
          {'Pick a ' + WRK.name + ' from the report.'}
        </R.Notice>
        <R.Card>
          <div className="flex flex-wrap gap-2">
            {(P.byFLW || []).slice(0, 300).map(function (f) {
              return (
                <button
                  key={f.key}
                  type="button"
                  className="px-2 py-1 rounded border border-gray-200 text-xs hover:bg-indigo-50"
                  onClick={function () {
                    setSelKey(f.key);
                  }}
                >
                  {f.flw || f.username}
                </button>
              );
            })}
          </div>
        </R.Card>
      </div>
    );

  var reading = D.reading;
  var readingPoints = reading
    ? visits.rows
        .filter(function (v) {
          return (
            v[reading.column] !== null &&
            v[reading.column] !== undefined &&
            v[reading.column] !== ''
          );
        })
        .map(function (v) {
          return {
            date: String(v.visit_date).slice(0, 10),
            value: Number(v[reading.column]),
          };
        })
    : [];
  var tintBand = function (e) {
    return R.tintFor(e);
  };
  return (
    <div className="p-4 space-y-4 bg-gray-50">
      <R.ReportHeader
        crumbs={(D.title || 'Indicator report') + ' › ' + WRK.name + ' review'}
        title={flw.flw || flw.username}
        subtitle={
          <span>
            {'opportunity ' +
              flw.opp +
              (flw.llo ? ' · ' + flw.llo : '') +
              ' · '}
            <b>{R.nounCount(cases.length || flw.n, ENT)}</b>
            {' · figures as of ' + R.dateLbl(asOf)}
          </span>
        }
        badges={
          <span className="flex flex-wrap gap-2">
            <R.Pill tone="source">
              {report.source === 'stored' ? 'Saved report' : 'Live report'}
            </R.Pill>
            {flw.startMonth ? (
              <R.Pill tone="muted">{'Started ' + flw.startMonth}</R.Pill>
            ) : null}
            {flw.caseloadLabel ? (
              <R.Pill tone="muted">{'Caseload: ' + flw.caseloadLabel}</R.Pill>
            ) : null}
          </span>
        }
      />

      <R.Card padded={false}>
        <div className="px-4 pt-3 pb-2">
          <R.SectionTitle
            right={
              'against ' +
              WRK.plural +
              ' who started the same month (' +
              peers.start.length +
              ') and with a similar caseload (' +
              peers.caseload.length +
              ')'
            }
          >
            Indicators
          </R.SectionTitle>
        </div>
        <table className="min-w-full text-sm">
          <thead>
            <tr className="text-xs text-gray-500 border-b border-gray-100">
              <th className="px-4 py-2 text-left">Indicator</th>
              <th className="px-2 py-2 text-right">{R.cap(WRK.name)}</th>
              <th className="px-2 py-2 text-right">Same-month median</th>
              <th className="px-2 py-2 text-right">Similar-caseload median</th>
              <th className="px-2 py-2 text-right">Target</th>
            </tr>
          </thead>
          <tbody>
            {LAYOUT.columns.map(function (c) {
              var m = M_BY_ID[c.id] || {};
              var e = (flw.ind || {})[c.id];
              var t = R.targetValue(m, D.indicators[c.id]);
              var ms = median(peers.start, c.id);
              var mc = median(peers.caseload, c.id);
              return (
                <tr key={c.id} className="border-t border-gray-100">
                  <td className="px-4 py-2">
                    <div className="font-medium text-gray-900">{c.label}</div>
                    <div className="text-xs text-gray-400">{c.category}</div>
                  </td>
                  <td
                    className={
                      'px-2 py-2 text-right tabular-nums ' + tintBand(e)
                    }
                  >
                    <R.ScoreCellText
                      column={c}
                      entry={e}
                      measure={m}
                      minDenominator={MIN_DEN}
                    />
                  </td>
                  <td className="px-2 py-2 text-right tabular-nums text-gray-600">
                    {ms === null ? '—' : R.fmtValue(m, ms)}
                  </td>
                  <td className="px-2 py-2 text-right tabular-nums text-gray-600">
                    {mc === null ? '—' : R.fmtValue(m, mc)}
                  </td>
                  <td className="px-2 py-2 text-right text-xs text-gray-500">
                    {t === null ? '' : R.fmtValue(m, t)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <R.ScorecardLegend minDenominator={MIN_DEN} />
      </R.Card>

      <R.Card padded={false}>
        <div className="px-4 pt-3 pb-2">
          <R.SectionTitle right={'click a ' + ENT.name + ' for its visits'}>
            {R.cap(ENT.plural)}
          </R.SectionTitle>
        </div>
        {!cases.length ? (
          <div className="px-4 pb-4 text-sm text-gray-500">
            {'No ' + ENT.plural + ' in this report.'}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-xs">
              <thead>
                <tr className="text-gray-500 border-b border-gray-100">
                  <th className="px-3 py-1.5 text-left font-semibold">
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
                {cases.map(function (c, i) {
                  var on =
                    selCase &&
                    selCase.entity_id === c.entity_id &&
                    String(selCase.opportunity_id) === String(c.opportunity_id);
                  return (
                    <tr
                      key={i}
                      className={
                        'border-t border-gray-100 cursor-pointer ' +
                        (on ? 'bg-indigo-50' : 'hover:bg-gray-50')
                      }
                      onClick={function () {
                        setSelCase(on ? null : c);
                      }}
                    >
                      <td className="px-3 py-1.5 font-mono text-indigo-700">
                        {String(c.entity_id || '').slice(0, 12)}
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
          </div>
        )}
      </R.Card>

      {selCase ? (
        <R.Card>
          <R.SectionTitle
            right={
              visits.status === 'ready' ? visits.rows.length + ' visits' : ''
            }
          >
            {R.cap(ENT.name) +
              ' ' +
              String(selCase.entity_id).slice(0, 12) +
              ' · visits'}
          </R.SectionTitle>
          {visits.status === 'loading' || visits.status === 'idle' ? (
            <R.Loading height={120}>Reading the visits…</R.Loading>
          ) : visits.status === 'error' ? (
            <R.Notice tone="error">
              The visits could not be read: {visits.error}
            </R.Notice>
          ) : (
            <div className="space-y-3">
              {reading ? (
                <div>
                  <div className="text-xs font-semibold text-gray-600">
                    {reading.label +
                      (reading.unit ? ' (' + reading.unit + ')' : '')}
                  </div>
                  <R.ReadingChart
                    points={readingPoints}
                    label={reading.label}
                    unit={reading.unit}
                  />
                </div>
              ) : null}
              <table className="min-w-full text-xs">
                <thead>
                  <tr className="text-gray-500 border-b border-gray-100">
                    <th className="px-2 py-1.5 text-left">Date</th>
                    <th className="px-2 py-1.5 text-left">Status</th>
                    {reading ? (
                      <th className="px-2 py-1.5 text-right">
                        {reading.label}
                      </th>
                    ) : null}
                    <th className="px-2 py-1.5 text-left">Flag</th>
                    {hasImages ? (
                      <th className="px-2 py-1.5 text-left">Images</th>
                    ) : null}
                  </tr>
                </thead>
                <tbody>
                  {visits.rows.map(function (v, i) {
                    return (
                      <tr key={v.id || i} className="border-t border-gray-100">
                        <td className="px-2 py-1.5">
                          {R.dateLbl(v.visit_date)}
                        </td>
                        <td className="px-2 py-1.5 text-gray-600">
                          {v.status || '—'}
                        </td>
                        {reading ? (
                          <td className="px-2 py-1.5 text-right tabular-nums">
                            {v[reading.column] === null ||
                            v[reading.column] === undefined
                              ? '—'
                              : R.nCount(v[reading.column])}
                          </td>
                        ) : null}
                        <td className="px-2 py-1.5 text-gray-600">
                          {v.flagged ? 'flagged' : ''}
                        </td>
                        {hasImages ? (
                          <td className="px-2 py-1.5">
                            {photoUrls(v).map(function (u) {
                              return (
                                <a
                                  key={u}
                                  href={u}
                                  target="_blank"
                                  rel="noopener"
                                >
                                  <img
                                    src={u}
                                    className="inline-block h-10 w-10 object-cover rounded mr-1"
                                  />
                                </a>
                              );
                            })}
                          </td>
                        ) : null}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </R.Card>
      ) : null}
    </div>
  );
}
