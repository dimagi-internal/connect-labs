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

from django.contrib.gis.geos import GEOSGeometry, Polygon

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

#: The service refuses anything larger, with
#: "The requested area was too large. Requested N km^2 but allowance was 100000."
#: Left slightly under the stated cap so a projection rounding difference cannot
#: push a piece over it.
MAX_AREA_KM2 = 95_000

#: Equal-area projection used to measure a polygon in km². Degrees-squared is
#: not an area, and the error between the Sahara and the equator is large enough
#: to matter when the whole point is staying under a limit.
EQUAL_AREA_SRID = 6933

#: Hard ceiling on pieces per boundary, purely to bound the work. Reached only
#: by island chains; the coverage rule below almost always bites first.
MAX_PIECES = 200

#: Keep the largest pieces until this share of the boundary's area is covered.
#: An island chain can decompose into dozens of specks whose combined population
#: is a rounding error but whose combined API cost is not. Dropping them by AREA
#: rather than by COUNT means the omission is measurable, and it is reported in
#: the method text rather than passed off as a complete figure.
AREA_COVERAGE = 0.995

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


def _area_km2(geom: GEOSGeometry) -> float:
    """Area in km², via an equal-area projection rather than degrees squared."""
    clone = geom.clone()
    try:
        clone.transform(EQUAL_AREA_SRID)
    except Exception:  # noqa: BLE001 — an unprojectable sliver is not worth failing over
        return 0.0
    return clone.area / 1_000_000.0


def _explode(geom: GEOSGeometry) -> list[GEOSGeometry]:
    """Flatten to single Polygons.

    The service rejects MultiPolygon outright ("This operation supports only
    Polygons"), which is why island-heavy coastal units failed while mainland
    ones succeeded. Splitting is exact here: the parts are disjoint and
    population is a count, so summing the parts reproduces the whole.
    """
    if geom.geom_type == "Polygon":
        return [geom]
    out: list[GEOSGeometry] = []
    for part in geom:
        if part.geom_type == "Polygon" and not part.empty:
            out.append(part)
    return out


def _split_to_limit(poly: GEOSGeometry, depth: int = 0) -> list[GEOSGeometry]:
    """Bisect a polygon until every piece is under the service's area cap.

    Cuts along the longer axis of the bounding box, recursing on each half. The
    pieces tile the original exactly — no overlap, no gap — so their population
    counts sum back to the whole polygon's.
    """
    if _area_km2(poly) <= MAX_AREA_KM2 or depth > 8:
        return [poly]

    xmin, ymin, xmax, ymax = poly.extent
    if (xmax - xmin) >= (ymax - ymin):
        mid = (xmin + xmax) / 2
        boxes = [(xmin, ymin, mid, ymax), (mid, ymin, xmax, ymax)]
    else:
        mid = (ymin + ymax) / 2
        boxes = [(xmin, ymin, xmax, mid), (xmin, mid, xmax, ymax)]

    out: list[GEOSGeometry] = []
    for box in boxes:
        try:
            piece = poly.intersection(Polygon.from_bbox(box))
        except Exception:  # noqa: BLE001 — a degenerate cut yields nothing useful
            continue
        if piece.empty:
            continue
        for part in _explode(piece):
            out.extend(_split_to_limit(part, depth + 1))
    return out or [poly]


def _pieces_for(boundary: AdminBoundary) -> list[str]:
    """The boundary as one or more GeoJSON Polygons the service will accept."""
    geom: GEOSGeometry = boundary.geometry
    simplified = geom.simplify(SIMPLIFY_TOLERANCE, preserve_topology=True)
    if simplified.empty or simplified.num_coords == 0:
        simplified = geom

    pieces: list[GEOSGeometry] = []
    for part in _explode(simplified):
        pieces.extend(_split_to_limit(part))

    kept, omitted_share = _select_pieces(pieces)
    if omitted_share:
        logger.info(
            "WorldPop: %s (%s) split into %d pieces; kept %d covering %.3f%% of its area",
            boundary.name,
            boundary.iso_code,
            len(pieces),
            len(kept),
            (1 - omitted_share) * 100,
        )

    return [p.geojson for p in kept], omitted_share


def _select_pieces(pieces: list[GEOSGeometry]) -> tuple[list[GEOSGeometry], float]:
    """Largest pieces first, until AREA_COVERAGE of the total area is covered.

    Returns the kept pieces and the share of area left out, so the caller can
    say so rather than presenting a short count as a complete one.
    """
    if len(pieces) <= 1:
        return pieces, 0.0

    # Each area costs a projection transform, so measure once and carry it.
    measured = sorted(((_area_km2(p), p) for p in pieces), key=lambda t: t[0], reverse=True)
    total = sum(area for area, _ in measured)
    if total <= 0:
        return [p for _, p in measured[:MAX_PIECES]], 0.0

    kept: list[GEOSGeometry] = []
    running = 0.0
    for area, piece in measured:
        if len(kept) >= MAX_PIECES:
            break
        kept.append(piece)
        running += area
        if running / total >= AREA_COVERAGE:
            break

    return kept, max(0.0, 1.0 - running / total)


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
    """Fetch the four population counts for one boundary.

    A boundary may go over the wire as several pieces — islands are separate
    polygons and a large region exceeds the service's area cap. The pieces tile
    the boundary exactly, so their counts are summed.
    """
    pieces, omitted_share = _pieces_for(boundary)
    if not pieces:
        return []

    counts: dict[str, float] = {}
    got_any = False
    for geojson in pieces:
        data = _run_task(geojson, year)
        if not data or "agesexpyramid" not in data:
            continue
        got_any = True
        for code, value in _denominators(data["agesexpyramid"]).items():
            counts[code] = counts.get(code, 0.0) + value

    if not got_any:
        return []

    method = METHOD.format(dataset=DATASET, year=year)
    if len(pieces) > 1:
        method += f" Submitted as {len(pieces)} disjoint pieces and summed."
    if omitted_share > 0:
        method += (
            f" {omitted_share * 100:.2f}% of the boundary's area (scattered small "
            "islands) was omitted, so this count is slightly low."
        )
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
                got = []
            if got:
                sink(got)
                produced += len(got)
            else:
                # A task that finishes with no data is just as much a gap as one
                # that raises, and is easier to miss.
                failures.append(f"{b.iso_code}/{b.name}")
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
