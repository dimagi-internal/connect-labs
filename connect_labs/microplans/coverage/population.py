"""The population lens: children served against the children a population grid says live there.

The buildings lens asks how many of the buildings around a facility were reached. This asks the question a funder
asks: of the people who should have been served, how many were? Its numerator is people served, placed by the
household's own GPS point (``check.served_sql`` + ``check.placed_households``). Its denominator is a population grid
read at the scale it can bear: WorldPop's 1 km UN-adjusted under-five grid, so each cell is a square kilometre, not a
catchment. At facility scale a modelled grid is noise (see the Niger SMC report: persons per building spread six-fold
between towns with identical building counts), so nothing here pretends otherwise: a cell is a kilometre, a district
is the cells its households touch, and the output says what the grid is.

"Eligible" is the under-five count times ``ELIGIBLE_SHARE_OF_U5`` (SMC covers 3 to 59 months; the under-five band is 0
to 59, and ages are taken as uniform within it, an assumption the output states).

Pure apart from ``fetch_grid`` (the network and the cache), which is injected.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

#: 3 to 59 months out of 0 to 59, ages uniform within the under-five band.
ELIGIBLE_SHARE_OF_U5 = 0.95
#: A cell is drawn only when at least this many households fall in it.
MIN_HOUSEHOLDS = 5
#: The whole admin unit joins a district's range only if the squares its households touched hold this much of it.
UNIT_FAIR_TOUCHED_SHARE = 0.5

GRID_NAME = "WorldPop 2022, 1 km, UN-adjusted, ages 0-4 (age-sex structure)"


@dataclass(frozen=True)
class Grid:
    """A window of a population grid: ``values[row, col]``, ``origin`` the outer corner of cell (0, 0)."""

    values: np.ndarray
    origin_x: float
    origin_y: float
    pixel_w: float
    pixel_h: float  # negative for a north-up grid

    def cell_of(self, lon, lat) -> tuple[np.ndarray, np.ndarray]:
        col = np.floor((np.asarray(lon, float) - self.origin_x) / self.pixel_w).astype(int)
        row = np.floor((np.asarray(lat, float) - self.origin_y) / self.pixel_h).astype(int)
        return row, col

    def centre(self, row: int, col: int) -> tuple[float, float]:
        return (
            self.origin_x + (col + 0.5) * self.pixel_w,
            self.origin_y + (row + 0.5) * self.pixel_h,
        )

    def value_at(self, row: int, col: int):
        if 0 <= row < self.values.shape[0] and 0 <= col < self.values.shape[1]:
            v = self.values[row, col]
            return float(v) if np.isfinite(v) else None
        return None


def crop(values: np.ndarray, origin_x, origin_y, pixel_w, pixel_h, bbox, pad_cells: int = 1) -> Grid:
    """The part of a grid under ``bbox`` = (min_lon, min_lat, max_lon, max_lat), with a margin of whole cells."""
    x0, y0, x1, y1 = bbox
    c0 = int(math.floor((x0 - origin_x) / pixel_w)) - pad_cells
    c1 = int(math.ceil((x1 - origin_x) / pixel_w)) + pad_cells
    r0 = int(math.floor((y1 - origin_y) / pixel_h)) - pad_cells  # pixel_h < 0: the top row holds the highest latitude
    r1 = int(math.ceil((y0 - origin_y) / pixel_h)) + pad_cells
    c0, r0 = max(c0, 0), max(r0, 0)
    c1, r1 = min(c1, values.shape[1]), min(r1, values.shape[0])
    if c1 <= c0 or r1 <= r0:
        raise ValueError("the households lie outside the population grid")
    return Grid(
        values=np.array(values[r0:r1, c0:c1], dtype="f8"),
        origin_x=origin_x + c0 * pixel_w,
        origin_y=origin_y + r0 * pixel_h,
        pixel_w=pixel_w,
        pixel_h=pixel_h,
    )


def fetch_grid(iso: str, bbox) -> Grid:
    """The under-five grid under ``bbox`` for a country: labs' own WorldPop age-sex reader, cropped, cached a week.

    The country's two bands are about 6 MB each and decode in a couple of seconds; the crop is what is cached, keyed
    by country and the box rounded out to a tenth of a degree, so a second run on the same campaign is instant."""
    from django.core.cache import cache

    x0, y0, x1, y1 = bbox
    rounded = (math.floor(x0 * 10) / 10, math.floor(y0 * 10) / 10, math.ceil(x1 * 10) / 10, math.ceil(y1 * 10) / 10)
    key = f"br-pop-u5:{iso.upper()}:" + ",".join(f"{v:.1f}" for v in rounded)
    hit = cache.get(key)
    if hit:
        return Grid(**hit)
    from connect_labs.labs.indicators.sources import worldpop_agesex

    raster = worldpop_agesex.grid_for(iso.upper(), "pop_u5")
    grid = crop(raster.masked(), raster.origin_x, raster.origin_y, raster.pixel_w, raster.pixel_h, rounded)
    cache.set(
        key,
        {
            "values": grid.values,
            "origin_x": grid.origin_x,
            "origin_y": grid.origin_y,
            "pixel_w": grid.pixel_w,
            "pixel_h": grid.pixel_h,
        },
        7 * 24 * 3600,
    )
    return grid


def bounds_of(placed: pd.DataFrame) -> tuple[float, float, float, float]:
    return (
        float(placed["lon"].min()),
        float(placed["lat"].min()),
        float(placed["lon"].max()),
        float(placed["lat"].max()),
    )


def population_lens(
    placed: pd.DataFrame,
    served: dict,
    grid: Grid,
    labels: dict | None = None,
    *,
    min_households: int = MIN_HOUSEHOLDS,
    eligible_share: float = ELIGIBLE_SHARE_OF_U5,
) -> dict:
    """Children served per grid cell, district and cycle, against the grid's eligible children.

    ``placed`` is ``check.placed_households``' frame (grp, hh, lon, lat, cycles); ``served`` is
    ``check.parse_served``'s {household: {cycle: children}}; ``labels`` maps a facility to its district.
    A district's denominator is the cells its households touch, each counted once; a cycle's share is
    out of the cells where that cycle served anyone, the same "areas that ran it" rule as the buildings lens."""
    labels = labels or {}
    cycles = sorted({c for per in served.values() for c in per})
    if not len(placed):
        return {
            "grid": _grid_facts(grid),
            "cycles": cycles,
            "cells": [],
            "districts": [],
            "total": {},
            "cells_total": 0,
        }
    row, col = grid.cell_of(placed["lon"].to_numpy(float), placed["lat"].to_numpy(float))
    frame = placed.assign(row=row, col=col, district=[labels.get(str(g)) or "(none)" for g in placed["grp"]])
    per_hh = {h: served.get(h, {}) for h in frame["hh"]}

    cells: dict = {}
    for rec in frame.itertuples(index=False):
        cell = cells.setdefault(
            (rec.row, rec.col), {"households": 0, "served": {c: 0 for c in cycles}, "districts": set()}
        )
        cell["households"] += 1
        cell["districts"].add(rec.district)
        for c, n in per_hh[rec.hh].items():
            cell["served"][c] += n

    out_cells, no_estimate = [], 0
    for (r, c_), cell in sorted(cells.items()):
        eligible = grid.value_at(r, c_)
        eligible = None if eligible is None else eligible * eligible_share
        if eligible is None:
            no_estimate += 1
        if cell["households"] < min_households:
            continue
        lon, lat = grid.centre(r, c_)
        out_cells.append(
            [
                round(lon, 6),
                round(lat, 6),
                None if eligible is None else round(eligible, 1),
                cell["households"],
                [cell["served"][c] for c in cycles],
            ]
        )

    def summarise(keys: set) -> dict:
        eligible, households = 0.0, 0
        served_by = {c: 0 for c in cycles}
        ran = {c: 0.0 for c in cycles}  # eligible in the cells that served anyone that cycle
        for k in keys:
            cell, el = cells[k], grid.value_at(*k)
            el = 0.0 if el is None else el * eligible_share
            eligible += el
            households += cell["households"]
            for c in cycles:
                served_by[c] += cell["served"][c]
                if cell["served"][c]:
                    ran[c] += el
        return {
            "cells": len(keys),
            "households": households,
            "eligible": round(eligible, 1),
            "served": served_by,
            "eligible_in_cycle": {c: round(v, 1) for c, v in ran.items()},
            "share": {c: (served_by[c] / ran[c]) if ran[c] else None for c in cycles},
        }

    by_district: dict = {}
    for rec in frame.itertuples(index=False):
        by_district.setdefault(rec.district, set()).add((rec.row, rec.col))
    districts = [{"label": d, **summarise(keys)} for d, keys in sorted(by_district.items())]
    return {
        "grid": _grid_facts(grid, eligible_share),
        "cycles": cycles,
        "cells": out_cells,
        "cells_total": len(cells),
        "cells_without_estimate": no_estimate,
        "districts": districts,
        "total": summarise(set(cells)),
        "min_households": min_households,
    }


def unit_index(lons, lats, geometries) -> np.ndarray:
    """For each point, the index of the geometry containing it (lowest index wins a border point), or -1."""
    import shapely
    from shapely import STRtree

    out = np.full(len(lons), -1, dtype=int)
    if not len(geometries) or not len(lons):
        return out
    tree = STRtree(list(geometries))
    pts = shapely.points(np.asarray(lons, float), np.asarray(lats, float))
    point_idx, geom_idx = tree.query(pts, predicate="intersects")
    for p, g in sorted(zip(point_idx.tolist(), geom_idx.tolist()), reverse=True):
        out[p] = g  # reversed so the lowest geometry index is written last and wins
    return out


def eligible_in(grid: Grid, geometry, eligible_share: float = ELIGIBLE_SHARE_OF_U5) -> float:
    """Estimated eligible children in a polygon: the grid cells whose centre falls inside it, times the share."""
    import shapely

    minx, miny, maxx, maxy = geometry.bounds
    r_top, c_left = grid.cell_of(minx, maxy)
    r_bot, c_right = grid.cell_of(maxx, miny)
    r0, r1 = max(int(r_top), 0), min(int(r_bot) + 1, grid.values.shape[0])
    c0, c1 = max(int(c_left), 0), min(int(c_right) + 1, grid.values.shape[1])
    if r1 <= r0 or c1 <= c0:
        return 0.0
    rows, cols = np.mgrid[r0:r1, c0:c1]
    xs = grid.origin_x + (cols + 0.5) * grid.pixel_w
    ys = grid.origin_y + (rows + 0.5) * grid.pixel_h
    inside = shapely.contains_xy(geometry, xs, ys)
    block = grid.values[r0:r1, c0:c1]
    return float(np.nansum(np.where(inside, block, 0.0))) * eligible_share


def denominators(placed: pd.DataFrame, served: dict, grid: Grid, units, lens: dict, labels=None, targets=None) -> dict:
    """Per district, coverage against every denominator available, so they can be read side by side.

    * ``squares``: the 1 km cells the district's households touch (the lens's own denominator);
    * ``unit``: the whole admin unit(s) (``units``, e.g. ADM2) the households fall in, summed WorldPop under-fives
      times the eligible share. It is the denominator a district programme is judged on, but only a fair one when
      the campaign covers the unit: ``touched_share`` says how much of the unit's children sit in the squares the
      households touched, so a low coverage over a low ``touched_share`` reads as 'campaign covers a small part of
      this unit', not as 'low coverage';
    * ``target``: the administrative target, if the facility list gave one.

    ``range`` is the lowest and highest coverage the denominators give for a cycle: where they disagree is the
    finding. Each denominator carries its source."""
    labels, targets = labels or {}, targets or {}
    cycles = lens.get("cycles", [])
    units = list(units or [])
    if not len(placed):
        return {"districts": [], "cycles": cycles}
    idx = unit_index(placed["lon"].to_numpy(float), placed["lat"].to_numpy(float), [u.geometry for u in units])
    frame = placed.assign(unit=idx, district=[labels.get(str(g)) or "(none)" for g in placed["grp"]])
    unit_eligible = {i: eligible_in(grid, u.geometry) for i, u in enumerate(units)}
    squares = {d["label"]: d for d in lens.get("districts", [])}
    target_by_district: dict = {}
    for gid, value in targets.items():
        target_by_district[labels.get(str(gid)) or "(none)"] = target_by_district.get(
            labels.get(str(gid)) or "(none)", 0
        ) + float(value)
    out = []
    for label, rows in frame.groupby("district", sort=True):
        sq = squares.get(label, {})
        served_by = {c: sum(served.get(h, {}).get(c, 0) for h in rows["hh"]) for c in cycles}
        touched = sorted({int(i) for i in rows["unit"] if i >= 0})
        unit_total = sum(unit_eligible[i] for i in touched)
        unplaced_in_unit = int((rows["unit"] < 0).sum())
        entry = {
            "label": label,
            "served": served_by,
            "squares": {
                "eligible": sq.get("eligible"),
                "source": GRID_NAME,
                "share": sq.get("share", {}),
            },
            "unit": {
                "units": [
                    {
                        "name": units[i].name,
                        "level": units[i].level,
                        "parent": units[i].parent_name,
                        "eligible": round(unit_eligible[i], 1),
                    }
                    for i in touched
                ],
                "eligible": round(unit_total, 1) if touched else None,
                "source": GRID_NAME + ", summed over the unit",
                "share": {c: (served_by[c] / unit_total) if touched and unit_total else None for c in cycles},
                "touched_share": (
                    (sq["eligible"] / unit_total) if touched and unit_total and sq.get("eligible") else None
                ),
                "households_outside_any_unit": unplaced_in_unit,
            },
            "target": None,
        }
        t = target_by_district.get(label)
        if t:
            entry["target"] = {
                "value": t,
                "source": "administrative target supplied with the facility list",
                "share": {c: served_by[c] / t for c in cycles},
            }
        entry["range"] = {}
        # The whole unit is a fair denominator only when the campaign covers most of it: if the squares the
        # households touched hold under half of the unit's children, its share stays in the table but out of the range.
        touched = entry["unit"]["touched_share"]
        unit_is_fair = touched is not None and touched >= UNIT_FAIR_TOUCHED_SHARE
        entry["range_excludes"] = [] if unit_is_fair or entry["unit"]["eligible"] is None else ["unit"]
        for c in cycles:
            vals = [
                v
                for v in (
                    entry["squares"]["share"].get(c),
                    entry["unit"]["share"].get(c) if unit_is_fair else None,
                    entry["target"]["share"].get(c) if entry["target"] else None,
                )
                if v is not None
            ]
            entry["range"][c] = [min(vals), max(vals)] if vals else None
        out.append(entry)
    return {"districts": out, "cycles": cycles}


def _grid_facts(grid: Grid, eligible_share: float = ELIGIBLE_SHARE_OF_U5) -> dict:
    return {
        "name": GRID_NAME,
        "pixel": [abs(grid.pixel_w), abs(grid.pixel_h)],
        "eligible_share_of_u5": eligible_share,
        "assumption": (
            "Eligible = 95% of the under-5 count (3 to 59 months of 0 to 59), ages uniform within the band. "
            "A 1 km modelled grid is a plausibility bound, not a census: expect a wide error on a single cell. "
            "A square kilometre can be only partly inside the campaign's catchments, which pulls its share down: "
            "read shares as a lower bound unless the campaign covers whole squares."
        ),
    }
