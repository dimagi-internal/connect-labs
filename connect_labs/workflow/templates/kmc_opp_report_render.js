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
  // ══ ONE opportunity: its own figures, its own workers, its anonymous peers ══
  //
  // Three sections and each answers a different question, from a different
  // source, with a different disclosure posture:
  //
  //   1  Scorecard   what this opportunity's own numbers are
  //   2  Workers     which of ITS OWN field workers sit where on each indicator
  //   3  Benchmark   how it compares with peers it may not name
  //
  // Sections 1 and 2 both come off ONE semantic call (`scopes=opportunity,flw`,
  // one GROUPING SETS pass), because asking per scope re-runs the whole Layer 1
  // extraction each time.
  //
  // WHY SECTION 2 SHOWS REAL USERNAMES AND SECTION 3 DOES NOT. An opportunity
  // owns its own workers' data, so naming them to the people running it is
  // correct. The benchmark store exists to cross an opportunity boundary, and
  // FLW identity must never cross one — so the worker table is read straight
  // from the semantic layer and never goes anywhere near /labs/benchmarks/.
  //
  // WHAT IS DELIBERATELY ABSENT. There is no per-worker trend: the snapshot's
  // `monthlyByScope` carries `all`, `llo:<name>` and `opp:<id>` and nothing per
  // FLW, so a worker line would have to be invented. The worker table is
  // point-in-time and says so.
  var cfg = (definition && definition.config) || {};
  // The render's own fallback for a measure declaring no `min_denominator`,
  // matching the programme report's `var MIN_DEN = 25`.
  var MIN_DEN = Number(cfg.min_denominator_default) || 25;
  // Which indicators the workbook gates on CREDIBILITY — "the figure exists but
  // must not be published" (semantic/gates.py). Declared on the definition and
  // sourced from the programme report's own credibility map, so there is one
  // copy of the fact in the repo and it is patchable through
  // `workflow_update_definition` without a deploy.
  var CREDIBILITY_GATED = cfg.credibility_gated_indicators || [];
  function isGated(indicator) {
    return CREDIBILITY_GATED.indexOf(indicator) !== -1;
  }

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
  // WHICH opportunity this page is about. Section 3 is addressed by opportunity
  // rather than by workflow, so this is a separate resolution from the one
  // above and has its own fallbacks.
  function opportunityId() {
    var m = search.match(/[?&]opportunity_id=(\d+)/);
    if (m) return Number(m[1]);
    if (instance && instance.opportunity_id)
      return Number(instance.opportunity_id);
    var ids = (definition && definition.opportunity_ids) || [];
    return ids.length ? Number(ids[0]) : null;
  }

  // C is the workbook's headline family; N is the 15-metric scorecard from the
  // demo compute spec. Both are graded from the same rows, and the benchmark
  // payload is keyed by the same letter, so ONE switch moves all three sections.
  var sSeries = React.useState(qp('series') === 'C' ? 'C' : 'N');
  var series = sSeries[0],
    setSeries = sSeries[1];

  // ══ Grading ════════════════════════════════════════════════════════════════
  // A port of `semantic/snapshot.py`'s `grade()` — the same branch order, which
  // is load-bearing: availability gates outrank the denominator floor, which
  // outranks the bands. The thresholds themselves are NOT copied; they arrive on
  // every response in `measures` (the registry's own `meta`), which is what
  // stops this page from becoming a third hand-kept copy of the registry.
  //
  // Credibility IS ported, and it is the one branch that does not arrive as a
  // threshold: the server does not serve the settings tables to a browser (see
  // `_deployment_facts_for_render`), so this page cannot RECOMPUTE the rule. It
  // reads the registry's own verdict instead — the `<measure>_suppressed` column
  // the compiler puts on every row — and withholds the cell (`notcredible`), or
  // `unverifiable` where a gated indicator arrives with no flag at all. See the
  // CREDIBILITY block inside `gradeCell` below; do not delete it on the strength
  // of a comment that says this page "cannot decide" credibility.
  function bandOf(direction, bands, value) {
    if (value === null || value === undefined) return 'nodata';
    var x = Number(value);
    if (x !== x) return 'nodata';
    if (!bands || !bands.length) return 'unbanded';
    if (direction === 'higher')
      return x >= bands[0] ? 'green' : x >= bands[1] ? 'yellow' : 'red';
    if (direction === 'lower')
      return x <= bands[0] ? 'green' : x <= bands[1] ? 'yellow' : 'red';
    if (direction === 'mid2') {
      // Two-sided. A one-dimensional band here would read as 'unbanded', which
      // is the one outcome a two-sided mortality band exists to prevent.
      if (!bands[0] || bands[0].length !== 2) return 'unbanded';
      if (bands[0][0] <= x && x <= bands[0][1]) return 'green';
      if (
        bands[1] &&
        bands[1].length === 2 &&
        bands[1][0] <= x &&
        x <= bands[1][1]
      )
        return 'yellow';
      return 'red';
    }
    return 'unbanded';
  }
  function anyAsks(field, oppId, appAsks, asksAs) {
    // Unknown opportunity, or unknown field on a known opportunity, counts as
    // asking — the same fail-open choice the server's gate makes.
    var col = (asksAs || {})[field] || field;
    if (oppId === null || oppId === undefined) return true;
    var m = (appAsks || {})[String(oppId)];
    if (!m) return true;
    return m[col] === undefined || m[col] === null || !!m[col];
  }
  function inputState(measure, row, oppId, deployment) {
    var appAsks = (deployment || {}).app_asks || {};
    var asksAs = (deployment || {}).asks_as || {};
    var inputs = measure.inputs || [];
    for (var i = 0; i < inputs.length; i++) {
      if (!anyAsks(inputs[i], oppId, appAsks, asksAs)) return 'notinapp';
      var gate = row['anyrec_' + inputs[i]];
      if (gate !== undefined && gate !== null && Number(gate) === 0)
        return 'unrecorded';
    }
    return 'ok';
  }
  function gradeCell(measure, row, deployment) {
    var out = { id: measure.indicator, n: 0, value: null, band: 'nodata' };
    if (!row) return out;
    var den = row[measure.id + '_denominator'];
    out.n = den === null || den === undefined ? 0 : Math.round(Number(den));
    var oppId =
      row.opportunity_id === undefined || row.opportunity_id === null
        ? null
        : Number(row.opportunity_id);
    var state = inputState(measure, row, oppId, deployment);
    if (state !== 'ok') {
      out.band = state;
      return out;
    }
    var raw = row[measure.id];
    if (raw === null || raw === undefined) return out;
    var rawf = Number(raw);
    if (rawf !== rawf) return out;
    var minDen = measure.min_denominator || MIN_DEN;
    if (out.n && minDen && out.n < minDen) {
      out.band = 'insufficient';
      return out;
    }
    // ── CREDIBILITY ────────────────────────────────────────────────────────
    // `<measure>_suppressed` is the registry's OWN suppression rule, compiled
    // against the bound settings tables by semantic/compiler.py and returned on
    // every row. Reading it here is not a second copy of the gate: the
    // programme report reads a server-BUILT snapshot whose cells arrive already
    // graded, and this page grades LIVE endpoint rows, so the flag is the only
    // form that decision can reach it in. Ignoring it is this page's failure
    // mode, and it would put a figure the system calls untrustworthy in front
    // of a delivery partner.
    //
    // The column is `BOOL_OR(llo IS NULL OR llo NOT IN (credible))` over the
    // scope's own rows, which at `opportunity` and `flw` scope is EXACTLY the
    // per-LLO predicate — every case in an opportunity has one LLO. An
    // opportunity outside the llo_map reads as suppressed, so an unknown
    // deployment fails closed.
    //
    // Greyed with the value showing, never blank: the rule's own note says a
    // blank cell reads as "no data", which is a different and wrong fact.
    var suppressed = row[measure.id + '_suppressed'];
    if (suppressed === true) {
      out.band = 'notcredible';
      out.value = measure.unit === '%' ? rawf / 100 : rawf;
      return out;
    }
    // A gated indicator whose row carries NO flag at all. Only the C family
    // carries the registry's suppression rule, and `filter_to_series` drops it
    // with the rest of C — so in the N scorecard N13 (mortality, which is C14
    // under another name) arrives ungated. The page cannot establish
    // credibility, so it must not publish a banded number: withheld, and named
    // on the page rather than silently dropped.
    if (
      (suppressed === undefined || suppressed === null) &&
      isGated(measure.indicator)
    ) {
      out.band = 'unverifiable';
      return out;
    }
    out.band = bandOf(measure.direction, measure.bands, rawf);
    out.value = measure.unit === '%' ? rawf / 100 : rawf;
    // A rate computed over a self-selected minority of its scope: the
    // registry's own `min_input_coverage` footnote, marked rather than hidden.
    var floor = measure.min_input_coverage;
    var baseId = measure.coverage_denominator;
    if (floor && baseId) {
      var base = Number(row[baseId]);
      if (base && out.n / base < Number(floor)) out.thin = true;
    }
    return out;
  }
  function tintFor(cell) {
    if (!cell) return '';
    if (cell.band === 'green') return 'bg-green-50 text-green-800';
    if (cell.band === 'yellow') return 'bg-amber-50 text-amber-800';
    if (cell.band === 'red') return 'bg-red-50 text-red-800';
    if (cell.band === 'insufficient') return 'text-gray-400';
    if (cell.band === 'notinapp' || cell.band === 'unrecorded')
      return 'text-slate-400 italic';
    // A withheld figure is never a colour. Same styling as the programme
    // report's `notcredible`, so the two surfaces read alike.
    if (cell.band === 'notcredible' || cell.band === 'unverifiable')
      return 'text-slate-400 italic';
    return '';
  }
  function nCount(v) {
    var x = Number(v);
    if (x !== x) return '—';
    return Math.round(x).toLocaleString();
  }
  function fmtValue(measure, value) {
    if (value === null || value === undefined) return '—';
    var v = Number(value);
    if (v !== v) return '—';
    if (measure.unit === '%') return (100 * v).toFixed(1) + '%';
    if (measure.unit === '/100') return v.toFixed(1);
    if (measure.unit === 'g') return nCount(v);
    if (measure.unit === 'wks' || measure.unit === 'd' || measure.unit === 'h')
      return String(Math.round(v * 10) / 10);
    if (measure.kind === 'count') return nCount(v);
    return String(Math.round(v * 10) / 10);
  }
  function cellText(measure, cell) {
    if (!cell) return '—';
    if (cell.band === 'notinapp') return 'n/a';
    if (cell.band === 'unrecorded') return 'not recorded';
    if (cell.band === 'unverifiable') return 'n/a';
    if (cell.band === 'insufficient')
      return 'n<' + (measure.min_denominator || MIN_DEN);
    return fmtValue(measure, cell.value);
  }
  function cellTitle(measure, cell) {
    if (!cell) return '';
    if (cell.band === 'notcredible')
      return (
        'Recording for this indicator is not credible for this organisation ' +
        "(the workbook's Targets & settings). Shown greyed and never banded."
      );
    if (cell.band === 'unverifiable')
      return (
        'This indicator is gated on recording credibility and this series ' +
        'carries no credibility rule, so the figure is withheld rather than ' +
        'shown unverified.'
      );
    if (cell.thin) return 'thin denominator';
    return measure.title || '';
  }
  // What may leave this page as a COMPARISON. Mirrors the publisher's own
  // `PUBLISHABLE_BANDS` (benchmarks/publish.py): everything else is the
  // registry or its gates saying the figure must not stand on its own, and
  // marking it on a peer chart is exactly making it stand on its own.
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
  // Which indicators on screen are being withheld, so the gap is visible on the
  // page instead of looking like missing data.
  function withheldNote(cells) {
    var greyed = [];
    var withheld = [];
    MEASURES.forEach(function (m) {
      var cell = cells[m.indicator];
      if (!cell) return;
      if (cell.band === 'notcredible') greyed.push(m.indicator);
      if (cell.band === 'unverifiable') withheld.push(m.indicator);
    });
    if (!greyed.length && !withheld.length) return null;
    return (
      <div className="mt-2 text-xs text-slate-500">
        {greyed.length ? (
          <div>
            Greyed, never banded: {greyed.join(', ')} — recording for these is
            not credible for this organisation, so the figure is shown but must
            not be read as a score.
          </div>
        ) : null}
        {withheld.length ? (
          <div>
            Withheld: {withheld.join(', ')} — gated on recording credibility,
            which this series carries no rule for. Shown on the programme
            report, which grades it from the workbook's settings.
          </div>
        ) : null}
      </div>
    );
  }

  // ══ 1 + 2  The semantic read ═══════════════════════════════════════════════
  var sSem = React.useState({ status: 'loading' });
  var sem = sSem[0],
    setSem = sSem[1];
  var sSemTry = React.useState(0);
  var semTry = sSemTry[0],
    setSemTry = sSemTry[1];
  React.useEffect(
    function () {
      var cancelled = false;
      var id = definitionId();
      if (!id) {
        setSem({
          status: 'error',
          error:
            'could not resolve this workflow id, so the indicators cannot be read',
        });
        return;
      }
      setSem({ status: 'loading' });
      var sp = scopeParams();
      fetch(
        '/labs/workflow/api/' +
          id +
          '/semantic/' +
          sp +
          (sp ? '&' : '?') +
          'series=' +
          series +
          '&scopes=opportunity,flw',
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
                    ? 'the server took too long to compute these figures (HTTP ' +
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
            throw new Error(res.j.error || 'could not read the indicators');
          setSem({
            status: 'ready',
            rows: res.j.rows || [],
            measures: (res.j.measures || []).filter(function (m) {
              return m && m.indicator;
            }),
            deployment: res.j.deployment || {},
            cold: !!res.j.cold_cache,
            partial: !!res.j.partial_cache,
            hint: res.j.cold_cache_hint || null,
          });
        })
        // A failed read is an ERROR, never empty rows. The KMC worker page
        // mapped a non-OK response to `{ rows: [] }` and a 404 read as "this
        // worker has no weighings" for months.
        .catch(function (e) {
          if (!cancelled)
            setSem({ status: 'error', error: String((e && e.message) || e) });
        });
      return function () {
        cancelled = true;
      };
    },
    [series, semTry],
  );

  var MEASURES = sem.measures || [];
  var DEPLOY = sem.deployment || {};
  var oppRow = React.useMemo(
    function () {
      var rows = (sem.rows || []).filter(function (r) {
        return r.scope === 'opportunity';
      });
      var oid = opportunityId();
      if (oid !== null) {
        var mine = rows.filter(function (r) {
          return Number(r.opportunity_id) === oid;
        });
        if (mine.length) return mine[0];
      }
      return rows[0] || null;
    },
    [sem],
  );
  var oppCells = React.useMemo(
    function () {
      var out = {};
      MEASURES.forEach(function (m) {
        out[m.indicator] = gradeCell(m, oppRow, DEPLOY);
      });
      return out;
    },
    [sem, oppRow],
  );

  // ── The indicator grouping, from the registry rather than a fourth copy ────
  // The programme report's `SCORECARD_GROUPS` is a hand-written span list that
  // has to be re-counted every time a measure is added. Every measure already
  // carries `meta.category`, and the registry lists them in category order, so
  // the same banner row is derived from consecutive runs of it.
  var GROUPS = React.useMemo(
    function () {
      var out = [];
      MEASURES.forEach(function (m) {
        var label = m.category || '—';
        if (out.length && out[out.length - 1].label === label)
          out[out.length - 1].span += 1;
        else out.push({ label: label, span: 1 });
      });
      return out;
    },
    [sem],
  );

  // Replicated from the programme report's scorecard rather than shared: a
  // workflow render is a standalone JSX string, so templates cannot import
  // each other, and the shareable-components mechanism that would fix that
  // properly is a bigger design than this table justifies. Moving it to
  // `static/` was considered and rejected -- render code is live-editable via
  // MCP, and a static module would put every scorecard tweak behind a deploy.
  // So: a second copy, deliberately, and the two will drift until that
  // mechanism exists.
  function attentionCell(ind) {
    var reds = 0,
      yellows = 0;
    Object.keys(ind || {}).forEach(function (k) {
      var b = (ind[k] || {}).band;
      if (b === 'red') reds += 1;
      else if (b === 'yellow') yellows += 1;
    });
    return (
      <td className="px-1.5 py-2 text-right">
        {reds ? (
          <span
            className="inline-block px-1.5 py-0.5 rounded-md text-xs font-semibold bg-red-100 text-red-800"
            title={reds + ' off target \u00b7 ' + yellows + ' to watch'}
          >
            {reds}
          </span>
        ) : yellows ? (
          <span
            className="inline-block px-1.5 py-0.5 rounded-md text-xs font-semibold bg-amber-100 text-amber-800"
            title={yellows + ' to watch'}
          >
            {yellows}
          </span>
        ) : null}
      </td>
    );
  }

  function ScorecardLegend(props) {
    return (
      <div className="px-4 py-2 text-xs text-gray-400 border-t border-gray-100 flex items-center gap-4 flex-wrap">
        <span>
          <span className="inline-block w-2.5 h-2.5 rounded-sm bg-red-100 border border-red-400 mr-1 align-middle" />
          Off target
        </span>
        <span>
          <span className="inline-block w-2.5 h-2.5 rounded-sm bg-amber-100 border border-amber-400 mr-1 align-middle" />
          Watch
        </span>
        <span>n&lt;{MIN_DEN} = below the minimum denominator</span>
        <span className="ml-auto">{props.right}</span>
      </div>
    );
  }

  // The lead column stays put while the 20-odd indicator columns scroll under
  // it -- without this you lose which row you are reading two columns in.
  var STICK = 'sticky left-0 z-10 bg-white';

  function GroupHead(props) {
    var lead = props.lead || [];
    return (
      <tr className="text-[10px] uppercase tracking-wide text-gray-400 border-b border-gray-200">
        {lead.map(function (l, i) {
          return <th key={'g' + i} className="px-3 py-1"></th>;
        })}
        {GROUPS.map(function (g, i) {
          return (
            <th key={i} className="px-1.5 py-1 text-center" colSpan={g.span}>
              {g.label}
            </th>
          );
        })}
        <th className="px-1.5 py-1" />
      </tr>
    );
  }

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
    // The worker table opens on the first indicator, highest first.
    if (table === 'workers' && MEASURES.length)
      return { key: MEASURES[0].indicator, dir: 'desc' };
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
  // One indicator's definition: the authored sentence, the sentence rendered
  // from its SQL, what each property it reads means, then the SQL chain -- the
  // compiled measure, the per-baby properties under it -- each block copyable.
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
          (p.notes ? '-- ' + p.name + ': ' + p.notes + '\n' : '') +
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
        {en.plain ? <p className="text-gray-900">{en.plain}</p> : null}
        {en.definition ? (
          <p className="text-gray-600 text-xs mt-1">
            <span className="font-semibold text-gray-500">From the SQL: </span>
            {en.definition}
            {consts ? ' Constants: ' + consts + '.' : ''}
          </p>
        ) : null}
        {reads.length ? (
          <ul className="mt-2 text-xs text-gray-600 space-y-0.5">
            {reads.map(function (r) {
              return (
                <li key={r.name}>
                  <span className="font-mono text-gray-500">{r.name}</span>
                  {' — ' + r.means}
                </li>
              );
            })}
          </ul>
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

  // ══ 2  Sorting the worker table ════════════════════════════════════════════
  // Every column sorts, through the column-header helpers above. Rows are
  // graded once per payload; the order is applied on top, by username first so
  // ties read alphabetically.
  var gradedFlwRows = React.useMemo(
    function () {
      return (sem.rows || [])
        .filter(function (r) {
          return r.scope === 'flw' && r.username;
        })
        .map(function (r) {
          var cells = {};
          MEASURES.forEach(function (m) {
            cells[m.indicator] = gradeCell(m, r, DEPLOY);
          });
          return {
            username: String(r.username),
            opportunity_id: r.opportunity_id,
            cases: Number(r.n_cases) || 0,
            cells: cells,
          };
        })
        .sort(function (a, b) {
          return a.username < b.username ? -1 : a.username > b.username ? 1 : 0;
        });
    },
    [sem],
  );
  function attentionScore(cells) {
    var reds = 0,
      yellows = 0;
    Object.keys(cells || {}).forEach(function (k) {
      var b = (cells[k] || {}).band;
      if (b === 'red') reds += 1;
      else if (b === 'yellow') yellows += 1;
    });
    return reds * 1000 + yellows;
  }
  var flwRows = sortRows('workers', gradedFlwRows, function (w, key) {
    if (key === 'worker') return w.username.toLowerCase();
    if (key === 'cases') return w.cases;
    if (key === 'attn') return attentionScore(w.cells);
    var c = w.cells[key];
    return c && c.value !== null && c.value !== undefined
      ? Number(c.value)
      : null;
  });
  var workerSort = sortOf('workers') || { key: null, dir: 'desc' };
  var sortInd = workerSort.key;
  var SORT_KEY_LABEL = { worker: 'Worker', cases: 'Cases', attn: 'Attention' };

  // ══ 3  The benchmark ═══════════════════════════════════════════════════════
  // Anonymous peer values for the cohorts this opportunity belongs to. An EMPTY
  // payload is a normal state with two innocent causes — no cohort has been
  // published for this opportunity, or the disclosure rules withheld every
  // indicator — so it is explained in words and never routed to the error
  // branch. Only a failed REQUEST is an error.
  var benchmarkEmptyMessage =
    'No benchmark is published for this opportunity yet. Either it is not in a ' +
    'benchmark cohort, or the disclosure rules withheld every indicator — a ' +
    'cohort needs at least three peers with enough cases before any figure can ' +
    'be shown.';
  var sBench = React.useState({ status: 'loading' });
  var bench = sBench[0],
    setBench = sBench[1];
  var oppForBenchmark = opportunityId();
  React.useEffect(
    function () {
      var cancelled = false;
      if (oppForBenchmark === null) {
        setBench({
          status: 'error',
          error: 'this page could not resolve which opportunity it is about',
        });
        return;
      }
      setBench({ status: 'loading' });
      fetch('/labs/benchmarks/api/' + oppForBenchmark + '/', {
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
        // Same rule as above: a failed request is an error, NOT an empty
        // benchmark. The two look identical on the page otherwise, and one of
        // them is a lie.
        .catch(function (e) {
          if (!cancelled)
            setBench({ status: 'error', error: String((e && e.message) || e) });
        });
      return function () {
        cancelled = true;
      };
    },
    [oppForBenchmark],
  );

  function PeerBars(props) {
    var peers = props.peers || [];
    var measure = props.measure;
    var own = props.own;
    var values = [];
    peers.forEach(function (p) {
      var v = Number(p.value);
      if (v === v) values.push(v);
    });
    if (!values.length) return null;
    var ownNum =
      own === null || own === undefined || Number(own) !== Number(own)
        ? null
        : Number(own);
    var top = Math.max.apply(
      null,
      values.concat(ownNum === null ? [] : [ownNum]),
    );
    var base = Math.min.apply(
      null,
      values.concat(ownNum === null ? [] : [ownNum]),
    );
    if (base > 0) base = 0;
    var span = top - base || 1;
    function pct(v) {
      return Math.max(2, Math.round(((v - base) / span) * 100));
    }
    // This opportunity's own bar is INSERTED by rank, never matched against a
    // peer. Its figure is the LIVE one; the peers are the published ones, so
    // the two agree only until this opportunity's data moves after
    // publication -- and a value-match would then quietly colour some OTHER
    // opportunity's bar and call it this one. The store drops the reader's own
    // published row (benchmarks/data_access.py), so inserting here draws it
    // once, not twice.
    var bars = values.map(function (v) {
      return { v: v, mine: false };
    });
    if (ownNum !== null) bars.push({ v: ownNum, mine: true });
    bars.sort(function (a, b) {
      return a.v - b.v;
    });
    return (
      <div>
        <div className="h-24 flex items-end gap-1 border-b border-gray-200">
          {bars.map(function (b, i) {
            return (
              <div
                key={i}
                className={
                  'flex-1 rounded-t ' +
                  (b.mine ? 'bg-indigo-600' : 'bg-slate-300')
                }
                style={{ height: pct(b.v) + '%' }}
                title={
                  fmtValue(measure, b.v) +
                  (b.mine ? ' — this opportunity' : ' — an anonymous peer')
                }
              ></div>
            );
          })}
        </div>
        {/* Two columns at half-width: the left label must wrap INSIDE its own
            column and the reading must stay whole. Without `min-w-0` a flex
            child refuses to shrink below its content, so the label overran the
            value and the two printed on top of each other -- which is how a
            paired card looks at 530px, not at full width where it was drawn. */}
        <div className="mt-1 flex items-baseline justify-between gap-2 text-[11px] text-gray-500">
          <span className="min-w-0">
            {values.length} anonymous peer{values.length === 1 ? '' : 's'}
            {ownNum === null ? '' : ' + this opportunity'}, low to high
          </span>
          {ownNum === null ? (
            <span className="shrink-0 whitespace-nowrap text-gray-400">
              this opportunity: no value
            </span>
          ) : (
            <span className="shrink-0 whitespace-nowrap text-indigo-700 font-semibold">
              this opportunity: {fmtValue(measure, ownNum)}
            </span>
          )}
        </div>
      </div>
    );
  }

  // ── This opportunity against its peers, over time ─────────────────────────
  // The x axis is TENURE, not the calendar: `W7` is everybody's eighth week of
  // delivering, counted from each opportunity's own first week of activity. A
  // cohort whose members started sixteen months apart is otherwise comparing
  // somebody's first week against somebody else's seventieth -- and a line
  // placed on a calendar axis says when that opportunity began, which
  // identifies it.
  // Two older axes are still parsed so an existing publication keeps charting:
  // `R3` was the opportunity's own fourth REPORT (which silently made a
  // finished opportunity's repeated final figure look like its first weeks),
  // and `M3` was a cohort-month axis before that.
  function periodNumber(p) {
    var m = /^[WRM](\d+)$/.exec(String(p || ''));
    return m ? Number(m[1]) : -1;
  }

  // "W7" -> "week 8": the axis is read by humans, and W7 invites an off-by-one.
  function periodLabel(p) {
    var n = periodNumber(p);
    if (n < 0) return String(p || '');
    return /^W/.test(String(p)) ? 'week ' + (n + 1) : String(p);
  }

  function PeerTrend(props) {
    var entry = props.entry,
      measure = props.measure;
    var byPeriod = (entry && entry.series) || {};
    var ownByPeriod = (entry && entry.ownSeries) || {};
    // Numeric, because "M10" sorts before "M2" as a string.
    var periods = Object.keys(byPeriod).sort(function (a, b) {
      return periodNumber(a) - periodNumber(b);
    });
    if (periods.length < 2) return null;

    // One line per peer index. A peer_index denotes the SAME peer in every
    // period of a series -- that is what makes the points joinable at all --
    // but only within THIS indicator, so a line cannot be followed to the
    // chart beside it.
    var lines = {};
    periods.forEach(function (p, i) {
      (byPeriod[p] || []).forEach(function (pt) {
        (lines[pt.peer_index] = lines[pt.peer_index] || [])[i] = Number(
          pt.value,
        );
      });
    });
    var ownPts = periods.map(function (p) {
      var v = ownByPeriod[p];
      return v === null || v === undefined ? null : Number(v);
    });

    var all = [];
    Object.keys(lines).forEach(function (k) {
      lines[k].forEach(function (v) {
        if (v === v && v !== null) all.push(v);
      });
    });
    ownPts.forEach(function (v) {
      if (v !== null) all.push(v);
    });
    if (!all.length) return null;
    var lo = Math.min.apply(null, all),
      hi = Math.max.apply(null, all);
    if (hi === lo) hi = lo + 1;
    var W = 260,
      H = 120,
      L = 34,
      R = 8,
      T = 10,
      B = 20;
    var iw = W - L - R,
      ih = H - T - B;
    function x(i) {
      return (
        L + (periods.length > 1 ? (i * iw) / (periods.length - 1) : iw / 2)
      );
    }
    function y(v) {
      return T + ih - ((v - lo) / (hi - lo)) * ih;
    }
    function path(vals) {
      var d = '',
        pen = false;
      vals.forEach(function (v, i) {
        if (v === null || v === undefined || v !== v) {
          pen = false;
          return;
        }
        d += (pen ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1) + ' ';
        pen = true;
      });
      return d;
    }
    var hasOwn = ownPts.some(function (v) {
      return v !== null;
    });
    var peerCount = Object.keys(lines).length;
    return (
      <div>
        <svg
          viewBox={'0 0 ' + W + ' ' + H}
          className="w-full h-auto block"
          role="img"
          aria-label={(measure.title || measure.indicator) + ' over tenure'}
        >
          {[lo, (lo + hi) / 2, hi].map(function (t, i) {
            return (
              <g key={i}>
                <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke="#eeeef4" />
                <text
                  x={L - 4}
                  y={y(t) + 3}
                  textAnchor="end"
                  fontSize="7"
                  fill="#9ca3af"
                >
                  {fmtValue(measure, t)}
                </text>
              </g>
            );
          })}
          {periods.map(function (p, i) {
            var last = periods.length - 1;
            if (i && i !== last && i !== Math.floor(last / 2)) return null;
            // The end ticks are anchored INWARD. Centred on the first and last
            // point they hang half a label over each edge of the plot, and the
            // card clips it -- "week 42" was rendering as "week 4".
            return (
              <text
                key={p}
                x={x(i)}
                y={H - 6}
                textAnchor={i === 0 ? 'start' : i === last ? 'end' : 'middle'}
                fontSize="7"
                fill="#9ca3af"
              >
                {periodLabel(p)}
              </text>
            );
          })}
          {Object.keys(lines).map(function (k) {
            return (
              <path
                key={k}
                d={path(lines[k])}
                fill="none"
                stroke="#cbd5e1"
                strokeWidth="1.5"
              />
            );
          })}
          {hasOwn ? (
            <path
              d={path(ownPts)}
              fill="none"
              stroke="#4f46e5"
              strokeWidth="2"
            />
          ) : null}
          {hasOwn
            ? ownPts.map(function (v, i) {
                if (v === null) return null;
                return (
                  <circle
                    key={i}
                    cx={x(i)}
                    cy={y(v)}
                    r="2.5"
                    fill="#4f46e5"
                    stroke="#fff"
                    strokeWidth="1"
                  >
                    <title>
                      {periodLabel(periods[i]) + ': ' + fmtValue(measure, v)}
                    </title>
                  </circle>
                );
              })
            : null}
        </svg>
        <div className="mt-1 text-[11px] text-gray-500 flex items-baseline justify-between gap-2">
          <span className="min-w-0">
            {peerCount} anonymous peer{peerCount === 1 ? '' : 's'} · each one's
            own weeks of delivering
          </span>
          {hasOwn ? (
            <span className="shrink-0 whitespace-nowrap text-indigo-700 font-semibold">
              this opportunity
            </span>
          ) : (
            <span className="shrink-0 whitespace-nowrap text-gray-400">
              this opportunity: not in the window
            </span>
          )}
        </div>
      </div>
    );
  }

  function Benchmark() {
    if (bench.status === 'loading')
      return (
        <div className="text-sm text-gray-400">Loading the benchmark…</div>
      );
    if (bench.status === 'error')
      return (
        <div className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          The benchmark could not be read: {bench.error}
          <div className="text-xs text-red-600 mt-1">
            This is a failed request, not an empty benchmark — the two are
            different and are never shown the same way.
          </div>
        </div>
      );
    var payload = bench.payload || {};
    var cohorts = payload.cohorts || {};
    var ids = Object.keys(cohorts);
    if (!ids.length)
      return (
        <div className="text-sm text-gray-500">{benchmarkEmptyMessage}</div>
      );
    var blocks = [];
    ids.forEach(function (cid) {
      var meta = cohorts[cid] || {};
      var byIndicator = ((payload.indicators || {})[cid] || {})[series] || {};
      // ONE CARD PER INDICATOR, carrying both readings side by side: where this
      // opportunity sits today, and how it got there. They were two separate
      // grids, so answering "am I low, and have I always been?" meant scrolling
      // between two sections and matching titles by eye.
      var shown = MEASURES.filter(function (m) {
        var e = byIndicator[m.indicator];
        return (
          e &&
          ((e.peers || []).length || Object.keys(e.series || {}).length > 1)
        );
      });
      blocks.push(
        <div key={cid} className="mb-6">
          <div className="flex items-baseline justify-between mb-2">
            <div className="font-semibold text-gray-800">
              {meta.name || 'Cohort ' + cid}
            </div>
            <div className="text-xs text-gray-400">
              as of {meta.as_of || payload.as_of || 'unknown'}
            </div>
          </div>
          {shown.length ? (
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
              {shown.map(function (m) {
                var entry = byIndicator[m.indicator];
                var mine = oppCells[m.indicator];
                // A published series is thinner than a published point: R5
                // keeps a period only if enough peers reached it and R6 then
                // keeps only peers present in every period, so an indicator can
                // have bars and no line. Say which, rather than leaving a gap.
                var hasTrend = Object.keys(entry.series || {}).length > 1;
                return (
                  <div
                    key={m.indicator}
                    className="border border-gray-200 rounded p-3"
                  >
                    <div className="text-xs font-semibold text-gray-700">
                      {m.title || m.indicator}
                    </div>
                    <div className="font-mono text-[10px] text-gray-300 mb-2">
                      {m.indicator}
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      <div>
                        <div className="text-[10px] uppercase tracking-wide text-gray-400 mb-1">
                          Where it sits
                        </div>
                        {(entry.peers || []).length ? (
                          <PeerBars
                            peers={entry.peers}
                            measure={m}
                            own={publishableValue(mine)}
                          />
                        ) : (
                          <div className="text-[11px] text-gray-400">
                            No point value cleared the disclosure floors.
                          </div>
                        )}
                      </div>
                      <div>
                        <div className="text-[10px] uppercase tracking-wide text-gray-400 mb-1">
                          Over time
                        </div>
                        {hasTrend ? (
                          <PeerTrend entry={entry} measure={m} />
                        ) : (
                          <div className="text-[11px] text-gray-400">
                            Too few peers span enough reports to publish a trend
                            for this indicator.
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="text-sm text-gray-500">
              Nothing published for the {series} indicators in this cohort.{' '}
              {benchmarkEmptyMessage}
            </div>
          )}
        </div>,
      );
    });
    return (
      <div>
        <div className="text-xs text-gray-500 mb-3">
          Each indicator carries both readings: where this opportunity sits
          against its peers today, and how it got there. Peers are anonymous and
          re-sorted per indicator, so a bar cannot be followed from one chart to
          the next. This opportunity is the blue bar and the blue line; a trend
          runs on each opportunity's own weeks of delivering — week 1 is week 1
          for everybody — so a cohort whose members started months apart is
          compared like with like.
        </div>
        {blocks}
      </div>
    );
  }

  // ══ Shared chrome ══════════════════════════════════════════════════════════
  function SeriesSwitch() {
    return (
      <div className="inline-flex rounded border border-gray-300 overflow-hidden text-xs">
        {['N', 'C'].map(function (s) {
          return (
            <button
              key={s}
              type="button"
              onClick={function () {
                setSeries(s);
              }}
              className={
                'px-3 py-1 ' +
                (series === s
                  ? 'bg-indigo-600 text-white font-semibold'
                  : 'bg-white text-gray-600 hover:bg-gray-50')
              }
            >
              {s === 'N' ? 'Scorecard (N)' : 'Workbook (C)'}
            </button>
          );
        })}
      </div>
    );
  }

  function CacheNote() {
    if (sem.status !== 'ready') return null;
    if (!sem.cold && !sem.partial) return null;
    return (
      <div className="rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 mb-4">
        {sem.hint ||
          'These figures were computed from an incomplete visit cache.'}
      </div>
    );
  }

  function Problem(props) {
    return (
      <div className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800">
        {props.what} {props.error}
        {props.onRetry ? (
          <button
            type="button"
            className="ml-2 underline"
            onClick={props.onRetry}
          >
            try again
          </button>
        ) : null}
      </div>
    );
  }

  // `table`/`scope` make a header sortable and say which grouping its
  // definitions' SQL is compiled for. The one-row scorecard passes no table:
  // there is nothing to order.
  function IndicatorHead(props) {
    var lead = props.lead || [];
    var table = props.table || null;
    var scope = props.scope || 'opportunity';
    return (
      <thead>
        <GroupHead lead={lead} />
        <tr className="text-xs text-gray-500 border-b border-gray-100">
          {lead.map(function (l, i) {
            var col = typeof l === 'string' ? { label: l } : l;
            return headCell({
              key: 'h' + i,
              label: col.label,
              align: i === 0 ? 'left' : 'right',
              table: col.sortKey ? table : null,
              sortKey: col.sortKey,
              className:
                (i === 0
                  ? 'px-3 ' + STICK + ' text-left '
                  : 'px-1.5 text-right ') +
                'py-2 font-semibold text-gray-600 whitespace-nowrap',
            });
          })}
          {MEASURES.map(function (m) {
            return headCell({
              key: m.indicator,
              label: m.title ? String(m.title).slice(0, 18) : m.indicator,
              sub: m.indicator,
              title: m.title || m.indicator,
              def: m.indicator,
              scope: scope,
              table: table,
              sortKey: m.indicator,
              className:
                'px-1.5 py-2 text-right whitespace-nowrap font-semibold text-gray-600',
            });
          })}
          {headCell({
            key: 'attn',
            label: 'Attention',
            table: table,
            sortKey: 'attn',
            className:
              'px-1.5 py-2 text-right whitespace-nowrap font-semibold text-gray-600',
          })}
        </tr>
      </thead>
    );
  }

  // ══ Render ═════════════════════════════════════════════════════════════════
  var oppLabel =
    oppForBenchmark === null
      ? 'this opportunity'
      : 'opportunity ' + oppForBenchmark;

  return (
    <div className="p-4 space-y-8">
      {colDefModal()}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">
            {(definition && definition.name) || 'KMC Opportunity Report'}
          </h1>
          <div className="text-sm text-gray-500">
            {oppLabel} — its own figures, its own field workers, and its
            anonymous peers.
          </div>
        </div>
        <SeriesSwitch />
      </div>

      <CacheNote />

      {/* ── 1. This opportunity's scorecard ─────────────────────────────── */}
      <section>
        <h2 className="text-lg font-semibold text-gray-900 mb-2">
          This opportunity
        </h2>
        {sem.status === 'loading' ? (
          <div className="text-sm text-gray-400">Computing the indicators…</div>
        ) : sem.status === 'error' ? (
          <Problem
            what="The indicators could not be read:"
            error={sem.error}
            onRetry={function () {
              setSemTry(semTry + 1);
            }}
          />
        ) : !oppRow ? (
          <div className="text-sm text-gray-500">
            The indicators computed, but no opportunity-scope row came back for{' '}
            {oppLabel}.
          </div>
        ) : (
          <div className="overflow-x-auto border border-gray-200 rounded">
            <table className="min-w-full text-sm">
              <IndicatorHead lead={['Scope', 'Cases']} scope="opportunity" />
              <tbody>
                <tr className="border-t border-gray-100 bg-indigo-50 font-semibold">
                  <td
                    className={'px-3 py-2 text-left whitespace-nowrap ' + STICK}
                    style={{ background: 'rgb(238 242 255)' }}
                  >
                    {oppLabel}
                  </td>
                  <td className="px-1.5 py-2 text-right tabular-nums">
                    {nCount(oppRow.n_cases)}
                  </td>
                  {MEASURES.map(function (m) {
                    var cell = oppCells[m.indicator];
                    return (
                      <td
                        key={m.indicator}
                        className={
                          'px-1.5 py-2 text-right tabular-nums ' + tintFor(cell)
                        }
                        title={cellTitle(m, cell)}
                      >
                        {cellText(m, cell)}
                      </td>
                    );
                  })}
                  {attentionCell(oppCells)}
                </tr>
              </tbody>
            </table>
            <ScorecardLegend right="Attention counts this row's off-target and watch indicators · click a column name for its definition" />
          </div>
        )}
        {sem.status === 'ready' ? withheldNote(oppCells) : null}
      </section>

      {/* ── 2. Workers ──────────────────────────────────────────────────── */}
      <section>
        <div className="flex flex-wrap items-baseline justify-between gap-2 mb-2">
          <h2 className="text-lg font-semibold text-gray-900">
            Field workers ({flwRows.length})
          </h2>
          <div className="text-xs text-gray-500">
            Sorted by{' '}
            <span className="font-semibold text-indigo-700">
              {SORT_KEY_LABEL[sortInd] || sortInd || '—'}
            </span>{' '}
            {workerSort.dir === 'asc' ? 'ascending' : 'descending'} — the arrow
            beside a column sorts by it; its name opens the definition.
            Point-in-time: there is no per-worker trend in the data.
          </div>
        </div>
        {sem.status === 'loading' ? (
          <div className="text-sm text-gray-400">Computing the indicators…</div>
        ) : sem.status === 'error' ? (
          <Problem
            what="The worker figures could not be read:"
            error={sem.error}
            onRetry={function () {
              setSemTry(semTry + 1);
            }}
          />
        ) : !flwRows.length ? (
          <div className="text-sm text-gray-500">
            The indicators computed and returned no worker rows for {oppLabel}.
          </div>
        ) : (
          <div className="overflow-x-auto border border-gray-200 rounded">
            <table className="min-w-full text-sm">
              <IndicatorHead
                lead={[
                  { label: 'Worker', sortKey: 'worker' },
                  { label: 'Cases', sortKey: 'cases' },
                ]}
                table="workers"
                scope="flw"
              />
              <tbody>
                {flwRows.map(function (w) {
                  return (
                    <tr
                      key={w.opportunity_id + '::' + w.username}
                      className="border-t border-gray-100 hover:bg-gray-50"
                    >
                      <td
                        className={
                          'px-3 py-2 text-left whitespace-nowrap text-gray-900 ' +
                          STICK
                        }
                      >
                        {w.username}
                      </td>
                      <td className="px-1.5 py-2 text-right tabular-nums text-gray-600">
                        {nCount(w.cases)}
                      </td>
                      {MEASURES.map(function (m) {
                        var cell = w.cells[m.indicator];
                        return (
                          <td
                            key={m.indicator}
                            className={
                              'px-1.5 py-2 text-right tabular-nums ' +
                              (m.indicator === sortInd ? 'bg-indigo-50 ' : '') +
                              tintFor(cell)
                            }
                            title={cellTitle(m, cell)}
                          >
                            {cellText(m, cell)}
                          </td>
                        );
                      })}
                      {attentionCell(w.cells)}
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <ScorecardLegend right="Click a column name for its definition · the arrow sorts" />
          </div>
        )}
        {sem.status === 'ready' ? withheldNote(oppCells) : null}
      </section>

      {/* ── 3. Benchmark ────────────────────────────────────────────────── */}
      <section>
        <h2 className="text-lg font-semibold text-gray-900 mb-2">
          Against its peers
        </h2>
        <Benchmark />
      </section>
    </div>
  );
}
