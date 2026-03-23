/**
 * Funder Dashboard — Chart Rendering
 *
 * Vanilla JS (no build step). Expects window.Chart (Chart.js 4.x) and
 * window.L (Leaflet 1.9.x + MarkerCluster) to be loaded before this file.
 *
 * Exports top-level functions consumed by the SSE client in the template:
 *   renderKPIs(visits, payments, container)
 *   renderImpactHeadline(visits, payments, container)
 *   renderPerformanceTable(visits, payments, container)
 *   renderRecentActivity(visits, payments, container)
 *   renderAlerts(visits, payments, container)
 *   renderVisitsChart(visits)
 *   renderPaymentsChart(payments)
 *   renderForecast(payments, budget, container)
 *   renderMap(visits)
 *   animateCounters()
 *   initFilterBus(allVisits, allPayments, renderCallback)
 *   aggregateForReport(visits, payments, allocations, budget)
 *   prepareForPrint()
 */

/* eslint-disable no-var */
/* global Chart, L */

var OPP_COLORS = [
  '#6366f1',
  '#10b981',
  '#f59e0b',
  '#ef4444',
  '#8b5cf6',
  '#06b6d4',
  '#ec4899',
  '#84cc16',
  '#f97316',
  '#14b8a6',
  '#a855f7',
  '#64748b',
  '#e11d48',
  '#0ea5e9',
  '#22c55e',
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Group a flat array of rows by opp_id, returning { [oppId]: { rows, opp_name, country, delivery_type } }. */
function groupByOpp(rows) {
  var result = {};
  for (var i = 0; i < rows.length; i++) {
    var r = rows[i];
    var id = String(r.opp_id);
    if (!result[id]) {
      result[id] = {
        rows: [],
        opp_name: r.opp_name || 'Opp ' + id,
        country: r.country || '',
        delivery_type: r.delivery_type || '',
      };
    }
    result[id].rows.push(r);
  }
  return result;
}

/** Return ISO week-start (Monday) string for a date.
 *  IMPORTANT: parse as local time (T00:00:00) to avoid UTC offset shifting
 *  the day-of-week calculation (e.g. UTC Monday midnight → local Sunday in US). */
function weekStart(dateStr) {
  if (!dateStr) return null;
  // Normalize: take only the YYYY-MM-DD portion to avoid time/timezone issues
  var datePart = String(dateStr).slice(0, 10);
  var d = new Date(datePart + 'T00:00:00');
  if (isNaN(d.getTime())) return null;
  var day = d.getDay();
  var diff = (day === 0 ? -6 : 1) - day; // Monday
  d.setDate(d.getDate() + diff);
  // Build YYYY-MM-DD manually from local date parts (not toISOString which uses UTC)
  var y = d.getFullYear();
  var m = String(d.getMonth() + 1).padStart(2, '0');
  var dd = String(d.getDate()).padStart(2, '0');
  return y + '-' + m + '-' + dd;
}

/** Get sorted unique week-start strings from an array of date strings. */
function uniqueWeeks(dates) {
  var set = {};
  for (var i = 0; i < dates.length; i++) {
    var ws = weekStart(dates[i]);
    if (ws) set[ws] = true;
  }
  return Object.keys(set).sort();
}

/** Assign a consistent color index for an opp id. */
var _oppColorMap = {};
var _oppColorIdx = 0;
function oppColor(oppId) {
  if (_oppColorMap[oppId] === undefined) {
    _oppColorMap[oppId] = _oppColorIdx++;
  }
  return OPP_COLORS[_oppColorMap[oppId] % OPP_COLORS.length];
}

/** Reset the color map (useful if re-rendering). */
function resetColors() {
  _oppColorMap = {};
  _oppColorIdx = 0;
}

/** Format a number with comma separators. */
function fmtNum(n) {
  if (n == null) return '0';
  return Number(n).toLocaleString('en-US');
}

/** Format a dollar amount. */
function fmtUSD(n) {
  if (n == null) return '$0';
  return (
    '$' +
    Number(n).toLocaleString('en-US', {
      minimumFractionDigits: 0,
      maximumFractionDigits: 0,
    })
  );
}

/** Show an empty-state message inside a container. */
function showEmpty(el, msg) {
  el.innerHTML =
    '<div class="flex items-center justify-center h-full text-gray-400 text-sm">' +
    '<div class="text-center"><i class="fa-solid fa-chart-simple text-3xl mb-2 block"></i>' +
    msg +
    '</div></div>';
}

// ---------------------------------------------------------------------------
// Weekly stats helpers
// ---------------------------------------------------------------------------

/**
 * Compute "this week" and "last week" relative to the max date in the data.
 * Returns { thisWeekStart, lastWeekStart } as ISO date strings.
 */
function referenceWeeks(dates) {
  if (!dates.length) return { thisWeekStart: null, lastWeekStart: null };
  var maxDate = dates[0];
  for (var i = 1; i < dates.length; i++) {
    if (dates[i] > maxDate) maxDate = dates[i];
  }
  var ws = weekStart(maxDate);
  if (!ws) return { thisWeekStart: null, lastWeekStart: null };
  var d = new Date(ws + 'T00:00:00');
  d.setDate(d.getDate() - 7);
  return { thisWeekStart: ws, lastWeekStart: d.toISOString().slice(0, 10) };
}

/** Count approved visits in a specific week (by weekStart). */
function countVisitsInWeek(visits, ws) {
  var count = 0;
  for (var i = 0; i < visits.length; i++) {
    if (
      visits[i].status === 'approved' &&
      weekStart(visits[i].visit_date) === ws
    )
      count++;
  }
  return count;
}

/** Sum USD payments in a specific week (by weekStart of status_modified_date). */
function sumUSDInWeek(payments, ws) {
  var total = 0;
  for (var i = 0; i < payments.length; i++) {
    var dateStr = payments[i].status_modified_date || payments[i].payment_date;
    if (weekStart(dateStr) === ws) {
      total +=
        (parseFloat(payments[i].usd_flw) || 0) +
        (parseFloat(payments[i].usd_org) || 0);
    }
  }
  return total;
}

/** Compute percent change, returning { pct, direction }. */
function trendInfo(thisVal, lastVal) {
  if (lastVal === 0 && thisVal === 0) return { pct: 0, direction: 'flat' };
  if (lastVal === 0) return { pct: 100, direction: 'up' };
  var pct = Math.round(((thisVal - lastVal) / lastVal) * 100);
  if (pct > 0) return { pct: pct, direction: 'up' };
  if (pct < 0) return { pct: Math.abs(pct), direction: 'down' };
  return { pct: 0, direction: 'flat' };
}

/** Render a trend arrow+text span. */
function trendHTML(trend) {
  if (trend.direction === 'up') {
    return (
      '<span class="text-green-600 text-xs font-medium"><i class="fa-solid fa-arrow-up mr-0.5"></i>+' +
      trend.pct +
      '%</span>'
    );
  }
  if (trend.direction === 'down') {
    return (
      '<span class="text-red-600 text-xs font-medium"><i class="fa-solid fa-arrow-down mr-0.5"></i>-' +
      trend.pct +
      '%</span>'
    );
  }
  return '<span class="text-gray-400 text-xs font-medium"><i class="fa-solid fa-minus mr-0.5"></i>0%</span>';
}

/** Count distinct usernames with a visit_date within the last N days of maxDate. */
function activeFLWs(visits, maxDateStr, days) {
  if (!maxDateStr) return 0;
  var maxD = new Date(maxDateStr + 'T00:00:00');
  var cutoff = new Date(maxD);
  cutoff.setDate(cutoff.getDate() - days);
  var set = {};
  for (var i = 0; i < visits.length; i++) {
    if (!visits[i].username || !visits[i].visit_date) continue;
    var vd = new Date(visits[i].visit_date + 'T00:00:00');
    if (vd >= cutoff && vd <= maxD) {
      set[visits[i].username] = true;
    }
  }
  return Object.keys(set).length;
}

/** Find the max visit_date string in a visits array. */
function maxVisitDate(visits) {
  var max = null;
  for (var i = 0; i < visits.length; i++) {
    if (visits[i].visit_date && (!max || visits[i].visit_date > max)) {
      max = visits[i].visit_date;
    }
  }
  return max;
}

// ---------------------------------------------------------------------------
// renderKPIs (enhanced)
// ---------------------------------------------------------------------------

function renderKPIs(visits, payments, container) {
  // Count approved visits
  var approvedCount = 0;
  for (var i = 0; i < visits.length; i++) {
    if (visits[i].status === 'approved') approvedCount++;
  }

  // Sum USD
  var totalUSD = 0;
  for (var i = 0; i < payments.length; i++) {
    totalUSD +=
      (parseFloat(payments[i].usd_flw) || 0) +
      (parseFloat(payments[i].usd_org) || 0);
  }

  // Distinct FLWs (total)
  var flwSet = {};
  for (var i = 0; i < visits.length; i++) {
    if (visits[i].username) flwSet[visits[i].username] = true;
  }
  var totalFLWs = Object.keys(flwSet).length;

  // Weekly stats
  var visitDates = [];
  for (var i = 0; i < visits.length; i++) {
    if (visits[i].visit_date) visitDates.push(visits[i].visit_date);
  }
  var weeks = referenceWeeks(visitDates);
  var thisWeekVisits = weeks.thisWeekStart
    ? countVisitsInWeek(visits, weeks.thisWeekStart)
    : 0;
  var lastWeekVisits = weeks.lastWeekStart
    ? countVisitsInWeek(visits, weeks.lastWeekStart)
    : 0;
  var visitTrend = trendInfo(thisWeekVisits, lastWeekVisits);

  var thisWeekUSD = weeks.thisWeekStart
    ? sumUSDInWeek(payments, weeks.thisWeekStart)
    : 0;
  var lastWeekUSD = weeks.lastWeekStart
    ? sumUSDInWeek(payments, weeks.lastWeekStart)
    : 0;
  var usdTrend = trendInfo(thisWeekUSD, lastWeekUSD);

  // Active FLWs (last 14 days relative to max visit date)
  var maxVD = maxVisitDate(visits);
  var activeCount = activeFLWs(visits, maxVD, 14);

  // Budget utilization
  var budgetTotal =
    typeof fundTotalBudget !== 'undefined' && fundTotalBudget
      ? Number(fundTotalBudget)
      : 0;
  var budgetPct =
    budgetTotal > 0
      ? Math.min(100, Math.round((totalUSD / budgetTotal) * 100))
      : 0;

  var html = '<div class="grid grid-cols-1 md:grid-cols-4 gap-4 mb-4">';

  // Card 1: Approved Visits
  html +=
    '<div class="bg-white rounded-xl shadow-sm p-5 border border-gray-200">' +
    '<div class="flex items-center gap-3 mb-2">' +
    '<div class="w-10 h-10 bg-green-50 rounded-lg flex items-center justify-center">' +
    '<i class="fa-solid fa-check-circle text-green-600"></i></div>' +
    '<div><div class="text-xs text-gray-500 uppercase tracking-wider">Approved Visits</div>' +
    '<div class="text-xl font-bold text-gray-900">' +
    '<span data-animate-value="' +
    approvedCount +
    '" data-animate-prefix="" data-animate-suffix="">0</span>' +
    '</div></div></div>' +
    '<div class="text-xs text-gray-500">This week: ' +
    fmtNum(thisWeekVisits) +
    ' <span class="mx-1">|</span> ' +
    trendHTML(visitTrend) +
    ' vs last week</div></div>';

  // Card 2: USD Distributed
  html +=
    '<div class="bg-white rounded-xl shadow-sm p-5 border border-gray-200">' +
    '<div class="flex items-center gap-3 mb-2">' +
    '<div class="w-10 h-10 bg-indigo-50 rounded-lg flex items-center justify-center">' +
    '<i class="fa-solid fa-dollar-sign text-indigo-600"></i></div>' +
    '<div><div class="text-xs text-gray-500 uppercase tracking-wider">USD Distributed</div>' +
    '<div class="text-xl font-bold text-gray-900">' +
    '<span data-animate-value="' +
    Math.round(totalUSD) +
    '" data-animate-prefix="$" data-animate-suffix="">0</span>' +
    '</div></div></div>' +
    '<div class="text-xs text-gray-500">This week: ' +
    fmtUSD(thisWeekUSD) +
    ' <span class="mx-1">|</span> ' +
    trendHTML(usdTrend) +
    ' vs last week</div></div>';

  // Card 3: Budget Utilization (only if budget is set)
  if (budgetTotal > 0) {
    html +=
      '<div class="bg-white rounded-xl shadow-sm p-5 border border-gray-200">' +
      '<div class="flex items-center gap-3 mb-2">' +
      '<div class="w-10 h-10 bg-amber-50 rounded-lg flex items-center justify-center">' +
      '<i class="fa-solid fa-chart-pie text-amber-600"></i></div>' +
      '<div><div class="text-xs text-gray-500 uppercase tracking-wider">Budget Utilization</div>' +
      '<div class="text-xl font-bold text-gray-900">' +
      '<span data-animate-value="' +
      budgetPct +
      '" data-animate-prefix="" data-animate-suffix="%">0</span>' +
      '</div></div></div>' +
      '<div class="w-full bg-gray-200 rounded-full h-2 mt-1">' +
      '<div class="h-2 rounded-full ' +
      (budgetPct >= 90
        ? 'bg-red-500'
        : budgetPct >= 70
        ? 'bg-amber-500'
        : 'bg-green-500') +
      '" style="width: ' +
      budgetPct +
      '%"></div></div>' +
      '<div class="text-xs text-gray-400 mt-1">' +
      fmtUSD(totalUSD) +
      ' of ' +
      fmtUSD(budgetTotal) +
      '</div></div>';
  } else {
    // Fallback: show countries card if no budget
    var countrySet = {};
    var allOpps = Object.assign({}, groupByOpp(visits), groupByOpp(payments));
    for (var id in allOpps) {
      if (allOpps[id].country) countrySet[allOpps[id].country] = true;
    }
    html +=
      '<div class="bg-white rounded-xl shadow-sm p-5 border border-gray-200">' +
      '<div class="flex items-center gap-3">' +
      '<div class="w-10 h-10 bg-amber-50 rounded-lg flex items-center justify-center">' +
      '<i class="fa-solid fa-globe text-amber-600"></i></div>' +
      '<div><div class="text-xs text-gray-500 uppercase tracking-wider">Countries</div>' +
      '<div class="text-xl font-bold text-gray-900">' +
      '<span data-animate-value="' +
      Object.keys(countrySet).length +
      '" data-animate-prefix="" data-animate-suffix="">0</span>' +
      '</div></div></div></div>';
  }

  // Card 4: Active FLWs
  html +=
    '<div class="bg-white rounded-xl shadow-sm p-5 border border-gray-200">' +
    '<div class="flex items-center gap-3">' +
    '<div class="w-10 h-10 bg-blue-50 rounded-lg flex items-center justify-center">' +
    '<i class="fa-solid fa-users text-blue-600"></i></div>' +
    '<div><div class="text-xs text-gray-500 uppercase tracking-wider">Active FLWs</div>' +
    '<div class="text-xl font-bold text-gray-900">' +
    '<span data-animate-value="' +
    activeCount +
    '" data-animate-prefix="" data-animate-suffix="">0</span>' +
    ' <span class="text-sm font-normal text-gray-400">of ' +
    fmtNum(totalFLWs) +
    '</span></div></div></div></div>';

  html += '</div>';
  container.innerHTML = html;
}

// ---------------------------------------------------------------------------
// renderImpactHeadline
// ---------------------------------------------------------------------------

function renderImpactHeadline(visits, payments, container) {
  if (!container) return;

  // Distinct entity_names (families)
  var entitySet = {};
  for (var i = 0; i < visits.length; i++) {
    if (visits[i].entity_name) entitySet[visits[i].entity_name] = true;
  }
  var familyCount = Object.keys(entitySet).length;

  // Distinct countries
  var countrySet = {};
  var allOpps = Object.assign({}, groupByOpp(visits), groupByOpp(payments));
  for (var id in allOpps) {
    if (allOpps[id].country) countrySet[allOpps[id].country] = true;
  }
  var countryCount = Object.keys(countrySet).length;

  // Distinct FLWs
  var flwSet = {};
  for (var i = 0; i < visits.length; i++) {
    if (visits[i].username) flwSet[visits[i].username] = true;
  }
  var flwCount = Object.keys(flwSet).length;

  // Total USD
  var totalUSD = 0;
  for (var i = 0; i < payments.length; i++) {
    totalUSD +=
      (parseFloat(payments[i].usd_flw) || 0) +
      (parseFloat(payments[i].usd_org) || 0);
  }

  // Approved visits count for cost/visit
  var approvedCount = 0;
  for (var i = 0; i < visits.length; i++) {
    if (visits[i].status === 'approved') approvedCount++;
  }
  var costPerVisit = approvedCount > 0 ? totalUSD / approvedCount : 0;

  // Date range
  var dates = [];
  for (var i = 0; i < visits.length; i++) {
    if (visits[i].visit_date) dates.push(visits[i].visit_date);
  }
  dates.sort();
  var rangeStr = '';
  if (dates.length > 0) {
    var fmtDate = function (ds) {
      var d = new Date(ds);
      if (isNaN(d.getTime())) d = new Date(ds.substring(0, 10) + 'T00:00:00');
      return d.toLocaleDateString('en-US', { month: 'short', year: 'numeric' });
    };
    rangeStr =
      fmtDate(dates[0]) + ' \u2014 ' + fmtDate(dates[dates.length - 1]);
  }

  var html =
    '<div class="bg-white rounded-xl shadow-sm p-6 mb-6 border border-gray-200 text-center">' +
    '<p class="text-lg md:text-xl font-semibold text-brand-deep-purple">' +
    'Your fund has reached <span class="text-2xl font-bold" data-animate-value="' +
    familyCount +
    '" data-animate-prefix="" data-animate-suffix="">0</span> families across ' +
    '<span class="text-2xl font-bold" data-animate-value="' +
    countryCount +
    '" data-animate-prefix="" data-animate-suffix="">0</span> ' +
    (countryCount === 1 ? 'country' : 'countries') +
    ' through <span class="text-2xl font-bold" data-animate-value="' +
    flwCount +
    '" data-animate-prefix="" data-animate-suffix="">0</span> community health workers' +
    '</p>' +
    '<p class="text-sm text-gray-500 mt-2">' +
    '$' +
    costPerVisit.toFixed(2) +
    ' per visit' +
    (rangeStr ? ' <span class="mx-1">|</span> ' + rangeStr : '') +
    '</p></div>';

  container.innerHTML = html;
}

// ---------------------------------------------------------------------------
// renderPerformanceTable
// ---------------------------------------------------------------------------

function renderPerformanceTable(visits, payments, container) {
  if (!container) return;

  var approvedVisits = [];
  for (var i = 0; i < visits.length; i++) {
    if (visits[i].status === 'approved') approvedVisits.push(visits[i]);
  }

  var visitsByOpp = groupByOpp(approvedVisits);
  var paymentsByOpp = groupByOpp(payments);

  // Merge opp sets
  var allOppIds = {};
  var id;
  for (id in visitsByOpp) allOppIds[id] = true;
  for (id in paymentsByOpp) allOppIds[id] = true;
  var oppIds = Object.keys(allOppIds);

  if (oppIds.length === 0) {
    container.innerHTML =
      '<div class="bg-white rounded-xl shadow-sm p-8 mb-6 border border-gray-200 text-center">' +
      '<i class="fa-solid fa-table text-3xl text-gray-300 mb-2 block"></i>' +
      '<p class="text-sm text-gray-500">No allocation data to display.</p></div>';
    return;
  }

  // Find global max visit date for "this week" / "last week"
  var allVisitDates = [];
  for (var i = 0; i < visits.length; i++) {
    if (visits[i].visit_date) allVisitDates.push(visits[i].visit_date);
  }
  var weeks = referenceWeeks(allVisitDates);
  var globalMaxDate = maxVisitDate(visits);

  // Build allocations lookup from template-injected var (if available)
  var allocLookup = {};
  if (typeof fundAllocations !== 'undefined' && fundAllocations) {
    for (var a = 0; a < fundAllocations.length; a++) {
      var alloc = fundAllocations[a];
      if (alloc.opportunity_id) {
        allocLookup[String(alloc.opportunity_id)] = alloc;
      }
    }
  }

  // Compute per-opp stats
  var rows = [];
  for (var idx = 0; idx < oppIds.length; idx++) {
    id = oppIds[idx];
    var vOpp = visitsByOpp[id] || {
      rows: [],
      opp_name: '',
      country: '',
      delivery_type: '',
    };
    var pOpp = paymentsByOpp[id] || {
      rows: [],
      opp_name: '',
      country: '',
      delivery_type: '',
    };
    var oppName = vOpp.opp_name || pOpp.opp_name || 'Opportunity ' + id;
    var country = vOpp.country || pOpp.country || '';
    var alloc = allocLookup[id] || {};
    var lloName = alloc.llo_name || '';

    // Visit count
    var visitCount = vOpp.rows.length;

    // USD total
    var usdTotal = 0;
    for (var j = 0; j < pOpp.rows.length; j++) {
      usdTotal +=
        (parseFloat(pOpp.rows[j].usd_flw) || 0) +
        (parseFloat(pOpp.rows[j].usd_org) || 0);
    }
    var costPerVisit = visitCount > 0 ? usdTotal / visitCount : 0;

    // Active FLWs for this opp (last 14 days)
    var oppMaxDate = null;
    for (var j = 0; j < vOpp.rows.length; j++) {
      if (
        vOpp.rows[j].visit_date &&
        (!oppMaxDate || vOpp.rows[j].visit_date > oppMaxDate)
      ) {
        oppMaxDate = vOpp.rows[j].visit_date;
      }
    }
    var oppActive = activeFLWs(vOpp.rows, globalMaxDate, 14);

    // This week / last week for this opp
    var twVisits = weeks.thisWeekStart
      ? countVisitsInWeek(vOpp.rows, weeks.thisWeekStart)
      : 0;
    var lwVisits = weeks.lastWeekStart
      ? countVisitsInWeek(vOpp.rows, weeks.lastWeekStart)
      : 0;
    var trend = trendInfo(twVisits, lwVisits);

    // Status: based on most recent visit relative to global max date
    var statusColor = '#ef4444'; // red by default
    if (oppMaxDate && globalMaxDate) {
      var daysSince = Math.floor(
        (new Date(globalMaxDate + 'T00:00:00') -
          new Date(oppMaxDate + 'T00:00:00')) /
          86400000,
      );
      if (daysSince <= 7) statusColor = '#10b981';
      else if (daysSince <= 14) statusColor = '#f59e0b';
    }

    // Sparkline data: last 8 weeks of approved visits
    var sparkWeeks = uniqueWeeks(
      vOpp.rows.map(function (r) {
        return r.visit_date;
      }),
    );
    // Take only the last 8 weeks
    if (sparkWeeks.length > 8)
      sparkWeeks = sparkWeeks.slice(sparkWeeks.length - 8);
    var sparkData = [];
    for (var w = 0; w < sparkWeeks.length; w++) {
      var wCount = 0;
      for (var j = 0; j < vOpp.rows.length; j++) {
        if (weekStart(vOpp.rows[j].visit_date) === sparkWeeks[w]) wCount++;
      }
      sparkData.push(wCount);
    }

    rows.push({
      id: id,
      oppName: oppName,
      lloName: lloName,
      country: country,
      visitCount: visitCount,
      sparkData: sparkData,
      usdTotal: usdTotal,
      costPerVisit: costPerVisit,
      activeFLWs: oppActive,
      trend: trend,
      statusColor: statusColor,
    });
  }

  // Sort by approved visits descending
  rows.sort(function (a, b) {
    return b.visitCount - a.visitCount;
  });

  // Build table HTML
  var html =
    '<div class="bg-white rounded-xl shadow-sm border border-gray-200 mb-6 overflow-x-auto">' +
    '<table class="min-w-full divide-y divide-gray-200">' +
    '<thead><tr>' +
    '<th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Opportunity</th>' +
    '<th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Visits</th>' +
    '<th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">USD Distributed</th>' +
    '<th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Active FLWs</th>' +
    '<th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Trend</th>' +
    '<th class="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider">Status</th>' +
    '</tr></thead><tbody class="divide-y divide-gray-100">';

  for (var r = 0; r < rows.length; r++) {
    var row = rows[r];
    html += '<tr>';

    // Opportunity name + llo_name + country
    html +=
      '<td class="px-4 py-3 text-sm">' +
      '<div class="font-medium text-gray-900">' +
      row.oppName +
      '</div>' +
      (row.lloName
        ? '<div class="text-xs text-gray-400">' + row.lloName + '</div>'
        : '') +
      (row.country
        ? '<div class="text-xs text-gray-400">' + row.country + '</div>'
        : '') +
      '</td>';

    // Visits + sparkline placeholder
    html +=
      '<td class="px-4 py-3 text-sm">' +
      '<div class="font-medium text-gray-900">' +
      fmtNum(row.visitCount) +
      '</div>' +
      '<canvas id="spark-' +
      row.id +
      '" width="60" height="24" class="mt-1"></canvas>' +
      '</td>';

    // USD Distributed + cost/visit
    html +=
      '<td class="px-4 py-3 text-sm">' +
      '<div class="font-medium text-gray-900">' +
      fmtUSD(row.usdTotal) +
      '</div>' +
      '<div class="text-xs text-gray-400">$' +
      row.costPerVisit.toFixed(2) +
      '/visit</div>' +
      '</td>';

    // Active FLWs
    html +=
      '<td class="px-4 py-3 text-sm font-medium text-gray-900">' +
      fmtNum(row.activeFLWs) +
      '</td>';

    // Trend
    html += '<td class="px-4 py-3 text-sm">' + trendHTML(row.trend) + '</td>';

    // Status dot
    html +=
      '<td class="px-4 py-3 text-center">' +
      '<span class="inline-block w-2 h-2 rounded-full" style="background-color: ' +
      row.statusColor +
      '"></span>' +
      '</td>';

    html += '</tr>';
  }

  html += '</tbody></table></div>';
  container.innerHTML = html;

  // Render sparklines after DOM is updated
  for (var r = 0; r < rows.length; r++) {
    _renderSparkline('spark-' + rows[r].id, rows[r].sparkData);
  }
}

/** Render a tiny sparkline chart on a canvas. */
function _renderSparkline(canvasId, data) {
  var canvas = document.getElementById(canvasId);
  if (!canvas || !data.length) return;

  new Chart(canvas, {
    type: 'line',
    data: {
      labels: data.map(function (_, i) {
        return i;
      }),
      datasets: [
        {
          data: data,
          borderColor: '#6366f1',
          borderWidth: 1.5,
          pointRadius: 0,
          fill: false,
          tension: 0.3,
        },
      ],
    },
    options: {
      responsive: false,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { enabled: false },
      },
      scales: {
        x: { display: false },
        y: { display: false },
      },
      animation: false,
    },
  });
}

// ---------------------------------------------------------------------------
// renderVisitsChart
// ---------------------------------------------------------------------------

function renderVisitsChart(visits) {
  var canvas = document.getElementById('visits-chart');
  if (!canvas) return;
  var wrapper = canvas.parentElement;

  var approved = visits.filter(function (v) {
    return v.status === 'approved';
  });
  if (approved.length === 0) {
    showEmpty(wrapper, 'No visit data available');
    canvas.style.display = 'none';
    return;
  }

  var byOpp = groupByOpp(approved);
  var oppIds = Object.keys(byOpp);

  // Collect all visit dates to determine weeks
  var allDates = approved.map(function (v) {
    return v.visit_date;
  });
  var weeks = uniqueWeeks(allDates);

  // Build datasets
  var datasets = [];
  for (var idx = 0; idx < oppIds.length; idx++) {
    var id = oppIds[idx];
    var opp = byOpp[id];
    var color = oppColor(id);

    // Count visits per week
    var weekCounts = {};
    for (var j = 0; j < opp.rows.length; j++) {
      var ws = weekStart(opp.rows[j].visit_date);
      if (ws) weekCounts[ws] = (weekCounts[ws] || 0) + 1;
    }

    var data = weeks.map(function (w) {
      return weekCounts[w] || 0;
    });
    datasets.push({
      label: opp.opp_name,
      data: data,
      backgroundColor: color,
      borderColor: color,
      borderWidth: 1,
    });
  }

  new Chart(canvas, {
    type: 'bar',
    data: {
      labels: weeks,
      datasets: datasets,
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        title: {
          display: true,
          text: 'Approved Visits by Week',
          font: { size: 14 },
        },
        legend: { position: 'bottom', labels: { boxWidth: 12, padding: 12 } },
        tooltip: {
          callbacks: {
            title: function (items) {
              return 'Week of ' + items[0].label;
            },
          },
        },
      },
      scales: {
        x: {
          stacked: true,
          title: { display: true, text: 'Week' },
          ticks: {
            maxRotation: 45,
            callback: function (val, idx) {
              var label = this.getLabelForValue(val);
              // Show abbreviated date
              var d = new Date(label + 'T00:00:00');
              if (isNaN(d.getTime())) return label;
              return d.toLocaleDateString('en-US', {
                month: 'short',
                day: 'numeric',
              });
            },
          },
        },
        y: {
          stacked: true,
          beginAtZero: true,
          title: { display: true, text: 'Visits' },
          ticks: { precision: 0 },
        },
      },
    },
  });
}

// ---------------------------------------------------------------------------
// renderPaymentsChart
// ---------------------------------------------------------------------------

function renderPaymentsChart(payments) {
  var canvas = document.getElementById('payments-chart');
  if (!canvas) return;
  var wrapper = canvas.parentElement;

  if (payments.length === 0) {
    showEmpty(wrapper, 'No payment data available');
    canvas.style.display = 'none';
    return;
  }

  var byOpp = groupByOpp(payments);
  var oppIds = Object.keys(byOpp);

  // Use status_modified_date or payment_date
  var allDates = payments
    .map(function (p) {
      return p.status_modified_date || p.payment_date;
    })
    .filter(Boolean);
  var weeks = uniqueWeeks(allDates);

  var datasets = [];
  for (var idx = 0; idx < oppIds.length; idx++) {
    var id = oppIds[idx];
    var opp = byOpp[id];
    var color = oppColor(id);

    // Sum per week
    var weekSums = {};
    for (var j = 0; j < opp.rows.length; j++) {
      var dateStr =
        opp.rows[j].status_modified_date || opp.rows[j].payment_date;
      var ws = weekStart(dateStr);
      if (ws) {
        var amt =
          (parseFloat(opp.rows[j].usd_flw) || 0) +
          (parseFloat(opp.rows[j].usd_org) || 0);
        weekSums[ws] = (weekSums[ws] || 0) + amt;
      }
    }

    // Cumulative
    var cumulative = [];
    var running = 0;
    for (var w = 0; w < weeks.length; w++) {
      running += weekSums[weeks[w]] || 0;
      cumulative.push(Math.round(running));
    }

    datasets.push({
      label: opp.opp_name,
      data: cumulative,
      borderColor: color,
      backgroundColor: color + '20', // 12% opacity
      fill: true,
      tension: 0.3,
      pointRadius: 2,
      pointHoverRadius: 5,
    });
  }

  new Chart(canvas, {
    type: 'line',
    data: {
      labels: weeks,
      datasets: datasets,
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        title: {
          display: true,
          text: 'Cumulative Payments (USD)',
          font: { size: 14 },
        },
        legend: { position: 'bottom', labels: { boxWidth: 12, padding: 12 } },
        tooltip: {
          callbacks: {
            label: function (ctx) {
              return ctx.dataset.label + ': $' + fmtNum(ctx.raw);
            },
          },
        },
      },
      scales: {
        x: {
          title: { display: true, text: 'Week' },
          ticks: {
            maxRotation: 45,
            callback: function (val, idx) {
              var label = this.getLabelForValue(val);
              var d = new Date(label + 'T00:00:00');
              if (isNaN(d.getTime())) return label;
              return d.toLocaleDateString('en-US', {
                month: 'short',
                day: 'numeric',
              });
            },
          },
        },
        y: {
          beginAtZero: true,
          title: { display: true, text: 'USD' },
          ticks: {
            callback: function (val) {
              return '$' + fmtNum(val);
            },
          },
        },
      },
    },
  });
}

// ---------------------------------------------------------------------------
// renderMap
// ---------------------------------------------------------------------------

function renderMap(visits) {
  var mapDiv = document.getElementById('visits-map');
  if (!mapDiv) return;
  var wrapper = mapDiv.parentElement;

  // Filter to rows with valid locations
  var located = [];
  for (var i = 0; i < visits.length; i++) {
    var loc = visits[i].location;
    if (!loc || typeof loc !== 'string') continue;
    var parts = loc.trim().split(/\s+/);
    if (parts.length < 2) continue;
    var lat = parseFloat(parts[0]);
    var lon = parseFloat(parts[1]);
    if (isNaN(lat) || isNaN(lon) || (lat === 0 && lon === 0)) continue;
    located.push({ row: visits[i], lat: lat, lon: lon });
  }

  if (located.length === 0) {
    showEmpty(wrapper, 'No location data available');
    mapDiv.style.display = 'none';
    return;
  }

  // Initialize map
  var map = L.map(mapDiv).setView([0, 0], 2);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution:
      '&copy; <a href="https://openstreetmap.org/copyright">OpenStreetMap</a>',
    maxZoom: 18,
  }).addTo(map);

  // Marker cluster with fallback
  var clusterGroup;
  if (typeof L.markerClusterGroup === 'function') {
    clusterGroup = L.markerClusterGroup({ maxClusterRadius: 40 });
  } else {
    clusterGroup = L.layerGroup();
  }

  // Pre-compute density for each point (count neighbors within 0.01 degrees)
  // Skip density computation for very large datasets to avoid O(n^2) perf hit
  var densities = [];
  var useDensity = located.length <= 5000;
  if (useDensity) {
    for (var i = 0; i < located.length; i++) {
      var neighbors = 0;
      for (var j = 0; j < located.length; j++) {
        if (i === j) continue;
        var dLat = Math.abs(located[i].lat - located[j].lat);
        var dLon = Math.abs(located[i].lon - located[j].lon);
        if (dLat <= 0.01 && dLon <= 0.01) neighbors++;
      }
      densities.push(neighbors);
    }
  } else {
    for (var i = 0; i < located.length; i++) densities.push(0);
  }
  var maxDensity = 1;
  for (var i = 0; i < densities.length; i++) {
    if (densities[i] > maxDensity) maxDensity = densities[i];
  }

  var bounds = [];
  for (var i = 0; i < located.length; i++) {
    var pt = located[i];
    var color = oppColor(String(pt.row.opp_id));
    // Density-based radius (6-12px) and opacity (lower for denser areas)
    var densityRatio = densities[i] / maxDensity;
    var radius = 6 + Math.round(densityRatio * 6);
    var fillOpacity = 0.6 - densityRatio * 0.35; // 0.6 to 0.25
    var marker = L.circleMarker([pt.lat, pt.lon], {
      radius: radius,
      fillColor: color,
      color: color,
      weight: 1,
      opacity: 0.8,
      fillOpacity: fillOpacity,
    });

    var visitDate = pt.row.visit_date
      ? new Date(pt.row.visit_date).toLocaleDateString()
      : '';
    marker.bindPopup(
      '<div class="text-sm">' +
        '<div class="font-medium">' +
        (pt.row.entity_name || 'Unknown') +
        '</div>' +
        '<div class="text-gray-500">' +
        (pt.row.opp_name || '') +
        '</div>' +
        (visitDate
          ? '<div class="text-gray-400 text-xs">' + visitDate + '</div>'
          : '') +
        '</div>',
    );

    clusterGroup.addLayer(marker);
    bounds.push([pt.lat, pt.lon]);
  }

  clusterGroup.addTo(map);

  if (bounds.length > 0) {
    map.fitBounds(bounds, { padding: [30, 30], maxZoom: 15 });
  }

  // Fix Leaflet map rendering in hidden containers
  setTimeout(function () {
    map.invalidateSize();
  }, 100);
}

// ---------------------------------------------------------------------------
// renderRecentActivity
// ---------------------------------------------------------------------------

function renderRecentActivity(visits, payments, container) {
  if (!container) return;

  var maxVD = maxVisitDate(visits);
  if (!maxVD) {
    container.innerHTML = '';
    return;
  }

  var maxD = new Date(maxVD + 'T00:00:00');
  var cutoff = new Date(maxD);
  cutoff.setDate(cutoff.getDate() - 7);

  // Visits in last 7 days
  var recentVisits = 0;
  var recentOppIds = {};
  var recentFLWs = {};
  for (var i = 0; i < visits.length; i++) {
    if (!visits[i].visit_date) continue;
    var vd = new Date(visits[i].visit_date + 'T00:00:00');
    if (vd >= cutoff && vd <= maxD) {
      if (visits[i].status === 'approved') recentVisits++;
      if (visits[i].opp_id) recentOppIds[visits[i].opp_id] = true;
      if (visits[i].username) recentFLWs[visits[i].username] = true;
    }
  }

  // Payments in last 7 days
  var recentUSD = 0;
  for (var i = 0; i < payments.length; i++) {
    var dateStr = payments[i].status_modified_date || payments[i].payment_date;
    if (!dateStr) continue;
    var pd = new Date(dateStr + 'T00:00:00');
    if (pd >= cutoff && pd <= maxD) {
      recentUSD +=
        (parseFloat(payments[i].usd_flw) || 0) +
        (parseFloat(payments[i].usd_org) || 0);
    }
  }

  var oppCount = Object.keys(recentOppIds).length;
  var flwCount = Object.keys(recentFLWs).length;

  if (recentVisits === 0 && recentUSD === 0) {
    container.innerHTML = '';
    return;
  }

  var parts = [];
  if (recentVisits > 0) {
    parts.push(
      fmtNum(recentVisits) +
        ' visits across ' +
        oppCount +
        ' opp' +
        (oppCount !== 1 ? 's' : ''),
    );
  }
  if (recentUSD > 0) {
    parts.push(fmtUSD(Math.round(recentUSD)) + ' distributed');
  }
  if (flwCount > 0) {
    parts.push(fmtNum(flwCount) + ' FLWs active');
  }

  container.innerHTML =
    '<div class="bg-indigo-50 rounded-xl px-5 py-3 mb-4 flex items-center gap-3">' +
    '<i class="fa-solid fa-chart-bar text-indigo-500"></i>' +
    '<span class="text-sm font-medium text-indigo-900">' +
    'Last 7 days: ' +
    parts.join(' | ') +
    '</span></div>';
}

// ---------------------------------------------------------------------------
// renderAlerts
// ---------------------------------------------------------------------------

function renderAlerts(visits, payments, container) {
  if (!container) return;

  var maxVD = maxVisitDate(visits);
  if (!maxVD) {
    container.innerHTML = '';
    return;
  }

  var maxD = new Date(maxVD + 'T00:00:00');
  var byOpp = groupByOpp(visits);
  var oppIds = Object.keys(byOpp);

  // Compute reference weeks
  var allVisitDates = [];
  for (var i = 0; i < visits.length; i++) {
    if (visits[i].visit_date) allVisitDates.push(visits[i].visit_date);
  }
  var weeks = referenceWeeks(allVisitDates);

  var alerts = [];

  for (var idx = 0; idx < oppIds.length; idx++) {
    var id = oppIds[idx];
    var opp = byOpp[id];
    var oppName = opp.opp_name;

    // Find max visit date for this opp
    var oppMaxDate = null;
    for (var j = 0; j < opp.rows.length; j++) {
      if (
        opp.rows[j].visit_date &&
        (!oppMaxDate || opp.rows[j].visit_date > oppMaxDate)
      ) {
        oppMaxDate = opp.rows[j].visit_date;
      }
    }

    // Red alert: no visits in 14+ days
    if (oppMaxDate) {
      var daysSince = Math.floor(
        (maxD - new Date(oppMaxDate + 'T00:00:00')) / 86400000,
      );
      if (daysSince >= 14) {
        alerts.push({
          level: 'red',
          icon: 'fa-circle-exclamation',
          bg: 'bg-red-50',
          text: oppName + ' has had no visits in ' + daysSince + ' days',
          sort: 0,
        });
        continue; // skip trend checks for inactive opps
      }
    }

    // Weekly trend checks
    if (weeks.thisWeekStart && weeks.lastWeekStart) {
      var twVisits = countVisitsInWeek(opp.rows, weeks.thisWeekStart);
      var lwVisits = countVisitsInWeek(opp.rows, weeks.lastWeekStart);

      if (lwVisits > 0) {
        var changePct = Math.round(((twVisits - lwVisits) / lwVisits) * 100);

        // Yellow alert: dropped >50%
        if (changePct <= -50) {
          alerts.push({
            level: 'yellow',
            icon: 'fa-triangle-exclamation',
            bg: 'bg-amber-50',
            text:
              oppName +
              ' visits dropped ' +
              Math.abs(changePct) +
              '% this week',
            sort: 1,
          });
        }
        // Green alert: increased >25%
        else if (changePct >= 25) {
          alerts.push({
            level: 'green',
            icon: 'fa-circle-check',
            bg: 'bg-green-50',
            text: oppName + ' is growing: +' + changePct + '% visits this week',
            sort: 2,
          });
        }
      }
    }
  }

  if (alerts.length === 0) {
    container.innerHTML = '';
    return;
  }

  // Sort: red first, then yellow, then green. Limit to 5.
  alerts.sort(function (a, b) {
    return a.sort - b.sort;
  });
  if (alerts.length > 5) alerts = alerts.slice(0, 5);

  var colorMap = {
    red: 'text-red-600',
    yellow: 'text-amber-600',
    green: 'text-green-600',
  };

  var html = '<div class="space-y-2 mb-6">';
  for (var i = 0; i < alerts.length; i++) {
    var a = alerts[i];
    html +=
      '<div class="' +
      a.bg +
      ' rounded-xl px-5 py-3 flex items-center gap-3">' +
      '<i class="fa-solid ' +
      a.icon +
      ' ' +
      colorMap[a.level] +
      '"></i>' +
      '<span class="text-sm font-medium text-gray-800">' +
      a.text +
      '</span>' +
      '</div>';
  }
  html += '</div>';
  container.innerHTML = html;
}

// ---------------------------------------------------------------------------
// renderForecast — Delivery Pace Chart (#2)
// ---------------------------------------------------------------------------

/**
 * Render a delivery pace forecast chart showing actual cumulative spend
 * plus a projected trajectory line. Positive framing: "On track to deliver
 * full impact by {date}".
 *
 * @param {Array} payments - flat array of payment objects
 * @param {number|null} budget - total fund budget (null if not set)
 * @param {HTMLElement} container - DOM element to render into
 */
function renderForecast(payments, budget, container) {
  if (!container) return;
  if (!payments || payments.length === 0) {
    container.innerHTML =
      '<div class="bg-white rounded-xl shadow-sm p-6 border border-gray-200">' +
      '<div class="flex items-center justify-center h-40 text-gray-400 text-sm">' +
      '<div class="text-center"><i class="fa-solid fa-chart-line text-3xl mb-2 block"></i>' +
      'Not enough data to forecast delivery pace</div></div></div>';
    return;
  }

  // Sum all payments per week
  var allDates = payments
    .map(function (p) {
      return p.status_modified_date || p.payment_date;
    })
    .filter(Boolean);
  var weeks = uniqueWeeks(allDates);
  if (weeks.length < 2) {
    container.innerHTML =
      '<div class="bg-white rounded-xl shadow-sm p-6 border border-gray-200">' +
      '<div class="flex items-center justify-center h-40 text-gray-400 text-sm">' +
      '<div class="text-center"><i class="fa-solid fa-chart-line text-3xl mb-2 block"></i>' +
      'Need at least 2 weeks of data to forecast</div></div></div>';
    return;
  }

  var weeklyTotals = {};
  for (var i = 0; i < payments.length; i++) {
    var dateStr = payments[i].status_modified_date || payments[i].payment_date;
    var ws = weekStart(dateStr);
    if (ws) {
      var amt =
        (parseFloat(payments[i].usd_flw) || 0) +
        (parseFloat(payments[i].usd_org) || 0);
      weeklyTotals[ws] = (weeklyTotals[ws] || 0) + amt;
    }
  }

  // Build cumulative actual spend
  var cumulative = [];
  var running = 0;
  for (var w = 0; w < weeks.length; w++) {
    running += weeklyTotals[weeks[w]] || 0;
    cumulative.push(Math.round(running));
  }
  var totalSpent = cumulative[cumulative.length - 1] || 0;

  // Linear regression on last 8 weeks (or all if fewer)
  var regressN = Math.min(8, cumulative.length);
  var regressStart = cumulative.length - regressN;
  var sumX = 0,
    sumY = 0,
    sumXY = 0,
    sumX2 = 0;
  for (var i = 0; i < regressN; i++) {
    sumX += i;
    sumY += cumulative[regressStart + i];
    sumXY += i * cumulative[regressStart + i];
    sumX2 += i * i;
  }
  var slope =
    (regressN * sumXY - sumX * sumY) / (regressN * sumX2 - sumX * sumX);
  if (isNaN(slope) || slope <= 0) slope = 0;

  // Project forward (weekly slope) to budget completion or 26 more weeks
  var projectedWeeks = [];
  var projectedValues = [];
  var lastWeekDate = new Date(weeks[weeks.length - 1] + 'T00:00:00');
  var projectedCompletion = null;
  var maxProjectWeeks =
    budget && budget > totalSpent
      ? Math.ceil((budget - totalSpent) / Math.max(slope, 1)) + 4
      : 26;
  maxProjectWeeks = Math.min(maxProjectWeeks, 52); // cap at 1 year

  for (var p = 1; p <= maxProjectWeeks; p++) {
    var nextDate = new Date(lastWeekDate);
    nextDate.setDate(nextDate.getDate() + 7 * p);
    var projWeek = nextDate.toISOString().slice(0, 10);
    var projValue = Math.round(totalSpent + slope * p);
    projectedWeeks.push(projWeek);
    projectedValues.push(projValue);
    if (budget && projValue >= budget && !projectedCompletion) {
      projectedCompletion = projWeek;
    }
  }

  // Headline
  var headlineText = '';
  if (budget && budget > 0) {
    if (totalSpent >= budget) {
      headlineText =
        'Full impact delivered! ' + fmtUSD(totalSpent) + ' distributed.';
    } else if (slope > 0 && projectedCompletion) {
      var compDate = new Date(projectedCompletion + 'T00:00:00');
      var compStr = compDate.toLocaleDateString('en-US', {
        month: 'long',
        year: 'numeric',
      });
      headlineText = 'On track to deliver full impact by ' + compStr;
    } else if (slope === 0) {
      headlineText =
        'Delivery pace is steady — ' +
        fmtUSD(totalSpent) +
        ' distributed so far';
    } else {
      headlineText = fmtUSD(totalSpent) + ' impact delivered so far';
    }
  } else {
    headlineText = fmtUSD(totalSpent) + ' total impact delivered';
  }

  // Build chart container
  container.innerHTML =
    '<div class="bg-white rounded-xl shadow-sm p-6 border border-gray-200">' +
    '<div class="text-center mb-4">' +
    '<div class="text-lg font-semibold text-brand-deep-purple">' +
    '<i class="fa-solid fa-rocket mr-2 text-indigo-500"></i>' +
    headlineText +
    '</div>' +
    (slope > 0
      ? '<div class="text-xs text-gray-500 mt-1">~' +
        fmtUSD(Math.round(slope)) +
        ' per week delivery pace</div>'
      : '') +
    '</div>' +
    '<canvas id="forecast-chart" style="height: 280px;"></canvas>' +
    '</div>';

  // Render chart
  var canvas = document.getElementById('forecast-chart');
  if (!canvas) return;

  // Combine labels: actual weeks + projected weeks
  var allLabels = weeks.concat(projectedWeeks);
  // Actual data: fill projected portion with null
  var actualData = cumulative.concat(
    projectedWeeks.map(function () {
      return null;
    }),
  );
  // Projected data: null for actuals, then connect from last actual
  var projData = weeks.map(function () {
    return null;
  });
  projData[projData.length - 1] = totalSpent; // connect point
  projData = projData.concat(projectedValues);

  var datasets = [
    {
      label: 'Impact Delivered',
      data: actualData,
      borderColor: '#6366f1',
      backgroundColor: '#6366f120',
      fill: true,
      tension: 0.3,
      pointRadius: 3,
      pointHoverRadius: 6,
      borderWidth: 2,
    },
    {
      label: 'Projected Pace',
      data: projData,
      borderColor: '#6366f1',
      borderDash: [8, 4],
      backgroundColor: 'transparent',
      tension: 0.3,
      pointRadius: 0,
      borderWidth: 2,
    },
  ];

  // Budget line
  if (budget && budget > 0) {
    datasets.push({
      label: 'Total Budget',
      data: allLabels.map(function () {
        return budget;
      }),
      borderColor: '#10b981',
      borderDash: [4, 4],
      backgroundColor: 'transparent',
      pointRadius: 0,
      borderWidth: 1.5,
    });
  }

  new Chart(canvas, {
    type: 'line',
    data: { labels: allLabels, datasets: datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        title: {
          display: true,
          text: 'Delivery Pace & Forecast',
          font: { size: 14 },
        },
        legend: { position: 'bottom', labels: { boxWidth: 12, padding: 12 } },
        tooltip: {
          callbacks: {
            label: function (ctx) {
              return ctx.dataset.label + ': ' + fmtUSD(ctx.raw);
            },
            title: function (items) {
              var d = new Date(items[0].label + 'T00:00:00');
              if (isNaN(d.getTime())) return items[0].label;
              return (
                'Week of ' +
                d.toLocaleDateString('en-US', {
                  month: 'short',
                  day: 'numeric',
                  year: 'numeric',
                })
              );
            },
          },
        },
      },
      scales: {
        x: {
          title: { display: true, text: 'Week' },
          ticks: {
            maxRotation: 45,
            maxTicksLimit: 20,
            callback: function (val) {
              var label = this.getLabelForValue(val);
              var d = new Date(label + 'T00:00:00');
              if (isNaN(d.getTime())) return label;
              return d.toLocaleDateString('en-US', {
                month: 'short',
                day: 'numeric',
              });
            },
          },
        },
        y: {
          beginAtZero: true,
          title: { display: true, text: 'USD Distributed' },
          ticks: {
            callback: function (val) {
              return '$' + fmtNum(val);
            },
          },
        },
      },
    },
  });
}

// ---------------------------------------------------------------------------
// Filter Bus (#3a-b) — Cross-chart filtering
// ---------------------------------------------------------------------------

/**
 * Initialize the filter bus. Returns an object with methods to apply/clear filters.
 * All render functions are stateless and accept data arrays, so filtering happens
 * at the data level before calling them.
 *
 * @param {Array} allVisits - unfiltered visits array
 * @param {Array} allPayments - unfiltered payments array
 * @param {Function} renderCallback - function(filteredVisits, filteredPayments) to re-render all charts
 * @returns {Object} { applyFilters(filters), clearFilters(), getAvailableFilters() }
 */
function initFilterBus(allVisits, allPayments, renderCallback) {
  var currentFilters = {
    deliveryTypes: [], // empty = all
    countries: [], // empty = all
    dateFrom: null,
    dateTo: null,
    oppIds: [], // empty = all (used by cross-chart click)
  };

  function getAvailableFilters() {
    var deliveryTypes = {};
    var countries = {};
    function collect(rows) {
      for (var i = 0; i < rows.length; i++) {
        if (rows[i].delivery_type) deliveryTypes[rows[i].delivery_type] = true;
        if (rows[i].country) countries[rows[i].country] = true;
      }
    }
    collect(allVisits);
    collect(allPayments);
    return {
      deliveryTypes: Object.keys(deliveryTypes).sort(),
      countries: Object.keys(countries).sort(),
    };
  }

  function filterRows(rows, dateField) {
    return rows.filter(function (r) {
      if (
        currentFilters.deliveryTypes.length > 0 &&
        currentFilters.deliveryTypes.indexOf(r.delivery_type) === -1
      )
        return false;
      if (
        currentFilters.countries.length > 0 &&
        currentFilters.countries.indexOf(r.country) === -1
      )
        return false;
      if (
        currentFilters.oppIds.length > 0 &&
        currentFilters.oppIds.indexOf(String(r.opp_id)) === -1
      )
        return false;
      var dateVal = r[dateField];
      if (dateVal) {
        if (currentFilters.dateFrom && dateVal < currentFilters.dateFrom)
          return false;
        if (currentFilters.dateTo && dateVal > currentFilters.dateTo)
          return false;
      }
      return true;
    });
  }

  function applyFilters(filters) {
    if (filters) {
      if (filters.deliveryTypes !== undefined)
        currentFilters.deliveryTypes = filters.deliveryTypes;
      if (filters.countries !== undefined)
        currentFilters.countries = filters.countries;
      if (filters.dateFrom !== undefined)
        currentFilters.dateFrom = filters.dateFrom;
      if (filters.dateTo !== undefined) currentFilters.dateTo = filters.dateTo;
      if (filters.oppIds !== undefined) currentFilters.oppIds = filters.oppIds;
    }
    var filteredVisits = filterRows(allVisits, 'visit_date');
    var filteredPayments = filterRows(
      allPayments,
      currentFilters.dateFrom ? 'status_modified_date' : 'status_modified_date',
    );
    resetColors();
    renderCallback(filteredVisits, filteredPayments);
  }

  function clearFilters() {
    currentFilters = {
      deliveryTypes: [],
      countries: [],
      dateFrom: null,
      dateTo: null,
      oppIds: [],
    };
    resetColors();
    renderCallback(allVisits, allPayments);
  }

  function getCurrentFilters() {
    return JSON.parse(JSON.stringify(currentFilters));
  }

  function isFiltered() {
    return (
      currentFilters.deliveryTypes.length > 0 ||
      currentFilters.countries.length > 0 ||
      currentFilters.oppIds.length > 0 ||
      currentFilters.dateFrom !== null ||
      currentFilters.dateTo !== null
    );
  }

  return {
    applyFilters: applyFilters,
    clearFilters: clearFilters,
    getAvailableFilters: getAvailableFilters,
    getCurrentFilters: getCurrentFilters,
    isFiltered: isFiltered,
  };
}

// ---------------------------------------------------------------------------
// aggregateForReport — Summarize data for AI report generation (#1)
// ---------------------------------------------------------------------------

/**
 * Aggregate visit/payment data into summary statistics for the AI report.
 * Returns a compact object (few KB) suitable for POSTing to the AI endpoint.
 */
function aggregateForReport(visits, payments, allocations, budget) {
  var approved = visits.filter(function (v) {
    return v.status === 'approved';
  });
  var byOpp = groupByOpp(approved);
  var payByOpp = groupByOpp(payments);
  var oppIds = Object.keys(Object.assign({}, byOpp, payByOpp));

  // Per-opportunity summary
  var oppSummaries = [];
  for (var i = 0; i < oppIds.length; i++) {
    var id = oppIds[i];
    var vRows = byOpp[id] ? byOpp[id].rows : [];
    var pRows = payByOpp[id] ? payByOpp[id].rows : [];
    var totalUSD = 0;
    for (var j = 0; j < pRows.length; j++) {
      totalUSD +=
        (parseFloat(pRows[j].usd_flw) || 0) +
        (parseFloat(pRows[j].usd_org) || 0);
    }
    var flws = {};
    for (var j = 0; j < vRows.length; j++) {
      if (vRows[j].username) flws[vRows[j].username] = true;
    }
    var oppMeta = byOpp[id] || payByOpp[id] || {};
    oppSummaries.push({
      opp_id: id,
      opp_name: oppMeta.opp_name || 'Opp ' + id,
      country: oppMeta.country || '',
      delivery_type: oppMeta.delivery_type || '',
      visit_count: vRows.length,
      payment_total_usd: Math.round(totalUSD),
      flw_count: Object.keys(flws).length,
      cost_per_visit:
        vRows.length > 0 ? Math.round(totalUSD / vRows.length) : 0,
    });
  }

  // Unique countries and families
  var countrySet = {};
  var entitySet = {};
  for (var i = 0; i < approved.length; i++) {
    if (approved[i].country) countrySet[approved[i].country] = true;
    if (approved[i].entity_name) entitySet[approved[i].entity_name] = true;
  }

  // Total USD
  var totalUSD = 0;
  for (var i = 0; i < payments.length; i++) {
    totalUSD +=
      (parseFloat(payments[i].usd_flw) || 0) +
      (parseFloat(payments[i].usd_org) || 0);
  }

  // Weekly delivery pace
  var allDates = payments
    .map(function (p) {
      return p.status_modified_date || p.payment_date;
    })
    .filter(Boolean);
  var weeks = uniqueWeeks(allDates);
  var weeklySpend = [];
  for (var w = 0; w < weeks.length; w++) {
    weeklySpend.push({
      week: weeks[w],
      usd: Math.round(sumUSDInWeek(payments, weeks[w])),
    });
  }

  return {
    total_visits: approved.length,
    total_usd_distributed: Math.round(totalUSD),
    total_budget: budget || null,
    budget_utilization_pct: budget
      ? Math.round((totalUSD / budget) * 100)
      : null,
    country_count: Object.keys(countrySet).length,
    countries: Object.keys(countrySet),
    families_reached: Object.keys(entitySet).length,
    opportunity_summaries: oppSummaries,
    weekly_spend: weeklySpend.slice(-12), // last 12 weeks
    allocations: allocations || [],
  };
}

// ---------------------------------------------------------------------------
// prepareForPrint — Canvas-to-image conversion for PDF export (#7)
// ---------------------------------------------------------------------------

/**
 * Convert all Chart.js canvas elements to img tags, call window.print(),
 * then restore canvases. This ensures charts appear in the printed PDF.
 */
function prepareForPrint() {
  var canvases = document.querySelectorAll('canvas');
  var replacements = [];

  for (var i = 0; i < canvases.length; i++) {
    var canvas = canvases[i];
    // Get the Chart.js instance if any
    var chartInstance = Chart.getChart(canvas);
    if (!chartInstance) continue;

    var img = document.createElement('img');
    img.src = canvas.toDataURL('image/png', 1.0);
    img.style.width = '100%';
    img.style.height = canvas.style.height || 'auto';
    img.className = canvas.className;

    canvas.parentNode.insertBefore(img, canvas);
    canvas.style.display = 'none';
    replacements.push({ canvas: canvas, img: img });
  }

  // Print after a brief delay to let images render
  setTimeout(function () {
    window.print();
    // Restore canvases after print dialog closes
    setTimeout(function () {
      for (var i = 0; i < replacements.length; i++) {
        replacements[i].canvas.style.display = '';
        if (replacements[i].img.parentNode) {
          replacements[i].img.parentNode.removeChild(replacements[i].img);
        }
      }
    }, 500);
  }, 200);
}

// ---------------------------------------------------------------------------
// animateCounters
// ---------------------------------------------------------------------------

function animateCounters() {
  var elements = document.querySelectorAll('[data-animate-value]');
  if (!elements.length) return;

  var duration = 1000; // ms
  var startTime = null;

  function step(timestamp) {
    if (!startTime) startTime = timestamp;
    var elapsed = timestamp - startTime;
    var progress = Math.min(elapsed / duration, 1);
    // Ease-out cubic
    var eased = 1 - Math.pow(1 - progress, 3);

    for (var i = 0; i < elements.length; i++) {
      var el = elements[i];
      var target = parseInt(el.getAttribute('data-animate-value'), 10);
      var prefix = el.getAttribute('data-animate-prefix') || '';
      var suffix = el.getAttribute('data-animate-suffix') || '';
      if (isNaN(target)) continue;

      var current = Math.round(eased * target);
      el.textContent = prefix + fmtNum(current) + suffix;
    }

    if (progress < 1) {
      requestAnimationFrame(step);
    }
  }

  requestAnimationFrame(step);
}
