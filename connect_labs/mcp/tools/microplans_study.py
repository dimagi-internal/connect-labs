"""MCP tools for the Microplans study-design CREATION demo seeder.

Thin ``@register`` shims over ``connect_labs.microplans.study_seed`` so the
study can be seeded/reset on DEPLOYED labs (the MCP runs in-app, server-side),
where Overture building footprints + the same-region extract are available — the
local CLI (``scripts/walkthroughs/study-design/ensure_study.py``) is the same
logic for local/dev. The study is a labs-only program (``opportunity_id``), so
the data-access short-circuits to the local labs DB; no Connect token is needed.
"""

from __future__ import annotations

from typing import Any

from connect_labs.microplans import study_seed

from ..tool_registry import MCPToolError, register


def _study_data_access(manifest, user):
    """The study program's data access, as ``user``.

    The study program is pinned by the manifest rather than named in the call, so
    the dispatcher's labs-only gate (which reads ``program_id`` from the arguments)
    never sees it; the data access checks it instead."""
    from connect_labs.microplans.core.data_access import ProgramAccessDenied

    try:
        return study_seed.data_access_for(manifest, user=user)
    except ProgramAccessDenied:
        raise MCPToolError("NOT_FOUND", f"program {manifest.program_id} is not there, or you cannot open it")


@register(
    name="microplans_study_ensure",
    description=(
        "Idempotently ensure the Vitamin-A Kaura two-arm study exists on the labs-only "
        "program (opportunity_id from verified-monitoring/demo_config.json, the SAME "
        "manifest the monitoring narrative reads). Per round: ONE two-arm plan (keyed by "
        "its ward boundary_ids) whose input_areas carry both wards arm-tagged "
        "(intervention + comparison) — no study group — and, unless generate=false, the "
        "PSU sample drawn with the shared size-balanced config (pins + arm-coloured "
        "psu_hulls). Re-run is a no-op; reconciles drift. Pass only_round='r6' to limit to "
        "one round. Returns the per-round plan_id/created_plans and sampling results."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "generate": {
                "type": "boolean",
                "default": True,
                "description": "Draw PSU samples for not-yet-sampled plans (fetches Overture footprints). "
                "false = create the boundary-only two-arm plans only.",
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
    da = _study_data_access(manifest, user)
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
    da = _study_data_access(manifest, user)
    try:
        return study_seed.reset_round(da, manifest, round_key)
    finally:
        da.close()
