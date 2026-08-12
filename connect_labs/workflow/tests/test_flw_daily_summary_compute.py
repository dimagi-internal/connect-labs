from connect_labs.workflow.flw_daily_summary_compute import age_in_months, compute_flw_daily_summary


def _hsd_row(time_start, form_display_name="Health Service Delivery", **overrides):
    row = {
        "form_display_name": form_display_name,
        "time_start": time_start,
        "hh_case_id": "hh-1",
        "child_case_id": f"child-{time_start}",
    }
    row.update(overrides)
    return row


def _approved_row(time_start, form_display_name="Health Service Delivery", **overrides):
    row = {
        "form_display_name": form_display_name,
        "time_start": time_start,
        "child_case_id": f"child-{time_start}",
        "childs_dob": None,
        "muac_cm": None,
        "muac_photo": None,
        "dw_dosage_date_time": None,
    }
    row.update(overrides)
    return row


# --- age_in_months ---


def test_age_in_months_basic():
    assert age_in_months("2025-01-15", date_from_iso("2026-07-20")) == 18


def test_age_in_months_floors_down_when_day_not_yet_reached():
    # Born on the 25th; as-of the 20th of a later month -- one month short of a full month.
    assert age_in_months("2026-01-25", date_from_iso("2026-07-20")) == 5


def test_age_in_months_none_on_unparseable_dob():
    assert age_in_months("not-a-date", date_from_iso("2026-07-20")) is None


def test_age_in_months_none_on_missing_dob():
    assert age_in_months(None, date_from_iso("2026-07-20")) is None


def date_from_iso(iso):
    from datetime import date

    return date.fromisoformat(iso)


# --- compute_flw_daily_summary: #1-3 (hsd_visits, any status) ---


def test_total_households_and_children_registered_dedupe_and_skip_falsy():
    hsd_rows = [
        _hsd_row("2026-07-20T08:00:00Z", hh_case_id="hh-a", child_case_id="c1"),
        _hsd_row("2026-07-20T08:10:00Z", hh_case_id="hh-a", child_case_id="c2"),  # same household, new child
        _hsd_row("2026-07-20T08:20:00Z", hh_case_id="hh-b", child_case_id="c1"),  # different household, same child?
        _hsd_row("2026-07-20T08:30:00Z", hh_case_id=None, child_case_id=None),  # falsy -- skipped
    ]
    result = compute_flw_daily_summary(hsd_rows, [])
    assert result["total_households_registered"] == 2  # hh-a, hh-b
    assert result["total_children_registered"] == 2  # c1, c2


def test_total_health_service_delivery_visits_counts_every_submission_not_deduped():
    hsd_rows = [
        _hsd_row("2026-07-20T08:00:00Z", child_case_id="c1"),
        _hsd_row("2026-07-20T09:00:00Z", child_case_id="c1"),  # repeat visit to same child -- still counts
        _hsd_row("2026-07-20T10:00:00Z", form_display_name="No Children Found"),  # different form -- excluded
    ]
    result = compute_flw_daily_summary(hsd_rows, [])
    assert result["total_health_service_delivery_visits"] == 2


# --- compute_flw_daily_summary: #4-5 (approved_visits, split by form) ---


def test_approved_visit_counts_split_by_form_display_name():
    approved_rows = [
        _approved_row("2026-07-20T08:00:00Z", form_display_name="Health Service Delivery", child_case_id="c1"),
        _approved_row("2026-07-20T09:00:00Z", form_display_name="Health Service Delivery", child_case_id="c2"),
        _approved_row("2026-07-20T10:00:00Z", form_display_name="No Children Found", child_case_id=None),
    ]
    result = compute_flw_daily_summary([], approved_rows)
    assert result["total_approved_health_service_delivery_visits"] == 2
    assert result["total_approved_no_children_found_visits"] == 1


# --- compute_flw_daily_summary: #6-9 (dedup + age eligibility + muac_photo/dw_dosage_date_time) ---


def test_muac_and_deworming_eligibility_by_age_at_visit_time():
    approved_rows = [
        # 5 months old at visit time -- not MUAC eligible, not deworming eligible
        _approved_row("2026-07-20T08:00:00Z", child_case_id="young", childs_dob="2026-02-20"),
        # 8 months old -- MUAC eligible, not deworming eligible
        _approved_row("2026-07-20T08:10:00Z", child_case_id="muac-only", childs_dob="2025-11-20"),
        # 24 months old -- both eligible
        _approved_row("2026-07-20T08:20:00Z", child_case_id="both", childs_dob="2024-07-20"),
    ]
    result = compute_flw_daily_summary([], approved_rows)
    assert result["total_children_muac_eligible"] == 2
    assert result["total_children_deworming_eligible"] == 1


def test_dedup_keeps_first_visit_per_child_this_day():
    approved_rows = [
        _approved_row("2026-07-20T09:00:00Z", child_case_id="c1", childs_dob="2024-07-20"),  # later
        _approved_row("2026-07-20T08:00:00Z", child_case_id="c1", childs_dob="2024-07-20"),  # earlier -- kept
    ]
    result = compute_flw_daily_summary([], approved_rows)
    # Only counted once despite two submissions for the same child this day.
    assert result["total_children_muac_eligible"] == 1
    assert result["total_children_deworming_eligible"] == 1


def test_muac_measured_and_deworming_delivered_use_photo_and_dosage_fields_not_muac_cm():
    approved_rows = [
        _approved_row(
            "2026-07-20T08:00:00Z",
            child_case_id="c1",
            childs_dob="2024-07-20",
            muac_cm="12.5",
            muac_photo=None,
            dw_dosage_date_time=None,
        ),  # eligible for both, has muac_cm value but NO photo/dosage
        _approved_row(
            "2026-07-20T08:10:00Z",
            child_case_id="c2",
            childs_dob="2024-07-20",
            muac_cm=None,
            muac_photo="muac_photo_1.jpg",
            dw_dosage_date_time="2026-07-20T08:15:00.000000Z",
        ),  # eligible for both, no muac_cm but HAS photo/dosage
    ]
    result = compute_flw_daily_summary([], approved_rows)
    assert result["total_children_muac_eligible"] == 2
    assert result["total_children_deworming_eligible"] == 2
    # Only c2 has muac_photo -- c1's muac_cm value does NOT count as "measured".
    assert result["total_children_muac_measured"] == 1
    assert result["total_children_deworming_photo_taken"] == 1
    # Bonus cross-check field: only c1 has a non-null muac_cm value.
    assert result["total_children_muac_value_recorded"] == 1


def test_children_with_unparseable_dob_are_skipped_entirely():
    approved_rows = [
        _approved_row("2026-07-20T08:00:00Z", child_case_id="c1", childs_dob="not-a-date", muac_cm="12.0"),
    ]
    result = compute_flw_daily_summary([], approved_rows)
    assert result["total_children_muac_eligible"] == 0
    assert result["total_children_deworming_eligible"] == 0
    # Value-recorded is independent of age-eligibility gating, so it still counts.
    assert result["total_children_muac_value_recorded"] == 1


def test_no_photo_or_dosage_fields_default_indicators_to_zero():
    approved_rows = [
        _approved_row("2026-07-20T08:00:00Z", child_case_id="c1", childs_dob="2024-07-20"),
    ]
    result = compute_flw_daily_summary([], approved_rows)
    assert result["total_children_muac_measured"] == 0
    assert result["total_children_deworming_photo_taken"] == 0
