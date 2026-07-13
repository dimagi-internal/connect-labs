"""Pure computation functions for the FLW Weekly Audit Report (Program 176).

Every function here takes plain dicts/lists and returns plain dicts/lists —
no Django, no network, no pipeline objects — so the statistical logic can be
unit-tested directly without a live CommCare/Connect connection. See
connect_labs/workflow/templates/flw_weekly_audit_report.py for the template
that fetches pipeline rows and calls compute_flw_indicators per opportunity.

Field expectations on each visit row (already extracted by the `hsd_visits`
pipeline schema — see the template file for the exact FieldComputation paths):
    username, opportunity_id, form_display_name, muac_cm, muac_colour,
    childs_gender, childs_dob, age_months, age_days, hh_case_id,
    child_case_id, wa_caseid, current_accuracy, accuracy_minimum,
    normalized_lat, normalized_lon, time_start, time_end,
    all_service_del_checks
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone

WAT_OFFSET = timedelta(hours=1)  # Africa/Lagos, fixed UTC+1, no DST

FORM_NAME = "Health Service Delivery"

# --- Fraud/data-quality thresholds (placeholders, see flw_audit_workflows_spec.md Q10) ---
GPS_ACCURACY_MAX_M = 100.0
GPS_NEAR_DUPLICATE_MAX_M = 10.0
IMPLIED_SPEED_MAX_KMH = 60.0
FORM_DURATION_MIN_MINUTES = 2.0
GAP_MINUTES_THRESHOLD = 3.0
MUAC_CLUSTER_RATIO_THRESHOLD = 2.0
WHIPPLE_INDEX_THRESHOLD = 125.0
HEAPING_MONTHS = (12, 24, 36, 48)

# --- Bucket definitions ---
AGE_MONTH_BUCKETS = list(range(60))  # 0..59, literal single-month
MUAC_BUCKET_LOW_CM = 6.0
MUAC_BUCKET_HIGH_CM = 20.0
MUAC_BUCKET_STEP_CM = 0.5


def _parse_dt(value) -> datetime | None:
    """Parse an ISO timestamp string (with or without trailing Z) to an aware UTC datetime."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _to_float(value) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (ValueError, TypeError):
        return None


def _to_int(value) -> int | None:
    f = _to_float(value)
    return int(round(f)) if f is not None else None


def wat_date(dt: datetime) -> str:
    """Calendar date in Africa/Lagos (UTC+1), as an ISO date string."""
    return (dt + WAT_OFFSET).date().isoformat()


def haversine_meters(lat1, lon1, lat2, lon2) -> float | None:
    """Great-circle distance between two lat/lon points, in meters."""
    if None in (lat1, lon1, lat2, lon2):
        return None
    r = 6371000.0  # Earth radius, meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def muac_bucket_label(muac_cm: float) -> str:
    if muac_cm < MUAC_BUCKET_LOW_CM:
        return f"<{MUAC_BUCKET_LOW_CM}"
    if muac_cm >= MUAC_BUCKET_HIGH_CM:
        return f">={MUAC_BUCKET_HIGH_CM}"
    lower = MUAC_BUCKET_LOW_CM + math.floor((muac_cm - MUAC_BUCKET_LOW_CM) / MUAC_BUCKET_STEP_CM) * MUAC_BUCKET_STEP_CM
    upper = lower + MUAC_BUCKET_STEP_CM
    return f"{lower:.1f}-{upper:.1f}"


def whipple_index(ages_in_months: list[int]) -> float | None:
    """Whipple-style heaping index for ages clustering at 12/24/36/48 months
    (adapted from the classic digit-preference index — 100 = no heaping,
    higher = more heaping toward the 4 milestone ages out of the 60 possible
    single-month values in 0-59)."""
    total = len(ages_in_months)
    if total == 0:
        return None
    heaped = sum(1 for a in ages_in_months if a in HEAPING_MONTHS)
    expected_fraction = len(HEAPING_MONTHS) / 60.0
    observed_fraction = heaped / total
    if expected_fraction == 0:
        return None
    return (observed_fraction / expected_fraction) * 100.0


def muac_cutoff_clustering_flags(muac_values_mm_rounded: list[int]) -> dict:
    """For each SAM/MAM cutoff (115mm/125mm), compare the count of values
    landing exactly on the cutoff against the average of the 4 neighboring
    1mm bins (cutoff-2, cutoff-1, cutoff+1, cutoff+2). Flag if the cutoff
    count exceeds MUAC_CLUSTER_RATIO_THRESHOLD times that neighbor average.
    ``muac_values_mm_rounded`` are MUAC readings in mm, rounded to the
    nearest mm (i.e. int(round(muac_cm * 10)))."""
    counts: dict[int, int] = defaultdict(int)
    for v in muac_values_mm_rounded:
        counts[v] += 1

    result = {}
    for cutoff in (115, 125):
        neighbors = [counts.get(cutoff + d, 0) for d in (-2, -1, 1, 2)]
        neighbor_avg = sum(neighbors) / len(neighbors)
        cutoff_count = counts.get(cutoff, 0)
        ratio = (cutoff_count / neighbor_avg) if neighbor_avg > 0 else (float("inf") if cutoff_count > 0 else 0.0)
        result[str(cutoff)] = {
            "cutoff_count": cutoff_count,
            "neighbor_avg": round(neighbor_avg, 2),
            "flagged": ratio > MUAC_CLUSTER_RATIO_THRESHOLD,
        }
    return result


def _dedup_children_this_week(visits: list[dict]) -> dict[str, dict]:
    """One representative visit per distinct child_case_id this week (first
    by time_start), for demographic/household indicators that shouldn't
    double-count a child visited more than once in the same week."""
    by_child: dict[str, dict] = {}
    for v in sorted(visits, key=lambda r: r["_time_start"]):
        cid = v.get("child_case_id")
        if not cid:
            continue
        by_child.setdefault(cid, v)
    return by_child


def compute_flw_indicators(visits: list[dict]) -> dict:
    """Compute every Workflow-1 indicator for ONE FLW's visits in ONE week.

    ``visits`` must already be filtered to this FLW, this opportunity, this
    week's window, and form_display_name == "Health Service Delivery" (the
    template does this filtering before grouping by username). Each dict
    must carry the raw pipeline field names listed in the module docstring.
    """
    parsed = []
    for v in visits:
        ts = _parse_dt(v.get("time_start"))
        te = _parse_dt(v.get("time_end"))
        if ts is None:
            continue
        row = dict(v)
        row["_time_start"] = ts
        row["_time_end"] = te or ts
        row["_lat"] = _to_float(v.get("normalized_lat"))
        row["_lon"] = _to_float(v.get("normalized_lon"))
        row["_accuracy"] = _to_float(v.get("current_accuracy"))
        row["_muac_cm"] = _to_float(v.get("muac_cm"))
        row["_age_months"] = _to_int(v.get("age_months"))
        parsed.append(row)

    total_forms = len(parsed)

    # Every "difference between visits" indicator (gap in time, distance, implied
    # speed) must only compare consecutive visits WITHIN the same calendar day —
    # comparing the last visit of one working day to the first visit of the next
    # (which could be many hours apart, spanning the FLW's off-hours/overnight)
    # is not a real "gap between forms" and previously inflated/distorted these
    # numbers. Build the day-grouping once and reuse it for every per-day and
    # per-week-pooled-from-per-day computation below (days_worked, daily span,
    # gap/distance/speed, near-duplicate GPS) instead of computing it twice.
    by_day: dict[str, list[dict]] = defaultdict(list)
    for r in parsed:
        by_day[wat_date(r["_time_start"])].append(r)
    for day_rows in by_day.values():
        day_rows.sort(key=lambda r: r["_time_start"])

    # --- Visit timing / cadence ---
    # Pairs are only ever formed between consecutive visits on the SAME day, then
    # pooled across the week's days into single week-level statistics (a true
    # median/average over every same-day gap this week, not an average of daily
    # medians) — cross-day transitions are never compared at all.
    gaps_minutes = []
    distances_m = []
    speed_flags = 0
    for day_rows in by_day.values():
        for prev, curr in zip(day_rows, day_rows[1:]):
            gap = (curr["_time_start"] - prev["_time_end"]).total_seconds() / 60.0
            gaps_minutes.append(gap)
            dist = haversine_meters(prev["_lat"], prev["_lon"], curr["_lat"], curr["_lon"])
            if dist is not None:
                distances_m.append(dist)
                hours = (curr["_time_start"] - prev["_time_start"]).total_seconds() / 3600.0
                if hours > 0 and (dist / 1000.0) / hours > IMPLIED_SPEED_MAX_KMH:
                    speed_flags += 1

    pct_gap_lt_3min = (
        (sum(1 for g in gaps_minutes if g < GAP_MINUTES_THRESHOLD) / len(gaps_minutes) * 100.0)
        if gaps_minutes
        else None
    )
    median_gap_minutes = statistics.median(gaps_minutes) if gaps_minutes else None
    avg_distance_m = statistics.mean(distances_m) if distances_m else None

    daily_spans_minutes = []
    for day_rows in by_day.values():
        start = min(r["_time_start"] for r in day_rows)
        end = max(r["_time_end"] for r in day_rows)
        daily_spans_minutes.append((end - start).total_seconds() / 60.0)
    avg_daily_span_minutes = statistics.mean(daily_spans_minutes) if daily_spans_minutes else None
    days_worked = len(by_day)

    # --- Fraud / fake-visit indicators ---
    gps_accuracy_flags = sum(
        1
        for r in parsed
        if r["_accuracy"] is not None and (r["_accuracy"] > GPS_ACCURACY_MAX_M or r["_accuracy"] == 0)
    )
    duration_outliers = sum(
        1 for r in parsed if (r["_time_end"] - r["_time_start"]).total_seconds() / 60.0 < FORM_DURATION_MIN_MINUTES
    )

    # NOTE on near_duplicate_count: this counts every PAIR of same-day visits
    # under different households within GPS_NEAR_DUPLICATE_MAX_M, not distinct
    # flagged visits — a day with k visits genuinely clustered together (e.g. a
    # dense compound with several officially-separate households) produces
    # C(k,2) pairs, which grows quadratically and can look alarmingly high even
    # when every visit is legitimate. Revisit this counting method (e.g. count
    # distinct visits involved in at least one near pair, not every pair) once
    # a few weeks of real distributions are visible — see flw_audit_workflows_spec.md Q10.
    near_duplicate_count = 0
    for day_rows in by_day.values():
        for i, a in enumerate(day_rows):
            for b in day_rows[i + 1 :]:
                if a.get("hh_case_id") and b.get("hh_case_id") and a["hh_case_id"] != b["hh_case_id"]:
                    d = haversine_meters(a["_lat"], a["_lon"], b["_lat"], b["_lon"])
                    if d is not None and d < GPS_NEAR_DUPLICATE_MAX_M:
                        near_duplicate_count += 1

    muac_mm_values = [int(round(r["_muac_cm"] * 10)) for r in parsed if r["_muac_cm"] is not None]
    muac_clustering = muac_cutoff_clustering_flags(muac_mm_values)

    children_this_week = _dedup_children_this_week(parsed)
    ages_for_heaping = [c["_age_months"] for c in children_this_week.values() if c["_age_months"] is not None]
    whipple = whipple_index(ages_for_heaping)

    dob_by_household: dict[str, list[str]] = defaultdict(list)
    dob_to_households: dict[str, set] = defaultdict(set)
    for c in children_this_week.values():
        hh = c.get("hh_case_id")
        dob = c.get("childs_dob")
        if hh and dob:
            dob_by_household[hh].append(dob)
        if dob:
            dob_to_households[dob].add(hh)
    households_with_dup_dob = sum(1 for dobs in dob_by_household.values() if len(dobs) != len(set(dobs)))
    pct_same_dob_within_household = (
        (households_with_dup_dob / len(dob_by_household) * 100.0) if dob_by_household else None
    )
    duplicate_child_count = sum(1 for hhs in dob_to_households.values() if len(hhs) > 1)

    # --- Household / child composition ---
    households: dict[str, set] = defaultdict(set)
    for c in children_this_week.values():
        hh = c.get("hh_case_id")
        cid = c.get("child_case_id")
        if hh and cid:
            households[hh].add(cid)
    avg_children_per_household = (
        statistics.mean(len(children) for children in households.values()) if households else None
    )

    children_by_age_month = {str(m): 0 for m in AGE_MONTH_BUCKETS}
    for c in children_this_week.values():
        m = c["_age_months"]
        if m is not None and 0 <= m <= 59:
            children_by_age_month[str(m)] += 1

    children_by_muac_bucket: dict[str, int] = defaultdict(int)
    for c in children_this_week.values():
        if c["_muac_cm"] is not None:
            children_by_muac_bucket[muac_bucket_label(c["_muac_cm"])] += 1

    # --- Support-only: raw inputs for a later MUAC-for-age z-score analysis ---
    muacz_inputs = [
        {
            "age_days": _to_int(c.get("age_days")),
            "sex": c.get("childs_gender"),
            "muac_cm": c["_muac_cm"],
        }
        for c in children_this_week.values()
        if c["_muac_cm"] is not None and c.get("age_days")
    ]

    return {
        "total_service_delivery_forms": total_forms,
        "days_worked": days_worked,
        "pct_gap_lt_3min": _round(pct_gap_lt_3min),
        "median_gap_minutes": _round(median_gap_minutes),
        "avg_distance_between_visits_m": _round(avg_distance_m),
        "avg_time_first_last_visit_minutes": _round(avg_daily_span_minutes),
        "avg_children_per_household": _round(avg_children_per_household),
        "children_by_age_month": children_by_age_month,
        "children_by_muac_bucket": dict(children_by_muac_bucket),
        "pct_same_dob_within_household": _round(pct_same_dob_within_household),
        "fraud": {
            "gps_accuracy_flag_count": gps_accuracy_flags,
            "gps_accuracy_flag_pct": _round(gps_accuracy_flags / total_forms * 100.0) if total_forms else None,
            "gps_near_duplicate_count": near_duplicate_count,
            "implied_speed_flag_count": speed_flags,
            "form_duration_outlier_count": duration_outliers,
            "muac_cutoff_clustering": muac_clustering,
            "age_heaping_whipple_index": _round(whipple),
            "age_heaping_flagged": bool(whipple is not None and whipple > WHIPPLE_INDEX_THRESHOLD),
            "duplicate_child_count": duplicate_child_count,
        },
        "muacz_support_inputs": muacz_inputs,
    }


def _round(value, ndigits=2):
    return round(value, ndigits) if isinstance(value, (int, float)) else value
