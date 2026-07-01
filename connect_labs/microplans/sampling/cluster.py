"""Sampling PSU formation = the kmeans_merge clustering strategy + stratification.

Clustering itself lives in core.clustering (shared with coverage mode). This
module adds the sampling-specific layer: distance_to_visit (from an optional
reference point) and High/Medium/Low strata per the R thresholds. Without a
reference point everything is a single "Low" pool (the pilot baseline).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from connect_labs.microplans.core import clustering
from connect_labs.microplans.core.geo import utm_epsg_for


@dataclass(frozen=True)
class ClusterConfig:
    target_psus: int = 25  # how many PSUs PPS will ultimately select
    k_multiplier: int = 3  # raw k-means k = target_psus * this
    min_cluster_size: int = 16  # clusters smaller than this are merged away
    seed: int = 123
    # Stratum thresholds (R clustering_pipeline defaults).
    high_pct_50: float = 95.0
    high_pct_75: float = 99.0
    high_min_buildings: int = 45
    high_max_radius95_m: float = 250.0
    low_pct_le_400_max: float = 0.0


@dataclass
class ClusterResult:
    buildings: pd.DataFrame
    psu_frame: pd.DataFrame
    k_used: int


def cluster_buildings(
    df: pd.DataFrame,
    config: ClusterConfig | None = None,
    reference_point: tuple[float, float] | None = None,
) -> ClusterResult:
    config = config or ClusterConfig()
    if len(df) == 0:
        out = clustering.kmeans_merge(df, k=1, min_cluster_size=config.min_cluster_size, seed=config.seed)
        out.psu_frame["stratum"] = pd.Series(dtype=object)
        return ClusterResult(out.buildings, out.psu_frame, 0)

    k = config.target_psus * config.k_multiplier
    out = clustering.kmeans_merge(df, k=k, min_cluster_size=config.min_cluster_size, seed=config.seed)
    buildings, psu_frame = out.buildings, out.psu_frame.copy()

    has_dist = False
    if reference_point is not None:
        from pyproj import Transformer

        epsg = utm_epsg_for(float(buildings["lon"].mean()), float(buildings["lat"].mean()))
        rx, ry = Transformer.from_crs(4326, epsg, always_xy=True).transform(*reference_point)
        buildings["distance_to_visit"] = np.sqrt((buildings["x_m"] - rx) ** 2 + (buildings["y_m"] - ry) ** 2)
        psu_frame = _add_coverage_metrics(psu_frame, buildings)
        has_dist = True

    psu_frame["stratum"] = _classify_strata(psu_frame, config) if has_dist else "Low"
    return ClusterResult(buildings, psu_frame, out.k_used)


def _add_coverage_metrics(psu_frame: pd.DataFrame, buildings: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for cluster, sub in buildings.groupby("cluster"):
        d = sub["distance_to_visit"].to_numpy()
        rows.append(
            {
                "cluster": cluster,
                "pct_50": 100.0 * np.mean(d <= 50),
                "pct_75": 100.0 * np.mean(d <= 75),
                "pct_le_400": 100.0 * np.mean(d <= 400),
            }
        )
    return psu_frame.merge(pd.DataFrame(rows), on="cluster", how="left")


def _classify_strata(frame: pd.DataFrame, config: ClusterConfig) -> pd.Series:
    high = (
        (frame["pct_50"] >= config.high_pct_50)
        & (frame["pct_75"] >= config.high_pct_75)
        & (frame["n_buildings"] >= config.high_min_buildings)
        & (frame["radius95_m"] <= config.high_max_radius95_m)
    )
    low = frame["pct_le_400"] <= config.low_pct_le_400_max
    return np.where(high, "High", np.where(low, "Low", "Medium"))
