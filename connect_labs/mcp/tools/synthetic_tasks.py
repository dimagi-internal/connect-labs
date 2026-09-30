"""MCP tool to create a synthetic labs Task with embedded OCS conversation."""

from __future__ import annotations

from typing import Any

from connect_labs.labs.integrations.connect.api_client import LabsRecordAPIClient
from connect_labs.labs.synthetic.access import user_can_access_labs_only_opp
from connect_labs.labs.synthetic.local_records_backend import is_labs_only_opportunity_id
from connect_labs.mcp.connect_token import require_connect_token

from ..tool_registry import MCPToolError, register


def _require_labs_only_opp(user, opportunity_id: int) -> None:
    """A synthetic task belongs on a labs-only opp the caller can see, never on a real one.

    Labs-only records live in the labs DB (the local backend) with no production
    permission check behind them, so the synthetic ACCESS model is the only gate.
    A real opp is refused outright: invented coaching conversations do not belong in
    production Connect's records, whoever is asking.
    """
    if not is_labs_only_opportunity_id(opportunity_id):
        raise MCPToolError(
            "INVALID_ARGUMENT",
            f"opportunity_id {opportunity_id} is not a labs-only opportunity; synthetic tasks are only "
            "written to labs-only (synthetic) opportunities.",
        )
    if not user_can_access_labs_only_opp(user, opportunity_id):
        raise MCPToolError(
            "PERMISSION_DENIED",
            f"labs-only opportunity {opportunity_id} is not accessible to your account",
        )


def _labs_api_for_user(user, opportunity_id: int) -> LabsRecordAPIClient:
    """Build a LabsRecordAPIClient scoped to the given labs-only opportunity.

    A labs-only opp routes to the local backend, which never uses the token, so a
    caller with no Connect token is not refused for lacking one.
    """
    try:
        token = require_connect_token(user)
    except MCPToolError:
        token = ""
    return LabsRecordAPIClient(access_token=token, opportunity_id=opportunity_id)


@register(
    name="task_create_synthetic",
    description=(
        "Create a labs Task LabsRecord with an embedded synthetic OCS "
        "coaching conversation. Used by ACE Phase 6 synthetic-workflow-seed "
        "to spawn coaching tasks attached to underperforming FLWs. Labs-only "
        "(synthetic) opportunities the caller can access only."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "opportunity_id": {"type": "integer"},
            "assigned_to": {"type": "string"},
            "subject": {"type": "string"},
            "ocs_conversation": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {"enum": ["bot", "flw"]},
                        "text": {"type": "string"},
                        "ts": {"type": "string"},
                    },
                    "required": ["role", "text", "ts"],
                },
            },
            "status": {"type": "string", "default": "completed"},
        },
        "required": ["opportunity_id", "assigned_to", "subject", "ocs_conversation"],
        "additionalProperties": False,
    },
    is_write=True,
)
def task_create_synthetic(
    user,
    *,
    opportunity_id: int,
    assigned_to: str,
    subject: str,
    ocs_conversation: list[dict[str, Any]],
    status: str = "completed",
) -> dict[str, Any]:
    _require_labs_only_opp(user, opportunity_id)
    client = _labs_api_for_user(user, opportunity_id)
    try:
        # opportunity_id is in the constructor; the client adds it to the
        # POST payload itself, so we don't duplicate it in `data`.
        record = client.create_record(
            experiment="task",
            type="synthetic_task",
            data={
                "title": subject,
                "assigned_to": assigned_to,
                "ocs_conversation": ocs_conversation,
                "status": status,
                "synthetic": True,
            },
        )
    finally:
        client.close()
    return {
        "id": record.id,
        "assigned_to": record.data.get("assigned_to"),
        "title": record.data.get("title"),
    }
