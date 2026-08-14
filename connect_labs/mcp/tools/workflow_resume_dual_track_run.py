"""MCP tool to manually resume a weekly_dual_track_audit run that died mid-batch.

Re-fires the SAME job against the SAME run_id (see
``connect_labs.workflow.audit_generation.resume_batch_run``), relying on the
``weekly_dual_track_audit_create`` handler's own idempotency (it skips any
(opportunity, track) call that already produced a session for this run_id) to
make repeat invocations safe -- this is what lets a run killed partway
through (an ECS deploy cutover, a crash) be completed without duplicating
already-created audit sessions.

This is the manual entry point for the resume/checkpoint design in
``docs/superpowers/specs/2026-08-14-dual-track-audit-resume-design.md``. The
periodic stale-run sweep described there calls the same
``resume_batch_run`` helper this tool wraps.
"""

from __future__ import annotations

from typing import Any

from ..tool_registry import MCPToolError, register


def _wda_for_user(user, opportunity_id: int | None = None, program_id: int | None = None):
    from connect_labs.workflow.data_access import WorkflowDataAccess

    from ..connect_token import require_connect_token

    token = require_connect_token(user)
    return WorkflowDataAccess(opportunity_id=opportunity_id, program_id=program_id, access_token=token)


@register(
    name="workflow_resume_dual_track_run",
    description=(
        "Resume a weekly_dual_track_audit run that died mid-batch (e.g. killed by "
        "an ECS deploy cutover) without redoing already-completed (opportunity, "
        "track) calls. Re-derives the window from the run's persisted state and "
        "sampling/clustering overrides from the definition's current pinned "
        "config, then re-dispatches the SAME batch job against the SAME run_id "
        "async -- the job handler skips any call it already COMPLETED for this "
        "run_id, so it's safe to call even if the run actually finished, while a "
        "call whose audits exist but are still mid-review is re-entered and "
        "finished. Refused if the run's job is still alive (fresh heartbeat), "
        "since a second invocation alongside a live one duplicates work; pass "
        "force=true only when you know the worker is gone. "
        "Only works for weekly_dual_track_audit definitions; raises INVALID_SCHEMA "
        "otherwise. Returns immediately with a task_id to poll -- does not wait "
        "for the batch to finish."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "run_id": {"type": "integer", "description": "The existing workflow run to resume."},
            "definition_id": {"type": "integer"},
            "opportunity_id": {
                "type": "integer",
                "description": "Scope by owning opportunity. Provide this OR program_id.",
            },
            "program_id": {
                "type": "integer",
                "description": "Scope by owning program. Provide this OR opportunity_id.",
            },
            "force": {
                "type": "boolean",
                "description": (
                    "Resume even if the run's job still looks alive. Only for when the worker "
                    "is known to be gone but its heartbeat is younger than the staleness window."
                ),
            },
        },
        "required": ["run_id", "definition_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def workflow_resume_dual_track_run(
    user,
    *,
    run_id: int,
    definition_id: int,
    opportunity_id: int | None = None,
    program_id: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    if (opportunity_id is None) == (program_id is None):
        raise MCPToolError("INVALID_SCHEMA", "Provide exactly one of opportunity_id / program_id.")

    from connect_labs.workflow.audit_generation import resume_batch_run

    wda = _wda_for_user(user, opportunity_id=opportunity_id, program_id=program_id)
    try:
        definition = wda.get_definition(definition_id)
        if definition is None:
            raise MCPToolError("NOT_FOUND", f"workflow definition {definition_id} not found")
        if definition.template_type != "weekly_dual_track_audit":
            raise MCPToolError(
                "INVALID_SCHEMA",
                f"workflow_resume_dual_track_run only supports weekly_dual_track_audit "
                f"definitions, got {definition.template_type!r}",
            )

        run = wda.get_run(run_id)
        if run is None:
            raise MCPToolError("NOT_FOUND", f"run {run_id} not found")
        if run.definition_id != definition_id:
            raise MCPToolError(
                "INVALID_SCHEMA", f"run {run_id} belongs to definition {run.definition_id}, not {definition_id}"
            )

        try:
            return resume_batch_run(definition, run, access_token=wda.access_token, force=force)
        except ValueError as e:
            raise MCPToolError("INVALID_SCHEMA", str(e)) from e
    finally:
        wda.close()
