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
  var frozen =
    view && view.isCompleted && view.state && view.state.frozen
      ? view.state.frozen
      : null;

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

  var MIN_DEN = 25;

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
      days_discharge_to_reg: false,
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
      days_discharge_to_reg: false,
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
      days_discharge_to_reg: false,
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
      days_discharge_to_reg: false,
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
  function anyRecorded(field, rows) {
    var present = false;
    for (var i = 0; i < rows.length; i++) {
      var v = rows[i][field];
      if (v === undefined) continue;
      present = true;
      if (ZERO_IS_ABSENT[field]) {
        if (v) return true;
      } else if (v !== null && v !== '' && !(Array.isArray(v) && !v.length)) {
        return true;
      }
    }
    // Fail OPEN when the field is absent from every row: that means the gate is
    // misconfigured (a renamed property), not that the programme collected nothing.
    // Blanking a real indicator on our own wiring error is the worse failure.
    return !present;
  }
  // 'ok' | 'notinapp' | 'unrecorded'
  function inputState(indId, rows, opps) {
    var need = IND_INPUTS[indId];
    if (!need) return 'ok';
    for (var i = 0; i < need.length; i++) {
      if (!anyAsks(need[i], opps)) return 'notinapp';
      if (!anyRecorded(need[i], rows)) return 'unrecorded';
    }
    return 'ok';
  }

  // ── Targets & settings tab (the workbook's typed human inputs) ────────────
  // These are GATES, not decoration. "Mortality recording credible" is TRUE for
  // PIPN and EHA only, and the LLO-indicator sheet says mortality is shown only
  // where recording is credible — so publishing a red mortality band for an LLO
  // that does not credibly record deaths is a false alarm, which is precisely
  // what the flag exists to prevent (the source doc: "only PIPN and EHA record
  // deaths credibly"; GHI 675 records zero discharges at all).
  var MORTALITY_CREDIBLE = { PIPN: true, EHA: true };
  var COMPLETION_CREDIBLE = { GHI: false };
  var MONTHLY_TARGET = { PIPN: 600 }; // per LLO per month
  var TOTAL_STARTED_TARGET = { PIPN: 50000, ALL: 25000 }; // ALL = 25,000 by 2027-Q1
  var SCALE_TIER_CASES_PER_MONTH = 1000;
  function credibleFor(indId, llo) {
    if (indId === 'C14') return llo === null || !!MORTALITY_CREDIBLE[llo];
    if (indId === 'C18' || indId === 'C22')
      return llo === null || COMPLETION_CREDIBLE[llo] !== false;
    return true;
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
    [cases, wrows],
  );

  // ── The registry (Case-indicators tab, verbatim definitions) ──────────────
  // num/den are predicates over a derived case row. `value` returns a ratio or a
  // mean. bands: [green, yellow] as thresholds with a direction, or null = unbanded.
  var IND = [
    {
      id: 'C01',
      cat: 'Scale',
      name: 'Registered cases',
      prom: 'Top',
      unit: 'n',
      den: function () {
        return true;
      },
      num: function (r) {
        return r.registered;
      },
      kind: 'count',
    },
    {
      id: 'C02',
      cat: 'Scale',
      name: 'Started cases',
      prom: 'Top',
      unit: 'n',
      den: function () {
        return true;
      },
      num: function (r) {
        return r.started;
      },
      kind: 'count',
    },
    {
      id: 'C05',
      cat: 'Scale',
      name: 'Cumulative SVNs reached',
      prom: 'Top',
      unit: 'n',
      den: function () {
        return true;
      },
      // Reached = any contact at all, which is every case in this table. Distinct from
      // C01 (a registration form exists) and C02 (a follow-up happened); it was a copy
      // of C02, so two of the three scale numbers said the same thing.
      num: function () {
        return true;
      },
      kind: 'count',
    },
    {
      id: 'C06',
      cat: 'Scale',
      name: 'Mean visits per case',
      prom: 'Lower',
      unit: 'n',
      den: function (r) {
        return r.started;
      },
      mean: function (r) {
        return r.num_visits;
      },
      kind: 'mean',
    },

    {
      id: 'C07',
      cat: 'Program quality',
      name: '% weight_gain_data_computable',
      prom: 'Lower',
      unit: '%',
      den: function (r) {
        return r.eligible && !r.early_exit;
      },
      num: function (r) {
        return r.weight_computable;
      },
      dir: 'higher',
      bands: [0.75, 0.55],
    },
    {
      id: 'C08',
      cat: 'Program quality',
      name: '% weight_gain_data_consistent',
      prom: 'Lower',
      unit: '%',
      den: function (r) {
        return r.weight_computable;
      },
      num: function (r) {
        return r.weight_consistent;
      },
      dir: 'higher',
      bands: [0.8, 0.6],
    },
    {
      id: 'C09',
      cat: 'Program quality',
      name: '% weight_gain_data_sufficient',
      prom: 'Top',
      unit: '%',
      den: function (r) {
        return r.eligible && !r.early_exit;
      },
      num: function (r) {
        return r.weight_gain_data_sufficient;
      },
      dir: 'higher',
      bands: [0.6, 0.4],
    },
    {
      id: 'C10',
      cat: 'Program quality',
      name: '% plausible growth',
      prom: 'Top',
      unit: '%',
      den: function (r) {
        return r.weight_gain_data_sufficient;
      },
      num: function (r) {
        return r.growth_class === 'plausible';
      },
      dir: 'higher',
      bands: [0.7, 0.5],
      tbdInput: 'growth_class thresholds are TBD in the workbook',
    },
    {
      id: 'C11',
      cat: 'Program quality',
      name: '% slow growth',
      prom: 'Top',
      unit: '%',
      den: function (r) {
        return r.weight_gain_data_sufficient;
      },
      num: function (r) {
        return r.growth_class === 'slow';
      },
      dir: 'lower',
      bands: [0.15, 0.3],
      tbdInput: 'growth_class thresholds are TBD in the workbook',
    },
    {
      id: 'C12',
      cat: 'Program quality',
      name: '% fast growth',
      prom: 'Lower',
      unit: '%',
      den: function (r) {
        return r.weight_gain_data_sufficient;
      },
      num: function (r) {
        return r.growth_class === 'fast';
      },
      dir: 'mid',
      bands: [0.15, 0.3],
      tbdInput: 'growth_class thresholds are TBD in the workbook',
    },
    {
      id: 'C13',
      cat: 'Program quality',
      name: 'Mean early growth rate',
      prom: 'Top',
      unit: 'g/kg/d',
      den: function (r) {
        return r.weight_gain_data_sufficient;
      },
      mean: function (r) {
        return r.early_g_per_kg_day;
      },
      kind: 'mean',
      dir: 'higher',
      bands: [15, 13],
    },

    {
      id: 'C14',
      cat: 'Performance',
      name: 'Mortality',
      prom: 'Top',
      // This row is ALL LLOs pooled. The headline card above is gated to the
      // credible recorders, so the two legitimately differ and the page must say
      // which is which — an unlabelled pair reads as a bug.
      scopeNote:
        'All LLOs pooled, including the four the workbook says do not record deaths credibly. The headline card above is PIPN + EHA only, and reads higher because non-recorders add denominator without deaths.',
      unit: '%',
      den: function (r) {
        return r.eligible && r.outcome_known;
      },
      num: function (r) {
        return r.died;
      },
      dir: 'mid2',
      bands: [
        [0.04, 0.12],
        [0.02, 0.16],
      ],
    },
    {
      id: 'C15',
      cat: 'Performance',
      name: 'Loss to follow-up by day 28',
      prom: 'Top',
      unit: '%',
      flw: true,
      den: function (r) {
        return r.eligible;
      },
      num: function (r) {
        return !r.outcome_known;
      },
      dir: 'lower',
      bands: [0.1, 0.25],
    },
    {
      id: 'C16',
      cat: 'Performance',
      name: '% enrolled within 3 days',
      prom: 'Top',
      unit: '%',
      den: function (r) {
        return r.started && typeof r.days_discharge_to_reg === 'number';
      },
      num: function (r) {
        return r.enrolled_within_3d;
      },
      dir: 'higher',
      bands: [0.5, 0.3],
    },
    {
      id: 'C17',
      cat: 'Performance',
      name: 'Median days to enrolment',
      prom: 'Lower',
      unit: 'd',
      den: function (r) {
        return r.started && typeof r.days_discharge_to_reg === 'number';
      },
      median: function (r) {
        return r.days_discharge_to_reg;
      },
      kind: 'median',
      dir: 'lower',
      bands: [3, 7],
    },
    {
      id: 'C19',
      cat: 'Performance',
      // Denominator is babies WITH a danger sign, not all eligible babies. Against
      // all-eligible this read 30.7% while danger-sign incidence (C20) read 7.0% —
      // i.e. more babies referred for danger signs than had one, which cannot happen.
      // `referred` counts referrals for any reason, so the fix is the denominator:
      // of the babies who had a danger sign, how many were referred.
      name: '% of danger-sign cases referred',
      prom: 'Top',
      unit: '%',
      den: function (r) {
        return r.eligible && r.ever_danger_sign;
      },
      num: function (r) {
        return r.referred;
      },
      dir: 'mid',
      bands: null,
    },
    {
      id: 'C20',
      cat: 'Performance',
      name: 'Danger-sign incidence',
      prom: 'Lower',
      unit: '%',
      den: function (r) {
        return r.eligible;
      },
      num: function (r) {
        return r.ever_danger_sign;
      },
      dir: 'mid',
      bands: null,
    },
    {
      id: 'C21',
      cat: 'Performance',
      name: 'Self-referrals per 100 cases',
      prom: 'Lower',
      unit: '/100',
      den: function (r) {
        return r.eligible;
      },
      mean: function (r) {
        return r.self_referral_count * 100;
      },
      kind: 'mean',
      bands: null,
    },
    {
      id: 'C23',
      cat: 'Performance',
      name: 'Mean skin-to-skin hours',
      prom: 'Lower',
      unit: 'h',
      den: function (r) {
        return r.eligible;
      },
      mean: function (r) {
        return r.kmc_hours_mean;
      },
      kind: 'mean',
      bands: null,
    },
    {
      id: 'C24',
      cat: 'Performance',
      name: 'Mean visits per started case',
      prom: 'Lower',
      unit: 'n',
      flw: true,
      // Says "per started case", so it must use started — it was using `eligible`,
      // which is why it differed from C06 only by the 28-day eligibility filter.
      den: function (r) {
        return r.started;
      },
      mean: function (r) {
        return r.num_visits;
      },
      kind: 'mean',
      bands: null,
    },

    {
      id: 'C28',
      cat: 'Data quality',
      name: 'Birth-copy rate',
      prom: 'Top',
      unit: '%',
      flw: true,
      den: function (r) {
        return r.enrollment_is_birth_copy !== null;
      },
      num: function (r) {
        return r.enrollment_is_birth_copy;
      },
      dir: 'lower',
      bands: [0.1, 0.2],
    },
    {
      id: 'C31',
      cat: 'Data quality',
      name: 'Weight rounding rate',
      prom: 'Top',
      unit: '%',
      flw: true,
      denSum: function (r) {
        return r.n_weight_readings;
      },
      numSum: function (r) {
        return r.n_weights_round_100;
      },
      kind: 'sumratio',
      dir: 'lower',
      bands: null,
      minDen: 100,
    },
  ];
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

  function evaluate(ind, rows) {
    var den = rows.filter(
      ind.den ||
        function () {
          return true;
        },
    );
    var out = { id: ind.id, n: den.length, value: null, band: 'nodata' };
    if (ind.kind === 'sumratio') {
      var ds = rows.reduce(function (a, r) {
        return a + (ind.denSum(r) || 0);
      }, 0);
      var ns = rows.reduce(function (a, r) {
        return a + (ind.numSum(r) || 0);
      }, 0);
      out.n = ds;
      out.value = ds ? ns / ds : null;
    } else if (ind.kind === 'count') {
      out.value = den.filter(ind.num).length;
      out.n = den.length;
    } else if (ind.kind === 'mean') {
      var vals = den.map(ind.mean).filter(function (x) {
        return typeof x === 'number' && !isNaN(x);
      });
      out.n = vals.length;
      out.value = vals.length
        ? vals.reduce(function (a, b) {
            return a + b;
          }, 0) / vals.length
        : null;
    } else if (ind.kind === 'median') {
      var mv = den
        .map(ind.median)
        .filter(function (x) {
          return typeof x === 'number' && !isNaN(x);
        })
        .sort(function (a, b) {
          return a - b;
        });
      out.n = mv.length;
      out.value = mv.length ? mv[Math.floor(mv.length / 2)] : null;
    } else {
      out.value = den.length ? den.filter(ind.num).length / den.length : null;
    }
    var minDen = ind.minDen || MIN_DEN;
    if (out.value === null) {
      out.band = 'nodata';
      return out;
    }
    if (out.n < minDen) {
      out.band = 'insufficient';
      return out;
    }
    if (!ind.bands) {
      out.band = 'unbanded';
      return out;
    }
    var x = out.value,
      b = ind.bands;
    if (ind.dir === 'higher')
      out.band = x >= b[0] ? 'green' : x >= b[1] ? 'yellow' : 'red';
    else if (ind.dir === 'lower')
      out.band = x <= b[0] ? 'green' : x <= b[1] ? 'yellow' : 'red';
    else if (ind.dir === 'mid2')
      out.band =
        x >= b[0][0] && x <= b[0][1]
          ? 'green'
          : x >= b[1][0] && x <= b[1][1]
          ? 'yellow'
          : 'red';
    else out.band = 'unbanded';
    return out;
  }

  function evalAll(rows, llo, opps) {
    var m = {};
    var scope =
      opps ||
      rows
        .map(function (r) {
          return r.opp;
        })
        .filter(function (v, i, a) {
          return a.indexOf(v) === i;
        });
    IND.forEach(function (i) {
      var st = inputState(i.id, rows, scope);
      if (st !== 'ok') {
        m[i.id] = {
          id: i.id,
          n: 0,
          value: null,
          band: st === 'notinapp' ? 'notinapp' : 'unrecorded',
        };
        return;
      }
      if (!credibleFor(i.id, llo === undefined ? null : llo)) {
        // Still compute it, but mark it. A blank cell reads as "no data", which is
        // wrong and actively confusing — these LLOs DO record deaths, the workbook
        // just says not credibly. Showing the figure greyed with the caveat lets a
        // reader see both the number and why it is not to be trusted, and makes the
        // under-recording visible: pooling every LLO reads LOWER than the credible
        // recorders alone, because non-recorders add denominator without deaths.
        // (Deliberately no figures here — an earlier version hardcoded them, they
        // went stale as the cohort changed, and they were quoted as current.)
        var ne = evaluate(i, rows);
        ne.band = 'notcredible';
        m[i.id] = ne;
        return;
      }
      m[i.id] = evaluate(i, rows);
    });
    return m;
  }

  // ── Roll up: opp → LLO → program ─────────────────────────────────────────
  var byOpp = React.useMemo(
    function () {
      if (frozen) return frozen.byOpp || [];
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
          ind: evalAll(g[o], llo),
        };
      });
    },
    [derived, frozen],
  );

  var byLLO = React.useMemo(
    function () {
      if (frozen) return frozen.byLLO || [];
      var g = {};
      derived.forEach(function (r) {
        (g[r.llo] = g[r.llo] || []).push(r);
      });
      return Object.keys(g)
        .sort()
        .map(function (l) {
          var rows = g[l];
          var ind = evalAll(rows, l);
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
    [derived, byOpp, frozen],
  );

  // Separator for the composite FLW key. NOT '\u0000': a NUL byte is legal in a JS
  // string but Postgres cannot store it in a JSON column, so freezing a run died with
  // `UntranslatableCharacter: \u0000 cannot be converted to text` — the key only
  // became unstorable at the moment it was persisted, long after it was built.
  var FLW_SEP = '::';

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
  // frozen run is a snapshot of a past period, and a trailing-30-days window
  // would silently audit nothing on one.
  function flwDateRange(f) {
    var ds = (f.rows || [])
      .map(function (r) {
        return r.first_visit || r.last_visit;
      })
      .filter(Boolean)
      .sort();
    return ds.length
      ? {
          start: String(ds[0]).slice(0, 10),
          end: String(ds[ds.length - 1]).slice(0, 10),
        }
      : null;
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

  var byFLW = React.useMemo(
    function () {
      if (frozen) return frozen.byFLW || [];
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
          var ind = evalAll(rows, llo, [opp]);
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
    [derived, frozen],
  );

  var programInd = React.useMemo(
    function () {
      if (frozen) return frozen.programInd || {};
      return evalAll(derived);
    },
    [derived, frozen],
  );

  // Programme mortality, restricted to the LLOs the workbook accepts as credible
  // recorders of death. The card pooled every LLO while the table below it showed
  // "recording not credible" for four of six — so the headline number was built on
  // exactly the data the same dashboard declined to show, and it read LOWER than
  // reality because non-recorders contribute denominator without deaths.
  var mortalityCredible = React.useMemo(
    function () {
      var rows = derived.filter(function (r) {
        return MORTALITY_CREDIBLE[r.llo];
      });
      var llos = Object.keys(MORTALITY_CREDIBLE).filter(function (l) {
        return byLLO.some(function (x) {
          return x.llo === l;
        });
      });
      return {
        ind: rows.length
          ? evaluate(
              IND.filter(function (i) {
                return i.id === 'C14';
              })[0],
              rows,
            )
          : null,
        llos: llos,
        of: byLLO.length,
      };
    },
    [derived, byLLO],
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
  // ══ N-series (Neal's demo compute spec), served by the semantic layer ══════
  // Everything else on this screen is computed in the browser from pipeline rows.
  // These come from SQL: the registry compiles to one GROUPING SETS query and runs
  // server-side, which is the only version where the scopes below cost one pass
  // instead of three. Fetched ON DEMAND rather than with the page -- it is a real
  // query against the visit cache, and the other tabs must not pay for it.
  var sN = React.useState({ status: 'idle', rows: [], measures: [] });
  var nSeries = sN[0],
    setNSeries = sN[1];

  // The DEFINITION id, which is NOT the run id. `definition.id` is not populated in
  // this render context -- measured live: the panel rendered, fetched nothing, and
  // reported "no workflow id" -- and instance.id is the RUN. The path carries it, so
  // fall through to that rather than guessing at another prop shape.
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
        '/semantic/?series=N&scopes=programme,opportunity,flw',
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
          coldCache: data.cold_cache || false,
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
      if (frozen) return frozen.byFLW || [];
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
          var ind = evalAll(rows, llo, [opp]);
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
    [derived, frozen],
  );

  var programInd = React.useMemo(
    function () {
      if (frozen) return frozen.programInd || {};
      return evalAll(derived);
    },
    [derived, frozen],
  );

  // Programme mortality, restricted to the LLOs the workbook accepts as credible
  // recorders of death. The card pooled every LLO while the table below it showed
  // "recording not credible" for four of six — so the headline number was built on
  // exactly the data the same dashboard declined to show, and it read LOWER than
  // reality because non-recorders contribute denominator without deaths.
  var mortalityCredible = React.useMemo(
    function () {
      var rows = derived.filter(function (r) {
        return MORTALITY_CREDIBLE[r.llo];
      });
      var llos = Object.keys(MORTALITY_CREDIBLE).filter(function (l) {
        return byLLO.some(function (x) {
          return x.llo === l;
        });
      });
      return {
        ind: rows.length
          ? evaluate(
              IND.filter(function (i) {
                return i.id === 'C14';
              })[0],
              rows,
            )
          : null,
        llos: llos,
        of: byLLO.length,
      };
    },
    [derived, byLLO],
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
      var ind = rows.length ? evalAll(rows) : {};
      var credible = rows.filter(function (r) {
        return MORTALITY_CREDIBLE[r.llo];
      });
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
        mortality: credible.length
          ? evaluate(
              IND.filter(function (i) {
                return i.id === 'C14';
              })[0],
              credible,
            )
          : null,
      };
    });
  }

  var monthly = React.useMemo(
    function () {
      if (frozen) {
        var all = frozen.monthly || [];
        // The frozen series is stored per scope key so a drill still works offline.
        var key = selFLW
          ? 'flw:' + selFLW
          : selOpp
          ? 'opp:' + selOpp
          : selLLO
          ? 'llo:' + selLLO
          : 'all';
        return (frozen.monthlyByScope && frozen.monthlyByScope[key]) || all;
      }
      var m = function (d) {
        return String(d || '').slice(0, 7);
      };
      // The trend follows the drill: pick an LLO or an opportunity on the
      // Indicators tab and this shows that scope, not the whole programme.
      var flwKey = selFLW ? String(selFLW).split(FLW_SEP) : null;
      var inScope = function (oppId, llo, flw) {
        // FLW is the deepest level, so it wins when set. Its key is opp+username
        // because usernames only repeat across opportunities.
        if (flwKey) return oppId === Number(flwKey[0]) && flw === flwKey[1];
        if (selOpp) return oppId === selOpp;
        if (selLLO) return llo === selLLO;
        return true;
      };
      var byMonth = {};
      var visitsByMonth = {};
      derived
        .filter(function (r) {
          return inScope(r.opp, r.llo, r.flw);
        })
        .forEach(function (r) {
          var k = m(r.first_visit);
          if (k) (byMonth[k] = byMonth[k] || []).push(r);
        });
      // Visits per month come from the VISIT pipeline: visit_date is a base column on
      // the raw visit cache, not a form_json path, so asking the entity stage to list
      // it extracted nothing and every month showed 0 visits.
      wrows.forEach(function (v) {
        if (!inScope(v.opportunity_id, lloOf(v.opportunity_id), v.username))
          return;
        var vk = m(v.visit_date);
        if (vk) visitsByMonth[vk] = (visitsByMonth[vk] || 0) + 1;
      });
      var months = Object.keys(byMonth).concat(Object.keys(visitsByMonth));
      months = months
        .filter(function (v, i, a) {
          return v && a.indexOf(v) === i;
        })
        .sort();
      return months.map(function (k) {
        var rows = byMonth[k] || [];
        var ind = rows.length ? evalAll(rows) : {};
        var credible = rows.filter(function (r) {
          return MORTALITY_CREDIBLE[r.llo];
        });
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
          // same credibility gate as the topline card
          mortality: credible.length
            ? evaluate(
                IND.filter(function (i) {
                  return i.id === 'C14';
                })[0],
                credible,
              )
            : null,
        };
      });
    },
    [derived, wrows, selLLO, selOpp, selFLW, frozen],
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
  function fmt(ind, e) {
    if (e.value === null) return '—';
    if (ind.unit === '%') return (100 * e.value).toFixed(1) + '%';
    if (ind.unit === 'n' || ind.unit === '/100')
      return Number(e.value).toFixed(ind.kind === 'count' ? 0 : 1);
    return Number(e.value).toFixed(1);
  }
  function bandLabel(e) {
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
          {IND.map(function (i) {
            var e = ind[i.id];
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
                    var ind = IND.filter(function (i) {
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

  // Build the frozen payload: everything the page DISPLAYS, and nothing it doesn't.
  // Per-case rows are deliberately excluded — 8,656 of them is 7.5 MB on its own, and
  // case-level drill is an investigative tool, not part of a published figure.
  // Is this run built on synthetic clones rather than real programme data? A frozen
  // run reads the flag captured at freeze time; a live run computes it from scope.
  var runIsSynthetic = React.useMemo(
    function () {
      if (frozen) return !!(frozen.meta && frozen.meta.synthetic);
      var seen = {};
      derived.forEach(function (r) {
        seen[r.opp] = true;
      });
      var opps = Object.keys(seen);
      return opps.length > 0 && opps.every(isSyntheticOpp);
    },
    [derived, frozen],
  );

  function buildFrozen() {
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
    view && view.state && view.state.frozen ? view.state.frozen : null;

  // Freezing is TWO steps on purpose. onUpdateState is fire-and-forget, so pairing it
  // with view.complete() behind one click is a race — and complete() won it, producing
  // a completed run whose snapshot captured an empty state. There is no un-complete
  // path, so that artifact is permanent. Waiting for the write to round-trip through
  // view.state removes the guess entirely.
  function stageSnapshot() {
    if (!derived.length) {
      window.alert(
        'No case data has loaded yet — a snapshot taken now would be empty, and a ' +
          'completed run cannot be reopened. Wait for the indicators to appear.',
      );
      return;
    }
    onUpdateState({ frozen: buildFrozen() });
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
      {frozen && (
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
          <span className="font-medium">Frozen run.</span> These figures are the
          snapshot taken{' '}
          {view.asOf ? String(view.asOf).slice(0, 16).replace('T', ' ') : ''} —{' '}
          {(frozen.meta || {}).cases} cases and {(frozen.meta || {}).visits}{' '}
          visits across {(frozen.meta || {}).opportunities} opportunities. They
          load instantly and cannot move. Per-case detail is not part of a
          snapshot; start a new run for that.
        </div>
      )}

      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">KMC Indicators</h1>
          <p className="text-sm text-gray-500 mt-1">
            The kmc_metrics_framework registry, evaluated live. Case properties
            are computed in SQL by the entity pipeline; only the weight series
            is derived here. Click any row to drill Programme → LLO →
            opportunity → cases.
          </p>
        </div>
        {!frozen && view && view.complete && (
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
              var labelFor = function (r) {
                if (nScope === 'opportunity') return oppLabel(r.opportunity_id);
                if (nScope === 'flw') return r.username || '(unassigned)';
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
                    {programInd['C02'].value}
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
                    {fmt(IND[6], programInd['C09'])}
                  </div>
                  <div className="text-xs text-gray-400 mt-1">
                    Program row 3 · % weight data sufficient (C09), pooled
                  </div>
                </div>
                <div className="bg-white border border-gray-200 rounded-xl p-4">
                  <div className="text-xs text-gray-500">Mortality</div>
                  <div className="text-2xl font-semibold mt-1">
                    {mortalityCredible.ind
                      ? fmt(IND[11], mortalityCredible.ind)
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
                            {l.rows.length}
                          </td>
                          <td className="px-3 py-2 text-right">
                            {l.ind['C02'].value}
                          </td>
                          <td className="px-3 py-2 text-right">
                            {fmt(IND[6], l.ind['C09'])}
                          </td>
                          <td className="px-3 py-2 text-right">
                            {fmt(IND[10], l.ind['C13'])}
                          </td>
                          <td className="px-3 py-2 text-right">
                            {fmt(IND[11], l.ind['C14'])}
                          </td>
                          <td className="px-3 py-2 text-right">
                            {fmt(IND[13], l.ind['C16'])}
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
                            {o.rows.length}
                          </td>
                          <td className="px-3 py-2 text-right">
                            {fmt(IND[4], o.ind['C07'])}
                          </td>
                          <td className="px-3 py-2 text-right">
                            {fmt(IND[6], o.ind['C09'])}
                          </td>
                          <td className="px-3 py-2 text-right">
                            {fmt(IND[10], o.ind['C13'])}
                          </td>
                          <td className="px-3 py-2 text-right">
                            {fmt(IND[11], o.ind['C14'])}
                          </td>
                          <td className="px-3 py-2 text-right">
                            {fmt(IND[12], o.ind['C15'])}
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
                var indSel = IND.filter(function (i) {
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
                                var i = IND.filter(function (x) {
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
                                    {f.rows.length}
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
                      {frozen ? 'Cases — not captured in a snapshot' : 'Cases '}
                      {!frozen && selInd
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
