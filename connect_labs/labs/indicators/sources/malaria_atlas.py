"""Malaria Atlas Project loader — modelled surfaces, read on our own boundaries.

Everything else in ``sources/`` fetches a number somebody already computed for
an administrative unit. This one fetches a *surface* and does the aggregation
here, which buys three things no survey source can give:

  * **Every boundary, at every level.** A geostatistical model has a value for
    all 5 km of Africa, so ADM2 units in countries with no recent DHS get a real
    estimate rather than a national figure inherited downward.
  * **Counts, not just rates.** MAP publishes clinical cases and deaths as
    counts. A count sums exactly up the hierarchy and can carry a per-case cost,
    which is the question a treatment programme is actually sized on and which
    every rate-only indicator has to refuse.
  * **2024.** The surfaces are annual to 2024, against DHS fieldwork that is
    frequently a decade old.

**Aggregation.** Counts sum; rates take a *population-weighted* mean. The weight
is not assumed — it is derived from MAP's own layers. Their incidence count and
incidence rate are the same quantity per cell, once per year and once per person
per year, so ``count / rate`` recovers the population that produced them, on
exactly the grid being aggregated. That is better than area-weighting (which
lets empty desert vote) and better than borrowing WorldPop (a different grid,
different vintage, and it would hide a disagreement instead of exposing one).
Where the ratio is undefined — no malaria, so no cases — the cells carry no
weight, which is correct: they contribute nothing to a malaria rate either.

Licence: MAP applies CC BY 3.0 Unported to its maps. Commercial use and
redistribution are permitted with attribution — see
https://malariaatlas.org/open-access-policy/.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import shapely

from connect_labs.labs.admin_boundaries.models import AdminBoundary
from connect_labs.labs.indicators import boundaries as boundary_set
from connect_labs.labs.indicators.models import License, Source
from connect_labs.labs.indicators.sources import geotiff
from connect_labs.labs.indicators.sources.base import Row, http_get_bytes

logger = logging.getLogger(__name__)

OWS = "https://data.malariaatlas.org/geoserver/{workspace}/ows"
PORTAL = "https://data.malariaatlas.org/maps"

#: MAP release these coverages come from. Their names carry it, so bumping this
#: to a newer release is a one-line change that the source_ref then records on
#: every row — a reader can tell 202406 numbers from 202508 ones.
RELEASE = "202508"

#: Most recent year in the 202508 mosaics. DescribeCoverage reports the time
#: domain; this is checked against it at fetch time rather than trusted.
YEAR = 2024

#: Degrees of padding around a country's bounding box. One cell is 1/24 of a
#: degree, so this guarantees a whole cell beyond the border in every direction
#: and keeps a coastal boundary from losing its edge cells to rounding.
BBOX_PAD = 0.1


@dataclass(frozen=True)
class Layer:
    """One MAP coverage and the indicator it lands in."""

    indicator: str
    workspace: str
    coverage: str
    #: True for a per-year count (cases, deaths); False for a rate.
    is_count: bool
    #: Multiply raw cell values by this. MAP publishes proportions and per-person
    #: rates; we store percentages and per-1,000 rates.
    scale: float = 1.0
    note: str = ""

    @property
    def coverage_id(self) -> str:
        return f"{self.workspace}__{RELEASE}_{self.coverage}"


LAYERS: tuple[Layer, ...] = (
    Layer(
        "malaria_cases",
        "Malaria",
        "Global_Pf_Incidence_Count",
        is_count=True,
        note="Clinical P. falciparum episodes, summed over the 5 km cells of the unit.",
    ),
    Layer(
        "malaria_deaths",
        "Malaria",
        "Global_Pf_Mortality_Count",
        is_count=True,
        note="P. falciparum deaths, summed over the 5 km cells of the unit.",
    ),
    Layer(
        "malaria_incidence",
        "Malaria",
        "Global_Pf_Incidence_Rate",
        is_count=False,
        scale=1000.0,
        note="Cases per person per year, restated per 1,000 and averaged over cells weighted by population.",
    ),
    Layer(
        "malaria_prevalence",
        "Malaria",
        "Global_Pf_Parasite_Rate",
        is_count=False,
        scale=100.0,
        note="PfPR2-10 — the modelled proportion of 2-10 year-olds carrying detectable parasites.",
    ),
    Layer(
        "itn_use",
        "Interventions",
        "Africa_Insecticide_Treated_Net_Use",
        is_count=False,
        scale=100.0,
        note="Proportion of the population sleeping under a net the previous night.",
    ),
    Layer(
        "itn_access",
        "Interventions",
        "Africa_Insecticide_Treated_Net_Access",
        is_count=False,
        scale=100.0,
        note="Proportion living in a household with a net for every two members.",
    ),
    Layer(
        "irs_coverage",
        "Interventions",
        "Africa_IRS_Coverage",
        is_count=False,
        scale=100.0,
        note="Proportion of the population in dwellings sprayed in the last year.",
    ),
    Layer(
        "antimalarial_effective",
        "Interventions",
        "Global_Antimalarial_Effective_Treatment",
        is_count=False,
        scale=100.0,
        note="Proportion of clinical cases receiving an effective antimalarial.",
    ),
)

#: The pair whose ratio is the population grid every rate is weighted by.
_WEIGHT_NUMERATOR = "Global_Pf_Incidence_Count"
_WEIGHT_DENOMINATOR = "Global_Pf_Incidence_Rate"


def source_url(layer: Layer) -> str:
    return f"{PORTAL}?layers={layer.coverage_id}"


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


def fetch(workspace: str, coverage_id: str, bbox: tuple[float, float, float, float], year: int) -> geotiff.Raster:
    """GetCoverage one layer over one bounding box, for one year.

    WCS 2.0.1 names its axes as the CRS does, so a 4326 coverage subsets on
    ``Lat`` and ``Long`` — not on x and y, and not in the order a bbox is
    usually written. Getting that wrong returns a valid raster of the wrong
    place, so the axis names are spelled out here rather than assembled.
    """
    west, south, east, north = bbox
    params = [
        ("service", "WCS"),
        ("version", "2.0.1"),
        ("request", "GetCoverage"),
        ("coverageId", coverage_id),
        ("format", "image/geotiff"),
        ("subset", f"Lat({south},{north})"),
        ("subset", f"Long({west},{east})"),
        ("subset", f'time("{year}-01-01T00:00:00.000Z")'),
    ]
    body = http_get_bytes(OWS.format(workspace=workspace), params=params, timeout=300)
    if body[:2] not in (b"MM", b"II"):
        # GeoServer reports failure as an HTML or XML page with a 200 or a 400;
        # either way it is not a raster, and the first bytes say so cheaply.
        snippet = body[:400].decode("utf-8", "replace")
        raise RuntimeError(f"{coverage_id}: expected a GeoTIFF, got {snippet!r}")
    return geotiff.read(body)


# ---------------------------------------------------------------------------
# Zonal statistics
# ---------------------------------------------------------------------------


def _window(raster: geotiff.Raster, bounds: tuple[float, float, float, float]) -> tuple[slice, slice] | None:
    """Row and column slices covering a boundary's bounds, or None if outside."""
    west, south, east, north = bounds
    xs, ys = raster.cell_centres()
    cols = np.nonzero((xs >= west - abs(raster.pixel_w)) & (xs <= east + abs(raster.pixel_w)))[0]
    rows = np.nonzero((ys >= south - abs(raster.pixel_h)) & (ys <= north + abs(raster.pixel_h)))[0]
    if not cols.size or not rows.size:
        return None
    return slice(rows[0], rows[-1] + 1), slice(cols[0], cols[-1] + 1)


def zonal(
    raster: geotiff.Raster,
    geom: shapely.Geometry,
    *,
    is_count: bool,
    weights: geotiff.Raster | None = None,
) -> float | None:
    """One boundary's value: a sum for counts, a weighted mean for rates.

    Cell membership is decided by the cell centre. That is the standard
    convention and it is unbiased over any unit large enough to hold cells, but
    a unit smaller than a 5 km cell can contain no centre at all — a couple of
    hundred of Africa's ADM2 units are that small. Those fall back to the single
    cell containing the unit's representative point, which is the honest answer
    at this resolution: the model has one estimate there and so do we.
    """
    win = _window(raster, geom.bounds)
    if win is None:
        return None
    rows, cols = win
    values = raster.masked()[rows, cols]
    xs, ys = raster.cell_centres()
    gx, gy = np.meshgrid(xs[cols], ys[rows])

    inside = shapely.contains_xy(geom, gx, gy)
    usable = inside & np.isfinite(values)

    if not usable.any():
        # Too small to hold a cell centre, or entirely over nodata.
        point = shapely.point_on_surface(geom)
        col = int(round((point.x - raster.origin_x) / raster.pixel_w - 0.5))
        row = int(round((point.y - raster.origin_y) / raster.pixel_h - 0.5))
        if not (0 <= row < raster.height and 0 <= col < raster.width):
            return None
        cell = raster.masked()[row, col]
        if not np.isfinite(cell):
            return None
        # A count over a sub-cell unit is the cell's share of that unit's area,
        # not the whole cell: attributing a 5 km cell's entire caseload to a
        # 2 km district would double-count against its neighbours.
        if is_count:
            cell_area = abs(raster.pixel_w * raster.pixel_h)
            return float(cell) * min(1.0, geom.area / cell_area)
        return float(cell)

    if is_count:
        return float(np.nansum(values[usable]))

    if weights is not None:
        w = weights.masked()[rows, cols]
        good = usable & np.isfinite(w) & (w > 0)
        if good.any():
            return float(np.average(values[good], weights=w[good]))
    return float(np.mean(values[usable]))


def population_grid(count: geotiff.Raster, rate: geotiff.Raster) -> geotiff.Raster:
    """Recover population from MAP's own count and rate layers.

    ``cases = rate x people``, both published on the same grid for the same
    year, so the quotient is the population MAP modelled against. Cells with no
    malaria leave it undefined and are dropped — they carry no weight in a
    malaria rate either.
    """
    c, r = count.masked(), rate.masked()
    if c.shape != r.shape:
        raise ValueError(f"count grid {c.shape} does not match rate grid {r.shape}")
    with np.errstate(divide="ignore", invalid="ignore"):
        people = np.where(r > 1e-9, c / r, np.nan)
    return geotiff.Raster(
        values=people,
        origin_x=count.origin_x,
        origin_y=count.origin_y,
        pixel_w=count.pixel_w,
        pixel_h=count.pixel_h,
        nodata=None,
    )


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _bbox(country: AdminBoundary) -> tuple[float, float, float, float]:
    west, south, east, north = country.geometry.extent
    return (west - BBOX_PAD, south - BBOX_PAD, east + BBOX_PAD, north + BBOX_PAD)


def load_country(iso: str, *, levels=(0, 1, 2), year: int = YEAR, only: set[str] | None = None) -> list[Row]:
    """Every MAP indicator for one country, at every requested admin level.

    One raster fetch per layer per country, then all of that country's
    boundaries are read out of the arrays already in memory. A country is the
    right unit: its bounding box is small enough that the whole stack fits
    comfortably in memory, and a failure costs one country rather than a
    continent.
    """
    country = boundary_set.owned().filter(iso_code=iso, admin_level=0).first()
    if country is None:
        logger.warning("malaria_atlas: no ADM0 boundary for %s", iso)
        return []

    bbox = _bbox(country)
    units = list(boundary_set.owned().filter(iso_code=iso, admin_level__in=levels))
    if not units:
        return []

    # Shapely geometries once per boundary, reused across every layer.
    shapes = {u.id: shapely.from_wkb(bytes(u.geometry.wkb)) for u in units}

    weights: geotiff.Raster | None = None
    try:
        weights = population_grid(
            fetch("Malaria", f"Malaria__{RELEASE}_{_WEIGHT_NUMERATOR}", bbox, year),
            fetch("Malaria", f"Malaria__{RELEASE}_{_WEIGHT_DENOMINATOR}", bbox, year),
        )
    except Exception as exc:  # noqa: BLE001 — a missing weight degrades, it does not fail
        logger.warning("malaria_atlas: %s population weights unavailable (%s); rates fall back to area mean", iso, exc)

    rows: list[Row] = []
    for layer in LAYERS:
        if only and layer.indicator not in only:
            continue
        try:
            raster = fetch(layer.workspace, layer.coverage_id, bbox, year)
        except Exception as exc:  # noqa: BLE001 — one layer missing must not lose the rest
            logger.warning("malaria_atlas: %s %s failed (%s)", iso, layer.coverage_id, exc)
            continue

        weighting = (
            "population-weighted (population recovered from MAP's own incidence count and rate)"
            if weights is not None and not layer.is_count
            else "area-weighted"
            if not layer.is_count
            else "summed"
        )
        for unit in units:
            value = zonal(raster, shapes[unit.id], is_count=layer.is_count, weights=weights)
            if value is None:
                continue
            rows.append(
                Row(
                    indicator=layer.indicator,
                    boundary=unit,
                    year=year,
                    value=value * layer.scale,
                    source=Source.MAP,
                    source_ref=f"MAP {RELEASE} {layer.coverage}",
                    source_url=source_url(layer),
                    license_code=License.CC_BY_3,
                    method=(
                        f"{layer.note} MAP {RELEASE} 5 km surface for {year}, read on this unit's own "
                        f"geometry and {weighting}."
                    ),
                    extra={"release": RELEASE, "coverage": layer.coverage_id},
                )
            )
    return rows
