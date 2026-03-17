"""
MBW Monitoring job handler.

Orchestrates the complex MBW dashboard computations:
- GPS analysis (distance, travel, flagging)
- Follow-up rate calculation (visit status, completion rates)
- Quality/fraud overview metrics
- FLW performance by assessment status

Receives pipeline data (already fetched by multi-pipeline infrastructure)
under job_config["pipeline_data"] with three datasets:
  - "visits": visit rows from Connect CSV pipeline
  - "registrations": registration form rows from CCHQ pipeline
  - "gs_forms": gold standard form rows from CCHQ pipeline

Each pipeline dataset has {"rows": [...], "metadata": {...}}.
"""

import logging
from collections import Counter
from datetime import date

from commcare_connect.workflow.tasks import register_job_handler

logger = logging.getLogger(__name__)


class _PipelineRowAdapter:
    """Lightweight adapter to make serialized pipeline row dicts look like VisitRow objects.

    The followup_analysis functions (build_followup_from_pipeline, count_mothers_from_pipeline)
    access rows via attribute syntax: row.username, row.computed, row.visit_date.
    Pipeline data from the SSE stream is serialized as plain dicts. This adapter
    bridges the two without importing the full VisitRow dataclass.
    """

    __slots__ = ("_data",)

    def __init__(self, row_dict: dict):
        self._data = row_dict

    @property
    def username(self) -> str:
        return self._data.get("username") or ""

    @property
    def computed(self) -> dict:
        """Return computed fields dict.

        Pipeline SSE serialization flattens computed fields into the row dict
        (see PipelineDataStreamView). We reconstruct a computed-like dict from
        the known field names that MBW analysis uses.
        """
        # If the row already has a nested "computed" dict, use it directly
        if "computed" in self._data and isinstance(self._data["computed"], dict):
            return self._data["computed"]

        # Otherwise, the pipeline SSE stream flattens computed fields into
        # the top-level row dict. Return the whole dict so .get() calls work.
        return self._data

    @property
    def visit_date(self):
        """Return visit_date, parsing from string if needed."""
        vd = self._data.get("visit_date")
        if vd is None:
            return None
        if isinstance(vd, date):
            return vd
        if isinstance(vd, str) and vd:
            try:
                return date.fromisoformat(vd[:10])
            except (ValueError, TypeError):
                return None
        return None


def _adapt_rows(rows: list[dict]) -> list[_PipelineRowAdapter]:
    """Wrap serialized pipeline row dicts as VisitRow-like objects."""
    return [_PipelineRowAdapter(r) for r in rows]


def _build_gps_visit_dicts(rows: list[dict]) -> list[dict]:
    """Convert pipeline row dicts to the format expected by analyze_gps_metrics.

    analyze_gps_metrics expects dicts with:
      - "id": visit ID
      - "username": FLW username
      - "metadata": {"location": "lat lon ..."}
      - "computed": {"gps_location", "case_id", "mother_case_id",
                     "entity_name", "visit_datetime", "form_name",
                     "app_build_version"}

    Pipeline SSE rows flatten computed fields into the top level.
    """
    result = []
    for row in rows:
        # Computed fields may be nested or flattened
        computed = row.get("computed", {}) if isinstance(row.get("computed"), dict) else {}
        # If flattened, pull from top level
        if not computed:
            computed = {
                "gps_location": row.get("gps_location"),
                "case_id": row.get("case_id"),
                "mother_case_id": row.get("mother_case_id"),
                "entity_name": row.get("entity_name"),
                "visit_datetime": row.get("visit_datetime"),
                "form_name": row.get("form_name"),
                "app_build_version": row.get("app_build_version"),
            }

        visit_dict = {
            "id": row.get("id") or row.get("entity_id") or row.get("visit_id", 0),
            "username": (row.get("username") or "").lower(),
            "visit_date": row.get("visit_date"),
            "metadata": row.get("metadata", {}),
            "computed": computed,
        }
        result.append(visit_dict)
    return result


def _extract_per_mother_fields(adapted_rows: list[_PipelineRowAdapter]) -> dict:
    """Extract parity, ANC date, PNC date, baby DOB from pipeline rows.

    Returns dict with keys: parity_by_mother, anc_date_by_mother,
    pnc_date_by_mother, baby_dob_by_mother.
    """
    parity_by_mother: dict[str, str] = {}
    anc_date_by_mother: dict[str, str] = {}
    pnc_date_by_mother: dict[str, str] = {}
    baby_dob_by_mother: dict[str, str] = {}

    for row in adapted_rows:
        form_name = (row.computed.get("form_name") or "").strip()
        mother_id = row.computed.get("mother_case_id")
        if not mother_id:
            continue

        if form_name == "ANC Visit":
            parity = row.computed.get("parity")
            if parity:
                parity_by_mother[mother_id] = parity
            anc_date = row.computed.get("anc_completion_date")
            if anc_date:
                anc_date_by_mother[mother_id] = anc_date
        elif form_name == "Post delivery visit":
            pnc_date = row.computed.get("pnc_completion_date")
            if pnc_date:
                pnc_date_by_mother[mother_id] = pnc_date
            baby_dob = row.computed.get("baby_dob")
            if baby_dob:
                baby_dob_by_mother[mother_id] = baby_dob

    return {
        "parity_by_mother": parity_by_mother,
        "anc_date_by_mother": anc_date_by_mother,
        "pnc_date_by_mother": pnc_date_by_mother,
        "baby_dob_by_mother": baby_dob_by_mother,
    }


def _compute_ebf_by_flw(adapted_rows: list[_PipelineRowAdapter]) -> dict[str, int]:
    """Compute % exclusive breastfeeding per FLW from pipeline rows."""
    ebf_counts: dict[str, dict] = {}
    for row in adapted_rows:
        bf_status = (row.computed.get("bf_status") or "").strip()
        if not bf_status:
            continue
        username = (row.username or "").strip().lower()
        if not username:
            continue
        if username not in ebf_counts:
            ebf_counts[username] = {"ebf": 0, "total": 0}
        ebf_counts[username]["total"] += 1
        if "ebf" in bf_status.split():
            ebf_counts[username]["ebf"] += 1

    return {
        username: round(counts["ebf"] / counts["total"] * 100)
        for username, counts in ebf_counts.items()
        if counts["total"] > 0
    }


@register_job_handler("mbw_monitoring")
def handle_mbw_monitoring_job(job_config: dict, _access_token: str, progress_callback) -> dict:
    """
    Handle MBW monitoring job - run GPS analysis, follow-up rates, quality metrics.

    Expects job_config to contain:
      - pipeline_data: dict with "visits", "registrations", "gs_forms" keys
      - active_usernames: list of active FLW usernames
      - flw_names: dict mapping username -> display name (optional)
      - flw_statuses: dict mapping username -> status key (optional)
      - opportunity_id: int

    Each pipeline_data entry has {"rows": [...], "metadata": {...}}.

    Returns:
        Results dict with dashboard data sections (gps_data, followup_data,
        overview_data, etc.) plus successful/failed counts.
    """
    from commcare_connect.workflow.templates.mbw_monitoring.followup_analysis import (
        aggregate_flw_followup,
        aggregate_mother_metrics,
        aggregate_visit_status_distribution,
        build_followup_from_pipeline,
        compute_flw_performance_by_status,
        compute_overview_quality_metrics,
        count_mothers_from_pipeline,
        extract_mother_metadata_from_forms,
    )
    from commcare_connect.workflow.templates.mbw_monitoring.gps_analysis import (
        analyze_gps_metrics,
        compute_median_meters_per_visit,
        compute_median_minutes_per_visit,
    )
    from commcare_connect.workflow.templates.mbw_monitoring.serializers import serialize_flw_summary

    pipeline_data = job_config.get("pipeline_data", {})
    active_usernames_list = job_config.get("active_usernames", [])
    active_usernames = {u.lower() for u in active_usernames_list}
    flw_names = job_config.get("flw_names", {})
    flw_statuses = job_config.get("flw_statuses", {})

    # Extract pipeline datasets
    visit_rows = pipeline_data.get("visits", {}).get("rows", [])
    registration_rows = pipeline_data.get("registrations", {}).get("rows", [])
    gs_form_rows = pipeline_data.get("gs_forms", {}).get("rows", [])

    total_records = len(visit_rows) + len(registration_rows) + len(gs_form_rows)
    logger.info(
        "[MBW Job] Starting: %d visits, %d registrations, %d gs_forms, %d active FLWs",
        len(visit_rows),
        len(registration_rows),
        len(gs_form_rows),
        len(active_usernames),
    )

    progress_callback(
        f"Processing {total_records} records",
        processed=0,
        total=5,  # 5 computation steps
    )

    results: dict = {
        "successful": 0,
        "failed": 0,
        "errors": [],
    }

    current_date = date.today()

    # =========================================================================
    # Step 1: GPS Analysis
    # =========================================================================
    try:
        progress_callback("Running GPS analysis...", processed=0, total=5)

        gps_visit_dicts = _build_gps_visit_dicts(visit_rows)
        gps_result = analyze_gps_metrics(gps_visit_dicts, flw_names)

        # Compute median distance/time per FLW
        median_meters = compute_median_meters_per_visit(gps_result.visits)
        median_minutes = compute_median_minutes_per_visit(gps_result.visits)

        gps_data = {
            "total_visits": gps_result.total_visits,
            "total_flagged": gps_result.total_flagged,
            "date_range_start": gps_result.date_range_start.isoformat() if gps_result.date_range_start else None,
            "date_range_end": gps_result.date_range_end.isoformat() if gps_result.date_range_end else None,
            "flw_summaries": [serialize_flw_summary(flw) for flw in gps_result.flw_summaries],
            "median_meters_by_flw": median_meters,
            "median_minutes_by_flw": median_minutes,
        }
        results["gps_data"] = gps_data
        results["successful"] += 1
        logger.info(
            "[MBW Job] GPS analysis complete: %d visits, %d flagged", gps_result.total_visits, gps_result.total_flagged
        )

    except Exception as e:
        logger.error("[MBW Job] GPS analysis failed: %s", e, exc_info=True)
        results["errors"].append({"step": "gps_analysis", "error": str(e)})
        results["failed"] += 1
        results["gps_data"] = None

    # =========================================================================
    # Step 2: Follow-up Rate Analysis
    # =========================================================================
    adapted_visit_rows = None
    try:
        progress_callback("Computing follow-up rates...", processed=1, total=5)

        # Adapt visit rows for followup_analysis functions
        adapted_visit_rows = _adapt_rows(visit_rows)

        # Build follow-up visit data from registration forms + pipeline completions
        visit_cases_by_flw = build_followup_from_pipeline(
            adapted_visit_rows,
            active_usernames,
            registration_forms=registration_rows,
        )

        # Extract mother metadata from registration forms
        mother_metadata = extract_mother_metadata_from_forms(registration_rows, current_date=current_date)

        # Aggregate per-FLW follow-up summaries
        flw_followup = aggregate_flw_followup(
            visit_cases_by_flw,
            current_date,
            flw_names,
            mother_cases_map=mother_metadata,
        )

        # Visit status distribution
        visit_status_distribution = aggregate_visit_status_distribution(visit_cases_by_flw, current_date)

        # Extract per-mother fields from pipeline rows
        per_mother = _extract_per_mother_fields(adapted_visit_rows)
        parity_by_mother = per_mother["parity_by_mother"]
        anc_date_by_mother = per_mother["anc_date_by_mother"]
        pnc_date_by_mother = per_mother["pnc_date_by_mother"]
        baby_dob_by_mother = per_mother["baby_dob_by_mother"]

        # Build per-FLW drilldown (mother-level metrics)
        flw_drilldown: dict[str, list] = {}
        for flw_username, flw_cases in visit_cases_by_flw.items():
            flw_drilldown[flw_username] = aggregate_mother_metrics(
                flw_cases,
                current_date,
                mother_cases_map=mother_metadata,
                anc_date_by_mother=anc_date_by_mother,
                pnc_date_by_mother=pnc_date_by_mother,
                baby_dob_by_mother=baby_dob_by_mother,
            )

        followup_data = {
            "flw_summaries": flw_followup,
            "total_cases": sum(len(v) for v in visit_cases_by_flw.values()),
            "flw_drilldown": flw_drilldown,
            "visit_status_distribution": visit_status_distribution,
        }
        results["followup_data"] = followup_data
        results["successful"] += 1

        logger.info(
            "[MBW Job] Follow-up analysis complete: %d FLWs, %d total cases",
            len(visit_cases_by_flw),
            followup_data["total_cases"],
        )

    except Exception as e:
        logger.error("[MBW Job] Follow-up analysis failed: %s", e, exc_info=True)
        results["errors"].append({"step": "followup_analysis", "error": str(e)})
        results["failed"] += 1
        results["followup_data"] = None
        # Set empty values so downstream steps can still run
        visit_cases_by_flw = {}
        mother_metadata = {}
        parity_by_mother = {}
        anc_date_by_mother = {}
        pnc_date_by_mother = {}
        baby_dob_by_mother = {}
        flw_drilldown = {}
        if not adapted_visit_rows:
            adapted_visit_rows = _adapt_rows(visit_rows)

    # =========================================================================
    # Step 3: Quality/Fraud Overview Metrics
    # =========================================================================
    try:
        progress_callback("Computing quality metrics...", processed=2, total=5)

        quality_metrics = compute_overview_quality_metrics(
            visit_cases_by_flw,
            mother_metadata,
            parity_by_mother,
            anc_date_by_mother=anc_date_by_mother,
            pnc_date_by_mother=pnc_date_by_mother,
        )
        results["quality_metrics"] = quality_metrics
        results["successful"] += 1

        logger.info("[MBW Job] Quality metrics complete for %d FLWs", len(quality_metrics))

    except Exception as e:
        logger.error("[MBW Job] Quality metrics failed: %s", e, exc_info=True)
        results["errors"].append({"step": "quality_metrics", "error": str(e)})
        results["failed"] += 1
        results["quality_metrics"] = None

    # =========================================================================
    # Step 4: FLW Performance by Assessment Status
    # =========================================================================
    try:
        progress_callback("Computing FLW performance metrics...", processed=3, total=5)

        performance_data = compute_flw_performance_by_status(flw_statuses, flw_drilldown, current_date)
        results["performance_data"] = performance_data
        results["successful"] += 1

        logger.info("[MBW Job] FLW performance metrics complete: %d status categories", len(performance_data))

    except Exception as e:
        logger.error("[MBW Job] FLW performance failed: %s", e, exc_info=True)
        results["errors"].append({"step": "flw_performance", "error": str(e)})
        results["failed"] += 1
        results["performance_data"] = None

    # =========================================================================
    # Step 5: Overview Summary (mother counts, EBF, form distribution)
    # =========================================================================
    try:
        progress_callback("Building overview summary...", processed=4, total=5)

        # Ensure adapted_visit_rows exists
        if not adapted_visit_rows:
            adapted_visit_rows = _adapt_rows(visit_rows)

        mother_counts = count_mothers_from_pipeline(
            adapted_visit_rows,
            active_usernames,
            registration_forms=registration_rows,
        )

        ebf_pct_by_flw = _compute_ebf_by_flw(adapted_visit_rows)

        # Form name distribution for diagnostics
        form_name_counts = Counter((row.computed.get("form_name") or "").strip() for row in adapted_visit_rows)

        overview_data = {
            "mother_counts": mother_counts,
            "ebf_pct_by_flw": ebf_pct_by_flw,
            "form_name_distribution": dict(form_name_counts),
            "total_visit_rows": len(visit_rows),
            "total_registration_forms": len(registration_rows),
            "total_gs_forms": len(gs_form_rows),
        }
        results["overview_data"] = overview_data
        results["successful"] += 1

        logger.info(
            "[MBW Job] Overview complete: %d mothers, form distribution: %s",
            sum(mother_counts.values()),
            dict(form_name_counts),
        )

    except Exception as e:
        logger.error("[MBW Job] Overview summary failed: %s", e, exc_info=True)
        results["errors"].append({"step": "overview_summary", "error": str(e)})
        results["failed"] += 1
        results["overview_data"] = None

    progress_callback(
        f"Complete: {results['successful']} steps succeeded, {results['failed']} failed",
        processed=5,
        total=5,
    )

    logger.info(
        "[MBW Job] Finished: %d successful, %d failed, errors=%s",
        results["successful"],
        results["failed"],
        [e["step"] for e in results["errors"]],
    )

    return results
