"""MCP tool to create a new in_progress workflow run.

Wraps :class:`WorkflowDataAccess.create_run`. The companion tool
``workflow_save_snapshot`` consumes the returned ``run_id`` to close
the run with a snapshot — together they let MCP callers drive the
canonical "saved runs" lifecycle programmatically (Phase 6 ACE seeds
build week-over-week snapshots this way).

Membership enforcement is implicit: the labs API client is constructed
with ``opportunity_id=opportunity_id`` so the upstream POST carries the
scope param, and Connect's permission check rejects callers who aren't
members of the owning org. Same pattern the rest of the workflow tools
already use.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from ..tool_registry import MCPToolError, register


def _wda_for_user(user, opportunity_id: int | None = None):
    """Build a WorkflowDataAccess for the user, scoped to ``opportunity_id``."""
    from connect_labs.workflow.data_access import WorkflowDataAccess

    from ..connect_token import require_connect_token

    token = require_connect_token(user)
    return WorkflowDataAccess(opportunity_id=opportunity_id, access_token=token)


@register(
    name="workflow_create_run",
    description=(
        "Create a new in_progress workflow run on the given definition. The "
        "returned run_id can be passed to workflow_save_snapshot to close "
        "the run with a snapshot. period_start / period_end default to "
        "today if omitted; initial_state defaults to {}."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "definition_id": {"type": "integer"},
            "opportunity_id": {"type": "integer"},
            "period_start": {
                "type": "string",
                "description": "ISO date or datetime; defaults to today's date.",
            },
            "period_end": {
                "type": "string",
                "description": "ISO date or datetime; defaults to today's date.",
            },
            "initial_state": {
                "type": "object",
                "description": "Optional starting run state dict.",
            },
        },
        "required": ["definition_id", "opportunity_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def workflow_create_run(
    user,
    *,
    definition_id: int,
    opportunity_id: int,
    period_start: str | None = None,
    period_end: str | None = None,
    initial_state: dict | None = None,
) -> dict[str, Any]:
    today = dt.date.today().isoformat()
    period_start = period_start or today
    period_end = period_end or today

    wda = _wda_for_user(user, opportunity_id=opportunity_id)
    try:
        definition = wda.get_definition(definition_id)
        if definition is None:
            raise MCPToolError(
                "NOT_FOUND",
                f"workflow definition {definition_id} not found",
            )

        run = wda.create_run(
            definition_id=definition_id,
            opportunity_id=opportunity_id,
            period_start=period_start,
            period_end=period_end,
            initial_state=initial_state,
        )
        if run is None:
            raise MCPToolError(
                "UPSTREAM_ERROR",
                f"create_run returned None for definition {definition_id}",
            )
    finally:
        wda.close()

    return {
        "run_id": run.id,
        "definition_id": definition_id,
        "opportunity_id": opportunity_id,
        "period_start": period_start,
        "period_end": period_end,
    }
