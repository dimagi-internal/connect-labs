"""Review tools — migrated from _pending_migration/review_tools.py.

Reviews are LabsRecord entries with type="solicitation_review".
Scoped by llo_entity_id (see CLAUDE.md record-type conventions).
The labs_record FK on a review points to the response record, not the solicitation.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from connect_labs.labs.integrations.connect.api_client import LabsRecordAPIClient

from ..connect_token import require_connect_token
from ..tool_registry import MCPToolError, register  # noqa: F401

logger = logging.getLogger(__name__)

REVIEW_TYPE = "solicitation_review"


def _serialize_record(record) -> dict:
    """Flatten a LocalLabsRecord into a plain dict matching the original shape.

    Merges the record's ``data`` dict into the outer envelope, giving callers
    a single flat dict with id/experiment/type/labs_record_id plus all
    application-level fields at the top level.
    """
    data = record.data or {}
    return {
        "id": record.id,
        "experiment": record.experiment,
        "type": record.type,
        "labs_record_id": record.labs_record_id,
        **data,
    }


_SCOPE_PROPS = {
    "program_id": {
        "type": ["integer", "string"],
        "description": (
            "Program ID owning the reviewed response. Required to reach a labs-only "
            "synthetic program's local backend (id >= the labs-only floor)."
        ),
    },
    "organization_id": {
        "type": ["integer", "string"],
        "description": "Organization ID alternative to program_id for scoping.",
    },
}


def _scoped_client(token: str, program_id=None, organization_id=None) -> LabsRecordAPIClient:
    """LabsRecordAPIClient with labs-only routing resolved from program_id.

    The local-records backend keys writes on opportunity_id, and a labs-only
    synthetic program's program_id == its opportunity_id — mirror the
    SolicitationsDataAccess resolution so review reads/writes on labs-only
    programs route locally instead of 404ing (or violating the local table's
    NOT NULL opportunity_id) via prod.
    """

    def _coerce(v):
        try:
            return int(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    pid = _coerce(program_id)
    opp_id = None
    if pid is not None:
        from connect_labs.labs.synthetic.local_records_backend import is_labs_only_opportunity_id

        if is_labs_only_opportunity_id(pid):
            opp_id = pid
    return LabsRecordAPIClient(
        access_token=token,
        opportunity_id=opp_id,
        program_id=pid,
        organization_id=_coerce(organization_id),
    )


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
    """List all reviews for a response (child records linked by labs_record_id)."""
    token = require_connect_token(user)
    client = _scoped_client(token, program_id, organization_id)
    try:
        records = client.get_records(
            type=REVIEW_TYPE,
            labs_record_id=response_id,
        )
        return {"reviews": [_serialize_record(r) for r in records]}
    finally:
        client.close()


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
    token = require_connect_token(user)
    client = _scoped_client(token, program_id, organization_id)
    try:
        record = client.get_record_by_id(review_id, type=REVIEW_TYPE)
        if record is None:
            raise MCPToolError("NOT_FOUND", f"Review {review_id} not found")
        return _serialize_record(record)
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Write tools (is_write=True)
# ---------------------------------------------------------------------------


_REVIEW_PUBLIC_WARNING = (
    "This review record is PUBLIC — the reviewed organisation can read it. "
    "Do not embed PII in the 'notes' field (patient names, dates of birth, "
    "phone numbers, addresses, clinical observations, or any health data). "
    "Safe fields: score, recommendation, criteria_scores, tags, reviewer_username."
)


@register(
    name="create_review",
    description=(
        "Create a review for a solicitation response. "
        "The review is linked to the response via labs_record_id and scoped by llo_entity_id. "
        "IMPORTANT: Review records are PUBLIC — readable by the reviewed organisation. "
        "You MUST pass public_record_acknowledged=true and confirm the 'notes' field "
        "contains no PII before calling this tool."
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
                "description": "LLO entity ID (used as experiment for API scoping).",
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
                "description": (
                    "Program ID owning the reviewed response. Required to reach a "
                    "labs-only synthetic program's local backend (id >= the labs-only floor)."
                ),
            },
            "organization_id": {
                "type": ["integer", "string"],
                "description": "Organization ID alternative to program_id for scoping.",
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
    logger.warning("create_review: creating public record. %s", _REVIEW_PUBLIC_WARNING)

    data: dict = {
        "response_id": response_id,
        "llo_entity_id": llo_entity_id,
        "recommendation": recommendation,
        "review_date": datetime.now(timezone.utc).isoformat(),
    }
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

    token = require_connect_token(user)
    client = _scoped_client(token, program_id, organization_id)
    try:
        record = client.create_record(
            experiment=llo_entity_id,
            type=REVIEW_TYPE,
            data=data,
            labs_record_id=response_id,
            public=True,
        )
        result = _serialize_record(record)
        result["_public_warning"] = _REVIEW_PUBLIC_WARNING
        return result
    finally:
        client.close()


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
    """Update an existing review by merging update_data into its data dict."""
    token = require_connect_token(user)
    client = _scoped_client(token, program_id, organization_id)
    try:
        current = client.get_record_by_id(review_id, type=REVIEW_TYPE)
        if current is None:
            raise MCPToolError("NOT_FOUND", f"Review {review_id} not found")

        merged_data = dict(current.data or {})
        update_data.pop("is_public", None)
        update_data.pop("public", None)
        merged_data.update(update_data)

        record = client.update_record(
            record_id=review_id,
            experiment=current.experiment,
            type=current.type,
            data=merged_data,
            current_record=current,
            labs_record_id=current.labs_record_id,
        )
        return _serialize_record(record)
    finally:
        client.close()
