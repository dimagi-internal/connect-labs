"""Canonical schema validation for solicitation payloads.

Single source of truth for the shape of a solicitation record's ``data``
dict. Every write path (UI form view, HTTP API, MCP tool) ultimately flows
through :func:`SolicitationsDataAccess.create_solicitation`, which calls
:func:`validate_solicitation_payload`. Nested-shape checks (``questions[]``,
``evaluation_criteria[]``) are enforced here because the Django form
delegates those fields to client-side JS, leaving API-only callers
unprotected against drift — exactly the gap ACE drifted through when it
shipped ``overview``/``response_window_days``/``rubric`` instead of the
canonical names.

The validator raises Django's :class:`ValidationError` so the existing form
machinery surfaces messages naturally; HTTP/MCP callers map the same
exception to their own protocol envelopes (HTTP 400 with ``message_dict``,
MCP ``INVALID_SCHEMA``).
"""
from __future__ import annotations

from datetime import date

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

# =========================================================================
# Canonical enums — imported by forms.py for the UI ChoiceFields so the
# validator and the form stay in lockstep automatically.
# =========================================================================

SOLICITATION_TYPE_CHOICES: tuple[tuple[str, str], ...] = (
    ("eoi", "Expression of Interest (EOI)"),
    ("rfp", "Request for Proposal (RFP)"),
)
STATUS_CHOICES: tuple[tuple[str, str], ...] = (
    ("draft", "Draft"),
    ("active", "Active"),
    ("closed", "Closed"),
    ("awarded", "Awarded"),
)
QUESTION_TYPES: tuple[str, ...] = ("text", "textarea", "number", "multiple_choice")

VALID_SOLICITATION_TYPES: frozenset[str] = frozenset(c[0] for c in SOLICITATION_TYPE_CHOICES)
VALID_STATUSES: frozenset[str] = frozenset(c[0] for c in STATUS_CHOICES)

# Allowed top-level keys persisted into LabsRecord.data. Anything outside
# this set is drift — reject loudly. ``is_public`` lives on the LabsRecord
# envelope, not in data, but callers commonly pass it alongside data and
# the data-access layer strips it before calling the validator; we tolerate
# it here so a stray ``is_public`` in a test payload doesn't false-positive.
ALLOWED_FIELDS: frozenset[str] = frozenset(
    {
        "title",
        "description",
        "scope_of_work",
        "solicitation_type",
        "status",
        "application_deadline",
        "expected_start_date",
        "expected_end_date",
        "estimated_scale",
        "contact_email",
        "program_name",
        "connect_opportunity_id",
        "fund_id",
        "questions",
        "evaluation_criteria",
        "created_by",
    }
)

REQUIRED_FIELDS: tuple[str, ...] = ("title", "description", "solicitation_type")

_QUESTION_FIELDS: frozenset[str] = frozenset({"id", "text", "type", "required", "options"})
_CRITERION_FIELDS: frozenset[str] = frozenset(
    {"id", "name", "weight", "description", "scoring_guide", "linked_questions"}
)


# =========================================================================
# Helpers
# =========================================================================


def _validate_iso_date(value, field_name: str) -> None:
    if value is None or value == "":
        return
    if not isinstance(value, str):
        raise ValidationError({field_name: "must be a YYYY-MM-DD string"})
    try:
        date.fromisoformat(value)
    except ValueError as e:
        raise ValidationError({field_name: f"must be YYYY-MM-DD ({e})"}) from e


def _validate_questions(questions) -> set[str]:
    """Validate questions[] shape and return the set of declared question IDs.

    IDs must be unique within a solicitation — responses are keyed by question
    ID, and duplicates silently overwrite each other in the response shape.
    """
    if not isinstance(questions, list):
        raise ValidationError({"questions": "must be a list"})
    seen_ids: set[str] = set()
    for i, q in enumerate(questions):
        prefix = f"questions[{i}]"
        if not isinstance(q, dict):
            raise ValidationError({prefix: "must be an object"})
        unknown = set(q.keys()) - _QUESTION_FIELDS
        if unknown:
            raise ValidationError({prefix: f"unknown keys {sorted(unknown)}"})

        q_id = q.get("id")
        if not isinstance(q_id, str) or not q_id:
            raise ValidationError({f"{prefix}.id": "must be a non-empty string"})
        if q_id in seen_ids:
            raise ValidationError({f"{prefix}.id": f"{q_id!r} duplicates an earlier question"})
        seen_ids.add(q_id)

        if not isinstance(q.get("text"), str) or not q["text"]:
            raise ValidationError({f"{prefix}.text": "must be a non-empty string"})

        q_type = q.get("type")
        if q_type not in QUESTION_TYPES:
            raise ValidationError({f"{prefix}.type": f"{q_type!r} must be one of {sorted(QUESTION_TYPES)}"})

        if "required" in q and not isinstance(q["required"], bool):
            raise ValidationError({f"{prefix}.required": "must be a boolean"})

        if q_type == "multiple_choice":
            opts = q.get("options")
            if not isinstance(opts, list) or not opts:
                raise ValidationError({f"{prefix}.options": "required and non-empty when type=multiple_choice"})
            if not all(isinstance(o, str) for o in opts):
                raise ValidationError({f"{prefix}.options": "must be strings"})

    return seen_ids


def _validate_evaluation_criteria(criteria, question_ids: set[str]) -> None:
    """Validate evaluation_criteria[] shape.

    ``linked_questions`` must reference IDs in the questions[] list — dangling
    references silently break the reviewer UI.
    """
    if not isinstance(criteria, list):
        raise ValidationError({"evaluation_criteria": "must be a list"})
    seen_ids: set[str] = set()
    for i, c in enumerate(criteria):
        prefix = f"evaluation_criteria[{i}]"
        if not isinstance(c, dict):
            raise ValidationError({prefix: "must be an object"})
        unknown = set(c.keys()) - _CRITERION_FIELDS
        if unknown:
            raise ValidationError({prefix: f"unknown keys {sorted(unknown)}"})

        c_id = c.get("id")
        if not isinstance(c_id, str) or not c_id:
            raise ValidationError({f"{prefix}.id": "must be a non-empty string"})
        if c_id in seen_ids:
            raise ValidationError({f"{prefix}.id": f"{c_id!r} duplicates an earlier criterion"})
        seen_ids.add(c_id)

        if not isinstance(c.get("name"), str) or not c["name"]:
            raise ValidationError({f"{prefix}.name": "must be a non-empty string"})

        weight = c.get("weight")
        if not isinstance(weight, (int, float)) or isinstance(weight, bool):
            raise ValidationError({f"{prefix}.weight": "must be a number"})
        if weight < 0 or weight > 100:
            raise ValidationError({f"{prefix}.weight": f"{weight} must be in [0, 100]"})

        linked = c.get("linked_questions", [])
        if not isinstance(linked, list) or not all(isinstance(q, str) for q in linked):
            raise ValidationError({f"{prefix}.linked_questions": "must be a list of strings"})
        dangling = [q for q in linked if q not in question_ids]
        if dangling:
            raise ValidationError({f"{prefix}.linked_questions": f"{dangling} reference unknown question IDs"})


# =========================================================================
# Public entry point
# =========================================================================


def validate_solicitation_payload(data, *, partial: bool = False) -> None:
    """Validate a solicitation payload against the canonical schema.

    Raises :class:`ValidationError` on any drift. Callers map to their
    protocol error envelope (HTTP 400, MCP INVALID_SCHEMA, form
    non_field_errors).

    With ``partial=False`` (default — create path), ``REQUIRED_FIELDS`` are
    enforced.

    With ``partial=True`` (update path), required fields are skipped — callers
    send only the keys they want to change — but every other check
    (unknown-field rejection, enums, dates, email, nested shapes,
    linked-questions references) applies identically.

    Cross-field invariants like "evaluation_criteria.linked_questions must
    reference declared question ids" are validated against whatever ``data``
    the caller passes — so for partial updates, the caller must merge the
    update_data with the existing record's data BEFORE validating, otherwise
    a criteria-only update would falsely reject for unknown question ids that
    actually live on the existing record. The MCP update_solicitation tool
    handles this by fetching, merging, then validating the merged shape.
    """
    if not isinstance(data, dict):
        raise ValidationError({"data": "must be a dict"})

    unknown = set(data.keys()) - ALLOWED_FIELDS - {"is_public"}
    if unknown:
        raise ValidationError(
            {"data": f"unknown fields: {sorted(unknown)} — see solicitations.validation.ALLOWED_FIELDS"}
        )

    if not partial:
        for field in REQUIRED_FIELDS:
            value = data.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValidationError({field: "required, must be a non-empty string"})

    if "solicitation_type" in data:
        s_type = data["solicitation_type"]
        if s_type not in VALID_SOLICITATION_TYPES:
            raise ValidationError(
                {"solicitation_type": f"{s_type!r} must be one of {sorted(VALID_SOLICITATION_TYPES)}"}
            )

    if "status" in data:
        status = data["status"]
        if status not in VALID_STATUSES:
            raise ValidationError({"status": f"{status!r} must be one of {sorted(VALID_STATUSES)}"})

    _validate_iso_date(data.get("application_deadline"), "application_deadline")
    _validate_iso_date(data.get("expected_start_date"), "expected_start_date")
    _validate_iso_date(data.get("expected_end_date"), "expected_end_date")

    email = data.get("contact_email")
    if email:
        # Use Django's validator (same one SolicitationForm.contact_email uses
        # via EmailField) so the MCP/API paths agree with the UI on what counts
        # as a valid address.
        try:
            validate_email(email)
        except ValidationError as e:
            raise ValidationError({"contact_email": "must be a valid email address"}) from e

    coid = data.get("connect_opportunity_id")
    if coid is not None and (not isinstance(coid, int) or isinstance(coid, bool)):
        raise ValidationError({"connect_opportunity_id": "must be an integer"})

    fid = data.get("fund_id")
    if fid is not None and (not isinstance(fid, int) or isinstance(fid, bool)):
        raise ValidationError({"fund_id": "must be an integer"})

    questions = data.get("questions")
    question_ids = _validate_questions(questions) if questions is not None else set()

    criteria = data.get("evaluation_criteria")
    if criteria is not None:
        _validate_evaluation_criteria(criteria, question_ids)
