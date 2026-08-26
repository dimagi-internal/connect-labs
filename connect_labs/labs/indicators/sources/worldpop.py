"""WorldPop loader — population by age and sex, via hosted zonal statistics.

WorldPop runs the raster work remotely: submit a polygon to
``api.worldpop.org/v1/services/stats``, poll the task, receive an age–sex
pyramid. That means continent-wide population needs no raster downloads and no
local GDAL work — which is the difference between a batch job and a project.

One call yields every population denominator we need:

    pop_total     all bands
    pop_u1        the 0–1 band — one birth cohort, less infant deaths
    pop_u5        the 0–1 and 1–5 bands
    pop_f_15_49   female bands 15 through 45

``wpgpas`` covers 2000–2020, so 2020 is the most recent year available.
Licence is CC BY 4.0: commercial use permitted with attribution.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.contrib.gis.geos import GEOSGeometry

from connect_labs.labs.admin_boundaries.models import AdminBoundary
from connect_labs.labs.indicators.models import License, Source
from connect_labs.labs.indicators.sources.base import Row, http_json, http_json_post

logger = logging.getLogger(__name__)

STATS = "https://api.worldpop.org/v1/services/stats"
TASKS = "https://api.worldpop.org/v1/tasks"

DATASET = "wpgpas"
LATEST_YEAR = 2020

#: Tolerance in degrees for simplifying a boundary before sending it (~1 km).
#: This is a payload-size courtesy, not a workaround: the geometry goes in a POST
#: body, so there is no length limit to design around, and the tolerance stays
#: small enough that the enclosed population is unchanged for a unit this size.
SIMPLIFY_TOLERANCE = 0.01

POLL_INTERVAL = 3
POLL_LIMIT = 40

#: Submitting a complex polygon can take a while to be accepted, and this is a
#: free public service — running eight submissions at once produced read
#: timeouts on Kenya's coastal counties where a single request succeeded in
#: seconds. Stay modest and patient rather than fast and rejected.
MAX_WORKERS = 4
SUBMIT_TIMEOUT = 180

METHOD = (
    "WorldPop Global Project age and sex structures ({dataset}, {year}), 100 m "
    "resolution, aggregated over this boundary by WorldPop's own hosted zonal "
    "statistics service. Boundary simplified to ~1 km before submission."
)


def _geojson_for(boundary: AdminBoundary) -> str:
    geom: GEOSGeometry = boundary.geometry
    simplified = geom.simplify(SIMPLIFY_TOLERANCE, preserve_topology=True)
    # Simplification can empty a very small unit; fall back to the original.
    if simplified.empty or simplified.num_coords == 0:
        simplified = geom
    return simplified.geojson


def _run_task(geojson: str, year: int) -> dict | None:
    """Submit one polygon and poll until the pyramid comes back."""
    created = http_json_post(
        STATS,
        {"dataset": DATASET, "year": str(year), "geojson": geojson},
        timeout=SUBMIT_TIMEOUT,
    )
    task_id = created.get("taskid")
    if not task_id:
        logger.warning("WorldPop: no taskid returned (%s)", created.get("message"))
        return None

    for _ in range(POLL_LIMIT):
        time.sleep(POLL_INTERVAL)
        status = http_json(f"{TASKS}/{task_id}")
        if status.get("status") != "finished":
            continue
        if status.get("error"):
            logger.warning("WorldPop task %s failed: %s", task_id, status.get("error_message"))
            return None
        return status.get("data")

    logger.warning("WorldPop task %s did not finish within %ds", task_id, POLL_INTERVAL * POLL_LIMIT)
    return None


def _denominators(pyramid: list[dict]) -> dict[str, float]:
    """Fold an age–sex pyramid into the four counts we care about."""
    total = 0.0
    u1 = 0.0
    u5 = 0.0
    f_15_49 = 0.0

    for band in pyramid:
        male = float(band.get("male") or 0)
        female = float(band.get("female") or 0)
        both = male + female
        total += both

        cls = str(band.get("class"))
        if cls == "0":
            u1 += both
            u5 += both
        elif cls == "1":
            u5 += both
        if cls in {"15", "20", "25", "30", "35", "40", "45"}:
            f_15_49 += female

    return {"pop_total": total, "pop_u1": u1, "pop_u5": u5, "pop_f_15_49": f_15_49}


def load_boundary(boundary: AdminBoundary, year: int = LATEST_YEAR) -> list[Row]:
    """Fetch the four population counts for one boundary."""
    data = _run_task(_geojson_for(boundary), year)
    if not data or "agesexpyramid" not in data:
        return []

    counts = _denominators(data["agesexpyramid"])
    method = METHOD.format(dataset=DATASET, year=year)
    return [
        Row(
            indicator=code,
            boundary=boundary,
            year=year,
            value=value,
            source=Source.WORLDPOP,
            source_ref=f"WorldPop {DATASET} {year}",
            license_code=License.CC_BY_4,
            method=method,
        )
        for code, value in counts.items()
    ]


def load(
    boundaries: list[AdminBoundary],
    year: int = LATEST_YEAR,
    max_workers: int = MAX_WORKERS,
    on_progress=None,
    sink=None,
) -> tuple[int, list[str]]:
    """Fetch population for many boundaries concurrently.

    The service is task-based and each polygon takes seconds, so this is IO-bound
    fan-out. Workers are kept modest to stay a polite client — this is a free
    public service, and pushing it harder produced timeouts rather than speed.

    Results are handed to ``sink`` as each boundary completes rather than
    accumulated and returned. A continent-wide run is several hundred tasks over
    many minutes; buffering it all would mean a single late failure discards
    every polygon already paid for.

    Returns ``(rows_produced, failed_boundaries)`` — the failures are returned
    rather than merely logged so the caller can record which places have no
    population, instead of letting them quietly vanish from the map.
    """
    if sink is None:
        raise ValueError("worldpop.load requires a sink to receive rows incrementally")

    produced = 0
    done = 0
    total = len(boundaries)
    failures: list[str] = []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(load_boundary, b, year): b for b in boundaries}
        for fut in as_completed(futures):
            b = futures[fut]
            done += 1
            try:
                got = fut.result()
            except Exception as exc:  # noqa: BLE001 — one bad polygon must not stop the run
                logger.warning("WorldPop failed for %s (%s): %s", b.name, b.iso_code, exc)
                failures.append(f"{b.iso_code}/{b.name}")
                got = []
            if got:
                sink(got)
                produced += len(got)
            if on_progress:
                on_progress(done, total, b, len(got))

    if failures:
        logger.warning(
            "WorldPop: %d of %d boundaries produced no data: %s",
            len(failures),
            total,
            ", ".join(failures[:20]),
        )
    return produced, failures
