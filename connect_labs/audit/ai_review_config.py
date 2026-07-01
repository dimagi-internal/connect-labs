"""Translate the wizard's image_audits payload into the audit pipeline's
internal related_fields rules plus a question_id -> reviewer map.

image_audits (from the creation wizard):
    [{"image_path": "form/scale_photo",
      "reviewers": [{"agent_id": "scale_validation",
                     "config": {"comparison_field": "form/child_weight"},
                     "auto_apply_actions": ["pass_matched", "fail_unmatched"]}]}]

context_fields (slim agent-less display):
    [{"image_path": "form/scale_photo", "field_path": "form/child_id", "label": "Child ID"}]

related_fields rules consumed by AuditDataAccess:
    {image_path, field_path, label, filter_by_image, filter_by_field}

ai_reviewers map consumed by tasks._run_ai_review_on_sessions:
    {question_id: {"agent_id": str, "auto_apply_actions": list | None}}
"""


def _filter_rule(image_path: str) -> dict:
    return {
        "image_path": image_path,
        "field_path": "",
        "label": "",
        "filter_by_image": True,
        "filter_by_field": False,
    }


def _value_rule(image_path: str, field_path: str, label: str = "") -> dict:
    return {
        "image_path": image_path,
        "field_path": field_path,
        "label": label,
        "filter_by_image": False,
        "filter_by_field": False,
    }


def build_review_config(
    image_audits: list[dict] | None,
    context_fields: list[dict] | None = None,
) -> tuple[list[dict], dict[str, dict]]:
    """Return (related_fields, ai_reviewers) for the given wizard payload."""
    related_fields: list[dict] = []
    ai_reviewers: dict[str, dict] = {}

    for entry in image_audits or []:
        image_path = (entry or {}).get("image_path")
        if not image_path:
            continue

        # Selecting an image type scopes the audit to visits that have it.
        related_fields.append(_filter_rule(image_path))

        reviewers = entry.get("reviewers") or []
        reviewer = reviewers[0] if reviewers else None  # v1: one reviewer per type
        if not reviewer or not reviewer.get("agent_id"):
            continue

        ai_reviewers[image_path] = {
            "agent_id": reviewer["agent_id"],
            "auto_apply_actions": reviewer.get("auto_apply_actions"),
        }

        # A form_field config value (e.g. the scale agent's comparison_field) becomes
        # the reading rule that supplies form_data["reading"] to the agent.
        config = reviewer.get("config") or {}
        comparison_field = config.get("comparison_field")
        if comparison_field:
            related_fields.append(_value_rule(image_path, comparison_field))

    for cf in context_fields or []:
        image_path = (cf or {}).get("image_path")
        field_path = (cf or {}).get("field_path")
        if image_path and field_path:
            related_fields.append(_value_rule(image_path, field_path, cf.get("label", "")))

    return related_fields, ai_reviewers
