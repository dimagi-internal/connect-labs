"""The age bands, read from the raster instead of asked for over the API.

``worldpop_raster.py`` finished ``pop_total`` by downloading a country's grid
and summing it here. The three age-band denominators — ``pop_u1``, ``pop_u5``
and ``pop_f_15_49`` — never got that treatment, so they still come only from
WorldPop's hosted statistics service, and they are stuck where that service
left them: roughly 742 of 1,518 ADM2 units for ``pop_u1``. The remainder are
not queued behind a quota. The service declines those particular geometries,
and re-asking returns the same refusal.

That matters more for the age bands than it did for the total. ``pop_u5`` is
the denominator under most of the child-health model and ``pop_u1`` is the
basis of the births estimate, and a count can never be inherited from the
province above — so a district with no age band contributes nothing at all to a
continental total rather than contributing a coarse figure. Half the districts
missing is half the children missing.

WorldPop publishes the bands as rasters regardless, one file per sex per age
group. Eleven of them answer all three measures:

  * ``pop_u1``      — the single-year bands ``f_0`` and ``m_0``.
  * ``pop_u5``      — the five-year bands ``f_0_4`` and ``m_0_4``.
  * ``pop_f_15_49`` — the seven female bands from ``f_15_19`` to ``f_45_49``.

About 4.8 MB each, downloaded once and discarded. The bands are added cell by
cell *before* any boundary is read, so each measure is one grid and one zonal
sum rather than eleven of each — see ``sum_bands``.

The year is 2022, not the 2020 of the total-population product: it is the
release WorldPop publishes this structure at, and it is fresher. Both are
UN-adjusted, so the national totals of each reconcile to UN World Population
Prospects and to each other's calibration.

This writes under ``Source.WORLDPOP_RASTER``, the same source the total already
uses — it is the same product family read the same way, and the resolution
order already places it after the API so an existing API figure stays put and
the raster fills only what is missing.

Licence: CC BY 4.0. https://hub.worldpop.org/geodata/listing?id=87
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

import numpy as np
import shapely

from connect_labs.labs.indicators import boundaries as boundary_set
from connect_labs.labs.indicators.models import License, Source
from connect_labs.labs.indicators.sources import geotiff
from connect_labs.labs.indicators.sources.base import Row, http_get_bytes
from connect_labs.labs.indicators.sources.worldpop_raster import zonal_sum

logger = logging.getLogger(__name__)

YEAR = 2022

#: Per-country 1 km age-sex structure, UN-adjusted, unconstrained.
BASE = "https://data.worldpop.org/GIS/AgeSex_structures/Global_2021_2022_1km_UNadj/unconstrained/{year}"
BAND_URL = BASE + "/{group}/{ISO}/{iso}_{band}_{year}_1km_UNadj.tif"
PRODUCT_PAGE = "https://hub.worldpop.org/geodata/listing?id=87"

#: The two directories the product splits its bands across. Single years exist
#: only for ages 0 and 1; everything else is a five-year group.
SINGLE_AGE = "single_age"
FIVE_YEAR = "five_year_age_groups"

#: Which rasters add up to each measure, as ``(directory, band)`` pairs. The
#: band token is the sex-and-age part of the filename: ``f_0`` is a single year,
#: ``f_0_4`` a five-year group.
BANDS: dict[str, tuple[tuple[str, str], ...]] = {
    "pop_u1": ((SINGLE_AGE, "f_0"), (SINGLE_AGE, "m_0")),
    "pop_u5": ((FIVE_YEAR, "f_0_4"), (FIVE_YEAR, "m_0_4")),
    # Women of childbearing age, WHO's 15-49. Seven five-year bands, female
    # only — there is no published 15-49 band to fetch instead.
    "pop_f_15_49": tuple((FIVE_YEAR, f"f_{lo}_{lo + 4}") for lo in range(15, 50, 5)),
}


def url_for(iso: str, group: str, band: str, year: int = YEAR) -> str:
    return BAND_URL.format(year=year, group=group, ISO=iso.upper(), iso=iso.lower(), band=band)


def fetch(iso: str, group: str, band: str, year: int = YEAR) -> geotiff.Raster:
    """One age-sex band for one country. BigTIFF, LZW-compressed, delta-coded."""
    body = http_get_bytes(url_for(iso, group, band, year), params=None, timeout=600)
    if body[:2] not in (b"MM", b"II"):
        raise RuntimeError(f"{iso} {band}: expected a GeoTIFF, got {body[:200]!r}")
    return geotiff.read(body)


def _placement(raster: geotiff.Raster) -> tuple:
    """Where a raster's cells sit — shape and affine, everything but the values."""
    return (raster.values.shape, raster.origin_x, raster.origin_y, raster.pixel_w, raster.pixel_h)


def sum_bands(rasters: Iterable[geotiff.Raster]) -> geotiff.Raster:
    """Add the bands cell by cell, giving one grid to read boundaries out of.

    A measure is a sum over bands, so it is formed here, on the grid, before any
    boundary is read. Not for accuracy: ``zonal_sum`` is linear in the cell
    values — its sub-cell fallback scales one cell by an area share — so summing
    each band's zonal total instead would give the same answer to within
    floating-point noise, and it was checked rather than assumed. The reasons
    are that it is one zonal pass over 2,296 boundaries instead of eleven, and
    that every band then contributes over the same set of cells rather than each
    band independently deciding which cells it can see. The second only starts
    to matter the day the fallback stops being linear, which is exactly when
    nobody would think to look.

    Consumed one band at a time rather than stacked, and that is not fussiness:
    a large country's 1 km grid is tens of megabytes per band as float64, eleven
    at once is most of a gigabyte, and a continental run has four countries in
    flight.

    Bands are only summable if they sit on the same grid, so that is checked
    rather than assumed — a mismatched extent added position by position would
    return a confident number about the wrong land.

    A cell with no estimate in *any* band stays nodata. Zeroing it would fill
    the country's bounding box out to sea, and a boundary that misses the grid
    entirely would come back as zero people rather than as no answer.
    """
    total: np.ndarray | None = None
    answered: np.ndarray | None = None
    placement: tuple | None = None

    for raster in rasters:
        if placement is None:
            placement = _placement(raster)
        elif _placement(raster) != placement:
            raise RuntimeError(
                f"bands sit on different grids and cannot be added: {_placement(raster)} vs {placement}"
            )

        band = raster.masked()
        known = np.isfinite(band)
        if total is None:
            total, answered = np.where(known, band, 0.0), known
        else:
            total += np.where(known, band, 0.0)
            answered |= known

    if total is None:
        raise ValueError("no bands to sum")

    total[~answered] = np.nan
    _shape, origin_x, origin_y, pixel_w, pixel_h = placement
    return geotiff.Raster(
        values=total, origin_x=origin_x, origin_y=origin_y, pixel_w=pixel_w, pixel_h=pixel_h, nodata=None
    )


def grid_for(iso: str, indicator: str, year: int = YEAR) -> geotiff.Raster:
    """The single grid that answers one measure for one country."""
    return sum_bands(fetch(iso, group, band, year) for group, band in BANDS[indicator])


def _method(indicator: str, year: int) -> str:
    named = ", ".join(band for _, band in BANDS[indicator])
    return (
        f"Summed from WorldPop's {year} 1 km UN-adjusted age-sex grids. The bands {named} were added "
        "cell by cell first, then summed over the cells whose centre falls inside this unit. Computed "
        "here rather than requested from WorldPop's statistics service, which has a daily quota and "
        "refuses many geometries outright — which is why this measure was missing for the unit in the "
        "first place."
    )


def load_country(iso: str, *, levels=(1, 2), year: int = YEAR) -> list[Row]:
    """The three age-band counts for one country's boundaries, from its grids.

    Every unit, not only the ones with no figure yet, for the reason
    ``worldpop_raster.load_country`` gives at greater length: the raster and the
    statistics service are two calibrations of the same product and filling only
    the gaps would mix them inside one continental total. Loading both in full
    leaves the resolution order to decide and keeps the disagreement measurable.

    A band that 404s costs its own measure and nothing else. WorldPop's coverage
    of this product is not quite uniform, and a country missing one age group is
    not a reason to leave its other two unanswered.
    """
    units = list(boundary_set.owned().filter(iso_code=iso, admin_level__in=levels))
    if not units:
        return []

    geometries = [(u, shapely.from_wkb(bytes(u.geometry.wkb))) for u in units]
    rows: list[Row] = []
    for indicator in BANDS:
        try:
            grid = grid_for(iso, indicator, year)
        except Exception as exc:  # noqa: BLE001 — one band must not lose the other measures
            logger.warning("worldpop_agesex: %s has no %s (%s)", iso, indicator, exc)
            continue

        for unit, geom in geometries:
            value = zonal_sum(grid, geom)
            if value is None:
                continue
            rows.append(
                Row(
                    indicator=indicator,
                    boundary=unit,
                    year=year,
                    value=value,
                    source=Source.WORLDPOP_RASTER,
                    source_ref=f"WorldPop {year} 1km age-sex UN-adjusted",
                    source_url=PRODUCT_PAGE,
                    license_code=License.CC_BY_4,
                    method=_method(indicator, year),
                    extra={"grid": "1km_UNadj", "bands": [band for _, band in BANDS[indicator]]},
                )
            )
    return rows


def national_total(iso: str, indicator: str, year: int = YEAR) -> float:
    """The whole grid summed — the figure to check a country's rollup against."""
    return float(np.nansum(grid_for(iso, indicator, year).masked()))
