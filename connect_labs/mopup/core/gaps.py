"""Planning-gap detection (design brief §7): buildings inside a ward that no
existing work area covers, gridded into new candidate WorkArea features.

V1 scope (decision made when this was designed): computed directly at Phase 3
hand-off time, gated by a simple on/off toggle
(`handoff.create_plan_from_locked_run`'s `include_planning_gaps`), with no
separate Phase 2 review step — the brief's own §7 calls for gap candidates to
be reviewed/locked alongside execution-gap candidates in Phase 2, but that
requires every ward under review to get a live building fetch + full-ward
gridding + diff during Phase 2 evaluation (today pure Python, no network) plus
a second candidate-row type in the UI/map — a distinctly larger effort,
deferred. This module's functions are written so they're reusable unchanged
if that Phase 2 version is ever built (it would just call
`planning_gap_features` earlier and add a UI surface, not rewrite the
diffing itself).

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
) -> list[dict]:
    """Grid the buildings-minus-existing-footprint remainder for one ward
    into the SAME Feature shape `generate_coverage_frame` produces (`cluster`,
    `area_id`, `ward`/`lga`/`state`, `building_count`, `expected_visit_count`,
    `cell_size_m` — matching `microplans.coverage.frame.generate_coverage_frame`'s
    Feature shape), tagged to share `area_id` with that ward's carry-forward
    features (`core.areas.carry_forward_features`) so
    `microplans.core.plan.recompute_area_visits` pools both into one
    target-spread group for the ward.
    """
    from connect_labs.microplans.core import clustering

    remainder = buildings_not_covered(ward_boundary, existing_wa_boundaries)
    out = clustering.grid_clusters(remainder, cell_size_m=cell_size_m)
    features = []
    for _, row in out.psu_frame.iterrows():
        n_b = int(row["n_buildings"])
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
                    "expected_visit_count": n_b,
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
