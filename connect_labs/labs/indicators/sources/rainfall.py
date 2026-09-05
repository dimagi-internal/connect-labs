"""Rainfall seasonality: when a place is wet, not just how wet.

**This exists because the system diagnosed the gap itself.** Working out how
often ORS should be distributed in Liberia, the seasonality note concluded:

    nothing in this dataset is monthly. It can size a campaign and cannot time
    one.

That is a real limit and not a small one. West Africa has *two* diarrhoea
seasons — bacterial and parasitic pathogens peak in the rains, rotavirus and
the enteric viruses peak in the dry season — so "distribute during the rainy
season" is half an answer, and which half depends on the pathogen. A programme
that buys a year of ORS still has to decide when it arrives.

CHIRPS is the obvious source: 0.05° monthly precipitation from 1981 to the
present, station-blended, and in the public domain. What it gives us is a
**climatology** — the average of each calendar month over a run of years —
rather than a forecast. That is the right object for a distribution schedule,
which is planned against a typical year rather than against next month.

Three measures come out, and only one of them is a threshold question:

    rain_annual_mm          total in a typical year, over people
    rain_peak_month         the wettest calendar month, 1-12
    rain_wettest_quarter    the share of the year's rain falling in its
                            wettest three consecutive months

``rain_peak_month`` is deliberately **not** targetable. "Where is the peak in
July" is not a threshold; it is a fact you read off a place you have already
selected, and putting it on the slider would invite a comparison it cannot
support. ``rain_wettest_quarter`` *is* targetable, because concentration is a
real programme constraint: a place taking 80% of its rain in three months needs
a different logistics plan from one where it is spread evenly, whatever the
totals.

**Weighted by people, not by land.** The same argument as the travel-time
surface: a district that is nine-tenths desert and one-tenth farmland has a
rainy season in the farmland, and averaging over land lets the desert vote on
when to ship. The twelve monthly means and the peak are all computed over
population-weighted cells.

**The window.** Ten years, ending at the last complete calendar year. Long
enough that one anomalous year cannot move the peak, short enough that it
describes the present climate rather than the 1980s. The years used travel on
every row, because a climatology without its window is not checkable.
"""

from __future__ import annotations

import dataclasses
import gzip
import logging

import numpy as np
import shapely

from connect_labs.labs.indicators import boundaries as boundary_set
from connect_labs.labs.indicators.models import License, Source
from connect_labs.labs.indicators.sources import geotiff, worldpop_raster
from connect_labs.labs.indicators.sources.base import Row, http_get_bytes
from connect_labs.labs.indicators.sources.geotiff import sample_onto

logger = logging.getLogger(__name__)

BASE = "https://data.chc.ucsb.edu/products/CHIRPS-2.0/africa_monthly/tifs"
PORTAL = "https://www.chc.ucsb.edu/data/chirps"

#: Years averaged into the climatology, and the reason for the length is in the
#: module docstring: long enough to survive one anomalous year, short enough to
#: describe the present climate.
WINDOW_YEARS = 10
LAST_YEAR = 2025

MONTHS = tuple(range(1, 13))

#: CHIRPS marks absent data with a large negative sentinel rather than NaN.
NODATA = -9999.0


def url_for(year: int, month: int) -> str:
    return f"{BASE}/chirps-v2.0.{year}.{month:02d}.tif.gz"


def fetch_month(year: int, month: int) -> geotiff.Raster:
    """One month of CHIRPS, gunzipped into a raster."""
    blob = http_get_bytes(url_for(year, month), [], timeout=600)
    if blob[:2] != b"\x1f\x8b":
        raise RuntimeError(f"rainfall: expected gzip for {year}-{month:02d}, got {blob[:80]!r}")
    body = gzip.decompress(blob)
    if body[:2] not in (b"MM", b"II"):
        raise RuntimeError(f"rainfall: expected a GeoTIFF inside the gzip for {year}-{month:02d}")
    return geotiff.read(body)


def climatology(years: range | list[int] | None = None) -> dict[int, geotiff.Raster]:
    """One raster per calendar month, averaged over the window.

    Averaged in raster space rather than per boundary, because every boundary
    reads the same twelve grids and fetching 120 files once beats fetching them
    per country.
    """
    years = list(years or range(LAST_YEAR - WINDOW_YEARS + 1, LAST_YEAR + 1))
    out: dict[int, geotiff.Raster] = {}
    for month in MONTHS:
        stack = None
        count = 0
        template = None
        for year in years:
            raster = fetch_month(year, month)
            values = raster.masked()
            values = np.where(values <= NODATA + 1, np.nan, values)
            stack = values if stack is None else np.nansum([stack, values], axis=0)
            template = template or raster
            count += 1
        if stack is None or template is None:
            continue
        out[month] = dataclasses.replace(template, values=stack / count)
        logger.info("rainfall: month %02d averaged over %d years", month, count)
    return out


def _profile(people: np.ndarray, monthly: list[np.ndarray], mask: np.ndarray) -> list[float] | None:
    """Twelve population-weighted monthly means for one boundary."""
    usable = mask & np.isfinite(people) & (people > 0)
    for m in monthly:
        usable = usable & np.isfinite(m)
    if not usable.any():
        return None
    weight = people[usable]
    if float(weight.sum()) <= 0:
        return None
    return [float(np.average(m[usable], weights=weight)) for m in monthly]


def _wettest_quarter_share(profile: list[float]) -> float | None:
    """Share of the year's rain in its wettest three CONSECUTIVE months.

    Consecutive, and wrapping December into January: a season that straddles
    the new year is still one season, and taking the three largest months
    regardless of adjacency would report a concentrated season for a place with
    two separate short ones.
    """
    total = sum(profile)
    if total <= 0:
        return None
    doubled = profile + profile
    best = max(sum(doubled[i : i + 3]) for i in range(12))
    return 100.0 * best / total


def load_country(iso: str, months: dict[int, geotiff.Raster], *, levels=(0, 1, 2)) -> list[Row]:
    """Rainfall seasonality for one country's boundaries, at every level."""
    country = boundary_set.owned().filter(iso_code=iso, admin_level=0).first()
    if country is None:
        logger.warning("rainfall: no ADM0 boundary for %s", iso)
        return []
    units = list(boundary_set.owned().filter(iso_code=iso, admin_level__in=levels))
    if not units:
        return []

    population = worldpop_raster.fetch(iso)
    monthly = [sample_onto(population, months[m]) for m in MONTHS]
    people = population.masked()
    xs, ys = population.cell_centres()

    years = list(range(LAST_YEAR - WINDOW_YEARS + 1, LAST_YEAR + 1))
    window = f"{years[0]}-{years[-1]}"
    method_tail = (
        f"CHIRPS 0.05 degree monthly precipitation averaged over {window}, read at every "
        "WorldPop cell centre in this unit and weighted by the population in each cell. "
        "Averaging over land instead of over people would let uninhabited area decide when "
        "the rains come."
    )

    rows: list[Row] = []
    for unit in units:
        geom = shapely.from_wkb(bytes(unit.geometry.wkb))
        west, south, east, north = geom.bounds
        cols = np.nonzero((xs >= west) & (xs <= east))[0]
        rws = np.nonzero((ys >= south) & (ys <= north))[0]
        if not cols.size or not rws.size:
            continue
        win = (slice(rws[0], rws[-1] + 1), slice(cols[0], cols[-1] + 1))
        gx, gy = np.meshgrid(xs[win[1]], ys[win[0]])
        profile = _profile(people[win], [m[win] for m in monthly], shapely.contains_xy(geom, gx, gy))
        if profile is None:
            continue

        annual = sum(profile)
        peak = int(np.argmax(profile)) + 1
        share = _wettest_quarter_share(profile)
        extra = {
            # The twelve months travel with every row. A peak month with no
            # profile behind it cannot be argued with, and the schedule this
            # is for is argued about.
            "monthly_mm": [round(v, 2) for v in profile],
            "window": window,
        }
        for indicator, value in (
            ("rain_annual_mm", annual),
            ("rain_peak_month", float(peak)),
            ("rain_wettest_quarter", share),
        ):
            if value is None:
                continue
            rows.append(
                Row(
                    indicator=indicator,
                    boundary=unit,
                    year=LAST_YEAR,
                    value=float(value),
                    source=Source.CHIRPS,
                    source_ref=f"CHIRPS v2.0 monthly climatology {window} x WorldPop 2020 1km UNadj",
                    source_url=PORTAL,
                    license_code=License.PUBLIC_DOMAIN,
                    method=method_tail,
                    extra=extra,
                )
            )
    return rows
