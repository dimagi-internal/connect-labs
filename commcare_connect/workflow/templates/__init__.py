"""
Workflow Templates Registry.

This module automatically discovers and registers workflow templates from
individual template files in this directory.

Each template file should export a TEMPLATE dict with:
- key: Unique identifier
- name: Human-readable name
- description: Brief description
- icon: Font Awesome icon class
- color: Tailwind color name
- definition: Workflow definition dict
- render_code: JSX render code string
- pipeline_schema: Optional pipeline schema dict
- pipeline_schemas: Optional list of pipeline schema dicts (for multi-source templates)
- multi_opp: Optional bool (default False). When True, the template opts in to
  multi-opportunity support: the create flow shows an opp picker, the run page
  shows an opp editor, and pipeline rows/workers are tagged with opportunity_id.

Usage:
    from commcare_connect.workflow.templates import (
        TEMPLATES,
        get_template,
        list_templates,
        create_workflow_from_template,
    )
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from commcare_connect.workflow.data_access import WorkflowDataAccess

logger = logging.getLogger(__name__)

# =============================================================================
# Template Registry
# =============================================================================

# Discovered templates will be stored here
TEMPLATES: dict[str, dict] = {}


def _discover_templates() -> None:
    """
    Discover and register all templates from modules in this package.

    Each module should export a TEMPLATE dict. Modules starting with '_' or
    named 'base' are skipped.

    Saved-runs opt-in (see WORKFLOW_REFERENCE.md §"Saved-runs templates"):
    - `TEMPLATE["supports_saved_runs"] = True` enables the in_progress→completed
      lifecycle for this template's runs.
    - Optional `TEMPLATE["snapshot_inputs"]` declares what the framework's
      default hook should capture: `{"pipelines": [aliases], "workers": bool,
      "state_keys": [keys]}`. Anything not listed is not captured.
    - Optional `TEMPLATE["snapshot_schema"]` documents the shape render code
      can read from `instance.snapshot` (consumed by the FE `useRunView`
      helper and the completion confirmation copy).
    - Optional module-level `build_snapshot(*, pipelines, state, opportunity_id,
      **context) -> dict` overrides the default hook entirely — use when the
      snapshot shape differs from the inputs (computed summaries, KPIs, etc.).
    """
    import commcare_connect.workflow.templates as templates_package

    for _, module_name, _ in pkgutil.iter_modules(templates_package.__path__):
        # Skip private modules and base
        if module_name.startswith("_") or module_name == "base":
            continue

        try:
            module = importlib.import_module(f".{module_name}", package=__name__)
            if hasattr(module, "TEMPLATE"):
                template = module.TEMPLATE
                key = template.get("key")
                if key:
                    if hasattr(module, "build_snapshot") and callable(module.build_snapshot):
                        template["build_snapshot"] = module.build_snapshot
                    TEMPLATES[key] = template
                    logger.debug(f"Registered workflow template: {key}")
                else:
                    logger.warning(f"Template in {module_name} missing 'key' field")
        except Exception as e:
            logger.error(f"Failed to load template from {module_name}: {e}")


# Discover templates on module load
_discover_templates()


# =============================================================================
# Public API
# =============================================================================


def get_template(template_key: str) -> dict | None:
    """
    Get a workflow template by key.

    Args:
        template_key: Template identifier (e.g., 'performance_review')

    Returns:
        Template dict with 'name', 'description', 'definition', 'render_code'
        or None if not found
    """
    return TEMPLATES.get(template_key)


def list_templates() -> list[dict]:
    """
    List all available templates.

    Returns:
        List of dicts with 'key', 'name', 'description', 'icon', 'color',
        'multi_opp', and 'supports_saved_runs'.
    """
    return [
        {
            "key": key,
            "name": t["name"],
            "description": t["description"],
            "icon": t.get("icon", "fa-cog"),
            "color": t.get("color", "gray"),
            "multi_opp": bool(t.get("multi_opp", False)),
            "supports_saved_runs": bool(t.get("supports_saved_runs", False)),
        }
        for key, t in TEMPLATES.items()
    ]


# Soft size guards on snapshot blobs. JSON-serialized size is a reasonable
# proxy for what ends up in LabsRecord.data. Warn at 1 MB; log loudly at
# 5 MB (the design's hard-reject threshold — kept as a log for now to avoid
# breaking adopters mid-migration).
_SNAPSHOT_SIZE_WARN_BYTES = 1 * 1024 * 1024
_SNAPSHOT_SIZE_HARD_BYTES = 5 * 1024 * 1024


def _default_snapshot_from_inputs(
    *, snapshot_inputs: dict, pipelines: dict, state: dict, context: dict, opportunity_id: int
) -> dict:
    """Build the default snapshot honoring a template's declarative manifest.

    `snapshot_inputs` keys (all optional):
      - `pipelines`: list of alias strings to capture verbatim. None/missing
        means "all"; an empty list means "none."
      - `workers`: bool (default True) — capture worker list if present.
      - `state_keys`: list of state keys to capture. None/missing means "all
        of state"; an empty list means "no state."
    Anything not listed is not captured.
    """
    out: dict = {"schema_version": 1}

    pipelines_filter = snapshot_inputs.get("pipelines")
    if pipelines_filter is None:
        out["pipelines"] = pipelines
    else:
        # A declared alias missing from the live result is contract drift.
        missing = [alias for alias in pipelines_filter if alias not in pipelines]
        if missing:
            logger.warning("snapshot_inputs declared pipeline aliases not present at completion: %s", missing)
        out["pipelines"] = {alias: pipelines[alias] for alias in pipelines_filter if alias in pipelines}

    if snapshot_inputs.get("workers", True):
        out["workers"] = context.get("workers", [])

    state_keys = snapshot_inputs.get("state_keys")
    if state_keys is None:
        out["state"] = state
    else:
        out["state"] = {k: state.get(k) for k in state_keys if k in state}

    out["opportunity_ids"] = context.get("opportunity_ids", [opportunity_id])
    return out


def _check_snapshot_size(template_key: str, snapshot: dict) -> None:
    """Log a warning if the JSON-serialized snapshot is larger than the soft cap."""
    import json as _json

    try:
        size = len(_json.dumps(snapshot, default=str).encode("utf-8"))
    except Exception:
        logger.exception("Could not measure snapshot size for %s", template_key)
        return
    if size >= _SNAPSHOT_SIZE_HARD_BYTES:
        logger.error(
            "Snapshot for template %r is %.1f MB (>= %.0f MB hard cap). "
            "Consider declaring snapshot_inputs to trim, or a custom build_snapshot hook.",
            template_key,
            size / 1024 / 1024,
            _SNAPSHOT_SIZE_HARD_BYTES / 1024 / 1024,
        )
    elif size >= _SNAPSHOT_SIZE_WARN_BYTES:
        logger.warning(
            "Snapshot for template %r is %.1f MB (>= %.0f MB soft cap).",
            template_key,
            size / 1024 / 1024,
            _SNAPSHOT_SIZE_WARN_BYTES / 1024 / 1024,
        )


def build_snapshot_for_template(
    template_key: str,
    *,
    pipelines: dict,
    state: dict,
    opportunity_id: int,
    **context,
) -> dict | None:
    """Build the snapshot for a saved-runs template.

    Resolution order:
      1. If the template isn't registered or doesn't declare
         `supports_saved_runs: True`, return `None`.
      2. If the template defines a module-level `build_snapshot` hook, call
         it. The hook owns the shape entirely; use this when the snapshot
         shape differs from the raw inputs (computed summaries, KPIs).
      3. Otherwise, use the framework's default hook, which respects the
         template's `snapshot_inputs` manifest. Templates that just need
         "capture these inputs verbatim" can opt in with one line plus the
         manifest and never write Python.

    Hook contract: `build_snapshot(*, pipelines, state, opportunity_id,
    **context) -> dict`. Context keys may grow over time (currently
    `workers`, `opportunity_ids`); hooks should accept `**context` to stay
    forward-compatible.

    Hooks run server-side at completion time, so they have full Python access.
    """
    template = TEMPLATES.get(template_key)
    if not template:
        return None
    if not template.get("supports_saved_runs"):
        return None

    builder = template.get("build_snapshot")
    if callable(builder):
        snapshot = builder(pipelines=pipelines, state=state, opportunity_id=opportunity_id, **context)
    else:
        snapshot_inputs = template.get("snapshot_inputs")
        if snapshot_inputs is None:
            # Permissive fallback: dump everything. Logged because templates
            # should declare what they capture for clarity and size discipline.
            logger.warning(
                "Template %r declares supports_saved_runs but no build_snapshot hook "
                "and no snapshot_inputs manifest — falling back to dump-everything. "
                "Add a `snapshot_inputs` block to the template to make the contract explicit.",
                template_key,
            )
            snapshot_inputs = {}
        snapshot = _default_snapshot_from_inputs(
            snapshot_inputs=snapshot_inputs,
            pipelines=pipelines,
            state=state,
            context=context,
            opportunity_id=opportunity_id,
        )

    if isinstance(snapshot, dict):
        _check_snapshot_size(template_key, snapshot)
    return snapshot


def create_workflow_from_template(
    data_access: WorkflowDataAccess,
    template_key: str,
    request=None,
    opportunity_ids: list[int] | None = None,
) -> tuple:
    """
    Create a workflow from a template using the data access layer.

    If the template includes a pipeline_schema, a pipeline will also be created
    and linked to the workflow.

    Args:
        data_access: WorkflowDataAccess instance with valid OAuth
        template_key: Template key (e.g., 'performance_review')
        request: Optional HttpRequest for creating pipelines (needed for PipelineDataAccess)
        opportunity_ids: Optional list of opp IDs this workflow should pull data from
            (multi-opp templates only; ignored for single-opp templates).

    Returns:
        Tuple of (definition_record, render_code_record, pipeline_record or None)

    Raises:
        ValueError: If template not found
    """
    template = get_template(template_key)
    if not template:
        raise ValueError(f"Unknown template: {template_key}")

    template_def = template["definition"]
    pipeline_schema = template.get("pipeline_schema")
    pipeline_record = None
    pipeline_sources = []

    # PipelineDataAccess can be constructed from either an HttpRequest (web
    # view path) or a direct access_token (MCP/CLI path). We reuse whatever
    # token ``data_access`` already has so the MCP can create pipelines too.
    # We also forward the scope IDs so the new pipeline record is scoped to
    # the same opp/program/org as the workflow — otherwise the record is
    # created unscoped and subsequent scoped reads (`pipeline_get`, list views)
    # can't see it. The web path gets this for free via
    # ``request.labs_context``; the MCP path has to pass them explicitly.
    pipeline_access_token = getattr(data_access, "access_token", None)
    pipeline_scope_kwargs = {
        "opportunity_id": getattr(data_access, "opportunity_id", None),
        "program_id": getattr(data_access, "program_id", None),
        "organization_id": getattr(data_access, "organization_id", None),
    }
    can_create_pipelines = bool(request) or bool(pipeline_access_token)

    # Create pipeline if template has one (singular schema)
    if pipeline_schema and can_create_pipelines:
        from commcare_connect.workflow.data_access import PipelineDataAccess

        pipeline_data_access = PipelineDataAccess(
            request=request,
            access_token=pipeline_access_token,
            **pipeline_scope_kwargs,
        )
        pipeline_record = pipeline_data_access.create_definition(
            name=pipeline_schema["name"],
            description=pipeline_schema["description"],
            schema=pipeline_schema,
        )
        pipeline_data_access.close()

        # Determine alias based on template type
        alias_map = {
            "performance_review": "performance_data",
        }
        pipeline_alias = alias_map.get(template_key, "data")

        # Add pipeline as a source with a default alias
        pipeline_sources = [
            {
                "pipeline_id": pipeline_record.id,
                "alias": pipeline_alias,
            }
        ]

    # Handle multiple pipeline schemas (e.g., MBW with 3 sources)
    pipeline_schemas = template.get("pipeline_schemas", [])
    if pipeline_schemas and can_create_pipelines:
        from commcare_connect.workflow.data_access import PipelineDataAccess

        pipeline_data_access = PipelineDataAccess(
            request=request,
            access_token=pipeline_access_token,
            **pipeline_scope_kwargs,
        )
        for ps in pipeline_schemas:
            record = pipeline_data_access.create_definition(
                name=ps["name"],
                description=ps.get("description", ""),
                schema=ps["schema"],
            )
            pipeline_sources.append(
                {
                    "pipeline_id": record.id,
                    "alias": ps["alias"],
                }
            )
        pipeline_data_access.close()

    # Create the workflow definition with pipeline source if created
    config = template_def.get("config", {})
    config["templateType"] = template_key  # Store template type for filtering
    config["multi_opp"] = bool(template.get("multi_opp", False))
    definition = data_access.create_definition(
        name=template_def["name"],
        description=template_def["description"],
        statuses=template_def.get("statuses", []),
        config=config,
        pipeline_sources=pipeline_sources,
        opportunity_ids=list(opportunity_ids or []),
    )

    # Create the render code
    render_code = data_access.save_render_code(
        definition_id=definition.id,
        component_code=template["render_code"],
        version=1,
    )

    return definition, render_code, pipeline_record


# =============================================================================
# Re-export for backwards compatibility
# =============================================================================

# Re-export individual template modules for direct access if needed
from . import (  # noqa: E402
    audit_with_ai_review,
    kmc_flw_flags,
    kmc_longitudinal,
    kmc_project_metrics,
    llo_weekly_review,
    mbw_monitoring_v2,
    mbw_monitoring_v3,
    ocs_outreach,
    performance_review,
    program_admin_audit,
)

__all__ = [
    "TEMPLATES",
    "get_template",
    "list_templates",
    "create_workflow_from_template",
    # Individual template modules
    "performance_review",
    "ocs_outreach",
    "audit_with_ai_review",
    "bulk_image_audit",
    "mbw_monitoring_v2",
    "mbw_monitoring_v3",
    "kmc_longitudinal",
    "kmc_flw_flags",
    "kmc_project_metrics",
    "llo_weekly_review",
    "program_admin_audit",
]
