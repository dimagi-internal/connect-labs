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

from connect_labs.microplans.core.geo import project_to_meters


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


# --- Cell-level (post-gridding) filters -------------------------------------
# The filters above operate on individual buildings *before* gridding. The two
# below operate on whole grid cells (work areas) *after* gridding, to drop cells
# that are likely noise rather than real settlement:
#   - min total rooftop area: a cell whose buildings sum to < X m² of roof is
#     probably a few spurious detections, not a dwelling cluster worth a visit.
#   - isolated single-building cell: a 1-building cell far from any cell holding
#     >=2 buildings is likely a misdetection sending an FLW on a pointless walk.
# Both default off so coverage behaviour is unchanged unless explicitly enabled.


@dataclass(frozen=True)
class CellFilterConfig:
    min_cell_roof_area_m2: float = 0.0  # 0 = off
    exclude_isolated_singletons: bool = False
    isolation_dist_m: float = 150.0  # a 1-bld cell farther than this from a >=2-bld cell is dropped


@dataclass
class CellFilterResult:
    psu_frame: pd.DataFrame  # annotated with roof_area_m2 + dist_to_multi_m, then filtered
    n_in: int
    n_out: int
    removed_small_area: int
    removed_isolated: int


def annotate_cell_metrics(buildings: pd.DataFrame, psu_frame: pd.DataFrame) -> pd.DataFrame:
    """Add per-cell `roof_area_m2` (sum of building footprint area in the cell) and
    `dist_to_multi_m` (distance from the cell centroid to the nearest cell holding
    >=2 buildings; 0 for cells that themselves have >=2). Reference set is all
    >=2-building cells, independent of any area filter."""
    frame = psu_frame.copy()
    if "area_m2" in buildings.columns and len(buildings):
        roof = buildings.groupby("cluster")["area_m2"].sum()
        frame["roof_area_m2"] = frame["cluster"].map(roof).fillna(0.0).astype(float)
    else:
        frame["roof_area_m2"] = 0.0

    if len(frame):
        x, y, _ = project_to_meters(frame["centroid_lon"].to_numpy(), frame["centroid_lat"].to_numpy())
        pts = np.column_stack([x, y])
        multi = frame["n_buildings"].to_numpy() >= 2
        if multi.any():
            dist, _ = cKDTree(pts[multi]).query(pts, k=1)
        else:
            dist = np.full(len(frame), np.inf)
        frame["dist_to_multi_m"] = dist
        frame.loc[frame["n_buildings"] >= 2, "dist_to_multi_m"] = 0.0
    else:
        frame["dist_to_multi_m"] = pd.Series(dtype=float)
    return frame


def apply_cell_filters(
    buildings: pd.DataFrame, psu_frame: pd.DataFrame, config: CellFilterConfig | None = None
) -> CellFilterResult:
    config = config or CellFilterConfig()
    frame = annotate_cell_metrics(buildings, psu_frame)
    n_in = len(frame)

    removed_small = 0
    if config.min_cell_roof_area_m2 and config.min_cell_roof_area_m2 > 0:
        before = len(frame)
        frame = frame[frame["roof_area_m2"] >= config.min_cell_roof_area_m2].reset_index(drop=True)
        removed_small = before - len(frame)

    removed_isolated = 0
    if config.exclude_isolated_singletons:
        before = len(frame)
        lone_far = (frame["n_buildings"] == 1) & (frame["dist_to_multi_m"] > config.isolation_dist_m)
        frame = frame[~lone_far].reset_index(drop=True)
        removed_isolated = before - len(frame)

    return CellFilterResult(frame, n_in, len(frame), removed_small, removed_isolated)
