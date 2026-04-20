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


from commcare_connect.workflow.templates import (  # noqa: E402
    create_workflow_from_template as _create_workflow_from_template,
)

_DEFINITION_PATCH_ALLOWED = {"name", "description", "statuses", "config"}


@register(
    name="workflow_update_definition",
    description=(
        "Update fields on a workflow definition. Accepts a patch dict. "
        "Allowed keys: name, description, statuses, config. `statuses` replaces "
        "wholesale; `config` shallow-merges. Unknown keys rejected with INVALID_SCHEMA. "
        "Uses expected_version for optimistic concurrency."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "workflow_id": {"type": "integer"},
            "opportunity_id": {"type": "integer"},
            "patch": {"type": "object"},
            "expected_version": {"type": "integer"},
        },
        "required": ["workflow_id", "opportunity_id", "patch", "expected_version"],
        "additionalProperties": False,
    },
    is_write=True,
)
def workflow_update_definition(
    user,
    workflow_id: int,
    opportunity_id: int,
    patch: dict,
    expected_version: int,
):
    unknown_keys = set(patch) - _DEFINITION_PATCH_ALLOWED
    if unknown_keys:
        raise MCPToolError(
            "INVALID_SCHEMA",
            f"Unknown patch keys: {sorted(unknown_keys)}. " f"Allowed: {sorted(_DEFINITION_PATCH_ALLOWED)}",
        )

    token = require_connect_token(user)
    wda = WorkflowDataAccess(access_token=token, opportunity_id=opportunity_id)
    try:
        current = wda.get_definition(workflow_id)
        if current is None:
            raise MCPToolError("NOT_FOUND", f"No workflow with id {workflow_id}")
        current_version = current.data.get("version", 1)
        if current_version != expected_version:
            raise MCPToolError(
                "VERSION_CONFLICT",
                f"workflow definition is at version {current_version}, not {expected_version}. "
                "Call workflow_get to re-read and retry.",
                details={"server_version": current_version, "expected": expected_version},
            )

        # Build the updated payload. Keep existing data, then apply patch per rules.
        # Real update_definition(definition_id, data) takes only id + full data dict.
        new_data = dict(current.data)  # shallow copy
        if "name" in patch:
            new_data["name"] = patch["name"]
        if "description" in patch:
            new_data["description"] = patch["description"]
        if "statuses" in patch:
            new_data["statuses"] = patch["statuses"]  # replace wholesale
        if "config" in patch:
            merged_config = dict(new_data.get("config", {}))
            merged_config.update(patch["config"])
            new_data["config"] = merged_config
        new_data["version"] = expected_version + 1

        updated = wda.update_definition(
            definition_id=workflow_id,
            data=new_data,
        )
        new_version = updated.data.get("version", expected_version + 1)
        return {
            "workflow_id": workflow_id,
            "new_version": new_version,
            "_version_before": expected_version,
            "_version_after": new_version,
        }
    finally:
        if hasattr(wda, "close"):
            wda.close()


@register(
    name="workflow_revert_render_code",
    description=(
        "Restore a prior render_code version as a new save. Useful for `undo that`. "
        "Does NOT rewrite history — it creates a new version containing the prior "
        "code. Re-reading returns the new version number. "
        "NOTE: Because only the latest render_code is stored (versions are counters, "
        "not snapshots), this tool can only revert if the caller supplies the prior "
        "code directly. Use to_version to confirm the expected current version before "
        "the revert, and component_code to supply the prior code to restore."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "workflow_id": {"type": "integer"},
            "opportunity_id": {"type": "integer"},
            "to_version": {
                "type": "integer",
                "description": (
                    "The version number the caller wants to restore TO. "
                    "Must be less than current version. Used as new version label."
                ),
            },
            "component_code": {
                "type": "string",
                "description": "The JSX code to restore (the prior code you want to reapply).",
            },
        },
        "required": ["workflow_id", "opportunity_id", "to_version", "component_code"],
        "additionalProperties": False,
    },
    is_write=True,
)
def workflow_revert_render_code(
    user,
    workflow_id: int,
    opportunity_id: int,
    to_version: int,
    component_code: str,
):
    """Restore a prior render_code version as a new save.

    Implementation note: The data access layer stores only the latest render_code
    (version is a counter, not a snapshot history). Old code is not retrievable via
    get_render_code. The caller must therefore supply the code to restore via
    component_code. This tool validates the JSX, bumps the version counter, and saves.
    """
    _validate_render_code(component_code)

    token = require_connect_token(user)
    wda = WorkflowDataAccess(access_token=token, opportunity_id=opportunity_id)
    try:
        current = wda.get_render_code(workflow_id)
        if current is None:
            raise MCPToolError("NOT_FOUND", f"Workflow {workflow_id} has no render_code")

        if to_version >= current.version:
            raise MCPToolError(
                "INVALID_SCHEMA",
                f"to_version ({to_version}) must be less than current version ({current.version}). "
                "You can only revert to an older version.",
                details={"current_version": current.version, "to_version": to_version},
            )

        new_version = current.version + 1
        new_record = wda.save_render_code(
            definition_id=workflow_id,
            component_code=component_code,
            version=new_version,
        )
        return {
            "workflow_id": workflow_id,
            "new_version": new_record.version,
            "reverted_to_source_version": to_version,
            "_version_before": current.version,
            "_version_after": new_record.version,
        }
    finally:
        if hasattr(wda, "close"):
            wda.close()


@register(
    name="workflow_create_from_template",
    description=(
        "Create a new workflow from a built-in Python seed template. "
        "template_key is one of the registered templates in "
        "commcare_connect/workflow/templates/*.py (e.g. 'performance_review'). "
        "Returns the new workflow_id. Pipelines linked in the template are NOT "
        "created automatically (they require a Django request context); use "
        "workflow_get to see pipeline_sources after creation."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "template_key": {"type": "string"},
            "opportunity_id": {"type": "integer"},
            "name": {
                "type": "string",
                "description": "Optional override for the workflow name.",
            },
        },
        "required": ["template_key", "opportunity_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def workflow_create_from_template(
    user,
    template_key: str,
    opportunity_id: int,
    name: str = None,
):
    token = require_connect_token(user)
    wda = WorkflowDataAccess(access_token=token, opportunity_id=opportunity_id)
    try:
        try:
            # request=None means pipelines won't be created (requires Django request).
            # The workflow definition and render_code are still created successfully.
            definition, render_code, pipeline = _create_workflow_from_template(
                data_access=wda,
                template_key=template_key,
                request=None,
            )
        except ValueError as e:
            # create_workflow_from_template raises ValueError on unknown template.
            raise MCPToolError("NOT_FOUND", str(e))

        # If an override name was passed, apply it via a second update.
        if name and name != definition.name:
            new_data = dict(definition.data)
            new_data["name"] = name
            wda.update_definition(
                definition_id=definition.id,
                data=new_data,
            )

        return {
            "workflow_id": definition.id,
            "render_code_version": render_code.version if render_code else None,
            "pipeline_id": pipeline.id if pipeline else None,
            "_version_before": None,
            "_version_after": 1,
        }
    finally:
        if hasattr(wda, "close"):
            wda.close()


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
