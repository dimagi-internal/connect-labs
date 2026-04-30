"""
SAM Follow-up Timeline Workflow Template.

Per-child dashboard for Severe Acute Malnutrition follow-up programs. The
entity stage groups follow-up visits by `child_case_id` server-side; the
render reads per-child rows directly with no JS-side `groupVisitsByChild`.

Two pipelines:
  - `children` (terminal_stage=entity): one row per child_case_id with
    demographics, latest MUAC reading and color, count of follow-ups.
  - `visits` (terminal_stage=visit_level): per-follow-up rows used for the
    timeline drill-down.
"""

DEFINITION = {
    "name": "SAM Follow-up Timeline",
    "description": "Track SAM follow-up visits per child with MUAC measurements, recovery status, and timeline drill-down",
    "version": 1,
    "templateType": "sam_followup",
    "statuses": [
        {"id": "active", "label": "Active", "color": "green"},
        {"id": "recovered", "label": "Recovered", "color": "blue"},
        {"id": "lost_to_followup", "label": "Lost to Follow-up", "color": "red"},
    ],
    "config": {"showSummaryCards": False, "showFilters": False},
    "pipeline_sources": [],
}


# Identity / demographic fields shared across both pipelines.
_COMMON_DEMOG = [
    {"name": "child_name", "paths": ["form.additional_case_info.child_name"]},
    {"name": "childs_age_in_month", "paths": ["form.additional_case_info.childs_age_in_month"]},
    {"name": "childs_gender", "paths": ["form.additional_case_info.childs_gender"]},
    {
        "name": "childs_dob",
        "paths": ["form.additional_case_info.childs_dob"],
        "transform": "date",
    },
    {"name": "household_name", "paths": ["form.additional_case_info.household_name"]},
    {"name": "household_phone", "paths": ["form.additional_case_info.household_phone"]},
    {"name": "hh_village_name", "paths": ["form.additional_case_info.hh_village_name"]},
]


def _f(name, paths, transform=None, aggregation="first"):
    f = {"name": name, "paths": paths, "aggregation": aggregation}
    if transform:
        f["transform"] = transform
    return f


# Entity-stage: one row per child_case_id.
ENTITY_FIELDS = [
    {
        "name": "child_case_id",
        "paths": ["form.case.@case_id", "form.additional_case_info.child_case_id"],
        "aggregation": "first",
    },
    *[_f(d["name"], d["paths"], d.get("transform")) for d in _COMMON_DEMOG],
    # Latest MUAC reading.
    _f(
        "latest_muac_cm",
        [
            "form.first_followup_muac.muac_display_group_1.soliciter_sam_followup_muac_cm",
            "form.next_followup.followup_muac_display_group_1.followup_soliciter_sam_followup_muac_cm",
        ],
        transform="float",
        aggregation="last",
    ),
    _f(
        "latest_muac_color",
        [
            "form.first_followup_muac.first_followup_muac_colour",
            "form.next_followup.followup_muac_display_group_1.next_followup_muac_colour",
            "form.final_muac_color",
        ],
        aggregation="last",
    ),
    _f(
        "latest_child_status",
        ["form.next_followup.followup_muac_display_group_1.followup_child_status_reported"],
        aggregation="last",
    ),
    _f("latest_recovered", ["form.child_recovered"], aggregation="last"),
    _f("flw_username", ["form.meta.username"]),
]


# Visit-stage: per-followup rows (drill-down).
VISIT_FIELDS = [
    {
        "name": "child_case_id",
        "paths": ["form.case.@case_id", "form.additional_case_info.child_case_id"],
        "aggregation": "first",
    },
    *[_f(d["name"], d["paths"], d.get("transform")) for d in _COMMON_DEMOG],
    _f("followup_number", ["form.followup_number"]),
    _f("fu_visit_date", ["form.fu_visit_date"], transform="date"),
    _f(
        "muac_cm",
        [
            "form.first_followup_muac.muac_display_group_1.soliciter_sam_followup_muac_cm",
            "form.next_followup.followup_muac_display_group_1.followup_soliciter_sam_followup_muac_cm",
        ],
        transform="float",
    ),
    _f(
        "muac_color",
        [
            "form.first_followup_muac.first_followup_muac_colour",
            "form.next_followup.followup_muac_display_group_1.next_followup_muac_colour",
            "form.final_muac_color",
        ],
    ),
    _f(
        "visited_facility",
        [
            "form.first_followup_muac.question_list_1.visited_facility",
            "form.next_followup.followup_visited_facility",
        ],
    ),
    _f(
        "treatment_received",
        [
            "form.first_followup_muac.visited_facility.treatment_received",
            "form.next_followup.followup_with_visit_facility.followup_treatment_received",
        ],
    ),
    _f("child_recovered", ["form.child_recovered"]),
    _f("flw_username", ["form.meta.username"]),
    _f("gps", ["form.location_blocks.gps_block.normalized_location"]),
]


PIPELINE_SCHEMAS = [
    {
        "alias": "children",
        "name": "SAM Children (entity stage)",
        "description": "Per-child summary — demographics, latest MUAC, recovery status",
        "schema": {
            "data_source": {"type": "connect_csv"},
            "grouping_key": "username",
            "terminal_stage": "entity",
            "linking_field": "child_case_id",
            "fields": ENTITY_FIELDS,
        },
    },
    {
        "alias": "visits",
        "name": "SAM Follow-up Visits (visit stage)",
        "description": "Per-follow-up rows for the timeline drill-down",
        "schema": {
            "data_source": {"type": "connect_csv"},
            "grouping_key": "username",
            "terminal_stage": "visit_level",
            "linking_field": "child_case_id",
            "fields": VISIT_FIELDS,
        },
    },
]


# Render code reads `pipelines.children.rows` for the dashboard and child list,
# and filters `pipelines.visits.rows` to a single child_case_id for the timeline
# drill-down. Clicking "Timeline →" opens a panel above the table and scrolls
# it into view.
RENDER_CODE = r"""function WorkflowUI({ definition, instance, workers, pipelines, links, actions, onUpdateState }) {
    var children = (pipelines && pipelines.children && pipelines.children.rows) || [];
    var visitsAll = (pipelines && pipelines.visits && pipelines.visits.rows) || [];

    var [selectedChildId, setSelectedChildId] = React.useState(null);
    var [search, setSearch] = React.useState('');
    var [colorFilter, setColorFilter] = React.useState('all');
    var timelineRef = React.useRef(null);

    var kpis = React.useMemo(function() {
        var total = children.length;
        var red = children.filter(function(c) { return c.latest_muac_color === 'red'; }).length;
        var yellow = children.filter(function(c) { return c.latest_muac_color === 'yellow'; }).length;
        var green = children.filter(function(c) { return c.latest_muac_color === 'green'; }).length;
        var recovered = children.filter(function(c) { return c.latest_recovered === 'yes'; }).length;
        return { total: total, red: red, yellow: yellow, green: green, recovered: recovered };
    }, [children]);

    var displayChildren = React.useMemo(function() {
        var rows = children;
        if (colorFilter !== 'all') {
            rows = rows.filter(function(c) { return c.latest_muac_color === colorFilter; });
        }
        if (search.trim()) {
            var q = search.toLowerCase();
            rows = rows.filter(function(c) {
                return (c.entity_id && String(c.entity_id).toLowerCase().indexOf(q) >= 0)
                    || (c.child_name && c.child_name.toLowerCase().indexOf(q) >= 0)
                    || (c.household_name && c.household_name.toLowerCase().indexOf(q) >= 0);
            });
        }
        return rows;
    }, [children, search, colorFilter]);

    var visitsForSelected = React.useMemo(function() {
        if (!selectedChildId) return [];
        return visitsAll.filter(function(v) { return v.child_case_id === selectedChildId; })
            .sort(function(a, b) {
                var da = a.fu_visit_date ? new Date(a.fu_visit_date) : new Date(0);
                var db = b.fu_visit_date ? new Date(b.fu_visit_date) : new Date(0);
                return da - db;
            });
    }, [visitsAll, selectedChildId]);

    var openTimeline = function(caseId) {
        setSelectedChildId(caseId);
        setTimeout(function() {
            if (timelineRef.current && timelineRef.current.scrollIntoView) {
                timelineRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        }, 50);
    };

    var selectedChild = selectedChildId ? children.find(function(c) { return c.entity_id === selectedChildId; }) : null;

    function colorChip(c) {
        var bg = { red: 'bg-red-100 text-red-800',
                   yellow: 'bg-yellow-100 text-yellow-800',
                   green: 'bg-green-100 text-green-800' }[c] || 'bg-gray-100 text-gray-700';
        return <span className={"px-2 py-0.5 text-xs font-medium rounded " + bg}>{c || '—'}</span>;
    }

    return (
        <div className="space-y-6">
            <div className="bg-white rounded-lg shadow-sm p-6">
                <h1 className="text-2xl font-bold text-gray-900">{definition.name}</h1>
                <p className="text-gray-600 mt-1">{definition.description}</p>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
                <Kpi label="Total" value={kpis.total} />
                <Kpi label="Red" value={kpis.red} color="red" />
                <Kpi label="Yellow" value={kpis.yellow} color="yellow" />
                <Kpi label="Green" value={kpis.green} color="green" />
                <Kpi label="Recovered" value={kpis.recovered} color="blue" />
            </div>

            {/* Timeline panel — rendered above the table when active so click feedback is visible */}
            {selectedChild && (
                <div ref={timelineRef} className="bg-white rounded-lg shadow-md p-4 border-2 border-blue-300">
                    <div className="flex justify-between items-start mb-3">
                        <div>
                            <h2 className="text-lg font-semibold">
                                Timeline: {selectedChild.child_name || selectedChildId}
                            </h2>
                            <div className="text-xs text-gray-500 mt-1">
                                {selectedChildId} · {visitsForSelected.length} follow-ups ·
                                {' '}HH: {selectedChild.household_name || '—'}
                                {' '}({selectedChild.hh_village_name || '—'})
                                {' '}· latest MUAC: {selectedChild.latest_muac_cm != null ? Number(selectedChild.latest_muac_cm).toFixed(1) + ' cm' : '—'}
                                {' '}{colorChip(selectedChild.latest_muac_color)}
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
                                <div key={v.id || (v.fu_visit_date + '-' + i)}
                                     className="border-l-4 border-blue-400 pl-3 py-2 text-sm bg-gray-50 rounded-r">
                                    <div className="flex items-center justify-between">
                                        <div className="font-medium">
                                            {v.fu_visit_date || '—'}
                                            {v.followup_number && <span className="ml-2 text-xs text-gray-500">FU #{v.followup_number}</span>}
                                        </div>
                                        {colorChip(v.muac_color)}
                                    </div>
                                    <div className="text-xs text-gray-600 mt-1">
                                        MUAC: {v.muac_cm != null ? v.muac_cm + ' cm' : '—'}
                                        {' · Visited facility: ' + (v.visited_facility || '—')}
                                        {v.treatment_received && ' · Treatment: ' + v.treatment_received}
                                        {' · Recovered: ' + (v.child_recovered || '—')}
                                    </div>
                                </div>
                            );
                        })}
                        {visitsForSelected.length === 0 && (
                            <div className="text-xs text-gray-500 px-3 py-4">No follow-up visits found for this child.</div>
                        )}
                    </div>
                </div>
            )}

            <div className="bg-white rounded-lg shadow-sm p-4 flex gap-3 items-center">
                <input
                    type="text"
                    value={search}
                    onChange={function(e) { setSearch(e.target.value); }}
                    placeholder="Search by case ID, child, household..."
                    className="flex-1 border border-gray-300 rounded px-3 py-2 text-sm"
                />
                <select
                    value={colorFilter}
                    onChange={function(e) { setColorFilter(e.target.value); }}
                    className="border border-gray-300 rounded px-3 py-2 text-sm"
                >
                    <option value="all">All MUAC colors</option>
                    <option value="red">Red</option>
                    <option value="yellow">Yellow</option>
                    <option value="green">Green</option>
                </select>
                <span className="text-xs text-gray-500">
                    {displayChildren.length} of {children.length}
                </span>
            </div>

            <div className="bg-white rounded-lg shadow-sm overflow-hidden">
                <table className="min-w-full divide-y divide-gray-200">
                    <thead className="bg-gray-50">
                        <tr>
                            <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Child</th>
                            <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Household</th>
                            <th className="px-4 py-2 text-right text-xs font-medium text-gray-500 uppercase">Visits</th>
                            <th className="px-4 py-2 text-right text-xs font-medium text-gray-500 uppercase">Latest MUAC</th>
                            <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Color</th>
                            <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Recovered</th>
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
                                    <td className="px-4 py-2 text-sm text-gray-700">{c.household_name || '—'}</td>
                                    <td className="px-4 py-2 text-sm text-right text-gray-700">{c.total_visits || 0}</td>
                                    <td className="px-4 py-2 text-sm text-right font-mono text-gray-900">
                                        {c.latest_muac_cm != null ? Number(c.latest_muac_cm).toFixed(1) : '—'}
                                    </td>
                                    <td className="px-4 py-2">{colorChip(c.latest_muac_color)}</td>
                                    <td className="px-4 py-2 text-sm text-gray-700">{c.latest_recovered || '—'}</td>
                                    <td className="px-4 py-2 text-right">
                                        <button
                                            onClick={function() { openTimeline(c.entity_id); }}
                                            className="text-xs text-blue-600 hover:text-blue-800 hover:underline px-2 py-1 rounded"
                                        >{isSelected ? 'Showing ✓' : 'Timeline →'}</button>
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

function Kpi(props) {
    var color = props.color || 'gray';
    var bg = {
        gray:   'bg-white',
        red:    'bg-red-50 border-red-200',
        yellow: 'bg-yellow-50 border-yellow-200',
        green:  'bg-green-50 border-green-200',
        blue:   'bg-blue-50 border-blue-200',
    }[color] || 'bg-white';
    return (
        <div className={"p-4 rounded-lg shadow-sm border " + bg}>
            <div className="text-3xl font-bold text-gray-900">{props.value}</div>
            <div className="text-sm text-gray-600">{props.label}</div>
        </div>
    );
}
"""


TEMPLATE = {
    "key": "sam_followup",
    "name": "SAM Follow-up Timeline",
    "description": "Track SAM follow-up visits per child with MUAC measurements, recovery status, and timeline drill-down.",
    "icon": "fa-child",
    "color": "red",
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schemas": PIPELINE_SCHEMAS,
}
