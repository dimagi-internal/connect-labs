"""
RUTF CIFF Program KPIs Workflow Template.

Program-level M&E dashboard for a CIFF-funded RUTF (community-based malnutrition
treatment) program, built against opportunity 2230's "RUTF Deliver - NG - CBI - P1 -
Sep 26" app. Field paths below were verified against that app's actual form
`calculate` logic (via get_opportunity_apps), not guessed from labels — but a
different RUTF opp's app may name things differently (form/case-block naming is
not standardized across RUTF apps the way, e.g., meta fields are). Re-verify paths
with pipeline_preview before pointing this template at another opportunity.

Three entity-stage pipelines, linked on the child's case id:
  - `screening`     (Screening form): SAM/MAM identification & enrollment decision.
  - `treatment`     (Visit Form): treatment visits, exit/recovery, referral,
                     caregiver counseling.
  - `post_recovery` (Monthly Visit form, the app's own 3-month post-exit
                     check-in): relapse status at the 3-month checkpoint.

Indicators that aren't in this app's form data at all (immunization linkage,
LLO contracts, MOUs, FLW anomaly/suspension counts) are NOT computed here --
they're manual entries stored in `instance.state.manual_kpis`, editable from the
render. See docs/plans (or the PR description) for the full indicator-to-field
mapping this was built from.
"""

DEFINITION = {
    "name": "RUTF CIFF Program KPIs",
    "description": "Program-level M&E dashboard tracking the CIFF RUTF indicator framework against targets",
    "version": 1,
    "templateType": "rutf_ciff_kpis",
    "statuses": [],
    "config": {"showSummaryCards": False, "showFilters": False},
    "pipeline_sources": [],
}


def _f(name, paths, transform=None, aggregation="last"):
    field = {"name": name, "paths": paths, "aggregation": aggregation}
    if transform:
        field["transform"] = transform
    return field


# Case id is created on the enrollment visit and referenced identically as
# "the child's case" by every later form. Multiple candidate paths because the
# case block sits in a different XPath group in the Screening form (nested
# under the conditional enrollment visit) vs. later Visit Form submissions.
_CASE_ID_PATHS = [
    "form.case.@case_id",
    "form.visits_case.visit_case_name.case.@case_id",
    "form.visit_1.visits_case.visit_case_name.case.@case_id",
    "form.visit_1.visits_case.child_case_id",
    "form.var.child_case_id",
]

PIPELINE_SCHEMAS = [
    {
        "alias": "screening",
        "name": "RUTF Screening",
        "description": "One row per screened child -- SAM/MAM identification and enrollment decision",
        "schema": {
            "data_source": {
                "type": "cchq_forms",
                "form_name": "Screening",
                "app_id_source": "opportunity",
            },
            "grouping_key": "username",
            "terminal_stage": "entity",
            "linking_field": "case_id",
            "fields": [
                _f("case_id", _CASE_ID_PATHS, aggregation="first"),
                _f("rutf_enrollment", ["form.screening_outcome.rutf_enrollment"]),
                _f("needs_referral", ["form.screening_outcome.needs_referral"]),
                _f("muac_qualifies", ["form.anthropometric_appetite.muac_measurement.muac_qualifies"]),
                _f("muac_cm", ["form.anthropometric_appetite.muac_measurement.muac_cm"], transform="float"),
                _f("oedema_grade", ["form.anthropometric_appetite.oedema.oedema_grade"]),
                _f("any_danger_sign", ["form.danger_signs.any_danger_sign"]),
                _f("screening_date", ["form.meta.timeEnd"], transform="date", aggregation="first"),
                _f("flw_username", ["form.meta.username"], aggregation="first"),
            ],
            "histograms": [],
            "filters": {},
        },
    },
    {
        "alias": "treatment",
        "name": "RUTF Treatment Visits",
        "description": "One row per enrolled child -- latest treatment status, exit/recovery, referral, counseling",
        "schema": {
            "data_source": {
                "type": "cchq_forms",
                "form_name": "Visit Form",
                "app_id_source": "opportunity",
            },
            "grouping_key": "username",
            "terminal_stage": "entity",
            "linking_field": "case_id",
            "fields": [
                _f("case_id", _CASE_ID_PATHS, aggregation="first"),
                _f("outcome_value", ["form.case_state.outcome_value"]),
                _f("default_reason", ["form.case_state.default_reason"]),
                _f("recovered_date", ["form.case_state.recovered_date"], transform="date"),
                _f("counseling_topics", ["form.rutf_dispensing.counseling.counseling_topics"]),
                _f("referral_facility", ["form.section_2__referral_details.referral_facility"]),
                _f("referral_reason", ["form.section_2__referral_details.referral_reason"]),
                _f("flw_username", ["form.meta.username"], aggregation="first"),
                {
                    "name": "visit_count",
                    "path": "form.meta.instanceID",
                    "aggregation": "count",
                },
                _f("last_visit_date", ["form.meta.timeEnd"], transform="date"),
            ],
            "histograms": [],
            "filters": {},
        },
    },
    {
        "alias": "post_recovery",
        "name": "RUTF 3-Month Post-Recovery Check-in",
        "description": "One row per child reaching the app's monthly post-exit check-in -- relapse signal at 3 months",
        "schema": {
            "data_source": {
                "type": "cchq_forms",
                "form_name": "Monthly Visit",
                "app_id_source": "opportunity",
            },
            "grouping_key": "username",
            "terminal_stage": "entity",
            "linking_field": "case_id",
            "fields": [
                _f("case_id", _CASE_ID_PATHS, aggregation="first"),
                _f("outcome_value", ["form.outcome_value"]),
                _f("checkin_date", ["form.meta.timeEnd"], transform="date"),
            ],
            "histograms": [],
            "filters": {},
        },
    },
]


# Render code computes indicators render-side from the three entity-stage
# pipelines, and reads/writes manual (non-computable) indicators via
# instance.state.manual_kpis. Table layout mirrors kmc_project_metrics (rows
# grouped by Level), with a Target column added since this sheet has real
# numeric targets.
RENDER_CODE = r"""function WorkflowUI({ definition, instance, workers, pipelines, links, actions, onUpdateState }) {
    var screening = (pipelines && pipelines.screening && pipelines.screening.rows) || [];
    var treatment = (pipelines && pipelines.treatment && pipelines.treatment.rows) || [];
    var postRecovery = (pipelines && pipelines.post_recovery && pipelines.post_recovery.rows) || [];

    var manual = (instance.state && instance.state.manual_kpis) || {};
    var manualKeys = [
        { key: 'sam_immunization_pct', label: '% SAM cases also receiving immunization info/services', target: 'N/A' },
        { key: 'screened_immunization_pct', label: '% screened for malnutrition also receiving immunization services', target: 'N/A' },
        { key: 'llo_contracts', label: '# of LLO contracts established', target: '2-3 LLOs' },
        { key: 'flws_flagged', label: '# of FLWs flagged for anomalous data', target: '' },
        { key: 'flws_suspended', label: '# of FLWs suspended', target: '' },
        { key: 'referral_centers_mou', label: '# of inpatient referral centers engaged (MOUs)', target: '' },
    ];

    var NOT_COMPLETED = { deceased: true, lost_for_follow_up: true, non_response: true };
    var RELAPSE_OUTCOMES = { re_enroll: true, post_visit_refer: true };

    var metrics = React.useMemo(function () {
        var totalScreened = screening.length;
        var enrolled = screening.filter(function (r) { return r.rutf_enrollment === 'yes'; });
        var referredAtScreening = screening.filter(function (r) { return r.needs_referral === 'yes'; }).length;

        var totalTreated = treatment.length;
        var recovered = treatment.filter(function (r) { return r.outcome_value === 'recovered'; }).length;
        var notCompleted = treatment.filter(function (r) { return NOT_COMPLETED[r.outcome_value]; }).length;
        var deceased = treatment.filter(function (r) { return r.outcome_value === 'deceased'; }).length;
        var caregiversEngaged = treatment.filter(function (r) {
            return r.counseling_topics != null && String(r.counseling_topics).trim() !== '';
        }).length;
        var referredHigherCare = treatment.filter(function (r) {
            return r.referral_facility != null && String(r.referral_facility).trim() !== '';
        }).length;

        var checkinTotal = postRecovery.length;
        var relapsed = postRecovery.filter(function (r) { return RELAPSE_OUTCOMES[r.outcome_value]; }).length;

        return {
            totalScreened: totalScreened,
            enrolledCount: enrolled.length,
            pctIdentified: totalScreened > 0 ? enrolled.length / totalScreened : null,
            referredAtScreening: referredAtScreening,
            totalTreated: totalTreated,
            pctRecovery: totalTreated > 0 ? recovered / totalTreated : null,
            pctNotCompleted: totalTreated > 0 ? notCompleted / totalTreated : null,
            deceased: deceased,
            caregiversEngaged: caregiversEngaged,
            referredHigherCare: referredHigherCare,
            pctNoRelapse: checkinTotal > 0 ? (checkinTotal - relapsed) / checkinTotal : null,
            checkinTotal: checkinTotal,
        };
    }, [screening, treatment, postRecovery]);

    function fmt(v, kind) {
        if (v == null) return '—';
        if (kind === 'pct') return (v * 100).toFixed(1) + '%';
        return Number(v).toLocaleString();
    }

    var rows = [
        { level: 'Intermediate Outcome', name: '% without relapse at 3 months post-treatment', value: fmt(metrics.pctNoRelapse, 'pct'), target: '≥80% recovered', freq: 'Monthly', note: metrics.checkinTotal + ' checked in at 3mo' },
        { level: 'Intermediate Outcome', name: '% reaching anthropometric recovery', value: fmt(metrics.pctRecovery, 'pct'), target: '', freq: 'Monthly' },
        { level: 'Intermediate Outcome', name: '% not completing treatment (opt-out/referred-out/deaths)', value: fmt(metrics.pctNotCompleted, 'pct'), target: '', freq: 'Monthly', note: metrics.deceased + ' deceased' },
        { level: 'Intermediate Outcome', name: '# of caregivers engaged during visits', value: fmt(metrics.caregiversEngaged), target: 'N/A', freq: 'Quarterly' },
        { level: 'Intermediate Outcome', name: '#/% of SAM cases referred for higher-level care', value: fmt(metrics.referredHigherCare), target: 'N/A', freq: 'Monthly' },
        { level: 'Results', name: '# of SAM and MAM cases identified', value: fmt(metrics.enrolledCount), target: 'N/A', freq: 'Monthly' },
        { level: 'Results', name: '% of screened identified as SAM/MAM', value: fmt(metrics.pctIdentified, 'pct'), target: 'N/A', freq: 'Monthly' },
        { level: 'Results', name: '# of SAM cases enrolled in home-based RUTF treatment', value: fmt(metrics.enrolledCount), target: '2,500 enrolled', freq: 'Monthly' },
        { level: 'Results', name: '# of complicated SAM cases referred following screening', value: fmt(metrics.referredAtScreening), target: '95% of engaged FLWs', freq: 'Monthly' },
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
                    {screening.length} screened · {treatment.length} in treatment · {postRecovery.length} at 3-month check-in
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
