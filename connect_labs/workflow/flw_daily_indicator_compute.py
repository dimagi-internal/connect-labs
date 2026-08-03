"""Pure computation functions for the FLW Daily Indicator Report (Program 176).

Every function here takes plain dicts/lists and returns plain dicts/lists — no
Django, no network, no pipeline objects — so the statistical logic can be
unit-tested directly. See connect_labs/workflow/templates/flw_daily_indicator_report.py
for the template that fetches pipeline rows and calls compute_flw_daily_indicators
per opportunity per FLW per day.

Field expectations on each visit row (already extracted by the `hsd_visits`
pipeline schema — see the template file for the exact FieldComputation paths):
    username, opportunity_id, form_display_name, muac_cm, hh_case_id,
    child_case_id, childs_dob, wa_caseid, child_name, normalized_lat,
    normalized_lon, time_start, time_end, received_any_vaccine,
    dw_child_unwell_today, diarrhea_last_month

``wa_building_counts`` maps wa_caseid -> building_count (float), sourced from
the `work_areas` (cchq_cases, case_type=work-area) pipeline.

This report deliberately computes RAW indicator values only — no per-day
pass/fail flag. The OUTER, tunable "is this FLW worth investigating today"
cutoffs live entirely in flw_daily_indicator_table.py's THRESHOLDS constant,
so they can be retuned without recomputing (or even redeploying) this report.
The few constants below are DEFINITIONAL (they describe what the metric IS —
e.g. what counts as a "large" household — not how suspicious a value has to
be to flag someone) and intentionally live here, the same way
flw_audit_compute.py bakes in its own near-duplicate radii.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

MIN_FORMS_FOR_RATE_INDICATORS = 8

# --- Definitional constants (what the metric measures, not the per-day cutoff) ---
HOUSEHOLD_CHILD_COUNT_THRESHOLD = 4  # a household counts as "large" at 4+ children
GAP_MINUTES_THRESHOLD = 2.0  # a form-to-form gap counts as "rushed" under 2 minutes


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


def _round(value, ndigits=2):
    return round(value, ndigits) if isinstance(value, (int, float)) else value


def _mode_share_with_n(values) -> tuple[float | None, int]:
    """Share (0-100) of non-null values equal to the most common value, plus
    the non-null sample size `n` (so callers can gate on MIN_FORMS_FOR_RATE_INDICATORS
    against the field's OWN answered count, not the day's total form count —
    a form can skip a question via branching/exemption logic)."""
    clean = [v for v in values if v not in (None, "")]
    n = len(clean)
    if n == 0:
        return None, 0
    counts: dict = defaultdict(int)
    for v in clean:
        counts[v] += 1
    return _round(max(counts.values()) / n * 100.0), n


def compute_flw_daily_indicators(visits: list[dict], wa_building_counts: dict[str, float] | None = None) -> dict:
    """Compute every Workflow-1 daily indicator for ONE FLW's visits on ONE day.

    ``visits`` must already be filtered to this FLW, this opportunity, this
    calendar day's window, and form_display_name == "Health Service Delivery"
    (the template does this filtering before grouping by username). Each dict
    must carry the raw pipeline field names listed in the module docstring.
    """
    wa_building_counts = wa_building_counts or {}

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
        row["_muac_cm"] = _to_float(v.get("muac_cm"))
        parsed.append(row)
    parsed.sort(key=lambda r: r["_time_start"])

    total_forms = len(parsed)
    unique_work_areas_count = len({r.get("wa_caseid") for r in parsed if r.get("wa_caseid")})

    # --- #2 households registered per building, grouped by work area ---
    # Distinct households, not raw form count -- a single large household
    # visited many times (many children) shouldn't look like over-coverage of
    # the work area the way a form-count ratio would.
    households_by_wa: dict[str, set] = defaultdict(set)
    for r in parsed:
        wa = r.get("wa_caseid")
        hh = r.get("hh_case_id")
        if wa and hh:
            households_by_wa[wa].add(hh)
    by_wa = []
    max_ratio = None
    for wa, households in households_by_wa.items():
        building_count = wa_building_counts.get(wa)
        ratio = (len(households) / building_count) if building_count else None
        by_wa.append(
            {"wa_caseid": wa, "households": len(households), "building_count": building_count, "ratio": _round(ratio)}
        )
        if ratio is not None and (max_ratio is None or ratio > max_ratio):
            max_ratio = ratio
    households_per_building = {"max_ratio": _round(max_ratio), "by_wa": by_wa}

    # --- daily span: first visit's start to last visit's end, for the
    # "30+ visits compressed into <=1hr" indicator (workflow 2 applies both
    # cutoffs; this just reports the raw span) ---
    daily_span_minutes = None
    if parsed:
        daily_span_minutes = _round(
            (max(r["_time_end"] for r in parsed) - parsed[0]["_time_start"]).total_seconds() / 60.0
        )

    # --- #3 households with HOUSEHOLD_CHILD_COUNT_THRESHOLD+ distinct children ---
    children_by_hh: dict[str, set] = defaultdict(set)
    for r in parsed:
        hh = r.get("hh_case_id")
        cid = r.get("child_case_id")
        if hh and cid:
            children_by_hh[hh].add(cid)
    households_4plus_children_count = sum(
        1 for kids in children_by_hh.values() if len(kids) >= HOUSEHOLD_CHILD_COUNT_THRESHOLD
    )

    # --- #4 gap < 2min (consecutive same-day visits) ---
    gap_lt_2min_count = 0
    for prev, curr in zip(parsed, parsed[1:]):
        gap_minutes = (curr["_time_start"] - prev["_time_end"]).total_seconds() / 60.0
        if gap_minutes < GAP_MINUTES_THRESHOLD:
            gap_lt_2min_count += 1

    # --- #5 % received_any_vaccine == "yes" ---
    vaccine_answers = [r.get("received_any_vaccine") for r in parsed if r.get("received_any_vaccine") in ("yes", "no")]
    vaccine_forms_count = len(vaccine_answers)
    vaccine_yes_pct = (
        _round(sum(1 for a in vaccine_answers if a == "yes") / vaccine_forms_count * 100.0)
        if vaccine_forms_count >= MIN_FORMS_FOR_RATE_INDICATORS
        else None
    )

    # --- #6 camping: % of the day's GPS-tagged forms sharing the single most-
    # repeated EXACT (lat, lon) reading. Ordinary GPS noise means a device
    # that's actually re-acquiring location on each form almost never returns
    # the identical fix twice -- repetition here means the location wasn't
    # really refreshed (a cached/stale fix reused across forms), which is a
    # tighter signal than "visits were merely near each other."
    gps_values = [(r["_lat"], r["_lon"]) for r in parsed if r["_lat"] is not None and r["_lon"] is not None]
    camping_repeat_pct, camping_forms_count = _mode_share_with_n(gps_values)
    if camping_forms_count < MIN_FORMS_FOR_RATE_INDICATORS:
        camping_repeat_pct = None

    # --- #8/#9 duplicate child name / age (DOB) reused across different households ---
    # Same-household repeats don't count (e.g. legitimate twins/siblings) --
    # only a name or DOB turning up under two or more DIFFERENT household IDs,
    # matching flw_audit_compute.py's own duplicate_child_count convention
    # (same-DOB-across-households, not same-DOB-within-household).
    name_to_hhs: dict[str, set] = defaultdict(set)
    dob_to_hhs: dict[str, set] = defaultdict(set)
    for r in parsed:
        hh = r.get("hh_case_id")
        if not hh:
            continue
        name = (r.get("child_name") or "").strip().lower()
        if name:
            name_to_hhs[name].add(hh)
        dob = (r.get("childs_dob") or "").strip()
        if dob:
            dob_to_hhs[dob].add(hh)
    duplicate_child_names_count = sum(1 for hhs in name_to_hhs.values() if len(hhs) > 1)
    duplicate_child_ages_count = sum(1 for hhs in dob_to_hhs.values() if len(hhs) > 1)

    # --- #9 straight-lining on dw_child_unwell_today / diarrhea_last_month ---
    dw_pct, dw_n = _mode_share_with_n([r.get("dw_child_unwell_today") for r in parsed])
    diarrhea_pct, diarrhea_n = _mode_share_with_n([r.get("diarrhea_last_month") for r in parsed])
    straight_line_pct = {
        "dw_child_unwell_today": dw_pct if dw_n >= MIN_FORMS_FOR_RATE_INDICATORS else None,
        "diarrhea_last_month": diarrhea_pct if diarrhea_n >= MIN_FORMS_FOR_RATE_INDICATORS else None,
    }
    straight_line_forms_count = {"dw_child_unwell_today": dw_n, "diarrhea_last_month": diarrhea_n}

    # --- #10 MUAC value repetition (soliciter_muac_cm only) ---
    muac_values = [r["_muac_cm"] for r in parsed if r["_muac_cm"] is not None]
    muac_repetition_pct, muac_n = _mode_share_with_n(muac_values)
    if muac_n < MIN_FORMS_FOR_RATE_INDICATORS:
        muac_repetition_pct = None

    return {
        "total_forms": total_forms,
        "unique_work_areas_count": unique_work_areas_count,
        "daily_span_minutes": daily_span_minutes,
        "households_per_building": households_per_building,
        "households_4plus_children_count": households_4plus_children_count,
        "gap_lt_2min_count": gap_lt_2min_count,
        "vaccine_yes_pct": vaccine_yes_pct,
        "vaccine_forms_count": vaccine_forms_count,
        "camping_repeat_pct": camping_repeat_pct,
        "camping_forms_count": camping_forms_count,
        "duplicate_child_names_count": duplicate_child_names_count,
        "duplicate_child_ages_count": duplicate_child_ages_count,
        "straight_line_pct": straight_line_pct,
        "straight_line_forms_count": straight_line_forms_count,
        "muac_repetition_pct": muac_repetition_pct,
        "muac_forms_count": muac_n,
    }
