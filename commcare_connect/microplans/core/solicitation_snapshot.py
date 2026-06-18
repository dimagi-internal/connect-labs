"""Build a self-contained snapshot of a plan or plan group for a solicitation.

The single seam between microplans and solicitations: solicitations imports
*only* this function to read live micro-plan data. Everything it returns is
plain JSON-able data the solicitation stores in its own ``data`` (frozen by
design — no live re-read, see the design spec).
"""
from __future__ import annotations


def _plan_entry(plan) -> dict:
    input_areas = (plan.data or {}).get("input_areas", []) or []
    wards = [a.get("name", "") for a in input_areas if a.get("name")]
    arms = sorted({a.get("arm") for a in input_areas if a.get("arm")})
    # Coverage boundary polygons (ward-level), carried into the snapshot so the
    # solicitation can draw a real map without re-reading the live plan. Geometry
    # is GeoJSON (Polygon/MultiPolygon) stored inline on each input_area.
    boundaries = []
    for a in input_areas:
        geom = a.get("geometry")
        if not geom:
            continue
        b = {"name": a.get("name", ""), "geometry": geom}
        if a.get("arm"):
            b["arm"] = a["arm"]
        boundaries.append(b)
    entry = {
        "plan_id": int(plan.id),
        "name": plan.name or f"Plan #{plan.id}",
        "region": plan.region or "",
        "wards": wards,
        "work_area_count": len(plan.work_areas or []),
    }
    if arms:
        entry["arms"] = arms
    if boundaries:
        entry["boundaries"] = boundaries
    return entry


def build_plan_snapshot(da, *, group_id: int | None = None, plan_id: int | None = None) -> dict:
    if (group_id is None) == (plan_id is None):
        raise ValueError("pass exactly one of group_id or plan_id")

    if group_id is not None:
        group = da.get_group(group_id)
        by_id = {p.id: p for p in da.list_plans()}
        ordered = [by_id[pid] for pid in group.plan_ids if pid in by_id]
        plans = [_plan_entry(p) for p in ordered]
        title = f"Solicitation for {group.name}".strip()
        scope = f'Coverage areas drawn from plan group "{group.name}".'
    else:
        plan = da.get_plan(plan_id)
        plans = [_plan_entry(plan)]
        region = f" ({plan.region})" if plan.region else ""
        title = f"Solicitation for {plan.name}{region}".strip()
        scope = f'Coverage area drawn from plan "{plan.name}".'

    return {
        "plans": plans,
        "source_program_id": int(da.program_id),
        "source_group_id": int(group_id) if group_id is not None else None,
        "source_plan_ids": [p["plan_id"] for p in plans],
        "suggested_title": title,
        "suggested_scope": scope,
    }
