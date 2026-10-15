"""Review tools: an owner's scoring of the responses to its call.

Reviews live in the labs database (``SolicitationReview``) and are the owning
organisation's working notes: only its members and platform support read or
write them, and never on a response from their own side (see
``solicitations/access.py``). One review per reviewer per response.
"""

from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied

from connect_labs.solicitations.data_access import SolicitationsDataAccess
from connect_labs.solicitations.local_models import SolicitationResponse, SolicitationReview

from ..tool_registry import MCPToolError, register  # noqa: F401

logger = logging.getLogger(__name__)

REVIEW_TYPE = "solicitation_review"


def _serialize_record(record) -> dict:
    """The flat envelope + data dict the review tools have always returned."""
    return record.to_record_dict()


_SCOPE_PROPS = {
    "program_id": {
        "type": ["integer", "string"],
        "description": "Accepted for compatibility; not needed.",
    },
    "organization_id": {
        "type": ["integer", "string"],
        "description": "Accepted for compatibility; not needed.",
    },
}


def _errors(exc):
    if isinstance(exc, PermissionDenied):
        return MCPToolError("PERMISSION_DENIED", str(exc) or "Not allowed")
    return MCPToolError("NOT_FOUND", str(exc))


# ---------------------------------------------------------------------------
# Read tools (is_write=False)
# ---------------------------------------------------------------------------


@register(
    name="list_reviews",
    description=(
        "List all reviews for a given solicitation response. " "Reviews are linked to responses via labs_record_id."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "response_id": {
                "type": "integer",
                "description": "The Labs Record ID of the response to list reviews for.",
            },
            **_SCOPE_PROPS,
        },
        "required": ["response_id"],
        "additionalProperties": False,
    },
)
def list_reviews(user, response_id: int, program_id=None, organization_id=None) -> dict:
    """Reviews of a response -- empty unless the caller manages the call."""
    records = SolicitationsDataAccess(user=user).get_reviews_for_response(response_id)
    return {"reviews": [_serialize_record(r) for r in records]}


@register(
    name="get_review",
    description="Get a single solicitation review by its Labs Record ID.",
    input_schema={
        "type": "object",
        "properties": {
            "review_id": {
                "type": "integer",
                "description": "The Labs Record ID of the review.",
            },
            **_SCOPE_PROPS,
        },
        "required": ["review_id"],
        "additionalProperties": False,
    },
)
def get_review(user, review_id: int, program_id=None, organization_id=None) -> dict:
    """Get a single review by ID. Returns the record or raises NOT_FOUND."""
    record = SolicitationsDataAccess(user=user).get_review_by_id(review_id)
    if record is None:
        raise MCPToolError("NOT_FOUND", f"Review {review_id} not found")
    return _serialize_record(record)


# ---------------------------------------------------------------------------
# Write tools (is_write=True)
# ---------------------------------------------------------------------------


_REVIEW_PUBLIC_WARNING = (
    "Reviews are read by the call's owning organisation and platform support, not by the "
    "reviewed organisation -- but still do not embed PII in 'notes' (patient names, dates of "
    "birth, phone numbers, addresses, clinical observations, or any health data). "
    "Safe fields: score, recommendation, criteria_scores, tags, reviewer_username."
)


@register(
    name="create_review",
    description=(
        "Create (or, for a reviewer who already reviewed it, update) your review of a solicitation "
        "response. Only the call's owning organisation and platform support review, and never a "
        "response from their own side. When the call has weighted evaluation criteria and you send "
        "criteria_scores, the overall score is computed from them. You MUST pass "
        "public_record_acknowledged=true and confirm the 'notes' field contains no PII."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "public_record_acknowledged": {
                "type": "boolean",
                "description": (
                    "Must be true. Confirms you understand this record will be publicly "
                    "readable by the reviewed organisation and that the 'notes' field "
                    "contains no PII (patient names, dates of birth, phone numbers, "
                    "addresses, clinical observations, or health data)."
                ),
            },
            "response_id": {
                "type": "integer",
                "description": "ID of the response being reviewed.",
            },
            "llo_entity_id": {
                "type": "string",
                "description": "Accepted for compatibility; the response already names its organisation.",
            },
            "score": {
                "type": "integer",
                "description": "Overall score 1-100.",
            },
            "recommendation": {
                "type": "string",
                "description": (
                    "Review recommendation: 'under_review', 'approved', " "'rejected', or 'needs_revision'."
                ),
            },
            "notes": {
                "type": "string",
                "description": (
                    "Reviewer notes. "
                    "Do NOT include PII — this field is publicly readable by the reviewed organisation."
                ),
            },
            "criteria_scores": {
                "type": "object",
                "description": "Dict of criterion_id -> score (1-10).",
                "additionalProperties": True,
            },
            "reviewer_username": {
                "type": "string",
                "description": "Username of the reviewer.",
            },
            "tags": {
                "type": "string",
                "description": "Comma-separated tags.",
            },
            "program_id": {
                "type": ["integer", "string"],
                "description": "Accepted for compatibility; not needed.",
            },
            "organization_id": {
                "type": ["integer", "string"],
                "description": "Accepted for compatibility; not needed.",
            },
        },
        "required": ["public_record_acknowledged", "response_id", "llo_entity_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def create_review(
    user,
    public_record_acknowledged: bool,
    response_id: int,
    llo_entity_id: str,
    score: int | None = None,
    recommendation: str = "under_review",
    notes: str = "",
    criteria_scores: dict | None = None,
    reviewer_username: str = "",
    tags: str = "",
    program_id: str | int | None = None,
    organization_id: str | int | None = None,
) -> dict:
    """Create a review for a response."""
    if not public_record_acknowledged:
        raise MCPToolError(
            "POLICY_VIOLATION",
            "public_record_acknowledged must be true. Review the field descriptions, "
            "confirm 'notes' contains no PII, then retry with public_record_acknowledged=true.",
        )

    data: dict = {"recommendation": recommendation}
    if score is not None:
        data["score"] = score
    if notes:
        data["notes"] = notes
    if criteria_scores:
        data["criteria_scores"] = criteria_scores
    if reviewer_username:
        data["reviewer_username"] = reviewer_username
    if tags:
        data["tags"] = tags
    try:
        record = SolicitationsDataAccess(user=user).create_review(response_id, data)
    except (PermissionDenied, SolicitationResponse.DoesNotExist) as e:
        raise _errors(e) from e
    result = _serialize_record(record)
    result["_public_warning"] = _REVIEW_PUBLIC_WARNING
    return result


@register(
    name="update_review",
    description=(
        "Update an existing review. Merges update_data into the existing data dict; "
        "keys present in update_data overwrite existing values, all other keys are preserved."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "review_id": {
                "type": "integer",
                "description": "The Labs Record ID of the review to update.",
            },
            "update_data": {
                "type": "object",
                "description": "Fields to update. Merged (shallow) into the existing data dict.",
                "additionalProperties": True,
            },
            **_SCOPE_PROPS,
        },
        "required": ["review_id", "update_data"],
        "additionalProperties": False,
    },
    is_write=True,
)
def update_review(user, review_id: int, update_data: dict, program_id=None, organization_id=None) -> dict:
    """Merge update_data into your review; keys you do not send are kept."""
    data = {k: v for k, v in dict(update_data).items() if k not in ("is_public", "public")}
    try:
        record = SolicitationsDataAccess(user=user).update_review(review_id, data)
    except (PermissionDenied, SolicitationReview.DoesNotExist) as e:
        raise _errors(e) from e
    return _serialize_record(record)
