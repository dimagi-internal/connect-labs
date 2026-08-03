"""FLW Daily Indicator Report — Program 176 (CHC PRE-RCT Nigeria).

Workflow 1 of a two-workflow pair (see flw_daily_indicator_table.py, "Workflow
2"). Computes a fixed set of daily fraud/data-quality indicators per
opportunity per FLW per calendar day, from raw "Health Service Delivery" form
submissions plus each work area's CommCare HQ case (for building counts) --
not the threshold/flag logic or the table itself, just the computed RAW
indicator values, structured so a second workflow can read this one's history
cheaply (one query per definition across many daily runs) and apply
(easily-retunable) thresholds there. This mirrors flw_weekly_audit_report.py /
flw_audit_trend_dashboard.py exactly, at daily instead of weekly granularity.

Runs on a schedule (WorkflowSchedule, daily, hour=23 UTC = ~midnight Africa/
Lagos, no DST) via run_default below. No interactive review step: run_default
both computes the day's indicators AND completes the run in one atomic pass
(there's no human "Mark Run Complete" click in this template's lifecycle).

Data sources: a connect_csv pipeline reading "Health Service Delivery" form
submissions (same shape as flw_weekly_audit_report.py's hsd_visits, plus extra
fields this report needs: childs_dob, child_name, dw_child_unwell_today,
diarrhea_last_month), and a cchq_cases pipeline over work-area cases (for
building_count, indicator #2's denominator). This is a NEW pipeline, not a
reuse of the weekly report's hsd_visits pipeline -- that one is live under the
shipped weekly report and doesn't need these fields.
"""
from __future__ import annotations

PIPELINE_SCHEMAS = [
    {
        "alias": "hsd_visits",
        "name": "Health Service Delivery Visits (Daily Indicators)",
        "description": (
            "Every Health Service Delivery form submission, all statuses, with GPS/timing/MUAC/"
            "demographic/household fields extracted for the FLW daily indicator computation."
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
                {
                    "name": "hh_case_id",
                    "paths": ["form.additional_case_info.hh_case_id", "form.case.update.hh_case_id"],
                    "aggregation": "first",
                },
                {"name": "child_case_id", "path": "form.case.@case_id", "aggregation": "first"},
                {"name": "wa_caseid", "path": "form.case.update.wa_caseid", "aggregation": "first"},
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
                    "name": "received_any_vaccine",
                    "path": "form.case.update.received_any_vaccine",
                    "aggregation": "first",
                    "description": "'yes'/'no' -- whether the child received a vaccine this visit.",
                },
                {
                    "name": "child_name",
                    "path": "form.additional_case_info.child_name",
                    "aggregation": "first",
                },
                {
                    "name": "childs_dob",
                    "path": "form.additional_case_info.childs_dob",
                    "aggregation": "first",
                    "description": "Used for the duplicate-child-age indicator -- the same DOB turning up "
                    "under two or more different households the same day.",
                },
                {
                    "name": "dw_child_unwell_today",
                    "path": "form.dw_group.dw_child_unwell_today",
                    "aggregation": "first",
                    "description": "'Does your child have breathing difficulty, vomiting, diarrhea, or "
                    "high body temperature today?' -- used for the straight-lining indicator.",
                },
                {
                    "name": "diarrhea_last_month",
                    "path": "form.ors_group.diarrhea_last_month",
                    "aggregation": "first",
                    "description": "'Did your child have diarrhea in the last month?' -- used for the "
                    "straight-lining indicator.",
                },
            ],
        },
    },
    {
        "alias": "work_areas",
        "name": "CHC Work Areas (Building Counts)",
        "description": "One row per work-area case (CommCare HQ case_type=work-area), for the "
        "households-per-building indicator's denominator.",
        "schema": {
            "data_source": {"type": "cchq_cases", "case_type": "work-area"},
            "grouping_key": "entity_id",
            "terminal_stage": "visit_level",
            "filters": {},
            "fields": [
                {
                    "name": "building_count",
                    "path": "case.properties.building_count",
                    "transform": "float",
                    "aggregation": "first",
                    "description": "WA building count",
                },
            ],
        },
    },
]

DEFINITION = {
    "name": "FLW Daily Indicator Report",
    "description": (
        "Program 176 (CHC PRE-RCT Nigeria) daily per-FLW fraud/data-quality indicators -- computed "
        "automatically every day. Raw indicator values only; see FLW Daily Indicator Table for the "
        "14-day view with thresholds and the roll-up flag. Not an interactive review (no statuses "
        "to assign)."
    ),
    "version": 1,
    "templateType": "flw_daily_indicator_report",
    "statuses": [],
    "config": {
        "showSummaryCards": False,
        "showFilters": False,
    },
    "pipeline_sources": [],  # Populated at creation time from PIPELINE_SCHEMAS
    "snapshot_inputs": {"pipelines": [], "workers": False, "state_keys": ["flw_daily_indicators"]},
}

SNAPSHOT_SCHEMA = {
    "version": 1,
    "keys": {
        "state.flw_daily_indicators.date": "ISO date (Africa/Lagos calendar day) this run covers",
        "state.flw_daily_indicators.generated_at": "ISO timestamp this run was computed",
        "state.flw_daily_indicators.flws": "List of per-FLW raw daily indicator dicts for this opportunity+day",
    },
}

RENDER_CODE = """function WorkflowUI({ definition, instance, view }) {
    var report = (view.state && view.state.flw_daily_indicators) || null;

    if (!view.isCompleted || !report) {
        return (
            <div className="p-6 text-gray-600">
                <p className="font-medium">No completed report yet for this opportunity.</p>
                <p className="text-sm mt-1">
                    This workflow is generated automatically by a daily schedule, not manually — check
                    Labs Admin → Scheduled Workflows if you expected data here. To see the 14-day view
                    with thresholds, open the FLW Daily Indicator Table workflow instead.
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
                            <th className="px-3 py-2 text-right font-semibold">HSD Forms</th>
                            <th className="px-3 py-2 text-right font-semibold">Unique Work Areas</th>
                            <th className="px-3 py-2 text-right font-semibold">Daily Span (min)</th>
                            <th className="px-3 py-2 text-right font-semibold">Peak HHs/Building</th>
                            <th className="px-3 py-2 text-right font-semibold">HHs w/ 4+ Children</th>
                            <th className="px-3 py-2 text-right font-semibold">Gaps &lt;2min</th>
                            <th className="px-3 py-2 text-right font-semibold">% Vaccine Yes</th>
                            <th className="px-3 py-2 text-right font-semibold">GPS Repeat %</th>
                            <th className="px-3 py-2 text-right font-semibold">Dup. Names</th>
                            <th className="px-3 py-2 text-right font-semibold">Dup. Ages</th>
                            <th className="px-3 py-2 text-right font-semibold">MUAC Repetition %</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y">
                        {flws.map(function (f, i) {
                            return (
                                <tr key={f.username || i}>
                                    <td className="px-3 py-2 font-mono text-xs">{f.username}</td>
                                    <td className="px-3 py-2 text-right">{f.total_forms}</td>
                                    <td className="px-3 py-2 text-right">{f.unique_work_areas_count}</td>
                                    <td className="px-3 py-2 text-right">{f.daily_span_minutes}</td>
                                    <td className="px-3 py-2 text-right">{f.households_per_building.max_ratio}</td>
                                    <td className="px-3 py-2 text-right">{f.households_4plus_children_count}</td>
                                    <td className="px-3 py-2 text-right">{f.gap_lt_2min_count}</td>
                                    <td className="px-3 py-2 text-right">{f.vaccine_yes_pct}</td>
                                    <td className="px-3 py-2 text-right">{f.camping_repeat_pct}</td>
                                    <td className="px-3 py-2 text-right">{f.duplicate_child_names_count}</td>
                                    <td className="px-3 py-2 text-right">{f.duplicate_child_ages_count}</td>
                                    <td className="px-3 py-2 text-right">{f.muac_repetition_pct}</td>
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
    """Compute one WAT calendar day's FLW daily indicators for every
    opportunity this (program-owned, multi-opp) definition spans, creating and
    completing one run per opportunity.

    Like flw_weekly_audit_report's run_default, the run is created and
    completed in the same call -- unattended daily snapshot, no reviewer in
    the loop. ``window`` overrides the default "yesterday, Africa/Lagos" day
    for backfills; it is a (window_start, window_end) UTC half-open pair, same
    convention as the weekly report, just spanning one day instead of seven.

    The work_areas (cchq_cases) pipeline is deliberately fetched directly via
    fetch_cchq_cases_as_visit_dicts, NOT through WorkflowDataAccess.get_pipeline_data:
    that generic path never threads a cchq_access_token down to the cchq_cases
    fetcher (its PipelineDataAccess is constructed with request=None in every
    headless run_default call), so it would raise CCHQHeadlessError every time
    this runs unattended -- on schedule or via backfill. Calling the fetcher
    directly lets us pass cchq_access_token explicitly (the scheduler mints one
    per run via get_valid_cchq_access_token(owner); the backfill management
    command does the same). If no cchq_access_token is available (or the fetch
    otherwise fails), building-count enrichment degrades gracefully: indicator
    #2's ratio is None rather than the whole run failing.
    """
    import logging
    from collections import defaultdict
    from datetime import datetime, timedelta, timezone

    from connect_labs.labs.analysis.backends.sql.cchq_cases_fetcher import fetch_cchq_cases_as_visit_dicts
    from connect_labs.labs.analysis.config import DataSourceConfig
    from connect_labs.workflow.data_access import WorkflowDataAccess
    from connect_labs.workflow.flw_audit_compute import FORM_NAME, WAT_OFFSET, wat_date
    from connect_labs.workflow.flw_daily_indicator_compute import compute_flw_daily_indicators

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
        raise ValueError("flw_daily_indicator_report requires at least one opportunity on the definition")

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

    def _to_float(value):
        try:
            return float(value) if value not in (None, "") else None
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

    visits_by_opp_flw = defaultdict(lambda: defaultdict(list))
    for r in hsd_rows:
        visits_by_opp_flw[r["opportunity_id"]][r["username"]].append(r)

    work_area_data_source = DataSourceConfig(type="cchq_cases", case_type="work-area")
    building_counts_by_opp = defaultdict(dict)
    for opp_id in opp_ids:
        try:
            wa_rows = fetch_cchq_cases_as_visit_dicts(
                request, work_area_data_source, access_token, opp_id, cchq_access_token=cchq_access_token
            )
        except Exception:
            logger.exception(
                "flw_daily_indicator_report: failed to fetch work-area building counts for opp %s "
                "(indicator #2's ratio will be None for this opp/day)",
                opp_id,
            )
            continue
        for row in wa_rows:
            case = row.get("form_json", {}).get("case", {})
            entity_id = case.get("case_id")
            if entity_id:
                building_counts_by_opp[opp_id][str(entity_id)] = _to_float(
                    (case.get("properties") or {}).get("building_count")
                )

    date_iso = wat_date(window_start)
    generated_at = datetime.now(timezone.utc).isoformat()

    opp_results = {}
    for opp_id in opp_ids:
        flws = []
        wa_building_counts = building_counts_by_opp.get(opp_id, {})
        for username, visits in visits_by_opp_flw.get(opp_id, {}).items():
            indicators = compute_flw_daily_indicators(visits, wa_building_counts=wa_building_counts)
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
                    "flw_daily_indicators": {
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
    "key": "flw_daily_indicator_report",
    "name": "FLW Daily Indicator Report",
    "description": (
        "Program 176 (CHC PRE-RCT Nigeria) daily per-FLW fraud/data-quality indicators -- computed "
        "automatically every day. Raw indicator values only; see FLW Daily Indicator Table for the "
        "14-day view with thresholds and the roll-up flag. Not an interactive review (no statuses "
        "to assign)."
    ),
    "icon": "fa-calendar-check",
    "color": "orange",
    "multi_opp": True,
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schemas": PIPELINE_SCHEMAS,
    "supports_saved_runs": True,
    "snapshot_schema": SNAPSHOT_SCHEMA,
    "supports_default_run": True,
}
