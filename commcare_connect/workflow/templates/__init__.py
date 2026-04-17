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
        List of dicts with 'key', 'name', 'description', 'icon', 'color', 'multi_opp'
    """
    return [
        {
            "key": key,
            "name": t["name"],
            "description": t["description"],
            "icon": t.get("icon", "fa-cog"),
            "color": t.get("color", "gray"),
            "multi_opp": bool(t.get("multi_opp", False)),
        }
        for key, t in TEMPLATES.items()
    ]


def create_workflow_from_template(
    data_access: WorkflowDataAccess,
    template_key: str,
    request=None,
) -> tuple:
    """
    Create a workflow from a template using the data access layer.

    If the template includes a pipeline_schema, a pipeline will also be created
    and linked to the workflow.

    Args:
        data_access: WorkflowDataAccess instance with valid OAuth
        template_key: Template key (e.g., 'performance_review')
        request: Optional HttpRequest for creating pipelines (needed for PipelineDataAccess)

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

    # Create pipeline if template has one (singular schema)
    if pipeline_schema and request:
        from commcare_connect.workflow.data_access import PipelineDataAccess

        pipeline_data_access = PipelineDataAccess(request=request)
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
    if pipeline_schemas and request:
        from commcare_connect.workflow.data_access import PipelineDataAccess

        pipeline_data_access = PipelineDataAccess(request=request)
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
    definition = data_access.create_definition(
        name=template_def["name"],
        description=template_def["description"],
        statuses=template_def.get("statuses", []),
        config=config,
        pipeline_sources=pipeline_sources,
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
    mbw_monitoring_v2,
    ocs_outreach,
    performance_review,
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
    "kmc_longitudinal",
    "kmc_flw_flags",
    "kmc_project_metrics",
]
