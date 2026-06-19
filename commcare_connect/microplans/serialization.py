"""Serialization + derivation helpers for microplans plans.

Pure functions that turn a ``PlanRecord`` into the shapes the program-scoped
endpoints return (full plan JSON, compact workspace row) or derive geometry from
it. Kept out of ``views.py`` so they're testable without an HTTP request and
reused across the views that render/serialize plans.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Coordinate precision for serialized work-area geometry. 6 decimals ≈ 0.11 m at
# the equator — far finer than building-footprint accuracy, imperceptible on the
# map — but trims ~⅓ off a large plan's JSON (a 1,100-area plan is >1 MB of
# mostly-redundant float digits). We round the *serialized* copy only; the stored
# plan and server-side KPIs keep full precision.
COORD_PRECISION = 6


def _round_coords(coords, ndigits: int):
    """Recursively round the leaf floats of a GeoJSON coordinate array."""
    if isinstance(coords, (int, float)):
        return round(coords, ndigits)
    return [_round_coords(c, ndigits) for c in coords]


def slim_work_areas(work_areas, ndigits: int = COORD_PRECISION) -> list:
    """Return work areas with geometry coordinates rounded to ``ndigits`` decimals.

    Shrinks the response without changing what's drawn; does not mutate the input
    (each area is shallow-copied only when it has roundable geometry).
    """
    slimmed = []
    for wa in work_areas:
        geom = wa.get("geometry") if isinstance(wa, dict) else None
        if isinstance(geom, dict) and "coordinates" in geom:
            wa = {**wa, "geometry": {**geom, "coordinates": _round_coords(geom["coordinates"], ndigits)}}
        slimmed.append(wa)
    return slimmed


def plan_to_json(plan) -> dict:
    """Serialize a plan for the review UI: work areas + headline summary.

    Includes the most recent grouping + assignment configs so the review
    sidebar can pre-fill its form controls with whatever produced the current
    layout — the LLO sees ``what was used`` without a separate config header.
    """
    from commcare_connect.microplans.core import plan as plan_lib

    work_areas = plan.work_areas
    return {
        "status": "ok",
        "plan_id": plan.id,
        "mode": plan.mode,
        # Optimistic-concurrency token the UI echoes back on saves (409 if stale).
        "revision": plan.data.get("revision", 0),
        # Round geometry for the wire; KPIs/summary stay on the full-precision source.
        "work_areas": slim_work_areas(work_areas),
        "summary": plan_lib.summarize(work_areas),
        "kpis": plan_lib.plan_kpis(work_areas, input_areas=plan.data.get("input_areas") or []),
        "grouping": plan.data.get("grouping") or {},
        "assignment": plan.data.get("assignment") or {},
        # Per-area visit targets + the populations available to seed them (#9/#15).
        "area_targets": plan.data.get("area_targets") or {},
        "area_populations": plan.data.get("area_populations") or {},
        # Sampling overlay so the review page redraws the ward boundaries + selected
        # PSU hulls + Sample details on load — making a reopened plan render exactly
        # like the just-created one. Empty/absent for coverage plans.
        "input_areas": plan.data.get("input_areas") or [],
        "psu_hulls": plan.data.get("psu_hulls") or {"type": "FeatureCollection", "features": []},
        "sampling_stats": plan.data.get("sampling_stats") or [],
    }


def plan_summary_row(plan) -> dict:
    """Compact per-plan row for the workspace (status, region, headline KPIs)."""
    from commcare_connect.microplans.core import plan as plan_lib

    input_areas = plan.data.get("input_areas") or []
    k = plan_lib.plan_kpis(plan.work_areas, input_areas=input_areas)
    # Travel/balance KPIs are only meaningful once areas are split across workers.
    # Pre-assignment everything collapses to one territory, so flag it so the UI can
    # show the area count instead of a misleading "1 worker / whole-region travel".
    assigned = k["dimension"] == "worker"
    return {
        "plan_id": plan.id,
        "name": plan.name or f"Plan {plan.id}",
        "region": plan.region,
        "mode": plan.mode,
        "status": plan.status,
        "status_label": plan_lib.PLAN_STATUS_LABELS.get(plan.status, plan.status),
        "opportunity_id": plan.data.get("opportunity_id"),
        "assigned": assigned,
        "work_areas": len(plan.work_areas),
        # Count of named coverage wards (input_areas) this plan defines — e.g. a
        # two-arm study plan has 2 (intervention + comparison). The workspace sums
        # this across a study group's member plans so the group card can report the
        # real ward count instead of the number of member plans.
        "ward_count": len([a for a in input_areas if isinstance(a, dict) and a.get("name")]),
        "max_spread_km": k["plan"]["max_spread_km"],
        "coverage_pct": k["coverage_pct"],
        "excluded": k["excluded"]["count"],
        "territory_count": k["plan"]["territory_count"],
        "created_at": plan.created_at,
    }


def plan_lookup_geometry(plan):
    """Best geometry to use when re-querying footprints for a plan.

    Order of preference:
      1. The plan's stored ``input_areas`` (the ward/draw/pin payload from setup;
         already PG-cached as a whole from generation → instant hit).
      2. The union of cell geometries (works but a different cache hash → cold
         miss the first time per plan).
    """
    from shapely.ops import unary_union

    from commcare_connect.microplans.core.area_input import resolve_area

    inputs = plan.data.get("input_areas") or []
    if inputs:
        try:
            return unary_union([resolve_area(a) for a in inputs])
        except Exception:  # noqa: BLE001
            logger.exception("plan footprints: input_areas resolve failed; falling back to cells")

    from shapely.geometry import shape

    geoms = []
    for w in plan.work_areas:
        g = w.get("geometry")
        if not g:
            continue
        try:
            geoms.append(shape(g))
        except Exception:  # noqa: BLE001
            continue
    return unary_union(geoms) if geoms else None
