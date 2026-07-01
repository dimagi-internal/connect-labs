"""MCP tools for the Campaign Utility Tool's synthetic data.

These run in-app on labs (direct DB access; the real GeoPoDe NGA boundaries are
already loaded), so a national synthetic campaign can be built without an AWS/ECS
one-off task.
"""
from typing import Any

from ..tool_registry import MCPToolError, register

DEFAULT_WORKERS = 5000


@register(
    name="campaign_build_national",
    description=(
        "Build (or rebuild) a national-scale synthetic campaign for the Campaign "
        "Utility Tool (/campaign/). Generates `worker_count` workers as CommCare "
        "cases spread across the REAL Nigeria geography (labs AdminBoundary: states -> "
        "LGAs -> wards), and registers them as a synthetic CommCare project space the "
        "tool reads via the Case API. Idempotent by campaign `code` (rebuilds it). "
        "Note: the bootstrap ships every worker in one payload, so counts above a few "
        "thousand are heavy in the browser until the paginated-bootstrap work lands "
        "(the data generates fine at any scale)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "worker_count": {"type": "integer", "default": DEFAULT_WORKERS, "minimum": 1, "maximum": 200000},
            "states_limit": {
                "type": ["integer", "null"],
                "default": None,
                "description": "Cap how many states the roster spreads across (null = all loaded states).",
            },
            "code": {"type": "string", "default": "MR-NAT-2026"},
            "name": {"type": "string", "default": "Measles–Rubella Vaccination Campaign (National)"},
        },
        "required": [],
        "additionalProperties": False,
    },
    is_write=True,
)
def campaign_build_national(
    user,
    *,
    worker_count: int = DEFAULT_WORKERS,
    states_limit: int | None = None,
    code: str = "MR-NAT-2026",
    name: str = "Measles–Rubella Vaccination Campaign (National)",
) -> dict[str, Any]:
    from connect_labs.campaign.services import geography, synthetic_campaign

    if not geography.is_loaded():
        raise MCPToolError(
            "UPSTREAM_ERROR",
            "Nigeria admin boundaries are not loaded in this environment. "
            "Run `manage.py load_geopode_from_drive --iso NGA` first.",
        )
    campaign = synthetic_campaign.build_synthetic_campaign(
        worker_count=worker_count, states_limit=states_limit, code=code, name=name
    )
    return {
        "campaign_code": campaign.code,
        "campaign_name": campaign.name,
        "commcare_domain": campaign.commcare_domain,
        "workers": campaign.worker_cases.count(),
        "states": campaign.regions.count(),
        "microplans": campaign.microplans.count(),
    }
