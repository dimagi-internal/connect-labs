"""FLW Daily Summary Report — RUTF variant (Program 263, "RUTF - NG - Program 1
- Sept 26").

Sibling of flw_daily_summary_report.py (Program 217's CHC version) for a
different app/case model. RUTF has no work-area/ward concept at all (no
CommCare HQ token needed here, unlike the CHC version) -- but it DOES have a
households-vs-children split and a MUAC/visit-count concept, computed from a
single "visits" connect_csv pipeline (any status -- approved-only subsets are
derived in Python, mirroring Program 217's hsd_visits/approved_visits split
from one fetch instead of two):

    1. total_households_registered   ("Register a New Family" form, distinct households)
    2. total_children_registered     ("Register a New Family" form, summed under_five_children_count)
    3. total_children_screened       ("Screening " form, every submission)
    4. total_sam_children_registered ("Screening " form, rutf_enrollment=yes)
    5. total_children_muac_measured  ("Screening " form, MUAC photo captured)
    6. total_visits                  ("Visit Form" submissions, any status)
    7. total_sam_followup_visits     ("Visit Form" submissions, approved only)

See connect_labs/workflow/flw_daily_summary_compute_rutf.py for the pure
computation, and that module's docstring for exactly how these fields were
derived from the RUTF app's own question list (Household Management ->
Register a New Family, Initial Screening -> Screening). #1/#2 are currently
verified-correct-but-silent-zero: as of 2026-09-16, no "Register a New Family"
submission has ever appeared in Connect's own visit data for the live
opportunity (2230), even after a full, forced, non-stale cache refresh that
DID surface 113 real "Screening " visits Labs had been missing (a separate,
now-fixed staleness bug -- see the cache note below). So #1/#2 reading 0 is
not (as of this writing) a sign either calculation is wrong; it is Connect
not creating a "visit" for that specific form at all, for a reason outside
this pipeline. #3-7 are unaffected and read real numbers.

CACHE STALENESS, separate from the above: this template's numbers can only
ever be as fresh as WorkflowDataAccess.get_pipeline_data's underlying raw
visit cache. That cache's own validity check normally compares its cached row
count against Connect's live "expected" count for the opportunity -- but a
Celery-run scheduled fire (this template's normal trigger, see run_default
below) does not have the request context that check needs, so it falls back
to trusting whatever is cached, however stale, as long as it has not hit its
own TTL. Observed live 2026-09-16: a cache last populated when the opportunity
had ~9 visits was still being served as "valid" over a day later, once the
opportunity actually had 112+ -- silently undercounting every indicator in
this file, not just #1/#2, until someone forced a refresh (workflow_ensure_
visit_cache, or opening the workflow's page live). Nothing in THIS template
can fix that on its own; flagging it here so a future reader chasing
"numbers look low" checks cache freshness before re-auditing the arithmetic.

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
        "alias": "visits",
        "name": "Visits (RUTF Daily Summary)",
        "description": (
            "Every form submission on the deliver unit, ANY status, with the fields the RUTF FLW "
            "Daily Summary Report needs: household/child registrations, SAM enrollments, MUAC "
            "photos, and follow-up visits. Status filtering is done in Python per indicator, "
            "mirroring Program 217's hsd_visits/approved_visits split but from one fetch."
        ),
        "schema": {
            "data_source": {"type": "connect_csv"},
            "grouping_key": "username",
            "terminal_stage": "visit_level",
            "filters": {},
            "fields": [
                {"name": "form_display_name", "path": "form.@name", "aggregation": "first"},
                {
                    "name": "status",
                    "path": "status",
                    "aggregation": "first",
                    "description": "Raw visit-cache column, e.g. approved/pending/rejected/over_limit. Used "
                    "to derive the approved-only subset in Python; NOT pre-filtered here so an any-status "
                    "total (Total Visits) is also available from the same fetch.",
                },
                {"name": "time_start", "path": "form.meta.timeStart", "aggregation": "first"},
                {
                    "name": "entity_id",
                    "path": "entity_id",
                    "aggregation": "first",
                    "description": "The CommCare case this visit is against -- a raw column on the visit "
                    "cache itself (not a form.* path). For 'Register a New Family' this is the HOUSEHOLD "
                    "case (that module's deliver-unit case type), NOT a child -- that form registers a "
                    "household and, via an internal repeat group, one or more children in a single "
                    "submission, so distinct entity_id counts households, not children. For 'Screening ', "
                    "this is the CHILD case; null when the child was screened OUT (rutf_enrollment=no), "
                    "since no case is opened in that case.",
                },
                {
                    "name": "rutf_enrollment",
                    "path": "form.screening_outcome.rutf_enrollment",
                    "aggregation": "first",
                    "description": "Set on the Screening form only. 'yes' = child diagnosed SAM and "
                    "enrolled into RUTF/OTP treatment. Absent/blank on every other form.",
                },
                {
                    "name": "muac_photo",
                    "path": "form.anthropometric_appetite.muac_measurement.muac_photo",
                    "aggregation": "first",
                    "description": "MUAC photo attachment filename captured at Screening (the child's "
                    "initial visit). Non-empty means a photo was taken. The follow-up Visit Form captures "
                    "MUAC again at a different nested path -- not read here; scoped to Screening-time MUAC.",
                },
                {
                    "name": "household_children_count",
                    "path": "form.household_form.under_five_children_count",
                    "aggregation": "first",
                    "description": "Set on the Register a New Family form only -- the FLW's own answer to "
                    "'How many children in this family are between 6 months and 5 years of age?', saved to "
                    "the household case. Summed (not deduped) across a day's household registrations to get "
                    "total_children_registered, since a single submission's repeat group can register "
                    "several children that this pipeline's one-row-per-form model can't otherwise count.",
                },
            ],
        },
    },
]

DEFINITION = {
    "name": "FLW Daily Summary Report (RUTF)",
    "description": (
        "Program 263 (RUTF - NG - Program 1 - Sept 26) daily per-FLW service-delivery summary -- "
        "computed automatically every day. Plain counts only (households/children registered, SAM "
        "enrollment, MUAC captured, visits, SAM follow-up visits) -- no fraud/data-quality "
        "thresholds, no interactive review (no statuses to assign)."
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
                            <th className="px-3 py-2 text-right font-semibold">Households Registered</th>
                            <th className="px-3 py-2 text-right font-semibold">Children Registered</th>
                            <th className="px-3 py-2 text-right font-semibold">Children Screened</th>
                            <th className="px-3 py-2 text-right font-semibold">SAM Children Registered</th>
                            <th className="px-3 py-2 text-right font-semibold">MUAC Measured</th>
                            <th className="px-3 py-2 text-right font-semibold">Total Visits</th>
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
                                    <td className="px-3 py-2 text-right">{f.total_households_registered}</td>
                                    <td className="px-3 py-2 text-right">{f.total_children_registered}</td>
                                    <td className="px-3 py-2 text-right">{f.total_children_screened}</td>
                                    <td className="px-3 py-2 text-right">{f.total_sam_children_registered}</td>
                                    <td className="px-3 py-2 text-right">{f.total_children_muac_measured}</td>
                                    <td className="px-3 py-2 text-right">{f.total_visits}</td>
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
    every indicator here comes from the single `visits` connect_csv pipeline
    above (any status; approved-only subsets derived in
    compute_flw_daily_summary_rutf), so this function is considerably smaller.
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
        per_opp_meta = pipeline_data.get("visits", {}).get("metadata", {}).get("per_opp", {})
        for opp_id in opp_ids:
            meta = per_opp_meta.get(str(opp_id)) or {}
            if meta.get("error"):
                warnings.append(f"visits unavailable for opp {opp_id}: {meta['error']}")
            elif meta.get("raw_fetch_anomaly"):
                warnings.append(f"visits short-read anomaly for opp {opp_id}: {meta['raw_fetch_anomaly']}")
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

    all_rows = pipeline_data.get("visits", {}).get("rows", [])
    windowed_rows = [r for r in all_rows if _in_window(r)]

    rows_by_opp_flw = defaultdict(lambda: defaultdict(list))
    for r in windowed_rows:
        rows_by_opp_flw[r["opportunity_id"]][r["username"]].append(r)

    date_iso = wat_date(window_start)
    generated_at = datetime.now(timezone.utc).isoformat()

    opp_results = {}
    for opp_id in opp_ids:
        opp_rows = rows_by_opp_flw.get(opp_id, {})
        roster = {w["username"]: w for w in roster_by_opp.get(opp_id, []) if w.get("username")}
        usernames = set(opp_rows.keys()) | set(roster.keys())

        flws = []
        for username in usernames:
            indicators = compute_flw_daily_summary_rutf(opp_rows.get(username, []))
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
        "computed automatically every day. Plain counts only (households/children registered, SAM "
        "enrollment, MUAC captured, visits, SAM follow-up visits) -- no fraud/data-quality "
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
