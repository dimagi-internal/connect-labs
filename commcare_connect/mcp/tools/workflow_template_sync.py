"""workflow_sync_from_template_file — push a template .py into a live workflow.

The tool lets template authors iterate on the version-controlled .py file
as the source of truth, without redeploying labs between edits. See
docs/superpowers/specs/2026-05-21-workflow-sync-from-template-file-design.md
for the design contract.
"""

from __future__ import annotations

from commcare_connect.workflow.data_access import WorkflowDataAccess

from ..connect_token import require_connect_token
from ..tool_registry import MCPToolError, register
from ._template_parser import TemplateParseError, parse_template_source

_DEFINITION_DIFF_KEYS = {"name", "description", "statuses", "config", "pipeline_sources"}


def _definition_changed_keys(before: dict, after: dict) -> list[str]:
    """Top-level keys whose values differ between two definition dicts."""
    keys = set(before) | set(after)
    return sorted(k for k in keys if k in _DEFINITION_DIFF_KEYS and before.get(k) != after.get(k))


def _build_new_definition_data(current: dict, parsed_definition: dict, new_version: int) -> dict:
    """Merge a parsed DEFINITION onto the current workflow's data.

    Preserves fields the workflow tracks but the template doesn't own
    (`opportunity_ids`, `is_template`, `template_scope`, `templateType` from
    the template config). Lifts only the fields the template authoritatively
    sets: name, description, statuses, config, pipeline_sources.
    """
    out = dict(current)
    for key in ("name", "description", "statuses", "config", "pipeline_sources"):
        if key in parsed_definition:
            out[key] = parsed_definition[key]
    out["version"] = new_version
    return out


@register(
    name="workflow_sync_from_template_file",
    description=(
        "Push a workflow template .py (plus any _render.js sidecar) into a "
        "live workflow instance. Lets template authors iterate against a "
        "preview workflow with no deploy. The workflow's template_type must "
        "match TEMPLATE['key'] in the supplied source. Uses optimistic "
        "concurrency on both the definition and the render_code version. "
        "Set dry_run=true to validate and diff without writing."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "workflow_id": {"type": "integer"},
            "opportunity_id": {"type": "integer"},
            "template_source": {"type": "string"},
            "sidecar_files": {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
            "expected_render_code_version": {"type": "integer"},
            "expected_definition_version": {"type": "integer"},
            "dry_run": {"type": "boolean"},
        },
        "required": [
            "workflow_id",
            "opportunity_id",
            "template_source",
            "expected_render_code_version",
            "expected_definition_version",
        ],
        "additionalProperties": False,
    },
    is_write=True,
)
def workflow_sync_from_template_file(
    user,
    workflow_id: int,
    opportunity_id: int,
    template_source: str,
    expected_render_code_version: int,
    expected_definition_version: int,
    sidecar_files: dict | None = None,
    dry_run: bool = False,
):
    try:
        parsed = parse_template_source(template_source, sidecar_files or {})
    except TemplateParseError as e:
        raise MCPToolError("INVALID_TEMPLATE", str(e))

    # Mirror the size cap enforced by workflow_update_render_code so the sync
    # path can't smuggle in a payload that the dedicated update tool would
    # refuse. 512 KB matches workflows._MAX_RENDER_CODE_BYTES.
    _MAX_RENDER_CODE_BYTES = 512 * 1024
    if not parsed.render_code.strip():
        raise MCPToolError("INVALID_TEMPLATE", "RENDER_CODE is empty after parsing")
    if len(parsed.render_code.encode("utf-8")) > _MAX_RENDER_CODE_BYTES:
        raise MCPToolError(
            "INVALID_TEMPLATE",
            f"RENDER_CODE exceeds {_MAX_RENDER_CODE_BYTES // 1024} KB. "
            "Split the template into helpers or move data to pipelines.",
        )

    token = require_connect_token(user)
    wda = WorkflowDataAccess(access_token=token, opportunity_id=opportunity_id)
    try:
        current_def = wda.get_definition(workflow_id)
        if current_def is None:
            raise MCPToolError("NOT_FOUND", f"No workflow with id {workflow_id}")

        current_render = wda.get_render_code(workflow_id)
        if current_render is None:
            raise MCPToolError(
                "NOT_FOUND",
                f"No render_code for workflow {workflow_id}.",
            )

        current_def_version = current_def.data.get("version", 1)
        if current_def_version != expected_definition_version:
            raise MCPToolError(
                "VERSION_CONFLICT",
                f"workflow definition is at version {current_def_version}, "
                f"not {expected_definition_version}. Re-fetch via workflow_get and retry.",
                details={
                    "field": "definition",
                    "server_version": current_def_version,
                    "expected": expected_definition_version,
                },
            )
        if current_render.version != expected_render_code_version:
            raise MCPToolError(
                "VERSION_CONFLICT",
                f"render_code is at version {current_render.version}, "
                f"not {expected_render_code_version}. Re-fetch via workflow_get and retry.",
                details={
                    "field": "render_code",
                    "server_version": current_render.version,
                    "expected": expected_render_code_version,
                },
            )

        if current_def.template_type and current_def.template_type != parsed.template_key:
            raise MCPToolError(
                "TEMPLATE_KEY_MISMATCH",
                f"workflow {workflow_id} has template_type {current_def.template_type!r} "
                f"but supplied template has key {parsed.template_key!r}",
                details={
                    "workflow_template_type": current_def.template_type,
                    "supplied_template_key": parsed.template_key,
                },
            )

        new_def_data = _build_new_definition_data(
            current_def.data,
            parsed.definition,
            new_version=expected_definition_version + 1,
        )

        render_changed = (current_render.component_code or "") != parsed.render_code
        definition_changed = _definition_changed_keys(current_def.data, new_def_data)

        result = {
            "workflow_id": workflow_id,
            "dry_run": dry_run,
            "render_code": {
                "version_before": current_render.version,
                "version_after": current_render.version if dry_run else current_render.version + 1,
                "bytes_before": len((current_render.component_code or "").encode("utf-8")),
                "bytes_after": len(parsed.render_code.encode("utf-8")),
                "changed": render_changed,
            },
            "definition": {
                "version_before": expected_definition_version,
                "version_after": expected_definition_version if dry_run else expected_definition_version + 1,
                "changed_keys": definition_changed,
            },
            "pipelines": [],
        }

        if dry_run:
            return result

        # Writes — implemented in a follow-up task.
        raise MCPToolError(
            "NOT_IMPLEMENTED",
            "non-dry-run sync is not implemented yet — set dry_run=true to validate.",
        )
    finally:
        if hasattr(wda, "close"):
            wda.close()
