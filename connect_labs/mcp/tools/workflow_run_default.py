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

from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..tool_registry import MCPToolError, register


def _parse_window_bound(value: str, *, field_name: str, end_of_day: bool = False) -> datetime:
    """Parse an ISO date/datetime string into a UTC-aware datetime.

    Every template's run_default (e.g. flw_daily_summary_report.py,
    flw_weekly_audit_report.py) does `window_start <= dt < window_end` against
    parsed datetimes, so a raw string here blows up with a TypeError deep in
    template code instead of failing cleanly at the tool boundary.

    A bare calendar date (no time component) is midnight UTC on that date.
    When end_of_day=True (window_end) a bare date is treated as *inclusive* of
    that whole day -- the natural reading of window_start="2026-08-27",
    window_end="2026-08-27" as "just August 27th" -- so it's bumped to
    midnight the following day to serve as the exclusive upper bound every
    template's `dt < window_end` comparison expects. A value that already
    carries a time component is used exactly as given (already a precise
    boundary, not a calendar date to be widened).
    """
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        pass
    else:
        bound = datetime(parsed.year, parsed.month, parsed.day, tzinfo=timezone.utc)
        return bound + timedelta(days=1) if end_of_day else bound
    try:
        parsed_dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise MCPToolError("INVALID_SCHEMA", f"{field_name} is not a valid ISO date: {value!r}") from e
    if parsed_dt.tzinfo is None:
        parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
    return parsed_dt


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
                "description": (
                    "ISO date, inclusive (e.g. window_start=window_end='2026-08-27' covers all of "
                    "that single day). Provide with window_start."
                ),
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
            parsed_start = _parse_window_bound(window_start, field_name="window_start")
            parsed_end = _parse_window_bound(window_end, field_name="window_end", end_of_day=True)
            run_kwargs["window"] = (parsed_start, parsed_end)
        elif cadence:
            run_kwargs["cadence"] = cadence

        # CommCare HQ token is best-effort and OPTIONAL, mirroring
        # run_scheduled_workflow's exact pattern in tasks.py: most templates
        # never touch a cchq_cases pipeline and simply ignore it. A missing/
        # expired CCHQ token must never block a manual run_default call that
        # doesn't need one -- a template that DOES need it degrades its own
        # affected fields gracefully (see e.g. flw_daily_summary_report.py /
        # flw_daily_indicator_report.py's work-area enrichment).
        from connect_labs.labs.integrations.commcare.cchq_tokens import CCHQTokenError, get_valid_cchq_access_token

        try:
            cchq_token = get_valid_cchq_access_token(user)
        except CCHQTokenError:
            cchq_token = None

        try:
            result = run_default_for_definition(
                definition, access_token=wda.access_token, request=None, cchq_access_token=cchq_token, **run_kwargs
            )
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
