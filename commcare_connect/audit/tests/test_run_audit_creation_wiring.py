"""Wiring test: run_audit_creation translates image_audits via build_review_config."""
from commcare_connect.audit.ai_review_config import build_review_config


def test_build_review_config_drives_related_fields_and_reviewers():
    image_audits = [
        {
            "image_path": "form/scale_photo",
            "reviewers": [
                {
                    "agent_id": "scale_validation",
                    "config": {"comparison_field": "form/child_weight"},
                    "auto_apply_actions": ["fail_unmatched"],
                }
            ],
        }
    ]
    related_fields, ai_reviewers = build_review_config(image_audits, context_fields=None)

    # filter rule scopes the audit; reading rule attaches the comparison value
    image_paths = {(r["image_path"], r["field_path"], r["filter_by_image"]) for r in related_fields}
    assert ("form/scale_photo", "", True) in image_paths
    assert ("form/scale_photo", "form/child_weight", False) in image_paths
    assert ai_reviewers["form/scale_photo"]["agent_id"] == "scale_validation"
    assert ai_reviewers["form/scale_photo"]["auto_apply_actions"] == ["fail_unmatched"]
