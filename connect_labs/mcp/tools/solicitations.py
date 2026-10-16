"""Solicitation tools: calls for partners, their responses and awards.

Solicitations live in the labs database, owned by an organisation
(``solicitations/local_models.py``). Every tool runs as the caller through
``SolicitationsDataAccess``, which enforces standing (``solicitations/access.py``):
the owning organisation's members and platform support manage a call and read its
responses; a respondent reads their own; everyone reads listed calls. Tool names,
arguments and output shapes are unchanged from the LabsRecord era; ``program_id``
and ``organization_id`` are accepted where they always were, as filters.
"""

from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied, ValidationError

from connect_labs.labs.integrations.connect.api_client import LabsRecordAPIClient
from connect_labs.solicitations import access
from connect_labs.solicitations.data_access import AlreadyAwarded, HasResponses, SolicitationsDataAccess
from connect_labs.solicitations.local_models import Solicitation, SolicitationResponse
from connect_labs.solicitations.validation import (
    QUESTION_TYPES,
    VALID_SOLICITATION_TYPES,
    VALID_STATUSES,
)

from ..connect_token import require_connect_token
from ..tool_registry import MCPToolError, register  # noqa: F401
from .funds import FUND_TYPE, _add_allocation_to_fund

logger = logging.getLogger(__name__)


def _serialize_record(record) -> dict:
    """The flat envelope + data dict every solicitation tool has always returned."""
    return record.to_record_dict()


def _serialize_response(record, user) -> dict:
    """A response, with the owner's working fields only for someone who manages its call."""
    return record.to_record_dict(owner_view=access.can_manage(user, record.solicitation))


def _da(user, program_id=None, organization_id=None, access_token=None) -> SolicitationsDataAccess:
    return SolicitationsDataAccess(
        user=user, program_id=program_id, organization_id=organization_id, access_token=access_token
    )


def _mcp_error(exc: Exception) -> MCPToolError:
    if isinstance(exc, ValidationError):
        return _validation_error_to_mcp(exc)
    if isinstance(exc, PermissionDenied):
        return MCPToolError("PERMISSION_DENIED", str(exc) or "Not allowed")
    if isinstance(exc, (Solicitation.DoesNotExist, SolicitationResponse.DoesNotExist)):
        return MCPToolError("NOT_FOUND", str(exc))
    if isinstance(exc, (HasResponses, AlreadyAwarded)):
        return MCPToolError("FAILED_PRECONDITION", str(exc))
    raise exc


def _coerce_id(value: str | int | None) -> int | None:
    """Coerce an ID to int; return None for empty/invalid values."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


_SCOPE_NOTE = (
    "program_id narrows to calls for that Connect programme; organization_id (an organisation "
    "slug or Connect id) to calls that organisation owns."
)


# ---------------------------------------------------------------------------
# Read tools (is_write=False)
# ---------------------------------------------------------------------------


@register(
    name="list_solicitations",
    description=(
        "List solicitations (calls for partners). Returns the calls the caller's organisations own "
        "(platform support: every call) plus every publicly listed call. " + _SCOPE_NOTE
    ),
    input_schema={
        "type": "object",
        "properties": {
            "program_id": {
                "type": "string",
                "description": "Only calls for this Connect programme.",
            },
            "organization_id": {
                "type": "string",
                "description": (
                    "Only calls owned by this organisation (labs org slug, Connect slug or Connect id). "
                    "Unlisted ones appear only if you belong to it."
                ),
            },
            "status": {
                "type": "string",
                "description": "Filter by solicitation status (e.g. 'active', 'closed').",
            },
            "solicitation_type": {
                "type": "string",
                "description": "Filter by solicitation_type ('eoi' or 'rfp').",
            },
        },
        "additionalProperties": False,
    },
)
def list_solicitations(
    user,
    program_id: str | None = None,
    organization_id: str | None = None,
    status: str | None = None,
    solicitation_type: str | None = None,
) -> dict:
    da = _da(user)
    records = da.get_solicitations(
        status=status,
        solicitation_type=solicitation_type,
        org=organization_id or None,
        program_id=_coerce_id(program_id),
        include_listed=True,
    )
    return {"solicitations": [_serialize_record(r) for r in records]}


@register(
    name="get_solicitation",
    description=(
        "Get a single solicitation by id (a prod LabsRecord id for an imported call, else its labs id). "
        "Unlisted calls are readable by their owner and by anyone who responded to them."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "solicitation_id": {
                "type": "integer",
                "description": "The solicitation's id.",
            },
            "program_id": {
                "type": "string",
                "description": "Accepted for compatibility; not needed.",
            },
            "organization_id": {
                "type": "string",
                "description": "Accepted for compatibility; not needed.",
            },
        },
        "required": ["solicitation_id"],
        "additionalProperties": False,
    },
)
def get_solicitation(
    user,
    solicitation_id: int,
    program_id: str | None = None,
    organization_id: str | None = None,
) -> dict:
    record = _da(user).get_solicitation_by_id(solicitation_id)
    if record is None:
        raise MCPToolError("NOT_FOUND", f"Solicitation {solicitation_id} not found")
    return _serialize_record(record)


@register(
    name="list_responses",
    description=(
        "List the responses to a solicitation. The owning organisation (and platform support) sees "
        "every submitted response; anyone else sees only their own. Responses are confidential to the "
        "owner: an applicant's answers, name and email are never shown to other applicants."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "solicitation_id": {
                "type": "integer",
                "description": "The parent solicitation's id.",
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
        "required": ["solicitation_id"],
        "additionalProperties": False,
    },
)
def list_responses(
    user,
    solicitation_id: int,
    program_id: str | None = None,
    organization_id: str | None = None,
) -> dict:
    da = _da(user)
    if da.get_solicitation_by_id(solicitation_id) is None:
        raise MCPToolError("NOT_FOUND", f"Solicitation {solicitation_id} not found")
    records = da.get_responses_for_solicitation(solicitation_id)
    return {"responses": [_serialize_response(r, user) for r in records]}


@register(
    name="get_response",
    description=(
        "Get a single solicitation response by id. Readable by the call's owner (and platform support) "
        "once submitted, and by the respondent."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "response_id": {
                "type": "integer",
                "description": "The response's id.",
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
        "required": ["response_id"],
        "additionalProperties": False,
    },
)
def get_response(
    user,
    response_id: int,
    program_id: str | None = None,
    organization_id: str | None = None,
) -> dict:
    record = _da(user).get_response_by_id(response_id)
    if record is None:
        raise MCPToolError("NOT_FOUND", f"Response {response_id} not found")
    return _serialize_response(record, user)


# ---------------------------------------------------------------------------
# Write tools (is_write=True)
# ---------------------------------------------------------------------------


# Canonical solicitation field properties — shared between create's top-level
# schema and update's update_data schema so both surfaces stay in lockstep with
# connect_labs.solicitations.validation.ALLOWED_FIELDS. When you add a new
# canonical field, edit it here and in validation.py once.
_CANONICAL_FIELD_PROPERTIES = {
    "title": {"type": "string", "maxLength": 255},
    "description": {"type": "string", "description": "Markdown."},
    "scope_of_work": {"type": "string", "description": "Markdown."},
    "solicitation_type": {"type": "string", "enum": sorted(VALID_SOLICITATION_TYPES)},
    "status": {"type": "string", "enum": sorted(VALID_STATUSES)},
    "application_deadline": {"type": "string", "format": "date", "description": "YYYY-MM-DD."},
    "expected_start_date": {"type": "string", "format": "date", "description": "YYYY-MM-DD."},
    "expected_end_date": {"type": "string", "format": "date", "description": "YYYY-MM-DD."},
    "estimated_scale": {"type": "string"},
    "contact_email": {"type": "string", "format": "email"},
    "program_name": {
        "type": "string",
        "description": "Optional human-readable program name shown on the public detail page.",
    },
    "connect_opportunity_id": {
        "type": "integer",
        "description": (
            "Connect Opportunity ID this solicitation targets. Stored on the record so "
            "downstream review/award flows can link back to the opp."
        ),
    },
    "fund_id": {
        "type": "integer",
        "description": "Optional fund ID for downstream allocation when a response is awarded.",
    },
    "currency": {
        "type": "string",
        "description": "ISO 4217 code the award is paid in (e.g. 'USD', 'KES'); shown on the award page.",
    },
    "questions": {
        "type": "array",
        "description": (
            "Application questions respondents must answer. Each question's `id` must be unique "
            "within the solicitation — responses are keyed by id."
        ),
        "items": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "text": {"type": "string"},
                "type": {"type": "string", "enum": sorted(QUESTION_TYPES)},
                "required": {"type": "boolean", "default": False},
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Required when type=multiple_choice.",
                },
                "framing": {
                    "type": "string",
                    "description": (
                        "Optional 1-2 sentence 'why we're asking' preface. Rendered "
                        "above the prompt in the public detail template so respondents "
                        "see the intent of the question, not just the prompt. Structured "
                        "field (vs. prose-prefixed text) so downstream consumers can "
                        "render or score against it cleanly."
                    ),
                },
            },
            "required": ["id", "text", "type"],
            "additionalProperties": False,
        },
    },
    "plans": {
        "type": "array",
        "description": (
            "Coverage snapshot carried from a micro-plan (the create form's Coverage-areas "
            "panel). Same shape the UI snapshots: plan_id/name plus optional region, wards[], "
            "arms[], work_area_count, population. Validated by validation._validate_plans."
        ),
        "items": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "integer"},
                "name": {"type": "string"},
                "region": {"type": "string"},
                "wards": {"type": "array", "items": {"type": "string"}},
                "arms": {"type": "array", "items": {"type": "string"}},
                "work_area_count": {"type": "integer"},
                "population": {"type": "integer"},
            },
            "required": ["plan_id", "name"],
            "additionalProperties": False,
        },
    },
    "source_group_id": {
        "type": "integer",
        "description": "Micro-plan study-group id this solicitation was created from.",
    },
    "source_plan_ids": {
        "type": "array",
        "items": {"type": "integer"},
        "description": "Micro-plan plan ids snapshotted into `plans`.",
    },
    "evaluation_criteria": {
        "type": "array",
        "description": (
            "Rubric used to score responses. `linked_questions` entries must reference IDs " "in the questions[] list."
        ),
        "items": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "weight": {"type": "number", "minimum": 0, "maximum": 100},
                "description": {"type": "string"},
                "scoring_guide": {"type": "string"},
                "linked_questions": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["id", "name", "weight"],
            "additionalProperties": False,
        },
    },
}


def _validation_error_to_mcp(exc: ValidationError) -> MCPToolError:
    """Convert a Django ValidationError to an MCP INVALID_SCHEMA error.

    Preserves the field-level structure via ``details`` so callers can render
    per-field messages instead of a stringified blob.
    """
    if hasattr(exc, "message_dict"):
        details = {"fields": exc.message_dict}
        # Render a one-line summary for the message text.
        summary = "; ".join(
            f"{field}: {msgs[0] if isinstance(msgs, list) else msgs}" for field, msgs in exc.message_dict.items()
        )
    else:
        details = {"messages": list(exc.messages)}
        summary = "; ".join(exc.messages)
    return MCPToolError("INVALID_SCHEMA", summary, details=details)


@register(
    name="create_solicitation",
    description=(
        "Create a solicitation (a call for partners) owned by an organisation. Name the owner with "
        "org_slug (preferred: a labs organisation slug you belong to); or pass program_id and the "
        "programme's running organisation owns it, else the programme's own organisation if you belong "
        "to it (a labs-only programme: its synthetic organisation) -- otherwise refused with a message "
        "naming org_slug. Fields are flat -- do NOT wrap them in a `data` object. Unknown fields are "
        "rejected. Set is_public=true to list it on the public /solicitations/ directory (its content "
        "is public-facing by design; do NOT include PII or pipeline-derived data). Schema is enforced "
        "at the data-access layer (connect_labs.solicitations.validation), shared with the UI form."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "org_slug": {
                "type": "string",
                "description": "The owning organisation: a labs org slug (or Connect slug/id) you belong to.",
            },
            "program_id": {
                "type": "string",
                "description": (
                    "Connect programme this call is for. Without org_slug, its running organisation (else its "
                    "own organisation, if you belong to it) owns the call."
                ),
            },
            "organization_id": {
                "type": "string",
                "description": "Alternative to org_slug: the owning organisation's Connect id or slug.",
            },
            "is_public": {"type": "boolean", "default": False},
            **_CANONICAL_FIELD_PROPERTIES,
        },
        "required": ["title", "description", "solicitation_type"],
        "additionalProperties": False,
    },
    is_write=True,
)
def create_solicitation(
    user,
    org_slug: str | None = None,
    program_id: str | None = None,
    organization_id: str | None = None,
    is_public: bool = False,
    **fields,
) -> dict:
    """Thin shim over SolicitationsDataAccess.create_solicitation (validation lives there).

    ``**fields`` keeps unknown top-level kwargs from raising TypeError at the
    transport's unpack -- the validator rejects them with INVALID_SCHEMA.
    """
    if not isinstance(is_public, bool):
        raise MCPToolError("INVALID_SCHEMA", "is_public must be a boolean")
    data = dict(fields)
    data.pop("created_by", None)  # the caller is the creator
    data["is_public"] = is_public
    if org_slug:
        data["org_slug"] = org_slug
    if organization_id:
        data["organization_id"] = organization_id
    if program_id:
        data["program_id"] = program_id
    try:
        return _serialize_record(_da(user).create_solicitation(data))
    except (ValidationError, PermissionDenied) as e:
        raise _mcp_error(e) from e


@register(
    name="create_response",
    description=(
        "Create a solicitation response. Built for server-side seeding of demo/synthetic responses "
        "(named firms with per-question answers): when the caller manages the call (its owner or "
        "platform support) the response is entered on the firm's behalf -- no respondent user, any "
        "named organisation, deadline ignored. Anyone else responds as themselves, to an open call, "
        "for an organisation they belong to (llo_entity_id: its labs slug; 'individual' for none), once: "
        "a second response after theirs is submitted is refused. "
        "`responses` maps question id -> answer text."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "solicitation_id": {
                "type": "integer",
                "description": "Id of the solicitation being responded to.",
            },
            "program_id": {
                "type": ["integer", "string"],
                "description": "Accepted for compatibility; not needed.",
            },
            "organization_id": {
                "type": ["integer", "string"],
                "description": "Accepted for compatibility; not needed.",
            },
            "llo_entity_id": {
                "type": "string",
                "default": "individual",
                "description": "The responding organisation's labs slug, or 'individual'.",
            },
            "responses": {
                "type": "object",
                "description": "Answers keyed by question id (question_id -> answer text).",
            },
            "status": {
                "type": "string",
                "enum": ["draft", "submitted"],
                "default": "submitted",
            },
            "submitted_by_name": {"type": "string"},
            "submitted_by_email": {"type": "string"},
            "org_name": {"type": "string"},
            "org_id": {"type": "string"},
            "submission_date": {
                "type": "string",
                "description": "ISO timestamp; defaults to now when omitted.",
            },
            "selected_plan_ids": {"type": "array", "items": {"type": ["integer", "string"]}},
            "selected_plan_names": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["solicitation_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def create_response(
    user,
    solicitation_id: int,
    program_id: str | None = None,
    organization_id: str | None = None,
    llo_entity_id: str = "individual",
    responses: dict | None = None,
    status: str = "submitted",
    submitted_by_name: str = "",
    submitted_by_email: str = "",
    org_name: str = "",
    org_id: str = "",
    submission_date: str = "",
    selected_plan_ids: list | None = None,
    selected_plan_names: list | None = None,
) -> dict:
    data = {
        "responses": responses or {},
        "status": status,
        "submitted_by_name": submitted_by_name,
        "submitted_by_email": submitted_by_email,
        "org_name": org_name or submitted_by_name,
        "org_id": org_id,
        "submission_date": submission_date,
    }
    if selected_plan_ids:
        data["selected_plan_ids"] = selected_plan_ids
    if selected_plan_names:
        data["selected_plan_names"] = selected_plan_names
    da = _da(user)
    solicitation = da.get_solicitation_by_id(solicitation_id)
    if solicitation is None:
        raise MCPToolError("NOT_FOUND", f"Solicitation {solicitation_id} not found")
    try:
        record = da.create_response(
            solicitation_id=solicitation_id,
            llo_entity_id=llo_entity_id,
            data=data,
            on_behalf=da.can_manage(solicitation),
        )
    except (ValidationError, PermissionDenied, Solicitation.DoesNotExist) as e:
        raise _mcp_error(e) from e
    return _serialize_response(record, user)


@register(
    name="update_solicitation",
    description=(
        "Update a solicitation you manage (its owning organisation, or platform support). Merges "
        "update_data into the call; fields you do not send are kept. Setting `is_public` lists or "
        "unlists it; omit it to leave visibility unchanged. Validated against the same canonical "
        "schema as create_solicitation (partial mode). Once the rubric is locked "
        "(criteria_locked), evaluation_criteria cannot change and the lock cannot be lifted."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "solicitation_id": {
                "type": "integer",
                "description": "The id of the solicitation to update.",
            },
            "update_data": {
                "type": "object",
                "description": (
                    "Fields to update. Merged (shallow) into the call. "
                    "Same canonical shape as create_solicitation, all fields optional."
                ),
                "properties": {
                    "is_public": {"type": "boolean"},
                    **_CANONICAL_FIELD_PROPERTIES,
                },
                "additionalProperties": False,
            },
            "program_id": {
                "type": "string",
                "description": "Accepted for compatibility; not needed.",
            },
            "organization_id": {
                "type": "string",
                "description": "Accepted for compatibility; not needed.",
            },
        },
        "required": ["solicitation_id", "update_data"],
        "additionalProperties": False,
    },
    is_write=True,
)
def update_solicitation(
    user,
    solicitation_id: int,
    update_data: dict,
    program_id: str | None = None,
    organization_id: str | None = None,
) -> dict:
    data = {k: v for k, v in dict(update_data).items() if k != "public"}
    try:
        return _serialize_record(_da(user).update_solicitation(solicitation_id, data))
    except (ValidationError, PermissionDenied, Solicitation.DoesNotExist) as e:
        raise _mcp_error(e) from e


@register(
    name="delete_solicitation",
    description=(
        "Delete a solicitation you manage and cascade-delete its responses and reviews. Refuses a "
        "solicitation with any responses or reviews unless force=true; the cascade count is reported. "
        "Status is not consulted -- the gate is the actual cascade. IRREVERSIBLE -- used by ACE sweep "
        "to clean up orphan dogfood solicitations."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "solicitation_id": {
                "type": "integer",
                "description": "The id of the solicitation to delete.",
            },
            "program_id": {
                "type": "string",
                "description": "Accepted for compatibility; not needed.",
            },
            "organization_id": {
                "type": "string",
                "description": "Accepted for compatibility; not needed.",
            },
            "force": {
                "type": "boolean",
                "description": (
                    "Override the cascade guard. When false (default), the tool refuses "
                    "to delete a solicitation with any responses or reviews. When true, "
                    "all child records are destroyed alongside the parent."
                ),
            },
        },
        "required": ["solicitation_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def delete_solicitation(
    user,
    solicitation_id: int,
    program_id: str | None = None,
    organization_id: str | None = None,
    force: bool = False,
) -> dict:
    try:
        deleted = _da(user).delete_solicitation(solicitation_id, force=force)
    except HasResponses as e:
        raise MCPToolError(
            "FAILED_PRECONDITION",
            (
                f"Cannot delete solicitation {solicitation_id}: {e.responses} responses + {e.reviews} reviews "
                "would be destroyed. Pass force=true to override."
            ),
        ) from e
    except (PermissionDenied, Solicitation.DoesNotExist) as e:
        raise _mcp_error(e) from e
    return {"solicitation_id": solicitation_id, "deleted": deleted}


@register(
    name="award_response",
    description=(
        "Award a solicitation response: marks it awarded with reward_budget and org_id, and emails the "
        "awardee when labs email is on (awardee_notified says whether it was sent). Only the call's "
        "owning organisation or platform support may award, and never a response from their own side. "
        "A response that is already awarded is refused (FAILED_PRECONDITION): no second allocation or email. "
        "If fund_id is provided, appends an allocation entry to that fund's allocations array: the fund "
        "is checked first (NOT_FOUND, nothing awarded), and if the allocation fails after the award the "
        "awarded response comes back with an allocation_error field."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "response_id": {
                "type": "integer",
                "description": "The id of the response to award.",
            },
            "reward_budget": {
                "type": "integer",
                "description": "The reward amount to grant to the respondent's organization.",
            },
            "org_id": {
                "type": "string",
                "description": "The organization ID of the winning respondent.",
            },
            "fund_id": {
                "type": "integer",
                "description": (
                    "Optional fund ID. When provided, an allocation entry is appended to the fund's "
                    "allocations array. The fund_id is explicit (not derived from the solicitation) "
                    "so callers have full control."
                ),
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
        "required": ["response_id", "reward_budget", "org_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def award_response(
    user,
    response_id: int,
    reward_budget: int,
    org_id: str,
    fund_id: int | None = None,
    program_id: str | None = None,
    organization_id: str | None = None,
) -> dict:
    """Award a response, and allocate from `fund_id` (a prod fund record) when given.

    The fund is checked before the award (a missing fund is NOT_FOUND and nothing
    is awarded). Once awarded, a failed allocation is reported in
    `allocation_error` rather than raised: a retry would be refused as already
    awarded, so raising would leave the caller believing the award failed.
    """
    client = LabsRecordAPIClient(access_token=require_connect_token(user)) if fund_id else None
    try:
        if client is not None and client.get_record_by_id(fund_id, type=FUND_TYPE) is None:
            raise MCPToolError("NOT_FOUND", f"No fund with id {fund_id}")
        try:
            response = _da(user).award_response(
                response_id, reward_budget=reward_budget, org_id=org_id, allocate_fund=False
            )
        except (PermissionDenied, SolicitationResponse.DoesNotExist, AlreadyAwarded) as e:
            raise _mcp_error(e) from e

        allocation_error = None
        if client is not None:
            solicitation = response.solicitation
            allocation = {
                "amount": reward_budget,
                "type": "award",
                "solicitation_id": solicitation.public_id,
                "response_id": response.pk,
                "org_id": org_id,
                "org_name": response.display_name,
                "notes": f"Award from {solicitation.title}" if solicitation.title else "Award",
            }
            try:
                _add_allocation_to_fund(client, fund_id, allocation)
            except Exception as e:  # noqa: BLE001 -- reported to the caller, never raised past the award
                logger.exception("award of response %s: allocation to fund %s failed", response.pk, fund_id)
                allocation_error = f"The response is awarded, but the allocation to fund {fund_id} failed: {e}"
    finally:
        if client is not None:
            client.close()
    result = _serialize_response(response, user)
    if allocation_error:
        result["allocation_error"] = allocation_error
    return result
