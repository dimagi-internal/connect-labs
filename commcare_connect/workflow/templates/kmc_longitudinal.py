"""
KMC Longitudinal Tracking Workflow Template.

Per-beneficiary dashboard for Kangaroo Mother Care programs. Tracks children
across visits with KPI cards, a filterable child list, and a per-child timeline
drill-down (visits, weights, KMC status changes).

Aggregation runs in SQL via the entity-stage pipeline (one row per
beneficiary_case_id). The render code reads those rows directly — no JS-side
`groupVisitsByChild` shaping. The visit-level pipeline is kept alongside as a
second source for the timeline drill-down.
"""

DEFINITION = {
    "name": "KMC Longitudinal Tracking",
    "description": "Track KMC children across visits with outcomes dashboard, child list, and timeline",
    "version": 1,
    "templateType": "kmc_longitudinal",
    "statuses": [
        {"id": "active", "label": "Active", "color": "green"},
        {"id": "discharged", "label": "Discharged", "color": "blue"},
        {"id": "lost_to_followup", "label": "Lost to Follow-up", "color": "red"},
    ],
    "config": {
        "showSummaryCards": False,
        "showFilters": False,
    },
    "pipeline_sources": [],
}


# Identity / demographic field paths shared across both pipelines.
_DEMOGRAPHIC_FIELDS = [
    {
        "name": "child_name",
        "paths": [
            "form.grp_kmc_beneficiary.child_name",
            "form.grp_beneficiary_details.child_name",
            "form.svn_name",
            "form.mothers_details.child_name",
        ],
    },
    {
        "name": "mother_name",
        "paths": [
            "form.grp_beneficiary_details.mother_name",
            "form.mother_name",
            "form.kmc_beneficiary_name",
        ],
    },
    {
        "name": "mother_phone",
        "paths": [
            "form.grp_kmc_beneficiary.mothers_phone_number",
            "form.grp_beneficiary_details.mothers_phone_number",
            "form.deduplication_block.mothers_phone_number",
            "form.mothers_phone_number",
        ],
    },
    {
        "name": "child_dob",
        "paths": ["form.mothers_details.child_DOB", "form.child_DOB"],
        "transform": "date",
    },
    {
        "name": "child_gender",
        "paths": ["form.child_details.child_gender"],
    },
    {
        "name": "village",
        "paths": [
            "form.grp_kmc_beneficiary.village",
            "form.address_change_grp.location.village",
            "form.village",
        ],
    },
    {
        "name": "subcounty",
        "paths": ["form.sub_country", "form.subcounty"],
    },
    {
        "name": "reg_date",
        "paths": ["form.grp_kmc_beneficiary.reg_date", "form.reg_date"],
        "transform": "date",
    },
    {
        "name": "birth_weight",
        "paths": [
            "form.child_details.birth_weight_group.child_weight_birth",
            "form.child_weight_birth",
        ],
        "transform": "kg_to_g",
    },
]


def _entity_field(name: str, paths: list[str], transform: str | None = None, aggregation: str = "first") -> dict:
    f = {"name": name, "paths": paths, "aggregation": aggregation}
    if transform:
        f["transform"] = transform
    return f


def _visit_field(name: str, paths: list[str], transform: str | None = None) -> dict:
    f = {"name": name, "paths": paths, "aggregation": "first"}
    if transform:
        f["transform"] = transform
    return f


# Entity-stage pipeline: one row per beneficiary_case_id.
# - Demographics use `first` (registration-visit value).
# - Current weight, kmc_status use `last` (most recent visit).
ENTITY_FIELDS = [
    {
        "name": "beneficiary_case_id",
        "paths": ["form.case.@case_id", "form.kmc_beneficiary_case_id"],
        "aggregation": "first",
    },
    *[_entity_field(f["name"], f["paths"], f.get("transform")) for f in _DEMOGRAPHIC_FIELDS],
    # Latest weight (g) — `last` instead of `first`.
    _entity_field(
        "current_weight",
        [
            "form.anthropometric.child_weight_visit",
            "form.child_details.birth_weight_reg.child_weight_reg",
        ],
        transform="kg_to_g",
        aggregation="last",
    ),
    # Latest KMC status — `last`.
    _entity_field(
        "kmc_status",
        ["form.grp_kmc_beneficiary.kmc_status", "form.kmc_status"],
        aggregation="last",
    ),
    # Representative FLW (also denormalized as `username` on the entity row, but expose
    # under flw_username for parity with caller expectations).
    _entity_field("flw_username", ["form.meta.username"]),
]


# Visit-stage pipeline — used for the per-child timeline drill-down.
VISIT_FIELDS = [
    {
        "name": "beneficiary_case_id",
        "paths": ["form.case.@case_id", "form.kmc_beneficiary_case_id"],
        "aggregation": "first",
    },
    *[_visit_field(f["name"], f["paths"], f.get("transform")) for f in _DEMOGRAPHIC_FIELDS],
    _visit_field(
        "weight",
        [
            "form.anthropometric.child_weight_visit",
            "form.child_details.birth_weight_reg.child_weight_reg",
        ],
        transform="kg_to_g",
    ),
    _visit_field("height", ["form.anthropometric.child_height"], transform="float"),
    # Form-reported visit date — namespaced to avoid shadowing the base `visit_date`
    # column on labs_raw_visit_cache (which is what the entity-stage query, the cache,
    # and the FE's `v.visit_date` already read). The form-reported value is exposed
    # as `form_visit_date` so render code can choose explicitly.
    _visit_field("form_visit_date", ["form.grp_kmc_visit.visit_date", "form.reg_date"], transform="date"),
    _visit_field("visit_number", ["form.grp_kmc_visit.visit_number"]),
    _visit_field("visit_type", ["form.grp_kmc_visit.visit_type"]),
    _visit_field("kmc_status", ["form.grp_kmc_beneficiary.kmc_status", "form.kmc_status"]),
    _visit_field("kmc_hours", ["form.kmc_24-hour_recall.kmc_hours"]),
    _visit_field("temperature", ["form.danger_signs_checklist.svn_temperature"], transform="float"),
    _visit_field("danger_signs", ["form.danger_signs_checklist.danger_sign_list"]),
    _visit_field("gps", ["form.visit_gps_manual", "form.reg_gps", "metadata.location"]),
    _visit_field("flw_username", ["form.meta.username"]),
]


PIPELINE_SCHEMAS = [
    {
        "alias": "children",
        "name": "KMC Children (entity stage)",
        "description": "Per-beneficiary summary — demographics, current weight, KMC status",
        "schema": {
            "data_source": {"type": "connect_csv"},
            "grouping_key": "username",
            "terminal_stage": "entity",
            "linking_field": "beneficiary_case_id",
            "fields": ENTITY_FIELDS,
        },
    },
    {
        "alias": "visits",
        "name": "KMC Visit Data (visit stage)",
        "description": "Per-visit data used for the timeline drill-down view",
        "schema": {
            "data_source": {"type": "connect_csv"},
            "grouping_key": "username",
            "terminal_stage": "visit_level",
            "linking_field": "beneficiary_case_id",
            "fields": VISIT_FIELDS,
        },
    },
]


# Render code reads `pipelines.children.rows` for the dashboard and child list,
# and filters `pipelines.visits.rows` to a single beneficiary_case_id for the
# timeline drill-down. Clicking "Timeline →" opens a panel above the table and
# scrolls it into view.
RENDER_CODE = r"""function WorkflowUI({ definition, instance, workers, pipelines, links, actions, onUpdateState }) {
    var children = (pipelines && pipelines.children && pipelines.children.rows) || [];
    var visitsAll = (pipelines && pipelines.visits && pipelines.visits.rows) || [];

    var [selectedChildId, setSelectedChildId] = React.useState(null);
    var [search, setSearch] = React.useState('');
    var timelineRef = React.useRef(null);

    var enriched = React.useMemo(function() {
        return children.map(function(c) {
            var bw = c.birth_weight != null ? parseFloat(c.birth_weight) : null;
            var cw = c.current_weight != null ? parseFloat(c.current_weight) : null;
            var weightGain = (cw != null && bw != null && !isNaN(cw) && !isNaN(bw)) ? cw - bw : null;
            var reachedThreshold = cw != null && !isNaN(cw) && cw >= 2500;

            var lastDate = c.last_visit_date ? new Date(c.last_visit_date) : null;
            var daysSinceLast = lastDate && !isNaN(lastDate.getTime())
                ? Math.floor((Date.now() - lastDate.getTime()) / 86400000) : null;
            var isOverdue = daysSinceLast != null && daysSinceLast > 14;

            var regDate = c.reg_date ? new Date(c.reg_date) : null;
            var avgWeightGainPerWeek = null;
            if (weightGain != null && regDate && !isNaN(regDate.getTime())) {
                var weeks = (Date.now() - regDate.getTime()) / (7 * 86400000);
                if (weeks > 0) avgWeightGainPerWeek = weightGain / weeks;
            }

            return Object.assign({}, c, {
                weightGain: weightGain,
                reachedThreshold: reachedThreshold,
                daysSinceLastVisit: daysSinceLast,
                isOverdue: isOverdue,
                avgWeightGainPerWeek: avgWeightGainPerWeek,
            });
        });
    }, [children]);

    var kpis = React.useMemo(function() {
        var total = enriched.length;
        var active = enriched.filter(function(c) { return !c.isOverdue && c.kmc_status !== 'discharged'; }).length;
        var overdue = enriched.filter(function(c) { return c.isOverdue; }).length;
        var belowAvgGain = enriched.filter(function(c) {
            return c.avgWeightGainPerWeek != null && c.avgWeightGainPerWeek < 100;
        }).length;
        var reachedThreshold = enriched.filter(function(c) { return c.reachedThreshold; }).length;
        return { total: total, active: active, overdue: overdue,
                 belowAvgGain: belowAvgGain, reachedThreshold: reachedThreshold };
    }, [enriched]);

    var displayChildren = React.useMemo(function() {
        if (!search.trim()) return enriched;
        var q = search.toLowerCase();
        return enriched.filter(function(c) {
            return (c.entity_id && String(c.entity_id).toLowerCase().indexOf(q) >= 0)
                || (c.child_name && c.child_name.toLowerCase().indexOf(q) >= 0)
                || (c.mother_name && c.mother_name.toLowerCase().indexOf(q) >= 0);
        });
    }, [enriched, search]);

    var visitsForSelected = React.useMemo(function() {
        if (!selectedChildId) return [];
        return visitsAll.filter(function(v) { return v.beneficiary_case_id === selectedChildId; })
            .sort(function(a, b) {
                var da = a.visit_date ? new Date(a.visit_date) : new Date(0);
                var db = b.visit_date ? new Date(b.visit_date) : new Date(0);
                return da - db;
            });
    }, [visitsAll, selectedChildId]);

    var openTimeline = function(caseId) {
        setSelectedChildId(caseId);
        // Smooth-scroll to the timeline panel after the next paint.
        setTimeout(function() {
            if (timelineRef.current && timelineRef.current.scrollIntoView) {
                timelineRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        }, 50);
    };

    var selectedChild = selectedChildId ? enriched.find(function(c) { return c.entity_id === selectedChildId; }) : null;

    return (
        <div className="space-y-6">
            <div className="bg-white rounded-lg shadow-sm p-6">
                <h1 className="text-2xl font-bold text-gray-900">{definition.name}</h1>
                <p className="text-gray-600 mt-1">{definition.description}</p>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
                <KpiCard label="Total" value={kpis.total} />
                <KpiCard label="Active" value={kpis.active} color="green" />
                <KpiCard label="Overdue (>14d)" value={kpis.overdue} color="amber" />
                <KpiCard label="Below avg gain" value={kpis.belowAvgGain} color="orange" />
                <KpiCard label="≥2500g" value={kpis.reachedThreshold} color="emerald" />
            </div>

            {/* Timeline panel — rendered above the table when active so it's immediately visible after click */}
            {selectedChild && (
                <div ref={timelineRef} className="bg-white rounded-lg shadow-md p-4 border-2 border-blue-300">
                    <div className="flex justify-between items-start mb-3">
                        <div>
                            <h2 className="text-lg font-semibold">
                                Timeline: {selectedChild.child_name || selectedChildId}
                            </h2>
                            <div className="text-xs text-gray-500 mt-1">
                                {selectedChildId} · {visitsForSelected.length} visits ·
                                {' '}reg {selectedChild.reg_date || '—'} · birth wt {selectedChild.birth_weight != null ? selectedChild.birth_weight + 'g' : '—'}
                                {' '}· current wt {selectedChild.current_weight != null ? selectedChild.current_weight + 'g' : '—'}
                            </div>
                        </div>
                        <button
                            onClick={function() { setSelectedChildId(null); }}
                            className="text-sm text-gray-500 hover:text-gray-700 px-3 py-1 border border-gray-300 rounded"
                        >Close ✕</button>
                    </div>
                    <div className="space-y-2 max-h-96 overflow-y-auto">
                        {visitsForSelected.map(function(v, i) {
                            return (
                                <div key={v.id || (v.visit_date + '-' + i)}
                                     className="border-l-4 border-blue-400 pl-3 py-2 text-sm bg-gray-50 rounded-r">
                                    <div className="flex items-center justify-between">
                                        <div className="font-medium">
                                            {v.visit_date || '—'}
                                            {v.visit_number && <span className="ml-2 text-xs text-gray-500">#{v.visit_number}</span>}
                                            {v.visit_type && <span className="ml-2 text-xs text-gray-500">({v.visit_type})</span>}
                                        </div>
                                        <div className="text-xs text-gray-600">FLW: {v.flw_username || '—'}</div>
                                    </div>
                                    <div className="text-xs text-gray-600 mt-1">
                                        Weight: {v.weight != null ? v.weight + 'g' : '—'}
                                        {v.height && ' · Height: ' + v.height + 'cm'}
                                        {v.temperature && ' · Temp: ' + v.temperature + '°C'}
                                        {v.kmc_hours && ' · KMC hrs: ' + v.kmc_hours}
                                        {v.kmc_status && ' · Status: ' + v.kmc_status}
                                    </div>
                                    {v.danger_signs && (
                                        <div className="text-xs text-red-700 mt-1">⚠ Danger signs: {v.danger_signs}</div>
                                    )}
                                </div>
                            );
                        })}
                        {visitsForSelected.length === 0 && (
                            <div className="text-xs text-gray-500 px-3 py-4">No visits found for this child.</div>
                        )}
                    </div>
                </div>
            )}

            <div className="bg-white rounded-lg shadow-sm p-4">
                <input
                    type="text"
                    value={search}
                    onChange={function(e) { setSearch(e.target.value); }}
                    placeholder="Search by case ID, child name, mother name..."
                    className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
                />
                <div className="mt-2 text-xs text-gray-500">
                    Showing {displayChildren.length} of {enriched.length} children
                </div>
            </div>

            <div className="bg-white rounded-lg shadow-sm overflow-hidden">
                <table className="min-w-full divide-y divide-gray-200">
                    <thead className="bg-gray-50">
                        <tr>
                            <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Child</th>
                            <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Mother</th>
                            <th className="px-4 py-2 text-right text-xs font-medium text-gray-500 uppercase">Visits</th>
                            <th className="px-4 py-2 text-right text-xs font-medium text-gray-500 uppercase">Weight (g)</th>
                            <th className="px-4 py-2 text-right text-xs font-medium text-gray-500 uppercase">Gain (g)</th>
                            <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Last Visit</th>
                            <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">KMC Status</th>
                            <th className="px-4 py-2"></th>
                        </tr>
                    </thead>
                    <tbody className="bg-white divide-y divide-gray-200">
                        {displayChildren.map(function(c) {
                            var isSelected = c.entity_id === selectedChildId;
                            return (
                                <tr key={c.entity_id} className={isSelected ? "bg-blue-50" : "hover:bg-gray-50"}>
                                    <td className="px-4 py-2 text-sm">
                                        <div className="font-medium text-gray-900">{c.child_name || '—'}</div>
                                        <div className="text-xs text-gray-500">{c.entity_id}</div>
                                    </td>
                                    <td className="px-4 py-2 text-sm text-gray-700">{c.mother_name || '—'}</td>
                                    <td className="px-4 py-2 text-sm text-right text-gray-700">{c.total_visits || 0}</td>
                                    <td className="px-4 py-2 text-sm text-right text-gray-700">
                                        {c.current_weight != null ? c.current_weight : '—'}
                                    </td>
                                    <td className={"px-4 py-2 text-sm text-right "
                                        + (c.weightGain != null && c.weightGain >= 0 ? "text-green-700" : "text-amber-700")}>
                                        {c.weightGain != null ? c.weightGain : '—'}
                                    </td>
                                    <td className="px-4 py-2 text-sm text-gray-700">
                                        {c.last_visit_date || '—'}
                                        {c.isOverdue && <span className="ml-1 text-xs text-red-600">overdue</span>}
                                    </td>
                                    <td className="px-4 py-2 text-sm text-gray-700">{c.kmc_status || '—'}</td>
                                    <td className="px-4 py-2 text-right">
                                        <button
                                            onClick={function() { openTimeline(c.entity_id); }}
                                            className="text-xs text-blue-600 hover:text-blue-800 hover:underline px-2 py-1 rounded"
                                        >
                                            {isSelected ? 'Showing ✓' : 'Timeline →'}
                                        </button>
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
                {displayChildren.length === 0 && (
                    <div className="px-4 py-12 text-center text-sm text-gray-500">No children match.</div>
                )}
            </div>
        </div>
    );
}

function KpiCard(props) {
    var color = props.color || 'gray';
    var bg = {
        gray:    'bg-white',
        green:   'bg-green-50 border-green-200',
        amber:   'bg-amber-50 border-amber-200',
        orange:  'bg-orange-50 border-orange-200',
        emerald: 'bg-emerald-50 border-emerald-200',
    }[color] || 'bg-white';
    return (
        <div className={"p-4 rounded-lg shadow-sm border " + bg}>
            <div className="text-3xl font-bold text-gray-900">{props.value}</div>
            <div className="text-sm text-gray-600">{props.label}</div>
        </div>
    );
}
"""


def build_snapshot(pipelines: dict, state: dict, opportunity_id: int) -> dict:
    """Freeze the per-child dashboard shape into a snapshot blob.

    The render code's first move when `instance.snapshot` is present is to read
    `snapshot.children` instead of the live `pipelines.children.rows`. That gives
    historical fidelity (the dashboard reflects what was true at freeze time)
    without storing raw visit rows. Visit-level drill-down still hits live
    pipeline data because the snapshot intentionally captures only the
    aggregated per-child shape.
    """
    children = (pipelines or {}).get("children", {}).get("rows", []) or []

    # Light derivations the render code repeats (weight gain, overdue) — pre-computed
    # so the snapshot is fully self-describing. Render code can re-derive too;
    # keeping these here means a stable read path even if render code changes.
    enriched = []
    for c in children:
        bw = float(c["birth_weight"]) if c.get("birth_weight") not in (None, "") else None
        cw = float(c["current_weight"]) if c.get("current_weight") not in (None, "") else None
        weight_gain = (cw - bw) if (cw is not None and bw is not None) else None
        reached = cw is not None and cw >= 2500
        enriched.append({**c, "weight_gain": weight_gain, "reached_threshold": reached})

    return {
        "schema_version": 1,
        "children": enriched,
        "kpis": {
            "total": len(enriched),
            "reached_threshold": sum(1 for c in enriched if c.get("reached_threshold")),
        },
    }


TEMPLATE = {
    "key": "kmc_longitudinal",
    "name": "KMC Longitudinal Tracking",
    "description": "Track KMC children with per-beneficiary dashboard, child list, and timeline drill-down.",
    "icon": "fa-baby",
    "color": "teal",
    "supports_snapshots": True,
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schemas": PIPELINE_SCHEMAS,
}
