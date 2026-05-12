"""
MBW Auditing V4 job handler.

Fetches all three pipeline datasets server-side (no browser round-trip), runs
the standard MBW analysis, and returns per-FLW metric summaries ready for direct
display in the render code. No 73MB POST body — the browser sends only a minimal
job config.

Supports a `task_filters` parameter for Tab 2: when provided, visit rows are
filtered to post-trigger-date rows for each FLW, enabling "improvement since
task triggered" metrics without a separate browser-side filtering step.
"""

import logging
import math
import statistics
from collections import defaultdict
from datetime import date, datetime

from commcare_connect.workflow.tasks import _create_mock_request, register_job_handler


def _compute_gps_metrics_from_rows(
    visit_rows: list[dict], active_usernames: set[str]
) -> tuple[dict, dict, dict]:
    """Compute GPS metrics directly from pipeline rows.

    The visits pipeline now delivers latitude, longitude (parsed floats) and
    distance_from_prev_case_visit_m (haversine distance to previous same-mother
    visit, computed by a SQL window function). This replaces the old approach of
    shipping raw gps_location strings and computing distances in Python.

    Returns:
        avg_case_dist_by_flw  — {username: meters | None}  (for revisit_dist)
        median_meters_by_flw  — {username: meters | None}  (for meter_per_visit)
        median_minutes_by_flw — {username: minutes | None} (for minute_per_visit)
    """

    def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        r = 6_371_000
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlam = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
        return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    def _parse_dt(s: str | None) -> datetime | None:
        if not s:
            return None
        try:
            return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None

    rows_by_flw: dict[str, list[dict]] = defaultdict(list)
    for row in visit_rows:
        u = (row.get("username") or "").lower()
        if u in active_usernames:
            rows_by_flw[u].append(row)

    avg_case_dist: dict[str, float | None] = {}
    median_meters: dict[str, float | None] = {}
    median_minutes: dict[str, float | None] = {}

    for username, rows in rows_by_flw.items():
        # revisit_dist: mean of SQL-pre-computed same-mother distances
        case_dists = []
        for r in rows:
            raw = r.get("distance_from_prev_case_visit_m")
            if raw is not None:
                try:
                    case_dists.append(float(raw))
                except (ValueError, TypeError):
                    pass
        avg_case_dist[username] = sum(case_dists) / len(case_dists) if case_dists else None

        # meter_per_visit and minute_per_visit: median over consecutive
        # between-mother pairs within each day (same logic as V3 gps_analysis).
        valid = []
        for r in rows:
            lat = r.get("latitude")
            lon = r.get("longitude")
            dt = _parse_dt(r.get("visit_datetime"))
            mid = r.get("mother_case_id")
            if lat is None or lon is None or dt is None or not mid:
                continue
            try:
                valid.append(
                    {
                        "lat": float(lat),
                        "lon": float(lon),
                        "dt": dt,
                        "mother_id": mid,
                        "app_v": r.get("app_build_version"),
                    }
                )
            except (ValueError, TypeError):
                pass

        by_day: dict = defaultdict(list)
        for r in valid:
            by_day[r["dt"].date()].append(r)

        meter_dists: list[float] = []
        minute_diffs: list[float] = []

        for day_rows in by_day.values():
            day_rows.sort(key=lambda r: r["dt"])
            seen: set[str] = set()
            unique = []
            for r in day_rows:
                if r["mother_id"] not in seen:
                    seen.add(r["mother_id"])
                    unique.append(r)
            if len(unique) < 2:
                continue
            for i in range(len(unique) - 1):
                a, b = unique[i], unique[i + 1]
                if a["app_v"] is not None and b["app_v"] is not None:
                    meter_dists.append(_haversine_m(a["lat"], a["lon"], b["lat"], b["lon"]))
                diff = (b["dt"] - a["dt"]).total_seconds() / 60
                if diff >= 0:
                    minute_diffs.append(diff)

        median_meters[username] = round(statistics.median(meter_dists)) if meter_dists else None
        median_minutes[username] = round(statistics.median(minute_diffs)) if minute_diffs else None

    return avg_case_dist, median_meters, median_minutes

logger = logging.getLogger(__name__)


@register_job_handler("mbw_auditing_v4")
def handle_mbw_auditing_v4_job(job_config: dict, access_token: str, progress_callback) -> dict:
    """
    Handle MBW Auditing V4 job.

    Fetches pipeline data server-side, runs analysis, returns flw_summaries.

    job_config keys:
      - active_usernames: list of FLW usernames to include
      - flw_names: {username → display_name}
      - flw_statuses: {username → result}
      - opportunity_id: int (injected by run_workflow_job)
      - task_filters: {username → triggered_at_iso} (optional, for Tab 2)

    Returns:
      - flw_summaries: list of per-FLW metric dicts
      - successful, failed, errors
    """
    from commcare_connect.workflow.data_access import PipelineDataAccess
    from commcare_connect.workflow.job_handlers.mbw_monitoring import (
        _adapt_rows,
        _compute_ebf_by_flw,
        _extract_per_mother_fields,
    )
    from commcare_connect.workflow.templates.mbw_auditing_v4 import PIPELINE_SCHEMAS
    from commcare_connect.workflow.templates.mbw_monitoring.followup_analysis import (
        aggregate_flw_followup,
        aggregate_mother_metrics,
        build_followup_from_pipeline,
        count_mothers_from_pipeline,
        extract_mother_metadata_from_forms,
    )

    active_usernames_list = job_config.get("active_usernames", [])
    active_usernames = {u.lower() for u in active_usernames_list}
    flw_names = job_config.get("flw_names", {})
    flw_statuses = job_config.get("flw_statuses", {})
    task_filters = job_config.get("task_filters", {})  # {username: triggered_at_iso}
    opportunity_id = job_config.get("opportunity_id")

    current_date = date.today()
    results: dict = {"successful": 0, "failed": 0, "errors": []}

    # =========================================================================
    # Stage 0: Fetch pipeline data server-side (uses SQL cache)
    # =========================================================================
    progress_callback("Fetching pipeline data...", processed=0, total=6)

    try:
        mock_request = _create_mock_request(access_token, opportunity_id)
        pipeline_access = PipelineDataAccess(
            request=mock_request,
            access_token=access_token,
            opportunity_id=opportunity_id,
        )

        schema_map = {entry["alias"]: entry["schema"] for entry in PIPELINE_SCHEMAS}

        visits_result = pipeline_access.execute_pipeline_from_schema(
            schema_map["visits"], opportunity_id, alias="v4_visits"
        )
        registrations_result = pipeline_access.execute_pipeline_from_schema(
            schema_map["registrations"], opportunity_id, alias="v4_registrations"
        )
        gs_forms_result = pipeline_access.execute_pipeline_from_schema(
            schema_map["gs_forms"], opportunity_id, alias="v4_gs_forms"
        )
        pipeline_access.close()

    except Exception as e:
        logger.error("[MBW V4 Job] Pipeline fetch failed: %s", e, exc_info=True)
        return {
            "successful": 0,
            "failed": 1,
            "errors": [{"step": "pipeline_fetch", "error": str(e)}],
            "flw_summaries": [],
        }

    visit_rows = visits_result.get("rows", [])
    registration_rows = registrations_result.get("rows", [])
    gs_form_rows = gs_forms_result.get("rows", [])

    # Apply task_filters: Tab 2 — keep only post-trigger-date visit rows per FLW
    if task_filters:
        filtered_visits = []
        for row in visit_rows:
            username = (row.get("username") or "").lower()
            triggered_at = task_filters.get(username)
            if not triggered_at:
                continue
            visit_dt = row.get("visit_datetime") or row.get("timeEnd") or ""
            if str(visit_dt) >= str(triggered_at):
                filtered_visits.append(row)
        visit_rows = filtered_visits
        # Restrict analysis to FLWs that have a task filter
        active_usernames = active_usernames & {u.lower() for u in task_filters}

    logger.info(
        "[MBW V4 Job] Pipeline loaded: %d visits, %d registrations, %d gs_forms, %d active FLWs",
        len(visit_rows),
        len(registration_rows),
        len(gs_form_rows),
        len(active_usernames),
    )

    progress_callback(
        f"Fetched {len(visit_rows)} visit rows for {len(active_usernames)} FLWs",
        processed=1,
        total=6,
    )

    # =========================================================================
    # Step 1: GPS Analysis
    # Pipeline now delivers latitude, longitude, and distance_from_prev_case_visit_m
    # (haversine distance to the previous same-mother visit, computed by a SQL
    # window function). No Python-side GPS string parsing needed.
    # =========================================================================
    avg_case_dist_by_flw: dict[str, float | None] = {}
    median_meters_by_flw: dict[str, float | None] = {}
    median_minutes_by_flw: dict[str, float | None] = {}
    try:
        progress_callback("Running GPS analysis...", processed=1, total=6)
        avg_case_dist_by_flw, median_meters_by_flw, median_minutes_by_flw = (
            _compute_gps_metrics_from_rows(visit_rows, active_usernames)
        )
        results["successful"] += 1
        logger.info("[MBW V4 Job] GPS analysis complete")
    except Exception as e:
        logger.error("[MBW V4 Job] GPS analysis failed: %s", e, exc_info=True)
        results["errors"].append({"step": "gps_analysis", "error": str(e)})
        results["failed"] += 1

    # =========================================================================
    # Step 2: Follow-up Rate + Drilldown
    # =========================================================================
    followup_data: dict | None = None
    flw_drilldown: dict = {}
    visit_cases_by_flw: dict = {}
    mother_metadata: dict = {}
    anc_date_by_mother: dict = {}
    pnc_date_by_mother: dict = {}
    baby_dob_by_mother: dict = {}
    adapted_visit_rows = None

    try:
        progress_callback("Computing follow-up rates...", processed=2, total=6)
        adapted_visit_rows = _adapt_rows(visit_rows)

        visit_cases_by_flw = build_followup_from_pipeline(
            adapted_visit_rows, active_usernames, registration_forms=registration_rows
        )
        mother_metadata = extract_mother_metadata_from_forms(registration_rows, current_date=current_date)
        flw_followup = aggregate_flw_followup(
            visit_cases_by_flw, current_date, flw_names, mother_cases_map=mother_metadata
        )

        per_mother = _extract_per_mother_fields(adapted_visit_rows)
        anc_date_by_mother = per_mother["anc_date_by_mother"]
        pnc_date_by_mother = per_mother["pnc_date_by_mother"]
        baby_dob_by_mother = per_mother["baby_dob_by_mother"]

        for flw_username, flw_cases in visit_cases_by_flw.items():
            flw_drilldown[flw_username] = aggregate_mother_metrics(
                flw_cases,
                current_date,
                mother_cases_map=mother_metadata,
                anc_date_by_mother=anc_date_by_mother,
                pnc_date_by_mother=pnc_date_by_mother,
                baby_dob_by_mother=baby_dob_by_mother,
            )

        followup_data = {"flw_summaries": flw_followup}
        results["successful"] += 1
        logger.info("[MBW V4 Job] Follow-up analysis complete: %d FLWs", len(visit_cases_by_flw))
    except Exception as e:
        logger.error("[MBW V4 Job] Follow-up analysis failed: %s", e, exc_info=True)
        results["errors"].append({"step": "followup_analysis", "error": str(e)})
        results["failed"] += 1
        if not adapted_visit_rows:
            adapted_visit_rows = _adapt_rows(visit_rows)

    # =========================================================================
    # Step 3: Overview (mother counts + EBF)
    # =========================================================================
    mother_counts: dict = {}
    ebf_pct_by_flw: dict = {}
    try:
        progress_callback("Computing overview metrics...", processed=3, total=6)
        rows_for_overview = adapted_visit_rows or _adapt_rows(visit_rows)
        mother_counts = count_mothers_from_pipeline(
            rows_for_overview, active_usernames, registration_forms=registration_rows
        )
        ebf_pct_by_flw = _compute_ebf_by_flw(rows_for_overview)
        results["successful"] += 1
    except Exception as e:
        logger.error("[MBW V4 Job] Overview failed: %s", e, exc_info=True)
        results["errors"].append({"step": "overview", "error": str(e)})
        results["failed"] += 1

    # =========================================================================
    # Step 4: GS Score — max score per FLW from gs_forms pipeline
    # =========================================================================
    gs_by_flw: dict[str, float] = {}
    try:
        progress_callback("Processing GS scores...", processed=4, total=6)
        for row in gs_form_rows:
            uid = ((row.get("user_connect_id") or row.get("username")) or "").lower()
            raw_score = row.get("gs_score")
            if raw_score is not None:
                try:
                    score = float(raw_score)
                    if score > 0:
                        gs_by_flw[uid] = max(gs_by_flw.get(uid, 0.0), score)
                except (ValueError, TypeError):
                    pass
        results["successful"] += 1
        logger.info("[MBW V4 Job] GS scores computed for %d FLWs", len(gs_by_flw))
    except Exception as e:
        logger.error("[MBW V4 Job] GS score computation failed: %s", e, exc_info=True)
        results["errors"].append({"step": "gs_scores", "error": str(e)})
        results["failed"] += 1

    # =========================================================================
    # Step 5: Assemble per-FLW summaries
    # =========================================================================
    progress_callback("Assembling FLW summaries...", processed=5, total=6)

    fu_by_username: dict = {}
    if followup_data:
        for f in followup_data["flw_summaries"]:
            fu_by_username[(f.get("username") or "").lower()] = f

    flw_summaries = []
    for flw_username in sorted(active_usernames):
        u = flw_username.lower()

        fu_flw = fu_by_username.get(u, {})
        drilldown = flw_drilldown.get(u, [])

        # % still eligible: mothers where eligible=True AND anc_completion_date is set
        elig_mothers = [m for m in drilldown if m.get("eligible") and m.get("anc_completion_date")]
        still_on_track = 0
        for m in elig_mothers:
            missed = sum(1 for v in (m.get("visits") or []) if (v.get("status") or "") == "Missed")
            if missed <= 1:
                still_on_track += 1
        pct_still_eligible = round(still_on_track / len(elig_mothers) * 100) if elig_mothers else None

        # GPS metrics — sourced directly from pipeline-computed fields
        avg_dist_m = avg_case_dist_by_flw.get(u)
        revisit_km = round(avg_dist_m / 1000 * 100) / 100 if avg_dist_m is not None else None
        median_m = median_meters_by_flw.get(u)
        median_min = median_minutes_by_flw.get(u)
        dist_ratio = None
        if revisit_km is not None and median_m is not None and median_m > 0:
            dist_ratio = round(revisit_km * 1000 / median_m * 10) / 10

        gs_score = gs_by_flw.get(u)
        if gs_score is not None:
            gs_score = round(gs_score)

        followup_rate = fu_flw.get("completion_rate")
        ebf_pct = ebf_pct_by_flw.get(u)

        display_name = flw_names.get(u) or flw_names.get(flw_username) or flw_username

        flw_summaries.append(
            {
                "username": u,
                "display_name": display_name,
                # last_active is not available server-side; render code merges from workers prop
                "num_mothers": mother_counts.get(u, 0),
                "num_mothers_eligible": len(elig_mothers),
                "gs_score": gs_score,
                "followup_rate": followup_rate,
                "pct_still_eligible": pct_still_eligible,
                "ebf_pct": ebf_pct,
                "revisit_dist": revisit_km,
                "meter_per_visit": median_m,
                "dist_ratio": dist_ratio,
                "minute_per_visit": median_min,
            }
        )

    results["flw_summaries"] = flw_summaries

    progress_callback(
        f"Complete: {results['successful']} steps OK, {results['failed']} failed",
        processed=6,
        total=6,
    )

    logger.info(
        "[MBW V4 Job] Finished: %d FLW summaries, %d successful, %d failed",
        len(flw_summaries),
        results["successful"],
        results["failed"],
    )

    return results
