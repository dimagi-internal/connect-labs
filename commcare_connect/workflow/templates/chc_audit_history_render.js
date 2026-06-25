// CHC Audit History — render code
// Three tabs: Audit History | Metric Detail | FLW Longitudinal
// Data sources: audit_reports, audit_entries, tasks pipelines (connect_export)
//
// Metric flags use in_range from the server (each entry.results[key].in_range).
// N/A = has_sufficient_data === false. No client-side threshold calculations.

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

// Format a metric object value for display
function fmtMetricVal(mObj) {
  if (!mObj || !mObj.has_sufficient_data || mObj.value == null) return "N/A";
  var v = parseFloat(mObj.value);
  if (isNaN(v)) return "N/A";
  return parseFloat(v.toFixed(2));
}

// Count flagged metrics from a results JSON string/object
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
// Flag = in_range === false (computed by the server, not client thresholds)
// =========================================================================
var CHC_METRICS = [
  {
    key: "camping_ratio",
    label: "Camping",
    full: "Camping (Visit:Building Ratio)",
  },
  {
    key: "gender_ratio_deviation",
    label: "Gender Ratio",
    full: "Gender Ratio Deviation",
  },
  {
    key: "muac_photo_compliance",
    label: "MUAC Photo",
    full: "MUAC Photo Compliance",
  },
  { key: "age_heaping", label: "Age Heaping", full: "Age Heaping" },
  {
    key: "wa_coverage_to_visit_ratio",
    label: "WA Coverage",
    full: "WA Coverage to Visit Ratio",
  },
  {
    key: "inaccessible_wa_rate_early_warning",
    label: "Inaccess. WA (Early)",
    full: "Inaccessible WA Rate – Early Warning",
  },
  {
    key: "inaccessible_wa_rate_last_completed_wag",
    label: "Inaccess. WA (Last)",
    full: "Inaccessible WA Rate – Last Completed WAG",
  },
  { key: "vaccine_rate", label: "Vaccine Rate", full: "Vaccine Rate" },
  {
    key: "vaccine_card_photo_compliance",
    label: "Vaccine Card",
    full: "Vaccine Card Photo Compliance",
  },
  {
    key: "muac_distribution_pattern_index",
    label: "MDPI",
    full: "MUAC Distribution Pattern Index (MDPI)",
  },
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
  };
  return ce(
    "span",
    {
      className:
        "px-2 py-0.5 rounded text-xs font-medium " +
        (cfg[s] || "bg-gray-100 text-gray-500"),
    },
    s || "—",
  );
}

// Renders a percentage cell with format "75% (3/4)"
function ChcPctCell(props) {
  var pct =
    props.den > 0 ? Math.round((props.num / props.den) * 100) : null;
  if (pct == null)
    return ce(
      "td",
      { className: "px-3 py-2 text-right text-gray-400 text-sm" },
      "—",
    );
  var good = props.higherIsBetter !== false ? pct >= 70 : pct <= 30;
  return ce(
    "td",
    { className: "px-3 py-2 text-right text-sm tabular-nums" },
    ce(
      "span",
      {
        className:
          "font-semibold " + (good ? "text-green-700" : "text-amber-700"),
      },
      pct + "%",
    ),
    ce(
      "span",
      { className: "text-gray-400 text-xs ml-1" },
      "(" + props.num + "/" + props.den + ")",
    ),
  );
}

function ChcSortTh(props) {
  var col = props.colKey,
    label = props.label,
    sortCol = props.sortCol,
    sortDir = props.sortDir,
    onSort = props.onSort;
  var active = sortCol === col;
  var nextDir = active && sortDir === "desc" ? "asc" : "desc";
  var icon = active ? (sortDir === "desc" ? " ↓" : " ↑") : "";
  return ce(
    "th",
    {
      className:
        "px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider bg-green-900 text-green-100 cursor-pointer select-none whitespace-nowrap hover:bg-green-800",
      onClick: function () {
        onSort(col, nextDir);
      },
    },
    label + icon,
  );
}

// =========================================================================
// Tab 1 — Audit History
// =========================================================================

function ChcAuditHistory(props) {
  var reportRows = props.reportRows;
  var entryRows = props.entryRows;
  var taskRows = props.taskRows;
  var oppIds = props.oppIds;
  var oppNames = props.oppNames || {};
  var nameMap = props.nameMap || {};

  var _state = React.useState("all");
  var oppFilter = _state[0];
  var setOppFilter = _state[1];

  var _sortState = React.useState({ col: "date_created", dir: "desc" });
  var sort = _sortState[0];
  var setSort = _sortState[1];

  function handleSort(col, dir) {
    setSort({ col: col, dir: dir });
  }

  var filtered = React.useMemo(
    function () {
      return oppFilter === "all"
        ? reportRows
        : reportRows.filter(function (r) {
            return String(r.opportunity_id) === oppFilter;
          });
    },
    [reportRows, oppFilter],
  );

  var enriched = React.useMemo(
    function () {
      return filtered.map(function (r) {
        var reportId = String(r.report_id || r.id);
        var reportOppId = String(r.opportunity_id);

        // Entries for this specific report
        var entries = entryRows.filter(function (e) {
          return (
            String(e.report_id) === reportId &&
            String(e.opportunity_id) === reportOppId
          );
        });

        var passedN = entries.filter(function (e) {
          return (
            e.is_flagged !== "1" &&
            e.is_flagged !== "true" &&
            e.is_flagged !== true
          );
        }).length;

        // Tasks are created on the same calendar day the audit was run (report.date_created).
        // Matching by period range is wrong: tasks are generated AFTER the period ends.
        var reportRunDate = (r.date_created || "").slice(0, 10);
        var workerSet = {};
        entries.forEach(function (e) {
          if (e.username) workerSet[e.username] = true;
        });
        var periodWorkerTasks = taskRows.filter(function (t) {
          return (
            t.date_created &&
            t.date_created.slice(0, 10) === reportRunDate &&
            String(t.opportunity_id) === reportOppId &&
            workerSet[t.username]
          );
        });
        var closedTasks = periodWorkerTasks.filter(function (t) {
          return t.status === "closed" || t.status === "completed";
        });
        var pendingWorkers = {};
        periodWorkerTasks.forEach(function (t) {
          if (t.status !== "closed" && t.status !== "completed") {
            pendingWorkers[t.username] = true;
          }
        });
        return {
          _r: r,
          flwCount: entries.length,
          passedN: passedN,
          totalTasks: periodWorkerTasks.length,
          closedTasks: closedTasks.length,
          pendingWorkers: Object.keys(pendingWorkers).length,
        };
      });
    },
    [filtered, entryRows, taskRows],
  );

  var sorted = React.useMemo(
    function () {
      var copy = enriched.slice();
      copy.sort(function (a, b) {
        var r = a._r,
          s = b._r;
        var va, vb;
        if (sort.col === "date_created") {
          va = r.date_created || "";
          vb = s.date_created || "";
        } else if (sort.col === "period") {
          va = r.period_start || "";
          vb = s.period_start || "";
        } else if (sort.col === "flws") {
          va = a.flwCount;
          vb = b.flwCount;
        } else if (sort.col === "status") {
          va = r.status || "";
          vb = s.status || "";
        } else if (sort.col === "passed") {
          va = a.flwCount ? a.passedN / a.flwCount : -1;
          vb = b.flwCount ? b.passedN / b.flwCount : -1;
        } else if (sort.col === "tasks") {
          va = a.totalTasks ? a.closedTasks / a.totalTasks : -1;
          vb = b.totalTasks ? b.closedTasks / b.totalTasks : -1;
        } else if (sort.col === "ptask") {
          va = a.flwCount ? a.pendingWorkers / a.flwCount : -1;
          vb = b.flwCount ? b.pendingWorkers / b.flwCount : -1;
        } else if (sort.col === "runby") {
          va =
            nameMap[r.completed_by_username] ||
            r.completed_by_username ||
            "";
          vb =
            nameMap[s.completed_by_username] ||
            s.completed_by_username ||
            "";
        } else {
          va = "";
          vb = "";
        }
        if (typeof va === "number" && typeof vb === "number")
          return sort.dir === "asc" ? va - vb : vb - va;
        return sort.dir === "asc"
          ? String(va).localeCompare(String(vb))
          : String(vb).localeCompare(String(va));
      });
      return copy;
    },
    [enriched, sort, nameMap],
  );

  var thProps = { sortCol: sort.col, sortDir: sort.dir, onSort: handleSort };

  return ce(
    "div",
    { className: "p-4 space-y-3" },
    // Filter bar
    ce(
      "div",
      { className: "flex items-center gap-3 flex-wrap" },
      ce(
        "span",
        {
          className:
            "text-xs font-semibold uppercase tracking-wider text-gray-500",
        },
        "Opportunity",
      ),
      ce(
        "select",
        {
          className:
            "text-sm border border-gray-300 rounded px-2 py-1.5 bg-white",
          value: oppFilter,
          onChange: function (e) {
            setOppFilter(e.target.value);
          },
        },
        ce("option", { value: "all" }, "All Opportunities"),
        oppIds.map(function (id) {
          return ce(
            "option",
            { key: id, value: String(id) },
            oppNames[id] || "Opp #" + id,
          );
        }),
      ),
      ce(
        "span",
        { className: "text-xs text-gray-400" },
        sorted.length + " report" + (sorted.length !== 1 ? "s" : ""),
      ),
    ),
    // Table
    ce(
      "div",
      { className: "rounded-lg overflow-hidden shadow-sm border border-gray-200" },
      ce(
        "div",
        { className: "overflow-x-auto" },
        ce(
          "table",
          { className: "w-full border-collapse bg-white text-sm" },
          ce(
            "thead",
            null,
            ce(
              "tr",
              null,
              ce(
                ChcSortTh,
                Object.assign(
                  { colKey: "date_created", label: "Created Date" },
                  thProps,
                ),
              ),
              ce(
                ChcSortTh,
                Object.assign(
                  { colKey: "period", label: "Audit Period" },
                  thProps,
                ),
              ),
              ce(
                ChcSortTh,
                Object.assign({ colKey: "flws", label: "FLWs" }, thProps),
              ),
              ce(
                ChcSortTh,
                Object.assign(
                  { colKey: "status", label: "Status" },
                  thProps,
                ),
              ),
              ce(
                ChcSortTh,
                Object.assign(
                  { colKey: "passed", label: "% FLWs Passed" },
                  thProps,
                ),
              ),
              ce(
                ChcSortTh,
                Object.assign(
                  { colKey: "tasks", label: "% Tasks Completed" },
                  thProps,
                ),
              ),
              ce(
                ChcSortTh,
                Object.assign(
                  { colKey: "ptask", label: "% FLWs w/ Pending Task" },
                  thProps,
                ),
              ),
              ce(
                ChcSortTh,
                Object.assign({ colKey: "runby", label: "Run By" }, thProps),
              ),
            ),
          ),
          ce(
            "tbody",
            null,
            sorted.length === 0
              ? ce(
                  "tr",
                  null,
                  ce(
                    "td",
                    {
                      colSpan: 8,
                      className: "px-4 py-8 text-center text-gray-400",
                    },
                    "No audit reports found",
                  ),
                )
              : sorted.map(function (row, i) {
                  var r = row._r;
                  var completed = r.status === "completed";
                  return ce(
                    "tr",
                    {
                      key: i,
                      className:
                        "border-b border-gray-100 hover:bg-gray-50" +
                        (!completed ? " text-gray-500" : ""),
                    },
                    ce(
                      "td",
                      { className: "px-3 py-2 font-medium whitespace-nowrap" },
                      (r.date_created || "—").slice(0, 10),
                    ),
                    ce(
                      "td",
                      { className: "px-3 py-2 whitespace-nowrap" },
                      chcDateRange(r.period_start, r.period_end),
                    ),
                    ce(
                      "td",
                      { className: "px-3 py-2 text-right tabular-nums" },
                      completed ? row.flwCount : "—",
                    ),
                    ce("td", { className: "px-3 py-2" }, ce(ChcPill, { status: r.status })),
                    ce(ChcPctCell, { num: row.passedN, den: row.flwCount }),
                    ce(ChcPctCell, {
                      num: row.closedTasks,
                      den: row.totalTasks,
                    }),
                    ce(ChcPctCell, {
                      num: row.pendingWorkers,
                      den: row.flwCount,
                      higherIsBetter: false,
                    }),
                    ce(
                      "td",
                      { className: "px-3 py-2 text-sm" },
                      nameMap[r.completed_by_username] ||
                        r.completed_by_username ||
                        "—",
                    ),
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
// Rows: one per (username, opportunity_id) — each FLW once per opp.
// Columns: Opportunity | Worker | camping | gender | muac_photo | age_heaping |
//          wa_coverage | inaccess_early | inaccess_last | vaccine | vaccine_card | mdpi | Flags
// Flags = count of metrics where in_range === false (with sufficient data).
// =========================================================================

function ChcMetricDetail(props) {
  var reportRows = props.reportRows;
  var entryRows = props.entryRows;
  var oppIds = props.oppIds;
  var oppNames = props.oppNames || {};
  var nameMap = props.nameMap || {};

  var _repState = React.useState("all");
  var repFilter = _repState[0];
  var setRepFilter = _repState[1];

  var _oppState = React.useState("all");
  var oppFilter = _oppState[0];
  var setOppFilter = _oppState[1];

  var _sortState = React.useState({ col: "opportunity", dir: "asc" });
  var mdSort = _sortState[0];
  var setMdSort = _sortState[1];

  var completedReports = React.useMemo(
    function () {
      return reportRows.filter(function (r) {
        return r.status === "completed";
      });
    },
    [reportRows],
  );

  // Filter entries by opp and/or report selection
  var filteredEntries = React.useMemo(
    function () {
      var rows = entryRows;
      if (oppFilter !== "all") {
        rows = rows.filter(function (e) {
          return String(e.opportunity_id) === oppFilter;
        });
      }
      if (repFilter !== "all") {
        // repFilter is "reportId:oppId"
        var parts = repFilter.split(":");
        var rId = parts[0];
        var rOppId = parts[1];
        rows = rows.filter(function (e) {
          return (
            String(e.report_id) === rId &&
            String(e.opportunity_id) === rOppId
          );
        });
      }
      return rows;
    },
    [entryRows, oppFilter, repFilter],
  );

  // One row per (username, opportunity_id) — latest entry if multiple exist
  var flwRows = React.useMemo(
    function () {
      var byKey = {};
      filteredEntries.forEach(function (e) {
        if (!e.username) return;
        var key = e.username + ":" + String(e.opportunity_id);
        // Keep latest by date_created
        if (!byKey[key] || e.date_created > byKey[key].date_created) {
          byKey[key] = e;
        }
      });
      return Object.values(byKey);
    },
    [filteredEntries],
  );

  // Sort
  var sortedFlwRows = React.useMemo(
    function () {
      var copy = flwRows.slice();
      copy.sort(function (a, b) {
        var col = mdSort.col;
        var dir = mdSort.dir;
        var va, vb;
        if (col === "opportunity") {
          va = oppNames[a.opportunity_id] || String(a.opportunity_id) || "";
          vb = oppNames[b.opportunity_id] || String(b.opportunity_id) || "";
          return dir === "asc"
            ? va.localeCompare(vb)
            : vb.localeCompare(va);
        } else if (col === "worker") {
          va = nameMap[a.username] || a.username || "";
          vb = nameMap[b.username] || b.username || "";
          return dir === "asc"
            ? va.localeCompare(vb)
            : vb.localeCompare(va);
        } else if (col === "flags") {
          va = countMetricFlags(a.results);
          vb = countMetricFlags(b.results);
          return dir === "asc" ? va - vb : vb - va;
        } else {
          // metric column key
          var resA = chcParseResults(a.results);
          var resB = chcParseResults(b.results);
          var objA = resA[col];
          var objB = resB[col];
          var numA =
            objA && objA.has_sufficient_data && objA.value != null
              ? objA.value
              : null;
          var numB =
            objB && objB.has_sufficient_data && objB.value != null
              ? objB.value
              : null;
          if (numA === null && numB === null) return 0;
          if (numA === null) return 1; // N/A to bottom
          if (numB === null) return -1;
          return dir === "asc" ? numA - numB : numB - numA;
        }
      });
      return copy;
    },
    [flwRows, mdSort, oppNames, nameMap],
  );

  // Per-column flag counts (across sorted rows)
  var colFlagCounts = React.useMemo(
    function () {
      return CHC_METRICS.map(function (m) {
        var count = 0;
        sortedFlwRows.forEach(function (row) {
          var res = chcParseResults(row.results);
          var obj = res[m.key];
          if (obj && obj.has_sufficient_data && obj.in_range === false)
            count++;
        });
        return count;
      });
    },
    [sortedFlwRows],
  );

  var totalFlags = colFlagCounts.reduce(function (a, b) {
    return a + b;
  }, 0);

  function handleSort(col, dir) {
    setMdSort({ col: col, dir: dir });
  }

  var thProps = {
    sortCol: mdSort.col,
    sortDir: mdSort.dir,
    onSort: handleSort,
  };

  return ce(
    "div",
    { className: "p-4 space-y-3" },
    // Filters
    ce(
      "div",
      { className: "flex items-center gap-3 flex-wrap" },
      ce(
        "span",
        {
          className:
            "text-xs font-semibold uppercase tracking-wider text-gray-500",
        },
        "Report",
      ),
      ce(
        "select",
        {
          className:
            "text-sm border border-gray-300 rounded px-2 py-1.5 bg-white",
          value: repFilter,
          onChange: function (e) {
            setRepFilter(e.target.value);
          },
        },
        ce("option", { value: "all" }, "All Completed Audits"),
        completedReports.map(function (r, i) {
          var val =
            String(r.report_id || r.id) + ":" + String(r.opportunity_id);
          var label =
            (oppNames[r.opportunity_id] || "Opp #" + r.opportunity_id) +
            " · " +
            chcDateRange(r.period_start, r.period_end);
          return ce("option", { key: i, value: val }, label);
        }),
      ),
      ce(
        "span",
        {
          className:
            "text-xs font-semibold uppercase tracking-wider text-gray-500",
        },
        "Opportunity",
      ),
      ce(
        "select",
        {
          className:
            "text-sm border border-gray-300 rounded px-2 py-1.5 bg-white",
          value: oppFilter,
          onChange: function (e) {
            setOppFilter(e.target.value);
          },
        },
        ce("option", { value: "all" }, "All Opportunities"),
        oppIds.map(function (id) {
          return ce(
            "option",
            { key: id, value: String(id) },
            oppNames[id] || "Opp #" + id,
          );
        }),
      ),
      ce(
        "span",
        { className: "text-xs text-gray-400" },
        sortedFlwRows.length +
          " worker" +
          (sortedFlwRows.length !== 1 ? "s" : ""),
      ),
    ),
    // Table
    ce(
      "div",
      {
        className:
          "rounded-lg overflow-hidden shadow-sm border border-gray-200",
      },
      ce(
        "div",
        { className: "overflow-x-auto" },
        ce(
          "table",
          { className: "border-collapse bg-white text-xs" },
          ce(
            "thead",
            null,
            ce(
              "tr",
              null,
              ce(
                ChcSortTh,
                Object.assign(
                  {
                    colKey: "opportunity",
                    label: "Opportunity",
                    style: { position: "sticky", left: 0 },
                  },
                  thProps,
                ),
              ),
              ce(
                ChcSortTh,
                Object.assign(
                  {
                    colKey: "worker",
                    label: "Connect Worker",
                    style: { position: "sticky", left: "80px" },
                  },
                  thProps,
                ),
              ),
              CHC_METRICS.map(function (m) {
                return ce(
                  ChcSortTh,
                  Object.assign(
                    {
                      key: m.key,
                      colKey: m.key,
                      label: m.label,
                      style: { whiteSpace: "normal", maxWidth: "80px" },
                    },
                    thProps,
                  ),
                );
              }),
              ce(
                ChcSortTh,
                Object.assign({ colKey: "flags", label: "Flags" }, thProps),
              ),
            ),
          ),
          ce(
            "tbody",
            null,
            sortedFlwRows.length === 0
              ? ce(
                  "tr",
                  null,
                  ce(
                    "td",
                    {
                      colSpan: CHC_METRICS.length + 3,
                      className: "px-4 py-8 text-center text-gray-400",
                    },
                    "No data for current filters",
                  ),
                )
              : sortedFlwRows.map(function (row, i) {
                  var res = chcParseResults(row.results);
                  var rowFlagCount = 0;
                  var cells = CHC_METRICS.map(function (m) {
                    var obj = res[m.key];
                    var isNA = !obj || !obj.has_sufficient_data || obj.value == null;
                    var isFlagged = !isNA && obj.in_range === false;
                    if (isFlagged) rowFlagCount++;
                    var displayVal = isNA ? "N/A" : fmtMetricVal(obj);
                    return ce(
                      "td",
                      {
                        key: m.key,
                        title: m.full,
                        className:
                          "border border-gray-200 px-2 py-1.5 text-center tabular-nums " +
                          (isFlagged
                            ? "bg-red-100 text-red-800 font-semibold"
                            : isNA
                              ? "text-gray-400"
                              : ""),
                      },
                      displayVal,
                    );
                  });
                  return ce(
                    "tr",
                    { key: i, className: "hover:bg-gray-50" },
                    ce(
                      "td",
                      {
                        className:
                          "border border-gray-200 px-3 py-1.5 text-left whitespace-nowrap font-medium text-green-900 bg-white",
                      },
                      oppNames[row.opportunity_id] ||
                        "Opp #" + row.opportunity_id,
                    ),
                    ce(
                      "td",
                      {
                        className:
                          "border border-gray-200 px-3 py-1.5 text-left font-medium whitespace-nowrap bg-white",
                      },
                      nameMap[row.username] || row.username,
                    ),
                    cells,
                    ce(
                      "td",
                      {
                        className:
                          "border border-gray-200 px-2 py-1.5 text-center font-bold bg-green-900 text-white",
                      },
                      rowFlagCount,
                    ),
                  );
                }),
          ),
          // Footer: per-column flag totals
          ce(
            "tfoot",
            null,
            ce(
              "tr",
              null,
              ce(
                "td",
                {
                  colSpan: 2,
                  className:
                    "border border-gray-200 px-3 py-1.5 text-left font-bold text-xs uppercase bg-green-50 text-green-800",
                },
                "Metric Totals",
              ),
              colFlagCounts.map(function (n, i) {
                return ce(
                  "td",
                  {
                    key: i,
                    className:
                      "border border-gray-200 px-2 py-1.5 text-center font-bold text-xs " +
                      (n > 0
                        ? "bg-red-100 text-red-800"
                        : "bg-green-50 text-green-700"),
                  },
                  n,
                );
              }),
              ce(
                "td",
                {
                  className:
                    "border border-gray-200 px-2 py-1.5 text-center font-bold bg-green-900 text-white text-xs",
                },
                totalFlags,
              ),
            ),
          ),
        ),
      ),
    ),
  );
}

// =========================================================================
// Tab 3 — FLW Longitudinal
// Uses the same metric flag logic as Metric Detail (in_range === false).
// =========================================================================

function ChcFLWLongitudinal(props) {
  var reportRows = props.reportRows;
  var entryRows = props.entryRows;
  var taskRows = props.taskRows;
  var workers = props.workers;
  var oppIds = props.oppIds;
  var oppNames = props.oppNames || {};
  var nameMap = props.nameMap || {};

  var _oppState = React.useState("all");
  var oppFilter = _oppState[0];
  var setOppFilter = _oppState[1];

  // Completed cycles (grouped by period_start across all opps)
  var cycles = React.useMemo(
    function () {
      var filteredReports =
        oppFilter === "all"
          ? reportRows
          : reportRows.filter(function (r) {
              return String(r.opportunity_id) === oppFilter;
            });
      var completedReports = filteredReports.filter(function (r) {
        return r.status === "completed";
      });
      var periodMap = {};
      completedReports.forEach(function (r) {
        var key = (r.period_start || "").slice(0, 10);
        if (!periodMap[key]) periodMap[key] = [];
        periodMap[key].push(r);
      });
      return Object.keys(periodMap)
        .sort(function (a, b) {
          return b.localeCompare(a);
        })
        .map(function (key) {
          return { period_start: key, reports: periodMap[key] };
        });
    },
    [reportRows, oppFilter],
  );

  // Unique FLW usernames sorted by display name
  var flwList = React.useMemo(
    function () {
      var seen = {};
      var list = [];
      entryRows.forEach(function (e) {
        if (e.username && !seen[e.username]) {
          seen[e.username] = true;
          list.push(e.username);
        }
      });
      (workers || []).forEach(function (w) {
        if (w.username && !seen[w.username]) {
          seen[w.username] = true;
          list.push(w.username);
        }
      });
      return list.sort(function (a, b) {
        return (nameMap[a] || a).localeCompare(nameMap[b] || b);
      });
    },
    [entryRows, workers, nameMap],
  );

  // Per-FLW per-cycle stats using the same flag logic as Metric Detail
  var flwCycleData = React.useMemo(
    function () {
      return flwList.map(function (username) {
        var totalFlags = 0;
        var cycleStats = cycles.map(function (cycle) {
          var cycleReports = cycle.reports;
          var entries = entryRows.filter(function (e) {
            return (
              e.username === username &&
              cycleReports.some(function (r) {
                return (
                  String(e.report_id) === String(r.report_id || r.id) &&
                  String(e.opportunity_id) === String(r.opportunity_id)
                );
              })
            );
          });
          if (!entries.length) return null;

          // Count metric flags: in_range=false with sufficient data
          var metricFlags = 0;
          entries.forEach(function (e) {
            metricFlags += countMetricFlags(e.results);
          });
          var totalMetricsWithData = 0;
          entries.forEach(function (e) {
            var res = chcParseResults(e.results);
            CHC_METRICS.forEach(function (m) {
              var obj = res[m.key];
              if (obj && obj.has_sufficient_data) totalMetricsWithData++;
            });
          });

          // Tasks for this FLW in this cycle (matched by report run date)
          var cycleTasks = [];
          cycleReports.forEach(function (r) {
            var reportRunDate = (r.date_created || "").slice(0, 10);
            var reportOppId = String(r.opportunity_id);
            taskRows
              .filter(function (t) {
                return (
                  t.username === username &&
                  t.date_created &&
                  t.date_created.slice(0, 10) === reportRunDate &&
                  String(t.opportunity_id) === reportOppId
                );
              })
              .forEach(function (t) {
                cycleTasks.push(t);
              });
          });

          var closedTasks = cycleTasks.filter(function (t) {
            return t.status === "closed" || t.status === "completed";
          }).length;

          totalFlags += metricFlags;
          return {
            metricFlags: metricFlags,
            totalMetricsWithData: totalMetricsWithData,
            totalTasks: cycleTasks.length,
            closedTasks: closedTasks,
          };
        });
        return { username: username, totalFlags: totalFlags, cycles: cycleStats };
      });
    },
    [flwList, cycles, entryRows, taskRows],
  );

  function severityCls(flags) {
    if (flags === 0) return "";
    if (flags === 1) return "bg-amber-50";
    if (flags === 2) return "bg-amber-100";
    return "bg-amber-200";
  }

  return ce(
    "div",
    { className: "p-4 space-y-3" },
    // Filter bar
    ce(
      "div",
      { className: "flex items-center gap-3 flex-wrap" },
      ce(
        "span",
        {
          className:
            "text-xs font-semibold uppercase tracking-wider text-gray-500",
        },
        "Opportunity",
      ),
      ce(
        "select",
        {
          className:
            "text-sm border border-gray-300 rounded px-2 py-1.5 bg-white",
          value: oppFilter,
          onChange: function (e) {
            setOppFilter(e.target.value);
          },
        },
        ce("option", { value: "all" }, "All Opportunities"),
        oppIds.map(function (id) {
          return ce(
            "option",
            { key: id, value: String(id) },
            oppNames[id] || "Opp #" + id,
          );
        }),
      ),
    ),
    // Legend
    ce(
      "div",
      { className: "flex gap-3 text-xs text-gray-500 flex-wrap items-center" },
      ["0 flags", "1 flag", "2 flags", "3+ flags"].map(function (lbl, i) {
        var bg = [
          "bg-white border-gray-200",
          "bg-amber-50 border-amber-200",
          "bg-amber-100 border-amber-300",
          "bg-amber-200 border-amber-400",
        ][i];
        return ce(
          "span",
          { key: i, className: "flex items-center gap-1" },
          ce("span", {
            className: "inline-block w-3 h-3 rounded border " + bg,
          }),
          lbl,
        );
      }),
      ce(
        "span",
        { className: "text-green-700 font-medium ml-2" },
        "% metrics passed",
      ),
      ce(
        "span",
        { className: "text-amber-700 font-medium" },
        "% tasks done",
      ),
    ),
    // Table
    cycles.length === 0
      ? ce(
          "div",
          {
            className:
              "rounded-lg border border-gray-200 p-8 text-center text-gray-400 text-sm bg-white",
          },
          "No completed audit cycles found",
        )
      : ce(
          "div",
          {
            className:
              "rounded-lg overflow-hidden shadow-sm border border-gray-200",
          },
          ce(
            "div",
            { className: "overflow-x-auto" },
            ce(
              "table",
              { className: "border-collapse bg-white text-xs w-full" },
              ce(
                "thead",
                null,
                ce(
                  "tr",
                  null,
                  ce(
                    "th",
                    {
                      className:
                        "sticky left-0 z-10 bg-green-900 text-white px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider whitespace-nowrap min-w-40",
                      style: { position: "sticky", left: 0 },
                    },
                    "FLW",
                  ),
                  ce(
                    "th",
                    {
                      className:
                        "bg-green-900 text-white px-3 py-2 text-center text-xs font-semibold uppercase tracking-wider whitespace-nowrap min-w-20",
                    },
                    "Total Flags",
                  ),
                  cycles.map(function (c, i) {
                    var r = c.reports[0];
                    return ce(
                      "th",
                      {
                        key: i,
                        className:
                          "bg-green-800 text-green-100 px-3 py-2 text-center font-semibold min-w-32",
                      },
                      chcDateRange(r.period_start, r.period_end),
                    );
                  }),
                ),
              ),
              ce(
                "tbody",
                null,
                flwCycleData.map(function (row, i) {
                  return ce(
                    "tr",
                    { key: i, className: "border-b border-gray-100" },
                    ce(
                      "td",
                      {
                        className:
                          "border-r border-gray-200 px-3 py-2 font-medium whitespace-nowrap sticky left-0 bg-gray-50 z-10",
                        style: { position: "sticky", left: 0 },
                      },
                      nameMap[row.username] || row.username,
                    ),
                    ce(
                      "td",
                      {
                        className:
                          "border-r border-gray-200 px-3 py-2 text-center font-bold " +
                          (row.totalFlags > 0
                            ? "text-red-700"
                            : "text-gray-400"),
                      },
                      row.totalFlags,
                    ),
                    row.cycles.map(function (cs, j) {
                      if (!cs) {
                        return ce(
                          "td",
                          {
                            key: j,
                            className:
                              "border-r border-gray-200 px-3 py-2 text-center text-gray-300",
                          },
                          "—",
                        );
                      }
                      var mPct =
                        cs.totalMetricsWithData > 0
                          ? Math.round(
                              ((cs.totalMetricsWithData - cs.metricFlags) /
                                cs.totalMetricsWithData) *
                                100,
                            )
                          : 100;
                      var tPct =
                        cs.totalTasks > 0
                          ? Math.round(
                              (cs.closedTasks / cs.totalTasks) * 100,
                            )
                          : null;
                      return ce(
                        "td",
                        {
                          key: j,
                          className:
                            "border-r border-gray-200 px-3 py-2 " +
                            severityCls(cs.metricFlags),
                        },
                        ce(
                          "div",
                          {
                            className:
                              "flex flex-col items-center gap-0.5",
                          },
                          ce(
                            "span",
                            {
                              className:
                                cs.metricFlags > 0
                                  ? "font-semibold text-red-700"
                                  : "font-semibold text-green-800",
                            },
                            mPct + "% passed",
                          ),
                          ce("div", {
                            className:
                              "w-16 h-1.5 bg-gray-200 rounded overflow-hidden",
                          },
                            ce("div", {
                              className:
                                "h-full rounded " +
                                (cs.metricFlags > 0
                                  ? "bg-red-500"
                                  : "bg-green-600"),
                              style: { width: mPct + "%" },
                            }),
                          ),
                          tPct != null
                            ? ce(
                                "span",
                                { className: "text-amber-700" },
                                tPct + "% tasks",
                              )
                            : ce(
                                "span",
                                { className: "text-gray-400" },
                                "no tasks",
                              ),
                        ),
                      );
                    }),
                  );
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
  var definition = props.definition;
  var workers = props.workers;
  var pipelines = props.pipelines;
  var view = props.view;

  var _tabState = React.useState(0);
  var activeTab = _tabState[0];
  var setTab = _tabState[1];

  var oppIds = React.useMemo(
    function () {
      return (definition && definition.opportunity_ids) || [];
    },
    [definition],
  );

  var oppNames = React.useMemo(
    function () {
      var m = {};
      try {
        var el = document.getElementById("user-opportunities");
        if (el)
          JSON.parse(el.textContent).forEach(function (o) {
            m[o.id] = o.name;
          });
      } catch (e) {
        console.error("CHC: failed to parse user-opportunities", e);
      }
      return m;
    },
    [],
  );

  var nameMap = React.useMemo(
    function () {
      var m = {};
      (workers || []).forEach(function (w) {
        if (w.username) m[w.username] = w.name || w.username;
      });
      return m;
    },
    [workers],
  );

  var srcPipelines = React.useMemo(
    function () {
      return (view && view.pipelines) || pipelines || {};
    },
    [view, pipelines],
  );

  var reportRows = React.useMemo(
    function () {
      return (
        (srcPipelines.audit_reports && srcPipelines.audit_reports.rows) || []
      );
    },
    [srcPipelines],
  );

  var entryRows = React.useMemo(
    function () {
      return (
        (srcPipelines.audit_entries && srcPipelines.audit_entries.rows) || []
      );
    },
    [srcPipelines],
  );

  var taskRows = React.useMemo(
    function () {
      return (srcPipelines.tasks && srcPipelines.tasks.rows) || [];
    },
    [srcPipelines],
  );

  var tabs = ["Audit History", "Metric Detail", "FLW Longitudinal"];

  return ce(
    "div",
    { className: "min-h-screen bg-gray-50" },
    // Header
    ce(
      "div",
      { className: "bg-green-900 text-white px-6 py-4" },
      ce(
        "div",
        { className: "text-xs uppercase tracking-widest opacity-60 mb-1" },
        "Program 176 · DIMAGI-CHC-RCT · Nigeria",
      ),
      ce(
        "div",
        { className: "text-xl font-bold tracking-tight" },
        "CHC Audit History",
      ),
      ce(
        "div",
        { className: "flex gap-2 mt-2 flex-wrap" },
        oppIds.map(function (id) {
          return ce(
            "span",
            {
              key: id,
              className:
                "text-xs px-2 py-0.5 bg-white/10 border border-white/20 rounded",
            },
            oppNames[id] || "Opp #" + id,
          );
        }),
      ),
    ),
    // Tabs
    ce(
      "div",
      { className: "bg-white border-b border-gray-200 flex px-4" },
      tabs.map(function (lbl, i) {
        return ce(
          "button",
          {
            key: i,
            className:
              "px-4 py-3 text-sm font-medium border-b-2 -mb-px " +
              (activeTab === i
                ? "text-green-800 border-green-700 font-semibold"
                : "text-gray-500 border-transparent hover:text-green-800"),
            onClick: function () {
              setTab(i);
            },
          },
          lbl,
        );
      }),
    ),
    // Tab content
    activeTab === 0 &&
      ce(ChcAuditHistory, {
        reportRows: reportRows,
        entryRows: entryRows,
        taskRows: taskRows,
        oppIds: oppIds,
        oppNames: oppNames,
        nameMap: nameMap,
      }),
    activeTab === 1 &&
      ce(ChcMetricDetail, {
        reportRows: reportRows,
        entryRows: entryRows,
        oppIds: oppIds,
        oppNames: oppNames,
        nameMap: nameMap,
      }),
    activeTab === 2 &&
      ce(ChcFLWLongitudinal, {
        reportRows: reportRows,
        entryRows: entryRows,
        taskRows: taskRows,
        workers: workers,
        oppIds: oppIds,
        oppNames: oppNames,
        nameMap: nameMap,
      }),
  );
}
