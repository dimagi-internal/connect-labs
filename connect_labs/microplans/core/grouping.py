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

VALID_STRATEGIES = ("bbox", "bfs_adjacency", "barrier_aware")
# Barrier-aware treats max_buildings as a TARGET with this tolerance (±20%): groups
# aim for the number and may run up to +20% above it, staying ≥ −20% where possible.
BARRIER_TOLERANCE = 0.2


@dataclass
class GroupingConfig:
    strategy: str = "bfs_adjacency"  # default mirrors Connect GIS
    # bbox-only:
    target_size: int = DEFAULT_TARGET_SIZE  # ~cells per super-grid bucket
    # bfs_adjacency + barrier_aware:
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


def group_work_areas(work_areas: list[dict], config: GroupingConfig, barriers=None) -> list[dict]:
    """Apply ``config.strategy`` to ``work_areas`` in place. Returns the same list.

    ``barriers`` (a shapely geometry of road/rail/water lines, WGS84) is used only by
    the ``barrier_aware`` strategy; when it's None/empty that strategy falls back to
    plain adjacency clustering so it never produces worse groups than today."""
    if not work_areas:
        return work_areas
    if config.strategy == "bbox":
        return _bbox_bucket(work_areas, config.target_size)
    if config.strategy == "barrier_aware":
        return _barrier_aware(work_areas, config.max_buildings, config.buffer_distance_m, barriers)
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


# ---- barrier-aware clusters --------------------------------------------------


def _barrier_aware(work_areas, max_buildings, buffer_distance_m, barriers):
    """Group so no group spans a major road / river / railway.

    Cut the adjacency graph wherever the link between two cells would cross a
    ``barriers`` line → connected components are "same-side" regions (a cell can be
    reached from another without crossing a barrier). Within each region, pack cells
    into groups aimed at ``max_buildings`` as a TARGET (±20%), merging stray cells so
    we avoid 1-cell/tiny groups except where a barrier genuinely isolates only a few.

    No barriers (None/empty) → fall back to plain adjacency, so this never yields
    worse groups than today when road/river data is missing."""
    if barriers is None or getattr(barriers, "is_empty", False):
        return _bfs_adjacency(work_areas, max_buildings, buffer_distance_m)

    from pyproj import Transformer
    from shapely.errors import ShapelyError
    from shapely.geometry import LineString, shape
    from shapely.ops import transform as shp_transform
    from shapely.prepared import prep
    from shapely.strtree import STRtree

    fwd = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    geoms: dict[str, object] = {}
    cents: dict[str, tuple] = {}
    skipped = []
    for w in work_areas:
        g = w.get("geometry")
        if not g:
            skipped.append(w)
            continue
        try:
            shp = shp_transform(fwd.transform, shape(g))
            if shp.is_empty:
                raise ValueError("empty")
            geoms[w["id"]] = shp
            cents[w["id"]] = (shp.centroid.x, shp.centroid.y)
        except (ShapelyError, ValueError, TypeError):
            skipped.append(w)
    if not geoms:
        return _bfs_adjacency(work_areas, max_buildings, buffer_distance_m)

    try:
        barriers_3857 = prep(shp_transform(fwd.transform, barriers))
    except (ShapelyError, ValueError, TypeError):
        return _bfs_adjacency(work_areas, max_buildings, buffer_distance_m)

    # ---- adjacency graph, cutting links that cross a barrier ----
    wa_ids = list(geoms.keys())
    geoms_list = [geoms[wid] for wid in wa_ids]
    tree = STRtree(geoms_list)
    adjacency: dict[str, set] = {wid: set() for wid in wa_ids}
    for wid, geom in geoms.items():
        for idx in tree.query(geom.buffer(buffer_distance_m)):
            nb = wa_ids[idx]
            if nb == wid or nb in adjacency[wid]:
                continue
            other = geoms[nb]
            if geom.distance(other) > buffer_distance_m:
                continue
            # Same group only if you can get between them without crossing a barrier.
            if barriers_3857.intersects(LineString([cents[wid], cents[nb]])):
                continue
            adjacency[wid].add(nb)
            adjacency[nb].add(wid)

    # ---- connected components = same-side regions ----
    by_id = {w["id"]: w for w in work_areas if w["id"] in geoms}
    unvisited = set(by_id)
    components: list[list[str]] = []
    for seed in sorted(unvisited, key=lambda wid: (cents[wid][0], -cents[wid][1])):
        if seed not in unvisited:
            continue
        comp = []
        queue = deque([seed])
        unvisited.discard(seed)
        while queue:
            cur = queue.popleft()
            comp.append(cur)
            for nb in adjacency[cur]:
                if nb in unvisited:
                    unvisited.discard(nb)
                    queue.append(nb)
        components.append(comp)

    # ---- pack each region into ~target-sized groups ----
    groups: list[list[str]] = []
    for comp in components:
        groups.extend(_pack_region(comp, by_id, max_buildings))

    for i, group in enumerate(groups, start=1):
        label = f"group-{i}"
        for wid in group:
            by_id[wid]["work_area_group"] = label
    for w in skipped:
        w["work_area_group"] = "group-no-geometry"
    return work_areas


def _pack_region(ids: list[str], by_id: dict, target: int) -> list[list[str]]:
    """Split one barrier-bounded region into groups aimed at ``target`` buildings
    (±20%). One group when the region is small; otherwise ~round(total/target) even,
    spatially-ordered chunks, with a tiny trailing chunk merged back to avoid a
    dribble group. A region smaller than the target is a single (possibly small)
    group — that's the intended "barrier isolated only a few WAs" case."""
    if not ids:
        return []
    b = lambda wid: int(by_id[wid].get("building_count", 0))  # noqa: E731
    total = sum(b(wid) for wid in ids)
    hard = target * (1 + BARRIER_TOLERANCE)
    floor = target * (1 - BARRIER_TOLERANCE)
    n = max(1, round(total / target)) if target > 0 else 1
    if n <= 1:
        return [list(ids)]
    per = total / n
    # Spatial order (columns W→E, then N→S) so chunks are geographically coherent.
    ordered = sorted(ids, key=lambda wid: (by_id[wid]["centroid"][0], -by_id[wid]["centroid"][1]))
    bins: list[list[str]] = []
    cur: list[str] = []
    cur_b = 0
    for wid in ordered:
        bb = b(wid)
        if cur and len(bins) < n - 1 and (cur_b >= per or cur_b + bb > hard):
            bins.append(cur)
            cur, cur_b = [], 0
        cur.append(wid)
        cur_b += bb
    if cur:
        bins.append(cur)
    # Fold a small trailing group into the previous one when it fits under the ceiling.
    if len(bins) >= 2:
        last = sum(b(wid) for wid in bins[-1])
        prev = sum(b(wid) for wid in bins[-2])
        if last < floor and last + prev <= hard:
            bins[-2].extend(bins.pop())
    return bins
