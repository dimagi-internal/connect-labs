"""Tests for build_review_config (image_audits -> related_fields + ai_reviewers)."""
from connect_labs.audit.ai_review_config import build_review_config


def test_scale_reviewer_produces_filter_rule_reading_rule_and_map():
    image_audits = [
        {
            "image_path": "form/scale_photo",
            "reviewers": [
                {
                    "agent_id": "scale_validation",
                    "config": {"comparison_field": "form/child_weight"},
                    "auto_apply_actions": ["pass_matched", "fail_unmatched"],
                }
            ],
        }
    ]
    related_fields, ai_reviewers = build_review_config(image_audits)

    # One filter rule (scope to visits with the image) + one reading rule (comparison field)
    assert {
        "image_path": "form/scale_photo",
        "field_path": "",
        "label": "",
        "filter_by_image": True,
        "filter_by_field": False,
    } in related_fields
    assert {
        "image_path": "form/scale_photo",
        "field_path": "form/child_weight",
        "label": "",
        "filter_by_image": False,
        "filter_by_field": False,
    } in related_fields

    assert ai_reviewers == {
        "form/scale_photo": [
            {
                "agent_id": "scale_validation",
                "auto_apply_actions": ["pass_matched", "fail_unmatched"],
                "comparison_field": "form/child_weight",
            }
        ]
    }


def test_image_only_agent_has_no_reading_rule():
    image_audits = [
        {
            "image_path": "form/muac_photo",
            "reviewers": [{"agent_id": "muac_overzoom", "config": {}, "auto_apply_actions": ["fail_overzoomed"]}],
        }
    ]
    related_fields, ai_reviewers = build_review_config(image_audits)
    # Only the filter rule — no reading rule because there's no comparison_field
    assert related_fields == [
        {
            "image_path": "form/muac_photo",
            "field_path": "",
            "label": "",
            "filter_by_image": True,
            "filter_by_field": False,
        }
    ]
    assert ai_reviewers["form/muac_photo"][0]["agent_id"] == "muac_overzoom"
    assert ai_reviewers["form/muac_photo"][0]["comparison_field"] is None


def test_multiple_reviewers_on_one_image_path_each_get_their_own_entry():
    """Two independent reviewers (e.g. MUAC OverZoom + MUAC Match) can both
    watch the same image path — each keeps its own auto_apply_actions and,
    if configured, its own reading rule."""
    image_audits = [
        {
            "image_path": "form/muac_photo",
            "reviewers": [
                {"agent_id": "muac_overzoom", "config": {}, "auto_apply_actions": ["fail_overzoomed"]},
                {
                    "agent_id": "muac_match",
                    "config": {"comparison_field": "form/muac_reading"},
                    "auto_apply_actions": ["fail_unmatched"],
                },
            ],
        }
    ]
    related_fields, ai_reviewers = build_review_config(image_audits)

    assert ai_reviewers["form/muac_photo"] == [
        {"agent_id": "muac_overzoom", "auto_apply_actions": ["fail_overzoomed"], "comparison_field": None},
        {
            "agent_id": "muac_match",
            "auto_apply_actions": ["fail_unmatched"],
            "comparison_field": "form/muac_reading",
        },
    ]
    # Only muac_match has a comparison_field, so only one reading rule is added
    assert {
        "image_path": "form/muac_photo",
        "field_path": "form/muac_reading",
        "label": "",
        "filter_by_image": False,
        "filter_by_field": False,
    } in related_fields


def test_type_with_no_reviewer_filters_but_no_map_entry():
    related_fields, ai_reviewers = build_review_config([{"image_path": "form/consent", "reviewers": []}])
    assert related_fields == [
        {
            "image_path": "form/consent",
            "field_path": "",
            "label": "",
            "filter_by_image": True,
            "filter_by_field": False,
        }
    ]
    assert ai_reviewers == {}


def test_context_fields_become_display_rules():
    related_fields, ai_reviewers = build_review_config(
        [],
        context_fields=[{"image_path": "form/scale_photo", "field_path": "form/child_id", "label": "Child ID"}],
    )
    assert related_fields == [
        {
            "image_path": "form/scale_photo",
            "field_path": "form/child_id",
            "label": "Child ID",
            "filter_by_image": False,
            "filter_by_field": False,
        }
    ]
    assert ai_reviewers == {}


def test_blank_image_path_is_ignored():
    related_fields, ai_reviewers = build_review_config([{"image_path": "", "reviewers": []}])
    assert related_fields == []
    assert ai_reviewers == {}
