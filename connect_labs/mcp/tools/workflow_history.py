"""MCP tools to rebuild a workflow's periodic run history.

`workflow_rebuild_history` writes the saved runs a periodic report WOULD have
if it had been running on its cadence all along -- one completed run per
period, each computed as of that period's end, all under the definitions in
force right now. That is what puts points on a trend that has only ever had
one, and what restates the whole series after an indicator definition changes.

`workflow_history_eligibility` answers whether a workflow can be rebuilt at all
without writing anything, so a caller can check before committing to minutes of
compute.

The operation itself is generic and lives in `workflow/history_rebuild.py`;
these are the thin MCP surface over it.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from ..progress import NULL_PROGRESS
from ..tool_registry import MCPToolError, register

# Periods per call. ~12s each on a ~9,000-case cohort, so a default batch finishes
# in about a minute -- long enough to make real progress, short enough that no
# client or load balancer gives up on it, and the per-period progress ping covers
# the rest.
DEFAULT_BATCH = 6


def _wda_for_user(user, opportunity_id: int | None = None, program_id: int | None = None):
    """Build a WorkflowDataAccess scoped to ``opportunity_id`` or ``program_id``.

    Same contract as ``workflow_create_run`` / ``workflow_save_snapshot``: the
    scope rides on the client and the upstream GET is an exact match, not
    hierarchical, so a program-owned workflow is invisible to an opp-scoped
    read and vice versa.
    """
    from connect_labs.workflow.data_access import WorkflowDataAccess

    from ..connect_token import require_connect_token

    token = require_connect_token(user)
    return WorkflowDataAccess(opportunity_id=opportunity_id, program_id=program_id, access_token=token)


def _parse_date(value: str | None, field: str) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError as e:
        raise MCPToolError("INVALID_SCHEMA", f"{field} must be an ISO date (YYYY-MM-DD); got {value!r}") from e


_ERROR_CLASS = {
    "no_definition": "NOT_FOUND",
    "cache_miss": "UPSTREAM_ERROR",
    "build_failed": "UPSTREAM_ERROR",
}


def _scope_label(opportunity_id: int | None, program_id: int | None) -> str:
    return f"program_id={program_id}" if program_id is not None else f"opportunity_id={opportunity_id}"


def _reraise_unreadable(exc, definition_id: int, opportunity_id: int | None, program_id: int | None):
    """Turn an upstream read failure into an error class the caller can act on.

    `get_definition` is annotated `-> Record | None`, but a 404 from the records
    API RAISES rather than returning None -- so an `is None` check never fires on
    the likeliest failure there is: a mistyped id, or the right id read under the
    wrong scope. Unhandled, the caller gets a Python traceback ending in a raw
    upstream URL rather than a sentence naming what to change.

    The scope is worth naming because it is the likelier culprit. The upstream
    read is an EXACT scope match, not a hierarchical one, so a program-owned
    workflow is invisible to an opportunity-scoped read and vice versa -- the
    definition can exist, be yours, and still 404 here.

    A non-404 (a 5xx, a timeout) is transient and stays UPSTREAM_ERROR: calling
    it NOT_FOUND would send someone to fix an id that was never wrong.
    """
    detail = str(exc)
    if "404" not in detail:
        raise MCPToolError("UPSTREAM_ERROR", detail) from exc
    raise MCPToolError(
        "NOT_FOUND",
        f"workflow definition {definition_id} could not be read under {_scope_label(opportunity_id, program_id)}. "
        "The upstream read is an exact scope match, not a hierarchical one, so a program-owned workflow is "
        "invisible to an opportunity-scoped read and vice versa -- check the scope as well as the id. "
        f"Upstream said: {detail}",
    ) from exc


def _mcp_error(e) -> MCPToolError:
    """Map a HistoryRebuildError onto the MCP error classes.

    Everything that is a caller mistake -- an ineligible workflow, an empty or
    oversized range, a start that cannot be derived -- is INVALID_SCHEMA and
    carries the reason verbatim, because in every one of those cases the
    message already says what to do instead.
    """
    return MCPToolError(_ERROR_CLASS.get(e.code, "INVALID_SCHEMA"), e.message)


@register(
    name="workflow_rebuild_history",
    description=(
        "Rebuild a periodic workflow's saved-run history: write one COMPLETED run per "
        "period between start and end, each computed AS OF that period's end, using the "
        "indicator definitions in force right now.\n\n"
        "WHAT IT IS FOR. A periodic report's trend draws one point per saved run, so a "
        "report run once has no line. This writes the runs that would exist if it had "
        "been running all along. Run it again after editing the workflow's semantic "
        "registry to restate the whole series under the new definitions -- rebuilt "
        "points are what we would say TODAY about each past date, not what was said at "
        "the time.\n\n"
        "WHICH WORKFLOWS. Only ones whose snapshot builder is a function of period_end "
        "(currently the `semantic_snapshot` builder). Anything else is refused, because "
        "rebuilding it would write identical snapshots for every period and draw a flat "
        "line across real dates. Call workflow_history_eligibility first if unsure.\n\n"
        "WHAT IT REPLACES. Only runs THIS operation previously created (stamped in run "
        "state). A run a person created by hand for the same period is left alone. Pass "
        "replace=false to skip any period that already has a run of any kind.\n\n"
        "ROLLING -- CALL IT REPEATEDLY. Each period is a full evaluation of the cohort, order "
        "10s apiece, so a year of weekly history is ~70 of them: far too long for one request. "
        "Each call therefore builds at most `limit` periods (default 6) and returns `done` and a "
        "`next_start` cursor. Until `done` is true, call again with start=<next_start> and "
        "end=<the end this report returned>, so every batch walks the same window. A batch is "
        "idempotent, so one cut off mid-flight is simply re-run.\n\n"
        "CHECK FIRST. Before writing a whole history, grade one date with "
        "workflow_preview_as_of and compare it to the reference figures; it uses the same builder "
        "and persists nothing. dry_run=true shows the full plan and period count without writing. "
        "The pipeline data must already be cached; if it is not, the call stops at the first "
        "period rather than failing every one.\n\n"
        "start defaults to the earliest dated row in the workflow's own case index, and "
        "end to the last COMPLETE period before today. Provide exactly one of "
        "opportunity_id / program_id, as for workflow_save_snapshot."
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
                "enum": ["weekly", "daily"],
                "description": "Period length. Weekly periods are Monday-Sunday. Defaults to weekly.",
            },
            "start": {
                "type": "string",
                "description": (
                    "ISO date of the first period to build. Defaults to the earliest dated row in "
                    "the workflow's case index -- i.e. as far back as the data goes."
                ),
            },
            "end": {
                "type": "string",
                "description": "ISO date. Defaults to the end of the last COMPLETE period before today.",
            },
            "replace": {
                "type": "boolean",
                "description": (
                    "Default true: replace runs this operation previously created. False: skip any "
                    "period that already has a run. Never touches a hand-made run either way."
                ),
            },
            "dry_run": {
                "type": "boolean",
                "description": "Report every period in the range and what would happen to each, writing nothing.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 26,
                "description": (
                    "Most periods to build in this call (default 6). The report's `next_start` is where "
                    "the next call begins."
                ),
            },
        },
        "required": ["definition_id"],
        "additionalProperties": False,
    },
    is_write=True,
    wants_progress=True,
)
def workflow_rebuild_history(
    user,
    *,
    definition_id: int,
    opportunity_id: int | None = None,
    program_id: int | None = None,
    cadence: str = "weekly",
    start: str | None = None,
    end: str | None = None,
    replace: bool = True,
    dry_run: bool = False,
    limit: int = DEFAULT_BATCH,
    progress=NULL_PROGRESS,
) -> dict[str, Any]:
    from connect_labs.labs.integrations.connect.api_client import LabsAPIError
    from connect_labs.workflow.history_rebuild import HistoryRebuildError, rebuild_history

    if (opportunity_id is None) == (program_id is None):
        raise MCPToolError("INVALID_SCHEMA", "Provide exactly one of opportunity_id / program_id.")

    start_date = _parse_date(start, "start")
    end_date = _parse_date(end, "end")

    wda = _wda_for_user(user, opportunity_id=opportunity_id, program_id=program_id)
    try:
        try:
            return rebuild_history(
                wda,
                definition_id,
                cadence=cadence,
                start=start_date,
                end=end_date,
                opportunity_id=opportunity_id,
                program_id=program_id,
                replace=replace,
                dry_run=dry_run,
                limit=limit,
                progress=progress,
            )
        except HistoryRebuildError as e:
            raise _mcp_error(e) from e
        except LabsAPIError as e:
            _reraise_unreadable(e, definition_id, opportunity_id, program_id)
    finally:
        wda.close()


@register(
    name="workflow_history_eligibility",
    description=(
        "Whether a workflow's periodic history can be rebuilt, and why not if it cannot. "
        "Reads only -- writes nothing and costs no evaluation. A workflow is eligible iff "
        "its snapshot builder is a function of period_end, so that a run dated in the past "
        "reports that date rather than today. Check this before workflow_rebuild_history "
        "on a workflow you have not rebuilt before."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "definition_id": {"type": "integer"},
            "opportunity_id": {"type": "integer"},
            "program_id": {"type": "integer"},
        },
        "required": ["definition_id"],
        "additionalProperties": False,
    },
    is_write=False,
)
def workflow_history_eligibility(
    user,
    *,
    definition_id: int,
    opportunity_id: int | None = None,
    program_id: int | None = None,
) -> dict[str, Any]:
    from connect_labs.labs.integrations.connect.api_client import LabsAPIError
    from connect_labs.workflow.history_rebuild import GENERATED_BY, eligibility

    if (opportunity_id is None) == (program_id is None):
        raise MCPToolError("INVALID_SCHEMA", "Provide exactly one of opportunity_id / program_id.")

    wda = _wda_for_user(user, opportunity_id=opportunity_id, program_id=program_id)
    try:
        try:
            definition = wda.get_definition(definition_id)
        except LabsAPIError as e:
            _reraise_unreadable(e, definition_id, opportunity_id, program_id)
        if definition is None:
            raise MCPToolError(
                "NOT_FOUND",
                f"workflow definition {definition_id} not found under " f"{_scope_label(opportunity_id, program_id)}",
            )

        ok, reason = eligibility(definition)

        # How much history already exists, and how much of it this operation
        # owns -- the number that says what a rebuild would replace.
        generated = 0
        manual = 0
        for run in wda.list_runs(definition_id) or []:
            if not run.is_completed:
                continue
            if (run.state or {}).get("generated_by") == GENERATED_BY:
                generated += 1
            else:
                manual += 1

        return {
            "definition_id": definition_id,
            "eligible": ok,
            "reason": reason,
            "completed_runs": generated + manual,
            "generated_runs": generated,
            "manual_runs": manual,
        }
    finally:
        wda.close()


@register(
    name="workflow_preview_as_of",
    description=(
        "Grade a periodic workflow's indicators AS OF a date, exactly as a rebuilt snapshot for "
        "that date would, and PERSIST NOTHING -- no run is created, completed or deleted.\n\n"
        "WHAT IT IS FOR. The check before workflow_rebuild_history: grade one date, compare the "
        "figures to the reference you trust, and only write history once they agree. It runs the "
        "same builder a rebuilt run uses, with an in-memory run dated `as_of`, so a match here is "
        "a match in the snapshots -- not a parallel computation that could agree while they do "
        "not.\n\n"
        "WHAT COMES BACK. The programme and per-LLO cells for the primary series (`programme`, "
        "`byLLO`) and for every further series under `series.<name>` -- e.g. `series.N` for the "
        "KMC 15-metric scorecard. Each cell is {id, value, n, band}; `n` is the denominator. Plus "
        "`meta` (cohort size as of the date) and `cache`: check cache.partial_cache before "
        "trusting totals, because a partially cached cohort understates every count while "
        "looking exactly like a genuine disagreement. The case index and per-worker cells are "
        "left out -- they are most of a snapshot's size and none of a comparison. Pass "
        "include_opportunities=true for per-opportunity cells.\n\n"
        "Eligibility is the same as for workflow_rebuild_history. Provide exactly one of "
        "opportunity_id / program_id."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "definition_id": {"type": "integer"},
            "opportunity_id": {"type": "integer"},
            "program_id": {"type": "integer"},
            "as_of": {"type": "string", "description": "ISO date (YYYY-MM-DD) to grade as of."},
            "include_opportunities": {
                "type": "boolean",
                "description": "Also return per-opportunity cells. Default false.",
            },
        },
        "required": ["definition_id", "as_of"],
        "additionalProperties": False,
    },
    is_write=False,
)
def workflow_preview_as_of(
    user,
    *,
    definition_id: int,
    as_of: str,
    opportunity_id: int | None = None,
    program_id: int | None = None,
    include_opportunities: bool = False,
) -> dict[str, Any]:
    from connect_labs.labs.integrations.connect.api_client import LabsAPIError
    from connect_labs.workflow.history_rebuild import HistoryRebuildError, preview_as_of

    if (opportunity_id is None) == (program_id is None):
        raise MCPToolError("INVALID_SCHEMA", "Provide exactly one of opportunity_id / program_id.")
    as_of_date = _parse_date(as_of, "as_of")
    if as_of_date is None:
        raise MCPToolError("INVALID_SCHEMA", "as_of is required (YYYY-MM-DD).")

    wda = _wda_for_user(user, opportunity_id=opportunity_id, program_id=program_id)
    try:
        try:
            return preview_as_of(
                wda,
                definition_id,
                as_of=as_of_date,
                opportunity_id=opportunity_id,
                program_id=program_id,
                include_opportunities=include_opportunities,
            )
        except HistoryRebuildError as e:
            raise _mcp_error(e) from e
        except LabsAPIError as e:
            _reraise_unreadable(e, definition_id, opportunity_id, program_id)
    finally:
        wda.close()
