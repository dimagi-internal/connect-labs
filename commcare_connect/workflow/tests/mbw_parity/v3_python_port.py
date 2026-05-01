"""Python port of v3 render JS aggregation functions, used for v1↔v3 parity.

The JS lives in `commcare_connect/workflow/templates/mbw_monitoring_v3_render.js`
and runs in the browser; we cannot diff its output against v1 (which is
Python) without porting it. This module mirrors the JS line-for-line so a
parity test can run both paths on the same synthetic input.

Keep this file in lockstep with the JS. When the JS aggregation logic
changes, change here too — and the parity test will catch any drift.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta

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

VISIT_ON_TIME_DAYS = {
    "ANC Visit": 7,
    "Postnatal Visit": 4,
    "Postnatal Delivery Visit": 4,
    "1 Week Visit": 7,
    "1 Month Visit": 7,
    "3 Month Visit": 7,
    "6 Month Visit": 7,
}
DEFAULT_ON_TIME_DAYS = 7

STATUS_COMPLETED_ON_TIME = "Completed - On Time"
STATUS_COMPLETED_LATE = "Completed - Late"
STATUS_DUE_ON_TIME = "Due - On Time"
STATUS_DUE_LATE = "Due - Late"
STATUS_MISSED = "Missed"
STATUS_NOT_DUE_YET = "Not Due Yet"

STATUS_TO_KEY = {
    STATUS_COMPLETED_ON_TIME: "completed_on_time",
    STATUS_COMPLETED_LATE: "completed_late",
    STATUS_DUE_ON_TIME: "due_on_time",
    STATUS_DUE_LATE: "due_late",
    STATUS_MISSED: "missed",
    STATUS_NOT_DUE_YET: "not_due_yet",
}
STATUS_KEYS = [
    "completed_on_time",
    "completed_late",
    "due_on_time",
    "due_late",
    "missed",
    "not_due_yet",
]

VISIT_TYPE_TO_BUCKET_KEY = {
    "ANC Visit": "anc",
    "Postnatal Visit": "postnatal",
    "Postnatal Delivery Visit": "postnatal",
    "1 Week Visit": "week1",
    "1 Month Visit": "month1",
    "3 Month Visit": "month3",
    "6 Month Visit": "month6",
}
VISIT_TYPE_BUCKET_ORDER = ["anc", "postnatal", "week1", "month1", "month3", "month6"]
VISIT_TYPE_BUCKET_DISPLAY = {
    "anc": "ANC",
    "postnatal": "Postnatal",
    "week1": "Week 1",
    "month1": "Month 1",
    "month3": "Month 3",
    "month6": "Month 6",
}


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
            on_time_days = VISIT_ON_TIME_DAYS.get(visit_type, DEFAULT_ON_TIME_DAYS)
            on_time_end_ms = scheduled_ms + on_time_days * 86_400_000 if scheduled_ms > 0 else 0
            if completed_date:
                completed_ms = _parse_iso_datetime_ms(completed_date)
                if completed_ms and on_time_end_ms > 0 and completed_ms <= on_time_end_ms:
                    status = STATUS_COMPLETED_ON_TIME
                else:
                    status = STATUS_COMPLETED_LATE
            elif expiry_ms > 0 and expiry_ms < today_ms:
                status = STATUS_MISSED
            elif scheduled_ms > 0 and scheduled_ms > today_ms:
                status = STATUS_NOT_DUE_YET
            elif on_time_end_ms > 0 and today_ms <= on_time_end_ms:
                status = STATUS_DUE_ON_TIME
            else:
                status = STATUS_DUE_LATE
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
        has_missed = any(v["status"] == STATUS_MISSED for v in visit_entries)
        completed_count = sum(1 for v in visit_entries if str(v["status"]).startswith("Completed"))
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
                is_completed = str(v["status"]).startswith("Completed")
                if v["status"] != STATUS_NOT_DUE_YET:
                    total_expected += 1
                    if is_completed:
                        total_completed += 1
                if m["eligible_full_intervention_bonus"] and v["past_grace"]:
                    filtered_denominator += 1
                    if is_completed:
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

    # Visit-status distribution: rich {by_visit_type, totals} shape that
    # matches v1's aggregate_visit_status_distribution. The JSX
    # visit-status chart consumes by_visit_type[].<status_key> + total per
    # row, so the shape is load-bearing — not just a stats blob.
    by_bucket: dict[str, dict[str, int]] = {k: {sk: 0 for sk in STATUS_KEYS} for k in VISIT_TYPE_BUCKET_ORDER}
    totals = {sk: 0 for sk in STATUS_KEYS}
    total_cases = 0
    for bucket in flw_buckets.values():
        for m in bucket["mothers"]:
            total_cases += 1
            for v in m["visits"]:
                bucket_key = VISIT_TYPE_TO_BUCKET_KEY.get(v["visit_type"])
                if not bucket_key:
                    continue
                status_key = STATUS_TO_KEY.get(v["status"])
                if not status_key:
                    continue
                by_bucket[bucket_key][status_key] += 1
                totals[status_key] += 1
    by_visit_type = []
    for k in VISIT_TYPE_BUCKET_ORDER:
        counts = by_bucket[k]
        total = sum(counts.values())
        by_visit_type.append({"visit_type": VISIT_TYPE_BUCKET_DISPLAY[k], **counts, "total": total})
    totals["total"] = sum(totals[sk] for sk in STATUS_KEYS)

    return {
        "flw_summaries": flw_summaries,
        "flw_drilldown": flw_drilldown,
        "total_cases": total_cases,
        "visit_status_distribution": {"by_visit_type": by_visit_type, "totals": totals},
    }


# ---- GPS data builder ---------------------------------------------------

GPS_FLAG_THRESHOLD_M = 5000  # matches v1's DEFAULT_CASE_DISTANCE_THRESHOLD_METERS
GPS_TRAILING_DAYS = 7


def _haversine_meters(lat1, lon1, lat2, lon2):
    """Mirror of `haversine_meters` (Postgres + the JS port). Returns None
    when any input is None or NaN — sparse-GPS-friendly."""
    for v in (lat1, lon1, lat2, lon2):
        if v is None or not isinstance(v, (int, float)) or (isinstance(v, float) and math.isnan(v)):
            return None
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = phi2 - phi1
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def _median(values):
    sorted_vals = sorted(v for v in values if isinstance(v, (int, float)) and not math.isnan(v))
    if not sorted_vals:
        return None
    n = len(sorted_vals)
    mid = n // 2
    return sorted_vals[mid] if n % 2 else (sorted_vals[mid - 1] + sorted_vals[mid]) / 2


def build_gps_data_v3(visits_gps_rows: list[dict], flw_name_map: dict[str, str]) -> dict:
    """Python port of `_v3BuildGpsData` from mbw_monitoring_v3_render.js.

    Mirrors v1's analyze_gps_metrics output shape (per-FLW summaries with
    flagged_visits, unique_cases, cases_with_revisits as distinct mother
    count, avg_daily_travel_km via trailing-7-days path-distance average).
    """
    by_flw: dict[str, dict] = {}
    all_dates: list[str] = []
    total_flagged = 0
    for r in visits_gps_rows:
        flw = (r.get("_username") or "").lower()
        if not flw:
            continue
        if flw not in by_flw:
            by_flw[flw] = {
                "username": flw,
                "display_name": flw_name_map.get(flw, flw),
                "total_visits": 0,
                "visits_with_gps": 0,
                "flagged_visits": 0,
                "unique_case_ids": set(),
                "revisit_mother_ids": set(),
                "distances_m": [],
                "timestamps_ms": [],
                "daily_visits": {},  # {date_str: [{lat, lon, ts}]}
            }
        bucket = by_flw[flw]
        bucket["total_visits"] += 1
        lat = r.get("latitude")
        lon = r.get("longitude")
        has_gps = isinstance(lat, (int, float)) and isinstance(lon, (int, float))
        if has_gps:
            bucket["visits_with_gps"] += 1
        if r.get("case_id"):
            bucket["unique_case_ids"].add(r["case_id"])
        dist = r.get("distance_from_prev_case_visit_m")
        if isinstance(dist, (int, float)) and not math.isnan(dist):
            bucket["distances_m"].append(dist)
            mid = r.get("mother_case_id")
            if mid:
                bucket["revisit_mother_ids"].add(mid)
            if dist > GPS_FLAG_THRESHOLD_M:
                bucket["flagged_visits"] += 1
                total_flagged += 1
        dt = r.get("visit_datetime") or r.get("_visit_date") or ""
        if dt:
            ms = _parse_iso_datetime_ms(dt)
            if ms:
                bucket["timestamps_ms"].append(ms)
            d = str(dt)[:10]
            if d:
                all_dates.append(d)
                if has_gps:
                    bucket["daily_visits"].setdefault(d, []).append({"lat": lat, "lon": lon, "ts": ms})

    all_dates.sort()
    max_date_str = all_dates[-1] if all_dates else None
    max_date = _parse_iso_date(max_date_str) if max_date_str else None

    median_meters: dict[str, int] = {}
    median_minutes: dict[str, int] = {}
    summaries: list[dict] = []
    for flw, b in by_flw.items():
        dist_med = _median(b["distances_m"])
        if dist_med is not None:
            median_meters[flw] = round(dist_med)
        ts_sorted = sorted(b["timestamps_ms"])
        gaps = [(ts_sorted[i] - ts_sorted[i - 1]) / 60000 for i in range(1, len(ts_sorted))]
        min_med = _median(gaps)
        if min_med is not None:
            median_minutes[flw] = round(min_med)
        sum_dist = sum(b["distances_m"])
        avg_km = (sum_dist / len(b["distances_m"]) / 1000) if b["distances_m"] else None
        max_km = (max(b["distances_m"]) / 1000) if b["distances_m"] else None

        # Trailing-7-days daily travel path distance average.
        avg_daily_travel_km: float | None = None
        if max_date is not None:
            daily_kms: list[float] = []
            for i in range(GPS_TRAILING_DAYS):
                d = max_date - timedelta(days=i)
                d_str = d.isoformat()
                day_visits = b["daily_visits"].get(d_str)
                if not day_visits:
                    continue
                sorted_day = sorted(day_visits, key=lambda x: x["ts"])
                path_m = 0.0
                for j in range(1, len(sorted_day)):
                    seg = _haversine_meters(
                        sorted_day[j - 1]["lat"],
                        sorted_day[j - 1]["lon"],
                        sorted_day[j]["lat"],
                        sorted_day[j]["lon"],
                    )
                    if seg is not None:
                        path_m += seg
                daily_kms.append(path_m / 1000)
            if daily_kms:
                avg_daily_travel_km = sum(daily_kms) / len(daily_kms)

        summaries.append(
            {
                "username": flw,
                "display_name": b["display_name"],
                "total_visits": b["total_visits"],
                "visits_with_gps": b["visits_with_gps"],
                "flagged_visits": b["flagged_visits"],
                "unique_cases": len(b["unique_case_ids"]),
                "avg_case_distance_km": avg_km,
                "max_case_distance_km": max_km,
                "cases_with_revisits": len(b["revisit_mother_ids"]),
                "avg_daily_travel_km": avg_daily_travel_km,
            }
        )

    return {
        "flw_summaries": summaries,
        "median_meters_by_flw": median_meters,
        "median_minutes_by_flw": median_minutes,
        "total_visits": len(visits_gps_rows),
        "total_flagged": total_flagged,
        "date_range_start": all_dates[0] if all_dates else None,
        "date_range_end": max_date_str,
    }


# ---- Performance tab builder --------------------------------------------

V3_FLW_STATUS_DISPLAY = {
    "eligible_for_renewal": "Eligible for Renewal",
    "probation": "Probation",
    "suspended": "Suspended",
    "none": "No Category",
}

# (canonical visit_type, min_completed_to_be_on_track, output_key) — v3
# uses canonical visit_type values (not v1's display name shorthand).
V3_VISIT_MILESTONES = [
    ("1 Month Visit", 3, "pct_4_visits_on_track"),
    ("3 Month Visit", 4, "pct_5_visits_complete"),
    ("6 Month Visit", 5, "pct_6_visits_complete"),
]


def build_performance_data_v3(
    flw_statuses: dict,
    flw_drilldown: dict[str, list],
    current_date_str: str | None = None,
) -> list[dict]:
    """Python port of `_v3BuildPerformanceData` from the v3 render JS.

    Mirrors v1's compute_flw_performance_by_status: 4 status-bucket rows
    (eligible_for_renewal / probation / suspended / none), each with
    aggregate counts and milestone percentages.
    """
    flw_statuses = flw_statuses or {}
    flw_drilldown = flw_drilldown or {}
    today = _parse_iso_date(current_date_str) if current_date_str else date.today()
    today_ms = int(datetime.combine(today, datetime.min.time()).timestamp() * 1000)
    grace_cutoff_ms = today_ms - FOLLOWUP_GRACE_PERIOD_DAYS * 86_400_000

    status_order = ["eligible_for_renewal", "probation", "suspended", "none"]
    buckets: dict[str, list[str]] = {s: [] for s in status_order}
    for username, raw in flw_statuses.items():
        status = (raw.get("status") if isinstance(raw, dict) else raw) or "none"
        bucket_key = status if status in buckets else "none"
        buckets[bucket_key].append(username.lower())

    results: list[dict] = []
    for status_key in status_order:
        flw_list = buckets[status_key]
        all_mothers: list[dict] = []
        for username in flw_list:
            for m in flw_drilldown.get(username, []):
                all_mothers.append(m)

        total_cases = len(all_mothers)
        eligible_mothers = [m for m in all_mothers if m.get("eligible")]
        total_eligible = len(eligible_mothers)

        still_eligible = 0
        for m in eligible_mothers:
            completed = sum(1 for v in m.get("visits", []) if (v.get("status") or "").startswith("Completed"))
            missed = sum(1 for v in m.get("visits", []) if v.get("status") == "Missed")
            if completed >= 5 or missed <= 1:
                still_eligible += 1

        missed_1_or_less = 0
        for m in eligible_mothers:
            missed = sum(1 for v in m.get("visits", []) if v.get("status") == "Missed")
            if missed <= 1:
                missed_1_or_less += 1

        milestone_results: dict[str, int] = {}
        for visit_type, min_completed, metric_key in V3_VISIT_MILESTONES:
            denominator = 0
            numerator = 0
            for m in eligible_mothers:
                milestone_visit = next(
                    (v for v in m.get("visits", []) if v.get("visit_type") == visit_type),
                    None,
                )
                if milestone_visit is None:
                    continue
                sched = milestone_visit.get("scheduled") or ""
                if not sched:
                    continue
                sched_ms = _parse_iso_datetime_ms(sched)
                if not sched_ms or sched_ms > grace_cutoff_ms:
                    continue
                denominator += 1
                completed = sum(1 for v in m.get("visits", []) if (v.get("status") or "").startswith("Completed"))
                if completed >= min_completed:
                    numerator += 1
            milestone_results[metric_key] = round(numerator / denominator * 100) if denominator > 0 else 0

        results.append(
            {
                "status": V3_FLW_STATUS_DISPLAY.get(status_key, status_key),
                "status_key": status_key,
                "num_flws": len(flw_list),
                "total_cases": total_cases,
                "total_cases_eligible_at_registration": total_eligible,
                "total_cases_still_eligible": still_eligible,
                "pct_still_eligible": round(still_eligible / total_eligible * 100) if total_eligible > 0 else 0,
                "pct_missed_1_or_less_visits": round(missed_1_or_less / total_eligible * 100)
                if total_eligible > 0
                else 0,
                **milestone_results,
            }
        )

    return results
