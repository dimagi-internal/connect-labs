"""MCP tool to save a snapshot of a saved-runs-capable workflow run.

The canonical "saved run" pattern in this codebase stores `data["snapshot"]`
on workflow **run** records (see `workflow/views.py` complete_run flow). The
snapshot contract is resolved by `resolve_snapshot_contract` (definition-owned
manifest first, template registry as fallback), built by
`build_snapshot_for_contract`, and persisted via
`WorkflowDataAccess.complete_run`. This tool wraps that exact path so MCP
callers (e.g. Phase 6 ACE seeds) save snapshots the same way the runner UI
does.
"""

from __future__ import annotations

from typing import Any

from ..tool_registry import MCPToolError, register


def _wda_for_user(user, opportunity_id: int | None = None, program_id: int | None = None):
    """Build a WorkflowDataAccess scoped to ``opportunity_id`` or ``program_id``.

    The scope rides on the client instance, and the upstream GET is an exact
    scope match rather than a hierarchical one — so a program-owned run is
    invisible to an opp-scoped DAO and vice versa. Same contract as
    ``workflow_create_run._wda_for_user``.

    Returns a WorkflowDataAccess; caller is responsible for calling .close()
    (or using a `with` block) since BaseDataAccess wraps an httpx.Client.
    """
    from connect_labs.workflow.data_access import WorkflowDataAccess

    from ..connect_token import require_connect_token

    token = require_connect_token(user)
    return WorkflowDataAccess(opportunity_id=opportunity_id, program_id=program_id, access_token=token)


@register(
    name="workflow_save_snapshot",
    description=(
        "Save a snapshot of a workflow run by completing it. The snapshot "
        "contract is resolved from the workflow definition's own "
        "`snapshot_inputs` manifest first, falling back to the template "
        "registry (Python build_snapshot hook or template manifest) for "
        "legacy instances. The snapshot is persisted on the run record "
        "as `data.snapshot`, alongside `status=completed` and `completed_at`. "
        "Mirrors the canonical run-completion endpoint.\n\n"
        "Provide exactly ONE of opportunity_id / program_id — whichever the run "
        "is filed under. The scope rides on the client and the upstream GET is "
        "an exact match, not hierarchical, so a program-owned run is invisible "
        "to an opp-scoped read and vice versa. Same contract as "
        "workflow_create_run, so a run created program-scoped can be concluded "
        "the same way.\n\n"
        "WHERE THE NUMBERS COME FROM, because it decides whether you can complete "
        "a run without opening a browser. Check `saved_runs.has_build_snapshot_hook` "
        "from workflow_get first:\n"
        "  true  — the template builds its snapshot SERVER-SIDE. Create a run, call "
        "this, done. No page visit.\n"
        "  false — the template's snapshot is whatever its RENDER staged into run "
        "state (see `saved_runs.snapshot_inputs.state_keys`). Those keys are computed "
        "in the browser, so calling this on a run nobody has opened would freeze an "
        "EMPTY snapshot onto a run that cannot be re-opened. That is refused with "
        "INVALID_SCHEMA naming the missing keys — it is not a transient error and "
        "retrying will not help. Either POST the computed keys to "
        "/labs/workflow/api/run/<run_id>/state/ yourself, or add a `build_snapshot` "
        "hook to the template so this path works unattended."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "run_id": {"type": "integer"},
            "opportunity_id": {"type": "integer"},
            "program_id": {"type": "integer"},
            "snapshot_name": {"type": "string"},
            "captured_at": {"type": "string"},
        },
        "required": ["run_id", "snapshot_name", "captured_at"],
        "additionalProperties": False,
    },
    is_write=True,
)
def workflow_save_snapshot(
    user,
    *,
    run_id: int,
    snapshot_name: str,
    captured_at: str,
    opportunity_id: int | None = None,
    program_id: int | None = None,
) -> dict[str, Any]:
    from connect_labs.workflow.snapshot_runtime import SnapshotBuildError, build_snapshot_for_run

    if (opportunity_id is None) == (program_id is None):
        raise MCPToolError("INVALID_SCHEMA", "Provide exactly one of opportunity_id / program_id.")

    wda = _wda_for_user(user, opportunity_id=opportunity_id, program_id=program_id)
    try:
        run = wda.get_run(run_id)
        if run is None:
            raise MCPToolError("NOT_FOUND", f"workflow run {run_id} not found")
        if run.is_completed:
            raise MCPToolError(
                "VERSION_CONFLICT",
                f"workflow run {run_id} is already completed; start a new run",
            )

        # ONE build step, shared with the web completion endpoint and the live
        # preview. This tool used to "mirror the canonical run-completion endpoint"
        # by hand; mirrors drift, and this one did (program_id / run_id context).
        try:
            built = build_snapshot_for_run(wda, run, requested_opportunity_id=opportunity_id, program_id=program_id)
        except SnapshotBuildError as e:
            raise _mcp_error_for(e, run_id) from e

        # Cross-check, opp-scoped calls only: the upstream GET already filtered by
        # opportunity_id, so if the run came back its opp must match — but assert it
        # explicitly to surface caller mistakes (wrong opp_id passed alongside a
        # foreign run). A program-scoped run has no singular opp to compare.
        if opportunity_id is not None:
            run_opp = run.opportunity_id or built["definition"].opportunity_id
            if run_opp and run_opp != opportunity_id:
                raise MCPToolError(
                    "INVALID_SCHEMA",
                    f"run {run_id} belongs to opportunity_id={run_opp}, "
                    f"not the {opportunity_id} passed to workflow_save_snapshot",
                )

        snapshot_payload = built["payload"]
        primary_opp_id = built["opportunity_id"]
        effective_opp_ids = built["opportunity_ids"]

        snapshot_payload["name"] = snapshot_name
        snapshot_payload["captured_at"] = captured_at

        completed = wda.complete_run(run_id, snapshot_payload, run=run)
        if completed is None:
            raise MCPToolError(
                "UPSTREAM_ERROR",
                f"failed to persist completion of run {run_id}",
            )
    finally:
        wda.close()

    return {
        "run_id": run_id,
        "snapshot_name": snapshot_name,
        "captured_at": captured_at,
        "opportunity_id": primary_opp_id,
        "opportunity_ids": effective_opp_ids,
    }


def _mcp_error_for(e, run_id: int) -> MCPToolError:
    """Map a SnapshotBuildError onto the MCP error classes."""
    if e.code in ("definition_not_found", "unknown_template"):
        return MCPToolError("NOT_FOUND", e.message)
    if e.code == "cache_miss":
        return MCPToolError("UPSTREAM_ERROR", e.message)
    if e.code == "non_dict":
        return MCPToolError("UPSTREAM_ERROR", e.message)
    if e.code == "not_staged":
        return MCPToolError(
            "INVALID_SCHEMA",
            f"{e.message} Stage those keys via POST /labs/workflow/api/run/{run_id}/state/ first, "
            "or declare a snapshot builder on the definition so this path works unattended.",
        )
    if e.code in ("no_contract", "template_not_saved_runs"):
        return MCPToolError(
            "INVALID_SCHEMA",
            f"{e.message} Set snapshot_inputs (or config.templateType) via workflow_update_definition.",
        )
    return MCPToolError("INVALID_SCHEMA", e.message)


@register(
    name="workflow_preview_snapshot",
    description=(
        "What completing a run WOULD store, built now and not persisted -- the graded "
        "payload of the run's snapshot builder over its bound registry, exactly as "
        "workflow_save_snapshot would write it. Use it to read a live run's numbers "
        "without completing it, or to check a builder spec / registry edit before "
        "saving anything. For a completed run the stored snapshot is returned as-is "
        "(source='stored').\n\n"
        "`cache` reports whether the visit cache behind the build was cold or partial: "
        "a preview over a cold cache is every metric zero, which reads as a programme "
        "with no data, and the payload cannot say so itself. Refuses with UPSTREAM_ERROR "
        "naming the pipeline when its processed cache is missing -- load the workflow's "
        "pipeline data (open the run page, or run the pipelines) and retry.\n\n"
        "Provide exactly ONE of opportunity_id / program_id, as for workflow_save_snapshot."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "run_id": {"type": "integer"},
            "opportunity_id": {"type": "integer"},
            "program_id": {"type": "integer"},
        },
        "required": ["run_id"],
        "additionalProperties": False,
    },
    is_write=False,
)
def workflow_preview_snapshot(
    user,
    *,
    run_id: int,
    opportunity_id: int | None = None,
    program_id: int | None = None,
) -> dict[str, Any]:
    from connect_labs.workflow.snapshot_runtime import SnapshotBuildError, build_snapshot_for_run, cache_state

    if (opportunity_id is None) == (program_id is None):
        raise MCPToolError("INVALID_SCHEMA", "Provide exactly one of opportunity_id / program_id.")

    wda = _wda_for_user(user, opportunity_id=opportunity_id, program_id=program_id)
    try:
        run = wda.get_run(run_id)
        if run is None:
            raise MCPToolError("NOT_FOUND", f"workflow run {run_id} not found")
        if run.is_completed and run.snapshot:
            return {"run_id": run_id, "source": "stored", "snapshot": run.snapshot, "cache": None}
        try:
            built = build_snapshot_for_run(wda, run, requested_opportunity_id=opportunity_id, program_id=program_id)
        except SnapshotBuildError as e:
            raise _mcp_error_for(e, run_id) from e
        return {
            "run_id": run_id,
            "source": "preview",
            "opportunity_id": built["opportunity_id"],
            "opportunity_ids": built["opportunity_ids"],
            "snapshot": built["payload"],
            "cache": cache_state(built["opportunity_ids"]),
        }
    finally:
        wda.close()
