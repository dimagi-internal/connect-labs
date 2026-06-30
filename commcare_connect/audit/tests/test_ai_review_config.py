"""Tests for build_review_config (image_audits -> related_fields + ai_reviewers)."""
from commcare_connect.audit.ai_review_config import build_review_config


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
        "form/scale_photo": {
            "agent_id": "scale_validation",
            "auto_apply_actions": ["pass_matched", "fail_unmatched"],
        }
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
    assert ai_reviewers["form/muac_photo"]["agent_id"] == "muac_overzoom"


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
