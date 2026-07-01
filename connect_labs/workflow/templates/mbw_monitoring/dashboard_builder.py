"""Pure-function entry point that produces v1's dashboard payload.

The MBW v1 dashboard view (`MBWAnalysisView.dispatch_streaming`) interleaves
data fetching, SSE event yielding, memory pressure mitigation, and the
actual compute logic. For programmatic use — parity testing, MCP tools,
batch jobs — we want only the compute logic, taking already-fetched inputs
and returning the dashboard dict.

This module factors that compute logic into a single callable. The view
still owns its fetch-and-stream orchestration; this module owns the
math. Both must produce the same payload from the same inputs.

Inputs come from upstream callers:
  - `pipeline_rows`: v1 visit pipeline rows (with `.username` and
    `.computed`). Same shape `analyze_gps_metrics` and
    `build_followup_from_pipeline` consume.
  - `registration_forms`: raw CCHQ form dicts ("Register Mother" forms).
  - `gs_forms`: raw CCHQ Gold Standard Visit Checklist form dicts.
  - `active_usernames`: lowercased set of FLW usernames in scope.
  - `flw_names`: lowercased username → display name.
  - `flw_last_active`: lowercased username → ISO timestamp string.
  - `flw_statuses`: lowercased username → assessment status key.
  - `current_date`: reference date for "due past grace" calculations.

Output: the same dashData dict structure the React render consumes.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from datetime import timezone as dt_timezone
from typing import Any

from .data_transforms import build_gps_visit_dicts, compute_ebf_by_flw, extract_per_mother_fields
from .followup_analysis import (
    aggregate_flw_followup,
    aggregate_mother_metrics,
    aggregate_visit_status_distribution,
    build_followup_from_pipeline,
    compute_flw_performance_by_status,
    compute_overview_quality_metrics,
    count_mothers_from_pipeline,
    extract_mother_metadata_from_forms,
)
from .gps_analysis import analyze_gps_metrics, compute_median_meters_per_visit, compute_median_minutes_per_visit


def build_v1_dashboard_payload(
    *,
    pipeline_rows: list,
    registration_forms: list[dict],
    gs_forms: list[dict],
    active_usernames: set[str],
    flw_names: dict[str, str],
    flw_last_active: dict[str, str] | None = None,
    flw_statuses: dict[str, str] | None = None,
    current_date: date | None = None,
) -> dict[str, Any]:
    """Run v1's MBW monitoring compute pipeline and return the dashData blob.

    No I/O, no SSE, no garbage-collection nudges — just the math. The view
    handles all of that and calls into this. Programmatic callers can
    skip the view entirely.
    """
    if current_date is None:
        current_date = date.today()
    flw_last_active = flw_last_active or {}
    flw_statuses = flw_statuses or {}
    active_usernames = {u.lower() for u in active_usernames}
    flw_names = {k.lower(): v for k, v in flw_names.items()}
    flw_last_active = {k.lower(): v for k, v in flw_last_active.items()}
    flw_statuses = {k.lower(): v for k, v in flw_statuses.items()}

    # ---- GPS analysis ----
    visits_for_gps = build_gps_visit_dicts(pipeline_rows, active_usernames)
    gps_result = analyze_gps_metrics(visits_for_gps, flw_names=flw_names)

    gps_data = {
        "flw_summaries": [
            {
                "username": s.username,
                "display_name": s.display_name,
                "total_visits": s.total_visits,
                "visits_with_gps": s.visits_with_gps,
                "flagged_visits": s.flagged_visits,
                "unique_cases": s.unique_cases,
                "avg_case_distance_km": s.avg_case_distance_km,
                "max_case_distance_km": s.max_case_distance_km,
                "cases_with_revisits": s.cases_with_revisits,
                "avg_daily_travel_km": s.avg_daily_travel_km,
            }
            for s in gps_result.flw_summaries
        ],
        "total_visits": gps_result.total_visits,
        "total_flagged": gps_result.total_flagged,
        "date_range_start": (gps_result.date_range_start.isoformat() if gps_result.date_range_start else None),
        "date_range_end": (gps_result.date_range_end.isoformat() if gps_result.date_range_end else None),
    }

    meters_per_visit_by_flw = compute_median_meters_per_visit(gps_result.visits)
    minutes_per_visit_by_flw = compute_median_minutes_per_visit(gps_result.visits)
    gps_data["median_meters_by_flw"] = {u: m for u, m in meters_per_visit_by_flw.items() if m is not None}
    gps_data["median_minutes_by_flw"] = {u: m for u, m in minutes_per_visit_by_flw.items() if m is not None}
    for flw_summary in gps_data["flw_summaries"]:
        flw_summary["median_meters_per_visit"] = meters_per_visit_by_flw.get(flw_summary["username"])

    # ---- Followup analysis ----
    visit_cases_by_flw = build_followup_from_pipeline(
        pipeline_rows, active_usernames, registration_forms=registration_forms
    )
    mother_metadata = extract_mother_metadata_from_forms(registration_forms, current_date=current_date)
    flw_followup = aggregate_flw_followup(
        visit_cases_by_flw, current_date, flw_names, mother_cases_map=mother_metadata
    )
    visit_status_distribution = aggregate_visit_status_distribution(visit_cases_by_flw, current_date)

    per_mother = extract_per_mother_fields(pipeline_rows)
    parity_by_mother = per_mother["parity_by_mother"]
    anc_date_by_mother = per_mother["anc_date_by_mother"]
    pnc_date_by_mother = per_mother["pnc_date_by_mother"]
    baby_dob_by_mother = per_mother["baby_dob_by_mother"]
    ebf_pct_by_flw = compute_ebf_by_flw(pipeline_rows)

    flw_drilldown = {}
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
    }

    # ---- GS scores per FLW ----
    gs_scores_by_flw: dict[str, list[tuple[str, str]]] = {}
    for form_dict in gs_forms:
        form = form_dict.get("form", {})
        connect_id = (form.get("load_flw_connect_id", "") or "").lower()
        score = form.get("checklist_percentage", "")
        time_end = form.get("meta", {}).get("timeEnd", "")
        if connect_id and score:
            gs_scores_by_flw.setdefault(connect_id, []).append((time_end, score))
    first_gs_by_flw: dict[str, str] = {}
    for connect_id, scores in gs_scores_by_flw.items():
        scores.sort(key=lambda x: x[0])
        first_gs_by_flw[connect_id] = scores[0][1]

    quality_metrics = compute_overview_quality_metrics(
        visit_cases_by_flw,
        mother_metadata,
        parity_by_mother,
        anc_date_by_mother=anc_date_by_mother,
        pnc_date_by_mother=pnc_date_by_mother,
    )

    mother_counts = count_mothers_from_pipeline(pipeline_rows, active_usernames, registration_forms=registration_forms)

    # GPS median + revisit aggregates merged into per-FLW summaries.
    gps_median_by_flw: dict[str, float] = {}
    gps_revisit_cases_by_flw: dict[str, int] = {}
    for flw in gps_result.flw_summaries:
        if flw.avg_case_distance_km is not None:
            gps_median_by_flw[flw.username] = round(flw.avg_case_distance_km, 2)
        gps_revisit_cases_by_flw[flw.username] = flw.cases_with_revisits

    # Followup rate + completed counts.
    completed_by_flw: dict[str, int] = {}
    followup_rate_by_flw: dict[str, int] = {}
    for flw_summary in flw_followup:
        completed_by_flw[flw_summary["username"]] = flw_summary["completed_total"]
        followup_rate_by_flw[flw_summary["username"]] = flw_summary["completion_rate"]

    eligible_mothers_by_flw: dict[str, int] = {}
    for flw_username, flw_cases in visit_cases_by_flw.items():
        mother_ids = {
            c.get("properties", {}).get("mother_case_id", "")
            for c in flw_cases
            if c.get("properties", {}).get("mother_case_id")
        }
        eligible_count = sum(
            1
            for mid in mother_ids
            if mother_metadata.get(mid, {}).get("properties", {}).get("eligible_full_intervention_bonus") == "1"
        )
        eligible_mothers_by_flw[flw_username] = eligible_count

    cases_eligible_by_flw: dict[str, dict] = {}
    for flw_username, mothers in flw_drilldown.items():
        eligible_mothers = [m for m in mothers if m.get("eligible")]
        still_on_track = 0
        for m in eligible_mothers:
            completed_count = sum(1 for v in m["visits"] if v["status"].startswith("Completed"))
            missed_count = sum(1 for v in m["visits"] if v["status"] == "Missed")
            if completed_count >= 5 or missed_count <= 1:
                still_on_track += 1
        total_eligible = len(eligible_mothers)
        cases_eligible_by_flw[flw_username] = {
            "eligible": still_on_track,
            "total": total_eligible,
            "pct": round(still_on_track / total_eligible * 100) if total_eligible > 0 else 0,
        }

    # ---- Per-FLW overview rows ----
    overview_flws: list[dict] = []
    now_utc = datetime.now(dt_timezone.utc)
    for username in sorted(active_usernames):
        display_name = flw_names.get(username, username)
        la_str = flw_last_active.get(username)
        last_active_days: int | None = None
        last_active_date: str | None = None
        if la_str:
            try:
                la_dt = datetime.fromisoformat(str(la_str).replace("Z", "+00:00"))
                last_active_days = max(0, (now_utc - la_dt).days)
                last_active_date = la_dt.strftime("%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                pass
        overview_flws.append(
            {
                "username": username,
                "display_name": display_name,
                "last_active_days": last_active_days,
                "last_active_date": last_active_date,
                "cases_registered": mother_counts.get(username, 0),
                "eligible_mothers": eligible_mothers_by_flw.get(username, 0),
                "first_gs_score": first_gs_by_flw.get(username),
                "post_test_attempts": None,
                "followup_rate": followup_rate_by_flw.get(username, 0),
                "ebf_pct": ebf_pct_by_flw.get(username),
                "revisit_distance_km": gps_median_by_flw.get(username),
                "cases_with_revisits": gps_revisit_cases_by_flw.get(username, 0),
                "median_meters_per_visit": meters_per_visit_by_flw.get(username),
                "median_minutes_per_visit": minutes_per_visit_by_flw.get(username),
                **quality_metrics.get(username, {}),
                "cases_still_eligible": cases_eligible_by_flw.get(username, {"eligible": 0, "total": 0, "pct": 0}),
            }
        )

    overview_data = {
        "flw_summaries": overview_flws,
        "visit_status_distribution": visit_status_distribution,
        "mother_counts": mother_counts,
        "ebf_pct_by_flw": ebf_pct_by_flw,
        "form_name_distribution": dict(
            Counter((row.computed.get("form_name") or "").strip() for row in pipeline_rows)
        ),
        "total_visit_rows": len(pipeline_rows),
        "total_registration_forms": len(registration_forms),
        "total_gs_forms": len(gs_forms),
    }

    performance_data = compute_flw_performance_by_status(flw_statuses, flw_drilldown, current_date)

    return {
        "gps_data": gps_data,
        "followup_data": followup_data,
        "overview_data": overview_data,
        "performance_data": performance_data,
        "quality_metrics": quality_metrics,
        "active_usernames": sorted(active_usernames),
        "flw_names": flw_names,
    }
