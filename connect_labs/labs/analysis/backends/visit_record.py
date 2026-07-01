"""
Convert v2 JSON export records into the labs visit dict shape.

The labs SQL backend cache and analysis pipeline expect a specific dict
shape for visits. The v2 `/export/opportunity/<id>/user_visits/` endpoint
returns records that are almost the right shape — this module normalizes
field defaults, extracts `xform_id` from `form_json`, and (optionally)
strips `form_json` for memory-efficient slim mode.
"""
from typing import Any

# All keys in a labs visit dict, in canonical order.
ALL_VISIT_KEYS = [
    "id",
    "xform_id",
    "opportunity_id",
    "username",
    "deliver_unit",
    "deliver_unit_id",
    "entity_id",
    "entity_name",
    "visit_date",
    "status",
    "reason",
    "location",
    "flagged",
    "flag_reason",
    "form_json",
    "completed_work",
    "status_modified_date",
    "review_status",
    "review_created_on",
    "justification",
    "date_created",
    "completed_work_id",
    "images",
]

# Slim mode excludes form_json — used when the analysis pipeline reads
# form_json directly from the SQL cache instead of from in-memory dicts.
SLIM_VISIT_KEYS = [k for k in ALL_VISIT_KEYS if k != "form_json"]


def record_to_visit_dict(
    record: dict[str, Any],
    opportunity_id: int,
    skip_form_json: bool = False,
) -> dict[str, Any]:
    """
    Normalize a single v2 export record into a labs visit dict.

    Args:
        record: One item from the `results` list of a v2 paginated response.
        opportunity_id: Fallback if the record itself does not include
            `opportunity_id` (some serializers omit it).
        skip_form_json: If True, replace `form_json` with `{}` and clear
            `xform_id`. Used in slim mode when form_json lives in the DB cache.

    Returns:
        A dict with all keys from `ALL_VISIT_KEYS`, populated from `record`
        with sensible defaults for missing fields.
    """
    form_json = record.get("form_json") or {}
    if not isinstance(form_json, dict):
        form_json = {}

    xform_id = form_json.get("id") if form_json else None

    if skip_form_json:
        form_json = {}
        xform_id = None

    images = record.get("images") or []
    if not isinstance(images, list):
        images = []

    # v2 JSON returns `deliver_unit` as an integer FK PK; v1 CSV stringified it
    # and the RawVisitCache.deliver_unit CharField stores it as a string. Coerce
    # here so the in-memory dict matches the cache round-trip and the v1 contract.
    deliver_unit_raw = record.get("deliver_unit")
    deliver_unit = str(deliver_unit_raw) if deliver_unit_raw is not None else None

    return {
        "id": record.get("id"),
        "xform_id": xform_id,
        "opportunity_id": record.get("opportunity_id") or opportunity_id,
        "username": record.get("username"),
        "deliver_unit": deliver_unit,
        "deliver_unit_id": record.get("deliver_unit_id"),
        "entity_id": record.get("entity_id"),
        "entity_name": record.get("entity_name"),
        "visit_date": record.get("visit_date"),
        "status": record.get("status"),
        "reason": record.get("reason"),
        "location": record.get("location"),
        "flagged": bool(record.get("flagged", False)),
        "flag_reason": record.get("flag_reason"),
        "form_json": form_json,
        "completed_work": record.get("completed_work"),
        "status_modified_date": record.get("status_modified_date"),
        "review_status": record.get("review_status"),
        "review_created_on": record.get("review_created_on"),
        "justification": record.get("justification"),
        "date_created": record.get("date_created"),
        "completed_work_id": record.get("completed_work_id"),
        "images": images,
    }
