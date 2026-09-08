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
  // ══ KMC indicator registry ════════════════════════════════════════════════
  // A direct port of the kmc_metrics_framework workbook. The registry below IS
  // the Case-indicators tab: id, category, numerator, denominator (eligibility),
  // bands, minimum denominator to score, and whether it is an FLW indicator.
  // Nothing here is invented — an indicator the workbook leaves TBD is rendered
  // as unbanded, and one whose inputs this programme does not collect is shown
  // as not-computable with the reason, rather than given a plausible number.
  //
  // Per-baby properties arrive already computed from the entity pipeline (SQL).
  // This file derives only what SQL cannot express — the weight-series triple —
  // then evaluates indicators and applies bands.

  var cases =
    (pipelines && pipelines.children && pipelines.children.rows) || [];
  var wrows = (pipelines && pipelines.visits && pipelines.visits.rows) || [];
  var chMeta =
    (pipelines && pipelines.children && pipelines.children.metadata) || {};

  var v = view || {
    state: (instance && instance.state) || {},
    isCompleted: false,
    asOf: null,
    complete: null,
  };

  // ── Saved runs ────────────────────────────────────────────────────────────
  // A completed run reads FROZEN AGGREGATES, never raw rows. This cohort's two
  // pipelines are 8,656 case rows + 34,737 visit rows = 21.6 MB of JSON, four times
  // the framework's 5 MB snapshot cap — and WORKFLOW_REFERENCE is explicit that
  // verbatim capture is the failure mode that OOM-killed a web worker on a 102k-visit
  // opp. So the render computes what it displays and freezes THAT (~300 KB), which is
  // also the thing a reader actually wants preserved: the numbers as published.
  var snapshot =
    view && view.isCompleted && view.state && view.state.snapshot
      ? view.state.snapshot
      : null;

  // The (opportunity, worker) key separator. Declared UP HERE, above every scope
  // memo that builds one: `var` hoists as undefined, so a memo evaluated earlier in
  // the render than the old declaration site produced keys like "10042undefined"
  // -- silently, since that is a perfectly good object key.
  var FLW_SEP = '::';

  var LLO_OF = {
    524: 'PIPN',
    874: 'PIPN',
    1487: 'PIPN',
    2166: 'PIPN',
    523: 'NAMA',
    938: 'NAMA',
    1488: 'NAMA',
    675: 'GHI',
    1234: 'GHI',
    1236: 'EHA',
    1739: 'Kikapu',
    1790: 'BERI',
    10021: 'PIPN',
    10019: 'PIPN',
    10015: 'PIPN',
    10022: 'NAMA',
    10018: 'NAMA',
    10014: 'NAMA',
    10020: 'GHI',
    10017: 'GHI',
    10016: 'EHA',
    10013: 'Kikapu',
    10042: 'BERI',
  };
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
  // Synthetic clones are provisioned above 10000; the real KMC opportunities are
  // all below it. This matters for honesty: on a synthetic run, "no value reaches
  // this row" can mean OUR clone does not carry the field, which is not evidence
  // about what the real programme records.
  function isSyntheticOpp(o) {
    return Number(o) >= 10000;
  }
  function oppLabel(o) {
    return OPP_LABEL[o] || 'opp ' + o;
  }

  // Case count for a rollup row. A FROZEN run carries the indicator results but
  // NOT the per-case rows -- a snapshot deliberately drops them -- so reading
  // `rows.length` there renders a confident 0 next to a Started column reading
  // 606, which is worse than showing nothing: it looks like a measurement.
  // C01's denominator IS every case in the group, and it does survive the
  // snapshot, so fall through to that before giving up.
  function caseCount(g) {
    if (g && g.rows && g.rows.length) return g.rows.length;
    if (g && g.ind && g.ind['C01'] && typeof g.ind['C01'].n === 'number')
      return g.ind['C01'].n;
    return '\u2014';
  }

  var MIN_DEN = 25;
  // Neal's spec item 8: below this share of cases carrying a hospital discharge
  // date, C16's denominator is thin and biased and the figure must be marked.
  var C16_MIN_COVERAGE = 0.45;

  // ── App-structure capability map ──────────────────────────────────────────
  // APP_ASKS is derived from each opportunity's app_structure.json — the app's
  // ACTUAL question set (its /data/ paths), not from the observed data. That
  // distinction is the whole point: a blank column has three very different
  // causes and only one of them is benign.
  //
  //   not-in-app    the app never asks the question           -> n/a, benign
  //   no-value      the app asks, but nothing reaches this row -> investigate
  //   normal        asked and a value arrives                  -> score it
  //
  // The middle state deliberately says "reaches this row", NOT "was never
  // recorded". Absence at entity stage is NOT evidence the field is uncollected:
  // opp 524 records birth weight on 100% of its Register KMC Beneficiary forms
  // and still reads 0% here, because registration forms carry form.case.@case_id
  // with no subcase while visit forms carry both, so the registration values do
  // not survive the entity_id join. Claiming "never recorded" there would blame
  // the programme for a join defect.
  //
  // Deriving this from data instead of the app definition collapses the middle
  // case into the first, which turns a collection failure into a benign n/a.
  // Two real examples this map keeps honest: NAMA-523 and PIPN-524 both ASK for
  // birth weight (/data/child_details/birth_weight_group/child_weight_birth) and
  // recorded it zero times, and every one of the 11 apps asks for reg_date and
  // kmc discharge and none of them has a single value.
  // Keyed by BOTH real and synthetic-clone opp ids so one map serves both.
  var APP_ASKS = {
    10013: {
      birth_weight_g: true,
      danger_visits: true,
      days_discharge_to_reg: true,
      discharge_visits: true,
      enrollment_weight_g: true,
      kmc_hours_mean: true,
      referral_visits: true,
      reg_date: true,
      self_referral_visits: true,
      weights: true,
    },
    10014: {
      birth_weight_g: true,
      danger_visits: true,
      days_discharge_to_reg: true,
      discharge_visits: true,
      enrollment_weight_g: true,
      kmc_hours_mean: true,
      referral_visits: true,
      reg_date: true,
      self_referral_visits: true,
      weights: true,
    },
    10015: {
      birth_weight_g: true,
      danger_visits: true,
      days_discharge_to_reg: true,
      discharge_visits: true,
      enrollment_weight_g: true,
      kmc_hours_mean: true,
      referral_visits: true,
      reg_date: true,
      self_referral_visits: true,
      weights: true,
    },
    10016: {
      birth_weight_g: true,
      danger_visits: true,
      days_discharge_to_reg: true,
      discharge_visits: true,
      enrollment_weight_g: true,
      kmc_hours_mean: true,
      referral_visits: true,
      reg_date: true,
      self_referral_visits: true,
      weights: true,
    },
    10017: {
      birth_weight_g: true,
      danger_visits: true,
      days_discharge_to_reg: true,
      discharge_visits: true,
      enrollment_weight_g: true,
      kmc_hours_mean: true,
      referral_visits: true,
      reg_date: true,
      self_referral_visits: true,
      weights: true,
    },
    10018: {
      birth_weight_g: true,
      danger_visits: true,
      days_discharge_to_reg: true,
      discharge_visits: true,
      enrollment_weight_g: true,
      kmc_hours_mean: true,
      referral_visits: true,
      reg_date: true,
      self_referral_visits: true,
      weights: true,
    },
    10019: {
      birth_weight_g: true,
      danger_visits: true,
      days_discharge_to_reg: true,
      discharge_visits: true,
      enrollment_weight_g: true,
      kmc_hours_mean: true,
      referral_visits: true,
      reg_date: true,
      self_referral_visits: true,
      weights: true,
    },
    10020: {
      birth_weight_g: true,
      danger_visits: true,
      days_discharge_to_reg: false,
      discharge_visits: true,
      enrollment_weight_g: false,
      kmc_hours_mean: true,
      referral_visits: true,
      reg_date: true,
      self_referral_visits: false,
      weights: true,
    },
    10021: {
      birth_weight_g: true,
      danger_visits: true,
      days_discharge_to_reg: false,
      discharge_visits: true,
      enrollment_weight_g: true,
      kmc_hours_mean: true,
      referral_visits: true,
      reg_date: true,
      self_referral_visits: false,
      weights: true,
    },
    10022: {
      birth_weight_g: true,
      danger_visits: true,
      days_discharge_to_reg: false,
      discharge_visits: true,
      enrollment_weight_g: true,
      kmc_hours_mean: true,
      referral_visits: true,
      reg_date: true,
      self_referral_visits: false,
      weights: true,
    },
    10042: {
      birth_weight_g: true,
      danger_visits: true,
      days_discharge_to_reg: true,
      discharge_visits: true,
      enrollment_weight_g: true,
      kmc_hours_mean: true,
      referral_visits: true,
      reg_date: true,
      self_referral_visits: true,
      weights: true,
    },
    1234: {
      birth_weight_g: true,
      danger_visits: true,
      days_discharge_to_reg: false,
      discharge_visits: true,
      enrollment_weight_g: true,
      kmc_hours_mean: true,
      referral_visits: true,
      reg_date: true,
      self_referral_visits: true,
      weights: true,
    },
    1236: {
      birth_weight_g: true,
      danger_visits: true,
      days_discharge_to_reg: false,
      discharge_visits: true,
      enrollment_weight_g: true,
      kmc_hours_mean: true,
      referral_visits: true,
      reg_date: true,
      self_referral_visits: true,
      weights: true,
    },
    1487: {
      birth_weight_g: true,
      danger_visits: true,
      days_discharge_to_reg: true,
      discharge_visits: true,
      enrollment_weight_g: true,
      kmc_hours_mean: true,
      referral_visits: true,
      reg_date: true,
      self_referral_visits: true,
      weights: true,
    },
    1488: {
      birth_weight_g: true,
      danger_visits: true,
      days_discharge_to_reg: true,
      discharge_visits: true,
      enrollment_weight_g: true,
      kmc_hours_mean: true,
      referral_visits: true,
      reg_date: true,
      self_referral_visits: true,
      weights: true,
    },
    1739: {
      birth_weight_g: true,
      danger_visits: true,
      days_discharge_to_reg: true,
      discharge_visits: true,
      enrollment_weight_g: true,
      kmc_hours_mean: true,
      referral_visits: true,
      reg_date: true,
      self_referral_visits: true,
      weights: true,
    },
    1790: {
      birth_weight_g: true,
      danger_visits: true,
      days_discharge_to_reg: true,
      discharge_visits: true,
      enrollment_weight_g: true,
      kmc_hours_mean: true,
      referral_visits: true,
      reg_date: true,
      self_referral_visits: true,
      weights: true,
    },
    523: {
      birth_weight_g: true,
      danger_visits: true,
      days_discharge_to_reg: false,
      discharge_visits: true,
      enrollment_weight_g: true,
      kmc_hours_mean: true,
      referral_visits: true,
      reg_date: true,
      self_referral_visits: false,
      weights: true,
    },
    524: {
      birth_weight_g: true,
      danger_visits: true,
      days_discharge_to_reg: false,
      discharge_visits: true,
      enrollment_weight_g: true,
      kmc_hours_mean: true,
      referral_visits: true,
      reg_date: true,
      self_referral_visits: false,
      weights: true,
    },
    675: {
      birth_weight_g: true,
      danger_visits: true,
      days_discharge_to_reg: false,
      discharge_visits: true,
      enrollment_weight_g: false,
      kmc_hours_mean: true,
      referral_visits: true,
      reg_date: true,
      self_referral_visits: false,
      weights: true,
    },
    874: {
      birth_weight_g: true,
      danger_visits: true,
      days_discharge_to_reg: false,
      discharge_visits: true,
      enrollment_weight_g: true,
      kmc_hours_mean: true,
      referral_visits: true,
      reg_date: true,
      self_referral_visits: true,
      weights: true,
    },
    938: {
      birth_weight_g: true,
      danger_visits: true,
      days_discharge_to_reg: false,
      discharge_visits: true,
      enrollment_weight_g: true,
      kmc_hours_mean: true,
      referral_visits: true,
      reg_date: true,
      self_referral_visits: true,
      weights: true,
    },
  };
  // Which pipeline field each indicator's numerator/denominator ultimately needs.
  // Which DERIVED case property each indicator ultimately needs. These are the
  // names on the derived row, not the pipeline column — the derivation renames
  // several (danger_visits -> ever_danger_sign, referral_visits -> referred,
  // self_referral_visits -> self_referral_count). Naming the pipeline column here
  // meant the lookup found nothing and silently blanked C19/C20/C21, which had
  // been reporting 27.1% / 15.5% / 31.3 the day before.
  var IND_INPUTS = {
    C07: ['weights'],
    C08: ['weights'],
    C09: ['weights'],
    C10: ['weights'],
    C11: ['weights'],
    C12: ['weights'],
    C13: ['weights'],
    C31: ['weights'],
    C16: ['days_discharge_to_reg'],
    C17: ['days_discharge_to_reg'],
    C19: ['referred'],
    C20: ['ever_danger_sign'],
    C21: ['self_referral_count'],
    C23: ['kmc_hours_mean'],
    C28: ['birth_weight_g', 'enrollment_weight_g'],
  };
  // derived name -> the pipeline column APP_ASKS is keyed on
  var ASKS_AS = {
    referred: 'referral_visits',
    ever_danger_sign: 'danger_visits',
    self_referral_count: 'self_referral_visits',
  };
  // Fields where 0/false means "nothing was recorded" rather than a real zero.
  var ZERO_IS_ABSENT = {
    referred: 1,
    ever_danger_sign: 1,
    self_referral_count: 1,
  };
  function anyAsks(field, opps) {
    var col = ASKS_AS[field] || field;
    if (!opps || !opps.length) return true;
    return opps.some(function (o) {
      var m = APP_ASKS[String(o)];
      return !m || m[col] === undefined || m[col];
    });
  }
  // "Recorded" is computed from the rows in scope rather than baked in, so it stays
  // true as the data changes.
  // ── Targets & settings tab (the workbook's typed human inputs) ────────────
  // The credibility gates that used to live here -- MORTALITY_CREDIBLE,
  // COMPLETION_CREDIBLE and credibleFor -- are gone. They are declared once, in
  // connect_labs/semantic/registry/kmc/deployment.yml, compiled into the query as
  // the registry's `suppression:` rules, and arrive on each row as
  // `<measure>_suppressed`. Three copies of one human judgement about which LLOs
  // record deaths credibly is how they drift; there is now one.
  //
  // MONTHLY_TARGET, TOTAL_STARTED_TARGET and SCALE_TIER_CASES_PER_MONTH went with
  // them: they were declared here and read nowhere, in this file or any other.

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
    [cases, wrows],
  );

  // ══ The C-series, served by the semantic layer ════════════════════════════
  // This was `var IND` -- ~360 lines of closures restating the workbook -- run by
  // `evaluate`/`evalAll`. The same 22 indicators now compile to SQL from
  // connect_labs/semantic/registry/kmc/indicators.yml and are evaluated in ONE
  // GROUPING SETS pass across all five scopes. Parity with the engine this
  // replaces is pinned in connect_labs/semantic/PARITY.md -- 5,698 checks, 0
  // mismatches at programme, opportunity, llo and flw -- and `month` was closed
  // separately (#1523). That the registry computes exactly the same 22 indicators
  // is pinned by test_the_registry_computes_exactly_the_indicators_the_render_did.
  //
  // UNITS -- the one conversion in this file, and it is deliberate. The registry
  // scales percentages in its own sql (100.0 * num / den), so a row carries 0-100.
  // This render has always carried FRACTIONS internally and multiplied by 100 in
  // `fmt` -- and, the part that actually forces the decision, every snapshot run
  // ever saved stores fractions. Converting once here rather than at fifteen
  // display sites keeps every saved snapshot rendering correctly and untouched.
  // Bands are graded BEFORE the conversion, against the registry's own percent
  // thresholds, so no threshold is ever compared across units.
  var C_SCOPES =
    'programme,opportunity,llo,flw,month,llo_month,opportunity_month,flw_month';

  // Frozen runs never query. Their values are in the snapshot; all they need from
  // the server is the display contract, and `catalog_only` returns exactly that
  // without touching a pipeline or the database.
  var sCS = React.useState({
    // A snapshot run that carries its own catalog needs nothing from the server, so
    // it is ready immediately. Starting it at 'loading' left the "Computing
    // indicators in SQL…" banner up permanently, because the effect below returns
    // early in exactly that case and never flipped the status.
    status:
      snapshot && (snapshot.cMeasures || []).length
        ? 'ready'
        : snapshot
          ? 'loading'
          : 'idle',
    rows: [],
    measures: (snapshot && snapshot.cMeasures) || [],
  });
  var cSeries = sCS[0],
    setCSeries = sCS[1];

  function cUrl(params) {
    var wfId = nWorkflowId();
    return wfId ? '/labs/workflow/api/' + wfId + '/semantic/?' + params : null;
  }

  function loadCSeries() {
    // A snapshot run wants labels only; anything else wants the numbers too.
    var wantRows = !snapshot;
    var url = cUrl(
      (wantRows ? 'series=C&scopes=' + C_SCOPES : 'series=C&catalog_only=1') +
        oppParam(),
    );
    if (!url) {
      setCSeries({
        status: 'error',
        rows: [],
        measures: [],
        error: 'could not determine the workflow id from the page',
      });
      return;
    }
    setCSeries({ status: 'loading', rows: [], measures: [] });
    fetch(url)
      .then(function (res) {
        return res.json();
      })
      .then(function (data) {
        if (data.error) {
          setCSeries({
            status: 'error',
            rows: [],
            measures: [],
            error: data.error,
          });
          return;
        }
        setCSeries({
          status: 'ready',
          rows: data.rows || [],
          measures: data.measures || [],
          coldCache: data.cold_cache || false,
          partialCache: data.partial_cache || false,
          coldHint: data.cold_cache_hint || '',
        });
      })
      .catch(function (err) {
        setCSeries({
          status: 'error',
          rows: [],
          measures: [],
          error: String((err && err.message) || err),
        });
      });
  }

  // The C-series IS this dashboard's content, not a side panel, so unlike the
  // N-series it loads with the page rather than behind a button. A snapshot run
  // fetches only the catalog, which runs no query at all.
  React.useEffect(
    function () {
      if (snapshot && (snapshot.cMeasures || []).length) return;
      loadCSeries();
    },
    [Boolean(snapshot)],
  );

  // The display contract, normalised into the field names the tables already use.
  // The served catalog calls things `indicator` / `title` / `category` /
  // `prominence`; `IND` called them id / name / cat / prom. Renaming HERE, once,
  // means every display site downstream is untouched -- the smallest diff, and the
  // one least able to silently mis-render a column.
  //
  // `measure` is the registry's own measure name (c09), which is the column prefix
  // in a result row. `id` stays the workbook's indicator id (C09), which is what
  // every table, selector and snapshot snapshot is keyed on.
  var C_LIST = React.useMemo(
    function () {
      return (cSeries.measures || [])
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
            tbdInput: m.tbd_input,
            scopeNote: m.scope_note,
          };
        });
    },
    [cSeries.measures],
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

  // `IND[6]` used to index the registry positionally. A lookup by id survives the
  // registry gaining, losing or reordering a measure; a position does not. The
  // fallback keeps a label on screen if the catalog has not arrived yet.
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

  // Rows, split by the `scope` label the GROUPING SETS query stamps on each one.
  var cRows = React.useMemo(
    function () {
      var out = {
        programme: null,
        opportunity: {},
        llo: {},
        flw: {},
        month: {},
        llo_month: {},
        opportunity_month: {},
        flw_month: {},
      };
      (cSeries.rows || []).forEach(function (r) {
        if (r.scope === 'programme') out.programme = r;
        else if (r.scope === 'opportunity')
          out.opportunity[String(r.opportunity_id)] = r;
        else if (r.scope === 'llo') out.llo[String(r.llo)] = r;
        else if (r.scope === 'flw')
          out.flw[r.opportunity_id + FLW_SEP + r.username] = r;
        else if (r.scope === 'month') out.month[cMonth(r)] = r;
        else if (r.scope === 'llo_month')
          out.llo_month[r.llo + '|' + cMonth(r)] = r;
        else if (r.scope === 'opportunity_month')
          out.opportunity_month[r.opportunity_id + '|' + cMonth(r)] = r;
        else if (r.scope === 'flw_month')
          out.flw_month[
            r.opportunity_id + FLW_SEP + r.username + '|' + cMonth(r)
          ] = r;
      });
      return out;
    },
    [cSeries.rows],
  );

  // One indicator's verdict for one scope row, in the shape the tables have always
  // consumed: { id, n, value, band }. `evaluate` produced this from closures over
  // case rows; it now reads three columns the query already computed.
  function cEntry(measure, row) {
    var id = measure.id;
    var out = { id: id, n: 0, value: null, band: 'nodata' };
    if (!row) return out;

    var den = row[measure.measure + '_denominator'];
    out.n = den === null || den === undefined ? 0 : Number(den);

    // Credibility marks a figure, it does not erase it. The engine this replaced
    // computed the value and set band 'notcredible' -- which is why bandLabel reads
    // "SHOWN, not credible" -- and its comment said why: a blank cell reads as "no
    // data", which is wrong and actively confusing, because these LLOs DO record
    // deaths; the workbook only says not credibly. Blanking also HIDES the
    // under-recording, since pooling every LLO reads lower than the credible
    // recorders alone. Returning early here regressed that to an em-dash for four
    // of six LLOs. Neal's spec agrees: his expected table carries a mortality figure
    // for every LLO with a sufficient denominator.
    var notCredible = !cCredible(measure, row);

    var state = cInputState(id, row, cScopeOpps(row));
    if (state !== 'ok') {
      out.band = state;
      return out;
    }

    var raw = row[measure.measure];
    if (raw === null || raw === undefined || isNaN(Number(raw))) return out;

    var minDen = measure.minDen || MIN_DEN;
    if (out.n && minDen && out.n < minDen) {
      out.band = 'insufficient';
      return out;
    }

    out.band = notCredible ? 'notcredible' : cBandOf(measure, Number(raw));
    out.value = measure.unit === '%' ? Number(raw) / 100 : Number(raw);

    // Neal's spec, item 8: "Parenthesize / footnote for any LLO where <45% of cases
    // carry a discharge date (thin, biased denominator)." C16's denominator is
    // `started AND has a discharge date`, so when few cases carry one the rate is
    // computed over a self-selected minority and reads far too well -- measured on
    // this cohort, PIPN scores 96.5% off 20% coverage where the full-coverage figure
    // is 66%.
    // `id` is the workbook id (C16); `measure` is the registry measure name (c16).
    if (measure.id === 'C16') {
      var started = row.c02;
      if (started) {
        var coverage = Number(out.n) / Number(started);
        if (coverage < C16_MIN_COVERAGE) {
          out.thinDenominator = true;
          out.coverage = coverage;
        }
      }
    }
    return out;
  }

  // 'ok' | 'notinapp' | 'unrecorded' -- the same two-reason gate that
  // semantic/gates.py:input_state() implements server-side, reading the
  // `anyrec_<field>` columns the registry emits for exactly this. It replaces the
  // old anyRecorded(), which scanned derived case rows: at a drill scope the query
  // already knows whether anything was recorded, and 268 of 5,302 per-FLW checks
  // turned on getting this right.
  //
  // The distinction is real and worth keeping: an app that never ASKS the question
  // ("not in this app") is a different fact about the programme from one that asks
  // and recorded nothing ("no value reaches this row"). Both render n/a, so the
  // values agree either way -- but reporting the wrong reason misdescribes it.
  function cInputState(indId, row, opps) {
    var need = IND_INPUTS[indId];
    if (!need) return 'ok';
    for (var i = 0; i < need.length; i++) {
      if (!anyAsks(need[i], opps)) return 'notinapp';
      var gate = row['anyrec_' + need[i]];
      if (gate !== null && gate !== undefined && Number(gate) === 0)
        return 'unrecorded';
    }
    return 'ok';
  }

  // Is this row's scope a credible recorder for this measure?
  //
  // NOT simply `row.<measure>_suppressed`. That column is
  // `(props.llo IS NULL OR props.llo NOT IN (credible))`, and `props.llo` is NULL in
  // every grouping set that does not group BY llo -- programme, opportunity, flw,
  // month. So the column reads TRUE there, and trusting it renders "recording not
  // credible" on the programme card, which is the one scope semantic/gates.py says
  // must never be gated ("Programme scope (llo=None) is never gated: it pools
  // credible recorders").
  //
  // So: read the credible SET off the llo scope, where the column does mean what it
  // says, then gate each row by ITS OWN llo. Programme has none and stays ungated,
  // which is also what the old render did -- it passed `llo` per scope and
  // `credibleFor` returned true for null.
  function cCredible(measure, row) {
    var llo = cLloOfRow(row);
    if (!llo) return true;
    var credible = cCredibleSet(measure.id);
    return credible === null || credible[llo] === true;
  }

  // The llo a scope row belongs to, or null when the row pools several.
  function cLloOfRow(row) {
    if (row.llo) return row.llo;
    if (row.opportunity_id !== null && row.opportunity_id !== undefined)
      return lloOf(Number(row.opportunity_id));
    return null;
  }

  // { LLO: true } for the recorders the registry did not suppress, read off the llo
  // scope. Null when that scope is absent from the response, which means "no basis
  // to gate" rather than "gate everything" -- withholding every number because a
  // scope was not requested would be worse than showing them.
  function cCredibleSet(id) {
    var measure = indOf(id);
    var keys = Object.keys(cRows.llo);
    if (!keys.length) return null;
    var out = {};
    var sawColumn = false;
    keys.forEach(function (k) {
      var r = cRows.llo[k];
      var v = r[measure.measure + '_suppressed'];
      if (v === undefined) return;
      sawColumn = true;
      if (v === false) out[r.llo] = true;
    });
    return sawColumn ? out : null;
  }

  // Bands are graded in the REGISTRY's units (percent), before cEntry converts the
  // value to the fraction this render carries. Shares `nBandOf`, which already
  // implements the workbook's higher / lower / two-sided rules.
  function cBandOf(measure, percentValue) {
    // nBandOf reads `direction`; a C_LIST entry renames that to `dir` so the
    // display sites keep the field names the old `IND` used. Passing the entry
    // straight through therefore matched none of nBandOf's branches and returned
    // 'unbanded' for EVERY indicator -- which renders as a grey chip and a
    // truthful-looking "0 of 6 LLOs with a red indicator" while C09 sat at 37.8%
    // against a red threshold of 40. Bridge the two names here rather than
    // widening C_LIST, so there is one entry shape and one place that knows both.
    return nBandOf(
      { bands: measure.bands, direction: measure.dir, unit: measure.unit },
      percentValue,
    );
  }

  // Which opportunities a scope row covers -- the input the "not in this app"
  // gate needs, since an app that never asks a question is a different fact from
  // one that asks and recorded nothing.
  function cScopeOpps(row) {
    if (row.scope === 'opportunity' || row.scope === 'flw')
      return [row.opportunity_id];
    if (row.scope === 'llo') {
      return Object.keys(LLO_OF).filter(function (o) {
        return LLO_OF[o] === row.llo;
      });
    }
    return null;
  }

  // 'YYYY-MM'. The query returns cohort_month as a date or a timestamp depending
  // on the driver, so slice rather than trust the type.
  function cMonth(row) {
    return String(row.cohort_month || '').slice(0, 7);
  }

  // Pool ONE indicator across several scope rows by summing its numerator and
  // denominator. Needed because "mortality among the LLOs that record it credibly"
  // is not any single scope: the programme row pools everyone, and the workbook
  // says four of six do not record deaths credibly. Summing the parts is exact --
  // averaging the per-LLO percentages would not be.
  function cPooled(id, rows) {
    var measure = indOf(id);
    var out = { id: id, n: 0, value: null, band: 'nodata' };
    var num = 0,
      den = 0,
      any = false;
    rows.forEach(function (r) {
      var nu = r[measure.measure + '_numerator'],
        de = r[measure.measure + '_denominator'];
      if (nu === null || nu === undefined || de === null || de === undefined)
        return;
      any = true;
      num += Number(nu);
      den += Number(de);
    });
    if (!any || !den) return out;
    out.n = den;
    var pct = (100 * num) / den;
    var minDen = measure.minDen || MIN_DEN;
    if (minDen && den < minDen) {
      out.band = 'insufficient';
      return out;
    }
    out.band = cBandOf(measure, pct);
    out.value = measure.unit === '%' ? pct / 100 : pct;
    return out;
  }

  // The LLO rows the registry did NOT suppress for an indicator -- i.e. the ones
  // the workbook accepts as credible recorders. Read off the query rather than a
  // local table, so there is one statement of who is credible.
  function cCredibleLloRows(id, index) {
    var measure = indOf(id);
    var src = index || cRows.llo;
    return Object.keys(src)
      .map(function (k) {
        return src[k];
      })
      .filter(function (r) {
        return cCredible(measure, r);
      });
  }

  // Every indicator for one scope row, keyed by id -- the `evalAll` replacement.
  function cIndFor(row) {
    var m = {};
    C_LIST.forEach(function (measure) {
      m[measure.id] = cEntry(measure, row);
    });
    return m;
  }

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

  // ── Roll up: opp → LLO → program ─────────────────────────────────────────
  var byOpp = React.useMemo(
    function () {
      if (snapshot) return snapshot.byOpp || [];
      var g = {};
      derived.forEach(function (r) {
        (g[r.opp] = g[r.opp] || []).push(r);
      });
      return Object.keys(g).map(function (o) {
        var llo = lloOf(Number(o));
        return {
          opp: Number(o),
          llo: llo,
          rows: g[o],
          ind: cIndFor(cRows.opportunity[String(o)]),
        };
      });
    },
    [derived, snapshot, cRows, C_LIST],
  );

  var byLLO = React.useMemo(
    function () {
      if (snapshot) return snapshot.byLLO || [];
      var g = {};
      derived.forEach(function (r) {
        (g[r.llo] = g[r.llo] || []).push(r);
      });
      return Object.keys(g)
        .sort()
        .map(function (l) {
          var rows = g[l];
          var ind = cIndFor(cRows.llo[String(l)]);
          var reds = Object.keys(ind).filter(function (k) {
            return ind[k].band === 'red';
          }).length;
          var yellows = Object.keys(ind).filter(function (k) {
            return ind[k].band === 'yellow';
          }).length;
          var opps = byOpp.filter(function (o) {
            return o.llo === l;
          });
          return {
            llo: l,
            rows: rows,
            ind: ind,
            reds: reds,
            yellows: yellows,
            opps: opps,
          };
        });
    },
    [derived, byOpp, snapshot],
  );

  // Separator for the composite FLW key. NOT '\u0000': a NUL byte is legal in a JS
  // string but Postgres cannot store it in a JSON column, so freezing a run died with
  // `UntranslatableCharacter: \u0000 cannot be converted to text` — the key only
  // became unstorable at the moment it was persisted, long after it was built.

  // FLW rollup. Keyed by opp+username: FLW usernames are only unique within an
  // opportunity (the synthetic cohort reuses flw_001.. across opps), so keying on
  // username alone silently merges different people into one row.
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
    var ms = ((snapshot && snapshot.monthly) || [])
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
        return r.first_visit || r.last_visit;
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

  // ══ N-series (Neal's demo compute spec), served by the semantic layer ══════
  // Everything else on this screen is computed in the browser from pipeline rows.
  // These come from SQL: the registry compiles to one GROUPING SETS query and runs
  // server-side, which is the only version where the scopes below cost one pass
  // instead of three. Fetched ON DEMAND rather than with the page -- it is a real
  // query against the visit cache, and the other tabs must not pay for it.
  // A snapshot run serves the N-series from its own snapshot. Every OTHER tab on
  // this screen already loads from `state.snapshot` and is instant; this one was
  // the sole holdout, re-querying the semantic endpoint every time. That made it
  // the only part of a supposedly-snapshot dashboard that could still go wrong --
  // and it did: production expires cached visits after an hour, so a snapshot
  // that "cannot move" sat next to a live tab reading 608 of 8,718 cases.
  // A snapshot is the right place for this. The rows are ~245 KB for 253 rows,
  // comfortably inside the framework's 5 MB cap.
  var sN = React.useState(
    snapshot && snapshot.nSeries
      ? {
          status: 'ready',
          rows: snapshot.nSeries.rows || [],
          measures: snapshot.nSeries.measures || [],
          opportunity_ids: snapshot.nSeries.opportunity_ids || [],
          fromSnapshot: true,
        }
      : { status: 'idle', rows: [], measures: [] },
  );
  var nSeries = sN[0],
    setNSeries = sN[1];

  // The DEFINITION id, which is NOT the run id. `definition.id` is not populated in
  // this render context -- measured live: the panel rendered, fetched nothing, and
  // reported "no workflow id" -- and instance.id is the RUN. The path carries it, so
  // fall through to that rather than guessing at another prop shape.
  // The labs context middleware REDIRECTS an api request that carries no
  // opportunity_id, substituting whatever opp the session last used. fetch()
  // follows that 302 transparently, so the render got a 200 whose body was the
  // wrong page: `data.rows` undefined, every indicator an em-dash, and no error
  // anywhere saying why. The framework's own pipeline-data/stream call has always
  // passed it; these did not.
  //
  // Read from the page URL -- the same place nWorkflowId() reads the definition id
  // from -- so a request is scoped to the opp you are looking at, not to session
  // state.
  function oppParam() {
    var m = String(window.location.search).match(/[?&]opportunity_id=(\d+)/);
    if (m) return '&opportunity_id=' + m[1];
    if (instance && instance.opportunity_id)
      return '&opportunity_id=' + instance.opportunity_id;
    return '';
  }

  function nWorkflowId() {
    if (definition && definition.id) return definition.id;
    if (instance && instance.definition_id) return instance.definition_id;
    var m = String(window.location.pathname).match(/\/workflow\/(\d+)\//);
    return m ? m[1] : null;
  }

  function loadNSeries() {
    var wfId = nWorkflowId();
    if (!wfId) {
      setNSeries({
        status: 'error',
        rows: [],
        measures: [],
        error: 'could not determine the workflow id from the page',
      });
      return;
    }
    setNSeries({ status: 'loading', rows: [], measures: [] });
    fetch(
      '/labs/workflow/api/' +
        wfId +
        '/semantic/?series=N&scopes=programme,opportunity,flw' +
        oppParam(),
    )
      .then(function (res) {
        return res.json();
      })
      .then(function (data) {
        if (data.error) {
          // The message names the missing column or relation -- show it rather
          // than a generic failure, which is the whole reason it is a 400.
          setNSeries({
            status: 'error',
            rows: [],
            measures: [],
            error: data.error,
          });
          return;
        }
        setNSeries({
          status: 'ready',
          rows: data.rows || [],
          measures: data.measures || [],
          // Carried so the snapshot can keep it: the card footer counts the
          // opportunities from here, and a snapshot run has no response to read.
          opportunity_ids: data.opportunity_ids || [],
          coldCache: data.cold_cache || false,
          partialCache: data.partial_cache || false,
          coldHint: data.cold_cache_hint || '',
        });
      })
      .catch(function (err) {
        setNSeries({
          status: 'error',
          rows: [],
          measures: [],
          error: String((err && err.message) || err),
        });
      });
  }

  // The measure catalog now arrives WITH the rows, from the same YAML that produced
  // the numbers — so a band cannot drift from the measure it grades. The C-series
  // above keeps a hand-maintained copy of its own registry in this file; that
  // duplication is the thing the semantic layer exists to end, and repeating it here
  // would have been the same mistake with a newer date on it.
  function nBandOf(m, value) {
    if (value === null || value === undefined || isNaN(Number(value)))
      return 'nodata';
    if (!m.bands) return 'unbanded';
    var x = Number(value),
      b = m.bands;
    if (m.direction === 'higher')
      return x >= b[0] ? 'green' : x >= b[1] ? 'yellow' : 'red';
    if (m.direction === 'lower')
      return x <= b[0] ? 'green' : x <= b[1] ? 'yellow' : 'red';
    if (m.direction === 'mid2') {
      // Two-sided: green inside the inner range, yellow inside the outer, red
      // beyond EITHER end. Guard the shape -- a one-dimensional band here would
      // silently read as unbanded, which is the single outcome a two-sided
      // mortality band exists to prevent (the spec: under ~2% means deaths are
      // not being recorded, not that babies are surviving).
      if (!b || !b.length || !b[0] || b[0].length !== 2) return 'unbanded';
      if (x >= b[0][0] && x <= b[0][1]) return 'green';
      if (b[1] && b[1].length === 2 && x >= b[1][0] && x <= b[1][1])
        return 'yellow';
      return 'red';
    }
    return 'unbanded';
  }

  var N_BAND_CLASS = {
    green: 'bg-green-100 text-green-800',
    yellow: 'bg-amber-100 text-amber-800',
    red: 'bg-red-100 text-red-800',
    unbanded: 'bg-gray-100 text-gray-500',
    nodata: 'bg-gray-50 text-gray-400',
    insufficient: 'bg-gray-100 text-gray-500',
  };

  function nFmt(value, unit) {
    if (value === null || value === undefined) return 'n/a';
    var num = Number(value);
    if (isNaN(num)) return String(value);
    if (unit === '%') return num.toFixed(1) + '%';
    if (unit === 'g') return Math.round(num) + ' g';
    return Math.round(num * 10) / 10;
  }

  // A value below its minimum denominator reads "insufficient volume", never a
  // number — the spec's rule 0.2, and the reason every measure ships a denominator.
  function nCell(m, row) {
    var v = row[m.id],
      den = row[m.id + '_denominator'];
    var minDen = m.min_denominator || 0;
    if (den !== null && den !== undefined && minDen && Number(den) < minDen) {
      return { text: 'n<' + minDen, band: 'insufficient', den: den };
    }
    return { text: nFmt(v, m.unit), band: nBandOf(m, v), den: den };
  }

  var byFLW = React.useMemo(
    function () {
      if (snapshot) return snapshot.byFLW || [];
      var g = {};
      derived.forEach(function (r) {
        var k = r.opp + FLW_SEP + (r.flw || '(unassigned)');
        (g[k] = g[k] || []).push(r);
      });
      return Object.keys(g)
        .map(function (k) {
          var parts = k.split(FLW_SEP),
            opp = Number(parts[0]);
          var rows = g[k],
            llo = lloOf(opp);
          var ind = cIndFor(cRows.flw[opp + FLW_SEP + parts[1]]);
          var reds = Object.keys(ind).filter(function (x) {
            return ind[x].band === 'red';
          }).length;
          var yellows = Object.keys(ind).filter(function (x) {
            return ind[x].band === 'yellow';
          }).length;
          return {
            key: k,
            opp: opp,
            flw: parts[1],
            llo: llo,
            rows: rows,
            ind: ind,
            reds: reds,
            yellows: yellows,
          };
        })
        .sort(function (a, b) {
          return b.rows.length - a.rows.length;
        });
    },
    [derived, snapshot, cRows, C_LIST],
  );

  var programInd = React.useMemo(
    function () {
      if (snapshot) return snapshot.programInd || {};
      return cIndFor(cRows.programme);
    },
    [derived, snapshot, cRows, C_LIST],
  );

  // Programme mortality, restricted to the LLOs the workbook accepts as credible
  // recorders of death. The card pooled every LLO while the table below it showed
  // "recording not credible" for four of six — so the headline number was built on
  // exactly the data the same dashboard declined to show, and it read LOWER than
  // reality because non-recorders contribute denominator without deaths.
  var mortalityCredible = React.useMemo(
    function () {
      // Snapshot first, like byOpp / byLLO / byFLW / programInd. This one is newly
      // snapshot-dependent: it used to be computed from `derived` -- pipeline rows a
      // snapshot run still loads -- and now reads the llo-scope SEMANTIC rows, which a
      // snapshot run deliberately never fetches. Without this the headline mortality
      // card silently degrades to "no credible recorder" the moment a run is snapshot,
      // while the LLO table beside it still shows EHA and PIPN reporting deaths.
      if (snapshot && snapshot.mortalityCredible)
        return snapshot.mortalityCredible;
      var credible = cCredibleLloRows('C14');
      var llos = credible
        .map(function (r) {
          return r.llo;
        })
        .filter(function (l) {
          return byLLO.some(function (x) {
            return x.llo === l;
          });
        });
      return {
        ind: credible.length ? cPooled('C14', credible) : null,
        llos: llos,
        of: byLLO.length,
      };
    },
    [byLLO, cRows, C_LIST, snapshot],
  );
  // ── UI ───────────────────────────────────────────────────────────────────
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
  // Cohort entry is anchored on the FIRST VISIT, not reg_date: every KMC app asks
  // for reg_date and not one has ever recorded a value, so C03 was listed as
  // not-computable. First visit is the honest proxy and is present on every case.
  // Each month's indicators are computed over the cases that ENTERED that month,
  // so a month's growth/weight figures describe that intake cohort rather than
  // everyone alive at the time.
  // The month-scope row matching the current drill, if any.
  function cMonthRow(month, lloArg, oppArg, flwArg) {
    if (flwArg) return cRows.flw_month[flwArg + '|' + month];
    if (oppArg) return cRows.opportunity_month[oppArg + '|' + month];
    if (lloArg) return cRows.llo_month[lloArg + '|' + month];
    return cRows.month[month];
  }

  // The rows to pool for credible-recorder mortality in one month. Undrilled that
  // is every credible LLO's row for that month; drilled it is the single row for
  // the scope in hand -- but only if the registry did not suppress it there, which
  // is what keeps a non-credible LLO's drill from showing a mortality figure the
  // programme card refuses to show.
  function cMonthCredible(month, lloArg, oppArg, flwArg) {
    if (flwArg || oppArg || lloArg) {
      var row = cMonthRow(month, lloArg, oppArg, flwArg);
      var measure = indOf('C14');
      return row && cCredible(measure, row) ? [row] : [];
    }
    var index = {};
    Object.keys(cRows.llo_month).forEach(function (k) {
      if (k.slice(k.indexOf('|') + 1) === month) index[k] = cRows.llo_month[k];
    });
    return cCredibleLloRows('C14', index);
  }

  // Scoped monthly series as a plain function, so the freeze step can precompute
  // every drill scope rather than only the one currently on screen.
  function monthlyFor(lloArg, oppArg, flwArg) {
    var m = function (d) {
      return String(d || '').slice(0, 7);
    };
    var flwKey = flwArg ? String(flwArg).split(FLW_SEP) : null;
    var ok = function (oppId, llo, flw) {
      if (flwKey) return oppId === Number(flwKey[0]) && flw === flwKey[1];
      if (oppArg) return oppId === oppArg;
      if (lloArg) return llo === lloArg;
      return true;
    };
    var byMonth = {},
      visitsByMonth = {};
    derived
      .filter(function (r) {
        return ok(r.opp, r.llo, r.flw);
      })
      .forEach(function (r) {
        // Cohort on the REGISTRATION date, falling back to first visit only where a
        // row genuinely has none. reg_date is a hidden field the form auto-calculates,
        // so it was absent from every clone until the profiler learned to carry
        // calculated fields — first visit was the proxy standing in for it, and this
        // is what C03 was waiting on.
        var k = m(r.reg_date) || m(r.first_visit);
        if (k) (byMonth[k] = byMonth[k] || []).push(r);
      });
    wrows.forEach(function (v) {
      if (!ok(v.opportunity_id, lloOf(v.opportunity_id), v.username)) return;
      var vk = m(v.visit_date);
      if (vk) visitsByMonth[vk] = (visitsByMonth[vk] || 0) + 1;
    });
    var months = Object.keys(byMonth)
      .concat(Object.keys(visitsByMonth))
      .filter(function (v, i, a) {
        return v && a.indexOf(v) === i;
      })
      .sort();
    return months.map(function (k) {
      var rows = byMonth[k] || [];
      // The trend FOLLOWS THE DRILL, so it reads the month scope that matches it:
      // flw_month / opportunity_month / llo_month, else the programme-wide month.
      // Those composite scopes exist for exactly this -- a bare `month` groups by
      // cohort_month alone and cannot answer a drilled question.
      var mrow = cMonthRow(k, lloArg, oppArg, flwArg);
      var ind = mrow ? cIndFor(mrow) : {};
      // Credible-recorder mortality still has to be pooled, and at a drill the
      // scope is already restricted, so the row itself is the pool.
      var credible = cMonthCredible(k, lloArg, oppArg, flwArg);
      return {
        month: k,
        started: rows.filter(function (r) {
          return r.started;
        }).length,
        registered: rows.filter(function (r) {
          return r.registered;
        }).length,
        visits: visitsByMonth[k] || 0,
        c09: ind['C09'],
        c13: ind['C13'],
        c15: ind['C15'],
        mortality: credible.length ? cPooled('C14', credible) : null,
      };
    });
  }

  var monthly = React.useMemo(
    function () {
      if (snapshot) {
        var all = snapshot.monthly || [];
        // The snapshot series is stored per scope key so a drill still works offline.
        var key = selFLW
          ? 'flw:' + selFLW
          : selOpp
            ? 'opp:' + selOpp
            : selLLO
              ? 'llo:' + selLLO
              : 'all';
        return (snapshot.monthlyByScope && snapshot.monthlyByScope[key]) || all;
      }
      // monthlyFor IS this computation, and the freeze step already calls it to
      // precompute every drill scope. Inlining a second copy here let the two
      // drift: this one cohorted on first_visit alone while monthlyFor cohorted on
      // reg_date falling back to first_visit. They agree only for as long as
      // reg_date stays unrecorded everywhere, which is a property of today's data,
      // not of the code.
      return monthlyFor(selLLO, selOpp, selFLW);
    },
    [derived, wrows, selLLO, selOpp, selFLW, snapshot, cRows, C_LIST],
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
      return Number(e.value).toFixed(ind.kind === 'count' ? 0 : 1);
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
          no month has enough data to score
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
          bars = babies started (max {maxS})
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
    if (!monthly.length) {
      return (
        <div className="bg-white border border-gray-200 rounded-xl p-6 text-sm text-gray-500">
          No dated visits to trend.
        </div>
      );
    }
    var months = monthly.map(function (m) {
      return m.month;
    });
    function series(key) {
      return monthly.map(function (m) {
        var e = m[key];
        // An unscored month (n below the minimum denominator) is a GAP, not a zero.
        if (!e || e.value === null || e.band === 'insufficient') return null;
        return e.value;
      });
    }
    var charts = [
      {
        id: 'C09',
        title: 'C09 · % weight data sufficient',
        note: 'of that month\u2019s intake cohort',
        values: series('c09'),
        color: '#0d9488',
        pct: true,
        target: 0.6,
      },
      {
        id: 'C14',
        title: 'C14 · Mortality',
        note: 'PIPN + EHA only \u2014 the credible recorders',
        values: series('mortality'),
        color: '#dc2626',
        pct: true,
        target: 0.04,
      },
      {
        id: 'C15',
        title: 'C15 · Loss to follow-up by day 28',
        note: 'newest month is right-censored, not a collapse',
        values: series('c15'),
        color: '#d97706',
        pct: true,
        target: 0.1,
      },
      {
        id: 'C13',
        title: 'C13 · Mean early growth rate',
        note: 'g/kg/day \u2014 target 15',
        values: series('c13'),
        color: '#4f46e5',
        pct: false,
        target: 15,
      },
    ];
    return (
      <div className="space-y-5">
        <div className="bg-white border border-gray-200 rounded-xl p-4">
          <div className="flex items-baseline justify-between gap-4 flex-wrap">
            <div className="font-medium text-gray-900">
              Monthly trend
              <span className="ml-2 text-sm font-normal text-gray-500">
                {selFLW
                  ? (
                      byFLW.filter(function (f) {
                        return f.key === selFLW;
                      })[0] || {}
                    ).flw +
                    ' — ' +
                    oppLabel(selOpp || (selFLW || '').split(FLW_SEP)[0])
                  : selOpp
                    ? oppLabel(selOpp)
                    : selLLO
                      ? selLLO + ' — all opportunities'
                      : 'Whole programme'}
              </span>
            </div>
            {/* Scope switcher, so you can move between LLOs without hopping tabs. */}
            <div className="flex items-center gap-1 flex-wrap">
              <button
                onClick={function () {
                  setSelLLO(null);
                  setSelOpp(null);
                  setSelFLW(null);
                }}
                className={
                  'px-2 py-1 rounded text-xs border ' +
                  (!selLLO && !selOpp
                    ? 'bg-indigo-50 border-indigo-300 text-indigo-700'
                    : 'border-gray-200 text-gray-600 hover:bg-gray-50')
                }
              >
                Whole programme
              </button>
              {byLLO.map(function (l) {
                var on = selLLO === l.llo && !selOpp;
                return (
                  <button
                    key={l.llo}
                    onClick={function () {
                      setSelLLO(l.llo);
                      setSelOpp(null);
                      setSelFLW(null);
                    }}
                    className={
                      'px-2 py-1 rounded text-xs border ' +
                      (on
                        ? 'bg-indigo-50 border-indigo-300 text-indigo-700'
                        : 'border-gray-200 text-gray-600 hover:bg-gray-50')
                    }
                  >
                    {l.llo}
                  </button>
                );
              })}
              {selFLW && (
                <button
                  onClick={function () {
                    setSelFLW(null);
                  }}
                  className="px-2 py-1 rounded text-xs border border-indigo-300 bg-indigo-50 text-indigo-700"
                >
                  {(
                    byFLW.filter(function (f) {
                      return f.key === selFLW;
                    })[0] || {}
                  ).flw + ' ✕'}
                </button>
              )}
              {selOpp && !selFLW && (
                <button
                  onClick={function () {
                    setSelOpp(null);
                  }}
                  className="px-2 py-1 rounded text-xs border border-indigo-300 bg-indigo-50 text-indigo-700"
                >
                  {oppLabel(selOpp)} ✕
                </button>
              )}
            </div>
          </div>
          <p className="text-xs text-gray-500 mt-2">
            Cohorted on each baby&rsquo;s REGISTRATION DATE, falling back to
            first visit only where a row has none &mdash; this is C03, computed
            rather than proxied now that the clone carries the form&rsquo;s
            hidden calculated fields. Each month&rsquo;s quality and growth
            figures describe the babies who ENTERED that month. A gap in a line
            is a month with too few cases to score, not a zero. Dashed line =
            target.
          </p>
        </div>

        <div className="bg-white border border-gray-200 rounded-xl p-4">
          <div className="text-sm font-medium text-gray-900 mb-1">
            Intake &amp; activity
          </div>
          <VolumeChart
            months={months}
            started={monthly.map(function (m) {
              return m.started;
            })}
            visits={monthly.map(function (m) {
              return m.visits;
            })}
          />
        </div>

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
                <div className="text-xs text-gray-400 mb-1">{c.note}</div>
                <LineChart
                  months={months}
                  values={c.values}
                  color={c.color}
                  pct={c.pct}
                  target={c.target}
                />
              </div>
            );
          })}
        </div>

        <details className="bg-white border border-gray-200 rounded-xl">
          <summary className="px-4 py-3 text-sm font-medium text-gray-900 cursor-pointer">
            Monthly figures (table)
          </summary>
          <div className="overflow-x-auto border-t border-gray-100">
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
      </div>
    );
  }

  // Build the snapshot payload: everything the page DISPLAYS, and nothing it doesn't.
  // Per-case rows are deliberately excluded — 8,656 of them is 7.5 MB on its own, and
  // case-level drill is an investigative tool, not part of a published figure.
  // Is this run built on synthetic clones rather than real programme data? A snapshot
  // run reads the flag captured at freeze time; a live run computes it from scope.
  var runIsSynthetic = React.useMemo(
    function () {
      if (snapshot) return !!(snapshot.meta && snapshot.meta.synthetic);
      var seen = {};
      derived.forEach(function (r) {
        seen[r.opp] = true;
      });
      var opps = Object.keys(seen);
      return opps.length > 0 && opps.every(isSyntheticOpp);
    },
    [derived, snapshot],
  );

  function buildSnapshot() {
    var scopes = { all: monthlyFor(null, null, null) };
    byLLO.forEach(function (l) {
      scopes['llo:' + l.llo] = monthlyFor(l.llo, null, null);
      (l.opps || []).forEach(function (o) {
        scopes['opp:' + o.opp] = monthlyFor(null, o.opp, null);
      });
    });
    return {
      schema: 1,
      generated_at: new Date().toISOString(),
      // The SQL-computed series, so the snapshot run does not have to re-query for
      // it. Null when the operator never ran the tab -- captured rather than
      // required, because a snapshot without it is still a valid snapshot.
      nSeries:
        nSeries.status === 'ready' && nSeries.rows.length
          ? {
              rows: nSeries.rows,
              measures: nSeries.measures,
              opportunity_ids: nSeries.opportunity_ids || [],
              generated_at: new Date().toISOString(),
            }
          : null,
      // The display contract the numbers below were graded with. The render reads
      // `snapshot.cMeasures` and nothing wrote it, so a snapshot run fetched the
      // CURRENT catalog instead -- meaning a later change to a band threshold
      // would silently re-grade a published snapshot, and a green chip could turn
      // red with no edit to the run. A snapshot is "the numbers as published",
      // which has to include what published them. Also makes a snapshot run
      // genuinely zero-query rather than one cheap call away from it.
      cMeasures: cSeries.measures || [],
      mortalityCredible: mortalityCredible,
      programInd: programInd,
      byLLO: byLLO.map(function (l) {
        return {
          llo: l.llo,
          rows: [],
          ind: l.ind,
          reds: l.reds,
          yellows: l.yellows,
          opps: (l.opps || []).map(function (o) {
            return {
              opp: o.opp,
              llo: o.llo,
              rows: [],
              ind: o.ind,
              n: (o.rows || []).length,
            };
          }),
        };
      }),
      byOpp: byOpp.map(function (o) {
        return {
          opp: o.opp,
          llo: o.llo,
          rows: [],
          ind: o.ind,
          n: (o.rows || []).length,
        };
      }),
      byFLW: byFLW.map(function (f) {
        return {
          key: f.key,
          opp: f.opp,
          flw: f.flw,
          llo: f.llo,
          rows: [],
          ind: f.ind,
          reds: f.reds,
          yellows: f.yellows,
          n: (f.rows || []).length,
        };
      }),
      monthly: scopes.all,
      monthlyByScope: scopes,
      meta: {
        cases: derived.length,
        visits: wrows.length,
        opportunities: byOpp.length,
        llos: byLLO.length,
        synthetic: runIsSynthetic,
      },
    };
  }

  // Staged snapshot: the aggregates have been written to run state but the run is
  // still in_progress. `view.state` reflects the persisted write, so its presence is
  // proof the write landed.
  var staged =
    view && view.state && view.state.snapshot ? view.state.snapshot : null;

  // Freezing is TWO steps on purpose. onUpdateState is fire-and-forget, so pairing it
  // with view.complete() behind one click is a race — and complete() won it, producing
  // a completed run whose snapshot captured an empty state. Waiting for the write to
  // round-trip through view.state removes the guess entirely.
  //
  // SCOPE OF "permanent", because this comment has been misread twice: a completed run
  // cannot be RE-OPENED, and that is all. Freezing is not a one-way door and it is not
  // a publishing act. A workflow is MEANT to carry many runs — take as many snapshots
  // as you like — and a bad one is deleted outright via
  // `DELETE /labs/workflow/api/run/<run_id>/delete/` (workflow/urls.py, api_delete_run).
  // Read as "irreversible", this sentence has twice stopped an agent from freezing a
  // run at all and made it ask a human for permission it did not need.
  function stageSnapshot() {
    if (!derived.length) {
      window.alert(
        'No case data has loaded yet — a snapshot taken now would be empty, and a ' +
          'completed run cannot be reopened (you would have to delete it and take ' +
          'another). Wait for the indicators to appear.',
      );
      return;
    }
    if (!(nSeries.status === 'ready' && nSeries.rows.length)) {
      if (
        !window.confirm(
          'The Demo metrics (SQL) tab has not been run, so it will not be part ' +
            'of this snapshot and will keep querying live (and can go stale). ' +
            'Run it first for a fully snapshot dashboard.\n\nSnapshot anyway?',
        )
      )
        return;
    }
    onUpdateState({ snapshot: buildSnapshot() });
  }

  function freezeRun() {
    if (!view || !view.complete) {
      window.alert('This run does not support snapshots yet.');
      return;
    }
    if (!staged) {
      window.alert('Prepare the snapshot first.');
      return;
    }
    view.complete({
      confirm:
        'Freeze this run? The figures become read-only and load from the snapshot ' +
        'instead of recomputing. Re-running later creates a new run; this one stays ' +
        'in the history.',
    });
  }

  return (
    <div className="p-6 space-y-5">
      {snapshot && (
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
          <span className="font-medium">Frozen run.</span> These figures are the
          snapshot taken{' '}
          {view.asOf ? String(view.asOf).slice(0, 16).replace('T', ' ') : ''} —{' '}
          {(snapshot.meta || {}).cases} cases and {(snapshot.meta || {}).visits}{' '}
          visits across {(snapshot.meta || {}).opportunities} opportunities.
          They load instantly and cannot move. Per-case detail is not part of a
          snapshot; start a new run for that.
        </div>
      )}

      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">KMC Indicators</h1>
          {/* "evaluated live" was true when this browser computed the
              indicators. It no longer does, and on a snapshot run the claim sat
              directly under a banner saying the figures cannot move. */}
          <p className="text-sm text-gray-500 mt-1">
            The kmc_metrics_framework registry, compiled to SQL and evaluated
            server-side
            {snapshot ? ' — these figures are from the snapshot' : ''}. Case
            properties come from the entity pipeline; only the weight series is
            derived in this browser. Click any row to drill Programme → LLO →
            opportunity → cases.
          </p>
        </div>
        {!snapshot && view && view.complete && (
          <div className="shrink-0 flex items-center gap-2">
            {staged && (
              <span className="text-xs text-gray-500">
                snapshot prepared · {(staged.meta || {}).cases} cases
              </span>
            )}
            <button
              onClick={staged ? freezeRun : stageSnapshot}
              disabled={!derived.length}
              className={
                'px-3 py-2 rounded-lg text-sm border ' +
                (!derived.length
                  ? 'border-gray-200 bg-gray-50 text-gray-400 cursor-not-allowed'
                  : staged
                    ? 'border-emerald-300 bg-emerald-50 text-emerald-700 hover:bg-emerald-100'
                    : 'border-indigo-300 bg-indigo-50 text-indigo-700 hover:bg-indigo-100')
              }
              title={
                !derived.length
                  ? 'Waiting for case data to load'
                  : staged
                    ? 'Freeze the prepared snapshot — figures become read-only'
                    : 'Compute the snapshot from what is on screen'
              }
            >
              {!derived.length
                ? 'Freeze this run (loading…)'
                : staged
                  ? 'Freeze this run'
                  : '1. Prepare snapshot'}
            </button>
          </div>
        )}
      </div>

      <div className="flex items-center gap-1 border-b border-gray-200">
        {[
          ['indicators', 'Indicators'],
          ['trends', 'Monthly trend'],
          ['nseries', 'Demo metrics (SQL)'],
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

      {tab === 'nseries' && (
        <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-gray-100">
            <div className="font-medium text-gray-900">
              Demo metrics — computed in SQL
            </div>
            <div className="text-xs text-gray-500 mt-1">
              The indicator registry evaluated server-side rather than in this
              browser. Maturity gates are 28/42/90 and the growth bands are
              birth-weight-band specific, so these deliberately differ from the
              Indicators tab where the spec differs.
            </div>
            <div className="mt-2 flex items-center gap-2 flex-wrap">
              <button
                type="button"
                onClick={loadNSeries}
                disabled={nSeries.status === 'loading'}
                className={
                  'px-3 py-1.5 rounded text-sm font-medium ' +
                  (nSeries.status === 'loading'
                    ? 'bg-gray-200 text-gray-500'
                    : 'bg-indigo-600 text-white hover:bg-indigo-700')
                }
              >
                {nSeries.status === 'loading'
                  ? 'Running the query…'
                  : nSeries.status === 'ready'
                    ? 'Re-run'
                    : 'Run'}
              </button>
              {nSeries.status === 'ready' &&
                [
                  ['programme', 'Programme'],
                  ['opportunity', 'By opportunity'],
                  ['flw', 'By worker'],
                ].map(function (sc) {
                  var on = nScope === sc[0];
                  return (
                    <button
                      key={sc[0]}
                      type="button"
                      onClick={function () {
                        setNScope(sc[0]);
                      }}
                      className={
                        'px-2.5 py-1 rounded text-xs ' +
                        (on
                          ? 'bg-indigo-100 text-indigo-800 font-medium'
                          : 'bg-gray-100 text-gray-600 hover:bg-gray-200')
                      }
                    >
                      {sc[1]}
                    </button>
                  );
                })}
            </div>
          </div>

          {nSeries.status === 'error' && (
            <div className="px-4 py-3 text-sm text-red-700">
              {nSeries.error}
            </div>
          )}

          {/* Two different lies the numbers cannot tell you about themselves.
              COLD: every count is zero, which reads as a programme with no
              babies. PARTIAL is worse and was silent -- the total is a real
              number computed over only the opportunities that happen to be
              cached, so it looks entirely credible while understating the
              cohort. Neither is visible in the figures. */}
          {nSeries.status === 'ready' &&
            (nSeries.coldCache || nSeries.partialCache) && (
              <div className="px-4 py-3 text-sm bg-amber-50 text-amber-900 border-b border-amber-100">
                <span className="font-medium">
                  {nSeries.coldCache
                    ? 'Every metric is zero because nothing is cached \u2014 not because the programme has no data.'
                    : 'These totals cover only part of the cohort.'}
                </span>{' '}
                {nSeries.coldHint}
              </div>
            )}

          {nSeries.status === 'ready' &&
            (function () {
              var measures = nSeries.measures.filter(function (m) {
                return m.id && m.id.charAt(0) === 'n';
              });
              var rows = nSeries.rows.filter(function (r) {
                return (r.scope || 'programme') === nScope;
              });
              if (!rows.length) {
                return (
                  <div className="px-4 py-3 text-sm text-gray-500">
                    No rows at this scope.
                  </div>
                );
              }
              // One column per metric, one row per entity in the scope. At
              // programme scope that is a single row, which reads as the topline.
              // FLW usernames are only unique WITHIN an opportunity -- the synthetic
              // cohort reuses flw_001.. across all eleven -- so the username alone
              // puts two different people on two rows reading the same name, with
              // nothing on screen to tell them apart. Measured live: flw_001
              // appeared twice in the first seven rows. The rollup elsewhere in this
              // file keys on opp+username for exactly this reason; the label has to
              // carry the same context or the drill points at the wrong person.
              var labelFor = function (r) {
                if (nScope === 'opportunity') return oppLabel(r.opportunity_id);
                if (nScope === 'flw')
                  return (
                    (r.username || '(unassigned)') +
                    ' \u00b7 ' +
                    oppLabel(r.opportunity_id)
                  );
                return 'All opportunities';
              };

              // At PROGRAMME scope there is exactly one row, and a 14-column table
              // to show a single record puts all the weight on the header and none
              // on the number -- measured live: the table clipped at the viewport
              // edge with two thirds of the page empty below it. Cards give the
              // topline the hierarchy it should have; the table stays for the
              // scopes that genuinely have many rows.
              if (nScope === 'programme') {
                var pr = rows[0];
                var counts = measures.filter(function (m) {
                  return m.unit === 'n' && !m.bands;
                });
                var rest = measures.filter(function (m) {
                  return !(m.unit === 'n' && !m.bands);
                });
                var TONE = {
                  green: 'border-green-200 bg-green-50 text-green-900',
                  yellow: 'border-amber-200 bg-amber-50 text-amber-900',
                  red: 'border-red-200 bg-red-50 text-red-900',
                  unbanded: 'border-gray-200 bg-white text-gray-900',
                  nodata: 'border-gray-200 bg-gray-50 text-gray-400',
                  insufficient: 'border-gray-200 bg-gray-50 text-gray-400',
                };
                var card = function (m, big) {
                  var c = nCell(m, pr);
                  var derived =
                    m.bands_source &&
                    m.bands_source.indexOf('PROVISIONAL') !== -1;
                  return (
                    <div
                      key={m.id}
                      className={
                        'rounded-lg border p-3 ' +
                        (TONE[c.band] || TONE.unbanded)
                      }
                      title={m.bands_source || 'no band defined'}
                    >
                      <div
                        className={
                          (big ? 'text-3xl' : 'text-2xl') +
                          ' font-semibold leading-none'
                        }
                      >
                        {c.text}
                      </div>
                      <div className="text-xs mt-1.5 leading-snug opacity-80">
                        {m.title}
                        {derived ? (
                          <span
                            className="text-amber-600"
                            title="band derived, not stated by the spec"
                          >
                            {' *'}
                          </span>
                        ) : (
                          ''
                        )}
                      </div>
                      <div className="text-[11px] mt-1 opacity-50">
                        {c.den === null || c.den === undefined
                          ? '\u2014'
                          : 'n = ' + c.den}
                      </div>
                    </div>
                  );
                };
                return (
                  <div className="p-4">
                    <div className="grid grid-cols-4 gap-3 mb-3">
                      {counts.map(function (m) {
                        return card(m, true);
                      })}
                    </div>
                    <div className="grid grid-cols-5 gap-3">
                      {rest.map(function (m) {
                        return card(m, false);
                      })}
                    </div>
                    <div className="mt-4 text-xs text-gray-400">
                      Across {pr.n_cases} cases in{' '}
                      {(nSeries.opportunity_ids || []).length || 11}{' '}
                      opportunities. Hover a card for where its band came from.{' '}
                      <span className="text-amber-600">*</span> marks a band
                      DERIVED from the workbook or from the spec's own
                      expected-answers table rather than stated by the spec.
                    </div>
                  </div>
                );
              }
              return (
                <div className="overflow-x-auto">
                  <table className="min-w-full text-xs">
                    <thead className="bg-gray-50 text-gray-500">
                      <tr>
                        <th className="px-3 py-2 text-left sticky left-0 bg-gray-50">
                          {nScope === 'flw'
                            ? 'Worker'
                            : nScope === 'opportunity'
                              ? 'Opportunity'
                              : 'Scope'}
                        </th>
                        <th className="px-2 py-2 text-right">Cases</th>
                        {measures.map(function (m) {
                          return (
                            <th
                              key={m.id}
                              className="px-2 py-2 text-right whitespace-nowrap"
                              title={
                                (m.bands_source || 'no band defined') +
                                (m.min_denominator
                                  ? ' · min n ' + m.min_denominator
                                  : '')
                              }
                            >
                              {m.title}
                              {m.bands_source &&
                              m.bands_source.indexOf('PROVISIONAL') !== -1 ? (
                                <span
                                  className="text-amber-600"
                                  title="band is derived, not stated by the spec"
                                >
                                  {' *'}
                                </span>
                              ) : (
                                ''
                              )}
                            </th>
                          );
                        })}
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map(function (r, ri) {
                        return (
                          <tr key={ri} className="border-t border-gray-100">
                            <td className="px-3 py-2 font-medium text-gray-900 sticky left-0 bg-white">
                              {labelFor(r)}
                            </td>
                            <td className="px-2 py-2 text-right text-gray-500">
                              {r.n_cases}
                            </td>
                            {measures.map(function (m) {
                              var c = nCell(m, r);
                              return (
                                <td key={m.id} className="px-2 py-2 text-right">
                                  <span
                                    className={
                                      'inline-block px-1.5 py-0.5 rounded ' +
                                      (N_BAND_CLASS[c.band] ||
                                        N_BAND_CLASS.unbanded)
                                    }
                                    title={
                                      c.den === null || c.den === undefined
                                        ? 'no denominator'
                                        : 'n = ' + c.den
                                    }
                                  >
                                    {c.text}
                                  </span>
                                </td>
                              );
                            })}
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                  <div className="px-4 py-2 text-xs text-gray-400 border-t border-gray-100">
                    <span className="text-gray-500">
                      {rows.length} row{rows.length === 1 ? '' : 's'} · scroll
                      sideways for the rest of the {measures.length} metrics
                      &rarr;
                    </span>{' '}
                    Hover a value for its denominator, or a column for where its
                    band came from. <span className="text-amber-600">*</span>{' '}
                    marks a band DERIVED from the workbook or from the spec's
                    own expected-answers table rather than stated by the spec —
                    those are the ones worth replacing with real ranges.
                  </div>
                </div>
              );
            })()}
        </div>
      )}

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
          {cSeries.status !== 'ready' && (
            <div
              className={
                'px-4 py-3 text-sm border rounded ' +
                (cSeries.status === 'error'
                  ? 'bg-red-50 text-red-900 border-red-200'
                  : 'bg-slate-50 text-slate-700 border-slate-200')
              }
            >
              {cSeries.status === 'error' ? (
                <span>
                  <span className="font-medium">
                    Indicators could not be computed.
                  </span>{' '}
                  {cSeries.error}
                </span>
              ) : (
                <span>
                  <span className="font-medium">
                    Computing indicators in SQL…
                  </span>{' '}
                  one pass over the whole cohort, usually ~30 seconds. Values
                  below stay blank until it returns.
                </span>
              )}
            </div>
          )}

          {cSeries.status === 'ready' &&
            (cSeries.coldCache || cSeries.partialCache) && (
              <div className="px-4 py-3 text-sm bg-amber-50 text-amber-900 border border-amber-200 rounded">
                <span className="font-medium">
                  {cSeries.coldCache
                    ? 'Every metric is blank because nothing is cached \u2014 not because the programme has no data.'
                    : 'These totals cover only part of the cohort.'}
                </span>{' '}
                {cSeries.coldHint}
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
                    Program row 8 — LLOs with any red indicator
                  </div>
                </div>
                <div className="bg-white border border-gray-200 rounded-xl p-4">
                  <div className="text-xs text-gray-500">Total started</div>
                  <div className="text-2xl font-semibold mt-1">
                    {fmt(indOf('C02'), entryOf(programInd, 'C02'))}
                  </div>
                  <div className="text-xs text-gray-400 mt-1">
                    Program row 2 · Started cases (C02) against 25,000 by
                    Q1-2027
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
                    Program row 3 · % weight data sufficient (C09), pooled
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
                    Program row 5 · Mortality (C14), two-sided ·{' '}
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
              var caseRows = byOpp.filter(function (o) {
                return o.opp === selOpp;
              })[0].rows;
              if (selFLW) {
                var f0 = byFLW.filter(function (f) {
                  return f.key === selFLW;
                })[0];
                if (f0) caseRows = f0.rows;
              }
              if (selInd) {
                var indSel = C_LIST.filter(function (i) {
                  return i.id === selInd;
                })[0];
                if (indSel && indSel.den)
                  caseRows = caseRows.filter(indSel.den);
              }
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
                        onPick={function (id) {
                          setSelInd(id === selInd ? null : id);
                        }}
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
                      {snapshot
                        ? 'Cases — not captured in a snapshot'
                        : 'Cases '}
                      {!snapshot && selInd
                        ? '\u2014 in the denominator of ' + selInd
                        : ''}
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
                            (selInd
                              ? ''
                              : ' \u2014 click an indicator above to filter')}
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
                This run is built on synthetic clones. The clone now carries the
                form&rsquo;s hidden calculated fields, so an indicator that
                reads one is no longer blank here for that reason. Identifiers
                &mdash; names, phones, addresses, GPS, free text &mdash; are
                deliberately never reproduced, so anything derived from those
                still reads empty. Confirm against a run on live data before
                treating any blank below as something the real programmes fail
                to collect.
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
