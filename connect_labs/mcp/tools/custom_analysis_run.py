"""custom_analysis_run — run a custom_analysis AnalysisPipelineConfig over MCP.

Mirrors what the custom_analysis dashboard views do server-side, but exposes
the per-FLW result rows over MCP so they can be diffed against the equivalent
workflow-template pipeline output. Used to verify that a workflow-template
port of a custom_analysis dashboard produces identical numbers before the
old dashboard is removed.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from connect_labs.labs.analysis.pipeline import AnalysisPipeline

from ..connect_token import require_connect_token
from ..tool_registry import MCPToolError, register

# Map MCP-callable config_key → import path of the AnalysisPipelineConfig instance.
# Keeping this as an explicit allowlist (not auto-discovered) so the tool surface
# stays narrow and we don't accidentally expose configs that weren't meant to be
# externally callable.
_CONFIG_REGISTRY = {
    "chc_nutrition": "connect_labs.custom_analysis.chc_nutrition.analysis_config:CHC_NUTRITION_CONFIG",
}


def _load_config(config_key: str):
    spec = _CONFIG_REGISTRY.get(config_key)
    if spec is None:
        raise MCPToolError(
            "INVALID_SCHEMA",
            f"Unknown config_key {config_key!r}. Available: {sorted(_CONFIG_REGISTRY)}.",
        )
    module_path, attr = spec.split(":", 1)
    import importlib

    module = importlib.import_module(module_path)
    return getattr(module, attr)


@register(
    name="custom_analysis_run",
    description=(
        "Run a custom_analysis AnalysisPipelineConfig server-side and return "
        "per-FLW rows. Use to compare custom_analysis output against the "
        "equivalent workflow-template pipeline output for the same opp before "
        "retiring the old dashboard. Available config_keys: chc_nutrition."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "opportunity_id": {"type": "integer"},
            "config_key": {
                "type": "string",
                "default": "chc_nutrition",
                "description": "Identifier for which custom_analysis config to run.",
            },
        },
        "required": ["opportunity_id"],
        "additionalProperties": False,
    },
    is_write=False,
)
def custom_analysis_run(user, *, opportunity_id: int, config_key: str = "chc_nutrition") -> dict[str, Any]:
    config = _load_config(config_key)
    token = require_connect_token(user)

    # AnalysisPipeline reads .user from the request for the export-client routing
    # (synthetic-vs-prod dispatch). For labs-only opps the export client doesn't
    # fire because the SQL cache is already hot, but we set .user anyway so the
    # cache-miss path still works for labs-only with a backing fixture folder.
    fake_request = SimpleNamespace(
        user=user,
        session={"labs_oauth": {"access_token": token}},
        labs_context={"opportunity_id": opportunity_id},
        GET={},
    )
    pipeline = AnalysisPipeline(request=fake_request, access_token=token)
    result = pipeline.stream_analysis_ignore_events(config, opportunity_id=opportunity_id)

    rows = []
    for flw in result.rows:
        row = {
            "username": flw.username,
            "total_visits": getattr(flw, "total_visits", None),
            "approved_visits": getattr(flw, "approved_visits", None),
            "pending_visits": getattr(flw, "pending_visits", None),
            "rejected_visits": getattr(flw, "rejected_visits", None),
            "flagged_visits": getattr(flw, "flagged_visits", None),
            "days_active": getattr(flw, "days_active", None),
            "first_visit_date": str(getattr(flw, "first_visit_date", "") or ""),
            "last_visit_date": str(getattr(flw, "last_visit_date", "") or ""),
        }
        # Custom fields are stored on flw.custom_fields (a dict). Surface them flat
        # so they can be diffed directly against pipeline_preview row keys.
        for k, v in (getattr(flw, "custom_fields", {}) or {}).items():
            row[k] = v
        rows.append(row)

    return {
        "opportunity_id": opportunity_id,
        "config_key": config_key,
        "row_count": len(rows),
        "rows": rows,
        "metadata": result.metadata or {},
    }
