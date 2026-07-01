"""MCP tool wrapper for the Program Admin Report synthetic generator.

The implementation lives in ``connect_labs/labs/synthetic/program_admin_demo.py``
next to the rest of the synthetic infrastructure (archetypes, manager_flow_views,
fixture_store, gdrive corpus). This module is a thin ``@register`` shim that
exposes it as the MCP-callable tool ``program_admin_demo_seed``.

Same pattern as ``synthetic.py`` / ``synthetic_tasks.py`` — keep MCP tools
thin, keep behavior in the labs/synthetic package.
"""

from __future__ import annotations

from typing import Any

from connect_labs.labs.synthetic.program_admin_demo import program_admin_demo_seed as _seed

from ..tool_registry import register


@register(
    name="program_admin_demo_seed",
    description=(
        "Narrative-driven synthetic generator for the program-admin-report demo. "
        "Per opp, builds weekly chc_nutrition saved runs with backdated "
        "completed_at, applies per-FLW archetype trajectories (solid / "
        "improver_* / suspended_* / new_hire), generates AuditSession + Task "
        "records from named audit_archetype + task_archetype vocabularies "
        "(see connect_labs/labs/synthetic/archetypes.py), and creates a "
        "final program_admin_report run watching all opps. ``weeks`` are the "
        "PAR window's COMPLETED weeks; ``current_week`` (optional) adds an "
        "in-progress run for the live manager-flow demo OUTSIDE that window. "
        "Audits attach real MUAC stock images so the bulk-assessment view "
        "renders thumbnails. Pass cleanup_first=true (default) to wipe prior "
        "runs/flags/tasks/audits for the opps before regenerating (idempotent)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "cleanup_first": {"type": "boolean", "default": True},
            "weeks": {
                "type": "array",
                "items": {"type": "string", "description": "ISO Monday date"},
                "minItems": 1,
                "description": (
                    "COMPLETED weeks — the Program Admin Report's watched "
                    "window. Every week here gets a completed run (unless "
                    "listed in an opp's missed_week_idxs)."
                ),
            },
            "current_week": {
                "type": ["string", "null"],
                "default": None,
                "description": (
                    "ISO Monday of the in-progress CURRENT week, outside the "
                    "PAR window. Opps with in_progress_current_week=true get "
                    "one extra status=in_progress run for this week with no "
                    "seeded audits/tasks (the manager-flow walkthrough creates "
                    "those live). FLW flag_week indices may reference this "
                    "week as index len(weeks)."
                ),
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
                        "in_progress_current_week": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "If True (and current_week is set), add one "
                                "status=in_progress run for the current week with no "
                                "audits/tasks generated. Used to set up manager-flow "
                                "walkthroughs where the demo recording drives the "
                                "manager doing the review live."
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
    current_week: str | None = None,
) -> dict[str, Any]:
    return _seed(user=user, weeks=weeks, opps=opps, cleanup_first=cleanup_first, current_week=current_week)
