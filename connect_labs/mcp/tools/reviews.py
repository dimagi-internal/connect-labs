"""Review tools: an owner's scoring of the responses to its call.

Reviews live in the labs database (``SolicitationReview``) and are the owning
organisation's working notes: only its members and platform support read or
write them, and never on a response from their own side (see
``solicitations/access.py``). Each reviewer has at most one review of each
kind (``ai``, ``human``) per response, and several people may review one.
"""

from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied, ValidationError

from connect_labs.solicitations import vocabulary
from connect_labs.solicitations.data_access import SolicitationsDataAccess
from connect_labs.solicitations.local_models import SolicitationResponse, SolicitationReview

from ..tool_registry import MCPToolError, register  # noqa: F401

# Each word with what it means, so an agent writes the same vocabulary a person reads.
_OUTCOME_HELP = "For a marketplace round, the recommendation: " + "; ".join(
    f"{key} = {m['label']} ({m['definition']})" for key, m in vocabulary.OUTCOMES.items()
)
_FLAG_HELP = (
    "Things to check before relying on the response; a flag costs no points by itself, and each needs a "
    "`flag` claim saying what raised it. Only these: "
    + "; ".join(f"{key} = {m['label']}: {m['definition']}" for key, m in vocabulary.FLAGS.items())
)

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
        "response from their own side; for a marketplace EOI round that is the round's owner "
        "(responses come from `marketplace_round_responses`, which gives each `response_id`). "
        "When the call has weighted evaluation criteria and you send criteria_scores, the overall "
        "score is computed from them. An AI first pass is `kind='ai'`: one per response per caller, "
        "re-running replaces it, and it never takes the place of a person's own review. A person "
        "starting from another review passes `based_on` and, to say what they make of it, `stance`. "
        "You MUST pass public_record_acknowledged=true and confirm no field contains PII or contact details."
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
            "kind": {"type": "string", "enum": ["ai", "human"], "description": "Who wrote it. Default human."},
            "based_on": {"type": "integer", "description": "The review (of this response) this one started from."},
            "stance": {
                "type": "string",
                "enum": ["agrees", "amended", "adds", "disagrees", ""],
                "description": (
                    "What this reviewer makes of the review in `based_on`. "
                    "`adds` stores only the additions; readers see the original with them marked."
                ),
            },
            "criteria_notes": {
                "type": "object",
                "description": "criterion_id -> one line on why it scored that, citing the answer.",
                "additionalProperties": True,
            },
            "outcome": {
                "type": "string",
                "enum": ["pick", "backup", "waitlist", "pass", ""],
                "description": _OUTCOME_HELP,
            },
            "strengths": {"type": "string", "description": "Strengths, citing the answers."},
            "concerns": {"type": "string", "description": "Concerns, citing the answers."},
            "ask_next": {"type": "string", "description": "Questions to ask the applicant before deciding."},
            "flags": {
                "type": "array",
                "items": {"type": "string", "enum": list(vocabulary.FLAGS)},
                "description": _FLAG_HELP,
            },
            "provenance": {
                "type": "object",
                "description": "For an AI review: model, skill, skill_version, rubric_version, sources.",
                "additionalProperties": True,
            },
            "claims": {
                "type": "array",
                "description": (
                    "The review's statements, each with the response text it rests on. Each is "
                    "{id, about: 'criterion:<id>'|'strength'|'concern'|'flag'|'outcome', text, "
                    "basis: 'quoted'|'synthesized', cites: [...]}; a cite is {question, quote} (exact "
                    "words from that answer, checked on save), {question, absent: true}, "
                    "{question, whole: true}, or {source: 'labs'|'web', detail, url}. A synthesized "
                    "claim may cite nothing. When sent, strengths, concerns and criteria_notes are "
                    "written from the claims. A quote not found in its answer refuses the review."
                ),
                "items": {"type": "object", "additionalProperties": True},
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
    kind: str = "human",
    based_on: int | None = None,
    stance: str = "",
    criteria_notes: dict | None = None,
    outcome: str = "",
    strengths: str = "",
    concerns: str = "",
    ask_next: str = "",
    flags: list | None = None,
    provenance: dict | None = None,
    claims: list | None = None,
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
    data["kind"] = kind
    for name, value in (
        ("based_on", based_on),
        ("stance", stance),
        ("criteria_notes", criteria_notes),
        ("outcome", outcome),
        ("strengths", strengths),
        ("concerns", concerns),
        ("ask_next", ask_next),
        ("flags", flags),
        ("provenance", provenance),
        ("claims", claims),
    ):
        if value:
            data[name] = value
    try:
        record = SolicitationsDataAccess(user=user).create_review(response_id, data)
    except (PermissionDenied, SolicitationResponse.DoesNotExist) as e:
        raise _errors(e) from e
    except ValidationError as e:
        raise MCPToolError("BAD_REQUEST", "; ".join(e.messages)) from e
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
    except ValidationError as e:
        raise MCPToolError("BAD_REQUEST", "; ".join(e.messages)) from e
    return _serialize_record(record)


@register(
    name="delete_review",
    description=(
        "Delete one of your own reviews (or, as platform support, anyone's). It cannot be undone. "
        "Reviews that agreed with or added to it keep their own content and lose the link. "
        "Get the requester's approval first."
    ),
    input_schema={
        "type": "object",
        "properties": {"review_id": {"type": "integer", "description": "The review to delete."}},
        "required": ["review_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def delete_review(user, review_id: int) -> dict:
    try:
        response_id = SolicitationsDataAccess(user=user).delete_review(review_id)
    except (PermissionDenied, SolicitationReview.DoesNotExist) as e:
        raise _errors(e) from e
    return {"deleted": review_id, "response_id": response_id}
