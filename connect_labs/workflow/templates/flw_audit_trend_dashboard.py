"""FLW Audit Trend Dashboard — Program 176 (CHC PRE-RCT Nigeria).

Workflow 2 of a two-workflow pair (see flw_audit_workflows_spec.md and
flw_weekly_audit_report.py, "Workflow 1"). Reads Workflow 1's saved weekly
snapshots (one completed WorkflowRun per opportunity per week) via a
dedicated read-only API endpoint (api/flw-audit-report-history/) and
displays them as trend lines and distribution snapshots + trends, scoped by
an opportunity filter (all 4, or one) and an FLW filter (all FLWs in scope,
or one).

"All FLWs" combines per-FLW indicators two different ways depending on what
kind of number each one is (see TREND_INDICATORS' `kind`): raw counts sum
cleanly across FLWs, but averages/percentages/medians/indices do NOT sum or
average correctly without the underlying numerator/denominator per FLW,
which Workflow 1's snapshot doesn't store — averaging those across FLWs is
therefore only an approximation (a plain mean of each FLW's own value, not
a statistic re-derived from raw visits), and is labeled as such in the UI.

No pipeline_schema: this template never reads CommCare/Connect visit data
directly — everything comes from Workflow 1's already-computed history via
a plain browser fetch(), same pattern used elsewhere in this codebase (e.g.
the Bulk Image Audit sessions-summary endpoint) for cross-workflow reads
that don't fit the pipelines/view contract.

config.source_definition_id (set on the instance after creation, since the
source workflow's id is only known once it exists) points at Workflow 1's
definition id.
"""
from __future__ import annotations

DEFINITION = {
    "name": "FLW Audit Trend Dashboard",
    "description": (
        "Program 176 (CHC PRE-RCT Nigeria) cross-opportunity trend view over the FLW Weekly "
        "Audit Report's saved weekly snapshots — filter by opportunity and FLW. Trends tab: "
        "every indicator over time. Snapshot tab: pick one week (or all weeks) to see MUAC/age "
        "distributions at that point in time."
    ),
    "version": 1,
    "templateType": "flw_audit_trend_dashboard",
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

    // Opportunity names (including the LLO name, e.g. "CHC PRE-RCT (Nigeria) - EHA")
    // come from a JSON blob the base template embeds — same convention chc_audit_history
    // (workflow 5181) uses, rather than a hardcoded id->name map.
    var oppNames = React.useMemo(function () {
        var m = {};
        try {
            var el = document.getElementById("user-opportunities");
            if (el) JSON.parse(el.textContent).forEach(function (o) { m[o.id] = o.name; });
        } catch (e) { console.error("FLW trend dashboard: failed to parse user-opportunities", e); }
        return m;
    }, []);

    var nameMap = React.useMemo(function () {
        var m = {};
        (workers || []).forEach(function (w) { if (w.username) m[w.username] = w.name || w.username; });
        return m;
    }, [workers]);

    var _state = React.useState({ loading: true, error: null, weeks: [] });
    var state = _state[0];
    var setState = _state[1];

    React.useEffect(function () {
        if (!sourceDefinitionId) {
            setState({ loading: false, error: "No source_definition_id configured on this workflow.", weeks: [] });
            return;
        }
        var url = "/labs/workflow/api/flw-audit-report-history/?definition_id=" + sourceDefinitionId;
        fetch(url, { credentials: "same-origin" })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.error) {
                    setState({ loading: false, error: data.error, weeks: [] });
                } else {
                    setState({ loading: false, error: null, weeks: data.weeks || [] });
                }
            })
            .catch(function (e) {
                setState({ loading: false, error: String(e), weeks: [] });
            });
    }, [sourceDefinitionId]);

    var opportunityOptions = React.useMemo(function () {
        var ids = {};
        state.weeks.forEach(function (w) { if (w.opportunity_id) ids[w.opportunity_id] = true; });
        return Object.keys(ids).map(Number).sort(function (a, b) { return a - b; });
    }, [state.weeks]);

    var _selectedOpp = React.useState("all"); // "all" or a single opportunity_id (as string)
    var selectedOpp = _selectedOpp[0];
    var setSelectedOpp = _selectedOpp[1];

    var effectiveOpps = selectedOpp === "all" ? opportunityOptions : [Number(selectedOpp)];

    var weeksInScope = React.useMemo(function () {
        return state.weeks.filter(function (w) { return effectiveOpps.indexOf(w.opportunity_id) !== -1; });
    }, [state.weeks, effectiveOpps]);

    var flwOptions = React.useMemo(function () {
        var names = {};
        weeksInScope.forEach(function (w) {
            (w.flws || []).forEach(function (f) { if (f.username) names[f.username] = true; });
        });
        return Object.keys(names).sort();
    }, [weeksInScope]);

    var _selectedFlw = React.useState("__all__"); // "__all__" or a single username
    var selectedFlw = _selectedFlw[0];
    var setSelectedFlw = _selectedFlw[1];

    React.useEffect(function () {
        if (selectedFlw !== "__all__" && flwOptions.indexOf(selectedFlw) === -1) {
            setSelectedFlw("__all__");
        }
    }, [flwOptions]);

    var sortedWeeks = React.useMemo(function () {
        return weeksInScope.slice().sort(function (a, b) {
            return (a.period_start || "").localeCompare(b.period_start || "");
        });
    }, [weeksInScope]);

    var distinctPeriods = React.useMemo(function () {
        var seen = {};
        var out = [];
        sortedWeeks.forEach(function (w) {
            if (w.period_start && !seen[w.period_start]) { seen[w.period_start] = true; out.push(w.period_start); }
        });
        return out;
    }, [sortedWeeks]);

    var periodEndByStart = React.useMemo(function () {
        var m = {};
        sortedWeeks.forEach(function (w) { if (w.period_start && !m[w.period_start] && w.period_end) m[w.period_start] = w.period_end; });
        return m;
    }, [sortedWeeks]);

    // "__all__" (aggregate every week in scope) or a single period_start.
    var _selectedPeriod = React.useState(null);
    var selectedPeriod = _selectedPeriod[0];
    var setSelectedPeriod = _selectedPeriod[1];

    React.useEffect(function () {
        if (distinctPeriods.length === 0) return;
        var isValid = selectedPeriod === "__all__" || distinctPeriods.indexOf(selectedPeriod) !== -1;
        if (!selectedPeriod || !isValid) {
            setSelectedPeriod(distinctPeriods[distinctPeriods.length - 1]);
        }
    }, [distinctPeriods]);

    var _activeTab = React.useState("trends"); // "trends" or "snapshot"
    var activeTab = _activeTab[0];
    var setActiveTab = _activeTab[1];

    if (state.loading) return <div className="p-6 text-gray-600">Loading trend data...</div>;
    if (state.error) return <div className="p-6 text-red-600">Error: {state.error}</div>;
    if (state.weeks.length === 0) {
        return (
            <div className="p-6 text-gray-600">
                No saved weekly reports yet — this dashboard reads Workflow 1's completed runs, which
                appear once its schedule (or a backfill) has run at least once.
            </div>
        );
    }

    var oppScopeLabel = selectedOpp === "all" ? "All Opportunities" : (oppNames[Number(selectedOpp)] || "Opp #" + selectedOpp);
    var flwScopeLabel = selectedFlw === "__all__" ? "All FLWs" : (nameMap[selectedFlw] || selectedFlw);

    return (
        <div className="space-y-6 p-4">
            <div>
                <h1 className="text-xl font-bold">{definition.name}</h1>
                <p className="text-sm text-gray-500">{definition.description}</p>
            </div>

            <FilterBar
                opportunityOptions={opportunityOptions}
                selectedOpp={selectedOpp}
                onChangeOpp={setSelectedOpp}
                oppNames={oppNames}
                flwOptions={flwOptions}
                selectedFlw={selectedFlw}
                onChangeFlw={setSelectedFlw}
                nameMap={nameMap}
            />

            <div className="bg-indigo-50 border border-indigo-200 rounded px-3 py-2 text-sm text-indigo-900">
                Showing: <span className="font-semibold">{oppScopeLabel}</span> · <span className="font-semibold">{flwScopeLabel}</span>
            </div>

            <TabBar activeTab={activeTab} onChange={setActiveTab} />

            {activeTab === "trends" && (
                <div className="space-y-6">
                    <TrendSection
                        weeks={sortedWeeks}
                        flw={selectedFlw}
                        flwName={flwScopeLabel}
                        flwCount={flwOptions.length}
                        selectedOpp={selectedOpp}
                        oppNames={oppNames}
                        nameMap={nameMap}
                        opportunityOptions={opportunityOptions}
                    />
                    <DistributionTrendCharts
                        weeks={sortedWeeks}
                        selectedFlw={selectedFlw}
                        flwName={flwScopeLabel}
                        selectedOpp={selectedOpp}
                        oppNames={oppNames}
                        nameMap={nameMap}
                        opportunityOptions={opportunityOptions}
                    />
                </div>
            )}

            {activeTab === "snapshot" && (
                <DistributionSnapshotSection
                    weeks={sortedWeeks}
                    distinctPeriods={distinctPeriods}
                    periodEndByStart={periodEndByStart}
                    selectedPeriod={selectedPeriod}
                    onChangePeriod={setSelectedPeriod}
                    selectedFlw={selectedFlw}
                    flwName={flwScopeLabel}
                />
            )}
        </div>
    );
}

function TabBar({ activeTab, onChange }) {
    var tabs = [
        { key: "trends", label: "Trends" },
        { key: "snapshot", label: "Snapshot" },
    ];
    return (
        <div className="flex gap-1 border-b border-gray-200">
            {tabs.map(function (t) {
                var isActive = activeTab === t.key;
                return (
                    <button
                        key={t.key}
                        type="button"
                        onClick={function () { onChange(t.key); }}
                        className={
                            "px-4 py-2 text-sm font-medium border-b-2 -mb-px " +
                            (isActive ? "border-indigo-600 text-indigo-700" : "border-transparent text-gray-500 hover:text-gray-700")
                        }
                    >
                        {t.label}
                    </button>
                );
            })}
        </div>
    );
}

function FilterBar({ opportunityOptions, selectedOpp, onChangeOpp, oppNames, flwOptions, selectedFlw, onChangeFlw, nameMap }) {
    return (
        <div className="bg-white rounded-lg border p-4 flex flex-wrap gap-6 items-start">
            <div>
                <div className="text-xs font-semibold text-gray-500 uppercase mb-1">Opportunity</div>
                <select
                    className="border rounded px-2 py-1 text-sm"
                    value={selectedOpp}
                    onChange={function (e) { onChangeOpp(e.target.value); }}
                >
                    <option value="all">All Opportunities</option>
                    {opportunityOptions.map(function (oppId) {
                        return (
                            <option key={oppId} value={String(oppId)}>
                                {oppNames[oppId] || "Opp #" + oppId}
                            </option>
                        );
                    })}
                </select>
            </div>
            <div>
                <div className="text-xs font-semibold text-gray-500 uppercase mb-1">FLW</div>
                <select
                    className="border rounded px-2 py-1 text-sm"
                    value={selectedFlw}
                    onChange={function (e) { onChangeFlw(e.target.value); }}
                >
                    <option value="__all__">All FLWs</option>
                    {flwOptions.map(function (u) {
                        return <option key={u} value={u}>{nameMap[u] || u}</option>;
                    })}
                </select>
            </div>
        </div>
    );
}

function InfoTooltip({ text }) {
    // Native title= doesn't render reliably inside this iframe/component context
    // (same gotcha hit building workflow 4593's metric tooltips) — use a custom
    // hover popover positioned via getBoundingClientRect instead.
    if (!text) return null;
    var _open = React.useState(false);
    var open = _open[0];
    var setOpen = _open[1];
    var iconRef = React.useRef(null);
    var _pos = React.useState({ top: 0, left: 0 });
    var pos = _pos[0];
    var setPos = _pos[1];

    function show() {
        if (iconRef.current) {
            var rect = iconRef.current.getBoundingClientRect();
            setPos({ top: rect.bottom + 6, left: rect.left });
        }
        setOpen(true);
    }
    function hide() { setOpen(false); }

    return (
        <span className="relative inline-block ml-1 align-middle">
            <span
                ref={iconRef}
                onMouseEnter={show}
                onMouseLeave={hide}
                className="inline-flex items-center justify-center w-4 h-4 rounded-full bg-gray-200 text-gray-600 text-[10px] font-bold cursor-help"
            >
                i
            </span>
            {open && (
                <span
                    style={{ position: "fixed", top: pos.top, left: pos.left, zIndex: 50, maxWidth: "240px" }}
                    className="bg-gray-900 text-white text-xs rounded px-2 py-1 shadow-lg"
                >
                    {text}
                </span>
            )}
        </span>
    );
}

// `kind` controls how "All FLWs" combines this indicator across FLWs:
// "sum" combines cleanly (a real total); "avg" is only an approximation (a
// plain mean of each FLW's own value — not the same as re-deriving the
// statistic from every FLW's raw visits, since medians/percentages/averages
// don't combine by simple averaging). `thresholds` (optional) draws flat
// reference lines at meaningful, already-established values from Workflow
// 1's own compute logic (see flw_audit_compute.py) — only added where a real
// threshold exists, not invented ones.
// Rendered together as one 2-line chart (see CombinedLineChart) instead of
// two separate single-line charts, since they're most useful compared side
// by side (forms submitted vs. visits actually approved).
var FORMS_VISITS_INDICATORS = [
    { path: "total_service_delivery_forms", label: "Total Service Delivery Forms", kind: "sum", tooltip: "Total Health Service Delivery forms submitted this week.", color: "#2563eb" },
    { path: "total_approved_visits", label: "Total Approved Visits", kind: "sum", tooltip: "Total visits approved in Connect this week.", color: "#16a34a" },
];

// Service-delivery coverage counts (see Connect-CHC System Design Document's
// calculations-group walkthrough) -- rendered together as one 4-line chart
// (see CombinedLineChart), same pattern as FORMS_VISITS_INDICATORS.
var COVERAGE_INDICATORS = [
    { path: "registered_children_count", label: "Registered Children", kind: "sum", tooltip: "Distinct children with at least one visit this week.", color: "#2563eb" },
    { path: "muac_taken_count", label: "MUAC Taken", kind: "sum", tooltip: "Distinct children with a MUAC measurement recorded this week.", color: "#16a34a" },
    { path: "dewormed_count", label: "Dewormed", kind: "sum", tooltip: "Distinct children with deworming actually delivered (dw_meds_delivery_status = \"DW Delivered\") on at least one visit this week.", color: "#d97706" },
    { path: "vaccinated_count", label: "Vaccinated", kind: "sum", tooltip: "Distinct children recorded as having received a vaccine (received_any_vaccine = \"yes\") on at least one visit this week.", color: "#7c3aed" },
];

var TREND_INDICATORS = [
    { path: "days_worked", label: "Days Worked", kind: "sum", tooltip: "Number of distinct days with at least one visit this week." },
    { path: "avg_children_per_household", label: "Avg Children per Household", kind: "avg", tooltip: "Average number of distinct children seen per household visited this week." },
    { path: "pct_gap_lt_3min", label: "% Gap < 3min Between Forms", kind: "avg", tooltip: "Share of consecutive same-day form submissions less than 3 minutes apart — a possible sign of rushed or fabricated visits." },
    { path: "median_gap_minutes", label: "Median Gap (minutes)", kind: "avg", tooltip: "Median time between consecutive same-day form submissions, pooled across the week." },
    { path: "avg_distance_between_visits_m", label: "Avg Distance Between Visits (m)", kind: "avg", tooltip: "Average GPS distance between consecutive same-day visits, pooled across the week." },
    { path: "avg_time_first_last_visit_minutes", label: "Avg First→Last Visit Span (min/day)", kind: "avg", tooltip: "Average span between the first and last visit on each day worked." },
    { path: "pct_same_dob_within_household", label: "% Same-DOB Within Household", kind: "avg", tooltip: "Share of households with two or more children sharing the same date of birth — a possible data-entry duplicate." },
    { path: "fraud.gps_accuracy_flag_pct", label: "GPS Accuracy Flag %", kind: "avg", tooltip: "Share of visits with GPS accuracy worse than 100m, or exactly 0 (no fix)." },
    { path: "fraud.gps_near_duplicate_count", label: "GPS Near-Duplicate Count", kind: "sum", tooltip: "Visits within 10m of another visit logged to a different household the same day." },
    { path: "fraud.form_duration_outlier_count", label: "Form Duration Outlier Count", kind: "sum", tooltip: "Forms completed in under 2 minutes." },
    { path: "fraud.duplicate_child_count", label: "Duplicate Child Count", kind: "sum", tooltip: "Children sharing the same date of birth across more than one household." },
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

function combineForWeek(rows, path, kind) {
    var vals = rows.map(function (r) { return getByPath(r, path); }).filter(function (v) { return typeof v === "number" && !isNaN(v); });
    if (vals.length === 0) return null;
    var sum = vals.reduce(function (a, b) { return a + b; }, 0);
    return kind === "sum" ? sum : sum / vals.length;
}

var ENTITY_COLORS = ["#2563eb", "#dc2626", "#16a34a", "#d97706", "#7c3aed", "#db2777", "#0891b2", "#65a30d", "#ea580c", "#4f46e5", "#0d9488", "#be123c"];

function colorForEntityIndex(i) {
    return ENTITY_COLORS[i % ENTITY_COLORS.length];
}

// Resolves how the Trends tab's per-indicator charts should split into
// entity lines/bars given the current Opportunity/FLW filter selection, so
// users can spot per-FLW/per-LLO patterns and outliers directly instead of
// always collapsing everyone into one combined line:
// - one specific FLW selected -> a single entity (that FLW alone)
// - "All FLWs" within one selected opportunity -> one entity per FLW in that LLO
// - "All FLWs" across "All Opportunities" -> one entity per LLO (aggregating its own FLWs)
function resolveTrendEntities(weeks, selectedOpp, selectedFlw, oppNames, nameMap, opportunityOptions) {
    if (selectedFlw !== "__all__") {
        return {
            mode: "flw-single",
            entities: [{ key: selectedFlw, label: nameMap[selectedFlw] || selectedFlw, matches: function (f) { return f.username === selectedFlw; } }],
        };
    }
    if (selectedOpp !== "all") {
        var names = {};
        weeks.forEach(function (w) { (w.flws || []).forEach(function (f) { if (f.username) names[f.username] = true; }); });
        return {
            mode: "flw-all",
            entities: Object.keys(names).sort().map(function (u) {
                return { key: u, label: nameMap[u] || u, matches: function (f) { return f.username === u; } };
            }),
        };
    }
    return {
        mode: "llo-all",
        entities: opportunityOptions.map(function (id) {
            return { key: String(id), label: oppNames[id] || "Opp #" + id, opportunityId: id };
        }),
    };
}

// Rows for one entity, scoped to the given (already period-filtered) weeks.
// llo-all entities are matched by the parent week's own opportunity_id (FLW
// rows don't carry it individually); flw-* entities are matched per-row.
function rowsForEntity(weeksForPeriod, entity, mode) {
    var out = [];
    if (mode === "llo-all") {
        weeksForPeriod.forEach(function (w) {
            if (w.opportunity_id === entity.opportunityId) out = out.concat(w.flws || []);
        });
        return out;
    }
    weeksForPeriod.forEach(function (w) { out = out.concat((w.flws || []).filter(entity.matches)); });
    return out;
}

// Distinct period_start values across a raw (non-period-grouped) weeks list,
// sorted ascending, each paired with its period_end.
function distinctPeriodsFromWeeks(weeks) {
    var endByStart = {};
    var order = [];
    weeks.forEach(function (w) {
        if (!w.period_start) return;
        if (endByStart[w.period_start] === undefined) {
            endByStart[w.period_start] = w.period_end || null;
            order.push(w.period_start);
        } else if (!endByStart[w.period_start] && w.period_end) {
            endByStart[w.period_start] = w.period_end;
        }
    });
    order.sort(function (a, b) { return a.localeCompare(b); });
    return order.map(function (p) { return { period_start: p, period_end: endByStart[p] }; });
}

function TrendSection({ weeks, flw, flwName, flwCount, selectedOpp, oppNames, nameMap, opportunityOptions }) {
    var isAll = flw === "__all__";

    // Forms-vs-Visits and Service Coverage stay as single combined lines
    // (they compare two/four DIFFERENT indicators against each other, not
    // one indicator across entities) -- unchanged from before.
    var groupedWeeks = React.useMemo(function () { return groupWeeksByPeriod(weeks); }, [weeks]);

    var COMBINED_PATHS = React.useMemo(function () { return FORMS_VISITS_INDICATORS.concat(COVERAGE_INDICATORS); }, []);

    var combinedSeries = React.useMemo(function () {
        var out = {};
        COMBINED_PATHS.forEach(function (ind) { out[ind.path] = []; });
        groupedWeeks.forEach(function (w) {
            var rows = w.flws || [];
            var relevant = isAll ? rows : rows.filter(function (f) { return f.username === flw; });
            COMBINED_PATHS.forEach(function (ind) {
                out[ind.path].push({
                    period_start: w.period_start,
                    period_end: w.period_end,
                    value: combineForWeek(relevant, ind.path, ind.kind),
                });
            });
        });
        return out;
    }, [groupedWeeks, flw, isAll, COMBINED_PATHS]);

    // The per-indicator TREND_INDICATORS charts, on the other hand, split
    // into one line per entity (LLO or FLW) -- see resolveTrendEntities.
    var trendMode = React.useMemo(function () {
        return resolveTrendEntities(weeks, selectedOpp, flw, oppNames, nameMap, opportunityOptions);
    }, [weeks, selectedOpp, flw, oppNames, nameMap, opportunityOptions]);

    var distinctPeriodList = React.useMemo(function () { return distinctPeriodsFromWeeks(weeks); }, [weeks]);

    var entitySeries = React.useMemo(function () {
        var out = {};
        trendMode.entities.forEach(function (ent) {
            out[ent.key] = {};
            TREND_INDICATORS.forEach(function (ind) { out[ent.key][ind.path] = []; });
        });
        distinctPeriodList.forEach(function (period) {
            var weeksThisPeriod = weeks.filter(function (w) { return w.period_start === period.period_start; });
            trendMode.entities.forEach(function (ent) {
                var rows = rowsForEntity(weeksThisPeriod, ent, trendMode.mode);
                TREND_INDICATORS.forEach(function (ind) {
                    out[ent.key][ind.path].push({ period_start: period.period_start, period_end: period.period_end, value: combineForWeek(rows, ind.path, ind.kind) });
                });
            });
        });
        return out;
    }, [trendMode, distinctPeriodList, weeks]);

    return (
        <div>
            <h2 className="text-lg font-semibold mb-2">Trends — {flwName}</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                <CombinedLineChart
                    chartId="trend-forms-visits"
                    title="Forms Submitted vs. Approved Visits"
                    indicators={FORMS_VISITS_INDICATORS}
                    series={combinedSeries}
                    isAll={isAll}
                    flwCount={flwCount}
                />
                <CombinedLineChart
                    chartId="trend-coverage"
                    title="Service Coverage"
                    indicators={COVERAGE_INDICATORS}
                    series={combinedSeries}
                    isAll={isAll}
                    flwCount={flwCount}
                />
                {TREND_INDICATORS.map(function (ind) {
                    return (
                        <LineTrendChart
                            key={ind.path}
                            chartId={"trend-" + ind.path}
                            label={ind.label}
                            tooltip={ind.tooltip}
                            entities={trendMode.entities}
                            mode={trendMode.mode}
                            seriesByEntity={entitySeries}
                            path={ind.path}
                            kind={ind.kind}
                            thresholds={ind.thresholds}
                        />
                    );
                })}
            </div>
        </div>
    );
}

var MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function formatShortDate(isoDate) {
    // "2026-07-06" -> "Jul 6". Parsed as plain string components (not `new Date`)
    // so it can't drift a day from browser-timezone conversion of a date-only string.
    if (!isoDate) return "";
    var parts = isoDate.split("-");
    if (parts.length < 3) return isoDate;
    var month = parseInt(parts[1], 10) - 1;
    var day = parseInt(parts[2], 10);
    if (month < 0 || month > 11 || isNaN(day)) return isoDate;
    return MONTH_ABBR[month] + " " + day;
}

function formatDateRange(startIso, endIso) {
    // Workflow 1's snapshot already stores period_start/period_end as the full
    // inclusive Mon-Sun window (see flw_weekly_audit_report.py run_default) —
    // this just surfaces both ends instead of only period_start.
    var start = formatShortDate(startIso);
    if (!endIso || endIso === startIso) return start;
    return start + " – " + formatShortDate(endIso);
}

function groupWeeksByPeriod(weeks) {
    // Merges every opportunity's own run for the same period_start into one
    // point — otherwise selecting "All Opportunities" plots one point per
    // opportunity-run instead of one combined point per week.
    var byPeriod = {};
    var order = [];
    (weeks || []).forEach(function (w) {
        var key = w.period_start;
        if (!byPeriod[key]) {
            byPeriod[key] = { period_start: w.period_start, period_end: w.period_end, flws: [] };
            order.push(key);
        }
        byPeriod[key].flws = byPeriod[key].flws.concat(w.flws || []);
        if (!byPeriod[key].period_end && w.period_end) byPeriod[key].period_end = w.period_end;
    });
    order.sort(function (a, b) { return a.localeCompare(b); });
    return order.map(function (key) { return byPeriod[key]; });
}

function linearTrendline(values) {
    var n = values.length;
    var clean = values.map(function (v) { return typeof v === "number" ? v : null; });
    if (n < 2) return clean.map(function () { return null; });
    var xs = [];
    var ys = [];
    clean.forEach(function (v, i) { if (v !== null) { xs.push(i); ys.push(v); } });
    if (xs.length < 2) return clean.map(function () { return null; });
    var m = xs.length;
    var sumX = xs.reduce(function (a, b) { return a + b; }, 0);
    var sumY = ys.reduce(function (a, b) { return a + b; }, 0);
    var sumXY = xs.reduce(function (acc, x, i) { return acc + x * ys[i]; }, 0);
    var sumXX = xs.reduce(function (acc, x) { return acc + x * x; }, 0);
    var denom = m * sumXX - sumX * sumX;
    if (denom === 0) return clean.map(function () { return null; });
    var slope = (m * sumXY - sumX * sumY) / denom;
    var intercept = (sumY - slope * sumX) / m;
    return clean.map(function (_, i) { return slope * i + intercept; });
}

var THRESHOLD_COLORS = ["#f59e0b", "#dc2626", "#7c3aed"];

function LineTrendChart({ chartId, label, tooltip, entities, mode, seriesByEntity, path, kind, thresholds }) {
    // One line per entity (LLO or FLW, see resolveTrendEntities) instead of a
    // single collapsed line -- lets patterns/outliers across FLWs or LLOs be
    // spotted directly; hovering a line shows its entity name via Chart.js's
    // default per-dataset tooltip. The dashed linear-trend overlay only makes
    // sense for a single line, so it's skipped once there's more than one.
    var canvasRef = React.useRef(null);
    var chartInstance = React.useRef(null);

    React.useEffect(function () {
        if (!canvasRef.current || !window.Chart) return;
        if (chartInstance.current) chartInstance.current.destroy();

        var firstPoints = (entities[0] && seriesByEntity[entities[0].key] && seriesByEntity[entities[0].key][path]) || [];
        var labels = firstPoints.map(function (p) { return formatDateRange(p.period_start, p.period_end); });

        var datasets = entities.map(function (ent, i) {
            var pts = (seriesByEntity[ent.key] && seriesByEntity[ent.key][path]) || [];
            var color = colorForEntityIndex(i);
            return { label: ent.label, data: pts.map(function (p) { return p.value; }), borderColor: color, backgroundColor: color, tension: 0.15, pointRadius: 3 };
        });

        if (entities.length === 1) {
            datasets.push({ label: "Trend", data: linearTrendline(datasets[0].data), borderColor: "#9ca3af", borderDash: [4, 4], pointRadius: 0, borderWidth: 1 });
        }

        (thresholds || []).forEach(function (t, i) {
            datasets.push({
                label: t.label,
                data: labels.map(function () { return t.value; }),
                borderColor: THRESHOLD_COLORS[i % THRESHOLD_COLORS.length],
                borderDash: [2, 2],
                pointRadius: 0,
                borderWidth: 1,
            });
        });

        chartInstance.current = new window.Chart(canvasRef.current, {
            type: "line",
            data: { labels: labels, datasets: datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: entities.length > 1 || (thresholds || []).length > 0, labels: { boxWidth: 10, font: { size: 9 } } } },
                scales: { x: { ticks: { maxRotation: 45, minRotation: 45 } } },
            },
        });
        return function () { if (chartInstance.current) chartInstance.current.destroy(); };
    }, [entities, seriesByEntity, path, thresholds]);

    var modeCaption = entities.length > 1 && mode === "llo-all"
        ? (kind === "sum" ? "One line per LLO, summing its FLWs" : "One line per LLO, averaging its FLWs — approximate, not re-derived from raw visits")
        : null;

    return (
        <div className="bg-white rounded-lg border p-3">
            <div className="flex items-center text-sm font-medium text-gray-700 mb-1">
                <span>{label}</span>
                <InfoTooltip text={tooltip} />
            </div>
            {modeCaption && (
                <div className="text-[11px] text-gray-400 mb-1">{modeCaption}</div>
            )}
            <div style={{ height: "160px" }}><canvas id={chartId} ref={canvasRef}></canvas></div>
        </div>
    );
}

function CombinedLineChart({ chartId, title, indicators, series, isAll, flwCount }) {
    var canvasRef = React.useRef(null);
    var chartInstance = React.useRef(null);
    var points = series[indicators[0].path] || [];

    React.useEffect(function () {
        if (!canvasRef.current || !window.Chart) return;
        if (chartInstance.current) chartInstance.current.destroy();

        var labels = points.map(function (p) { return formatDateRange(p.period_start, p.period_end); });
        var datasets = indicators.map(function (ind) {
            var values = (series[ind.path] || []).map(function (p) { return p.value; });
            return { label: ind.label, data: values, borderColor: ind.color, backgroundColor: ind.color, tension: 0.15, pointRadius: 3 };
        });

        chartInstance.current = new window.Chart(canvasRef.current, {
            type: "line",
            data: { labels: labels, datasets: datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: true, labels: { boxWidth: 10, font: { size: 9 } } } },
                scales: { x: { ticks: { maxRotation: 45, minRotation: 45 } } },
            },
        });
        return function () { if (chartInstance.current) chartInstance.current.destroy(); };
    }, [points, series, indicators]);

    var tooltip = indicators.map(function (ind) { return ind.label + ": " + ind.tooltip; }).join(" ");

    return (
        <div className="bg-white rounded-lg border p-3">
            <div className="flex items-center text-sm font-medium text-gray-700 mb-1">
                <span>{title}</span>
                <InfoTooltip text={tooltip} />
            </div>
            {isAll && (
                <div className="text-[11px] text-gray-400 mb-1">Sum across {flwCount} FLW(s) in scope</div>
            )}
            <div style={{ height: "160px" }}><canvas id={chartId} ref={canvasRef}></canvas></div>
        </div>
    );
}

var MUAC_BUCKET_LOW_CM = 6.0;
var MUAC_BUCKET_STEP_CM = 0.5;

function muacBucketMidpoint(label) {
    if (label.indexOf("<") === 0) return MUAC_BUCKET_LOW_CM - MUAC_BUCKET_STEP_CM / 2;
    if (label.indexOf(">=") === 0) return parseFloat(label.slice(2)) + MUAC_BUCKET_STEP_CM / 2;
    var parts = label.split("-");
    return (parseFloat(parts[0]) + parseFloat(parts[1])) / 2;
}

function muacValueZoneColor(label) {
    // Same WHO SAM/MAM thresholds used elsewhere (see muacTriage), applied
    // directly to an exact recorded value label (e.g. "11.5") instead of a
    // bucket-range label (e.g. "11.5-12.0").
    var v = parseFloat(label);
    if (v < 11.5) return "#dc2626"; // red: SAM
    if (v < 12.5) return "#d97706"; // yellow: MAM
    return "#16a34a"; // green: normal
}

function sortedValueLabels(buckets) {
    return Object.keys(buckets).sort(function (a, b) { return parseFloat(a) - parseFloat(b); });
}

function muacValuesToPercents(buckets) {
    // Same buckets, expressed as % of the total children in scope rather than
    // raw counts -- lets distribution shape be compared across periods/FLW
    // scopes with very different sample sizes.
    var total = Object.keys(buckets || {}).reduce(function (a, k) { return a + (buckets[k] || 0); }, 0);
    var out = {};
    Object.keys(buckets || {}).forEach(function (k) {
        out[k] = total ? ((buckets[k] || 0) / total) * 100 : 0;
    });
    return out;
}

function muacTriage(muacBuckets) {
    // WHO SAM/MAM convention: red <11.5cm, yellow 11.5-12.5cm, green >=12.5cm.
    var red = 0, yellow = 0, green = 0;
    Object.keys(muacBuckets || {}).forEach(function (label) {
        var mid = muacBucketMidpoint(label);
        var count = muacBuckets[label] || 0;
        if (mid < 11.5) red += count;
        else if (mid < 12.5) yellow += count;
        else green += count;
    });
    return { red: red, yellow: yellow, green: green };
}

var AGE_BANDS = [
    { label: "0-5", min: 0, max: 5 },
    { label: "6-11", min: 6, max: 11 },
    { label: "12-23", min: 12, max: 23 },
    { label: "24-35", min: 24, max: 35 },
    { label: "36-47", min: 36, max: 47 },
    { label: "48-59", min: 48, max: 59 },
];

function ageBandCounts(ageByMonth) {
    var out = {};
    AGE_BANDS.forEach(function (b) { out[b.label] = 0; });
    Object.keys(ageByMonth || {}).forEach(function (monthStr) {
        var month = parseInt(monthStr, 10);
        var count = ageByMonth[monthStr] || 0;
        var band = AGE_BANDS.find(function (b) { return month >= b.min && month <= b.max; });
        if (band) out[band.label] += count;
    });
    return out;
}

var AGE_MONTH_LABELS = (function () {
    var out = [];
    for (var m = 0; m <= 59; m++) out.push(String(m));
    return out;
})();

function expectedUniformAgeMonthCounts(ageByMonth) {
    // With no heaping, every one of the 60 months should get an equal share — a flat
    // reference line at total/60 makes spikes at 12/24/36/48mo (the classic
    // heaping ages) directly visible, the same signal age_heaping_whipple_index
    // summarized as a single number.
    var total = Object.keys(ageByMonth || {}).reduce(function (a, k) { return a + (ageByMonth[k] || 0); }, 0);
    var expected = total / 60;
    return AGE_MONTH_LABELS.map(function () { return expected; });
}

function muacTriagePercents(triage) {
    var total = (triage.red || 0) + (triage.yellow || 0) + (triage.green || 0);
    if (!total) return { red: null, yellow: null, green: null };
    return {
        red: (triage.red / total) * 100,
        yellow: (triage.yellow / total) * 100,
        green: (triage.green / total) * 100,
    };
}

function ageBandPercents(bandCounts) {
    var total = Object.keys(bandCounts || {}).reduce(function (a, k) { return a + (bandCounts[k] || 0); }, 0);
    var out = {};
    AGE_BANDS.forEach(function (b) {
        out[b.label] = total ? ((bandCounts[b.label] || 0) / total) * 100 : null;
    });
    return out;
}

function sumHistograms(rows, key) {
    var out = {};
    rows.forEach(function (row) {
        var hist = row[key] || {};
        Object.keys(hist).forEach(function (bucket) {
            out[bucket] = (out[bucket] || 0) + (hist[bucket] || 0);
        });
    });
    return out;
}

function rowsForFlwFilter(flws, selectedFlw) {
    var rows = flws || [];
    return selectedFlw === "__all__" ? rows : rows.filter(function (f) { return f.username === selectedFlw; });
}

function DistributionTrendCharts({ weeks, selectedFlw, flwName, selectedOpp, oppNames, nameMap, opportunityOptions }) {
    var distinctPeriods = React.useMemo(function () {
        var seen = {};
        var out = [];
        weeks.forEach(function (w) {
            if (w.period_start && !seen[w.period_start]) { seen[w.period_start] = true; out.push(w.period_start); }
        });
        return out;
    }, [weeks]);

    var periodEndByStart = React.useMemo(function () {
        var m = {};
        weeks.forEach(function (w) { if (w.period_start && !m[w.period_start] && w.period_end) m[w.period_start] = w.period_end; });
        return m;
    }, [weeks]);

    // MUAC Zone Composition splits into one bar-group per entity (LLO or FLW,
    // same rule as the Trends tab's line charts -- see resolveTrendEntities).
    // Children by Age Band intentionally does NOT split this way (kept as one
    // combined view, respecting only the FLW filter) -- 6 bands x up to 10
    // FLWs would be 60 lines, unreadable.
    var trendMode = React.useMemo(function () {
        return resolveTrendEntities(weeks, selectedOpp, selectedFlw, oppNames, nameMap, opportunityOptions);
    }, [weeks, selectedOpp, selectedFlw, oppNames, nameMap, opportunityOptions]);

    var distinctPeriodList = React.useMemo(function () { return distinctPeriodsFromWeeks(weeks); }, [weeks]);

    var muacEntitySeries = React.useMemo(function () {
        var out = {};
        trendMode.entities.forEach(function (ent) { out[ent.key] = []; });
        distinctPeriodList.forEach(function (period) {
            var weeksThisPeriod = weeks.filter(function (w) { return w.period_start === period.period_start; });
            trendMode.entities.forEach(function (ent) {
                var rows = rowsForEntity(weeksThisPeriod, ent, trendMode.mode);
                var t = muacTriage(sumHistograms(rows, "children_by_muac_bucket"));
                out[ent.key].push({ period_start: period.period_start, period_end: period.period_end, value: t });
            });
        });
        return out;
    }, [trendMode, distinctPeriodList, weeks]);

    var ageTrendSeries = React.useMemo(function () {
        var byPeriod = {};
        weeks.forEach(function (w) {
            var bands = ageBandCounts(sumHistograms(rowsForFlwFilter(w.flws, selectedFlw), "children_by_age_month"));
            byPeriod[w.period_start] = bands;
        });
        return distinctPeriods.map(function (p) { return { period_start: p, period_end: periodEndByStart[p], value: byPeriod[p] || {} }; });
    }, [weeks, distinctPeriods, periodEndByStart, selectedFlw]);

    return (
        <div>
            <h2 className="text-lg font-semibold mb-2">Distribution Trend — {flwName}</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <MuacTrendChart chartId="muac-trend" entities={trendMode.entities} mode={trendMode.mode} entitySeries={muacEntitySeries} />
                <AgeBandTrendChart chartId="age-trend" series={ageTrendSeries} />
            </div>
        </div>
    );
}

function DistributionSnapshotSection({ weeks, distinctPeriods, periodEndByStart, selectedPeriod, onChangePeriod, selectedFlw, flwName }) {
    var isAllWeeks = selectedPeriod === "__all__";

    var weekRowsForPeriod = React.useMemo(function () {
        if (isAllWeeks) return weeks;
        return weeks.filter(function (w) { return w.period_start === selectedPeriod; });
    }, [weeks, selectedPeriod, isAllWeeks]);

    var flwRowsForPeriod = React.useMemo(function () {
        var out = [];
        weekRowsForPeriod.forEach(function (w) { out = out.concat(rowsForFlwFilter(w.flws, selectedFlw)); });
        return out;
    }, [weekRowsForPeriod, selectedFlw]);

    var muacValues = React.useMemo(function () { return sumHistograms(flwRowsForPeriod, "children_by_muac_value"); }, [flwRowsForPeriod]);
    var muacValuePercents = React.useMemo(function () { return muacValuesToPercents(muacValues); }, [muacValues]);
    var ageByMonth = React.useMemo(function () { return sumHistograms(flwRowsForPeriod, "children_by_age_month"); }, [flwRowsForPeriod]);
    var ageByMonthExpected = React.useMemo(function () { return expectedUniformAgeMonthCounts(ageByMonth); }, [ageByMonth]);

    var coverageCounts = React.useMemo(function () {
        var out = {};
        COVERAGE_INDICATORS.forEach(function (ind) { out[ind.path] = combineForWeek(flwRowsForPeriod, ind.path, ind.kind); });
        return out;
    }, [flwRowsForPeriod]);

    return (
        <div>
            <div className="flex items-center gap-3 mb-2">
                <h2 className="text-lg font-semibold">Distribution Snapshot — {flwName}</h2>
                <select
                    className="border rounded px-2 py-1 text-sm"
                    value={selectedPeriod || ""}
                    onChange={function (e) { onChangePeriod(e.target.value); }}
                >
                    <option value="__all__">All Weeks</option>
                    {distinctPeriods.map(function (p) { return <option key={p} value={p}>{formatDateRange(p, periodEndByStart[p])}</option>; })}
                </select>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <BarHistogramChart
                    chartId="muac-value-hist"
                    label="MUAC Distribution (Recorded Values)"
                    tooltip="Count of children at each exact recorded MUAC value (not grouped into 0.5cm ranges), colored by WHO zone."
                    buckets={muacValues}
                    order={sortedValueLabels(muacValues)}
                    barColorFn={muacValueZoneColor}
                />
                <BarHistogramChart
                    chartId="muac-value-hist-pct"
                    label="MUAC Distribution (% of Children)"
                    tooltip="Share of children at each exact recorded MUAC value (not grouped into 0.5cm ranges), colored by WHO zone."
                    buckets={muacValuePercents}
                    order={sortedValueLabels(muacValues)}
                    barColorFn={muacValueZoneColor}
                    percentAxis={true}
                />
                <BarHistogramChart
                    chartId="age-month-hist"
                    label="Children by Age (Individual Months)"
                    tooltip="Count of children at each individual age in months (not grouped into bands); the dashed line is the expected count per month if ages were evenly spread with no heaping — spikes at 12/24/36/48mo indicate age-heaping."
                    buckets={ageByMonth}
                    order={AGE_MONTH_LABELS}
                    referenceLine={{ label: "Expected (uniform, no heaping)", values: ageByMonthExpected }}
                />
                <CoverageBarChart chartId="coverage-hist" indicators={COVERAGE_INDICATORS} counts={coverageCounts} />
            </div>
        </div>
    );
}

function sortedBucketLabels(buckets, order) {
    if (order) return order;
    return Object.keys(buckets).sort(function (a, b) { return muacBucketMidpoint(a) - muacBucketMidpoint(b); });
}

function BarHistogramChart({ chartId, label, tooltip, buckets, order, barColorFn, referenceLine, percentAxis }) {
    var canvasRef = React.useRef(null);
    var chartInstance = React.useRef(null);

    React.useEffect(function () {
        if (!canvasRef.current || !window.Chart) return;
        if (chartInstance.current) chartInstance.current.destroy();

        var labels = sortedBucketLabels(buckets, order);
        var values = labels.map(function (l) { return buckets[l] || 0; });

        var datasets = [{
            label: label,
            data: values,
            backgroundColor: barColorFn ? labels.map(barColorFn) : "#60a5fa",
            order: 2,
        }];
        if (referenceLine) {
            datasets.push({
                type: "line",
                label: referenceLine.label,
                data: referenceLine.values,
                borderColor: "#6b7280",
                borderDash: [4, 4],
                pointRadius: 0,
                borderWidth: 1,
                order: 1,
            });
        }

        chartInstance.current = new window.Chart(canvasRef.current, {
            type: "bar",
            data: { labels: labels, datasets: datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: !!referenceLine, labels: { boxWidth: 10, font: { size: 9 } } } },
                scales: percentAxis ? { y: { ticks: { callback: function (v) { return v + "%"; } } } } : undefined,
            },
        });
        return function () { if (chartInstance.current) chartInstance.current.destroy(); };
    }, [buckets, label, order, referenceLine, percentAxis]);

    return (
        <div className="bg-white rounded-lg border p-3">
            <div className="flex items-center text-sm font-medium text-gray-700 mb-1">
                <span>{label}</span>
                <InfoTooltip text={tooltip} />
            </div>
            <div style={{ height: "220px" }}><canvas id={chartId} ref={canvasRef}></canvas></div>
        </div>
    );
}

function CoverageBarChart({ chartId, indicators, counts }) {
    var canvasRef = React.useRef(null);
    var chartInstance = React.useRef(null);

    React.useEffect(function () {
        if (!canvasRef.current || !window.Chart) return;
        if (chartInstance.current) chartInstance.current.destroy();

        var labels = indicators.map(function (ind) { return ind.label; });
        var values = indicators.map(function (ind) { return counts[ind.path] || 0; });
        var colors = indicators.map(function (ind) { return ind.color; });

        chartInstance.current = new window.Chart(canvasRef.current, {
            type: "bar",
            data: { labels: labels, datasets: [{ label: "Children", data: values, backgroundColor: colors }] },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } },
        });
        return function () { if (chartInstance.current) chartInstance.current.destroy(); };
    }, [indicators, counts]);

    var tooltip = indicators.map(function (ind) { return ind.label + ": " + ind.tooltip; }).join(" ");

    return (
        <div className="bg-white rounded-lg border p-3">
            <div className="flex items-center text-sm font-medium text-gray-700 mb-1">
                <span>Service Coverage</span>
                <InfoTooltip text={tooltip} />
            </div>
            <div style={{ height: "220px" }}><canvas id={chartId} ref={canvasRef}></canvas></div>
        </div>
    );
}

function MuacTrendChart({ chartId, entities, mode, entitySeries }) {
    // Rendered as a 100%-stacked bar per entity (not 3 overlapping lines):
    // with red and yellow both near/at zero most weeks, their lines would sit
    // on top of each other at y=0 and one becomes invisible behind the other.
    // A stacked bar shows each zone as its own visible-width segment. When
    // multiple entities (LLOs or FLWs) are in scope, each week shows one
    // stacked bar PER entity, touching its neighbors (barPercentage: 1) with
    // a gap between weeks (categoryPercentage < 1) -- via Chart.js's
    // grouped-stacked-bar support (datasets sharing a `stack` id stack on top
    // of each other; different `stack` ids group side by side).
    var canvasRef = React.useRef(null);
    var chartInstance = React.useRef(null);

    React.useEffect(function () {
        if (!canvasRef.current || !window.Chart) return;
        if (chartInstance.current) chartInstance.current.destroy();

        var firstSeries = (entities[0] && entitySeries[entities[0].key]) || [];
        var labels = firstSeries.map(function (s) { return formatDateRange(s.period_start, s.period_end); });

        var datasets = [];
        entities.forEach(function (ent, i) {
            var pcts = (entitySeries[ent.key] || []).map(function (s) { return muacTriagePercents(s.value); });
            var stackId = "ent" + i;
            datasets.push({ label: "Red", stack: stackId, backgroundColor: "#dc2626", data: pcts.map(function (p) { return p.red; }), _entityLabel: ent.label, _zone: "Red (SAM, <11.5cm)" });
            datasets.push({ label: "Yellow", stack: stackId, backgroundColor: "#d97706", data: pcts.map(function (p) { return p.yellow; }), _entityLabel: ent.label, _zone: "Yellow (MAM, 11.5-12.5cm)" });
            datasets.push({ label: "Green", stack: stackId, backgroundColor: "#16a34a", data: pcts.map(function (p) { return p.green; }), _entityLabel: ent.label, _zone: "Green (Normal, ≥12.5cm)" });
        });

        chartInstance.current = new window.Chart(canvasRef.current, {
            type: "bar",
            data: { labels: labels, datasets: datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: { stacked: true, categoryPercentage: 0.9, barPercentage: 1.0 },
                    y: { stacked: true, min: 0, max: 100, ticks: { callback: function (v) { return v + "%"; } } },
                },
                plugins: {
                    legend: {
                        labels: {
                            filter: function (item, data) {
                                return data.datasets.findIndex(function (d) { return d.label === item.text; }) === item.datasetIndex;
                            },
                        },
                    },
                    tooltip: {
                        callbacks: {
                            label: function (context) {
                                var ds = context.dataset;
                                var v = context.raw;
                                var pct = v === null || v === undefined ? "–" : v.toFixed(1) + "%";
                                return (entities.length > 1 ? ds._entityLabel + " — " : "") + ds._zone + ": " + pct;
                            },
                        },
                    },
                },
            },
        });
        return function () { if (chartInstance.current) chartInstance.current.destroy(); };
    }, [entities, entitySeries]);

    var tooltipText = entities.length > 1
        ? "Weekly % of children in each WHO MUAC zone (red/yellow/green), stacked to 100% -- one bar per " + (mode === "llo-all" ? "LLO" : "FLW") + ", hover for exact values."
        : "Weekly % of children in each WHO MUAC zone (red/yellow/green), stacked to 100%.";

    return (
        <div className="bg-white rounded-lg border p-3">
            <div className="flex items-center text-sm font-medium text-gray-700 mb-1">
                <span>MUAC Zone Composition Over Time</span>
                <InfoTooltip text={tooltipText} />
            </div>
            <div style={{ height: "220px" }}><canvas id={chartId} ref={canvasRef}></canvas></div>
        </div>
    );
}

var AGE_BAND_COLORS = ["#2563eb", "#7c3aed", "#db2777", "#d97706", "#16a34a", "#0891b2"];

function AgeBandTrendChart({ chartId, series }) {
    var canvasRef = React.useRef(null);
    var chartInstance = React.useRef(null);

    React.useEffect(function () {
        if (!canvasRef.current || !window.Chart) return;
        if (chartInstance.current) chartInstance.current.destroy();

        var labels = series.map(function (s) { return formatDateRange(s.period_start, s.period_end); });
        var pcts = series.map(function (s) { return ageBandPercents(s.value || {}); });
        var datasets = AGE_BANDS.map(function (band, i) {
            return {
                label: band.label + "mo",
                data: pcts.map(function (p) { return p[band.label]; }),
                borderColor: AGE_BAND_COLORS[i % AGE_BAND_COLORS.length],
                tension: 0.15,
            };
        });
        chartInstance.current = new window.Chart(canvasRef.current, {
            type: "line",
            data: { labels: labels, datasets: datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: { y: { min: 0, max: 100, ticks: { callback: function (v) { return v + "%"; } } } },
            },
        });
        return function () { if (chartInstance.current) chartInstance.current.destroy(); };
    }, [series]);

    return (
        <div className="bg-white rounded-lg border p-3">
            <div className="flex items-center text-sm font-medium text-gray-700 mb-1">
                <span>Children by Age Band Over Time (% of children)</span>
                <InfoTooltip text="Weekly % of children in each age band over time." />
            </div>
            <div style={{ height: "220px" }}><canvas id={chartId} ref={canvasRef}></canvas></div>
        </div>
    );
}"""

TEMPLATE = {
    "key": "flw_audit_trend_dashboard",
    "name": "FLW Audit Trend Dashboard",
    "description": DEFINITION["description"],
    "icon": "fa-chart-line",
    "color": "indigo",
    "multi_opp": True,
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
}
