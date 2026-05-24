"""
CHC Nutrition Analysis Workflow Template.

FLW-level nutrition and health metrics dashboard. Displays per-FLW aggregated
stats with inline MUAC distribution sparklines, SAM/MAM rates, gender split,
and one-click audit creation.

Single aggregated pipeline with a MUAC histogram. SAM and MAM counts are
derived from histogram bins in render code rather than filter_path (which
doesn't support range comparisons).

Form path variants:
  - Opp 814: form.case.update.*, form.additional_case_info.*
  - Opp 822: form.subcase_0.case.update.*, form.child_registration.*
The primary paths target opp 814. When instantiating for opp 822, edit the
pipeline schema to use the alternate paths (documented in comments).
"""

PIPELINE_SCHEMA = {
    "name": "CHC Nutrition Metrics",
    "description": "Per-FLW aggregated nutrition, MUAC, and health metrics",
    "version": 1,
    "data_source": {"type": "connect_csv"},
    "grouping_key": "username",
    "terminal_stage": "aggregated",
    "fields": [
        {
            "name": "male_count",
            "path": "form.additional_case_info.childs_gender",
            # Alt paths: "form.child_registration.childs_gender",
            #            "form.subcase_0.case.update.childs_gender"
            "aggregation": "count",
            "filter_path": "form.additional_case_info.childs_gender",
            "filter_value": "male",
            "description": "Number of male children visited",
        },
        {
            "name": "female_count",
            "path": "form.additional_case_info.childs_gender",
            "aggregation": "count",
            "filter_path": "form.additional_case_info.childs_gender",
            "filter_value": "female",
            "description": "Number of female children visited",
        },
        {
            "name": "muac_measurements_count",
            "paths": [
                "form.case.update.soliciter_muac_cm",
                "form.subcase_0.case.update.soliciter_muac",
                "form.service_delivery.muac_group.soliciter_muac",
            ],
            "aggregation": "count",
            "transform": "float",
            "description": "Number of valid MUAC measurements",
        },
        {
            "name": "avg_muac_cm",
            "paths": [
                "form.case.update.soliciter_muac_cm",
                "form.subcase_0.case.update.soliciter_muac",
                "form.service_delivery.muac_group.soliciter_muac",
            ],
            "aggregation": "avg",
            "transform": "float",
            "description": "Average MUAC measurement in cm",
        },
        {
            "name": "children_unwell_count",
            "paths": [
                "form.case.update.va_child_unwell_today",
                "form.subcase_0.case.update.va_child_unwell_today",
            ],
            "aggregation": "count",
            "filter_path": "form.case.update.va_child_unwell_today",
            "filter_value": "yes",
            "description": "Number of visits where child was unwell",
        },
        {
            "name": "under_treatment_count",
            "paths": [
                "form.case.update.under_treatment_for_mal",
                "form.subcase_0.case.update.under_treatment_for_mal",
                "form.service_delivery.muac_group.muac_display_group_1.under_treatment_for_mal",
            ],
            "aggregation": "count",
            "filter_path": "form.case.update.under_treatment_for_mal",
            "filter_value": "yes",
            "description": "Number of children under malnutrition treatment",
        },
        {
            "name": "malnutrition_diagnosed_count",
            "paths": [
                "form.case.update.diagnosed_with_mal_past_3_months",
                "form.subcase_0.case.update.diagnosed_with_mal_past_3_months",
                "form.service_delivery.muac_group.muac_display_group_1.diagnosed_with_mal_past_3_months",
            ],
            "aggregation": "count",
            "filter_path": "form.case.update.diagnosed_with_mal_past_3_months",
            "filter_value": "yes",
            "description": "Children diagnosed with malnutrition in past 3 months",
        },
        {
            "name": "va_knowledge_shared_count",
            "paths": [
                "form.case.update.va_confirm_shared_knowledge",
                "form.subcase_0.case.update.va_confirm_shared_knowledge",
            ],
            "aggregation": "count",
            "description": "Times VA knowledge was shared and confirmed",
        },
        {
            "name": "ors_recovered_count",
            "paths": [
                "form.ors_group.did_the_child_recover",
                "form.service_delivery.ors_group.did_the_child_recover",
            ],
            "aggregation": "count",
            "filter_path": "form.ors_group.did_the_child_recover",
            "filter_value": "yes",
            "description": "Children who recovered with ORS",
        },
        {
            "name": "received_vaccine_count",
            "paths": [
                "form.pictures.received_any_vaccine",
                "form.service_delivery.pictures.received_any_vaccine",
            ],
            "aggregation": "count",
            "filter_path": "form.pictures.received_any_vaccine",
            "filter_value": "yes",
            "description": "Children who received any vaccine",
        },
    ],
    "histograms": [
        {
            "name": "muac_distribution",
            "paths": [
                "form.case.update.soliciter_muac_cm",
                "form.subcase_0.case.update.soliciter_muac",
                "form.service_delivery.muac_group.soliciter_muac",
            ],
            "lower_bound": 9.5,
            "upper_bound": 21.5,
            "num_bins": 12,
            "bin_name_prefix": "muac",
            "transform": "float",
            "description": "MUAC measurement distribution (9.5-21.5 cm, 1cm bins)",
        },
    ],
    "filters": {},
}

DEFINITION = {
    "name": "CHC Nutrition Analysis",
    "description": "FLW-level nutrition and health metrics with MUAC distribution, SAM/MAM rates, and gender split.",
    "version": 1,
    "templateType": "chc_nutrition_analysis",
    "statuses": [
        {"id": "pending", "label": "Pending Review", "color": "gray"},
        {"id": "reviewed", "label": "Reviewed", "color": "green"},
    ],
    "config": {
        "showSummaryCards": False,
        "showFilters": False,
    },
    "pipeline_sources": [],
}

RENDER_CODE = r"""function WorkflowUI({ definition, instance, workers, pipelines, links, actions, onUpdateState }) {
    // ── Data ────────────────────────────────────────────────────
    var rows = (pipelines && pipelines.default && pipelines.default.rows) || [];

    // ── Histogram bin names ─────────────────────────────────────
    var BINS = [
        'muac_9_5_10_5_visits',  'muac_10_5_11_5_visits',
        'muac_11_5_12_5_visits', 'muac_12_5_13_5_visits',
        'muac_13_5_14_5_visits', 'muac_14_5_15_5_visits',
        'muac_15_5_16_5_visits', 'muac_16_5_17_5_visits',
        'muac_17_5_18_5_visits', 'muac_18_5_19_5_visits',
        'muac_19_5_20_5_visits', 'muac_20_5_21_5_visits'
    ];
    // Red (SAM < 11.5), yellow (MAM 11.5-12.5), green (healthy ≥ 12.5)
    var BIN_COLORS = [
        '#ef4444', '#ef4444',
        '#facc15',
        '#22c55e', '#22c55e', '#22c55e', '#22c55e', '#22c55e',
        '#22c55e', '#22c55e', '#22c55e', '#22c55e'
    ];

    // ── Per-row derived fields ──────────────────────────────────
    function samCount(r) { return (r.muac_9_5_10_5_visits || 0) + (r.muac_10_5_11_5_visits || 0); }
    function mamCount(r) { return r.muac_11_5_12_5_visits || 0; }
    function muacCount(r) { return r.muac_distribution_count || r.muac_measurements_count || 0; }
    function genderPct(r) {
        var m = r.male_count || 0;
        var f = r.female_count || 0;
        var t = m + f;
        return t > 0 ? Math.round(f / t * 1000) / 10 : null;
    }

    // ── Summary stats ───────────────────────────────────────────
    var totalFlws = rows.length;
    var totalVisits = rows.reduce(function(s, r) { return s + (r.total_visits || 0); }, 0);
    var totalApproved = rows.reduce(function(s, r) { return s + (r.approved_visits || 0); }, 0);
    var avgVisitsPerFlw = totalFlws > 0 ? Math.round(totalVisits / totalFlws * 10) / 10 : 0;
    var totalMuac = rows.reduce(function(s, r) { return s + muacCount(r); }, 0);
    var totalSam = rows.reduce(function(s, r) { return s + samCount(r); }, 0);
    var totalMam = rows.reduce(function(s, r) { return s + mamCount(r); }, 0);
    var samRate = totalMuac > 0 ? Math.round(totalSam / totalMuac * 1000) / 10 : 0;
    var mamRate = totalMuac > 0 ? Math.round(totalMam / totalMuac * 1000) / 10 : 0;
    var totalUnwell = rows.reduce(function(s, r) { return s + (r.children_unwell_count || 0); }, 0);
    var totalTreatment = rows.reduce(function(s, r) { return s + (r.under_treatment_count || 0); }, 0);

    // ── State ───────────────────────────────────────────────────
    var _sort = React.useState('total_visits');
    var sortKey = _sort[0]; var setSortKey = _sort[1];
    var _dir = React.useState(true);
    var sortDesc = _dir[0]; var setSortDesc = _dir[1];
    var _search = React.useState('');
    var search = _search[0]; var setSearch = _search[1];

    function toggleSort(key) {
        if (sortKey === key) { setSortDesc(!sortDesc); }
        else { setSortKey(key); setSortDesc(true); }
    }

    // ── Worker name lookup ──────────────────────────────────────
    var workerMap = React.useMemo(function() {
        var m = {};
        (workers || []).forEach(function(w) { m[w.username] = w.name || w.username; });
        return m;
    }, [workers]);
    function displayName(r) { return workerMap[r.username] || r.username; }

    // ── Sort + filter ───────────────────────────────────────────
    var sortedRows = React.useMemo(function() {
        var filtered = rows;
        if (search) {
            var q = search.toLowerCase();
            filtered = rows.filter(function(r) {
                return displayName(r).toLowerCase().indexOf(q) >= 0 ||
                       r.username.toLowerCase().indexOf(q) >= 0;
            });
        }
        var sorted = filtered.slice().sort(function(a, b) {
            var va, vb;
            if (sortKey === 'name') { va = displayName(a).toLowerCase(); vb = displayName(b).toLowerCase(); }
            else if (sortKey === 'sam') { va = muacCount(a) > 0 ? samCount(a) / muacCount(a) : -1; vb = muacCount(b) > 0 ? samCount(b) / muacCount(b) : -1; }
            else if (sortKey === 'mam') { va = muacCount(a) > 0 ? mamCount(a) / muacCount(a) : -1; vb = muacCount(b) > 0 ? mamCount(b) / muacCount(b) : -1; }
            else if (sortKey === 'gender') { va = genderPct(a) !== null ? genderPct(a) : -1; vb = genderPct(b) !== null ? genderPct(b) : -1; }
            else { va = a[sortKey] || 0; vb = b[sortKey] || 0; }
            if (va < vb) return sortDesc ? 1 : -1;
            if (va > vb) return sortDesc ? -1 : 1;
            return 0;
        });
        return sorted;
    }, [rows, sortKey, sortDesc, search, workerMap]);

    // ── Sparkline component ─────────────────────────────────────
    function MuacSparkline(props) {
        var r = props.row;
        var count = muacCount(r);
        if (!count) return React.createElement('span', {className: 'text-gray-400'}, '-');
        var vals = BINS.map(function(b) { return r[b] || 0; });
        var maxBin = Math.max.apply(null, vals.concat([1]));
        return React.createElement('div', {
            className: 'inline-flex items-end gap-px overflow-hidden cursor-help',
            style: {height: '28px'},
            title: 'MUAC: ' + (r.muac_distribution_mean || 0).toFixed(1) + ' cm avg (n=' + count + ')'
        }, vals.map(function(v, i) {
            return React.createElement('span', {
                key: i,
                className: 'rounded-sm',
                style: {
                    width: '4px',
                    backgroundColor: BIN_COLORS[i],
                    height: Math.max(1, v / maxBin * 28) + 'px'
                }
            });
        }));
    }

    // ── Sort header helper ──────────────────────────────────────
    function SortTh(props) {
        var arrow = sortKey === props.skey ? (sortDesc ? ' ↓' : ' ↑') : '';
        return React.createElement('th', {
            className: 'px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer select-none hover:text-gray-700',
            onClick: function() { toggleSort(props.skey); },
            title: props.title || ''
        }, props.label + arrow);
    }

    // ── Gender badge ────────────────────────────────────────────
    function GenderBadge(props) {
        var r = props.row;
        var pct = genderPct(r);
        if (pct === null) return React.createElement('span', {className: 'text-gray-400'}, '-');
        var color = (pct >= 45 && pct <= 55) ? 'text-green-600' :
                    (pct < 40 || pct > 60) ? 'text-red-600' : 'text-yellow-600';
        return React.createElement('span', null,
            React.createElement('span', {className: color + ' font-medium'}, pct + '%'),
            React.createElement('span', {className: 'text-gray-500 text-xs ml-1'},
                '(' + (r.female_count || 0) + 'F/' + (r.male_count || 0) + 'M)')
        );
    }

    // ── Render ──────────────────────────────────────────────────
    return React.createElement('div', {className: 'space-y-6'},

        // Title
        React.createElement('div', null,
            React.createElement('h1', {className: 'text-2xl font-bold text-gray-900'}, definition.name),
            React.createElement('p', {className: 'text-gray-600 mt-1'}, definition.description)
        ),

        // Summary cards
        React.createElement('div', {className: 'bg-white border border-gray-200 rounded-lg p-6 shadow-sm'},
            React.createElement('h2', {className: 'text-lg font-semibold text-gray-900 mb-4'}, 'Summary'),
            React.createElement('div', {className: 'grid grid-cols-2 md:grid-cols-4 gap-4'},
                // Row 1: general
                React.createElement('div', {className: 'border-l-4 border-blue-500 pl-3'},
                    React.createElement('div', {className: 'text-sm text-gray-600'}, 'Total FLWs'),
                    React.createElement('div', {className: 'text-2xl font-bold text-gray-900'}, totalFlws)
                ),
                React.createElement('div', {className: 'border-l-4 border-blue-500 pl-3'},
                    React.createElement('div', {className: 'text-sm text-gray-600'}, 'Total Visits'),
                    React.createElement('div', {className: 'text-2xl font-bold text-gray-900'}, totalVisits.toLocaleString())
                ),
                React.createElement('div', {className: 'border-l-4 border-blue-500 pl-3'},
                    React.createElement('div', {className: 'text-sm text-gray-600'}, 'Avg Visits/FLW'),
                    React.createElement('div', {className: 'text-2xl font-bold text-gray-900'}, avgVisitsPerFlw)
                ),
                React.createElement('div', {className: 'border-l-4 border-blue-500 pl-3'},
                    React.createElement('div', {className: 'text-sm text-gray-600'}, 'Total Approved'),
                    React.createElement('div', {className: 'text-2xl font-bold text-gray-900'}, totalApproved.toLocaleString())
                ),
                // Row 2: nutrition
                React.createElement('div', {className: 'border-l-4 border-red-600 pl-3', title: 'Severe Acute Malnutrition (MUAC < 11.5 cm)'},
                    React.createElement('div', {className: 'text-sm text-gray-600'}, 'SAM Rate'),
                    React.createElement('div', {className: 'text-2xl font-bold text-red-600'},
                        samRate + '%',
                        React.createElement('span', {className: 'text-base text-gray-500 ml-1'}, '(' + totalSam + ')')
                    )
                ),
                React.createElement('div', {className: 'border-l-4 border-yellow-500 pl-3', title: 'Moderate Acute Malnutrition (MUAC 11.5-12.5 cm)'},
                    React.createElement('div', {className: 'text-sm text-gray-600'}, 'MAM Rate'),
                    React.createElement('div', {className: 'text-2xl font-bold text-yellow-600'},
                        mamRate + '%',
                        React.createElement('span', {className: 'text-base text-gray-500 ml-1'}, '(' + totalMam + ')')
                    )
                ),
                React.createElement('div', {className: 'border-l-4 border-orange-500 pl-3'},
                    React.createElement('div', {className: 'text-sm text-gray-600'}, 'Children Unwell'),
                    React.createElement('div', {className: 'text-2xl font-bold text-gray-900'}, totalUnwell.toLocaleString())
                ),
                React.createElement('div', {className: 'border-l-4 border-purple-500 pl-3'},
                    React.createElement('div', {className: 'text-sm text-gray-600'}, 'Under Treatment'),
                    React.createElement('div', {className: 'text-2xl font-bold text-gray-900'}, totalTreatment.toLocaleString())
                )
            )
        ),

        // Search
        React.createElement('div', {className: 'flex items-center gap-4'},
            React.createElement('input', {
                type: 'text',
                placeholder: 'Search FLWs...',
                value: search,
                onChange: function(e) { setSearch(e.target.value); },
                className: 'px-3 py-2 border border-gray-300 rounded-md text-sm w-64 focus:outline-none focus:ring-2 focus:ring-blue-500'
            }),
            React.createElement('span', {className: 'text-sm text-gray-500'},
                sortedRows.length + ' of ' + rows.length + ' FLWs')
        ),

        // FLW table
        React.createElement('div', {className: 'bg-white border border-gray-200 rounded-lg shadow-sm overflow-hidden'},
            React.createElement('div', {className: 'px-6 py-4 border-b border-gray-200 bg-gray-50'},
                React.createElement('h2', {className: 'text-lg font-semibold text-gray-900'},
                    'FLW-Level Analysis',
                    React.createElement('span', {className: 'text-sm text-gray-600 font-normal ml-2'},
                        '(' + rows.length + ' FLWs)')
                )
            ),
            React.createElement('div', {className: 'overflow-x-auto'},
                React.createElement('table', {className: 'min-w-full divide-y divide-gray-200'},
                    React.createElement('thead', {className: 'bg-gray-50'},
                        React.createElement('tr', null,
                            React.createElement(SortTh, {skey: 'name', label: 'FLW Name'}),
                            React.createElement(SortTh, {skey: 'total_visits', label: 'Total Visits'}),
                            React.createElement(SortTh, {skey: 'approved_visits', label: 'Approved'}),
                            React.createElement(SortTh, {skey: 'days_active', label: 'Days Active'}),
                            React.createElement(SortTh, {skey: 'muac_measurements_count', label: 'MUAC Count'}),
                            React.createElement(SortTh, {skey: 'avg_muac_cm', label: 'Avg MUAC (cm)'}),
                            React.createElement(SortTh, {skey: 'sam', label: 'SAM', title: 'Severe Acute Malnutrition (MUAC < 11.5 cm)'}),
                            React.createElement(SortTh, {skey: 'mam', label: 'MAM', title: 'Moderate Acute Malnutrition (MUAC 11.5-12.5 cm)'}),
                            React.createElement('th', {className: 'px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider'}, 'MUAC Distribution'),
                            React.createElement(SortTh, {skey: 'gender', label: 'Gender Split', title: 'Female % of (Male + Female)'}),
                            React.createElement('th', {className: 'px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider'}, 'Actions')
                        )
                    ),
                    React.createElement('tbody', {className: 'bg-white divide-y divide-gray-200'},
                        sortedRows.length === 0
                            ? React.createElement('tr', null,
                                React.createElement('td', {colSpan: 11, className: 'px-4 py-8 text-center text-sm text-gray-500'}, 'No data available'))
                            : sortedRows.map(function(r) {
                                var mc = muacCount(r);
                                var sc = samCount(r);
                                var mmc = mamCount(r);
                                var samPct = mc > 0 ? Math.round(sc / mc * 100) : null;
                                var mamPct = mc > 0 ? Math.round(mmc / mc * 100) : null;
                                var name = displayName(r);

                                return React.createElement('tr', {key: r.username, className: 'hover:bg-gray-50'},
                                    // FLW Name
                                    React.createElement('td', {className: 'px-4 py-3 text-sm'},
                                        React.createElement('div', {className: 'font-medium text-gray-900'}, name),
                                        name !== r.username
                                            ? React.createElement('div', {className: 'text-xs text-gray-500'}, r.username)
                                            : null
                                    ),
                                    // Total Visits
                                    React.createElement('td', {className: 'px-4 py-3 whitespace-nowrap text-sm text-gray-900'}, r.total_visits || 0),
                                    // Approved
                                    React.createElement('td', {className: 'px-4 py-3 whitespace-nowrap text-sm text-gray-900'},
                                        r.total_visits > 0
                                            ? (Math.round((r.approved_visits || 0) / r.total_visits * 1000) / 10) + '% (' + (r.approved_visits || 0) + ')'
                                            : '-'
                                    ),
                                    // Days Active
                                    React.createElement('td', {className: 'px-4 py-3 whitespace-nowrap text-sm text-gray-900'}, r.days_active || 0),
                                    // MUAC Count
                                    React.createElement('td', {className: 'px-4 py-3 whitespace-nowrap text-sm text-gray-900'}, mc),
                                    // Avg MUAC
                                    React.createElement('td', {className: 'px-4 py-3 whitespace-nowrap text-sm text-gray-900'},
                                        r.avg_muac_cm ? r.avg_muac_cm.toFixed(1) : '-'),
                                    // SAM
                                    React.createElement('td', {className: 'px-4 py-3 whitespace-nowrap text-sm'},
                                        samPct !== null
                                            ? React.createElement('span', null,
                                                React.createElement('span', {className: 'text-red-600 font-medium'}, samPct + '%'),
                                                React.createElement('span', {className: 'text-gray-500 ml-1'}, '(' + sc + ')'))
                                            : React.createElement('span', {className: 'text-gray-400'}, '-')
                                    ),
                                    // MAM
                                    React.createElement('td', {className: 'px-4 py-3 whitespace-nowrap text-sm'},
                                        mamPct !== null
                                            ? React.createElement('span', null,
                                                React.createElement('span', {className: 'text-yellow-600 font-medium'}, mamPct + '%'),
                                                React.createElement('span', {className: 'text-gray-500 ml-1'}, '(' + mmc + ')'))
                                            : React.createElement('span', {className: 'text-gray-400'}, '-')
                                    ),
                                    // MUAC Distribution sparkline
                                    React.createElement('td', {className: 'px-4 py-3 whitespace-nowrap text-sm'},
                                        React.createElement(MuacSparkline, {row: r})),
                                    // Gender Split
                                    React.createElement('td', {className: 'px-4 py-3 whitespace-nowrap text-sm'},
                                        React.createElement(GenderBadge, {row: r})),
                                    // Actions
                                    React.createElement('td', {className: 'px-4 py-3 whitespace-nowrap text-sm'},
                                        React.createElement('a', {
                                            href: links.auditUrl({username: r.username, count: 5}),
                                            className: 'inline-flex items-center px-3 py-1 border border-blue-300 rounded-md text-xs font-medium text-blue-700 bg-blue-50 hover:bg-blue-100 transition-colors',
                                            title: 'Create audit for ' + name
                                        }, '📋 Audit')
                                    )
                                );
                            })
                    )
                )
            )
        )
    );
}"""

TEMPLATE = {
    "key": "chc_nutrition_analysis",
    "name": "CHC Nutrition Analysis",
    "description": "FLW-level nutrition and health metrics with MUAC distribution, SAM/MAM rates, and gender split.",
    "icon": "fa-heartbeat",
    "color": "red",
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schema": PIPELINE_SCHEMA,
}
