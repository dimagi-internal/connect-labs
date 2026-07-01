"""Fund tools — migrated from _pending_migration/fund_tools.py.

Funds are LabsRecord entries with type="fund", scoped by program_id for ACL.
The experiment field stores the funder slug (derived from fund name).
"""

from __future__ import annotations

import logging

from connect_labs.labs.integrations.connect.api_client import LabsRecordAPIClient

from ..connect_token import require_connect_token
from ..tool_registry import MCPToolError, register  # noqa: F401

logger = logging.getLogger(__name__)

FUND_TYPE = "fund"


def _serialize_record(record) -> dict:
    """Flatten a LocalLabsRecord into a plain dict matching the original shape.

    Merges the record's ``data`` dict into the outer envelope, giving callers
    a single flat dict with id/experiment/type/organization_id plus all
    application-level fields at the top level.
    """
    data = record.data or {}
    return {
        "id": record.id,
        "experiment": record.experiment,
        "type": record.type,
        "organization_id": record.organization_id,
        **data,
    }


def _add_allocation_to_fund(client: LabsRecordAPIClient, fund_id: int, allocation: dict):
    """Internal helper: append an allocation to a fund's allocations list.

    Used by add_fund_allocation directly AND by solicitations.award_response
    (which previously had this logic inlined).

    Takes a pre-constructed LabsRecordAPIClient so callers that already have
    a client don't instantiate two.

    Returns the updated LocalLabsRecord.
    """
    fund_record = client.get_record_by_id(fund_id, type=FUND_TYPE)
    if fund_record is None:
        raise MCPToolError("NOT_FOUND", f"No fund with id {fund_id}")

    fund_data = dict(fund_record.data or {})
    allocations = list(fund_data.get("allocations", []))
    allocations.append(allocation)
    fund_data["allocations"] = allocations

    updated = client.update_record(
        record_id=fund_id,
        experiment=fund_record.experiment,
        type=fund_record.type,
        data=fund_data,
        current_record=fund_record,
    )
    return updated


# ---------------------------------------------------------------------------
# Read tools (is_write=False)
# ---------------------------------------------------------------------------


@register(
    name="list_funds",
    description=("List funds scoped by program_id. " "Funds are stored as LabsRecord entries with type='fund'."),
    input_schema={
        "type": "object",
        "properties": {
            "program_id": {
                "type": "string",
                "description": "Program ID to scope the fund listing (used for ACL).",
            },
        },
        "required": ["program_id"],
        "additionalProperties": False,
    },
)
def list_funds(user, program_id: str) -> dict:
    """List funds scoped by program_id."""
    token = require_connect_token(user)
    client = LabsRecordAPIClient(access_token=token)
    try:
        records = client.get_records(
            type=FUND_TYPE,
            program_id=int(program_id),
        )
        return {"funds": [_serialize_record(r) for r in records]}
    finally:
        client.close()


@register(
    name="get_fund",
    description="Get a single fund by its Labs Record ID.",
    input_schema={
        "type": "object",
        "properties": {
            "fund_id": {
                "type": "integer",
                "description": "The Labs Record ID of the fund.",
            },
        },
        "required": ["fund_id"],
        "additionalProperties": False,
    },
)
def get_fund(user, fund_id: int) -> dict:
    """Get a single fund by ID. Returns the record or raises NOT_FOUND."""
    token = require_connect_token(user)
    client = LabsRecordAPIClient(access_token=token)
    try:
        record = client.get_record_by_id(fund_id, type=FUND_TYPE)
        if record is None:
            raise MCPToolError("NOT_FOUND", f"Fund {fund_id} not found")
        return _serialize_record(record)
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Write tools (is_write=True)
# ---------------------------------------------------------------------------


_FUND_PUBLIC_WARNING = (
    "This fund record is PUBLIC — any authenticated Connect user can read it. "
    "Do not embed PII in the 'description' field. "
    "Safe fields: name, currency, total_budget, status, program_ids, delivery_types."
)


@register(
    name="create_fund",
    description=(
        "Create a new fund. Scoped by program_id for ACL. "
        "The experiment field stores the funder slug (derived from name). "
        "IMPORTANT: Fund records are PUBLIC (readable by any authenticated Connect user). "
        "You MUST pass public_record_acknowledged=true and confirm the 'description' field "
        "contains no PII before calling this tool."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "public_record_acknowledged": {
                "type": "boolean",
                "description": (
                    "Must be true. Confirms you understand this record will be publicly "
                    "readable and that the 'description' field contains no PII "
                    "(names, dates of birth, phone numbers, addresses, health data)."
                ),
            },
            "program_id": {
                "type": "string",
                "description": "Program ID to scope the record (used for ACL).",
            },
            "name": {
                "type": "string",
                "description": "Fund name. Also used to derive the funder slug.",
            },
            "total_budget": {
                "type": "number",
                "description": "Total fund budget (optional).",
            },
            "currency": {
                "type": "string",
                "description": "Currency code (default: USD).",
            },
            "description": {
                "type": "string",
                "description": (
                    "Optional description of the fund. " "Do NOT include PII — this field is publicly readable."
                ),
            },
            "program_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of program IDs the fund applies to.",
            },
            "delivery_types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of delivery types the fund covers.",
            },
            "status": {
                "type": "string",
                "description": "Fund status (default: active).",
            },
        },
        "required": ["public_record_acknowledged", "program_id", "name"],
        "additionalProperties": False,
    },
    is_write=True,
)
def create_fund(
    user,
    public_record_acknowledged: bool,
    program_id: str,
    name: str,
    total_budget: float | None = None,
    currency: str = "USD",
    description: str = "",
    program_ids: list | None = None,
    delivery_types: list | None = None,
    status: str = "active",
) -> dict:
    """Create a new fund scoped by program_id."""
    if not public_record_acknowledged:
        raise MCPToolError(
            "POLICY_VIOLATION",
            "public_record_acknowledged must be true. Review the field descriptions, "
            "confirm 'description' contains no PII, then retry with public_record_acknowledged=true.",
        )
    logger.warning("create_fund: creating public record. %s", _FUND_PUBLIC_WARNING)

    funder_slug = name.lower().replace(" ", "-")
    data: dict = {
        "name": name,
        "funder_slug": funder_slug,
        "status": status,
        "currency": currency,
        "allocations": [],
    }
    if total_budget is not None:
        data["total_budget"] = total_budget
    if description:
        data["description"] = description
    if program_ids:
        data["program_ids"] = program_ids
    if delivery_types:
        data["delivery_types"] = delivery_types

    token = require_connect_token(user)
    client = LabsRecordAPIClient(access_token=token)
    try:
        record = client.create_record(
            experiment=funder_slug,
            type=FUND_TYPE,
            data=data,
            program_id=int(program_id),
            public=True,
        )
        result = _serialize_record(record)
        result["_public_warning"] = _FUND_PUBLIC_WARNING
        return result
    finally:
        client.close()


@register(
    name="update_fund",
    description=(
        "Update an existing fund. Merges update_data into the existing data dict; "
        "keys present in update_data overwrite existing values, all other keys are preserved."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "fund_id": {
                "type": "integer",
                "description": "The Labs Record ID of the fund to update.",
            },
            "update_data": {
                "type": "object",
                "description": "Fields to update. Merged (shallow) into the existing data dict.",
                "additionalProperties": True,
            },
        },
        "required": ["fund_id", "update_data"],
        "additionalProperties": False,
    },
    is_write=True,
)
def update_fund(user, fund_id: int, update_data: dict) -> dict:
    """Update an existing fund by merging update_data into its data dict."""
    token = require_connect_token(user)
    client = LabsRecordAPIClient(access_token=token)
    try:
        current = client.get_record_by_id(fund_id, type=FUND_TYPE)
        if current is None:
            raise MCPToolError("NOT_FOUND", f"Fund {fund_id} not found")

        merged_data = dict(current.data or {})
        update_data.pop("is_public", None)
        update_data.pop("public", None)
        merged_data.update(update_data)

        record = client.update_record(
            record_id=fund_id,
            experiment=current.experiment,
            type=current.type,
            data=merged_data,
            current_record=current,
        )
        return _serialize_record(record)
    finally:
        client.close()


@register(
    name="add_fund_allocation",
    description=(
        "Append an allocation entry to a fund's allocations array. "
        "The allocation dict can contain any fields (amount, type, notes, etc.)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "fund_id": {
                "type": "integer",
                "description": "The Labs Record ID of the fund to add an allocation to.",
            },
            "allocation": {
                "type": "object",
                "description": "Allocation entry to append (e.g. {amount, type, notes, org_id}).",
                "additionalProperties": True,
            },
        },
        "required": ["fund_id", "allocation"],
        "additionalProperties": False,
    },
    is_write=True,
)
def add_fund_allocation(user, fund_id: int, allocation: dict) -> dict:
    """Append an allocation entry to a fund's allocations array."""
    token = require_connect_token(user)
    client = LabsRecordAPIClient(access_token=token)
    try:
        updated = _add_allocation_to_fund(client, fund_id, allocation)
        return _serialize_record(updated)
    finally:
        client.close()


@register(
    name="remove_fund_allocation",
    description=(
        "Remove an allocation entry from a fund's allocations array by index. "
        "Note: index-based removal is fragile under concurrent access."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "fund_id": {
                "type": "integer",
                "description": "The Labs Record ID of the fund.",
            },
            "index": {
                "type": "integer",
                "description": "Zero-based index of the allocation to remove.",
            },
        },
        "required": ["fund_id", "index"],
        "additionalProperties": False,
    },
    is_write=True,
)
def remove_fund_allocation(user, fund_id: int, index: int) -> dict:
    """Remove an allocation entry by index from a fund's allocations array."""
    token = require_connect_token(user)
    client = LabsRecordAPIClient(access_token=token)
    try:
        fund_record = client.get_record_by_id(fund_id, type=FUND_TYPE)
        if fund_record is None:
            raise MCPToolError("NOT_FOUND", f"Fund {fund_id} not found")

        fund_data = dict(fund_record.data or {})
        allocations = list(fund_data.get("allocations", []))
        if not (0 <= index < len(allocations)):
            raise MCPToolError(
                "INVALID_SCHEMA",
                f"Allocation index {index} out of range (0-{len(allocations) - 1})",
            )
        allocations.pop(index)
        fund_data["allocations"] = allocations

        updated = client.update_record(
            record_id=fund_id,
            experiment=fund_record.experiment,
            type=fund_record.type,
            data=fund_data,
            current_record=fund_record,
        )
        return _serialize_record(updated)
    finally:
        client.close()
