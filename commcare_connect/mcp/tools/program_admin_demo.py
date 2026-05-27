"""MCP tool wrapper for the Program Admin Report synthetic generator.

The implementation lives in ``commcare_connect/labs/synthetic/program_admin_demo.py``
next to the rest of the synthetic infrastructure (archetypes, manager_flow_views,
fixture_store, gdrive corpus). This module is a thin ``@register`` shim that
exposes it as the MCP-callable tool ``program_admin_demo_seed``.

Same pattern as ``synthetic.py`` / ``synthetic_tasks.py`` — keep MCP tools
thin, keep behavior in the labs/synthetic package.
"""

from __future__ import annotations

from typing import Any

from commcare_connect.labs.synthetic.program_admin_demo import program_admin_demo_seed as _seed

from ..tool_registry import register


@register(
    name="program_admin_demo_seed",
    description=(
        "Narrative-driven synthetic generator for the program-admin-report demo. "
        "Per opp, builds weekly chc_nutrition saved runs with backdated "
        "completed_at, applies per-FLW archetype trajectories (solid / "
        "improver_* / suspended_* / new_hire), generates AuditSession + Task "
        "records from named audit_archetype + task_archetype vocabularies "
        "(see commcare_connect/labs/synthetic/archetypes.py), and creates a "
        "final program_admin_report run watching all opps. Audits attach real "
        "MUAC stock images so the bulk-assessment view renders thumbnails. "
        "Pass cleanup_first=true (default) to wipe prior runs/decisions/tasks/"
        "audits for the opps before regenerating (idempotent)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "cleanup_first": {"type": "boolean", "default": True},
            "weeks": {
                "type": "array",
                "items": {"type": "string", "description": "ISO Monday date"},
                "minItems": 1,
            },
            "opps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "opportunity_id": {"type": "integer"},
                        "label": {"type": "string"},
                        "network_manager": {"type": "string"},
                        "missed_week_idxs": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "default": [],
                        },
                        "in_progress_last_week": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "If True, leave the LAST week's run as status=in_progress "
                                "with no decisions/audits/tasks generated. Used to set up "
                                "manager-flow walkthroughs where the demo recording drives "
                                "the manager doing the review live."
                            ),
                        },
                        "flws": {
                            "type": "array",
                            "items": {"type": "object"},  # validated dynamically
                            "minItems": 1,
                        },
                    },
                    "required": ["opportunity_id", "label", "network_manager", "flws"],
                    "additionalProperties": False,
                },
                "minItems": 1,
            },
        },
        "required": ["weeks", "opps"],
        "additionalProperties": False,
    },
    is_write=True,
)
def program_admin_demo_seed(
    user,
    *,
    weeks: list[str],
    opps: list[dict],
    cleanup_first: bool = True,
) -> dict[str, Any]:
    return _seed(user=user, weeks=weeks, opps=opps, cleanup_first=cleanup_first)
