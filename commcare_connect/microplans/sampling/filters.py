"""Frame filters — port of the R clustering_pipeline cleaning stage.

Order matches the R script:
  1. confidence >= min_confidence  (applied upstream in the Overture query)
  2. tiny-roof rule: drop roofs < area_min UNLESS the roof has >= 2 other tiny
     roofs within 12m (clustered tiny roofs are real dwellings; isolated tiny
     polygons are noise).
  3. drop very large roofs > area_max (markets, schools, compounds).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from commcare_connect.microplans.sampling.geo import project_to_meters


@dataclass(frozen=True)
class FilterConfig:
    area_min_m2: float = 9.0
    area_max_m2: float = 330.0
    tiny_neighbor_dist_m: float = 12.0
    tiny_neighbor_min: int = 2  # other tiny roofs within dist to keep an otherwise-too-small roof


@dataclass
class FilterResult:
    buildings: pd.DataFrame
    n_in: int
    n_out: int
    removed_tiny_isolated: int
    kept_tiny_clustered: int
    removed_large: int


def apply_frame_filters(df: pd.DataFrame, config: FilterConfig | None = None) -> FilterResult:
    config = config or FilterConfig()
    n_in = len(df)
    work = df.dropna(subset=["lon", "lat"]).reset_index(drop=True)

    kept_tiny = 0
    removed_tiny = 0
    if "area_m2" in work.columns and config.area_min_m2:
        small_mask = work["area_m2"].to_numpy() < config.area_min_m2
        keep_small = np.zeros(len(work), dtype=bool)
        small_idx = np.flatnonzero(small_mask)
        if small_idx.size >= 2:
            x, y, _ = project_to_meters(work["lon"].to_numpy()[small_idx], work["lat"].to_numpy()[small_idx])
            tree = cKDTree(np.column_stack([x, y]))
            # neighbors within dist, minus self → count of OTHER tiny roofs nearby
            counts = (
                tree.query_ball_point(np.column_stack([x, y]), r=config.tiny_neighbor_dist_m, return_length=True) - 1
            )
            keep_small[small_idx] = counts >= config.tiny_neighbor_min
        kept_tiny = int(keep_small[small_idx].sum()) if small_idx.size else 0
        removed_tiny = int(small_idx.size - kept_tiny)
        # keep: not-small OR (small AND clustered)
        work = work[(~small_mask) | keep_small].reset_index(drop=True)

    removed_large = 0
    if "area_m2" in work.columns and config.area_max_m2:
        before = len(work)
        work = work[work["area_m2"] <= config.area_max_m2].reset_index(drop=True)
        removed_large = before - len(work)

    return FilterResult(
        buildings=work,
        n_in=n_in,
        n_out=len(work),
        removed_tiny_isolated=removed_tiny,
        kept_tiny_clustered=kept_tiny,
        removed_large=removed_large,
    )
