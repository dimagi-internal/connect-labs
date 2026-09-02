"""World Bank loader — national fertility, as a universal fallback.

DHS gives subnational fertility, which is what we want, but only for the ~40
African countries with a recent survey. Without a national fallback, every
region of Chad, Somalia, Libya and a dozen others has no way to reach a births
estimate at all, and simply vanishes from a continent-wide total — which is a
worse error than using a coarser number, because it is invisible.

``tfr`` is registered as an inheriting measure, so one national value stands in
for every region that lacks its own, and ``resolve()`` reports that it did.

The obvious alternative, UN WPP, now returns 401 on its data endpoints — the
indicator and location metadata are still open, but the values are not. World
Bank publishes the same series openly under CC BY 4.0.
"""

from __future__ import annotations

import logging

from connect_labs.labs.indicators import boundaries
from connect_labs.labs.indicators.models import License, Source
from connect_labs.labs.indicators.sources.base import Row, http_json

logger = logging.getLogger(__name__)

API = "https://api.worldbank.org/v2"

#: The public indicator page, deep-linked to the country (which WDI keys by
#: ISO-2, returned alongside each observation).
INDICATOR_PAGE = "https://data.worldbank.org/indicator/{code}?locations={iso2}"

#: our measure code → World Bank indicator code
INDICATORS = {
    "tfr": "SP.DYN.TFRT.IN",
    # Total population growth, used to carry a count to the year a programme
    # runs. The 0-4 cohort does not grow at exactly the national rate, but the
    # World Bank publishes no subnational or cohort-specific series and the
    # alternative — projecting nothing — means answering a 2027 question with a
    # 2022 number and saying nothing about it.
    "pop_growth_rate": "SP.POP.GROW",
}

METHOD = (
    "World Bank World Development Indicators, {code}, {year} national value. "
    "Used where no subnational survey estimate exists; inherited by this "
    "boundary's regions."
)


def load(measure: str = "tfr", iso_codes: list[str] | None = None) -> list[Row]:
    """Most recent national value per country, attached to the ADM0 boundary."""
    code = INDICATORS[measure]

    adm0 = {}
    qs = boundaries.owned().filter(admin_level=0)
    if iso_codes:
        qs = qs.filter(iso_code__in=[c.upper() for c in iso_codes])
    for b in qs:
        adm0.setdefault(b.iso_code, b)

    if not adm0:
        return []

    rows: list[Row] = []
    page = 1
    while True:
        payload = http_json(
            f"{API}/country/{';'.join(sorted(adm0))}/indicator/{code}",
            # mrnev=1 asks for the most recent non-empty value per country, which
            # avoids guessing which year every country last reported.
            {"format": "json", "per_page": "300", "page": str(page), "mrnev": "1"},
        )
        if not isinstance(payload, list) or len(payload) < 2:
            break

        meta, records = payload[0], payload[1] or []
        for r in records:
            iso = r.get("countryiso3code")
            value = r.get("value")
            boundary = adm0.get(iso)
            if boundary is None or value is None:
                continue
            year = int(r["date"])
            iso2 = (r.get("country") or {}).get("id") or ""
            rows.append(
                Row(
                    indicator=measure,
                    boundary=boundary,
                    year=year,
                    value=float(value),
                    source=Source.WORLDBANK,
                    source_ref=f"World Bank WDI {year} (national)",
                    source_url=INDICATOR_PAGE.format(code=code, iso2=iso2),
                    license_code=License.CC_BY_4,
                    method=METHOD.format(code=code, year=year),
                )
            )

        if page >= int(meta.get("pages") or 1):
            break
        page += 1

    logger.info("World Bank: %d national %s values", len(rows), measure)
    return rows
