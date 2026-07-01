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


def _wda_for_user(user, opportunity_id: int | None = None):
    """Build a WorkflowDataAccess for the user, scoped if opportunity_id is given.

    Returns a WorkflowDataAccess; caller is responsible for calling .close()
    (or using a `with` block) since BaseDataAccess wraps an httpx.Client.
    """
    from connect_labs.workflow.data_access import WorkflowDataAccess

    from ..connect_token import require_connect_token

    token = require_connect_token(user)
    return WorkflowDataAccess(opportunity_id=opportunity_id, access_token=token)


@register(
    name="workflow_save_snapshot",
    description=(
        "Save a snapshot of a workflow run by completing it. The snapshot "
        "contract is resolved from the workflow definition's own "
        "`snapshot_inputs` manifest first, falling back to the template "
        "registry (Python build_snapshot hook or template manifest) for "
        "legacy instances. The snapshot is persisted on the run record "
        "as `data.snapshot`, alongside `status=completed` and `completed_at`. "
        "Mirrors the canonical run-completion endpoint. opportunity_id must "
        "match the run's owning opp — it scopes the upstream GET so the run "
        "is actually visible (workflow runs are opp-scoped, not public)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "run_id": {"type": "integer"},
            "opportunity_id": {"type": "integer"},
            "snapshot_name": {"type": "string"},
            "captured_at": {"type": "string"},
        },
        "required": ["run_id", "opportunity_id", "snapshot_name", "captured_at"],
        "additionalProperties": False,
    },
    is_write=True,
)
def workflow_save_snapshot(
    user,
    *,
    run_id: int,
    opportunity_id: int,
    snapshot_name: str,
    captured_at: str,
) -> dict[str, Any]:
    from connect_labs.workflow.data_access import PipelineCacheMiss
    from connect_labs.workflow.templates import (
        SnapshotTooLargeError,
        build_snapshot_for_contract,
        resolve_snapshot_contract,
    )

    wda = _wda_for_user(user, opportunity_id=opportunity_id)
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
            raise MCPToolError("INVALID_SCHEMA", f"run {run_id} has no definition_id")

        definition = wda.get_definition(definition_id)
        if definition is None:
            raise MCPToolError("NOT_FOUND", f"workflow definition {definition_id} not found")

        contract = resolve_snapshot_contract(definition)
        if not contract["ok"]:
            if contract["error"] == "unknown_template":
                raise MCPToolError("NOT_FOUND", f"unknown template: {contract['template_key']}")
            if contract["error"] == "template_not_saved_runs":
                raise MCPToolError(
                    "INVALID_SCHEMA",
                    f"template {contract['template_key']!r} does not declare supports_saved_runs=True; "
                    "to opt this workflow in anyway, set snapshot_inputs on its definition",
                )
            raise MCPToolError(
                "INVALID_SCHEMA",
                "workflow definition has no snapshot_inputs manifest, no template_type, and its "
                "name does not match a known template; set snapshot_inputs (or config.templateType) "
                "via workflow_update_definition so a snapshot builder can be resolved",
            )

        # Cross-check: the upstream GET already filtered by opportunity_id, so
        # if the run came back its opp must match — but assert it explicitly to
        # surface caller mistakes (wrong opp_id passed alongside a foreign run).
        run_opp = run.opportunity_id or definition.opportunity_id
        if run_opp and run_opp != opportunity_id:
            raise MCPToolError(
                "INVALID_SCHEMA",
                f"run {run_id} belongs to opportunity_id={run_opp}, "
                f"not the {opportunity_id} passed to workflow_save_snapshot",
            )

        # Match the views.py:complete_run code path exactly: cache-only,
        # manifest-scoped pipeline read (snapshots freeze what was reviewed —
        # they never re-execute pipelines), then workers, then the builder.
        contract_inputs = contract.get("snapshot_inputs")
        aliases = None if contract["source"] == "template_hook" else (contract_inputs or {}).get("pipelines")
        if aliases == []:
            pipelines = {}
        else:
            try:
                pipelines = wda.get_cached_pipeline_data(
                    definition_id,
                    opportunity_id,
                    aliases=aliases,
                    # Period-scope pipelines that opt in, so each saved run
                    # freezes its own window rather than the all-time aggregate
                    # (ace#764). No-op for runs without a period / opted-out
                    # pipelines.
                    period_start=run.period_start,
                    period_end=run.period_end,
                )
            except PipelineCacheMiss as e:
                raise MCPToolError(
                    "UPSTREAM_ERROR",
                    f"no cached data for pipeline {e.pipeline_name or e.alias!r} (opp {e.opportunity_id}); "
                    "load the workflow's pipeline data first (open the run page or run the pipelines), "
                    "then retry",
                ) from e
        effective_opp_ids = definition.opportunity_ids or [opportunity_id]
        workers: list[dict] = []
        for oid in effective_opp_ids:
            try:
                for w in wda.get_workers(oid):
                    workers.append({**w, "opportunity_id": oid})
            except Exception:
                # Match views.py tolerance — skip opps the user can't enumerate.
                pass

        try:
            snapshot_payload = build_snapshot_for_contract(
                contract,
                pipelines=pipelines,
                state=run.data.get("state", {}),
                opportunity_id=opportunity_id,
                workers=workers,
                opportunity_ids=effective_opp_ids,
                # Optional context fields some templates' build_snapshot hooks accept
                # (definition_id, access_token). Templates that don't use these
                # absorb them via **_. access_token is necessary for hooks that
                # construct their own DAOs (no `request` is available in MCP path).
                definition_id=definition_id,
                access_token=wda.access_token,
            )
        except SnapshotTooLargeError as e:
            raise MCPToolError("INVALID_SCHEMA", str(e)) from e
        if not isinstance(snapshot_payload, dict):
            raise MCPToolError(
                "UPSTREAM_ERROR",
                "snapshot builder returned non-dict",
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
