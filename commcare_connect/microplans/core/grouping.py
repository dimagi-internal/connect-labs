"""Grouping algorithms — cells → work-area groups.

Phase 1 of the two-phase planning pipeline:

    cells  →  [GROUPING]  →  work-area groups  →  [ASSIGNMENT]  →  CHWs

Grouping is the operation of clumping work-area cells into CHW-walkable
territories. It is *spatially-aware* (cell geometry matters) and *load-aware*
(building counts matter), but is independent of *who* (which CHW) walks each
group — that's Phase 2 (assignment.py).

Strategies
----------
- ``bbox``           — quick row/col bucketing of centroids over the cells' bbox.
                       Fast, deterministic, no adjacency check, no building cap.
                       Useful as a placeholder + as a stress test.
- ``bfs_adjacency``  — port of Connect GIS's WorkAreaGrouper. BFS from each
                       unvisited seed cell, walking to adjacent neighbours
                       (shared boundary OR within ``buffer_distance_m``), greedy
                       admit until the cluster's building total would exceed
                       ``max_buildings``. Spatially contiguous + capped load.
                       Distances computed in EPSG:3857 (approximate metres).

The grouping operation mutates the ``work_area_group`` field on each work area
in place; nothing else changes (counts, CHW assignment, status all unaffected).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

# Connect GIS defaults (microplanning/clustering.py):
#   max_buildings=200, buffer_distance=100 m. We mirror these so a labs plan
#   regrouped with BFS lines up with what Connect's grouper would have done.
DEFAULT_TARGET_SIZE = 30
DEFAULT_MAX_BUILDINGS = 200
DEFAULT_BUFFER_DISTANCE_M = 100

VALID_STRATEGIES = ("bbox", "bfs_adjacency")


@dataclass
class GroupingConfig:
    strategy: str = "bfs_adjacency"  # default mirrors Connect GIS
    # bbox-only:
    target_size: int = DEFAULT_TARGET_SIZE  # ~cells per super-grid bucket
    # bfs_adjacency-only:
    max_buildings: int = DEFAULT_MAX_BUILDINGS
    buffer_distance_m: int = DEFAULT_BUFFER_DISTANCE_M

    @classmethod
    def from_payload(cls, d: dict) -> GroupingConfig:
        strategy = d.get("strategy", "bfs_adjacency")
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"unknown grouping strategy: {strategy!r} (one of {VALID_STRATEGIES})")
        return cls(
            strategy=strategy,
            target_size=max(1, int(d.get("target_size", DEFAULT_TARGET_SIZE))),
            max_buildings=max(1, int(d.get("max_buildings", DEFAULT_MAX_BUILDINGS))),
            buffer_distance_m=max(0, int(d.get("buffer_distance_m", DEFAULT_BUFFER_DISTANCE_M))),
        )


def group_work_areas(work_areas: list[dict], config: GroupingConfig) -> list[dict]:
    """Apply ``config.strategy`` to ``work_areas`` in place. Returns the same list."""
    if not work_areas:
        return work_areas
    if config.strategy == "bbox":
        return _bbox_bucket(work_areas, config.target_size)
    if config.strategy == "bfs_adjacency":
        return _bfs_adjacency(work_areas, config.max_buildings, config.buffer_distance_m)
    raise ValueError(f"unknown grouping strategy: {config.strategy!r}")


# ---- bbox bucket -------------------------------------------------------------


def _bbox_bucket(work_areas: list[dict], target_size: int) -> list[dict]:
    """Tile the cells' bbox into a sqrt(N/target_size)-side super-grid by centroid.
    Group label = ``g{row}-{col}+1`` row-major from south-west.

    No spatial adjacency check, no building-balance — purely positional.
    """
    n = len(work_areas)
    grid_n = max(1, math.ceil(math.sqrt(n / max(1, target_size))))
    centroids = [w["centroid"] for w in work_areas]
    lons = [c[0] for c in centroids]
    lats = [c[1] for c in centroids]
    lon_min, lon_max = min(lons), max(lons)
    lat_min, lat_max = min(lats), max(lats)
    lon_span = max(lon_max - lon_min, 1e-9)
    lat_span = max(lat_max - lat_min, 1e-9)
    for w, (lon, lat) in zip(work_areas, centroids):
        i = min(grid_n - 1, int((lat - lat_min) / lat_span * grid_n))
        j = min(grid_n - 1, int((lon - lon_min) / lon_span * grid_n))
        w["work_area_group"] = f"group-{i * grid_n + j + 1}"
    return work_areas


# ---- BFS adjacency (port of Connect GIS WorkAreaGrouper) ---------------------


def _bfs_adjacency(work_areas: list[dict], max_buildings: int, buffer_distance_m: int) -> list[dict]:
    """Cluster cells via BFS over a buffer-thickened adjacency graph, capped by
    total building count per cluster.

    Mirrors the production algorithm at
    ``dimagi/commcare-connect/commcare_connect/microplanning/clustering.py``.
    Adjacency check uses EPSG:3857 (Web Mercator) for an approximate metres-
    distance; this is what Connect uses and the same caveats apply (Mercator is
    not equidistant — buffer_distance_m is approximate near the equator).
    """
    from pyproj import Transformer
    from shapely import get_dimensions
    from shapely.errors import ShapelyError
    from shapely.geometry import shape
    from shapely.ops import transform as shp_transform
    from shapely.strtree import STRtree

    fwd = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    geoms_3857: dict[str, object] = {}
    skipped: list[dict] = []
    for w in work_areas:
        g = w.get("geometry")
        if not g:
            skipped.append(w)
            continue
        try:
            shp = shape(g)
            if shp.is_empty:
                raise ValueError("empty geometry")
            geoms_3857[w["id"]] = shp_transform(fwd.transform, shp)
        except (ShapelyError, ValueError, TypeError):
            skipped.append(w)

    if not geoms_3857:
        # No geometries to cluster — fall back to bbox so every cell still gets a label.
        return _bbox_bucket(work_areas, DEFAULT_TARGET_SIZE)

    # ---- adjacency graph ----
    wa_ids = list(geoms_3857.keys())
    geoms_list = [geoms_3857[wid] for wid in wa_ids]
    tree = STRtree(geoms_list)
    adjacency: dict[str, set] = {wid: set() for wid in wa_ids}
    distances: dict[tuple, float] = {}

    for wid, geom in geoms_3857.items():
        for idx in tree.query(geom.buffer(buffer_distance_m), predicate="intersects"):
            neighbour = wa_ids[idx]
            if neighbour == wid or neighbour in adjacency[wid]:
                continue
            other = geoms_3857[neighbour]
            shared = geom.intersection(other)
            dist = geom.distance(other)
            # Connect's rule: connected if they share an edge OR are within buffer.
            if get_dimensions(shared) >= 1 or dist <= buffer_distance_m:
                adjacency[wid].add(neighbour)
                adjacency[neighbour].add(wid)
                distances[_pair(wid, neighbour)] = dist
    # Order each cell's neighbours by distance, so BFS tends to walk the closest first.
    for wid in adjacency:
        adjacency[wid] = sorted(adjacency[wid], key=lambda n: distances.get(_pair(wid, n), float("inf")))

    # ---- deterministic seed order (mirror Connect: sort by centroid x asc, y desc) ----
    by_id = {w["id"]: w for w in work_areas if w["id"] in geoms_3857}
    sorted_ids = sorted(
        by_id.keys(),
        key=lambda wid: (by_id[wid]["centroid"][0], -by_id[wid]["centroid"][1]),
    )

    # ---- BFS clusters ----
    unvisited = set(by_id.keys())
    clusters: list[list[str]] = []
    for seed in sorted_ids:
        if seed not in unvisited:
            continue
        cluster = _bfs_single_cluster(seed, unvisited, adjacency, by_id, max_buildings)
        if not cluster:
            # A single oversized cell still gets its own group (Connect's behaviour).
            cluster = [seed]
            unvisited.discard(seed)
        clusters.append(cluster)

    # ---- assign labels ("group-N", row-major equivalent: order matches BFS seeding) ----
    for i, cluster in enumerate(clusters, start=1):
        label = f"group-{i}"
        for wid in cluster:
            by_id[wid]["work_area_group"] = label
    # Any cells without geometry get a sentinel so they're not silently swept under
    # the last real group.
    for w in skipped:
        w["work_area_group"] = "group-no-geometry"
    return work_areas


def _bfs_single_cluster(
    seed: str,
    unvisited: set,
    adjacency: dict[str, list[str]],
    by_id: dict[str, dict],
    max_buildings: int,
) -> list[str]:
    cluster: list[str] = []
    total = 0
    queue = deque([seed])
    seen = {seed}
    while queue:
        current = queue.popleft()
        if current not in unvisited:
            continue
        b = int(by_id[current].get("building_count", 0))
        if total + b > max_buildings:
            seen.discard(current)
            continue
        cluster.append(current)
        unvisited.discard(current)
        total += b
        for neighbour in adjacency.get(current, []):
            if neighbour in unvisited and neighbour not in seen:
                queue.append(neighbour)
                seen.add(neighbour)
    return cluster


def _pair(a: str, b: str) -> tuple:
    return (a, b) if a < b else (b, a)
