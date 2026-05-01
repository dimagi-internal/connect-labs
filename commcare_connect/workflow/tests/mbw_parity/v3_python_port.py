"""Python port of v3 render JS aggregation functions, used for v1↔v3 parity.

The JS lives in `commcare_connect/workflow/templates/mbw_monitoring_v3_render.js`
and runs in the browser; we cannot diff its output against v1 (which is
Python) without porting it. This module mirrors the JS line-for-line so a
parity test can run both paths on the same synthetic input.

Keep this file in lockstep with the JS. When the JS aggregation logic
changes, change here too — and the parity test will catch any drift.
"""

from __future__ import annotations

from datetime import date, datetime

# Mirrors MBW_FORM_NAME_TO_VISIT_TYPE in the JS.
MBW_FORM_NAME_TO_VISIT_TYPE = {
    "ANC Visit": "ANC Visit",
    "ANC Visit ": "ANC Visit",  # trailing-space variant in production data
    "Post delivery visit": "Postnatal Delivery Visit",
    "Postnatal Delivery Visit": "Postnatal Delivery Visit",
    "Postnatal Visit": "Postnatal Visit",
    "1 Week Visit": "1 Week Visit",
    "1 Month Visit": "1 Month Visit",
    "3 Month Visit": "3 Month Visit",
    "6 Month Visit": "6 Month Visit",
}


def _parse_iso_date(s: str | None) -> date | None:
    """Mirror the JS `Date.parse(...)` then take date portion."""
    if not s:
        return None
    try:
        # Trim to YYYY-MM-DD; JS Date.parse handles both date-only and ISO timestamps.
        return date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


def _parse_iso_datetime_ms(s: str | None) -> int:
    """Approximate JS `Date.parse(s)` returning ms since epoch. 0 on failure
    so missing values sort before real ones — same as JS NaN→falsy default."""
    if not s:
        return 0
    try:
        s = str(s).replace("Z", "+00:00")
        return int(datetime.fromisoformat(s).timestamp() * 1000)
    except (ValueError, TypeError):
        return 0


FOLLOWUP_GRACE_PERIOD_DAYS = 5


def build_followup_data_v3(
    registrations_rows: list[dict],
    visits_gps_rows: list[dict],
    flw_name_map: dict[str, str],
    current_date_str: str | None = None,
) -> dict:
    """Python port of `_v3BuildFollowupData` from mbw_monitoring_v3_render.js.

    Inputs mirror the v3 JS function: pipeline rows from `registrations` and
    `visits_gps` pipelines, an FLW username→display name map, and a current
    date string. Output is the same shape JS produces — `flw_summaries`,
    `flw_drilldown`, `total_cases`, `visit_status_distribution`.

    The JS reads `r._username` / `r._visit_date` (set by `_v3PipelineRows`
    for visits_gps rows) and `r.schedules` / `r.registration_date` /
    `r.mother_case_id` / `r.eligible_full_intervention_bonus` for
    registrations.

    completion_rate matches v1's filtered definition: numerator counts
    completed visits among mothers with eligible_full_intervention_bonus=1
    that are past the 5-day grace period; denominator counts all such
    eligible+past-grace visits.
    """
    today = _parse_iso_date(current_date_str) if current_date_str else date.today()
    today_ms = int(datetime.combine(today, datetime.min.time()).timestamp() * 1000)
    grace_cutoff_ms = today_ms - FOLLOWUP_GRACE_PERIOD_DAYS * 86_400_000

    # mother → owner (last-visit-wins from visits_gps).
    mother_to_flw: dict[str, str] = {}
    mother_last_ts: dict[str, int] = {}
    visits_by_mother_type: dict[str, dict[str, str]] = {}  # {mid: {visit_type: visit_date_str}}
    for r in visits_gps_rows:
        mid = r.get("mother_case_id") or ""
        flw = (r.get("_username") or "").lower()
        if not mid or not flw:
            continue
        dt = r.get("visit_datetime") or r.get("_visit_date") or ""
        ts = _parse_iso_datetime_ms(dt)
        if mother_last_ts.get(mid, -1) < ts:
            mother_last_ts[mid] = ts
            mother_to_flw[mid] = flw
        form_name = (r.get("form_name") or "").strip()
        visit_type = MBW_FORM_NAME_TO_VISIT_TYPE.get(form_name)
        if visit_type:
            if mid not in visits_by_mother_type:
                visits_by_mother_type[mid] = {}
            visits_by_mother_type[mid][visit_type] = (str(dt))[:10]

    # mother → schedules + eligibility + form-side FLW fallback (from
    # registrations). The fallback lets registered-but-not-yet-visited
    # mothers still appear in the table, attributed to whoever submitted
    # the registration form.
    mother_to_schedules: dict[str, list[dict]] = {}
    mother_to_reg_date: dict[str, str] = {}
    mother_to_eligibility: dict[str, bool] = {}
    mother_to_flw_fallback: dict[str, str] = {}
    for reg in registrations_rows:
        schedules = reg.get("schedules") or []
        if not isinstance(schedules, list) or not schedules:
            continue
        first_mid = schedules[0].get("mother_case_id") or ""
        if not first_mid:
            continue
        mother_to_schedules[first_mid] = schedules
        rd = reg.get("registration_date")
        if rd:
            mother_to_reg_date[first_mid] = rd
        mother_to_eligibility[first_mid] = str(reg.get("eligible_full_intervention_bonus") or "") == "1"
        reg_user = (reg.get("_username") or reg.get("username") or "").lower()
        if reg_user:
            mother_to_flw_fallback[first_mid] = reg_user

    # Per-FLW buckets of (mother → {eligible, visits[]}). Visits-side
    # attribution wins; registration-side is the fallback for unvisited
    # mothers (mirrors v1's pipeline-overrides-form-metadata precedence).
    flw_buckets: dict[str, dict] = {}
    for mid, schedules in mother_to_schedules.items():
        flw = mother_to_flw.get(mid) or mother_to_flw_fallback.get(mid)
        if not flw:
            continue
        bucket = flw_buckets.setdefault(flw, {"mothers": []})
        mother_visits = visits_by_mother_type.get(mid, {})
        visit_entries: list[dict] = []
        for s in schedules:
            visit_type = s.get("visit_type") or ""
            scheduled_str = s.get("visit_date_scheduled") or ""
            expiry_str = s.get("visit_expiry_date") or ""
            completed_date = mother_visits.get(visit_type)
            scheduled_ms = _parse_iso_datetime_ms(scheduled_str) if scheduled_str else 0
            expiry_ms = _parse_iso_datetime_ms(expiry_str) if expiry_str else 0
            if completed_date:
                status = "Completed"
            elif expiry_str and expiry_ms < today_ms:
                status = "Missed"
            elif scheduled_str and scheduled_ms <= today_ms:
                status = "Due"
            else:
                status = "Upcoming"
            past_grace = scheduled_ms > 0 and scheduled_ms <= grace_cutoff_ms
            visit_entries.append(
                {
                    "visit_type": visit_type,
                    "scheduled": scheduled_str,
                    "expiry": expiry_str,
                    "completed_date": completed_date or None,
                    "status": status,
                    "past_grace": past_grace,
                }
            )
        has_missed = any(v["status"] == "Missed" for v in visit_entries)
        completed_count = sum(1 for v in visit_entries if v["status"] == "Completed")
        bucket["mothers"].append(
            {
                "mother_case_id": mid,
                "eligible_full_intervention_bonus": bool(mother_to_eligibility.get(mid)),
                "eligible": (not has_missed) or completed_count > 0,
                "visits": visit_entries,
            }
        )

    # Per-FLW summary. completion_rate is the v1-business-defined filtered
    # rate (eligibility + 5-day grace), matching v1's _build_flw_summary.
    flw_summaries: list[dict] = []
    for flw, bucket in flw_buckets.items():
        mothers = bucket["mothers"]
        total_expected = 0
        total_completed = 0
        filtered_completed = 0
        filtered_denominator = 0
        for m in mothers:
            for v in m["visits"]:
                if v["status"] != "Upcoming":
                    total_expected += 1
                    if v["status"] == "Completed":
                        total_completed += 1
                if m["eligible_full_intervention_bonus"] and v["past_grace"]:
                    filtered_denominator += 1
                    if v["status"] == "Completed":
                        filtered_completed += 1
        flw_summaries.append(
            {
                "username": flw,
                "display_name": flw_name_map.get(flw, flw),
                "total_mothers": len(mothers),
                "total_expected": total_expected,
                "total_completed": total_completed,
                "completion_rate": (
                    round(filtered_completed / filtered_denominator * 100) if filtered_denominator > 0 else 0
                ),
            }
        )

    flw_drilldown = {flw: bucket["mothers"] for flw, bucket in flw_buckets.items()}

    # Visit-status distribution (across all FLWs).
    dist = {"Completed": 0, "Missed": 0, "Due": 0, "Upcoming": 0}
    total_cases = 0
    for bucket in flw_buckets.values():
        for m in bucket["mothers"]:
            total_cases += 1
            for v in m["visits"]:
                dist[v["status"]] = dist.get(v["status"], 0) + 1

    return {
        "flw_summaries": flw_summaries,
        "flw_drilldown": flw_drilldown,
        "total_cases": total_cases,
        "visit_status_distribution": dist,
    }
