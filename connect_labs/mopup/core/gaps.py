"""Planning-gap detection (design brief §7): buildings inside a ward that no
existing work area covers, gridded into new candidate WorkArea features.

Computed at Phase 2's Step 2 (`mopup.tasks.preview_planning_gaps`, only
reachable once the run is locked), not at Phase 3 hand-off time — an earlier
version computed this at hand-off, but that meant the reviewer never saw the
gap-fill work areas before they landed in the final plan, and a real
`include_planning_gaps=True` hand-off measured well over a minute
synchronously (fetching + diffing thousands of buildings). Step 2's own
Recompute button now does that work up front, with the result stored on the
run (`MopupRunRecord.planning_gap_features`) and carried forward as-is by
`core.handoff.create_plan_from_locked_run` — no recomputation at hand-off.

Deliberately not built on top of `microplans.coverage.frame.generate_coverage_frame`
— that function always calls `fetch_buildings` itself with no seam to inject
a pre-filtered building set, and adding one would be a microplans-file change
to a widely-used function for exactly one caller. This module composes the
same lower-level primitives (`fetch_buildings`, `clustering.grid_clusters`)
directly instead, keeping microplans untouched, per this app's zero-footprint
architecture constraint (see `connect_labs/mopup/models.py`'s module
docstring).
"""

from __future__ import annotations

import logging

import pandas as pd
from django.http import HttpRequest
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.prepared import prep

from connect_labs.microplans.core.footprints import fetch_buildings

logger = logging.getLogger(__name__)


def buildings_not_covered(
    ward_boundary: BaseGeometry,
    existing_wa_boundaries: list[dict],
    *,
    min_confidence: float | None = None,
    sources: list[str] | None = None,
) -> pd.DataFrame:
    """Buildings inside `ward_boundary` whose centroid does NOT fall inside
    the union of `existing_wa_boundaries` — the "never covered by any
    existing work area" remainder. Returns the same DataFrame shape
    `microplans.core.footprints.fetch_buildings` returns
    (`lon`/`lat`/`area_m2`/`confidence`/`dataset`), ready to feed straight
    into `microplans.core.clustering.grid_clusters`.

    ``existing_wa_boundaries``: GeoJSON geometries (dicts) of EVERY existing
    work area in the ward — not just the locked candidates, since a
    candidate being revisited is itself an existing WA and its footprint is
    already covered.

    Raises `ValueError` (the same `MAX_AREA_KM2` guard) via `fetch_buildings`.
    """
    buildings = fetch_buildings(ward_boundary, min_confidence=min_confidence, sources=sources)
    if not existing_wa_boundaries or buildings.empty:
        return buildings

    covered = prep(unary_union([shape(g) for g in existing_wa_boundaries]))
    keep = [not covered.contains(Point(lon, lat)) for lon, lat in zip(buildings["lon"], buildings["lat"])]
    return buildings[pd.Series(keep, index=buildings.index)]


def planning_gap_features(
    ward: str,
    lga: str,
    state: str,
    area_id: str,
    ward_boundary: BaseGeometry,
    existing_wa_boundaries: list[dict],
    cell_size_m: float = 100.0,
    *,
    min_confidence: float | None = None,
    sources: list[str] | None = None,
    min_buildings_per_cell: int = 1,
    visits_per_building: float | None = None,
) -> list[dict]:
    """Grid the buildings-minus-existing-footprint remainder for one ward
    into the SAME Feature shape `generate_coverage_frame` produces (`cluster`,
    `area_id`, `ward`/`lga`/`state`, `building_count`, `expected_visit_count`,
    `cell_size_m` — matching `microplans.coverage.frame.generate_coverage_frame`'s
    Feature shape), tagged to share `area_id` with that ward's carry-forward
    features (`core.areas.carry_forward_features`) so
    `microplans.core.plan.recompute_area_visits` pools both into one
    target-spread group for the ward.

    `min_confidence`/`sources` are Phase 2 Step 2's building-source controls,
    passed straight through to `buildings_not_covered`/`fetch_buildings`.
    `min_buildings_per_cell` drops any occupied grid cell with fewer
    buildings than this before it becomes a candidate work area — `grid_clusters`
    itself has no such floor (every occupied cell is a cluster).
    `visits_per_building`, if given, sets each feature's `expected_visit_count`
    to `round(visits_per_building * building_count)` — a DISPLAY estimate for
    Phase 2's tables (this ward's own observed visits-per-building rate,
    since a gap-fill cell has no visit history of its own); the real plan's
    target still comes from `core.areas.ward_children_per_building`,
    unrelated to this estimate. `None` (the default) keeps the original
    building-count placeholder.
    """
    from connect_labs.microplans.core import clustering

    remainder = buildings_not_covered(
        ward_boundary, existing_wa_boundaries, min_confidence=min_confidence, sources=sources
    )
    out = clustering.grid_clusters(remainder, cell_size_m=cell_size_m)
    features = []
    for _, row in out.psu_frame.iterrows():
        n_b = int(row["n_buildings"])
        if n_b < min_buildings_per_cell:
            continue
        expected_visit_count = round(visits_per_building * n_b) if visits_per_building is not None else n_b
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [row["cell_polygon"]]},
                "properties": {
                    # "-gap-" namespacing keeps this from ever colliding with
                    # a carry-forward feature's "{area_id}-existing-{wa_id}"
                    # cluster name in the same ward.
                    "cluster": f"{area_id}-gap-{row['cluster']}",
                    "area_id": area_id,
                    "ward": ward,
                    "lga": lga,
                    "state": state,
                    "building_count": n_b,
                    "expected_visit_count": expected_visit_count,
                    "cell_size_m": float(cell_size_m),
                },
            }
        )
    return features


def work_area_boundaries_for_ward(
    pipeline,
    opportunity_id: int,
    ward: str,
    lga: str,
    state: str,
    *,
    request: HttpRequest | None = None,
) -> list[dict]:
    """Boundary GeoJSON for EVERY existing work area in `ward` (not just
    locked candidates) — joins two things that already exist separately:
    `core.areas.work_area_ids_for_ward` (WA case ids in a ward) against
    `core.geometry.fetch_work_area_geometry`'s output (boundary GeoJSON per
    WA case id). This is exactly what `buildings_not_covered` needs to
    exclude buildings already inside a drawn work area.

    Pass one of `request` or `pipeline` (mirrors every other function in this
    app's `core/` package taking either).
    """
    from connect_labs.labs.analysis.pipeline import AnalysisPipeline
    from connect_labs.mopup.core.areas import work_area_ids_for_ward
    from connect_labs.mopup.core.geometry import fetch_work_area_geometry

    if pipeline is None:
        if request is None:
            raise ValueError("work_area_boundaries_for_ward requires either `request` or `pipeline`")
        pipeline = AnalysisPipeline(request=request)

    wa_ids = work_area_ids_for_ward(pipeline, opportunity_id, ward, lga, state)
    if not wa_ids:
        return []

    geometry = fetch_work_area_geometry(opportunity_id, request=request, pipeline=pipeline)
    return [geometry[wa_id]["boundary"] for wa_id in wa_ids if geometry.get(wa_id, {}).get("boundary")]


def ward_visits_per_building(all_rows: list[dict], ward: str) -> float:
    """This ward's own observed (approved HSD visits) ÷ (buildings), across
    every CONCLUDED work area Phase 2 evaluated for it — the best available
    stand-in for a gap-fill cell's expected visit count, since a cell that's
    never been visited has no history of its own. Computed per ward (never
    pooled across wards), so a multi-ward mop-up run applies each ward's own
    rate to its own gap-fill cells. Returns 0.0 (not a ZeroDivisionError) when
    there's no eligible data for this ward — a genuine zero is a valid
    answer here, matching `core.areas.ward_children_per_building`'s same
    convention.

    Deliberately NOT `ward_children_per_building` (registered-CHILDREN per
    building, from CommCare HQ case data) — that formula still drives the
    real plan's `area_targets` at hand-off, unchanged. This is a cheaper,
    Phase-2-local estimate (observed VISITS per building, from data Phase 2
    already evaluated) purely for Step 2's preview tables/map, so the
    reviewer isn't staring at an unexplained 0 before locking."""
    from connect_labs.mopup.core.indicators import _CONCLUDED_STATUSES

    eligible = [r for r in all_rows if r.get("ward") == ward and r.get("status") in _CONCLUDED_STATUSES]
    buildings = sum(r.get("building_count", 0) or 0 for r in eligible)
    if not buildings:
        return 0.0
    visits = sum(r.get("approved_hsd_count", 0) or 0 for r in eligible)
    return visits / buildings
