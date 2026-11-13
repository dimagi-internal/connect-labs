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
