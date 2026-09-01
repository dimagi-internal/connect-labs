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

Three more fields ride along on each FLW's dict, but are NOT day-specific
indicators -- current-status info, only ever useful as "latest known value",
so unlike #1-9 above there's no need to backfill them into older runs:
  - suspended / suspension_date: Connect's own worker-suspension flag, from
    WorkflowDataAccess.get_workers() (the /user_data/ export -- no CCHQ
    token, same access_token as everything else here).
  - Every FLW on the opportunity's Connect roster gets a row now, active or
    not (get_workers() again) -- previously only FLWs with hsd/approved
    activity that day appeared at all.
  - work_areas_left: count of this FLW's still-open (not closed) work-area
    cases, from a cchq_cases pull over CommCare's work-area case type,
    joined via the commcare_userid captured on approved_visits rows (added
    2026-08-26 for report 13003's grey-out, unused there so far). This is
    the one piece that DOES need a CommCare HQ token -- see run_default's
    docstring for how that's threaded through headlessly, mirroring
    flw_daily_indicator_report.py (Program 176)'s identical pattern.
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
                {
                    "name": "wa_case_id",
                    "paths": ["form.work_area_info.wa_caseid", "form.wa_case_id"],
                    "aggregation": "first",
                    "description": "Which work area this visit happened in. TWO paths on purpose -- "
                    "the No Children Found form stores it at form.wa_case_id, a single path undercounts.",
                },
                {
                    "name": "commcare_userid",
                    "path": "form.meta.userID",
                    "aggregation": "first",
                    "description": "The CommCare user UUID behind this Connect username -- joins a "
                    "visit to a work-area case's owner_id. Used to compute work_areas_left.",
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
        "state.flw_daily_summary.flws": (
            "List of per-FLW daily summary indicator dicts for this opportunity+day. Every FLW on the "
            "opportunity's roster appears (not just ones with activity). Also carries name, and where "
            "available: suspended, suspension_date, work_areas_left -- current-status fields, not "
            "day-specific indicators, so only present from whenever this was added onward."
        ),
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
            <div className="text-sm text-gray-600">{flws.length} FLW(s) on roster</div>
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
                            <th className="px-3 py-2 text-right font-semibold">WAs Left</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y">
                        {flws.map(function (f, i) {
                            return (
                                <tr key={f.username || i} className={f.suspended ? "bg-red-50" : ""}>
                                    <td className="px-3 py-2 font-mono text-xs">
                                        {f.name || f.username}
                                        {f.suspended && (
                                            <span className="ml-2 px-1.5 py-0.5 rounded text-xs bg-red-100 text-red-700">
                                                Suspended
                                            </span>
                                        )}
                                    </td>
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
                                    <td className="px-3 py-2 text-right">
                                        {f.work_areas_left === undefined ? "—" : f.work_areas_left}
                                    </td>
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
    """Compute one WAT calendar day's FLW daily summary for every opportunity
    this (program-owned, multi-opp) definition spans, creating and completing
    one run per opportunity.

    Like flw_daily_indicator_report's run_default, the run is created and
    completed in the same call -- unattended daily snapshot, no reviewer in
    the loop. ``window`` overrides the default "yesterday, Africa/Lagos" day
    for backfills; it is a (window_start, window_end) UTC half-open pair.

    Indicators #1-9 come from the two connect_csv pipelines above -- no
    CommCare HQ token, no extra fetch. Three more fields (module docstring
    has the full rationale) ride along on each FLW dict but aren't
    day-specific: the full opportunity roster (so every FLW gets a row, not
    just ones with activity today), suspended/suspension_date, and
    work_areas_left.

    work_areas_left is the one piece needing a CommCare HQ token, fetched the
    same way flw_daily_indicator_report.py (Program 176) fetches building
    counts: directly via fetch_cchq_cases_as_visit_dicts, NOT through
    WorkflowDataAccess.get_pipeline_data (that generic path never threads a
    cchq_access_token down to the cchq_cases fetcher, so it would raise
    CCHQHeadlessError every time this runs unattended). cchq_access_token is
    optional and best-effort -- the scheduler mints one per run via
    get_valid_cchq_access_token(owner) when available; if it's missing,
    expired, or the fetch otherwise fails, work_areas_left is just absent
    from every FLW's dict for that opp/day rather than failing the run.
    """
    import logging
    from collections import defaultdict
    from datetime import datetime, timedelta, timezone

    from connect_labs.labs.analysis.backends.sql.cchq_cases_fetcher import fetch_cchq_cases_as_visit_dicts
    from connect_labs.labs.analysis.config import DataSourceConfig
    from connect_labs.workflow.data_access import WorkflowDataAccess
    from connect_labs.workflow.flw_audit_compute import WAT_OFFSET, wat_date
    from connect_labs.workflow.flw_daily_summary_compute import compute_flw_daily_summary

    logger = logging.getLogger(__name__)

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
        roster_by_opp = {}
        for opp_id in opp_ids:
            try:
                roster_by_opp[opp_id] = fetch_wda.get_workers(opp_id)
            except Exception:
                logger.exception(
                    "flw_daily_summary_report: failed to fetch worker roster for opp %s "
                    "(suspended flag and roster-only FLW rows will be unavailable for this opp/day)",
                    opp_id,
                )
                roster_by_opp[opp_id] = []
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

    all_hsd_rows = pipeline_data.get("hsd_visits", {}).get("rows", [])
    all_approved_rows = pipeline_data.get("approved_visits", {}).get("rows", [])
    hsd_rows = [r for r in all_hsd_rows if _in_window(r)]
    approved_rows = [r for r in all_approved_rows if _in_window(r)]

    hsd_by_opp_flw = defaultdict(lambda: defaultdict(list))
    for r in hsd_rows:
        hsd_by_opp_flw[r["opportunity_id"]][r["username"]].append(r)

    approved_by_opp_flw = defaultdict(lambda: defaultdict(list))
    for r in approved_rows:
        approved_by_opp_flw[r["opportunity_id"]][r["username"]].append(r)

    # username -> CommCare HQ user UUID, from EVERY approved_visits row this
    # pull returned (not just today's window) -- an FLW with zero activity
    # today can still have this mapping from an earlier visit.
    commcare_userid_by_opp_username = {}
    for r in all_approved_rows:
        ccuid = r.get("commcare_userid")
        if not ccuid:
            continue
        commcare_userid_by_opp_username.setdefault((r["opportunity_id"], r["username"]), ccuid)

    # Open (not closed) work-area case count per opp, keyed by CommCare
    # owner_id. An opp missing from this dict means the fetch failed or no
    # cchq_access_token was available -- work_areas_left is left off every
    # FLW dict for that opp rather than defaulting to a misleading 0.
    work_area_data_source = DataSourceConfig(type="cchq_cases", case_type="work-area")
    open_wa_counts_by_opp = {}
    for opp_id in opp_ids:
        try:
            wa_rows = fetch_cchq_cases_as_visit_dicts(
                request, work_area_data_source, access_token, opp_id, cchq_access_token=cchq_access_token
            )
        except Exception:
            logger.exception(
                "flw_daily_summary_report: failed to fetch work-area cases for opp %s "
                "(work_areas_left will be unavailable for this opp/day)",
                opp_id,
            )
            continue
        counts = defaultdict(int)
        for row in wa_rows:
            case = row.get("form_json", {}).get("case", {})
            owner_id = case.get("owner_id")
            if owner_id and not case.get("closed", False):
                counts[owner_id] += 1
        open_wa_counts_by_opp[opp_id] = counts

    date_iso = wat_date(window_start)
    generated_at = datetime.now(timezone.utc).isoformat()

    opp_results = {}
    for opp_id in opp_ids:
        opp_hsd = hsd_by_opp_flw.get(opp_id, {})
        opp_approved = approved_by_opp_flw.get(opp_id, {})
        roster = {w["username"]: w for w in roster_by_opp.get(opp_id, []) if w.get("username")}
        usernames = set(opp_hsd.keys()) | set(opp_approved.keys()) | set(roster.keys())
        wa_counts = open_wa_counts_by_opp.get(opp_id)

        flws = []
        for username in usernames:
            indicators = compute_flw_daily_summary(opp_hsd.get(username, []), opp_approved.get(username, []))
            indicators["username"] = username

            worker = roster.get(username)
            if worker is not None:
                indicators["name"] = worker.get("name")
                if "suspended" in worker:
                    indicators["suspended"] = worker["suspended"]
                if worker.get("suspension_date"):
                    indicators["suspension_date"] = worker["suspension_date"]

            ccuid = commcare_userid_by_opp_username.get((opp_id, username))
            if ccuid is not None and wa_counts is not None:
                indicators["work_areas_left"] = wa_counts.get(ccuid, 0)

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
