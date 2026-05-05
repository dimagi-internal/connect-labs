"""MCP tools for the labs synthetic-data system."""

from __future__ import annotations

from typing import Any

from commcare_connect.labs.synthetic.models import SyntheticOpportunity
from commcare_connect.labs.synthetic.registry import invalidate_cache

from ..tool_registry import register


@register(
    name="synthetic_register",
    description=(
        "Register or update a synthetic-opportunity entry. Set enabled=True "
        "to make labs serve fixtures from the given GDrive folder for this "
        "opportunity_id; set enabled=False to disable without deleting."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "opportunity_id": {"type": "integer"},
            "gdrive_folder_id": {"type": "string"},
            "enabled": {"type": "boolean", "default": True},
            "label": {"type": ["string", "null"], "default": None},
        },
        "required": ["opportunity_id", "gdrive_folder_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def synthetic_register(
    user,
    *,
    opportunity_id: int,
    gdrive_folder_id: str,
    enabled: bool = True,
    label: str | None = None,
) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "gdrive_folder_id": gdrive_folder_id,
        "enabled": enabled,
        "created_by": user,
    }
    if label is not None:
        defaults["label"] = label
    row, _created = SyntheticOpportunity.objects.update_or_create(
        opportunity_id=opportunity_id,
        defaults=defaults,
    )
    invalidate_cache()
    return {
        "opportunity_id": row.opportunity_id,
        "gdrive_folder_id": row.gdrive_folder_id,
        "enabled": row.enabled,
        "label": row.label,
    }
