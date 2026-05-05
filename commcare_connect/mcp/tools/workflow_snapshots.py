"""MCP tool to save a snapshot of a saved-runs-capable workflow."""

from __future__ import annotations

from typing import Any

from ..tool_registry import MCPToolError, register


class _WorkflowDataAccessAdapter:
    """Thin adapter over WorkflowDataAccess exposing get_workflow / update_workflow.

    WorkflowDataAccess uses get_definition / update_definition; this class
    bridges the naming so the MCP tool and its test double share the same
    interface contract.
    """

    def __init__(self, wda):
        self._wda = wda

    def get_workflow(self, workflow_id: int):
        definition = self._wda.get_definition(workflow_id)
        if definition is None:
            return None
        # Attach template_key as an alias for template_type so callers can use
        # a consistent name regardless of the underlying proxy property name.
        definition.template_key = definition.template_type
        return definition

    def update_workflow(self, workflow_id: int, *, data: dict):
        return self._wda.update_definition(definition_id=workflow_id, data=data)

    def close(self):
        if hasattr(self._wda, "close"):
            self._wda.close()


def _workflow_data_access_for_user(user) -> _WorkflowDataAccessAdapter:
    """Return an adapter with get_workflow / update_workflow for the given user."""
    from commcare_connect.workflow.data_access import WorkflowDataAccess

    from ..connect_token import require_connect_token

    token = require_connect_token(user)
    # No opportunity_id needed — get_definition / update_definition look up by
    # record id and do not require opportunity scoping.
    wda = WorkflowDataAccess(access_token=token)
    return _WorkflowDataAccessAdapter(wda)


def _build_snapshot(template_key: str, workflow) -> dict[str, Any]:
    """Call the template's build_snapshot hook if present; else best-effort fallback."""
    from commcare_connect.workflow.templates import get_template

    template = get_template(template_key)
    if template is not None and callable(template.get("build_snapshot")):
        # The real hook signature is build_snapshot(*, pipelines, state,
        # opportunity_id, **context). We don't have pipeline data here, so
        # pass empty dicts and let the hook handle missing inputs gracefully.
        return template["build_snapshot"](
            pipelines={},
            state=workflow.data.get("state") or {},
            opportunity_id=None,
        )
    # Fallback: enumerate top-level state keys as a lightweight manifest.
    return {
        "state_keys": list((workflow.data.get("state") or {}).keys()),
        "metrics": {},
    }


@register(
    name="workflow_save_snapshot",
    description=(
        "Capture a saved-run snapshot of a workflow that supports them. "
        "Calls the template's build_snapshot hook (when present) and appends "
        "the result to the workflow's saved_runs[] list."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "workflow_id": {"type": "integer"},
            "snapshot_name": {"type": "string"},
            "captured_at": {"type": "string"},
        },
        "required": ["workflow_id", "snapshot_name", "captured_at"],
        "additionalProperties": False,
    },
    is_write=True,
)
def workflow_save_snapshot(
    user,
    *,
    workflow_id: int,
    snapshot_name: str,
    captured_at: str,
) -> dict[str, Any]:
    client = _workflow_data_access_for_user(user)
    try:
        workflow = client.get_workflow(workflow_id)
        if workflow is None:
            raise MCPToolError("NOT_FOUND", f"workflow {workflow_id} not found")

        snapshot_payload = _build_snapshot(workflow.template_key, workflow)
        snapshot_payload["name"] = snapshot_name
        snapshot_payload["captured_at"] = captured_at

        data = dict(workflow.data)
        saved_runs = list(data.get("saved_runs") or [])
        saved_runs.append(snapshot_payload)
        data["saved_runs"] = saved_runs

        client.update_workflow(workflow_id, data=data)
    finally:
        if hasattr(client, "close"):
            client.close()

    return {
        "workflow_id": workflow_id,
        "snapshot_name": snapshot_name,
        "captured_at": captured_at,
        "snapshot_count": len(saved_runs),
    }
