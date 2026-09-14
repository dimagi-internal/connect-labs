from connect_labs.workflow.flw_daily_summary_compute_rutf import compute_flw_daily_summary_rutf


def _row(form_display_name, entity_id, **overrides):
    row = {
        "form_display_name": form_display_name,
        "entity_id": entity_id,
        "rutf_enrollment": None,
    }
    row.update(overrides)
    return row


def test_total_children_registered_dedupes_distinct_entities():
    rows = [
        _row("Register a New Family", "child-a"),
        _row("Register a New Family", "child-b"),
        _row("Register a New Family", "child-a"),  # duplicate submission for same child -- deduped
    ]
    result = compute_flw_daily_summary_rutf(rows)
    assert result["total_children_registered"] == 2


def test_registration_rows_with_no_entity_id_are_skipped():
    rows = [
        _row("Register a New Family", None),
        _row("Register a New Family", "child-a"),
    ]
    result = compute_flw_daily_summary_rutf(rows)
    assert result["total_children_registered"] == 1


def test_sam_children_registered_only_counts_screening_rows_enrolled_yes():
    rows = [
        _row("Screening ", "child-a", rutf_enrollment="yes"),
        _row("Screening ", None, rutf_enrollment="no"),  # screened out -- no case opened, no entity_id
        _row("Screening ", "child-b", rutf_enrollment="yes"),
        _row("Visit Form", "child-c"),  # different form entirely -- never counts here
    ]
    result = compute_flw_daily_summary_rutf(rows)
    assert result["total_sam_children_registered"] == 2


def test_sam_children_registered_dedupes_distinct_entities():
    rows = [
        _row("Screening ", "child-a", rutf_enrollment="yes"),
        _row("Screening ", "child-a", rutf_enrollment="yes"),  # duplicate submission -- deduped
    ]
    result = compute_flw_daily_summary_rutf(rows)
    assert result["total_sam_children_registered"] == 1


def test_sam_followup_visits_counts_every_visit_form_submission_not_deduped():
    rows = [
        _row("Visit Form", "child-a"),
        _row("Visit Form", "child-a"),  # repeat visit to the same child -- still counts
        _row("Visit Form", "child-b"),
        _row("Screening ", "child-c", rutf_enrollment="yes"),  # different form -- excluded
    ]
    result = compute_flw_daily_summary_rutf(rows)
    assert result["total_sam_followup_visits"] == 3


def test_empty_rows_produce_all_zero_indicators():
    result = compute_flw_daily_summary_rutf([])
    assert result == {
        "total_children_registered": 0,
        "total_sam_children_registered": 0,
        "total_sam_followup_visits": 0,
    }


def test_indicators_are_computed_independently_across_forms():
    rows = [
        _row("Register a New Family", "child-a"),
        _row("Register a New Family", "child-b"),
        _row("Screening ", "child-a", rutf_enrollment="yes"),
        _row("Screening ", None, rutf_enrollment="no"),
        _row("Visit Form", "child-c"),
        _row("Visit Form", "child-c"),
    ]
    result = compute_flw_daily_summary_rutf(rows)
    assert result["total_children_registered"] == 2
    assert result["total_sam_children_registered"] == 1
    assert result["total_sam_followup_visits"] == 2
