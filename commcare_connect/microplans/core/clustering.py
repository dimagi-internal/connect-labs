"""Clustering strategies — shared building→cluster algorithms.

Each strategy returns a ClusterOutput: the input buildings tagged with a
`cluster` label + projected x_m / y_m, plus a base `psu_frame` (one row per
cluster: cluster, n_buildings, centroid_lon/lat, radius95_m). Mode layers build
on top — sampling adds strata + PPS; coverage assigns whole clusters to FLWs.

Strategies:
- `kmeans_merge`   — k-means then merge clusters < min_size into the nearest
  surviving centroid. The sampling default (clusters can be uneven).
- `balanced_kmeans` — equal-sized clusters via KMeansConstrained. Even FLW
  workloads → the coverage default (ported from connect-gis).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from commcare_connect.microplans.core.geo import project_to_meters


@dataclass
class ClusterOutput:
    buildings: pd.DataFrame  # input rows + 'cluster' + projected x_m/y_m
    psu_frame: pd.DataFrame  # cluster, n_buildings, centroid_lon, centroid_lat, radius95_m
    k_used: int


def _project(df: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray, int]:
    work = df.reset_index(drop=True).copy()
    x, y, epsg = project_to_meters(work["lon"].to_numpy(), work["lat"].to_numpy())
    work["x_m"], work["y_m"] = x, y
    return work, np.column_stack([x, y]), epsg


def _base_psu_frame(work: pd.DataFrame, coords: np.ndarray, labels: np.ndarray) -> pd.DataFrame:
    rows = []
    for lbl in np.unique(labels):
        mask = labels == lbl
        pts = coords[mask]
        center = pts.mean(axis=0)
        dists = np.sqrt(((pts - center) ** 2).sum(axis=1))
        rows.append(
            {
                "cluster": f"C{lbl}",
                "n_buildings": int(mask.sum()),
                "centroid_lon": float(work.loc[mask, "lon"].mean()),
                "centroid_lat": float(work.loc[mask, "lat"].mean()),
                "radius95_m": float(np.quantile(dists, 0.95)) if len(dists) else 0.0,
            }
        )
    return pd.DataFrame(rows).sort_values("cluster").reset_index(drop=True)


def _empty_output(df: pd.DataFrame) -> ClusterOutput:
    empty = df.assign(cluster=pd.Series(dtype=int), x_m=pd.Series(dtype=float), y_m=pd.Series(dtype=float))
    cols = ["cluster", "n_buildings", "centroid_lon", "centroid_lat", "radius95_m"]
    return ClusterOutput(empty, pd.DataFrame(columns=cols), 0)


def kmeans_merge(df: pd.DataFrame, k: int, min_cluster_size: int = 16, seed: int = 123) -> ClusterOutput:
    """k-means(k) then merge clusters < min_cluster_size into the nearest centroid."""
    from sklearn.cluster import KMeans

    if len(df) == 0:
        return _empty_output(df)
    work, coords, _ = _project(df)
    k = max(1, min(k, len(work)))
    labels = KMeans(n_clusters=k, random_state=seed, n_init=10).fit_predict(coords)
    labels = _merge_small_clusters(coords, labels, min_cluster_size)
    work["cluster"] = [f"C{lbl}" for lbl in labels]
    return ClusterOutput(work, _base_psu_frame(work, coords, labels), k)


def balanced_kmeans(
    df: pd.DataFrame,
    n_clusters: int | None = None,
    buildings_per_cluster: int | None = None,
    balance_tolerance: float = 0.05,
    seed: int = 42,
) -> ClusterOutput:
    """Equal-sized clusters via KMeansConstrained (even FLW workloads).

    Specify either `n_clusters` or `buildings_per_cluster`. `balance_tolerance`
    is the allowed +/- fraction around the even split (0 = exactly even).
    """
    from k_means_constrained import KMeansConstrained

    if len(df) == 0:
        return _empty_output(df)
    work, coords, _ = _project(df)
    n = len(work)
    if buildings_per_cluster:
        n_clusters = max(1, math.ceil(n / buildings_per_cluster))
    n_clusters = max(1, min(n_clusters or 1, n))
    size_min, size_max = _balanced_size_bounds(n, n_clusters, balance_tolerance)
    labels = KMeansConstrained(
        n_clusters=n_clusters, size_min=size_min, size_max=size_max, random_state=seed, n_init=1, n_jobs=1
    ).fit_predict(coords)
    work["cluster"] = [f"C{lbl}" for lbl in labels]
    return ClusterOutput(work, _base_psu_frame(work, coords, labels), n_clusters)


def _balanced_size_bounds(n_samples: int, n_clusters: int, balance_tolerance: float) -> tuple[int, int]:
    base = math.ceil(n_samples / n_clusters)
    if n_clusters == 1 or balance_tolerance == 0:
        return math.floor(n_samples / n_clusters), base
    return max(1, math.floor(base * (1 - balance_tolerance))), math.ceil(base * (1 + balance_tolerance))


def _merge_small_clusters(coords: np.ndarray, labels: np.ndarray, min_size: int) -> np.ndarray:
    labels = labels.astype(int).copy()
    while True:
        uniq = np.unique(labels)
        sizes = {lbl: int(np.sum(labels == lbl)) for lbl in uniq}
        small = [lbl for lbl in uniq if sizes[lbl] < min_size]
        if not small or len(uniq) == 1:
            break
        centers = {lbl: coords[labels == lbl].mean(axis=0) for lbl in uniq}
        changed = False
        for sc in small:
            others = [lbl for lbl in np.unique(labels) if lbl != sc]
            if not others:
                continue
            sc_center = centers[sc]
            target = min(others, key=lambda o: np.sum((centers[o] - sc_center) ** 2))
            labels[labels == sc] = target
            changed = True
        if not changed:
            break
    remap = {old: i for i, old in enumerate(np.unique(labels))}
    return np.array([remap[lbl] for lbl in labels], dtype=int)
