"""
Base types and utilities for workflow templates.

Each template module should export a TEMPLATE dict with the following structure:
{
    "key": str,              # Unique identifier (e.g., "performance_review")
    "name": str,             # Human-readable name
    "description": str,      # Brief description
    "icon": str,             # Font Awesome icon class (e.g., "fa-clipboard-check")
    "color": str,            # Tailwind color name (green, blue, purple, orange, red, gray)
    "definition": dict,      # Workflow definition structure
    "render_code": str,      # JSX render code as a string
    "pipeline_schema": dict | None,  # Optional pipeline schema
}
"""

from typing import TypedDict


class TemplateDefinition(TypedDict, total=False):
    """Structure for workflow definition within a template."""

    name: str
    description: str
    version: int
    templateType: str
    statuses: list[dict]
    config: dict
    pipeline_sources: list


class PipelineSchema(TypedDict, total=False):
    """Structure for optional pipeline schema."""

    name: str
    description: str
    version: int
    grouping_key: str
    terminal_stage: str
    linking_field: str
    fields: list[dict]
    histograms: list
    filters: dict


class Template(TypedDict, total=False):
    """Complete template structure that each template module should export."""

    key: str
    name: str
    description: str
    icon: str
    color: str
    definition: TemplateDefinition
    render_code: str
    pipeline_schema: PipelineSchema | None
