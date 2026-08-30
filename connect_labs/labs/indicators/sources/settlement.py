"""Rural and urban, by the one definition that is comparable across countries.

"How many rural people live here" is one of the most-asked targeting questions
and one of the easiest to answer badly, because **the definition dominates the
answer**. Applied to Rwanda, DEGURBA classes 17% of villages as rural against a
national figure near 72% — Rwanda's density clears DEGURBA's urban threshold
almost everywhere. Neither number is wrong. A number quoted without its
definition is not a fact about the world.

DEGURBA (GHS-SMOD, endorsed by the UN Statistical Commission in 2020) is used
here because it is the only definition that is *comparable* between countries,
which is a different property from being the right one for any single country.
Every value this loader writes says so in its method text.

The classification is a grid, so it has the same problem the travel-time surface
had: counting rural *cells* answers a question about land, and the question is
about people. Both are read on WorldPop's population grid, so a rural share is
the share of the population living in cells DEGURBA calls rural.

The source grid is global — 43,202 x 21,384, 1.85 GB as a whole array — so it is
read through a bounding box. One country is a tenth of a second and a few
megabytes.

Licence: CC BY 4.0.
https://human-settlement.emergency.copernicus.eu/degurba.php
"""

from __future__ import annotations

import io
import logging
import threading
import zipfile

import numpy as np
import shapely

from connect_labs.labs.indicators import boundaries as boundary_set
from connect_labs.labs.indicators.models import License, Source
from connect_labs.labs.indicators.sources import geotiff, worldpop_raster
from connect_labs.labs.indicators.sources.base import Row, http_get_bytes

logger = logging.getLogger(__name__)

RELEASE = "R2023A"
EPOCH = 2020
YEAR = EPOCH

SMOD_ZIP = (
    "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_SMOD_GLOBE_{release}/"
    "GHS_SMOD_E{epoch}_GLOBE_{release}_4326_30ss/V2-0/GHS_SMOD_E{epoch}_GLOBE_{release}_4326_30ss_V2_0.zip"
)
PRODUCT_PAGE = "https://human-settlement.emergency.copernicus.eu/degurba.php"

#: DEGURBA level 2 classes. Level 1 groups them into cities (30), towns and
#: semi-dense areas (21-23), and rural (11-13).
CLASSES = {
    10: "water",
    11: "very low density rural",
    12: "low density rural",
    13: "rural cluster",
    21: "suburban or peri-urban",
    22: "semi-dense urban cluster",
    23: "dense urban cluster",
    30: "urban centre",
}
RURAL = (11, 12, 13)

BBOX_PAD = 0.2

_grid_lock = threading.Lock()
_grid: bytes | None = None


def smod_geotiff() -> bytes:
    """The global settlement grid, downloaded once per process.

    33 MB over the wire for a grid every country then windows into. Fetching it
    per country would be 55 downloads of the same file; holding it as a module
    global is the whole of the caching this needs, and the lock is there because
    countries are loaded concurrently.
    """
    global _grid
    with _grid_lock:
        if _grid is None:
            url = SMOD_ZIP.format(release=RELEASE, epoch=EPOCH)
            logger.info("settlement: fetching the global DEGURBA grid (once)")
            archive = zipfile.ZipFile(io.BytesIO(http_get_bytes(url, params=None, timeout=900)))
            name = next(n for n in archive.namelist() if n.endswith(".tif"))
            _grid = archive.read(name)
        return _grid


def classify(smod: np.ndarray) -> np.ndarray:
    """Boolean mask of the cells DEGURBA calls rural."""
    return np.isin(smod, RURAL)


def load_country(iso: str, *, levels=(0, 1, 2), year: int = YEAR) -> list[Row]:
    """Rural population and rural share for one country's boundaries."""
    country = boundary_set.owned().filter(iso_code=iso, admin_level=0).first()
    if country is None:
        logger.warning("settlement: no ADM0 boundary for %s", iso)
        return []
    units = list(boundary_set.owned().filter(iso_code=iso, admin_level__in=levels))
    if not units:
        return []

    population = worldpop_raster.fetch(iso)
    bbox = worldpop_raster.bounds_of(country, BBOX_PAD)
    settlement = geotiff.read(smod_geotiff(), bbox=bbox)
    # Sampled by coordinate: the two grids are both 30 arc-second but are
    # published on different extents, so index alignment is a coincidence.
    classes = geotiff.sample_onto(population, settlement)
    rural = classify(classes)
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
        inside = shapely.contains_xy(geom, gx, gy)
        block = people[window]
        usable = inside & np.isfinite(block) & (block > 0)
        if not usable.any():
            continue
        total = float(block[usable].sum())
        if total <= 0:
            continue
        rural_here = float(block[usable & rural[window]].sum())

        for indicator, value in (("pop_rural", rural_here), ("share_rural", 100.0 * rural_here / total)):
            rows.append(
                Row(
                    indicator=indicator,
                    boundary=unit,
                    year=year,
                    value=value,
                    source=Source.GHSL,
                    source_ref=f"GHS-SMOD {RELEASE} E{EPOCH} x WorldPop {worldpop_raster.YEAR} 1km UNadj",
                    source_url=PRODUCT_PAGE,
                    license_code=License.CC_BY_4,
                    method=(
                        f"DEGURBA (GHS-SMOD {RELEASE}, {EPOCH}) read at every WorldPop cell centre in "
                        "this unit; rural means classes 11, 12 and 13. Counted over people rather than "
                        "over land. DEGURBA is the UN-endorsed definition and the only one comparable "
                        "between countries -- national definitions differ, sometimes sharply, and this "
                        "figure should always be quoted with the definition that produced it."
                    ),
                    extra={"release": RELEASE, "epoch": EPOCH, "rural_classes": list(RURAL)},
                )
            )
    return rows
