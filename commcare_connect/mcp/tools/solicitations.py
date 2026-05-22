"""Solicitation tools — migrated from _pending_migration/solicitation_tools.py.

Solicitations are LabsRecord entries with type="solicitation".
Responses are LabsRecord entries with type="solicitation_response".
"""

from __future__ import annotations

import logging

from commcare_connect.labs.integrations.connect.api_client import LabsRecordAPIClient

from ..connect_token import require_connect_token
from ..tool_registry import MCPToolError, register  # noqa: F401
from .funds import _add_allocation_to_fund

logger = logging.getLogger(__name__)


def _serialize_record(record) -> dict:
    """Flatten a LocalLabsRecord into a plain dict matching the original shape.

    The original code merged the record's ``data`` dict into the outer envelope,
    giving callers a single flat dict with id/experiment/type/program_id/labs_record_id
    plus all application-level fields (title, status, etc.) at the top level.

    ``is_public`` is the canonical name for the public-listing flag and is
    sourced from ``record.public`` (the server-side LabsRecord.public column).
    Any stale ``is_public`` inside ``data`` from legacy records is dropped
    before the spread, so the response has exactly one source of truth.
    """
    data = {k: v for k, v in (record.data or {}).items() if k != "is_public"}
    return {
        "id": record.id,
        "experiment": record.experiment,
        "type": record.type,
        "program_id": record.program_id,
        "labs_record_id": record.labs_record_id,
        **data,
        "is_public": bool(record.public),
    }


# ---------------------------------------------------------------------------
# Read tools (is_write=False)
# ---------------------------------------------------------------------------


def _coerce_id(value: str | int | None) -> int | None:
    """Coerce an ID to int; return None for empty/invalid values."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@register(
    name="list_solicitations",
    description=(
        "List solicitations from the Labs Record API. "
        "Pass program_id or organization_id to scope the read so the prod-side "
        "membership check authorizes non-public records — without scope, only "
        "is_public=true records are returned."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "program_id": {
                "type": "string",
                "description": (
                    "Program ID to scope the listing. Used both as an experiment "
                    "filter and as the prod-side membership scope that authorizes "
                    "non-public records."
                ),
            },
            "organization_id": {
                "type": "string",
                "description": (
                    "Organization ID to scope the listing when program_id is absent. "
                    "Authorizes non-public records via org membership."
                ),
            },
            "status": {
                "type": "string",
                "description": "Filter by solicitation status (e.g. 'active', 'closed').",
            },
            "solicitation_type": {
                "type": "string",
                "description": "Filter by solicitation_type field inside the data JSON.",
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
    """List solicitations from the Labs Record API."""
    token = require_connect_token(user)
    client = LabsRecordAPIClient(
        access_token=token,
        program_id=_coerce_id(program_id),
        organization_id=_coerce_id(organization_id),
    )
    try:
        kwargs: dict = {"type": "solicitation"}
        experiment = program_id or organization_id
        if experiment:
            kwargs["experiment"] = experiment
        if status:
            kwargs["status"] = status
        if solicitation_type:
            kwargs["solicitation_type"] = solicitation_type

        records = client.get_records(**kwargs)
        return {"solicitations": [_serialize_record(r) for r in records]}
    finally:
        client.close()


@register(
    name="get_solicitation",
    description=(
        "Get a single solicitation by its Labs Record ID. "
        "Pass program_id (or organization_id) to read non-public records — without "
        "scope, prod returns only is_public=true solicitations."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "solicitation_id": {
                "type": "integer",
                "description": "The Labs Record ID of the solicitation.",
            },
            "program_id": {
                "type": "string",
                "description": (
                    "Program ID that owns the solicitation. Required to read non-public "
                    "records (prod authorizes via program membership)."
                ),
            },
            "organization_id": {
                "type": "string",
                "description": (
                    "Organization ID alternative to program_id when the solicitation is "
                    "scoped to an org rather than a program."
                ),
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
    """Get a single solicitation by ID. Returns the record or raises NOT_FOUND."""
    token = require_connect_token(user)
    client = LabsRecordAPIClient(
        access_token=token,
        program_id=_coerce_id(program_id),
        organization_id=_coerce_id(organization_id),
    )
    try:
        record = client.get_record_by_id(solicitation_id, type="solicitation")
        if record is None:
            raise MCPToolError("NOT_FOUND", f"Solicitation {solicitation_id} not found")
        return _serialize_record(record)
    finally:
        client.close()


@register(
    name="list_responses",
    description="List all responses submitted for a given solicitation.",
    input_schema={
        "type": "object",
        "properties": {
            "solicitation_id": {
                "type": "integer",
                "description": "The Labs Record ID of the parent solicitation.",
            },
        },
        "required": ["solicitation_id"],
        "additionalProperties": False,
    },
)
def list_responses(user, solicitation_id: int) -> dict:
    """List responses for a solicitation (child records linked by labs_record_id)."""
    token = require_connect_token(user)
    client = LabsRecordAPIClient(access_token=token)
    try:
        records = client.get_records(
            type="solicitation_response",
            labs_record_id=solicitation_id,
        )
        return {"responses": [_serialize_record(r) for r in records]}
    finally:
        client.close()


@register(
    name="get_response",
    description="Get a single solicitation response by its Labs Record ID.",
    input_schema={
        "type": "object",
        "properties": {
            "response_id": {
                "type": "integer",
                "description": "The Labs Record ID of the response.",
            },
        },
        "required": ["response_id"],
        "additionalProperties": False,
    },
)
def get_response(user, response_id: int) -> dict:
    """Get a single response by ID. Returns the record or raises NOT_FOUND."""
    token = require_connect_token(user)
    client = LabsRecordAPIClient(access_token=token)
    try:
        record = client.get_record_by_id(response_id, type="solicitation_response")
        if record is None:
            raise MCPToolError("NOT_FOUND", f"Response {response_id} not found")
        return _serialize_record(record)
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Write tools (is_write=True)
# ---------------------------------------------------------------------------


@register(
    name="create_solicitation",
    description=(
        "Create a new solicitation via the Labs Record API. "
        "Requires either program_id or organization_id for scoping."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "program_id": {
                "type": "string",
                "description": "Program ID to scope the record (used as experiment and program_id).",
            },
            "organization_id": {
                "type": "string",
                "description": "Organization ID to scope the record when program_id is absent.",
            },
            "data": {
                "type": "object",
                "description": (
                    "Application-level solicitation fields (title, status, solicitation_type, etc.). "
                    "Set `is_public=true` to surface the record on the public /solicitations/ "
                    "marketplace listing — this also flips the server-side `public` ACL flag, which "
                    "is the field the marketplace query actually filters on. Solicitations are an "
                    "exception to the broader MCP no-public-records policy because their data is "
                    "public-facing by design (title, scope, questions). Do NOT include PII or "
                    "pipeline-derived data in a public solicitation."
                ),
                "additionalProperties": True,
            },
        },
        "required": ["data"],
        "additionalProperties": False,
    },
    is_write=True,
)
def create_solicitation(
    user,
    data: dict,
    program_id: str | None = None,
    organization_id: str | None = None,
) -> dict:
    """Create a new solicitation. Requires data and at least one scope param."""
    if not data:
        raise MCPToolError("INVALID_SCHEMA", "data is required")
    experiment = program_id or organization_id
    if not experiment:
        raise MCPToolError("INVALID_SCHEMA", "Either program_id or organization_id is required")

    token = require_connect_token(user)
    client = LabsRecordAPIClient(access_token=token)
    try:
        # is_public is the user-facing API name; persistence is record.public on
        # the LabsRecord envelope. Strip from data so the JSON column never
        # duplicates the flag.
        is_public = bool(data.get("is_public", False))
        data = {k: v for k, v in data.items() if k != "is_public"}
        prog_id = int(program_id) if program_id else None
        record = client.create_record(
            experiment=experiment,
            type="solicitation",
            data=data,
            program_id=prog_id,
            public=is_public,
        )
        return _serialize_record(record)
    finally:
        client.close()


@register(
    name="update_solicitation",
    description=(
        "Update an existing solicitation. Merges update_data into the existing data dict; "
        "keys present in update_data overwrite existing values, all other keys are preserved. "
        "Pass program_id (or organization_id) for non-public records — the merge starts with "
        "a get_record_by_id that needs scope to authorize the read. Setting `is_public` in "
        "update_data also flips the server-side `public` ACL flag (the one the marketplace "
        "filters on); omit `is_public` to leave the existing visibility unchanged."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "solicitation_id": {
                "type": "integer",
                "description": "The Labs Record ID of the solicitation to update.",
            },
            "update_data": {
                "type": "object",
                "description": "Fields to update. Merged (shallow) into the existing data dict.",
                "additionalProperties": True,
            },
            "program_id": {
                "type": "string",
                "description": (
                    "Program ID that owns the solicitation. Required for non-public "
                    "records so prod authorizes the underlying read."
                ),
            },
            "organization_id": {
                "type": "string",
                "description": (
                    "Organization ID alternative to program_id when the solicitation is "
                    "org-scoped rather than program-scoped."
                ),
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
    """Update an existing solicitation by merging update_data into its data dict.

    Visibility (``is_public``) lives on the LabsRecord envelope as a separate ACL
    column, not inside the JSON ``data``. When ``update_data`` includes
    ``is_public``, we capture it before stripping the key from the merge and
    forward it as ``public=`` to the server, so the public marketplace listing
    actually sees the change. The redundant ``public`` key is also stripped to
    prevent the envelope name from being shadowed inside ``data``.
    """
    token = require_connect_token(user)
    client = LabsRecordAPIClient(
        access_token=token,
        program_id=_coerce_id(program_id),
        organization_id=_coerce_id(organization_id),
    )
    try:
        # Fetch current record to read current data and metadata
        current = client.get_record_by_id(solicitation_id, type="solicitation")
        if current is None:
            raise MCPToolError("NOT_FOUND", f"Solicitation {solicitation_id} not found")

        # Capture envelope-level is_public BEFORE stripping it from the merge —
        # otherwise the propagation check below always sees a missing key.
        public_override: bool | None = None
        if "is_public" in update_data:
            public_override = bool(update_data["is_public"])

        # Merge: existing data wins on unspecified keys; update_data wins on overlapping keys.
        # Strip visibility keys from BOTH sides — flag lives on the envelope, not in JSON.
        # Stripping current.data lets legacy records migrate naturally on first update.
        merged_data = {k: v for k, v in (current.data or {}).items() if k not in ("is_public", "public")}
        update_data.pop("is_public", None)
        update_data.pop("public", None)
        merged_data.update(update_data)

        update_kwargs: dict = {
            "record_id": solicitation_id,
            "experiment": current.experiment,
            "type": current.type,
            "data": merged_data,
            "current_record": current,
        }
        if public_override is not None:
            update_kwargs["public"] = public_override

        record = client.update_record(**update_kwargs)
        return _serialize_record(record)
    finally:
        client.close()


@register(
    name="delete_solicitation",
    description=(
        "Delete a solicitation and cascade-delete its associated responses and "
        "reviews. Refuses to delete solicitations with non-zero responses or "
        "reviews unless force=true. Cascade count is reported in the response. "
        "Status field is not consulted — the gate is the actual cascade, not "
        "a status proxy. IRREVERSIBLE — used by ACE sweep to clean up orphan "
        "dogfood solicitations."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "solicitation_id": {
                "type": "integer",
                "description": "The Labs Record ID of the solicitation to delete.",
            },
            "program_id": {
                "type": "string",
                "description": (
                    "Program ID that owns the solicitation. Required for non-public "
                    "records so prod authorizes the underlying read."
                ),
            },
            "organization_id": {
                "type": "string",
                "description": (
                    "Organization ID alternative to program_id when the solicitation is "
                    "org-scoped rather than program-scoped."
                ),
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
    """Delete a solicitation plus its child responses and reviews.

    Cascade guard: refuses when responses or reviews exist unless ``force=True``.
    The status field is intentionally not consulted — a freshly-published
    solicitation lands in 'active' with an empty cascade, and an admin can
    flip a populated solicitation to 'closed', so status is not a reliable
    proxy for whether destroying the record would lose engagement data.
    """
    token = require_connect_token(user)
    client = LabsRecordAPIClient(
        access_token=token,
        program_id=_coerce_id(program_id),
        organization_id=_coerce_id(organization_id),
    )
    try:
        solicitation = client.get_record_by_id(solicitation_id, type="solicitation")
        if solicitation is None:
            raise MCPToolError("NOT_FOUND", f"Solicitation {solicitation_id} not found")

        # Collect cascade first so the gate can compare against actual engagement
        # data rather than the status proxy.
        responses = client.get_records(
            type="solicitation_response",
            labs_record_id=solicitation_id,
        )
        response_ids = [r.id for r in responses]

        review_ids: list[int] = []
        for response_id in response_ids:
            reviews = client.get_records(
                type="solicitation_review",
                labs_record_id=response_id,
            )
            review_ids.extend(r.id for r in reviews)

        if (response_ids or review_ids) and not force:
            raise MCPToolError(
                "FAILED_PRECONDITION",
                (
                    f"Cannot delete solicitation {solicitation_id}: "
                    f"{len(response_ids)} responses + {len(review_ids)} reviews "
                    f"would be destroyed. Pass force=true to override."
                ),
            )

        # Bottom-up delete so a partial failure never leaves dangling parents.
        if review_ids:
            client.delete_records(review_ids)
        if response_ids:
            client.delete_records(response_ids)
        client.delete_record(solicitation_id)

        return {
            "solicitation_id": solicitation_id,
            "deleted": {
                "solicitations": 1,
                "responses": len(response_ids),
                "reviews": len(review_ids),
            },
        }
    finally:
        client.close()


@register(
    name="award_response",
    description=(
        "Award a solicitation response: marks it as awarded with reward_budget and org_id. "
        "If fund_id is provided, appends an allocation entry to that fund's allocations array."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "response_id": {
                "type": "integer",
                "description": "The Labs Record ID of the response to award.",
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
) -> dict:
    """Award a response: mark as awarded and optionally allocate from a fund.

    Flow:
    1. Fetch the current response record.
    2. Update response: set status=awarded, reward_budget, org_id.
    3. If fund_id: fetch fund record, append allocation, update fund.
    4. Return serialized updated response.
    """
    token = require_connect_token(user)
    client = LabsRecordAPIClient(access_token=token)
    try:
        # 1. Fetch current response
        current_response = client.get_record_by_id(response_id, type="solicitation_response")
        if current_response is None:
            raise MCPToolError("NOT_FOUND", f"Response {response_id} not found")

        # 2. Update response status
        updated_data = dict(current_response.data or {})
        updated_data["status"] = "awarded"
        updated_data["reward_budget"] = reward_budget
        updated_data["org_id"] = org_id

        updated_response_record = client.update_record(
            record_id=response_id,
            experiment=current_response.experiment,
            type=current_response.type,
            data=updated_data,
            current_record=current_response,
            public=True,
        )
        updated_response = _serialize_record(updated_response_record)

        # 3. Auto-allocate from fund if fund_id provided
        if fund_id:
            solicitation_id = updated_data.get("solicitation_id")
            solicitation_title = ""
            org_name = updated_data.get("llo_entity_name", "")

            # Try to get solicitation title for the allocation notes
            if solicitation_id:
                try:
                    sol_record = client.get_record_by_id(int(solicitation_id), type="solicitation")
                    if sol_record:
                        solicitation_title = (sol_record.data or {}).get("title", "")
                except Exception:
                    logger.warning(
                        "Could not fetch solicitation %s to get title for fund allocation",
                        solicitation_id,
                    )

            allocation = {
                "amount": reward_budget,
                "type": "award",
                "solicitation_id": solicitation_id,
                "response_id": response_id,
                "org_id": org_id,
                "org_name": org_name,
                "notes": f"Award from {solicitation_title}" if solicitation_title else "Award",
            }

            _add_allocation_to_fund(client, fund_id, allocation)

        warning_msg = (
            "award_response sets the response record to public=True so the "
            "awarded organisation can read their own award status. Do not embed "
            "PII from pipeline previews or form data in the response record fields."
        )
        logger.warning("award_response: %s", warning_msg)
        updated_response["_warning"] = warning_msg
        return updated_response
    finally:
        client.close()
