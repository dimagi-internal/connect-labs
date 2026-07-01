"""Template discovery tool.

Lightweight read-only tool that surfaces the built-in template registry so MCP
callers can enumerate available templates before calling
`workflow_create_from_template`. Without this the caller has to know template
keys from the source tree.
"""

from __future__ import annotations

from connect_labs.workflow.templates import list_templates as _list_templates

from ..tool_registry import register


@register(
    name="list_templates",
    description=(
        "List the built-in workflow templates registered on this server. "
        "Returns one entry per template with key, name, description, icon, "
        "color, multi_opp, and supports_saved_runs. "
        "`supports_saved_runs: true` means the template is run-shaped — its "
        "runs follow the in_progress|completed lifecycle, and render code "
        "uses the `view` helper to read snapshot-vs-live data. "
        "`supports_saved_runs: false` (the default) means action-shaped — "
        "value lives in artifacts (audit sessions, tasks) persisted in "
        "their own models, no run-level completion. Use this to discover "
        "valid template_key values for workflow_create_from_template. "
        "Read-only; no auth needed beyond the PAT."
    ),
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
def list_templates(user):  # noqa: ARG001 — PAT auth happens upstream; tool is read-only
    return {"templates": _list_templates()}
