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

  // ══ 2  Sorting the worker table ════════════════════════════════════════════
  var sSort = React.useState({ ind: null, dir: 'desc' });
  var sort = sSort[0],
    setSort = sSort[1];
  var sortInd = sort.ind || (MEASURES.length ? MEASURES[0].indicator : null);
  function clickSort(indicator) {
    setSort(function (prev) {
      var current =
        prev.ind || (MEASURES.length ? MEASURES[0].indicator : null);
      if (current === indicator)
        return { ind: indicator, dir: prev.dir === 'desc' ? 'asc' : 'desc' };
      return { ind: indicator, dir: 'desc' };
    });
  }
  var flwRows = React.useMemo(
    function () {
      var out = (sem.rows || [])
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
        });
      var dir = sort.dir === 'asc' ? 1 : -1;
      out.sort(function (a, b) {
        var av = sortInd ? a.cells[sortInd] && a.cells[sortInd].value : null;
        var bv = sortInd ? b.cells[sortInd] && b.cells[sortInd].value : null;
        // A worker with no value for the sort column sits at the bottom in BOTH
        // directions: "no data" is not a low score.
        var aNull = av === null || av === undefined;
        var bNull = bv === null || bv === undefined;
        if (aNull && bNull) return a.username < b.username ? -1 : 1;
        if (aNull) return 1;
        if (bNull) return -1;
        if (Number(av) === Number(bv)) return a.username < b.username ? -1 : 1;
        return Number(av) < Number(bv) ? dir : -dir;
      });
      return out;
    },
    [sem, sortInd, sort.dir],
  );

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
        <div className="mt-1 flex justify-between text-[11px] text-gray-500">
          <span>
            {values.length} anonymous peer{values.length === 1 ? '' : 's'}
            {ownNum === null ? '' : ' + this opportunity'}, low to high
          </span>
          {ownNum === null ? (
            <span className="text-gray-400">this opportunity: no value</span>
          ) : (
            <span className="text-indigo-700 font-semibold">
              this opportunity: {fmtValue(measure, ownNum)}
            </span>
          )}
        </div>
      </div>
    );
  }

  // ── This opportunity against its peers, over time ─────────────────────────
  // The x axis is TENURE, not the calendar: M0 is each opportunity's own first
  // month. Published that way because a cohort whose opportunities started
  // across seven months was otherwise comparing somebody's first month against
  // somebody else's sixth -- and because a line that starts late on a calendar
  // axis says when that opportunity began, which identifies it.
  // `R3` is the opportunity's own fourth report. (`M3` was an earlier,
  // cohort-month axis; still parsed so an older publication still charts.)
  function periodNumber(p) {
    var m = /^[RM](\d+)$/.exec(String(p || ''));
    return m ? Number(m[1]) : -1;
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
            if (
              i &&
              i !== periods.length - 1 &&
              i !== Math.floor((periods.length - 1) / 2)
            )
              return null;
            return (
              <text
                key={p}
                x={x(i)}
                y={H - 6}
                textAnchor="middle"
                fontSize="7"
                fill="#9ca3af"
              >
                {p}
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
                    <title>{periods[i] + ': ' + fmtValue(measure, v)}</title>
                  </circle>
                );
              })
            : null}
        </svg>
        <div className="mt-1 text-[11px] text-gray-500 flex justify-between gap-2">
          <span>
            {peerCount} anonymous peer{peerCount === 1 ? '' : 's'} · reports
            since each one joined
          </span>
          {hasOwn ? (
            <span className="text-indigo-700 font-semibold">
              this opportunity
            </span>
          ) : (
            <span className="text-gray-400">
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
      var shown = MEASURES.filter(function (m) {
        var e = byIndicator[m.indicator];
        return e && (e.peers || []).length;
      });
      // A published series is thinner than a published point: R5 keeps a period
      // only if enough peers reached it and R6 then keeps only peers present in
      // every period, so an indicator can have bars and no line.
      var trended = MEASURES.filter(function (m) {
        var e = byIndicator[m.indicator];
        return e && Object.keys(e.series || {}).length > 1;
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
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
              {shown.map(function (m) {
                var entry = byIndicator[m.indicator];
                var mine = oppCells[m.indicator];
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
                    <PeerBars
                      peers={entry.peers}
                      measure={m}
                      own={publishableValue(mine)}
                    />
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
          {trended.length ? (
            <div className="mt-5">
              <div className="text-xs font-semibold text-gray-700 mb-1">
                Over time
              </div>
              <div className="text-[11px] text-gray-500 mb-2">
                One line per opportunity, across its own first reports — so a
                cohort whose members joined at different times still lines up.
                Each point is one saved programme report, the same series the
                programme page charts. Only indicators whose series cleared the
                disclosure window appear here; the others are point-in-time
                above.
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
                {trended.map(function (m) {
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
                      <PeerTrend entry={byIndicator[m.indicator]} measure={m} />
                    </div>
                  );
                })}
              </div>
            </div>
          ) : null}
        </div>,
      );
    });
    return (
      <div>
        <div className="text-xs text-gray-500 mb-3">
          Peers are anonymous and re-sorted per indicator, so a bar cannot be
          followed from one chart to the next. This opportunity is the blue bar,
          placed by rank among them.
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

  function IndicatorHead(props) {
    var lead = props.lead || [];
    var sortable = !!props.sortable;
    return (
      <thead>
        <GroupHead lead={lead} />
        <tr className="text-xs text-gray-500 border-b border-gray-100">
          {lead.map(function (l, i) {
            return (
              <th
                key={'h' + i}
                className={
                  (i === 0 ? 'px-3 ' + STICK + ' ' : 'px-1.5 ') +
                  'py-2 text-left font-semibold text-gray-600'
                }
              >
                {l}
              </th>
            );
          })}
          {MEASURES.map(function (m) {
            var isSort = sortable && m.indicator === sortInd;
            return (
              <th
                key={m.indicator}
                title={m.title || m.indicator}
                onClick={
                  sortable
                    ? function () {
                        clickSort(m.indicator);
                      }
                    : undefined
                }
                className={
                  'px-1.5 py-2 text-right whitespace-nowrap font-semibold ' +
                  (sortable ? 'cursor-pointer hover:bg-gray-50 ' : '') +
                  (isSort
                    ? 'bg-indigo-100 text-indigo-900 border-b-2 border-indigo-600'
                    : 'text-gray-600')
                }
              >
                {m.title ? String(m.title).slice(0, 18) : m.indicator}
                {isSort ? (
                  <span className="ml-1">{sort.dir === 'asc' ? '▲' : '▼'}</span>
                ) : null}
                <div className="font-mono text-[10px] font-normal text-gray-300">
                  {m.indicator}
                </div>
              </th>
            );
          })}
          <th className="px-1.5 py-2 text-right whitespace-nowrap font-semibold text-gray-600">
            Attention
          </th>
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
              <IndicatorHead lead={['Scope', 'Cases']} />
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
            <ScorecardLegend right="Attention counts this row's off-target and watch indicators" />
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
              {sortInd || '—'}
            </span>{' '}
            {sort.dir === 'asc' ? 'ascending' : 'descending'} — click any
            indicator column to sort by it. Point-in-time: there is no
            per-worker trend in the data.
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
              <IndicatorHead lead={['Worker', 'Cases']} sortable={true} />
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
            <ScorecardLegend right="Click an indicator column to sort the workers by it" />
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
