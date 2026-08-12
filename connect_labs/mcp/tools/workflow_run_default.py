"""MCP tool to run a workflow's default (no-UI) mode headlessly.

Wraps the exact code path a `WorkflowSchedule`'s scheduled fire uses
(`run_default_for_definition`, called synchronously by
`connect_labs.workflow.tasks.run_scheduled_workflow`) so an operator can
verify a schedulable workflow manually, without waiting for its cadence to
tick — including for a PROGRAM-owned workflow, which the existing
`run_default_api` Django view can't reach at all (it only ever resolves
`labs_context.get("opportunity_id")`, so a program-owned definition 404s
there regardless of this tool). This tool builds its own scoped
`WorkflowDataAccess` from an explicit `opportunity_id` OR `program_id`,
mirroring `workflow_create_run`'s pattern instead.
"""

from __future__ import annotations

from typing import Any

from ..tool_registry import MCPToolError, register


def _wda_for_user(user, opportunity_id: int | None = None, program_id: int | None = None):
    """Build a WorkflowDataAccess for the user, scoped to opportunity_id or program_id."""
    from connect_labs.workflow.data_access import WorkflowDataAccess

    from ..connect_token import require_connect_token

    token = require_connect_token(user)
    return WorkflowDataAccess(opportunity_id=opportunity_id, program_id=program_id, access_token=token)


@register(
    name="workflow_run_default",
    description=(
        "Run a workflow's default (no-UI) mode headlessly — the exact code path a "
        "WorkflowSchedule's scheduled fire uses (run_default_for_definition). Only "
        "works for templates that support default-run (see list_templates' "
        "supports_default_run flag) — raises INVALID_SCHEMA otherwise. Pass "
        "cadence to reproduce exactly what that cadence's next tick would resolve "
        "(e.g. cadence='daily' -> yesterday's window, matching a daily 1am-UTC "
        "schedule), or window_start/window_end for an explicit range instead. "
        "Unlike the browser's per-opp 'Run Now' action, this reaches program-owned "
        "workflows too (pass program_id) — useful to verify a program-owned "
        "schedule will actually fire correctly before its next real cadence tick."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "definition_id": {"type": "integer"},
            "opportunity_id": {
                "type": "integer",
                "description": "Scope by owning opportunity. Provide this OR program_id.",
            },
            "program_id": {
                "type": "integer",
                "description": "Scope by owning program (program-owned workflow). Provide this OR opportunity_id.",
            },
            "cadence": {
                "type": "string",
                "enum": ["daily", "weekdays", "weekly", "monthly"],
                "description": (
                    "Reproduce this cadence's resolved window (e.g. 'daily' -> "
                    "yesterday). Ignored if window_start/window_end are given."
                ),
            },
            "window_start": {
                "type": "string",
                "description": "ISO date. Provide with window_end for an explicit window; overrides cadence.",
            },
            "window_end": {
                "type": "string",
                "description": "ISO date. Provide with window_start.",
            },
        },
        "required": ["definition_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def workflow_run_default(
    user,
    *,
    definition_id: int,
    opportunity_id: int | None = None,
    program_id: int | None = None,
    cadence: str | None = None,
    window_start: str | None = None,
    window_end: str | None = None,
) -> dict[str, Any]:
    if (opportunity_id is None) == (program_id is None):
        raise MCPToolError("INVALID_SCHEMA", "Provide exactly one of opportunity_id / program_id.")
    if (window_start is None) != (window_end is None):
        raise MCPToolError("INVALID_SCHEMA", "window_start and window_end must be provided together.")

    from connect_labs.workflow.templates import run_default_for_definition

    wda = _wda_for_user(user, opportunity_id=opportunity_id, program_id=program_id)
    try:
        definition = wda.get_definition(definition_id)
        if definition is None:
            raise MCPToolError("NOT_FOUND", f"workflow definition {definition_id} not found")

        run_kwargs: dict[str, Any] = {}
        if window_start and window_end:
            run_kwargs["window"] = (window_start, window_end)
        elif cadence:
            run_kwargs["cadence"] = cadence

        try:
            result = run_default_for_definition(definition, access_token=wda.access_token, request=None, **run_kwargs)
        except ValueError as e:
            # run_default_for_definition raises ValueError when the template
            # doesn't opt into default-run — same code workflow_save_snapshot
            # uses for its "template doesn't support X" case.
            raise MCPToolError("INVALID_SCHEMA", str(e)) from e
    finally:
        wda.close()

    if not isinstance(result, dict):
        raise MCPToolError("UPSTREAM_ERROR", "run_default hook returned a non-dict result")
    return result
