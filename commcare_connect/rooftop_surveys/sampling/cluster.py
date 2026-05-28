"""PSU formation — port of the R k-means + min-size-merge stage.

k-means over the projected coordinates, then small clusters (< min_cluster_size)
are merged into their nearest surviving centroid, iteratively, until every
cluster clears the floor. Clusters are the PSUs that PPS selection draws from.

The pilot forced every cluster to the "Low" stratum (stratification keys off a
`distance_to_visit` reference point the pilot didn't vary), so we don't stratify
here — selection treats all clusters as one pool, matching the baseline run.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans

from commcare_connect.rooftop_surveys.sampling.geo import project_to_meters


@dataclass(frozen=True)
class ClusterConfig:
    target_psus: int = 25  # how many PSUs PPS will ultimately select
    k_multiplier: int = 3  # raw k-means k = target_psus * this (R used ~75 for 25)
    min_cluster_size: int = 16  # clusters smaller than this are merged away
    seed: int = 123


@dataclass
class ClusterResult:
    buildings: pd.DataFrame  # input rows + 'cluster' label + projected x_m/y_m
    psu_frame: pd.DataFrame  # one row per cluster: cluster, n_buildings, centroid_lon/lat, radius95_m
    k_used: int


def cluster_buildings(df: pd.DataFrame, config: ClusterConfig | None = None) -> ClusterResult:
    config = config or ClusterConfig()
    work = df.reset_index(drop=True).copy()
    n = len(work)
    if n == 0:
        empty = work.assign(cluster=pd.Series(dtype=int), x_m=pd.Series(dtype=float), y_m=pd.Series(dtype=float))
        return ClusterResult(
            empty, pd.DataFrame(columns=["cluster", "n_buildings", "centroid_lon", "centroid_lat", "radius95_m"]), 0
        )

    x, y, _ = project_to_meters(work["lon"].to_numpy(), work["lat"].to_numpy())
    work["x_m"], work["y_m"] = x, y
    coords = np.column_stack([x, y])

    k = max(1, min(config.target_psus * config.k_multiplier, n))
    labels = KMeans(n_clusters=k, random_state=config.seed, n_init=10).fit_predict(coords)
    labels = _merge_small_clusters(coords, labels, config.min_cluster_size)
    work["cluster"] = [f"C{lbl}" for lbl in labels]

    psu_frame = _build_psu_frame(work, coords, labels)
    return ClusterResult(buildings=work, psu_frame=psu_frame, k_used=k)


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
    # relabel contiguous 0..m-1
    remap = {old: i for i, old in enumerate(np.unique(labels))}
    return np.array([remap[lbl] for lbl in labels], dtype=int)


def _build_psu_frame(work: pd.DataFrame, coords: np.ndarray, labels: np.ndarray) -> pd.DataFrame:
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
