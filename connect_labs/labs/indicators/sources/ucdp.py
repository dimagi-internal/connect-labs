"""Conflict deaths, from the Uppsala Conflict Data Program's Georeferenced Event Dataset (GED).

This exists so a delivery plan can say where it would send people into active fighting. A territory with the highest
multiple on paper is not one a team can work in when armed groups control its roads; the plan flags it and lets the
reader set a ceiling (see ``effectiveness.market``). The figure is NOT a delivery-feasibility score and is never
multiplied into a multiple: whether a team can work somewhere is a decision, not a coefficient.

**What a value is.** Reported fatalities from organised violence (state-based conflict, non-state conflict and
one-sided violence), as the best estimate of each coded event, summed over the latest ``WINDOW_YEARS`` complete years
and divided by the number of years: deaths a year, per boundary. UCDP's low and high estimates travel as ``ci_low`` and
``ci_high``. ``conflict_rate`` (per 100,000 people) is derived from it in ``derive.load_conflict_rate``.

**What it is not.** A count of reported deaths, not of deaths: UCDP codes what sources report, so under-reporting
(remote or censored places, ongoing wars with few journalists) makes a quiet figure weaker evidence than a loud one.
Events are located to the place the source names, so an event coded only to "northern region" has no usable point.

**Placement.** An event counts in a boundary when its point lies inside it and its coordinates are good to the
admin-1 level (``where_prec`` 1 to 4: exact, within 25 km, admin-2, admin-1). Less precise events (region, country,
sea) cannot be put in a territory, are left out of every territory's figure, and the country row carries
``located_share``: the share of the country's coded deaths that were placed, so a country whose war is mostly coded
"somewhere in the north" says its territory figures are floors.

Licence: CC BY 4.0, https://ucdp.uu.se/downloads/. Method: Sundberg and Melander (2013), Journal of Peace
Research 50(4).
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from collections import defaultdict
from dataclasses import dataclass

import shapely

from connect_labs.labs.indicators import boundaries as boundary_set
from connect_labs.labs.indicators.models import License, Source
from connect_labs.labs.indicators.sources.base import Row, http_get_bytes

logger = logging.getLogger(__name__)

#: The GED release, "major.minor". 26.1 (March 2026) covers 1989 to 2025. Bump it to pick up a newer release.
RELEASE = "26.1"
URL = "https://ucdp.uu.se/downloads/ged/ged{compact}-csv.zip"
PORTAL = "https://ucdp.uu.se/downloads/"
TIMEOUT = 300

MEASURE = "conflict_deaths"
#: Years averaged. Three smooths one bad year without hiding a war that has just started.
WINDOW_YEARS = 3
#: Coordinates at least this good (``where_prec``) can be placed in an admin-1 territory.
MAX_PRECISION_FOR_TERRITORY = 4
#: Country-level coordinates (``where_prec`` 5 and 6) still sit in the right country; used only for ``located_share``.
MAX_PRECISION_FOR_COUNTRY = 6


@dataclass(frozen=True)
class Event:
    year: int
    lon: float
    lat: float
    best: float
    low: float
    high: float
    precision: int


def url(release: str = RELEASE) -> str:
    return URL.format(compact=release.replace(".", ""))


def fetch(release: str = RELEASE) -> bytes:
    """The GED CSV (inside its zip), as bytes."""
    logger.info("UCDP GED %s: fetching %s", release, url(release))
    return http_get_bytes(url(release), [], timeout=TIMEOUT)


def parse(zip_bytes: bytes, years: range | None = None) -> list[Event]:
    """Every coded event (optionally only ``years``), read straight from the zip without unpacking it to disk."""
    events: list[Event] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        with z.open(name) as raw:
            for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8")):
                year = int(row["year"])
                if years is not None and year not in years:
                    continue
                try:
                    events.append(
                        Event(
                            year,
                            float(row["longitude"]),
                            float(row["latitude"]),
                            float(row["best"]),
                            float(row["low"]),
                            float(row["high"]),
                            int(row["where_prec"]),
                        )
                    )
                except (TypeError, ValueError):  # an event with no usable coordinates cannot be placed
                    continue
    return events


def latest_window(events: list[Event], width: int = WINDOW_YEARS) -> range:
    """The last ``width`` years present in ``events``."""
    last = max(e.year for e in events)
    return range(last - width + 1, last + 1)


def assign(events: list[Event], polygons: dict, max_precision: int) -> dict:
    """Total (best, low, high) deaths of the events placed in each polygon.

    ``polygons`` maps a key to a shapely polygon. An event is placed in the first polygon (in key order) that contains
    its point; one the polygons do not contain, or one coded less precisely than ``max_precision``, is not placed.
    """
    totals = {k: [0.0, 0.0, 0.0] for k in polygons}
    usable = [e for e in events if e.precision <= max_precision]
    if not usable or not polygons:
        return totals
    keys = sorted(polygons, key=str)
    tree = shapely.STRtree([polygons[k] for k in keys])
    points = shapely.points([(e.lon, e.lat) for e in usable])
    point_idx, poly_idx = tree.query(points, predicate="intersects")
    taken = {}
    for p, g in sorted(zip(point_idx.tolist(), poly_idx.tolist())):
        taken.setdefault(p, g)  # the first containing polygon, deterministically
    for p, g in taken.items():
        e = usable[p]
        t = totals[keys[g]]
        t[0] += e.best
        t[1] += e.low
        t[2] += e.high
    return totals


def _polygon(boundary):
    return shapely.from_wkb(bytes(boundary.geometry.wkb))


def rows_for(events: list[Event], window: range, boundaries: list) -> list[Row]:
    """One ``conflict_deaths`` row per boundary: deaths a year over ``window``, placed to admin-1.

    ``boundaries`` are the level-0 and level-1 boundaries to write rows for. Level 1 counts only events placed to
    admin-1 or better; level 0 counts the same (so the country is the sum of its territories, give or take events on a
    border), and ``located_share`` says how much of the country's coded deaths that was.
    """
    years = len(window)
    inside = [e for e in events if e.year in window]
    by_level = {lvl: {b.pk: b for b in boundaries if b.admin_level == lvl} for lvl in (0, 1)}
    placed = {
        lvl: assign(inside, {pk: _polygon(b) for pk, b in bs.items()}, MAX_PRECISION_FOR_TERRITORY)
        for lvl, bs in by_level.items()
    }
    coded = assign(inside, {pk: _polygon(b) for pk, b in by_level[0].items()}, MAX_PRECISION_FOR_COUNTRY)
    rows: list[Row] = []
    for lvl, bs in by_level.items():
        for pk, b in bs.items():
            best, low, high = (v / years for v in placed[lvl][pk])
            extra = {
                "window": [window.start, window.stop - 1],
                "release": RELEASE,
                "max_precision": MAX_PRECISION_FOR_TERRITORY,
            }
            if lvl == 0:
                total = coded[pk][0]
                extra["located_share"] = (placed[0][pk][0] / total) if total else None
            rows.append(
                Row(
                    indicator=MEASURE,
                    boundary=b,
                    year=window.stop - 1,
                    value=best,
                    source=Source.UCDP,
                    source_ref=f"UCDP GED {RELEASE}, {window.start}-{window.stop - 1}",
                    source_url=PORTAL,
                    license_code=License.CC_BY_4,
                    method=(
                        f"Reported conflict deaths a year: UCDP GED {RELEASE} best estimates of events "
                        f"{window.start} to {window.stop - 1} placed inside this boundary (coordinates good to "
                        f"admin-1 or better), divided by {years}. Bounds are UCDP's low and high estimates. "
                        "Reported deaths, not all deaths."
                    ),
                    ci_low=low,
                    ci_high=high,
                    extra=extra,
                )
            )
    return rows


def load(iso_codes: list[str] | None = None, payload: bytes | None = None, release: str = RELEASE) -> list[Row]:
    """The loader: fetch (or take ``payload``), find the latest window, and write a row per country and territory."""
    payload = payload if payload is not None else fetch(release)
    events = parse(payload)
    window = latest_window(events)
    qs = boundary_set.owned().filter(admin_level__in=(0, 1))
    if iso_codes:
        qs = qs.filter(iso_code__in=[c.upper() for c in iso_codes])
    by_iso = defaultdict(list)
    for b in qs:
        by_iso[b.iso_code].append(b)
    rows: list[Row] = []
    for iso, bs in sorted(by_iso.items()):
        rows.extend(rows_for(events, window, bs))
    logger.info("UCDP: %d rows for %d countries, window %s", len(rows), len(by_iso), window)
    return rows
