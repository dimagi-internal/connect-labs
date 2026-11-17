"""Births per country, from the UN's World Population Prospects (WPP), as a check on our own.

Our births are derived (``derive.load``) from a population grid and an infant-mortality figure, which is the only way
to get them below the country. That makes them only as good as the grid, and a grid can be wrong for a whole country
(a population layer calibrated to an old census) without anything inside the data saying so. WPP is the one current,
consistent, licensed national count of births, so this loads it ONLY to be compared with: ``derive.births_audit`` sets
our country total beside it, and a plan says when a country it leans on is far off.

It does not replace or rescale our births. Pulling every territory to the national total would hide a territory-level
fault behind a correct sum (a grid can put 1.5x too many births in Lagos and 0.6x too few in Borno and still sum to
the national figure), and a plan ranks territories. The audit says where to look; it does not look for us.

**What a value is.** WPP's medium-variant births for a country and year, in births (WPP publishes thousands). Years up
to the last estimate year are estimates, later ones projections; ``extra.projection`` says which.

Licence: CC BY 3.0 IGO (UN DESA), https://population.un.org/wpp/ . The UN's name must not imply endorsement.
"""

from __future__ import annotations

import csv
import gzip
import io
import logging

from connect_labs.labs.indicators import boundaries as boundary_set
from connect_labs.labs.indicators.africa import ISO_CODES
from connect_labs.labs.indicators.models import License, Source
from connect_labs.labs.indicators.sources.base import Row, http_get_bytes

logger = logging.getLogger(__name__)

RELEASE = "WPP2024"
URL = (
    "https://population.un.org/wpp/assets/Excel%20Files/1_Indicator%20(Standard)/CSV_FILES/"
    "WPP2024_Demographic_Indicators_Medium.csv.gz"
)
PORTAL = "https://population.un.org/wpp/"
TIMEOUT = 300

MEASURE = "births_wpp"
#: The years kept: enough either side of today for a count in any year a plan is dated to.
FIRST_YEAR, LAST_YEAR = 2015, 2030
#: WPP 2024's estimates run to here; later years are projections.
LAST_ESTIMATE_YEAR = 2023


def fetch() -> bytes:
    logger.info("WPP: fetching %s", URL)
    return http_get_bytes(URL, [], timeout=TIMEOUT)


def parse(payload: bytes, iso_codes: list[str] | None = None) -> dict[str, dict[int, float]]:
    """``{iso3: {year: births}}`` from the gzipped CSV, for the asked countries and years."""
    wanted = {c.upper() for c in iso_codes} if iso_codes else None
    out: dict[str, dict[int, float]] = {}
    text = io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(payload)), encoding="utf-8-sig")
    for row in csv.DictReader(text):
        iso = (row.get("ISO3_code") or "").strip().upper()
        if not iso or (wanted is not None and iso not in wanted):
            continue
        try:
            year, births = int(row["Time"]), float(row["Births"]) * 1000.0
        except (KeyError, TypeError, ValueError):
            continue
        if FIRST_YEAR <= year <= LAST_YEAR:
            out.setdefault(iso, {})[year] = births
    return out


def load(iso_codes: list[str] | None = None, payload: bytes | None = None) -> list[Row]:
    """One ``births_wpp`` row per African country and year in range, written on the country boundary."""
    payload = payload if payload is not None else fetch()
    by_iso = parse(payload, iso_codes or ISO_CODES)
    rows: list[Row] = []
    for country in boundary_set.owned().filter(iso_code__in=sorted(by_iso), admin_level=0):
        for year, births in sorted(by_iso[country.iso_code].items()):
            rows.append(
                Row(
                    indicator=MEASURE,
                    boundary=country,
                    year=year,
                    value=births,
                    source=Source.WPP,
                    source_ref=f"{RELEASE} medium variant, {year}",
                    source_url=PORTAL,
                    license_code=License.CC_BY_3_IGO,
                    method=(
                        f"Births in the year, UN World Population Prospects 2024, medium variant "
                        f"({'projection' if year > LAST_ESTIMATE_YEAR else 'estimate'}). A national count kept to "
                        "check our own births against; it does not replace them."
                    ),
                    extra={"projection": year > LAST_ESTIMATE_YEAR, "release": RELEASE},
                )
            )
    logger.info("WPP: %d rows for %d countries", len(rows), len(by_iso))
    return rows
