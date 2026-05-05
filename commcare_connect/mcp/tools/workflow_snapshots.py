"""MCP tool to save a snapshot of a saved-runs-capable workflow run.

The canonical "saved run" pattern in this codebase stores `data["snapshot"]`
on workflow **run** records (see `workflow/views.py` complete_run flow). The
snapshot is built by `build_snapshot_for_template` and persisted via
`WorkflowDataAccess.complete_run`. This tool wraps that exact path so MCP
callers (e.g. Phase 6 ACE seeds) save snapshots the same way the runner UI
does.
"""

from __future__ import annotations

from typing import Any

from ..tool_registry import MCPToolError, register


def _wda_for_user(user, opportunity_id: int | None = None):
    """Build a WorkflowDataAccess for the user, scoped if opportunity_id is given.

    Returns a WorkflowDataAccess; caller is responsible for calling .close()
    (or using a `with` block) since BaseDataAccess wraps an httpx.Client.
    """
    from commcare_connect.workflow.data_access import WorkflowDataAccess

    from ..connect_token import require_connect_token

    token = require_connect_token(user)
    return WorkflowDataAccess(opportunity_id=opportunity_id, access_token=token)


@register(
    name="workflow_save_snapshot",
    description=(
        "Save a snapshot of a workflow run by completing it. The snapshot is "
        "built via the template's build_snapshot hook (or the framework's "
        "default `snapshot_inputs` resolver) and persisted on the run record "
        "as `data.snapshot`, alongside `status=completed` and `completed_at`. "
        "Mirrors the canonical run-completion endpoint."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "run_id": {"type": "integer"},
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
) -> dict[str, Any]:
    from commcare_connect.workflow.templates import (
        TEMPLATES,
        build_snapshot_for_template,
    )

    wda = _wda_for_user(user)
    try:
        run = wda.get_run(run_id)
        if run is None:
            raise MCPToolError("NOT_FOUND", f"workflow run {run_id} not found")
        if run.is_completed:
            raise MCPToolError(
                "VERSION_CONFLICT",
                f"workflow run {run_id} is already completed; start a new run",
            )

        definition_id = run.data.get("definition_id")
        if not definition_id:
            raise MCPToolError(
                "INVALID_SCHEMA", f"run {run_id} has no definition_id"
            )

        definition = wda.get_definition(definition_id)
        if definition is None:
            raise MCPToolError(
                "NOT_FOUND", f"workflow definition {definition_id} not found"
            )

        template_key = definition.template_type
        if not template_key:
            raise MCPToolError(
                "INVALID_SCHEMA",
                "workflow definition has no template_type; cannot resolve snapshot builder",
            )

        template = TEMPLATES.get(template_key)
        if not template:
            raise MCPToolError("NOT_FOUND", f"unknown template: {template_key}")
        if not template.get("supports_saved_runs"):
            raise MCPToolError(
                "INVALID_SCHEMA",
                f"template {template_key!r} does not declare supports_saved_runs=True",
            )

        opportunity_id = run.opportunity_id or definition.opportunity_id
        if not opportunity_id:
            raise MCPToolError(
                "INVALID_SCHEMA", "run has no opportunity_id; cannot build snapshot"
            )

        # Match the views.py:complete_run code path exactly: pull pipelines and
        # workers from the same data access, then call build_snapshot_for_template.
        pipelines = wda.get_pipeline_data(definition_id, opportunity_id)
        effective_opp_ids = definition.opportunity_ids or [opportunity_id]
        workers: list[dict] = []
        for oid in effective_opp_ids:
            try:
                for w in wda.get_workers(oid):
                    workers.append({**w, "opportunity_id": oid})
            except Exception:
                # Match views.py tolerance — skip opps the user can't enumerate.
                pass

        snapshot_payload = build_snapshot_for_template(
            template_key=template_key,
            pipelines=pipelines,
            state=run.data.get("state", {}),
            opportunity_id=opportunity_id,
            workers=workers,
            opportunity_ids=effective_opp_ids,
        )
        if not isinstance(snapshot_payload, dict):
            raise MCPToolError(
                "UPSTREAM_ERROR",
                f"build_snapshot for {template_key!r} returned non-dict",
            )

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
    }
