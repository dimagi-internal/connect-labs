"""Placement: which admin areas did recorded service delivery happen in, and how much in each?

Several parts of labs need the same answer from recorded locations (a visit's GPS, a household's
point): Pulse names the places a donor report covers, the impact model takes each opportunity's
baseline from the most local area that has data, and the coverage check needs the areas whose
buildings form its denominator. This is the one place that answers it, so those three cannot
disagree about where work happened.

Two halves:

* the core is pure (shapely and numpy, no database): ``place_points(points, units)``;
* ``load_units`` fetches the units for a set of points from the boundary resolver, which already
  prefers labs' curated boundaries and falls back to Overture for levels labs does not hold
  (Niger's curated set stops at the region, Overture reaches the commune).

Every point is counted in the finest unit that contains it at each level, with a weight (default
1, or the people reached, or visits), and whatever no unit contains is reported as unplaced
rather than dropped, so the caller can see how much of the work a level explains. A point
exactly on a border goes to the unit with the lowest key, so the result does not depend on the
order boundaries happen to load in.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from shapely import STRtree
from shapely import points as shapely_points
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

#: A level that explains less of the placed weight than this is not trusted to stand in for the work.
DEFAULT_MIN_PLACED_SHARE = 0.9


@dataclass(frozen=True)
class Unit:
    """One admin area. ``key`` is unique across sources; ``level`` is canonical (1 region, 2 district, 3 ward)."""

    key: str
    name: str
    level: int
    geometry: BaseGeometry
    country: str = ""
    source: str = ""
    parent_name: str = ""


@dataclass
class LevelPlacement:
    """Where the weight landed at one level."""

    level: int
    weights: dict = field(default_factory=dict)  # unit key -> weight
    units: dict = field(default_factory=dict)  # unit key -> Unit (every unit considered, hit or not)
    unplaced: float = 0.0
    total: float = 0.0

    @property
    def placed(self) -> float:
        return self.total - self.unplaced

    @property
    def placed_share(self) -> float | None:
        return self.placed / self.total if self.total else None

    def share(self, key: str) -> float:
        return self.weights.get(key, 0.0) / self.total if self.total else 0.0

    def areas(self, min_share: float = 0.01) -> list[Unit]:
        """The units holding at least ``min_share`` of ALL the weight, biggest first: where the work was aimed."""
        keep = [k for k, w in self.weights.items() if self.total and w / self.total >= min_share]
        return [self.units[k] for k in sorted(keep, key=lambda k: (-self.weights[k], k))]

    def top(self, n: int = 10) -> list[tuple[Unit, float]]:
        order = sorted(self.weights, key=lambda k: (-self.weights[k], k))[:n]
        return [(self.units[k], self.weights[k]) for k in order]


@dataclass
class Placement:
    total: float
    levels: dict = field(default_factory=dict)  # level -> LevelPlacement

    def finest_level(self, min_placed_share: float = DEFAULT_MIN_PLACED_SHARE) -> int | None:
        """The finest level that still places at least ``min_placed_share`` of the weight: the most local
        boundary that honestly describes where the work was. None when no level reaches it."""
        ok = [lv for lv, p in self.levels.items() if (p.placed_share or 0) >= min_placed_share]
        return max(ok) if ok else None


def _point_array(points: pd.DataFrame):
    lon = pd.to_numeric(points["lon"], errors="coerce").to_numpy(float)
    lat = pd.to_numeric(points["lat"], errors="coerce").to_numpy(float)
    ok = np.isfinite(lon) & np.isfinite(lat)
    return lon, lat, ok


def place_points(points: pd.DataFrame, units: Iterable[Unit], *, weight: str | None = "weight") -> Placement:
    """Count the points' weight in every unit that contains them, level by level.

    ``points`` needs ``lon`` and ``lat``; ``weight`` names an optional column of weights (default 1 per
    point). A point with no usable coordinates counts toward the total and is unplaced at every level.
    """
    units = sorted(units, key=lambda u: u.key)
    lon, lat, ok = _point_array(points)
    w = (
        pd.to_numeric(points[weight], errors="coerce").fillna(0.0).to_numpy(float)
        if weight and weight in points.columns
        else np.ones(len(points))
    )
    total = float(w.sum())
    geoms = np.full(len(points), None, dtype=object)
    if ok.any():
        geoms[ok] = shapely_points(lon[ok], lat[ok])
    by_level: dict[int, list[Unit]] = {}
    for u in units:
        by_level.setdefault(u.level, []).append(u)
    result = Placement(total=total)
    for level, lv_units in sorted(by_level.items()):
        lp = LevelPlacement(level=level, units={u.key: u for u in lv_units}, total=total)
        placed = np.zeros(len(points), dtype=bool)
        if ok.any():
            tree = STRtree([u.geometry for u in lv_units])
            pt_idx, unit_idx = tree.query(geoms[ok], predicate="intersects")
            # lowest unit index (units are key-sorted) wins a border point, deterministically
            first: dict[int, int] = {}
            for p, u in sorted(zip(pt_idx.tolist(), unit_idx.tolist())):
                first.setdefault(p, u)
            ok_positions = np.flatnonzero(ok)
            for p, u in first.items():
                i = ok_positions[p]
                key = lv_units[u].key
                lp.weights[key] = lp.weights.get(key, 0.0) + float(w[i])
                placed[i] = True
        lp.unplaced = float(w[~placed].sum())
        result.levels[level] = lp
    return result


def units_from_features(features: Iterable) -> list[Unit]:
    """Units from the boundary resolver's ``BoundaryFeature`` rows (GeoJSON geometry)."""
    out = []
    for f in features:
        geom = shape(f.geometry) if isinstance(f.geometry, Mapping) else f.geometry
        if geom is None or geom.is_empty:
            continue
        out.append(
            Unit(
                key=f"{f.source}:{f.boundary_id}",
                name=f.name,
                level=int(f.level),
                geometry=geom if geom.is_valid else geom.buffer(0),
                country=f.country or "",
                source=f.source or "",
                parent_name=f.parent_name or "",
            )
        )
    return out


def load_units(
    points: pd.DataFrame, *, iso: str | None = None, levels=(1, 2, 3), source: str | None = None, pad_deg: float = 0.05
) -> list[Unit]:
    """The admin units under the points, from the boundary resolver (labs' curated set, else Overture).

    One query per level over the points' bounding box, each level from the source that holds it (a
    country's curated boundaries may stop at the region while Overture goes down to the commune).
    """
    from django.contrib.gis.geos import Polygon

    from connect_labs.microplans.core.admin_boundaries import get_resolver

    lon, lat, ok = _point_array(points)
    if not ok.any():
        return []
    bbox = Polygon.from_bbox(
        (lon[ok].min() - pad_deg, lat[ok].min() - pad_deg, lon[ok].max() + pad_deg, lat[ok].max() + pad_deg)
    )
    bbox.srid = 4326
    resolver = get_resolver()
    out: list[Unit] = []
    for level in levels:
        features, _truncated = resolver.boundaries_in_bbox(bbox, source=source, iso=iso, levels=[level], limit=5000)
        out.extend(units_from_features(features))
    return out


def place(
    points: pd.DataFrame, *, iso: str | None = None, levels=(1, 2, 3), source: str | None = None, weight="weight"
):
    """Load the units under the points and place them: the one call most callers want."""
    return place_points(points, load_units(points, iso=iso, levels=levels, source=source), weight=weight)
