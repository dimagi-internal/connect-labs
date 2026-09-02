// CHC Mop-up Candidate Analysis — render code
// Program 217 (CHC - NG - RCT - Aug 2026), 4 LLOs (JHF 2154 / EHA 2155 / ISODAF 2156 / SOLINA 2157).
//
// Data sources (see chc_mopup_candidates.py for the pipeline schemas / join-key
// rationale): work_areas (cchq_cases, case_type=work-area — wired at workflow-
// creation time via workflow_add_pipeline_source; program 217 has more than one
// "CHC Work Areas" pipeline across its 4 LLOs, use the one an existing live
// dashboard already depends on, not just any pipeline with a matching name/
// description — a stale, never-iterated duplicate looks identical on paper but
// can silently behave differently) + wa_geometry (connect_export work_areas) +
// visit_quality (this template's own chc_mopup_visit_quality, entity-stage)
// join on the work-area case id (work_areas.entity_id == wa_geometry.wa_case_id
// == visit_quality.entity_id). audit_entries is optional FLW-week context only
// (not used for any inclusion decision).
//
// Three criteria groups, computed per work area, combined as UNION/OR:
//   (a) EVC shortfall     — visit_quality.hsd_visit_count / work_areas.expected_visit_count.
//       Excludes WAs Connect still shows as NOT_VISITED / a pending inaccessible request
//       (isWaDone) unless "Include not-yet-visited work areas" is on (default off) — an
//       unattempted WA in a still-running campaign isn't "underperforming", it just hasn't
//       happened yet. This gate applies at both WA and FLW-rollup granularity.
//   (b) NCF / inaccessible — visit_quality.ncf_visit_count / inaccessible_visit_count (approved,
//       form-name-filtered visit counts — NOT work_areas' wa_checkout_remark/reason_for_inaccessible/
//       case_closed/delivered_visit_count case properties. Those WA-case aggregates were the original
//       design but turned out not to be trustworthy for precise approved/form-type-scoped counting —
//       the same problem already solved for the 5 DQ metrics by reading the approved, form-filtered
//       visit pipeline instead of a case property. work_areas.reason_for_inaccessible is still surfaced
//       as a supplementary free-text display detail only, never in threshold/inclusion math.)
//   (c) 5 data-quality metrics — deworming / MUAC / gender-split / age-heaping / vaccination,
//       all computed at WA granularity from visit_quality's numerator/denominator counts.
// Every rule has its own "WA-level only" vs "whole FLW" toggle (FLW rollups sum
// numerators/denominators across a FLW's own work areas, then divide — never
// average WA-level percentages). Everything is a client-side React.useMemo over
// already-loaded pipeline rows — no server round-trip on threshold changes.
//
// A "Lock candidate set" button freezes the resolved WA-id list + full rule
// config into instance.state via onUpdateState; the hand-off ("Create mop-up
// microplan") button only ever acts on the LOCKED set, never the live preview.

var ce = React.createElement;

// =========================================================================
// Generic helpers
// =========================================================================

function safeParseJSON(raw) {
  if (raw == null) return null;
  if (typeof raw === 'object') return raw;
  try {
    return JSON.parse(raw);
  } catch (e) {
    return null;
  }
}

function csrfToken() {
  var root = document.getElementById('workflow-root');
  if (root && root.dataset && root.dataset.csrfToken)
    return root.dataset.csrfToken;
  var el = document.querySelector('[name=csrfmiddlewaretoken]');
  return el ? el.value : '';
}

// Render-code-local fetch wrapper (same shape as chc_nutrition_analysis.py's
// apiPost) — full URLs at each call site so they self-document the endpoint hit.
function apiPost(url, body) {
  return fetch(url, {
    method: 'POST',
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': csrfToken(),
    },
    body: JSON.stringify(body || {}),
  }).then(function (r) {
    return r.json().then(
      function (data) {
        return { ok: r.ok, status: r.status, data: data };
      },
      function () {
        return { ok: r.ok, status: r.status, data: null };
      },
    );
  });
}

function numOr(v, fallback) {
  var n = typeof v === 'number' ? v : parseFloat(v);
  return isFinite(n) ? n : fallback;
}

function round1(n) {
  return Math.round(n * 10) / 10;
}

function fmtPct(rate) {
  if (rate == null || isNaN(rate)) return '—';
  return round1(rate) + '%';
}

function fmtNum(n) {
  if (n == null || isNaN(n)) return '—';
  return round1(n).toLocaleString
    ? round1(n).toLocaleString()
    : String(round1(n));
}

// =========================================================================
// Rule vocabulary
// =========================================================================

// Whipple-style age-heaping index — same 4 milestone months / expected-fraction
// convention as workflow/flw_audit_compute.py's whipple_index() (100 = no
// heaping, higher = more clustering at 12/24/36/48mo out of 60 possible months).
var HEAPING_MONTHS = [12, 24, 36, 48];
var HEAPING_EXPECTED_FRACTION = HEAPING_MONTHS.length / 60.0;

function ageMonthBucketField(m) {
  return 'age_months_' + m + '_0_' + (m + 1) + '_0_visits';
}

function ageHeapedCount(row) {
  // buildWaRows() (below) copies only the named visit_quality fields onto the
  // joined WA row, not all 60 age_months_<N>_<N+1>_visits histogram buckets --
  // those live on the raw visit_quality row, preserved as row._vqRow. Reading
  // `row` directly here would silently always return 0 for every WA (this WAS
  // a real bug, caught by the node-harness unit checks against real fixture
  // data before this template shipped). Falling back to `row` itself keeps
  // this working when called directly on a raw visit_quality pipeline row.
  var vq = row._vqRow || row;
  var sum = 0;
  HEAPING_MONTHS.forEach(function (m) {
    sum += numOr(vq[ageMonthBucketField(m)], 0);
  });
  return sum;
}

// Every metric: {key, label, short, unit, numerator(row), denominator(row),
// rate(num, den), direction, defaultThreshold, describe}.
// direction 'low'  -> flagged when rate < threshold (deworming/MUAC/vaccination)
// direction 'high' -> flagged when rate > threshold (gender deviation, age-heaping index)
var DQ_METRICS = [
  {
    key: 'deworming',
    label: 'Deworming',
    short: 'DW',
    unit: '%',
    numerator: function (r) {
      return numOr(r.deworming_given_count, 0);
    },
    denominator: function (r) {
      return numOr(r.hsd_visit_count, 0);
    },
    rate: function (num, den) {
      return den > 0 ? (num / den) * 100 : null;
    },
    direction: 'low',
    defaultThreshold: 70,
    describe: 'Deworming dose delivered ÷ approved HSD visits',
  },
  {
    key: 'muac',
    label: 'MUAC recorded',
    short: 'MUAC',
    unit: '%',
    numerator: function (r) {
      return numOr(r.muac_recorded_count, 0);
    },
    denominator: function (r) {
      return numOr(r.hsd_visit_count, 0);
    },
    rate: function (num, den) {
      return den > 0 ? (num / den) * 100 : null;
    },
    direction: 'low',
    defaultThreshold: 70,
    describe: 'MUAC value recorded ÷ approved HSD visits',
  },
  {
    key: 'vaccination',
    label: 'Vaccination',
    short: 'Vax',
    unit: '%',
    numerator: function (r) {
      return numOr(r.vaccination_given_count, 0);
    },
    denominator: function (r) {
      return numOr(r.hsd_visit_count, 0);
    },
    rate: function (num, den) {
      return den > 0 ? (num / den) * 100 : null;
    },
    direction: 'low',
    defaultThreshold: 70,
    describe: 'Vaccine given ÷ approved HSD visits',
  },
  {
    key: 'gender_split',
    label: 'Gender split',
    short: 'Gender',
    unit: 'pp',
    numerator: function (r) {
      return numOr(r.gender_male_count, 0);
    },
    denominator: function (r) {
      return numOr(r.gender_recorded_count, 0);
    },
    rate: function (num, den) {
      return den > 0 ? Math.abs((num / den) * 100 - 50) : null;
    },
    direction: 'high',
    defaultThreshold: 15,
    describe: 'Deviation of male share from 50/50 (percentage points)',
  },
  {
    key: 'age_heaping',
    label: 'Age heaping',
    short: 'Heap',
    unit: 'idx',
    numerator: function (r) {
      return ageHeapedCount(r);
    },
    denominator: function (r) {
      return numOr(r.age_months_count, 0);
    },
    rate: function (num, den) {
      if (den <= 0) return null;
      return (num / den / HEAPING_EXPECTED_FRACTION) * 100;
    },
    direction: 'high',
    defaultThreshold: 125,
    describe:
      'Whipple-style index — ages clustering at 12/24/36/48mo (100 = no heaping)',
  },
];

function metricFails(metric, rate, threshold) {
  if (rate == null || isNaN(rate)) return false;
  return metric.direction === 'low' ? rate < threshold : rate > threshold;
}

// ---- NCF / inaccessible ----
// Sourced from visit_quality's own approved, form-name-filtered visit counts
// (ncf_visit_count / inaccessible_visit_count) -- NOT from work_areas' case-level
// wa_checkout_remark / reason_for_inaccessible / case_closed / delivered_visit_count
// properties. Those case aggregates were the original design but were found not to
// be trustworthy for precise approved/form-type-scoped counting during Phase 2
// review (delivered_visit_count doesn't reliably exclude non-approved visits
// either) -- the same class of problem already solved for the 5 DQ metrics by
// reading the approved, form-filtered Connect visit pipeline directly. See
// chc_mopup_candidates.py's module docstring for the exact form-name validation.

function ncfOrInaccessibleVisitCount(row) {
  return numOr(row.ncf_visit_count, 0) + numOr(row.inaccessible_visit_count, 0);
}

function waHasNcfOrInaccessibleVisit(row) {
  return ncfOrInaccessibleVisitCount(row) > 0;
}

// Total approved visits at this WA across all three known deliver-unit forms —
// the denominator for a WA/FLW-level NCF rate.
function totalApprovedVisitCount(row) {
  return numOr(row.hsd_visit_count, 0) + ncfOrInaccessibleVisitCount(row);
}

// Free-text closure detail for display only (tooltip) -- work_areas is the ONLY
// source for *why* a WA was marked inaccessible (that reason text has no visit_quality
// equivalent), but it never drives any threshold/inclusion decision.
function closureDisplayLabel(row) {
  if (waHasNcfOrInaccessibleVisit(row)) {
    if (
      numOr(row.inaccessible_visit_count, 0) > 0 &&
      numOr(row.ncf_visit_count, 0) > 0
    )
      return 'NCF + Inaccessible';
    if (numOr(row.inaccessible_visit_count, 0) > 0) return 'Inaccessible';
    return 'NCF';
  }
  return row.hsd_visit_count > 0 ? 'HSD delivered' : '—';
}

// =========================================================================
// Join: work_areas (12965) + wa_geometry (12971) + visit_quality (this template)
// =========================================================================

function joinKey(oppId, entityId) {
  return String(oppId) + '::' + String(entityId);
}

function buildWaRows(workAreaRows, geometryRows, visitQualityRows) {
  var geomByKey = {};
  (geometryRows || []).forEach(function (g) {
    if (!g.wa_case_id) return;
    geomByKey[joinKey(g.opportunity_id, g.wa_case_id)] = g;
  });
  var vqByKey = {};
  (visitQualityRows || []).forEach(function (v) {
    if (!v.entity_id) return;
    vqByKey[joinKey(v.opportunity_id, v.entity_id)] = v;
  });

  return (workAreaRows || [])
    .filter(function (wa) {
      return !!wa.entity_id;
    })
    .map(function (wa) {
      var key = joinKey(wa.opportunity_id, wa.entity_id);
      var geom = geomByKey[key];
      var vq = vqByKey[key];
      var boundary = geom ? safeParseJSON(geom.boundary) : null;
      return {
        key: key,
        opportunity_id: wa.opportunity_id,
        entity_id: wa.entity_id,
        ward: wa.ward || (geom && geom.ward) || '',
        lga: wa.lga || '',
        state: wa.state || '',
        work_area_group: wa.work_area_group || '',
        expected_visit_count: numOr(wa.expected_visit_count, null),
        building_count: numOr(wa.building_count, 0),
        household_count: numOr(wa.household_count, null),
        delivered_visit_count: numOr(wa.delivered_visit_count, null),
        hq_status_wa: wa.hq_status_wa || '',
        wa_status: wa.wa_status || '',
        owner_id: wa.owner_id || '',
        // Display-only (tooltip) -- never used for threshold/inclusion math. See
        // the NCF/inaccessible helpers above for why.
        reason_for_inaccessible: wa.reason_for_inaccessible || '',
        // wa_geometry side
        slug: geom ? geom.slug : '',
        connect_status: geom ? geom.status : '',
        boundary: boundary,
        hasGeometry: !!boundary,
        // visit_quality side (default to 0 -- "no visit_quality row" reads the
        // same as "zero approved HSD visits at this WA", which is itself a real,
        // legitimate signal per the pipeline's own module docstring).
        hsd_visit_count: vq ? numOr(vq.hsd_visit_count, 0) : 0,
        ncf_visit_count: vq ? numOr(vq.ncf_visit_count, 0) : 0,
        inaccessible_visit_count: vq
          ? numOr(vq.inaccessible_visit_count, 0)
          : 0,
        deworming_given_count: vq ? numOr(vq.deworming_given_count, 0) : 0,
        muac_recorded_count: vq ? numOr(vq.muac_recorded_count, 0) : 0,
        vaccination_given_count: vq ? numOr(vq.vaccination_given_count, 0) : 0,
        gender_recorded_count: vq ? numOr(vq.gender_recorded_count, 0) : 0,
        gender_male_count: vq ? numOr(vq.gender_male_count, 0) : 0,
        gender_female_count: vq ? numOr(vq.gender_female_count, 0) : 0,
        age_months_count: vq ? numOr(vq.age_months_count, 0) : 0,
        age_months_mean: vq ? vq.age_months_mean : null,
        hasVisitQuality: !!vq,
        _vqRow: vq || null,
      };
    });
}

function evcRatio(row) {
  var expected = row.expected_visit_count;
  if (!(expected > 0)) return null;
  return row.hsd_visit_count / expected;
}

// Connect's own WA status enum (wa_geometry.status / row.connect_status):
// NOT_VISITED / VISITED / EXPECTED_VISIT_REACHED / REQUEST_FOR_INACCESSIBLE /
// INACCESSIBLE. "Done" = the delivery attempt at this WA is concluded one way
// or another -- used to keep EVC-shortfall from flagging every work area a
// still-running campaign simply hasn't reached yet (an unattempted WA isn't
// "underperforming", it's just not due yet). REQUEST_FOR_INACCESSIBLE is
// deliberately NOT included -- it's a pending request, not a resolved
// outcome, so it reads the same as NOT_VISITED for this purpose. A missing/
// unknown status is conservatively treated as not-done (excluded), same as
// NOT_VISITED, rather than assumed done.
var WA_DONE_STATUSES = {
  VISITED: true,
  EXPECTED_VISIT_REACHED: true,
  INACCESSIBLE: true,
};

function isWaDone(row) {
  return !!WA_DONE_STATUSES[row.connect_status];
}

// =========================================================================
// FLW rollups — sum numerators/denominators across a FLW's own work areas,
// THEN divide. Never average WA-level percentages (misweights a 1-visit WA
// the same as a 30-visit WA). Scoped per (opportunity_id, owner_id) since a
// CommCare case owner id is only meaningful within its own domain/opportunity.
//
// EVC sums are tracked as TWO pairs (all WAs vs. done-only WAs) so
// evaluateRules can pick the right pair per the includeNotVisited toggle
// without needing `config` threaded into this function -- this keeps
// buildFlwRollups a pure function of waRows only, matching its existing
// memoization (it's recomputed on ward-filter change, not on every threshold
// tweak; see the component's useMemo chain).
// =========================================================================

function flwKeyOf(row) {
  return joinKey(row.opportunity_id, row.owner_id || '__unowned__');
}

function buildFlwRollups(waRows) {
  var byFlw = {};
  waRows.forEach(function (row) {
    var fk = flwKeyOf(row);
    if (!byFlw[fk]) {
      byFlw[fk] = {
        key: fk,
        opportunity_id: row.opportunity_id,
        owner_id: row.owner_id,
        waCount: 0,
        evcActualSum: 0, // all WAs (includeNotVisited=true reading)
        evcExpectedSum: 0,
        evcActualSumDone: 0, // done-only WAs (includeNotVisited=false reading, the default)
        evcExpectedSumDone: 0,
        ncfNumSum: 0, // sum of (ncf_visit_count + inaccessible_visit_count)
        ncfDenSum: 0, // sum of total approved visits (hsd + ncf + inaccessible)
        ncfCountModeCount: 0, // # of WAs that individually trip the count/N-buildings mode
        dq: {},
      };
      DQ_METRICS.forEach(function (m) {
        byFlw[fk].dq[m.key] = { numSum: 0, denSum: 0 };
      });
    }
    var flw = byFlw[fk];
    flw.waCount += 1;
    if (row.expected_visit_count > 0) {
      flw.evcActualSum += row.hsd_visit_count;
      flw.evcExpectedSum += row.expected_visit_count;
      if (isWaDone(row)) {
        flw.evcActualSumDone += row.hsd_visit_count;
        flw.evcExpectedSumDone += row.expected_visit_count;
      }
    }
    flw.ncfNumSum += ncfOrInaccessibleVisitCount(row);
    flw.ncfDenSum += totalApprovedVisitCount(row);
    DQ_METRICS.forEach(function (m) {
      flw.dq[m.key].numSum += m.numerator(row);
      flw.dq[m.key].denSum += m.denominator(row);
    });
  });
  return byFlw;
}

// =========================================================================
// Rule evaluation — combines all enabled rules as UNION/OR, per-rule
// WA-level-only vs whole-FLW toggle honored individually.
// =========================================================================

function defaultRuleConfig() {
  var dq = {};
  DQ_METRICS.forEach(function (m) {
    dq[m.key] = {
      enabled: true,
      threshold: m.defaultThreshold,
      flwLevel: false,
    };
  });
  return {
    dqMinN: 5,
    evc: {
      enabled: true,
      thresholdPct: 60,
      flwLevel: false,
      // Default false: a WA the campaign simply hasn't reached yet
      // (connect_status NOT_VISITED / still-pending REQUEST_FOR_INACCESSIBLE)
      // isn't "underperforming" -- it just hasn't happened yet. Flip on to
      // mop up areas a still-in-progress ward hasn't fully attempted, e.g.
      // when running mop-up slightly ahead of full completion on purpose.
      includeNotVisited: false,
    },
    ncf: {
      enabled: true,
      // Mode (a): a WA with an approved NCF/Inaccessible visit, 0 HSD visits,
      // and >=N buildings -- the plan's literal "0 HSD visits AND ≥N buildings".
      countMode: { enabled: true, minBuildings: 1, flwLevel: false },
      // Mode (b): rank FLWs by (ncf+inaccessible)/(total approved visits),
      // summed across their own WAs then divided (never averaged), flag ALL
      // WAs owned by the top-K ranked FLWs. Always FLW-granular by definition.
      rankMode: { enabled: false, topK: 5, minVisits: 10 },
    },
    dq: dq,
  };
}

function ncfCountModeWaFails(row, minBuildings) {
  return (
    waHasNcfOrInaccessibleVisit(row) &&
    row.hsd_visit_count === 0 &&
    row.building_count >= minBuildings
  );
}

function evaluateRules(waRows, flwRollups, config) {
  var evc = config.evc,
    ncf = config.ncf,
    dqCfg = config.dq,
    minN = numOr(config.dqMinN, 5);

  // Count-mode FLW rollup: does ANY of this FLW's own WAs individually trip the
  // WA-level count/N-buildings condition? (A boolean per-WA fact rolls up as an
  // OR across the FLW's portfolio -- unlike the numerator/denominator ratio
  // rules below, there is no "sum then divide" reading of a boolean.)
  var ncfCountModeFlwFlags = {};
  if (ncf.enabled && ncf.countMode.enabled) {
    var minBuildings = numOr(ncf.countMode.minBuildings, 0);
    waRows.forEach(function (row) {
      if (ncfCountModeWaFails(row, minBuildings))
        ncfCountModeFlwFlags[flwKeyOf(row)] = true;
    });
  }

  // Rank-mode: FLWs ranked by (ncf+inaccessible)/(total approved visits) --
  // summed across their own WAs, then divided once (never averaged WA rates) --
  // flag ALL of the top-K FLWs' work areas. Genuinely FLW-granular: there is no
  // WA-level reading of "this FLW ranks in the top K".
  var rankFlaggedFlwKeys = {};
  if (ncf.enabled && ncf.rankMode && ncf.rankMode.enabled) {
    var minVisits = numOr(ncf.rankMode.minVisits, 0);
    var ranked = Object.keys(flwRollups)
      .map(function (k) {
        return flwRollups[k];
      })
      .filter(function (f) {
        return f.ncfDenSum >= minVisits;
      })
      .map(function (f) {
        return {
          key: f.key,
          rate: f.ncfDenSum > 0 ? f.ncfNumSum / f.ncfDenSum : 0,
        };
      })
      .sort(function (a, b) {
        return b.rate - a.rate;
      });
    ranked
      .slice(0, Math.max(0, Math.round(numOr(ncf.rankMode.topK, 0))))
      .forEach(function (r) {
        rankFlaggedFlwKeys[r.key] = true;
      });
  }

  var results = {};
  waRows.forEach(function (row) {
    var flw = flwRollups[flwKeyOf(row)];
    var reasons = [];

    // --- EVC shortfall ---
    // A not-yet-attempted WA (connect_status NOT_VISITED / still-pending
    // REQUEST_FOR_INACCESSIBLE) never counts as an EVC shortfall unless
    // includeNotVisited is on -- see isWaDone's docstring. This is a WA-level
    // eligibility gate; it's separate from evcApplied's WA-vs-FLW toggle below
    // (both can apply at once: e.g. flwLevel=true still only sums a FLW's
    // done WAs into the ratio unless includeNotVisited is also on).
    var evcRowEligible = evc.includeNotVisited || isWaDone(row);
    var evcWaRatio = evcRatio(row);
    var evcWaFails =
      evcRowEligible &&
      evcWaRatio != null &&
      evcWaFails_(evcWaRatio, evc.thresholdPct);
    var evcActualSum = evc.includeNotVisited
      ? flw && flw.evcActualSum
      : flw && flw.evcActualSumDone;
    var evcExpectedSum = evc.includeNotVisited
      ? flw && flw.evcExpectedSum
      : flw && flw.evcExpectedSumDone;
    var evcFlwRatio = evcExpectedSum > 0 ? evcActualSum / evcExpectedSum : null;
    var evcFlwFails =
      evcFlwRatio != null && evcWaFails_(evcFlwRatio, evc.thresholdPct);
    // FLW-level mode still only flags a WA if THAT WA is itself eligible --
    // an unattempted WA shouldn't get pulled into the mop-up just because its
    // FLW's other (done) WAs happened to trip the ratio, unless
    // includeNotVisited is on.
    var evcApplied =
      evcRowEligible && (evc.flwLevel ? evcFlwFails : evcWaFails);
    if (evc.enabled && evcApplied) reasons.push('evc');

    // --- NCF / inaccessible (visit_quality-sourced, see helpers above) ---
    var ncfWaRate =
      totalApprovedVisitCount(row) > 0
        ? ncfOrInaccessibleVisitCount(row) / totalApprovedVisitCount(row)
        : null;
    var ncfCountWaFails =
      ncf.countMode.enabled &&
      ncfCountModeWaFails(row, numOr(ncf.countMode.minBuildings, 0));
    var ncfCountFlwFails =
      ncf.countMode.enabled && !!ncfCountModeFlwFlags[flwKeyOf(row)];
    var ncfCountApplied =
      ncf.countMode.enabled &&
      (ncf.countMode.flwLevel ? ncfCountFlwFails : ncfCountWaFails);
    var ncfRankApplied =
      ncf.rankMode.enabled && !!rankFlaggedFlwKeys[flwKeyOf(row)];
    if (ncf.enabled && (ncfCountApplied || ncfRankApplied)) reasons.push('ncf');

    // --- 5 data-quality metrics ---
    var dqDetail = {};
    DQ_METRICS.forEach(function (m) {
      var num = m.numerator(row),
        den = m.denominator(row);
      var rate = den >= minN ? m.rate(num, den) : null;
      var waFails = metricFails(m, rate, dqCfg[m.key].threshold);
      var flwSums = flw ? flw.dq[m.key] : null;
      var flwRate =
        flwSums && flwSums.denSum >= minN
          ? m.rate(flwSums.numSum, flwSums.denSum)
          : null;
      var flwFails = metricFails(m, flwRate, dqCfg[m.key].threshold);
      var applied =
        dqCfg[m.key].enabled && (dqCfg[m.key].flwLevel ? flwFails : waFails);
      if (applied) reasons.push(m.key);
      dqDetail[m.key] = {
        num: num,
        den: den,
        rate: rate,
        waFails: waFails,
        flwRate: flwRate,
        flwFails: flwFails,
      };
    });

    results[row.key] = {
      isCandidate: reasons.length > 0,
      reasons: reasons,
      evc: {
        ratio: evcWaRatio,
        flwRatio: evcFlwRatio,
        applied: evcApplied,
        // True when this row's ratio was computed but excluded from EVC
        // consideration because the WA hasn't been attempted yet (see
        // isWaDone) -- lets the table show "not yet visited" instead of a
        // bare, misleadingly-alarming "0%" for a WA nobody has reached.
        excludedNotVisited: !evcRowEligible,
      },
      ncf: {
        rate: ncfWaRate,
        countApplied: ncfCountApplied,
        rankApplied: ncfRankApplied,
      },
      dq: dqDetail,
    };
  });
  return results;
}

function evcWaFails_(ratio, thresholdPct) {
  return ratio < thresholdPct / 100;
}

// =========================================================================
// UI atoms
// =========================================================================

function KpiCard(props) {
  return ce(
    'div',
    { className: 'bg-white p-4 rounded-lg shadow-sm border border-gray-200' },
    ce(
      'div',
      { className: 'text-2xl font-bold ' + (props.tone || 'text-gray-900') },
      props.value,
    ),
    ce('div', { className: 'text-xs text-gray-500 mt-1' }, props.label),
  );
}

function NumberField(props) {
  return ce(
    'label',
    { className: 'flex items-center gap-2 text-sm text-gray-700' },
    ce('span', { className: 'min-w-0 flex-1' }, props.label),
    ce('input', {
      type: 'number',
      className:
        'w-20 border border-gray-300 rounded px-2 py-1 text-sm text-right',
      value: props.value,
      min: props.min,
      max: props.max,
      step: props.step || 1,
      disabled: props.disabled,
      onChange: function (e) {
        var v = parseFloat(e.target.value);
        props.onChange(isNaN(v) ? 0 : v);
      },
    }),
    props.suffix
      ? ce('span', { className: 'text-xs text-gray-400 w-6' }, props.suffix)
      : null,
  );
}

function LevelToggle(props) {
  // "WA-level only" vs "whole FLW" -- the explicit per-rule toggle.
  return ce(
    'div',
    {
      className: 'flex text-xs rounded overflow-hidden border border-gray-300',
    },
    ce(
      'button',
      {
        type: 'button',
        className:
          'px-2 py-1 ' +
          (!props.value
            ? 'bg-orange-600 text-white'
            : 'bg-white text-gray-600'),
        onClick: function () {
          props.onChange(false);
        },
        disabled: props.disabled,
      },
      'This WA only',
    ),
    ce(
      'button',
      {
        type: 'button',
        className:
          'px-2 py-1 ' +
          (props.value ? 'bg-orange-600 text-white' : 'bg-white text-gray-600'),
        onClick: function () {
          props.onChange(true);
        },
        disabled: props.disabled,
      },
      "Whole FLW's WAs",
    ),
  );
}

function SortTh(props) {
  var active = props.sortCol === props.colKey;
  var nextDir = active && props.sortDir === 'desc' ? 'asc' : 'desc';
  var icon = active ? (props.sortDir === 'desc' ? ' ↓' : ' ↑') : '';
  return ce(
    'th',
    {
      className:
        'px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider bg-orange-900 text-orange-100 cursor-pointer select-none hover:bg-orange-800 whitespace-nowrap',
      onClick: function () {
        props.onSort(props.colKey, nextDir);
      },
    },
    props.label + icon,
  );
}

function ReasonBadges(props) {
  var labels = { evc: 'EVC', ncf: 'NCF/Inacc' };
  DQ_METRICS.forEach(function (m) {
    labels[m.key] = m.short;
  });
  return ce(
    'div',
    { className: 'flex flex-wrap gap-1' },
    (props.reasons || []).map(function (r) {
      return ce(
        'span',
        {
          key: r,
          className:
            'px-1.5 py-0.5 rounded text-[10px] font-medium bg-red-100 text-red-700',
        },
        labels[r] || r,
      );
    }),
  );
}

// =========================================================================
// Threshold panel
// =========================================================================

function ThresholdPanel(props) {
  var config = props.config,
    setConfig = props.setConfig,
    disabled = props.disabled;

  function patch(path, value) {
    setConfig(function (prev) {
      var next = JSON.parse(JSON.stringify(prev));
      var target = next;
      for (var i = 0; i < path.length - 1; i++) target = target[path[i]];
      target[path[path.length - 1]] = value;
      return next;
    });
  }

  return ce(
    'div',
    {
      className:
        'bg-white rounded-lg shadow-sm border border-gray-200 p-4 space-y-5',
    },
    ce(
      'h3',
      { className: 'font-semibold text-gray-900' },
      'Candidate criteria (union — any enabled rule flags a WA)',
    ),

    // --- EVC ---
    ce(
      'div',
      { className: 'border-t border-gray-100 pt-3' },
      ce(
        'div',
        { className: 'flex items-center justify-between' },
        ce(
          'label',
          {
            className:
              'flex items-center gap-2 font-medium text-sm text-gray-800',
          },
          ce('input', {
            type: 'checkbox',
            checked: config.evc.enabled,
            disabled: disabled,
            onChange: function (e) {
              patch(['evc', 'enabled'], e.target.checked);
            },
          }),
          'EVC shortfall',
        ),
        ce(LevelToggle, {
          value: config.evc.flwLevel,
          disabled: disabled || !config.evc.enabled,
          onChange: function (v) {
            patch(['evc', 'flwLevel'], v);
          },
        }),
      ),
      ce(
        'div',
        { className: 'text-xs text-gray-500 mt-1' },
        'Flag when approved HSD visits delivered < threshold % of expected_visit_count.',
      ),
      ce(NumberField, {
        label: 'Threshold (actual < N% of expected)',
        value: config.evc.thresholdPct,
        min: 0,
        max: 100,
        suffix: '%',
        disabled: disabled || !config.evc.enabled,
        onChange: function (v) {
          patch(['evc', 'thresholdPct'], v);
        },
      }),
      ce(
        'label',
        {
          className: 'flex items-center gap-2 text-xs text-gray-600 mt-2',
          title:
            "Off (default): a work area Connect still shows as NOT_VISITED (or a pending inaccessible request) never counts as an EVC shortfall — it just hasn't been attempted yet. On: score it anyway, e.g. to mop up a ward slightly before it fully wraps up.",
        },
        ce('input', {
          type: 'checkbox',
          checked: config.evc.includeNotVisited,
          disabled: disabled || !config.evc.enabled,
          onChange: function (e) {
            patch(['evc', 'includeNotVisited'], e.target.checked);
          },
        }),
        'Include not-yet-visited work areas',
      ),
    ),

    // --- NCF / inaccessible ---
    ce(
      'div',
      { className: 'border-t border-gray-100 pt-3' },
      ce(
        'label',
        {
          className:
            'flex items-center gap-2 font-medium text-sm text-gray-800',
        },
        ce('input', {
          type: 'checkbox',
          checked: config.ncf.enabled,
          disabled: disabled,
          onChange: function (e) {
            patch(['ncf', 'enabled'], e.target.checked);
          },
        }),
        'NCF / inaccessible',
      ),
      ce(
        'div',
        { className: 'text-xs text-gray-500 mt-1 mb-2' },
        'From visit_quality.ncf_visit_count / inaccessible_visit_count — approved, form-name-filtered ' +
          'visit counts (same precision as the HSD/DQ metrics), not the work_areas case’s closure fields.',
      ),
      ce(
        'div',
        { className: 'pl-4 border-l-2 border-orange-100 space-y-2' },
        ce(
          'div',
          { className: 'flex items-center justify-between' },
          ce(
            'label',
            { className: 'flex items-center gap-2 text-sm text-gray-700' },
            ce('input', {
              type: 'checkbox',
              checked: config.ncf.countMode.enabled,
              disabled: disabled || !config.ncf.enabled,
              onChange: function (e) {
                patch(['ncf', 'countMode', 'enabled'], e.target.checked);
              },
            }),
            '0 HSD visits AND ≥N buildings',
          ),
          ce(LevelToggle, {
            value: config.ncf.countMode.flwLevel,
            disabled:
              disabled || !config.ncf.enabled || !config.ncf.countMode.enabled,
            onChange: function (v) {
              patch(['ncf', 'countMode', 'flwLevel'], v);
            },
          }),
        ),
        ce(NumberField, {
          label: 'Minimum buildings (N)',
          value: config.ncf.countMode.minBuildings,
          min: 0,
          disabled:
            disabled || !config.ncf.enabled || !config.ncf.countMode.enabled,
          onChange: function (v) {
            patch(['ncf', 'countMode', 'minBuildings'], v);
          },
        }),
        ce(
          'label',
          { className: 'flex items-center gap-2 text-sm text-gray-700 pt-2' },
          ce('input', {
            type: 'checkbox',
            checked: config.ncf.rankMode.enabled,
            disabled: disabled || !config.ncf.enabled,
            onChange: function (e) {
              patch(['ncf', 'rankMode', 'enabled'], e.target.checked);
            },
          }),
          'Top-ranking FLWs by NCF% (always whole-FLW)',
        ),
        ce(NumberField, {
          label: 'Top K FLWs',
          value: config.ncf.rankMode.topK,
          min: 0,
          disabled:
            disabled || !config.ncf.enabled || !config.ncf.rankMode.enabled,
          onChange: function (v) {
            patch(['ncf', 'rankMode', 'topK'], v);
          },
        }),
        ce(NumberField, {
          label: 'Min. approved visits to qualify for ranking',
          value: config.ncf.rankMode.minVisits,
          min: 0,
          disabled:
            disabled || !config.ncf.enabled || !config.ncf.rankMode.enabled,
          onChange: function (v) {
            patch(['ncf', 'rankMode', 'minVisits'], v);
          },
        }),
      ),
    ),

    // --- 5 DQ metrics ---
    ce(
      'div',
      { className: 'border-t border-gray-100 pt-3' },
      ce(
        'div',
        { className: 'font-medium text-sm text-gray-800 mb-1' },
        'Data-quality metrics (WA granularity)',
      ),
      ce(NumberField, {
        label: 'Minimum N (denominator floor before a rate counts)',
        value: config.dqMinN,
        min: 0,
        disabled: disabled,
        onChange: function (v) {
          setConfig(function (prev) {
            var next = JSON.parse(JSON.stringify(prev));
            next.dqMinN = v;
            return next;
          });
        },
      }),
      DQ_METRICS.map(function (m) {
        var mc = config.dq[m.key];
        return ce(
          'div',
          { key: m.key, className: 'mt-2 pt-2 border-t border-gray-50' },
          ce(
            'div',
            { className: 'flex items-center justify-between' },
            ce(
              'label',
              { className: 'flex items-center gap-2 text-sm text-gray-700' },
              ce('input', {
                type: 'checkbox',
                checked: mc.enabled,
                disabled: disabled,
                onChange: function (e) {
                  patch(['dq', m.key, 'enabled'], e.target.checked);
                },
              }),
              m.label,
            ),
            ce(LevelToggle, {
              value: mc.flwLevel,
              disabled: disabled || !mc.enabled,
              onChange: function (v) {
                patch(['dq', m.key, 'flwLevel'], v);
              },
            }),
          ),
          ce('div', { className: 'text-xs text-gray-500' }, m.describe),
          ce(NumberField, {
            label:
              'Threshold (flag when ' +
              (m.direction === 'low' ? '<' : '>') +
              ' N ' +
              m.unit +
              ')',
            value: mc.threshold,
            disabled: disabled || !mc.enabled,
            onChange: function (v) {
              patch(['dq', m.key, 'threshold'], v);
            },
          }),
        );
      }),
    ),
  );
}

// =========================================================================
// Ward summary table -- one row per ward in scope, totals only (no rates/
// candidates here, that's the detailed CandidateTable below this).
// =========================================================================

function WardSummaryTable(props) {
  var rows = props.rows;

  var summary = React.useMemo(
    function () {
      var byWard = {};
      var order = [];
      rows.forEach(function (r) {
        var w = r.ward || '(no ward)';
        if (!byWard[w]) {
          byWard[w] = { ward: w, waCount: 0, buildingCount: 0, evcTotal: 0 };
          order.push(w);
        }
        var s = byWard[w];
        s.waCount += 1;
        s.buildingCount += numOr(r.building_count, 0);
        s.evcTotal += numOr(r.expected_visit_count, 0);
      });
      return order.map(function (w) {
        return byWard[w];
      });
    },
    [rows],
  );

  if (summary.length === 0) return null;

  return ce(
    'div',
    {
      className:
        'bg-white rounded-lg shadow-sm border border-gray-200 overflow-x-auto',
    },
    ce(
      'table',
      { className: 'min-w-full divide-y divide-gray-200 text-sm' },
      ce(
        'thead',
        null,
        ce(
          'tr',
          null,
          ce(
            'th',
            {
              className:
                'px-3 py-2 text-left text-xs font-semibold uppercase bg-orange-900 text-orange-100',
            },
            'Ward',
          ),
          ce(
            'th',
            {
              className:
                'px-3 py-2 text-right text-xs font-semibold uppercase bg-orange-900 text-orange-100',
            },
            'Total Work Areas',
          ),
          ce(
            'th',
            {
              className:
                'px-3 py-2 text-right text-xs font-semibold uppercase bg-orange-900 text-orange-100',
            },
            'Total Building Count',
          ),
          ce(
            'th',
            {
              className:
                'px-3 py-2 text-right text-xs font-semibold uppercase bg-orange-900 text-orange-100',
            },
            'Total EVC',
          ),
        ),
      ),
      ce(
        'tbody',
        { className: 'divide-y divide-gray-100' },
        summary.map(function (s) {
          return ce(
            'tr',
            { key: s.ward },
            ce('td', { className: 'px-3 py-1.5' }, s.ward),
            ce(
              'td',
              { className: 'px-3 py-1.5 text-right tabular-nums' },
              fmtNum(s.waCount),
            ),
            ce(
              'td',
              { className: 'px-3 py-1.5 text-right tabular-nums' },
              fmtNum(s.buildingCount),
            ),
            ce(
              'td',
              { className: 'px-3 py-1.5 text-right tabular-nums' },
              fmtNum(s.evcTotal),
            ),
          );
        }),
      ),
    ),
  );
}

// =========================================================================
// Candidate table (Ward -> WAG -> WA -> FLW)
// =========================================================================

function CandidateTable(props) {
  var rows = props.rows,
    ruleResults = props.ruleResults,
    nameMap = props.nameMap,
    oppNames = props.oppNames;

  var _groupBy = React.useState('ward');
  var groupBy = _groupBy[0],
    setGroupBy = _groupBy[1];
  var _onlyCandidates = React.useState(true);
  var onlyCandidates = _onlyCandidates[0],
    setOnlyCandidates = _onlyCandidates[1];
  var _sort = React.useState({ col: 'evc_ratio', dir: 'asc' });
  var sort = _sort[0],
    setSort = _sort[1];

  var visible = React.useMemo(
    function () {
      var out = onlyCandidates
        ? rows.filter(function (r) {
            return ruleResults[r.key] && ruleResults[r.key].isCandidate;
          })
        : rows;
      var sorted = out.slice().sort(function (a, b) {
        var av, bv;
        if (sort.col === 'ward') {
          av = a.ward || '';
          bv = b.ward || '';
          return sort.dir === 'asc'
            ? av.localeCompare(bv)
            : bv.localeCompare(av);
        }
        if (sort.col === 'evc_ratio') {
          av = ruleResults[a.key] && ruleResults[a.key].evc.ratio;
          bv = ruleResults[b.key] && ruleResults[b.key].evc.ratio;
          av = av == null ? Infinity : av;
          bv = bv == null ? Infinity : bv;
        } else if (
          sort.col === 'building_count' ||
          sort.col === 'hsd_visit_count' ||
          sort.col === 'expected_visit_count'
        ) {
          av = numOr(a[sort.col], -Infinity);
          bv = numOr(b[sort.col], -Infinity);
        } else {
          av = 0;
          bv = 0;
        }
        return sort.dir === 'asc' ? av - bv : bv - av;
      });
      return sorted;
    },
    [rows, ruleResults, onlyCandidates, sort],
  );

  var grouped = React.useMemo(
    function () {
      var groups = {};
      var order = [];
      visible.forEach(function (r) {
        var g =
          groupBy === 'wag'
            ? r.work_area_group || '(no WAG)'
            : r.ward || '(no ward)';
        if (!groups[g]) {
          groups[g] = [];
          order.push(g);
        }
        groups[g].push(r);
      });
      return order.map(function (g) {
        return { group: g, rows: groups[g] };
      });
    },
    [visible, groupBy],
  );

  function onSort(col, dir) {
    setSort({ col: col, dir: dir });
  }

  return ce(
    'div',
    { className: 'bg-white rounded-lg shadow-sm border border-gray-200' },
    ce(
      'div',
      {
        className:
          'p-3 border-b border-gray-100 flex flex-wrap items-center gap-4',
      },
      ce(
        'label',
        { className: 'flex items-center gap-2 text-sm' },
        ce('input', {
          type: 'checkbox',
          checked: onlyCandidates,
          onChange: function (e) {
            setOnlyCandidates(e.target.checked);
          },
        }),
        'Show candidates only',
      ),
      ce(
        'label',
        { className: 'flex items-center gap-2 text-sm' },
        'Group by',
        ce(
          'select',
          {
            className: 'border border-gray-300 rounded px-2 py-1 text-sm',
            value: groupBy,
            onChange: function (e) {
              setGroupBy(e.target.value);
            },
          },
          ce('option', { value: 'ward' }, 'Ward'),
          ce('option', { value: 'wag' }, 'Work Area Group'),
        ),
      ),
      ce(
        'div',
        { className: 'text-sm text-gray-500 ml-auto' },
        visible.length + ' work area(s) shown',
      ),
    ),
    ce(
      'div',
      { className: 'overflow-x-auto' },
      ce(
        'table',
        { className: 'min-w-full divide-y divide-gray-200 text-sm' },
        ce(
          'thead',
          null,
          ce(
            'tr',
            null,
            ce(SortTh, {
              colKey: 'ward',
              label: 'Ward',
              sortCol: sort.col,
              sortDir: sort.dir,
              onSort: onSort,
            }),
            ce(
              'th',
              {
                className:
                  'px-3 py-2 text-left text-xs font-semibold uppercase bg-orange-900 text-orange-100',
              },
              'WAG',
            ),
            ce(
              'th',
              {
                className:
                  'px-3 py-2 text-left text-xs font-semibold uppercase bg-orange-900 text-orange-100',
              },
              'WA',
            ),
            ce(
              'th',
              {
                className:
                  'px-3 py-2 text-left text-xs font-semibold uppercase bg-orange-900 text-orange-100',
              },
              'FLW',
            ),
            ce(
              'th',
              {
                className:
                  'px-3 py-2 text-left text-xs font-semibold uppercase bg-orange-900 text-orange-100',
              },
              'Opp',
            ),
            ce(SortTh, {
              colKey: 'expected_visit_count',
              label: 'Expected',
              sortCol: sort.col,
              sortDir: sort.dir,
              onSort: onSort,
            }),
            ce(SortTh, {
              colKey: 'hsd_visit_count',
              label: 'HSD visits',
              sortCol: sort.col,
              sortDir: sort.dir,
              onSort: onSort,
            }),
            ce(SortTh, {
              colKey: 'evc_ratio',
              label: 'EVC %',
              sortCol: sort.col,
              sortDir: sort.dir,
              onSort: onSort,
            }),
            ce(
              'th',
              {
                className:
                  'px-3 py-2 text-left text-xs font-semibold uppercase bg-orange-900 text-orange-100',
              },
              'NCF/Inaccessible',
            ),
            ce(SortTh, {
              colKey: 'building_count',
              label: 'Buildings',
              sortCol: sort.col,
              sortDir: sort.dir,
              onSort: onSort,
            }),
            DQ_METRICS.map(function (m) {
              return ce(
                'th',
                {
                  key: m.key,
                  className:
                    'px-3 py-2 text-left text-xs font-semibold uppercase bg-orange-900 text-orange-100',
                },
                m.short,
              );
            }),
            ce(
              'th',
              {
                className:
                  'px-3 py-2 text-left text-xs font-semibold uppercase bg-orange-900 text-orange-100',
              },
              'Flags',
            ),
          ),
        ),
        ce(
          'tbody',
          { className: 'divide-y divide-gray-100' },
          grouped.map(function (g) {
            return ce(
              React.Fragment,
              { key: g.group },
              ce(
                'tr',
                { className: 'bg-orange-50' },
                ce(
                  'td',
                  {
                    colSpan: 11 + DQ_METRICS.length,
                    className:
                      'px-3 py-1 text-xs font-semibold text-orange-800',
                  },
                  g.group + ' (' + g.rows.length + ')',
                ),
              ),
              g.rows.map(function (r) {
                var res = ruleResults[r.key] || {
                  reasons: [],
                  evc: {},
                  ncf: {},
                  dq: {},
                };
                var flwName =
                  (nameMap && nameMap[joinKey(r.opportunity_id, r.owner_id)]) ||
                  r.owner_id ||
                  '—';
                var oppLabel =
                  (oppNames && oppNames[r.opportunity_id]) ||
                  'Opp #' + r.opportunity_id;
                var closure = closureDisplayLabel(r);
                return ce(
                  'tr',
                  {
                    key: r.key,
                    className: res.isCandidate ? 'bg-red-50/40' : '',
                  },
                  ce('td', { className: 'px-3 py-1.5' }, r.ward || '—'),
                  ce(
                    'td',
                    { className: 'px-3 py-1.5' },
                    r.work_area_group || '—',
                  ),
                  ce(
                    'td',
                    { className: 'px-3 py-1.5 font-mono text-xs' },
                    r.slug || r.entity_id.slice(0, 8),
                  ),
                  ce('td', { className: 'px-3 py-1.5' }, flwName),
                  ce(
                    'td',
                    { className: 'px-3 py-1.5 text-xs text-gray-500' },
                    oppLabel,
                  ),
                  ce(
                    'td',
                    { className: 'px-3 py-1.5 text-right tabular-nums' },
                    fmtNum(r.expected_visit_count),
                  ),
                  ce(
                    'td',
                    { className: 'px-3 py-1.5 text-right tabular-nums' },
                    fmtNum(r.hsd_visit_count),
                  ),
                  ce(
                    'td',
                    {
                      className:
                        'px-3 py-1.5 text-right tabular-nums ' +
                        (res.evc.applied ? 'text-red-700 font-semibold' : ''),
                      title: res.evc.excludedNotVisited
                        ? 'Not yet visited — excluded from EVC shortfall unless "Include not-yet-visited" is on'
                        : undefined,
                    },
                    res.evc.excludedNotVisited
                      ? 'not visited'
                      : res.evc.ratio == null
                      ? '—'
                      : fmtPct(res.evc.ratio * 100),
                  ),
                  ce(
                    'td',
                    {
                      className:
                        'px-3 py-1.5 text-xs' +
                        (res.ncf && res.ncf.countApplied
                          ? ' text-red-700 font-semibold'
                          : ''),
                      title: r.reason_for_inaccessible || '',
                    },
                    closure,
                  ),
                  ce(
                    'td',
                    { className: 'px-3 py-1.5 text-right tabular-nums' },
                    fmtNum(r.building_count),
                  ),
                  DQ_METRICS.map(function (m) {
                    var d = res.dq[m.key] || {};
                    return ce(
                      'td',
                      {
                        key: m.key,
                        className:
                          'px-3 py-1.5 text-right tabular-nums ' +
                          (d.waFails || d.flwFails
                            ? 'text-red-700 font-semibold'
                            : ''),
                      },
                      d.rate == null
                        ? '—'
                        : round1(d.rate) + (m.unit ? ' ' + m.unit : ''),
                    );
                  }),
                  ce(
                    'td',
                    { className: 'px-3 py-1.5' },
                    ce(ReasonBadges, { reasons: res.reasons }),
                  ),
                );
              }),
            );
          }),
        ),
      ),
    ),
  );
}

// =========================================================================
// Map panel — ConnectMap + PlanLayers.workAreas, candidate vs non-candidate.
// Ward boundary outline fetched via the existing microplans admin-boundary
// search endpoints (AdminAreasView / AdminAreaGeometryView) -- best-effort,
// never blocks the WA coloring (the main value of this map).
// =========================================================================

function MapPanel(props) {
  var rows = props.rows,
    ruleResults = props.ruleResults,
    wardFilter = props.wardFilter,
    anchorOppId = props.anchorOppId,
    // Program 217 is Nigeria-only; not wired up as a prop from WorkflowUI below
    // since there's no other country in scope for this template.
    country = props.country || 'NGA';

  var mapRef = React.useRef(null);
  var mapInstance = React.useRef(null);
  var _ready = React.useState(false);
  var ready = _ready[0],
    setReady = _ready[1];

  var geoRows = React.useMemo(
    function () {
      return rows.filter(function (r) {
        return r.hasGeometry;
      });
    },
    [rows],
  );

  React.useEffect(function () {
    if (!mapRef.current || !window.ConnectMap || mapInstance.current) return;
    var center = [8.6753, 9.082]; // Nigeria centroid fallback until data fits bounds
    if (
      geoRows.length > 0 &&
      geoRows[0].boundary &&
      geoRows[0].boundary.coordinates
    ) {
      try {
        var ring = geoRows[0].boundary.coordinates[0];
        center = ring[0];
      } catch (e) {
        // fall back to default center
      }
    }
    mapInstance.current = window.ConnectMap.createMap(mapRef.current, {
      center: center,
      zoom: 11,
    });
    mapInstance.current.on('load', function () {
      setReady(true);
    });
    return function () {
      mapInstance.current = null;
    };
    // eslint-disable-next-line
  }, []);

  React.useEffect(
    function () {
      if (!ready || !mapInstance.current || !window.PlanLayers) return;
      var fc = {
        type: 'FeatureCollection',
        features: geoRows.map(function (r) {
          var res = ruleResults[r.key];
          var isCandidate = res && res.isCandidate;
          return {
            type: 'Feature',
            geometry: r.boundary,
            properties: {
              id: r.entity_id,
              status: isCandidate ? 'CANDIDATE' : 'OK',
              fill: isCandidate ? '#dc2626' : '#16a34a',
              outline: isCandidate ? '#7f1d1d' : '#14532d',
            },
          };
        }),
      };
      window.PlanLayers.workAreas(mapInstance.current, { data: fc });
      if (fc.features.length > 0) {
        try {
          window.ConnectMap.fit(mapInstance.current, fc, 40);
        } catch (e) {
          // non-fatal -- map just keeps its current viewport
        }
      }
    },
    [ready, geoRows, ruleResults],
  );

  // Best-effort ward boundary outline via the existing admin-boundary search
  // endpoints (reused, not invented): AdminAreasView (name search) then
  // AdminAreaGeometryView (resolve to geometry). LEVEL_LOCALITY (3) = Ward in
  // NGA's admin vocabulary. opp_id in these URLs is a routing placeholder
  // (public boundary data, not opportunity-scoped), so any in-scope opp works.
  React.useEffect(
    function () {
      if (!ready || !mapInstance.current || !window.ConnectMap || !anchorOppId)
        return;
      if (!wardFilter || wardFilter.length !== 1) return; // ambiguous for multi-ward view
      var ward = wardFilter[0];
      var sampleRow = rows.filter(function (r) {
        return r.ward === ward;
      })[0];
      if (!sampleRow) return;
      var areasUrl = '/microplans/' + anchorOppId + '/boundaries/areas/';
      var geomUrl = '/microplans/' + anchorOppId + '/boundaries/geometry/';
      apiPost(areasUrl, { country: country, level: 3, q: ward })
        .then(function (res) {
          if (!res.ok || !res.data || !res.data.areas || !res.data.areas.length)
            return null;
          var areas = res.data.areas;
          var match =
            areas.filter(function (a) {
              return (
                (a.lga || '').toLowerCase() ===
                  (sampleRow.lga || '').toLowerCase() &&
                (a.state || '').toLowerCase() ===
                  (sampleRow.state || '').toLowerCase()
              );
            })[0] || areas[0];
          return apiPost(geomUrl, { area: match });
        })
        .then(function (res) {
          if (!res || !res.ok || !res.data || !res.data.geometry) return;
          var fc = {
            type: 'FeatureCollection',
            features: [
              { type: 'Feature', geometry: res.data.geometry, properties: {} },
            ],
          };
          window.ConnectMap.boundary(
            mapInstance.current,
            'mopup-ward-boundary',
            fc,
            {},
          );
        })
        .catch(function () {
          // best-effort only -- WA coloring above is the map's primary value
        });
    },
    [ready, wardFilter, anchorOppId, rows, country],
  );

  return ce(
    'div',
    { className: 'bg-white rounded-lg shadow-sm border border-gray-200 p-3' },
    ce(
      'div',
      { className: 'text-sm text-gray-500 mb-2' },
      geoRows.length +
        ' of ' +
        rows.length +
        ' work areas have geometry loaded',
    ),
    ce('div', {
      ref: mapRef,
      style: { height: '420px', width: '100%', borderRadius: '6px' },
      className: 'bg-gray-100',
    }),
    ce(
      'div',
      { className: 'flex gap-4 mt-2 text-xs text-gray-600' },
      ce(
        'div',
        { className: 'flex items-center gap-1' },
        ce('span', {
          className: 'inline-block w-3 h-3 rounded-sm',
          style: { background: '#dc2626' },
        }),
        'Candidate',
      ),
      ce(
        'div',
        { className: 'flex items-center gap-1' },
        ce('span', {
          className: 'inline-block w-3 h-3 rounded-sm',
          style: { background: '#16a34a' },
        }),
        'Not a candidate',
      ),
    ),
  );
}

// =========================================================================
// Main component
// =========================================================================

function WorkflowUI(props) {
  var definition = props.definition,
    instance = props.instance,
    workers = props.workers,
    pipelines = props.pipelines,
    onUpdateState = props.onUpdateState,
    view = props.view;

  var srcPipelines = (view && view.pipelines) || pipelines || {};
  var workAreaRows =
    (srcPipelines.work_areas && srcPipelines.work_areas.rows) || [];
  var geometryRows =
    (srcPipelines.wa_geometry && srcPipelines.wa_geometry.rows) || [];
  var visitQualityRows =
    (srcPipelines.visit_quality && srcPipelines.visit_quality.rows) || [];
  var auditEntryRows =
    (srcPipelines.audit_entries && srcPipelines.audit_entries.rows) || [];

  // instance.opportunity_ids is the documented multi-opp accessor (authoring
  // guide §8); definition.opportunity_ids is also populated at creation time
  // and is what chc_audit_history_render.js reads, so check both before
  // falling back to a single-opp array.
  var oppIds =
    (instance && instance.opportunity_ids) ||
    (definition && definition.opportunity_ids) ||
    (instance && [instance.opportunity_id]) ||
    [];

  var oppNames = React.useMemo(function () {
    var m = {};
    try {
      var el = document.getElementById('user-opportunities');
      if (el)
        JSON.parse(el.textContent).forEach(function (o) {
          m[o.id] = o.name;
        });
    } catch (e) {
      // no-op: falls back to "Opp #<id>" labels
    }
    return m;
  }, []);

  var nameMap = React.useMemo(
    function () {
      var m = {};
      (workers || []).forEach(function (w) {
        if (w.username)
          m[joinKey(w.opportunity_id, w.username)] = w.name || w.username;
      });
      return m;
    },
    [workers],
  );

  var waRows = React.useMemo(
    function () {
      return buildWaRows(workAreaRows, geometryRows, visitQualityRows);
    },
    [workAreaRows, geometryRows, visitQualityRows],
  );

  var allWards = React.useMemo(
    function () {
      var seen = {};
      var out = [];
      waRows.forEach(function (r) {
        var w = r.ward || '(no ward)';
        if (!seen[w]) {
          seen[w] = true;
          out.push(w);
        }
      });
      return out.sort();
    },
    [waRows],
  );

  // Advisory only, not a hard gate: a ward can still be picked and mopped up
  // before every one of its work areas is done (see isWaDone) -- this just
  // surfaces how far along each ward is so that's a deliberate choice, not an
  // accident of not noticing some WAs are still NOT_VISITED.
  var wardReadiness = React.useMemo(
    function () {
      var byWard = {};
      waRows.forEach(function (r) {
        var w = r.ward || '(no ward)';
        if (!byWard[w]) byWard[w] = { total: 0, done: 0 };
        byWard[w].total += 1;
        if (isWaDone(r)) byWard[w].done += 1;
      });
      return byWard;
    },
    [waRows],
  );

  // null = explicit "All wards" (user opted in, accepting the cost of
  // rendering every work area in scope); [] = nothing picked yet -- the
  // default, so the table/map/threshold panel start empty instead of
  // rendering (and re-rendering, on every threshold keystroke) tens of
  // thousands of rows before the reviewer has even chosen a ward. A
  // non-empty array is a specific ward selection. This only limits what's
  // rendered/computed client-side -- the pipeline data itself is still
  // fetched in full for every opportunity in scope up front (a multi-opp
  // workflow has no per-ward server-side fetch), so it doesn't speed up the
  // initial page load, only what happens after.
  var _wardFilter = React.useState([]);
  var wardFilter = _wardFilter[0],
    setWardFilter = _wardFilter[1];

  var savedState =
    (instance && instance.state && instance.state.mopup_candidates) || null;

  var _config = React.useState(function () {
    return (savedState && savedState.ruleConfig) || defaultRuleConfig();
  });
  var config = _config[0],
    setConfig = _config[1];

  var _locked = React.useState(!!(savedState && savedState.locked));
  var locked = _locked[0],
    setLocked = _locked[1];
  var _lockedWaKeys = React.useState(
    (savedState && savedState.lockedWaKeys) || [],
  );
  var lockedWaKeys = _lockedWaKeys[0],
    setLockedWaKeys = _lockedWaKeys[1];
  var _lastPlan = React.useState(
    (savedState && savedState.lastPlanResult) || null,
  );
  var lastPlan = _lastPlan[0],
    setLastPlan = _lastPlan[1];
  var _planName = React.useState((savedState && savedState.planName) || '');
  var planName = _planName[0],
    setPlanName = _planName[1];
  var _saving = React.useState(false);
  var saving = _saving[0],
    setSaving = _saving[1];
  var _creating = React.useState(false);
  var creating = _creating[0],
    setCreating = _creating[1];
  var _err = React.useState('');
  var errMsg = _err[0],
    setErrMsg = _err[1];

  var anyWardChosen =
    wardFilter === null || (!!wardFilter && wardFilter.length > 0);

  var scopedRows = React.useMemo(
    function () {
      if (wardFilter === null) return waRows; // explicit "All wards"
      if (!wardFilter || wardFilter.length === 0) return []; // nothing picked yet
      var set = {};
      wardFilter.forEach(function (w) {
        set[w] = true;
      });
      return waRows.filter(function (r) {
        return set[r.ward || '(no ward)'];
      });
    },
    [waRows, wardFilter],
  );

  var flwRollups = React.useMemo(
    function () {
      return buildFlwRollups(scopedRows);
    },
    [scopedRows],
  );

  var ruleResults = React.useMemo(
    function () {
      return evaluateRules(scopedRows, flwRollups, config);
    },
    [scopedRows, flwRollups, config],
  );

  var previewCandidateKeys = React.useMemo(
    function () {
      return Object.keys(ruleResults).filter(function (k) {
        return ruleResults[k].isCandidate;
      });
    },
    [ruleResults],
  );

  var lockedRows = React.useMemo(
    function () {
      var set = {};
      lockedWaKeys.forEach(function (k) {
        set[k] = true;
      });
      return waRows.filter(function (r) {
        return set[r.key];
      });
    },
    [waRows, lockedWaKeys],
  );

  function toggleWard(ward) {
    setWardFilter(function (prev) {
      // Was in explicit "All wards" mode -- clicking a specific ward exits
      // that mode and starts a fresh single-ward selection, rather than
      // trying to "remove" one ward from an implicit full set.
      if (prev === null) return [ward];
      var cur = prev.slice();
      var idx = cur.indexOf(ward);
      if (idx >= 0) cur.splice(idx, 1);
      else cur.push(ward);
      return cur;
    });
  }

  function handleLock() {
    setSaving(true);
    setErrMsg('');
    var keys = previewCandidateKeys.slice();
    var payload = {
      mopup_candidates: {
        locked: true,
        lockedAt: new Date().toISOString(),
        lockedWaKeys: keys,
        ruleConfig: config,
        planName: planName,
        lastPlanResult: lastPlan,
      },
    };
    Promise.resolve(onUpdateState ? onUpdateState(payload) : null)
      .then(function () {
        setLockedWaKeys(keys);
        setLocked(true);
      })
      .catch(function (e) {
        setErrMsg('Could not save the locked set: ' + e);
      })
      .finally(function () {
        setSaving(false);
      });
  }

  function handleUnlock() {
    setSaving(true);
    Promise.resolve(
      onUpdateState
        ? onUpdateState({
            mopup_candidates: {
              locked: false,
              lockedAt: null,
              lockedWaKeys: [],
              ruleConfig: config,
              planName: planName,
              lastPlanResult: lastPlan,
            },
          })
        : null,
    )
      .then(function () {
        setLocked(false);
        setLockedWaKeys([]);
      })
      .finally(function () {
        setSaving(false);
      });
  }

  function handleCreatePlan() {
    if (!locked || lockedRows.length === 0) return;
    setCreating(true);
    setErrMsg('');
    var missingGeom = lockedRows.filter(function (r) {
      return !r.hasGeometry;
    });
    var candidateWorkAreas = lockedRows
      .filter(function (r) {
        return r.hasGeometry;
      })
      .map(function (r) {
        return {
          ward: r.ward,
          lga: r.lga,
          state: r.state,
          geometry: r.boundary,
        };
      });
    // instance.program_id is the established accessor for a program-owned workflow
    // run's owning program (see program_audit_creator.py's identical usage);
    // definition.program_id is a defensive fallback only, not documented in the
    // Props Reference.
    var programId =
      (instance && instance.program_id) ||
      (definition && definition.program_id);
    var name =
      planName || 'CHC Mop-up ' + new Date().toISOString().slice(0, 10);
    var url = '/microplans/program/' + programId + '/plan/create_mopup/';
    apiPost(url, {
      candidate_work_areas: candidateWorkAreas,
      opportunity_ids: oppIds,
      name: name,
      grouping: {},
      config: {},
    })
      .then(function (res) {
        if (!res.ok || !res.data || res.data.status === 'error') {
          setErrMsg(
            (res.data && res.data.detail) ||
              'Could not create the mop-up plan.',
          );
          return;
        }
        var result = {
          plan_id: res.data.plan_id,
          urls: res.data.urls,
          name: name,
          created_at: new Date().toISOString(),
        };
        setLastPlan(result);
        return onUpdateState
          ? onUpdateState({
              mopup_candidates: {
                locked: locked,
                lockedAt: savedState
                  ? savedState.lockedAt
                  : new Date().toISOString(),
                lockedWaKeys: lockedWaKeys,
                ruleConfig: config,
                planName: planName,
                lastPlanResult: result,
              },
            })
          : null;
      })
      .catch(function (e) {
        setErrMsg('Could not create the mop-up plan: ' + e);
      })
      .finally(function () {
        setCreating(false);
      });
    if (missingGeom.length > 0) {
      // eslint-disable-next-line no-console
      console.warn(
        'CHC mop-up: ' +
          missingGeom.length +
          ' locked WA(s) have no geometry and were excluded from hand-off.',
      );
    }
  }

  var kpi = React.useMemo(
    function () {
      return {
        totalWas: scopedRows.length,
        previewCount: previewCandidateKeys.length,
        lockedCount: locked ? lockedRows.length : 0,
        wards: allWards.length,
      };
    },
    [scopedRows, previewCandidateKeys, locked, lockedRows, allWards],
  );

  return ce(
    'div',
    { className: 'min-h-screen bg-gray-50' },
    ce(
      'div',
      { className: 'bg-orange-900 text-white px-6 py-4' },
      ce(
        'div',
        { className: 'text-xs uppercase tracking-widest opacity-60 mb-1' },
        'Program 217 · CHC - NG - RCT - Aug 2026',
      ),
      ce(
        'div',
        { className: 'text-xl font-bold tracking-tight' },
        'CHC Mop-up Candidate Analysis',
      ),
      ce(
        'div',
        { className: 'flex gap-2 mt-2 flex-wrap' },
        oppIds.map(function (id) {
          return ce(
            'span',
            {
              key: id,
              className:
                'text-xs px-2 py-0.5 bg-white/10 border border-white/20 rounded',
            },
            oppNames[id] || 'Opp #' + id,
          );
        }),
      ),
    ),
    ce(
      'div',
      { className: 'p-6 space-y-4' },
      errMsg
        ? ce(
            'div',
            {
              className:
                'bg-red-50 border border-red-200 text-red-700 text-sm rounded p-3',
            },
            errMsg,
          )
        : null,

      ce(
        'div',
        { className: 'grid grid-cols-2 md:grid-cols-4 gap-4' },
        ce(KpiCard, { value: kpi.wards, label: 'Wards in scope' }),
        ce(KpiCard, { value: kpi.totalWas, label: 'Work areas in scope' }),
        ce(KpiCard, {
          value: kpi.previewCount,
          label: 'Live preview candidates',
          tone: 'text-orange-700',
        }),
        ce(KpiCard, {
          value: locked ? kpi.lockedCount : '—',
          label: locked ? 'Locked candidate set' : 'Not locked yet',
          tone: locked ? 'text-red-700' : 'text-gray-400',
        }),
      ),

      ce(
        'div',
        {
          className: 'bg-white rounded-lg shadow-sm border border-gray-200 p-3',
        },
        ce(
          'div',
          { className: 'text-sm font-medium text-gray-800 mb-2' },
          'Ward selection (' + allWards.length + ' wards)',
        ),
        ce(
          'div',
          { className: 'flex flex-wrap gap-1 max-h-32 overflow-y-auto' },
          ce(
            'button',
            {
              type: 'button',
              className:
                'px-2 py-1 rounded text-xs border ' +
                (wardFilter === null
                  ? 'bg-orange-600 text-white border-orange-600'
                  : 'bg-white text-gray-600 border-gray-300'),
              title:
                'Loads every work area in scope at once — can be slow with tens of thousands of rows.',
              onClick: function () {
                setWardFilter(null);
              },
            },
            'All wards',
          ),
          allWards.map(function (w) {
            var active = !!(wardFilter && wardFilter.indexOf(w) !== -1);
            var ready = wardReadiness[w] || { total: 0, done: 0 };
            var allDone = ready.total > 0 && ready.done === ready.total;
            return ce(
              'button',
              {
                key: w,
                type: 'button',
                title:
                  ready.done +
                  ' of ' +
                  ready.total +
                  ' work areas visited / expected-visit-reached / inaccessible' +
                  (allDone
                    ? ' — ward fully done'
                    : ' — ward still in progress (not a blocker, just informational)'),
                className:
                  'px-2 py-1 rounded text-xs border ' +
                  (active
                    ? 'bg-orange-600 text-white border-orange-600'
                    : 'bg-white text-gray-600 border-gray-300'),
                onClick: function () {
                  toggleWard(w);
                },
              },
              w + ' ',
              ce(
                'span',
                { className: active ? 'opacity-80' : 'text-gray-400' },
                allDone ? '✓' : '(' + ready.done + '/' + ready.total + ')',
              ),
            );
          }),
        ),
      ),

      ce(
        'div',
        { className: 'grid grid-cols-1 lg:grid-cols-3 gap-4' },
        ce(
          'div',
          { className: 'lg:col-span-1' },
          ce(ThresholdPanel, {
            config: config,
            setConfig: setConfig,
            disabled: locked,
          }),
        ),
        ce(
          'div',
          { className: 'lg:col-span-2 space-y-4' },
          !anyWardChosen
            ? ce(
                'div',
                {
                  className:
                    'bg-white rounded-lg shadow-sm border border-gray-200 p-8 text-center text-sm text-gray-500',
                },
                'Select one or more wards above (or "All wards") to load the map and tables.',
              )
            : [
                ce(MapPanel, {
                  key: 'map',
                  rows: scopedRows,
                  ruleResults: ruleResults,
                  wardFilter: wardFilter,
                  anchorOppId: oppIds[0],
                }),
                ce(WardSummaryTable, { key: 'summary', rows: scopedRows }),
                ce(CandidateTable, {
                  key: 'table',
                  rows: scopedRows,
                  ruleResults: ruleResults,
                  nameMap: nameMap,
                  oppNames: oppNames,
                }),
              ],
        ),
      ),

      ce(
        'div',
        {
          className:
            'bg-white rounded-lg shadow-sm border border-gray-200 p-4 space-y-3',
        },
        ce(
          'h3',
          { className: 'font-semibold text-gray-900' },
          'Lock & hand off',
        ),
        ce(
          'div',
          { className: 'text-sm text-gray-600' },
          locked
            ? 'Locked ' +
                lockedRows.length +
                ' work area(s) on ' +
                (savedState && savedState.lockedAt
                  ? new Date(savedState.lockedAt).toLocaleString()
                  : '—') +
                '. Thresholds above are frozen for the hand-off — unlock to keep tuning.'
            : 'Thresholds only drive a live preview count until you lock. The hand-off below only ever acts on the locked set.',
        ),
        ce(
          'div',
          { className: 'flex flex-wrap items-center gap-3' },
          !locked
            ? ce(
                'button',
                {
                  type: 'button',
                  className:
                    'px-4 py-2 bg-orange-700 text-white text-sm font-medium rounded hover:bg-orange-800 disabled:opacity-50',
                  disabled: saving || previewCandidateKeys.length === 0,
                  onClick: handleLock,
                },
                saving
                  ? 'Locking…'
                  : 'Lock candidate set (' + previewCandidateKeys.length + ')',
              )
            : ce(
                'button',
                {
                  type: 'button',
                  className:
                    'px-4 py-2 bg-gray-200 text-gray-800 text-sm font-medium rounded hover:bg-gray-300 disabled:opacity-50',
                  disabled: saving,
                  onClick: handleUnlock,
                },
                saving ? 'Unlocking…' : 'Unlock (edit thresholds again)',
              ),
          ce('input', {
            type: 'text',
            placeholder: 'Mop-up plan name (optional)',
            className:
              'border border-gray-300 rounded px-3 py-2 text-sm flex-1 min-w-[200px]',
            value: planName,
            onChange: function (e) {
              setPlanName(e.target.value);
            },
          }),
          ce(
            'button',
            {
              type: 'button',
              className:
                'px-4 py-2 bg-green-700 text-white text-sm font-medium rounded hover:bg-green-800 disabled:opacity-50',
              disabled: !locked || creating || lockedRows.length === 0,
              onClick: handleCreatePlan,
            },
            creating ? 'Creating…' : 'Create mop-up microplan',
          ),
        ),
        lastPlan
          ? ce(
              'div',
              {
                className:
                  'bg-green-50 border border-green-200 rounded p-3 text-sm text-green-800',
              },
              'Mop-up plan #' + lastPlan.plan_id + ' created. ',
              lastPlan.urls && lastPlan.urls.review
                ? ce(
                    'a',
                    {
                      href: lastPlan.urls.review,
                      className: 'underline font-medium',
                      target: '_blank',
                      rel: 'noreferrer',
                    },
                    'Open in the review/exclude/group/CSV flow →',
                  )
                : null,
            )
          : null,
      ),

      auditEntryRows.length > 0
        ? ce(
            'div',
            { className: 'text-xs text-gray-400' },
            "Connect's own FLW-week audit context (" +
              auditEntryRows.length +
              ' entries) is loaded but not used for filtering — see audit_entries pipeline.',
          )
        : null,
    ),
  );
}
