from connect_labs.workflow.flw_daily_summary_compute_rutf import compute_flw_daily_summary_rutf


def _row(form_display_name, entity_id, status="approved", **overrides):
    row = {
        "form_display_name": form_display_name,
        "entity_id": entity_id,
        "status": status,
        "rutf_enrollment": None,
        "muac_photo": None,
    }
    row.update(overrides)
    return row


def test_households_registered_dedupes_distinct_entities():
    rows = [
        _row("Register a New Family", "hh-a"),
        _row("Register a New Family", "hh-b"),
        _row("Register a New Family", "hh-a"),  # duplicate submission for same household -- deduped
    ]
    result = compute_flw_daily_summary_rutf(rows)
    assert result["total_households_registered"] == 2


def test_registration_rows_with_no_entity_id_are_skipped():
    rows = [
        _row("Register a New Family", None),
        _row("Register a New Family", "hh-a"),
    ]
    result = compute_flw_daily_summary_rutf(rows)
    assert result["total_households_registered"] == 1


def test_children_registered_counts_every_screening_submission_not_deduped():
    rows = [
        _row("Screening ", "child-a", rutf_enrollment="yes"),
        _row("Screening ", None, rutf_enrollment="no"),  # screened out -- still a screening event
        _row("Screening ", "child-a", rutf_enrollment="yes"),  # repeat submission -- still counts
        _row("Visit Form", "child-c"),  # different form -- excluded
    ]
    result = compute_flw_daily_summary_rutf(rows)
    assert result["total_children_registered"] == 3


def test_sam_children_registered_only_counts_screening_rows_enrolled_yes_and_dedupes():
    rows = [
        _row("Screening ", "child-a", rutf_enrollment="yes"),
        _row("Screening ", None, rutf_enrollment="no"),
        _row("Screening ", "child-b", rutf_enrollment="yes"),
        _row("Screening ", "child-a", rutf_enrollment="yes"),  # duplicate -- deduped
        _row("Visit Form", "child-c"),
    ]
    result = compute_flw_daily_summary_rutf(rows)
    assert result["total_sam_children_registered"] == 2


def test_muac_measured_counts_screening_rows_with_a_photo():
    rows = [
        _row("Screening ", "child-a", rutf_enrollment="yes", muac_photo="a.jpg"),
        _row("Screening ", "child-b", rutf_enrollment="yes", muac_photo=None),
        _row("Screening ", None, rutf_enrollment="no", muac_photo="c.jpg"),
        _row("Visit Form", "child-d", muac_photo="d.jpg"),  # different form -- excluded, different path in reality
    ]
    result = compute_flw_daily_summary_rutf(rows)
    assert result["total_children_muac_measured"] == 2


def test_total_visits_counts_every_status_but_sam_followup_visits_only_approved():
    rows = [
        _row("Visit Form", "child-a", status="approved"),
        _row("Visit Form", "child-b", status="pending"),
        _row("Visit Form", "child-c", status="over_limit"),
        _row("Screening ", "child-d", rutf_enrollment="yes"),  # different form -- excluded
    ]
    result = compute_flw_daily_summary_rutf(rows)
    assert result["total_visits"] == 3
    assert result["total_sam_followup_visits"] == 1


def test_non_approved_registration_and_screening_rows_are_excluded():
    rows = [
        _row("Register a New Family", "hh-a", status="pending"),
        _row("Screening ", "child-a", status="pending", rutf_enrollment="yes"),
    ]
    result = compute_flw_daily_summary_rutf(rows)
    assert result["total_households_registered"] == 0
    assert result["total_children_registered"] == 0
    assert result["total_sam_children_registered"] == 0


def test_empty_rows_produce_all_zero_indicators():
    result = compute_flw_daily_summary_rutf([])
    assert result == {
        "total_households_registered": 0,
        "total_children_registered": 0,
        "total_sam_children_registered": 0,
        "total_children_muac_measured": 0,
        "total_visits": 0,
        "total_sam_followup_visits": 0,
    }


def test_indicators_are_computed_independently_across_forms():
    rows = [
        _row("Register a New Family", "hh-a"),
        _row("Register a New Family", "hh-b"),
        _row("Screening ", "child-a", rutf_enrollment="yes", muac_photo="a.jpg"),
        _row("Screening ", None, rutf_enrollment="no"),
        _row("Visit Form", "child-c", status="approved"),
        _row("Visit Form", "child-c", status="approved"),
        _row("Visit Form", "child-e", status="rejected"),
    ]
    result = compute_flw_daily_summary_rutf(rows)
    assert result["total_households_registered"] == 2
    assert result["total_children_registered"] == 2
    assert result["total_sam_children_registered"] == 1
    assert result["total_children_muac_measured"] == 1
    assert result["total_visits"] == 3
    assert result["total_sam_followup_visits"] == 2
