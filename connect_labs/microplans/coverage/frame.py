"""Coverage microplan generator: small uniform grid cells → cell-as-WorkArea.

Coverage mode: tile the area into uniform N×N meter grid cells (e.g. 100m × 100m).
Each occupied cell — one that contains at least one building — becomes a WorkArea.
Boundary = the cell box itself (a square in projected UTM, reprojected to lat/lon).
expected_visit_count = building_count.

This matches Connect prod's WorkArea model: small operationally-meaningful squares
that aggregate into larger CHW territories (WorkAreaGroup). We *do not* use convex
hulls of building clusters — those produce arbitrary, variable-size polygons that
don't match how an FLW thinks about their territory ("this square is mine").
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from connect_labs.microplans.core import clustering
from connect_labs.microplans.core.area_input import resolve_area
from connect_labs.microplans.core.filters import (
    CellFilterConfig,
    FilterConfig,
    apply_cell_filters,
    apply_frame_filters,
)
from connect_labs.microplans.core.footprints import fetch_buildings

# Upper bound on work areas a single coverage plan may produce. Far above real
# use (the largest live plan is ~1,100 areas) — this is a guardrail so a tiny
# cell size on a huge area can't generate a 50k-cell, multi-MB plan that bloats
# every plan response and is unreviewable anyway. Exceeding it is a user error
# with an actionable fix (bigger cells / split the area), surfaced via the
# preview's error envelope.
MAX_WORK_AREAS = 8000


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


@dataclass
class CoverageConfig:
    cell_size_m: float = 100.0  # square cell edge length (meters)
    # coverage wants completeness → no confidence gate by default (include MS/OSM roofs)
    min_confidence: float | None = None
    # near-pass-through defaults: coverage covers every household, so we keep
    # almost everything. Only exclude truly degenerate (<1m²) or super-massive
    # (>10000m²) footprints which are typically OSM landmass artifacts.
    area_min_m2: float = 1.0
    area_max_m2: float = 10000.0
    # coverage wants completeness → all providers by default (None = every source).
    sources: list[str] | None = None
    # cell-level exclusion filters (post-gridding). Defaults = off → unchanged behaviour.
    min_cell_roof_area_m2: float = 0.0
    exclude_isolated_singletons: bool = False
    isolation_dist_m: float = 150.0
    # population-weighted expected-visit calc. None → expected_visit_count = building_count (legacy).
    # When set, expected_visit_count = ceil(cell_buildings * population / retained_buildings), min 1,
    # where retained_buildings is the total over cells surviving the exclusion filters.
    population: float | None = None

    @classmethod
    def from_payload(cls, d: dict) -> CoverageConfig:
        conf = d.get("min_confidence")
        src = d.get("sources")
        pop = d.get("population")
        return cls(
            cell_size_m=_clamp(float(d.get("cell_size_m", 100)), 10.0, 100000.0),
            min_confidence=(None if conf in (None, "", 0) else _clamp(float(conf), 0.0, 1.0)),
            area_min_m2=_clamp(float(d.get("area_min_m2", 1)), 0.0, 1e6),
            area_max_m2=_clamp(float(d.get("area_max_m2", 10000)), 1.0, 1e7),
            sources=([str(s) for s in src] if isinstance(src, list) and src else None),
            min_cell_roof_area_m2=_clamp(float(d.get("min_cell_roof_area_m2", 0) or 0), 0.0, 1e7),
            exclude_isolated_singletons=bool(d.get("exclude_isolated_singletons", False)),
            isolation_dist_m=_clamp(float(d.get("isolation_dist_m", 150) or 150), 0.0, 1e6),
            population=(None if pop in (None, "", 0) else float(pop)),
        )


@dataclass
class CoverageFrameResult:
    areas_geojson: dict  # grid cells (the WorkAreas), each w/ building_count + expected_visit_count
    stats: list[dict] = field(default_factory=list)


def _area_meta(a: dict, idx: int) -> dict:
    """Per-area identity for tagging work areas. Boundary picks carry ward/lga/state;
    drawn/custom shapes fall back to a numeric ward name (area_1, area_2, …)."""
    ward = str(a.get("ward") or a.get("name") or "").strip() or f"area_{idx + 1}"
    return {
        "area_id": idx,
        "ward": ward,
        "lga": str(a.get("lga") or "").strip(),
        "state": str(a.get("state") or "").strip(),
    }


def generate_coverage_frame(areas: list[dict], config: CoverageConfig) -> CoverageFrameResult:
    """areas: [{"geometry": <GeoJSON>, "ward"/"lga"/"state": ...}, ...]. Each selected
    area is fetched + tiled into `cell_size_m` grid cells INDEPENDENTLY (so two small
    wards far apart aren't treated as one giant bounding box), and every occupied cell
    becomes a WorkArea tagged with its source ward/LGA/state. Coverage has no arms.
    """
    import numpy as np

    # Pass 1: per-area fetch → filter → grid. Per-area fetch keeps each ward's bounding
    # box small (fixes the false "area too large" on scattered wards) and lets us
    # attribute every work area to its ward.
    grids = []  # (meta, filtered, out)
    fetched_total = after_total = 0
    raw_cells = 0
    for idx, a in enumerate(areas):
        geom = resolve_area(a)
        meta = _area_meta(a, idx)
        buildings = fetch_buildings(geom, min_confidence=config.min_confidence, sources=config.sources)
        filtered = apply_frame_filters(
            buildings, FilterConfig(area_min_m2=config.area_min_m2, area_max_m2=config.area_max_m2)
        )
        out = clustering.grid_clusters(filtered.buildings, cell_size_m=config.cell_size_m)
        grids.append((meta, filtered, out))
        fetched_total += filtered.n_in
        after_total += filtered.n_out
        raw_cells += len(out.psu_frame)

    if raw_cells > MAX_WORK_AREAS:
        raise ValueError(
            f"This selection at {config.cell_size_m:.0f} m work areas produces {raw_cells:,} work areas "
            f"(limit {MAX_WORK_AREAS:,}). Increase the work-area size, or split into separate plans."
        )

    # Pass 2: cell-level exclusion filters, per area.
    per_area = []  # (meta, frame_df)
    cells_before = removed_small = removed_isolated = 0
    for meta, _filtered, out in grids:
        cell_result = apply_cell_filters(
            out.buildings,
            out.psu_frame,
            CellFilterConfig(
                min_cell_roof_area_m2=config.min_cell_roof_area_m2,
                exclude_isolated_singletons=config.exclude_isolated_singletons,
                isolation_dist_m=config.isolation_dist_m,
            ),
        )
        per_area.append((meta, cell_result.psu_frame))
        cells_before += cell_result.n_in
        removed_small += cell_result.removed_small_area
        removed_isolated += cell_result.removed_isolated

    total_cells = sum(len(f) for _, f in per_area)

    retained_buildings = int(sum(int(f["n_buildings"].sum()) for _, f in per_area if len(f)))
    # people-per-building over the RETAINED set; None → legacy EVC = building_count.
    # (Population/visits move to a per-area post-creation step; kept here for parity.)
    ppb = (config.population / retained_buildings) if (config.population and retained_buildings) else None

    features: list[dict] = []
    for meta, frame in per_area:
        for _, row in frame.iterrows():
            cell_polygon = row["cell_polygon"]  # [[lon, lat], ...] closed ring
            n_b = int(row["n_buildings"])
            if ppb is not None:
                expected_visits = max(1, math.ceil(n_b * ppb))
                target_population = round(n_b * ppb)
            else:
                expected_visits = n_b
                target_population = None
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [cell_polygon]},
                    "properties": {
                        # Namespace the cluster by area so ids stay unique across wards.
                        "cluster": f"{meta['area_id']}-{row['cluster']}",
                        "area_id": meta["area_id"],
                        "ward": meta["ward"],
                        "lga": meta["lga"],
                        "state": meta["state"],
                        "building_count": n_b,
                        "expected_visit_count": expected_visits,
                        "target_population": target_population,
                        "roof_area_m2": round(float(row["roof_area_m2"]), 1),
                        "dist_to_multi_m": round(float(row["dist_to_multi_m"]), 1),
                        "cell_size_m": float(config.cell_size_m),
                    },
                }
            )

    all_sizes = np.array([int(r["n_buildings"]) for _, fr in per_area for _, r in fr.iterrows()] or [0])
    # Per-ward breakdown for the per-area metrics/summary.
    per_ward = [
        {
            "ward": m["ward"],
            "lga": m["lga"],
            "state": m["state"],
            "work_areas": len(fr),
            "buildings": int(fr["n_buildings"].sum()) if len(fr) else 0,
        }
        for m, fr in per_area
    ]
    stats = [
        {
            "fetched": fetched_total,
            "after_filters": after_total,
            "work_areas": total_cells,
            "cells_before_exclusions": cells_before,
            "removed_small_area": removed_small,
            "removed_isolated": removed_isolated,
            "retained_buildings": retained_buildings,
            "population": config.population,
            "people_per_building": round(ppb, 4) if ppb is not None else None,
            "cell_size_m": float(config.cell_size_m),
            "min_buildings": int(all_sizes.min()),
            "median_buildings": int(np.median(all_sizes)),
            "max_buildings": int(all_sizes.max()),
            "per_area": per_ward,
        }
    ]
    return CoverageFrameResult(areas_geojson={"type": "FeatureCollection", "features": features}, stats=stats)
