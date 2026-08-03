"""FLW Daily Indicator Table — Program 176 (CHC PRE-RCT Nigeria).

Workflow 2 of a two-workflow pair (see flw_daily_indicator_report.py,
"Workflow 1"). Reads Workflow 1's saved daily snapshots (one completed
WorkflowRun per opportunity per day) via a dedicated read-only API endpoint
(api/flw-daily-indicator-history/) and displays them as a 14-day grid: one
row per FLW, one column per day, a single 0/1 "investigate today" flag per
cell. Clicking an FLW's name expands an inline detail table -- same day
columns as the row above it, one row per indicator -- showing every raw
indicator value alongside its threshold, with over-threshold values
highlighted (red for indicators that contribute to the flag, orange for
those that don't), so it's immediately clear WHICH indicator(s) tripped the
flag on which day, and which merely warrant a look. Each indicator label has
an (i) hover tooltip with a full description (see INDICATOR_DEFS'
`description` field) -- a small InfoTooltip popover, not the native `title=`
attribute, mirroring flw_audit_trend_dashboard.py's own workaround for
native title= not rendering reliably inside this runner's iframe context.

All evaluated indicators' thresholds live in the THRESHOLDS constant below
-- by design, per the user's explicit requirement that thresholds be tunable
here without touching or recomputing Workflow 1's history. Retuning a
threshold is a one-line render_code edit; it takes effect immediately on next
page load, no backfill needed, since the raw values are already stored. Only
indicators marked `contributes: true` in INDICATOR_DEFS roll up into the main
grid's per-day flag; the rest (plus the two purely informational entries,
HSD forms submitted and unique work areas visited) still compute and
highlight their own tripped state, just not in red.

No pipeline_schema: like flw_audit_trend_dashboard.py, this template never
reads CommCare/Connect visit data directly -- everything comes from Workflow
1's already-computed history via a plain browser fetch(), same
cross-workflow-read pattern used elsewhere in this codebase.

config.source_definition_id (set on the instance after creation, since
Workflow 1's id is only known once it exists) points at Workflow 1's
definition id -- same two-step dance flw_audit_trend_dashboard.py uses.
"""
from __future__ import annotations

DEFINITION = {
    "name": "FLW Daily Indicator Table",
    "description": (
        "Program 176 (CHC PRE-RCT Nigeria) 14-day per-FLW daily indicator grid, sourced from the "
        "FLW Daily Indicator Report's saved daily snapshots. One 0/1 flag per FLW per day; expand a "
        "row to see every indicator + threshold for every day and exactly which ones tripped."
    ),
    "version": 1,
    "templateType": "flw_daily_indicator_table",
    "statuses": [],
    "config": {
        "showSummaryCards": False,
        "showFilters": False,
        "source_definition_id": None,
    },
    "pipeline_sources": [],
}

RENDER_CODE = r"""function WorkflowUI({ definition, workers }) {
    var sourceDefinitionId = definition.config && definition.config.source_definition_id;
    var NUM_DAYS = 14;
    var FETCH_DAYS = 18; // small buffer past 14 for schedule/timezone slack

    // Thresholds for the evaluated indicators (the informational entries --
    // total forms and unique work areas -- never trip anything). These are
    // the ONLY place thresholds live -- retune here, no changes to Workflow 1
    // needed.
    var THRESHOLDS = {
        households_per_building: 5,          // flag if any WA's households/building ratio > this
        households_4plus_children: 2,        // flag if count > this
        gap_lt_2min: 15,                      // flag if count >= this
        vaccine_yes_pct: 50,                  // flag if % received_any_vaccine=yes < this
        camping_repeat_pct: 80,               // flag if % of forms sharing the same exact GPS reading >= this
        duplicate_child_names: 2,             // flag if count >= this
        duplicate_child_ages: 2,              // flag if count >= this
        straight_line_pct: 95,                // flag if either straight-lining field's mode share >= this
        muac_repetition_pct: 30,              // flag if MUAC value repetition % >= this
        min_forms_for_compression: 30,        // "30+ visits" half of the compression check
        max_span_minutes_for_compression: 60, // "<=1hr" half of the compression check
    };

    var oppNames = React.useMemo(function () {
        var m = {};
        try {
            var el = document.getElementById("user-opportunities");
            if (el) JSON.parse(el.textContent).forEach(function (o) { m[o.id] = o.name; });
        } catch (e) { console.error("FLW daily indicator table: failed to parse user-opportunities", e); }
        return m;
    }, []);

    var nameMap = React.useMemo(function () {
        var m = {};
        (workers || []).forEach(function (w) { if (w.username) m[w.username] = w.name || w.username; });
        return m;
    }, [workers]);

    var _state = React.useState({ loading: true, error: null, days: [] });
    var state = _state[0];
    var setState = _state[1];

    React.useEffect(function () {
        if (!sourceDefinitionId) {
            setState({ loading: false, error: "No source_definition_id configured on this workflow.", days: [] });
            return;
        }
        var url = "/labs/workflow/api/flw-daily-indicator-history/?definition_id=" + sourceDefinitionId;
        fetch(url, { credentials: "same-origin" })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.error) {
                    setState({ loading: false, error: data.error, days: [] });
                } else {
                    setState({ loading: false, error: null, days: data.days || [] });
                }
            })
            .catch(function (e) {
                setState({ loading: false, error: String(e), days: [] });
            });
    }, [sourceDefinitionId]);

    // "Yesterday" in Africa/Lagos (UTC+1, no DST) -- matches flw_daily_indicator_report's
    // own definition of the calendar day a run covers, since today's report hasn't run yet.
    // This is the default LAST column when the user hasn't picked a reference date below.
    var autoEndDate = React.useMemo(function () {
        var now = new Date();
        var watNow = new Date(now.getTime() + 60 * 60 * 1000);
        var end = new Date(Date.UTC(watNow.getUTCFullYear(), watNow.getUTCMonth(), watNow.getUTCDate() - 1));
        return end.toISOString().slice(0, 10);
    }, []);

    // Reference-date filter: the user picks a single date (e.g. a Sunday) and
    // the grid always shows a FIXED-size window of NUM_DAYS days ending on that
    // date -- the column count never changes, only which days it covers.
    var _endDate = React.useState(null); // null = use autoEndDate (today's default)
    var endDate = _endDate[0];
    var setEndDate = _endDate[1];
    var effectiveEndDate = endDate || autoEndDate;

    var dayColumns = React.useMemo(function () {
        var end = new Date(effectiveEndDate + "T00:00:00Z");
        var days = [];
        for (var i = NUM_DAYS - 1; i >= 0; i--) {
            var d = new Date(end);
            d.setUTCDate(d.getUTCDate() - i);
            days.push(d.toISOString().slice(0, 10));
        }
        return days;
    }, [effectiveEndDate]);

    var earliestNeeded = dayColumns[0];
    var latestNeeded = dayColumns[dayColumns.length - 1];

    var rows = React.useMemo(function () {
        var rowMap = {};
        state.days.forEach(function (d) {
            // Outside the selected window on EITHER side -- both bounds matter:
            // without the upper one, picking an older reference date would still
            // pull in FLWs from newer data (their row would appear with every
            // cell empty), since only the lower bound was ever checked here.
            if (!d.date || d.date < earliestNeeded || d.date > latestNeeded) return;
            (d.flws || []).forEach(function (f) {
                var rk = d.opportunity_id + "::" + f.username;
                if (!rowMap[rk]) {
                    rowMap[rk] = { opportunity_id: d.opportunity_id, username: f.username, byDate: {} };
                }
                rowMap[rk].byDate[d.date] = f;
            });
        });
        return Object.keys(rowMap)
            .map(function (k) { return rowMap[k]; })
            .sort(function (a, b) {
                var nameA = nameMap[a.username] || a.username;
                var nameB = nameMap[b.username] || b.username;
                return nameA.localeCompare(nameB);
            });
    }, [state.days, earliestNeeded, latestNeeded, nameMap]);

    var _lloFilter = React.useState("__all__");
    var lloFilter = _lloFilter[0];
    var setLloFilter = _lloFilter[1];
    var _flwFilter = React.useState("__all__");
    var flwFilter = _flwFilter[0];
    var setFlwFilter = _flwFilter[1];
    var _expanded = React.useState({});
    var expanded = _expanded[0];
    var setExpanded = _expanded[1];

    var lloOptions = React.useMemo(function () {
        var seen = {};
        rows.forEach(function (r) { seen[r.opportunity_id] = lloName(oppNames[r.opportunity_id]) || ("Opportunity " + r.opportunity_id); });
        return Object.keys(seen).map(Number).map(function (id) { return { value: id, label: seen[id] }; })
            .sort(function (a, b) { return a.label.localeCompare(b.label); });
    }, [rows, oppNames]);

    var flwOptions = React.useMemo(function () {
        var seen = {};
        rows.forEach(function (r) {
            if (lloFilter !== "__all__" && String(r.opportunity_id) !== lloFilter) return;
            seen[r.username] = nameMap[r.username] || r.username;
        });
        return Object.keys(seen).map(function (u) { return { value: u, label: seen[u] }; })
            .sort(function (a, b) { return a.label.localeCompare(b.label); });
    }, [rows, lloFilter, nameMap]);

    var filteredRows = rows.filter(function (r) {
        if (lloFilter !== "__all__" && String(r.opportunity_id) !== lloFilter) return false;
        if (flwFilter !== "__all__" && r.username !== flwFilter) return false;
        return true;
    });

    if (state.loading) return <div className="p-6 text-gray-600">Loading daily indicator history...</div>;
    if (state.error) return <div className="p-6 text-red-600">Error: {state.error}</div>;
    if (state.days.length === 0) {
        return (
            <div className="p-6 text-gray-600">
                No saved daily reports yet — this table reads Workflow 1's completed runs, which
                appear once its daily schedule (or a manual test run) has run at least once.
            </div>
        );
    }

    function toggleExpanded(rowKey) {
        setExpanded(function (prev) {
            var next = Object.assign({}, prev);
            next[rowKey] = !next[rowKey];
            return next;
        });
    }

    return (
        <div className="space-y-4 p-4">
            <div>
                <h1 className="text-xl font-bold">{definition.name}</h1>
                <p className="text-sm text-gray-500">{definition.description}</p>
            </div>

            <div className="bg-white rounded-lg border p-4 flex flex-wrap gap-4 items-center">
                <div>
                    <div className="text-xs font-semibold text-gray-500 uppercase mb-1">14-Day Window Ending</div>
                    <input
                        type="date"
                        className="border rounded px-2 py-1 text-sm"
                        value={effectiveEndDate}
                        max={autoEndDate}
                        onChange={function (e) { setEndDate(e.target.value || null); }}
                    />
                </div>
                <div>
                    <div className="text-xs font-semibold text-gray-500 uppercase mb-1">LLO</div>
                    <select
                        className="border rounded px-2 py-1 text-sm"
                        value={lloFilter}
                        onChange={function (e) { setLloFilter(e.target.value); setFlwFilter("__all__"); }}
                    >
                        <option value="__all__">All LLOs</option>
                        {lloOptions.map(function (o) { return <option key={o.value} value={String(o.value)}>{o.label}</option>; })}
                    </select>
                </div>
                <div>
                    <div className="text-xs font-semibold text-gray-500 uppercase mb-1">FLW</div>
                    <select
                        className="border rounded px-2 py-1 text-sm"
                        value={flwFilter}
                        onChange={function (e) { setFlwFilter(e.target.value); }}
                    >
                        <option value="__all__">All FLWs</option>
                        {flwOptions.map(function (o) { return <option key={o.value} value={o.value}>{o.label}</option>; })}
                    </select>
                </div>
            </div>

            <div className="bg-white rounded-lg shadow-sm p-4 overflow-x-auto">
                {filteredRows.length === 0 ? (
                    <div className="text-sm text-gray-500 p-6 text-center">No FLWs match the selected filters.</div>
                ) : (
                    <table className="min-w-full text-sm border-collapse">
                        <thead>
                            <tr>
                                <th className="sticky left-0 bg-white text-left px-3 py-2 border-b border-gray-200 font-medium text-gray-600 w-6"></th>
                                <th className="sticky left-6 bg-white text-left px-3 py-2 border-b border-gray-200 font-medium text-gray-600">FLW</th>
                                <th className="text-left px-3 py-2 border-b border-gray-200 font-medium text-gray-600">LLO</th>
                                {dayColumns.map(function (d) {
                                    return (
                                        <th key={d} className="text-center px-2 py-2 border-b border-gray-200 font-medium text-gray-600 whitespace-nowrap">
                                            {formatShortDate(d)}
                                        </th>
                                    );
                                })}
                            </tr>
                        </thead>
                        <tbody>
                            {filteredRows.map(function (row) {
                                var rowKey = row.opportunity_id + "::" + row.username;
                                var isOpen = !!expanded[rowKey];
                                return (
                                    <React.Fragment key={rowKey}>
                                        <tr className="hover:bg-gray-50">
                                            <td className="sticky left-0 bg-white px-3 py-2 border-b border-gray-100 text-center">
                                                <button
                                                    type="button"
                                                    onClick={function () { toggleExpanded(rowKey); }}
                                                    className="text-gray-400 hover:text-gray-700"
                                                    title={isOpen ? "Collapse" : "Expand"}
                                                >
                                                    {isOpen ? "▼" : "▶"}
                                                </button>
                                            </td>
                                            <td className="sticky left-6 bg-white px-3 py-2 border-b border-gray-100 font-medium text-gray-900 whitespace-nowrap">
                                                <button type="button" onClick={function () { toggleExpanded(rowKey); }} className="hover:underline text-left">
                                                    {nameMap[row.username] || row.username}
                                                </button>
                                            </td>
                                            <td className="px-3 py-2 border-b border-gray-100 text-gray-500 whitespace-nowrap">
                                                {lloName(oppNames[row.opportunity_id]) || ("Opp #" + row.opportunity_id)}
                                            </td>
                                            {dayColumns.map(function (d) {
                                                var cell = cellInfo(row.byDate[d], THRESHOLDS);
                                                return (
                                                    <td key={d} className={"px-2 py-2 border-b border-gray-100 text-center " + cell.cls} title={cell.title}>
                                                        {cell.text}
                                                    </td>
                                                );
                                            })}
                                        </tr>
                                        {isOpen && (
                                            <tr>
                                                <td colSpan={3 + dayColumns.length} className="bg-gray-50 border-b border-gray-200 p-3">
                                                    <ExpandedFlwDetail row={row} dayColumns={dayColumns} thresholds={THRESHOLDS} />
                                                </td>
                                            </tr>
                                        )}
                                    </React.Fragment>
                                );
                            })}
                        </tbody>
                    </table>
                )}
            </div>
        </div>
    );
}

// Every daily indicator, in display order. `path` reaches into a Workflow-1
// per-FLW indicator dict (dot-notated); `thresholdKey` looks up THRESHOLDS
// (null for the two purely informational entries, which never trip
// anything). `direction` determines the trip comparison: "gte" (>=
// threshold), "gt" (> threshold), or "lt" (< threshold). `contributes: true`
// marks the indicators that roll up into the main grid's per-day red/green
// flag; the rest still compute/show their own tripped state (highlighted
// orange in the expanded detail view instead of red) but don't affect the
// top-level flag -- either because they're informational, or because they're
// judged too easily triggered by legitimate behavior to justify flagging an
// FLW on their own. `description` is the full explanation shown in the (i)
// hover tooltip next to the indicator's label. The last entry (`custom:
// true`) needs BOTH total_forms and daily_span_minutes at once, so it's
// handled as a special case in indicatorDetailsForDay/thresholdDisplayFor
// rather than via path/thresholdKey/direction.
var INDICATOR_DEFS = [
    {
        key: "total_forms", label: "HSD Forms Submitted", path: "total_forms", thresholdKey: null, direction: null,
        description: "Total number of Health Service Delivery (HSD) forms this FLW submitted this day. Shown for context only — never contributes to the flag.",
    },
    {
        key: "unique_work_areas", label: "Unique Work Areas Visited", path: "unique_work_areas_count", thresholdKey: null, direction: null,
        description: "Number of distinct work areas this FLW submitted forms in this day. Shown for context only — never contributes to the flag.",
    },
    {
        key: "households_per_building", label: "Peak Households per Building", path: "households_per_building.max_ratio", thresholdKey: "households_per_building", direction: "gt", contributes: true,
        description: "The highest households-registered ÷ buildings-in-work-area ratio, across every work area this FLW visited this day. Flags a work area where the number of distinct households registered is out of proportion to how many buildings it actually has.",
    },
    {
        key: "households_4plus_children", label: "Large Households (4+ Under-5s)", path: "households_4plus_children_count", thresholdKey: "households_4plus_children", direction: "gt", contributes: true,
        description: "Number of households visited this day where 4 or more distinct children under 5 were registered. Encountering that many large households in one day is uncommon.",
    },
    {
        key: "gap_lt_2min", label: "Rushed Visits (<2 min Apart)", path: "gap_lt_2min_count", thresholdKey: "gap_lt_2min", direction: "gte",
        description: "Number of times two consecutive HSD forms were submitted less than 2 minutes apart — a possible sign of rushing through or fabricating visits.",
    },
    {
        key: "vaccine_yes_pct", label: "% Children Vaccinated", path: "vaccine_yes_pct", thresholdKey: "vaccine_yes_pct", direction: "lt", contributes: true,
        description: "Share of this day's HSD forms where the child was recorded as having received any vaccine. An unusually low share can mean the vaccination step isn't being done.",
    },
    {
        key: "camping_repeat_pct", label: "Camping % (Same-Spot Visits)", path: "camping_repeat_pct", thresholdKey: "camping_repeat_pct", direction: "gte", contributes: true,
        description: "Share of this day's GPS-tagged forms sharing the exact same recorded coordinate. Ordinary GPS noise means a device actually re-acquiring location on each form almost never returns the identical fix twice — repetition suggests the location wasn't really refreshed between forms.",
    },
    {
        key: "duplicate_child_names", label: "Duplicate Child Names Across Households", path: "duplicate_child_names_count", thresholdKey: "duplicate_child_names", direction: "gte", contributes: true,
        description: "Number of child names that appear under more than one household visited this day (case-insensitive) — may indicate a fabricated or reused identity.",
    },
    {
        key: "duplicate_child_ages", label: "Duplicate Child Ages Across Households", path: "duplicate_child_ages_count", thresholdKey: "duplicate_child_ages", direction: "gte", contributes: true,
        description: "Number of child dates of birth that appear under more than one household visited this day. Two unrelated households having a child with the exact same birth date is uncommon.",
    },
    {
        key: "straight_line_dw", label: "Straight-Lining: Child Unwell Today", path: "straight_line_pct.dw_child_unwell_today", thresholdKey: "straight_line_pct", direction: "gte",
        description: "Share of this day's forms with the identical answer to “Does your child have breathing difficulty, vomiting, diarrhea, or high body temperature today?” A near-unanimous answer across different children is an unlikely coincidence — though genuinely high local prevalence can also produce this, so it's shown for context rather than flagged on its own.",
    },
    {
        key: "straight_line_diarrhea", label: "Straight-Lining: Diarrhea Last Month", path: "straight_line_pct.diarrhea_last_month", thresholdKey: "straight_line_pct", direction: "gte",
        description: "Share of this day's forms with the identical answer to “Did your child have diarrhea in the last month?” — same straight-lining concern, different question, and the same caveat about genuinely high local prevalence.",
    },
    {
        key: "muac_repetition_pct", label: "MUAC Value Repetition %", path: "muac_repetition_pct", thresholdKey: "muac_repetition_pct", direction: "gte", contributes: true,
        description: "Share of this day's MUAC (arm circumference) measurements that are the exact same value. Real measurements vary child to child; high repetition suggests numbers may be reused rather than actually measured.",
    },
    {
        key: "visits_compressed_1hr", label: "Total Time Between First and Last Visit", custom: true, // gitleaks:allow (not a secret -- an indicator key string)
        description: "Total elapsed time from this FLW's first visit of the day to their last. Shown alongside its threshold (30+ forms in that day AND a span of 60 minutes or less) as worth digging into, but doesn't contribute to the flag on its own.",
    },
];

function getByPath(obj, path) {
    var parts = path.split(".");
    var cur = obj;
    for (var i = 0; i < parts.length; i++) {
        if (cur == null) return null;
        cur = cur[parts[i]];
    }
    return cur;
}

function tripped(value, threshold, direction) {
    if (value == null || threshold == null) return false;
    if (direction === "gte") return value >= threshold;
    if (direction === "gt") return value > threshold;
    if (direction === "lt") return value < threshold;
    return false;
}

// The one compound indicator (30+ visits AND <=1hr span) needs both
// total_forms and daily_span_minutes at once -- can't be expressed as a
// single path/threshold/direction triple like every other indicator.
function thresholdDisplayFor(def, thresholds) {
    if (def.custom) {
        return thresholds.min_forms_for_compression + "+ forms & ≤" + thresholds.max_span_minutes_for_compression + "min";
    }
    return def.thresholdKey ? thresholds[def.thresholdKey] : null;
}

// Computes every indicator's {value, threshold, evaluable, tripped,
// contributes} for one FLW-day. `contributes` mirrors the def's own flag --
// only tripped indicators with contributes:true feed the main grid's per-day
// red/green flag; the rest still report tripped (for orange highlighting)
// but don't affect it.
function indicatorDetailsForDay(flw, thresholds) {
    return INDICATOR_DEFS.map(function (def) {
        if (def.custom) {
            var totalForms = flw ? flw.total_forms : null;
            var spanMin = flw ? flw.daily_span_minutes : null;
            var evaluable = totalForms != null && spanMin != null;
            var isTripped = evaluable
                && totalForms >= thresholds.min_forms_for_compression
                && spanMin <= thresholds.max_span_minutes_for_compression;
            return {
                key: def.key,
                label: def.label,
                value: evaluable ? (spanMin + " min") : null,
                threshold: thresholdDisplayFor(def, thresholds),
                evaluable: evaluable,
                tripped: isTripped,
                contributes: !!def.contributes,
            };
        }
        var value = flw ? getByPath(flw, def.path) : null;
        var threshold = def.thresholdKey ? thresholds[def.thresholdKey] : null;
        var evaluable = value != null;
        return {
            key: def.key,
            label: def.label,
            value: value,
            threshold: threshold,
            evaluable: evaluable,
            tripped: def.thresholdKey ? tripped(value, threshold, def.direction) : false,
            contributes: !!def.contributes,
        };
    });
}

function cellInfo(flw, thresholds) {
    if (!flw) return { text: "—", cls: "text-gray-300", title: "No report for this day" };
    var details = indicatorDetailsForDay(flw, thresholds);
    var anyTripped = details.some(function (d) { return d.tripped && d.contributes; });
    return anyTripped
        ? { text: "●", cls: "text-red-600 font-bold text-base", title: "Flagged — expand row for which indicator(s) tripped" }
        : { text: "○", cls: "text-green-600 text-base", title: "OK — no indicator over threshold" };
}

// This program's opportunity names all follow "<LLO>-PRE-RCT Connect-CHC 2026"
// (e.g. "EHA-PRE-RCT Connect-CHC 2026"); the short LLO code is everything
// before the first hyphen. Falls back to the full name if it doesn't match
// that shape, so this degrades gracefully rather than mangling anything.
function lloName(oppName) {
    if (!oppName) return oppName;
    return oppName.split("-")[0];
}

function formatShortDate(isoDate) {
    var parts = isoDate.split("-");
    if (parts.length < 3) return isoDate;
    var months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    var month = parseInt(parts[1], 10) - 1;
    var day = parseInt(parts[2], 10);
    if (month < 0 || month > 11 || isNaN(day)) return isoDate;
    return months[month] + " " + day;
}

function InfoTooltip({ text }) {
    // Native title= doesn't render reliably inside this iframe/component context
    // (same gotcha the trend dashboard hit building workflow 4593's metric
    // tooltips). An earlier version of this popover rendered as a React child
    // positioned via `position: fixed`, but inside this table's sticky/
    // overflow-x-auto columns, `position: fixed` descendants get trapped in the
    // sticky ancestor's own stacking context and painted UNDER later rows'
    // sticky cells -- only a sliver of the popup peeked out. Rendering the
    // popup as a plain DOM node appended directly to document.body sidesteps
    // that entirely: it's a body-level sibling of the table, so it always
    // paints on top, and its position is clamped to the viewport so it's never
    // cut off at the right edge either.
    if (!text) return null;
    var iconRef = React.useRef(null);
    var elRef = React.useRef(null);

    function removeEl() {
        if (elRef.current) {
            elRef.current.remove();
            elRef.current = null;
        }
    }

    function show() {
        removeEl();
        if (!iconRef.current) return;
        var rect = iconRef.current.getBoundingClientRect();
        var width = 280;
        var el = document.createElement("div");
        el.textContent = text;
        el.style.position = "fixed";
        el.style.zIndex = "9999";
        el.style.maxWidth = width + "px";
        el.style.background = "#111827";
        el.style.color = "#fff";
        el.style.fontSize = "12px";
        el.style.fontWeight = "400";
        el.style.textTransform = "none";
        el.style.borderRadius = "4px";
        el.style.padding = "6px 8px";
        el.style.boxShadow = "0 4px 10px rgba(0,0,0,0.3)";
        el.style.pointerEvents = "none";
        el.style.top = (rect.bottom + 6) + "px";
        var left = rect.left;
        var maxLeft = window.innerWidth - width - 10;
        if (left > maxLeft) left = Math.max(10, maxLeft);
        el.style.left = left + "px";
        document.body.appendChild(el);
        elRef.current = el;
    }

    React.useEffect(function () {
        return removeEl;
    }, []);

    return (
        <span
            ref={iconRef}
            onMouseEnter={show}
            onMouseLeave={removeEl}
            className="relative inline-flex items-center justify-center w-4 h-4 ml-1 align-middle rounded-full bg-gray-200 text-gray-600 text-[10px] font-bold cursor-help"
        >
            i
        </span>
    );
}

function ExpandedFlwDetail({ row, dayColumns, thresholds }) {
    // Transposed from the main grid's own orientation: here, days are the
    // COLUMNS (matching the day columns directly above this expanded row) and
    // indicators are the ROWS -- one row per indicator, its threshold shown
    // alongside its label, so scanning down a column reads as "this FLW's day",
    // same as scanning down the main table.
    var detailsByDay = {};
    dayColumns.forEach(function (d) {
        var flw = row.byDate[d];
        detailsByDay[d] = flw ? indicatorDetailsForDay(flw, thresholds) : null;
    });

    return (
        <div className="overflow-x-auto">
            <table className="min-w-full text-xs border-collapse bg-white rounded border">
                <thead>
                    <tr>
                        <th className="sticky left-0 bg-white text-left px-2 py-1.5 border-b font-medium text-gray-600 whitespace-nowrap">Indicator</th>
                        {dayColumns.map(function (d) {
                            return (
                                <th key={d} className="text-center px-2 py-1.5 border-b font-medium text-gray-600 whitespace-nowrap">
                                    {formatShortDate(d)}
                                </th>
                            );
                        })}
                    </tr>
                </thead>
                <tbody className="divide-y">
                    {INDICATOR_DEFS.map(function (def) {
                        var thresholdDisplay = thresholdDisplayFor(def, thresholds);
                        return (
                            <tr key={def.key}>
                                <td className="sticky left-0 bg-white px-2 py-1.5 font-medium text-gray-700 whitespace-nowrap">
                                    {def.label}
                                    <InfoTooltip text={def.description} />
                                    {thresholdDisplay != null && (
                                        <span className="text-gray-400 font-normal"> (thr: {thresholdDisplay})</span>
                                    )}
                                </td>
                                {dayColumns.map(function (d) {
                                    var dayDetails = detailsByDay[d];
                                    if (!dayDetails) {
                                        return <td key={d} className="px-2 py-1.5 text-center text-gray-300">—</td>;
                                    }
                                    var det = dayDetails.filter(function (x) { return x.key === def.key; })[0];
                                    var display = det.value == null ? "n/a" : det.value;
                                    // Tripped-but-non-contributing indicators are highlighted orange, not
                                    // red -- they're worth a look but don't by themselves justify flagging
                                    // this FLW the way a contributing indicator's red does.
                                    var cellCls = det.tripped
                                        ? (det.contributes ? "bg-red-50 text-red-700 font-semibold" : "bg-orange-50 text-orange-700 font-semibold")
                                        : "text-gray-700";
                                    return (
                                        <td key={d} className={"px-2 py-1.5 text-center whitespace-nowrap " + cellCls}>
                                            {display}
                                        </td>
                                    );
                                })}
                            </tr>
                        );
                    })}
                </tbody>
            </table>
        </div>
    );
}"""

TEMPLATE = {
    "key": "flw_daily_indicator_table",
    "name": "FLW Daily Indicator Table",
    "description": (
        "Program 176 (CHC PRE-RCT Nigeria) 14-day per-FLW daily indicator grid, sourced from the "
        "FLW Daily Indicator Report's saved daily snapshots. One 0/1 flag per FLW per day; expand a "
        "row to see every indicator + threshold for every day and exactly which ones tripped."
    ),
    "icon": "fa-table-cells",
    "color": "indigo",
    "multi_opp": True,
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
}
