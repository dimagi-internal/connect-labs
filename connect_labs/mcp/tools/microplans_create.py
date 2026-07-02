"""MCP tools for CREATING microplans plans in bulk — run N wards through the plan
creation pipeline (coverage gridding / sampling) headlessly, then poll for results.

The web bulk-create page (``ProgramBulkCreatePlansView``) is session-authenticated,
so it can't be driven with a PAT. These tools expose the SAME engine
(``bulk_create_plans_task`` on the Celery worker → ``create_boundary_plan``) over the
MCP: enqueue with the full coverage parameter surface, then poll ``bulk_create_status``
for incremental per-ward results — matching the UI's enqueue+poll shape so 40 cold
Overture fetches don't block a single request.

The parameter surface (``coverage_config`` / ``grouping`` / ``assignment``) is passed
straight through to the ``CoverageConfig`` / ``GroupingConfig`` / ``AssignmentConfig``
dataclasses — their fields are the single source of truth. ``coverage_param_schema``
reflects those dataclasses so a caller can discover every knob (and its default/range)
without reading code, and adding a field to a dataclass surfaces it automatically.
"""

from __future__ import annotations

import dataclasses

from ..connect_token import require_connect_token
from ..tool_registry import MCPToolError, register
from .microplans import _is_labs_only, _require_program_access


def _access_token_for(user, program_id: int) -> str:
    """Token the Celery task constructs its data-access with. Labs-only programs
    short-circuit to the local backend (token unused), so a placeholder is fine when
    the caller has no Connect token; real programs require a valid one."""
    if _is_labs_only(program_id):
        try:
            return require_connect_token(user)
        except MCPToolError:
            return "labs-local"  # unused for labs-only programs
    return require_connect_token(user)


@register(
    name="microplans_bulk_create_plans",
    description=(
        "Create one draft plan per ward, in bulk, on the Celery worker — the headless "
        "equivalent of the bulk-create page. Give the ward AdminBoundary ids; coverage "
        "plans are gridded into work areas at creation (Overture fetch + clustering), "
        "sampling plans start boundary-only. Pass the full coverage parameter surface "
        "via coverage_config (see microplans_coverage_param_schema) and Phase-1 bucketing "
        "via grouping — both captured on each plan so a tuning run is reproducible. "
        "Returns immediately with {task_id, run_id}; poll microplans_bulk_create_status "
        "with the task_id for incremental per-ward results. Works for labs-only synthetic "
        "programs (program_id >= the labs-only floor = the backing opp id)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "program_id": {"type": "integer"},
            "boundary_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "AdminBoundary ids of the wards to run through creation (one plan each).",
            },
            "mode": {
                "type": "string",
                "enum": ["coverage", "sampling"],
                "default": "coverage",
            },
            "cell_size_m": {
                "type": "number",
                "default": 100.0,
                "description": "Coverage grid cell edge in metres (also settable inside coverage_config).",
            },
            "coverage_config": {
                "type": "object",
                "description": "CoverageConfig knobs beyond cell_size_m (min_confidence, sources, area/cell "
                "exclusion filters, population). See microplans_coverage_param_schema.",
                "additionalProperties": True,
            },
            "grouping": {
                "type": "object",
                "description": "Phase-1 GroupingConfig (strategy=bfs_adjacency|bbox, target_size, max_buildings, "
                "buffer_distance_m). See microplans_coverage_param_schema.",
                "additionalProperties": True,
            },
            "group_id": {
                "type": ["integer", "null"],
                "description": "Optional: file every created plan into this existing study/bundle group.",
            },
        },
        "required": ["program_id", "boundary_ids"],
        "additionalProperties": False,
    },
    is_write=True,
)
def microplans_bulk_create_plans(
    user,
    *,
    program_id,
    boundary_ids,
    mode="coverage",
    cell_size_m=100.0,
    coverage_config=None,
    grouping=None,
    group_id=None,
):
    import uuid

    from connect_labs.microplans.tasks import bulk_create_plans_task

    _require_program_access(user, program_id)
    if not isinstance(boundary_ids, list) or not boundary_ids:
        raise MCPToolError("INVALID_SCHEMA", "`boundary_ids` must be a non-empty list of AdminBoundary ids")
    mode = "coverage" if mode != "sampling" else "sampling"
    plans_input = [{"boundary_id": str(b).strip()} for b in boundary_ids if str(b).strip()]
    if not plans_input:
        raise MCPToolError("INVALID_SCHEMA", "`boundary_ids` contained no non-empty ids")

    access_token = _access_token_for(user, int(program_id))
    actor = user.get_username() if hasattr(user, "get_username") else str(user)
    run_id = f"bulk-{uuid.uuid4().hex[:12]}"

    task = bulk_create_plans_task.delay(
        int(program_id),
        plans_input,
        mode,
        dict(grouping or {}),
        float(cell_size_m),
        access_token,
        group_id=int(group_id) if group_id is not None else None,
        coverage_config=dict(coverage_config or {}),
        run_id=run_id,
        actor=actor,
    )
    return {
        "program_id": int(program_id),
        "task_id": task.id,
        "run_id": run_id,
        "mode": mode,
        "n_wards": len(plans_input),
        "poll_with": "microplans_bulk_create_status",
        "message": f"Enqueued {len(plans_input)} ward(s). Poll microplans_bulk_create_status with task_id={task.id!r}",
    }


@register(
    name="microplans_bulk_create_status",
    description=(
        "Poll a microplans_bulk_create_plans task by task_id. Returns the run state "
        "(queued|running|completed|failed) with incremental per-ward results — each "
        "row carries {index, name, boundary_id, status, plan_id?, work_areas?, detail?} "
        "— plus created/total counts and the run_id. Call repeatedly until state is "
        "completed or failed."
    ),
    input_schema={
        "type": "object",
        "properties": {"task_id": {"type": "string"}},
        "required": ["task_id"],
        "additionalProperties": False,
    },
)
def microplans_bulk_create_status(user, *, task_id):
    from celery.result import AsyncResult

    result = AsyncResult(str(task_id))
    state = result.state
    info = result.info if isinstance(result.info, dict) else {}
    if state == "PENDING":
        return {"state": "queued", "results": [], "created": 0, "total": 0, "run_id": None}
    if state in ("RECEIVED", "STARTED", "PROGRESS"):
        return {
            "state": "running",
            "results": info.get("results", []),
            "created": info.get("created", 0),
            "total": info.get("total", 0),
            "run_id": info.get("run_id"),
        }
    if state == "SUCCESS":
        payload = result.result if isinstance(result.result, dict) else {}
        return {
            "state": "completed",
            "results": payload.get("results", []),
            "created": payload.get("created", 0),
            "total": payload.get("total", 0),
            "run_id": payload.get("run_id"),
        }
    if state == "FAILURE":
        return {"state": "failed", "detail": "Bulk create failed. Check server logs.", "results": [], "run_id": None}
    return {"state": state.lower(), "results": [], "created": 0, "total": 0, "run_id": None}


def _reflect_config(cls) -> list[dict]:
    """Reflect a config dataclass into a list of field descriptors (name/type/default),
    layered with best-effort help text. The reflected name/type/default is the drift-
    proof part — a field added to the dataclass appears here with no change; help is an
    optional overlay keyed by field name."""
    help_text = _CONFIG_FIELD_HELP.get(cls.__name__, {})
    out = []
    for f in dataclasses.fields(cls):
        if f.default is not dataclasses.MISSING:
            default = f.default
        elif f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            default = f.default_factory()  # type: ignore[misc]
        else:
            default = None
        out.append(
            {
                "name": f.name,
                "type": str(f.type),
                "default": default,
                "help": help_text.get(f.name, ""),
            }
        )
    return out


# Best-effort per-field help (overlay only — reflection is the source of truth for the
# field set). Keep in sync opportunistically; a missing entry just shows an empty help.
_CONFIG_FIELD_HELP = {
    "CoverageConfig": {
        "cell_size_m": "Square work-area cell edge in metres (one cell per occupied grid square).",
        "min_confidence": "Drop building footprints below this Overture confidence (None = keep all sources).",
        "area_min_m2": "Exclude footprints smaller than this (m²) — degenerate OSM artifacts.",
        "area_max_m2": "Exclude footprints larger than this (m²) — OSM landmass artifacts.",
        "sources": "Restrict to these footprint sources (e.g. Google/OSM/Microsoft); None = every source.",
        "min_cell_roof_area_m2": "Post-gridding: drop cells whose total roof area is below this (0 = off).",
        "exclude_isolated_singletons": "Drop 1-building cells beyond isolation_dist_m from a multi-building cell.",
        "isolation_dist_m": "Distance threshold (m) for the isolated-singleton exclusion.",
        "population": "Ward population for population-weighted expected_visit_count (None = visits==building_count).",
    },
    "GroupingConfig": {
        "strategy": "Phase-1 cell→group bucketing: 'bfs_adjacency' (Connect-GIS parity) or 'bbox'.",
        "target_size": "bbox: approx cells per super-grid bucket.",
        "max_buildings": "bfs_adjacency: cap on buildings per contiguous group.",
        "buffer_distance_m": "bfs_adjacency: adjacency buffer in metres.",
    },
    "AssignmentConfig": {
        "strategy": "Phase-2 CHW assignment: 'minimax_spread' (Neal Lesh), 'round_robin', or 'manual'.",
        "workers": "CHW/worker ids to assign groups across (required for round_robin/minimax_spread).",
        "restarts": "minimax_spread: greedy restart count.",
        "seed": "minimax_spread: RNG seed for reproducibility.",
    },
}


@register(
    name="microplans_coverage_param_schema",
    description=(
        "Describe the microplans plan-creation parameter surface by reflecting the "
        "CoverageConfig (coverage_config), GroupingConfig (grouping), and AssignmentConfig "
        "(assignment) dataclasses — each field's name, type, default, and a short help. "
        "Read-only, no program needed. Use this to discover every knob microplans_bulk_"
        "create_plans accepts (and see new ones as features are added) without reading code."
    ),
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
def microplans_coverage_param_schema(user):
    from connect_labs.microplans.core.assignment import VALID_STRATEGIES as ASSIGN_STRATEGIES
    from connect_labs.microplans.core.assignment import AssignmentConfig
    from connect_labs.microplans.core.grouping import VALID_STRATEGIES as GROUP_STRATEGIES
    from connect_labs.microplans.core.grouping import GroupingConfig
    from connect_labs.microplans.coverage.frame import CoverageConfig

    return {
        "coverage_config": {
            "applies_to": "mode=coverage",
            "fields": _reflect_config(CoverageConfig),
        },
        "grouping": {
            "applies_to": "Phase-1 cell bucketing (both modes)",
            "strategies": list(GROUP_STRATEGIES),
            "fields": _reflect_config(GroupingConfig),
        },
        "assignment": {
            "applies_to": "Phase-2 CHW assignment (applied post-create via regroup/reassign)",
            "strategies": list(ASSIGN_STRATEGIES),
            "fields": _reflect_config(AssignmentConfig),
        },
    }
