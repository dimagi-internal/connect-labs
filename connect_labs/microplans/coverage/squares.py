"""Buildings reached, over the places the campaign worked: no facilities, no catchments.

The squares are the population grid's own 1 km cells (``population.Grid``), the same squares the population lens
counts children in, so the two comparisons cover the same ground. In each district, of the buildings inside the
squares where doses were recorded in a cycle, the share with a recorded dose point within ``max_dist_m``: the
building-footprint reading of "did the campaign get to the households in the places it went".

Reading every building in every square a national campaign touched is millions of buildings, so each district's
squares are SAMPLED: up to ``per_district`` of them, chosen by a hash of the square (the same squares on every run,
so the footprint reads are cached). The share is the sample's, and the output says how many squares it read of how
many were touched.

Pure apart from ``fetch_square`` (the footprints of one square), which is injected.
"""

from __future__ import annotations

import hashlib

import pandas as pd

from connect_labs.microplans.coverage import reached

#: Squares read per district.
PER_DISTRICT = 25


class SquaresContinue(Exception):
    """The deadline passed between two squares. The footprints read so far are kept (``fetch_buildings`` caches each
    square), so the next run reads them back at once and carries on."""


def _rank(row: int, col: int) -> str:
    return hashlib.sha1(f"{row}:{col}".encode()).hexdigest()


def square_box(grid, row: int, col: int) -> tuple[float, float, float, float]:
    """``(min lon, min lat, max lon, max lat)`` of one grid cell."""
    lon, lat = grid.centre(row, col)
    hw, hh = abs(grid.pixel_w) / 2, abs(grid.pixel_h) / 2
    return lon - hw, lat - hh, lon + hw, lat + hh


def buildings_reached(
    points: pd.DataFrame,
    grid,
    fetch_square,
    *,
    max_dist_m: float = reached.DEFAULT_MAX_DIST_M,
    per_district: int = PER_DISTRICT,
    report=None,
    deadline: float | None = None,
) -> dict:
    """``points``: one row per recorded dose place (``district``, ``lon``, ``lat``, ``cycles``: a set).

    Returns ``{"districts": {district: {cycle: {"buildings", "reached", "share", "squares"}}}, "squares_read",
    "squares_touched"}``. A cycle's share is out of the buildings of the sampled squares that cycle recorded doses
    in, the same "where it ran" rule as the population lens."""
    if not len(points):
        return {"districts": {}, "squares_read": 0, "squares_touched": 0}
    rows, cols = grid.cell_of(points["lon"].to_numpy(float), points["lat"].to_numpy(float))
    frame = points.assign(row=rows, col=cols)
    touched: dict = {}
    for district, r, c in zip(frame["district"], frame["row"], frame["col"]):
        touched.setdefault(district, set()).add((int(r), int(c)))
    sample = {d: sorted(cells, key=lambda rc: _rank(*rc))[:per_district] for d, cells in sorted(touched.items())}
    todo = [(d, rc) for d, cells in sample.items() for rc in cells]
    # Points a building in a square can be reached from: those in the square or within max_dist_m of its edge.
    pad = max_dist_m / 111_320.0 * 1.5
    out: dict = {}
    import time

    for n, (district, (r, c)) in enumerate(todo, 1):
        if deadline is not None and time.monotonic() >= deadline:
            raise SquaresContinue()
        if report and (n == 1 or n % 10 == 0):
            report(f"Buildings in square {n} of {len(todo)}", stage_name="buildings", processed=n, total=len(todo))
        x0, y0, x1, y1 = square_box(grid, r, c)
        blds = fetch_square((x0, y0, x1, y1))
        if blds is None or not len(blds):
            continue
        inside = blds[(blds["lon"] >= x0) & (blds["lon"] < x1) & (blds["lat"] >= y0) & (blds["lat"] < y1)].reset_index(
            drop=True
        )
        near = frame[
            (frame["lon"] >= x0 - pad)
            & (frame["lon"] <= x1 + pad)
            & (frame["lat"] >= y0 - pad)
            & (frame["lat"] <= y1 + pad)
        ]
        here = frame[(frame["row"] == r) & (frame["col"] == c)]
        for cycle in sorted({cy for s in here["cycles"] for cy in s}):
            pts = near[[cycle in s for s in near["cycles"]]]
            mask = reached.reached_mask(inside, pts, max_dist_m=max_dist_m)
            slot = out.setdefault(district, {}).setdefault(cycle, {"buildings": 0, "reached": 0, "squares": 0})
            slot["buildings"] += int(len(inside))
            slot["reached"] += int(mask.sum())
            slot["squares"] += 1
    for cycles in out.values():
        for slot in cycles.values():
            slot["share"] = slot["reached"] / slot["buildings"] if slot["buildings"] else None
    return {
        "districts": out,
        "squares_read": len(todo),
        "squares_touched": sum(len(s) for s in touched.values()),
        "per_district": per_district,
    }


def default_fetch_square(filters: dict | None = None):
    """Footprints of one square (a lon/lat box): the microplanner's reader (cached per square) and cleaning."""

    def fetch(box):
        from shapely.geometry import box as shapely_box

        from connect_labs.microplans.core.filters import FilterConfig
        from connect_labs.microplans.core.footprints import fetch_buildings

        raw = fetch_buildings(shapely_box(*box))
        return reached.clean_buildings(raw, FilterConfig(**filters) if filters else None)

    return fetch


def as_points(cells: list[tuple], labels: dict) -> pd.DataFrame:
    """``scan``'s cells ``(centre id, cycle, lat, lon, seen, served)`` as one row per dose place with its district and
    the cycles doses were recorded there."""
    by_place: dict = {}
    for csi, cycle, lat, lon, seen, _served in cells:
        place = by_place.setdefault((csi, lat, lon), set())
        if seen:
            place.add(cycle)
    return (
        pd.DataFrame(
            [
                {"district": labels.get(csi) or "(no district)", "lon": lon, "lat": lat, "cycles": cycles}
                for (csi, lat, lon), cycles in by_place.items()
            ],
            columns=["district", "lon", "lat", "cycles"],
        ).astype({"lon": float, "lat": float})
        if by_place
        else pd.DataFrame(columns=["district", "lon", "lat", "cycles"])
    )
