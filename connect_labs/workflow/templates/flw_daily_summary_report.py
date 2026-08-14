"""FLW Daily Summary Report — Program 217 ("CHC - NG - RCT - Aug 2026").

A much simpler sibling of flw_daily_indicator_report.py (Program 176): plain
per-FLW, per-opportunity, per-calendar-day service-delivery counts -- no
fraud/data-quality thresholds, no rate/percentage indicators. Computes 9
indicators (plus one bonus cross-check field) from "Health Service Delivery"
and "No Children Found" form submissions:

    1. total_households_registered
    2. total_children_registered
    3. total_health_service_delivery_visits
    4. total_approved_health_service_delivery_visits
    5. total_approved_no_children_found_visits
    6. total_children_muac_eligible
    7. total_children_muac_measured        (MUAC photo captured, not muac_cm != null)
    8. total_children_deworming_eligible
    9. total_children_deworming_photo_taken (deworming dose actually administered)
   (+) total_children_muac_value_recorded  (bonus -- cheap cross-check, not one
       of the 9 requested indicators)

See connect_labs/workflow/flw_daily_summary_compute.py for the pure
computation. Runs on a schedule (WorkflowSchedule, daily) via run_default
below, exactly like flw_daily_indicator_report.py: no interactive review
step, the run is created AND completed in the same call.

Data sources: two connect_csv pipelines --
  - hsd_visits: ALL statuses, ALL form types on the deliver unit (filters={}),
    for indicators #1-3 (which must NOT be restricted to approved visits).
  - approved_visits: status=approved only (filters={"status": ["approved"]}),
    ALL form types, for indicators #4-9.

Indicators #7 and #9 (MUAC-photo-captured / deworming-dose-administered) are
both plain scalar fields directly on the Health Service Delivery form, read
the same way as every other field here -- no CommCare HQ connection, no case
data, no extra fetch. Verified live against real program-217 data via a
Superset schema/sample-value export of this exact form (not guessed):
    MUAC photo:  form.muac_group.muac_display_group_2.muac_display_group_photo.muac_photo
                 (the raw attachment filename; non-empty = photo captured.
                 The sibling calculate field `muac_photo_link` was checked too
                 but is blank on every sampled form in this app build, so it
                 is not used here.)
    Deworming:   form.case.update.dw_dosage_date_time
                 (set when a dose was actually administered)
"""
from __future__ import annotations

PIPELINE_SCHEMAS = [
    {
        "alias": "hsd_visits",
        "name": "Health Service Delivery Visits (Daily Summary)",
        "description": (
            "Every form submission on the deliver unit, all statuses, with the fields the FLW "
            "Daily Summary Report's registration/visit-count indicators (#1-3) need."
        ),
        "schema": {
            "data_source": {"type": "connect_csv"},
            "grouping_key": "username",
            "terminal_stage": "visit_level",
            "filters": {},
            "fields": [
                {"name": "form_display_name", "path": "form.@name", "aggregation": "first"},
                {
                    "name": "hh_case_id",
                    "paths": ["form.additional_case_info.hh_case_id", "form.case.update.hh_case_id"],
                    "aggregation": "first",
                },
                {"name": "child_case_id", "path": "form.case.@case_id", "aggregation": "first"},
                {"name": "time_start", "path": "form.meta.timeStart", "aggregation": "first"},
            ],
        },
    },
    {
        "alias": "approved_visits",
        "name": "Approved Visits (Daily Summary)",
        "description": (
            "Every APPROVED form submission on the deliver unit, with the fields the FLW Daily "
            "Summary Report's approval/coverage indicators (#4-9) need."
        ),
        "schema": {
            "data_source": {"type": "connect_csv"},
            "grouping_key": "username",
            "terminal_stage": "visit_level",
            "filters": {"status": ["approved"]},
            "fields": [
                {"name": "form_display_name", "path": "form.@name", "aggregation": "first"},
                {"name": "time_start", "path": "form.meta.timeStart", "aggregation": "first"},
                {"name": "child_case_id", "path": "form.case.@case_id", "aggregation": "first"},
                {
                    "name": "childs_dob",
                    "path": "form.additional_case_info.childs_dob",
                    "aggregation": "first",
                    "description": "Used for the MUAC/deworming age-eligibility indicators (#6/#8).",
                },
                {
                    "name": "muac_cm",
                    "path": "form.case.update.soliciter_muac_cm",
                    "transform": "float",
                    "aggregation": "first",
                    "description": "MUAC measurement in cm -- bonus cross-check field only, distinct "
                    "from the photo-presence signal indicator #7 actually measures.",
                },
                {
                    "name": "muac_photo",
                    "path": "form.muac_group.muac_display_group_2.muac_display_group_photo.muac_photo",
                    "aggregation": "first",
                    "description": "Raw MUAC photo attachment filename -- non-empty means a photo was "
                    "captured this visit. Indicator #7's actual signal.",
                },
                {
                    "name": "dw_dosage_date_time",
                    "path": "form.case.update.dw_dosage_date_time",
                    "aggregation": "first",
                    "description": "Set when a deworming dose was actually administered this visit. "
                    "Indicator #9's actual signal.",
                },
            ],
        },
    },
]

DEFINITION = {
    "name": "FLW Daily Summary Report",
    "description": (
        "Program 217 (CHC - NG - RCT - Aug 2026) daily per-FLW service-delivery summary -- "
        "computed automatically every day. Plain counts only (households/children registered, "
        "HSD visits, approvals, MUAC/deworming eligibility and coverage) -- no fraud/data-quality "
        "thresholds, no interactive review (no statuses to assign)."
    ),
    "version": 1,
    "templateType": "flw_daily_summary_report",
    "statuses": [],
    "config": {
        "showSummaryCards": False,
        "showFilters": False,
    },
    "pipeline_sources": [],  # Populated at creation time from PIPELINE_SCHEMAS
    "snapshot_inputs": {"pipelines": [], "workers": False, "state_keys": ["flw_daily_summary"]},
}

SNAPSHOT_SCHEMA = {
    "version": 1,
    "keys": {
        "state.flw_daily_summary.date": "ISO date (Africa/Lagos calendar day) this run covers",
        "state.flw_daily_summary.generated_at": "ISO timestamp this run was computed",
        "state.flw_daily_summary.flws": "List of per-FLW daily summary indicator dicts for this opportunity+day",
    },
}

RENDER_CODE = """function WorkflowUI({ definition, instance, view }) {
    var report = (view.state && view.state.flw_daily_summary) || null;

    if (!view.isCompleted || !report) {
        return (
            <div className="p-6 text-gray-600">
                <p className="font-medium">No completed report yet for this opportunity.</p>
                <p className="text-sm mt-1">
                    This workflow is generated automatically by a daily schedule, not manually — check
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
                    {report.date} · generated {report.generated_at}
                </p>
            </div>
            <div className="text-sm text-gray-600">{flws.length} FLW(s) with activity this day</div>
            <div className="overflow-x-auto border rounded-lg">
                <table className="min-w-full text-sm">
                    <thead className="bg-gray-50">
                        <tr>
                            <th className="px-3 py-2 text-left font-semibold">FLW</th>
                            <th className="px-3 py-2 text-right font-semibold">HHs Registered</th>
                            <th className="px-3 py-2 text-right font-semibold">Children Registered</th>
                            <th className="px-3 py-2 text-right font-semibold">HSD Visits</th>
                            <th className="px-3 py-2 text-right font-semibold">Approved HSD Visits</th>
                            <th className="px-3 py-2 text-right font-semibold">Approved No-Children-Found</th>
                            <th className="px-3 py-2 text-right font-semibold">MUAC Eligible</th>
                            <th className="px-3 py-2 text-right font-semibold">MUAC Measured (Photo)</th>
                            <th className="px-3 py-2 text-right font-semibold">Deworming Eligible</th>
                            <th className="px-3 py-2 text-right font-semibold">Deworming Delivered</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y">
                        {flws.map(function (f, i) {
                            return (
                                <tr key={f.username || i}>
                                    <td className="px-3 py-2 font-mono text-xs">{f.username}</td>
                                    <td className="px-3 py-2 text-right">{f.total_households_registered}</td>
                                    <td className="px-3 py-2 text-right">{f.total_children_registered}</td>
                                    <td className="px-3 py-2 text-right">{f.total_health_service_delivery_visits}</td>
                                    <td className="px-3 py-2 text-right">
                                        {f.total_approved_health_service_delivery_visits}
                                    </td>
                                    <td className="px-3 py-2 text-right">
                                        {f.total_approved_no_children_found_visits}
                                    </td>
                                    <td className="px-3 py-2 text-right">{f.total_children_muac_eligible}</td>
                                    <td className="px-3 py-2 text-right">{f.total_children_muac_measured}</td>
                                    <td className="px-3 py-2 text-right">{f.total_children_deworming_eligible}</td>
                                    <td className="px-3 py-2 text-right">{f.total_children_deworming_photo_taken}</td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
        </div>
    );
}"""


def run_default(*, definition, access_token, request=None, window=None, **_):
    """Compute one WAT calendar day's FLW daily summary for every opportunity
    this (program-owned, multi-opp) definition spans, creating and completing
    one run per opportunity.

    Like flw_daily_indicator_report's run_default, the run is created and
    completed in the same call -- unattended daily snapshot, no reviewer in
    the loop. ``window`` overrides the default "yesterday, Africa/Lagos" day
    for backfills; it is a (window_start, window_end) UTC half-open pair.

    Every indicator here comes from the two connect_csv pipelines above --
    no CommCare HQ token, no extra fetch. This is deliberately simpler than
    flw_daily_indicator_report.py (Program 176), which needs a cchq_cases
    pipeline for building counts.
    """
    from collections import defaultdict
    from datetime import datetime, timedelta, timezone

    from connect_labs.workflow.data_access import WorkflowDataAccess
    from connect_labs.workflow.flw_audit_compute import WAT_OFFSET, wat_date
    from connect_labs.workflow.flw_daily_summary_compute import compute_flw_daily_summary

    if window is not None:
        window_start, window_end = window
    else:
        now = datetime.now(timezone.utc)
        today_wat = (now + WAT_OFFSET).date()
        yesterday_wat = today_wat - timedelta(days=1)
        window_start = (
            datetime(yesterday_wat.year, yesterday_wat.month, yesterday_wat.day, tzinfo=timezone.utc) - WAT_OFFSET
        )
        window_end = window_start + timedelta(days=1)

    opp_ids = definition.opportunity_ids or ([definition.opportunity_id] if definition.opportunity_id else [])
    if not opp_ids:
        raise ValueError("flw_daily_summary_report requires at least one opportunity on the definition")

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

    hsd_rows = [r for r in pipeline_data.get("hsd_visits", {}).get("rows", []) if _in_window(r)]
    approved_rows = [r for r in pipeline_data.get("approved_visits", {}).get("rows", []) if _in_window(r)]

    hsd_by_opp_flw = defaultdict(lambda: defaultdict(list))
    for r in hsd_rows:
        hsd_by_opp_flw[r["opportunity_id"]][r["username"]].append(r)

    approved_by_opp_flw = defaultdict(lambda: defaultdict(list))
    for r in approved_rows:
        approved_by_opp_flw[r["opportunity_id"]][r["username"]].append(r)

    date_iso = wat_date(window_start)
    generated_at = datetime.now(timezone.utc).isoformat()

    opp_results = {}
    for opp_id in opp_ids:
        opp_hsd = hsd_by_opp_flw.get(opp_id, {})
        opp_approved = approved_by_opp_flw.get(opp_id, {})
        usernames = set(opp_hsd.keys()) | set(opp_approved.keys())

        flws = []
        for username in usernames:
            indicators = compute_flw_daily_summary(opp_hsd.get(username, []), opp_approved.get(username, []))
            indicators["username"] = username
            flws.append(indicators)

        opp_wda = WorkflowDataAccess(access_token=access_token, opportunity_id=opp_id)
        try:
            run = opp_wda.create_run(
                definition.id,
                opportunity_id=opp_id,
                period_start=date_iso,
                period_end=date_iso,
                initial_state={},
            )
            snapshot_payload = {
                "pipelines": {},
                "workers": [],
                "state": {
                    "flw_daily_summary": {
                        "date": date_iso,
                        "generated_at": generated_at,
                        "flws": flws,
                    }
                },
            }
            opp_wda.complete_run(run.id, snapshot_payload, run=run)
            opp_results[str(opp_id)] = {"run_id": run.id, "flw_count": len(flws), "status": "ready"}
        finally:
            opp_wda.close()

    return {"opportunities": opp_results, "date": date_iso}


TEMPLATE = {
    "key": "flw_daily_summary_report",
    "name": "FLW Daily Summary Report",
    "description": (
        "Program 217 (CHC - NG - RCT - Aug 2026) daily per-FLW service-delivery summary -- "
        "computed automatically every day. Plain counts only (households/children registered, "
        "HSD visits, approvals, MUAC/deworming eligibility and coverage) -- no fraud/data-quality "
        "thresholds, no interactive review (no statuses to assign)."
    ),
    "icon": "fa-clipboard-list",
    "color": "teal",
    "multi_opp": True,
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schemas": PIPELINE_SCHEMAS,
    "supports_saved_runs": True,
    "snapshot_schema": SNAPSHOT_SCHEMA,
    "supports_default_run": True,
}
