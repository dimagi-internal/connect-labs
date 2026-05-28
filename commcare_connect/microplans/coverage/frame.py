"""Coverage microplan generator: balanced clusters → cluster-as-WorkArea.

The coverage mode (what connect-gis does): divide an area into balanced clusters
so FLWs visit *every* household, with even workloads. Each cluster becomes one
WorkArea — boundary = the cluster hull, expected_visit_count = building_count.

Shares the core footprint fetch + filters + clustering with sampling mode; the
difference is balanced_kmeans (even workloads) + assign-the-whole-cluster instead
of PPS-sample-a-subset.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import MultiPoint, mapping
from shapely.ops import unary_union

from commcare_connect.microplans.core import clustering
from commcare_connect.microplans.core.area_input import resolve_area
from commcare_connect.microplans.core.filters import FilterConfig, apply_frame_filters
from commcare_connect.microplans.core.footprints import fetch_buildings


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


@dataclass
class CoverageConfig:
    strategy: str = "balanced"  # "balanced" (even workloads) | "grid" (square cells)
    buildings_per_cluster: int = 100  # target workload per FLW area (balanced strategy)
    n_clusters: int | None = None  # alternative to buildings_per_cluster (balanced strategy)
    balance_tolerance: float = 0.1
    cell_size_m: float = 200.0  # grid strategy: square cell edge
    # coverage wants completeness → no confidence gate by default (include MS/OSM roofs)
    min_confidence: float | None = None
    area_min_m2: float = 9.0
    area_max_m2: float = 330.0

    @classmethod
    def from_payload(cls, d: dict) -> "CoverageConfig":
        conf = d.get("min_confidence")
        nc = d.get("n_clusters")
        strategy = d.get("strategy", "balanced")
        return cls(
            strategy="grid" if strategy == "grid" else "balanced",
            buildings_per_cluster=_clamp(int(d.get("buildings_per_cluster", 100)), 1, 100000),
            n_clusters=(_clamp(int(nc), 1, 5000) if nc else None),
            balance_tolerance=_clamp(float(d.get("balance_tolerance", 0.1)), 0.0, 1.0),
            cell_size_m=_clamp(float(d.get("cell_size_m", 200)), 10.0, 100000.0),
            min_confidence=(None if conf in (None, "", 0) else _clamp(float(conf), 0.0, 1.0)),
            area_min_m2=_clamp(float(d.get("area_min_m2", 9)), 0.0, 1e6),
            area_max_m2=_clamp(float(d.get("area_max_m2", 330)), 1.0, 1e7),
        )


@dataclass
class CoverageFrameResult:
    areas_geojson: dict  # cluster hulls (the WorkAreas), each w/ building_count + expected_visit_count
    stats: list[dict] = field(default_factory=list)


def generate_coverage_frame(areas: list[dict], config: CoverageConfig) -> CoverageFrameResult:
    """areas: [{"arm": ..., "geometry": <GeoJSON>}, ...]. Default arm: "coverage"."""
    by_arm: dict[str, list] = {}
    for a in areas:
        by_arm.setdefault(a.get("arm", "coverage"), []).append(resolve_area(a))

    features: list[dict] = []
    stats: list[dict] = []
    for arm, geoms in by_arm.items():
        area = unary_union(geoms)
        buildings = fetch_buildings(area, min_confidence=config.min_confidence)
        filtered = apply_frame_filters(
            buildings, FilterConfig(area_min_m2=config.area_min_m2, area_max_m2=config.area_max_m2)
        )
        if config.strategy == "grid":
            out = clustering.grid_clusters(filtered.buildings, cell_size_m=config.cell_size_m)
        else:
            out = clustering.balanced_kmeans(
                filtered.buildings,
                n_clusters=config.n_clusters,
                buildings_per_cluster=(None if config.n_clusters else config.buildings_per_cluster),
                balance_tolerance=config.balance_tolerance,
            )
        for _, row in out.psu_frame.iterrows():
            cluster = row["cluster"]
            pts = out.buildings[out.buildings["cluster"] == cluster]
            hull = MultiPoint(list(zip(pts["lon"], pts["lat"]))).convex_hull
            if hull.geom_type != "Polygon":
                hull = hull.buffer(0.0001)  # 1-2 points → tiny polygon
            features.append(
                {
                    "type": "Feature",
                    "geometry": mapping(hull),
                    "properties": {
                        "arm": arm,
                        "cluster": cluster,
                        "building_count": int(row["n_buildings"]),
                        "expected_visit_count": int(row["n_buildings"]),
                    },
                }
            )
        sizes = out.psu_frame["n_buildings"].to_numpy() if len(out.psu_frame) else np.array([0])
        stats.append(
            {
                "arm": arm,
                "strategy": config.strategy,
                "fetched": filtered.n_in,
                "after_filters": filtered.n_out,
                "work_areas": len(out.psu_frame),
                "min_buildings": int(sizes.min()),
                "median_buildings": int(np.median(sizes)),
                "max_buildings": int(sizes.max()),
            }
        )
    return CoverageFrameResult(areas_geojson={"type": "FeatureCollection", "features": features}, stats=stats)
