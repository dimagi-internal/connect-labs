"""FLW Daily Summary Report — RUTF variant (Program 263, "RUTF - NG - Program 1
- Sept 26").

Sibling of flw_daily_summary_report.py (Program 217's CHC version) for a
different app/case model. RUTF has no households-vs-children registration
split, no MUAC/deworming age-eligibility split, and no work-area/ward concept
(no CommCare HQ token needed at all here, unlike the CHC version) -- so this
computes 3 indicators from a single "approved visits" connect_csv pipeline:

    1. total_children_registered      ("Register a New Family" form)
    2. total_sam_children_registered  ("Screening " form, rutf_enrollment=yes)
    3. total_sam_followup_visits      ("Visit Form" submissions)

See connect_labs/workflow/flw_daily_summary_compute_rutf.py for the pure
computation, and that module's docstring for exactly how these 3 fields were
verified against real submitted RUTF data (not guessed from the blank app
schema) -- via an existing pipeline built for the RUTF Internal Test
opportunity (id 2092), "RUTF FLW Service Delivery Indicators" (pipeline id
19854).

Runs on a schedule (WorkflowSchedule, daily) via run_default below, exactly
like flw_daily_summary_report.py: no interactive review step, the run is
created AND completed in the same call.

IMPORTANT: the snapshot's state key is deliberately still "flw_daily_summary"
(not "flw_daily_summary_rutf") -- connect_labs/workflow/views.py's
flw_daily_summary_history_api reads that exact literal key regardless of which
definition_id it's asked about, and the RUTF FLW day-by-day report (the RUTF
clone of workflow 13003) reads this workflow's history through that same
shared endpoint. Renaming the key here would silently break that without
touching a single line of view code to notice.
"""

from __future__ import annotations

PIPELINE_SCHEMAS = [
    {
        "alias": "approved_visits",
        "name": "Approved Visits (RUTF Daily Summary)",
        "description": (
            "Every APPROVED form submission on the deliver unit, with the fields the RUTF FLW "
            "Daily Summary Report needs: registrations, SAM enrollments, and follow-up visits."
        ),
        "schema": {
            "data_source": {"type": "connect_csv"},
            "grouping_key": "username",
            "terminal_stage": "visit_level",
            "filters": {"status": ["approved"]},
            "fields": [
                {"name": "form_display_name", "path": "form.@name", "aggregation": "first"},
                {"name": "time_start", "path": "form.meta.timeStart", "aggregation": "first"},
                {
                    "name": "entity_id",
                    "path": "entity_id",
                    "aggregation": "first",
                    "description": "The CommCare case this visit is against -- a raw column on the "
                    "visit cache itself (not a form.* path). Null on a Screening submission that "
                    "screened a child OUT (rutf_enrollment=no), since no case is opened in that case.",
                },
                {
                    "name": "rutf_enrollment",
                    "path": "form.screening_outcome.rutf_enrollment",
                    "aggregation": "first",
                    "description": "Set on the Screening form only. 'yes' = child diagnosed SAM and "
                    "enrolled into RUTF/OTP treatment. Absent/blank on every other form.",
                },
            ],
        },
    },
]

DEFINITION = {
    "name": "FLW Daily Summary Report (RUTF)",
    "description": (
        "Program 263 (RUTF - NG - Program 1 - Sept 26) daily per-FLW service-delivery summary -- "
        "computed automatically every day. Plain counts only (children registered, SAM enrollment, "
        "SAM follow-up visits) -- no fraud/data-quality thresholds, no interactive review (no "
        "statuses to assign)."
    ),
    "version": 1,
    "templateType": "flw_daily_summary_report_rutf",
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
            "opportunity's roster appears (not just ones with activity)."
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
                            <th className="px-3 py-2 text-right font-semibold">Children Registered</th>
                            <th className="px-3 py-2 text-right font-semibold">SAM Children Registered</th>
                            <th className="px-3 py-2 text-right font-semibold">SAM Follow-up Visits</th>
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
                                    <td className="px-3 py-2 text-right">{f.total_children_registered}</td>
                                    <td className="px-3 py-2 text-right">{f.total_sam_children_registered}</td>
                                    <td className="px-3 py-2 text-right">{f.total_sam_followup_visits}</td>
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
    """Compute one WAT calendar day's RUTF FLW daily summary for every
    opportunity this (program-owned, multi-opp) definition spans, creating and
    completing one run per opportunity.

    Like flw_daily_summary_report's run_default (Program 217), the run is
    created and completed in the same call -- unattended daily snapshot, no
    reviewer in the loop. ``window`` overrides the default "yesterday,
    Africa/Lagos" day for backfills; it is a (window_start, window_end) UTC
    half-open pair.

    Unlike the CHC version, there is no CommCare HQ token dependency at all --
    every indicator here comes from the single approved_visits connect_csv
    pipeline above, so this function is considerably smaller.
    """
    import logging
    from collections import defaultdict
    from datetime import datetime, timedelta, timezone

    from connect_labs.workflow.data_access import WorkflowDataAccess
    from connect_labs.workflow.flw_audit_compute import WAT_OFFSET, wat_date
    from connect_labs.workflow.flw_daily_summary_compute_rutf import compute_flw_daily_summary_rutf

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
        raise ValueError("flw_daily_summary_report_rutf requires at least one opportunity on the definition")

    # Collected across the pipeline fetch below and returned as result["errors"]
    # -- run_scheduled_workflow (tasks.py) already reads that key on ANY
    # template's return value and writes it into the schedule's last_error even
    # when the run overall succeeds (last_status stays "ok"). Same treatment as
    # flw_daily_summary_report.py (Program 217).
    warnings: list[str] = []

    if definition.program_id:
        fetch_wda = WorkflowDataAccess(access_token=access_token, program_id=definition.program_id)
    else:
        fetch_wda = WorkflowDataAccess(access_token=access_token, opportunity_id=opp_ids[0])
    try:
        pipeline_data = fetch_wda.get_pipeline_data(definition.id, opportunity_id=opp_ids[0])
        per_opp_meta = pipeline_data.get("approved_visits", {}).get("metadata", {}).get("per_opp", {})
        for opp_id in opp_ids:
            meta = per_opp_meta.get(str(opp_id)) or {}
            if meta.get("error"):
                warnings.append(f"approved_visits unavailable for opp {opp_id}: {meta['error']}")
            elif meta.get("raw_fetch_anomaly"):
                warnings.append(f"approved_visits short-read anomaly for opp {opp_id}: {meta['raw_fetch_anomaly']}")
        roster_by_opp = {}
        for opp_id in opp_ids:
            try:
                roster_by_opp[opp_id] = fetch_wda.get_workers(opp_id)
            except Exception as exc:
                logger.exception(
                    "flw_daily_summary_report_rutf: failed to fetch worker roster for opp %s "
                    "(suspended flag and roster-only FLW rows will be unavailable for this opp/day)",
                    opp_id,
                )
                roster_by_opp[opp_id] = []
                warnings.append(f"worker roster unavailable for opp {opp_id}: {exc}")
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

    all_approved_rows = pipeline_data.get("approved_visits", {}).get("rows", [])
    approved_rows = [r for r in all_approved_rows if _in_window(r)]

    approved_by_opp_flw = defaultdict(lambda: defaultdict(list))
    for r in approved_rows:
        approved_by_opp_flw[r["opportunity_id"]][r["username"]].append(r)

    date_iso = wat_date(window_start)
    generated_at = datetime.now(timezone.utc).isoformat()

    opp_results = {}
    for opp_id in opp_ids:
        opp_approved = approved_by_opp_flw.get(opp_id, {})
        roster = {w["username"]: w for w in roster_by_opp.get(opp_id, []) if w.get("username")}
        usernames = set(opp_approved.keys()) | set(roster.keys())

        flws = []
        for username in usernames:
            indicators = compute_flw_daily_summary_rutf(opp_approved.get(username, []))
            indicators["username"] = username

            worker = roster.get(username)
            if worker is not None:
                indicators["name"] = worker.get("name")
                if "suspended" in worker:
                    indicators["suspended"] = worker["suspended"]
                if worker.get("suspension_date"):
                    indicators["suspension_date"] = worker["suspension_date"]

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

    return {"opportunities": opp_results, "date": date_iso, "errors": warnings}


TEMPLATE = {
    "key": "flw_daily_summary_report_rutf",
    "name": "FLW Daily Summary Report (RUTF)",
    "description": (
        "Program 263 (RUTF - NG - Program 1 - Sept 26) daily per-FLW service-delivery summary -- "
        "computed automatically every day. Plain counts only (children registered, SAM enrollment, "
        "SAM follow-up visits) -- no fraud/data-quality thresholds, no interactive review (no "
        "statuses to assign)."
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
