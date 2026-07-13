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
        "Audit Report's saved weekly snapshots — filter by opportunity and FLW, see indicator "
        "trends and MUAC/age distribution snapshots + trends."
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

    var _selectedPeriod = React.useState(null);
    var selectedPeriod = _selectedPeriod[0];
    var setSelectedPeriod = _selectedPeriod[1];

    React.useEffect(function () {
        if (distinctPeriods.length === 0) return;
        if (!selectedPeriod || distinctPeriods.indexOf(selectedPeriod) === -1) {
            setSelectedPeriod(distinctPeriods[distinctPeriods.length - 1]);
        }
    }, [distinctPeriods]);

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

            <TrendSection
                weeks={sortedWeeks}
                flw={selectedFlw}
                flwName={flwScopeLabel}
                flwCount={flwOptions.length}
            />

            <DistributionSection
                weeks={sortedWeeks}
                distinctPeriods={distinctPeriods}
                selectedPeriod={selectedPeriod}
                onChangePeriod={setSelectedPeriod}
                selectedFlw={selectedFlw}
                flwName={flwScopeLabel}
            />
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
var TREND_INDICATORS = [
    { path: "total_service_delivery_forms", label: "Total Service Delivery Forms", kind: "sum", tooltip: "Total Health Service Delivery forms submitted this week." },
    { path: "total_approved_visits", label: "Total Approved Visits", kind: "sum", tooltip: "Total visits approved in Connect this week." },
    { path: "days_worked", label: "Days Worked", kind: "sum", tooltip: "Number of distinct days with at least one visit this week." },
    { path: "avg_children_per_household", label: "Avg Children per Household", kind: "avg", tooltip: "Average number of distinct children seen per household visited this week." },
    { path: "pct_gap_lt_3min", label: "% Gap < 3min Between Forms", kind: "avg", tooltip: "Share of consecutive form submissions less than 3 minutes apart — a possible sign of rushed or fabricated visits." },
    { path: "median_gap_minutes", label: "Median Gap (minutes)", kind: "avg", tooltip: "Median time between consecutive form submissions this week." },
    { path: "avg_distance_between_visits_m", label: "Avg Distance Between Visits (m)", kind: "avg", tooltip: "Average GPS distance between consecutive visits this week." },
    { path: "avg_time_first_last_visit_minutes", label: "Avg First→Last Visit Span (min/day)", kind: "avg", tooltip: "Average span between the first and last visit on each day worked." },
    { path: "pct_same_dob_within_household", label: "% Same-DOB Within Household", kind: "avg", tooltip: "Share of households with two or more children sharing the same date of birth — a possible data-entry duplicate." },
    { path: "fraud.gps_accuracy_flag_pct", label: "GPS Accuracy Flag %", kind: "avg", tooltip: "Share of visits with GPS accuracy worse than 100m, or exactly 0 (no fix)." },
    { path: "fraud.gps_near_duplicate_count", label: "GPS Near-Duplicate Count", kind: "sum", tooltip: "Visits within 10m of another visit logged to a different household the same day." },
    { path: "fraud.implied_speed_flag_count", label: "Implied-Speed Flag Count", kind: "sum", tooltip: "Consecutive visits implying travel faster than 60 km/h." },
    { path: "fraud.form_duration_outlier_count", label: "Form Duration Outlier Count", kind: "sum", tooltip: "Forms completed in under 2 minutes." },
    {
        path: "fraud.age_heaping_whipple_index",
        label: "Age-Heaping Whipple Index",
        kind: "avg",
        tooltip: "Measure of age heaping at 12/24/36/48 months — 100 is expected with no heaping, values above 125 are flagged.",
        thresholds: [
            { value: 100, label: "Expected (no heaping)" },
            { value: 125, label: "Flag threshold" },
        ],
    },
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

function TrendSection({ weeks, flw, flwName, flwCount }) {
    var isAll = flw === "__all__";

    var series = React.useMemo(function () {
        var out = {};
        TREND_INDICATORS.forEach(function (ind) { out[ind.path] = []; });
        weeks.forEach(function (w) {
            var rows = w.flws || [];
            var relevant = isAll ? rows : rows.filter(function (f) { return f.username === flw; });
            TREND_INDICATORS.forEach(function (ind) {
                out[ind.path].push({ period_start: w.period_start, value: combineForWeek(relevant, ind.path, ind.kind) });
            });
        });
        return out;
    }, [weeks, flw, isAll]);

    return (
        <div>
            <h2 className="text-lg font-semibold mb-2">Trends — {flwName}</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {TREND_INDICATORS.map(function (ind) {
                    return (
                        <LineTrendChart
                            key={ind.path}
                            chartId={"trend-" + ind.path}
                            label={ind.label}
                            tooltip={ind.tooltip}
                            points={series[ind.path] || []}
                            isAll={isAll}
                            kind={ind.kind}
                            flwCount={flwCount}
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

function LineTrendChart({ chartId, label, tooltip, points, isAll, kind, flwCount, thresholds }) {
    var canvasRef = React.useRef(null);
    var chartInstance = React.useRef(null);

    React.useEffect(function () {
        if (!canvasRef.current || !window.Chart) return;
        if (chartInstance.current) chartInstance.current.destroy();

        var labels = points.map(function (p) { return formatShortDate(p.period_start); });
        var values = points.map(function (p) { return p.value; });
        var trend = linearTrendline(values);

        var datasets = [
            { label: label, data: values, borderColor: "#2563eb", backgroundColor: "#2563eb", tension: 0.15, pointRadius: 3 },
            { label: "Trend", data: trend, borderColor: "#9ca3af", borderDash: [4, 4], pointRadius: 0, borderWidth: 1 },
        ];
        (thresholds || []).forEach(function (t, i) {
            datasets.push({
                label: t.label,
                data: values.map(function () { return t.value; }),
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
                plugins: { legend: { display: (thresholds || []).length > 0, labels: { boxWidth: 10, font: { size: 9 } } } },
                scales: { x: { ticks: { maxRotation: 45, minRotation: 45 } } },
            },
        });
        return function () { if (chartInstance.current) chartInstance.current.destroy(); };
    }, [points, label, thresholds]);

    return (
        <div className="bg-white rounded-lg border p-3">
            <div className="flex items-center text-sm font-medium text-gray-700 mb-1">
                <span>{label}</span>
                <InfoTooltip text={tooltip} />
            </div>
            {isAll && (
                <div className="text-[11px] text-gray-400 mb-1">
                    {kind === "sum"
                        ? "Sum across " + flwCount + " FLW(s) in scope"
                        : "Avg across " + flwCount + " FLW(s) in scope — approximate, not re-derived from raw visits"}
                </div>
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

function muacZoneColor(label) {
    var mid = muacBucketMidpoint(label);
    if (mid < 11.5) return "#dc2626"; // red: SAM
    if (mid < 12.5) return "#d97706"; // yellow: MAM
    return "#16a34a"; // green: normal
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

function expectedUniformAgeBandCounts(ageBands) {
    // If ages were evenly spread across all 60 months with no heaping, each
    // band's expected share is proportional to its width (in months) out of
    // 60 — the same "uniform" assumption Workflow 1's Whipple's-index-style
    // age-heaping check is built on (see flw_audit_compute.py whipple_index).
    var total = Object.keys(ageBands).reduce(function (a, k) { return a + (ageBands[k] || 0); }, 0);
    return AGE_BANDS.map(function (b) { return total * (b.max - b.min + 1) / 60; });
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

function DistributionSection({ weeks, distinctPeriods, selectedPeriod, onChangePeriod, selectedFlw, flwName }) {
    var weekRowsForPeriod = React.useMemo(function () {
        return weeks.filter(function (w) { return w.period_start === selectedPeriod; });
    }, [weeks, selectedPeriod]);

    var flwRowsForPeriod = React.useMemo(function () {
        var out = [];
        weekRowsForPeriod.forEach(function (w) { out = out.concat(rowsForFlwFilter(w.flws, selectedFlw)); });
        return out;
    }, [weekRowsForPeriod, selectedFlw]);

    var muacBuckets = React.useMemo(function () { return sumHistograms(flwRowsForPeriod, "children_by_muac_bucket"); }, [flwRowsForPeriod]);
    var ageBuckets = React.useMemo(function () { return ageBandCounts(sumHistograms(flwRowsForPeriod, "children_by_age_month")); }, [flwRowsForPeriod]);
    var ageExpected = React.useMemo(function () { return expectedUniformAgeBandCounts(ageBuckets); }, [ageBuckets]);

    var muacTrendSeries = React.useMemo(function () {
        var byPeriod = {};
        weeks.forEach(function (w) {
            var t = muacTriage(sumHistograms(rowsForFlwFilter(w.flws, selectedFlw), "children_by_muac_bucket"));
            if (!byPeriod[w.period_start]) byPeriod[w.period_start] = { red: 0, yellow: 0, green: 0 };
            byPeriod[w.period_start].red += t.red;
            byPeriod[w.period_start].yellow += t.yellow;
            byPeriod[w.period_start].green += t.green;
        });
        return distinctPeriods.map(function (p) { return { period_start: p, value: byPeriod[p] || { red: 0, yellow: 0, green: 0 } }; });
    }, [weeks, distinctPeriods, selectedFlw]);

    var ageTrendSeries = React.useMemo(function () {
        var byPeriod = {};
        weeks.forEach(function (w) {
            var bands = ageBandCounts(sumHistograms(rowsForFlwFilter(w.flws, selectedFlw), "children_by_age_month"));
            byPeriod[w.period_start] = bands;
        });
        return distinctPeriods.map(function (p) { return { period_start: p, value: byPeriod[p] || {} }; });
    }, [weeks, distinctPeriods, selectedFlw]);

    return (
        <div className="space-y-6">
            <div>
                <div className="flex items-center gap-3 mb-2">
                    <h2 className="text-lg font-semibold">Distribution Snapshot — {flwName}</h2>
                    <select
                        className="border rounded px-2 py-1 text-sm"
                        value={selectedPeriod || ""}
                        onChange={function (e) { onChangePeriod(e.target.value); }}
                    >
                        {distinctPeriods.map(function (p) { return <option key={p} value={p}>{formatShortDate(p)}</option>; })}
                    </select>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <BarHistogramChart
                        chartId="muac-hist"
                        label="MUAC Distribution (0.5cm buckets)"
                        tooltip="Distribution of children's MUAC measurements this week, colored by WHO zone: red = SAM (<11.5cm), yellow = MAM (11.5-12.5cm), green = normal (≥12.5cm)."
                        buckets={muacBuckets}
                        barColorFn={muacZoneColor}
                    />
                    <BarHistogramChart
                        chartId="age-hist"
                        label="Children by Age Band (months)"
                        tooltip="Distribution of children's ages this week; the dashed line is the expected count per band if ages were evenly spread with no heaping."
                        buckets={ageBuckets}
                        order={AGE_BANDS.map(function (b) { return b.label; })}
                        referenceLine={{ label: "Expected (uniform, no heaping)", values: ageExpected }}
                    />
                </div>
            </div>

            <div>
                <h2 className="text-lg font-semibold mb-2">Distribution Trend — {flwName}</h2>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <MuacTrendChart chartId="muac-trend" series={muacTrendSeries} />
                    <AgeBandTrendChart chartId="age-trend" series={ageTrendSeries} />
                </div>
            </div>
        </div>
    );
}

function sortedBucketLabels(buckets, order) {
    if (order) return order;
    return Object.keys(buckets).sort(function (a, b) { return muacBucketMidpoint(a) - muacBucketMidpoint(b); });
}

function BarHistogramChart({ chartId, label, tooltip, buckets, order, barColorFn, referenceLine }) {
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
            },
        });
        return function () { if (chartInstance.current) chartInstance.current.destroy(); };
    }, [buckets, label, order, referenceLine]);

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

function MuacTrendChart({ chartId, series }) {
    var canvasRef = React.useRef(null);
    var chartInstance = React.useRef(null);

    React.useEffect(function () {
        if (!canvasRef.current || !window.Chart) return;
        if (chartInstance.current) chartInstance.current.destroy();

        var labels = series.map(function (s) { return formatShortDate(s.period_start); });
        chartInstance.current = new window.Chart(canvasRef.current, {
            type: "line",
            data: {
                labels: labels,
                datasets: [
                    { label: "Red (SAM, <11.5cm)", data: series.map(function (s) { return s.value.red; }), borderColor: "#dc2626", tension: 0.15 },
                    { label: "Yellow (MAM, 11.5-12.5cm)", data: series.map(function (s) { return s.value.yellow; }), borderColor: "#d97706", tension: 0.15 },
                    { label: "Green (Normal, ≥12.5cm)", data: series.map(function (s) { return s.value.green; }), borderColor: "#16a34a", tension: 0.15 },
                ],
            },
            options: { responsive: true, maintainAspectRatio: false },
        });
        return function () { if (chartInstance.current) chartInstance.current.destroy(); };
    }, [series]);

    return (
        <div className="bg-white rounded-lg border p-3">
            <div className="flex items-center text-sm font-medium text-gray-700 mb-1">
                <span>MUAC Bucket Counts Over Time</span>
                <InfoTooltip text="Weekly count of children in each WHO MUAC zone (red/yellow/green) over time." />
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

        var labels = series.map(function (s) { return formatShortDate(s.period_start); });
        var datasets = AGE_BANDS.map(function (band, i) {
            return {
                label: band.label + "mo",
                data: series.map(function (s) { return (s.value && s.value[band.label]) || 0; }),
                borderColor: AGE_BAND_COLORS[i % AGE_BAND_COLORS.length],
                tension: 0.15,
            };
        });
        chartInstance.current = new window.Chart(canvasRef.current, {
            type: "line",
            data: { labels: labels, datasets: datasets },
            options: { responsive: true, maintainAspectRatio: false },
        });
        return function () { if (chartInstance.current) chartInstance.current.destroy(); };
    }, [series]);

    return (
        <div className="bg-white rounded-lg border p-3">
            <div className="flex items-center text-sm font-medium text-gray-700 mb-1">
                <span>Children by Age Band Over Time</span>
                <InfoTooltip text="Weekly count of children in each age band over time." />
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
