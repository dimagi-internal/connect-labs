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
                       ``target_buildings``. Spatially contiguous + capped load.
                       Distances computed in EPSG:3857 (approximate metres).

                       A second pass then tops up any resulting group under
                       ``min_buildings`` — see ``_top_up_undersized_clusters``.
- ``barrier_aware``  — the SAME BFS as ``bfs_adjacency`` (including the top-up
                       pass), but a link between two cells is dropped when the
                       segment between them crosses a major road / river /
                       railway, so a cluster's border stops at the barrier while
                       it keeps growing in every other direction. Barriers
                       absent → identical to ``bfs_adjacency``.

The grouping operation mutates the ``work_area_group`` field on each work area
in place; nothing else changes (counts, CHW assignment, status all unaffected).

Building-count knobs (bfs_adjacency + barrier_aware), added 2026-07
--------------------------------------------------------------------
Phase-1 BFS clustering aims for ``target_buildings`` per group (this is the
field that used to be called ``max_buildings`` — see the backward-compat note
on ``GroupingConfig.from_payload``). A hard cap alone tends to strand small
"remainder" groups — e.g. a village's last 10-20% of cells, split off into
their own tiny group once the cap is hit. A post-clustering pass fixes this:
any group under ``min_buildings`` merges into (or steals cells from) its
nearest eligible neighbour — see ``_top_up_undersized_clusters`` for the full
algorithm. ``max_buildings`` (the new hard ceiling) and ``max_reach_m`` (the
top-up pass's own search radius, independent of ``buffer_distance_m``) bound
how far this can go. ``min_buildings=0`` (the default) disables the whole pass,
so existing plans/callers that don't set it behave exactly as before.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

# Connect GIS defaults (microplanning/clustering.py):
#   max_buildings=200, buffer_distance=100 m. We mirror these so a labs plan
#   regrouped with BFS lines up with what Connect's grouper would have done.
DEFAULT_TARGET_SIZE = 30
DEFAULT_TARGET_BUILDINGS = 200
DEFAULT_BUFFER_DISTANCE_M = 100

VALID_STRATEGIES = ("bbox", "bfs_adjacency", "barrier_aware")


def _ward_prefix(ward: str | None) -> str:
    """First 3 letters of a ward/area name, uppercased, for a human-guessable
    group-name prefix (e.g. "Kano North" -> "KAN"). Punctuation/spaces are
    dropped before taking the first 3 so short or oddly-formatted names still
    yield a usable prefix. Empty/missing ward -> "" (caller falls back to the
    plain, unprefixed label)."""
    letters = "".join(ch for ch in (ward or "") if ch.isalnum())
    return letters[:3].upper()


def _group_label(ward: str | None, n) -> str:
    prefix = _ward_prefix(ward)
    return f"{prefix}-group-{n}" if prefix else f"group-{n}"


@dataclass
class GroupingConfig:
    strategy: str = "bfs_adjacency"  # default mirrors Connect GIS
    # bbox-only:
    target_size: int = DEFAULT_TARGET_SIZE  # ~cells per super-grid bucket
    # bfs_adjacency + barrier_aware — phase-1 clustering:
    target_buildings: int = DEFAULT_TARGET_BUILDINGS
    buffer_distance_m: int = DEFAULT_BUFFER_DISTANCE_M
    # bfs_adjacency + barrier_aware — phase-2 top-up for undersized groups.
    # min_buildings=0 (the default) disables the whole pass. max_buildings/
    # max_reach_m of None resolve to target_buildings/buffer_distance_m (no
    # headroom) via the effective_* properties below — so leaving these unset
    # reproduces the pre-2026-07 behaviour exactly.
    max_buildings: int | None = None
    min_buildings: int = 0
    max_reach_m: float | None = None

    @property
    def effective_max_buildings(self) -> int:
        return self.max_buildings if self.max_buildings is not None else self.target_buildings

    @property
    def effective_max_reach_m(self) -> float:
        return self.max_reach_m if self.max_reach_m is not None else float(self.buffer_distance_m)

    @classmethod
    def from_payload(cls, d: dict) -> GroupingConfig:
        strategy = d.get("strategy", "bfs_adjacency")
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"unknown grouping strategy: {strategy!r} (one of {VALID_STRATEGIES})")
        # "max_buildings" was this field's name before the 2026-07 target/max/min
        # split — a payload carrying it WITHOUT "target_buildings" is old-format
        # (a plan saved before this change, or a caller that hasn't updated), so
        # treat it as target_buildings, exactly what it always meant. A payload
        # from the updated UI sends both keys explicitly, so this fallback never
        # fires for new data.
        target_buildings = max(1, int(d.get("target_buildings", d.get("max_buildings", DEFAULT_TARGET_BUILDINGS))))
        raw_max = d.get("max_buildings")
        max_buildings = max(target_buildings, int(raw_max)) if raw_max is not None else None
        raw_reach = d.get("max_reach_m")
        max_reach_m = max(0.0, float(raw_reach)) if raw_reach is not None else None
        return cls(
            strategy=strategy,
            target_size=max(1, int(d.get("target_size", DEFAULT_TARGET_SIZE))),
            target_buildings=target_buildings,
            buffer_distance_m=max(0, int(d.get("buffer_distance_m", DEFAULT_BUFFER_DISTANCE_M))),
            max_buildings=max_buildings,
            min_buildings=max(0, int(d.get("min_buildings", 0))),
            max_reach_m=max_reach_m,
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
    if config.strategy in ("barrier_aware", "bfs_adjacency"):
        useful = None
        if config.strategy == "barrier_aware":
            # Same algorithm as walkable clusters, but a cluster's border stops at a
            # barrier. None/empty barriers → identical to plain walkable clusters.
            useful = barriers if (barriers is not None and not getattr(barriers, "is_empty", True)) else None
        return _bfs_adjacency(
            work_areas,
            config.target_buildings,
            config.buffer_distance_m,
            useful,
            min_buildings=config.min_buildings,
            max_buildings=config.effective_max_buildings,
            max_reach_m=config.effective_max_reach_m,
        )
    raise ValueError(f"unknown grouping strategy: {config.strategy!r}")


# ---- bbox bucket -------------------------------------------------------------


def _bbox_bucket(work_areas: list[dict], target_size: int) -> list[dict]:
    """Tile the cells' bbox into a sqrt(N/target_size)-side super-grid by centroid.
    Group label = ``{WARD-prefix-}group-N``, prefixed with the first 3 letters of
    the work area's own ward (see ``_ward_prefix``) so the name hints which ward
    it belongs to. ``N`` restarts at 1 for each distinct ward — see
    ``_ward_numbered_labels``.

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
    bucket_keys = []
    for w, (lon, lat) in zip(work_areas, centroids):
        i = min(grid_n - 1, int((lat - lat_min) / lat_span * grid_n))
        j = min(grid_n - 1, int((lon - lon_min) / lon_span * grid_n))
        ward = (w.get("properties") or {}).get("ward") or ""
        bucket_keys.append((ward, i * grid_n + j))
    labels = _ward_numbered_labels(bucket_keys)
    for w, key in zip(work_areas, bucket_keys):
        w["work_area_group"] = labels[key]
    return work_areas


def _ward_numbered_labels(keys: list[tuple[str, object]]) -> dict[tuple[str, object], str]:
    """Assign each distinct ``(ward, bucket)`` key a label ``{WARD-}group-N``, where
    ``N`` restarts at 1 for every distinct ward (first-seen order) — so two wards'
    groups don't share a running count (``KAN-group-1, KAN-group-2, MAD-group-1``,
    not ``..., MAD-group-3``)."""
    counters: dict[str, int] = {}
    labels: dict[tuple[str, object], str] = {}
    for key in keys:
        if key in labels:
            continue
        ward = key[0]
        counters[ward] = counters.get(ward, 0) + 1
        labels[key] = _group_label(ward, counters[ward])
    return labels


# ---- BFS adjacency (port of Connect GIS WorkAreaGrouper) ---------------------


def _bfs_adjacency(
    work_areas: list[dict],
    target_buildings: int,
    buffer_distance_m: int,
    barriers=None,
    *,
    min_buildings: int = 0,
    max_buildings: int | None = None,
    max_reach_m: float | None = None,
) -> list[dict]:
    """Cluster cells via BFS over a buffer-thickened adjacency graph, capped by
    total building count per cluster, then top up any undersized result.

    Mirrors the production algorithm at
    ``dimagi/commcare-connect/commcare_connect/microplanning/clustering.py``.
    Adjacency check uses EPSG:3857 (Web Mercator) for an approximate metres-
    distance; this is what Connect uses and the same caveats apply (Mercator is
    not equidistant — buffer_distance_m is approximate near the equator).

    ``barriers`` (a shapely geometry of road/rail/water lines in WGS84) is the ONLY
    difference between "walkable clusters" (barriers=None) and "barrier-aware
    clusters": when set, a link between two cells is dropped if the segment between
    their centroids crosses a barrier, so a cluster stops growing across a major
    road/river/railway but keeps growing in every other direction. Everything else —
    seeding, BFS, the building cap — is identical, so walkable clusters behave exactly
    as before. The same barrier test also gates the top-up pass below.

    ``min_buildings``/``max_buildings``/``max_reach_m`` drive the post-clustering
    top-up pass (see ``_top_up_undersized_clusters``); ``min_buildings<=0`` (the
    default) disables it, reproducing the pre-2026-07 behaviour exactly."""
    from pyproj import Transformer
    from shapely import get_dimensions
    from shapely.errors import ShapelyError
    from shapely.geometry import LineString, shape
    from shapely.ops import transform as shp_transform
    from shapely.prepared import prep
    from shapely.strtree import STRtree

    fwd = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    geoms_3857: dict[str, object] = {}
    cents_3857: dict[str, tuple] = {}
    skipped: list[dict] = []
    for w in work_areas:
        g = w.get("geometry")
        if not g:
            skipped.append(w)
            continue
        try:
            shp = shp_transform(fwd.transform, shape(g))
            if shp.is_empty:
                raise ValueError("empty geometry")
            geoms_3857[w["id"]] = shp
            cents_3857[w["id"]] = (shp.centroid.x, shp.centroid.y)
        except (ShapelyError, ValueError, TypeError):
            skipped.append(w)

    if not geoms_3857:
        # No geometries to cluster — fall back to bbox so every cell still gets a label.
        return _bbox_bucket(work_areas, DEFAULT_TARGET_SIZE)

    # Barrier lines in 3857 (prepared for fast repeated crossing tests). None → the
    # plain walkable-clusters behaviour.
    barriers_3857 = None
    if barriers is not None and not getattr(barriers, "is_empty", True):
        try:
            barriers_3857 = prep(shp_transform(fwd.transform, barriers))
        except (ShapelyError, ValueError, TypeError):
            barriers_3857 = None

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
            if not (get_dimensions(shared) >= 1 or dist <= buffer_distance_m):
                continue
            # Barrier-aware: don't link two cells if getting between them crosses a
            # major road / river / railway — the cluster's border is defined there.
            if barriers_3857 is not None and barriers_3857.intersects(
                LineString([cents_3857[wid], cents_3857[neighbour]])
            ):
                continue
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
        cluster = _bfs_single_cluster(seed, unvisited, adjacency, by_id, target_buildings)
        if not cluster:
            # A single oversized cell still gets its own group (Connect's behaviour).
            cluster = [seed]
            unvisited.discard(seed)
        clusters.append(cluster)

    # ---- Phase 2 (optional): top up any cluster under min_buildings by merging/
    # stealing from a nearby neighbour, before labels are assigned. ----
    resolved_max = max_buildings if max_buildings is not None else target_buildings
    resolved_reach = max_reach_m if max_reach_m is not None else float(buffer_distance_m)
    clusters_with_seed = _top_up_undersized_clusters(
        clusters,
        by_id,
        geoms_3857,
        cents_3857,
        adjacency,
        tree,
        wa_ids,
        min_buildings,
        resolved_max,
        resolved_reach,
        barriers_3857,
    )

    # ---- assign labels ("{WARD-prefix-}group-N", N restarting at 1 per ward — see
    # _ward_numbered_labels). Prefix comes from each cluster's SURVIVING seed's ward
    # (the bigger/established cluster's own original seed when two clusters merge in
    # the top-up pass above) — one label per cluster (mirrors Connect's own
    # WorkAreaGroup.ward, which is stamped from the first row seen with a given group
    # name; see CONNECT_IMPORT_CONTRACT.md) ----
    cluster_keys = [
        ((by_id[seed].get("properties") or {}).get("ward") or "", i)
        for i, (seed, _cells) in enumerate(clusters_with_seed)
    ]
    labels = _ward_numbered_labels(cluster_keys)
    for key, (_seed, cells) in zip(cluster_keys, clusters_with_seed):
        label = labels[key]
        for wid in cells:
            by_id[wid]["work_area_group"] = label
    # Any cells without geometry get a sentinel so they're not silently swept under
    # the last real group — still ward-prefixed per cell (no shared cluster to derive
    # a single label from).
    for w in skipped:
        ward = (w.get("properties") or {}).get("ward")
        w["work_area_group"] = _group_label(ward, "no-geometry")
    return work_areas


def _bfs_single_cluster(
    seed: str,
    unvisited: set,
    adjacency: dict[str, list[str]],
    by_id: dict[str, dict],
    target_buildings: int,
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
        if total + b > target_buildings:
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


# ---- Phase-2 top-up: fix undersized clusters (#26) ----------------------------


def _top_up_undersized_clusters(
    clusters: list[list[str]],
    by_id: dict[str, dict],
    geoms_3857: dict[str, object],
    cents_3857: dict[str, tuple],
    adjacency: dict[str, set],
    tree,
    wa_ids: list[str],
    min_buildings: int,
    max_buildings: int,
    max_reach_m: float,
    barriers_3857=None,
) -> list[tuple[str, list[str]]]:
    """Any cluster under ``min_buildings`` merges into (or steals cells from) its
    nearest eligible neighbour:

    1. Find the nearest cross-cluster cell pair within ``max_reach_m`` (independent
       of the ``buffer_distance_m`` used for phase-1 clustering), skipping any pair
       whose connecting segment crosses a barrier under ``barrier_aware``, OR — when
       the pair isn't already graph-adjacent — crosses a cell belonging to a THIRD
       cluster (reaching across genuinely empty terrain is fine; reaching across
       another group's already-claimed territory is not, since that would leave that
       group's cells sitting in the gap between two pieces of the merged group).
    2. If the two clusters' combined total fits under ``max_buildings``, merge them
       whole — the bigger/established cluster's own seed survives as the merged
       group's identity (so its ward-prefixed label doesn't change).
    3. If a whole merge would breach ``max_buildings``, and the nearest pair is
       already graph-adjacent (a real phase-1 edge, not just within max_reach_m),
       steal cells from that donor one at a time — closest-to-recipient first —
       until the recipient reaches ``min_buildings``. A cell is only taken if (a)
       the donor would stay at/above its OWN ``min_buildings``, and (b) the donor's
       remaining cells stay one connected piece (checked by re-running reachability
       over the existing adjacency graph). Non-adjacent candidates can only be used
       via a whole merge — stealing across a gap would leave the recipient with a
       hole, defeating the point of a "walkable" group.
    4. If a donor is exhausted (hit its own floor, or has no more safely-removable
       cells) and the recipient is still short, move to the next-nearest eligible
       donor. No eligible donor at all (within reach, or under the ceiling) →
       leave the cluster standalone, however small.

    Returns ``[(seed_cell_id, [cell_ids...]), ...]`` — the seed identifies which
    cluster's ward a merged group's label is anchored to. Mutates nothing passed
    in; ``clusters`` itself is read-only here."""
    if min_buildings <= 0 or len(clusters) < 2:
        return [(cluster[0], cluster) for cluster in clusters]

    from shapely.geometry import LineString

    def building_count(wid: str) -> int:
        return int(by_id[wid].get("building_count", 0))

    active: dict[int, dict] = {}
    cell_owner: dict[str, int] = {}
    for cid, cluster in enumerate(clusters):
        active[cid] = {
            "cells": set(cluster),
            "seed": cluster[0],
            "total": sum(building_count(w) for w in cluster),
        }
        for w in cluster:
            cell_owner[w] = cid

    def crosses_barrier(a: str, b: str) -> bool:
        if barriers_3857 is None:
            return False
        return barriers_3857.intersects(LineString([cents_3857[a], cents_3857[b]]))

    def is_connected(cell_set: set) -> bool:
        if len(cell_set) <= 1:
            return True
        start = next(iter(cell_set))
        seen = {start}
        stack = [start]
        while stack:
            cur = stack.pop()
            for nb in adjacency.get(cur, []):
                if nb in cell_set and nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        return len(seen) == len(cell_set)

    def crosses_other_cluster(a: str, b: str, allowed: set) -> bool:
        """True if the straight line between two cells passes through a cell owned
        by a cluster OTHER than the two under consideration (`allowed`). Reaching
        across genuinely empty/unclaimed space (sparse terrain) is fine — reaching
        across a THIRD group's already-claimed territory is not: it would leave
        that group's cells sitting in the gap between two pieces of the merged
        group, which is exactly the "another WAG runs in between" artifact this
        guards against."""
        line = LineString([cents_3857[a], cents_3857[b]])
        for idx in tree.query(line, predicate="intersects"):
            wid2 = wa_ids[idx]
            if wid2 in (a, b):
                continue
            owner = cell_owner.get(wid2)
            if owner is not None and owner not in allowed:
                return True
        return False

    def nearest_donor(recipient_id: int, exclude: set):
        """Nearest (donor_id, is_adjacent) across max_reach_m, or None. A candidate
        reached only via the wider search (not a real phase-1 edge) is skipped if
        the straight line to it crosses a third cluster's cells — see
        crosses_other_cluster."""
        rec = active[recipient_id]
        best = None  # (dist, donor_id, is_adjacent)
        for wid in rec["cells"]:
            geom = geoms_3857[wid]
            for idx in tree.query(geom.buffer(max_reach_m), predicate="intersects"):
                nb = wa_ids[idx]
                if nb == wid or nb not in cell_owner:
                    continue
                donor_id = cell_owner[nb]
                if donor_id == recipient_id or donor_id in exclude:
                    continue
                dist = geom.distance(geoms_3857[nb])
                if dist > max_reach_m or crosses_barrier(wid, nb):
                    continue
                is_adjacent = nb in adjacency.get(wid, [])
                if not is_adjacent and crosses_other_cluster(wid, nb, {recipient_id, donor_id}):
                    continue
                if best is None or dist < best[0]:
                    best = (dist, donor_id, is_adjacent)
        return best[1:] if best else None

    def has_safe_link(cell: str, recipient_cells: set) -> bool:
        return any(nb in recipient_cells and not crosses_barrier(cell, nb) for nb in adjacency.get(cell, []))

    # Smallest-first, deterministic tie-break by original seed id.
    order = sorted(active.keys(), key=lambda cid: (active[cid]["total"], active[cid]["seed"]))

    for recipient_id in order:
        if recipient_id not in active:
            continue  # already absorbed as someone else's donor
        exhausted: set[int] = set()
        while active[recipient_id]["total"] < min_buildings:
            found = nearest_donor(recipient_id, exhausted)
            if found is None:
                break
            donor_id, is_adjacent = found
            recipient = active[recipient_id]
            donor = active[donor_id]
            combined = recipient["total"] + donor["total"]
            if combined <= max_buildings:
                # Whole merge — the bigger/established cluster's seed survives.
                if donor["total"] >= recipient["total"]:
                    recipient["seed"] = donor["seed"]
                recipient["cells"] |= donor["cells"]
                recipient["total"] = combined
                for w in donor["cells"]:
                    cell_owner[w] = recipient_id
                del active[donor_id]
                continue
            if not is_adjacent:
                # Only reachable via the wider max_reach_m search, not touching —
                # stealing would leave a gap, and whole-merge just failed on size.
                exhausted.add(donor_id)
                continue
            # ---- steal cells from this donor, closest-to-recipient first ----
            took_any = False
            while recipient["total"] < min_buildings:
                boundary = [c for c in donor["cells"] if has_safe_link(c, recipient["cells"])]
                if not boundary:
                    break
                boundary.sort(key=lambda c: min(geoms_3857[c].distance(geoms_3857[r]) for r in recipient["cells"]))
                took = False
                for c in boundary:
                    b = building_count(c)
                    if donor["total"] - b < min_buildings:
                        continue
                    remaining = donor["cells"] - {c}
                    if not is_connected(remaining):
                        continue
                    donor["cells"].discard(c)
                    donor["total"] -= b
                    recipient["cells"].add(c)
                    recipient["total"] += b
                    cell_owner[c] = recipient_id
                    took = took_any = True
                    break
                if not took:
                    break
            if not donor["cells"]:
                del active[donor_id]
            if not took_any or recipient["total"] < min_buildings:
                exhausted.add(donor_id)

    return [(rec["seed"], sorted(rec["cells"])) for rec in active.values()]


def _pair(a: str, b: str) -> tuple:
    return (a, b) if a < b else (b, a)
