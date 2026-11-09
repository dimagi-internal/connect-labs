"""Buildings reached: how much of an area's building stock did a set of recorded points get to?

The microplanner decides what a "building" is (``fetch_buildings`` + ``apply_frame_filters``);
this module asks the inverse question of delivery that was NOT planned on those buildings: given
where visits were recorded, which buildings did the work get close to? It is the footprint method
for a campaign that never used work areas (a CommCare-run campaign), so it can be compared with
the Connect approach on the same building definition.

Pure functions over plain frames, no network and no database, so the whole method is testable
against generated data. Callers supply:

* ``buildings``: the frame ``fetch_buildings`` returns (``lon``, ``lat``, ``area_m2``, ...), already
  cleaned with the microplanner's own filters (``clean_buildings`` below runs them with a
  ``FilterConfig`` so every toggle the plan creator has is available here too);
* ``points``: one row per recorded location (``lon``, ``lat``, and optionally ``cycle``, ``accuracy_m``).

Each knob has a default that is meant to be sensible, not final: they are parameters because the
point of the method is to compare settings (which footprints, how close counts as "reached").
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from connect_labs.microplans.core.filters import FilterConfig, apply_frame_filters
from connect_labs.microplans.core.geo import project_to_meters

#: How close a recorded point must be to a building's centre to count as having reached it. A
#: building the microplanner keeps is at most 330 m2 (centre to edge about 10 m), and a phone's GPS
#: fix is good to a few metres in the open, so 30 m is generous enough to survive both without
#: reaching the next compound.
DEFAULT_MAX_DIST_M = 30.0
#: A fix with a worse reported accuracy than this is not trusted to place a household.
DEFAULT_MAX_ACCURACY_M = 50.0


@dataclass(frozen=True)
class PointQuality:
    """What happened to the recorded points before matching: nothing is dropped without being counted."""

    total: int
    missing: int = 0
    null_island: int = 0
    out_of_bounds: int = 0
    poor_accuracy: int = 0
    duplicate: int = 0
    valid: int = 0

    @property
    def valid_share(self) -> float | None:
        return self.valid / self.total if self.total else None


def valid_points(
    points: pd.DataFrame,
    *,
    bounds: tuple[float, float, float, float] | None = None,
    max_accuracy_m: float | None = DEFAULT_MAX_ACCURACY_M,
    drop_duplicates: bool = True,
) -> tuple[pd.DataFrame, PointQuality]:
    """Keep the points that can place a household, and say how many of each kind were not kept.

    ``bounds`` is ``(min_lon, min_lat, max_lon, max_lat)`` of the place the campaign worked in; a
    point outside it is a bad fix, not a household. A point at 0,0 (the "null island" a device
    reports with no fix) is dropped even without bounds. ``duplicate`` counts repeats of the same
    household location within a cycle (same rounded coordinates), kept once.
    """
    total = len(points)
    df = points.copy()
    lon = pd.to_numeric(df["lon"], errors="coerce")
    lat = pd.to_numeric(df["lat"], errors="coerce")
    missing = lon.isna() | lat.isna() | ~np.isfinite(lon) | ~np.isfinite(lat)
    ok = ~missing
    null_island = ok & (lon.abs() < 1e-6) & (lat.abs() < 1e-6)
    ok &= ~null_island
    out_of_bounds = pd.Series(False, index=df.index)
    ok &= lon.between(-180, 180) & lat.between(-90, 90)
    if bounds is not None:
        x0, y0, x1, y1 = bounds
        inside = lon.between(x0, x1) & lat.between(y0, y1)
        out_of_bounds = ok & ~inside
        ok &= inside
    poor = pd.Series(False, index=df.index)
    if max_accuracy_m is not None and "accuracy_m" in df.columns:
        acc = pd.to_numeric(df["accuracy_m"], errors="coerce")
        poor = ok & acc.notna() & (acc > max_accuracy_m)
        ok &= ~poor
    df = df.assign(lon=lon, lat=lat)[ok]
    dupes = 0
    if drop_duplicates and len(df):
        key = [df["lon"].round(5), df["lat"].round(5)]
        if "cycle" in df.columns:
            key.append(df["cycle"])
        flag = pd.concat(key, axis=1).duplicated()
        dupes = int(flag.sum())
        df = df[~flag.to_numpy()]
    return df.reset_index(drop=True), PointQuality(
        total=total,
        missing=int(missing.sum()),
        null_island=int(null_island.sum()),
        out_of_bounds=int(out_of_bounds.sum()),
        poor_accuracy=int(poor.sum()),
        duplicate=dupes,
        valid=len(df),
    )


def clean_buildings(buildings: pd.DataFrame, config: FilterConfig | None = None) -> pd.DataFrame:
    """The microplanner's own building cleaning (tiny isolated roofs and very large roofs out)."""
    return apply_frame_filters(buildings, config).buildings


def reached_mask(
    buildings: pd.DataFrame, points: pd.DataFrame, *, max_dist_m: float = DEFAULT_MAX_DIST_M
) -> np.ndarray:
    """For each building, whether any point lies within ``max_dist_m`` of its centre."""
    if len(buildings) == 0:
        return np.zeros(0, dtype=bool)
    if len(points) == 0:
        return np.zeros(len(buildings), dtype=bool)
    lon = np.concatenate([buildings["lon"].to_numpy(float), points["lon"].to_numpy(float)])
    lat = np.concatenate([buildings["lat"].to_numpy(float), points["lat"].to_numpy(float)])
    # One projection for both, so the distances share an origin.
    x, y, _ = project_to_meters(lon, lat)
    n = len(buildings)
    tree = cKDTree(np.column_stack([x[n:], y[n:]]))
    dist, _ = tree.query(np.column_stack([x[:n], y[:n]]), k=1, distance_upper_bound=max_dist_m)
    return np.isfinite(dist)


@dataclass
class ReachedResult:
    buildings: int
    reached_ever: int
    by_cycle: dict = field(default_factory=dict)  # cycle -> buildings reached in that cycle
    cumulative: dict = field(default_factory=dict)  # cycle -> buildings reached in it or any earlier one

    @property
    def share_ever(self) -> float | None:
        return self.reached_ever / self.buildings if self.buildings else None

    def share(self, cycle) -> float | None:
        return self.by_cycle[cycle] / self.buildings if self.buildings else None


def reached_by_cycle(
    buildings: pd.DataFrame,
    points: pd.DataFrame,
    *,
    max_dist_m: float = DEFAULT_MAX_DIST_M,
    cycles: Iterable | None = None,
) -> ReachedResult:
    """Buildings reached in each cycle, in all cycles so far, and ever. ``points`` needs a ``cycle`` column
    for the per-cycle view; without one everything counts as a single cycle named ``None``."""
    n = len(buildings)
    if "cycle" not in points.columns:
        points = points.assign(cycle=None)
    order = list(cycles) if cycles is not None else sorted(points["cycle"].dropna().unique().tolist()) or [None]
    ever = np.zeros(n, dtype=bool)
    by_cycle, cumulative = {}, {}
    for c in order:
        sub = points[points["cycle"].isna()] if c is None else points[points["cycle"] == c]
        mask = reached_mask(buildings, sub, max_dist_m=max_dist_m)
        ever |= mask
        by_cycle[c] = int(mask.sum())
        cumulative[c] = int(ever.sum())
    return ReachedResult(buildings=n, reached_ever=int(ever.sum()), by_cycle=by_cycle, cumulative=cumulative)


def points_by_area(points: pd.DataFrame, areas: dict) -> dict:
    """How many points fall in each area. ``areas`` maps an id to a shapely (Multi)Polygon in lon/lat.
    A point is counted in the first area that contains it; the remainder is under the key ``None``."""
    from shapely import STRtree
    from shapely.geometry import Point

    ids = list(areas)
    geoms = [areas[i] for i in ids]
    tree = STRtree(geoms)
    counts = {i: 0 for i in ids}
    counts[None] = 0
    for lon, lat in zip(points["lon"].to_numpy(float), points["lat"].to_numpy(float)):
        pt = Point(lon, lat)
        hit = next((int(j) for j in sorted(tree.query(pt)) if geoms[int(j)].covers(pt)), None)
        counts[None if hit is None else ids[hit]] += 1
    return counts


def infer_areas(points: pd.DataFrame, areas: dict, *, min_share: float = 0.01) -> list:
    """The areas the campaign evidently worked in: those holding at least ``min_share`` of the points,
    largest first. Answers "which admin boundaries was this aimed at?" from where the points are, so the
    coverage denominator is a set of whole areas chosen by the evidence and not by an assumption."""
    counts = points_by_area(points, areas)
    total = sum(counts.values())
    if not total:
        return []
    chosen = [(a, n) for a, n in counts.items() if a is not None and n / total >= min_share]
    return [a for a, _ in sorted(chosen, key=lambda t: -t[1])]
