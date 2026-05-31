"""Serialization + derivation helpers for microplans plans.

Pure functions that turn a ``PlanRecord`` into the shapes the program-scoped
endpoints return (full plan JSON, compact workspace row) or derive geometry from
it. Kept out of ``views.py`` so they're testable without an HTTP request and
reused across the views that render/serialize plans.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def plan_to_json(plan) -> dict:
    """Serialize a plan for the review UI: work areas + headline summary.

    Includes the most recent grouping + assignment configs so the review
    sidebar can pre-fill its form controls with whatever produced the current
    layout — the LLO sees ``what was used`` without a separate config header.
    """
    from commcare_connect.microplans.core import plan as plan_lib

    return {
        "status": "ok",
        "plan_id": plan.id,
        "mode": plan.mode,
        "work_areas": plan.work_areas,
        "summary": plan_lib.summarize(plan.work_areas),
        "kpis": plan_lib.plan_kpis(plan.work_areas),
        "grouping": plan.data.get("grouping") or {},
        "assignment": plan.data.get("assignment") or {},
    }


def plan_summary_row(plan) -> dict:
    """Compact per-plan row for the workspace (status, region, headline KPIs)."""
    from commcare_connect.microplans.core import plan as plan_lib

    k = plan_lib.plan_kpis(plan.work_areas)
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
