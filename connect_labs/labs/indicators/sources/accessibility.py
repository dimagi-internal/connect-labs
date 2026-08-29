"""Physical access to care: a travel-time surface, weighted by where people are.

This is the loader that needed two rasters, and the reason it did is worth
stating plainly because it is the difference between a defensible number and a
plausible-looking one.

MAP hosts the Weiss et al. accessibility surfaces — minutes to the nearest
health facility, walking or motorised, globally at 1 km. Aggregated on their
own, they answer "what is the average travel time in this district", and that
average is dominated by land nobody lives on. A district that is nine-tenths
desert and one-tenth town reads as remote when almost everyone in it is a
short walk from a clinic.

What a programme actually argues from is the number of people beyond a
threshold. That needs the population grid on the same cells, which is why this
waited for ``worldpop_raster``. With both, three honest numbers fall out:

    travel_time_healthcare  minutes, averaged over people rather than land
    share_beyond_2h         the proportion further than two hours' walk
    pop_beyond_2h           how many people that is — a count, so it can carry
                            a per-person cost and sum up the hierarchy exactly

Two hours is the threshold the access literature settled on, and the one a
community-health programme is usually justified against.

**Co-registration.** Both grids are 30 arc-second, but they are published on
different extents, so a cell in one is not cell (i, j) in the other. Every
population cell centre is looked up in the travel surface by its coordinates
rather than by index. Assuming aligned indices would shift the whole country by
some fraction of a degree and produce numbers that are wrong everywhere and
obviously wrong nowhere.

Licences: MAP CC BY 3.0 Unported, WorldPop CC BY 4.0. Both permit commercial
use with attribution.
"""

from __future__ import annotations

import logging

import numpy as np
import shapely

from connect_labs.labs.indicators import boundaries as boundary_set
from connect_labs.labs.indicators.models import License, Source
from connect_labs.labs.indicators.sources import geotiff, worldpop_raster
from connect_labs.labs.indicators.sources.base import Row, http_get_bytes

logger = logging.getLogger(__name__)

OWS = "https://data.malariaatlas.org/geoserver/Accessibility/ows"
COVERAGE = "Accessibility__202001_Global_Walking_Only_Travel_Time_To_Healthcare"
PORTAL = "https://data.malariaatlas.org/maps"

#: The epoch of the surface. Weiss et al. published one, in 2020; there is no
#: time axis to subset and no newer release to bump to.
YEAR = 2020

#: Minutes on foot beyond which a household is treated as out of reach.
REMOTE_MINUTES = 120.0

BBOX_PAD = 0.2


def fetch_travel(bbox: tuple[float, float, float, float]) -> geotiff.Raster:
    west, south, east, north = bbox
    body = http_get_bytes(
        OWS,
        params=[
            ("service", "WCS"),
            ("version", "2.0.1"),
            ("request", "GetCoverage"),
            ("coverageId", COVERAGE),
            ("format", "image/geotiff"),
            ("subset", f"Lat({south},{north})"),
            ("subset", f"Long({west},{east})"),
        ],
        timeout=600,
    )
    if body[:2] not in (b"MM", b"II"):
        raise RuntimeError(f"accessibility: expected a GeoTIFF, got {body[:200]!r}")
    return geotiff.read(body)


def sample_onto(target: geotiff.Raster, source: geotiff.Raster) -> np.ndarray:
    """Read ``source`` at every cell centre of ``target``.

    Nearest-cell lookup by coordinate. Both grids are 30 arc-second, so this is
    a re-index rather than a resampling — but it is done by coordinate because
    the two are published on different extents and index alignment is a
    coincidence we must not rely on.
    """
    xs, ys = target.cell_centres()
    cols = np.floor((xs - source.origin_x) / source.pixel_w).astype(int)
    rows = np.floor((ys - source.origin_y) / source.pixel_h).astype(int)
    inside_x = (cols >= 0) & (cols < source.width)
    inside_y = (rows >= 0) & (rows < source.height)

    out = np.full((target.height, target.width), np.nan)
    if not inside_x.any() or not inside_y.any():
        return out
    values = source.masked()
    grid = values[np.ix_(np.clip(rows, 0, source.height - 1), np.clip(cols, 0, source.width - 1))]
    out[np.ix_(inside_y, inside_x)] = grid[np.ix_(inside_y, inside_x)]
    return out


def _stats(people: np.ndarray, minutes: np.ndarray, mask: np.ndarray) -> dict[str, float] | None:
    """The three numbers for one boundary, or None if it has neither."""
    usable = mask & np.isfinite(people) & np.isfinite(minutes) & (people > 0)
    if not usable.any():
        return None
    weight = people[usable]
    total = float(weight.sum())
    if total <= 0:
        return None
    remote = minutes[usable] > REMOTE_MINUTES
    beyond = float(weight[remote].sum())
    return {
        "travel_time_healthcare": float(np.average(minutes[usable], weights=weight)),
        "pop_beyond_2h": beyond,
        "share_beyond_2h": 100.0 * beyond / total,
    }


def load_country(iso: str, *, levels=(0, 1, 2), year: int = YEAR) -> list[Row]:
    """Access statistics for one country's boundaries, at every level."""
    country = boundary_set.owned().filter(iso_code=iso, admin_level=0).first()
    if country is None:
        logger.warning("accessibility: no ADM0 boundary for %s", iso)
        return []
    units = list(boundary_set.owned().filter(iso_code=iso, admin_level__in=levels))
    if not units:
        return []

    population = worldpop_raster.fetch(iso)
    travel = fetch_travel(worldpop_raster.bounds_of(country, BBOX_PAD))
    minutes = sample_onto(population, travel)
    people = population.masked()
    xs, ys = population.cell_centres()

    rows: list[Row] = []
    for unit in units:
        geom = shapely.from_wkb(bytes(unit.geometry.wkb))
        west, south, east, north = geom.bounds
        cols = np.nonzero((xs >= west) & (xs <= east))[0]
        rws = np.nonzero((ys >= south) & (ys <= north))[0]
        if not cols.size or not rws.size:
            continue
        window = (slice(rws[0], rws[-1] + 1), slice(cols[0], cols[-1] + 1))
        gx, gy = np.meshgrid(xs[window[1]], ys[window[0]])
        stats = _stats(people[window], minutes[window], shapely.contains_xy(geom, gx, gy))
        if stats is None:
            continue
        for indicator, value in stats.items():
            rows.append(
                Row(
                    indicator=indicator,
                    boundary=unit,
                    year=year,
                    value=value,
                    source=Source.MAP_WORLDPOP,
                    source_ref="MAP 202001 walking-only travel time x WorldPop 2020 1km UNadj",
                    source_url=f"{PORTAL}?layers={COVERAGE}",
                    license_code=License.CC_BY_3,
                    method=(
                        "Weiss et al.'s walking-only travel-time surface read at every WorldPop cell "
                        f"centre in this unit, then {'summed' if indicator == 'pop_beyond_2h' else 'averaged'} "
                        "weighted by the population in each cell. Averaging over land instead of over "
                        f"people would let uninhabited area vote. Remote means over {REMOTE_MINUTES:.0f} "
                        "minutes on foot."
                    ),
                    extra={"remote_minutes": REMOTE_MINUTES},
                )
            )
    return rows
