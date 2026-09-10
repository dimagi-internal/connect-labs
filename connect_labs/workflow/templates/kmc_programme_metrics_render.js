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
  // ══ One payload, one path ═══════════════════════════════════════════════════
  // Every number on this page comes from ONE server-built payload: the graded
  // output of the semantic-snapshot builder (workflow/snapshot_builders.py) over
  // the registry this workflow is bound to. A completed run reads it off the run
  // record; a live run fetches the SAME payload, built by the SAME code, as a
  // preview of what completing the run would store. This file is a view over
  // that one shape and computes no indicator, band, pool or trend itself.
  //
  // It used to. Grading (`cEntry`), pooling (`cPooled`), the monthly trend
  // (`monthlyFor`) and the per-worker rollup each existed twice -- once here in
  // JavaScript for a live run, once in Python for a saved one -- and every
  // saved-run defect this dashboard has had (a worker table with no names, a
  // mortality card that threw, a trend that drew NaN) was the two copies
  // disagreeing about a shape. There is one copy now.
  //
  // What this file still computes: per-case DISPLAY enrichment for the live case
  // drill (the weight-series triple and growth velocity for one baby's row),
  // which feeds no indicator and is absent, not approximated, on a saved run.
  //
  // ES5 dialect throughout -- no arrows, no destructuring, no computed keys --
  // because nothing outside a browser can execute this file.

  var cases =
    (pipelines && pipelines.children && pipelines.children.rows) || [];
  var wrows = (pipelines && pipelines.visits && pipelines.visits.rows) || [];

  // A completed run's payload, exactly as stored.
  var snapshot =
    view && view.isCompleted && view.state && view.state.snapshot
      ? view.state.snapshot
      : null;

  // A live run's payload, fetched: same shape, same builder, not persisted.
  var _live = React.useState({
    status: snapshot ? 'ready' : 'idle',
    payload: null,
    cache: null,
    error: null,
  });
  var live = _live[0];
  var setLive = _live[1];

  // THE payload. Null only while a live preview is in flight or has failed.
  var payload = snapshot || live.payload;
  var P = payload || {};

  // The (opportunity, worker) key separator. Declared up here, above every memo
  // that builds one: `var` hoists as undefined.
  var FLW_SEP = '::';

  // Deployment facts travel WITH the payload, so a saved run keeps explaining
  // itself after the registry moves on and a live run reads what the bound
  // registry currently says. Only `llo_map` is read here (labels); the gates
  // that read `app_asks` run server-side.
  var LLO_OF = (P.deployment && P.deployment.llo_map) || {};

  var OPP_LABEL = {
    10021: 'PIPN pilot (524)',
    10019: 'PIPN 874',
    10015: 'PIPN Apr-26 (1487)',
    10022: 'NAMA pilot (523)',
    10018: 'NAMA 938',
    10014: 'NAMA Apr-26 (1488)',
    10020: 'GHI 675',
    10017: 'GHI Mar-26 (1234)',
    10016: 'EHA Mar-26 (1236)',
    10013: 'Kikapu May-26 (1739)',
    10042: 'BERI May-26 (1790)',
  };
  function lloOf(o) {
    return LLO_OF[o] || 'opp ' + o;
  }
  function oppLabel(o) {
    return OPP_LABEL[o] || 'opp ' + o;
  }

  // Case count for a rollup row. The payload's rollups carry indicator results,
  // not case rows (byFLW carries positions into the case index), so reading
  // `rows.length` renders a confident 0 next to a Started column reading 606.
  // C01's denominator IS every case in the group; fall through to it.
  function caseCount(g) {
    if (g && g.rows && g.rows.length) return g.rows.length;
    if (g && g.ind && g.ind['C01'] && typeof g.ind['C01'].n === 'number')
      return g.ind['C01'].n;
    return '—';
  }

  var MIN_DEN = 25;

  // ── Fetch the live payload ────────────────────────────────────────────────
  // The scope the page is viewing, forwarded so the server resolves the same
  // run the page did. `owning_program_id` is deliberately NOT `program_id`:
  // that name is a labs-context param and putting it on a URL rewrites the
  // session's ambient scope for every later request from this page.
  function scopeParams() {
    var q = String(window.location.search || '');
    var out = [];
    var m = q.match(/[?&]opportunity_id=(\d+)/);
    if (m) out.push('opportunity_id=' + m[1]);
    else if (instance && instance.opportunity_id)
      out.push('opportunity_id=' + instance.opportunity_id);
    var pgm = q.match(/[?&]owning_program_id=(\d+)/);
    if (pgm) out.push('owning_program_id=' + pgm[1]);
    else if (instance && instance.program_id)
      out.push('owning_program_id=' + instance.program_id);
    return out.length ? '?' + out.join('&') : '';
  }

  function loadPreview() {
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
    setLive({ status: 'loading', payload: null, cache: null, error: null });
    fetch(
      '/labs/workflow/api/run/' + runId + '/snapshot/preview/' + scopeParams(),
    )
      .then(function (res) {
        return res.json();
      })
      .then(function (data) {
        if (data.error) {
          setLive({
            status: 'error',
            payload: null,
            cache: null,
            error: data.error,
          });
          return;
        }
        var snap = data.snapshot || {};
        var st = snap.state || {};
        setLive({
          status: 'ready',
          payload: st.snapshot || null,
          cache: data.cache || null,
          error: st.snapshot ? null : 'the preview carried no snapshot payload',
        });
      })
      .catch(function (err) {
        setLive({
          status: 'error',
          payload: null,
          cache: null,
          error: String((err && err.message) || err),
        });
      });
  }

  // The payload IS this dashboard's content, so it loads with the page. A
  // completed run already has it and fetches nothing.
  React.useEffect(
    function () {
      if (snapshot) return;
      loadPreview();
    },
    [Boolean(snapshot), instance && instance.id],
  );

  // ── The display contract, normalised into the field names the tables use ──
  // The payload's catalog calls things `indicator` / `title` / `category` /
  // `prominence`; the tables were written against id / name / cat / prom.
  // Renaming HERE, once, leaves every display site untouched.
  var C_LIST = React.useMemo(
    function () {
      return (P.cMeasures || [])
        .filter(function (m) {
          return m && m.indicator;
        })
        .map(function (m) {
          return {
            id: m.indicator,
            measure: m.id,
            name: m.title,
            cat: m.category,
            prom: m.prominence,
            unit: m.unit,
            kind: m.kind,
            dir: m.direction,
            bands: m.bands,
            minDen: m.min_denominator,
            inputs: m.inputs,
            minCoverage: m.min_input_coverage,
            coverageDen: m.coverage_denominator,
            tbdInput: m.tbd_input,
            scopeNote: m.scope_note,
          };
        });
    },
    [payload],
  );

  var C_BY_ID = React.useMemo(
    function () {
      var m = {};
      C_LIST.forEach(function (x) {
        m[x.id] = x;
      });
      return m;
    },
    [C_LIST],
  );

  // A lookup by id survives the registry gaining, losing or reordering a
  // measure; a position does not. The fallback keeps a label on screen while
  // the payload is in flight.
  function indOf(id) {
    return (
      C_BY_ID[id] || {
        id: id,
        measure: String(id).toLowerCase(),
        name: id,
        unit: '',
      }
    );
  }

  // ── Derive the weight series (the one thing SQL cannot express) ───────────
  var derived = React.useMemo(
    function () {
      var DAY = 86400000,
        ELIG = 28,
        LO = 21,
        HI = 35,
        WMIN = 250,
        WMAX = 8000,
        SWING = 0.3;
      // growth_class is defined as slow/plausible/fast "against the band-specific
      // range", built from early_g_per_kg_day AND birth_weight_g — i.e. the cut-offs
      // vary by birth-weight band. That band table is in neither the Case-indicators
      // tab nor Targets & settings, so it does not exist yet. These flat values are a
      // PLACEHOLDER so the C10/C11/C12 chain is exercisable; they are not the
      // workbook's definition and the UI labels them provisional.
      var PLAUSIBLE_LO = 10,
        PLAUSIBLE_HI = 20;

      function pd(s) {
        if (!s) return null;
        var d = new Date(s);
        return isNaN(d.getTime()) ? null : d;
      }

      // Weight series per (opp, baby) from the minimal visits pipeline.
      // Both pipelines key on the KMC beneficiary case (form.case.@case_id), NOT
      // on entity_id: Connect's entity_id is per-VISIT here, so grouping on it put
      // every visit in its own "entity" row and left every registration-form field
      // — birth weight, DOB, enrolment weight — attached to nothing
      // (connect-labs#1224). The entity query emits its group expression as
      // `entity_id`, so the case side is c.entity_id and the visit side is the
      // baby_case_id column the visit pipeline now carries.
      var series = {};
      wrows.forEach(function (r) {
        var rid = r.baby_case_id || r.entity_id;
        if (!rid) return;
        var w =
          typeof r.weight_g === 'number' ? r.weight_g : parseFloat(r.weight_g);
        if (!w || w < WMIN || w > WMAX) return;
        var day = String(r.visit_date || '').slice(0, 10);
        if (!day) return;
        var k = r.opportunity_id + '|' + rid;
        (series[k] = series[k] || {})[day] = (
          (series[k][day] || []).concat ? series[k][day] || [] : []
        ).concat([w]);
      });

      var now = new Date();
      return cases.map(function (c) {
        var k = c.opportunity_id + '|' + c.entity_id;
        var byDay = series[k] || {};
        var ws = Object.keys(byDay)
          .sort()
          .map(function (d) {
            var a = byDay[d];
            return {
              day: d,
              w:
                a.reduce(function (x, y) {
                  return x + y;
                }, 0) / a.length,
            };
          });

        var d = {
          opp: c.opportunity_id,
          llo: lloOf(c.opportunity_id),
          entity_id: c.entity_id,
          name: c.entity_name,
          flw: c.username,
          dob: c.dob,
          gender: c.gender,
          num_visits: c.total_visits || 0,
          reg_date: c.reg_date,
          first_visit: c.first_visit_date,
          visit_dates: c.visit_dates || [],
          last_visit: c.last_visit_date,
          birth_weight_g: c.birth_weight_g,
          enrollment_weight_g: c.enrollment_weight_g,
          weights: c.weights || [],
          n_weight_readings: c.n_weights || 0,
          days_discharge_to_reg: c.days_discharge_to_reg,
          kmc_hours_mean: c.kmc_hours_mean,
          last_kmc_status: c.last_kmc_status,
        };

        // Case properties (workbook Layer 2)
        // REGISTERED = the registration form exists for this baby.
        // STARTED     = at least one follow-up visit happened after registration.
        // Defining started as ">=1 visit" made C01/C02/C05 mathematically identical:
        // every case is in this table BECAUSE it has a visit, so 'started' was
        // always true and two of the three scale indicators carried no information.
        // Registration forms only began joining the case row once the entity key was
        // fixed, so this distinction is newly computable.
        var formNames = c.form_names || [];
        var isReg = function (n) {
          return /regist/i.test(String(n || ''));
        };
        d.n_reg_forms = formNames.filter(isReg).length;
        d.n_followups = formNames.filter(function (n) {
          return !isReg(n);
        }).length;
        // Apps whose export carries no form name at all fall back to the old rule
        // rather than reporting every baby as unregistered.
        d.registered = formNames.length
          ? d.n_reg_forms >= 1
          : d.num_visits >= 1;
        d.started = formNames.length ? d.n_followups >= 1 : d.num_visits >= 1;
        var fv = pd(d.first_visit);
        d.days_since_first_visit = fv ? Math.floor((now - fv) / DAY) : null;
        d.eligible = !!(d.started && fv && d.days_since_first_visit >= ELIG); // 28d from FIRST VISIT
        d.died = (c.death_visits || 0) > 0;
        // Case-properties tab, verbatim: outcome_known = "Died, or seen at least 28
        // days after the first visit". FALSE means lost to follow-up. Reading it as
        // "child_alive was recorded at some point" (as this did first) makes it true
        // for essentially every case and reports C15 loss-to-follow-up as ~0.
        var lv = pd(d.last_visit);
        d.days_first_to_last = fv && lv ? Math.round((lv - fv) / DAY) : null;
        d.outcome_known =
          d.died ||
          (d.days_first_to_last !== null && d.days_first_to_last >= ELIG);
        // early_exit = "died before its eligibility date". The death visit's own date
        // is not carried at entity stage, but a death is always recorded AT a visit,
        // so a died case whose LAST visit precedes day 28 must have died before
        // eligibility. Deaths in cases seen at/after day 28 are not counted here —
        // a deliberate under-count rather than a guess at the death date.
        d.early_exit = !!(
          d.died &&
          d.days_first_to_last !== null &&
          d.days_first_to_last < ELIG
        );

        // weight triple
        var span =
          ws.length >= 2
            ? (pd(ws[ws.length - 1].day) - pd(ws[0].day)) / DAY
            : 0;
        d.n_weights = ws.length;
        d.weight_computable = ws.length >= 2 && span >= 7;
        d.weight_consistent = d.weight_computable;
        for (var i = 1; i < ws.length; i++) {
          if (Math.abs(ws[i].w - ws[i - 1].w) > SWING * ws[i - 1].w) {
            d.weight_consistent = false;
            break;
          }
        }
        d.early_g_per_kg_day = null;
        if (d.weight_computable && fv) {
          var w0 = ws[0],
            w28 = null;
          ws.forEach(function (p) {
            var age = (pd(p.day) - fv) / DAY;
            if (age >= LO && age <= HI) w28 = p;
          });
          if (w28 && w28.day !== w0.day) {
            var dd = (pd(w28.day) - pd(w0.day)) / DAY;
            if (dd > 0)
              d.early_g_per_kg_day = (w28.w - w0.w) / (w0.w / 1000) / dd;
          }
        }
        d.weight_gain_data_sufficient =
          d.early_g_per_kg_day !== null && d.weight_consistent;
        d.growth_class = d.weight_gain_data_sufficient
          ? d.early_g_per_kg_day < PLAUSIBLE_LO
            ? 'slow'
            : d.early_g_per_kg_day > PLAUSIBLE_HI
            ? 'fast'
            : 'plausible'
          : null;
        d.first_weight_g = ws.length ? Math.round(ws[0].w) : null;
        d.last_weight_g = ws.length ? Math.round(ws[ws.length - 1].w) : null;

        // performance / data-quality inputs
        d.ever_danger_sign = (c.danger_visits || 0) > 0;
        d.referred = (c.referral_visits || 0) > 0;
        d.self_referral_count = c.self_referral_visits || 0;
        d.ebf_visits = c.ebf_visits || 0;
        d.enrolled_within_3d =
          typeof d.days_discharge_to_reg === 'number'
            ? d.days_discharge_to_reg <= 3
            : null;
        d.enrollment_is_birth_copy =
          c.birth_weight_g && c.enrollment_weight_g
            ? Math.abs(c.birth_weight_g - c.enrollment_weight_g) < 1
            : null;
        d.n_weights_round_100 = (c.weights || []).filter(function (w) {
          return w % 100 === 0;
        }).length;
        return d;
      });
    },
    // `payload` because this memo calls `lloOf`, which reads the payload's
    // llo_map. Without it every case row keeps the llo assigned before the
    // payload arrived -- "opp 10021" -- permanently, with no error.
    [cases, wrows, payload],
  );

  // ══ Drill-to-action ═══════════════════════════════════════════════════════
  // The drill ended here: a worker reading red, and nothing to do about it but
  // carry the name by hand into a separate workflow. This opens an audit on that
  // ONE worker, in place, with the scale reviewer their LLO's hardware needs.
  var cfgAudit = (definition && definition.config) || {};
  var AUDIT_ENABLED = cfgAudit.audit_enabled !== false;
  var AGENT_BY_LLO = cfgAudit.scale_agent_by_llo || {};
  var UNVERIFIED_SCALE = cfgAudit.scale_unverified_llos || [];
  var WEIGHT_IMAGE_PATH =
    cfgAudit.weight_image_path || 'anthropometric/upload_weight_image';
  var WEIGHT_VALUE_PATH =
    cfgAudit.weight_value_path || 'anthropometric/child_weight_visit';

  // The drill's second workflow. A worker row is a LINK to the KMC Worker
  // Review run named in config, carrying the worker key and THIS run, so that
  // page reads the very payload this one shows.
  var FLW_REVIEW = cfgAudit.flw_review || null;
  function flwReviewUrl(f) {
    if (!FLW_REVIEW || !FLW_REVIEW.workflow_id || !FLW_REVIEW.run_id || !f)
      return null;
    var sp = scopeParams();
    return (
      '/labs/workflow/' +
      FLW_REVIEW.workflow_id +
      '/run/?run_id=' +
      FLW_REVIEW.run_id +
      (sp ? '&' + sp.slice(1) : '') +
      '&flw=' +
      encodeURIComponent(f.key) +
      '&source_run=' +
      (instance && instance.id)
    );
  }

  var sAudit = React.useState({});
  var auditState = sAudit[0],
    setAuditState = sAudit[1];

  // Written in the same ES5 dialect as the rest of this file: no arrow
  // functions, no destructuring, no computed property keys. That is not a style
  // preference here -- the other 3,100 lines contain zero of all three.
  function setAuditFor(key, value) {
    setAuditState(function (prev) {
      var next = Object.assign({}, prev);
      next[key] = value;
      return next;
    });
  }

  // The audit window is the worker's OWN data range, not a fixed lookback: a
  // snapshot run is a snapshot of a past period, and a trailing-30-days window
  // would silently audit nothing on one.
  // The span a snapshot covers, from its own monthly series. Months are 'YYYY-MM'
  // keys, so the end is the last day of the last month rather than its first.
  function snapshotSpan() {
    var ms = (P.monthly || [])
      .map(function (m) {
        return m.month;
      })
      .filter(Boolean)
      .sort();
    if (!ms.length) return null;
    var last = String(ms[ms.length - 1]).slice(0, 7);
    var endDay = new Date(
      Date.UTC(Number(last.slice(0, 4)), Number(last.slice(5, 7)), 0),
    ).getUTCDate();
    return {
      start: String(ms[0]).slice(0, 7) + '-01',
      end: last + '-' + (endDay < 10 ? '0' : '') + endDay,
    };
  }

  function flwDateRange(f) {
    var ds = (f.rows || [])
      .map(function (r) {
        // The pipeline (and so both the live rows and a snapshot's case records)
        // emits *_visit_date. `first_visit`/`last_visit` never existed on either
        // shape, so this range silently resolved to nothing on every run.
        return (
          r.first_visit_date ||
          r.first_visit ||
          r.last_visit_date ||
          r.last_visit
        );
      })
      .filter(Boolean)
      .sort();
    if (ds.length)
      return {
        start: String(ds[0]).slice(0, 10),
        end: String(ds[ds.length - 1]).slice(0, 10),
      };
    // A FROZEN run keeps the indicator results but drops the per-case rows, so a
    // worker has no dated visits HERE -- which is not the same as having none.
    // Refusing the audit was the wrong answer: the worker, the opportunity and
    // the period are all still known, and the audit takes a date range, so fall
    // back to the span the snapshot itself covers. Without this the drill dead-
    // ends on exactly the run the demo opens with.
    return snapshotSpan();
  }

  function auditWorker(f) {
    var range = flwDateRange(f);
    if (!range) {
      setAuditFor(f.key, {
        status: 'error',
        message: 'No dated visits for this worker to audit.',
      });
      return;
    }
    var agent = AGENT_BY_LLO[f.llo];
    setAuditFor(f.key, { status: 'running' });
    actions
      .createAudit({
        opportunities: [{ id: f.opp, name: oppLabel(f.opp) }],
        criteria: {
          audit_type: 'date_range',
          granularity: 'per_flw',
          title: 'KMC review — ' + f.flw + ' (' + f.llo + ')',
          start_date: range.start,
          end_date: range.end,
          count_per_flw: cfgAudit.audit_count_per_flw || 25,
          // Scoped to the weight photo and the value entered beside it, which is
          // what the scale reviewers compare. Harmless when the opp carries no
          // photos: the audit is then a plain per-worker visit review.
          related_fields: [
            {
              image_path: WEIGHT_IMAGE_PATH,
              field_path: WEIGHT_VALUE_PATH,
              label: 'Weight entered',
              filter_by_image: false,
              filter_by_field: false,
            },
          ],
          selected_flw_user_ids: [f.flw],
        },
        workflow_run_id: instance && instance.id,
        ai_agent_id: agent || undefined,
      })
      .then(function (result) {
        if (!result || !result.success) {
          throw new Error((result && result.error) || 'audit creation failed');
        }
        setAuditFor(f.key, {
          status: 'created',
          taskId: result.task_id,
          agent: agent,
        });
      })
      .catch(function (err) {
        setAuditFor(f.key, {
          status: 'error',
          message: String((err && err.message) || err),
        });
      });
  }

  // Counts in a published report carry thousands separators; 37853 reads as a
  // typo next to 37,853.
  function nCount(value) {
    if (value === null || value === undefined) return 'n/a';
    var num = Number(value);
    if (isNaN(num)) return String(value);
    return Math.round(num).toLocaleString('en-US');
  }

  // ── Roll-ups, straight off the payload ─────────────────────────────────────
  var byOpp = P.byOpp || [];
  var byLLO = P.byLLO || [];
  var programInd = P.programInd || {};

  // The headline for a credibility-gated indicator: the figure pooled over the
  // recorders the workbook accepts, which LLOs those were, and how many there
  // are in total. Built server-side (it cannot be rebuilt from graded cells: a
  // row banded `insufficient` still contributes to the pool while storing no
  // value), keyed by indicator, in the {ind, llos, of} shape the card reads.
  var mortalityCredible = (P.pooledOverCredible &&
    P.pooledOverCredible['C14']) || {
    ind: null,
    llos: [],
    of: 0,
  };

  // ── Neal's scorecard ───────────────────────────────────────────────────────
  // His compute spec's §5 table, column for column, from the N series the builder
  // grades alongside the headline C series. `Qual N` is the shared denominator of
  // the four growth-quality columns, which the spec prints as its own column.
  var SC = (P.series && P.series.N) || null;
  var SCORECARD = [
    { id: 'N01', label: 'Total', title: 'Total cases' },
    { id: 'N02', label: 'Reg', title: 'Registered (C01)' },
    { id: 'N03', label: 'Started', title: 'Started (C02)' },
    { id: 'N05', label: 'Med GA', title: 'Median gestational age, weeks' },
    { id: 'N06', label: 'Med BW', title: 'Median birthweight, g' },
    { id: 'N07', label: 'Visits/case', title: 'Mean visits per case (C24)' },
    {
      id: 'N08',
      label: '%1st\u22643d',
      title: '% first visit within 3 days of discharge (C16)',
    },
    {
      id: 'N09',
      label: 'Qual N',
      title:
        'Qualifying SVNs \u2014 the shared denominator of the four growth-quality columns',
      denOnly: true,
    },
    { id: 'N09', label: '%slow', title: '% slow growth, of qualifying SVNs' },
    {
      id: 'N10',
      label: '%healthy',
      title: '% healthy growth, of qualifying SVNs',
    },
    { id: 'N11', label: '%fast', title: '% fast growth, of qualifying SVNs' },
    {
      id: 'N12',
      label: '%incompl',
      title: '% incomplete growth data, of qualifying SVNs',
    },
    {
      id: 'N13',
      label: 'Mortality',
      title:
        'Mortality (C14) \u2014 shown only where death recording is credible',
    },
    { id: 'N14', label: 'Round%', title: 'Weight rounding rate (C31)' },
    { id: 'N15', label: '%imposs', title: '% impossible weight changes (C27)' },
  ];
  var N_BY_ID = React.useMemo(
    function () {
      var m = {};
      ((SC && SC.measures) || []).forEach(function (x) {
        m[x.indicator] = x;
      });
      return m;
    },
    [payload],
  );
  var nByFLW = React.useMemo(
    function () {
      var m = {};
      ((SC && SC.byFLW) || []).forEach(function (f) {
        m[f.key] = f;
      });
      return m;
    },
    [payload],
  );
  function scoreCell(c, ind) {
    var e = ind && ind[c.id];
    if (!e) return '\u2014';
    if (c.denOnly) return e.n ? nCount(e.n) : '\u2014';
    var m = N_BY_ID[c.id] || {};
    if (e.band === 'insufficient')
      return (
        <span className="text-gray-400">
          n&lt;{m.min_denominator || MIN_DEN}
        </span>
      );
    if (e.value === null || e.value === undefined) return '\u2014';
    var v = Number(e.value);
    var text =
      m.unit === '%'
        ? (100 * v).toFixed(1) + '%'
        : m.unit === 'g'
        ? nCount(v)
        : m.unit === 'wks'
        ? String(Math.round(v * 10) / 10)
        : c.id === 'N07'
        ? v.toFixed(1)
        : nCount(v);
    if (e.band === 'notcredible')
      return (
        <span
          className="text-slate-400"
          title="Death recording is not credible for this organisation"
        >
          {text}
        </span>
      );
    return text;
  }

  // Per-worker roll-up. The payload stores each worker's cases as POSITIONS into
  // the case index -- holding the records in both places stored every case twice
  // and pushed the payload past the 5 MB cap -- so resolve them once, here, and
  // every consumer below (the case drill, flwDateRange, the counts) sees case
  // objects. Sorted busiest first, as the table has always been.
  var byFLW = React.useMemo(
    function () {
      var allCases = P.cases || [];
      return (P.byFLW || [])
        .map(function (f) {
          var r = f.rows || [];
          if (!r.length || typeof r[0] !== 'number') return f;
          return Object.assign({}, f, {
            rows: r
              .map(function (i) {
                return allCases[i];
              })
              .filter(Boolean),
          });
        })
        .sort(function (a, b) {
          return (b.rows || []).length - (a.rows || []).length;
        });
    },
    [payload],
  );

  // Most recent visit per organisation -- the one date that says whether an
  // organisation is still reporting. From the payload's case index.
  var lastVisitByLLO = React.useMemo(
    function () {
      var out = {};
      (P.cases || []).forEach(function (c) {
        var d = c.last_visit_date;
        if (!c.llo || !d) return;
        d = String(d).slice(0, 10);
        if (!out[c.llo] || d > out[c.llo]) out[c.llo] = d;
      });
      return out;
    },
    [payload],
  );

  // Is this cohort synthetic? Decided server-side against the synthetic
  // registry and carried in the payload, so a saved run keeps its disclaimer.
  var runIsSynthetic = !!(P.meta && P.meta.synthetic);

  // ── Case drill ────────────────────────────────────────────────────────────
  // The payload's case index, filtered to the opportunity (and worker) in hand.
  // On a live run each record is merged with this browser's per-case enrichment
  // (weight triple, growth velocity) -- display only, and absent on a saved run.
  var derivedByKey = React.useMemo(
    function () {
      var m = {};
      derived.forEach(function (d) {
        m[d.opp + '|' + d.entity_id] = d;
      });
      return m;
    },
    [derived],
  );

  function casesForDrill(opp, flwKey) {
    var flw = flwKey ? String(flwKey).split(FLW_SEP)[1] : null;
    return (P.cases || [])
      .filter(function (c) {
        return (
          Number(c.opportunity_id) === Number(opp) &&
          (!flw || c.username === flw)
        );
      })
      .map(function (c) {
        var d = derivedByKey[c.opportunity_id + '|' + c.entity_id];
        if (d) return Object.assign({}, c, d);
        return Object.assign(
          {
            flw: c.username,
            name: c.entity_id,
            num_visits: c.total_visits || 0,
            first_visit: c.first_visit_date,
            last_visit: c.last_visit_date,
          },
          c,
        );
      });
  }

  // ── UI state ───────────────────────────────────────────────────────────────
  var s1 = React.useState(null);
  var selLLO = s1[0],
    setSelLLO = s1[1];
  var s2 = React.useState(null);
  var selOpp = s2[0],
    setSelOpp = s2[1];
  var s3 = React.useState(null);
  var selInd = s3[0],
    setSelInd = s3[1];
  // The organisation drill filters its worker table by opportunity; this is
  // that filter, distinct from selOpp, which names the worker panel's opp.
  var s5 = React.useState(null);
  var oppFilter = s5[0],
    setOppFilter = s5[1];
  var s7 = React.useState(false);
  var showAllFLW = s7[0],
    setShowAllFLW = s7[1];
  var s4 = React.useState(null);
  var selFLW = s4[0],
    setSelFLW = s4[1];

  // ── Weekly trend ───────────────────────────────────────────────────────────
  // Two halves. ACTIVITY (visits, registrations) by week comes off this payload,
  // per drill scope, cut at the run's as-of date. INDICATORS over time are the
  // series of SAVED RUNS: each is computed as of its own period end by the same
  // builder, so the line is one point per saved report -- a weekly report, saved
  // weekly, is the time series. Nothing here re-grades anything.
  var trendKey = oppFilter
    ? 'opp:' + oppFilter
    : selLLO
    ? 'llo:' + selLLO
    : 'all';
  var weekly = React.useMemo(
    function () {
      var w = (P.weekly && P.weekly[trendKey]) || [];
      return w.slice(-26);
    },
    [payload, trendKey],
  );
  var historyState = React.useState(null);
  var history = historyState[0];
  var setHistory = historyState[1];

  // The definitions behind the indicators -- plain English AND the resolved SQL
  // chain -- from the same reader the MCP explain tool uses, fetched once when
  // the panel is first opened. Nothing is computed here.
  var explainState = React.useState({ status: 'idle', by: {}, error: null });
  var explainData = explainState[0];
  var setExplain = explainState[1];
  // The panel's open/closed state lives HERE, not in the <details> element.
  // AllIndicators is a function defined inside WorkflowUI, so every state change
  // (the explain fetch landing, a definition row toggling) gives it a new
  // identity and React remounts the <details>, wiping its native `open`. Seen
  // live on run 5623: the panel shut itself the instant a row was clicked.
  var allOpenState = React.useState(false);
  var allOpen = allOpenState[0];
  var setAllOpen = allOpenState[1];
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
  function explainUrl(fmt, download) {
    var sp = scopeParams();
    return (
      '/labs/workflow/api/' +
      definitionId() +
      '/indicator-definitions/' +
      sp +
      (sp ? '&' : '?') +
      'format=' +
      fmt +
      (download ? '&download=1' : '')
    );
  }
  function loadExplain() {
    if (explainData.status !== 'idle' || !definitionId()) return;
    setExplain({ status: 'loading', by: {}, error: null });
    fetch(explainUrl('json', false), { credentials: 'same-origin' })
      .then(function (r) {
        return r.json();
      })
      .then(function (j) {
        if (j.error) throw new Error(j.error);
        var by = {};
        (j.indicators || []).forEach(function (e) {
          by[e.indicator] = e;
        });
        setExplain({ status: 'ready', by: by, error: null });
      })
      .catch(function (err) {
        setExplain({
          status: 'error',
          by: {},
          error: String((err && err.message) || err),
        });
      });
  }
  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text);
    }
  }
  React.useEffect(
    function () {
      // The definition prop has not always carried `id`; the run knows its
      // definition, and so does the URL (/labs/workflow/<id>/run/).
      var pathMatch = String(window.location.pathname || '').match(
        /\/workflow\/(\d+)\//,
      );
      var defId =
        (definition && (definition.id || definition.definition_id)) ||
        (instance && instance.definition_id) ||
        (pathMatch && Number(pathMatch[1]));
      if (!defId) return;
      var cancelled = false;
      // Paths are under the snapshot's own state key (`state.snapshot.*`), and
      // the page's scope travels the same way the preview fetch sends it.
      var keys = ['programInd', 'byLLO', 'byOpp', 'pooledOverCredible', 'meta']
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
  // One point per as-of date: the graded cells for the current drill scope, taken
  // from each saved run's own snapshot. The run being viewed is a point too -- a
  // live run as of today, a saved one via the history (deduplicated by date).
  var historyPoints = React.useMemo(
    function () {
      function cellsOf(st) {
        if (!st) return null;
        if (oppFilter) {
          var o = (st.byOpp || []).filter(function (x) {
            return String(x.opp) === String(oppFilter);
          })[0];
          return o ? o.ind : null;
        }
        if (selLLO) {
          var l = (st.byLLO || []).filter(function (x) {
            return x.llo === selLLO;
          })[0];
          return l ? l.ind : null;
        }
        var ind = {};
        Object.keys(st.programInd || {}).forEach(function (k) {
          ind[k] = st.programInd[k];
        });
        // Undrilled mortality is the pooled-over-credible figure, as the headline.
        var pc = st.pooledOverCredible && st.pooledOverCredible.C14;
        if (pc && pc.ind) ind.C14 = pc.ind;
        return ind;
      }
      var byDate = {};
      (history || []).forEach(function (r) {
        // Unprefix the projection so the same reader serves history and this run.
        var st = {};
        Object.keys(r.state || {}).forEach(function (k) {
          st[k.replace(/^snapshot\./, '')] = r.state[k];
        });
        var d =
          (st.meta && st.meta.as_of) || String(r.period_end || '').slice(0, 10);
        if (!d) return;
        var ind = cellsOf(st);
        // A run saved before this payload shape carries none of these cells; it
        // is not a point.
        if (!ind || !Object.keys(ind).length) return;
        byDate[d] = {
          date: d,
          ind: ind,
          runId: r.id,
          n: st.meta && st.meta.cases,
        };
      });
      var own = {
        programInd: P.programInd,
        byLLO: P.byLLO,
        byOpp: P.byOpp,
        pooledOverCredible: P.pooledOverCredible,
        meta: P.meta,
      };
      var ownDate =
        (P.meta && P.meta.as_of) ||
        (view && view.asOf ? String(view.asOf).slice(0, 10) : '') ||
        new Date().toISOString().slice(0, 10);
      var ownInd = cellsOf(own);
      if (ownInd && !byDate[ownDate])
        byDate[ownDate] = {
          date: ownDate,
          ind: ownInd,
          runId: null,
          n: P.meta && P.meta.cases,
          current: true,
        };
      return Object.keys(byDate)
        .sort()
        .map(function (d) {
          return byDate[d];
        });
    },
    [history, payload, selLLO, oppFilter],
  );

  var llosRed = byLLO.filter(function (l) {
    return l.reds > 0;
  }).length;

  var BAND_CLS = {
    green: 'bg-green-100 text-green-800',
    yellow: 'bg-amber-100 text-amber-800',
    red: 'bg-red-100 text-red-800',
    unbanded: 'bg-gray-100 text-gray-500',
    insufficient: 'bg-gray-50 text-gray-400',
    nodata: 'bg-gray-50 text-gray-300',
    notcredible: 'bg-slate-100 text-slate-500',
    notinapp: 'bg-slate-100 text-slate-400 italic',
    unrecorded: 'bg-amber-100 text-amber-900',
  };
  // An indicator's entry for a scope, or a stand-in. This exists because the map is
  // no longer guaranteed complete: `evalAll` built it from the static `IND` array so
  // every id was always present, while `cIndFor` builds it from the catalog the
  // server sends -- which is empty while that request is in flight, and stays empty
  // if it fails. Fourteen display sites assumed completeness; one undefined entry
  // took the whole dashboard down with "Cannot read properties of undefined
  // (reading 'value')", because a React render that throws renders nothing at all.
  function entryOf(map, id) {
    return (map && map[id]) || { id: id, n: 0, value: null, band: 'nodata' };
  }

  // Neal's "parenthesize" for a thin, biased denominator. Rendering it as a value
  // like any other is the failure he is warning about: 96.5% off 20% coverage looks
  // like the best performer in the table.
  function fmtCov(ind, e) {
    var text = fmt(ind, e);
    return e && e.thinDenominator && text !== '—' ? '(' + text + ')' : text;
  }

  function covTitle(e) {
    if (!e || !e.thinDenominator) return undefined;
    return (
      'Thin denominator: only ' +
      Math.round(100 * (e.coverage || 0)) +
      '% of started cases carry a hospital discharge date, so this rate is computed ' +
      'over a self-selected minority and reads better than the programme does.'
    );
  }

  function fmt(ind, e) {
    if (!e || e.value === null || e.value === undefined) return '—';
    if (ind.unit === '%') return (100 * e.value).toFixed(1) + '%';
    if (ind.unit === 'n' || ind.unit === '/100')
      // A count carries thousands separators; a mean keeps its decimal.
      return ind.kind === 'count'
        ? nCount(e.value)
        : Number(e.value).toFixed(1);
    return Number(e.value).toFixed(1);
  }
  function bandLabel(e) {
    if (!e) return 'no data';
    if (e.band === 'notinapp') return 'not in this app';
    if (e.band === 'unrecorded') return 'no value reaches this row';
    if (e.band === 'notcredible') return 'shown, not credible';
    if (e.band === 'insufficient') return 'n<' + MIN_DEN;
    if (e.band === 'nodata') return 'no data';
    return e.band;
  }

  var openDefState = React.useState({});
  var openDef = openDefState[0];
  var setOpenDef = openDefState[1];
  function toggleDef(id) {
    setOpenDef(function (prev) {
      var next = Object.assign({}, prev);
      next[id] = !prev[id];
      return next;
    });
  }

  // One indicator's definition: the authored sentence, the sentence rendered
  // from its SQL, the properties it reads, and the compiled measure -- each
  // block copyable. Read from the explain endpoint; nothing derived here.
  function DefinitionRow(props) {
    var i = props.ind;
    var e = explainData.by[i.id];
    var en = (e && e.english) || null;
    if (
      explainData.status === 'loading' ||
      (!e && explainData.status !== 'error')
    )
      return (
        <tr className="bg-gray-50">
          <td colSpan={6} className="px-4 py-2 text-xs text-gray-400">
            Loading the definition…
          </td>
        </tr>
      );
    if (!e)
      return (
        <tr className="bg-gray-50">
          <td colSpan={6} className="px-4 py-2 text-xs text-red-700">
            {explainData.error || 'No definition for ' + i.id + '.'}
          </td>
        </tr>
      );
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
    function block(title, text) {
      return (
        <div className="mt-2">
          <div className="flex items-center justify-between">
            <span className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">
              {title}
            </span>
            <button
              type="button"
              className="text-xs text-indigo-600 hover:underline"
              onClick={function () {
                copyText(text);
              }}
            >
              Copy
            </button>
          </div>
          <pre className="mt-1 text-xs bg-white border border-gray-200 rounded-md p-2 overflow-x-auto whitespace-pre">
            {text}
          </pre>
        </div>
      );
    }
    return (
      <tr className="bg-gray-50">
        <td colSpan={6} className="px-4 py-3 text-sm">
          {en && en.plain ? <p className="text-gray-900">{en.plain}</p> : null}
          {en && en.definition ? (
            <p className="text-gray-600 text-xs mt-1">
              <span className="font-semibold text-gray-500">
                From the SQL:{' '}
              </span>
              {en.definition}
              {consts ? ' Constants: ' + consts + '.' : ''}
            </p>
          ) : null}
          {block('Measure', measureSql)}
          {propSql
            ? block('Properties and window derivations it reads', propSql)
            : null}
        </td>
      </tr>
    );
  }

  function IndicatorTable(props) {
    var rows = props.rows,
      ind = props.ind,
      onPick = props.onPick,
      withDefinitions = props.withDefinitions;
    return (
      <table className="min-w-full text-sm">
        <thead className="bg-gray-50 text-gray-500">
          <tr>
            <th className="px-3 py-2 text-left">ID</th>
            <th className="px-3 py-2 text-left">Indicator</th>
            <th className="px-3 py-2 text-left">Category</th>
            <th className="px-3 py-2 text-right">Value</th>
            <th className="px-3 py-2 text-right">n</th>
            <th className="px-3 py-2 text-left">Band</th>
          </tr>
        </thead>
        <tbody>
          {C_LIST.map(function (i) {
            var e = entryOf(ind, i.id);
            var rowsOut = [];
            rowsOut.push(
              <tr
                key={i.id}
                className={
                  'border-t border-gray-100 ' +
                  (onPick || withDefinitions
                    ? 'cursor-pointer hover:bg-indigo-50'
                    : '')
                }
                onClick={
                  onPick
                    ? function () {
                        onPick(i.id);
                      }
                    : withDefinitions
                    ? function () {
                        toggleDef(i.id);
                      }
                    : undefined
                }
              >
                <td className="px-3 py-2 font-mono text-xs text-gray-500">
                  {i.id}
                </td>
                <td className="px-3 py-2">
                  {i.name}
                  {i.prom === 'Top' && (
                    <span className="ml-2 text-xs text-indigo-500">top</span>
                  )}
                  {i.tbdInput && (
                    <span
                      className="ml-2 text-xs text-amber-600"
                      title={i.tbdInput}
                    >
                      provisional
                    </span>
                  )}
                  {i.scopeNote && (
                    <span
                      className="ml-2 text-xs text-gray-400"
                      title={i.scopeNote}
                    >
                      all LLOs pooled
                    </span>
                  )}
                </td>
                <td className="px-3 py-2 text-gray-500">{i.cat}</td>
                <td className="px-3 py-2 text-right font-medium">
                  {fmt(i, e)}
                </td>
                <td className="px-3 py-2 text-right text-gray-400">{e.n}</td>
                <td className="px-3 py-2">
                  <span
                    className={
                      'px-2 py-0.5 rounded text-xs ' + BAND_CLS[e.band]
                    }
                  >
                    {bandLabel(e)}
                  </span>
                  {withDefinitions ? (
                    <span className="ml-2 text-xs text-indigo-600">
                      {openDef[i.id] ? 'hide definition' : 'definition'}
                    </span>
                  ) : null}
                </td>
              </tr>,
            );
            if (withDefinitions && openDef[i.id])
              rowsOut.push(<DefinitionRow key={i.id + ':def'} ind={i} />);
            return rowsOut;
          })}
        </tbody>
      </table>
    );
  }

  var crumb = ['Programme'];
  if (selLLO) crumb.push(selLLO);

  // Saving a run persists the payload this page is already showing: the server
  // builds it again with the same builder and stores it. One step, because
  // nothing is computed here and so nothing has to be staged first.
  //
  // SCOPE OF "final": a completed run cannot be RE-OPENED, and that is all. A
  // workflow is meant to carry many runs; a bad one is deleted outright via
  // DELETE /labs/workflow/api/run/<run_id>/delete/.
  function saveRun() {
    if (!view || !view.complete) {
      window.alert('This run does not support saved snapshots yet.');
      return;
    }
    view.complete({
      confirm:
        'Save this run? Its figures become final and load from the saved ' +
        'snapshot. Re-running later creates a new run; this one stays in the ' +
        'history.',
    });
  }

  // ══ The report ══════════════════════════════════════════════════════════════
  // One page, one table. The headline tiles carry a week-on-week delta, the
  // organisations table is Neal's scorecard with last-visit and attention
  // columns, and the charts sit under it: activity by week off this payload,
  // indicators over time off the saved-run history. The full C-series is a
  // collapsed panel, not a second table. One level down, the same shape
  // repeats for an organisation with its workers as the one table.

  var MONTHS = [
    'Jan',
    'Feb',
    'Mar',
    'Apr',
    'May',
    'Jun',
    'Jul',
    'Aug',
    'Sep',
    'Oct',
    'Nov',
    'Dec',
  ];
  function dateLbl(s) {
    if (!s) return '';
    var p = String(s).slice(0, 10).split('-');
    if (p.length < 3) return String(s);
    return Number(p[2]) + ' ' + (MONTHS[Number(p[1]) - 1] || p[1]);
  }
  function daysBetween(a, b) {
    var da = new Date(String(a).slice(0, 10) + 'T00:00:00Z');
    var db = new Date(String(b).slice(0, 10) + 'T00:00:00Z');
    if (isNaN(da.getTime()) || isNaN(db.getTime())) return null;
    return Math.round((db - da) / 86400000);
  }
  var asOf =
    (P.meta && P.meta.as_of) ||
    (view && view.asOf ? String(view.asOf).slice(0, 10) : '') ||
    new Date().toISOString().slice(0, 10);

  var BAND_WORD = { green: 'On target', yellow: 'Watch', red: 'Off target' };
  var BAND_TEXT = {
    green: 'text-green-700',
    yellow: 'text-amber-700',
    red: 'text-red-700',
  };
  var CELL_TINT = {
    red: 'bg-red-50 text-red-700 font-semibold',
    yellow: 'bg-amber-50 text-amber-800 font-semibold',
  };
  function tintFor(e) {
    return (e && CELL_TINT[e.band]) || '';
  }

  // ── Scope: programme, or one organisation (optionally one opportunity) ────
  var scopeLLO = selLLO
    ? byLLO.filter(function (l) {
        return l.llo === selLLO;
      })[0] || null
    : null;
  var scopeOppRow = oppFilter
    ? byOpp.filter(function (o) {
        return String(o.opp) === String(oppFilter);
      })[0] || null
    : null;
  var scopeInd = scopeOppRow
    ? scopeOppRow.ind
    : scopeLLO
    ? scopeLLO.ind
    : programInd;
  var scopeName = scopeOppRow
    ? oppLabel(oppFilter)
    : scopeLLO
    ? scopeLLO.llo
    : 'Programme';

  // Workers per organisation, for the table's row meta. byFLW is keyed by
  // (opportunity, username); an organisation is the union over its opps.
  var flwCountByLLO = React.useMemo(
    function () {
      var oppLLO = {};
      byLLO.forEach(function (l) {
        (l.opps || []).forEach(function (o) {
          oppLLO[String(o.opp)] = l.llo;
        });
      });
      var out = {};
      byFLW.forEach(function (f) {
        var llo = oppLLO[String(f.opp)];
        if (!llo) return;
        out[llo] = (out[llo] || 0) + 1;
      });
      return out;
    },
    [payload, byFLW],
  );

  // ── Headline tiles ────────────────────────────────────────────────────────
  // Value from this payload for the scope in hand; delta against the previous
  // saved report in the same scope, off the history the charts already use.
  var TILES = [
    { id: 'C02', label: 'Started cases', count: true, sub: '' },
    {
      id: 'C09',
      label: 'Weight data sufficient',
      pct: true,
      target: 0.6,
      sub: 'target 60%',
    },
    {
      id: 'C13',
      label: 'Early growth rate',
      unit: 'g/kg/day',
      target: 15,
      sub: 'target 15',
    },
    { id: 'C14', label: 'Mortality', pct: true, target: 0.04, sub: '' },
    {
      id: 'C15',
      label: 'Lost by day 28',
      pct: true,
      target: 0.1,
      sub: 'target 10%',
    },
  ];
  // The N-series cells for the scope in hand -- the scorecard's own rows.
  function nScopeInd() {
    if (!SC) return null;
    if (oppFilter) {
      var o = (SC.byOpp || []).filter(function (x) {
        return String(x.opp) === String(oppFilter);
      })[0];
      return o ? o.ind : null;
    }
    if (selLLO) {
      var l = (SC.byLLO || []).filter(function (x) {
        return x.llo === selLLO;
      })[0];
      return l ? l.ind : null;
    }
    return SC.programme || null;
  }
  // Started reads the scorecard's N03 (the demo compute spec: two or more
  // visits) when the payload carries it, so the tile and the table agree. The
  // workbook's C02 (one follow-up) is the fallback for an older run.
  function tileEntry(id) {
    if (id === 'C14' && !selLLO && !oppFilter) return mortalityCredible.ind;
    if (id === 'C02') {
      var n = nScopeInd();
      if (n && n.N03) return n.N03;
    }
    return entryOf(scopeInd, id);
  }
  function tileId(t) {
    if (t.id === 'C02' && nScopeInd() && nScopeInd().N03) return 'N03';
    return t.id;
  }
  function tileValue(t, e) {
    if (!e || e.value === null || e.value === undefined) return '—';
    if (e.band === 'insufficient') return 'n<' + MIN_DEN;
    if (t.count) return nCount(e.value);
    if (t.pct) return (100 * e.value).toFixed(1) + '%';
    return Number(e.value).toFixed(1);
  }
  function tileDelta(t) {
    // The run history projects the C-series only; a delta for the spec's
    // started count against the workbook's would compare two definitions.
    if (t.id === 'C02' && tileId(t) === 'N03') return '';
    if (historyPoints.length < 2) return '';
    var prev = historyPoints[historyPoints.length - 2];
    var cur = historyPoints[historyPoints.length - 1];
    var a = prev.ind && prev.ind[t.id],
      b = cur.ind && cur.ind[t.id];
    if (
      !a ||
      !b ||
      a.value === null ||
      a.value === undefined ||
      b.value === null ||
      b.value === undefined
    )
      return '';
    var d = Number(b.value) - Number(a.value);
    var since = ' since ' + dateLbl(prev.date);
    if (t.pct) {
      if (Math.abs(d) < 0.0005) return 'Unchanged' + since;
      return (
        (d > 0 ? '+' : '−') + (100 * Math.abs(d)).toFixed(1) + ' pt' + since
      );
    }
    if (Math.abs(d) < 0.05) return 'Unchanged' + since;
    var s = t.count ? nCount(Math.abs(d)) : Math.abs(d).toFixed(1);
    return (d > 0 ? '+' : '−') + s + since;
  }
  function Tiles() {
    var started = tileEntry('C02');
    var pctOfTarget =
      !selLLO && started.value
        ? Math.min(100, (started.value / 25000) * 100)
        : null;
    return (
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        {TILES.map(function (t) {
          var e = tileEntry(t.id);
          var band = e && BAND_WORD[e.band] ? e.band : null;
          var sub = t.sub;
          if (t.id === 'C14')
            sub =
              selLLO || oppFilter
                ? 'two-sided'
                : mortalityCredible.llos && mortalityCredible.llos.length
                ? mortalityCredible.llos.join(' + ') + ' only'
                : 'no credible recorder';
          if (t.id === 'C02')
            sub =
              (tileId(t) === 'N03' ? 'two or more visits' : 'one follow-up') +
              (pctOfTarget !== null ? ' · of 25,000 target by Q1 2027' : '');
          return (
            <div
              key={t.id}
              className="bg-white border border-gray-200 rounded-xl px-4 pt-3 pb-3"
            >
              <div className="flex items-center justify-between gap-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
                <span className="truncate">{t.label}</span>
                <span className="font-mono font-normal normal-case tracking-normal text-gray-300">
                  {tileId(t)}
                </span>
              </div>
              <div className="mt-1 text-2xl font-bold text-gray-900 tabular-nums">
                {tileValue(t, e)}
                {t.unit ? (
                  <span className="ml-2 text-xs font-medium text-gray-400">
                    {t.unit}
                  </span>
                ) : null}
              </div>
              <div className="mt-1 flex items-center justify-between gap-2 text-xs text-gray-600 whitespace-nowrap">
                <span className="truncate" title={sub}>
                  {sub}
                </span>
                {band ? (
                  <span className={'font-semibold ' + BAND_TEXT[band]}>
                    {BAND_WORD[band]}
                  </span>
                ) : null}
              </div>
              {pctOfTarget !== null && t.id === 'C02' ? (
                <div className="mt-2 h-1.5 rounded bg-gray-100 overflow-hidden">
                  <div
                    className="h-full rounded bg-indigo-600"
                    style={{ width: pctOfTarget.toFixed(1) + '%' }}
                  />
                </div>
              ) : null}
              <div className="mt-1 text-xs text-gray-400 whitespace-nowrap truncate">
                {tileDelta(t) || ' '}
              </div>
            </div>
          );
        })}
      </div>
    );
  }

  // ── Activity by week: bars = babies registered, line = visits, ONE scale ──
  function ActivityChart(props) {
    var weeks = props.weeks || [];
    var W = 720,
      H = 200,
      L = 42,
      R = 12,
      T = 14,
      B = 28;
    if (!weeks.length)
      return (
        <div className="text-xs text-gray-400 py-10 text-center">
          No dated visits in this scope.
        </div>
      );
    var max = 1;
    weeks.forEach(function (w) {
      max = Math.max(max, w.visits || 0, w.registered || 0);
    });
    var step = Math.pow(10, Math.floor(Math.log(max) / Math.LN10));
    var top = Math.ceil(max / step) * step;
    var iw = W - L - R,
      ih = H - T - B;
    var bw = iw / weeks.length;
    function y(v) {
      return T + ih - (v / top) * ih;
    }
    var ticks = [0, top / 2, top];
    var path = weeks
      .map(function (w, i) {
        return (
          (i ? 'L' : 'M') +
          (L + i * bw + bw / 2).toFixed(1) +
          ' ' +
          y(w.visits || 0).toFixed(1)
        );
      })
      .join(' ');
    return (
      <svg
        viewBox={'0 0 ' + W + ' ' + H}
        className="w-full h-auto block"
        role="img"
        aria-label="Registrations and visits by week"
      >
        {ticks.map(function (t) {
          return (
            <g key={t}>
              <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke="#eeeef4" />
              <text
                x={L - 6}
                y={y(t) + 4}
                fontSize="10"
                fill="#9ca3af"
                textAnchor="end"
              >
                {nCount(t)}
              </text>
            </g>
          );
        })}
        {weeks.map(function (w, i) {
          var x = L + i * bw;
          var h = ih - (y(w.registered || 0) - T);
          return (
            <g key={w.week}>
              <rect
                x={(x + bw * 0.2).toFixed(1)}
                y={y(w.registered || 0).toFixed(1)}
                width={(bw * 0.6).toFixed(1)}
                height={h.toFixed(1)}
                rx="2"
                fill="#a5b4fc"
              >
                <title>
                  {'Week of ' +
                    dateLbl(w.week) +
                    ': ' +
                    nCount(w.registered) +
                    ' registered, ' +
                    nCount(w.visits) +
                    ' visits'}
                </title>
              </rect>
              {i % 4 === 0 || i === weeks.length - 1 ? (
                <text
                  x={x + bw / 2}
                  y={H - 8}
                  fontSize="10"
                  fill="#9ca3af"
                  textAnchor="middle"
                >
                  {dateLbl(w.week)}
                </text>
              ) : null}
            </g>
          );
        })}
        <path
          d={path}
          fill="none"
          stroke="#4f46e5"
          strokeWidth="2"
          strokeLinejoin="round"
        />
        {weeks.map(function (w, i) {
          return (
            <circle
              key={'v' + w.week}
              cx={L + i * bw + bw / 2}
              cy={y(w.visits || 0)}
              r={i === weeks.length - 1 ? 4 : 2.5}
              fill="#4f46e5"
              stroke="#fff"
              strokeWidth="1.5"
            >
              <title>
                {'Week of ' +
                  dateLbl(w.week) +
                  ': ' +
                  nCount(w.visits) +
                  ' visits, ' +
                  nCount(w.registered) +
                  ' registered'}
              </title>
            </circle>
          );
        })}
      </svg>
    );
  }

  // ── One indicator over the saved reports, small-multiple sized ────────────
  function SmallTrend(props) {
    var id = props.id,
      label = props.label,
      pct = props.pct,
      target = props.target;
    var ind = indOf(id);
    var pts = historyPoints.map(function (p) {
      var e = p.ind && p.ind[id];
      if (!e || e.value === null || e.value === undefined) return null;
      if (e.band === 'insufficient' || e.band === 'notcredible') return null;
      return { v: Number(e.value), e: e, date: p.date, n: e.n };
    });
    var W = 260,
      H = 130,
      L = 36,
      R = 10,
      T = 12,
      B = 22;
    var n = pts.length;
    var real = pts.filter(Boolean);
    var cur = real.length ? real[real.length - 1].e : null;
    var body;
    if (real.length < 2) {
      body = (
        <div className="text-xs text-gray-400 py-8 text-center">
          {real.length
            ? 'One report so far — the line builds as reports are saved weekly.'
            : 'No report has enough cases to score this yet.'}
        </div>
      );
    } else {
      var lo = target,
        hi = target;
      real.forEach(function (p) {
        lo = Math.min(lo, p.v);
        hi = Math.max(hi, p.v);
      });
      if (pct) {
        lo = Math.max(0, Math.floor((lo - 0.05) * 10) / 10);
        hi = Math.min(1, Math.ceil((hi + 0.05) * 10) / 10);
      } else {
        lo = Math.floor(lo - 1);
        hi = Math.ceil(hi + 1);
      }
      if (hi <= lo) hi = lo + 1;
      var iw = W - L - R,
        ih = H - T - B;
      function x(i) {
        return L + (n > 1 ? (i * iw) / (n - 1) : iw / 2);
      }
      function y(v) {
        return T + ih - ((v - lo) / (hi - lo)) * ih;
      }
      function f(v) {
        return pct ? Math.round(v * 100) + '%' : Number(v).toFixed(0);
      }
      var d = '';
      var pen = false;
      pts.forEach(function (p, i) {
        if (!p) {
          pen = false;
          return;
        }
        d +=
          (pen ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(p.v).toFixed(1) + ' ';
        pen = true;
      });
      var last = real[real.length - 1];
      var lastIdx = pts.lastIndexOf(last);
      var yTicks = [lo, (lo + hi) / 2, hi];
      var xTicks = [0, Math.floor((n - 1) / 2), n - 1];
      var dotColor =
        last.e.band === 'red'
          ? '#dc2626'
          : last.e.band === 'yellow'
          ? '#d97706'
          : last.e.band === 'green'
          ? '#15803d'
          : '#4f46e5';
      body = (
        <svg
          viewBox={'0 0 ' + W + ' ' + H}
          className="w-full h-auto block"
          role="img"
          aria-label={label}
        >
          {yTicks.map(function (t) {
            return (
              <g key={t}>
                <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke="#eeeef4" />
                <text
                  x={L - 5}
                  y={y(t) + 3.5}
                  fontSize="9.5"
                  fill="#9ca3af"
                  textAnchor="end"
                >
                  {f(t)}
                </text>
              </g>
            );
          })}
          <line
            x1={L}
            x2={W - R}
            y1={y(target)}
            y2={y(target)}
            stroke="#c3c6d3"
            strokeDasharray="3 3"
          />
          <text
            x={W - R}
            y={y(target) - 3}
            fontSize="9"
            fill="#9ca3af"
            textAnchor="end"
          >
            {'target ' + f(target)}
          </text>
          <path
            d={d}
            fill="none"
            stroke="#4f46e5"
            strokeWidth="2"
            strokeLinejoin="round"
          />
          {pts.map(function (p, i) {
            if (!p) return null;
            var isLast = i === lastIdx;
            return (
              <circle
                key={p.date}
                cx={x(i)}
                cy={y(p.v)}
                r={isLast ? 4.5 : 3}
                fill={isLast ? dotColor : '#4f46e5'}
                stroke="#fff"
                strokeWidth="1.5"
              >
                <title>
                  {'As of ' +
                    dateLbl(p.date) +
                    ': ' +
                    fmt(ind, p.e) +
                    ' (n = ' +
                    nCount(p.n) +
                    ')'}
                </title>
              </circle>
            );
          })}
          {xTicks.map(function (i) {
            return (
              <text
                key={'x' + i}
                x={x(i)}
                y={H - 7}
                fontSize="9.5"
                fill="#9ca3af"
                textAnchor={i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'}
              >
                {dateLbl(historyPoints[i].date)}
              </text>
            );
          })}
        </svg>
      );
    }
    return (
      <div className="bg-white border border-gray-200 rounded-xl px-4 pt-3 pb-2">
        <div className="flex items-baseline justify-between gap-2">
          <div
            className="text-sm font-semibold text-gray-900 whitespace-nowrap"
            title={ind.name + ' (' + id + ')'}
          >
            {label}
          </div>
          {cur && BAND_WORD[cur.band] ? (
            <span className={'text-xs font-semibold ' + BAND_TEXT[cur.band]}>
              {BAND_WORD[cur.band]}
            </span>
          ) : null}
        </div>
        {body}
      </div>
    );
  }

  function ChartsRow() {
    var savedCount = historyPoints.filter(function (p) {
      return !p.current;
    }).length;
    return (
      <div>
        <div className="grid grid-cols-1 lg:grid-cols-6 gap-3">
          <div className="lg:col-span-2 bg-white border border-gray-200 rounded-xl px-4 pt-3 pb-2">
            <div className="flex items-baseline justify-between gap-2 flex-wrap">
              <div className="text-sm font-semibold text-gray-900">
                Registrations and visits by week
              </div>
              <div className="flex items-center gap-3 text-xs text-gray-500">
                <span>
                  <span
                    className="inline-block w-2.5 h-2.5 rounded-sm mr-1 align-middle"
                    style={{ background: '#a5b4fc' }}
                  />
                  Babies registered
                </span>
                <span>
                  <span
                    className="inline-block w-3 h-0.5 mr-1 align-middle"
                    style={{ background: '#4f46e5' }}
                  />
                  Visits
                </span>
              </div>
            </div>
            <ActivityChart weeks={weekly} />
          </div>
          <SmallTrend id="C09" label="Weight data" pct={true} target={0.6} />
          <SmallTrend id="C13" label="Growth rate" pct={false} target={15} />
          <SmallTrend id="C14" label="Mortality" pct={true} target={0.04} />
          <SmallTrend id="C15" label="Lost by d28" pct={true} target={0.1} />
        </div>
        <p className="mt-2 text-xs text-gray-400">
          Activity is counted in the week it happened, to {dateLbl(asOf)}. Each
          indicator point is the figure as of a saved report
          {savedCount ? ' (' + savedCount + ' saved)' : ''}; a gap is a report
          with too few cases to score, not a zero. Dashed line = target.
        </p>
      </div>
    );
  }

  // ── One scorecard head and one cell renderer, for BOTH tables ─────────────
  // The organisations table and the workers table are the same 15 columns in
  // the same groups, the same labels and the same type; only the leading
  // (who) and trailing (last visit / attention / review) columns differ. One
  // component for the head and one for the cells is what keeps them identical.
  var SCORECARD_GROUPS = [
    { label: 'Scale', span: 3 },
    { label: 'Cohort', span: 2 },
    { label: 'Enrolment & visits', span: 3 },
    { label: 'Growth quality (of Qual N)', span: 4 },
    { label: 'Outcome', span: 1 },
    { label: 'Data quality', span: 2 },
  ];
  function scorecardTh(c, i) {
    return (
      <th
        key={i}
        className="px-1.5 py-2 text-right whitespace-nowrap font-semibold text-gray-600"
        title={c.title}
      >
        {c.label}
        <div className="font-mono text-[10px] font-normal text-gray-300">
          {c.id}
        </div>
      </th>
    );
  }
  function ScorecardHead(props) {
    var lead = props.lead || [];
    var trail = props.trail || [];
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
          {trail.map(function (l, i) {
            return <th key={'t' + i} className="px-1.5 py-1"></th>;
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
          {SCORECARD.map(scorecardTh)}
          {trail.map(function (l, i) {
            return (
              <th
                key={'t' + i}
                className="px-1.5 py-2 text-right font-semibold text-gray-600"
              >
                {l}
              </th>
            );
          })}
        </tr>
      </thead>
    );
  }
  function scorecardCells(ind) {
    return SCORECARD.map(function (c, i) {
      var e = ind && ind[c.id];
      var tint = c.denOnly ? '' : tintFor(e);
      return (
        <td key={i} className={'px-1.5 py-2 text-right tabular-nums ' + tint}>
          {scoreCell(c, ind)}
        </td>
      );
    });
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
        <span>n&lt;20 = below the minimum denominator</span>
        <span className="ml-auto">{props.right}</span>
      </div>
    );
  }
  function attentionCell(reds, yellows) {
    return (
      <td className="px-1.5 py-2 text-right">
        {reds ? (
          <span
            className="inline-block px-1.5 py-0.5 rounded-md text-xs font-semibold bg-red-100 text-red-800 text-center"
            title={reds + ' off target · ' + (yellows || 0) + ' to watch'}
          >
            {reds}
          </span>
        ) : yellows ? (
          <span
            className="inline-block px-1.5 py-0.5 rounded-md text-xs font-semibold bg-amber-100 text-amber-800 text-center"
            title={yellows + ' to watch'}
          >
            {yellows}
          </span>
        ) : (
          <span className="text-gray-300">0</span>
        )}
      </td>
    );
  }

  // ── The organisations table: Neal's scorecard, plus last visit and attention ──
  function OrgTable() {
    if (!SC) return null;
    var rows = byLLO.slice().sort(function (a, b) {
      return (entryOf(b.ind, 'C01').n || 0) - (entryOf(a.ind, 'C01').n || 0);
    });
    var nByLLO = {};
    (SC.byLLO || []).forEach(function (r) {
      nByLLO[r.llo] = r.ind;
    });
    return (
      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <div className="px-4 py-3 flex items-baseline justify-between gap-3 flex-wrap">
          <div className="font-semibold text-gray-900">Organisations</div>
          <div className="text-xs text-gray-400">
            15-metric scorecard · as of {dateLbl(asOf)} · click a row to open
            the organisation
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full text-xs">
            <ScorecardHead
              lead={['Organisation']}
              trail={['Last visit', 'Attention']}
            />
            <tbody>
              {rows.map(function (l) {
                var lv = lastVisitByLLO[l.llo];
                var gap = lv ? daysBetween(lv, asOf) : null;
                var stale = gap !== null && gap > 14;
                return (
                  <tr
                    key={l.llo}
                    className="border-t border-gray-100 cursor-pointer hover:bg-indigo-50"
                    onClick={function () {
                      setSelLLO(l.llo);
                      setOppFilter(null);
                      setSelOpp(null);
                      setSelFLW(null);
                    }}
                  >
                    <td className="px-3 py-2 whitespace-nowrap">
                      <div className="font-semibold text-indigo-700">
                        {l.llo}
                      </div>
                      <div className="text-xs text-gray-400">
                        {l.opps.length +
                          (l.opps.length === 1
                            ? ' opportunity'
                            : ' opportunities') +
                          ' · ' +
                          (flwCountByLLO[l.llo] || 0) +
                          ' workers'}
                      </div>
                    </td>
                    {scorecardCells(nByLLO[l.llo])}
                    <td
                      className={
                        'px-1.5 py-2 text-right whitespace-nowrap tabular-nums ' +
                        (stale ? 'text-red-700 font-semibold' : 'text-gray-600')
                      }
                      title={
                        stale ? 'No visits for ' + gap + ' days' : undefined
                      }
                    >
                      {lv ? dateLbl(lv) : '—'}
                    </td>
                    {attentionCell(l.reds, l.yellows)}
                  </tr>
                );
              })}
              <tr className="border-t-2 border-gray-200 bg-gray-50 font-semibold">
                <td className="px-3 py-2 whitespace-nowrap">
                  <div className="text-gray-900">All organisations</div>
                  <div className="text-xs font-normal text-gray-400">
                    {byLLO.length +
                      ' organisations · ' +
                      ((P.meta && P.meta.opportunities) || byOpp.length) +
                      ' opportunities'}
                  </div>
                </td>
                {scorecardCells(SC.programme)}
                <td className="px-1.5 py-2"></td>
                <td className="px-1.5 py-2"></td>
              </tr>
            </tbody>
          </table>
        </div>
        <ScorecardLegend right="Hover a column for its definition" />
      </div>
    );
  }

  // ── One organisation: its opportunities as filter chips, its workers as the table ──
  function OppChips() {
    if (!scopeLLO) return null;
    function chip(label, value, reds, count) {
      var on = (oppFilter || null) === value;
      return (
        <button
          key={String(value)}
          type="button"
          onClick={function () {
            setOppFilter(value);
            setSelFLW(null);
            setSelOpp(null);
          }}
          className={
            'px-3 py-1 rounded-full text-xs font-medium border ' +
            (on
              ? 'bg-indigo-600 border-indigo-600 text-white'
              : 'bg-white border-gray-200 text-gray-600 hover:bg-indigo-50')
          }
        >
          {label}
          {count !== null ? ' · ' + nCount(count) + ' babies' : ''}
          {reds ? (
            <span className={on ? ' text-red-100' : ' text-red-700'}>
              {' · ' + reds + ' off target'}
            </span>
          ) : null}
        </button>
      );
    }
    return (
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs text-gray-400 mr-1">Opportunities</span>
        {chip('All ' + scopeLLO.opps.length, null, 0, null)}
        {scopeLLO.opps.map(function (o) {
          var reds = Object.keys(o.ind || {}).filter(function (k) {
            return o.ind[k].band === 'red';
          }).length;
          return chip(oppLabel(o.opp), o.opp, reds, entryOf(o.ind, 'C01').n);
        })}
      </div>
    );
  }

  // The workers table: the SAME scorecard as the organisations table, one row
  // per worker, with the worker's cells from the N-series byFLW.
  function FLWTable() {
    if (!scopeLLO) return null;
    var oppSet = {};
    scopeLLO.opps.forEach(function (o) {
      oppSet[String(o.opp)] = true;
    });
    var all = byFLW.filter(function (f) {
      if (oppFilter) return String(f.opp) === String(oppFilter);
      return oppSet[String(f.opp)];
    });
    var CAP = 25;
    var rows = showAllFLW ? all : all.slice(0, CAP);
    var sel = selFLW
      ? byFLW.filter(function (x) {
          return x.key === selFLW;
        })[0]
      : null;
    return (
      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <div className="px-4 py-3 flex items-baseline justify-between gap-3 flex-wrap">
          <div className="font-semibold text-gray-900">Frontline workers</div>
          <div className="text-xs text-gray-400">
            {(showAllFLW || all.length <= CAP
              ? all.length + ' workers'
              : 'busiest ' + rows.length + ' of ' + all.length + ' workers') +
              (oppFilter
                ? ' in ' + oppLabel(oppFilter)
                : ' across ' + scopeLLO.opps.length + ' opportunities') +
              ' · click a worker for their indicators, cases and review'}
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full text-xs">
            <ScorecardHead
              lead={['Worker', 'Opportunity']}
              trail={['Attention', '']}
            />
            <tbody>
              {rows.map(function (f) {
                var on = selFLW === f.key;
                var reviewUrl = flwReviewUrl(f);
                var nf = nByFLW[f.key];
                return (
                  <tr
                    key={f.key}
                    className={
                      'border-t border-gray-100 cursor-pointer hover:bg-indigo-50 ' +
                      (on ? 'bg-indigo-50' : '')
                    }
                    onClick={function () {
                      setSelFLW(on ? null : f.key);
                      setSelOpp(on ? null : f.opp);
                    }}
                  >
                    <td className="px-3 py-2 font-semibold text-indigo-700 whitespace-nowrap">
                      {f.flw}
                    </td>
                    <td className="px-1.5 py-2 text-gray-600 whitespace-nowrap">
                      {oppLabel(f.opp)}
                    </td>
                    {scorecardCells(nf && nf.ind)}
                    {attentionCell(f.reds, f.yellows)}
                    <td className="px-1.5 py-2 text-right whitespace-nowrap">
                      {reviewUrl ? (
                        <a
                          className="inline-block px-2.5 py-1 rounded-md text-xs font-medium border border-gray-200 text-indigo-700 hover:bg-indigo-50 bg-white"
                          href={reviewUrl}
                          onClick={function (ev) {
                            ev.stopPropagation();
                          }}
                        >
                          Review →
                        </a>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {all.length > CAP ? (
          <div className="px-4 py-2 border-t border-gray-100 text-xs">
            <button
              type="button"
              className="text-indigo-600 hover:underline"
              onClick={function () {
                setShowAllFLW(!showAllFLW);
              }}
            >
              {showAllFLW
                ? 'Show the busiest ' + CAP
                : 'Show all ' + all.length + ' workers'}
            </button>
          </div>
        ) : null}
        <ScorecardLegend right="Review opens the worker's own page: stats, cases, growth charts, image audit" />
        {sel ? <FLWPanel f={sel} /> : null}
      </div>
    );
  }

  // ── The selected worker: their full indicator set, the review link, the audit, their cases ──
  function FLWPanel(props) {
    var f = props.f;
    var st = auditState[f.key] || {};
    var agent = AGENT_BY_LLO[f.llo];
    var unverified = UNVERIFIED_SCALE.indexOf(f.llo) !== -1;
    var reviewUrl = flwReviewUrl(f);
    var RECENT = 8;
    var caseRows = casesForDrill(f.opp, f.key);
    var recentCases = caseRows
      .slice()
      .sort(function (a, b) {
        return String(b.last_visit || '') < String(a.last_visit || '') ? -1 : 1;
      })
      .slice(0, RECENT);
    return (
      <div className="border-t-2 border-indigo-100">
        <div className="px-4 py-3 flex items-center justify-between gap-3 flex-wrap bg-indigo-50">
          <div>
            <span className="font-semibold text-gray-900">{f.flw}</span>
            <span className="ml-2 text-xs text-gray-500">
              {oppLabel(f.opp)} · {caseCount(f)} cases ·{' '}
              {f.reds
                ? f.reds +
                  ' indicator' +
                  (f.reds === 1 ? '' : 's') +
                  ' off target'
                : 'no indicator off target'}
              {agent
                ? ' · ' +
                  (agent === 'scale_dial_read' ? 'dial' : 'digital') +
                  ' scale reader'
                : ' · no scale reader for this LLO'}
              {unverified ? ' (hardware unconfirmed)' : ''}
            </span>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            {reviewUrl ? (
              <a
                className="inline-block px-3 py-1.5 rounded-md text-sm font-medium bg-indigo-600 text-white hover:bg-indigo-700"
                href={reviewUrl}
              >
                Open worker review →
              </a>
            ) : null}
            {AUDIT_ENABLED ? (
              <button
                type="button"
                disabled={st.status === 'running'}
                onClick={function () {
                  auditWorker(f);
                }}
                className={
                  'px-3 py-1.5 rounded-md text-sm font-medium border ' +
                  (st.status === 'running'
                    ? 'bg-gray-100 border-gray-200 text-gray-400'
                    : 'bg-white border-indigo-200 text-indigo-700 hover:bg-indigo-100')
                }
              >
                {st.status === 'running'
                  ? 'Opening audit…'
                  : 'Audit recent images'}
              </button>
            ) : null}
            <button
              type="button"
              className="text-xs text-gray-500 hover:text-gray-800"
              onClick={function () {
                setSelFLW(null);
                setSelOpp(null);
              }}
            >
              Close
            </button>
          </div>
        </div>
        {st.status === 'created' && (
          <div className="px-4 py-2 text-xs text-green-700 bg-green-50 border-t border-green-100">
            Audit queued for {f.flw}. It appears under Audits for{' '}
            {oppLabel(f.opp)} once the sessions finish building.
          </div>
        )}
        {st.status === 'error' && (
          <div className="px-4 py-2 text-xs text-red-700 bg-red-50 border-t border-red-100">
            Could not open the audit: {st.message}
          </div>
        )}
        <div className="grid grid-cols-1 lg:grid-cols-2">
          <div className="border-t border-gray-100 px-4 py-3">
            <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
              Indicators
              <span className="ml-2 font-normal normal-case tracking-normal text-gray-400">
                this worker, as of {dateLbl(asOf)}
              </span>
            </div>
            <IndicatorChips ind={f.ind} />
          </div>
          <div className="border-t border-gray-100">
            <div className="px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">
              Recent cases
              <span className="ml-2 font-normal normal-case tracking-normal text-gray-400">
                {caseRows.length > RECENT
                  ? 'latest ' + RECENT + ' of ' + caseRows.length
                  : caseRows.length +
                    (caseRows.length === 1 ? ' case' : ' cases')}
              </span>
            </div>
            <div className="overflow-x-auto">
              <CaseTable rows={recentCases} />
            </div>
            {reviewUrl ? (
              <div className="px-4 py-2 text-xs border-t border-gray-100">
                <a className="text-indigo-600 hover:underline" href={reviewUrl}>
                  {'All ' +
                    caseRows.length +
                    ' cases, growth charts and the image audit in the worker review →'}
                </a>
              </div>
            ) : null}
          </div>
        </div>
      </div>
    );
  }

  // The worker's indicator set as chips: only rows that carry a value, the band
  // on the left edge, the full title and n on hover. Twenty-two rows of table
  // was the wrong shape for a panel that sits under a table.
  function IndicatorChips(props) {
    var ind = props.ind || {};
    var EDGE = {
      green: '#15803d',
      yellow: '#d97706',
      red: '#dc2626',
    };
    var items = C_LIST.filter(function (i) {
      var e = ind[i.id];
      return e && e.value !== null && e.value !== undefined;
    });
    if (!items.length)
      return (
        <div className="text-xs text-gray-400">
          No scored indicators for this worker.
        </div>
      );
    return (
      <div className="flex flex-wrap gap-1.5">
        {items.map(function (i) {
          var e = ind[i.id];
          var ins = e.band === 'insufficient';
          return (
            <span
              key={i.id}
              className="inline-flex items-center gap-1.5 border border-gray-200 rounded-md pl-2 pr-2 py-1 text-xs bg-white"
              style={{
                borderLeft: '3px solid ' + (EDGE[e.band] || '#c3c6d3'),
              }}
              title={i.id + ' · ' + i.name + ' · n = ' + nCount(e.n)}
            >
              <span className="text-gray-600 whitespace-nowrap">{i.name}</span>
              <span
                className={
                  'font-semibold whitespace-nowrap ' +
                  (ins ? 'text-gray-400' : BAND_TEXT[e.band] || 'text-gray-900')
                }
              >
                {ins ? 'n<' + MIN_DEN : fmt(i, e)}
              </span>
            </span>
          );
        })}
      </div>
    );
  }

  function CaseTable(props) {
    var rows = props.rows || [];
    return (
      <table className="min-w-full text-xs">
        <thead className="bg-gray-50 text-gray-500">
          <tr>
            <th className="px-3 py-2 text-left">Baby</th>
            <th className="px-2 py-2 text-right">Visits</th>
            <th className="px-2 py-2 text-left">First visit</th>
            <th className="px-2 py-2 text-left">Last visit</th>
            <th className="px-2 py-2 text-right">Birth wt</th>
            <th className="px-2 py-2 text-right">Weight, first → last</th>
            <th className="px-2 py-2 text-left">Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(function (r) {
            var fw = r.first_weight_g,
              lw = r.last_weight_g;
            return (
              <tr key={r.entity_id} className="border-t border-gray-100">
                <td className="px-3 py-1.5 font-mono text-gray-600 whitespace-nowrap">
                  {String(r.name || r.entity_id || '').slice(0, 8)}
                </td>
                <td className="px-2 py-1.5 text-right tabular-nums">
                  {r.num_visits}
                </td>
                <td className="px-2 py-1.5 whitespace-nowrap">
                  {r.first_visit ? dateLbl(r.first_visit) : '—'}
                </td>
                <td className="px-2 py-1.5 whitespace-nowrap">
                  {r.last_visit ? dateLbl(r.last_visit) : '—'}
                </td>
                <td className="px-2 py-1.5 text-right tabular-nums whitespace-nowrap">
                  {r.birth_weight_g ? nCount(r.birth_weight_g) + ' g' : '—'}
                </td>
                <td className="px-2 py-1.5 text-right tabular-nums whitespace-nowrap">
                  {fw && lw ? nCount(fw) + ' → ' + nCount(lw) + ' g' : '—'}
                </td>
                <td className="px-2 py-1.5 text-gray-500 whitespace-nowrap">
                  {String(r.last_kmc_status || '—').replace(/_/g, ' ')}
                </td>
              </tr>
            );
          })}
          {!rows.length ? (
            <tr>
              <td className="px-3 py-4 text-center text-gray-400" colSpan={7}>
                No cases for this worker in the report.
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
    );
  }

  function AllIndicators() {
    return (
      <details
        className="bg-white border border-gray-200 rounded-xl"
        open={allOpen}
        onToggle={function (ev) {
          var isOpen = !!(ev.target && ev.target.open);
          setAllOpen(isOpen);
          if (isOpen) loadExplain();
        }}
      >
        <summary className="px-4 py-3 text-sm font-semibold text-gray-700 cursor-pointer flex items-center justify-between">
          <span>All programme indicators · {scopeName}</span>
          <span className="text-xs font-normal text-gray-400">
            value, n and band for the full C-series · click a row for its
            definition
          </span>
        </summary>
        <div className="px-4 py-2 border-t border-gray-100 flex items-center gap-3 flex-wrap text-xs">
          <span className="text-gray-500">
            Definitions, in English and as the SQL that computes them:
          </span>
          <a
            className="text-indigo-600 hover:underline"
            href={explainUrl('md', true)}
          >
            Download Markdown
          </a>
          <a
            className="text-indigo-600 hover:underline"
            href={explainUrl('sql', true)}
          >
            Download SQL
          </a>
          <a
            className="text-indigo-600 hover:underline"
            href={explainUrl('json', true)}
          >
            Download JSON
          </a>
          <a
            className="text-indigo-600 hover:underline"
            href={explainUrl('md', false)}
            target="_blank"
            rel="noopener"
          >
            Open as text
          </a>
          <span className="text-gray-400">
            Same reader as the labs MCP tool semantic_registry_explain.
          </span>
        </div>
        <div className="overflow-x-auto border-t border-gray-100">
          <IndicatorTable ind={scopeInd} withDefinitions={true} />
        </div>
      </details>
    );
  }

  var headline = selLLO ? selLLO : 'Kangaroo Mother Care programme';
  var meta = P.meta || {};
  function subline() {
    if (!selLLO)
      return (
        <span>
          <b className="font-semibold text-gray-900">{nCount(meta.cases)}</b>{' '}
          babies ·{' '}
          <b className="font-semibold text-gray-900">{nCount(meta.visits)}</b>{' '}
          visits · <b className="font-semibold text-gray-900">{byLLO.length}</b>{' '}
          organisations ·{' '}
          <b className="font-semibold text-gray-900">
            {meta.opportunities || byOpp.length}
          </b>{' '}
          opportunities · figures as of {dateLbl(asOf)}
        </span>
      );
    var lv = lastVisitByLLO[selLLO];
    return (
      <span>
        <b className="font-semibold text-gray-900">
          {nCount(entryOf(scopeLLO && scopeLLO.ind, 'C01').n)}
        </b>{' '}
        babies ·{' '}
        <b className="font-semibold text-gray-900">
          {flwCountByLLO[selLLO] || 0}
        </b>{' '}
        workers ·{' '}
        <b className="font-semibold text-gray-900">
          {scopeLLO ? scopeLLO.opps.length : 0}
        </b>{' '}
        opportunities{lv ? ' · last visit ' + dateLbl(lv) : ''} · figures as of{' '}
        {dateLbl(asOf)}
      </span>
    );
  }

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center gap-2 text-sm">
        {crumb.map(function (c, i) {
          var last = i === crumb.length - 1;
          return (
            <span key={i} className="flex items-center gap-2">
              <button
                onClick={function () {
                  if (i === 0) {
                    setSelLLO(null);
                    setOppFilter(null);
                    setSelOpp(null);
                    setSelFLW(null);
                  }
                }}
                className={
                  last
                    ? 'font-semibold text-gray-900'
                    : 'text-indigo-600 hover:underline'
                }
              >
                {c}
              </button>
              {!last && <span className="text-gray-300">›</span>}
            </span>
          );
        })}
      </div>

      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{headline}</h1>
          <p className="text-sm text-gray-600 mt-1">{subline()}</p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <span className="inline-flex items-center border border-gray-200 bg-white rounded-lg px-3 py-1.5 text-sm font-semibold text-gray-800 whitespace-nowrap">
            Report of {dateLbl(asOf)}
          </span>
          {snapshot ? (
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-green-100 text-green-800">
              <span className="w-1.5 h-1.5 rounded-full bg-green-600" />
              Final report
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-gray-100 text-gray-600">
              <span className="w-1.5 h-1.5 rounded-full bg-gray-400" />
              {live.status === 'ready' ? 'Current period' : 'Computing…'}
            </span>
          )}
          {!snapshot && view && view.complete && (
            <button
              onClick={saveRun}
              disabled={live.status !== 'ready'}
              className={
                'px-3 py-1.5 rounded-lg text-sm border ' +
                (live.status !== 'ready'
                  ? 'border-gray-200 bg-gray-50 text-gray-400 cursor-not-allowed'
                  : 'border-indigo-300 bg-indigo-50 text-indigo-700 hover:bg-indigo-100')
              }
              title={
                live.status !== 'ready'
                  ? 'Waiting for the figures to load'
                  : 'Save this report — its figures become final'
              }
            >
              Save this report
            </button>
          )}
        </div>
      </div>

      {!snapshot && live.status !== 'ready' && (
        <div
          className={
            'px-4 py-3 text-sm border rounded-xl ' +
            (live.status === 'error'
              ? 'bg-red-50 text-red-900 border-red-200'
              : 'bg-slate-50 text-slate-700 border-slate-200')
          }
        >
          {live.status === 'error' ? (
            <span>
              <span className="font-medium">
                Indicators could not be computed.
              </span>{' '}
              {live.error}
            </span>
          ) : (
            <span>
              <span className="font-medium">Computing indicators…</span> one
              pass over the whole cohort, usually under a minute. Figures below
              stay blank until it returns.
            </span>
          )}
        </div>
      )}

      {!snapshot &&
        live.status === 'ready' &&
        live.cache &&
        (live.cache.cold_cache || live.cache.partial_cache) && (
          <div className="px-4 py-3 text-sm bg-amber-50 text-amber-900 border border-amber-200 rounded-xl">
            <span className="font-medium">
              {live.cache.cold_cache
                ? 'Every metric is blank because nothing is cached — not because the programme has no data.'
                : 'These totals cover only part of the cohort.'}
            </span>{' '}
            {live.cache.cold_cache_hint}
          </div>
        )}

      {selLLO ? <OppChips /> : null}

      <Tiles />

      {selLLO ? <FLWTable /> : <OrgTable />}

      <ChartsRow />

      <AllIndicators />

      {runIsSynthetic ? (
        <p className="text-xs text-gray-400 max-w-3xl">
          Prepared on a synthetic copy of the programme data. Personal
          identifiers — names, phone numbers, addresses, GPS and free text — are
          never reproduced, so any measure derived from them is shown as
          unavailable rather than as zero.
        </p>
      ) : null}
    </div>
  );
}
