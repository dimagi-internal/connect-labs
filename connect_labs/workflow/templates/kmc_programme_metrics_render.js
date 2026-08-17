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
    C19: ['referral_visits'],
    C20: ['danger_visits'],
    C21: ['self_referral_visits'],
    C23: ['kmc_hours_mean'],
    C28: ['birth_weight_g', 'enrollment_weight_g'],
  };
  var COUNT_FIELDS = {
    danger_visits: 1,
    referral_visits: 1,
    self_referral_visits: 1,
    discharge_visits: 1,
    ebf_visits: 1,
    death_visits: 1,
  };
  function anyAsks(field, opps) {
    if (!opps || !opps.length) return true;
    return opps.some(function (o) {
      var m = APP_ASKS[String(o)];
      return !m || m[field];
    });
  }
  // "Recorded" is computed from the rows in scope rather than baked in, so it
  // stays true as the data changes. A count field sitting at 0 is not evidence
  // that anything was recorded.
  function anyRecorded(field, rows) {
    for (var i = 0; i < rows.length; i++) {
      var v = rows[i][field];
      if (COUNT_FIELDS[field]) {
        if ((v || 0) > 0) return true;
      } else if (v !== null && v !== undefined && v !== '') return true;
    }
    return false;
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

      // weight series per (opp, entity) from the minimal visits pipeline
      var series = {};
      wrows.forEach(function (r) {
        if (!r.entity_id) return;
        var w =
          typeof r.weight_g === 'number' ? r.weight_g : parseFloat(r.weight_g);
        if (!w || w < WMIN || w > WMAX) return;
        var day = String(r.visit_date || '').slice(0, 10);
        if (!day) return;
        var k = r.opportunity_id + '|' + r.entity_id;
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
          first_visit: c.first_visit_date,
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
        d.started = d.num_visits >= 1; // >=1 visit (CHANGED from 2)
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
      num: function () {
        return true;
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
      num: function (r) {
        return r.started;
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
      name: '% referred for danger signs',
      prom: 'Top',
      unit: '%',
      den: function (r) {
        return r.eligible;
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
      den: function (r) {
        return r.eligible;
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
      why: 'every app asks for reg_date and none has recorded one — use the Trend tab (first visit)',
    },
    { id: 'C04', name: 'Visits per month', why: 'available on the Trend tab' },
    {
      id: 'C18',
      name: 'KMC completion rate',
      why: 'all 11 apps ask for kmc discharge (kmc_status_discharged) and none has recorded a single value; gate also TBD in the workbook',
    },
    { id: 'C22', name: '% EBF at completion', why: 'depends on C18' },
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
        m[i.id] = { id: i.id, n: 0, value: null, band: 'notcredible' };
        return;
      }
      m[i.id] = evaluate(i, rows);
    });
    return m;
  }

  // ── Roll up: opp → LLO → program ─────────────────────────────────────────
  var byOpp = React.useMemo(
    function () {
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
    [derived],
  );

  var byLLO = React.useMemo(
    function () {
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
    [derived, byOpp],
  );

  // FLW rollup. Keyed by opp+username: FLW usernames are only unique within an
  // opportunity (the synthetic cohort reuses flw_001.. across opps), so keying on
  // username alone silently merges different people into one row.
  var byFLW = React.useMemo(
    function () {
      var g = {};
      derived.forEach(function (r) {
        var k = r.opp + '\u0000' + (r.flw || '(unassigned)');
        (g[k] = g[k] || []).push(r);
      });
      return Object.keys(g)
        .map(function (k) {
          var parts = k.split('\u0000'),
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
    [derived],
  );

  var programInd = React.useMemo(
    function () {
      return evalAll(derived);
    },
    [derived],
  );
  var llosRed = byLLO.filter(function (l) {
    return l.reds > 0;
  }).length;

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
  var s4 = React.useState(null);
  var selFLW = s4[0],
    setSelFLW = s4[1];

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
    if (e.band === 'notcredible') return 'recording not credible';
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

  return (
    <div className="p-6 space-y-5">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">KMC Indicators</h1>
        <p className="text-sm text-gray-500 mt-1">
          The kmc_metrics_framework registry, evaluated live. Case properties
          are computed in SQL by the entity pipeline; only the weight series is
          derived here. Click any row to drill Programme → LLO → opportunity →
          cases.
        </p>
      </div>

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
                Program row 8 — the catch-all
              </div>
            </div>
            <div className="bg-white border border-gray-200 rounded-xl p-4">
              <div className="text-xs text-gray-500">Total started</div>
              <div className="text-2xl font-semibold mt-1">
                {programInd['C02'].value}
              </div>
              <div className="text-xs text-gray-400 mt-1">
                Program row 2 · against 25,000 by Q1-2027
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
                Program row 3 · pooled (C09)
              </div>
            </div>
            <div className="bg-white border border-gray-200 rounded-xl p-4">
              <div className="text-xs text-gray-500">Mortality</div>
              <div className="text-2xl font-semibold mt-1">
                {fmt(IND[11], programInd['C14'])}
              </div>
              <div className="text-xs text-gray-400 mt-1">
                Program row 5 · two-sided (C14)
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
                  <th className="px-3 py-2 text-right">C09 sufficient</th>
                  <th className="px-3 py-2 text-right">C13 growth</th>
                  <th className="px-3 py-2 text-right">C14 mortality</th>
                  <th className="px-3 py-2 text-right">C16 ≤3 days</th>
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
                      <td className="px-3 py-2 text-right">{l.rows.length}</td>
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
                <th className="px-3 py-2 text-right">C07 computable</th>
                <th className="px-3 py-2 text-right">C09 sufficient</th>
                <th className="px-3 py-2 text-right">C13 growth</th>
                <th className="px-3 py-2 text-right">C14 mortality</th>
                <th className="px-3 py-2 text-right">C15 LTFU</th>
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
                      <td className="px-3 py-2 text-right">{o.rows.length}</td>
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
            if (indSel && indSel.den) caseRows = caseRows.filter(indSel.den);
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
                    click an FLW for their full indicator set and to filter the
                    case list
                  </span>
                </div>
                <div className="overflow-x-auto">
                  <table className="min-w-full text-sm">
                    <thead className="bg-gray-50 text-gray-500">
                      <tr>
                        <th className="px-3 py-2 text-left">FLW</th>
                        <th className="px-3 py-2 text-right">Cases</th>
                        <th className="px-3 py-2 text-right">C09 sufficient</th>
                        <th className="px-3 py-2 text-right">C13 growth</th>
                        <th className="px-3 py-2 text-right">C15 LTFU</th>
                        <th className="px-3 py-2 text-right">
                          C24 visits/case
                        </th>
                        <th className="px-3 py-2 text-right">C28 birth-copy</th>
                        <th className="px-3 py-2 text-right">C31 rounding</th>
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
                    </div>
                  )}
              </div>

              <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
                <div className="px-4 py-3 border-b border-gray-100 font-medium text-gray-900">
                  Cases {selInd ? '\u2014 in the denominator of ' + selInd : ''}
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
                          Outcome
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
                        <th className="px-2 py-2 text-right">Wt n</th>
                        <th className="px-2 py-2 text-center">
                          Comp
                          <div className="text-[10px] font-normal text-gray-400">
                            C07
                          </div>
                        </th>
                        <th className="px-2 py-2 text-center">
                          Cons
                          <div className="text-[10px] font-normal text-gray-400">
                            C08
                          </div>
                        </th>
                        <th className="px-2 py-2 text-center">
                          Suff
                          <div className="text-[10px] font-normal text-gray-400">
                            C09
                          </div>
                        </th>
                        <th className="px-2 py-2 text-right">Weights</th>
                        <th className="px-2 py-2 text-right">
                          g/kg/d
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
                          Days to enrol
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
                          Danger
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
                          Self-ref
                          <div className="text-[10px] font-normal text-gray-400">
                            C21
                          </div>
                        </th>
                        <th className="px-2 py-2 text-right">
                          KMC h
                          <div className="text-[10px] font-normal text-gray-400">
                            C23
                          </div>
                        </th>
                        <th className="px-2 py-2 text-center">
                          Birth-copy
                          <div className="text-[10px] font-normal text-gray-400">
                            C28
                          </div>
                        </th>
                        <th className="px-2 py-2 text-right">
                          Round100
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
        <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-1 text-xs text-gray-500">
          {NOT_COMPUTABLE.map(function (n) {
            return (
              <div key={n.id}>
                <span className="font-mono text-gray-400">{n.id}</span> {n.name}{' '}
                — {n.why}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
