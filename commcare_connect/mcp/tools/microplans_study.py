"""MCP tools for the Microplans study-design CREATION demo seeder.

Thin ``@register`` shims over ``commcare_connect.microplans.study_seed`` so the
study can be seeded/reset on DEPLOYED labs (the MCP runs in-app, server-side),
where Overture building footprints + the same-region extract are available — the
local CLI (``scripts/walkthroughs/study-design/ensure_study.py``) is the same
logic for local/dev. The study is a labs-only program (``-opportunity_id``), so
the data-access short-circuits to the local labs DB; no Connect token is needed.
"""

from __future__ import annotations

from typing import Any

from commcare_connect.microplans import study_seed

from ..tool_registry import register


@register(
    name="microplans_study_ensure",
    description=(
        "Idempotently ensure the Vitamin-A Kaura two-arm study exists on the labs-only "
        "program (-opportunity_id from verified-monitoring/demo_config.json, the SAME "
        "manifest the monitoring narrative reads). Per round: two per-ward boundary plans "
        "(keyed by ward boundary_id), one study group (keyed by name) with labs-side arms "
        "(treatment->intervention, comparison->comparison), and — unless generate=false — "
        "the PSU sample drawn with the shared size-balanced config. Re-run is a no-op; "
        "reconciles drift. Pass only_round='r6' to limit to one round. Returns the per-round "
        "group_ids/plan_ids and sampling results."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "generate": {
                "type": "boolean",
                "default": True,
                "description": "Draw PSU samples for not-yet-sampled plans (fetches Overture footprints). "
                "false = create groups + boundary-only plans only.",
            },
            "only_round": {
                "type": ["string", "null"],
                "default": None,
                "description": "Limit to one round key, 'r1'..'r6' (optional).",
            },
        },
        "additionalProperties": False,
    },
    is_write=True,
)
def microplans_study_ensure(user, *, generate: bool = True, only_round: str | None = None) -> dict[str, Any]:
    manifest = study_seed.load_manifest()
    study_seed.ensure_synthetic_program(manifest, user=user)
    da = study_seed.data_access_for(manifest)
    try:
        return study_seed.ensure_study(da, manifest, generate=generate, only_round=only_round)
    finally:
        da.close()


@register(
    name="microplans_study_reset_round",
    description=(
        "Delete one round's study group + its member plans (and any plan still matching the "
        "round's ward boundaries) so the creation walkthrough can re-create that round live on "
        "camera. Safe when nothing exists yet. round_key is 'r1'..'r6' (the live-demo round is "
        "'r6', Attakar x Gura)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "round_key": {"type": "string", "description": "Round to reset, 'r1'..'r6'."},
        },
        "required": ["round_key"],
        "additionalProperties": False,
    },
    is_write=True,
)
def microplans_study_reset_round(user, *, round_key: str) -> dict[str, Any]:
    manifest = study_seed.load_manifest()
    da = study_seed.data_access_for(manifest)
    try:
        return study_seed.reset_round(da, manifest, round_key)
    finally:
        da.close()
