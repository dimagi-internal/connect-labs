"""FLW Weekly Audit Report — Program 176 (CHC PRE-RCT Nigeria).

Workflow 1 of a two-workflow pair (see flw_audit_workflows_spec.md). This
template computes a fixed set of household/child, visit-cadence, and
fraud/fake-visit indicators per opportunity per FLW per week, from raw
"Health Service Delivery" form submissions — not the analysis/graphs
themselves, just the computed indicator values, structured so a second,
separate cross-opportunity trend-dashboard workflow can consume them cheaply
(one query per opportunity across many weekly runs, via list_runs).

Runs on a schedule (WorkflowSchedule, weekly, hour=0 UTC = 1am Africa/Lagos,
no DST) via run_default below. No interactive review step: run_default both
computes the week's indicators AND completes the run in one atomic pass
(there's no human "Mark Run Complete" click in this template's lifecycle),
so every run this template creates goes straight from nonexistent to
completed.

Data source: a single connect_csv pipeline reading "Health Service
Delivery" form submissions (Connect's own visit export mirrors the raw
CommCare form JSON — see flw_audit_workflows_spec.md's own confirmed
assumption that received_on/completed_time-style fields, not started_time,
would normally bound the window; this instance was explicitly configured to
use started_time per user instruction). No CommCare HQ pipeline is needed:
every field this report needs (MUAC, GPS, timing, demographics) is already
mirrored into Connect's UserVisit.form_json for this form.
"""
from __future__ import annotations

PIPELINE_SCHEMAS = [
    {
        "alias": "hsd_visits",
        "name": "Health Service Delivery Visits",
        "description": (
            "Every Health Service Delivery form submission, all statuses, with GPS/timing/"
            "MUAC/demographic fields extracted for the FLW audit indicator computation."
        ),
        "schema": {
            "data_source": {"type": "connect_csv"},
            "grouping_key": "username",
            "terminal_stage": "visit_level",
            "filters": {},
            "fields": [
                {"name": "form_display_name", "path": "form.@name", "aggregation": "first"},
                {
                    "name": "muac_cm",
                    "path": "form.case.update.soliciter_muac_cm",
                    "transform": "float",
                    "aggregation": "first",
                    "description": "MUAC measurement in cm (1 decimal place)",
                },
                {"name": "muac_colour", "path": "form.case.update.muac_colour", "aggregation": "first"},
                {
                    "name": "childs_gender",
                    "paths": ["form.additional_case_info.childs_gender", "form.case.update.childs_gender"],
                    "aggregation": "first",
                },
                {"name": "childs_dob", "path": "form.additional_case_info.childs_dob", "aggregation": "first"},
                {
                    "name": "age_months",
                    "paths": [
                        "form.additional_case_info.childs_age_in_months",
                        "form.case.update.childs_age_in_months",
                    ],
                    "transform": "float",
                    "aggregation": "first",
                },
                {
                    "name": "age_days",
                    "path": "form.additional_case_info.child_age_in_days",
                    "transform": "float",
                    "aggregation": "first",
                    "description": "Support-only: not a persisted case property, only available from this form's own calculation.",
                },
                {
                    "name": "hh_case_id",
                    "paths": ["form.additional_case_info.hh_case_id", "form.case.update.hh_case_id"],
                    "aggregation": "first",
                },
                {"name": "child_case_id", "path": "form.case.@case_id", "aggregation": "first"},
                {"name": "wa_caseid", "path": "form.case.update.wa_caseid", "aggregation": "first"},
                {
                    "name": "current_accuracy",
                    "path": "form.user_location_check.location_blocks.gps_block.current_accuracy",
                    "transform": "float",
                    "aggregation": "first",
                },
                {
                    "name": "accuracy_minimum",
                    "path": "form.user_location_check.location_blocks.gps_block.accuracy_minimum",
                    "transform": "float",
                    "aggregation": "first",
                },
                {
                    "name": "normalized_lat",
                    "path": "form.user_location_check.location_blocks.gps_block.normalized_lat",
                    "transform": "float",
                    "aggregation": "first",
                },
                {
                    "name": "normalized_lon",
                    "path": "form.user_location_check.location_blocks.gps_block.normalized_lon",
                    "transform": "float",
                    "aggregation": "first",
                },
                {"name": "time_start", "path": "form.meta.timeStart", "aggregation": "first"},
                {"name": "time_end", "path": "form.meta.timeEnd", "aggregation": "first"},
                {
                    "name": "all_service_del_checks",
                    "path": "form.calculations.all_service_del_checks",
                    "aggregation": "first",
                },
                {
                    "name": "dw_meds_delivery_status",
                    "path": "form.case.update.dw_meds_delivery_status",
                    "aggregation": "first",
                    "description": "'DW Delivered' when deworming meds were actually administered this visit "
                    "(see Connect-CHC System Design Document's dw_check logic) -- distinct from all_service_del_checks, "
                    "which also passes on a valid exemption (too young, recently dosed, unwell).",
                },
                {
                    "name": "received_any_vaccine",
                    "path": "form.case.update.received_any_vaccine",
                    "aggregation": "first",
                    "description": "'yes'/'no' -- whether the child received a vaccine this visit.",
                },
            ],
        },
    },
    {
        "alias": "approved_visits",
        "name": "Approved Health Service Delivery Visits",
        "description": "Same form, filtered to Connect-approved status only — for the Total Approved Visits indicator.",
        "schema": {
            "data_source": {"type": "connect_csv"},
            "grouping_key": "username",
            "terminal_stage": "visit_level",
            "filters": {"status": ["approved"]},
            "fields": [
                {"name": "form_display_name", "path": "form.@name", "aggregation": "first"},
                {"name": "time_start", "path": "form.meta.timeStart", "aggregation": "first"},
            ],
        },
    },
]

DEFINITION = {
    "name": "FLW Weekly Audit Report",
    "description": (
        "Program 176 (CHC PRE-RCT Nigeria) weekly per-FLW audit indicators — household/child "
        "composition, visit cadence, and fraud/data-quality flags. Computed automatically every "
        "Monday; not an interactive review (no statuses to assign)."
    ),
    "version": 1,
    "templateType": "flw_weekly_audit_report",
    "statuses": [],
    "config": {
        "showSummaryCards": True,
        "showFilters": True,
    },
    "pipeline_sources": [],  # Populated at creation time from PIPELINE_SCHEMAS
    "snapshot_inputs": {"pipelines": [], "workers": False, "state_keys": ["flw_audit_report"]},
}

SNAPSHOT_SCHEMA = {
    "version": 1,
    "keys": {
        "state.flw_audit_report.period_start": "ISO date, the Monday this run's window starts",
        "state.flw_audit_report.period_end": "ISO date, the Sunday this run's window ends",
        "state.flw_audit_report.generated_at": "ISO timestamp this run was computed",
        "state.flw_audit_report.flws": "List of per-FLW indicator dicts for this opportunity+week",
    },
}

RENDER_CODE = """function WorkflowUI({ definition, instance, view }) {
    var report = (view.state && view.state.flw_audit_report) || null;

    if (!view.isCompleted || !report) {
        return (
            <div className="p-6 text-gray-600">
                <p className="font-medium">No completed report yet for this opportunity.</p>
                <p className="text-sm mt-1">
                    This workflow is generated automatically by a weekly schedule, not manually — check
                    Labs Admin → Scheduled Workflows if you expected data here.
                </p>
            </div>
        );
    }

    var flws = report.flws || [];

    return (
        <div className="space-y-4 p-4">
            <div>
                <h1 className="text-xl font-bold">{definition.name}</h1>
                <p className="text-sm text-gray-500">
                    Week {report.period_start} – {report.period_end} · generated {report.generated_at}
                </p>
            </div>
            <div className="text-sm text-gray-600">{flws.length} FLW(s) with activity this week</div>
            <div className="overflow-x-auto border rounded-lg">
                <table className="min-w-full text-sm">
                    <thead className="bg-gray-50">
                        <tr>
                            <th className="px-3 py-2 text-left font-semibold">FLW</th>
                            <th className="px-3 py-2 text-right font-semibold">Forms</th>
                            <th className="px-3 py-2 text-right font-semibold">Approved</th>
                            <th className="px-3 py-2 text-right font-semibold">Days Worked</th>
                            <th className="px-3 py-2 text-right font-semibold">Avg Children/HH</th>
                            <th className="px-3 py-2 text-right font-semibold">% Gap &lt;3min</th>
                            <th className="px-3 py-2 text-right font-semibold">GPS Flags</th>
                            <th className="px-3 py-2 text-right font-semibold">Speed Flags</th>
                            <th className="px-3 py-2 text-right font-semibold">Duration Outliers</th>
                            <th className="px-3 py-2 text-right font-semibold">Whipple Idx</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y">
                        {flws.map(function (f, i) {
                            var fraud = f.fraud || {};
                            return (
                                <tr key={f.username || i}>
                                    <td className="px-3 py-2 font-mono text-xs">{f.username}</td>
                                    <td className="px-3 py-2 text-right">{f.total_service_delivery_forms}</td>
                                    <td className="px-3 py-2 text-right">{f.total_approved_visits}</td>
                                    <td className="px-3 py-2 text-right">{f.days_worked}</td>
                                    <td className="px-3 py-2 text-right">{f.avg_children_per_household}</td>
                                    <td className="px-3 py-2 text-right">{f.pct_gap_lt_3min}</td>
                                    <td className="px-3 py-2 text-right">{fraud.gps_accuracy_flag_count}</td>
                                    <td className="px-3 py-2 text-right">{fraud.implied_speed_flag_count}</td>
                                    <td className="px-3 py-2 text-right">{fraud.form_duration_outlier_count}</td>
                                    <td className="px-3 py-2 text-right">{fraud.age_heaping_whipple_index}</td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
        </div>
    );
}"""


def run_default(*, definition, access_token, request=None, window=None, cchq_access_token=None, **_):
    """Compute this week's FLW audit indicators for every opportunity this
    (program-owned, multi-opp) definition spans, creating and completing one
    run per opportunity.

    Unlike weekly_dual_track_audit's run_default, this template never leaves
    a run in_progress for a later "Mark Complete" click — the run is created
    and completed in the same call, since the whole point is an unattended
    weekly snapshot with no reviewer in the loop. cchq_access_token is
    accepted (the scheduler always forwards one when available) but unused:
    every field this report needs comes from Connect's own connect_csv
    export, not CommCare HQ.
    """
    from collections import defaultdict
    from datetime import datetime, timedelta, timezone

    from connect_labs.workflow.data_access import WorkflowDataAccess
    from connect_labs.workflow.flw_audit_compute import FORM_NAME, compute_flw_indicators

    if window is not None:
        window_start, window_end = window
    else:
        now = datetime.now(timezone.utc)
        this_monday_00utc = datetime(now.year, now.month, now.day, tzinfo=timezone.utc) - timedelta(days=now.weekday())
        if now < this_monday_00utc:
            this_monday_00utc -= timedelta(days=7)
        window_end = this_monday_00utc
        window_start = window_end - timedelta(days=7)

    opp_ids = definition.opportunity_ids or ([definition.opportunity_id] if definition.opportunity_id else [])
    if not opp_ids:
        raise ValueError("flw_weekly_audit_report requires at least one opportunity on the definition")

    if definition.program_id:
        fetch_wda = WorkflowDataAccess(access_token=access_token, program_id=definition.program_id)
    else:
        fetch_wda = WorkflowDataAccess(access_token=access_token, opportunity_id=opp_ids[0])
    try:
        pipeline_data = fetch_wda.get_pipeline_data(definition.id, opportunity_id=opp_ids[0])
    finally:
        fetch_wda.close()

    def _parse(ts):
        if not ts:
            return None
        try:
            return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None

    def _in_window(row):
        dt = _parse(row.get("time_start"))
        return dt is not None and window_start <= dt < window_end

    hsd_rows = [
        r
        for r in pipeline_data.get("hsd_visits", {}).get("rows", [])
        if r.get("form_display_name") == FORM_NAME and _in_window(r)
    ]
    approved_rows = [
        r
        for r in pipeline_data.get("approved_visits", {}).get("rows", [])
        if r.get("form_display_name") == FORM_NAME and _in_window(r)
    ]

    visits_by_opp_flw = defaultdict(lambda: defaultdict(list))
    for r in hsd_rows:
        visits_by_opp_flw[r["opportunity_id"]][r["username"]].append(r)

    approved_counts = defaultdict(lambda: defaultdict(int))
    for r in approved_rows:
        approved_counts[r["opportunity_id"]][r["username"]] += 1

    period_start_iso = window_start.date().isoformat()
    period_end_iso = (window_end - timedelta(days=1)).date().isoformat()
    generated_at = datetime.now(timezone.utc).isoformat()

    opp_results = {}
    for opp_id in opp_ids:
        flws = []
        for username, visits in visits_by_opp_flw.get(opp_id, {}).items():
            indicators = compute_flw_indicators(visits)
            indicators["username"] = username
            indicators["total_approved_visits"] = approved_counts.get(opp_id, {}).get(username, 0)
            flws.append(indicators)

        opp_wda = WorkflowDataAccess(access_token=access_token, opportunity_id=opp_id)
        try:
            run = opp_wda.create_run(
                definition.id,
                opportunity_id=opp_id,
                period_start=period_start_iso,
                period_end=period_end_iso,
                initial_state={},
            )
            snapshot_payload = {
                "pipelines": {},
                "workers": [],
                "state": {
                    "flw_audit_report": {
                        "period_start": period_start_iso,
                        "period_end": period_end_iso,
                        "generated_at": generated_at,
                        "flws": flws,
                    }
                },
            }
            opp_wda.complete_run(run.id, snapshot_payload, run=run)
            opp_results[str(opp_id)] = {"run_id": run.id, "flw_count": len(flws), "status": "ready"}
        finally:
            opp_wda.close()

    return {"opportunities": opp_results, "period_start": period_start_iso, "period_end": period_end_iso}


TEMPLATE = {
    "key": "flw_weekly_audit_report",
    "name": "FLW Weekly Audit Report",
    "description": DEFINITION["description"],
    "icon": "fa-clipboard-list",
    "color": "orange",
    "multi_opp": True,
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schemas": PIPELINE_SCHEMAS,
    "supports_saved_runs": True,
    "snapshot_schema": SNAPSHOT_SCHEMA,
}
TEMPLATE["supports_default_run"] = True
