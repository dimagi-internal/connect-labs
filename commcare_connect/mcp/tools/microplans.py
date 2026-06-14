"""MCP read tools for microplans programs.

A generic, read-only window onto a program's plans, study groups, and sampled
work areas — enough for an external caller (e.g. the synthetic survey generator)
to ground synthetic data on the real primary/alternate footprints without a
session. Works for labs-only synthetic programs (``program_id`` in the reserved
``>= LABS_ONLY_OPP_ID_FLOOR`` range = the backing opp id), which short-circuit to
the labs DB; real program reads (PKs below the floor) go through the production
LabsRecord API, which enforces membership.
"""

from __future__ import annotations

import logging

from ..connect_token import require_connect_token
from ..tool_registry import MCPToolError, register

logger = logging.getLogger(__name__)


def _is_labs_only(program_id) -> bool:
    """A labs-only program surfaces as a program id in the reserved
    ``>= LABS_ONLY_OPP_ID_FLOOR`` range (= its backing opp id)."""
    from commcare_connect.labs.synthetic.local_records_backend import is_labs_only_opportunity_id

    return is_labs_only_opportunity_id(int(program_id))


def _require_program_access(user, program_id: int) -> None:
    """Gate access. Labs-only programs are checked via the synthetic opp's labs
    visibility (the program id IS the backing opp id); real programs rely on the
    prod LabsRecord API membership check at read time, so we only require the
    caller to carry a Connect token."""
    if _is_labs_only(program_id):
        from .synthetic import _require_opportunity_access

        _require_opportunity_access(user, int(program_id))
    else:
        require_connect_token(user)


def _data_access(user, program_id: int):
    """Build a ProgramPlanDataAccess for ``program_id``. BaseDataAccess requires
    an access token to construct; for a labs-only program it is never used (the
    client short-circuits to the local backend), so a placeholder is fine when the
    user has no Connect token."""
    from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess

    if _is_labs_only(program_id):
        try:
            token = require_connect_token(user)
        except MCPToolError:
            token = "labs-local"  # unused for labs-only programs
    else:
        token = require_connect_token(user)
    return ProgramPlanDataAccess(int(program_id), user=user, access_token=token)


@register(
    name="microplans_list_plans",
    description=(
        "List a microplans program's plans and study/bundle groups. For each "
        "group, includes the per-plan arm assignment (arm_for). Read-only. "
        "Works for labs-only synthetic programs (negative program_id = "
        "-opportunity_id)."
    ),
    input_schema={
        "type": "object",
        "properties": {"program_id": {"type": "integer"}},
        "required": ["program_id"],
        "additionalProperties": False,
    },
)
def microplans_list_plans(user, *, program_id):
    _require_program_access(user, program_id)
    da = _data_access(user, program_id)
    try:
        plans = [
            {
                "id": p.id,
                "name": p.name,
                "region": p.region,
                "phase": p.phase,
                "status": p.status,
                "n_work_areas": len(p.work_areas),
            }
            for p in da.list_plans()
        ]
        groups = [
            {
                "group_id": g.id,
                "name": g.name,
                "kind": g.kind,
                "plan_ids": g.plan_ids,
                "arm_for": {str(pid): g.arm_for(pid) for pid in g.plan_ids},
            }
            for g in da.list_groups()
        ]
        return {"program_id": int(program_id), "plans": plans, "groups": groups}
    finally:
        da.close()


@register(
    name="microplans_plan_work_areas",
    description=(
        "Return a plan's sampled work areas in a compact form: per work area the "
        "centroid (lon/lat), sample_type (primary|alternate), cluster, "
        "order_in_cluster, stratum, and arm. Read-only. Use to ground synthetic "
        "survey data on the real sampled footprints."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "program_id": {"type": "integer"},
            "plan_id": {"type": "integer"},
        },
        "required": ["program_id", "plan_id"],
        "additionalProperties": False,
    },
)
def microplans_plan_work_areas(user, *, program_id, plan_id):
    _require_program_access(user, program_id)
    da = _data_access(user, program_id)
    try:
        p = da.get_plan(int(plan_id))
        was = []
        for w in p.work_areas:
            c = w.get("centroid") or [None, None]
            props = w.get("properties") or {}
            was.append(
                {
                    "wa_id": w.get("id"),
                    "lon": c[0],
                    "lat": c[1],
                    "sample_type": props.get("sample_type"),
                    "cluster": props.get("cluster"),
                    "order_in_cluster": props.get("order_in_cluster"),
                    "stratum": props.get("stratum"),
                    "arm": w.get("arm"),
                }
            )
        return {
            "program_id": int(program_id),
            "plan_id": int(plan_id),
            "name": p.name,
            "phase": p.phase,
            "n": len(was),
            "work_areas": was,
            # The arm-tagged study wards + the selected-PSU hulls, so a consumer (the
            # verified-monitoring generator) can draw the DESIGNED plan via PlanLayers.
            "input_areas": p.data.get("input_areas") or [],
            "psu_hulls": p.data.get("psu_hulls") or {"type": "FeatureCollection", "features": []},
        }
    finally:
        da.close()
