// =========================================================================
// V5 compute helpers — port of v4's Python job handler to JS.
//
// Pure functions (no React state). Match the algorithms in
// commcare_connect/workflow/job_handlers/mbw_auditing_v4.py line-for-line so
// that v5 produces byte-identical numbers vs v4 against the same opp data.
// Critical invariants:
//   - visits sorted chronologically ASC so last-write-wins mother→FLW
//     attribution matches v4's `mother_to_flw[mid] = username`.
//   - form_name normalized via _FORM_NAME_ALIASES before keying into the
//     visits_by_mother map (matches v4 line 224-225).
//   - grace_cutoff = now - 7 days (matches v4 _GRACE_PERIOD_DAYS = 7).
//   - All date comparisons use YYYY-MM-DD string slices to avoid timezone
//     drift between Python's tz-aware datetime and JS Date.
// =========================================================================

var V5_GRACE_PERIOD_DAYS = 7;

// Banker's rounding to match Python's round() behavior. JS's Math.round
// rounds .5 away from zero (16.5 → 17); Python's round rounds .5 to nearest
// even (16.5 → 16, 17.5 → 18). Without this, v5 produces off-by-one diffs
// from v4 on any metric whose pre-rounded value lands exactly on .5 —
// which happens routinely with means of small integer samples.
function v5_round(x) {
  if (x == null || !isFinite(x)) return x;
  if (x < 0) return -v5_round(-x);
  var floor = Math.floor(x);
  var frac = x - floor;
  // Tolerance handles float-representation drift (e.g. 0.1+0.2-0.3 ≠ 0).
  if (Math.abs(frac - 0.5) < 1e-9) {
    return floor % 2 === 0 ? floor : floor + 1;
  }
  return Math.round(x);
}

// Form name aliases — visit_type keys produced by the mbw_visit_schedules
// extractor have specific canonical names; raw form names from CCHQ submissions
// may differ. Match v4 _FORM_NAME_ALIASES exactly.
var V5_FORM_NAME_ALIASES = {
  // None today — v4 reads form.@name which already matches visit_type for the
  // standard MBW deployment. Kept as an extension point if a downstream opp
  // needs renaming.
};

function v5_normFormName(rawName) {
  var trimmed = (rawName || '').replace(/^\s+|\s+$/g, '');
  return V5_FORM_NAME_ALIASES[trimmed] || trimmed;
}

function v5_dateStr(s) {
  // Slice to YYYY-MM-DD. v4 does this with `[:10]`.
  return (s || '').slice(0, 10);
}

function v5_subtractDays(dateStr, days) {
  // dateStr is YYYY-MM-DD. Returns a YYYY-MM-DD string `days` days earlier.
  var d = new Date(dateStr + 'T00:00:00Z');
  d.setUTCDate(d.getUTCDate() - days);
  var y = d.getUTCFullYear();
  var m = String(d.getUTCMonth() + 1).padStart(2, '0');
  var dd = String(d.getUTCDate()).padStart(2, '0');
  return y + '-' + m + '-' + dd;
}

function v5_todayStr(currentDateOverride) {
  if (currentDateOverride) return v5_dateStr(currentDateOverride);
  var n = new Date();
  var y = n.getUTCFullYear();
  var m = String(n.getUTCMonth() + 1).padStart(2, '0');
  var dd = String(n.getUTCDate()).padStart(2, '0');
  return y + '-' + m + '-' + dd;
}

// Process visits rows: single chronological pass building all per-FLW and
// per-mother indices. Mirrors v4 handler lines 174-290.
function v5_processVisits(visitsRows, taskFilters) {
  taskFilters = taskFilters || {};

  // Sort chronologically in-place — same as v4 line 174. The sort is what
  // makes `mother_to_flw[mid] = username` produce last-visit-wins attribution.
  var sorted = visitsRows.slice().sort(function (a, b) {
    var ad = a.visit_datetime || '';
    var bd = b.visit_datetime || '';
    return ad < bd ? -1 : ad > bd ? 1 : 0;
  });

  var motherToFlw = {};
  var visitsByMother = {}; // mid → {form_name → date} post-trigger only
  var visitsByMotherAll = {}; // mid → {form_name → earliest date} all visits
  var gpsDistances = {};
  var visitDurations = {};
  var interVisitGaps = {};
  var lastVisitEnd = {};
  var visitsCompletedByFlw = {};
  var ebfCountByFlw = {};
  var bfCountByFlw = {};
  var motherSetsByFlw = {}; // every-visit attribution
  var ancOkMothers = {}; // set of mothers with any anc_ok visit (global)

  for (var i = 0; i < sorted.length; i++) {
    var row = sorted[i];
    var username = ((row.username || row._username || '') + '').toLowerCase();
    if (!username) continue;

    var vdt = v5_dateStr(row.visit_datetime);
    var mid = ((row.mother_case_id || '') + '').toLowerCase();
    var formName = v5_normFormName(row.form_name);

    // Always update attribution and all-time visit history (needed for
    // baseline follow-up rate even when task_filters skips below).
    if (mid) {
      motherToFlw[mid] = username;
      if (formName && vdt) {
        if (!visitsByMotherAll[mid]) visitsByMotherAll[mid] = {};
        if (
          !visitsByMotherAll[mid][formName] ||
          vdt < visitsByMotherAll[mid][formName]
        ) {
          visitsByMotherAll[mid][formName] = vdt; // earliest visit date per (mother, type)
        }
      }
      if ((row.antenatal_visit_completion || '').toString().trim() === 'ok') {
        ancOkMothers[mid] = true;
      }
    }

    // For Tab 2: skip visits submitted before this FLW's task trigger date.
    if (
      taskFilters &&
      Object.prototype.hasOwnProperty.call(taskFilters, username)
    ) {
      var trigger = v5_dateStr(taskFilters[username] || '');
      if (vdt && trigger && vdt < trigger) {
        continue;
      }
    }

    visitsCompletedByFlw[username] = (visitsCompletedByFlw[username] || 0) + 1;

    if (mid) {
      if (!motherSetsByFlw[username]) motherSetsByFlw[username] = {};
      motherSetsByFlw[username][mid] = true;
      if (formName && vdt) {
        if (!visitsByMother[mid]) visitsByMother[mid] = {};
        visitsByMother[mid][formName] = vdt;
      }
    }

    var dist = row.distance_from_prev_case_visit_m;
    if (dist != null) {
      var distF = parseFloat(dist);
      if (!isNaN(distF)) {
        if (!gpsDistances[username]) gpsDistances[username] = [];
        gpsDistances[username].push(distF);
      }
    }

    var tsStart = row.time_start || '';
    var tsEnd = row.visit_datetime || '';
    if (tsStart && tsEnd) {
      var t0 = Date.parse(tsStart.replace('Z', '+00:00'));
      var t1 = Date.parse(tsEnd.replace('Z', '+00:00'));
      if (!isNaN(t0) && !isNaN(t1)) {
        var mins = (t1 - t0) / 60000;
        if (mins > 0 && mins < 300) {
          if (!visitDurations[username]) visitDurations[username] = [];
          visitDurations[username].push(mins);
        }
        // Inter-visit gap: from previous visit_end to this visit_start.
        // Sorted chronologically so lastVisitEnd[username] is the prior visit.
        var prevEnd = lastVisitEnd[username];
        if (prevEnd) {
          var tPrev = Date.parse(prevEnd.replace('Z', '+00:00'));
          if (!isNaN(tPrev)) {
            var gapMins = (t0 - tPrev) / 60000;
            if (gapMins > 0 && gapMins < 480) {
              if (!interVisitGaps[username]) interVisitGaps[username] = [];
              interVisitGaps[username].push(gapMins);
            }
          }
        }
        lastVisitEnd[username] = tsEnd;
      }
    }

    var bfStatus = (row.bf_status || '').toString().trim();
    if (bfStatus) {
      bfCountByFlw[username] = (bfCountByFlw[username] || 0) + 1;
      var tokens = bfStatus.split(/\s+/);
      if (tokens.indexOf('ebf') !== -1) {
        ebfCountByFlw[username] = (ebfCountByFlw[username] || 0) + 1;
      }
    }
  }

  return {
    motherToFlw: motherToFlw,
    visitsByMother: visitsByMother,
    visitsByMotherAll: visitsByMotherAll,
    gpsDistances: gpsDistances,
    visitDurations: visitDurations,
    interVisitGaps: interVisitGaps,
    visitsCompletedByFlw: visitsCompletedByFlw,
    ebfCountByFlw: ebfCountByFlw,
    bfCountByFlw: bfCountByFlw,
    motherSetsByFlw: motherSetsByFlw,
    ancOkMothers: ancOkMothers,
  };
}

// Process registrations rows. Mirrors v4 handler lines 293-315.
function v5_processRegistrations(regRows) {
  var motherSchedules = {};
  var motherEligibility = {};
  for (var i = 0; i < regRows.length; i++) {
    var row = regRows[i];
    var schedules = row.schedules || [];
    if (!Array.isArray(schedules) || schedules.length === 0) continue;
    var mid = '';
    for (var j = 0; j < schedules.length; j++) {
      var s = schedules[j];
      if (s && typeof s === 'object') {
        mid = ((s.mother_case_id || '') + '').toLowerCase();
        if (mid) break;
      }
    }
    if (!mid) mid = ((row.mother_case_id || '') + '').toLowerCase();
    if (!mid) continue;
    motherSchedules[mid] = schedules;
    var elig = ((row.eligible_full_intervention_bonus || '') + '').trim();
    motherEligibility[mid] = elig === '1';
  }
  return {
    motherSchedules: motherSchedules,
    motherEligibility: motherEligibility,
  };
}

// Process GS forms rows. Mirrors v4 handler lines 317-329.
function v5_processGsForms(gsRows) {
  var gsByUser = {};
  for (var i = 0; i < gsRows.length; i++) {
    var row = gsRows[i];
    var cid = ((row.user_connect_id || row.username || '') + '').toLowerCase();
    var raw = row.gs_score;
    if (!cid || raw == null) continue;
    var score = parseFloat(raw);
    if (isNaN(score)) continue;
    if (gsByUser[cid] == null || score > gsByUser[cid]) {
      gsByUser[cid] = score;
    }
  }
  return gsByUser;
}

// Compute per-FLW follow-up rate, total_eligible, still_eligible. Mirrors v4
// handler lines 331-399.
function v5_computeFollowupRates({
  motherSchedules,
  visitsByMother,
  motherToFlw,
  motherEligibility,
  ancOkMothers,
  activeUsernames,
  today,
  graceCutoff,
}) {
  var flwFu = {};
  var activeSet = null;
  if (activeUsernames && activeUsernames.length) {
    activeSet = {};
    for (var i = 0; i < activeUsernames.length; i++) {
      activeSet[activeUsernames[i].toLowerCase()] = true;
    }
  }

  for (var mid in motherSchedules) {
    if (!Object.prototype.hasOwnProperty.call(motherSchedules, mid)) continue;
    var schedules = motherSchedules[mid];
    var flw = motherToFlw[mid];
    if (!flw) continue;
    if (activeSet && !activeSet[flw]) continue;

    var isEligible = !!(motherEligibility[mid] && ancOkMothers[mid]);
    var motherVisits = visitsByMother[mid] || {};

    if (!flwFu[flw]) {
      flwFu[flw] = {
        total_eligible: 0,
        filtered_completed: 0,
        filtered_denominator: 0,
        still_eligible: 0,
      };
    }
    var bucket = flwFu[flw];

    if (isEligible) bucket.total_eligible += 1;

    var missedCount = 0;
    for (var j = 0; j < schedules.length; j++) {
      var s = schedules[j];
      if (!s || typeof s !== 'object') continue;
      var visitType = s.visit_type || '';
      if (visitType === 'ANC Visit') continue;
      var scheduledStr = v5_dateStr(s.visit_date_scheduled);
      var expiryStr = v5_dateStr(s.visit_expiry_date);
      var isCompleted = !!motherVisits[visitType];

      var pastGrace = scheduledStr && scheduledStr <= graceCutoff;

      if (!isCompleted && expiryStr && expiryStr < today) {
        missedCount += 1;
      }

      if (pastGrace) {
        bucket.filtered_denominator += 1;
        if (isCompleted) bucket.filtered_completed += 1;
      }
    }

    if (isEligible && missedCount < 2) {
      bucket.still_eligible += 1;
    }
  }

  return flwFu;
}

// Per-FLW baseline follow-up rates as of the FLW's trigger date. Mirrors v4
// handler lines 401-458.
function v5_computeBaselineFollowupRates({
  motherSchedules,
  visitsByMotherAll,
  motherToFlw,
  taskFilters,
}) {
  var baseline = {};
  if (!taskFilters || Object.keys(taskFilters).length === 0) return baseline;

  // Pre-index mothers by attributed FLW so the per-FLW loop doesn't scan all.
  var mothersByFlw = {};
  for (var mid in motherSchedules) {
    if (!Object.prototype.hasOwnProperty.call(motherSchedules, mid)) continue;
    var flw = motherToFlw[mid];
    if (flw) {
      if (!mothersByFlw[flw]) mothersByFlw[flw] = [];
      mothersByFlw[flw].push(mid);
    }
  }

  Object.keys(taskFilters).forEach(function (flwUsername) {
    var triggerDateStr = v5_dateStr(taskFilters[flwUsername]);
    if (!triggerDateStr) return;
    var triggerGraceCutoff = v5_subtractDays(
      triggerDateStr,
      V5_GRACE_PERIOD_DAYS,
    );

    var baselineCompleted = 0;
    var baselineDenominator = 0;
    var mothers = mothersByFlw[flwUsername] || [];

    for (var k = 0; k < mothers.length; k++) {
      var mid2 = mothers[k];
      var schedules = motherSchedules[mid2];
      var motherVisits = visitsByMotherAll[mid2] || {};
      for (var jj = 0; jj < schedules.length; jj++) {
        var s = schedules[jj];
        if (!s || typeof s !== 'object') continue;
        var visitType = s.visit_type || '';
        if (visitType === 'ANC Visit') continue;
        var scheduledStr = v5_dateStr(s.visit_date_scheduled);
        var visitDate = motherVisits[visitType] || '';
        var isCompletedAtTrigger =
          !!visitDate && visitDate.slice(0, 10) <= triggerDateStr;
        var pastGrace = scheduledStr && scheduledStr <= triggerGraceCutoff;

        if (pastGrace) {
          baselineDenominator += 1;
          if (isCompletedAtTrigger) baselineCompleted += 1;
        }
      }
    }

    baseline[flwUsername] =
      baselineDenominator > 0
        ? v5_round((baselineCompleted / baselineDenominator) * 100)
        : null;
  });

  return baseline;
}

// Compose the per-FLW summary rows. Mirrors v4 handler lines 460-561.
function v5_computeMbwAuditingData({
  visitsRows,
  visitsAggRows,
  regRows,
  gsRows,
  activeUsernames,
  flwNames,
  taskFilters,
  currentDate,
}) {
  var today = v5_todayStr(currentDate);
  var graceCutoff = v5_subtractDays(today, V5_GRACE_PERIOD_DAYS);

  var v = v5_processVisits(visitsRows || [], taskFilters || {});
  var r = v5_processRegistrations(regRows || []);
  var gsByUser = v5_processGsForms(gsRows || []);

  // When visits_agg rows are available AND no task filter (Tab 1 only), use
  // SQL-aggregated counts for num_mothers/bf_count/ebf_count instead of the
  // per-visit-scan values. Matches v4's `use_agg_counts` branch (line 179).
  var useAggCounts = !!(visitsAggRows && visitsAggRows.length) && !taskFilters;
  var numMothersByFlw = {};
  if (useAggCounts) {
    for (var i = 0; i < visitsAggRows.length; i++) {
      var aggRow = visitsAggRows[i];
      var u = ((aggRow.username || aggRow._username || '') + '').toLowerCase();
      if (!u) continue;
      var nm = parseInt(aggRow.num_mothers || 0, 10);
      if (!isNaN(nm)) numMothersByFlw[u] = nm;
      var bc = parseInt(aggRow.bf_count || 0, 10);
      if (!isNaN(bc)) v.bfCountByFlw[u] = bc;
      var ec = parseInt(aggRow.ebf_count || 0, 10);
      if (!isNaN(ec)) v.ebfCountByFlw[u] = ec;
    }
  }

  var flwFu = v5_computeFollowupRates({
    motherSchedules: r.motherSchedules,
    visitsByMother: v.visitsByMother,
    motherToFlw: v.motherToFlw,
    motherEligibility: r.motherEligibility,
    ancOkMothers: v.ancOkMothers,
    activeUsernames: activeUsernames || [],
    today: today,
    graceCutoff: graceCutoff,
  });

  var baselineRates = v5_computeBaselineFollowupRates({
    motherSchedules: r.motherSchedules,
    visitsByMotherAll: v.visitsByMotherAll,
    motherToFlw: v.motherToFlw,
    taskFilters: taskFilters,
  });

  // Build FLW summaries. Match v4 line 475-561 exactly.
  var activeSet = (activeUsernames || []).map(function (u) {
    return u.toLowerCase();
  });
  var targetSet;
  if (activeSet.length > 0) {
    targetSet = activeSet;
  } else {
    // v4 fallback: set of all attributed FLWs (mother_to_flw values).
    var set = {};
    Object.values(v.motherToFlw).forEach(function (u) {
      if (u) set[u] = true;
    });
    targetSet = Object.keys(set);
  }
  targetSet.sort();

  var summaries = [];
  for (var n = 0; n < targetSet.length; n++) {
    var username = targetSet[n];
    var u = username.toLowerCase();
    var fu = flwFu[u] || {};
    var dists = v.gpsDistances[u] || [];

    var mothersVisited = v.motherSetsByFlw[u] || {};
    var numMothers = useAggCounts
      ? numMothersByFlw[u] || 0
      : Object.keys(mothersVisited).length;
    var totalEligible = fu.total_eligible || 0;
    var eligibleMothersVisited = 0;
    Object.keys(mothersVisited).forEach(function (mid3) {
      if (r.motherEligibility[mid3]) eligibleMothersVisited += 1;
    });
    var visitsCompleted = v.visitsCompletedByFlw[u] || 0;

    var bfCount = v.bfCountByFlw[u] || 0;
    var ebfCount = v.ebfCountByFlw[u] || 0;
    var ebfPct = bfCount > 0 ? v5_round((ebfCount / bfCount) * 100) : null;

    var denom = fu.filtered_denominator || 0;
    var completedFu = fu.filtered_completed || 0;
    var followupRate = denom > 0 ? v5_round((completedFu / denom) * 100) : null;

    var stillElig = fu.still_eligible || 0;
    var pctStillEligible =
      totalEligible > 0 ? v5_round((stillElig / totalEligible) * 100) : null;

    var revisitM = null;
    var meterPerVisit = null;
    var distRatio = null;
    if (dists.length > 0) {
      var sum = 0;
      for (var dd = 0; dd < dists.length; dd++) sum += dists[dd];
      var meanM = sum / dists.length;
      var sortedD = dists.slice().sort(function (a, b) {
        return a - b;
      });
      var medianM = sortedD[Math.floor(sortedD.length / 2)];
      revisitM = v5_round(meanM);
      meterPerVisit = v5_round(medianM);
      // dist_ratio is round(meanM/medianM, 2) in v4. Banker's rounding at
      // the 2-decimal grain matches what Python does to floats.
      distRatio = medianM > 0 ? v5_round((meanM / medianM) * 100) / 100 : null;
    }

    var gsRaw = gsByUser[u];
    var gsScore = gsRaw != null ? v5_round(gsRaw) : null;

    var durations = v.visitDurations[u] || [];
    var minutePerVisit = null;
    if (durations.length > 0) {
      var sortedDur = durations.slice().sort(function (a, b) {
        return a - b;
      });
      minutePerVisit = v5_round(sortedDur[Math.floor(sortedDur.length / 2)]);
    }

    var gaps = v.interVisitGaps[u] || [];
    var travelTime = null;
    if (gaps.length > 0) {
      var sortedGaps = gaps.slice().sort(function (a, b) {
        return a - b;
      });
      travelTime = v5_round(sortedGaps[Math.floor(sortedGaps.length / 2)]);
    }

    summaries.push({
      username: u,
      display_name: (flwNames && (flwNames[u] || flwNames[username])) || u,
      num_mothers: numMothers,
      num_mothers_eligible: totalEligible,
      num_eligible_mothers_visited: eligibleMothersVisited,
      visits_completed: visitsCompleted,
      gs_score: gsScore,
      followup_rate: followupRate,
      followup_rate_denom: denom,
      followup_rate_at_trigger: taskFilters
        ? baselineRates[u] != null
          ? baselineRates[u]
          : null
        : null,
      pct_still_eligible: pctStillEligible,
      ebf_pct: ebfPct,
      ebf_denom: bfCount,
      revisit_dist: revisitM,
      gps_denom: dists.length,
      meter_per_visit: meterPerVisit,
      dist_ratio: distRatio,
      minute_per_visit: minutePerVisit,
      duration_denom: durations.length,
      travel_time: travelTime,
      travel_time_denom: gaps.length,
    });
  }

  return { flw_summaries: summaries };
}

function WorkflowUI({
  definition,
  instance,
  workers,
  pipelines,
  view,
  links,
  actions,
  onUpdateState,
}) {
  // =========================================================================
  // Constants
  // =========================================================================
  var THRESHOLDS = {
    gs_red: 50,
    fu_red: 50,
    fu_yellow: 80,
    pct_still_elig_red: 50,
    pct_still_elig_yellow: 80,
    ebf_low: 30,
    ebf_high: 94,
    dist_ratio_low: 1.0,
    worsened_pct: 10,
  };

  var PERF_CATEGORIES = [
    {
      id: 'eligible_for_renewal',
      label: 'Eligible for Renewal',
      icon: 'fa-circle-check',
      active: 'bg-green-600 text-white border-green-600',
      inactive:
        'bg-green-50 text-green-800 border-green-300 hover:bg-green-100',
    },
    {
      id: 'requires_improvement',
      label: 'Requires Improvement',
      icon: 'fa-triangle-exclamation',
      active: 'bg-amber-600 text-white border-amber-600',
      inactive:
        'bg-amber-50 text-amber-800 border-amber-300 hover:bg-amber-100',
    },
    {
      id: 'suspended',
      label: 'Suspension',
      icon: 'fa-ban',
      active: 'bg-red-600 text-white border-red-600',
      inactive: 'bg-red-50 text-red-800 border-red-300 hover:bg-red-100',
    },
  ];

  var METRIC_COLS = [
    { key: 'gs_score', label: 'GS Score', fmt: 'pct', higherBetter: true },
    {
      key: 'followup_rate',
      label: 'Follow-up Rate',
      fmt: 'pct',
      higherBetter: true,
      denomKey: 'followup_rate_denom',
    },
    {
      key: 'pct_still_eligible',
      label: '% Still Eligible',
      fmt: 'pct',
      higherBetter: true,
    },
    {
      key: 'ebf_pct',
      label: 'EBF %',
      fmt: 'pct',
      higherBetter: true,
      denomKey: 'ebf_denom',
    },
    {
      key: 'revisit_dist',
      label: 'Revisit Dist (m)',
      fmt: 'int',
      higherBetter: false,
      denomKey: 'gps_denom',
    },
    {
      key: 'meter_per_visit',
      label: 'Meter/Visit',
      fmt: 'int',
      higherBetter: null,
      denomKey: 'gps_denom',
    },
    {
      key: 'dist_ratio',
      label: 'Dist Ratio',
      fmt: 'dec',
      higherBetter: true,
      denomKey: 'gps_denom',
    },
    {
      key: 'minute_per_visit',
      label: 'Min/Visit',
      fmt: 'int',
      higherBetter: null,
      denomKey: 'duration_denom',
    },
  ];
  var MIN_DENOM = 3;
  var TAB2_METRIC_COLS = METRIC_COLS.filter(function (col) {
    return col.key !== 'pct_still_eligible';
  });

  // =========================================================================
  // State
  // =========================================================================
  // v5: read via `view.state` so completed runs render from the snapshot,
  // not whatever is currently in instance.state (which may be empty on a
  // completed run if the snapshot is the authoritative source). The view
  // helper transparently returns snapshot data when view.isCompleted.
  var _viewState = (view && view.state) || (instance && instance.state) || {};
  var savedResults = _viewState.worker_results || {};
  var savedTaskStates = _viewState.task_states || {};
  var prevMetrics = _viewState.previous_metrics || {};
  var savedSelectedWorkers = _viewState.selected_workers || [];
  var isCompleted = !!(view && view.isCompleted);

  var _step = React.useState(
    savedSelectedWorkers.length > 0 ? 'idle' : 'select',
  );
  var step = _step[0];
  var setStep = _step[1];
  var _dashData = React.useState(null);
  var dashData = _dashData[0];
  var setDashData = _dashData[1];
  var _jobMessages = React.useState([]);
  var jobMessages = _jobMessages[0];
  var setJobMessages = _jobMessages[1];
  var _jobError = React.useState(null);
  var jobError = _jobError[0];
  var setJobError = _jobError[1];
  var _activeTab = React.useState('audit');
  var activeTab = _activeTab[0];
  var setActiveTab = _activeTab[1];
  var _workerResults = React.useState(savedResults);
  var workerResults = _workerResults[0];
  var setWorkerResults = _workerResults[1];
  var _taskStates = React.useState(savedTaskStates);
  var taskStates = _taskStates[0];
  var setTaskStates = _taskStates[1];
  var _sortCol = React.useState('flags');
  var sortCol = _sortCol[0];
  var setSortCol = _sortCol[1];
  var _sortAsc = React.useState(false);
  var sortAsc = _sortAsc[0];
  var setSortAsc = _sortAsc[1];
  var _search = React.useState('');
  var search = _search[0];
  var setSearch = _search[1];
  var _filterFlag = React.useState('all');
  var filterFlag = _filterFlag[0];
  var setFilterFlag = _filterFlag[1];
  var _tab2FilterFlag = React.useState('all');
  var tab2FilterFlag = _tab2FilterFlag[0];
  var setTab2FilterFlag = _tab2FilterFlag[1];
  var _savingUser = React.useState(null);
  var savingUser = _savingUser[0];
  var setSavingUser = _savingUser[1];
  var _concludeModal = React.useState(false);
  var concludeModal = _concludeModal[0];
  var setConcludeModal = _concludeModal[1];
  var _concluding = React.useState(false);
  var concluding = _concluding[0];
  var setConcluding = _concluding[1];
  var _notesModal = React.useState(null);
  var notesModal = _notesModal[0];
  var setNotesModal = _notesModal[1];
  var _notesDraft = React.useState('');
  var notesDraft = _notesDraft[0];
  var setNotesDraft = _notesDraft[1];
  var _notesModalResult = React.useState(null);
  var notesModalResult = _notesModalResult[0];
  var setNotesModalResult = _notesModalResult[1];
  var _savingNotes = React.useState(false);
  var savingNotes = _savingNotes[0];
  var setSavingNotes = _savingNotes[1];
  var _tab2Step = React.useState('idle');
  var tab2Step = _tab2Step[0];
  var setTab2Step = _tab2Step[1];
  var _tab2Data = React.useState(null);
  var tab2Data = _tab2Data[0];
  var setTab2Data = _tab2Data[1];
  var _visibleCols = React.useState(
    METRIC_COLS.map(function (c) {
      return c.key;
    }),
  );
  var visibleCols = _visibleCols[0];
  var setVisibleCols = _visibleCols[1];
  var _showColPicker = React.useState(false);
  var showColPicker = _showColPicker[0];
  var setShowColPicker = _showColPicker[1];
  var _perfData = React.useState(null);
  var perfData = _perfData[0];
  var setPerfData = _perfData[1];

  // FLW selection step state
  var _selectedFlws = React.useState({});
  var selectedFlws = _selectedFlws[0];
  var setSelectedFlws = _selectedFlws[1];
  var _flwHistory = React.useState({});
  var flwHistory = _flwHistory[0];
  var setFlwHistory = _flwHistory[1];
  var _prevCatsForSelect = React.useState({});
  var prevCatsForSelect = _prevCatsForSelect[0];
  var setPrevCatsForSelect = _prevCatsForSelect[1];
  var _historyLoading = React.useState(false);
  var historyLoading = _historyLoading[0];
  var setHistoryLoading = _historyLoading[1];
  var _selSearch = React.useState('');
  var selSearch = _selSearch[0];
  var setSelSearch = _selSearch[1];
  var _selSort = React.useState({ col: 'name', dir: 'asc' });
  var selSort = _selSort[0];
  var setSelSort = _selSort[1];
  var _launching = React.useState(false);
  var launching = _launching[0];
  var setLaunching = _launching[1];

  var savedAuditStatuses = _viewState.audit_statuses || {};
  var _auditStatuses = React.useState(savedAuditStatuses);
  var auditStatuses = _auditStatuses[0];
  var setAuditStatuses = _auditStatuses[1];
  var _auditStatusModal = React.useState(null);
  var auditStatusModal = _auditStatusModal[0];
  var setAuditStatusModal = _auditStatusModal[1];
  var _auditStatusDraft = React.useState('');
  var auditStatusDraft = _auditStatusDraft[0];
  var setAuditStatusDraft = _auditStatusDraft[1];

  var _expandedTaskFlw = React.useState(null);
  var expandedTaskFlw = _expandedTaskFlw[0];
  var setExpandedTaskFlw = _expandedTaskFlw[1];
  var _taskDetail = React.useState(null);
  var taskDetail = _taskDetail[0];
  var setTaskDetail = _taskDetail[1];
  var _taskTranscript = React.useState(null);
  var taskTranscript = _taskTranscript[0];
  var setTaskTranscript = _taskTranscript[1];
  var _taskDetailLoading = React.useState(false);
  var taskDetailLoading = _taskDetailLoading[0];
  var setTaskDetailLoading = _taskDetailLoading[1];
  var _taskTranscriptError = React.useState(null);
  var taskTranscriptError = _taskTranscriptError[0];
  var setTaskTranscriptError = _taskTranscriptError[1];
  var _transcriptOcsRequired = React.useState(false);
  var transcriptOcsRequired = _transcriptOcsRequired[0];
  var setTranscriptOcsRequired = _transcriptOcsRequired[1];
  var _oauthStatus = React.useState(null);
  var oauthStatus = _oauthStatus[0];
  var setOauthStatus = _oauthStatus[1];

  var jobCleanupRef = React.useRef(null);
  var tab2CleanupRef = React.useRef(null);
  var taskDetailRequestIdRef = React.useRef(0);
  var saveQueueRef = React.useRef(Promise.resolve());
  // Holds selected usernames for the current run so runAnalysis can read them
  // even before onUpdateState resolves (instance.state not yet updated)
  var selectedForRunRef = React.useRef(
    savedSelectedWorkers.length > 0 ? savedSelectedWorkers : null,
  );

  // =========================================================================
  // Derived helpers
  // =========================================================================
  var flwNameMap = React.useMemo(
    function () {
      var m = {};
      (workers || []).forEach(function (w) {
        if (w.username) m[w.username.toLowerCase()] = w.name || w.username;
      });
      return m;
    },
    [workers],
  );

  var prevCategories = (dashData && dashData.prev_categories) || {};

  // =========================================================================
  // Helpers
  // =========================================================================
  var getCSRF = React.useCallback(function () {
    return (
      (document.querySelector('[name=csrfmiddlewaretoken]') || {}).value ||
      (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] ||
      ''
    );
  }, []);

  var toggleFlw = function (username) {
    setSelectedFlws(function (prev) {
      var next = Object.assign({}, prev);
      next[username] = !next[username];
      return next;
    });
  };

  var toggleAll = function () {
    var allSel =
      workers.length > 0 &&
      workers.every(function (w) {
        return selectedFlws[w.username];
      });
    var updated = {};
    workers.forEach(function (w) {
      updated[w.username] = !allSel;
    });
    setSelectedFlws(updated);
  };

  var handleLaunch = function () {
    if (isCompleted) return;
    var selected = Object.entries(selectedFlws)
      .filter(function (e) {
        return e[1];
      })
      .map(function (e) {
        return e[0];
      });
    if (selected.length === 0) return;
    setLaunching(true);
    selectedForRunRef.current = selected;
    onUpdateState({
      selected_workers: selected,
    })
      .then(function () {
        setLaunching(false);
        setStep('idle');
        runAnalysis();
      })
      .catch(function () {
        setLaunching(false);
      });
  };

  var daysAgo = function (dt) {
    if (!dt) return '—';
    var ms = Date.parse(dt);
    if (isNaN(ms)) return dt;
    var days = Math.floor((Date.now() - ms) / 86400000);
    if (days === 0) return 'today';
    if (days === 1) return '1d ago';
    return days + 'd ago';
  };

  var fmtVal = function (val, fmt) {
    if (val == null) return '—';
    if (fmt === 'pct') return val + '%';
    if (fmt === 'dec') return val.toFixed(1);
    if (fmt === 'int') return Math.round(val).toString();
    return String(val);
  };

  var getMetricValueColor = function (key, val) {
    if (val == null) return '';
    if (key === 'gs_score') return val < 50 ? 'text-red-600' : 'text-green-600';
    if (key === 'followup_rate') {
      if (val < 50) return 'text-red-600';
      if (val < 80) return 'text-yellow-600';
      return 'text-green-600';
    }
    if (key === 'pct_still_eligible') {
      if (val < 50) return 'text-red-600';
      if (val < 80) return 'text-yellow-600';
      return 'text-green-600';
    }
    if (key === 'ebf_pct') {
      if (val < 10 || val >= 99) return 'text-red-600';
      if (val <= 30 || val > 94) return 'text-yellow-600';
      return 'text-green-600';
    }
    if (key === 'revisit_dist') {
      if (val < 30) return 'text-green-600';
      if (val <= 50) return 'text-yellow-600';
      return 'text-red-600';
    }
    if (key === 'meter_per_visit') {
      if (val > 50) return 'text-green-600';
      if (val >= 20) return 'text-yellow-600';
      return 'text-red-600';
    }
    if (key === 'minute_per_visit') {
      if (val > 20) return 'text-green-600';
      if (val >= 10) return 'text-yellow-600';
      return 'text-red-600';
    }
    return '';
  };

  var getChangeDir = function (curr, prev, higherBetter) {
    if (curr == null || prev == null || higherBetter === null) return null;
    var diff = curr - prev;
    var threshold = Math.abs(prev) * 0.02;
    if (Math.abs(diff) <= threshold) return 'same';
    return (higherBetter ? diff > 0 : diff < 0) ? 'up' : 'down';
  };

  var ChangeIcon = function (props) {
    var dir = props.dir;
    if (!dir) return null;
    if (dir === 'up')
      return React.createElement(
        'span',
        { className: 'text-green-600 ml-1 text-xs', title: 'Improved' },
        '▲',
      );
    if (dir === 'same')
      return React.createElement(
        'span',
        {
          className: 'text-yellow-500 ml-1 text-xs',
          title: 'No significant change',
        },
        '≈',
      );
    return React.createElement(
      'span',
      { className: 'text-red-500 ml-1 text-xs', title: 'Worsened' },
      '▼',
    );
  };

  var resultBadge = function (result) {
    var str =
      typeof result === 'string' ? result : (result && result.result) || '';
    if (!str) return null;
    var styles = {
      eligible_for_renewal: 'bg-green-100 text-green-800',
      requires_improvement: 'bg-amber-100 text-amber-800',
      suspended: 'bg-red-100 text-red-800',
    };
    return React.createElement(
      'span',
      {
        className:
          'px-1.5 py-0.5 rounded text-xs font-medium whitespace-nowrap ' +
          (styles[str] || 'bg-gray-100 text-gray-700'),
      },
      str.replace(/_/g, ' '),
    );
  };

  // =========================================================================
  // Job runner
  // =========================================================================
  // v5: pure-JSX compute + REST fetches replace the v4 server-side job.
  // Keeps the same callable surface (runAnalysis() with no args) so every
  // downstream caller — handleLaunch, mount-effect, refresh button, etc. —
  // works unchanged.
  //
  // Reads pipeline rows from `view.pipelines.X` (snapshot when completed,
  // live otherwise). Fetches open_tasks + prev_categories from the existing
  // REST endpoints (same shape v4's job handler used internally). Computes
  // flw_summaries via v5_computeMbwAuditingData — line-for-line port of v4's
  // Python handler.
  var runAnalysis = React.useCallback(
    function () {
      if (step === 'running') return;
      if (isCompleted) {
        // Completed runs render directly from the snapshot — no recompute,
        // no network. Just populate dashData from snapshot pipeline rows.
        var snapPipelines = (view && view.pipelines) || {};
        var snapSel = (view && view.state && view.state.selected_workers) || [];
        var snapResult = v5_computeMbwAuditingData({
          visitsRows: (snapPipelines.visits && snapPipelines.visits.rows) || [],
          visitsAggRows:
            (snapPipelines.visits_agg && snapPipelines.visits_agg.rows) || [],
          regRows:
            (snapPipelines.registrations && snapPipelines.registrations.rows) ||
            [],
          gsRows: (snapPipelines.gs_forms && snapPipelines.gs_forms.rows) || [],
          activeUsernames: snapSel,
          flwNames: flwNameMap,
          taskFilters: null,
        });
        var snapWorkerMap = {};
        (workers || []).forEach(function (w) {
          snapWorkerMap[(w.username || '').toLowerCase()] = w;
        });
        var snapSummaries = snapResult.flw_summaries.map(function (s) {
          var w = snapWorkerMap[s.username] || {};
          return Object.assign({}, s, {
            last_active: w.last_active || s.last_active || '',
            display_name: s.display_name || w.name || s.username,
          });
        });
        setDashData({
          flw_summaries: snapSummaries,
          prev_categories:
            (view && view.state && view.state.previous_categories) || {},
        });
        setStep('ready');
        return;
      }

      setStep('running');
      setJobError(null);
      setJobMessages(['Computing analysis…']);
      setDashData(null);

      var selRef = selectedForRunRef.current;
      var allUsernames =
        selRef && selRef.length > 0
          ? selRef
          : (workers || []).map(function (w) {
              return w.username;
            });

      // Pipeline rows: prefer view.pipelines (snapshot-aware) and fall back
      // to props.pipelines (live data when view not yet wired in some legacy
      // contexts). For an in_progress run both resolve to the same data.
      var srcPipelines = (view && view.pipelines) || pipelines || {};
      var visitsRows = (srcPipelines.visits && srcPipelines.visits.rows) || [];
      var visitsAggRows =
        (srcPipelines.visits_agg && srcPipelines.visits_agg.rows) || [];
      var regRows =
        (srcPipelines.registrations && srcPipelines.registrations.rows) || [];
      var gsRows = (srcPipelines.gs_forms && srcPipelines.gs_forms.rows) || [];

      // If pipelines haven't loaded yet, bail out — the framework's
      // streamPipelineData effect will load them and re-render; the mount
      // effect below will re-trigger runAnalysis once they arrive.
      if (visitsRows.length === 0 && regRows.length === 0) {
        setStep('idle');
        setJobMessages([]);
        return;
      }

      var computeResult;
      try {
        computeResult = v5_computeMbwAuditingData({
          visitsRows: visitsRows,
          visitsAggRows: visitsAggRows,
          regRows: regRows,
          gsRows: gsRows,
          activeUsernames: allUsernames,
          flwNames: flwNameMap,
          taskFilters: null, // Tab 1 — no per-FLW trigger date
        });
      } catch (e) {
        setStep('error');
        setJobError((e && e.message) || 'Compute failed');
        return;
      }

      // Enrich with worker metadata (last_active, display_name fallback).
      var workerMap = {};
      (workers || []).forEach(function (w) {
        workerMap[(w.username || '').toLowerCase()] = w;
      });
      var enrichedSummaries = computeResult.flw_summaries.map(function (s) {
        var w = workerMap[s.username] || {};
        return Object.assign({}, s, {
          last_active: w.last_active || s.last_active || '',
          display_name: s.display_name || w.name || s.username,
        });
      });

      // Fetch open_tasks + prev_categories in parallel from existing labs
      // endpoints. These replace what v4's job handler did internally.
      var opportunityId = instance.opportunity_id;
      var openTasksUrl =
        '/labs/workflow/api/open-tasks/' +
        (opportunityId ? '?opportunity_id=' + opportunityId : '');
      var openTasksPromise = fetch(openTasksUrl, {
        credentials: 'same-origin',
      })
        .then(function (r) {
          return r.ok ? r.json() : { open_tasks: {} };
        })
        .catch(function (err) {
          console.warn('open_tasks fetch failed:', err);
          return { open_tasks: {} };
        });

      var prevCatPromise = fetch('/labs/workflow/api/prev-categories/', {
        credentials: 'same-origin',
      })
        .then(function (r) {
          return r.ok ? r.json() : { prev_categories: {} };
        })
        .catch(function (err) {
          console.warn('prev_categories fetch failed:', err);
          return { prev_categories: {} };
        });

      var openRunStatePromise = fetch('/labs/workflow/api/open-run-state/', {
        credentials: 'same-origin',
      })
        .then(function (r) {
          return r.ok ? r.json() : { worker_results: {}, audit_statuses: {} };
        })
        .catch(function (err) {
          console.warn('open_run_state fetch failed:', err);
          return { worker_results: {}, audit_statuses: {} };
        });

      Promise.all([openTasksPromise, prevCatPromise, openRunStatePromise]).then(function (vals) {
        var openTasksResp = vals[0] || {};
        var prevCatResp = vals[1] || {};
        var openRunStateResp = vals[2] || {};
        var fetchedTasks = openTasksResp.open_tasks || {};
        var prevCats = prevCatResp.prev_categories || {};
        var crossRunWorkerResults = openRunStateResp.worker_results || {};
        var crossRunAuditStatuses = openRunStateResp.audit_statuses || {};

        setDashData({
          flw_summaries: enrichedSummaries,
          prev_categories: prevCats,
        });

        if (Object.keys(crossRunWorkerResults).length > 0 && !isCompleted) {
          setWorkerResults(crossRunWorkerResults);
          onUpdateState({ worker_results: crossRunWorkerResults }).catch(function (e) {
            console.warn('worker_results cross-run seed failed:', e);
          });
        }
        if (Object.keys(crossRunAuditStatuses).length > 0 && !isCompleted) {
          setAuditStatuses(crossRunAuditStatuses);
          onUpdateState({ audit_statuses: crossRunAuditStatuses }).catch(function (e) {
            console.warn('audit_statuses cross-run seed failed:', e);
          });
        }

        if (Object.keys(fetchedTasks).length > 0) {
          setTaskStates(function (prev) {
            var merged = Object.assign({}, prev);
            Object.keys(fetchedTasks).forEach(function (u) {
              var t = fetchedTasks[u];
              // Don't clobber locally-set task state for tasks the user has
              // already interacted with this session.
              if (!merged[u] || !merged[u].triggered_at) {
                merged[u] = {
                  status: t.status,
                  triggered_at: t.triggered_at,
                  task_id: t.task_id,
                  title: t.title,
                };
              }
            });
            if (!isCompleted) {
              onUpdateState({ task_states: merged }).catch(function (e) {
                console.warn('task_states persist failed:', e);
              });
            }
            return merged;
          });
        }

        setStep('ready');
        if (!isCompleted) {
          onUpdateState({
            analysis_complete: true,
            analysis_ts: new Date().toISOString(),
          }).catch(function (e) {
            console.warn('state save failed:', e);
          });
        }
      });
    },
    [
      step,
      isCompleted,
      workers,
      flwNameMap,
      workerResults,
      instance.id,
      instance.opportunity_id,
      instance.definition_id,
      view,
      pipelines,
      onUpdateState,
    ],
  );

  // Fetch OCS auth status on mount so transcript panel can show auth link
  React.useEffect(function () {
    fetch(
      '/labs/workflow/api/auth-status/?next=' +
        encodeURIComponent(window.location.pathname),
      { credentials: 'same-origin' },
    )
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        setOauthStatus(data);
      })
      .catch(function () {});
  }, []);

  // Auto-run on mount only when reopening an existing run (saved workers present)
  React.useEffect(function () {
    var hasSaved =
      selectedForRunRef.current && selectedForRunRef.current.length > 0;
    if (!dashData && hasSaved) {
      runAnalysis();
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Fetch data for the FLW selection step (history + prev categories)
  React.useEffect(
    function () {
      if (!instance.opportunity_id) return;
      if (savedSelectedWorkers.length > 0) return;
      setHistoryLoading(true);

      var historyPromise = fetch(
        '/custom_analysis/mbw_monitoring/api/opportunity-flws/',
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCSRF(),
          },
          body: JSON.stringify({ opportunities: [instance.opportunity_id] }),
        },
      )
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          if (data.success) {
            var hm = {};
            (data.flws || []).forEach(function (f) {
              hm[f.username] = f.history || {};
            });
            setFlwHistory(hm);
          }
        })
        .catch(function (err) {
          console.error('Failed to fetch FLW history:', err);
        });

      var prevCatPromise = fetch('/labs/workflow/api/prev-categories/', {
        credentials: 'same-origin',
      })
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          if (data.prev_categories) {
            setPrevCatsForSelect(data.prev_categories);
          }
        })
        .catch(function (err) {
          console.error('Failed to fetch prev categories:', err);
        });

      Promise.all([historyPromise, prevCatPromise]).finally(function () {
        setHistoryLoading(false);
      });
    },
    [instance.opportunity_id], // eslint-disable-line react-hooks/exhaustive-deps
  );
  React.useEffect(function () {
    return function () {
      if (jobCleanupRef.current) jobCleanupRef.current();
    };
  }, []);
  React.useEffect(function () {
    return function () {
      if (tab2CleanupRef.current) tab2CleanupRef.current();
    };
  }, []);

  // =========================================================================
  // Tab 2 job runner
  // =========================================================================
  // v5: Tab 2 baseline-rate compute. Re-runs the same v5_computeMbwAuditingData
  // entry point with per-FLW task_filters (their trigger date) so the
  // follow-up rate is computed as-of that date. Matches v4 lines 401-458
  // exactly via the helper.
  var runTab2Analysis = React.useCallback(
    function () {
      if (!dashData || tab2Step === 'running') return;
      setTab2Step('running');

      var flaggedWithTask = enrichedData.filter(function (f) {
        return (
          f.hasTask &&
          taskStates[f.username] &&
          taskStates[f.username].triggered_at
        );
      });

      if (flaggedWithTask.length === 0) {
        setTab2Step('idle');
        return;
      }

      var flaggedUsernames = flaggedWithTask.map(function (f) {
        return f.username;
      });
      var taskFilters = {};
      flaggedWithTask.forEach(function (f) {
        taskFilters[f.username] = taskStates[f.username].triggered_at;
      });

      var srcPipelines2 = (view && view.pipelines) || pipelines || {};
      var visitsRows2 =
        (srcPipelines2.visits && srcPipelines2.visits.rows) || [];
      var regRows2 =
        (srcPipelines2.registrations && srcPipelines2.registrations.rows) || [];
      var gsRows2 =
        (srcPipelines2.gs_forms && srcPipelines2.gs_forms.rows) || [];

      var tab2Result;
      try {
        tab2Result = v5_computeMbwAuditingData({
          visitsRows: visitsRows2,
          // visits_agg deliberately omitted for Tab 2 — task_filters are set,
          // so the JS handler falls back to per-visit scanning (matches v4's
          // `use_agg_counts = bool(visits_agg_rows) and not task_filters`).
          visitsAggRows: [],
          regRows: regRows2,
          gsRows: gsRows2,
          activeUsernames: flaggedUsernames,
          flwNames: flwNameMap,
          taskFilters: taskFilters,
        });
      } catch (e) {
        console.error('Tab 2 compute failed:', e);
        setTab2Step('error');
        return;
      }

      var byUser = {};
      (tab2Result.flw_summaries || []).forEach(function (s) {
        byUser[s.username] = s;
      });
      setTab2Data(byUser);
      setTab2Step('ready');
    },
    [
      dashData,
      enrichedData,
      taskStates,
      flwNameMap,
      workerResults,
      instance.id,
      instance.opportunity_id,
      instance.definition_id,
      view,
      pipelines,
      tab2Step,
    ],
  );

  // =========================================================================
  // Flag computation
  // =========================================================================
  var computeFlags = function (flw) {
    var reasons = [];
    var type = null;

    if (flw.gs_score != null && flw.gs_score < THRESHOLDS.gs_red) {
      reasons.push('GS Score: ' + flw.gs_score + '% (below 50%)');
      type = 'red';
    }
    if (flw.followup_rate != null && flw.followup_rate < THRESHOLDS.fu_red) {
      reasons.push('Follow-up Rate: ' + flw.followup_rate + '% (below 50%)');
      type = 'red';
    } else if (
      flw.followup_rate != null &&
      flw.followup_rate < THRESHOLDS.fu_yellow
    ) {
      reasons.push('Follow-up Rate: ' + flw.followup_rate + '% (50–79%)');
      if (!type) type = 'yellow';
    }
    if (
      flw.pct_still_eligible != null &&
      flw.pct_still_eligible < THRESHOLDS.pct_still_elig_red
    ) {
      reasons.push(
        '% Still Eligible: ' + flw.pct_still_eligible + '% (below 50%)',
      );
      if (type !== 'red') type = 'red';
    } else if (
      flw.pct_still_eligible != null &&
      flw.pct_still_eligible < THRESHOLDS.pct_still_elig_yellow
    ) {
      reasons.push('% Still Eligible: ' + flw.pct_still_eligible + '% (below 80%)');
      if (!type) type = 'yellow';
    }
    if (
      flw.ebf_pct != null &&
      (flw.ebf_pct <= THRESHOLDS.ebf_low || flw.ebf_pct > THRESHOLDS.ebf_high)
    ) {
      reasons.push('EBF: ' + flw.ebf_pct + '%');
      if (!type) type = 'yellow';
    }
    if (flw.dist_ratio != null && flw.dist_ratio < THRESHOLDS.dist_ratio_low) {
      reasons.push('GPS Clustering (Dist Ratio: ' + flw.dist_ratio + ')');
      if (!type) type = 'yellow';
    }
    var prev = prevMetrics[flw.username];
    if (prev) {
      METRIC_COLS.forEach(function (col) {
        var curr = flw[col.key];
        var prevVal = prev[col.key];
        if (
          curr == null ||
          prevVal == null ||
          prevVal === 0 ||
          col.higherBetter === null
        )
          return;
        var worsened = col.higherBetter ? curr < prevVal : curr > prevVal;
        if (!worsened) return;
        var changePct = (Math.abs(curr - prevVal) / Math.abs(prevVal)) * 100;
        if (changePct > THRESHOLDS.worsened_pct) {
          reasons.push(
            col.label + ' worsened (' + Math.round(changePct) + '%)',
          );
          if (!type) type = 'yellow';
        }
      });
    }
    return { type: type, reasons: reasons };
  };

  var toggleCol = function (key) {
    setVisibleCols(function (prev) {
      return prev.indexOf(key) >= 0
        ? prev.filter(function (c) {
            return c !== key;
          })
        : prev.concat([key]);
    });
  };

  // Filtered column lists respecting per-session visibility toggles
  var effectiveMetricCols = METRIC_COLS.filter(function (col) {
    return visibleCols.indexOf(col.key) >= 0;
  });
  var effectiveTab2MetricCols = TAB2_METRIC_COLS.filter(function (col) {
    return visibleCols.indexOf(col.key) >= 0;
  });

  // =========================================================================
  // Enriched data
  // =========================================================================
  var enrichedData = React.useMemo(
    function () {
      if (!dashData) return [];
      return dashData.flw_summaries.map(function (flw) {
        var flags = computeFlags(flw);
        var wr = workerResults[flw.username] || {};
        var ts = taskStates[flw.username] || {};
        return Object.assign({}, flw, {
          flags: flags,
          result: wr.result || null,
          notes: wr.notes || '',
          hasTask: !!ts.triggered_at,
          taskStatus: ts.status || null,
          taskTriggeredAt: ts.triggered_at || null,
          taskId: ts.task_id || null,
        });
      });
    },
    [dashData, workerResults, taskStates],
  );

  var filteredData = React.useMemo(
    function () {
      var data = enrichedData;
      if (search.trim()) {
        var q = search.toLowerCase();
        data = data.filter(function (f) {
          return (
            (f.display_name && f.display_name.toLowerCase().indexOf(q) >= 0) ||
            (f.username && f.username.toLowerCase().indexOf(q) >= 0)
          );
        });
      }
      if (filterFlag === 'red')
        data = data.filter(function (f) {
          return f.flags.type === 'red';
        });
      else if (filterFlag === 'flagged')
        data = data.filter(function (f) {
          return f.flags.type !== null;
        });
      else if (filterFlag === 'tasks')
        data = data.filter(function (f) {
          return f.hasTask;
        });

      data = data.slice().sort(function (a, b) {
        if (sortCol === 'name') {
          var va = a.display_name || '';
          var vb = b.display_name || '';
          return sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
        }
        if (sortCol === 'flags') {
          var order = { red: 2, yellow: 1 };
          var va = order[a.flags.type] || 0;
          var vb = order[b.flags.type] || 0;
          return sortAsc ? va - vb : vb - va;
        }
        var va = a[sortCol] != null ? a[sortCol] : -Infinity;
        var vb = b[sortCol] != null ? b[sortCol] : -Infinity;
        return sortAsc ? va - vb : vb - va;
      });
      return data;
    },
    [enrichedData, search, filterFlag, sortCol, sortAsc],
  );

  var tab2FlaggedRows = React.useMemo(
    function () {
      return enrichedData.filter(function (f) {
        return (
          f.hasTask && f.taskStatus !== 'closed' && f.taskStatus !== 'completed'
        );
      });
    },
    [enrichedData],
  );

  var tab2FilteredRows = React.useMemo(
    function () {
      var data = tab2FlaggedRows;
      if (tab2FilterFlag === 'red')
        data = data.filter(function (f) {
          return f.flags.type === 'red';
        });
      else if (tab2FilterFlag === 'flagged')
        data = data.filter(function (f) {
          return f.flags.type !== null;
        });
      else if (tab2FilterFlag === 'tasks')
        data = data.filter(function (f) {
          return f.hasTask;
        });
      return data;
    },
    [tab2FlaggedRows, tab2FilterFlag],
  );

  // =========================================================================
  // Performance band summary (Tab 3)
  // =========================================================================
  var computePerfBands = function () {
    var bands = [
      {
        id: 'eligible_for_renewal',
        label: 'Eligible for Renewal',
        color: 'green',
      },
      {
        id: 'requires_improvement',
        label: 'Requires Improvement',
        color: 'yellow',
      },
      { id: 'suspended', label: 'Suspension', color: 'red' },
      { id: null, label: 'Uncategorized', color: 'gray' },
    ];
    return bands.map(function (band) {
      var catFlws = enrichedData.filter(function (f) {
        return f.result === band.id;
      });
      var fuFlws = catFlws.filter(function (f) {
        return f.followup_rate != null;
      });
      var avgFu =
        fuFlws.length > 0
          ? Math.round(
              fuFlws.reduce(function (s, f) {
                return s + f.followup_rate;
              }, 0) / fuFlws.length,
            )
          : null;
      var gsFlws = catFlws.filter(function (f) {
        return f.gs_score != null;
      });
      var avgGs =
        gsFlws.length > 0
          ? Math.round(
              gsFlws.reduce(function (s, f) {
                return s + f.gs_score;
              }, 0) / gsFlws.length,
            )
          : null;
      var totalElig = catFlws.reduce(function (s, f) {
        return s + (f.num_mothers_eligible || 0);
      }, 0);
      var totalStillElig = catFlws.reduce(function (s, f) {
        if (f.pct_still_eligible == null || !f.num_mothers_eligible) return s;
        return (
          s + Math.round((f.pct_still_eligible / 100) * f.num_mothers_eligible)
        );
      }, 0);
      var pctStillElig =
        totalElig > 0 ? Math.round((totalStillElig / totalElig) * 100) : null;
      return Object.assign({}, band, {
        num_flws: catFlws.length,
        total_mothers: catFlws.reduce(function (s, f) {
          return s + (f.num_mothers || 0);
        }, 0),
        total_eligible: totalElig,
        total_still_eligible: totalStillElig,
        pct_still_eligible: pctStillElig,
        avg_fu: avgFu,
        avg_gs: avgGs,
      });
    });
  };

  // =========================================================================
  // Handlers
  // =========================================================================
  var handleSort = function (col) {
    if (sortCol === col) setSortAsc(!sortAsc);
    else {
      setSortCol(col);
      setSortAsc(col === 'name');
    }
  };

  var handleSetCategory = function (username, category) {
    // Toggle: clicking the active category clears it
    var current = (workerResults[username] || {}).result;
    var newCategory = current === category ? null : category;
    var wr = workerResults[username] || {};
    setSavingUser(username);
    saveQueueRef.current = saveQueueRef.current
      .catch(function () {})
      .then(function () {
        return actions
          .saveWorkerResult(instance.id, {
            username: username,
            result: newCategory,
            notes: wr.notes || '',
          })
          .then(function (resp) {
            if (resp.success) {
              var updated = Object.assign({}, workerResults);
              updated[username] = Object.assign({}, wr, { result: newCategory });
              setWorkerResults(resp.worker_results || updated);
            } else {
              alert('Failed to save: ' + (resp.error || 'unknown error'));
            }
          })
          .catch(function (e) {
            alert('Error: ' + ((e && e.message) || e));
          })
          .finally(function () {
            setSavingUser(null);
          });
      });
  };

  var handleOpenNotes = function (flw) {
    var wr = workerResults[flw.username] || {};
    setNotesModal(flw.username);
    setNotesDraft(flw.notes || '');
    setNotesModalResult(wr.result || null);
  };

  var handleSaveNotes = function () {
    if (!notesModal || isCompleted) return;
    setSavingNotes(true);
    var username = notesModal;
    var wr = workerResults[username] || {};
    saveQueueRef.current = saveQueueRef.current
      .catch(function () {})
      .then(function () {
        return actions
          .saveWorkerResult(instance.id, {
            username: username,
            result: notesModalResult,
            notes: notesDraft,
          })
          .then(function (resp) {
            if (resp.success) {
              var updated = Object.assign({}, workerResults);
              updated[username] = Object.assign({}, wr, {
                result: notesModalResult,
                notes: notesDraft,
              });
              setWorkerResults(resp.worker_results || updated);
              setNotesModal(null);
            } else {
              alert('Failed to save notes: ' + (resp.error || 'unknown error'));
            }
          })
          .catch(function (e) {
            alert('Error: ' + ((e && e.message) || e));
          })
          .finally(function () {
            setSavingNotes(false);
          });
      });
  };

  var handleTriggerTask = function (flw) {
    if (isCompleted) return;
    var flagDesc =
      flw.flags.reasons.join('; ') || 'Performance review required';
    actions.openTaskCreator({
      username: flw.username,
      title: 'MBW Audit: ' + flw.display_name,
      description: flagDesc,
      priority: flw.flags.type === 'red' ? 'high' : 'medium',
      workflow_instance_id: instance.id,
    });
    var updated = Object.assign({}, taskStates);
    updated[flw.username] = {
      status: 'open',
      triggered_at: new Date().toISOString(),
    };
    setTaskStates(updated);
    onUpdateState({ task_states: updated }).catch(function (e) {
      console.warn('task state save failed:', e);
    });
  };

  var handleMarkTaskResolved = function (username) {
    if (isCompleted) return;
    var updated = Object.assign({}, taskStates);
    updated[username] = Object.assign({}, updated[username], {
      status: 'closed',
    });
    setTaskStates(updated);
    setExpandedTaskFlw(null);
    setTaskDetail(null);
    setTaskTranscript(null);
    onUpdateState({ task_states: updated }).catch(function (e) {
      console.warn('task state save failed:', e);
    });
  };

  var toggleTaskExpand = function (username) {
    if (expandedTaskFlw === username) {
      taskDetailRequestIdRef.current++;
      setExpandedTaskFlw(null);
      setTaskDetail(null);
      setTaskTranscript(null);
      setTaskTranscriptError(null);
      setTranscriptOcsRequired(false);
      return;
    }
    var ts = taskStates[username] || {};
    var tid = ts.task_id;
    if (!tid) return;
    var requestId = ++taskDetailRequestIdRef.current;
    setExpandedTaskFlw(username);
    setTaskDetailLoading(true);
    setTaskDetail(null);
    setTaskTranscript(null);
    setTaskTranscriptError(null);
    setTranscriptOcsRequired(false);
    if (!actions || !actions.getTaskDetail) {
      setTaskDetailLoading(false);
      return;
    }
    actions
      .getTaskDetail(tid)
      .then(function (result) {
        if (requestId !== taskDetailRequestIdRef.current) return null;
        if (result && result.success && result.task) {
          setTaskDetail(result.task);
          // getAISessions links the OCS session_id to the task before we fetch
          // the transcript — without this step task_ai_transcript returns 404
          return actions.getAISessions(tid);
        }
        setTaskDetailLoading(false);
        return null;
      })
      .then(function (sessionsResult) {
        if (sessionsResult === null) return null;
        if (requestId !== taskDetailRequestIdRef.current) return null;
        return actions.getAITranscript(tid);
      })
      .then(function (transcriptResult) {
        if (requestId !== taskDetailRequestIdRef.current) return;
        setTaskDetailLoading(false);
        if (transcriptResult && transcriptResult.success) {
          setTaskTranscript(transcriptResult.messages || []);
        } else if (transcriptResult) {
          setTranscriptOcsRequired(!!transcriptResult.ocs_auth_required);
          setTaskTranscriptError(
            transcriptResult.ocs_auth_required
              ? null
              : transcriptResult.error || 'Transcript unavailable',
          );
          setTaskTranscript([]);
        }
      })
      .catch(function (err) {
        if (requestId !== taskDetailRequestIdRef.current) return;
        setTaskDetailLoading(false);
        setTaskTranscriptError(
          (err && err.message) || 'Failed to load transcript',
        );
      });
  };

  var handleSetAuditStatus = function (username, status, reason) {
    if (isCompleted) return;
    var updated = Object.assign({}, auditStatuses);
    updated[username] = {
      status: status,
      reason: reason || '',
      set_at: new Date().toISOString(),
    };
    setAuditStatuses(updated);
    onUpdateState({ audit_statuses: updated }).catch(function (e) {
      console.warn('audit status save failed:', e);
    });
  };

  var handleClearAuditStatus = function (username) {
    if (isCompleted) return;
    var updated = Object.assign({}, auditStatuses);
    delete updated[username];
    setAuditStatuses(updated);
    onUpdateState({ audit_statuses: updated }).catch(function (e) {
      console.warn('audit status save failed:', e);
    });
  };

  var handleSaveAuditNotRequired = function () {
    if (!auditStatusModal) return;
    if (!auditStatusDraft.trim()) {
      alert('Please enter a reason why no audit is required.');
      return;
    }
    handleSetAuditStatus(
      auditStatusModal,
      'audit_not_required',
      auditStatusDraft.trim(),
    );
    setAuditStatusModal(null);
    setAuditStatusDraft('');
  };

  var handleConclude = function () {
    if (concluding || isCompleted) return;
    setConcluding(true);
    var currentMetrics = {};
    enrichedData.forEach(function (f) {
      var snap = {};
      METRIC_COLS.forEach(function (col) {
        snap[col.key] = f[col.key];
      });
      currentMetrics[f.username] = snap;
    });
    // Save previous_metrics and previous_categories BEFORE completing —
    // the run becomes immutable once view.complete() flips status. The
    // snapshot built server-side captures both keys per SNAPSHOT_INPUTS.
    onUpdateState({
      previous_metrics: currentMetrics,
      previous_categories: workerResults,
    })
      .then(function () {
        // view.complete() handles the confirm dialog (skipped — we already
        // surface the conclude modal), atomically builds the snapshot,
        // flips status, and reloads the page on success. On error it
        // surfaces a window.alert and returns false; the run stays
        // in_progress so the user can retry.
        return view && view.complete
          ? view.complete({})
          : Promise.reject(
              new Error(
                'view.complete unavailable — workflow runner version mismatch?',
              ),
            );
      })
      .then(function (ok) {
        if (ok) {
          setConcludeModal(false);
          // view.complete reloads on success; no further FE work needed.
        } else {
          // view.complete already surfaced an alert; nothing more to do.
        }
      })
      .catch(function (e) {
        alert('Error: ' + ((e && e.message) || e));
      })
      .finally(function () {
        setConcluding(false);
      });
  };

  var canConclude = React.useMemo(
    function () {
      if (
        !Object.values(taskStates).every(function (t) {
          return (
            !t.triggered_at || t.status === 'closed' || t.status === 'completed'
          );
        })
      )
        return false;
      if (enrichedData.length > 0) {
        if (
          !enrichedData
            .filter(function (f) {
              return f.flags.type === 'red';
            })
            .every(function (f) {
              return (
                taskStates[f.username] && taskStates[f.username].triggered_at
              );
            })
        )
          return false;
        if (
          !enrichedData
            .filter(function (f) {
              return f.flags.type === 'yellow';
            })
            .every(function (f) {
              var as = auditStatuses[f.username] || {};
              if (!as.status) return false;
              if (as.status === 'audit_not_required') return !!as.reason;
              if (as.status === 'audit_required')
                return !!(
                  taskStates[f.username] && taskStates[f.username].triggered_at
                );
              return false;
            })
        )
          return false;
      }
      return true;
    },
    [taskStates, enrichedData, auditStatuses],
  );

  // =========================================================================
  // Sub-components
  // =========================================================================
  var SortTh = function (props) {
    var active = sortCol === props.col;
    return React.createElement(
      'th',
      {
        className:
          'px-3 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-100 whitespace-nowrap select-none',
        onClick: function () {
          handleSort(props.col);
        },
        title: props.title || '',
      },
      props.label,
      active ? (sortAsc ? ' ▲' : ' ▼') : ' ⇅',
    );
  };

  var FlagBadge = function (props) {
    var flags = props.flags;
    if (!flags.type)
      return React.createElement(
        'span',
        { className: 'text-gray-300 text-xs' },
        '—',
      );
    var isRed = flags.type === 'red';
    return React.createElement(
      'span',
      {
        className:
          'inline-flex items-center justify-center w-6 h-6 rounded-full text-white text-xs font-bold cursor-help ' +
          (isRed ? 'bg-red-500' : 'bg-yellow-400'),
        title: flags.reasons.join('\n'),
      },
      isRed ? '!' : '?',
    );
  };

  // Button-based category toggle (mirrors mbw_monitoring_v2 assessment buttons)
  var CategoryButtons = function (props) {
    var flw = props.flw;
    var saving = savingUser === flw.username;
    if (saving)
      return React.createElement(
        'span',
        { className: 'text-xs text-gray-400 italic' },
        'Saving…',
      );
    return React.createElement(
      'div',
      { className: 'inline-flex items-center gap-1' },
      PERF_CATEGORIES.map(function (cat) {
        var active = flw.result === cat.id;
        return React.createElement(
          'button',
          {
            key: cat.id,
            onClick: function () {
              handleSetCategory(flw.username, cat.id);
            },
            className:
              'px-2 py-1 rounded text-xs font-medium border transition-colors ' +
              (active ? cat.active : cat.inactive),
            title: cat.label,
          },
          React.createElement('i', { className: 'fa-solid ' + cat.icon }),
        );
      }),
    );
  };

  var TaskCell = function (props) {
    var flw = props.flw;
    if (flw.hasTask) {
      var isClosed =
        flw.taskStatus === 'closed' || flw.taskStatus === 'completed';
      if (isClosed) {
        return React.createElement(
          'span',
          {
            className:
              'inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded bg-green-100 text-green-700',
          },
          React.createElement('i', { className: 'fa-solid fa-circle-check' }),
          flw.taskStatus,
        );
      }
      var isExpanded = expandedTaskFlw === flw.username;
      return React.createElement(
        'button',
        {
          className:
            'inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded ' +
            (isExpanded
              ? 'bg-blue-200 text-blue-800'
              : 'bg-blue-100 text-blue-700 hover:bg-blue-200'),
          onClick: function () {
            toggleTaskExpand(flw.username);
          },
          title: 'View task details and chat history',
        },
        React.createElement('i', { className: 'fa-solid fa-clock' }),
        flw.taskStatus || 'investigating',
      );
    }
    return React.createElement(
      'button',
      {
        className:
          'text-xs px-2 py-0.5 rounded bg-orange-100 text-orange-700 hover:bg-orange-200 border border-orange-200',
        onClick: function () {
          handleTriggerTask(flw);
        },
        title: 'Open task creator for this FLW',
      },
      React.createElement('i', { className: 'fa-solid fa-plus mr-1' }),
      'Task',
    );
  };

  var TaskDetailPanel = function (props) {
    var username = props.username;
    var colCount = props.colCount || 16;
    return React.createElement(
      'tr',
      { key: username + '-task-detail' },
      React.createElement(
        'td',
        {
          colSpan: colCount,
          className: 'px-0 py-0 bg-blue-50',
        },
        React.createElement(
          'div',
          {
            className:
              'border-t border-b border-blue-200 bg-white mx-4 my-2 rounded-lg shadow-sm overflow-hidden',
          },
          taskDetailLoading && !taskDetail
            ? React.createElement(
                'div',
                { className: 'p-6 text-center text-gray-500' },
                React.createElement('i', {
                  className: 'fa-solid fa-spinner fa-spin mr-2',
                }),
                'Loading task...',
              )
            : taskDetail
            ? React.createElement(
                'div',
                null,
                React.createElement(
                  'div',
                  {
                    className:
                      'px-4 py-3 bg-blue-50 border-b border-blue-100 flex items-center justify-between',
                  },
                  React.createElement(
                    'div',
                    { className: 'flex items-center gap-2' },
                    React.createElement('i', {
                      className: 'fa-solid fa-clipboard-list text-blue-600',
                    }),
                    React.createElement(
                      'span',
                      { className: 'font-medium text-sm text-blue-900' },
                      taskDetail.title,
                    ),
                    React.createElement(
                      'span',
                      {
                        className:
                          'px-2 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-700',
                      },
                      taskDetail.status || 'investigating',
                    ),
                  ),
                  React.createElement(
                    'button',
                    {
                      className: 'text-gray-400 hover:text-gray-600 text-sm',
                      onClick: function () {
                        toggleTaskExpand(username);
                      },
                    },
                    React.createElement('i', {
                      className: 'fa-solid fa-xmark',
                    }),
                  ),
                ),
                React.createElement(
                  'div',
                  { className: 'flex' },
                  React.createElement(
                    'div',
                    {
                      className: 'flex-1 min-w-0 border-r border-gray-100',
                    },
                    React.createElement(
                      'div',
                      {
                        className:
                          'px-4 py-2 bg-gray-50 border-b border-gray-100 flex items-center',
                      },
                      React.createElement(
                        'span',
                        { className: 'text-xs font-medium text-gray-600' },
                        React.createElement('i', {
                          className: 'fa-solid fa-comments mr-1',
                        }),
                        'AI Conversation',
                      ),
                    ),
                    React.createElement(
                      'div',
                      {
                        className: 'p-3 overflow-y-auto space-y-2',
                        style: { minHeight: '120px', maxHeight: '400px' },
                      },
                      taskTranscript && taskTranscript.length > 0
                        ? taskTranscript.map(function (msg, idx) {
                            var isAssistant = msg.role === 'assistant';
                            return React.createElement(
                              'div',
                              {
                                key: idx,
                                className:
                                  'flex ' +
                                  (isAssistant
                                    ? 'justify-start'
                                    : 'justify-end'),
                              },
                              React.createElement(
                                'div',
                                {
                                  className:
                                    'rounded-lg px-3 py-2 text-sm ' +
                                    (isAssistant
                                      ? 'bg-gray-100 text-gray-800'
                                      : 'bg-blue-500 text-white'),
                                  style: { maxWidth: '85%' },
                                },
                                React.createElement(
                                  'div',
                                  {
                                    className:
                                      'whitespace-pre-wrap break-words',
                                  },
                                  msg.content,
                                ),
                                msg.created_at &&
                                  React.createElement(
                                    'div',
                                    {
                                      className:
                                        'text-xs mt-1 ' +
                                        (isAssistant
                                          ? 'text-gray-400'
                                          : 'text-blue-200'),
                                    },
                                    new Date(msg.created_at).toLocaleString(),
                                  ),
                              ),
                            );
                          })
                        : taskTranscript && taskTranscript.length === 0
                        ? transcriptOcsRequired ||
                          (oauthStatus && !oauthStatus.ocs?.active)
                          ? React.createElement(
                              'div',
                              { className: 'text-center py-4' },
                              React.createElement(
                                'div',
                                { className: 'text-amber-600 text-sm mb-2' },
                                React.createElement('i', {
                                  className: 'fa-solid fa-link-slash mr-1',
                                }),
                                ' OCS authorization required to load AI conversation',
                              ),
                              oauthStatus &&
                              oauthStatus.ocs &&
                              oauthStatus.ocs.authorize_url
                                ? React.createElement(
                                    'a',
                                    {
                                      href: oauthStatus.ocs.authorize_url,
                                      className:
                                        'inline-block px-3 py-1.5 bg-blue-600 text-white rounded text-sm hover:bg-blue-700 no-underline',
                                    },
                                    React.createElement('i', {
                                      className:
                                        'fa-solid fa-arrow-right-to-bracket mr-1',
                                    }),
                                    ' Connect to OCS',
                                  )
                                : null,
                            )
                          : React.createElement(
                              'div',
                              {
                                className:
                                  'text-center text-sm py-4 ' +
                                  (taskTranscriptError
                                    ? 'text-red-500'
                                    : 'text-gray-400'),
                              },
                              React.createElement('i', {
                                className:
                                  'fa-solid ' +
                                  (taskTranscriptError
                                    ? 'fa-circle-exclamation'
                                    : 'fa-comment-slash') +
                                  ' mr-1',
                              }),
                              taskTranscriptError || 'No messages yet',
                            )
                        : !taskDetailLoading
                        ? React.createElement(
                            'div',
                            {
                              className:
                                'text-center text-gray-400 text-sm py-4',
                            },
                            'Loading conversation...',
                          )
                        : null,
                    ),
                  ),
                  React.createElement(
                    'div',
                    {
                      className: 'w-56 p-4 space-y-3 bg-gray-50 flex-shrink-0',
                    },
                    React.createElement(
                      'button',
                      {
                        className:
                          'w-full px-3 py-2 rounded text-sm font-medium bg-green-600 text-white hover:bg-green-700',
                        onClick: function () {
                          handleMarkTaskResolved(username);
                        },
                      },
                      React.createElement('i', {
                        className: 'fa-solid fa-circle-check mr-1',
                      }),
                      'Resolve task',
                    ),
                  ),
                ),
              )
            : null,
        ),
      ),
    );
  };

  var AuditStatusCell = function (props) {
    var flw = props.flw;
    if (!flw.flags.type) {
      return React.createElement(
        'span',
        { className: 'text-gray-200 text-xs' },
        '—',
      );
    }
    if (flw.flags.type === 'red') {
      return React.createElement(
        'span',
        {
          className:
            'inline-block text-xs px-2 py-0.5 rounded bg-red-50 text-red-700 border border-red-200 font-medium whitespace-nowrap',
        },
        'Audit Required',
      );
    }
    // Yellow flag
    var as = auditStatuses[flw.username] || {};
    var status = as.status;
    var hasTask = !!(
      taskStates[flw.username] && taskStates[flw.username].triggered_at
    );
    if (!status) {
      return React.createElement(
        'div',
        { className: 'flex flex-col gap-1 items-start' },
        React.createElement(
          'button',
          {
            className:
              'text-xs px-2 py-0.5 rounded bg-amber-50 text-amber-700 hover:bg-amber-100 border border-amber-200 whitespace-nowrap',
            onClick: function () {
              handleSetAuditStatus(flw.username, 'audit_required');
            },
          },
          'Audit Required',
        ),
        React.createElement(
          'button',
          {
            className:
              'text-xs px-2 py-0.5 rounded bg-gray-50 text-gray-600 hover:bg-gray-100 border border-gray-200 whitespace-nowrap',
            onClick: function () {
              setAuditStatusModal(flw.username);
              setAuditStatusDraft('');
            },
          },
          'Not Required',
        ),
      );
    }
    if (status === 'audit_required') {
      return React.createElement(
        'div',
        { className: 'flex flex-col gap-1 items-start' },
        React.createElement(
          'span',
          {
            className:
              'text-xs px-2 py-0.5 rounded bg-amber-100 text-amber-800 border border-amber-300 font-medium whitespace-nowrap',
          },
          '✓ Audit Required',
        ),
        !hasTask &&
          React.createElement(
            'span',
            { className: 'text-xs text-red-500 whitespace-nowrap' },
            'Task required →',
          ),
        React.createElement(
          'button',
          {
            className: 'text-xs text-gray-400 hover:text-gray-600',
            onClick: function () {
              handleClearAuditStatus(flw.username);
            },
          },
          'Change',
        ),
      );
    }
    if (status === 'audit_not_required') {
      return React.createElement(
        'div',
        { className: 'flex flex-col gap-1 items-start' },
        React.createElement(
          'button',
          {
            className:
              'text-xs px-2 py-0.5 rounded bg-gray-100 text-gray-600 border border-gray-300 font-medium whitespace-nowrap',
            title: 'Reason: ' + as.reason,
            onClick: function () {
              setAuditStatusModal(flw.username);
              setAuditStatusDraft(as.reason || '');
            },
          },
          '✓ Not Required',
        ),
        React.createElement(
          'button',
          {
            className: 'text-xs text-gray-400 hover:text-gray-600',
            onClick: function () {
              handleClearAuditStatus(flw.username);
            },
          },
          'Change',
        ),
      );
    }
    return null;
  };

  // =========================================================================
  // Metric table row (Tab 1 and Tab 2)
  // =========================================================================
  var MetricRow = function (props) {
    var flw = props.flw;
    var showChange = props.showChange;
    var prev =
      props.prevOverride !== undefined
        ? props.prevOverride
        : prevMetrics[flw.username] || null;
    var prevCat = prevCategories[flw.username] || null;

    var followupRateAtTrigger =
      props.followupRateAtTrigger !== undefined
        ? props.followupRateAtTrigger
        : null;
    var rowCols =
      props.metricCols !== undefined ? props.metricCols : METRIC_COLS;

    var cells = rowCols.map(function (col) {
      var val = flw[col.key];
      // pct_still_eligible: hide when fewer than 10 eligible mothers
      if (
        col.key === 'pct_still_eligible' &&
        flw.num_mothers_eligible != null &&
        flw.num_mothers_eligible < 10
      ) {
        return React.createElement(
          'td',
          {
            key: col.key,
            className: 'px-3 py-2 text-sm text-center whitespace-nowrap',
          },
          React.createElement(
            'span',
            { className: 'text-gray-400 text-xs italic' },
            'not enough data',
          ),
        );
      }
      var denom = col.denomKey != null ? flw[col.denomKey] : null;
      if (denom != null && denom < MIN_DENOM) {
        return React.createElement(
          'td',
          {
            key: col.key,
            className: 'px-3 py-2 text-sm text-center whitespace-nowrap',
          },
          React.createElement(
            'span',
            { className: 'text-gray-400 text-xs italic' },
            'not enough data',
          ),
        );
      }
      var dir =
        showChange && prev
          ? getChangeDir(val, prev[col.key], col.higherBetter)
          : null;
      var valColor = getMetricValueColor(col.key, val);
      var deltaEl = null;
      if (col.key === 'followup_rate' && followupRateAtTrigger != null) {
        var fuDir =
          val > followupRateAtTrigger
            ? 'up'
            : val < followupRateAtTrigger
            ? 'down'
            : 'same';
        var arrowChar = fuDir === 'up' ? '▲' : fuDir === 'down' ? '▼' : '≈';
        var arrowColor =
          fuDir === 'up'
            ? 'text-green-600'
            : fuDir === 'down'
            ? 'text-red-500'
            : 'text-yellow-500';
        deltaEl = React.createElement(
          'span',
          { className: arrowColor + ' ml-1 text-xs' },
          '(' + arrowChar + ' from ' + followupRateAtTrigger + '%)',
        );
      }
      return React.createElement(
        'td',
        {
          key: col.key,
          className: 'px-3 py-2 text-sm text-center whitespace-nowrap',
        },
        React.createElement(
          'span',
          { className: valColor || undefined },
          fmtVal(val, col.fmt),
        ),
        deltaEl || (dir ? React.createElement(ChangeIcon, { dir: dir }) : null),
      );
    });

    return React.createElement(
      'tr',
      {
        key: flw.username,
        className:
          'hover:bg-gray-50 ' +
          (flw.flags.type === 'red'
            ? 'border-l-4 border-red-400'
            : flw.flags.type === 'yellow'
            ? 'border-l-4 border-yellow-400'
            : ''),
      },
      React.createElement(
        'td',
        { className: 'px-3 py-2 text-sm' },
        React.createElement(
          'div',
          { className: 'font-medium text-gray-900' },
          flw.display_name,
        ),
        React.createElement(
          'div',
          { className: 'text-xs text-gray-400 font-mono' },
          flw.username,
        ),
      ),
      React.createElement(
        'td',
        { className: 'px-3 py-2 text-xs text-gray-500 whitespace-nowrap' },
        daysAgo(flw.last_active),
      ),
      props.taskTriggeredAt !== undefined
        ? React.createElement(
            'td',
            { className: 'px-3 py-2 text-xs text-gray-500 whitespace-nowrap' },
            daysAgo(props.taskTriggeredAt),
          )
        : null,
      React.createElement(
        'td',
        { className: 'px-3 py-2 text-sm text-center' },
        flw.num_mothers,
        flw.num_mothers_eligible != null
          ? React.createElement(
              'span',
              { className: 'text-gray-400 ml-1 text-xs' },
              '(' + flw.num_mothers_eligible + ')',
            )
          : null,
      ),
      cells,
      // Previous run category badge
      React.createElement(
        'td',
        { className: 'px-3 py-2 text-center' },
        prevCat
          ? resultBadge(prevCat.result || prevCat)
          : React.createElement(
              'span',
              { className: 'text-gray-300 text-xs' },
              '—',
            ),
      ),
      React.createElement(
        'td',
        { className: 'px-3 py-2 text-center' },
        React.createElement(FlagBadge, { flags: flw.flags }),
      ),
      // Audit Status (Tab 1 only)
      props.showAuditStatus
        ? React.createElement(
            'td',
            { className: 'px-3 py-2 text-center' },
            React.createElement(AuditStatusCell, { flw: flw }),
          )
        : null,
      React.createElement(
        'td',
        { className: 'px-3 py-2 text-center' },
        React.createElement(TaskCell, { flw: flw }),
      ),
      React.createElement(
        'td',
        { className: 'px-3 py-2 text-center' },
        React.createElement(
          'button',
          {
            className: 'text-xs text-gray-500 hover:text-gray-800 px-1',
            onClick: function () {
              handleOpenNotes(flw);
            },
            title: flw.notes ? 'Notes: ' + flw.notes : 'Add notes',
          },
          flw.notes
            ? React.createElement('i', {
                className: 'fa-solid fa-note-sticky text-blue-400',
              })
            : React.createElement('i', {
                className: 'fa-regular fa-note-sticky text-gray-300',
              }),
        ),
      ),
      // Current category buttons
      React.createElement(
        'td',
        { className: 'px-3 py-2 text-center' },
        React.createElement(CategoryButtons, { flw: flw }),
      ),
    );
  };

  // =========================================================================
  // Table header (shared Tab 1 and Tab 2)
  // =========================================================================
  var TableHeader = function (props) {
    var thCols =
      props.metricCols !== undefined ? props.metricCols : METRIC_COLS;
    return React.createElement(
      'thead',
      { className: 'bg-gray-50 sticky top-0 z-10' },
      React.createElement(
        'tr',
        null,
        React.createElement(SortTh, { col: 'name', label: 'FLW' }),
        React.createElement(
          'th',
          {
            className:
              'px-3 py-3 text-left text-xs font-medium text-gray-500 uppercase whitespace-nowrap',
          },
          'Last Active',
        ),
        props.showTaskTriggered
          ? React.createElement(
              'th',
              {
                className:
                  'px-3 py-3 text-left text-xs font-medium text-gray-500 uppercase whitespace-nowrap',
              },
              'Task Triggered',
            )
          : null,
        React.createElement(SortTh, {
          col: 'num_mothers',
          label: '# Mothers',
          title: 'Total (eligible)',
        }),
        thCols.map(function (col) {
          return React.createElement(SortTh, {
            key: col.key,
            col: col.key,
            label: col.label,
          });
        }),
        React.createElement(
          'th',
          {
            className:
              'px-3 py-3 text-center text-xs font-medium text-gray-500 uppercase whitespace-nowrap',
          },
          'Prev',
        ),
        React.createElement(
          'th',
          {
            className:
              'px-3 py-3 text-center text-xs font-medium text-gray-500 uppercase whitespace-nowrap',
          },
          'Flag',
        ),
        props.showAuditStatus
          ? React.createElement(
              'th',
              {
                className:
                  'px-3 py-3 text-center text-xs font-medium text-gray-500 uppercase whitespace-nowrap',
              },
              'Audit Status',
            )
          : null,
        React.createElement(
          'th',
          {
            className:
              'px-3 py-3 text-center text-xs font-medium text-gray-500 uppercase whitespace-nowrap',
          },
          'Task',
        ),
        React.createElement(
          'th',
          {
            className:
              'px-3 py-3 text-center text-xs font-medium text-gray-500 uppercase w-10',
          },
          'Notes',
        ),
        React.createElement(
          'th',
          {
            className:
              'px-3 py-3 text-center text-xs font-medium text-gray-500 uppercase whitespace-nowrap',
          },
          'Category',
        ),
      ),
    );
  };

  // =========================================================================
  // Filter buttons bar (reusable for Tab 1 and Tab 2)
  // =========================================================================
  var FilterBar = function (props) {
    var total = props.total;
    var redCount = props.redCount;
    var yellowCount = props.yellowCount;
    var taskedCount = props.taskedCount;
    var current = props.current;
    var onChange = props.onChange;
    var options = [
      { id: 'all', label: 'All (' + total + ')' },
      { id: 'red', label: 'Red Flags (' + redCount + ')' },
      {
        id: 'flagged',
        label: 'All Flagged (' + (redCount + yellowCount) + ')',
      },
      { id: 'tasks', label: 'Has Task (' + taskedCount + ')' },
    ];
    return React.createElement(
      'div',
      { className: 'flex gap-2 flex-wrap' },
      options.map(function (f) {
        return React.createElement(
          'button',
          {
            key: f.id,
            onClick: function () {
              onChange(f.id);
            },
            className:
              'px-3 py-1.5 text-sm rounded-full border transition-colors ' +
              (current === f.id
                ? 'bg-blue-600 text-white border-blue-600'
                : 'bg-white text-gray-700 border-gray-300 hover:border-blue-400'),
          },
          f.label,
        );
      }),
    );
  };

  // =========================================================================
  // FLW selection step
  // =========================================================================
  if (step === 'select') {
    var filteredWorkers = (workers || []).filter(function (w) {
      if (!selSearch) return true;
      var q = selSearch.toLowerCase();
      return (
        (w.name || '').toLowerCase().indexOf(q) >= 0 ||
        (w.username || '').toLowerCase().indexOf(q) >= 0
      );
    });
    filteredWorkers = filteredWorkers.slice().sort(function (a, b) {
      var ha = flwHistory[a.username] || {};
      var hb = flwHistory[b.username] || {};
      var va, vb;
      if (selSort.col === 'name') {
        va = (a.name || a.username || '').toLowerCase();
        vb = (b.name || b.username || '').toLowerCase();
      } else if (selSort.col === 'audit_count') {
        va = ha.audit_count || 0;
        vb = hb.audit_count || 0;
      } else if (selSort.col === 'last_audit_date') {
        va = ha.last_audit_date || '';
        vb = hb.last_audit_date || '';
      } else if (selSort.col === 'last_audit_result') {
        var pa = prevCatsForSelect[a.username] || {};
        var pb = prevCatsForSelect[b.username] || {};
        va = pa.result || pa || '';
        vb = pb.result || pb || '';
      } else {
        va = '';
        vb = '';
      }
      var cmp =
        typeof va === 'number' ? va - vb : String(va).localeCompare(String(vb));
      return selSort.dir === 'asc' ? cmp : -cmp;
    });

    var selectedCount = Object.values(selectedFlws).filter(Boolean).length;
    var allSel =
      (workers || []).length > 0 &&
      (workers || []).every(function (w) {
        return selectedFlws[w.username];
      });

    var selHeader = function (col, label, align) {
      var active = selSort.col === col;
      return React.createElement(
        'th',
        {
          key: col,
          className:
            'px-4 py-2 text-xs font-medium text-gray-500 uppercase cursor-pointer hover:bg-gray-100 select-none' +
            (align === 'center' ? ' text-center' : ' text-left'),
          onClick: function () {
            setSelSort({
              col: col,
              dir: active && selSort.dir === 'asc' ? 'desc' : 'asc',
            });
          },
        },
        label + (active ? (selSort.dir === 'asc' ? ' ▲' : ' ▼') : ''),
      );
    };

    var prevCatBadge = function (catEntry) {
      // catEntry may be {result: "...", notes: "..."} or a bare string
      var result = catEntry && (catEntry.result || catEntry);
      if (!result)
        return React.createElement('span', { className: 'text-gray-300' }, '—');
      return resultBadge(result);
    };

    return React.createElement(
      'div',
      { className: 'space-y-6' },
      // Header
      React.createElement(
        'div',
        { className: 'bg-white rounded-lg shadow-sm p-6' },
        React.createElement(
          'h2',
          { className: 'text-xl font-bold text-gray-900' },
          'Select FLWs for Audit',
        ),
        React.createElement(
          'p',
          { className: 'text-gray-600 mt-1' },
          'Choose which frontline workers to include in this audit run.',
        ),
      ),
      // Table
      React.createElement(
        'div',
        { className: 'bg-white rounded-lg shadow-sm overflow-hidden' },
        historyLoading &&
          React.createElement(
            'div',
            {
              className: 'px-4 py-2 text-xs text-gray-400 bg-gray-50 border-b',
            },
            'Loading audit history…',
          ),
        // Toolbar
        React.createElement(
          'div',
          {
            className: 'px-4 py-2 bg-gray-50 border-b flex items-center gap-2',
          },
          React.createElement('input', {
            type: 'text',
            value: selSearch,
            onChange: function (e) {
              setSelSearch(e.target.value);
            },
            placeholder: 'Search FLWs…',
            className: 'border rounded px-2 py-1 text-sm flex-1',
          }),
          React.createElement(
            'span',
            { className: 'text-sm text-gray-500' },
            selectedCount + ' selected',
          ),
        ),
        // Table body
        React.createElement(
          'div',
          { className: 'max-h-96 overflow-y-auto' },
          React.createElement(
            'table',
            { className: 'min-w-full divide-y divide-gray-200' },
            React.createElement(
              'thead',
              { className: 'bg-gray-50 sticky top-0' },
              React.createElement(
                'tr',
                null,
                React.createElement(
                  'th',
                  { className: 'px-4 py-2 text-left w-10' },
                  React.createElement('input', {
                    type: 'checkbox',
                    checked: allSel,
                    onChange: toggleAll,
                  }),
                ),
                selHeader('name', 'FLW (' + (workers || []).length + ')'),
                selHeader('username', 'Connect ID'),
                selHeader('audit_count', 'Past Audits', 'center'),
                selHeader('last_audit_date', 'Last Audit Date'),
                selHeader('last_audit_result', 'Last Performance Category'),
              ),
            ),
            React.createElement(
              'tbody',
              { className: 'divide-y divide-gray-200' },
              filteredWorkers.map(function (w) {
                var h = flwHistory[w.username] || {};
                return React.createElement(
                  'tr',
                  {
                    key: w.username,
                    className: 'hover:bg-gray-50 cursor-pointer',
                    onClick: function () {
                      toggleFlw(w.username);
                    },
                  },
                  React.createElement(
                    'td',
                    { className: 'px-4 py-2' },
                    React.createElement('input', {
                      type: 'checkbox',
                      checked: !!selectedFlws[w.username],
                      onChange: function () {
                        toggleFlw(w.username);
                      },
                      onClick: function (e) {
                        e.stopPropagation();
                      },
                    }),
                  ),
                  React.createElement(
                    'td',
                    { className: 'px-4 py-2' },
                    React.createElement(
                      'div',
                      { className: 'font-medium text-sm' },
                      w.name || w.username,
                    ),
                  ),
                  React.createElement(
                    'td',
                    { className: 'px-4 py-2 text-xs text-gray-500 font-mono' },
                    w.username,
                  ),
                  React.createElement(
                    'td',
                    {
                      className: 'px-4 py-2 text-center text-sm text-gray-600',
                    },
                    h.audit_count > 0
                      ? h.audit_count
                      : React.createElement(
                          'span',
                          { className: 'text-gray-300' },
                          '—',
                        ),
                  ),
                  React.createElement(
                    'td',
                    { className: 'px-4 py-2 text-sm text-gray-600' },
                    h.last_audit_date
                      ? new Date(h.last_audit_date).toLocaleDateString(
                          'en-US',
                          {
                            month: 'short',
                            day: 'numeric',
                            year: 'numeric',
                          },
                        )
                      : React.createElement(
                          'span',
                          { className: 'text-gray-300' },
                          '—',
                        ),
                  ),
                  React.createElement(
                    'td',
                    { className: 'px-4 py-2 text-sm' },
                    prevCatBadge(prevCatsForSelect[w.username]),
                  ),
                );
              }),
            ),
          ),
        ),
      ),
      // Launch button
      React.createElement(
        'div',
        { className: 'flex justify-end' },
        React.createElement(
          'button',
          {
            onClick: handleLaunch,
            disabled: selectedCount === 0 || launching,
            className:
              'px-6 py-2 bg-indigo-600 text-white rounded-md hover:bg-indigo-700 disabled:opacity-50',
          },
          launching ? 'Launching…' : 'Run Audit (' + selectedCount + ' FLWs)',
        ),
      ),
    );
  }

  // =========================================================================
  // Loading / error states
  // =========================================================================
  if (step === 'idle' || step === 'running') {
    return React.createElement(
      'div',
      { className: 'space-y-4' },
      React.createElement(
        'div',
        { className: 'bg-white rounded-lg shadow-sm p-6' },
        React.createElement(
          'h1',
          { className: 'text-2xl font-bold text-gray-900' },
          definition.name,
        ),
        React.createElement(
          'p',
          { className: 'text-gray-600 mt-1' },
          definition.description,
        ),
      ),
      React.createElement(
        'div',
        { className: 'bg-blue-50 border border-blue-200 rounded-lg p-6' },
        React.createElement(
          'div',
          { className: 'flex items-center gap-3 mb-3' },
          React.createElement('i', {
            className: 'fa-solid fa-spinner fa-spin text-blue-600 text-xl',
          }),
          React.createElement(
            'span',
            { className: 'font-medium text-blue-800' },
            step === 'idle' ? 'Preparing analysis…' : 'Running analysis…',
          ),
        ),
        jobMessages.length > 0 &&
          React.createElement(
            'div',
            { className: 'text-sm text-blue-700 space-y-0.5' },
            jobMessages.slice(-5).map(function (m, i) {
              return React.createElement('div', { key: i }, m);
            }),
          ),
      ),
    );
  }

  if (step === 'error') {
    return React.createElement(
      'div',
      { className: 'space-y-4' },
      React.createElement(
        'div',
        { className: 'bg-white rounded-lg shadow-sm p-6' },
        React.createElement(
          'h1',
          { className: 'text-2xl font-bold text-gray-900' },
          definition.name,
        ),
      ),
      React.createElement(
        'div',
        { className: 'bg-red-50 border border-red-200 rounded-lg p-6' },
        React.createElement(
          'div',
          {
            className: 'flex items-center gap-2 text-red-800 font-medium mb-2',
          },
          React.createElement('i', {
            className: 'fa-solid fa-circle-exclamation',
          }),
          'Analysis Error',
        ),
        React.createElement(
          'p',
          { className: 'text-red-700 text-sm' },
          jobError || 'An unknown error occurred.',
        ),
        React.createElement(
          'button',
          {
            className:
              'mt-4 px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700 text-sm font-medium',
            onClick: function () {
              setStep('idle');
              runAnalysis();
            },
          },
          'Retry',
        ),
      ),
    );
  }

  // =========================================================================
  // KPI summary (compact single row)
  // =========================================================================
  var totalFlws = enrichedData.length;
  var redCount = enrichedData.filter(function (f) {
    return f.flags.type === 'red';
  }).length;
  var yellowCount = enrichedData.filter(function (f) {
    return f.flags.type === 'yellow';
  }).length;
  var taskedCount = enrichedData.filter(function (f) {
    return f.hasTask;
  }).length;
  var categorizedCount = enrichedData.filter(function (f) {
    return f.result;
  }).length;

  var KpiBar = function () {
    var kpis = [
      {
        label: 'FLWs',
        value: totalFlws,
        bg: 'bg-blue-50 border-blue-300 text-blue-700',
      },
      {
        label: 'Red ⚑',
        value: redCount,
        bg: 'bg-red-50 border-red-300 text-red-700',
      },
      {
        label: 'Yellow ⚑',
        value: yellowCount,
        bg: 'bg-yellow-50 border-yellow-300 text-yellow-700',
      },
      {
        label: 'Tasks',
        value: taskedCount,
        bg: 'bg-orange-50 border-orange-300 text-orange-700',
      },
      {
        label: 'Categorized',
        value: categorizedCount + '/' + totalFlws,
        bg: 'bg-green-50 border-green-300 text-green-700',
      },
    ];
    return React.createElement(
      'div',
      { className: 'flex items-center gap-2 flex-wrap' },
      kpis.map(function (kpi, i) {
        return React.createElement(
          'div',
          {
            key: i,
            className:
              'flex flex-col items-center justify-center w-20 h-14 rounded-lg border text-center ' +
              kpi.bg,
          },
          React.createElement(
            'div',
            { className: 'text-lg font-bold leading-tight' },
            kpi.value,
          ),
          React.createElement(
            'div',
            { className: 'text-xs leading-tight mt-0.5' },
            kpi.label,
          ),
        );
      }),
    );
  };

  // =========================================================================
  // Tab 1: Per FLW Audit Report
  // =========================================================================
  var Tab1 = function () {
    return React.createElement(
      'div',
      { className: 'space-y-4' },
      React.createElement(
        'div',
        { className: 'bg-white rounded-lg shadow-sm p-4' },
        React.createElement(
          'div',
          { className: 'flex flex-wrap items-center gap-3' },
          React.createElement(FilterBar, {
            total: enrichedData.length,
            redCount: redCount,
            yellowCount: yellowCount,
            taskedCount: taskedCount,
            current: filterFlag,
            onChange: setFilterFlag,
          }),
          React.createElement('input', {
            type: 'text',
            placeholder: 'Search FLWs…',
            value: search,
            onChange: function (e) {
              setSearch(e.target.value);
            },
            className:
              'flex-1 min-w-36 border border-gray-300 rounded-lg px-3 py-1.5 text-sm',
          }),
          React.createElement(
            'div',
            {
              className: 'ml-auto flex items-center gap-2',
              style: { position: 'relative' },
            },
            showColPicker &&
              React.createElement('div', {
                style: { position: 'fixed', inset: 0, zIndex: 40 },
                onClick: function () {
                  setShowColPicker(false);
                },
              }),
            React.createElement(
              'button',
              {
                className:
                  'px-3 py-1.5 text-sm rounded border bg-white text-gray-700 border-gray-300 hover:bg-gray-50 inline-flex items-center',
                onClick: function () {
                  setShowColPicker(function (v) {
                    return !v;
                  });
                },
              },
              React.createElement('i', {
                className: 'fa-solid fa-table-columns mr-2',
              }),
              'Columns',
              React.createElement(
                'span',
                {
                  className:
                    'ml-1.5 bg-gray-100 text-gray-600 text-xs px-1.5 py-0.5 rounded-full',
                },
                visibleCols.length + '/' + METRIC_COLS.length,
              ),
            ),
            showColPicker &&
              React.createElement(
                'div',
                {
                  style: {
                    position: 'absolute',
                    right: 0,
                    top: '100%',
                    marginTop: '4px',
                    zIndex: 50,
                    width: '210px',
                    backgroundColor: 'white',
                    border: '1px solid #e5e7eb',
                    borderRadius: '8px',
                    boxShadow: '0 10px 15px -3px rgba(0,0,0,0.1)',
                  },
                },
                React.createElement(
                  'div',
                  { className: 'px-3 py-2 border-b border-gray-200' },
                  React.createElement(
                    'span',
                    {
                      className: 'text-xs font-medium text-gray-500 uppercase',
                    },
                    'Toggle Columns',
                  ),
                ),
                React.createElement(
                  'div',
                  {
                    style: { maxHeight: '320px', overflowY: 'auto' },
                    className: 'py-1',
                  },
                  METRIC_COLS.map(function (col) {
                    return React.createElement(
                      'label',
                      {
                        key: col.key,
                        className:
                          'flex items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50',
                      },
                      React.createElement('input', {
                        type: 'checkbox',
                        checked: visibleCols.indexOf(col.key) >= 0,
                        onChange: function () {
                          toggleCol(col.key);
                        },
                        className: 'mr-2 rounded border-gray-300',
                        style: { accentColor: '#2563eb' },
                      }),
                      col.label,
                    );
                  }),
                ),
                React.createElement(
                  'div',
                  {
                    className: 'px-3 py-2 border-t border-gray-200 flex gap-3',
                  },
                  React.createElement(
                    'button',
                    {
                      className: 'text-xs text-blue-600 hover:underline',
                      onClick: function () {
                        setVisibleCols(
                          METRIC_COLS.map(function (c) {
                            return c.key;
                          }),
                        );
                      },
                    },
                    'Show all',
                  ),
                  React.createElement(
                    'button',
                    {
                      className: 'text-xs text-gray-500 hover:underline',
                      onClick: function () {
                        setVisibleCols([]);
                      },
                    },
                    'Hide all',
                  ),
                ),
              ),
            React.createElement(
              'button',
              {
                className:
                  'px-3 py-1.5 text-sm rounded border bg-white text-gray-700 border-gray-300 hover:bg-gray-50',
                onClick: runAnalysis,
                title: 'Refresh data',
              },
              React.createElement('i', {
                className: 'fa-solid fa-rotate-right mr-1',
              }),
              'Refresh',
            ),
          ),
        ),
      ),
      React.createElement(
        'div',
        {
          className: 'bg-white rounded-lg shadow-sm overflow-auto',
          style: { maxHeight: '70vh' },
        },
        React.createElement(
          'table',
          { className: 'min-w-full divide-y divide-gray-200' },
          React.createElement(TableHeader, {
            showAuditStatus: true,
            metricCols: effectiveMetricCols,
          }),
          React.createElement(
            'tbody',
            { className: 'bg-white divide-y divide-gray-200' },
            filteredData.map(function (flw) {
              var rows = [
                React.createElement(MetricRow, {
                  key: flw.username,
                  flw: flw,
                  showChange: true,
                  showAuditStatus: true,
                  metricCols: effectiveMetricCols,
                }),
              ];
              if (expandedTaskFlw === flw.username) {
                rows.push(
                  React.createElement(TaskDetailPanel, {
                    key: flw.username + '-detail',
                    username: flw.username,
                    colCount: effectiveMetricCols.length + 9,
                  }),
                );
              }
              return rows;
            }),
            filteredData.length === 0 &&
              React.createElement(
                'tr',
                null,
                React.createElement(
                  'td',
                  {
                    colSpan: 17,
                    className: 'px-4 py-8 text-center text-gray-400',
                  },
                  'No FLWs match current filters.',
                ),
              ),
          ),
        ),
      ),
      Object.keys(prevMetrics).length > 0 &&
        React.createElement(
          'p',
          { className: 'text-xs text-gray-400 px-1' },
          '▲▼ arrows show change vs. previous concluded run. "Prev" column shows last run\'s category.',
        ),
    );
  };

  // =========================================================================
  // Tab 2: Improvement Within Audit
  // =========================================================================
  var Tab2 = function () {
    if (tab2FlaggedRows.length === 0) {
      return React.createElement(
        'div',
        { className: 'bg-white rounded-lg shadow-sm p-8 text-center' },
        React.createElement('i', {
          className: 'fa-solid fa-check-circle text-green-400 text-3xl mb-3',
        }),
        React.createElement(
          'p',
          { className: 'text-gray-600' },
          'No FLWs with open tasks in this run.',
        ),
      );
    }

    var taskedWithDate = tab2FlaggedRows.filter(function (f) {
      return taskStates[f.username] && taskStates[f.username].triggered_at;
    });

    return React.createElement(
      'div',
      { className: 'space-y-4' },
      // Filter bar
      React.createElement(
        'div',
        { className: 'bg-white rounded-lg shadow-sm p-4' },
        React.createElement(FilterBar, {
          total: tab2FlaggedRows.length,
          redCount: tab2FlaggedRows.filter(function (f) {
            return f.flags.type === 'red';
          }).length,
          yellowCount: tab2FlaggedRows.filter(function (f) {
            return f.flags.type === 'yellow';
          }).length,
          taskedCount: tab2FlaggedRows.filter(function (f) {
            return f.hasTask;
          }).length,
          current: tab2FilterFlag,
          onChange: setTab2FilterFlag,
        }),
      ),
      // Info + compute button
      React.createElement(
        'div',
        {
          className:
            'bg-blue-50 border border-blue-200 rounded-lg p-3 flex items-start justify-between gap-3 flex-wrap',
        },
        React.createElement(
          'div',
          { className: 'text-sm text-blue-700' },
          React.createElement('i', {
            className: 'fa-solid fa-circle-info mr-1',
          }),
          tab2Step === 'ready'
            ? "Post-task metrics: only data submitted after each FLW's task was triggered."
            : 'Showing FLWs with open tasks. Click "Compute Post-Task Metrics" to load data submitted after each task was triggered.',
        ),
        React.createElement(
          'div',
          {
            className: 'shrink-0 flex items-center gap-2',
            style: { position: 'relative' },
          },
          showColPicker &&
            React.createElement('div', {
              style: { position: 'fixed', inset: 0, zIndex: 40 },
              onClick: function () {
                setShowColPicker(false);
              },
            }),
          React.createElement(
            'button',
            {
              className:
                'px-3 py-1.5 text-sm rounded border bg-white text-blue-700 border-blue-300 hover:bg-blue-50 inline-flex items-center',
              onClick: function () {
                setShowColPicker(function (v) {
                  return !v;
                });
              },
            },
            React.createElement('i', {
              className: 'fa-solid fa-table-columns mr-2',
            }),
            'Columns',
            React.createElement(
              'span',
              {
                className:
                  'ml-1.5 bg-blue-100 text-blue-600 text-xs px-1.5 py-0.5 rounded-full',
              },
              visibleCols.length + '/' + METRIC_COLS.length,
            ),
          ),
          showColPicker &&
            React.createElement(
              'div',
              {
                style: {
                  position: 'absolute',
                  right: 0,
                  top: '100%',
                  marginTop: '4px',
                  zIndex: 50,
                  width: '210px',
                  backgroundColor: 'white',
                  border: '1px solid #e5e7eb',
                  borderRadius: '8px',
                  boxShadow: '0 10px 15px -3px rgba(0,0,0,0.1)',
                },
              },
              React.createElement(
                'div',
                { className: 'px-3 py-2 border-b border-gray-200' },
                React.createElement(
                  'span',
                  { className: 'text-xs font-medium text-gray-500 uppercase' },
                  'Toggle Columns',
                ),
              ),
              React.createElement(
                'div',
                {
                  style: { maxHeight: '320px', overflowY: 'auto' },
                  className: 'py-1',
                },
                METRIC_COLS.map(function (col) {
                  return React.createElement(
                    'label',
                    {
                      key: col.key,
                      className:
                        'flex items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50',
                    },
                    React.createElement('input', {
                      type: 'checkbox',
                      checked: visibleCols.indexOf(col.key) >= 0,
                      onChange: function () {
                        toggleCol(col.key);
                      },
                      className: 'mr-2 rounded border-gray-300',
                      style: { accentColor: '#2563eb' },
                    }),
                    col.label,
                  );
                }),
              ),
              React.createElement(
                'div',
                { className: 'px-3 py-2 border-t border-gray-200 flex gap-3' },
                React.createElement(
                  'button',
                  {
                    className: 'text-xs text-blue-600 hover:underline',
                    onClick: function () {
                      setVisibleCols(
                        METRIC_COLS.map(function (c) {
                          return c.key;
                        }),
                      );
                    },
                  },
                  'Show all',
                ),
                React.createElement(
                  'button',
                  {
                    className: 'text-xs text-gray-500 hover:underline',
                    onClick: function () {
                      setVisibleCols([]);
                    },
                  },
                  'Hide all',
                ),
              ),
            ),
          React.createElement(
            'button',
            {
              className:
                'shrink-0 px-3 py-1.5 text-sm rounded border font-medium transition-colors ' +
                (tab2Step === 'running' || taskedWithDate.length === 0
                  ? 'bg-gray-200 text-gray-400 cursor-not-allowed'
                  : 'bg-blue-600 text-white border-blue-600 hover:bg-blue-700'),
              onClick: runTab2Analysis,
              disabled: tab2Step === 'running' || taskedWithDate.length === 0,
              title:
                taskedWithDate.length === 0
                  ? 'No task trigger dates found — create tasks for FLWs first'
                  : '',
            },
            tab2Step === 'running'
              ? React.createElement(
                  'span',
                  null,
                  React.createElement('i', {
                    className: 'fa-solid fa-spinner fa-spin mr-1',
                  }),
                  'Computing…',
                )
              : React.createElement(
                  'span',
                  null,
                  React.createElement('i', {
                    className: 'fa-solid fa-rotate-right mr-1',
                  }),
                  'Compute Post-Task Metrics',
                ),
          ),
        ),
      ),
      React.createElement(
        'div',
        {
          className: 'bg-white rounded-lg shadow-sm overflow-auto',
          style: { maxHeight: '70vh' },
        },
        React.createElement(
          'table',
          { className: 'min-w-full divide-y divide-gray-200' },
          React.createElement(TableHeader, {
            metricCols: effectiveTab2MetricCols,
            showTaskTriggered: true,
          }),
          React.createElement(
            'tbody',
            { className: 'bg-white divide-y divide-gray-200' },
            tab2FilteredRows.map(function (flw) {
              var postTask = tab2Data && tab2Data[flw.username];
              var followupRateAtTrigger =
                postTask && postTask.followup_rate_at_trigger != null
                  ? postTask.followup_rate_at_trigger
                  : null;
              var displayFlw = postTask
                ? Object.assign({}, flw, {
                    gs_score: postTask.gs_score,
                    followup_rate: flw.followup_rate,
                    ebf_pct: postTask.ebf_pct,
                    revisit_dist: postTask.revisit_dist,
                    meter_per_visit: postTask.meter_per_visit,
                    dist_ratio: postTask.dist_ratio,
                    minute_per_visit: postTask.minute_per_visit,
                    followup_rate_denom: flw.followup_rate_denom,
                    ebf_denom: postTask.ebf_denom,
                    gps_denom: postTask.gps_denom,
                    duration_denom: postTask.duration_denom,
                  })
                : flw;
              var tab2PrevOverride = null;
              if (postTask) {
                tab2PrevOverride = {};
                effectiveTab2MetricCols.forEach(function (col) {
                  tab2PrevOverride[col.key] = flw[col.key];
                });
              }
              var tab2Rows = [
                React.createElement(MetricRow, {
                  key: flw.username,
                  flw: displayFlw,
                  showChange: !!postTask,
                  prevOverride: tab2PrevOverride,
                  followupRateAtTrigger: followupRateAtTrigger,
                  metricCols: effectiveTab2MetricCols,
                  taskTriggeredAt: flw.taskTriggeredAt,
                }),
              ];
              if (expandedTaskFlw === flw.username) {
                tab2Rows.push(
                  React.createElement(TaskDetailPanel, {
                    key: flw.username + '-detail',
                    username: flw.username,
                    colCount: effectiveTab2MetricCols.length + 9,
                  }),
                );
              }
              return tab2Rows;
            }),
            tab2FilteredRows.length === 0 &&
              React.createElement(
                'tr',
                null,
                React.createElement(
                  'td',
                  {
                    colSpan: 15,
                    className: 'px-4 py-8 text-center text-gray-400',
                  },
                  'No FLWs match current filter.',
                ),
              ),
          ),
        ),
      ),
    );
  };

  // =========================================================================
  // Tab 3: Summary by Performance Band
  // =========================================================================
  var Tab3 = function () {
    var bands = perfData || computePerfBands();
    var bandColor = {
      green: 'border-green-400',
      yellow: 'border-yellow-400',
      red: 'border-red-400',
      gray: 'border-gray-300',
    };

    return React.createElement(
      'div',
      { className: 'space-y-4' },
      React.createElement(
        'div',
        { className: 'flex items-center justify-between' },
        React.createElement(
          'p',
          { className: 'text-sm text-gray-500' },
          'Based on latest performance categories set for each FLW.',
        ),
        React.createElement(
          'button',
          {
            className:
              'px-3 py-1.5 text-sm rounded border bg-white text-gray-700 border-gray-300 hover:bg-gray-50',
            onClick: function () {
              setPerfData(computePerfBands());
            },
          },
          React.createElement('i', {
            className: 'fa-solid fa-rotate-right mr-1',
          }),
          'Refresh',
        ),
      ),
      React.createElement(
        'div',
        { className: 'overflow-x-auto bg-white rounded-lg shadow-sm' },
        React.createElement(
          'table',
          { className: 'min-w-full divide-y divide-gray-200' },
          React.createElement(
            'thead',
            { className: 'bg-gray-50' },
            React.createElement(
              'tr',
              null,
              React.createElement(
                'th',
                {
                  className:
                    'px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase',
                },
                'Status',
              ),
              React.createElement(
                'th',
                {
                  className:
                    'px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase',
                },
                '# FLWs',
              ),
              React.createElement(
                'th',
                {
                  className:
                    'px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase',
                },
                'Total Mothers',
              ),
              React.createElement(
                'th',
                {
                  className:
                    'px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase',
                },
                'Eligible at Reg',
              ),
              React.createElement(
                'th',
                {
                  className:
                    'px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase',
                },
                'Still Eligible',
              ),
              React.createElement(
                'th',
                {
                  className:
                    'px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase',
                },
                '% Still Eligible',
              ),
              React.createElement(
                'th',
                {
                  className:
                    'px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase',
                },
                'Avg Follow-up %',
              ),
              React.createElement(
                'th',
                {
                  className:
                    'px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase',
                },
                'Avg GS Score',
              ),
            ),
          ),
          React.createElement(
            'tbody',
            { className: 'bg-white divide-y divide-gray-200' },
            bands.map(function (band) {
              var pctColor =
                band.pct_still_eligible != null
                  ? band.pct_still_eligible >= 85
                    ? '#22c55e'
                    : band.pct_still_eligible >= 50
                    ? '#eab308'
                    : '#ef4444'
                  : undefined;
              return React.createElement(
                'tr',
                {
                  key: band.id || 'none',
                  className:
                    'hover:bg-gray-50 border-l-4 ' +
                    (bandColor[band.color] || bandColor.gray),
                },
                React.createElement(
                  'td',
                  {
                    className:
                      'px-3 py-2 font-medium text-sm text-gray-900 whitespace-nowrap',
                  },
                  React.createElement(
                    'span',
                    { className: 'inline-flex items-center gap-1.5' },
                    React.createElement('span', {
                      style: {
                        width: 10,
                        height: 10,
                        borderRadius: '50%',
                        backgroundColor: {
                          green: '#22c55e',
                          yellow: '#eab308',
                          red: '#ef4444',
                          gray: '#9ca3af',
                        }[band.color],
                        display: 'inline-block',
                      },
                    }),
                    band.label,
                  ),
                ),
                React.createElement(
                  'td',
                  {
                    className:
                      'px-3 py-2 text-right text-sm font-bold text-gray-800',
                  },
                  band.num_flws,
                ),
                React.createElement(
                  'td',
                  { className: 'px-3 py-2 text-right text-sm text-gray-700' },
                  band.total_mothers,
                ),
                React.createElement(
                  'td',
                  { className: 'px-3 py-2 text-right text-sm text-gray-700' },
                  band.total_eligible,
                ),
                React.createElement(
                  'td',
                  { className: 'px-3 py-2 text-right text-sm text-gray-700' },
                  band.total_still_eligible,
                ),
                React.createElement(
                  'td',
                  {
                    className: 'px-3 py-2 text-right text-sm font-medium',
                    style: pctColor ? { color: pctColor } : undefined,
                  },
                  band.pct_still_eligible != null
                    ? band.pct_still_eligible + '%'
                    : '—',
                ),
                React.createElement(
                  'td',
                  { className: 'px-3 py-2 text-right text-sm text-gray-700' },
                  band.avg_fu != null ? band.avg_fu + '%' : '—',
                ),
                React.createElement(
                  'td',
                  { className: 'px-3 py-2 text-right text-sm text-gray-700' },
                  band.avg_gs != null ? band.avg_gs + '%' : '—',
                ),
              );
            }),
          ),
        ),
      ),
    );
  };

  // =========================================================================
  // Tab 4: Guide
  // =========================================================================
  var Tab4 = function () {
    var sections = [
      {
        title: 'Workflow Overview',
        body: 'Every two weeks, the PM triggers a new audit run. The dashboard loads data for all active FLWs. The PM reviews flags, triggers tasks for red-flagged FLWs (mandatory) and yellow-flagged FLWs (optional with a note), monitors improvement over 7 days, then sets final performance categories and concludes the run.',
      },
      {
        title: 'Flag Types',
        body: '🔴 Red flag = task required. Triggered by: Follow-up Rate below 50%, % Still Eligible below 50%, or GS Score below 50%.\n🟡 Yellow flag = task optional. Triggered by: Follow-up Rate 50–79%, % Still Eligible below 80%, EBF% ≤30% or >94%, GPS Dist Ratio < 1.0, or any metric worsening >10% since the last concluded run.',
      },
      {
        title: 'Metric Definitions',
        items: [
          {
            name: 'GS Score',
            def: 'Gold Standard visit checklist score. Max score recorded for this FLW. Red flag if below 50%.',
          },
          {
            name: 'Follow-up Rate',
            def: 'Of visits due more than 5 days ago (across all mothers), % completed. Red if below 50%, yellow if 50–79%.',
          },
          {
            name: '% Still Eligible',
            def: 'Of eligible mothers, % who have missed fewer than 2 visits. Green ≥85%, yellow 50–84%, red <50%.',
          },
          {
            name: 'EBF %',
            def: 'Percentage of visits where current breastfeeding status is exclusive. Yellow flag if ≤30% or >94%.',
          },
          {
            name: 'Revisit Dist (m)',
            def: 'Mean GPS distance between visits to the same mother case (meters). Green < 30m, yellow 30–50m, red > 50m.',
          },
          {
            name: 'Meter/Visit',
            def: 'Median GPS distance per revisit (meters). Green > 50m, yellow 20–50m, red < 20m.',
          },
          {
            name: 'Min/Visit',
            def: 'Median duration per visit in minutes (requires visit timeStart). Green > 20 min, yellow 10–20 min, red < 10 min.',
          },
          {
            name: 'Dist Ratio',
            def: 'Mean revisit distance ÷ Median. Values below 1.0 suggest GPS clustering (yellow flag).',
          },
          {
            name: 'Prev',
            def: 'Category assigned in the previous concluded run (Eligible / Requires Improvement / Suspension).',
          },
        ],
      },
      {
        title: 'Performance Categories',
        items: [
          {
            name: 'Eligible for Renewal ✓',
            def: 'FLW met performance standards and is eligible for program renewal.',
          },
          {
            name: 'Requires Improvement ⚠',
            def: 'FLW showed improvement but needs continued monitoring.',
          },
          {
            name: 'Suspension ✗',
            def: 'FLW did not improve sufficiently and is recommended for suspension.',
          },
        ],
      },
      {
        title: 'Concluding a Run',
        body: 'The run can only be concluded once all open tasks are resolved. When concluded, current metrics and categories are saved as the baseline for the next run. Use Tab 3 to review the performance band breakdown before concluding.',
      },
    ];
    return React.createElement(
      'div',
      { className: 'space-y-6 max-w-3xl' },
      sections.map(function (s, i) {
        return React.createElement(
          'div',
          { key: i, className: 'bg-white rounded-lg shadow-sm p-6' },
          React.createElement(
            'h3',
            { className: 'text-lg font-semibold text-gray-900 mb-3' },
            s.title,
          ),
          s.body &&
            React.createElement(
              'p',
              { className: 'text-sm text-gray-700 whitespace-pre-line' },
              s.body,
            ),
          s.items &&
            React.createElement(
              'dl',
              { className: 'space-y-2' },
              s.items.map(function (item, j) {
                return React.createElement(
                  'div',
                  { key: j },
                  React.createElement(
                    'dt',
                    { className: 'text-sm font-medium text-gray-900' },
                    item.name,
                  ),
                  React.createElement(
                    'dd',
                    { className: 'text-sm text-gray-600 ml-4' },
                    item.def,
                  ),
                );
              }),
            ),
        );
      }),
    );
  };

  // =========================================================================
  // Main render
  // =========================================================================
  var tabs = [
    { id: 'audit', label: 'Audit Report', icon: 'fa-table' },
    { id: 'improvement', label: 'Improvement in Audit', icon: 'fa-chart-line' },
    { id: 'summary', label: 'Summary by Band', icon: 'fa-layer-group' },
    { id: 'guide', label: 'Guide', icon: 'fa-book' },
  ];

  var notesFlwName = notesModal ? flwNameMap[notesModal] || notesModal : '';

  return React.createElement(
    'div',
    { className: 'space-y-3 pb-8' },

    // Header
    React.createElement(
      'div',
      {
        className:
          'bg-white rounded-lg shadow-sm p-4 flex items-center justify-between gap-4',
      },
      React.createElement(
        'div',
        null,
        React.createElement(
          'h1',
          { className: 'text-xl font-bold text-gray-900' },
          definition.name,
        ),
        React.createElement(
          'p',
          { className: 'text-gray-600 text-sm mt-0.5' },
          definition.description,
        ),
      ),
      React.createElement(
        'button',
        {
          className:
            'px-4 py-2 text-sm rounded-lg font-medium transition-colors shrink-0 ' +
            (!canConclude
              ? 'bg-gray-200 text-gray-400 cursor-not-allowed'
              : 'bg-green-600 text-white hover:bg-green-700'),
          onClick: function () {
            if (canConclude) setConcludeModal(true);
          },
          disabled: !canConclude,
          title: canConclude
            ? 'Conclude this audit run'
            : 'All tasks must be resolved, all red FLWs must have tasks, and all yellow FLWs must be triaged before concluding',
        },
        React.createElement('i', {
          className: 'fa-solid fa-flag-checkered mr-2',
        }),
        'Conclude Run',
      ),
    ),

    // KPI bar (compact single row)
    React.createElement(
      'div',
      { className: 'bg-white rounded-lg shadow-sm px-4 py-3' },
      React.createElement(KpiBar, null),
    ),

    // Tab bar + content
    React.createElement(
      'div',
      { className: 'bg-white rounded-lg shadow-sm' },
      React.createElement(
        'div',
        { className: 'flex border-b border-gray-200 overflow-x-auto' },
        tabs.map(function (tab) {
          return React.createElement(
            'button',
            {
              key: tab.id,
              onClick: function () {
                setActiveTab(tab.id);
              },
              className:
                'flex items-center gap-2 px-5 py-3 text-sm font-medium border-b-2 transition-colors whitespace-nowrap ' +
                (activeTab === tab.id
                  ? 'border-blue-600 text-blue-600'
                  : 'border-transparent text-gray-500 hover:text-gray-800 hover:border-gray-300'),
            },
            React.createElement('i', { className: 'fa-solid ' + tab.icon }),
            tab.label,
          );
        }),
      ),
      React.createElement(
        'div',
        { className: 'p-4' },
        activeTab === 'audit'
          ? React.createElement(Tab1, null)
          : activeTab === 'improvement'
          ? React.createElement(Tab2, null)
          : activeTab === 'summary'
          ? React.createElement(Tab3, null)
          : React.createElement(Tab4, null),
      ),
    ),

    // Notes modal (with inline category result buttons, like mbw_monitoring_v2)
    notesModal &&
      React.createElement(
        'div',
        {
          className:
            'fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-40',
          onClick: function (e) {
            if (e.target === e.currentTarget) setNotesModal(null);
          },
        },
        React.createElement(
          'div',
          { className: 'bg-white rounded-xl shadow-2xl w-full max-w-md mx-4' },
          React.createElement(
            'div',
            {
              className:
                'px-6 py-4 border-b border-gray-200 font-semibold text-gray-900',
            },
            'Notes for ' + notesFlwName,
          ),
          React.createElement(
            'div',
            { className: 'px-6 py-4 space-y-3' },
            React.createElement('textarea', {
              className:
                'w-full border border-gray-300 rounded-lg px-3 py-2 text-sm',
              rows: 4,
              value: notesDraft,
              onChange: function (e) {
                setNotesDraft(e.target.value);
              },
              placeholder: 'Add notes about this FLW…',
            }),
            React.createElement(
              'div',
              { className: 'flex items-center gap-2 flex-wrap' },
              React.createElement(
                'span',
                { className: 'text-xs text-gray-600' },
                'Category:',
              ),
              PERF_CATEGORIES.map(function (cat) {
                var active = notesModalResult === cat.id;
                return React.createElement(
                  'button',
                  {
                    key: cat.id,
                    onClick: function () {
                      setNotesModalResult(active ? null : cat.id);
                    },
                    className:
                      'px-3 py-1 rounded text-xs font-medium border transition-colors ' +
                      (active ? cat.active : cat.inactive),
                  },
                  React.createElement('i', {
                    className: 'fa-solid ' + cat.icon + ' mr-1',
                  }),
                  cat.label,
                );
              }),
              notesModalResult &&
                React.createElement(
                  'button',
                  {
                    onClick: function () {
                      setNotesModalResult(null);
                    },
                    className:
                      'px-2 py-1 text-xs rounded border border-gray-300 text-gray-500 hover:bg-gray-100',
                  },
                  'Clear',
                ),
            ),
          ),
          React.createElement(
            'div',
            {
              className:
                'px-6 py-4 border-t border-gray-200 flex justify-end gap-3',
            },
            React.createElement(
              'button',
              {
                className:
                  'px-4 py-2 text-sm border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50',
                onClick: function () {
                  setNotesModal(null);
                },
              },
              'Cancel',
            ),
            React.createElement(
              'button',
              {
                className:
                  'px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:bg-gray-400',
                onClick: handleSaveNotes,
                disabled: savingNotes,
              },
              savingNotes ? 'Saving…' : 'Save Notes',
            ),
          ),
        ),
      ),

    // Audit status "Not Required" reason modal
    auditStatusModal &&
      React.createElement(
        'div',
        {
          className:
            'fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-40',
          onClick: function (e) {
            if (e.target === e.currentTarget) {
              setAuditStatusModal(null);
              setAuditStatusDraft('');
            }
          },
        },
        React.createElement(
          'div',
          { className: 'bg-white rounded-xl shadow-2xl w-full max-w-md mx-4' },
          React.createElement(
            'div',
            {
              className:
                'px-6 py-4 border-b border-gray-200 font-semibold text-gray-900',
            },
            'Audit Not Required — ',
            flwNameMap[auditStatusModal] || auditStatusModal,
          ),
          React.createElement(
            'div',
            { className: 'px-6 py-4 space-y-2' },
            React.createElement(
              'p',
              { className: 'text-sm text-gray-600' },
              'Please provide a reason why this yellow-flagged FLW does not require an audit.',
            ),
            React.createElement('textarea', {
              className:
                'w-full border border-gray-300 rounded-lg px-3 py-2 text-sm',
              rows: 3,
              value: auditStatusDraft,
              onChange: function (e) {
                setAuditStatusDraft(e.target.value);
              },
              placeholder:
                'e.g. Flag triggered by data entry error, FLW confirmed compliant…',
              autoFocus: true,
            }),
          ),
          React.createElement(
            'div',
            {
              className:
                'px-6 py-4 border-t border-gray-200 flex justify-end gap-3',
            },
            React.createElement(
              'button',
              {
                className:
                  'px-4 py-2 text-sm border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50',
                onClick: function () {
                  setAuditStatusModal(null);
                  setAuditStatusDraft('');
                },
              },
              'Cancel',
            ),
            React.createElement(
              'button',
              {
                className:
                  'px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:bg-gray-400',
                onClick: handleSaveAuditNotRequired,
                disabled: !auditStatusDraft.trim(),
              },
              'Save',
            ),
          ),
        ),
      ),

    // Conclude run modal
    concludeModal &&
      React.createElement(
        'div',
        {
          className:
            'fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-40',
          onClick: function (e) {
            if (e.target === e.currentTarget) setConcludeModal(false);
          },
        },
        React.createElement(
          'div',
          { className: 'bg-white rounded-xl shadow-2xl w-full max-w-md mx-4' },
          React.createElement(
            'div',
            { className: 'px-6 py-4 bg-green-50 border-b border-green-200' },
            React.createElement(
              'h3',
              { className: 'text-lg font-semibold text-green-900' },
              React.createElement('i', {
                className: 'fa-solid fa-flag-checkered mr-2',
              }),
              'Conclude Audit Run',
            ),
          ),
          React.createElement(
            'div',
            { className: 'px-6 py-5 text-sm text-gray-700 space-y-3' },
            React.createElement(
              'p',
              null,
              'This will mark the run as ',
              React.createElement('strong', null, 'Completed'),
              ' and save current metrics and categories as the baseline for the next run.',
            ),
            React.createElement(
              'p',
              null,
              categorizedCount +
                ' of ' +
                totalFlws +
                ' FLWs have been categorized.',
            ),
            // Completion checklist
            (function () {
              var openTasks = Object.values(taskStates).filter(function (t) {
                return (
                  t.triggered_at &&
                  t.status !== 'closed' &&
                  t.status !== 'completed'
                );
              });
              var redNoTask = enrichedData.filter(function (f) {
                return (
                  f.flags.type === 'red' &&
                  !(
                    taskStates[f.username] &&
                    taskStates[f.username].triggered_at
                  )
                );
              });
              var yellowNotTriaged = enrichedData.filter(function (f) {
                if (f.flags.type !== 'yellow') return false;
                var as = auditStatuses[f.username] || {};
                if (!as.status) return true;
                if (as.status === 'audit_not_required') return !as.reason;
                if (as.status === 'audit_required')
                  return !(
                    taskStates[f.username] &&
                    taskStates[f.username].triggered_at
                  );
                return false;
              });
              var items = [
                {
                  ok: openTasks.length === 0,
                  label:
                    openTasks.length === 0
                      ? 'All triggered tasks resolved'
                      : openTasks.length + ' task(s) still open',
                },
                {
                  ok: redNoTask.length === 0,
                  label:
                    redNoTask.length === 0
                      ? 'All red-flagged FLWs have tasks'
                      : redNoTask.length + ' red-flagged FLW(s) missing a task',
                },
                {
                  ok: yellowNotTriaged.length === 0,
                  label:
                    yellowNotTriaged.length === 0
                      ? 'All yellow-flagged FLWs triaged'
                      : yellowNotTriaged.length +
                        ' yellow-flagged FLW(s) need audit status or task',
                },
              ];
              return React.createElement(
                'div',
                {
                  className:
                    'bg-gray-50 rounded-lg p-3 space-y-1.5 border border-gray-200',
                },
                items.map(function (item, i) {
                  return React.createElement(
                    'div',
                    { key: i, className: 'flex items-center gap-2 text-xs' },
                    React.createElement('i', {
                      className: item.ok
                        ? 'fa-solid fa-circle-check text-green-500'
                        : 'fa-solid fa-circle-xmark text-red-400',
                    }),
                    React.createElement(
                      'span',
                      {
                        className: item.ok
                          ? 'text-gray-600'
                          : 'text-red-700 font-medium',
                      },
                      item.label,
                    ),
                  );
                }),
              );
            })(),
            React.createElement(
              'p',
              { className: 'text-orange-700 font-medium' },
              'This action cannot be undone.',
            ),
          ),
          React.createElement(
            'div',
            {
              className:
                'px-6 py-4 border-t border-gray-200 flex justify-end gap-3',
            },
            React.createElement(
              'button',
              {
                className:
                  'px-4 py-2 text-sm border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50',
                onClick: function () {
                  setConcludeModal(false);
                },
              },
              'Cancel',
            ),
            React.createElement(
              'button',
              {
                className:
                  'px-5 py-2 text-sm bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:bg-gray-400 font-medium',
                onClick: handleConclude,
                disabled: concluding,
              },
              concluding ? 'Concluding…' : 'Conclude Run',
            ),
          ),
        ),
      ),
  );
}
