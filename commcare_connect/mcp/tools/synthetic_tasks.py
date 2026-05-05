"""MCP tool to create a synthetic labs Task with embedded OCS conversation."""

from __future__ import annotations

from typing import Any

from commcare_connect.labs.integrations.connect.api_client import LabsRecordAPIClient
from commcare_connect.mcp.connect_token import require_connect_token

from ..tool_registry import register


def _labs_api_for_user(user, opportunity_id: int) -> LabsRecordAPIClient:
    """Build a LabsRecordAPIClient scoped to the given opportunity.

    The ``opportunity_id`` is passed to the client constructor so every
    write through ``create_record`` includes it in the upstream payload —
    that's what triggers production's per-record permission check (the user
    must have membership in the opp's owning organization).

    Raises whatever ``require_connect_token`` raises if the user has no
    token; the MCP framework converts that to a structured error.
    """
    token = require_connect_token(user)
    return LabsRecordAPIClient(access_token=token, opportunity_id=opportunity_id)


@register(
    name="task_create_synthetic",
    description=(
        "Create a labs Task LabsRecord with an embedded synthetic OCS "
        "coaching conversation. Used by ACE Phase 6 synthetic-workflow-seed "
        "to spawn coaching tasks attached to underperforming FLWs."
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
