// CHC Audit History — render code
// Three tabs: Audit History | Metric Detail | FLW Longitudinal
// Data sources: audit_reports, audit_entries, tasks pipelines (connect_export)
//
// Metric flags use in_range from the server (each entry.results[key].in_range).
// N/A = has_sufficient_data === false. No client-side threshold calculations.
// Task matching: ±2-day window around report.date_created (tasks are created the day after
// the audit run, so exact-date match misses them).

var ce = React.createElement;

// =========================================================================
// Helpers
// =========================================================================

function chcDateRange(start, end) {
  function fmt(d) {
    return d ? String(d).slice(0, 10) : "?";
  }
  if (!start && !end) return "—";
  return fmt(start) + " – " + fmt(end);
}

function chcParseResults(raw) {
  if (!raw) return {};
  if (typeof raw === "object") return raw;
  try {
    return JSON.parse(raw);
  } catch (e) {
    return {};
  }
}

function fmtMetricVal(mObj) {
  if (!mObj || !mObj.has_sufficient_data || mObj.value == null) return "N/A";
  var v = parseFloat(mObj.value);
  if (isNaN(v)) return "N/A";
  return parseFloat(v.toFixed(2));
}

// Absolute calendar-day difference between two date strings (YYYY-MM-DD or ISO datetime).
function chcDayDiff(dateA, dateB) {
  if (!dateA || !dateB) return Infinity;
  var a = new Date(String(dateA).slice(0, 10));
  var b = new Date(String(dateB).slice(0, 10));
  return Math.abs(a.getTime() - b.getTime()) / 86400000;
}

function countMetricFlags(resultsRaw) {
  var res = chcParseResults(resultsRaw);
  var count = 0;
  CHC_METRICS.forEach(function (m) {
    var obj = res[m.key];
    if (obj && obj.has_sufficient_data && obj.in_range === false) count++;
  });
  return count;
}

// =========================================================================
// Metric config — order matches the reference spreadsheet
// =========================================================================
var CHC_METRICS = [
  { key: "camping_ratio", label: "Camping", full: "Camping (Visit:Building Ratio)" },
  { key: "gender_ratio_deviation", label: "Gender Ratio", full: "Gender Ratio Deviation" },
  { key: "muac_photo_compliance", label: "MUAC Photo", full: "MUAC Photo Compliance" },
  { key: "age_heaping", label: "Age Heaping", full: "Age Heaping" },
  { key: "wa_coverage_to_visit_ratio", label: "WA Coverage", full: "WA Coverage to Visit Ratio" },
  { key: "inaccessible_wa_rate_early_warning", label: "Inaccess. WA (Early)", full: "Inaccessible WA Rate – Early Warning" },
  { key: "inaccessible_wa_rate_last_completed_wag", label: "Inaccess. WA (Last)", full: "Inaccessible WA Rate – Last Completed WAG" },
  { key: "vaccine_rate", label: "Vaccine Rate", full: "Vaccine Rate" },
  { key: "vaccine_card_photo_compliance", label: "Vaccine Card", full: "Vaccine Card Photo Compliance" },
  { key: "muac_distribution_pattern_index", label: "MDPI", full: "MUAC Distribution Pattern Index (MDPI)" },
];

// =========================================================================
// Shared UI atoms
// =========================================================================

function ChcPill(props) {
  var s = props.status;
  var cfg = {
    completed: "bg-green-100 text-green-800",
    in_progress: "bg-blue-100 text-blue-800",
    pending: "bg-gray-100 text-gray-600",
    closed: "bg-green-100 text-green-800",
  };
  return ce(
    "span",
    { className: "px-2 py-0.5 rounded text-xs font-medium " + (cfg[s] || "bg-gray-100 text-gray-500") },
    s || "—",
  );
}

function ChcPctCell(props) {
  var pct = props.den > 0 ? Math.round((props.num / props.den) * 100) : null;
  if (pct == null)
    return ce("td", { className: "px-3 py-2 text-right text-gray-400 text-sm" }, "—");
  var good = props.higherIsBetter !== false ? pct >= 70 : pct <= 30;
  return ce(
    "td",
    { className: "px-3 py-2 text-right text-sm tabular-nums" },
    ce("span", { className: "font-semibold " + (good ? "text-green-700" : "text-amber-700") }, pct + "%"),
    ce("span", { className: "text-gray-400 text-xs ml-1" }, "(" + props.num + "/" + props.den + ")"),
  );
}

function ChcSortTh(props) {
  var col = props.colKey, label = props.label, sortCol = props.sortCol,
    sortDir = props.sortDir, onSort = props.onSort;
  var active = sortCol === col;
  var nextDir = active && sortDir === "desc" ? "asc" : "desc";
  var icon = active ? (sortDir === "desc" ? " ↓" : " ↑") : "";
  return ce(
    "th",
    {
      className: "px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider bg-green-900 text-green-100 cursor-pointer select-none hover:bg-green-800",
      style: props.style || {},
      onClick: function () { onSort(col, nextDir); },
    },
    label + icon,
  );
}

// =========================================================================
// Tab 1 — Audit History
// =========================================================================

function ChcAuditHistory(props) {
  var reportRows = props.reportRows, entryRows = props.entryRows,
    taskRows = props.taskRows, oppIds = props.oppIds,
    oppNames = props.oppNames || {}, nameMap = props.nameMap || {};

  var _state = React.useState("all");
  var oppFilter = _state[0], setOppFilter = _state[1];

  var _sortState = React.useState({ col: "date_created", dir: "desc" });
  var sort = _sortState[0], setSort = _sortState[1];

  var filtered = React.useMemo(function () {
    return oppFilter === "all"
      ? reportRows
      : reportRows.filter(function (r) { return String(r.opportunity_id) === oppFilter; });
  }, [reportRows, oppFilter]);

  var enriched = React.useMemo(function () {
    return filtered.map(function (r) {
      var reportId = String(r.report_id || r.id);
      var reportOppId = String(r.opportunity_id);
      var reportDate = (r.date_created || "").slice(0, 10);

      var entries = entryRows.filter(function (e) {
        return String(e.report_id) === reportId && String(e.opportunity_id) === reportOppId;
      });

      var passedN = entries.filter(function (e) {
        return e.is_flagged !== "1" && e.is_flagged !== "true" && e.is_flagged !== true;
      }).length;

      var workerSet = {};
      entries.forEach(function (e) { if (e.username) workerSet[e.username] = true; });

      // ±2-day window: tasks are often created the day after the audit runs
      var periodWorkerTasks = taskRows.filter(function (t) {
        return (
          t.date_created &&
          chcDayDiff(t.date_created.slice(0, 10), reportDate) <= 2 &&
          String(t.opportunity_id) === reportOppId &&
          workerSet[t.username]
        );
      });
      var closedTasks = periodWorkerTasks.filter(function (t) {
        return t.status === "closed" || t.status === "completed";
      });
      var pendingWorkers = {};
      periodWorkerTasks.forEach(function (t) {
        if (t.status !== "closed" && t.status !== "completed") pendingWorkers[t.username] = true;
      });
      return {
        _r: r, flwCount: entries.length, passedN: passedN,
        totalTasks: periodWorkerTasks.length, closedTasks: closedTasks.length,
        pendingWorkers: Object.keys(pendingWorkers).length,
      };
    });
  }, [filtered, entryRows, taskRows]);

  var sorted = React.useMemo(function () {
    var copy = enriched.slice();
    copy.sort(function (a, b) {
      var r = a._r, s = b._r, va, vb;
      if (sort.col === "opportunity") { va = oppNames[r.opportunity_id] || String(r.opportunity_id); vb = oppNames[s.opportunity_id] || String(s.opportunity_id); }
      else if (sort.col === "date_created") { va = r.date_created || ""; vb = s.date_created || ""; }
      else if (sort.col === "period") { va = r.period_start || ""; vb = s.period_start || ""; }
      else if (sort.col === "flws") { va = a.flwCount; vb = b.flwCount; }
      else if (sort.col === "status") { va = r.status || ""; vb = s.status || ""; }
      else if (sort.col === "passed") { va = a.flwCount ? a.passedN / a.flwCount : -1; vb = b.flwCount ? b.passedN / b.flwCount : -1; }
      else if (sort.col === "tasks") { va = a.totalTasks ? a.closedTasks / a.totalTasks : -1; vb = b.totalTasks ? b.closedTasks / b.totalTasks : -1; }
      else if (sort.col === "ptask") { va = a.flwCount ? a.pendingWorkers / a.flwCount : -1; vb = b.flwCount ? b.pendingWorkers / b.flwCount : -1; }
      else if (sort.col === "runby") { va = nameMap[r.completed_by_username] || r.completed_by_username || ""; vb = nameMap[s.completed_by_username] || s.completed_by_username || ""; }
      else { va = ""; vb = ""; }
      if (typeof va === "number" && typeof vb === "number") return sort.dir === "asc" ? va - vb : vb - va;
      return sort.dir === "asc" ? String(va).localeCompare(String(vb)) : String(vb).localeCompare(String(va));
    });
    return copy;
  }, [enriched, sort, nameMap]);

  var thProps = { sortCol: sort.col, sortDir: sort.dir, onSort: function (c, d) { setSort({ col: c, dir: d }); } };

  return ce(
    "div", { className: "p-4 space-y-3" },
    ce("div", { className: "flex items-center gap-3 flex-wrap" },
      ce("span", { className: "text-xs font-semibold uppercase tracking-wider text-gray-500" }, "Opportunity"),
      ce("select", {
        className: "text-sm border border-gray-300 rounded px-2 py-1.5 bg-white",
        value: oppFilter, onChange: function (e) { setOppFilter(e.target.value); },
      },
        ce("option", { value: "all" }, "All Opportunities"),
        oppIds.map(function (id) { return ce("option", { key: id, value: String(id) }, oppNames[id] || "Opp #" + id); }),
      ),
      ce("span", { className: "text-xs text-gray-400" }, sorted.length + " report" + (sorted.length !== 1 ? "s" : "")),
    ),
    ce("div", { className: "rounded-lg overflow-hidden shadow-sm border border-gray-200" },
      ce("div", { className: "overflow-x-auto" },
        ce("table", { className: "w-full border-collapse bg-white text-sm" },
          ce("thead", null, ce("tr", null,
            ce(ChcSortTh, Object.assign({ colKey: "opportunity", label: "Opportunity" }, thProps)),
            ce(ChcSortTh, Object.assign({ colKey: "date_created", label: "Created Date" }, thProps)),
            ce(ChcSortTh, Object.assign({ colKey: "period", label: "Audit Period" }, thProps)),
            ce(ChcSortTh, Object.assign({ colKey: "flws", label: "FLWs" }, thProps)),
            ce(ChcSortTh, Object.assign({ colKey: "status", label: "Status" }, thProps)),
            ce(ChcSortTh, Object.assign({ colKey: "passed", label: "% FLWs Passed", style: { maxWidth: "80px" } }, thProps)),
            ce(ChcSortTh, Object.assign({ colKey: "tasks", label: "% Tasks Completed", style: { maxWidth: "80px" } }, thProps)),
            ce(ChcSortTh, Object.assign({ colKey: "ptask", label: "% FLWs w/ Pending Task", style: { maxWidth: "80px" } }, thProps)),
            ce(ChcSortTh, Object.assign({ colKey: "runby", label: "Run By" }, thProps)),
          )),
          ce("tbody", null,
            sorted.length === 0
              ? ce("tr", null, ce("td", { colSpan: 9, className: "px-4 py-8 text-center text-gray-400" }, "No audit reports found"))
              : sorted.map(function (row, i) {
                var r = row._r;
                var completed = r.status === "completed";
                return ce("tr", { key: i, className: "border-b border-gray-100 hover:bg-gray-50" + (!completed ? " text-gray-500" : "") },
                  ce("td", { className: "px-3 py-2 font-medium text-green-900" }, oppNames[r.opportunity_id] || "Opp #" + r.opportunity_id),
                  ce("td", { className: "px-3 py-2 font-medium whitespace-nowrap" }, (r.date_created || "—").slice(0, 10)),
                  ce("td", { className: "px-3 py-2 whitespace-nowrap" }, chcDateRange(r.period_start, r.period_end)),
                  ce("td", { className: "px-3 py-2 text-right tabular-nums" }, completed ? row.flwCount : "—"),
                  ce("td", { className: "px-3 py-2" }, ce(ChcPill, { status: r.status })),
                  ce(ChcPctCell, { num: row.passedN, den: row.flwCount }),
                  ce(ChcPctCell, { num: row.closedTasks, den: row.totalTasks }),
                  ce(ChcPctCell, { num: row.pendingWorkers, den: row.flwCount, higherIsBetter: false }),
                  ce("td", { className: "px-3 py-2 text-sm" }, nameMap[r.completed_by_username] || r.completed_by_username || "—"),
                );
              }),
          ),
        ),
      ),
    ),
  );
}

// =========================================================================
// Tab 2 — Metric Detail
// Rows: one per (username, opportunity_id). Flags = in_range === false.
// Column picker lets users show/hide individual metric columns.
// =========================================================================

function ChcMetricDetail(props) {
  var reportRows = props.reportRows, entryRows = props.entryRows,
    oppIds = props.oppIds, oppNames = props.oppNames || {}, nameMap = props.nameMap || {};

  var _repState = React.useState("all");
  var repFilter = _repState[0], setRepFilter = _repState[1];

  var _oppState = React.useState("all");
  var oppFilter = _oppState[0], setOppFilter = _oppState[1];

  var _dateState = React.useState("all");
  var runDateFilter = _dateState[0], setRunDateFilter = _dateState[1];

  var _sortState = React.useState({ col: "opportunity", dir: "asc" });
  var mdSort = _sortState[0], setMdSort = _sortState[1];

  // Column visibility — all on by default
  var _visState = React.useState(function () {
    var v = {};
    CHC_METRICS.forEach(function (m) { v[m.key] = true; });
    return v;
  });
  var visibleCols = _visState[0], setVisibleCols = _visState[1];

  var shownMetrics = React.useMemo(function () {
    return CHC_METRICS.filter(function (m) { return visibleCols[m.key]; });
  }, [visibleCols]);

  var completedReports = React.useMemo(function () {
    return reportRows.filter(function (r) { return r.status === "completed"; });
  }, [reportRows]);

  // Distinct run dates from completed reports
  var runDates = React.useMemo(function () {
    var seen = {};
    var dates = [];
    completedReports.forEach(function (r) {
      var d = (r.date_created || "").slice(0, 10);
      if (d && !seen[d]) { seen[d] = true; dates.push(d); }
    });
    return dates.sort(function (a, b) { return b.localeCompare(a); });
  }, [completedReports]);

  var filteredEntries = React.useMemo(function () {
    var rows = entryRows;
    if (oppFilter !== "all") {
      rows = rows.filter(function (e) { return String(e.opportunity_id) === oppFilter; });
    }
    if (repFilter !== "all") {
      var parts = repFilter.split(":");
      var rId = parts[0], rOppId = parts[1];
      rows = rows.filter(function (e) {
        return String(e.report_id) === rId && String(e.opportunity_id) === rOppId;
      });
    }
    if (runDateFilter !== "all") {
      var matchKeys = {};
      completedReports.forEach(function (r) {
        if ((r.date_created || "").slice(0, 10) === runDateFilter) {
          matchKeys[String(r.report_id || r.id) + ":" + String(r.opportunity_id)] = true;
        }
      });
      rows = rows.filter(function (e) {
        return matchKeys[String(e.report_id) + ":" + String(e.opportunity_id)];
      });
    }
    return rows;
  }, [entryRows, oppFilter, repFilter, runDateFilter, completedReports]);

  // One row per (username, opportunity_id).
  // When multiple audits are in the filter, average each metric across audits with sufficient data.
  var flwRows = React.useMemo(function () {
    var byKey = {};
    filteredEntries.forEach(function (e) {
      if (!e.username) return;
      var key = e.username + ":" + String(e.opportunity_id);
      if (!byKey[key]) byKey[key] = [];
      byKey[key].push(e);
    });

    return Object.keys(byKey).map(function (key) {
      var entries = byKey[key];
      var latest = entries.reduce(function (best, e) {
        return !best || e.date_created > best.date_created ? e : best;
      }, null);
      if (entries.length === 1) return latest;

      var avgResults = {};
      CHC_METRICS.forEach(function (m) {
        var values = [], anyFlagged = false;
        entries.forEach(function (e) {
          var obj = chcParseResults(e.results)[m.key];
          if (obj && obj.has_sufficient_data && obj.value != null) {
            var parsed = parseFloat(obj.value);
            if (!isNaN(parsed)) {
              values.push(parsed);
              if (obj.in_range === false) anyFlagged = true;
            }
          }
        });
        if (values.length > 0) {
          var avg = values.reduce(function (s, v) { return s + v; }, 0) / values.length;
          avgResults[m.key] = { value: avg, has_sufficient_data: true, in_range: anyFlagged ? false : true };
        } else {
          avgResults[m.key] = { value: null, has_sufficient_data: false, in_range: null };
        }
      });
      return Object.assign({}, latest, { results: avgResults, _auditCount: entries.length });
    });
  }, [filteredEntries]);

  var sortedFlwRows = React.useMemo(function () {
    var copy = flwRows.slice();
    copy.sort(function (a, b) {
      var col = mdSort.col, dir = mdSort.dir, va, vb;
      if (col === "opportunity") {
        va = oppNames[a.opportunity_id] || String(a.opportunity_id) || "";
        vb = oppNames[b.opportunity_id] || String(b.opportunity_id) || "";
        return dir === "asc" ? va.localeCompare(vb) : vb.localeCompare(va);
      } else if (col === "worker") {
        va = nameMap[a.username] || a.username || "";
        vb = nameMap[b.username] || b.username || "";
        return dir === "asc" ? va.localeCompare(vb) : vb.localeCompare(va);
      } else if (col === "flags") {
        va = countMetricFlags(a.results); vb = countMetricFlags(b.results);
        return dir === "asc" ? va - vb : vb - va;
      } else {
        var resA = chcParseResults(a.results), resB = chcParseResults(b.results);
        var objA = resA[col], objB = resB[col];
        var numA = objA && objA.has_sufficient_data && objA.value != null ? objA.value : null;
        var numB = objB && objB.has_sufficient_data && objB.value != null ? objB.value : null;
        if (numA === null && numB === null) return 0;
        if (numA === null) return 1;
        if (numB === null) return -1;
        return dir === "asc" ? numA - numB : numB - numA;
      }
    });
    return copy;
  }, [flwRows, mdSort, oppNames, nameMap]);

  // Per-column flag counts (only for shown columns, but using all-metric totals for the Flags cell)
  var colFlagCounts = React.useMemo(function () {
    return shownMetrics.map(function (m) {
      var count = 0;
      sortedFlwRows.forEach(function (row) {
        var obj = chcParseResults(row.results)[m.key];
        if (obj && obj.has_sufficient_data && obj.in_range === false) count++;
      });
      return count;
    });
  }, [sortedFlwRows, shownMetrics]);

  var totalFlags = React.useMemo(function () {
    var n = 0;
    sortedFlwRows.forEach(function (row) { n += countMetricFlags(row.results); });
    return n;
  }, [sortedFlwRows]);

  var thProps = { sortCol: mdSort.col, sortDir: mdSort.dir, onSort: function (c, d) { setMdSort({ col: c, dir: d }); } };

  return ce(
    "div", { className: "p-4 space-y-3" },
    // Filter bar
    ce("div", { className: "flex items-center gap-3 flex-wrap" },
      ce("span", { className: "text-xs font-semibold uppercase tracking-wider text-gray-500" }, "Report"),
      ce("select", {
        className: "text-sm border border-gray-300 rounded px-2 py-1.5 bg-white",
        value: repFilter, onChange: function (e) { setRepFilter(e.target.value); },
      },
        ce("option", { value: "all" }, "All Completed Audits"),
        completedReports.map(function (r, i) {
          var val = String(r.report_id || r.id) + ":" + String(r.opportunity_id);
          var label = (oppNames[r.opportunity_id] || "Opp #" + r.opportunity_id) + " · " + chcDateRange(r.period_start, r.period_end);
          return ce("option", { key: i, value: val }, label);
        }),
      ),
      ce("span", { className: "text-xs font-semibold uppercase tracking-wider text-gray-500" }, "Opportunity"),
      ce("select", {
        className: "text-sm border border-gray-300 rounded px-2 py-1.5 bg-white",
        value: oppFilter, onChange: function (e) { setOppFilter(e.target.value); },
      },
        ce("option", { value: "all" }, "All Opportunities"),
        oppIds.map(function (id) { return ce("option", { key: id, value: String(id) }, oppNames[id] || "Opp #" + id); }),
      ),
      ce("span", { className: "text-xs font-semibold uppercase tracking-wider text-gray-500" }, "Run Date"),
      ce("select", {
        className: "text-sm border border-gray-300 rounded px-2 py-1.5 bg-white",
        value: runDateFilter, onChange: function (e) { setRunDateFilter(e.target.value); },
      },
        ce("option", { value: "all" }, "All Dates"),
        runDates.map(function (d) { return ce("option", { key: d, value: d }, d); }),
      ),
      ce("span", { className: "text-xs text-gray-400" }, sortedFlwRows.length + " worker" + (sortedFlwRows.length !== 1 ? "s" : "")),
      // Column picker
      ce("details", { className: "relative ml-auto" },
        ce("summary", {
          className: "cursor-pointer text-xs font-semibold uppercase tracking-wider text-green-800 border border-green-300 rounded px-3 py-1.5 bg-green-50 hover:bg-green-100 list-none",
        }, "Columns ▾"),
        ce("div", {
          className: "absolute right-0 top-full z-30 bg-white border border-gray-200 rounded shadow-lg p-3 min-w-48 mt-1",
          onClick: function (e) { e.stopPropagation(); },
        },
          ce("div", { className: "text-xs font-semibold uppercase text-gray-500 mb-2" }, "Show / Hide Metrics"),
          CHC_METRICS.map(function (m) {
            return ce("label", { key: m.key, className: "flex items-center gap-2 py-1 cursor-pointer text-sm hover:text-green-800" },
              ce("input", {
                type: "checkbox",
                checked: visibleCols[m.key],
                onChange: function () {
                  setVisibleCols(function (prev) {
                    var next = Object.assign({}, prev);
                    next[m.key] = !next[m.key];
                    return next;
                  });
                },
              }),
              m.label,
            );
          }),
          ce("div", { className: "border-t border-gray-100 mt-2 pt-2 flex gap-2" },
            ce("button", {
              className: "text-xs text-green-700 hover:underline",
              onClick: function () {
                setVisibleCols(function () {
                  var v = {};
                  CHC_METRICS.forEach(function (m) { v[m.key] = true; });
                  return v;
                });
              },
            }, "All"),
            ce("button", {
              className: "text-xs text-gray-500 hover:underline",
              onClick: function () {
                setVisibleCols(function () {
                  var v = {};
                  CHC_METRICS.forEach(function (m) { v[m.key] = false; });
                  return v;
                });
              },
            }, "None"),
          ),
        ),
      ),
    ),
    // Table
    ce("div", { className: "rounded-lg overflow-hidden shadow-sm border border-gray-200" },
      ce("div", { className: "overflow-x-auto" },
        ce("table", { className: "border-collapse bg-white text-xs" },
          ce("thead", null,
            ce("tr", null,
              ce(ChcSortTh, Object.assign({ colKey: "opportunity", label: "Opportunity" }, thProps)),
              ce(ChcSortTh, Object.assign({ colKey: "worker", label: "Connect Worker" }, thProps)),
              shownMetrics.map(function (m) {
                return ce(ChcSortTh, Object.assign({ key: m.key, colKey: m.key, label: m.label, style: { maxWidth: "72px" } }, thProps));
              }),
              ce(ChcSortTh, Object.assign({ colKey: "flags", label: "Flags" }, thProps)),
            ),
          ),
          ce("tbody", null,
            sortedFlwRows.length === 0
              ? ce("tr", null, ce("td", { colSpan: shownMetrics.length + 3, className: "px-4 py-8 text-center text-gray-400" }, "No data for current filters"))
              : sortedFlwRows.map(function (row, i) {
                var res = chcParseResults(row.results);
                var rowFlagCount = countMetricFlags(row.results);
                var cells = shownMetrics.map(function (m) {
                  var obj = res[m.key];
                  var isNA = !obj || !obj.has_sufficient_data || obj.value == null;
                  var isFlagged = !isNA && obj.in_range === false;
                  return ce("td", {
                    key: m.key, title: m.full,
                    className: "border border-gray-200 px-2 py-1.5 text-center tabular-nums " +
                      (isFlagged ? "bg-red-100 text-red-800 font-semibold" : isNA ? "text-gray-400" : ""),
                  }, isNA ? "N/A" : fmtMetricVal(obj));
                });
                return ce("tr", { key: i, className: "hover:bg-gray-50" },
                  ce("td", { className: "border border-gray-200 px-3 py-1.5 text-left whitespace-nowrap font-medium text-green-900 bg-white" },
                    oppNames[row.opportunity_id] || "Opp #" + row.opportunity_id),
                  ce("td", { className: "border border-gray-200 px-3 py-1.5 text-left font-medium whitespace-nowrap bg-white" },
                    nameMap[row.username] || row.username,
                    row._auditCount > 1 ? ce("span", { className: "ml-1 text-gray-400 text-xs font-normal", title: row._auditCount + " audits averaged" }, "×" + row._auditCount) : null,
                  ),
                  cells,
                  ce("td", { className: "border border-gray-200 px-2 py-1.5 text-center font-bold bg-green-900 text-white" }, rowFlagCount),
                );
              }),
          ),
          ce("tfoot", null,
            ce("tr", null,
              ce("td", { colSpan: 2, className: "border border-gray-200 px-3 py-1.5 text-left font-bold text-xs uppercase bg-green-50 text-green-800" }, "Metric Totals"),
              colFlagCounts.map(function (n, i) {
                return ce("td", { key: i, className: "border border-gray-200 px-2 py-1.5 text-center font-bold text-xs " + (n > 0 ? "bg-red-100 text-red-800" : "bg-green-50 text-green-700") }, n);
              }),
              ce("td", { className: "border border-gray-200 px-2 py-1.5 text-center font-bold bg-green-900 text-white text-xs" }, totalFlags),
            ),
          ),
        ),
      ),
    ),
  );
}

// =========================================================================
// Tab 3 — FLW Longitudinal
// =========================================================================

function ChcFLWLongitudinal(props) {
  var reportRows = props.reportRows, entryRows = props.entryRows,
    taskRows = props.taskRows, workers = props.workers,
    oppIds = props.oppIds, oppNames = props.oppNames || {}, nameMap = props.nameMap || {};

  var _oppState = React.useState("all");
  var oppFilter = _oppState[0], setOppFilter = _oppState[1];

  var _expandState = React.useState({});
  var expandedWorkers = _expandState[0], setExpandedWorkers = _expandState[1];

  function toggleExpand(username) {
    setExpandedWorkers(function (prev) {
      var next = Object.assign({}, prev);
      if (next[username]) delete next[username]; else next[username] = true;
      return next;
    });
  }

  // Completed reports, optionally scoped to opp
  var scopedReports = React.useMemo(function () {
    var base = reportRows.filter(function (r) { return r.status === "completed"; });
    return oppFilter === "all" ? base : base.filter(function (r) { return String(r.opportunity_id) === oppFilter; });
  }, [reportRows, oppFilter]);

  // Completed cycles grouped by period_start
  var cycles = React.useMemo(function () {
    var periodMap = {};
    scopedReports.forEach(function (r) {
      var key = (r.period_start || "").slice(0, 10);
      if (!periodMap[key]) periodMap[key] = [];
      periodMap[key].push(r);
    });
    return Object.keys(periodMap)
      .sort(function (a, b) { return b.localeCompare(a); })
      .map(function (key) { return { period_start: key, reports: periodMap[key] }; });
  }, [scopedReports]);

  // FLWs visible under current opp filter
  var flwList = React.useMemo(function () {
    var seen = {};
    var list = [];
    var sourceEntries = oppFilter === "all"
      ? entryRows
      : entryRows.filter(function (e) { return String(e.opportunity_id) === oppFilter; });
    sourceEntries.forEach(function (e) {
      if (e.username && !seen[e.username]) { seen[e.username] = true; list.push(e.username); }
    });
    if (oppFilter === "all") {
      (workers || []).forEach(function (w) {
        if (w.username && !seen[w.username]) { seen[w.username] = true; list.push(w.username); }
      });
    }
    return list.sort(function (a, b) { return (nameMap[a] || a).localeCompare(nameMap[b] || b); });
  }, [entryRows, workers, nameMap, oppFilter]);

  // Per-FLW per-cycle stats
  var flwCycleData = React.useMemo(function () {
    return flwList.map(function (username) {
      var workerTotalFlags = 0;
      var workerTotalTasks = 0;
      var cycleStats = cycles.map(function (cycle) {
        var cycleReports = cycle.reports;
        var entries = entryRows.filter(function (e) {
          return e.username === username &&
            cycleReports.some(function (r) {
              return String(e.report_id) === String(r.report_id || r.id) &&
                String(e.opportunity_id) === String(r.opportunity_id);
            });
        });
        if (!entries.length) return null;

        var metricFlags = 0;
        var totalMetricsWithData = 0;
        entries.forEach(function (e) {
          metricFlags += countMetricFlags(e.results);
          var res = chcParseResults(e.results);
          CHC_METRICS.forEach(function (m) {
            var obj = res[m.key];
            if (obj && obj.has_sufficient_data) totalMetricsWithData++;
          });
        });

        // ±2-day window task matching
        var cycleTasks = [];
        cycleReports.forEach(function (r) {
          var reportDate = (r.date_created || "").slice(0, 10);
          var reportOppId = String(r.opportunity_id);
          taskRows.filter(function (t) {
            return t.username === username &&
              t.date_created &&
              String(t.opportunity_id) === reportOppId &&
              chcDayDiff(t.date_created.slice(0, 10), reportDate) <= 2;
          }).forEach(function (t) { cycleTasks.push(t); });
        });

        var closedTasks = cycleTasks.filter(function (t) {
          return t.status === "closed" || t.status === "completed";
        }).length;

        workerTotalFlags += metricFlags;
        workerTotalTasks += cycleTasks.length;
        return {
          metricFlags: metricFlags,
          totalMetricsWithData: totalMetricsWithData,
          nTasks: cycleTasks.length,
          closedTasks: closedTasks,
        };
      });
      return { username: username, totalFlags: workerTotalFlags, totalTasks: workerTotalTasks, cycles: cycleStats };
    });
  }, [flwList, cycles, entryRows, taskRows]);

  // Build per-FLW task list for expand view (all completed reports, ±2 day match)
  var completedReports = React.useMemo(function () {
    return reportRows.filter(function (r) { return r.status === "completed"; });
  }, [reportRows]);

  function getFlwTasksWithCycle(username) {
    var flwTasks = taskRows.filter(function (t) { return t.username === username; });
    return flwTasks.map(function (t) {
      var taskDate = (t.date_created || "").slice(0, 10);
      var taskOppId = String(t.opportunity_id);
      var bestReport = null, bestDiff = Infinity;
      completedReports.forEach(function (r) {
        if (String(r.opportunity_id) !== taskOppId) return;
        var d = chcDayDiff(taskDate, (r.date_created || "").slice(0, 10));
        if (d <= 2 && d < bestDiff) { bestReport = r; bestDiff = d; }
      });
      return { task: t, report: bestReport };
    }).sort(function (a, b) {
      var pa = a.report ? (a.report.period_start || "") : "zzz";
      var pb = b.report ? (b.report.period_start || "") : "zzz";
      return pa.localeCompare(pb);
    });
  }

  function taskStatusLabel(s) {
    if (s === "closed" || s === "completed") return "Complete";
    if (s === "pending") return "Pending";
    return s || "—";
  }

  function taskStatusCls(s) {
    if (s === "closed" || s === "completed") return "bg-green-100 text-green-800";
    if (s === "pending") return "bg-amber-100 text-amber-700";
    return "bg-gray-100 text-gray-500";
  }

  var colCount = 3 + cycles.length; // FLW + Total Flags + Total Tasks + cycles

  return ce(
    "div", { className: "p-4 space-y-3" },
    // Filter bar
    ce("div", { className: "flex items-center gap-3 flex-wrap" },
      ce("span", { className: "text-xs font-semibold uppercase tracking-wider text-gray-500" }, "Opportunity"),
      ce("select", {
        className: "text-sm border border-gray-300 rounded px-2 py-1.5 bg-white",
        value: oppFilter, onChange: function (e) { setOppFilter(e.target.value); },
      },
        ce("option", { value: "all" }, "All Opportunities"),
        oppIds.map(function (id) { return ce("option", { key: id, value: String(id) }, oppNames[id] || "Opp #" + id); }),
      ),
    ),
    // Table
    cycles.length === 0
      ? ce("div", { className: "rounded-lg border border-gray-200 p-8 text-center text-gray-400 text-sm bg-white" }, "No completed audit cycles found")
      : ce("div", { className: "rounded-lg overflow-hidden shadow-sm border border-gray-200" },
        ce("div", { className: "overflow-x-auto" },
          ce("table", { className: "border-collapse bg-white text-xs w-full" },
            ce("thead", null,
              ce("tr", null,
                ce("th", {
                  className: "bg-green-900 text-white px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider whitespace-nowrap min-w-40",
                  style: { position: "sticky", left: 0 },
                }, "FLW"),
                ce("th", { className: "bg-green-900 text-white px-3 py-2 text-center text-xs font-semibold uppercase tracking-wider whitespace-nowrap" }, "Total Flags"),
                ce("th", { className: "bg-green-900 text-white px-3 py-2 text-center text-xs font-semibold uppercase tracking-wider whitespace-nowrap" }, "Total Tasks"),
                cycles.map(function (c, i) {
                  var r = c.reports[0];
                  return ce("th", { key: i, className: "bg-green-800 text-green-100 px-3 py-2 text-center font-semibold min-w-40" },
                    chcDateRange(r.period_start, r.period_end));
                }),
              ),
            ),
            ce("tbody", null,
              flwCycleData.map(function (row, i) {
                var isExpanded = expandedWorkers[row.username];
                var flwTasks = isExpanded ? getFlwTasksWithCycle(row.username) : [];
                var displayName = nameMap[row.username] || row.username;
                return [
                  // Main FLW row
                  ce("tr", { key: "r" + i, className: "border-b border-gray-100 cursor-pointer hover:bg-gray-50", onClick: function () { toggleExpand(row.username); } },
                    ce("td", {
                      className: "border-r border-gray-200 px-3 py-2 font-medium whitespace-nowrap bg-gray-50",
                      style: { position: "sticky", left: 0 },
                    },
                      ce("span", { className: "mr-1.5 text-gray-400 text-xs" }, isExpanded ? "▼" : "▶"),
                      displayName,
                    ),
                    ce("td", {
                      className: "border-r border-gray-200 px-3 py-2 text-center font-bold " + (row.totalFlags > 0 ? "text-red-700" : "text-gray-400"),
                    }, row.totalFlags),
                    ce("td", {
                      className: "border-r border-gray-200 px-3 py-2 text-center font-bold text-gray-700",
                    }, row.totalTasks),
                    row.cycles.map(function (cs, j) {
                      if (!cs) return ce("td", { key: j, className: "border-r border-gray-200 px-3 py-2 text-center text-gray-300" }, "—");
                      var mPassed = cs.totalMetricsWithData - cs.metricFlags;
                      var mPct = cs.totalMetricsWithData > 0 ? Math.round((mPassed / cs.totalMetricsWithData) * 100) : 100;
                      var tPct = cs.nTasks > 0 ? Math.round((cs.closedTasks / cs.nTasks) * 100) : null;
                      var flagCls = cs.metricFlags === 0 ? "" : cs.metricFlags === 1 ? "bg-amber-50" : cs.metricFlags === 2 ? "bg-amber-100" : "bg-amber-200";
                      return ce("td", { key: j, className: "border-r border-gray-200 px-3 py-2 " + flagCls },
                        ce("div", { className: "flex flex-col gap-0.5" },
                          ce("span", { className: cs.metricFlags > 0 ? "font-semibold text-red-700" : "font-semibold text-green-800" },
                            mPct + "% (" + mPassed + "/" + cs.totalMetricsWithData + ")"),
                          tPct != null
                            ? ce("span", { className: "text-amber-700 text-xs" }, tPct + "% tasks (" + cs.closedTasks + "/" + cs.nTasks + ")")
                            : ce("span", { className: "text-gray-400 text-xs" }, "no tasks"),
                        ),
                      );
                    }),
                  ),
                  // Expanded task detail row
                  isExpanded ? ce("tr", { key: "e" + i, className: "bg-gray-50" },
                    ce("td", { colSpan: colCount, className: "px-4 py-3" },
                      ce("div", { className: "text-xs font-semibold uppercase text-gray-500 mb-2" },
                        "Tasks for " + displayName + " (" + flwTasks.length + " total)"),
                      flwTasks.length === 0
                        ? ce("p", { className: "text-xs text-gray-400" }, "No tasks found")
                        : ce("table", { className: "w-full border-collapse text-xs" },
                          ce("thead", null,
                            ce("tr", { className: "bg-white" },
                              ce("th", { className: "px-3 py-1.5 text-left font-semibold text-gray-600 border-b border-gray-200" }, "Audit Cycle"),
                              ce("th", { className: "px-3 py-1.5 text-left font-semibold text-gray-600 border-b border-gray-200" }, "Task"),
                              ce("th", { className: "px-3 py-1.5 text-left font-semibold text-gray-600 border-b border-gray-200" }, "Status"),
                              ce("th", { className: "px-3 py-1.5 text-left font-semibold text-gray-600 border-b border-gray-200" }, "Date Assigned"),
                              ce("th", { className: "px-3 py-1.5 text-left font-semibold text-gray-600 border-b border-gray-200" }, "Completed At"),
                            ),
                          ),
                          ce("tbody", null,
                            flwTasks.map(function (tw, k) {
                              var t = tw.task, r = tw.report;
                              return ce("tr", { key: k, className: "border-b border-gray-100 hover:bg-white" },
                                ce("td", { className: "px-3 py-1.5 text-gray-600 whitespace-nowrap" },
                                  r ? chcDateRange(r.period_start, r.period_end) : ce("span", { className: "text-gray-400" }, "Unmatched")),
                                ce("td", { className: "px-3 py-1.5 font-medium" }, t.name || ce("span", { className: "text-gray-400" }, "(unnamed)")),
                                ce("td", { className: "px-3 py-1.5" },
                                  ce("span", { className: "px-2 py-0.5 rounded font-medium " + taskStatusCls(t.status) }, taskStatusLabel(t.status))),
                                ce("td", { className: "px-3 py-1.5 text-gray-500 whitespace-nowrap" }, (t.date_created || "—").slice(0, 10)),
                                ce("td", { className: "px-3 py-1.5 text-gray-500 whitespace-nowrap" }, t.completed_at ? t.completed_at.slice(0, 10) : "—"),
                              );
                            }),
                          ),
                        ),
                    ),
                  ) : null,
                ];
              }),
            ),
          ),
        ),
      ),
  );
}

// =========================================================================
// Main component
// =========================================================================

function WorkflowUI(props) {
  var definition = props.definition, workers = props.workers,
    pipelines = props.pipelines, view = props.view;

  var _tabState = React.useState(0);
  var activeTab = _tabState[0], setTab = _tabState[1];

  var oppIds = React.useMemo(function () {
    return (definition && definition.opportunity_ids) || [];
  }, [definition]);

  var oppNames = React.useMemo(function () {
    var m = {};
    try {
      var el = document.getElementById("user-opportunities");
      if (el) JSON.parse(el.textContent).forEach(function (o) { m[o.id] = o.name; });
    } catch (e) { console.error("CHC: failed to parse user-opportunities", e); }
    return m;
  }, []);

  var nameMap = React.useMemo(function () {
    var m = {};
    (workers || []).forEach(function (w) { if (w.username) m[w.username] = w.name || w.username; });
    return m;
  }, [workers]);

  var srcPipelines = React.useMemo(function () {
    return (view && view.pipelines) || pipelines || {};
  }, [view, pipelines]);

  var reportRows = React.useMemo(function () {
    return (srcPipelines.audit_reports && srcPipelines.audit_reports.rows) || [];
  }, [srcPipelines]);

  var entryRows = React.useMemo(function () {
    return (srcPipelines.audit_entries && srcPipelines.audit_entries.rows) || [];
  }, [srcPipelines]);

  var taskRows = React.useMemo(function () {
    return (srcPipelines.tasks && srcPipelines.tasks.rows) || [];
  }, [srcPipelines]);

  var tabs = ["Audit History", "Metric Detail", "FLW Longitudinal"];

  return ce(
    "div", { className: "min-h-screen bg-gray-50" },
    ce("div", { className: "bg-green-900 text-white px-6 py-4" },
      ce("div", { className: "text-xs uppercase tracking-widest opacity-60 mb-1" }, "Program 176 · DIMAGI-CHC-RCT · Nigeria"),
      ce("div", { className: "text-xl font-bold tracking-tight" }, "CHC Audit History"),
      ce("div", { className: "flex gap-2 mt-2 flex-wrap" },
        oppIds.map(function (id) {
          return ce("span", { key: id, className: "text-xs px-2 py-0.5 bg-white/10 border border-white/20 rounded" },
            oppNames[id] || "Opp #" + id);
        }),
      ),
    ),
    ce("div", { className: "bg-white border-b border-gray-200 flex px-4" },
      tabs.map(function (lbl, i) {
        return ce("button", {
          key: i,
          className: "px-4 py-3 text-sm font-medium border-b-2 -mb-px " +
            (activeTab === i ? "text-green-800 border-green-700 font-semibold" : "text-gray-500 border-transparent hover:text-green-800"),
          onClick: function () { setTab(i); },
        }, lbl);
      }),
    ),
    activeTab === 0 && ce(ChcAuditHistory, { reportRows: reportRows, entryRows: entryRows, taskRows: taskRows, oppIds: oppIds, oppNames: oppNames, nameMap: nameMap }),
    activeTab === 1 && ce(ChcMetricDetail, { reportRows: reportRows, entryRows: entryRows, oppIds: oppIds, oppNames: oppNames, nameMap: nameMap }),
    activeTab === 2 && ce(ChcFLWLongitudinal, { reportRows: reportRows, entryRows: entryRows, taskRows: taskRows, workers: workers, oppIds: oppIds, oppNames: oppNames, nameMap: nameMap }),
  );
}
