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
  var MIN_DEN = Number(D.min_denominator) || 20;
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
  function peerN(list, id) {
    return list.filter(function (f) {
      var e = f.ind && f.ind[id];
      return R.drawable(e) && !isNaN(Number(e.value));
    }).length;
  }
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

  // Opening a case brings its visits panel into view (clear of the sticky
  // header), once on open and again when its visits have loaded.
  var panelRef = React.useRef(null);
  React.useEffect(
    function () {
      if (!selCase || !panelRef.current) return;
      var el = panelRef.current;
      if (typeof el.scrollIntoView !== 'function') return;
      var id = window.setTimeout(function () {
        try {
          el.scrollIntoView({ behavior: 'smooth', block: 'start' });
        } catch (e) {
          el.scrollIntoView(true);
        }
      }, 30);
      return function () {
        window.clearTimeout(id);
      };
    },
    [
      selCase && selCase.opportunity_id,
      selCase && selCase.entity_id,
      visits.status === 'ready',
    ],
  );

  // ══ Definitions, from the explain reader of the source report's registry ══
  var sDef = React.useState(null);
  var colDef = sDef[0],
    setColDef = sDef[1];
  var sDefCache = React.useState({});
  var defCache = sDefCache[0],
    setDefCache = sDefCache[1];
  var srcDefRef = React.useRef(null);
  function sourceDefId() {
    if (cfg.source_workflow_id) return Promise.resolve(cfg.source_workflow_id);
    if (srcDefRef.current) return Promise.resolve(srcDefRef.current);
    var rid = report.runId || sourceRun;
    if (!rid) return Promise.reject(new Error('no source report'));
    return getJson(withParams('/labs/workflow/api/run/' + rid + '/')).then(
      function (j) {
        var id = (j.run || {}).definition_id;
        if (!id) throw new Error('the source report is not known');
        srcDefRef.current = id;
        return id;
      },
    );
  }
  function explainUrl(defId, id, fmt, download) {
    return withParams(
      '/labs/workflow/api/' + defId + '/indicator-definitions/',
      'indicators=' +
        encodeURIComponent(id) +
        '&scope=flw&format=' +
        fmt +
        (download ? '&download=1' : ''),
    );
  }
  function openDef(id) {
    setColDef({ id: id });
    if (defCache[id] && defCache[id].status !== 'error') return;
    function put(v) {
      setDefCache(function (prev) {
        var next = Object.assign({}, prev);
        next[id] = v;
        return next;
      });
    }
    put({ status: 'loading' });
    sourceDefId()
      .then(function (defId) {
        return getJson(explainUrl(defId, id, 'json', false)).then(function (j) {
          var e = (j.indicators || [])[0];
          if (!e) throw new Error('no definition came back for ' + id);
          put({ status: 'ready', entry: e, defId: defId });
        });
      })
      .catch(function (err) {
        put({ status: 'error', error: String((err && err.message) || err) });
      });
  }
  function DefModal() {
    if (!colDef) return null;
    var m = M_BY_ID[colDef.id] || {};
    var st = defCache[colDef.id] || { status: 'loading' };
    return (
      <R.DefinitionModal
        id={colDef.id}
        title={m.title}
        state={st}
        entityPlural={ENT.plural}
        onClose={function () {
          setColDef(null);
        }}
        downloadUrl={
          st.defId ? explainUrl(st.defId, colDef.id, 'sql', true) : null
        }
        textUrl={st.defId ? explainUrl(st.defId, colDef.id, 'md', false) : null}
      />
    );
  }
  // An as-of date carries its year ("4 Oct 2026").
  function asOfLbl(d) {
    var y = String(d || '').slice(0, 4);
    return R.dateLbl(d) + (/^\d{4}$/.test(y) ? ' ' + y : '');
  }

  // The reading chart is drawn at its container's real pixel width.
  var sChartW = React.useState(0);
  var chartW = sChartW[0],
    setChartW = sChartW[1];
  var sTick = React.useState(0);
  React.useEffect(function () {
    function onResize() {
      sTick[1](function (n) {
        return n + 1;
      });
    }
    window.addEventListener('resize', onResize);
    return function () {
      window.removeEventListener('resize', onResize);
    };
  }, []);
  function chartRef(el) {
    if (!el) return;
    var w = Math.floor(el.clientWidth || 0);
    if (w && Math.abs(w - chartW) > 1) setChartW(w);
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
                  {f.name || f.flw || f.username}
                </button>
              );
            })}
          </div>
        </R.Card>
      </div>
    );

  // A case's human label (`display.entity.label_field`), else its id.
  function caseLabel(c, n) {
    if (R.caseLabel) return R.caseLabel(D, c, n);
    return String((c && c.entity_id) || '').slice(0, n);
  }
  var reading = D.reading;
  // The registry's per-visit columns and review flags (display.visit_fields /
  // display.visit_flags). A flag is how a flagged-visit indicator -- a repeat-count
  // rate, say -- points at WHICH visits; Connect's own review flag (`flagged`) is a
  // different thing and gets its own column only when some visit carries it.
  var visitFields = D.visit_fields || [];
  var visitFlags = D.visit_flags || [];
  function flagsOf(v) {
    return R.visitFlagsOf ? R.visitFlagsOf(D, v) : [];
  }
  // On a flagged row, the numeric cells the flag is about -- the registry's
  // `display.visit_flags[].fields` -- are marked where they equal the same
  // column on the previous visit (a repeat-count flag points at the counts).
  function repeatFieldsOf(v) {
    var hitLabels = flagsOf(v);
    var out = {};
    visitFlags.forEach(function (fl) {
      if (hitLabels.indexOf(fl.label) < 0) return;
      (fl.fields || []).forEach(function (n) {
        out[n] = true;
      });
    });
    return out;
  }
  function sameAsPrev(v, prev, field) {
    if (!prev) return false;
    var a = v[field],
      b = prev[field];
    if (a === null || a === undefined || a === '') return false;
    if (b === null || b === undefined || b === '') return false;
    return String(a) === String(b) || Number(a) === Number(b);
  }
  // The Flags header's tooltip: one line per flag present on this page, with
  // the registry's `description` for it when it gives one.
  var flagsOnPage = {};
  visits.rows.forEach(function (v) {
    flagsOf(v).forEach(function (l) {
      flagsOnPage[l] = true;
    });
  });
  function flagDefinition(fl) {
    return fl.description || '';
  }
  var flagsTitle = visitFlags
    .filter(function (fl) {
      return flagsOnPage[fl.label];
    })
    .map(function (fl) {
      var d = flagDefinition(fl);
      return fl.label + ': ' + (d || 'raised on this visit');
    })
    .join('\n');
  var anyReviewFlag = visits.rows.some(function (v) {
    return !!v.flagged;
  });
  var flaggedCount = visitFlags.length
    ? visits.rows.filter(function (v) {
        return flagsOf(v).length > 0;
      }).length
    : 0;
  // One status for every visit: said once in the card header, not per row.
  var uniformStatus =
    visits.rows.length > 1 &&
    visits.rows.every(function (v) {
      return v.status && String(v.status) === String(visits.rows[0].status);
    })
      ? String(visits.rows[0].status)
      : null;
  // A visit field with one value on every visit is said once in the card
  // header, like the status: a column of identical cells says nothing per row.
  function blank(x) {
    return x === null || x === undefined || x === '';
  }
  var constantFields =
    visits.rows.length > 1
      ? visitFields.filter(function (f) {
          var first = visits.rows[0][f.field];
          return (
            !blank(first) &&
            visits.rows.every(function (v) {
              return String(v[f.field]) === String(first);
            })
          );
        })
      : [];
  var shownFields = visitFields.filter(function (f) {
    return constantFields.indexOf(f) < 0;
  });
  function statusStyle(s, extra) {
    var st = {
      display: 'inline-block',
      padding: '0 6px',
      borderRadius: 9999,
      fontSize: 11,
      lineHeight: '16px',
      fontWeight: 400,
      background: /^approv/i.test(s)
        ? '#ecfdf5'
        : /^reject/i.test(s)
          ? '#fef2f2'
          : '#f3f4f6',
      color: /^approv/i.test(s)
        ? '#047857'
        : /^reject/i.test(s)
          ? '#b91c1c'
          : '#4b5563',
    };
    for (var k in extra || {}) st[k] = extra[k];
    return st;
  }
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
  // A category whose indicators all point the same way carries the direction
  // chip once, on its subheader; otherwise each directional row carries it.
  var catDirs = {};
  LAYOUT.columns.forEach(function (c) {
    var d = (M_BY_ID[c.id] || {}).direction || '';
    (catDirs[c.category] = catDirs[c.category] || {})[d] = true;
  });
  var catDir = {};
  Object.keys(catDirs).forEach(function (k) {
    var ds = Object.keys(catDirs[k]);
    if (ds.length === 1 && (ds[0] === 'higher' || ds[0] === 'lower'))
      catDir[k] = ds[0];
  });
  var dirChip = {
    display: 'inline-block',
    marginLeft: 8,
    padding: '0 8px',
    borderRadius: 9999,
    fontSize: 12,
    fontWeight: 400,
    background: '#e5e7eb',
    color: '#374151',
  };
  // The worker's own column keeps a light tint (where no band tints it), so
  // the eye finds it among the peer medians.
  var OWN_BG = '#f5f7ff';
  // Peer columns: ONE column when both cohorts are the same size and every
  // median agrees (the two columns would only repeat each other).
  var peerRows = {};
  LAYOUT.columns.forEach(function (c) {
    peerRows[c.id] = {
      ms: median(peers.start, c.id),
      mc: median(peers.caseload, c.id),
      ns: peerN(peers.start, c.id),
      nc: peerN(peers.caseload, c.id),
    };
  });
  var caseloadKeys = {};
  peers.caseload.forEach(function (f) {
    caseloadKeys[f.key] = true;
  });
  var samePeople =
    peers.start.length === peers.caseload.length &&
    peers.start.every(function (f) {
      return caseloadKeys[f.key];
    });
  var mergePeers =
    peers.start.length > 0 &&
    peers.start.length === peers.caseload.length &&
    LAYOUT.columns.every(function (c) {
      return peerRows[c.id].ms === peerRows[c.id].mc;
    });
  // Legend: only the states some cell on this page actually shows.
  var shownBands = {};
  LAYOUT.columns.forEach(function (c) {
    var e = (flw.ind || {})[c.id];
    if (e && e.band) shownBands[e.band] = true;
  });
  var anyLegend =
    shownBands.red || shownBands.yellow || shownBands.insufficient;
  // Each coloured state carries its rule, from the measures' own bands (the
  // thresholds the builder graded on), for the indicators shown in it.
  function bandNum(m, v) {
    var u = m && m.unit;
    return String(Number(v)) + (u === '%' ? '%' : u ? ' ' + u : '');
  }
  function bandRuleOf(c, band) {
    var m = M_BY_ID[c.id] || {};
    var b = m.bands;
    if (!b || b.length < 2 || b[0] === null || b[1] === null) return null;
    var hi = bandNum(m, b[0]),
      lo = bandNum(m, b[1]);
    var lower = m.direction === 'lower';
    if (band === 'yellow')
      return lower
        ? c.label + ' ' + hi + '–' + lo + ' (target ≤ ' + hi + ')'
        : c.label + ' ' + lo + '–' + hi + ' (target ≥ ' + hi + ')';
    return lower ? c.label + ' > ' + lo : c.label + ' < ' + lo;
  }
  function legendRule(band, word) {
    var rules = LAYOUT.columns
      .filter(function (c) {
        var e = (flw.ind || {})[c.id];
        return e && e.band === band;
      })
      .map(function (c) {
        return bandRuleOf(c, band);
      })
      .filter(Boolean);
    return rules.length ? word + ': ' + rules.join(' · ') : undefined;
  }
  // The reading chart, with the line BROKEN where consecutive visits are more
  // than GAP_DAYS apart (points still drawn), so a multi-week gap is not drawn
  // as if it had been observed. The threshold is the report's own staleness
  // rule (config.stale_after_days) when the review is given one.
  var GAP_DAYS = Number(cfg.stale_after_days) || 7;
  function dayMs(d) {
    return new Date(String(d).slice(0, 10) + 'T00:00:00Z').getTime();
  }
  function gapDays(a, b) {
    return Math.round((dayMs(b) - dayMs(a)) / 86400000);
  }
  // Gaps over the threshold between the case's visits, plus the silence since
  // its last visit when that is still open on the report's as-of date.
  var asOfDay = String(asOf || '').slice(0, 10);
  var lastVisitDay = visits.rows.length
    ? String(visits.rows[visits.rows.length - 1].visit_date).slice(0, 10)
    : '';
  var openGapDays =
    lastVisitDay && asOfDay > lastVisitDay ? gapDays(lastVisitDay, asOfDay) : 0;
  var openGap = openGapDays > GAP_DAYS;
  var gapCount =
    visits.rows.filter(function (v, i) {
      return (
        i > 0 && gapDays(visits.rows[i - 1].visit_date, v.visit_date) > GAP_DAYS
      );
    }).length + (openGap ? 1 : 0);
  // "Days since previous" is a column only when it varies.
  var sinceVals = visits.rows.slice(1).map(function (v, i) {
    return gapDays(visits.rows[i].visit_date, v.visit_date);
  });
  var showSince =
    sinceVals.length > 1 &&
    sinceVals.some(function (d) {
      return d !== sinceVals[0];
    });
  function gappedReadingChart(props) {
    var t = (props.points || []).filter(function (p) {
      return p && p.date && p.value !== null && !isNaN(Number(p.value));
    });
    if (t.length < 1)
      return (
        <div className="text-xs text-gray-500 py-6 text-center">
          {'No ' +
            (props.label ? props.label.toLowerCase() : 'reading') +
            ' recorded.'}
        </div>
      );
    var lastDay = t[t.length - 1].date;
    // The axis runs to the report's as-of date, so silence after the last
    // visit shows.
    var runsToAsOf = !!(asOfDay && asOfDay > lastDay);
    var x0 = dayMs(t[0].date),
      x1 = dayMs(runsToAsOf ? asOfDay : lastDay);
    var lo = Infinity,
      hi = -Infinity;
    t.forEach(function (p) {
      lo = Math.min(lo, Number(p.value));
      hi = Math.max(hi, Number(p.value));
    });
    // A non-negative series is drawn on a zero-based axis, so the line's
    // height reads as magnitude, not as the spread between visits.
    if (lo >= 0) {
      lo = 0;
      if (hi <= 0) hi = 1;
      // Gridlines at 0, s and 2s for the smallest round step s that clears
      // the highest reading.
      var half = hi / 2,
        mag = Math.pow(10, Math.floor(Math.log(half) / Math.LN10)),
        steps =
          mag >= 10
            ? [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]
            : [1, 2, 3, 4, 5, 6, 8, 10],
        s = steps[steps.length - 1] * mag;
      for (var si = 0; si < steps.length; si++)
        if (steps[si] * mag >= half) {
          s = steps[si] * mag;
          break;
        }
      hi = 2 * s;
    } else {
      if (hi === lo) {
        hi += 1;
        lo -= 1;
      }
      var pad = 0.1 * (hi - lo);
      lo -= pad;
      hi += pad;
    }
    var W = Math.max(chartW || 720, 320),
      L = 44,
      RX = W - 20;
    function X(d) {
      return x1 === x0
        ? (L + RX) / 2
        : L + ((dayMs(d) - x0) / (x1 - x0)) * (RX - L);
    }
    function Y(v) {
      return 146 - ((v - lo) / (hi - lo)) * 130;
    }
    // A date tick under every visit point. The ends and both bounds of every
    // gap are always labelled; any other date only where it does not collide
    // with a label already placed.
    var must = {};
    must[t[0].date] = true;
    must[lastDay] = true;
    t.forEach(function (p, i) {
      if (i && gapDays(t[i - 1].date, p.date) > GAP_DAYS) {
        must[t[i - 1].date] = true;
        must[p.date] = true;
      }
    });
    var placed = [];
    function clear(x) {
      return placed.every(function (px) {
        return Math.abs(px - x) >= 48;
      });
    }
    t.forEach(function (p) {
      if (must[p.date]) placed.push(X(p.date));
    });
    var ticks = t.map(function (p, i) {
      var x = X(p.date);
      var show = !!must[p.date];
      if (!show && clear(x)) {
        placed.push(x);
        show = true;
      }
      return { x: x, date: p.date, show: show };
    });
    // The as-of end is labelled when it clears the last visit's label.
    if (runsToAsOf)
      ticks.push({
        x: X(asOfDay),
        date: asOfDay,
        show: X(asOfDay) - X(lastDay) >= 96,
        asOf: true,
      });
    var path = t
      .map(function (p, i) {
        var brk =
          !i || (dayMs(p.date) - dayMs(t[i - 1].date)) / 86400000 > GAP_DAYS;
        return (
          (brk ? 'M' : 'L') +
          X(p.date).toFixed(1) +
          ' ' +
          Y(Number(p.value)).toFixed(1)
        );
      })
      .join(' ');
    return (
      <svg
        width={W}
        height={194}
        viewBox={'0 0 ' + W + ' 194'}
        className="block"
        role="img"
        aria-label={props.label || 'Readings'}
      >
        {props.label ? (
          <text x={0} y={11} fontSize="12" fontWeight="600" fill="#4b5563">
            {props.label + (props.unit ? ' (' + props.unit + ')' : '')}
          </text>
        ) : null}
        <defs>
          <pattern
            id="wr-open-hatch"
            patternUnits="userSpaceOnUse"
            width="7"
            height="7"
            patternTransform="rotate(45)"
          >
            <rect width="7" height="7" fill="#f3f4f6" />
            <line
              x1="0"
              y1="0"
              x2="0"
              y2="7"
              stroke="#d1d5db"
              strokeWidth="2"
            />
          </pattern>
        </defs>
        <g transform="translate(0,18)">
          {t.map(function (p, i) {
            if (
              !i ||
              (dayMs(p.date) - dayMs(t[i - 1].date)) / 86400000 <= GAP_DAYS
            )
              return null;
            // One band per gap, inset from both visits so adjacent gaps stay
            // separate bands and never cover a visit point.
            var gx0 = X(t[i - 1].date) + 6,
              gx1 = X(p.date) - 6,
              gw = Math.max(gx1 - gx0, 0),
              gd = Math.round(
                (dayMs(p.date) - dayMs(t[i - 1].date)) / 86400000,
              );
            return (
              <g key={'gap' + i}>
                <rect
                  data-gap={gd}
                  x={gx0}
                  y={Y(hi)}
                  width={gw}
                  height={Y(lo) - Y(hi)}
                  fill="#f3f4f6"
                />
                {gw >= 28 ? (
                  <text
                    x={gx0 + gw / 2}
                    y={Y(hi) + 14}
                    fontSize="12"
                    fill="#4b5563"
                    textAnchor="middle"
                    data-gap-days={gd}
                  >
                    {gw >= 52
                      ? gd + (Number(gd) === 1 ? ' day' : ' days')
                      : gd + ' d'}
                  </text>
                ) : null}
              </g>
            );
          })}
          {runsToAsOf && openGap ? (
            <g>
              <rect
                data-gap={openGapDays}
                data-gap-open="1"
                x={X(lastDay) + 6}
                y={Y(hi)}
                width={Math.max(X(asOfDay) - X(lastDay) - 6, 0)}
                height={Y(lo) - Y(hi)}
                fill="url(#wr-open-hatch)"
              >
                <title>
                  {openGapDays +
                    ' days without a visit since ' +
                    R.dateLbl(lastDay) +
                    ', still open on ' +
                    asOfLbl(asOfDay)}
                </title>
              </rect>
            </g>
          ) : null}
          {[lo, (lo + hi) / 2, hi].map(function (v) {
            return (
              <g key={v}>
                <line x1={L} x2={RX} y1={Y(v)} y2={Y(v)} stroke="#eeeef4" />
                <text
                  x={L - 6}
                  y={Y(v) + 4}
                  fontSize="11"
                  fill="#6b7280"
                  textAnchor="end"
                >
                  {R.nCount(v)}
                </text>
              </g>
            );
          })}
          <path d={path} fill="none" stroke="#4f46e5" strokeWidth="2" />
          {t.map(function (p, i) {
            return (
              <circle
                key={i}
                cx={X(p.date)}
                cy={Y(Number(p.value))}
                r="3"
                fill="#4f46e5"
                stroke="#fff"
                strokeWidth="1.5"
              >
                <title>
                  {R.dateLbl(p.date) +
                    ': ' +
                    R.nCount(p.value) +
                    (props.unit ? ' ' + props.unit : '')}
                </title>
              </circle>
            );
          })}
          {ticks.map(function (k, i) {
            return (
              <g key={'t' + i}>
                <line x1={k.x} x2={k.x} y1={150} y2={154} stroke="#d1d5db" />
                {k.show ? (
                  <text
                    x={k.x}
                    y={168}
                    fontSize="11"
                    fill="#6b7280"
                    textAnchor={
                      i === 0 && ticks.length > 1
                        ? 'start'
                        : i === ticks.length - 1 && ticks.length > 1
                          ? 'end'
                          : 'middle'
                    }
                  >
                    {k.asOf ? 'as of ' + asOfLbl(k.date) : R.dateLbl(k.date)}
                  </text>
                ) : null}
              </g>
            );
          })}
        </g>
      </svg>
    );
  }
  return (
    <div className="p-4 space-y-4 bg-gray-50">
      <R.ReportHeader
        crumbs={(D.title || 'Indicator report') + ' › ' + WRK.name + ' review'}
        title={flw.name || flw.flw || flw.username}
        subtitle={
          <span>
            {(flw.llo ? flw.llo : 'opportunity ' + flw.opp) + ' · '}
            <b>{R.nounCount(cases.length || flw.n, ENT)}</b>
            {' · figures as of ' + asOfLbl(asOf)}
          </span>
        }
        badges={
          <span className="flex flex-wrap gap-2">
            <R.Pill tone="source">
              {report.source === 'stored' ? 'Saved report' : 'Live report'}
            </R.Pill>
            {flw.startMonth ? (
              <R.Pill tone="muted">
                {'Started ' +
                  (R.monthLbl && /^\d{4}-\d{2}/.test(String(flw.startMonth))
                    ? R.monthLbl(String(flw.startMonth).slice(0, 7))
                    : flw.startMonth)}
              </R.Pill>
            ) : null}
            {flw.caseloadLabel &&
            peers.caseload.length < (P.byFLW || []).length ? (
              <span
                data-caseload-tip="1"
                style={{ cursor: 'help' }}
                title={(function () {
                  var ce = (P.cohortEdges && P.cohortEdges.caseload) || [];
                  var nw = (P.byFLW || []).length;
                  var t =
                    'Caseload = how many ' +
                    ((ENT && ENT.plural) || 'records') +
                    ' this ' +
                    ((WRK && WRK.name) || 'worker') +
                    ' holds (' +
                    flw.n +
                    '), ranked in thirds against all ' +
                    nw +
                    ' ' +
                    ((WRK && WRK.plural) || 'workers') +
                    ' in this report: lightest, middle, heaviest.';
                  if (ce.length === 2) {
                    t +=
                      ce[0] === ce[1]
                        ? ' Most hold the same number, so the bands collapse here: ' +
                          flw.caseloadLabel +
                          ' = ' +
                          ce[1] +
                          ' or more.'
                        : ' Cut-points: lightest < ' +
                          ce[0] +
                          ', heaviest ≥ ' +
                          ce[1] +
                          '.';
                  }
                  t +=
                    ' ' +
                    peers.caseload.length +
                    ' of ' +
                    nw +
                    ' share this band.';
                  return t;
                })()}
              >
                <R.Pill tone="muted">{'Caseload: ' + flw.caseloadLabel}</R.Pill>
              </span>
            ) : null}
          </span>
        }
      />

      <R.Card padded={false}>
        <div className="px-4 pt-3 pb-2">
          <R.SectionTitle
            right={
              mergePeers
                ? 'against ' + peers.start.length + ' peer ' + WRK.plural
                : 'against ' +
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
        <table
          className="min-w-full text-sm"
          style={{
            tableLayout: 'fixed',
            width: '100%',
            minWidth: 0,
            maxWidth: 880,
          }}
        >
          <thead>
            <tr className="text-xs text-gray-500 border-b border-gray-100">
              <th className="px-4 py-2 text-left">Indicator</th>
              <th
                className="px-2 py-2 text-right text-gray-900"
                style={{ width: 112, background: OWN_BG }}
              >
                {R.cap(WRK.name)}
              </th>
              {mergePeers ? (
                <th
                  className="px-2 py-2 text-right cursor-help"
                  style={{ width: 150, whiteSpace: 'nowrap' }}
                  title={
                    'Median of ' +
                    WRK.plural +
                    ' who started the same month (' +
                    peers.start.length +
                    ') and of those with a similar caseload (' +
                    peers.caseload.length +
                    ')' +
                    (samePeople
                      ? ' — the same ' + WRK.plural
                      : ' — equal on every indicator')
                  }
                >
                  {'Peer median (' + peers.start.length + ') '}
                  <span className="text-gray-500">ⓘ</span>
                </th>
              ) : (
                <th
                  className="px-2 py-2 text-right"
                  style={{ width: 120 }}
                  title={
                    WRK.plural +
                    ' who started the same month (' +
                    peers.start.length +
                    ')'
                  }
                >
                  {'Same-month median (' + peers.start.length + ')'}
                </th>
              )}
              {mergePeers ? null : (
                <th
                  className="px-2 py-2 text-right"
                  style={{ width: 120 }}
                  title={
                    WRK.plural +
                    ' with a similar caseload (' +
                    peers.caseload.length +
                    ')'
                  }
                >
                  {'Similar-caseload median (' + peers.caseload.length + ')'}
                </th>
              )}
              <th className="px-2 py-2 text-right" style={{ width: 96 }}>
                Target
              </th>
            </tr>
          </thead>
          <tbody>
            {LAYOUT.columns.map(function (c, ci) {
              var m = M_BY_ID[c.id] || {};
              var e = (flw.ind || {})[c.id];
              var t = R.targetValue(m, D.indicators[c.id]);
              var ms = peerRows[c.id].ms;
              var mc = peerRows[c.id].mc;
              // Rows sit under their category; the direction chip goes on the
              // category when the whole category shares it, else on the row.
              var groupDir = catDir[c.category] || null;
              var rowDir =
                !groupDir &&
                (m.direction === 'higher' || m.direction === 'lower')
                  ? m.direction
                  : null;
              var out = [];
              if (ci === 0 || LAYOUT.columns[ci - 1].category !== c.category)
                out.push(
                  <tr
                    key={'cat' + ci}
                    className="border-t border-gray-100 bg-gray-50"
                  >
                    <td
                      colSpan={mergePeers ? 4 : 5}
                      className="px-4 py-1.5 text-xs font-semibold text-gray-600"
                    >
                      {c.category}
                      {groupDir ? (
                        <span style={dirChip}>{groupDir + ' is better'}</span>
                      ) : null}
                    </td>
                  </tr>,
                );
              out.push(
                <tr
                  key={c.id}
                  className="border-t border-gray-100"
                  style={{ scrollMarginTop: 72 }}
                >
                  <td className="px-4 py-2" title={c.title || undefined}>
                    <div className="font-medium text-gray-900">
                      <button
                        type="button"
                        className="text-left font-medium text-gray-900 hover:text-indigo-700 underline decoration-dotted decoration-gray-300 underline-offset-2"
                        onClick={function () {
                          openDef(c.id);
                        }}
                      >
                        {c.label}
                      </button>
                      {c.title ? (
                        <span
                          className="ml-1 text-gray-500 cursor-help"
                          aria-label={'Definition: ' + c.title}
                        >
                          ⓘ
                        </span>
                      ) : null}
                      {rowDir ? (
                        <span style={dirChip}>{rowDir + ' is better'}</span>
                      ) : null}
                    </div>
                  </td>
                  <td
                    className={
                      'px-2 py-2 text-right tabular-nums font-semibold ' +
                      tintBand(e)
                    }
                    style={tintBand(e) ? undefined : { background: OWN_BG }}
                  >
                    {e &&
                    e.band !== 'insufficient' &&
                    (e.value === null || e.value === undefined) ? (
                      e.n !== null && e.n !== undefined && Number(e.n) === 0 ? (
                        <span
                          className="text-xs text-gray-500 cursor-help"
                          title={
                            "No records meet this indicator's denominator yet (n = 0)" +
                            (c.title ? '. ' + c.title : '')
                          }
                        >
                          none eligible
                        </span>
                      ) : (
                        <span
                          className="text-xs text-gray-500 cursor-help"
                          title={
                            'No value computed' +
                            (c.title ? '. ' + c.title : '')
                          }
                        >
                          no data
                        </span>
                      )
                    ) : (
                      <R.ScoreCellText
                        column={c}
                        entry={e}
                        measure={m}
                        minDenominator={MIN_DEN}
                      />
                    )}
                  </td>
                  <td className="px-2 py-2 text-right tabular-nums text-gray-600">
                    {ms === null ? '—' : R.fmtValue(m, ms)}
                    {ms !== null && peerRows[c.id].ns < peers.start.length ? (
                      <span
                        className="text-xs text-gray-500 cursor-help"
                        title={
                          'Median over the ' +
                          peerRows[c.id].ns +
                          ' of ' +
                          peers.start.length +
                          ' peer ' +
                          WRK.plural +
                          ' with a value for this indicator'
                        }
                      >
                        {' (n=' + peerRows[c.id].ns + ')'}
                      </span>
                    ) : null}
                  </td>
                  {mergePeers ? null : (
                    <td className="px-2 py-2 text-right tabular-nums text-gray-600">
                      {mc === null ? '—' : R.fmtValue(m, mc)}
                      {mc !== null &&
                      peerRows[c.id].nc < peers.caseload.length ? (
                        <span
                          className="text-xs text-gray-500 cursor-help"
                          title={
                            'Median over the ' +
                            peerRows[c.id].nc +
                            ' of ' +
                            peers.caseload.length +
                            ' peer ' +
                            WRK.plural +
                            ' with a value for this indicator'
                          }
                        >
                          {' (n=' + peerRows[c.id].nc + ')'}
                        </span>
                      ) : null}
                    </td>
                  )}
                  <td className="px-2 py-2 text-right tabular-nums text-gray-600">
                    {t === null ? (
                      <span className="text-xs text-gray-500">no target</span>
                    ) : (
                      R.fmtValue(m, t)
                    )}
                  </td>
                </tr>,
              );
              return out;
            })}
          </tbody>
        </table>
        {anyLegend ? (
          <div className="px-4 py-2 text-xs text-gray-500 border-t border-gray-100 flex items-center gap-4 flex-wrap">
            {shownBands.red ? (
              <span
                className="cursor-help"
                title={legendRule('red', 'Off target')}
              >
                <span className="inline-block w-2.5 h-2.5 rounded-sm bg-red-100 border border-red-400 mr-1 align-middle" />
                Off target
              </span>
            ) : null}
            {shownBands.yellow ? (
              <span
                className="cursor-help"
                title={legendRule('yellow', 'Watch')}
              >
                <span className="inline-block w-2.5 h-2.5 rounded-sm bg-amber-100 border border-amber-400 mr-1 align-middle" />
                Watch
              </span>
            ) : null}
            {shownBands.insufficient ? (
              <span>{'n<' + MIN_DEN + ' = too few records to compare'}</span>
            ) : null}
          </div>
        ) : null}
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
            <table className="min-w-full text-sm">
              <thead>
                <tr className="text-xs text-gray-500 border-b border-gray-100">
                  <th className="px-4 py-2 text-left font-semibold">
                    {R.cap(ENT.name)}
                  </th>
                  {D.case_fields.map(function (f) {
                    return (
                      <th
                        key={f.field}
                        className={
                          'py-2 text-right font-semibold ' +
                          (D.case_fields.indexOf(f) === D.case_fields.length - 1
                            ? 'px-4'
                            : 'px-2')
                        }
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
                      style={{ scrollMarginTop: 72 }}
                      onClick={function () {
                        setSelCase(on ? null : c);
                      }}
                    >
                      <td
                        className={
                          'px-4 py-2 text-indigo-700 ' +
                          (ENT.label_field && c[ENT.label_field]
                            ? ''
                            : 'font-mono')
                        }
                      >
                        {caseLabel(c, 12)}
                      </td>
                      {D.case_fields.map(function (f) {
                        return (
                          <td
                            key={f.field}
                            className={
                              'py-2 text-right tabular-nums ' +
                              (D.case_fields.indexOf(f) ===
                              D.case_fields.length - 1
                                ? 'px-4'
                                : 'px-2')
                            }
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
        <div
          ref={panelRef}
          style={{ scrollMarginTop: 96, minHeight: 'calc(100vh - 96px)' }}
        >
          <R.Card>
            <R.SectionTitle
              right={
                visits.status === 'ready' ? (
                  <span className="font-semibold text-gray-900">
                    {visits.rows.length +
                      ' visits' +
                      (visitFlags.length
                        ? ' · ' + flaggedCount + ' flagged'
                        : '') +
                      (gapCount
                        ? ' · ' +
                          gapCount +
                          (gapCount === 1 ? ' gap' : ' gaps') +
                          ' over ' +
                          GAP_DAYS +
                          ' days'
                        : '')}
                    {uniformStatus ? (
                      <span
                        style={statusStyle(uniformStatus, { marginLeft: 8 })}
                      >
                        {'status: all ' + uniformStatus}
                      </span>
                    ) : null}
                    {constantFields.map(function (f) {
                      return (
                        <span
                          key={f.field}
                          data-constant-field={f.field}
                          style={statusStyle('', { marginLeft: 8 })}
                        >
                          {f.label +
                            ': ' +
                            R.fmtCaseField(f, visits.rows[0][f.field]) +
                            ' at every visit'}
                        </span>
                      );
                    })}
                  </span>
                ) : (
                  ''
                )
              }
            >
              {R.cap(ENT.name) + ' ' + caseLabel(selCase, 12) + ' · visits'}
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
                    <div ref={chartRef} style={{ width: '100%' }}>
                      {gappedReadingChart({
                        points: readingPoints,
                        label: reading.label,
                        unit: reading.unit,
                      })}
                    </div>
                    {gapCount ? (
                      <div className="flex flex-wrap gap-4 text-xs text-gray-600">
                        {gapCount > (openGap ? 1 : 0) ? (
                          <span>
                            <span
                              className="inline-block mr-1 align-middle"
                              style={{
                                width: 12,
                                height: 12,
                                borderRadius: 2,
                                background: '#f3f4f6',
                                border: '1px solid #d1d5db',
                              }}
                            />
                            {'no visit for over ' + GAP_DAYS + ' days'}
                          </span>
                        ) : null}
                        {openGap ? (
                          <span>
                            <span
                              className="inline-block mr-1 align-middle"
                              style={{
                                width: 12,
                                height: 12,
                                borderRadius: 2,
                                background:
                                  'repeating-linear-gradient(45deg, #d1d5db 0 2px, #f3f4f6 2px 5px)',
                              }}
                            />
                            {'still open (no visit since)'}
                          </span>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                ) : null}
                <table className="min-w-full text-xs">
                  <thead>
                    <tr className="text-gray-500 border-b border-gray-100">
                      <th className="px-2 py-1.5 text-left">Date</th>
                      {showSince ? (
                        <th className="px-2 py-1.5 text-right">
                          Days since previous
                        </th>
                      ) : null}
                      {uniformStatus ? null : (
                        <th className="px-2 py-1.5 text-left">Status</th>
                      )}
                      {reading ? (
                        <th className="px-2 py-1.5 text-right">
                          {reading.label}
                        </th>
                      ) : null}
                      {shownFields.map(function (f) {
                        return (
                          <th
                            key={f.field}
                            className={
                              'px-2 py-1.5 ' +
                              (f.format === 'count' || f.format === 'number'
                                ? 'text-right'
                                : 'text-left')
                            }
                          >
                            {f.label}
                          </th>
                        );
                      })}
                      {visitFlags.length && flaggedCount ? (
                        <th
                          className="px-3 py-1.5 text-left cursor-help"
                          title={flagsTitle || undefined}
                        >
                          {'Flags '}
                          <span className="text-gray-500">ⓘ</span>
                        </th>
                      ) : null}
                      {anyReviewFlag ? (
                        <th className="px-2 py-1.5 text-left">
                          Sent to review
                        </th>
                      ) : null}
                      {hasImages ? (
                        <th className="px-2 py-1.5 text-left">Images</th>
                      ) : null}
                    </tr>
                  </thead>
                  <tbody>
                    {visits.rows.map(function (v, i) {
                      var hit = flagsOf(v);
                      var repF = hit.length ? repeatFieldsOf(v) : {};
                      var prevV = i > 0 ? visits.rows[i - 1] : null;
                      return (
                        <tr
                          key={v.id || i}
                          className="border-t border-gray-100"
                        >
                          <td
                            className="px-2 py-1.5"
                            style={
                              hit.length
                                ? { boxShadow: 'inset 3px 0 0 #f59e0b' }
                                : undefined
                            }
                          >
                            {R.dateLbl(v.visit_date)}
                          </td>
                          {showSince ? (
                            <td className="px-2 py-1.5 text-right tabular-nums text-gray-600">
                              {i === 0 ? (
                                '—'
                              ) : sinceVals[i - 1] > GAP_DAYS ? (
                                <span
                                  data-over-gap="1"
                                  className="font-semibold text-gray-900"
                                  style={{
                                    background: '#f3f4f6',
                                    border: '1px solid #d1d5db',
                                    padding: '0 6px',
                                    borderRadius: 2,
                                  }}
                                  title={'More than ' + GAP_DAYS + ' days'}
                                >
                                  {sinceVals[i - 1]}
                                </span>
                              ) : (
                                sinceVals[i - 1]
                              )}
                            </td>
                          ) : null}
                          {uniformStatus ? null : (
                            <td className="px-2 py-1.5">
                              {v.status ? (
                                <span style={statusStyle(v.status)}>
                                  {v.status}
                                </span>
                              ) : (
                                '—'
                              )}
                            </td>
                          )}
                          {reading ? (
                            <td className="px-2 py-1.5 text-right tabular-nums">
                              {v[reading.column] === null ||
                              v[reading.column] === undefined
                                ? '—'
                                : R.nCount(v[reading.column])}
                            </td>
                          ) : null}
                          {shownFields.map(function (f) {
                            var rep =
                              repF[f.field] &&
                              (f.format === 'count' || f.format === 'number') &&
                              sameAsPrev(v, prevV, f.field);
                            return (
                              <td
                                key={f.field}
                                className={
                                  'px-2 py-1.5 ' +
                                  (f.format === 'count' || f.format === 'number'
                                    ? 'text-right tabular-nums'
                                    : 'text-gray-600')
                                }
                                data-repeat={rep ? '1' : undefined}
                                title={
                                  rep ? 'Same as the previous visit' : undefined
                                }
                                style={
                                  rep
                                    ? {
                                        background: '#fffbeb',
                                        color: '#92400e',
                                      }
                                    : undefined
                                }
                              >
                                {R.fmtCaseField(f, v[f.field])}
                              </td>
                            );
                          })}
                          {visitFlags.length && flaggedCount ? (
                            <td className="px-3 py-1.5 font-medium text-amber-800">
                              {hit.map(function (h) {
                                return (
                                  <span
                                    key={h}
                                    data-flag-chip="1"
                                    style={{
                                      display: 'inline-block',
                                      border: '1px solid #f59e0b',
                                      borderRadius: 9999,
                                      padding: '0 8px',
                                      fontSize: 12,
                                      lineHeight: '18px',
                                      whiteSpace: 'nowrap',
                                      marginRight: 4,
                                      background: '#fffbeb',
                                    }}
                                  >
                                    {h}
                                  </span>
                                );
                              })}
                            </td>
                          ) : null}
                          {anyReviewFlag ? (
                            <td className="px-2 py-1.5 text-gray-600">
                              {v.flagged ? 'Yes' : ''}
                            </td>
                          ) : null}
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
        </div>
      ) : null}
      <DefModal />
    </div>
  );
}
