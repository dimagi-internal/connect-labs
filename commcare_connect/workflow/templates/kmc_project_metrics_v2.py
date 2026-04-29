"""
KMC Project Metrics — v2 (entity stage).

Project-level M&E dashboard. Shares the entity-stage `children` pipeline with
kmc_longitudinal_v2 (per-beneficiary) and visit-stage `visits` pipeline (per-visit).

Project KPIs are computed render-side over the entity rows + visit rows because
they aggregate across children, not within a child — the entity stage shaves off
the per-child JS shaping but not cross-child reductions.

Runs alongside kmc_project_metrics (v1).
"""

# Reuse the v2 pipeline schemas from kmc_longitudinal_v2. The registry treats
# pipelines by (workflow, alias) so re-declaring identical schemas under the same
# aliases is fine; both templates produce structurally identical pipeline data
# and the FE consumes them the same way.
from commcare_connect.workflow.templates.kmc_longitudinal_v2 import PIPELINE_SCHEMAS as KMC_V2_PIPELINE_SCHEMAS

DEFINITION = {
    "name": "KMC Project Metrics (v2)",
    "description": "Program-level M&E dashboard — entity-stage pipeline replaces JS-side grouping",
    "version": 1,
    "templateType": "kmc_project_metrics_v2",
    "statuses": [],
    "config": {"showSummaryCards": False, "showFilters": False},
    "pipeline_sources": [],
}

PIPELINE_SCHEMAS = KMC_V2_PIPELINE_SCHEMAS


# Render code computes project-wide KPIs from entity rows + visit rows. The
# per-child shaping (groupVisitsByChild) is gone; what remains is cross-child
# aggregation, which the entity stage doesn't do for us.
RENDER_CODE = r"""function WorkflowUI({ definition, instance, workers, pipelines, links, actions, onUpdateState }) {
    var children = (pipelines && pipelines.children && pipelines.children.rows) || [];
    var visits = (pipelines && pipelines.visits && pipelines.visits.rows) || [];

    // Cross-child KPIs.
    var metrics = React.useMemo(function() {
        var totalEnrolled = children.length;

        // Avg visits per child.
        var totalVisits = children.reduce(function(s, c) { return s + (c.total_visits || 0); }, 0);
        var avgVisitsPerChild = totalEnrolled > 0 ? totalVisits / totalEnrolled : null;

        // Reached-threshold rate.
        var reached = children.filter(function(c) {
            return c.current_weight != null && parseFloat(c.current_weight) >= 2500;
        }).length;
        var reachedRate = totalEnrolled > 0 ? reached / totalEnrolled : null;

        // Discharged / active / overdue.
        var discharged = children.filter(function(c) { return c.kmc_status === 'discharged'; }).length;
        var lostToFollowup = children.filter(function(c) {
            if (c.kmc_status === 'discharged') return false;
            if (!c.last_visit_date) return false;
            var d = new Date(c.last_visit_date);
            return !isNaN(d.getTime()) && (Date.now() - d.getTime()) > 14 * 86400000;
        }).length;

        // Visit-derived metrics — danger sign assessment rate.
        var visitsWithDangerCheck = visits.filter(function(v) {
            return v.danger_signs != null && String(v.danger_signs).trim() !== '';
        }).length;
        var dangerAssessRate = visits.length > 0 ? visitsWithDangerCheck / visits.length : null;

        // Avg KMC hours.
        var hoursVisits = visits.filter(function(v) { return v.kmc_hours != null && v.kmc_hours !== ''; });
        var avgKmcHours = null;
        if (hoursVisits.length > 0) {
            var sum = hoursVisits.reduce(function(s, v) { return s + (parseFloat(v.kmc_hours) || 0); }, 0);
            avgKmcHours = sum / hoursVisits.length;
        }

        return {
            totalEnrolled: totalEnrolled,
            totalVisits: totalVisits,
            avgVisitsPerChild: avgVisitsPerChild,
            reachedRate: reachedRate,
            discharged: discharged,
            lostToFollowup: lostToFollowup,
            dangerAssessRate: dangerAssessRate,
            avgKmcHours: avgKmcHours,
        };
    }, [children, visits]);

    function fmt(v, kind) {
        if (v == null) return '—';
        if (kind === 'pct') return (v * 100).toFixed(1) + '%';
        if (kind === 'decimal1') return Number(v).toFixed(1);
        return Number(v).toLocaleString();
    }

    var rows = [
        { level: 'Output', name: 'SVNs Enrolled',           value: metrics.totalEnrolled,    fmt: 'int' },
        { level: 'Output', name: 'Avg Visits per Child',    value: metrics.avgVisitsPerChild, fmt: 'decimal1' },
        { level: 'Outcome', name: 'Avg KMC Hours',          value: metrics.avgKmcHours,      fmt: 'decimal1' },
        { level: 'Outcome', name: 'Reached ≥2500g rate',    value: metrics.reachedRate,      fmt: 'pct' },
        { level: 'Output', name: 'Danger Signs Assessed',   value: metrics.dangerAssessRate, fmt: 'pct' },
        { level: 'Output', name: 'Discharged',              value: metrics.discharged,       fmt: 'int' },
        { level: 'Output', name: 'Lost to Follow-up (>14d)',value: metrics.lostToFollowup,   fmt: 'int' },
    ];

    return (
        <div className="space-y-6">
            <div className="bg-white rounded-lg shadow-sm p-6">
                <h1 className="text-2xl font-bold text-gray-900">{definition.name}</h1>
                <p className="text-gray-600 mt-1">{definition.description}</p>
                <div className="mt-2 inline-block px-2 py-1 text-xs font-medium bg-blue-100 text-blue-800 rounded">
                    Entity-stage pipeline (v2)
                </div>
                <div className="mt-2 text-xs text-gray-500">
                    {children.length} children · {visits.length} visits
                </div>
            </div>

            <div className="bg-white rounded-lg shadow-sm overflow-hidden">
                <table className="min-w-full divide-y divide-gray-200">
                    <thead className="bg-gray-50">
                        <tr>
                            <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Level</th>
                            <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Metric</th>
                            <th className="px-4 py-2 text-right text-xs font-medium text-gray-500 uppercase">Value</th>
                        </tr>
                    </thead>
                    <tbody className="bg-white divide-y divide-gray-200">
                        {rows.map(function(r, i) {
                            return (
                                <tr key={i} className="hover:bg-gray-50">
                                    <td className="px-4 py-2 text-sm">
                                        <span className="px-2 py-1 text-xs rounded bg-gray-100 text-gray-700">
                                            {r.level}
                                        </span>
                                    </td>
                                    <td className="px-4 py-2 text-sm text-gray-900">{r.name}</td>
                                    <td className="px-4 py-2 text-sm text-right font-mono text-gray-900">
                                        {fmt(r.value, r.fmt)}
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
"""


TEMPLATE = {
    "key": "kmc_project_metrics_v2",
    "name": "KMC Project Metrics (v2)",
    "description": "Program-level M&E for KMC, on top of the entity-stage pipeline.",
    "icon": "fa-chart-line",
    "color": "blue",
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schemas": PIPELINE_SCHEMAS,
}
