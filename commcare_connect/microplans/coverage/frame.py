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

from dataclasses import dataclass, field

from shapely.ops import unary_union

from commcare_connect.microplans.core import clustering
from commcare_connect.microplans.core.area_input import resolve_area
from commcare_connect.microplans.core.filters import FilterConfig, apply_frame_filters
from commcare_connect.microplans.core.footprints import fetch_buildings

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

    @classmethod
    def from_payload(cls, d: dict) -> CoverageConfig:
        conf = d.get("min_confidence")
        src = d.get("sources")
        return cls(
            cell_size_m=_clamp(float(d.get("cell_size_m", 100)), 10.0, 100000.0),
            min_confidence=(None if conf in (None, "", 0) else _clamp(float(conf), 0.0, 1.0)),
            area_min_m2=_clamp(float(d.get("area_min_m2", 1)), 0.0, 1e6),
            area_max_m2=_clamp(float(d.get("area_max_m2", 10000)), 1.0, 1e7),
            sources=([str(s) for s in src] if isinstance(src, list) and src else None),
        )


@dataclass
class CoverageFrameResult:
    areas_geojson: dict  # grid cells (the WorkAreas), each w/ building_count + expected_visit_count
    stats: list[dict] = field(default_factory=list)


def generate_coverage_frame(areas: list[dict], config: CoverageConfig) -> CoverageFrameResult:
    """areas: [{"geometry": <GeoJSON>}, ...]. The input areas are unioned and tiled
    into uniform `cell_size_m` grid cells; each cell containing ≥1 building becomes
    one WorkArea. Coverage has no arms — the whole input is one coverage zone.
    """
    import numpy as np

    geoms = [resolve_area(a) for a in areas]
    area = unary_union(geoms)
    buildings = fetch_buildings(area, min_confidence=config.min_confidence, sources=config.sources)
    filtered = apply_frame_filters(
        buildings, FilterConfig(area_min_m2=config.area_min_m2, area_max_m2=config.area_max_m2)
    )
    out = clustering.grid_clusters(filtered.buildings, cell_size_m=config.cell_size_m)

    n_cells = len(out.psu_frame)
    if n_cells > MAX_WORK_AREAS:
        raise ValueError(
            f"This area at {config.cell_size_m:.0f} m work areas produces {n_cells:,} work areas "
            f"(limit {MAX_WORK_AREAS:,}). Increase the work-area size, or split the area into separate plans."
        )

    features: list[dict] = []
    for _, row in out.psu_frame.iterrows():
        cell_polygon = row["cell_polygon"]  # [[lon, lat], ...] closed ring
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [cell_polygon]},
                "properties": {
                    "cluster": row["cluster"],
                    "building_count": int(row["n_buildings"]),
                    "expected_visit_count": int(row["n_buildings"]),
                    "cell_size_m": float(config.cell_size_m),
                },
            }
        )
    sizes = out.psu_frame["n_buildings"].to_numpy() if len(out.psu_frame) else np.array([0])
    stats = [
        {
            "fetched": filtered.n_in,
            "after_filters": filtered.n_out,
            "work_areas": len(out.psu_frame),
            "cell_size_m": float(config.cell_size_m),
            "min_buildings": int(sizes.min()),
            "median_buildings": int(np.median(sizes)),
            "max_buildings": int(sizes.max()),
        }
    ]
    return CoverageFrameResult(areas_geojson={"type": "FeatureCollection", "features": features}, stats=stats)
