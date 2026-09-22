"""
RUTF CIFF Program KPIs Workflow Template.

Program-level M&E dashboard for a CIFF-funded RUTF (community-based malnutrition
treatment) program, built against opportunity 2230's "RUTF Deliver - NG - CBI - P1 -
Sep 26" app (Program 263).

Follows the SAME data-access pattern as the sibling
flw_daily_summary_report_rutf.py (verified against real production data for this
same program): ONE visit_level `connect_csv` pipeline covering every form
submission (any status), with per-form custom fields read directly off the raw
row -- NOT entity-stage. Entity-stage grouping does not work for this app's
Screening form: a child screened OUT (rutf_enrollment != 'yes') never gets a
CommCare case opened, so `entity_id` is null on those rows and an entity-stage
GROUP BY would silently collapse or drop them. Per-child dedup (for treatment /
post-recovery indicators, where every row DOES have an entity_id) is done in
render code instead, mirroring compute_flw_daily_summary_rutf.py's approach of
deriving distinct-entity subsets in Python from one flat fetch.

Form/field mapping (traced through the app's own `calculate` XPath logic via
get_opportunity_apps, not guessed from question labels):
  - "Screening " (trailing space is the app's own form name; module: Initial
    Screening): screening_outcome/rutf_enrollment (SAM identified + enrolled),
    screening_outcome/needs_referral, muac_qualifies, oedema_grade,
    danger_signs/any_danger_sign. entity_id is set only when enrolled.
  - "Visit Form" (module: Visits Due; follow-up visits for an already-enrolled
    child): case_state/outcome_value (enrolled/recovered/deceased/non_response/
    lost_for_follow_up/visit_referred), case_state/default_reason,
    case_state/recovered_date, rutf_dispensing/counseling/counseling_topics,
    section_2__referral_details/referral_facility.
  - "Monthly Visit" (module: Post Recovery Check-In; the app's own in-person
    3-month post-exit check-in): outcome_value (re_enroll/post_visit_refer
    signal relapse; recovered = no relapse).

Indicators not present in this app's form data at all (immunization linkage,
LLO contracts, MOUs, FLW anomaly/suspension counts) are NOT computed here --
they're manual entries stored in `instance.state.manual_kpis`, editable from
the render.

Like the sibling template, if a different RUTF opp is ever pointed at this
template, re-verify form names and field paths with pipeline_preview first --
RUTF app case/form naming is not standardized across opportunities.
"""

from __future__ import annotations

SCREENING_FORM_NAME = "Screening "
VISIT_FORM_NAME = "Visit Form"
MONTHLY_VISIT_FORM_NAME = "Monthly Visit"

DEFINITION = {
    "name": "RUTF CIFF Program KPIs",
    "description": "Program-level M&E dashboard tracking the CIFF RUTF indicator framework against targets",
    "version": 1,
    "templateType": "rutf_ciff_kpis",
    "statuses": [],
    "config": {"showSummaryCards": False, "showFilters": False},
    "pipeline_sources": [],
}

PIPELINE_SCHEMAS = [
    {
        "alias": "visits",
        "name": "RUTF Visits (CIFF KPIs)",
        "description": (
            "Every form submission on the deliver unit, ANY status, with the fields the CIFF KPI "
            "dashboard needs across Screening / Visit Form / Monthly Visit. Per-form field values are "
            "null on rows from other forms; split and deduped in render code, same convention as "
            "flw_daily_summary_compute_rutf.py."
        ),
        "schema": {
            "data_source": {"type": "connect_csv"},
            "grouping_key": "username",
            "terminal_stage": "visit_level",
            "filters": {},
            "fields": [
                {"name": "form_display_name", "path": "form.@name", "aggregation": "first"},
                {"name": "status", "path": "status", "aggregation": "first"},
                {"name": "entity_id", "path": "entity_id", "aggregation": "first"},
                {"name": "time_start", "path": "form.meta.timeStart", "aggregation": "first"},
                # Screening fields.
                {
                    "name": "rutf_enrollment",
                    "path": "form.screening_outcome.rutf_enrollment",
                    "aggregation": "first",
                },
                {
                    "name": "needs_referral_screening",
                    "path": "form.screening_outcome.needs_referral",
                    "aggregation": "first",
                },
                {
                    "name": "muac_qualifies",
                    "path": "form.anthropometric_appetite.muac_measurement.muac_qualifies",
                    "aggregation": "first",
                },
                {
                    "name": "oedema_grade",
                    "path": "form.anthropometric_appetite.oedema.oedema_grade",
                    "aggregation": "first",
                },
                {
                    "name": "any_danger_sign",
                    "path": "form.danger_signs.any_danger_sign",
                    "aggregation": "first",
                },
                # Visit Form fields.
                {"name": "outcome_value_visit", "path": "form.case_state.outcome_value", "aggregation": "first"},
                {"name": "default_reason", "path": "form.case_state.default_reason", "aggregation": "first"},
                {
                    "name": "counseling_topics",
                    "path": "form.rutf_dispensing.counseling.counseling_topics",
                    "aggregation": "first",
                },
                {
                    "name": "referral_facility",
                    "path": "form.section_2__referral_details.referral_facility",
                    "aggregation": "first",
                },
                # Monthly Visit (3-month post-recovery check-in) field.
                {"name": "outcome_value_monthly", "path": "form.outcome_value", "aggregation": "first"},
            ],
        },
    },
]


# Render code derives every indicator from the single flat `visits` pipeline,
# splitting by form_display_name and deduping by entity_id where a per-child
# (not per-visit) count is needed -- mirroring
# flw_daily_summary_compute_rutf.py's compute_flw_daily_summary_rutf, just done
# in JS since this dashboard is interactive rather than a scheduled snapshot.
# Manual (non-computable) indicators live in instance.state.manual_kpis.
RENDER_CODE = r"""function WorkflowUI({ definition, instance, workers, pipelines, links, actions, onUpdateState }) {
    var visits = (pipelines && pipelines.visits && pipelines.visits.rows) || [];

    var SCREENING = 'Screening ';
    var VISIT_FORM = 'Visit Form';
    var MONTHLY_VISIT = 'Monthly Visit';
    var APPROVED = 'approved';
    var NOT_COMPLETED = { deceased: true, lost_for_follow_up: true, non_response: true };
    var RELAPSE_OUTCOMES = { re_enroll: true, post_visit_refer: true };

    var manual = (instance.state && instance.state.manual_kpis) || {};
    var manualKeys = [
        { key: 'sam_immunization_pct', label: '% SAM cases also receiving immunization info/services', target: 'N/A' },
        { key: 'screened_immunization_pct', label: '% screened for malnutrition also receiving immunization services', target: 'N/A' },
        { key: 'llo_contracts', label: '# of LLO contracts established', target: '2-3 LLOs' },
        { key: 'flws_flagged', label: '# of FLWs flagged for anomalous data', target: '' },
        { key: 'flws_suspended', label: '# of FLWs suspended', target: '' },
        { key: 'referral_centers_mou', label: '# of inpatient referral centers engaged (MOUs)', target: '' },
    ];

    function distinctEntityCount(rows) {
        var seen = {};
        var n = 0;
        rows.forEach(function (r) {
            if (r.entity_id && !seen[r.entity_id]) { seen[r.entity_id] = true; n += 1; }
        });
        return n;
    }

    // One row per entity_id: the row with the latest time_start.
    function latestByEntity(rows) {
        var byEntity = {};
        rows.forEach(function (r) {
            if (!r.entity_id) return;
            var existing = byEntity[r.entity_id];
            if (!existing || String(r.time_start) > String(existing.time_start)) {
                byEntity[r.entity_id] = r;
            }
        });
        return Object.keys(byEntity).map(function (k) { return byEntity[k]; });
    }

    var metrics = React.useMemo(function () {
        var screeningRows = visits.filter(function (r) { return r.form_display_name === SCREENING; });
        var approvedScreeningRows = screeningRows.filter(function (r) { return r.status === APPROVED; });
        var enrolledRows = approvedScreeningRows.filter(function (r) { return r.rutf_enrollment === 'yes'; });
        var referredAtScreeningRows = approvedScreeningRows.filter(function (r) { return r.needs_referral_screening === 'yes'; });

        var totalScreened = screeningRows.length;
        var enrolledCount = distinctEntityCount(enrolledRows);
        var referredAtScreeningCount = distinctEntityCount(referredAtScreeningRows);

        var visitFormRows = visits.filter(function (r) { return r.form_display_name === VISIT_FORM && r.status === APPROVED; });
        var latestPerChild = latestByEntity(visitFormRows);
        var totalTreated = latestPerChild.length;
        var recoveredCount = latestPerChild.filter(function (r) { return r.outcome_value_visit === 'recovered'; }).length;
        var notCompletedCount = latestPerChild.filter(function (r) { return NOT_COMPLETED[r.outcome_value_visit]; }).length;
        var deceasedCount = latestPerChild.filter(function (r) { return r.outcome_value_visit === 'deceased'; }).length;
        var caregiversEngaged = visitFormRows.filter(function (r) {
            return r.counseling_topics != null && String(r.counseling_topics).trim() !== '';
        }).length;
        var referredHigherCare = distinctEntityCount(visitFormRows.filter(function (r) {
            return r.referral_facility != null && String(r.referral_facility).trim() !== '';
        }));

        var monthlyRows = visits.filter(function (r) { return r.form_display_name === MONTHLY_VISIT && r.status === APPROVED; });
        var latestMonthlyPerChild = latestByEntity(monthlyRows);
        var checkinTotal = latestMonthlyPerChild.length;
        var relapsedCount = latestMonthlyPerChild.filter(function (r) { return RELAPSE_OUTCOMES[r.outcome_value_monthly]; }).length;

        return {
            totalScreened: totalScreened,
            enrolledCount: enrolledCount,
            pctIdentified: totalScreened > 0 ? enrolledCount / totalScreened : null,
            referredAtScreeningCount: referredAtScreeningCount,
            totalTreated: totalTreated,
            pctRecovery: totalTreated > 0 ? recoveredCount / totalTreated : null,
            pctNotCompleted: totalTreated > 0 ? notCompletedCount / totalTreated : null,
            deceasedCount: deceasedCount,
            caregiversEngaged: caregiversEngaged,
            referredHigherCare: referredHigherCare,
            pctNoRelapse: checkinTotal > 0 ? (checkinTotal - relapsedCount) / checkinTotal : null,
            checkinTotal: checkinTotal,
        };
    }, [visits]);

    function fmt(v, kind) {
        if (v == null) return '—';
        if (kind === 'pct') return (v * 100).toFixed(1) + '%';
        return Number(v).toLocaleString();
    }

    var rows = [
        { level: 'Intermediate Outcome', name: '% without relapse at 3 months post-treatment', value: fmt(metrics.pctNoRelapse, 'pct'), target: '≥80% recovered', freq: 'Monthly', note: metrics.checkinTotal + ' checked in at 3mo' },
        { level: 'Intermediate Outcome', name: '% reaching anthropometric recovery', value: fmt(metrics.pctRecovery, 'pct'), target: '', freq: 'Monthly' },
        { level: 'Intermediate Outcome', name: '% not completing treatment (opt-out/referred-out/deaths)', value: fmt(metrics.pctNotCompleted, 'pct'), target: '', freq: 'Monthly', note: metrics.deceasedCount + ' deceased' },
        { level: 'Intermediate Outcome', name: '# of caregivers engaged during visits', value: fmt(metrics.caregiversEngaged), target: 'N/A', freq: 'Quarterly' },
        { level: 'Intermediate Outcome', name: '#/% of SAM cases referred for higher-level care', value: fmt(metrics.referredHigherCare), target: 'N/A', freq: 'Monthly' },
        { level: 'Results', name: '# of SAM and MAM cases identified', value: fmt(metrics.enrolledCount), target: 'N/A', freq: 'Monthly' },
        { level: 'Results', name: '% of screened identified as SAM/MAM', value: fmt(metrics.pctIdentified, 'pct'), target: 'N/A', freq: 'Monthly' },
        { level: 'Results', name: '# of SAM cases enrolled in home-based RUTF treatment', value: fmt(metrics.enrolledCount), target: '2,500 enrolled', freq: 'Monthly' },
        { level: 'Results', name: '# of complicated SAM cases referred following screening', value: fmt(metrics.referredAtScreeningCount), target: '95% of engaged FLWs', freq: 'Monthly' },
        { level: 'Inputs', name: '# of children screened for malnutrition', value: fmt(metrics.totalScreened), target: '40,000 screened', freq: 'Monthly' },
    ];

    function ManualRow(props) {
        var m = props.m;
        var val = manual[m.key];
        var draft = React.useState(val == null ? '' : String(val));
        var draftVal = draft[0];
        var setDraft = draft[1];
        var saving = React.useState(false);
        var isSaving = saving[0];
        var setSaving = saving[1];

        function save() {
            setSaving(true);
            var next = {};
            for (var k in manual) { next[k] = manual[k]; }
            next[m.key] = draftVal;
            onUpdateState({ manual_kpis: next }).then(function () { setSaving(false); });
        }

        return (
            <tr className="hover:bg-gray-50">
                <td className="px-4 py-2 text-sm">
                    <span className="px-2 py-1 text-xs rounded bg-amber-100 text-amber-800">Inputs</span>
                </td>
                <td className="px-4 py-2 text-sm text-gray-900">
                    {m.label}
                    <span className="ml-2 text-xs text-gray-400">(manual)</span>
                </td>
                <td className="px-4 py-2 text-sm text-right">
                    <input
                        type="text"
                        className="w-24 border rounded px-2 py-1 text-right text-sm"
                        value={draftVal}
                        onChange={function (e) { setDraft(e.target.value); }}
                    />
                    <button
                        className="ml-2 text-xs text-blue-600 hover:underline disabled:opacity-50"
                        disabled={isSaving}
                        onClick={save}
                    >
                        {isSaving ? 'Saving…' : 'Save'}
                    </button>
                </td>
                <td className="px-4 py-2 text-sm text-right text-gray-500">{m.target}</td>
                <td className="px-4 py-2 text-sm text-right text-gray-500">—</td>
            </tr>
        );
    }

    return (
        <div className="space-y-6">
            <div className="bg-white rounded-lg shadow-sm p-6">
                <h1 className="text-2xl font-bold text-gray-900">{definition.name}</h1>
                <p className="text-gray-600 mt-1">{definition.description}</p>
                <div className="mt-2 text-xs text-gray-500">
                    {metrics.totalScreened} screened · {metrics.totalTreated} in treatment · {metrics.checkinTotal} at 3-month check-in
                </div>
            </div>

            <div className="bg-white rounded-lg shadow-sm overflow-hidden">
                <table className="min-w-full divide-y divide-gray-200">
                    <thead className="bg-gray-50">
                        <tr>
                            <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Level</th>
                            <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Indicator</th>
                            <th className="px-4 py-2 text-right text-xs font-medium text-gray-500 uppercase">Actual</th>
                            <th className="px-4 py-2 text-right text-xs font-medium text-gray-500 uppercase">Target</th>
                            <th className="px-4 py-2 text-right text-xs font-medium text-gray-500 uppercase">Frequency</th>
                        </tr>
                    </thead>
                    <tbody className="bg-white divide-y divide-gray-200">
                        {rows.map(function (r, i) {
                            return (
                                <tr key={i} className="hover:bg-gray-50">
                                    <td className="px-4 py-2 text-sm">
                                        <span className="px-2 py-1 text-xs rounded bg-gray-100 text-gray-700">{r.level}</span>
                                    </td>
                                    <td className="px-4 py-2 text-sm text-gray-900">
                                        {r.name}
                                        {r.note ? <span className="ml-2 text-xs text-gray-400">({r.note})</span> : null}
                                    </td>
                                    <td className="px-4 py-2 text-sm text-right font-mono text-gray-900">{r.value}</td>
                                    <td className="px-4 py-2 text-sm text-right text-gray-500">{r.target}</td>
                                    <td className="px-4 py-2 text-sm text-right text-gray-500">{r.freq}</td>
                                </tr>
                            );
                        })}
                        {manualKeys.map(function (m, i) {
                            return <ManualRow key={'manual-' + i} m={m} />;
                        })}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
"""


TEMPLATE = {
    "key": "rutf_ciff_kpis",
    "name": "RUTF CIFF Program KPIs",
    "description": "Program-level M&E dashboard tracking the CIFF RUTF indicator framework against targets",
    "icon": "fa-chart-line",
    "color": "emerald",
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schemas": PIPELINE_SCHEMAS,
}
