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
  var MIN_DEN = 25;

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

  var sourceRun = qp('source_run');
  var sKey = React.useState(qp('flw'));
  var selKey = sKey[0],
    setSelKey = sKey[1];
  var sCase = React.useState(null);
  var selCase = sCase[0],
    setSelCase = sCase[1];

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

  var P = report.payload || {};
  var LLO_OF = (P.deployment && P.deployment.llo_map) || {};
  function oppLabel(o) {
    return LLO_OF[o] ? LLO_OF[o] + ' · opp ' + o : 'opp ' + o;
  }

  // The display contract travels with the payload, as on the programme page.
  var C_LIST = React.useMemo(
    function () {
      return (P.cMeasures || [])
        .filter(function (m) {
          return m && m.indicator;
        })
        .map(function (m) {
          return {
            id: m.indicator,
            name: m.title,
            cat: m.category,
            prom: m.prominence,
            unit: m.unit,
            kind: m.kind,
            minDen: m.min_denominator,
          };
        });
    },
    [P],
  );
  var N_LIST = React.useMemo(
    function () {
      return (((P.series || {}).N || {}).measures || [])
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
      (((P.series || {}).N || {}).byFLW || []).forEach(function (f) {
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

  // Keep the address bar shareable: a picked worker is a link, not a click.
  function pickWorker(key) {
    setSelKey(key);
    setSelCase(null);
    try {
      var u = new URL(window.location.href);
      u.searchParams.set('flw', key);
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
  var childRows =
    (pipelines && pipelines.children && pipelines.children.rows) || [];
  var visitRows =
    (pipelines && pipelines.visits && pipelines.visits.rows) || [];
  var pipelinesLoaded = !!(pipelines && pipelines.children);
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
  function visitsFor(c) {
    if (!c) return [];
    return visitRows
      .filter(function (v) {
        return (
          String(v.opportunity_id) === String(c.opportunity_id) &&
          String(v.baby_case_id || v.entity_id) === String(c.entity_id) &&
          v.visit_date
        );
      })
      .sort(function (a, b) {
        return String(a.visit_date).localeCompare(String(b.visit_date));
      });
  }
  var caseVisits = React.useMemo(
    function () {
      return visitsFor(selCase);
    },
    [selCase, visitRows],
  );

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
  // Weight against age (days since birth where the date of birth is known, else
  // since the first weighing), every recorded weighing as a point, and a dashed
  // reference of 15 g/kg/day compounding from the first weight -- the early
  // growth target the C13 indicator is graded against.
  function GrowthChart(props) {
    var visits = props.visits.filter(function (v) {
      return (
        v.weight_g !== null &&
        v.weight_g !== undefined &&
        !isNaN(Number(v.weight_g))
      );
    });
    var origin = props.dob || (visits[0] && visits[0].visit_date);
    if (!visits.length || !origin)
      return (
        <div className="text-xs text-gray-400 py-10 text-center">
          No weights recorded for this case.
        </div>
      );
    var pts = visits
      .map(function (v) {
        return {
          x: daysBetween(origin, v.visit_date),
          y: Number(v.weight_g),
          date: dateOnly(v.visit_date),
        };
      })
      .filter(function (p) {
        return p.x !== null;
      });
    if (props.birthWeight && props.dob)
      pts.unshift({
        x: 0,
        y: Number(props.birthWeight),
        date: dateOnly(props.dob),
        birth: true,
      });
    if (!pts.length)
      return (
        <div className="text-xs text-gray-400 py-10 text-center">
          No dated weights for this case.
        </div>
      );
    var W = 720,
      H = 270,
      PL = 54,
      PR = 18,
      PT = 18,
      PB = 38;
    var xs = pts.map(function (p) {
      return p.x;
    });
    var ys = pts.map(function (p) {
      return p.y;
    });
    var xmin = Math.min(0, Math.min.apply(null, xs));
    var xmax = Math.max(28, Math.max.apply(null, xs));
    var w0 = pts[0].y,
      x0 = pts[0].x;
    var ref = [];
    var stepDays = Math.max(1, Math.ceil((xmax - x0) / 60));
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
      ) * 1.06;
    var ymin = Math.max(0, Math.min.apply(null, ys) * 0.9);
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
                x={PL - 6}
                y={Y(v) + 3}
                fontSize="9"
                fill="#94a3b8"
                textAnchor="end"
              >
                {(v / 1000).toFixed(2)} kg
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
              y={H - PB + 14}
              fontSize="9"
              fill="#94a3b8"
              textAnchor="middle"
            >
              {'day ' + v}
            </text>
          );
        })}
        <text x={W - PR} y={H - 4} fontSize="9" fill="#94a3b8" textAnchor="end">
          {props.dob ? 'days since birth' : 'days since first weighing'}
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
          x={X(ref[ref.length - 1].x) - 4}
          y={Y(ref[ref.length - 1].y) - 6}
          fontSize="9"
          fill="#94a3b8"
          textAnchor="end"
        >
          15 g/kg/day
        </text>
        <polyline
          fill="none"
          stroke="#4f46e5"
          strokeWidth="2.25"
          strokeLinejoin="round"
          points={pts
            .filter(function (p) {
              return !p.birth;
            })
            .map(function (p) {
              return X(p.x) + ',' + Y(p.y);
            })
            .join(' ')}
        />
        {pts.map(function (p, i) {
          return (
            <g key={i}>
              <circle
                cx={X(p.x)}
                cy={Y(p.y)}
                r={p.birth ? 4 : 3.5}
                fill={p.birth ? '#ffffff' : '#4f46e5'}
                stroke="#4f46e5"
                strokeWidth="2"
              >
                <title>
                  {(p.birth ? 'birth ' : '') + p.date + ' · ' + p.y + ' g'}
                </title>
              </circle>
            </g>
          );
        })}
      </svg>
    );
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
    var rows = byFLW.slice().sort(function (a, b) {
      return b.reds - a.reds || b.n - a.n;
    });
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
                <th className="px-3 py-2 text-left">Worker</th>
                <th className="px-3 py-2 text-left">Organisation</th>
                <th className="px-3 py-2 text-right">Cases</th>
                <th className="px-3 py-2 text-right">Red</th>
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
    var visits = caseVisits;
    var weighed = visits.filter(function (v) {
      return v.weight_g !== null && v.weight_g !== undefined;
    });
    var rows = weighed.map(function (v, i) {
      var prev = weighed[i - 1];
      var vel = null;
      if (prev) {
        var days = daysBetween(prev.visit_date, v.visit_date);
        if (days > 0 && Number(prev.weight_g) > 0)
          vel =
            (Number(v.weight_g) - Number(prev.weight_g)) /
            ((Number(prev.weight_g) / 1000) * days);
      }
      return {
        v: v,
        vel: vel,
        days: prev ? daysBetween(prev.visit_date, v.visit_date) : null,
      };
    });
    var facts = [
      ['Registered', dateOnly(c.reg_date)],
      ['Date of birth', dateOnly(c.dob)],
      ['Sex', c.gender || '—'],
      [
        'Gestational age',
        c.gestational_age_wks ? c.gestational_age_wks + ' wks' : '—',
      ],
      [
        'Birth weight',
        c.birth_weight_g ? nCount(c.birth_weight_g) + ' g' : '—',
      ],
      ['Hospital discharge', dateOnly(c.hospital_discharge_date)],
      ['Visits', c.total_visits || visits.length || '—'],
      ['Last visit', dateOnly(c.last_visit_date)],
      ['KMC status', c.last_kmc_status || '—'],
      [
        'Danger-sign visits',
        c.danger_visits === undefined ? '—' : c.danger_visits,
      ],
      ['Referrals', c.referral_visits === undefined ? '—' : c.referral_visits],
      [
        'Alive at last visit',
        c.alive_last === undefined || c.alive_last === null
          ? '—'
          : String(c.alive_last),
      ],
    ];
    return (
      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <div className="px-4 py-3 border-b border-gray-100 flex items-baseline justify-between gap-3 flex-wrap">
          <div>
            <div className="font-medium text-gray-900">
              Case {c.entity_id}
              <span className="ml-2 text-xs font-normal text-gray-400">
                {oppLabel(c.opportunity_id)}
              </span>
            </div>
            <div className="text-xs text-gray-500">
              {weighed.length} weighings
              {c.first_weight_g && c.last_weight_g
                ? ' · ' +
                  nCount(c.first_weight_g) +
                  ' g → ' +
                  nCount(c.last_weight_g) +
                  ' g'
                : ''}
            </div>
          </div>
          <button
            type="button"
            className="text-xs text-indigo-600 hover:underline"
            onClick={function () {
              setSelCase(null);
            }}
          >
            ← all cases
          </button>
        </div>
        <div className="p-4">
          <div className="text-sm font-medium text-gray-900 mb-1">Growth</div>
          {pipelinesLoaded ? (
            <GrowthChart
              visits={visits}
              dob={c.dob}
              birthWeight={c.birth_weight_g}
            />
          ) : (
            <div className="text-xs text-gray-400 py-10 text-center">
              Loading the weight series…
            </div>
          )}
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 px-4 pb-4">
          <div>
            <div className="text-sm font-medium text-gray-900 mb-1">
              Case record
            </div>
            <table className="min-w-full text-sm">
              <tbody>
                {facts.map(function (f) {
                  return (
                    <tr key={f[0]} className="border-t border-gray-100">
                      <td className="py-1.5 pr-3 text-gray-500">{f[0]}</td>
                      <td className="py-1.5 text-gray-900">{f[1]}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div>
            <div className="text-sm font-medium text-gray-900 mb-1">
              Weighings
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead className="bg-gray-50 text-gray-500">
                  <tr>
                    <th className="px-2 py-1.5 text-left">Date</th>
                    <th className="px-2 py-1.5 text-right">Weight</th>
                    <th className="px-2 py-1.5 text-right">Days</th>
                    <th
                      className="px-2 py-1.5 text-right"
                      title="g/kg/day since the previous weighing"
                    >
                      g/kg/day
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(function (r, i) {
                    var flag = r.vel !== null && (r.vel < 0 || r.vel > 50);
                    return (
                      <tr key={i} className="border-t border-gray-100">
                        <td className="px-2 py-1.5">
                          {dateOnly(r.v.visit_date)}
                        </td>
                        <td className="px-2 py-1.5 text-right">
                          {nCount(r.v.weight_g)} g
                        </td>
                        <td className="px-2 py-1.5 text-right text-gray-400">
                          {r.days === null ? '' : r.days}
                        </td>
                        <td
                          className={
                            'px-2 py-1.5 text-right ' +
                            (flag ? 'text-red-600 font-medium' : '')
                          }
                        >
                          {r.vel === null ? '' : r.vel.toFixed(1)}
                        </td>
                      </tr>
                    );
                  })}
                  {!rows.length && (
                    <tr>
                      <td
                        colSpan="4"
                        className="px-2 py-4 text-center text-xs text-gray-400"
                      >
                        {pipelinesLoaded
                          ? 'No weighings recorded.'
                          : 'Loading…'}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
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
      </div>
    );

  var agent = AGENT_BY_LLO[flw.llo];
  var unverified = UNVERIFIED_SCALE.indexOf(flw.llo) !== -1;

  return (
    <div className="space-y-4">
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

      {nFLW && N_LIST.length > 0 && (
        <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-gray-100 font-medium text-gray-900">
            Scorecard
            <span className="ml-2 text-xs font-normal text-gray-400">
              the 15 headline metrics, this worker
            </span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-px bg-gray-100">
            {N_LIST.map(function (m) {
              var e = entryOf(nFLW.ind, m.id);
              return (
                <div key={m.id} className="bg-white px-3 py-2">
                  <div className="text-[10px] text-gray-400">
                    {m.id} · {m.name}
                  </div>
                  <div className="text-base font-semibold text-gray-900">
                    {e.band === 'insufficient' ? (
                      <span className="text-gray-400 text-sm">
                        n&lt;{m.minDen || MIN_DEN}
                      </span>
                    ) : (
                      fmt(m, e)
                    )}
                  </div>
                  <div className="text-[10px] text-gray-400">n = {e.n}</div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-gray-100 font-medium text-gray-900">
            Indicators
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="bg-gray-50 text-gray-500">
                <tr>
                  <th className="px-3 py-2 text-left">ID</th>
                  <th className="px-3 py-2 text-left">Indicator</th>
                  <th className="px-3 py-2 text-right">Value</th>
                  <th className="px-3 py-2 text-right">n</th>
                  <th className="px-3 py-2 text-left">Band</th>
                </tr>
              </thead>
              <tbody>
                {C_LIST.map(function (i) {
                  var e = entryOf(flw.ind, i.id);
                  return (
                    <tr key={i.id} className="border-t border-gray-100">
                      <td className="px-3 py-2 font-mono text-xs text-gray-500">
                        {i.id}
                      </td>
                      <td className="px-3 py-2">
                        {i.name}
                        {i.prom === 'Top' && (
                          <span className="ml-2 text-xs text-indigo-500">
                            top
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2 text-right font-medium">
                        {fmt(i, e)}
                      </td>
                      <td className="px-3 py-2 text-right text-gray-400">
                        {e.n}
                      </td>
                      <td className="px-3 py-2">
                        <span
                          className={
                            'px-2 py-0.5 rounded text-xs ' +
                            (BAND_CLS[e.band] || BAND_CLS.nodata)
                          }
                        >
                          {bandLabel(e, i)}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        <div className="space-y-4">
          {selCase ? (
            <CaseDetail c={selCase} />
          ) : (
            <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
              <div className="px-4 py-3 border-b border-gray-100 font-medium text-gray-900">
                Cases
                <span className="ml-2 text-xs font-normal text-gray-400">
                  {cases.length} · most recent first · click a case for its
                  growth chart
                </span>
              </div>
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead className="bg-gray-50 text-gray-500">
                    <tr>
                      <th className="px-3 py-2 text-left">Case</th>
                      <th className="px-3 py-2 text-left">Registered</th>
                      <th className="px-3 py-2 text-right">Birth wt</th>
                      <th className="px-3 py-2 text-right">Last wt</th>
                      <th className="px-3 py-2 text-right">Visits</th>
                      <th className="px-3 py-2 text-left">Last visit</th>
                      <th className="px-3 py-2 text-left">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cases.map(function (c) {
                      return (
                        <tr
                          key={c.opportunity_id + '|' + c.entity_id}
                          className="border-t border-gray-100 cursor-pointer hover:bg-indigo-50"
                          onClick={function () {
                            setSelCase(c);
                          }}
                        >
                          <td className="px-3 py-2 font-mono text-xs text-gray-700">
                            {c.entity_id}
                          </td>
                          <td className="px-3 py-2">{dateOnly(c.reg_date)}</td>
                          <td className="px-3 py-2 text-right">
                            {c.birth_weight_g
                              ? nCount(c.birth_weight_g) + ' g'
                              : '—'}
                          </td>
                          <td className="px-3 py-2 text-right">
                            {c.last_weight_g
                              ? nCount(c.last_weight_g) + ' g'
                              : '—'}
                          </td>
                          <td className="px-3 py-2 text-right">
                            {c.total_visits || '—'}
                          </td>
                          <td className="px-3 py-2">
                            {dateOnly(c.last_visit_date)}
                          </td>
                          <td className="px-3 py-2 text-gray-600">
                            {c.last_kmc_status || '—'}
                          </td>
                        </tr>
                      );
                    })}
                    {!cases.length && (
                      <tr>
                        <td
                          colSpan="7"
                          className="px-3 py-6 text-center text-xs text-gray-400"
                        >
                          {pipelinesLoaded
                            ? 'No cases for this worker in the report.'
                            : 'Loading cases…'}
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
