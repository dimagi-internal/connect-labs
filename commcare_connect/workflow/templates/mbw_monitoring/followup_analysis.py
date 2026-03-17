"""
Follow-up visit analysis for MBW Monitoring Dashboard.

Calculates visit status (Completed On-Time, Completed Late, Due On-Time,
Due Late, Missed) and aggregates per-FLW and per-mother metrics.
"""

import logging
from collections import Counter, defaultdict
from datetime import date, timedelta

logger = logging.getLogger(__name__)

# Visit status constants
STATUS_COMPLETED_ON_TIME = "Completed - On Time"
STATUS_COMPLETED_LATE = "Completed - Late"
STATUS_DUE_ON_TIME = "Due - On Time"
STATUS_DUE_LATE = "Due - Late"
STATUS_MISSED = "Missed"
STATUS_NOT_DUE_YET = "Not Due Yet"

# Status color thresholds
THRESHOLD_GREEN = 80
THRESHOLD_YELLOW = 60

# Only count visits due 5+ days ago in follow-up rate denominator
GRACE_PERIOD_DAYS = 5

# Completion flag mapping: visit_type → property name
COMPLETION_FLAGS = {
    "ANC Visit": "antenatal_visit_completion",
    "Postnatal Visit": "postnatal_visit_completion",
    "Postnatal Delivery Visit": "postnatal_visit_completion",
    "1 Week Visit": "one_two_week_visit_completion",
    "1 Month Visit": "one_month_visit_completion",
    "3 Month Visit": "three_month_visit_completion",
    "6 Month Visit": "six_month_visit_completion",
}

# Visit type display names (normalize)
VISIT_TYPE_DISPLAY = {
    "ANC Visit": "ANC",
    "Postnatal Visit": "Postnatal",
    "Postnatal Delivery Visit": "Postnatal",
    "1 Week Visit": "Week 1",
    "1 Month Visit": "Month 1",
    "3 Month Visit": "Month 3",
    "6 Month Visit": "Month 6",
}

# Visit type keys for per-type breakdown
VISIT_TYPE_KEYS = ["anc", "postnatal", "week1", "month1", "month3", "month6"]
VISIT_TYPE_TO_KEY = {
    "ANC Visit": "anc",
    "Postnatal Visit": "postnatal",
    "Postnatal Delivery Visit": "postnatal",
    "1 Week Visit": "week1",
    "1 Month Visit": "month1",
    "3 Month Visit": "month3",
    "6 Month Visit": "month6",
}

# On-time window days per visit type (from MBW schedule spec).
# Most visits: 7 days from scheduled date. PNC: 4 days (delivery through day 4).
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


def _parse_date(date_str: str | None) -> date | None:
    """Parse a date string (YYYY-MM-DD) into a date object."""
    if not date_str:
        return None
    try:
        return date.fromisoformat(date_str[:10])
    except (ValueError, TypeError):
        return None


def _parse_bool(value) -> bool:
    """Parse a boolean-like value from case properties."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("yes", "true", "1", "completed", "ok")
    return bool(value)


def is_visit_completed(visit_case: dict) -> bool:
    """
    Check if a visit case is completed based on its type-specific completion flag.

    Args:
        visit_case: Case dict from CommCare HQ

    Returns:
        True if the visit is marked as completed
    """
    props = visit_case.get("properties", {})
    visit_type = props.get("visit_type", "")

    flag_name = COMPLETION_FLAGS.get(visit_type)
    if not flag_name:
        return False

    return _parse_bool(props.get(flag_name))


def calculate_visit_status(visit_case: dict, current_date: date) -> str:
    """
    Determine the status of a visit case.

    Categories:
    - Completed - On Time: Completed within the on-time window
    - Completed - Late: Completed after on-time window but before expiry
    - Due - On Time: Not completed, currently within on-time window
    - Due - Late: Not completed, past on-time window but before expiry
    - Missed: Not completed and past expiry date
    - Not Due Yet: Not completed and not yet within the on-time window

    Args:
        visit_case: Case dict from CommCare HQ with properties
        current_date: Reference date for status calculation

    Returns:
        Status string (one of the 6 categories)
    """
    props = visit_case.get("properties", {})
    visit_type = props.get("visit_type", "")
    scheduled_date = _parse_date(props.get("visit_date_scheduled"))
    expiry_date = _parse_date(props.get("visit_expiry_date"))
    completed = is_visit_completed(visit_case)

    # If we can't parse dates, default to Unknown handling
    if not scheduled_date:
        if completed:
            return STATUS_COMPLETED_LATE
        return STATUS_DUE_ON_TIME

    # On-time window varies by visit type (PNC = 4 days, others = 7 days)
    on_time_days = VISIT_ON_TIME_DAYS.get(visit_type, DEFAULT_ON_TIME_DAYS)
    on_time_end = scheduled_date + timedelta(days=on_time_days)

    if completed:
        # Try to get completion date from case modified date
        modified_date = _parse_date(visit_case.get("date_modified") or visit_case.get("server_date_modified"))

        if modified_date and modified_date <= on_time_end:
            return STATUS_COMPLETED_ON_TIME
        return STATUS_COMPLETED_LATE

    # Not completed
    if expiry_date and current_date > expiry_date:
        return STATUS_MISSED

    if current_date < scheduled_date:
        return STATUS_NOT_DUE_YET

    if current_date <= on_time_end:
        return STATUS_DUE_ON_TIME

    return STATUS_DUE_LATE


def aggregate_flw_followup(
    visit_cases_by_flw: dict[str, list[dict]],
    current_date: date,
    flw_names: dict[str, str] | None = None,
    mother_cases_map: dict[str, dict] | None = None,
) -> list[dict]:
    """
    Aggregate follow-up metrics per FLW.

    Args:
        visit_cases_by_flw: Dict mapping username to list of visit case dicts
        current_date: Reference date for status calculations
        flw_names: Optional dict mapping username to display name
        mother_cases_map: Optional dict mapping mother_case_id to mother case dict
            (used for eligibility filtering in rate calculation)

    Returns:
        List of per-FLW summary dicts, sorted by completion rate ascending
    """
    flw_names = flw_names or {}
    summaries = []

    for username, cases in visit_cases_by_flw.items():
        summary = _build_flw_summary(username, cases, current_date, flw_names, mother_cases_map)
        summaries.append(summary)

    # Sort by completion rate ascending (worst performers first)
    summaries.sort(key=lambda s: s["completion_rate"])

    return summaries


def _build_flw_summary(
    username: str,
    cases: list[dict],
    current_date: date,
    flw_names: dict[str, str],
    mother_cases_map: dict[str, dict] | None = None,
) -> dict:
    """Build a follow-up summary for one FLW."""
    display_name = flw_names.get(username, username)
    mother_cases_map = mother_cases_map or {}
    grace_cutoff = current_date - timedelta(days=GRACE_PERIOD_DAYS)

    def _is_eligible(case):
        """Check if the mother for this visit is eligible for full intervention bonus."""
        mid = case.get("properties", {}).get("mother_case_id", "")
        mc = mother_cases_map.get(mid, {})
        return mc.get("properties", {}).get("eligible_full_intervention_bonus") == "1"

    def _is_due_past_grace(case):
        """Check if the visit's scheduled date is 5+ days ago."""
        sched = _parse_date(case.get("properties", {}).get("visit_date_scheduled"))
        return sched is not None and sched <= grace_cutoff

    # Initialize counters
    completed_on_time = 0
    completed_late = 0
    due_on_time = 0
    due_late = 0
    missed = 0

    # Per-visit-type counters
    type_counts = {}
    for key in VISIT_TYPE_KEYS:
        type_counts[key] = {
            "completed_on_time": 0,
            "completed_late": 0,
            "due_on_time": 0,
            "due_late": 0,
            "missed": 0,
        }

    for case in cases:
        status = calculate_visit_status(case, current_date)
        visit_type = case.get("properties", {}).get("visit_type", "")
        type_key = VISIT_TYPE_TO_KEY.get(visit_type)

        if status == STATUS_COMPLETED_ON_TIME:
            completed_on_time += 1
            if type_key:
                type_counts[type_key]["completed_on_time"] += 1
        elif status == STATUS_COMPLETED_LATE:
            completed_late += 1
            if type_key:
                type_counts[type_key]["completed_late"] += 1
        elif status == STATUS_DUE_ON_TIME:
            due_on_time += 1
            if type_key:
                type_counts[type_key]["due_on_time"] += 1
        elif status == STATUS_DUE_LATE:
            due_late += 1
            if type_key:
                type_counts[type_key]["due_late"] += 1
        elif status == STATUS_MISSED:
            missed += 1
            if type_key:
                type_counts[type_key]["missed"] += 1

    completed_total = completed_on_time + completed_late
    due_total = due_on_time + due_late
    missed_total = missed
    total_visits = completed_total + due_total + missed_total

    # Filtered follow-up rate (business definition):
    # % of visits due 5+ days ago that have been completed,
    # among mothers eligible for full intervention bonus
    filtered_completed = 0
    filtered_denominator = 0
    for case in cases:
        if not _is_eligible(case):
            continue
        if not _is_due_past_grace(case):
            continue
        filtered_denominator += 1
        if is_visit_completed(case):
            filtered_completed += 1

    completion_rate = round((filtered_completed / filtered_denominator) * 100) if filtered_denominator > 0 else 0

    # Status color
    if completion_rate >= THRESHOLD_GREEN:
        status_color = "green"
    elif completion_rate >= THRESHOLD_YELLOW:
        status_color = "yellow"
    else:
        status_color = "red"

    summary = {
        "username": username,
        "display_name": display_name,
        "completed_on_time": completed_on_time,
        "completed_late": completed_late,
        "due_on_time": due_on_time,
        "due_late": due_late,
        "missed": missed,
        "completed_total": completed_total,
        "due_total": due_total,
        "missed_total": missed_total,
        "total_visits": total_visits,
        "completion_rate": completion_rate,
        "status_color": status_color,
    }

    # Add per-visit-type breakdown
    for key in VISIT_TYPE_KEYS:
        for status_key, count in type_counts[key].items():
            summary[f"{key}_{status_key}"] = count

    return summary


_STATUS_KEYS = [
    "completed_on_time",
    "completed_late",
    "due_on_time",
    "due_late",
    "missed",
    "not_due_yet",
]

_STATUS_TO_KEY = {
    STATUS_COMPLETED_ON_TIME: "completed_on_time",
    STATUS_COMPLETED_LATE: "completed_late",
    STATUS_DUE_ON_TIME: "due_on_time",
    STATUS_DUE_LATE: "due_late",
    STATUS_MISSED: "missed",
    STATUS_NOT_DUE_YET: "not_due_yet",
}

# Reverse lookup: visit type key -> display name for chart labels
_VISIT_TYPE_KEY_TO_DISPLAY = {
    "anc": "ANC",
    "postnatal": "Postnatal",
    "week1": "Week 1",
    "month1": "Month 1",
    "month3": "Month 3",
    "month6": "Month 6",
}


def aggregate_visit_status_distribution(
    visit_cases_by_flw: dict[str, list[dict]],
    current_date: date,
) -> dict:
    """
    Aggregate visit status distribution per visit type across all FLWs.

    Returns:
        Dict with ``by_visit_type`` (list of per-type counts) and ``totals``.
    """
    # Initialise counters per visit-type key
    by_type: dict[str, dict[str, int]] = {}
    for vt_key in VISIT_TYPE_KEYS:
        by_type[vt_key] = {sk: 0 for sk in _STATUS_KEYS}

    totals = {sk: 0 for sk in _STATUS_KEYS}

    for cases in visit_cases_by_flw.values():
        for case in cases:
            props = case.get("properties", {})
            visit_type = props.get("visit_type", "")
            vt_key = VISIT_TYPE_TO_KEY.get(visit_type)
            if not vt_key:
                continue

            status = calculate_visit_status(case, current_date)
            status_key = _STATUS_TO_KEY.get(status)
            if not status_key:
                continue

            by_type[vt_key][status_key] += 1
            totals[status_key] += 1

    # Build ordered list for the frontend
    by_visit_type = []
    for vt_key in VISIT_TYPE_KEYS:
        counts = by_type[vt_key]
        total = sum(counts.values())
        by_visit_type.append(
            {
                "visit_type": _VISIT_TYPE_KEY_TO_DISPLAY.get(vt_key, vt_key),
                **counts,
                "total": total,
            }
        )

    totals["total"] = sum(totals.values())
    return {"by_visit_type": by_visit_type, "totals": totals}


def aggregate_mother_metrics(
    visit_cases: list[dict],
    current_date: date,
    mother_cases_map: dict[str, dict] | None = None,
    anc_date_by_mother: dict[str, str] | None = None,
    pnc_date_by_mother: dict[str, str] | None = None,
    baby_dob_by_mother: dict[str, str] | None = None,
) -> list[dict]:
    """
    Aggregate follow-up metrics per mother case for drill-down view.

    Args:
        visit_cases: List of visit case dicts for one FLW
        current_date: Reference date
        mother_cases_map: Optional dict mapping mother_case_id to mother case dict
        anc_date_by_mother: ANC completion dates from pipeline rows
        pnc_date_by_mother: PNC completion dates from pipeline rows
        baby_dob_by_mother: Baby DOB from pipeline PNC rows

    Returns:
        List of per-mother summary dicts
    """
    mother_cases_map = mother_cases_map or {}
    anc_date_by_mother = anc_date_by_mother or {}
    pnc_date_by_mother = pnc_date_by_mother or {}
    baby_dob_by_mother = baby_dob_by_mother or {}

    by_mother = defaultdict(list)
    for case in visit_cases:
        mother_id = case.get("properties", {}).get("mother_case_id", "unknown")
        by_mother[mother_id].append(case)

    mothers = []
    for mother_id, cases in by_mother.items():
        all_visits = _build_visit_details(cases, current_date)
        has_due_visits = any(v["status"] in (STATUS_DUE_ON_TIME, STATUS_DUE_LATE) for v in all_visits)

        # Mother metadata from mother case
        mother_case = mother_cases_map.get(mother_id, {})
        mother_props = mother_case.get("properties", {})

        # Check mother eligibility
        is_eligible = mother_props.get("eligible_full_intervention_bonus") == "1"

        # Mother-level rate: simple completed / total for all mothers
        completed = sum(1 for c in cases if is_visit_completed(c))
        total = len(cases)

        rate = round((completed / total) * 100) if total > 0 else 0

        mothers.append(
            {
                "mother_case_id": mother_id,
                "mother_name": mother_case.get("case_name") or mother_props.get("mother_name", ""),
                "registration_date": (mother_case.get("date_opened") or "")[:10] or "",
                "age": mother_props.get("age") or mother_props.get("mother_age", ""),
                "phone_number": mother_props.get("phone_number") or mother_props.get("contact_phone", ""),
                "household_size": mother_props.get("household_size", ""),
                "preferred_time_of_visit": mother_props.get("preferred_time_of_visit", ""),
                "anc_completion_date": anc_date_by_mother.get(mother_id, ""),
                "pnc_completion_date": pnc_date_by_mother.get(mother_id, ""),
                "expected_delivery_date": mother_props.get("expected_delivery_date", ""),
                "baby_dob": baby_dob_by_mother.get(mother_id, ""),
                "eligible": is_eligible,
                "completed": completed,
                "total": total,
                "total_visits": len(cases),
                "follow_up_rate": rate,
                "has_due_visits": has_due_visits,
                "visits": all_visits,
            }
        )

    # Sort by follow-up rate ascending (worst first)
    mothers.sort(key=lambda m: m["follow_up_rate"])
    return mothers


def _build_visit_details(cases: list[dict], current_date: date) -> list[dict]:
    """Build detail rows for visits within a mother group (includes all statuses)."""
    details = []
    for case in cases:
        props = case.get("properties", {})
        status = calculate_visit_status(case, current_date)

        details.append(
            {
                "case_id": case.get("case_id"),
                "visit_type": VISIT_TYPE_DISPLAY.get(props.get("visit_type", ""), props.get("visit_type", "")),
                "visit_date_scheduled": props.get("visit_date_scheduled"),
                "visit_expiry_date": props.get("visit_expiry_date"),
                "status": status,
            }
        )

    # Sort by scheduled date, then visit type
    details.sort(key=lambda d: (d.get("visit_date_scheduled") or "", d.get("visit_type", "")))
    return details


# Visit type → form-level create flag name (used in Register Mother forms)
VISIT_CREATE_FLAGS = {
    "ANC Visit": "create_antenatal_visit",
    "Postnatal Delivery Visit": "create_postnatal_visit",
    "1 Week Visit": "create_one_two_visit",
    "1 Month Visit": "create_one_month_visit",
    "3 Month Visit": "create_three_month_visit",
    "6 Month Visit": "create_six_month_visit",
}

# Normalize pipeline form_name values to canonical visit_type names
FORM_NAME_TO_VISIT_TYPE = {
    "ANC Visit": "ANC Visit",
    "ANC Visit ": "ANC Visit",  # trailing space variant
    "Post delivery visit": "Postnatal Delivery Visit",
    "Postnatal Delivery Visit": "Postnatal Delivery Visit",
    "Postnatal Visit": "Postnatal Visit",
    "1 Week Visit": "1 Week Visit",
    "1 Month Visit": "1 Month Visit",
    "3 Month Visit": "3 Month Visit",
    "6 Month Visit": "6 Month Visit",
}


def _extract_schedules_from_registration_form(form_dict: dict) -> list[dict]:
    """Extract visit schedules from a CCHQ registration form dict.

    Parses var_visit_1..6 blocks, checking create flags.

    Args:
        form_dict: A form dict from CCHQ Form API v1 (has "form" key with form data)

    Returns:
        List of schedule dicts with visit_type, dates, and mother_case_id
    """
    form = form_dict.get("form", {})
    schedules = []
    for i in range(1, 7):
        var_visit = form.get(f"var_visit_{i}")
        if not var_visit or not isinstance(var_visit, dict):
            continue

        visit_type = var_visit.get("visit_type", "")
        create_flag_name = VISIT_CREATE_FLAGS.get(visit_type)
        if create_flag_name:
            if str(var_visit.get(create_flag_name, "")) != "1":
                continue  # Visit not created for this mother

        schedules.append(
            {
                "visit_type": visit_type,
                "visit_date_scheduled": var_visit.get("visit_date_scheduled", ""),
                "visit_expiry_date": var_visit.get("visit_expiry_date", ""),
                "mother_case_id": var_visit.get("mother_case_id", ""),
            }
        )
    return schedules


def extract_mother_metadata_from_forms(
    registration_forms: list[dict],
    current_date: date | None = None,
) -> dict[str, dict]:
    """Extract mother metadata from CCHQ registration forms.

    Parses each registration form to find the mother_case_id (from var_visit blocks)
    and mother metadata from the ``mother_details`` group.

    Returns:
        Dict mapping mother_case_id to pseudo-case dict compatible with
        aggregate_mother_metrics()'s mother_cases_map parameter.
    """
    metadata_map: dict[str, dict] = {}
    if current_date is None:
        current_date = date.today()

    for i, form_dict in enumerate(registration_forms):
        form = form_dict.get("form", {})

        if i == 0:
            logger.info(
                "[MBW Metadata] First registration form top-level keys: %s",
                list(form.keys()),
            )

        # Find mother_case_id from the first var_visit block that has one
        mother_case_id = ""
        for j in range(1, 7):
            var_visit = form.get(f"var_visit_{j}")
            if isinstance(var_visit, dict):
                mid = var_visit.get("mother_case_id", "")
                if mid:
                    mother_case_id = mid
                    break

        if not mother_case_id or mother_case_id in metadata_map:
            continue

        # Extract metadata from mother_details group (nested, not top-level)
        md = form.get("mother_details", {})
        if not isinstance(md, dict):
            md = {}

        if i == 0 and md:
            logger.info("[MBW Metadata] mother_details keys: %s", list(md.keys()))

        name = (
            md.get("format_mother_name")
            or md.get("mother_full_name")
            or f'{md.get("mother_name", "")} {md.get("mother_surname", "")}'.strip()
            or ""
        )
        phone = md.get("phone_number") or md.get("back_up_phone_number") or ""

        # Age: compute from DOB if available, otherwise fall back to recorded age
        age = ""
        mother_dob = md.get("mother_dob") or ""
        if mother_dob:
            try:
                dob = date.fromisoformat(mother_dob)
                age = str(
                    current_date.year - dob.year - ((current_date.month, current_date.day) < (dob.month, dob.day))
                )
            except (ValueError, TypeError):
                age = md.get("age_in_years_rounded") or md.get("mothers_age") or ""
        else:
            age = md.get("age_in_years_rounded") or md.get("mothers_age") or ""

        # Household size and eligibility from top-level fields
        household_size = form.get("number_of_other_household_members") or ""
        eligible = form.get("eligible_full_intervention_bonus", "")

        # Expected delivery date from mother_birth_outcome
        mother_birth_outcome = form.get("mother_birth_outcome", {})
        if not isinstance(mother_birth_outcome, dict):
            mother_birth_outcome = {}
        expected_delivery_date = mother_birth_outcome.get("expected_delivery_date", "")

        # preferred_visit_time is per-visit in var_visit_1, not in mother_details
        var_visit_1 = form.get("var_visit_1", {})
        pref_time = ""
        if isinstance(var_visit_1, dict):
            pref_time = var_visit_1.get("preferred_visit_time") or ""

        # Registration date from form metadata
        form_metadata = form_dict.get("metadata", {})
        registration_date = form_dict.get("received_on", "")[:10] or (form_metadata.get("timeEnd") or "")[:10]

        metadata_map[mother_case_id] = {
            "case_name": name,
            "date_opened": registration_date,
            "properties": {
                "mother_name": name,
                "age": age,
                "phone_number": phone,
                "household_size": household_size,
                "preferred_time_of_visit": pref_time,
                "eligible_full_intervention_bonus": eligible,
                "mother_dob": mother_dob,
                "expected_delivery_date": expected_delivery_date,
            },
        }

    logger.info(
        "[MBW Metadata] Extracted metadata for %d mothers from %d registration forms",
        len(metadata_map),
        len(registration_forms),
    )
    return metadata_map


def build_followup_from_pipeline(
    pipeline_rows: list,
    active_usernames: set[str],
    registration_forms: list[dict] | None = None,
) -> dict[str, list[dict]]:
    """Build follow-up visit data from CCHQ registration forms + pipeline completions.

    Three steps:
      1. Registration forms (CCHQ) → expected visits with schedules
      2. Build mother→FLW mapping from pipeline rows + registration form metadata
      3. Pipeline completion forms → mark matching visits as completed

    Args:
        pipeline_rows: Pipeline visit rows (completion forms)
        active_usernames: Set of active FLW usernames to include
        registration_forms: List of form dicts from CCHQ Form API v1

    Returns:
        dict mapping username → list of synthetic case dicts compatible with
        is_visit_completed(), calculate_visit_status(), and aggregate_flw_followup().
    """
    registration_forms = registration_forms or []

    # Normalize all usernames to lowercase for case-insensitive comparison.
    # CCHQ lowercases usernames while Connect may preserve original casing
    # (e.g. "VJSV07RI5UM93QIW7QD2" in Connect vs "vjsv07ri5um93qiw7qd2" in CCHQ).
    active_usernames = {u.lower() for u in active_usernames}

    # Step 1: Build expected visits from CCHQ registration forms
    # Key: (mother_case_id, visit_type) → synthetic case dict
    expected_visits: dict[tuple[str, str], dict] = {}
    # Track username from registration form metadata
    mother_to_username: dict[str, str] = {}

    for form_dict in registration_forms:
        schedules = _extract_schedules_from_registration_form(form_dict)
        if not schedules:
            continue

        # Get username from form metadata
        metadata = form_dict.get("metadata", {})
        form_username = metadata.get("username", "")

        for sched in schedules:
            mother_case_id = sched.get("mother_case_id", "")
            visit_type = sched.get("visit_type", "")
            if not mother_case_id or not visit_type:
                continue

            key = (mother_case_id, visit_type)
            if key in expected_visits:
                continue  # Deduplicate

            flag_name = COMPLETION_FLAGS.get(visit_type)
            if not flag_name:
                continue  # Unknown visit type

            expected_visits[key] = {
                "case_id": f"{mother_case_id}_{visit_type}",
                "properties": {
                    "visit_type": visit_type,
                    "mother_case_id": mother_case_id,
                    "visit_date_scheduled": sched.get("visit_date_scheduled", ""),
                    "visit_expiry_date": sched.get("visit_expiry_date", ""),
                    flag_name: "",  # Not completed yet
                },
            }
            if form_username:
                mother_to_username[mother_case_id] = form_username.lower()

    logger.info(
        "[MBW Follow-Up] Step 1: %d expected visits from %d mothers (%d registration forms)",
        len(expected_visits),
        len(mother_to_username),
        len(registration_forms),
    )

    # Step 2: Enrich mother→FLW mapping from pipeline rows
    # Pipeline rows use Connect usernames (matching active_usernames), while
    # registration forms use CCHQ usernames (which may differ). Pipeline
    # mappings take precedence so the final username matches active_usernames.
    step2_added = 0
    step2_overridden = 0
    for row in pipeline_rows:
        row_username = row.username.lower() if row.username else ""
        if row_username not in active_usernames:
            continue
        mother_case_id = row.computed.get("mother_case_id", "")
        if mother_case_id:
            existing = mother_to_username.get(mother_case_id)
            if existing is None:
                step2_added += 1
            elif existing != row_username:
                step2_overridden += 1
            mother_to_username[mother_case_id] = row_username

    logger.info(
        "[MBW Follow-Up] Step 2: enriched mother→FLW mapping from pipeline: "
        "%d added, %d overridden (CCHQ→Connect username)",
        step2_added,
        step2_overridden,
    )

    # Step 3: Mark completions from pipeline completion forms
    flag_to_visit_types: dict[str, list[str]] = defaultdict(list)
    for vt, flag in COMPLETION_FLAGS.items():
        flag_to_visit_types[flag].append(vt)

    # Diagnostic counters
    completions_matched = 0
    skip_inactive = 0
    skip_empty_form = 0
    skip_registration = 0
    skip_unmapped_form = 0
    skip_no_flag = 0
    skip_no_mother_id = 0
    skip_no_match = 0
    unique_form_names: dict[str, int] = defaultdict(int)
    completions_by_type: dict[str, int] = defaultdict(int)
    unmapped_form_names: dict[str, int] = defaultdict(int)

    for row in pipeline_rows:
        row_username = row.username.lower() if row.username else ""
        if row_username not in active_usernames:
            skip_inactive += 1
            continue
        raw_form_name = row.computed.get("form_name", "").strip()
        if not raw_form_name:
            skip_empty_form += 1
            continue
        if raw_form_name == "Register Mother":
            skip_registration += 1
            continue

        unique_form_names[raw_form_name] += 1

        # Normalize form name to canonical visit type
        visit_type = FORM_NAME_TO_VISIT_TYPE.get(raw_form_name)
        if not visit_type:
            skip_unmapped_form += 1
            unmapped_form_names[raw_form_name] += 1
            continue

        flag_name = COMPLETION_FLAGS.get(visit_type)
        if not flag_name:
            skip_no_flag += 1
            continue

        mother_case_id = row.computed.get("mother_case_id", "")
        if not mother_case_id:
            skip_no_mother_id += 1
            continue

        # Try all visit_types that share this completion flag
        # (handles "Postnatal Visit" / "Postnatal Delivery Visit" cross-matching)
        matched = False
        for vt in flag_to_visit_types.get(flag_name, []):
            key = (mother_case_id, vt)
            if key in expected_visits:
                expected_visits[key]["properties"][flag_name] = "ok"
                if row.visit_date:
                    expected_visits[key]["date_modified"] = row.visit_date.isoformat()
                completions_matched += 1
                completions_by_type[vt] += 1
                matched = True
                break

        if not matched:
            skip_no_match += 1

    logger.info(
        "[MBW Follow-Up] Step 3: %d completions matched | "
        "skipped: inactive=%d, empty_form=%d, registration=%d, "
        "unmapped_form=%d, no_flag=%d, no_mother_id=%d, no_match=%d",
        completions_matched,
        skip_inactive,
        skip_empty_form,
        skip_registration,
        skip_unmapped_form,
        skip_no_flag,
        skip_no_mother_id,
        skip_no_match,
    )
    logger.info(
        "[MBW Follow-Up] Step 3 form_name distribution: %s",
        dict(unique_form_names),
    )
    if unmapped_form_names:
        logger.warning(
            "[MBW Follow-Up] Step 3 UNMAPPED form names (need FORM_NAME_TO_VISIT_TYPE entry): %s",
            dict(unmapped_form_names),
        )
    logger.info(
        "[MBW Follow-Up] Step 3 completions by visit type: %s",
        dict(completions_by_type),
    )

    # Group by FLW username (only include active FLWs)
    by_flw: dict[str, list[dict]] = defaultdict(list)
    for (mother_case_id, _visit_type), case_dict in expected_visits.items():
        username = mother_to_username.get(mother_case_id)
        if username and username in active_usernames:
            by_flw[username].append(case_dict)

    # Log which active FLWs are included vs missing
    missing_flws = active_usernames - set(by_flw.keys())
    logger.info(
        "[MBW Follow-Up] Result: %d FLWs with visits, %d total visits. " "Active FLWs missing from follow-up: %d %s",
        len(by_flw),
        sum(len(v) for v in by_flw.values()),
        len(missing_flws),
        sorted(missing_flws) if missing_flws else "[]",
    )

    return dict(by_flw)


def count_mothers_from_pipeline(
    pipeline_rows: list,
    active_usernames: set[str],
    registration_forms: list[dict] | None = None,
) -> dict[str, int]:
    """Count unique mothers registered per FLW.

    Uses CCHQ registration forms if available, falls back to pipeline data.

    Returns:
        dict mapping username → count of unique mother case IDs
    """
    # Normalize to lowercase for case-insensitive comparison
    active_usernames = {u.lower() for u in active_usernames}
    mothers_by_flw: dict[str, set[str]] = defaultdict(set)

    if registration_forms:
        # Count from CCHQ registration forms
        for form_dict in registration_forms:
            metadata = form_dict.get("metadata", {})
            username = (metadata.get("username", "") or "").lower()
            if username not in active_usernames:
                continue
            schedules = _extract_schedules_from_registration_form(form_dict)
            for sched in schedules:
                mother_case_id = sched.get("mother_case_id", "")
                if mother_case_id:
                    mothers_by_flw[username].add(mother_case_id)
    else:
        # Fallback: count from pipeline rows with mother_case_id
        for row in pipeline_rows:
            row_username = (row.username or "").lower()
            if row_username not in active_usernames:
                continue
            mother_case_id = row.computed.get("mother_case_id", "")
            if mother_case_id:
                mothers_by_flw[row_username].add(mother_case_id)

    return {username: len(mothers) for username, mothers in mothers_by_flw.items()}


def _compute_value_concentration(values: list[str]) -> dict:
    """Compute concentration metrics for a list of values.

    Returns:
        {
            "pct_duplicate": int,   # % of values appearing more than once
            "mode_value": str,      # most common value
            "mode_pct": int,        # % with the mode value
        }
    """
    if not values:
        return {"pct_duplicate": 0, "mode_value": "", "mode_pct": 0}

    counter = Counter(values)
    total = len(values)
    duplicate_count = sum(count for count in counter.values() if count > 1)
    mode_value, mode_count = counter.most_common(1)[0]

    return {
        "pct_duplicate": round(duplicate_count / total * 100) if total > 0 else 0,
        "mode_value": str(mode_value),
        "mode_pct": round(mode_count / total * 100) if total > 0 else 0,
    }


def compute_overview_quality_metrics(
    visit_cases_by_flw: dict[str, list[dict]],
    mother_metadata: dict[str, dict],
    parity_by_mother: dict[str, str],
    anc_date_by_mother: dict[str, str] | None = None,
    pnc_date_by_mother: dict[str, str] | None = None,
) -> dict[str, dict]:
    """Compute per-FLW quality/fraud overview metrics.

    Returns: {
        username: {
            "phone_dup_pct": int,
            "anc_pnc_same_date_count": int,
            "anc_pnc_denominator": int,
            "parity_concentration": dict,
            "age_concentration": dict,
            "age_equals_reg_pct": int,
        }
    }
    """
    anc_date_by_mother = anc_date_by_mother or {}
    pnc_date_by_mother = pnc_date_by_mother or {}
    result: dict[str, dict] = {}

    for username, cases in visit_cases_by_flw.items():
        # Collect unique mother_case_ids for this FLW
        mother_ids = set()
        cases_by_mother: dict[str, list[dict]] = defaultdict(list)
        for c in cases:
            mid = c.get("properties", {}).get("mother_case_id", "")
            if mid:
                mother_ids.add(mid)
                cases_by_mother[mid].append(c)

        # --- Phone Dup % ---
        phones = []
        for mid in mother_ids:
            meta = mother_metadata.get(mid, {})
            phone = meta.get("properties", {}).get("phone_number", "")
            if phone:
                phones.append(phone)
        phone_conc = _compute_value_concentration(phones)

        # --- ANC ≠ PNC (count where ANC completion date == PNC completion date) ---
        anc_pnc_same = 0
        anc_pnc_denom = 0
        for mid in mother_ids:
            anc_date = anc_date_by_mother.get(mid, "")[:10] if anc_date_by_mother.get(mid) else ""
            pnc_date = pnc_date_by_mother.get(mid, "")[:10] if pnc_date_by_mother.get(mid) else ""
            if anc_date and pnc_date:
                anc_pnc_denom += 1
                if anc_date == pnc_date:
                    anc_pnc_same += 1

        # --- Parity concentration ---
        parity_values = []
        for mid in mother_ids:
            p = parity_by_mother.get(mid, "")
            if p:
                parity_values.append(p)
        parity_conc = _compute_value_concentration(parity_values)

        # --- Age concentration ---
        age_values = []
        for mid in mother_ids:
            meta = mother_metadata.get(mid, {})
            age = meta.get("properties", {}).get("age", "")
            if age:
                age_values.append(age)
        age_conc = _compute_value_concentration(age_values)

        # --- Age ≠ Reg (DOB month/day == registration month/day) ---
        age_eq_reg_count = 0
        age_eq_reg_total = 0
        for mid in mother_ids:
            meta = mother_metadata.get(mid, {})
            props = meta.get("properties", {})
            dob_str = props.get("mother_dob", "")
            reg_str = meta.get("date_opened", "")
            if dob_str and reg_str:
                try:
                    dob = date.fromisoformat(dob_str[:10])
                    reg = date.fromisoformat(reg_str[:10])
                    age_eq_reg_total += 1
                    if dob.month == reg.month and dob.day == reg.day:
                        age_eq_reg_count += 1
                except (ValueError, TypeError):
                    pass

        result[username] = {
            "phone_dup_pct": phone_conc["pct_duplicate"],
            "anc_pnc_same_date_count": anc_pnc_same,
            "anc_pnc_denominator": anc_pnc_denom,
            "parity_concentration": parity_conc,
            "age_concentration": age_conc,
            "age_equals_reg_pct": round(age_eq_reg_count / age_eq_reg_total * 100) if age_eq_reg_total > 0 else 0,
        }

    return result


# ---------------------------------------------------------------------------
# FLW Performance by Status
# ---------------------------------------------------------------------------

# Display label → (status_key, color hint)
FLW_STATUS_DISPLAY = {
    "eligible_for_renewal": "Eligible for Renewal",
    "probation": "Probation",
    "suspended": "Suspended",
    "none": "No Category",
}

# Visit milestones: (display visit_type, min_completed_to_be_on_track, output_key)
_VISIT_MILESTONES = [
    ("Month 1", 3, "pct_4_visits_on_track"),
    ("Month 3", 4, "pct_5_visits_complete"),
    ("Month 6", 5, "pct_6_visits_complete"),
]


def compute_flw_performance_by_status(
    flw_statuses: dict[str, str],
    flw_drilldown: dict[str, list],
    current_date: date,
) -> list[dict]:
    """Aggregate case-level performance metrics grouped by FLW assessment status.

    Args:
        flw_statuses: username (lowercase) → status key
            ("eligible_for_renewal", "probation", "suspended", or "none").
        flw_drilldown: username → list of mother summary dicts
            (output of aggregate_mother_metrics). Each mother has:
            - "eligible": bool
            - "visits": list of {visit_type, visit_date_scheduled, status}
        current_date: reference date for grace-period check.

    Returns:
        List of 4 dicts, one per status category (ordered: eligible, probation,
        suspended, none). Each dict contains aggregated metrics.
    """
    grace_cutoff = current_date - timedelta(days=GRACE_PERIOD_DAYS)

    # Group FLW usernames by status bucket
    status_order = ["eligible_for_renewal", "probation", "suspended", "none"]
    buckets: dict[str, list[str]] = {s: [] for s in status_order}
    for username, status in flw_statuses.items():
        bucket = status if status in buckets else "none"
        buckets[bucket].append(username)

    results = []
    for status_key in status_order:
        flw_list = buckets[status_key]

        # Collect all mothers across FLWs in this bucket
        all_mothers = []
        for username in flw_list:
            all_mothers.extend(flw_drilldown.get(username, []))

        total_cases = len(all_mothers)
        eligible_mothers = [m for m in all_mothers if m.get("eligible")]
        total_eligible = len(eligible_mothers)

        # --- still eligible: eligible AND (completed >= 5 OR missed <= 1) ---
        still_eligible = 0
        for m in eligible_mothers:
            completed = sum(1 for v in m["visits"] if v["status"].startswith("Completed"))
            missed = sum(1 for v in m["visits"] if v["status"] == "Missed")
            if completed >= 5 or missed <= 1:
                still_eligible += 1

        # --- pct missed ≤1 (eligible mothers only) ---
        missed_1_or_less = 0
        for m in eligible_mothers:
            missed = sum(1 for v in m["visits"] if v["status"] == "Missed")
            if missed <= 1:
                missed_1_or_less += 1

        # --- visit milestone percentages ---
        milestone_results: dict[str, int] = {}
        for visit_display_type, min_completed, metric_key in _VISIT_MILESTONES:
            denominator = 0
            numerator = 0
            for m in eligible_mothers:
                # Find the specific visit type for this mother
                milestone_visit = None
                for v in m["visits"]:
                    if v["visit_type"] == visit_display_type:
                        milestone_visit = v
                        break
                if milestone_visit is None:
                    continue
                # Check if this visit is due past grace period
                sched_str = milestone_visit.get("visit_date_scheduled")
                if not sched_str:
                    continue
                try:
                    sched_date = date.fromisoformat(sched_str[:10])
                except (ValueError, TypeError):
                    continue
                if sched_date > grace_cutoff:
                    continue  # not yet due past buffer
                denominator += 1
                # Count total completed visits for this mother
                completed = sum(1 for v in m["visits"] if v["status"].startswith("Completed"))
                if completed >= min_completed:
                    numerator += 1
            milestone_results[metric_key] = round(numerator / denominator * 100) if denominator > 0 else 0

        results.append(
            {
                "status": FLW_STATUS_DISPLAY.get(status_key, status_key),
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
