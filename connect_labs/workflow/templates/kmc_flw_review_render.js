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

  var P = report.payload || {};
  var LLO_OF = (P.deployment && P.deployment.llo_map) || {};
  function oppLabel(o) {
    return LLO_OF[o] ? LLO_OF[o] + ' · opp ' + o : 'opp ' + o;
  }

  // The display contract travels with the payload, as on the programme page.
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
  var SC = (P.series && P.series.N) || null;
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
  // 15 g/kg/day target C13 is graded against, compounding from the first weight.
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
    { id: 'N01', label: 'Total', caseLabel: 'Counted', title: 'Total cases' },
    { id: 'N02', label: 'Reg', caseLabel: 'Reg', title: 'Registered (C01)' },
    {
      id: 'N03',
      label: 'Started',
      caseLabel: 'Started',
      title: 'Started (C02)',
    },
    {
      id: 'N05',
      label: 'Med GA',
      caseLabel: 'GA',
      title: 'Median gestational age, weeks',
    },
    {
      id: 'N06',
      label: 'Med BW',
      caseLabel: 'BW',
      title: 'Median birthweight, g',
    },
    {
      id: 'N07',
      label: 'Visits/case',
      caseLabel: 'Visits',
      title: 'Mean visits per case (C24)',
    },
    {
      id: 'N08',
      label: '%1st≤3d',
      caseLabel: '1st≤3d',
      title: '% first visit within 3 days of discharge (C16)',
    },
    {
      id: 'N09',
      label: 'Qual N',
      caseLabel: 'Qual',
      title:
        'Qualifying SVNs — the shared denominator of the four growth-quality columns',
      denOnly: true,
    },
    {
      id: 'N09',
      label: '%slow',
      caseLabel: 'Slow',
      title: '% slow growth, of qualifying SVNs',
    },
    {
      id: 'N10',
      label: '%healthy',
      caseLabel: 'Healthy',
      title: '% healthy growth, of qualifying SVNs',
    },
    {
      id: 'N11',
      label: '%fast',
      caseLabel: 'Fast',
      title: '% fast growth, of qualifying SVNs',
    },
    {
      id: 'N12',
      label: '%incompl',
      caseLabel: 'Incompl',
      title: '% incomplete growth data, of qualifying SVNs',
    },
    {
      id: 'N13',
      label: 'Mortality',
      caseLabel: 'Outcome',
      title: 'Mortality (C14) — shown only where death recording is credible',
    },
    {
      id: 'N14',
      label: 'Round%',
      caseLabel: 'Rounded',
      title: 'Weight rounding rate (C31)',
    },
    {
      id: 'N15',
      label: '%imposs',
      caseLabel: 'Implausible',
      title: '% impossible weight changes (C27)',
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
    if (c.id === 'N07') return v.toFixed(1);
    return nCount(v);
  }
  function ScorecardHead(props) {
    var lead = props.lead || [];
    var forCases = !!props.forCases;
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
            return (
              <th
                key={'l' + i}
                className={
                  (i === 0 ? 'px-3' : 'px-1.5') +
                  ' py-2 text-left font-semibold text-gray-600'
                }
              >
                {l}
              </th>
            );
          })}
          {SCORECARD.map(function (c, i) {
            return (
              <th
                key={i}
                className="px-1.5 py-2 text-right whitespace-nowrap font-semibold text-gray-600"
                title={c.title}
              >
                {forCases ? c.caseLabel : c.label}
                <div className="font-mono text-[10px] font-normal text-gray-300">
                  {c.id}
                </div>
              </th>
            );
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
    if (c.id === 'N01') return YES;
    if (c.id === 'N02' || c.id === 'N03' || c.id === 'N08')
      return has ? (pos ? YES : NO) : DASH;
    if (c.id === 'N05')
      return v === null || v === undefined
        ? DASH
        : String(Math.round(Number(v) * 10) / 10);
    if (c.id === 'N06') return v === null || v === undefined ? DASH : nCount(v);
    if (c.id === 'N07')
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
    if (c.id === 'N09' || c.id === 'N10' || c.id === 'N11' || c.id === 'N12')
      return has ? (pos ? DOT : DASH) : '';
    if (c.id === 'N13')
      return has ? (
        pos ? (
          <span className="text-red-700 font-semibold">died</span>
        ) : (
          <span className="text-gray-500">alive</span>
        )
      ) : (
        DASH
      );
    if (c.id === 'N14')
      return has
        ? Math.round((Number(v) / 100) * Number(den)) + '/' + den
        : DASH;
    if (c.id === 'N15')
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
    var built = weighingPoints(caseVisits, c);
    var pts = built.pts;
    var weighed = pts.filter(function (p) {
      return !p.birth;
    });
    var idx = cases.indexOf(c);
    var prev = idx > 0 ? cases[idx - 1] : null;
    var next = idx >= 0 && idx < cases.length - 1 ? cases[idx + 1] : null;
    // Early growth: the velocity across the weighings inside the first 42 days
    // after the first weighing -- the window C13 uses.
    var first = weighed[0];
    var earlyEnd = weighed.filter(function (p) {
      return first && p.x - first.x <= 42;
    });
    var earlyLast = earlyEnd[earlyEnd.length - 1];
    // Early growth is C13 at case scope when the registry has it (the baby is
    // past the growth gate); the descriptive window here is only a fallback.
    var scopeRow = caseScopeRow(c);
    var c13 =
      scopeRow && Number(scopeRow.c13_denominator) > 0
        ? Number(scopeRow.c13)
        : null;
    var earlyVel =
      c13 !== null
        ? c13
        : first &&
          earlyLast &&
          earlyLast !== first &&
          first.y > 0 &&
          earlyLast.x > first.x
        ? (earlyLast.y - first.y) / ((first.y / 1000) * (earlyLast.x - first.x))
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
      ['Discharged', dateOnly(c.hospital_discharge_date)],
      ['Registered', dateOnly(c.reg_date)],
      null,
      ['KMC status', c.last_kmc_status || '—'],
      [
        'Skin-to-skin',
        c.kmc_hours_mean ? Number(c.kmc_hours_mean).toFixed(1) + ' h/day' : '—',
      ],
      [
        'Danger signs',
        c.danger_visits === undefined
          ? '—'
          : c.danger_visits + (c.danger_visits === 1 ? ' visit' : ' visits'),
      ],
      ['Referrals', c.referral_visits === undefined ? '—' : c.referral_visits],
      [
        'Alive at last visit',
        c.alive_last === undefined || c.alive_last === null
          ? '—'
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
              {pipelinesLoaded ? (
                <GrowthChart
                  pts={pts}
                  gaWks={c.gestational_age_wks}
                  dob={built.dob}
                />
              ) : (
                <div className="text-xs text-gray-400 py-10 text-center">
                  Loading the weight series…
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
                    {pipelinesLoaded ? 'No weighings recorded.' : 'Loading…'}
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
                  {c13 !== null
                    ? 'C13 · target 15'
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
                  {span === null ? '' : 'over ' + span + ' days'}
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
                  readings ending in 00
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
                  losses after day 7 or &gt;50 g/kg/day
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
            to open it
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
              lead={['Case', 'Registered', 'Last visit']}
              forCases={true}
            />
            <tbody>
              {cases.map(function (c) {
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
                    {pipelinesLoaded
                      ? 'No cases for this worker in the report.'
                      : 'Loading cases…'}
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
