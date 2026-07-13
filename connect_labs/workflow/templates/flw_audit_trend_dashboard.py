"""FLW Audit Trend Dashboard — Program 176 (CHC PRE-RCT Nigeria).

Workflow 2 of a two-workflow pair (see flw_audit_workflows_spec.md and
flw_weekly_audit_report.py, "Workflow 1"). Reads Workflow 1's saved weekly
snapshots (one completed WorkflowRun per opportunity per week) via a
dedicated read-only API endpoint (api/flw-audit-report-history/) and
displays them as trend lines (scalar indicators, for the selected FLW) and
distribution snapshots + trends (MUAC buckets, age-in-months buckets,
aggregated across FLWs in the selected opportunity scope).

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

RENDER_CODE = r"""function WorkflowUI({ definition }) {
    var sourceDefinitionId = definition.config && definition.config.source_definition_id;

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

    var _selectedOpps = React.useState(null); // null = all
    var selectedOpps = _selectedOpps[0];
    var setSelectedOpps = _selectedOpps[1];

    var effectiveOpps = selectedOpps === null ? opportunityOptions : selectedOpps;

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

    var _selectedFlw = React.useState("");
    var selectedFlw = _selectedFlw[0];
    var setSelectedFlw = _selectedFlw[1];

    React.useEffect(function () {
        if (!selectedFlw && flwOptions.length > 0) setSelectedFlw(flwOptions[0]);
        if (selectedFlw && flwOptions.indexOf(selectedFlw) === -1 && flwOptions.length > 0) {
            setSelectedFlw(flwOptions[0]);
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

    return (
        <div className="space-y-6 p-4">
            <div>
                <h1 className="text-xl font-bold">{definition.name}</h1>
                <p className="text-sm text-gray-500">{definition.description}</p>
            </div>

            <FilterBar
                opportunityOptions={opportunityOptions}
                selectedOpps={selectedOpps}
                onChangeOpps={setSelectedOpps}
                flwOptions={flwOptions}
                selectedFlw={selectedFlw}
                onChangeFlw={setSelectedFlw}
            />

            <TrendSection weeks={sortedWeeks} flw={selectedFlw} />

            <DistributionSection
                weeks={sortedWeeks}
                distinctPeriods={distinctPeriods}
                selectedPeriod={selectedPeriod}
                onChangePeriod={setSelectedPeriod}
            />
        </div>
    );
}

function FilterBar({ opportunityOptions, selectedOpps, onChangeOpps, flwOptions, selectedFlw, onChangeFlw }) {
    var effective = selectedOpps === null ? opportunityOptions : selectedOpps;

    function toggleOpp(oppId) {
        var next = effective.slice();
        var idx = next.indexOf(oppId);
        if (idx === -1) next.push(oppId); else next.splice(idx, 1);
        onChangeOpps(next.length === opportunityOptions.length ? null : next);
    }

    return (
        <div className="bg-white rounded-lg border p-4 flex flex-wrap gap-6 items-start">
            <div>
                <div className="text-xs font-semibold text-gray-500 uppercase mb-1">Opportunities</div>
                <div className="flex flex-wrap gap-2">
                    <button
                        className={"px-2 py-1 rounded text-sm border " + (selectedOpps === null ? "bg-blue-600 text-white border-blue-600" : "bg-white text-gray-700 border-gray-300")}
                        onClick={function () { onChangeOpps(null); }}
                    >
                        All
                    </button>
                    {opportunityOptions.map(function (oppId) {
                        var active = effective.indexOf(oppId) !== -1;
                        return (
                            <button
                                key={oppId}
                                className={"px-2 py-1 rounded text-sm border " + (active ? "bg-blue-100 text-blue-800 border-blue-300" : "bg-white text-gray-500 border-gray-300")}
                                onClick={function () { toggleOpp(oppId); }}
                            >
                                Opp {oppId}
                            </button>
                        );
                    })}
                </div>
            </div>
            <div>
                <div className="text-xs font-semibold text-gray-500 uppercase mb-1">FLW (single-select)</div>
                <select
                    className="border rounded px-2 py-1 text-sm"
                    value={selectedFlw}
                    onChange={function (e) { onChangeFlw(e.target.value); }}
                >
                    {flwOptions.length === 0 && <option value="">No FLWs in scope</option>}
                    {flwOptions.map(function (u) {
                        return <option key={u} value={u}>{u}</option>;
                    })}
                </select>
            </div>
        </div>
    );
}

var TREND_INDICATORS = [
    { path: "total_service_delivery_forms", label: "Total Service Delivery Forms" },
    { path: "total_approved_visits", label: "Total Approved Visits" },
    { path: "days_worked", label: "Days Worked" },
    { path: "avg_children_per_household", label: "Avg Children per Household" },
    { path: "pct_gap_lt_3min", label: "% Gap < 3min Between Forms" },
    { path: "median_gap_minutes", label: "Median Gap (minutes)" },
    { path: "avg_distance_between_visits_m", label: "Avg Distance Between Visits (m)" },
    { path: "avg_time_first_last_visit_minutes", label: "Avg First→Last Visit Span (min/day)" },
    { path: "pct_same_dob_within_household", label: "% Same-DOB Within Household" },
    { path: "fraud.gps_accuracy_flag_pct", label: "GPS Accuracy Flag %" },
    { path: "fraud.gps_near_duplicate_count", label: "GPS Near-Duplicate Count" },
    { path: "fraud.implied_speed_flag_count", label: "Implied-Speed Flag Count" },
    { path: "fraud.form_duration_outlier_count", label: "Form Duration Outlier Count" },
    { path: "fraud.age_heaping_whipple_index", label: "Age-Heaping Whipple Index" },
    { path: "fraud.duplicate_child_count", label: "Duplicate Child Count" },
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

function TrendSection({ weeks, flw }) {
    var series = React.useMemo(function () {
        if (!flw) return {};
        var out = {};
        TREND_INDICATORS.forEach(function (ind) { out[ind.path] = []; });
        weeks.forEach(function (w) {
            var flwRow = (w.flws || []).find(function (f) { return f.username === flw; });
            if (!flwRow) return;
            TREND_INDICATORS.forEach(function (ind) {
                out[ind.path].push({ period_start: w.period_start, value: getByPath(flwRow, ind.path) });
            });
        });
        return out;
    }, [weeks, flw]);

    if (!flw) {
        return <div className="text-gray-500 text-sm">Select an FLW above to see their indicator trends.</div>;
    }

    return (
        <div>
            <h2 className="text-lg font-semibold mb-2">Trends — {flw}</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {TREND_INDICATORS.map(function (ind) {
                    return (
                        <LineTrendChart
                            key={ind.path}
                            chartId={"trend-" + ind.path}
                            label={ind.label}
                            points={series[ind.path] || []}
                        />
                    );
                })}
            </div>
        </div>
    );
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

function LineTrendChart({ chartId, label, points }) {
    var canvasRef = React.useRef(null);
    var chartInstance = React.useRef(null);

    React.useEffect(function () {
        if (!canvasRef.current || !window.Chart) return;
        if (chartInstance.current) chartInstance.current.destroy();

        var labels = points.map(function (p) { return p.period_start; });
        var values = points.map(function (p) { return p.value; });
        var trend = linearTrendline(values);

        chartInstance.current = new window.Chart(canvasRef.current, {
            type: "line",
            data: {
                labels: labels,
                datasets: [
                    { label: label, data: values, borderColor: "#2563eb", backgroundColor: "#2563eb", tension: 0.15, pointRadius: 3 },
                    { label: "Trend", data: trend, borderColor: "#9ca3af", borderDash: [4, 4], pointRadius: 0, borderWidth: 1 },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: { x: { ticks: { maxRotation: 45, minRotation: 45 } } },
            },
        });
        return function () { if (chartInstance.current) chartInstance.current.destroy(); };
    }, [points, label]);

    return (
        <div className="bg-white rounded-lg border p-3">
            <div className="text-sm font-medium text-gray-700 mb-1">{label}</div>
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

function DistributionSection({ weeks, distinctPeriods, selectedPeriod, onChangePeriod }) {
    var weekRowsForPeriod = React.useMemo(function () {
        return weeks.filter(function (w) { return w.period_start === selectedPeriod; });
    }, [weeks, selectedPeriod]);

    var allFlwRowsForPeriod = React.useMemo(function () {
        var out = [];
        weekRowsForPeriod.forEach(function (w) { out = out.concat(w.flws || []); });
        return out;
    }, [weekRowsForPeriod]);

    var muacBuckets = React.useMemo(function () { return sumHistograms(allFlwRowsForPeriod, "children_by_muac_bucket"); }, [allFlwRowsForPeriod]);
    var ageBuckets = React.useMemo(function () { return ageBandCounts(sumHistograms(allFlwRowsForPeriod, "children_by_age_month")); }, [allFlwRowsForPeriod]);

    var muacTrendSeries = React.useMemo(function () {
        var byPeriod = {};
        weeks.forEach(function (w) {
            var t = muacTriage(sumHistograms(w.flws || [], "children_by_muac_bucket"));
            if (!byPeriod[w.period_start]) byPeriod[w.period_start] = { red: 0, yellow: 0, green: 0 };
            byPeriod[w.period_start].red += t.red;
            byPeriod[w.period_start].yellow += t.yellow;
            byPeriod[w.period_start].green += t.green;
        });
        return distinctPeriods.map(function (p) { return { period_start: p, value: byPeriod[p] || { red: 0, yellow: 0, green: 0 } }; });
    }, [weeks, distinctPeriods]);

    var ageTrendSeries = React.useMemo(function () {
        var byPeriod = {};
        weeks.forEach(function (w) {
            var bands = ageBandCounts(sumHistograms(w.flws || [], "children_by_age_month"));
            byPeriod[w.period_start] = bands;
        });
        return distinctPeriods.map(function (p) { return { period_start: p, value: byPeriod[p] || {} }; });
    }, [weeks, distinctPeriods]);

    return (
        <div className="space-y-6">
            <div>
                <div className="flex items-center gap-3 mb-2">
                    <h2 className="text-lg font-semibold">Distribution Snapshot</h2>
                    <select
                        className="border rounded px-2 py-1 text-sm"
                        value={selectedPeriod || ""}
                        onChange={function (e) { onChangePeriod(e.target.value); }}
                    >
                        {distinctPeriods.map(function (p) { return <option key={p} value={p}>{p}</option>; })}
                    </select>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <BarHistogramChart chartId="muac-hist" label="MUAC Distribution (0.5cm buckets)" buckets={muacBuckets} />
                    <BarHistogramChart chartId="age-hist" label="Children by Age Band (months)" buckets={ageBuckets} order={AGE_BANDS.map(function (b) { return b.label; })} />
                </div>
            </div>

            <div>
                <h2 className="text-lg font-semibold mb-2">Distribution Trend</h2>
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

function BarHistogramChart({ chartId, label, buckets, order }) {
    var canvasRef = React.useRef(null);
    var chartInstance = React.useRef(null);

    React.useEffect(function () {
        if (!canvasRef.current || !window.Chart) return;
        if (chartInstance.current) chartInstance.current.destroy();

        var labels = sortedBucketLabels(buckets, order);
        var values = labels.map(function (l) { return buckets[l] || 0; });

        chartInstance.current = new window.Chart(canvasRef.current, {
            type: "bar",
            data: { labels: labels, datasets: [{ label: label, data: values, backgroundColor: "#60a5fa" }] },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } },
        });
        return function () { if (chartInstance.current) chartInstance.current.destroy(); };
    }, [buckets, label, order]);

    return (
        <div className="bg-white rounded-lg border p-3">
            <div className="text-sm font-medium text-gray-700 mb-1">{label}</div>
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

        var labels = series.map(function (s) { return s.period_start; });
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
            <div className="text-sm font-medium text-gray-700 mb-1">MUAC Bucket Counts Over Time</div>
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

        var labels = series.map(function (s) { return s.period_start; });
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
            <div className="text-sm font-medium text-gray-700 mb-1">Children by Age Band Over Time</div>
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
