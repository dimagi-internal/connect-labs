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

  // Declared in the workbook but not computable from what these programmes collect today.
  var NOT_COMPUTABLE = [
    {
      id: 'C03',
      name: 'Cases started per month',
      why: 'now computed \u2014 see the Trend tab, which cohorts on each baby\u2019s actual registration date',
    },
    { id: 'C04', name: 'Visits per month', why: 'available on the Trend tab' },
    {
      id: 'C18',
      name: 'KMC completion rate',
      why: 'the discharge data is now present in these rows; what is missing is the DEFINITION \u2014 the workbook leaves the completion gate TBD, so there is no rule yet for when a baby counts as completed',
    },
    {
      id: 'C22',
      name: '% EBF at completion',
      why: 'depends on C18, so it is blocked on the same missing definition rather than on missing data',
    },
    { id: 'C25', name: '% thin', why: 'needs per-reading flag_thin' },
    {
      id: 'C26',
      name: '% inconsistent',
      why: 'needs per-reading flag_inconsistent',
    },
    {
      id: 'C27',
      name: '% impossible',
      why: 'needs per-reading flag_impossible',
    },
    {
      id: 'C29',
      name: '% enrollment_weight_credible',
      why: 'needs the credibility rule from Targets & settings',
    },
    { id: 'C30', name: '% expected dip', why: 'depends on C29' },
    {
      id: 'C32',
      name: 'GPS mismatch rate',
      why: 'needs visit-pair GPS comparison',
    },
    {
      id: 'C33',
      name: 'Repeat vitals rate',
      why: 'needs visit-pair vitals comparison',
    },
  ];

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

  var sNScope = React.useState('programme');
  var nScope = sNScope[0],
    setNScope = sNScope[1];

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
  var s5 = React.useState('indicators');
  var tab = s5[0],
    setTab = s5[1];
  var s4 = React.useState(null);
  var selFLW = s4[0],
    setSelFLW = s4[1];

  // ── Monthly trend ─────────────────────────────────────────────────────────
  // Precomputed per drill scope by the builder, so the drill works with no live
  // pipeline behind it. Each point carries the graded indicators, the cohort
  // size, the visit count for the month the visits HAPPENED in, and the
  // credible-recorder pool; the trend tab reads it in the shape below.
  var monthly = React.useMemo(
    function () {
      var all = P.monthly || [];
      var key = selFLW
        ? 'flw:' + selFLW
        : selOpp
        ? 'opp:' + selOpp
        : selLLO
        ? 'llo:' + selLLO
        : 'all';
      var series = (P.monthlyByScope && P.monthlyByScope[key]) || all;
      return series.map(function (m) {
        var ind = m.ind || {};
        var count = function (id) {
          var e = ind[id];
          return e && e.value !== null && e.value !== undefined
            ? Number(e.value)
            : 0;
        };
        return {
          month: m.month,
          started: count('C02'),
          registered: count('C01'),
          visits: m.visits || 0,
          c09: ind['C09'],
          c13: ind['C13'],
          c15: ind['C15'],
          mortality: (m.pooled && m.pooled['C14']) || null,
        };
      });
    },
    [payload, selLLO, selOpp, selFLW],
  );

  // ── Weekly trend ───────────────────────────────────────────────────────────
  // Two halves. ACTIVITY (visits, registrations) by week comes off this payload,
  // per drill scope, cut at the run's as-of date. INDICATORS over time are the
  // series of SAVED RUNS: each is computed as of its own period end by the same
  // builder, so the line is one point per saved report -- a weekly report, saved
  // weekly, is the time series. Nothing here re-grades anything.
  var trendKey = selOpp ? 'opp:' + selOpp : selLLO ? 'llo:' + selLLO : 'all';
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
        if (selOpp) {
          var o = (st.byOpp || []).filter(function (x) {
            return String(x.opp) === String(selOpp);
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
    [history, payload, selLLO, selOpp],
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

  function IndicatorTable(props) {
    var rows = props.rows,
      ind = props.ind,
      onPick = props.onPick;
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
            return (
              <tr
                key={i.id}
                className={
                  'border-t border-gray-100 ' +
                  (onPick ? 'cursor-pointer hover:bg-indigo-50' : '')
                }
                onClick={
                  onPick
                    ? function () {
                        onPick(i.id);
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
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    );
  }

  var crumb = ['Programme'];
  if (selLLO) crumb.push(selLLO);
  if (selOpp) crumb.push(oppLabel(selOpp));

  // ── Trend charts ──────────────────────────────────────────────────────────
  // Time on the X axis. A month-per-row table is a ledger, not a trend — the shape
  // of a programme (quality climbing, mortality falling, follow-up tightening) is
  // only legible as a line.
  var CHART_W = 720,
    CHART_H = 150,
    PAD_L = 44,
    PAD_R = 14,
    PAD_T = 12,
    PAD_B = 26;

  function Axis(props) {
    var months = props.months;
    var innerW = CHART_W - PAD_L - PAD_R;
    var step = months.length > 1 ? innerW / (months.length - 1) : 0;
    // With ~16 months, label every other one so they don't collide.
    var every = months.length > 10 ? 2 : 1;
    return (
      <g>
        <line
          x1={PAD_L}
          y1={CHART_H - PAD_B}
          x2={CHART_W - PAD_R}
          y2={CHART_H - PAD_B}
          stroke="#e5e7eb"
        />
        {months.map(function (m, i) {
          if (i % every !== 0) return null;
          return (
            <text
              key={m}
              x={PAD_L + i * step}
              y={CHART_H - PAD_B + 14}
              fontSize="9"
              fill="#9ca3af"
              textAnchor="middle"
            >
              {m.slice(2)}
            </text>
          );
        })}
      </g>
    );
  }

  function LineChart(props) {
    var months = props.months,
      values = props.values,
      color = props.color,
      pct = props.pct,
      target = props.target;
    var innerW = CHART_W - PAD_L - PAD_R;
    var innerH = CHART_H - PAD_T - PAD_B;
    var step = months.length > 1 ? innerW / (months.length - 1) : 0;
    var real = values.filter(function (v) {
      return typeof v === 'number';
    });
    if (!real.length) {
      return (
        <div className="text-xs text-gray-400 py-8 text-center">
          no point has enough data to score
        </div>
      );
    }
    var hi = Math.max.apply(null, real);
    var lo = Math.min.apply(null, real);
    if (target !== undefined && target !== null) {
      hi = Math.max(hi, target);
      lo = Math.min(lo, target);
    }
    if (pct) {
      lo = 0;
      hi = Math.max(hi, 0.01);
    } else {
      var padv = (hi - lo) * 0.15 || 1;
      hi = hi + padv;
      lo = Math.max(0, lo - padv);
    }
    var span = hi - lo || 1;
    function y(v) {
      return PAD_T + innerH - ((v - lo) / span) * innerH;
    }
    function x(i) {
      return PAD_L + i * step;
    }
    // Break the line wherever a month could not be scored, rather than drawing
    // through the gap and implying data we do not have.
    var segments = [];
    var cur = [];
    values.forEach(function (v, i) {
      if (typeof v === 'number') cur.push([x(i), y(v)]);
      else if (cur.length) {
        segments.push(cur);
        cur = [];
      }
    });
    if (cur.length) segments.push(cur);
    var ticks = [lo, lo + span / 2, hi];
    return (
      <svg
        viewBox={'0 0 ' + CHART_W + ' ' + CHART_H}
        className="w-full"
        style={{ height: 'auto' }}
      >
        {ticks.map(function (t, i) {
          return (
            <g key={i}>
              <line
                x1={PAD_L}
                y1={y(t)}
                x2={CHART_W - PAD_R}
                y2={y(t)}
                stroke="#f3f4f6"
              />
              <text
                x={PAD_L - 6}
                y={y(t) + 3}
                fontSize="9"
                fill="#9ca3af"
                textAnchor="end"
              >
                {pct ? Math.round(t * 100) + '%' : Math.round(t * 10) / 10}
              </text>
            </g>
          );
        })}
        {target !== undefined && target !== null && (
          <g>
            <line
              x1={PAD_L}
              y1={y(target)}
              x2={CHART_W - PAD_R}
              y2={y(target)}
              stroke="#94a3b8"
              strokeDasharray="4 3"
            />
            <text
              x={CHART_W - PAD_R}
              y={y(target) - 4}
              fontSize="9"
              fill="#94a3b8"
              textAnchor="end"
            >
              target
            </text>
          </g>
        )}
        <Axis months={months} />
        {segments.map(function (seg, i) {
          return (
            <polyline
              key={i}
              fill="none"
              stroke={color}
              strokeWidth="2"
              strokeLinejoin="round"
              points={seg
                .map(function (p) {
                  return p[0] + ',' + p[1];
                })
                .join(' ')}
            />
          );
        })}
        {values.map(function (v, i) {
          if (typeof v !== 'number') return null;
          return <circle key={i} cx={x(i)} cy={y(v)} r="2.5" fill={color} />;
        })}
      </svg>
    );
  }

  function VolumeChart(props) {
    var months = props.months,
      started = props.started,
      visits = props.visits;
    var innerW = CHART_W - PAD_L - PAD_R;
    var innerH = CHART_H - PAD_T - PAD_B;
    var step = months.length ? innerW / months.length : 0;
    var maxS = Math.max.apply(null, started.concat([1]));
    var maxV = Math.max.apply(null, visits.concat([1]));
    return (
      <svg
        viewBox={'0 0 ' + CHART_W + ' ' + CHART_H}
        className="w-full"
        style={{ height: 'auto' }}
      >
        <Axis months={months} />
        {started.map(function (v, i) {
          var h = (v / maxS) * innerH;
          return (
            <rect
              key={i}
              x={PAD_L + i * step + step * 0.2}
              y={PAD_T + innerH - h}
              width={step * 0.6}
              height={h}
              fill="#6366f1"
              opacity="0.85"
            />
          );
        })}
        <polyline
          fill="none"
          stroke="#0ea5e9"
          strokeWidth="2"
          points={visits
            .map(function (v, i) {
              return (
                PAD_L +
                i * step +
                step / 2 +
                ',' +
                (PAD_T + innerH - (v / maxV) * innerH)
              );
            })
            .join(' ')}
        />
        <text x={PAD_L} y={PAD_T - 2} fontSize="9" fill="#6366f1">
          bars = babies registered (max {maxS})
        </text>
        <text
          x={CHART_W - PAD_R}
          y={PAD_T - 2}
          fontSize="9"
          fill="#0ea5e9"
          textAnchor="end"
        >
          line = visits (max {maxV})
        </text>
      </svg>
    );
  }

  function TrendView() {
    var scopeLabel = selOpp ? oppLabel(selOpp) : selLLO ? selLLO : 'Programme';
    var dates = historyPoints.map(function (p) {
      return p.date;
    });
    function series(id) {
      return historyPoints.map(function (p) {
        var e = p.ind && p.ind[id];
        // An unscored point (n below the minimum denominator) is a GAP, not a zero.
        if (!e || e.value === null || e.value === undefined) return null;
        if (e.band === 'insufficient' || e.band === 'notcredible') return null;
        return e.value;
      });
    }
    var charts = [
      {
        id: 'C09',
        title: 'C09 \u00b7 % weight data sufficient',
        values: series('C09'),
        color: '#0d9488',
        pct: true,
        target: 0.6,
      },
      {
        id: 'C14',
        title: 'C14 \u00b7 Mortality',
        note: selLLO || selOpp ? '' : 'pooled over the credible recorders',
        values: series('C14'),
        color: '#dc2626',
        pct: true,
        target: 0.04,
      },
      {
        id: 'C15',
        title: 'C15 \u00b7 Loss to follow-up by day 28',
        values: series('C15'),
        color: '#d97706',
        pct: true,
        target: 0.1,
      },
      {
        id: 'C13',
        title: 'C13 \u00b7 Mean early growth rate',
        note: 'g/kg/day \u2014 target 15',
        values: series('C13'),
        color: '#4f46e5',
        pct: false,
        target: 15,
      },
    ];
    var weeks = weekly.map(function (w) {
      return w.week;
    });
    var savedCount = historyPoints.filter(function (p) {
      return !p.current;
    }).length;
    return (
      <div className="space-y-5">
        <div className="bg-white border border-gray-200 rounded-xl p-4">
          <div className="flex items-baseline justify-between gap-4 flex-wrap">
            <div className="font-medium text-gray-900">
              Weekly trend
              <span className="ml-2 text-sm font-normal text-gray-500">
                {scopeLabel}
              </span>
            </div>
            {(selLLO || selOpp) && (
              <button
                className="text-xs text-indigo-600 hover:underline"
                onClick={function () {
                  setSelLLO(null);
                  setSelOpp(null);
                  setSelFLW(null);
                }}
              >
                Programme-wide
              </button>
            )}
          </div>
          <p className="text-xs text-gray-500 mt-1">
            Activity is counted in the week it happened. Each indicator point is
            the figure as of a saved weekly report, computed the same way as the
            headline; a gap is a week with too few cases to score, not a zero.
            Dashed line = target.
          </p>
        </div>

        <div className="bg-white border border-gray-200 rounded-xl p-4">
          <div className="text-sm font-medium text-gray-900 mb-1">
            Registrations &amp; visits by week
            <span className="ml-2 text-xs font-normal text-gray-400">
              last {weeks.length} weeks
              {P.meta && P.meta.as_of ? ' to ' + P.meta.as_of : ''}
            </span>
          </div>
          {weeks.length ? (
            <VolumeChart
              months={weeks}
              started={weekly.map(function (w) {
                return w.registered;
              })}
              visits={weekly.map(function (w) {
                return w.visits;
              })}
            />
          ) : (
            <div className="text-xs text-gray-400 py-8 text-center">
              No dated visits in this scope.
            </div>
          )}
        </div>

        {historyPoints.length < 2 ? (
          <div className="bg-white border border-gray-200 rounded-xl p-4 text-sm text-gray-600">
            <div className="font-medium text-gray-900 mb-1">
              Indicators over time
            </div>
            {savedCount
              ? 'One saved report so far. '
              : 'No saved reports yet. '}
            Each saved report adds a point as of its date; save this report
            weekly and the indicator lines build from here.
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {charts.map(function (c) {
              return (
                <div
                  key={c.id}
                  className="bg-white border border-gray-200 rounded-xl p-4"
                >
                  <div className="text-sm font-medium text-gray-900">
                    {c.title}
                  </div>
                  <div className="text-xs text-gray-400 mb-1">
                    {c.note || '\u00a0'}
                  </div>
                  <LineChart
                    months={dates}
                    values={c.values}
                    color={c.color}
                    pct={c.pct}
                    target={c.target}
                  />
                </div>
              );
            })}
          </div>
        )}

        {historyPoints.length > 0 && (
          <details className="bg-white border border-gray-200 rounded-xl">
            <summary className="px-4 py-3 text-sm font-medium text-gray-900 cursor-pointer">
              Weekly figures (table)
            </summary>
            <div className="overflow-x-auto border-t border-gray-100">
              <table className="min-w-full text-sm">
                <thead className="bg-gray-50 text-gray-500">
                  <tr>
                    <th className="px-3 py-2 text-left">As of</th>
                    <th className="px-3 py-2 text-right">Cases</th>
                    {['C09', 'C13', 'C14', 'C15'].map(function (id) {
                      var ind = C_LIST.filter(function (i) {
                        return i.id === id;
                      })[0];
                      return (
                        <th key={id} className="px-3 py-2 text-right">
                          {ind ? ind.title : id}
                          <div className="text-[10px] font-normal text-gray-400">
                            {id}
                          </div>
                        </th>
                      );
                    })}
                  </tr>
                </thead>
                <tbody>
                  {historyPoints.map(function (p) {
                    return (
                      <tr key={p.date} className="border-t border-gray-100">
                        <td className="px-3 py-2 font-medium text-gray-900">
                          {p.date}
                          {p.current ? (
                            <span className="ml-2 text-[10px] text-gray-400">
                              this report
                            </span>
                          ) : null}
                        </td>
                        <td className="px-3 py-2 text-right">
                          {p.n ? nCount(p.n) : '\u2014'}
                        </td>
                        {['C09', 'C13', 'C14', 'C15'].map(function (id) {
                          var ind = C_LIST.filter(function (i) {
                            return i.id === id;
                          })[0];
                          var e = p.ind && p.ind[id];
                          return (
                            <td key={id} className="px-3 py-2 text-right">
                              {!e ||
                              e.value === null ||
                              e.value === undefined ? (
                                <span className="text-gray-300">&mdash;</span>
                              ) : e.band === 'insufficient' ? (
                                <span className="text-gray-400">
                                  n&lt;{(ind && ind.min_denominator) || MIN_DEN}
                                </span>
                              ) : (
                                fmt(ind, e)
                              )}
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </details>
        )}

        {monthly.length > 0 && (
          <details className="bg-white border border-gray-200 rounded-xl">
            <summary className="px-4 py-3 text-sm font-medium text-gray-900 cursor-pointer">
              Intake cohorts by month (table)
            </summary>
            <div className="px-4 pt-2 text-xs text-gray-500">
              Babies grouped by the month they were registered; each row
              describes that cohort as of this report. Recent cohorts are still
              maturing into the 28- and 42-day gates, so their figures are not
              yet comparable.
            </div>
            <div className="overflow-x-auto border-t border-gray-100 mt-2">
              <table className="min-w-full text-sm">
                <thead className="bg-gray-50 text-gray-500">
                  <tr>
                    <th className="px-3 py-2 text-left">Month</th>
                    <th className="px-3 py-2 text-right">Registered</th>
                    <th className="px-3 py-2 text-right">Started</th>
                    <th className="px-3 py-2 text-right">Visits</th>
                    <th className="px-3 py-2 text-right">
                      % weight data sufficient
                      <div className="text-[10px] font-normal text-gray-400">
                        C09
                      </div>
                    </th>
                    <th className="px-3 py-2 text-right">
                      Mean early growth rate
                      <div className="text-[10px] font-normal text-gray-400">
                        C13
                      </div>
                    </th>
                    <th className="px-3 py-2 text-right">
                      Mortality
                      <div className="text-[10px] font-normal text-gray-400">
                        C14
                      </div>
                    </th>
                    <th className="px-3 py-2 text-right">
                      Loss to follow-up
                      <div className="text-[10px] font-normal text-gray-400">
                        C15
                      </div>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {monthly.map(function (m) {
                    function cell(e, id) {
                      var ind = C_LIST.filter(function (i) {
                        return i.id === id;
                      })[0];
                      if (!e || e.value === null)
                        return <span className="text-gray-300">&mdash;</span>;
                      if (e.band === 'insufficient')
                        return (
                          <span className="text-gray-400">n&lt;{MIN_DEN}</span>
                        );
                      return fmt(ind, e);
                    }
                    return (
                      <tr key={m.month} className="border-t border-gray-100">
                        <td className="px-3 py-2 font-medium text-gray-900">
                          {m.month}
                        </td>
                        <td className="px-3 py-2 text-right">{m.registered}</td>
                        <td className="px-3 py-2 text-right">{m.started}</td>
                        <td className="px-3 py-2 text-right">{m.visits}</td>
                        <td className="px-3 py-2 text-right">
                          {cell(m.c09, 'C09')}
                        </td>
                        <td className="px-3 py-2 text-right">
                          {cell(m.c13, 'C13')}
                        </td>
                        <td className="px-3 py-2 text-right">
                          {cell(m.mortality, 'C14')}
                        </td>
                        <td className="px-3 py-2 text-right">
                          {cell(m.c15, 'C15')}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </details>
        )}
      </div>
    );
  }

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

  return (
    <div className="p-6 space-y-5">
      {snapshot && (
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
          <span className="font-medium">
            Reporting period to{' '}
            {(P.meta && P.meta.as_of) ||
              (view.asOf ? String(view.asOf).slice(0, 10) : '')}
            .
          </span>{' '}
          {nCount((snapshot.meta || {}).cases)} cases and{' '}
          {nCount((snapshot.meta || {}).visits)} visits across{' '}
          {(snapshot.meta || {}).opportunities} opportunities. Figures are final
          for this period; individual case records are available in the current
          reporting period.
        </div>
      )}

      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">KMC Indicators</h1>
          {/* "evaluated live" was true when this browser computed the
              indicators. It no longer does, and on a snapshot run the claim sat
              directly under a banner saying the figures cannot move. */}
          <p className="text-sm text-gray-500 mt-1">
            Kangaroo Mother Care programme performance across all participating
            organisations. Click any row to drill from Programme to
            organisation, opportunity and individual cases.
          </p>
        </div>
        {!snapshot && view && view.complete && (
          <div className="shrink-0 flex items-center gap-2">
            <button
              onClick={saveRun}
              disabled={live.status !== 'ready'}
              className={
                'px-3 py-2 rounded-lg text-sm border ' +
                (live.status !== 'ready'
                  ? 'border-gray-200 bg-gray-50 text-gray-400 cursor-not-allowed'
                  : 'border-indigo-300 bg-indigo-50 text-indigo-700 hover:bg-indigo-100')
              }
              title={
                live.status !== 'ready'
                  ? 'Waiting for the figures to load'
                  : 'Save this run — its figures become final'
              }
            >
              {live.status === 'ready'
                ? 'Save this run'
                : 'Save this run (loading…)'}
            </button>
          </div>
        )}
      </div>

      <div className="flex items-center gap-1 border-b border-gray-200">
        {[
          ['indicators', 'Indicators'],
          ['trends', 'Weekly trend'],
        ].map(function (t) {
          var on = tab === t[0];
          return (
            <button
              key={t[0]}
              onClick={function () {
                setTab(t[0]);
              }}
              className={
                'px-4 py-2 text-sm -mb-px border-b-2 ' +
                (on
                  ? 'border-indigo-600 text-indigo-700 font-medium'
                  : 'border-transparent text-gray-500 hover:text-gray-700')
              }
            >
              {t[1]}
            </button>
          );
        })}
      </div>

      {tab === 'trends' && <TrendView />}

      {tab === 'indicators' && (
        <>
          {/* The C-series is fetched now, not computed in this browser, and that
              introduced a state the old engine never had: in-flight. While the
              query runs every figure is an em-dash, which is indistinguishable
              from a programme with no data -- and the query takes ~30s over 8,700
              cases, so that is not a blink. An error was worse: it rendered the
              same dashes and said nothing at all.

              Two cache lies get the same treatment the N-series already gives
              them. COLD: every count is zero, reading as a programme with no
              babies. PARTIAL: a real number over only the cached opportunities,
              entirely credible and understated. Neither is visible in the
              figures themselves. */}
          {!snapshot && live.status !== 'ready' && (
            <div
              className={
                'px-4 py-3 text-sm border rounded ' +
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
                  pass over the whole cohort, usually under a minute. Values
                  below stay blank until it returns.
                </span>
              )}
            </div>
          )}

          {!snapshot &&
            live.status === 'ready' &&
            live.cache &&
            (live.cache.cold_cache || live.cache.partial_cache) && (
              <div className="px-4 py-3 text-sm bg-amber-50 text-amber-900 border border-amber-200 rounded">
                <span className="font-medium">
                  {live.cache.cold_cache
                    ? 'Every metric is blank because nothing is cached \u2014 not because the programme has no data.'
                    : 'These totals cover only part of the cohort.'}
                </span>{' '}
                {live.cache.cold_cache_hint}
              </div>
            )}

          <div className="flex items-center gap-2 text-sm">
            {crumb.map(function (c, i) {
              var last = i === crumb.length - 1;
              return (
                <span key={i} className="flex items-center gap-2">
                  <button
                    onClick={function () {
                      if (i === 0) {
                        setSelLLO(null);
                        setSelOpp(null);
                        setSelInd(null);
                      }
                      if (i === 1) {
                        setSelOpp(null);
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

          {!selLLO && (
            <div className="space-y-5">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <div className="bg-white border border-gray-200 rounded-xl p-4">
                  <div className="text-xs text-gray-500">
                    LLOs with a red indicator
                  </div>
                  <div className="text-2xl font-semibold mt-1">
                    {llosRed}{' '}
                    <span className="text-base text-gray-400">
                      of {byLLO.length}
                    </span>
                  </div>
                  <div className="text-xs text-gray-400 mt-1">
                    Organisations with at least one indicator in the red band
                  </div>
                </div>
                <div className="bg-white border border-gray-200 rounded-xl p-4">
                  <div className="text-xs text-gray-500">Total started</div>
                  <div className="text-2xl font-semibold mt-1">
                    {fmt(indOf('C02'), entryOf(programInd, 'C02'))}
                  </div>
                  <div className="text-xs text-gray-400 mt-1">
                    Started cases (C02) against the 25,000 target by Q1-2027
                  </div>
                </div>
                <div className="bg-white border border-gray-200 rounded-xl p-4">
                  <div className="text-xs text-gray-500">
                    % weight data sufficient
                  </div>
                  <div className="text-2xl font-semibold mt-1">
                    {fmt(indOf('C09'), entryOf(programInd, 'C09'))}
                  </div>
                  <div className="text-xs text-gray-400 mt-1">
                    Weight data sufficient (C09), pooled across all cases
                  </div>
                </div>
                <div className="bg-white border border-gray-200 rounded-xl p-4">
                  <div className="text-xs text-gray-500">Mortality</div>
                  <div className="text-2xl font-semibold mt-1">
                    {mortalityCredible.ind
                      ? fmt(indOf('C14'), mortalityCredible.ind)
                      : '\u2014'}
                  </div>
                  <div className="text-xs text-gray-400 mt-1">
                    Mortality (C14), two-sided ·{' '}
                    {mortalityCredible.llos.length
                      ? mortalityCredible.llos.join(' + ') +
                        ' only (' +
                        mortalityCredible.llos.length +
                        ' of ' +
                        mortalityCredible.of +
                        ' LLOs record deaths credibly)'
                      : 'no credible recorder'}
                  </div>
                </div>
              </div>

              {SC && (
                <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
                  <div className="px-4 py-3 border-b border-gray-100 font-medium text-gray-900">
                    Programme scorecard{' '}
                    <span className="text-xs font-normal text-gray-400 ml-2">
                      15 headline metrics by organisation
                      {P.meta && P.meta.as_of
                        ? ' \u00b7 as of ' + P.meta.as_of
                        : ''}
                    </span>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="min-w-full text-sm">
                      <thead className="bg-gray-50 text-gray-500">
                        <tr>
                          <th className="px-3 py-2 text-left">LLO</th>
                          {SCORECARD.map(function (c, i) {
                            return (
                              <th
                                key={i}
                                className="px-2 py-2 text-right whitespace-nowrap"
                                title={c.title}
                              >
                                {c.label}
                                <div className="text-[10px] font-normal text-gray-400">
                                  {c.id}
                                </div>
                              </th>
                            );
                          })}
                        </tr>
                      </thead>
                      <tbody>
                        {(SC.byLLO || []).map(function (r) {
                          return (
                            <tr
                              key={r.llo}
                              className="border-t border-gray-100"
                            >
                              <td className="px-3 py-2 font-medium text-gray-900">
                                {r.llo}
                              </td>
                              {SCORECARD.map(function (c, i) {
                                return (
                                  <td key={i} className="px-2 py-2 text-right">
                                    {scoreCell(c, r.ind)}
                                  </td>
                                );
                              })}
                            </tr>
                          );
                        })}
                        <tr className="border-t-2 border-gray-200 bg-gray-50 font-semibold">
                          <td className="px-3 py-2">Programme</td>
                          {SCORECARD.map(function (c, i) {
                            return (
                              <td key={i} className="px-2 py-2 text-right">
                                {scoreCell(c, SC.programme)}
                              </td>
                            );
                          })}
                        </tr>
                      </tbody>
                    </table>
                  </div>
                  <div className="px-4 py-2 text-xs text-gray-400 border-t border-gray-100">
                    %slow + %healthy + %fast + %incompl = 100 per row. n&lt;20 =
                    below the minimum denominator. Hover a column for its
                    definition.
                  </div>
                </div>
              )}

              <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
                <div className="px-4 py-3 border-b border-gray-100 font-medium text-gray-900">
                  LLOs{' '}
                  <span className="text-xs font-normal text-gray-400 ml-2">
                    click to drill into an LLO's opportunities
                  </span>
                </div>
                <table className="min-w-full text-sm">
                  <thead className="bg-gray-50 text-gray-500">
                    <tr>
                      <th className="px-3 py-2 text-left">LLO</th>
                      <th className="px-3 py-2 text-right">Opps</th>
                      <th className="px-3 py-2 text-right">Cases</th>
                      <th className="px-3 py-2 text-right">Started</th>
                      <th className="px-3 py-2 text-right">
                        % weight data sufficient
                        <div className="text-[10px] font-normal text-gray-400">
                          C09
                        </div>
                      </th>
                      <th className="px-3 py-2 text-right">
                        Mean early growth rate
                        <div className="text-[10px] font-normal text-gray-400">
                          C13 · g/kg/day
                        </div>
                      </th>
                      <th className="px-3 py-2 text-right">
                        Mortality
                        <div className="text-[10px] font-normal text-gray-400">
                          C14
                        </div>
                      </th>
                      <th className="px-3 py-2 text-right">
                        % enrolled within 3 days
                        <div className="text-[10px] font-normal text-gray-400">
                          C16
                        </div>
                      </th>
                      <th className="px-3 py-2 text-right">Last visit</th>
                      <th className="px-3 py-2 text-right">Red</th>
                      <th className="px-3 py-2 text-right">Yellow</th>
                    </tr>
                  </thead>
                  <tbody>
                    {byLLO.map(function (l) {
                      return (
                        <tr
                          key={l.llo}
                          className="border-t border-gray-100 cursor-pointer hover:bg-indigo-50"
                          onClick={function () {
                            setSelLLO(l.llo);
                          }}
                        >
                          <td className="px-3 py-2 font-medium text-indigo-700">
                            {l.llo}
                          </td>
                          <td className="px-3 py-2 text-right text-gray-500">
                            {l.opps.length}
                          </td>
                          <td className="px-3 py-2 text-right">
                            {caseCount(l)}
                          </td>
                          <td className="px-3 py-2 text-right">
                            {fmt(indOf('C02'), entryOf(l.ind, 'C02'))}
                          </td>
                          <td className="px-3 py-2 text-right">
                            {fmt(indOf('C09'), entryOf(l.ind, 'C09'))}
                          </td>
                          <td className="px-3 py-2 text-right">
                            {fmt(indOf('C13'), entryOf(l.ind, 'C13'))}
                          </td>
                          <td className="px-3 py-2 text-right">
                            {fmt(indOf('C14'), entryOf(l.ind, 'C14'))}
                          </td>
                          <td className="px-3 py-2 text-right">
                            <span title={covTitle(entryOf(l.ind, 'C16'))}>
                              {fmtCov(indOf('C16'), entryOf(l.ind, 'C16'))}
                            </span>
                          </td>
                          <td className="px-3 py-2 text-right text-gray-500 whitespace-nowrap">
                            {lastVisitByLLO[l.llo] || '\u2014'}
                          </td>
                          <td className="px-3 py-2 text-right">
                            {l.reds ? (
                              <span className="px-2 py-0.5 rounded text-xs bg-red-100 text-red-800">
                                {l.reds}
                              </span>
                            ) : (
                              '0'
                            )}
                          </td>
                          <td className="px-3 py-2 text-right text-gray-500">
                            {l.yellows}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
                <div className="px-4 py-3 border-b border-gray-100 font-medium text-gray-900">
                  Programme-wide indicators{' '}
                  <span className="text-xs font-normal text-gray-400 ml-2">
                    all cases pooled
                  </span>
                </div>
                <div className="overflow-x-auto">
                  <IndicatorTable ind={programInd} />
                </div>
              </div>
            </div>
          )}

          {selLLO && !selOpp && (
            <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
              <div className="px-4 py-3 border-b border-gray-100 font-medium text-gray-900">
                {selLLO} — opportunities
                <span className="text-xs font-normal text-gray-400 ml-2">
                  one LLO can have a good opp and a bad one; this is where that
                  shows
                </span>
              </div>
              <table className="min-w-full text-sm">
                <thead className="bg-gray-50 text-gray-500">
                  <tr>
                    <th className="px-3 py-2 text-left">Opportunity</th>
                    <th className="px-3 py-2 text-right">Cases</th>
                    <th className="px-3 py-2 text-right">
                      % weight data computable
                      <div className="text-[10px] font-normal text-gray-400">
                        C07
                      </div>
                    </th>
                    <th className="px-3 py-2 text-right">
                      % weight data sufficient
                      <div className="text-[10px] font-normal text-gray-400">
                        C09
                      </div>
                    </th>
                    <th className="px-3 py-2 text-right">
                      Mean early growth rate
                      <div className="text-[10px] font-normal text-gray-400">
                        C13 · g/kg/day
                      </div>
                    </th>
                    <th className="px-3 py-2 text-right">
                      Mortality
                      <div className="text-[10px] font-normal text-gray-400">
                        C14
                      </div>
                    </th>
                    <th className="px-3 py-2 text-right">
                      Loss to follow-up by day 28
                      <div className="text-[10px] font-normal text-gray-400">
                        C15
                      </div>
                    </th>
                    <th className="px-3 py-2 text-right">Red</th>
                  </tr>
                </thead>
                <tbody>
                  {byLLO
                    .filter(function (l) {
                      return l.llo === selLLO;
                    })[0]
                    .opps.map(function (o) {
                      var reds = Object.keys(o.ind).filter(function (k) {
                        return o.ind[k].band === 'red';
                      }).length;
                      return (
                        <tr
                          key={o.opp}
                          className="border-t border-gray-100 cursor-pointer hover:bg-indigo-50"
                          onClick={function () {
                            setSelOpp(o.opp);
                            setSelFLW(null);
                          }}
                        >
                          <td className="px-3 py-2 font-medium text-indigo-700">
                            {oppLabel(o.opp)}
                          </td>
                          <td className="px-3 py-2 text-right">
                            {caseCount(o)}
                          </td>
                          <td className="px-3 py-2 text-right">
                            {fmt(indOf('C07'), entryOf(o.ind, 'C07'))}
                          </td>
                          <td className="px-3 py-2 text-right">
                            {fmt(indOf('C09'), entryOf(o.ind, 'C09'))}
                          </td>
                          <td className="px-3 py-2 text-right">
                            {fmt(indOf('C13'), entryOf(o.ind, 'C13'))}
                          </td>
                          <td className="px-3 py-2 text-right">
                            {fmt(indOf('C14'), entryOf(o.ind, 'C14'))}
                          </td>
                          <td className="px-3 py-2 text-right">
                            {fmt(indOf('C15'), entryOf(o.ind, 'C15'))}
                          </td>
                          <td className="px-3 py-2 text-right">
                            {reds ? (
                              <span className="px-2 py-0.5 rounded text-xs bg-red-100 text-red-800">
                                {reds}
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
              <div className="px-4 py-3 border-t border-gray-100">
                <div className="text-sm font-medium text-gray-900 mb-2">
                  {selLLO} — all indicators (pooled across its opportunities)
                </div>
                <div className="overflow-x-auto">
                  <IndicatorTable
                    ind={
                      byLLO.filter(function (l) {
                        return l.llo === selLLO;
                      })[0].ind
                    }
                  />
                </div>
              </div>
            </div>
          )}

          {selOpp &&
            (function () {
              var CASE_CAP = 300;
              var caseRows = casesForDrill(selOpp, selFLW);
              return (
                <div className="space-y-5">
                  <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
                    <div className="px-4 py-3 border-b border-gray-100 font-medium text-gray-900">
                      {oppLabel(selOpp)} — indicators
                    </div>
                    <div className="overflow-x-auto">
                      <IndicatorTable
                        ind={
                          byOpp.filter(function (o) {
                            return o.opp === selOpp;
                          })[0].ind
                        }
                      />
                    </div>
                  </div>

                  <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
                    <div className="px-4 py-3 border-b border-gray-100 font-medium text-gray-900">
                      Frontline workers
                      <span className="text-xs font-normal text-gray-400 ml-2">
                        click an FLW for their full indicator set and to filter
                        the case list
                      </span>
                    </div>
                    <div className="overflow-x-auto">
                      <table className="min-w-full text-sm">
                        <thead className="bg-gray-50 text-gray-500">
                          <tr>
                            <th className="px-3 py-2 text-left">FLW</th>
                            <th className="px-3 py-2 text-right">Cases</th>
                            <th className="px-3 py-2 text-right">
                              % weight data sufficient
                              <div className="text-[10px] font-normal text-gray-400">
                                C09
                              </div>
                            </th>
                            <th className="px-3 py-2 text-right">
                              Mean early growth rate
                              <div className="text-[10px] font-normal text-gray-400">
                                C13 · g/kg/day
                              </div>
                            </th>
                            <th className="px-3 py-2 text-right">
                              Loss to follow-up by day 28
                              <div className="text-[10px] font-normal text-gray-400">
                                C15
                              </div>
                            </th>
                            <th className="px-3 py-2 text-right">
                              Mean visits per started case
                              <div className="text-[10px] font-normal text-gray-400">
                                C24
                              </div>
                            </th>
                            <th className="px-3 py-2 text-right">
                              Birth-copy rate
                              <div className="text-[10px] font-normal text-gray-400">
                                C28
                              </div>
                            </th>
                            <th className="px-3 py-2 text-right">
                              Weight rounding rate
                              <div className="text-[10px] font-normal text-gray-400">
                                C31
                              </div>
                            </th>
                            <th
                              className="px-3 py-2 text-right"
                              title="Mean visits per case (scorecard)"
                            >
                              Visits/case
                              <div className="text-[10px] font-normal text-gray-400">
                                N07
                              </div>
                            </th>
                            <th
                              className="px-3 py-2 text-right"
                              title="% impossible weight changes (scorecard)"
                            >
                              %imposs
                              <div className="text-[10px] font-normal text-gray-400">
                                N15
                              </div>
                            </th>
                            <th className="px-3 py-2 text-right">Red</th>
                          </tr>
                        </thead>
                        <tbody>
                          {byFLW
                            .filter(function (f) {
                              return f.opp === selOpp;
                            })
                            .map(function (f) {
                              function cell(id) {
                                var i = C_LIST.filter(function (x) {
                                  return x.id === id;
                                })[0];
                                return fmt(i, f.ind[id]);
                              }
                              return (
                                <tr
                                  key={f.key}
                                  className={
                                    'border-t border-gray-100 cursor-pointer hover:bg-indigo-50 ' +
                                    (selFLW === f.key ? 'bg-indigo-50' : '')
                                  }
                                  onClick={function () {
                                    setSelFLW(selFLW === f.key ? null : f.key);
                                  }}
                                >
                                  <td className="px-3 py-2 font-medium text-indigo-700">
                                    {f.flw}
                                  </td>
                                  <td className="px-3 py-2 text-right">
                                    {caseCount(f)}
                                  </td>
                                  <td className="px-3 py-2 text-right">
                                    {cell('C09')}
                                  </td>
                                  <td className="px-3 py-2 text-right">
                                    {cell('C13')}
                                  </td>
                                  <td className="px-3 py-2 text-right">
                                    {cell('C15')}
                                  </td>
                                  <td className="px-3 py-2 text-right">
                                    {cell('C24')}
                                  </td>
                                  <td className="px-3 py-2 text-right">
                                    {cell('C28')}
                                  </td>
                                  <td className="px-3 py-2 text-right">
                                    {cell('C31')}
                                  </td>
                                  <td className="px-3 py-2 text-right">
                                    {scoreCell(
                                      SCORECARD[5],
                                      (nByFLW[f.key] || {}).ind,
                                    )}
                                  </td>
                                  <td className="px-3 py-2 text-right">
                                    {scoreCell(
                                      SCORECARD[14],
                                      (nByFLW[f.key] || {}).ind,
                                    )}
                                  </td>
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
                    {selFLW &&
                      byFLW.filter(function (f) {
                        return f.key === selFLW;
                      })[0] && (
                        <div className="px-4 py-3 border-t border-gray-100">
                          <div className="text-sm font-medium text-gray-900 mb-2">
                            {
                              byFLW.filter(function (f) {
                                return f.key === selFLW;
                              })[0].flw
                            }{' '}
                            — all indicators
                            <span className="text-xs font-normal text-gray-400 ml-2">
                              n is small per FLW, so most rows will read n&lt;
                              {MIN_DEN}
                            </span>
                          </div>
                          <div className="overflow-x-auto">
                            <IndicatorTable
                              ind={
                                byFLW.filter(function (f) {
                                  return f.key === selFLW;
                                })[0].ind
                              }
                            />
                          </div>
                          {AUDIT_ENABLED &&
                            (function () {
                              var f = byFLW.filter(function (x) {
                                return x.key === selFLW;
                              })[0];
                              var st = auditState[f.key] || {};
                              var agent = AGENT_BY_LLO[f.llo];
                              var unverified =
                                UNVERIFIED_SCALE.indexOf(f.llo) !== -1;
                              return (
                                <div className="mt-3 pt-3 border-t border-gray-100">
                                  <div className="flex items-center gap-3 flex-wrap">
                                    <button
                                      type="button"
                                      disabled={st.status === 'running'}
                                      onClick={function () {
                                        auditWorker(f);
                                      }}
                                      className={
                                        'px-3 py-1.5 rounded text-sm font-medium ' +
                                        (st.status === 'running'
                                          ? 'bg-gray-200 text-gray-500'
                                          : 'bg-indigo-600 text-white hover:bg-indigo-700')
                                      }
                                    >
                                      {st.status === 'running'
                                        ? 'Opening audit…'
                                        : 'Review this worker'}
                                    </button>
                                    <span className="text-xs text-gray-500">
                                      {f.reds
                                        ? f.reds +
                                          ' indicator' +
                                          (f.reds === 1 ? '' : 's') +
                                          ' reading red'
                                        : 'no red indicators'}
                                      {agent
                                        ? ' · ' +
                                          (agent === 'scale_dial_read'
                                            ? 'dial'
                                            : 'digital') +
                                          ' scale reader'
                                        : ' · no scale reader for this LLO'}
                                      {unverified
                                        ? ' (hardware unconfirmed)'
                                        : ''}
                                    </span>
                                  </div>
                                  {st.status === 'created' && (
                                    <div className="mt-2 text-xs text-green-700">
                                      Audit queued for {f.flw}. It appears under
                                      Audits for {oppLabel(f.opp)} once the
                                      sessions finish building.
                                    </div>
                                  )}
                                  {st.status === 'error' && (
                                    <div className="mt-2 text-xs text-red-700">
                                      Could not open the audit: {st.message}
                                    </div>
                                  )}
                                </div>
                              );
                            })()}
                        </div>
                      )}
                  </div>

                  <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
                    <div className="px-4 py-3 border-b border-gray-100 font-medium text-gray-900">
                      {'Cases '}
                      {selFLW
                        ? ' \u2014 ' +
                          (
                            byFLW.filter(function (f) {
                              return f.key === selFLW;
                            })[0] || {}
                          ).flw
                        : ''}
                      <span className="text-xs font-normal text-gray-400 ml-2">
                        {caseRows.length > CASE_CAP
                          ? 'showing first ' +
                            CASE_CAP +
                            ' of ' +
                            caseRows.length +
                            ' \u2014 narrow by FLW or indicator to see the rest'
                          : caseRows.length +
                            ' case' +
                            (caseRows.length === 1 ? '' : 's') +
                            (derived.length
                              ? ''
                              : ' \u2014 per-visit detail is shown on the current reporting period')}
                      </span>
                    </div>
                    <div className="overflow-x-auto">
                      <table className="min-w-full text-xs">
                        <thead className="bg-gray-50 text-gray-500">
                          <tr>
                            <th className="px-2 py-2 text-left">Baby</th>
                            <th className="px-2 py-2 text-left">FLW</th>
                            <th className="px-2 py-2 text-right">
                              Visits
                              <div className="text-[10px] font-normal text-gray-400">
                                C06/C24
                              </div>
                            </th>
                            <th className="px-2 py-2 text-left">First visit</th>
                            <th className="px-2 py-2 text-center">
                              Started
                              <div className="text-[10px] font-normal text-gray-400">
                                C02
                              </div>
                            </th>
                            <th className="px-2 py-2 text-center">Eligible</th>
                            <th className="px-2 py-2 text-center">
                              Outcome known
                              <div className="text-[10px] font-normal text-gray-400">
                                C15
                              </div>
                            </th>
                            <th className="px-2 py-2 text-center">
                              Died
                              <div className="text-[10px] font-normal text-gray-400">
                                C14
                              </div>
                            </th>
                            <th className="px-2 py-2 text-right">
                              Weight readings
                            </th>
                            <th className="px-2 py-2 text-center">
                              Weight computable
                              <div className="text-[10px] font-normal text-gray-400">
                                C07
                              </div>
                            </th>
                            <th className="px-2 py-2 text-center">
                              Weight consistent
                              <div className="text-[10px] font-normal text-gray-400">
                                C08
                              </div>
                            </th>
                            <th className="px-2 py-2 text-center">
                              Weight sufficient
                              <div className="text-[10px] font-normal text-gray-400">
                                C09
                              </div>
                            </th>
                            <th className="px-2 py-2 text-right">
                              First &rarr; last weight (g)
                            </th>
                            <th className="px-2 py-2 text-right">
                              Early growth rate
                              <div className="text-[10px] font-normal text-gray-400">
                                C13
                              </div>
                            </th>
                            <th className="px-2 py-2 text-left">
                              Growth
                              <div className="text-[10px] font-normal text-gray-400">
                                C10-12
                              </div>
                            </th>
                            <th className="px-2 py-2 text-right">
                              Days discharge to enrolment
                              <div className="text-[10px] font-normal text-gray-400">
                                C17
                              </div>
                            </th>
                            <th className="px-2 py-2 text-center">
                              &le;3d
                              <div className="text-[10px] font-normal text-gray-400">
                                C16
                              </div>
                            </th>
                            <th className="px-2 py-2 text-center">
                              Danger sign
                              <div className="text-[10px] font-normal text-gray-400">
                                C20
                              </div>
                            </th>
                            <th className="px-2 py-2 text-center">
                              Referred
                              <div className="text-[10px] font-normal text-gray-400">
                                C19
                              </div>
                            </th>
                            <th className="px-2 py-2 text-right">
                              Self-referrals
                              <div className="text-[10px] font-normal text-gray-400">
                                C21
                              </div>
                            </th>
                            <th className="px-2 py-2 text-right">
                              Skin-to-skin hours
                              <div className="text-[10px] font-normal text-gray-400">
                                C23
                              </div>
                            </th>
                            <th className="px-2 py-2 text-center">
                              Enrolment wt = birth wt
                              <div className="text-[10px] font-normal text-gray-400">
                                C28
                              </div>
                            </th>
                            <th className="px-2 py-2 text-right">
                              Weights rounded to 100g
                              <div className="text-[10px] font-normal text-gray-400">
                                C31
                              </div>
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {(function () {
                            var rows = caseRows;
                            function tick(b) {
                              return b ? '\u2713' : '';
                            }
                            function num(x, dp) {
                              return typeof x === 'number' && !isNaN(x)
                                ? x.toFixed(dp || 0)
                                : '\u2014';
                            }
                            return rows.slice(0, CASE_CAP).map(function (r) {
                              return (
                                <tr
                                  key={r.entity_id}
                                  className="border-t border-gray-100"
                                >
                                  <td className="px-2 py-1.5">{r.name}</td>
                                  <td className="px-2 py-1.5">{r.flw}</td>
                                  <td className="px-2 py-1.5 text-right">
                                    {r.num_visits}
                                  </td>
                                  <td className="px-2 py-1.5">
                                    {r.first_visit || '\u2014'}
                                  </td>
                                  <td className="px-2 py-1.5 text-center">
                                    {tick(r.started)}
                                  </td>
                                  <td className="px-2 py-1.5 text-center">
                                    {tick(r.eligible)}
                                  </td>
                                  <td className="px-2 py-1.5 text-center">
                                    {tick(r.outcome_known)}
                                  </td>
                                  <td className="px-2 py-1.5 text-center">
                                    {r.died ? '\u2715' : ''}
                                  </td>
                                  <td className="px-2 py-1.5 text-right">
                                    {r.n_weight_readings}
                                  </td>
                                  <td className="px-2 py-1.5 text-center">
                                    {tick(r.weight_computable)}
                                  </td>
                                  <td className="px-2 py-1.5 text-center">
                                    {tick(r.weight_consistent)}
                                  </td>
                                  <td className="px-2 py-1.5 text-center">
                                    {tick(r.weight_gain_data_sufficient)}
                                  </td>
                                  <td className="px-2 py-1.5 text-right">
                                    {r.first_weight_g
                                      ? r.first_weight_g +
                                        '\u2192' +
                                        r.last_weight_g
                                      : '\u2014'}
                                  </td>
                                  <td className="px-2 py-1.5 text-right">
                                    {num(r.early_g_per_kg_day, 1)}
                                  </td>
                                  <td className="px-2 py-1.5">
                                    {r.growth_class || '\u2014'}
                                  </td>
                                  <td className="px-2 py-1.5 text-right">
                                    {num(r.days_discharge_to_reg)}
                                  </td>
                                  <td className="px-2 py-1.5 text-center">
                                    {tick(r.enrolled_within_3d)}
                                  </td>
                                  <td className="px-2 py-1.5 text-center">
                                    {tick(r.ever_danger_sign)}
                                  </td>
                                  <td className="px-2 py-1.5 text-center">
                                    {tick(r.referred)}
                                  </td>
                                  <td className="px-2 py-1.5 text-right">
                                    {r.self_referral_count || 0}
                                  </td>
                                  <td className="px-2 py-1.5 text-right">
                                    {num(r.kmc_hours_mean, 1)}
                                  </td>
                                  <td className="px-2 py-1.5 text-center">
                                    {r.enrollment_is_birth_copy === null
                                      ? '\u2014'
                                      : tick(r.enrollment_is_birth_copy)}
                                  </td>
                                  <td className="px-2 py-1.5 text-right">
                                    {r.n_weights_round_100}
                                  </td>
                                </tr>
                              );
                            });
                          })()}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              );
            })()}

          <div className="bg-white border border-gray-200 rounded-xl p-4">
            <div className="font-medium text-gray-900 mb-2 text-sm">
              Declared in the workbook, not computable yet
            </div>
            {runIsSynthetic && (
              <div className="mb-2 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1.5">
                Prepared on a synthetic copy of the programme data. Personal
                identifiers &mdash; names, phone numbers, addresses, GPS and
                free text &mdash; are never reproduced, so any measure derived
                from them is shown as unavailable rather than as zero.
              </div>
            )}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-1 text-xs text-gray-500">
              {NOT_COMPUTABLE.map(function (n) {
                return (
                  <div key={n.id}>
                    <span className="font-mono text-gray-400">{n.id}</span>{' '}
                    {n.name} — {n.why}
                  </div>
                );
              })}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
