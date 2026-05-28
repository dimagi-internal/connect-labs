"""PSU formation — port of the R k-means + min-size-merge + stratification stage.

k-means over the projected coordinates, then small clusters (< min_cluster_size)
are merged into their nearest surviving centroid. Clusters are the PSUs that PPS
selection draws from.

Stratification (High/Medium/Low) keys off `distance_to_visit` — each building's
distance to the verification reference point (e.g. the facility being checked).
When a `reference_point` is supplied we compute it and classify per the R
thresholds; without one we can't stratify, so every cluster falls in a single
"Low" pool (which is also exactly what the Nigeria pilot forced via
`mutate(stratum="Low")`).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans

from commcare_connect.microplans.core.geo import project_to_meters


@dataclass(frozen=True)
class ClusterConfig:
    target_psus: int = 25  # how many PSUs PPS will ultimately select
    k_multiplier: int = 3  # raw k-means k = target_psus * this (R used ~75 for 25)
    min_cluster_size: int = 16  # clusters smaller than this are merged away
    seed: int = 123
    # Stratum thresholds (R clustering_pipeline defaults).
    high_pct_50: float = 95.0  # % of buildings within 50m of the reference point
    high_pct_75: float = 99.0  # % within 75m
    high_min_buildings: int = 45
    high_max_radius95_m: float = 250.0
    low_pct_le_400_max: float = 0.0  # Low = essentially no buildings within 400m


@dataclass
class ClusterResult:
    buildings: pd.DataFrame  # input rows + 'cluster' label + projected x_m/y_m (+ distance_to_visit)
    psu_frame: pd.DataFrame  # one row per cluster: cluster, n_buildings, centroid, radius95_m, coverage, stratum
    k_used: int


def cluster_buildings(
    df: pd.DataFrame,
    config: ClusterConfig | None = None,
    reference_point: tuple[float, float] | None = None,
) -> ClusterResult:
    """Cluster buildings into PSUs. `reference_point` is (lon, lat) of the visit reference."""
    config = config or ClusterConfig()
    work = df.reset_index(drop=True).copy()
    n = len(work)
    if n == 0:
        empty = work.assign(cluster=pd.Series(dtype=int), x_m=pd.Series(dtype=float), y_m=pd.Series(dtype=float))
        cols = ["cluster", "n_buildings", "centroid_lon", "centroid_lat", "radius95_m", "stratum"]
        return ClusterResult(empty, pd.DataFrame(columns=cols), 0)

    x, y, epsg = project_to_meters(work["lon"].to_numpy(), work["lat"].to_numpy())
    work["x_m"], work["y_m"] = x, y
    coords = np.column_stack([x, y])

    if reference_point is not None and "distance_to_visit" not in work.columns:
        from pyproj import Transformer

        fwd = Transformer.from_crs(4326, epsg, always_xy=True)
        rx, ry = fwd.transform(reference_point[0], reference_point[1])
        work["distance_to_visit"] = np.sqrt((x - rx) ** 2 + (y - ry) ** 2)

    k = max(1, min(config.target_psus * config.k_multiplier, n))
    labels = KMeans(n_clusters=k, random_state=config.seed, n_init=10).fit_predict(coords)
    labels = _merge_small_clusters(coords, labels, config.min_cluster_size)
    work["cluster"] = [f"C{lbl}" for lbl in labels]

    psu_frame = _build_psu_frame(work, coords, labels, config)
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
    remap = {old: i for i, old in enumerate(np.unique(labels))}
    return np.array([remap[lbl] for lbl in labels], dtype=int)


def _build_psu_frame(
    work: pd.DataFrame, coords: np.ndarray, labels: np.ndarray, config: ClusterConfig
) -> pd.DataFrame:
    has_dist = "distance_to_visit" in work.columns
    rows = []
    for lbl in np.unique(labels):
        mask = labels == lbl
        pts = coords[mask]
        center = pts.mean(axis=0)
        dists = np.sqrt(((pts - center) ** 2).sum(axis=1))
        row = {
            "cluster": f"C{lbl}",
            "n_buildings": int(mask.sum()),
            "centroid_lon": float(work.loc[mask, "lon"].mean()),
            "centroid_lat": float(work.loc[mask, "lat"].mean()),
            "radius95_m": float(np.quantile(dists, 0.95)) if len(dists) else 0.0,
        }
        if has_dist:
            d = work.loc[mask, "distance_to_visit"].to_numpy()
            row["pct_50"] = 100.0 * np.mean(d <= 50)
            row["pct_75"] = 100.0 * np.mean(d <= 75)
            row["pct_le_400"] = 100.0 * np.mean(d <= 400)
        rows.append(row)

    frame = pd.DataFrame(rows).sort_values("cluster").reset_index(drop=True)
    frame["stratum"] = _classify_strata(frame, config) if has_dist else "Low"
    return frame


def _classify_strata(frame: pd.DataFrame, config: ClusterConfig) -> pd.Series:
    high = (
        (frame["pct_50"] >= config.high_pct_50)
        & (frame["pct_75"] >= config.high_pct_75)
        & (frame["n_buildings"] >= config.high_min_buildings)
        & (frame["radius95_m"] <= config.high_max_radius95_m)
    )
    low = frame["pct_le_400"] <= config.low_pct_le_400_max
    return np.where(high, "High", np.where(low, "Low", "Medium"))
