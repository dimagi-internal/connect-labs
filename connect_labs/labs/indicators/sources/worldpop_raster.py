"""WorldPop, read from the raster instead of asked for over the API.

``worldpop.py`` submits a polygon to WorldPop's hosted statistics service and
receives an age-sex pyramid back. That is the right default — it needs no raster
work and it answers the age bands, which is most of what the targeting model
needs. But it has two limits, and both of them bite:

  * An undocumented daily quota.
  * Geometries it simply refuses. Re-running the outstanding ADM2 units returns
    ``IndexError`` and "No recorded population in this area" for essentially all
    of them. That is the service declining those specific polygons, not
    throttling us, so waiting will never finish the backfill.

The raster has the population for those units regardless. Downloading a
country's 1 km grid and summing it here sidesteps the service entirely: about
5 MB per country, computed once and discarded. It is also, unlike the API, an
answer we can check — Nigeria comes back at 206,139,587 against the UN-adjusted
published figure of 206,139,589.

The UN-adjusted product is used deliberately: its national totals reconcile to
UN World Population Prospects, which is what every other source in this app is
already implicitly calibrated against. Using the unadjusted grid would make
country totals disagree with IGME's denominators for no gain.

This registers as its own source rather than overwriting ``worldpop``. It sits
*after* it in the resolution order, so an existing API figure is untouched and
the raster fills only what is missing — and where both exist, the disagreement
stays visible rather than being quietly resolved.

Licence: CC BY 4.0. https://hub.worldpop.org/geodata/listing?id=75
"""

from __future__ import annotations

import logging

import numpy as np
import shapely

from connect_labs.labs.admin_boundaries.models import AdminBoundary
from connect_labs.labs.indicators import boundaries as boundary_set
from connect_labs.labs.indicators.models import License, Source
from connect_labs.labs.indicators.sources import geotiff
from connect_labs.labs.indicators.sources.base import Row, http_get_bytes

logger = logging.getLogger(__name__)

YEAR = 2020

#: Per-country 1 km mosaic, UN-adjusted. Roughly 5 MB compressed per country.
POP_URL = (
    "https://data.worldpop.org/GIS/Population/Global_2000_2020_1km_UNadj/"
    "{year}/{ISO}/{iso}_ppp_{year}_1km_Aggregated_UNadj.tif"
)
PRODUCT_PAGE = "https://hub.worldpop.org/geodata/listing?id=75"


def url_for(iso: str, year: int = YEAR) -> str:
    return POP_URL.format(year=year, ISO=iso.upper(), iso=iso.lower())


def fetch(iso: str, year: int = YEAR) -> geotiff.Raster:
    """The country's population grid. BigTIFF, LZW-compressed, delta-coded."""
    body = http_get_bytes(url_for(iso, year), params=None, timeout=600)
    if body[:2] not in (b"MM", b"II"):
        raise RuntimeError(f"{iso}: expected a GeoTIFF, got {body[:200]!r}")
    return geotiff.read(body)


def zonal_sum(raster: geotiff.Raster, geom: shapely.Geometry) -> float | None:
    """Population inside one boundary.

    Cell membership is by cell centre, which is unbiased for anything larger
    than a cell. At 1 km almost every administrative unit in Africa qualifies;
    the handful that do not take the containing cell scaled by their share of
    its area, so they cannot claim population that belongs to a neighbour.
    """
    west, south, east, north = geom.bounds
    xs, ys = raster.cell_centres()
    cols = np.nonzero((xs >= west) & (xs <= east))[0]
    rows = np.nonzero((ys >= south) & (ys <= north))[0]

    if cols.size and rows.size:
        window = (slice(rows[0], rows[-1] + 1), slice(cols[0], cols[-1] + 1))
        values = raster.masked()[window]
        gx, gy = np.meshgrid(xs[window[1]], ys[window[0]])
        usable = shapely.contains_xy(geom, gx, gy) & np.isfinite(values)
        if usable.any():
            return float(np.nansum(values[usable]))

    point = shapely.point_on_surface(geom)
    col = int(round((point.x - raster.origin_x) / raster.pixel_w - 0.5))
    row = int(round((point.y - raster.origin_y) / raster.pixel_h - 0.5))
    if not (0 <= row < raster.height and 0 <= col < raster.width):
        return None
    cell = raster.masked()[row, col]
    if not np.isfinite(cell):
        return None
    cell_area = abs(raster.pixel_w * raster.pixel_h)
    return float(cell) * min(1.0, geom.area / cell_area)


def load_country(iso: str, *, levels=(1, 2), year: int = YEAR, missing_only: bool = False) -> list[Row]:
    """Population for one country's boundaries, summed from its own grid.

    Every unit by default, not only the missing ones, and that is a considered
    choice. The two WorldPop products are not the same number: across Nigeria's
    37 states the raster reads about 5% below the API, consistently, because
    this grid is UN-adjusted and the API's age-sex product is not. Filling only
    the gaps would silently mix two calibrations inside one continental total.
    Loading both in full instead leaves the resolution order to decide, keeps
    the disagreement measurable, and means a user who wants one calibration
    throughout can have it.

    Pass ``missing_only`` to fill gaps alone when that is genuinely what is
    wanted — a fast finish for a backfill, accepting the mixture.
    """
    units = list(boundary_set.owned().filter(iso_code=iso, admin_level__in=levels))
    if not units:
        return []

    if missing_only:
        from connect_labs.labs.indicators.models import IndicatorValue

        answered = set(
            IndicatorValue.objects.filter(
                indicator="pop_total", boundary__in=units, source=Source.WORLDPOP
            ).values_list("boundary_id", flat=True)
        )
        units = [u for u in units if u.id not in answered]
        if not units:
            return []

    raster = fetch(iso, year)
    rows: list[Row] = []
    for unit in units:
        value = zonal_sum(raster, shapely.from_wkb(bytes(unit.geometry.wkb)))
        if value is None:
            continue
        rows.append(
            Row(
                indicator="pop_total",
                boundary=unit,
                year=year,
                value=value,
                source=Source.WORLDPOP_RASTER,
                source_ref=f"WorldPop {year} 1km UN-adjusted",
                source_url=PRODUCT_PAGE,
                license_code=License.CC_BY_4,
                method=(
                    f"Summed from WorldPop's {year} 1 km UN-adjusted population grid over the cells "
                    "whose centre falls inside this unit. Computed here rather than requested from "
                    "WorldPop's statistics service, which refuses some geometries outright. Reads "
                    "roughly 5% below that service's figure because this product is reconciled to UN "
                    "World Population Prospects and the age-sex product it serves is not."
                ),
                extra={"grid": "1km_UNadj", "cells": True},
            )
        )
    return rows


def national_total(iso: str, year: int = YEAR) -> float:
    """The whole grid summed — the figure to check a country's rollup against."""
    return float(np.nansum(fetch(iso, year).masked()))


def bounds_of(country: AdminBoundary, pad: float = 0.1) -> tuple[float, float, float, float]:
    west, south, east, north = country.geometry.extent
    return (west - pad, south - pad, east + pad, north + pad)
