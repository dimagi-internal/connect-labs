"""Pure computation functions for the FLW Daily Summary Report (Program 217,
"CHC - NG - RCT - Aug 2026").

Every function here takes plain dicts/lists and returns plain dicts/lists — no
Django, no network, no pipeline objects — so the logic can be unit-tested
directly. See connect_labs/workflow/templates/flw_daily_summary_report.py for
the template that fetches pipeline rows and calls compute_flw_daily_summary
per opportunity per FLW per day.

Unlike flw_daily_indicator_compute.py (Program 176's fraud/data-quality
indicators), this report is a much simpler set of plain service-delivery
counts — no thresholds, no rate/percentage indicators, no fraud heuristics.

Field expectations on each `hsd_rows` row (from the `hsd_visits` pipeline,
filters={} — ALL statuses, ALL form types on the deliver unit):
    username, opportunity_id, form_display_name, hh_case_id, child_case_id,
    time_start

Field expectations on each `approved_rows` row (from the `approved_visits`
pipeline, filters={"status": ["approved"]} — approved only, ALL form types):
    username, opportunity_id, form_display_name, time_start, child_case_id,
    childs_dob, muac_cm, muac_photo, dw_dosage_date_time

``muac_photo`` and ``dw_dosage_date_time`` are both plain scalar fields on the
Health Service Delivery form (verified live against real program-217 data via
a Superset schema/sample-value export, not guessed): ``muac_photo`` is the raw
MUAC-photo attachment's filename (form.muac_group.muac_display_group_2.
muac_display_group_photo.muac_photo — non-empty means a photo was captured;
the sibling calculate field `muac_photo_link` was checked too but is blank on
every sampled form, effectively unused in this app build). `dw_dosage_date_time`
(form.case.update.dw_dosage_date_time) is set when a deworming dose was
actually administered. Both are ordinary connect_csv path fields — no
CommCare HQ connection, case data, or extra fetch beyond the two pipelines.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from connect_labs.workflow.flw_audit_compute import FORM_NAME, WAT_OFFSET

# "No Children Found" is the other form submitted against the same deliver
# unit as "Health Service Delivery" (FORM_NAME) -- used to split the
# approved_visits pipeline's rows for indicators #4/#5.
NO_CHILDREN_FOUND_FORM_NAME = "No Children Found"


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


def _parse_date_only(value) -> date | None:
    """Parse an ISO date (or the date portion of an ISO timestamp) string."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def age_in_months(dob_str, as_of_date: date | None) -> int | None:
    """Calendar months between ``dob_str`` (ISO date/timestamp string) and
    ``as_of_date`` (a date, e.g. the visit's own WAT calendar day -- NOT
    "today" when the job runs), floor division. Returns None if either side
    is missing/unparseable."""
    dob = _parse_date_only(dob_str)
    if dob is None or as_of_date is None:
        return None
    return (as_of_date.year - dob.year) * 12 + (as_of_date.month - dob.month) - (1 if as_of_date.day < dob.day else 0)


def _dedup_children_by_day(rows: list[dict]) -> dict[str, dict]:
    """One representative visit per distinct child_case_id this day (first by
    time_start) -- mirrors flw_audit_compute.py's _dedup_children_this_week,
    scoped to a single calendar day instead of a week. ``rows`` must already
    carry a parsed ``_time_start`` datetime key."""
    by_child: dict[str, dict] = {}
    for v in sorted(rows, key=lambda r: r["_time_start"]):
        cid = v.get("child_case_id")
        if not cid:
            continue
        by_child.setdefault(cid, v)
    return by_child


def compute_flw_daily_summary(hsd_rows: list[dict], approved_rows: list[dict]) -> dict:
    """Compute every Program-217 daily summary indicator for ONE FLW's visits
    on ONE day, for ONE opportunity.

    ``hsd_rows`` must already be filtered to this FLW, this opportunity, and
    this day's window (any status, any form -- the ``hsd_visits`` pipeline
    applies no filters). ``approved_rows`` must already be filtered to this
    FLW, this opportunity, and this day's window (already approved-only, any
    form -- the ``approved_visits`` pipeline filters status server-side).
    """
    # --- #1-3: from hsd_visits, "Health Service Delivery" form, ANY status ---
    hsd_only = [r for r in hsd_rows if r.get("form_display_name") == FORM_NAME]
    household_ids = {r["hh_case_id"] for r in hsd_only if r.get("hh_case_id")}
    child_ids = {r["child_case_id"] for r in hsd_only if r.get("child_case_id")}
    total_health_service_delivery_visits = len(hsd_only)  # not deduped -- every submission counts

    # --- #4-5: from approved_visits (already approved-only), split by form ---
    approved_hsd = [r for r in approved_rows if r.get("form_display_name") == FORM_NAME]
    approved_ncf = [r for r in approved_rows if r.get("form_display_name") == NO_CHILDREN_FOUND_FORM_NAME]

    # --- #6-9: approved HSD visits, deduped to one representative visit per
    # distinct child this day (first by time_start) ---
    parsed_approved_hsd = []
    for r in approved_hsd:
        ts = _parse_dt(r.get("time_start"))
        if ts is None:
            continue
        row = dict(r)
        row["_time_start"] = ts
        parsed_approved_hsd.append(row)
    children_this_day = _dedup_children_by_day(parsed_approved_hsd)

    muac_eligible_count = 0
    muac_measured_count = 0
    muac_value_recorded_count = 0
    deworming_eligible_count = 0
    deworming_photo_taken_count = 0

    for child in children_this_day.values():
        # Cheap cross-check against the muac_photo signal -- independent of
        # age eligibility, since it's just "was a numeric value recorded".
        if _to_float(child.get("muac_cm")) is not None:
            muac_value_recorded_count += 1

        as_of_date = (child["_time_start"] + WAT_OFFSET).date()
        months = age_in_months(child.get("childs_dob"), as_of_date)
        if months is None:
            continue  # unparseable/missing childs_dob -- skip this child entirely

        if months >= 6:
            muac_eligible_count += 1
            if child.get("muac_photo"):
                muac_measured_count += 1
        if months >= 12:
            deworming_eligible_count += 1
            if child.get("dw_dosage_date_time"):
                deworming_photo_taken_count += 1

    return {
        "total_households_registered": len(household_ids),
        "total_children_registered": len(child_ids),
        "total_health_service_delivery_visits": total_health_service_delivery_visits,
        "total_approved_health_service_delivery_visits": len(approved_hsd),
        "total_approved_no_children_found_visits": len(approved_ncf),
        "total_children_muac_eligible": muac_eligible_count,
        "total_children_muac_measured": muac_measured_count,
        "total_children_muac_value_recorded": muac_value_recorded_count,
        "total_children_deworming_eligible": deworming_eligible_count,
        "total_children_deworming_photo_taken": deworming_photo_taken_count,
    }
