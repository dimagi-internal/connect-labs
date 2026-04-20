"""Workflow tools — live-instance iteration from Claude Code."""

import re

from commcare_connect.workflow.data_access import PipelineDataAccess, WorkflowDataAccess

from ..connect_token import require_connect_token
from ..tool_registry import MCPToolError, register


def _data_access(user, opportunity_id=None, program_id=None, organization_id=None) -> WorkflowDataAccess:
    """Build a WorkflowDataAccess for the user, carrying their Connect token.

    BaseDataAccess accepts access_token as a direct kwarg. The scope IDs are
    also passed at construction time so labs_api is initialised with the correct
    opportunity_id / program_id / organization_id for scoped API calls.
    """
    token = require_connect_token(user)
    return WorkflowDataAccess(
        access_token=token,
        opportunity_id=opportunity_id,
        program_id=program_id,
        organization_id=organization_id,
    )


@register(
    name="workflow_list",
    description=(
        "List workflows visible to the calling user. "
        "Scope by exactly one of: opportunity_id, program_id, organization_id. "
        "Returns minimal metadata; use workflow_get to fetch the full workflow."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "opportunity_id": {"type": "integer"},
            "program_id": {"type": "integer"},
            "organization_id": {"type": "integer"},
        },
        "additionalProperties": False,
    },
)
def workflow_list(user, opportunity_id=None, program_id=None, organization_id=None):
    scope_count = sum(1 for x in (opportunity_id, program_id, organization_id) if x is not None)
    if scope_count != 1:
        raise MCPToolError(
            "INVALID_SCHEMA",
            "workflow_list requires exactly one of opportunity_id / program_id / organization_id.",
        )

    da = _data_access(
        user,
        opportunity_id=opportunity_id,
        program_id=program_id,
        organization_id=organization_id,
    )
    try:
        # list_definitions() uses the scope set at construction time (via labs_api).
        # Scope params are NOT accepted by list_definitions() itself.
        definitions = da.list_definitions()
    finally:
        da.close()

    return {
        "workflows": [
            {
                "id": d.id,
                "name": d.name,
                "description": d.description,
                "template_type": d.template_type,
                # updated_at is not on LocalLabsRecord; omit rather than error
                "updated_at": None,
                "pipeline_source_count": len(d.pipeline_sources),
            }
            for d in definitions
        ]
    }


@register(
    name="workflow_get",
    description=(
        "Fetch everything needed to iterate on a workflow in one call: "
        "definition (name, description, statuses, config), latest render_code "
        "with its version number, and linked pipeline metadata."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "workflow_id": {"type": "integer"},
            "opportunity_id": {"type": "integer"},
        },
        "required": ["workflow_id", "opportunity_id"],
        "additionalProperties": False,
    },
)
def workflow_get(user, workflow_id: int, opportunity_id: int):
    """Fetch one workflow with all the context needed to edit it."""
    token = require_connect_token(user)

    wda = WorkflowDataAccess(access_token=token, opportunity_id=opportunity_id)
    try:
        definition = wda.get_definition(workflow_id)
        if definition is None:
            raise MCPToolError("NOT_FOUND", f"No workflow with id {workflow_id}")

        # get_render_code is the actual method name (not get_latest_render_code)
        render_code = wda.get_render_code(workflow_id)
        pipeline_sources = definition.data.get("pipeline_sources", [])

        # Fetch each linked pipeline's summary.
        pda = PipelineDataAccess(access_token=token, opportunity_id=opportunity_id)
        try:
            enriched_sources = []
            for src in pipeline_sources:
                pid = src.get("pipeline_id")
                pdef = pda.get_definition(pid) if pid else None
                enriched_sources.append(
                    {
                        "pipeline_id": pid,
                        "alias": src.get("alias"),
                        "name": pdef.name if pdef else None,
                        "schema_summary": {
                            "field_count": len(pdef.data.get("schema", {}).get("fields", [])) if pdef else 0,
                        },
                    }
                )
        finally:
            pda.close()
    finally:
        wda.close()

    return {
        "id": definition.id,
        "name": definition.name,
        "description": definition.description,
        "statuses": definition.data.get("statuses", []),
        "config": definition.data.get("config", {}),
        "template_type": definition.template_type,
        "render_code": render_code.component_code if render_code else None,
        "render_code_version": render_code.version if render_code else None,
        "pipeline_sources": enriched_sources,
    }


_WORKFLOW_UI_RE = re.compile(r"\bfunction\s+WorkflowUI\s*\(")
_LET_CONST_RE = re.compile(r"\b(const|let)\s+\w+")


def _validate_render_code(jsx: str) -> None:
    """Light heuristic validation. Rejects obvious foot-guns; leaves full
    Babel parsing to the frontend transpile step."""
    if not jsx or not jsx.strip():
        raise MCPToolError("INVALID_JSX", "render_code is empty")
    if not _WORKFLOW_UI_RE.search(jsx):
        raise MCPToolError(
            "INVALID_JSX",
            "render_code must declare `function WorkflowUI(...)`. "
            "Use a function declaration, not an arrow or const.",
        )
    bad_decls = _LET_CONST_RE.findall(jsx)
    if bad_decls:
        raise MCPToolError(
            "INVALID_JSX",
            "render_code must use `var` (not `const`/`let`). " f"Found: {', '.join(sorted(set(bad_decls)))}",
        )


@register(
    name="workflow_update_render_code",
    description=(
        "Replace a workflow's render_code (the JSX UI). Validates on the server: "
        "must define function WorkflowUI and use `var` declarations only. "
        "Rejects with INVALID_JSX on validation failure. Uses expected_version "
        "for optimistic concurrency — re-fetch via workflow_get on VERSION_CONFLICT."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "workflow_id": {"type": "integer"},
            "opportunity_id": {"type": "integer"},
            "component_code": {"type": "string"},
            "expected_version": {"type": "integer"},
        },
        "required": ["workflow_id", "opportunity_id", "component_code", "expected_version"],
        "additionalProperties": False,
    },
    is_write=True,
)
def workflow_update_render_code(
    user,
    workflow_id: int,
    opportunity_id: int,
    component_code: str,
    expected_version: int,
):
    _validate_render_code(component_code)

    token = require_connect_token(user)
    wda = WorkflowDataAccess(access_token=token, opportunity_id=opportunity_id)
    try:
        current = wda.get_render_code(workflow_id)
        if current is None:
            raise MCPToolError(
                "NOT_FOUND",
                f"No render_code for workflow {workflow_id}. "
                "Create the workflow first via workflow_create_from_template.",
            )
        if current.version != expected_version:
            raise MCPToolError(
                "VERSION_CONFLICT",
                f"render_code is at version {current.version}, not {expected_version}. "
                "Call workflow_get to re-read and retry.",
                details={"server_version": current.version, "expected": expected_version},
            )
        new_record = wda.save_render_code(
            definition_id=workflow_id,
            component_code=component_code,
            version=expected_version + 1,
        )
        return {
            "workflow_id": workflow_id,
            "new_version": new_record.version,
            "_version_before": expected_version,
            "_version_after": new_record.version,
        }
    finally:
        if hasattr(wda, "close"):
            wda.close()
